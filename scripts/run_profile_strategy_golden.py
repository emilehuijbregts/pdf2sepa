#!/usr/bin/env python3
"""
Profile strategy engine regression runner against golden dataset.

Golden JSON is used for validation only (not training): each case runs
run_strategies() in learn mode with confirmed_value from golden JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

APP_BASE = Path(__file__).resolve().parents[1]
if str(APP_BASE) not in sys.path:
    sys.path.insert(0, str(APP_BASE))

from parser.field_model import FieldId
from parser.pdf_parser import extract_text_strict
from parser.golden_dataset_learning_pass import compute_golden_hash
from parser.profile_strategy_engine import (
    FRAGILE_INTERNAL_STRATEGIES,
    StrategyContext,
    run_strategies,
    value_in_raw_text,
)
from parser.strategy_failure_miner import aggregate_strategy_stats, mine_failure_patterns
from parser.strategy_regression_guard import (
    build_regression_baseline,
    run_phase5_evaluation_sweep,
    write_regression_baseline,
)
from tests.golden_test_support import (
    GOLDEN_PDFS_DIR,
    golden_expected,
    iter_golden_cases,
)

CORE_FIELDS: tuple[FieldId, ...] = (
    "amount",
    "invoice_number",
    "customer_number",
    "iban",
)

DEFAULT_BASELINE_PATH = APP_BASE / "data" / "strategy_regression_baseline.json"
DEFAULT_BUNDLE_PATH = APP_BASE / "data" / "strategy_engine_bundle.json"

FIELD_GOLDEN_KEY = {
    "amount": "amount",
    "invoice_number": "invoice_number",
    "customer_number": "customer_code",
    "iban": "iban",
}


def _normalize_actual(field_id: FieldId, value: Any) -> str | None:
    if value is None:
        return None
    if field_id == "amount":
        try:
            return str(Decimal(str(value)).quantize(Decimal("0.01")))
        except Exception:
            return str(value)
    return str(value).strip()


def _normalize_expected(field_id: FieldId, expected: Any) -> str | None:
    if expected is None:
        return None
    if field_id == "amount":
        try:
            return str(Decimal(str(expected)).quantize(Decimal("0.01")))
        except Exception:
            return str(expected)
    return str(expected).strip()


def run_case(case, field_id: FieldId) -> dict[str, Any]:
    pdf_path = GOLDEN_PDFS_DIR / case.source_file
    raw_text = extract_text_strict(str(pdf_path))
    golden_key = FIELD_GOLDEN_KEY[field_id]
    raw_expected = case.golden.get(golden_key)
    if raw_expected is None or str(raw_expected).strip() == "":
        return {
            "pdf": case.source_file,
            "field": field_id,
            "status": "skipped",
            "reason": "no_golden_value",
        }

    expected = golden_expected(case, field_id if field_id != "customer_number" else "customer_code")
    expected_norm = _normalize_expected(field_id, expected)

    if not value_in_raw_text(raw_text, expected, field_id):
        return {
            "pdf": case.source_file,
            "field": field_id,
            "status": "expected_failure",
            "reason": "value_not_in_text",
            "expected": expected_norm,
            "validation_trace": ["value_not_in_raw_text"],
        }

    ctx = StrategyContext(
        field_id=field_id,
        raw_text=raw_text,
        confirmed_value=expected,
        mode="learn",
        evaluation_mode=True,
    )
    result = run_strategies(field_id, ctx)
    actual_norm = _normalize_actual(field_id, result.value)
    success = actual_norm == expected_norm

    return {
        "pdf": case.source_file,
        "field": field_id,
        "status": "success" if success else "failure",
        "success": success,
        "expected": expected_norm,
        "actual": actual_norm,
        "strategy_used": result.strategy_used,
        "all_attempted_strategies": [a.to_dict() for a in result.all_attempted_strategies],
        "validation_trace": result.validation_trace,
        "confidence": result.confidence,
        "confidence_breakdown": next(
            (
                a.confidence_breakdown
                for a in reversed(result.all_attempted_strategies)
                if a.strategy == result.strategy_used and a.status == "valid"
            ),
            {},
        ),
    }


def _field_quality_metrics(results: list[dict[str, Any]], field_id: str) -> dict[str, Any]:
    successes = [r for r in results if r.get("field") == field_id and r.get("status") == "success"]
    strategy_wins: dict[str, int] = {}
    for r in successes:
        s = str(r.get("strategy_used") or "unknown")
        strategy_wins[s] = strategy_wins.get(s, 0) + 1
    fragile = sum(1 for r in successes if float(r.get("confidence") or 0.0) <= 0.75)
    stable = sum(
        1
        for r in successes
        if float(r.get("confidence") or 0.0) >= 0.8
        and str(r.get("strategy_used") or "") not in FRAGILE_INTERNAL_STRATEGIES
    )
    multi_valid = sum(
        1
        for r in successes
        if sum(1 for a in (r.get("all_attempted_strategies") or []) if a.get("status") == "valid") > 1
    )
    return {
        "avg_confidence": round(
            sum(float(r.get("confidence") or 0.0) for r in successes) / len(successes),
            3,
        )
        if successes
        else 0.0,
        "fragile_wins": fragile,
        "stable_learning_rate_pct": round((stable / len(successes) * 100) if successes else 0.0, 1),
        "multi_valid_cases": multi_valid,
        "strategy_distribution": strategy_wins,
    }


def run_all() -> dict[str, Any]:
    sweep = run_phase5_evaluation_sweep(
        enable_state_diffing=False,
        enable_runtime_parity=False,
    )
    results: list[dict[str, Any]] = sweep.get("results") or []
    stats: dict[str, dict[str, int]] = {
        f: {"success": 0, "failure": 0, "expected_failure": 0, "skipped": 0, "total": 0}
        for f in CORE_FIELDS
    }

    for row in results:
        field_id = str(row.get("field") or "")
        if field_id not in stats:
            continue
        st = stats[field_id]
        st["total"] += 1
        status = row.get("status", "failure")
        if status in st:
            st[status] += 1

    summary: dict[str, Any] = {}
    for field_id, st in stats.items():
        evaluable = st["success"] + st["failure"]
        rate = (st["success"] / evaluable * 100) if evaluable else 0.0
        quality = _field_quality_metrics(results, field_id)
        summary[field_id] = {
            **st,
            "success_rate_pct": round(rate, 1),
            **quality,
        }

    failure_patterns = [p.to_dict() for p in mine_failure_patterns(results)]
    strategy_stats = aggregate_strategy_stats(results)

    return {
        "summary": summary,
        "failure_patterns": failure_patterns,
        "strategy_stats": strategy_stats,
        "results": results,
    }


def count_evaluable_successes(results: list[dict[str, Any]]) -> int:
    return sum(
        1 for r in results if r.get("status") == "success" and r.get("field") in CORE_FIELDS
    )


def capture_baseline(
    report: dict[str, Any],
    *,
    baseline_path: Path = DEFAULT_BASELINE_PATH,
    bundle_path: Path = DEFAULT_BUNDLE_PATH,
) -> Path:
    """Write strategy regression baseline from a golden report."""
    results = report.get("results") or []
    ghash = compute_golden_hash(results)
    if bundle_path.is_file():
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    else:
        bundle = {}
    baseline = build_regression_baseline(results, golden_hash=ghash, bundle=bundle)
    write_regression_baseline(baseline, baseline_path)
    return baseline_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run profile strategy golden regression")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=APP_BASE / "reports" / "profile_strategy_golden.json",
        help="Write JSON report to this path",
    )
    parser.add_argument(
        "--capture-snapshots",
        action="store_true",
        help="Deprecated: use scripts/run_phase5_guard.py --capture-baseline",
    )
    parser.add_argument(
        "--baseline-path",
        type=Path,
        default=DEFAULT_BASELINE_PATH,
        help="Regression baseline output path (with --capture-snapshots)",
    )
    parser.add_argument("--quiet", action="store_true", help="Only print summary")
    args = parser.parse_args()

    if args.capture_snapshots:
        from scripts.run_phase5_guard import run_phase5_guard

        run_phase5_guard(
            baseline_path=args.baseline_path,
            bundle_path=DEFAULT_BUNDLE_PATH,
            capture_baseline=True,
        )
        if not args.quiet:
            print(f"Regression baseline written to {args.baseline_path}")
        return 0

    report = run_all()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.capture_snapshots:
        return 0

    if not args.quiet:
        print(f"Report written to {args.output}")
    for field_id, st in report["summary"].items():
        print(
            f"{field_id}: {st['success']}/{st['success'] + st['failure']} "
            f"({st['success_rate_pct']}%) "
            f"stable={st.get('stable_learning_rate_pct', 0)}% "
            f"fragile={st.get('fragile_wins', 0)} "
            f"[expected_failures={st['expected_failure']}, skipped={st['skipped']}]"
        )

    core_fields = ("amount", "invoice_number", "customer_number")
    core_rates = [report["summary"][f]["success_rate_pct"] for f in core_fields]
    min_core = min(core_rates) if core_rates else 0.0
    if min_core < 95.0:
        print(f"WARNING: core field success below 95% threshold (min={min_core}%)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
