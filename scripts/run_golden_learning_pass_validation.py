#!/usr/bin/env python3
"""
Phase 4 learning pass regression guard.

Validates golden accuracy, field stability, and optional runtime candidate parity.
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

from parser.golden_dataset_learning_pass import (
    DEFAULT_BUNDLE_PATH,
    _amount_fallback_dependency,
    build_engine_bundle,
    compute_golden_hash,
    run_golden_learning_pass,
    write_engine_bundle,
)
from parser.profile_strategy_engine import FRAGILE_INTERNAL_STRATEGIES, reload_strategy_engine_state
from parser.pdf_parser import extract_text_strict
from parser.profile_strategy_engine import StrategyContext, run_strategies
from scripts.run_profile_strategy_golden import CORE_FIELDS, run_all
from tests.golden_test_support import GOLDEN_PDFS_DIR, golden_expected, iter_golden_cases

DEFAULT_VALIDATION_REPORT = APP_BASE / "reports" / "golden_learning_validation.json"


def _field_metrics(results: list[dict[str, Any]], field_id: str) -> dict[str, Any]:
    successes = [r for r in results if r.get("field") == field_id and r.get("status") == "success"]
    evaluable = [
        r for r in results if r.get("field") == field_id and r.get("status") in ("success", "failure")
    ]
    fragile = sum(1 for r in successes if float(r.get("confidence") or 0.0) <= 0.75)
    stable = sum(
        1
        for r in successes
        if float(r.get("confidence") or 0.0) >= 0.8
        and str(r.get("strategy_used") or "") not in FRAGILE_INTERNAL_STRATEGIES
    )
    fallback = sum(
        1 for r in successes if str(r.get("strategy_used") or "") in FRAGILE_INTERNAL_STRATEGIES
    )
    rate = (len(successes) / len(evaluable) * 100) if evaluable else 0.0
    return {
        "success": len(successes),
        "evaluable": len(evaluable),
        "success_rate_pct": round(rate, 1),
        "stable_learning_rate_pct": round((stable / len(successes) * 100) if successes else 0.0, 1),
        "fragile_wins": fragile,
        "fallback_dependency_rate": round((fallback / len(successes)) if successes else 0.0, 4),
    }


def _total_evaluable_successes(results: list[dict[str, Any]]) -> int:
    return sum(
        1 for r in results if r.get("status") == "success" and r.get("field") in CORE_FIELDS
    )


def _runtime_smoke_candidate_divergence(limit: int | None = 20) -> dict[str, Any]:
    """Compare evaluation vs runtime mode on golden subset."""
    strategy_divergences: list[dict[str, Any]] = []
    value_divergences: list[dict[str, Any]] = []
    checked = 0
    for case in iter_golden_cases():
        for field_id in CORE_FIELDS:
            if limit is not None and checked >= limit:
                break
            pdf_path = GOLDEN_PDFS_DIR / case.source_file
            if not pdf_path.is_file():
                continue
            raw_text = extract_text_strict(str(pdf_path))
            golden_key = {
                "amount": "amount",
                "invoice_number": "invoice_number",
                "customer_number": "customer_code",
                "iban": "iban",
            }[field_id]
            if case.golden.get(golden_key) in (None, ""):
                continue
            expected = golden_expected(
                case, field_id if field_id != "customer_number" else "customer_code"
            )
            eval_ctx = StrategyContext(
                field_id=field_id,
                raw_text=raw_text,
                confirmed_value=expected,
                mode="learn",
                evaluation_mode=True,
            )
            run_ctx = StrategyContext(
                field_id=field_id,
                raw_text=raw_text,
                confirmed_value=expected,
                mode="learn",
                evaluation_mode=False,
            )
            eval_result = run_strategies(field_id, eval_ctx)
            run_result = run_strategies(field_id, run_ctx)
            checked += 1
            if eval_result.value != run_result.value:
                value_divergences.append(
                    {
                        "pdf": case.source_file,
                        "field": field_id,
                        "eval_strategy": eval_result.strategy_used,
                        "runtime_strategy": run_result.strategy_used,
                        "eval_value": eval_result.value,
                        "runtime_value": run_result.value,
                    }
                )
            elif eval_result.strategy_used != run_result.strategy_used:
                strategy_divergences.append(
                    {
                        "pdf": case.source_file,
                        "field": field_id,
                        "eval_strategy": eval_result.strategy_used,
                        "runtime_strategy": run_result.strategy_used,
                        "eval_value": eval_result.value,
                        "runtime_value": run_result.value,
                    }
                )
        if limit is not None and checked >= limit:
            break

    def _by_field(rows: list[dict[str, Any]]) -> dict[str, int]:
        out: dict[str, int] = {f: 0 for f in CORE_FIELDS}
        for d in rows:
            out[str(d.get("field"))] = out.get(str(d.get("field")), 0) + 1
        return out

    return {
        "checked": checked,
        "value_divergence_count": len(value_divergences),
        "strategy_divergence_count": len(strategy_divergences),
        "divergence_count": len(value_divergences),
        "value_divergences_by_field": _by_field(value_divergences),
        "strategy_divergences_by_field": _by_field(strategy_divergences),
        "value_examples": value_divergences[:10],
        "strategy_examples": strategy_divergences[:10],
    }


def run_validation(
    *,
    golden_report: dict[str, Any] | None = None,
    bundle_path: Path = DEFAULT_BUNDLE_PATH,
    runtime_check: bool = False,
    runtime_limit: int | None = 20,
    write_bundle: bool = False,
) -> dict[str, Any]:
    reload_strategy_engine_state(regression_baseline_path="auto")

    # Baseline always from fresh golden run (evaluation_mode=True in run_case).
    baseline_report = run_all()
    baseline_results = baseline_report.get("results") or []
    baseline_metrics = {f: _field_metrics(baseline_results, f) for f in CORE_FIELDS}
    baseline_success = _total_evaluable_successes(baseline_results)

    # Learning pass input: optional cached report or fresh baseline.
    learn_report = golden_report if golden_report is not None else baseline_report
    results = learn_report.get("results") or baseline_results
    ghash = compute_golden_hash(results)
    report = run_golden_learning_pass(results, golden_hash=ghash)
    bundle = build_engine_bundle(report)

    if write_bundle:
        write_engine_bundle(bundle, bundle_path)

    reload_strategy_engine_state(bundle_path=bundle_path, skip_bundle_validation=True)
    post_report = run_all()
    post_results = post_report.get("results") or []
    post_metrics = {f: _field_metrics(post_results, f) for f in CORE_FIELDS}
    post_success = _total_evaluable_successes(post_results)

    checks: list[dict[str, Any]] = []
    passed = True

    if post_success < baseline_success:
        passed = False
    checks.append(
        {
            "name": "evaluable_success_count",
            "baseline": baseline_success,
            "post": post_success,
            "passed": post_success >= baseline_success,
        }
    )

    for field_id in CORE_FIELDS:
        base = baseline_metrics[field_id]
        post = post_metrics[field_id]
        drop = base["success_rate_pct"] - post["success_rate_pct"]
        ok_drop = drop <= 2.0
        ok_fallback = post["fallback_dependency_rate"] <= base["fallback_dependency_rate"]
        if not ok_drop or not ok_fallback:
            passed = False
        checks.append(
            {
                "name": f"{field_id}_success_rate",
                "baseline_pct": base["success_rate_pct"],
                "post_pct": post["success_rate_pct"],
                "max_drop_pct": 2.0,
                "passed": ok_drop,
            }
        )
        checks.append(
            {
                "name": f"{field_id}_fallback_dependency",
                "baseline": base["fallback_dependency_rate"],
                "post": post["fallback_dependency_rate"],
                "passed": ok_fallback,
            }
        )

    if bundle.get("golden_hash") != ghash:
        passed = False
    checks.append(
        {
            "name": "bundle_golden_hash",
            "expected": ghash,
            "actual": bundle.get("golden_hash"),
            "passed": bundle.get("golden_hash") == ghash,
        }
    )

    runtime_smoke: dict[str, Any] | None = None
    if runtime_check:
        reload_strategy_engine_state(bundle_path=bundle_path, skip_bundle_validation=True)
        runtime_smoke = _runtime_smoke_candidate_divergence(limit=runtime_limit)
        amount_base = baseline_metrics["amount"]["stable_learning_rate_pct"]
        amount_post = post_metrics["amount"]["stable_learning_rate_pct"]
        stable_ok = amount_post >= amount_base
        value_parity_ok = int(runtime_smoke.get("value_divergence_count") or 0) == 0
        checks.append(
            {
                "name": "amount_stable_learning_not_decreased",
                "baseline_pct": amount_base,
                "post_pct": amount_post,
                "passed": stable_ok,
            }
        )
        checks.append(
            {
                "name": "runtime_value_parity",
                "value_divergence_count": runtime_smoke.get("value_divergence_count"),
                "strategy_divergence_count": runtime_smoke.get("strategy_divergence_count"),
                "passed": value_parity_ok,
            }
        )
        if not stable_ok or not value_parity_ok:
            passed = False

    out = {
        "passed": passed,
        "golden_hash": ghash,
        "baseline_metrics": baseline_metrics,
        "post_metrics": post_metrics,
        "checks": checks,
        "runtime_smoke": runtime_smoke,
        "amount_fallback_dependency": round(_amount_fallback_dependency(results), 4),
    }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate golden learning pass regression guard")
    parser.add_argument("--input", type=Path, default=None, help="Golden report JSON")
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE_PATH)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_VALIDATION_REPORT,
        help="Write validation report JSON",
    )
    parser.add_argument("--runtime-check", action="store_true", help="Run runtime smoke comparison")
    parser.add_argument("--runtime-limit", type=int, default=20, help="Max cases for runtime smoke")
    parser.add_argument("--write-bundle", action="store_true", help="Write engine bundle to disk")
    args = parser.parse_args()

    golden_report = None
    if args.input and args.input.is_file():
        golden_report = json.loads(args.input.read_text(encoding="utf-8"))

    result = run_validation(
        golden_report=golden_report,
        bundle_path=args.bundle,
        runtime_check=args.runtime_check,
        runtime_limit=args.runtime_limit,
        write_bundle=args.write_bundle,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Validation {'PASSED' if result['passed'] else 'FAILED'}")
    print(f"Report written to {args.output}")
    for check in result["checks"]:
        status = "OK" if check.get("passed") else "FAIL"
        print(f"  [{status}] {check.get('name')}")

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
