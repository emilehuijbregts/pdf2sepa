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
_PAGE_FOOTER_MARKER_RE = re.compile(r"\b\d{1,3}\s*/\s*\d{1,3}\s*$")
_LABELED_IBAN_ON_LINE_RE = re.compile(r"(?i)\bIBAN\b")


def _has_label_on_line(line: str) -> bool:
    return bool(_IBAN_LABEL_RE.search(line or ""))


def _iban_line_confidence_adjustment(line: str, iban: str) -> int:
    """Contextuele bonus/penalty voor meerdere IBAN's op één factuur (bv. PGB-footer)."""
    adj = 0
    if re.search(r"(?i)\bpeppol\b", line or ""):
        adj -= 25
    if _PAGE_FOOTER_MARKER_RE.search((line or "").strip()):
        adj -= 20
    ibans = _scan_sepa_ibans_in_text(line or "")
    if len(ibans) <= 1:
        return adj
    adj -= 4 * (len(ibans) - 1)
    compact = clean_iban(iban)
    try:
        pos = ibans.index(compact)
    except ValueError:
        return adj
    labeled_count = len(_LABELED_IBAN_ON_LINE_RE.findall(line or ""))
    if labeled_count > 1:
        adj += pos * 4
    elif labeled_count == 0:
        adj -= pos * 4
    return adj


def _best_iban_line_context(text: str, iban: str) -> tuple[str, int]:
    """Kies de meest betrouwbare regel voor context + confidence-adjustment."""
    compact = clean_iban(iban)
    best_ctx = ""
    best_adj = -999
    best_idx = 999999
    for i, line in enumerate((text or "").splitlines()):
        line_compact = re.sub(r"\s+", "", (line or "").upper())
        if not compact or compact not in line_compact:
            continue
        adj = _iban_line_confidence_adjustment(line, iban)
        ctx = re.sub(r"\s+", " ", (line or "").strip())[:160]
        if adj > best_adj or (adj == best_adj and i < best_idx):
            best_adj = adj
            best_ctx = ctx
            best_idx = i
    if best_ctx:
        return best_ctx, best_adj
    if len(compact) > 6:
        return f"IBAN {compact[:4]}…{compact[-4:]}", 0
    return compact, 0


def _context_for_iban_in_text(text: str, iban: str) -> str:
    ctx, _adj = _best_iban_line_context(text, iban)
    return ctx


def collect_iban_candidates_from_text(
    text: str,
    *,
    debtor_iban: str | None = None,
    context_text: str | None = None,
) -> list[IdentFieldCandidate]:
    debtor_clean = clean_iban(debtor_iban) if debtor_iban else ""
    ctx_source = context_text if context_text is not None else text
    lines = (text or "").split("\n")
    raw_ibans = _scan_sepa_ibans_in_text(text or "")
    # Extra cross-line pass: label line + next line merge (OCR/PDF line breaks).
    for i, line in enumerate(lines):
        if not _has_label_on_line(line):
            continue
        if i + 1 >= len(lines):
            continue
        merged = f"{line}\n{lines[i + 1]}"
        raw_ibans.extend(_scan_sepa_ibans_in_text(merged))
    cands: list[IdentFieldCandidate] = []
    pdf_rank = 0
    seen_values: set[str] = set()

    for iban in raw_ibans:
        if not is_plausible_iban(iban):
            continue
        if debtor_clean and iban == debtor_clean:
            continue
        if iban in seen_values:
            continue
        seen_values.add(iban)

        ctx, ctx_adj = _best_iban_line_context(ctx_source, iban)
        has_label = any(
            _has_label_on_line(line)
            for line in (text or "").split("\n")
            if clean_iban(iban) in re.sub(r"\s+", "", (line or "").upper())
        )
        if has_label:
            conf = max(40, min(95, 88 + ctx_adj))
        elif pdf_rank == 0:
            conf = max(40, min(90, 78 + ctx_adj))
        else:
            conf = max(40, min(85, 72 + ctx_adj))

        cands.append(
            IdentFieldCandidate(
                value=iban,
                source="pdf_text",
                confidence=conf,
                context=ctx,
                label="IBAN" if has_label else "",
                meta={
                    "match_type": "label" if has_label else "fallback",
                    "label_source": "IBAN" if has_label else "",
                    "field_id": "iban",
                    "iban_context_adjustment": ctx_adj,
                },
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
    context_text: str | None = None,
) -> IdentFieldResult:
    pdf_cands = collect_iban_candidates_from_text(
        text,
        debtor_iban=debtor_iban,
        context_text=context_text,
    )
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
        field_id="iban",
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
        field_id="iban",
    )
