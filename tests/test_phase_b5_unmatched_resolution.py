"""Phase B5 — unmatched suppliers route parser winners through resolve_field."""

from __future__ import annotations

import json
from pathlib import Path

from parser.hybrid_field_apply import apply_generic_field_resolution
from parser.supplier_db import SupplierDB
from parser.supplier_matcher import match_suppliers


def _invoice_with_parser_results() -> dict:
    return {
        "supplier_hint": "Unknown Corp",
        "iban": "NL00XXXX9999999999",
        "customer_number": "999",
        "amount": 42.50,
        "amount_result": {
            "candidates": [
                {
                    "value": "42.50",
                    "source": "total_line_hint",
                    "confidence": 40,
                    "context": "Totaal 42,50",
                    "type": "incl",
                },
                {
                    "value": "42.50",
                    "source": "total_label_payable",
                    "confidence": 90,
                    "context": "Te betalen 42,50",
                    "type": "incl",
                }
            ],
            "value": "42.50",
            "selected_value": "42.50",
            "confidence": 90,
            "source": "TOTAL_LABEL_PAYABLE",
            "status": "confirmed",
        },
        "invoice_number": "INV-42",
        "invoice_number_result": {
            "candidates": [
                {
                    "value": "INV-42",
                    "source": "label",
                    "confidence": 90,
                    "context": "Factuurnummer INV-42",
                }
            ],
            "value": "INV-42",
            "selected_value": "INV-42",
            "confidence": 90,
            "source": "label",
            "status": "confirmed",
        },
    }


def test_apply_generic_field_resolution_preserves_parser_winners() -> None:
    invoice = _invoice_with_parser_results()
    invoice_copy = dict(invoice)

    apply_generic_field_resolution(invoice, invoice_copy)

    assert invoice_copy["amount"] == 42.5
    assert invoice_copy["invoice_number"] == "INV-42"
    assert invoice_copy["amount_result"]["resolver_finalized"] is True
    assert invoice_copy["invoice_number_result"]["resolver_finalized"] is True
    assert invoice_copy["amount_result"]["selected_value"] == "42.50"
    assert invoice_copy["amount_result"]["source"] == "TOTAL_LABEL_PAYABLE"
    assert invoice_copy["amount_result"]["confidence"] == 90
    assert invoice_copy["invoice_number_result"]["selected_value"] == "INV-42"


def test_unmatched_supplier_routes_existing_parser_results_through_resolver(tmp_path: Path) -> None:
    db_path = tmp_path / "suppliers.json"
    db_path.write_text(
        json.dumps(
            {
                "suppliers": [
                    {
                        "name": "Known Supplier",
                        "iban": "NL25CITI0266075452",
                        "aliases": [],
                        "customer_codes": ["1012146"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    db = SupplierDB(path=str(db_path))

    result = match_suppliers([_invoice_with_parser_results()], db)[0]

    assert result["match_status"] == "unmatched"
    assert result["supplier_match_source"] == "unmatched"
    assert result["amount"] == 42.5
    assert result["invoice_number"] == "INV-42"
    assert result["amount_result"]["resolver_finalized"] is True
    assert result["invoice_number_result"]["resolver_finalized"] is True
    assert result["amount_result"]["source"] == "TOTAL_LABEL_PAYABLE"
    assert result["amount_result"]["confidence"] == 90
