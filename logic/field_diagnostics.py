"""Universele diagnostics-mapping voor veldkandidaten."""

from __future__ import annotations

from typing import Any

from logic.payment_amounts import amount_to_decimal, format_eur_xml
from logic.validation import mask_iban_for_log
from parser.field_adapters import (
    field_candidate_from_amount_dict,
    field_candidate_from_ident_dict,
    field_result_from_amount,
    field_result_from_iban,
    field_result_from_ident,
    normalize_amount_result_dict,
)
from parser.field_model import FieldCandidate, FieldId
from parser.supplier_db import (
    CUSTOMER_ABSENT_STATE,
    customer_number_authoritative_value,
    customer_number_is_absent_or_none,
)

_CONTEXT_PREVIEW_MAX = 80

_AMOUNT_CONFLICT_SOURCES = frozenset(
    {"INCL_CONFLICT", "GENERIC_TOTAL_CONFLICT", "CONFLICTING_HIGH_CONFIDENCE", "LOAD_FAILED"}
)

_AMOUNT_WARNING_KEYS = frozenset(
    {
        "amount_low_confidence",
        "amount_tentative",
        "amount_ambiguous",
        "amount_uncertain",
    }
)

_AMOUNT_REASON_CODES = frozenset(
    {
        "missing_amount",
        "amount_ambiguous",
        "amount_uncertain",
        "amount_failed",
        "amount_low_confidence",
    }
)

_AMOUNT_NEEDS_ATTENTION = frozenset({"tentative", "ambiguous", "failed"})


def _field_key(prefix: str, code: str) -> str:
    s = str(code or "").strip()
    if not s:
        return ""
    return f"{prefix}.{s}"


def translate_final_decision_reason(code: str) -> str:
    s = str(code or "").strip()
    if not s:
        return "field.final_decision_reason._default"
    return _field_key("field.final_decision_reason", s)


def translate_rejection_reason(code: str) -> str:
    s = str(code or "").strip()
    if not s:
        return "field.rejection_reason._default"
    return _field_key("field.rejection_reason", s)


def translate_winner_reason(code: str) -> str:
    s = str(code or "").strip()
    if not s:
        return "field.winner_reason._default"
    return _field_key("field.winner_reason", s)


def translate_extraction_method(code: str) -> str:
    s = str(code or "").strip()
    if not s:
        return "field.extraction_method._default"
    return _field_key("field.extraction_method", s)


def translate_context_hint(code: str) -> str:
    s = str(code or "").strip()
    if not s:
        return "field.context_hint._default"
    return _field_key("field.context_hint", s)


def translate_source_label(code: str) -> str:
    s = str(code or "").strip()
    if not s:
        return "field.source_label._default"
    return _field_key("field.source_label", s)


def _score_breakdown_lines_nl(score_breakdown: dict[str, Any] | None) -> list[str]:
    if not isinstance(score_breakdown, dict):
        return []
    lines: list[str] = []
    for key, raw_value in score_breakdown.items():
        k = str(key or "").strip()
        if not k:
            continue
        lines.append(f"field.score_label.{k}|{raw_value}")
    return lines[:8]


def _format_amount_display(raw: object | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        formatted = format_eur_xml(amount_to_decimal(s)).replace(".", ",")
        return f"€ {formatted}"
    except ValueError:
        return None


def _context_preview(ctx: str) -> str | None:
    if not ctx:
        return None
    if len(ctx) > _CONTEXT_PREVIEW_MAX:
        return ctx[:_CONTEXT_PREVIEW_MAX] + "…"
    return ctx[:_CONTEXT_PREVIEW_MAX]


def _hybrid_override_meta(result_dict: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result_dict, dict):
        return {}
    out: dict[str, Any] = {}
    reason = str(result_dict.get("override_reason") or "").strip()
    if reason:
        out["override_reason"] = reason
        out["override_reason_nl"] = _field_key("field.override_reason", reason)
    trace = result_dict.get("decision_trace")
    if isinstance(trace, list) and trace:
        out["decision_trace"] = trace
        trace_human: list[dict[str, Any]] = []
        for entry in trace:
            if not isinstance(entry, dict):
                continue
            rendered = dict(entry)
            if str(entry.get("kind") or "") == "final":
                rendered["final_decision_reason_nl"] = translate_final_decision_reason(
                    str(entry.get("final_decision_reason") or "").strip()
                )
                winner = entry.get("winner") if isinstance(entry.get("winner"), dict) else {}
                if winner:
                    winner_reason = str(winner.get("winner_reason") or "").strip()
                    rendered["winner"] = {
                        **winner,
                        "source_nl": translate_source_label(
                            str(winner.get("source") or "").strip()
                        ),
                        "winner_reason_nl": (
                            translate_winner_reason(winner_reason) if winner_reason else None
                        ),
                    }
            else:
                rendered["source_nl"] = translate_source_label(
                    str(entry.get("source") or "").strip()
                )
                winner_reason = str(entry.get("winner_reason") or "").strip()
                if winner_reason:
                    rendered["winner_reason_nl"] = translate_winner_reason(winner_reason)
                reason_code = str(
                    entry.get("rejection_reason") or entry.get("excluded_reason") or ""
                ).strip()
                if reason_code:
                    reason_nl = translate_rejection_reason(reason_code)
                    rendered["rejection_reason_nl"] = reason_nl
                    rendered["excluded_reason_nl"] = reason_nl
            trace_human.append(rendered)
        if trace_human:
            out["decision_trace_human"] = trace_human
    if result_dict.get("user_overridden"):
        out["user_overridden"] = True
    prev = result_dict.get("previous_value")
    if prev is not None and str(prev).strip():
        out["previous_value"] = str(prev)
    return out


def map_field_candidate_for_diag(
    cand: FieldCandidate | dict[str, Any],
    *,
    field_id: FieldId,
    
) -> dict[str, Any]:
    cand_dict: dict[str, Any] | None = cand if isinstance(cand, dict) else None
    if isinstance(cand, dict):
        if field_id == "amount":
            fc = field_candidate_from_amount_dict(cand)
        else:
            fc = field_candidate_from_ident_dict(cand)
    else:
        fc = cand

    raw_val = fc.value
    val_str = str(raw_val) if raw_val is not None else ""
    src = str(fc.source or "").strip()
    context = str(fc.context or "").strip()
    preview = _context_preview(context)
    conf = int(fc.confidence or 0)
    extraction_method = str((cand_dict or {}).get("extraction_method") or "").strip()
    label_source = str((cand_dict or {}).get("label_source") or "").strip()
    match_type = str((cand_dict or {}).get("match_type") or "").strip().lower()
    label_reason = str((cand_dict or {}).get("label_reason") or "").strip()
    context_hint = str((cand_dict or {}).get("context_hint") or "").strip()
    parse_path = str((cand_dict or {}).get("parse_path") or "").strip()
    raw_detected = (cand_dict or {}).get("raw_detected")
    normalized_iso = (cand_dict or {}).get("normalized_iso")
    score_breakdown = (
        (cand_dict or {}).get("score_breakdown")
        if isinstance((cand_dict or {}).get("score_breakdown"), dict)
        else None
    )
    common_explain = {
        "context": context or None,
        "context_preview": preview,
        "extraction_method": extraction_method or None,
        "extraction_method_nl": (
            translate_extraction_method(extraction_method) if extraction_method else None
        ),
        "label_source": label_source or None,
        "match_type": match_type if match_type in {"label", "regex", "fallback"} else None,
        "label_reason": label_reason or None,
        "label_reason_nl": label_reason or None,
        "context_hint": context_hint or None,
        "context_hint_nl": translate_context_hint(context_hint) if context_hint else None,
        "parse_path": parse_path or None,
        "raw_detected": raw_detected,
        "normalized_iso": normalized_iso,
        "score_breakdown": score_breakdown,
        "score_breakdown_nl": _score_breakdown_lines_nl(score_breakdown),
    }

    if field_id == "amount":
        return {
            "value": val_str,
            "value_display": _format_amount_display(raw_val),
            "source": src,
            "source_nl": translate_source_label(src) if src else "",
            "confidence": conf,
            "type": str(fc.meta.get("type") or "unknown"),
            **common_explain,
        }

    if field_id == "iban":
        return {
            "value": val_str,
            "value_display": mask_iban_for_log(val_str) if val_str else val_str,
            "source": src,
            "source_nl": _field_key("field.iban.source", src),
            "confidence": conf,
            "label": str(fc.label or "").strip() or None,
            **common_explain,
        }

    return {
        "value": val_str,
        "value_display": val_str,
        "source": src,
        "source_nl": translate_source_label(src),
        "confidence": conf,
        "label": str(fc.label or "").strip() or None,
        **common_explain,
    }


def build_ident_field_diag_block(
    snap: dict[str, Any],
    field: str,
    *,
    payment_fallback: str | None = None,
) -> dict[str, Any]:
    """Diagnostics-weergave; voor ``customer_number`` is ``*_result`` authoritative."""
    scalar_legacy = str(snap.get(field) or "").strip() or None
    if not scalar_legacy and payment_fallback:
        scalar_legacy = str(payment_fallback).strip() or None
    extraction_source = str(snap.get("extraction_source") or "").strip().lower()
    profile_fields = snap.get("profile_fields")
    from_profile = extraction_source == "profile" or (
        isinstance(profile_fields, list) and field in profile_fields
    )

    fr_raw = snap.get(f"{field}_result")
    if field == "customer_number":
        probe: dict[str, Any] = dict(snap)
        if isinstance(fr_raw, dict):
            probe["customer_number_result"] = fr_raw
        if customer_number_is_absent_or_none(probe):
            user_absent = (
                isinstance(fr_raw, dict)
                and str(fr_raw.get("absence_state") or "").strip() == CUSTOMER_ABSENT_STATE
                and bool(fr_raw.get("user_selected"))
            )
            return {
                "value": None,
                "value_display": "diag.ident.status.no_customer_number",
                "status": "confirmed" if user_absent else "not_applicable",
                "needs_attention": False,
                "status_nl": (
                    "diag.ident.status.customer_absent_user"
                    if user_absent
                    else "diag.ident.status.customer_absent_profile"
                ),
                "candidates": [],
                "resolved_source": str(
                    (fr_raw or {}).get("source") or "NOT_PRESENT_SUPPLIER_LEVEL"
                ),
                **_hybrid_override_meta(fr_raw if isinstance(fr_raw, dict) else None),
            }
        if not isinstance(fr_raw, dict):
            auth = customer_number_authoritative_value(probe, scalar_fallback=scalar_legacy)
            return {
                "value": auth,
                "needs_attention": not auth,
                "status_nl": "diag.ident.status.via_profile" if auth and from_profile else (
                    "diag.ident.status.present" if auth else "diag.ident.status.missing"
                ),
                "candidates": [],
                "resolved_source": "profile" if from_profile else None,
            }

    legacy = scalar_legacy
    if not isinstance(fr_raw, dict):
        return {
            "value": legacy,
            "needs_attention": not legacy,
            "status_nl": "diag.ident.status.via_profile" if legacy and from_profile else (
                "diag.ident.status.present" if legacy else "diag.ident.status.missing"
            ),
            "candidates": [],
            "resolved_source": "profile" if from_profile else None,
        }

    field_id: FieldId = (
        "invoice_number" if field == "invoice_number" else "customer_number"
    )
    fr = field_result_from_ident(fr_raw, field_id=field_id)
    st = fr.status
    cands_out: list[dict[str, Any]] = [
        map_field_candidate_for_diag(c, field_id=field_id) for c in fr.candidates
    ]

    if field == "customer_number":
        val = customer_number_authoritative_value(
            {**snap, "customer_number_result": fr_raw},
            scalar_fallback=legacy,
        )
    else:
        val = legacy or (str(fr.selected_value).strip() if fr.selected_value else None) or None
    if (
        field != "customer_number"
        and val
        and legacy
        and str(fr_raw.get("value") or "").strip() not in ("", val)
    ):
        st = "confirmed"

    if val:
        if from_profile:
            cands_out = [
                {
                    "value": val,
                    "value_display": val,
                    "source": "profile",
                    "source_nl": "field.source_label.profile",
                    "confidence": 95,
                    "label": None,
                    "context_preview": None,
                    "is_resolved": True,
                }
            ]
            st = "confirmed"
        else:
            matching = [c for c in cands_out if str(c.get("value") or "").strip() == val]
            if not matching or not cands_out:
                cands_out = [
                    {
                        "value": val,
                        "value_display": val,
                        "source": "resolved",
                        "source_nl": "field.source_label.resolved",
                        "confidence": 95,
                        "label": None,
                        "context_preview": None,
                        "is_resolved": True,
                    },
                    *[
                        c
                        for c in cands_out
                        if str(c.get("value") or "").strip() != val
                    ],
                ]
                if st in ("confirmed", "tentative", "failed", "ambiguous", ""):
                    st = "confirmed"
            else:
                for c in cands_out:
                    c["is_resolved"] = str(c.get("value") or "").strip() == val
    elif st == "confirmed" and fr.selected_value:
        val = str(fr.selected_value).strip() or None

    needs = (
        not val
        and st in ("ambiguous", "tentative", "failed")
        and bool(cands_out)
    ) or (st in ("ambiguous", "tentative") and val and len(cands_out) > 1)

    if from_profile and val:
        status_nl = "diag.ident.status.via_profile"
    elif st == "confirmed" and val:
        status_nl = "diag.ident.status.present"
    elif st == "ambiguous":
        status_nl = "diag.ident.status.ambiguous"
    elif st == "tentative":
        status_nl = "diag.ident.status.tentative"
    elif val:
        status_nl = "diag.ident.status.present"
    else:
        status_nl = "diag.ident.status.missing"

    return {
        "value": val,
        "status": st or None,
        "needs_attention": needs,
        "status_nl": status_nl,
        "candidates": cands_out,
        "resolved_source": "profile" if from_profile and val else None,
        **_hybrid_override_meta(fr_raw if isinstance(fr_raw, dict) else None),
    }


def amount_needs_attention(
    status: str,
    reason_code: str,
    warning_keys: list[str],
) -> bool:
    if status in _AMOUNT_NEEDS_ATTENTION:
        return True
    if reason_code in _AMOUNT_REASON_CODES:
        return True
    return bool(_AMOUNT_WARNING_KEYS.intersection(warning_keys))


def build_amount_diag_block(
    snap: dict[str, Any],
    *,
    reason_code: str,
    warning_keys: list[str],
) -> dict[str, Any]:
    ar_raw = snap.get("amount_result") if isinstance(snap.get("amount_result"), dict) else None
    ar_norm = normalize_amount_result_dict(ar_raw)
    amount_status = ar_norm["status"]
    amount_source = ar_norm["source"]

    engine_reason_code: str | None = None
    engine_reason_nl: str | None = None
    if reason_code in _AMOUNT_REASON_CODES:
        engine_reason_code = reason_code
        engine_reason_nl = _field_key("error.reason", reason_code)

    amount_needs = amount_needs_attention(amount_status, reason_code, warning_keys)
    amount_warnings_nl = [
        _field_key("warning", k) for k in warning_keys if k in _AMOUNT_WARNING_KEYS
    ]

    detail_nl: str | None = None
    if amount_source in _AMOUNT_CONFLICT_SOURCES:
        detail_nl = _field_key("diag.amount.source", amount_source)

    fr = field_result_from_amount(ar_raw or {})
    candidates_out = [
        map_field_candidate_for_diag(c, field_id="amount")
        for c in fr.candidates
    ]
    if not candidates_out:
        for c in ar_norm.get("candidates") or []:
            if isinstance(c, dict):
                candidates_out.append(
                    map_field_candidate_for_diag(
                        c, field_id="amount"
                    )
                )

    return {
        "status": amount_status,
        "value": ar_norm["value"],
        "value_display": _format_amount_display(ar_norm["value"]),
        "confidence": ar_norm["confidence"],
        "source": amount_source,
        "candidates": candidates_out,
        "needs_attention": amount_needs,
        "status_nl": _field_key("diag.amount.status", amount_status),
        "detail_nl": detail_nl,
        "engine_reason_code": engine_reason_code,
        "engine_reason_nl": engine_reason_nl,
        "warnings_nl": amount_warnings_nl,
        **_hybrid_override_meta(ar_raw),
    }


def build_iban_diag_block(
    snap: dict[str, Any],
    *,
    payment_fallback: str | None = None,
    reason_code: str = "",
    warning_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Diagnostics voor IBAN: ``iban_result`` + legacy ``iban`` / ``all_ibans``."""
    warning_keys = warning_keys or []
    legacy = str(snap.get("iban") or "").strip() or None
    if not legacy and payment_fallback:
        legacy = str(payment_fallback).strip() or None

    iban_mismatch = bool(snap.get("iban_mismatch"))
    ocr_attempted = bool(snap.get("ocr_iban_attempted"))
    ocr_error = snap.get("ocr_iban_error")
    ocr_error_s = str(ocr_error).strip() if ocr_error else None

    fr_raw = snap.get("iban_result")
    if not isinstance(fr_raw, dict):
        all_ibans = snap.get("all_ibans")
        iban_list: list[str] = []
        if isinstance(all_ibans, list):
            for x in all_ibans:
                s = str(x or "").strip()
                if s:
                    iban_list.append(s)
        if legacy and legacy not in iban_list:
            iban_list.insert(0, legacy)
        elif legacy and not iban_list:
            iban_list = [legacy]

        cands_out = [
            {
                # Keep raw value for click/apply pipelines; mask only for display.
                "value": x,
                "value_display": mask_iban_for_log(x),
                "source": "ocr" if ocr_attempted and iban_list and x == iban_list[0] else "pdf_text",
                "source_nl": "field.iban.source.ocr" if ocr_attempted and iban_list and x == iban_list[0] else "field.iban.source.pdf_text",
                "confidence": 95 if x == legacy else 80,
                "is_resolved": x == legacy,
            }
            for x in iban_list
        ]
        iban_needs = (
            iban_mismatch
            or "iban_mismatch_supplier" in warning_keys
            or reason_code in {"missing_iban", "invalid_iban"}
            or not legacy
        )
        status_nl = "diag.ident.status.iban_present" if legacy else "diag.ident.status.iban_missing"
        if iban_mismatch or "iban_mismatch_supplier" in warning_keys:
            status_nl = "diag.ident.status.iban_mismatch"
        return {
            "masked_value": mask_iban_for_log(legacy) if legacy else "<none>",
            "all_ibans_masked": [mask_iban_for_log(x) for x in iban_list],
            "value": legacy,
            "status": "confirmed" if legacy else "failed",
            "candidates": cands_out,
            "mismatch": iban_mismatch,
            "ocr_attempted": ocr_attempted,
            "ocr_error": ocr_error_s,
            "needs_attention": iban_needs,
            "status_nl": status_nl,
            "warnings_nl": [],
        }

    fr = field_result_from_iban(fr_raw)
    st = fr.status
    cands_out: list[dict[str, Any]] = [
        map_field_candidate_for_diag(c, field_id="iban") for c in fr.candidates
    ]
    for c in cands_out:
        raw = str(c.get("value") or "").strip()
        if raw:
            c["value"] = raw
            c["value_display"] = mask_iban_for_log(raw)

    val = legacy or (str(fr.selected_value).strip() if fr.selected_value else None) or None
    if val and legacy and str(fr_raw.get("value") or "").strip() not in ("", val):
        st = "confirmed"

    if val:
        matching = [c for c in cands_out if str(c.get("value") or "").strip() == str(val)]
        if not matching or not cands_out:
            cands_out = [
                {
                    "value": val,
                    "value_display": mask_iban_for_log(val),
                    "source": "resolved",
                    "source_nl": "field.source_label.resolved",
                    "confidence": 95,
                    "label": None,
                    "context_preview": None,
                    "is_resolved": True,
                },
                *[
                    c
                    for c in cands_out
                    if str(c.get("value") or "").strip() != str(val)
                ],
            ]
            if st in ("confirmed", "tentative", "failed", "ambiguous", ""):
                st = "confirmed"
        else:
            for c in cands_out:
                c["is_resolved"] = str(c.get("value") or "").strip() == str(val)
    elif st == "confirmed" and fr.selected_value:
        val = str(fr.selected_value).strip() or None

    needs = (
        iban_mismatch
        or "iban_mismatch_supplier" in warning_keys
        or reason_code in {"missing_iban", "invalid_iban"}
        or (
            not val
            and st in ("ambiguous", "tentative", "failed")
            and bool(cands_out)
        )
        or (st in ("ambiguous", "tentative") and val and len(cands_out) > 1)
        or not val
    )

    if st == "confirmed" and val:
        status_nl = "diag.ident.status.iban_present"
    elif st == "ambiguous":
        status_nl = "diag.ident.status.iban_ambiguous"
    elif st == "tentative":
        status_nl = "diag.ident.status.iban_tentative"
    elif val:
        status_nl = "diag.ident.status.iban_present"
    else:
        status_nl = "diag.ident.status.iban_missing"
    if iban_mismatch or "iban_mismatch_supplier" in warning_keys:
        status_nl = "diag.ident.status.iban_mismatch"

    all_masked = [str(c.get("value_display") or c.get("value") or "") for c in cands_out if c.get("value")]

    return {
        "masked_value": mask_iban_for_log(val) if val else "<none>",
        "all_ibans_masked": all_masked,
        "value": val,
        "status": st or None,
        "candidates": cands_out,
        "mismatch": iban_mismatch,
        "ocr_attempted": ocr_attempted,
        "ocr_error": ocr_error_s,
        "needs_attention": needs,
        "status_nl": status_nl,
        "warnings_nl": [],
        **_hybrid_override_meta(fr_raw if isinstance(fr_raw, dict) else None),
    }
