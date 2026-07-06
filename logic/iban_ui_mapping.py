"""Pure mapping from raw UI answers to domain IBAN decisions."""

from __future__ import annotations

from logic.batch_load_types import (
    IbanAmbiguity,
    IbanRawUiAnswer,
    IbanRawUiAnswers,
    IbanUserDecision,
    IbanUserDecisions,
)


def map_raw_ui_answers_to_decisions(
    ambiguities: tuple[IbanAmbiguity, ...],
    raw_answers: IbanRawUiAnswers,
) -> IbanUserDecisions:
    """Map raw QMessageBox answers to domain decisions (clicked_yes → use_pdf)."""
    by_index: dict[int, IbanRawUiAnswer] = {a.ambiguity_index: a for a in raw_answers.answers}
    decisions: list[IbanUserDecision] = []
    for amb in ambiguities:
        raw = by_index.get(amb.ambiguity_index)
        if raw is None or not raw.clicked_yes:
            choice: str = "keep_db"
        else:
            choice = "use_pdf"
        decisions.append(
            IbanUserDecision(
                supplier_name=amb.supplier_name,
                pdf_iban=amb.pdf_iban,
                choice=choice,  # type: ignore[arg-type]
            )
        )
    return IbanUserDecisions(decisions=tuple(decisions))
