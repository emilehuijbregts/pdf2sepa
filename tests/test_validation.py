"""Tests for logic/validation.py helpers."""

from __future__ import annotations

from logic.validation import mask_iban_for_log


def test_mask_iban_empty() -> None:
    assert mask_iban_for_log(None) == "<none>"
    assert mask_iban_for_log("") == "<none>"


def test_mask_iban_typical() -> None:
    assert mask_iban_for_log("NL20 INGB 0001 2345 67") == "NL…4567"


def test_mask_iban_short() -> None:
    assert mask_iban_for_log("NL91XX") == "NL…"
