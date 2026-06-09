"""Phase C — golden extraction concern tests (mirrors test_02 field sources)."""

from __future__ import annotations

import pytest

from tests.golden_test_support import (
    EXTRACTION_FIELDS,
    PipelineOutput,
    golden_actual,
    golden_expected,
    is_known_golden_field_failure,
    iter_golden_cases,
    iter_profile_field_cases,
    load_pipeline_with_payments,
    profile_field_matches,
    user_data_dir,
)

pytestmark = [pytest.mark.golden, pytest.mark.golden_slow]


@pytest.fixture(scope="module")
def pipeline_output() -> PipelineOutput:
    out = load_pipeline_with_payments(use_cache=True)
    if not out.invoices_by_pdf:
        pytest.skip("No PDFs in tests/golden_dataset/pdfs/")
    return out


def _golden_extraction_params() -> list[tuple[object, str]]:
    return [(case, field) for case in iter_golden_cases() for field in EXTRACTION_FIELDS]


def _profile_extraction_params() -> list[object]:
    return iter_profile_field_cases(user_data_dir())


@pytest.mark.parametrize(("golden_case", "field"), _golden_extraction_params())
def test_extraction_field(golden_case, field: str, pipeline_output: PipelineOutput) -> None:
    if is_known_golden_field_failure(golden_case, field):
        pytest.xfail("pre-existing golden mismatch; demasked by Phase C atomic test")
    inv = pipeline_output.invoices_by_pdf.get(golden_case.source_file)
    pay = pipeline_output.payments_by_pdf.get(golden_case.source_file)
    expected = golden_expected(golden_case, field)
    actual = golden_actual(golden_case, field, inv, pay) if inv is not None else None
    assert inv is not None and actual == expected, (
        f"Golden extraction mismatch:\n\n"
        f"File: {golden_case.json_path.name}\n\n"
        f"Field: {field}\n"
        f"Expected: {expected}\n"
        f"Actual:   {actual}\n"
    )


@pytest.mark.parametrize("profile_case", _profile_extraction_params())
def test_profile_extraction_field(profile_case) -> None:
    assert profile_field_matches(profile_case), (
        f"Profile extraction mismatch: supplier={profile_case.supplier} "
        f"pdf={profile_case.pdf_name} field={profile_case.field} "
        f"expected={profile_case.expected!r}"
    )
