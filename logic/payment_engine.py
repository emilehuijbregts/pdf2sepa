# Bouwt betaalopdrachten op basis van geparste factuurdata en leveranciersregels.
"""Verwerkt verrijkte factuurdicts naar betalingen en fouten.

Statuses ``matched``, ``new``, ``confirmed``, ``reviewed`` worden verder verwerkt.
``load_failed`` (met ``load_error`` in het factuurdict) wordt als PDF-fout gerapporteerd.

Geen mutatie van invoerdicts.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Literal

from logic.engine_result import EngineResult
from logic.batch_trace import log_batch_stage, log_batch_summary
from logic.payment_decisions import (
    DECISION_EXCLUDED,
    DECISION_INCLUDED,
    DECISION_NEEDS_REVIEW,
    REASON_AMBIGUOUS,
    REASON_CREDIT_REFUND_REQUIRED,
    REASON_EXPORT_ALLOWED,
    REASON_INVALID_IBAN,
    REASON_LOW_CONFIDENCE,
    REASON_MANUAL_PENDING,
    REASON_MISSING_AMOUNT,
    REASON_MISSING_IBAN,
    REASON_UNCERTAIN,
    build_decision,
    canonicalize_payments,
)
from logic.payment_engine_assembly import InvoiceDraft, build_groups_from_drafts
from logic.settlement_payments import sort_settlement_groups
from logic.credit_matching import (
    CreditMatchResult,
    build_engine_credit_allocations,
    build_engine_credit_links,
    match_credits_in_batch,
)
from logic.credit_override_apply import apply_credit_overrides
from logic.credit_override_store import OverrideSession
from logic.credit_settlement import (
    SETTLEMENT_MANUAL_REVIEW,
    SETTLEMENT_REFUND_REQUIRED,
    compute_settlement_groups,
    document_id,
    settlement_for_invoice,
    settlement_group_to_dict,
)
from logic.payment_amounts import (
    format_eur_xml,
    incl_amount_to_excl_for_discount,
    normalize_supplier_vat_rate_pct,
)
from logic.validation import clean_iban, is_plausible_iban

_clean_iban = clean_iban
_is_plausible_iban = is_plausible_iban
_MONEY_QUANT = Decimal("0.01")
ENGINE_VERSION = "decision-model-v1"

# Standardized decision trace reason codes.
TRACE_REASON_AMOUNT_SELECTED_SINGLE_CANDIDATE = "amount_selected_single_candidate"
TRACE_REASON_AMOUNT_SELECTED_LABEL_PRIORITY = "amount_selected_label_priority"
TRACE_REASON_AMOUNT_BLOCKED_AMBIGUOUS_UPSTREAM = "amount_blocked_ambiguous_upstream"
TRACE_REASON_CREDIT_NOTE_MATCHED = "credit_note_matched"
TRACE_REASON_CREDIT_MATCH_REVIEW = "credit_match_needs_review"
TRACE_REASON_CREDIT_REFUND_REQUIRED = "credit_refund_required"
TRACE_REASON_DISCOUNT_APPLIED_TRUSTED_SUPPLIER = "discount_applied_trusted_supplier"
TRACE_REASON_VAT_INFERRED_FROM_RATE = "vat_inferred_from_rate"
TRACE_REASON_AMOUNT_SELECTED_AMOUNT_RESULT = "amount_selected_amount_result"
TRACE_REASON_AMOUNT_SELECTED_INVOICE_FIELD = "amount_selected_invoice_field"
TRACE_REASON_AMOUNT_BLOCKED_FAILED_UPSTREAM = "amount_blocked_failed_upstream"

# region agent log
def _agent_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    try:
        import json, time  # noqa: E401

        payload = {
            "sessionId": "9a8545",
            "runId": "pre-fix",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(
            "/Users/eh/Documents/Cursor/PDF2SEPA/.cursor/debug-9a8545.log",
            "a",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
# endregion


def _effective_amount_status(inv_raw: dict) -> tuple[str, dict]:
    """Read stored amount field status for payment decisions (no mutation or re-promotion)."""
    inv_amt_result = inv_raw.get("amount_result") or {}
    if not isinstance(inv_amt_result, dict):
        inv_amt_result = {}
    st = str(inv_amt_result.get("status") or inv_amt_result.get("amount_status") or "").strip().lower()
    return st, inv_amt_result


def _to_decimal_money(value: object, *, field: str) -> Decimal:
    """Strict money coercion for engine boundary: only parse, never guess."""
    if isinstance(value, bool):
        raise ValueError(f"{field}: bool is not a valid money type")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, float):
        # Legacy compatibility at boundary only; core remains Decimal-only.
        dec = Decimal(str(value))
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            raise ValueError(f"{field}: empty string")
        try:
            dec = Decimal(s.replace(",", "."))
        except InvalidOperation as exc:
            raise ValueError(f"{field}: invalid decimal string") from exc
    else:
        raise ValueError(f"{field}: unsupported type {type(value).__name__}")
    try:
        return dec.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field}: invalid decimal value") from exc


def _amount_value_from_invoice(inv: dict) -> object | None:
    amt_result = inv.get("amount_result")
    if isinstance(amt_result, dict):
        if amt_result.get("user_selected"):
            for key in ("value", "selected_amount"):
                val = amt_result.get(key)
                if val is not None and str(val).strip():
                    return val
            return None
        val = amt_result.get("value")
        if val is not None:
            return val
    return inv.get("amount")


def _trace_amount_source_chain(inv: dict) -> list[str]:
    chain: list[str] = []
    amt_result = inv.get("amount_result")
    if isinstance(amt_result, dict):
        if amt_result.get("value") is not None:
            source = str(amt_result.get("source") or "").strip()
            if source:
                chain.append(f"amount_result.value:{source}")
            else:
                chain.append("amount_result.value")
        if amt_result.get("candidates"):
            chain.append("amount_result.candidates")
    if inv.get("amount") is not None:
        chain.append("invoice.amount")
    return chain


def _trace_amount_decision_reason(inv: dict) -> str:
    amt_result = inv.get("amount_result")
    if not isinstance(amt_result, dict):
        return TRACE_REASON_AMOUNT_SELECTED_INVOICE_FIELD

    status = str(amt_result.get("status") or amt_result.get("amount_status") or "").strip().lower()
    if status == "ambiguous":
        return TRACE_REASON_AMOUNT_BLOCKED_AMBIGUOUS_UPSTREAM
    if status == "failed":
        return TRACE_REASON_AMOUNT_BLOCKED_FAILED_UPSTREAM
    if status == "tentative":
        return TRACE_REASON_AMOUNT_SELECTED_AMOUNT_RESULT
    if amt_result.get("user_selected"):
        return TRACE_REASON_AMOUNT_SELECTED_AMOUNT_RESULT

    candidates = amt_result.get("candidates")
    if isinstance(candidates, list) and len(candidates) == 1:
        return TRACE_REASON_AMOUNT_SELECTED_SINGLE_CANDIDATE

    source = str(amt_result.get("source") or "").strip().upper()
    if source.startswith("TOTAL_LABEL"):
        return TRACE_REASON_AMOUNT_SELECTED_LABEL_PRIORITY

    if amt_result.get("value") is not None:
        return TRACE_REASON_AMOUNT_SELECTED_AMOUNT_RESULT
    return TRACE_REASON_AMOUNT_SELECTED_INVOICE_FIELD


def _trace_compact_amount_result(inv: dict) -> dict:
    amt_result = inv.get("amount_result")
    if not isinstance(amt_result, dict):
        return {}
    candidates = amt_result.get("candidates")
    cand_count = len(candidates) if isinstance(candidates, list) else 0
    return {
        "status": str(amt_result.get("status") or amt_result.get("amount_status") or ""),
        "source": str(amt_result.get("source") or ""),
        "value": str(amt_result.get("value")) if amt_result.get("value") is not None else None,
        "confidence": amt_result.get("confidence"),
        "candidate_count": cand_count,
    }


def _serialize_credit_match_result(result: CreditMatchResult) -> dict:
    return {
        "match_method": result.match_method,
        "confidence": result.confidence,
        "warnings": list(result.warnings),
        "remaining_credit": str(result.remaining_credit.quantize(_MONEY_QUANT)),
        "credit_invoice_number": str(result.credit_invoice.get("invoice_number") or ""),
        "allocation": [
            {
                "invoice_id": a.invoice_id,
                "invoice_number": a.invoice_number,
                "amount_applied": str(a.amount_applied.quantize(_MONEY_QUANT)),
            }
            for a in result.allocation
        ],
    }


def _credit_match_details_for_credits(
    creds: list[dict],
    match_results: list[CreditMatchResult],
) -> list[dict]:
    out: list[dict] = []
    for c in creds:
        raw = c.get("raw") or c
        for result in match_results:
            if result.credit_invoice is raw:
                out.append(_serialize_credit_match_result(result))
                break
    return out


def _build_payment_decision_trace(
    *,
    inv_raw: dict,
    creds: list[dict],
    warn_parts: list[str],
    discount_pct: Decimal,
    discount_amount: Decimal,
    final_amount: Decimal,
    amount_before_discount: Decimal,
    vat_inference_used: bool,
    discount_skipped_reason: str | None,
    discount_base_excl: Decimal | None,
    credit_total_incl: Decimal,
    credit_total_excl: Decimal | None,
    vat_source: str | None = None,
    supplier_vat_rate_used: int | None = None,
    credit_match_details: list[dict] | None = None,
    settlement_snapshot: dict | None = None,
) -> dict:
    reason_codes: list[str] = [_trace_amount_decision_reason(inv_raw)]
    if creds:
        reason_codes.append(TRACE_REASON_CREDIT_NOTE_MATCHED)
    if credit_match_details and any(
        d.get("match_method") == "manual_review" or d.get("warnings") for d in credit_match_details
    ):
        reason_codes.append(TRACE_REASON_CREDIT_MATCH_REVIEW)
    if discount_pct > Decimal("0.00") and discount_amount > Decimal("0.00"):
        reason_codes.append(TRACE_REASON_DISCOUNT_APPLIED_TRUSTED_SUPPLIER)
    if vat_inference_used:
        reason_codes.append(TRACE_REASON_VAT_INFERRED_FROM_RATE)

    credit_numbers = [
        str(c["raw"].get("invoice_number") or "")
        for c in creds
        if str(c["raw"].get("invoice_number") or "").strip()
    ]

    return {
        "reason_codes": reason_codes,
        "steps": [
            "normalize_input",
            "evaluate_amount",
            "evaluate_credits_discount",
            "evaluate_iban",
            "finalize_decision",
        ],
        "reason_chain": list(reason_codes),
        "final_decision_source": "engine",
        "amount_source_chain": _trace_amount_source_chain(inv_raw),
        "amount_decision_reason": _trace_amount_decision_reason(inv_raw),
        "supplier_match_status": str(inv_raw.get("match_status") or ""),
        "credit_applied": {
            "used": bool(creds),
            "details": {
                "count": len(creds),
                "invoice_numbers": credit_numbers,
                "total_incl": str(credit_total_incl.quantize(_MONEY_QUANT)),
                "total_excl": str(credit_total_excl.quantize(_MONEY_QUANT))
                if isinstance(credit_total_excl, Decimal)
                else None,
            },
        },
        "discount_applied": {
            "used": bool(discount_pct > Decimal("0.00") and discount_amount > Decimal("0.00")),
            "percentage": str(discount_pct.quantize(_MONEY_QUANT)),
            "source": "invoice.discount",
            "amount": str(discount_amount.quantize(_MONEY_QUANT)),
            "skipped_reason": discount_skipped_reason,
        },
        "vat_inference_used": bool(vat_inference_used),
        "vat_source": vat_source,
        "credit_match_details": credit_match_details or [],
        "engine_status_flags": list(dict.fromkeys(warn_parts)),
        "reconciliation_snapshot": {
            "supplier_vat_rate_used": str(supplier_vat_rate_used)
            if supplier_vat_rate_used is not None
            else None,
            "settlement": settlement_snapshot,
            "invoice_input": {
                "supplier_name": str(inv_raw.get("supplier_name") or ""),
                "invoice_number": str(inv_raw.get("invoice_number") or ""),
                "type": str(inv_raw.get("type") or "invoice"),
                "iban": str(inv_raw.get("iban") or ""),
                "amount": str(inv_raw.get("amount")) if inv_raw.get("amount") is not None else None,
                "amount_excl_vat": str(inv_raw.get("amount_excl_vat"))
                if inv_raw.get("amount_excl_vat") is not None
                else None,
                "discount": str(inv_raw.get("discount")) if inv_raw.get("discount") is not None else None,
                "invoice_date": str(inv_raw.get("invoice_date") or ""),
                "invoice_date_source": str(inv_raw.get("invoice_date_source") or "missing"),
                "match_status": str(inv_raw.get("match_status") or ""),
            },
            "parsed_amount_result": _trace_compact_amount_result(inv_raw),
            "final_amount_decimal": str(final_amount.quantize(_MONEY_QUANT)),
            "applied_transformations": {
                "amount_before_discount": str(amount_before_discount.quantize(_MONEY_QUANT)),
                "discount_amount": str(discount_amount.quantize(_MONEY_QUANT)),
                "discount_base_excl": str(discount_base_excl.quantize(_MONEY_QUANT))
                if isinstance(discount_base_excl, Decimal)
                else None,
                "credit_total_incl": str(credit_total_incl.quantize(_MONEY_QUANT)),
                "credit_total_excl": str(credit_total_excl.quantize(_MONEY_QUANT))
                if isinstance(credit_total_excl, Decimal)
                else None,
            },
        },
    }


def _normalize_invoice_for_engine(inv: dict) -> tuple[dict | None, str | None]:
    raw_amount = _amount_value_from_invoice(inv)
    if raw_amount is None:
        return None, "missing_amount"
    try:
        amount_dec = _to_decimal_money(raw_amount, field="amount")
    except ValueError:
        return None, "amount_invalid_format"

    raw_discount = inv.get("discount")
    if raw_discount in (None, ""):
        discount_dec = Decimal("0.00")
    else:
        try:
            discount_dec = _to_decimal_money(raw_discount, field="discount")
        except ValueError:
            return None, "discount_invalid_format"

    raw_excl = inv.get("amount_excl_vat")
    amount_excl_vat_dec: Decimal | None = None
    if raw_excl is not None:
        try:
            amount_excl_vat_dec = _to_decimal_money(raw_excl, field="amount_excl_vat")
        except ValueError:
            return None, "amount_excl_vat_invalid_format"

    return {
        "raw": inv,
        "amount_dec": amount_dec,
        "discount_dec": discount_dec,
        "amount_excl_vat_dec": amount_excl_vat_dec,
    }, None


def _assert_decimal(value: object, *, field: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field} must be Decimal, got {type(value).__name__}")
    return value


def _decision_from_reason(
    *,
    status: str,
    reason_code: str,
    inv: dict,
    reason_detail: str | None = None,
    requires_rerun: bool = False,
) -> dict:
    if status not in (DECISION_INCLUDED, DECISION_NEEDS_REVIEW, DECISION_EXCLUDED):
        status = DECISION_NEEDS_REVIEW
    editable = status != DECISION_INCLUDED
    causal_inputs = ["amount", "iban", "supplier_name", "invoice_number", "match_status"]
    return build_decision(
        status=status,  # type: ignore[arg-type]
        reason_code=reason_code,
        reason_detail=reason_detail,
        editable=editable,
        requires_rerun=requires_rerun,
        causal_inputs=causal_inputs,
        input_fields={
            "amount": inv.get("amount"),
            "iban": inv.get("iban"),
            "supplier_name": inv.get("supplier_name"),
            "invoice_number": inv.get("invoice_number"),
            "match_status": inv.get("match_status"),
            "amount_result_status": str(
                (inv.get("amount_result") or {}).get("status")
                or (inv.get("amount_result") or {}).get("amount_status")
                or ""
            ),
        },
    )


def _invoice_with_decision(
    inv: dict,
    *,
    status: str,
    reason_code: str,
    reason_detail: str | None = None,
    requires_rerun: bool = False,
) -> dict:
    inv_copy = dict(inv)
    inv_copy["decision"] = _decision_from_reason(
        status=status,
        reason_code=reason_code,
        inv=inv,
        reason_detail=reason_detail,
        requires_rerun=requires_rerun,
    )
    inv_copy["engine_version"] = ENGINE_VERSION
    return inv_copy

def calculate_payments(
    invoices: list[dict],
    *,
    session_date: date | None = None,
    override_session: OverrideSession | None = None,
    force_pipeline: Literal["legacy", "settlement"] | None = None,
) -> EngineResult:
    """Bereken settlement groups (SSOT) + keyed legacy display projection.

    Args:
        session_date: Kalenderdatum voor ``execution_date`` bij modus direct;
            default ``date.today()`` indien None.
        force_pipeline: Overschrijft fork (shadow/tests only).
    """
    from logic.batch_trace import assert_legacy_output_isolation
    from logic.settlement_call_guard import (
        allocation_edges_from_result,
        assert_zero_settlement_calls,
        settlement_call_guard,
    )

    with settlement_call_guard():
        return _calculate_payments_inner(
            invoices,
            session_date=session_date,
            override_session=override_session,
            force_pipeline=force_pipeline,
            assert_legacy_output_isolation=assert_legacy_output_isolation,
            assert_zero_settlement_calls=assert_zero_settlement_calls,
            allocation_edges_from_result=allocation_edges_from_result,
        )


def _calculate_payments_inner(
    invoices: list[dict],
    *,
    session_date: date | None,
    override_session: OverrideSession | None,
    force_pipeline: Literal["legacy", "settlement"] | None,
    assert_legacy_output_isolation,
    assert_zero_settlement_calls,
    allocation_edges_from_result,
) -> EngineResult:
    err = _ErrorBuckets()
    settlement_groups: list[dict] = []
    legacy_payments: list[dict] = []
    review_documents: list[dict] = []
    sess = session_date if session_date is not None else date.today()
    batch_needs_settlement = batch_requires_settlement(invoices)
    if force_pipeline == "settlement":
        use_settlement = True
    elif force_pipeline == "legacy":
        use_settlement = False
    else:
        use_settlement = batch_needs_settlement
    log_batch_stage(
        "input",
        input_invoices=len(invoices),
        pipeline="settlement" if use_settlement else "legacy",
    )

    _ACCEPTED_STATUSES = {"matched", "new", "confirmed", "reviewed"}

    accepted: list[dict] = []
    for inv in invoices:
        ms = inv.get("match_status")
        if ms == "load_failed":
            code = str(inv.get("load_error") or "read_failed")
            reason = "pdf_no_text" if code == "no_text" else "pdf_read_failed"
            err.add(
                reason,
                inv.get("supplier_name"),
                [
                    _invoice_with_decision(
                        inv,
                        status=DECISION_EXCLUDED,
                        reason_code=reason,
                    )
                ],
            )
            continue
        if ms in _ACCEPTED_STATUSES:
            accepted.append(inv)
            continue
        if ms == "no_hint":
            reason = "no_supplier_hint"
        elif ms == "needs_review":
            reason = "needs_review"
        else:
            reason = "unmatched_supplier"
        reason_detail: str | None = None
        if reason == "needs_review":
            try:
                core = inv.get("db_core_matches") or []
                if not isinstance(core, list):
                    core = []
                mi = inv.get("match_info") if isinstance(inv.get("match_info"), dict) else {}
                missing: list[str] = []
                if isinstance(mi, dict):
                    if not bool(mi.get("iban_match")):
                        missing.append("IBAN")
                    if not bool(mi.get("customer_code_match")):
                        missing.append("customer_code")
                    if not bool(mi.get("alias_match")):
                        missing.append("alias")
                ocr_flags: list[str] = []
                if bool(inv.get("ocr_iban_attempted")) and not bool(str(inv.get("iban") or "").strip()):
                    ocr_flags.append("ocr_iban_no_result")
                if str(inv.get("ocr_iban_error") or "").strip():
                    ocr_flags.append(f"ocr_iban_error={str(inv.get('ocr_iban_error'))}")
                if bool(inv.get("ocr_hint_attempted")) and not bool(str(inv.get("supplier_hint") or "").strip()):
                    ocr_flags.append("ocr_hint_no_result")
                if str(inv.get("ocr_hint_error") or "").strip():
                    ocr_flags.append(f"ocr_hint_error={str(inv.get('ocr_hint_error'))}")
                reason_detail = (
                    "core_matches="
                    + ",".join([str(x) for x in core if str(x).strip()])
                    + (";missing=" + ",".join(missing) if missing else "")
                    + (";ocr=" + ",".join(ocr_flags) if ocr_flags else "")
                )
            except Exception:
                reason_detail = None
        _agent_log(
            "H1",
            "logic/payment_engine.py:calculate_payments",
            "invoice rejected into errors bucket",
            {
                "reason": reason,
                "match_status": ms,
                "supplier_name": str(inv.get("supplier_name") or ""),
                "supplier_hint": str(inv.get("supplier_hint") or ""),
                "db_core_match_count": int(inv.get("db_core_match_count") or 0),
                "db_core_matches": inv.get("db_core_matches") or [],
                "has_iban": bool(str(inv.get("iban") or "").strip()),
                "has_amount": inv.get("amount") is not None,
                "has_invoice_date": bool(str(inv.get("invoice_date") or "").strip()),
            },
        )
        err.add(
            reason,
            inv.get("supplier_name"),
            [
                _invoice_with_decision(
                    inv,
                    status=DECISION_NEEDS_REVIEW
                    if reason in ("needs_review", "unmatched_supplier")
                    else DECISION_EXCLUDED,
                    reason_code=reason,
                    reason_detail=reason_detail,
                    requires_rerun=reason in ("needs_review", "unmatched_supplier"),
                )
            ],
        )

    groups: dict[str, list[dict]] = {}
    for inv in accepted:
        sn = inv.get("supplier_name")
        if sn is None or (isinstance(sn, str) and not str(sn).strip()):
            err.add(
                "missing_supplier_name",
                None,
                [
                    _invoice_with_decision(
                        inv,
                        status=DECISION_EXCLUDED,
                        reason_code="missing_supplier_name",
                    )
                ],
            )
            continue
        gkey = str(sn).strip().lower()
        groups.setdefault(gkey, []).append(inv)

    log_batch_stage(
        "accepted",
        accepted=len(accepted),
        supplier_groups=len(groups),
        pipeline="settlement" if use_settlement else "legacy",
    )

    if use_settlement:
        for _gkey, group_invs in sorted(groups.items(), key=lambda x: x[0]):
            _process_supplier_group_settlement(
                group_invs, err, settlement_groups, sess, override_session
            )
        log_batch_stage(
            "settlement_built",
            settlement_groups=len(settlement_groups),
            pipeline="settlement",
        )
    else:
        from logic.payment_engine_legacy import process_supplier_group_legacy

        for _gkey, group_invs in sorted(groups.items(), key=lambda x: x[0]):
            process_supplier_group_legacy(group_invs, err, legacy_payments, sess)
        legacy_payments = canonicalize_payments(legacy_payments)
        log_batch_stage(
            "legacy_built",
            legacy_payments=len(legacy_payments),
            pipeline="legacy",
        )

    for bucket in err.to_list():
        for inv in bucket.get("invoices") or []:
            if isinstance(inv, dict):
                inv_copy = dict(inv)
                inv_copy["_review_reason"] = bucket.get("reason")
                review_documents.append(inv_copy)

    if use_settlement:
        groups_sorted = sort_settlement_groups(settlement_groups)
        result = EngineResult(
            settlement_groups=groups_sorted,
            review_documents=review_documents,
            pipeline="settlement",
        )
    else:
        result = EngineResult(
            settlement_groups=[],
            review_documents=review_documents,
            legacy_payments=legacy_payments,
            pipeline="legacy",
        )
        if force_pipeline is None and not batch_needs_settlement:
            assert_zero_settlement_calls(context="legacy_production")
            assert allocation_edges_from_result(result.settlement_groups) == 0
            assert_legacy_output_isolation(result)
    log_batch_summary(
        input_invoices=len(invoices),
        settlement_groups=len(result.settlement_groups),
        review_documents=len(result.review_documents),
        pipeline=result.pipeline,
        extra=f"legacy_payments={len(result.legacy_payments or [])}",
    )
    return result


def calculate_payments_with_overrides(
    invoices: list[dict],
    *,
    override_session: OverrideSession | None = None,
    session_date: date | None = None,
    force_pipeline: Literal["legacy", "settlement"] | None = None,
) -> EngineResult:
    """Run payment engine with optional credit override session."""
    return calculate_payments(
        invoices,
        session_date=session_date,
        override_session=override_session,
        force_pipeline=force_pipeline,
    )


class _ErrorBuckets:
    """Fouten gegroepeerd op (reason, supplier_name)."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str | None], list[dict]] = {}

    def add(self, reason: str, supplier_name: str | None, invoice_dicts: list[dict]) -> None:
        key = (reason, supplier_name)
        self._data.setdefault(key, []).extend(invoice_dicts)

    def to_list(self) -> list[dict]:
        return [
            {"supplier_name": sup, "reason": reason, "invoices": invs}
            for (reason, sup), invs in sorted(
                self._data.items(),
                key=lambda item: (item[0][0], item[0][1] or ""),
            )
        ]

def _doc_type(d: dict) -> str:
    t = d.get("type")
    if t == "credit_note":
        return "credit_note"
    return "invoice"


def batch_requires_settlement(invoices: list[dict]) -> bool:
    """True when the batch contains at least one credit note document."""
    return any(_doc_type(inv) == "credit_note" for inv in invoices)


def _ambiguous_amount_bucket_reason(amt_result: dict) -> str:
    """``ambiguous`` from parser: either multiple parsed amounts or an unclear single hit."""
    cands = amt_result.get("candidates")
    n = len(cands) if isinstance(cands, list) else 0
    if n >= 2:
        return "amount_ambiguous"
    return "amount_uncertain"


def _apply_settlement_outcomes(
    settlement,
    *,
    valid_invoices: list[dict],
    credits: list[dict],
    group_supplier: str | None,
    err: _ErrorBuckets,
) -> set[str]:
    """Emit errors for refund/manual-review settlement groups; return blocked invoice doc ids."""
    raw_by_doc: dict[str, dict] = {}
    for norm in valid_invoices:
        raw_by_doc[document_id(norm)] = norm["raw"]
    for norm in credits:
        raw_by_doc[document_id(norm)] = norm["raw"]

    refund_doc_ids: set[str] = set()
    manual_review_credit_doc_ids: set[str] = set()
    refund_detail_by_doc: dict[str, str] = {}

    for group in settlement.groups:
        group_doc_ids = {line.document_id for line in group.invoices} | {
            line.document_id for line in group.credits
        }
        if group.status == SETTLEMENT_REFUND_REQUIRED:
            refund_doc_ids |= group_doc_ids
            detail = f"refund_amount={group.refund_amount}"
            for doc_id in group_doc_ids:
                refund_detail_by_doc[doc_id] = detail
        elif group.status == SETTLEMENT_MANUAL_REVIEW:
            for line in group.credits:
                manual_review_credit_doc_ids.add(line.document_id)

    if refund_doc_ids:
        refund_raws = [raw_by_doc[d] for d in sorted(refund_doc_ids) if d in raw_by_doc]
        err.add(
            REASON_CREDIT_REFUND_REQUIRED,
            group_supplier,
            [
                _invoice_with_decision(
                    raw,
                    status=DECISION_NEEDS_REVIEW,
                    reason_code=REASON_CREDIT_REFUND_REQUIRED,
                    reason_detail=refund_detail_by_doc.get(document_id({"raw": raw})),
                    requires_rerun=True,
                )
                for raw in refund_raws
            ],
        )

    for doc_id in sorted(manual_review_credit_doc_ids):
        if doc_id in refund_doc_ids:
            continue
        raw = raw_by_doc.get(doc_id)
        if raw is None:
            continue
        err.add(
            "credit_match_needs_review",
            group_supplier,
            [
                _invoice_with_decision(
                    raw,
                    status=DECISION_NEEDS_REVIEW,
                    reason_code="credit_match_needs_review",
                    requires_rerun=True,
                )
            ],
        )

    return refund_doc_ids


def _process_supplier_group_settlement(
    group_invs: list[dict],
    err: _ErrorBuckets,
    settlement_groups_out: list[dict],
    session: date,
    override_session: OverrideSession | None = None,
) -> None:
    from logic.settlement_call_guard import record_settlement_call

    record_settlement_call("settlement_pipeline")
    group_supplier = group_invs[0].get("supplier_name")
    display_name = str(group_supplier) if group_supplier is not None else ""

    credits_raw = [x for x in group_invs if _doc_type(x) == "credit_note"]
    all_invoices_raw = [x for x in group_invs if _doc_type(x) != "credit_note"]

    credits: list[dict] = []
    for credit in credits_raw:
        normalized_credit, credit_reason = _normalize_invoice_for_engine(credit)
        if normalized_credit is None:
            rest = [x for x in group_invs if x is not credit]
            reason = credit_reason or "amount_invalid_format"
            err.add(
                reason,
                group_supplier,
                [
                    _invoice_with_decision(credit, status=DECISION_EXCLUDED, reason_code=reason),
                    *[
                        _invoice_with_decision(x, status=DECISION_EXCLUDED, reason_code=reason)
                        for x in rest
                    ],
                ],
            )
            return
        credits.append(normalized_credit)

    valid_invoices: list[dict] = []
    for inv_raw in all_invoices_raw:
        amt_result = inv_raw.get("amount_result") or {}
        amt_status = str(amt_result.get("status") or amt_result.get("amount_status") or "").strip().lower()
        if amt_status == "ambiguous":
            reason = _ambiguous_amount_bucket_reason(amt_result)
            err.add(
                reason,
                inv_raw.get("supplier_name"),
                [
                    _invoice_with_decision(
                        inv_raw,
                        status=DECISION_NEEDS_REVIEW,
                        reason_code=REASON_AMBIGUOUS if reason == "amount_ambiguous" else REASON_UNCERTAIN,
                        requires_rerun=True,
                    )
                ],
            )
        elif amt_status == "failed":
            err.add(
                "amount_failed",
                inv_raw.get("supplier_name"),
                [
                    _invoice_with_decision(
                        inv_raw,
                        status=DECISION_EXCLUDED,
                        reason_code="amount_failed",
                    )
                ],
            )
        else:
            normalized_invoice, inv_reason = _normalize_invoice_for_engine(inv_raw)
            if normalized_invoice is None:
                reason = inv_reason or "amount_invalid_format"
                err.add(
                    reason,
                    inv_raw.get("supplier_name"),
                    [
                        _invoice_with_decision(
                            inv_raw,
                            status=DECISION_EXCLUDED,
                            reason_code=reason,
                        )
                    ],
                )
                continue
            valid_invoices.append(normalized_invoice)

    if not valid_invoices and credits:
        err.add(
            "credit_note_only",
            group_supplier,
            [
                _invoice_with_decision(c["raw"], status=DECISION_EXCLUDED, reason_code="credit_note_only")
                for c in credits
            ],
        )
        return

    if not valid_invoices and not credits:
        return

    linked: dict[int, list[dict]] = {}
    match_results = match_credits_in_batch(group_invs)
    match_results, override_events = apply_credit_overrides(
        match_results,
        override_session,
        batch_invoices=group_invs,
    )
    settlement = compute_settlement_groups(
        match_results,
        valid_invoices,
        credits,
        supplier_name=display_name,
    )
    refund_doc_ids = _apply_settlement_outcomes(
        settlement,
        valid_invoices=valid_invoices,
        credits=credits,
        group_supplier=group_supplier,
        err=err,
    )
    linked = build_engine_credit_links(match_results, credits, valid_invoices)
    credit_allocations = build_engine_credit_allocations(match_results, credits, valid_invoices)

    invoice_drafts: dict[str, InvoiceDraft] = {}

    pct_100 = Decimal("100.00")

    for inv in sorted(
        valid_invoices,
        key=lambda x: (-x["amount_dec"], str(x["raw"].get("invoice_number", ""))),
    ):
        inv_raw = inv["raw"]
        creds = linked.get(id(inv), [])
        alloc_pairs = credit_allocations.get(id(inv), [])
        credit_applied_total = sum(
            (amt for _, amt in alloc_pairs),
            start=Decimal("0.00"),
        ).quantize(_MONEY_QUANT)
        try:
            discount = _assert_decimal(inv["discount_dec"], field="invoice.discount_dec")
        except TypeError:
            err.add(
                "internal_money_type_error",
                inv_raw.get("supplier_name"),
                [
                    _invoice_with_decision(
                        inv_raw,
                        status=DECISION_EXCLUDED,
                        reason_code="internal_money_type_error",
                    )
                ],
            )
            continue
        warn_parts: list[str] = []
        if inv_raw.get("iban_mismatch"):
            warn_parts.append("iban_mismatch_supplier")
        if inv_raw.get("supplier_term_trusted") is False:
            warn_parts.append("supplier_term_not_applied")
        inv_date = inv_raw.get("invoice_date")
        src = str(inv_raw.get("invoice_date_source") or "missing")
        if not inv_date:
            warn_parts.append("missing_invoice_date")
        inv_amt_status, inv_amt_result = _effective_amount_status(inv_raw)
        if inv_amt_status == "tentative":
            warn_parts.append("amount_tentative")
        elif inv_amt_status == "low_confidence":
            warn_parts.append("amount_low_confidence")
        elif str(inv_raw.get("amount_confidence") or "").strip().lower() == "low":
            # Legacy fallback for dicts without amount_result
            warn_parts.append("amount_low_confidence")

        vat_rate = normalize_supplier_vat_rate_pct(inv_raw.get("supplier_vat_rate", 21))

        discount_skipped_reason: str | None = None
        vat_inference_used = False
        discount_base_excl: Decimal | None = None
        credit_total_incl = credit_applied_total
        credit_total_excl: Decimal | None = None
        korting = Decimal("0.00")
        vat_source: str | None = None

        if not creds:
            amt_dec = inv["amount_dec"]
            amount_before_discount = amt_dec
            if discount > Decimal("0"):
                excl_base = incl_amount_to_excl_for_discount(amount_before_discount, vat_rate)
                korting = (excl_base * discount / pct_100).quantize(_MONEY_QUANT)
                vat_inference_used = True
                discount_base_excl = excl_base
                if vat_rate == 21:
                    vat_source = "calculated_21"
                elif vat_rate == 0:
                    vat_source = "calculated_0"
                else:
                    vat_source = "calculated_other"
            te_betalen_dec = (amt_dec - korting).quantize(_MONEY_QUANT)
        else:
            saldo_incl = (inv["amount_dec"] - credit_applied_total).quantize(_MONEY_QUANT)
            amount_before_discount = saldo_incl
            if discount > Decimal("0"):
                excl_base = incl_amount_to_excl_for_discount(amount_before_discount, vat_rate)
                korting = (excl_base * discount / pct_100).quantize(_MONEY_QUANT)
                vat_inference_used = True
                discount_base_excl = excl_base
                credit_total_excl = sum(
                    (
                        incl_amount_to_excl_for_discount(amt, vat_rate)
                        for _, amt in alloc_pairs
                    ),
                    start=Decimal("0.00"),
                ).quantize(_MONEY_QUANT)
                if vat_rate == 21:
                    vat_source = "calculated_21"
                elif vat_rate == 0:
                    vat_source = "calculated_0"
                else:
                    vat_source = "calculated_other"
            te_betalen_dec = (saldo_incl - korting).quantize(_MONEY_QUANT)

        inv_credit_match_details = _credit_match_details_for_credits(creds, match_results)
        if inv_credit_match_details and any(
            d.get("match_method") == "manual_review"
            or d.get("warnings")
            or Decimal(str(d.get("remaining_credit") or "0")) > Decimal("0")
            for d in inv_credit_match_details
        ):
            warn_parts.append("credit_match_needs_review")

        warning: str | None = "|".join(warn_parts) if warn_parts else None
        if warning:
            _agent_log(
                "H3",
                "logic/payment_engine.py:_process_supplier_group",
                "payment warning computed",
                {
                    "supplier_name": str(inv_raw.get("supplier_name") or ""),
                    "invoice_number": str(inv_raw.get("invoice_number") or ""),
                    "warning": warning,
                    "discount_pct": str(discount),
                    "amount_excl_vat_present": inv_raw.get("amount_excl_vat") is not None,
                    "invoice_date_present": bool(str(inv_raw.get("invoice_date") or "").strip()),
                },
            )

        sup_out = inv_raw.get("supplier_name")
        sup_for_err = sup_out if sup_out is not None else group_supplier

        iban_raw = (inv_raw.get("iban") or "").strip()
        iban_clean = _clean_iban(iban_raw) if iban_raw else ""
        iban_valid = bool(iban_clean and _is_plausible_iban(iban_clean))

        credit_notes_applied = [
            str(c["raw"]["invoice_number"])
            for c in creds
            if c["raw"].get("invoice_number") is not None
        ]

        trusted = bool(inv_raw.get("supplier_term_trusted"))
        try:
            raw_term = int(inv_raw.get("supplier_payment_term_days_raw") or 0)
        except (TypeError, ValueError):
            raw_term = 0
        effective_term = raw_term if trusted else 0
        inv_date_out = inv_raw.get("invoice_date")
        if inv_date_out is not None:
            inv_date_out = str(inv_date_out).strip() or None

        decision_status = DECISION_INCLUDED
        decision_reason = REASON_EXPORT_ALLOWED
        if te_betalen_dec <= Decimal("0.00"):
            decision_status = DECISION_EXCLUDED
            decision_reason = "zero_amount"
        elif not iban_valid:
            decision_status = DECISION_EXCLUDED
            decision_reason = REASON_MISSING_IBAN if not iban_raw else REASON_INVALID_IBAN
        elif inv_amt_status in ("tentative", "low_confidence"):
            decision_status = DECISION_NEEDS_REVIEW
            decision_reason = REASON_LOW_CONFIDENCE
        elif inv_amt_status == "ambiguous":
            decision_status = DECISION_NEEDS_REVIEW
            decision_reason = REASON_AMBIGUOUS
        elif inv_amt_status == "failed":
            decision_status = DECISION_EXCLUDED
            decision_reason = REASON_MISSING_AMOUNT
        elif "credit_match_needs_review" in warn_parts:
            decision_status = DECISION_NEEDS_REVIEW
            decision_reason = TRACE_REASON_CREDIT_MATCH_REVIEW
        decision_payload = _decision_from_reason(
            status=decision_status,
            reason_code=decision_reason,
            inv=inv_raw,
            requires_rerun=decision_status == DECISION_NEEDS_REVIEW,
        )
        inv_group = settlement_for_invoice(inv, settlement)
        settlement_dict = settlement_group_to_dict(inv_group) if inv_group is not None else None
        decision_trace = _build_payment_decision_trace(
            inv_raw=inv_raw,
            creds=creds,
            warn_parts=warn_parts,
            discount_pct=discount,
            discount_amount=korting,
            final_amount=te_betalen_dec,
            amount_before_discount=amount_before_discount,
            vat_inference_used=vat_inference_used,
            discount_skipped_reason=discount_skipped_reason,
            discount_base_excl=discount_base_excl,
            credit_total_incl=credit_total_incl.quantize(_MONEY_QUANT),
            credit_total_excl=credit_total_excl,
            vat_source=vat_source,
            supplier_vat_rate_used=vat_rate if discount > Decimal("0") else None,
            credit_match_details=inv_credit_match_details,
            settlement_snapshot=settlement_dict,
        )
        doc_id = document_id(inv)
        invoice_drafts[doc_id] = InvoiceDraft(
            doc_id=doc_id,
            inv_raw=inv_raw,
            te_betalen_dec=te_betalen_dec,
            decision=decision_payload,
            decision_trace=decision_trace,
            credit_notes_applied=credit_notes_applied,
            warning=warning,
            iban=iban_clean,
            iban_valid=iban_valid,
            trusted=trusted,
            raw_term=raw_term,
            effective_term=effective_term,
            inv_date_out=inv_date_out,
            invoice_date_source=src if src in ("parsed", "manual", "missing") else "missing",
        )

    raw_by_id: dict[str, dict] = {}
    for norm in valid_invoices:
        raw_by_id[document_id(norm)] = norm["raw"]
    for norm in credits:
        raw_by_id[document_id(norm)] = norm["raw"]

    built = build_groups_from_drafts(
        settlement.groups,
        invoice_drafts,
        raw_by_id,
        refund_doc_ids=refund_doc_ids,
        execution_date=session.isoformat(),
        engine_version=ENGINE_VERSION,
        override_history=override_events,
    )
    settlement_groups_out.extend(built)
