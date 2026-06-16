"""Display rules when customer_number_mode is NONE (main_window helpers)."""

from __future__ import annotations

from main_window import (
    _customer_number_none_mode_active,
    _customer_number_none_mode_from_parts,
    _ident_field_display_from_inv,
    _remittance_display_from_inv,
)
from ui.field_review import CUSTOMER_ABSENT_PICK_SOURCE, CUSTOMER_ABSENT_STATE


def _absent_inv(*, invoice_number: str = "INV/2026/00364") -> dict:
    return {
        "invoice_number": invoice_number,
        "invoice_number_result": {
            "value": invoice_number,
            "status": "confirmed",
        },
        "customer_number_result": {
            "value": None,
            "selected_value": None,
            "absence_state": CUSTOMER_ABSENT_STATE,
            "source": CUSTOMER_ABSENT_PICK_SOURCE,
            "status": "confirmed",
            "user_selected": True,
        },
    }


def test_none_mode_withdraws_customer_cell() -> None:
    inv = _absent_inv()
    assert _customer_number_none_mode_active(inv)
    assert _ident_field_display_from_inv(inv, "customer_number") == ""


def test_none_mode_remittance_is_invoice_only() -> None:
    inv = _absent_inv()
    assert _remittance_display_from_inv(inv) == "INV/2026/00364"
    assert " / " not in _remittance_display_from_inv(inv)
    assert "Geen klantnummer" not in _remittance_display_from_inv(inv)


def test_none_mode_ignores_stale_description_composite() -> None:
    inv = _absent_inv()
    inv["description"] = "OLD-CUST / INV/2026/00364"
    assert _remittance_display_from_inv(inv) == "INV/2026/00364"


def test_none_mode_ignores_pdf_customer_number_audit_field() -> None:
    """pdf_customer_number is audit-only; must not re-activate customer display in NONE mode."""
    inv = {
        "extraction_profile": {"customer_number_mode": "NONE"},
        "pdf_customer_number": "75187760",
        "invoice_number": "INV/2026/00364",
        "invoice_number_result": {"value": "INV/2026/00364", "status": "confirmed"},
        "customer_number_result": {
            "value": None,
            "absence_state": CUSTOMER_ABSENT_STATE,
            "status": "not_applicable",
            "source": "NOT_PRESENT_SUPPLIER_LEVEL",
        },
    }
    assert _customer_number_none_mode_active(inv)
    assert _ident_field_display_from_inv(inv, "customer_number") == ""
    assert _remittance_display_from_inv(inv) == "INV/2026/00364"


def test_none_mode_from_row_result_snapshot() -> None:
    absent_result = {
        "value": None,
        "absence_state": CUSTOMER_ABSENT_STATE,
        "source": CUSTOMER_ABSENT_PICK_SOURCE,
        "status": "confirmed",
        "user_selected": True,
    }
    assert _customer_number_none_mode_from_parts(
        snap={"pdf_customer_number": "STALE"},
        customer_result=absent_result,
    )


def test_profile_none_mode_without_user_selected() -> None:
    inv = {
        "extraction_profile": {"customer_number_mode": "NONE"},
        "invoice_number": "VP601987",
        "invoice_number_result": {"value": "VP601987", "status": "confirmed"},
        "customer_number_result": {
            "value": None,
            "absence_state": CUSTOMER_ABSENT_STATE,
            "status": "not_applicable",
            "source": "NOT_PRESENT_SUPPLIER_LEVEL",
        },
    }
    assert _customer_number_none_mode_active(inv)
    assert _ident_field_display_from_inv(inv, "customer_number") == ""
    assert _remittance_display_from_inv(inv) == "VP601987"


def test_stale_scalar_with_absent_result_still_none_mode() -> None:
    """Stale scalar must not block absent/NONE display."""
    inv = _absent_inv()
    inv["customer_number"] = "75187760"
    assert _customer_number_none_mode_active(inv)
    assert _ident_field_display_from_inv(inv, "customer_number") == ""
    assert _remittance_display_from_inv(inv) == "INV/2026/00364"
