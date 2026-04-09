"""Tests for logic/payment_dates.py."""

from __future__ import annotations

from datetime import date

from logic.payment_dates import execution_date_for_due, execution_date_for_direct, is_weekend


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
