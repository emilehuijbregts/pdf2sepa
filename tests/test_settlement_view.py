"""Tests for settlement view model mapping."""

from __future__ import annotations

from logic.payment_engine import calculate_payments
from ui.settlement_view import build_settlement_group_vms


def _base_invoice(**overrides):
    inv = {
        "supplier_name": "Test BV",
        "match_status": "confirmed",
        "amount": 200.0,
        "iban": "NL20INGB0001234567",
        "type": "invoice",
        "invoice_number": "INV-2",
        "invoice_date": "2025-06-01",
        "invoice_date_source": "parsed",
        "supplier_term_trusted": True,
        "supplier_payment_term_days_raw": 0,
    }
    inv.update(overrides)
    return inv


def test_settlement_vm_from_engine():
    inv = _base_invoice()
    credit = _base_invoice(amount=50.0, type="credit_note", invoice_number="CR-1")
    result = calculate_payments([inv, credit])
    vms = build_settlement_group_vms(result.settlement_groups)
    assert len(vms) == 1
    vm = vms[0]
    assert vm.group_id
    assert vm.exportable is True
    assert "CR-1" in vm.description
