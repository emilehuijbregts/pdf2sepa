"""Tests for the AmountCandidate / AmountResult model and selection logic.

Covers:
- _select_amount decision tree (confirmed / tentative / ambiguous)
- _extract_amount_candidates on representative PDF text snippets
- Integration with extract_invoice_data (amount_result dict)
- Engine behaviour: ambiguous/failed block payment, low_confidence warns for legacy only
"""

from __future__ import annotations

from decimal import Decimal

import pytest

import parser.pdf_parser as pdf_parser
from parser.pdf_parser import (
    AmountCandidate,
    _amount_payable_score,
    _classify_candidate_amount_type,
    _extract_amount_candidates,
    _select_amount,
    _PAYABLE_SCORE_MARGIN,
    extract_invoice_data,
    normalize_amount_decimal,
)
from logic.payment_engine import calculate_payments


# ---------------------------------------------------------------------------
# normalize_amount_decimal
# ---------------------------------------------------------------------------

class TestNormalizeAmountDecimal:
    def test_eu_comma(self):
        assert normalize_amount_decimal("1.234,56") == Decimal("1234.56")

    def test_simple(self):
        assert normalize_amount_decimal("100,00") == Decimal("100.00")

    def test_none(self):
        assert normalize_amount_decimal(None) is None

    def test_empty(self):
        assert normalize_amount_decimal("") is None

    def test_rounding(self):
        assert normalize_amount_decimal("99,999") == Decimal("100.00")


# ---------------------------------------------------------------------------
# _select_amount — all 5 status paths
# ---------------------------------------------------------------------------

class TestSelectAmountMissing:
    """No candidates → ambiguous (no confirmed winner)."""

    def test_empty_list(self):
        r = _select_amount([])
        assert r.status == "ambiguous"
        assert r.value is None
        assert r.confidence == 0
        assert r.source == "NO_CANDIDATES"


class TestSelectAmountCertain:
    """Single high-confidence candidate → confirmed."""

    def test_single_high(self):
        c = AmountCandidate(Decimal("605.92"), "total_label_payable", 100, "Te betalen 605,92")
        r = _select_amount([c])
        assert r.status == "confirmed"
        assert r.value == Decimal("605.92")
        assert r.confidence == 100
        assert r.source == "TOTAL_LABEL_PAYABLE"

    def test_multiple_same_value(self):
        """Multiple labels pointing to the same amount → certain."""
        c1 = AmountCandidate(Decimal("605.92"), "total_label_payable", 100, "Te betalen")
        c2 = AmountCandidate(Decimal("605.92"), "total_label_invoice", 95, "Factuurbedrag")
        r = _select_amount([c1, c2])
        assert r.status == "confirmed"
        assert r.value == Decimal("605.92")
        assert r.confidence == 100

    def test_same_value_within_one_cent(self):
        """Values within EUR 0.01 are grouped together."""
        c1 = AmountCandidate(Decimal("605.92"), "total_label_payable", 100, "ctx")
        c2 = AmountCandidate(Decimal("605.93"), "total_label_invoice", 95, "ctx")
        r = _select_amount([c1, c2])
        assert r.status == "confirmed"
        assert r.value == Decimal("605.92")

    def test_dominant_winner(self):
        """One candidate confidence >= 85 with >= 20 point lead → confirmed (capped at 85)."""
        c1 = AmountCandidate(Decimal("605.92"), "total_label_payable", 100, "ctx")
        c2 = AmountCandidate(Decimal("500.00"), "total_label_generic", 70, "ctx")
        r = _select_amount([c1, c2])
        assert r.status == "confirmed"
        assert r.value == Decimal("605.92")
        assert r.confidence <= 85

    def test_explicit_payable_beats_sum_when_both_incl(self):
        """``Te betalen`` vs ``Totaalbedrag`` subline: same invoice, different cents — trust payable."""
        c1 = AmountCandidate(Decimal("10052.41"), "total_label_payable", 100, "Te betalen", "incl")
        c2 = AmountCandidate(Decimal("8307.78"), "total_label_sum", 85, "Totaalbedrag", "incl")
        r = _select_amount([c1, c2])
        assert r.status == "confirmed"
        assert r.value == Decimal("10052.41")
        assert r.source == "TOTAL_LABEL_PAYABLE"


class TestPhase2PayableSelectionRanking:
    def test_payable_beats_subtotal_at_lower_value_with_margin(self):
        payable = AmountCandidate(
            Decimal("614.93"),
            "total_label_payable",
            95,
            "Te betalen EUR 614,93",
            "incl",
        )
        subtotal = AmountCandidate(
            Decimal("10000.00"),
            "total_label_sum",
            98,
            "Subtotaal EUR 10000,00",
            "incl",
        )
        assert _amount_payable_score(payable) - _amount_payable_score(subtotal) >= _PAYABLE_SCORE_MARGIN
        r = _select_amount([payable, subtotal])
        assert r.status == "confirmed"
        assert r.value == Decimal("614.93")

    def test_close_payable_scores_stay_ambiguous_or_tentative(self):
        a = AmountCandidate(
            Decimal("100.00"),
            "total_label_generic",
            90,
            "Totaal netto EUR 100,00",
            "incl",
        )
        b = AmountCandidate(
            Decimal("105.00"),
            "total_label_sum",
            90,
            "Totaal EUR 105,00",
            "incl",
        )
        gap = abs(_amount_payable_score(a) - _amount_payable_score(b))
        assert gap < _PAYABLE_SCORE_MARGIN
        r = _select_amount([a, b])
        assert r.status in ("ambiguous", "tentative")


class TestSelectAmountAmbiguous:
    """Multiple high-confidence candidates, no dominant → ambiguous."""

    def test_two_close_confidence(self):
        c1 = AmountCandidate(Decimal("605.92"), "total_label_payable", 100, "ctx", "incl")
        c2 = AmountCandidate(Decimal("500.00"), "total_label_invoice", 95, "ctx", "incl")
        r = _select_amount([c1, c2])
        assert r.status == "tentative"
        assert r.value == Decimal("500.00")
        assert r.confidence == 95

    def test_three_candidates_no_dominant(self):
        c1 = AmountCandidate(Decimal("605.92"), "total_label_payable", 95, "ctx", "incl")
        c2 = AmountCandidate(Decimal("500.00"), "total_label_invoice", 90, "ctx", "incl")
        c3 = AmountCandidate(Decimal("400.00"), "total_label_generic", 70, "ctx", "incl")
        r = _select_amount([c1, c2, c3])
        assert r.status == "tentative"
        assert r.value == Decimal("500.00")


class TestSelectAmountNoHighConfidence:
    """Only low-confidence candidates (all < 70) → ambiguous, never auto-selected."""

    def test_fallback_only(self):
        c = AmountCandidate(Decimal("100.50"), "fallback_last_token", 15, "ctx")
        r = _select_amount([c])
        assert r.status == "ambiguous"
        assert r.value is None
        assert r.confidence == 0
        assert r.source == "NO_HIGH_CONFIDENCE"

    def test_total_line_hint_only(self):
        c = AmountCandidate(Decimal("200.00"), "total_line_hint", 40, "ctx")
        r = _select_amount([c])
        assert r.status == "ambiguous"
        assert r.value is None
        assert r.confidence == 0
        assert r.source == "NO_HIGH_CONFIDENCE"

    def test_multiple_low_picks_highest(self):
        c1 = AmountCandidate(Decimal("200.00"), "total_line_hint", 40, "ctx")
        c2 = AmountCandidate(Decimal("100.50"), "fallback_last_token", 15, "ctx")
        r = _select_amount([c1, c2])
        assert r.status == "ambiguous"
        assert r.value is None
        assert r.confidence == 0
        assert r.source == "NO_HIGH_CONFIDENCE"


class TestSelectAmountTentative:
    """Parser ``ambiguous`` + minstens één incl ≥70 → ``tentative`` met voorkeursbedrag."""

    def test_incl_conflict_picks_highest_confidence_incl(self):
        c1 = AmountCandidate(Decimal("999.99"), "total_label_payable", 100, "ctx", "incl")
        c2 = AmountCandidate(Decimal("888.88"), "total_label_invoice", 95, "ctx", "incl")
        r = _select_amount([c1, c2])
        assert r.status == "tentative"
        assert r.value == Decimal("888.88")

    def test_only_excl_high_confidence_stays_ambiguous(self):
        c1 = AmountCandidate(Decimal("100.00"), "total_label_excl", 100, "ctx", "excl")
        r = _select_amount([c1])
        assert r.status == "ambiguous"
        assert r.value is None


# ---------------------------------------------------------------------------
# _extract_amount_candidates — representative text snippets
# ---------------------------------------------------------------------------

class TestExtractAmountCandidates:
    def test_te_betalen_same_line(self):
        cands = _extract_amount_candidates("Te betalen EUR 605,92")
        payable = [c for c in cands if c.source == "total_label_payable"]
        assert len(payable) >= 1
        assert payable[0].value == Decimal("605.92")
        assert payable[0].confidence >= 95

    def test_factuurbedrag_next_line(self):
        cands = _extract_amount_candidates("Factuurbedrag:\n605,92")
        invoice = [c for c in cands if c.source == "total_label_invoice"]
        assert len(invoice) >= 1
        assert invoice[0].value == Decimal("605.92")

    def test_totaal_generic(self):
        cands = _extract_amount_candidates("Totaal 999.999,99")
        generic = [c for c in cands if c.source == "total_label_generic"]
        assert len(generic) >= 1
        assert generic[0].value == Decimal("999999.99")

    def test_subtotaal_excl(self):
        cands = _extract_amount_candidates("Subtotaal EUR 100,00\nTotaal EUR 121,00")
        excl = [c for c in cands if c.source == "total_label_excl"]
        assert len(excl) >= 1
        assert excl[0].value == Decimal("100.00")

    def test_empty_text_no_candidates(self):
        cands = _extract_amount_candidates("")
        assert cands == []

    def test_fallback_last_token(self):
        cands = _extract_amount_candidates("Leverancier XYZ\nRef: 12345\n100,50")
        fb = [c for c in cands if c.source == "fallback_last_token"]
        assert len(fb) == 1
        assert fb[0].value == Decimal("100.50")
        assert fb[0].confidence < 70

    def test_conflicting_labels_both_collected(self):
        """When two high-priority labels yield different amounts, both appear."""
        text = "Te betalen 605,92\nFactuurbedrag 500,00"
        cands = _extract_amount_candidates(text)
        sources = {c.source for c in cands}
        assert "total_label_payable" in sources
        assert "total_label_invoice" in sources
        vals = {c.value for c in cands if c.confidence >= 70}
        assert len(vals) >= 2

    def test_totaalbedrag_eur_confirmed_high_confidence(self):
        """Explicit ``Totaalbedrag`` line must not be dropped because ``Netto`` appears as column header."""
        text = (
            "Artikel  Aantal  Netto EUR  BTW EUR  Totaal EUR\n"
            "foo  1  100,00  21,00  121,00\n"
            "Totaalbedrag EUR 152,53"
        )
        cands = _extract_amount_candidates(text)
        assert any(c.source == "total_label_sum" and c.value == Decimal("152.53") for c in cands)
        r = _select_amount(cands)
        assert r.status == "confirmed"
        assert r.value == Decimal("152.53")

    def test_totaal_bedrag_two_words_split_pdf_token(self):
        """Some PDFs split ``Totaal bedrag`` into two words — same priority as ``totaalbedrag``."""
        text = "Totaal bedrag EUR 88,12"
        cands = _extract_amount_candidates(text)
        assert any(c.source == "total_label_sum" and c.value == Decimal("88.12") for c in cands)

    def test_totaalbedrag_next_line_not_misclassified_excl_due_to_excl_substring(self):
        """``"excl" in line`` used to match inside ``FactEXCL…`` / unrelated tokens → false ``excl`` type."""
        text = "Totaalbedrag: FACTEXCL2024-99\n184,56\n"
        cands = _extract_amount_candidates(text)
        r = _select_amount(cands)
        assert r.status == "confirmed"
        assert r.value == Decimal("184.56")

    def test_totaal_spaced_factuur_bedrag_label(self):
        """Spaced / punctuated ``Totaal … factuur … bedrag`` from flattened PDF text."""
        text = "Totaal - factuur - bedrag:\n184,56\n"
        cands = _extract_amount_candidates(text)
        assert any(c.source == "total_label_sum" and c.confidence >= 70 for c in cands)
        r = _select_amount(cands)
        assert r.status == "confirmed"
        assert r.value == Decimal("184.56")

    def test_totaalfactuurbedrag_soft_hyphen_removed(self):
        text = "Totaal\u00adFactuur\u00adBedrag:\n184,56\n"
        cands = _extract_amount_candidates(text)
        assert any(c.source == "total_label_sum" for c in cands)

    def test_totaalfactuurbedrag_next_line_high_confidence(self):
        """Compound Dutch label ``Totaalfactuurbedrag`` (one line) + amount next line → not only 15% fallback."""
        text = "Totaalfactuurbedrag:\n184,56\n"
        cands = _extract_amount_candidates(text)
        lab = [c for c in cands if c.source == "total_label_sum"]
        assert len(lab) >= 1
        assert lab[0].confidence >= 70
        assert lab[0].value == Decimal("184.56")
        r = _select_amount(cands)
        assert r.status == "confirmed"
        assert r.value == Decimal("184.56")

    def test_totaal_bedrag_split_lines_upgrade_and_amount_not_fallback(self):
        """PDF splits ``Totaal`` / ``bedrag:`` / amount → strong ``total_label_sum`` wins; fallback may still list."""
        text = "Totaal\nbedrag:\n184,56\n"
        cands = _extract_amount_candidates(text)
        assert any(c.source == "total_label_sum" and c.confidence >= 70 for c in cands)
        r = _select_amount(cands)
        assert r.status == "confirmed"
        assert r.value == Decimal("184.56")

    def test_totaal_factuur_bedrag_scans_all_continuation_distances(self):
        """Do not stop after first numeric continuation: later lines can carry the payable total."""
        text = (
            "Totaal - factuur - bedrag:\n"
            "Binnen 30 dgn excl. BTW 152,53\n"
            "184,56\n"
        )
        cands = _extract_amount_candidates(text)
        sums = [c for c in cands if c.source == "total_label_sum"]
        by_val = {(c.value, c.type) for c in sums}
        assert (Decimal("152.53"), "incl") in by_val
        assert (Decimal("184.56"), "incl") in by_val

    def test_fused_table_line_netto_headers_with_totaal_eur(self):
        """Flattened table line: must not auto-confirm (netto column noise); amount after ``Totaal`` kept as hint."""
        text = "Omschrijving  Aantal  Netto  BTW  Totaal EUR 1.234,56"
        cands = _extract_amount_candidates(text)
        gen = [c for c in cands if c.source == "total_label_generic"]
        assert len(gen) >= 1
        assert gen[0].value == Decimal("1234.56")
        assert gen[0].confidence >= 70
        assert gen[0].type == "unknown"
        r = _select_amount(cands)
        assert r.status == "ambiguous"
        assert r.value is None

    def test_totaal_netto_adjacent_still_skipped_for_generic(self):
        """Explicit ``Totaal netto`` remains excluded from generic 70 (subtotal semantics)."""
        text = "Totaal netto EUR 100,00"
        cands = _extract_amount_candidates(text)
        assert not any(c.source == "total_label_generic" for c in cands)


class TestClassifyCandidateAmountType:
    def test_total_label_sum_defaults_incl(self):
        assert (
            _classify_candidate_amount_type(
                classification_line="Totaalbedrag EUR 152,53",
                source="total_label_sum",
            )
            == "incl"
        )

    def test_total_label_sum_with_netto_on_line_is_unknown(self):
        assert (
            _classify_candidate_amount_type(
                classification_line="Netto kolom Totaalbedrag EUR 152,53",
                source="total_label_sum",
            )
            == "unknown"
        )

    def test_total_label_sum_excl_btw_only_in_continuation_is_incl(self):
        """Payment terms after ``>>`` must not reclassify the labelled total as ``excl``."""
        assert (
            _classify_candidate_amount_type(
                classification_line="Totaal - factuur - bedrag: >> Binnen 30 dgn excl. BTW 152,53",
                source="total_label_sum",
            )
            == "incl"
        )

    def test_total_label_sum_payment_excl_on_same_line_as_anchor_is_incl(self):
        """Flattened PDF: total anchor + ``excl. btw`` payment snippet on one physical line → still payable incl."""
        assert (
            _classify_candidate_amount_type(
                classification_line="Totaal - factuur - bedrag: Binnen 30 dgn excl. BTW 152,53",
                source="total_label_sum",
            )
            == "incl"
        )

    def test_total_label_sum_bedrag_excl_snippet_after_strong_anchor_is_incl(self):
        """``bedrag excl`` payment wording after ``Totaal … factuur … bedrag`` must not force ``excl`` (Fischer-style)."""
        assert (
            _classify_candidate_amount_type(
                classification_line="Totaal factuur bedrag excl. BTW 152,53",
                source="total_label_sum",
            )
            == "incl"
        )

    def test_subtotaal_before_strong_sum_anchor_same_line_is_incl(self):
        """Flattened line: ``Subtotaal`` left of ``Totaal … bedrag`` must not mark the invoice total as ``excl``."""
        assert (
            _classify_candidate_amount_type(
                classification_line="Subtotaal excl. btw 8307,78 Totaal - factuur - bedrag: 152,53",
                source="total_label_sum",
            )
            == "incl"
        )

    def test_nettobedrag_after_strong_sum_anchor_same_line_is_incl(self):
        """``Nettobedrag`` after the total anchor is column noise on one PDF line (Fischer-style)."""
        assert (
            _classify_candidate_amount_type(
                classification_line="Totaal - factuur - bedrag: EUR 184,56 Nettobedrag EUR 152,53",
                source="total_label_sum",
            )
            == "incl"
        )

    def test_totaal_excl_before_strong_sum_anchor_same_line_is_incl(self):
        """``Totaal excl.`` payment wording left of the invoice-total anchor must not force ``excl`` (ASF Fischer)."""
        assert (
            _classify_candidate_amount_type(
                classification_line=(
                    "Totaal excl. btw 10 Totaal - factuur - bedrag: EUR 184,56 Nettobedrag EUR 152,53"
                ),
                source="total_label_sum",
            )
            == "incl"
        )

    def test_totaal_excl_only_line_without_anchor_stays_excl(self):
        assert (
            _classify_candidate_amount_type(
                classification_line="Totaal excl. btw 152,53",
                source="total_label_sum",
            )
            == "excl"
        )

    def test_invoice_line_with_excl_btw_is_unknown(self):
        assert (
            _classify_candidate_amount_type(
                classification_line="Factuurbedrag excl. btw 100,00",
                source="total_label_invoice",
            )
            == "unknown"
        )

    def test_invoice_clean_line_is_incl(self):
        assert (
            _classify_candidate_amount_type(
                classification_line="Factuurbedrag 121,00",
                source="total_label_invoice",
            )
            == "incl"
        )

    def test_table_total_column_btw_over_percentage_before_btw_is_vat(self):
        """Bitasco-style: ``21 % BTW over € … = € …`` must not become a second incl candidate."""
        ctx = (
            "Betalingscondities: 30 dagen netto Totaal EUR excl btw € 3.543,71 "
            ">> 21 % BTW over € 3.543,71 = € 744,18"
        )
        assert (
            _classify_candidate_amount_type(
                classification_line=ctx,
                source="table_total_column",
            )
            == "vat"
        )

    def test_table_total_column_btw_over_eur_is_vat(self):
        assert (
            _classify_candidate_amount_type(
                classification_line="Totaal excl >> BTW over € 1.000,00 = € 210,00",
                source="table_total_column",
            )
            == "vat"
        )


class TestInclFirstExtractAndSelect:
    def test_netto_row_plus_totaalbedrag_line_confirms_incl_not_net(self):
        text = (
            "Artikel  Aantal  Netto EUR  BTW EUR  Totaal EUR\n"
            "foo  1  100,00  21,00  121,00\n"
            "Totaalbedrag EUR 152,53"
        )
        cands = _extract_amount_candidates(text)
        r = _select_amount(cands)
        assert r.status == "confirmed"
        assert r.value == Decimal("152.53")

    def test_excl_subtotaal_and_te_betalen_confirms_incl(self):
        text = (
            "Subtotaal excl. btw 100,00\n"
            "BTW 21% 21,00\n"
            "Te betalen 121,00"
        )
        cands = _extract_amount_candidates(text)
        r = _select_amount(cands)
        assert r.status == "confirmed"
        assert r.value == Decimal("121.00")

    def test_factuurbedrag_excl_not_auto_confirmed(self):
        d = extract_invoice_data("Factuurbedrag excl. btw 100,00")
        ar = d["amount_result"]
        assert ar["status"] == "ambiguous"
        assert ar["value"] is None


# ---------------------------------------------------------------------------
# extract_invoice_data — amount_result integration
# ---------------------------------------------------------------------------

class TestExtractInvoiceDataAmountResult:
    def test_has_amount_result_key(self):
        d = extract_invoice_data("Te betalen 605,92")
        assert "amount_result" in d
        ar = d["amount_result"]
        assert ar["status"] == "confirmed"
        assert ar["value"] == "605.92"
        assert ar["source"] == "TOTAL_LABEL_PAYABLE"
        assert len(ar["candidates"]) >= 1
        for c in ar["candidates"]:
            assert "type" in c

    def test_empty_returns_ambiguous(self):
        d = extract_invoice_data("")
        ar = d["amount_result"]
        assert ar["status"] == "ambiguous"
        assert ar["value"] is None
        assert ar["candidates"] == []

    def test_legacy_amount_matches_selected(self):
        d = extract_invoice_data("Factuurbedrag: 121,00")
        ar = d["amount_result"]
        if ar["status"] == "confirmed":
            assert d["amount"] == float(ar["value"])
        elif ar["status"] in ("ambiguous", "failed"):
            assert d["amount"] is None

    def test_legacy_confidence_mapping(self):
        d = extract_invoice_data("Te betalen 605,92")
        assert d["amount_confidence"] == "high"

    def test_ambiguous_sets_legacy_none(self):
        d = extract_invoice_data("Te betalen 605,92\nFactuurbedrag 500,00")
        ar = d["amount_result"]
        if ar["status"] == "ambiguous":
            assert d["amount"] is None
            assert d["amount_confidence"] == "ambiguous"

    def test_exception_sets_failed(self, monkeypatch: pytest.MonkeyPatch):
        def boom(_text: str):
            raise RuntimeError("forced amount extraction failure")

        monkeypatch.setattr(pdf_parser, "_extract_amount_candidates", boom)
        d = extract_invoice_data("Te betalen 605,92")
        ar = d["amount_result"]
        assert ar["status"] == "failed"
        assert ar["value"] is None
        assert ar["source"] == "EXCEPTION"
        assert d["amount"] is None
        assert d["amount_confidence"] == "missing"


class TestExtractInvoiceDataOcrAmountIsolation:
    def test_ocr_text_never_degrades_confirmed_primary_amount(self):
        primary = "Te betalen 76,33"
        # OCR may contain unrelated totals (e.g. VAT line totals); must not affect primary decision.
        ocr = "Te betalen 21,00\nBTW 21% 21,00"
        d = extract_invoice_data(primary, ocr_text=ocr)
        ar = d["amount_result"]
        assert ar["status"] == "confirmed"
        assert ar["value"] == "76.33"

    def test_ocr_amount_can_override_only_when_primary_has_zero_candidates(self):
        primary = ""
        ocr = "Te betalen 10,00"
        d = extract_invoice_data(primary, ocr_text=ocr)
        ar = d["amount_result"]
        assert ar["status"] in ("confirmed", "tentative")
        assert ar["value"] == "10.00"

    def test_ocr_amount_does_not_override_when_primary_is_ambiguous(self):
        primary = "Factuurbedrag excl. btw 100,00"
        ocr = "Te betalen 121,00"
        d = extract_invoice_data(primary, ocr_text=ocr)
        ar = d["amount_result"]
        assert ar["status"] == "ambiguous"
        assert ar["value"] is None


# ---------------------------------------------------------------------------
# Payment engine — ambiguous blocks, low_confidence warns
# ---------------------------------------------------------------------------

def _base_invoice(**overrides):
    inv = {
        "supplier_name": "Test BV",
        "match_status": "confirmed",
        "amount": 121.0,
        "amount_excl_vat": 100.0,
        "amount_result": {
            "candidates": [],
            "value": "121.00",
            "confidence": 100,
            "source": "TOTAL_LABEL_PAYABLE",
            "status": "confirmed",
            "selected_amount": "121.00",
            "amount_confidence": 100,
            "amount_status": "confirmed",
        },
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


class TestEngineAmbiguousBlocked:
    def test_ambiguous_blocks_payment(self):
        inv = _base_invoice(
            amount=None,
            amount_result={
                "candidates": [
                    {"value": "605.92", "source": "total_label_payable", "confidence": 100, "context": "ctx"},
                    {"value": "500.00", "source": "total_label_invoice", "confidence": 95, "context": "ctx"},
                ],
                "value": None,
                "confidence": 0,
                "source": "CONFLICTING_HIGH_CONFIDENCE",
                "status": "ambiguous",
                "selected_amount": None,
                "amount_confidence": 0,
                "amount_status": "ambiguous",
            },
        )
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        reasons = [e["reason"] for e in errors]
        assert "amount_ambiguous" in reasons

    def test_ambiguous_not_exported_as_payment(self):
        inv = _base_invoice(
            amount=605.92,
            amount_result={
                "candidates": [],
                "value": None,
                "confidence": 0,
                "source": "CONFLICTING_HIGH_CONFIDENCE",
                "status": "ambiguous",
                "selected_amount": None,
                "amount_confidence": 0,
                "amount_status": "ambiguous",
            },
        )
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] == "amount_uncertain" for e in errors)
        assert not any(e["reason"] == "amount_ambiguous" for e in errors)

    def test_ambiguous_single_candidate_buckets_uncertain(self):
        inv = _base_invoice(
            amount=None,
            amount_result={
                "candidates": [
                    {
                        "value": "152.53",
                        "source": "total_line_hint",
                        "confidence": 40,
                        "context": "ctx",
                        "type": "unknown",
                    },
                ],
                "value": None,
                "confidence": 0,
                "source": "NO_HIGH_CONFIDENCE",
                "status": "ambiguous",
                "selected_amount": None,
                "amount_confidence": 0,
                "amount_status": "ambiguous",
            },
        )
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] == "amount_uncertain" for e in errors)
        assert not any(e["reason"] == "amount_ambiguous" for e in errors)


class TestEngineFailedBlocked:
    def test_failed_blocks_payment(self):
        inv = _base_invoice(
            amount=None,
            amount_result={
                "candidates": [
                    {"value": "121.00", "source": "fallback_last_token", "confidence": 15, "context": "ctx"},
                ],
                "value": None,
                "confidence": 0,
                "source": "EXCEPTION",
                "status": "failed",
                "selected_amount": None,
                "amount_confidence": 0,
                "amount_status": "failed",
            },
        )
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        reasons = [e["reason"] for e in errors]
        assert "amount_failed" in reasons

class TestEngineLowConfidenceWarning:
    def test_legacy_low_confidence_warns(self):
        inv = _base_invoice(amount_confidence="low")
        inv.pop("amount_result", None)
        payments, errors = calculate_payments([inv])
        assert len(payments) == 1
        assert not errors
        assert "amount_low_confidence" in (payments[0].get("warning") or "")

    def test_certain_no_warning(self):
        inv = _base_invoice(
            amount_result={
                "candidates": [],
                "value": "121.00",
                "confidence": 100,
                "source": "TOTAL_LABEL_PAYABLE",
                "status": "confirmed",
                "selected_amount": "121.00",
                "amount_confidence": 100,
                "amount_status": "confirmed",
            },
        )
        payments, errors = calculate_payments([inv])
        assert len(payments) == 1
        warn = payments[0].get("warning") or ""
        assert "amount_low_confidence" not in warn
        assert "amount_ambiguous" not in warn


class TestEngineLegacyFallback:
    """Dicts without amount_result still work via legacy amount_confidence field."""

    def test_legacy_low_confidence_warns(self):
        inv = _base_invoice(amount_confidence="low")
        inv.pop("amount_result", None)
        payments, _ = calculate_payments([inv])
        assert len(payments) == 1
        assert "amount_low_confidence" in (payments[0].get("warning") or "")

    def test_legacy_missing_amount_blocks(self):
        inv = _base_invoice(amount=None)
        inv.pop("amount_result", None)
        payments, errors = calculate_payments([inv])
        assert len(payments) == 0
        assert any(e["reason"] == "missing_amount" for e in errors)
