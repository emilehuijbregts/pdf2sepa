"""IBAN-kandidaten (zelfde IdentFieldResult-vorm als factuur-/klantnummer)."""

from __future__ import annotations

import re
from typing import Any

from logic.validation import clean_iban, is_plausible_iban
from parser.field_candidates import (
    IdentFieldCandidate,
    IdentFieldResult,
    build_ident_field_result,
)
from parser.pdf_parser import _scan_sepa_ibans_in_text

_IBAN_LABEL_RE = re.compile(
    r"(?i)\b(?:IBAN|Rekening(?:nummer)?|Bankrekening|Bank\s*rekening|BIC\s*/\s*IBAN)\b"
)


def _has_label_on_line(line: str) -> bool:
    return bool(_IBAN_LABEL_RE.search(line or ""))


def _context_for_iban_in_text(text: str, iban: str) -> str:
    lines = (text or "").split("\n")
    compact = clean_iban(iban)
    for line in lines:
        line_compact = re.sub(r"\s+", "", (line or "").upper())
        if compact and compact in line_compact:
            return re.sub(r"\s+", " ", (line or "").strip())[:160]
    if len(compact) > 6:
        return f"IBAN {compact[:4]}…{compact[-4:]}"
    return compact


def collect_iban_candidates_from_text(
    text: str,
    *,
    debtor_iban: str | None = None,
) -> list[IdentFieldCandidate]:
    debtor_clean = clean_iban(debtor_iban) if debtor_iban else ""
    raw_ibans = _scan_sepa_ibans_in_text(text or "")
    cands: list[IdentFieldCandidate] = []
    pdf_rank = 0

    for iban in raw_ibans:
        if not is_plausible_iban(iban):
            continue
        if debtor_clean and iban == debtor_clean:
            continue

        ctx = _context_for_iban_in_text(text, iban)
        has_label = any(
            _has_label_on_line(line)
            for line in (text or "").split("\n")
            if clean_iban(iban) in re.sub(r"\s+", "", (line or "").upper())
        )
        if has_label:
            conf = 88
        elif pdf_rank == 0:
            conf = 78
        else:
            conf = 72

        cands.append(
            IdentFieldCandidate(
                value=iban,
                source="pdf_text",
                confidence=conf,
                context=ctx,
                label="IBAN" if has_label else "",
            )
        )
        pdf_rank += 1

    return cands


def collect_iban_candidates_from_ocr(
    ocr_ibans: list[str],
    *,
    pdf_had_any: bool,
) -> list[IdentFieldCandidate]:
    cands: list[IdentFieldCandidate] = []
    for raw in ocr_ibans:
        iban = clean_iban(str(raw or ""))
        if not iban or not is_plausible_iban(iban):
            continue
        conf = 90 if not pdf_had_any else 82
        cands.append(
            IdentFieldCandidate(
                value=iban,
                source="ocr",
                confidence=conf,
                context="OCR",
                label="OCR",
            )
        )
    return cands


def merge_iban_candidates(
    pdf: list[IdentFieldCandidate],
    ocr: list[IdentFieldCandidate],
) -> list[IdentFieldCandidate]:
    out: list[IdentFieldCandidate] = []
    seen: set[str] = set()
    for c in pdf + ocr:
        key = clean_iban(c.value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(c)
    out.sort(key=lambda c: (-int(c.confidence or 0), c.value))
    return out


def iban_values_from_candidates(candidates: list[IdentFieldCandidate]) -> list[str]:
    return [clean_iban(c.value) for c in candidates if clean_iban(c.value)]


def _candidates_from_result_dict(data: dict[str, Any]) -> list[IdentFieldCandidate]:
    out: list[IdentFieldCandidate] = []
    for c in data.get("candidates") or []:
        if not isinstance(c, dict):
            continue
        val = clean_iban(str(c.get("value") or ""))
        if not val or not is_plausible_iban(val):
            continue
        out.append(
            IdentFieldCandidate(
                value=val,
                source=str(c.get("source") or "").strip(),
                confidence=int(c.get("confidence") or 0),
                context=str(c.get("context") or ""),
                label=str(c.get("label") or "").strip(),
            )
        )
    return out


def extract_iban_result(
    text: str,
    *,
    debtor_iban: str | None = None,
    ocr_ibans: list[str] | None = None,
    resolved: str | None = None,
    resolved_source: str | None = None,
) -> IdentFieldResult:
    pdf_cands = collect_iban_candidates_from_text(text, debtor_iban=debtor_iban)
    ocr_cands = collect_iban_candidates_from_ocr(
        list(ocr_ibans or []),
        pdf_had_any=bool(pdf_cands),
    )
    merged = merge_iban_candidates(pdf_cands, ocr_cands)
    resolved_clean = clean_iban(resolved) if resolved else None
    return build_ident_field_result(
        merged,
        resolved_value=resolved_clean or None,
        resolved_source=resolved_source,
    )


def merge_ocr_into_iban_result(
    existing_result: dict[str, Any],
    ocr_ibans: list[str],
    *,
    debtor_iban: str | None = None,
) -> IdentFieldResult:
    """Voeg OCR-IBANs toe aan bestaand ``iban_result`` (loader)."""
    existing_cands = _candidates_from_result_dict(existing_result)
    debtor_clean = clean_iban(debtor_iban) if debtor_iban else ""
    existing_cands = [
        c
        for c in existing_cands
        if not debtor_clean or clean_iban(c.value) != debtor_clean
    ]
    ocr_cands = collect_iban_candidates_from_ocr(
        list(ocr_ibans or []),
        pdf_had_any=bool(existing_cands),
    )
    merged = merge_iban_candidates(existing_cands, ocr_cands)
    resolved = clean_iban(str(existing_result.get("value") or "")) or None
    resolved_source = str(existing_result.get("source") or "").strip() or None
    if not resolved and ocr_ibans:
        for raw in ocr_ibans:
            cand = clean_iban(str(raw or ""))
            if cand and is_plausible_iban(cand):
                resolved = cand
                resolved_source = "ocr"
                break
    return build_ident_field_result(
        merged,
        resolved_value=resolved,
        resolved_source=resolved_source,
    )
