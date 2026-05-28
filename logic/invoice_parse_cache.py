"""In-memory cache van geparste factuurdicts per map (geen OCR bij warm rematch)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from logic.payment_decisions import stable_hash


def list_invoice_pdf_paths(folder: Path) -> list[Path]:
    """Gesorteerde PDF-paden in ``folder`` (niet recursief, geen AppleDouble)."""
    folder = folder.resolve()
    if not folder.is_dir():
        return []
    out: list[Path] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        if path.name.startswith("."):
            continue
        out.append(path)
    return out


def batch_folder_fingerprint(
    folder: Path,
    *,
    debtor_iban: str | None = None,
    debtor_kvk: str | None = None,
    debtor_vat: str | None = None,
) -> str:
    """Fingerprint van map + PDF-mtimes + debiteur (invalidatie bij wijziging)."""
    folder = folder.resolve()
    pdf_parts: list[str] = []
    for path in list_invoice_pdf_paths(folder):
        try:
            st = path.stat()
            pdf_parts.append(f"{path.name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            pdf_parts.append(f"{path.name}:missing")
    return stable_hash(
        {
            "folder": str(folder),
            "pdfs": pdf_parts,
            "debtor_iban": (debtor_iban or "").strip(),
            "debtor_kvk": (debtor_kvk or "").strip(),
            "debtor_vat": (debtor_vat or "").strip(),
        }
    )


def index_invoices_by_source_file(invoices: list[dict]) -> dict[str, dict]:
    """Index factuurdicts op genormaliseerd ``source_file``-pad."""
    out: dict[str, dict] = {}
    for inv in invoices:
        if not isinstance(inv, dict):
            continue
        sf = str(inv.get("source_file") or "").strip()
        if not sf:
            continue
        out[sf] = inv
        try:
            resolved = str(Path(sf).resolve())
            out[resolved] = inv
        except OSError:
            pass
    return out


class ParsedInvoiceBatchCache:
    """Sessie-cache: geparste facturen na cold load (vóór ``match_suppliers``)."""

    def __init__(self) -> None:
        self._invoices: list[dict] | None = None
        self._fingerprint: str | None = None

    def clear(self) -> None:
        self._invoices = None
        self._fingerprint = None

    def store(
        self,
        folder: Path,
        invoices: list[dict],
        *,
        debtor_iban: str | None = None,
        debtor_kvk: str | None = None,
        debtor_vat: str | None = None,
    ) -> None:
        self._invoices = deepcopy(invoices)
        self._fingerprint = batch_folder_fingerprint(
            folder,
            debtor_iban=debtor_iban,
            debtor_kvk=debtor_kvk,
            debtor_vat=debtor_vat,
        )

    def is_valid(
        self,
        folder: Path,
        *,
        debtor_iban: str | None = None,
        debtor_kvk: str | None = None,
        debtor_vat: str | None = None,
    ) -> bool:
        if self._invoices is None or self._fingerprint is None:
            return False
        return self._fingerprint == batch_folder_fingerprint(
            folder,
            debtor_iban=debtor_iban,
            debtor_kvk=debtor_kvk,
            debtor_vat=debtor_vat,
        )

    def get_parsed_invoices(
        self,
        folder: Path,
        *,
        debtor_iban: str | None = None,
        debtor_kvk: str | None = None,
        debtor_vat: str | None = None,
    ) -> list[dict] | None:
        if not self.is_valid(
            folder,
            debtor_iban=debtor_iban,
            debtor_kvk=debtor_kvk,
            debtor_vat=debtor_vat,
        ):
            return None
        return deepcopy(self._invoices or [])
