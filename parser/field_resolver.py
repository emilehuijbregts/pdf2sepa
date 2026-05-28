"""Hybride per-veld resolver: generic primair, profile/db_master als override."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from parser.field_model import FieldCandidate, FieldId, FieldResult, normalize_field_status

HIGH_CONFIDENCE = 85
LOW_CONFIDENCE = 70
OVERRIDE_MARGIN = 10

_OVERRIDE_SOURCES = frozenset({"profile", "db_master"})
_USER_SOURCES = frozenset({"manual", "USER_PICKED", "user", "picked"})

PROFILE_CONFIDENCE_VALIDATED = 90
PROFILE_CONFIDENCE_UNVALIDATED = 60
DB_MASTER_IBAN_CONFIDENCE = 92
DB_MASTER_CUSTOMER_CONFIDENCE = 88


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
    if generic.selected_value is not None:
        return FieldCandidate(
            value=generic.selected_value,
            source=generic.source or "generic",
            confidence=int(generic.confidence or 0),
            context=str(generic.context or ""),
        )
    if generic.candidates:
        best = max(generic.candidates, key=lambda c: int(c.confidence or 0))
        return FieldCandidate(
            value=best.value,
            source=best.source or "generic",
            confidence=int(best.confidence or 0),
            context=str(best.context or ""),
        )
    return None


def _trace_entry(
    cand: FieldCandidate,
    *,
    considered: bool,
    win: bool,
    excluded_reason: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "source": cand.source,
        "confidence": int(cand.confidence or 0),
        "considered": considered,
        "win": win,
    }
    if excluded_reason:
        entry["excluded_reason"] = excluded_reason
    return entry


def _pick_best_override(overrides: list[FieldCandidate]) -> FieldCandidate | None:
    if not overrides:
        return None
    return max(overrides, key=lambda c: int(c.confidence or 0))


def _generic_is_strong(generic: FieldResult) -> bool:
    st = normalize_field_status(generic.status)
    conf = int(generic.confidence or 0)
    return st == "confirmed" and conf >= HIGH_CONFIDENCE


def _generic_is_weak(generic: FieldResult) -> bool:
    st = normalize_field_status(generic.status)
    conf = int(generic.confidence or 0)
    if st in ("ambiguous", "failed"):
        return True
    if st == "tentative":
        return True
    return conf < LOW_CONFIDENCE


def _db_master_conflict_winner(
    field_id: FieldId,
    generic: FieldResult,
    overrides: list[FieldCandidate],
) -> FieldCandidate | None:
    """Bij IBAN/klantnummer wint DB-master wanneer PDF-waarde afwijkt (geen user lock)."""
    if field_id not in ("customer_number", "iban"):
        return None
    if generic.user_overridden:
        return None
    db_cands = [o for o in overrides if o.source == "db_master"]
    if not db_cands:
        return None
    best_db = _pick_best_override(db_cands)
    if best_db is None:
        return None
    gen_cand = _generic_candidate(generic)
    if gen_cand is None or gen_cand.value is None:
        return None
    if _values_equal(field_id, gen_cand.value, best_db.value):
        return None
    return best_db


def resolve_field(
    field_id: FieldId,
    generic: FieldResult,
    overrides: list[FieldCandidate],
    user_pick: FieldCandidate | None = None,
) -> FieldResult:
    """Kies winnaar volgens hybride override-strategie; vult decision_trace."""
    trace: list[dict[str, Any]] = []
    all_cands: list[FieldCandidate] = list(generic.candidates)
    gen_cand = _generic_candidate(generic)
    if gen_cand and not any(
        _values_equal(field_id, c.value, gen_cand.value) and c.source == gen_cand.source
        for c in all_cands
    ):
        all_cands.insert(0, gen_cand)
    for ov in overrides:
        if not any(_values_equal(field_id, c.value, ov.value) for c in all_cands):
            all_cands.append(ov)
        else:
            all_cands.append(ov)

    if generic.user_overridden and user_pick is None:
        for c in all_cands:
            if _is_user_source(c.source) or generic.user_selected:
                user_pick = c
                break
        if user_pick is None and generic.selected_value is not None:
            user_pick = FieldCandidate(
                value=generic.selected_value,
                source=generic.source or "USER_PICKED",
                confidence=100,
                context=str(generic.context or ""),
            )

    if user_pick is not None or generic.user_overridden:
        # Preserve explicit override reason from caller (e.g. UI candidate click),
        # otherwise fall back to the generic user-lock reason.
        explicit_reason = str(getattr(generic, "override_reason", "") or "").strip()
        user_lock_reason = explicit_reason or "user_locked"
        winner = user_pick or FieldCandidate(
            value=generic.selected_value,
            source="USER_PICKED",
            confidence=100,
            context="",
        )
        for c in all_cands:
            trace.append(
                _trace_entry(
                    c,
                    considered=True,
                    win=_values_equal(field_id, c.value, winner.value)
                    and c.source == winner.source,
                    excluded_reason=None
                    if _values_equal(field_id, c.value, winner.value)
                    else "user_locked",
                )
            )
        return _build_result(
            field_id,
            generic,
            winner,
            all_cands,
            override_reason=user_lock_reason,
            decision_trace=trace,
            user_overridden=True,
            previous_value=generic.previous_value,
        )

    db_conflict = _db_master_conflict_winner(field_id, generic, overrides)
    if db_conflict is not None:
        winner = db_conflict
        reason = "db_master_conflict"
        for c in all_cands:
            is_win = _values_equal(field_id, c.value, winner.value) and c.source == winner.source
            excl = None if is_win else ("db_master_conflict" if c.source != "db_master" else None)
            trace.append(_trace_entry(c, considered=True, win=is_win, excluded_reason=excl))
        return _build_result(
            field_id,
            generic,
            winner,
            all_cands,
            override_reason=reason,
            decision_trace=trace,
        )

    best_ov = _pick_best_override(overrides)
    has_override = best_ov is not None

    if _generic_is_strong(generic) and gen_cand is not None:
        winner = gen_cand
        reason = "generic_strong"
        for c in all_cands:
            excl = None
            if c is not winner and not (
                _values_equal(field_id, c.value, winner.value) and c.source == winner.source
            ):
                if _is_override_source(c.source):
                    excl = "generic_strong"
            trace.append(
                _trace_entry(
                    c,
                    considered=True,
                    win=_values_equal(field_id, c.value, winner.value)
                    and c.source == winner.source,
                    excluded_reason=excl,
                )
            )
        return _build_result(
            field_id,
            generic,
            winner,
            all_cands,
            override_reason=reason,
            decision_trace=trace,
        )

    if not has_override:
        winner = gen_cand or _pick_best_override(all_cands) or FieldCandidate(
            value=None, source="UNKNOWN", confidence=0
        )
        reason = "generic_only"
        for c in all_cands:
            trace.append(
                _trace_entry(
                    c,
                    considered=bool(c.value is not None),
                    win=_values_equal(field_id, c.value, winner.value)
                    if winner.value is not None
                    else False,
                )
            )
        return _build_result(
            field_id,
            generic,
            winner,
            all_cands,
            override_reason=reason,
            decision_trace=trace,
        )

    assert best_ov is not None
    gen_conf = int(generic.confidence or 0)
    ov_conf = int(best_ov.confidence or 0)

    if _generic_is_weak(generic):
        winner = best_ov
        reason = "profile_fills_gap"
    elif ov_conf > gen_conf + OVERRIDE_MARGIN:
        winner = best_ov
        reason = "profile_higher_confidence"
    else:
        winner = gen_cand or best_ov
        reason = "generic_preferred"

    for c in all_cands:
        is_win = (
            winner.value is not None
            and _values_equal(field_id, c.value, winner.value)
            and (c.source == winner.source or reason != "generic_preferred")
        )
        excl = None
        if not is_win and _is_override_source(c.source) and reason == "generic_preferred":
            excl = "generic_preferred"
        elif not is_win and not _is_override_source(c.source) and reason.startswith("profile"):
            excl = reason
        trace.append(
            _trace_entry(c, considered=True, win=is_win, excluded_reason=excl)
        )

    return _build_result(
        field_id,
        generic,
        winner,
        all_cands,
        override_reason=reason,
        decision_trace=trace,
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
        decision_trace=decision_trace,
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
