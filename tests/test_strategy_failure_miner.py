"""Unit tests for strategy failure pattern miner."""

from __future__ import annotations

from parser.strategy_failure_miner import (
    PATTERN_LABEL_MISMATCH,
    PATTERN_TOKEN_AMBIGUITY,
    aggregate_strategy_stats,
    mine_failure_patterns,
)


def test_mine_token_ambiguity_fragile_win():
    rows = [
        {
            "pdf": "test.pdf",
            "field": "amount",
            "status": "success",
            "strategy_used": "amount_fallback_scan",
            "confidence": 0.72,
            "all_attempted_strategies": [
                {
                    "strategy": "amount_fallback_scan",
                    "status": "valid",
                    "confidence": 0.72,
                    "confidence_breakdown": {"penalty": -0.12, "uniqueness": 0.2},
                }
            ],
        }
    ]
    patterns = mine_failure_patterns(rows)
    types = {p.pattern_type for p in patterns}
    assert PATTERN_TOKEN_AMBIGUITY in types


def test_mine_label_mismatch_on_validate_fail():
    rows = [
        {
            "pdf": "besli.pdf",
            "field": "invoice_number",
            "status": "failure",
            "all_attempted_strategies": [
                {
                    "strategy": "generic_label_same_line_after_colon",
                    "status": "invalid",
                    "reason": "validate_profile_failed",
                }
            ],
            "validation_trace": ["generic_label_same_line_after_colon:validate_failed"],
        }
    ]
    patterns = mine_failure_patterns(rows)
    types = {p.pattern_type for p in patterns}
    assert PATTERN_LABEL_MISMATCH in types


def test_aggregate_strategy_stats():
    rows = [
        {
            "field": "iban",
            "status": "success",
            "strategy_used": "iban_full_text_scan",
            "all_attempted_strategies": [
                {
                    "strategy": "iban_full_text_scan",
                    "status": "valid",
                    "confidence_breakdown": {"penalty": 0.0},
                }
            ],
        }
    ]
    stats = aggregate_strategy_stats(rows)
    assert stats["iban"]["strategies"]["iban_full_text_scan"]["wins"] == 1
    assert stats["iban"]["strategies"]["iban_full_text_scan"]["attempts"] == 1
