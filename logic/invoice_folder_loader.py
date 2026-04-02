"""Laad factuurdicts uit alle PDF-bestanden in een map (één dict per bestand)."""

from __future__ import annotations

from pathlib import Path

from parser.pdf_parser import extract_invoice_data, extract_text


def load_invoices_from_folder(
    folder: Path,
    *,
    debtor_iban: str | None = None,
) -> list[dict]:
    """
    Lees elke ``*.pdf`` in ``folder`` (niet recursief), parse naar factuurdict.

    Elk dict bevat alle sleutels van ``extract_invoice_data`` behalve ``raw_text``
    (geheugen); plus ``source_file`` als absoluut pad-string.
    """
    folder = folder.resolve()
    if not folder.is_dir():
        return []

    out: list[dict] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        text = extract_text(str(path))
        data = extract_invoice_data(text, debtor_iban=debtor_iban)
        data.pop("raw_text", None)
        data["source_file"] = str(path.resolve())
        out.append(data)
    return out
