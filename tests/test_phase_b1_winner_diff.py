"""Phase B1 — winner-diff report vs Phase A.1 snapshot (no production behavior change)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from parser.field_candidates import rank_key
from parser.field_model import ALL_FIELD_IDS, FieldCandidate
from tests.snapshot_observability_helpers import (
    build_field_observability,
    resolver_rank_key,
)
from tests.test_ranking_snapshot import SNAPSHOT_PATH, observability_bundle

REPORT_PATH = Path(__file__).resolve().parents[1] / "reports" / "phase_b1_winner_diff.json"


def _candidate_from_snapshot_row(field_id: str, row: dict[str, Any]) -> FieldCandidate:
    meta: dict[str, Any] = {"field_id": field_id}
    if row.get("payable_score") is not None:
        meta["payable_score"] = row["payable_score"]
    if row.get("amount_type"):
        meta["type"] = row["amount_type"]
    return FieldCandidate(
        value=row.get("value"),
        source=str(row.get("source") or ""),
        confidence=int(row.get("confidence") or 0),
        meta=meta,
    )


def test_phase_b1_winner_diff_report(observability_bundle: dict[str, Any]) -> None:
    if not SNAPSHOT_PATH.is_file():
        pytest.skip("Committed Phase A.1 snapshot missing")

    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8") or "{}")
    parse_by_pdf = observability_bundle["parse_by_pdf"]
    matched_by_pdf = observability_bundle["matched_by_pdf"]
    db = observability_bundle["db"]

    production_changes: list[dict[str, Any]] = []
    rank_key_mismatches: list[dict[str, Any]] = []

    for pdf in sorted(matched_by_pdf):
        inv_m = matched_by_pdf[pdf]
        inv_p = parse_by_pdf.get(pdf)
        if not inv_p:
            continue
        snap_fields = snapshot.get(pdf) or {}
        from tests.snapshot_observability_helpers import supplier_for_matched

        supplier = supplier_for_matched(inv_m, db)

        for field_id in ALL_FIELD_IDS:
            live = build_field_observability(inv_p, inv_m, supplier, db, field_id)
            snap_field = snap_fields.get(field_id) or {}
            live_prod = live["production"]["winner"]
            snap_prod = (snap_field.get("production") or {}).get("winner") or snap_field.get(
                "winner", {}
            )
            if live_prod != snap_prod:
                production_changes.append(
                    {
                        "pdf": pdf,
                        "field": field_id,
                        "old_winner": snap_prod,
                        "new_winner": live_prod,
                        "reason": "production_pipeline_drift_vs_phase_a1_snapshot",
                    }
                )

            resolver_rows = (snap_field.get("resolver_stage") or {}).get("candidates") or []
            for row in resolver_rows:
                fc = _candidate_from_snapshot_row(field_id, row)
                legacy_rk = list(resolver_rank_key(field_id, fc))
                canonical_rk = list(rank_key(field_id, fc, context="resolver"))
                if legacy_rk != canonical_rk:
                    rank_key_mismatches.append(
                        {
                            "pdf": pdf,
                            "field": field_id,
                            "value": row.get("value"),
                            "legacy_rank_key": legacy_rk,
                            "canonical_rank_key": canonical_rk,
                            "reason": "b1_rank_key_extraction_mismatch",
                        }
                    )

    report = {
        "phase": "B1",
        "note": (
            "B1 extracts rank_key/rank_candidates only; production paths are not wired. "
            "production_winner_changes must be 0. rank_key_mismatches must be 0."
        ),
        "summary": {
            "production_winner_changes": len(production_changes),
            "rank_key_mismatches": len(rank_key_mismatches),
        },
        "production_winner_changes": production_changes,
        "rank_key_mismatches": rank_key_mismatches,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    assert production_changes == [], (
        "Production winners drifted vs Phase A.1 snapshot:\n"
        + "\n".join(
            f"  {c['pdf']} :: {c['field']}: {c['old_winner']!r} -> {c['new_winner']!r}"
            for c in production_changes[:20]
        )
    )
    assert rank_key_mismatches == [], (
        f"B1 rank_key must match legacy resolver keys ({len(rank_key_mismatches)} mismatches); "
        f"see {REPORT_PATH}"
    )
