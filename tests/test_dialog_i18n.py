"""Tests for dialog i18n rendering."""

from __future__ import annotations

import pytest

from logic.batch_load_types import IbanAmbiguityDialogSpec
from ui.i18n import UiStrings, tr


@pytest.fixture(autouse=True)
def reset_language() -> None:
    yield
    UiStrings.set_language("nl")


def test_iban_dialog_nl() -> None:
    spec = IbanAmbiguityDialogSpec(
        ambiguity_index=0,
        supplier_name="Acme BV",
        db_iban="NL91ABNA0417164300",
        pdf_iban="NL99RABO0123456789",
        count=2,
    )
    title = tr(f"{spec.key}.title")
    message = tr(
        f"{spec.key}.message",
        supplier_name=spec.supplier_name,
        db_iban=spec.db_iban,
        pdf_iban=spec.pdf_iban,
        count=spec.count,
    )
    assert title == "IBAN-afwijking gedetecteerd"
    assert "Acme BV" in message
    assert "Aantal facturen: 2" in message
    assert "NL91ABNA0417164300" in message
    assert "NL99RABO0123456789" in message


def test_iban_dialog_en() -> None:
    UiStrings.set_language("en")
    message = tr(
        "dialog.iban.mismatch.message",
        supplier_name="Acme BV",
        db_iban="NL91ABNA0417164300",
        pdf_iban="NL99RABO0123456789",
        count=1,
    )
    assert "Supplier: Acme BV" in message
    assert "Number of invoices: 1" in message
    assert "Database IBAN" in message
