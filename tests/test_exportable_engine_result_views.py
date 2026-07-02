"""Contract tests for exportable_engine_result_views dual-pipeline projection."""

from __future__ import annotations

from decimal import Decimal

from logic.payment_engine import calculate_payments
from ui.settlement_table import exportable_engine_result_views


def _invoice(i: int, **overrides):
    inv = {
        "supplier_name": f"Supplier {i % 6}",
        "match_status": "confirmed",
        "type": "invoice",
        "invoice_number": f"INV{i:04d}",
        "source_file": f"/tmp/inv{i}.pdf",
        "amount": 100.0,
        "iban": "NL20INGB0001234567",
        "invoice_date": "2026-01-15",
        "invoice_date_source": "parsed",
        "supplier_term_trusted": True,
        "supplier_payment_term_days_raw": 30,
    }
    inv.update(overrides)
    return inv


def _base_invoice(**overrides):
    inv = {
        "supplier_name": "Test BV",
        "match_status": "confirmed",
        "amount": 200.0,
        "iban": "NL20INGB0001234567",
        "type": "invoice",
        "invoice_number": "INV-1",
        "invoice_date": "2025-06-01",
        "invoice_date_source": "parsed",
        "supplier_term_trusted": True,
        "supplier_payment_term_days_raw": 0,
    }
    inv.update(overrides)
    return inv


def test_legacy_exportable_views_returns_nineteen_for_nineteen_invoice_batch():
    invs = [_invoice(i) for i in range(19)]
    result = calculate_payments(invs)
    payments, errors = exportable_engine_result_views(result)

    assert result.pipeline == "legacy"
    assert len(payments) == 19
    assert len(errors) == 0
    assert all(p.get("settlement_group_id") is None for p in payments)


def test_legacy_exportable_views_single_invoice_amount():
    inv = _base_invoice(
        amount=121.0,
        amount_excl_vat=100.0,
        discount=10,
        invoice_number="INV-1",
    )
    result = calculate_payments([inv])
    payments, _errors = exportable_engine_result_views(result)

    assert len(payments) == 1
    assert payments[0]["amount"] == Decimal("111.00")


def test_settlement_exportable_views_wasco_returns_one_group():
    inv_a = _base_invoice(supplier_name="Wasco", invoice_number="5660148", amount=65.51, source_file="5660148.pdf")
    inv_b = _base_invoice(supplier_name="Wasco", invoice_number="6305463", amount=41.16, source_file="6305463.pdf")
    credit = _base_invoice(
        supplier_name="Wasco",
        invoice_number="6230076",
        amount=66.67,
        type="credit_note",
        source_file="6230076.pdf",
    )
    result = calculate_payments([inv_a, inv_b, credit])
    payments, _errors = exportable_engine_result_views(result)

    assert result.pipeline == "settlement"
    assert len(payments) == 1
    assert payments[0]["amount"] == Decimal("40.00")
    assert payments[0]["settlement_group_id"]


def test_settlement_exportable_views_vte_returns_exportable_group_count():
    inv_a = _base_invoice(supplier_name="VTE", invoice_number="VF2600048", amount=245.15, source_file="VF2600048.pdf")
    inv_b = _base_invoice(supplier_name="VTE", invoice_number="VF2601788", amount=135.35, source_file="VF2601788.pdf")
    credit_small = _base_invoice(
        supplier_name="VTE",
        invoice_number="VCR2600003",
        amount=33.0,
        type="credit_note",
        source_file="VCR2600003.pdf",
        referenced_invoice_numbers=["VF2600115"],
    )
    credit_large = _base_invoice(
        supplier_name="VTE",
        invoice_number="VCR2600064",
        amount=408.57,
        type="credit_note",
        source_file="VCR2600064.pdf",
        referenced_invoice_numbers=["VF2601543"],
    )
    result = calculate_payments([inv_a, inv_b, credit_small, credit_large])
    payments, _errors = exportable_engine_result_views(result)

    exportable_group_count = sum(1 for g in result.settlement_groups if g.get("exportable"))
    assert len(result.settlement_groups) == 3
    assert len(payments) == exportable_group_count
    assert len(payments) == 1
    assert payments[0]["settlement_group_id"]
