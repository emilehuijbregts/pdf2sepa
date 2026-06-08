"""Phase C — golden ranking concern tests (production winners vs Phase A.1 snapshot)."""

from __future__ import annotations

import pytest

from parser.field_model import ALL_FIELD_IDS, FieldId
from tests.golden_test_support import (
    iter_golden_cases,
    load_matched_invoices,
    load_ranking_snapshot,
    production_winner,
    snapshot_production_winner,
)

pytestmark = [pytest.mark.golden, pytest.mark.golden_slow]


@pytest.fixture(scope="module")
def matched_by_pdf() -> dict[str, dict]:
    by_pdf = load_matched_invoices(use_cache=True)
    if not by_pdf:
        pytest.skip("No PDFs in tests/golden_dataset/pdfs/")
    return by_pdf


@pytest.fixture(scope="module")
def ranking_snapshot() -> dict:
    snap = load_ranking_snapshot()
    if not snap:
        pytest.skip("Committed Phase A.1 snapshot missing")
    return snap


def _golden_ranking_params() -> list[tuple[object, FieldId]]:
    return [(case, field_id) for case in iter_golden_cases() for field_id in ALL_FIELD_IDS]


@pytest.mark.parametrize(("golden_case", "field_id"), _golden_ranking_params())
def test_ranking_production_winner(
    golden_case,
    field_id: FieldId,
    matched_by_pdf: dict[str, dict],
    ranking_snapshot: dict,
) -> None:
    inv = matched_by_pdf.get(golden_case.source_file)
    live = production_winner(inv, field_id) if inv is not None else {}
    snap = snapshot_production_winner(ranking_snapshot, golden_case.source_file, field_id)
    assert inv is not None and live == snap, (
        f"Ranking winner drift for {golden_case.source_file} :: {field_id}:\n"
        f"  snapshot: {snap!r}\n"
        f"  live:     {live!r}"
    )
