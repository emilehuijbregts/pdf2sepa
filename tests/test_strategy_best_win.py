"""Unit tests for best-win strategy selection."""

from __future__ import annotations

from parser.profile_strategy_engine import StrategyContext, run_strategies


def test_best_win_prefers_higher_confidence_spec():
    raw = (
        "Factuurnummer : 2025-001\n"
        "Factuurnummer : 2025-001\n"
        "Debiteurnummer : 99999\n"
    )
    ctx = StrategyContext(
        field_id="invoice_number",
        raw_text=raw,
        confirmed_value="2025-001",
        mode="learn",
    )
    result = run_strategies("invoice_number", ctx)
    assert result.strategy_used is not None
    assert result.value == "2025-001"
    valid = [a for a in result.all_attempted_strategies if a.status == "valid"]
    assert len(valid) >= 1
    winner = max(valid, key=lambda a: a.confidence)
    assert result.strategy_used == winner.strategy
    assert result.confidence == winner.confidence


def test_best_win_tiebreaker_uses_pipeline_order():
    raw = "Factuurnummer : ABC123\n"
    ctx = StrategyContext(
        field_id="invoice_number",
        raw_text=raw,
        confirmed_value="ABC123",
        mode="learn",
    )
    result = run_strategies("invoice_number", ctx)
    assert result.value == "ABC123"
    assert result.confidence > 0.0
    assert result.profile_spec is not None


def test_best_win_amount_with_payable_label():
    raw = "Totaal te betalen : 1.234,56\n"
    ctx = StrategyContext(
        field_id="amount",
        raw_text=raw,
        confirmed_value="1234.56",
        mode="learn",
    )
    result = run_strategies("amount", ctx)
    assert result.value == 1234.56
    assert result.strategy_used is not None
