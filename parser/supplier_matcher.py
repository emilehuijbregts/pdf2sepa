from __future__ import annotations

import logging
import re
from pathlib import Path
import json
import time

from logic.validation import is_plausible_iban, mask_iban_for_log
from logic.payment_amounts import normalize_supplier_vat_rate_pct
from parser.supplier_db import SupplierDB

logger = logging.getLogger(__name__)

# region agent log (debug mode - session 935dd7)
_DEBUG_935_PATH = "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-935dd7.log"
_DEBUG_935_SESSION = "935dd7"


def _dbg_935(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "pre-fix") -> None:
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

# endregion

# region agent log (debug mode - session a6a30a)
_DEBUG_A6_PATH = "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-a6a30a.log"
_DEBUG_A6_SESSION = "a6a30a"


def _dbg_a6(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "ui-run") -> None:
    # Keep minimal; no secrets/PII
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

# endregion

# region agent log
def _agent_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    try:
        import json, time  # noqa: E401

        payload = {
            "sessionId": "9a8545",
            "runId": "pre-fix",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(
            "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-9a8545.log",
            "a",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
# endregion

_SUPPLIER_NAME_NOISE = frozenset({
    "factuur",
    "invoice",
    "verkoopfactuur",
    "fact",
    "afleveradres",
    "factuuradres",
    "bestand",
    "onderwerp",
    "duister",
    "wouter",
    "beheer",
    "artikelen",
    "sales",
    "van",
    "nederland",
})
_SUPPLIER_PREFIX_NOISE = ("factuur", "invoice", "verkoopfactuur", "fact")

def _clean_supplier_candidate(raw: str) -> str:
    s = re.sub(r"\s+", " ", str(raw or "").strip())
    s = re.sub(r"^[\W_]+|[\W_]+$", "", s)
    return s.strip()

def _sanitize_supplier_hint(raw: str) -> str:
    s = _clean_supplier_candidate(raw)
    if not s:
        return ""
    s = re.sub(
        r"^(?i)(?:factuur|invoice|verkoopfactuur|afleveradres|factuuradres)\s+",
        "",
        s,
    )
    return _clean_supplier_candidate(s)

def _is_poor_supplier_candidate(raw: str) -> bool:
    s = _sanitize_supplier_hint(raw)
    if not s or len(s) < 3:
        return True
    first = s.split(" ", 1)[0].lower()
    if first in _SUPPLIER_PREFIX_NOISE:
        return True
    tokens = [t.lower() for t in re.findall(r"[A-Za-z]+", s)]
    if not tokens:
        return True
    noisy = sum(1 for t in tokens if t in _SUPPLIER_NAME_NOISE)
    return noisy >= max(1, len(tokens) // 2)

def _collect_match_signals(invoice: dict) -> list[str]:
    signals: list[str] = []
    if str(invoice.get("iban") or "").strip():
        signals.append("iban")
    if str(invoice.get("customer_number") or "").strip():
        signals.append("customer_number")
    if str(invoice.get("invoice_number") or "").strip():
        signals.append("invoice_number")
    if str(invoice.get("supplier_hint") or "").strip():
        signals.append("supplier_hint")
    if str(invoice.get("email_domain") or "").strip():
        signals.append("email_domain")
    if str(invoice.get("kvk_number") or "").strip():
        signals.append("kvk")
    if str(invoice.get("vat_number") or "").strip():
        signals.append("vat")
    return signals

def _db_core_matches(match_info: dict) -> list[str]:
    core: list[str] = []
    if match_info.get("iban_match"):
        core.append("IBAN")
    if match_info.get("customer_code_match"):
        core.append("Klantnummer")
    if match_info.get("kvk_match"):
        core.append("KvK")
    if match_info.get("vat_match"):
        core.append("BTW")
    if match_info.get("email_domain_match"):
        core.append("E-maildomein")
    return core

def _determine_match_status(match_info: dict) -> str:
    """Derive match status from the characteristics that matched.

    Rules (per plan):
      - 2+ independent characteristics  → confirmed
      - 1 primary + 1 secondary         → confirmed
      - 1 characteristic only           → needs_review
      - fuzzy match only                → needs_review
      - nothing                         → (caller sets unmatched/no_hint)

    Primary:   iban, customer_code
    Secondary: alias (exact/substring)
    Weak:      fuzzy (never sufficient alone for confirmed)
    """
    iban = match_info.get("iban_match", False)
    alias = match_info.get("alias_match", False)
    code = match_info.get("customer_code_match", False)
    fuzzy = match_info.get("fuzzy_match", False)

    kvk = match_info.get("kvk_match", False)
    vat = match_info.get("vat_match", False)
    email = match_info.get("email_domain_match", False)

    primary = sum([iban, code, kvk, vat])
    secondary = sum([1 if alias else 0, 1 if email else 0])

    if primary >= 2:
        return "confirmed"
    if primary >= 1 and secondary >= 1:
        return "confirmed"
    if code and fuzzy:
        return "confirmed"

    if iban or code or alias or fuzzy or kvk or vat or email:
        return "needs_review"

    return "unmatched"


def _is_unanchored_tax_only_match(match_info: dict) -> bool:
    """True when match relies only on VAT+KvK and has no identity anchor."""
    return bool(
        match_info.get("vat_match")
        and match_info.get("kvk_match")
        and not match_info.get("iban_match")
        and not match_info.get("customer_code_match")
        and not match_info.get("alias_match")
        and not match_info.get("email_domain_match")
    )

def match_suppliers(invoices: list[dict], db: SupplierDB) -> list[dict]:
    out: list[dict] = []
    db_is_empty = not bool(getattr(db, "suppliers", None))

    for invoice in invoices:
        if invoice.get("load_error"):
            src = str(invoice.get("source_file") or "")
            base = Path(src).name if src else "PDF"
            out.append({
                **invoice,
                "supplier_name": base,
                "match_status": "load_failed",
                "discount": 0.0,
                "supplier_payment_term_days_raw": 0,
                "supplier_term_trusted": False,
                "supplier_vat_rate": 21,
            })
            continue

        invoice_copy = invoice.copy()
        parsed_signals = _collect_match_signals(invoice_copy)
        invoice_copy["match_signals"] = parsed_signals
        invoice_copy["match_signal_count"] = len(parsed_signals)
        invoice_copy["match_signals_summary"] = ", ".join(parsed_signals)

        supplier, match_info = db.find_supplier_scored(
            invoice.get("supplier_hint"),
            invoice.get("iban"),
            invoice.get("customer_number"),
            vat_number=invoice.get("vat_number"),
            kvk_number=invoice.get("kvk_number"),
            email_domain=invoice.get("email_domain"),
        )

        if supplier:
            try:
                src = str(invoice.get("source_file") or "")
                pdf = Path(src).name if src else ""
            except Exception:
                pdf = ""
            _dbg_935(
                "H6",
                "parser/supplier_matcher.py:match_suppliers",
                "db.find_supplier_scored returned supplier",
                {
                    "pdf": pdf,
                    "supplier_db_name": str(supplier.get("name") or ""),
                    "invoice_signals": invoice_copy.get("match_signals") or [],
                    "match_info_flags": {
                        k: bool(match_info.get(k))
                        for k in (
                            "iban_match",
                            "customer_code_match",
                            "alias_match",
                            "fuzzy_match",
                            "kvk_match",
                            "vat_match",
                            "email_domain_match",
                            "ocr_confirmed",
                        )
                    },
                    "fuzzy_score": float(match_info.get("fuzzy_score") or 0.0),
                    "iban_masked": mask_iban_for_log(str(invoice.get("iban") or "").strip())
                    if str(invoice.get("iban") or "").strip()
                    else None,
                    "has_vat": bool(str(invoice.get("vat_number") or "").strip()),
                    "has_kvk": bool(str(invoice.get("kvk_number") or "").strip()),
                    "email_domain": str(invoice.get("email_domain") or "").strip(),
                },
                run_id="pre-fix",
            )
            invoice_copy["supplier_match_source"] = "db_match"
            invoice_copy["match_info"] = match_info
            core_matches = _db_core_matches(match_info)
            invoice_copy["db_core_matches"] = core_matches
            invoice_copy["db_core_match_count"] = len(core_matches)
            invoice_copy["db_core_match_confirmed"] = len(core_matches) >= 2

            status = _determine_match_status(match_info)
            try:
                src = str(invoice.get("source_file") or "")
                pdf = Path(src).name if src else ""
                if pdf.casefold() in {"aluned 502601306.pdf", "bauder 24065433.pdf"}:
                    _dbg_a6(
                        "SM1",
                        "parser/supplier_matcher.py:match_suppliers",
                        "match computed for target PDF",
                        {
                            "pdf": pdf,
                            "supplier_db_name": str(supplier.get("name") or ""),
                            "status_initial": status,
                            "match_signals": invoice_copy.get("match_signals") or [],
                            "db_core_matches": invoice_copy.get("db_core_matches") or [],
                            "match_info_flags": {
                                k: bool(match_info.get(k))
                                for k in (
                                    "iban_match",
                                    "customer_code_match",
                                    "alias_match",
                                    "fuzzy_match",
                                    "kvk_match",
                                    "vat_match",
                                    "email_domain_match",
                                    "ocr_confirmed",
                                )
                            },
                            "iban_masked": mask_iban_for_log(str(invoice.get("iban") or "").strip())
                            if str(invoice.get("iban") or "").strip()
                            else None,
                        },
                    )
            except Exception:
                pass
            _agent_log(
                "H1",
                "parser/supplier_matcher.py:match_suppliers",
                "initial match status computed",
                {
                    "supplier": str(supplier.get("name") or ""),
                    "status_initial": status,
                    "core_matches": core_matches,
                    "match_info_keys": {k: bool(match_info.get(k)) for k in (
                        "iban_match",
                        "customer_code_match",
                        "alias_match",
                        "fuzzy_match",
                        "kvk_match",
                        "vat_match",
                        "email_domain_match",
                        "ocr_confirmed",
                    )},
                    "pdf_iban_masked": mask_iban_for_log(str(invoice.get("iban") or "").strip()) if str(invoice.get("iban") or "").strip() else None,
                },
            )

            if status == "needs_review":
                status = _try_ocr_upgrade(invoice, supplier, match_info, db)
                try:
                    src = str(invoice.get("source_file") or "")
                    pdf = Path(src).name if src else ""
                    if pdf.casefold() in {"aluned 502601306.pdf", "bauder 24065433.pdf"}:
                        _dbg_a6(
                            "SM2",
                            "parser/supplier_matcher.py:match_suppliers",
                            "after OCR upgrade attempt",
                            {"pdf": pdf, "status_after_ocr": status, "ocr_confirmed": bool(match_info.get("ocr_confirmed"))},
                        )
                except Exception:
                    pass

            # Strict safety guard:
            # Never auto-confirm solely on VAT+KvK when no direct identity anchors match.
            # This prevents cross-supplier misclassification when tax identifiers are
            # extracted from an unrelated section of the document.
            if status == "confirmed" and _is_unanchored_tax_only_match(match_info):
                has_invoice_identity_signals = bool(
                    str(invoice.get("supplier_hint") or "").strip()
                    or str(invoice.get("email_domain") or "").strip()
                    or str(invoice.get("iban") or "").strip()
                    or str(invoice.get("customer_number") or "").strip()
                )
                if has_invoice_identity_signals:
                    status = "needs_review"
                    _agent_log(
                        "H1",
                        "parser/supplier_matcher.py:match_suppliers",
                        "tax-only guard downgraded confirmed→needs_review",
                        {
                            "supplier": str(supplier.get("name") or ""),
                            "has_invoice_identity_signals": True,
                            "core_matches": core_matches,
                        },
                    )

            invoice_copy["match_status"] = status
            invoice_copy["discount"] = supplier.get("discount", 0.0) if status == "confirmed" else 0.0
            _agent_log(
                "H1",
                "parser/supplier_matcher.py:match_suppliers",
                "final match status stored",
                {
                    "supplier": str(supplier.get("name") or ""),
                    "match_status_final": status,
                    "db_core_match_count": int(len(core_matches)),
                    "discount_applied": float(invoice_copy.get("discount") or 0.0),
                },
            )

            # --- Master data: DB is authoritative for stable supplier fields ---
            # Ook bij needs_review vullen we de leveranciernaam en (waar mogelijk) IBAN/klantcode
            # uit de DB, zodat de gebruiker meteen ziet wie het waarschijnlijk is. De status
            # blijft de safety guard: needs_review blijft geel en vraagt om bevestiging.
            invoice_copy["supplier_name"] = supplier["name"]

            # IBAN: prefer DB; store PDF value for mismatch detection
            inv_iban_raw = invoice.get("iban")
            inv_iban = str(inv_iban_raw).strip() if inv_iban_raw is not None else ""
            sup_iban_raw = supplier.get("iban")
            sup_iban = str(sup_iban_raw).strip() if sup_iban_raw is not None else ""

            if inv_iban:
                invoice_copy["pdf_iban"] = inv_iban
            if sup_iban:
                invoice_copy["iban"] = sup_iban
            if inv_iban and sup_iban and db._clean_iban(inv_iban) != db._clean_iban(sup_iban):
                invoice_copy["iban_mismatch"] = True

            # Customer code: prefer DB value when a matching code exists
            pdf_cc = str(invoice.get("customer_number") or "").strip()
            if pdf_cc:
                invoice_copy["pdf_customer_number"] = pdf_cc

            db_codes = supplier.get("customer_codes") or []
            if db_codes:
                matched_code = None
                if pdf_cc:
                    norm_pdf = db._normalize_customer_code(pdf_cc)
                    for code in db_codes:
                        if norm_pdf and db._normalize_customer_code(code) == norm_pdf:
                            matched_code = code
                            break
                db_cc = matched_code or db_codes[0]
                invoice_copy["customer_number"] = db_cc
                inv_no = invoice_copy.get("invoice_number")
                if db_cc and inv_no:
                    invoice_copy["description"] = f"{db_cc} / {inv_no}"

            try:
                invoice_copy["supplier_payment_term_days_raw"] = int(
                    supplier.get("default_payment_term_days") or 0
                )
            except (TypeError, ValueError):
                invoice_copy["supplier_payment_term_days_raw"] = 0
            invoice_copy["supplier_term_trusted"] = (
                invoice_copy.get("match_status") == "confirmed"
            )
            invoice_copy["supplier_vat_rate"] = normalize_supplier_vat_rate_pct(
                supplier.get("vat_rate", 21)
            )
        else:
            inv_iban = str(invoice.get("iban") or "").strip()
            inv_hint = str(invoice.get("supplier_hint") or "").strip()
            email_domain = str(invoice.get("email_domain") or "").strip()
            email_name = ""
            if email_domain:
                email_name = _clean_supplier_candidate(email_domain.split(".", 1)[0])
            invoice_copy["db_core_matches"] = []
            invoice_copy["db_core_match_count"] = 0
            invoice_copy["db_core_match_confirmed"] = False

            # Only suggest "new" suppliers when the DB is empty and we have a plausible IBAN.
            # Otherwise, remain conservative: treat as unmatched/no_hint.
            if db_is_empty and inv_iban and is_plausible_iban(inv_iban) and inv_hint:
                candidate = _sanitize_supplier_hint(inv_hint)
                if _is_poor_supplier_candidate(candidate):
                    candidate = email_name
                invoice_copy["supplier_name"] = _clean_supplier_candidate(candidate) or "Onbekende leverancier"
                invoice_copy["supplier_match_source"] = "new_from_iban"
                invoice_copy["match_status"] = "new"
            else:
                invoice_copy["supplier_name"] = None
                invoice_copy["supplier_match_source"] = "unmatched"
                invoice_copy["match_status"] = "unmatched" if invoice.get("supplier_hint") else "no_hint"

            invoice_copy["discount"] = 0.0
            invoice_copy["supplier_payment_term_days_raw"] = 0
            invoice_copy["supplier_term_trusted"] = False
            invoice_copy["supplier_vat_rate"] = 21

        out.append(invoice_copy)

    return out

def _try_ocr_upgrade(
    invoice: dict,
    supplier: dict,
    match_info: dict,
    db: SupplierDB,
) -> str:
    """Try OCR on embedded images to find additional supplier characteristics.

    Checks for IBAN, customer codes, and supplier name/aliases in OCR text.
    Re-evaluates match_status with any newly found characteristics.
    """
    source_file = invoice.get("source_file")
    if not source_file:
        return "needs_review"

    try:
        from parser.pdf_parser import extract_text_from_images

        ocr_text = extract_text_from_images(str(source_file))
        if not ocr_text:
            return "needs_review"

        logger.debug("OCR tekst voor matching: %.300s", ocr_text)

        # --- IBAN match ---
        if not match_info.get("iban_match"):
            sup_iban = db._clean_iban(supplier.get("iban") or "")
            if sup_iban:
                for m in re.finditer(r"NL\d{2}\s*[A-Z]{4}\s*\d{4}\s*\d{4}\s*\d{2}", ocr_text):
                    candidate = re.sub(r"\s+", "", m.group(0))
                    if db._clean_iban(candidate) == sup_iban:
                        logger.info(
                            "OCR bevestigde IBAN %s voor %s",
                            mask_iban_for_log(candidate),
                            supplier.get("name"),
                        )
                        match_info["iban_match"] = True
                        match_info["ocr_confirmed"] = True
                        break

        # --- Customer code match ---
        if not match_info.get("customer_code_match"):
            sup_codes = supplier.get("customer_codes") or []
            for code in sup_codes:
                normalized = db._normalize_customer_code(code)
                if not normalized:
                    continue
                for token in re.split(r"[\s,;:|\-/]+", ocr_text):
                    token_normalized = db._normalize_customer_code(token.strip())
                    if token_normalized and token_normalized == normalized:
                        logger.info("OCR bevestigde klantcode %s voor %s", code, supplier.get("name"))
                        match_info["customer_code_match"] = True
                        match_info["ocr_confirmed"] = True
                        break
                if match_info.get("customer_code_match"):
                    break

        # --- Alias / name match ---
        if not match_info.get("alias_match"):
            ocr_lower = ocr_text.lower()
            aliases = supplier.get("aliases") or []
            names_to_check = [supplier.get("name") or "", *aliases]
            for name in names_to_check:
                clean = name.strip().lower()
                if clean and len(clean) >= 3 and clean in ocr_lower:
                    logger.info("OCR bevestigde naam/alias '%s' voor %s", name, supplier.get("name"))
                    match_info["alias_match"] = True
                    match_info["ocr_confirmed"] = True
                    break

        status = _determine_match_status(match_info)
        if status == "confirmed":
            logger.info("OCR upgrade → confirmed voor %s", supplier.get("name"))
        return status
    except Exception:
        logger.debug("OCR fallback mislukt", exc_info=True)
        return "needs_review"
