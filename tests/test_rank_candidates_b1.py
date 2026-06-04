"""Phase B1 — canonical rank_key / rank_candidates extraction (no production wiring)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from parser.field_candidates import (
    IdentFieldCandidate,
    candidate_rank_key,
    rank_candidates,
    rank_key,
)
from parser.field_model import FieldCandidate
from parser.pdf_parser import AmountCandidate, _amount_pick_key
from parser.field_resolver import _resolver_rank_key as resolver_rank_key


def test_rank_key_ident_matches_candidate_rank_key() -> None:
    cand = IdentFieldCandidate(
        value="INV-1",
        source="label",
        confidence=88,
        context="Factuurnummer INV-1",
        meta={"field_id": "invoice_number"},
    )
    assert rank_key("invoice_number", cand, context="parse") == candidate_rank_key(
        cand, prefer_k_prefix=False
    )
    assert rank_key(
        "customer_number", cand, prefer_k_prefix=True, context="parse"
    ) == candidate_rank_key(cand, prefer_k_prefix=True)


def test_rank_key_resolver_amount_matches_legacy_resolver() -> None:
    fc = FieldCandidate(
        value=Decimal("1551.22"),
        source="total_label_payable",
        confidence=60,
        context="Te betalen",
        meta={"field_id": "amount", "payable_score": 100, "type": "incl"},
    )
    assert rank_key("amount", fc, context="resolver") == resolver_rank_key("amount", fc)


def test_rank_key_parse_amount_matches_amount_pick_key() -> None:
    fc = FieldCandidate(
        value=Decimal("10.00"),
        source="total_label_payable",
        confidence=90,
        context="Subtotaal",
        meta={"field_id": "amount", "type": "incl", "payable_score": 0},
    )
    ac = AmountCandidate(
        value=Decimal("10.00"),
        source="total_label_payable",
        confidence=90,
        context="Subtotaal",
        type="incl",
    )
    assert rank_key("amount", fc, context="parse") == _amount_pick_key(ac)


def test_rank_candidates_resolver_orders_highest_first() -> None:
    low = FieldCandidate(
        value="10.00",
        source="total_label_payable",
        confidence=90,
        context="",
        meta={"field_id": "amount", "payable_score": 0, "type": "incl"},
    )
    high = FieldCandidate(
        value="1551.22",
        source="total_label_payable",
        confidence=60,
        context="",
        meta={"field_id": "amount", "payable_score": 100, "type": "incl"},
    )
    ordered = rank_candidates("amount", [low, high], context="resolver")
    assert ordered[0] is high
