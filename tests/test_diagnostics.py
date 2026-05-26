"""Tests for logic.diagnostics (read-only mapper)."""

from __future__ import annotations

from logic.diagnostics import (
    build_diagnostics,
    build_invoice_diagnostics_snapshot,
)


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
            "invoice_number",
            "customer_number",
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
    assert inv_diag["status_nl"] == "Via extractieprofiel"
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
    assert diag["supplier"]["matched_by"] == ["IBAN", "KvK"]
