from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Literal

import pdfplumber

from logic.validation import _iban_mod97_valid, mask_iban_for_log

try:
    import fitz as _fitz
except ImportError:
    _fitz = None

logger = logging.getLogger(__name__)

# region agent log (debug mode - session 935dd7)
def _dbg_935(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "pre-fix") -> None:
    try:
        import json, time  # noqa: E401

        payload = {
            "sessionId": "935dd7",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(
            "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-935dd7.log",
            "a",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
# endregion

# region agent log
def _agent_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    try:
        import json, time  # noqa: E401

        payload = {
            "sessionId": "c9cbe4",
            "runId": "post-fix",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(
            "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-c9cbe4.log",
            "a",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
# endregion

# region agent log (debug session 10a5df)
def _dbg_10a5df(
    hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "extract"
) -> None:
    try:
        import json, time  # noqa: E401

        payload = {
            "sessionId": "10a5df",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(
            "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-10a5df.log",
            "a",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


# endregion

# ---------------------------------------------------------------------------
# Amount candidate model
# ---------------------------------------------------------------------------

_QUANT_2 = Decimal("0.01")

AmountCandidateType = Literal["incl", "excl", "vat", "unknown"]


@dataclass
class AmountCandidate:
    """Single parsed amount with provenance metadata."""

    value: Decimal
    source: str
    confidence: int  # 0–100
    context: str
    type: AmountCandidateType = "unknown"  # incl | excl | vat | unknown (payable incl. BTW heuristiek)


@dataclass
class AmountResult:
    """Aggregated amount-selection outcome produced by the parser."""

    candidates: list[AmountCandidate] = field(default_factory=list)
    value: Decimal | None = None
    confidence: int = 0
    source: str = "UNKNOWN"
    status: str = "failed"  # confirmed | tentative | ambiguous | failed
    user_selected: bool = False

    @property
    def selected_amount(self) -> Decimal | None:
        # Backward-compatible alias.
        return self.value

    @property
    def amount_confidence(self) -> int:
        # Backward-compatible alias.
        return self.confidence

    @property
    def amount_status(self) -> str:
        # Backward-compatible alias.
        return self.status

    def to_dict(self) -> dict[str, Any]:
        selected = str(self.value) if self.value is not None else None
        d: dict[str, Any] = {
            "candidates": [
                {
                    "value": str(c.value),
                    "source": c.source,
                    "confidence": c.confidence,
                    "context": c.context,
                    "type": getattr(c, "type", "unknown"),
                }
                for c in self.candidates
            ],
            "value": selected,
            "selected_value": selected,
            "confidence": self.confidence,
            "source": self.source,
            "status": self.status,
            "decision_trace": [],
            "override_reason": "",
            "resolver_finalized": False,
            # Backward-compatible keys
            "selected_amount": selected,
            "amount_confidence": self.confidence,
            "amount_status": self.status,
        }
        if self.user_selected:
            d["user_selected"] = True
        if self.status == "tentative":
            d["review_suggested"] = True
        return d


def normalize_amount_decimal(amount_str: str | None) -> Decimal | None:
    """Normalise an EU-format amount string to a 2-decimal Decimal, or None."""
    v = normalize_amount(amount_str)
    if v is None:
        return None
    return Decimal(str(v)).quantize(_QUANT_2, rounding=ROUND_HALF_UP)


def _normalize_kvk_digits(kvk: str | None) -> str:
    """Zelfde logica als leveranciers-db: 7-8 cijfers, anders leeg."""
    digits = re.sub(r"\D", "", str(kvk or ""))
    if len(digits) in (7, 8):
        return digits
    return ""


def _normalize_vat_compact(vat: str | None) -> str:
    return re.sub(r"\s+", "", str(vat or "")).upper()


# ISO 13616 nationale IBAN-lengtes (compact = deze lengte). Onbekende → trim + mod‑97‑probe.
_IBAN_ISO_LENGTH_BY_CC: dict[str, int] = {
    "NL": 18,
    "BE": 16,
    "DE": 22,
    "FR": 27,
    "AT": 20,
    "CH": 21,
    "LI": 21,
    "GB": 22,
    "IE": 22,
    "LU": 20,
    "ES": 24,
    "IT": 27,
    "PT": 25,
    "FI": 18,
    "DK": 18,
    "SE": 24,
    "NO": 15,
    "PL": 28,
    "CZ": 24,
    "SK": 24,
    "HU": 28,
    "RO": 24,
    "BG": 22,
    "HR": 21,
    "SI": 19,
    "EE": 20,
    "LV": 21,
    "LT": 20,
    "MT": 31,
    "CY": 28,
    "GR": 27,
    "IS": 26,
    "MC": 27,
    "SM": 27,
    "AD": 24,
}


def _finalize_iban_from_scan_parts(cc: str, check: str, body_merged_upper: str) -> str | None:
    tgt = _IBAN_ISO_LENGTH_BY_CC.get(cc.upper())
    need_body = tgt - 4 if tgt else None
    if need_body is not None:
        if len(body_merged_upper) < need_body:
            return None
        cand = f"{cc}{check}{body_merged_upper[:need_body]}"
        return cand if _iban_mod97_valid(cand) else None
    full = (f"{cc}{check}" + body_merged_upper).upper()
    full = full[:34]
    while len(full) >= 15:
        if (
            full.isalnum()
            and re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,}", full)
            and _iban_mod97_valid(full)
        ):
            return full
        full = full[:-1]
    return None


def _scan_sepa_ibans_in_text(text: str) -> list[str]:
    """Vind geldige SEPA IBAN’s (compact) in tekst/OCR; gebruikt nationale lengtes + mod‑97."""
    raw = text or ""
    slen = len(raw)
    out: list[str] = []
    seen: set[str] = set()

    sep_chars = frozenset(" \t\n\r._-:")
    i = 0
    while i < slen - 5:
        if i > 0 and raw[i - 1].isalnum():
            i += 1
            continue
        if not raw[i].isalpha() or not raw[i + 1].isalpha():
            i += 1
            continue
        cc_candidate = raw[i : i + 2]
        if not cc_candidate.isascii():
            i += 1
            continue
        cc = cc_candidate.upper()
        if not (raw[i + 2].isdigit() and raw[i + 3].isdigit()):
            i += 1
            continue
        check_d = raw[i + 2 : i + 4]
        body_chars: list[str] = []
        j = i + 4
        max_take = (_IBAN_ISO_LENGTH_BY_CC.get(cc, 34) - 4) + 12
        max_take = min(max_take, 32)
        while j < slen:
            if len(body_chars) >= max_take:
                break
            c = raw[j]
            if c.isdigit():
                body_chars.append(c)
            elif c.isalpha() and c.isascii():
                body_chars.append(c.upper())
            elif c in sep_chars:
                pass
            else:
                break
            j += 1
        merged = "".join(body_chars)
        finalized = _finalize_iban_from_scan_parts(cc, check_d, merged)
        if finalized and finalized not in seen:
            seen.add(finalized)
            out.append(finalized)
        i += 1
    return out


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

# Rightmost label match on a line: amounts *before* this are usually netto/subregels.
_TOTAL_LABEL_ANCHOR_RE = re.compile(
    r"(?i)\b(?:"
    r"totaalfactuurbedrag|totaal[\s\-:._]+factuurbedrag|totaal[\s\-:._]+factuur[\s\-:._]+bedrag|"
    r"totaal\s+factuurbedrag|totaalbedrag|totaal\s+bedrag|"
    r"eindtotaal|eindbedrag|factuurbedrag|factuurtotaal|"
    r"te\s+betalen|totaal\s+te\s+betalen|totaal\s+te\s+voldoen|amount\s+due|total\s+due|"
    r"totaal|total"
    r")\b"
)
_SOURCES_WITH_LINE_TOTAL_ANCHOR: frozenset[str] = frozenset(
    {
        "total_label_payable",
        "total_label_invoice",
        "total_label_sum",
        "total_label_generic",
    }
)
# Labels voor bedrag excl. BTW; specifiekere patronen eerst (alternatie).
_EXCL_VAT_LABEL_RE = re.compile(
    rf"(?i)(?:Totaal\s+netto\s+goederenwaarde|Netto\s+goederenbedrag|"
    rf"Totaal\s+excl\.?|Bedrag\s+excl\.?|Excl\.\s*BTW|Subtotaal|Nettobedrag)"
    rf"\s*[:]?\s*(?:EUR\b|€)?\s*({_AMOUNT_TOKEN})",
)

_INVOICE_LABEL_RE = re.compile(
    r"(?:Factuurnummer|Factuurnr\.?|Factuur(?:\s*nummer|\s*nr\.?)|Fact\.?\s*nr\.?|"
    r"Document\s*nr\.?|Documentnr\.?|"
    r"Invoice\s*(?:number|no\.?|nr\.?)|\bINVOICE\b|"
    r"Rechnung\s*(?:nr\.?|nummer)|Rechnungsnummer|"
    r"Nota(?:\s*nummer|\s*nr\.?)|"
    r"Polisnummer|Polis\s*nr\.?|Polis\s*nummer|"
    r"\bNummer\b(?=\s+(?:INV-|REG|SIN/|[A-Z]{1,8}[\-/]?\d)))",
    flags=re.IGNORECASE,
)

# Prefer real invoice identifiers over insurance "Polisnummer" when both exist in the same document.
_INVOICE_LABEL_RE_NO_POLIS = re.compile(
    r"(?:Factuurnummer|Factuurnr\.?|Factuur(?:\s*nummer|\s*nr\.?)|Fact\.?\s*nr\.?|"
    r"Document\s*nr\.?|Documentnr\.?|"
    r"Invoice\s*(?:number|no\.?|nr\.?)|\bINVOICE\b|"
    r"Rechnung\s*(?:nr\.?|nummer)|Rechnungsnummer|"
    r"Nota(?:\s*nummer|\s*nr\.?)|"
    r"\bNummer\b(?=\s+(?:INV-|REG|SIN/|[A-Z]{1,8}[\-/]?\d)))",
    flags=re.IGNORECASE,
)

_CUSTOMER_LABEL_RE = re.compile(
    r"(?:\bKlantcode\b|\bklantnummer\b|\bklant-nummer\b|"
    r"Klant(?:en)?(?:\s*nummer|\s*nr\.?|-nr\.?|\s*code)|Klantnr\.?|"
    r"\bKlant\b(?=\s+\d)|"
    r"Debiteur(?:en)?(?:\s*nummer|\s*nr\.?)|"
    r"Deb\.?\s*(?:nr\.?|nummer)|Debnr\.?|"
    r"Debiteur|"
    r"\bDebtor\b(?:\s*(?:number|no\.?|nr\.?|id))?|"
    r"Betaler(?:\s*(?:nr\.?|nummer|no\.?|id))?|"
    r"Factureren\s+aan(?:\s*(?:nr\.?|nummer|no\.?|id))?|"
    r"Lid(?:\s*nummer|\s*nr\.?)|"
    r"Relatie(?:\s*nummer|\s*nr\.?)?|\bRelatie\b|"
    r"\bCustomer\b(?=\s+\d)|"
    r"Customer\s*(?:number|no\.?|code|nr\.?|id)|"
    r"Client\s*(?:number|no\.?|code|nr\.?|id)|"
    r"Account\s*(?:number|no\.?|nr\.?)|"
    r"Kunden(?:nummer|nr\.?|-\s*nr\.?)|Kundennr\.?|"
    r"Debitor(?:en)?(?:nummer|nr\.?)|Debitorennummer|"
    r"Billing\s+to(?:\s*(?:number|no\.?|nr\.?|id))?)",
    flags=re.IGNORECASE,
)

_INVOICE_DATE_LABEL_RE = re.compile(
    r"(?i)(?:Factuurdatum|Factuur\s*datum|Invoice\s*date|Date\s*of\s*invoice|"
    r"\bDatum\s*factuur|Factuur\s*d\.?\s*d\.?)\b",
)

# Header variant seen on some invoices: "FACTUUR Nr. <id> van 30-01-2026"
_INVOICE_NR_VAN_DATE_RE = re.compile(
    r"(?i)\b(?:factuur\s*)?(?:nr\.?|no\.?)\s*[:#.]?\s*"
    r"[A-Za-z0-9][A-Za-z0-9\-\/]*\s+van\s+"
    r"(\d{1,2}[\./-]\d{1,2}[\./-]\d{4}|\d{1,2}[\./-]\d{1,2}[\./-]\d{2})\b"
)

_DD_MM_YYYY_RE = re.compile(
    r"\b(\d{1,2})[\./-](\d{1,2})[\./-](\d{4}|\d{2})\b",
)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_EXCLUDE_HINT_RE = re.compile(
    r"(?i)\b(?:vervaldatum|due\s*date|geleverd|pakbon|ordernummer|leverdatum|afleverbon|leveringsbon)\b"
)

_MONTH_NAME_DATE_RE = re.compile(r"(?i)\b(\d{1,2})\s+([A-Za-z]{3,})\.?\s+(\d{4})\b")
_MONTHS = {
    # NL
    "jan": 1,
    "januari": 1,
    "feb": 2,
    "februari": 2,
    "mrt": 3,
    "maart": 3,
    "apr": 4,
    "april": 4,
    "mei": 5,
    "jun": 6,
    "juni": 6,
    "jul": 7,
    "juli": 7,
    "aug": 8,
    "augustus": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "okt": 10,
    "oct": 10,  # common OCR/pdf variant
    "oktober": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
    # EN (seen in some invoices)
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_FIELD_VALUE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-\/]*")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+\s*@\s*([A-Za-z0-9.-]+\s*\.\s*[A-Za-z]{2,})\b")
_KVK_RE = re.compile(
    r"(?i)\b(?:"
    r"kvk|k\.?v\.?k\.?|kvk\s*nr\.?|kvk-nummer|"
    r"chamber\s+of\s+commerce|coc|handelsregister"
    r")\D{0,16}(\d[\d\s]{6,11})\b"
)
# VAT number (NL) — accept spaced/punctuated variants produced by OCR/PDF text extraction.
_VAT_RE = re.compile(r"(?i)\bN\s*L\s*\d{9}\s*B\s*\d{2}\b")
_VAT_DEBTOR_HINT_RE = re.compile(
    r"(?i)\b(?:uw|your|afnemer|customer|klant)\b[^\n]{0,32}\b(?:btw|vat)\b|"
    r"\b(?:btw|vat)\b[^\n]{0,32}\b(?:uw|your|afnemer|customer|klant)\b"
)
_VAT_LABEL_RE = re.compile(
    r"(?i)\b(?:"
    r"btw(?:\s*nr\.?|\s*nummer|-\s*nummer)?|"
    r"vat(?:\s*id|\s*number|-\s*number|(?:\s+|-)?nr\.?)?"
    r")\b"
)
_VAT_BTW_VALUE_RE = re.compile(
    r"(?i)\b(?:btw|vat)\s*:\s*([\d.\s]+B[\d.\s]+)"
)
# EU VAT fallback (landcode + blok); validatie in field_candidates.
_VAT_EU_FALLBACK_RE = re.compile(
    r"(?i)\b([A-Z]{2})[\s.-]*(\d[\dA-Z\s.-]{7,14})\b"
)
_KVK_LABEL_RE = re.compile(
    r"(?i)\b(?:"
    r"kvk|k\.?v\.?k\.?|kvk\s*nr\.?|kvk-nummer|"
    r"chamber\s+of\s+commerce|coc|handelsregister"
    r")\b"
)
_KVK_BUSINESS_BLOCK_RE = re.compile(
    r"(?i)\b(?:"
    r"kvk|handelsregister|chamber|commerce|coc|btw|vat|iban|"
    r"bedrijfsgegevens|statutair|vestiging|trade\s*register"
    r")\b"
)
_EMAIL_CONTACT_LABEL_RE = re.compile(
    r"(?i)\b(?:from|reply-?to|e-?mail|email|contact|support|info)\s*:"
)
_DOMAIN_WWW_RE = re.compile(
    r"(?i)\b(?:www\.)?([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b"
)

_NOISE_WORDS = frozenset({
    "datum", "date", "vervaldatum", "due", "pagina", "page",
    "btw", "vat", "kvk", "iban", "bic", "swift", "bedrag",
    "amount", "totaal", "total", "naam", "name", "adres",
    "omschrijving", "description", "betaling", "payment",
    "betalingstermijn",
    "nummer", "number", "netto", "bruto",
    "op", "klant", "klanten", "klantnr", "uw", "ons", "onze",
    "van", "de", "het", "per", "factuur", "nota", "nr",
    "no", "ref", "je", "te", "voor", "aan",
    "onderwerp", "factuuradres", "afleveradres",
    "debiteur", "debiteurnummer", "debiteurennummer",
    "factuurnummer", "factuurnr",
    # Common "placeholder" wording near invoice-number labels.
    "vermelden",
})
_TOTAL_LINE_HINT_RE = re.compile(
    r"(?i)\b(?:totaal|total|te\s+betalen|te\s+voldoen|totaalfactuurbedrag|totaal\s+factuurbedrag|"
    r"factuurbedrag|factuurtotaal|eindbedrag|amount\s+due)\b"
)
# Labels voor profiel-leren (incl. Pearlpaint-achtige BTW-inclusief regels).
_AMOUNT_PROFILE_LABEL_RE = re.compile(
    r"(?i)\b(?:"
    r"totaal|total|te\s+betalen|te\s+voldoen|totaalfactuurbedrag|totaal\s+factuurbedrag|"
    r"factuurbedrag|factuurtotaal|eindbedrag|amount\s+due|"
    r"btw\s*&\s*bedrag\s*inclusief\s*(?:btw|vat)|"
    r"bedrag\s*inclusief\s*(?:btw|vat)|"
    r"opensta(?:and|ande)\s+premie|verschuldigde\s+(?:premie|premies|bedrag)"
    r")\b"
)
# Skip subtotal / excl / unit-price lines. Avoid bare ``netto``/``bruto`` — PDF table rows often
# contain those column headers on the same line as the payable ``Totaal EUR …`` amount.
_TOTAL_LINE_EXCLUDE_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:excl|exclusive|exclusief|stuksprijs|unit\s*price|prijs\s+per)\b|"
    r"\b(?:nett?obedrag|netto(?:\s+goederen)?waarde|netto\s+goederenbedrag|"
    r"bruto(?:\s+bedrag)?|bedrag\s+nett?o|bedrag\s+bruto)\b|"
    r"\bsub[-\s]*totaal\b|"
    r"\b(?:totaal|total)\s+netto\b|\bnetto\s+(?:totaal|total)\b"
    r")"
)

def _is_noise_value(val: str) -> bool:
    return val.strip().lower() in _NOISE_WORDS

def _looks_like_date_token(val: str) -> bool:
    v = str(val or "").strip()
    return bool(_DD_MM_YYYY_RE.fullmatch(v) or _ISO_DATE_RE.fullmatch(v))


def _is_false_factuurdatum_label(line: str, label_start: int) -> bool:
    """Reject embedded references like ``na factuurdatum`` (payment terms, not a label)."""
    before = str(line[: max(0, label_start)] or "").rstrip()
    return bool(re.search(r"(?i)\bna\s*$", before))


def _truncate_before_payment_due(line: str) -> str:
    """Keep only the segment before payment-due / terms tail on multi-date rows."""
    s = str(line or "")
    cut = len(s)
    for pat in (
        r"(?i)\bbetaling\s+v[oó]r\b",
        r"(?i)\bna\s+factuurdatum\b",
        r"(?i)\bvervaldatum\b",
        r"(?i)\bdue\s*date\b",
    ):
        m = re.search(pat, s)
        if m:
            cut = min(cut, m.start())
    return s[:cut]


def _is_payment_due_context_line(line: str) -> bool:
    low = str(line or "").lower()
    if _DATE_EXCLUDE_HINT_RE.search(line or ""):
        return True
    if "verval" in low or "due" in low:
        return True
    if re.search(r"(?i)\bbetaling\s+v[oó]r\b", line or ""):
        return True
    if re.search(r"(?i)\bna\s+factuurdatum\b", line or ""):
        return True
    return False


def _first_invoice_date_token(segment: str) -> str | None:
    seg = str(segment or "")
    m_iso = _ISO_DATE_RE.search(seg)
    if m_iso:
        return f"{m_iso.group(1)}-{m_iso.group(2)}-{m_iso.group(3)}"
    m = _DD_MM_YYYY_RE.search(seg)
    if m:
        return _iso_from_dmy(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m_name = _MONTH_NAME_DATE_RE.search(seg)
    if m_name:
        day = int(m_name.group(1))
        mon_key = str(m_name.group(2) or "").strip().lower()
        month = _MONTHS.get(mon_key)
        if month:
            return _iso_from_dmy(day, int(month), int(m_name.group(3)))
    return None


def _extract_invoice_date_table_header(lines: list[str]) -> str | None:
    """Map ``Factuurdatum`` column in header/value table rows (Korver-style layouts)."""
    for i, hdr in enumerate(lines):
        if not re.search(r"(?i)\bfactuurdatum\b", hdr):
            continue
        if not re.search(r"(?i)\b(?:factuurnr|factuurnummer)\b", hdr):
            continue
        hdr_words = [w for w in re.split(r"\s+", hdr.strip()) if w]
        date_col: int | None = None
        for wi, w in enumerate(hdr_words):
            if re.fullmatch(r"(?i)factuurdatum", w):
                date_col = wi
                break
        if date_col is None:
            continue
        for j in range(1, 4):
            if i + j >= len(lines):
                break
            val_line = lines[i + j] or ""
            if not val_line.strip():
                continue
            if _is_payment_due_context_line(val_line) and not _DD_MM_YYYY_RE.search(
                _truncate_before_payment_due(val_line)
            ):
                continue
            segment = _truncate_before_payment_due(val_line)
            tokens = [t for t in re.split(r"\s+", segment.strip()) if t]
            if date_col < len(tokens) and _looks_like_date_token(tokens[date_col]):
                iso = _first_invoice_date_token(tokens[date_col])
                if iso:
                    return iso
            iso = _first_invoice_date_token(segment)
            if iso:
                return iso
    return None


def _score_customer_candidate_token(tok: str) -> tuple[int, int, int, int]:
    """Sort key: hogere waarde = méér waarschijnlijk klantcode (ook op vervolgregels)."""
    t = str(tok or "").strip()
    digits = re.sub(r"\D", "", t)
    dlen = len(digits)
    alnum = bool(re.search(r"[A-Za-z]", t) and re.search(r"\d", t))
    pure_digits = bool(re.fullmatch(r"\d{4,10}", t))
    postcode_like = bool(re.fullmatch(r"\d{4}[A-Za-z]{2}", t))
    calendar_year_penalty = -120 if re.fullmatch(r"20\d{2}", t) else 0
    band = 2 if 4 <= dlen <= 8 else (1 if alnum else 0)
    long_penalty = -1 if dlen >= 9 else 0
    digit_shape = 0
    if pure_digits:
        if 5 <= dlen <= 7:
            digit_shape = 3
        elif dlen == 8:
            digit_shape = 2
        elif dlen == 4:
            digit_shape = 1
    return (
        band + long_penalty + calendar_year_penalty,
        digit_shape,
        -1 if postcode_like else 0,
        -dlen,
    )


def _sanitize_customer_number(value: str | None) -> str | None:
    v = str(value or "").strip()
    if not v:
        return None
    # OCR/label bleed: "nr143934" should be "143934".
    m = re.fullmatch(r"(?i)(?:nr|no)\W*(\d{3,})", v)
    if m:
        return m.group(1)
    return v


def _normalize_k_customer_code(raw: str) -> str | None:
    """``K 014135`` / ``K0 14135`` → ``K014135`` (PDF/OCR spaties)."""
    compact = re.sub(r"\s+", "", (raw or "").strip())
    if not re.fullmatch(r"(?i)K\d{4,12}", compact):
        return None
    return "K" + compact[1:]


def _reject_uw_referentie_as_customer(
    value: str | None, text: str
) -> str | None:
    """``Uw referentie 202603`` is geen klantnummer."""
    v = str(value or "").strip()
    if not v:
        return None
    if re.fullmatch(r"20\d{4,6}", v) and re.search(
        rf"(?i)\buw\s+referentie\s+{re.escape(v)}\b", text or ""
    ):
        return None
    return v

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

    # Fast-path: header pattern "Nr <invoice> van <date>" (Frige-like).
    # Do this early, before any "Factuurdatum" header heuristics pick a due date.
    try:
        for i, line in enumerate(lines):
            ln0 = line or ""
            ln1 = (lines[i + 1] if i + 1 < len(lines) else "") or ""
            chunk = f"{ln0}\n{ln1}"
            low = chunk.lower()
            if "van" not in low:
                continue
            if "verval" in low or "due" in low or _DATE_EXCLUDE_HINT_RE.search(chunk):
                continue
            m_nv = _INVOICE_NR_VAN_DATE_RE.search(chunk)
            if not m_nv:
                continue
            tok = m_nv.group(1)
            m_dmy = _DD_MM_YYYY_RE.search(tok)
            if m_dmy:
                inv = _iso_from_dmy(int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3)))
                if inv:
                    return inv, "parsed"
    except Exception:
        pass

    try:
        table_date = _extract_invoice_date_table_header(lines)
        if table_date:
            return table_date, "parsed"
    except Exception:
        pass

    label_hits = 0
    for i, line in enumerate(lines):
        lm = _INVOICE_DATE_LABEL_RE.search(line)
        if not lm:
            continue
        if _is_false_factuurdatum_label(line, lm.start()):
            continue
        label_hits += 1
        # No debug logging here; extraction is deterministic.
        if label_hits <= 2:
            _agent_log(
                "H2",
                "parser/pdf_parser.py:_extract_invoice_date_from_text",
                "invoice_date label hit",
                {
                    "label_hits_so_far": label_hits,
                    "line_preview": re.sub(r"\s+", " ", (line or "")).strip()[:160],
                    "next_line_preview": re.sub(r"\s+", " ", (lines[i + 1] if i + 1 < len(lines) else "")).strip()[:160],
                },
            )
        after = _truncate_before_payment_due(line[lm.end() :])

        # 1) Prefer date tokens on the same line (right after the label).
        iso = _first_invoice_date_token(after)
        if iso:
            return iso, "parsed"

        # 2) Next-line date tokens only if the next line is NOT a due/verval line.
        if i + 1 < len(lines):
            nxt = _truncate_before_payment_due(lines[i + 1] or "")
            if not _is_payment_due_context_line(lines[i + 1] or ""):
                iso = _first_invoice_date_token(nxt)
                if iso:
                    return iso, "parsed"
            else:
                pass

        # Heuristic: some invoices contain a "Factuurdatum" column header with terms,
        # e.g. "Betalingsconditie Factuurdatum 8 dagen - 2%". This is not a date value.
        # In that case, do NOT fall back to nearby dates (often the due date); instead
        # continue searching for the next real "Factuurdatum" label occurrence.
        try:
            line_low = (line or "").lower()
            after_low = (after or "").lower()
            looks_like_terms_header = (
                ("betalingsconditie" in line_low or "betalingstermijn" in line_low)
                and "factuurdatum" in line_low
                and not _DD_MM_YYYY_RE.search(after)
                and not _ISO_DATE_RE.search(after)
                and not _MONTH_NAME_DATE_RE.search(after)
                and (
                    ("dagen" in after_low)
                    or ("dagen" in line_low)
                    or (re.search(r"\b\d+\s+dagen\b", after_low) is not None)
                    or (re.search(r"\b\d+\s+dagen\b", line_low) is not None)
                    or (re.search(r"\b\d+\s*%\b", after_low) is not None)
                    or (re.search(r"\b\d+\s*%\b", line_low) is not None)
                )
            )
            if looks_like_terms_header:
                continue
        except Exception:
            pass

        # If we hit a factuurdatum label but didn't find a date in the immediate chunk,
        # scan a compact lookaround window (both directions) and pick the most recent
        # **non-due/non-verval** date token. This prevents selecting due dates when
        # "Factuurdatum" is used as a column header (e.g. "8 dagen - 2%").
        window_diag: list[dict[str, object]] = []
        strong_candidates: list[str] = []
        weak_candidates2: list[str] = []
        start = max(0, i - 3)
        end = min(len(lines), i + 4)
        for j in range(start, end):
            ln = lines[j] or ""
            segment = _truncate_before_payment_due(ln)
            excluded = _is_payment_due_context_line(ln)
            picked_list = weak_candidates2 if excluded else strong_candidates
            found_any = False
            iso = _first_invoice_date_token(segment)
            if iso:
                picked_list.append(iso)
                found_any = True
            if found_any and len(window_diag) < 10:
                window_diag.append(
                    {
                        "idx": int(j),
                        "excluded": bool(excluded),
                        "line_preview": re.sub(r"\s+", " ", ln).strip()[:160],
                    }
                )

        pick_from = strong_candidates or weak_candidates2
        if pick_from:
            inv = min(pick_from)
            return inv, "parsed"
        # No suitable date found near label; fall through to other strategies.

    # Fallback: generic "datum" lines (excluding due/delivery/order contexts)
    for i, line in enumerate(lines):
        line_low = (line or "").lower()
        if "datum" not in line_low:
            continue
        if _DATE_EXCLUDE_HINT_RE.search(line):
            continue
        if (
            ("betalingsconditie" in line_low or "betalingstermijn" in line_low)
            and "factuurdatum" in line_low
            and not _ISO_DATE_RE.search(line)
            and not _DD_MM_YYYY_RE.search(line)
            and not _MONTH_NAME_DATE_RE.search(line)
        ):
            # Terms headers often have a due date on the next line.
            continue
        # Some PDFs render as: "Datum:" + empty line + "11-02-2026".
        # Scan line-by-line and skip excluded due/delivery lines.
        for j in (0, 1, 2, 3):
            if i + j >= len(lines):
                break
            probe = lines[i + j] or ""
            if _DATE_EXCLUDE_HINT_RE.search(probe):
                continue
            m_iso = _ISO_DATE_RE.search(probe)
            if m_iso:
                inv = f"{m_iso.group(1)}-{m_iso.group(2)}-{m_iso.group(3)}"
                return inv, "parsed"
            m = _DD_MM_YYYY_RE.search(probe)
            if m:
                inv = _iso_from_dmy(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if inv:
                    return inv, "parsed"
            m_name = _MONTH_NAME_DATE_RE.search(probe)
            if m_name:
                day = int(m_name.group(1))
                mon_key = str(m_name.group(2) or "").strip().lower()
                month = _MONTHS.get(mon_key)
                if month:
                    inv = _iso_from_dmy(day, int(month), int(m_name.group(3)))
                    if inv:
                        return inv, "parsed"

    # (Nr...van header handled earlier)

    first_any_date: str | None = None
    # Last resort: collect candidate dates across the document and prefer the
    # most recent plausible date (never oldest-by-default).
    candidates: list[str] = []
    weak_candidates: list[str] = []
    date_diag: list[dict[str, object]] = []
    for line in lines:
        ln = line or ""
        segment = _truncate_before_payment_due(ln)
        iso = _first_invoice_date_token(segment)
        if not iso:
            continue
        excluded = _is_payment_due_context_line(ln) and segment.strip() == ln.strip()
        (weak_candidates if excluded else candidates).append(iso)
        if len(date_diag) < 10:
            date_diag.append(
                {
                    "iso": iso,
                    "excluded": bool(excluded),
                    "line_preview": re.sub(r"\s+", " ", ln).strip()[:160],
                }
            )

    pick_from = candidates or weak_candidates
    if pick_from:
        first_any_date = max(pick_from)

    if first_any_date:
        _agent_log(
            "H2",
            "parser/pdf_parser.py:_extract_invoice_date_from_text",
            "invoice_date fallback_any_date used",
            {"invoice_date": first_any_date, "weak_used": bool(not candidates and weak_candidates)},
        )
        return first_any_date, "parsed"

    if label_hits:
        _agent_log(
            "H2",
            "parser/pdf_parser.py:_extract_invoice_date_from_text",
            "invoice_date missing despite label hits",
            {"label_hits": label_hits},
        )
    return None, "missing"


def extract_invoice_date(text: str | None) -> tuple[str | None, str]:
    """Public wrapper for invoice-date extraction (used for OCR-only re-parse)."""
    try:
        return _extract_invoice_date_from_text(text or "")
    except Exception:
        return None, "missing"


def build_invoice_date_result_snapshot(
    text: str,
    *,
    invoice_date: str | None = None,
    invoice_date_source: str | None = None,
) -> dict[str, Any]:
    """Build hybrid-safe ``invoice_date_result`` using label-scoped legacy extraction."""
    from parser.field_candidates import (
        IdentFieldCandidate,
        IdentFieldResult,
        extract_invoice_date_result,
    )

    legacy_date, legacy_src = _extract_invoice_date_from_text(text or "")
    chosen_date = legacy_date or (str(invoice_date or "").strip() or None)
    chosen_src = legacy_src if legacy_date else (str(invoice_date_source or "").strip() or "parsed")
    if not chosen_date:
        return extract_invoice_date_result(text or "").to_dict()

    date_result = extract_invoice_date_result(
        text or "",
        resolved=chosen_date,
        resolved_source=chosen_src,
    )
    winning = next(
        (c for c in date_result.candidates if str(c.value or "").strip() == chosen_date),
        None,
    )
    conf = max(int(getattr(winning, "confidence", 0) or 0), 88)
    src = chosen_src or str(getattr(winning, "source", "") or "parsed")
    synced = IdentFieldResult(
        candidates=[
            IdentFieldCandidate(
                value=chosen_date,
                source=src,
                confidence=conf,
                context=str(getattr(winning, "context", "") or ""),
                label=str(getattr(winning, "label", "") or "Factuurdatum"),
                meta={"field_id": "invoice_date", "match_type": "label"},
            )
        ],
        value=chosen_date,
        confidence=conf,
        source=src,
        status="confirmed",
    )
    return synced.to_dict()

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

    def _score_inv(tok: str) -> tuple[int, int]:
        t = str(tok or "")
        has_hyphen = 1 if "-" in t or "/" in t else 0
        has_letters = 1 if re.search(r"[A-Za-z]", t) else 0
        has_digits = 1 if re.search(r"\d", t) else 0
        return (has_hyphen * 3 + has_letters * 2 + has_digits, len(t))

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

        # Header rows often contain multiple labels on one line:
        # "klantnummer Factuurdatum Factuurnr Betalingstermijn …"
        # In that case the value is typically on the next line; avoid picking the next label word.
        if label_re in (_INVOICE_LABEL_RE, _INVOICE_LABEL_RE_NO_POLIS):
            hdr_low = (line or "").lower()
            after_low = (after_stripped or "").lower()
            has_multi_labels = (
                ("factuurdatum" in hdr_low or "invoice date" in hdr_low)
                and ("betalingstermijn" in hdr_low or "betaling" in hdr_low or "due" in hdr_low)
                and ("klant" in hdr_low or "deb" in hdr_low)
            )
            next_is_labelish = bool(
                re.match(
                    r"(?i)^(?:betalingstermijn|factuurdatum|vervaldatum|datum|due|klantnummer|debiteur|uw)\b",
                    after_stripped.strip(),
                )
            )
            if has_multi_labels or next_is_labelish:
                after_stripped = ""

        # PM coded-style invoice ids with a spaced slash: ``2026 / 15``.
        if label_re in (_INVOICE_LABEL_RE, _INVOICE_LABEL_RE_NO_POLIS) and (after_stripped or "").strip():
            m_yrslash = re.match(
                r"(?i)^(\d{4})\s*/\s*(\d{1,6})\b(?!\s*/\s*\d)",
                after_stripped.strip(),
            )
            if m_yrslash:
                return f"{m_yrslash.group(1)}/{m_yrslash.group(2)}"

        # Preserve split customer code forms like "603540 / 880".
        if label_re is _CUSTOMER_LABEL_RE:
            slash_same = re.match(
                r"([A-Za-z0-9][A-Za-z0-9\-]*\s*/\s*[A-Za-z0-9][A-Za-z0-9\-]*)",
                after_stripped,
            )
            if slash_same:
                picked = slash_same.group(1).strip()
                return picked

        # Customer codes are often shorter than invoice numbers on the same line.
        # When multiple plausible tokens exist on the same line/next line, score them.
        if label_re is _CUSTOMER_LABEL_RE:
            candidates_same: list[str] = []
            rem = after_stripped
            while rem:
                vm = _FIELD_VALUE_RE.match(rem)
                if not vm:
                    break
                val = vm.group(0).strip()
                if (
                    len(val) >= min_value_len
                    and not _is_noise_value(val)
                    and not _looks_like_date_token(val)
                    and (not require_digit or any(ch.isdigit() for ch in val))
                ):
                    candidates_same.append(val)
                rem = rem[vm.end():]
                rem = re.sub(r"^[\s:\.\[\]]+", "", rem)
            # Also include next line tokens (common 2-line layouts).
            if i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                rem2 = nxt
                while rem2:
                    vm = _FIELD_VALUE_RE.match(rem2)
                    if not vm:
                        break
                    val = vm.group(0).strip()
                    if (
                        len(val) >= min_value_len
                        and not _is_noise_value(val)
                        and not _looks_like_date_token(val)
                        and (not require_digit or any(ch.isdigit() for ch in val))
                    ):
                        candidates_same.append(val)
                    rem2 = rem2[vm.end():]
                    rem2 = re.sub(r"^[\s:\.\[\]]+", "", rem2)
            if candidates_same:
                best = sorted(candidates_same, key=_score_customer_candidate_token, reverse=True)[0]
                best = re.sub(r"(?i)^(?:nr|no)\W*(\d{3,})$", r"\1", str(best or "").strip())
                return best

        # Skip Dutch postcode false positives (e.g. "1185 XE" from merged columns)
        if re.match(r"\d{4}\s+[A-Z]{2}\b", after_stripped):
            continue

        remainder = after_stripped
        inv_same: list[str] = []
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
                if label_re in (_INVOICE_LABEL_RE, _INVOICE_LABEL_RE_NO_POLIS):
                    inv_same.append(val)
                else:
                    return val
            remainder = remainder[vm.end():]
            remainder = re.sub(r"^[\s:\.\[\]]+", "", remainder)
        if inv_same:
            # Prefer invoice-like tokens in same-line multi-token layouts.
            return sorted(inv_same, key=_score_inv, reverse=True)[0]

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
            # PM coded-style invoice ids with a spaced slash can appear on the next line: ``2026 / 15``.
            if label_re in (_INVOICE_LABEL_RE, _INVOICE_LABEL_RE_NO_POLIS) and next_line:
                m_yrslash_n = re.match(r"^(\d{4})\s*/\s*(\d{1,6})\b(?!\s*/\s*\d)", next_line)
                if m_yrslash_n:
                    picked = f"{m_yrslash_n.group(1)}/{m_yrslash_n.group(2)}"
                    return picked
            remainder = next_line
            picked_candidates: list[str] = []
            cust_next_tokens: list[str] = []
            for _ in range(5):
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
                    if label_re in (_INVOICE_LABEL_RE, _INVOICE_LABEL_RE_NO_POLIS):
                        picked_candidates.append(val)
                    elif label_re is _CUSTOMER_LABEL_RE:
                        cust_next_tokens.append(val)
                    else:
                        return val
                remainder = remainder[vm.end():]
                remainder = re.sub(r"^[\s:\.\[\]]+", "", remainder)
            if label_re is _CUSTOMER_LABEL_RE and cust_next_tokens:
                cbest = sorted(cust_next_tokens, key=_score_customer_candidate_token, reverse=True)[0]
                cbest = re.sub(r"(?i)^(?:nr|no)\W*(\d{3,})$", r"\1", str(cbest or "").strip())
                return cbest
            if label_re in (_INVOICE_LABEL_RE, _INVOICE_LABEL_RE_NO_POLIS) and picked_candidates:
                # Prefer invoice-like tokens in multi-column value rows (e.g. "VF-1094659")
                best = sorted(picked_candidates, key=_score_inv, reverse=True)[0]
                return best

    return None

def _extract_amounts_from_total_lines(text: str) -> list[Decimal]:
    """Collect fallback amount candidates from total/payable lines."""
    candidates: list[Decimal] = []
    seen: set[Decimal] = set()
    for line in text.splitlines():
        if not _TOTAL_LINE_HINT_RE.search(line):
            continue
        if _TOTAL_LINE_EXCLUDE_RE.search(line):
            continue
        for tok in _iter_amount_tokens_excluding_percent(line):
            v = normalize_amount_decimal(tok)
            if v is not None and v > 0 and v not in seen:
                seen.add(v)
                candidates.append(v)
    return candidates

def _compact_nl_vat_token(raw: str) -> str | None:
    """Normaliseer NL-BTW naar ``NL#########B##`` (OCR met punten/spaties)."""
    compact = re.sub(r"[^0-9A-Za-z]", "", str(raw or "")).upper()
    if not compact:
        return None
    if re.fullmatch(r"NL\d{9}B\d{2}", compact):
        return compact
    if re.fullmatch(r"\d{9}B\d{2}", compact):
        return f"NL{compact}"
    return None


def _iter_supplier_vat_candidates(text: str) -> list[str]:
    """BTW-nummers uit tekst, regels met 'klant/uw BTW' overgeslagen; genormaliseerd uniek volgordelijk."""
    raw_order: list[str] = []
    for line in text.splitlines():
        vat_hits: list[str] = []
        for m in _VAT_RE.finditer(line):
            raw = str(m.group(0) or "")
            compact = _compact_nl_vat_token(raw)
            if compact:
                vat_hits.append(compact)
        if not vat_hits:
            m_btw = re.search(
                r"(?i)\b(?:btw|vat)\s*:\s*([\d.\s]+B[\d.\s]+)",
                line,
            )
            if m_btw:
                compact = _compact_nl_vat_token(m_btw.group(1))
                if compact:
                    vat_hits.append(compact)
        if not vat_hits:
            continue
        if _VAT_DEBTOR_HINT_RE.search(line):
            continue
        for v in vat_hits:
            nv = _normalize_vat_compact(v)
            if nv:
                raw_order.append(nv)
    seen: set[str] = set()
    out: list[str] = []
    for v in raw_order:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _pick_vat_excluding_debtor(candidates: list[str], debtor_vat_norm: str) -> str | None:
    for v in candidates:
        if debtor_vat_norm and v == debtor_vat_norm:
            continue
        return v
    return None


def _pick_kvk_excluding_debtor(text: str, debtor_kvk_norm: str) -> str | None:
    """Eerste KvK naast 'kvk'-label dat niet het debiteur-nummer is."""
    for m in _KVK_RE.finditer(text):
        raw = str(m.group(1) or "").strip()
        if not raw:
            continue
        if debtor_kvk_norm and _normalize_kvk_digits(raw) == debtor_kvk_norm:
            continue
        return raw
    return None

_TOTAL_PAYABLE_LABEL_RE = re.compile(
    rf"(?i)(?:Totaal\s+te\s+betalen|Te\s+voldoen|Total\s+due|"
    rf"Totaal\s+incl\.?\s*BTW|Factuurbedrag|Totaalfactuurbedrag|Totaal\s+factuurbedrag|"
    rf"Totaalbedrag|Te\s+betalen)"
    rf"\s*[:]?\s*(?:EUR\b|€)?\s*({_AMOUNT_TOKEN})",
)

# Never use bare ``"excl" in text`` — it matches inside ``exclusief`` and tokens like ``FactEXCL123``.
_EXCL_TAX_STANDALONE_RE = re.compile(
    r"(?i)(?:\bexcl(?:\.|usief)?\b|\bexclusief\b|\bzonder\s+btw\b)"
)


def _amount_classify_label_head(classification_line: str) -> str:
    """Only the label side of ``ctx >> continuation`` — payment boilerplate must not flip excl/incl."""
    s = str(classification_line or "")
    if ">>" in s:
        s = s.split(">>", 1)[0]
    return re.sub(r"\s+", " ", s.strip()).lower()


def _classify_candidate_amount_type(*, classification_line: str, source: str) -> AmountCandidateType:
    """Heuristiek: 'te betalen' ≈ incl BTW; factuurbedrag conservatief (unknown bij excl/btw op regel)."""
    low = re.sub(r"\s+", " ", (classification_line or "").strip().lower())
    if source == "total_label_excl":
        return "excl"
    if source in ("total_label_insurance", "total_label_btw_inclusive"):
        return "incl"
    if source == "total_label_payable":
        return "incl"
    if source == "total_label_invoice":
        # Table layouts often render a header row like:
        # "… BTW % BTW bedrag Factuurbedrag" and the *amount* appears on the next line.
        # In that case, "BTW" tokens are column headers and should not downgrade "Factuurbedrag".
        head = _amount_classify_label_head(classification_line)
        is_tabular_factuurbedrag_header = (
            ">>" in str(classification_line or "")
            and re.search(r"(?i)\bfactuurbedrag\b", head) is not None
            and re.search(r"(?i)\bbtw\b", head) is not None
            and re.search(r"(?i)\b(?:btw\s*%|btw\s*bedrag|grondslag|netto|goederenbedrag)\b", head) is not None
            and re.search(r"(?i)\b(?:excl(?:\.|usief)?|exclusief|zonder\s+btw)\b", head) is None
        )
        if is_tabular_factuurbedrag_header:
            return "incl"
        if re.search(r"(?i)\bexcl(?:\.|usief)?\b", low) or "exclusief" in low or "zonder btw" in low:
            return "unknown"
        if re.search(r"(?i)\b(?:btw|vat)\b", low):
            return "unknown"
        return "incl"
    if source == "total_label_sum":
        head = _amount_classify_label_head(classification_line)
        strong_m = _TOTAL_SUM_PAYABLE_HEAD_ANCHOR_RE.search(head)
        strong_payable_sum = strong_m is not None
        # Structural excl/subtotal labels. With a strong payable-total anchor, ``Subtotaal`` / ``Nettobedrag`` /
        # ``Netto goederenbedrag`` anywhere on the same line is treated as column/BTW noise (ASF Fischer).
        for _m in _STRICT_SUM_EXCL_HEAD_KW_RE.finditer(head):
            gl = (_m.group(0) or "").lower()
            # ``totaal\s+excl`` (betalingsvoorwaarden / kolomtekst) *vóór* het factuurtotaal-anker is geen type
            # van het totaalbedrag (ASF Fischer: ``Totaal excl. btw …`` links van ``Totaal - factuur - bedrag``).
            if re.match(r"(?i)totaal\s+excl", gl):
                if strong_payable_sum and strong_m is not None and _m.end() <= strong_m.start():
                    continue
                return "excl"
            if strong_payable_sum:
                continue
            return "excl"
        # ``bedrag excl`` alone often sits in payment snippets on the *same* line as ``Totaal … factuur … bedrag`` (ASF Fischer).
        if re.search(r"(?i)\bbedrag\s+excl\b", head) and not strong_payable_sum:
            return "excl"
        # ``excl. btw`` / ``exclusief`` on the *same line* as ``Totaal … bedrag`` is often payment boilerplate, not the total type.
        if not strong_payable_sum:
            if re.search(r"(?i)\bexcl\.?\s*btw\b", head):
                return "excl"
            if _EXCL_TAX_STANDALONE_RE.search(head) and not re.search(
                r"(?i)(?:totaal\s+incl|incl\.?\s*btw|inclusief|including\s+vat)",
                head,
            ):
                return "excl"
        if re.search(r"(?i)(?:totaal\s+incl|incl\.?\s*btw|inclusief|including\s+vat)", head):
            return "incl"
        if re.search(r"(?i)\bnetto\b", head) or re.search(r"(?i)\bbruto\b", head):
            return "unknown"
        # Explicit sum wording (totaalbedrag, …) → default payable incl. unless excl/column noise above.
        return "incl"

    if source == "total_label_generic":
        # If the line contains a "Totaal EUR/€" anchor, ignore "excl/ex. btw" wording that appears
        # *before* that anchor (often shipping/payment terms like "vrachtkosten ... ex. btw").
        m_total_eur = re.search(r"(?i)\btotaal\s+(?:eur|€)\b", low)
        head = low
        if m_total_eur:
            head = low[m_total_eur.start() :]
        if re.search(
            r"(?i)\b(?:totaal\s+excl|bedrag\s+excl|excl\.?\s*btw|subtotaal|nettobedrag|netto\s+goederenbedrag)\b",
            head,
        ):
            return "excl"
        if _EXCL_TAX_STANDALONE_RE.search(head) and not re.search(
            r"(?i)(?:totaal\s+incl|incl\.?\s*btw|inclusief|including\s+vat)",
            head,
        ):
            return "excl"
        if re.search(r"(?i)(?:totaal\s+incl|incl\.?\s*btw|inclusief|including\s+vat)", low):
            return "incl"
        # Flattened PDF tables: ``Netto`` / ``Bruto`` column headers on the same line as ``Totaal EUR …``.
        if re.search(r"(?i)\bnetto\b", low) or re.search(r"(?i)\bbruto\b", low):
            return "unknown"
        return "unknown"
    if source == "total_line_hint":
        return "unknown"
    if source == "fallback_last_token":
        return "unknown"
    return "unknown"


# ``Totaal`` on one PDF line + ``bedrag`` on the next → treat as ``totaalbedrag`` (layout split).
# Lookahead avoids matching ``Totaal bedrijfsnaam`` / ``Totaal bedrag vermeld`` (no amount/EUR/colon).
_PAIR_TOTAALBEDRAG_RE = re.compile(
    r"(?i)\b(?:"
    r"totaalfactuurbedrag|totaal[\s\-:._]+factuurbedrag|totaal[\s\-:._]+factuur[\s\-:._]+bedrag|"
    r"totaal\s+factuurbedrag|totaalbedrag|totaal\s+bedrag"
    r")\b(?:\s*:)?(?=\s*(?:$|eur|€|\d))"
)

# Same wording as the ``total_label_sum`` priority row — used to ignore payment ``excl. btw`` on the same PDF line.
_TOTAL_SUM_PAYABLE_HEAD_ANCHOR_RE = re.compile(
    r"(?i)\b(?:"
    r"totaalfactuurbedrag|totaal[\s\-:._]+factuurbedrag|totaal[\s\-:._]+factuur[\s\-:._]+bedrag|"
    r"totaal\s+factuurbedrag|"
    r"totaalbedrag|totaal\s+bedrag|eindtotaal|opensta(?:and|ande)(?:\s+bedrag)?|"
    r"grand\s+total|invoice\s+total|balance\s+due|gesamtbetrag|rechnungsbetrag"
    r")\b"
)
_STRICT_SUM_EXCL_HEAD_KW_RE = re.compile(
    r"(?i)\b(?:totaal\s+excl|subtotaal|nettobedrag|netto\s+goederenbedrag)\b"
)

_TOTAL_LABEL_PRIORITY: tuple[tuple[int, str, re.Pattern], ...] = (
    # Verzekerings-/premiefacturen (Polaris e.d.): geen klassiek “factuurbedrag”.
    (
        93,
        "total_label_insurance",
        re.compile(
            r"(?i)\b(?:"
            r"opensta(?:and|ande)\s+premie|"
            r"verschuldigde\s+(?:premie|premies|bedrag)|"
            r"premie\s+verschuldigd(?:e)?"
            r")\b"
        ),
    ),
    # Tabellen met “btw & … incl.” (Pearlpaint-achtige layout).
    (
        78,
        "total_label_btw_inclusive",
        re.compile(
            r"(?i)\b(?:"
            r"btw\s*&\s*bedrag\s*inclusief\s*(?:btw|vat)|"
            r"bedrag\s*inclusief\s*(?:btw|vat)"
            r")\b"
        ),
    ),
    # High confidence: explicit payable/amount-due labels.
    (
        100,
        "total_label_payable",
        re.compile(
            r"(?i)\b(?:te\s+betalen|totaal\s+te\s+betalen|totaal\s+te\s+voldoen|amount\s+due|total\s+due)\b"
        ),
    ),
    (95, "total_label_invoice", re.compile(r"(?i)\b(?:factuurbedrag|factuurtotaal|eindbedrag)\b")),
    # NOTE: "Totaal EUR …" is treated as generic total (see selection logic to avoid subtotaal/vrachtkosten traps).
    # Strong invoice totals (often printed with column headers like ``Netto`` on the same PDF text line).
    (
        85,
        "total_label_sum",
        re.compile(
            r"(?i)\b(?:"
            r"totaalfactuurbedrag|totaal[\s\-:._]+factuurbedrag|totaal[\s\-:._]+factuur[\s\-:._]+bedrag|"
            r"totaal\s+factuurbedrag|"
            r"totaalbedrag|totaal\s+bedrag|eindtotaal|opensta(?:and|ande)(?:\s+bedrag)?|"
            r"grand\s+total|invoice\s+total|balance\s+due|gesamtbetrag|rechnungsbetrag"
            r")\b"
        ),
    ),
    # Medium: generic totals (sometimes used for payable amounts, sometimes not).
    (
        70,
        "total_label_generic",
        re.compile(
            r"(?i)\b(?:"
            r"totaalfactuurbedrag|totaal[\s\-:._]+factuurbedrag|totaal[\s\-:._]+factuur[\s\-:._]+bedrag|"
            r"totaal\s+factuurbedrag|totaalbedrag|totaal\s+incl\.?\s*btw|"
            r"(?:(?<!sub-)(?<!sub\s)totaal|total)"
            r")\b"
        ),
    ),
    # Low: explicitly excl/netto/subtotal labels (not payable-incl).
    (
        30,
        "total_label_excl",
        re.compile(
            r"(?i)\b(?:sub[-\s]*totaal|subtotaal|nettobedrag|netto\s+goederenbedrag|"
            r"totaal\s+excl\.?|bedrag\s+excl\.?|excl\.?\s*btw)\b"
        ),
    ),
)

# Table header layouts: amount may appear on the next line under "Totaal incl. BTW".
_TABLE_TOTAL_INCL_HDR_RE = re.compile(
    r"(?i)\b(?:totaal\s+incl\.?\s*btw|totaal\s+incl\.?\s*vat|total\s+incl\.?\s*vat)\b"
)

# VAT summary header rows like:
# "Netto Totaal exclusief BTW BTW basis BTW 21% Totaal"
# Keep strict: besides total+VAT, require summary semantics (excl/basis/bedrag/%).
_VAT_SUMMARY_HDR_RE = re.compile(
    r"(?i)(?=.*\b(?:totaal|total)\b)(?=.*\b(?:btw|vat)\b)(?=.*\b(?:excl|exclusive|exclusief|basis|bedrag|%|percent)\b).+"
)

# Polyglass-achtig: kopregel ``% Bedrag TOTALE FACTUUR`` + bedragen op volgende regel.
_TOTALE_FACTUUR_HDR_RE = re.compile(
    r"(?i)\b(?:%?\s*bedrag\s+)?totale\s+factuur\b"
)

_PERCENT_CONTEXT_RE = re.compile(
    r"(?i)\b(?:btw|vat)\b\s*[\(\[]?\s*([0-9]{1,2}(?:[.,][0-9]{1,2})?)\s*%"
)

def _parse_pct(s: str) -> float | None:
    try:
        t = str(s or "").strip().replace(",", ".")
        if not t:
            return None
        return float(t)
    except Exception:
        return None

def _extract_vat_rate_pct(text: str) -> float | None:
    """Best-effort VAT percentage extraction (e.g. 'BTW(21.00%)', 'BTW 21%')."""
    try:
        t = text or ""
        hits: list[float] = []
        for m in re.finditer(
            r"(?i)\b(?:btw|vat)\b[^\n%]{0,24}\b([0-9]{1,2}(?:[.,][0-9]{1,2})?)\s*%",
            t,
        ):
            v = _parse_pct(m.group(1))
            if v is not None and 0 < v <= 30:
                hits.append(v)
        for m in _PERCENT_CONTEXT_RE.finditer(t):
            v = _parse_pct(m.group(1))
            if v is not None and 0 < v <= 30:
                hits.append(v)
        # Extra fallback: bare percentages like "(21%)" on BTW lines.
        for m in re.finditer(r"(?i)\b(?:btw|vat)\b[^\n%]{0,24}\((\d{1,2})\s*%\)", t):
            v = _parse_pct(m.group(1))
            if v is not None and 0 < v <= 30:
                hits.append(v)
        if not hits:
            return None
        from collections import Counter

        c = Counter(round(x, 2) for x in hits)
        best = sorted(c.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[0][0]
        return float(best)
    except Exception:
        return None

def _refine_excl_vat_using_incl_and_rate(
    text: str,
    *,
    amount_incl: float,
    vat_pct: float,
) -> float | None:
    """Pick the excl amount that best matches incl given VAT% from lines mentioning excl/netto."""
    try:
        if amount_incl <= 0 or vat_pct <= 0:
            return None
        factor = 1.0 + (float(vat_pct) / 100.0)
        best: tuple[float, float] | None = None  # (abs_error, excl)
        lines = (text or "").splitlines()
        for i, line in enumerate(lines):
            low = (line or "").lower()
            if not any(k in low for k in ("excl", "netto", "nettobedrag", "subtotaal", "bedrag")):
                continue
            chunk = [line or ""]
            if i + 1 < len(lines):
                chunk.append(lines[i + 1] or "")
            if i + 2 < len(lines):
                chunk.append(lines[i + 2] or "")
            joined = "\n".join(chunk)
            toks = []
            for ln in joined.splitlines():
                toks.extend(_iter_amount_tokens_excluding_percent(ln))
            for tok in toks:
                v = normalize_amount(tok)
                if not isinstance(v, float) or v <= 0:
                    continue
                incl_guess = v * factor
                err = abs(incl_guess - float(amount_incl))
                if best is None or err < best[0]:
                    best = (err, v)
        if best is None:
            return None
        err, excl = best
        # Only accept if it is plausible (excl should be smaller than incl).
        if excl >= float(amount_incl):
            return None
        # Accept when it matches within a few cents (rounding/formatting noise).
        if err <= 0.05:
            return float(excl)
        return None
    except Exception:
        return None

def _normalize_text_for_amount_labels(text: str) -> str:
    """Strip PDF noise (BOM, soft hyphen, unicode spaces) so total-label regexes hit real invoices."""
    raw = text or ""
    raw = raw.replace("\ufeff", "").replace("\u00ad", "")
    raw = re.sub(r"[\u00a0\u2007\u202f\u2009\u2002\u2003\u3000]", " ", raw)
    return raw


def collapse_stutter_chars(text: str) -> str:
    """
    Normaliseer PDF-tekst waar letters 4+ keer herhaald zijn (Pearlpaint: ``BBBBeee…`` → ``Be…``).

    Gebruikt bij profiel-leren en -extractie zodat labels en contextregels matchen.
    """
    raw = text or ""
    if not raw:
        return raw
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        j = i + 1
        while j < n and raw[j].lower() == c.lower():
            j += 1
        run = j - i
        if run >= 4 and c.isalpha():
            out.append(c)
        else:
            out.append(raw[i:j])
        i = j
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _iter_amount_tokens_excluding_percent(line: str) -> list[str]:
    """Return amount-like tokens on a line, excluding percentage contexts like 'BTW(21.00%)'."""
    ln = line or ""
    # Exclude tokens that appear as percentages (e.g. 21.00 in '21.00%')
    percent_spans: list[tuple[int, int]] = []
    for m in re.finditer(r"(\d{1,2}[.,]\d{2})\s*%", ln):
        percent_spans.append((m.start(1), m.end(1)))
    out: list[str] = []
    for m in re.finditer(_AMOUNT_TOKEN, ln):
        s, e = m.start(), m.end()
        if any(ps <= s <= pe or ps <= e <= pe or (s <= ps and pe <= e) for ps, pe in percent_spans):
            continue
        out.append(m.group(0))
    return out


def _append_value_line_amount_candidates(
    candidates: list[AmountCandidate],
    value_line: str,
    *,
    source: str,
    base_confidence: int,
    context: str,
    classification_line: str,
) -> None:
    """
    Voeg kandidaten toe voor alle bedragen op een waardenregel.

    Bij ``1.063,88 EUR 1.287,29`` moet het betaalbare totaal (laatste bedrag) altijd
    in de kandidatenlijst staan — nooit alleen het eerste token.
    """
    toks = _iter_amount_tokens_excluding_percent(value_line or "")
    decs: list[Decimal] = []
    for t in toks:
        v = normalize_amount_decimal(t)
        if v is not None and v > Decimal("0.00"):
            decs.append(v)
    if not decs:
        return

    hdr = (context.split(">>", 1)[0] if ">>" in (context or "") else (context or "")).strip()
    hdr_low = hdr.lower()
    has_eur = bool(re.search(r"(?i)\b(?:eur|€)\b", value_line or ""))
    is_payable_footer = bool(
        re.search(r"(?i)\b(?:totale\s+factuur|te\s+betalen|factuurbedrag)\b", hdr_low)
    )
    multi_emit = len(decs) >= 2 and (has_eur or is_payable_footer)

    if multi_emit:
        for idx, v in enumerate(decs):
            conf = base_confidence if idx == len(decs) - 1 else max(base_confidence - 18, 42)
            ctype: AmountCandidateType = "incl" if idx == len(decs) - 1 else "excl"
            if idx < len(decs) - 1 and not has_eur:
                ctype = "unknown"
            candidates.append(
                AmountCandidate(
                    value=v,
                    source=source,
                    confidence=conf,
                    context=context,
                    type=ctype,
                )
            )
        return

    pick = decs[-1] if len(decs) >= 2 else decs[0]
    ctype = _classify_candidate_amount_type(
        classification_line=classification_line,
        source=source,
    )
    candidates.append(
        AmountCandidate(
            value=pick,
            source=source,
            confidence=base_confidence,
            context=context,
            type=ctype,
        )
    )


def _pick_labeled_line_amount_decimal(line: str, matched_source: str) -> Decimal | None:
    """Amount on a label row; with multiple tokens, prefer the last amount *after* the last total anchor."""
    ln = line or ""
    toks = _iter_amount_tokens_excluding_percent(ln)
    decs: list[Decimal] = []
    for t in toks:
        v = normalize_amount_decimal(t)
        if v is not None and v > Decimal("0.00"):
            decs.append(v)
    if not decs:
        return None
    if len(decs) == 1 or matched_source not in _SOURCES_WITH_LINE_TOTAL_ANCHOR:
        return decs[-1]
    last_anchor = -1
    for m in _TOTAL_LABEL_ANCHOR_RE.finditer(ln):
        last_anchor = max(last_anchor, m.start())
    if last_anchor < 0:
        return decs[-1]
    after: list[Decimal] = []
    for m in re.finditer(_AMOUNT_TOKEN, ln):
        v = normalize_amount_decimal(m.group(0))
        if v is None or v <= Decimal("0.00"):
            continue
        if m.start() >= last_anchor:
            after.append(v)
    return after[-1] if after else decs[-1]


def _extract_amount_candidates(text: str) -> list[AmountCandidate]:
    """Collect all plausible payable-amount candidates with provenance — no selection."""
    t = _normalize_text_for_amount_labels(text or "")
    if not t.strip():
        return []

    lines = t.splitlines()
    candidates: list[AmountCandidate] = []

    for i, line in enumerate(lines):
        ln = line or ""
        # Explicit freight-cost totals are never invoice totals.
        if re.search(r"(?i)\b(?:totaal\s+vrachtkosten|vrachtkosten\s+totaal)\b", ln):
            continue

        # ``% Bedrag TOTALE FACTUUR`` + ``1.063,88 EUR 1.287,29`` op de volgende regel.
        if _TOTALE_FACTUUR_HDR_RE.search(ln) and i + 1 < len(lines):
            nxt = lines[i + 1] or ""
            if _iter_amount_tokens_excluding_percent(nxt):
                ctx_hdr = re.sub(r"\s+", " ", ln).strip()[:160]
                nxt_ctx = re.sub(r"\s+", " ", nxt).strip()[:160]
                full_ctx = f"{ctx_hdr} >> {nxt_ctx}"
                _append_value_line_amount_candidates(
                    candidates,
                    nxt,
                    source="total_label_invoice",
                    base_confidence=90,
                    context=full_ctx,
                    classification_line=full_ctx,
                )

        # VAT summary tables: header line mentions BTW/totaal, values line contains multiple € amounts.
        # Must run regardless of other "totaal" matches on the page.
        if _VAT_SUMMARY_HDR_RE.search(ln) and i + 1 < len(lines):
            nxt = lines[i + 1] or ""
            toks = _iter_amount_tokens_excluding_percent(nxt)
            # Only treat as VAT-summary when the values row has multiple money columns.
            if len(toks) >= 3:
                decs = [normalize_amount_decimal(t) for t in toks]
                decs = [d for d in decs if d is not None and d > 0]
                if decs:
                    v_last = normalize_amount_decimal(toks[-1])
                    v_max = max(decs)
                    # In VAT summaries the last column is typically the payable total; require it equals max.
                    if v_last is not None and v_last == v_max:
                        ctx = re.sub(r"\s+", " ", ln).strip()[:160]
                        nxt_ctx = re.sub(r"\s+", " ", nxt).strip()[:160]
                        candidates.append(
                            AmountCandidate(
                                value=v_last,
                                source="vat_summary_last_amount",
                                confidence=95,
                                context=f"{ctx} >> {nxt_ctx}",
                                type="incl",
                            )
                        )
        # Physical line ``i`` (never pair-merged) — ``total_label_sum`` incl/excl must not see payment text from ``i+1``.
        line_i_norm = re.sub(r"\s+", " ", ln).strip()[:160]
        matched_prio: int | None = None
        matched_source: str | None = None
        for p, src_tag, rx in _TOTAL_LABEL_PRIORITY:
            if rx.search(ln):
                matched_prio = p
                matched_source = src_tag
                break
        if matched_source == "total_label_generic":
            # "Totaal: € 201,85" / "Totaal EUR 2,65" are usually payable totals.
            if (
                re.search(rf"(?i)\btotaal\b\s*:?\s*(?:eur|€)\s*{_AMOUNT_TOKEN}\b", ln)
                and re.search(r"(?i)\b(?:netto|bruto|btw|vat|basis|grondslag)\b", ln) is None
            ):
                matched_prio = 85
                matched_source = "total_label_sum"
        # Table header: amount may be on the next line under "Totaal incl. BTW".
        # Must run even when generic "totaal" patterns are excluded as table noise.
        if _TABLE_TOTAL_INCL_HDR_RE.search(ln) and i + 1 < len(lines):
            nxt = lines[i + 1] or ""
            toks = _iter_amount_tokens_excluding_percent(nxt)
            if toks:
                pick_tok = toks[-1]
                v = normalize_amount_decimal(pick_tok)
                if v is not None and v > 0:
                    nxt_ctx = re.sub(r"\s+", " ", nxt).strip()[:160]
                    ctx = re.sub(r"\s+", " ", ln).strip()[:160]
                    candidates.append(
                        AmountCandidate(
                            value=v,
                            source="table_total_incl_hdr",
                            confidence=85,
                            context=f"{ctx} >> {nxt_ctx}",
                            type="incl",
                        )
                    )

        if matched_prio is None or matched_source is None:
            continue

        if (
            matched_source == "total_label_payable"
            and re.search(r"(?i)\bte\s+betalen\b", ln)
            and re.search(r"(?i)\b(?:btw|vat)\b", ln)
            and re.search(r"(?i)\b(?:basis|grondslag)\b", ln)
        ):
            # Header-like VAT tables (e.g. "Bedrag BTW % Basis Bedrag Te betalen") are not payable values.
            continue

        # Upgrade weak single-line ``Totaal``/``Total`` hit when ``bedrag`` continues on the next line.
        if (
            matched_source == "total_label_generic"
            and matched_prio == 70
            and i + 1 < len(lines)
        ):
            pair_norm = re.sub(r"\s+", " ", f"{ln} {lines[i + 1] or ''}".strip())
            if _PAIR_TOTAALBEDRAG_RE.search(pair_norm):
                matched_prio = 85
                matched_source = "total_label_sum"
                ln = pair_norm

        if matched_prio < 80 and matched_source != "total_label_excl" and _TOTAL_LINE_EXCLUDE_RE.search(ln):
            continue

        ctx = re.sub(r"\s+", " ", ln).strip()[:160]

        def _classify_line_for_source(classification_line: str) -> str:
            return line_i_norm if matched_source == "total_label_sum" else classification_line

        best_same = _pick_labeled_line_amount_decimal(ln, matched_source)
        if best_same is not None:
            ctype = _classify_candidate_amount_type(
                classification_line=_classify_line_for_source(ctx),
                source=matched_source,
            )
            candidates.append(
                AmountCandidate(
                    value=best_same,
                    source=matched_source,
                    confidence=matched_prio,
                    context=ctx,
                    type=ctype,
                )
            )
            continue

        _sum_scan_all_dist = matched_source == "total_label_sum"
        for dist in (1, 2, 3, 4):
            if i + dist >= len(lines):
                break
            nxt = lines[i + dist] or ""
            toks = _iter_amount_tokens_excluding_percent(nxt)
            if toks:
                nxt_ctx = re.sub(r"\s+", " ", nxt).strip()[:160]
                conf = max(matched_prio - dist * 5, 0)
                if matched_source != "total_label_excl" and matched_prio >= 70:
                    conf = max(conf, 70)
                full_ctx = f"{ctx} >> {nxt_ctx}"
                _append_value_line_amount_candidates(
                    candidates,
                    nxt,
                    source=matched_source,
                    base_confidence=conf,
                    context=full_ctx,
                    classification_line=_classify_line_for_source(full_ctx),
                )
                if not _sum_scan_all_dist:
                    break

    # Fallback tier 1: total-line hints (e.g. "Totaal" / "Te betalen" without label regex)
    total_line_amounts = _extract_amounts_from_total_lines(t)
    for dec in total_line_amounts:
        candidates.append(
            AmountCandidate(
                value=dec,
                source="total_line_hint",
                confidence=40,
                context="(total-line fallback)",
                type="unknown",
            )
        )

    # Fallback tier 2: last amount token in the entire document (confidence 15).
    # Only used when no line matched ``_TOTAL_LABEL_PRIORITY`` — e.g. label wording not in the allow-list.
    # Always keep in ``candidates`` so ambiguous rows still offer a manual pick in the UI.
    amount_matches = re.findall(_AMOUNT_TOKEN, t)
    for a in reversed(amount_matches):
        v = normalize_amount_decimal(a)
        if v is not None and v > 0:
            candidates.append(
                AmountCandidate(
                    value=v,
                    source="fallback_last_token",
                    confidence=15,
                    context="(last amount token in document)",
                    type="unknown",
                )
            )
            break

    # region agent log
    _snip: list[dict[str, Any]] = []
    for _i, _raw in enumerate(lines):
        _s = re.sub(r"\s+", " ", (_raw or "").strip())
        _low = _s.lower()
        if "totaal" in _low and "factuur" in _low:
            _snip.append({"idx": _i, "preview": _s[:140]})
            if len(_snip) >= 10:
                break
    _agent_log(
        "H1",
        "parser/pdf_parser.py:_extract_amount_candidates",
        "amount candidates summary",
        {
            "line_count": len(lines),
            "totaal_factuur_snippets": _snip,
            "candidates_brief": [
                {"src": c.source, "cf": c.confidence, "ty": c.type, "v": str(c.value)}
                for c in candidates[:14]
            ],
            "had_fallback_15": any(c.source == "fallback_last_token" for c in candidates),
        },
    )
    # endregion

    return candidates


def _group_candidates_by_cent(groups_seed: list[AmountCandidate]) -> list[list[AmountCandidate]]:
    groups: list[list[AmountCandidate]] = []
    for c in groups_seed:
        placed = False
        for g in groups:
            if abs(g[0].value - c.value) <= Decimal("0.01"):
                g.append(c)
                placed = True
                break
        if not placed:
            groups.append([c])
    return groups


_TENTATIVE_INCL_SOURCE_RANK: dict[str, int] = {
    "total_label_payable": 5,
    "total_label_invoice": 4,
    "total_label_sum": 3,
    "total_label_generic": 2,
    "total_line_hint": 1,
}


def _tentative_incl_pick(candidates: list[AmountCandidate]) -> AmountCandidate | None:
    """Als de parser ``ambiguous`` geeft: kies het **incl**-label met hoogste betrouwbaarheid (min. 70), nooit ``fallback_last_token``."""
    pool = [
        c
        for c in candidates
        if c.type == "incl" and c.confidence >= 70 and c.source != "fallback_last_token"
    ]
    if not pool:
        return None
    picked = max(pool, key=_amount_pick_key)
    if picked.value is not None and picked.value < Decimal("120"):
        larger = [c for c in pool if c.value is not None and c.value >= Decimal("200")]
        if larger:
            return max(larger, key=_amount_pick_key)
    return picked


def _select_amount_legacy(candidates: list[AmountCandidate]) -> AmountResult:
    """Pre-incl-first decision tree (high-confidence bands, dominant winner)."""
    sorted_cands = sorted(candidates, key=lambda c: c.confidence, reverse=True)
    high = [c for c in sorted_cands if c.confidence >= 70]

    if not high:
        return AmountResult(
            candidates=sorted_cands,
            value=None,
            confidence=0,
            source="NO_HIGH_CONFIDENCE",
            status="ambiguous",
        )

    groups = _group_candidates_by_cent(high)

    if len(groups) == 1:
        g0 = groups[0]
        best = max(g0, key=lambda c: c.confidence)
        # Never auto-pay on an explicit excl/subtotal candidate.
        if best.type == "excl":
            return AmountResult(
                candidates=sorted_cands,
                value=None,
                confidence=0,
                source="NO_PAYABLE_INCL_CANDIDATE",
                status="ambiguous",
            )
        # Conservatief: "Factuurbedrag … excl/btw" → type unknown; niet als enige bron auto-bevestigen.
        if (
            len(g0) == 1
            and g0[0].type == "unknown"
            and g0[0].source == "total_label_invoice"
        ):
            return AmountResult(
                candidates=sorted_cands,
                value=None,
                confidence=0,
                source="UNVERIFIED_INVOICE_LABEL",
                status="ambiguous",
            )
        # Generic ``Totaal`` on lines with netto/bruto column noise → unknown; never auto-confirm.
        if (
            len(g0) == 1
            and g0[0].type == "unknown"
            and g0[0].source == "total_label_generic"
            and (
                re.search(r"(?i)\bnetto\b", str(g0[0].context or ""))
                or re.search(r"(?i)\bbruto\b", str(g0[0].context or ""))
            )
        ):
            return AmountResult(
                candidates=sorted_cands,
                value=None,
                confidence=0,
                source="UNVERIFIED_GENERIC_TOTAL",
                status="ambiguous",
            )
        if (
            len(g0) == 1
            and g0[0].type == "unknown"
            and g0[0].source == "total_label_sum"
            and (
                re.search(r"(?i)\bnetto\b", str(g0[0].context or ""))
                or re.search(r"(?i)\bbruto\b", str(g0[0].context or ""))
            )
        ):
            return AmountResult(
                candidates=sorted_cands,
                value=None,
                confidence=0,
                source="UNVERIFIED_SUM_TOTAL",
                status="ambiguous",
            )
        return AmountResult(
            candidates=sorted_cands,
            value=best.value,
            confidence=best.confidence,
            source=best.source.upper(),
            status="confirmed",
        )

    group_bests = sorted(
        [max(g, key=lambda c: c.confidence) for g in groups],
        key=lambda c: c.confidence,
        reverse=True,
    )
    top = group_bests[0]
    runner_up = group_bests[1]

    if top.confidence >= 85 and (top.confidence - runner_up.confidence) >= 20:
        return AmountResult(
            candidates=sorted_cands,
            value=top.value,
            confidence=min(top.confidence, 85),
            source=top.source.upper(),
            status="confirmed",
        )

    return AmountResult(
        candidates=sorted_cands,
        value=None,
        confidence=0,
        source="CONFLICTING_HIGH_CONFIDENCE",
        status="ambiguous",
    )


# When ``Te betalen`` / explicit payable exists, subtotal-style ``total_label_sum`` lines may still carry incl BTW
# but a different cent total — prefer the payable label (generiek, geen leveranciers-hacks).
_BEATEN_SOURCES_WHEN_EXPLICIT_PAYABLE_INCL = frozenset(
    {
        "total_label_sum",
        "total_label_generic",
        "total_line_hint",
        "fallback_last_token",
    }
)

_PAYABLE_SCORE_MARGIN = 25

_NON_PAYABLE_AMOUNT_CTX_RE = re.compile(
    r"(?i)\b(?:sub[-\s]*totaal|totaal\s+excl|bedrag\s+excl|excl\.?\s*btw|"
    r"nettobedrag|netto\s+goederen|order(?:bedrag)?|bestel(?:bedrag)?|"
    r"korting|discount|stuksprijs)\b"
)
_PAYABLE_AMOUNT_CTX_RE = re.compile(
    r"(?i)\b(?:te\s+betalen|totaal\s+te\s+(?:betalen|voldoen)|amount\s+due|total\s+due)\b"
)


def _amount_payable_score_fields(
    ctype: str,
    source: str,
    context: str,
) -> int:
    """0–100 heuristic for selection only (no new extractors)."""
    ctx = str(context or "")
    src = str(source or "").strip().lower()
    low = ctx.lower()
    amount_type = str(ctype or "unknown")
    if amount_type == "vat":
        return 5
    if amount_type == "excl" or src == "total_label_excl":
        score = 10
    elif src == "total_label_payable":
        score = 100
    elif amount_type == "incl":
        score = 78
    else:
        score = 35
    if _PAYABLE_AMOUNT_CTX_RE.search(low):
        score = max(score, 100)
    if _NON_PAYABLE_AMOUNT_CTX_RE.search(low):
        score = min(score, 22)
    if re.search(
        r"(?i)\b(?:btw\s*bedrag|vat\s*amount|grondslag|tax\s*amount|basis\s*btw)\b",
        low,
    ):
        score = min(score, 18)
    if re.search(r"(?i)\bbtw\b", low) and not _PAYABLE_AMOUNT_CTX_RE.search(low):
        score = min(score, 28)
    if re.search(r"(?i)\b(?:factuurbedrag|factuurtotaal|eindbedrag)\b", low) and amount_type == "incl":
        score = max(score, 72)
    return score


def _amount_payable_score(c: AmountCandidate) -> int:
    return _amount_payable_score_fields(
        str(getattr(c, "type", "unknown") or "unknown"),
        str(c.source or ""),
        str(c.context or ""),
    )


def _amount_pick_key(c: AmountCandidate) -> tuple[int, int, int]:
    return (
        _amount_payable_score(c),
        int(c.confidence or 0),
        _TENTATIVE_INCL_SOURCE_RANK.get(c.source, 0),
    )


def _pick_amount_group_best(group: list[AmountCandidate]) -> AmountCandidate:
    if not group:
        raise ValueError("empty amount group")
    best_ps = max(_amount_payable_score(c) for c in group)
    close = [c for c in group if _amount_payable_score(c) >= best_ps - 10]
    if len(close) == 1:
        return close[0]
    return max(
        close,
        key=lambda c: (
            _amount_payable_score(c),
            int(c.confidence or 0),
            _TENTATIVE_INCL_SOURCE_RANK.get(c.source, 0),
        ),
    )


def _select_amount_core(candidates: list[AmountCandidate]) -> AmountResult:
    """Incl-first + legacy; kan ``ambiguous`` teruggeven (vóór tentative fallback)."""
    if not candidates:
        return AmountResult(
            candidates=[],
            value=None,
            confidence=0,
            source="NO_CANDIDATES",
            status="ambiguous",
        )

    sorted_cands = sorted(candidates, key=lambda c: c.confidence, reverse=True)
    incl_cands = [c for c in candidates if c.type == "incl"]

    if incl_cands:
        groups = _group_candidates_by_cent(incl_cands)
        if len(groups) == 1:
            g0 = groups[0]
            best = _pick_amount_group_best(g0) if len(g0) > 1 else g0[0]
            if best.value is not None and best.value < Decimal("120"):
                alt_pool = [
                    c
                    for c in candidates
                    if c.type != "excl"
                    and c.value is not None
                    and c.value >= Decimal("200")
                    and int(c.confidence or 0) >= 65
                ]
                if alt_pool:
                    alt = max(alt_pool, key=_amount_pick_key)
                    if _amount_payable_score(alt) + 5 >= _amount_payable_score(best):
                        best = alt
            return AmountResult(
                candidates=sorted_cands,
                value=best.value,
                confidence=best.confidence,
                source=best.source.upper(),
                status="confirmed",
            )
        payable_groups = [g for g in groups if any(c.source == "total_label_payable" for c in g)]
        if len(payable_groups) == 1 and all(
            all(c.source in _BEATEN_SOURCES_WHEN_EXPLICIT_PAYABLE_INCL for c in g)
            for g in groups
            if g is not payable_groups[0]
        ):
            pg = payable_groups[0]
            pay = [c for c in pg if c.source == "total_label_payable"]
            pick = _pick_amount_group_best(pay)
            return AmountResult(
                candidates=sorted_cands,
                value=pick.value,
                confidence=pick.confidence,
                source=pick.source.upper(),
                status="confirmed",
            )
        return AmountResult(
            candidates=sorted_cands,
            value=None,
            confidence=0,
            source="INCL_CONFLICT",
            status="ambiguous",
        )

    return _select_amount_legacy(candidates)


def _select_amount(candidates: list[AmountCandidate]) -> AmountResult:
    """Confirmed wanneer hard; anders best incl ≥70 als ``tentative`` (UI markeert review)."""
    res = _select_amount_core(candidates)
    # Safety: when we "confirm" a generic total but there is a much larger other candidate present,
    # treat as ambiguous to prevent wrong payments (review required).
    try:
        if (
            res.status == "confirmed"
            and res.value is not None
            and str(res.source or "").lower() in ("total_label_generic", "total_label_sum", "total_line_hint")
        ):
            chosen = Decimal(str(res.value))
            other_max = None
            for c in (res.candidates or []):
                if c.value is None:
                    continue
                if c.value == chosen:
                    continue
                # Ignore very-low-confidence fallback guesses.
                if int(getattr(c, "confidence", 0) or 0) < 40:
                    continue
                if other_max is None or c.value > other_max:
                    other_max = c.value
            if other_max is not None and chosen > Decimal("0.00") and other_max >= chosen * Decimal("1.50"):
                return AmountResult(
                    candidates=res.candidates,
                    value=None,
                    confidence=0,
                    source="GENERIC_TOTAL_CONFLICT",
                    status="ambiguous",
                )
    except Exception:
        pass
    if res.status != "ambiguous":
        return res
    tent = _tentative_incl_pick(res.candidates)
    if tent is None:
        return res
    return AmountResult(
        candidates=res.candidates,
        value=tent.value,
        confidence=tent.confidence,
        source=tent.source.upper(),
        status="tentative",
    )


def _extract_amount_with_confidence(text: str) -> tuple[float | None, str, str]:
    """Legacy wrapper — returns (amount, source, confidence_label).

    Delegates to _extract_amount_candidates + _select_amount internally.
    Kept for backward compatibility; callers should migrate to AmountResult.
    """
    cands = _extract_amount_candidates(text)
    result = _select_amount(cands)

    if result.value is not None and result.status in ("confirmed", "tentative"):
        amount_float: float | None = float(result.value)
    else:
        amount_float = None

    source = result.source

    # Map status to legacy confidence label
    _STATUS_TO_LEGACY = {
        "confirmed": "high",
        "tentative": "medium",
        "ambiguous": "ambiguous",
        "failed": "missing",
    }
    confidence_label = _STATUS_TO_LEGACY.get(result.status, "missing")

    return amount_float, source, confidence_label

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
        # Accept more real-world label variants (e.g. "Exclusief B.T.W.")
        excl_label_re = re.compile(
            rf"(?i)\b(?:"
            rf"totaal\s+netto\s+goederenwaarde|netto\s+goederenbedrag|"
            rf"totaal\s+excl\.?|bedrag\s+excl\.?|subtotaal|nettobedrag|"
            rf"excl\.?\s*btw|excl\.?\s*b\.?\s*t\.?\s*w\.?|"
            rf"exclusief\s*btw|exclusief\s*b\.?\s*t\.?\s*w\.?"
            rf")\b"
        )
        candidates: list[float] = []
        match_count = 0
        for m in _EXCL_VAT_LABEL_RE.finditer(t):
            match_count += 1
            v = normalize_amount(m.group(1))
            if isinstance(v, float):
                candidates.append(v)
        # Second pass: line-by-line label hit with amount on same/next line.
        if not candidates:
            lines = t.splitlines()
            for i, line in enumerate(lines):
                if not excl_label_re.search(line or ""):
                    continue
                toks_same = _iter_amount_tokens_excluding_percent(line or "")
                if toks_same:
                    pick_tok = toks_same[0] if len(toks_same) >= 2 else toks_same[-1]
                    v = normalize_amount(pick_tok)
                    if isinstance(v, float):
                        candidates.append(v)
                        break
                if i + 1 < len(lines):
                    nxt = lines[i + 1] or ""
                    toks_next = _iter_amount_tokens_excluding_percent(nxt)
                    if toks_next:
                        pick_tok = toks_next[0] if len(toks_next) >= 2 else toks_next[-1]
                        v = normalize_amount(pick_tok)
                        if isinstance(v, float):
                            candidates.append(v)
                            break
        if not candidates:
            # region agent log
            try:
                # Try to surface why we missed, by sampling lines around likely labels.
                samples: list[dict[str, object]] = []
                for line in (t.splitlines() or []):
                    ln = line or ""
                    low = ln.lower()
                    if any(k in low for k in ("netto", "excl", "subtotaal", "bedrag")):
                        toks = _iter_amount_tokens_excluding_percent(ln)
                        samples.append(
                            {
                                "line_preview": re.sub(r"\s+", " ", ln).strip()[:160],
                                "amount_tokens": toks[:3],
                            }
                        )
                    if len(samples) >= 4:
                        break
                _agent_log(
                    "H3",
                    "parser/pdf_parser.py:extract_amount_excl_vat",
                    "amount_excl_vat not found",
                    {
                        "regex_match_count": int(match_count),
                        "sample_lines": samples,
                    },
                )
            except Exception:
                pass
            # endregion
            return None
        picked = max(candidates)
        _agent_log(
            "H3",
            "parser/pdf_parser.py:extract_amount_excl_vat",
            "amount_excl_vat picked",
            {"picked": picked, "candidate_count": int(len(candidates))},
        )
        return picked
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

def extract_invoice_data(
    text: str | None,
    *,
    ocr_text: str | None = None,
    debtor_iban: str | None = None,
    debtor_kvk: str | None = None,
    debtor_vat: str | None = None,
) -> dict[str, Any]:
    """
    Parseer ruwe PDF-tekst naar een Module 3-ready JSON dict.

    Args:
        debtor_iban: If provided, this IBAN is excluded from extraction results
                     (to avoid capturing the user's own IBAN as a supplier IBAN).
        debtor_kvk: Eigen KvK (uit instellingen); wordt nooit als leverancier-KvK gebruikt.
        debtor_vat: Eigen BTW-nummer; wordt nooit als leverancier-BTW gebruikt. Bij meerdere
                    BTW-nummers op de factuur wordt de eerstvolgende na dit nummer gekozen.
    """
    primary_text = text or ""
    ocr_clean = str(ocr_text or "").strip()
    ident_text = primary_text
    if ocr_clean and ocr_clean not in ident_text:
        ident_text = f"{ident_text.rstrip()}\n{ocr_clean}".strip()

    # #region agent log
    try:
        import json as _json
        import time as _time

        payload = {
            "sessionId": "808457",
            "runId": "pre-fix",
            "hypothesisId": "H6",
            "location": "parser/pdf_parser.py:extract_invoice_data:ocr_summary",
            "message": "OCR/ident_text availability for customer_number",
            "data": {
                "primary_len": len(primary_text),
                "ocr_len": len(ocr_clean),
                "ident_len": len(ident_text),
                "ocr_has_k_token": bool(re.search(r"(?i)\bk\s*\d{4,12}\b", ocr_clean)),
                "ocr_has_klant_label": bool(re.search(r"(?i)\bklant(?:nummer|code)\b", ocr_clean)),
                "primary_has_k_token": bool(re.search(r"(?i)\bk\s*\d{4,12}\b", primary_text)),
                "primary_has_klant_label": bool(re.search(r"(?i)\bklant(?:nummer|code)\b", primary_text)),
            },
            "timestamp": int(_time.time() * 1000),
        }
        with open(
            "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-808457.log",
            "a",
            encoding="utf-8",
        ) as _fh:
            _fh.write(_json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # #endregion

    iban: str | None = None
    all_ibans: list[str] = []
    amount: float | None = None
    amount_source: str = "UNKNOWN"
    amount_confidence: str = "missing"
    amount_excl_vat: float | None = None
    invoice_number: str | None = None
    customer_number: str | None = None
    invoice_number_source: str = "unset"
    customer_number_source: str = "unset"
    supplier_hint: str | None = None
    email_domain: str | None = None
    kvk_number: str | None = None
    vat_number: str | None = None
    # Payment term parsing from PDF text intentionally disabled.
    # In practice it proved unreliable and is supplier-master-data (set once in SupplierDB).
    payment_term_days: int | None = None

    debtor_clean = re.sub(r"\s+", "", (debtor_iban or "")).upper() if debtor_iban else ""
    debtor_kvk_norm = _normalize_kvk_digits(debtor_kvk) if debtor_kvk else ""
    debtor_vat_norm = _normalize_vat_compact(debtor_vat) if debtor_vat else ""

    # IBAN — kandidaten + status via iban_result (legacy iban/all_ibans blijven gesynchroniseerd)
    from parser.iban_candidates import extract_iban_result, iban_values_from_candidates
    from parser.field_candidates import IdentFieldResult

    debtor_filtered = 0
    candidates_clean: list[str] = []
    iban_result = IdentFieldResult(status="failed", source="NOT_FOUND")
    try:
        candidates_clean = _scan_sepa_ibans_in_text(ident_text)
        debtor_filtered = sum(
            1 for c in candidates_clean if debtor_clean and c.upper() == debtor_clean
        )
        iban_result = extract_iban_result(ident_text, debtor_iban=debtor_iban)
        iban = iban_result.value
        all_ibans = iban_values_from_candidates(iban_result.candidates)
        if iban:
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
        all_ibans = []
        iban_result = IdentFieldResult(status="failed", source="NOT_FOUND")

    _dbg_935(
        "H4",
        "parser/pdf_parser.py:extract_invoice_data",
        "iban regex diagnostics",
        {
            "sepa_scan_raw_count": int(len(candidates_clean)),
            "all_ibans_count": int(len(all_ibans)),
            "chosen_iban_masked": mask_iban_for_log(iban) if iban else None,
            "debtor_filtered_count": int(debtor_filtered),
        },
        run_id="pre-fix",
    )

    _agent_log(
        "H4",
        "parser/pdf_parser.py:extract_invoice_data",
        "iban extraction summary",
        {
            "debtor_clean_present": bool(debtor_clean),
            "debtor_filtered": int(debtor_filtered),
            "candidates_clean_count": int(len(candidates_clean)),
            "all_ibans_count": int(len(all_ibans)),
            "chosen_iban_masked": mask_iban_for_log(iban) if iban else None,
        },
    )

    # Amount — multi-candidate extraction with explicit status
    amount_result = AmountResult(source="NOT_EVALUATED", status="failed")
    try:
        primary_candidates = _extract_amount_candidates(primary_text)
        amount_result = _select_amount(primary_candidates)

        # OCR override layer: never add candidates; only override when primary truly fails.
        # Allowed only when primary failed OR produced zero candidates. Never for "ambiguous".
        try:
            if (
                ocr_clean
                and (amount_result.status == "failed" or len(primary_candidates) == 0)
            ):
                ocr_candidates = _extract_amount_candidates(ocr_clean)
                ocr_result = _select_amount(ocr_candidates)
                if (
                    ocr_result.value is not None
                    and ocr_result.status in ("confirmed", "tentative")
                ):
                    amount_result = ocr_result
        except Exception:
            pass

        if amount_result.status in ("confirmed", "tentative") and amount_result.value is not None:
            amount = float(amount_result.value)
        else:
            amount = None
        # Legacy fields (deprecated — use amount_result instead)
        amount_source = amount_result.source
        _STATUS_TO_LEGACY_CONF = {
            "confirmed": "high",
            "tentative": "medium",
            "ambiguous": "ambiguous",
            "failed": "missing",
        }
        amount_confidence = _STATUS_TO_LEGACY_CONF.get(amount_result.status, "missing")
        logger.debug(
            "Bedrag: %s (status=%s, confidence=%d, candidates=%d)",
            amount,
            amount_result.status,
            amount_result.confidence,
            len(amount_result.candidates),
        )
        _dbg_935(
            "H7",
            "parser/pdf_parser.py:extract_invoice_data",
            "amount selection summary",
            {
                "status": str(amount_result.status),
                "source": str(amount_result.source),
                "confidence": int(amount_result.confidence),
                "candidate_count": int(len(amount_result.candidates)),
                "candidates_brief": [
                    {
                        "src": str(c.get("source") or ""),
                        "cf": int(c.get("confidence") or 0),
                        "ty": str(c.get("type") or ""),
                        "v": str(c.get("value") or ""),
                    }
                    for c in (amount_result.to_dict().get("candidates") or [])[:8]
                ],
            },
            run_id="pre-fix",
        )
        if amount_result.status == "ambiguous":
            cand_ctx = []
            for c in (amount_result.to_dict().get("candidates") or [])[:8]:
                cand_ctx.append(
                    {
                        "src": str(c.get("source") or ""),
                        "cf": int(c.get("confidence") or 0),
                        "ty": str(c.get("type") or ""),
                        "v": str(c.get("value") or ""),
                        "ctx": re.sub(r"\s+", " ", str(c.get("context") or "")).strip()[:180],
                    }
                )
            sample_lines = []
            for ln in (primary_text or "").splitlines():
                low = (ln or "").lower()
                if "totaal" in low or "total" in low or "betalen" in low or "due" in low or "eur" in low or "€" in low:
                    toks = _iter_amount_tokens_excluding_percent(ln or "")
                    if toks or ("totaal" in low or "total" in low):
                        sample_lines.append(
                            {
                                "line": re.sub(r"\s+", " ", (ln or "")).strip()[:180],
                                "amount_tokens": toks[:4],
                            }
                        )
                if len(sample_lines) >= 10:
                    break
            _dbg_935(
                "H7",
                "parser/pdf_parser.py:extract_invoice_data",
                "amount ambiguous diagnostics",
                {"candidate_contexts": cand_ctx, "sample_lines": sample_lines},
                run_id="pre-fix",
            )
    except Exception:
        logger.debug("Bedrag niet gevonden", exc_info=True)
        amount = None
        amount_source = "EXCEPTION"
        amount_confidence = "missing"
        amount_result = AmountResult(source="EXCEPTION", status="failed")
        pass

    # Amount excl. BTW (nabij label; anders None)
    try:
        amount_excl_vat = extract_amount_excl_vat(primary_text)
        if amount_excl_vat is not None:
            logger.debug("Bedrag excl. BTW gevonden: %s", amount_excl_vat)
        else:
            logger.debug("Bedrag excl. BTW niet gevonden")
    except Exception:
        amount_excl_vat = None
        logger.debug("Bedrag excl. BTW niet gevonden", exc_info=True)

    # Derive excl amount from incl amount + VAT% when missing or clearly wrong (e.g. equals incl).
    try:
        vat_pct = _extract_vat_rate_pct(primary_text)
        _agent_log(
            "H3",
            "parser/pdf_parser.py:extract_invoice_data",
            "vat_pct extraction summary",
            {
                "vat_pct": float(vat_pct) if vat_pct is not None else None,
                "amount_incl": float(amount) if amount is not None else None,
                "amount_excl_vat_before": float(amount_excl_vat) if isinstance(amount_excl_vat, (int, float)) else None,
            },
        )
        if amount is not None and vat_pct and vat_pct > 0:
            # First: if excl is missing or clearly invalid (>= incl), try to refine from totals lines.
            if amount_excl_vat is None or float(amount_excl_vat) >= float(amount):
                refined = _refine_excl_vat_using_incl_and_rate(
                    primary_text,
                    amount_incl=float(amount),
                    vat_pct=float(vat_pct),
                )
                _agent_log(
                    "H3",
                    "parser/pdf_parser.py:extract_invoice_data",
                    "amount_excl_vat refine attempt",
                    {
                        "refined": float(refined) if refined is not None else None,
                        "amount_excl_vat_before": float(amount_excl_vat) if isinstance(amount_excl_vat, (int, float)) else None,
                    },
                )
                if refined is not None:
                    _agent_log(
                        "H3",
                        "parser/pdf_parser.py:extract_invoice_data",
                        "amount_excl_vat refined_from_lines",
                        {
                            "amount_incl": float(amount),
                            "vat_pct": float(vat_pct),
                            "amount_excl_vat_before": amount_excl_vat,
                            "amount_excl_vat_after": refined,
                        },
                    )
                    amount_excl_vat = refined

            if amount_excl_vat is None or abs(float(amount_excl_vat) - float(amount)) <= 0.02:
                from decimal import Decimal as _D

                derived = _D(str(amount)) / (_D("1") + (_D(str(vat_pct)) / _D("100")))
                derived = float(derived.quantize(_D("0.01")))
                _agent_log(
                    "H3",
                    "parser/pdf_parser.py:extract_invoice_data",
                    "amount_excl_vat derived_from_vat",
                    {
                        "amount_incl": float(amount),
                        "vat_pct": float(vat_pct),
                        "amount_excl_vat_before": amount_excl_vat,
                        "amount_excl_vat_after": derived,
                    },
                )
                amount_excl_vat = derived
    except Exception:
        pass

    _agent_log(
        "H3",
        "parser/pdf_parser.py:extract_invoice_data",
        "amount_excl_vat extraction summary",
        {
            "amount_excl_vat": amount_excl_vat,
        },
    )

    # Invoice/customer number: try tabular header layout first, then labeled fields.
    try:
        def _tabular_invoice_customer(lines: list[str]) -> tuple[str | None, str | None]:
            def _parse_vals(val_line: str) -> list[str]:
                raw_tokens = [t for t in re.split(r"\s+", val_line.strip()) if t]
                filtered: list[str] = []
                for tok in raw_tokens:
                    if _DD_MM_YYYY_RE.fullmatch(tok) or _ISO_DATE_RE.fullmatch(tok):
                        continue
                    if re.fullmatch(r"(?i)NL\d{9}B\d{2}", tok.replace(" ", "")):
                        continue
                    filtered.append(tok)
                vals: list[str] = []
                for tok in filtered:
                    clean_tok = re.sub(r"^[\W_]+|[\W_]+$", "", tok)
                    if not clean_tok:
                        continue
                    if re.search(r"[A-Za-z]", clean_tok) and re.search(r"\d", clean_tok):
                        if len(clean_tok) >= 4:
                            vals.append(clean_tok)
                        continue
                    digits = re.sub(r"\D", "", clean_tok)
                    if len(digits) >= 4:
                        vals.append(clean_tok)
                if vals and re.fullmatch(r"20\d{6}", vals[0]):
                    vals = vals[1:]
                return vals

            def _word_indices(hdr: str) -> tuple[int | None, int | None]:
                words = [w.lower() for w in re.findall(r"[A-Za-z]+", hdr or "")]
                inv_i: int | None = None
                cust_i: int | None = None
                for i, w in enumerate(words):
                    if inv_i is None and (
                        "factuurnr" in w
                        or w in ("factuurnummer", "nummer", "invoice")
                        or (
                            w == "factuur"
                            and any(x in words for x in ("relatie", "datum", "nummer", "nr"))
                        )
                    ):
                        inv_i = i
                    if cust_i is None and w in (
                        "betaler",
                        "klant",
                        "debiteur",
                        "debnr",
                        "deb",
                        "customer",
                        "relatie",
                    ):
                        cust_i = i
                return inv_i, cust_i

            for i, hdr in enumerate(lines):
                h = hdr.lower()
                if not ("fact" in h or "fakt" in h or "invoice" in h or "nota" in h or "nummer" in h):
                    continue
                has_inv = bool(
                    not re.search(r"(?i)\bdatum\s+nummer\b", hdr)
                    and (
                        re.search(
                            r"(?i)\b(?:factuurnummer|factuurnr|factuur\s*nr\.?|fact\.?\s*nr\.?|"
                            r"faktuurnummer|faktuurnr\.?|fkt\.?\b|invoice\s*number|invoice\s*no\.?|"
                            r"rechnung\s*(?:nr\.?|nummer)|rechnungsnummer)\b",
                            hdr,
                        )
                        or (
                            re.search(r"(?i)\bfactuur\b", hdr)
                            and re.search(r"(?i)\b(?:relatie|datum)\b", hdr)
                        )
                        or (
                            re.search(r"(?i)\bnummer\b", hdr)
                            and re.search(r"(?i)\b(?:procedure|facturatiedatum)\b", hdr)
                        )
                    )
                )
                has_cust = bool(
                    re.search(
                        r"(?i)\b(?:klant\s*code|klantcode|klant\s*-?\s*nr\.?|klantnr\.?|"
                        r"deb\.?\s*nr\.?|debnr\.?|debiteur|betaler|relatie|"
                        r"customer\s*(?:number|no\.?|nr\.?)?|\bcustomer\b|"
                        r"factureren\s+aan)\b",
                        hdr,
                    )
                )
                if not (has_inv and has_cust):
                    continue
                hdr_fields = sum(
                    1
                    for pat in (
                        r"(?i)\b(?:factuurnr|factuurnummer|factuur\s*nr|fact\.?\s*nr|invoice|nummer)\b",
                        r"(?i)\b(?:betaler|klant|deb|relatie|customer)\b",
                        r"(?i)\b(?:datum|facturatiedatum|procedure)\b",
                        r"(?i)\bordernummer\b",
                    )
                    if re.search(pat, hdr)
                )
                if hdr_fields < 2:
                    continue
                inv_i, cust_i = _word_indices(hdr)
                for j in range(1, 5):
                    if i + j >= len(lines):
                        break
                    val_line = lines[i + j] or ""
                    if not val_line.strip():
                        continue
                    if re.search(r"\b\d{4}\s+[A-Z]{2}\b", val_line):
                        continue
                    order_ref_fact_table = bool(
                        re.search(r"(?i)\border\s*/\s*referentie\b", hdr)
                        and re.search(r"(?i)\bpakbon\b", hdr)
                        and re.search(r"(?i)\bdeb\.?\s*nr\.?\b", hdr)
                        and re.search(r"(?i)\bfact\.?\s*nr\.?\b", hdr)
                        and re.search(r"(?i)\bdatum\b", hdr)
                    )
                    if re.search(
                        r"(?i)\b(?:factuurnummer|factuurnr|invoice\s*no|debiteur|klant\s*nr|"
                        r"ordernummer|betaler|relatie)\b",
                        val_line,
                    ) and not order_ref_fact_table:
                        continue
                    if re.search(r"(?i)\b(?:totaal|te\s+betalen)\b", val_line) and re.search(
                        r"(?i)\b(?:eur|€)\b", val_line
                    ):
                        continue
                    vals = _parse_vals(val_line)
                    if (
                        len(vals) >= 3
                        and vals[0].isdigit()
                        and len(vals[0]) >= 10
                        and vals[0].startswith("0")
                        and re.search(r"(?i)\bfactuurnr\b", hdr)
                        and re.search(r"(?i)\bbetaler\b", hdr)
                    ):
                        vals = vals[1:]
                    if len(vals) < 2:
                        continue
                    idx_inv_chr = (
                        hdr.lower().find("fakt")
                        if "fakt" in hdr.lower()
                        else hdr.lower().find("fact")
                    )
                    idx_deb_chr = hdr.lower().find("deb")
                    if (
                        re.search(r"(?i)\bordernummer\b", hdr)
                        and idx_inv_chr != -1
                        and idx_deb_chr != -1
                        and idx_deb_chr < idx_inv_chr
                        and len(vals) >= 3
                    ):
                        return vals[-1], vals[-2]
                    if (
                        order_ref_fact_table
                        and idx_deb_chr != -1
                        and idx_inv_chr != -1
                        and idx_deb_chr < idx_inv_chr
                        and len(vals) >= 3
                    ):
                        return vals[-1], vals[-2]
                    if inv_i is None:
                        inv_i = 0
                    if cust_i is None:
                        cust_i = 1 if inv_i == 0 else 0
                    tab_inv = vals[inv_i] if inv_i < len(vals) else None
                    tab_cust = vals[cust_i] if cust_i < len(vals) else None
                    if tab_inv and tab_cust:
                        return tab_inv, tab_cust
            return None, None

        lines = primary_text.split("\n")
        tab_inv, tab_cust = _tabular_invoice_customer(lines)
        # no debug logging
        if tab_inv or tab_cust:
            _dbg_935(
                "H5",
                "parser/pdf_parser.py:extract_invoice_data",
                "tabular invoice/customer extraction hit",
                {
                    "tab_invoice": str(tab_inv or ""),
                    "tab_customer": str(tab_cust or ""),
                    "line0_preview": re.sub(r"\s+", " ", (lines[0] if lines else "")).strip()[:160],
                },
                run_id="pre-fix",
            )
        if tab_inv and invoice_number is None:
            invoice_number = tab_inv
            invoice_number_source = "tabular"
        if tab_cust and customer_number is None:
            customer_number = tab_cust
            customer_number_source = "tabular"

        if invoice_number is None:
            # Invoice numbers should contain at least one digit; prevents placeholder words like "vermelden".
            # Prefer explicit invoice labels over "Polisnummer" when both exist (Felison-style layouts).
            invoice_number = _extract_labeled_field(
                primary_text,
                _INVOICE_LABEL_RE_NO_POLIS,
                min_value_len=2,
                require_digit=True,
            )
            if invoice_number is None:
                invoice_number = _extract_labeled_field(
                    primary_text,
                    _INVOICE_LABEL_RE,
                    min_value_len=2,
                    require_digit=True,
                )
            if invoice_number is not None:
                invoice_number_source = "label"
        if invoice_number is None:
            # Fallback for vendors using plain "Factuur : <nr>"
            m_fact = re.search(r"(?im)^\s*Factuur\s*:\s*([A-Za-z0-9][A-Za-z0-9\-\/]{3,})\s*$", primary_text)
            if m_fact:
                invoice_number = m_fact.group(1).strip()
                invoice_number_source = "factuur_colon"
        if invoice_number is None:
            # Fallback for layouts like "Factuur 41107739".
            m_fact_plain = re.search(
                r"(?im)^\s*Factuur\b\s+([A-Za-z0-9][A-Za-z0-9\-\/]{5,})\s*$",
                primary_text,
            )
            if m_fact_plain and re.search(r"\d", m_fact_plain.group(1) or ""):
                invoice_number = m_fact_plain.group(1).strip()
                invoice_number_source = "factuur_plain"
        if invoice_number is None:
            # OEG-like: ``Factuur HA 13451308`` op één regel.
            m_pref = re.search(r"(?i)\bFactuur\s+([A-Za-z]{1,8})\s+(\d{6,})\b", primary_text)
            if m_pref:
                invoice_number = f"{str(m_pref.group(1)).upper()}{m_pref.group(2)}"
                invoice_number_source = "factuur_prefixed_digits"
        if invoice_number is None:
            # Belgische referenties: ``26/1800001827`` (2 cijfers + lange serie).
            m_yrslash = re.search(r"(?<![A-Za-z0-9./])(\d{2}/\d{7,})(?!\d)", primary_text)
            if m_yrslash:
                invoice_number = m_yrslash.group(1).strip()
                invoice_number_source = "year_slash_ref"
        if invoice_number is None:
            # Miko-style table: "Nummer/Datum" + next row "9926106153 / 03.03.2026".
            m_nummer_datum = re.search(
                r"(?is)\bNummer\s*/\s*Datum\b[\s:]*([A-Za-z0-9][A-Za-z0-9\-\/]{4,})\s*/\s*\d{1,2}[\./-]\d{1,2}[\./-]\d{2,4}\b",
                primary_text,
            )
            if m_nummer_datum:
                invoice_number = m_nummer_datum.group(1).strip()
                invoice_number_source = "nummer_datum_table"
        if invoice_number is None:
            # Polyglass e.a.: kop ``Datum Nummer``, regel ``05/03/2026 26FC000498 1/2``.
            for i, hdr in enumerate(lines):
                if not (
                    re.search(r"(?i)\bdatum\b", hdr)
                    and re.search(r"(?i)\bnummer\b", hdr)
                ):
                    continue
                for j in range(1, 4):
                    if i + j >= len(lines):
                        break
                    m_dn = re.match(
                        r"^\s*\d{1,2}/\d{1,2}/\d{4}\s+([A-Za-z0-9][A-Za-z0-9\-\/]{4,})\s",
                        (lines[i + j] or "").strip(),
                    )
                    if m_dn and re.search(r"\d", m_dn.group(1) or ""):
                        invoice_number = m_dn.group(1).strip()
                        invoice_number_source = "datum_nummer_table"
                        break
                if invoice_number:
                    break
        if invoice_number:
            logger.debug("Factuurnummer gevonden: %s", invoice_number)
        else:
            logger.debug("Factuurnummer niet gevonden")
    except Exception:
        logger.debug("Factuurnummer niet gevonden", exc_info=True)
        invoice_number = None
        invoice_number_source = "exception"

    # Customer number (comprehensive label variants, alphanumeric capture)
    try:
        if customer_number is None:
            customer_number = _extract_labeled_field(
                primary_text, _CUSTOMER_LABEL_RE, min_value_len=2, require_digit=True
            )
            if customer_number is not None:
                customer_number_source = "label"
        if customer_number:
            logger.debug("Klantnummer gevonden: %s", customer_number)
        else:
            logger.debug("Klantnummer niet gevonden")
    except Exception:
        logger.debug("Klantnummer niet gevonden", exc_info=True)
        customer_number = None
        customer_number_source = "exception"

    customer_number = _sanitize_customer_number(customer_number)
    customer_number = _reject_uw_referentie_as_customer(customer_number, primary_text)

    if customer_number is None:
        try:
            m_kc = re.search(
                r"(?i)\bklantcode\s*[:#]?\s*((?:K)?\d{4,12})\b", primary_text
            )
            if m_kc:
                customer_number = (
                    _normalize_k_customer_code(m_kc.group(1)) or m_kc.group(1).strip()
                )
                customer_number_source = "klantcode_inline"
        except Exception:
            pass

    if customer_number is None:
        try:
            m_kw = re.search(
                r"(?i)\bUw\s+(?:Klant\s*[:]?\s*)?(K(?:\s*\d){3,12})\b", primary_text
            )
            if m_kw:
                customer_number = _normalize_k_customer_code(m_kw.group(1))
                customer_number_source = "uw_klant_k_prefix"
            if customer_number is None:
                mc = re.search(
                    r"(?i)\bKlant[^\n]{0,48}\s*[:]?\s*(K(?:\s*\d){3,12})\b",
                    primary_text,
                )
                if mc:
                    customer_number = _normalize_k_customer_code(mc.group(1))
                    customer_number_source = "klant_line_k_prefix"
            if customer_number is None:
                mu = re.search(
                    r"(?is)\bUw\s+klant\b[^\n]{0,40}\n\s*(0?\d{4,10})\s*(?:\n|$)",
                    primary_text,
                )
                if mu:
                    digits = mu.group(1).strip()
                    customer_number = f"K{digits}"
                    customer_number_source = "uw_klant_digits_composed"
            if customer_number is None:
                mk = re.search(r"(?is)(?<![a-z])k\s*\n\s*(0?\d{4,10})\b", primary_text)
                if mk:
                    customer_number = f"K{mk.group(1).strip()}"
                    customer_number_source = "split_k_newline"
            if customer_number is None:
                # Pipelife-achtig: eerste 6-cijferige ref vlak onder afleveradres-regelblok.
                md = re.search(
                    r"(?is)\bAfleveradres\b[^\n]{0,88}(?:\n[^\n]*){1,10}?\s*(\d{6})(?!\d)",
                    primary_text,
                )
                if md:
                    customer_number = md.group(1).strip()
                    customer_number_source = "delivery_block_six_digit"
        except Exception:
            pass

    if invoice_number and customer_number and str(invoice_number).strip() == str(customer_number).strip():
        # Suspicious: often caused by merged columns or label mis-detection.
        lines = (primary_text or "").splitlines()
        suspect = []
        for ln in lines:
            low = (ln or "").lower()
            if "factuur" in low or "factuurnr" in low or "invoice" in low or "klant" in low or "deb" in low:
                suspect.append(re.sub(r"\s+", " ", (ln or "")).strip()[:180])
            if len(suspect) >= 8:
                break
        _dbg_935(
            "H8",
            "parser/pdf_parser.py:extract_invoice_data",
            "invoice_number equals customer_number (suspicious)",
            {
                "invoice_number": str(invoice_number),
                "customer_number": str(customer_number),
                "suspect_lines": suspect,
            },
            run_id="pre-fix",
        )

    # Factuurdatum (gelabeld; anders missing)
    try:
        invoice_date, invoice_date_source = _extract_invoice_date_from_text(primary_text)
        if invoice_date:
            logger.debug("Factuurdatum gevonden: %s", invoice_date)
        else:
            logger.debug("Factuurdatum niet gevonden")
    except Exception:
        invoice_date, invoice_date_source = None, "missing"
        logger.debug("Factuurdatum niet gevonden", exc_info=True)

    _agent_log(
        "H2",
        "parser/pdf_parser.py:extract_invoice_data",
        "invoice_date extraction summary",
        {
            "invoice_date": invoice_date,
            "invoice_date_source": invoice_date_source,
        },
    )

    _agent_log(
        "H2",
        "parser/pdf_parser.py:extract_invoice_data",
        "invoice key fields (for missing date triage)",
        {
            "invoice_number": invoice_number,
            "supplier_hint_preview": (str(supplier_hint or "")[:80] if supplier_hint is not None else ""),
                "has_any_date_token_dmy": bool(_DD_MM_YYYY_RE.search(primary_text)),
                "has_any_date_token_iso": bool(_ISO_DATE_RE.search(primary_text)),
                "has_any_date_token_monthname": bool(_MONTH_NAME_DATE_RE.search(primary_text)),
        },
    )

    # Restricted fallback: ``12345/9876`` betaal-/relatiereferenties (NIET het ``26/long`` PGB‑patroon).
    try:
        if customer_number is None or invoice_number is None:
            m_ref = re.search(r"\b(?!\d{2}/\d{7})(\d{5,})\s*/\s*(\d{4,})\b", primary_text)
            if m_ref:
                if invoice_number is None:
                    invoice_number = m_ref.group(1).strip()
                    logger.debug("Factuurnummer via betaalreferentie: %s", invoice_number)
                    invoice_number_source = "ref_slash"
                if customer_number is None:
                    customer_number = m_ref.group(2).strip()
                    logger.debug("Klantnummer via betaalreferentie: %s", customer_number)
                    customer_number_source = "ref_slash"
    except Exception:
        pass

    # Supplier hint (heuristiek; vult alleen aan)
    try:
        # Matcht: `supplier_hint`
        if supplier_hint is None:
            from parser.supplier_rules import extract_supplier_name_hint

            supplier_hint = extract_supplier_name_hint(ident_text)
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
        m_email = _EMAIL_RE.search(ident_text)
        if m_email:
            email_domain = str(m_email.group(1) or "").strip().lower() or None
        kvk_number = _pick_kvk_excluding_debtor(ident_text, debtor_kvk_norm) or None
        vat_candidates = _iter_supplier_vat_candidates(ident_text)
        vat_number = _pick_vat_excluding_debtor(vat_candidates, debtor_vat_norm)
    except Exception:
        email_domain = None
        kvk_number = None
        vat_number = None

    # Payment term in days: disabled (see note above)

    # Type
    try:
        # Matcht: `type`
        if re.search(r"\b(creditnota|credit note|credit|CREN)\b", primary_text, flags=re.IGNORECASE):
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

    _dbg_10a5df(
        "SUM",
        "parser/pdf_parser.py:extract_invoice_data",
        "parse_field_summary",
        {
            "has_iban": bool(iban),
            "iban_masked": mask_iban_for_log(iban),
            "all_ibans_n": len(all_ibans),
            "amount_status": getattr(amount_result, "status", None),
            "amount_src": getattr(amount_result, "source", None),
            "inv_src": invoice_number_source,
            "cust_src": customer_number_source,
        },
        run_id="extract",
    )

    from parser.field_candidates import (
        extract_customer_number_result,
        extract_email_domain_result,
        extract_invoice_number_result,
        extract_kvk_number_result,
        extract_vat_number_result,
    )

    invoice_ident_text = primary_text
    tabular_order_ref_invoice = bool(
        invoice_number_source == "tabular"
        and re.search(
            r"(?im)^.*\border\s*/\s*referentie\b.*\bpakbon\b.*\bdeb\.?\s*nr\.?\b.*\bfact\.?\s*nr\.?\b.*\bdatum\b.*$",
            primary_text,
        )
    )
    explicit_factuur_colon = re.search(
        r"(?im)^\s*Factuur\s*:\s*([A-Za-z0-9][A-Za-z0-9\-\/]{3,})\s*$",
        primary_text,
    )
    explicit_invoice_number = (
        explicit_factuur_colon.group(1).strip() if explicit_factuur_colon else invoice_number
    )
    if explicit_invoice_number and (explicit_factuur_colon or tabular_order_ref_invoice):
        # Preserve explicit invoice context for the structured ranker.
        invoice_ident_text = f"{primary_text.rstrip()}\nFactuurnummer: {explicit_invoice_number}"

    inv_result = extract_invoice_number_result(
        invoice_ident_text,
        resolved=invoice_number,
        resolved_source=invoice_number_source if invoice_number else None,
    )
    if inv_result.value:
        invoice_number = inv_result.value
        invoice_number_source = inv_result.source
    # NOTE: customer codes can live in OCR-only layers; use ident_text (primary + OCR appended).
    cust_result = extract_customer_number_result(
        ident_text,
        resolved=customer_number,
        resolved_source=customer_number_source if customer_number else None,
    )
    if cust_result.value:
        customer_number = cust_result.value
        customer_number_source = cust_result.source

    # New ident-like fields (use ident_text where OCR may contain footer/header details).
    date_result_dict = build_invoice_date_result_snapshot(
        primary_text,
        invoice_date=invoice_date,
        invoice_date_source=invoice_date_source,
    )
    if date_result_dict.get("value"):
        invoice_date = str(date_result_dict.get("value") or "").strip() or invoice_date
        invoice_date_source = str(date_result_dict.get("source") or "").strip() or invoice_date_source

    email_result = extract_email_domain_result(
        ident_text,
        resolved=email_domain,
        resolved_source="parsed" if email_domain else None,
    )
    if email_result.value:
        email_domain = email_result.value

    kvk_result = extract_kvk_number_result(
        ident_text,
        resolved=kvk_number,
        resolved_source="parsed" if kvk_number else None,
        debtor_kvk=debtor_kvk,
    )
    if kvk_result.value:
        kvk_number = kvk_result.value

    vat_result = extract_vat_number_result(
        ident_text,
        resolved=vat_number,
        resolved_source="parsed" if vat_number else None,
        debtor_vat=debtor_vat,
    )
    if vat_result.value:
        vat_number = vat_result.value

    try:
        description = build_description(customer_number, invoice_number)
    except Exception:
        pass

    return {
        "iban": iban,
        "all_ibans": all_ibans,
        "iban_result": iban_result.to_dict(),
        # Legacy amount fields (deprecated — use amount_result)
        "amount": amount,
        "amount_source": amount_source,
        "amount_confidence": amount_confidence,
        # New structured amount result
        "amount_result": amount_result.to_dict(),
        "amount_excl_vat": amount_excl_vat,
        "invoice_number": invoice_number,
        "customer_number": customer_number,
        "invoice_number_result": inv_result.to_dict(),
        "customer_number_result": cust_result.to_dict(),
        "invoice_date_result": date_result_dict,
        "invoice_date": invoice_date,
        "invoice_date_source": invoice_date_source,
        "description": description,
        "type": doc_type,
        "supplier_hint": supplier_hint,
        "email_domain": email_domain,
        "email_domain_result": email_result.to_dict(),
        "kvk_number": kvk_number,
        "kvk_number_result": kvk_result.to_dict(),
        "vat_number": vat_number,
        "vat_number_result": vat_result.to_dict(),
        "payment_term_days": payment_term_days,
        "raw_text": primary_text,
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
    # #region agent log (debug mode - session a6a30a)
    def _dbg_a6(hypothesis_id: str, location: str, message: str, data: dict) -> None:
        try:
            import json, time  # noqa: E401

            payload = {
                "sessionId": "a6a30a",
                "hypothesisId": hypothesis_id,
                "location": location,
                "message": message,
                "data": data,
                "timestamp": int(time.time() * 1000),
                "runId": "ocr-run",
            }
            with open("/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-a6a30a.log", "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass
    # #endregion

    base = Path(str(file_path or "")).name
    base_cf = base.casefold()
    is_target = base_cf in {"aluned 502601306.pdf", "bauder 24065433.pdf"}
    is_debug_935_target = any(
        k in base_cf
        for k in (
            "felison",
            "dissel",
            "korver",
            "labor",
        )
    )

    if _fitz is None:
        if is_debug_935_target:
            _dbg_935(
                "OCR0",
                "parser/pdf_parser.py:extract_text_from_images",
                "PyMuPDF not available; OCR skipped",
                {"pdf": base},
                run_id="pre-fix",
            )
        if is_target:
            _dbg_a6(
                "OCR0",
                "parser/pdf_parser.py:extract_text_from_images",
                "PyMuPDF not available; OCR skipped",
                {},
            )
        logger.debug("PyMuPDF niet beschikbaar — OCR overgeslagen")
        return ""

    try:
        doc = _fitz.open(file_path)
    except Exception:
        if is_debug_935_target:
            _dbg_935(
                "OCR1",
                "parser/pdf_parser.py:extract_text_from_images",
                "Failed to open PDF in PyMuPDF",
                {"pdf": base},
                run_id="pre-fix",
            )
        if is_target:
            _dbg_a6(
                "OCR1",
                "parser/pdf_parser.py:extract_text_from_images",
                "Failed to open PDF in PyMuPDF",
                {},
            )
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

    # Fallback: some PDFs render header "images" as vector paths (no embedded images).
    # If we saw no images at all, try page-level OCR once.
    if image_count == 0 and hasattr(_fitz.Page, "get_textpage_ocr"):
        try:
            doc2 = _fitz.open(file_path)
            page_level_parts: list[str] = []
            for p in doc2:
                try:
                    tp = p.get_textpage_ocr(flags=0, language="nld", dpi=300)
                    t = p.get_text("text", textpage=tp).strip()
                    if t:
                        page_level_parts.append(t)
                except Exception:
                    continue
            doc2.close()
            if page_level_parts:
                text_parts.extend(page_level_parts)
        except Exception:
            pass

    # Deep fallback: render full page to a pixmap and OCR via pytesseract.
    # Ook wanneer embedded images bestaan maar geen IBAN/bankhint opleveren (Omniplast-footer).
    try:
        combined_so_far = "\n".join(text_parts)
        has_iban_bank_hint = bool(
            _scan_sepa_ibans_in_text(combined_so_far)
            or re.search(
                r"(?i)\b(?:iban|rabo|ingb|abna|bic|swift)\b",
                combined_so_far,
            )
        )
        has_any_hint = bool(
            has_iban_bank_hint
            or re.search(r"(?i)\b(?:kvk|k\.?v\.?k\.?|btw|vat)\b", combined_so_far)
            or re.search(r"(?i)\bN\s*L\s*\d{9}\s*B\s*\d{2}\b", combined_so_far)
            or re.search(
                r"\b[A-Za-z0-9._%+-]+\s*@\s*[A-Za-z0-9.-]+\s*\.\s*[A-Za-z]{2,}\b",
                combined_so_far,
            )
        )
        need_page_raster = image_count == 0 or not has_iban_bank_hint
        if need_page_raster and not has_any_hint:
            try:
                from PIL import Image  # noqa: E401
                import pytesseract  # noqa: E401
            except Exception:
                pytesseract = None
            if pytesseract is not None:
                doc3 = _fitz.open(file_path)
                raster_parts: list[str] = []
                for p in doc3:
                    try:
                        mat = _fitz.Matrix(3, 3)
                        pix = p.get_pixmap(matrix=mat, alpha=False)
                        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                        t = pytesseract.image_to_string(img, lang="nld+eng") or ""
                        t = t.strip()
                        if t:
                            raster_parts.append(t)
                    except Exception:
                        continue
                doc3.close()
                if raster_parts:
                    text_parts.extend(raster_parts)
                    if is_debug_935_target:
                        _dbg_935(
                            "OCR3",
                            "parser/pdf_parser.py:extract_text_from_images",
                            "page raster OCR added text",
                            {"pdf": base, "added_chars": int(sum(len(x) for x in raster_parts))},
                            run_id="pre-fix",
                        )
    except Exception:
        pass

    combined = "\n".join(text_parts)
    if is_debug_935_target:
        _dbg_935(
            "OCR2",
            "parser/pdf_parser.py:extract_text_from_images",
            "OCR summary (935 targets)",
            {
                "pdf": base,
                "page_count": int(page_count),
                "image_count": int(image_count),
                "skipped_small": int(skipped_small),
                "pymupdf_used": int(pymupdf_used),
                "pytesseract_used": int(pytesseract_used),
                "pymupdf_nonempty": int(pymupdf_nonempty),
                "pytesseract_nonempty": int(pytesseract_nonempty),
                "weak_primary_count": int(weak_primary_count),
                "combined_chars": int(len(combined)),
                "has_get_textpage_ocr": bool(hasattr(_fitz.Page, "get_textpage_ocr")),
                "ocr_samples": ocr_samples,
            },
            run_id="pre-fix",
        )
    if is_target:
        _dbg_a6(
            "OCR2",
            "parser/pdf_parser.py:extract_text_from_images",
            "OCR summary",
            {
                "pdf": base,
                "page_count": int(page_count),
                "image_count": int(image_count),
                "skipped_small": int(skipped_small),
                "pymupdf_used": int(pymupdf_used),
                "pytesseract_used": int(pytesseract_used),
                "pymupdf_nonempty": int(pymupdf_nonempty),
                "pytesseract_nonempty": int(pytesseract_nonempty),
                "weak_primary_count": int(weak_primary_count),
                "combined_chars": int(len(combined)),
                "has_get_textpage_ocr": bool(hasattr(_fitz.Page, "get_textpage_ocr")),
                "ocr_samples": ocr_samples,
            },
        )
    if combined:
        logger.debug("OCR tekst uit afbeeldingen (%d chars): %.200s", len(combined), combined)
    return combined


def extract_ocr_supplement_text(file_path: str) -> str:
    """Tekst uit embedded afbeeldingen + raster van pagina(’s) (footer in kleine afbeelding/OCR)."""
    parts: list[str] = []
    seen: set[str] = set()
    for chunk in (
        extract_text_from_images(file_path) or "",
        extract_text_force_raster_ocr(file_path, max_pages=1) or "",
    ):
        c = str(chunk or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        parts.append(c)
    return "\n\n".join(parts)


def extract_text_force_raster_ocr(file_path: str, *, max_pages: int = 1) -> str:
    """Force raster-based OCR of full page(s) via pytesseract.

    Used as a last-resort for headers that are not part of the PDF text layer nor embedded images
    (e.g. vector text or complex layouts). Returns empty string if dependencies are unavailable.
    """
    try:
        if _fitz is None:
            return ""
        try:
            from PIL import Image  # noqa: E401
            import pytesseract  # noqa: E401
        except Exception:
            return ""

        doc = _fitz.open(file_path)
        parts: list[str] = []
        page_limit = max(1, int(max_pages))
        for idx, p in enumerate(doc):
            if idx >= page_limit:
                break
            try:
                # High DPI render for small header text.
                mat = _fitz.Matrix(3.5, 3.5)
                pix = p.get_pixmap(matrix=mat, alpha=False)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                t = pytesseract.image_to_string(img, lang="nld+eng") or ""
                t = t.strip()
                if t:
                    parts.append(t)
            except Exception:
                continue
        doc.close()
        combined = "\n".join(parts)
        return combined
    except Exception:
        return ""

def extract_ibans_from_images(file_path: str) -> list[str]:
    """Extract validated SEPA IBANs via OCR (embedded images, daarna pagina-raster)."""
    seen: set[str] = set()
    ordered: list[str] = []

    def _collect(text: str) -> None:
        for iban in _scan_sepa_ibans_in_text(text or ""):
            if iban not in seen:
                seen.add(iban)
                ordered.append(iban)

    img_text = extract_text_from_images(file_path) or ""
    _collect(img_text)

    if not ordered:
        raster_text = extract_text_force_raster_ocr(file_path, max_pages=2) or ""
        _collect(raster_text)

    return ordered

