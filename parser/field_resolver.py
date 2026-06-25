"""Hybride per-veld resolver: generic primair, profile/db_master als override."""

from __future__ import annotations

import re
from typing import Any

from parser.field_candidates import (
    IdentFieldCandidate,
    candidate_rank_key,
    rank_candidates,
    rank_key,
)
from parser.field_model import FieldCandidate, FieldId, FieldResult, normalize_field_status

HIGH_CONFIDENCE = 85
LOW_CONFIDENCE = 70
OVERRIDE_MARGIN = 10
TRACE_TOP_N = 10

_OVERRIDE_SOURCES = frozenset({"profile", "db_master"})
_USER_SOURCES = frozenset({"manual", "USER_PICKED", "user", "picked"})

PROFILE_CONFIDENCE_VALIDATED = 90
PROFILE_CONFIDENCE_UNVALIDATED = 60
DB_MASTER_IBAN_CONFIDENCE = 92
DB_MASTER_CUSTOMER_CONFIDENCE = 88

_MATCH_TYPE_PRIORITY = {
    "label": 3,
    "regex": 2,
    "fallback": 1,
}

_SPECIFIC_LABEL_HINT_RE = re.compile(
    r"(?i)\b(?:factuurnummer|invoice\s*(?:number|no\.?|nr\.?)|rechnungsnummer|"
    r"klantnummer|klantcode|customer\s*(?:number|code|id)|"
    r"polisnummer|relatienummer|contractnummer|btw|vat|kvk|iban|e-?mail)\b"
)
_GENERIC_LABEL_HINT_RE = re.compile(
    r"(?i)\b(?:factuur|invoice|klant|debiteur|customer|nummer|nr\.?|code)\b"
)

_SOURCE_PRIORITY_EXACT: dict[str, int] = {
    "manual": 200,
    "user_picked": 199,
    "db_master": 180,
    "profile": 170,
    "label_block_same_line": 130,
    "label": 126,
    "label_block_next_line": 124,
    "label_next_line": 122,
    "tabular": 118,
    "extra": 116,
    "datum_nummer_table": 110,
    "invoice_nr_van_date": 107,
    "invoice_date_label_same_line": 106,
    "invoice_date_label_next_line": 104,
    "factuur_colon": 98,
    "factuur_plain": 97,
    "factuur_prefixed_digits": 96,
    "date_invoice_line": 95,
    "year_slash_ref": 94,
    "fallback_missing": 1,
    "resolved": 2,
}


def _is_override_source(source: str) -> bool:
    return str(source or "").strip().lower() in _OVERRIDE_SOURCES or source in (
        "profile",
        "db_master",
    )


def _is_user_source(source: str) -> bool:
    s = str(source or "").strip()
    return s in _USER_SOURCES or s.lower() in {"manual", "user", "picked"}


def _values_equal(field_id: FieldId, a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    if field_id == "amount":
        try:
            from logic.payment_amounts import amount_to_decimal

            return amount_to_decimal(str(a)) == amount_to_decimal(str(b))
        except (TypeError, ValueError):
            return str(a).strip() == str(b).strip()
    if field_id == "iban":
        from logic.validation import clean_iban

        return clean_iban(str(a)) == clean_iban(str(b))
    return str(a).strip() == str(b).strip()


def _generic_candidate(generic: FieldResult) -> FieldCandidate | None:
    def _to_field_candidate(c: FieldCandidate) -> FieldCandidate:
        return FieldCandidate(
            value=c.value,
            source=c.source or "generic",
            confidence=int(c.confidence or 0),
            context=str(c.context or ""),
            label=str(c.label or ""),
            meta=dict(c.meta or {}),
        )

    if generic.selected_value is not None:
        matches = [
            c
            for c in generic.candidates
            if _values_equal(generic.field_id, c.value, generic.selected_value)
        ]
        if matches:
            best = max(matches, key=_ident_rank_tuple)
            chosen = _to_field_candidate(best)
            if not chosen.source:
                chosen.source = generic.source or "generic"
            if not chosen.context:
                chosen.context = str(generic.context or "")
            return chosen
        return FieldCandidate(
            value=generic.selected_value,
            source=generic.source or "generic",
            confidence=int(generic.confidence or 0),
            context=str(generic.context or ""),
        )
    if generic.candidates:
        best = max(generic.candidates, key=_ident_rank_tuple)
        return _to_field_candidate(best)
    return None


def _trace_entry(
    cand: FieldCandidate,
    *,
    considered: bool,
    win: bool,
    excluded_reason: str | None = None,
    rank: int | None = None,
    winner_reason: str | None = None,
    loser_reason: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "value": cand.value,
        "source": cand.source,
        "confidence": int(cand.confidence or 0),
        "considered": considered,
        "win": win,
    }
    if rank is not None:
        entry["rank"] = int(rank)
    if excluded_reason:
        entry["excluded_reason"] = excluded_reason
        entry["rejection_reason"] = excluded_reason
    if winner_reason:
        entry["winner_reason"] = winner_reason
    if loser_reason:
        entry["loser_reason"] = loser_reason
    return entry


def _to_ident_candidate(cand: FieldCandidate) -> IdentFieldCandidate:
    return IdentFieldCandidate(
        value=str(cand.value) if cand.value is not None else "",
        source=str(cand.source or ""),
        confidence=int(cand.confidence or 0),
        context=str(cand.context or ""),
        label=str(cand.label or ""),
        meta=dict(cand.meta or {}),
    )


def _candidate_match_type(cand: FieldCandidate) -> str:
    meta = cand.meta if isinstance(cand.meta, dict) else {}
    mt = str(meta.get("match_type") or "").strip().lower()
    if mt in {"label", "regex", "fallback"}:
        return mt
    src = str(cand.source or "").strip().lower()
    if src.startswith(("label", "extra", "klantcode")) or src == "tabular":
        return "label"
    if src.startswith(
        (
            "factuur",
            "year_slash",
            "nummer_datum",
            "date_invoice",
            "split_k",
            "standalone",
            "spaced_k",
            "line_only_k",
            "collapsed",
            "uw_klant",
            "klant_line",
            "delivery_block",
            "ref_slash",
        )
    ):
        return "regex"
    return "fallback"


def _label_strength(cand: FieldCandidate) -> int:
    mt = _candidate_match_type(cand)
    strength = _MATCH_TYPE_PRIORITY.get(mt, 1) * 100
    if mt != "label":
        return strength
    meta = cand.meta if isinstance(cand.meta, dict) else {}
    label_src = str(meta.get("label_source") or cand.label or cand.source or "").strip()
    if _SPECIFIC_LABEL_HINT_RE.search(label_src):
        strength += 30
    elif _GENERIC_LABEL_HINT_RE.search(label_src):
        strength += 10
    return strength


def _source_priority(cand: FieldCandidate) -> int:
    src = str(cand.source or "").strip().lower()
    if src in _SOURCE_PRIORITY_EXACT:
        return _SOURCE_PRIORITY_EXACT[src]
    if src.startswith("label"):
        return 122
    if src.startswith("factuur"):
        return 98
    mt = _candidate_match_type(cand)
    if mt == "label":
        return 110
    if mt == "regex":
        return 90
    return 20


def _resolver_rank_key(field_id: FieldId, cand: FieldCandidate) -> tuple[Any, ...]:
    """Canonical resolver ranking (Phase B4 → ``rank_key``, context ``resolver``)."""
    return rank_key(field_id, cand, context="resolver")


def _ident_rank_tuple(cand: FieldCandidate) -> tuple[Any, ...]:
    """Ident-only rank for generic-helper paths (no amount/date resolver branches)."""
    ident = _to_ident_candidate(cand)
    return candidate_rank_key(ident)


def _candidate_rank_components(cand: FieldCandidate) -> tuple[int, int, int, int, int]:
    key = _ident_rank_tuple(cand)
    return (int(key[0]), int(key[1]), int(key[2]), int(key[3]), int(key[4]))


def _candidate_rank_tuple(cand: FieldCandidate) -> tuple[Any, ...]:
    """Backward-compatible alias for generic-helper ident ranking."""
    return _ident_rank_tuple(cand)


def _is_valid_labeled_pdf_iban_candidate(cand: FieldCandidate) -> bool:
    if str(cand.source or "").strip().lower() != "pdf_text":
        return False
    meta = cand.meta if isinstance(cand.meta, dict) else {}
    match_type = str(meta.get("match_type") or "").strip().lower()
    label = str(cand.label or "").strip()
    label_source = str(meta.get("label_source") or "").strip()
    has_label = bool(label) or bool(label_source) or match_type == "label"
    if not has_label:
        return False
    from logic.validation import clean_iban, is_plausible_iban

    iban = clean_iban(str(cand.value or ""))
    return bool(iban and is_plausible_iban(iban))


def _winner_reason(winner: FieldCandidate, runner_up: FieldCandidate | None) -> str:
    if runner_up is None:
        return "deterministic_tiebreak"
    w = _candidate_rank_components(winner)
    r = _candidate_rank_components(runner_up)
    if w[0] != r[0]:
        return "stronger_label_match"
    if w[1] != r[1]:
        return "field_keyword_match"
    if w[2] != r[2]:
        return "better_context_proximity"
    if w[3] != r[3]:
        return "higher_confidence"
    if w[4] != r[4]:
        return "lower_source_priority"
    return "deterministic_tiebreak"


def _loser_reason(winner: FieldCandidate, loser: FieldCandidate) -> str:
    w = _candidate_rank_components(winner)
    l = _candidate_rank_components(loser)
    if w[0] != l[0]:
        return "weaker_label"
    if w[1] != l[1]:
        return "weaker_field_type"
    if w[2] != l[2]:
        return "worse_context_proximity"
    if w[3] != l[3]:
        return "lower_confidence"
    if w[4] != l[4]:
        return "lower_source_priority"
    return "deterministic_tiebreak"


def resolve_field(
    field_id: FieldId,
    generic: FieldResult,
    overrides: list[FieldCandidate],
    user_pick: FieldCandidate | None = None,
    *,
    amount_profile_review_cap: bool = False,
) -> FieldResult:
    """Deterministische resolver: consumeert één ranking en kiest index 0."""
    trace: list[dict[str, Any]] = []

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
        if best is None or _resolver_rank_key(field_id, cand) > _resolver_rank_key(
            field_id, best
        ):
            dedup[key] = cand
    ranked = rank_candidates(field_id, list(dedup.values()), context="resolver")
    forced_excluded_reasons: dict[tuple[str, str], str] = {}
    forced_final_reason: str | None = None

    if not ranked:
        winner = FieldCandidate(value=None, source="UNKNOWN", confidence=0, context="")
        trace.append(
            {
                "kind": "final",
                "final_decision_reason": "not_found",
                "winner": {},
            }
        )
        return _build_result(
            field_id,
            generic,
            winner,
            [],
            override_reason="generic_only",
            decision_trace=trace,
            user_overridden=generic.user_overridden,
            previous_value=generic.previous_value,
            amount_profile_review_cap=amount_profile_review_cap,
        )

    winner = ranked[0]
    if user_pick is not None:
        winner = user_pick
        forced_final_reason = "user_locked"
        if ranked and (str(ranked[0].source or ""), str(ranked[0].value or "")) != (
            str(user_pick.source or ""),
            str(user_pick.value or ""),
        ):
            forced_excluded_reasons[
                (str(ranked[0].source or ""), str(ranked[0].value or ""))
            ] = "user_pick_override"
    if field_id == "iban":
        best_labeled_pdf = next(
            (c for c in ranked if _is_valid_labeled_pdf_iban_candidate(c)),
            None,
        )
        best_db = next(
            (c for c in ranked if str(c.source or "").strip().lower() == "db_master"),
            None,
        )
        if (
            best_labeled_pdf is not None
            and best_db is not None
            and not _values_equal(field_id, best_labeled_pdf.value, best_db.value)
        ):
            if str(winner.source or "").strip().lower() == "db_master":
                winner = best_labeled_pdf
                forced_final_reason = "labeled_pdf_iban_precedence"
            if winner is best_labeled_pdf:
                forced_excluded_reasons[
                    (str(best_db.source or ""), str(best_db.value or ""))
                ] = "pdf_labeled_priority_over_db"

    if winner is not ranked[0]:
        winner_key = (str(winner.source or ""), str(winner.value or ""))
        ranked = [winner] + [c for c in ranked if (str(c.source or ""), str(c.value or "")) != winner_key]

    runner_up = ranked[1] if len(ranked) > 1 else None
    winner_reason = _winner_reason(winner, runner_up)
    if forced_final_reason is not None:
        winner_reason = "pdf_labeled_priority_over_db"
    for idx, cand in enumerate(ranked, start=1):
        is_win = idx == 1
        key = (str(cand.source or ""), str(cand.value or ""))
        excluded_reason = forced_excluded_reasons.get(key) if not is_win else None
        if excluded_reason is None and not is_win:
            excluded_reason = _loser_reason(winner, cand)
        entry = _trace_entry(
            cand,
            considered=True,
            win=is_win,
            excluded_reason=excluded_reason,
            rank=idx,
            winner_reason=winner_reason if is_win else None,
            loser_reason=None if is_win else excluded_reason,
        )
        comp = _candidate_rank_components(cand)
        entry["label_strength"] = comp[0]
        entry["source_priority"] = comp[4]
        entry["rank_score"] = [str(x) for x in _resolver_rank_key(field_id, cand)]
        trace.append(entry)

    if forced_final_reason is not None:
        final_reason = forced_final_reason
    else:
        final_reason = (
            "highest_confidence"
            if runner_up is None or int(winner.confidence or 0) != int(runner_up.confidence or 0)
            else "deterministic_tiebreak"
        )
    trace.append(
        {
            "kind": "final",
            "final_decision_reason": final_reason,
            "winner": {
                "value": winner.value,
                "source": winner.source,
                "confidence": int(winner.confidence or 0),
                "winner_reason": winner_reason,
            },
        }
    )

    if _is_user_source(winner.source) or user_pick is not None or generic.user_overridden:
        override_reason = "user_locked"
    elif _is_override_source(winner.source):
        override_reason = "profile_higher_confidence"
    else:
        override_reason = "generic_only"

    return _build_result(
        field_id,
        generic,
        winner,
        ranked,
        override_reason=override_reason,
        decision_trace=trace,
        user_overridden=generic.user_overridden or _is_user_source(winner.source),
        previous_value=generic.previous_value,
        amount_profile_review_cap=amount_profile_review_cap,
    )


def _build_result(
    field_id: FieldId,
    generic: FieldResult,
    winner: FieldCandidate,
    all_cands: list[FieldCandidate],
    *,
    override_reason: str,
    decision_trace: list[dict[str, Any]],
    user_overridden: bool = False,
    previous_value: Any | None = None,
    amount_profile_review_cap: bool = False,
) -> FieldResult:
    st = normalize_field_status(generic.status)
    conf = int(winner.confidence or 0)
    src = str(winner.source or "UNKNOWN")

    if winner.value is None:
        st = "failed"
        conf = 0
    elif _is_user_source(src) or user_overridden:
        st = "confirmed"
        conf = max(conf, 100)
    elif _is_override_source(src):
        if override_reason == "profile_fills_gap":
            st = "confirmed" if conf >= HIGH_CONFIDENCE else "tentative"
        elif override_reason == "profile_higher_confidence":
            st = "confirmed" if conf >= HIGH_CONFIDENCE else "tentative"
        else:
            st = normalize_field_status(generic.status)
            if st in ("ambiguous", "failed") and winner.value is not None:
                st = "confirmed" if conf >= LOW_CONFIDENCE else "tentative"
    elif st in ("ambiguous", "failed") and winner.value is not None:
        st = "confirmed" if conf >= LOW_CONFIDENCE else "tentative"

    trace_out = list(decision_trace)
    winner_meta = winner.meta if isinstance(winner.meta, dict) else {}
    profile_validated = bool(winner_meta.get("profile_validated"))
    if (
        field_id == "amount"
        and amount_profile_review_cap
        and winner.value is not None
        and str(src).strip().lower() == "profile"
        and st == "confirmed"
        and not profile_validated
    ):
        st = "tentative"
        if conf > 75:
            conf = 75
        trace_out.append(
            {
                "kind": "amount_profile_review_cap",
                "from_status": "confirmed",
                "to_status": "tentative",
            }
        )

    merged_candidates = list(all_cands)
    user_sel = generic.user_selected or user_overridden

    result = FieldResult(
        field_id=field_id,
        candidates=merged_candidates,
        selected_value=winner.value,
        confidence=conf,
        source=src,
        status=st,
        user_selected=user_sel,
        user_overridden=user_overridden or generic.user_overridden,
        previous_value=previous_value if previous_value is not None else generic.previous_value,
        decision_trace=trace_out,
        override_reason=override_reason,
        resolver_finalized=True,
    )
    result.context = winner.context or result.resolved_context()
    return result


def profile_confidence_for_field(
    field_id: FieldId,
    *,
    validated: bool,
) -> int:
    if validated:
        return PROFILE_CONFIDENCE_VALIDATED
    return PROFILE_CONFIDENCE_UNVALIDATED


def db_master_confidence(field_id: FieldId) -> int:
    if field_id == "iban":
        return DB_MASTER_IBAN_CONFIDENCE
    if field_id == "customer_number":
        return DB_MASTER_CUSTOMER_CONFIDENCE
    return 0
