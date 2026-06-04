"""Phase B3 — parser amount path delegates to canonical rank_key / rank_candidates."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from parser.field_candidates import rank_candidates, rank_key
from parser.field_model import FieldCandidate
from parser.field_resolver import _resolver_rank_key
from parser.pdf_parser import (
    AmountCandidate,
    _amount_field_candidate,
    _amount_pick_key,
    _pick_best_amount,
)
from tests.test_ranking_snapshot import SNAPSHOT_PATH, observability_bundle


def test_amount_pick_key_delegates_to_rank_key_parse() -> None:
    ac = AmountCandidate(
        value=Decimal("1551.22"),
        source="total_label_payable",
        confidence=60,
        context="Te betalen",
        type="incl",
    )
    assert _amount_pick_key(ac) == rank_key("amount", _amount_field_candidate(ac), context="parse")


def test_pick_best_amount_matches_rank_candidates() -> None:
    low = AmountCandidate(
        value=Decimal("10.00"),
        source="total_label_payable",
        confidence=90,
        context="Subtotaal",
        type="incl",
    )
    high = AmountCandidate(
        value=Decimal("1551.22"),
        source="total_label_payable",
        confidence=60,
        context="Te betalen",
        type="incl",
    )
    pool = [low, high]
    assert _pick_best_amount(pool) is high
    ordered = rank_candidates(
        "amount",
        [_amount_field_candidate(c) for c in pool],
        context="parse",
    )
    assert ordered[0].value == high.value


def _amount_fc_with_resolver_meta(c: AmountCandidate) -> FieldCandidate:
    from parser.pdf_parser import _amount_payable_score

    return FieldCandidate(
        value=c.value,
        source=str(c.source or ""),
        confidence=int(c.confidence or 0),
        context=str(c.context or ""),
        meta={
            "field_id": "amount",
            "type": str(getattr(c, "type", "unknown") or "unknown"),
            "payable_score": _amount_payable_score(c),
        },
    )


def test_snapshot_amount_parse_order_matches_resolver_order(observability_bundle: dict[str, Any]) -> None:
    """Parse and resolver amount keys must agree on candidate ordering when payable_score is aligned."""
    if not SNAPSHOT_PATH.is_file():
        pytest.skip("Committed snapshot missing")

    parse_by_pdf = observability_bundle["parse_by_pdf"]
    mismatches: list[str] = []

    for pdf in sorted(parse_by_pdf):
        inv = parse_by_pdf[pdf]
        cands = inv.get("amount_candidates") or []
        if len(cands) < 2:
            continue
        amount_pool: list[AmountCandidate] = []
        for row in cands:
            try:
                val = Decimal(str(row.get("value") or "0"))
            except Exception:
                continue
            amount_pool.append(
                AmountCandidate(
                    value=val,
                    source=str(row.get("source") or ""),
                    confidence=int(row.get("confidence") or 0),
                    context=str(row.get("context") or ""),
                    type=row.get("type") or "unknown",  # type: ignore[arg-type]
                )
            )
        if len(amount_pool) < 2:
            continue

        parse_order = [
            str(c.value)
            for c in sorted(
                amount_pool,
                key=lambda c: rank_key("amount", _amount_field_candidate(c), context="parse"),
                reverse=True,
            )
        ]
        resolver_order = [
            str(c.value)
            for c in sorted(
                amount_pool,
                key=lambda c: rank_key(
                    "amount", _amount_fc_with_resolver_meta(c), context="resolver"
                ),
                reverse=True,
            )
        ]
        if parse_order != resolver_order:
            mismatches.append(f"{pdf}: parse={parse_order!r} resolver={resolver_order!r}")

    assert mismatches == [], "Amount ordering diverged:\n" + "\n".join(mismatches[:15])


def test_snapshot_resolver_rank_key_matches_b2_resolver(observability_bundle: dict[str, Any]) -> None:
    """Resolver amount keys still match production ``_resolver_rank_key`` (B2 contract)."""
    parse_by_pdf = observability_bundle["parse_by_pdf"]
    for pdf in sorted(parse_by_pdf):
        inv = parse_by_pdf[pdf]
        for row in inv.get("amount_candidates") or []:
            try:
                val = Decimal(str(row.get("value") or "0"))
            except Exception:
                continue
            fc = _amount_fc_with_resolver_meta(
                AmountCandidate(
                    value=val,
                    source=str(row.get("source") or ""),
                    confidence=int(row.get("confidence") or 0),
                    context=str(row.get("context") or ""),
                    type=row.get("type") or "unknown",  # type: ignore[arg-type]
                )
            )
            assert list(_resolver_rank_key("amount", fc)) == list(
                rank_key("amount", fc, context="resolver")
            )
