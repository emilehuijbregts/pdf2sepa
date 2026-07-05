#!/usr/bin/env python3
"""
Phase 5 CI gate — golden freeze + regression suite + bundle compatibility.

Read-only observer of Phase 4: no learning pass, no bundle mutation.
Baseline capture and CI both use run_phase5_evaluation_sweep() (cold subprocess).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

APP_BASE = Path(__file__).resolve().parents[1]
if str(APP_BASE) not in sys.path:
    sys.path.insert(0, str(APP_BASE))

from parser.strategy_regression_guard import (
    BundleCompatibilityError,
    StrategyRegressionError,
    build_regression_baseline,
    load_regression_baseline,
    run_phase5_evaluation_sweep,
    validate_bundle_compatibility,
    write_regression_baseline,
)
from scripts.run_profile_strategy_golden import (
    DEFAULT_BASELINE_PATH,
    DEFAULT_BUNDLE_PATH,
    count_evaluable_successes,
)
from scripts.run_strategy_regression_suite import run_regression_suite

DEFAULT_REPORT_PATH = APP_BASE / "reports" / "strategy_regression_report.json"


def run_phase5_guard(
    *,
    baseline_path: Path = DEFAULT_BASELINE_PATH,
    bundle_path: Path = DEFAULT_BUNDLE_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
    capture_baseline: bool = False,
) -> bool:
    sweep = run_phase5_evaluation_sweep(
        enable_state_diffing=True,
        enable_runtime_parity=True,
        regression_baseline_path=None if capture_baseline else "auto",
    )

    state_diff = sweep.get("execution_state_diffing") or {}
    if not state_diff.get("passed"):
        mutations = int(state_diff.get("mutations_detected") or 0)
        raise StrategyRegressionError(
            f"execution state diffing failed: {mutations} mutation(s)"
        )

    parity = sweep.get("runtime_value_parity") or {}
    if not parity.get("passed"):
        count = int(parity.get("value_divergence_count") or 0)
        raise StrategyRegressionError(
            f"runtime value parity failed: {count} divergence(s)"
        )

    results = sweep.get("results") or []
    golden_success = count_evaluable_successes(results)

    if capture_baseline:
        bundle: dict[str, Any] = {}
        if bundle_path.is_file():
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        baseline = build_regression_baseline(
            results,
            golden_hash=str(sweep.get("golden_hash") or ""),
            bundle=bundle,
            engine_fingerprint=sweep.get("engine_fingerprint"),
        )
        write_regression_baseline(baseline, baseline_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "capture_baseline": True,
                    "golden_success_count": golden_success,
                    "expected_success_count": len(baseline.snapshots),
                    "execution_state_diffing": state_diff,
                    "runtime_value_parity": parity,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return True

    if not baseline_path.is_file():
        raise StrategyRegressionError(f"regression baseline not found: {baseline_path}")

    baseline = load_regression_baseline(baseline_path)
    if baseline.capture_import_graph_audit_passed is not True:
        raise StrategyRegressionError(
            "baseline missing capture_import_graph_audit_passed=true"
        )

    expected = len(baseline.snapshots)
    if golden_success != expected:
        raise StrategyRegressionError(
            f"golden freeze failed: {golden_success}/{expected} evaluable successes"
        )

    if bundle_path.is_file():
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        try:
            validate_bundle_compatibility(bundle, baseline=baseline)
        except BundleCompatibilityError as exc:
            raise StrategyRegressionError(f"bundle compatibility failed: {exc}") from exc

    regression_report = run_regression_suite(
        baseline_path=baseline_path,
        bundle_path=bundle_path,
        report_path=report_path,
        sweep=sweep,
    )
    regression_report["execution_state_diffing"] = state_diff
    regression_report["runtime_value_parity"] = parity
    report_path.write_text(
        json.dumps(regression_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return bool(regression_report.get("passed"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 5 strategy regression guard")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_PATH)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument(
        "--capture-baseline",
        action="store_true",
        help="Capture regression baseline via canonical sweep (subprocess by default)",
    )
    parser.add_argument(
        "--in-process",
        action="store_true",
        help="Run capture in-process (tests only; CI uses subprocess wrapper)",
    )
    args = parser.parse_args()

    if args.capture_baseline and not args.in_process:
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--capture-baseline",
            "--in-process",
            "--baseline",
            str(args.baseline),
            "--bundle",
            str(args.bundle),
            "--report",
            str(args.report),
        ]
        result = subprocess.run(cmd, cwd=str(APP_BASE))
        return int(result.returncode)

    try:
        passed = run_phase5_guard(
            baseline_path=args.baseline,
            bundle_path=args.bundle,
            report_path=args.report,
            capture_baseline=args.capture_baseline,
        )
    except (StrategyRegressionError, BundleCompatibilityError) as exc:
        print(f"PHASE 5 GUARD FAILED: {exc}", file=sys.stderr)
        return 1

    if passed:
        label = "Phase 5 baseline captured" if args.capture_baseline else "Phase 5 guard PASSED"
        print(label)
        return 0

    print("Phase 5 guard FAILED — see regression report", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
