"""PHASE A / A.1 — BEHAVIOR LOCK (observability only).

Captures dual call-site observability (parse vs resolver), resolver amount keys,
and production outcome metadata. Does not change ranking, resolver, or outputs.

Snapshot: tests/snapshots/phase_a_ranking_snapshot.json

Regenerate: UPDATE_PHASE_A_SNAPSHOT=1 python3 -m pytest tests/test_ranking_snapshot.py
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from logic.golden_dataset import pdf_filename
from logic.invoice_folder_loader import (
    load_invoices_from_folder,
    strip_raw_text_from_invoices,
)
from logic.paths import read_user_data_root
from logic.payment_engine import calculate_payments
from logic.settings import load_settings, merge_debtor_with_defaults
from parser.field_model import ALL_FIELD_IDS
from parser.supplier_db import SupplierDB
from parser.supplier_matcher import match_suppliers
from tests.snapshot_observability_helpers import (
    build_field_observability,
    capture_parse_before_match,
    supplier_for_matched,
)

APP_BASE = Path(__file__).resolve().parents[1]
GOLDEN_PDFS_DIR = APP_BASE / "tests" / "golden_dataset" / "pdfs"
SNAPSHOT_PATH = APP_BASE / "tests" / "snapshots" / "phase_a_ranking_snapshot.json"


@pytest.fixture(scope="module")
def observability_bundle() -> dict[str, Any]:
    """Production load path + frozen parse-stage copies before match_suppliers."""
    pdfs = (
        sorted(p for p in GOLDEN_PDFS_DIR.glob("*.pdf") if p.is_file())
        if GOLDEN_PDFS_DIR.exists()
        else []
    )
    if not pdfs:
        pytest.skip("No PDFs in tests/golden_dataset/pdfs/")

    user_data_dir = read_user_data_root(APP_BASE)
    settings = load_settings(str(user_data_dir / "settings.json"))
    debtor = merge_debtor_with_defaults(settings.get("debtor"))
    debtor_iban = (debtor.get("iban") or "").strip() or None
    debtor_kvk = (debtor.get("kvk") or "").strip() or None
    debtor_vat = (debtor.get("vat") or "").strip() or None

    invoices = load_invoices_from_folder(
        GOLDEN_PDFS_DIR,
        debtor_iban=debtor_iban,
        debtor_kvk=debtor_kvk,
        debtor_vat=debtor_vat,
    )
    parse_by_pdf = capture_parse_before_match(invoices)

    db = SupplierDB(path=str(user_data_dir / "suppliers.json"))
    matched = match_suppliers(invoices, db)
    strip_raw_text_from_invoices(matched)
    calculate_payments(matched, session_date=date.today())

    matched_by_pdf: dict[str, dict] = {}
    for inv in matched:
        key = pdf_filename(inv.get("source_file"))
        if not key or key in matched_by_pdf:
            continue
        matched_by_pdf[key] = inv

    return {
        "parse_by_pdf": parse_by_pdf,
        "matched_by_pdf": matched_by_pdf,
        "db": db,
    }


def _production_winner(field_blob: dict[str, Any]) -> dict[str, Any]:
    prod = field_blob.get("production")
    if isinstance(prod, dict) and isinstance(prod.get("winner"), dict):
        return prod["winner"]
    if isinstance(field_blob.get("winner"), dict):
        return field_blob["winner"]
    return {}


def _build_snapshot(bundle: dict[str, Any]) -> dict[str, Any]:
    parse_by_pdf = bundle["parse_by_pdf"]
    matched_by_pdf = bundle["matched_by_pdf"]
    db: SupplierDB = bundle["db"]

    snapshot: dict[str, Any] = {}
    for pdf in sorted(matched_by_pdf):
        inv_matched = matched_by_pdf[pdf]
        inv_parse = parse_by_pdf.get(pdf)
        if inv_parse is None:
            continue
        supplier = supplier_for_matched(inv_matched, db)
        per_field: dict[str, Any] = {}
        for field_id in ALL_FIELD_IDS:
            per_field[field_id] = build_field_observability(
                inv_parse, inv_matched, supplier, db, field_id
            )
        snapshot[pdf] = per_field
    return snapshot


def _diff_snapshots(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    for pdf in sorted(set(expected) | set(actual)):
        exp_fields = expected.get(pdf)
        act_fields = actual.get(pdf)
        if exp_fields is None:
            diffs.append(f"{pdf}: present now but missing in committed snapshot")
            continue
        if act_fields is None:
            diffs.append(f"{pdf}: in committed snapshot but not produced now")
            continue
        for field_id in sorted(set(exp_fields) | set(act_fields)):
            exp = exp_fields.get(field_id) or {}
            act = act_fields.get(field_id) or {}
            exp_w = _production_winner(exp)
            act_w = _production_winner(act)
            if exp_w == act_w:
                continue
            diffs.append(
                f"{pdf} :: {field_id} :: PRODUCTION WINNER CHANGED "
                f"old={exp_w.get('value')!r}({exp_w.get('source')}/{exp_w.get('status')}) "
                f"new={act_w.get('value')!r}({act_w.get('source')}/{act_w.get('status')})"
            )
    return diffs


def test_phase_a_ranking_snapshot(observability_bundle: dict[str, Any]) -> None:
    if not observability_bundle.get("matched_by_pdf"):
        pytest.skip("No invoices produced by pipeline")

    actual = _build_snapshot(observability_bundle)

    update = os.environ.get("UPDATE_PHASE_A_SNAPSHOT") == "1"
    if update or not SNAPSHOT_PATH.exists():
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(
            json.dumps(actual, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        reason = "UPDATE_PHASE_A_SNAPSHOT=1" if update else "no committed snapshot yet"
        pytest.skip(f"Phase A snapshot written ({reason}); rerun to validate against it")

    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8") or "{}")
    diffs = _diff_snapshots(expected, actual)
    assert not diffs, (
        "Phase A behavior lock violated — production winner changed vs committed snapshot.\n"
        "If intentional, regenerate with UPDATE_PHASE_A_SNAPSHOT=1 and document it.\n\n"
        + "\n".join(diffs[:40])
        + (f"\n... and {len(diffs) - 40} more" if len(diffs) > 40 else "")
    )
