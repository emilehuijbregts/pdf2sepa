"""Kandidaten voor factuur-/klantnummer (zelfde idee als ``AmountResult``)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from parser.pdf_parser import (
    _CUSTOMER_LABEL_RE,
    _DD_MM_YYYY_RE,
    _FIELD_VALUE_RE,
    _INVOICE_LABEL_RE,
    _ISO_DATE_RE,
    _is_noise_value,
    _looks_like_date_token,
    _score_customer_candidate_token,
    collapse_stutter_chars,
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
_YEAR_SLASH_REF_RE = re.compile(r"(?<![A-Za-z0-9./])(\d{2}/\d{7,})(?!\d)")
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
    r"klantrekening|uw\s+klant)\b"
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

@dataclass
class IdentFieldCandidate:
    value: str
    source: str
    confidence: int
    context: str
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "context": self.context,
            "label": self.label,
        }


@dataclass
class IdentFieldResult:
    candidates: list[IdentFieldCandidate] = field(default_factory=list)
    value: str | None = None
    confidence: int = 0
    source: str = "UNKNOWN"
    status: str = "failed"
    user_selected: bool = False
    absence_state: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "candidates": [c.to_dict() for c in self.candidates],
            "value": self.value,
            "selected_value": self.value,
            "confidence": self.confidence,
            "source": self.source,
            "status": self.status,
            "decision_trace": [],
            "override_reason": "",
            "resolver_finalized": False,
        }
        if self.absence_state:
            d["absence_state"] = self.absence_state
        if self.user_selected:
            d["user_selected"] = True
        return d


def _normalize_ident_value(raw: str, *, join_spaced_digits: bool = False) -> str | None:
    s = re.sub(r"\s+", " ", (raw or "").strip())
    if not s or _is_noise_value(s) or _looks_like_date_token(s):
        return None
    if join_spaced_digits and re.fullmatch(r"[\d\s]+", s):
        compact = re.sub(r"\s+", "", s)
        if len(compact) >= 4:
            return compact
    return s


def _tokens_after_label(line: str, end: int, *, join_spaced_digits: bool) -> list[str]:
    after = re.sub(r"^[\s:\.\[\]]+", "", (line or "")[end:])
    if not after.strip():
        return []
    if join_spaced_digits:
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


def _invoice_candidate_ok(value: str) -> bool:
    return bool(
        re.search(r"\d", value)
        and len(value) >= 4
        and not _is_noise_value(value)
    )


def _token_from_date_invoice_line(line: str) -> str | None:
    m = _DATE_INVOICE_LINE_RE.match((line or "").strip())
    if not m:
        return None
    val = m.group(1).strip()
    if re.fullmatch(r"\d{1,2}/\d{1,2}", val):
        return None
    return val if _invoice_candidate_ok(val) else None


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
            )
        )
    return cands


def _collect_invoice_fallback_candidates(text: str) -> list[IdentFieldCandidate]:
    """Layout-fallbacks zonder expliciet factuurnummer-label."""
    cands: list[IdentFieldCandidate] = []
    body = text or ""

    cands.extend(_collect_datum_nummer_table_candidates(body))

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


def _same_line_plausible_customer_values(
    vals: list[str],
    *,
    label_line: str,
) -> list[str]:
    """Kolomkoppen (MAGAZIJN, CODE) zijn geen klantnummer op dezelfde regel als ``Klantcode``."""
    out: list[str] = []
    for val in vals:
        if not _customer_value_ok(val, label_line=label_line):
            continue
        if not re.search(r"\d", val):
            continue
        if re.fullmatch(r"(?i)[a-z]+", val):
            continue
        out.append(val)
    return out


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


def _customer_value_ok(value: str, *, label_line: str = "") -> bool:
    v = str(value or "").strip()
    if not _customer_candidate_ok(v):
        return False
    if _is_order_or_reference_token(v, line=label_line):
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
        for val in same_line_vals:
            norm = _normalize_k_customer_code(val) or val
            cands.append(
                IdentFieldCandidate(
                    value=norm,
                    source="label_block_same_line",
                    confidence=94,
                    context=ctx,
                    label=label_span,
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
            for val in _tokens_after_label(nxt_line, 0, join_spaced_digits=False):
                if not _customer_value_ok(val, label_line=line):
                    continue
                norm = _normalize_k_customer_code(val) or val
                if re.search(r"(?i)\buw\s+klant\b", line or "") and re.fullmatch(
                    r"0?\d{4,10}", val
                ):
                    norm = _compose_k_digits(val) or norm
                cands.append(
                    IdentFieldCandidate(
                        value=norm,
                        source="label_block_next_line",
                        confidence=93 - j,
                        context=nxt_ctx,
                        label=label_span,
                    )
                )
                got = True
                break
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
        cands.append(
            IdentFieldCandidate(
                value=v,
                source=source,
                confidence=confidence,
                context=_line_context_at(body, pos),
                label=label,
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
) -> list[IdentFieldCandidate]:
    rv = str(resolved_value or "").strip()
    if not rv:
        return candidates
    if any(c.value.casefold() == rv.casefold() for c in candidates):
        return candidates
    return candidates + [
        IdentFieldCandidate(
            value=rv,
            source=str(resolved_source or "resolved"),
            confidence=92,
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


def _dedupe_candidates(cands: list[IdentFieldCandidate]) -> list[IdentFieldCandidate]:
    seen: set[str] = set()
    out: list[IdentFieldCandidate] = []
    for c in sorted(cands, key=lambda x: (-x.confidence, -len(x.value))):
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
        for rx, src_kind in label_patterns:
            for m in rx.finditer(line or ""):
                label_span = collapse_stutter_chars(line[m.start() : m.end()].strip()) or line[m.start() : m.end()].strip()
                vals = _tokens_after_label(line, m.end(), join_spaced_digits=join_digits)
                for j in (0, 1, 2):
                    if j > 0:
                        if i + j >= len(lines):
                            break
                        nxt = lines[i + j]
                        ctx_n = re.sub(r"\s+", " ", (nxt or "")).strip()[:160]
                        for val in _tokens_after_label(nxt, 0, join_spaced_digits=join_digits):
                            conf = 72 - j * 8
                            cands.append(
                                IdentFieldCandidate(
                                    value=val,
                                    source=f"{src_kind}_next_line",
                                    confidence=conf,
                                    context=ctx_n or ctx,
                                    label=label_span,
                                )
                            )
                        continue
                    for val in vals:
                        conf = 88 if src_kind == "label" else 85
                        cands.append(
                            IdentFieldCandidate(
                                value=val,
                                source=src_kind,
                                confidence=conf,
                                context=ctx,
                                label=label_span,
                            )
                        )
    if field_kind == "invoice_number":
        cands = [
            c
            for c in cands
            if re.search(r"\d", c.value) and len(c.value) >= 4 and not _is_noise_value(c.value)
        ]
    elif field_kind == "customer_number":
        cands = [c for c in cands if re.search(r"\d", c.value) and len(c.value) >= 3]
    return _dedupe_candidates(cands)


def _prefer_polis_candidate(
    candidates: list[IdentFieldCandidate],
    resolved_value: str | None,
) -> IdentFieldCandidate | None:
    polis = [
        c
        for c in candidates
        if re.search(r"(?i)polis", c.label or "") or c.source == "extra"
    ]
    if not polis:
        return None
    best = max(polis, key=lambda c: (len(c.value), c.confidence))
    rv = str(resolved_value or "").strip()
    if not rv or (rv.isdigit() and best.value.isdigit() and rv in best.value and rv != best.value):
        return best
    return None


def build_ident_field_result(
    candidates: list[IdentFieldCandidate],
    *,
    resolved_value: str | None = None,
    resolved_source: str | None = None,
    prefer_k_prefix: bool = False,
) -> IdentFieldResult:
    """Selecteer status; ``resolved_value`` van legacy extractie heeft voorrang."""
    polis_best = _prefer_polis_candidate(candidates, resolved_value)
    if polis_best is not None:
        resolved_value = polis_best.value
        resolved_source = polis_best.source
    candidates = _merge_resolved_into_candidates(
        candidates, resolved_value, resolved_source
    )
    if resolved_value and str(resolved_value).strip():
        val = str(resolved_value).strip()
        return IdentFieldResult(
            candidates=candidates,
            value=val,
            confidence=95,
            source=str(resolved_source or "resolved"),
            status="confirmed",
        )
    if not candidates:
        return IdentFieldResult(status="failed", source="NOT_FOUND")
    ordered = list(candidates)
    if prefer_k_prefix:
        k_cands = [c for c in ordered if _is_k_customer_code(c.value)]
        other = [c for c in ordered if not _is_k_customer_code(c.value)]
        if k_cands:
            ordered = sorted(k_cands, key=lambda x: (-x.confidence, -len(x.value))) + sorted(
                other, key=lambda x: (-x.confidence, -len(x.value))
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
        )
    top = ordered[0]
    second = ordered[1]
    if top.confidence >= 85 and top.confidence - second.confidence >= 12:
        return IdentFieldResult(
            candidates=candidates,
            value=top.value,
            confidence=top.confidence,
            source=top.source,
            status="tentative",
        )
    return IdentFieldResult(
        candidates=candidates,
        value=None,
        confidence=max(c.confidence for c in candidates),
        source="AMBIGUOUS",
        status="ambiguous",
    )


def extract_invoice_number_result(
    text: str,
    *,
    resolved: str | None = None,
    resolved_source: str | None = None,
) -> IdentFieldResult:
    cands = collect_ident_field_candidates(
        text,
        _INVOICE_LABEL_RE,
        field_kind="invoice_number",
        extra_label_res=(_POLIS_LABEL_RE, _RELATIE_LABEL_RE),
    )
    cands.extend(_collect_invoice_fallback_candidates(text))
    cands = _dedupe_candidates(cands)
    result = build_ident_field_result(
        cands,
        resolved_value=resolved,
        resolved_source=resolved_source,
    )
    return result


def extract_customer_number_result(
    text: str,
    *,
    resolved: str | None = None,
    resolved_source: str | None = None,
    supplier_customer_absent: bool = False,
) -> IdentFieldResult:
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
    cands = _drop_kvk_smear_k_candidates(
        _drop_false_k_glue_candidates(
            _drop_redundant_k_suffix_candidates(
                _dedupe_candidates(
                    block_cands + label_cands + collapsed_cands + fallback_cands
                )
            )
        ),
        body,
    )
    cands = _filter_weak_customer_fallbacks(cands)
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
    )
    has_value = bool(str(result.value or "").strip())
    if not cands and not has_value:
        if supplier_customer_absent:
            result.absence_state = "NOT_PRESENT_SUPPLIER_LEVEL"
            result.source = "NOT_PRESENT_SUPPLIER_LEVEL"
        else:
            result.absence_state = "NOT_FOUND"
            result.source = "NOT_FOUND"
        result.status = "failed"
    return result
