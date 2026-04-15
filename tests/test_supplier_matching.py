"""Tests for parser/supplier_db.py and parser/supplier_matcher.py."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from parser.supplier_db import SupplierDB
from parser.supplier_matcher import match_suppliers


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

    def test_iban_plus_alias_confirmed(self, db_with_suppliers):
        """IBAN + exact alias → confirmed."""
        inv = {"supplier_hint": "Wavin NL", "iban": "NL25CITI0266075452", "customer_number": None}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["supplier_name"] == "Wavin Nederland B.V."
        assert result["match_status"] == "confirmed"

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

    def test_code_plus_alias_confirmed(self, db_with_suppliers):
        """Customer code + alias → confirmed."""
        inv = {"supplier_hint": "Wavin NL", "iban": None, "customer_number": "1012146"}
        result = match_suppliers([inv], db_with_suppliers)[0]
        assert result["supplier_name"] == "Wavin Nederland B.V."
        assert result["match_status"] == "confirmed"


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
