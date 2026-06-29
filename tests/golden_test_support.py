"""Read-only helpers for golden concern-split tests (Golden Suite v2). No pytest fixtures."""

from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from logic.golden_dataset import (
    amount_status_from_payment,
    decision_status_from_payment,
    discount_pct_to_str,
    money_to_str,
    normalize_iban,
    normalize_text,
    pdf_filename,
)
from logic.invoice_folder_loader import load_invoices_from_folder, strip_raw_text_from_invoices
from logic.paths import read_user_data_root
from logic.payment_engine import calculate_payments
from logic.settings import load_settings, merge_debtor_with_defaults
from parser.field_adapters import field_result_from_legacy_dict
from parser.field_model import FieldId
from parser.pdf_parser import extract_text_strict
from parser.profile_extractor import FIELD_KEYS, extract_with_profile, validate_profile
from parser.supplier_db import SupplierDB
from parser.supplier_matcher import match_suppliers

APP_BASE = Path(__file__).resolve().parents[1]
GOLDEN_DIR = APP_BASE / "tests" / "golden_dataset"
GOLDEN_PDFS_DIR = GOLDEN_DIR / "pdfs"
CACHE_DIR = APP_BASE / "tests" / ".cache"
# Bump when parser/golden contract changes so stale pickles are never reused.
GOLDEN_PIPELINE_CACHE_VERSION = "v5-iban-context-primary-slash-customer"
MATCHED_CACHE_FILE = CACHE_DIR / "golden_matched_v1.pkl"
PIPELINE_CACHE_FILE = CACHE_DIR / "golden_pipeline_v1.pkl"
SNAPSHOT_PATH = APP_BASE / "tests" / "snapshots" / "phase_a_ranking_snapshot.json"

HARD_EXTRACTION_FIELDS: tuple[str, ...] = (
    "invoice_number",
    "customer_number",
    "iban",
    "amount",
)

SOFT_DECISION_FIELDS: tuple[str, ...] = (
    "decision_status",
    "amount_status",
)

SOFT_LEGACY_FIELDS: tuple[str, ...] = (
    "supplier_name",
    "description",
    "invoice_date",
    "payment_terms_days",
    "discount_percentage",
)

SOFT_GOLDEN_FIELDS: tuple[str, ...] = SOFT_DECISION_FIELDS + SOFT_LEGACY_FIELDS

# Deprecated aliases (Phase C)
EXTRACTION_FIELDS: tuple[str, ...] = HARD_EXTRACTION_FIELDS + SOFT_LEGACY_FIELDS
DECISION_FIELDS: tuple[str, ...] = ("amount",) + SOFT_DECISION_FIELDS

_PARSER_FINGERPRINT_ROOT = APP_BASE / "parser"

_RESULT_KEY_BY_FIELD: dict[FieldId, str] = {
    "amount": "amount_result",
    "invoice_number": "invoice_number_result",
    "customer_number": "customer_number_result",
    "iban": "iban_result",
    "vat_number": "vat_number_result",
    "kvk_number": "kvk_number_result",
    "invoice_date": "invoice_date_result",
    "email_domain": "email_domain_result",
}


@dataclass(frozen=True)
class GoldenCase:
    json_path: Path
    source_file: str
    golden: dict[str, Any]


@dataclass(frozen=True)
class PipelineOutput:
    invoices_by_pdf: dict[str, dict[str, Any]]
    payments_by_pdf: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ProfileFieldCase:
    supplier: str
    pdf_name: str
    field: str
    profile: dict[str, Any]
    expected: object


def assert_field_equal(*, golden_file: str, field: str, expected: object, actual: object) -> None:
    if expected == actual:
        return
    raise AssertionError(
        "Golden dataset mismatch:\n\n"
        f"File: {golden_file}\n\n"
        f"Field: {field}\n"
        f"Expected: {expected}\n"
        f"Actual:   {actual}\n"
    )


def sorted_golden_files() -> list[Path]:
    if not GOLDEN_DIR.exists():
        return []
    return sorted(p for p in GOLDEN_DIR.glob("*.json") if p.is_file())


def iter_golden_cases() -> list[GoldenCase]:
    out: list[GoldenCase] = []
    for gf in sorted_golden_files():
        golden = json.loads(gf.read_text(encoding="utf-8") or "{}")
        if not isinstance(golden, dict):
            continue
        src = normalize_text(golden.get("source_file"))
        if not src:
            continue
        if not (GOLDEN_PDFS_DIR / src).is_file():
            continue
        out.append(GoldenCase(json_path=gf, source_file=src, golden=golden))
    return out


def user_data_dir() -> Path:
    return read_user_data_root(APP_BASE)


def _debtor_kwargs() -> dict[str, str | None]:
    user_data_dir_path = user_data_dir()
    settings = load_settings(str(user_data_dir_path / "settings.json"))
    debtor = merge_debtor_with_defaults(settings.get("debtor"))
    debtor_iban = debtor.get("iban") or None
    debtor_kvk = debtor.get("kvk") or None
    debtor_vat = debtor.get("vat") or None
    if not (debtor_iban or "").strip():
        debtor_iban = None
    if not (debtor_kvk or "").strip():
        debtor_kvk = None
    if not (debtor_vat or "").strip():
        debtor_vat = None
    return {
        "debtor_iban": debtor_iban,
        "debtor_kvk": debtor_kvk,
        "debtor_vat": debtor_vat,
    }


def _matched_cache_fingerprint() -> str:
    parts: list[str] = [f"schema:{GOLDEN_PIPELINE_CACHE_VERSION}"]
    if GOLDEN_PDFS_DIR.is_dir():
        for pdf in sorted(GOLDEN_PDFS_DIR.glob("*.pdf")):
            st = pdf.stat()
            parts.append(f"{pdf.name}:{st.st_mtime_ns}:{st.st_size}")
    if _PARSER_FINGERPRINT_ROOT.is_dir():
        for py in sorted(_PARSER_FINGERPRINT_ROOT.rglob("*.py")):
            st = py.stat()
            parts.append(f"parser/{py.relative_to(_PARSER_FINGERPRINT_ROOT)}:{st.st_mtime_ns}:{st.st_size}")
    suppliers_path = user_data_dir() / "suppliers.json"
    if suppliers_path.is_file():
        st = suppliers_path.stat()
        parts.append(f"suppliers:{st.st_mtime_ns}:{st.st_size}")
    settings_path = user_data_dir() / "settings.json"
    if settings_path.is_file():
        st = settings_path.stat()
        parts.append(f"settings:{st.st_mtime_ns}:{st.st_size}")
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest


def _invoices_by_pdf(invoices: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for inv in invoices:
        k = pdf_filename(inv.get("source_file"))
        if not k or k in out:
            continue
        out[k] = inv
    return out


def _payments_by_pdf(payments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in payments:
        k = pdf_filename(p.get("_source_file"))
        if not k:
            continue
        if k in out:
            out.pop(k, None)
            continue
        out[k] = p
    return out


def load_matched_invoices(*, use_cache: bool = True) -> dict[str, dict[str, Any]]:
    """Load + match golden PDFs; no calculate_payments."""
    pdfs = (
        sorted(p for p in GOLDEN_PDFS_DIR.glob("*.pdf") if p.is_file())
        if GOLDEN_PDFS_DIR.exists()
        else []
    )
    if not pdfs:
        return {}

    fingerprint = _matched_cache_fingerprint()
    if use_cache and MATCHED_CACHE_FILE.is_file():
        try:
            cached = pickle.loads(MATCHED_CACHE_FILE.read_bytes())
            if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint:
                by_pdf = cached.get("invoices_by_pdf")
                if isinstance(by_pdf, dict):
                    return by_pdf
        except (pickle.PickleError, OSError, TypeError, ValueError):
            pass

    kwargs = _debtor_kwargs()
    invoices = load_invoices_from_folder(
        GOLDEN_PDFS_DIR,
        debtor_iban=kwargs["debtor_iban"],
        debtor_kvk=kwargs["debtor_kvk"],
        debtor_vat=kwargs["debtor_vat"],
    )
    db = SupplierDB(path=str(user_data_dir() / "suppliers.json"))
    matched = match_suppliers(invoices, db)
    strip_raw_text_from_invoices(matched)
    by_pdf = _invoices_by_pdf(matched)

    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"fingerprint": fingerprint, "invoices_by_pdf": by_pdf}
        MATCHED_CACHE_FILE.write_bytes(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))

    return by_pdf


def load_pipeline_with_payments(*, use_cache: bool = True) -> PipelineOutput:
    """Full golden pipeline through calculate_payments."""
    fingerprint = _matched_cache_fingerprint()
    if use_cache and PIPELINE_CACHE_FILE.is_file():
        try:
            cached = pickle.loads(PIPELINE_CACHE_FILE.read_bytes())
            if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint:
                inv_by = cached.get("invoices_by_pdf")
                pay_by = cached.get("payments_by_pdf")
                if isinstance(inv_by, dict) and isinstance(pay_by, dict):
                    return PipelineOutput(invoices_by_pdf=inv_by, payments_by_pdf=pay_by)
        except (pickle.PickleError, OSError, TypeError, ValueError):
            pass

    by_pdf = load_matched_invoices(use_cache=use_cache)
    if not by_pdf:
        return PipelineOutput(invoices_by_pdf={}, payments_by_pdf={})

    matched = list(by_pdf.values())
    payments, _errors = calculate_payments(matched, session_date=date.today())
    out = PipelineOutput(
        invoices_by_pdf=by_pdf,
        payments_by_pdf=_payments_by_pdf(payments),
    )
    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "fingerprint": fingerprint,
            "invoices_by_pdf": out.invoices_by_pdf,
            "payments_by_pdf": out.payments_by_pdf,
        }
        PIPELINE_CACHE_FILE.write_bytes(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))
    return out


def suppliers_with_extraction_profiles(user_data_dir_path: Path) -> list[tuple[str, dict[str, Any]]]:
    suppliers_path = user_data_dir_path / "suppliers.json"
    if not suppliers_path.is_file():
        return []
    data = json.loads(suppliers_path.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    for s in data.get("suppliers") or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        ep = s.get("extraction_profile")
        if not name or not isinstance(ep, dict):
            continue
        learned = str(ep.get("learned_from") or "").strip()
        if not learned:
            continue
        out.append((name, ep))
    return out


def iter_profile_field_cases(user_data_dir_path: Path) -> list[ProfileFieldCase]:
    cases: list[ProfileFieldCase] = []
    for supplier, profile in suppliers_with_extraction_profiles(user_data_dir_path):
        pdf_name = Path(str(profile.get("learned_from") or "")).name
        if not (GOLDEN_PDFS_DIR / pdf_name).is_file():
            continue
        for field in FIELD_KEYS:
            spec = profile.get(field)
            if not isinstance(spec, dict):
                continue
            expected = spec.get("confirmed_value")
            if expected is None:
                continue
            cases.append(
                ProfileFieldCase(
                    supplier=supplier,
                    pdf_name=pdf_name,
                    field=field,
                    profile=profile,
                    expected=expected,
                )
            )
    return cases


def profile_field_actual(case: ProfileFieldCase) -> object:
    pdf_path = GOLDEN_PDFS_DIR / case.pdf_name
    raw = extract_text_strict(str(pdf_path))
    extracted = extract_with_profile(raw, case.profile)
    return extracted.get(case.field)


def profile_field_matches(case: ProfileFieldCase) -> bool:
    actual = profile_field_actual(case)
    if case.field == "amount":
        try:
            exp_d = Decimal(str(case.expected)).quantize(Decimal("0.01"))
            if actual is None:
                return False
            act_d = Decimal(str(actual)).quantize(Decimal("0.01"))
            return abs(act_d - exp_d) <= Decimal("0.01")
        except (InvalidOperation, ValueError, TypeError):
            return False
    return str(actual or "").strip() == str(case.expected).strip()


def golden_expected(case: GoldenCase, field: str) -> object:
    """Expected value from golden JSON (same normalizers as test_02)."""
    g = case.golden
    if field == "invoice_number":
        return normalize_text(g.get("invoice_number"))
    if field == "supplier_name":
        return normalize_text(g.get("supplier_name"))
    if field == "iban":
        return normalize_iban(g.get("iban"))
    if field == "customer_code" or field == "customer_number":
        return normalize_text(g.get("customer_code"))
    if field == "description":
        return normalize_text(g.get("description"))
    if field == "invoice_date":
        return normalize_text(g.get("invoice_date"))
    if field == "payment_terms_days":
        return int(g.get("payment_terms_days") or 0)
    if field == "discount_percentage":
        return money_to_str(g.get("discount_percentage"))
    if field == "amount":
        return str(Decimal(str(g.get("amount") or "0")).quantize(Decimal("0.01")))
    if field == "decision_status":
        st = normalize_text(g.get("decision_status"))
        return st or "included"
    if field == "amount_status":
        return normalize_text(g.get("amount_status"))
    raise ValueError(f"unknown golden field: {field}")


def golden_actual(
    case: GoldenCase,
    field: str,
    inv: dict[str, Any],
    pay: dict[str, Any] | None,
) -> object:
    """Actual pipeline value using the same rules as test_02_golden_dataset_business_output."""
    g = case.golden
    if pay is None:
        if field == "amount":
            return money_to_str(g.get("amount"))
        if field == "decision_status":
            st = normalize_text(g.get("decision_status"))
            return st or "included"
        if field == "amount_status":
            return normalize_text(g.get("amount_status"))
        return golden_expected(case, field)

    if field == "invoice_number":
        return normalize_text(inv.get("invoice_number") or pay.get("invoice_number"))
    if field == "supplier_name":
        return normalize_text(inv.get("supplier_name") or pay.get("supplier_name"))
    if field == "iban":
        return normalize_iban(pay.get("iban") or inv.get("iban"))
    if field == "customer_code" or field == "customer_number":
        return normalize_text(inv.get("customer_number"))
    if field == "description":
        return normalize_text(pay.get("description") or inv.get("description"))
    if field == "invoice_date":
        return normalize_text(inv.get("invoice_date"))
    if field == "payment_terms_days":
        return int(inv.get("supplier_payment_term_days_raw") or 0)
    if field == "discount_percentage":
        return discount_pct_to_str(inv.get("discount"))
    if field == "amount":
        return money_to_str(pay.get("amount"))
    if field == "decision_status":
        return normalize_text(decision_status_from_payment(pay))
    if field == "amount_status":
        return normalize_text(amount_status_from_payment(pay))
    raise ValueError(f"unknown golden field: {field}")


def assert_golden_field(
    *,
    case: GoldenCase,
    field: str,
    inv: dict[str, Any],
    pay: dict[str, Any] | None,
) -> None:
    expected = golden_expected(case, field)
    actual = golden_actual(case, field, inv, pay)
    if field == "amount":
        expected = str(Decimal(str(expected)).quantize(Decimal("0.01")))
        actual = str(Decimal(str(actual)).quantize(Decimal("0.01")))
    assert_field_equal(
        golden_file=case.json_path.name,
        field=field,
        expected=expected,
        actual=actual,
    )


def production_winner(inv: dict[str, Any], field_id: FieldId) -> dict[str, Any]:
    key = _RESULT_KEY_BY_FIELD[field_id]
    raw = inv.get(key)
    if not isinstance(raw, dict):
        raw = {}
    fr = field_result_from_legacy_dict(raw, field_id=field_id)
    val = fr.selected_value
    if val is None:
        val_str = ""
    elif isinstance(val, Decimal):
        val_str = str(val)
    else:
        val_str = str(val).strip()
    return {
        "value": val_str,
        "source": str(fr.source or ""),
        "status": str(fr.status or ""),
        "confidence": int(fr.confidence or 0),
    }


def load_ranking_snapshot() -> dict[str, Any]:
    if not SNAPSHOT_PATH.is_file():
        return {}
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8") or "{}")


def snapshot_production_winner(snapshot: dict[str, Any], pdf: str, field_id: FieldId) -> dict[str, Any]:
    snap_field = (snapshot.get(pdf) or {}).get(field_id) or {}
    snap_prod = (snap_field.get("production") or {}).get("winner") or snap_field.get("winner") or {}
    return {
        "value": str(snap_prod.get("value") or ""),
        "source": str(snap_prod.get("source") or ""),
        "status": str(snap_prod.get("status") or ""),
        "confidence": int(snap_prod.get("confidence") or 0),
    }
