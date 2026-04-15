"""Central payment decision contracts and deterministic helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

DecisionStatus = Literal["included", "needs_review", "excluded"]
DecisionSource = Literal["engine", "user_edit", "rerun"]

DECISION_INCLUDED = "included"
DECISION_NEEDS_REVIEW = "needs_review"
DECISION_EXCLUDED = "excluded"

REASON_MANUAL_PENDING = "manual_edit_pending_engine_validation"
REASON_USER_APPROVED = "user_approved"
REASON_USER_MARKED_ERROR = "user_marked_error"
REASON_LOW_CONFIDENCE = "amount_low_confidence"
REASON_AMBIGUOUS = "amount_ambiguous"
REASON_UNCERTAIN = "amount_uncertain"
REASON_MISSING_IBAN = "missing_iban"
REASON_INVALID_IBAN = "invalid_iban"
REASON_MISSING_AMOUNT = "missing_amount"
REASON_EXPORT_ALLOWED = "included_validated"
REASON_RUNTIME_MISMATCH = "ui_engine_state_mismatch"
REASON_MISSING_DECISION_IN_STORE = "missing_decision_in_store"

REASON_CODE_VERSION = 1


class PaymentDecision(TypedDict):
    status: DecisionStatus
    reason_code: str
    reason_detail: str | None
    editable: bool
    requires_rerun: bool
    reason_code_version: int
    input_field_fingerprint: str
    causal_inputs: list[str]


class EngineInputRow(TypedDict, total=False):
    row_id: str
    supplier_name: str
    iban: str
    amount: str
    invoice_number: str
    customer_code: str
    description: str
    execution_date: str
    invoice_date: str | None
    date_mode: str
    discount: str
    amount_result: dict[str, Any]
    decision_trace: dict[str, Any]
    supplier_match_status: str
    source_file: str


class EngineInputSnapshot(TypedDict):
    invoices: list[EngineInputRow]
    supplier_db_hash: str
    config_hash: str
    runtime_context_hash: str
    engine_version: str
    snapshot_hash: str


def _json_stable(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def stable_hash(data: Any) -> str:
    return hashlib.sha256(_json_stable(data).encode("utf-8")).hexdigest()


def decision_input_fingerprint(input_fields: dict[str, Any]) -> str:
    return stable_hash(input_fields)


def build_decision(
    *,
    status: DecisionStatus,
    reason_code: str,
    reason_detail: str | None,
    editable: bool,
    requires_rerun: bool,
    causal_inputs: list[str],
    input_fields: dict[str, Any],
) -> PaymentDecision:
    return PaymentDecision(
        status=status,
        reason_code=reason_code,
        reason_detail=reason_detail,
        editable=editable,
        requires_rerun=requires_rerun,
        reason_code_version=REASON_CODE_VERSION,
        input_field_fingerprint=decision_input_fingerprint(input_fields),
        causal_inputs=list(dict.fromkeys(causal_inputs)),
    )


def normalize_decision(value: Any) -> PaymentDecision:
    if isinstance(value, dict):
        status = str(value.get("status") or DECISION_NEEDS_REVIEW).strip().lower()
        if status not in (DECISION_INCLUDED, DECISION_NEEDS_REVIEW, DECISION_EXCLUDED):
            status = DECISION_NEEDS_REVIEW
        reason = str(value.get("reason_code") or REASON_LOW_CONFIDENCE).strip() or REASON_LOW_CONFIDENCE
        return build_decision(
            status=status,  # type: ignore[arg-type]
            reason_code=reason,
            reason_detail=(str(value.get("reason_detail")).strip() if value.get("reason_detail") else None),
            editable=bool(value.get("editable", True)),
            requires_rerun=bool(value.get("requires_rerun", status != DECISION_INCLUDED)),
            causal_inputs=[str(x) for x in (value.get("causal_inputs") or []) if str(x).strip()],
            input_fields={"raw": value.get("input_field_fingerprint") or value},
        )
    return build_decision(
        status=DECISION_NEEDS_REVIEW,
        reason_code=REASON_LOW_CONFIDENCE,
        reason_detail="missing decision payload",
        editable=True,
        requires_rerun=True,
        causal_inputs=[],
        input_fields={"raw": "missing"},
    )


def decision_is_exportable(decision: PaymentDecision | dict[str, Any] | None) -> bool:
    if not decision:
        return False
    d = normalize_decision(decision)
    return d["status"] == DECISION_INCLUDED and not d["requires_rerun"]


def decision_status_label_nl(status: str) -> str:
    if status == DECISION_INCLUDED:
        return "Wordt betaald"
    if status == DECISION_NEEDS_REVIEW:
        return "Controle nodig"
    return "Wordt niet betaald"


def decision_reason_text_nl(reason_code: str) -> str:
    reason_map = {
        REASON_EXPORT_ALLOWED: "Gevalideerd door engine.",
        REASON_MANUAL_PENDING: "Wijziging nog niet door engine gevalideerd.",
        REASON_USER_APPROVED: "Handmatig goedgekeurd.",
        REASON_USER_MARKED_ERROR: "Handmatig gemarkeerd als fout (niet exporteren).",
        REASON_LOW_CONFIDENCE: "Bedrag of match heeft lage betrouwbaarheid.",
        REASON_AMBIGUOUS: "Meerdere mogelijke bedragen gevonden.",
        REASON_UNCERTAIN: "Onvoldoende zekerheid over factuurgegevens.",
        REASON_MISSING_IBAN: "IBAN ontbreekt.",
        REASON_INVALID_IBAN: "IBAN is ongeldig.",
        REASON_MISSING_AMOUNT: "Bedrag ontbreekt of is ongeldig.",
        REASON_RUNTIME_MISMATCH: "UI en engine-state zijn niet consistent.",
        REASON_MISSING_DECISION_IN_STORE: "Geen engine decision beschikbaar (nog niet berekend).",
    }
    return reason_map.get(reason_code, reason_code)


def decision_fix_hint_nl(reason_code: str) -> str:
    hint_map = {
        REASON_MANUAL_PENDING: "Sla op of bevestig de wijziging om opnieuw te valideren.",
        REASON_USER_APPROVED: "Deze rij is door jou goedgekeurd en wordt meegenomen in de export.",
        REASON_USER_MARKED_ERROR: "Herstel de rij om opnieuw te beoordelen of te exporteren.",
        REASON_MISSING_IBAN: "Vul een geldig IBAN in om betaling te activeren.",
        REASON_INVALID_IBAN: "Corrigeer het IBAN-formaat en valideer opnieuw.",
        REASON_AMBIGUOUS: "Kies handmatig het juiste bedrag en herbereken.",
        REASON_UNCERTAIN: "Controleer factuur en voer bedrag handmatig in.",
        REASON_LOW_CONFIDENCE: "Controleer leverancier en bedrag, daarna opnieuw valideren.",
    }
    return hint_map.get(reason_code, "")


def canonical_payment_sort_key(payment: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(payment.get("invoice_number") or "").strip().lower(),
        str(payment.get("supplier_name") or "").strip().lower(),
        str(payment.get("amount") or "").strip(),
        str(payment.get("row_id") or payment.get("_source_file") or "").strip().lower(),
    )


def canonicalize_payments(payments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(payments, key=canonical_payment_sort_key)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_engine_snapshot(
    *,
    invoices: list[EngineInputRow],
    supplier_db_hash: str,
    config_hash: str,
    runtime_context_hash: str,
    engine_version: str,
) -> EngineInputSnapshot:
    body = {
        "invoices": invoices,
        "supplier_db_hash": supplier_db_hash,
        "config_hash": config_hash,
        "runtime_context_hash": runtime_context_hash,
        "engine_version": engine_version,
    }
    snap_hash = stable_hash(body)
    return EngineInputSnapshot(
        invoices=invoices,
        supplier_db_hash=supplier_db_hash,
        config_hash=config_hash,
        runtime_context_hash=runtime_context_hash,
        engine_version=engine_version,
        snapshot_hash=snap_hash,
    )


@dataclass(frozen=True)
class SchemaValidationResult:
    valid: bool
    errors: list[str]


class EngineInputSchema:
    @staticmethod
    def validate(snapshot: EngineInputSnapshot | dict[str, Any]) -> SchemaValidationResult:
        errs: list[str] = []
        invoices = snapshot.get("invoices") if isinstance(snapshot, dict) else None
        if not isinstance(invoices, list):
            errs.append("invoices must be a list")
        else:
            for idx, row in enumerate(invoices):
                if not isinstance(row, dict):
                    errs.append(f"invoices[{idx}] must be object")
                    continue
                for key in ("supplier_name", "iban", "amount", "invoice_number", "row_id"):
                    if not str(row.get(key) or "").strip():
                        errs.append(f"invoices[{idx}].{key} is required")
                dm = str(row.get("date_mode") or "direct").strip().lower()
                if dm not in ("direct", "due", "manual"):
                    errs.append(f"invoices[{idx}].date_mode invalid")
        for hash_key in ("supplier_db_hash", "config_hash", "runtime_context_hash", "engine_version"):
            if not str(snapshot.get(hash_key) or "").strip():
                errs.append(f"{hash_key} is required")
        return SchemaValidationResult(valid=not errs, errors=errs)
