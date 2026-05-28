"""Adapters tussen legacy AmountResult/IdentFieldResult en universeel FieldResult."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from parser.field_candidates import IdentFieldCandidate, IdentFieldResult
from parser.field_model import (
    FieldCandidate,
    FieldId,
    FieldResult,
    normalize_field_status,
)
from parser.pdf_parser import AmountCandidate, AmountResult

_CONTEXT_PREVIEW_MAX = 80


def canonicalize_legacy_result_dict(
    data: dict[str, Any] | None,
    *,
    field_id: FieldId,
    resolver_finalized: bool | None = None,
) -> dict[str, Any]:
    """Enforce one minimal canonical shape for all legacy field result dicts."""
    base = dict(data) if isinstance(data, dict) else {}

    raw_cands = base.get("candidates")
    candidates = [c for c in raw_cands if isinstance(c, dict)] if isinstance(raw_cands, list) else []

    raw_trace = base.get("decision_trace")
    decision_trace = list(raw_trace) if isinstance(raw_trace, list) else []

    status = normalize_field_status(str(base.get("status") or base.get("amount_status") or "failed"))
    try:
        confidence = int(base.get("confidence") if base.get("confidence") is not None else base.get("amount_confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0

    selected = base.get("selected_value")
    if selected is None:
        selected = base.get("value")
    if selected is None:
        selected = base.get("selected_amount")

    if field_id == "amount":
        dec = _parse_decimal(selected)
        selected_norm = str(dec) if dec is not None else None
        value = selected_norm
    else:
        selected_norm = str(selected).strip() if selected is not None else None
        if selected_norm == "":
            selected_norm = None
        value = selected_norm

    out = dict(base)
    out["selected_value"] = selected_norm
    out["candidates"] = candidates
    out["decision_trace"] = decision_trace
    out["override_reason"] = str(base.get("override_reason") or "").strip()
    out["confidence"] = confidence
    out["status"] = status

    finalized = bool(base.get("resolver_finalized"))
    if resolver_finalized is not None:
        finalized = bool(resolver_finalized)
    out["resolver_finalized"] = finalized

    out["value"] = value
    if field_id == "amount":
        out["selected_amount"] = value
        out["amount_confidence"] = confidence
        out["amount_status"] = status
    return out


def _hybrid_meta_from_dict(data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    trace = data.get("decision_trace")
    if not isinstance(trace, list):
        trace = []
    prev = data.get("previous_value")
    return {
        "user_overridden": bool(data.get("user_overridden")),
        "previous_value": prev,
        "decision_trace": list(trace),
        "override_reason": str(data.get("override_reason") or "").strip(),
        "resolver_finalized": bool(data.get("resolver_finalized")),
    }


def _apply_hybrid_meta_to_field_result(fr: FieldResult, meta: dict[str, Any]) -> None:
    fr.user_overridden = bool(meta.get("user_overridden"))
    fr.previous_value = meta.get("previous_value")
    trace = meta.get("decision_trace")
    fr.decision_trace = list(trace) if isinstance(trace, list) else []
    fr.override_reason = str(meta.get("override_reason") or "").strip()
    if meta.get("resolver_finalized"):
        fr.resolver_finalized = True


def _merge_hybrid_meta_into_legacy_dict(d: dict[str, Any], fr: FieldResult) -> dict[str, Any]:
    if fr.user_overridden:
        d["user_overridden"] = True
    if fr.previous_value is not None:
        d["previous_value"] = fr.previous_value
    if fr.decision_trace:
        d["decision_trace"] = list(fr.decision_trace)
    if fr.override_reason:
        d["override_reason"] = fr.override_reason
    if fr.resolver_finalized:
        d["resolver_finalized"] = True
    return d


def normalize_amount_result_dict(ar: dict[str, Any] | None) -> dict[str, Any]:
    """Gedeelde normalisatie (compatibel met logic.diagnostics._normalize_amount_result)."""
    if not isinstance(ar, dict):
        return {
            "status": "failed",
            "source": "",
            "value": None,
            "confidence": 0,
            "candidates": [],
        }
    status = str(ar.get("status") or ar.get("amount_status") or "failed").strip() or "failed"
    source = str(ar.get("source") or "").strip()
    val = ar.get("selected_value")
    if val is None:
        val = ar.get("value")
    if val is None:
        val = ar.get("selected_amount")
    conf = ar.get("confidence")
    if conf is None:
        conf = ar.get("amount_confidence")
    try:
        confidence = int(conf) if conf is not None else 0
    except (TypeError, ValueError):
        confidence = 0
    cands = ar.get("candidates")
    if not isinstance(cands, list):
        cands = []
    return {
        "status": status,
        "source": source,
        "value": str(val) if val is not None else None,
        "confidence": confidence,
        "candidates": cands,
        "user_selected": bool(ar.get("user_selected")),
    }


def _parse_decimal(raw: object | None) -> Decimal | None:
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    try:
        from logic.payment_amounts import amount_to_decimal

        return amount_to_decimal(str(raw))
    except (TypeError, ValueError, InvalidOperation):
        return None


def field_candidate_from_amount_dict(cand: dict[str, Any]) -> FieldCandidate:
    raw_v = cand.get("value")
    dec = _parse_decimal(raw_v)
    meta: dict[str, Any] = {}
    ctype = cand.get("type")
    if ctype:
        meta["type"] = str(ctype)
    return FieldCandidate(
        value=dec if dec is not None else raw_v,
        source=str(cand.get("source") or "").strip(),
        confidence=int(cand.get("confidence") or 0),
        context=str(cand.get("context") or ""),
        meta=meta,
    )


def field_candidate_from_amount(ac: AmountCandidate) -> FieldCandidate:
    meta: dict[str, Any] = {"type": getattr(ac, "type", "unknown")}
    return FieldCandidate(
        value=ac.value,
        source=ac.source,
        confidence=ac.confidence,
        context=ac.context,
        meta=meta,
    )


def field_candidate_from_ident_dict(cand: dict[str, Any]) -> FieldCandidate:
    return FieldCandidate(
        value=str(cand.get("value") or "").strip(),
        source=str(cand.get("source") or "").strip(),
        confidence=int(cand.get("confidence") or 0),
        context=str(cand.get("context") or ""),
        label=str(cand.get("label") or "").strip(),
    )


def field_candidate_from_ident(ic: IdentFieldCandidate) -> FieldCandidate:
    return FieldCandidate(
        value=ic.value,
        source=ic.source,
        confidence=ic.confidence,
        context=ic.context,
        label=ic.label,
    )


def field_result_from_amount(ar: AmountResult | dict[str, Any]) -> FieldResult:
    if isinstance(ar, AmountResult):
        candidates = [field_candidate_from_amount(c) for c in ar.candidates]
        fr = FieldResult(
            field_id="amount",
            candidates=candidates,
            selected_value=ar.value,
            confidence=ar.confidence,
            source=ar.source,
            status=normalize_field_status(ar.status),
            user_selected=bool(ar.user_selected),
        )
        fr.context = fr.resolved_context()
        return fr
    norm = normalize_amount_result_dict(ar if isinstance(ar, dict) else None)
    candidates: list[FieldCandidate] = []
    for c in norm.get("candidates") or []:
        if isinstance(c, dict):
            candidates.append(field_candidate_from_amount_dict(c))
    val = _parse_decimal(norm.get("value"))
    user_sel = bool((ar or {}).get("user_selected")) if isinstance(ar, dict) else False
    fr = FieldResult(
        field_id="amount",
        candidates=candidates,
        selected_value=val,
        confidence=int(norm.get("confidence") or 0),
        source=str(norm.get("source") or "UNKNOWN"),
        status=normalize_field_status(str(norm.get("status"))),
        user_selected=user_sel,
    )
    if isinstance(ar, dict):
        _apply_hybrid_meta_to_field_result(fr, _hybrid_meta_from_dict(ar))
    fr.context = fr.resolved_context()
    return fr


def amount_result_from_field_result(fr: FieldResult) -> AmountResult:
    candidates: list[AmountCandidate] = []
    for c in fr.candidates:
        dec = c.value if isinstance(c.value, Decimal) else _parse_decimal(c.value)
        if dec is None:
            continue
        ctype = str(c.meta.get("type") or "unknown")
        candidates.append(
            AmountCandidate(
                value=dec,
                source=c.source,
                confidence=c.confidence,
                context=c.context,
                type=ctype,  # type: ignore[arg-type]
            )
        )
    val = fr.selected_value
    if val is not None and not isinstance(val, Decimal):
        val = _parse_decimal(val)
    return AmountResult(
        candidates=candidates,
        value=val,
        confidence=fr.confidence,
        source=fr.source,
        status=fr.status,
        user_selected=fr.user_selected,
    )


def field_result_from_ident(
    fr: IdentFieldResult | dict[str, Any],
    *,
    field_id: FieldId,
) -> FieldResult:
    if isinstance(fr, IdentFieldResult):
        candidates = [field_candidate_from_ident(c) for c in fr.candidates]
        result = FieldResult(
            field_id=field_id,
            candidates=candidates,
            selected_value=fr.value,
            confidence=fr.confidence,
            source=fr.source,
            status=normalize_field_status(fr.status),
            user_selected=bool(fr.user_selected),
        )
        result.context = result.resolved_context()
        return result
    if not isinstance(fr, dict):
        return FieldResult(field_id=field_id, status="failed", source="NOT_FOUND")
    candidates = []
    for c in fr.get("candidates") or []:
        if isinstance(c, dict):
            candidates.append(field_candidate_from_ident_dict(c))
    val = fr.get("selected_value")
    if val is None:
        val = fr.get("value")
    if val is not None:
        val = str(val).strip() or None
    result = FieldResult(
        field_id=field_id,
        candidates=candidates,
        selected_value=val,
        confidence=int(fr.get("confidence") or 0),
        source=str(fr.get("source") or "UNKNOWN"),
        status=normalize_field_status(str(fr.get("status"))),
        user_selected=bool(fr.get("user_selected")),
    )
    _apply_hybrid_meta_to_field_result(result, _hybrid_meta_from_dict(fr))
    result.context = result.resolved_context()
    return result


def ident_field_result_from_field_result(fr: FieldResult) -> IdentFieldResult:
    candidates = [
        IdentFieldCandidate(
            value=str(c.value or ""),
            source=c.source,
            confidence=c.confidence,
            context=c.context,
            label=c.label,
        )
        for c in fr.candidates
    ]
    val = fr.selected_value
    if val is not None:
        val = str(val).strip() or None
    return IdentFieldResult(
        candidates=candidates,
        value=val,
        confidence=fr.confidence,
        source=fr.source,
        status=fr.status,
        user_selected=fr.user_selected,
    )


def field_result_from_iban(
    fr: IdentFieldResult | dict[str, Any],
) -> FieldResult:
    return field_result_from_ident(fr, field_id="iban")


def iban_result_from_field_result(fr: FieldResult) -> IdentFieldResult:
    return ident_field_result_from_field_result(fr)


def field_result_to_legacy_dict(fr: FieldResult) -> dict[str, Any]:
    if fr.field_id == "amount":
        d = amount_result_from_field_result(fr).to_dict()
    else:
        d = ident_field_result_from_field_result(fr).to_dict()
    d = _merge_hybrid_meta_into_legacy_dict(d, fr)
    return canonicalize_legacy_result_dict(
        d,
        field_id=fr.field_id,
        resolver_finalized=fr.resolver_finalized,
    )


def field_result_from_legacy_dict(
    data: dict[str, Any] | None,
    *,
    field_id: FieldId,
) -> FieldResult:
    if field_id == "amount":
        return field_result_from_amount(data or {})
    if field_id == "iban":
        return field_result_from_iban(data or {})
    return field_result_from_ident(data or {}, field_id=field_id)
