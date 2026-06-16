"""Regression tests for supplier profile customer_number_mode = NONE."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parser.field_candidates import extract_customer_number_result
from parser.hybrid_field_apply import apply_hybrid_field_extraction
from parser.pdf_parser import build_absent_customer_number_snapshot, extract_invoice_data
from parser.supplier_db import CUSTOMER_NUMBER_MODE_NONE, SupplierDB
from parser.supplier_matcher import match_suppliers

# Rich invoice text: labeled customer number, debiteur fallback, and layout cues.
INVOICE_TEXT = """SALO B.V.
Klantnummer: 30146
Debiteurnummer: 12345
Factuurnummer: 99999
Totaal EUR 100,00"""

SUPPLIER_NAME = "SALO B.V."
SUPPLIER_IBAN = "NL64ABNA0589033654"

PROFILE_WITH_NONE = {
    "customer_number_mode": CUSTOMER_NUMBER_MODE_NONE,
    "amount": {
        "label": "Totaal",
        "strategy": "same_line_last_amount",
        "confirmed_value": "100.00",
    },
    # Cached profile field: must not be applied when mode is NONE.
    "customer_number": {
        "label": "Klantnummer",
        "strategy": "same_line_after_colon",
        "confirmed_value": "30146",
    },
}

PROFILE_WITHOUT_NONE = {
    "amount": {
        "label": "Totaal",
        "strategy": "same_line_last_amount",
        "confirmed_value": "100.00",
    },
}


@pytest.fixture
def db_none_mode(tmp_path: Path) -> SupplierDB:
    data = {
        "suppliers": [
            {
                "name": SUPPLIER_NAME,
                "iban": SUPPLIER_IBAN,
                "discount": 0.0,
                "aliases": [SUPPLIER_NAME],
                "customer_codes": ["3503", "30146"],
                "extraction_profile": PROFILE_WITH_NONE,
            }
        ]
    }
    p = tmp_path / "suppliers.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return SupplierDB(path=str(p))


@pytest.fixture
def db_normal_mode(tmp_path: Path) -> SupplierDB:
    data = {
        "suppliers": [
            {
                "name": SUPPLIER_NAME,
                "iban": SUPPLIER_IBAN,
                "discount": 0.0,
                "aliases": [SUPPLIER_NAME],
                "customer_codes": ["3503"],
                "extraction_profile": PROFILE_WITHOUT_NONE,
            }
        ]
    }
    p = tmp_path / "suppliers.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return SupplierDB(path=str(p))


def _assert_absent_customer_number(
    invoice: dict,
    *,
    expect_profile_override: bool = False,
) -> None:
    """Shared assertions for supplier-level «geen klantnummer» outcomes."""
    assert invoice.get("customer_number") is None
    cr = invoice.get("customer_number_result") or {}
    assert cr.get("value") is None
    assert cr.get("selected_value") is None
    assert cr.get("status") == "not_applicable"
    assert cr.get("candidates") == []
    assert cr.get("absence_state") == "NOT_PRESENT_SUPPLIER_LEVEL"
    assert cr.get("source") == "NOT_PRESENT_SUPPLIER_LEVEL"
    if expect_profile_override:
        assert str(cr.get("override_reason") or "") == "supplier_profile_customer_absent"
        assert cr.get("resolver_finalized") is True


def _baseline_invoice_dict(*, parsed: dict | None = None) -> dict:
    base = parsed or {}
    amount_result = base.get("amount_result") or {
        "status": "confirmed",
        "source": "TEST",
        "value": "100.00",
        "candidates": [{"value": "100.00"}],
    }
    return {
        **base,
        "supplier_hint": SUPPLIER_NAME,
        "iban": SUPPLIER_IBAN,
        "raw_text": INVOICE_TEXT,
        "amount": 100.0,
        "amount_result": amount_result,
    }


class TestExtractCustomerNumberNoneMode:
    def test_none_mode_skips_all_extraction_paths(self):
        """Labeled + fallback candidates in text must not surface when mode is NONE."""
        without = extract_customer_number_result(INVOICE_TEXT)
        assert without.value == "30146"
        assert without.candidates

        result = extract_customer_number_result(
            INVOICE_TEXT,
            customer_number_mode=CUSTOMER_NUMBER_MODE_NONE,
        )
        assert result.value is None
        assert result.status == "not_applicable"
        assert result.candidates == []

    def test_none_mode_ignores_cached_resolved_value(self):
        """Legacy resolved/cache input must not leak through when mode is NONE."""
        result = extract_customer_number_result(
            INVOICE_TEXT,
            resolved="30146",
            resolved_source="label",
            customer_number_mode=CUSTOMER_NUMBER_MODE_NONE,
        )
        assert result.value is None
        assert result.status == "not_applicable"
        assert result.candidates == []


class TestHybridFieldApplyNoneMode:
    def test_hybrid_apply_does_not_override_with_profile_or_db(self, db_none_mode: SupplierDB):
        """hybrid_field_apply must force absent snapshot, not profile/db overrides."""
        supplier = db_none_mode.suppliers[0]
        invoice = {
            "raw_text": INVOICE_TEXT,
            "customer_number": "30146",
            "customer_number_result": {
                "value": "30146",
                "selected_value": "30146",
                "status": "confirmed",
                "source": "label",
                "candidates": [
                    {"value": "30146", "source": "label", "confidence": 90},
                    {"value": "12345", "source": "label", "confidence": 70},
                ],
            },
            "iban": SUPPLIER_IBAN,
            "amount": 100.0,
            "amount_result": {
                "status": "confirmed",
                "source": "TEST",
                "value": "100.00",
                "candidates": [{"value": "100.00"}],
            },
        }
        invoice_copy = dict(invoice)

        apply_hybrid_field_extraction(
            invoice,
            invoice_copy,
            supplier,
            db_none_mode,
            amount_status="confirmed",
            use_profile=True,
        )

        _assert_absent_customer_number(invoice_copy, expect_profile_override=True)
        expected = build_absent_customer_number_snapshot()
        cr = invoice_copy["customer_number_result"]
        assert cr.get("resolver_finalized") is True
        assert cr.get("override_reason") == expected["override_reason"]
        assert cr.get("source") == expected["source"]
        assert "3503" not in str(cr.get("value") or "")
        assert "30146" not in str(invoice_copy.get("customer_number") or "")


class TestPipelineNoneMode:
    def test_parse_stage_respects_none_mode(self):
        parsed = extract_invoice_data(
            INVOICE_TEXT,
            extraction_profile=PROFILE_WITH_NONE,
            supplier_name=SUPPLIER_NAME,
        )
        _assert_absent_customer_number(parsed)

    def test_match_suppliers_end_to_end_none_mode(self, db_none_mode: SupplierDB):
        parsed = extract_invoice_data(
            INVOICE_TEXT,
            extraction_profile=PROFILE_WITH_NONE,
            supplier_name=SUPPLIER_NAME,
        )
        # Simulate pre-match invoice that still carries a parsed/scalar customer number.
        inv = _baseline_invoice_dict(parsed=parsed)
        inv["customer_number"] = "30146"
        inv["customer_number_result"] = {
            "value": "30146",
            "status": "confirmed",
            "source": "label",
            "candidates": [{"value": "30146"}, {"value": "12345"}],
        }

        result = match_suppliers([inv], db_none_mode)[0]

        _assert_absent_customer_number(result, expect_profile_override=True)
        assert db_none_mode.get_customer_number_mode(SUPPLIER_NAME) == CUSTOMER_NUMBER_MODE_NONE
        # PDF value may be preserved for audit, but must not become the active field.
        assert result.get("pdf_customer_number") == "30146"
        assert result.get("customer_number") is None


class TestPipelineWithoutNoneMode:
    """Regression safety: extraction still works when profile mode is not NONE."""

    def test_extract_customer_number_without_none_mode(self):
        result = extract_customer_number_result(INVOICE_TEXT)
        assert result.value == "30146"
        assert result.status == "confirmed"
        assert any(c.value == "30146" for c in result.candidates)

    def test_match_suppliers_extracts_customer_number_without_none_mode(self, db_normal_mode: SupplierDB):
        parsed = extract_invoice_data(
            INVOICE_TEXT,
            extraction_profile=PROFILE_WITHOUT_NONE,
            supplier_name=SUPPLIER_NAME,
        )
        assert parsed.get("customer_number") == "30146"
        cr = parsed.get("customer_number_result") or {}
        assert cr.get("value") == "30146"
        assert cr.get("status") == "confirmed"

        inv = _baseline_invoice_dict(parsed=parsed)
        result = match_suppliers([inv], db_normal_mode)[0]

        assert result.get("customer_number") == "30146"
        final_cr = result.get("customer_number_result") or {}
        assert final_cr.get("value") == "30146"
        assert final_cr.get("status") == "confirmed"
        assert db_normal_mode.get_customer_number_mode(SUPPLIER_NAME) is None
