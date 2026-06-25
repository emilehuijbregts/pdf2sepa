"""Tests for parser/field_authority.py (user > profile > OCR)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parser.field_authority import (
    AUTHORITY_FIELD_IDS,
    build_user_pick,
    build_user_pick_from_legacy,
    is_user_locked,
    should_apply_profile_override,
    should_enforce_none_absent,
)
from parser.field_adapters import field_result_from_legacy_dict
from parser.field_model import FieldCandidate
from parser.hybrid_field_apply import apply_hybrid_field_extraction
from parser.supplier_db import CUSTOMER_NUMBER_MODE_NONE, SupplierDB
from ui.field_review import CUSTOMER_ABSENT_PICK_SOURCE, CUSTOMER_ABSENT_STATE


class TestIsUserLocked:
    def test_locked_when_flag_set(self) -> None:
        assert is_user_locked({"user_overridden": True, "selected_value": "X"}) is True

    def test_not_locked_without_flag(self) -> None:
        assert is_user_locked({"selected_value": "X"}) is False
        assert is_user_locked(None) is False


class TestShouldApplyProfileOverride:
    def test_skips_when_user_locked_for_authority_fields(self) -> None:
        locked = {"user_overridden": True}
        for field_id in AUTHORITY_FIELD_IDS:
            assert should_apply_profile_override(field_id, locked) is False

    def test_allows_when_not_locked(self) -> None:
        assert should_apply_profile_override("amount", {}) is True


class TestShouldEnforceNoneAbsent:
    def test_enforces_when_none_active_and_not_locked(self) -> None:
        assert should_enforce_none_absent({}, none_mode_active=True) is True

    def test_skips_when_user_locked(self) -> None:
        assert (
            should_enforce_none_absent({"user_overridden": True, "selected_value": "30146"}, none_mode_active=True)
            is False
        )

    def test_skips_when_none_inactive(self) -> None:
        assert should_enforce_none_absent({}, none_mode_active=False) is False


class TestBuildUserPick:
    def test_normal_value(self) -> None:
        pick = build_user_pick(
            "invoice_number",
            {"user_overridden": True, "selected_value": "INV-1", "source": "USER_PICKED"},
        )
        assert pick is not None
        assert pick.value == "INV-1"
        assert pick.confidence == 100

    def test_cleared_iban(self) -> None:
        pick = build_user_pick(
            "iban",
            {"user_overridden": True, "selected_value": "", "source": "USER_PICKED"},
        )
        assert pick is not None
        assert pick.value == ""

    def test_absent_customer_none_mode(self) -> None:
        pick = build_user_pick(
            "customer_number",
            {
                "user_overridden": True,
                "selected_value": None,
                "source": CUSTOMER_ABSENT_PICK_SOURCE,
                "absence_state": CUSTOMER_ABSENT_STATE,
            },
        )
        assert pick is not None
        assert pick.value is None

    def test_returns_none_when_not_locked(self) -> None:
        assert build_user_pick("amount", {"selected_value": "10.00"}) is None


class TestBuildUserPickFromLegacy:
    def test_from_field_result(self) -> None:
        fr = field_result_from_legacy_dict(
            {"user_overridden": True, "selected_value": "99.00", "source": "manual", "status": "confirmed"},
            field_id="amount",
        )
        pick = build_user_pick_from_legacy("amount", fr)
        assert pick is not None
        assert str(pick.value) == "99.00"


@pytest.fixture
def db_none_mode(tmp_path: Path) -> SupplierDB:
    data = {
        "suppliers": [
            {
                "name": "SALO B.V.",
                "iban": "NL64ABNA0589033654",
                "discount": 0.0,
                "aliases": ["SALO B.V."],
                "customer_codes": ["30146"],
                "extraction_profile": {
                    "customer_number_mode": CUSTOMER_NUMBER_MODE_NONE,
                    "amount": {
                        "label": "Totaal",
                        "strategy": "same_line_last_amount",
                        "confirmed_value": "100.00",
                    },
                },
            }
        ]
    }
    p = tmp_path / "suppliers.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return SupplierDB(path=str(p))


class TestHybridUserPickBeatsProfile:
    def test_user_locked_customer_wins_over_none_and_profile(self, db_none_mode: SupplierDB) -> None:
        supplier = db_none_mode.suppliers[0]
        text = "Klantnummer: 30146\nTotaal EUR 100,00"
        invoice = {
            "raw_text": text,
            "customer_number": "30146",
            "customer_number_result": {
                "value": "30146",
                "selected_value": "30146",
                "status": "confirmed",
                "source": "USER_PICKED",
                "user_overridden": True,
                "candidates": [{"value": "30146", "source": "label", "confidence": 90}],
            },
            "iban": "NL64ABNA0589033654",
            "amount": 100.0,
            "amount_result": {
                "status": "confirmed",
                "source": "TEST",
                "value": "100.00",
                "candidates": [{"value": "100.00"}],
            },
        }
        invoice_copy: dict = {}
        apply_hybrid_field_extraction(
            invoice,
            invoice_copy,
            supplier,
            db_none_mode,
            amount_status="confirmed",
            use_profile=True,
        )
        assert invoice_copy.get("customer_number") == "30146"
        cr = invoice_copy.get("customer_number_result") or {}
        assert cr.get("selected_value") == "30146"
        assert cr.get("user_overridden") is True

    def test_user_locked_amount_beats_profile(self, tmp_path: Path) -> None:
        data = {
            "suppliers": [
                {
                    "name": "DG Europe B.V.",
                    "iban": "NL31RABO0172459192",
                    "discount": 0.0,
                    "aliases": ["DG Europe B.V."],
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
        p = tmp_path / "suppliers.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        db = SupplierDB(path=str(p))
        supplier = data["suppliers"][0]
        text = "Excl. btw € 328,30\nBTW 21% € 68,94\n"
        invoice = {
            "raw_text": text,
            "amount_result": {
                "status": "confirmed",
                "value": "350.00",
                "selected_value": "350.00",
                "confidence": 100,
                "source": "USER_PICKED",
                "user_overridden": True,
                "candidates": [
                    {"value": "350.00", "source": "USER_PICKED", "confidence": 100},
                    {"value": "397.24", "source": "derived_excl_plus_vat", "confidence": 82},
                ],
            },
        }
        invoice_copy: dict = {}
        apply_hybrid_field_extraction(invoice, invoice_copy, supplier, db)
        ar = invoice_copy.get("amount_result") or {}
        assert float(ar.get("value") or ar.get("selected_value") or 0) == pytest.approx(350.0, abs=0.01)
        assert ar.get("user_overridden") is True
