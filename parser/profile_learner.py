"""Profile learning: observe resolver-final FieldResults and derive extraction specs."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from parser.field_model import (
    ALL_FIELD_IDS,
    FieldId,
    FieldResult,
    is_resolver_final_field_result,
    normalize_field_value,
    field_result_from_result_dict,
    _RESULT_KEY_BY_FIELD,
)
from parser.pdf_parser import (
    _EMAIL_RE,
    _INVOICE_DATE_LABEL_RE,
    _VAT_RE,
)
from parser.profile_extractor import _find_label_line
from parser.profile_strategy_engine import (
    StrategyContext,
    StrategyFieldResult,
    extend_label_span,
    locate_string_position,
    run_strategies,
    split_lines,
    validate_field_spec,
)

logger = logging.getLogger(__name__)

_DIALOG_OVERLAY_FIELD_IDS: frozenset[FieldId] = frozenset(
    {"amount", "invoice_number", "customer_number"}
)

IDENTIFICATION_LEARN_FIELDS: tuple[FieldId, ...] = ("invoice_number", "customer_number")
AMOUNT_LEARN_FIELDS: tuple[FieldId, ...] = ("amount",)

# Populated during learn_profile_from_resolved_fields for outcome tracing.
_last_strategy_results: dict[FieldId, StrategyFieldResult] = {}


def get_last_strategy_results() -> dict[FieldId, StrategyFieldResult]:
    """Return strategy traces from the most recent learn_profile_from_resolved_fields call."""
    return dict(_last_strategy_results)


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


def _log_strategy_failure(field_id: FieldId, result: StrategyFieldResult) -> None:
    logger.debug(
        "profile_learn_rejected field=%s trace=%s attempts=%s",
        field_id,
        result.validation_trace,
        [a.to_dict() for a in result.all_attempted_strategies],
    )


def _learn_field_spec(
    raw_text: str,
    field_id: FieldId,
    fr: FieldResult,
) -> dict[str, Any] | None:
    confirmed = normalize_field_value(field_id, fr.selected_value)
    if confirmed is None:
        return None

    if field_id in STRATEGY_REGISTRY_FIELDS:
        ctx = StrategyContext(
            field_id=field_id,
            raw_text=raw_text,
            confirmed_value=confirmed,
            context_line=fr.resolved_context(target_value=confirmed),
            mode="learn",
        )
        result = run_strategies(field_id, ctx)
        _last_strategy_results[field_id] = result
        if result.profile_spec is None:
            _log_strategy_failure(field_id, result)
            return None
        spec = dict(result.profile_spec)
        conf = int(fr.confidence or 0)
        if conf > 0:
            spec["confidence"] = conf
        return spec

    lines = split_lines(raw_text)
    context = fr.resolved_context(target_value=confirmed)

    if field_id == "invoice_date":
        spec = _learn_field_invoice_date(raw_text, lines, str(confirmed), context or "")
    elif field_id == "vat_number":
        spec = _learn_field_vat_number(raw_text, lines, str(confirmed))
    elif field_id == "email_domain":
        spec = _learn_field_email_domain(raw_text, lines, str(confirmed))
    else:
        return None

    if spec is None:
        return None
    conf = int(fr.confidence or 0)
    if conf > 0:
        spec = dict(spec)
        spec["confidence"] = conf
    return spec


STRATEGY_REGISTRY_FIELDS: frozenset[FieldId] = frozenset(
    {"amount", "invoice_number", "customer_number", "iban"}
)


def _find_best_label_legacy(
    lines: list[str],
    value_line_idx: int,
    value_pos_in_line: int,
    field: str,
) -> tuple[str, int] | None:
    from parser.profile_strategy_engine import _label_candidates_on_line

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


def _infer_strategy_for_field_legacy(
    lines: list[str],
    label_line_idx: int,
    value_line_idx: int,
    value_pos_in_line: int,
    field: str,
) -> str | None:
    if field == "iban":
        if value_line_idx == label_line_idx + 1:
            return "next_line_first_iban"
        if value_line_idx == label_line_idx:
            return "same_line_first_iban"
        return None
    if value_line_idx == label_line_idx + 1:
        return "next_line_first_token"
    if value_line_idx == label_line_idx:
        return "same_line_after_colon"
    return None


def _learn_field_invoice_date(
    raw_text: str,
    lines: list[str],
    confirmed_iso: str,
    context_line: str,
) -> dict[str, str] | None:
    target = normalize_field_value("invoice_date", confirmed_iso)
    if not isinstance(target, str) or not target:
        return None

    for i, line in enumerate(lines):
        for m in _INVOICE_DATE_LABEL_RE.finditer(line or ""):
            label_text = extend_label_span(line, m.start(), m.end())
            tail = (line or "")[m.end() :]
            for dm in re.finditer(
                r"\b\d{1,4}[\./-]\d{1,2}[\./-]\d{1,4}\b|\b\d{1,2}\s+[A-Za-z]{3,}\.?\s+\d{4}\b",
                tail,
            ):
                tok = dm.group(0)
                if normalize_field_value("invoice_date", tok) == target:
                    spec = {
                        "label": label_text,
                        "strategy": "same_line_after_colon",
                        "confirmed_value": target,
                    }
                    if validate_field_spec(raw_text, "invoice_date", spec, target):
                        return spec
            if i + 1 < len(lines):
                nxt = (lines[i + 1] or "").strip()
                if nxt:
                    for dm in re.finditer(
                        r"\b\d{1,4}[\./-]\d{1,2}[\./-]\d{1,4}\b|\b\d{1,2}\s+[A-Za-z]{3,}\.?\s+\d{4}\b",
                        nxt,
                    ):
                        tok = dm.group(0)
                        if normalize_field_value("invoice_date", tok) == target:
                            spec = {
                                "label": label_text,
                                "strategy": "next_line_first_token",
                                "confirmed_value": target,
                            }
                            if validate_field_spec(raw_text, "invoice_date", spec, target):
                                return spec
    if context_line:
        idx = _find_label_line(split_lines(raw_text), context_line)
        if idx is not None and 0 <= idx < len(lines):
            ln = lines[idx]
            for m in _INVOICE_DATE_LABEL_RE.finditer(ln or ""):
                label_text = extend_label_span(ln, m.start(), m.end())
                spec = {
                    "label": label_text,
                    "strategy": "same_line_after_colon",
                    "confirmed_value": target,
                }
                if validate_field_spec(raw_text, "invoice_date", spec, target):
                    return spec
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
        located = locate_string_position(raw_text, tok)
        if located is None:
            continue
        _pos, line_idx, pos_in_line, _actual = located
        found = _find_best_label_legacy(lines, line_idx, pos_in_line, "vat_number")
        if found is None:
            continue
        label_text, label_line_idx = found
        strategy = _infer_strategy_for_field_legacy(
            lines, label_line_idx, line_idx, pos_in_line, "vat_number"
        )
        if strategy is None:
            continue
        spec = {"label": label_text, "strategy": strategy, "confirmed_value": target}
        if validate_field_spec(raw_text, "vat_number", spec, target):
            return spec
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
        located = locate_string_position(raw_text, dom)
        if located is None:
            continue
        _pos, line_idx, pos_in_line, _actual = located
        found = _find_best_label_legacy(lines, line_idx, pos_in_line, "email_domain")
        if found is None:
            continue
        label_text, label_line_idx = found
        strategy = _infer_strategy_for_field_legacy(
            lines, label_line_idx, line_idx, pos_in_line, "email_domain"
        )
        if strategy is None:
            continue
        spec = {"label": label_text, "strategy": strategy, "confirmed_value": target}
        if validate_field_spec(raw_text, "email_domain", spec, target):
            return spec
    return None


def learn_profile_from_resolved_fields(
    *,
    raw_text: str,
    source_file: str,
    field_results: dict[FieldId, FieldResult],
) -> dict[str, Any] | None:
    """Learn profile specs from resolver-final FieldResults only."""
    global _last_strategy_results
    _last_strategy_results = {}
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
