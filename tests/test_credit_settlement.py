"""Unit tests for credit settlement groups."""

from __future__ import annotations

from decimal import Decimal

from logic.credit_matching import (
    CreditAllocation,
    CreditMatchResult,
    match_credit_to_invoices,
    match_credits_in_batch,
)
from logic.credit_settlement import (
    SETTLEMENT_MANUAL_REVIEW,
    SETTLEMENT_OK,
    compute_settlement_groups,
)


def _norm_invoice(**kwargs) -> dict:
    raw = {
        "type": "invoice",
        "supplier_name": "Test Supplier B.V.",
        "match_status": "matched",
        "amount": 200.0,
        "invoice_number": "INV-1",
        "source_file": "inv1.pdf",
    }
    raw.update(kwargs)
    return {"raw": raw, "amount_dec": Decimal(str(raw["amount"])), "discount_dec": Decimal("0.00")}


def _norm_credit(**kwargs) -> dict:
    raw = {
        "type": "credit_note",
        "supplier_name": "Test Supplier B.V.",
        "match_status": "matched",
        "amount": 50.0,
        "invoice_number": "CR-1",
        "source_file": "cr1.pdf",
    }
    raw.update(kwargs)
    return {"raw": raw, "amount_dec": Decimal(str(raw["amount"])), "discount_dec": Decimal("0.00")}


def _settle(invoices: list[dict], credits: list[dict], match_results: list[CreditMatchResult]):
    return compute_settlement_groups(
        match_results,
        invoices,
        credits,
        supplier_name="Test Supplier B.V.",
    )


class TestSingleInvoiceSingleCredit:
    def test_ok_status_and_amounts(self):
        inv = _norm_invoice(amount=200.0)
        cred = _norm_credit(amount=50.0)
        match = match_credit_to_invoices(cred["raw"], [inv["raw"]])
        result = _settle([inv], [cred], [match])
        assert len(result.groups) == 1
        group = result.groups[0]
        assert group.status == SETTLEMENT_OK
        assert group.invoices_total == Decimal("200.00")
        assert group.credits_total == Decimal("50.00")
        assert group.final_amount_due == Decimal("150.00")
        assert group.refund_amount is None


class TestMultipleInvoicesSingleCredit:
    def test_subset_credit_spans_two_invoices(self):
        inv_a = _norm_invoice(amount=100.0, invoice_number="INV-A", source_file="a.pdf")
        inv_b = _norm_invoice(amount=50.0, invoice_number="INV-B", source_file="b.pdf")
        cred = _norm_credit(amount=150.0, invoice_number="CR-3")
        match = match_credit_to_invoices(cred["raw"], [inv_a["raw"], inv_b["raw"]])
        result = _settle([inv_a, inv_b], [cred], [match])
        group = result.groups[0]
        assert group.status == SETTLEMENT_OK
        assert group.invoices_total == Decimal("150.00")
        assert group.credits_total == Decimal("150.00")
        assert group.final_amount_due == Decimal("0.00")
        assert len(group.invoices) == 2


class TestMultipleInvoicesMultipleCredits:
    def test_two_credits_one_invoice(self):
        inv = _norm_invoice(amount=200.0)
        cred_a = _norm_credit(amount=30.0, invoice_number="CR-A", source_file="cra.pdf")
        cred_b = _norm_credit(amount=40.0, invoice_number="CR-B", source_file="crb.pdf")
        matches = match_credits_in_batch([inv["raw"], cred_a["raw"], cred_b["raw"]])
        result = _settle([inv], [cred_a, cred_b], matches)
        assert len(result.groups) == 1
        group = result.groups[0]
        assert group.status == SETTLEMENT_OK
        assert group.credits_total == Decimal("70.00")
        assert group.final_amount_due == Decimal("130.00")


class TestRefundRequired:
    def test_credit_exceeds_invoices_not_silent_zero(self):
        inv = _norm_invoice(amount=100.0)
        cred = _norm_credit(amount=200.0, invoice_number="CR-X")
        match = match_credit_to_invoices(cred["raw"], [inv["raw"]])
        result = _settle([inv], [cred], [match])
        assert len(result.groups) == 2
        credit_group = next(g for g in result.groups if g.credits)
        invoice_group = next(g for g in result.groups if g.invoices)
        assert credit_group.status == SETTLEMENT_MANUAL_REVIEW
        assert invoice_group.status == SETTLEMENT_OK
        assert credit_group.credit_allocation[0].status == "unallocated_full"
        assert credit_group.credit_allocation[0].remaining_balance == Decimal("200.00")
        assert invoice_group.final_amount_due == Decimal("100.00")


class TestManualReview:
    def test_unmatched_credit_singleton(self):
        inv = _norm_invoice(amount=100.0)
        cred = _norm_credit(
            amount=500.0,
            invoice_number="CR-U",
            referenced_invoice_numbers=["INV-MISSING"],
        )
        match = match_credit_to_invoices(cred["raw"], [inv["raw"]])
        result = _settle([inv], [cred], [match])
        assert len(result.groups) == 2
        statuses = {g.status for g in result.groups}
        assert SETTLEMENT_MANUAL_REVIEW in statuses
        assert SETTLEMENT_OK in statuses


class TestTraceability:
    def test_group_totals_traceable(self):
        inv = _norm_invoice(amount=200.0)
        cred = _norm_credit(amount=50.0)
        match = match_credit_to_invoices(cred["raw"], [inv["raw"]])
        group = _settle([inv], [cred], [match]).groups[0]
        assert len(group.invoices) == 1
        assert len(group.credits) == 1
        assert group.credits[0].gross_amount == Decimal("50.00")
        assert group.invoices[0].gross_amount == Decimal("200.00")
        assert group.credit_allocation[0].status == "matched"
        assert group.credit_allocation[0].amount_applied == Decimal("50.00")


class TestDeterministicGroupId:
    def test_same_inputs_same_group_id(self):
        inv = _norm_invoice()
        cred = _norm_credit()
        match = match_credit_to_invoices(cred["raw"], [inv["raw"]])
        g1 = _settle([inv], [cred], [match]).groups[0].group_id
        g2 = _settle([inv], [cred], [match]).groups[0].group_id
        assert g1 == g2
        assert len(g1) == 16

    def test_manual_match_result_same_group_id(self):
        inv = _norm_invoice()
        cred = _norm_credit()
        manual = CreditMatchResult(
            credit_invoice=cred["raw"],
            linked_invoices=(inv["raw"],),
            allocation=(
                CreditAllocation(
                    invoice_id="inv1.pdf",
                    invoice_number="INV-1",
                    amount_applied=Decimal("50.00"),
                ),
            ),
            remaining_credit=Decimal("0.00"),
            match_method="reference",
            confidence=95,
            warnings=(),
        )
        g1 = _settle([inv], [cred], [manual]).groups[0].group_id
        g2 = _settle([inv], [cred], [manual]).groups[0].group_id
        assert g1 == g2
