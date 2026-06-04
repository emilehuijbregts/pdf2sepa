"""Phase B6 — parser scalars are applied only through resolved field writer."""

from __future__ import annotations

from typing import Any

from parser.field_model import ALL_FIELD_IDS
from parser.pdf_parser import extract_invoice_data


def test_extract_invoice_data_applies_field_scalars_via_resolved_writer(monkeypatch) -> None:
    import parser.hybrid_field_apply as hybrid_apply

    original = hybrid_apply.apply_resolved_field_result
    applied: list[str] = []

    def _recording_apply(*args: Any, **kwargs: Any) -> None:
        applied.append(str(args[1]))
        original(*args, **kwargs)

    monkeypatch.setattr(hybrid_apply, "apply_resolved_field_result", _recording_apply)

    out = extract_invoice_data(
        "Factuurnummer INV-42\n"
        "Klantnummer C-7\n"
        "Factuurdatum 01-04-2026\n"
        "Totaal te betalen EUR 42,50\n"
        "IBAN NL20 INGB 0001 2345 67\n"
        "KvK 24489568\n"
        "BTW NL822167037B01\n"
        "Contact billing@example.nl\n"
    )

    for field_id in ALL_FIELD_IDS:
        assert field_id in applied

    assert out["amount"] == 42.5
    assert out["invoice_number"] == "INV-42"
    assert out["customer_number"] == "C-7"
    assert out["invoice_date"] == "2026-04-01"
    assert out["iban"] == "NL20INGB0001234567"
    assert out["kvk_number"] == "24489568"
    assert out["vat_number"] == "NL822167037B01"
    assert out["email_domain"] == "example.nl"


def test_extract_invoice_data_preserves_missing_scalar_keys_via_resolved_writer() -> None:
    out = extract_invoice_data("")

    for key in (
        "amount",
        "iban",
        "invoice_number",
        "customer_number",
        "invoice_date",
        "kvk_number",
        "vat_number",
        "email_domain",
    ):
        assert key in out
        assert out[key] is None
