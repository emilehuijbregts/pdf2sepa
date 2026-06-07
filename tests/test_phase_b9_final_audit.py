"""Phase B9 — final Phase B validation and intentional-change audit."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from parser.field_model import ALL_FIELD_IDS
from tests.snapshot_observability_helpers import build_field_observability
from tests.test_ranking_snapshot import SNAPSHOT_PATH, observability_bundle

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "phase_b_final_audit.json"

PHASE_REPORT_PATHS = [
    REPO_ROOT / "tests" / "reports" / "phase_b1_winner_diff.json",
    REPO_ROOT / "reports" / "phase_b2_winner_diff.json",
    REPO_ROOT / "reports" / "phase_b3_winner_diff.json",
    REPO_ROOT / "reports" / "phase_b4_winner_diff.json",
    REPO_ROOT / "reports" / "phase_b5_winner_diff.json",
    REPO_ROOT / "reports" / "phase_b6_winner_diff.json",
    REPO_ROOT / "reports" / "phase_b7_winner_diff.json",
    REPO_ROOT / "reports" / "phase_b8_winner_diff.json",
]

INTENTIONAL_CHANGES: list[dict[str, str]] = [
    {
        "phase": "B1",
        "change": "Extract canonical rank_key/rank_candidates in field_candidates.py (no production wiring).",
        "production_impact": "none",
    },
    {
        "phase": "B2",
        "change": "Resolver amount/invoice_date delegate to rank_key(context=resolver).",
        "production_impact": "none",
    },
    {
        "phase": "B3",
        "change": "Parse-time amount selection delegates to rank_key/rank_candidates(context=parse).",
        "production_impact": "none",
    },
    {
        "phase": "B4",
        "change": "Resolver unified on rank_key/rank_candidates; ident helpers remain legacy aliases.",
        "production_impact": "none",
    },
    {
        "phase": "B5",
        "change": "Unmatched suppliers route parser winners through resolve_field(empty overrides).",
        "production_impact": "none",
    },
    {
        "phase": "B6",
        "change": "Parser field scalars written only via apply_resolved_field_result after resolve_field.",
        "production_impact": "none",
    },
    {
        "phase": "B7",
        "change": "Field status assigned only in _build_result; payment_engine reads status read-only.",
        "production_impact": "none",
    },
    {
        "phase": "B5-B7",
        "change": "Production matched/unmatched paths set resolver_finalized=true after resolve_field (metadata only; winners unchanged).",
        "production_impact": "metadata_only",
    },
    {
        "phase": "B8",
        "change": "Remove unused G5 dead resolver/candidate helpers.",
        "production_impact": "none",
    },
]

REVERTED_CHANGES: list[dict[str, str]] = []

KNOWN_ARCHITECTURE_GAPS: list[dict[str, str]] = [
    {
        "area": "ui",
        "location": "main_window.py",
        "detail": "Post-resolve status mutation on user-repair paths (resolved.status / resolved_dict status).",
        "phase_b_scope": "deferred",
    },
    {
        "area": "parse",
        "location": "parser/field_candidates.py build_ident_field_result",
        "detail": "Parse-stage field status is decided before resolve_field (separate from resolver status authority).",
        "phase_b_scope": "by_design",
    },
    {
        "area": "parse",
        "location": "parser/pdf_parser.py extract_invoice_data",
        "detail": "Legacy amount_source/amount_confidence mirror writes after apply_generic_field_resolution.",
        "phase_b_scope": "deferred",
    },
    {
        "area": "loader",
        "location": "logic/invoice_folder_loader.py",
        "detail": "Direct date_dict status assignment outside resolver pipeline.",
        "phase_b_scope": "deferred",
    },
    {
        "area": "parity",
        "location": "tests/test_pipeline_parity.py",
        "detail": "Three documented parse-vs-resolver ranking divergences (K-prefix, cross-field penalty, amount key shape).",
        "phase_b_scope": "documented_intentional",
    },
]


def _load_phase_reports() -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = []
    for path in PHASE_REPORT_PATHS:
        if path.is_file():
            loaded.append(json.loads(path.read_text(encoding="utf-8") or "{}"))
    return loaded


def _architecture_checks() -> dict[str, Any]:
    resolver_src = (REPO_ROOT / "parser" / "field_resolver.py").read_text(encoding="utf-8")
    hybrid_src = (REPO_ROOT / "parser" / "hybrid_field_apply.py").read_text(encoding="utf-8")
    payment_src = (REPO_ROOT / "logic" / "payment_engine.py").read_text(encoding="utf-8")
    supplier_src = (REPO_ROOT / "parser" / "supplier_matcher.py").read_text(encoding="utf-8")
    pdf_parser_src = (REPO_ROOT / "parser" / "pdf_parser.py").read_text(encoding="utf-8")

    ranking = {
        "canonical_rank_key_exists": "def rank_key(" in (REPO_ROOT / "parser" / "field_candidates.py").read_text(encoding="utf-8"),
        "amount_pick_key_delegates_rank_key": "_amount_pick_key" in pdf_parser_src and "rank_key(" in pdf_parser_src,
        "resolver_rank_key_delegates_rank_key": "_resolver_rank_key" in resolver_src and "return rank_key(" in resolver_src,
        "dead_cap_helper_removed": "_cap_amount_tentative" not in hybrid_src,
    }

    authority = {
        "resolve_field_defined": "def resolve_field(" in resolver_src,
        "apply_resolved_field_result_defined": "def apply_resolved_field_result(" in (REPO_ROOT / "parser" / "resolved_field_apply.py").read_text(encoding="utf-8"),
        "hybrid_uses_resolve_field": "resolve_field(" in hybrid_src,
        "unmatched_uses_apply_generic_field_resolution": "apply_generic_field_resolution(invoice, invoice_copy)" in supplier_src,
        "extract_invoice_data_uses_apply_generic_field_resolution": "apply_generic_field_resolution(" in pdf_parser_src,
    }

    status = {
        "build_result_has_amount_profile_review_cap": "amount_profile_review_cap" in resolver_src,
        "payment_engine_no_status_repromotion": "match_st == \"confirmed\" and conf >= 85" not in payment_src,
        "post_resolve_cap_removed": "_cap_amount_tentative" not in hybrid_src,
    }

    routing = {
        "unmatched_supplier_resolve_field_path": "apply_generic_field_resolution(invoice, invoice_copy)" in supplier_src,
    }

    goals = {
        "ranking": all(ranking.values()),
        "authority": all(authority.values()),
        "status": all(status.values()),
        "routing": all(routing.values()),
    }

    return {
        "ranking_checks": ranking,
        "authority_checks": authority,
        "status_checks": status,
        "routing_checks": routing,
        "phase_b_goals_met": goals,
        "remaining_architecture_gaps": KNOWN_ARCHITECTURE_GAPS,
    }


def _post_resolve_status_violations() -> list[str]:
    violations: list[str] = []
    for rel in (
        "parser/hybrid_field_apply.py",
        "logic/payment_engine.py",
        "parser/resolved_field_apply.py",
    ):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        if "_cap_amount_tentative" in text:
            violations.append(f"{rel}: _cap_amount_tentative still present")
    payment = (REPO_ROOT / "logic" / "payment_engine.py").read_text(encoding="utf-8")
    if "match_st == \"confirmed\" and conf >= 85" in payment:
        violations.append("logic/payment_engine.py: profile amount status re-promotion still present")
    return violations


def test_phase_b9_final_audit_report(observability_bundle: dict[str, Any]) -> None:
    if not SNAPSHOT_PATH.is_file():
        pytest.skip("Committed Phase A.1 snapshot missing")

    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8") or "{}")
    parse_by_pdf = observability_bundle["parse_by_pdf"]
    matched_by_pdf = observability_bundle["matched_by_pdf"]
    db = observability_bundle["db"]

    production_winner_changes: list[dict[str, Any]] = []
    status_changes: list[dict[str, Any]] = []
    ranking_changes: list[dict[str, Any]] = []
    authority_changes: list[dict[str, Any]] = []

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
            snap_prod = (snap_field.get("production") or {}).get("winner") or snap_field.get("winner", {})
            if live_prod != snap_prod:
                production_winner_changes.append(
                    {
                        "pdf": pdf,
                        "field": field_id,
                        "old_winner": snap_prod,
                        "new_winner": live_prod,
                        "classification": "unexplained",
                    }
                )

            live_status = str(live_prod.get("status") or "")
            snap_status = str(snap_prod.get("status") or "")
            if live_status != snap_status:
                status_changes.append(
                    {
                        "pdf": pdf,
                        "field": field_id,
                        "old_status": snap_status,
                        "new_status": live_status,
                        "winner_value": live_prod.get("value"),
                        "classification": "unexplained",
                    }
                )

            for stage in ("parse_stage", "resolver_stage"):
                live_stage = live.get(stage) or {}
                snap_stage = snap_field.get(stage) or {}
                live_order = live_stage.get("ordering") or []
                snap_order = snap_stage.get("ordering") or []
                if live_order != snap_order:
                    ranking_changes.append(
                        {
                            "pdf": pdf,
                            "field": field_id,
                            "stage": stage,
                            "old_ordering": snap_order,
                            "new_ordering": live_order,
                            "classification": "unexplained",
                        }
                    )

            live_rf = bool(live["production"].get("resolver_finalized"))
            snap_rf = bool((snap_field.get("production") or {}).get("resolver_finalized"))
            if live_rf != snap_rf:
                classification = "intentional"
                if not snap_rf and live_rf and live_prod == snap_prod and live_status == snap_status:
                    classification = "intentional_resolver_finalized_propagation"
                elif live_prod != snap_prod or live_status != snap_status:
                    classification = "unexplained"
                authority_changes.append(
                    {
                        "pdf": pdf,
                        "field": field_id,
                        "old_resolver_finalized": snap_rf,
                        "new_resolver_finalized": live_rf,
                        "classification": classification,
                    }
                )

    phase_reports = _load_phase_reports()
    per_phase_winner_totals = {
        str(r.get("phase", "?")): int((r.get("summary") or {}).get("production_winner_changes", 0))
        for r in phase_reports
    }

    architecture = _architecture_checks()
    post_resolve_violations = _post_resolve_status_violations()

    intentional_authority = [c for c in authority_changes if c.get("classification") != "unexplained"]

    unexplained = (
        production_winner_changes
        + [c for c in status_changes if c.get("classification") == "unexplained"]
        + [c for c in ranking_changes if c.get("classification") == "unexplained"]
        + [c for c in authority_changes if c.get("classification") == "unexplained"]
    )

    goals_met = architecture["phase_b_goals_met"]
    phase_b_complete = (
        not unexplained
        and not post_resolve_violations
        and all(goals_met.values())
    )

    report = {
        "phase": "B9",
        "baseline": "tests/snapshots/phase_a_ranking_snapshot.json",
        "summary": {
            "production_winner_changes": len(production_winner_changes),
            "status_changes": len(status_changes),
            "ranking_changes": len(ranking_changes),
            "authority_changes": len(authority_changes),
            "intentional_authority_changes": len(intentional_authority),
            "unexplained_changes": len(unexplained),
            "post_resolve_status_violations": len(post_resolve_violations),
            "phase_b_goals_met": goals_met,
            "recommendation": "Phase B complete" if phase_b_complete else "Phase B not complete",
        },
        "production_winner_changes": production_winner_changes,
        "status_changes": status_changes,
        "ranking_changes": ranking_changes,
        "authority_changes": authority_changes,
        "intentional_authority_changes": intentional_authority,
        "intentional_changes": INTENTIONAL_CHANGES,
        "reverted_changes": REVERTED_CHANGES,
        "unexplained_changes": unexplained,
        "per_phase_winner_diff_totals": per_phase_winner_totals,
        "architecture_audit": architecture,
        "post_resolve_status_violations": post_resolve_violations,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    assert unexplained == [], (
        "Unexplained Phase B drift vs Phase A.1:\n"
        + json.dumps(unexplained[:20], indent=2, ensure_ascii=False)
    )
    assert post_resolve_violations == [], (
        "Post-resolve status violations remain:\n" + "\n".join(post_resolve_violations)
    )
    assert phase_b_complete, report["summary"]["recommendation"]


def test_phase_b9_architecture_static_scan() -> None:
    """Supplement observability audit with static checks on resolver/hybrid/payment modules."""
    violations = _post_resolve_status_violations()
    assert violations == []

    hybrid_tree = ast.parse((REPO_ROOT / "parser" / "hybrid_field_apply.py").read_text(encoding="utf-8"))
    hybrid_names = {n.id for n in ast.walk(hybrid_tree) if isinstance(n, ast.Name)}
    assert "_cap_amount_tentative" not in hybrid_names

    resolver_text = (REPO_ROOT / "parser" / "field_resolver.py").read_text(encoding="utf-8")
    assert "amount_profile_review_cap" in resolver_text
    assert "def _build_result(" in resolver_text
