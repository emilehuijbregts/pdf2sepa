"""UI tests for settlement expand and badges."""

from __future__ import annotations

from ui.settlement_badges import settlement_badge_nl
from ui.settlement_expand import (
    SettlementRowKind,
    _ROW_SETTLEMENT_DOC_ID_ROLE,
    _ROW_SETTLEMENT_DOC_TYPE_ROLE,
    _ROW_SETTLEMENT_SOURCE_PDF_ROLE,
    _ROW_SETTLEMENT_SUPPLIER_ROLE,
    breakdown_child_rows,
    expand_indicator,
    header_supplier_label,
    vm_from_group,
)


def _sample_group():
    return {
        "group_id": "g1",
        "supplier_name": "Test BV",
        "settlement_status": "ok",
        "exportable": True,
        "final_amount_due": "150.00",
        "description": "test",
        "credit_allocation": [
            {
                "credit_number": "CR-1",
                "invoice_number": "INV-1",
                "amount_applied": "50.00",
                "remaining_balance": "0.00",
                "status": "matched",
            }
        ],
        "member_documents": [
            {
                "document_id": "doc-inv-1",
                "raw": {
                    "type": "invoice",
                    "invoice_number": "INV-1",
                    "gross_amount": "200.00",
                    "supplier_name": "Test BV",
                    "iban": "NL91ABNA0417164300",
                    "customer_number": "C-100",
                    "source_file": "/invoices/inv1.pdf",
                },
            },
            {
                "document_id": "doc-cr-1",
                "raw": {
                    "type": "credit_note",
                    "invoice_number": "CR-1",
                    "gross_amount": "50.00",
                    "supplier_name": "Test BV",
                    "iban": "NL91ABNA0417164300",
                    "customer_number": "C-100",
                    "source_file": "/invoices/cr1.pdf",
                },
            },
        ],
        "breakdown": {
            "invoices_total": "200.00",
            "credits_total": "50.00",
            "linked_groups": [
                {
                    "invoices": [
                        {
                            "doc_type": "invoice",
                            "invoice_number": "INV-1",
                            "gross_amount": "200.00",
                            "amount_applied": "200.00",
                        }
                    ],
                    "credits": [
                        {
                            "doc_type": "credit_note",
                            "invoice_number": "CR-1",
                            "gross_amount": "50.00",
                            "amount_applied": "50.00",
                        }
                    ],
                }
            ],
        },
    }


def test_settlement_badges():
    assert settlement_badge_nl("ok") == "OK"
    assert settlement_badge_nl("zero_amount") == "Volledig verrekend"
    assert settlement_badge_nl("manual_review") == "Controle credit"
    assert settlement_badge_nl("refund_required") == "Terugbetaling"


def test_expand_indicator():
    assert expand_indicator(False) == "▶"
    assert expand_indicator(True) == "▼"


def test_breakdown_child_rows_collapsed_empty():
    vm = vm_from_group(_sample_group())
    assert breakdown_child_rows(vm, expanded=False) == []


def test_breakdown_child_rows_expanded_kinds():
    """Expanded rows must contain exactly one INVOICE_CHILD and one CREDIT_CHILD."""
    group = _sample_group()
    vm = vm_from_group(group)
    rows = breakdown_child_rows(vm, expanded=True, group=group)
    kinds = [r["kind"] for r in rows]
    assert SettlementRowKind.INVOICE_CHILD in kinds
    assert SettlementRowKind.CREDIT_CHILD in kinds
    # No legacy section headers
    labels = [r["label"] for r in rows]
    assert "Facturen" not in labels
    assert "Credits" not in labels
    assert "Toewijzing" not in labels
    assert "Te betalen" not in labels


def test_breakdown_child_rows_expanded_labels():
    """Invoice and credit numbers appear as row labels."""
    group = _sample_group()
    vm = vm_from_group(group)
    rows = breakdown_child_rows(vm, expanded=True, group=group)
    labels = [r["label"] for r in rows]
    assert "INV-1" in labels
    assert "CR-1" in labels


def test_breakdown_child_rows_metadata_enriched():
    """With group supplied, specs carry document_id and raw_invoice."""
    group = _sample_group()
    vm = vm_from_group(group)
    rows = breakdown_child_rows(vm, expanded=True, group=group)

    inv_row = next(r for r in rows if r["kind"] == SettlementRowKind.INVOICE_CHILD)
    assert inv_row["document_id"] == "doc-inv-1"
    assert inv_row["raw_invoice"].get("iban") == "NL91ABNA0417164300"
    assert inv_row["supplier_name"] == "Test BV"
    assert inv_row["group_id"] == "g1"

    cr_row = next(r for r in rows if r["kind"] == SettlementRowKind.CREDIT_CHILD)
    assert cr_row["document_id"] == "doc-cr-1"
    assert cr_row["raw_invoice"].get("source_file") == "/invoices/cr1.pdf"


def test_breakdown_child_rows_credit_amount_negated():
    """Credit amount in spec must be negative (deduction)."""
    group = _sample_group()
    vm = vm_from_group(group)
    rows = breakdown_child_rows(vm, expanded=True, group=group)
    cr_row = next(r for r in rows if r["kind"] == SettlementRowKind.CREDIT_CHILD)
    assert str(cr_row["amount"]).startswith("-"), f"Expected negative amount, got {cr_row['amount']!r}"


def test_breakdown_child_rows_no_group_still_works():
    """Calling without group must not raise and must return basic rows (no metadata)."""
    vm = vm_from_group(_sample_group())
    rows = breakdown_child_rows(vm, expanded=True)
    assert any(r["kind"] == SettlementRowKind.INVOICE_CHILD for r in rows)
    assert any(r["kind"] == SettlementRowKind.CREDIT_CHILD for r in rows)
    # No metadata when group not provided
    inv_row = next(r for r in rows if r["kind"] == SettlementRowKind.INVOICE_CHILD)
    assert inv_row["document_id"] == ""
    assert inv_row["raw_invoice"] == {}


def test_breakdown_child_rows_unallocated_credit_warning():
    """Unallocated credit produces a WARNING_CHILD row."""
    group = _sample_group()
    group["credit_allocation"] = [
        {
            "credit_number": "CR-X",
            "invoice_number": None,
            "amount_applied": "0.00",
            "remaining_balance": "408.57",
            "status": "unallocated_full",
        }
    ]
    # Add matching member_document for CR-X so the credit line appears
    group["member_documents"].append({
        "document_id": "doc-cr-x",
        "raw": {
            "type": "credit_note",
            "invoice_number": "CR-X",
            "gross_amount": "408.57",
            "supplier_name": "Test BV",
            "source_file": "/invoices/crx.pdf",
        },
    })
    # Patch breakdown to include CR-X as an unallocated credit
    group["breakdown"]["linked_groups"][0]["credits"].append({
        "doc_type": "credit_note",
        "invoice_number": "CR-X",
        "gross_amount": "408.57",
        "amount_applied": "0.00",
        "remaining_balance": "408.57",
    })
    vm = vm_from_group(group)
    rows = breakdown_child_rows(vm, expanded=True, group=group)
    assert any(r["kind"] == SettlementRowKind.WARNING_CHILD for r in rows), (
        "Expected a WARNING_CHILD row for unallocated credit"
    )


def test_header_supplier_label():
    vm = vm_from_group(_sample_group())
    assert "Test BV" in header_supplier_label(vm, False)
    assert header_supplier_label(vm, False).startswith("▶")


def test_new_role_constants_exported():
    """New UserRole constants must be importable from settlement_expand."""
    assert _ROW_SETTLEMENT_DOC_TYPE_ROLE != _ROW_SETTLEMENT_SUPPLIER_ROLE
    assert _ROW_SETTLEMENT_SUPPLIER_ROLE != _ROW_SETTLEMENT_SOURCE_PDF_ROLE
    assert _ROW_SETTLEMENT_DOC_ID_ROLE != _ROW_SETTLEMENT_DOC_TYPE_ROLE
