"""Tests for IBAN raw UI answer mapping."""

from __future__ import annotations

from logic.batch_load_types import IbanAmbiguity, IbanRawUiAnswer, IbanRawUiAnswers
from logic.iban_ui_mapping import map_raw_ui_answers_to_decisions


def _amb(index: int = 0) -> IbanAmbiguity:
    return IbanAmbiguity(
        ambiguity_index=index,
        supplier_name="Test BV",
        db_iban="NL91ABNA0417164300",
        pdf_iban="NL99RABO0123456789",
        invoice_count=1,
        source_files=("/tmp/a.pdf",),
    )


def test_clicked_yes_maps_to_use_pdf() -> None:
    amb = (_amb(),)
    raw = IbanRawUiAnswers(answers=(IbanRawUiAnswer(ambiguity_index=0, clicked_yes=True),))
    decisions = map_raw_ui_answers_to_decisions(amb, raw)
    assert decisions.decisions[0].choice == "use_pdf"


def test_clicked_no_maps_to_keep_db() -> None:
    amb = (_amb(),)
    raw = IbanRawUiAnswers(answers=(IbanRawUiAnswer(ambiguity_index=0, clicked_yes=False),))
    decisions = map_raw_ui_answers_to_decisions(amb, raw)
    assert decisions.decisions[0].choice == "keep_db"


def test_missing_answer_defaults_keep_db() -> None:
    amb = (_amb(),)
    raw = IbanRawUiAnswers()
    decisions = map_raw_ui_answers_to_decisions(amb, raw)
    assert decisions.decisions[0].choice == "keep_db"
