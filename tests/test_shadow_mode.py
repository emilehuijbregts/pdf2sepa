"""Shadow mode validation tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from logic.payment_engine import calculate_payments
from logic.shadow_mode import run_shadow_validation


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


def test_no_credit_shadow_nineteen_invoice_parity():
    invs = [_invoice(i) for i in range(19)]
    production = calculate_payments(invs)
    report = run_shadow_validation(invs, production, log=False)
    assert report.status == "PASS"
    assert report.batch_type == "no-credit"
    assert report.legacy_rows == 19
    assert report.settlement_rows == 19
    assert report.pipeline_match is True
    assert report.diffs == ()


def test_credit_shadow_determinism_wasco():
    inv_a = _invoice(0, supplier_name="Wasco", invoice_number="5660148", amount=65.51, source_file="5660148.pdf")
    inv_b = _invoice(1, supplier_name="Wasco", invoice_number="6305463", amount=41.16, source_file="6305463.pdf")
    credit = _invoice(
        2,
        supplier_name="Wasco",
        invoice_number="6230076",
        amount=66.67,
        type="credit_note",
        source_file="6230076.pdf",
    )
    invs = [inv_a, inv_b, credit]
    production = calculate_payments(invs)
    report = run_shadow_validation(invs, production, log=False)
    assert report.status == "PASS"
    assert report.batch_type == "credit"
    assert report.extra["determinism"] == "PASS"
    assert report.extra["coverage"] == "PASS"
    assert production.settlement_groups[0]["final_amount_due"] == Decimal("40.00")


def test_credit_shadow_determinism_vte():
    invs = [
        _invoice(0, supplier_name="VTE", invoice_number="VF2600048", amount=245.15, source_file="VF2600048.pdf"),
        _invoice(1, supplier_name="VTE", invoice_number="VF2601788", amount=135.35, source_file="VF2601788.pdf"),
        _invoice(
            2,
            supplier_name="VTE",
            invoice_number="VCR2600003",
            amount=33.0,
            type="credit_note",
            source_file="VCR2600003.pdf",
        ),
        _invoice(
            3,
            supplier_name="VTE",
            invoice_number="VCR2600064",
            amount=408.57,
            type="credit_note",
            source_file="VCR2600064.pdf",
        ),
    ]
    production = calculate_payments(invs)
    report = run_shadow_validation(invs, production, log=False)
    assert report.status == "PASS"
    assert report.settlement_rows == 3


@pytest.mark.parametrize("field_name", ["settlement_group_id", "settlement_status", "settlement"])
def test_no_credit_legacy_has_no_settlement_fields(field_name: str):
    invs = [_invoice(i) for i in range(3)]
    result = calculate_payments(invs)
    for payment in result.legacy_payments or []:
        assert not payment.get(field_name)
