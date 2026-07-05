"""Unit tests for profile strategy confidence scoring."""

from __future__ import annotations

from parser.profile_strategy_engine import (
    StrategyContext,
    strategy_confidence,
    split_lines,
)


def test_strategy_confidence_label_match_invoice():
    raw = "Factuurnummer : INV-12345\nTotaal 100,00"
    lines = split_lines(raw)
    ctx = StrategyContext(
        field_id="invoice_number",
        raw_text=raw,
        confirmed_value="INV-12345",
        mode="learn",
    )
    spec = {
        "label": "Factuurnummer : ",
        "strategy": "same_line_after_colon",
        "confirmed_value": "INV-12345",
    }
    conf, breakdown = strategy_confidence(
        ctx, spec, "INV-12345", lines, internal_strategy="token_matching_confirmed_value"
    )
    assert conf >= 0.7
    assert breakdown["label_match"] >= 0.12
    assert breakdown["format"] == 0.2


def test_strategy_confidence_iban_checksum_boost():
    raw = "IBAN NL91ABNA0417164300 betaling"
    lines = split_lines(raw)
    ctx = StrategyContext(
        field_id="iban",
        raw_text=raw,
        confirmed_value="NL91ABNA0417164300",
        mode="learn",
    )
    spec = {"strategy": "iban_full_text_scan", "confirmed_value": "NL91ABNA0417164300"}
    conf, breakdown = strategy_confidence(
        ctx, spec, "NL91ABNA0417164300", lines, internal_strategy="iban_full_text_scan"
    )
    assert breakdown["format"] == 0.2
    assert conf >= 0.5


def test_strategy_confidence_amount_excl_penalty():
    raw = "Netto goederenbedrag excl. BTW 100,00\nBTW 21% 21,00"
    lines = split_lines(raw)
    ctx = StrategyContext(
        field_id="amount",
        raw_text=raw,
        confirmed_value="100.00",
        mode="learn",
    )
    spec = {
        "label": "Netto goederenbedrag excl. BTW",
        "strategy": "same_line_last_amount",
        "confirmed_value": "100.00",
    }
    _conf, breakdown = strategy_confidence(
        ctx, spec, 100.0, lines, internal_strategy="token_matching_confirmed_amount"
    )
    assert breakdown["penalty"] <= -0.1


def test_strategy_confidence_fallback_capped():
    raw = "X12345 : waarde\n"
    lines = split_lines(raw)
    ctx = StrategyContext(
        field_id="customer_number",
        raw_text=raw,
        confirmed_value="waarde",
        mode="learn",
    )
    spec = {
        "label": "X12345",
        "strategy": "same_line_after_colon",
        "confirmed_value": "waarde",
    }
    conf, _breakdown = strategy_confidence(
        ctx,
        spec,
        "waarde",
        lines,
        internal_strategy="fallback_value_locate_minimal_label",
    )
    assert conf <= 0.55
