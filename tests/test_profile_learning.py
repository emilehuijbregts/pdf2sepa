"""Tests for logic/profile_learning.py."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from logic.profile_learning import (
    ProfileLearnResult,
    _normalize_confirmed,
    can_offer_profile_learning,
    confirm_invoice_fields,
    confirmed_amount_xml,
    merge_extraction_profiles,
    profile_field_keys_missing,
    profile_learning_block_reason,
)
from parser.hybrid_field_apply import apply_hybrid_field_extraction
from parser.supplier_db import SupplierDB, CUSTOMER_NUMBER_MODE_NONE
from ui.field_review import CUSTOMER_ABSENT_PICK_SOURCE, CUSTOMER_ABSENT_STATE, make_customer_absent_pick_candidate
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

    def test_merge_skips_customer_number_when_existing_none_mode(self) -> None:
        existing = {
            "customer_number_mode": CUSTOMER_NUMBER_MODE_NONE,
            "amount": {"label": "Totaal", "strategy": "same_line_last_amount", "confirmed_value": "10.00"},
        }
        learned = {
            "customer_number": {
                "label": "Klantnummer",
                "strategy": "same_line_after_colon",
                "confirmed_value": "30146",
            },
        }
        merged = merge_extraction_profiles(existing, learned)
        assert merged.get("customer_number_mode") == CUSTOMER_NUMBER_MODE_NONE
        assert "customer_number" not in merged


class TestNormalizeConfirmed:
    def test_skips_absent_customer_pick_dict(self) -> None:
        norm = _normalize_confirmed({"customer_number": make_customer_absent_pick_candidate()})
        assert "customer_number" not in norm

    def test_does_not_stringify_absent_dict(self) -> None:
        cand = make_customer_absent_pick_candidate()
        norm = _normalize_confirmed({"customer_number": cand})
        for val in norm.values():
            assert "USER_ABSENT_CUSTOMER" not in str(val)


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
            vat_number=None,
            kvk_number=None,
            email_domain=None,
        )

    def test_save_profile_derived_excl_plus_vat_success(self) -> None:
        text = (
            "Factuurnummer: INV-001\n"
            "Klantnummer: 740777\n"
            "Excl. btw € 328,30\n"
            "BTW 21% € 68,94\n"
        )
        confirmed = {
            "amount": Decimal("397.24"),
            "invoice_number": "INV-001",
            "customer_number": "740777",
        }
        snap = {
            "amount_result": {
                "status": "confirmed",
                "value": "397.24",
                "confidence": 82,
                "source": "derived_excl_plus_vat",
                "candidates": [
                    {
                        "value": "397.24",
                        "context": "excl. btw + BTW % line sum",
                        "confidence": 82,
                        "source": "derived_excl_plus_vat",
                    }
                ],
                "decision_trace": [{"source": "derived_excl_plus_vat", "win": True}],
            },
        }
        db = MagicMock()
        db.get_extraction_profile.return_value = None
        db.save_extraction_profile.return_value = True
        result = confirm_invoice_fields(
            raw_text=text,
            source_file="qblades.pdf",
            supplier_name="DG Europe B.V.",
            confirmed=confirmed,
            db=db,
            save_profile=True,
            post_resolve_snapshot=snap,
            amount_result=snap["amount_result"],
        )
        assert result.saved is True
        assert result.profile is not None
        assert result.profile["amount"]["strategy"] == "derived_excl_plus_vat"
        assert "opgeslagen" in result.message
        db.save_extraction_profile.assert_called_once()

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

    def test_save_none_mode_when_profile_learn_fails(self) -> None:
        db = MagicMock()
        db.set_customer_number_mode.return_value = True
        db.get_extraction_profile.return_value = {"customer_number_mode": CUSTOMER_NUMBER_MODE_NONE}
        absent_result = {
            "value": None,
            "absence_state": CUSTOMER_ABSENT_STATE,
            "source": CUSTOMER_ABSENT_PICK_SOURCE,
            "status": "confirmed",
            "user_selected": True,
        }
        result = confirm_invoice_fields(
            raw_text=TEXT_2BA,
            source_file="x.pdf",
            supplier_name="Qblades",
            confirmed={},
            db=db,
            save_profile=True,
            customer_number_result=absent_result,
        )
        assert result.saved is True
        db.set_customer_number_mode.assert_called_once_with("Qblades", CUSTOMER_NUMBER_MODE_NONE)

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

    def test_ident_saved_when_amount_learn_fails(self) -> None:
        """Amount failure must not block identification domain save."""
        text = """2ba B.V.
Factuurnummer : 260789
Debiteurnummer : 113073/17078
Regels zonder totaalregel
"""
        db = MagicMock()
        db.get_extraction_profile.return_value = None
        db.save_extraction_profile.return_value = True
        result = confirm_invoice_fields(
            raw_text=text,
            source_file="2ba.pdf",
            supplier_name="Technische Unie B.V.",
            confirmed=dict(CONFIRMED_2BA),
            db=db,
            save_profile=True,
        )
        assert result.saved is True
        assert result.profile is not None
        assert "invoice_number" in (result.profile or {})
        assert "customer_number" in (result.profile or {})
        db.save_extraction_profile.assert_called_once()

        outcomes = {o.field_id: o.status for o in result.field_outcomes}
        assert outcomes["invoice_number"] == "learned"
        assert outcomes["customer_number"] == "learned"
        assert outcomes["amount"] == "failed"

        assert "maar het bedrag" not in result.message.lower()
        assert "Factuurnummer: geleerd en opgeslagen." in result.message
        assert "Klantnummer: geleerd en opgeslagen." in result.message
        assert "Bedrag:" in result.message
        assert "(deels) opgeslagen" in result.message

    def test_field_outcomes_all_learned(self) -> None:
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
        )
        assert all(o.status == "learned" for o in result.field_outcomes if o.field_id in CONFIRMED_2BA)
        assert "deels" not in result.message.lower()


class TestConfirmedAmountXml:
    def test_formats_decimal(self) -> None:
        assert confirmed_amount_xml({"amount": Decimal("1551.22")}) == "1551.22"


class TestDerivedAmountProfileHybrid:
    def test_profile_amount_wins_over_generic_derived(self, tmp_path: Path) -> None:
        text = (
            "Excl. btw € 328,30\n"
            "BTW 21% € 68,94\n"
        )
        data = {
            "suppliers": [
                {
                    "name": "DG Europe B.V.",
                    "iban": "NL31RABO0172459192",
                    "discount": 0.0,
                    "aliases": ["DG Europe B.V."],
                    "customer_codes": ["740777"],
                    "extraction_profile": {
                        "amount": {
                            "strategy": "derived_excl_plus_vat",
                            "label_excl": "Excl. btw",
                            "label_btw": "BTW 21%",
                            "confirmed_value": "397.24",
                        }
                    },
                }
            ]
        }
        import json

        p = tmp_path / "suppliers.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        db = SupplierDB(path=str(p))
        supplier = data["suppliers"][0]
        invoice = {
            "raw_text": text,
            "amount_result": {
                "status": "confirmed",
                "value": "397.24",
                "selected_value": "397.24",
                "confidence": 82,
                "source": "derived_excl_plus_vat",
                "candidates": [
                    {
                        "value": "397.24",
                        "source": "derived_excl_plus_vat",
                        "confidence": 82,
                        "context": "excl. btw + BTW % line sum",
                    }
                ],
                "decision_trace": [{"source": "derived_excl_plus_vat", "win": True}],
            },
        }
        invoice_copy: dict = {}
        apply_hybrid_field_extraction(invoice, invoice_copy, supplier, db)
        ar = invoice_copy.get("amount_result") or {}
        assert ar.get("source") == "profile"
        assert float(ar.get("value") or ar.get("selected_value") or 0) == pytest.approx(
            397.24, abs=0.01
        )
        assert "amount" in (invoice_copy.get("profile_fields") or [])
