"""PHASE A — BEHAVIOR LOCK (observability only).

These tests make the CURRENT parse-time vs resolver ranking divergences VISIBLE.
They assert today's reality; they do NOT fix it. Phase B will reconcile each
divergence inside one canonical ranking function, at which point these tests are
expected to change (intentionally, with a documented winner-change log).

Documented divergences (see plan section B / C1):
  1. prefer_k_prefix is honored at parse time but dropped by the resolver.
  2. cross-field penalties are applied only at parse time; the resolver never
     re-applies them, so a non-penalized candidate (e.g. a profile/db override
     or a re-synthesized generic candidate) keeps full confidence.
  3. amount is ranked by a payable-score-first key in the resolver, which
     differs from the label-strength-first base key used for ident fields.
"""

from __future__ import annotations

from parser.field_candidates import (
    _CROSS_FIELD_CONFIDENCE_PENALTY,
    _apply_cross_field_penalties,
    IdentFieldCandidate,
    candidate_rank_key,
)
from parser.field_model import FieldCandidate
from parser.field_resolver import _candidate_rank_tuple

# The +3 K-prefix source-priority bonus (parser/field_candidates.py:_source_priority).
_K_PREFIX_BONUS = 3


def test_divergence_1_prefer_k_prefix_dropped_by_resolver() -> None:
    """The +3 K-prefix source-priority bonus exists at parse time but not in the resolver."""
    cand = IdentFieldCandidate(
        value="K04816069",
        source="klant_line",
        confidence=70,
        context="Klantcode K04816069",
        meta={"field_id": "customer_number"},
    )

    parse_key = candidate_rank_key(cand, prefer_k_prefix=True)
    resolver_key = candidate_rank_key(cand, prefer_k_prefix=False)

    # VISIBLE divergence: parse-time and resolver-time keys differ for a K-code.
    assert parse_key != resolver_key
    assert parse_key[4] == resolver_key[4] + _K_PREFIX_BONUS

    # The resolver's actual ranking entrypoint uses the no-bonus key.
    fc = FieldCandidate(
        value="K04816069",
        source="klant_line",
        confidence=70,
        context="Klantcode K04816069",
        meta={"field_id": "customer_number"},
    )
    assert tuple(_candidate_rank_tuple(fc)) == resolver_key


def test_divergence_2_cross_field_penalty_not_applied_in_resolver() -> None:
    """Order-like invoice_number candidate is penalized at parse, but not in the resolver."""
    penalized = IdentFieldCandidate(
        value="123456",
        source="factuur_plain",
        confidence=80,
        context="Ordernummer 123456",
        meta={"field_id": "invoice_number"},
    )
    _apply_cross_field_penalties([penalized], field_id="invoice_number")

    # Parse-time: confidence is reduced and flagged.
    assert penalized.confidence == 80 - _CROSS_FIELD_CONFIDENCE_PENALTY
    assert penalized.meta.get("cross_field_penalty_applied") is True

    # Resolver path: an equivalent candidate that did NOT go through parse-time
    # penalty (as profile/db overrides and re-synthesized generic candidates do
    # not) keeps full confidence and therefore outranks the penalized one.
    override = FieldCandidate(
        value="123456",
        source="factuur_plain",
        confidence=80,
        context="Ordernummer 123456",
        meta={"field_id": "invoice_number"},
    )
    resolver_key = tuple(_candidate_rank_tuple(override))
    parse_key = candidate_rank_key(penalized)

    assert resolver_key[3] == 80  # resolver sees un-penalized confidence
    assert parse_key[3] == 80 - _CROSS_FIELD_CONFIDENCE_PENALTY
    assert resolver_key > parse_key  # VISIBLE divergence in winner ordering


def _resolver_amount_key(fc: FieldCandidate) -> tuple:
    """Mirror of the resolver's amount-specific key (field_resolver.py resolve_field._rank_key)."""
    base = tuple(_candidate_rank_tuple(fc))
    meta = fc.meta if isinstance(fc.meta, dict) else {}
    try:
        payable_score = int(meta.get("payable_score") or 0)
    except (TypeError, ValueError):
        payable_score = 0
    return (payable_score, int(base[3]), int(base[4]), str(base[5]), str(base[6]))


def test_divergence_3_amount_uses_payable_score_first_key() -> None:
    """Amount selection uses a payable-score-first key that disagrees with the ident base key."""
    high_conf_low_payable = FieldCandidate(
        value="10.00",
        source="total_label_payable",
        confidence=90,
        context="Subtotaal 10.00",
        meta={"field_id": "amount", "payable_score": 0},
    )
    low_conf_high_payable = FieldCandidate(
        value="1551.22",
        source="total_label_payable",
        confidence=60,
        context="Te betalen 1551.22",
        meta={"field_id": "amount", "payable_score": 100},
    )

    pool = [high_conf_low_payable, low_conf_high_payable]

    # Ident base key (label-strength-first, then confidence) would pick the
    # higher-confidence candidate.
    base_winner = max(pool, key=lambda c: _candidate_rank_tuple(c))
    # Resolver amount key (payable-score-first) picks the higher payable score.
    amount_winner = max(pool, key=_resolver_amount_key)

    assert base_winner is high_conf_low_payable
    assert amount_winner is low_conf_high_payable
    # VISIBLE divergence: the two keys select different amount winners.
    assert base_winner is not amount_winner
