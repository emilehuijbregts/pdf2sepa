"""Phase C — golden decision concern tests (payment_engine outputs)."""

from __future__ import annotations

import pytest

from tests.golden_test_support import (
    DECISION_FIELDS,
    PipelineOutput,
    decision_actual,
    decision_expected,
    iter_golden_cases,
    load_pipeline_with_payments,
)

pytestmark = [pytest.mark.golden, pytest.mark.golden_slow]


@pytest.fixture(scope="module")
def pipeline_output() -> PipelineOutput:
    out = load_pipeline_with_payments(use_cache=True)
    if not out.invoices_by_pdf:
        pytest.skip("No PDFs in tests/golden_dataset/pdfs/")
    return out


def _golden_decision_params() -> list[tuple[object, str]]:
    return [(case, field) for case in iter_golden_cases() for field in DECISION_FIELDS]


@pytest.mark.parametrize(("golden_case", "field"), _golden_decision_params())
def test_decision_field(
    golden_case,
    field: str,
    pipeline_output: PipelineOutput,
) -> None:
    inv = pipeline_output.invoices_by_pdf.get(golden_case.source_file)
    pay = pipeline_output.payments_by_pdf.get(golden_case.source_file)
    expected = decision_expected(golden_case, field)
    actual = decision_actual(inv, pay, golden_case, field) if inv is not None else None
    assert inv is not None and actual == expected, (
        f"Golden decision mismatch:\n\n"
        f"File: {golden_case.json_path.name}\n\n"
        f"Field: {field}\n"
        f"Expected: {expected}\n"
        f"Actual:   {actual}\n"
    )
