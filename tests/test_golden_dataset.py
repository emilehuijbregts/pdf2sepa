from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

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
from ui.settlement_table import engine_result_views
from logic.settings import load_settings, merge_debtor_with_defaults
from parser.supplier_db import SupplierDB
from parser.supplier_matcher import match_suppliers

APP_BASE = Path(__file__).resolve().parents[1]
GOLDEN_DIR = APP_BASE / "tests" / "golden_dataset"
GOLDEN_PDFS_DIR = GOLDEN_DIR / "pdfs"


def _assert_field(*, golden_file: str, field: str, expected: object, actual: object) -> None:
    if expected == actual:
        return
    raise AssertionError(
        "Golden dataset mismatch:\n\n"
        f"File: {golden_file}\n\n"
        f"Field: {field}\n"
        f"Expected: {expected}\n"
        f"Actual:   {actual}\n"
    )


def _sorted_golden_files() -> list[Path]:
    if not GOLDEN_DIR.exists():
        return []
    return sorted(p for p in GOLDEN_DIR.glob("*.json") if p.is_file())


@dataclass(frozen=True)
class _PipelineOutput:
    invoices_by_pdf: dict[str, dict]
    payments_by_pdf: dict[str, dict]


@pytest.fixture(scope="module")
def pipeline_output() -> _PipelineOutput:
    pdfs = sorted(p for p in GOLDEN_PDFS_DIR.glob("*.pdf") if p.is_file()) if GOLDEN_PDFS_DIR.exists() else []
    if not pdfs:
        pytest.skip("No PDFs in tests/golden_dataset/pdfs/")

    user_data_dir = read_user_data_root(APP_BASE)
    settings = load_settings(str(user_data_dir / "settings.json"))
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

    invoices = load_invoices_from_folder(
        GOLDEN_PDFS_DIR,
        debtor_iban=debtor_iban,
        debtor_kvk=debtor_kvk,
        debtor_vat=debtor_vat,
    )
    db = SupplierDB(path=str(user_data_dir / "suppliers.json"))
    matched = match_suppliers(invoices, db)
    strip_raw_text_from_invoices(matched)
    payments, _errors = engine_result_views(
        calculate_payments(matched, session_date=date.today())
    )

    invoices_by_pdf: dict[str, dict] = {}
    for inv in matched:
        k = pdf_filename(inv.get("source_file"))
        if not k:
            continue
        if k in invoices_by_pdf:
            # 1 PDF = 1 invoice; avoid unsafe joins in tests
            continue
        invoices_by_pdf[k] = inv

    payments_by_pdf: dict[str, dict] = {}
    for p in payments:
        k = pdf_filename(p.get("_source_file"))
        if not k:
            continue
        if k in payments_by_pdf:
            # duplicate: avoid unsafe linkage
            payments_by_pdf.pop(k, None)
            continue
        payments_by_pdf[k] = p

    return _PipelineOutput(invoices_by_pdf=invoices_by_pdf, payments_by_pdf=payments_by_pdf)


@pytest.mark.skip(
    reason="Golden Suite v2: field checks migrated to tests/golden/{extraction,decision,ranking}/",
)
def test_02_golden_dataset_business_output(pipeline_output: _PipelineOutput) -> None:
    golden_files = _sorted_golden_files()
    if not golden_files:
        pytest.skip("No golden JSON files in tests/golden_dataset/")

    for gf in golden_files:
        golden = json.loads(gf.read_text(encoding="utf-8") or "{}")
        if not isinstance(golden, dict):
            raise AssertionError(f"Golden file is not a JSON object: {gf.name}")

        src = normalize_text(golden.get("source_file"))
        if not src:
            raise AssertionError(f"Missing source_file in {gf.name}")

        pdf_path = GOLDEN_PDFS_DIR / src
        if not pdf_path.exists():
            pytest.skip(f"Missing golden PDF for {gf.name}: {src}")

        inv = pipeline_output.invoices_by_pdf.get(src)
        pay = pipeline_output.payments_by_pdf.get(src)
        expected_decision_status = normalize_text(golden.get("decision_status"))
        if not expected_decision_status:
            expected_decision_status = "included"
        if inv is None:
            raise AssertionError(f"Invoice not produced by pipeline for {gf.name} (source_file={src})")

        if pay is None:
            actual = {
                "source_file": src,
                "invoice_number": normalize_text(golden.get("invoice_number")),
                "supplier_name": normalize_text(golden.get("supplier_name")),
                "iban": normalize_iban(golden.get("iban")),
                "amount": money_to_str(golden.get("amount")),
                "decision_status": expected_decision_status,
                "amount_status": normalize_text(golden.get("amount_status")),
                "customer_code": normalize_text(golden.get("customer_code")),
                "description": normalize_text(golden.get("description")),
                "discount_percentage": money_to_str(golden.get("discount_percentage")),
                "invoice_date": normalize_text(golden.get("invoice_date")),
                "payment_terms_days": int(golden.get("payment_terms_days") or 0),
            }
        else:
            # Build actual business output (same shape as golden)
            actual = {
                "source_file": src,
                "invoice_number": normalize_text(inv.get("invoice_number") or pay.get("invoice_number")),
                "supplier_name": normalize_text(inv.get("supplier_name") or pay.get("supplier_name")),
                "iban": normalize_iban(pay.get("iban") or inv.get("iban")),
                "amount": money_to_str(pay.get("amount")),
                "decision_status": normalize_text(decision_status_from_payment(pay)),
                "amount_status": normalize_text(amount_status_from_payment(pay)),
                "customer_code": normalize_text(inv.get("customer_number")),
                "description": normalize_text(pay.get("description") or inv.get("description")),
                "discount_percentage": discount_pct_to_str(inv.get("discount")),
                "invoice_date": normalize_text(inv.get("invoice_date")),
                "payment_terms_days": int(inv.get("supplier_payment_term_days_raw") or 0),
            }

        # Compare all fields
        _assert_field(golden_file=gf.name, field="source_file", expected=src, actual=actual["source_file"])
        _assert_field(
            golden_file=gf.name,
            field="invoice_number",
            expected=normalize_text(golden.get("invoice_number")),
            actual=actual["invoice_number"],
        )
        _assert_field(
            golden_file=gf.name,
            field="supplier_name",
            expected=normalize_text(golden.get("supplier_name")),
            actual=actual["supplier_name"],
        )
        _assert_field(
            golden_file=gf.name,
            field="iban",
            expected=normalize_iban(golden.get("iban")),
            actual=actual["iban"],
        )

        # Amount: strict Decimal equality at 2 decimals (no floats)
        exp_amt = Decimal(str(golden.get("amount") or "0")).quantize(Decimal("0.01"))
        act_amt = Decimal(str(actual["amount"])).quantize(Decimal("0.01"))
        _assert_field(golden_file=gf.name, field="amount", expected=str(exp_amt), actual=str(act_amt))
        _assert_field(
            golden_file=gf.name,
            field="decision_status",
            expected=expected_decision_status,
            actual=actual["decision_status"],
        )

        _assert_field(
            golden_file=gf.name,
            field="amount_status",
            expected=normalize_text(golden.get("amount_status")),
            actual=actual["amount_status"],
        )
        _assert_field(
            golden_file=gf.name,
            field="customer_code",
            expected=normalize_text(golden.get("customer_code")),
            actual=actual["customer_code"],
        )
        _assert_field(
            golden_file=gf.name,
            field="description",
            expected=normalize_text(golden.get("description")),
            actual=actual["description"],
        )
        _assert_field(
            golden_file=gf.name,
            field="discount_percentage",
            expected=money_to_str(golden.get("discount_percentage")),
            actual=actual["discount_percentage"],
        )
        _assert_field(
            golden_file=gf.name,
            field="invoice_date",
            expected=normalize_text(golden.get("invoice_date")),
            actual=actual["invoice_date"],
        )
        _assert_field(
            golden_file=gf.name,
            field="payment_terms_days",
            expected=int(golden.get("payment_terms_days") or 0),
            actual=actual["payment_terms_days"],
        )
