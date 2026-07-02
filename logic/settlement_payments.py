"""Settlement group building and description formatting (SSOT business logic)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from logic.credit_settlement import (
    SETTLEMENT_MANUAL_REVIEW,
    SETTLEMENT_OK,
    SETTLEMENT_REFUND_REQUIRED,
    SettlementGroup,
    document_id,
    settlement_group_to_dict,
)
from logic.engine_result import SettlementGroupOutput
from logic.payment_amounts import format_eur_xml
from logic.payment_decisions import (
    DECISION_EXCLUDED,
    DECISION_INCLUDED,
    DECISION_NEEDS_REVIEW,
    REASON_CREDIT_REFUND_REQUIRED,
    REASON_EXPORT_ALLOWED,
    REASON_LOW_CONFIDENCE,
    REASON_MISSING_AMOUNT,
    PaymentDecision,
)

SETTLEMENT_ZERO_AMOUNT = "zero_amount"


def format_settlement_description(
    customer_number: str | None,
    invoice_numbers: list[str],
    credit_numbers: list[str],
) -> str:
    """``{klant} / INV1 - INV2 - CR1`` — facturen alfabetisch, daarna credits."""
    invs = sorted({str(n).strip() for n in invoice_numbers if str(n).strip()})
    creds = sorted({str(n).strip() for n in credit_numbers if str(n).strip()})
    parts = invs + creds
    if not parts:
        return ""
    chain = " - ".join(parts)
    cust = str(customer_number or "").strip()
    if cust:
        return f"{cust} / {chain}"
    return chain


def _pdf_basename(source_file: str | None) -> str:
    if not source_file:
        return ""
    s = str(source_file).replace("\\", "/")
    return s.rsplit("/", 1)[-1]


def _customer_from_member(member_documents: list[dict[str, Any]]) -> str | None:
    for doc in member_documents:
        raw = doc.get("raw") if isinstance(doc.get("raw"), dict) else doc
        if not isinstance(raw, dict):
            continue
        cn = str(raw.get("customer_number") or "").strip()
        if cn and cn != "?":
            return cn
    return None


def _structured_member_documents(member_documents: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    structured: dict[str, list[dict[str, Any]]] = {"invoices": [], "credits": []}
    for doc in member_documents:
        raw = doc.get("raw") if isinstance(doc.get("raw"), dict) else doc
        if not isinstance(raw, dict):
            continue
        if str(raw.get("type") or "invoice") == "credit_note":
            structured["credits"].append(doc)
        else:
            structured["invoices"].append(doc)
    return structured


def _credit_allocation_from_group(group: SettlementGroup) -> list[dict[str, Any]]:
    return [
        {
            "credit_id": allocation.credit_id,
            "credit_number": allocation.credit_number,
            "invoice_id": allocation.invoice_id,
            "invoice_number": allocation.invoice_number,
            "amount_applied": allocation.amount_applied,
            "remaining_balance": allocation.remaining_balance,
            "status": allocation.status,
        }
        for allocation in group.credit_allocation
    ]


def _resolve_settlement_status(group: SettlementGroup, final_payable: Decimal) -> str:
    if group.status == SETTLEMENT_REFUND_REQUIRED:
        return SETTLEMENT_REFUND_REQUIRED
    if group.status == SETTLEMENT_MANUAL_REVIEW:
        return SETTLEMENT_MANUAL_REVIEW
    if final_payable <= Decimal("0.00"):
        return SETTLEMENT_ZERO_AMOUNT
    if group.status == SETTLEMENT_OK:
        return SETTLEMENT_OK
    return str(group.status)


def _is_exportable(settlement_status: str, final_payable: Decimal) -> bool:
    if settlement_status in (
        SETTLEMENT_REFUND_REQUIRED,
        SETTLEMENT_MANUAL_REVIEW,
        SETTLEMENT_ZERO_AMOUNT,
    ):
        return False
    return final_payable > Decimal("0.00")


def build_settlement_group_output(
    group: SettlementGroup,
    *,
    final_amount_due: Decimal,
    iban: str,
    decision: PaymentDecision,
    decision_trace: dict[str, Any],
    member_documents: list[dict[str, Any]],
    credit_notes_applied: list[str],
    primary_invoice_number: str,
    primary_source_file: str | None,
    invoice_date: str | None,
    invoice_date_source: str,
    execution_date: str,
    supplier_term_trusted: bool,
    raw_term: int,
    effective_term: int,
    warning: str | None,
    iban_mismatch: bool,
    engine_version: str,
) -> SettlementGroupOutput:
    inv_numbers = [line.invoice_number for line in group.invoices if line.invoice_number]
    cred_numbers = [line.invoice_number for line in group.credits if line.invoice_number]
    customer = _customer_from_member(member_documents)
    description = format_settlement_description(customer, inv_numbers, cred_numbers)
    settlement_status = _resolve_settlement_status(group, final_amount_due)
    exportable = _is_exportable(settlement_status, final_amount_due)
    if str(decision.get("status") or "") != DECISION_INCLUDED:
        exportable = False

    credit_allocation = _credit_allocation_from_group(group)
    if any(str(a.get("status") or "").startswith("unallocated_") for a in credit_allocation):
        exportable = False

    amount_display = format_eur_xml(final_amount_due).replace(".", ",")
    breakdown = settlement_group_to_dict(group)
    structured_members = _structured_member_documents(member_documents)

    return SettlementGroupOutput(
        group_id=group.group_id,
        supplier_name=group.supplier_name,
        iban=iban,
        customer_number=customer,
        description=description,
        final_amount_due=final_amount_due,
        exportable=exportable,
        settlement_status=settlement_status,
        decision=decision,
        breakdown=breakdown,
        member_documents=member_documents,
        member_documents_structured=structured_members,
        credit_allocation=credit_allocation,
        ownership={
            "group_id": group.group_id,
            "document_ids": [str(doc.get("document_id") or "") for doc in member_documents],
            "sealed": True,
        },
        decision_trace=decision_trace,
        amount_display=amount_display,
        invoice_number=primary_invoice_number or f"GRP-{group.group_id[:8]}",
        _source_file=primary_source_file,
        invoice_date=invoice_date,
        invoice_date_source=invoice_date_source,
        execution_date=execution_date,
        date_mode="direct",
        supplier_term_trusted=supplier_term_trusted,
        supplier_payment_term_days_raw=raw_term,
        supplier_payment_term_days_effective=effective_term,
        credit_notes_applied=credit_notes_applied,
        warning=warning,
        iban_mismatch=iban_mismatch,
        engine_version=engine_version,
    )


def group_decision_from_invoice_decisions(
    decisions: list[PaymentDecision],
    *,
    settlement_status: str,
    refund_detail: str | None = None,
) -> PaymentDecision:
    """Merge per-invoice decisions into one group decision."""
    if settlement_status == SETTLEMENT_REFUND_REQUIRED:
        return {
            **decisions[0],
            "status": DECISION_NEEDS_REVIEW,
            "reason_code": REASON_CREDIT_REFUND_REQUIRED,
            "reason_detail": refund_detail,
            "requires_rerun": True,
        }
    if settlement_status == SETTLEMENT_ZERO_AMOUNT:
        return {
            **decisions[0],
            "status": DECISION_EXCLUDED,
            "reason_code": "zero_amount",
            "reason_detail": None,
            "requires_rerun": False,
        }
    if settlement_status == SETTLEMENT_MANUAL_REVIEW:
        return {
            **decisions[0],
            "status": DECISION_NEEDS_REVIEW,
            "reason_code": "credit_match_needs_review",
            "requires_rerun": True,
        }
    statuses = {d.get("status") for d in decisions}
    if DECISION_EXCLUDED in statuses:
        worst = next(d for d in decisions if d.get("status") == DECISION_EXCLUDED)
        return dict(worst)
    if DECISION_NEEDS_REVIEW in statuses:
        worst = next(d for d in decisions if d.get("status") == DECISION_NEEDS_REVIEW)
        return dict(worst)
    return {
        **decisions[0],
        "status": DECISION_INCLUDED,
        "reason_code": REASON_EXPORT_ALLOWED,
        "requires_rerun": False,
    }


def sort_settlement_groups(groups: list[SettlementGroupOutput]) -> list[SettlementGroupOutput]:
    return sorted(
        groups,
        key=lambda g: (
            str(g.get("supplier_name") or "").lower(),
            str(g.get("invoice_number") or "").lower(),
            str(g.get("group_id") or ""),
        ),
    )
