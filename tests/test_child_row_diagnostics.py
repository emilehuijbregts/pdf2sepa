"""Child row diagnostics: frozen snapshot is row SSOT (UI + diagnostics)."""

from __future__ import annotations

from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication, QTableWidget

import main_window as mw
from logic.credit_settlement import document_id
from logic.diagnostics import build_invoice_diagnostics_snapshot
from ui.settlement_expand import SettlementRowKind


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _base_invoice(**overrides: object) -> dict:
    inv = {
        "source_file": "/tmp/Factuur-123.pdf",
        "supplier_name": "Acme BV",
        "supplier_hint": "Acme",
        "match_status": "confirmed",
        "iban": "NL20INGB0001234567",
        "invoice_number": "F-001",
        "customer_number": "K42",
        "invoice_date": "2025-01-15",
        "invoice_date_source": "parsed",
        "type": "invoice",
        "amount_result": {
            "status": "confirmed",
            "source": "total_label_payable",
            "value": "100.00",
            "confidence": 95,
            "candidates": [],
        },
        "iban_result": {
            "status": "confirmed",
            "value": "NL20INGB0001234567",
            "confidence": 88,
            "source": "pdf_text",
            "candidates": [],
        },
        "invoice_number_result": {
            "status": "confirmed",
            "value": "F-001",
            "confidence": 90,
            "candidates": [],
        },
        "customer_number_result": {
            "status": "confirmed",
            "value": "K42",
            "confidence": 85,
            "candidates": [],
        },
    }
    inv.update(overrides)
    return inv


def _stub_window(matched: list[dict]) -> mw.MainWindow:
    win = mw.MainWindow.__new__(mw.MainWindow)
    win._matched_invoices = matched
    win._table = QTableWidget(0, int(mw.PaymentColumn.SETTLEMENT) + 1)
    return win


def test_build_snapshot_does_not_mutate_input() -> None:
    inv = _base_invoice()
    before = deepcopy(inv)
    build_invoice_diagnostics_snapshot(inv)
    assert inv == before


def test_outer_deepcopy_isolates_nested_refs() -> None:
    inv = _base_invoice()
    nested = {"status": "confirmed", "value": "100.00", "nested": {"a": 1}}
    inv["match_info"] = nested
    snap_outer = deepcopy(build_invoice_diagnostics_snapshot(inv))
    nested["value"] = "999.00"
    nested["nested"]["a"] = 99
    assert snap_outer["match_info"]["value"] == "100.00"
    assert snap_outer["match_info"]["nested"]["a"] == 1


def test_field_roles_match_snapshot_subfields() -> None:
    inv = _base_invoice()
    snapshot = deepcopy(build_invoice_diagnostics_snapshot(inv))
    amt = mw._role_snap_from_snapshot(snapshot, "amount_result")
    assert amt == snapshot["amount_result"]
    assert amt is not snapshot["amount_result"]


def test_helpers_receive_snapshot_only(qapp) -> None:
    inv = _base_invoice()
    snapshot = deepcopy(build_invoice_diagnostics_snapshot(inv))
    cust = mw._ident_field_display_from_inv(snapshot, "customer_number")
    assert cust == "K42"
    inv["customer_number"] = "MUTATED"
    assert mw._ident_field_display_from_inv(snapshot, "customer_number") == "K42"


def test_no_spec_fallback_for_invoice_ui(qapp) -> None:
    inv = _base_invoice(supplier_name="Snapshot Supplier")
    doc_id = document_id({"raw": inv})
    win = _stub_window([inv])
    win._table.insertRow(0)
    spec = {
        "kind": SettlementRowKind.INVOICE_CHILD,
        "document_id": doc_id,
        "amount": "100.00",
        "supplier_name": "Spec Supplier WRONG",
        "raw_invoice": {"supplier_name": "Raw WRONG", "iban": "NL00WRONG"},
        "meta": {"doc_type": "invoice"},
    }
    win._apply_settlement_child_row_full(0, spec, "grp-1")
    sup_it = win._table.item(0, int(mw.PaymentColumn.SUPPLIER))
    iban_it = win._table.item(0, int(mw.PaymentColumn.IBAN))
    assert sup_it is not None
    assert iban_it is not None
    assert sup_it.text() == "Snapshot Supplier"
    assert iban_it.text() == "NL20INGB0001234567"


def test_mutating_inv_does_not_change_row(qapp) -> None:
    inv = _base_invoice()
    doc_id = document_id({"raw": inv})
    win = _stub_window([inv])
    win._table.insertRow(0)
    spec = {
        "kind": SettlementRowKind.INVOICE_CHILD,
        "document_id": doc_id,
        "amount": "100.00",
        "meta": {},
    }
    win._apply_settlement_child_row_full(0, spec, "grp-1")
    snap_before = deepcopy(win._get_row_invoice_diagnostics_snapshot(0))
    inv["supplier_name"] = "Mutated After Freeze"
    inv["amount_result"] = {"status": "failed", "value": "1.00"}
    snap_after = win._get_row_invoice_diagnostics_snapshot(0)
    assert snap_before == snap_after
    assert win._table.item(0, int(mw.PaymentColumn.SUPPLIER)).text() == "Acme BV"


def test_settlement_amount_is_presentation_only(qapp) -> None:
    inv = _base_invoice(
        amount_result={
            "status": "confirmed",
            "value": "80.00",
            "confidence": 90,
            "candidates": [],
        }
    )
    doc_id = document_id({"raw": inv})
    win = _stub_window([inv])
    win._table.insertRow(0)
    spec = {
        "kind": SettlementRowKind.CREDIT_CHILD,
        "document_id": doc_id,
        "amount": "-100.00",
        "meta": {"doc_type": "credit_note"},
    }
    win._apply_settlement_child_row_full(0, spec, "grp-1")
    amt_it = win._table.item(0, int(mw.PaymentColumn.AMOUNT))
    role = amt_it.data(mw._ROW_AMOUNT_RESULT_ROLE)
    assert amt_it.text() == "-100,00" or amt_it.text() == "-100.00" or "-100" in amt_it.text()
    assert isinstance(role, dict)
    assert role.get("value") == "80.00"
    snap, limited = win._invoice_diagnostics_snapshot_for_display(0)
    assert limited is False
    assert snap.get("amount_result", {}).get("value") == "80.00"


def test_child_diagnostics_not_limited(qapp) -> None:
    inv = _base_invoice()
    doc_id = document_id({"raw": inv})
    win = _stub_window([inv])
    win._table.insertRow(0)
    spec = {
        "kind": SettlementRowKind.INVOICE_CHILD,
        "document_id": doc_id,
        "amount": "100.00",
        "meta": {},
    }
    win._apply_settlement_child_row_full(0, spec, "grp-1")
    _, limited = win._invoice_diagnostics_snapshot_for_display(0)
    assert limited is False


def test_child_row_gets_full_diagnostics_roles(qapp) -> None:
    inv = _base_invoice()
    doc_id = document_id({"raw": inv})
    win = _stub_window([inv])
    win._table.insertRow(0)
    spec = {
        "kind": SettlementRowKind.INVOICE_CHILD,
        "document_id": doc_id,
        "amount": "100.00",
        "meta": {},
    }
    win._apply_settlement_child_row_full(0, spec, "grp-1")
    sup_it = win._table.item(0, int(mw.PaymentColumn.SUPPLIER))
    amt_it = win._table.item(0, int(mw.PaymentColumn.AMOUNT))
    iban_it = win._table.item(0, int(mw.PaymentColumn.IBAN))
    cust_it = win._table.item(0, int(mw.PaymentColumn.CUSTOMER_CODE))
    assert isinstance(sup_it.data(mw._ROW_INVOICE_DIAGNOSTICS_ROLE), dict)
    assert isinstance(amt_it.data(mw._ROW_AMOUNT_RESULT_ROLE), dict)
    assert isinstance(iban_it.data(mw._ROW_IBAN_RESULT_ROLE), dict)
    assert isinstance(cust_it.data(mw._ROW_CUSTOMER_NUMBER_RESULT_ROLE), dict)
    assert sup_it.data(mw._ROW_INVOICE_META_ROLE) == "F-001"


def test_child_row_has_info_column(qapp) -> None:
    inv = _base_invoice()
    doc_id = document_id({"raw": inv})
    win = _stub_window([inv])
    win._table.insertRow(0)
    spec = {
        "kind": SettlementRowKind.INVOICE_CHILD,
        "document_id": doc_id,
        "amount": "100.00",
        "meta": {},
    }
    win._apply_settlement_child_row_full(0, spec, "grp-1")
    info_it = win._table.item(0, int(mw.PaymentColumn.INFO))
    assert info_it is not None
    assert info_it.text() == "🔍"


def test_warning_row_stays_limited(qapp) -> None:
    win = _stub_window([])
    win._table.insertRow(0)
    spec = {
        "kind": SettlementRowKind.WARNING_CHILD,
        "label": "Something wrong",
        "supplier_name": "Acme",
    }
    win._apply_settlement_child_row_full(0, spec, "grp-1")
    _, limited = win._invoice_diagnostics_snapshot_for_display(0)
    assert limited is True
