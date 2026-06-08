"""Phase C — golden extraction concern tests (pre-payment field scalars)."""

from __future__ import annotations

import pytest

from tests.golden_test_support import (
    EXTRACTION_FIELDS,
    extraction_actual,
    extraction_expected,
    iter_golden_cases,
    iter_profile_field_cases,
    load_matched_invoices,
    profile_field_matches,
    user_data_dir,
)

pytestmark = [pytest.mark.golden, pytest.mark.golden_slow]


@pytest.fixture(scope="module")
def matched_by_pdf() -> dict[str, dict]:
    by_pdf = load_matched_invoices(use_cache=True)
    if not by_pdf:
        pytest.skip("No PDFs in tests/golden_dataset/pdfs/")
    return by_pdf


def _golden_extraction_params() -> list[tuple[object, str]]:
    return [(case, field) for case in iter_golden_cases() for field in EXTRACTION_FIELDS]


def _profile_extraction_params() -> list[object]:
    return iter_profile_field_cases(user_data_dir())


@pytest.mark.parametrize(("golden_case", "field"), _golden_extraction_params())
def test_extraction_field(golden_case, field: str, matched_by_pdf: dict[str, dict]) -> None:
    inv = matched_by_pdf.get(golden_case.source_file)
    expected = extraction_expected(golden_case, field)
    actual = extraction_actual(inv, field) if inv is not None else None
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
