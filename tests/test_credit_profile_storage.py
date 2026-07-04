"""Tests for supplier credit_profile persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parser.supplier_db import SupplierDB, supplier_key_from_name

SUPPLIER_NAME = "Wasco B.V."
SUPPLIER_IBAN = "NL91ABNA0417164300"
CREDIT_TEXT = """Wasco B.V.
Creditnota
Creditnummer: CN-12345
Factuurnr.: INV-999
Totaal te betalen EUR 150,00"""


@pytest.fixture
def db_with_supplier(tmp_path: Path) -> SupplierDB:
    data = {
        "suppliers": [
            {
                "name": SUPPLIER_NAME,
                "iban": SUPPLIER_IBAN,
                "discount": 0.0,
                "aliases": [SUPPLIER_NAME],
            }
        ]
    }
    p = tmp_path / "suppliers.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return SupplierDB(path=str(p))


def test_supplier_key_from_name_stable() -> None:
    assert supplier_key_from_name("Wasco B.V.") == supplier_key_from_name("wasco bv")


def test_save_credit_profile_requires_explicit_user_action(db_with_supplier: SupplierDB) -> None:
    key = supplier_key_from_name(SUPPLIER_NAME)
    profile = {
        "learned_from": "credit.pdf",
        "amount": {
            "label": "Totaal te betalen",
            "strategy": "same_line_last_amount",
            "confirmed_value": "150.00",
        },
        "credit_number": {
            "label": "Creditnummer",
            "strategy": "same_line_after_colon",
            "confirmed_value": "CN-12345",
        },
    }
    assert db_with_supplier.save_credit_profile(key, profile, raw_text=CREDIT_TEXT) is False
    assert db_with_supplier.get_credit_profile(key) is None


def test_save_credit_profile_persists_without_touching_extraction_profile(
    db_with_supplier: SupplierDB,
) -> None:
    key = supplier_key_from_name(SUPPLIER_NAME)
    db_with_supplier.save_extraction_profile(
        SUPPLIER_NAME,
        {
            "learned_from": "invoice.pdf",
            "amount": {
                "label": "Totaal",
                "strategy": "same_line_last_amount",
                "confirmed_value": "200.00",
            },
        },
        raw_text="Totaal EUR 200,00",
    )
    ep_before = db_with_supplier.get_extraction_profile(SUPPLIER_NAME)
    profile = {
        "learned_from": "credit.pdf",
        "amount": {
            "label": "Totaal te betalen",
            "strategy": "same_line_last_amount",
            "confirmed_value": "150.00",
        },
        "credit_number": {
            "label": "Creditnummer",
            "strategy": "same_line_after_colon",
            "confirmed_value": "CN-12345",
        },
    }
    saved = db_with_supplier.save_credit_profile(
        key,
        profile,
        raw_text=CREDIT_TEXT,
        explicit_user_action=True,
    )
    assert saved is True
    stored = db_with_supplier.get_credit_profile(key)
    assert stored is not None
    assert stored["credit_number"]["confirmed_value"] == "CN-12345"
    ep_after = db_with_supplier.get_extraction_profile(SUPPLIER_NAME)
    assert ep_after == ep_before


def test_get_credit_profile_by_supplier_key_only(db_with_supplier: SupplierDB) -> None:
    key = supplier_key_from_name(SUPPLIER_NAME)
    profile = {
        "learned_from": "credit.pdf",
        "credit_number": {
            "label": "Creditnummer",
            "strategy": "same_line_after_colon",
            "confirmed_value": "CN-12345",
        },
    }
    db_with_supplier.save_credit_profile(
        key,
        profile,
        raw_text=CREDIT_TEXT,
        explicit_user_action=True,
    )
    assert db_with_supplier.get_credit_profile("wasco bv") is not None
    assert db_with_supplier.get_credit_profile("wrong supplier") is None
