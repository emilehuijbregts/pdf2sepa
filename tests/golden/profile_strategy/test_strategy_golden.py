"""Golden regression tests for profile strategy engine (learn-mode validation)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from parser.field_model import FieldId
from parser.pdf_parser import extract_text_strict
from parser.profile_strategy_engine import StrategyContext, run_strategies, value_in_raw_text
from tests.golden_test_support import GOLDEN_PDFS_DIR, golden_expected, iter_golden_cases

pytestmark = [pytest.mark.golden, pytest.mark.golden_slow]

CORE_FIELDS: tuple[FieldId, ...] = (
    "amount",
    "invoice_number",
    "customer_number",
    "iban",
)


def _strategy_params() -> list[tuple[object, FieldId]]:
    return [(case, field) for case in iter_golden_cases() for field in CORE_FIELDS]


def _normalize(field_id: FieldId, value: object) -> str | None:
    if value is None:
        return None
    if field_id == "amount":
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    return str(value).strip()


@pytest.mark.parametrize(("golden_case", "field_id"), _strategy_params())
def test_strategy_golden_field(golden_case, field_id: FieldId) -> None:
    pdf_path = GOLDEN_PDFS_DIR / golden_case.source_file
    if not pdf_path.is_file():
        pytest.skip(f"PDF missing: {golden_case.source_file}")

    golden_key = "customer_code" if field_id == "customer_number" else field_id
    if not golden_case.golden.get(golden_key):
        pytest.skip(f"No golden value for {field_id}")

    expected = golden_expected(
        golden_case,
        field_id if field_id != "customer_number" else "customer_code",
    )
    raw_text = extract_text_strict(str(pdf_path))

    if not value_in_raw_text(raw_text, expected, field_id):
        pytest.skip(f"value_not_in_text: {golden_case.source_file} {field_id}")

    ctx = StrategyContext(
        field_id=field_id,
        raw_text=raw_text,
        confirmed_value=expected,
        mode="learn",
        evaluation_mode=True,
    )
    result = run_strategies(field_id, ctx)

    assert result.strategy_used is not None, (
        f"No strategy succeeded for {golden_case.source_file} field={field_id}\n"
        f"trace={result.validation_trace}\n"
        f"attempts={[a.to_dict() for a in result.all_attempted_strategies]}"
    )
    assert result.value is not None
    assert _normalize(field_id, result.value) == _normalize(field_id, expected), (
        f"Strategy extraction mismatch:\n"
        f"PDF: {golden_case.source_file}\n"
        f"Field: {field_id}\n"
        f"Strategy: {result.strategy_used}\n"
        f"Expected: {expected}\n"
        f"Actual: {result.value}\n"
        f"Trace: {result.validation_trace}"
    )
