"""Phase C — golden decision concern tests (payment_engine outputs)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.golden_test_support import (
    DECISION_FIELDS,
    PipelineOutput,
    golden_actual,
    golden_expected,
    is_known_golden_field_failure,
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
def test_decision_field(golden_case, field: str, pipeline_output: PipelineOutput) -> None:
    if is_known_golden_field_failure(golden_case, field):
        pytest.xfail("pre-existing golden mismatch; demasked by Phase C atomic test")
    inv = pipeline_output.invoices_by_pdf.get(golden_case.source_file)
    pay = pipeline_output.payments_by_pdf.get(golden_case.source_file)
    expected = golden_expected(golden_case, field)
    actual = golden_actual(golden_case, field, inv, pay) if inv is not None else None
    if field == "amount" and actual is not None:
        expected = str(Decimal(str(expected)).quantize(Decimal("0.01")))
        actual = str(Decimal(str(actual)).quantize(Decimal("0.01")))
    assert inv is not None and actual == expected, (
        f"Golden decision mismatch:\n\n"
        f"File: {golden_case.json_path.name}\n\n"
        f"Field: {field}\n"
        f"Expected: {expected}\n"
        f"Actual:   {actual}\n"
    )
