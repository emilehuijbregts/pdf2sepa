"""Unit tests for credit-to-invoice matching."""

from __future__ import annotations

from decimal import Decimal

from logic.credit_matching import match_credit_to_invoices, match_credits_in_batch


def _invoice(**kwargs) -> dict:
    base = {
        "type": "invoice",
        "supplier_name": "Test Supplier B.V.",
        "match_status": "matched",
        "amount": 200.0,
        "invoice_number": "INV-1",
        "source_file": "inv1.pdf",
    }
    base.update(kwargs)
    return base


def _credit(**kwargs) -> dict:
    base = {
        "type": "credit_note",
        "supplier_name": "Test Supplier B.V.",
        "match_status": "matched",
        "amount": 50.0,
        "invoice_number": "CR-1",
        "source_file": "cr1.pdf",
    }
    base.update(kwargs)
    return base


class TestCreditMatchingReference:
    def test_roba_style_reference_match(self):
        credit = _credit(
            invoice_number="CN00009082",
            amount=14.08,
            referenced_invoice_numbers=["INV-0396393"],
        )
        inv = _invoice(invoice_number="INV-0396393", amount=100.0)
        result = match_credit_to_invoices(credit, [inv])
        assert result.match_method == "reference"
        assert len(result.linked_invoices) == 1
        assert result.remaining_credit == Decimal("0.00")


class TestCreditMatchingAmount:
    def test_exact_amount_match(self):
        credit = _credit(amount=66.67, invoice_number="CR-W")
        inv = _invoice(invoice_number="6230076", amount=66.67)
        result = match_credit_to_invoices(credit, [inv])
        assert result.match_method == "amount_exact"

    def test_amount_fit_partial_credit(self):
        credit = _credit(amount=50.0, invoice_number="CR-1")
        inv = _invoice(amount=200.0, invoice_number="INV-2")
        result = match_credit_to_invoices(credit, [inv])
        assert result.match_method == "amount_fit"
        assert result.allocation[0].amount_applied == Decimal("50.00")

    def test_subset_sum_two_invoices(self):
        credit = _credit(amount=150.0, invoice_number="CR-3")
        inv_a = _invoice(amount=100.0, invoice_number="INV-A", source_file="a.pdf")
        inv_b = _invoice(amount=50.0, invoice_number="INV-B", source_file="b.pdf")
        result = match_credit_to_invoices(credit, [inv_a, inv_b])
        assert result.match_method == "amount_subset"
        assert len(result.linked_invoices) == 2

    def test_wasco_style_amount_span(self):
        credit = _credit(amount=66.67, invoice_number="6230076")
        inv_a = _invoice(amount=65.51, invoice_number="5660148", source_file="a.pdf")
        inv_b = _invoice(amount=41.16, invoice_number="6305463", source_file="b.pdf")
        result = match_credit_to_invoices(credit, [inv_a, inv_b])
        assert result.match_method == "amount_span"
        assert len(result.linked_invoices) == 2
        assert result.remaining_credit == Decimal("0.00")

    def test_single_invoice_exact_beats_span(self):
        credit = _credit(amount=70.0, invoice_number="CR-EX")
        exact = _invoice(amount=70.0, invoice_number="INV-EX", source_file="exact.pdf")
        inv_a = _invoice(amount=40.0, invoice_number="INV-A", source_file="a.pdf")
        inv_b = _invoice(amount=30.0, invoice_number="INV-B", source_file="b.pdf")
        result = match_credit_to_invoices(credit, [exact, inv_a, inv_b])
        assert result.match_method == "amount_exact"
        assert result.linked_invoices[0]["invoice_number"] == "INV-EX"


class TestCreditMatchingEdgeCases:
    def test_two_credits_one_invoice_batch(self):
        credit_a = _credit(amount=30.0, invoice_number="CR-A", source_file="cra.pdf")
        credit_b = _credit(amount=40.0, invoice_number="CR-B", source_file="crb.pdf")
        inv = _invoice(amount=200.0, invoice_number="INV-1")
        results = match_credits_in_batch([inv, credit_a, credit_b])
        assert len(results) == 2
        assert all(r.match_method == "amount_fit" for r in results)

    def test_credit_exceeds_all_invoices(self):
        credit = _credit(amount=200.0, invoice_number="CR-X")
        inv = _invoice(amount=100.0, invoice_number="INV-3")
        result = match_credit_to_invoices(credit, [inv])
        assert result.match_method == "manual_review"
        assert "credit_exceeds_available_invoices" in result.warnings

    def test_wrong_supplier_no_match(self):
        credit = _credit(supplier_name="Supplier A")
        inv = _invoice(supplier_name="Supplier B")
        result = match_credit_to_invoices(credit, [inv])
        assert result.match_method == "manual_review"
        assert "no_same_supplier_invoices" in result.warnings

    def test_multiple_reference_matches_allocates_credit(self):
        credit = _credit(
            amount=50.0,
            referenced_invoice_numbers=["INV-1", "INV-2"],
        )
        inv1 = _invoice(invoice_number="INV-1", amount=100.0, source_file="i1.pdf")
        inv2 = _invoice(invoice_number="INV-2", amount=80.0, source_file="i2.pdf")
        result = match_credit_to_invoices(credit, [inv1, inv2])
        assert result.match_method == "reference"
        assert len(result.linked_invoices) == 1
        assert result.remaining_credit == Decimal("0.00")

    def test_missing_referenced_invoice_manual_review(self):
        credit = _credit(
            amount=50.0,
            referenced_invoice_numbers=["INV-MISSING"],
        )
        inv = _invoice(invoice_number="INV-1", amount=100.0)
        result = match_credit_to_invoices(credit, [inv])
        assert result.match_method == "amount_fit"
        assert "referenced_invoices_not_in_batch" in result.warnings
