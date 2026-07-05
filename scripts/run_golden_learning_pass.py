#!/usr/bin/env python3
"""
Run Phase 4 golden learning pass: stats (diagnostic) + atomic engine bundle + report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP_BASE = Path(__file__).resolve().parents[1]
if str(APP_BASE) not in sys.path:
    sys.path.insert(0, str(APP_BASE))

from parser.golden_dataset_learning_pass import (
    DEFAULT_BUNDLE_PATH,
    DEFAULT_REPORT_PATH,
    DEFAULT_STATS_PATH,
    build_engine_bundle,
    compute_golden_hash,
    run_golden_learning_pass,
    write_engine_bundle,
    write_learning_report,
    write_stats_document,
)
from scripts.run_profile_strategy_golden import run_all

DEFAULT_GOLDEN = APP_BASE / "reports" / "profile_strategy_golden.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run golden dataset learning pass (Phase 4)")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Golden report JSON (default: run golden regression)",
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=DEFAULT_BUNDLE_PATH,
        help="Write atomic engine bundle",
    )
    parser.add_argument(
        "--stats",
        type=Path,
        default=DEFAULT_STATS_PATH,
        help="Write diagnostic stats JSON",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="Write human-readable learning report",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write files")
    args = parser.parse_args()

    if args.input and args.input.is_file():
        golden_report = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        golden_report = run_all()

    results = golden_report.get("results") or []
    ghash = compute_golden_hash(results)
    report = run_golden_learning_pass(results, golden_hash=ghash)
    report.bundle_path = str(args.bundle)
    report.stats_path = str(args.stats)

    if args.dry_run:
        print(f"golden_hash={ghash}")
        for field_id, order in report.recommended_order.items():
            print(f"{field_id} order: {order[:3]}... ({len(order)} strategies)")
        return 0

    bundle = build_engine_bundle(report)
    write_engine_bundle(bundle, args.bundle)
    write_stats_document(report, args.stats)
    write_learning_report(report, args.report)

    print(f"Wrote bundle {args.bundle}")
    print(f"Wrote stats {args.stats}")
    print(f"Wrote report {args.report}")
    print(f"golden_hash={ghash}")
    for field_id, frag in report.field_fragility.items():
        stable = report.baseline_metrics.get(field_id, {}).get("stable_learning_rate_pct")
        print(f"{field_id}: fragility={frag} stable={stable}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
