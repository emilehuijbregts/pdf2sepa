"""Tests for logic/credit_profile_learning.py and apply gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from logic.credit_classifier import classify_credit_document
from logic.credit_enrichment import enrich_credit_document
from logic.credit_profile_apply import credit_profile_may_apply
from logic.credit_profile_learning import (
    can_offer_credit_profile_learning,
    confirm_credit_profile_fields,
    credit_profile_learning_block_reason,
    learn_credit_profile_from_confirmation,
)
from parser.supplier_db import SupplierDB, supplier_key_from_name

CREDIT_TEXT = """Wasco B.V.
Creditnota
Creditnummer: CN-12345
Factuurnr.: INV-999
Totaal te betalen EUR 150,00"""

INVOICE_TEXT = """Wasco B.V.
Factuur
Factuurnummer: INV-100
Totaal te betalen EUR 200,00"""


@pytest.fixture
def db_with_supplier(tmp_path: Path) -> SupplierDB:
    data = {
        "suppliers": [
            {
                "name": "Wasco B.V.",
                "iban": "NL91ABNA0417164300",
                "discount": 0.0,
                "aliases": ["Wasco B.V."],
            }
        ]
    }
    p = tmp_path / "suppliers.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return SupplierDB(path=str(p))


def test_credit_profile_learning_blocked_without_explicit_action() -> None:
    profile = learn_credit_profile_from_confirmation(
        raw_text=CREDIT_TEXT,
        source_file="credit.pdf",
        supplier_key="wasco bv",
        confirmed={"amount": "150.00", "invoice_number": "CN-12345"},
        explicit_user_action=False,
    )
    assert profile is None


def test_confirm_credit_profile_requires_explicit_user_action(db_with_supplier: SupplierDB) -> None:
    key = supplier_key_from_name("Wasco B.V.")
    result = confirm_credit_profile_fields(
        raw_text=CREDIT_TEXT,
        source_file="credit.pdf",
        supplier_key=key,
        confirmed={"amount": "150.00", "invoice_number": "CN-12345"},
        db=db_with_supplier,
        save_profile=True,
        explicit_user_action=False,
    )
    assert result.saved is False
    assert db_with_supplier.get_credit_profile(key) is None


def test_confirm_credit_profile_saves_on_explicit_action(db_with_supplier: SupplierDB) -> None:
    key = supplier_key_from_name("Wasco B.V.")
    result = confirm_credit_profile_fields(
        raw_text=CREDIT_TEXT,
        source_file="credit.pdf",
        supplier_key=key,
        confirmed={"amount": "150.00", "invoice_number": "CN-12345"},
        db=db_with_supplier,
        save_profile=True,
        explicit_user_action=True,
    )
    assert result.saved is True
    stored = db_with_supplier.get_credit_profile(key)
    assert stored is not None
    assert "amount" in stored or "credit_number" in stored


def test_can_offer_credit_profile_learning_credit_only(tmp_path: Path) -> None:
    pdf = tmp_path / "credit.pdf"
    pdf.write_text("x", encoding="utf-8")
    snap = {
        "type": "credit_note",
        "match_status": "confirmed",
        "source_file": str(pdf),
    }
    assert can_offer_credit_profile_learning(snap, source_file=str(pdf), supplier_key="wasco bv") is True
    assert credit_profile_learning_block_reason(
        {"type": "invoice", "match_status": "confirmed"},
        source_file=str(pdf),
        supplier_key="wasco bv",
    ) == "not_credit_note"


def test_credit_profile_not_applied_to_invoice_without_credit_type() -> None:
    inv = {
        "type": "invoice",
        "raw_text": INVOICE_TEXT,
        "supplier_key": "wasco bv",
        "credit_profile": {
            "amount": {
                "label": "Totaal te betalen",
                "strategy": "same_line_last_amount",
                "confirmed_value": "150.00",
            }
        },
    }
    detection = classify_credit_document(INVOICE_TEXT)
    assert credit_profile_may_apply(inv, detection) is False
    enriched = enrich_credit_document(inv)
    assert enriched.get("amount") != 150.0


def test_credit_profile_applied_for_confirmed_credit_with_key() -> None:
    inv = {
        "type": "credit_note",
        "raw_text": CREDIT_TEXT,
        "supplier_key": "wasco bv",
        "amount_result": {"status": "failed", "value": None, "candidates": []},
        "invoice_number_result": {"status": "failed", "value": None, "candidates": []},
        "credit_profile": {
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
        },
    }
    enriched = enrich_credit_document(inv)
    assert enriched.get("amount") == 150.0
    assert enriched.get("invoice_number") == "CN-12345"
    cd = enriched.get("credit_detection") or {}
    assert cd.get("profile_applied") is True


def test_credit_profile_rebuilds_description_after_invoice_override() -> None:
    inv = {
        "type": "credit_note",
        "customer_number": "104031",
        "invoice_number": "SI25-99999",
        "description": "104031 / SI25-99999",
        "supplier_key": "bitasco trading bv",
        "raw_text": "Bitasco\nnota Nr: SCM23-00472\nTotaal 363,00",
        "amount_result": {"status": "confirmed", "value": "100.00", "candidates": []},
        "invoice_number_result": {
            "status": "confirmed",
            "value": "SI25-99999",
            "candidates": [],
        },
        "credit_profile": {
            "amount": {
                "label": "Totaal",
                "strategy": "same_line_last_amount",
                "confirmed_value": "363.00",
            },
            "credit_number": {
                "label": "nota Nr: ",
                "strategy": "same_line_after_colon",
                "confirmed_value": "SCM23-00472",
            },
        },
    }
    enriched = enrich_credit_document(inv)
    assert enriched.get("invoice_number") == "SCM23-00472"
    assert enriched.get("description") == "104031 / SCM23-00472"


def test_credit_profile_not_applied_without_supplier_key() -> None:
    inv = {
        "type": "credit_note",
        "raw_text": CREDIT_TEXT,
        "credit_profile": {
            "amount": {
                "label": "Totaal te betalen",
                "strategy": "same_line_last_amount",
                "confirmed_value": "150.00",
            },
        },
    }
    enriched = enrich_credit_document(inv)
    assert enriched.get("amount") != 150.0
