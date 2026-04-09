"""Laad factuurdicts uit alle PDF-bestanden in een map (één dict per bestand)."""

from __future__ import annotations

import re
from pathlib import Path

from parser.pdf_parser import (
    extract_ibans_from_images,
    extract_invoice_data,
    extract_text_from_images,
    extract_text_strict,
)

_SUPPLIER_HINT_NOISE = frozenset({
    "factuur", "invoice", "debiteur", "iban", "btw", "kvk", "adres", "afleveradres",
})

def _supplier_hint_from_ocr_text(text: str) -> str | None:
    tokens = re.findall(r"[A-Za-z][A-Za-z&\-]{2,}", text or "")
    for tok in tokens:
        t = tok.strip()
        if not t:
            continue
        if t.lower() in _SUPPLIER_HINT_NOISE:
            continue
        return t
    return None

def _invoice_load_error_dict(path: Path, code: str) -> dict:
    """Minimaal factuurdict bij mislukte of lege PDF; ``load_error`` triggert UI/engine."""
    return {
        "source_file": str(path.resolve()),
        "load_error": code,
        "iban": None,
        "all_ibans": [],
        "amount": None,
        "amount_excl_vat": None,
        "invoice_number": None,
        "customer_number": None,
        "invoice_date": None,
        "invoice_date_source": "missing",
        "description": None,
        "type": "invoice",
        "supplier_hint": None,
    }

def load_invoices_from_folder(
    folder: Path,
    *,
    debtor_iban: str | None = None,
) -> list[dict]:
    """
    Lees elke ``*.pdf`` in ``folder`` (niet recursief), parse naar factuurdict.

    Elk dict bevat alle sleutels van ``extract_invoice_data`` behalve ``raw_text``
    (geheugen); plus ``source_file`` als absoluut pad-string.

    Bij leesfouten of ontbrekende tekstlaag: dict met ``load_error`` (``read_failed`` /
    ``no_text``) i.p.v. stille lege parse.
    """
    folder = folder.resolve()
    if not folder.is_dir():
        return []

    out: list[dict] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        try:
            text = extract_text_strict(str(path))
        except Exception:
            out.append(_invoice_load_error_dict(path, "read_failed"))
            continue
        if not (text or "").strip():
            out.append(_invoice_load_error_dict(path, "no_text"))
            continue
        try:
            data = extract_invoice_data(text, debtor_iban=debtor_iban)
        except Exception:
            out.append(_invoice_load_error_dict(path, "read_failed"))
            continue
        data.pop("raw_text", None)
        data["source_file"] = str(path.resolve())
        if not data.get("supplier_hint"):
            try:
                ocr_text_hint = extract_text_from_images(str(path))
                hint = _supplier_hint_from_ocr_text(ocr_text_hint)
                if hint:
                    data["supplier_hint"] = hint
            except Exception:
                pass
        if not data.get("iban"):
            ocr_ibans = extract_ibans_from_images(str(path))
            if ocr_ibans:
                data["iban"] = ocr_ibans[0]
        out.append(data)
    return out
