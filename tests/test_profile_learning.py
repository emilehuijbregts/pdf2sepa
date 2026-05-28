"""Tests for logic/profile_learning.py."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from logic.profile_learning import (
    ProfileLearnResult,
    can_offer_profile_learning,
    confirm_invoice_fields,
    confirmed_amount_xml,
    merge_extraction_profiles,
    profile_field_keys_missing,
    profile_learning_block_reason,
)
from tests.test_profile_extractor import CONFIRMED_2BA, TEXT_2BA


def _base_snapshot(**overrides) -> dict:
    snap = {
        "match_status": "confirmed",
        "extraction_source": "generic",
        "source_file": "/tmp/factuur.pdf",
        "supplier_name": "Technische Unie B.V.",
    }
    snap.update(overrides)
    return snap


class TestCanOfferProfileLearning:
    def test_all_gates_pass(self, tmp_path: Path) -> None:
        pdf = tmp_path / "factuur.pdf"
        pdf.write_text("x", encoding="utf-8")
        snap = _base_snapshot(source_file=str(pdf))
        assert can_offer_profile_learning(snap, source_file=str(pdf), amount_resolved=True) is True

    def test_needs_review_allowed_when_amount_resolved(self, tmp_path: Path) -> None:
        pdf = tmp_path / "factuur.pdf"
        pdf.write_text("x", encoding="utf-8")
        snap = _base_snapshot(match_status="needs_review", source_file=str(pdf))
        assert can_offer_profile_learning(snap, source_file=str(pdf), amount_resolved=True) is True

    def test_rejects_unmatched_match(self, tmp_path: Path) -> None:
        pdf = tmp_path / "factuur.pdf"
        pdf.write_text("x", encoding="utf-8")
        snap = _base_snapshot(match_status="unmatched", source_file=str(pdf))
        assert can_offer_profile_learning(snap, source_file=str(pdf), amount_resolved=True) is False

    def test_allows_without_resolved_amount_on_button(self, tmp_path: Path) -> None:
        """Bedrag mag in de dialog; knop niet blokkeren op amount_unresolved."""
        pdf = tmp_path / "factuur.pdf"
        pdf.write_text("x", encoding="utf-8")
        snap = _base_snapshot(source_file=str(pdf))
        assert profile_learning_block_reason(snap, source_file=str(pdf), amount_resolved=False) is None

    def test_rejects_profile_extraction_source(self, tmp_path: Path) -> None:
        pdf = tmp_path / "factuur.pdf"
        pdf.write_text("x", encoding="utf-8")
        snap = _base_snapshot(extraction_source="profile", source_file=str(pdf))
        complete = {
            "amount": {"label": "T", "strategy": "same_line_last_amount"},
            "invoice_number": {"label": "F", "strategy": "same_line_after_colon"},
            "customer_number": {"label": "K", "strategy": "same_line_after_colon"},
        }
        assert can_offer_profile_learning(
            snap, source_file=str(pdf), amount_resolved=True, stored_profile=complete
        ) is False

    def test_allows_profile_when_amount_missing_in_db(self, tmp_path: Path) -> None:
        pdf = tmp_path / "factuur.pdf"
        pdf.write_text("x", encoding="utf-8")
        snap = _base_snapshot(extraction_source="profile", source_file=str(pdf))
        partial = {
            "invoice_number": {"label": "Factuurnummer:", "strategy": "same_line_after_colon"},
            "customer_number": {"label": "Klantnummer:", "strategy": "same_line_after_colon"},
        }
        assert profile_field_keys_missing(partial) == ["amount"]
        assert can_offer_profile_learning(
            snap,
            source_file=str(pdf),
            amount_resolved=True,
            stored_profile=partial,
        ) is True

    def test_accepts_missing_extraction_source(self, tmp_path: Path) -> None:
        pdf = tmp_path / "factuur.pdf"
        pdf.write_text("x", encoding="utf-8")
        snap = _base_snapshot(extraction_source="", source_file=str(pdf))
        del snap["extraction_source"]
        assert can_offer_profile_learning(snap, source_file=str(pdf), amount_resolved=True) is True

    def test_rejects_missing_file(self) -> None:
        snap = _base_snapshot(source_file="/no/such/file.pdf")
        assert can_offer_profile_learning(snap, source_file="/no/such/file.pdf") is False

    def test_rejects_none_source_file(self) -> None:
        assert can_offer_profile_learning(_base_snapshot(), source_file=None) is False


class TestMergeExtractionProfiles:
    def test_keeps_existing_amount_adds_invoice(self):
        existing = {
            "learned_from": "old.pdf",
            "amount": {"label": "Totaal", "strategy": "same_line_last_amount", "confirmed_value": "10.00"},
        }
        learned = {
            "learned_from": "new.pdf",
            "invoice_number": {
                "label": "Factuurnummer:",
                "strategy": "same_line_after_colon",
                "confirmed_value": "X1",
            },
        }
        merged = merge_extraction_profiles(existing, learned)
        assert "amount" in merged
        assert merged["invoice_number"]["confirmed_value"] == "X1"
        assert merged["learned_from"] == "new.pdf"


class TestConfirmInvoiceFields:
    def test_confirm_only_no_save(self) -> None:
        db = MagicMock()
        result = confirm_invoice_fields(
            raw_text=TEXT_2BA,
            source_file="2ba.pdf",
            supplier_name="Technische Unie B.V.",
            confirmed=dict(CONFIRMED_2BA),
            db=db,
            save_profile=False,
        )
        assert result.saved is False
        assert result.profile is None
        assert "niet opgeslagen" in result.message
        assert result.confirmed["amount"] == Decimal("1551.22")
        db.save_extraction_profile.assert_not_called()

    def test_save_profile_success(self) -> None:
        db = MagicMock()
        db.get_extraction_profile.return_value = None
        db.save_extraction_profile.return_value = True
        result = confirm_invoice_fields(
            raw_text=TEXT_2BA,
            source_file="2ba.pdf",
            supplier_name="Technische Unie B.V.",
            confirmed=dict(CONFIRMED_2BA),
            db=db,
            save_profile=True,
            iban="NL71ABNA0804385750",
        )
        assert result.saved is True
        assert result.profile is not None
        assert "amount" in (result.profile or {})
        assert "opgeslagen" in result.message
        db.save_extraction_profile.assert_called_once()
        db.merge_or_add_supplier.assert_called_once_with(
            "Technische Unie B.V.",
            "NL71ABNA0804385750",
            "113073/17078",
        )

    def test_save_profile_learn_fails(self) -> None:
        db = MagicMock()
        result = confirm_invoice_fields(
            raw_text=TEXT_2BA,
            source_file="x.pdf",
            supplier_name="X",
            confirmed={},
            db=db,
            save_profile=True,
        )
        assert result.saved is False
        assert result.profile is None
        assert "niet automatisch" in result.message
        db.save_extraction_profile.assert_not_called()

    def test_save_profile_validation_fails(self) -> None:
        db = MagicMock()
        db.save_extraction_profile.return_value = False
        result = confirm_invoice_fields(
            raw_text=TEXT_2BA,
            source_file="2ba.pdf",
            supplier_name="Technische Unie B.V.",
            confirmed=dict(CONFIRMED_2BA),
            db=db,
            save_profile=True,
        )
        assert result.saved is False
        assert result.profile is not None
        assert "validatie" in result.message

    def test_empty_supplier_name(self) -> None:
        db = MagicMock()
        result = confirm_invoice_fields(
            raw_text=TEXT_2BA,
            source_file="x.pdf",
            supplier_name="",
            confirmed=dict(CONFIRMED_2BA),
            db=db,
            save_profile=True,
        )
        assert "Leveranciersnaam" in result.message


class TestConfirmedAmountXml:
    def test_formats_decimal(self) -> None:
        assert confirmed_amount_xml({"amount": Decimal("1551.22")}) == "1551.22"
