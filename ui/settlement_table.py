"""Settlement table helpers — SSOT populate support."""

from __future__ import annotations

from typing import Any

from logic.engine_result import EngineResult, SettlementGroupOutput


def payment_stub_from_group(group: SettlementGroupOutput) -> dict[str, Any]:
    """Minimal dict for row field resolution (not legacy export)."""
    return {
        "supplier_name": group.get("supplier_name"),
        "iban": group.get("iban"),
        "amount": group.get("final_amount_due"),
        "amount_display": group.get("amount_display"),
        "description": group.get("description"),
        "invoice_number": group.get("invoice_number"),
        "_source_file": group.get("_source_file"),
        "credit_notes_applied": group.get("credit_notes_applied") or [],
        "settlement": group.get("breakdown"),
        "warning": group.get("warning"),
        "invoice_date": group.get("invoice_date"),
        "invoice_date_source": group.get("invoice_date_source"),
        "supplier_term_trusted": group.get("supplier_term_trusted"),
        "supplier_payment_term_days_effective": group.get("supplier_payment_term_days_effective"),
        "date_mode": group.get("date_mode"),
        "execution_date": group.get("execution_date"),
        "decision_trace": group.get("decision_trace"),
        "decision": group.get("decision"),
        "settlement_group_id": group.get("group_id"),
        "settlement_status": group.get("settlement_status"),
        "iban_mismatch": group.get("iban_mismatch"),
    }


def settlement_group_rows(engine_result: EngineResult) -> list[tuple[dict[str, Any], SettlementGroupOutput]]:
    return [(payment_stub_from_group(g), g) for g in engine_result.settlement_groups]


def review_documents_as_error_buckets(review_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str | None], list[dict]] = {}
    for doc in review_documents:
        reason = str(
            doc.get("_review_reason") or doc.get("decision", {}).get("reason_code") or "needs_review"
        )
        sup = doc.get("supplier_name")
        buckets.setdefault((reason, sup if sup is not None else None), []).append(doc)
    return [
        {"reason": reason, "supplier_name": sup, "invoices": invs}
        for (reason, sup), invs in sorted(buckets.items(), key=lambda x: (x[0][0], x[0][1] or ""))
    ]


def engine_result_views(result: EngineResult) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Payment-shaped stubs + error buckets from EngineResult (tests/tooling only)."""
    if result.legacy_payments is not None:
        payments = list(result.legacy_payments)
    else:
        payments = [payment_stub_from_group(g) for g in result.settlement_groups]
    errors = review_documents_as_error_buckets(result.review_documents)
    return payments, errors


def exportable_engine_result_views(result: EngineResult) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Exportable payment stubs + error buckets (verify/export tooling)."""
    from logic.settlement_export import exportable_groups, exportable_legacy_payments

    if result.legacy_payments is not None:
        payments = exportable_legacy_payments(result)
    else:
        payments = [payment_stub_from_group(g) for g in exportable_groups(result).groups]
    errors = review_documents_as_error_buckets(result.review_documents)
    return payments, errors


def credit_document_ids_from_batch(invoices: list[dict[str, Any]]) -> set[str]:
    from logic.credit_settlement import document_id

    out: set[str] = set()
    for inv in invoices:
        if str(inv.get("type") or "") == "credit_note":
            out.add(document_id({"raw": inv}))
    return out
