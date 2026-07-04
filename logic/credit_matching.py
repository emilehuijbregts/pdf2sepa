"""Credit-to-invoice matching (supplier-scoped, deterministic)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from itertools import combinations
from typing import Any

from logic.credit_references import extract_referenced_invoice_numbers
from logic.payment_amounts import amount_to_decimal

_MONEY_TOL = Decimal("0.01")
_MAX_SUBSET_SIZE = 5
_REFERENCE_MISS_FALLBACK_THRESHOLD = 70


@dataclass(frozen=True)
class CreditAllocation:
    invoice_id: str
    invoice_number: str
    amount_applied: Decimal


@dataclass(frozen=True)
class CreditMatchResult:
    credit_invoice: dict[str, Any]
    linked_invoices: tuple[dict[str, Any], ...]
    allocation: tuple[CreditAllocation, ...]
    remaining_credit: Decimal
    match_method: str
    confidence: int
    warnings: tuple[str, ...]


def _doc_type(d: dict[str, Any]) -> str:
    t = str(d.get("type") or "invoice").strip().lower()
    return "credit_note" if t == "credit_note" else "invoice"


def _supplier_key(inv: dict[str, Any]) -> str:
    return str(inv.get("supplier_name") or "").strip().lower()


def _invoice_id(inv: dict[str, Any]) -> str:
    src = str(inv.get("source_file") or "").strip()
    if src:
        return src
    inv_no = str(inv.get("invoice_number") or "").strip()
    if inv_no:
        return f"inv:{inv_no}"
    return f"anon:{id(inv)}"


def _parse_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _invoice_amount(inv: dict[str, Any]) -> Decimal | None:
    raw = inv.get("amount")
    if raw is None:
        return None
    try:
        dec = amount_to_decimal(raw)
    except ValueError:
        return None
    return dec.copy_abs()


def _credit_amount(credit: dict[str, Any]) -> Decimal | None:
    return _invoice_amount(credit)


def _effective_invoice_amount(
    inv: dict[str, Any],
    capacity_by_id: dict[str, Decimal] | None,
) -> Decimal | None:
    gross = _invoice_amount(inv)
    if gross is None:
        return None
    if not capacity_by_id:
        return gross
    cap = capacity_by_id.get(_invoice_id(inv))
    if cap is None:
        return gross
    return _quantize_money(min(gross, cap))


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_TOL)


def _referenced_numbers(credit: dict[str, Any]) -> list[str]:
    refs = credit.get("referenced_invoice_numbers")
    if isinstance(refs, list) and refs:
        return [str(r).strip() for r in refs if str(r).strip()]
    text = str(credit.get("raw_text") or "")
    return extract_referenced_invoice_numbers(text)


def _refs_upper(refs: list[str]) -> set[str]:
    return {r.upper() for r in refs if r}


def _amounts_close(a: Decimal, b: Decimal) -> bool:
    return (a - b).copy_abs() <= _MONEY_TOL


def _sort_invoices_for_tiebreak(invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    credit_date = None

    def key(inv: dict[str, Any]) -> tuple[Any, ...]:
        inv_date = _parse_date(inv.get("invoice_date"))
        date_dist = 99999
        if credit_date and inv_date:
            date_dist = abs((inv_date - credit_date).days)
        amt = _invoice_amount(inv) or Decimal("0")
        return (date_dist, amt, str(inv.get("invoice_number") or ""))

    return sorted(invoices, key=key)


def _build_allocation(
    credit: dict[str, Any],
    invoices: list[dict[str, Any]],
    *,
    method: str,
    confidence: int,
    warnings: list[str],
    capacity_by_id: dict[str, Decimal] | None = None,
) -> CreditMatchResult:
    credit_amt = _credit_amount(credit)
    if credit_amt is None:
        return CreditMatchResult(
            credit_invoice=credit,
            linked_invoices=(),
            allocation=(),
            remaining_credit=Decimal("0.00"),
            match_method="manual_review",
            confidence=0,
            warnings=tuple(warnings + ["credit_amount_missing"]),
        )

    allocations: list[CreditAllocation] = []
    remaining = credit_amt
    linked: list[dict[str, Any]] = []

    for inv in invoices:
        if remaining <= _MONEY_TOL:
            break
        inv_amt = _effective_invoice_amount(inv, capacity_by_id)
        if inv_amt is None or inv_amt <= _MONEY_TOL:
            continue
        applied = min(remaining, inv_amt)
        if applied <= _MONEY_TOL:
            continue
        allocations.append(
            CreditAllocation(
                invoice_id=_invoice_id(inv),
                invoice_number=str(inv.get("invoice_number") or ""),
                amount_applied=applied.quantize(_MONEY_TOL),
            )
        )
        linked.append(inv)
        remaining = (remaining - applied).quantize(_MONEY_TOL)

    if remaining > _MONEY_TOL:
        warnings.append("remaining_credit_unallocated")

    return CreditMatchResult(
        credit_invoice=credit,
        linked_invoices=tuple(linked),
        allocation=tuple(allocations),
        remaining_credit=remaining,
        match_method=method,
        confidence=confidence,
        warnings=tuple(warnings),
    )


def _find_subset_sum(
    credit_amt: Decimal,
    invoices: list[dict[str, Any]],
    *,
    capacity_by_id: dict[str, Decimal] | None = None,
) -> list[dict[str, Any]] | None:
    ordered = _sort_invoices_for_tiebreak(invoices)
    if len(ordered) > _MAX_SUBSET_SIZE:
        ordered = ordered[:_MAX_SUBSET_SIZE]
    n = len(ordered)
    for size in range(1, n + 1):
        for combo in combinations(ordered, size):
            total = sum(
                (_effective_invoice_amount(i, capacity_by_id) or Decimal("0") for i in combo),
                start=Decimal("0"),
            )
            if _amounts_close(total, credit_amt):
                return list(combo)
    return None


def _minimal_span_invoices(
    credit_amt: Decimal,
    invoices: list[dict[str, Any]],
    *,
    capacity_by_id: dict[str, Decimal] | None = None,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    running = Decimal("0.00")
    for inv in _sort_invoices_for_tiebreak(invoices):
        inv_amt = _effective_invoice_amount(inv, capacity_by_id)
        if inv_amt is None or inv_amt <= _MONEY_TOL:
            continue
        selected.append(inv)
        running += inv_amt
        if running + _MONEY_TOL >= credit_amt:
            break
    return selected


def _manual_review_result(
    credit: dict[str, Any],
    *,
    credit_amt: Decimal,
    warnings: list[str],
) -> CreditMatchResult:
    return CreditMatchResult(
        credit_invoice=credit,
        linked_invoices=(),
        allocation=(),
        remaining_credit=credit_amt,
        match_method="manual_review",
        confidence=0,
        warnings=tuple(warnings),
    )


def _reference_miss_allows(result: CreditMatchResult, *, had_reference_miss: bool) -> bool:
    if not had_reference_miss:
        return True
    return result.confidence >= _REFERENCE_MISS_FALLBACK_THRESHOLD


def match_credit_to_invoices(
    credit: dict[str, Any],
    invoices: list[dict[str, Any]],
    *,
    capacity_by_id: dict[str, Decimal] | None = None,
) -> CreditMatchResult:
    """Match one credit note to zero or more invoices from the same supplier."""
    warnings: list[str] = []
    credit_sup = _supplier_key(credit)

    def _amt(inv: dict[str, Any]) -> Decimal | None:
        return _effective_invoice_amount(inv, capacity_by_id)

    candidates = [
        inv
        for inv in invoices
        if _doc_type(inv) != "credit_note"
        and _supplier_key(inv) == credit_sup
        and (_amt(inv) or Decimal("0")) > _MONEY_TOL
    ]
    if not candidates:
        return CreditMatchResult(
            credit_invoice=credit,
            linked_invoices=(),
            allocation=(),
            remaining_credit=_credit_amount(credit) or Decimal("0.00"),
            match_method="manual_review",
            confidence=0,
            warnings=("no_same_supplier_invoices",),
        )

    credit_amt = _credit_amount(credit)
    if credit_amt is None:
        return CreditMatchResult(
            credit_invoice=credit,
            linked_invoices=(),
            allocation=(),
            remaining_credit=Decimal("0.00"),
            match_method="manual_review",
            confidence=0,
            warnings=("credit_amount_missing",),
        )

    total_available = sum((_amt(inv) or Decimal("0") for inv in candidates), start=Decimal("0"))
    if credit_amt > total_available + _MONEY_TOL:
        warnings.append("credit_exceeds_available_invoices")
        return _manual_review_result(credit, credit_amt=credit_amt, warnings=warnings)

    refs = _referenced_numbers(credit)
    ref_set = _refs_upper(refs)
    had_reference_miss = False
    if ref_set:
        ref_matched = [
            inv
            for inv in candidates
            if str(inv.get("invoice_number") or "").strip().upper() in ref_set
        ]
        if len(ref_matched) == 1:
            return _build_allocation(
                credit,
                ref_matched,
                method="reference",
                confidence=95,
                warnings=warnings,
                capacity_by_id=capacity_by_id,
            )
        if len(ref_matched) > 1:
            return _build_allocation(
                credit,
                ref_matched,
                method="reference",
                confidence=85,
                warnings=warnings,
                capacity_by_id=capacity_by_id,
            )
        if refs:
            warnings.append("referenced_invoices_not_in_batch")
            had_reference_miss = True

    exact = [
        inv
        for inv in candidates
        if _amt(inv) is not None and _amounts_close(_amt(inv), credit_amt)
    ]
    if len(exact) == 1:
        result = _build_allocation(
            credit, exact, method="amount_exact", confidence=90, warnings=warnings, capacity_by_id=capacity_by_id
        )
        if _reference_miss_allows(result, had_reference_miss=had_reference_miss):
            return result
        return _manual_review_result(credit, credit_amt=credit_amt, warnings=warnings)
    if len(exact) > 1:
        pick = _sort_invoices_for_tiebreak(exact)[0]
        warnings.append("multiple_exact_amount_matches")
        result = _build_allocation(
            credit,
            [pick],
            method="amount_exact",
            confidence=75,
            warnings=warnings,
            capacity_by_id=capacity_by_id,
        )
        if _reference_miss_allows(result, had_reference_miss=had_reference_miss):
            return result
        return _manual_review_result(credit, credit_amt=credit_amt, warnings=warnings)

    subset = _find_subset_sum(credit_amt, candidates, capacity_by_id=capacity_by_id)
    if subset:
        result = _build_allocation(
            credit,
            subset,
            method="amount_subset",
            confidence=80,
            warnings=warnings,
            capacity_by_id=capacity_by_id,
        )
        if _reference_miss_allows(result, had_reference_miss=had_reference_miss):
            return result
        return _manual_review_result(credit, credit_amt=credit_amt, warnings=warnings)

    fit_candidates = [
        inv
        for inv in candidates
        if _amt(inv) is not None and _amt(inv) >= credit_amt
    ]
    if fit_candidates:
        pick = min(
            fit_candidates,
            key=lambda inv: (
                _amt(inv) or Decimal("0"),
                str(inv.get("invoice_number") or ""),
            ),
        )
        result = _build_allocation(
            credit,
            [pick],
            method="amount_fit",
            confidence=70,
            warnings=warnings,
            capacity_by_id=capacity_by_id,
        )
        if _reference_miss_allows(result, had_reference_miss=had_reference_miss):
            return result
        return _manual_review_result(credit, credit_amt=credit_amt, warnings=warnings)

    if credit_amt <= total_available + _MONEY_TOL:
        span = _minimal_span_invoices(credit_amt, candidates, capacity_by_id=capacity_by_id)
        if len(span) >= 2:
            result = _build_allocation(
                credit,
                span,
                method="amount_span",
                confidence=75,
                warnings=warnings,
                capacity_by_id=capacity_by_id,
            )
            if result.remaining_credit <= _MONEY_TOL and _reference_miss_allows(
                result,
                had_reference_miss=had_reference_miss,
            ):
                return result

    warnings.append("no_confident_match")
    return _manual_review_result(credit, credit_amt=credit_amt, warnings=warnings)


def match_credits_in_batch(invoices: list[dict[str, Any]]) -> list[CreditMatchResult]:
    """Match all credit notes in a batch (may span suppliers).

    Credits are processed largest-first against remaining invoice capacity so a
    high credit consumes available invoices before smaller credits are matched.
    """
    credits = [inv for inv in invoices if _doc_type(inv) == "credit_note"]
    invoices_only = [inv for inv in invoices if _doc_type(inv) != "credit_note"]
    capacity: dict[str, Decimal] = {}
    for inv in invoices_only:
        amt = _invoice_amount(inv)
        if amt is not None:
            capacity[_invoice_id(inv)] = _quantize_money(amt)

    by_credit_id: dict[str, CreditMatchResult] = {}
    for credit in sorted(
        credits,
        key=lambda c: (
            -(_credit_amount(c) or Decimal("0")),
            str(c.get("invoice_number") or ""),
            str(c.get("source_file") or ""),
        ),
    ):
        result = match_credit_to_invoices(credit, invoices_only, capacity_by_id=capacity)
        if capacity and result.remaining_credit > _MONEY_TOL and result.allocation:
            result = _manual_review_result(
                credit,
                credit_amt=_credit_amount(credit) or Decimal("0.00"),
                warnings=[*result.warnings, "remaining_credit_unallocated"],
            )
        else:
            for alloc in result.allocation:
                prev = capacity.get(alloc.invoice_id)
                if prev is not None:
                    capacity[alloc.invoice_id] = _quantize_money(prev - alloc.amount_applied)
        by_credit_id[_invoice_id(credit)] = result

    return [
        by_credit_id[_invoice_id(c)]
        for c in sorted(
            credits,
            key=lambda c: (str(c.get("invoice_number") or ""), str(c.get("source_file") or "")),
        )
    ]


def build_engine_credit_links(
    match_results: list[CreditMatchResult],
    normalized_credits: list[dict[str, Any]],
    normalized_invoices: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    """Map match results to payment-engine ``linked`` dict (id(normalized_invoice) → credits)."""
    linked: dict[int, list[dict[str, Any]]] = {}

    def _find_credit_norm(credit_raw: dict[str, Any]) -> dict[str, Any] | None:
        for cn in normalized_credits:
            raw = cn.get("raw") or cn
            if raw is credit_raw:
                return cn
            if _invoice_id(raw) == _invoice_id(credit_raw):
                return cn
        return None

    def _find_invoice_norm(alloc: CreditAllocation) -> dict[str, Any] | None:
        for norm in normalized_invoices:
            raw = norm.get("raw") or norm
            if _invoice_id(raw) == alloc.invoice_id:
                return norm
            if str(raw.get("invoice_number") or "") == alloc.invoice_number:
                return norm
        return None

    for result in match_results:
        cred_norm = _find_credit_norm(result.credit_invoice)
        if cred_norm is None:
            continue
        for alloc in result.allocation:
            inv_norm = _find_invoice_norm(alloc)
            if inv_norm is None:
                continue
            bucket = linked.setdefault(id(inv_norm), [])
            if cred_norm not in bucket:
                bucket.append(cred_norm)
    return linked


def build_engine_credit_allocations(
    match_results: list[CreditMatchResult],
    normalized_credits: list[dict[str, Any]],
    normalized_invoices: list[dict[str, Any]],
) -> dict[int, list[tuple[dict[str, Any], Decimal]]]:
    """Map invoice norm id → [(credit_norm, amount_applied), ...]."""
    allocations: dict[int, list[tuple[dict[str, Any], Decimal]]] = {}

    def _find_credit_norm(credit_raw: dict[str, Any]) -> dict[str, Any] | None:
        for cn in normalized_credits:
            raw = cn.get("raw") or cn
            if raw is credit_raw:
                return cn
            if _invoice_id(raw) == _invoice_id(credit_raw):
                return cn
        return None

    def _find_invoice_norm(alloc: CreditAllocation) -> dict[str, Any] | None:
        for norm in normalized_invoices:
            raw = norm.get("raw") or norm
            if _invoice_id(raw) == alloc.invoice_id:
                return norm
            if str(raw.get("invoice_number") or "") == alloc.invoice_number:
                return norm
        return None

    for result in match_results:
        cred_norm = _find_credit_norm(result.credit_invoice)
        if cred_norm is None:
            continue
        for alloc in result.allocation:
            inv_norm = _find_invoice_norm(alloc)
            if inv_norm is None:
                continue
            bucket = allocations.setdefault(id(inv_norm), [])
            bucket.append((cred_norm, alloc.amount_applied.quantize(_MONEY_TOL)))
    return allocations


def match_has_blocking_error(result: CreditMatchResult) -> bool:
    """True when credit cannot be applied and should block the supplier group."""
    return (
        result.match_method == "manual_review"
        and not result.linked_invoices
        and "credit_exceeds_available_invoices" in result.warnings
    )


def match_needs_review(result: CreditMatchResult) -> bool:
    return result.match_method == "manual_review" or bool(result.warnings)
