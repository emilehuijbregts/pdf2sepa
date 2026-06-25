"""Profile learning: observe resolver-final FieldResults and derive extraction specs."""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from logic.validation import clean_iban
from parser.field_model import (
    FieldCandidate,
    ALL_FIELD_IDS,
    FieldId,
    FieldResult,
    is_resolver_final_field_result,
    normalize_field_value,
    field_result_from_result_dict,
    _RESULT_KEY_BY_FIELD,
)
from parser.iban_candidates import _IBAN_LABEL_RE
from parser.pdf_parser import (
    _AMOUNT_PROFILE_LABEL_RE,
    _AMOUNT_TOKEN,
    _CUSTOMER_LABEL_RE,
    _INVOICE_LABEL_RE,
    _INVOICE_DATE_LABEL_RE,
    _EMAIL_RE,
    _KVK_RE,
    _VAT_RE,
    _TOTAL_LINE_EXCLUDE_RE,
    _TOTAL_LINE_HINT_RE,
    _iter_amount_tokens_excluding_percent,
    collapse_stutter_chars,
    normalize_amount,
    normalize_amount_decimal,
)
from parser.profile_extractor import (
    STRATEGIES,
    FIELD_KEYS,
    _EXCL_BTW_LINE_RE,
    _VAT_PERCENT_LINE_RE,
    _extract_derived_excl_plus_vat,
)

logger = logging.getLogger(__name__)

_AMOUNT_TOLERANCE = Decimal("0.01")

_FIELD_LABEL_RES: dict[str, re.Pattern[str]] = {
    "amount": _AMOUNT_PROFILE_LABEL_RE,
    "invoice_number": _INVOICE_LABEL_RE,
    "customer_number": _CUSTOMER_LABEL_RE,
    "iban": _IBAN_LABEL_RE,
    "invoice_date": _INVOICE_DATE_LABEL_RE,
    "vat_number": re.compile(r"(?i)\b(?:btw(?:-|\s*)nummer|btw|vat)\b"),
    "kvk_number": re.compile(r"(?i)\b(?:kvk|k\.?v\.?k\.?)\b"),
    "email_domain": re.compile(r"(?i)\b(?:e-?mail|email)\b"),
}

_DIALOG_OVERLAY_FIELD_IDS: frozenset[FieldId] = frozenset(
    {"amount", "invoice_number", "customer_number"}
)

# Domain split for profile learning (independent save/reporting per domain).
IDENTIFICATION_LEARN_FIELDS: tuple[FieldId, ...] = ("invoice_number", "customer_number")
AMOUNT_LEARN_FIELDS: tuple[FieldId, ...] = ("amount",)


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
    return _extend_payable_amount_label_span(line, m.start(), m.end())


def _verified_amount_spec(
    lines: list[str],
    spec: dict[str, str],
    target: Decimal,
) -> bool:
    from parser.profile_extractor import amount_field_spec_matches

    return amount_field_spec_matches(lines, spec, target)


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
    if strategy == "same_line_first_iban":
        return _first_iban_on_line(line)
    if strategy == "next_line_first_iban":
        return _extract_next_line_first_iban(lines, label_line_idx)
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


def extract_with_profile(raw_text: str, profile: dict[str, Any]) -> dict[str, float | str | None]:
    """Extract amount, invoice_number and customer_number using a supplier profile."""
    lines = _split_lines(raw_text)
    out: dict[str, float | str | None] = {
        "amount": None,
        "invoice_number": None,
        "customer_number": None,
        "iban": None,
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
        elif field == "iban" and val is not None:
            val = clean_iban(str(val)) or None
        if val is not None:
            out[field] = val
    return out


def _extend_label_span(line: str, start: int, end: int) -> str:
    tail = line[end:]
    m = re.match(r"\s*:\s*", tail)
    if m:
        end = end + m.end()
    return line[start:end]


def _extend_payable_amount_label_span(line: str, start: int, end: int) -> str:
    tail = line[end:]
    m = re.match(r"\s*:\s*", tail)
    if m:
        end = end + m.end()
    m2 = re.match(r"\s*\(\s*(?:incl|excl)\b[^)]*\)", line[end:], re.IGNORECASE)
    if m2:
        end = end + m2.end()
    return line[start:end].strip()


def _label_candidates_on_line(line: str, field: str) -> list[tuple[str, int, int]]:
    rx = _FIELD_LABEL_RES.get(field)
    if rx is None:
        return []
    out: list[tuple[str, int, int]] = []
    for m in rx.finditer(line or ""):
        if field == "amount":
            label = _extend_payable_amount_label_span(line, m.start(), m.end())
        else:
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
    *,
    label_text: str = "",
) -> str | None:
    if value_line_idx == label_line_idx + 1:
        return "next_line_first_token"

    if value_line_idx != label_line_idx:
        return None

    line = lines[label_line_idx] or ""
    colon_idx = line.find(":")
    if colon_idx >= 0 and value_pos_in_line > colon_idx:
        label_start = line.lower().find((label_text or "").lower())
        if label_start >= 0 and value_pos_in_line >= label_start + len(label_text):
            decs = _positive_amounts_on_line(line)
            if decs:
                if confirmed_amount is not None:
                    for pick, strategy in (
                        (decs[-1], "same_line_last_amount"),
                        (decs[0], "same_line_first_amount"),
                    ):
                        if abs(pick - confirmed_amount) <= _AMOUNT_TOLERANCE:
                            return strategy
                return "same_line_last_amount"
        after = _extract_after_colon(line, label_text)
        if after is not None and confirmed_amount is not None:
            got = normalize_amount_decimal(after)
            if got is not None and abs(got - confirmed_amount) <= _AMOUNT_TOLERANCE:
                return "same_line_after_colon"
        if after is not None and confirmed_amount is None:
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
    *,
    label_text: str = "",
) -> str | None:
    if field == "amount":
        return _infer_strategy(
            lines,
            label_line_idx,
            value_line_idx,
            value_pos_in_line,
            confirmed_amount,
            label_text=label_text,
        )
    if field == "iban":
        if value_line_idx == label_line_idx + 1:
            return "next_line_first_iban"
        if value_line_idx == label_line_idx:
            return "same_line_first_iban"
        return None
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
            strategy = _infer_strategy(
                lines, line_idx, line_idx, label_end, target, label_text=label_text
            )
            if not strategy:
                continue
            spec = {
                "label": label_text,
                "strategy": strategy,
                "confirmed_value": _format_confirmed_amount(amount),
            }
            if _verified_amount_spec(lines, spec, target):
                return spec
        return _learn_field_amount_unlabeled_same_line(
            line, line_idx, amount, pos_in_line, target
        )
    label_text, label_line_idx = found
    strategy = _infer_strategy(
        lines, label_line_idx, line_idx, pos_in_line, target, label_text=label_text
    )
    if strategy is None:
        return None
    spec = {
        "label": label_text,
        "strategy": strategy,
        "confirmed_value": _format_confirmed_amount(amount),
    }
    if not _verified_amount_spec(lines, spec, target):
        return None
    return spec


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


def _learn_field_amount_derived_excl_vat(
    lines: list[str],
    amount: float | Decimal,
) -> dict[str, str] | None:
    """
    Leer bedrag-profiel wanneer totaal afgeleid is uit excl. BTW + BTW%-regel.

    Zelfde scan als ``pdf_parser`` ``derived_excl_plus_vat``; opgeslagen labels
    moeten herleidbaar zijn via ``extract_with_profile``.
    """
    target = _confirmed_amount_decimal(amount)
    if target is None:
        return None
    excl_label: str | None = None
    excl_val: Decimal | None = None
    vat_label: str | None = None
    vat_val: Decimal | None = None
    for ln in lines:
        m_excl = _EXCL_BTW_LINE_RE.search(ln or "")
        if m_excl:
            toks = _positive_amounts_on_line(ln)
            if toks:
                excl_val = toks[-1]
                excl_label = _extend_label_span(ln, m_excl.start(), m_excl.end()).strip()
        if excl_val is not None:
            m_vat = _VAT_PERCENT_LINE_RE.search(ln or "")
            if m_vat:
                toks = _positive_amounts_on_line(ln)
                if toks:
                    vat_val = toks[-1]
                    vat_label = _extend_label_span(ln, m_vat.start(), m_vat.end()).strip()
    if (
        excl_val is None
        or vat_val is None
        or not excl_label
        or not vat_label
    ):
        return None
    derived = (excl_val + vat_val).quantize(Decimal("0.01"))
    if abs(derived - target) > _AMOUNT_TOLERANCE:
        return None
    check = _extract_derived_excl_plus_vat(lines, excl_label, vat_label)
    if check is None or abs(check - target) > _AMOUNT_TOLERANCE:
        return None
    return {
        "strategy": "derived_excl_plus_vat",
        "label_excl": excl_label,
        "label_btw": vat_label,
        "confirmed_value": _format_confirmed_amount(amount),
    }


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
            lines,
            label_line_idx,
            line_idx,
            pos_in_line,
            target,
            label_text=label_text,
        )
        if strategy is None:
            continue
        spec = {
            "label": label_text,
            "strategy": strategy,
            "confirmed_value": _format_confirmed_amount(amount),
        }
        if not _verified_amount_spec(lines, spec, target):
            alt = _learn_field_amount_on_line(lines, line_idx, amount)
            if alt is not None and _verified_amount_spec(lines, alt, target):
                return alt
            continue
        priority = 0 if label_line_idx == line_idx else 1
        dist = pos_in_line - (
            lines[label_line_idx].lower().find(label_text.lower()) + len(label_text)
        )
        key = (priority, max(0, dist))
        if best is None or key < best[0]:
            best = (key, spec)

    if best is None:
        for _pos, line_idx, _pos_in_line in positions:
            alt = _learn_field_amount_on_line(lines, line_idx, amount)
            if alt is not None and _verified_amount_spec(lines, alt, target):
                return alt
        return None
    return best[1]


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


def _overlay_dialog_confirmed(
    fr: FieldResult,
    field_id: FieldId,
    dialog_confirmed: dict[str, Any],
) -> FieldResult:
    """Dialog bevestiging -> expliciet user-overridden resolver-finale state."""
    raw = dialog_confirmed.get(field_id)
    if raw is None:
        return fr
    norm = normalize_field_value(field_id, raw)
    if norm is None:
        return fr
    prev = fr.selected_value
    fr.selected_value = norm
    fr.user_selected = True
    fr.user_overridden = True
    fr.override_reason = "user_locked"
    if prev is not None and prev != norm:
        fr.previous_value = prev
    fr.resolver_finalized = True
    fr.decision_trace = list(fr.decision_trace or [])
    fr.decision_trace.append(
        {
            "source": "manual",
            "confidence": 100,
            "considered": True,
            "win": True,
            "override_reason": "user_locked",
        }
    )
    fr.confidence = max(int(fr.confidence or 0), 100)
    return fr


def prepare_learnable_field_results(
    post_resolve_snapshot: dict[str, Any],
    *,
    dialog_confirmed: dict[str, Any] | None = None,
    legacy_result_dicts: dict[FieldId, dict[str, Any] | None] | None = None,
) -> dict[FieldId, FieldResult]:
    """Build learnable FieldResults from post-resolve snapshot (and optional dialog overlay)."""
    snap = post_resolve_snapshot if isinstance(post_resolve_snapshot, dict) else {}
    legacy = legacy_result_dicts or {}
    dialog = dialog_confirmed if isinstance(dialog_confirmed, dict) else {}
    out: dict[FieldId, FieldResult] = {}

    for field_id in ALL_FIELD_IDS:
        data = legacy.get(field_id)
        if data is None:
            key = _RESULT_KEY_BY_FIELD.get(field_id)
            if key:
                raw = snap.get(key)
                if isinstance(raw, dict):
                    data = raw
        if data is None and field_id not in _DIALOG_OVERLAY_FIELD_IDS:
            continue
        fr = field_result_from_result_dict(data, field_id=field_id)
        if field_id in _DIALOG_OVERLAY_FIELD_IDS and dialog:
            fr = _overlay_dialog_confirmed(fr, field_id, dialog)
        if field_id == "customer_number":
            from parser.supplier_db import CUSTOMER_NUMBER_MODE_NONE, infer_customer_number_mode_from_result

            cr_src = data if isinstance(data, dict) else None
            if cr_src is None:
                key = _RESULT_KEY_BY_FIELD.get("customer_number")
                if key:
                    raw_cr = snap.get(key)
                    if isinstance(raw_cr, dict):
                        cr_src = raw_cr
            if infer_customer_number_mode_from_result(cr_src) == CUSTOMER_NUMBER_MODE_NONE:
                continue
        if not is_resolver_final_field_result(fr):
            continue
        if normalize_field_value(field_id, fr.selected_value) is None:
            continue
        out[field_id] = fr
    return out


def _format_confirmed_for_spec(field_id: FieldId, value: Any) -> str:
    if field_id == "amount":
        d = _confirmed_amount_decimal(value)
        if d is None:
            return str(value)
        return f"{d:.2f}"
    if field_id == "iban":
        return clean_iban(str(value)) or str(value)
    return str(value).strip()


def _learn_field_spec(
    raw_text: str,
    field_id: FieldId,
    fr: FieldResult,
) -> dict[str, Any] | None:
    confirmed = normalize_field_value(field_id, fr.selected_value)
    if confirmed is None:
        return None
    context = fr.resolved_context(target_value=confirmed)
    lines = _split_lines(raw_text)

    if field_id == "amount":
        if not context:
            context = fr.resolved_context(target_value=confirmed)
        target_dec = _confirmed_amount_decimal(confirmed)
        reject_reason = "unknown"
        if target_dec is not None and not _locate_amount_positions(raw_text, target_dec):
            reject_reason = "amount_not_in_text"
        spec = _learn_field_amount(raw_text, lines, confirmed)
        if spec is None and context:
            spec = _learn_field_amount_from_context(lines, confirmed, context)
            if spec is None:
                reject_reason = "context_no_match"
        if spec is None:
            spec = _learn_field_amount_fallback(lines, confirmed)
            if spec is None:
                reject_reason = "no_eligible_line"
        if spec is None:
            spec = _learn_field_amount_label_next_line(lines, confirmed)
            if spec is None and reject_reason == "unknown":
                reject_reason = "no_label_next_line"
        if spec is None:
            spec = _learn_field_amount_derived_excl_vat(lines, confirmed)
            if spec is None:
                fr_source = str(fr.source or "").strip().lower()
                if "derived_excl" in fr_source or reject_reason == "amount_not_in_text":
                    reject_reason = "derived_components_missing"
                elif reject_reason in ("unknown", "no_eligible_line"):
                    reject_reason = "no_label_or_strategy"
        if spec is None:
            logger.debug(
                "amount_profile_learn_rejected reason=%s source=%s context=%r",
                reject_reason,
                fr.source,
                context,
            )
    elif field_id == "invoice_date":
        spec = _learn_field_invoice_date(raw_text, lines, str(confirmed), context or "")
    elif field_id == "vat_number":
        spec = _learn_field_vat_number(raw_text, lines, str(confirmed))
    elif field_id == "email_domain":
        spec = _learn_field_email_domain(raw_text, lines, str(confirmed))
    else:
        val_s = _format_confirmed_for_spec(field_id, confirmed)
        spec = _learn_field_string(raw_text, lines, field_id, val_s)
        if spec is None and context:
            spec = _learn_field_string_from_context(lines, field_id, val_s, context)

    if spec is None:
        return None
    conf = int(fr.confidence or 0)
    if conf > 0:
        spec = dict(spec)
        spec["confidence"] = conf
    return spec


def _learn_field_invoice_date(
    raw_text: str,
    lines: list[str],
    confirmed_iso: str,
    context_line: str,
) -> dict[str, str] | None:
    """
    Invoice date learning is value-normalized (ISO), but the PDF contains locale date tokens.
    We therefore locate matching date tokens on lines with an invoice-date label and infer
    the same (label,strategy) contract as other fields.
    """
    target = normalize_field_value("invoice_date", confirmed_iso)
    if not isinstance(target, str) or not target:
        return None

    # Prefer explicit label hits.
    for i, line in enumerate(lines):
        for m in _INVOICE_DATE_LABEL_RE.finditer(line or ""):
            label_text = _extend_label_span(line, m.start(), m.end())
            # Same line: try to find any token that normalizes to target.
            tail = (line or "")[m.end() :]
            for dm in re.finditer(r"\b\d{1,4}[\./-]\d{1,2}[\./-]\d{1,4}\b|\b\d{1,2}\s+[A-Za-z]{3,}\.?\s+\d{4}\b", tail):
                tok = dm.group(0)
                if normalize_field_value("invoice_date", tok) == target:
                    return {
                        "label": label_text,
                        "strategy": "same_line_after_colon",
                        "confirmed_value": target,
                    }
            # Next line: date token on next non-empty line.
            if i + 1 < len(lines):
                nxt = (lines[i + 1] or "").strip()
                if nxt:
                    for dm in re.finditer(r"\b\d{1,4}[\./-]\d{1,2}[\./-]\d{1,4}\b|\b\d{1,2}\s+[A-Za-z]{3,}\.?\s+\d{4}\b", nxt):
                        tok = dm.group(0)
                        if normalize_field_value("invoice_date", tok) == target:
                            return {
                                "label": label_text,
                                "strategy": "next_line_first_token",
                                "confirmed_value": target,
                            }
    # Context fallback: if we have a context line, try to find the label line by substring.
    if context_line:
        idx = _find_label_line(lines, context_line)
        if idx is not None and 0 <= idx < len(lines):
            ln = lines[idx]
            for m in _INVOICE_DATE_LABEL_RE.finditer(ln or ""):
                label_text = _extend_label_span(ln, m.start(), m.end())
                return {"label": label_text, "strategy": "same_line_after_colon", "confirmed_value": target}
    return None


def _learn_field_vat_number(
    raw_text: str,
    lines: list[str],
    confirmed_vat: str,
) -> dict[str, str] | None:
    target = normalize_field_value("vat_number", confirmed_vat)
    if not isinstance(target, str) or not target:
        return None
    for m in _VAT_RE.finditer(raw_text or ""):
        tok = m.group(0)
        if normalize_field_value("vat_number", tok) != target:
            continue
        located = _locate_string_position(raw_text, tok)
        if located is None:
            continue
        _pos, line_idx, pos_in_line, _actual = located
        found = _find_best_label(lines, line_idx, pos_in_line, "vat_number")
        if found is None:
            continue
        label_text, label_line_idx = found
        strategy = _infer_strategy_for_field(lines, label_line_idx, line_idx, pos_in_line, "vat_number", None)
        if strategy is None:
            continue
        return {"label": label_text, "strategy": strategy, "confirmed_value": target}
    return None


def _learn_field_email_domain(
    raw_text: str,
    lines: list[str],
    confirmed_domain: str,
) -> dict[str, str] | None:
    target = normalize_field_value("email_domain", confirmed_domain)
    if not isinstance(target, str) or not target:
        return None
    for m in _EMAIL_RE.finditer(raw_text or ""):
        dom = m.group(1)
        if normalize_field_value("email_domain", dom) != target:
            continue
        located = _locate_string_position(raw_text, dom)
        if located is None:
            continue
        _pos, line_idx, pos_in_line, _actual = located
        found = _find_best_label(lines, line_idx, pos_in_line, "email_domain")
        if found is None:
            continue
        label_text, label_line_idx = found
        strategy = _infer_strategy_for_field(lines, label_line_idx, line_idx, pos_in_line, "email_domain", None)
        if strategy is None:
            continue
        return {"label": label_text, "strategy": strategy, "confirmed_value": target}
    return None


def learn_profile_from_resolved_fields(
    *,
    raw_text: str,
    source_file: str,
    field_results: dict[FieldId, FieldResult],
) -> dict[str, Any] | None:
    """Learn profile specs from resolver-final FieldResults only."""
    profile: dict[str, Any] = {"learned_from": Path(source_file or "").name}
    for field_id in ALL_FIELD_IDS:
        fr = field_results.get(field_id)
        if fr is None:
            continue
        if not is_resolver_final_field_result(fr):
            continue
        spec = _learn_field_spec(raw_text, field_id, fr)
        if spec is not None:
            profile[field_id] = spec
    if not any(k in profile for k in ALL_FIELD_IDS):
        return None
    return profile


def learn_profile_from_confirmation(
    raw_text: str,
    confirmed: dict[str, Any],
    source_file: str,
    *,
    amount_context_line: str | None = None,
    invoice_context_line: str | None = None,
    customer_context_line: str | None = None,
    iban_context_line: str | None = None,
) -> dict[str, Any] | None:
    """Backward-compat wrapper routed through prepare_learnable_field_results()."""
    _ = amount_context_line, invoice_context_line, customer_context_line, iban_context_line
    field_results = prepare_learnable_field_results({}, dialog_confirmed=dict(confirmed or {}))
    return learn_profile_from_resolved_fields(
        raw_text=raw_text,
        source_file=source_file,
        field_results=field_results,
    )

