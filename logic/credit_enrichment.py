"""Enrich parsed invoices with credit detection metadata before payment engine."""

from __future__ import annotations

import copy
from typing import Any

from logic.credit_classifier import CreditDetectionResult, classify_credit_document
from logic.credit_references import extract_referenced_invoice_numbers

_CREDIT_TYPE = "credit_note"
_INVOICE_TYPE = "invoice"


def _credit_detection_snapshot(result: CreditDetectionResult) -> dict[str, Any]:
    return {
        "is_credit": result.is_credit,
        "confidence": result.confidence,
        "signals": list(result.signals),
        "reason": result.reason,
    }


def _normalize_credit_amount_fields(inv: dict[str, Any]) -> None:
    """Store credit amounts as positive magnitudes; preserve sign metadata for trace."""
    amount = inv.get("amount")
    if amount is None:
        return
    try:
        from decimal import Decimal

        if isinstance(amount, (int, float)):
            dec = Decimal(str(amount))
        elif isinstance(amount, Decimal):
            dec = amount
        else:
            dec = Decimal(str(amount).strip().replace(",", "."))
    except Exception:
        return
    if dec < 0:
        inv["amount_sign"] = "credit"
        inv["amount"] = float(abs(dec))
        amt_result = inv.get("amount_result")
        if isinstance(amt_result, dict):
            val = amt_result.get("value")
            if val is not None:
                try:
                    vdec = Decimal(str(val).strip().replace(",", "."))
                    if vdec < 0:
                        amt_result["value"] = str(abs(vdec).quantize(Decimal("0.01")))
                except Exception:
                    pass


def enrich_credit_document(inv: dict[str, Any]) -> dict[str, Any]:
    """Classify, extract references, and normalize amounts on one invoice dict copy."""
    out = copy.deepcopy(inv)
    text = str(out.get("raw_text") or "")
    detection = classify_credit_document(
        text,
        metadata={"type": out.get("type"), "amount": out.get("amount")},
    )
    out["credit_detection"] = _credit_detection_snapshot(detection)

    if detection.is_credit:
        out["type"] = _CREDIT_TYPE
    elif str(out.get("type") or "") == _CREDIT_TYPE and not detection.is_credit:
        # Parser marked credit but classifier disagrees — keep credit, flag review.
        out["credit_detection"]["needs_review"] = True
    else:
        out.setdefault("type", _INVOICE_TYPE)

    refs = extract_referenced_invoice_numbers(text)
    if refs:
        out["referenced_invoice_numbers"] = refs
    else:
        out.setdefault("referenced_invoice_numbers", [])

    if str(out.get("type") or "") == _CREDIT_TYPE:
        _normalize_credit_amount_fields(out)

    return out


def enrich_credit_documents(invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich all invoices after supplier matching, before payment engine."""
    from logic.settlement_call_guard import record_settlement_call

    record_settlement_call("enrich_credit_documents")
    return [enrich_credit_document(inv) for inv in invoices]
