"""Tests for logic/settings.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from logic.settings import (
    DEFAULT_SETTINGS,
    coerce_internal_vat_numbers,
    load_settings,
    save_settings,
    merge_debtor_with_defaults,
    normalize_settings,
    validate_debtor_for_export,
    resolve_settings_path,
)


class TestLoadSettings:
    def test_missing_file_creates_defaults(self, tmp_path):
        p = tmp_path / "settings.json"
        s = load_settings(str(p))
        assert p.exists()
        assert "debtor" in s

    def test_corrupt_json_returns_defaults(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text("not json!!!", encoding="utf-8")
        s = load_settings(str(p))
        assert "debtor" in s
        assert s["debtor"]["name"] == ""

    def test_valid_json_preserved(self, tmp_path):
        p = tmp_path / "settings.json"
        data = {"debtor": {"name": "Mijn Bedrijf", "iban": "NL20INGB0001234567", "bic": "INGBNL2A"}}
        p.write_text(json.dumps(data), encoding="utf-8")
        s = load_settings(str(p))
        assert s["debtor"]["name"] == "Mijn Bedrijf"


class TestSaveSettings:
    def test_save_and_reload(self, tmp_path):
        p = tmp_path / "settings.json"
        data = {
            "debtor": {"name": "Test", "iban": "", "bic": "", "kvk": "", "vat": ""},
            "export_dir": "exports",
            "last_invoice_dir": "",
        }
        assert save_settings(data, str(p))
        loaded = json.loads(p.read_text(encoding="utf-8"))
        assert loaded["debtor"]["name"] == "Test"

    def test_save_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "sub" / "dir" / "settings.json"
        assert save_settings(
            {"debtor": {"name": "", "iban": "", "bic": "", "kvk": "", "vat": ""}},
            str(p),
        )
        assert p.exists()


class TestMergeDebtor:
    def test_none_input(self):
        d = merge_debtor_with_defaults(None)
        assert d == {"name": "", "iban": "", "bic": "", "kvk": "", "vat": ""}

    def test_partial_input(self):
        d = merge_debtor_with_defaults({"name": "Test"})
        assert d["name"] == "Test"
        assert d["iban"] == ""
        assert d["bic"] == ""
        assert d["kvk"] == ""
        assert d["vat"] == ""


class TestValidateDebtor:
    def test_valid(self):
        d = {"name": "Test BV", "iban": "NL20INGB0001234567", "bic": "INGBNL2A"}
        assert validate_debtor_for_export(d) is None

    def test_missing_name(self):
        d = {"name": "", "iban": "NL20INGB0001234567", "bic": "INGBNL2A"}
        err = validate_debtor_for_export(d)
        assert err is not None and "naam" in err.lower()

    def test_missing_iban(self):
        d = {"name": "Test", "iban": "", "bic": "INGBNL2A"}
        err = validate_debtor_for_export(d)
        assert err is not None and "iban" in err.lower()

    def test_missing_bic(self):
        d = {"name": "Test", "iban": "NL20INGB0001234567", "bic": ""}
        err = validate_debtor_for_export(d)
        assert err is not None and "bic" in err.lower()


class TestResolveSettingsPath:
    def test_relative(self, tmp_path):
        p = resolve_settings_path("exports", base_dir=tmp_path)
        assert p == tmp_path / "exports"

    def test_absolute(self, tmp_path):
        abs_path = "/tmp/my_exports"
        p = resolve_settings_path(abs_path, base_dir=tmp_path)
        assert str(p) == abs_path


class TestInternalVatNumbers:
    def test_coerce_reads_internal_vat_numbers_list(self):
        data = {"internal_vat_numbers": ["NL148005664B01", "NL813771213B01"]}
        assert coerce_internal_vat_numbers(data) == [
            "NL148005664B01",
            "NL813771213B01",
        ]

    def test_coerce_ignores_debtor_vat(self):
        data = {
            "debtor": {"vat": "NL148005664B01"},
            "internal_vat_numbers": ["NL813771213B01"],
        }
        assert coerce_internal_vat_numbers(data) == ["NL813771213B01"]

    def test_normalize_syncs_debtor_vat_output(self):
        data = {
            "debtor": {"name": "X", "iban": "", "bic": "", "kvk": "", "vat": "OLD"},
            "internal_vat_numbers": ["NL148005664B01", "NL813771213B01"],
        }
        out = normalize_settings(data)
        assert out["internal_vat_numbers"] == [
            "NL148005664B01",
            "NL813771213B01",
        ]
        assert out["debtor"]["vat"] == "NL148005664B01"

    def test_multi_vat_roundtrip_save_reload(self, tmp_path):
        p = tmp_path / "settings.json"
        data = {
            "debtor": {
                "name": "Test",
                "iban": "",
                "bic": "",
                "kvk": "",
                "vat": "",
            },
            "internal_vat_numbers": [
                "NL148005664B01",
                "NL813771213B01",
            ],
            "export_dir": "exports",
            "last_invoice_dir": "",
        }
        assert save_settings(data, str(p))
        loaded = load_settings(str(p))
        assert loaded["internal_vat_numbers"] == [
            "NL148005664B01",
            "NL813771213B01",
        ]
        assert loaded["debtor"]["vat"] == "NL148005664B01"
