"""
Failure pattern miner for profile strategy engine traces.

Analyzes all_attempted_strategies from golden/learn runs to detect recurring
failure clusters and suggest strategy-order fixes (statistics-based, no ML).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from parser.profile_strategy_engine import FRAGILE_INTERNAL_STRATEGIES

PATTERN_LABEL_MISMATCH = "label_mismatch"
PATTERN_TOKEN_AMBIGUITY = "token_ambiguity"
PATTERN_IBAN_SCAN_MISS = "iban_scan_miss"
PATTERN_AMOUNT_VAT_CONFUSION = "amount_vat_confusion"

_LABEL_MISMATCH_REASONS = frozenset(
    {
        "no_label_near_value",
        "no_label",
        "no_colon_label",
        "no_label_prefix",
        "no_minimal_label",
        "no_iban_label",
        "validate_profile_failed",
    }
)

_IBAN_SCAN_STRATEGIES = frozenset(
    {
        "iban_full_text_scan",
        "iban_scan_with_checksum_filter",
    }
)


@dataclass
class FailurePatternReport:
    pattern_type: str
    frequency: int
    affected_fields: list[str]
    suggested_strategy_fix: str
    examples: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_type": self.pattern_type,
            "frequency": self.frequency,
            "affected_fields": list(self.affected_fields),
            "suggested_strategy_fix": self.suggested_strategy_fix,
            "examples": list(self.examples),
        }


def _example_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "pdf": row.get("pdf"),
        "field": row.get("field"),
        "strategy_used": row.get("strategy_used"),
        "status": row.get("status"),
        "confidence": row.get("confidence"),
    }


def _collect_label_mismatch(row: dict[str, Any]) -> bool:
    if row.get("status") == "success":
        return False
    for attempt in row.get("all_attempted_strategies") or []:
        reason = str(attempt.get("reason") or "")
        if reason in _LABEL_MISMATCH_REASONS:
            return True
    trace = row.get("validation_trace") or []
    return any("validate_failed" in str(t) for t in trace)


def _collect_token_ambiguity(row: dict[str, Any]) -> bool:
    if row.get("status") != "success":
        return False
    conf = float(row.get("confidence") or 0.0)
    strategy = str(row.get("strategy_used") or "")
    if conf <= 0.75 or strategy in FRAGILE_INTERNAL_STRATEGIES:
        return True
    for attempt in row.get("all_attempted_strategies") or []:
        if attempt.get("status") != "valid":
            continue
        breakdown = attempt.get("confidence_breakdown") or {}
        if float(breakdown.get("uniqueness") or 0.0) < 0.1:
            return True
    return False


def _collect_iban_scan_miss(row: dict[str, Any]) -> bool:
    if row.get("field") != "iban":
        return False
    if row.get("status") == "success":
        strategy = str(row.get("strategy_used") or "")
        return strategy in _IBAN_SCAN_STRATEGIES and float(row.get("confidence") or 0.0) < 0.85
    attempts = row.get("all_attempted_strategies") or []
    scan_invalid = all(
        a.get("status") != "valid"
        for a in attempts
        if str(a.get("strategy") or "") in _IBAN_SCAN_STRATEGIES
    )
    return scan_invalid and row.get("status") != "skipped"


def _collect_amount_vat_confusion(row: dict[str, Any]) -> bool:
    if row.get("field") != "amount":
        return False
    for attempt in row.get("all_attempted_strategies") or []:
        if attempt.get("status") != "valid":
            reason = str(attempt.get("reason") or "")
            if reason in ("derived_mismatch", "derived_components_missing"):
                return True
            continue
        breakdown = attempt.get("confidence_breakdown") or {}
        penalty = float(breakdown.get("penalty") or 0.0)
        if penalty <= -0.15:
            return True
    return False


def mine_failure_patterns(results: list[dict[str, Any]]) -> list[FailurePatternReport]:
    """Detect recurring failure patterns from golden/learn result rows."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in results:
        if row.get("status") == "skipped":
            continue
        if _collect_label_mismatch(row):
            buckets[PATTERN_LABEL_MISMATCH].append(row)
        if _collect_token_ambiguity(row):
            buckets[PATTERN_TOKEN_AMBIGUITY].append(row)
        if _collect_iban_scan_miss(row):
            buckets[PATTERN_IBAN_SCAN_MISS].append(row)
        if _collect_amount_vat_confusion(row):
            buckets[PATTERN_AMOUNT_VAT_CONFUSION].append(row)

    fixes = {
        PATTERN_LABEL_MISMATCH: (
            "Prioritize token_matching_* and generic_label_same_line_after_colon; "
            "demote fallback_value_locate_minimal_label"
        ),
        PATTERN_TOKEN_AMBIGUITY: (
            "Demote fragile strategies (amount_fallback_scan, unlabeled_prefix_amount); "
            "boost label_match scoring weight"
        ),
        PATTERN_IBAN_SCAN_MISS: (
            "Prioritize iban_scan_with_checksum_filter and iban_label_same_line "
            "over plain iban_full_text_scan"
        ),
        PATTERN_AMOUNT_VAT_CONFUSION: (
            "Prioritize derived_excl_plus_vat when excl/btw lines present; "
            "penalize excl-line amount matches"
        ),
    }

    reports: list[FailurePatternReport] = []
    for pattern_type, rows in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        fields = sorted({str(r.get("field") or "") for r in rows if r.get("field")})
        reports.append(
            FailurePatternReport(
                pattern_type=pattern_type,
                frequency=len(rows),
                affected_fields=fields,
                suggested_strategy_fix=fixes.get(pattern_type, "review strategy order"),
                examples=[_example_row(r) for r in rows[:5]],
            )
        )
    return reports


def aggregate_strategy_stats(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build per-field strategy success/attempt stats for optimize_strategy_order."""
    by_field: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(lambda: {"attempts": 0, "wins": 0, "penalty_sum": 0.0, "penalty_n": 0})
    )

    for row in results:
        field_id = str(row.get("field") or "")
        if not field_id or row.get("status") == "skipped":
            continue
        winner = str(row.get("strategy_used") or "")
        for attempt in row.get("all_attempted_strategies") or []:
            name = str(attempt.get("strategy") or "")
            if not name or attempt.get("status") == "skipped":
                continue
            st = by_field[field_id][name]
            st["attempts"] += 1
            if attempt.get("status") == "valid":
                if name == winner and row.get("status") == "success":
                    st["wins"] += 1
                breakdown = attempt.get("confidence_breakdown") or {}
                penalty = float(breakdown.get("penalty") or 0.0)
                st["penalty_sum"] += penalty
                st["penalty_n"] += 1

    out: dict[str, Any] = {}
    for field_id, strategies in by_field.items():
        field_stats: dict[str, Any] = {"strategies": {}}
        for name, st in strategies.items():
            pn = int(st["penalty_n"]) or 1
            field_stats["strategies"][name] = {
                "attempts": st["attempts"],
                "wins": st["wins"],
                "avg_penalty": round(float(st["penalty_sum"]) / pn, 4),
            }
        out[field_id] = field_stats
    return out
