"""Apply persisted supplier credit_profile during credit enrichment only."""

from __future__ import annotations

import copy
import re
from decimal import Decimal
from typing import Any

from logic.credit_classifier import CreditDetectionResult
from logic.credit_references import extract_referenced_invoice_numbers
from parser.profile_extractor import extract_amount_with_field_spec, extract_with_profile

_CREDIT_TYPE = "credit_note"
_CREDIT_THRESHOLD = 50


def _has_user_override(inv: dict[str, Any]) -> bool:
    if inv.get("credit_profile_user_override") is True:
        return True
    for key in ("amount_result", "invoice_number_result"):
        res = inv.get(key)
        if isinstance(res, dict) and res.get("user_overridden"):
            return True
    return False


def credit_profile_may_apply(
    inv: dict[str, Any],
    detection: CreditDetectionResult,
) -> bool:
    """
    Gate credit_profile application.

    Requires attached profile + supplier_key, confirmed credit document type,
    and either sufficient detection confidence or explicit user override.
    """
    if str(inv.get("type") or "") != _CREDIT_TYPE:
        return False
    profile = inv.get("credit_profile")
    if not isinstance(profile, dict) or not profile:
        return False
    if not str(inv.get("supplier_key") or "").strip():
        return False
    if _has_user_override(inv):
        return True
    return bool(detection.is_credit and detection.confidence >= _CREDIT_THRESHOLD)


def _apply_ocr_corrections(text: str, corrections: list[Any] | None) -> str:
    if not corrections:
        return text
    out = text
    for item in corrections:
        if not isinstance(item, dict):
            continue
        pat = str(item.get("pattern") or "").strip()
        if not pat:
            continue
        repl = str(item.get("replacement") or "")
        try:
            out = re.sub(pat, repl, out)
        except re.error:
            continue
    return out


def _extract_credit_number(raw_text: str, profile: dict[str, Any]) -> str | None:
    spec = profile.get("credit_number")
    if not isinstance(spec, dict):
        return None
    extracted = extract_with_profile(raw_text, {"invoice_number": spec})
    val = extracted.get("invoice_number")
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def _extract_credit_amount(raw_text: str, profile: dict[str, Any]) -> float | None:
    spec = profile.get("amount")
    if not isinstance(spec, dict):
        return None
    lines = (raw_text or "").split("\n")
    dec = extract_amount_with_field_spec(lines, spec)
    if dec is None:
        return None
    return float(dec)


def _extract_reference_patterns(text: str, patterns: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in (text or "").splitlines():
        for pat_s in patterns:
            try:
                pat = re.compile(pat_s)
            except re.error:
                continue
            for m in pat.finditer(line):
                raw = str(m.group(1) if m.lastindex else m.group(0)).strip()
                if not raw or len(raw) < 3:
                    continue
                key = raw.upper()
                if key in seen:
                    continue
                seen.add(key)
                out.append(raw)
    return out


def _patch_amount_result(inv: dict[str, Any], amount: float) -> None:
    amt_result = inv.get("amount_result")
    if not isinstance(amt_result, dict):
        return
    dec = Decimal(str(amount)).quantize(Decimal("0.01"))
    amt_result["value"] = str(dec)
    if amt_result.get("selected_value") is not None:
        amt_result["selected_value"] = str(dec)
    amt_result["source"] = "credit_profile"
    amt_result["status"] = "confirmed"


def _patch_invoice_number_result(inv: dict[str, Any], credit_number: str) -> None:
    inv_result = inv.get("invoice_number_result")
    if not isinstance(inv_result, dict):
        return
    inv_result["value"] = credit_number
    if inv_result.get("selected_value") is not None:
        inv_result["selected_value"] = credit_number
    inv_result["source"] = "credit_profile"
    inv_result["status"] = "confirmed"


def apply_credit_profile_overrides(
    inv: dict[str, Any],
    *,
    detection: CreditDetectionResult,
) -> dict[str, Any]:
    """Patch amount, credit number, and references from attached credit_profile."""
    out = copy.deepcopy(inv)
    if not credit_profile_may_apply(out, detection):
        return out

    profile = out.get("credit_profile")
    if not isinstance(profile, dict):
        return out

    text = str(out.get("raw_text") or "")
    ocr = profile.get("ocr_corrections")
    if isinstance(ocr, list) and ocr:
        text = _apply_ocr_corrections(text, ocr)
        out["raw_text"] = text

    applied_fields: list[str] = []

    amount = _extract_credit_amount(text, profile)
    if amount is not None:
        out["amount"] = amount
        _patch_amount_result(out, amount)
        applied_fields.append("amount")

    credit_number = _extract_credit_number(text, profile)
    if credit_number is not None:
        out["invoice_number"] = credit_number
        _patch_invoice_number_result(out, credit_number)
        applied_fields.append("credit_number")

    generic_refs = extract_referenced_invoice_numbers(text)
    refs = list(generic_refs)
    if not refs and detection.confidence < _CREDIT_THRESHOLD:
        patterns = profile.get("reference_patterns")
        if isinstance(patterns, list) and patterns:
            refs = _extract_reference_patterns(text, [str(p) for p in patterns if str(p or "").strip()])

    if refs:
        out["referenced_invoice_numbers"] = refs
        applied_fields.append("referenced_invoice_numbers")

    if applied_fields:
        cd = out.get("credit_detection")
        if isinstance(cd, dict):
            cd = dict(cd)
            cd["profile_applied"] = True
            cd["profile_fields"] = applied_fields
            out["credit_detection"] = cd

    return out
