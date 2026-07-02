"""Apply user credit overrides to match results before settlement."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from logic.credit_matching import CreditAllocation, CreditMatchResult, _credit_amount, _invoice_amount, _invoice_id
from logic.credit_override_store import CreditOverride, CreditOverrideAllocation, OverrideSession
from logic.credit_settlement import document_id
from logic.payment_decisions import now_utc_iso

_MONEY_TOL = Decimal("0.01")


def _credit_doc_id(credit: dict[str, Any]) -> str:
    return document_id({"raw": credit})


def _invoice_lookup(invoices: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for inv in invoices:
        out[_credit_doc_id(inv)] = inv
        out[_invoice_id(inv)] = inv
        inv_no = str(inv.get("invoice_number") or "").strip()
        if inv_no:
            out[f"inv:{inv_no}"] = inv
    return out


def _override_map(session: OverrideSession | None) -> dict[str, CreditOverride]:
    if session is None:
        return {}
    out: dict[str, CreditOverride] = {}
    for o in session.overrides:
        if o.credit_document_id:
            out[o.credit_document_id] = o
    return out


def _build_from_override(
    credit: dict[str, Any],
    override: CreditOverride,
    invoice_by_id: dict[str, dict[str, Any]],
) -> tuple[CreditMatchResult | None, dict[str, Any] | None]:
    credit_amt = _credit_amount(credit)
    if credit_amt is None:
        return None, {
            "event": "override_skipped",
            "reason": "credit_amount_missing",
            "credit_document_id": override.credit_document_id,
            "at": now_utc_iso(),
        }

    if override.action == "detach":
        return (
            CreditMatchResult(
                credit_invoice=credit,
                linked_invoices=(),
                allocation=(),
                remaining_credit=credit_amt,
                match_method="user_override",
                confidence=100,
                warnings=("user_detached",),
            ),
            {
                "event": "user_detached",
                "credit_document_id": override.credit_document_id,
                "at": override.created_at or now_utc_iso(),
            },
        )

    allocations: list[CreditAllocation] = []
    linked: list[dict[str, Any]] = []
    total_applied = Decimal("0.00")

    for alloc in override.allocations:
        inv = invoice_by_id.get(alloc.invoice_document_id)
        if inv is None and alloc.invoice_number:
            inv = invoice_by_id.get(f"inv:{alloc.invoice_number}")
        if inv is None:
            continue
        applied = alloc.amount_applied.quantize(_MONEY_TOL)
        if applied <= _MONEY_TOL:
            continue
        allocations.append(
            CreditAllocation(
                invoice_id=_invoice_id(inv),
                invoice_number=str(inv.get("invoice_number") or alloc.invoice_number),
                amount_applied=applied,
            )
        )
        linked.append(inv)
        total_applied += applied

    if total_applied > credit_amt + _MONEY_TOL:
        return None, {
            "event": "override_skipped",
            "reason": "allocation_exceeds_credit",
            "credit_document_id": override.credit_document_id,
            "at": now_utc_iso(),
        }

    remaining = (credit_amt - total_applied).quantize(_MONEY_TOL)
    warnings: list[str] = ["user_override"]
    if remaining > _MONEY_TOL:
        warnings.append("remaining_credit_unallocated")

    event_type = "user_reassigned" if override.action == "reassign" else "user_allocation_adjusted"
    return (
        CreditMatchResult(
            credit_invoice=credit,
            linked_invoices=tuple(linked),
            allocation=tuple(allocations),
            remaining_credit=remaining,
            match_method="user_override",
            confidence=100,
            warnings=tuple(warnings),
        ),
        {
            "event": event_type,
            "credit_document_id": override.credit_document_id,
            "invoices": [str(a.invoice_number) for a in allocations],
            "at": override.created_at or now_utc_iso(),
        },
    )


def apply_credit_overrides(
    match_results: list[CreditMatchResult],
    overrides: OverrideSession | None,
    *,
    batch_invoices: list[dict[str, Any]] | None = None,
) -> tuple[list[CreditMatchResult], list[dict[str, Any]]]:
    """Transform auto match results according to persisted user overrides."""
    override_by_credit = _override_map(overrides)
    if not override_by_credit:
        events = [
            {
                "event": "auto_matched",
                "credit_document_id": _credit_doc_id(r.credit_invoice),
                "invoices": [str(i.get("invoice_number") or "") for i in r.linked_invoices],
                "method": r.match_method,
            }
            for r in match_results
        ]
        return match_results, events

    invoice_pool: list[dict[str, Any]] = list(
        batch_invoices
        if batch_invoices is not None
        else [r.credit_invoice for r in match_results]
        + [inv for r in match_results for inv in r.linked_invoices]
    )
    invoice_by_id = _invoice_lookup(invoice_pool)

    out: list[CreditMatchResult] = []
    events: list[dict[str, Any]] = []

    for result in match_results:
        credit = result.credit_invoice
        cid = _credit_doc_id(credit)
        override = override_by_credit.get(cid)
        if override is None:
            out.append(result)
            events.append(
                {
                    "event": "auto_matched",
                    "credit_document_id": cid,
                    "invoices": [str(i.get("invoice_number") or "") for i in result.linked_invoices],
                    "method": result.match_method,
                }
            )
            continue

        if cid not in invoice_by_id and credit not in invoice_pool:
            invoice_by_id[cid] = credit

        modified, event = _build_from_override(credit, override, invoice_by_id)
        if modified is None:
            out.append(result)
            if event:
                events.append(event)
            continue
        out.append(modified)
        if event:
            events.append(event)

    events.append({"event": "settlement_recomputed", "at": now_utc_iso()})
    return out, events


def make_detach_override(credit_document_id: str, *, reason: str = "user_detach") -> CreditOverride:
    return CreditOverride(
        credit_document_id=credit_document_id,
        action="detach",
        target_invoice_ids=(),
        allocations=(),
        created_at=now_utc_iso(),
        reason=reason,
    )


def make_reassign_override(
    credit_document_id: str,
    allocations: tuple[CreditOverrideAllocation, ...],
    *,
    reason: str = "user_reassign",
) -> CreditOverride:
    return CreditOverride(
        credit_document_id=credit_document_id,
        action="reassign",
        target_invoice_ids=tuple(a.invoice_document_id for a in allocations),
        allocations=allocations,
        created_at=now_utc_iso(),
        reason=reason,
    )


def make_allocation_adjust_override(
    credit_document_id: str,
    allocations: tuple[CreditOverrideAllocation, ...],
    *,
    reason: str = "user_allocation_adjust",
) -> CreditOverride:
    return CreditOverride(
        credit_document_id=credit_document_id,
        action="allocation_adjust",
        target_invoice_ids=tuple(a.invoice_document_id for a in allocations),
        allocations=allocations,
        created_at=now_utc_iso(),
        reason=reason,
    )
