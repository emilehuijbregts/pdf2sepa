"""Phase B4 winner-diff vs committed Phase A.1 snapshot (B3 production baseline)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from parser.field_model import ALL_FIELD_IDS
from tests.snapshot_observability_helpers import build_field_observability
from tests.test_ranking_snapshot import SNAPSHOT_PATH, observability_bundle

REPORT_PATH = Path(__file__).resolve().parents[1] / "reports" / "phase_b4_winner_diff.json"
PHASE_LABEL = "B4"


def test_phase_b4_winner_diff_report(observability_bundle: dict[str, Any]) -> None:
    if not SNAPSHOT_PATH.is_file():
        pytest.skip("Committed Phase A.1 snapshot missing")

    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8") or "{}")
    parse_by_pdf = observability_bundle["parse_by_pdf"]
    matched_by_pdf = observability_bundle["matched_by_pdf"]
    db = observability_bundle["db"]

    production_changes: list[dict[str, Any]] = []

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
                        "reason": "production_pipeline_drift_vs_b3_baseline_snapshot",
                    }
                )

    report = {
        "phase": PHASE_LABEL,
        "note": (
            f"Phase {PHASE_LABEL}: resolver unified on rank_key/rank_candidates; "
            "production winners must match Phase A.1/B3 snapshot."
        ),
        "summary": {"production_winner_changes": len(production_changes)},
        "production_winner_changes": production_changes,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    assert production_changes == [], (
        "Production winners drifted vs B3 baseline:\n"
        + "\n".join(
            f"  {c['pdf']} :: {c['field']}: {c['old_winner']!r} -> {c['new_winner']!r} ({c['reason']})"
            for c in production_changes[:20]
        )
    )
