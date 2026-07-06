"""Unit tests for generic credit document classification."""

from __future__ import annotations

from logic.credit_classifier import classify_credit_document


class TestCreditClassifierPositive:
    def test_creditnota_dutch(self):
        r = classify_credit_document("Dit is een Creditnota van leverancier X")
        assert r.is_credit is True
        assert r.confidence >= 50
        assert "keyword_strong" in r.signals

    def test_credit_note_english(self):
        r = classify_credit_document("Credit Note ref CN-001")
        assert r.is_credit is True

    def test_credit_factuur_title(self):
        r = classify_credit_document("CREDIT FACTUUR\nFactuurnr. 12345")
        assert r.is_credit is True
        assert "title_credit_factuur" in r.signals

    def test_credit_banner(self):
        r = classify_credit_document("Factuur 6230076 ***** CREDIT *****")
        assert r.is_credit is True
        assert "title_credit_banner" in r.signals

    def test_negative_total(self):
        r = classify_credit_document("Factuurbedrag EUR(Incl. BTW) -66,67")
        assert r.is_credit is True

    def test_storno_keyword(self):
        r = classify_credit_document("Storno Rechnung 2025-001")
        assert r.is_credit is True

    def test_pearlpaint_style_weak_credit(self):
        text = (
            "Pearlpaint\n"
            "Credit volgens afspraak wegens verkeerd ingevoerd artikel\n"
            "Factuurbedrag EUR -128,89\n"
        )
        r = classify_credit_document(text)
        assert r.is_credit is True

    def test_metadata_type_boost(self):
        r = classify_credit_document(
            "Factuur 12345",
            metadata={"type": "credit_note"},
        )
        assert r.is_credit is True


class TestCreditClassifierNegative:
    def test_normal_invoice(self):
        r = classify_credit_document("Factuur aan klant\nBedrag: 100,00")
        assert r.is_credit is False

    def test_creditor_false_positive(self):
        r = classify_credit_document("Payment to creditor account IBAN NL00...")
        assert r.is_credit is False

    def test_credit_transfer_false_positive(self):
        r = classify_credit_document("Please use credit transfer for payment")
        assert r.is_credit is False

    def test_loose_credit_without_context(self):
        r = classify_credit_document("We offer store credit for future purchases")
        assert r.is_credit is False

    def test_te_betalen_with_due_date_not_credit(self):
        """Due dates like 19-02-2026 must not trigger negative-amount signals."""
        text = (
            "S for Software\n"
            "Factuur\n"
            "Te betalen € 121,00 (voor 19-02-2026) Factuurdatum 05-02-2026\n"
        )
        r = classify_credit_document(text)
        assert r.is_credit is False
        assert "negative_total_label" not in r.signals
        assert "negative_amount_line" not in r.signals
