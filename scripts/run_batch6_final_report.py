#!/usr/bin/env python3
"""Compare Batch 6 after-run vs baseline and print summary table."""

from __future__ import annotations

import json
import sys
from pathlib import Path

APP_BASE = Path(__file__).resolve().parents[1]
if str(APP_BASE) not in sys.path:
    sys.path.insert(0, str(APP_BASE))

from scripts.run_batch6_baseline import list_batch6_pdfs
from scripts.run_batch6_single import extract_snapshot

BASELINE = APP_BASE / "reports" / "batch6_round1_baseline.json"
OUT = APP_BASE / "reports" / "batch6_round1_after.json"
REPORT = APP_BASE / "reports" / "batch6_round1_final_report.md"

CORE_FIELDS = ("amount", "invoice_number", "invoice_date")


def _load_baseline() -> dict[str, dict]:
    if not BASELINE.is_file():
        return {}
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    return {row["pdf"]: row for row in data.get("pdfs", [])}


def main() -> int:
    baseline = _load_baseline()
    after_rows: list[dict] = []
    for pdf in list_batch6_pdfs():
        after_rows.append(extract_snapshot(pdf))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"count": len(after_rows), "pdfs": after_rows}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Batch 6 Round 1 — Final Report",
        "",
        "| PDF | amount | invoice_number | invoice_date | vat | kvk | status |",
        "|-----|--------|----------------|--------------|-----|-----|--------|",
    ]
    fully = partial = missing = regressions = 0
    regression_list: list[str] = []

    for row in after_rows:
        pdf = row["pdf"]
        if row.get("load_error"):
            status = "LOAD_ERROR"
            missing += 1
        else:
            core_ok = all(row.get(f) not in (None, "") for f in CORE_FIELDS)
            any_ok = any(row.get(f) not in (None, "") for f in CORE_FIELDS)
            if core_ok:
                status = "OK"
                fully += 1
            elif any_ok:
                status = "PARTIAL"
                partial += 1
            else:
                status = "MISS"
                missing += 1

        base = baseline.get(pdf, {})
        if base and not base.get("load_error") and not row.get("load_error"):
            for f in CORE_FIELDS + ("vat_number", "kvk_number"):
                if base.get(f) != row.get(f) and base.get(f) not in (None, "") and row.get(f) in (
                    None,
                    "",
                ):
                    regression_list.append(f"{pdf}: {f} {base.get(f)!r} -> {row.get(f)!r}")
                    if status == "OK":
                        status = "REGRESSION"

        lines.append(
            f"| {pdf} | {row.get('amount')} | {row.get('invoice_number')} | "
            f"{row.get('invoice_date')} | {row.get('vat_number')} | {row.get('kvk_number')} | {status} |"
        )

    n = len(after_rows)
    lines.extend(
        [
            "",
            f"- Fully correct (amount + invoice_number + invoice_date): **{fully}/{n} ({100*fully/n:.1f}%)**",
            f"- Partial: **{partial}/{n} ({100*partial/n:.1f}%)**",
            f"- Missing core fields: **{missing}/{n}**",
            "",
            "## Regressions vs baseline",
            "",
        ]
    )
    if regression_list:
        lines.extend(f"- {r}" for r in regression_list)
    else:
        lines.append("- None")

    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nWrote {OUT} and {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
