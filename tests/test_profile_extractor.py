"""Tests for parser/profile_extractor.py — profile learn, extract, validate."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from parser.profile_extractor import (
    STRATEGIES,
    extract_with_profile,
    validate_profile,
)
from parser.profile_learner import learn_profile_from_confirmation
from parser.pdf_parser import extract_text_strict

# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

TEXT_LAST_AMOUNT = "Subtotaal 269,22\nTotaal € 269.22 € 1551.22"
PROFILE_LAST_AMOUNT = {
    "learned_from": "test.pdf",
    "amount": {
        "label": "Totaal",
        "strategy": "same_line_last_amount",
        "confirmed_value": "1551.22",
    },
}

TEXT_FIRST_AMOUNT = "Totaal 100,00 250,00"
PROFILE_FIRST_AMOUNT = {
    "learned_from": "test.pdf",
    "amount": {
        "label": "Totaal",
        "strategy": "same_line_first_amount",
        "confirmed_value": "100.00",
    },
}

TEXT_AFTER_COLON = "Factuurnummer : 260789"
PROFILE_AFTER_COLON = {
    "learned_from": "test.pdf",
    "invoice_number": {
        "label": "Factuurnummer :",
        "strategy": "same_line_after_colon",
        "confirmed_value": "260789",
    },
}

TEXT_NEXT_LINE = "Klantnummer\n113073/17078"
PROFILE_NEXT_LINE = {
    "learned_from": "test.pdf",
    "customer_number": {
        "label": "Klantnummer",
        "strategy": "next_line_first_token",
        "confirmed_value": "113073/17078",
    },
}

TEXT_2BA = """2ba B.V.
Factuurnummer : 260789
Debiteurnummer : 113073/17078
Regels ...
Totaal € 269.22 € 1551.22
"""

CONFIRMED_2BA = {
    "amount": Decimal("1551.22"),
    "invoice_number": "260789",
    "customer_number": "113073/17078",
}

GOLDEN_2BA_PDF = (
    Path(__file__).resolve().parent
    / "golden_dataset"
    / "pdfs"
    / "2ba Fact-2BA-20260114-260789-Duister.pdf"
)


# ---------------------------------------------------------------------------
# extract_with_profile — per strategy
# ---------------------------------------------------------------------------

class TestExtractWithProfile:
    def test_same_line_last_amount(self):
        out = extract_with_profile(TEXT_LAST_AMOUNT, PROFILE_LAST_AMOUNT)
        assert out["amount"] == 1551.22
        assert out["invoice_number"] is None

    def test_same_line_first_amount(self):
        out = extract_with_profile(TEXT_FIRST_AMOUNT, PROFILE_FIRST_AMOUNT)
        assert out["amount"] == 100.0

    def test_same_line_after_colon(self):
        out = extract_with_profile(TEXT_AFTER_COLON, PROFILE_AFTER_COLON)
        assert out["invoice_number"] == "260789"

    def test_next_line_first_token(self):
        out = extract_with_profile(TEXT_NEXT_LINE, PROFILE_NEXT_LINE)
        assert out["customer_number"] == "113073/17078"

    def test_missing_label_returns_none_for_field(self):
        out = extract_with_profile("geen totaal hier", PROFILE_LAST_AMOUNT)
        assert out["amount"] is None
        assert "invoice_number" in out

    def test_always_returns_four_keys(self):
        out = extract_with_profile("", {})
        assert set(out.keys()) == {
            "amount",
            "invoice_number",
            "customer_number",
            "iban",
            "vat_number",
            "kvk_number",
            "invoice_date",
            "email_domain",
        }

    def test_totaal_label_skips_header_without_amount(self):
        """«Prijs totaal» in tabelkop mag niet vóór echte totalenregel winnen (Option Tape)."""
        text = (
            "Artikel Uw ref Omschrijving Prijs totaal\n"
            "regel zonder bedrag\n"
            "Totaal 305,36 EUR\n"
        )
        profile = {
            "learned_from": "option_tape.pdf",
            "amount": {
                "label": "Totaal",
                "strategy": "same_line_last_amount",
                "confirmed_value": "305.36",
            },
        }
        out = extract_with_profile(text, profile)
        assert out["amount"] == 305.36
        assert validate_profile(text, profile, {"amount": Decimal("305.36")})

    def test_same_line_first_iban(self):
        text = "IBAN NL20 INGB 0001 2345 67\n"
        profile = {
            "learned_from": "test.pdf",
            "iban": {
                "label": "IBAN",
                "strategy": "same_line_first_iban",
                "confirmed_value": "NL20INGB0001234567",
            },
        }
        out = extract_with_profile(text, profile)
        assert out["iban"] == "NL20INGB0001234567"
        assert validate_profile(text, profile, None)


# ---------------------------------------------------------------------------
# learn + extract + validate — 2BA-style
# ---------------------------------------------------------------------------

class TestLearnAndRoundtrip:
    def test_learn_2ba_style_profile(self):
        profile = learn_profile_from_confirmation(
            TEXT_2BA,
            CONFIRMED_2BA,
            "2ba Fact-2BA-20260114-260789-Duister.pdf",
        )
        assert profile is not None
        assert profile["learned_from"] == "2ba Fact-2BA-20260114-260789-Duister.pdf"
        assert profile["amount"]["strategy"] == "same_line_last_amount"
        assert profile["amount"]["confirmed_value"] == "1551.22"
        assert profile["invoice_number"]["strategy"] == "same_line_after_colon"
        assert profile["customer_number"]["strategy"] == "same_line_after_colon"

    def test_extract_after_learn_2ba(self):
        profile = learn_profile_from_confirmation(
            TEXT_2BA, CONFIRMED_2BA, "2ba.pdf"
        )
        out = extract_with_profile(TEXT_2BA, profile)
        assert out["amount"] == 1551.22
        assert out["invoice_number"] == "260789"
        assert out["customer_number"] == "113073/17078"

    def test_validate_profile_2ba(self):
        profile = learn_profile_from_confirmation(
            TEXT_2BA, CONFIRMED_2BA, "2ba.pdf"
        )
        assert validate_profile(TEXT_2BA, profile, CONFIRMED_2BA)

    def test_validate_uses_profile_confirmed_values(self):
        profile = learn_profile_from_confirmation(
            TEXT_2BA, CONFIRMED_2BA, "2ba.pdf"
        )
        assert validate_profile(TEXT_2BA, profile, None)

    def test_learn_empty_returns_none(self):
        assert learn_profile_from_confirmation(TEXT_2BA, {}, "x.pdf") is None

    def test_validate_fails_on_wrong_text(self):
        profile = learn_profile_from_confirmation(
            TEXT_2BA, CONFIRMED_2BA, "2ba.pdf"
        )
        assert not validate_profile("ander document", profile, CONFIRMED_2BA)

    def test_validate_only_checks_fields_in_profile(self):
        """validate_profile only checks fields present in the profile spec."""
        assert validate_profile(TEXT_LAST_AMOUNT, PROFILE_LAST_AMOUNT, None)
        invoice_only = {
            "learned_from": "test.pdf",
            "invoice_number": PROFILE_AFTER_COLON["invoice_number"],
        }
        assert validate_profile(TEXT_AFTER_COLON, invoice_only, None)
        assert not validate_profile(TEXT_LAST_AMOUNT, invoice_only, None)

    def test_validate_fails_when_amount_confirmed_value_wrong(self):
        profile = dict(PROFILE_LAST_AMOUNT)
        profile["amount"] = dict(profile["amount"])
        profile["amount"]["confirmed_value"] = "999.99"
        assert not validate_profile(TEXT_LAST_AMOUNT, profile, None)

    def test_learn_amount_garbled_stutter_pdf_line(self):
        """Echte Pearlpaint PDF-tekst: herhaalde letters, geen platte regex-labels."""
        text = (
            "Factuurnummer: 2610I000151\n"
            "Klantnummer: K1628\n"
            "BBBBeeeeddddrrrraaaagggg iiiinnnnccccllll.... BBBBTTTTWWWW 1.185,12\n"
        )
        confirmed = {
            "amount": Decimal("1185.12"),
            "invoice_number": "2610I000151",
            "customer_number": "K1628",
        }
        profile = learn_profile_from_confirmation(
            text,
            confirmed,
            "pearl.pdf",
            amount_context_line="BBBBeeeeddddrrrraaaagggg iiiinnnnccccllll.... BBBBTTTTWWWW 1.185,12",
        )
        assert profile is not None
        assert "amount" in profile
        assert "Bedrag" in profile["amount"]["label"]
        out = extract_with_profile(text, profile)
        assert out["amount"] == pytest.approx(1185.12, abs=0.01)
        assert validate_profile(text, profile, confirmed)

    def test_learn_amount_pearlpaint_btw_inclusive_on_netto_line(self):
        """Regel met Netto-kop én BTW-inclusief totaal (Pearlpaint-layout)."""
        text = (
            "Factuurnummer: 2610I000151\n"
            "Klantnummer: K1628\n"
            "Netto Totaal exclusief BTW BTW basis BTW 21% BTW & Bedrag inclusief BTW 1.210,00\n"
        )
        confirmed = {
            "amount": Decimal("1210.00"),
            "invoice_number": "2610I000151",
            "customer_number": "K1628",
        }
        profile = learn_profile_from_confirmation(text, confirmed, "pearl.pdf")
        assert profile is not None
        assert "amount" in profile
        out = extract_with_profile(text, profile)
        assert out["amount"] == pytest.approx(1210.00, abs=0.01)

    def test_learn_amount_pearlpaint_next_line_after_label(self):
        text = (
            "Factuurnummer: 2610I000151\n"
            "Klantnummer: K1628\n"
            "BTW & Bedrag inclusief BTW\n"
            "1.210,00\n"
        )
        confirmed = {"amount": Decimal("1210.00")}
        profile = learn_profile_from_confirmation(text, confirmed, "pearl.pdf")
        assert profile is not None
        assert profile.get("amount", {}).get("strategy") == "next_line_first_token"
        out = extract_with_profile(text, profile)
        assert out["amount"] == pytest.approx(1210.00, abs=0.01)

    def test_learn_amount_via_parser_context(self):
        text = (
            "Factuurnummer: 2610I000151\n"
            "Klantnummer: K1628\n"
            "Regels\n"
            "Totaal EUR 1.234,56\n"
        )
        confirmed = {
            "amount": Decimal("1234.56"),
            "invoice_number": "2610I000151",
            "customer_number": "K1628",
        }
        profile = learn_profile_from_confirmation(
            text,
            confirmed,
            "pearl.pdf",
            amount_context_line="Totaal EUR 1.234,56",
        )
        assert profile is not None
        assert "amount" in profile
        assert profile["amount"]["strategy"] in (
            "same_line_last_amount",
            "same_line_first_amount",
        )
        out = extract_with_profile(text, profile)
        assert out["amount"] == pytest.approx(1234.56, abs=0.01)


@pytest.mark.skipif(not GOLDEN_2BA_PDF.is_file(), reason="golden PDF missing")
class TestGolden2baPdf:
    def test_learn_extract_validate_real_pdf(self):
        raw = extract_text_strict(str(GOLDEN_2BA_PDF))
        confirmed = {
            "amount": Decimal("1551.22"),
            "invoice_number": "260789",
            "customer_number": "113073/17078",
        }
        profile = learn_profile_from_confirmation(
            raw, confirmed, GOLDEN_2BA_PDF.name
        )
        assert profile is not None
        out = extract_with_profile(raw, profile)
        assert out["amount"] == pytest.approx(1551.22, abs=0.01)
        assert out["invoice_number"] == "260789"
        assert out["customer_number"] == "113073/17078"
        assert validate_profile(raw, profile, confirmed)


class TestStrategiesConstant:
    def test_six_strategies(self):
        assert len(STRATEGIES) == 6
        assert "same_line_first_iban" in STRATEGIES
        assert "next_line_first_iban" in STRATEGIES
