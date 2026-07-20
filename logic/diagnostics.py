"""Read-only diagnostics mapper: invoice snapshot + payment/decision → structured UI dict."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from logic.field_diagnostics import (
    amount_needs_attention,
    build_amount_diag_block,
    build_iban_diag_block,
    build_ident_field_diag_block,
)
from logic.payment_amounts import amount_to_decimal, resolved_payment_amount_for_export
from logic.profile_learning import can_offer_profile_learning
from logic.validation import clean_iban, mask_iban_for_log
from parser.field_adapters import normalize_amount_result_dict
from parser.field_model import FieldId, _LEGACY_VALUE_KEY_BY_FIELD, _RESULT_KEY_BY_FIELD
from parser.supplier_db import (
    CUSTOMER_NUMBER_MODE_NONE,
    customer_number_authoritative_value,
    customer_number_is_absent_or_none,
    infer_customer_number_mode_from_result,
)

_IBAN_REASON_CODES = frozenset({"missing_iban", "invalid_iban"})

_SUPPLIER_NEEDS_ATTENTION = frozenset(
    {"needs_review", "unmatched", "no_hint", "new", "load_failed"}
)

_MATCH_INFO_FLAG_KEYS = (
    "iban_match",
    "customer_code_match",
    "alias_match",
    "fuzzy_match",
    "kvk_match",
    "vat_match",
    "email_domain_match",
)

_BLOCKING_EXCLUDED_REASON_CODES = frozenset(
    {
        "pdf_read_failed",
        "pdf_no_text",
        "amount_failed",
        "missing_amount",
        "missing_iban",
        "invalid_iban",
        "no_supplier_hint",
        "unmatched_supplier",
        "missing_supplier_name",
        "credit_note_only",
        "zero_amount",
        "negative_amount",
        "credit_exceeds_available_invoices",
        "credit_exceeds_invoice_total",
        "internal_money_type_error",
    }
)

_SNAPSHOT_FIELDS = (
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
    "iban_result",
    "iban_mismatch",
    "ocr_iban_attempted",
    "ocr_iban_error",
    "amount_result",
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
)

def _diag_key(prefix: str, code: str) -> str:
    s = str(code or "").strip()
    if not s:
        return ""
    return f"{prefix}.{s}"


def _parse_warnings(pipe_str: str) -> list[str]:
    return [p.strip() for p in str(pipe_str or "").split("|") if p.strip()]


def _normalize_amount_result(ar: dict[str, Any] | None) -> dict[str, Any]:
    """Backward-compatible wrapper; canonical logic in ``normalize_amount_result_dict``."""
    n = normalize_amount_result_dict(ar)
    return {
        "status": n["status"],
        "source": n["source"],
        "value": n["value"],
        "confidence": n["confidence"],
        "candidates": n["candidates"],
    }


def _matched_by_from_snapshot(snap: dict[str, Any]) -> list[str]:
    core = snap.get("db_core_matches")
    if isinstance(core, list) and core:
        return [str(x).strip() for x in core if str(x).strip()]
    match_info = snap.get("match_info")
    if not isinstance(match_info, dict):
        return []
    labels: list[str] = []
    if match_info.get("iban_match"):
        labels.append("iban")
    if match_info.get("customer_code_match"):
        labels.append("customer_number")
    if match_info.get("kvk_match"):
        labels.append("kvk")
    if match_info.get("vat_match"):
        labels.append("vat")
    if match_info.get("email_domain_match"):
        labels.append("email")
    return labels


def _match_info_flags_compact(match_info: object) -> dict[str, bool] | None:
    if not isinstance(match_info, dict):
        return None
    flags = {k: bool(match_info.get(k)) for k in _MATCH_INFO_FLAG_KEYS if k in match_info}
    return flags or None


def _supplier_detail_nl(snap: dict[str, Any], status: str) -> str | None:
    if status != "needs_review":
        return None
    matched = _matched_by_from_snapshot(snap)
    if matched:
        return f"Kernkenmerken: {', '.join(matched)}"
    count = snap.get("db_core_match_count")
    if count is not None:
        try:
            n = int(count)
            return f"{n} kernkenmerk(en) in database"
        except (TypeError, ValueError):
            pass
    return None


def _supplier_display(snap: dict[str, Any]) -> str:
    sn = str(snap.get("supplier_name") or "").strip()
    if sn:
        return sn
    return str(snap.get("supplier_hint") or "").strip()


def _pdf_basename(snap: dict[str, Any], payment: dict[str, Any] | None) -> str:
    src = snap.get("source_file")
    if src:
        return Path(str(src)).name
    if payment:
        psf = payment.get("_source_file")
        if psf:
            return Path(str(psf)).name
    return ""


def _resolved_source_file_for_profile(snap: dict[str, Any], payment: dict[str, Any] | None) -> str | None:
    src = snap.get("source_file")
    if src:
        p = Path(str(src))
        if p.is_file():
            return str(p)
    if payment:
        psf = payment.get("_source_file")
        if psf:
            p = Path(str(psf))
            if p.is_file():
                return str(p)
    return None


def _payment_amount_resolved(payment: dict[str, Any] | None) -> bool:
    if not isinstance(payment, dict):
        return False
    cell = str(payment.get("amount") or "").strip()
    ar = payment.get("amount_result") if isinstance(payment.get("amount_result"), dict) else None
    if cell in ("", "?"):
        if isinstance(ar, dict):
            st = str(ar.get("status") or ar.get("amount_status") or "").strip().lower()
            if st in ("confirmed", "tentative"):
                raw_v = ar.get("value") or ar.get("selected_amount")
                if raw_v is not None:
                    try:
                        amount_to_decimal(str(raw_v))
                        return True
                    except (TypeError, ValueError):
                        pass
        return False
    try:
        amount_to_decimal(cell.replace(",", "."))
        return True
    except (TypeError, ValueError):
        return False


def _build_action_suggestions(
    snap: dict[str, Any],
    amount_status: str,
    match_status: str,
    warning_keys: list[str],
    customer_empty: bool,
    load_error: str | None,
    payment: dict[str, Any] | None = None,
) -> list[str]:
    suggestions: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        if text and text not in seen:
            seen.add(text)
            suggestions.append(text)

    if amount_status == "ambiguous":
        add("Klik op de bedragcel om een kandidaat te kiezen")
    elif amount_status == "failed":
        add("Vul het bedrag handmatig in de bedragcel")
    elif amount_status == "tentative":
        add("Controleer het bedrag of kies een kandidaat")

    if customer_empty:
        add("Vul klantnummer in kolom Klantnummer")

    if "iban_mismatch_supplier" in warning_keys:
        add("Controleer IBAN in leveranciersdatabase")

    if match_status == "unmatched":
        add("Voeg leverancier toe of corrigeer IBAN/aliassen")
    elif match_status == "no_hint":
        add("Vul leveranciersnaam handmatig of voeg alias toe")
    elif match_status == "needs_review":
        add('Bevestig leverancier (contextmenu: "Bevestig factuur")')

    if load_error == "no_text":
        add("PDF heeft geen tekst — scan/OCR of brondocument aanpassen")

    amount_ok = amount_status in ("confirmed", "tentative")
    if can_offer_profile_learning(
        snap,
        source_file=_resolved_source_file_for_profile(snap, payment),
        amount_resolved=amount_ok,
    ):
        add(
            "Bevestig factuurgegevens en leer extractieprofiel "
            "(contextmenu of knop hieronder)"
        )

    return suggestions


def overlay_field_result(
    invoice_snapshot: dict,
    field_id: FieldId,
    field_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return snapshot copy with live ``*_result`` from UI (canonical over batch snap)."""
    out = copy.deepcopy(invoice_snapshot)
    if not isinstance(field_result, dict):
        return out
    result_key = _RESULT_KEY_BY_FIELD.get(field_id)
    if not result_key:
        return out
    out[result_key] = copy.deepcopy(field_result)
    legacy_key = _LEGACY_VALUE_KEY_BY_FIELD.get(field_id)
    if legacy_key and field_id in ("invoice_number", "customer_number", "iban"):
        if field_id == "customer_number":
            if infer_customer_number_mode_from_result(field_result) == CUSTOMER_NUMBER_MODE_NONE:
                out.pop(legacy_key, None)
            else:
                val = str(field_result.get("selected_value") or field_result.get("value") or "").strip()
                if val:
                    out[legacy_key] = val
                else:
                    out.pop(legacy_key, None)
        else:
            val = str(field_result.get("value") or "").strip()
            if val:
                out[legacy_key] = clean_iban(val) if field_id == "iban" else val
            elif field_id == "invoice_number":
                out.pop(legacy_key, None)
    return out


def overlay_iban_result(
    invoice_snapshot: dict,
    iban_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return snapshot copy with live iban_result (UI canonical IBAN cell)."""
    return overlay_field_result(invoice_snapshot, "iban", iban_result)


def overlay_amount_result(
    invoice_snapshot: dict,
    amount_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return snapshot copy with live amount_result (UI canonical amount cell)."""
    return overlay_field_result(invoice_snapshot, "amount", amount_result)


def build_invoice_diagnostics_snapshot(invoice: dict) -> dict:
    """Compacte, JSON-serialiseerbare subset voor opslag op tabelrij."""
    snap: dict[str, Any] = {}
    for key in _SNAPSHOT_FIELDS:
        if key not in invoice:
            continue
        if key in ("amount_result", "iban_result"):
            val = invoice.get(key)
            snap[key] = copy.deepcopy(val) if isinstance(val, dict) else val
        else:
            snap[key] = invoice[key]
    return snap


def build_diagnostics(
    invoice_snapshot: dict,
    *,
    payment: dict | None = None,
    decision: dict | None = None,
) -> dict:
    """Volledige diagnostics voor popup."""
    snap = invoice_snapshot
    pay = payment or {}
    dec = decision or pay.get("decision") or {}
    if not isinstance(dec, dict):
        dec = {}
    trace = pay.get("decision_trace") if isinstance(pay.get("decision_trace"), dict) else {}
    warning_raw = str(pay.get("warning") or "")
    warning_keys = _parse_warnings(warning_raw)

    source_file = snap.get("source_file")
    if source_file is not None:
        source_file = str(source_file).strip() or None

    load_error = snap.get("load_error")
    load_error_s = str(load_error).strip() if load_error else None

    match_status = str(snap.get("match_status") or "").strip()
    if not match_status:
        match_status = str(trace.get("supplier_match_status") or "").strip()

    supplier_name = str(snap.get("supplier_name") or "").strip() or None
    matched_by = _matched_by_from_snapshot(snap)
    match_info_flags = _match_info_flags_compact(snap.get("match_info"))

    supplier_needs = match_status in _SUPPLIER_NEEDS_ATTENTION

    ar_norm = _normalize_amount_result(
        snap.get("amount_result") if isinstance(snap.get("amount_result"), dict) else None
    )
    amount_status = ar_norm["status"]
    reason_code = str(dec.get("reason_code") or "").strip()
    decision_status = str(dec.get("status") or "").strip() or None

    iban_warnings_nl = [_diag_key("warning", k) for k in warning_keys if k == "iban_mismatch_supplier"]

    amount_block = build_amount_diag_block(
        snap,
        reason_code=reason_code,
        warning_keys=warning_keys,
    )
    amount_needs = amount_block["needs_attention"]

    inv_no = str(snap.get("invoice_number") or "").strip()
    if not inv_no and pay:
        inv_no = str(pay.get("invoice_number") or "").strip()
    inv_no_val = inv_no or None

    cust_auth = customer_number_authoritative_value(snap)
    cust_empty = customer_number_is_absent_or_none(snap) or not cust_auth
    cust_val = None if customer_number_is_absent_or_none(snap) else (cust_auth or None)

    iban_fallback = str(pay.get("iban") or "").strip() if pay else None
    iban_block = build_iban_diag_block(
        snap,
        payment_fallback=iban_fallback or None,
        reason_code=reason_code,
        warning_keys=warning_keys,
    )
    iban_block["warnings_nl"] = iban_warnings_nl
    iban_needs = bool(iban_block.get("needs_attention"))

    action_suggestions = _build_action_suggestions(
        snap,
        amount_status,
        match_status,
        warning_keys,
        cust_empty,
        load_error_s,
        payment=pay,
    )

    any_needs = (
        supplier_needs
        or amount_needs
        or cust_empty
        or iban_needs
    )

    is_error = bool(
        load_error_s
        or amount_status == "failed"
        or (
            decision_status == "excluded"
            and reason_code in _BLOCKING_EXCLUDED_REASON_CODES
        )
    )

    if is_error:
        overall_status = "error"
    elif any_needs:
        overall_status = "needs_review"
    else:
        overall_status = "ok"

    return {
        "header": {
            "supplier_display": _supplier_display(snap),
            "pdf_basename": _pdf_basename(snap, pay if pay else None),
            "source_file": source_file,
        },
        "supplier": {
            "status": match_status,
            "name": supplier_name,
            "matched_by": matched_by,
            "match_info_flags": match_info_flags,
            "needs_attention": supplier_needs,
            "status_nl": _diag_key("diag.match.status", match_status) if match_status else "",
            "detail_nl": _supplier_detail_nl(snap, match_status),
        },
        "amount": amount_block,
        "vat_number": build_ident_field_diag_block(snap, "vat_number"),
        "kvk_number": build_ident_field_diag_block(snap, "kvk_number"),
        "email_domain": build_ident_field_diag_block(snap, "email_domain"),
        "invoice_date": _invoice_date_block(snap),
        "invoice_number": build_ident_field_diag_block(
            snap,
            "invoice_number",
            payment_fallback=str(pay.get("invoice_number") or "").strip() or None if pay else None,
        ),
        "customer_number": build_ident_field_diag_block(snap, "customer_number"),
        "iban": iban_block,
        "general": {
            "load_error": load_error_s,
            "load_error_nl": _diag_key("diag.load.error", load_error_s) if load_error_s else None,
            "document_type": str(snap.get("type") or "").strip() or None,
            "document_type_source": _document_type_source(snap),
            "document_type_needs_review": _document_type_needs_review(snap),
            "invoice_profile_score": _document_type_profile_score(snap, "invoice_profile_score"),
            "credit_profile_score": _document_type_profile_score(snap, "credit_profile_score"),
            "can_set_document_type": _can_set_document_type(snap),
            "decision_status": decision_status,
            "decision_reason_code": reason_code or None,
            "decision_reason_nl": _diag_key("error.reason", reason_code) if reason_code else None,
            "decision_reason_detail": str(dec.get("reason_detail") or "").strip() or None,
        },
        "action_suggestions": action_suggestions,
        "overall_status": overall_status,
    }


def _invoice_date_block(snap: dict[str, Any]) -> dict[str, Any]:
    from logic.payment_dates import format_date_nl_from_iso

    block = build_ident_field_diag_block(snap, "invoice_date")
    iso = str(block.get("value") or "").strip()
    if iso:
        try:
            nl = format_date_nl_from_iso(iso)
        except Exception:
            nl = None
        if nl:
            block = dict(block)
            block["value_display"] = nl
    return block


def _document_type_resolution(snap: dict[str, Any]) -> dict[str, Any]:
    raw = snap.get("document_type_resolution")
    return raw if isinstance(raw, dict) else {}


def _document_type_source(snap: dict[str, Any]) -> str | None:
    resolution = _document_type_resolution(snap)
    source = str(resolution.get("source") or "").strip()
    if source:
        return source
    credit_detection = snap.get("credit_detection")
    if isinstance(credit_detection, dict):
        cd_source = str(credit_detection.get("type_source") or "").strip()
        if cd_source:
            return cd_source
    return None


def _document_type_needs_review(snap: dict[str, Any]) -> bool:
    resolution = _document_type_resolution(snap)
    if resolution.get("needs_review"):
        return True
    credit_detection = snap.get("credit_detection")
    if isinstance(credit_detection, dict) and credit_detection.get("needs_review"):
        return True
    return False


def _document_type_profile_score(snap: dict[str, Any], key: str) -> float | None:
    resolution = _document_type_resolution(snap)
    if key in resolution and resolution.get(key) is not None:
        try:
            return float(resolution[key])
        except (TypeError, ValueError):
            return None
    credit_detection = snap.get("credit_detection")
    if isinstance(credit_detection, dict) and credit_detection.get(key) is not None:
        try:
            return float(credit_detection[key])
        except (TypeError, ValueError):
            return None
    return None


def _can_set_document_type(snap: dict[str, Any]) -> bool:
    match_status = str(snap.get("match_status") or "").strip()
    if match_status not in ("confirmed", "needs_review"):
        return False
    doc_type = str(snap.get("type") or "").strip()
    if doc_type not in ("invoice", "credit_note"):
        return False
    if str(snap.get("supplier_key") or "").strip():
        return True
    return bool(str(snap.get("supplier_name") or "").strip())
