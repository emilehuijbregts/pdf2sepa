"""
Generic profile extraction strategy engine.

Learn-time: all strategies run → validate_profile → strategy_confidence → best-win.
Runtime: execute persisted spec; optional fallback re-runs pipeline without learn-only strategies.

Positions are never persisted — only {label, strategy, confirmed_value} contracts.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from logic.validation import clean_iban, is_plausible_iban
from parser.field_model import FieldId, normalize_field_value
from parser.iban_candidates import _IBAN_LABEL_RE
from parser.pdf_parser import (
    _AMOUNT_PROFILE_LABEL_RE,
    _AMOUNT_TOKEN,
    _CUSTOMER_LABEL_RE,
    _INVOICE_LABEL_RE,
    _TOTAL_LINE_EXCLUDE_RE,
    _TOTAL_LINE_HINT_RE,
    _iter_amount_tokens_excluding_percent,
    _scan_sepa_ibans_in_text,
    collapse_stutter_chars,
    normalize_amount,
    normalize_amount_decimal,
)

logger = logging.getLogger(__name__)

StrategyMode = Literal["learn", "runtime", "runtime_fallback"]
AttemptStatus = Literal["valid", "invalid", "skipped"]
RegressionTag = Literal["stable", "fragile", "changed_behavior", "new_strategy"]

_AMOUNT_TOLERANCE = Decimal("0.01")

# Extended strategy names (backward compatible with existing suppliers.json specs).
STRATEGY_NAMES = (
    "same_line_last_amount",
    "same_line_after_colon",
    "next_line_first_token",
    "next_line_last_amount",
    "same_line_first_amount",
    "same_line_first_iban",
    "next_line_first_iban",
    "derived_excl_plus_vat",
    "factuur_inline_pagina",
    "iban_full_text_scan",
    "last_token_on_line",
    "ocr_tag_extraction",
    "unlabeled_prefix_amount",
    "same_line_value_after_label",
)

LABEL_OPTIONAL_STRATEGIES = frozenset({"derived_excl_plus_vat", "iban_full_text_scan"})

LEARN_ONLY_STRATEGIES = frozenset(
    {
        "token_matching_confirmed_value",
        "token_matching_confirmed_amount",
    }
)

_FACTUUR_INLINE_PAGINA_RE = re.compile(
    r"(?i)\bFactuur\s+([A-Za-z0-9][A-Za-z0-9\-\/]{4,})\s+Pagina\b"
)
_EXCL_BTW_LINE_RE = re.compile(
    r"(?i)\b(?:excl\.?\s*btw|netto\s+goederenbedrag)\b"
)
_INCL_BTW_RE = re.compile(r"(?i)\b(?:incl\.?\s*btw|inclusief\s*btw|te\s+betalen)\b")
_VAT_PERCENT_LINE_RE = re.compile(r"(?i)\bbtw\s*(?:\d{1,2}\s*%|:)")
_OCR_TAG_RE = re.compile(r"\[O\+[^\]]+\]")

FRAGILE_INTERNAL_STRATEGIES = frozenset(
    {
        "fallback_value_locate_minimal_label",
        "amount_fallback_scan",
        "unlabeled_prefix_amount",
        "last_token_on_line",
        "ocr_tag_extraction",
    }
)

_HEAVY_FRAGILE_STRATEGIES = frozenset(
    {
        "fallback_value_locate_minimal_label",
        "unlabeled_prefix_amount",
    }
)

from logic.runtime_paths import bundled_engine_data_path

_ENGINE_BUNDLE_PATH = bundled_engine_data_path("strategy_engine_bundle.json")
_REGRESSION_BASELINE_PATH = bundled_engine_data_path("strategy_regression_baseline.json")
_engine_bundle_cache: dict[str, Any] | None = None
_engine_bundle_version: int | None = None
_strategy_order_cache: dict[str, tuple[str, ...]] | None = None
_semantic_scoring_cache: dict[str, Any] | None = None
_bundle_load_attempted: bool = False

# Default amount penalty/boost constants (evaluation freeze + bundle-disabled runtime).
_DEFAULT_AMOUNT_SCORING: dict[str, float] = {
    "incl_btw_boost": 0.08,
    "payable_label_boost": 0.08,
    "totaal_anchor_boost": 0.0,
    "vat_line_penalty": -0.10,
    "excl_without_payable_penalty": -0.15,
    "multi_amount_penalty": -0.10,
}

# Reserved defaults for non-amount fields (overridable via bundle.semantic_scoring[field_id]).
_DEFAULT_FIELD_SCORING: dict[str, dict[str, float]] = {
    "iban": {
        "multiple_iban_penalty": -0.08,
        "non_sepa_match_penalty": -0.30,
        "single_plausible_boost": 0.05,
    },
    "invoice_number": {},
    "customer_number": {},
}

_FIELD_LABEL_RES: dict[str, re.Pattern[str]] = {
    "amount": _AMOUNT_PROFILE_LABEL_RE,
    "invoice_number": _INVOICE_LABEL_RE,
    "customer_number": _CUSTOMER_LABEL_RE,
    "iban": _IBAN_LABEL_RE,
    "vat_number": re.compile(r"(?i)\b(?:btw(?:-|\s*)nummer|btw|vat)\b"),
    "kvk_number": re.compile(r"(?i)\b(?:kvk|k\.?v\.?k\.?)\b"),
    "invoice_date": re.compile(
        r"(?i)\b(?:factuurdatum|factuur\s*datum|invoice\s*date|date\s*of\s*invoice|datum\s*factuur)\b"
    ),
    "email_domain": re.compile(r"(?i)\b(?:e-?mail|email)\b"),
}


@dataclass
class StrategyContext:
    field_id: FieldId
    raw_text: str
    confirmed_value: Any
    snapshot: dict[str, Any] | None = None
    context_line: str | None = None
    mode: StrategyMode = "learn"
    evaluation_mode: bool = False


@dataclass
class StrategyAttempt:
    strategy: str
    candidate: Any | None = None
    profile_spec: dict[str, Any] | None = None
    confidence: float = 0.0
    confidence_breakdown: dict[str, float] = field(default_factory=dict)
    status: AttemptStatus = "invalid"
    reason: str = ""
    regression_tag: RegressionTag | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "strategy": self.strategy,
            "candidate": self.candidate,
            "profile_spec": self.profile_spec,
            "confidence": self.confidence,
            "confidence_breakdown": dict(self.confidence_breakdown),
            "status": self.status,
            "reason": self.reason,
        }
        if self.regression_tag is not None:
            out["regression_tag"] = self.regression_tag
        return out


@dataclass
class StrategyFieldResult:
    value: Any | None = None
    profile_spec: dict[str, Any] | None = None
    strategy_used: str | None = None
    all_attempted_strategies: list[StrategyAttempt] = field(default_factory=list)
    validation_trace: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "profile_spec": self.profile_spec,
            "strategy_used": self.strategy_used,
            "all_attempted_strategies": [a.to_dict() for a in self.all_attempted_strategies],
            "validation_trace": list(self.validation_trace),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ResolvedAttempt:
    """Immutable winner carrier — single source of truth for one validated attempt."""

    value: Any
    raw_fingerprint: str
    value_key: str
    identity_key: str
    spec: dict[str, Any]
    strategy: str
    confidence: float
    confidence_breakdown: dict[str, float] = field(default_factory=dict)


class AmbiguousEquivalenceError(Exception):
    """Raised when multiple valid attempts share confidence but differ in resolved value."""


class EvaluationDeterminismError(Exception):
    """Raised when evaluation_mode violates frozen registry/scoring contract."""


StrategyFn = Callable[[StrategyContext, list[str]], Optional[StrategyAttempt]]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def split_lines(raw_text: str) -> list[str]:
    return (raw_text or "").split("\n")


def confirmed_amount_decimal(amount: float | Decimal | str | None) -> Decimal | None:
    if amount is None:
        return None
    if isinstance(amount, Decimal):
        return amount.quantize(Decimal("0.01"))
    v = normalize_amount(str(amount))
    if v is None:
        return None
    return Decimal(str(v)).quantize(Decimal("0.01"))


def format_confirmed_amount(amount: float | Decimal) -> str:
    d = confirmed_amount_decimal(amount)
    if d is None:
        return str(amount)
    return f"{d:.2f}"


def format_confirmed_for_spec(field_id: FieldId, value: Any) -> str:
    if field_id == "amount":
        return format_confirmed_amount(value)
    if field_id == "iban":
        return clean_iban(str(value)) or str(value)
    return str(value).strip()


def amount_decimal_matches(a: Decimal | None, b: Decimal | None) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= _AMOUNT_TOLERANCE


def clean_value_token(tok: str) -> str:
    return (tok or "").strip().strip(".,;")


def char_pos_to_line(raw_text: str, pos: int) -> tuple[int, int]:
    line_idx = raw_text[:pos].count("\n")
    return line_idx, pos - (raw_text.rfind("\n", 0, pos) + 1)


def locate_string_position(raw_text: str, value: str) -> tuple[int, int, int, str] | None:
    if not value:
        return None
    text = raw_text or ""
    pos = text.find(value)
    actual = value
    if pos < 0:
        m = re.search(re.escape(value), text, re.IGNORECASE)
        if not m:
            return None
        pos = m.start()
        actual = m.group(0)
    line_idx, pos_in_line = char_pos_to_line(text, pos)
    return pos, line_idx, pos_in_line, actual


def value_in_raw_text(raw_text: str, value: Any, field_id: FieldId) -> bool:
    if value is None:
        return False
    if field_id == "amount":
        target = confirmed_amount_decimal(value)
        if target is None:
            return False
        for m in re.finditer(_AMOUNT_TOKEN, raw_text or ""):
            d = normalize_amount_decimal(m.group(0))
            if d is not None and amount_decimal_matches(d, target):
                return True
        return False
    if field_id == "iban":
        target = clean_iban(str(value))
        if not target:
            return False
        for iban in _scan_sepa_ibans_in_text(raw_text or ""):
            if clean_iban(iban) == target:
                return True
        return target.lower() in (raw_text or "").lower()
    s = str(value).strip()
    if not s:
        return False
    if s in (raw_text or ""):
        return True
    return bool(re.search(re.escape(s), raw_text or "", re.IGNORECASE))


def extend_label_span(line: str, start: int, end: int) -> str:
    tail = line[end:]
    m = re.match(r"\s*:\s*", tail)
    if m:
        end = end + m.end()
    return line[start:end]


def extend_payable_amount_label_span(line: str, start: int, end: int) -> str:
    tail = line[end:]
    m = re.match(r"\s*:\s*", tail)
    if m:
        end = end + m.end()
    m2 = re.match(r"\s*\(\s*(?:incl|excl)\b[^)]*\)", line[end:], re.IGNORECASE)
    if m2:
        end = end + m2.end()
    return line[start:end].strip()


def positive_amounts_on_line(line: str) -> list[Decimal]:
    out: list[Decimal] = []
    for tok in _iter_amount_tokens_excluding_percent(line or ""):
        d = normalize_amount_decimal(tok)
        if d is not None and d > Decimal("0"):
            out.append(d)
    return out


def extract_amount_on_line(line: str, strategy: str) -> float | None:
    decs = positive_amounts_on_line(line)
    if not decs:
        return None
    pick = decs[0] if strategy == "same_line_first_amount" else decs[-1]
    return float(pick)


def extract_value_after_label(line: str, label: str, *, target: str | None = None) -> str | None:
    """Value immediately following ``label`` on the same line (no colon required)."""
    ln = line or ""
    li = ln.lower().find((label or "").lower())
    if li < 0:
        return None
    rest = ln[li + len(label) :].strip()
    if not rest:
        return None
    if target:
        tgt = target.strip()
        if tgt.casefold() in rest.casefold():
            for tok in re.findall(r"[\w\-\/]+", rest):
                if tok.casefold() == tgt.casefold():
                    return tok
            m = re.search(re.escape(tgt), rest, re.IGNORECASE)
            if m:
                return m.group(0)
    tok = rest.split()[0]
    cleaned = clean_value_token(tok)
    return cleaned or None


def _token_matches_confirmed(field_id: FieldId, token: str, target: str) -> bool:
    if not token or not target:
        return False
    if token.strip().casefold() == target.strip().casefold():
        return True
    n_tok = normalize_field_value(field_id, token)
    n_tgt = normalize_field_value(field_id, target)
    return n_tok is not None and n_tgt is not None and n_tok == n_tgt


def _extract_token_matching_target(rest: str, target: str) -> str | None:
    """Return a rest token that matches target (exact or via field normalizers)."""
    tgt = (target or "").strip()
    if not tgt or not (rest or "").strip():
        return None
    date_pat = re.compile(
        r"\b\d{1,4}[\./-]\d{1,2}[\./-]\d{1,4}\b|\b\d{1,2}\s+[A-Za-z]{3,}\.?\s+\d{4}\b"
    )
    candidates: list[str] = []
    for m in date_pat.finditer(rest):
        candidates.append(m.group(0))
    candidates.extend(re.findall(r"[\w@.\-/]+", rest))
    seen: set[str] = set()
    for raw in candidates:
        cleaned = clean_value_token(raw)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        if cleaned.casefold() == tgt.casefold():
            return cleaned
        for field_id in (
            "invoice_date",
            "email_domain",
            "invoice_number",
            "customer_number",
            "vat_number",
        ):
            if _token_matches_confirmed(field_id, cleaned, tgt):
                return cleaned
    return None


def extract_after_colon(
    line: str,
    label: str,
    *,
    target: str | None = None,
) -> str | None:
    ln = line or ""
    colon_idx = ln.find(":")
    if colon_idx >= 0:
        rest = ln[colon_idx + 1 :]
    else:
        li = ln.lower().find((label or "").lower())
        if li < 0:
            return None
        rest = ln[li + len(label) :]
    rest = rest.strip()
    if not rest:
        return None
    m = re.match(r"^(\d{4})\s*/\s*(\d{1,6})(?!\d)", rest)
    if m:
        norm = f"{m.group(1)}/{m.group(2)}"
        if target is None or norm.casefold() == target.strip().casefold():
            return norm
    if target:
        tgt = target.strip()
        if tgt.casefold() in rest.casefold():
            if " / " in tgt or "/" in tgt:
                slash_m = re.search(re.escape(tgt.replace(" ", "")), re.sub(r"\s+", "", rest), re.IGNORECASE)
                if slash_m:
                    return tgt
            for tok in rest.split():
                cleaned = clean_value_token(tok)
                if cleaned.casefold() == tgt.casefold():
                    return cleaned
                if tgt.startswith(cleaned) and "/" in tgt:
                    continue
        slash_parts = re.findall(r"[\w\-]+(?:\s*/\s*[\w\-]+)?", rest)
        for part in slash_parts:
            if part.strip().casefold() == tgt.casefold():
                return part.strip()
        matched = _extract_token_matching_target(rest, tgt)
        if matched:
            return matched
        return None
    tok = rest.split()[0]
    cleaned = clean_value_token(tok)
    return cleaned or None


def first_iban_on_line(line: str) -> str | None:
    ibans = _scan_sepa_ibans_in_text(line or "")
    if not ibans:
        return None
    return clean_iban(ibans[0])


def extract_next_line_first_iban(lines: list[str], label_line_idx: int) -> str | None:
    for j in range(label_line_idx + 1, len(lines)):
        ln = (lines[j] or "").strip()
        if not ln:
            continue
        iban = first_iban_on_line(ln)
        if iban:
            return iban
    return None


def extract_next_line_first_token(lines: list[str], label_line_idx: int) -> str | None:
    for j in range(label_line_idx + 1, len(lines)):
        ln = (lines[j] or "").strip()
        if not ln:
            continue
        tok = ln.split()[0]
        cleaned = clean_value_token(tok)
        return cleaned or None
    return None


def extract_next_line_last_amount(lines: list[str], label_line_idx: int) -> float | None:
    for j in range(label_line_idx + 1, len(lines)):
        ln = (lines[j] or "").strip()
        if not ln:
            continue
        return extract_amount_on_line(ln, "same_line_last_amount")
    return None


def iter_label_line_indices(lines: list[str], label: str) -> list[int]:
    if not label:
        return []
    indices: list[int] = []
    seen: set[int] = set()

    def add(i: int) -> None:
        if i not in seen:
            seen.add(i)
            indices.append(i)

    if re.search(r"[\s:]", label):
        needle = label.lower()
        for i, line in enumerate(lines):
            if needle in (line or "").lower():
                add(i)
        collapsed_needle = collapse_stutter_chars(label).lower()
        if len(collapsed_needle) >= 3:
            for i, line in enumerate(lines):
                if collapsed_needle in collapse_stutter_chars(line).lower():
                    add(i)
        return indices

    pattern = re.compile(
        r"(?<![a-zA-Z])" + re.escape(label) + r"(?![a-zA-Z0-9])",
        re.IGNORECASE,
    )
    for i, line in enumerate(lines):
        if pattern.search(line or ""):
            add(i)
    collapsed_needle = collapse_stutter_chars(label).lower()
    if len(collapsed_needle) >= 3:
        for i, line in enumerate(lines):
            if collapsed_needle in collapse_stutter_chars(line).lower():
                add(i)
    return indices


def _string_target_matches(val: Any, target: str) -> bool:
    if val is None:
        return False
    v = str(val).strip()
    t = str(target).strip()
    if not v or not t:
        return False
    if v.casefold() == t.casefold():
        return True
    for field_id in (
        "invoice_date",
        "email_domain",
        "invoice_number",
        "customer_number",
        "vat_number",
    ):
        if _token_matches_confirmed(field_id, v, t):
            return True
    return False


def find_label_line(
    lines: list[str],
    label: str,
    *,
    strategy: str | None = None,
    amount_target: Decimal | None = None,
    string_target: str | None = None,
) -> int | None:
    indices = iter_label_line_indices(lines, label)
    if not indices:
        return None
    if strategy:
        for idx in indices:
            val = apply_strategy(
                lines,
                idx,
                label,
                strategy,
                target=string_target,
                amount_target=amount_target,
            )
            if val is None:
                continue
            if amount_target is not None:
                ext = confirmed_amount_decimal(val)
                if not amount_decimal_matches(ext, amount_target):
                    continue
            if string_target is not None and strategy in (
                "same_line_after_colon",
                "same_line_value_after_label",
                "last_token_on_line",
            ):
                if not _string_target_matches(val, string_target):
                    continue
            return idx
        return None
    return indices[0]


def apply_strategy(
    lines: list[str],
    label_line_idx: int,
    label: str,
    strategy: str,
    *,
    target: str | None = None,
    amount_target: Decimal | None = None,
) -> float | str | None:
    line = lines[label_line_idx] if label_line_idx < len(lines) else ""
    if strategy == "same_line_last_amount":
        return extract_amount_on_line(line, strategy)
    if strategy == "same_line_first_amount":
        return extract_amount_on_line(line, strategy)
    if strategy == "same_line_value_after_label":
        return extract_value_after_label(line, label, target=target)
    if strategy == "same_line_after_colon":
        tgt = target
        if tgt is None and amount_target is not None:
            tgt = str(amount_target)
        return extract_after_colon(line, label, target=tgt)
    if strategy == "next_line_first_token":
        return extract_next_line_first_token(lines, label_line_idx)
    if strategy == "next_line_last_amount":
        return extract_next_line_last_amount(lines, label_line_idx)
    if strategy == "same_line_first_iban":
        return first_iban_on_line(line)
    if strategy == "next_line_first_iban":
        return extract_next_line_first_iban(lines, label_line_idx)
    if strategy == "factuur_inline_pagina":
        m = _FACTUUR_INLINE_PAGINA_RE.search(line or "")
        return m.group(1).strip() if m else None
    if strategy == "last_token_on_line":
        toks = (line or "").split()
        if not toks:
            return None
        cleaned = clean_value_token(toks[-1])
        return cleaned or None
    if strategy == "ocr_tag_extraction":
        m = _OCR_TAG_RE.search(line or "")
        if not m:
            return None
        inner = m.group(0).strip("[]")
        parts = inner.split("+", 1)
        if len(parts) > 1:
            return clean_value_token(parts[1])
        return None
    if strategy == "unlabeled_prefix_amount":
        return extract_amount_on_line(line, "same_line_last_amount")
    if strategy == "iban_full_text_scan":
        ibans = _scan_sepa_ibans_in_text("\n".join(lines))
        if not ibans or not target:
            return None
        tgt = clean_iban(target)
        for ib in ibans:
            if clean_iban(ib) == tgt:
                return tgt
        return None
    return None


def extract_derived_excl_plus_vat(
    lines: list[str],
    label_excl: str,
    label_btw: str,
) -> Decimal | None:
    excl_val: Decimal | None = None
    vat_val: Decimal | None = None
    for ln in lines:
        if _EXCL_BTW_LINE_RE.search(ln or "") and label_excl.lower() in (ln or "").lower():
            toks = positive_amounts_on_line(ln)
            if toks:
                excl_val = toks[-1]
        if (
            excl_val is not None
            and _VAT_PERCENT_LINE_RE.search(ln or "")
            and label_btw.lower() in (ln or "").lower()
        ):
            toks = positive_amounts_on_line(ln)
            if toks:
                vat_val = toks[-1]
    if excl_val is None or vat_val is None:
        return None
    return (excl_val + vat_val).quantize(Decimal("0.01"))


def values_match(field: str, extracted: float | str | None, expected: Any) -> bool:
    if extracted is None:
        return False
    if field == "amount":
        exp_d = confirmed_amount_decimal(expected)
        ext_d = confirmed_amount_decimal(extracted)
        if exp_d is None or ext_d is None:
            return False
        return amount_decimal_matches(ext_d, exp_d)
    if field == "iban":
        return clean_iban(str(extracted)) == clean_iban(str(expected))
    return str(extracted).strip() == str(expected).strip()


def extracted_values_equal(field: str, left: Any, right: Any) -> bool:
    """Compare two resolved extraction values (eval vs runtime parity)."""
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return values_match(field, left, right)


def normalize_extracted(field_id: FieldId, val: Any) -> Any | None:
    if val is None:
        return None
    if field_id == "amount":
        dec = normalize_amount_decimal(str(val))
        return float(dec) if dec is not None else None
    if field_id == "iban":
        return clean_iban(str(val)) or None
    if field_id in ("vat_number", "kvk_number", "invoice_date", "email_domain"):
        return normalize_field_value(field_id, val)  # type: ignore[arg-type]
    return str(val).strip() if val else None


def is_valid_field_spec(spec: dict[str, Any], field_id: FieldId) -> bool:
    strategy = spec.get("strategy")
    if not strategy or strategy not in STRATEGY_NAMES:
        return False
    if strategy == "derived_excl_plus_vat":
        return field_id == "amount" and bool(spec.get("label_excl")) and bool(spec.get("label_btw"))
    if strategy in LABEL_OPTIONAL_STRATEGIES:
        return True
    return bool(spec.get("label"))


def execute_spec(raw_text: str, field_id: FieldId, spec: dict[str, Any]) -> Any | None:
    """Runtime execution of one persisted field spec."""
    if not is_valid_field_spec(spec, field_id):
        return None
    lines = split_lines(raw_text)
    strategy_s = str(spec["strategy"])
    confirmed = spec.get("confirmed_value")
    string_target = str(confirmed).strip() if confirmed is not None and field_id != "amount" else None
    amount_target = confirmed_amount_decimal(confirmed) if field_id == "amount" else None

    if strategy_s == "derived_excl_plus_vat":
        derived = extract_derived_excl_plus_vat(
            lines,
            str(spec["label_excl"]),
            str(spec["label_btw"]),
        )
        return normalize_extracted(field_id, float(derived) if derived is not None else None)

    if strategy_s == "iban_full_text_scan":
        val = apply_strategy(lines, 0, "", strategy_s, target=string_target)
        return normalize_extracted(field_id, val)

    label = str(spec.get("label") or "")
    idx = find_label_line(
        lines,
        label,
        strategy=strategy_s,
        amount_target=amount_target,
        string_target=string_target,
    )
    if idx is None:
        return None
    val = apply_strategy(
        lines,
        idx,
        label,
        strategy_s,
        target=string_target,
        amount_target=amount_target,
    )
    return normalize_extracted(field_id, val)


def canonical_raw_repr(field_id: FieldId, raw_value: Any) -> str:
    """Stable repr of execute_spec output before normalization transforms."""
    if raw_value is None:
        return ""
    if field_id == "amount":
        dec = confirmed_amount_decimal(raw_value)
        val_repr = repr(dec) if dec is not None else repr(raw_value)
        return f"{type(raw_value).__name__}:{val_repr}"
    if field_id == "iban":
        return clean_iban(str(raw_value)) or str(raw_value)
    return repr(str(raw_value).strip())


def normalized_fingerprint(field_id: FieldId, raw_value: Any) -> str:
    """Normalized fingerprint used for value comparison and tie-break."""
    if raw_value is None:
        return ""
    if field_id == "amount":
        dec = confirmed_amount_decimal(raw_value)
        if dec is not None:
            return str(dec.quantize(Decimal("0.01")))
        return str(raw_value)
    if field_id == "iban":
        ib = clean_iban(str(raw_value))
        return ib or str(raw_value).strip()
    return str(raw_value).strip().casefold()


def compute_value_fingerprints(field_id: FieldId, raw_value: Any) -> tuple[str, str, str]:
    raw_fp = canonical_raw_repr(field_id, raw_value)
    value_key = normalized_fingerprint(field_id, raw_value)
    identity_key = hashlib.sha256(f"{raw_fp}|{value_key}".encode("utf-8")).hexdigest()
    return raw_fp, value_key, identity_key


def _resolve_attempt(
    raw_text: str,
    field_id: FieldId,
    spec: dict[str, Any],
    confirmed_value: Any,
    *,
    strategy: str,
    confidence: float,
    confidence_breakdown: dict[str, float] | None = None,
) -> ResolvedAttempt | None:
    """Single truth path: structure gate → one execute_spec → values_match → ResolvedAttempt."""
    spec_copy = copy.deepcopy(spec)
    if not is_valid_field_spec(spec_copy, field_id):
        return None
    extracted = execute_spec(raw_text, field_id, spec_copy)
    if not values_match(field_id, extracted, confirmed_value):
        return None
    raw_fp, value_key, identity_key = compute_value_fingerprints(field_id, extracted)
    return ResolvedAttempt(
        value=extracted,
        raw_fingerprint=raw_fp,
        value_key=value_key,
        identity_key=identity_key,
        spec=spec_copy,
        strategy=strategy,
        confidence=confidence,
        confidence_breakdown=dict(confidence_breakdown or {}),
    )


def assert_no_ambiguous_equivalence(
    valid: list[ResolvedAttempt],
    winner: ResolvedAttempt,
) -> None:
    """Hard fail when peers at winner confidence share value_key but differ in raw repr."""
    peers = [a for a in valid if a.confidence == winner.confidence]
    value_keys = {a.value_key for a in peers}
    if len(value_keys) != 1:
        return
    raw_fps = {a.raw_fingerprint for a in peers}
    if len(raw_fps) > 1:
        raise AmbiguousEquivalenceError(
            f"ambiguous equivalence at confidence={winner.confidence}: "
            f"value_key={next(iter(value_keys))} raw_fps={sorted(raw_fps)}"
        )


def _select_winner(
    valid: list[ResolvedAttempt],
    ctx: StrategyContext,
    pipeline_index: dict[str, int],
) -> ResolvedAttempt:
    if ctx.evaluation_mode:
        winner = max(
            valid,
            key=lambda a: (a.confidence, a.identity_key, a.strategy),
        )
    else:
        winner = max(
            valid,
            key=lambda a: (
                a.confidence,
                a.identity_key,
                -pipeline_index.get(a.strategy, 999),
            ),
        )
    assert_no_ambiguous_equivalence(valid, winner)
    return winner


def validate_field_spec(
    raw_text: str,
    field_id: FieldId,
    spec: dict[str, Any],
    confirmed_value: Any,
) -> bool:
    strategy = str(spec.get("strategy") or "")
    return (
        _resolve_attempt(
            raw_text,
            field_id,
            spec,
            confirmed_value,
            strategy=strategy,
            confidence=0.0,
        )
        is not None
    )


def _generic_colon_label(line: str, value_pos: int) -> str | None:
    ln = line or ""
    colon_idx = ln.find(":")
    if colon_idx < 0 or value_pos <= colon_idx:
        return None
    label = ln[: colon_idx + 1].strip()
    if len(label) < 2:
        return None
    return label if not label.endswith(":") else ln[: colon_idx + 1].rstrip() + " :"


def _label_candidates_on_line(line: str, field: str) -> list[tuple[str, int, int]]:
    rx = _FIELD_LABEL_RES.get(field)
    if rx is None:
        return []
    out: list[tuple[str, int, int]] = []
    for m in rx.finditer(line or ""):
        if field == "amount":
            label = extend_payable_amount_label_span(line, m.start(), m.end())
        else:
            label = extend_label_span(line, m.start(), m.end())
        out.append((label, m.start(), m.end()))
    return out


def _line_matches_context(line: str, context: str) -> bool:
    ln = re.sub(r"\s+", " ", (line or "").strip())
    ctx = re.sub(r"\s+", " ", (context or "").strip())
    if not ln or not ctx:
        return False
    if ln in ctx or ctx in ln:
        return True
    cln = collapse_stutter_chars(ln)
    cctx = collapse_stutter_chars(ctx)
    if cln and cctx and (cln in cctx or cctx in cln):
        return True
    head = ctx.split(" >> ", 1)[0].strip()
    if head and (head in ln or ln in head):
        return True
    chead = collapse_stutter_chars(head)
    return bool(chead and (chead in cln or cln in chead))


def _line_eligible_for_amount(line: str, target: Decimal | None = None) -> bool:
    if not (line or "").strip():
        return False
    has_payable_label = bool(_AMOUNT_PROFILE_LABEL_RE.search(line))
    if target is not None:
        decs = positive_amounts_on_line(line)
        if decs and any(amount_decimal_matches(d, target) for d in decs):
            if has_payable_label:
                return True
            return not _TOTAL_LINE_EXCLUDE_RE.search(line)
    if _TOTAL_LINE_EXCLUDE_RE.search(line):
        return has_payable_label
    return has_payable_label or bool(_TOTAL_LINE_HINT_RE.search(line))


def _iban_candidates_scored(raw_text: str) -> list[tuple[str, float]]:
    """Scan text for IBANs with checksum-based quality scores."""
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for ib in _scan_sepa_ibans_in_text(raw_text or ""):
        cleaned = clean_iban(ib)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        score = 0.5
        if is_plausible_iban(cleaned):
            score = 1.0
        out.append((cleaned, score))
    return out


def _amount_candidate_context(raw_text: str, line: str, pos_in_line: int) -> dict[str, bool]:
    """Semantic cues near an amount token (±120 chars)."""
    text = raw_text or ""
    line_start = text.find(line) if line else -1
    if line_start < 0:
        abs_pos = max(0, pos_in_line)
    else:
        abs_pos = line_start + pos_in_line
    window = text[max(0, abs_pos - 120) : abs_pos + 120]
    ln = line or ""
    return {
        "incl_btw": bool(_INCL_BTW_RE.search(window) or _TOTAL_LINE_HINT_RE.search(ln)),
        "excl_btw": bool(_EXCL_BTW_LINE_RE.search(window) or _EXCL_BTW_LINE_RE.search(ln)),
        "vat_line": bool(_VAT_PERCENT_LINE_RE.search(ln)),
        "payable_label": bool(_AMOUNT_PROFILE_LABEL_RE.search(ln)),
    }


def _count_value_occurrences(field_id: FieldId, raw_text: str, confirmed: Any) -> int:
    text = raw_text or ""
    if field_id == "amount":
        target = confirmed_amount_decimal(confirmed)
        if target is None:
            return 0
        count = 0
        for m in re.finditer(_AMOUNT_TOKEN, text):
            d = normalize_amount_decimal(m.group(0))
            if d is not None and amount_decimal_matches(d, target):
                count += 1
        return count
    if field_id == "iban":
        target = clean_iban(str(confirmed))
        if not target:
            return 0
        count = 0
        for ib in _scan_sepa_ibans_in_text(text):
            if clean_iban(ib) == target:
                count += 1
        if count == 0 and target.lower() in text.lower():
            return 1
        return count
    val_s = str(confirmed).strip()
    if not val_s:
        return 0
    count = len(re.findall(re.escape(val_s), text, re.IGNORECASE))
    if count == 0 and val_s.casefold() in text.casefold():
        return 1
    return count


def _locate_candidate_position(
    field_id: FieldId,
    raw_text: str,
    candidate: Any,
    spec: dict[str, Any],
) -> tuple[int, int, int] | None:
    if field_id == "amount":
        target = confirmed_amount_decimal(candidate if candidate is not None else spec.get("confirmed_value"))
        if target is None:
            return None
        for m in re.finditer(_AMOUNT_TOKEN, raw_text or ""):
            d = normalize_amount_decimal(m.group(0))
            if d is not None and amount_decimal_matches(d, target):
                line_idx, pos_in_line = char_pos_to_line(raw_text, m.start())
                return m.start(), line_idx, pos_in_line
        return None
    if field_id == "iban":
        target = clean_iban(str(candidate or spec.get("confirmed_value") or ""))
        if not target:
            return None
        pos = (raw_text or "").upper().find(target)
        if pos < 0:
            return None
        line_idx, pos_in_line = char_pos_to_line(raw_text, pos)
        return pos, line_idx, pos_in_line
    val_s = str(candidate or spec.get("confirmed_value") or "").strip()
    located = locate_string_position(raw_text, val_s)
    if located is None:
        return None
    pos, line_idx, pos_in_line, _actual = located
    return pos, line_idx, pos_in_line


def _score_label_match(
    field_id: FieldId,
    spec: dict[str, Any],
    lines: list[str],
    line_idx: int,
    *,
    internal_strategy: str = "",
) -> float:
    persisted = str(spec.get("strategy") or "")
    if persisted in LABEL_OPTIONAL_STRATEGIES:
        if persisted == "derived_excl_plus_vat":
            excl = str(spec.get("label_excl") or "")
            btw = str(spec.get("label_btw") or "")
            if excl and btw:
                return 0.3
        if persisted == "iban_full_text_scan" and internal_strategy in (
            "iban_full_text_scan",
            "iban_scan_with_checksum_filter",
        ):
            return 0.28
        return 0.15
    label = str(spec.get("label") or "")
    if not label:
        return 0.0
    line = lines[line_idx] if 0 <= line_idx < len(lines) else ""
    if not line:
        return 0.0
    score = 0.0
    rx = _FIELD_LABEL_RES.get(field_id)
    if rx and rx.search(line):
        for label_text, _s, _e in _label_candidates_on_line(line, field_id):
            if (
                label_text.casefold() in label.casefold()
                or label.casefold() in label_text.casefold()
            ):
                score = 0.3
                break
    if score == 0.0 and label.casefold() in (line or "").casefold():
        score = 0.18
    if internal_strategy in _HEAVY_FRAGILE_STRATEGIES:
        score = min(score, 0.12)
    elif internal_strategy in FRAGILE_INTERNAL_STRATEGIES:
        score = min(score, 0.2)
    elif internal_strategy in (
        "token_matching_confirmed_value",
        "token_matching_confirmed_amount",
    ):
        score = max(score, 0.28 if score >= 0.18 else score)
    return score


def _score_proximity(
    field_id: FieldId,
    spec: dict[str, Any],
    lines: list[str],
    line_idx: int,
    pos_in_line: int,
) -> float:
    label = str(spec.get("label") or "")
    if not label:
        if str(spec.get("strategy") or "") in LABEL_OPTIONAL_STRATEGIES:
            return 0.18
        return 0.05
    line = lines[line_idx] if 0 <= line_idx < len(lines) else ""
    if not line:
        return 0.0
    label_pos = line.lower().find(label.lower())
    if label_pos < 0:
        label_end = 0
        for _label_text, start, end in _label_candidates_on_line(line, field_id):
            label_end = end
            break
        dist = max(0, pos_in_line - label_end)
    else:
        dist = max(0, pos_in_line - (label_pos + len(label)))
    return round(0.3 * (1.0 - min(dist, 120) / 120.0), 4)


def _score_uniqueness(field_id: FieldId, raw_text: str, confirmed: Any) -> float:
    count = _count_value_occurrences(field_id, raw_text, confirmed)
    if count <= 1:
        return 0.2
    if count == 2:
        return 0.15
    return 0.05


def _score_format(field_id: FieldId, candidate: Any, confirmed: Any) -> float:
    if field_id == "amount":
        ext = confirmed_amount_decimal(candidate)
        exp = confirmed_amount_decimal(confirmed)
        if ext is not None and exp is not None and amount_decimal_matches(ext, exp):
            return 0.2
        return 0.0
    if field_id == "iban":
        ib = clean_iban(str(candidate or confirmed or ""))
        if ib and is_plausible_iban(ib):
            return 0.2
        if ib and len(ib) >= 15:
            return 0.08
        return 0.0
    val = str(candidate or confirmed or "").strip()
    if val and re.match(r"^[\w\-\/\.]+$", val):
        return 0.2
    return 0.0


def _score_penalty(
    field_id: FieldId,
    internal_strategy: str,
    spec: dict[str, Any],
    raw_text: str,
    lines: list[str],
    line_idx: int,
    pos_in_line: int,
    confirmed: Any,
    *,
    evaluation_mode: bool = False,
) -> float:
    penalty = 0.0
    line = lines[line_idx] if 0 <= line_idx < len(lines) else ""

    if field_id == "amount":
        amt = _amount_scoring_constants(evaluation_mode)
        ctx = _amount_candidate_context(raw_text, line, pos_in_line)
        decs = positive_amounts_on_line(line)
        if len(decs) >= 2:
            penalty += float(amt["multi_amount_penalty"])
        if ctx["vat_line"]:
            penalty += float(amt["vat_line_penalty"])
        if (
            ctx["excl_btw"]
            and not ctx["payable_label"]
            and _EXCL_BTW_LINE_RE.search(line)
            and str(spec.get("strategy") or "") != "derived_excl_plus_vat"
        ):
            penalty += float(amt["excl_without_payable_penalty"])
        elif ctx["incl_btw"]:
            penalty += float(amt["incl_btw_boost"])
        elif ctx["payable_label"]:
            penalty += float(amt["payable_label_boost"])
        if _TOTAL_LINE_HINT_RE.search(line) and not ctx["excl_btw"]:
            penalty += float(amt["totaal_anchor_boost"])

    if field_id == "iban":
        iban_adj = _field_scoring_constants("iban", evaluation_mode)
        ibans = _iban_candidates_scored(raw_text)
        if len(ibans) >= 2:
            penalty += float(iban_adj.get("multiple_iban_penalty", -0.08))
        target = clean_iban(str(confirmed or ""))
        if target and target.lower() in (raw_text or "").lower():
            exact = any(clean_iban(ib) == target for ib in _scan_sepa_ibans_in_text(raw_text or ""))
            if not exact:
                penalty += float(iban_adj.get("non_sepa_match_penalty", -0.30))
        elif target and is_plausible_iban(target) and len(ibans) == 1:
            penalty += float(iban_adj.get("single_plausible_boost", 0.05))

    if internal_strategy in _HEAVY_FRAGILE_STRATEGIES:
        penalty -= 0.28
    elif internal_strategy in FRAGILE_INTERNAL_STRATEGIES:
        penalty -= 0.15

    return round(max(penalty, -0.35), 4)


def strategy_confidence(
    ctx: StrategyContext,
    spec: dict[str, Any],
    candidate: Any,
    lines: list[str],
    *,
    internal_strategy: str,
) -> tuple[float, dict[str, float]]:
    """Score a validated strategy attempt (0.0–1.0) with component breakdown."""
    confirmed = normalize_field_value(ctx.field_id, ctx.confirmed_value)
    located = _locate_candidate_position(ctx.field_id, ctx.raw_text, candidate, spec)
    line_idx = located[1] if located else 0
    pos_in_line = located[2] if located else 0

    breakdown = {
        "label_match": _score_label_match(
            ctx.field_id, spec, lines, line_idx, internal_strategy=internal_strategy
        ),
        "proximity": _score_proximity(ctx.field_id, spec, lines, line_idx, pos_in_line)
        if located
        else (0.18 if str(spec.get("strategy") or "") in LABEL_OPTIONAL_STRATEGIES else 0.0),
        "uniqueness": _score_uniqueness(ctx.field_id, ctx.raw_text, confirmed),
        "format": _score_format(ctx.field_id, candidate, confirmed),
        "penalty": _score_penalty(
            ctx.field_id,
            internal_strategy,
            spec,
            ctx.raw_text,
            lines,
            line_idx,
            pos_in_line,
            confirmed,
            evaluation_mode=ctx.evaluation_mode,
        ),
    }
    total = sum(breakdown.values())
    if internal_strategy == "fallback_value_locate_minimal_label":
        total = min(total, 0.55)
    total = max(0.0, min(1.0, round(total, 4)))
    return total, breakdown


def _attempt(
    strategy: str,
    spec: dict[str, Any] | None,
    *,
    candidate: Any | None = None,
    reason: str = "",
) -> StrategyAttempt:
    return StrategyAttempt(
        strategy=strategy,
        candidate=candidate,
        profile_spec=spec,
        reason=reason or ("no_spec" if spec is None else ""),
    )


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------


def _strategy_token_matching_confirmed_value(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    val_s = format_confirmed_for_spec(ctx.field_id, ctx.confirmed_value)
    located = locate_string_position(ctx.raw_text, val_s)
    if located is None:
        compact = re.sub(r"\s+", "", val_s)
        if compact and compact != val_s:
            located = locate_string_position(ctx.raw_text, compact)
    if located is None:
        return _attempt("token_matching_confirmed_value", None, reason="value_not_in_text")
    _pos, line_idx, pos_in_line, actual = located
    line = lines[line_idx] if line_idx < len(lines) else ""

    prefix = (line[:pos_in_line] or "").strip()
    if prefix and line_idx == line_idx:
        label = collapse_stutter_chars(prefix)
        if len(label) >= 2:
            strategy = "same_line_after_colon" if ":" in prefix else "same_line_value_after_label"
            spec = {
                "label": label,
                "strategy": strategy,
                "confirmed_value": actual,
            }
            return _attempt("token_matching_confirmed_value", spec, candidate=actual)

    colon_label = _generic_colon_label(line, pos_in_line)
    if colon_label:
        spec = {
            "label": colon_label,
            "strategy": "same_line_after_colon",
            "confirmed_value": actual,
        }
        return _attempt("token_matching_confirmed_value", spec, candidate=actual)

    if line_idx > 0:
        prev = lines[line_idx - 1] or ""
        if ":" in prev or prev.strip():
            label = prev.split(":")[0].strip()
            if label and len(label) >= 2:
                spec = {
                    "label": label if ":" not in prev else prev[: prev.find(":") + 1].strip(),
                    "strategy": "next_line_first_token",
                    "confirmed_value": actual,
                }
                return _attempt("token_matching_confirmed_value", spec, candidate=actual)

    for label_text, _s, label_end in _label_candidates_on_line(line, ctx.field_id):
        if pos_in_line >= label_end:
            spec = {
                "label": label_text,
                "strategy": "same_line_after_colon",
                "confirmed_value": actual,
            }
            return _attempt("token_matching_confirmed_value", spec, candidate=actual)

    return _attempt("token_matching_confirmed_value", None, reason="no_label_near_value")


def _strategy_same_line_value_after_label(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    val_s = format_confirmed_for_spec(ctx.field_id, ctx.confirmed_value)
    located = locate_string_position(ctx.raw_text, val_s)
    if located is None:
        return _attempt("same_line_value_after_label", None, reason="value_not_in_text")
    _pos, line_idx, pos_in_line, actual = located
    line = lines[line_idx] if line_idx < len(lines) else ""
    prefix = (line[:pos_in_line] or "").strip()
    if not prefix:
        return _attempt("same_line_value_after_label", None, reason="no_label_prefix")
    label = collapse_stutter_chars(prefix)
    spec = {
        "label": label,
        "strategy": "same_line_value_after_label",
        "confirmed_value": actual,
    }
    return _attempt("same_line_value_after_label", spec, candidate=actual)


def _strategy_factuur_inline_pagina(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    if ctx.field_id != "invoice_number":
        return None
    target = format_confirmed_for_spec(ctx.field_id, ctx.confirmed_value)
    for line in lines:
        m = _FACTUUR_INLINE_PAGINA_RE.search(line or "")
        if m and m.group(1).strip().casefold() == target.casefold():
            spec = {
                "label": "Factuur",
                "strategy": "factuur_inline_pagina",
                "confirmed_value": m.group(1).strip(),
            }
            return _attempt("factuur_inline_pagina", spec, candidate=m.group(1).strip())
    return _attempt("factuur_inline_pagina", None, reason="pattern_not_found")


def _strategy_generic_label_same_line_after_colon(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    val_s = format_confirmed_for_spec(ctx.field_id, ctx.confirmed_value)
    located = locate_string_position(ctx.raw_text, val_s)
    if located is None:
        return _attempt("generic_label_same_line_after_colon", None, reason="value_not_in_text")
    _pos, line_idx, pos_in_line, actual = located
    line = lines[line_idx] if line_idx < len(lines) else ""
    colon_label = _generic_colon_label(line, pos_in_line)
    if not colon_label:
        for label_text, _s, label_end in _label_candidates_on_line(line, ctx.field_id):
            if pos_in_line >= label_end:
                colon_label = label_text
                break
    if not colon_label:
        return _attempt("generic_label_same_line_after_colon", None, reason="no_colon_label")
    spec = {
        "label": colon_label,
        "strategy": "same_line_after_colon",
        "confirmed_value": actual,
    }
    return _attempt("generic_label_same_line_after_colon", spec, candidate=actual)


def _strategy_label_then_next_line(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    val_s = format_confirmed_for_spec(ctx.field_id, ctx.confirmed_value)
    located = locate_string_position(ctx.raw_text, val_s)
    if located is None:
        return _attempt("label_then_next_line", None, reason="value_not_in_text")
    _pos, line_idx, _pos_in_line, actual = located
    if line_idx < 1:
        return _attempt("label_then_next_line", None, reason="no_line_above")
    prev = lines[line_idx - 1] or ""
    label = prev.split(":")[0].strip() if ":" in prev else prev.strip()
    if not label or len(label) < 2:
        for label_text, _s, _e in _label_candidates_on_line(prev, ctx.field_id):
            label = label_text
            break
    if not label:
        return _attempt("label_then_next_line", None, reason="no_label")
    spec = {
        "label": label if ":" not in prev else prev[: prev.find(":") + 1].strip(),
        "strategy": "next_line_first_token",
        "confirmed_value": actual,
    }
    return _attempt("label_then_next_line", spec, candidate=actual)


def _strategy_last_token_on_line(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    val_s = format_confirmed_for_spec(ctx.field_id, ctx.confirmed_value)
    located = locate_string_position(ctx.raw_text, val_s)
    if located is None:
        return _attempt("last_token_on_line", None, reason="value_not_in_text")
    _pos, line_idx, pos_in_line, actual = located
    line = lines[line_idx] if line_idx < len(lines) else ""
    colon_label = _generic_colon_label(line, pos_in_line)
    if not colon_label:
        prefix = line[:pos_in_line].strip()
        if len(prefix) >= 3:
            colon_label = collapse_stutter_chars(prefix)
    if not colon_label:
        return _attempt("last_token_on_line", None, reason="no_label")
    spec = {
        "label": colon_label,
        "strategy": "last_token_on_line",
        "confirmed_value": actual,
    }
    return _attempt("last_token_on_line", spec, candidate=actual)


def _strategy_slash_compound_split(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    val_s = format_confirmed_for_spec(ctx.field_id, ctx.confirmed_value)
    if " / " not in val_s and "/" not in val_s:
        return StrategyAttempt(strategy="slash_compound_split", status="skipped", reason="not_slash_value")
    located = locate_string_position(ctx.raw_text, val_s.split("/")[0].strip())
    if located is None:
        return _attempt("slash_compound_split", None, reason="value_not_in_text")
    _pos, line_idx, pos_in_line, _actual = located
    line = lines[line_idx] if line_idx < len(lines) else ""
    colon_label = _generic_colon_label(line, pos_in_line)
    if not colon_label:
        for label_text, _s, label_end in _label_candidates_on_line(line, ctx.field_id):
            if pos_in_line >= label_end:
                colon_label = label_text
                break
    if not colon_label:
        return _attempt("slash_compound_split", None, reason="no_label")
    spec = {
        "label": colon_label,
        "strategy": "same_line_after_colon",
        "confirmed_value": val_s,
    }
    return _attempt("slash_compound_split", spec, candidate=val_s)


def _strategy_ocr_tag_extraction(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    val_s = format_confirmed_for_spec(ctx.field_id, ctx.confirmed_value)
    for i, line in enumerate(lines):
        if val_s.casefold() not in (line or "").casefold() and not _OCR_TAG_RE.search(line or ""):
            continue
        m = _OCR_TAG_RE.search(line or "")
        if m and val_s.casefold() in (line or "").casefold():
            label = (line or "")[: m.start()].strip() or "OCR"
            spec = {
                "label": label,
                "strategy": "ocr_tag_extraction",
                "confirmed_value": val_s,
            }
            return _attempt("ocr_tag_extraction", spec, candidate=val_s)
    return _attempt("ocr_tag_extraction", None, reason="ocr_tag_not_found")


def _strategy_fallback_value_locate_minimal_label(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    val_s = format_confirmed_for_spec(ctx.field_id, ctx.confirmed_value)
    located = locate_string_position(ctx.raw_text, val_s)
    if located is None:
        return _attempt("fallback_value_locate_minimal_label", None, reason="value_not_in_text")
    _pos, line_idx, pos_in_line, actual = located
    line = lines[line_idx] if line_idx < len(lines) else ""
    prefix = (line[:pos_in_line] or "").strip()
    label = collapse_stutter_chars(prefix)
    if len(label) < 2:
        if line_idx > 0:
            prev = (lines[line_idx - 1] or "").strip()
            label = prev.split(":")[0].strip() if prev else ""
    if len(label) < 2:
        return _attempt("fallback_value_locate_minimal_label", None, reason="no_minimal_label")
    spec = {
        "label": label,
        "strategy": "same_line_after_colon" if ":" in line else "next_line_first_token",
        "confirmed_value": actual,
    }
    if spec["strategy"] == "next_line_first_token" and line_idx > 0:
        prev = lines[line_idx - 1] or ""
        spec["label"] = prev.split(":")[0].strip() if ":" in prev else prev.strip()
    return _attempt("fallback_value_locate_minimal_label", spec, candidate=actual)


def _strategy_token_matching_confirmed_amount(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    target = confirmed_amount_decimal(ctx.confirmed_value)
    if target is None:
        return _attempt("token_matching_confirmed_amount", None, reason="invalid_amount")
    for i, line in enumerate(lines):
        if not _line_eligible_for_amount(line, target):
            continue
        for m in re.finditer(_AMOUNT_TOKEN, line or ""):
            d = normalize_amount_decimal(m.group(0))
            if d is None or not amount_decimal_matches(d, target):
                continue
            pos_in_line = m.start()
            colon_label = _generic_colon_label(line, pos_in_line)
            if colon_label:
                decs = positive_amounts_on_line(line)
                strategy = "same_line_last_amount"
                if len(decs) >= 2 and amount_decimal_matches(decs[0], target):
                    strategy = "same_line_first_amount"
                spec = {
                    "label": colon_label,
                    "strategy": strategy,
                    "confirmed_value": format_confirmed_amount(ctx.confirmed_value),
                }
                return _attempt("token_matching_confirmed_amount", spec, candidate=float(target))
            for label_text, _s, label_end in _label_candidates_on_line(line, "amount"):
                if pos_in_line >= label_end:
                    decs = positive_amounts_on_line(line)
                    strategy = "same_line_last_amount"
                    if len(decs) >= 2 and amount_decimal_matches(decs[0], target):
                        strategy = "same_line_first_amount"
                    spec = {
                        "label": label_text,
                        "strategy": strategy,
                        "confirmed_value": format_confirmed_amount(ctx.confirmed_value),
                    }
                    return _attempt("token_matching_confirmed_amount", spec, candidate=float(target))
    return _attempt("token_matching_confirmed_amount", None, reason="amount_not_on_labeled_line")


def _strategy_amount_label_next_line(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    target = confirmed_amount_decimal(ctx.confirmed_value)
    if target is None:
        return None
    for i, line in enumerate(lines):
        if i + 1 >= len(lines):
            continue
        next_ln = lines[i + 1] or ""
        decs = positive_amounts_on_line(next_ln)
        if not any(amount_decimal_matches(d, target) for d in decs):
            continue
        label_text: str | None = None
        m = _AMOUNT_PROFILE_LABEL_RE.search(line or "")
        if m:
            label_text = extend_payable_amount_label_span(line, m.start(), m.end())
        if not label_text or len(label_text) < 2:
            continue
        strategy = "next_line_last_amount"
        if decs and amount_decimal_matches(decs[0], target) and len(decs) == 1:
            strategy = "next_line_first_token"
        elif decs and amount_decimal_matches(decs[-1], target):
            strategy = "next_line_last_amount"
        elif decs and amount_decimal_matches(decs[0], target):
            strategy = "next_line_first_token"
        spec = {
            "label": label_text,
            "strategy": strategy,
            "confirmed_value": format_confirmed_amount(ctx.confirmed_value),
        }
        return _attempt("amount_label_next_line", spec, candidate=float(target))
    return _attempt("amount_label_next_line", None, reason="no_label_next_line")


def _strategy_amount_derived_excl_vat(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    target = confirmed_amount_decimal(ctx.confirmed_value)
    if target is None:
        return None
    excl_label: str | None = None
    excl_val: Decimal | None = None
    vat_label: str | None = None
    vat_val: Decimal | None = None
    for ln in lines:
        m_excl = _EXCL_BTW_LINE_RE.search(ln or "")
        if m_excl:
            toks = positive_amounts_on_line(ln)
            if toks:
                excl_val = toks[-1]
                excl_label = extend_label_span(ln, m_excl.start(), m_excl.end()).strip()
        if excl_val is not None:
            m_vat = _VAT_PERCENT_LINE_RE.search(ln or "")
            if m_vat:
                toks = positive_amounts_on_line(ln)
                if toks:
                    vat_val = toks[-1]
                    vat_label = extend_label_span(ln, m_vat.start(), m_vat.end()).strip()
    if excl_val is None or vat_val is None or not excl_label or not vat_label:
        return _attempt("derived_excl_plus_vat", None, reason="derived_components_missing")
    derived = (excl_val + vat_val).quantize(Decimal("0.01"))
    if not amount_decimal_matches(derived, target):
        return _attempt("derived_excl_plus_vat", None, reason="derived_mismatch")
    spec = {
        "strategy": "derived_excl_plus_vat",
        "label_excl": excl_label,
        "label_btw": vat_label,
        "confirmed_value": format_confirmed_amount(ctx.confirmed_value),
    }
    return _attempt("derived_excl_plus_vat", spec, candidate=float(derived))


def _strategy_unlabeled_prefix_amount(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    target = confirmed_amount_decimal(ctx.confirmed_value)
    if target is None:
        return None
    for i, line in enumerate(lines):
        if not _line_eligible_for_amount(line, target):
            continue
        for m in re.finditer(_AMOUNT_TOKEN, line or ""):
            d = normalize_amount_decimal(m.group(0))
            if d is None or not amount_decimal_matches(d, target):
                continue
            prefix = (line[: m.start()] or "").strip()
            label = collapse_stutter_chars(prefix)
            if len(label) < 3:
                continue
            decs = positive_amounts_on_line(line)
            strategy = "same_line_last_amount"
            if len(decs) >= 2 and amount_decimal_matches(decs[0], target):
                strategy = "same_line_first_amount"
            spec = {
                "label": label,
                "strategy": strategy,
                "confirmed_value": format_confirmed_amount(ctx.confirmed_value),
            }
            return _attempt("unlabeled_prefix_amount", spec, candidate=float(target))
    return _attempt("unlabeled_prefix_amount", None, reason="no_unlabeled_line")


def _strategy_amount_from_context(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    if ctx.evaluation_mode:
        return StrategyAttempt(
            strategy="amount_from_context",
            status="skipped",
            reason="evaluation_context_disabled",
        )
    if not ctx.context_line:
        return StrategyAttempt(strategy="amount_from_context", status="skipped", reason="no_context")
    target = confirmed_amount_decimal(ctx.confirmed_value)
    if target is None:
        return None
    for i, line in enumerate(lines):
        if not _line_matches_context(line, ctx.context_line):
            continue
        if not _line_eligible_for_amount(line, target):
            continue
        for m in re.finditer(_AMOUNT_TOKEN, line or ""):
            d = normalize_amount_decimal(m.group(0))
            if d is None or not amount_decimal_matches(d, target):
                continue
            for label_text, _s, label_end in _label_candidates_on_line(line, "amount"):
                if m.start() >= label_end:
                    decs = positive_amounts_on_line(line)
                    strategy = "same_line_last_amount"
                    if len(decs) >= 2 and amount_decimal_matches(decs[0], target):
                        strategy = "same_line_first_amount"
                    spec = {
                        "label": label_text,
                        "strategy": strategy,
                        "confirmed_value": format_confirmed_amount(ctx.confirmed_value),
                    }
                    return _attempt("amount_from_context", spec, candidate=float(target))
    return _attempt("amount_from_context", None, reason="context_no_match")


def _strategy_amount_fallback_scan(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    target = confirmed_amount_decimal(ctx.confirmed_value)
    if target is None:
        return None
    for i, line in enumerate(lines):
        if not _line_eligible_for_amount(line, target):
            continue
        decs = positive_amounts_on_line(line)
        if not any(amount_decimal_matches(d, target) for d in decs):
            continue
        for m in re.finditer(_AMOUNT_TOKEN, line or ""):
            d = normalize_amount_decimal(m.group(0))
            if d is None or not amount_decimal_matches(d, target):
                continue
            for label_text, _s, label_end in _label_candidates_on_line(line, "amount"):
                if m.start() >= label_end:
                    decs2 = positive_amounts_on_line(line)
                    strategy = "same_line_last_amount"
                    if len(decs2) >= 2 and amount_decimal_matches(decs2[0], target):
                        strategy = "same_line_first_amount"
                    spec = {
                        "label": label_text,
                        "strategy": strategy,
                        "confirmed_value": format_confirmed_amount(ctx.confirmed_value),
                    }
                    return _attempt("amount_fallback_scan", spec, candidate=float(target))
    return _attempt("amount_fallback_scan", None, reason="no_eligible_line")


def _strategy_iban_full_text_scan(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    target = clean_iban(str(ctx.confirmed_value))
    if not target:
        return _attempt("iban_full_text_scan", None, reason="invalid_iban")
    ibans = _scan_sepa_ibans_in_text(ctx.raw_text or "")
    if ctx.evaluation_mode:
        ibans = _require_deterministic_candidate_selection(ibans)
    for ib in ibans:
        cleaned = clean_iban(ib)
        if cleaned == target:
            spec = {
                "strategy": "iban_full_text_scan",
                "confirmed_value": target,
            }
            return _attempt("iban_full_text_scan", spec, candidate=target)
    return _attempt("iban_full_text_scan", None, reason="iban_not_in_scan")


def _strategy_iban_label_same_line(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    target = clean_iban(str(ctx.confirmed_value))
    if not target:
        return None
    located = locate_string_position(ctx.raw_text, target)
    if located is None:
        return _attempt("iban_label_same_line", None, reason="value_not_in_text")
    _pos, line_idx, pos_in_line, actual = located
    line = lines[line_idx] if line_idx < len(lines) else ""
    for label_text, _s, _e in _label_candidates_on_line(line, "iban"):
        spec = {
            "label": label_text,
            "strategy": "same_line_first_iban",
            "confirmed_value": actual,
        }
        return _attempt("iban_label_same_line", spec, candidate=target)
    colon_label = _generic_colon_label(line, pos_in_line)
    if colon_label:
        spec = {
            "label": colon_label,
            "strategy": "same_line_first_iban",
            "confirmed_value": actual,
        }
        return _attempt("iban_label_same_line", spec, candidate=target)
    return _attempt("iban_label_same_line", None, reason="no_iban_label")


def _strategy_iban_label_next_line(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    target = clean_iban(str(ctx.confirmed_value))
    if not target:
        return None
    located = locate_string_position(ctx.raw_text, target)
    if located is None:
        return _attempt("iban_label_next_line", None, reason="value_not_in_text")
    _pos, line_idx, _pos_in_line, actual = located
    if line_idx < 1:
        return _attempt("iban_label_next_line", None, reason="no_line_above")
    prev = lines[line_idx - 1] or ""
    for label_text, _s, _e in _label_candidates_on_line(prev, "iban"):
        spec = {
            "label": label_text,
            "strategy": "next_line_first_iban",
            "confirmed_value": actual,
        }
        return _attempt("iban_label_next_line", spec, candidate=target)
    label = prev.split(":")[0].strip() if ":" in prev else prev.strip()
    if label:
        spec = {
            "label": label if ":" not in prev else prev[: prev.find(":") + 1].strip(),
            "strategy": "next_line_first_iban",
            "confirmed_value": actual,
        }
        return _attempt("iban_label_next_line", spec, candidate=target)
    return _attempt("iban_label_next_line", None, reason="no_label")


def _strategy_iban_scan_with_checksum_filter(ctx: StrategyContext, lines: list[str]) -> StrategyAttempt | None:
    target = clean_iban(str(ctx.confirmed_value))
    if not target:
        return None
    ibans = _scan_sepa_ibans_in_text(ctx.raw_text or "")
    plausible = [clean_iban(ib) for ib in ibans if is_plausible_iban(clean_iban(ib) or "")]
    if ctx.evaluation_mode:
        plausible = _require_deterministic_candidate_selection([p for p in plausible if p])
    for ib in plausible:
        if ib == target:
            spec = {"strategy": "iban_full_text_scan", "confirmed_value": target}
            return _attempt("iban_scan_with_checksum_filter", spec, candidate=target)
    return _attempt("iban_scan_with_checksum_filter", None, reason="checksum_no_match")


# Registry: internal strategy name -> implementation
_STRATEGY_IMPLS: dict[str, StrategyFn] = {
    "token_matching_confirmed_value": _strategy_token_matching_confirmed_value,
    "same_line_value_after_label": _strategy_same_line_value_after_label,
    "factuur_inline_pagina": _strategy_factuur_inline_pagina,
    "generic_label_same_line_after_colon": _strategy_generic_label_same_line_after_colon,
    "label_then_next_line": _strategy_label_then_next_line,
    "last_token_on_line": _strategy_last_token_on_line,
    "slash_compound_split": _strategy_slash_compound_split,
    "ocr_tag_extraction": _strategy_ocr_tag_extraction,
    "fallback_value_locate_minimal_label": _strategy_fallback_value_locate_minimal_label,
    "token_matching_confirmed_amount": _strategy_token_matching_confirmed_amount,
    "amount_from_context": _strategy_amount_from_context,
    "amount_fallback_scan": _strategy_amount_fallback_scan,
    "amount_label_next_line": _strategy_amount_label_next_line,
    "derived_excl_plus_vat": _strategy_amount_derived_excl_vat,
    "unlabeled_prefix_amount": _strategy_unlabeled_prefix_amount,
    "iban_full_text_scan": _strategy_iban_full_text_scan,
    "iban_label_same_line": _strategy_iban_label_same_line,
    "iban_label_next_line": _strategy_iban_label_next_line,
    "iban_scan_with_checksum_filter": _strategy_iban_scan_with_checksum_filter,
}


def known_strategy_impl_names() -> frozenset[str]:
    """Strategy names with runtime implementations (Phase 5 bundle validation)."""
    return frozenset(_STRATEGY_IMPLS.keys())


STRATEGY_REGISTRY: dict[FieldId, tuple[str, ...]] = {
    "invoice_number": (
        "factuur_inline_pagina",
        "token_matching_confirmed_value",
        "same_line_value_after_label",
        "generic_label_same_line_after_colon",
        "label_then_next_line",
        "last_token_on_line",
        "slash_compound_split",
        "ocr_tag_extraction",
        "fallback_value_locate_minimal_label",
    ),
    "customer_number": (
        "token_matching_confirmed_value",
        "same_line_value_after_label",
        "generic_label_same_line_after_colon",
        "label_then_next_line",
        "last_token_on_line",
        "slash_compound_split",
        "ocr_tag_extraction",
        "fallback_value_locate_minimal_label",
    ),
    "amount": (
        "token_matching_confirmed_amount",
        "amount_label_next_line",
        "amount_from_context",
        "amount_fallback_scan",
        "derived_excl_plus_vat",
        "unlabeled_prefix_amount",
    ),
    "iban": (
        "iban_full_text_scan",
        "iban_label_same_line",
        "iban_label_next_line",
        "iban_scan_with_checksum_filter",
    ),
}


def _require_deterministic_candidate_selection(candidates: list[Any]) -> list[Any]:
    """Sort candidates lexicographically for deterministic evaluation picks."""
    return sorted(candidates, key=lambda c: str(c).casefold())


def _enforce_evaluation_determinism(ctx: StrategyContext) -> None:
    """evaluation_mode uses frozen registry order and default scoring only."""
    if not ctx.evaluation_mode:
        return
    registry_pipeline = STRATEGY_REGISTRY.get(ctx.field_id, ())
    if get_strategy_pipeline(ctx.field_id, evaluation_mode=True) != registry_pipeline:
        raise EvaluationDeterminismError(
            f"evaluation pipeline drift for {ctx.field_id}"
        )
    if _amount_scoring_constants(True) != dict(_DEFAULT_AMOUNT_SCORING):
        raise EvaluationDeterminismError("evaluation amount scoring drift")
    for field_id, defaults in _DEFAULT_FIELD_SCORING.items():
        if _field_scoring_constants(field_id, True) != dict(defaults):
            raise EvaluationDeterminismError(f"evaluation scoring drift for {field_id}")


def _assert_evaluation_determinism(ctx: StrategyContext) -> None:
    """Backward-compatible alias."""
    _enforce_evaluation_determinism(ctx)


def _amount_scoring_constants(evaluation_mode: bool) -> dict[str, float]:
    return _field_scoring_constants("amount", evaluation_mode)


def _field_scoring_constants(field_id: FieldId, evaluation_mode: bool) -> dict[str, float]:
    """Per-field scoring constants: defaults in evaluation freeze; bundle overrides at runtime."""
    if field_id == "amount":
        defaults = dict(_DEFAULT_AMOUNT_SCORING)
    else:
        defaults = dict(_DEFAULT_FIELD_SCORING.get(field_id, {}))
    if evaluation_mode:
        return defaults
    scoring = get_semantic_scoring(field_id)
    if not scoring or not scoring.get("enabled"):
        return defaults
    adj = scoring.get("adjustments") if isinstance(scoring.get("adjustments"), dict) else {}
    out = dict(defaults)
    for key, val in adj.items():
        out[str(key)] = float(val)
    return out


def _ensure_bundle_loaded() -> dict[str, Any]:
    """Single reload boundary: all load_* readers use caches populated here."""
    global _bundle_load_attempted
    if not _bundle_load_attempted:
        reload_strategy_engine_state()
    return _engine_bundle_cache if isinstance(_engine_bundle_cache, dict) else {}


def get_engine_bundle() -> dict[str, Any]:
    """Return the loaded deployment bundle (read-only; empty dict if missing)."""
    return dict(_ensure_bundle_loaded())


def get_engine_bundle_version() -> int | None:
    """Return bundle version from last reload, or None if no bundle loaded."""
    _ensure_bundle_loaded()
    return _engine_bundle_version


def get_semantic_scoring(field_id: FieldId) -> dict[str, Any] | None:
    """Read field entry from loaded bundle semantic_scoring[field_id]."""
    _ensure_bundle_loaded()
    cache = _semantic_scoring_cache
    if not isinstance(cache, dict):
        return None
    entry = cache.get(field_id)
    return entry if isinstance(entry, dict) else None


def reload_strategy_engine_state(
    *,
    bundle_path: Path | None = None,
    skip_bundle_validation: bool = False,
    regression_baseline_path: Path | None | Literal["auto"] = "auto",
) -> None:
    """FULL reset: invalidate all caches, reload atomic bundle from disk."""
    global _engine_bundle_cache, _engine_bundle_version
    global _strategy_order_cache, _semantic_scoring_cache, _bundle_load_attempted

    previous = (
        _engine_bundle_cache,
        _engine_bundle_version,
        _strategy_order_cache,
        _semantic_scoring_cache,
        _bundle_load_attempted,
    )

    def _restore_previous() -> None:
        global _engine_bundle_cache, _engine_bundle_version
        global _strategy_order_cache, _semantic_scoring_cache, _bundle_load_attempted
        (
            _engine_bundle_cache,
            _engine_bundle_version,
            _strategy_order_cache,
            _semantic_scoring_cache,
            _bundle_load_attempted,
        ) = previous

    _engine_bundle_cache = None
    _engine_bundle_version = None
    _strategy_order_cache = None
    _semantic_scoring_cache = None
    _bundle_load_attempted = True

    path = bundle_path or _ENGINE_BUNDLE_PATH
    if not path.is_file():
        _engine_bundle_cache = {}
        _strategy_order_cache = {}
        _semantic_scoring_cache = {}
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        logger.debug("engine_bundle_load_failed path=%s", path)
        _restore_previous()
        return

    if not isinstance(data, dict):
        _restore_previous()
        return

    try:
        if not skip_bundle_validation:
            from parser.strategy_regression_guard import (
                load_regression_baseline,
                validate_bundle_compatibility,
            )

            baseline = None
            if regression_baseline_path == "auto":
                if _REGRESSION_BASELINE_PATH.is_file():
                    baseline = load_regression_baseline(_REGRESSION_BASELINE_PATH)
            elif regression_baseline_path is not None:
                baseline = load_regression_baseline(regression_baseline_path)
            validate_bundle_compatibility(data, baseline=baseline)

        _engine_bundle_cache = data
        _engine_bundle_version = int(data.get("version") or 0)

        patch = data.get("patch") if isinstance(data.get("patch"), dict) else {}
        orders = patch.get("order") if isinstance(patch.get("order"), dict) else {}
        overrides: dict[str, tuple[str, ...]] = {}
        for field_id, order in orders.items():
            if isinstance(order, list) and order:
                overrides[str(field_id)] = tuple(str(s) for s in order)
        _strategy_order_cache = overrides

        semantic = data.get("semantic_scoring")
        _semantic_scoring_cache = semantic if isinstance(semantic, dict) else {}
    except Exception:
        _restore_previous()
        raise


def load_strategy_order_overrides() -> dict[str, tuple[str, ...]]:
    """Load strategy order from atomic engine bundle (deployment artifact)."""
    _ensure_bundle_loaded()
    return _strategy_order_cache or {}


def get_strategy_pipeline(field_id: FieldId, *, evaluation_mode: bool = False) -> tuple[str, ...]:
    """Return strategy pipeline for field (bundle override at runtime; registry default in evaluation)."""
    if evaluation_mode:
        return STRATEGY_REGISTRY.get(field_id, ())
    overrides = load_strategy_order_overrides()
    if field_id in overrides:
        base = STRATEGY_REGISTRY.get(field_id, ())
        ordered = overrides[field_id]
        seen = set(ordered)
        tail = tuple(s for s in base if s not in seen)
        return ordered + tail
    return STRATEGY_REGISTRY.get(field_id, ())


def optimize_strategy_order(
    field_id: FieldId,
    stats: dict[str, Any],
) -> tuple[str, ...]:
    """
    Reorder strategies via golden learning pass (thin wrapper for backward compat).
    """
    from parser.golden_dataset_learning_pass import FieldStrategyStats, compute_optimal_strategy_order

    base = STRATEGY_REGISTRY.get(field_id, ())
    if not base:
        return ()
    strategy_stats = stats.get("strategies") if isinstance(stats, dict) else None
    if not isinstance(strategy_stats, dict):
        return base

    matrix: list[FieldStrategyStats] = []
    for name in base:
        st = strategy_stats.get(name) if isinstance(strategy_stats.get(name), dict) else {}
        attempts = int(st.get("attempts") or 0)
        wins = int(st.get("wins") or 0)
        matrix.append(
            FieldStrategyStats(
                strategy_name=name,
                attempts=attempts,
                wins=wins,
                win_rate=(wins / attempts) if attempts else 0.0,
                avg_confidence=0.0,
                avg_fragility=0.15 if name in FRAGILE_INTERNAL_STRATEGIES else 0.0,
                fallback_dependency_rate=0.0,
            )
        )
    return tuple(compute_optimal_strategy_order(field_id, matrix, registry_order=base))


def run_strategies(field_id: FieldId, ctx: StrategyContext) -> StrategyFieldResult:
    """Run all strategies; validated attempts scored — highest confidence wins."""
    _enforce_evaluation_determinism(ctx)
    lines = split_lines(ctx.raw_text)
    pipeline = get_strategy_pipeline(field_id, evaluation_mode=ctx.evaluation_mode)
    pipeline_index = {name: i for i, name in enumerate(pipeline)}
    attempts: list[StrategyAttempt] = []
    valid_resolved: list[ResolvedAttempt] = []
    trace: list[str] = []
    confirmed = normalize_field_value(field_id, ctx.confirmed_value)
    if confirmed is None:
        trace.append("confirmed_value_invalid")
        return StrategyFieldResult(validation_trace=trace)

    if not value_in_raw_text(ctx.raw_text, confirmed, field_id) and field_id != "iban":
        trace.append("value_not_in_raw_text")

    for name in pipeline:
        if name in LEARN_ONLY_STRATEGIES and ctx.mode not in ("learn", "runtime_fallback"):
            attempts.append(
                StrategyAttempt(strategy=name, status="skipped", reason="learn_only")
            )
            continue
        impl = _STRATEGY_IMPLS.get(name)
        if impl is None:
            continue
        raw_attempt = impl(ctx, lines)
        if raw_attempt is None:
            continue
        if raw_attempt.status == "skipped":
            attempts.append(raw_attempt)
            trace.append(f"{name}:skipped:{raw_attempt.reason}")
            continue
        spec = raw_attempt.profile_spec
        if spec is None:
            raw_attempt.status = "invalid"
            attempts.append(raw_attempt)
            trace.append(f"{name}:invalid:{raw_attempt.reason or 'no_spec'}")
            continue

        conf, breakdown = strategy_confidence(
            ctx,
            spec,
            raw_attempt.candidate,
            lines,
            internal_strategy=name,
        )
        resolved = _resolve_attempt(
            ctx.raw_text,
            field_id,
            spec,
            confirmed,
            strategy=name,
            confidence=conf,
            confidence_breakdown=breakdown,
        )
        if resolved is None:
            raw_attempt.status = "invalid"
            raw_attempt.reason = raw_attempt.reason or "validate_profile_failed"
            attempts.append(raw_attempt)
            trace.append(f"{name}:validate_failed")
            continue

        raw_attempt.status = "valid"
        raw_attempt.confidence = resolved.confidence
        raw_attempt.confidence_breakdown = dict(resolved.confidence_breakdown)
        raw_attempt.candidate = resolved.value
        attempts.append(raw_attempt)
        valid_resolved.append(resolved)
        trace.append(f"{name}:valid:{resolved.confidence:.2f}")

    if not valid_resolved:
        return StrategyFieldResult(
            all_attempted_strategies=attempts,
            validation_trace=trace,
        )

    winner = _select_winner(valid_resolved, ctx, pipeline_index)
    trace.append(f"winner:{winner.strategy}:{winner.confidence:.2f}")
    return StrategyFieldResult(
        value=winner.value,
        profile_spec=dict(winner.spec),
        strategy_used=winner.strategy,
        all_attempted_strategies=attempts,
        validation_trace=trace,
        confidence=winner.confidence,
    )


def run_runtime_fallback(
    field_id: FieldId,
    raw_text: str,
    confirmed_value: Any,
) -> StrategyFieldResult:
    """Re-run strategy pipeline when persisted spec fails validation."""
    ctx = StrategyContext(
        field_id=field_id,
        raw_text=raw_text,
        confirmed_value=confirmed_value,
        mode="runtime_fallback",
    )
    return run_strategies(field_id, ctx)

