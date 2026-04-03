from __future__ import annotations

import logging
import re
from pathlib import Path

from logic.validation import mask_iban_for_log
from parser.supplier_db import SupplierDB

logger = logging.getLogger(__name__)


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

    primary = sum([iban, code])
    secondary = 1 if alias else 0

    if primary >= 2:
        return "confirmed"
    if primary >= 1 and secondary >= 1:
        return "confirmed"
    if code and fuzzy:
        return "confirmed"

    if iban or code or alias or fuzzy:
        return "needs_review"

    return "unmatched"


def match_suppliers(invoices: list[dict], db: SupplierDB) -> list[dict]:
    out: list[dict] = []

    for invoice in invoices:
        if invoice.get("load_error"):
            src = str(invoice.get("source_file") or "")
            base = Path(src).name if src else "PDF"
            out.append({
                **invoice,
                "supplier_name": base,
                "match_status": "load_failed",
                "discount": 0.0,
            })
            continue

        invoice_copy = invoice.copy()

        supplier, match_info = db.find_supplier_scored(
            invoice.get("supplier_hint"),
            invoice.get("iban"),
            invoice.get("customer_number"),
        )

        if supplier:
            invoice_copy["supplier_name"] = supplier["name"]
            invoice_copy["discount"] = supplier.get("discount", 0.0)
            invoice_copy["match_info"] = match_info

            status = _determine_match_status(match_info)

            if status == "needs_review":
                status = _try_ocr_upgrade(invoice, supplier, match_info, db)

            invoice_copy["match_status"] = status

            # --- Master data: DB is authoritative for stable supplier fields ---

            # IBAN: always prefer DB; store PDF value for mismatch detection
            inv_iban_raw = invoice.get("iban")
            inv_iban = str(inv_iban_raw).strip() if inv_iban_raw is not None else ""
            sup_iban_raw = supplier.get("iban")
            sup_iban = str(sup_iban_raw).strip() if sup_iban_raw is not None else ""

            if inv_iban:
                invoice_copy["pdf_iban"] = inv_iban

            if sup_iban:
                invoice_copy["iban"] = supplier["iban"]
                if inv_iban and db._clean_iban(inv_iban) != db._clean_iban(sup_iban):
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
        else:
            invoice_copy["supplier_name"] = None
            invoice_copy["discount"] = 0.0

            if invoice.get("supplier_hint"):
                invoice_copy["match_status"] = "unmatched"
            else:
                invoice_copy["match_status"] = "no_hint"

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
