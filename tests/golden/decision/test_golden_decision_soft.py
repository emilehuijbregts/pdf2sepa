"""
Golden Suite v2 split:

- extraction = hard contract (must never break)
- decision = soft behavior contract (allowed to evolve)
- ranking = debug signal only (non-blocking)

This prevents engine improvements from causing false regressions.
"""

from __future__ import annotations

import pytest

from tests.golden_test_support import (
    SOFT_DECISION_FIELDS,
    SOFT_LEGACY_FIELDS,
    PipelineOutput,
    golden_actual,
    golden_expected,
    iter_golden_cases,
    load_pipeline_with_payments,
    production_winner,
)

pytestmark = [pytest.mark.golden, pytest.mark.golden_slow, pytest.mark.soft]


@pytest.fixture(scope="module")
def pipeline_output() -> PipelineOutput:
    out = load_pipeline_with_payments(use_cache=True)
    if not out.invoices_by_pdf:
        pytest.skip("No PDFs in tests/golden_dataset/pdfs/")
    return out


def _soft_decision_params() -> list[tuple[object, str]]:
    return [(case, field) for case in iter_golden_cases() for field in SOFT_DECISION_FIELDS]


def _soft_legacy_params() -> list[tuple[object, str]]:
    return [(case, field) for case in iter_golden_cases() for field in SOFT_LEGACY_FIELDS]


@pytest.mark.parametrize(("golden_case", "field"), _soft_decision_params())
def test_decision_field(golden_case, field: str, pipeline_output: PipelineOutput) -> None:
    inv = pipeline_output.invoices_by_pdf.get(golden_case.source_file)
    pay = pipeline_output.payments_by_pdf.get(golden_case.source_file)
    expected = golden_expected(golden_case, field)
    actual = golden_actual(golden_case, field, inv, pay) if inv is not None else None
    if inv is not None and actual != expected:
        amount_winner = production_winner(inv, "amount")
        print(
            f"DECISION_DIAGNOSTIC {golden_case.source_file} :: {field}\n"
            f"  amount_winner: {amount_winner!r}"
        )
    assert inv is not None and actual == expected, (
        f"Golden decision mismatch:\n\n"
        f"File: {golden_case.json_path.name}\n\n"
        f"Field: {field}\n"
        f"Expected: {expected}\n"
        f"Actual:   {actual}\n"
    )


@pytest.mark.parametrize(("golden_case", "field"), _soft_legacy_params())
def test_legacy_golden_field(golden_case, field: str, pipeline_output: PipelineOutput) -> None:
    inv = pipeline_output.invoices_by_pdf.get(golden_case.source_file)
    pay = pipeline_output.payments_by_pdf.get(golden_case.source_file)
    expected = golden_expected(golden_case, field)
    actual = golden_actual(golden_case, field, inv, pay) if inv is not None else None
    assert inv is not None and actual == expected, (
        f"Golden legacy field mismatch:\n\n"
        f"File: {golden_case.json_path.name}\n\n"
        f"Field: {field}\n"
        f"Expected: {expected}\n"
        f"Actual:   {actual}\n"
    )
