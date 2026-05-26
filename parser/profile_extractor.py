"""
Profielgestuurde extractie van bedrag, factuurnummer en klantnummer uit factuur-raw_text.

Zonder profiel gebruikt de generieke parser heuristieken; met een eenmaal geleerd profiel
worden velden deterministisch uit vaste labels + strategieën gehaald.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from parser.pdf_parser import (
    _AMOUNT_PROFILE_LABEL_RE,
    _AMOUNT_TOKEN,
    _CUSTOMER_LABEL_RE,
    _INVOICE_LABEL_RE,
    _TOTAL_LINE_EXCLUDE_RE,
    _TOTAL_LINE_HINT_RE,
    _iter_amount_tokens_excluding_percent,
    collapse_stutter_chars,
    normalize_amount,
    normalize_amount_decimal,
)

FIELD_KEYS = ("amount", "invoice_number", "customer_number")

STRATEGIES = (
    "same_line_last_amount",
    "same_line_after_colon",
    "next_line_first_token",
    "same_line_first_amount",
)

_AMOUNT_TOLERANCE = Decimal("0.01")

_FIELD_LABEL_RES: dict[str, re.Pattern[str]] = {
    "amount": _AMOUNT_PROFILE_LABEL_RE,
    "invoice_number": _INVOICE_LABEL_RE,
    "customer_number": _CUSTOMER_LABEL_RE,
}


def _line_eligible_for_amount_profile_learning(
    line: str,
    target: Decimal | None = None,
) -> bool:
    """
    Of een regel geschikt is om een bedrag-profiel van te leren.

    Tabellen (Pearlpaint e.d.) kunnen ``Netto`` én ``BTW & bedrag inclusief`` op één regel
    hebben; die regel mag dan niet worden weggefilterd als het bevestigde bedrag er staat.
    """
    if not (line or "").strip():
        return False
    has_payable_label = bool(_AMOUNT_PROFILE_LABEL_RE.search(line))
    if target is not None:
        decs = _positive_amounts_on_line(line)
        if decs and any(abs(d - target) <= _AMOUNT_TOLERANCE for d in decs):
            if has_payable_label:
                return True
            return not _TOTAL_LINE_EXCLUDE_RE.search(line)
    if _TOTAL_LINE_EXCLUDE_RE.search(line):
        return has_payable_label
    return has_payable_label or bool(_TOTAL_LINE_HINT_RE.search(line))


def _amount_label_text_from_line(line: str) -> str | None:
    m = _AMOUNT_PROFILE_LABEL_RE.search(line or "")
    if not m:
        return None
    return _extend_label_span(line, m.start(), m.end())


def _split_lines(raw_text: str) -> list[str]:
    return (raw_text or "").split("\n")


def _find_label_line(lines: list[str], label: str) -> int | None:
    if not label:
        return None
    # Labels with space/colon are specific enough for substring search.
    if re.search(r"[\s:]", label):
        needle = label.lower()
        for i, line in enumerate(lines):
            if needle in (line or "").lower():
                return i
        collapsed_needle = collapse_stutter_chars(label).lower()
        if len(collapsed_needle) >= 3:
            for i, line in enumerate(lines):
                if collapsed_needle in collapse_stutter_chars(line).lower():
                    return i
        return None
    # Avoid "Subtotaal" matching label "Totaal".
    pattern = re.compile(
        r"(?<![a-zA-Z])" + re.escape(label) + r"(?![a-zA-Z0-9])",
        re.IGNORECASE,
    )
    for i, line in enumerate(lines):
        if pattern.search(line or ""):
            return i
    collapsed_needle = collapse_stutter_chars(label).lower()
    if len(collapsed_needle) >= 3:
        for i, line in enumerate(lines):
            if collapsed_needle in collapse_stutter_chars(line).lower():
                return i
    return None


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
    tok = rest.split()[0]
    cleaned = _clean_value_token(tok)
    return cleaned or None


def _extract_next_line_first_token(lines: list[str], label_line_idx: int) -> str | None:
    for j in range(label_line_idx + 1, len(lines)):
        ln = (lines[j] or "").strip()
        if not ln:
            continue
        tok = ln.split()[0]
        cleaned = _clean_value_token(tok)
        return cleaned or None
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
    return None


def _field_spec(profile: dict[str, Any], field: str) -> dict[str, Any] | None:
    spec = profile.get(field)
    if not isinstance(spec, dict):
        return None
    label = spec.get("label")
    strategy = spec.get("strategy")
    if not label or not strategy:
        return None
    if strategy not in STRATEGIES:
        return None
    return spec


def extract_with_profile(raw_text: str, profile: dict[str, Any]) -> dict[str, float | str | None]:
    """Extract amount, invoice_number and customer_number using a supplier profile."""
    lines = _split_lines(raw_text)
    out: dict[str, float | str | None] = {
        "amount": None,
        "invoice_number": None,
        "customer_number": None,
    }
    for field in FIELD_KEYS:
        spec = _field_spec(profile, field)
        if spec is None:
            continue
        idx = _find_label_line(lines, str(spec["label"]))
        if idx is None:
            continue
        val = _apply_strategy(lines, idx, str(spec["label"]), str(spec["strategy"]))
        if field == "amount" and val is not None:
            dec = normalize_amount_decimal(str(val))
            if dec is not None:
                val = float(dec)
        if val is not None:
            out[field] = val
    return out


def _extend_label_span(line: str, start: int, end: int) -> str:
    tail = line[end:]
    m = re.match(r"\s*:\s*", tail)
    if m:
        end = end + m.end()
    return line[start:end]


def _label_candidates_on_line(line: str, field: str) -> list[tuple[str, int, int]]:
    rx = _FIELD_LABEL_RES.get(field)
    if rx is None:
        return []
    out: list[tuple[str, int, int]] = []
    for m in rx.finditer(line or ""):
        label = _extend_label_span(line, m.start(), m.end())
        out.append((label, m.start(), m.end()))
    return out


def _char_pos_to_line(raw_text: str, pos: int) -> tuple[int, int]:
    line_idx = raw_text[:pos].count("\n")
    line_start = raw_text.rfind("\n", 0, pos) + 1
    return line_idx, pos - line_start


def _locate_amount_positions(raw_text: str, target: Decimal) -> list[tuple[int, int, int]]:
    results: list[tuple[int, int, int]] = []
    for m in re.finditer(_AMOUNT_TOKEN, raw_text or ""):
        d = normalize_amount_decimal(m.group(0))
        if d is None or abs(d - target) > _AMOUNT_TOLERANCE:
            continue
        line_idx, pos_in_line = _char_pos_to_line(raw_text, m.start())
        results.append((m.start(), line_idx, pos_in_line))
    return results


def _locate_string_position(raw_text: str, value: str) -> tuple[int, int, int, str] | None:
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
    line_idx, pos_in_line = _char_pos_to_line(text, pos)
    return pos, line_idx, pos_in_line, actual


def _find_best_label(
    lines: list[str],
    value_line_idx: int,
    value_pos_in_line: int,
    field: str,
) -> tuple[str, int] | None:
    """Return (label_text, label_line_idx) with same-line preference."""
    best: tuple[int, int, str, int] | None = None
    for check_idx in (value_line_idx, value_line_idx - 1):
        if check_idx < 0:
            continue
        line = lines[check_idx]
        for label_text, _start, label_end in _label_candidates_on_line(line, field):
            if check_idx == value_line_idx:
                if value_pos_in_line < label_end:
                    continue
                dist = value_pos_in_line - label_end
                priority = 0
            else:
                dist = value_pos_in_line + 1000
                priority = 1
            cand = (priority, dist, label_text, check_idx)
            if best is None or cand < best:
                best = cand
    if best is None:
        return None
    return best[2], best[3]


def _infer_strategy(
    lines: list[str],
    label_line_idx: int,
    value_line_idx: int,
    value_pos_in_line: int,
    confirmed_amount: Decimal | None,
) -> str | None:
    if value_line_idx == label_line_idx + 1:
        return "next_line_first_token"

    if value_line_idx != label_line_idx:
        return None

    line = lines[label_line_idx] or ""
    colon_idx = line.find(":")
    if colon_idx >= 0 and value_pos_in_line > colon_idx:
        return "same_line_after_colon"

    decs = _positive_amounts_on_line(line)
    if len(decs) >= 2:
        if confirmed_amount is not None and decs[0] == confirmed_amount:
            return "same_line_first_amount"
        return "same_line_last_amount"
    if len(decs) == 1:
        return "same_line_last_amount"
    return None


def _infer_strategy_for_field(
    lines: list[str],
    label_line_idx: int,
    value_line_idx: int,
    value_pos_in_line: int,
    field: str,
    confirmed_amount: Decimal | None,
) -> str | None:
    if field == "amount":
        return _infer_strategy(
            lines, label_line_idx, value_line_idx, value_pos_in_line, confirmed_amount
        )
    if value_line_idx == label_line_idx + 1:
        return "next_line_first_token"
    if value_line_idx == label_line_idx:
        line = lines[label_line_idx] or ""
        if ":" in line and value_pos_in_line > line.find(":"):
            return "same_line_after_colon"
        return "same_line_after_colon"
    return None


def _confirmed_amount_decimal(amount: float | Decimal | None) -> Decimal | None:
    if amount is None:
        return None
    if isinstance(amount, Decimal):
        return amount.quantize(Decimal("0.01"))
    v = normalize_amount(str(amount))
    if v is None:
        return None
    return Decimal(str(v)).quantize(Decimal("0.01"))


def _format_confirmed_amount(amount: float | Decimal) -> str:
    d = _confirmed_amount_decimal(amount)
    if d is None:
        return str(amount)
    return f"{d:.2f}"


def _line_matches_amount_context(line: str, context: str) -> bool:
    """True if ``context`` (from parser candidate) matches this PDF line."""
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
    # Parser joins lines with " >> "
    head = ctx.split(" >> ", 1)[0].strip()
    if head and (head in ln or ln in head):
        return True
    chead = collapse_stutter_chars(head)
    return bool(chead and (chead in cln or cln in chead))


def _learn_field_amount_unlabeled_same_line(
    line: str,
    line_idx: int,
    amount: float | Decimal,
    pos_in_line: int,
    target: Decimal,
) -> dict[str, str] | None:
    """
    Bedrag staat op de regel maar geen herkenbaar label (gestotterde PDF-tekst).

    Slaat het genormaliseerde label vóór het bedrag op (``collapse_stutter_chars``).
    """
    prefix = (line[:pos_in_line] or "").strip()
    label = collapse_stutter_chars(prefix)
    if len(label) < 3:
        return None
    decs = _positive_amounts_on_line(line)
    strategy = "same_line_last_amount"
    if len(decs) >= 2 and decs[0] is not None and abs(decs[0] - target) <= _AMOUNT_TOLERANCE:
        strategy = "same_line_first_amount"
    return {
        "label": label,
        "strategy": strategy,
        "confirmed_value": _format_confirmed_amount(amount),
    }


def _learn_field_amount_on_line(
    lines: list[str],
    line_idx: int,
    amount: float | Decimal,
) -> dict[str, str] | None:
    """Learn amount spec when we already know the label line (e.g. from parser context)."""
    target = _confirmed_amount_decimal(amount)
    if target is None or line_idx < 0 or line_idx >= len(lines):
        return None
    line = lines[line_idx] or ""
    if not _line_eligible_for_amount_profile_learning(line, target):
        return None
    pos_in_line = 0
    for m in re.finditer(_AMOUNT_TOKEN, line):
        d = normalize_amount_decimal(m.group(0))
        if d is not None and abs(d - target) <= _AMOUNT_TOLERANCE:
            pos_in_line = m.start()
            break
    else:
        return None
    found = _find_best_label(lines, line_idx, pos_in_line, "amount")
    if found is None:
        for label_text, _s, label_end in _label_candidates_on_line(line, "amount"):
            strategy = _infer_strategy(lines, line_idx, line_idx, label_end, target)
            if strategy:
                return {
                    "label": label_text,
                    "strategy": strategy,
                    "confirmed_value": _format_confirmed_amount(amount),
                }
        return _learn_field_amount_unlabeled_same_line(
            line, line_idx, amount, pos_in_line, target
        )
    label_text, label_line_idx = found
    strategy = _infer_strategy(
        lines, label_line_idx, line_idx, pos_in_line, target
    )
    if strategy is None:
        return None
    return {
        "label": label_text,
        "strategy": strategy,
        "confirmed_value": _format_confirmed_amount(amount),
    }


def _learn_field_amount_fallback(
    lines: list[str],
    amount: float | Decimal,
) -> dict[str, str] | None:
    """Scan totaal-regels in de PDF wanneer positie-zoeken faalt."""
    target = _confirmed_amount_decimal(amount)
    if target is None:
        return None
    for i, line in enumerate(lines):
        if not _line_eligible_for_amount_profile_learning(line, target):
            continue
        decs = _positive_amounts_on_line(line)
        if not any(abs(d - target) <= _AMOUNT_TOLERANCE for d in decs):
            continue
        spec = _learn_field_amount_on_line(lines, i, amount)
        if spec is not None:
            return spec
    return None


def _learn_field_amount_label_next_line(
    lines: list[str],
    amount: float | Decimal,
) -> dict[str, str] | None:
    """Label op regel i, bedrag op regel i+1 (tabelkop Pearlpaint e.d.)."""
    target = _confirmed_amount_decimal(amount)
    if target is None:
        return None
    for i, line in enumerate(lines):
        label_text = _amount_label_text_from_line(line)
        if not label_text or i + 1 >= len(lines):
            continue
        next_ln = lines[i + 1] or ""
        decs = _positive_amounts_on_line(next_ln)
        if not any(abs(d - target) <= _AMOUNT_TOLERANCE for d in decs):
            continue
        tok = _extract_next_line_first_token(lines, i)
        if not tok:
            continue
        got = normalize_amount_decimal(tok)
        if got is None or abs(got - target) > _AMOUNT_TOLERANCE:
            continue
        return {
            "label": label_text,
            "strategy": "next_line_first_token",
            "confirmed_value": _format_confirmed_amount(amount),
        }
    return None


def _learn_field_amount_from_context(
    lines: list[str],
    amount: float | Decimal,
    amount_context_line: str,
) -> dict[str, str] | None:
    ctx = (amount_context_line or "").strip()
    if not ctx:
        return None
    for i, line in enumerate(lines):
        if _line_matches_amount_context(line, ctx):
            spec = _learn_field_amount_on_line(lines, i, amount)
            if spec is not None:
                return spec
    head = ctx.split(" >> ", 1)[0].strip()
    if head:
        for i, line in enumerate(lines):
            if head in (line or ""):
                spec = _learn_field_amount_on_line(lines, i, amount)
                if spec is not None:
                    return spec
    return None


def _learn_field_amount(
    raw_text: str,
    lines: list[str],
    amount: float | Decimal,
) -> dict[str, str] | None:
    target = _confirmed_amount_decimal(amount)
    if target is None:
        return None
    positions = _locate_amount_positions(raw_text, target)
    if not positions:
        return None

    best: tuple[tuple[int, int], str, int, str] | None = None
    for _pos, line_idx, pos_in_line in positions:
        found = _find_best_label(lines, line_idx, pos_in_line, "amount")
        if found is None:
            continue
        label_text, label_line_idx = found
        strategy = _infer_strategy(
            lines, label_line_idx, line_idx, pos_in_line, target
        )
        if strategy is None:
            continue
        priority = 0 if label_line_idx == line_idx else 1
        dist = pos_in_line - (lines[label_line_idx].lower().find(label_text.lower()) + len(label_text))
        key = (priority, max(0, dist))
        if best is None or key < best[0]:
            best = (key, label_text, label_line_idx, strategy)

    if best is None:
        return None
    _key, label_text, _label_idx, strategy = best
    return {
        "label": label_text,
        "strategy": strategy,
        "confirmed_value": _format_confirmed_amount(amount),
    }


def _learn_field_string(
    raw_text: str,
    lines: list[str],
    field: str,
    value: str,
) -> dict[str, str] | None:
    located = _locate_string_position(raw_text, value)
    if located is None:
        return None
    _pos, line_idx, pos_in_line, actual = located
    found = _find_best_label(lines, line_idx, pos_in_line, field)
    if found is None:
        return None
    label_text, label_line_idx = found
    strategy = _infer_strategy_for_field(
        lines, label_line_idx, line_idx, pos_in_line, field, None
    )
    if strategy is None:
        return None
    return {
        "label": label_text,
        "strategy": strategy,
        "confirmed_value": actual,
    }


def _learn_field_string_from_context(
    lines: list[str],
    field: str,
    value: str,
    context_line: str,
) -> dict[str, str] | None:
    ctx = (context_line or "").strip()
    if not ctx or not value:
        return None
    for i, line in enumerate(lines):
        if _line_matches_amount_context(line, ctx):
            spec = _learn_field_string_on_line(lines, i, field, value)
            if spec is not None:
                return spec
    head = collapse_stutter_chars(ctx.split(" >> ", 1)[0].strip())
    if head:
        for i, line in enumerate(lines):
            if head in collapse_stutter_chars(line):
                spec = _learn_field_string_on_line(lines, i, field, value)
                if spec is not None:
                    return spec
    return None


def _learn_field_string_on_line(
    lines: list[str],
    line_idx: int,
    field: str,
    value: str,
) -> dict[str, str] | None:
    line = lines[line_idx] if 0 <= line_idx < len(lines) else ""
    located = _locate_string_position(line, value)
    if located is None:
        compact = re.sub(r"\s+", "", value)
        if compact and compact != value:
            located = _locate_string_position(line, compact)
    if located is None:
        return None
    _pos, _li, pos_in_line, actual = located
    found = _find_best_label(lines, line_idx, pos_in_line, field)
    if found is None:
        for label_text, _s, label_end in _label_candidates_on_line(line, field):
            strategy = _infer_strategy_for_field(
                lines, line_idx, line_idx, label_end, field, None
            )
            if strategy:
                return {
                    "label": label_text,
                    "strategy": strategy,
                    "confirmed_value": actual,
                }
        return None
    label_text, label_line_idx = found
    strategy = _infer_strategy_for_field(
        lines, label_line_idx, line_idx, pos_in_line, field, None
    )
    if strategy is None:
        return None
    return {
        "label": label_text,
        "strategy": strategy,
        "confirmed_value": actual,
    }


def learn_profile_from_confirmation(
    raw_text: str,
    confirmed: dict[str, Any],
    source_file: str,
    *,
    amount_context_line: str | None = None,
    invoice_context_line: str | None = None,
    customer_context_line: str | None = None,
) -> dict[str, Any] | None:
    """
    Learn an extraction profile from user-confirmed field values on a PDF raw_text.

    confirmed keys: amount (float|Decimal|None), invoice_number (str), customer_number (str).
    Empty strings skip a field. Returns None if no field could be learned.

  ``amount_context_line``: optionele regelcontext uit generieke ``amount_result``-kandidaat.
    """
    profile: dict[str, Any] = {
        "learned_from": Path(source_file or "").name,
    }
    lines = _split_lines(raw_text)

    amt = confirmed.get("amount")
    if amt is not None:
        spec = _learn_field_amount(raw_text, lines, amt)
        if spec is None and amount_context_line:
            spec = _learn_field_amount_from_context(lines, amt, amount_context_line)
        if spec is None:
            spec = _learn_field_amount_fallback(lines, amt)
        if spec is None:
            spec = _learn_field_amount_label_next_line(lines, amt)
        if spec:
            profile["amount"] = spec

    inv = str(confirmed.get("invoice_number") or "").strip()
    if inv:
        spec = _learn_field_string(raw_text, lines, "invoice_number", inv)
        if spec is None and invoice_context_line:
            spec = _learn_field_string_from_context(
                lines, "invoice_number", inv, invoice_context_line
            )
        if spec:
            profile["invoice_number"] = spec

    cust = str(confirmed.get("customer_number") or "").strip()
    if cust:
        spec = _learn_field_string(raw_text, lines, "customer_number", cust)
        if spec is None and customer_context_line:
            spec = _learn_field_string_from_context(
                lines, "customer_number", cust, customer_context_line
            )
        if spec:
            profile["customer_number"] = spec

    field_specs = [k for k in FIELD_KEYS if k in profile]
    if not field_specs:
        return None
    return profile


def _merge_confirmed(
    profile: dict[str, Any],
    confirmed: dict[str, Any] | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in FIELD_KEYS:
        if confirmed and field in confirmed and confirmed[field] is not None:
            if field == "amount":
                out[field] = confirmed[field]
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
