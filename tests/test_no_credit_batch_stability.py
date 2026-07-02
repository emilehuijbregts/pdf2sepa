"""Regression: no-credit batches must use legacy 1:1 payment flow (HEAD 01550c0 parity)."""

from __future__ import annotations

from logic.batch_trace import assert_legacy_output_isolation, validate_no_credit_batch_invariants
from logic.payment_engine import batch_requires_settlement, calculate_payments
from logic.settlement_call_guard import allocation_edges_from_result, last_settlement_call_counts
from logic.shadow_mode import run_shadow_validation
from ui.settlement_table import engine_result_views, review_documents_as_error_buckets


def _invoice(i: int, **overrides):
    inv = {
        "supplier_name": f"Supplier {i % 6}",
        "match_status": "confirmed",
        "type": "invoice",
        "invoice_number": f"INV{i:04d}",
        "source_file": f"/tmp/inv{i}.pdf",
        "amount": 100.0,
        "iban": "NL20INGB0001234567",
        "invoice_date": "2026-01-15",
        "invoice_date_source": "parsed",
        "supplier_term_trusted": True,
        "supplier_payment_term_days_raw": 30,
    }
    inv.update(overrides)
    return inv


def test_batch_requires_settlement_false_for_invoices_only():
    invs = [_invoice(i) for i in range(19)]
    assert batch_requires_settlement(invs) is False


def test_nineteen_invoices_produce_nineteen_legacy_payments():
    invs = [_invoice(i) for i in range(19)]
    result = calculate_payments(invs)
    payments, errors = engine_result_views(result)

    assert result.pipeline == "legacy"
    assert result.legacy_payments is not None
    assert len(result.settlement_groups) == 0
    assert len(payments) == 19
    assert len(errors) == 0
    assert len(result.review_documents) == 0


def test_no_credit_batch_has_no_settlement_groups_or_edges():
    invs = [_invoice(i) for i in range(19)]
    result = calculate_payments(invs)
    assert len(result.settlement_groups) == 0
    for group in result.settlement_groups:
        assert not group.get("credit_allocation")


def test_accepted_filter_splits_payment_vs_review_rows():
    invs = [
        _invoice(i, match_status="confirmed" if i < 6 else "needs_review")
        for i in range(19)
    ]
    result = calculate_payments(invs)
    payments, _errors = engine_result_views(result)
    assert result.pipeline == "legacy"
    assert len(payments) == 6
    assert len(result.review_documents) == 13


def test_credit_batch_uses_settlement_pipeline():
    inv = _invoice(0)
    credit = _invoice(99, type="credit_note", invoice_number="CR-1", amount=50.0)
    result = calculate_payments([inv, credit])
    assert result.pipeline == "settlement"
    assert result.legacy_payments is None
    assert len(result.settlement_groups) >= 1


def test_validate_no_credit_invariants_helper():
    invs = [_invoice(i) for i in range(3)]
    result = calculate_payments(invs)
    assert len(result.settlement_groups) == 0
    validate_no_credit_batch_invariants(
        settlement_groups=0,
        allocation_edges=0,
        n_invoices=3,
        n_groups=0,
    )


def test_legacy_output_isolation():
    invs = [_invoice(i) for i in range(5)]
    result = calculate_payments(invs)
    assert_legacy_output_isolation(result)


def test_no_settlement_calls_on_legacy_production():
    invs = [_invoice(i) for i in range(5)]
    calculate_payments(invs)
    assert last_settlement_call_counts() == {
        "enrich_credit_documents": 0,
        "compute_settlement_groups": 0,
        "settlement_pipeline": 0,
    }


def test_shadow_validation_passes_for_nineteen_invoices():
    invs = [_invoice(i) for i in range(19)]
    result = calculate_payments(invs)
    report = run_shadow_validation(invs, result, log=False)
    assert report.status == "PASS"
    assert allocation_edges_from_result(result.settlement_groups) == 0
