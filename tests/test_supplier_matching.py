"""Tests for parser/supplier_db.py and parser/supplier_matcher.py."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from parser.profile_learner import learn_profile_from_confirmation
from parser.supplier_db import SupplierDB
from parser.supplier_matcher import match_suppliers
from tests.test_profile_extractor import (
    CONFIRMED_2BA,
    PROFILE_LAST_AMOUNT,
    TEXT_2BA,
    TEXT_LAST_AMOUNT,
)


@pytest.fixture
def db_with_suppliers(tmp_path):
    """Create a SupplierDB with test suppliers."""
    data = {
        "suppliers": [
            {
                "name": "Wavin Nederland B.V.",
                "iban": "NL25CITI0266075452",
                "discount": 2.0,
                "aliases": ["Wavin-Nederland", "Wavin Nederland B.V.", "Wavin NL"],
                "customer_codes": ["1012146"],
            },
            {
                "name": "SALO B.V.",
                "iban": "NL64ABNA0589033654",
                "discount": 0.0,
                "aliases": ["SALO B.V."],
                "customer_codes": ["3503"],
            },
            {
                "name": "Technische Unie B.V.",
                "iban": "NL71ABNA0804385750",
                "discount": 0.0,
                "aliases": ["Technische Unie B.V."],
                "customer_codes": ["232210"],
            },
        ]
    }
    p = tmp_path / "suppliers.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return SupplierDB(path=str(p))


@pytest.fixture
def empty_db(tmp_path):
    p = tmp_path / "suppliers.json"
    p.write_text('{"suppliers": []}', encoding="utf-8")
    return SupplierDB(path=str(p))


class TestIbanMatch:
    def test_exact_iban_only_needs_review(self, db_with_suppliers):
        """Single characteristic (IBAN only) → needs_review."""
        inv = {"supplier_hint": None, "iban": "NL25CITI0266075452", "customer_number": None}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["supplier_name"] == "Wavin Nederland B.V."
        assert result["match_status"] == "needs_review"

    def test_iban_plus_customer_code_confirmed(self, db_with_suppliers):
        """Two primary characteristics → confirmed."""
        inv = {"supplier_hint": None, "iban": "NL25CITI0266075452", "customer_number": "1012146"}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["supplier_name"] == "Wavin Nederland B.V."
        assert result["match_status"] == "confirmed"

    def test_iban_plus_alias_needs_review(self, db_with_suppliers):
        """IBAN + exact alias: naam telt niet als tweede kernkenmerk."""
        inv = {"supplier_hint": "Wavin NL", "iban": "NL25CITI0266075452", "customer_number": None}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["supplier_name"] == "Wavin Nederland B.V."
        assert result["match_status"] == "needs_review"
        assert result["db_core_matches"] == ["IBAN"]

    def test_iban_with_spaces(self, db_with_suppliers):
        inv = {"supplier_hint": None, "iban": "NL25 CITI 0266 0754 52", "customer_number": None}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["supplier_name"] == "Wavin Nederland B.V."

    def test_iban_case_insensitive(self, db_with_suppliers):
        inv = {"supplier_hint": None, "iban": "nl25citi0266075452", "customer_number": None}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["supplier_name"] == "Wavin Nederland B.V."


class TestAliasMatch:
    def test_exact_alias(self, db_with_suppliers):
        inv = {"supplier_hint": "Wavin-Nederland", "iban": None, "customer_number": None}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["supplier_name"] == "Wavin Nederland B.V."

    def test_substring_alias(self, db_with_suppliers):
        inv = {"supplier_hint": "Wavin NL producten", "iban": None, "customer_number": None}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["supplier_name"] == "Wavin Nederland B.V."


class TestCustomerCodeMatch:
    def test_code_only_needs_review(self, db_with_suppliers):
        """Customer code only → needs_review."""
        inv = {"supplier_hint": None, "iban": None, "customer_number": "1012146"}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["supplier_name"] == "Wavin Nederland B.V."
        assert result["match_status"] == "needs_review"

    def test_code_match_fills_iban(self, db_with_suppliers):
        inv = {"supplier_hint": None, "iban": None, "customer_number": "1012146"}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["iban"] == "NL25CITI0266075452"

    def test_code_plus_alias_needs_review(self, db_with_suppliers):
        """Klantnummer + alias: alleen één kernkenmerk → needs_review."""
        inv = {"supplier_hint": "Wavin NL", "iban": None, "customer_number": "1012146"}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["supplier_name"] == "Wavin Nederland B.V."
        assert result["match_status"] == "needs_review"
        assert result["db_core_matches"] == ["Klantnummer"]


class TestPolarisCoreMatches:
    def test_polaris_iban_plus_name_needs_review(self, tmp_path):
        data = {
            "suppliers": [
                {
                    "name": "Polaris",
                    "iban": "NL34ABNA0135735831",
                    "discount": 0.0,
                    "aliases": ["Polaris"],
                    "customer_codes": [],
                    "kvk_numbers": ["34095053"],
                    "email_domains": ["polaris-werkvitaalverzekeren.nl"],
                },
            ]
        }
        p = tmp_path / "suppliers.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        db = SupplierDB(path=str(p))
        inv = {
            "supplier_hint": "Polaris",
            "iban": "NL34ABNA0135735831",
            "customer_number": None,
            "kvk_number": None,
            "email_domain": None,
        }
        result = match_suppliers([inv], db)[0]
        assert result["supplier_name"] == "Polaris"
        assert result["match_status"] == "needs_review"
        assert result["db_core_matches"] == ["IBAN"]

    def test_polaris_iban_profile_amount_while_needs_review(self, tmp_path):
        """Polaris-achtige factuur: profiel levert bedrag, kernmatch blijft 1/2."""
        data = {
            "suppliers": [
                {
                    "name": "Polaris",
                    "iban": "NL34ABNA0135735831",
                    "discount": 0.0,
                    "aliases": ["Polaris"],
                    "customer_codes": [],
                    "kvk_numbers": ["34095053"],
                    "email_domains": ["polaris-werkvitaalverzekeren.nl"],
                    "extraction_profile": {
                        "amount": {
                            "label": "Inkomenspakket EUR",
                            "strategy": "same_line_last_amount",
                            "confirmed_value": "4947.17",
                        }
                    },
                },
            ]
        }
        p = tmp_path / "suppliers.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        db = SupplierDB(path=str(p))
        text = (
            "Inkomenspakket EUR 4.947,17\n"
            "Wij verzoeken u deze premie binnen 14 dagen op IBAN NL34 ABNA 0135 7358 31\n"
            "t.n.v. Polaris Werk, Vitaal en Verzekeren over te maken.\n"
        )
        inv = {
            "supplier_hint": None,
            "iban": "NL34ABNA0135735831",
            "raw_text": text,
        }
        result = match_suppliers([inv], db)[0]
        assert result["supplier_name"] == "Polaris"
        assert result["match_status"] == "needs_review"
        assert result["db_core_matches"] == ["IBAN"]
        assert result.get("amount") == pytest.approx(4947.17)
        ar = result.get("amount_result") or {}
        assert ar.get("status") == "tentative"
        assert result.get("supplier_db_traits_not_on_invoice") == ["KvK", "e-mail"]

    def test_polaris_iban_plus_kvk_confirmed(self, tmp_path):
        data = {
            "suppliers": [
                {
                    "name": "Polaris",
                    "iban": "NL34ABNA0135735831",
                    "discount": 0.0,
                    "aliases": ["Polaris"],
                    "customer_codes": [],
                    "kvk_numbers": ["34095053"],
                    "email_domains": ["polaris-werkvitaalverzekeren.nl"],
                },
            ]
        }
        p = tmp_path / "suppliers.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        db = SupplierDB(path=str(p))
        inv = {
            "supplier_hint": "Polaris",
            "iban": "NL34ABNA0135735831",
            "kvk_number": "34095053",
        }
        result = match_suppliers([inv], db)[0]
        assert result["match_status"] == "confirmed"
        assert set(result["db_core_matches"]) == {"IBAN", "KvK"}


class TestUnmatched:
    def test_no_match(self, db_with_suppliers):
        inv = {"supplier_hint": "Unknown Corp", "iban": "NL00XXXX9999999999", "customer_number": "999"}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["match_status"] == "unmatched"

    def test_no_hint(self, db_with_suppliers):
        inv = {"supplier_hint": None, "iban": None, "customer_number": None}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["match_status"] == "no_hint"

    def test_empty_database(self, empty_db):
        inv = {"supplier_hint": "Some Corp", "iban": "NL00TEST1234567890", "customer_number": "123"}
        result = match_suppliers([inv], empty_db)[0]
        assert result["match_status"] == "unmatched"

    def test_empty_database_valid_iban_becomes_new(self, empty_db):
        inv = {
            "supplier_hint": "Some Corp",
            "iban": "NL25CITI0266075452",
            "customer_number": "123",
            "source_file": "/tmp/some_invoice.pdf",
        }
        result = match_suppliers([inv], empty_db)[0]
        assert result["match_status"] == "new"
        assert result["supplier_name"] == "Some Corp"


class TestIbanMismatch:
    def test_iban_mismatch_flagged(self, db_with_suppliers):
        inv = {
            "supplier_hint": "Wavin NL",
            "iban": "NL99XXXX0000000001",
            "customer_number": None,
        }
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["match_status"] in ("needs_review", "confirmed")
        assert result.get("iban_mismatch") is True


class TestMatchInfo:
    def test_match_info_present(self, db_with_suppliers):
        inv = {"supplier_hint": None, "iban": "NL25CITI0266075452", "customer_number": "1012146"}
        result = match_suppliers([inv], db_with_suppliers)[0]
        info = result.get("match_info", {})
        assert info["iban_match"] is True
        assert info["customer_code_match"] is True


class TestSupplierVatRateOnInvoice:
    def test_matched_invoice_gets_supplier_vat_rate_default_21(self, db_with_suppliers):
        inv = {"supplier_hint": None, "iban": "NL25CITI0266075452", "customer_number": "1012146"}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result.get("supplier_vat_rate") == 21

    def test_matched_invoice_gets_db_vat_rate_zero(self, tmp_path):
        data = {
            "suppliers": [
                {
                    "name": "DE GmbH",
                    "iban": "DE89370400440532013000",
                    "discount": 0.0,
                    "vat_rate": 0,
                    "aliases": ["DE GmbH"],
                    "customer_codes": [],
                },
            ]
        }
        p = tmp_path / "suppliers.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        db = SupplierDB(path=str(p))
        inv = {
            "supplier_hint": "DE GmbH",
            "iban": "DE89370400440532013000",
            "customer_number": None,
        }
        result = match_suppliers([inv], db)[0]
        assert result.get("supplier_vat_rate") == 0

    def test_unmatched_defaults_vat_rate_21(self, db_with_suppliers):
        inv = {"supplier_hint": "Unknown Corp", "iban": "NL00XXXX9999999999", "customer_number": "999"}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result.get("supplier_vat_rate") == 21


class TestSupplierDB:
    def test_add_supplier(self, empty_db):
        empty_db.add_supplier("Test BV", "NL00TEST1234567890", 1.5, aliases=["Test"], customer_codes=["111"])
        assert len(empty_db.get_all()) == 1
        row = empty_db.get_all()[0]
        assert row["name"] == "Test BV"
        assert row.get("vat_rate") == 21

    def test_delete_supplier(self, db_with_suppliers):
        assert db_with_suppliers.delete_supplier("SALO B.V.")
        assert len(db_with_suppliers.get_all()) == 2

    def test_update_supplier(self, db_with_suppliers):
        assert db_with_suppliers.update_supplier("SALO B.V.", discount=5.0)
        for s in db_with_suppliers.get_all():
            if s["name"] == "SALO B.V.":
                assert s["discount"] == 5.0

    def test_update_supplier_vat_rate(self, db_with_suppliers):
        assert db_with_suppliers.update_supplier("SALO B.V.", vat_rate=0)
        for s in db_with_suppliers.get_all():
            if s["name"] == "SALO B.V.":
                assert s.get("vat_rate") == 0

    def test_corrupt_json_creates_empty(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not valid json!!!", encoding="utf-8")
        db = SupplierDB(path=str(p))
        assert db.get_all() == []

    def test_missing_file_creates_empty(self, tmp_path):
        p = tmp_path / "nonexistent.json"
        db = SupplierDB(path=str(p))
        assert db.get_all() == []
        assert p.exists()


class TestExtractionProfile:
    def test_get_unknown_supplier(self, db_with_suppliers):
        assert db_with_suppliers.get_extraction_profile("No Such BV") is None

    def test_get_no_profile(self, db_with_suppliers):
        assert db_with_suppliers.get_extraction_profile("SALO B.V.") is None

    def test_get_corrupt_type(self, db_with_suppliers):
        for s in db_with_suppliers.suppliers:
            if s.get("name") == "SALO B.V.":
                s["extraction_profile"] = "not-a-dict"
                break
        assert db_with_suppliers.get_extraction_profile("SALO B.V.") is None

    def test_save_unknown_supplier(self, db_with_suppliers):
        ok = db_with_suppliers.save_extraction_profile(
            "Ghost BV",
            PROFILE_LAST_AMOUNT,
            raw_text=TEXT_LAST_AMOUNT,
        )
        assert ok is False
        assert db_with_suppliers.get_extraction_profile("Ghost BV") is None

    @patch("parser.supplier_db.validate_profile", return_value=False)
    def test_save_validation_fail_no_persist(self, mock_validate, db_with_suppliers):
        ok = db_with_suppliers.save_extraction_profile(
            "SALO B.V.",
            PROFILE_LAST_AMOUNT,
            raw_text=TEXT_LAST_AMOUNT,
        )
        assert ok is False
        mock_validate.assert_called_once()
        assert db_with_suppliers.get_extraction_profile("SALO B.V.") is None
        for s in db_with_suppliers.get_all():
            if s["name"] == "SALO B.V.":
                assert "extraction_profile" not in s

    def test_save_success_roundtrip(self, db_with_suppliers, tmp_path):
        ok = db_with_suppliers.save_extraction_profile(
            "salo b.v.",
            PROFILE_LAST_AMOUNT,
            raw_text=TEXT_LAST_AMOUNT,
        )
        assert ok is True
        got = db_with_suppliers.get_extraction_profile("SALO B.V.")
        assert got is not None
        assert got["amount"]["confirmed_value"] == "1551.22"
        assert got["learned_from"] == "test.pdf"

        data = json.loads((tmp_path / "suppliers.json").read_text(encoding="utf-8"))
        salo = next(s for s in data["suppliers"] if s["name"] == "SALO B.V.")
        assert salo["extraction_profile"]["amount"]["strategy"] == "same_line_last_amount"

    def test_save_integration_validate_profile(self, db_with_suppliers):
        """Real validate_profile path (no mock)."""
        ok = db_with_suppliers.save_extraction_profile(
            "Technische Unie B.V.",
            PROFILE_LAST_AMOUNT,
            raw_text=TEXT_LAST_AMOUNT,
        )
        assert ok is True
        assert db_with_suppliers.get_extraction_profile("Technische Unie B.V.") is not None


class TestProfilePipeline:
    def test_confirmed_applies_profile_amount(self, db_with_suppliers):
        db_with_suppliers.save_extraction_profile(
            "SALO B.V.",
            PROFILE_LAST_AMOUNT,
            raw_text=TEXT_LAST_AMOUNT,
        )
        inv = {
            "supplier_hint": "SALO B.V.",
            "iban": "NL64ABNA0589033654",
            "customer_number": "3503",
            "raw_text": TEXT_LAST_AMOUNT,
            "amount": 1.0,
            "amount_result": {
                "status": "ambiguous",
                "source": "TEST",
                "value": None,
                "candidates": [{"value": "1.00"}],
            },
        }
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["match_status"] == "confirmed"
        assert result["extraction_source"] == "profile"
        assert "amount" in result["profile_fields"]
        assert result["amount"] == 1551.22
        assert result["amount_result"]["source"] == "profile"
        assert result["amount_result"]["status"] == "confirmed"
        assert "raw_text" in result

    def test_needs_review_with_iban_applies_profile_tentative(self, db_with_suppliers):
        """IBAN-match + profiel: bedrag uit profiel, status tentative (export nog review)."""
        db_with_suppliers.save_extraction_profile(
            "SALO B.V.",
            PROFILE_LAST_AMOUNT,
            raw_text=TEXT_LAST_AMOUNT,
        )
        inv = {
            "supplier_hint": None,
            "iban": "NL64ABNA0589033654",
            "customer_number": None,
            "raw_text": TEXT_LAST_AMOUNT,
            "amount": 1.0,
            "amount_result": {
                "status": "ambiguous",
                "source": "TEST",
                "value": None,
                "candidates": [],
            },
        }
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["match_status"] == "needs_review"
        assert result["extraction_source"] == "profile"
        assert "amount" in result["profile_fields"]
        assert result["amount_result"]["source"] == "profile"
        assert result["amount_result"]["status"] == "tentative"

    def test_confirmed_no_profile_is_generic(self, db_with_suppliers):
        inv = {
            "supplier_hint": "SALO B.V.",
            "iban": "NL64ABNA0589033654",
            "customer_number": "3503",
            "raw_text": TEXT_LAST_AMOUNT,
        }
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["match_status"] == "confirmed"
        assert result["extraction_source"] == "generic"
        assert result["profile_fields"] == []

    def test_confirmed_profile_without_raw_text_is_generic(self, db_with_suppliers):
        db_with_suppliers.save_extraction_profile(
            "SALO B.V.",
            PROFILE_LAST_AMOUNT,
            raw_text=TEXT_LAST_AMOUNT,
        )
        inv = {
            "supplier_hint": "SALO B.V.",
            "iban": "NL64ABNA0589033654",
            "customer_number": "3503",
        }
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["match_status"] == "confirmed"
        assert result["extraction_source"] == "generic"
        assert result["profile_fields"] == []

    def test_profile_rebuilds_description(self, db_with_suppliers):
        profile = learn_profile_from_confirmation(
            TEXT_2BA,
            CONFIRMED_2BA,
            "2ba.pdf",
        )
        assert profile is not None
        db_with_suppliers.save_extraction_profile(
            "Technische Unie B.V.",
            profile,
            raw_text=TEXT_2BA,
        )
        inv = {
            "supplier_hint": "Technische Unie B.V.",
            "iban": "NL71ABNA0804385750",
            "customer_number": "232210",
            "raw_text": TEXT_2BA,
            "invoice_number": "OLD",
            "description": "old desc",
        }
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["match_status"] == "confirmed"
        assert result["extraction_source"] == "profile"
        assert result["invoice_number"] == "260789"
        assert result["customer_number"] == "113073/17078"
        assert result["description"] == "113073/17078 / 260789"


class TestLoadFailedShortcut:
    def test_load_error_skips_matching(self, empty_db, tmp_path):
        pdf = tmp_path / "broken.pdf"
        inv = {
            "source_file": str(pdf),
            "load_error": "no_text",
            "iban": None,
        }
        out = match_suppliers([inv], empty_db)
        assert len(out) == 1
        assert out[0]["match_status"] == "load_failed"
        assert out[0]["supplier_name"] == "broken.pdf"
        assert out[0]["load_error"] == "no_text"
        assert out[0].get("supplier_vat_rate") == 21
