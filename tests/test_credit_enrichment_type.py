"""Tests for credit enrichment with document type resolution."""

from __future__ import annotations

from logic.credit_enrichment import enrich_credit_document
from logic.document_type_apply import resolve_document_types
from logic.document_type_resolver import resolution_to_dict, resolve_document_type


def test_enrich_credit_document_uses_pre_resolved_invoice_type() -> None:
    inv = {
        "raw_text": "Factuur\nFactuurnummer : 100\nTotaal 10.00",
        "type": "credit_note",
        "referenced_invoice_numbers": ["OLD-1"],
        "amount_sign": "credit",
    }
    resolution = resolve_document_type(
        {
            **inv,
            "extraction_profile": {
                "amount": {
                    "label": "Totaal",
                    "strategy": "same_line_last_amount",
                    "confirmed_value": "10.00",
                },
                "invoice_number": {
                    "label": "Factuurnummer : ",
                    "strategy": "same_line_after_colon",
                    "confirmed_value": "100",
                },
            },
        }
    )
    inv["document_type_resolution"] = resolution_to_dict(resolution)
    out = enrich_credit_document(inv)
    assert out["type"] == "invoice"
    assert out.get("referenced_invoice_numbers") == []


def test_resolve_document_types_batch() -> None:
    invoices = [
        {
            "raw_text": "Creditnota\nnota Nr: CN-1\nTotaal 25.00",
            "type": "invoice",
            "source_file": "/tmp/a.pdf",
            "credit_profile": {
                "amount": {
                    "label": "Totaal",
                    "strategy": "same_line_last_amount",
                    "confirmed_value": "25.00",
                },
                "credit_number": {
                    "label": "nota Nr: ",
                    "strategy": "same_line_after_colon",
                    "confirmed_value": "CN-1",
                },
            },
            "supplier_key": "acme",
        }
    ]
    resolved = resolve_document_types(invoices)
    assert resolved[0]["type"] == "credit_note"
    assert resolved[0]["document_type_resolution"]["source"] == "profile_fit"
