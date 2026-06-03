"""Tests voor factuur-/klantnummer-kandidaten."""

from __future__ import annotations

from parser.field_candidates import (
    IdentFieldCandidate,
    build_ident_field_result,
    extract_customer_number_result,
    extract_email_domain_result,
    extract_invoice_date_result,
    extract_invoice_number_result,
    extract_kvk_number_result,
    extract_vat_number_result,
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
        assert len(r.candidates) >= 1
        assert any(str(c.source or "") == "fallback_missing" for c in r.candidates)
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
        assert "VP601987" in {c.value for c in inv.candidates}

    def test_samedia_customer_and_invoice_lines(self):
        text = "Customer 58181 SAMEDIAGmbH\nINVOICE R1126096 30/01/2026\n"
        inv = extract_invoice_number_result(text)
        cust = extract_customer_number_result(text)
        assert "R1126096" in {c.value for c in inv.candidates}
        assert "58181" in {c.value for c in cust.candidates}


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
        va = extract_vat_number_result(text, debtor_vat="NL148005664B01")
        assert kv.value == "24489568"
        assert va.value == "NL822167037B01"
        assert not any(c.value == "62254448" for c in kv.candidates)

    def test_iban_line_no_kvk_false_positive(self):
        text = "IBAN: NL91 ABNA 0417 1643 00\nTotaal EUR 50,00"
        r = extract_kvk_number_result(text)
        real = [c for c in r.candidates if c.source != "fallback_missing"]
        assert len(real) == 0


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
