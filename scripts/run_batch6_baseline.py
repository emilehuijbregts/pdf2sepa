#!/usr/bin/env python3
"""Snapshot all Batch 6 PDFs to JSON (Round 1 baseline / final validation)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

APP_BASE = Path(__file__).resolve().parents[1]
if str(APP_BASE) not in sys.path:
    sys.path.insert(0, str(APP_BASE))

from scripts.run_batch6_single import extract_snapshot

BATCH6 = APP_BASE / "tests" / "Batch 6"
DEFAULT_OUT = APP_BASE / "reports" / "batch6_round1_baseline.json"


def list_batch6_pdfs() -> list[Path]:
    pdfs = list(BATCH6.glob("*.pdf")) + list(BATCH6.glob("*.PDF"))
    return sorted(pdfs, key=lambda p: p.name.lower())


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    rows: list[dict] = []
    for pdf in list_batch6_pdfs():
        rows.append(extract_snapshot(pdf))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"count": len(rows), "pdfs": rows}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} PDFs to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
