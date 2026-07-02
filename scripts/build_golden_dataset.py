#!/usr/bin/env python3
"""Bouw een golden dataset (JSON per PDF) via dezelfde pipeline als de desktop-app.

Pipeline: invoice_folder_loader → supplier_matcher → payment_engine.

Alleen facturen met parser-bedragstatus ``confirmed`` (niet ``certain`` — die bestaat
niet in de codebase) worden automatisch opgeslagen; anders warning en geen write.

Headless: geen IBAN-mismatch dialoog (app-default = database-IBAN blijft leidend).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

APP_BASE = Path(__file__).resolve().parents[1]
if str(APP_BASE) not in sys.path:
    sys.path.insert(0, str(APP_BASE))

from logic.invoice_folder_loader import (  # noqa: E402
    load_invoices_from_folder,
    strip_raw_text_from_invoices,
)
from logic.paths import read_user_data_root  # noqa: E402
from logic.credit_enrichment import enrich_credit_documents
from logic.golden_dataset import build_payment_index_from_engine
from logic.payment_engine import calculate_payments  # noqa: E402
from ui.settlement_table import review_documents_as_error_buckets
from logic.settings import load_settings, merge_debtor_with_defaults  # noqa: E402
from parser.supplier_db import SupplierDB  # noqa: E402
from parser.supplier_matcher import match_suppliers  # noqa: E402

_MONEY_Q = Decimal("0.01")
logger = logging.getLogger("build_golden_dataset")


def _payment_lookup_key(p: dict) -> tuple[str, str]:
    sup = str(p.get("supplier_name") or "").strip().lower()
    inv_no = str(p.get("invoice_number") or "").strip()
    return (sup, inv_no)


def _invoice_lookup_key(inv: dict) -> tuple[str, str]:
    sup = str(inv.get("supplier_name") or "").strip().lower()
    inv_no = str(inv.get("invoice_number") or "").strip()
    return (sup, inv_no)


def _build_payment_index(payments: list[dict]) -> dict[tuple[str, str], dict]:
    """Eén payment per (supplier, invoice_number); bij duplicaten geen van beide."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in payments:
        groups[_payment_lookup_key(p)].append(p)
    out: dict[tuple[str, str], dict] = {}
    for key, lst in groups.items():
        if len(lst) > 1:
            logger.warning(
                "Dubbele betalingsregel voor (supplier, invoice_number)=%r — %d rijen; "
                "golden writes voor deze sleutel worden overgeslagen.",
                key,
                len(lst),
            )
            continue
        out[key] = lst[0]
    return out


def _parsed_amount_status_raw(payment: dict) -> str:
    dt = payment.get("decision_trace")
    if not isinstance(dt, dict):
        return ""
    snap = dt.get("reconciliation_snapshot")
    if not isinstance(snap, dict):
        return ""
    par = snap.get("parsed_amount_result")
    if not isinstance(par, dict):
        return ""
    return str(par.get("status") or "").strip()


def _supplier_match_status(payment: dict) -> str:
    dt = payment.get("decision_trace")
    if not isinstance(dt, dict):
        return ""
    return str(dt.get("supplier_match_status") or "").strip()


def _parse_session_date(raw: str) -> date:
    return datetime.strptime(raw.strip(), "%Y-%m-%d").date()


def _amount_to_json_string(amount: object) -> str:
    if isinstance(amount, Decimal):
        d = amount
    else:
        d = Decimal(str(amount))
    return str(d.quantize(_MONEY_Q, rounding=ROUND_HALF_UP))


def _golden_payload(payment: dict, *, amount_status: str) -> dict[str, str]:
    return {
        "invoice_number": str(payment.get("invoice_number") or ""),
        "supplier_name": str(payment.get("supplier_name") or ""),
        "amount": _amount_to_json_string(payment.get("amount")),
        "iban": str(payment.get("iban") or ""),
        "amount_status": amount_status,
        "match_status": _supplier_match_status(payment),
        "execution_date": str(payment.get("execution_date") or "").strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bouw golden dataset JSON per PDF-factuur.")
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=Path("golden_input"),
        help="Map met PDF-facturen (niet recursief).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("tests/golden_dataset"),
        help="Doelmap voor JSON-bestanden (één per PDF-stem).",
    )
    parser.add_argument(
        "--user-data-dir",
        type=Path,
        default=None,
        help="Gegevensmap (settings.json, suppliers.json). Default: bootstrap zoals de app.",
    )
    parser.add_argument(
        "--settings-path",
        type=Path,
        default=None,
        help="Override pad naar settings.json (anders: {user_data_dir}/settings.json).",
    )
    parser.add_argument(
        "--session-date",
        type=str,
        default="",
        help="YYYY-MM-DD voor calculate_payments (execution_date). Default: vandaag.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s  %(message)s",
        stream=sys.stderr,
    )

    input_dir = args.input.expanduser().resolve()
    output_dir = args.output.expanduser().resolve()
    user_data_dir = (
        args.user_data_dir.expanduser().resolve()
        if args.user_data_dir is not None
        else read_user_data_root(APP_BASE)
    )
    settings_path = (
        args.settings_path.expanduser().resolve()
        if args.settings_path is not None
        else user_data_dir / "settings.json"
    )

    if not input_dir.is_dir():
        logger.error("Invoermap bestaat niet of is geen map: %s", input_dir)
        return 1

    settings = load_settings(str(settings_path))
    debtor = merge_debtor_with_defaults(settings.get("debtor"))
    debtor_iban = debtor.get("iban") or None
    debtor_kvk = debtor.get("kvk") or None
    debtor_vat = debtor.get("vat") or None
    if not (debtor_iban or "").strip():
        debtor_iban = None
    if not (debtor_kvk or "").strip():
        debtor_kvk = None
    if not (debtor_vat or "").strip():
        debtor_vat = None

    if args.session_date.strip():
        try:
            session_d = _parse_session_date(args.session_date)
        except ValueError:
            logger.error("Ongeldige --session-date (verwacht YYYY-MM-DD): %r", args.session_date)
            return 1
    else:
        session_d = date.today()

    invoices = load_invoices_from_folder(
        input_dir,
        debtor_iban=debtor_iban,
        debtor_kvk=debtor_kvk,
        debtor_vat=debtor_vat,
    )
    db = SupplierDB(path=str(user_data_dir / "suppliers.json"))
    matched = match_suppliers(invoices, db)
    matched = enrich_credit_documents(matched)
    strip_raw_text_from_invoices(matched)
    engine_result = calculate_payments(matched, session_date=session_d)
    payment_index = build_payment_index_from_engine(engine_result)
    errors = review_documents_as_error_buckets(engine_result.review_documents)

    logger.info(
        "Pipeline: %d factuurdict(s) uit map, %d betalingsregel(s), %d error-bucket(s).",
        len(matched),
        len(engine_result.settlement_groups),
        len(errors),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for inv in matched:
        src = str(inv.get("source_file") or "").strip()
        if not src.lower().endswith(".pdf"):
            continue
        pdf_path = Path(src)
        stem = pdf_path.stem

        if inv.get("load_error"):
            logger.warning("Skip %s: load_error=%r", stem, inv.get("load_error"))
            continue
        if str(inv.get("match_status") or "") == "load_failed":
            logger.warning("Skip %s: match_status=load_failed", stem)
            continue

        doc_type = str(inv.get("type") or "invoice")
        key = _invoice_lookup_key(inv)
        payment = payment_index.get(key)

        if doc_type == "credit_note":
            logger.warning(
                "Skip %s: creditnota (geen eigen SEPA-regel per PDF in deze builder).",
                stem,
            )
            continue

        if payment is None:
            logger.warning(
                "Skip %s: geen betalingsregel (match_status=%r, invoice_number=%r).",
                stem,
                inv.get("match_status"),
                inv.get("invoice_number"),
            )
            continue

        amt_st_raw = _parsed_amount_status_raw(payment)
        if amt_st_raw.lower() != "confirmed":
            logger.warning(
                "Skip %s: amount_status=%r (vereist 'confirmed' voor automatische golden write).",
                stem,
                amt_st_raw or "(leeg)",
            )
            continue

        out_path = output_dir / f"{stem}.json"
        payload = _golden_payload(payment, amount_status=amt_st_raw)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        saved += 1

    print(f"{saved} invoices opgeslagen in golden dataset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
