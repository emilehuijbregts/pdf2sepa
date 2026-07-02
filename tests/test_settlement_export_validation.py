"""Export validation tests for settlement SSOT."""

from __future__ import annotations

from decimal import Decimal

from logic.payment_engine import calculate_payments, calculate_payments_with_overrides
from logic.credit_override_apply import make_detach_override
from logic.credit_override_store import OverrideSession
from logic.settlement_export import exportable_groups, validate_engine_result_for_export


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
        "source_file": "inv.pdf",
    }
    inv.update(overrides)
    return inv


def test_validate_clean_result():
    inv = _base_invoice()
    credit = _base_invoice(amount=50.0, type="credit_note", invoice_number="CR-1", source_file="cr.pdf")
    result = calculate_payments([inv, credit])
    assert validate_engine_result_for_export(result) == []


def test_refund_not_exportable():
    inv = _base_invoice(amount=50.0)
    credit = _base_invoice(amount=100.0, type="credit_note", invoice_number="CR-1", source_file="cr.pdf")
    result = calculate_payments([inv, credit])
    exportable = exportable_groups(result).groups
    assert len(exportable) == 1
    unresolved = next(g for g in result.settlement_groups if g.get("credit_allocation"))
    assert unresolved["settlement_status"] == "manual_review"
    assert unresolved["exportable"] is False


def test_export_after_override():
    inv_a = _base_invoice(invoice_number="INV-A", amount=100.0, source_file="a.pdf")
    inv_b = _base_invoice(invoice_number="INV-B", amount=100.0, source_file="b.pdf")
    credit = _base_invoice(amount=50.0, type="credit_note", invoice_number="CR-1", source_file="cr.pdf")
    result = calculate_payments_with_overrides(
        [inv_a, inv_b, credit],
        override_session=OverrideSession(
            batch_key="x",
            overrides=(make_detach_override("cr.pdf"),),
            history=(),
        ),
    )
    credit_ids = {"cr.pdf"}
    errs = validate_engine_result_for_export(
        result,
        batch_credit_document_ids=credit_ids,
        override_credit_document_ids={"cr.pdf"},
    )
    assert not any("orphan override" in e for e in errs)


def test_duplicate_document_detection():
    inv = _base_invoice()
    result = calculate_payments([inv])
    g = dict(result.settlement_groups[0])
    dup = dict(result.settlement_groups[0])
    dup["group_id"] = "other"
    dup["exportable"] = True
    bad = type(result)(settlement_groups=[g, dup], review_documents=[])
    errors = validate_engine_result_for_export(bad)
    assert any("multiple settlement groups" in e for e in errors)


def test_amount_zero_blocks_exportable():
    inv = _base_invoice(amount=100.0)
    credit = _base_invoice(amount=100.0, type="credit_note", invoice_number="CR-1", source_file="cr.pdf")
    result = calculate_payments([inv, credit])
    g = dict(result.settlement_groups[0])
    g["exportable"] = True
    g["final_amount_due"] = Decimal("0.00")
    bad = type(result)(settlement_groups=[g], review_documents=[])
    errors = validate_engine_result_for_export(bad)
    assert any("amount <= 0" in e for e in errors)


def test_unresolved_credit_never_exportable():
    inv = _base_invoice(amount=50.0)
    credit = _base_invoice(amount=100.0, type="credit_note", invoice_number="CR-1", source_file="cr.pdf")
    result = calculate_payments([inv, credit])
    unresolved = dict(next(g for g in result.settlement_groups if g.get("credit_allocation")))
    unresolved["exportable"] = True
    bad = type(result)(settlement_groups=[unresolved], review_documents=[])
    errors = validate_engine_result_for_export(bad)
    assert any("unresolved credit allocation" in e for e in errors)


def test_exportable_group_requires_engine_description():
    inv = _base_invoice()
    result = calculate_payments([inv])
    group = dict(result.settlement_groups[0])
    group["exportable"] = True
    group["description"] = ""
    bad = type(result)(settlement_groups=[group], review_documents=[])
    errors = validate_engine_result_for_export(bad)
    assert any("no settlement description" in e for e in errors)
