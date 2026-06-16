"""Tests for customer_number result-first authority helpers."""

from __future__ import annotations

from parser.supplier_db import (
    CUSTOMER_ABSENT_STATE,
    customer_number_authoritative_value,
    customer_number_is_absent_or_none,
)
from ui.field_review import CUSTOMER_ABSENT_PICK_SOURCE


def test_absent_result_ignores_stale_scalar() -> None:
    inv = {
        "customer_number": "75187760",
        "customer_number_result": {
            "value": None,
            "selected_value": None,
            "absence_state": CUSTOMER_ABSENT_STATE,
            "source": CUSTOMER_ABSENT_PICK_SOURCE,
            "status": "confirmed",
            "user_selected": True,
        },
    }
    assert customer_number_is_absent_or_none(inv)
    assert customer_number_authoritative_value(inv) is None


def test_result_value_wins_over_stale_scalar() -> None:
    inv = {
        "customer_number": "STALE",
        "customer_number_result": {
            "value": "30146",
            "selected_value": "30146",
            "status": "confirmed",
            "source": "label",
        },
    }
    assert not customer_number_is_absent_or_none(inv)
    assert customer_number_authoritative_value(inv) == "30146"


def test_scalar_fallback_when_no_result_dict() -> None:
    inv = {"customer_number": "4242"}
    assert customer_number_authoritative_value(inv) == "4242"
    assert customer_number_authoritative_value({}, scalar_fallback="999") == "999"


def test_profile_none_without_result() -> None:
    inv = {"extraction_profile": {"customer_number_mode": "NONE"}}
    assert customer_number_is_absent_or_none(inv)
    assert customer_number_authoritative_value(inv) is None


def test_pdf_customer_number_not_used_for_display() -> None:
    inv = {
        "pdf_customer_number": "75187760",
        "customer_number_result": {
            "value": None,
            "absence_state": CUSTOMER_ABSENT_STATE,
            "source": CUSTOMER_ABSENT_PICK_SOURCE,
            "status": "confirmed",
            "user_selected": True,
        },
    }
    assert customer_number_authoritative_value(inv) is None
