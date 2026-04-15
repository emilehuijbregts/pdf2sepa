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
            "value": str(self.value) if self.value is not None else None,
            "confidence": self.confidence,
            "source": self.source,
            "status": self.status,
            # Backward-compatible keys
            "selected_amount": str(self.value) if self.value is not None else None,
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
    r"(?:Factuurnummer|Factuur(?:\s*nummer|\s*nr\.?)|Fact\.?\s*nr\.?|"
    r"Invoice\s*(?:number|no\.?|nr\.?)|"
    r"Nota(?:\s*nummer|\s*nr\.?))",
    flags=re.IGNORECASE,
)

_CUSTOMER_LABEL_RE = re.compile(
    r"(?:Klant(?:en)?(?:\s*nummer|\s*nr\.?|\s*code)|Klantnr\.?|"
    r"Debiteur(?:en)?(?:\s*nummer|\s*nr\.?)|"
    r"Deb\.?\s*nr\.?|Debnr\.?|"
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
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_KVK_RE = re.compile(r"(?i)\b(?:kvk|k\.?v\.?k\.?)\D{0,12}(\d{7,8})\b")
_VAT_RE = re.compile(r"(?i)\bNL\d{9}B\d{2}\b")
_VAT_DEBTOR_HINT_RE = re.compile(
    r"(?i)\b(?:uw|your|afnemer|customer|klant)\b[^\n]{0,32}\b(?:btw|vat)\b|"
    r"\b(?:btw|vat)\b[^\n]{0,32}\b(?:uw|your|afnemer|customer|klant)\b"
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
    r"(?i)\b(?:totaal|total|te\s+betalen|te\s+voldoen|totaalfactuurbedrag|totaal\s+factuurbedrag|"
    r"factuurbedrag|factuurtotaal|eindbedrag|amount\s+due)\b"
)
# Skip subtotal / excl / unit-price lines. Avoid bare ``netto``/``bruto`` — PDF table rows often
# contain those column headers on the same line as the payable ``Totaal EUR …`` amount.
_TOTAL_LINE_EXCLUDE_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:excl|exclusive|exclusief|stuksprijs|unit\s*price|prijs\s+per)\b|"
    r"\b(?:nett?obedrag|netto(?:\s+goederen)?waarde|netto\s+goederenbedrag|"
    r"bruto(?:\s+bedrag)?|bedrag\s+nett?o|bedrag\s+bruto)\b|"
    r"\b(?:totaal|total)\s+netto\b|\bnetto\s+(?:totaal|total)\b"
    r")"
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
        m_name = _MONTH_NAME_DATE_RE.search(chunk)
        if m_name:
            day = int(m_name.group(1))
            mon_key = str(m_name.group(2) or "").strip().lower()
            month = _MONTHS.get(mon_key)
            if month:
                inv = _iso_from_dmy(day, int(month), int(m_name.group(3)))
                if inv:
                    return inv, "parsed"

    # Fallback: generic "datum" lines (excluding due/delivery/order contexts)
    for i, line in enumerate(lines):
        if "datum" not in line.lower():
            continue
        if _DATE_EXCLUDE_HINT_RE.search(line):
            continue
        # Some PDFs render as:
        # "Datum:" + empty line + "11-02-2026"
        chunk_parts = [line]
        for j in (1, 2, 3):
            if i + j < len(lines):
                chunk_parts.append(lines[i + j])
        chunk = "\n".join(chunk_parts)
        _agent_log(
            "H2",
            "parser/pdf_parser.py:_extract_invoice_date_from_text",
            "invoice_date generic-datum fallback chunk",
            {"chunk_preview": re.sub(r"\s+", " ", chunk).strip()[:200]},
        )
        m_iso = _ISO_DATE_RE.search(chunk)
        if m_iso:
            inv = f"{m_iso.group(1)}-{m_iso.group(2)}-{m_iso.group(3)}"
            return inv, "parsed"
        m = _DD_MM_YYYY_RE.search(chunk)
        if m:
            inv = _iso_from_dmy(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if inv:
                return inv, "parsed"
        m_name = _MONTH_NAME_DATE_RE.search(chunk)
        if m_name:
            day = int(m_name.group(1))
            mon_key = str(m_name.group(2) or "").strip().lower()
            month = _MONTHS.get(mon_key)
            if month:
                inv = _iso_from_dmy(day, int(month), int(m_name.group(3)))
                if inv:
                    return inv, "parsed"

    first_any_date: str | None = None
    # Last resort: collect candidate dates across the document (invoice date is typically earlier than due date).
    candidates: list[str] = []
    weak_candidates: list[str] = []
    for line in lines:
        ln = line or ""
        low = ln.lower()
        excluded = bool(_DATE_EXCLUDE_HINT_RE.search(ln) or "verval" in low or "due" in low)
        m_iso = _ISO_DATE_RE.search(ln)
        if m_iso:
            iso = f"{m_iso.group(1)}-{m_iso.group(2)}-{m_iso.group(3)}"
            (weak_candidates if excluded else candidates).append(iso)
            continue
        m = _DD_MM_YYYY_RE.search(ln)
        if m:
            iso = _iso_from_dmy(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if iso:
                (weak_candidates if excluded else candidates).append(iso)
                continue
        m_name = _MONTH_NAME_DATE_RE.search(ln)
        if m_name:
            day = int(m_name.group(1))
            mon_key = str(m_name.group(2) or "").strip().lower()
            month = _MONTHS.get(mon_key)
            if month:
                iso = _iso_from_dmy(day, int(month), int(m_name.group(3)))
                if iso:
                    (weak_candidates if excluded else candidates).append(iso)
                    continue

    pick_from = candidates or weak_candidates
    if pick_from:
        first_any_date = min(pick_from)

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

def _extract_amounts_from_total_lines(text: str) -> list[Decimal]:
    """Collect fallback amount candidates from total/payable lines."""
    candidates: list[Decimal] = []
    seen: set[Decimal] = set()
    for line in text.splitlines():
        if not _TOTAL_LINE_HINT_RE.search(line):
            continue
        if _TOTAL_LINE_EXCLUDE_RE.search(line):
            continue
        for tok in re.findall(_AMOUNT_TOKEN, line):
            v = normalize_amount_decimal(tok)
            if v is not None and v > 0 and v not in seen:
                seen.add(v)
                candidates.append(v)
    return candidates

def _iter_supplier_vat_candidates(text: str) -> list[str]:
    """BTW-nummers uit tekst, regels met 'klant/uw BTW' overgeslagen; genormaliseerd uniek volgordelijk."""
    raw_order: list[str] = []
    for line in text.splitlines():
        vat_hits = [m.group(0).strip().upper() for m in _VAT_RE.finditer(line)]
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
        if re.search(
            r"(?i)\b(?:totaal\s+excl|bedrag\s+excl|excl\.?\s*btw|subtotaal|nettobedrag|netto\s+goederenbedrag)\b",
            low,
        ):
            return "excl"
        if _EXCL_TAX_STANDALONE_RE.search(low) and not re.search(
            r"(?i)(?:totaal\s+incl|incl\.?\s*btw|inclusief|including\s+vat)",
            low,
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
    # High confidence: explicit payable/amount-due labels.
    (
        100,
        "total_label_payable",
        re.compile(
            r"(?i)\b(?:te\s+betalen|totaal\s+te\s+betalen|totaal\s+te\s+voldoen|amount\s+due|total\s+due)\b"
        ),
    ),
    (95, "total_label_invoice", re.compile(r"(?i)\b(?:factuurbedrag|factuurtotaal|eindbedrag)\b")),
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
            r"totaal\s+factuurbedrag|totaalbedrag|totaal\s+incl\.?\s*btw|totaal|total"
            r")\b"
        ),
    ),
    # Low: explicitly excl/netto/subtotal labels (not payable-incl).
    (30, "total_label_excl", re.compile(r"(?i)\b(?:subtotaal|nettobedrag|netto\s+goederenbedrag|totaal\s+excl\.?|bedrag\s+excl\.?|excl\.?\s*btw)\b")),
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
        # Physical line ``i`` (never pair-merged) — ``total_label_sum`` incl/excl must not see payment text from ``i+1``.
        line_i_norm = re.sub(r"\s+", " ", ln).strip()[:160]
        matched_prio: int | None = None
        matched_source: str | None = None
        for p, src_tag, rx in _TOTAL_LABEL_PRIORITY:
            if rx.search(ln):
                matched_prio = p
                matched_source = src_tag
                break
        if matched_prio is None or matched_source is None:
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

        if matched_prio < 80 and _TOTAL_LINE_EXCLUDE_RE.search(ln):
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
                pick_tok = toks[-1] if len(toks) >= 2 and matched_prio >= 85 else toks[0]
                v = normalize_amount_decimal(pick_tok)
                if v is not None and v > 0:
                    nxt_ctx = re.sub(r"\s+", " ", nxt).strip()[:160]
                    conf = max(matched_prio - dist * 5, 0)
                    if matched_source != "total_label_excl" and matched_prio >= 70:
                        conf = max(conf, 70)
                    full_ctx = f"{ctx} >> {nxt_ctx}"
                    ctype = _classify_candidate_amount_type(
                        classification_line=_classify_line_for_source(full_ctx),
                        source=matched_source,
                    )
                    candidates.append(
                        AmountCandidate(
                            value=v,
                            source=matched_source,
                            confidence=conf,
                            context=full_ctx,
                            type=ctype,
                        )
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
    return max(
        pool,
        key=lambda c: (
            c.confidence,
            _TENTATIVE_INCL_SOURCE_RANK.get(c.source, 0),
        ),
    )


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
            best = max(groups[0], key=lambda c: c.confidence)
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
            pick = max(pay, key=lambda c: c.confidence)
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
    text = text or ""

    iban: str | None = None
    all_ibans: list[str] = []
    amount: float | None = None
    amount_source: str = "UNKNOWN"
    amount_confidence: str = "missing"
    amount_excl_vat: float | None = None
    invoice_number: str | None = None
    customer_number: str | None = None
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

    # IBAN — find NL IBANs in strict and formatted notation, then filter debtor IBAN
    try:
        found = re.findall(r"\bNL\d{2}[A-Z]{4}\d{10}\b", text, flags=re.IGNORECASE)
        found_spaced_raw = re.findall(
            # Also accept PDFs that emit "N L 12 R A B O 0 1 2 3 4 5 6 7 8 9"
            r"\bN\s*L\s*\d{2}\s*(?:[A-Z]\s*){4}(?:[\s.\-]*\d){10}\b",
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

    _agent_log(
        "H4",
        "parser/pdf_parser.py:extract_invoice_data",
        "iban extraction summary",
        {
            "debtor_clean_present": bool(debtor_clean),
            "debtor_filtered": int(debtor_filtered) if "debtor_filtered" in locals() else None,
            "candidates_clean_count": int(len(candidates_clean)) if "candidates_clean" in locals() else None,
            "all_ibans_count": int(len(all_ibans)),
            "chosen_iban_masked": mask_iban_for_log(iban) if iban else None,
        },
    )

    # Amount — multi-candidate extraction with explicit status
    amount_result = AmountResult(source="NOT_EVALUATED", status="failed")
    try:
        amt_candidates = _extract_amount_candidates(text)
        amount_result = _select_amount(amt_candidates)
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
    except Exception:
        logger.debug("Bedrag niet gevonden", exc_info=True)
        amount = None
        amount_source = "EXCEPTION"
        amount_confidence = "missing"
        amount_result = AmountResult(source="EXCEPTION", status="failed")

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

    # Derive excl amount from incl amount + VAT% when missing or clearly wrong (e.g. equals incl).
    try:
        vat_pct = _extract_vat_rate_pct(text)
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
                    text,
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
            for i, hdr in enumerate(lines[:-1]):
                h = hdr.lower()
                if not ("fact" in h or "fakt" in h or "invoice" in h or "nota" in h):
                    continue
                has_inv = bool(
                    re.search(
                        r"(?i)\b(?:factuurnummer|factuur\s*nr\.?|fact\.?\s*nr\.?|faktuurnummer|faktuurnr\.?|fkt\.?\b|invoice\s*number|invoice\s*no\.?)\b",
                        hdr,
                    )
                )
                has_cust = bool(
                    re.search(
                        r"(?i)\b(?:klant\s*nr\.?|klantnr\.?|deb\.?\s*nr\.?|debnr\.?|debiteur)\b",
                        hdr,
                    )
                )
                if not (has_inv and has_cust):
                    continue
                val_line = lines[i + 1]
                raw_tokens = [t for t in re.split(r"\s+", val_line.strip()) if t]
                filtered: list[str] = []
                for tok in raw_tokens:
                    if _DD_MM_YYYY_RE.fullmatch(tok) or _ISO_DATE_RE.fullmatch(tok):
                        continue
                    if re.fullmatch(r"(?i)NL\d{9}B\d{2}", tok.replace(" ", "")):
                        continue
                    filtered.append(tok)
                nums: list[str] = []
                for tok in filtered:
                    digits = re.sub(r"\D", "", tok)
                    if len(digits) >= 4:
                        nums.append(digits)
                if nums and re.fullmatch(r"20\d{6}", nums[0]):
                    nums = nums[1:]
                if len(nums) < 2:
                    continue
                idx_inv = hdr.lower().find("fakt") if "fakt" in hdr.lower() else hdr.lower().find("fact")
                idx_klant = hdr.lower().find("klant")
                idx_deb = hdr.lower().find("deb")
                if idx_klant != -1 and idx_inv != -1 and idx_inv < idx_klant:
                    return nums[0], nums[-1]
                if idx_deb != -1 and "fact" in hdr.lower():
                    # Customer (deb) before invoice (fact): value row often contains extra numbers,
                    # but the invoice number tends to be the last column.
                    return nums[-1], nums[-2]
                return nums[-2], nums[-1]
            return None, None

        lines = text.split("\n")
        tab_inv, tab_cust = _tabular_invoice_customer(lines)
        if tab_inv and invoice_number is None:
            invoice_number = tab_inv
        if tab_cust and customer_number is None:
            customer_number = tab_cust

        if invoice_number is None:
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
        if customer_number is None:
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
            "has_any_date_token_dmy": bool(_DD_MM_YYYY_RE.search(text)),
            "has_any_date_token_iso": bool(_ISO_DATE_RE.search(text)),
            "has_any_date_token_monthname": bool(_MONTH_NAME_DATE_RE.search(text)),
        },
    )

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
        kvk_number = _pick_kvk_excluding_debtor(text, debtor_kvk_norm) or None
        vat_candidates = _iter_supplier_vat_candidates(text)
        vat_number = _pick_vat_excluding_debtor(vat_candidates, debtor_vat_norm)
    except Exception:
        email_domain = None
        kvk_number = None
        vat_number = None

    # Payment term in days: disabled (see note above)

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
        # Legacy amount fields (deprecated — use amount_result)
        "amount": amount,
        "amount_source": amount_source,
        "amount_confidence": amount_confidence,
        # New structured amount result
        "amount_result": amount_result.to_dict(),
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
    is_target = base.casefold() in {"aluned 502601306.pdf", "bauder 24065433.pdf"}

    if _fitz is None:
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

    combined = "\n".join(text_parts)
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

