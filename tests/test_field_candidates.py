"""Tests voor factuur-/klantnummer-kandidaten."""

from __future__ import annotations

from parser.field_candidates import extract_invoice_number_result
from parser.pdf_parser import extract_invoice_data, extract_text_strict


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
