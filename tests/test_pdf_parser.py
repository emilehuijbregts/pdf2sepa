"""Tests for parser/pdf_parser.py — invoice data extraction from PDF text."""

from __future__ import annotations

import pytest

from parser.pdf_parser import (
    extract_invoice_data,
    normalize_amount,
    extract_amount_excl_vat,
    format_remittance_text,
    build_description,
)


# ---------------------------------------------------------------------------
# Regression: customer number label variants
# ---------------------------------------------------------------------------

class TestCustomerNumberExtraction:
    def test_klantnummer_colon(self):
        d = extract_invoice_data("Klantnummer: 12345")
        assert d["customer_number"] == "12345"

    def test_klantnummer_newline(self):
        d = extract_invoice_data("Klantnummer:\n12345")
        assert d["customer_number"] == "12345"

    def test_klantnummer_space(self):
        d = extract_invoice_data("Klantnummer 12345")
        assert d["customer_number"] == "12345"

    def test_klant_nr_colon(self):
        d = extract_invoice_data("Klant nr: 1012146")
        assert d["customer_number"] == "1012146"

    def test_klantnr_space(self):
        d = extract_invoice_data("Klantnr 1012146")
        assert d["customer_number"] == "1012146"

    def test_klant_nr_dot(self):
        d = extract_invoice_data("Klant nr. 1012146")
        assert d["customer_number"] == "1012146"

    def test_debiteurnummer(self):
        d = extract_invoice_data("Debiteurnummer: 1012146")
        assert d["customer_number"] == "1012146"

    def test_debiteurennummer(self):
        d = extract_invoice_data("Debiteurennummer: 12345")
        assert d["customer_number"] == "12345"

    def test_debiteuren_nummer_alphanumeric(self):
        """Bosta-style: 'Debiteuren nummer : NL01114276'."""
        d = extract_invoice_data("Debiteuren nummer : NL01114276")
        assert d["customer_number"] == "NL01114276"

    def test_debnr_colon(self):
        d = extract_invoice_data("Debnr: 10234")
        assert d["customer_number"] == "10234"

    def test_deb_nr_dot_spaced(self):
        d = extract_invoice_data("Deb. nr. 10234")
        assert d["customer_number"] == "10234"

    def test_lidnummer(self):
        d = extract_invoice_data("Lidnummer: 1012146")
        assert d["customer_number"] == "1012146"

    def test_lidnummer_short(self):
        d = extract_invoice_data("Lidnummer: 3503")
        assert d["customer_number"] == "3503"

    def test_customer_number_english(self):
        d = extract_invoice_data("Customer number: 1012146")
        assert d["customer_number"] == "1012146"

    def test_relatienummer(self):
        d = extract_invoice_data("Relatienummer: 98765")
        assert d["customer_number"] == "98765"

    def test_klantcode(self):
        d = extract_invoice_data("Klantcode: ABC-123")
        assert d["customer_number"] == "ABC-123"

    def test_deb_nummer_variant(self):
        d = extract_invoice_data("Factuurnummer 26402381 Deb. nummer 700269")
        assert d["customer_number"] == "700269"

    def test_customer_nr_prefix_stripped(self):
        d = extract_invoice_data("Klantnummer nr143934")
        assert d["customer_number"] == "143934"

    def test_customer_klant_nr_prefers_numeric_over_postcode(self):
        d = extract_invoice_data("Klant Nr : 0956 5216jw den bosch")
        assert d["customer_number"] == "0956"

    def test_customer_klantnummer_prefers_5digit_over_postcode_digit(self):
        d = extract_invoice_data("Jan Campertlaan 6 IBAN:NL65 INGB 0669 7769 63 Klantnummer\n3201 ZZ 29459")
        assert d["customer_number"] == "29459"

    def test_customer_klantnummer_after_long_address_line(self):
        text = (
            "Majestic Products B.V.\n"
            "Jan Campertlaan 6 IBAN:NL65 INGB 0669 7769 63 Klantnummer\n"
            "3201 AX Spijkenisse BIC CODE: INGBNL2A 29459\n"
        )
        d = extract_invoice_data(text)
        assert d["customer_number"] == "29459"


# ---------------------------------------------------------------------------
# Regression: invoice number extraction
# ---------------------------------------------------------------------------

class TestInvoiceNumberExtraction:
    def test_factuurnummer_colon(self):
        d = extract_invoice_data("Factuurnummer: INV-001")
        assert d["invoice_number"] == "INV-001"

    def test_factuurnummer_colon_spaced(self):
        """Bosta-style: 'Factuurnummer : NL01D00078069'."""
        d = extract_invoice_data("Factuurnummer : NL01D00078069")
        assert d["invoice_number"] == "NL01D00078069"

    def test_factuurnummer_newline(self):
        d = extract_invoice_data("Factuurnummer:\n67890")
        assert d["invoice_number"] == "67890"

    def test_factuurnr_dot(self):
        d = extract_invoice_data("Factuurnr. 7012254003")
        assert d["invoice_number"] == "7012254003"

    def test_fact_nr_abbrev(self):
        d = extract_invoice_data("Fact. nr. 26012345")
        assert d["invoice_number"] == "26012345"

    def test_invoice_number_english(self):
        d = extract_invoice_data("Invoice number: INV-2025-001")
        assert d["invoice_number"] == "INV-2025-001"

    def test_documentnr_label(self):
        d = extract_invoice_data("Documentnr: 99887766")
        assert d["invoice_number"] == "99887766"

    def test_factuur_plain_fallback(self):
        d = extract_invoice_data("Debiteurnummer: 13395\nFactuur 41107739")
        assert d["invoice_number"] == "41107739"

    def test_nummer_datum_table_invoice(self):
        d = extract_invoice_data("Nummer/Datum 9926106153 / 03.03.2026")
        assert d["invoice_number"] == "9926106153"

    def test_ref_no_longer_matches_broadly(self):
        """'Ref: KVK12345' earlier in text must NOT override 'Factuurnummer: INV-001'."""
        text = "Ref: KVK12345\nSome other text\nFactuurnummer: INV-001"
        d = extract_invoice_data(text)
        assert d["invoice_number"] == "INV-001"

    def test_column_layout_no_cross_capture(self):
        """Column layout: 'Factuurnummer   Datum' must NOT capture 'Datum'."""
        text = "Factuurnummer   Datum          Vervaldatum\n12345           01-04-2025     30-04-2025"
        d = extract_invoice_data(text)
        assert d["invoice_number"] != "Datum"
        assert d["invoice_number"] == "12345"

    def test_tabular_klantnr_factuurnr_not_swapped(self):
        text = "Klantnr Factuurnr\nK12493 VF26-05543"
        d = extract_invoice_data(text)
        assert d["customer_number"] == "K12493"
        assert d["invoice_number"] == "VF26-05543"

    def test_tabular_deb_fact_with_leading_order_token(self):
        text = "Ordernummer Deb. nr. Fact. nr. Datum\n2603296 10295 2602561 11-02-2026"
        d = extract_invoice_data(text)
        assert d["customer_number"] == "10295"
        assert d["invoice_number"] == "2602561"


# ---------------------------------------------------------------------------
# Noise-word skipping
# ---------------------------------------------------------------------------

class TestNoiseWordSkipping:
    def test_factuurnummer_skip_op(self):
        """TU-style: 'Factuurnummer Op 7012254003' must skip 'Op'."""
        d = extract_invoice_data("Factuurnummer Op 7012254003")
        assert d["invoice_number"] == "7012254003"

    def test_klantnummer_skip_klant(self):
        """Wavin-style: 'Klantnummer klant 1012146' must skip 'klant'."""
        d = extract_invoice_data("Klantnummer klant 1012146")
        assert d["customer_number"] == "1012146"

    def test_klantnummer_skip_klant_colon(self):
        """'Klantnummer klant: 1012146' must skip 'klant'."""
        d = extract_invoice_data("Klantnummer klant: 1012146")
        assert d["customer_number"] == "1012146"

    def test_factuurnr_skip_nr(self):
        """'Factuurnummer nr 99001' must skip 'nr'."""
        d = extract_invoice_data("Factuurnummer nr 99001")
        assert d["invoice_number"] == "99001"

    def test_klantcode_skip_uw(self):
        """'Klantcode uw ref 55443' must skip 'uw' and 'ref'."""
        d = extract_invoice_data("Klantcode uw ref 55443")
        assert d["customer_number"] == "55443"

    def test_no_false_skip_real_value(self):
        """Real alphanumeric values must NOT be skipped."""
        d = extract_invoice_data("Factuurnummer: INV-001")
        assert d["invoice_number"] == "INV-001"


# ---------------------------------------------------------------------------
# Fallback restricted
# ---------------------------------------------------------------------------

class TestFallbackRestriction:
    def test_page_number_not_captured(self):
        """'1 / 5' (page number) must NOT be captured as invoice/customer."""
        d = extract_invoice_data("Pagina 1 / 5\nSome invoice text")
        assert d["invoice_number"] != "1"
        assert d["customer_number"] != "5"

    def test_large_reference_still_works(self):
        """'7012254003 / 1012146' (Wavin-style) should still work as fallback."""
        d = extract_invoice_data("7012254003 / 1012146")
        assert d["invoice_number"] == "7012254003"
        assert d["customer_number"] == "1012146"

    def test_labeled_plus_fallback(self):
        """Invoice found by label, customer from fallback."""
        text = "Factuurnummer: INV-123\n7012254003 / 1012146"
        d = extract_invoice_data(text)
        assert d["invoice_number"] == "INV-123"
        assert d["customer_number"] == "1012146"

    def test_both_labeled(self):
        text = "Acme B.V.\nFactuurnummer: 7012254003\nKlant nr: 1012146"
        d = extract_invoice_data(text)
        assert d["invoice_number"] == "7012254003"
        assert d["customer_number"] == "1012146"


# ---------------------------------------------------------------------------
# IBAN extraction
# ---------------------------------------------------------------------------

class TestIbanExtraction:
    def test_nl_iban(self):
        # NL91… is géén geldige mod‑97 testcase; echte parity vereist valide checksum.
        d = extract_invoice_data("IBAN: NL25CITI0266075452")
        assert d["iban"] == "NL25CITI0266075452"

    def test_no_iban(self):
        d = extract_invoice_data("Geen bankgegevens hier")
        assert d["iban"] is None

    def test_iban_embedded_in_text(self):
        d = extract_invoice_data("Betaal op rekeningnummer NL25CITI0266075452 svp")
        assert d["iban"] == "NL25CITI0266075452"

    def test_iban_with_spaces_is_normalized(self):
        d = extract_invoice_data("IBAN: NL25 CITI 0266 0754 52")
        assert d["iban"] == "NL25CITI0266075452"


# ---------------------------------------------------------------------------
# Amount extraction
# ---------------------------------------------------------------------------

class TestAmountExtraction:
    def test_simple_amount(self):
        d = extract_invoice_data("Totaal EUR 121,00")
        assert d["amount"] == 121.0

    def test_large_amount(self):
        d = extract_invoice_data("Totaal 999.999,99")
        assert d["amount"] == 999999.99

    def test_excl_vat(self):
        d = extract_invoice_data("Subtotaal EUR 100,00\nTotaal EUR 121,00")
        assert d["amount"] == 121.0
        assert d["amount_excl_vat"] == 100.0

    def test_te_betalen_next_line(self):
        d = extract_invoice_data("Te betalen\n€ 605,92")
        assert d["amount"] == 605.92

    def test_factuurbedrag_next_line(self):
        d = extract_invoice_data("Factuurbedrag:\n605,92")
        assert d["amount"] == 605.92

    def test_netto_goederenwaarde(self):
        assert extract_amount_excl_vat("Totaal netto goederenwaarde 9,99") == 9.99

    def test_netto_goederenbedrag(self):
        assert extract_amount_excl_vat("Netto goederenbedrag: 252,72") == 252.72

    def test_totaal_eur_preferred_over_vat_basis_totaal(self):
        text = "BTW 21,00 % Totaal\nBasisbedrag 2,19 2,19\nTotaal EUR 2,65"
        d = extract_invoice_data(text)
        assert d["amount"] == 2.65

    def test_te_betalen_table_header_not_used_as_payable(self):
        text = (
            "Omschrijving Bedrag BTW % Basis Bedrag Te betalen\n"
            "VI 9,00 330,78 29,77\n"
            "Totaal 330,78 29,77 360,55 EUR"
        )
        d = extract_invoice_data(text)
        assert d["amount"] == 360.55


# ---------------------------------------------------------------------------
# Amount normalization
# ---------------------------------------------------------------------------

class TestNormalizeAmount:
    def test_eu_comma_decimal(self):
        assert normalize_amount("1.234,56") == 1234.56

    def test_eu_dot_decimal(self):
        assert normalize_amount("1,234.56") == 1234.56

    def test_simple(self):
        assert normalize_amount("100,00") == 100.0

    def test_none(self):
        assert normalize_amount(None) is None

    def test_empty(self):
        assert normalize_amount("") is None

    def test_negative(self):
        assert normalize_amount("-100,00") == -100.0


# ---------------------------------------------------------------------------
# Credit note detection
# ---------------------------------------------------------------------------

class TestCreditNoteDetection:
    def test_creditnota(self):
        d = extract_invoice_data("Dit is een Creditnota van leverancier X")
        assert d["type"] == "credit_note"

    def test_credit_note_english(self):
        d = extract_invoice_data("Credit Note ref CN-001")
        assert d["type"] == "credit_note"

    def test_normal_invoice(self):
        d = extract_invoice_data("Factuur aan klant\nBedrag: 100,00")
        assert d["type"] == "invoice"


# ---------------------------------------------------------------------------
# Description and remittance text
# ---------------------------------------------------------------------------

class TestDescription:
    def test_build_both(self):
        assert build_description("12345", "INV-001") == "12345 / INV-001"

    def test_build_missing_customer(self):
        assert build_description(None, "INV-001") is None

    def test_build_missing_invoice(self):
        assert build_description("12345", None) is None

    def test_remittance_with_description(self):
        assert format_remittance_text("12345", "INV-001", "custom desc") == "custom desc"

    def test_remittance_without_description(self):
        assert format_remittance_text("1012146", "7012254003", None) == "1012146 / 7012254003"

    def test_remittance_only_invoice(self):
        assert format_remittance_text(None, "INV-001") == "INV-001"

    def test_remittance_only_customer(self):
        assert format_remittance_text("12345", None) == "12345"

    def test_remittance_empty(self):
        assert format_remittance_text(None, None) == ""


# ---------------------------------------------------------------------------
# Supplier hint
# ---------------------------------------------------------------------------

class TestSupplierHint:
    def test_bv(self):
        d = extract_invoice_data("Wavin Nederland B.V.\nFactuurnummer: 123")
        assert d["supplier_hint"] is not None
        assert "wavin" in d["supplier_hint"].lower() or "b.v" in d["supplier_hint"].lower()

    def test_no_hint(self):
        d = extract_invoice_data("Geen bedrijfsnaam hier\nBedrag 100,00")
        assert d["supplier_hint"] is None


class TestDebtorKvkVatExclusion:
    """Eigen KvK/BTW (debiteur) mogen nooit als leveranciervelden landen."""

    def test_picks_next_kvk_when_first_is_debtor(self):
        text = "KvK 62254448\nLeverancier KvK 24489568\nBTW NL822167037B01"
        d = extract_invoice_data(
            text,
            debtor_kvk="62254448",
            debtor_vat="NL148005664B01",
        )
        assert d["kvk_number"] == "24489568"
        assert d["vat_number"] == "NL822167037B01"

    def test_no_supplier_kvk_vat_when_only_debtor_numbers(self):
        text = "Factuur\nKvK 62254448 BTW NL148005664B01\nBedrag 100,00"
        d = extract_invoice_data(
            text,
            debtor_kvk="62254448",
            debtor_vat="NL148005664B01",
        )
        assert d["kvk_number"] is None
        assert d["vat_number"] is None

    def test_polaris_footer_ocr_vat_dotted(self):
        footer = (
            "Polaris Werk, Vitaal & Verzekeren\n"
            "info@polaris-werkvitaalverzekeren.nl\n"
            "IBAN: NL34 ABNA 0135 7358 31 | KvK: 34095053 | Btw: 8053.01.021.B.01\n"
        )
        d = extract_invoice_data(footer)
        assert d["kvk_number"] == "34095053"
        assert d["email_domain"] == "polaris-werkvitaalverzekeren.nl"
        assert d["vat_number"] == "NL805301021B01"


# ---------------------------------------------------------------------------
# Empty / edge-case inputs
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_text(self):
        d = extract_invoice_data("")
        assert d["iban"] is None
        assert d["amount"] is None
        assert d["invoice_number"] is None
        assert d["customer_number"] is None

    def test_none_text(self):
        d = extract_invoice_data(None)
        assert d["iban"] is None

    def test_only_whitespace(self):
        d = extract_invoice_data("   \n\n  ")
        assert d["amount"] is None


# ---------------------------------------------------------------------------
# Batch 5 style regressions (internationale IBAN's, hyphen labels, insurers)
# ---------------------------------------------------------------------------

class TestBatchFiveInvoicePatterns:
    def test_scan_sepa_iban_be(self):
        d = extract_invoice_data("IBAN: BE68 5390 0754 7034\nTotaal EUR 50,00")
        assert d["iban"] == "BE68539007547034"

    def test_scan_sepa_iban_de_with_spacing(self):
        d = extract_invoice_data(
            "Deutsche Bank AG IBAN: DE89 3704 0044 0532 0130 00\nTotaal EUR 500,01"
        )
        assert d["iban"] == "DE89370400440532013000"

    def test_klant_nr_hyphen_customer(self):
        d = extract_invoice_data(
            "Klant-nr.: 85763\nFactuur HA 13451308\nTotaal te betalen EUR 100,05"
        )
        assert d["customer_number"] == "85763"
        assert d["invoice_number"] == "HA13451308"

    def test_factuur_prefixed_ha_digits(self):
        d = extract_invoice_data("Leverancier Z\nFactuur HA 13451308\nEUR 88,08")
        assert d["invoice_number"] == "HA13451308"

    def test_belgian_year_slash_invoice_number(self):
        d = extract_invoice_data(
            "Referentie 26/1800001827\nIBAN BE50 4459 6389 4118\nTotaal EUR 120,44"
        )
        assert d["invoice_number"] == "26/1800001827"
        assert d["iban"].startswith("BE")

    def test_pm_coded_spaced_factuurslash_after_label(self):
        d = extract_invoice_data(
            "Factuurnummer: 2026 / 15\nTotaal EUR 222,02"
        )
        assert d["invoice_number"] == "2026/15"

    def test_uw_klant_k_prefix_customer(self):
        d = extract_invoice_data("Uw Klant K014135\nTotaal te betalen 11,05")
        assert d["customer_number"] == "K014135"

    def test_delivery_block_six_digit_customer(self):
        t = (
            "Verkoopfactuur 9\nAfleveradres\nFirma bv\nStraatnaam 88\n475700\nFactuurdatum 08-03-2026"
        )
        d = extract_invoice_data(t + "\nTe betalen 50,06")
        assert d["customer_number"] == "475700"

    def test_customer_prefers_k_prefixed_over_standalone_calendar_year_token(self):
        d = extract_invoice_data(
            "Debiteuren\nKlantnummer nr K1628\n2026\nTotaal EUR 99,91"
        )
        assert d["customer_number"] == "K1628"

    def test_insurance_difficulte_premium_line_totals_amount(self):
        d = extract_invoice_data(
            "Polaris Nederland\nVerschuldigde premie EUR 4.947,17\n"
        )
        assert d["amount"] == pytest.approx(4947.17)

    def test_polyglass_style_klantcode_and_invoice_amount(self):
        t = (
            "Leverancier\nKlantcode 04816069\nFactuur 26FC000498\n"
            "Totaal te betalen EUR 1287,29"
        )
        d = extract_invoice_data(t)
        assert d["customer_number"] == "04816069"
        assert d["invoice_number"] == "26FC000498"
        assert d["amount"] == pytest.approx(1287.29)
        ir = d.get("invoice_number_result") or {}
        assert len(ir.get("candidates") or []) >= 1
        assert any(
            c.get("value") == "26FC000498" for c in (ir.get("candidates") or [])
        )

    def test_polyglass_table_footer_both_amounts_in_candidates(self):
        """PDF-tabel: netto en totaal op één regel met EUR ertussen."""
        t = (
            "TOTAAL HANDELSWAAR KORTING\n"
            "% Bedrag TOTALE FACTUUR\n"
            "1.063,88 EUR 1.287,29\n"
        )
        d = extract_invoice_data(t)
        ar = d.get("amount_result") or {}
        values = {float(c["value"]) for c in (ar.get("candidates") or []) if c.get("value")}
        assert 1287.29 in values
        assert 1063.88 in values

    def test_klantcode_fused_word(self):
        d = extract_invoice_data("Klantcode: 09998877\nBedrag EUR 44,03")
        assert d["customer_number"] == "09998877"
