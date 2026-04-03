"""Laad factuurdicts uit alle PDF-bestanden in een map (één dict per bestand)."""

from __future__ import annotations

from pathlib import Path

from parser.pdf_parser import extract_invoice_data, extract_text_strict


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
        out.append(data)
    return out
