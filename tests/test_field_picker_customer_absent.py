"""Picker eligibility for customer_number with absent option."""

from __future__ import annotations

from ui.field_picker import picker_eligible


def test_customer_picker_eligible_with_single_candidate() -> None:
    snap = {
        "value": "K014135",
        "status": "confirmed",
        "candidates": [
            {"value": "K014135", "source": "label_block_same_line", "confidence": 94},
        ],
    }
    assert picker_eligible(snap, field_id="customer_number") is True


def test_customer_picker_not_eligible_without_candidates() -> None:
    snap = {"value": None, "status": "failed", "candidates": []}
    assert picker_eligible(snap, field_id="customer_number") is False
