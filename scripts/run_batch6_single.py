#!/usr/bin/env python3
"""Run extraction for a single Batch 6 PDF (Round 1 tight feedback loop)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP_BASE = Path(__file__).resolve().parents[1]
if str(APP_BASE) not in sys.path:
    sys.path.insert(0, str(APP_BASE))

from logic.invoice_folder_loader import load_invoice_from_pdf_path

FIELDS = (
    "amount",
    "amount_status",
    "invoice_number",
    "customer_number",
    "invoice_date",
    "vat_number",
    "kvk_number",
    "iban",
)


def _load_debtor() -> dict:
    settings_path = APP_BASE / "data" / "settings.json"
    if not settings_path.is_file():
        return {}
    return json.loads(settings_path.read_text(encoding="utf-8")).get("debtor") or {}


def extract_snapshot(pdf_path: Path) -> dict:
    debtor = _load_debtor()
    data = load_invoice_from_pdf_path(
        pdf_path.resolve(),
        debtor_iban=debtor.get("iban"),
        debtor_kvk=debtor.get("kvk"),
        debtor_vat=debtor.get("vat"),
    )
    out = {"pdf": pdf_path.name, "source_file": str(pdf_path.resolve())}
    if data.get("load_error"):
        out["load_error"] = data["load_error"]
        return out
    for key in FIELDS:
        out[key] = data.get(key)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract one Batch 6 invoice PDF")
    parser.add_argument("pdf", type=Path, help="Path to PDF file")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    args = parser.parse_args()
    pdf = args.pdf
    if not pdf.is_file():
        print(f"Missing file: {pdf}", file=sys.stderr)
        return 1
    snap = extract_snapshot(pdf)
    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
        return 0
    print(f"PDF: {snap['pdf']}")
    if snap.get("load_error"):
        print(f"load_error: {snap['load_error']}")
        return 1
    for key in FIELDS:
        print(f"  {key}: {snap.get(key)!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
