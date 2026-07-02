"""Per-batch regression matrix for legacy/settlement isolation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from logic.payment_engine import batch_requires_settlement, calculate_payments
from logic.settlement_call_guard import allocation_edges_from_result
from logic.shadow_mode import batch_id_from_invoices, run_shadow_validation

_ROOT = Path(__file__).resolve().parents[1]
_SETTLEMENT_EXPECTATIONS = (
    _ROOT / "tests" / "credit_dataset" / "settlement_expectations.json"
)
_DEFAULT_REPORT = _ROOT / "reports" / "batch_regression_matrix.json"


@dataclass(frozen=True)
class MatrixEntry:
    batch_id: str
    batch_type: Literal["no-credit", "credit"]
    expected: dict[str, Any]
    actual: dict[str, Any]
    status: Literal["PASS", "FAIL"]
    details: str = ""


def _base_invoice(**overrides: Any) -> dict[str, Any]:
    inv: dict[str, Any] = {
        "supplier_name": "Supplier A",
        "match_status": "confirmed",
        "type": "invoice",
        "invoice_number": "INV-1",
        "source_file": "/tmp/inv1.pdf",
        "amount": 100.0,
        "iban": "NL20INGB0001234567",
        "invoice_date": "2026-01-15",
        "invoice_date_source": "parsed",
        "supplier_term_trusted": True,
        "supplier_payment_term_days_raw": 30,
    }
    inv.update(overrides)
    return inv


def synthetic_no_credit_19() -> list[dict[str, Any]]:
    return [
        _base_invoice(
            supplier_name=f"Supplier {i % 6}",
            invoice_number=f"INV{i:04d}",
            source_file=f"/tmp/inv{i}.pdf",
        )
        for i in range(19)
    ]


def wasco_credit_batch() -> list[dict[str, Any]]:
    return [
        _base_invoice(supplier_name="Wasco", invoice_number="5660148", amount=65.51, source_file="5660148.pdf"),
        _base_invoice(supplier_name="Wasco", invoice_number="6305463", amount=41.16, source_file="6305463.pdf"),
        _base_invoice(
            supplier_name="Wasco",
            invoice_number="6230076",
            amount=66.67,
            type="credit_note",
            source_file="6230076.pdf",
        ),
    ]


def vte_credit_batch() -> list[dict[str, Any]]:
    return [
        _base_invoice(supplier_name="VTE", invoice_number="VF2600048", amount=245.15, source_file="VF2600048.pdf"),
        _base_invoice(supplier_name="VTE", invoice_number="VF2601788", amount=135.35, source_file="VF2601788.pdf"),
        _base_invoice(
            supplier_name="VTE",
            invoice_number="VCR2600003",
            amount=33.0,
            type="credit_note",
            source_file="VCR2600003.pdf",
            referenced_invoice_numbers=["VF2600115"],
        ),
        _base_invoice(
            supplier_name="VTE",
            invoice_number="VCR2600064",
            amount=408.57,
            type="credit_note",
            source_file="VCR2600064.pdf",
            referenced_invoice_numbers=["VF2601543"],
        ),
    ]


def _evaluate_no_credit(batch_id: str, invoices: list[dict[str, Any]]) -> MatrixEntry:
    accepted = sum(1 for inv in invoices if inv.get("match_status") in {"matched", "new", "confirmed", "reviewed"})
    result = calculate_payments(invoices)
    shadow = run_shadow_validation(invoices, result, batch_id=batch_id, log=False)
    actual = {
        "legacy_rows": len(result.legacy_payments or []),
        "settlement_groups": len(result.settlement_groups),
        "review_docs": len(result.review_documents),
        "allocation_edges": allocation_edges_from_result(result.settlement_groups),
        "pipeline": result.pipeline,
        "shadow_status": shadow.status,
    }
    expected = {
        "legacy_rows": accepted,
        "settlement_groups": 0,
        "review_docs": len(result.review_documents),
        "pipeline": "legacy",
        "shadow_status": "PASS",
    }
    ok = (
        actual["legacy_rows"] == expected["legacy_rows"]
        and actual["settlement_groups"] == 0
        and actual["allocation_edges"] == 0
        and actual["pipeline"] == "legacy"
        and shadow.status == "PASS"
    )
    return MatrixEntry(
        batch_id=batch_id,
        batch_type="no-credit",
        expected=expected,
        actual=actual,
        status="PASS" if ok else "FAIL",
        details="" if ok else f"shadow_diffs={shadow.diffs}",
    )


def _evaluate_credit(batch_id: str, invoices: list[dict[str, Any]], spec: dict[str, Any]) -> MatrixEntry:
    result = calculate_payments(invoices)
    shadow = run_shadow_validation(invoices, result, batch_id=batch_id, log=False)
    groups = result.settlement_groups
    actual: dict[str, Any] = {
        "settlement_groups": len(groups),
        "pipeline": result.pipeline,
        "shadow_status": shadow.status,
    }
    if spec.get("expected_final_amount_due") is not None and groups:
        exportable = [g for g in groups if g.get("exportable")]
        if exportable:
            actual["final_amount_due"] = str(exportable[0].get("final_amount_due"))
    if spec.get("expected_allocation_statuses") and groups:
        merged = []
        for g in groups:
            merged.extend([a.get("status") for a in (g.get("credit_allocation") or [])])
        actual["allocation_statuses"] = merged

    expected: dict[str, Any] = {
        "settlement_groups": spec.get("expected_groups"),
        "pipeline": "settlement",
        "shadow_status": "PASS",
    }
    if spec.get("expected_final_amount_due") is not None:
        expected["final_amount_due"] = spec["expected_final_amount_due"]
    if spec.get("expected_allocation_statuses"):
        expected["allocation_statuses"] = spec["expected_allocation_statuses"]

    ok = (
        actual["settlement_groups"] == expected["settlement_groups"]
        and actual["pipeline"] == "settlement"
        and shadow.status == "PASS"
    )
    if expected.get("final_amount_due") is not None:
        ok = ok and actual.get("final_amount_due") == expected["final_amount_due"]
    if expected.get("allocation_statuses") is not None:
        ok = ok and actual.get("allocation_statuses") == expected["allocation_statuses"]

    return MatrixEntry(
        batch_id=batch_id,
        batch_type="credit",
        expected=expected,
        actual=actual,
        status="PASS" if ok else "FAIL",
        details="" if ok else f"shadow_diffs={shadow.diffs}",
    )


def _golden_single_batches() -> list[tuple[str, list[dict[str, Any]]]]:
    try:
        from tests.golden_test_support import load_matched_invoices
    except ImportError:
        return []
    try:
        by_pdf = load_matched_invoices(use_cache=True)
    except Exception:
        return []
    batches: list[tuple[str, list[dict[str, Any]]]] = []
    for pdf_name, inv in sorted(by_pdf.items()):
        if batch_requires_settlement([inv]):
            continue
        batch_id = f"golden:{pdf_name}"
        batches.append((batch_id, [inv]))
    return batches


def run_regression_matrix(*, include_golden_singles: bool = True) -> list[MatrixEntry]:
    entries: list[MatrixEntry] = []

    inv19 = synthetic_no_credit_19()
    entries.append(_evaluate_no_credit("synthetic_19_invoices", inv19))

    if include_golden_singles:
        for batch_id, invoices in _golden_single_batches()[:20]:
            entries.append(_evaluate_no_credit(batch_id, invoices))

    settlement_specs: dict[str, Any] = {}
    if _SETTLEMENT_EXPECTATIONS.is_file():
        settlement_specs = json.loads(_SETTLEMENT_EXPECTATIONS.read_text(encoding="utf-8"))

    wasco_spec = settlement_specs.get("wasco_batch", {})
    entries.append(_evaluate_credit("wasco_batch", wasco_credit_batch(), wasco_spec))

    vte_spec = settlement_specs.get("vte_batch", {})
    entries.append(_evaluate_credit("vte_batch", vte_credit_batch(), vte_spec))

    return entries


def format_matrix_entry(entry: MatrixEntry) -> str:
    lines = [
        f"BATCH_TYPE={entry.batch_type} BATCH_ID={entry.batch_id}",
        f"EXPECTED: {entry.expected}",
        f"ACTUAL: {entry.actual}",
        f"STATUS: {entry.status}",
    ]
    if entry.details:
        lines.append(f"DETAILS: {entry.details}")
    return "\n".join(lines)


def write_regression_report(
    entries: list[MatrixEntry],
    path: Path | None = None,
) -> Path:
    out = path or _DEFAULT_REPORT
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": {
            "total": len(entries),
            "passed": sum(1 for e in entries if e.status == "PASS"),
            "failed": sum(1 for e in entries if e.status == "FAIL"),
        },
        "entries": [asdict(e) for e in entries],
    }
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return out


def all_passed(entries: list[MatrixEntry]) -> bool:
    return all(e.status == "PASS" for e in entries)
