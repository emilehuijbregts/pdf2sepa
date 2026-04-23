"""Tests for logic/payment_engine.py."""

from __future__ import annotations

from decimal import Decimal

import pytest

from logic.payment_engine import calculate_payments, clean_iban, is_plausible_iban


def _base_invoice(**overrides):
    inv = {
        "supplier_name": "Test BV",
        "match_status": "confirmed",
        "amount": 121.0,
        "amount_excl_vat": 100.0,
        "discount": 0,
        "iban": "NL20INGB0001234567",
        "type": "invoice",
        "invoice_number": "INV-001",
        "description": "test",
        "invoice_date": "2025-06-01",
        "invoice_date_source": "parsed",
        "supplier_term_trusted": True,
        "supplier_payment_term_days_raw": 0,
    }
    inv.update(overrides)
    return inv


class TestNormalPayment:
    def test_simple_invoice(self):
        payments, errors = calculate_payments([_base_invoice()])
        assert len(payments) == 1
        assert payments[0]["amount"] == Decimal("121.00")
        assert payments[0]["amount_display"] == "121,00"
        assert payments[0].get("date_mode") == "direct"
        assert payments[0].get("execution_date")
        assert payments[0]["decision"]["status"] == "included"
        assert len(errors) == 0

    def test_tentative_amount_adds_warning(self):
        inv = _base_invoice(
            amount=184.56,
            amount_result={
                "candidates": [
                    {
                        "value": "184.56",
                        "source": "total_label_sum",
                        "confidence": 80,
                        "context": "ctx",
                        "type": "incl",
                    },
                ],
                "value": "184.56",
                "confidence": 80,
                "source": "TOTAL_LABEL_SUM",
                "status": "tentative",
                "selected_amount": "184.56",
                "amount_confidence": 80,
                "amount_status": "tentative",
            },
        )
        payments, errors = calculate_payments([inv])
        assert len(errors) == 0
        assert len(payments) == 1
        assert payments[0]["amount"] == Decimal("184.56")
        assert "amount_tentative" in (payments[0].get("warning") or "")
        assert payments[0]["decision"]["status"] == "needs_review"

    def test_invoice_with_discount(self):
        payments, _ = calculate_payments([_base_invoice(discount=10)])
        assert payments[0]["amount"] == Decimal("111.00")
        assert payments[0]["amount_display"] == "111,00"

    def test_discount_with_supplier_vat_rate_zero(self):
        inv = _base_invoice(amount=100.0, discount=10, supplier_vat_rate=0)
        payments, _ = calculate_payments([inv])
        assert payments[0]["amount"] == Decimal("90.00")
        assert payments[0]["decision_trace"]["vat_source"] == "calculated_0"

    def test_two_invoices_same_supplier(self):
        inv_a = _base_invoice(invoice_number="INV-A", amount=100.0, amount_excl_vat=82.64)
        inv_b = _base_invoice(invoice_number="INV-B", amount=50.0, amount_excl_vat=41.32)
        payments, errors = calculate_payments([inv_a, inv_b])
        assert len(payments) == 2
        assert len(errors) == 0


class TestCreditNotes:
    def test_credit_applied(self):
        inv = _base_invoice(amount=200.0, amount_excl_vat=165.29, invoice_number="INV-2")
        credit = _base_invoice(amount=50.0, type="credit_note", invoice_number="CR-1")
        payments, errors = calculate_payments([inv, credit])
        assert len(payments) == 1
        assert payments[0]["amount"] == Decimal("150.00")
        assert payments[0]["amount_display"] == "150,00"
        assert payments[0]["credit_notes_applied"] == ["CR-1"]

    def test_credit_overflow_blocked(self):
        inv = _base_invoice(amount=100.0, invoice_number="INV-3")
        credit = _base_invoice(amount=200.0, type="credit_note", invoice_number="CR-2")
        payments, errors = calculate_payments([inv, credit])
        assert len(payments) == 0
        reasons = [e["reason"] for e in errors]
        assert "credit_exceeds_available_invoices" in reasons

    def test_credit_only_error(self):
        credit = _base_invoice(amount=50.0, type="credit_note", invoice_number="CR-3")
        payments, errors = calculate_payments([credit])
        assert len(payments) == 0
        assert any(e["reason"] == "credit_note_only" for e in errors)


class TestDiscountWarnings:
    def test_discount_without_parsed_excl_uses_supplier_vat_rate(self):
        """Zonder geparsete excl: korting op exclusief berekend via supplier_vat_rate (default 21%)."""
        inv = _base_invoice(discount=2.0, amount_excl_vat=None)
        payments, _ = calculate_payments([inv])
        assert payments[0]["amount"] == Decimal("119.00")
        assert "no_excl_vat_amount_discount_skipped" not in (payments[0].get("warning") or "")


class TestIbanValidation:
    def test_missing_iban_error(self):
        inv = _base_invoice(iban="")
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] == "missing_iban" for e in errors)

    def test_invalid_iban_error(self):
        inv = _base_invoice(iban="GEEN_IBAN")
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] in ("missing_iban", "invalid_iban") for e in errors)

    def test_foreign_ibans_accepted(self):
        invoices = [
            _base_invoice(supplier_name="DE GmbH", iban="DE89370400440532013000", invoice_number="DE-1"),
            _base_invoice(supplier_name="BE BVBA", iban="BE68539007547034", invoice_number="BE-1"),
            _base_invoice(supplier_name="FR SARL", iban="FR7630006000011234567890189", invoice_number="FR-1"),
        ]
        payments, errors = calculate_payments(invoices)
        assert len(payments) == 3
        assert not any(e["reason"] == "invalid_iban" for e in errors)

    def test_iban_mismatch_warning(self):
        inv = _base_invoice(iban_mismatch=True)
        payments, _ = calculate_payments([inv])
        assert "iban_mismatch_supplier" in (payments[0].get("warning") or "")

    def test_supplier_term_not_applied_when_untrusted(self):
        inv = _base_invoice(
            supplier_term_trusted=False,
            supplier_payment_term_days_raw=30,
        )
        payments, _ = calculate_payments([inv])
        assert "supplier_term_not_applied" in (payments[0].get("warning") or "")
        assert payments[0]["supplier_payment_term_days_effective"] == 0


class TestErrorCases:
    def test_missing_amount(self):
        inv = _base_invoice(amount=None)
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] == "missing_amount" for e in errors)

    def test_amount_failed_blocks(self):
        inv = _base_invoice(
            amount=None,
            amount_result={
                "candidates": [],
                "value": None,
                "confidence": 0,
                "source": "EXCEPTION",
                "status": "failed",
            },
        )
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] == "amount_failed" for e in errors)

    def test_unmatched_supplier(self):
        inv = _base_invoice(match_status="unmatched")
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] == "unmatched_supplier" for e in errors)
        err_inv = errors[0]["invoices"][0]
        assert err_inv["decision"]["status"] == "needs_review"

    def test_no_supplier_hint(self):
        inv = _base_invoice(match_status="no_hint")
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] == "no_supplier_hint" for e in errors)

    def test_needs_review_rejected(self):
        inv = _base_invoice(match_status="needs_review")
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] == "needs_review" for e in errors)

    def test_reviewed_accepted(self):
        inv = _base_invoice(match_status="reviewed")
        payments, errors = calculate_payments([inv])
        assert len(payments) == 1

    def test_matched_still_accepted(self):
        """Backward compatibility: 'matched' still works."""
        inv = _base_invoice(match_status="matched")
        payments, _ = calculate_payments([inv])
        assert len(payments) == 1

    def test_load_failed_no_text(self):
        inv = {
            "supplier_name": "x.pdf",
            "match_status": "load_failed",
            "load_error": "no_text",
        }
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] == "pdf_no_text" for e in errors)

    def test_load_failed_read_failed(self):
        inv = {
            "supplier_name": "y.pdf",
            "match_status": "load_failed",
            "load_error": "read_failed",
        }
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] == "pdf_read_failed" for e in errors)

    def test_load_failed_defaults_to_pdf_read_failed(self):
        inv = {
            "supplier_name": "z.pdf",
            "match_status": "load_failed",
        }
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] == "pdf_read_failed" for e in errors)

    def test_zero_amount_after_discount(self):
        # 121% van exclusief (100) = 121 → restbedrag na korting 0
        inv = _base_invoice(amount=121.0, discount=121.0)
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] == "zero_amount" for e in errors)

    def test_invalid_amount_result_value_blocks(self):
        inv = _base_invoice(
            amount_result={
                "candidates": [],
                "value": "not-a-decimal",
                "confidence": 100,
                "source": "TOTAL_LABEL_PAYABLE",
                "status": "confirmed",
            }
        )
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] == "amount_invalid_format" for e in errors)

    def test_user_selected_ignores_stale_top_level_amount(self):
        """Handmatige/UI-keuze in amount_result wint nooit terug naar legacy invoice.amount."""
        inv = _base_invoice(
            amount=121.0,
            amount_result={
                "status": "confirmed",
                "user_selected": True,
                "value": "200.00",
                "selected_amount": "200.00",
                "confidence": 100,
                "source": "MANUAL",
                "candidates": [{"value": "200.00", "source": "manual", "confidence": 100}],
            },
        )
        payments, errors = calculate_payments([inv])
        assert not errors
        assert len(payments) == 1
        assert payments[0]["amount"] == Decimal("200.00")


class TestCleanIban:
    def test_strips_spaces(self):
        assert clean_iban("NL20 INGB 0001 2345 67") == "NL20INGB0001234567"

    def test_uppercases(self):
        assert clean_iban("nl20ingb0001234567") == "NL20INGB0001234567"

    def test_none(self):
        assert clean_iban(None) == ""


class TestIsPlausibleIban:
    def test_valid_nl(self):
        assert is_plausible_iban("NL20INGB0001234567")

    def test_valid_de(self):
        assert is_plausible_iban("DE89370400440532013000")

    def test_too_short(self):
        assert not is_plausible_iban("NL91INGB")

    def test_garbage(self):
        assert not is_plausible_iban("GEEN_IBAN")

    def test_mod97_valid(self):
        assert is_plausible_iban("NL02ABNA0123456789")

    def test_mod97_invalid(self):
        """Correct format but wrong check digits."""
        assert not is_plausible_iban("NL01ABNA0123456789")


class TestDecisionTrace:
    def test_trace_present_on_normal_payment(self):
        payments, errors = calculate_payments([_base_invoice()])
        assert not errors
        trace = payments[0].get("decision_trace")
        assert isinstance(trace, dict)
        assert trace["amount_decision_reason"] == "amount_selected_invoice_field"
        assert trace["supplier_match_status"] == "confirmed"
        assert trace["credit_applied"]["used"] is False
        assert trace["discount_applied"]["used"] is False
        assert trace["vat_inference_used"] is False
        assert trace.get("vat_source") is None

    def test_trace_credit_discount_and_vat_flags(self):
        inv = _base_invoice(
            amount=200.0,
            amount_excl_vat=165.29,
            discount=10,
            invoice_number="INV-T1",
            amount_result={
                "status": "confirmed",
                "source": "TOTAL_LABEL_PAYABLE",
                "value": "200.00",
                "confidence": 99,
                "candidates": [{"value": "200.00"}],
            },
        )
        credit = _base_invoice(
            amount=50.0,
            amount_excl_vat=41.32,
            type="credit_note",
            invoice_number="CR-T1",
        )
        payments, errors = calculate_payments([inv, credit])
        assert not errors
        payment = payments[0]
        assert payment["amount"] == Decimal("137.60")
        trace = payment["decision_trace"]
        assert trace["amount_decision_reason"] == "amount_selected_single_candidate"
        assert "credit_note_matched" in trace["reason_codes"]
        assert "discount_applied_trusted_supplier" in trace["reason_codes"]
        assert "vat_inferred_from_rate" in trace["reason_codes"]
        assert trace["credit_applied"]["details"]["invoice_numbers"] == ["CR-T1"]
        assert trace["reconciliation_snapshot"]["final_amount_decimal"] == "137.60"
        assert trace.get("vat_source") == "calculated_21"

    def test_decision_contains_causal_backreference(self):
        payments, errors = calculate_payments([_base_invoice()])
        assert not errors
        decision = payments[0]["decision"]
        assert decision["input_field_fingerprint"]
        assert "amount" in decision["causal_inputs"]

    def test_trace_low_confidence_discount_still_applied_via_vat(self):
        inv = _base_invoice(
            amount=121.0,
            amount_excl_vat=None,
            discount=5,
            amount_result={
                "status": "low_confidence",
                "source": "TOTAL_LABEL_DUE",
                "value": "121.00",
                "confidence": 31,
                "candidates": [{"value": "121.00"}, {"value": "120.00"}],
            },
        )
        payments, errors = calculate_payments([inv])
        assert not errors
        payment = payments[0]
        assert payment["amount"] == Decimal("116.00")
        trace = payment["decision_trace"]
        assert trace["discount_applied"]["used"] is True
        assert trace["discount_applied"]["skipped_reason"] is None
        assert trace["vat_source"] == "calculated_21"
        assert "amount_low_confidence" in trace["engine_status_flags"]
        assert trace["amount_source_chain"][:2] == [
            "amount_result.value:TOTAL_LABEL_DUE",
            "amount_result.candidates",
        ]
