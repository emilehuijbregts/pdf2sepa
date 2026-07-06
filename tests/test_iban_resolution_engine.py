"""Tests for IBAN resolution engine."""

from __future__ import annotations

from logic.batch_load_types import (
    IbanUserDecision,
    IbanUserDecisions,
    MatchedInvoiceBatch,
    assert_no_shared_refs,
    freeze_invoice_tuple,
)
from logic.iban_resolution_engine import (
    build_iban_dialog_specs,
    extract_iban_ambiguities,
    resolve_iban_context,
)
from parser.supplier_db import SupplierDBSnapshot


def _matched_with_mismatch() -> MatchedInvoiceBatch:
    inv = {
        "supplier_name": "Test BV",
        "iban": "NL91ABNA0417164300",
        "pdf_iban": "NL99RABO0123456789",
        "iban_mismatch": True,
        "source_file": "/tmp/a.pdf",
        "iban_result": {"value": "NL91ABNA0417164300", "status": "confirmed"},
    }
    return MatchedInvoiceBatch(
        batch_id="v1-test",
        parent_batch_id="v0-test",
        invoices=freeze_invoice_tuple([inv]),
    )


def test_extract_iban_ambiguities() -> None:
    v1 = _matched_with_mismatch()
    amb = extract_iban_ambiguities(v1)
    assert len(amb) == 1
    assert amb[0].supplier_name == "Test BV"


def test_build_iban_dialog_specs_structured() -> None:
    v1 = _matched_with_mismatch()
    amb = extract_iban_ambiguities(v1)
    specs = build_iban_dialog_specs(amb)
    spec = specs[0]
    assert spec.key == "dialog.iban.mismatch"
    assert spec.supplier_name == "Test BV"
    assert spec.count == 1
    assert spec.db_iban == "NL91ABNA0417164300"
    assert spec.pdf_iban == "NL99RABO0123456789"
    assert not hasattr(spec, "message")


def test_resolve_iban_context_deterministic() -> None:
    v1 = _matched_with_mismatch()
    decisions = IbanUserDecisions(
        decisions=(
            IbanUserDecision(
                supplier_name="Test BV",
                pdf_iban="NL99RABO0123456789",
                choice="keep_db",
            ),
        )
    )
    snapshot = SupplierDBSnapshot.from_path("data/suppliers.json")
    v2a = resolve_iban_context(v1, decisions, snapshot)
    v2b = resolve_iban_context(v1, decisions, snapshot)
    assert v2a.iban_resolution_map == v2b.iban_resolution_map
    assert v2a.invoices[0].get("iban_mismatch") is None


def test_resolve_does_not_share_refs_with_v1() -> None:
    v1 = _matched_with_mismatch()
    decisions = IbanUserDecisions(
        decisions=(
            IbanUserDecision(
                supplier_name="Test BV",
                pdf_iban="NL99RABO0123456789",
                choice="use_pdf",
            ),
        )
    )
    snapshot = SupplierDBSnapshot.from_path("data/suppliers.json")
    v2 = resolve_iban_context(v1, decisions, snapshot)
    assert_no_shared_refs(v1.invoices, v2.invoices)


def test_v1_nested_mutation_does_not_affect_v2() -> None:
    inv = {
        "supplier_name": "Test BV",
        "iban": "NL91ABNA0417164300",
        "pdf_iban": "NL99RABO0123456789",
        "iban_mismatch": True,
        "source_file": "/tmp/a.pdf",
        "iban_result": {"value": "NL91ABNA0417164300", "status": "confirmed"},
        "items": [1],
    }
    v1 = MatchedInvoiceBatch(
        batch_id="v1-test",
        parent_batch_id="v0-test",
        invoices=freeze_invoice_tuple([inv]),
    )
    decisions = IbanUserDecisions(
        decisions=(
            IbanUserDecision(
                supplier_name="Test BV",
                pdf_iban="NL99RABO0123456789",
                choice="keep_db",
            ),
        )
    )
    snapshot = SupplierDBSnapshot.from_path("data/suppliers.json")
    v2 = resolve_iban_context(v1, decisions, snapshot)
    inv = list(v1.invoices)[0]
    inv["items"].append("mutated")
    assert v2.invoices[0]["items"] == [1]
