#!/usr/bin/env python3
"""
Optimize profile strategy order via Phase 4 golden learning pass.

Writes data/strategy_engine_bundle.json (deployment) and diagnostic stats.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

APP_BASE = Path(__file__).resolve().parents[1]
if str(APP_BASE) not in sys.path:
    sys.path.insert(0, str(APP_BASE))

from parser.golden_dataset_learning_pass import (
    DEFAULT_BUNDLE_PATH,
    DEFAULT_STATS_PATH,
    build_engine_bundle,
    compute_golden_hash,
    run_golden_learning_pass,
    write_engine_bundle,
    write_stats_document,
)
from scripts.run_profile_strategy_golden import CORE_FIELDS, run_all

DEFAULT_OUTPUT = DEFAULT_BUNDLE_PATH


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimize strategy order via golden learning pass")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Write atomic engine bundle JSON",
    )
    parser.add_argument(
        "--stats",
        type=Path,
        default=DEFAULT_STATS_PATH,
        help="Write diagnostic stats JSON",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Use existing golden report JSON instead of re-running",
    )
    args = parser.parse_args()

    if args.input and args.input.is_file():
        import json

        golden_report = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        golden_report = run_all()

    results = golden_report.get("results") or []
    ghash = compute_golden_hash(results)
    report = run_golden_learning_pass(results, golden_hash=ghash)
    bundle = build_engine_bundle(report)
    write_engine_bundle(bundle, args.output)
    write_stats_document(report, args.stats)

    print(f"Wrote bundle {args.output}")
    print(f"Wrote stats {args.stats}")
    for field_id in CORE_FIELDS:
        m = report.baseline_metrics.get(field_id, {})
        print(
            f"{field_id}: stable={m.get('stable_learning_rate_pct')}% "
            f"fragile={m.get('fragile_wins')} "
            f"fallback_dep={m.get('fallback_dependency_rate')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
