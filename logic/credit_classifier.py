"""Generic credit-invoice document classification (supplier-agnostic)."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Strong document-type keywords (NL / DE / EN).
_STRONG_KEYWORD_RE = re.compile(
    r"(?i)\b(?:"
    r"creditnota|credit\s*note|creditfactuur|verkoopcredit(?:nota)?|"
    r"credit\s*invoice|cren|gutschrift|storno|reversal|correctiefactuur|correctie"
    r")\b"
)

# Title / header patterns typical of credit documents.
_TITLE_CREDIT_FACTUUR_RE = re.compile(
    r"(?i)\b(?:credit\s*factuur|creditfactuur|credit\s*invoice)\b"
)
_TITLE_CREDIT_BANNER_RE = re.compile(r"(?i)\*{2,}\s*credit\s*\*{2,}")
_CREDITNOTA_TITLE_LINE_RE = re.compile(
    r"(?i)^\s*(?:creditnota|verkoopcreditnota|credit\s*note)\b"
)

# Weak: standalone "credit" only counts with invoice/nota context nearby.
_WEAK_CREDIT_RE = re.compile(r"(?i)\bcredit\b")
_CREDIT_CONTEXT_RE = re.compile(
    r"(?i)\b(?:nota|factuur|invoice|volgens\s+afspraak|slechte|terug|crediteren)\b"
)

# Negative payable totals (exclude date fragments; require monetary decimals).
_NEGATIVE_AMOUNT_TOKEN = r"-\s*(?!\d{2}-)\d+(?:[.,]\d{2})\b"
_NEGATIVE_TOTAL_RE = re.compile(
    r"(?i)\b(?:"
    r"factuurbedrag|totaal(?:\s*te\s*betalen)?|te\s*betalen|total\s*due|"
    r"gesamtbetrag|amount\s*due"
    rf")\b[^\n]{{0,40}}?{_NEGATIVE_AMOUNT_TOKEN}"
)
_NEGATIVE_AMOUNT_LINE_RE = re.compile(
    rf"(?i)(?:EUR|€)\s*{_NEGATIVE_AMOUNT_TOKEN}|{_NEGATIVE_AMOUNT_TOKEN}(?:\s*(?:EUR|€))?"
)

# VAT reversal lines (negative VAT amount near total context).
_NEGATIVE_VAT_LINE_RE = re.compile(
    r"(?i)\bbtw\b[^\n]{0,30}?-\s*\d[\d.,]*"
)

# False-positive guards.
_CREDITOR_RE = re.compile(r"(?i)\bcreditor\b")
_CREDIT_TRANSFER_RE = re.compile(r"(?i)\bcredit\s*transfer\b")

# Minimum score to classify as credit.
_CREDIT_THRESHOLD = 50

_SIGNAL_SCORES: dict[str, int] = {
    "keyword_strong": 70,
    "title_credit_factuur": 65,
    "title_credit_banner": 60,
    "creditnota_title_line": 65,
    "weak_credit_with_context": 45,
    "negative_total_label": 55,
    "negative_amount_line": 40,
    "negative_vat_line": 25,
    "metadata_type_credit_note": 80,
}


@dataclass(frozen=True)
class CreditDetectionResult:
    is_credit: bool
    confidence: int
    signals: tuple[str, ...]
    reason: str


def _first_lines(text: str, n: int = 12) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[:n])


def classify_credit_document(
    text: str,
    metadata: dict | None = None,
) -> CreditDetectionResult:
    """Score-based credit document classifier.

    Deterministic and supplier-agnostic. Uses weighted signals; requires
    ``_CREDIT_THRESHOLD`` unless metadata already marks ``type=credit_note``.
    """
    meta = metadata or {}
    body = text or ""
    head = _first_lines(body, 15)
    signals: list[str] = []
    score = 0

    if str(meta.get("type") or "").strip().lower() == "credit_note":
        signals.append("metadata_type_credit_note")
        score += _SIGNAL_SCORES["metadata_type_credit_note"]

    if _STRONG_KEYWORD_RE.search(body):
        signals.append("keyword_strong")
        score += _SIGNAL_SCORES["keyword_strong"]

    if _TITLE_CREDIT_FACTUUR_RE.search(head):
        signals.append("title_credit_factuur")
        score += _SIGNAL_SCORES["title_credit_factuur"]

    if _TITLE_CREDIT_BANNER_RE.search(body):
        signals.append("title_credit_banner")
        score += _SIGNAL_SCORES["title_credit_banner"]

    if _CREDITNOTA_TITLE_LINE_RE.search(body):
        signals.append("creditnota_title_line")
        score += _SIGNAL_SCORES["creditnota_title_line"]

    if _WEAK_CREDIT_RE.search(body) and _CREDIT_CONTEXT_RE.search(body):
        signals.append("weak_credit_with_context")
        score += _SIGNAL_SCORES["weak_credit_with_context"]

    if _NEGATIVE_TOTAL_RE.search(body):
        signals.append("negative_total_label")
        score += _SIGNAL_SCORES["negative_total_label"]

    if _NEGATIVE_AMOUNT_LINE_RE.search(body):
        signals.append("negative_amount_line")
        score += _SIGNAL_SCORES["negative_amount_line"]

    if _NEGATIVE_VAT_LINE_RE.search(body):
        signals.append("negative_vat_line")
        score += _SIGNAL_SCORES["negative_vat_line"]

    # Penalize common false positives unless strong credit signals present.
    has_strong = any(
        s in signals
        for s in (
            "keyword_strong",
            "title_credit_factuur",
            "title_credit_banner",
            "creditnota_title_line",
            "metadata_type_credit_note",
            "negative_total_label",
        )
    )
    if not has_strong:
        if _CREDITOR_RE.search(body) or _CREDIT_TRANSFER_RE.search(body):
            score = max(0, score - 30)

    confidence = min(100, score)
    is_credit = confidence >= _CREDIT_THRESHOLD

    if is_credit:
        reason = "credit_signals_above_threshold"
    elif signals:
        reason = "credit_signals_below_threshold"
    else:
        reason = "no_credit_signals"

    return CreditDetectionResult(
        is_credit=is_credit,
        confidence=confidence,
        signals=tuple(signals),
        reason=reason,
    )
