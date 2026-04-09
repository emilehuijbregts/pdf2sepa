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

# Bedragstoken voor EU-notatie; accepteert ook 4+ cijfers zonder duizendseparator.
_AMOUNT_TOKEN = r"(?:\d{1,3}(?:[.,]\d{3})+|\d+)[.,]\d{2}"
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
    r"Debiteur|"
    r"Lid(?:\s*nummer|\s*nr\.?)|"
    r"Relatie(?:\s*nummer|\s*nr\.?)|"
    r"Customer\s*(?:number|no\.?|code|nr\.?)|"
    r"Account\s*(?:number|no\.?|nr\.?))",
    flags=re.IGNORECASE,
)

_INVOICE_DATE_LABEL_RE = re.compile(
    r"(?i)(?:Factuurdatum|Factuur\s*datum|Invoice\s*date|Date\s*of\s*invoice|"
    r"Datum\s*factuur)\b",
)

_DD_MM_YYYY_RE = re.compile(
    r"\b(\d{1,2})[\./-](\d{1,2})[\./-](\d{4}|\d{2})\b",
)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_EXCLUDE_HINT_RE = re.compile(
    r"(?i)\b(?:vervaldatum|due\s*date|geleverd|pakbon|ordernummer|leverdatum)\b"
)

_FIELD_VALUE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-\/]*")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_KVK_RE = re.compile(r"(?i)\b(?:kvk|k\.?v\.?k\.?)\D{0,12}(\d{7,8})\b")
_VAT_RE = re.compile(r"(?i)\bNL\d{9}B\d{2}\b")
_VAT_DEBTOR_HINT_RE = re.compile(
    r"(?i)\b(?:uw|your|afnemer|customer|klant)\b[^\n]{0,32}\b(?:btw|vat)\b|"
    r"\b(?:btw|vat)\b[^\n]{0,32}\b(?:uw|your|afnemer|customer|klant)\b"
)
_PAYMENT_TERM_RE = re.compile(
    r"(?i)\b(?:betalingstermijn|betalingsconditie|betaaltermijn|binnen)\b[^\n]{0,40}?\b(\d{1,3})\s*(?:dagen|dag)\b"
)

_NOISE_WORDS = frozenset({
    "datum", "date", "vervaldatum", "due", "pagina", "page",
    "btw", "vat", "kvk", "iban", "bic", "swift", "bedrag",
    "amount", "totaal", "total", "naam", "name", "adres",
    "omschrijving", "description", "betaling", "payment",
    "nummer", "number", "netto", "bruto",
    "op", "klant", "klanten", "klantnr", "uw", "ons", "onze",
    "van", "de", "het", "per", "factuur", "nota", "nr",
    "no", "ref", "je", "te", "voor", "aan",
    "onderwerp", "factuuradres", "afleveradres",
    "debiteur", "debiteurnummer", "debiteurennummer",
    "factuurnummer", "factuurnr",
})
_TOTAL_LINE_HINT_RE = re.compile(
    r"(?i)\b(?:totaal|total|te\s+betalen|te\s+voldoen|factuurbedrag|factuurtotaal|eindbedrag|amount\s+due)\b"
)
_TOTAL_LINE_EXCLUDE_RE = re.compile(
    r"(?i)\b(?:excl|exclusive|exclusief|netto|bruto|stuksprijs|unit\s*price|prijs\s+per)\b"
)

def _is_noise_value(val: str) -> bool:
    return val.strip().lower() in _NOISE_WORDS

def _looks_like_date_token(val: str) -> bool:
    v = str(val or "").strip()
    return bool(_DD_MM_YYYY_RE.fullmatch(v) or _ISO_DATE_RE.fullmatch(v))

def _normalize_two_digit_year(y: int) -> int:
    if y >= 100:
        return y
    return 2000 + y if y < 70 else 1900 + y

def _iso_from_dmy(day: int, month: int, year: int) -> str | None:
    try:
        from datetime import date as _date

        yf = _normalize_two_digit_year(year)
        return _date(yf, month, day).isoformat()
    except Exception:
        return None

def _extract_invoice_date_from_text(text: str) -> tuple[str | None, str]:
    """Extraheer factuurdatum na gelabeld veld; retourneer (YYYY-MM-DD of None, 'parsed'|'missing')."""
    lines = text.split("\n")
    label_hits = 0
    for i, line in enumerate(lines):
        lm = _INVOICE_DATE_LABEL_RE.search(line)
        if not lm:
            continue
        label_hits += 1
        after = line[lm.end() :]
        chunk_parts = [after]
        if i + 1 < len(lines):
            chunk_parts.append(lines[i + 1])
        chunk = "\n".join(chunk_parts)
        m_iso = _ISO_DATE_RE.search(chunk)
        if m_iso:
            return f"{m_iso.group(1)}-{m_iso.group(2)}-{m_iso.group(3)}", "parsed"
        m = _DD_MM_YYYY_RE.search(chunk)
        if m:
            inv = _iso_from_dmy(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if inv:
                return inv, "parsed"

    # Fallback: generic "datum" lines (excluding due/delivery/order contexts)
    for i, line in enumerate(lines):
        if "datum" not in line.lower():
            continue
        if _DATE_EXCLUDE_HINT_RE.search(line):
            continue
        chunk_parts = [line]
        if i + 1 < len(lines):
            chunk_parts.append(lines[i + 1])
        chunk = "\n".join(chunk_parts)
        m_iso = _ISO_DATE_RE.search(chunk)
        if m_iso:
            inv = f"{m_iso.group(1)}-{m_iso.group(2)}-{m_iso.group(3)}"
            return inv, "parsed"
        m = _DD_MM_YYYY_RE.search(chunk)
        if m:
            inv = _iso_from_dmy(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if inv:
                return inv, "parsed"

    first_any_date: str | None = None
    m_any_iso = _ISO_DATE_RE.search(text)
    if m_any_iso:
        first_any_date = f"{m_any_iso.group(1)}-{m_any_iso.group(2)}-{m_any_iso.group(3)}"
    else:
        m_any = _DD_MM_YYYY_RE.search(text)
        if m_any:
            first_any_date = _iso_from_dmy(int(m_any.group(1)), int(m_any.group(2)), int(m_any.group(3)))

    return None, "missing"

def _extract_labeled_field(
    text: str,
    label_re: re.Pattern,
    *,
    min_value_len: int = 2,
    require_digit: bool = False,
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
        if label_re is _CUSTOMER_LABEL_RE:
            # Skip email/word-fragment false positives like "debiteuren@asf-fischer.nl".
            next_ch = line[m.end()] if m.end() < len(line) else ""
            prev_ch = line[m.start() - 1] if m.start() > 0 else ""
            if (prev_ch and prev_ch.isalnum()) or (next_ch and (next_ch.isalpha() or next_ch == "@")):
                continue

        after = line[m.end():]
        after_stripped = re.sub(r"^[\s:\.\[\]]+", "", after)

        # Preserve split customer code forms like "603540 / 880".
        if label_re is _CUSTOMER_LABEL_RE:
            slash_same = re.match(
                r"([A-Za-z0-9][A-Za-z0-9\-]*\s*/\s*[A-Za-z0-9][A-Za-z0-9\-]*)",
                after_stripped,
            )
            if slash_same:
                picked = slash_same.group(1).strip()
                return picked

        # Skip Dutch postcode false positives (e.g. "1185 XE" from merged columns)
        if re.match(r"\d{4}\s+[A-Z]{2}\b", after_stripped):
            continue

        remainder = after_stripped
        for _ in range(3):
            vm = _FIELD_VALUE_RE.match(remainder)
            if not vm:
                break
            val = vm.group(0).strip()
            if (
                len(val) >= min_value_len
                and not _is_noise_value(val)
                and not _looks_like_date_token(val)
                and (not require_digit or any(ch.isdigit() for ch in val))
            ):
                return val
            remainder = remainder[vm.end():]
            remainder = re.sub(r"^[\s:\.\[\]]+", "", remainder)

        for j in (1, 2):
            if i + j >= len(lines):
                break
            next_line = lines[i + j].strip()
            if label_re is _CUSTOMER_LABEL_RE and "@" in next_line:
                continue
            if label_re is _CUSTOMER_LABEL_RE:
                slash_next = re.match(
                    r"([A-Za-z0-9][A-Za-z0-9\-]*\s*/\s*[A-Za-z0-9][A-Za-z0-9\-]*)",
                    next_line,
                )
                if slash_next:
                    picked = slash_next.group(1).strip()
                    return picked
            remainder = next_line
            for _ in range(3):
                vm = _FIELD_VALUE_RE.match(remainder)
                if not vm:
                    break
                val = vm.group(0).strip()
                if (
                    len(val) >= min_value_len
                    and not _is_noise_value(val)
                    and not _looks_like_date_token(val)
                    and (not require_digit or any(ch.isdigit() for ch in val))
                ):
                    return val
                remainder = remainder[vm.end():]
                remainder = re.sub(r"^[\s:\.\[\]]+", "", remainder)

    return None

def _extract_amount_from_total_lines(text: str) -> float | None:
    """Fallback amount from total/payable lines; prefers the last explicit total candidate."""
    candidates: list[float] = []
    for line in text.splitlines():
        if not _TOTAL_LINE_HINT_RE.search(line):
            continue
        if _TOTAL_LINE_EXCLUDE_RE.search(line):
            continue
        for tok in re.findall(_AMOUNT_TOKEN, line):
            v = normalize_amount(tok)
            if isinstance(v, float) and v > 0:
                candidates.append(v)
    if not candidates:
        return None
    return candidates[-1]

def _extract_vat_number_from_text(text: str) -> str | None:
    """Extract likely supplier VAT, skipping debtor/customer VAT labels like 'Uw BTW-nummer'."""
    accepted: list[str] = []
    skipped_debtor_hint = 0
    for line in text.splitlines():
        vat_hits = [m.group(0).strip().upper() for m in _VAT_RE.finditer(line)]
        if not vat_hits:
            continue
        if _VAT_DEBTOR_HINT_RE.search(line):
            skipped_debtor_hint += len(vat_hits)
            continue
        accepted.extend(vat_hits)
    if not accepted:
        return None
    return accepted[0]

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
    email_domain: str | None = None
    kvk_number: str | None = None
    vat_number: str | None = None
    payment_term_days: int | None = None

    debtor_clean = re.sub(r"\s+", "", (debtor_iban or "")).upper() if debtor_iban else ""

    # IBAN — find NL IBANs in strict and formatted notation, then validate + filter debtor IBAN
    try:
        found = re.findall(r"\bNL\d{2}[A-Z]{4}\d{10}\b", text, flags=re.IGNORECASE)
        found_spaced_raw = re.findall(
            r"\bNL\s*\d{2}\s*[A-Z]{4}(?:[\s.\-]*\d){10}\b",
            text,
            flags=re.IGNORECASE,
        )
        candidates_raw = [*found, *found_spaced_raw]
        candidates_clean: list[str] = []
        seen_candidates: set[str] = set()
        for raw in candidates_raw:
            c = re.sub(r"[^0-9A-Za-z]", "", raw).upper()
            if c in seen_candidates:
                continue
            if not re.fullmatch(r"NL\d{2}[A-Z]{4}\d{10}", c):
                continue
            if not _iban_mod97_valid(c):
                continue
            seen_candidates.add(c)
            candidates_clean.append(c)
        debtor_filtered = 0
        for candidate in candidates_clean:
            if debtor_clean and candidate.upper() == debtor_clean:
                debtor_filtered += 1
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
        labeled_tokens_raw: list[str] = []
        for m in _TOTAL_PAYABLE_LABEL_RE.finditer(text):
            labeled_tokens_raw.append(m.group(1))
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
            total_line_amount = _extract_amount_from_total_lines(text)
            if total_line_amount is not None:
                amount = total_line_amount
                logger.debug("Bedrag gevonden (total-regel fallback): %s", amount)
            elif normalized_amounts:
                amount = normalized_amounts[-1]
                logger.debug("Bedrag gevonden (last token fallback): %s", amount)
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
        if invoice_number is None:
            # Fallback for vendors using plain "Factuur : <nr>"
            m_fact = re.search(r"(?im)^\s*Factuur\s*:\s*([A-Za-z0-9][A-Za-z0-9\-\/]{3,})\s*$", text)
            if m_fact:
                invoice_number = m_fact.group(1).strip()
        if invoice_number:
            logger.debug("Factuurnummer gevonden: %s", invoice_number)
        else:
            logger.debug("Factuurnummer niet gevonden")
    except Exception:
        logger.debug("Factuurnummer niet gevonden", exc_info=True)
        invoice_number = None

    # Customer number (comprehensive label variants, alphanumeric capture)
    try:
        customer_number = _extract_labeled_field(
            text, _CUSTOMER_LABEL_RE, min_value_len=2, require_digit=True
        )
        if customer_number:
            logger.debug("Klantnummer gevonden: %s", customer_number)
        else:
            logger.debug("Klantnummer niet gevonden")
    except Exception:
        logger.debug("Klantnummer niet gevonden", exc_info=True)
        customer_number = None

    # Factuurdatum (gelabeld; anders missing)
    try:
        invoice_date, invoice_date_source = _extract_invoice_date_from_text(text)
        if invoice_date:
            logger.debug("Factuurdatum gevonden: %s", invoice_date)
        else:
            logger.debug("Factuurdatum niet gevonden")
    except Exception:
        invoice_date, invoice_date_source = None, "missing"
        logger.debug("Factuurdatum niet gevonden", exc_info=True)

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

    # Extra supplier-identification signals (for diagnostics/review flow)
    try:
        m_email = _EMAIL_RE.search(text)
        if m_email:
            email_domain = str(m_email.group(1) or "").strip().lower() or None
        m_kvk = _KVK_RE.search(text)
        if m_kvk:
            kvk_number = str(m_kvk.group(1) or "").strip() or None
        vat_number = _extract_vat_number_from_text(text)
    except Exception:
        email_domain = None
        kvk_number = None
        vat_number = None

    # Payment term in days (diagnostic signal; DB remains authoritative for payment execution)
    try:
        m_term = _PAYMENT_TERM_RE.search(text)
        if m_term:
            td = int(m_term.group(1))
            if 0 <= td <= 365:
                payment_term_days = td
    except Exception:
        payment_term_days = None

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
        if not invoice_date:
            missing.append("invoice_date")
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
        "invoice_date": invoice_date,
        "invoice_date_source": invoice_date_source,
        "description": description,
        "type": doc_type,
        "supplier_hint": supplier_hint,
        "email_domain": email_domain,
        "kvk_number": kvk_number,
        "vat_number": vat_number,
        "payment_term_days": payment_term_days,
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

def _is_weak_ocr_text(s: str) -> bool:
    """Heuristic: OCR output too sparse/short to trust as final result."""
    t = re.sub(r"\s+", " ", str(s or "")).strip()
    if not t:
        return True
    if len(t) < 24:
        return True
    has_signal = bool(
        re.search(r"\d", t)
        or "@" in t
        or "nl" in t.lower()
        or re.search(r"\b(?:iban|btw|kvk|factuur|invoice|debiteur|klant)\b", t, flags=re.IGNORECASE)
    )
    return not has_signal

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
    page_count = 0
    image_count = 0
    skipped_small = 0
    pymupdf_used = 0
    pytesseract_used = 0
    pymupdf_nonempty = 0
    pytesseract_nonempty = 0
    weak_primary_count = 0
    ocr_samples: list[dict[str, Any]] = []
    try:
        for page in doc:
            page_count += 1
            images = page.get_images(full=True)
            if not images:
                continue
            for img_info in images:
                image_count += 1
                try:
                    xref = img_info[0]
                    pix = _fitz.Pixmap(doc, xref)
                    if pix.n > 4:
                        pix = _fitz.Pixmap(_fitz.csRGB, pix)
                    if pix.width < 50 or pix.height < 50:
                        skipped_small += 1
                        continue

                    ocr_text = ""
                    source = "none"

                    # Primary: PyMuPDF built-in OCR via Tesseract
                    if hasattr(_fitz.Page, "get_textpage_ocr"):
                        try:
                            pymupdf_used += 1
                            img_pdf = _fitz.open()
                            img_page = img_pdf.new_page(width=pix.width, height=pix.height)
                            img_page.insert_image(img_page.rect, pixmap=pix)
                            tp = img_page.get_textpage_ocr(flags=0, language="nld", dpi=300)
                            ocr_text = img_page.get_text("text", textpage=tp).strip()
                            img_pdf.close()
                            source = "pymupdf"
                            if ocr_text:
                                pymupdf_nonempty += 1
                            if 0 < len(ocr_text) < 24:
                                weak_primary_count += 1
                        except Exception:
                            logger.debug("PyMuPDF OCR mislukt voor xref %s, probeer pytesseract", xref, exc_info=True)
                            ocr_text = ""

                    # Fallback: pytesseract + Pillow
                    # Also trigger when primary OCR is non-empty but weak.
                    if not ocr_text or _is_weak_ocr_text(ocr_text):
                        primary_text = ocr_text
                        pytesseract_used += 1
                        tesseract_text = _ocr_pixmap_pytesseract(pix).strip()
                        if tesseract_text:
                            pytesseract_nonempty += 1
                        choose_tesseract = False
                        if not primary_text and tesseract_text:
                            choose_tesseract = True
                        elif primary_text and tesseract_text:
                            score_primary = len(primary_text)
                            score_tesseract = len(tesseract_text)
                            if _is_weak_ocr_text(primary_text):
                                score_primary -= 12
                            if _is_weak_ocr_text(tesseract_text):
                                score_tesseract -= 12
                            choose_tesseract = score_tesseract > score_primary
                        if choose_tesseract:
                            ocr_text = tesseract_text
                            source = "pytesseract"
                        elif not primary_text and tesseract_text:
                            ocr_text = tesseract_text
                            source = "pytesseract"
                        else:
                            ocr_text = primary_text

                    if ocr_text:
                        text_parts.append(ocr_text)
                    if len(ocr_samples) < 6:
                        ocr_samples.append(
                            {
                                "xref": int(xref),
                                "w": int(pix.width),
                                "h": int(pix.height),
                                "source": source,
                                "chars": len(ocr_text),
                                "preview": re.sub(r"\s+", " ", ocr_text[:60]),
                            }
                        )
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

