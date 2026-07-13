"""Tests voor factuur-/klantnummer-kandidaten."""

from __future__ import annotations

from pathlib import Path

import pytest

from parser.field_candidates import (
    IdentFieldCandidate,
    build_ident_field_result,
    extract_customer_number_result,
    extract_email_domain_result,
    extract_invoice_date_result,
    extract_invoice_number_result,
    extract_kvk_number_result,
    extract_vat_number_result,
    normalize_internal_vat_blacklist,
    normalize_internal_vat_numbers_for_storage,
    normalize_internal_vat_blacklist,
    parse_internal_vat_numbers,
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

    def test_klantnummer_slash_compound_same_line(self):
        """Bauder: ``Klantnummer: 603540 / 880`` op één regel."""
        text = (
            "Tesselschadestraat 28 Klantnummer: 603540 / 880\n"
            "Factuur 24065433\n"
        )
        r = extract_customer_number_result(text)
        assert r.value == "603540 / 880"

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
        assert len(r.candidates) >= 1
        assert any(str(c.source or "") == "fallback_missing" for c in r.candidates)
        assert r.absence_state == "NOT_FOUND"
        assert r.source == "NOT_FOUND"

    def test_supplier_level_absent_state(self):
        text = "Klantnummer: 99999\nFactuur 12345\nTotaal EUR 10,00"
        r = extract_customer_number_result(
            text,
            supplier_customer_absent=True,
        )
        assert r.value is None
        assert r.candidates == []
        assert r.status == "not_applicable"
        assert r.absence_state == "NOT_PRESENT_SUPPLIER_LEVEL"
        assert r.source == "NOT_PRESENT_SUPPLIER_LEVEL"

    def test_customer_number_mode_none_skips_extraction(self):
        text = "Klantnummer: 99999\nFactuur 12345\n"
        r = extract_customer_number_result(text, customer_number_mode="NONE")
        assert r.value is None
        assert r.candidates == []
        assert r.status == "not_applicable"

    def test_rejects_bic_on_klantnr_line(self):
        text = "Klantnr K12493 Uw referentie 230556 BIC INGBNL2A\n"
        r = extract_customer_number_result(text)
        assert r.value == "K12493"
        assert "INGBNL2A" not in {c.value for c in r.candidates}

    def test_rejects_internal_vat_in_customer_table(self):
        text = (
            "Faktuurnummer Fkt. Datum Klant Nr° Klant BTW Nr°\n"
            "1210001330 24.03.2026 1025995 NL001740777B35\n"
        )
        bl = normalize_internal_vat_blacklist(["NL001740777B35"])
        r = extract_customer_number_result(text, internal_vat_blacklist=bl)
        assert r.value == "1025995"
        assert "NL001740777B35" not in {c.value for c in r.candidates}

    def test_2ba_debiteurnummer_not_supplier_brand(self):
        text = (
            "Bestand Beheer Artikelen bv 2BA\n"
            "Factuurnummer : 260789 Factuurdatum : 14-01-2026\n"
            "Debiteurnummer : 113073/17078 Vervaldatum : 28-01-2026\n"
        )
        r = extract_customer_number_result(text)
        assert r.value == "113073/17078"
        assert "2BA" not in {c.value for c in r.candidates}

    def test_bosta_ocr_nlo_debiteur_normalized(self):
        text = "Debiteuren nummer : NLO1114276\n"
        r = extract_customer_number_result(text)
        assert r.value == "NL01114276"

    def test_frencken_klant_n_degree_same_line(self):
        text = (
            "Factuur N° 1800039013 01.07.2026 Pagina 1/1\n"
            "Klant N° 1158174 Betalingscondities 30 dagen, factuurdatum\n"
            "Uw BTW N° NL001740777B35 Netto gewicht 17,500 KG\n"
        )
        bl = normalize_internal_vat_blacklist(["NL001740777B35", "NL821165379B01"])
        inv = extract_invoice_number_result(text, internal_vat_blacklist=bl)
        cust = extract_customer_number_result(text, internal_vat_blacklist=bl)
        assert inv.value == "1800039013"
        assert cust.value == "1158174"
        assert "17,500" not in {c.value for c in cust.candidates}


_GOLDEN_PDF_DIR = Path(__file__).resolve().parent / "golden_dataset" / "pdfs"
_GOLDEN_CUSTOMER_NUMBER_CASES: tuple[tuple[str, str], ...] = (
    ("2ba Fact-2BA-20260114-260789-Duister.pdf", "113073/17078"),
    ("Bosta Factuur NL01D00074953_2.pdf", "NL01114276"),
    (
        "Caleffi Invoice Caleffi NV N° 1210001330  of 24.03.2026. pdf.pdf",
        "1025995",
    ),
    ("Frencken 1800039013.PDF", "1158174"),
    ("Installatiebalie Verkoopfactuur VF26-05543.pdf", "K12493"),
)


class TestGoldenPdfCustomerNumberRegression:
    @pytest.mark.parametrize(("pdf_name", "expected"), _GOLDEN_CUSTOMER_NUMBER_CASES)
    def test_golden_pdf_customer_number(self, pdf_name: str, expected: str) -> None:
        pdf = _GOLDEN_PDF_DIR / pdf_name
        if not pdf.is_file():
            pytest.skip(f"Missing golden PDF: {pdf}")
        text = extract_text_strict(str(pdf))
        bl = normalize_internal_vat_blacklist(["NL001740777B35"])
        r = extract_customer_number_result(text, internal_vat_blacklist=bl)
        assert r.value == expected


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

    def test_label_candidate_wins_over_legacy_regex_resolved(self):
        text = "Invoice No: INV-2025-002\nFactuur 202603"
        r = extract_invoice_number_result(
            text,
            resolved="202603",
            resolved_source="factuur_plain",
        )
        assert r.value == "INV-2025-002"
        assert r.source == "label"
        assert any(c.value == "INV-2025-002" for c in r.candidates)

    def test_order_reference_candidate_is_filtered_for_invoice(self):
        text = "Ordernummer: 202603\nOnze referentie 202603/8844"
        r = extract_invoice_number_result(text)
        assert r.value is None
        assert all(c.value not in {"202603", "202603/8844"} for c in r.candidates)


class TestBatch6LayoutSnippets:
    def test_rexel_header_table_candidates(self):
        text = (
            "Factuurnr Betaler Factuurdatum\n"
            "5222 AS Den Bosch\n"
            "0885007110 113023143 52111087 16-01-2026\n"
        )
        inv = extract_invoice_number_result(text)
        cust = extract_customer_number_result(text)
        assert "113023143" in {c.value for c in inv.candidates}
        assert "52111087" in {c.value for c in cust.candidates}

    def test_rensa_header_table_invoice_not_debiteur(self):
        text = "Datum Factuurnr. Debiteurnr.\n14-01-26 26033768 017650\n"
        inv = extract_invoice_number_result(text)
        cust = extract_customer_number_result(text)
        assert inv.value == "26033768"
        assert "017650" not in {
            c.value for c in inv.candidates if c.source == "header_table_invoice"
        }
        assert cust.value == "017650"

    def test_prima_arbo_header_table_columns(self):
        text = (
            "Factuurdatum Factuurnummer Debiteurennummer\n"
            "09-01-2026 20260075 20180168\n"
        )
        inv = extract_invoice_number_result(text)
        cust = extract_customer_number_result(text)
        assert inv.value == "20260075"
        assert cust.value == "20180168"

    def test_prima_arbo_split_header_amount_row_not_customer(self):
        text = (
            "Factuur datum Factuur nummer Debiteur nummer\n"
            "09-01-2026 20260075 20180168\n"
            "118,84 24,96 143,80\n"
        )
        cust = extract_customer_number_result(text)
        assert "118,84" not in {
            c.value for c in cust.candidates if c.source == "header_table_customer"
        }

    def test_roba_nummer_and_pipe_customer(self):
        text = "HANDELSONDERNEMING DUISTER | C05630\nNummer INV-0396393 NL007469184B01\n"
        inv = extract_invoice_number_result(text)
        cust = extract_customer_number_result(text)
        assert "INV-0396393" in {c.value for c in inv.candidates}
        assert "C05630" in {c.value for c in cust.candidates}

    def test_ubbink_factuur_relatie_table(self):
        text = (
            "Factuur Relatie Datum\n"
            "SIN/10567557 101900683 26-02-2026\n"
            "Uw Order : 20260314\n"
        )
        inv = extract_invoice_number_result(text)
        cust = extract_customer_number_result(text)
        assert "SIN/10567557" in {c.value for c in inv.candidates}
        assert "101900683" in {c.value for c in cust.candidates}

    def test_walraven_inline_factuur_pagina(self):
        text = "Factuur VP601987 Pagina 1 / 1\nDebiteurnummer 801083\n"
        inv = extract_invoice_number_result(text)
        assert inv.value == "VP601987"
        assert "VP601987" in {c.value for c in inv.candidates}

    def test_vent_axia_debiteur_not_pakbon(self):
        text = (
            "Factuurnummer : 26801599 d.d. 05-02-2026 Vervaldatum : 07-03-2026\n"
            "Ordernr. Vent-Axia : 22603170 Debiteurnummer : 219073\n"
            "Pakbonnummer : 26003076 d.d. 05-02-2026 Betalingstermijn : 30 dagen\n"
        )
        cust = extract_customer_number_result(text)
        assert cust.value == "219073"
        assert cust.status == "confirmed"

    def test_wentzel_deb_nr_not_payment_term_dg(self):
        text = (
            "5216JW 'S-Hertogenbosch Uw deb.nr.: 994 Betalingscond.: 14dg 1,5% | Netto 45dg\n"
            "Bij betaling vermelden: 994 - VF00269858\n"
        )
        cust = extract_customer_number_result(text)
        assert cust.value == "994"
        assert cust.status == "confirmed"

    def test_samedia_customer_and_invoice_lines(self):
        text = "Customer 58181 SAMEDIAGmbH\nINVOICE R1126096 30/01/2026\n"
        inv = extract_invoice_number_result(text)
        cust = extract_customer_number_result(text)
        assert "R1126096" in {c.value for c in inv.candidates}
        assert "58181" in {c.value for c in cust.candidates}

    def test_venttrade_multi_slash_invoice(self):
        text = (
            "Uw BTW-nummer NL001740777B35\n"
            "Project / Referentie Ordernummer Verzenddatum Factuurdatum Vervaldatum Factuurnummer\n"
            "20260052 100004805 14-01-2026 14-01-2026 13-02-2026 1100/220/10020159\n"
        )
        inv = extract_invoice_number_result(text)
        vals = {c.value for c in inv.candidates}
        assert "1100/220/10020159" in vals
        assert "NL001740777B35" not in vals

    def test_option_tape_invoice_number_not_vat(self):
        pdf = (
            Path(__file__).resolve().parent
            / "golden_dataset/pdfs/Option tape Verkoopfactuur VF2602902.pdf"
        )
        if not pdf.is_file():
            import pytest

            pytest.skip(f"Missing fixture PDF: {pdf}")
        text = extract_text_strict(str(pdf))
        inv = extract_invoice_number_result(text)
        vals = {c.value for c in inv.candidates if c.source != "fallback_missing"}
        assert inv.value == "VF2602902"
        assert "VF2602902" in vals
        assert "NL001740777B35" not in vals

    def test_resolved_vat_rejected_from_invoice_pool(self):
        text = "Factuur\nVF2602902\nUw BTW-nummer NL001740777B35\n"
        inv = extract_invoice_number_result(
            text,
            resolved="NL001740777B35",
            resolved_source="label",
        )
        vals = {c.value for c in inv.candidates if c.source != "fallback_missing"}
        assert inv.value == "VF2602902"
        assert "NL001740777B35" not in vals

    def test_vte_credit_note_vcr_candidate(self):
        text = "Creditnota VCR2600003+\nFact.nr. VF2600115+ - Verz.nr.\n"
        inv = extract_invoice_number_result(text)
        vals = {c.value for c in inv.candidates if c.source != "fallback_missing"}
        assert "VCR2600003" in vals
        assert "VF2600115" not in vals

    def test_defrancq_credit_note_vcn_colon(self):
        text = "Creditnota: VCN25/000453\nFactuurnr. VFA25/13655\n"
        inv = extract_invoice_number_result(text)
        vals = {c.value for c in inv.candidates if c.source != "fallback_missing"}
        assert "VCN25/000453" in vals
        assert inv.value == "VCN25/000453"

    def test_korver_credit_note_vc_inline(self):
        text = "Creditnota: VC-51710\nFactuurnr VF-1094659\n"
        inv = extract_invoice_number_result(text)
        vals = {c.value for c in inv.candidates if c.source != "fallback_missing"}
        assert "VC-51710" in vals
        assert inv.value == "VC-51710"

    def test_korver_credit_note_vc_next_line(self):
        text = "Korver Holland B.V.\nCreditnota\nVC-51710\nFactuurnr VF-12345\n"
        inv = extract_invoice_number_result(text)
        vals = {c.value for c in inv.candidates if c.source != "fallback_missing"}
        assert "VC-51710" in vals
        assert inv.value == "VC-51710"

    def test_korver_creditnota_table_header(self):
        text = (
            "klantnummer Factuurdatum Creditnota Betalingstermijn\n"
            "D3269 10-02-2026 VC-51710 30 dgn. netto na factuurdatum\n"
        )
        inv = extract_invoice_number_result(text)
        vals = {c.value for c in inv.candidates if c.source != "fallback_missing"}
        assert "VC-51710" in vals
        assert inv.value == "VC-51710"

    def test_defrancq_credit_beats_debtor_kvk_header_table(self):
        from parser.field_model import FieldCandidate
        from parser.field_candidates import rank_candidates

        text = (
            "Creditnota: VCN25/000453\n"
            "klantnummer Factuurdatum Factuurnr Betalingstermijn\n"
            "K05251 14-01-2026 62254448 30 dgn.\n"
        )
        inv = extract_invoice_number_result(text, debtor_kvk="62254448")
        assert inv.value == "VCN25/000453"
        assert not any(c.value == "62254448" for c in inv.candidates)

        vcn = FieldCandidate(
            value="VCN25/000453",
            source="credit_note_title",
            confidence=92,
            context="Creditnota: VCN25/000453",
            meta={"field_id": "invoice_number", "match_type": "regex"},
        )
        kvk = FieldCandidate(
            value="62254448",
            source="header_table_invoice",
            confidence=55,
            context="K05251 14-01-2026 62254448 30 dgn.",
            label="klantnummer Factuurdatum Factuurnr",
            meta={"field_id": "invoice_number", "match_type": "label"},
        )
        ranked = rank_candidates("invoice_number", [vcn, kvk], context="resolver")
        assert ranked[0].value == "VCN25/000453"

    def test_van_den_borne_colon_invoice_id(self):
        text = "Factuurnummer :4126VF01369\n"
        inv = extract_invoice_number_result(text)
        assert "4126VF01369" in {c.value for c in inv.candidates}

    def test_bic_not_invoice_candidate(self):
        from parser.field_candidates import _invoice_candidate_ok

        assert not _invoice_candidate_ok("RABONL2U")
        assert not _invoice_candidate_ok("NL27")

    def test_tegeka_factuur_debiteur_table_row(self):
        text = (
            "Factuur Debiteur Factuur\n"
            "Bestelbonnr. 20260458 18-03-2026 10476 93557\n"
        )
        inv = extract_invoice_number_result(text)
        cust = extract_customer_number_result(text)
        assert "93557" in {c.value for c in inv.candidates}
        assert cust.value == "10476"

    def test_dawo_factuur_debiteur_table_row(self):
        text = (
            "Factuur Debiteur Factuur\n"
            "20250817 / DEN BOSCH 18-06-2025 10890 251237\n"
        )
        inv = extract_invoice_number_result(text)
        cust = extract_customer_number_result(text)
        assert inv.value == "251237"
        assert cust.value == "10890"

    def test_de_waal_short_invoice_number_same_line_label(self):
        text = (
            "Tesselschadestraat 28 Factuurnummer: 2861\n"
            "5216 JW 'S-HERTOGENBOSCH Klantnummer: 218\n"
        )
        inv = extract_invoice_number_result(text)
        assert inv.value == "2861"

    def test_korver_table_factuurnr_column(self):
        text = (
            "klantnummer Factuurdatum Factuurnr Betalingstermijn\n"
            "D3269 10-02-2026 VF-1094659 30 dgn. netto na factuurdatum\n"
        )
        inv = extract_invoice_number_result(text)
        assert inv.value == "VF-1094659"

    def test_louwman_disclaimer_not_header_table(self):
        text = (
            "Dubbel uitgereikt op aanvraag van de klant aan wie deze factuur is "
            "uitgereikt en de originele factuur:\n"
            "aanschrijving 10/1974.\n"
            "Bij betaling gaarne vermelden: Klantnr. 2060342 Factnr. PVF74-1308422\n"
        )
        inv = extract_invoice_number_result(text)
        assert inv.value == "PVF74-1308422"
        assert "10/1974" not in {
            c.value for c in inv.candidates if c.source == "header_table_invoice"
        }


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


class TestVatKvkEmailExtraction:
    def test_vat_label_spaced_nl_format(self):
        text = "BTW-nummer: NL 8053 010 21 B 01"
        r = extract_vat_number_result(text)
        assert r.value == "NL805301021B01"
        assert any(
            c.meta.get("extraction_method") == "label_match"
            for c in r.candidates
            if c.value == "NL805301021B01"
        )

    def test_footer_kvk_vat_email_context(self):
        footer = (
            "Polaris Werk\n"
            "info@polaris-werkvitaalverzekeren.nl\n"
            "IBAN: NL34 ABNA 0135 7358 31 | KvK: 34095053 | Btw: 8053.01.021.B.01\n"
        )
        kv = extract_kvk_number_result(footer)
        va = extract_vat_number_result(footer)
        em = extract_email_domain_result(footer)
        assert kv.value == "34095053"
        assert va.value == "NL805301021B01"
        assert em.value == "polaris-werkvitaalverzekeren.nl"
        assert any(c.meta.get("context_hint") == "footer" for c in kv.candidates if c.value)

    def test_chamber_of_commerce_kvk(self):
        r = extract_kvk_number_result("Chamber of Commerce 12345678")
        assert r.value == "12345678"
        assert any(c.meta.get("extraction_method") == "label_match" for c in r.candidates)

    def test_from_header_email_with_source_email_meta(self):
        text = "From: support@supplier.nl\n\nLine items\nTotaal 100,00"
        r = extract_email_domain_result(text)
        assert r.value == "supplier.nl"
        c = next(c for c in r.candidates if c.value == "supplier.nl")
        assert c.meta.get("source_email") == "support@supplier.nl"
        assert c.meta.get("context_hint") == "header"

    def test_random_eight_digit_not_kvk_without_business_context(self):
        r = extract_kvk_number_result("Lorem ipsum 12345678 dolor sit amet")
        real = [c for c in r.candidates if c.source != "fallback_missing"]
        assert not any(c.value == "12345678" for c in real)

    def test_debtor_kvk_vat_excluded_from_candidates(self):
        text = "KvK 62254448\nLeverancier KvK 24489568\nBTW NL822167037B01"
        kv = extract_kvk_number_result(text, debtor_kvk="62254448")
        va = extract_vat_number_result(text)
        assert kv.value == "24489568"
        assert va.value == "NL822167037B01"
        assert not any(c.value == "62254448" for c in kv.candidates)

    def test_iban_line_no_kvk_false_positive(self):
        text = "IBAN: NL91 ABNA 0417 1643 00\nTotaal EUR 50,00"
        r = extract_kvk_number_result(text)
        real = [c for c in r.candidates if c.source != "fallback_missing"]
        assert len(real) == 0


class TestRound4Hardening:
    def test_parse_internal_vat_numbers_comma_semicolon(self):
        assert parse_internal_vat_numbers("NL111111111B01, NL222222222B02; NL333333333B03") == [
            "NL111111111B01",
            "NL222222222B02",
            "NL333333333B03",
        ]

    def test_normalize_internal_vat_numbers_for_storage(self):
        raw = "NL 148005664 B01, NL813771213B01"
        assert normalize_internal_vat_numbers_for_storage(raw) == [
            "NL148005664B01",
            "NL813771213B01",
        ]

    def test_internal_vat_blacklist_normalizes_list(self):
        bl = normalize_internal_vat_blacklist(["NL 148005664 B01", ""])
        assert bl == frozenset({"NL148005664B01"})

    def test_internal_vat_blacklist_drops_from_invoice_and_vat(self):
        blacklist = frozenset({"NL148005664B01"})
        text = "Factuurnummer: NL148005664B01\nBTW NL822167037B01"
        inv = extract_invoice_number_result(text, internal_vat_blacklist=blacklist)
        assert not any(c.value == "NL148005664B01" for c in inv.candidates)
        vat = extract_vat_number_result(text, internal_vat_blacklist=blacklist)
        assert vat.value == "NL822167037B01"

    def test_vat_on_iban_line_rejected(self):
        text = "IBAN: NL91 ABNA 0417 1643 00 | fragment AB410COPERBASE"
        r = extract_vat_number_result(text)
        assert r.value is None

    def test_wavin_footer_vat_dropped_when_header_labeled(self):
        text = (
            "Wavin Nederland B.V.\n"
            "BTW nr. NL813771213B01\n"
            + "\n".join(["regel"] * 18)
            + "\nBTW: NL148005664B01\n"
        )
        r = extract_vat_number_result(
            text,
            internal_vat_blacklist=frozenset({"NL148005664B01"}),
        )
        assert r.value == "NL813771213B01"

    def test_qblades_style_no_customer_without_label(self):
        text = (
            "QBlades\n"
            "INV/2026/00364\n"
            "KvK 75187760\n"
            "BTW NL860176113B01\n"
        )
        r = extract_customer_number_result(text)
        assert r.value is None

    def test_unlabeled_date_not_customer_number(self):
        text = "Referentie 2026-01-23\nTotaal 100,00"
        r = extract_customer_number_result(text)
        assert r.value is None


class TestIdentExplainability:
    def test_invoice_candidate_contains_label_source_and_match_type(self):
        r = extract_invoice_number_result("Rechnung Nr: RE-2026-77")
        c = next(c for c in r.candidates if c.value == "RE-2026-77")
        assert c.meta.get("label_source")
        assert c.meta.get("match_type") == "label"

    def test_customer_candidate_prefers_factureren_aan_label_context(self):
        text = "Factureren aan nr: 556677\nUw referentie 202603"
        r = extract_customer_number_result(text)
        assert r.value == "556677"
        c = next(c for c in r.candidates if c.value == "556677")
        assert c.meta.get("match_type") == "label"
        assert "factureren aan" in str(c.meta.get("label_source") or "").lower()


class TestDeterministicCandidateRanking:
    def test_higher_confidence_wins_within_same_label_tier(self):
        cands = [
            IdentFieldCandidate(
                value="LOW",
                source="label",
                confidence=80,
                context="",
                label="Factuurnummer",
            ),
            IdentFieldCandidate(
                value="HIGH",
                source="label",
                confidence=90,
                context="",
                label="Factuurnummer",
            ),
        ]
        r = build_ident_field_result(cands, field_id="invoice_number")
        assert r.value == "HIGH"
        assert r.confidence == 90

    def test_label_beats_higher_confidence_regex(self):
        cands = [
            IdentFieldCandidate(
                value="LOW",
                source="label",
                confidence=80,
                context="",
                label="Factuurnummer",
            ),
            IdentFieldCandidate(
                value="HIGH",
                source="factuur_plain",
                confidence=90,
                context="",
                label="Factuur",
            ),
        ]
        r = build_ident_field_result(cands, field_id="invoice_number")
        assert r.value == "LOW"

    def test_specific_label_beats_generic_label_on_equal_confidence(self):
        cands = [
            IdentFieldCandidate(
                value="GEN",
                source="label",
                confidence=88,
                context="",
                label="Klant",
            ),
            IdentFieldCandidate(
                value="SPEC",
                source="label",
                confidence=88,
                context="",
                label="Klantnummer",
            ),
        ]
        r = build_ident_field_result(cands)
        assert r.value == "SPEC"

    def test_label_beats_regex_on_equal_confidence(self):
        cands = [
            IdentFieldCandidate(
                value="RX",
                source="factuur_plain",
                confidence=87,
                context="",
                label="Factuur",
            ),
            IdentFieldCandidate(
                value="LBL",
                source="label",
                confidence=87,
                context="",
                label="Factuurnummer",
            ),
        ]
        r = build_ident_field_result(cands)
        assert r.value == "LBL"

    def test_source_priority_breaks_full_tie_deterministically(self):
        cands = [
            IdentFieldCandidate(
                value="YEAR",
                source="year_slash_ref",
                confidence=82,
                context="",
                label="Factuur",
            ),
            IdentFieldCandidate(
                value="COLON",
                source="factuur_colon",
                confidence=82,
                context="",
                label="Factuur",
            ),
        ]
        r = build_ident_field_result(cands)
        assert r.value == "COLON"

    def test_trace_contains_winner_and_loser_reasons(self):
        cands = [
            IdentFieldCandidate(
                value="A",
                source="label",
                confidence=88,
                context="",
                label="Factuurnummer",
            ),
            IdentFieldCandidate(
                value="B",
                source="factuur_plain",
                confidence=88,
                context="",
                label="Factuur",
            ),
        ]
        r = build_ident_field_result(cands)
        trace = r.decision_trace
        assert any(
            isinstance(e, dict) and e.get("win") is True and e.get("winner_reason")
            for e in trace
        )
        assert any(
            isinstance(e, dict) and e.get("win") is False and e.get("excluded_reason")
            for e in trace
        )

    def test_resolved_value_is_candidate_hint_not_bypass(self):
        cands = [
            IdentFieldCandidate(
                value="INV-9001",
                source="label",
                confidence=88,
                context="",
                label="Factuurnummer",
            ),
        ]
        r = build_ident_field_result(
            cands,
            resolved_value="LEGACY-LOW",
            resolved_source="resolved",
            field_id="invoice_number",
        )
        assert r.value == "INV-9001"
        assert r.source == "label"

    def test_cross_field_penalty_applies_before_ranking(self):
        cands = [
            IdentFieldCandidate(
                value="202603",
                source="ref_slash",
                confidence=90,
                context="Ordernummer: 202603",
                label="",
            ),
            IdentFieldCandidate(
                value="INV-2026-01",
                source="label",
                confidence=90,
                context="Factuurnummer: INV-2026-01",
                label="Factuurnummer",
            ),
        ]
        r = build_ident_field_result(cands, field_id="invoice_number")
        assert r.value == "INV-2026-01"
        penalized = next(c for c in r.candidates if c.value == "202603")
        assert penalized.meta.get("cross_field_penalty_applied") is True
        assert int(penalized.confidence) < 90

    def test_deterministic_result_repeated_runs(self):
        cands = [
            IdentFieldCandidate(
                value="X",
                source="factuur_plain",
                confidence=84,
                context="",
                label="Factuur",
            ),
            IdentFieldCandidate(
                value="Y",
                source="label",
                confidence=84,
                context="",
                label="Factuurnummer",
            ),
        ]
        winners = [build_ident_field_result(cands).value for _ in range(10)]
        assert len(set(winners)) == 1

    def test_trace_reason_codes_are_restricted(self):
        cands = [
            IdentFieldCandidate(
                value="A",
                source="label",
                confidence=88,
                context="",
                label="Factuurnummer",
            ),
            IdentFieldCandidate(
                value="B",
                source="factuur_plain",
                confidence=88,
                context="",
                label="Factuur",
            ),
        ]
        allowed = {
            "higher_confidence",
            "lower_confidence",
            "stronger_label_match",
            "weaker_label",
            "field_keyword_match",
            "weaker_field_type",
            "better_context_proximity",
            "worse_context_proximity",
            "lower_source_priority",
            "deterministic_tiebreak",
            "cross_field_penalty",
        }
        r = build_ident_field_result(cands, field_id="invoice_number")
        for entry in r.decision_trace:
            if not isinstance(entry, dict) or entry.get("kind") == "final":
                continue
            reason = str(entry.get("winner_reason") or entry.get("excluded_reason") or "")
            if reason:
                assert reason in allowed


class TestPhase2InvoiceSelectionRanking:
    def test_invoice_labeled_beats_order_when_both_present(self):
        cands = [
            IdentFieldCandidate(
                value="ORD-202603",
                source="label",
                confidence=92,
                context="Ordernummer: ORD-202603",
                label="Ordernummer",
                meta={"field_id": "invoice_number", "match_type": "label"},
            ),
            IdentFieldCandidate(
                value="INV-9001",
                source="label",
                confidence=90,
                context="Factuurnummer: INV-9001",
                label="Factuurnummer",
                meta={"field_id": "invoice_number", "match_type": "label"},
            ),
        ]
        r = build_ident_field_result(cands, field_id="invoice_number")
        assert r.value == "INV-9001"

    def test_order_may_win_without_invoice_labeled_peer(self):
        cands = [
            IdentFieldCandidate(
                value="ORD-202603",
                source="label",
                confidence=88,
                context="Ordernummer: ORD-202603",
                label="Ordernummer",
                meta={"field_id": "invoice_number", "match_type": "label"},
            ),
        ]
        r = build_ident_field_result(cands, field_id="invoice_number")
        assert r.value == "ORD-202603"

    def test_credit_invoice_beats_normal_invoice_same_field(self):
        cands = [
            IdentFieldCandidate(
                value="VF2600001",
                source="label",
                confidence=90,
                context="Factuurnummer: VF2600001",
                label="Factuurnummer",
                meta={"field_id": "invoice_number", "match_type": "label"},
            ),
            IdentFieldCandidate(
                value="VCR2600003",
                source="label",
                confidence=90,
                context="Creditnota: VCR2600003",
                label="Creditnota",
                meta={"field_id": "invoice_number", "match_type": "label"},
            ),
        ]
        r = build_ident_field_result(cands, field_id="invoice_number")
        assert r.value == "VCR2600003"


class TestInvoiceDateCandidateRanking:
    def test_invoice_date_tiebreak_prefers_newer_date(self):
        cands = [
            IdentFieldCandidate(
                value="2024-02-05",
                source="invoice_date_label_same_line",
                confidence=90,
                context="Factuurdatum 05-02-2024",
                label="Factuurdatum",
                meta={"field_id": "invoice_date", "match_type": "label"},
            ),
            IdentFieldCandidate(
                value="2026-02-05",
                source="invoice_date_label_same_line",
                confidence=90,
                context="Factuurdatum 05-02-2026",
                label="Factuurdatum",
                meta={"field_id": "invoice_date", "match_type": "label"},
            ),
        ]
        r = build_ident_field_result(cands, field_id="invoice_date")
        assert r.value == "2026-02-05"

    def test_extract_invoice_date_prefers_label_proximity_and_recency(self):
        text = (
            "Besteldatum 05-02-2024\n"
            "Factuurdatum\n"
            "05-02-2026\n"
            "Vervaldatum 05-03-2026\n"
        )
        r = extract_invoice_date_result(text)
        assert r.value == "2026-02-05"


class TestBatch8LayoutSnippets:
    def test_te_solutions_short_labeled_klantnummer(self):
        text = (
            "Factuurnummer: 2025092\n"
            "Klantnummer: 82\n"
            "Uw kenmerk: 250071\n"
        )
        r = extract_customer_number_result(text)
        assert r.value == "82"

    def test_brozus_debiteur_not_payment_ref_slash(self):
        text = (
            "Debiteur-nr: 11287\n"
            "Factuurnummer: 218531\n"
            "Betalingskenmerk: 11287/218531\n"
        )
        r = extract_customer_number_result(text)
        assert r.value == "11287"

    def test_bruil_debiteurnummer_beats_opdrachtnummer(self):
        text = (
            "Debiteurnummer : 212554 Factuurdatum : 28-10-2025\n"
            "Klantnummer / Projectnummer : 212554 / VO097572\n"
            "Uw opdrachtnummer : 20251405\n"
        )
        r = extract_customer_number_result(text)
        assert r.value == "212554"

    def test_discount_office_factuur_inline(self):
        text = "Factuur F2661213 Factuuradres: Duister\n"
        r = extract_invoice_number_result(text)
        assert r.value == "F2661213"

    def test_hasmi_faktuurnr_label(self):
        text = "DebiteurNr.: 1003242 FaktuurNr. : 2511381 Datum: 17-11-2025\n"
        r = extract_invoice_number_result(text)
        assert r.value == "2511381"

    def test_nedsale_faktuurnummer_table(self):
        text = (
            "FACTUUR Factuurdatum Faktuurnummer\n"
            "Bij betaling vermelden\n"
            "Btw nr: code DUIDEN 11-11-2025 1007-81032\n"
        )
        r = extract_invoice_number_result(text)
        assert r.value == "1007-81032"

    def test_wildkamp_factuur_inline_number(self):
        text = "FACTUUR 125004140 Datum: 28-09-2025\n"
        r = extract_invoice_number_result(text)
        assert r.value == "125004140"

    def test_english_date_label_month_first(self):
        text = "Invoice: 2450016881\nDate: Jul 2, 2026\n"
        r = extract_invoice_date_result(text)
        assert r.value == "2026-07-02"
        assert r.source.startswith("invoice_date_label")

    def test_unatherm_belegnummer(self):
        text = "Belegnummer 2025-10235\nKundennummer 53516\n"
        r = extract_invoice_number_result(text)
        assert r.value == "2025-10235"

    def test_unatherm_vorgangs_beleg_combined_table(self):
        text = "Vorgangsnummer Belegnummer\n668 2025-10235\n"
        r = extract_invoice_number_result(text)
        assert r.value == "668 2025-10235"

    def test_unatherm_vorgangs_beleg_combined_inline(self):
        text = "Vorgangsnummer: 668\nBelegnummer: 2025-10235\n"
        r = extract_invoice_number_result(text)
        assert r.value == "668 2025-10235"

    def test_unatherm_vorgangs_beleg_not_combined_without_vorgang(self):
        text = "Belegnummer 2025-10235\n"
        r = extract_invoice_number_result(text)
        assert r.value == "2025-10235"

    def test_eurosalt_header_table_deb_factuur(self):
        text = (
            "Deb. nr. Factuur nr. Datum Ordernr.:\n"
            "Verkoop Volgens Bestellingnummer: 2954/BETAALD\n"
            "839525 25704611 30-06-2025 20254628\n"
        )
        inv = extract_invoice_number_result(text)
        cust = extract_customer_number_result(text)
        assert inv.value == "25704611"
        assert cust.value == "839525"

    def test_goossens_factuur_f_prefix(self):
        text = (
            "FACTUUR F.25000090\n"
            "Datum Klant.Nr Btw.Nr Referentie Klant\n"
            "21/05/2025 002260/000 NL001740777B35\n"
        )
        r = extract_invoice_number_result(text)
        assert r.value == "F.25000090"

    def test_skylux_dotted_nummer_label(self):
        text = "Nummer.............................:VF25-058813\n"
        r = extract_invoice_number_result(text)
        assert r.value == "VF25-058813"

    def test_grainplastics_fact_dat_not_orderdatum(self):
        text = (
            "Debiteurnummer : 1071 Orderdatum : 09-04-2025\n"
            "Fact.dat. : 11-04-2025\n"
        )
        r = extract_invoice_date_result(text)
        assert r.value == "2025-04-11"

    def test_dewin_no_customer_from_afleverbon(self):
        text = (
            "Factuurnummer 192432\n"
            "Afleveradres:\n"
            "Handelsonderneming Duister\n"
            "Onze afleverbon: 746646 Onze order: 58841\n"
        )
        r = extract_customer_number_result(text)
        assert r.value is None

    def test_dewin_email_prefers_supplier_domain_over_debtor_billing(self):
        text = (
            "0543-518822 E dewin@dewinisolatie.nl dewinisolatie.nl\n"
            "E-mail factuur@duister.eu\n"
            "Voor Handelsonderneming Duister\n"
        )
        r = extract_email_domain_result(text, debtor_name="Handelsonderneming Duister")
        assert r.value == "dewinisolatie.nl"

    def test_unatherm_rechnung_datum_not_payment_due(self):
        text = (
            "Rechnung\n"
            "Belegnummer 2025-10235\n"
            "Datum 18.12.2025\n"
            "Kundennummer 53516\n"
            "30Tage (bis 17.01.2026) ohne Abzug 27.818,00EUR\n"
        )
        r = extract_invoice_date_result(text)
        assert r.value == "2025-12-18"
        assert r.source.startswith("invoice_date_label")

    def test_vloerbedekking_no_customer_from_bank(self):
        text = (
            "Rekening nummer: Rabobank 1774.58.739\n"
            "Iban: NL81RABO0177458739\n"
            "Factuurnummer : 2025-4606\n"
        )
        r = extract_customer_number_result(text)
        assert r.value is None

    def test_prima_arbo_no_customer_from_payment_term_days(self):
        text = (
            "Factuurdatum Factuurnummer Debiteurennummer Onderwerp\n"
            "09-01-2026 20260075 20180168 Factuur\n"
            "U wordt verzocht het bovenstaande bedrag ovv uw factuur- en debiteurennummer binnen 14 dagen over te maken\n"
        )
        r = extract_customer_number_result(text)
        assert r.value == "20180168"

    def test_werova_combined_factuur_line_not_header_table(self):
        text = (
            "Factuur nr: 1620543 Ordernummer: 181795\n"
            "Debiteurnr: 010525 Uw referentie: Dhr. W Duister\n"
        )
        r = extract_invoice_number_result(text)
        assert r.value == "1620543"

    def test_werova_debiteurnr_not_uw_referentie_date(self):
        """Werova: ``Uw referentie`` kan een datumcode zijn op dezelfde regel als Debiteurnr."""
        text = (
            "Factuur nr: 1621391 Ordernummer: 182672\n"
            "Debiteurnr: 010525 Uw referentie: 20260729\n"
        )
        r = extract_customer_number_result(text)
        assert r.value == "010525"
        assert "20260729" not in [c.value for c in r.candidates]

    def test_dsg_combined_factuurnr_line_not_header_table(self):
        text = (
            "Factuurnr 025261476 Ordernummer 0020135829 Verkoper\n"
            "Factuurdatum 22-05-2025 Leveringsnummer 15011880 Theo Beset\n"
        )
        r = extract_invoice_number_result(text)
        assert r.value == "025261476"
