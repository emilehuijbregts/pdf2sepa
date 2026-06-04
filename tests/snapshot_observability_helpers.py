"""Phase A.1 observability helpers (test-only). No production behavior changes."""

from __future__ import annotations

import copy
import re
from decimal import Decimal
from typing import Any

from parser.field_adapters import field_result_from_legacy_dict
from parser.field_candidates import IdentFieldCandidate, candidate_rank_key, rank_key
from parser.field_resolver import _resolver_rank_key
from parser.field_model import ALL_FIELD_IDS, FieldCandidate, FieldId, FieldResult
from parser.field_resolver import (
    _candidate_rank_tuple,
    _generic_candidate,
    resolve_field,
)
from parser.hybrid_field_apply import (
    _build_db_override_candidates,
    _cap_amount_tentative,
    _generic_result_dict,
    _profile_candidate,
)
from parser.pdf_parser import AmountCandidate
from parser.profile_extractor import extract_with_profile, validate_profile
from parser.supplier_db import SupplierDB


def _value_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return str(value)
    return str(value).strip()


def _parse_rank_key(field_id: FieldId, cand: FieldCandidate) -> list[Any]:
    if field_id == "amount":
        meta = cand.meta if isinstance(cand.meta, dict) else {}
        ctype = str(meta.get("type") or "unknown")
        try:
            val = Decimal(str(cand.value)) if cand.value is not None else Decimal("0")
        except Exception:
            val = Decimal("0")
        ac = AmountCandidate(
            value=val,
            source=str(cand.source or ""),
            confidence=int(cand.confidence or 0),
            context=str(cand.context or ""),
            type=ctype,  # type: ignore[arg-type]
        )
        fc = FieldCandidate(
            value=ac.value,
            source=ac.source,
            confidence=ac.confidence,
            context=ac.context,
            meta={"field_id": "amount", "type": ctype},
        )
        return list(rank_key("amount", fc, context="parse"))
    ident = IdentFieldCandidate(
        value=str(cand.value) if cand.value is not None else "",
        source=str(cand.source or ""),
        confidence=int(cand.confidence or 0),
        context=str(cand.context or ""),
        label=str(cand.label or ""),
        meta=dict(cand.meta or {}),
    )
    if "field_id" not in ident.meta:
        ident.meta["field_id"] = field_id
    prefer_k = field_id == "customer_number"
    return list(candidate_rank_key(ident, prefer_k_prefix=prefer_k))


def resolver_rank_key(field_id: FieldId, cand: FieldCandidate) -> tuple[Any, ...]:
    """Production resolver rank key (Phase B2: ``field_resolver._resolver_rank_key``)."""
    return _resolver_rank_key(field_id, cand)


def _rank_key_kind(field_id: FieldId, *, stage: str) -> str:
    if stage == "parse":
        return "amount_pick_key" if field_id == "amount" else "ident_candidate_rank_key"
    if field_id == "amount":
        return "resolver_amount_payable_score_first"
    if field_id == "invoice_date":
        return "resolver_ident_with_date_tiebreak"
    return "resolver_ident_candidate_rank_key"


def _serialize_candidate(
    field_id: FieldId,
    cand: FieldCandidate,
    *,
    stage: str,
) -> dict[str, Any]:
    meta = cand.meta if isinstance(cand.meta, dict) else {}
    if stage == "parse":
        rk = _parse_rank_key(field_id, cand)
    else:
        rk = list(resolver_rank_key(field_id, cand))
    out: dict[str, Any] = {
        "value": _value_str(cand.value),
        "source": str(cand.source or ""),
        "confidence": int(cand.confidence or 0),
        "rank_key": rk,
        "rank_key_kind": _rank_key_kind(field_id, stage=stage),
    }
    if field_id == "amount":
        ps = meta.get("payable_score")
        if ps is not None:
            out["payable_score"] = int(ps)
        if meta.get("type"):
            out["amount_type"] = str(meta.get("type"))
    return out


def _winner_from_field_result(fr: FieldResult | None) -> dict[str, Any]:
    if fr is None:
        return {
            "value": "",
            "source": "",
            "status": "failed",
            "confidence": 0,
        }
    return {
        "value": _value_str(fr.selected_value),
        "source": str(fr.source or ""),
        "status": str(fr.status or ""),
        "confidence": int(fr.confidence or 0),
    }


def _ordering_values(candidates: list[dict[str, Any]]) -> list[str]:
    return [str(c.get("value") or "") for c in candidates]


def _field_result_from_parse_invoice(inv: dict[str, Any], field_id: FieldId) -> FieldResult:
    return field_result_from_legacy_dict(
        inv.get(
            {
                "amount": "amount_result",
                "invoice_number": "invoice_number_result",
                "customer_number": "customer_number_result",
                "iban": "iban_result",
                "vat_number": "vat_number_result",
                "kvk_number": "kvk_number_result",
                "invoice_date": "invoice_date_result",
                "email_domain": "email_domain_result",
            }[field_id]
        ),
        field_id=field_id,
    )


def build_parse_stage(inv_parse: dict[str, Any], field_id: FieldId) -> dict[str, Any]:
    fr = _field_result_from_parse_invoice(inv_parse, field_id)
    serialized = [_serialize_candidate(field_id, c, stage="parse") for c in fr.candidates]
    serialized.sort(key=lambda c: (c["rank_key"], c["value"]), reverse=True)
    return {
        "call_site": "build_ident_field_result"
        if field_id != "amount"
        else "pdf_parser._select_amount",
        "candidates": serialized,
        "ordering": _ordering_values(serialized),
        "winner": _winner_from_field_result(fr),
    }


def _hybrid_resolver_inputs(
    inv_parse: dict[str, Any],
    inv_matched: dict[str, Any],
    supplier: dict | None,
    db: SupplierDB,
) -> tuple[dict[FieldId, FieldResult], dict[FieldId, list[FieldCandidate]], dict[FieldId, FieldCandidate | None], dict[str, Any]]:
    """Rebuild generic + overrides + user_pick exactly like apply_hybrid_field_extraction."""
    meta: dict[str, Any] = {
        "use_profile": False,
        "amount_status": "confirmed",
        "amount_tentative_cap": False,
    }
    overrides_by_field: dict[FieldId, list[FieldCandidate]] = {fid: [] for fid in ALL_FIELD_IDS}
    user_pick_by_field: dict[FieldId, FieldCandidate | None] = {fid: None for fid in ALL_FIELD_IDS}
    generic_by_field: dict[FieldId, FieldResult] = {}

    if not supplier:
        for field_id in ALL_FIELD_IDS:
            generic_by_field[field_id] = _field_result_from_parse_invoice(inv_parse, field_id)
        return generic_by_field, overrides_by_field, user_pick_by_field, meta

    ms = str(inv_matched.get("match_status") or "").strip().lower()
    mi = inv_matched.get("match_info") if isinstance(inv_matched.get("match_info"), dict) else {}
    use_profile = ms == "confirmed" or bool(mi.get("iban_match"))
    meta["use_profile"] = use_profile
    amount_status = "confirmed" if ms == "confirmed" else ("tentative" if mi.get("iban_match") else "confirmed")
    meta["amount_status"] = amount_status

    profile = db.get_extraction_profile(supplier["name"]) if use_profile else None
    raw = inv_parse.get("raw_text") or inv_matched.get("raw_text")
    extracted: dict[str, Any] = {fid: None for fid in ALL_FIELD_IDS}
    profile_validated = False
    if profile and raw:
        extracted = extract_with_profile(raw, profile)
        profile_validated = validate_profile(raw, profile)

    from parser.hybrid_field_apply import _amount_decimal
    from logic.validation import clean_iban

    for field_id in ALL_FIELD_IDS:
        generic_fr = field_result_from_legacy_dict(
            _generic_result_dict(inv_parse, inv_matched, field_id),
            field_id=field_id,
        )
        generic_by_field[field_id] = generic_fr

        overrides: list[FieldCandidate] = []
        overrides.extend(_build_db_override_candidates(field_id, supplier, inv_parse, db))

        prof_val = extracted.get(field_id)
        if prof_val is not None and profile and field_id in profile:
            if field_id == "amount":
                cand_val = _amount_decimal(prof_val)
            elif field_id == "iban":
                cand_val = clean_iban(str(prof_val)) or None
            else:
                cand_val = str(prof_val).strip()
            if cand_val is not None:
                field_spec = {field_id: profile[field_id]}
                field_valid = profile_validated or validate_profile(
                    raw,
                    field_spec,
                    {field_id: prof_val},
                )
                overrides.append(
                    _profile_candidate(
                        field_id,
                        cand_val,
                        validated=field_valid,
                        context=str(profile.get(field_id, {}).get("label") or ""),
                    )
                )
        overrides_by_field[field_id] = overrides

        if generic_fr.user_overridden and generic_fr.selected_value is not None:
            user_pick_by_field[field_id] = FieldCandidate(
                value=generic_fr.selected_value,
                source=generic_fr.source or "USER_PICKED",
                confidence=100,
                context=str(generic_fr.context or ""),
            )

    return generic_by_field, overrides_by_field, user_pick_by_field, meta


def _resolver_merged_pool(
    field_id: FieldId,
    generic: FieldResult,
    overrides: list[FieldCandidate],
    user_pick: FieldCandidate | None,
) -> list[FieldCandidate]:
    all_cands: list[FieldCandidate] = list(generic.candidates)
    gen_cand = _generic_candidate(generic)
    if gen_cand is not None:
        all_cands.append(gen_cand)
    all_cands.extend(overrides)
    if user_pick is not None:
        all_cands.append(user_pick)
    dedup: dict[tuple[str, str], FieldCandidate] = {}
    for cand in all_cands:
        key = (str(cand.source or ""), str(cand.value or ""))
        best = dedup.get(key)
        if best is None or resolver_rank_key(field_id, cand) > resolver_rank_key(field_id, best):
            dedup[key] = cand
    return sorted(dedup.values(), key=lambda c: resolver_rank_key(field_id, c), reverse=True)


def build_resolver_stage(
    inv_parse: dict[str, Any],
    inv_matched: dict[str, Any],
    supplier: dict | None,
    db: SupplierDB,
    field_id: FieldId,
) -> dict[str, Any]:
    generic_by_field, overrides_by_field, user_pick_by_field, meta = _hybrid_resolver_inputs(
        inv_parse, inv_matched, supplier, db
    )
    generic = generic_by_field[field_id]
    overrides = overrides_by_field[field_id]
    user_pick = user_pick_by_field[field_id]

    pool = _resolver_merged_pool(field_id, generic, overrides, user_pick)
    serialized = [_serialize_candidate(field_id, c, stage="resolver") for c in pool]

    resolved_fr = resolve_field(field_id, generic, overrides, user_pick=user_pick)
    resolved_dict = resolved_fr.to_dict() if hasattr(resolved_fr, "to_dict") else {}

    cap_applied = False
    if (
        field_id == "amount"
        and meta.get("amount_status") == "tentative"
        and str(resolved_dict.get("source") or "") == "profile"
    ):
        capped = _cap_amount_tentative(resolved_dict)
        cap_applied = capped.get("status") != resolved_dict.get("status") or capped.get(
            "confidence"
        ) != resolved_dict.get("confidence")
        if cap_applied:
            resolved_dict = capped

    trace = list(resolved_fr.decision_trace or [])
    final_reason = ""
    for entry in reversed(trace):
        if isinstance(entry, dict) and entry.get("kind") == "final":
            final_reason = str(entry.get("final_decision_reason") or "")
            break

    return {
        "call_site": "resolve_field",
        "hybrid_meta": {
            "use_profile": bool(meta.get("use_profile")),
            "amount_status": str(meta.get("amount_status") or ""),
            "amount_tentative_cap_applied": cap_applied,
        },
        "candidates": serialized,
        "ordering": _ordering_values(serialized),
        "winner": {
            "value": _value_str(resolved_fr.selected_value),
            "source": str(resolved_fr.source or ""),
            "status": str(resolved_fr.status or ""),
            "confidence": int(resolved_fr.confidence or 0),
        },
        "override_reason": str(resolved_fr.override_reason or ""),
        "resolver_finalized": bool(resolved_fr.resolver_finalized),
        "decision_trace_final_reason": final_reason,
    }


def build_production_outcome(inv_matched: dict[str, Any], field_id: FieldId) -> dict[str, Any]:
    key = {
        "amount": "amount_result",
        "invoice_number": "invoice_number_result",
        "customer_number": "customer_number_result",
        "iban": "iban_result",
        "vat_number": "vat_number_result",
        "kvk_number": "kvk_number_result",
        "invoice_date": "invoice_date_result",
        "email_domain": "email_domain_result",
    }[field_id]
    raw = inv_matched.get(key)
    if not isinstance(raw, dict):
        raw = {}
    fr = field_result_from_legacy_dict(raw, field_id=field_id)
    trace = list(fr.decision_trace or [])
    final_reason = ""
    for entry in reversed(trace):
        if isinstance(entry, dict) and entry.get("kind") == "final":
            final_reason = str(entry.get("final_decision_reason") or "")
            break
    return {
        "winner": _winner_from_field_result(fr),
        "resolver_finalized": bool(raw.get("resolver_finalized") or fr.resolver_finalized),
        "override_reason": str(raw.get("override_reason") or fr.override_reason or ""),
        "user_overridden": bool(raw.get("user_overridden") or fr.user_overridden),
        "user_selected": bool(raw.get("user_selected") or fr.user_selected),
        "decision_trace_final_reason": final_reason,
    }


def build_field_observability(
    inv_parse: dict[str, Any],
    inv_matched: dict[str, Any],
    supplier: dict | None,
    db: SupplierDB,
    field_id: FieldId,
) -> dict[str, Any]:
    return {
        "parse_stage": build_parse_stage(inv_parse, field_id),
        "resolver_stage": build_resolver_stage(inv_parse, inv_matched, supplier, db, field_id),
        "production": build_production_outcome(inv_matched, field_id),
    }


def capture_parse_before_match(
    invoices: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    from logic.golden_dataset import pdf_filename

    out: dict[str, dict[str, Any]] = {}
    for inv in invoices:
        key = pdf_filename(inv.get("source_file"))
        if not key or key in out:
            continue
        out[key] = copy.deepcopy(inv)
    return out


def supplier_for_matched(inv_matched: dict[str, Any], db: SupplierDB) -> dict | None:
    name = str(inv_matched.get("supplier_name") or "").strip()
    if not name:
        return None
    for s in db.suppliers or []:
        if isinstance(s, dict) and str(s.get("name") or "").strip() == name:
            return s
    return None
