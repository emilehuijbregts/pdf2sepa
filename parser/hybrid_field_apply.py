"""Hybride veld-toepassing: generic primair, profile/db als kandidaten."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from logic.validation import clean_iban
from parser.field_adapters import field_result_from_legacy_dict, field_result_to_legacy_dict
from parser.field_model import FieldCandidate, FieldId
from parser.resolved_field_apply import apply_resolved_field_result
from parser.field_resolver import (
    db_master_confidence,
    profile_confidence_for_field,
    resolve_field,
)
from parser.profile_extractor import extract_with_profile, validate_profile, amount_field_spec_matches
from parser.supplier_db import SupplierDB, customer_number_mode_from_profile, CUSTOMER_NUMBER_MODE_NONE

_HYBRID_FIELD_IDS: tuple[FieldId, ...] = (
    "amount",
    "invoice_number",
    "customer_number",
    "iban",
    "vat_number",
    "kvk_number",
    "invoice_date",
    "email_domain",
)

_RESULT_KEY: dict[FieldId, str] = {
    "amount": "amount_result",
    "invoice_number": "invoice_number_result",
    "customer_number": "customer_number_result",
    "iban": "iban_result",
    "vat_number": "vat_number_result",
    "kvk_number": "kvk_number_result",
    "invoice_date": "invoice_date_result",
    "email_domain": "email_domain_result",
}


def _profile_candidate(
    field_id: FieldId,
    value: Any,
    *,
    validated: bool,
    context: str = "",
) -> FieldCandidate:
    meta: dict[str, Any] = {"profile_validated": bool(validated)}
    if field_id == "amount" and validated:
        # Resolver amount ranking is payable_score-first; validated profiles must beat generic incl.
        meta["payable_score"] = 100
        meta["type"] = "incl"
    return FieldCandidate(
        value=value,
        source="profile",
        confidence=profile_confidence_for_field(field_id, validated=validated),
        context=context,
        meta=meta,
    )


def _profile_raw_text(invoice: dict, invoice_copy: dict) -> str | None:
    """Profile specs are learned from strict PDF text; parsed ``raw_text`` may be shorter."""
    source_file = str(invoice.get("source_file") or invoice_copy.get("source_file") or "").strip()
    if source_file:
        try:
            from parser.pdf_parser import extract_text_strict

            strict = extract_text_strict(source_file)
            if strict:
                return strict
        except Exception:
            pass
    raw = invoice.get("raw_text") or invoice_copy.get("raw_text")
    if raw is None:
        return None
    text = str(raw)
    return text or None


def _amount_decimal(val: Any) -> Decimal | None:
    if val is None:
        return None
    try:
        from logic.payment_amounts import amount_to_decimal

        return amount_to_decimal(str(val))
    except (TypeError, ValueError):
        return None


def _generic_result_dict(invoice: dict, invoice_copy: dict, field_id: FieldId) -> dict[str, Any]:
    key = _RESULT_KEY[field_id]
    raw = invoice.get(key)
    if not isinstance(raw, dict):
        raw = {}
    merged = dict(raw)
    ic = invoice_copy.get(key)
    if isinstance(ic, dict):
        for flag in ("user_overridden", "previous_value", "user_selected", "override_reason"):
            if flag in ic:
                merged[flag] = ic[flag]
        if ic.get("decision_trace"):
            merged["decision_trace"] = ic["decision_trace"]
    return merged


def _build_db_override_candidates(
    field_id: FieldId,
    supplier: dict,
    invoice: dict,
    db: SupplierDB,
) -> list[FieldCandidate]:
    out: list[FieldCandidate] = []
    if field_id == "iban":
        sup_iban = clean_iban(str(supplier.get("iban") or ""))
        if sup_iban:
            out.append(
                FieldCandidate(
                    value=sup_iban,
                    source="db_master",
                    confidence=db_master_confidence("iban"),
                    context="Leveranciers-DB",
                )
            )
        return out

    if field_id == "customer_number":
        pdf_cc = str(invoice.get("customer_number") or "").strip()
        db_codes = supplier.get("customer_codes") or []
        if not db_codes:
            return out
        matched_code = None
        if pdf_cc:
            norm_pdf = db._normalize_customer_code(pdf_cc)
            for code in db_codes:
                if norm_pdf and db._normalize_customer_code(code) == norm_pdf:
                    matched_code = code
                    break
        db_cc = matched_code or db_codes[0]
        if db_cc:
            out.append(
                FieldCandidate(
                    value=str(db_cc).strip(),
                    source="db_master",
                    confidence=db_master_confidence("customer_number"),
                    context="Leveranciers-DB",
                )
            )
    return out


def apply_hybrid_field_extraction(
    invoice: dict,
    invoice_copy: dict,
    supplier: dict,
    db: SupplierDB,
    *,
    amount_status: str = "confirmed",
    use_profile: bool = True,
) -> None:
    """Pas hybride resolver toe op alle extractievelden."""
    profile = db.get_extraction_profile(supplier["name"]) if use_profile else None
    raw = _profile_raw_text(invoice, invoice_copy) if use_profile else (
        invoice.get("raw_text") or invoice_copy.get("raw_text")
    )
    extracted: dict[str, float | str | None] = {
        "amount": None,
        "invoice_number": None,
        "customer_number": None,
        "iban": None,
        "vat_number": None,
        "kvk_number": None,
        "invoice_date": None,
        "email_domain": None,
    }
    profile_validated = False
    if profile and raw:
        extracted = extract_with_profile(raw, profile)
        profile_validated = validate_profile(raw, profile)
    elif not use_profile:
        profile = None

    profile_fields: list[str] = []

    inv_iban = str(invoice.get("iban") or "").strip()
    if inv_iban:
        invoice_copy["pdf_iban"] = inv_iban

    pdf_cc = str(invoice.get("customer_number") or "").strip()
    if pdf_cc:
        invoice_copy["pdf_customer_number"] = pdf_cc

    amount_tentative = str(amount_status or "").strip().lower() == "tentative"

    for field_id in _HYBRID_FIELD_IDS:
        if (
            field_id == "customer_number"
            and profile
            and customer_number_mode_from_profile(profile) == CUSTOMER_NUMBER_MODE_NONE
        ):
            from parser.pdf_parser import build_absent_customer_number_snapshot

            apply_resolved_field_result(
                invoice_copy,
                field_id,
                build_absent_customer_number_snapshot(),
            )
            continue

        generic_dict = _generic_result_dict(invoice, invoice_copy, field_id)
        generic_fr = field_result_from_legacy_dict(generic_dict, field_id=field_id)

        overrides: list[FieldCandidate] = []
        overrides.extend(_build_db_override_candidates(field_id, supplier, invoice, db))

        prof_val = extracted.get(field_id)
        if prof_val is not None and profile and field_id in profile:
            if field_id == "amount":
                cand_val = _amount_decimal(prof_val)
            elif field_id == "iban":
                cand_val = clean_iban(str(prof_val)) or None
            else:
                cand_val = str(prof_val).strip()
            if cand_val is not None:
                field_spec = {field_id: profile[field_id]}
                field_spec_dict = profile[field_id]
                field_valid = profile_validated or validate_profile(
                    raw,
                    field_spec,
                    {field_id: prof_val},
                )
                if (
                    not field_valid
                    and field_id == "amount"
                    and raw
                    and isinstance(field_spec_dict, dict)
                ):
                    field_valid = amount_field_spec_matches(
                        (raw or "").split("\n"),
                        field_spec_dict,
                        prof_val,
                    )
                overrides.append(
                    _profile_candidate(
                        field_id,
                        cand_val,
                        validated=field_valid,
                        context=str(profile.get(field_id, {}).get("label") or ""),
                    )
                )

        user_pick: FieldCandidate | None = None
        if generic_fr.user_overridden and generic_fr.selected_value is not None:
            user_pick = FieldCandidate(
                value=generic_fr.selected_value,
                source=generic_fr.source or "USER_PICKED",
                confidence=100,
                context=str(generic_fr.context or ""),
            )

        resolved_fr = resolve_field(
            field_id,
            generic_fr,
            overrides,
            user_pick=user_pick,
            amount_profile_review_cap=field_id == "amount" and amount_tentative,
        )
        resolved_fr.resolver_finalized = True
        resolved_dict = field_result_to_legacy_dict(resolved_fr)

        apply_resolved_field_result(invoice_copy, field_id, resolved_dict)

        if str(resolved_dict.get("source") or "") == "profile":
            profile_fields.append(field_id)

    cc = str(invoice_copy.get("customer_number") or "").strip()
    inv_no = str(invoice_copy.get("invoice_number") or "").strip()
    if cc and inv_no:
        invoice_copy["description"] = f"{cc} / {inv_no}"

    sup_iban = clean_iban(str(supplier.get("iban") or ""))
    pdf_iban_clean = clean_iban(str(invoice_copy.get("pdf_iban") or inv_iban or ""))
    if pdf_iban_clean and sup_iban and pdf_iban_clean != sup_iban:
        invoice_copy["iban_mismatch"] = True

    invoice_copy["extraction_source"] = "profile" if profile_fields else "generic"
    invoice_copy["profile_fields"] = profile_fields


def apply_generic_field_resolution(
    invoice: dict,
    invoice_copy: dict,
    *,
    preserve_generic_outcome: bool = False,
    preserve_null_scalars: bool = False,
) -> None:
    """Route parser-only fields through the resolver with empty overrides.

    Used for invoices without a supplier match: there is no DB/profile input, but
    parser-produced winners should still pass through the same ``resolve_field``
    selection path as matched invoices.
    """
    for field_id in _HYBRID_FIELD_IDS:
        key = _RESULT_KEY[field_id]
        if key not in invoice and key not in invoice_copy:
            continue

        generic_dict = _generic_result_dict(invoice, invoice_copy, field_id)
        generic_fr = field_result_from_legacy_dict(generic_dict, field_id=field_id)
        if generic_fr.selected_value is not None:
            winner_meta: dict[str, Any] = {}
            for cand in generic_fr.candidates:
                if str(cand.value) == str(generic_fr.selected_value) and str(
                    cand.source or ""
                ).casefold() == str(generic_fr.source or "").casefold():
                    winner_meta = dict(cand.meta or {})
                    break
            if field_id == "amount":
                same_value_scores: list[int] = []
                for cand in generic_fr.candidates:
                    if str(cand.value) != str(generic_fr.selected_value):
                        continue
                    try:
                        same_value_scores.append(int((cand.meta or {}).get("payable_score") or 0))
                    except (TypeError, ValueError):
                        same_value_scores.append(0)
                if same_value_scores:
                    winner_meta = {
                        **winner_meta,
                        "payable_score": max(same_value_scores),
                    }
            generic_fr.candidates = [
                FieldCandidate(
                    value=generic_fr.selected_value,
                    source=generic_fr.source,
                    confidence=int(generic_fr.confidence or 0),
                    context=str(generic_fr.context or ""),
                    meta=winner_meta,
                )
            ] + list(generic_fr.candidates)

        user_pick: FieldCandidate | None = None
        if generic_fr.user_overridden and generic_fr.selected_value is not None:
            user_pick = FieldCandidate(
                value=generic_fr.selected_value,
                source=generic_fr.source or "USER_PICKED",
                confidence=100,
                context=str(generic_fr.context or ""),
            )

        resolved_fr = resolve_field(field_id, generic_fr, [], user_pick=user_pick)
        resolved_dict: dict[str, Any]
        if preserve_generic_outcome:
            generic_fr.decision_trace = list(resolved_fr.decision_trace or [])
            generic_fr.override_reason = resolved_fr.override_reason
            generic_fr.resolver_finalized = True
            resolved_dict = {
                **generic_dict,
                "decision_trace": list(resolved_fr.decision_trace or []),
                "override_reason": str(resolved_fr.override_reason or ""),
                "resolver_finalized": True,
            }
        else:
            resolved_fr.resolver_finalized = True
            resolved_dict = field_result_to_legacy_dict(resolved_fr)
        apply_resolved_field_result(
            invoice_copy,
            field_id,
            resolved_dict,
            preserve_null_scalar=preserve_null_scalars,
        )
