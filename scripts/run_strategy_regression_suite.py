#!/usr/bin/env python3
"""
Phase 5 strategy regression suite — hard gate on snapshot drift.

Loads committed baseline, re-runs golden under evaluation_mode=True,
and fails on any winner/candidate/confidence/breakdown drift.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

APP_BASE = Path(__file__).resolve().parents[1]
if str(APP_BASE) not in sys.path:
    sys.path.insert(0, str(APP_BASE))

from parser.strategy_regression_guard import (
    StrategyRegressionError,
    build_regression_report,
    load_regression_baseline,
    run_phase5_evaluation_sweep,
    snapshots_from_golden_results,
    validate_bundle_compatibility,
)
from scripts.run_profile_strategy_golden import (
    DEFAULT_BASELINE_PATH,
    DEFAULT_BUNDLE_PATH,
    count_evaluable_successes,
)

DEFAULT_REPORT_PATH = APP_BASE / "reports" / "strategy_regression_report.json"


def run_regression_suite(
    *,
    baseline_path: Path = DEFAULT_BASELINE_PATH,
    bundle_path: Path = DEFAULT_BUNDLE_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
    sweep: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not baseline_path.is_file():
        raise StrategyRegressionError(f"regression baseline not found: {baseline_path}")

    baseline = load_regression_baseline(baseline_path)

    if sweep is None:
        sweep = run_phase5_evaluation_sweep(
            enable_state_diffing=True,
            enable_runtime_parity=False,
        )

    results = sweep.get("results") or []
    golden_success_count = count_evaluable_successes(results)
    expected_success_count = len(baseline.snapshots)

    bundle: dict[str, Any] = {}
    if bundle_path.is_file():
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        validate_bundle_compatibility(bundle, baseline=baseline)

    semantic = bundle.get("semantic_scoring") if isinstance(bundle.get("semantic_scoring"), dict) else {}
    current_snapshots = snapshots_from_golden_results(results, semantic_scoring=semantic)

    regression_report = build_regression_report(
        baseline,
        current_snapshots,
        golden_success_count=golden_success_count,
        expected_success_count=expected_success_count,
    )
    regression_report["baseline_path"] = str(baseline_path)
    regression_report["bundle_path"] = str(bundle_path)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(regression_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return regression_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 5 strategy regression suite")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_PATH)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    try:
        result = run_regression_suite(
            baseline_path=args.baseline,
            bundle_path=args.bundle,
            report_path=args.report,
        )
    except StrategyRegressionError as exc:
        print(f"REGRESSION SUITE FAILED: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"Report written to {args.report}")

    summary = result.get("summary") or {}
    drift_count = int(summary.get("drift_count") or 0)
    golden_ok = result.get("golden_success_count") == result.get("expected_success_count")

    print(
        f"Regression suite {'PASSED' if result.get('passed') else 'FAILED'}: "
        f"drifts={drift_count}, golden={result.get('golden_success_count')}/{result.get('expected_success_count')}"
    )

    if not result.get("passed"):
        if not golden_ok:
            print(
                f"Golden success count mismatch: {result.get('golden_success_count')} != {result.get('expected_success_count')}",
                file=sys.stderr,
            )
        if drift_count:
            print(f"Snapshot drift count: {drift_count}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
