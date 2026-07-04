"""Tests for credit override dialog allocation helpers."""

from __future__ import annotations

from decimal import Decimal

from logic.credit_override_store import CreditOverrideAllocation
from ui.credit_override_dialog import (
    build_allocations_from_inputs,
    suggest_allocations_across_invoices,
    validate_credit_reassign,
)


def _inv(no: str, amount: float, source: str) -> dict:
    return {
        "type": "invoice",
        "invoice_number": no,
        "amount": amount,
        "amount_dec": Decimal(str(amount)),
        "source_file": source,
    }


def _credit(no: str, amount: float, source: str) -> dict:
    return {
        "type": "credit_note",
        "invoice_number": no,
        "amount": amount,
        "amount_dec": Decimal(str(amount)),
        "source_file": source,
    }


def test_build_allocations_multi_invoice():
    credit = _credit("6230076", 66.67, "wasco_cr.pdf")
    inv_a = _inv("5660148", 65.51, "wasco_a.pdf")
    inv_b = _inv("6305463", 41.16, "wasco_b.pdf")
    raw = {
        "wasco_a.pdf": "25.51",
        "wasco_b.pdf": "41,16",
    }
    result = build_allocations_from_inputs(credit, [inv_a, inv_b], raw)
    assert result is not None
    assert len(result) == 2
    assert result[0].invoice_number == "5660148"
    assert result[0].amount_applied == Decimal("25.51")
    assert result[1].amount_applied == Decimal("41.16")


def test_build_allocations_rejects_over_credit_total():
    credit = _credit("CR-1", 50.0, "c.pdf")
    inv = _inv("INV-1", 100.0, "i.pdf")
    result = build_allocations_from_inputs(credit, [inv], {"i.pdf": "60"})
    assert result is None


def test_suggest_allocations_greedy():
    credit = _credit("6230076", 66.67, "wasco_cr.pdf")
    inv_a = _inv("5660148", 65.51, "wasco_a.pdf")
    inv_b = _inv("6305463", 41.16, "wasco_b.pdf")
    suggested = suggest_allocations_across_invoices(credit, [inv_a, inv_b])
    assert suggested["wasco_a.pdf"] == Decimal("65.51")
    assert suggested["wasco_b.pdf"] == Decimal("1.16")


def test_validate_reassign_insufficient_invoice_capacity():
    credit = _credit("VCR2600064", 408.57, "vte_cr.pdf")
    inv_a = _inv("VF2600048", 241.10, "a.pdf")
    inv_b = _inv("VF2601788", 135.35, "b.pdf")
    raw = {"a.pdf": "241,10", "b.pdf": "135,35"}
    allocations, err = validate_credit_reassign(credit, [inv_a, inv_b], raw)
    assert allocations is None
    assert err == "insufficient_invoices"


def test_validate_reassign_requires_full_credit_allocation():
    credit = _credit("6230076", 66.67, "wasco_cr.pdf")
    inv_a = _inv("5660148", 65.51, "wasco_a.pdf")
    inv_b = _inv("6305463", 41.16, "wasco_b.pdf")
    raw = {"wasco_a.pdf": "25,51", "wasco_b.pdf": "30,00"}
    allocations, err = validate_credit_reassign(credit, [inv_a, inv_b], raw)
    assert allocations is None
    assert err == "partial_allocation"
