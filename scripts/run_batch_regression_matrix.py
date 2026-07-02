#!/usr/bin/env python3
"""Run batch regression matrix and write reports/batch_regression_matrix.json."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from logic.batch_regression_matrix import (  # noqa: E402
    all_passed,
    format_matrix_entry,
    run_regression_matrix,
    write_regression_report,
)


def main() -> int:
    entries = run_regression_matrix(include_golden_singles=True)
    report_path = write_regression_report(entries)
    print(f"Wrote {report_path}")
    for entry in entries:
        print()
        print(format_matrix_entry(entry))
    passed = sum(1 for e in entries if e.status == "PASS")
    print()
    print(f"SUMMARY: {passed}/{len(entries)} PASS")
    return 0 if all_passed(entries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
