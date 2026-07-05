"""Child row diagnostics: frozen snapshot is row SSOT (UI + diagnostics)."""

from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QTableWidget

import main_window as mw
from logic.credit_settlement import document_id
from logic.diagnostics import build_invoice_diagnostics_snapshot
from ui.settlement_expand import SettlementRowKind

_REPO = Path(__file__).resolve().parents[1]


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


def _child_row_spec(doc_id: str, **overrides: object) -> dict:
    spec = {
        "kind": SettlementRowKind.INVOICE_CHILD,
        "document_id": doc_id,
        "amount": "100.00",
        "meta": {},
    }
    spec.update(overrides)
    return spec


def _render_child_row(win: mw.MainWindow, inv: dict, **spec_overrides: object) -> None:
    doc_id = document_id({"raw": inv})
    win._table.insertRow(0)
    win._apply_settlement_child_row_full(0, _child_row_spec(doc_id, **spec_overrides), "grp-1")


def _stub_window(matched: list[dict]) -> mw.MainWindow:
    win = mw.MainWindow.__new__(mw.MainWindow)
    win._matched_invoices = matched
    win._table = QTableWidget(0, int(mw.PaymentColumn.SETTLEMENT) + 1)
    return win


def test_freeze_immutable_row_snapshot_double_deepcopy() -> None:
    inv = _base_invoice()
    nested = {"status": "confirmed", "value": "100.00", "nested": {"a": 1}}
    inv["amount_result"] = nested
    snap = mw._freeze_immutable_row_snapshot(inv)
    nested["value"] = "999.00"
    nested["nested"]["a"] = 99
    inv["supplier_name"] = "Mutated"
    assert snap["amount_result"]["value"] == "100.00"
    assert snap["amount_result"]["nested"]["a"] == 1
    assert snap["supplier_name"] == "Acme BV"


def test_build_snapshot_does_not_mutate_input() -> None:
    inv = _base_invoice()
    before = deepcopy(inv)
    build_invoice_diagnostics_snapshot(inv)
    assert inv == before


def test_no_inv_usage_after_freeze_in_child_row() -> None:
    source = (_REPO / "main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn_node: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_apply_settlement_child_row_full":
            fn_node = node
            break
    assert fn_node is not None

    del_inv_line: int | None = None
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "inv":
                    del_inv_line = node.lineno

    assert del_inv_line is not None, "expected del inv in _apply_settlement_child_row_full"

    violations: list[str] = []
    for node in ast.walk(fn_node):
        if getattr(node, "lineno", 0) <= del_inv_line:
            continue
        if isinstance(node, ast.Name) and node.id == "inv":
            violations.append(f"inv reference after del inv at line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "spec":
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and arg.value in (
                            "raw_invoice",
                            "supplier_name",
                            "label",
                        ):
                            violations.append(
                                f"spec.get({arg.value!r}) after freeze at line {node.lineno}"
                            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("_ident_field_display_from_inv", "_remittance_display_from_inv"):
                if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id != "snapshot":
                    violations.append(
                        f"{node.func.id} first arg is not snapshot at line {node.lineno}"
                    )

    assert violations == [], "SSOT violations:\n" + "\n".join(violations)


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


def test_field_roles_only_from_snapshot(qapp) -> None:
    inv = _base_invoice(
        vat_number="NL123",
        kvk_number="12345678",
        email_domain="acme.nl",
    )
    win = _stub_window([inv])
    _render_child_row(win, inv)
    sup_it = win._table.item(0, int(mw.PaymentColumn.SUPPLIER))
    amt_it = win._table.item(0, int(mw.PaymentColumn.AMOUNT))
    iban_it = win._table.item(0, int(mw.PaymentColumn.IBAN))
    cust_it = win._table.item(0, int(mw.PaymentColumn.CUSTOMER_CODE))
    diag = sup_it.data(mw._ROW_INVOICE_DIAGNOSTICS_ROLE)
    expected = mw._freeze_immutable_row_snapshot(inv)

    role_checks = [
        (mw._ROW_AMOUNT_RESULT_ROLE, amt_it, "amount_result"),
        (mw._ROW_IBAN_RESULT_ROLE, iban_it, "iban_result"),
        (mw._ROW_INVOICE_NUMBER_RESULT_ROLE, sup_it, "invoice_number_result"),
        (mw._ROW_CUSTOMER_NUMBER_RESULT_ROLE, cust_it, "customer_number_result"),
    ]
    for role, item, key in role_checks:
        role_val = item.data(role)
        assert role_val == expected[key]
        assert role_val is not expected[key]
        assert role_val is not diag[key]

    assert sup_it.data(mw._ROW_VAT_NUMBER_ROLE) == "NL123"
    assert sup_it.data(mw._ROW_KVK_NUMBER_ROLE) == "12345678"
    assert sup_it.data(mw._ROW_EMAIL_DOMAIN_ROLE) == "acme.nl"
    assert diag == expected
    assert diag is not expected


def test_field_role_mutation_does_not_affect_diagnostics_snapshot(qapp) -> None:
    inv = _base_invoice(
        amount_result={
            "status": "confirmed",
            "value": "100.00",
            "confidence": 95,
            "candidates": [{"value": "100.00", "score": 0.9}],
        }
    )
    win = _stub_window([inv])
    _render_child_row(win, inv)
    sup_it = win._table.item(0, int(mw.PaymentColumn.SUPPLIER))
    amt_it = win._table.item(0, int(mw.PaymentColumn.AMOUNT))
    diag = sup_it.data(mw._ROW_INVOICE_DIAGNOSTICS_ROLE)
    amt_role = amt_it.data(mw._ROW_AMOUNT_RESULT_ROLE)
    assert isinstance(diag, dict) and isinstance(amt_role, dict)
    assert diag["amount_result"] is not amt_role
    amt_role["value"] = "MUTATED"
    assert diag["amount_result"]["value"] == "100.00"
    assert isinstance(amt_role["candidates"], list)
    assert isinstance(diag["amount_result"]["candidates"], list)
    assert amt_role["candidates"] is not diag["amount_result"]["candidates"]
    amt_role["candidates"].append({"value": "999.00", "score": 0.1})
    assert len(diag["amount_result"]["candidates"]) == 1
    assert diag["amount_result"]["candidates"][0]["value"] == "100.00"


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


def test_snapshot_is_immutable_after_inv_mutation(qapp) -> None:
    inv = _base_invoice()
    win = _stub_window([inv])
    _render_child_row(
        win,
        inv,
        supplier_name="Spec Supplier WRONG",
        raw_invoice={"supplier_name": "Raw WRONG"},
        label="Wrong label",
    )
    snap_before = deepcopy(win._get_row_invoice_diagnostics_snapshot(0))
    inv["supplier_name"] = "Mutated After Freeze"
    inv["amount_result"] = {"status": "failed", "value": "1.00"}
    snap_after = win._get_row_invoice_diagnostics_snapshot(0)
    assert snap_before == snap_after
    assert win._table.item(0, int(mw.PaymentColumn.SUPPLIER)).text() == "Acme BV"


def test_mutating_inv_does_not_change_row(qapp) -> None:
    test_snapshot_is_immutable_after_inv_mutation(qapp)


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
