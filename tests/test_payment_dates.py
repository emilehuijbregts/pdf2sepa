"""Tests for logic/payment_dates.py."""

from __future__ import annotations

from datetime import date

from logic.payment_dates import (
    execution_date_for_due,
    execution_date_for_direct,
    format_date_nl_from_iso,
    is_weekend,
    parse_ui_date_to_iso,
)


def test_direct():
    s = date(2026, 4, 3)
    assert execution_date_for_direct(s) == "2026-04-03"


def test_due_after_session():
    session = date(2026, 4, 3)
    assert execution_date_for_due("2026-04-01", 14, session) == "2026-04-15"


def test_due_before_session_caps_to_session():
    session = date(2026, 4, 10)
    assert execution_date_for_due("2026-03-01", 7, session) == "2026-04-10"


def test_due_invalid_invoice():
    assert execution_date_for_due(None, 7, date.today()) is None
    assert execution_date_for_due("niet-datum", 7, date.today()) is None


def test_weekend_saturday():
    assert is_weekend(date(2026, 4, 4))  # Saturday


def test_weekday():
    assert not is_weekend(date(2026, 4, 3))  # Friday


def test_format_nl_from_iso():
    assert format_date_nl_from_iso("2026-04-09") == "09-04-2026"
    assert format_date_nl_from_iso("") == ""
    assert format_date_nl_from_iso(None) == ""
    assert format_date_nl_from_iso("niet") == ""


def test_parse_iso_then_nl_roundtrip():
    assert parse_ui_date_to_iso("2026-04-09") == "2026-04-09"
    assert format_date_nl_from_iso(parse_ui_date_to_iso("09-04-2026") or "") == "09-04-2026"


def test_parse_ui_accepts_slash_dot():
    assert parse_ui_date_to_iso("09/04/2026") == "2026-04-09"
    assert parse_ui_date_to_iso("09.04.2026") == "2026-04-09"


def test_parse_ui_invalid():
    assert parse_ui_date_to_iso("32-01-2026") is None
    assert parse_ui_date_to_iso("2026-13-01") is None
    assert parse_ui_date_to_iso("  ") is None


def test_parse_ui_whitespace_trimmed():
    assert parse_ui_date_to_iso("  2026-04-09  ") == "2026-04-09"
    assert parse_ui_date_to_iso(" 09-04-2026 ") == "2026-04-09"
