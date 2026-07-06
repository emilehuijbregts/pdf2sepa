"""Enrich parsed invoices with credit detection metadata before payment engine."""

from __future__ import annotations

import copy
from typing import Any

from logic.credit_classifier import CreditDetectionResult, classify_credit_document
from logic.credit_profile_apply import apply_credit_profile_overrides
from logic.credit_references import extract_referenced_invoice_numbers
from logic.document_type_resolver import DocumentTypeResolution, resolve_document_type, resolution_to_dict
from parser.pdf_parser import build_description
from parser.profile_extractor import extract_with_profile

_CREDIT_TYPE = "credit_note"
_INVOICE_TYPE = "invoice"


def _credit_detection_snapshot(
    result: CreditDetectionResult,
    *,
    resolution: DocumentTypeResolution | None = None,
) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "is_credit": result.is_credit,
        "confidence": result.confidence,
        "signals": list(result.signals),
        "reason": result.reason,
    }
    if resolution is not None:
        snap["type_source"] = resolution.source
        snap["invoice_profile_score"] = resolution.invoice_profile_score
        snap["credit_profile_score"] = resolution.credit_profile_score
        if resolution.needs_review:
            snap["needs_review"] = True
    return snap


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


def _reapply_invoice_profile_fields(inv: dict[str, Any]) -> None:
    """Re-extract core invoice fields from extraction_profile after credit→invoice flip."""
    profile = inv.get("extraction_profile")
    raw_text = str(inv.get("raw_text") or "")
    if not isinstance(profile, dict) or not raw_text:
        return
    extracted = extract_with_profile(raw_text, profile)
    for field, result_key in (
        ("amount", "amount_result"),
        ("invoice_number", "invoice_number_result"),
        ("customer_number", "customer_number_result"),
    ):
        val = extracted.get(field)
        if val is None:
            continue
        inv[field] = val
        result = inv.get(result_key)
        if isinstance(result, dict):
            result = dict(result)
            result["value"] = str(val)
            if result.get("selected_value") is not None:
                result["selected_value"] = str(val)
            result["source"] = "profile"
            result["status"] = "confirmed"
            inv[result_key] = result


def _clear_credit_only_fields(inv: dict[str, Any]) -> None:
    inv["referenced_invoice_numbers"] = []
    inv.pop("amount_sign", None)


def enrich_credit_document(inv: dict[str, Any]) -> dict[str, Any]:
    """Classify, extract references, and normalize amounts on one invoice dict copy."""
    out = copy.deepcopy(inv)
    text = str(out.get("raw_text") or "")
    prior_type = str(out.get("type") or "").strip()

    resolution_snap = out.get("document_type_resolution")
    if isinstance(resolution_snap, dict) and resolution_snap.get("document_type"):
        resolved_type = str(resolution_snap.get("document_type") or "").strip()
        detection = classify_credit_document(
            text,
            metadata={"type": resolved_type, "amount": out.get("amount")},
        )
        resolution = DocumentTypeResolution(
            document_type=resolved_type,  # type: ignore[arg-type]
            source=str(resolution_snap.get("source") or "classifier"),  # type: ignore[arg-type]
            invoice_profile_score=float(resolution_snap.get("invoice_profile_score") or 0.0),
            credit_profile_score=float(resolution_snap.get("credit_profile_score") or 0.0),
            needs_review=bool(resolution_snap.get("needs_review")),
            reason=str(resolution_snap.get("reason") or ""),
            classifier_is_credit=bool(resolution_snap.get("classifier_is_credit")),
            classifier_confidence=int(resolution_snap.get("classifier_confidence") or 0),
        )
    else:
        detection = classify_credit_document(
            text,
            metadata={"type": out.get("type"), "amount": out.get("amount")},
        )
        resolution = resolve_document_type(out, detection=detection)
        out["document_type_resolution"] = resolution_to_dict(resolution)

    out["type"] = resolution.document_type
    out["credit_detection"] = _credit_detection_snapshot(detection, resolution=resolution)

    if resolution.document_type == _INVOICE_TYPE and prior_type == _CREDIT_TYPE:
        _clear_credit_only_fields(out)
        _reapply_invoice_profile_fields(out)
    elif resolution.document_type == _CREDIT_TYPE:
        detection_for_profile = classify_credit_document(
            text,
            metadata={"type": out.get("type"), "amount": out.get("amount")},
        )
        out = apply_credit_profile_overrides(out, detection=detection_for_profile)

        refs = extract_referenced_invoice_numbers(str(out.get("raw_text") or ""))
        existing_refs = out.get("referenced_invoice_numbers")
        if isinstance(existing_refs, list) and existing_refs:
            refs = list(existing_refs)
        elif refs:
            out["referenced_invoice_numbers"] = refs
        else:
            out.setdefault("referenced_invoice_numbers", [])

        _normalize_credit_amount_fields(out)
        cc = str(out.get("customer_number") or "").strip()
        inv_no = str(out.get("invoice_number") or "").strip()
        if cc and inv_no:
            desc = build_description(cc, inv_no)
            if desc:
                out["description"] = desc
    else:
        out.setdefault("referenced_invoice_numbers", [])

    return out


def enrich_credit_documents(invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich all invoices after supplier matching, before payment engine."""
    from logic.settlement_call_guard import record_settlement_call

    record_settlement_call("enrich_credit_documents")
    return [enrich_credit_document(inv) for inv in invoices]
