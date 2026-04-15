"""Laad factuurdicts uit alle PDF-bestanden in een map (één dict per bestand)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from parser.pdf_parser import (
    extract_ibans_from_images,
    extract_invoice_data,
    extract_text_from_images,
    extract_text_strict,
)
from logic.validation import mask_iban_for_log

# #region agent log (debug mode - session a6a30a)
_DEBUG_A6_PATH = "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-a6a30a.log"
_DEBUG_A6_SESSION = "a6a30a"


def _dbg_a6(hypothesis_id: str, location: str, message: str, data: dict, run_id: str) -> None:
    try:
        payload = {
            "sessionId": _DEBUG_A6_SESSION,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
            "runId": run_id,
        }
        with open(_DEBUG_A6_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return

# #endregion

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
        "amount_source": "LOAD_FAILED",
        "amount_confidence": "missing",
        "amount_result": {
            "candidates": [],
            "value": None,
            "confidence": 0,
            "source": "LOAD_FAILED",
            "status": "failed",
            # Backward-compatible keys
            "selected_amount": None,
            "amount_confidence": 0,
            "amount_status": "failed",
        },
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
    debtor_kvk: str | None = None,
    debtor_vat: str | None = None,
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
        run_id = "loader-run"
        try:
            text = extract_text_strict(str(path))
        except Exception:
            out.append(_invoice_load_error_dict(path, "read_failed"))
            continue
        if not (text or "").strip():
            out.append(_invoice_load_error_dict(path, "no_text"))
            continue
        try:
            data = extract_invoice_data(
                text,
                debtor_iban=debtor_iban,
                debtor_kvk=debtor_kvk,
                debtor_vat=debtor_vat,
            )
        except Exception:
            out.append(_invoice_load_error_dict(path, "read_failed"))
            continue
        data.pop("raw_text", None)
        data["source_file"] = str(path.resolve())

        try:
            pdf = path.name
            if pdf.casefold() in {"aluned 502601306.pdf", "bauder 24065433.pdf"}:
                _dbg_a6(
                    "L1",
                    "logic/invoice_folder_loader.py:load_invoices_from_folder",
                    "post extract_invoice_data (before OCR enrichment)",
                    {
                        "pdf": pdf,
                        "has_supplier_hint": bool(str(data.get("supplier_hint") or "").strip()),
                        "has_iban": bool(str(data.get("iban") or "").strip()),
                        "iban_masked": mask_iban_for_log(str(data.get("iban") or "").strip())
                        if str(data.get("iban") or "").strip()
                        else None,
                        "customer_number": str(data.get("customer_number") or "").strip(),
                        "invoice_number": str(data.get("invoice_number") or "").strip(),
                    },
                    run_id,
                )
        except Exception:
            pass

        if not data.get("supplier_hint"):
            data["ocr_hint_attempted"] = True
            data["ocr_hint_error"] = None
            try:
                # OCR can be flaky depending on runtime deps; try twice without delays.
                ocr_text_hint = extract_text_from_images(str(path)) or ""
                if not ocr_text_hint.strip():
                    ocr_text_hint = extract_text_from_images(str(path)) or ""
                hint = _supplier_hint_from_ocr_text(ocr_text_hint)
                if hint:
                    data["supplier_hint"] = hint
                data["ocr_hint_text_len"] = int(len(ocr_text_hint or ""))
            except Exception as exc:
                data["ocr_hint_error"] = f"{type(exc).__name__}"
                data["ocr_hint_text_len"] = 0
        if not data.get("iban"):
            data["ocr_iban_attempted"] = True
            data["ocr_iban_error"] = None
            try:
                ocr_ibans = extract_ibans_from_images(str(path)) or []
                if not ocr_ibans:
                    ocr_ibans = extract_ibans_from_images(str(path)) or []
                if ocr_ibans:
                    data["iban"] = ocr_ibans[0]
            except Exception as exc:
                data["ocr_iban_error"] = f"{type(exc).__name__}"

        try:
            pdf = path.name
            if pdf.casefold() in {"aluned 502601306.pdf", "bauder 24065433.pdf"}:
                _dbg_a6(
                    "L2",
                    "logic/invoice_folder_loader.py:load_invoices_from_folder",
                    "after OCR enrichment",
                    {
                        "pdf": pdf,
                        "has_supplier_hint": bool(str(data.get("supplier_hint") or "").strip()),
                        "has_iban": bool(str(data.get("iban") or "").strip()),
                        "iban_masked": mask_iban_for_log(str(data.get("iban") or "").strip())
                        if str(data.get("iban") or "").strip()
                        else None,
                        "ocr_hint_attempted": bool(data.get("ocr_hint_attempted")),
                        "ocr_hint_error": data.get("ocr_hint_error"),
                        "ocr_hint_text_len": int(data.get("ocr_hint_text_len") or 0),
                        "ocr_iban_attempted": bool(data.get("ocr_iban_attempted")),
                        "ocr_iban_error": data.get("ocr_iban_error"),
                    },
                    run_id,
                )
        except Exception:
            pass
        out.append(data)
    return out
