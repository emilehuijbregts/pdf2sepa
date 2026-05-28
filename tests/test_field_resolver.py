"""Tests voor hybride field_resolver."""

from __future__ import annotations

from decimal import Decimal

import pytest

from parser.field_model import FieldCandidate, FieldResult
from parser.field_resolver import (
    HIGH_CONFIDENCE,
    OVERRIDE_MARGIN,
    resolve_field,
)


def _generic(
    *,
    value: object,
    confidence: int = 90,
    status: str = "confirmed",
    source: str = "total_label_payable",
) -> FieldResult:
    return FieldResult(
        field_id="amount",
        candidates=[
            FieldCandidate(value=value, source=source, confidence=confidence, context="ctx"),
        ],
        selected_value=value,
        confidence=confidence,
        source=source,
        status=status,
    )


class TestResolveFieldUserLocked:
    def test_user_overridden_wins(self):
        generic = FieldResult(
            field_id="invoice_number",
            selected_value="GEN-1",
            confidence=50,
            source="label",
            status="tentative",
            user_overridden=True,
            previous_value="OLD",
        )
        user = FieldCandidate(value="USER-1", source="USER_PICKED", confidence=100, context="")
        overrides = [
            FieldCandidate(value="PROF-1", source="profile", confidence=90, context=""),
        ]
        out = resolve_field("invoice_number", generic, overrides, user_pick=user)
        assert out.selected_value == "USER-1"
        assert out.override_reason == "user_locked"
        assert out.user_overridden is True
        assert out.previous_value == "OLD"


class TestResolveFieldGenericStrong:
    def test_strong_generic_beats_profile(self):
        generic = _generic(value=Decimal("100.00"), confidence=90, status="confirmed")
        overrides = [
            FieldCandidate(value=Decimal("200.00"), source="profile", confidence=90, context=""),
        ]
        out = resolve_field("amount", generic, overrides)
        assert out.selected_value == Decimal("100.00")
        assert out.override_reason == "generic_strong"


class TestResolveFieldProfileFillsGap:
    def test_weak_generic_uses_profile(self):
        generic = _generic(
            value=None,
            confidence=0,
            status="failed",
            source="UNKNOWN",
        )
        generic.selected_value = None
        overrides = [
            FieldCandidate(value=Decimal("50.00"), source="profile", confidence=90, context=""),
        ]
        out = resolve_field("amount", generic, overrides)
        assert out.selected_value == Decimal("50.00")
        assert out.override_reason == "profile_fills_gap"

    def test_ambiguous_generic_uses_profile(self):
        generic = _generic(
            value=Decimal("100.00"),
            confidence=50,
            status="ambiguous",
        )
        overrides = [
            FieldCandidate(value=Decimal("120.00"), source="profile", confidence=90, context=""),
        ]
        out = resolve_field("amount", generic, overrides)
        assert out.selected_value == Decimal("120.00")
        assert out.override_reason == "profile_fills_gap"


class TestResolveFieldConfidenceComparison:
    def test_profile_higher_confidence_wins(self):
        generic = _generic(
            value=Decimal("100.00"),
            confidence=80,
            status="confirmed",
        )
        overrides = [
            FieldCandidate(
                value=Decimal("110.00"),
                source="profile",
                confidence=80 + OVERRIDE_MARGIN + 1,
                context="",
            ),
        ]
        out = resolve_field("amount", generic, overrides)
        assert out.selected_value == Decimal("110.00")
        assert out.override_reason == "profile_higher_confidence"

    def test_generic_preferred_when_close(self):
        generic = _generic(
            value=Decimal("100.00"),
            confidence=80,
            status="confirmed",
        )
        overrides = [
            FieldCandidate(value=Decimal("110.00"), source="profile", confidence=85, context=""),
        ]
        out = resolve_field("amount", generic, overrides)
        assert out.selected_value == Decimal("100.00")
        assert out.override_reason == "generic_preferred"


class TestResolveFieldNoOverrides:
    def test_generic_only(self):
        generic = _generic(value=Decimal("10.00"), confidence=70, status="tentative")
        out = resolve_field("amount", generic, [])
        assert out.selected_value == Decimal("10.00")
        assert out.override_reason == "generic_only"


class TestDbMasterConflict:
    def test_db_wins_when_customer_differs(self):
        generic = FieldResult(
            field_id="customer_number",
            selected_value="16003040",
            confidence=95,
            source="label",
            status="confirmed",
            candidates=[
                FieldCandidate(value="16003040", source="label", confidence=95, context=""),
            ],
        )
        overrides = [
            FieldCandidate(value="3349", source="db_master", confidence=88, context="DB"),
        ]
        out = resolve_field("customer_number", generic, overrides)
        assert out.selected_value == "3349"
        assert out.override_reason == "db_master_conflict"

    def test_db_skipped_when_same_value(self):
        generic = FieldResult(
            field_id="customer_number",
            selected_value="3349",
            confidence=95,
            source="label",
            status="confirmed",
        )
        overrides = [
            FieldCandidate(value="3349", source="db_master", confidence=88, context="DB"),
        ]
        out = resolve_field("customer_number", generic, overrides)
        assert out.override_reason == "generic_strong"


class TestDecisionTrace:
    def test_trace_populated(self):
        generic = _generic(value=Decimal("1.00"), confidence=90, status="confirmed")
        overrides = [
            FieldCandidate(value=Decimal("2.00"), source="profile", confidence=90, context=""),
        ]
        out = resolve_field("amount", generic, overrides)
        assert len(out.decision_trace) >= 1
        assert any(e.get("win") for e in out.decision_trace)
