"""Tests voor factuur-/klantnummer-kandidaten."""

from __future__ import annotations

from parser.field_candidates import (
    extract_customer_number_result,
    extract_invoice_number_result,
)
from parser.pdf_parser import extract_invoice_data, extract_text_strict


class TestCustomerNumberCandidates:
    def test_uw_klant_k_prefix_yields_candidate_without_resolved(self):
        text = "Uw Klant K014135\nTotaal te betalen 11,05"
        r = extract_customer_number_result(text)
        assert r.value == "K014135"
        assert r.status == "confirmed"
        assert any(c.value == "K014135" for c in r.candidates)

    def test_standalone_k_token_in_text(self):
        text = "K014135\nTotaal te betalen 11,05"
        r = extract_customer_number_result(text)
        assert r.value == "K014135"
        assert any(c.value == "K014135" for c in r.candidates)

    def test_spaced_k_token_ocr_style(self):
        text = "Uw referentie\nK 014135\nTotaal EUR 11,05"
        r = extract_customer_number_result(text)
        assert r.value == "K014135"
        assert not any(c.value == "014135" for c in r.candidates)

    def test_line_only_spaced_k_code(self):
        text = "Factuur 999\nK 014135\nTotaal te betalen 11,05"
        r = extract_customer_number_result(text)
        assert r.value == "K014135"

    def test_uw_klant_digits_on_next_line_composed(self):
        text = "Uw klant\n014135\nTotaal EUR 11,05"
        r = extract_customer_number_result(text)
        assert r.value == "K014135"
        assert any(c.value == "K014135" for c in r.candidates)

    def test_split_k_line_then_digits(self):
        text = "Factuur 1\nK\n014135\nTotaal 10,00"
        r = extract_customer_number_result(text)
        assert r.value == "K014135"

    def test_lowercase_k_prefix(self):
        text = "Klantcode K014135\nBedrag 99,00"
        r = extract_customer_number_result(text)
        assert r.value == "K014135"

    def test_option_tape_klantnummer_cell_below_label(self):
        """Layout: label ``Klantnummer``, waarde ``K014135`` in cel eronder."""
        text = (
            "Uw referentie 202603 80 | Verkooporder VO2602744\n"
            "Klantnummer\n"
            "K014135\n"
            "Totaal te betalen EUR 11,05"
        )
        r = extract_customer_number_result(text)
        assert r.value == "K014135"
        assert any(c.value == "K014135" for c in r.candidates)
        assert "202603" not in [c.value for c in r.candidates]

    def test_polyglass_klantcode_next_line(self):
        text = (
            "Leverancier\n"
            "Klantcode\n"
            "04816069\n"
            "Factuur 26FC000498\n"
            "Totaal te betalen EUR 1287,29"
        )
        r = extract_customer_number_result(text)
        assert r.value == "04816069"
        assert any(c.value == "04816069" for c in r.candidates)

    def test_delivery_block_ignored_when_klantcode_labeled(self):
        """Polyglass: legacy afleveradres-6-cijfer mag label ``Klantcode`` niet overrulen."""
        text = (
            "Leverancier\n"
            "Klantcode\n"
            "04816069\n"
            "Afleveradres\n"
            "Firma bv\n"
            "000119\n"
            "Factuur 26FC000498\n"
        )
        r = extract_customer_number_result(
            text,
            resolved="000119",
            resolved_source="delivery_block_six_digit",
        )
        assert r.value == "04816069"
        assert r.source != "delivery_block_six_digit"
        assert not any(
            c.value == "000119" and c.source == "delivery_block_six_digit"
            for c in r.candidates
        )

    def test_polyglass_table_header_klantcode_value_on_next_line(self):
        """Kolomkop ``KLANTCODE`` + waarde op volgende regel (geen woorden op kopregel)."""
        text = (
            "BTW NUMMER KLANTCODE MAGAZIJN VERKOOP RAYON VOORWAARDEN CODE Factuur PAGINA\n"
            "04816069 DU 23 Z23 030N010000\n"
            "Factuur 26FC000498\n"
            "Totaal te betalen EUR 1287,29"
        )
        r = extract_customer_number_result(text)
        assert r.value == "04816069"
        assert "K94258392" not in [c.value for c in r.candidates]

    def test_kvk_smear_not_customer_candidate(self):
        text = (
            "ABNA NL2A BTW Nummer kvk 94258392 algemene voorwaarden\n"
            "Klantcode\n"
            "04816069\n"
            "Factuur 26FC000498\n"
        )
        r = extract_customer_number_result(text)
        assert "K94258392" not in [c.value for c in r.candidates]
        assert r.value == "04816069"

    def test_false_k_glue_dropped_when_shorter_k_exists(self):
        text = (
            "Klantnummer\n"
            "K014135\n"
            "K 014135 52\n"
            "Totaal EUR 11,05"
        )
        r = extract_customer_number_result(text)
        values = {c.value for c in r.candidates}
        assert "K014135" in values
        assert "K01413552" not in values

    def test_onze_referentie_not_customer_label(self):
        text = "Onze referentie: Angelique Meijer\nKlantnr.: 12040\n"
        r = extract_customer_number_result(text)
        assert r.value == "12040"

    def test_not_found_when_absent(self):
        text = "Factuur 12345\nTotaal EUR 10,00"
        r = extract_customer_number_result(text)
        assert r.value is None
        assert r.candidates == []
        assert r.absence_state == "NOT_FOUND"
        assert r.source == "NOT_FOUND"

    def test_supplier_level_absent_state(self):
        r = extract_customer_number_result(
            "Factuur 12345\nTotaal EUR 10,00",
            supplier_customer_absent=True,
        )
        assert r.absence_state == "NOT_PRESENT_SUPPLIER_LEVEL"
        assert r.source == "NOT_PRESENT_SUPPLIER_LEVEL"


class TestPolyglassInvoiceCandidates:
    def test_datum_nummer_table_layout(self):
        text = (
            "CODE Factuur PAGINA\n"
            "Datum Nummer\n"
            "04816069 DU 23 Z23 030N010000\n"
            "05/03/2026 26FC000498 1/2\n"
        )
        r = extract_invoice_number_result(text)
        assert r.value == "26FC000498"
        assert len(r.candidates) >= 1
        assert any(c.source == "datum_nummer_table" for c in r.candidates)

    def test_polyglass_pdf_when_available(self):
        from pathlib import Path

        from parser.pdf_parser import extract_invoice_data, extract_text_strict

        pdf = Path(
            "/Volumes/KINGSTON/Facturen om te testen/Batch 5/Polyglass 26FC000498.pdf"
        )
        if not pdf.is_file():
            return
        raw = extract_text_strict(str(pdf))
        d = extract_invoice_data(raw)
        assert d.get("invoice_number") == "26FC000498"
        ir = d.get("invoice_number_result") or {}
        assert len(ir.get("candidates") or []) >= 1

    def test_factuur_plain_line_yields_candidates(self):
        text = "Leverancier\nKlantcode 04816069\nFactuur 26FC000498\n"
        r = extract_invoice_number_result(text, resolved="26FC000498", resolved_source="factuur_plain")
        assert r.value == "26FC000498"
        assert r.status == "confirmed"
        values = {c.value for c in r.candidates}
        assert "26FC000498" in values
        assert len(r.candidates) >= 1

    def test_factuur_plain_without_resolved_is_ambiguous_or_confirmed(self):
        text = "Factuur 26FC000498\n"
        r = extract_invoice_number_result(text)
        assert r.value == "26FC000498"
        assert len(r.candidates) >= 1


class TestPolisInvoiceCandidates:
    def test_polisnummer_spaced_digits(self):
        text = "Polisnummer : 8 0 35714\n"
        r = extract_invoice_number_result(text)
        assert r.value == "8035714"
        assert r.status == "confirmed"

    def test_polaris_pdf_invoice_number(self):
        pdf = (
            "/Volumes/KINGSTON/Facturen om te testen/Batch 5/"
            "Polaris Eerste herinnering Factuurnummer  2606(26892143).pdf"
        )
        try:
            raw = extract_text_strict(pdf)
        except OSError:
            return
        d = extract_invoice_data(raw)
        assert d.get("invoice_number") == "8035714"
        ir = d.get("invoice_number_result") or {}
        assert ir.get("value") == "8035714"
