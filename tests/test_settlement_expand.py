"""UI tests for settlement expand and badges."""

from __future__ import annotations

from ui.settlement_badges import settlement_badge_nl
from ui.settlement_expand import breakdown_child_rows, expand_indicator, header_supplier_label, vm_from_group


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


def test_breakdown_child_rows_expanded():
    vm = vm_from_group(_sample_group())
    rows = breakdown_child_rows(vm, expanded=True)
    labels = [r["label"] for r in rows]
    assert "Facturen" in labels
    assert "INV-1" in labels
    assert "Credits" in labels
    assert "CR-1" in labels
    assert "Toewijzing" in labels
    assert "CR-1 -> INV-1" in labels
    assert "Te betalen" in labels


def test_breakdown_child_rows_unallocated_credit():
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
    vm = vm_from_group(group)
    rows = breakdown_child_rows(vm, expanded=True)
    assert any(r["label"] == "CR-X -> UNMATCHED" for r in rows)


def test_header_supplier_label():
    vm = vm_from_group(_sample_group())
    assert "Test BV" in header_supplier_label(vm, False)
    assert header_supplier_label(vm, False).startswith("▶")
