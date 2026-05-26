#!/usr/bin/env python3
from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

APP_BASE = Path(__file__).resolve().parents[1]
if str(APP_BASE) not in sys.path:
    sys.path.insert(0, str(APP_BASE))

from logic.golden_dataset import (  # noqa: E402
    amount_status_from_payment,
    match_status_from_payment,
    pdf_filename,
)
from logic.invoice_folder_loader import (  # noqa: E402
    load_invoices_from_folder,
    strip_raw_text_from_invoices,
)
from logic.paths import read_user_data_root  # noqa: E402
from logic.payment_engine import calculate_payments  # noqa: E402
from logic.settings import load_settings, merge_debtor_with_defaults  # noqa: E402
from parser.supplier_db import SupplierDB  # noqa: E402
from parser.supplier_matcher import match_suppliers  # noqa: E402


def main() -> int:
    user_data_dir = read_user_data_root(APP_BASE)
    settings = load_settings(str(user_data_dir / "settings.json"))
    debtor = merge_debtor_with_defaults(settings.get("debtor"))
    raw = str(settings.get("last_invoice_dir") or "").strip()
    input_dir = Path(raw).expanduser().resolve()

    invoices = load_invoices_from_folder(
        input_dir,
        debtor_iban=(debtor.get("iban") or None),
        debtor_kvk=(debtor.get("kvk") or None),
        debtor_vat=(debtor.get("vat") or None),
    )
    db = SupplierDB(path=str(user_data_dir / "suppliers.json"))
    matched = match_suppliers(invoices, db)
    strip_raw_text_from_invoices(matched)
    payments, errors = calculate_payments(matched, session_date=date.today())

    inv_by_pdf: dict[str, dict] = {}
    for inv in matched:
        k = pdf_filename(inv.get("source_file"))
        if k and k not in inv_by_pdf:
            inv_by_pdf[k] = inv

    pay_by_pdf: dict[str, dict] = {}
    for p in payments:
        k = pdf_filename(p.get("_source_file") or p.get("source_file"))
        if not k:
            continue
        if k in pay_by_pdf:
            # Duplicate: remove to avoid wrong linkage (same logic as save_current_batch_as_golden.py)
            pay_by_pdf.pop(k, None)
            continue
        pay_by_pdf[k] = p

    saved: list[str] = []
    skipped: list[tuple[str, str]] = []
    for pdf in sorted(inv_by_pdf.keys()):
        p = pay_by_pdf.get(pdf)
        if p is None:
            skipped.append((pdf, "no_payment"))
            continue
        a = amount_status_from_payment(p).casefold()
        m = match_status_from_payment(p).casefold()
        if a != "confirmed" or m != "confirmed":
            skipped.append((pdf, f"status a={a} m={m}"))
            continue
        saved.append(pdf)

    print(f"INPUT_DIR: {input_dir}")
    print(f"PIPELINE: invoices={len(matched)} payments={len(payments)} error_buckets={len(errors)}")
    print(f"SAVED: {len(saved)}")
    for x in saved:
        print(f"  {x}")
    print(f"SKIPPED: {len(skipped)}")
    for pdf, why in skipped:
        print(f"  {pdf} :: {why}")
    print("ERROR_BUCKETS:")
    for e in errors:
        invs = e.get("invoices") or []
        print(f"  reason={e.get('reason')} count={len(invs) if isinstance(invs, list) else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

