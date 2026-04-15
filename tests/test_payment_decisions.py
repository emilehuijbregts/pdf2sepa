from __future__ import annotations

from logic.payment_decisions import (
    DECISION_INCLUDED,
    EngineInputSchema,
    REASON_CODE_VERSION,
    REASON_USER_APPROVED,
    build_decision,
    build_engine_snapshot,
    decision_is_exportable,
)


def test_build_decision_has_stable_contract_fields() -> None:
    dec = build_decision(
        status=DECISION_INCLUDED,
        reason_code="included_validated",
        reason_detail=None,
        editable=False,
        requires_rerun=False,
        causal_inputs=["amount", "iban"],
        input_fields={"amount": "100.00", "iban": "NL20INGB0001234567"},
    )
    assert dec["reason_code_version"] == REASON_CODE_VERSION
    assert dec["input_field_fingerprint"]
    assert decision_is_exportable(dec) is True


def test_user_approved_decision_is_exportable() -> None:
    dec = build_decision(
        status=DECISION_INCLUDED,
        reason_code=REASON_USER_APPROVED,
        reason_detail="context_menu_approve",
        editable=False,
        requires_rerun=False,
        causal_inputs=["user_approve"],
        input_fields={"row_id": "r1"},
    )
    assert decision_is_exportable(dec) is True


def test_engine_input_schema_rejects_invalid_rows() -> None:
    snapshot = build_engine_snapshot(
        invoices=[{"row_id": "r1", "supplier_name": "", "iban": "", "amount": "", "invoice_number": ""}],
        supplier_db_hash="db",
        config_hash="cfg",
        runtime_context_hash="ctx",
        engine_version="v1",
    )
    result = EngineInputSchema.validate(snapshot)
    assert result.valid is False
    assert result.errors
