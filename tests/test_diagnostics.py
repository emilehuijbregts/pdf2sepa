"""Tests for logic.diagnostics (read-only mapper)."""

from __future__ import annotations

from ui.i18n import tr
from logic.diagnostics import (
    build_diagnostics,
    build_invoice_diagnostics_snapshot,
    overlay_amount_result,
    overlay_field_result,
    overlay_iban_result,
)
from logic.field_diagnostics import build_iban_diag_block
from logic.field_diagnostics import map_field_candidate_for_diag


def _base_invoice(**overrides: object) -> dict:
    inv = {
        "source_file": "/tmp/Factuur-123.pdf",
        "supplier_name": "Acme BV",
        "supplier_hint": "Acme",
        "match_status": "confirmed",
        "supplier_match_source": "db_match",
        "match_info": {"iban_match": True, "customer_code_match": True},
        "db_core_matches": ["IBAN", "Klantnummer"],
        "db_core_match_count": 2,
        "iban": "NL20INGB0001234567",
        "all_ibans": ["NL20INGB0001234567"],
        "iban_mismatch": False,
        "invoice_number": "F-001",
        "customer_number": "K42",
        "invoice_date_source": "parsed",
        "type": "invoice",
        "amount_result": {
            "status": "confirmed",
            "source": "total_label_payable",
            "value": "100.00",
            "confidence": 95,
            "candidates": [],
        },
        "iban_result": {
            "status": "confirmed",
            "value": "NL20INGB0001234567",
            "confidence": 88,
            "source": "pdf_text",
            "candidates": [
                {
                    "value": "NL20INGB0001234567",
                    "source": "pdf_text",
                    "confidence": 88,
                    "context": "IBAN",
                }
            ],
        },
        "raw_text": "SHOULD NOT APPEAR IN SNAPSHOT",
    }
    inv.update(overrides)
    return inv


def test_snapshot_whitelist_and_deepcopy_amount_result() -> None:
    inv = _base_invoice()
    snap = build_invoice_diagnostics_snapshot(inv)
    assert "raw_text" not in snap
    assert set(snap.keys()).issubset(
        {
            "source_file",
            "load_error",
            "supplier_name",
            "supplier_key",
            "supplier_hint",
            "match_status",
            "supplier_match_source",
            "match_info",
            "db_core_matches",
            "db_core_match_count",
            "match_signals",
            "iban",
            "all_ibans",
            "iban_mismatch",
            "ocr_iban_attempted",
            "ocr_iban_error",
            "amount_result",
            "iban_result",
            "invoice_number_result",
            "customer_number_result",
            "vat_number_result",
            "kvk_number_result",
            "invoice_date_result",
            "email_domain_result",
            "invoice_number",
            "customer_number",
            "vat_number",
            "kvk_number",
            "invoice_date",
            "email_domain",
            "invoice_date_source",
            "type",
            "extraction_source",
            "profile_fields",
            "pdf_customer_number",
        }
    )
    snap["amount_result"]["status"] = "mutated"
    assert inv["amount_result"]["status"] == "confirmed"


def test_ok_path_overall_status() -> None:
    snap = build_invoice_diagnostics_snapshot(_base_invoice())
    diag = build_diagnostics(
        snap,
        payment={
            "decision": {
                "status": "included",
                "reason_code": "included_validated",
            }
        },
    )
    assert diag["overall_status"] == "ok"
    assert diag["amount"]["needs_attention"] is False
    assert diag["supplier"]["needs_attention"] is False
    assert diag["action_suggestions"] == []


def test_amount_ambiguous_suggestion() -> None:
    inv = _base_invoice(
        amount_result={
            "status": "ambiguous",
            "source": "INCL_CONFLICT",
            "value": None,
            "confidence": 0,
            "candidates": [
                {
                    "value": "10.00",
                    "source": "total_label_payable",
                    "confidence": 80,
                    "context": "x" * 100,
                    "type": "incl",
                }
            ],
        }
    )
    snap = build_invoice_diagnostics_snapshot(inv)
    diag = build_diagnostics(snap)
    assert diag["amount"]["needs_attention"] is True
    assert diag["overall_status"] == "needs_review"
    assert any("bedragcel" in s.lower() for s in diag["action_suggestions"])
    preview = diag["amount"]["candidates"][0]["context_preview"]
    assert preview is not None
    assert len(preview) <= 81
    assert preview.endswith("…")
    assert diag["amount"]["detail_nl"]


def test_overlay_field_result_invoice_number_live_wins() -> None:
    inv = _base_invoice(
        invoice_number="OLD",
        invoice_number_result={
            "status": "ambiguous",
            "value": "OLD",
            "confidence": 0,
            "source": "label",
            "candidates": [
                {"value": "OLD", "source": "a", "confidence": 80, "context": "x"},
                {"value": "NEW", "source": "b", "confidence": 85, "context": "y"},
            ],
        },
    )
    snap = build_invoice_diagnostics_snapshot(inv)
    live = {
        "status": "confirmed",
        "user_selected": True,
        "value": "NEW",
        "confidence": 95,
        "source": "USER_PICKED",
        "candidates": [],
    }
    merged = overlay_field_result(snap, "invoice_number", live)
    assert merged["invoice_number"] == "NEW"
    assert merged["invoice_number_result"]["status"] == "confirmed"
    diag = build_diagnostics(merged)
    assert diag["invoice_number"]["value"] == "NEW"
    assert snap["invoice_number"] == "OLD"


def test_overlay_field_result_clears_stale_customer_scalar_on_absent() -> None:
    inv = _base_invoice(
        customer_number="STALE-CODE",
        customer_number_result={
            "value": "K42",
            "status": "confirmed",
            "source": "label",
            "confidence": 90,
            "candidates": [],
        },
    )
    snap = build_invoice_diagnostics_snapshot(inv)
    absent_live = {
        "value": None,
        "selected_value": None,
        "absence_state": "NOT_PRESENT_SUPPLIER_LEVEL",
        "source": "USER_ABSENT_CUSTOMER",
        "status": "confirmed",
        "user_selected": True,
        "user_overridden": True,
        "candidates": [],
        "resolver_finalized": True,
    }
    merged = overlay_field_result(snap, "customer_number", absent_live)
    assert "customer_number" not in merged
    assert merged["customer_number_result"]["source"] == "USER_ABSENT_CUSTOMER"
    diag = build_diagnostics(merged)
    assert diag["customer_number"]["value_display"] == "diag.ident.status.no_customer_number"
    assert tr(diag["customer_number"]["value_display"]) == "Geen klantnummer"


def test_build_ident_field_diag_block_absent_ignores_stale_scalar() -> None:
    from logic.field_diagnostics import build_ident_field_diag_block
    from ui.field_review import CUSTOMER_ABSENT_PICK_SOURCE

    block = build_ident_field_diag_block(
        {
            "customer_number": "WRONG",
            "customer_number_result": {
                "value": None,
                "selected_value": None,
                "absence_state": "NOT_PRESENT_SUPPLIER_LEVEL",
                "source": CUSTOMER_ABSENT_PICK_SOURCE,
                "status": "confirmed",
                "user_selected": True,
                "candidates": [{"value": "WRONG", "source": "label", "confidence": 90}],
            },
        },
        "customer_number",
    )
    assert block["value_display"] == "diag.ident.status.no_customer_number"
    assert tr(block["value_display"]) == "Geen klantnummer"
    assert block["candidates"] == []
    assert "WRONG" not in str(block.get("value") or "")


def test_overlay_amount_result_replaces_stale_parser_snapshot() -> None:
    """Live amount_result from UI cell must win over batch diagnostics snapshot."""
    inv = _base_invoice(
        amount_result={
            "status": "ambiguous",
            "source": "INCL_CONFLICT",
            "value": None,
            "confidence": 0,
            "candidates": [{"value": "10.00", "source": "total_label_payable", "confidence": 80}],
        }
    )
    snap = build_invoice_diagnostics_snapshot(inv)
    live = {
        "status": "confirmed",
        "amount_status": "confirmed",
        "user_selected": True,
        "value": "200.00",
        "selected_amount": "200.00",
        "confidence": 100,
        "source": "TOTAL_LABEL_PAYABLE",
        "candidates": [{"value": "200.00", "source": "total_label_payable", "confidence": 80}],
    }
    merged = overlay_amount_result(snap, live)
    diag = build_diagnostics(merged)
    assert diag["amount"]["status"] == "confirmed"
    assert diag["amount"]["value"] == "200.00"
    assert diag["amount"]["needs_attention"] is False
    assert snap["amount_result"]["status"] == "ambiguous"


def test_overlay_iban_result_replaces_stale_snapshot() -> None:
    inv = _base_invoice(
        iban="NL20INGB0001234567",
        iban_result={
            "status": "ambiguous",
            "value": None,
            "confidence": 0,
            "source": "AMBIGUOUS",
            "candidates": [
                {"value": "NL20INGB0001234567", "source": "pdf_text", "confidence": 88, "context": "a"},
                {"value": "NL91ABNA0417164300", "source": "pdf_text", "confidence": 72, "context": "b"},
            ],
        },
    )
    snap = build_invoice_diagnostics_snapshot(inv)
    live = {
        "status": "confirmed",
        "user_selected": True,
        "value": "NL20INGB0001234567",
        "confidence": 95,
        "source": "USER_PICKED",
        "candidates": [],
    }
    merged = overlay_iban_result(snap, live)
    assert merged["iban"] == "NL20INGB0001234567"
    diag = build_diagnostics(merged)
    assert diag["iban"]["value"] == "NL20INGB0001234567"
    assert diag["iban"]["masked_value"] == "NL…4567"


def test_build_iban_diag_block_masks_candidates() -> None:
    snap = {
        "iban": "NL20INGB0001234567",
        "iban_result": {
            "status": "confirmed",
            "value": "NL20INGB0001234567",
            "confidence": 88,
            "source": "pdf_text",
            "candidates": [
                {"value": "NL20INGB0001234567", "source": "pdf_text", "confidence": 88, "context": "IBAN"},
            ],
        },
    }
    block = build_iban_diag_block(snap)
    assert block["value"] == "NL20INGB0001234567"
    assert block["candidates"][0]["value_display"] == "NL…4567"


def test_hybrid_override_trace_in_amount_diag() -> None:
    snap = _base_invoice(
        amount_result={
            "status": "confirmed",
            "value": "100.00",
            "confidence": 90,
            "source": "total_label_payable",
            "override_reason": "generic_strong",
            "decision_trace": [
                {
                    "source": "total_label_payable",
                    "confidence": 90,
                    "considered": True,
                    "win": True,
                    "winner_reason": "higher_confidence",
                    "rank_score": [90, 300, 100, "total_label_payable", "100.00"],
                },
                {
                    "source": "profile",
                    "confidence": 80,
                    "considered": True,
                    "win": False,
                    "excluded_reason": "lower_confidence",
                    "rank_score": [80, 100, 50, "profile", "100.00"],
                },
            ],
            "candidates": [],
        },
    )
    diag = build_diagnostics(snap)
    amount = diag["amount"]
    assert amount.get("override_reason") == "generic_strong"
    assert amount.get("override_reason_nl")
    assert len(amount.get("decision_trace") or []) == 2
    assert len(amount.get("decision_trace_human") or []) == 2
    trace_human = amount.get("decision_trace_human") or []
    assert trace_human[1].get("rejection_reason_nl")
    assert trace_human[1].get("excluded_reason_nl")
    assert trace_human[0].get("winner_reason_nl")


def test_diagnostics_can_render_final_trace_entry() -> None:
    snap = _base_invoice(
        amount_result={
            "status": "confirmed",
            "value": "100.00",
            "confidence": 90,
            "source": "total_label_payable",
            "override_reason": "generic_only",
            "decision_trace": [
                {
                    "value": "100.00",
                    "source": "total_label_payable",
                    "confidence": 90,
                    "considered": True,
                    "win": True,
                    "rank": 1,
                    "winner_reason": "higher_confidence",
                },
                {
                    "kind": "final",
                    "final_decision_reason": "highest_confidence",
                    "winner": {
                        "value": "100.00",
                        "source": "total_label_payable",
                        "confidence": 90,
                        "winner_reason": "higher_confidence",
                    },
                },
            ],
            "candidates": [],
        },
    )
    diag = build_diagnostics(snap)
    assert diag["amount"].get("decision_trace")
    assert any(
        isinstance(e, dict) and e.get("kind") == "final"
        for e in (diag["amount"].get("decision_trace") or [])
    )
    trace_human = diag["amount"].get("decision_trace_human") or []
    assert any(
        isinstance(e, dict)
        and e.get("kind") == "final"
        and str(e.get("final_decision_reason_nl") or "").strip()
        for e in trace_human
    )
    final = next(e for e in trace_human if isinstance(e, dict) and e.get("kind") == "final")
    assert isinstance(final.get("winner"), dict)
    assert final["winner"].get("winner_reason_nl")


def test_load_failed_error_status() -> None:
    inv = _base_invoice(
        load_error="read_failed",
        match_status="load_failed",
        amount_result={"status": "failed", "source": "LOAD_FAILED", "value": None, "confidence": 0, "candidates": []},
    )
    snap = build_invoice_diagnostics_snapshot(inv)
    diag = build_diagnostics(snap)
    assert diag["overall_status"] == "error"
    assert diag["general"]["load_error_nl"]
    assert diag["supplier"]["needs_attention"] is True


def test_iban_masking_and_mismatch_warning() -> None:
    snap = build_invoice_diagnostics_snapshot(
        _base_invoice(iban_mismatch=True)
    )
    diag = build_diagnostics(
        snap,
        payment={"warning": "iban_mismatch_supplier"},
    )
    assert diag["iban"]["masked_value"] == "NL…4567"
    assert diag["iban"]["needs_attention"] is True
    assert diag["iban"]["warnings_nl"]
    assert any("leveranciersdatabase" in s.lower() for s in diag["action_suggestions"])


def test_warnings_pipe_split_amount_and_iban() -> None:
    snap = build_invoice_diagnostics_snapshot(_base_invoice())
    diag = build_diagnostics(
        snap,
        payment={"warning": "iban_mismatch_supplier|amount_tentative"},
    )
    assert len(diag["iban"]["warnings_nl"]) == 1
    assert len(diag["amount"]["warnings_nl"]) == 1


def test_supplier_status_fallback_from_trace() -> None:
    inv = _base_invoice()
    del inv["match_status"]
    snap = build_invoice_diagnostics_snapshot(inv)
    diag = build_diagnostics(
        snap,
        payment={"decision_trace": {"supplier_match_status": "needs_review"}},
    )
    assert diag["supplier"]["status"] == "needs_review"
    assert diag["supplier"]["needs_attention"] is True


def test_customer_number_empty_needs_attention() -> None:
    snap = build_invoice_diagnostics_snapshot(_base_invoice(customer_number=""))
    diag = build_diagnostics(snap)
    assert diag["customer_number"]["needs_attention"] is True
    assert any("klantnummer" in s.lower() for s in diag["action_suggestions"])


def test_blocking_excluded_reason_error() -> None:
    snap = build_invoice_diagnostics_snapshot(_base_invoice())
    diag = build_diagnostics(
        snap,
        decision={"status": "excluded", "reason_code": "missing_iban"},
    )
    assert diag["overall_status"] == "error"


def test_non_blocking_excluded_needs_review() -> None:
    snap = build_invoice_diagnostics_snapshot(_base_invoice())
    diag = build_diagnostics(
        snap,
        decision={"status": "excluded", "reason_code": "user_marked_error"},
    )
    assert diag["overall_status"] == "ok"


def test_invoice_number_from_payment_fallback() -> None:
    snap = build_invoice_diagnostics_snapshot(_base_invoice(invoice_number=""))
    diag = build_diagnostics(snap, payment={"invoice_number": "PAY-99"})
    assert diag["invoice_number"]["value"] == "PAY-99"


def test_invoice_number_diagnostics_prefers_profile_value_over_stale_candidates() -> None:
    """Tabel/profiel-waarde wint op oude parser-kandidaten in snapshot."""
    inv = _base_invoice(
        invoice_number="8035714",
        extraction_source="profile",
        profile_fields=["invoice_number"],
        invoice_number_result={
            "status": "ambiguous",
            "value": "35714",
            "candidates": [
                {
                    "value": "35714",
                    "source": "label",
                    "confidence": 88,
                    "context": "Polisnummer : 8 0 35714",
                    "label": "Polisnummer",
                },
                {
                    "value": "Notadatum",
                    "source": "label",
                    "confidence": 85,
                    "context": "Polisnummer : Notadatum",
                    "label": "Polisnummer",
                },
            ],
        },
    )
    snap = build_invoice_diagnostics_snapshot(inv)
    diag = build_diagnostics(snap)
    inv_diag = diag["invoice_number"]
    assert inv_diag["value"] == "8035714"
    assert inv_diag["status_nl"] == "diag.ident.status.via_profile"
    assert tr(inv_diag["status_nl"]) == "Via extractieprofiel"
    assert len(inv_diag["candidates"]) == 1
    assert inv_diag["candidates"][0]["value"] == "8035714"
    assert inv_diag["candidates"][0].get("is_resolved") is True


def test_build_diagnostics_without_resolved_payment_amount() -> None:
    """Diagnostics mag openen als UI-bedragcel leeg is (ambiguous parser, geen exportbedrag)."""
    inv = _base_invoice(
        amount_result={
            "status": "ambiguous",
            "source": "INCL_CONFLICT",
            "value": None,
            "confidence": 0,
            "candidates": [
                {
                    "value": "1287.29",
                    "source": "total_label_payable",
                    "confidence": 80,
                    "context": "Totaal te betalen",
                    "type": "incl",
                }
            ],
        }
    )
    snap = build_invoice_diagnostics_snapshot(inv)
    diag = build_diagnostics(
        snap,
        payment={"amount": None, "amount_result": inv["amount_result"]},
        decision={"status": "needs_review", "reason_code": "unmatched_supplier"},
    )
    assert diag["amount"]["status"] == "ambiguous"
    assert len(diag["amount"]["candidates"]) == 1
    assert diag["amount"]["value_display"] is None


def test_profile_learning_suggestion_when_eligible(tmp_path) -> None:
    pdf = tmp_path / "Factuur-123.pdf"
    pdf.write_text("factuur", encoding="utf-8")
    inv = _base_invoice(
        source_file=str(pdf),
        extraction_source="generic",
        match_status="confirmed",
    )
    snap = build_invoice_diagnostics_snapshot(inv)
    diag = build_diagnostics(snap, payment={"_source_file": str(pdf)})
    assert any(
        "extractieprofiel" in s.lower() for s in diag["action_suggestions"]
    )


def test_snapshot_includes_extraction_profile_fields() -> None:
    inv = _base_invoice(
        extraction_source="profile",
        profile_fields=["amount"],
        pdf_customer_number="PDF-99",
    )
    snap = build_invoice_diagnostics_snapshot(inv)
    assert snap["extraction_source"] == "profile"
    assert snap["profile_fields"] == ["amount"]
    assert snap["pdf_customer_number"] == "PDF-99"


def test_matched_by_from_match_info_when_no_db_core() -> None:
    inv = _base_invoice()
    inv.pop("db_core_matches", None)
    inv["match_info"] = {"iban_match": True, "kvk_match": True}
    snap = build_invoice_diagnostics_snapshot(inv)
    diag = build_diagnostics(snap)
    assert diag["supplier"]["matched_by"] == ["iban", "kvk"]


def test_map_field_candidate_for_diag_includes_explainability_fields() -> None:
    mapped = map_field_candidate_for_diag(
        {
            "value": "26FC000498",
            "source": "label_block_same_line",
            "confidence": 92,
            "context": "Factuurnummer: 26FC000498",
            "label": "Factuurnummer",
            "extraction_method": "label_match",
            "label_source": "Factuurnummer",
            "match_type": "label",
            "label_reason": "Gevonden direct na label",
            "context_hint": "header",
            "score_breakdown": {"base": 90, "label_bonus": 8},
            "raw_detected": "26FC 000498",
            "normalized_iso": "26FC000498",
            "parse_path": "line_parser",
        },
        field_id="invoice_number",
    )
    assert mapped["source_nl"] == "field.source_label.label_block_same_line"
    assert tr(mapped["source_nl"]) == "Waarde op dezelfde regel als label"
    assert mapped["extraction_method_nl"] == "field.extraction_method.label_match"
    assert tr(mapped["extraction_method_nl"]) == "Gevonden naast herkenbaar label"
    assert mapped["label_source"] == "Factuurnummer"
    assert mapped["match_type"] == "label"
    assert mapped["context_hint_nl"] == "field.context_hint.header"
    assert tr(mapped["context_hint_nl"]) == "header van document"
    assert mapped["score_breakdown_nl"]
    assert mapped["score_breakdown_nl"][0].startswith("field.score_label.")
    assert mapped["raw_detected"] == "26FC 000498"
    assert mapped["normalized_iso"] == "26FC000498"


def test_build_diagnostics_includes_document_type() -> None:
    snap = build_invoice_diagnostics_snapshot(_base_invoice(type="credit_note"))
    diag = build_diagnostics(snap)
    assert diag["general"]["document_type"] == "credit_note"


def test_build_diagnostics_allows_document_type_buttons_with_supplier_key() -> None:
    snap = build_invoice_diagnostics_snapshot(
        _base_invoice(type="credit_note", supplier_key="acme-bv")
    )
    diag = build_diagnostics(snap)
    assert diag["general"]["can_set_document_type"] is True


def test_build_diagnostics_allows_document_type_buttons_with_supplier_name_fallback() -> None:
    inv = _base_invoice(type="invoice")
    snap = build_invoice_diagnostics_snapshot(inv)
    diag = build_diagnostics(snap)
    assert diag["general"]["can_set_document_type"] is True


def test_build_diagnostics_blocks_document_type_buttons_without_supplier_context() -> None:
    snap = build_invoice_diagnostics_snapshot(
        _base_invoice(type="invoice", supplier_name="", supplier_hint="", match_status="confirmed")
    )
    diag = build_diagnostics(snap)
    assert diag["general"]["can_set_document_type"] is False
