"""Tests for ui.field_review registry and formatters."""

from __future__ import annotations

from ui.field_review import (
    CUSTOMER_ABSENT_PICK_SOURCE,
    CUSTOMER_ABSENT_STATE,
    FIELD_REVIEW_SPECS,
    REVIEW_FIELD_IDS,
    format_amount_candidate_menu_label,
    format_iban_candidate_menu_label,
    format_ident_candidate_menu_label,
    is_customer_absent_pick,
    make_customer_absent_pick_candidate,
)


def test_review_field_ids() -> None:
    assert REVIEW_FIELD_IDS == ("amount", "invoice_number", "customer_number", "iban")
    for fid in REVIEW_FIELD_IDS:
        assert fid in FIELD_REVIEW_SPECS


def test_format_iban_candidate_menu_label() -> None:
    label = format_iban_candidate_menu_label(
        {
            "value": "NL20INGB0001234567",
            "source": "pdf_text",
            "confidence": 88,
        }
    )
    assert "NL20INGB0001234567" in label
    assert "PDF-tekst" in label
    assert "88%" in label


def test_format_ident_candidate_menu_label() -> None:
    label = format_ident_candidate_menu_label(
        {
            "value": "26FC000498",
            "label": "Factuur",
            "confidence": 83,
        }
    )
    assert "26FC000498" in label
    assert "Factuur" in label
    assert "83%" in label


def test_customer_absent_pick_candidate() -> None:
    cand = make_customer_absent_pick_candidate()
    assert is_customer_absent_pick(cand)
    assert cand["source"] == CUSTOMER_ABSENT_PICK_SOURCE
    assert not str(cand.get("value") or "").strip()


def test_apply_customer_absent_to_invoice() -> None:
    from parser.resolved_field_apply import apply_resolved_field_result

    inv: dict = {
        "customer_number": "99999",
        "customer_number_result": {
            "value": "99999",
            "candidates": [{"value": "99999", "source": "label", "confidence": 90}],
            "status": "confirmed",
        },
    }
    resolved = {
        "value": None,
        "selected_value": None,
        "absence_state": CUSTOMER_ABSENT_STATE,
        "source": CUSTOMER_ABSENT_PICK_SOURCE,
        "status": "confirmed",
        "confidence": 100,
        "user_selected": True,
        "candidates": inv["customer_number_result"]["candidates"],
        "resolver_finalized": True,
    }
    apply_resolved_field_result(inv, "customer_number", resolved)
    assert "customer_number" not in inv
    assert inv["customer_number_result"]["absence_state"] == CUSTOMER_ABSENT_STATE


def test_format_amount_candidate_menu_label() -> None:
    label = format_amount_candidate_menu_label(
        {
            "value": "100.00",
            "source": "total_label_payable",
            "confidence": 80,
            "type": "incl",
        },
        format_amount_nl=lambda v: f"€ {v}",
    )
    assert "100.00" in label
    assert "Totaal te betalen" in label
    assert "80%" in label
