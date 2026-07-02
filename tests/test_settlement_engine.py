"""SSOT tests for settlement_groups engine output."""

from __future__ import annotations

from decimal import Decimal

import pytest

from logic.payment_engine import calculate_payments
from logic.payment_engine_assembly import OwnershipIndex
from logic.settlement_export import exportable_groups


def _base_invoice(**overrides):
    inv = {
        "supplier_name": "Test BV",
        "match_status": "confirmed",
        "amount": 200.0,
        "amount_excl_vat": 165.29,
        "discount": 0,
        "iban": "NL20INGB0001234567",
        "type": "invoice",
        "invoice_number": "INV-2",
        "invoice_date": "2025-06-01",
        "invoice_date_source": "parsed",
        "supplier_term_trusted": True,
        "supplier_payment_term_days_raw": 0,
    }
    inv.update(overrides)
    return inv


def test_credit_group_net_amount_ssot():
    inv = _base_invoice()
    credit = _base_invoice(amount=50.0, type="credit_note", invoice_number="CR-1")
    result = calculate_payments([inv, credit])
    assert len(result.settlement_groups) == 1
    g = result.settlement_groups[0]
    assert g["exportable"] is True
    assert g["final_amount_due"] == Decimal("150.00")


def test_zero_amount_group_visible_not_exportable():
    inv_a = _base_invoice(invoice_number="INV-A", amount=100.0)
    inv_b = _base_invoice(invoice_number="INV-B", amount=50.0)
    credit = _base_invoice(amount=150.0, type="credit_note", invoice_number="CR-M")
    result = calculate_payments([inv_a, inv_b, credit])
    assert len(result.settlement_groups) == 1
    g = result.settlement_groups[0]
    assert g["settlement_status"] == "zero_amount"
    assert g["exportable"] is False
    assert len(exportable_groups(result).groups) == 0


def test_no_duplicate_export_paths():
    inv_a = _base_invoice(invoice_number="INV-A", amount=100.0)
    inv_b = _base_invoice(invoice_number="INV-B", amount=50.0)
    credit = _base_invoice(amount=50.0, type="credit_note", invoice_number="CR-1")
    result = calculate_payments([inv_a, inv_b, credit])
    exportable = exportable_groups(result).groups
    seen_docs: set[str] = set()
    for g in exportable:
        for doc in g.get("member_documents") or []:
            doc_id = doc.get("document_id") or ""
            assert doc_id not in seen_docs
            seen_docs.add(doc_id)
    assert len(exportable) <= len(result.settlement_groups)


def test_settlement_description_format():
    inv = _base_invoice(invoice_number="INV-B", customer_number="12345")
    credit = _base_invoice(amount=50.0, type="credit_note", invoice_number="CR-1", customer_number="12345")
    result = calculate_payments([inv, credit])
    desc = result.settlement_groups[0]["description"]
    assert "12345" in desc
    assert "INV-B" in desc
    assert "CR-1" in desc


def test_wasco_batch_settlement_amount_span():
    inv_a = _base_invoice(supplier_name="Wasco", invoice_number="5660148", amount=65.51, source_file="5660148.pdf")
    inv_b = _base_invoice(supplier_name="Wasco", invoice_number="6305463", amount=41.16, source_file="6305463.pdf")
    credit = _base_invoice(
        supplier_name="Wasco",
        invoice_number="6230076",
        amount=66.67,
        type="credit_note",
        source_file="6230076.pdf",
    )
    result = calculate_payments([inv_a, inv_b, credit])
    assert len(result.settlement_groups) == 1
    group = result.settlement_groups[0]
    assert group["final_amount_due"] == Decimal("40.00")
    assert group["exportable"] is True
    assert len(group["member_documents_structured"]["invoices"]) == 2
    assert len(group["member_documents_structured"]["credits"]) == 1
    assert [a["status"] for a in group["credit_allocation"]] == ["matched", "matched"]


def test_vte_batch_split_with_unresolved_credit():
    inv_a = _base_invoice(supplier_name="VTE", invoice_number="VF2600048", amount=245.15, source_file="VF2600048.pdf")
    inv_b = _base_invoice(supplier_name="VTE", invoice_number="VF2601788", amount=135.35, source_file="VF2601788.pdf")
    credit_small = _base_invoice(
        supplier_name="VTE",
        invoice_number="VCR2600003",
        amount=33.0,
        type="credit_note",
        source_file="VCR2600003.pdf",
        referenced_invoice_numbers=["VF2600115"],
    )
    credit_large = _base_invoice(
        supplier_name="VTE",
        invoice_number="VCR2600064",
        amount=408.57,
        type="credit_note",
        source_file="VCR2600064.pdf",
        referenced_invoice_numbers=["VF2601543"],
    )
    result = calculate_payments([inv_a, inv_b, credit_small, credit_large])
    assert len(result.settlement_groups) == 3
    unresolved = next(g for g in result.settlement_groups if g["credit_allocation"] and g["credit_allocation"][0]["status"] == "unallocated_full")
    assert unresolved["exportable"] is False
    assert unresolved["settlement_status"] == "manual_review"
    matched = next(g for g in result.settlement_groups if any(a["credit_number"] == "VCR2600003" for a in g["credit_allocation"]))
    assert matched["final_amount_due"] == Decimal("102.35")
    assert len(matched["member_documents_structured"]["invoices"]) == 1
    assert len(matched["member_documents_structured"]["credits"]) == 1


def test_ownership_index_sealed_after_build():
    ownership = OwnershipIndex()
    ownership.add_group_member("g1", "doc-1")
    ownership.validate_complete({"doc-1"})
    ownership.seal()
    with pytest.raises(RuntimeError):
        ownership.add_review_output("g2", "doc-2")
