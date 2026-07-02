"""Credit invoice settlement: group-level netting on top of credit matching."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from logic.credit_matching import CreditMatchResult
from logic.payment_amounts import amount_to_decimal

_MONEY_TOL = Decimal("0.01")
_MONEY_QUANT = Decimal("0.01")

SETTLEMENT_OK = "ok"
SETTLEMENT_REFUND_REQUIRED = "refund_required"
SETTLEMENT_MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True)
class SettlementLine:
    document_id: str
    invoice_number: str
    doc_type: str
    gross_amount: Decimal
    amount_applied: Decimal
    remaining_balance: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class SettlementAllocation:
    credit_id: str
    credit_number: str
    invoice_id: str | None
    invoice_number: str | None
    amount_applied: Decimal
    remaining_balance: Decimal
    status: str


@dataclass(frozen=True)
class SettlementGroup:
    group_id: str
    supplier_name: str
    invoices: tuple[SettlementLine, ...]
    credits: tuple[SettlementLine, ...]
    invoices_total: Decimal
    credits_total: Decimal
    final_amount_due: Decimal
    status: str
    refund_amount: Decimal | None
    match_methods: tuple[str, ...]
    warnings: tuple[str, ...]
    credit_allocation: tuple[SettlementAllocation, ...] = ()


@dataclass(frozen=True)
class SupplierSettlementResult:
    groups: tuple[SettlementGroup, ...]
    unlinked_credits: tuple[SettlementLine, ...]
    unlinked_invoices: tuple[SettlementLine, ...]


def document_id(doc: dict[str, Any]) -> str:
    raw = doc.get("raw") or doc
    src = str(raw.get("source_file") or "").strip()
    if src:
        return src
    inv_no = str(raw.get("invoice_number") or "").strip()
    if inv_no:
        return f"inv:{inv_no}"
    return f"anon:{id(raw)}"


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_QUANT)


def _gross_amount(normalized: dict[str, Any]) -> Decimal:
    raw = normalized.get("amount_dec")
    if isinstance(raw, Decimal):
        return _quantize_money(raw.copy_abs())
    return _quantize_money(amount_to_decimal(raw).copy_abs())


def _group_id_from_doc_ids(doc_ids: list[str]) -> str:
    stable = "|".join(sorted(doc_ids))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]


def _invoice_line(normalized: dict[str, Any], *, credit_applied: Decimal = Decimal("0.00")) -> SettlementLine:
    raw = normalized.get("raw") or normalized
    return SettlementLine(
        document_id=document_id(normalized),
        invoice_number=str(raw.get("invoice_number") or ""),
        doc_type="invoice",
        gross_amount=_gross_amount(normalized),
        amount_applied=_quantize_money(credit_applied),
    )


def _credit_line(
    normalized: dict[str, Any],
    *,
    amount_applied: Decimal | None = None,
    remaining_balance: Decimal | None = None,
) -> SettlementLine:
    raw = normalized.get("raw") or normalized
    gross = _gross_amount(normalized)
    applied = gross if amount_applied is None else _quantize_money(amount_applied)
    remaining = (
        _quantize_money(max(gross - applied, Decimal("0.00")))
        if remaining_balance is None
        else _quantize_money(remaining_balance)
    )
    return SettlementLine(
        document_id=document_id(normalized),
        invoice_number=str(raw.get("invoice_number") or ""),
        doc_type="credit_note",
        gross_amount=gross,
        amount_applied=applied,
        remaining_balance=remaining,
    )


def _connected_components(
    nodes: set[str],
    edges: list[tuple[str, str]],
) -> list[set[str]]:
    parent: dict[str, str] = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in edges:
        if a in nodes and b in nodes:
            union(a, b)

    components: dict[str, set[str]] = {}
    for n in nodes:
        root = find(n)
        components.setdefault(root, set()).add(n)
    return list(components.values())


def _credit_applied_per_invoice(
    match_results: list[CreditMatchResult],
) -> dict[str, Decimal]:
    applied: dict[str, Decimal] = {}
    for result in match_results:
        for alloc in result.allocation:
            prev = applied.get(alloc.invoice_id, Decimal("0.00"))
            applied[alloc.invoice_id] = _quantize_money(prev + alloc.amount_applied)
    return applied


def _credit_gross_by_id(normalized_credits: list[dict[str, Any]]) -> dict[str, Decimal]:
    return {document_id(n): _gross_amount(n) for n in normalized_credits}


def _allocation_graph(
    match_results: list[CreditMatchResult],
    normalized_credits: list[dict[str, Any]],
) -> tuple[SettlementAllocation, ...]:
    """Canonical engine allocation graph: applied edges plus unresolved credit nodes."""
    credit_gross = _credit_gross_by_id(normalized_credits)
    traces: list[SettlementAllocation] = []
    for result in match_results:
        credit_id = document_id({"raw": result.credit_invoice})
        credit_number = str(result.credit_invoice.get("invoice_number") or "")
        applied_total = Decimal("0.00")
        for alloc in result.allocation:
            applied = _quantize_money(alloc.amount_applied)
            applied_total = _quantize_money(applied_total + applied)
            traces.append(
                SettlementAllocation(
                    credit_id=credit_id,
                    credit_number=credit_number,
                    invoice_id=alloc.invoice_id,
                    invoice_number=alloc.invoice_number,
                    amount_applied=applied,
                    remaining_balance=Decimal("0.00"),
                    status="matched",
                )
            )

        remaining = _quantize_money(result.remaining_credit)
        if remaining > _MONEY_TOL:
            traces.append(
                SettlementAllocation(
                    credit_id=credit_id,
                    credit_number=credit_number,
                    invoice_id=None,
                    invoice_number=None,
                    amount_applied=Decimal("0.00"),
                    remaining_balance=remaining,
                    status="unallocated_partial" if applied_total > _MONEY_TOL else "unallocated_full",
                )
            )
        elif not result.allocation and result.match_method == "manual_review":
            gross = credit_gross.get(credit_id, Decimal("0.00"))
            if gross > _MONEY_TOL:
                traces.append(
                    SettlementAllocation(
                        credit_id=credit_id,
                        credit_number=credit_number,
                        invoice_id=None,
                        invoice_number=None,
                        amount_applied=Decimal("0.00"),
                        remaining_balance=gross,
                        status="unallocated_full",
                    )
                )
    return tuple(traces)


def _allocation_for_component(
    component: set[str],
    graph: tuple[SettlementAllocation, ...],
) -> tuple[SettlementAllocation, ...]:
    return tuple(
        trace
        for trace in graph
        if trace.credit_id in component
        and (trace.invoice_id is None or trace.invoice_id in component)
    )


def _resolve_group_status(
    *,
    final_amount_due: Decimal,
    group_warnings: list[str],
    group_match_methods: list[str],
    has_unmatched_credit: bool,
    has_remaining_credit: bool,
) -> tuple[str, Decimal | None]:
    if has_unmatched_credit or has_remaining_credit:
        return SETTLEMENT_MANUAL_REVIEW, None
    if "credit_exceeds_available_invoices" in group_warnings and final_amount_due < -_MONEY_TOL:
        return SETTLEMENT_REFUND_REQUIRED, _quantize_money(final_amount_due.copy_abs())
    if any(m == "manual_review" for m in group_match_methods) and any(
        w in ("referenced_invoices_not_in_batch", "no_confident_match", "no_same_supplier_invoices")
        for w in group_warnings
    ):
        return SETTLEMENT_MANUAL_REVIEW, None
    if final_amount_due < -_MONEY_TOL:
        return SETTLEMENT_REFUND_REQUIRED, _quantize_money(final_amount_due.copy_abs())
    return SETTLEMENT_OK, None


def _build_settlement_group(
    component: set[str],
    *,
    supplier_name: str,
    normalized_invoices: list[dict[str, Any]],
    normalized_credits: list[dict[str, Any]],
    match_results: list[CreditMatchResult],
    credit_applied_map: dict[str, Decimal],
    allocation_graph: tuple[SettlementAllocation, ...],
) -> SettlementGroup:
    inv_by_id = {document_id(n): n for n in normalized_invoices}
    cred_by_id = {document_id(n): n for n in normalized_credits}

    invoice_lines: list[SettlementLine] = []
    credit_lines: list[SettlementLine] = []
    group_warnings: list[str] = []
    group_match_methods: list[str] = []
    has_unmatched_credit = False
    has_remaining_credit = False

    relevant_results = [
        r
        for r in match_results
        if document_id({"raw": r.credit_invoice}) in component
    ]
    group_allocations = _allocation_for_component(component, allocation_graph)
    for result in relevant_results:
        group_match_methods.append(result.match_method)
        group_warnings.extend(result.warnings)
        if any(a.status == "unallocated_full" for a in group_allocations):
            has_unmatched_credit = True
        if any(a.status == "unallocated_partial" for a in group_allocations):
            has_remaining_credit = True

    for doc_id in sorted(component):
        if doc_id in inv_by_id:
            applied = credit_applied_map.get(doc_id, Decimal("0.00"))
            invoice_lines.append(_invoice_line(inv_by_id[doc_id], credit_applied=applied))
        elif doc_id in cred_by_id:
            total_applied = Decimal("0.00")
            credit_results = [
                r
                for r in relevant_results
                if document_id({"raw": r.credit_invoice}) == doc_id
            ]
            for result in credit_results:
                total_applied = _quantize_money(
                    total_applied
                    + sum((a.amount_applied for a in result.allocation), Decimal("0.00"))
                )
            remaining_balance = _quantize_money(
                sum(
                    (
                        a.remaining_balance
                        for a in group_allocations
                        if a.credit_id == doc_id and a.status.startswith("unallocated_")
                    ),
                    Decimal("0.00"),
                )
            )
            credit_lines.append(
                _credit_line(
                    cred_by_id[doc_id],
                    amount_applied=total_applied,
                    remaining_balance=remaining_balance,
                )
            )

    invoices_total = _quantize_money(
        sum((line.gross_amount for line in invoice_lines), Decimal("0.00"))
    )
    credits_total = _quantize_money(
        sum((line.gross_amount for line in credit_lines), Decimal("0.00"))
    )
    final_amount_due = _quantize_money(invoices_total - credits_total)

    status, refund_amount = _resolve_group_status(
        final_amount_due=final_amount_due,
        group_warnings=group_warnings,
        group_match_methods=group_match_methods,
        has_unmatched_credit=has_unmatched_credit,
        has_remaining_credit=has_remaining_credit,
    )

    return SettlementGroup(
        group_id=_group_id_from_doc_ids(list(component)),
        supplier_name=supplier_name,
        invoices=tuple(sorted(invoice_lines, key=lambda x: x.document_id)),
        credits=tuple(sorted(credit_lines, key=lambda x: x.document_id)),
        invoices_total=invoices_total,
        credits_total=credits_total,
        final_amount_due=final_amount_due,
        status=status,
        refund_amount=refund_amount,
        match_methods=tuple(dict.fromkeys(group_match_methods)),
        warnings=tuple(dict.fromkeys(group_warnings)),
        credit_allocation=group_allocations,
    )


def compute_settlement_groups(
    match_results: list[CreditMatchResult],
    normalized_invoices: list[dict[str, Any]],
    normalized_credits: list[dict[str, Any]],
    *,
    supplier_name: str,
) -> SupplierSettlementResult:
    """Build settlement groups from match results and normalized batch documents."""
    from logic.settlement_call_guard import record_settlement_call

    record_settlement_call("compute_settlement_groups")
    inv_ids = {document_id(n) for n in normalized_invoices}
    cred_ids = {document_id(n) for n in normalized_credits}
    all_ids = inv_ids | cred_ids

    edges: list[tuple[str, str]] = []
    credit_applied_map = _credit_applied_per_invoice(match_results)
    allocation_graph = _allocation_graph(match_results, normalized_credits)

    for result in match_results:
        credit_id = document_id({"raw": result.credit_invoice})
        for alloc in result.allocation:
            edges.append((credit_id, alloc.invoice_id))

    components = _connected_components(all_ids, edges)

    groups: list[SettlementGroup] = []
    covered: set[str] = set()
    for component in sorted(components, key=lambda c: sorted(c)[0] if c else ""):
        covered |= component
        groups.append(
            _build_settlement_group(
                component,
                supplier_name=supplier_name,
                normalized_invoices=normalized_invoices,
                normalized_credits=normalized_credits,
                match_results=match_results,
                credit_applied_map=credit_applied_map,
                allocation_graph=allocation_graph,
            )
        )

    unlinked_inv_lines = tuple(
        sorted(
            [_invoice_line(n) for n in normalized_invoices if document_id(n) not in covered],
            key=lambda x: x.document_id,
        )
    )
    unlinked_cred_lines = tuple(
        sorted(
            [_credit_line(n) for n in normalized_credits if document_id(n) not in covered],
            key=lambda x: x.document_id,
        )
    )

    return SupplierSettlementResult(
        groups=tuple(groups),
        unlinked_credits=unlinked_cred_lines,
        unlinked_invoices=unlinked_inv_lines,
    )


def settlement_line_to_dict(line: SettlementLine) -> dict[str, str]:
    return {
        "document_id": line.document_id,
        "invoice_number": line.invoice_number,
        "doc_type": line.doc_type,
        "gross_amount": str(line.gross_amount),
        "amount_applied": str(line.amount_applied),
        "remaining_balance": str(line.remaining_balance),
    }


def settlement_allocation_to_dict(allocation: SettlementAllocation) -> dict[str, str | None]:
    return {
        "credit_id": allocation.credit_id,
        "credit_number": allocation.credit_number,
        "invoice_id": allocation.invoice_id,
        "invoice_number": allocation.invoice_number,
        "amount_applied": str(allocation.amount_applied),
        "remaining_balance": str(allocation.remaining_balance),
        "status": allocation.status,
    }


def settlement_group_to_dict(group: SettlementGroup) -> dict[str, Any]:
    allocations = [settlement_allocation_to_dict(a) for a in group.credit_allocation]
    return {
        "group_id": group.group_id,
        "supplier_name": group.supplier_name,
        "status": group.status,
        "final_amount_due": str(group.final_amount_due),
        "refund_amount": str(group.refund_amount) if group.refund_amount is not None else None,
        "match_methods": list(group.match_methods),
        "warnings": list(group.warnings),
        "allocations": allocations,
        "breakdown": {
            "invoices_total": str(group.invoices_total),
            "credits_total": str(group.credits_total),
            "allocations": allocations,
            "linked_groups": [
                {
                    "invoices": [settlement_line_to_dict(line) for line in group.invoices],
                    "credits": [settlement_line_to_dict(line) for line in group.credits],
                }
            ],
        },
    }


def credit_applied_on_invoice(
    invoice_norm: dict[str, Any],
    settlement: SupplierSettlementResult,
    match_results: list[CreditMatchResult],
) -> Decimal:
    """Credit amount applied to one invoice (from allocations)."""
    inv_id = document_id(invoice_norm)
    total = Decimal("0.00")
    for result in match_results:
        for alloc in result.allocation:
            if alloc.invoice_id == inv_id:
                total += alloc.amount_applied
    return _quantize_money(total)


def settlement_for_invoice(
    invoice_norm: dict[str, Any],
    settlement: SupplierSettlementResult,
) -> SettlementGroup | None:
    inv_id = document_id(invoice_norm)
    for group in settlement.groups:
        if any(line.document_id == inv_id for line in group.invoices):
            return group
    return None
