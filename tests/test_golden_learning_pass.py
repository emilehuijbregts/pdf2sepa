"""Tests for Phase 4 golden dataset learning pass."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parser.golden_dataset_learning_pass import (
    BUNDLE_VERSION,
    CORE_FIELDS,
    FieldStrategyStats,
    build_engine_bundle,
    build_stats_document,
    compute_fragility_score,
    compute_golden_hash,
    compute_optimal_strategy_order,
    compute_amount_semantic_adjustments,
    run_golden_learning_pass,
    write_engine_bundle,
)
from parser.profile_strategy_engine import (
    STRATEGY_REGISTRY,
    AmbiguousEquivalenceError,
    ResolvedAttempt,
    StrategyContext,
    _enforce_evaluation_determinism,
    compute_value_fingerprints,
    get_engine_bundle,
    get_engine_bundle_version,
    get_semantic_scoring,
    get_strategy_pipeline,
    reload_strategy_engine_state,
    run_strategies,
    validate_field_spec,
)

APP_BASE = Path(__file__).resolve().parents[1]


def test_fragility_single_valid_strategy_is_fragile():
    trace = [
        {"strategy": "token_matching_confirmed_amount", "status": "valid", "confidence_breakdown": {}},
    ]
    score = compute_fragility_score(
        trace,
        winner="token_matching_confirmed_amount",
        winner_confidence=0.95,
        winner_breakdown={},
    )
    assert score == 1.0


def test_fragility_high_confidence_non_fragile_win():
    trace = [
        {"strategy": "token_matching_confirmed_value", "status": "valid", "confidence_breakdown": {"penalty": 0.0}},
        {"strategy": "generic_label_same_line_after_colon", "status": "valid", "confidence_breakdown": {"penalty": 0.0}},
    ]
    score = compute_fragility_score(
        trace,
        winner="token_matching_confirmed_value",
        winner_confidence=0.95,
        winner_breakdown={"penalty": 0.0},
    )
    assert score == 0.0


def test_fragility_fallback_winner():
    trace = [
        {"strategy": "amount_fallback_scan", "status": "valid", "confidence_breakdown": {"penalty": -0.1}},
        {"strategy": "token_matching_confirmed_amount", "status": "valid", "confidence_breakdown": {}},
    ]
    score = compute_fragility_score(
        trace,
        winner="amount_fallback_scan",
        winner_confidence=0.85,
        winner_breakdown={"penalty": -0.1},
    )
    assert score == 1.0


def test_optimal_order_demotes_high_fragility_to_fallback_tier():
    stats = [
        FieldStrategyStats("strong", 10, 8, 0.8, 0.9, 0.1, 0.1),
        FieldStrategyStats("fragile_fb", 10, 5, 0.5, 0.6, 0.85, 0.4),
        FieldStrategyStats("mid", 10, 6, 0.6, 0.85, 0.3, 0.2),
    ]
    order = compute_optimal_strategy_order(
        "amount",
        stats,
        registry_order=("strong", "fragile_fb", "mid"),
    )
    assert order.index("fragile_fb") > order.index("strong")
    assert order.index("fragile_fb") > order.index("mid")


def test_amount_semantic_trigger_on_high_field_fragility():
    results = [
        {
            "field": "amount",
            "status": "success",
            "strategy_used": "unlabeled_prefix_amount",
            "confidence": 0.7,
            "all_attempted_strategies": [],
        }
    ] * 5
    field_fragility = {"amount": 0.45}
    adj = compute_amount_semantic_adjustments(field_fragility, results)
    assert adj is not None
    assert adj["enabled"] is True
    assert "adjustments" in adj


def test_amount_semantic_not_triggered_when_stable():
    results = [
        {
            "field": "amount",
            "status": "success",
            "strategy_used": "token_matching_confirmed_amount",
            "confidence": 0.95,
            "all_attempted_strategies": [],
        }
    ] * 10
    field_fragility = {"amount": 0.1}
    adj = compute_amount_semantic_adjustments(field_fragility, results)
    assert adj is None


def test_bundle_atomicity_version_and_hash(tmp_path):
    report = run_golden_learning_pass(
        [
            {
                "field": "amount",
                "status": "success",
                "strategy_used": "token_matching_confirmed_amount",
                "confidence": 0.9,
                "all_attempted_strategies": [
                    {
                        "strategy": "token_matching_confirmed_amount",
                        "status": "valid",
                        "confidence": 0.9,
                        "confidence_breakdown": {"penalty": 0.0},
                    }
                ],
            }
        ],
        golden_hash="sha256:test",
    )
    bundle = build_engine_bundle(report)
    assert bundle["version"] == BUNDLE_VERSION
    assert bundle["golden_hash"] == "sha256:test"
    assert "patch" in bundle and "semantic_scoring" in bundle
    for field_id in CORE_FIELDS:
        assert field_id in bundle["semantic_scoring"]

    path = tmp_path / "bundle.json"
    write_engine_bundle(bundle, path)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["golden_hash"] == bundle["golden_hash"]
    assert loaded["patch"]["order"] == bundle["patch"]["order"]


def test_reload_strategy_engine_state_reads_bundle(tmp_path):
    bundle = {
        "version": 1,
        "patch": {"order": {"amount": list(reversed(STRATEGY_REGISTRY["amount"]))}},
        "semantic_scoring": {
            "amount": {"enabled": True, "adjustments": {"incl_btw_boost": 0.12}},
            "iban": {"enabled": False},
            "invoice_number": {"enabled": False},
            "customer_number": {"enabled": False},
        },
    }
    path = tmp_path / "strategy_engine_bundle.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")

    reload_strategy_engine_state(bundle_path=path, skip_bundle_validation=True)
    pipeline = get_strategy_pipeline("amount", evaluation_mode=False)
    assert pipeline[0] == STRATEGY_REGISTRY["amount"][-1]

    scoring = get_semantic_scoring("amount")
    assert scoring is not None
    assert scoring.get("enabled") is True


def test_evaluation_mode_skips_amount_from_context():
    ctx = StrategyContext(
        field_id="amount",
        raw_text="Totaal : 100,00\nContext line",
        confirmed_value="100.00",
        context_line="Context line",
        evaluation_mode=True,
    )
    result = run_strategies("amount", ctx)
    skipped = [
        a for a in result.all_attempted_strategies if a.strategy == "amount_from_context"
    ]
    assert skipped
    assert skipped[0].status == "skipped"
    assert skipped[0].reason == "evaluation_context_disabled"


def test_validate_field_spec_amount():
    raw = "Totaal : 100,00"
    ctx = StrategyContext(
        field_id="amount",
        raw_text=raw,
        confirmed_value="100.00",
        evaluation_mode=True,
    )
    result = run_strategies("amount", ctx)
    assert result.value is not None
    assert result.profile_spec is not None
    assert validate_field_spec(raw, "amount", result.profile_spec, "100.00") is True
    assert validate_field_spec(raw, "amount", result.profile_spec, "99.00") is False


def test_evaluation_freeze_ignores_bundle_order(tmp_path):
    bundle = {
        "version": 1,
        "patch": {
            "order": {
                "invoice_number": list(reversed(STRATEGY_REGISTRY["invoice_number"])),
            }
        },
        "semantic_scoring": {f: {"enabled": False} for f in CORE_FIELDS},
    }
    path = tmp_path / "strategy_engine_bundle.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    reload_strategy_engine_state(bundle_path=path, skip_bundle_validation=True)

    eval_pipeline = get_strategy_pipeline("invoice_number", evaluation_mode=True)
    runtime_pipeline = get_strategy_pipeline("invoice_number", evaluation_mode=False)
    assert eval_pipeline == STRATEGY_REGISTRY["invoice_number"]
    assert runtime_pipeline[0] == STRATEGY_REGISTRY["invoice_number"][-1]


def test_evaluation_determinism_same_winner_twice():
    raw = "Factuurnummer : ABC123\n"
    winners = []
    for _ in range(2):
        ctx = StrategyContext(
            field_id="invoice_number",
            raw_text=raw,
            confirmed_value="ABC123",
            mode="learn",
            evaluation_mode=True,
        )
        result = run_strategies("invoice_number", ctx)
        assert result.value == "ABC123"
        winners.append(result.strategy_used)
    assert winners[0] == winners[1]


def test_patch_completeness_all_registry_strategies():
    report = run_golden_learning_pass([])
    for field_id in CORE_FIELDS:
        order = report.recommended_order.get(field_id, [])
        registry = list(STRATEGY_REGISTRY[field_id])
        assert set(registry) <= set(order)


def test_stats_document_is_diagnostic_only():
    report = run_golden_learning_pass(
        [
            {
                "field": "iban",
                "status": "success",
                "strategy_used": "iban_full_text_scan",
                "confidence": 0.9,
                "all_attempted_strategies": [],
            }
        ],
        golden_hash="sha256:stats",
    )
    doc = build_stats_document(report)
    assert doc["source"] == "golden_learning_pass"
    assert "field_matrix" in doc
    assert "optimized_order" not in doc


def test_compute_golden_hash_stable():
    rows = [{"field": "amount", "status": "success", "pdf": "a.pdf"}]
    assert compute_golden_hash(rows) == compute_golden_hash(rows)


def test_bundle_atomic_write_no_partial_tmp(tmp_path):
    report = run_golden_learning_pass([], golden_hash="sha256:atomic")
    bundle = build_engine_bundle(report)
    path = tmp_path / "strategy_engine_bundle.json"
    write_engine_bundle(bundle, path)
    assert path.is_file()
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_enforce_evaluation_determinism_invariants():
    ctx = StrategyContext(
        field_id="amount",
        raw_text="Totaal : 100,00",
        confirmed_value="100.00",
        evaluation_mode=True,
    )
    _enforce_evaluation_determinism(ctx)


def test_get_engine_bundle_after_reload(tmp_path):
    bundle_data = {
        "version": 2,
        "patch": {"order": {}},
        "semantic_scoring": {"amount": {"enabled": False}},
    }
    path = tmp_path / "strategy_engine_bundle.json"
    path.write_text(json.dumps(bundle_data), encoding="utf-8")
    reload_strategy_engine_state(bundle_path=path, skip_bundle_validation=True)
    loaded = get_engine_bundle()
    assert loaded.get("version") == 2
    assert get_engine_bundle_version() == 2


def test_field_scoring_constants_respects_bundle(tmp_path):
    bundle = {
        "version": 1,
        "patch": {"order": {}},
        "semantic_scoring": {
            "amount": {
                "enabled": True,
                "adjustments": {"incl_btw_boost": 0.15},
            },
            "iban": {"enabled": False},
            "invoice_number": {"enabled": False},
            "customer_number": {"enabled": False},
        },
    }
    path = tmp_path / "strategy_engine_bundle.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    reload_strategy_engine_state(bundle_path=path, skip_bundle_validation=True)
    scoring = get_semantic_scoring("amount")
    assert scoring is not None
    assert scoring.get("enabled") is True
    assert float(scoring["adjustments"]["incl_btw_boost"]) == 0.15


def test_ensure_bundle_loaded_atomic_caches(tmp_path):
    bundle = {
        "version": 1,
        "golden_hash": "sha256:abc",
        "patch": {"order": {"amount": list(STRATEGY_REGISTRY["amount"])}},
        "semantic_scoring": {f: {"enabled": False} for f in CORE_FIELDS},
    }
    path = tmp_path / "strategy_engine_bundle.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    reload_strategy_engine_state(bundle_path=path, skip_bundle_validation=True)
    b1 = get_engine_bundle()
    b2 = get_engine_bundle()
    assert b1.get("golden_hash") == b2.get("golden_hash") == "sha256:abc"


def test_compute_value_fingerprints_distinguishes_raw_types():
    raw_fp_a, value_key_a, identity_a = compute_value_fingerprints("amount", 100.0)
    raw_fp_b, value_key_b, identity_b = compute_value_fingerprints("amount", "100.00")
    assert value_key_a == value_key_b
    assert raw_fp_a != raw_fp_b
    assert identity_a != identity_b


def test_ambiguous_equivalence_raises():
    from parser.profile_strategy_engine import assert_no_ambiguous_equivalence

    a = ResolvedAttempt(
        value=100.0,
        raw_fingerprint="float:Decimal('100.00')",
        value_key="100.00",
        identity_key="id_a",
        spec={"strategy": "s1"},
        strategy="s1",
        confidence=0.9,
    )
    b = ResolvedAttempt(
        value="100.00",
        raw_fingerprint="str:Decimal('100.00')",
        value_key="100.00",
        identity_key="id_b",
        spec={"strategy": "s2"},
        strategy="s2",
        confidence=0.9,
    )
    with pytest.raises(AmbiguousEquivalenceError):
        assert_no_ambiguous_equivalence([a, b], a)


def test_different_value_keys_at_same_confidence_use_identity_key():
    from parser.profile_strategy_engine import _select_winner

    ctx = StrategyContext(
        field_id="amount",
        raw_text="Totaal : 100,00",
        confirmed_value="100.00",
        evaluation_mode=True,
    )
    a = ResolvedAttempt(
        value=100.0,
        raw_fingerprint="float:Decimal('100.00')",
        value_key="100.00",
        identity_key="aaa",
        spec={"strategy": "s1"},
        strategy="s1",
        confidence=0.9,
    )
    b = ResolvedAttempt(
        value=99.0,
        raw_fingerprint="float:Decimal('99.00')",
        value_key="99.00",
        identity_key="bbb",
        spec={"strategy": "s2"},
        strategy="s2",
        confidence=0.9,
    )
    winner = _select_winner([a, b], ctx, {"s1": 0, "s2": 1})
    assert winner.identity_key == "bbb"


def test_execution_state_snapshot_stable_across_noop():
    from parser.strategy_statelessness_audit import (
        assert_no_mutation,
        snapshot_execution_state,
    )

    before = snapshot_execution_state()
    after = snapshot_execution_state()
    assert_no_mutation(before, after, context="noop")


def test_reload_restores_state_on_invalid_bundle(tmp_path):
    good = {
        "version": 1,
        "patch": {"order": {"amount": list(STRATEGY_REGISTRY["amount"])}},
        "semantic_scoring": {f: {"enabled": False} for f in CORE_FIELDS},
    }
    path = tmp_path / "strategy_engine_bundle.json"
    path.write_text(json.dumps(good), encoding="utf-8")
    reload_strategy_engine_state(bundle_path=path, skip_bundle_validation=True)
    assert get_engine_bundle_version() == 1

    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{not json", encoding="utf-8")
    reload_strategy_engine_state(bundle_path=bad_path, skip_bundle_validation=True)
    assert get_engine_bundle_version() == 1
