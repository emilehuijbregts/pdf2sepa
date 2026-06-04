"""Phase B4 — resolver ranking fully delegates to rank_key / rank_candidates."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from parser.field_candidates import candidate_rank_key, rank_candidates, rank_key
from parser.field_model import ALL_FIELD_IDS, FieldCandidate
from parser.field_resolver import (
    _candidate_rank_tuple,
    _ident_rank_tuple,
    _resolver_rank_key,
    _to_ident_candidate,
)
from parser.pdf_parser import AmountCandidate, _amount_field_candidate
from tests.test_ranking_snapshot import SNAPSHOT_PATH, observability_bundle


def test_resolver_rank_key_matches_canonical_rank_key() -> None:
    fc = FieldCandidate(
        value="INV-9",
        source="label",
        confidence=88,
        context="Factuurnummer INV-9",
        meta={"field_id": "invoice_number"},
    )
    assert _resolver_rank_key("invoice_number", fc) == rank_key(
        "invoice_number", fc, context="resolver"
    )


def test_ident_rank_tuple_unchanged_for_generic_helpers() -> None:
    fc = FieldCandidate(
        value="K04816069",
        source="klant_line",
        confidence=70,
        context="Klantcode",
        meta={"field_id": "customer_number"},
    )
    assert _candidate_rank_tuple(fc) == _ident_rank_tuple(fc)
    assert _ident_rank_tuple(fc) == candidate_rank_key(_to_ident_candidate(fc))


def test_parse_and_resolver_order_identical_for_snapshot_amount_pools(
    observability_bundle: dict[str, Any],
) -> None:
    if not SNAPSHOT_PATH.is_file():
        pytest.skip("Committed snapshot missing")

    parse_by_pdf = observability_bundle["parse_by_pdf"]
    mismatches: list[str] = []

    for pdf in sorted(parse_by_pdf):
        inv = parse_by_pdf[pdf]
        pool: list[AmountCandidate] = []
        for row in inv.get("amount_candidates") or []:
            try:
                val = Decimal(str(row.get("value") or "0"))
            except Exception:
                continue
            pool.append(
                AmountCandidate(
                    value=val,
                    source=str(row.get("source") or ""),
                    confidence=int(row.get("confidence") or 0),
                    context=str(row.get("context") or ""),
                    type=row.get("type") or "unknown",  # type: ignore[arg-type]
                )
            )
        if len(pool) < 2:
            continue
        fcs = [_amount_field_candidate(c) for c in pool]
        parse_order = [str(c.value) for c in rank_candidates("amount", fcs, context="parse")]
        from parser.pdf_parser import _amount_payable_score

        resolver_fcs = [
            FieldCandidate(
                value=fc.value,
                source=fc.source,
                confidence=fc.confidence,
                context=fc.context,
                meta={
                    **dict(fc.meta or {}),
                    "payable_score": _amount_payable_score(
                        AmountCandidate(
                            value=Decimal(str(fc.value)),
                            source=str(fc.source or ""),
                            confidence=int(fc.confidence or 0),
                            context=str(fc.context or ""),
                            type=(fc.meta or {}).get("type") or "unknown",  # type: ignore[arg-type]
                        )
                    ),
                },
            )
            for fc in fcs
        ]
        resolver_order = [
            str(c.value) for c in rank_candidates("amount", resolver_fcs, context="resolver")
        ]
        if parse_order != resolver_order:
            mismatches.append(f"{pdf}: parse={parse_order!r} resolver={resolver_order!r}")

    assert mismatches == [], "Amount ordering diverged:\n" + "\n".join(mismatches[:15])


def test_resolver_ident_fields_use_canonical_rank_key_on_snapshot(
    observability_bundle: dict[str, Any],
) -> None:
    """Resolver-stage ident keys match rank_key(resolver) for every snapshot candidate row."""
    if not SNAPSHOT_PATH.is_file():
        pytest.skip("Committed snapshot missing")

    snapshot = __import__("json").loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    mismatches: list[str] = []

    for pdf, fields in snapshot.items():
        for field_id in ALL_FIELD_IDS:
            if field_id in ("amount", "invoice_date"):
                continue
            rows = (fields.get(field_id) or {}).get("resolver_stage", {}).get("candidates") or []
            for row in rows:
                fc = FieldCandidate(
                    value=row.get("value"),
                    source=str(row.get("source") or ""),
                    confidence=int(row.get("confidence") or 0),
                    meta={"field_id": field_id},
                )
                legacy = list(_ident_rank_tuple(fc))
                canonical = list(rank_key(field_id, fc, context="resolver"))
                if legacy != canonical:
                    mismatches.append(
                        f"{pdf}::{field_id} value={row.get('value')!r} legacy={legacy} canonical={canonical}"
                    )

    assert mismatches == [], "Ident resolver rank drift:\n" + "\n".join(mismatches[:20])
