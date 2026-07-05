"""Unit tests for parser/strategy_regression_guard.py."""

from __future__ import annotations

import pytest

from parser.strategy_regression_guard import (
    BundleCompatibilityError,
    RegressionBaseline,
    RegressionSnapshot,
    StrategyRegressionError,
    assert_no_regression,
    build_bundle_fingerprint,
    build_regression_baseline,
    compare_snapshots,
    validate_bundle_compatibility,
)


def _snap(
    pdf: str,
    field: str,
    *,
    winner: str = "token_matching_confirmed_value",
    candidate: str = "123",
    confidence: float = 0.9,
    breakdown: dict[str, float] | None = None,
) -> RegressionSnapshot:
    return RegressionSnapshot(
        pdf_id=pdf,
        field_id=field,
        candidate=candidate,
        winner=winner,
        confidence=confidence,
        confidence_breakdown=breakdown or {"label_match": 0.3, "penalty": 0.0},
    )


def _valid_bundle() -> dict:
    return {
        "version": 1,
        "golden_hash": "sha256:abc",
        "patch": {
            "order": {
                "amount": [
                    "token_matching_confirmed_amount",
                    "amount_label_next_line",
                    "amount_from_context",
                    "amount_fallback_scan",
                    "derived_excl_plus_vat",
                    "unlabeled_prefix_amount",
                ],
                "invoice_number": [
                    "factuur_inline_pagina",
                    "token_matching_confirmed_value",
                    "same_line_value_after_label",
                    "generic_label_same_line_after_colon",
                    "label_then_next_line",
                    "last_token_on_line",
                    "slash_compound_split",
                    "ocr_tag_extraction",
                    "fallback_value_locate_minimal_label",
                ],
                "customer_number": [
                    "token_matching_confirmed_value",
                    "same_line_value_after_label",
                    "generic_label_same_line_after_colon",
                    "label_then_next_line",
                    "last_token_on_line",
                    "slash_compound_split",
                    "ocr_tag_extraction",
                    "fallback_value_locate_minimal_label",
                ],
                "iban": [
                    "iban_full_text_scan",
                    "iban_label_same_line",
                    "iban_label_next_line",
                    "iban_scan_with_checksum_filter",
                ],
            }
        },
        "semantic_scoring": {
            "amount": {
                "enabled": True,
                "adjustments": {
                    "incl_btw_boost": 0.12,
                    "payable_label_boost": 0.12,
                    "totaal_anchor_boost": 0.1,
                    "vat_line_penalty": -0.15,
                    "excl_without_payable_penalty": -0.2,
                },
            },
            "invoice_number": {"enabled": False},
            "customer_number": {"enabled": False},
            "iban": {"enabled": False},
        },
    }


def test_compare_snapshots_no_drift():
    before = [_snap("a.pdf", "amount")]
    after = [_snap("a.pdf", "amount")]
    assert compare_snapshots(before, after) == []


def test_compare_snapshots_winner_drift():
    before = [_snap("a.pdf", "amount", winner="s1")]
    after = [_snap("a.pdf", "amount", winner="s2")]
    diffs = compare_snapshots(before, after)
    assert any(d.drift_type == "winner" for d in diffs)


def test_compare_snapshots_candidate_drift():
    before = [_snap("a.pdf", "amount", candidate="1.00")]
    after = [_snap("a.pdf", "amount", candidate="2.00")]
    diffs = compare_snapshots(before, after)
    assert any(d.drift_type == "candidate" for d in diffs)


def test_compare_snapshots_confidence_drift():
    before = [_snap("a.pdf", "amount", confidence=0.90)]
    after = [_snap("a.pdf", "amount", confidence=0.85)]
    diffs = compare_snapshots(before, after)
    assert any(d.drift_type == "confidence" for d in diffs)


def test_compare_snapshots_breakdown_drift():
    before = [_snap("a.pdf", "amount", breakdown={"a": 1.0})]
    after = [_snap("a.pdf", "amount", breakdown={"b": 1.0})]
    diffs = compare_snapshots(before, after)
    assert any(d.drift_type == "breakdown" for d in diffs)


def test_compare_snapshots_missing_extra():
    before = [_snap("a.pdf", "amount")]
    after = [_snap("b.pdf", "amount")]
    diffs = compare_snapshots(before, after)
    types = {d.drift_type for d in diffs}
    assert "missing" in types
    assert "extra" in types


def test_assert_no_regression_raises():
    before = [_snap("a.pdf", "amount", winner="s1")]
    after = [_snap("a.pdf", "amount", winner="s2")]
    with pytest.raises(StrategyRegressionError, match="Strategy regression detected"):
        assert_no_regression(before, after)


def test_build_bundle_fingerprint_stable():
    bundle = _valid_bundle()
    assert build_bundle_fingerprint(bundle) == build_bundle_fingerprint(bundle)


def test_validate_bundle_compatibility_valid():
    validate_bundle_compatibility(_valid_bundle())


def test_validate_bundle_compatibility_unknown_strategy():
    bundle = _valid_bundle()
    bundle["patch"]["order"]["amount"][0] = "nonexistent_strategy"
    with pytest.raises(BundleCompatibilityError, match="unknown strategies"):
        validate_bundle_compatibility(bundle)


def test_validate_bundle_compatibility_missing_registry_strategy():
    bundle = _valid_bundle()
    bundle["patch"]["order"]["amount"] = bundle["patch"]["order"]["amount"][:-1]
    with pytest.raises(BundleCompatibilityError, match="missing registry strategies"):
        validate_bundle_compatibility(bundle)


def test_validate_bundle_compatibility_golden_hash_mismatch():
    bundle = _valid_bundle()
    baseline = RegressionBaseline(
        version=1,
        generated_at="t",
        golden_hash="sha256:expected",
        bundle_fingerprint=build_bundle_fingerprint(bundle),
        snapshots=[],
        capture_import_graph_audit_passed=True,
    )
    with pytest.raises(BundleCompatibilityError, match="golden_hash mismatch"):
        validate_bundle_compatibility(bundle, baseline=baseline)


def test_build_regression_baseline_from_results():
    results = [
        {
            "pdf": "x.pdf",
            "field": "amount",
            "status": "success",
            "actual": "10.00",
            "strategy_used": "token_matching_confirmed_amount",
            "confidence": 0.88,
            "confidence_breakdown": {"penalty": 0.0},
        }
    ]
    bundle = _valid_bundle()
    baseline = build_regression_baseline(results, golden_hash="sha256:x", bundle=bundle)
    assert len(baseline.snapshots) == 1
    assert baseline.snapshots[0].candidate == "10.00"


def test_validate_bundle_compatibility_rejects_missing_import_audit():
    bundle = _valid_bundle()
    baseline = RegressionBaseline(
        version=1,
        generated_at="t",
        golden_hash="sha256:abc",
        bundle_fingerprint=build_bundle_fingerprint(bundle),
        snapshots=[],
        capture_import_graph_audit_passed=None,
    )
    with pytest.raises(BundleCompatibilityError, match="capture_import_graph_audit_passed"):
        validate_bundle_compatibility(bundle, baseline=baseline)
