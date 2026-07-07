"""Tests for document type resolver and profile-fit logic."""

from __future__ import annotations

from logic.document_type_resolver import resolve_document_type, score_profile_fit


_INVOICE_TEXT = """\
Factuur
Factuurnummer : INV-1001
Debiteurnummer : CUST-42
Totaal 1551.22
"""

_CREDIT_TEXT = """\
Creditnota
nota Nr: SCM23-00472
Totaal 363.00
"""


def _invoice_profile() -> dict:
    return {
        "learned_from": "invoice.pdf",
        "amount": {
            "label": "Totaal",
            "strategy": "same_line_last_amount",
            "confirmed_value": "1551.22",
        },
        "invoice_number": {
            "label": "Factuurnummer : ",
            "strategy": "same_line_after_colon",
            "confirmed_value": "INV-1001",
        },
        "customer_number": {
            "label": "Debiteurnummer : ",
            "strategy": "same_line_after_colon",
            "confirmed_value": "CUST-42",
        },
    }


def _credit_profile() -> dict:
    return {
        "learned_from": "credit.pdf",
        "amount": {
            "label": "Totaal",
            "strategy": "same_line_last_amount",
            "confirmed_value": "363.00",
        },
        "credit_number": {
            "label": "nota Nr: ",
            "strategy": "same_line_after_colon",
            "confirmed_value": "SCM23-00472",
        },
    }


def test_score_profile_fit_invoice_full_match() -> None:
    score = score_profile_fit(_INVOICE_TEXT, _invoice_profile(), ("amount", "invoice_number", "customer_number"))
    assert score == 1.0


def test_score_profile_fit_credit_full_match() -> None:
    score = score_profile_fit(_CREDIT_TEXT, _credit_profile(), ("amount", "credit_number"))
    assert score == 1.0


def test_user_override_wins_over_classifier() -> None:
    inv = {
        "raw_text": _CREDIT_TEXT,
        "type": "credit_note",
        "extraction_profile": _invoice_profile(),
        "credit_profile": _credit_profile(),
    }
    resolution = resolve_document_type(inv, user_override="invoice")
    assert resolution.document_type == "invoice"
    assert resolution.source == "user_override"


def test_profile_fit_forces_invoice_when_classifier_says_credit() -> None:
    inv = {
        "raw_text": _INVOICE_TEXT,
        "type": "credit_note",
        "extraction_profile": _invoice_profile(),
        "credit_profile": _credit_profile(),
    }
    resolution = resolve_document_type(inv)
    assert resolution.document_type == "invoice"
    assert resolution.source == "profile_fit"


def test_profile_fit_forces_credit_when_classifier_says_invoice() -> None:
    inv = {
        "raw_text": _CREDIT_TEXT,
        "type": "invoice",
        "extraction_profile": _invoice_profile(),
        "credit_profile": _credit_profile(),
    }
    resolution = resolve_document_type(inv)
    assert resolution.document_type == "credit_note"
    assert resolution.source == "profile_fit"


def test_ambiguous_when_both_profiles_fit() -> None:
    shared_text = _INVOICE_TEXT
    inv = {
        "raw_text": shared_text,
        "type": "invoice",
        "extraction_profile": _invoice_profile(),
        "credit_profile": {
            "learned_from": "credit.pdf",
            "amount": _invoice_profile()["amount"],
            "credit_number": {
                "label": "Factuurnummer : ",
                "strategy": "same_line_after_colon",
                "confirmed_value": "INV-1001",
            },
        },
    }
    resolution = resolve_document_type(inv)
    assert resolution.source == "ambiguous"
    assert resolution.needs_review is True


def test_classifier_used_without_profiles() -> None:
    inv = {
        "raw_text": _CREDIT_TEXT,
        "type": "invoice",
    }
    resolution = resolve_document_type(inv)
    assert resolution.document_type == "credit_note"
    assert resolution.source == "classifier"
