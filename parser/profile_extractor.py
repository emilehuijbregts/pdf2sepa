"""
Profielgestuurde extractie van bedrag, factuurnummer, klantnummer en IBAN uit factuur-raw_text.

Execution-only: learning lives in ``parser.profile_learner``.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from logic.validation import clean_iban
from parser.field_model import ALL_FIELD_IDS, normalize_field_value
from parser.iban_candidates import _IBAN_LABEL_RE
from parser.pdf_parser import (
    _AMOUNT_PROFILE_LABEL_RE,
    _AMOUNT_TOKEN,
    _CUSTOMER_LABEL_RE,
    _INVOICE_LABEL_RE,
    _iter_amount_tokens_excluding_percent,
    _scan_sepa_ibans_in_text,
    collapse_stutter_chars,
    normalize_amount,
    normalize_amount_decimal,
)

FIELD_KEYS = ALL_FIELD_IDS

STRATEGIES = (
    "same_line_last_amount",
    "same_line_after_colon",
    "next_line_first_token",
    "next_line_last_amount",
    "same_line_first_amount",
    "same_line_first_iban",
    "next_line_first_iban",
    "derived_excl_plus_vat",
    "factuur_inline_pagina",
)

_FACTUUR_INLINE_PAGINA_RE = re.compile(
    r"(?i)\bFactuur\s+([A-Za-z0-9][A-Za-z0-9\-\/]{4,})\s+Pagina\b"
)

_EXCL_BTW_LINE_RE = re.compile(
    r"(?i)\b(?:excl\.?\s*btw|netto\s+goederenbedrag)\b"
)
_VAT_PERCENT_LINE_RE = re.compile(r"(?i)\bbtw\s*(?:\d{1,2}\s*%|:)")

_AMOUNT_TOLERANCE = Decimal("0.01")

_FIELD_LABEL_RES: dict[str, re.Pattern[str]] = {
    "amount": _AMOUNT_PROFILE_LABEL_RE,
    "invoice_number": _INVOICE_LABEL_RE,
    "customer_number": _CUSTOMER_LABEL_RE,
    "iban": _IBAN_LABEL_RE,
    "vat_number": re.compile(r"(?i)\b(?:btw(?:-|\s*)nummer|btw|vat)\b"),
    "kvk_number": re.compile(r"(?i)\b(?:kvk|k\.?v\.?k\.?)\b"),
    "invoice_date": re.compile(r"(?i)\b(?:factuurdatum|factuur\s*datum|invoice\s*date|date\s*of\s*invoice|datum\s*factuur)\b"),
    "email_domain": re.compile(r"(?i)\b(?:e-?mail|email)\b"),
}


def _split_lines(raw_text: str) -> list[str]:
    return (raw_text or "").split("\n")


def _extend_payable_amount_label_span(line: str, start: int, end: int) -> str:
    """Include ``(incl.BTW)`` / ``(excl.BTW)`` suffix so labels disambiguate totaal-regels."""
    tail = line[end:]
    m = re.match(r"\s*:\s*", tail)
    if m:
        end = end + m.end()
    m2 = re.match(r"\s*\(\s*(?:incl|excl)\b[^)]*\)", line[end:], re.IGNORECASE)
    if m2:
        end = end + m2.end()
    return line[start:end].strip()


def _amount_decimal_matches(a: Decimal | None, b: Decimal | None) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= _AMOUNT_TOLERANCE


def extract_amount_with_field_spec(
    lines: list[str],
    field_spec: dict[str, Any],
) -> Decimal | None:
    """Extract one amount field spec from pre-split lines (profile execution)."""
    spec = _field_spec({"amount": field_spec}, "amount")
    if spec is None:
        return None
    strategy_s = str(spec["strategy"])
    if strategy_s == "derived_excl_plus_vat":
        derived = _extract_derived_excl_plus_vat(
            lines,
            str(spec["label_excl"]),
            str(spec["label_btw"]),
        )
        return derived
    cv = field_spec.get("confirmed_value")
    target = _confirmed_amount_decimal(cv) if cv is not None else None
    idx = _find_label_line(
        lines,
        str(spec["label"]),
        strategy=strategy_s,
        amount_target=target,
    )
    if idx is None:
        return None
    val = _apply_strategy(lines, idx, str(spec["label"]), strategy_s)
    if val is None:
        return None
    return _confirmed_amount_decimal(val)


def amount_field_spec_matches(
    lines: list[str],
    field_spec: dict[str, Any],
    expected: Decimal | float | str,
) -> bool:
    target = _confirmed_amount_decimal(expected)
    if target is None:
        return False
    ext = extract_amount_with_field_spec(lines, field_spec)
    return _amount_decimal_matches(ext, target)


def _iter_label_line_indices(lines: list[str], label: str) -> list[int]:
    """Alle regels waar ``label`` voorkomt (volgorde = documentvolgorde)."""
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


def _find_label_line(
    lines: list[str],
    label: str,
    *,
    strategy: str | None = None,
    amount_target: Decimal | None = None,
) -> int | None:
    """
    Regelindex voor ``label``.

    Met ``strategy``: eerste regel waar extractie een waarde oplevert (niet alleen
  eerste substring-match — voorkomt bv. «Prijs totaal» i.p.v. «Totaal 305,36 EUR»).
    Met ``amount_target``: kies de regel waar de strategy het bevestigde bedrag oplevert.
    """
    indices = _iter_label_line_indices(lines, label)
    if not indices:
        return None
    if strategy:
        for idx in indices:
            val = _apply_strategy(lines, idx, label, strategy)
            if val is None:
                continue
            if amount_target is not None:
                ext = _confirmed_amount_decimal(val)
                if not _amount_decimal_matches(ext, amount_target):
                    continue
            return idx
        return None
    return indices[0]


def _positive_amounts_on_line(line: str) -> list[Decimal]:
    out: list[Decimal] = []
    for tok in _iter_amount_tokens_excluding_percent(line or ""):
        d = normalize_amount_decimal(tok)
        if d is not None and d > Decimal("0"):
            out.append(d)
    return out


def _extract_amount_on_line(line: str, strategy: str) -> float | None:
    decs = _positive_amounts_on_line(line)
    if not decs:
        return None
    pick = decs[0] if strategy == "same_line_first_amount" else decs[-1]
    return float(pick)


def _clean_value_token(tok: str) -> str:
    return (tok or "").strip().strip(".,;")


def _extract_after_colon(line: str, label: str) -> str | None:
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
        return norm
    tok = rest.split()[0]
    cleaned = _clean_value_token(tok)
    return cleaned or None


def _first_iban_on_line(line: str) -> str | None:
    ibans = _scan_sepa_ibans_in_text(line or "")
    if not ibans:
        return None
    return clean_iban(ibans[0])


def _extract_next_line_first_iban(lines: list[str], label_line_idx: int) -> str | None:
    for j in range(label_line_idx + 1, len(lines)):
        ln = (lines[j] or "").strip()
        if not ln:
            continue
        iban = _first_iban_on_line(ln)
        if iban:
            return iban
    return None


def _extract_next_line_first_token(lines: list[str], label_line_idx: int) -> str | None:
    for j in range(label_line_idx + 1, len(lines)):
        ln = (lines[j] or "").strip()
        if not ln:
            continue
        tok = ln.split()[0]
        cleaned = _clean_value_token(tok)
        return cleaned or None
    return None


def _extract_next_line_last_amount(lines: list[str], label_line_idx: int) -> float | None:
    for j in range(label_line_idx + 1, len(lines)):
        ln = (lines[j] or "").strip()
        if not ln:
            continue
        return _extract_amount_on_line(ln, "same_line_last_amount")
    return None


def _apply_strategy(
    lines: list[str],
    label_line_idx: int,
    label: str,
    strategy: str,
) -> float | str | None:
    line = lines[label_line_idx] if label_line_idx < len(lines) else ""
    if strategy == "same_line_last_amount":
        return _extract_amount_on_line(line, strategy)
    if strategy == "same_line_first_amount":
        return _extract_amount_on_line(line, strategy)
    if strategy == "same_line_after_colon":
        return _extract_after_colon(line, label)
    if strategy == "next_line_first_token":
        return _extract_next_line_first_token(lines, label_line_idx)
    if strategy == "next_line_last_amount":
        return _extract_next_line_last_amount(lines, label_line_idx)
    if strategy == "same_line_first_iban":
        return _first_iban_on_line(line)
    if strategy == "next_line_first_iban":
        return _extract_next_line_first_iban(lines, label_line_idx)
    if strategy == "factuur_inline_pagina":
        m = _FACTUUR_INLINE_PAGINA_RE.search(line or "")
        return m.group(1).strip() if m else None
    return None


def _field_spec(profile: dict[str, Any], field: str) -> dict[str, Any] | None:
    spec = profile.get(field)
    if not isinstance(spec, dict):
        return None
    strategy = spec.get("strategy")
    if not strategy or strategy not in STRATEGIES:
        return None
    if strategy == "derived_excl_plus_vat":
        if field != "amount":
            return None
        if not spec.get("label_excl") or not spec.get("label_btw"):
            return None
        return spec
    label = spec.get("label")
    if not label:
        return None
    return spec


def _extract_derived_excl_plus_vat(
    lines: list[str],
    label_excl: str,
    label_btw: str,
) -> Decimal | None:
    """Som excl.-regel + BTW%-regel (zelfde contract als pdf_parser derived_excl_plus_vat)."""
    excl_val: Decimal | None = None
    vat_val: Decimal | None = None
    for ln in lines:
        if _EXCL_BTW_LINE_RE.search(ln or "") and label_excl.lower() in (ln or "").lower():
            toks = _positive_amounts_on_line(ln)
            if toks:
                excl_val = toks[-1]
        if (
            excl_val is not None
            and _VAT_PERCENT_LINE_RE.search(ln or "")
            and label_btw.lower() in (ln or "").lower()
        ):
            toks = _positive_amounts_on_line(ln)
            if toks:
                vat_val = toks[-1]
    if excl_val is None or vat_val is None:
        return None
    return (excl_val + vat_val).quantize(Decimal("0.01"))


def extract_with_profile(raw_text: str, profile: dict[str, Any]) -> dict[str, float | str | None]:
    """Extract profile fields using a supplier profile."""
    lines = _split_lines(raw_text)
    out: dict[str, float | str | None] = {
        "amount": None,
        "invoice_number": None,
        "customer_number": None,
        "iban": None,
        "vat_number": None,
        "kvk_number": None,
        "invoice_date": None,
        "email_domain": None,
    }
    for field in FIELD_KEYS:
        spec = _field_spec(profile, field)
        if spec is None:
            continue
        strategy_s = str(spec["strategy"])
        if field == "amount" and strategy_s == "derived_excl_plus_vat":
            derived = _extract_derived_excl_plus_vat(
                lines,
                str(spec["label_excl"]),
                str(spec["label_btw"]),
            )
            if derived is not None:
                out["amount"] = float(derived)
            continue
        amount_target = None
        if field == "amount":
            cv = spec.get("confirmed_value")
            if cv is not None:
                amount_target = _confirmed_amount_decimal(cv)
        idx = _find_label_line(
            lines,
            str(spec["label"]),
            strategy=strategy_s,
            amount_target=amount_target,
        )
        if idx is None:
            continue
        val = _apply_strategy(lines, idx, str(spec["label"]), strategy_s)
        if field == "amount" and val is not None:
            dec = normalize_amount_decimal(str(val))
            if dec is not None:
                val = float(dec)
        elif field == "iban" and val is not None:
            val = clean_iban(str(val)) or None
        elif field in ("vat_number", "kvk_number", "invoice_date", "email_domain") and val is not None:
            val = normalize_field_value(field, val)  # type: ignore[arg-type]
        if val is not None:
            out[field] = val
    return out


def _confirmed_amount_decimal(amount: float | Decimal | None) -> Decimal | None:
    if amount is None:
        return None
    if isinstance(amount, Decimal):
        return amount.quantize(Decimal("0.01"))
    v = normalize_amount(str(amount))
    if v is None:
        return None
    return Decimal(str(v)).quantize(Decimal("0.01"))


def _merge_confirmed(
    profile: dict[str, Any],
    confirmed: dict[str, Any] | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in FIELD_KEYS:
        if confirmed and field in confirmed and confirmed[field] is not None:
            if field == "amount":
                out[field] = confirmed[field]
            elif field == "iban":
                v = clean_iban(str(confirmed[field]))
                if v:
                    out[field] = v
            else:
                v = str(confirmed[field]).strip()
                if v:
                    out[field] = v
            continue
        spec = _field_spec(profile, field)
        if spec and spec.get("confirmed_value") is not None:
            out[field] = spec["confirmed_value"]
    return out


def _values_match(field: str, extracted: float | str | None, expected: Any) -> bool:
    if extracted is None:
        return False
    if field == "amount":
        exp_d = _confirmed_amount_decimal(expected)
        ext_d = _confirmed_amount_decimal(extracted)
        if exp_d is None or ext_d is None:
            return False
        return abs(ext_d - exp_d) <= _AMOUNT_TOLERANCE
    if field == "iban":
        return clean_iban(str(extracted)) == clean_iban(str(expected))
    return str(extracted).strip() == str(expected).strip()


def validate_profile(
    raw_text: str,
    profile: dict[str, Any],
    confirmed: dict[str, Any] | None = None,
) -> bool:
    """Return True if extract_with_profile matches confirmed for every profile field."""
    merged = _merge_confirmed(profile, confirmed)
    extracted = extract_with_profile(raw_text, profile)
    for field in FIELD_KEYS:
        if field not in profile or not _field_spec(profile, field):
            continue
        if field not in merged:
            return False
        if not _values_match(field, extracted.get(field), merged[field]):
            return False
    return True
