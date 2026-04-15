"""Tests for logic/payment_amounts.py — strict money parsing."""

from __future__ import annotations

from decimal import Decimal

import pytest

from logic.payment_amounts import (
    amount_to_decimal,
    format_eur_xml,
    incl_amount_to_excl_for_discount,
    normalize_supplier_vat_rate_pct,
    resolved_payment_amount_for_export,
    sum_decimals,
)


class TestAmountToDecimal:
    def test_none_raises(self):
        with pytest.raises(ValueError, match="None"):
            amount_to_decimal(None)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty"):
            amount_to_decimal("")
        with pytest.raises(ValueError, match="empty"):
            amount_to_decimal("   ")

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError, match="invalid"):
            amount_to_decimal("abc")

    def test_bool_raises(self):
        with pytest.raises(ValueError, match="bool"):
            amount_to_decimal(True)

    def test_decimal(self):
        assert amount_to_decimal(Decimal("12.345")) == Decimal("12.35")

    def test_int(self):
        assert amount_to_decimal(7) == Decimal("7.00")

    def test_float(self):
        assert amount_to_decimal(1.1) == Decimal("1.10")

    def test_string_comma(self):
        assert amount_to_decimal("1234,56") == Decimal("1234.56")

    def test_string_dot(self):
        assert amount_to_decimal(" 99.9 ") == Decimal("99.90")


class TestHelpers:
    def test_format_eur_xml(self):
        assert format_eur_xml(Decimal("3")) == "3.00"

    def test_sum_decimals(self):
        assert sum_decimals([Decimal("1.10"), Decimal("2.20")]) == Decimal("3.30")


class TestVatExcl:
    def test_normalize_vat_rate(self):
        assert normalize_supplier_vat_rate_pct(21) == 21
        assert normalize_supplier_vat_rate_pct(0) == 0
        assert normalize_supplier_vat_rate_pct(None) == 21
        assert normalize_supplier_vat_rate_pct(150) == 21

    def test_incl_to_excl_21(self):
        assert incl_amount_to_excl_for_discount(Decimal("121.00"), 21) == Decimal("100.00")

    def test_incl_to_excl_0(self):
        assert incl_amount_to_excl_for_discount(Decimal("100.00"), 0) == Decimal("100.00")


class TestResolvedPaymentAmountForExport:
    def test_user_selected_uses_snapshot_not_cell(self):
        ar = {
            "status": "confirmed",
            "user_selected": True,
            "value": "250.00",
            "selected_amount": "250.00",
        }
        d = resolved_payment_amount_for_export(amount_cell_text="100,00", amount_result=ar)
        assert d == Decimal("250.00")

    def test_user_selected_missing_value_raises(self):
        ar = {"status": "confirmed", "user_selected": True, "value": None}
        with pytest.raises(ValueError, match="user_selected"):
            resolved_payment_amount_for_export(amount_cell_text="100,00", amount_result=ar)

    def test_confirmed_uses_value_before_cell(self):
        ar = {
            "status": "confirmed",
            "value": "137.60",
            "selected_amount": "137.60",
        }
        d = resolved_payment_amount_for_export(amount_cell_text="999,99", amount_result=ar)
        assert d == Decimal("137.60")

    def test_failed_snapshot_falls_back_to_cell(self):
        ar = {"status": "failed", "user_selected": False, "value": None}
        d = resolved_payment_amount_for_export(amount_cell_text="88,50", amount_result=ar)
        assert d == Decimal("88.50")

    def test_invalid_cell_raises(self):
        with pytest.raises(ValueError):
            resolved_payment_amount_for_export(amount_cell_text="abc", amount_result=None)
