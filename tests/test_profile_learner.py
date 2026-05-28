"""Tests for parser/profile_learner.py — snapshot-driven profile learning."""

from __future__ import annotations

from decimal import Decimal

from parser.field_model import FieldResult, is_resolver_final_field_result
from parser.profile_learner import (
    learn_profile_from_confirmation,
    learn_profile_from_resolved_fields,
    prepare_learnable_field_results,
)
from tests.test_profile_extractor import CONFIRMED_2BA, TEXT_2BA


def _post_resolve_amount_result(**overrides) -> dict:
    base = {
        "status": "confirmed",
        "value": "1551.22",
        "selected_amount": "1551.22",
        "confidence": 90,
        "source": "total_label_payable",
        "candidates": [
            {
                "value": "1551.22",
                "context": "Totaal € 269.22 € 1551.22",
                "confidence": 90,
                "source": "total_label_payable",
            }
        ],
        "decision_trace": [{"source": "total_label_payable", "win": True, "considered": True}],
    }
    base.update(overrides)
    return base


class TestIsResolverFinal:
    def test_pre_resolve_generic_not_learnable(self):
        fr = FieldResult(
            field_id="amount",
            selected_value=Decimal("100.00"),
            confidence=90,
            source="total_label_payable",
            status="confirmed",
        )
        assert is_resolver_final_field_result(fr) is False

    def test_decision_trace_is_learnable(self):
        fr = FieldResult(
            field_id="amount",
            selected_value=Decimal("100.00"),
            confidence=90,
            source="total_label_payable",
            status="confirmed",
            decision_trace=[{"win": True}],
        )
        assert is_resolver_final_field_result(fr) is True

    def test_user_overridden_is_learnable(self):
        fr = FieldResult(
            field_id="invoice_number",
            selected_value="260789",
            confidence=100,
            source="USER_PICKED",
            status="confirmed",
            user_overridden=True,
        )
        assert is_resolver_final_field_result(fr) is True


class TestPrepareLearnableFieldResults:
    def test_skips_pre_resolve_amount_without_dialog(self):
        snap = {
            "amount_result": {
                "status": "confirmed",
                "value": "1551.22",
                "confidence": 90,
                "source": "total_label_payable",
                "candidates": [],
            }
        }
        out = prepare_learnable_field_results(snap)
        assert "amount" not in out

    def test_includes_post_resolve_amount(self):
        snap = {"amount_result": _post_resolve_amount_result()}
        out = prepare_learnable_field_results(snap)
        assert "amount" in out
        assert out["amount"].selected_value == Decimal("1551.22")

    def test_dialog_overlay_makes_amount_learnable(self):
        snap = {
            "amount_result": {
                "status": "tentative",
                "value": "99.00",
                "confidence": 50,
                "source": "fallback",
                "candidates": [],
            }
        }
        out = prepare_learnable_field_results(
            snap,
            dialog_confirmed={"amount": Decimal("1551.22")},
        )
        assert "amount" in out
        assert out["amount"].user_selected is True
        assert out["amount"].user_overridden is True

    def test_includes_post_resolve_iban(self):
        snap = {
            "iban_result": {
                "status": "confirmed",
                "value": "NL71ABNA0804385750",
                "confidence": 88,
                "source": "pdf_text",
                "candidates": [],
                "decision_trace": [{"source": "pdf_text", "win": True, "considered": True}],
            }
        }
        out = prepare_learnable_field_results(snap)
        assert "iban" in out


class TestLearnProfileFromResolvedFields:
    def test_learns_2ba_fields_via_dialog_overlay(self):
        learnable = prepare_learnable_field_results(
            {},
            dialog_confirmed=dict(CONFIRMED_2BA),
        )
        profile = learn_profile_from_resolved_fields(
            raw_text=TEXT_2BA,
            source_file="2ba.pdf",
            field_results=learnable,
        )
        assert profile is not None
        assert "amount" in profile
        assert "invoice_number" in profile
        assert "customer_number" in profile

    def test_backward_compat_confirmation_wrapper(self):
        profile = learn_profile_from_confirmation(
            TEXT_2BA,
            dict(CONFIRMED_2BA),
            "2ba.pdf",
        )
        assert profile is not None
        assert profile["amount"]["strategy"] == "same_line_last_amount"

    def test_stores_confidence_metadata(self):
        learnable = prepare_learnable_field_results(
            {"amount_result": _post_resolve_amount_result(confidence=88)},
        )
        profile = learn_profile_from_resolved_fields(
            raw_text=TEXT_2BA,
            source_file="2ba.pdf",
            field_results=learnable,
        )
        assert profile is not None
        assert profile["amount"].get("confidence") == 88
