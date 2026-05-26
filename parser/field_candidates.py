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

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "candidates": [c.to_dict() for c in self.candidates],
            "value": self.value,
            "confidence": self.confidence,
            "source": self.source,
            "status": self.status,
        }
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
    if len(candidates) == 1:
        c = candidates[0]
        st = "confirmed" if c.confidence >= 80 else "tentative"
        return IdentFieldResult(
            candidates=candidates,
            value=c.value,
            confidence=c.confidence,
            source=c.source,
            status=st,
        )
    top = candidates[0]
    second = candidates[1]
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
    return build_ident_field_result(
        cands,
        resolved_value=resolved,
        resolved_source=resolved_source,
    )


def extract_customer_number_result(
    text: str,
    *,
    resolved: str | None = None,
    resolved_source: str | None = None,
) -> IdentFieldResult:
    cands = collect_ident_field_candidates(
        text,
        _CUSTOMER_LABEL_RE,
        field_kind="customer_number",
    )
    return build_ident_field_result(
        cands,
        resolved_value=resolved,
        resolved_source=resolved_source,
    )
