"""Legacy per-invoice payment path (HEAD 01550c0 behaviour) for no-credit batches."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from logic.payment_amounts import (
    format_eur_xml,
    incl_amount_to_excl_for_discount,
    normalize_supplier_vat_rate_pct,
)
from logic.payment_decisions import (
    DECISION_EXCLUDED,
    DECISION_INCLUDED,
    DECISION_NEEDS_REVIEW,
    REASON_AMBIGUOUS,
    REASON_EXPORT_ALLOWED,
    REASON_INVALID_IBAN,
    REASON_LOW_CONFIDENCE,
    REASON_MISSING_AMOUNT,
    REASON_MISSING_IBAN,
    REASON_UNCERTAIN,
)
from logic.payment_engine import (
    ENGINE_VERSION,
    _MONEY_QUANT,
    _agent_log,
    _ambiguous_amount_bucket_reason,
    _assert_decimal,
    _build_payment_decision_trace,
    _clean_iban,
    _decision_from_reason,
    _doc_type,
    _effective_amount_status,
    _invoice_with_decision,
    _is_plausible_iban,
    _normalize_invoice_for_engine,
)

if TYPE_CHECKING:
    from logic.payment_engine import _ErrorBuckets


def process_supplier_group_legacy(
    group_invs: list[dict],
    err: _ErrorBuckets,
    payments: list[dict],
    session: date,
) -> None:
    """One payment dict per valid invoice — identical to pre-settlement engine."""
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
    for credit in credits:
        try:
            _assert_decimal(credit["amount_dec"], field="credit.amount_dec")
        except TypeError:
            err.add(
                "internal_money_type_error",
                group_supplier,
                [
                    _invoice_with_decision(
                        credit["raw"],
                        status=DECISION_EXCLUDED,
                        reason_code="internal_money_type_error",
                    )
                ],
            )
            return
        kandidaten = [inv for inv in valid_invoices if inv["amount_dec"] >= credit["amount_dec"]]
        if not kandidaten:
            err.add(
                "credit_exceeds_available_invoices",
                group_supplier,
                [
                    _invoice_with_decision(
                        credit["raw"],
                        status=DECISION_EXCLUDED,
                        reason_code="credit_exceeds_available_invoices",
                    ),
                    *[
                        _invoice_with_decision(
                            inv["raw"],
                            status=DECISION_EXCLUDED,
                            reason_code="credit_exceeds_available_invoices",
                        )
                        for inv in valid_invoices
                    ],
                ],
            )
            return
        best = min(
            kandidaten,
            key=lambda inv: (inv["amount_dec"], str(inv["raw"].get("invoice_number", ""))),
        )
        linked.setdefault(id(best), []).append(credit)

    for inv in valid_invoices:
        try:
            _assert_decimal(inv["amount_dec"], field="invoice.amount_dec")
        except TypeError:
            err.add(
                "internal_money_type_error",
                group_supplier,
                [
                    _invoice_with_decision(
                        inv["raw"],
                        status=DECISION_EXCLUDED,
                        reason_code="internal_money_type_error",
                    )
                ],
            )
            return
        creds = linked.get(id(inv), [])
        if not creds:
            continue
        total_c = sum((c["amount_dec"] for c in creds), start=Decimal("0.00"))
        if total_c > inv["amount_dec"]:
            err.add(
                "credit_exceeds_invoice_total",
                group_supplier,
                [
                    _invoice_with_decision(
                        raw_inv,
                        status=DECISION_EXCLUDED,
                        reason_code="credit_exceeds_invoice_total",
                    )
                    for raw_inv in group_invs
                ],
            )
            return

    pct_100 = Decimal("100.00")

    for inv in sorted(
        valid_invoices,
        key=lambda x: (-x["amount_dec"], str(x["raw"].get("invoice_number", ""))),
    ):
        inv_raw = inv["raw"]
        creds = linked.get(id(inv), [])
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
        inv_amt_status, _inv_amt_result = _effective_amount_status(inv_raw)
        if inv_amt_status == "tentative":
            warn_parts.append("amount_tentative")
        elif inv_amt_status == "low_confidence":
            warn_parts.append("amount_low_confidence")
        elif str(inv_raw.get("amount_confidence") or "").strip().lower() == "low":
            warn_parts.append("amount_low_confidence")

        vat_rate = normalize_supplier_vat_rate_pct(inv_raw.get("supplier_vat_rate", 21))

        discount_skipped_reason: str | None = None
        vat_inference_used = False
        discount_base_excl: Decimal | None = None
        credit_total_incl = sum((c["amount_dec"] for c in creds), start=Decimal("0.00"))
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
            saldo_incl = inv["amount_dec"] - sum(
                (c["amount_dec"] for c in creds), start=Decimal("0.00")
            )
            saldo_incl = saldo_incl.quantize(_MONEY_QUANT)
            amount_before_discount = saldo_incl
            if discount > Decimal("0"):
                excl_base = incl_amount_to_excl_for_discount(amount_before_discount, vat_rate)
                korting = (excl_base * discount / pct_100).quantize(_MONEY_QUANT)
                vat_inference_used = True
                discount_base_excl = excl_base
                credit_total_excl = sum(
                    (
                        incl_amount_to_excl_for_discount(c["amount_dec"], vat_rate)
                        for c in creds
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

        warning: str | None = "|".join(warn_parts) if warn_parts else None
        if warning:
            _agent_log(
                "H3",
                "logic/payment_engine_legacy.py:process_supplier_group_legacy",
                "payment warning computed",
                {
                    "supplier_name": str(inv_raw.get("supplier_name") or ""),
                    "invoice_number": str(inv_raw.get("invoice_number") or ""),
                    "warning": warning,
                },
            )

        sup_out = inv_raw.get("supplier_name")
        sup_for_err = sup_out if sup_out is not None else group_supplier

        if te_betalen_dec <= Decimal("0.00"):
            if te_betalen_dec == Decimal("0.00"):
                err.add(
                    "zero_amount",
                    sup_for_err,
                    [
                        _invoice_with_decision(
                            inv_raw,
                            status=DECISION_EXCLUDED,
                            reason_code="zero_amount",
                        )
                    ],
                )
            else:
                err.add(
                    "negative_amount",
                    sup_for_err,
                    [
                        _invoice_with_decision(
                            inv_raw,
                            status=DECISION_EXCLUDED,
                            reason_code="negative_amount",
                        )
                    ],
                )
            continue

        iban_raw = (inv_raw.get("iban") or "").strip()
        if not iban_raw:
            err.add(
                "missing_iban",
                sup_for_err,
                [
                    _invoice_with_decision(
                        inv_raw,
                        status=DECISION_EXCLUDED,
                        reason_code=REASON_MISSING_IBAN,
                    )
                ],
            )
            continue
        iban = _clean_iban(iban_raw)
        if not iban or not _is_plausible_iban(iban):
            err.add(
                "invalid_iban",
                sup_for_err,
                [
                    _invoice_with_decision(
                        inv_raw,
                        status=DECISION_EXCLUDED,
                        reason_code=REASON_INVALID_IBAN,
                    )
                ],
            )
            continue

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

        amount_display = format_eur_xml(te_betalen_dec).replace(".", ",")
        decision_status = DECISION_INCLUDED
        decision_reason = REASON_EXPORT_ALLOWED
        if inv_amt_status in ("tentative", "low_confidence"):
            decision_status = DECISION_NEEDS_REVIEW
            decision_reason = REASON_LOW_CONFIDENCE
        elif inv_amt_status == "ambiguous":
            decision_status = DECISION_NEEDS_REVIEW
            decision_reason = REASON_AMBIGUOUS
        elif inv_amt_status == "failed":
            decision_status = DECISION_EXCLUDED
            decision_reason = REASON_MISSING_AMOUNT
        decision_payload = _decision_from_reason(
            status=decision_status,
            reason_code=decision_reason,
            inv=inv_raw,
            requires_rerun=decision_status == DECISION_NEEDS_REVIEW,
        )
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
        )
        payments.append(
            {
                "supplier_name": str(sup_out) if sup_out is not None else display_name,
                "iban": iban,
                "amount": te_betalen_dec,
                "amount_display": amount_display,
                "description": inv_raw.get("description") if inv_raw.get("description") is not None else "",
                "invoice_number": str(inv_raw["invoice_number"])
                if inv_raw.get("invoice_number") is not None
                else "",
                "_source_file": str(inv_raw.get("source_file") or "").strip() or None,
                "credit_notes_applied": credit_notes_applied,
                "warning": warning,
                "iban_mismatch": bool(inv_raw.get("iban_mismatch")),
                "status": "ok" if decision_status == DECISION_INCLUDED else decision_status,
                "invoice_date": inv_date_out,
                "invoice_date_source": src
                if src in ("parsed", "manual", "missing")
                else "missing",
                "supplier_term_trusted": trusted,
                "supplier_payment_term_days_raw": raw_term,
                "supplier_payment_term_days_effective": effective_term,
                "date_mode": "direct",
                "execution_date": session.isoformat(),
                "decision_trace": decision_trace,
                "decision": decision_payload,
                "decision_batch_id": None,
                "engine_version": ENGINE_VERSION,
            }
        )
