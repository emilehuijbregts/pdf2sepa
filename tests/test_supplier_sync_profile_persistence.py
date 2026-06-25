"""Regression: diagnostics/profile state persists through supplier sync write-path."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from main_window import (
    build_supplier_sync_payload_from_parts,
    patch_authoritative_row_fields_into_invoice,
)
from parser.hybrid_field_apply import apply_hybrid_field_extraction
from parser.supplier_db import CUSTOMER_NUMBER_MODE_NONE, SupplierDB
from parser.supplier_matcher import match_suppliers
from ui.field_review import CUSTOMER_ABSENT_PICK_SOURCE, CUSTOMER_ABSENT_STATE

INVOICE_TEXT = """SALO B.V.
Klantnummer: 30146
Factuurnummer: 99999
Totaal EUR 100,00"""

SUPPLIER_NAME = "SALO B.V."
SUPPLIER_IBAN = "NL64ABNA0589033654"


def _absent_customer_result(*, user_selected: bool = True) -> dict:
    return {
        "value": None,
        "selected_value": None,
        "absence_state": CUSTOMER_ABSENT_STATE,
        "source": CUSTOMER_ABSENT_PICK_SOURCE,
        "status": "confirmed",
        "confidence": 100,
        "user_selected": user_selected,
        "user_overridden": True,
        "candidates": [],
        "resolver_finalized": True,
    }


@pytest.fixture
def db_with_supplier(tmp_path: Path) -> SupplierDB:
    data = {
        "suppliers": [
            {
                "name": SUPPLIER_NAME,
                "iban": SUPPLIER_IBAN,
                "discount": 0.0,
                "aliases": [SUPPLIER_NAME],
                "customer_codes": ["30146"],
            }
        ]
    }
    p = tmp_path / "suppliers.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return SupplierDB(path=str(p))


def test_sync_payload_none_mode_ignores_customer_cell() -> None:
    payload = build_supplier_sync_payload_from_parts(
        name=SUPPLIER_NAME,
        iban_cell=SUPPLIER_IBAN,
        customer_code_cell="30146",
        discount_raw="0",
        term_raw="",
        iban_result=None,
        customer_result=_absent_customer_result(),
        row_snap=None,
        supplier_exists=True,
    )
    assert payload["none_mode"] is True
    assert payload["customer_code"] is None
    assert payload["customer_number_mode"] == CUSTOMER_NUMBER_MODE_NONE


def test_sync_payload_user_cleared_iban() -> None:
    payload = build_supplier_sync_payload_from_parts(
        name=SUPPLIER_NAME,
        iban_cell="",
        customer_code_cell="30146",
        discount_raw="0",
        term_raw="",
        iban_result={"user_overridden": True, "value": "", "selected_value": ""},
        customer_result=None,
        row_snap=None,
        supplier_exists=True,
    )
    assert payload["iban"] == ""
    assert payload["iban_user_cleared"] is True
    assert payload["iban_user_overridden"] is True


def test_supplier_exists_by_name_without_iban(db_with_supplier: SupplierDB) -> None:
    assert db_with_supplier.supplier_exists_by_name(SUPPLIER_NAME) is True
    assert db_with_supplier.find_supplier(SUPPLIER_NAME, None, None, match_customer_code=False) is None


def test_update_supplier_clears_iban(db_with_supplier: SupplierDB) -> None:
    assert db_with_supplier.update_supplier(SUPPLIER_NAME, iban="")
    assert db_with_supplier.supplier_exists_by_name(SUPPLIER_NAME)
    sup = next(
        s for s in db_with_supplier.suppliers
        if db_with_supplier._clean_name(s.get("name") or "") == db_with_supplier._clean_name(SUPPLIER_NAME)
    )
    assert sup.get("iban") == ""


def test_sync_gate_allows_existing_supplier_with_cleared_iban(db_with_supplier: SupplierDB) -> None:
    payload = build_supplier_sync_payload_from_parts(
        name=SUPPLIER_NAME,
        iban_cell="",
        customer_code_cell="30146",
        discount_raw="0",
        term_raw="",
        iban_result={"user_overridden": True, "value": "", "selected_value": ""},
        customer_result=None,
        row_snap=None,
        supplier_exists=db_with_supplier.supplier_exists_by_name(SUPPLIER_NAME),
    )
    iban = str(payload.get("iban") or "").strip()
    existing_supplier = bool(payload.get("existing_supplier"))
    iban_user_cleared = bool(payload.get("iban_user_cleared"))
    assert not iban
    assert existing_supplier
    assert iban_user_cleared
    assert not (not iban and not existing_supplier and not iban_user_cleared)


def test_patch_invoice_none_mode_clears_ocr_customer_number() -> None:
    inv = {
        "source_file": "/tmp/invoice.pdf",
        "raw_text": INVOICE_TEXT,
        "customer_number": "30146",
        "customer_number_result": {
            "value": "30146",
            "status": "confirmed",
            "source": "parser",
        },
    }
    payload = build_supplier_sync_payload_from_parts(
        name=SUPPLIER_NAME,
        iban_cell=SUPPLIER_IBAN,
        customer_code_cell="",
        discount_raw="0",
        term_raw="",
        iban_result=None,
        customer_result=_absent_customer_result(),
        row_snap=None,
    )
    patch_authoritative_row_fields_into_invoice(
        inv,
        name=SUPPLIER_NAME,
        payload=payload,
        iban_result=None,
        customer_result=_absent_customer_result(),
        field_results={},
        user_overridden_fields=frozenset({"customer_number"}),
    )
    assert "customer_number" not in inv
    cr = inv.get("customer_number_result")
    assert isinstance(cr, dict)
    assert cr.get("source") == CUSTOMER_ABSENT_PICK_SOURCE
    assert cr.get("user_overridden") is True


def test_patch_invoice_user_cleared_iban() -> None:
    inv = {
        "source_file": "/tmp/invoice.pdf",
        "iban": SUPPLIER_IBAN,
        "iban_result": {"value": SUPPLIER_IBAN, "status": "confirmed"},
    }
    iban_result = {
        "value": "",
        "selected_value": "",
        "user_overridden": True,
        "user_selected": True,
        "status": "confirmed",
    }
    payload = build_supplier_sync_payload_from_parts(
        name=SUPPLIER_NAME,
        iban_cell="",
        customer_code_cell="",
        discount_raw="0",
        term_raw="",
        iban_result=iban_result,
        customer_result=None,
        row_snap=None,
        supplier_exists=True,
    )
    patch_authoritative_row_fields_into_invoice(
        inv,
        name=SUPPLIER_NAME,
        payload=payload,
        iban_result=iban_result,
        customer_result=None,
        field_results={"iban": iban_result},
        user_overridden_fields=frozenset({"iban"}),
    )
    assert inv.get("iban") in ("", None)
    ir = inv.get("iban_result")
    assert isinstance(ir, dict)
    assert ir.get("user_overridden") is True


def test_hybrid_resolver_honors_user_absent_customer_without_db_profile(
    db_with_supplier: SupplierDB,
) -> None:
    invoice = {
        "raw_text": INVOICE_TEXT,
        "customer_number": "30146",
        "customer_number_result": {
            "value": "30146",
            "status": "confirmed",
            "source": "parser",
            "candidates": [{"value": "30146", "source": "parser", "confidence": 80}],
        },
    }
    invoice_copy = deepcopy(invoice)
    invoice_copy["customer_number_result"] = _absent_customer_result()
    supplier = db_with_supplier.find_supplier(SUPPLIER_NAME, SUPPLIER_IBAN) or {}
    apply_hybrid_field_extraction(invoice, invoice_copy, supplier, db_with_supplier)
    assert invoice_copy.get("customer_number_result", {}).get("source") == CUSTOMER_ABSENT_PICK_SOURCE
    assert "customer_number" not in invoice_copy or not str(invoice_copy.get("customer_number") or "").strip()


def test_set_customer_number_mode_on_sync_style_update(db_with_supplier: SupplierDB) -> None:
    assert db_with_supplier.set_customer_number_mode(SUPPLIER_NAME, CUSTOMER_NUMBER_MODE_NONE)
    assert db_with_supplier.get_customer_number_mode(SUPPLIER_NAME) == CUSTOMER_NUMBER_MODE_NONE
    ep = db_with_supplier.get_extraction_profile(SUPPLIER_NAME)
    assert isinstance(ep, dict)
    assert ep.get("customer_number_mode") == CUSTOMER_NUMBER_MODE_NONE


def test_update_supplier_overwrite_customer_codes_clears_list(db_with_supplier: SupplierDB) -> None:
    assert db_with_supplier.update_supplier(
        SUPPLIER_NAME,
        customer_codes=[],
        overwrite_customer_codes=True,
    )
    sup = db_with_supplier.find_supplier(SUPPLIER_NAME, SUPPLIER_IBAN)
    assert sup is not None
    assert sup.get("customer_codes") == []


def test_rematch_after_patch_keeps_absent_customer(db_with_supplier: SupplierDB) -> None:
    db_with_supplier.set_customer_number_mode(SUPPLIER_NAME, CUSTOMER_NUMBER_MODE_NONE)
    parsed = [
        {
            "source_file": "/tmp/invoice.pdf",
            "raw_text": INVOICE_TEXT,
            "supplier_hint": SUPPLIER_NAME,
            "iban": SUPPLIER_IBAN,
            "customer_number": "30146",
        }
    ]
    payload = build_supplier_sync_payload_from_parts(
        name=SUPPLIER_NAME,
        iban_cell=SUPPLIER_IBAN,
        customer_code_cell="",
        discount_raw="0",
        term_raw="",
        iban_result=None,
        customer_result=_absent_customer_result(),
        row_snap=None,
        supplier_exists=True,
    )
    patch_authoritative_row_fields_into_invoice(
        parsed[0],
        name=SUPPLIER_NAME,
        payload=payload,
        iban_result=None,
        customer_result=_absent_customer_result(),
        field_results={},
        user_overridden_fields=frozenset({"customer_number"}),
    )
    matched = match_suppliers(parsed, db_with_supplier)
    inv = matched[0]
    assert not str(inv.get("customer_number") or "").strip()
    cr = inv.get("customer_number_result")
    assert isinstance(cr, dict)
    assert cr.get("absence_state") == CUSTOMER_ABSENT_STATE


def test_rematch_preserves_user_locked_customer_value(db_with_supplier: SupplierDB) -> None:
    """NONE supplier rematch must not wipe user-locked per-document customer number."""
    db_with_supplier.set_customer_number_mode(SUPPLIER_NAME, CUSTOMER_NUMBER_MODE_NONE)
    user_locked = {
        "value": "30146",
        "selected_value": "30146",
        "status": "confirmed",
        "source": "USER_PICKED",
        "user_overridden": True,
        "candidates": [{"value": "30146", "source": "label", "confidence": 90}],
    }
    parsed = [
        {
            "source_file": "/tmp/invoice.pdf",
            "raw_text": INVOICE_TEXT,
            "supplier_hint": SUPPLIER_NAME,
            "iban": SUPPLIER_IBAN,
            "customer_number": "30146",
            "customer_number_result": dict(user_locked),
        }
    ]
    payload = build_supplier_sync_payload_from_parts(
        name=SUPPLIER_NAME,
        iban_cell=SUPPLIER_IBAN,
        customer_code_cell="30146",
        discount_raw="0",
        term_raw="",
        iban_result=None,
        customer_result=user_locked,
        row_snap=None,
        supplier_exists=True,
    )
    patch_authoritative_row_fields_into_invoice(
        parsed[0],
        name=SUPPLIER_NAME,
        payload=payload,
        iban_result=None,
        customer_result=user_locked,
        field_results={"customer_number": user_locked},
        user_overridden_fields=frozenset({"customer_number"}),
    )
    matched = match_suppliers(parsed, db_with_supplier)
    inv = matched[0]
    assert inv.get("customer_number") == "30146"
    cr = inv.get("customer_number_result") or {}
    assert cr.get("selected_value") == "30146"
    assert cr.get("user_overridden") is True


def test_merge_or_add_supplier_skips_customer_code_when_none_locked(
    db_with_supplier: SupplierDB,
) -> None:
    db_with_supplier.set_customer_number_mode(SUPPLIER_NAME, CUSTOMER_NUMBER_MODE_NONE)
    sup_before = db_with_supplier.find_supplier(SUPPLIER_NAME, SUPPLIER_IBAN)
    assert sup_before is not None
    codes_before = list(sup_before.get("customer_codes") or [])

    assert db_with_supplier.merge_or_add_supplier(SUPPLIER_NAME, SUPPLIER_IBAN, "99999")

    sup_after = db_with_supplier.find_supplier(SUPPLIER_NAME, SUPPLIER_IBAN)
    assert sup_after is not None
    codes_after = list(sup_after.get("customer_codes") or [])
    assert codes_after == codes_before
    assert "99999" not in codes_after
