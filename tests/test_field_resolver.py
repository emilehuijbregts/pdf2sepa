"""Tests voor deterministische field_resolver."""

from __future__ import annotations

from parser.field_candidates import IdentFieldCandidate, build_ident_field_result
from parser.field_model import FieldCandidate, FieldResult
from parser.field_resolver import resolve_field


def _to_field_candidate(c: IdentFieldCandidate) -> FieldCandidate:
    return FieldCandidate(
        value=c.value,
        source=c.source,
        confidence=c.confidence,
        context=c.context,
        label=c.label,
        meta=dict(c.meta or {}),
    )


def _generic_invoice(candidates: list[FieldCandidate]) -> FieldResult:
    return FieldResult(
        field_id="invoice_number",
        candidates=candidates,
        selected_value=None,
        confidence=max((int(c.confidence or 0) for c in candidates), default=0),
        source="UNKNOWN",
        status="tentative",
    )


def _generic_iban(candidates: list[FieldCandidate]) -> FieldResult:
    return FieldResult(
        field_id="iban",
        candidates=candidates,
        selected_value=None,
        confidence=max((int(c.confidence or 0) for c in candidates), default=0),
        source="UNKNOWN",
        status="tentative",
    )


class TestDeterministicResolverParity:
    def test_identical_candidate_set_matches_field_candidates_winner(self):
        ident_candidates = [
            IdentFieldCandidate(
                value="YEAR",
                source="year_slash_ref",
                confidence=82,
                context="Factuur 26/1234567",
                label="Factuur",
            ),
            IdentFieldCandidate(
                value="COLON",
                source="factuur_colon",
                confidence=82,
                context="Factuur: COLON",
                label="Factuur",
            ),
        ]
        ident_result = build_ident_field_result(ident_candidates, field_id="invoice_number")
        generic = _generic_invoice([_to_field_candidate(c) for c in ident_candidates])
        resolver_result = resolve_field("invoice_number", generic, [])
        assert resolver_result.selected_value == ident_result.value

    def test_resolver_deterministic_across_repeated_runs(self):
        candidates = [
            FieldCandidate(value="A", source="label", confidence=88, context="", label="Factuurnummer"),
            FieldCandidate(value="B", source="factuur_plain", confidence=88, context="", label="Factuur"),
        ]
        generic = _generic_invoice(candidates)
        winners = [resolve_field("invoice_number", generic, []).selected_value for _ in range(10)]
        assert len(set(winners)) == 1

    def test_user_pick_is_ranked_candidate_and_wins(self):
        generic = _generic_invoice(
            [FieldCandidate(value="GEN", source="label", confidence=90, context="", label="Factuurnummer")]
        )
        user_pick = FieldCandidate(value="USER", source="USER_PICKED", confidence=100, context="")
        out = resolve_field("invoice_number", generic, [], user_pick=user_pick)
        assert out.selected_value == "USER"
        assert out.override_reason == "user_locked"

    def test_trace_contains_rank_score_and_restricted_reasons(self):
        generic = _generic_invoice(
            [
                FieldCandidate(value="A", source="label", confidence=88, context="", label="Factuurnummer"),
                FieldCandidate(value="B", source="factuur_plain", confidence=88, context="", label="Factuur"),
            ]
        )
        out = resolve_field("invoice_number", generic, [])
        allowed = {
            "higher_confidence",
            "lower_confidence",
            "stronger_label_match",
            "weaker_label",
            "field_keyword_match",
            "weaker_field_type",
            "better_context_proximity",
            "worse_context_proximity",
            "lower_source_priority",
            "deterministic_tiebreak",
            "cross_field_penalty",
            "user_pick_override",
            "pdf_labeled_priority_over_db",
            "db_master_priority_over_pdf",
        }
        entries = [e for e in out.decision_trace if isinstance(e, dict) and e.get("kind") != "final"]
        assert entries
        assert all("rank_score" in e for e in entries)
        for e in entries:
            reason = str(e.get("winner_reason") or e.get("excluded_reason") or "")
            if reason:
                assert reason in allowed

    def test_db_master_iban_beats_conflicting_labeled_pdf(self):
        pdf_iban = "NL20INGB0001234567"
        db_iban = "NL91ABNA0417164300"
        generic = _generic_iban(
            [
                FieldCandidate(
                    value=pdf_iban,
                    source="pdf_text",
                    confidence=88,
                    context="IBAN: NL20INGB0001234567",
                    label="IBAN",
                    meta={"match_type": "label", "label_source": "IBAN"},
                )
            ]
        )
        overrides = [
            FieldCandidate(
                value=db_iban,
                source="db_master",
                confidence=92,
                context="Leveranciers-DB",
            )
        ]
        out = resolve_field("iban", generic, overrides)
        assert out.selected_value == db_iban
        assert out.source == "db_master"
        assert any(
            isinstance(e, dict)
            and (
                e.get("winner_reason") == "db_master_priority_over_pdf"
                or e.get("excluded_reason") == "db_master_priority_over_pdf"
            )
            for e in out.decision_trace
        )
