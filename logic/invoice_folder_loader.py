"""Laad factuurdicts uit alle PDF-bestanden in een map (één dict per bestand)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from parser.pdf_parser import (
    extract_ibans_from_images,
    extract_invoice_date,
    extract_invoice_data,
    extract_ocr_supplement_text,
    extract_text_force_raster_ocr,
    extract_text_from_images,
    extract_text_strict,
)
from logic.invoice_parse_cache import list_invoice_pdf_paths
from logic.validation import mask_iban_for_log

# #region agent log (debug mode - session 935dd7)
_DEBUG_935_PATH = "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-935dd7.log"
_DEBUG_935_SESSION = "935dd7"


def _dbg_935(hypothesis_id: str, location: str, message: str, data: dict, run_id: str) -> None:
    try:
        payload = {
            "sessionId": _DEBUG_935_SESSION,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
            "runId": run_id,
        }
        with open(_DEBUG_935_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return

# #endregion

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

# #region agent log (debug mode - session 10a5df)
_DEBUG_10A5DF_PATH = "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-10a5df.log"
_DEBUG_10A5DF_SESSION = "10a5df"


def _dbg_10a5df(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "repro") -> None:
    try:
        payload = {
            "sessionId": _DEBUG_10A5DF_SESSION,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
            "runId": run_id,
        }
        with open(_DEBUG_10A5DF_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return


# #endregion

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


def strip_raw_text_from_invoices(invoices: list[dict]) -> None:
    """Verwijder ``raw_text`` uit factuurdicts na ``match_suppliers`` (geheugen)."""
    for inv in invoices:
        inv.pop("raw_text", None)


def load_invoice_from_pdf_path(
    path: Path,
    *,
    debtor_iban: str | None = None,
    debtor_kvk: str | None = None,
    debtor_vat: str | None = None,
) -> dict:
    """
    Parse één factuur-PDF naar factuurdict (inclusief OCR-verrijking indien nodig).

    Bij leesfouten of ontbrekende tekstlaag: dict met ``load_error``.
    """
    path = path.resolve()
    run_id = "loader-run"
    try:
        text = extract_text_strict(str(path))
    except Exception:
        return _invoice_load_error_dict(path, "read_failed")

    ocr_supplement = ""
    ocr_supplement_error: str | None = None
    try:
        ocr_supplement = extract_ocr_supplement_text(str(path)) or ""
    except Exception as exc:
        ocr_supplement_error = f"{type(exc).__name__}"

    primary_text = text or ""
    sup_stripped = (ocr_supplement or "").strip()
    ocr_text = sup_stripped

    if not (primary_text or "").strip():
        return _invoice_load_error_dict(path, "no_text")

    try:
        data = extract_invoice_data(
            primary_text,
            ocr_text=ocr_text,
            debtor_iban=debtor_iban,
            debtor_kvk=debtor_kvk,
            debtor_vat=debtor_vat,
        )
    except Exception:
        return _invoice_load_error_dict(path, "read_failed")

    data["source_file"] = str(path.resolve())
    # Contract: keep raw_text as primary PDF text layer (no OCR amounts noise).
    data["raw_text"] = primary_text
    data["ocr_text"] = ocr_text
    data["ocr_supplement_len"] = int(len(ocr_text))
    data["ocr_supplement_error"] = ocr_supplement_error

    _dbg_935(
        "H0",
        "logic/invoice_folder_loader.py:load_invoice_from_pdf_path",
        "post extract_invoice_data (before OCR enrichment)",
        {
            "pdf": path.name,
            "pdf_text_len": int(len(text or "")),
            "has_supplier_hint": bool(str(data.get("supplier_hint") or "").strip()),
            "has_iban": bool(str(data.get("iban") or "").strip()),
            "iban_masked": mask_iban_for_log(str(data.get("iban") or "").strip())
            if str(data.get("iban") or "").strip()
            else None,
            "customer_number": str(data.get("customer_number") or "").strip(),
            "invoice_number": str(data.get("invoice_number") or "").strip(),
            "amount_status": str((data.get("amount_result") or {}).get("status") or ""),
            "amount_source": str((data.get("amount_result") or {}).get("source") or ""),
            "has_vat": bool(str(data.get("vat_number") or "").strip()),
            "has_kvk": bool(str(data.get("kvk_number") or "").strip()),
            "has_email_domain": bool(str(data.get("email_domain") or "").strip()),
        },
        run_id,
    )
    # #region agent log (debug mode - session 10a5df)
    try:
        ar = data.get("amount_result") if isinstance(data.get("amount_result"), dict) else {}
        _dbg_10a5df(
            "R1",
            "logic/invoice_folder_loader.py:load_invoice_from_pdf_path",
            "per_pdf_parse_and_ocr_summary",
            {
                "pdf": path.name,
                "iban_present": bool(str(data.get("iban") or "").strip()),
                "iban_masked": mask_iban_for_log(str(data.get("iban") or "").strip())
                if str(data.get("iban") or "").strip()
                else None,
                "all_ibans_count": int(len(data.get("all_ibans") or [])),
                "ocr_iban_attempted": bool(data.get("ocr_iban_attempted")),
                "ocr_iban_error": str(data.get("ocr_iban_error") or ""),
                "invoice_number": str(data.get("invoice_number") or ""),
                "customer_number": str(data.get("customer_number") or ""),
                "amount_status": str(ar.get("status") or ""),
                "amount_source": str(ar.get("source") or ""),
                "amount_candidates_count": int(len(ar.get("candidates") or [])),
                "amount_candidates_brief": [
                    {
                        "v": str(c.get("value") or ""),
                        "src": str(c.get("source") or ""),
                        "cf": int(c.get("confidence") or 0),
                        "ty": str(c.get("type") or ""),
                    }
                    for c in (ar.get("candidates") or [])[:6]
                    if isinstance(c, dict)
                ],
            },
            run_id="repro",
        )
    except Exception:
        pass
    # #endregion

    try:
        pdf = path.name
        if pdf.casefold() in {"aluned 502601306.pdf", "bauder 24065433.pdf"}:
            _dbg_a6(
                "L1",
                "logic/invoice_folder_loader.py:load_invoice_from_pdf_path",
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

    skipped_ocr_hint = False
    skipped_ocr_iban = False

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
    else:
        skipped_ocr_hint = True

    data["ocr_iban_attempted"] = True
    data["ocr_iban_error"] = None
    try:
        from parser.iban_candidates import (
            extract_iban_result,
            iban_values_from_candidates,
            merge_ocr_into_iban_result,
        )

        ocr_ibans = extract_ibans_from_images(str(path)) or []
        if not ocr_ibans:
            ocr_ibans = extract_ibans_from_images(str(path)) or []
        if ocr_ibans:
            existing_ir = data.get("iban_result")
            if isinstance(existing_ir, dict):
                ir = merge_ocr_into_iban_result(
                    existing_ir,
                    ocr_ibans,
                    debtor_iban=debtor_iban,
                )
            else:
                ir = extract_iban_result(
                    "",
                    debtor_iban=debtor_iban,
                    ocr_ibans=ocr_ibans,
                    resolved=data.get("iban"),
                    resolved_source="ocr",
                )
            data["iban_result"] = ir.to_dict()
            data["iban"] = ir.value
            data["all_ibans"] = iban_values_from_candidates(ir.candidates)
    except Exception as exc:
        data["ocr_iban_error"] = f"{type(exc).__name__}"

    # If key supplier-identification signals are missing, do a lightweight OCR enrichment pass.
    # This addresses invoices where VAT/KvK/email live only in a logo/header image even when IBAN is present.
    ocr_ident_attempted = False
    ocr_ident_error = None
    ocr_ident_text_len = 0
    if not str(data.get("vat_number") or "").strip() or not str(data.get("kvk_number") or "").strip() or not str(data.get("email_domain") or "").strip():
        try:
            ocr_ident_attempted = True
            ocr_text_ident = sup_stripped or extract_text_from_images(str(path)) or ""
            if not str(data.get("vat_number") or "").strip() or not str(data.get("kvk_number") or "").strip() or not str(data.get("email_domain") or "").strip():
                raster_extra = extract_text_force_raster_ocr(str(path), max_pages=1) or ""
                if raster_extra.strip() and raster_extra.strip() not in ocr_text_ident:
                    ocr_text_ident = f"{ocr_text_ident.rstrip()}\n{raster_extra.strip()}".strip()
            ocr_ident_text_len = int(len(ocr_text_ident or ""))
            if ocr_text_ident:
                # Email domain
                if not str(data.get("email_domain") or "").strip():
                    # Tolerate OCR spaces: "info @ felison . nl"
                    m = re.search(
                        r"\b[A-Za-z0-9._%+-]+\s*@\s*([A-Za-z0-9.-]+\s*\.\s*[A-Za-z]{2,})\b",
                        ocr_text_ident,
                    )
                    if m:
                        dom = re.sub(r"\s+", "", str(m.group(1) or "")).strip().lower()
                        data["email_domain"] = dom or None
                # KvK
                if not str(data.get("kvk_number") or "").strip():
                    # Accept spaced/dotted digits around KvK label.
                    m = re.search(r"(?i)\b(?:kvk|k\.?v\.?k\.?|kvk\s*nr\.?)\D{0,24}([\d\.\s]{7,16})\b", ocr_text_ident)
                    if m:
                        digits = re.sub(r"\D", "", str(m.group(1) or ""))
                        if len(digits) in (7, 8):
                            data["kvk_number"] = digits
                # VAT
                if not str(data.get("vat_number") or "").strip():
                    # Accept spaced/punctuated OCR: "N L 123456789 B 01" / "NL 123456789B01"
                    m = re.search(r"(?i)\bN\s*L\s*\d{9}\s*B\s*\d{2}\b", ocr_text_ident)
                    if not m:
                        m = re.search(r"(?i)\bNL\d{9}\s*B\d{2}\b", ocr_text_ident)
                    if not m:
                        # Dotted grouping seen in Felison OCR: "Btw NL8053.01.021.B.01"
                        m = re.search(
                            r"(?i)\bNL\s*\d{4}[\s.\-]*\d{2}[\s.\-]*\d{3}[\s.\-]*B[\s.\-]*\d{2}\b",
                            ocr_text_ident,
                        )
                    if not m:
                        m = re.search(
                            r"(?i)\b(?:btw|vat)\s*:\s*([\d.\s]+B[\d.\s]+)",
                            ocr_text_ident,
                        )
                    if m:
                        raw = re.sub(r"[^0-9A-Za-z]", "", str(m.group(0) or "")).upper()
                        if re.fullmatch(r"NL\d{9}B\d{2}", raw):
                            data["vat_number"] = raw

                # Debug (Felison focus): show whether OCR text contains our key hints.
                try:
                    pdf_cf = path.name.casefold()
                    if "felison" in pdf_cf:
                        sample_lines = []
                        for ln in (ocr_text_ident or "").splitlines():
                            low = (ln or "").lower()
                            if any(k in low for k in ("kvk", "btw", "vat", "@", "mail")):
                                # Avoid logging full email addresses; keep only line with @ redacted.
                                safe = re.sub(r"[A-Za-z0-9._%+-]+\s*@\s*[A-Za-z0-9.-]+\s*\.\s*[A-Za-z]{2,}", "<email>", ln)
                                sample_lines.append(re.sub(r"\s+", " ", safe).strip()[:160])
                            if len(sample_lines) >= 8:
                                break
                        # Additional safe stats: detect patterns even if keywords are missing.
                        vat_like = bool(re.search(r"(?i)\bN\s*L\s*\d{9}\s*B\s*\d{2}\b", ocr_text_ident))
                        email_like = bool(re.search(r"\b[A-Za-z0-9._%+-]+\s*@\s*[A-Za-z0-9.-]+\s*\.\s*[A-Za-z]{2,}\b", ocr_text_ident))
                        domain_like = bool(re.search(r"\b(?:www\.)?[A-Za-z0-9-]+\s*\.\s*(?:nl|com|net|eu)\b", ocr_text_ident, flags=re.IGNORECASE))
                        kvk_like = bool(re.search(r"(?i)\b(?:kvk|k\.?v\.?k\.?)\b", ocr_text_ident))
                        domain_preview = None
                        m_dom = re.search(
                            r"(?i)\b(?:www\.)?[A-Za-z0-9-]+\s*\.\s*(?:nl|com|net|eu)\b",
                            ocr_text_ident,
                        )
                        if m_dom:
                            domain_preview = re.sub(r"\s+", "", str(m_dom.group(0) or "")).lower()
                        _dbg_935(
                            "H9",
                            "logic/invoice_folder_loader.py:load_invoice_from_pdf_path",
                            "Felison OCR-ident hint scan",
                            {
                                "pdf": path.name,
                                "ocr_ident_text_len": int(ocr_ident_text_len),
                                "found_vat": bool(str(data.get("vat_number") or "").strip()),
                                "found_kvk": bool(str(data.get("kvk_number") or "").strip()),
                                "found_email_domain": bool(str(data.get("email_domain") or "").strip()),
                                "vat_like_present": vat_like,
                                "email_like_present": email_like,
                                "domain_like_present": domain_like,
                                "domain_preview": domain_preview,
                                "kvk_keyword_present": kvk_like,
                                "sample_lines": sample_lines,
                            },
                            run_id,
                        )
                except Exception:
                    pass
        except Exception as exc:
            ocr_ident_error = f"{type(exc).__name__}"

    data["ocr_ident_attempted"] = bool(ocr_ident_attempted)
    data["ocr_ident_error"] = ocr_ident_error
    data["ocr_ident_text_len"] = int(ocr_ident_text_len or 0)

    # --- Invoice date OCR repair (Frige-like headers) ---
    # If the selected invoice_date appears on a "verval/due" line in the PDF text,
    # it is likely the due date. In that case, try OCR text for header patterns
    # (e.g. "Nr ... van 30-1-2026") and overwrite invoice_date when found.
    try:
        inv_date = str(data.get("invoice_date") or "").strip()
        suspicious_due = False
        if inv_date:
            from parser.pdf_parser import _DD_MM_YYYY_RE, _ISO_DATE_RE, _iso_from_dmy  # type: ignore

            for ln in (text or "").splitlines():
                low = (ln or "").lower()
                if "vervaldatum" in low or "due date" in low or "verval" in low or "due" in low:
                    # Compare any date token on that line to inv_date (ISO).
                    m_iso = _ISO_DATE_RE.search(ln)
                    if m_iso:
                        tok_iso = f"{m_iso.group(1)}-{m_iso.group(2)}-{m_iso.group(3)}"
                        if tok_iso == inv_date:
                            suspicious_due = True
                            break
                    m_dmy = _DD_MM_YYYY_RE.search(ln)
                    if m_dmy:
                        tok_iso = _iso_from_dmy(int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3)))
                        if tok_iso and tok_iso == inv_date:
                            suspicious_due = True
                            break
        if suspicious_due:
            ocr_text_date = extract_text_from_images(str(path)) or ""
            ocr_date, ocr_src = extract_invoice_date(ocr_text_date)
            if (not ocr_date) or int(len(ocr_text_date)) < 120:
                # Stronger fallback: force raster OCR of first page for header blocks.
                raster_text = extract_text_force_raster_ocr(str(path), max_pages=1) or ""
                if len(raster_text) > len(ocr_text_date):
                    ocr_text_date = raster_text
                    ocr_date, ocr_src = extract_invoice_date(ocr_text_date)
            if ocr_date and str(ocr_date).strip() and ocr_date != inv_date:
                data["invoice_date"] = ocr_date
                data["invoice_date_source"] = "ocr"
    except Exception as exc:
        pass

    # Keep invoice_date snapshot in sync after OCR/date repair paths.
    try:
        from parser.pdf_parser import build_invoice_date_result_snapshot

        synced_date = str(data.get("invoice_date") or "").strip() or None
        date_dict = build_invoice_date_result_snapshot(
            primary_text,
            invoice_date=synced_date,
            invoice_date_source=str(data.get("invoice_date_source") or "").strip() or None,
        )
        if synced_date and date_dict.get("selected_value") != synced_date:
            date_dict["value"] = synced_date
            date_dict["selected_value"] = synced_date
            if str(data.get("invoice_date_source") or "").strip():
                date_dict["source"] = str(data.get("invoice_date_source") or "").strip()
            date_dict["status"] = "confirmed"
        data["invoice_date_result"] = date_dict
    except Exception:
        pass

    _dbg_935(
        "H0",
        "logic/invoice_folder_loader.py:load_invoice_from_pdf_path",
        "after OCR enrichment",
        {
            "pdf": path.name,
            "skipped_ocr_hint": bool(skipped_ocr_hint),
            "skipped_ocr_iban": bool(skipped_ocr_iban),
            "ocr_hint_attempted": bool(data.get("ocr_hint_attempted")),
            "ocr_hint_error": data.get("ocr_hint_error"),
            "ocr_hint_text_len": int(data.get("ocr_hint_text_len") or 0),
            "ocr_iban_attempted": bool(data.get("ocr_iban_attempted")),
            "ocr_iban_error": data.get("ocr_iban_error"),
            "ocr_ident_attempted": bool(data.get("ocr_ident_attempted")),
            "ocr_ident_error": data.get("ocr_ident_error"),
            "ocr_ident_text_len": int(data.get("ocr_ident_text_len") or 0),
            "has_supplier_hint": bool(str(data.get("supplier_hint") or "").strip()),
            "has_iban": bool(str(data.get("iban") or "").strip()),
            "iban_masked": mask_iban_for_log(str(data.get("iban") or "").strip())
            if str(data.get("iban") or "").strip()
            else None,
            "has_vat": bool(str(data.get("vat_number") or "").strip()),
            "has_kvk": bool(str(data.get("kvk_number") or "").strip()),
            "has_email_domain": bool(str(data.get("email_domain") or "").strip()),
        },
        run_id,
    )

    try:
        pdf = path.name
        if pdf.casefold() in {"aluned 502601306.pdf", "bauder 24065433.pdf"}:
            _dbg_a6(
                "L2",
                "logic/invoice_folder_loader.py:load_invoice_from_pdf_path",
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
    return data


def load_invoices_from_folder(
    folder: Path,
    *,
    debtor_iban: str | None = None,
    debtor_kvk: str | None = None,
    debtor_vat: str | None = None,
) -> list[dict]:
    """
    Lees elke ``*.pdf`` in ``folder`` (niet recursief), parse naar factuurdict.

    Elk dict bevat alle sleutels van ``extract_invoice_data`` inclusief ``raw_text``
    (voor profiel-extractie in ``match_suppliers``; wordt later gestript via
    ``strip_raw_text_from_invoices``), plus ``source_file`` als absoluut pad-string.

    Bij leesfouten of ontbrekende tekstlaag: dict met ``load_error`` (``read_failed`` /
    ``no_text``) i.p.v. stille lege parse.
    """
    folder = folder.resolve()
    if not folder.is_dir():
        return []

    return [
        load_invoice_from_pdf_path(
            path,
            debtor_iban=debtor_iban,
            debtor_kvk=debtor_kvk,
            debtor_vat=debtor_vat,
        )
        for path in list_invoice_pdf_paths(folder)
    ]
