"""Kandidaten voor factuur-/klantnummer (zelfde idee als ``AmountResult``)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Union

from parser.pdf_parser import (
    _CUSTOMER_LABEL_RE,
    _DATE_EXCLUDE_HINT_RE,
    _DD_MM_YYYY_RE,
    _DOMAIN_WWW_RE,
    _EMAIL_CONTACT_LABEL_RE,
    _EMAIL_RE,
    _FIELD_VALUE_RE,
    _INVOICE_LABEL_RE,
    _INVOICE_DATE_LABEL_RE,
    _INVOICE_NR_VAN_DATE_RE,
    _ISO_DATE_RE,
    _KVK_BUSINESS_BLOCK_RE,
    _KVK_LABEL_RE,
    _KVK_RE,
    _MONTHS,
    _MONTH_NAME_DATE_RE,
    _VAT_BTW_VALUE_RE,
    _VAT_DEBTOR_HINT_RE,
    _VAT_EU_FALLBACK_RE,
    _VAT_LABEL_RE,
    _VAT_RE,
    _IBAN_ISO_LENGTH_BY_CC,
    _compact_nl_vat_token,
    _is_noise_value,
    _looks_like_date_token,
    _score_customer_candidate_token,
    collapse_stutter_chars,
    _normalize_kvk_digits,
    _normalize_vat_compact,
)

_POLIS_LABEL_RE = re.compile(
    r"(?i)\b(?:Polisnummer|Polis\s*nr\.?|Polis\s*nummer|Polis\s*[:#]?)\b"
)
_RELATIE_LABEL_RE = re.compile(
    r"(?i)\b(?:Relatienummer|Relatie\s*nr\.?|Contractnummer|Contract\s*nr\.?)\b"
)
# Zelfde fallbacks als ``pdf_parser`` (o.a. Polyglass: ``Factuur 26FC000498``).
_FACTUUR_COLON_RE = re.compile(
    r"(?im)^\s*Factuur\s*:\s*([A-Za-z0-9][A-Za-z0-9\-\/]{3,})\s*$"
)
_FACTUUR_PLAIN_RE = re.compile(
    r"(?im)^\s*Factuur\b\s+([A-Za-z0-9][A-Za-z0-9\-\/]{5,})\s*$"
)
_FACTUUR_PREFIXED_RE = re.compile(
    r"(?i)\bFactuur\s+([A-Za-z]{1,8})\s+(\d{6,})\b"
)
_FACTUUR_INLINE_PAGINA_RE = re.compile(
    r"(?i)\bFactuur\s+([A-Za-z0-9][A-Za-z0-9\-\/]{4,})\s+Pagina\b"
)
_NUMMER_INV_RE = re.compile(r"(?i)\bNummer\s+(INV-[A-Za-z0-9\-]+)\b")
_NUMMER_REG_INVOICE_RE = re.compile(
    r"(?i)\bNummer\s+(REG[A-Z0-9][A-Z0-9\-\/]*\d+)\b"
)
_REG_INVOICE_ROW_RE = re.compile(
    r"(?im)^\s*(REG[A-Z0-9][A-Z0-9\-\/]*\d+)\b"
)
_INVOICE_ONLY_LINE_RE = re.compile(
    r"(?i)\bINVOICE\s+([A-Za-z0-9][A-Za-z0-9\-\/]+)"
)
_CUSTOMER_STANDALONE_LINE_RE = re.compile(r"(?im)^Customer\s+(\d{4,12})\b")
_PIPE_SUFFIX_CUSTOMER_RE = re.compile(r"(?i)\|\s*([A-Z]\d{4,8})\b")
_SANHA_KLANT_VALUE_RE = re.compile(
    r"(?is)\bVerzendingswijze\s+Klant\b[^\n]{0,40}\n\s*\S+\s+(\d{5,12})\b"
)
_YEAR_SLASH_REF_RE = re.compile(r"(?<![A-Za-z0-9./])(\d{2}/\d{7,})(?!\d)")
_MULTI_SLASH_INVOICE_RE = re.compile(
    r"(?<![A-Za-z0-9./])(\d{2,4}/\d{2,4}/\d{5,})(?!\d)"
)
_PAYMENT_INVOICE_REF_RE = re.compile(
    r"(?i)\bvermelden\s+(\d{2,4}/\d{2,4}/\d{5,})\b"
)
_NUMMER_DATUM_RE = re.compile(
    r"(?is)\bNummer\s*/\s*Datum\b[\s:]*([A-Za-z0-9][A-Za-z0-9\-\/]{4,})\s*/\s*\d{1,2}[\./-]\d{1,2}[\./-]\d{2,4}\b"
)
# Polyglass e.a.: kop ``Datum Nummer``, waarde ``05/03/2026 26FC000498 1/2``.
_DATE_INVOICE_LINE_RE = re.compile(
    r"^\s*\d{1,2}/\d{1,2}/\d{4}\s+([A-Za-z0-9][A-Za-z0-9\-\/]{4,})\s"
)
# Klantnummer-fallbacks (zelfde patronen als ``pdf_parser``, plus losse K-codes in tekst).
_UW_KLANT_K_RE = re.compile(r"(?i)\bUw\s+(?:Klant\s*[:]?\s*)?(K\d{3,12})\b")
_KLANT_LINE_K_RE = re.compile(r"(?i)\bKlant[^\n]{0,48}\s*[:]?\s*(K\d{3,12})\b")
_DELIVERY_BLOCK_SIX_DIGIT_RE = re.compile(
    r"(?is)\bAfleveradres\b[^\n]{0,88}(?:\n[^\n]*){1,10}?\s*(\d{6})(?!\d)"
)
# K + cijfers: veel leveranciers (Option Tape, Wavin, …); case-insensitive, OCR-spaties.
_STANDALONE_K_CUSTOMER_RE = re.compile(r"(?i)(?<![a-z])(k\d{4,12})(?!\d)")
_SPACED_K_CUSTOMER_RE = re.compile(r"(?i)(?<![a-z])(k(?:\s*\d){4,12})(?!\d)")
_LINE_ONLY_K_CODE_RE = re.compile(r"(?im)^\s*(k(?:\s*\d){4,12})\s*$")
_K_NEWLINE_DIGITS_RE = re.compile(r"(?is)(?<![a-z])k\s*\n\s*(0?\d{4,10})\b")
# Max ~7 cijfers na K (voorkomt ``K0141357550`` op collapsed tekst zonder spaties).
_COLLAPSED_K_IN_TEXT_RE = re.compile(r"(?i)k0?\d{4,7}(?!\d)")
# Labels voor klantnummer/klantcode (layout: label + cel ernaast/eronder).
_CUSTOMER_FIELD_LABEL_RE = re.compile(
    r"(?i)\b(?:klantnummer|klant-nummer|klant\s*nummer|klantcode|klantnr\.?|klant-nr\.?|"
    r"klantrekening|uw\s+klant|klant(?=\s+\d)|"
    r"debiteur(?:en)?(?:\s*nummer|\s*nr\.?)|"
    r"deb\.?\s*(?:nr\.?|nummer)|debnr\.?|debiteur|debtor(?:\s*(?:number|no\.?|nr\.?|id))?|"
    r"betaler(?:\s*(?:nr\.?|nummer|no\.?|id))?|"
    r"relatie(?:\s*nummer|\s*nr\.?)?|relatie|"
    r"customer(?=\s+\d)|customer\s*(?:number|no\.?|code|nr\.?|id)|"
    r"kunden(?:nummer|nr\.?|-\s*nr\.?)|"
    r"factureren\s+aan(?:\s*(?:nr\.?|nummer|no\.?|id))?|"
    r"lid(?:\s*nummer|\s*nr\.?))\b"
)
_REFERENTIE_ONLY_LINE_RE = re.compile(
    r"(?i)\b(?:uw|onze|jullie|your)\s+referentie\b"
)
_KLANTCODE_INLINE_RE = re.compile(
    r"(?i)\bklantcode\s*[:#]?\s*([A-Za-z]?\d{4,12})\b"
)
_UW_REFERENTIE_LINE_RE = re.compile(r"(?i)\buw\s+referentie\b")
_ORDER_REF_TOKEN_RE = re.compile(r"^20\d{4,6}$")
_REF_SLASH_CUSTOMER_RE = re.compile(r"\b(?!\d{2}/\d{7})(\d{5,})\s*/\s*(\d{4,})\b")
_ORDER_HINT_RE = re.compile(
    r"(?i)\b(?:ordernummer|order\s*(?:nr\.?|number|no\.?)|bestel(?:nummer|nr\.?)|purchase\s*order|po\s*number|uw\s+referentie|onze\s+referentie|referentie)\b"
)
_INVOICE_HINT_RE = re.compile(
    r"(?i)\b(?:factuur|factuurnummer|factuurnr|invoice|invoice\s*no\.?|rechnung|"
    r"rechnungsnummer|documentnr\.?|nummer)\b"
)
_EXPLICIT_INVOICE_LABEL_RE = re.compile(
    r"(?i)\b(?:factuurnummer|factuurnr|factuur\s*nr\.?|invoice\s*(?:number|no\.?|nr\.?)?|\binvoice\b|"
    r"rechnung|rechnungsnummer)\b"
)
_STRICT_ORDER_HINT_RE = re.compile(
    r"(?i)\b(?:ordernummer|order\s*(?:nr\.?|number|no\.?)|bestel(?:nummer|nr\.?)|"
    r"purchase\s*order|po\s*number)\b"
)
_PAKBON_HINT_RE = re.compile(
    r"(?i)\b(?:pakbon(?:nummer|-nummer)?|packing\s*slip|leveringsbon|afleverbon)\b"
)
_PAYMENT_TERM_DG_RE = re.compile(r"(?i)^\d{1,3}dg$")
_CREDIT_INVOICE_HINT_RE = re.compile(
    r"(?i)\b(?:creditnota|credit\s*note|verkoopcredit|creditfactuur|creditnota)\b"
)
_CREDIT_NOTE_NUMBER_RE = re.compile(
    r"(?i)\b(?:creditnota|verkoopcreditnota|credit\s*note)\s+(VCR[\dA-Z+]+)"
)
_PARENT_INVOICE_REF_CTX_RE = re.compile(
    r"(?i)\b(?:fact\.?\s*nr\.?|vereffening\s+met\s+factuurnr)\b"
)
_ORDER_VS_INVOICE_PENALTY = 60
_LABEL_SOURCE_PREFIXES = (
    "label",
    "extra",
    "klantcode",
)
_REGEX_SOURCE_PREFIXES = (
    "factuur",
    "year_slash",
    "nummer_datum",
    "date_invoice",
    "header_table",
    "nummer_inv",
    "nummer_reg",
    "invoice_only",
    "customer_standalone",
    "pipe_customer",
    "sanha_klant",
    "split_k",
    "standalone",
    "spaced_k",
    "line_only_k",
    "collapsed",
    "uw_klant",
    "klant_line",
    "delivery_block",
    "ref_slash",
)
# NL-BTW na label (spaties/punten tussen cijfergroepen, OCR).
_VAT_RELAXED_VALUE_RE = re.compile(
    r"(?i)\b(?:NL\s*)?([\d][\d.\s]{8,22}B[\d.\s]{1,4})\b"
)
# Factuurnummer mag nooit uit BTW/VAT-gelabelde context komen.
_INVOICE_VAT_LABEL_CONTEXT_RE = re.compile(
    r"(?i)\b(?:btw|vat)(?:\s*|-|\s*)(?:nummer|number|nr\.?)\b|"
    r"\btax\s*number\b|"
    r"\bbtw-nummer\b"
)
_INVOICE_BANK_TOKEN_RE = re.compile(
    r"(?i)(?:RABO|INGB|ABNA|TRIO|KNAB|BUNQ|ASNB|BNPA|HAND|COBA|DEUT|GEBA|RABONL)"
)
_DEBTOR_ZONE_VAT_RE = re.compile(
    r"(?i)\b(?:"
    r"uw\s+bedrijf|your\s+company|factureren\s+aan|invoice\s+address|"
    r"afleveradres|leveradres|ship\s*to|bill\s*to"
    r")\b"
)
_CUSTOMER_DATE_TOKEN_RE = re.compile(
    r"^\d{1,2}[\./-]\d{1,2}[\./-]\d{2,4}$|^\d{4}[\./-]\d{1,2}[\./-]\d{1,2}$|^\d{8}$"
)
_UNLABELED_CUSTOMER_WHITELIST_SOURCES = frozenset(
    {
        "header_table_customer",
        "uw_klant_k_prefix",
        "klant_line_k_prefix",
        "ref_slash_customer",
        "label_block_same_line",
        "label_block_next_line",
        "label",
        "label_next_line",
        "collapsed_klantcode_fused",
        "klantcode_inline",
        "klantcode_table",
        "standalone_k_token",
        "spaced_k_token",
        "line_only_k_code",
        "collapsed_k_token",
        "collapsed_k_near_label",
        "split_k_line",
        "pipe_customer",
        "delivery_block_six_digit",
        "customer_standalone_line",
    }
)


def parse_internal_vat_numbers(raw: object) -> list[str]:
    """Parse settings ``internal_vat_numbers`` (list or comma/semicolon-separated string)."""
    if raw is None:
        return []
    if isinstance(raw, list):
        out: list[str] = []
        for x in raw:
            out.extend(parse_internal_vat_numbers(x))
        return out
    if isinstance(raw, str):
        return [part.strip() for part in re.split(r"[,;]+", raw) if part.strip()]
    return []


def normalize_internal_vat_numbers_for_storage(raw: object) -> list[str]:
    """Parse and normalize VAT numbers for JSON storage (order preserved, deduped)."""
    seen: set[str] = set()
    out: list[str] = []
    for token in parse_internal_vat_numbers(raw):
        compact = _compact_nl_vat_token(_normalize_vat_compact(token)) or _normalize_vat_compact(token)
        if not compact:
            continue
        key = compact.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def normalize_internal_vat_blacklist(numbers: list[str] | None) -> frozenset[str]:
    out: set[str] = set()
    for raw in numbers or []:
        compact = _compact_nl_vat_token(_normalize_vat_compact(raw)) or _normalize_vat_compact(raw)
        if compact:
            out.add(compact.upper())
    return frozenset(out)


def build_internal_vat_blacklist(raw: object) -> frozenset[str]:
    """Normalize parsed settings value to a compact uppercase blacklist set."""
    return normalize_internal_vat_blacklist(parse_internal_vat_numbers(raw))


def _value_matches_internal_vat(value: str, blacklist: frozenset[str]) -> bool:
    if not blacklist:
        return False
    v = _compact_nl_vat_token(_normalize_vat_compact(value)) or _normalize_vat_compact(value)
    return bool(v and v in blacklist)


def _filter_internal_vat_blacklist(
    cands: list[IdentFieldCandidate],
    blacklist: frozenset[str],
    *,
    field_id: str,
) -> list[IdentFieldCandidate]:
    if not blacklist:
        return cands
    out: list[IdentFieldCandidate] = []
    for c in cands:
        val = str(c.value or "").strip()
        if not val:
            continue
        if field_id == "kvk_number":
            if _value_matches_internal_vat(val, blacklist):
                continue
        elif _value_matches_internal_vat(val, blacklist):
            continue
        out.append(c)
    return out


@dataclass
class IdentFieldCandidate:
    value: str
    source: str
    confidence: int
    context: str
    label: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "context": self.context,
            "label": self.label,
        }
        if self.meta:
            for k, v in self.meta.items():
                if k not in d:
                    d[k] = v
        return d


@dataclass
class IdentFieldResult:
    candidates: list[IdentFieldCandidate] = field(default_factory=list)
    value: str | None = None
    confidence: int = 0
    source: str = "UNKNOWN"
    status: str = "failed"
    user_selected: bool = False
    absence_state: str | None = None
    decision_trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "candidates": [c.to_dict() for c in self.candidates],
            "value": self.value,
            "selected_value": self.value,
            "confidence": self.confidence,
            "source": self.source,
            "status": self.status,
            "decision_trace": list(self.decision_trace),
            "override_reason": "",
            "resolver_finalized": False,
        }
        if self.absence_state:
            d["absence_state"] = self.absence_state
        if self.user_selected:
            d["user_selected"] = True
        return d


def _candidate_explain_meta(
    *,
    extraction_method: str,
    label_reason: str = "",
    context_hint: str = "",
    score_breakdown: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    meta: dict[str, Any] = {"extraction_method": extraction_method}
    if label_reason:
        meta["label_reason"] = label_reason
    if context_hint:
        meta["context_hint"] = context_hint
    if score_breakdown is not None:
        meta["score_breakdown"] = score_breakdown
    for k, v in extra.items():
        if v is not None:
            meta[k] = v
    return meta


def _infer_match_type(source: str) -> str:
    src = str(source or "").strip().lower()
    if not src:
        return "fallback"
    if src.startswith(_LABEL_SOURCE_PREFIXES):
        return "label"
    if src in {
        "datum_nummer_table",
        "nummer_datum_table",
        "header_table_invoice",
        "header_table_customer",
        "nummer_inv",
        "nummer_reg",
        "tabular",
    }:
        return "label"
    if src.startswith(_REGEX_SOURCE_PREFIXES):
        return "regex"
    if src in {"fallback_missing", "resolved", "not_found", "ambiguous"}:
        return "fallback"
    return "fallback"


def _candidate_match_type(cand: IdentFieldCandidate) -> str:
    meta = cand.meta if isinstance(cand.meta, dict) else {}
    mt = str(meta.get("match_type") or "").strip().lower()
    if mt in {"label", "regex", "fallback"}:
        return mt
    return _infer_match_type(cand.source)


def _ensure_candidate_explainability(
    cands: list[IdentFieldCandidate],
) -> list[IdentFieldCandidate]:
    for cand in cands:
        meta = dict(cand.meta or {})
        match_type = str(meta.get("match_type") or "").strip().lower()
        if match_type not in {"label", "regex", "fallback"}:
            match_type = _infer_match_type(cand.source)
            meta["match_type"] = match_type
        label_source = str(meta.get("label_source") or "").strip()
        if not label_source and match_type == "label":
            label_source = str(cand.label or "").strip()
            if label_source:
                meta["label_source"] = label_source
        cand.meta = meta
    return cands


def _is_preferred_customer_label(label: str) -> bool:
    lbl = str(label or "").strip().lower()
    if not lbl:
        return False
    return any(
        key in lbl
        for key in (
            "debiteur",
            "deb.",
            "debtor",
            "klant",
            "customer",
            "betaler",
            "relatie",
            "factureren aan",
            "lidnummer",
            "lid nr",
        )
    )


_MATCH_TYPE_PRIORITY = {
    "label": 3,
    "regex": 2,
    "fallback": 1,
}

_SPECIFIC_LABEL_HINT_RE = re.compile(
    r"(?i)\b(?:factuurnummer|factuurnr|factuur\s*nr\.?|invoice\s*(?:number|no\.?|nr\.?)?|\binvoice\b|"
    r"rechnungsnummer|nummer|"
    r"klantnummer|klantcode|betaler|relatie|customer\s*(?:number|code|id)?|"
    r"polisnummer|relatienummer|contractnummer|"
    r"creditnota|verkoopcredit|creditfactuur|"
    r"btw|vat|kvk|iban|e-?mail)\b"
)

_GENERIC_LABEL_HINT_RE = re.compile(
    r"(?i)\b(?:factuur|invoice|klant|debiteur|customer|nummer|nr\.?|code)\b"
)

_SOURCE_PRIORITY_EXACT: dict[str, int] = {
    "label_block_same_line": 130,
    "label": 126,
    "label_block_next_line": 124,
    "label_next_line": 122,
    "label_block_uw_klant_digits": 120,
    "tabular": 118,
    "extra": 116,
    "datum_nummer_table": 110,
    "header_table_invoice": 109,
    "header_table_customer": 108,
    "nummer_datum_table": 107,
    "nummer_inv": 106,
    "nummer_reg": 105,
    "factuur_inline_pagina": 104,
    "invoice_only_line": 103,
    "customer_standalone_line": 102,
    "pipe_customer": 101,
    "sanha_klant": 100,
    "invoice_nr_van_date": 107,
    "invoice_date_label_same_line": 106,
    "invoice_date_label_next_line": 104,
    "factuur_colon": 98,
    "factuur_plain": 97,
    "factuur_prefixed_digits": 96,
    "date_invoice_line": 95,
    "year_slash_ref": 94,
    "uw_klant_k_prefix": 93,
    "klant_line_k_prefix": 92,
    "standalone_k_token": 91,
    "spaced_k_token": 90,
    "line_only_k_code": 89,
    "split_k_line": 88,
    "split_k_newline": 87,
    "collapsed_k_near_label": 86,
    "collapsed_klantcode_fused": 85,
    "collapsed_k_token": 84,
    "delivery_block_six_digit": 82,
    "ref_slash_customer": 81,
    "fallback_missing": 1,
    "resolved": 2,
}

_SOURCE_PRIORITY_PREFIXES: tuple[tuple[str, int], ...] = (
    ("label_block", 124),
    ("label", 122),
    ("extra", 116),
    ("klantcode", 114),
    ("factuur", 98),
)

FIELD_CONFLICT_RULES: dict[str, tuple[str, ...]] = {
    "invoice_number": ("order_number", "reference_number"),
    "customer_number": ("invoice_number", "order_number"),
}
_CROSS_FIELD_CONFIDENCE_PENALTY = 35


def _candidate_label_haystack(cand: IdentFieldCandidate) -> str:
    meta = cand.meta if isinstance(cand.meta, dict) else {}
    return " ".join(
        (
            str(cand.label or ""),
            str(meta.get("label_source") or ""),
            str(cand.context or ""),
        )
    )


def _has_explicit_invoice_label(cand: IdentFieldCandidate) -> bool:
    return bool(_EXPLICIT_INVOICE_LABEL_RE.search(_candidate_label_haystack(cand)))


def _has_invoice_labeled_peer(cands: list[IdentFieldCandidate]) -> bool:
    return any(_has_explicit_invoice_label(c) for c in cands)


def _field_type_match_score(cand: IdentFieldCandidate, *, field_id: str) -> int:
    """Invoice field: invoice > credit_invoice > reference > order (within same field)."""
    if field_id == "customer_number":
        conflict = _candidate_conflict_type(cand, field_id=field_id)
        if conflict == "invoice_number":
            return 15
        if conflict == "order_number":
            return 20
        digits = re.sub(r"\D", "", str(cand.value or ""))
        if len(digits) >= 10:
            return 25
        score = 50
        if _is_preferred_customer_label(str(cand.label or "")):
            score = 90
        elif _CUSTOMER_LABEL_RE.search(_candidate_label_haystack(cand)):
            score = 75
        if 5 <= len(digits) <= 8:
            score = max(score, 88)
        return score
    if field_id != "invoice_number":
        return 50
    hay = f"{_candidate_label_haystack(cand)} {str(cand.source or '')}"
    value = str(cand.value or "").strip().upper()
    if re.fullmatch(r"NL\d{2}", value) or re.fullmatch(r"[A-Z]{2}\d{2}", value):
        return 12
    conflict = _candidate_conflict_type(cand, field_id=field_id)
    if conflict == "order_number":
        return 15
    if conflict == "reference_number":
        return 25
    if _CREDIT_INVOICE_HINT_RE.search(hay) or value.startswith("VCR"):
        return 95
    if _has_explicit_invoice_label(cand):
        return 90
    if _INVOICE_HINT_RE.search(hay):
        return 75
    return 45


def _context_proximity_score(cand: IdentFieldCandidate) -> int:
    src = str(cand.source or "").strip().lower()
    meta = cand.meta if isinstance(cand.meta, dict) else {}
    method = str(meta.get("extraction_method") or "").strip().lower()
    label_reason = str(meta.get("label_reason") or "").lower()
    if src in {"label_block_same_line", "label"} or "same_line" in src:
        return 100
    if method == "label_match" and "same" in label_reason:
        return 98
    if src in {"label_block_next_line", "label_next_line"} or "next_line" in src:
        return 85
    if src.startswith("header_table"):
        return 92
    if src.startswith("label_block") or src.startswith("label"):
        return 78
    if method == "proximity":
        return 72
    if src.startswith("factuur") or method == "regex":
        return 50
    return 35


def _label_strength(cand: IdentFieldCandidate) -> int:
    mt = _candidate_match_type(cand)
    strength = _MATCH_TYPE_PRIORITY.get(mt, 1) * 100
    if mt != "label":
        return strength
    meta = cand.meta if isinstance(cand.meta, dict) else {}
    label_src = str(meta.get("label_source") or cand.label or cand.source or "").strip()
    if _SPECIFIC_LABEL_HINT_RE.search(label_src):
        strength += 30
    elif _GENERIC_LABEL_HINT_RE.search(label_src):
        strength += 10
    meta = cand.meta if isinstance(cand.meta, dict) else {}
    field_id = str(meta.get("field_id") or "").strip().lower()
    if field_id == "invoice_number" and re.search(
        r"(?i)\b(?:relatie\w*|contract\w*)\b", label_src
    ):
        strength -= 40
    return strength


def _source_priority(cand: IdentFieldCandidate, *, prefer_k_prefix: bool = False) -> int:
    src = str(cand.source or "").strip().lower()
    if src in _SOURCE_PRIORITY_EXACT:
        base = _SOURCE_PRIORITY_EXACT[src]
    else:
        base = 0
        for prefix, score in _SOURCE_PRIORITY_PREFIXES:
            if src.startswith(prefix):
                base = score
                break
        if base == 0:
            mt = _candidate_match_type(cand)
            if mt == "label":
                base = 110
            elif mt == "regex":
                base = 90
            else:
                base = 20
    if prefer_k_prefix and _is_k_customer_code(cand.value):
        base += 3
    return base


def _candidate_rank_components(
    cand: IdentFieldCandidate,
    *,
    prefer_k_prefix: bool = False,
) -> tuple[int, int, int, int, int]:
    meta = cand.meta if isinstance(cand.meta, dict) else {}
    field_id = str(meta.get("field_id") or "").strip().lower()
    return (
        _label_strength(cand),
        _field_type_match_score(cand, field_id=field_id),
        _context_proximity_score(cand),
        int(cand.confidence or 0),
        _source_priority(cand, prefer_k_prefix=prefer_k_prefix),
    )


def candidate_rank_key(
    cand: IdentFieldCandidate,
    *,
    prefer_k_prefix: bool = False,
) -> tuple[int, int, int, int, int, str, str]:
    lbl, ft, prox, conf, src_prio = _candidate_rank_components(
        cand, prefer_k_prefix=prefer_k_prefix
    )
    return (
        lbl,
        ft,
        prox,
        conf,
        src_prio,
        str(cand.source or "").strip().lower(),
        _stable_tiebreak_value(cand),
    )


def _candidate_rank_key(
    cand: IdentFieldCandidate,
    *,
    prefer_k_prefix: bool = False,
) -> tuple[int, int, int, int, int, str, str]:
    return candidate_rank_key(cand, prefer_k_prefix=prefer_k_prefix)


RankingContext = Literal["parse", "resolver"]


def _amount_parse_rank_key(c: Any) -> tuple[int, int, int]:
    """Parse-time amount rank (canonical; legacy ``pdf_parser._amount_pick_key``)."""
    from parser.pdf_parser import AmountCandidate, _amount_payable_score, _TENTATIVE_INCL_SOURCE_RANK

    if not isinstance(c, AmountCandidate):
        raise TypeError(f"expected AmountCandidate, got {type(c)!r}")
    return (
        _amount_payable_score(c),
        int(c.confidence or 0),
        _TENTATIVE_INCL_SOURCE_RANK.get(str(c.source or ""), 0),
    )


def _coerce_ident_candidate(
    cand: IdentFieldCandidate | Any,
    *,
    field_id: str | None,
) -> IdentFieldCandidate:
    from parser.field_model import FieldCandidate

    if isinstance(cand, IdentFieldCandidate):
        ident = cand
    elif isinstance(cand, FieldCandidate):
        ident = IdentFieldCandidate(
            value=str(cand.value) if cand.value is not None else "",
            source=str(cand.source or ""),
            confidence=int(cand.confidence or 0),
            context=str(cand.context or ""),
            label=str(cand.label or ""),
            meta=dict(cand.meta or {}),
        )
    else:
        raise TypeError(f"unsupported candidate type: {type(cand)!r}")
    meta = dict(ident.meta or {})
    fid = str(field_id or meta.get("field_id") or "").strip().lower()
    if fid and "field_id" not in meta:
        meta["field_id"] = fid
        ident.meta = meta
    return ident


def rank_key(
    field_id: str,
    cand: IdentFieldCandidate | Any,
    *,
    prefer_k_prefix: bool = False,
    context: RankingContext = "resolver",
) -> tuple[Any, ...]:
    """Canonical per-candidate rank key (Phase B1).

    ``parse``: ident fields use ``candidate_rank_key`` (incl. ``prefer_k_prefix``);
    amount uses ``_amount_parse_rank_key`` (payable-score-first, same as legacy pick key).

    ``resolver``: ident fields use ``candidate_rank_key`` (no K-prefix bonus);
    amount prepends ``payable_score``; ``invoice_date`` adds date tiebreak.
    """
    from decimal import Decimal

    from parser.pdf_parser import AmountCandidate

    # Resolver must not inject resolve-time field_id into meta (legacy ident ranking).
    coerce_fid = field_id if context == "parse" else None
    ident = _coerce_ident_candidate(cand, field_id=coerce_fid)
    fid = str(field_id or (ident.meta or {}).get("field_id") or "").strip().lower()

    if fid == "amount" and context == "parse":
        meta = ident.meta if isinstance(ident.meta, dict) else {}
        ctype = str(meta.get("type") or "unknown")
        try:
            val = Decimal(str(ident.value)) if str(ident.value or "").strip() else Decimal("0")
        except Exception:
            val = Decimal("0")
        ac = AmountCandidate(
            value=val,
            source=str(ident.source or ""),
            confidence=int(ident.confidence or 0),
            context=str(ident.context or ""),
            type=ctype,  # type: ignore[arg-type]
        )
        return _amount_parse_rank_key(ac)

    use_k_prefix = prefer_k_prefix if context == "parse" else False
    base = candidate_rank_key(ident, prefer_k_prefix=use_k_prefix)

    if fid == "amount" and context == "resolver":
        meta = ident.meta if isinstance(ident.meta, dict) else {}
        try:
            payable_score = int(meta.get("payable_score") or 0)
        except (TypeError, ValueError):
            payable_score = 0
        return (
            payable_score,
            int(base[3]),
            int(base[4]),
            str(base[5]),
            str(base[6]),
        )

    if fid == "invoice_date" and context == "resolver":
        raw = str(ident.value or "").strip()
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            date_rank = (y * 10000) + (mo * 100) + d
        else:
            date_rank = 0
        return (
            int(base[0]),
            int(base[1]),
            int(base[2]),
            int(base[3]),
            int(base[4]),
            date_rank,
            raw.casefold(),
        )

    return base


def rank_candidates(
    field_id: str,
    candidates: list[IdentFieldCandidate | Any],
    *,
    prefer_k_prefix: bool = False,
    context: RankingContext = "resolver",
) -> list[IdentFieldCandidate | Any]:
    """Canonical deterministic ordering (Phase B1). Higher rank first when ``reverse`` sort."""
    if not candidates:
        return []
    return sorted(
        list(candidates),
        key=lambda c: rank_key(
            field_id,
            c,
            prefer_k_prefix=prefer_k_prefix,
            context=context,
        ),
        reverse=True,
    )


def _raw_confidence(cand: IdentFieldCandidate) -> int:
    meta = cand.meta if isinstance(cand.meta, dict) else {}
    raw = meta.get("raw_confidence")
    if raw is None:
        return int(cand.confidence or 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(cand.confidence or 0)


def _stable_tiebreak_value(cand: IdentFieldCandidate) -> str:
    value = str(cand.value or "").strip()
    meta = cand.meta if isinstance(cand.meta, dict) else {}
    field_id = str(meta.get("field_id") or "").strip().lower()
    if field_id == "invoice_number":
        return value.casefold()
    if field_id == "invoice_date":
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            date_num = (y * 10000) + (mo * 100) + d
            # Newer dates win on full ties (deterministic recency rule).
            return f"{date_num:08d}:{value}"
    return value.casefold()


def _candidate_conflict_type(
    cand: IdentFieldCandidate,
    *,
    field_id: str,
) -> str | None:
    src = str(cand.source or "").strip().lower()
    label = str(cand.label or "").strip().lower()
    context = str(cand.context or "").strip().lower()
    value = str(cand.value or "").strip().lower()
    hay = " ".join((src, label, context, value))

    if field_id == "invoice_number":
        if _STRICT_ORDER_HINT_RE.search(hay) or (
            _ORDER_HINT_RE.search(hay)
            and re.search(r"(?i)\b(?:ordernummer|bestel|purchase\s*order|po\s*number)\b", hay)
        ):
            return "order_number"
        if src in {"ref_slash", "year_slash_ref"} and _STRICT_ORDER_HINT_RE.search(hay):
            return "order_number"
        if _REFERENTIE_ONLY_LINE_RE.search(hay):
            return "reference_number"
    if field_id == "customer_number":
        val = str(cand.value or "").strip()
        if src == "label_block_same_line" and _is_preferred_customer_label(str(cand.label or "")):
            return None
        if _PAKBON_HINT_RE.search(hay):
            return "order_number"
        if src.startswith("header_table"):
            if re.fullmatch(r"(?i)(?:VF-?\d{4,}|F\d{5,}|[A-Z]{1,3}-\d{4,})", val):
                return "invoice_number"
            return None
        if src.startswith(("factuur", "date_invoice")):
            return "invoice_number"
        if re.fullmatch(r"(?i)(?:VF-?\d{4,}|F\d{5,}|[A-Z]{1,3}-\d{4,})", val):
            return "invoice_number"
        order_hay = f"{label} {context}"
        if _STRICT_ORDER_HINT_RE.search(order_hay) or (
            re.search(r"(?i)\b(?:ordernummer|bestel|purchase\s*order|po\s*number)\b", order_hay)
            and src in {"ref_slash", "year_slash_ref"}
        ):
            return "order_number"
    return None


def _apply_cross_field_penalties(
    cands: list[IdentFieldCandidate],
    *,
    field_id: str | None,
) -> list[IdentFieldCandidate]:
    if not field_id:
        return cands
    conflicts = FIELD_CONFLICT_RULES.get(field_id)
    if not conflicts:
        return cands
    has_inv_peer = field_id == "invoice_number" and _has_invoice_labeled_peer(cands)
    out: list[IdentFieldCandidate] = []
    for cand in cands:
        meta = dict(cand.meta or {})
        raw_conf = int(cand.confidence or 0)
        meta.setdefault("raw_confidence", raw_conf)
        conflict_type = _candidate_conflict_type(cand, field_id=field_id)
        if conflict_type and conflict_type in conflicts:
            penalty = _CROSS_FIELD_CONFIDENCE_PENALTY
            if has_inv_peer and conflict_type == "order_number":
                penalty = _ORDER_VS_INVOICE_PENALTY
            cand.confidence = max(0, raw_conf - penalty)
            meta["cross_field_penalty_applied"] = True
            meta["cross_field_conflict_type"] = conflict_type
        cand.meta = meta
        out.append(cand)
    return out


def _winner_reason(
    winner: IdentFieldCandidate,
    runner_up: IdentFieldCandidate | None,
    *,
    prefer_k_prefix: bool = False,
) -> str:
    if runner_up is None:
        return "deterministic_tiebreak"
    w = _candidate_rank_components(winner, prefer_k_prefix=prefer_k_prefix)
    r = _candidate_rank_components(runner_up, prefer_k_prefix=prefer_k_prefix)
    if w[0] != r[0]:
        return "stronger_label_match"
    if w[1] != r[1]:
        return "field_keyword_match"
    if w[2] != r[2]:
        return "better_context_proximity"
    if w[3] != r[3]:
        return "higher_confidence"
    if w[4] != r[4]:
        return "lower_source_priority"
    return "deterministic_tiebreak"


def _loser_reason(
    winner: IdentFieldCandidate,
    loser: IdentFieldCandidate,
    *,
    prefer_k_prefix: bool = False,
) -> str:
    w = _candidate_rank_components(winner, prefer_k_prefix=prefer_k_prefix)
    l = _candidate_rank_components(loser, prefer_k_prefix=prefer_k_prefix)
    loser_raw = _raw_confidence(loser)
    winner_raw = _raw_confidence(winner)
    loser_meta = loser.meta if isinstance(loser.meta, dict) else {}
    if (
        bool(loser_meta.get("cross_field_penalty_applied"))
        and loser_raw >= winner_raw
        and l[3] < loser_raw
    ):
        return "cross_field_penalty"
    if w[0] != l[0]:
        return "weaker_label"
    if w[1] != l[1]:
        return "weaker_field_type"
    if w[2] != l[2]:
        return "worse_context_proximity"
    if w[3] != l[3]:
        return "lower_confidence"
    if w[4] != l[4]:
        return "lower_source_priority"
    return "deterministic_tiebreak"


def _selection_trace(
    ordered: list[IdentFieldCandidate],
    winner: IdentFieldCandidate,
    *,
    final_reason: str,
    status: str,
    prefer_k_prefix: bool = False,
) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    rank_map = {
        (str(c.source or ""), str(c.value or "")): idx + 1
        for idx, c in enumerate(ordered)
    }
    runner_up = next(
        (c for c in ordered if c is not winner and str(c.value or "").strip()),
        None,
    )
    winner_reason = _winner_reason(
        winner,
        runner_up,
        prefer_k_prefix=prefer_k_prefix,
    )
    for cand in ordered:
        is_win = (
            str(cand.value or "").strip().casefold()
            == str(winner.value or "").strip().casefold()
            and str(cand.source or "") == str(winner.source or "")
        )
        entry: dict[str, Any] = {
            "value": cand.value,
            "source": cand.source,
            "confidence": int(cand.confidence or 0),
            "label_strength": _label_strength(cand),
            "source_priority": _source_priority(cand, prefer_k_prefix=prefer_k_prefix),
            "rank_score": list(
                _candidate_rank_key(cand, prefer_k_prefix=prefer_k_prefix)
            ),
            "considered": True,
            "win": is_win,
            "rank": rank_map.get((str(cand.source or ""), str(cand.value or ""))),
        }
        if is_win:
            entry["winner_reason"] = winner_reason
        else:
            lost_reason = _loser_reason(
                winner,
                cand,
                prefer_k_prefix=prefer_k_prefix,
            )
            entry["excluded_reason"] = lost_reason
            entry["rejection_reason"] = lost_reason
        trace.append(entry)
    trace.append(
        {
            "kind": "final",
            "final_decision_reason": final_reason,
            "winner": {
                "value": winner.value,
                "source": winner.source,
                "confidence": int(winner.confidence or 0),
                "winner_reason": winner_reason,
            },
            "status": status,
        }
    )
    return trace


def _missing_candidate() -> IdentFieldCandidate:
    return IdentFieldCandidate(
        value="",
        source="fallback_missing",
        confidence=5,
        context="",
        label="Not found in structured extraction",
        meta=_candidate_explain_meta(
            extraction_method="fallback_missing",
            label_reason="No structured candidates produced",
            score_breakdown={"base": 5, "note": "explicit missing candidate"},
            label_source="fallback_missing",
            match_type="fallback",
        ),
    )


def _normalize_ident_value(raw: str, *, join_spaced_digits: bool = False) -> str | None:
    s = re.sub(r"\s+", " ", (raw or "").strip())
    if not s or _is_noise_value(s) or _looks_like_date_token(s):
        return None
    if join_spaced_digits and re.fullmatch(r"[\d\s]+", s):
        compact = re.sub(r"\s+", "", s)
        if len(compact) >= 4:
            return compact
    return s


def _normalize_customer_token(raw: str) -> str:
    token = str(raw or "").strip()
    compact = re.sub(r"\s+", "", token)
    if re.fullmatch(r"(?i)K[O0]\d{3,12}", compact):
        return "K0" + compact[2:]
    m = re.fullmatch(r"(?i)(?:nr|no)\W*(\d{3,})", token)
    if m:
        return m.group(1)
    return token


def _tokens_after_label(line: str, end: int, *, join_spaced_digits: bool) -> list[str]:
    after = re.sub(r"^[\s:\.\[\]]+", "", (line or "")[end:])
    if not after.strip():
        return []
    if join_spaced_digits:
        m_multi = re.search(
            r"(?<![A-Za-z0-9./])(\d{2,4}/\d{2,4}/\d{5,})(?!\d)",
            after or "",
        )
        if m_multi:
            v = _normalize_ident_value(m_multi.group(1).strip(), join_spaced_digits=False)
            if v:
                return [v]
        m_compact = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9\-/]{3,})\b", after.strip())
        if m_compact and re.search(r"\d", m_compact.group(1)):
            compact_val = m_compact.group(1).strip()
            if "/" not in compact_val and re.fullmatch(r"\d{6,}", compact_val):
                pass
            else:
                v = _normalize_ident_value(compact_val, join_spaced_digits=False)
                if v:
                    return [v]
        # PM Coded e.a.: factuurnummer kan zijn "2026 / 15" (spaties rond slash).
        m_slash = re.match(r"^\s*(\d[\d\s]{1,8})\s*/\s*(\d[\d\s]{1,8})\b", after)
        if m_slash:
            left = re.sub(r"\s+", "", m_slash.group(1))
            right = re.sub(r"\s+", "", m_slash.group(2))
            joined = f"{left}/{right}"
            v = _normalize_ident_value(joined, join_spaced_digits=False)
            if v:
                return [v]
        m = re.match(r"([\d][\d\s]{2,})", after)
        if m:
            v = _normalize_ident_value(m.group(1), join_spaced_digits=True)
            if v:
                return [v]
    out: list[str] = []
    rem = after
    while rem:
        vm = _FIELD_VALUE_RE.match(rem)
        if not vm:
            break
        val = vm.group(0).strip()
        v = _normalize_ident_value(val)
        if v:
            out.append(v)
        rem = rem[vm.end() :]
        rem = re.sub(r"^[\s:\.\[\]]+", "", rem)
    return out


def _line_context_at(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end < 0:
        end = len(text)
    return re.sub(r"\s+", " ", text[start:end]).strip()[:160]


def _line_index_at(text: str, pos: int) -> int:
    return (text or "")[: max(0, pos)].count("\n")


def _context_hint_at(text: str, pos: int) -> str:
    lines = (text or "").splitlines()
    if not lines:
        return "body"
    idx = min(_line_index_at(text, pos), len(lines) - 1)
    line = lines[idx]
    n = len(lines)
    raw_cut = max(12, int(n * 0.2))
    header_cut = min(raw_cut, max(1, (n + 1) // 2))
    footer_cut = min(raw_cut, max(1, n - header_cut))
    if idx < header_cut:
        return "header"
    if footer_cut and idx >= n - footer_cut:
        return "footer"
    if "|" in line or "\t" in line or len(re.findall(r"\S+", line)) >= 6:
        return "table"
    return "body"


def _header_segment(text: str) -> str:
    lines = (text or "").splitlines()
    if not lines:
        return ""
    n = len(lines)
    cut = min(max(12, int(n * 0.2)), max(1, (n + 1) // 2))
    return "\n".join(lines[:cut])


def _footer_segment(text: str) -> str:
    lines = (text or "").splitlines()
    if not lines:
        return ""
    n = len(lines)
    raw_cut = max(12, int(n * 0.2))
    header_cut = min(raw_cut, max(1, (n + 1) // 2))
    cut = min(raw_cut, max(1, n - header_cut))
    return "\n".join(lines[max(0, n - cut) :])


def _line_at_pos(text: str, pos: int) -> str:
    return _line_context_at(text, pos)


_INVOICE_CUSTOMER_LINE_RE = re.compile(
    r"(?i)\b(?:factuur(?:nummer|nr)?|klant(?:nummer|nr)?|customer\s*(?:no|number)?)\b"
)
_AMOUNT_ON_LINE_RE = re.compile(
    r"(?i)(?:\b(?:eur|€)\b.*\d+[.,]\d{2}|\d+[.,]\d{2}.*\b(?:eur|€)\b|"
    r"\b(?:totaal|te\s+betalen|bedrag)\b.*\d+[.,]\d{2})"
)


def _line_has_plausible_iban(line: str) -> bool:
    from logic.validation import clean_iban, is_plausible_iban

    for m in re.finditer(r"\b[A-Z]{2}[\dA-Z\s]{13,40}\b", line or "", flags=re.IGNORECASE):
        if is_plausible_iban(clean_iban(m.group(0))):
            return True
    return False


_REJECT_PRODUCT_LINE_IBAN_RE = re.compile(
    r"(?i)(?:"
    r"\d{3}/\d{2}\s+YR\d{2}\s+TL|"
    r"\b(?:omschrijving|stuk\s*prijs|aantal)\b.*\b(?:PRIMACY|MO\s*XL)\b|"
    r"\b(?:PRIMACY|MO\s*XL)\b.*\b(?:omschrijving|stuk\s*prijs|aantal)\b"
    r")"
)


def _iban_candidate_ok(value: str, *, context: str = "") -> bool:
    from logic.validation import clean_iban, is_plausible_iban

    iban = clean_iban(value)
    if not iban or not is_plausible_iban(iban):
        return False
    if iban[:2].upper() not in _IBAN_ISO_LENGTH_BY_CC:
        return False
    ctx = str(context or "")
    if _REJECT_PRODUCT_LINE_IBAN_RE.search(ctx):
        return False
    return True


def _normalize_eu_vat_fallback(country: str, body: str) -> str | None:
    cc = str(country or "").upper()
    if len(cc) != 2:
        return None
    compact = re.sub(r"[^0-9A-Z]", "", str(body or "").upper())
    if cc == "NL":
        nl = _compact_nl_vat_token(f"NL{compact}" if not compact.startswith("NL") else compact)
        if nl:
            return nl
        return _compact_nl_vat_token(compact)
    full = cc + compact
    if not re.fullmatch(r"[A-Z]{2}[0-9A-Z]{8,14}", full):
        return None
    if cc == "NL":
        return _compact_nl_vat_token(full)
    return _normalize_vat_compact(full) or None


def _should_reject_ident_candidate(
    value: str,
    *,
    field_id: str,
    line: str = "",
) -> bool:
    """True = verwerp kandidaat (cross-field contamination)."""
    from logic.validation import clean_iban, is_plausible_iban

    val = str(value or "").strip()
    if not val:
        return True
    ln = str(line or "")
    if _line_has_plausible_iban(ln):
        compact_line = re.sub(r"\s+", "", ln.upper())
        if field_id == "vat_number" and val.upper() in compact_line:
            return True
        if val.upper() in compact_line and field_id == "kvk_number":
            if val.isdigit() and len(val) == 8:
                if re.search(rf"\b[A-Z]{{2}}\d+{re.escape(val)}\b", compact_line):
                    return True
    if field_id == "vat_number":
        v = _normalize_vat_compact(val)
        if not v:
            return True
        if is_plausible_iban(v):
            return True
        if _compact_nl_vat_token(v):
            return False
        if re.fullmatch(r"[A-Z]{2}[0-9A-Z]{8,14}", v):
            return False
        return True
    if field_id == "kvk_number":
        digits = _normalize_kvk_digits(val)
        if not digits:
            return True
        if is_plausible_iban(digits) or is_plausible_iban(f"NL{digits}"):
            return True
        if _INVOICE_CUSTOMER_LINE_RE.search(ln) and not _KVK_BUSINESS_BLOCK_RE.search(ln):
            return True
        return False
    if field_id == "email_domain":
        if "@" in val:
            return True
        if "." not in val:
            return True
        return False
    return False


def _filter_ident_contamination(
    cands: list[IdentFieldCandidate],
    *,
    field_id: str,
    body: str,
) -> list[IdentFieldCandidate]:
    out: list[IdentFieldCandidate] = []
    for c in cands:
        pos = (body or "").find(c.value)
        if pos < 0 and c.context:
            pos = (body or "").find(c.context[:40])
        line = _line_at_pos(body, pos) if pos >= 0 else (c.context or "")
        if _should_reject_ident_candidate(c.value, field_id=field_id, line=line):
            continue
        if field_id == "email_domain" and _AMOUNT_ON_LINE_RE.search(line):
            if not _EMAIL_RE.search(line) and not _EMAIL_CONTACT_LABEL_RE.search(line):
                continue
        out.append(c)
    return out


def _kvk_in_business_context(text: str, pos: int) -> bool:
    lines = (text or "").splitlines()
    if not lines:
        return False
    idx = _line_index_at(text, pos)
    window = []
    for j in (idx - 1, idx, idx + 1):
        if 0 <= j < len(lines):
            window.append(lines[j])
    block = "\n".join(window)
    if not _KVK_LABEL_RE.search(block):
        return False
    if _KVK_BUSINESS_BLOCK_RE.search(block):
        return True
    hint = _context_hint_at(text, pos)
    if hint in ("header", "footer") and _KVK_LABEL_RE.search(block):
        return True
    return False


def _vat_candidate_allowed(
    vat: str,
    *,
    debtor_norm: str,
    blacklist: frozenset[str],
) -> bool:
    if not vat or not _supplier_vat_shape_ok(vat):
        return False
    if debtor_norm and vat == debtor_norm:
        return False
    if _value_matches_internal_vat(vat, blacklist):
        return False
    return True


def _supplier_vat_shape_ok(vat: str) -> bool:
    compact = re.sub(r"[^0-9A-Z]", "", str(vat or "").upper())
    if not compact:
        return False
    if re.search(r"(?i)(ADRES|ORDER|TEL|FAX|EMAIL|PCE)", str(vat or "")):
        return False
    if re.fullmatch(r"NL\d{9}B\d{2}", compact):
        return True
    if re.fullmatch(r"DE\d{9}", compact):
        return True
    if re.fullmatch(r"[A-Z]{2}\d{8,12}", compact):
        return True
    return False


def _filter_footer_vat_when_header_btw_nr(
    cands: list[IdentFieldCandidate],
    body: str,
) -> list[IdentFieldCandidate]:
    """Drop footer ``BTW:`` duplicates when ``BTW nr.`` exists higher on the page."""
    has_btw_nr = False
    header = _header_segment(body)
    for c in cands:
        hay = f"{c.label or ''} {(c.meta or {}).get('label_reason', '')}"
        if re.search(r"(?i)btw\s*nr", hay) and str(c.value or "") in header:
            has_btw_nr = True
            break
    if not has_btw_nr:
        return cands
    footer = _footer_segment(body)
    if not footer.strip():
        return cands
    out: list[IdentFieldCandidate] = []
    for c in cands:
        val = str(c.value or "")
        if val and val in footer:
            hay = f"{c.label or ''} {(c.meta or {}).get('label_reason', '')}"
            if re.search(r"(?i)btw\s*nr", hay):
                out.append(c)
                continue
            if re.search(r"(?i)btw\s*nr", hay) is None and re.search(
                r"(?i)after label:\s*btw\s*$", hay
            ):
                continue
            if str(c.source or "") == "regex_fallback":
                continue
        out.append(c)
    return out


def _line_in_debtor_zone(text: str, pos: int) -> bool:
    lines = (text or "").splitlines()
    if not lines:
        return False
    idx = _line_index_at(text, pos)
    start = max(0, idx - 6)
    block = "\n".join(lines[start : idx + 1])
    return bool(_DEBTOR_ZONE_VAT_RE.search(block) or _VAT_DEBTOR_HINT_RE.search(block))


def _filter_footer_regex_vat_when_header_labeled(
    cands: list[IdentFieldCandidate],
    body: str,
) -> list[IdentFieldCandidate]:
    header = _header_segment(body)
    has_header_labeled = any(
        isinstance(c.meta, dict)
        and c.meta.get("extraction_method") == "label_match"
        and str(c.value or "") in header
        for c in cands
    )
    if not has_header_labeled:
        return cands
    footer = _footer_segment(body)
    if not footer.strip():
        return cands
    out: list[IdentFieldCandidate] = []
    for c in cands:
        val = str(c.value or "")
        meta = c.meta if isinstance(c.meta, dict) else {}
        if (
            val
            and val in footer
            and meta.get("extraction_method") == "regex_fallback"
        ):
            continue
        out.append(c)
    return out


def _line_in_customer_vat_block(lines: list[str], line_idx: int) -> bool:
    line = lines[line_idx] if 0 <= line_idx < len(lines) else ""
    if re.search(r"(?i)\bbtw\s*nr\.?\s*:", line or ""):
        return False
    if not re.search(r"(?i)\b(?:vat|btw)(?:-|\s*)?number\b", line or ""):
        return False
    start = max(0, line_idx - 8)
    block = "\n".join(lines[start : line_idx + 1])
    if not re.search(r"(?i)\b(?:customer|niederlande|afnemer|customeraddress)\b", block):
        return False
    m_nl = _VAT_RE.search(line or "")
    if m_nl:
        return True
    return bool(
        re.search(
            r"(?i)\b(?:customer\s*address|customeraddress|invoice\s*address|uw\s+ref)\b",
            block,
        )
    )


def _invoice_compact_token(value: str) -> str:
    return re.sub(r"[\s.\-_/]+", "", str(value or "")).upper()


def _invoice_in_vat_labeled_context(
    *,
    line: str = "",
    label: str = "",
    context: str = "",
) -> bool:
    hay = " ".join((str(line or ""), str(label or ""), str(context or "")))
    return bool(_INVOICE_VAT_LABEL_CONTEXT_RE.search(hay))


def _invoice_candidate_ok(
    value: str,
    *,
    line: str = "",
    label: str = "",
    context: str = "",
    source: str = "",
    internal_vat_blacklist: frozenset[str] | None = None,
) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    if _value_matches_internal_vat(raw, internal_vat_blacklist or frozenset()):
        return False
    compact = _invoice_compact_token(raw)
    if re.fullmatch(r"20\d{2}", raw):
        return False
    if re.fullmatch(r"NL\d{9}B\d{2}", compact) or re.fullmatch(
        r"(?:NL)?\d{9,12}B\d{2}", compact
    ):
        return False
    if re.fullmatch(r"NL\d{2}[A-Z]{4}\d{10,20}", compact):
        return False
    if re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?", compact):
        return False
    if _INVOICE_BANK_TOKEN_RE.search(compact):
        return False
    if re.fullmatch(r"NL\d{2}", compact) or (
        compact.startswith("NL") and len(compact) <= 6 and not re.search(r"\d{3}", compact)
    ):
        return False
    if _invoice_in_vat_labeled_context(line=line, label=label, context=context):
        return False
    if _line_has_plausible_iban(line or "") or re.search(r"(?i)\bbic\s*:", line or ""):
        compact_digits = re.sub(r"\D", "", raw)
        if raw.isdigit() or (re.fullmatch(r"\d{3,6}", raw) and not re.search(r"[A-Za-z]", raw)):
            return False
    digits = re.sub(r"\D", "", raw)
    if raw.isdigit() and len(digits) < 5:
        short_digit_ok = (
            len(digits) == 4
            and source in ("label", "label_block", "extra")
            and bool(_EXPLICIT_INVOICE_LABEL_RE.search(label))
        )
        if not short_digit_ok:
            return False
    if not re.search(r"\d", raw):
        return False
    return bool(
        len(raw) >= 4
        and (not raw.isdigit() or len(digits) <= 14)
        and not _is_noise_value(raw)
    )


def _filter_invoice_number_candidates(
    cands: list[IdentFieldCandidate],
    body: str,
    *,
    internal_vat_blacklist: frozenset[str] | None = None,
) -> list[IdentFieldCandidate]:
    """Hard type guard: drop VAT/IBAN-shaped tokens and VAT-labeled context hits."""
    text = body or ""
    blacklist = internal_vat_blacklist or frozenset()
    out: list[IdentFieldCandidate] = []
    for cand in cands:
        val = str(cand.value or "").strip()
        if not val:
            continue
        line = (cand.context or "").strip()
        if not line:
            pos = text.find(val)
            if pos >= 0:
                line = _line_at_pos(text, pos)
        effective_label = str(cand.label or "")
        if str(cand.source or "").startswith("header_table_"):
            # Volledige kopregel kan BTW/Klant-kolommen bevatten; geen VAT-context voor waarde.
            effective_label = ""
        if _invoice_candidate_ok(
            val,
            line=line,
            label=effective_label,
            context=str(cand.context or ""),
            source=str(cand.source or ""),
            internal_vat_blacklist=blacklist,
        ):
            out.append(cand)
    return out


def _token_from_date_invoice_line(line: str) -> str | None:
    m = _DATE_INVOICE_LINE_RE.match((line or "").strip())
    if not m:
        return None
    val = m.group(1).strip()
    if re.fullmatch(r"\d{1,2}/\d{1,2}", val):
        return None
    return val if _invoice_candidate_ok(val) else None


def _looks_like_order_context(cand: IdentFieldCandidate) -> bool:
    if _candidate_match_type(cand) == "label":
        return False
    ctx = str(cand.context or "")
    if not ctx:
        return False
    if _INVOICE_HINT_RE.search(ctx):
        return False
    if _ORDER_HINT_RE.search(ctx):
        return True
    return False


def _looks_like_order_token(value: str) -> bool:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return False
    return bool(_ORDER_REF_TOKEN_RE.fullmatch(digits))


def _collect_credit_note_invoice_candidates(text: str) -> list[IdentFieldCandidate]:
    """Creditnota-titel ``Creditnota VCR2600003+`` als factuurnummer-kandidaat."""
    body = text or ""
    if not _CREDIT_INVOICE_HINT_RE.search(body):
        return []
    cands: list[IdentFieldCandidate] = []
    for m in _CREDIT_NOTE_NUMBER_RE.finditer(body):
        val = re.sub(r"\++$", "", str(m.group(1) or "").strip())
        if not val or not _invoice_candidate_ok(val):
            continue
        cands.append(
            IdentFieldCandidate(
                value=val,
                source="credit_note_title",
                confidence=92,
                context=_line_context_at(body, m.start()),
                label="Creditnota",
                meta=_candidate_explain_meta(
                    extraction_method="regex",
                    label_reason="credit note title VCR number",
                    score_breakdown={"base": 92},
                ),
            )
        )
    return cands


def _filter_parent_invoice_refs_on_credit(
    cands: list[IdentFieldCandidate],
    text: str,
) -> list[IdentFieldCandidate]:
    """Op creditnota's: parent factuur-referenties (Fact.nr. VF…) uit de pool."""
    body = text or ""
    if not _CREDIT_INVOICE_HINT_RE.search(body):
        return cands
    has_vcr = any(str(c.value or "").upper().startswith("VCR") for c in cands)
    if not has_vcr:
        return cands
    out: list[IdentFieldCandidate] = []
    for cand in cands:
        hay = f"{cand.label or ''} {cand.context or ''}"
        if _PARENT_INVOICE_REF_CTX_RE.search(hay):
            continue
        val = str(cand.value or "").strip().upper()
        if val.startswith("VF") and _PARENT_INVOICE_REF_CTX_RE.search(hay):
            continue
        out.append(cand)
    return out


def _filter_order_like_invoice_candidates(
    cands: list[IdentFieldCandidate],
) -> list[IdentFieldCandidate]:
    if not _has_invoice_labeled_peer(cands):
        return cands
    out: list[IdentFieldCandidate] = []
    for cand in cands:
        if _candidate_match_type(cand) != "label" and _looks_like_order_context(cand):
            continue
        if (
            _candidate_match_type(cand) != "label"
            and cand.source in {"ref_slash", "year_slash_ref", "date_invoice_line"}
            and _looks_like_order_token(cand.value)
            and not _INVOICE_HINT_RE.search(
                f"{cand.label or ''}\n{cand.context or ''}"
            )
        ):
            continue
        out.append(cand)
    return out


def _collect_datum_nummer_table_candidates(text: str) -> list[IdentFieldCandidate]:
    """Tabel met ``Datum`` + ``Nummer`` in kopregel (Polyglass-layout)."""
    cands: list[IdentFieldCandidate] = []
    lines = (text or "").split("\n")
    for i, hdr in enumerate(lines):
        if not re.search(r"(?i)\bdatum\b", hdr) or not re.search(r"(?i)\bnummer\b", hdr):
            continue
        label = "Datum / Nummer"
        for j in range(1, 4):
            if i + j >= len(lines):
                break
            line = lines[i + j]
            val = _token_from_date_invoice_line(line)
            if not val:
                continue
            ctx = re.sub(r"\s+", " ", (line or "")).strip()[:160]
            cands.append(
                IdentFieldCandidate(
                    value=val,
                    source="datum_nummer_table",
                    confidence=87 - j * 2,
                    context=ctx,
                    label=label,
                    meta=_candidate_explain_meta(
                        extraction_method="proximity",
                        label_reason="table: Datum/Nummer header + first data row token",
                        score_breakdown={"base": 87 - j * 2, "table_bonus": 2},
                    ),
                )
            )
    return cands


def _collect_date_invoice_line_candidates(text: str) -> list[IdentFieldCandidate]:
    """Regels die met factuurdatum beginnen, gevolgd door referentie."""
    cands: list[IdentFieldCandidate] = []
    for line in (text or "").split("\n"):
        val = _token_from_date_invoice_line(line)
        if not val:
            continue
        ctx = re.sub(r"\s+", " ", (line or "")).strip()[:160]
        cands.append(
            IdentFieldCandidate(
                value=val,
                source="date_invoice_line",
                confidence=79,
                context=ctx,
                label="",
                meta=_candidate_explain_meta(
                    extraction_method="proximity",
                    label_reason="line starts with date, next token treated as reference",
                    score_breakdown={"base": 79},
                ),
            )
        )
    return cands


def _line_looks_like_postcode_row(line: str) -> bool:
    return bool(re.search(r"\b\d{4}\s+[A-Z]{2}\b", line or ""))


def _line_looks_like_label_not_value(line: str) -> bool:
    """Volgende regel is zelf een label, geen tabelwaarde."""
    ln = str(line or "")
    if re.search(
        r"(?i)\b(?:factuurnummer|factuurnr|invoice\s*no|debiteur|klant\s*nr|"
        r"ordernummer|betaler|relatie)\b",
        ln,
    ):
        return True
    if re.search(r"(?i)\baanschrijving\b", ln):
        return True
    if re.search(r"(?i)\b(?:totaal|te\s+betalen)\b", ln) and re.search(
        r"(?i)\b(?:eur|€)\b", ln
    ):
        return True
    return False


def _line_looks_like_prose_not_table_header(hdr: str) -> bool:
    """Disclaimer/zin met factuur/klant — geen tabulaire kopregel."""
    ln = (hdr or "").lower()
    if any(
        p in ln
        for p in (
            "dubbel uitgereikt",
            "originele factuur",
            "aanvraag van de klant",
            "aanschrijving",
        )
    ):
        return True
    if len(ln) > 90 and re.search(r"\bfactuur\b", ln):
        words = re.findall(r"[A-Za-z]+", ln)
        col_words = sum(
            1
            for w in words
            if w
            in (
                "factuurnr",
                "factuurnummer",
                "klant",
                "klantnummer",
                "klantnr",
                "debiteur",
                "datum",
                "factuurdatum",
                "betaler",
                "relatie",
                "ordernummer",
                "referentie",
            )
            or "factuurnr" in w
            or w.startswith("klant")
        )
        if col_words < 3 and len(words) > 10:
            return True
    return False


def _table_header_field_count(hdr: str) -> int:
    """Aantal herkende kolomkoppen op één regel (tabular vereist ≥2)."""
    n = 0
    for pat in (
        r"(?i)\b(?:factuurnr|factuurnummer|factuur\s*nr|fact\.?\s*nr|invoice|nummer)\b",
        r"(?i)\b(?:betaler|klant(?:nr|nummer)?|deb|relatie|customer)\b",
        r"(?i)\b(?:datum|facturatiedatum|factuurdatum|verzenddatum|vervaldatum|procedure)\b",
        r"(?i)\b(?:ordernummer|referentie|project)\b",
    ):
        if re.search(pat, hdr or ""):
            n += 1
    if re.search(r"(?i)\bfactuur\b", hdr or "") and re.search(
        r"(?i)\b(?:debiteur|klant|customer|betaler)\b", hdr or ""
    ):
        n = max(n, 2)
    if re.search(r"(?i)klantnr", hdr or "") and re.search(r"(?i)factuurnr", hdr or ""):
        n = max(n, 2)
    return n


def _parse_table_row_tokens(val_line: str) -> list[str]:
    """Tokens uit tabelwaarderegel (datum/BTW weg, slash-refs behouden)."""
    raw_tokens = [t for t in re.split(r"\s+", (val_line or "").strip()) if t]
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
    return vals


_HEADER_COLUMN_PATTERN_SPECS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)factuurdatum|facturatiedatum|verzenddatum|vervaldatum|leverdatum|leverdt"
        ),
        "date",
    ),
    (
        re.compile(r"(?i)factuurnummer|factuurnr\.?|faktuurnummer|faktuurnr\.?"),
        "invoice",
    ),
    (
        re.compile(
            r"(?i)debiteurennummer|debiteurnr\.?|klantnummer|klantnr\.?|"
            r"customer(?:\s*number)?|relatienummer|relatie\s*nr\.?"
        ),
        "customer",
    ),
    (re.compile(r"(?i)\bbetaler\b"), "customer"),
)


def _header_column_spans(hdr: str) -> list[tuple[int, str]]:
    """Kolomvolgorde uit samengestelde koplabels (Factuurdatum ≠ Factuur + datum)."""
    spans: list[tuple[int, str]] = []
    used: list[tuple[int, int]] = []
    for pattern, field_type in _HEADER_COLUMN_PATTERN_SPECS:
        for m in pattern.finditer(hdr or ""):
            start, end = m.start(), m.end()
            if any(not (end <= s or start >= e) for s, e in used):
                continue
            spans.append((start, field_type))
            used.append((start, end))
    spans.sort(key=lambda x: x[0])
    return spans


def _span_column_value_index(spans: list[tuple[int, str]], field: str) -> int | None:
    col_i = next((i for i, (_, ft) in enumerate(spans) if ft == field), None)
    if col_i is None:
        return None
    date_before = sum(1 for i, (_, ft) in enumerate(spans) if i < col_i and ft == "date")
    return col_i - date_before


def _row_looks_like_amount_row(vals: list[str]) -> bool:
    if not vals:
        return False
    amount_like = sum(1 for v in vals if re.fullmatch(r"\d+,\d{2}", v))
    return amount_like >= 2 or (amount_like == 1 and len(vals) <= 3)


def _shift_leading_phone_token(vals: list[str], hdr: str) -> list[str]:
    """Rexel e.d.: vestigingstelefoon vóór factuurnr/betaler-kolommen."""
    if len(vals) >= 3 and vals[0].isdigit() and len(vals[0]) >= 10 and vals[0].startswith("0"):
        if re.search(r"(?i)\bfactuurnr\b", hdr) and re.search(r"(?i)\bbetaler\b", hdr):
            return vals[1:]
    return vals


def _header_alpha_words(hdr: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Za-z]+", hdr or "")]


def _is_date_header_word(w: str) -> bool:
    return w in (
        "datum",
        "factuurdatum",
        "facturatiedatum",
        "verzenddatum",
        "vervaldatum",
        "leverdatum",
        "leverdt",
    ) or w.startswith("factuurdat")


def _date_columns_before(words: list[str], col_i: int) -> int:
    """Aantal datum-kolommen vóór ``col_i`` (waarderegel stript datums)."""
    return sum(1 for i, w in enumerate(words) if i < col_i and _is_date_header_word(w))


def _map_header_index_to_value_index(hdr: str, header_idx: int | None) -> int | None:
    """Map kopregel-woordindex naar index in ``_parse_table_row_tokens``-uitvoer."""
    if header_idx is None:
        return None
    words = _header_alpha_words(hdr)
    if header_idx >= len(words):
        return None
    return header_idx - _date_columns_before(words, header_idx)


def _header_word_indices(hdr: str) -> tuple[int | None, int | None]:
    """Index van factuur- en klant-kolom in kopregel (op woordvolgorde)."""
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", hdr or "")]
    inv_i: int | None = None
    cust_i: int | None = None
    bare_factuur_indices: list[int] = []
    for i, w in enumerate(words):
        if "factuurnr" in w or w in ("factuurnummer", "faktuurnr", "faktuurnummer"):
            inv_i = i
        elif w == "factuur":
            bare_factuur_indices.append(i)
            if inv_i is None and any(x in words for x in ("relatie", "datum", "nummer", "nr")):
                inv_i = i
        elif inv_i is None and (
            (w == "nummer" and "datum" not in words)
            or w == "invoice"
        ):
            inv_i = i
        if cust_i is None and (
            w in (
                "betaler",
                "klant",
                "klantnr",
                "klantnummer",
                "debiteur",
                "debnr",
                "deb",
                "customer",
                "relatie",
                "client",
            )
            or w.startswith("klant")
            or w.startswith("debiteur")
        ):
            cust_i = i
    if (
        len(bare_factuur_indices) > 1
        and cust_i is not None
        and not re.search(r"(?i)factuurnummer|factuurnr", hdr or "")
    ):
        inv_i = bare_factuur_indices[-1]
    elif inv_i is None and bare_factuur_indices:
        inv_i = bare_factuur_indices[0]
    return inv_i, cust_i


def _collect_header_value_table_candidates(
    text: str,
    *,
    field_kind: str,
) -> list[IdentFieldCandidate]:
    """Kopregel + waarderegel (Rexel, Ubbink, Sanha-nummer)."""
    body = text or ""
    lines = body.splitlines()
    cands: list[IdentFieldCandidate] = []

    def _append(
        val: str,
        *,
        source: str,
        confidence: int,
        ctx: str,
        label: str,
        ambiguous: bool = False,
    ) -> None:
        v = str(val or "").strip()
        if field_kind == "invoice_number":
            if not _invoice_candidate_ok(v):
                return
        else:
            v = _normalize_customer_token(v)
            if not _customer_value_ok(v, label_line=label, candidate_line=ctx):
                return
        meta = _candidate_explain_meta(
            extraction_method="proximity",
            label_reason=f"header table: {label}",
            score_breakdown={"base": confidence, "table_bonus": 3},
        )
        if ambiguous:
            meta["ambiguous_column_map"] = True
        cands.append(
            IdentFieldCandidate(
                value=v,
                source=source,
                confidence=confidence,
                context=ctx[:160],
                label=label,
                meta=meta,
            )
        )

    for i, hdr in enumerate(lines):
        h_low = (hdr or "").lower()
        if _line_looks_like_prose_not_table_header(hdr):
            continue
        inv_i, cust_i = _header_word_indices(hdr)
        col_spans = _header_column_spans(hdr)
        has_inv_hdr = (
            not re.search(r"(?i)\bdatum\s+nummer\b", hdr)
            and (
                inv_i is not None
                or re.search(r"(?i)\b(?:factuurnr|factuurnummer|factuur\s*nr)\b", hdr)
                or (
                    re.search(r"(?i)\bfactuur\b", hdr)
                    and re.search(r"(?i)\b(?:relatie|datum)\b", hdr)
                )
                or (
                    re.search(r"(?i)\bfactuur\b", hdr)
                    and re.search(r"(?i)\b(?:debiteur|klant|customer|betaler)\b", hdr)
                )
                or (
                    re.search(r"(?i)\bnummer\b", hdr)
                    and re.search(r"(?i)\b(?:procedure|facturatiedatum)\b", hdr)
                )
            )
        )
        has_cust_hdr = cust_i is not None or re.search(
            r"(?i)\b(?:betaler|relatie|klant|debiteur|customer)\b", hdr
        )
        if field_kind == "invoice_number":
            if not has_inv_hdr:
                continue
        elif not has_cust_hdr:
            continue
        if _table_header_field_count(hdr) < 2:
            continue
        label = re.sub(r"\s+", " ", (hdr or "")).strip()[:80]
        for j in range(1, 5):
            if i + j >= len(lines):
                break
            val_line = lines[i + j] or ""
            if (
                not val_line.strip()
                or _line_looks_like_postcode_row(val_line)
                or _line_looks_like_label_not_value(val_line)
            ):
                continue
            vals = _shift_leading_phone_token(_parse_table_row_tokens(val_line), hdr)
            if len(vals) < 1 or _row_looks_like_amount_row(vals):
                continue
            ctx = re.sub(r"\s+", " ", val_line).strip()[:160]
            inv_i2, cust_i2 = inv_i, cust_i
            debiteur_factuur_hdr = bool(
                re.search(r"(?i)\bfactuur\b", hdr)
                and re.search(r"(?i)\b(?:debiteur|klant|customer|betaler)\b", hdr)
            )
            if inv_i2 is None and has_inv_hdr and not debiteur_factuur_hdr:
                inv_i2 = 0

            def _mapped_value_index(field: str, word_idx: int | None) -> int | None:
                if col_spans:
                    span_idx = _span_column_value_index(col_spans, field)
                    if span_idx is not None:
                        return span_idx
                if word_idx is not None:
                    return _map_header_index_to_value_index(hdr, word_idx)
                return None

            if field_kind == "invoice_number":
                inv_pick: str | None = None
                ambiguous = False
                mapped_i = _mapped_value_index("invoice", inv_i2)
                if mapped_i is not None and mapped_i < len(vals):
                    inv_pick = vals[mapped_i]
                elif mapped_i is not None:
                    ambiguous = True
                elif (
                    re.search(r"(?i)\bfactuurnummer\b", hdr)
                    and re.search(r"(?i)\bfactuurdatum\b", hdr)
                    and vals
                    and not col_spans
                ):
                    inv_pick = vals[-1]
                if inv_pick:
                    conf = (55 if ambiguous else 90 - j)
                    _append(
                        inv_pick,
                        source="header_table_invoice",
                        confidence=conf,
                        ctx=ctx,
                        label=label,
                        ambiguous=ambiguous,
                    )
                    break
            if field_kind == "customer_number" and vals:
                cust_pick: str | None = None
                ambiguous = False
                mapped_i = _mapped_value_index("customer", cust_i2)
                if mapped_i is not None and mapped_i < len(vals):
                    cust_pick = vals[mapped_i]
                elif mapped_i is not None:
                    ambiguous = True
                if cust_pick:
                    conf = (55 if ambiguous else 89 - j)
                    _append(
                        cust_pick,
                        source="header_table_customer",
                        confidence=conf,
                        ctx=ctx,
                        label=label,
                        ambiguous=ambiguous,
                    )
                    break
    return cands


def _collect_inline_factuur_pagina_candidates(text: str) -> list[IdentFieldCandidate]:
    cands: list[IdentFieldCandidate] = []
    body = text or ""
    for m in _FACTUUR_INLINE_PAGINA_RE.finditer(body):
        val = m.group(1).strip()
        if _invoice_candidate_ok(val):
            cands.append(
                IdentFieldCandidate(
                    value=val,
                    source="factuur_inline_pagina",
                    confidence=86,
                    context=_line_context_at(body, m.start()),
                    label="Factuur",
                    meta=_candidate_explain_meta(
                        extraction_method="regex",
                        label_reason="regex: Factuur <id> Pagina",
                        score_breakdown={"base": 86},
                    ),
                )
            )
    return cands


def _collect_reg_invoice_row_candidates(text: str) -> list[IdentFieldCandidate]:
    cands: list[IdentFieldCandidate] = []
    body = text or ""
    for m in _REG_INVOICE_ROW_RE.finditer(body):
        val = m.group(1).strip()
        if _invoice_candidate_ok(val):
            cands.append(
                IdentFieldCandidate(
                    value=val,
                    source="reg_invoice_row",
                    confidence=86,
                    context=_line_context_at(body, m.start()),
                    label="Nummer",
                    meta=_candidate_explain_meta(
                        extraction_method="regex",
                        label_reason="regex: REG… invoice row",
                        score_breakdown={"base": 86},
                    ),
                )
            )
    return cands


def _collect_nummer_prefixed_invoice_candidates(text: str) -> list[IdentFieldCandidate]:
    cands: list[IdentFieldCandidate] = []
    body = text or ""
    for rx, src in (
        (_NUMMER_INV_RE, "nummer_inv"),
        (_NUMMER_REG_INVOICE_RE, "nummer_reg"),
    ):
        for m in rx.finditer(body):
            val = m.group(1).strip()
            if _invoice_candidate_ok(val):
                cands.append(
                    IdentFieldCandidate(
                        value=val,
                        source=src,
                        confidence=88,
                        context=_line_context_at(body, m.start()),
                        label="Nummer",
                        meta=_candidate_explain_meta(
                            extraction_method="regex",
                            label_reason=f"regex match: {src}",
                            score_breakdown={"base": 88},
                        ),
                    )
                )
    return cands


def _collect_invoice_only_line_candidates(text: str) -> list[IdentFieldCandidate]:
    cands: list[IdentFieldCandidate] = []
    body = text or ""
    for m in _INVOICE_ONLY_LINE_RE.finditer(body):
        val = m.group(1).strip()
        if _invoice_candidate_ok(val):
            cands.append(
                IdentFieldCandidate(
                    value=val,
                    source="invoice_only_line",
                    confidence=87,
                    context=_line_context_at(body, m.start()),
                    label="INVOICE",
                    meta=_candidate_explain_meta(
                        extraction_method="regex",
                        label_reason="regex: INVOICE <ref>",
                        score_breakdown={"base": 87},
                    ),
                )
            )
    return cands


def _collect_customer_standalone_line_candidates(text: str) -> list[IdentFieldCandidate]:
    cands: list[IdentFieldCandidate] = []
    body = text or ""
    for m in _CUSTOMER_STANDALONE_LINE_RE.finditer(body):
        val = _normalize_customer_token(m.group(1).strip())
        if _customer_value_ok(val, label_line="Customer", candidate_line=m.group(0)):
            cands.append(
                IdentFieldCandidate(
                    value=val,
                    source="customer_standalone_line",
                    confidence=91,
                    context=re.sub(r"\s+", " ", m.group(0))[:160],
                    label="Customer",
                    meta=_candidate_explain_meta(
                        extraction_method="regex",
                        label_reason="regex: Customer <digits> at line start",
                        score_breakdown={"base": 91},
                    ),
                )
            )
    return cands


def _collect_pipe_suffix_customer_candidates(text: str) -> list[IdentFieldCandidate]:
    cands: list[IdentFieldCandidate] = []
    body = text or ""
    for m in _PIPE_SUFFIX_CUSTOMER_RE.finditer(body):
        val = _normalize_customer_token(m.group(1).strip())
        ctx = _line_context_at(body, m.start())
        if _customer_value_ok(val, label_line="", candidate_line=ctx):
            cands.append(
                IdentFieldCandidate(
                    value=val,
                    source="pipe_customer",
                    confidence=85,
                    context=ctx,
                    label="",
                    meta=_candidate_explain_meta(
                        extraction_method="regex",
                        label_reason="regex: | <customer code>",
                        score_breakdown={"base": 85},
                    ),
                )
            )
    return cands


def _collect_sanha_klant_line_candidates(text: str) -> list[IdentFieldCandidate]:
    cands: list[IdentFieldCandidate] = []
    body = text or ""
    for m in _SANHA_KLANT_VALUE_RE.finditer(body):
        val = _normalize_customer_token(m.group(1).strip())
        if _customer_value_ok(val, label_line="Klant", candidate_line=m.group(0)):
            cands.append(
                IdentFieldCandidate(
                    value=val,
                    source="sanha_klant",
                    confidence=90,
                    context=re.sub(r"\s+", " ", m.group(0))[:160],
                    label="Klant",
                    meta=_candidate_explain_meta(
                        extraction_method="proximity",
                        label_reason="Sanha: value after Verzendingswijze Klant",
                        score_breakdown={"base": 90},
                    ),
                )
            )
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if not re.search(r"(?i)\bverzendingswijze\b", line or ""):
            continue
        if not re.search(r"(?i)\bklant\b", line or ""):
            continue
        for j in range(1, 3):
            if i + j >= len(lines):
                break
            nxt = (lines[i + j] or "").strip()
            parts = nxt.split()
            if len(parts) >= 2 and parts[-1].isdigit():
                val = _normalize_customer_token(parts[-1])
                ctx = re.sub(r"\s+", " ", nxt)[:160]
                if _customer_value_ok(val, label_line="Klant", candidate_line=ctx):
                    cands.append(
                        IdentFieldCandidate(
                            value=val,
                            source="sanha_klant",
                            confidence=88 - j,
                            context=ctx,
                            label="Klant",
                            meta=_candidate_explain_meta(
                                extraction_method="proximity",
                                label_reason="Sanha: digits after Klant header row",
                                score_breakdown={"base": 88 - j},
                            ),
                        )
                    )
                break
    return cands


def _collect_invoice_layout_fallback_candidates(text: str) -> list[IdentFieldCandidate]:
    body = text or ""
    cands: list[IdentFieldCandidate] = []
    cands.extend(_collect_inline_factuur_pagina_candidates(body))
    cands.extend(_collect_nummer_prefixed_invoice_candidates(body))
    cands.extend(_collect_reg_invoice_row_candidates(body))
    cands.extend(_collect_invoice_only_line_candidates(body))
    cands.extend(_collect_header_value_table_candidates(body, field_kind="invoice_number"))
    return cands


def _collect_customer_layout_fallback_candidates(text: str) -> list[IdentFieldCandidate]:
    body = text or ""
    cands: list[IdentFieldCandidate] = []
    cands.extend(_collect_customer_standalone_line_candidates(body))
    cands.extend(_collect_pipe_suffix_customer_candidates(body))
    cands.extend(_collect_sanha_klant_line_candidates(body))
    cands.extend(_collect_header_value_table_candidates(body, field_kind="customer_number"))
    return cands


def _collect_invoice_fallback_candidates(text: str) -> list[IdentFieldCandidate]:
    """Layout-fallbacks zonder expliciet factuurnummer-label."""
    cands: list[IdentFieldCandidate] = []
    body = text or ""

    cands.extend(_collect_datum_nummer_table_candidates(body))
    cands.extend(_collect_invoice_layout_fallback_candidates(body))

    for m in _FACTUUR_COLON_RE.finditer(body):
        val = m.group(1).strip()
        if _invoice_candidate_ok(val):
            cands.append(
                IdentFieldCandidate(
                    value=val,
                    source="factuur_colon",
                    confidence=84,
                    context=_line_context_at(body, m.start()),
                    label="Factuur",
                    meta=_candidate_explain_meta(
                        extraction_method="regex",
                        label_reason="regex match: _FACTUUR_COLON_RE",
                        score_breakdown={"base": 84, "regex_bonus": 2},
                    ),
                )
            )

    for m in _FACTUUR_PLAIN_RE.finditer(body):
        val = m.group(1).strip()
        if _invoice_candidate_ok(val):
            cands.append(
                IdentFieldCandidate(
                    value=val,
                    source="factuur_plain",
                    confidence=83,
                    context=_line_context_at(body, m.start()),
                    label="Factuur",
                    meta=_candidate_explain_meta(
                        extraction_method="regex",
                        label_reason="regex match: _FACTUUR_PLAIN_RE",
                        score_breakdown={"base": 83, "regex_bonus": 2},
                    ),
                )
            )

    for m in _FACTUUR_PREFIXED_RE.finditer(body):
        val = f"{str(m.group(1)).upper()}{m.group(2)}"
        if _invoice_candidate_ok(val):
            cands.append(
                IdentFieldCandidate(
                    value=val,
                    source="factuur_prefixed_digits",
                    confidence=81,
                    context=_line_context_at(body, m.start()),
                    label="Factuur",
                    meta=_candidate_explain_meta(
                        extraction_method="regex",
                        label_reason="regex match: _FACTUUR_PREFIXED_RE",
                        score_breakdown={"base": 81, "regex_bonus": 1},
                    ),
                )
            )

    for m in _YEAR_SLASH_REF_RE.finditer(body):
        val = m.group(1).strip()
        if _invoice_candidate_ok(val):
            cands.append(
                IdentFieldCandidate(
                    value=val,
                    source="year_slash_ref",
                    confidence=78,
                    context=_line_context_at(body, m.start()),
                    label="",
                    meta=_candidate_explain_meta(
                        extraction_method="regex",
                        label_reason="regex match: _YEAR_SLASH_REF_RE",
                        score_breakdown={"base": 78},
                    ),
                )
            )

    for m in _MULTI_SLASH_INVOICE_RE.finditer(body):
        val = m.group(1).strip()
        if _invoice_candidate_ok(val):
            cands.append(
                IdentFieldCandidate(
                    value=val,
                    source="multi_slash_ref",
                    confidence=82,
                    context=_line_context_at(body, m.start()),
                    label="",
                    meta=_candidate_explain_meta(
                        extraction_method="regex",
                        label_reason="regex match: multi-segment slash invoice ref",
                        score_breakdown={"base": 82},
                    ),
                )
            )

    for m in _PAYMENT_INVOICE_REF_RE.finditer(body):
        val = m.group(1).strip()
        if _invoice_candidate_ok(val):
            cands.append(
                IdentFieldCandidate(
                    value=val,
                    source="payment_reference",
                    confidence=84,
                    context=_line_context_at(body, m.start()),
                    label="Bij betaling vermelden",
                    meta=_candidate_explain_meta(
                        extraction_method="regex",
                        label_reason="payment reference line",
                        score_breakdown={"base": 84},
                    ),
                )
            )

    for m in _NUMMER_DATUM_RE.finditer(body):
        val = m.group(1).strip()
        if _invoice_candidate_ok(val):
            cands.append(
                IdentFieldCandidate(
                    value=val,
                    source="nummer_datum_table",
                    confidence=80,
                    context=_line_context_at(body, m.start()),
                    label="Nummer/Datum",
                    meta=_candidate_explain_meta(
                        extraction_method="regex",
                        label_reason="regex match: _NUMMER_DATUM_RE",
                        score_breakdown={"base": 80},
                    ),
                )
            )

    cands.extend(_collect_date_invoice_line_candidates(body))

    return cands


def _customer_candidate_ok(value: str) -> bool:
    return bool(
        re.search(r"\d", value)
        and len(value) >= 3
        and not _is_noise_value(value)
        and not _looks_like_date_token(value)
    )


def _normalize_k_customer_code(raw: str) -> str | None:
    """``K 014135`` / ``k014135`` / ``K0 14135`` → ``K014135``."""
    compact = re.sub(r"\s+", "", (raw or "").strip())
    if not re.fullmatch(r"(?i)k\d{4,12}", compact):
        return None
    return "K" + compact[1:]


def _is_k_customer_code(value: str) -> bool:
    norm = _normalize_k_customer_code(value)
    if not norm:
        return False
    digits = norm[1:]
    return 4 <= len(digits) <= 8


def _compose_k_digits(digits: str) -> str | None:
    d = str(digits or "").strip()
    if not re.fullmatch(r"0?\d{4,10}", d):
        return None
    if _ORDER_REF_TOKEN_RE.fullmatch(d):
        return None
    return "K" + d


def _is_order_or_reference_token(value: str, *, line: str = "") -> bool:
    """Alleen orderrefs op expliciete referentieregels — niet op klantnummer-regels."""
    v = str(value or "").strip()
    ln = str(line or "")
    if not v or not _ORDER_REF_TOKEN_RE.fullmatch(v):
        return False
    if _CUSTOMER_FIELD_LABEL_RE.search(ln):
        return False
    return bool(_REFERENTIE_ONLY_LINE_RE.search(ln))


_WEAK_CUSTOMER_RESOLVED_SOURCES = frozenset(
    {"delivery_block_six_digit", "ref_slash_customer"}
)
_WEAK_CUSTOMER_FALLBACK_SOURCES = frozenset(
    {
        "delivery_block_six_digit",
        "ref_slash_customer",
        "collapsed_k_token",
        "collapsed_k_near_label",
    }
)


def _sanitize_legacy_customer_resolved(
    resolved: str | None,
    resolved_source: str | None,
    body: str,
) -> tuple[str | None, str | None]:
    rv = str(resolved or "").strip()
    if not rv:
        return None, None
    for line in (body or "").splitlines():
        if rv in (line or "") and _is_order_or_reference_token(rv, line=line):
            return None, None
    if _ORDER_REF_TOKEN_RE.fullmatch(rv) and _UW_REFERENTIE_LINE_RE.search(body or ""):
        return None, None
    return rv, resolved_source


def _has_labeled_customer_candidate(cands: list[IdentFieldCandidate]) -> bool:
    for c in cands:
        src = str(c.source or "")
        if src.startswith("label_block") or src.startswith("klantcode"):
            return True
        if src in ("label", "label_next_line", "collapsed_klantcode_fused"):
            return True
    return False


def _reject_weak_resolved_against_candidates(
    resolved: str | None,
    resolved_source: str | None,
    cands: list[IdentFieldCandidate],
) -> tuple[str | None, str | None]:
    """Geen afleveradres-6-cijfer als er een klantcode/klantnummer-labelkandidaat is."""
    rv = str(resolved or "").strip()
    rs = str(resolved_source or "").strip()
    if not rv or rs not in _WEAK_CUSTOMER_RESOLVED_SOURCES:
        return resolved, resolved_source
    if not _has_labeled_customer_candidate(cands):
        return resolved, resolved_source
    if any(c.value.casefold() == rv.casefold() for c in cands):
        return resolved, resolved_source
    return None, None


def _filter_weak_customer_fallbacks(
    cands: list[IdentFieldCandidate],
) -> list[IdentFieldCandidate]:
    if not _has_labeled_customer_candidate(cands):
        return cands
    return [c for c in cands if c.source not in _WEAK_CUSTOMER_FALLBACK_SOURCES]


def _is_whitelisted_unlabeled_customer_candidate(cand: IdentFieldCandidate) -> bool:
    src = str(cand.source or "")
    if src in _UNLABELED_CUSTOMER_WHITELIST_SOURCES:
        return True
    if src.startswith("label_block") or src.startswith("klantcode"):
        return True
    if _is_k_customer_code(str(cand.value or "")):
        return True
    if _has_customer_label_context(label_line=str(cand.label or ""), candidate_line=str(cand.context or "")):
        return True
    return False


def _filter_unlabeled_customer_fallbacks(
    cands: list[IdentFieldCandidate],
) -> list[IdentFieldCandidate]:
    if _has_labeled_customer_candidate(cands):
        return cands
    return [c for c in cands if _is_whitelisted_unlabeled_customer_candidate(c)]


def _customer_resolution_allowed(
    result: IdentFieldResult,
    cands: list[IdentFieldCandidate],
) -> bool:
    val = str(result.value or "").strip()
    if not val:
        return True
    for c in cands:
        if str(c.value or "").strip().casefold() != val.casefold():
            continue
        if _is_whitelisted_unlabeled_customer_candidate(c):
            return True
        if _has_customer_label_context(
            label_line=str(c.label or ""),
            candidate_line=str(c.context or ""),
        ):
            return True
    return False


def _same_line_plausible_customer_values(
    vals: list[str],
    *,
    label_line: str,
) -> list[str]:
    """Kolomkoppen (MAGAZIJN, CODE) zijn geen klantnummer op dezelfde regel als ``Klantcode``."""
    out: list[str] = []
    for val in vals:
        val = _normalize_customer_token(val)
        if not _customer_value_ok(val, label_line=label_line, candidate_line=label_line):
            continue
        if not re.search(r"\d", val):
            continue
        if re.fullmatch(r"(?i)[a-z]+", val):
            continue
        out.append(val)
    out.sort(key=_customer_token_rank, reverse=True)
    return out


def _customer_token_rank(val: str) -> tuple[int, int, int, int, int]:
    v = str(val or "").strip()
    bonus = 0
    if re.fullmatch(r"(?i)[A-Za-z]\d{3,8}", v):
        bonus += 5
    if re.fullmatch(r"20\d{6,}", v):
        bonus -= 4
    if re.fullmatch(r"20(?:2[5-9]|[3-9]\d)\d{4,}", v):
        bonus -= 3
    if re.fullmatch(r"(?i)V[FO]-?\d{4,}", v):
        bonus -= 6
    s = _score_customer_candidate_token(v)
    return (bonus, s[0], s[1], s[2], s[3])


def _drop_kvk_smear_k_candidates(
    cands: list[IdentFieldCandidate],
    body: str,
) -> list[IdentFieldCandidate]:
    """Geen ``K94258392`` uit platte tekst ``kvk94258392``."""
    compact = re.sub(r"[^a-z0-9]", "", (body or "").lower())
    if not compact:
        return cands
    out: list[IdentFieldCandidate] = []
    for c in cands:
        if not _is_k_customer_code(c.value):
            out.append(c)
            continue
        digits = str(c.value or "").strip()[1:].lower()
        if digits and f"kvk{digits}" in compact:
            continue
        out.append(c)
    return out


def _drop_false_k_glue_candidates(
    cands: list[IdentFieldCandidate],
) -> list[IdentFieldCandidate]:
    """Verwijder OCR-lijm zoals ``K01413552`` wanneer ``K014135`` al bestaat."""
    k_vals = sorted(
        {str(c.value or "").strip() for c in cands if _is_k_customer_code(c.value)},
        key=len,
    )
    if len(k_vals) < 2:
        return cands
    drop: set[str] = set()
    for i, short in enumerate(k_vals):
        for long in k_vals[i + 1 :]:
            if long.upper().startswith(short.upper()) and len(long) > len(short):
                drop.add(long.casefold())
    if not drop:
        return cands
    return [c for c in cands if str(c.value or "").strip().casefold() not in drop]


def _text_has_k_customer_code(text: str) -> bool:
    body = text or ""
    if _STANDALONE_K_CUSTOMER_RE.search(body):
        return True
    if _SPACED_K_CUSTOMER_RE.search(body):
        return True
    collapsed = re.sub(r"\s+", "", body)
    return bool(_COLLAPSED_K_IN_TEXT_RE.search(collapsed))


def _has_customer_label_context(*, label_line: str = "", candidate_line: str = "") -> bool:
    block = f"{label_line}\n{candidate_line}"
    if _CUSTOMER_FIELD_LABEL_RE.search(block):
        return True
    return _is_preferred_customer_label(label_line)


def _customer_value_ok(
    value: str,
    *,
    label_line: str = "",
    candidate_line: str = "",
) -> bool:
    v = _normalize_customer_token(str(value or "").strip())
    if not _customer_candidate_ok(v):
        return False
    labeled = _has_customer_label_context(
        label_line=label_line, candidate_line=candidate_line
    )
    if _is_order_or_reference_token(v, line=label_line):
        return False
    if _PAKBON_HINT_RE.search(f"{label_line}\n{candidate_line}"):
        return False
    if _PAYMENT_TERM_DG_RE.fullmatch(v):
        return False
    if str(v).upper().startswith(("VF", "VO")) and re.search(r"\d", str(v)):
        return False
    if re.fullmatch(r"(?i)F\d{5,}", v):
        return False
    if re.fullmatch(r"(?i)NL[0-9A-Z]{2,6}", v):
        return False
    if re.fullmatch(r"\d{9,}", v) and not labeled:
        return False
    if _is_k_customer_code(v):
        digits = str(v or "").strip()[1:]
        if len(digits) > 6 and not labeled:
            return False
    if re.fullmatch(r"20\d{2}", v):
        return False
    if re.fullmatch(r"20\d{6}", v) and not labeled:
        return False
    if _CUSTOMER_DATE_TOKEN_RE.fullmatch(v) and not labeled:
        return False
    if re.fullmatch(r"\d{6,}", v) and not labeled and not _is_k_customer_code(v):
        ctx = f"{label_line}\n{candidate_line}"
        if re.search(rf"\b\d+\s*/\s*{re.escape(v)}\b", ctx):
            return True
        if re.search(r"(?i)\bafleveradres\b", ctx):
            return True
        return False
    if re.fullmatch(r"[A-Z]\d{4,8}", v) and not labeled:
        if re.search(r"(?i)\b(?:bron|referentie|levering|ref\.?)\b", str(candidate_line or "")):
            return False
    compact = re.sub(r"[\s.\-_/]+", "", v).upper()
    if re.fullmatch(r"(?:NL)?\d{9,12}B\d{2}", compact):
        return False
    if re.fullmatch(r"NL\d{2}[A-Z]{4}\d{6,20}", compact):
        return False
    if re.fullmatch(r"(?i)\d{4}[a-z]{2}", v):
        return False
    if re.fullmatch(r"\d{4}", v):
        line = str(candidate_line or "")
        if re.search(rf"\b{re.escape(v)}\s+[A-Za-z]{{2}}\b", line):
            return False
    if re.fullmatch(r"(?i)klant(?:nummer|code|nr)?", v):
        return False
    return True


def _collect_customer_label_block_candidates(text: str) -> list[IdentFieldCandidate]:
    """``Klantnummer`` / ``Klantcode`` + waarde opzelfde regel of in cel eronder/ernaast."""
    body = text or ""
    lines = body.splitlines()
    cands: list[IdentFieldCandidate] = []

    for i, line in enumerate(lines):
        m = _CUSTOMER_FIELD_LABEL_RE.search(line or "")
        if not m:
            continue
        if _REFERENTIE_ONLY_LINE_RE.search(line or "") and not re.search(
            r"(?i)klant(?:nummer|code|nr)", line or ""
        ):
            continue
        label_span = (line or "")[m.start() : m.end()].strip()
        ctx = re.sub(r"\s+", " ", (line or "")).strip()[:160]
        same_line_vals = _same_line_plausible_customer_values(
            _tokens_after_label(line, m.end(), join_spaced_digits=False),
            label_line=line,
        )
        for idx, val in enumerate(same_line_vals):
            norm = _normalize_k_customer_code(val) or val
            cands.append(
                IdentFieldCandidate(
                    value=norm,
                    source="label_block_same_line",
                    confidence=max(70, 94 - idx),
                    context=ctx,
                    label=label_span,
                    meta=_candidate_explain_meta(
                        extraction_method="proximity",
                        label_reason=f"after label: {label_span} (same line)",
                        score_breakdown={"base": 94, "label_bonus": 8},
                    ),
                )
            )
        if same_line_vals:
            continue
        for j in range(1, 4):
            if i + j >= len(lines):
                break
            nxt_line = lines[i + j] or ""
            if not nxt_line.strip():
                continue
            if _REFERENTIE_ONLY_LINE_RE.search(nxt_line) and not _CUSTOMER_FIELD_LABEL_RE.search(
                nxt_line
            ):
                continue
            nxt_ctx = re.sub(r"\s+", " ", nxt_line).strip()[:160]
            got = False
            next_vals = [
                _normalize_customer_token(v)
                for v in _tokens_after_label(nxt_line, 0, join_spaced_digits=False)
                if _customer_value_ok(v, label_line=line, candidate_line=nxt_line)
            ]
            next_vals.sort(key=_customer_token_rank, reverse=True)
            if next_vals:
                val = next_vals[0]
                norm = _normalize_k_customer_code(val) or val
                conf_next = 93 - j
                if re.fullmatch(r"20(?:2[5-9]|[3-9]\d)\d{4,}", val):
                    conf_next = max(40, conf_next - 30)
                if re.search(r"(?i)\buw\s+klant\b", line or "") and re.fullmatch(
                    r"0?\d{4,10}", val
                ):
                    norm = _compose_k_digits(val) or norm
                cands.append(
                    IdentFieldCandidate(
                        value=norm,
                        source="label_block_next_line",
                        confidence=conf_next,
                        context=nxt_ctx,
                        label=label_span,
                        meta=_candidate_explain_meta(
                            extraction_method="proximity",
                            label_reason=f"after label: {label_span} (next line +{j})",
                            score_breakdown={"base": conf_next, "label_bonus": 7, "distance_penalty": j},
                        ),
                    )
                )
                got = True
            if got:
                break
            if re.search(r"(?i)\buw\s+klant\b", line or "") and re.fullmatch(
                r"0?\d{4,10}", nxt_line.strip()
            ):
                composed = _compose_k_digits(nxt_line.strip())
                if composed:
                    cands.append(
                        IdentFieldCandidate(
                            value=composed,
                            source="label_block_uw_klant_digits",
                            confidence=91 - j,
                            context=nxt_ctx,
                            label=label_span,
                            meta=_candidate_explain_meta(
                                extraction_method="proximity",
                                label_reason=f"digits under label: {label_span} (next line +{j})",
                                score_breakdown={"base": 91 - j, "label_bonus": 6, "distance_penalty": j},
                            ),
                        )
                    )
                    break
    return cands


def _collect_collapsed_customer_layout_candidates(text: str) -> list[IdentFieldCandidate]:
    """Alleen direct na ``klantcode``/``klantnummer`` in platte tekst (geen globale K-scan)."""
    body = text or ""
    compact = re.sub(r"[^a-z0-9]", "", body.lower())
    cands: list[IdentFieldCandidate] = []
    if "klantnummer" not in compact and "klantcode" not in compact:
        return cands
    for label in ("klantcode", "klantnummer"):
        start = 0
        while True:
            pos = compact.find(label, start)
            if pos < 0:
                break
            window = compact[pos : pos + len(label) + 22]
            tail = window[len(label) :]
            fused = re.match(r"^(0?\d{5,10})(?!\d)", tail)
            if fused and not _ORDER_REF_TOKEN_RE.fullmatch(fused.group(1)):
                cands.append(
                    IdentFieldCandidate(
                        value=fused.group(1),
                        source="collapsed_klantcode_fused",
                        confidence=92,
                        context="",
                        label=label,
                        meta=_candidate_explain_meta(
                            extraction_method="regex",
                            label_reason=f"collapsed layout near '{label}'",
                            score_breakdown={"base": 92, "layout_bonus": 4},
                        ),
                    )
                )
            for m in re.finditer(r"(?<!kv)k(\d{4,7})(?!\d)", tail):
                val = "K" + m.group(1)
                if _is_k_customer_code(val):
                    cands.append(
                        IdentFieldCandidate(
                            value=val,
                            source="collapsed_k_near_label",
                            confidence=88,
                            context="",
                            label=label,
                            meta=_candidate_explain_meta(
                                extraction_method="regex",
                                label_reason=f"collapsed K-code near '{label}'",
                                score_breakdown={"base": 88, "layout_bonus": 2},
                            ),
                        )
                    )
            start = pos + 1
    return cands


def _collect_split_k_line_candidates(text: str) -> list[IdentFieldCandidate]:
    """Regel met alleen ``K``, cijfers op volgende regel(s)."""
    body = text or ""
    lines = body.splitlines()
    cands: list[IdentFieldCandidate] = []
    for i, line in enumerate(lines):
        if not re.fullmatch(r"(?i)\s*k\s*", (line or "").strip()):
            continue
        for j in range(1, 3):
            if i + j >= len(lines):
                break
            nxt = (lines[i + j] or "").strip()
            composed = _compose_k_digits(nxt)
            if composed:
                cands.append(
                    IdentFieldCandidate(
                        value=composed,
                        source="split_k_line",
                        confidence=88 - j,
                        context=nxt[:160],
                        label="K",
                        meta=_candidate_explain_meta(
                            extraction_method="proximity",
                            label_reason="line 'K' + digits on next line",
                            score_breakdown={"base": 88 - j, "distance_penalty": j},
                        ),
                    )
                )
    for m in _K_NEWLINE_DIGITS_RE.finditer(body):
        composed = _compose_k_digits(m.group(1))
        if composed:
            cands.append(
                IdentFieldCandidate(
                    value=composed,
                    source="split_k_newline",
                    confidence=87,
                    context=_line_context_at(body, m.start()),
                    label="K",
                    meta=_candidate_explain_meta(
                        extraction_method="regex",
                        label_reason="regex match: _K_NEWLINE_DIGITS_RE",
                        score_breakdown={"base": 87},
                    ),
                )
            )
    return cands


def _collect_klantcode_table_candidates(text: str) -> list[IdentFieldCandidate]:
    """Tabelkop met ``KLANTCODE`` (Option Tape e.d.)."""
    body = text or ""
    lines = body.splitlines()
    cands: list[IdentFieldCandidate] = []
    for i, hdr in enumerate(lines):
        if not re.search(r"(?i)\bklantcode\b", hdr or ""):
            continue
        hdr_tokens = re.split(r"\s+", (hdr or "").strip())
        col_idx: int | None = None
        for j, tok in enumerate(hdr_tokens):
            if re.search(r"(?i)klantcode", tok):
                col_idx = j
                break
        label = "KLANTCODE"
        for row in lines[i + 1 : i + 6]:
            if not (row or "").strip():
                continue
            ctx = re.sub(r"\s+", " ", row).strip()[:160]
            inline = _KLANTCODE_INLINE_RE.search(row or "")
            if inline:
                val = inline.group(1).strip()
                norm = _normalize_k_customer_code(val) or val
                cands.append(
                    IdentFieldCandidate(
                        value=norm,
                        source="klantcode_inline",
                        confidence=92,
                        context=ctx,
                        label=label,
                        meta=_candidate_explain_meta(
                            extraction_method="regex",
                            label_reason="regex match: _KLANTCODE_INLINE_RE",
                            score_breakdown={"base": 92, "label_bonus": 4},
                        ),
                    )
                )
                continue
            for m in _STANDALONE_K_CUSTOMER_RE.finditer(row or ""):
                norm = _normalize_k_customer_code(m.group(1))
                if norm:
                    cands.append(
                        IdentFieldCandidate(
                            value=norm,
                            source="klantcode_table_k",
                            confidence=93,
                            context=ctx,
                            label=label,
                            meta=_candidate_explain_meta(
                                extraction_method="proximity",
                                label_reason="table: KLANTCODE column token",
                                score_breakdown={"base": 93, "table_bonus": 3},
                            ),
                        )
                    )
    for m in _KLANTCODE_INLINE_RE.finditer(body):
        val = m.group(1).strip()
        norm = _normalize_k_customer_code(val) or val
        if _customer_candidate_ok(norm):
            cands.append(
                IdentFieldCandidate(
                    value=norm,
                    source="klantcode_inline",
                    confidence=92,
                    context=_line_context_at(body, m.start()),
                    label="Klantcode",
                    meta=_candidate_explain_meta(
                        extraction_method="regex",
                        label_reason="regex match: _KLANTCODE_INLINE_RE (global scan)",
                        score_breakdown={"base": 92},
                    ),
                )
            )
    return cands


def _collect_customer_fallback_candidates(text: str) -> list[IdentFieldCandidate]:
    """Tekstscan voor klantnummer zonder label (onafhankelijk van supplier/profiel)."""
    body = text or ""
    cands: list[IdentFieldCandidate] = []

    def _add(val: str, source: str, confidence: int, pos: int, label: str = "") -> None:
        v = str(val or "").strip()
        if not _customer_candidate_ok(v):
            return
        ctx_line = _line_at_pos(body, pos) if pos >= 0 else ""
        if not _customer_value_ok(v, label_line=label, candidate_line=ctx_line):
            return
        cands.append(
            IdentFieldCandidate(
                value=v,
                source=source,
                confidence=confidence,
                context=_line_context_at(body, pos),
                label=label,
                meta=_candidate_explain_meta(
                    extraction_method="regex",
                    label_reason=f"fallback scan ({source})",
                    score_breakdown={"base": confidence},
                ),
            )
        )

    for rx, source, conf in (
        (_UW_KLANT_K_RE, "uw_klant_k_prefix", 90),
        (_KLANT_LINE_K_RE, "klant_line_k_prefix", 88),
        (_DELIVERY_BLOCK_SIX_DIGIT_RE, "delivery_block_six_digit", 84),
    ):
        for m in rx.finditer(body):
            _add(m.group(1), source, conf, m.start())

    for m in _REF_SLASH_CUSTOMER_RE.finditer(body):
        _add(m.group(2), "ref_slash_customer", 76, m.start(2))

    for m in _STANDALONE_K_CUSTOMER_RE.finditer(body):
        norm = _normalize_k_customer_code(m.group(1))
        if norm:
            _add(norm, "standalone_k_token", 86, m.start(1))

    for m in _SPACED_K_CUSTOMER_RE.finditer(body):
        norm = _normalize_k_customer_code(m.group(1))
        if norm:
            _add(norm, "spaced_k_token", 85, m.start(1))

    for m in _LINE_ONLY_K_CODE_RE.finditer(body):
        norm = _normalize_k_customer_code(m.group(1))
        if norm:
            _add(norm, "line_only_k_code", 84, m.start(1))

    collapsed = re.sub(r"\s+", "", body)
    seen_k: set[str] = set()
    for m in _COLLAPSED_K_IN_TEXT_RE.finditer(collapsed):
        val = _normalize_k_customer_code(m.group(0))
        if not val:
            continue
        key = val.casefold()
        if key in seen_k:
            continue
        seen_k.add(key)
        pos = 0
        for raw_m in _SPACED_K_CUSTOMER_RE.finditer(body):
            if _normalize_k_customer_code(raw_m.group(1)) == val:
                pos = raw_m.start(1)
                break
        else:
            pos_m = _STANDALONE_K_CUSTOMER_RE.search(body)
            if pos_m and _normalize_k_customer_code(pos_m.group(1)) == val:
                pos = pos_m.start(1)
        _add(val, "collapsed_k_token", 83, pos)

    for c in _collect_split_k_line_candidates(body):
        _add(c.value, c.source, c.confidence, 0, c.label)

    for c in _collect_klantcode_table_candidates(body):
        _add(c.value, c.source, c.confidence, 0, c.label)

    return cands


def _merge_resolved_into_candidates(
    candidates: list[IdentFieldCandidate],
    resolved_value: str | None,
    resolved_source: str | None,
    *,
    field_id: str | None = None,
) -> list[IdentFieldCandidate]:
    rv = str(resolved_value or "").strip()
    if not rv:
        return candidates
    if field_id == "invoice_number" and not _invoice_candidate_ok(rv):
        return candidates
    if any(c.value.casefold() == rv.casefold() for c in candidates):
        if field_id == "invoice_date":
            for c in candidates:
                if c.value.casefold() != rv.casefold():
                    continue
                if int(c.confidence or 0) >= 70:
                    continue
                c.confidence = 70
                meta = dict(c.meta or {})
                meta["resolved_hint_boost"] = True
                c.meta = meta
        return candidates
    hint_confidence = 70
    if field_id == "customer_number" and "/" in rv:
        # Preserve explicit compound customer codes from legacy parsing as strong hint.
        hint_confidence = 96
    return candidates + [
        IdentFieldCandidate(
            value=rv,
            source=str(resolved_source or "resolved"),
            confidence=hint_confidence,
            context="",
            label="",
        )
    ]


def _drop_redundant_k_suffix_candidates(
    cands: list[IdentFieldCandidate],
) -> list[IdentFieldCandidate]:
    """Verwijder ``014135`` als ``K014135`` al als kandidaat bestaat (label + K-regel)."""
    k_codes = [
        c.value
        for c in cands
        if re.fullmatch(r"(?i)K\d{4,12}", str(c.value or "").strip())
    ]
    if not k_codes:
        return cands
    drop: set[str] = set()
    for kc in k_codes:
        suffix = str(kc)[1:]
        if suffix:
            drop.add(suffix.casefold())
    return [c for c in cands if str(c.value or "").strip().casefold() not in drop]


def _prefer_slashed_customer_candidates(
    cands: list[IdentFieldCandidate],
) -> list[IdentFieldCandidate]:
    slashed: dict[str, IdentFieldCandidate] = {}
    for c in cands:
        val = str(c.value or "").strip()
        if "/" not in val:
            continue
        canon = re.sub(r"[^A-Za-z0-9]", "", val).upper()
        if canon:
            slashed.setdefault(canon, c)
    if not slashed:
        return cands
    out: list[IdentFieldCandidate] = []
    for c in cands:
        val = str(c.value or "").strip()
        canon = re.sub(r"[^A-Za-z0-9]", "", val).upper()
        if (
            canon in slashed
            and "/" not in val
            and str(c.source or "").startswith("collapsed_")
        ):
            continue
        out.append(c)
    return out


def _drop_short_suffix_customer_candidates(
    cands: list[IdentFieldCandidate],
) -> list[IdentFieldCandidate]:
    numeric_vals = [
        str(c.value or "").strip()
        for c in cands
        if re.fullmatch(r"\d{6,12}", str(c.value or "").strip())
    ]
    if not numeric_vals:
        return cands
    suffixes = {
        long_v[-4:]
        for long_v in numeric_vals
        if len(long_v) >= 8
    }
    if not suffixes:
        return cands
    out: list[IdentFieldCandidate] = []
    for c in cands:
        v = str(c.value or "").strip()
        if (
            re.fullmatch(r"\d{4}", v)
            and v in suffixes
            and str(c.source or "") in {"ref_slash_customer", "label_next_line", "label_block_next_line"}
        ):
            continue
        out.append(c)
    return out


def _dedupe_candidates(cands: list[IdentFieldCandidate]) -> list[IdentFieldCandidate]:
    cands = _ensure_candidate_explainability(cands)
    seen: set[str] = set()
    out: list[IdentFieldCandidate] = []
    for c in sorted(cands, key=_candidate_rank_key, reverse=True):
        key = c.value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def collect_ident_field_candidates(
    text: str,
    label_re: re.Pattern[str],
    *,
    field_kind: str,
    extra_label_res: tuple[re.Pattern[str], ...] = (),
) -> list[IdentFieldCandidate]:
    """Verzamel alle label→waarde treffers (niet alleen de eerste)."""
    lines = (text or "").split("\n")
    cands: list[IdentFieldCandidate] = []
    join_digits = field_kind == "invoice_number"

    label_patterns: list[tuple[re.Pattern[str], str]] = [(label_re, "label")]
    for rx in extra_label_res:
        label_patterns.append((rx, "extra"))

    for i, line in enumerate(lines):
        ctx = re.sub(r"\s+", " ", (line or "")).strip()[:160]
        if field_kind == "invoice_number" and _invoice_in_vat_labeled_context(line=line):
            continue
        for rx, src_kind in label_patterns:
            for m in rx.finditer(line or ""):
                if field_kind == "invoice_number":
                    pre = (line[: m.start()] or "")
                    if pre.rstrip().endswith(("btw-", "vat-")) or _invoice_in_vat_labeled_context(
                        line=pre + line[m.start() : m.end()]
                    ):
                        continue
                label_span = collapse_stutter_chars(line[m.start() : m.end()].strip()) or line[m.start() : m.end()].strip()
                vals = _tokens_after_label(line, m.end(), join_spaced_digits=join_digits)
                for j in (0, 1, 2):
                    if j > 0:
                        if field_kind == "customer_number" and vals:
                            break
                        if i + j >= len(lines):
                            break
                        nxt = lines[i + j]
                        if field_kind == "customer_number" and _PAKBON_HINT_RE.search(nxt or ""):
                            continue
                        if field_kind == "invoice_number" and _CUSTOMER_FIELD_LABEL_RE.search(
                            nxt or ""
                        ):
                            continue
                        ctx_n = re.sub(r"\s+", " ", (nxt or "")).strip()[:160]
                        table_vals: list[str] | None = None
                        if _table_header_field_count(line) >= 2:
                            inv_i, cust_i = _header_word_indices(line)
                            parsed = _parse_table_row_tokens(nxt)
                            if field_kind == "invoice_number" and inv_i is not None:
                                mapped_i = _map_header_index_to_value_index(line, inv_i)
                                if mapped_i is not None and mapped_i < len(parsed):
                                    table_vals = [parsed[mapped_i]]
                            elif field_kind == "customer_number" and cust_i is not None:
                                mapped_i = _map_header_index_to_value_index(line, cust_i)
                                if mapped_i is not None and mapped_i < len(parsed):
                                    table_vals = [parsed[mapped_i]]
                        next_line_vals = (
                            table_vals
                            if table_vals is not None
                            else _tokens_after_label(nxt, 0, join_spaced_digits=join_digits)
                        )
                        for val in next_line_vals:
                            if field_kind == "customer_number":
                                val = _normalize_customer_token(val)
                                if not _customer_value_ok(
                                    val, label_line=line, candidate_line=nxt
                                ):
                                    continue
                            conf = 72 - j * 8
                            if field_kind == "customer_number" and _is_preferred_customer_label(
                                label_span
                            ):
                                conf += 4
                            cands.append(
                                IdentFieldCandidate(
                                    value=val,
                                    source=f"{src_kind}_next_line",
                                    confidence=conf,
                                    context=ctx_n or ctx,
                                    label=label_span,
                                    meta=_candidate_explain_meta(
                                        extraction_method="proximity",
                                        label_reason=f"after label: {label_span} (next line +{j})",
                                        score_breakdown={
                                            "base": conf,
                                            "label_bonus": 4 if src_kind == "label" else 3,
                                            "distance_penalty": j,
                                        },
                                    ),
                                )
                            )
                        continue
                    for val in vals:
                        if field_kind == "customer_number":
                            val = _normalize_customer_token(val)
                            if not _customer_value_ok(
                                val, label_line=line, candidate_line=line
                            ):
                                continue
                        conf = 88 if src_kind == "label" else 85
                        if field_kind == "customer_number" and _is_preferred_customer_label(
                            label_span
                        ):
                            conf += 4
                        cands.append(
                            IdentFieldCandidate(
                                value=val,
                                source=src_kind,
                                confidence=conf,
                                context=ctx,
                                label=label_span,
                                meta=_candidate_explain_meta(
                                    extraction_method="proximity",
                                    label_reason=f"after label: {label_span} (same line)",
                                    score_breakdown={
                                        "base": conf,
                                        "label_bonus": 4 if src_kind == "label" else 3,
                                    },
                                ),
                            )
                        )
    if field_kind == "invoice_number":
        cands = [
            c
            for c in cands
            if _invoice_candidate_ok(
                c.value,
                line=c.context or "",
                label=c.label or "",
                context=c.context or "",
                source=str(c.source or ""),
            )
        ]
    elif field_kind == "customer_number":
        cands = [
            c
            for c in cands
            if _customer_value_ok(c.value, label_line=c.label or "", candidate_line=c.context or "")
            and re.search(r"\d", c.value)
            and len(c.value) >= 3
        ]
    return _dedupe_candidates(cands)


def build_ident_field_result(
    candidates: list[IdentFieldCandidate],
    *,
    resolved_value: str | None = None,
    resolved_source: str | None = None,
    prefer_k_prefix: bool = False,
    field_id: str | None = None,
) -> IdentFieldResult:
    """Selecteer winnaar via een enkele deterministische ranking."""
    candidates = _merge_resolved_into_candidates(
        candidates, resolved_value, resolved_source, field_id=field_id
    )
    if field_id == "iban":
        candidates = [
            c
            for c in candidates
            if _iban_candidate_ok(c.value, context=str(c.context or ""))
        ]
    candidates = _apply_cross_field_penalties(candidates, field_id=field_id)
    candidates = _ensure_candidate_explainability(candidates)
    if field_id:
        for cand in candidates:
            meta = dict(cand.meta or {})
            meta["field_id"] = field_id
            cand.meta = meta
    if not candidates and not (resolved_value and str(resolved_value).strip()):
        # Always expose at least one explicit candidate, but keep the field unresolved.
        miss = _missing_candidate()
        return IdentFieldResult(
            candidates=[miss],
            value=None,
            confidence=miss.confidence,
            source="NOT_FOUND",
            status="failed",
            decision_trace=[
                {
                    "value": miss.value,
                    "source": miss.source,
                    "confidence": miss.confidence,
                    "considered": True,
                    "win": False,
                    "excluded_reason": "no_candidates",
                    "rejection_reason": "no_candidates",
                    "rank": 1,
                },
                {"kind": "final", "final_decision_reason": "not_found", "winner": {}},
            ],
        )
    if resolved_value and str(resolved_value).strip():
        # resolved_value is candidate/hint only; no bypass winner path.
        pass
    if not candidates:
        return IdentFieldResult(status="failed", source="NOT_FOUND")
    ordered = sorted(
        candidates,
        key=lambda c: _candidate_rank_key(c, prefer_k_prefix=prefer_k_prefix),
        reverse=True,
    )
    if len(ordered) == 1:
        c = ordered[0]
        st = "confirmed" if c.confidence >= 80 else "tentative"
        return IdentFieldResult(
            candidates=candidates,
            value=c.value,
            confidence=c.confidence,
            source=c.source,
            status=st,
            decision_trace=_selection_trace(
                ordered,
                c,
                final_reason="single_candidate",
                status=st,
                prefer_k_prefix=prefer_k_prefix,
            ),
        )
    top = ordered[0]
    second = ordered[1]
    st = "confirmed" if top.confidence >= 80 else "tentative"
    final_reason = (
        "highest_confidence"
        if int(top.confidence or 0) != int(second.confidence or 0)
        else "deterministic_tiebreak"
    )
    return IdentFieldResult(
        candidates=candidates,
        value=top.value,
        confidence=top.confidence,
        source=top.source,
        status=st,
        decision_trace=_selection_trace(
            ordered,
            top,
            final_reason=final_reason,
            status=st,
            prefer_k_prefix=prefer_k_prefix,
        ),
    )


def extract_invoice_number_result(
    text: str,
    *,
    resolved: str | None = None,
    resolved_source: str | None = None,
    internal_vat_blacklist: frozenset[str] | None = None,
) -> IdentFieldResult:
    cands = collect_ident_field_candidates(
        text,
        _INVOICE_LABEL_RE,
        field_kind="invoice_number",
        extra_label_res=(_POLIS_LABEL_RE, _RELATIE_LABEL_RE),
    )
    cands.extend(_collect_invoice_fallback_candidates(text))
    cands.extend(_collect_credit_note_invoice_candidates(text))
    cands = _filter_order_like_invoice_candidates(cands)
    cands = _filter_parent_invoice_refs_on_credit(cands, text)
    blacklist = internal_vat_blacklist or frozenset()
    cands = _filter_invoice_number_candidates(cands, text, internal_vat_blacklist=blacklist)
    cands = _filter_internal_vat_blacklist(cands, blacklist, field_id="invoice_number")
    cands = _dedupe_candidates(cands)
    resolved_value = resolved
    resolved_src = resolved_source
    if resolved_value and not _invoice_candidate_ok(
        str(resolved_value).strip(),
        internal_vat_blacklist=blacklist,
    ):
        resolved_value = None
        resolved_src = None
    if resolved_value and _value_matches_internal_vat(str(resolved_value), blacklist):
        resolved_value = None
        resolved_src = None
    result = build_ident_field_result(
        cands,
        resolved_value=resolved_value,
        resolved_source=resolved_src,
        field_id="invoice_number",
    )
    return result


def absent_customer_number_result(*, supplier_profile: bool = False) -> IdentFieldResult:
    """Deterministic absent outcome: no candidates, no ranking, value always None."""
    src = "NOT_PRESENT_SUPPLIER_LEVEL"
    return IdentFieldResult(
        candidates=[],
        value=None,
        confidence=100 if supplier_profile else 0,
        source=src,
        status="not_applicable" if supplier_profile else "failed",
        absence_state=src,
        decision_trace=[
            {
                "kind": "final",
                "final_decision_reason": "supplier_customer_absent",
                "winner": {},
            }
        ],
    )


def extract_customer_number_result(
    text: str,
    *,
    resolved: str | None = None,
    resolved_source: str | None = None,
    supplier_customer_absent: bool = False,
    customer_number_mode: str | None = None,
) -> IdentFieldResult:
    mode = str(customer_number_mode or "").strip().upper()
    if supplier_customer_absent or mode == "NONE":
        return absent_customer_number_result(supplier_profile=True)

    body = text or ""
    resolved, resolved_source = _sanitize_legacy_customer_resolved(
        resolved, resolved_source, body
    )
    block_cands = _collect_customer_label_block_candidates(body)
    label_cands = collect_ident_field_candidates(
        body,
        _CUSTOMER_LABEL_RE,
        field_kind="customer_number",
    )
    collapsed_cands = _collect_collapsed_customer_layout_candidates(body)
    fallback_cands = _collect_customer_fallback_candidates(body)
    layout_cands = _collect_customer_layout_fallback_candidates(body)
    cands = _drop_kvk_smear_k_candidates(
        _drop_false_k_glue_candidates(
            _drop_redundant_k_suffix_candidates(
                _dedupe_candidates(
                    block_cands
                    + label_cands
                    + collapsed_cands
                    + fallback_cands
                    + layout_cands
                )
            )
        ),
        body,
    )
    cands = _prefer_slashed_customer_candidates(cands)
    cands = _drop_short_suffix_customer_candidates(cands)
    cands = _filter_weak_customer_fallbacks(cands)
    cands = _filter_unlabeled_customer_fallbacks(cands)
    resolved, resolved_source = _reject_weak_resolved_against_candidates(
        resolved, resolved_source, cands
    )
    if cands and not any(_is_k_customer_code(c.value) for c in cands):
        collapsed = re.sub(r"\s+", "", body)
    result = build_ident_field_result(
        cands,
        resolved_value=resolved,
        resolved_source=resolved_source,
        prefer_k_prefix=_text_has_k_customer_code(body),
        field_id="customer_number",
    )
    if not _customer_resolution_allowed(result, cands):
        result.value = None
        result.status = "failed"
        result.source = "NOT_FOUND"
    has_value = bool(str(result.value or "").strip())
    if not cands and not has_value:
        result.absence_state = "NOT_FOUND"
        result.source = "NOT_FOUND"
        result.status = "failed"
    return result


def _normalize_email_domain(domain_or_email: str) -> str | None:
    s = re.sub(r"\s+", "", str(domain_or_email or "").strip().lower())
    if not s:
        return None
    if "@" in s:
        s = s.split("@", 1)[1]
    s = re.sub(r"^www\.", "", s)
    s = s.strip(".")
    if "." not in s:
        return None
    return s or None


def _vat_candidate_from_token(
    body: str,
    pos: int,
    vat: str,
    *,
    extraction_method: str,
    label_reason: str,
    confidence: int,
    label: str = "BTW/VAT",
) -> IdentFieldCandidate:
    ctx_hint = _context_hint_at(body, pos)
    return IdentFieldCandidate(
        value=vat,
        source="vat",
        confidence=confidence,
        context=_line_context_at(body, pos),
        label=label,
        meta=_candidate_explain_meta(
            extraction_method=extraction_method,
            label_reason=label_reason,
            context_hint=ctx_hint,
        ),
    )


def _collect_vat_candidates_primary(
    body: str,
    *,
    debtor_norm: str,
    internal_vat_blacklist: frozenset[str] | None = None,
) -> list[IdentFieldCandidate]:
    blacklist = internal_vat_blacklist or frozenset()
    cands: list[IdentFieldCandidate] = []
    line_starts: list[int] = []
    pos = 0
    for line in body.splitlines():
        line_starts.append(pos)
        pos += len(line) + 1

    all_lines = body.splitlines()
    for line_idx, line in enumerate(all_lines):
        if _VAT_DEBTOR_HINT_RE.search(line):
            continue
        if _line_in_customer_vat_block(all_lines, line_idx):
            continue
        line_pos = line_starts[line_idx] if line_idx < len(line_starts) else 0
        if _line_in_debtor_zone(body, line_pos):
            continue

        for lm in _VAT_LABEL_RE.finditer(line):
            after = line[lm.end() :]
            m_nl = _VAT_RE.search(after)
            vat: str | None = None
            tok_start = 0
            if m_nl:
                vat = _normalize_vat_compact(m_nl.group(0))
                tok_start = m_nl.start()
            else:
                m_relaxed = _VAT_RELAXED_VALUE_RE.search(after)
                if m_relaxed:
                    vat = _compact_nl_vat_token(f"NL{m_relaxed.group(1)}")
                    tok_start = m_relaxed.start()
            if vat and _vat_candidate_allowed(vat, debtor_norm=debtor_norm, blacklist=blacklist):
                abs_pos = line_pos + lm.end() + tok_start
                cands.append(
                    _vat_candidate_from_token(
                        body,
                        abs_pos,
                        vat,
                        extraction_method="label_match",
                        label_reason=f"after label: {lm.group(0).strip()}",
                        confidence=90,
                    )
                )
            m_btw = _VAT_BTW_VALUE_RE.search(line)
            if m_btw:
                compact = _compact_nl_vat_token(m_btw.group(1))
                if compact and _vat_candidate_allowed(
                    compact, debtor_norm=debtor_norm, blacklist=blacklist
                ):
                    cands.append(
                        _vat_candidate_from_token(
                            body,
                            line_pos + m_btw.start(),
                            compact,
                            extraction_method="label_match",
                            label_reason="btw/vat colon value on labeled line",
                            confidence=89,
                        )
                    )

        for m in _VAT_RE.finditer(line):
            vat = _normalize_vat_compact(m.group(0))
            if not vat or not _vat_candidate_allowed(
                vat, debtor_norm=debtor_norm, blacklist=blacklist
            ):
                continue
            abs_pos = line_pos + m.start()
            cands.append(
                _vat_candidate_from_token(
                    body,
                    abs_pos,
                    vat,
                    extraction_method="regex_fallback",
                    label_reason="NL VAT pattern on line",
                    confidence=89,
                )
            )

        m_eu_label = re.search(
            r"(?i)\b(?:vat|btw)(?:-|\s*)?number\s*:\s*([A-Z]{2}[\dA-Z]+)",
            line or "",
        )
        if m_eu_label:
            eu = re.sub(r"[^0-9A-Z]", "", m_eu_label.group(1).upper())
            if eu and _vat_candidate_allowed(eu, debtor_norm=debtor_norm, blacklist=blacklist):
                cands.append(
                    _vat_candidate_from_token(
                        body,
                        line_pos + m_eu_label.start(1),
                        eu,
                        extraction_method="label_match",
                        label_reason="VAT-Number label (EU)",
                        confidence=91,
                    )
                )

        if not _VAT_LABEL_RE.search(line):
            m_btw = re.search(
                r"(?i)\b(?:btw|vat)\s*:\s*([\d.\s]+B[\d.\s]+)",
                line,
            )
            if m_btw:
                compact = _compact_nl_vat_token(m_btw.group(1))
                if compact and _vat_candidate_allowed(
                    compact, debtor_norm=debtor_norm, blacklist=blacklist
                ):
                    cands.append(
                        _vat_candidate_from_token(
                            body,
                            line_pos + m_btw.start(),
                            compact,
                            extraction_method="label_match",
                            label_reason="btw/vat colon value",
                            confidence=88,
                        )
                    )

    return cands


def _collect_vat_candidates_fallback(
    body: str,
    *,
    debtor_norm: str,
    internal_vat_blacklist: frozenset[str] | None = None,
) -> list[IdentFieldCandidate]:
    blacklist = internal_vat_blacklist or frozenset()
    cands: list[IdentFieldCandidate] = []
    lines = body.splitlines()
    for m in _VAT_EU_FALLBACK_RE.finditer(body):
        vat = _normalize_eu_vat_fallback(m.group(1), m.group(2))
        if not vat or not _vat_candidate_allowed(vat, debtor_norm=debtor_norm, blacklist=blacklist):
            continue
        line_idx = _line_index_at(body, m.start())
        if _line_in_customer_vat_block(lines, line_idx):
            continue
        if _VAT_DEBTOR_HINT_RE.search(_line_at_pos(body, m.start())):
            continue
        if _line_in_debtor_zone(body, m.start()):
            continue
        cands.append(
            _vat_candidate_from_token(
                body,
                m.start(),
                vat,
                extraction_method="regex_fallback",
                label_reason="EU VAT pattern without explicit label",
                confidence=72,
            )
        )
    return cands


def _kvk_candidate_from_digits(
    body: str,
    pos: int,
    digits: str,
    *,
    extraction_method: str,
    label_reason: str,
    confidence: int,
) -> IdentFieldCandidate:
    return IdentFieldCandidate(
        value=digits,
        source="kvk",
        confidence=confidence,
        context=_line_context_at(body, pos),
        label="KvK",
        meta=_candidate_explain_meta(
            extraction_method=extraction_method,
            label_reason=label_reason,
            context_hint=_context_hint_at(body, pos),
        ),
    )


def _collect_kvk_candidates_primary(
    body: str,
    *,
    debtor_norm: str,
) -> list[IdentFieldCandidate]:
    cands: list[IdentFieldCandidate] = []
    for m in _KVK_RE.finditer(body):
        digits = _normalize_kvk_digits(m.group(1))
        if not digits or (debtor_norm and digits == debtor_norm):
            continue
        label_reason = "kvk label pattern"
        lm = _KVK_LABEL_RE.search(_line_at_pos(body, m.start()))
        if lm:
            extraction_method = "label_match"
            label_reason = f"near label: {lm.group(0)}"
        else:
            extraction_method = "regex_fallback"
        cands.append(
            _kvk_candidate_from_digits(
                body,
                m.start(),
                digits,
                extraction_method=extraction_method,
                label_reason=label_reason,
                confidence=88 if extraction_method == "label_match" else 85,
            )
        )

    line_starts: list[int] = []
    pos = 0
    for line in body.splitlines():
        line_starts.append(pos)
        pos += len(line) + 1
    for line_idx, line in enumerate(body.splitlines()):
        for lm in _KVK_LABEL_RE.finditer(line):
            after = line[lm.end() :]
            m_digits = re.search(r"(\d[\d\s]{6,11})", after)
            if not m_digits:
                continue
            digits = _normalize_kvk_digits(m_digits.group(1))
            if not digits or (debtor_norm and digits == debtor_norm):
                continue
            line_pos = line_starts[line_idx] if line_idx < len(line_starts) else 0
            abs_pos = line_pos + lm.end() + m_digits.start()
            if any(c.value == digits for c in cands):
                continue
            cands.append(
                _kvk_candidate_from_digits(
                    body,
                    abs_pos,
                    digits,
                    extraction_method="label_match",
                    label_reason=f"after label: {lm.group(0).strip()}",
                    confidence=88,
                )
            )
    return cands


_KVK_8DIGIT_FALLBACK_RE = re.compile(r"\b(\d[\d\s]{6,11})\b")


def _collect_kvk_candidates_fallback(
    body: str,
    *,
    debtor_norm: str,
) -> list[IdentFieldCandidate]:
    cands: list[IdentFieldCandidate] = []
    for m in _KVK_8DIGIT_FALLBACK_RE.finditer(body):
        digits = _normalize_kvk_digits(m.group(1))
        if not digits or len(digits) not in (7, 8):
            continue
        if debtor_norm and digits == debtor_norm:
            continue
        if not _kvk_in_business_context(body, m.start()):
            continue
        cands.append(
            _kvk_candidate_from_digits(
                body,
                m.start(),
                digits,
                extraction_method="regex_fallback",
                label_reason="8-digit in business registration context",
                confidence=75,
            )
        )
    return cands


def _email_candidate_from_match(
    body: str,
    m: re.Match[str],
    *,
    extraction_method: str,
    label_reason: str,
    confidence: int,
    abs_pos: int | None = None,
) -> IdentFieldCandidate | None:
    pos = abs_pos if abs_pos is not None else m.start()
    raw_email = re.sub(r"\s+", "", m.group(0))
    dom = _normalize_email_domain(m.group(1))
    if not dom:
        return None
    return IdentFieldCandidate(
        value=dom,
        source="email",
        confidence=confidence,
        context=_line_context_at(body, pos),
        label="E-mail",
        meta=_candidate_explain_meta(
            extraction_method=extraction_method,
            label_reason=label_reason,
            context_hint=_context_hint_at(body, pos),
            source_email=raw_email,
        ),
    )


def _collect_email_domain_candidates_primary(body: str) -> list[IdentFieldCandidate]:
    cands: list[IdentFieldCandidate] = []
    for m in _EMAIL_RE.finditer(body):
        c = _email_candidate_from_match(
            body,
            m,
            extraction_method="regex_fallback",
            label_reason="email address in text",
            confidence=86,
        )
        if c:
            cands.append(c)

    line_starts: list[int] = []
    pos_acc = 0
    for line in body.splitlines():
        line_starts.append(pos_acc)
        pos_acc += len(line) + 1
    for line_idx, line in enumerate(body.splitlines()):
        if not _EMAIL_CONTACT_LABEL_RE.search(line):
            continue
        line_pos = line_starts[line_idx] if line_idx < len(line_starts) else 0
        for m in _EMAIL_RE.finditer(line):
            c = _email_candidate_from_match(
                body,
                m,
                extraction_method="label_match",
                label_reason="email on contact/from/reply line",
                confidence=88,
                abs_pos=line_pos + m.start(),
            )
            if c:
                cands.append(c)
    return cands


def _collect_email_domain_candidates_region(
    body: str,
    segment: str,
    segment_offset: int,
    *,
    extraction_method: str,
) -> list[IdentFieldCandidate]:
    cands: list[IdentFieldCandidate] = []
    ctx_hint = extraction_method.replace("_scan", "")
    for m in _EMAIL_RE.finditer(segment):
        abs_start = segment_offset + m.start()
        raw_email = re.sub(r"\s+", "", m.group(0))
        dom = _normalize_email_domain(m.group(1))
        if not dom:
            continue
        cands.append(
            IdentFieldCandidate(
                value=dom,
                source="email",
                confidence=87,
                context=_line_context_at(body, abs_start),
                label="E-mail",
                meta=_candidate_explain_meta(
                    extraction_method=extraction_method,
                    label_reason=f"email in {extraction_method.replace('_', ' ')}",
                    context_hint=ctx_hint,
                    source_email=raw_email,
                ),
            )
        )
    return cands


def _collect_email_domain_candidates_fallback(body: str) -> list[IdentFieldCandidate]:
    cands: list[IdentFieldCandidate] = []
    contact_hint = re.compile(r"(?i)\b(?:mail|contact|support|www\.|http)\b")
    for line_idx, line in enumerate(body.splitlines()):
        if not contact_hint.search(line):
            continue
        hint = _context_hint_at(body, sum(len(ln) + 1 for ln in body.splitlines()[:line_idx]))
        if hint not in ("header", "footer", "body"):
            continue
        for m in _DOMAIN_WWW_RE.finditer(line):
            dom = _normalize_email_domain(m.group(0))
            if not dom:
                continue
            line_pos = sum(len(ln) + 1 for ln in body.splitlines()[:line_idx])
            cands.append(
                IdentFieldCandidate(
                    value=dom,
                    source="email",
                    confidence=70,
                    context=_line_context_at(body, line_pos + m.start()),
                    label="E-mail",
                    meta=_candidate_explain_meta(
                        extraction_method="regex_fallback",
                        label_reason="domain on contact line without @",
                        context_hint=hint,
                    ),
                )
            )
    return cands


def extract_email_domain_result(
    text: str,
    *,
    resolved: str | None = None,
    resolved_source: str | None = None,
) -> IdentFieldResult:
    body = text or ""
    cands = _collect_email_domain_candidates_primary(body)

    header = _header_segment(body)
    if header:
        off = 0
        cands.extend(
            _collect_email_domain_candidates_region(
                body, header, off, extraction_method="header_scan"
            )
        )
    footer = _footer_segment(body)
    if footer:
        off = len(body) - len(footer)
        if off < 0:
            off = 0
        cands.extend(
            _collect_email_domain_candidates_region(
                body, footer, off, extraction_method="footer_scan"
            )
        )

    if not cands:
        cands = _collect_email_domain_candidates_fallback(body)

    cands = _filter_ident_contamination(cands, field_id="email_domain", body=body)
    cands = _dedupe_candidates(cands)
    return build_ident_field_result(
        cands,
        resolved_value=_normalize_email_domain(resolved or "") if resolved else None,
        resolved_source=resolved_source,
    )


def extract_kvk_number_result(
    text: str,
    *,
    resolved: str | None = None,
    resolved_source: str | None = None,
    debtor_kvk: str | None = None,
    internal_vat_blacklist: frozenset[str] | None = None,
) -> IdentFieldResult:
    body = text or ""
    debtor_norm = _normalize_kvk_digits(debtor_kvk) if debtor_kvk else ""
    blacklist = internal_vat_blacklist or frozenset()
    cands = _collect_kvk_candidates_primary(body, debtor_norm=debtor_norm)
    if not cands:
        cands = _collect_kvk_candidates_fallback(body, debtor_norm=debtor_norm)
    cands = _filter_ident_contamination(cands, field_id="kvk_number", body=body)
    cands = _filter_internal_vat_blacklist(cands, blacklist, field_id="kvk_number")
    cands = _dedupe_candidates(cands)
    resolved_norm = _normalize_kvk_digits(resolved) if resolved else None
    return build_ident_field_result(
        cands,
        resolved_value=resolved_norm,
        resolved_source=resolved_source,
    )


def extract_vat_number_result(
    text: str,
    *,
    resolved: str | None = None,
    resolved_source: str | None = None,
    debtor_vat: str | None = None,
    internal_vat_blacklist: frozenset[str] | None = None,
) -> IdentFieldResult:
    body = text or ""
    _ = debtor_vat  # legacy param; exclusion via internal_vat_blacklist only
    debtor_norm = ""
    blacklist = internal_vat_blacklist or frozenset()
    cands = _collect_vat_candidates_primary(
        body, debtor_norm=debtor_norm, internal_vat_blacklist=blacklist
    )
    if not cands:
        cands = _collect_vat_candidates_fallback(
            body, debtor_norm=debtor_norm, internal_vat_blacklist=blacklist
        )
    cands = [
        c
        for c in cands
        if _supplier_vat_shape_ok(str(c.value or ""))
        and not _value_matches_internal_vat(str(c.value or ""), blacklist)
    ]
    cands = _filter_footer_vat_when_header_btw_nr(cands, body)
    cands = _filter_footer_regex_vat_when_header_labeled(cands, body)
    cands = _filter_ident_contamination(cands, field_id="vat_number", body=body)
    cands = _filter_internal_vat_blacklist(cands, blacklist, field_id="vat_number")
    cands = _dedupe_candidates(cands)
    resolved_norm = _normalize_vat_compact(resolved) if resolved else None
    if resolved_norm and _value_matches_internal_vat(resolved_norm, blacklist):
        resolved_norm = None
    return build_ident_field_result(
        cands,
        resolved_value=resolved_norm,
        resolved_source=resolved_source,
    )


def _month_name_to_int(name: str) -> int | None:
    key = re.sub(r"[^a-z]", "", str(name or "").strip().lower())
    if not key:
        return None
    return _MONTHS.get(key)


def _date_token_to_iso(token: str) -> str | None:
    from parser.field_model import normalize_field_value

    iso = normalize_field_value("invoice_date", token)
    if isinstance(iso, str) and iso:
        return iso

    m = _MONTH_NAME_DATE_RE.search(token or "")
    if not m:
        return None
    dd = int(m.group(1))
    mm = _month_name_to_int(m.group(2))
    yy = int(m.group(3))
    if not mm:
        return None
    try:
        from datetime import date

        return date(yy, mm, dd).isoformat()
    except ValueError:
        return None


def _date_token_to_iso_explain(token: str) -> tuple[str | None, dict[str, Any]]:
    """Return (iso, meta) without affecting parsing behavior."""
    raw = str(token or "")
    from parser.field_model import normalize_field_value

    try:
        iso = normalize_field_value("invoice_date", raw)
    except Exception:
        iso = None
    if isinstance(iso, str) and iso:
        meta = _candidate_explain_meta(
            extraction_method="regex",
            label_reason="date token normalized via field_model.normalize_field_value(invoice_date)",
            score_breakdown={"base": 90, "normalization_bonus": 2},
            raw_detected=raw,
            normalized_iso=iso,
            parse_path="normalize_field_value",
        )
        return iso, meta

    m = _MONTH_NAME_DATE_RE.search(raw)
    if not m:
        meta = _candidate_explain_meta(
            extraction_method="regex",
            label_reason="date token did not match supported patterns",
            score_breakdown={"base": 0},
            raw_detected=raw,
            normalized_iso=None,
            parse_path="unparsed",
        )
        return None, meta

    dd = int(m.group(1))
    mm = _month_name_to_int(m.group(2))
    yy = int(m.group(3))
    if not mm:
        meta = _candidate_explain_meta(
            extraction_method="regex",
            label_reason="month name not recognized",
            score_breakdown={"base": 0},
            raw_detected=raw,
            normalized_iso=None,
            parse_path="month_name_unrecognized",
        )
        return None, meta
    try:
        from datetime import date

        iso2 = date(yy, mm, dd).isoformat()
        meta = _candidate_explain_meta(
            extraction_method="regex",
            label_reason="month-name date parsed via _MONTH_NAME_DATE_RE + datetime.date",
            score_breakdown={"base": 88, "normalization_bonus": 1},
            raw_detected=raw,
            normalized_iso=iso2,
            parse_path="month_name_date",
        )
        return iso2, meta
    except ValueError:
        meta = _candidate_explain_meta(
            extraction_method="regex",
            label_reason="month-name date invalid (ValueError)",
            score_breakdown={"base": 0},
            raw_detected=raw,
            normalized_iso=None,
            parse_path="month_name_value_error",
        )
        return None, meta


def extract_invoice_date_result(
    text: str,
    *,
    resolved: str | None = None,
    resolved_source: str | None = None,
) -> IdentFieldResult:
    body = text or ""
    cands: list[IdentFieldCandidate] = []

    # Strong: "Factuur nr ... van 30-01-2026"
    for m in _INVOICE_NR_VAN_DATE_RE.finditer(body):
        raw_tok = m.group(1)
        iso, meta = _date_token_to_iso_explain(raw_tok)
        if not iso:
            continue
        cands.append(
            IdentFieldCandidate(
                value=iso,
                source="invoice_nr_van_date",
                confidence=92,
                context=_line_context_at(body, m.start()),
                label="Factuurdatum",
                meta={
                    **meta,
                    **_candidate_explain_meta(
                        extraction_method=str(meta.get("extraction_method") or "regex"),
                        label_reason="regex match: _INVOICE_NR_VAN_DATE_RE",
                        score_breakdown={"base": 92, "label_bonus": 4},
                    ),
                },
            )
        )

    lines = (body or "").splitlines()
    for i, line in enumerate(lines):
        if _DATE_EXCLUDE_HINT_RE.search(line or ""):
            continue
        lm = _INVOICE_DATE_LABEL_RE.search(line or "")
        if not lm:
            continue
        ctx = re.sub(r"\s+", " ", (line or "")).strip()[:160]
        added_same_line = False
        # Same line after label
        tail = (line or "")[lm.end() :]
        same_line_conf = 90
        if re.search(r"(?i)\bna\s+factuurdatum\b", line or ""):
            same_line_conf = 60
        for rx in (_ISO_DATE_RE, _DD_MM_YYYY_RE, _MONTH_NAME_DATE_RE):
            dm = rx.search(tail)
            if dm:
                raw_tok = dm.group(0)
                iso, meta = _date_token_to_iso_explain(raw_tok)
                if iso:
                    cands.append(
                        IdentFieldCandidate(
                            value=iso,
                            source="invoice_date_label_same_line",
                            confidence=same_line_conf,
                            context=ctx,
                            label=collapse_stutter_chars(line[lm.start() : lm.end()].strip())
                            or "Factuurdatum",
                            meta={
                                **meta,
                                **_candidate_explain_meta(
                                    extraction_method=str(meta.get("extraction_method") or "regex"),
                                    label_reason=f"after invoice-date label (same line), regex: {getattr(rx, 'pattern', '')}",
                                    score_breakdown={"base": same_line_conf, "label_bonus": 3},
                                ),
                            },
                        )
                    )
                    added_same_line = True
                break
        if added_same_line:
            continue
        # Nearby lines: ±3 lines around label (excluding the label line itself).
        for j in (-3, -2, -1, 1, 2, 3):
            idx = i + j
            if idx < 0 or idx >= len(lines):
                continue
            nxt = lines[idx] or ""
            if _DATE_EXCLUDE_HINT_RE.search(nxt):
                continue
            nxt_ctx = re.sub(r"\s+", " ", nxt).strip()[:160]
            for rx in (_ISO_DATE_RE, _DD_MM_YYYY_RE, _MONTH_NAME_DATE_RE):
                dm = rx.search(nxt)
                if not dm:
                    continue
                raw_tok = dm.group(0)
                iso, meta = _date_token_to_iso_explain(raw_tok)
                if not iso:
                    continue
                cands.append(
                    IdentFieldCandidate(
                        value=iso,
                        source="invoice_date_label_next_line",
                        confidence=max(78, 86 - abs(j) * 2),
                        context=nxt_ctx or ctx,
                        label="Factuurdatum",
                        meta={
                            **meta,
                            **_candidate_explain_meta(
                                extraction_method=str(meta.get("extraction_method") or "regex"),
                                label_reason=f"near invoice-date label (offset {j}), regex: {getattr(rx, 'pattern', '')}",
                                score_breakdown={
                                    "base": max(78, 86 - abs(j) * 2),
                                    "label_bonus": 2,
                                    "distance_penalty": abs(j),
                                },
                            ),
                        },
                    )
                )
                break

    # Deterministic fallback: if no label candidates were found, consider non-due dates
    # document-wide and rank them lower than labeled/near-label candidates.
    if not cands:
        for line in lines:
            ln = line or ""
            if _DATE_EXCLUDE_HINT_RE.search(ln):
                continue
            for rx in (_ISO_DATE_RE, _DD_MM_YYYY_RE, _MONTH_NAME_DATE_RE):
                dm = rx.search(ln)
                if not dm:
                    continue
                raw_tok = dm.group(0)
                iso, meta = _date_token_to_iso_explain(raw_tok)
                if not iso:
                    continue
                cands.append(
                    IdentFieldCandidate(
                        value=iso,
                        source="invoice_date_fallback_any",
                        confidence=66,
                        context=re.sub(r"\s+", " ", ln).strip()[:160],
                        label="Datum",
                        meta={
                            **meta,
                            **_candidate_explain_meta(
                                extraction_method=str(meta.get("extraction_method") or "regex"),
                                label_reason="document fallback date (non-due line)",
                                score_breakdown={"base": 66, "fallback_penalty": 20},
                            ),
                        },
                    )
                )
                break

    cands = _dedupe_candidates(cands)

    resolved_iso, _ = _date_token_to_iso_explain(resolved or "") if resolved else (None, {})
    return build_ident_field_result(
        cands,
        resolved_value=resolved_iso,
        resolved_source=resolved_source,
        field_id="invoice_date",
    )
