from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pdfplumber

from logic.validation import _iban_mod97_valid, mask_iban_for_log

try:
    import fitz as _fitz
except ImportError:
    _fitz = None

logger = logging.getLogger(__name__)


def extract_text_strict(file_path: str) -> str:
    """Lees alle tekst uit een PDF. Gooit bij open/read-fouten (voor per-bestand afhandeling)."""
    pages_text: list[str] = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            pages_text.append(page.extract_text() or "")
    return "\n".join(pages_text)


def extract_text(file_path: str) -> str:
    """Extracteer alle tekst uit een PDF (alle pagina's), samengevoegd met newlines."""
    try:
        return extract_text_strict(file_path)
    except Exception:
        logger.warning("PDF tekst uitlezen mislukt: %s", Path(file_path).name)
        return ""


# Bedragstoken gelijk aan die voor het bruto-`amount`-veld (EU-notatie).
_AMOUNT_TOKEN = r"\d{1,3}(?:[.,]\d{3})*[.,]\d{2}"
# Labels voor bedrag excl. BTW; specifiekere patronen eerst (alternatie).
_EXCL_VAT_LABEL_RE = re.compile(
    rf"(?i)(?:Totaal\s+netto\s+goederenwaarde|Netto\s+goederenbedrag|"
    rf"Totaal\s+excl\.?|Bedrag\s+excl\.?|Excl\.\s*BTW|Subtotaal|Nettobedrag)"
    rf"\s*[:]?\s*(?:EUR\b|€)?\s*({_AMOUNT_TOKEN})",
)


_INVOICE_LABEL_RE = re.compile(
    r"(?:Factuur(?:\s*nummer|\s*nr\.?)|"
    r"Invoice\s*(?:number|no\.?|nr\.?)|"
    r"Nota(?:\s*nummer|\s*nr\.?))",
    flags=re.IGNORECASE,
)

_CUSTOMER_LABEL_RE = re.compile(
    r"(?:Klant(?:en)?(?:\s*nummer|\s*nr\.?|\s*code)|"
    r"Debiteur(?:en)?(?:\s*nummer|\s*nr\.?)|"
    r"Lid(?:\s*nummer|\s*nr\.?)|"
    r"Relatie(?:\s*nummer|\s*nr\.?)|"
    r"Customer\s*(?:number|no\.?|code|nr\.?)|"
    r"Account\s*(?:number|no\.?|nr\.?))",
    flags=re.IGNORECASE,
)

_FIELD_VALUE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-\/]*")

_NOISE_WORDS = frozenset({
    "datum", "date", "vervaldatum", "due", "pagina", "page",
    "btw", "vat", "kvk", "iban", "bic", "swift", "bedrag",
    "amount", "totaal", "total", "naam", "name", "adres",
    "omschrijving", "description", "betaling", "payment",
    "nummer", "number", "netto", "bruto",
    "op", "klant", "klanten", "klantnr", "uw", "ons", "onze",
    "van", "de", "het", "per", "factuur", "nota", "nr",
    "no", "ref", "je", "te", "voor", "aan",
})


def _is_noise_value(val: str) -> bool:
    return val.strip().lower() in _NOISE_WORDS


def _extract_labeled_field(
    text: str,
    label_re: re.Pattern,
    *,
    min_value_len: int = 2,
) -> str | None:
    """Line-by-line extraction: same-line (after colon/separator) first, then next-line.

    Processes per-line to avoid cross-column captures in tabular PDF layouts.
    Skips up to 3 noise words on the same line before falling back to the next line.
    """
    lines = text.split("\n")

    for i, line in enumerate(lines):
        m = label_re.search(line)
        if not m:
            continue

        after = line[m.end():]
        after_stripped = re.sub(r"^[\s:\.]+", "", after)

        # Skip Dutch postcode false positives (e.g. "1185 XE" from merged columns)
        if re.match(r"\d{4}\s+[A-Z]{2}\b", after_stripped):
            continue

        remainder = after_stripped
        for _ in range(3):
            vm = _FIELD_VALUE_RE.match(remainder)
            if not vm:
                break
            val = vm.group(0).strip()
            if len(val) >= min_value_len and not _is_noise_value(val):
                return val
            remainder = remainder[vm.end():]
            remainder = re.sub(r"^[\s:\.]+", "", remainder)

        if i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            vm = _FIELD_VALUE_RE.match(next_line)
            if vm:
                val = vm.group(0).strip()
                if len(val) >= min_value_len and not _is_noise_value(val):
                    return val

    return None


_TOTAL_PAYABLE_LABEL_RE = re.compile(
    rf"(?i)(?:Totaal\s+te\s+betalen|Te\s+voldoen|Total\s+due|"
    rf"Totaal\s+incl\.?\s*BTW|Factuurbedrag|Totaalbedrag|Te\s+betalen)"
    rf"\s*[:]?\s*(?:EUR\b|€)?\s*({_AMOUNT_TOKEN})",
)


def normalize_amount(amount_str: str | None) -> float | None:
    """Normaliseer een bedragstring (EU-notatie) naar `float` of `None`."""
    try:
        if not amount_str:
            return None

        s = str(amount_str).strip()
        if not s:
            return None

        # Remove currency markers and whitespace
        s = re.sub(r"(?i)\bEUR\b", "", s)
        s = s.replace("€", "")
        s = re.sub(r"\s+", "", s)

        # Keep only digits and separators and minus sign
        s = re.sub(r"[^0-9,.\-]", "", s)
        if not s or s in {"-", ".", ",", "-.", "-,"}:
            return None

        # Determine decimal separator by last occurrence of '.' or ','
        last_dot = s.rfind(".")
        last_comma = s.rfind(",")
        sep_idx = max(last_dot, last_comma)
        if sep_idx == -1:
            return None

        int_part = s[:sep_idx]
        dec_part = s[sep_idx + 1 :]
        if not dec_part:
            return None

        # Remove thousands separators from integer part
        sign = ""
        if int_part.startswith("-"):
            sign = "-"
            int_part = int_part[1:]

        int_part = int_part.replace(".", "").replace(",", "")
        if not int_part.isdigit():
            return None

        dec_part = re.sub(r"[^0-9]", "", dec_part)
        if not dec_part.isdigit():
            return None

        normalized = f"{sign}{int_part}.{dec_part}"
        return float(normalized)
    except Exception:
        return None


def extract_amount_excl_vat(text: str | None) -> float | None:
    """Zoek een bedrag excl. BTW nabij bekende factuurlabels; zelfde normalisatie als `amount`."""
    try:
        t = text or ""
        if not t:
            return None
        candidates: list[float] = []
        for m in _EXCL_VAT_LABEL_RE.finditer(t):
            v = normalize_amount(m.group(1))
            if isinstance(v, float):
                candidates.append(v)
        if not candidates:
            return None
        return max(candidates)
    except Exception:
        return None


def build_description(customer_number: str | None, invoice_number: str | None) -> str | None:
    """Bouw description als `{customer_number} / {invoice_number}` wanneer beide bestaan."""
    try:
        if customer_number and invoice_number:
            return f"{customer_number} / {invoice_number}"
        return None
    except Exception:
        return None


def format_remittance_text(
    customer_number: str | None,
    invoice_number: str | None,
    description: str | None = None,
) -> str:
    """
    Tekst voor SEPA-omschrijving (klant / factuur), zonder bestandsnaam.

    Gebruikt `description` uit de parser als die gezet is; anders `build_description`
    of fallback naar alleen factuur- of klantnummer.
    """
    try:
        if description and str(description).strip():
            return str(description).strip()
        bd = build_description(
            str(customer_number).strip() if customer_number is not None else None,
            str(invoice_number).strip() if invoice_number is not None else None,
        )
        if bd:
            return bd
        if invoice_number is not None and str(invoice_number).strip():
            return str(invoice_number).strip()
        if customer_number is not None and str(customer_number).strip():
            return str(customer_number).strip()
        return ""
    except Exception:
        return ""


def extract_invoice_data(text: str | None, *, debtor_iban: str | None = None) -> dict[str, Any]:
    """
    Parseer ruwe PDF-tekst naar een Module 3-ready JSON dict.

    Args:
        debtor_iban: If provided, this IBAN is excluded from extraction results
                     (to avoid capturing the user's own IBAN as a supplier IBAN).
    """
    text = text or ""

    iban: str | None = None
    all_ibans: list[str] = []
    amount: float | None = None
    amount_excl_vat: float | None = None
    invoice_number: str | None = None
    customer_number: str | None = None
    supplier_hint: str | None = None

    debtor_clean = re.sub(r"\s+", "", (debtor_iban or "")).upper() if debtor_iban else ""

    # IBAN — find all NL IBANs, filter debtor IBAN, pick the first remaining
    try:
        found = re.findall(r"\bNL\d{2}[A-Z]{4}\d{10}\b", text)
        for candidate in found:
            if debtor_clean and candidate.upper() == debtor_clean:
                continue
            all_ibans.append(candidate)
        if all_ibans:
            iban = all_ibans[0]
            logger.debug(
                "IBAN gevonden: %s (van %d kandidaten)",
                mask_iban_for_log(iban),
                len(all_ibans),
            )
        else:
            logger.debug("IBAN niet gevonden")
    except Exception:
        logger.debug("IBAN niet gevonden", exc_info=True)
        iban = None

    # Amount — prefer labeled "total payable" amounts, fall back to max token
    try:
        labeled_amount: float | None = None
        for m in _TOTAL_PAYABLE_LABEL_RE.finditer(text):
            v = normalize_amount(m.group(1))
            if isinstance(v, float) and v > 0:
                if labeled_amount is None or v > labeled_amount:
                    labeled_amount = v

        if labeled_amount is not None:
            amount = labeled_amount
            logger.debug("Bedrag gevonden (gelabeld): %s", amount)
        else:
            amount_matches = re.findall(_AMOUNT_TOKEN, text)
            normalized_amounts: list[float] = []
            for a in amount_matches:
                v = normalize_amount(a)
                if isinstance(v, float):
                    normalized_amounts.append(v)
            if normalized_amounts:
                amount = max(normalized_amounts)
                logger.debug("Bedrag gevonden (max token): %s", amount)
            else:
                logger.debug("Bedrag niet gevonden")
    except Exception:
        logger.debug("Bedrag niet gevonden", exc_info=True)
        amount = None

    # Amount excl. BTW (nabij label; anders None)
    try:
        amount_excl_vat = extract_amount_excl_vat(text)
        if amount_excl_vat is not None:
            logger.debug("Bedrag excl. BTW gevonden: %s", amount_excl_vat)
        else:
            logger.debug("Bedrag excl. BTW niet gevonden")
    except Exception:
        amount_excl_vat = None
        logger.debug("Bedrag excl. BTW niet gevonden", exc_info=True)

    # Invoice number (line-by-line extraction avoids cross-column captures)
    try:
        invoice_number = _extract_labeled_field(text, _INVOICE_LABEL_RE, min_value_len=2)
        if invoice_number:
            logger.debug("Factuurnummer gevonden: %s", invoice_number)
        else:
            logger.debug("Factuurnummer niet gevonden")
    except Exception:
        logger.debug("Factuurnummer niet gevonden", exc_info=True)
        invoice_number = None

    # Customer number (comprehensive label variants, alphanumeric capture)
    try:
        customer_number = _extract_labeled_field(text, _CUSTOMER_LABEL_RE, min_value_len=2)
        if customer_number:
            logger.debug("Klantnummer gevonden: %s", customer_number)
        else:
            logger.debug("Klantnummer niet gevonden")
    except Exception:
        logger.debug("Klantnummer niet gevonden", exc_info=True)
        customer_number = None

    # Restricted fallback: only substantial digit/digit reference patterns (min 5/4 digits)
    try:
        if customer_number is None or invoice_number is None:
            m_ref = re.search(r"(\d{5,})\s*/\s*(\d{4,})", text)
            if m_ref:
                if invoice_number is None:
                    invoice_number = m_ref.group(1).strip()
                    logger.debug("Factuurnummer via betaalreferentie: %s", invoice_number)
                if customer_number is None:
                    customer_number = m_ref.group(2).strip()
                    logger.debug("Klantnummer via betaalreferentie: %s", customer_number)
    except Exception:
        pass

    # Supplier hint (heuristiek; vult alleen aan)
    try:
        # Matcht: `supplier_hint`
        if supplier_hint is None:
            from parser.supplier_rules import extract_supplier_name_hint

            supplier_hint = extract_supplier_name_hint(text)
            if supplier_hint:
                logger.debug("Supplier hint gevonden: %s", supplier_hint)
            else:
                supplier_hint = None
                logger.debug("Supplier hint niet gevonden")
    except Exception:
        supplier_hint = None
        logger.debug("Supplier hint niet gevonden", exc_info=True)

    # Type
    try:
        # Matcht: `type`
        if re.search(r"\b(creditnota|credit note|credit|CREN)\b", text, flags=re.IGNORECASE):
            doc_type = "credit_note"
        else:
            doc_type = "invoice"
        logger.debug("Type: %s", doc_type)
    except Exception:
        doc_type = "invoice"
        logger.debug("Type: %s", doc_type, exc_info=True)

    # Description
    try:
        description = build_description(customer_number, invoice_number)
        if description:
            logger.debug("Description gemaakt: %s", description)
        else:
            logger.debug("Description niet gemaakt")
    except Exception:
        description = None
        logger.debug("Description niet gemaakt", exc_info=True)

    # Debug: gemiste velden voor troubleshooting
    try:
        missing: list[str] = []
        if iban is None:
            missing.append("iban")
        if amount is None:
            missing.append("amount")
        if invoice_number is None:
            missing.append("invoice_number")
        if customer_number is None:
            missing.append("customer_number")
        if description is None:
            missing.append("description")
        if supplier_hint is None:
            missing.append("supplier_hint")
        if missing:
            logger.debug("Gemiste velden: %s", ", ".join(missing))
    except Exception:
        pass

    return {
        "iban": iban,
        "all_ibans": all_ibans,
        "amount": amount,
        "amount_excl_vat": amount_excl_vat,
        "invoice_number": invoice_number,
        "customer_number": customer_number,
        "description": description,
        "type": doc_type,
        "supplier_hint": supplier_hint,
        "raw_text": text,
    }


def _ocr_pixmap_pytesseract(pix) -> str:
    """OCR a PyMuPDF Pixmap via pytesseract + Pillow (fallback path)."""
    try:
        from PIL import Image
        import pytesseract

        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return pytesseract.image_to_string(img, lang="nld+eng") or ""
    except Exception:
        logger.debug("pytesseract OCR mislukt", exc_info=True)
        return ""


def extract_text_from_images(file_path: str) -> str:
    """OCR all embedded images in a PDF and return the combined text.

    Uses PyMuPDF's built-in Tesseract OCR (get_textpage_ocr) as primary path,
    with pytesseract + Pillow as fallback.
    Returns empty string if no OCR backend is available.
    """
    if _fitz is None:
        logger.debug("PyMuPDF niet beschikbaar — OCR overgeslagen")
        return ""

    try:
        doc = _fitz.open(file_path)
    except Exception:
        logger.debug("Kon PDF niet openen met PyMuPDF: %s", file_path, exc_info=True)
        return ""

    text_parts: list[str] = []
    try:
        for page in doc:
            images = page.get_images(full=True)
            if not images:
                continue
            for img_info in images:
                try:
                    xref = img_info[0]
                    pix = _fitz.Pixmap(doc, xref)
                    if pix.n > 4:
                        pix = _fitz.Pixmap(_fitz.csRGB, pix)
                    if pix.width < 50 or pix.height < 50:
                        continue

                    ocr_text = ""

                    # Primary: PyMuPDF built-in OCR via Tesseract
                    if hasattr(_fitz.Page, "get_textpage_ocr"):
                        try:
                            img_pdf = _fitz.open()
                            img_page = img_pdf.new_page(width=pix.width, height=pix.height)
                            img_page.insert_image(img_page.rect, pixmap=pix)
                            tp = img_page.get_textpage_ocr(flags=0, language="nld", dpi=300)
                            ocr_text = img_page.get_text("text", textpage=tp).strip()
                            img_pdf.close()
                        except Exception:
                            logger.debug("PyMuPDF OCR mislukt voor xref %s, probeer pytesseract", xref, exc_info=True)
                            ocr_text = ""

                    # Fallback: pytesseract + Pillow
                    if not ocr_text:
                        ocr_text = _ocr_pixmap_pytesseract(pix).strip()

                    if ocr_text:
                        text_parts.append(ocr_text)
                except Exception:
                    continue
    except Exception:
        logger.debug("OCR verwerking mislukt voor %s", file_path, exc_info=True)
    finally:
        doc.close()

    combined = "\n".join(text_parts)
    if combined:
        logger.debug("OCR tekst uit afbeeldingen (%d chars): %.200s", len(combined), combined)
    return combined


def extract_ibans_from_images(file_path: str) -> list[str]:
    """Extract validated NL IBANs from embedded images via OCR.

    Thin wrapper around extract_text_from_images for backward compatibility.
    """
    ocr_text = extract_text_from_images(file_path)
    if not ocr_text:
        return []

    ibans: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"\bNL\d{2}\s*[A-Z]{4}\s*\d{4}\s*\d{4}\s*\d{2}\b", ocr_text):
        candidate = re.sub(r"\s+", "", m.group(0))
        if candidate not in seen and _iban_mod97_valid(candidate):
            ibans.append(candidate)
            seen.add(candidate)
    return ibans

