"""Batch regression matrix tests."""

from __future__ import annotations

from logic.batch_regression_matrix import (
    all_passed,
    run_regression_matrix,
    synthetic_no_credit_19,
    vte_credit_batch,
    wasco_credit_batch,
)
from logic.payment_engine import calculate_payments


def test_synthetic_19_invoices_matrix_passes():
    entries = run_regression_matrix(include_golden_singles=False)
    synthetic = next(e for e in entries if e.batch_id == "synthetic_19_invoices")
    assert synthetic.status == "PASS"
    assert synthetic.actual["legacy_rows"] == 19
    assert synthetic.actual["settlement_groups"] == 0


def test_wasco_and_vte_matrix_pass():
    entries = run_regression_matrix(include_golden_singles=False)
    by_id = {e.batch_id: e for e in entries}
    assert by_id["wasco_batch"].status == "PASS"
    assert by_id["vte_batch"].status == "PASS"


def test_regression_matrix_all_core_fixtures_pass():
    entries = run_regression_matrix(include_golden_singles=False)
    assert all_passed(entries)


def test_no_credit_batch_engine_isolation():
    result = calculate_payments(synthetic_no_credit_19())
    assert result.pipeline == "legacy"
    assert len(result.legacy_payments or []) == 19
    assert len(result.settlement_groups) == 0


def test_credit_batches_use_settlement_pipeline():
    assert calculate_payments(wasco_credit_batch()).pipeline == "settlement"
    assert calculate_payments(vte_credit_batch()).pipeline == "settlement"
