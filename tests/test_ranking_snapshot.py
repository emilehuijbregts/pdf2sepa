"""PHASE A — BEHAVIOR LOCK (observability only).

Captures, per golden PDF and per field, the current candidate pool, each
candidate's canonical rank key, the chosen winner, and the resolver outcome.

This is a *reference oracle*: it does not change any ranking, resolver, status
or authority behavior. It only records what the pipeline does today so that the
Phase B single-authority migration can diff against a frozen baseline.

The snapshot is committed at:
    tests/snapshots/phase_a_ranking_snapshot.json

To (re)generate after an *intentional* change, run:
    UPDATE_PHASE_A_SNAPSHOT=1 python3 -m pytest tests/test_ranking_snapshot.py

Phase A exit criterion: this test is green AND the committed snapshot shows zero
winner changes vs pre-Phase-A behavior.
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
from parser.field_candidates import IdentFieldCandidate, candidate_rank_key
from parser.field_model import ALL_FIELD_IDS, CandidateCollection, FieldCandidate
from parser.supplier_db import SupplierDB
from parser.supplier_matcher import match_suppliers

APP_BASE = Path(__file__).resolve().parents[1]
GOLDEN_PDFS_DIR = APP_BASE / "tests" / "golden_dataset" / "pdfs"
SNAPSHOT_PATH = APP_BASE / "tests" / "snapshots" / "phase_a_ranking_snapshot.json"


@pytest.fixture(scope="module")
def invoices_by_pdf() -> dict[str, dict]:
    """Production load path (mirrors tests/test_golden_dataset.py::pipeline_output)."""
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
    db = SupplierDB(path=str(user_data_dir / "suppliers.json"))
    matched = match_suppliers(invoices, db)
    strip_raw_text_from_invoices(matched)
    # Run the engine for full parity with the golden pipeline (does not change *_result).
    calculate_payments(matched, session_date=date.today())

    by_pdf: dict[str, dict] = {}
    for inv in matched:
        key = pdf_filename(inv.get("source_file"))
        if not key or key in by_pdf:
            continue
        by_pdf[key] = inv
    return by_pdf

# Resolver semantics: the resolver ranks via candidate_rank_key WITHOUT
# prefer_k_prefix (see parser/field_resolver.py:_candidate_rank_tuple). The
# parse-time divergence (prefer_k_prefix for customer_number) is documented
# separately in tests/test_pipeline_parity.py; here we record the key that
# actually decides the final winner.
def _resolver_rank_key(field_id: str, fc: FieldCandidate) -> list[Any]:
    ident = IdentFieldCandidate(
        value=str(fc.value) if fc.value is not None else "",
        source=str(fc.source or ""),
        confidence=int(fc.confidence or 0),
        context=str(fc.context or ""),
        label=str(fc.label or ""),
        meta=dict(fc.meta or {}),
    )
    if "field_id" not in ident.meta:
        ident.meta["field_id"] = field_id
    return list(candidate_rank_key(ident))


def _value_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _field_snapshot(field_id: str, fr) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for fc in fr.candidates:
        candidates.append(
            {
                "value": _value_str(fc.value),
                "source": str(fc.source or ""),
                "confidence": int(fc.confidence or 0),
                "rank_key": _resolver_rank_key(field_id, fc),
            }
        )
    # Deterministic order: by rank key (desc) then value, mirroring selection.
    candidates.sort(key=lambda c: (c["rank_key"], c["value"]), reverse=True)
    return {
        "winner": {
            "value": _value_str(fr.selected_value),
            "source": str(fr.source or ""),
            "status": str(fr.status or ""),
            "confidence": int(fr.confidence or 0),
        },
        "candidates": candidates,
    }


def _build_snapshot(invoices_by_pdf: dict[str, dict]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for pdf in sorted(invoices_by_pdf):
        inv = invoices_by_pdf[pdf]
        collection = CandidateCollection.from_invoice_dict(inv)
        per_field: dict[str, Any] = {}
        for field_id in ALL_FIELD_IDS:
            fr = collection.get(field_id)
            if fr is None:
                continue
            per_field[field_id] = _field_snapshot(field_id, fr)
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
            exp = exp_fields.get(field_id)
            act = act_fields.get(field_id)
            if exp == act:
                continue
            exp_w = (exp or {}).get("winner", {})
            act_w = (act or {}).get("winner", {})
            if exp_w != act_w:
                diffs.append(
                    f"{pdf} :: {field_id} :: WINNER CHANGED "
                    f"old={exp_w.get('value')!r}({exp_w.get('source')}/{exp_w.get('status')}) "
                    f"new={act_w.get('value')!r}({act_w.get('source')}/{act_w.get('status')})"
                )
            else:
                diffs.append(f"{pdf} :: {field_id} :: candidate pool / rank keys changed")
    return diffs


def test_phase_a_ranking_snapshot(invoices_by_pdf: dict[str, dict]) -> None:
    if not invoices_by_pdf:
        pytest.skip("No invoices produced by pipeline")

    actual = _build_snapshot(invoices_by_pdf)

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
        "Phase A behavior lock violated — ranking/winner changed vs committed snapshot.\n"
        "If this change is intentional, regenerate with UPDATE_PHASE_A_SNAPSHOT=1 and document it.\n\n"
        + "\n".join(diffs[:40])
        + (f"\n... and {len(diffs) - 40} more" if len(diffs) > 40 else "")
    )
