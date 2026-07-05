"""
Golden dataset learning pass — Phase 4 post-processing intelligence layer.

Analyzes golden strategy traces to produce fragility-aware reorder recommendations
and an atomic deployment bundle. Does not mutate the strategy engine at runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from parser.field_model import FieldId
from parser.profile_strategy_engine import FRAGILE_INTERNAL_STRATEGIES, STRATEGY_REGISTRY
from parser.strategy_failure_miner import aggregate_strategy_stats, mine_failure_patterns

BUNDLE_VERSION = 1
BUNDLE_SOURCE = "golden_learning_pass"

CORE_FIELDS: tuple[FieldId, ...] = (
    "amount",
    "invoice_number",
    "customer_number",
    "iban",
)

AMOUNT_FALLBACK_STRATEGIES = frozenset(
    {
        "amount_fallback_scan",
        "unlabeled_prefix_amount",
        "amount_from_context",
    }
)

FRAGILITY_DEMOTION_THRESHOLD = 0.7
AMOUNT_SEMANTIC_TRIGGER_FALLBACK = 0.40
AMOUNT_SEMANTIC_TRIGGER_FRAGILITY = 0.40

DEFAULT_AMOUNT_ADJUSTMENTS = {
    "incl_btw_boost": 0.12,
    "payable_label_boost": 0.12,
    "totaal_anchor_boost": 0.10,
    "vat_line_penalty": -0.15,
    "excl_without_payable_penalty": -0.20,
}

APP_BASE = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE_PATH = APP_BASE / "data" / "strategy_engine_bundle.json"
DEFAULT_STATS_PATH = APP_BASE / "data" / "strategy_order_stats.json"
DEFAULT_REPORT_PATH = APP_BASE / "reports" / "golden_learning_report.json"


@dataclass
class FieldStrategyStats:
    strategy_name: str
    attempts: int
    wins: int
    win_rate: float
    avg_confidence: float
    avg_fragility: float
    fallback_dependency_rate: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GoldenLearningReport:
    field_fragility: dict[str, float]
    top_fragile_strategies: list[dict[str, Any]]
    strategy_matrix: dict[str, list[FieldStrategyStats]]
    recommended_demotions: list[dict[str, Any]]
    recommended_order: dict[str, list[str]]
    amount_failure_clusters: list[dict[str, Any]]
    amount_semantic_adjustments: dict[str, Any] | None
    baseline_metrics: dict[str, dict[str, Any]]
    bundle_path: str
    bundle_version: int
    golden_hash: str
    stats_path: str
    failure_patterns: list[dict[str, Any]] = field(default_factory=list)


def compute_golden_hash(golden_results: list[dict[str, Any]]) -> str:
    payload = json.dumps(golden_results, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def compute_fragility_score(
    strategy_trace: list[dict[str, Any]],
    *,
    winner: str,
    winner_confidence: float,
    winner_breakdown: dict[str, float],
) -> float:
    """Composite fragility 0.0 (stable) – 1.0 (extremely fragile)."""
    valid_count = sum(1 for a in strategy_trace if a.get("status") == "valid")
    components = [
        1.0 if float(winner_confidence or 0.0) < 0.80 else 0.0,
        1.0 if valid_count == 1 else 0.0,
        1.0 if str(winner or "") in FRAGILE_INTERNAL_STRATEGIES else 0.0,
        1.0 if float(winner_breakdown.get("penalty") or 0.0) <= -0.15 else 0.0,
    ]
    return max(components)


def _winner_breakdown(row: dict[str, Any]) -> dict[str, float]:
    winner = str(row.get("strategy_used") or "")
    for attempt in reversed(row.get("all_attempted_strategies") or []):
        if attempt.get("strategy") == winner and attempt.get("status") == "valid":
            return dict(attempt.get("confidence_breakdown") or {})
    return dict(row.get("confidence_breakdown") or {})


def _field_baseline_metrics(results: list[dict[str, Any]], field_id: str) -> dict[str, Any]:
    successes = [r for r in results if r.get("field") == field_id and r.get("status") == "success"]
    evaluable = [
        r for r in results if r.get("field") == field_id and r.get("status") in ("success", "failure")
    ]
    fragile_wins = sum(1 for r in successes if float(r.get("confidence") or 0.0) <= 0.75)
    stable = sum(
        1
        for r in successes
        if float(r.get("confidence") or 0.0) >= 0.8
        and str(r.get("strategy_used") or "") not in FRAGILE_INTERNAL_STRATEGIES
    )
    fallback_wins = sum(
        1 for r in successes if str(r.get("strategy_used") or "") in FRAGILE_INTERNAL_STRATEGIES
    )
    rate = (len(successes) / len(evaluable) * 100) if evaluable else 0.0
    return {
        "success": len(successes),
        "failure": len(evaluable) - len(successes),
        "success_rate_pct": round(rate, 1),
        "stable_learning_rate_pct": round((stable / len(successes) * 100) if successes else 0.0, 1),
        "fragile_wins": fragile_wins,
        "fallback_dependency_rate": round((fallback_wins / len(successes)) if successes else 0.0, 4),
        "avg_confidence": round(
            sum(float(r.get("confidence") or 0.0) for r in successes) / len(successes),
            3,
        )
        if successes
        else 0.0,
    }


def build_field_strategy_matrix(
    golden_results: list[dict[str, Any]],
) -> tuple[dict[str, list[FieldStrategyStats]], dict[str, float]]:
    """Build per-field strategy stats and average field fragility."""
    raw_stats = aggregate_strategy_stats(golden_results)
    matrix: dict[str, list[FieldStrategyStats]] = {}
    field_fragility: dict[str, float] = {}

    for field_id in CORE_FIELDS:
        field_rows = [r for r in golden_results if r.get("field") == field_id]
        successes = [r for r in field_rows if r.get("status") == "success"]
        success_count = len(successes) or 1

        fragility_by_strategy: dict[str, list[float]] = {}
        confidence_by_strategy: dict[str, list[float]] = {}

        for row in successes:
            winner = str(row.get("strategy_used") or "")
            conf = float(row.get("confidence") or 0.0)
            breakdown = _winner_breakdown(row)
            frag = compute_fragility_score(
                row.get("all_attempted_strategies") or [],
                winner=winner,
                winner_confidence=conf,
                winner_breakdown=breakdown,
            )
            fragility_by_strategy.setdefault(winner, []).append(frag)
            confidence_by_strategy.setdefault(winner, []).append(conf)

        field_strategies = raw_stats.get(field_id, {}).get("strategies", {})
        entries: list[FieldStrategyStats] = []
        registry = STRATEGY_REGISTRY.get(field_id, ())

        for name in registry:
            st = field_strategies.get(name, {})
            attempts = int(st.get("attempts") or 0)
            wins = int(st.get("wins") or 0)
            frags = fragility_by_strategy.get(name, [])
            confs = confidence_by_strategy.get(name, [])
            entries.append(
                FieldStrategyStats(
                    strategy_name=name,
                    attempts=attempts,
                    wins=wins,
                    win_rate=round((wins / attempts) if attempts else 0.0, 4),
                    avg_confidence=round(sum(confs) / len(confs), 4) if confs else 0.0,
                    avg_fragility=round(sum(frags) / len(frags), 4) if frags else 0.0,
                    fallback_dependency_rate=round(wins / success_count, 4),
                )
            )

        matrix[field_id] = entries
        row_frags = []
        for row in successes:
            winner = str(row.get("strategy_used") or "")
            row_frags.append(
                compute_fragility_score(
                    row.get("all_attempted_strategies") or [],
                    winner=winner,
                    winner_confidence=float(row.get("confidence") or 0.0),
                    winner_breakdown=_winner_breakdown(row),
                )
            )
        field_fragility[field_id] = round(sum(row_frags) / len(row_frags), 4) if row_frags else 0.0

    return matrix, field_fragility


def compute_optimal_strategy_order(
    field_id: FieldId,
    stats: list[FieldStrategyStats],
    *,
    registry_order: tuple[str, ...] | None = None,
) -> list[str]:
    """Reorder strategies: win_rate desc, fragility asc, fallback_dependency asc; demote fragile."""
    base = registry_order if registry_order is not None else STRATEGY_REGISTRY.get(field_id, ())
    if not base:
        return []

    by_name = {s.strategy_name: s for s in stats}
    missing = [n for n in base if n not in by_name]
    for name in missing:
        by_name[name] = FieldStrategyStats(
            strategy_name=name,
            attempts=0,
            wins=0,
            win_rate=0.0,
            avg_confidence=0.0,
            avg_fragility=0.0,
            fallback_dependency_rate=0.0,
        )

    def sort_key(name: str) -> tuple[int, float, float, float, int]:
        st = by_name[name]
        tier = 1 if st.avg_fragility > FRAGILITY_DEMOTION_THRESHOLD else 0
        index = base.index(name) if name in base else 999
        return (
            tier,
            -st.win_rate,
            st.avg_fragility,
            st.fallback_dependency_rate,
            index,
        )

    ordered = sorted(base, key=sort_key)
    return list(ordered)


def _recommended_demotions(matrix: dict[str, list[FieldStrategyStats]]) -> list[dict[str, Any]]:
    demotions: list[dict[str, Any]] = []
    for field_id, entries in matrix.items():
        for st in entries:
            if st.avg_fragility > FRAGILITY_DEMOTION_THRESHOLD:
                demotions.append(
                    {
                        "field": field_id,
                        "strategy": st.strategy_name,
                        "reason": f"avg_fragility={st.avg_fragility:.2f}",
                    }
                )
    return demotions


def _top_fragile_strategies(matrix: dict[str, list[FieldStrategyStats]], limit: int = 10) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for field_id, entries in matrix.items():
        for st in entries:
            if st.wins == 0 and st.avg_fragility == 0.0:
                continue
            ranked.append(
                {
                    "field": field_id,
                    "strategy": st.strategy_name,
                    "wins": st.wins,
                    "avg_fragility": st.avg_fragility,
                    "score": st.wins * st.avg_fragility,
                }
            )
    ranked.sort(key=lambda x: (-float(x["score"]), -float(x["avg_fragility"])))
    return ranked[:limit]


def compute_amount_semantic_adjustments(
    field_fragility: dict[str, float],
    golden_results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    amount_frag = float(field_fragility.get("amount") or 0.0)
    fallback_dep = _amount_fallback_dependency(golden_results)

    if fallback_dep <= AMOUNT_SEMANTIC_TRIGGER_FALLBACK and amount_frag <= AMOUNT_SEMANTIC_TRIGGER_FRAGILITY:
        return None

    return {
        "enabled": True,
        "trigger_observed": {
            "fallback_dependency": round(fallback_dep, 4),
            "field_fragility": round(amount_frag, 4),
        },
        "adjustments": dict(DEFAULT_AMOUNT_ADJUSTMENTS),
    }


def _amount_fallback_dependency(results: list[dict[str, Any]]) -> float:
    successes = [r for r in results if r.get("field") == "amount" and r.get("status") == "success"]
    if not successes:
        return 0.0
    fallback = sum(
        1 for r in successes if str(r.get("strategy_used") or "") in AMOUNT_FALLBACK_STRATEGIES
    )
    return fallback / len(successes)


def build_semantic_scoring(
    field_fragility: dict[str, float],
    golden_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Field-keyed semantic scoring map for the atomic bundle."""
    scoring: dict[str, Any] = {}
    amount_adj = compute_amount_semantic_adjustments(field_fragility, golden_results)
    scoring["amount"] = amount_adj if amount_adj else {"enabled": False}

    for field_id in CORE_FIELDS:
        if field_id != "amount":
            scoring[field_id] = {"enabled": False}

    return scoring


def run_golden_learning_pass(
    golden_results: list[dict[str, Any]],
    *,
    golden_hash: str | None = None,
) -> GoldenLearningReport:
    """Analyze golden traces and produce learning report (no file I/O)."""
    ghash = golden_hash or compute_golden_hash(golden_results)
    matrix, field_fragility = build_field_strategy_matrix(golden_results)
    failure_patterns = [p.to_dict() for p in mine_failure_patterns(golden_results)]

    baseline_metrics = {f: _field_baseline_metrics(golden_results, f) for f in CORE_FIELDS}

    recommended_order: dict[str, list[str]] = {}
    for field_id in CORE_FIELDS:
        recommended_order[field_id] = compute_optimal_strategy_order(
            field_id,
            matrix.get(field_id, []),
        )

    demotions = _recommended_demotions(matrix)
    top_fragile = _top_fragile_strategies(matrix)

    amount_clusters = [
        p for p in failure_patterns if p.get("pattern_type") == "amount_vat_confusion"
    ]

    semantic = build_semantic_scoring(field_fragility, golden_results)
    amount_adj = semantic.get("amount") if semantic.get("amount", {}).get("enabled") else None

    return GoldenLearningReport(
        field_fragility=field_fragility,
        top_fragile_strategies=top_fragile,
        strategy_matrix=matrix,
        recommended_demotions=demotions,
        recommended_order=recommended_order,
        amount_failure_clusters=amount_clusters,
        amount_semantic_adjustments=amount_adj,
        baseline_metrics=baseline_metrics,
        bundle_path=str(DEFAULT_BUNDLE_PATH),
        bundle_version=BUNDLE_VERSION,
        golden_hash=ghash,
        stats_path=str(DEFAULT_STATS_PATH),
        failure_patterns=failure_patterns,
    )


def build_engine_bundle(
    report: GoldenLearningReport,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Assemble atomic deployment bundle dict."""
    ts = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    semantic_scoring: dict[str, Any] = {}
    if report.amount_semantic_adjustments:
        semantic_scoring["amount"] = dict(report.amount_semantic_adjustments)
    else:
        semantic_scoring["amount"] = {"enabled": False}
    for field_id in CORE_FIELDS:
        if field_id != "amount":
            semantic_scoring[field_id] = {"enabled": False}

    amount_frag = float(report.field_fragility.get("amount") or 0.0)
    amount_metrics = report.baseline_metrics.get("amount", {})
    fallback_dep = float(amount_metrics.get("fallback_dependency_rate") or 0.0)

    return {
        "version": report.bundle_version,
        "source": BUNDLE_SOURCE,
        "generated_at": ts,
        "golden_hash": report.golden_hash,
        "patch": {
            "order": dict(report.recommended_order),
            "demotions": list(report.recommended_demotions),
        },
        "semantic_scoring": semantic_scoring,
        "trigger_observed": {
            "amount_fallback_dependency": round(fallback_dep, 4),
            "amount_field_fragility": round(amount_frag, 4),
        },
    }


def write_engine_bundle(bundle: dict[str, Any], path: Path | None = None) -> Path:
    """Atomic write: .tmp → rename."""
    target = path or DEFAULT_BUNDLE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    data = json.dumps(bundle, indent=2, ensure_ascii=False) + "\n"
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, target)
    return target


def build_stats_document(report: GoldenLearningReport) -> dict[str, Any]:
    """Diagnostic-only stats JSON (never read by engine)."""
    field_matrix = {
        field_id: [st.to_dict() for st in entries]
        for field_id, entries in report.strategy_matrix.items()
    }
    return {
        "source": BUNDLE_SOURCE,
        "golden_hash": report.golden_hash,
        "field_matrix": field_matrix,
        "field_fragility": report.field_fragility,
        "failure_patterns": report.failure_patterns,
        "baseline_metrics": report.baseline_metrics,
        "recommended_order": report.recommended_order,
        "recommended_demotions": report.recommended_demotions,
    }


def write_stats_document(report: GoldenLearningReport, path: Path | None = None) -> Path:
    target = path or DEFAULT_STATS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_stats_document(report), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


def build_learning_report_document(report: GoldenLearningReport) -> dict[str, Any]:
    return {
        "field_fragility": report.field_fragility,
        "top_fragile_strategies": report.top_fragile_strategies,
        "recommended_order": report.recommended_order,
        "recommended_demotions": report.recommended_demotions,
        "amount_failure_clusters": report.amount_failure_clusters,
        "amount_semantic_adjustments": report.amount_semantic_adjustments,
        "bundle_version": report.bundle_version,
        "golden_hash": report.golden_hash,
        "baseline_metrics": report.baseline_metrics,
        "stats_path": report.stats_path,
        "bundle_path": report.bundle_path,
    }


def write_learning_report(report: GoldenLearningReport, path: Path | None = None) -> Path:
    target = path or DEFAULT_REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_learning_report_document(report), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target
