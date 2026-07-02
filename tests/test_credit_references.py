"""Unit tests for parent invoice reference extraction."""

from __future__ import annotations

from logic.credit_references import extract_referenced_invoice_numbers


class TestCreditReferences:
    def test_roba_parent_invoice(self):
        text = "1 Betr.: Onze factuur INV-0396393 2 -5,82 -11,64\n"
        refs = extract_referenced_invoice_numbers(text)
        assert refs == ["INV-0396393"]

    def test_vte_fact_nr(self):
        text = "Creditnota VCR2600003+\nFact.nr. VF2600115+ - Verz.nr.\n"
        refs = extract_referenced_invoice_numbers(text)
        assert "VF2600115" in refs

    def test_deduplicates(self):
        text = (
            "Fact.nr. VF123\n"
            "Betr.: Onze factuur VF123\n"
        )
        refs = extract_referenced_invoice_numbers(text)
        assert refs == ["VF123"]

    def test_skips_own_credit_number(self):
        text = "Creditnota CN00009082\nFact.nr. CN00009082\n"
        refs = extract_referenced_invoice_numbers(text)
        assert "CN00009082" not in refs
