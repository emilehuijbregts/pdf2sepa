#!/usr/bin/env python3
"""CI guard: legacy transitional helpers must not appear in SEPA/export code paths."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPORT_PATHS = [
    ROOT / "output" / "sepa_xml.py",
    ROOT / "logic" / "settlement_export.py",
]

MAIN_WINDOW = ROOT / "main_window.py"
FORBIDDEN = ("legacy_payments_and_errors",)


def _check_export_file(path: Path) -> list[str]:
    violations: list[str] = []
    text = path.read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines(), start=1):
        if any(token in line for token in FORBIDDEN):
            violations.append(f"{path}:{i}: {line.strip()}")
    return violations


def _check_main_window_export() -> list[str]:
    violations: list[str] = []
    text = MAIN_WINDOW.read_text(encoding="utf-8")
    in_export = False
    for i, line in enumerate(text.splitlines(), start=1):
        if "def _on_make_xml" in line:
            in_export = True
        if in_export and line.startswith("    def ") and "_on_make_xml" not in line:
            in_export = False
        if in_export and any(token in line for token in FORBIDDEN):
            violations.append(f"{MAIN_WINDOW}:{i}: {line.strip()}")
    return violations


def main() -> int:
    violations: list[str] = []
    for p in EXPORT_PATHS:
        if p.exists():
            violations.extend(_check_export_file(p))
    if MAIN_WINDOW.exists():
        violations.extend(_check_main_window_export())
    if violations:
        print("Export SSOT violations:")
        for v in violations:
            print(" ", v)
        return 1
    print("Export SSOT check OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
