"""Universele diagnostics-mapping voor veldkandidaten."""

from __future__ import annotations

from typing import Any

from logic.payment_amounts import amount_to_decimal, format_eur_xml
from logic.validation import mask_iban_for_log
from parser.field_adapters import (
    field_candidate_from_amount_dict,
    field_candidate_from_ident_dict,
    field_result_from_amount,
    field_result_from_iban,
    field_result_from_ident,
    normalize_amount_result_dict,
)
from parser.field_model import FieldCandidate, FieldId

_CONTEXT_PREVIEW_MAX = 80

_AMOUNT_CONFLICT_SOURCES = frozenset(
    {"INCL_CONFLICT", "GENERIC_TOTAL_CONFLICT", "CONFLICTING_HIGH_CONFIDENCE", "LOAD_FAILED"}
)

_AMOUNT_WARNING_KEYS = frozenset(
    {
        "amount_low_confidence",
        "amount_tentative",
        "amount_ambiguous",
        "amount_uncertain",
    }
)

_AMOUNT_REASON_CODES = frozenset(
    {
        "missing_amount",
        "amount_ambiguous",
        "amount_uncertain",
        "amount_failed",
        "amount_low_confidence",
    }
)

_AMOUNT_NEEDS_ATTENTION = frozenset({"tentative", "ambiguous", "failed"})

_OVERRIDE_REASON_NL: dict[str, str] = {
    "user_locked": "Handmatig vergrendeld door gebruiker",
    "generic_strong": "Generieke parser sterk genoeg — profiel niet toegepast",
    "generic_only": "Alleen generieke extractie beschikbaar",
    "profile_fills_gap": "Profiel vult ontbrekende of zwakke generieke waarde aan",
    "profile_higher_confidence": "Profiel heeft hogere confidence dan generiek",
    "generic_preferred": "Generieke waarde heeft voorrang (confidence dichtbij)",
    "db_master_conflict": "Leveranciers-DB heeft voorrang bij afwijkende waarde",
}

_IBAN_SOURCE_NL: dict[str, str] = {
    "pdf_text": "PDF-tekst",
    "ocr": "OCR",
    "USER_PICKED": "Handmatige keuze",
    "resolved": "Gekozen waarde",
    "AMBIGUOUS": "Meerdere kandidaten",
    "NOT_FOUND": "Niet gevonden",
}


def _nl(code: str, mapping: dict[str, str]) -> str:
    s = str(code or "").strip()
    if not s:
        return ""
    return mapping.get(s, s)


def _format_amount_display(raw: object | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        formatted = format_eur_xml(amount_to_decimal(s)).replace(".", ",")
        return f"€ {formatted}"
    except ValueError:
        return None


def _context_preview(ctx: str) -> str | None:
    if not ctx:
        return None
    if len(ctx) > _CONTEXT_PREVIEW_MAX:
        return ctx[:_CONTEXT_PREVIEW_MAX] + "…"
    return ctx[:_CONTEXT_PREVIEW_MAX]


def _hybrid_override_meta(result_dict: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result_dict, dict):
        return {}
    out: dict[str, Any] = {}
    reason = str(result_dict.get("override_reason") or "").strip()
    if reason:
        out["override_reason"] = reason
        out["override_reason_nl"] = _OVERRIDE_REASON_NL.get(reason, reason)
    trace = result_dict.get("decision_trace")
    if isinstance(trace, list) and trace:
        out["decision_trace"] = trace
    if result_dict.get("user_overridden"):
        out["user_overridden"] = True
    prev = result_dict.get("previous_value")
    if prev is not None and str(prev).strip():
        out["previous_value"] = str(prev)
    return out


def map_field_candidate_for_diag(
    cand: FieldCandidate | dict[str, Any],
    *,
    field_id: FieldId,
    source_nl_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    if isinstance(cand, dict):
        if field_id == "amount":
            fc = field_candidate_from_amount_dict(cand)
        else:
            fc = field_candidate_from_ident_dict(cand)
    else:
        fc = cand

    raw_val = fc.value
    val_str = str(raw_val) if raw_val is not None else ""
    src = str(fc.source or "").strip()
    preview = _context_preview(str(fc.context or ""))
    conf = int(fc.confidence or 0)

    if field_id == "amount":
        return {
            "value": val_str,
            "value_display": _format_amount_display(raw_val),
            "source": src,
            "source_nl": _nl(src, source_nl_map or {}),
            "confidence": conf,
            "type": str(fc.meta.get("type") or "unknown"),
            "context_preview": preview,
        }

    if field_id == "iban":
        nl_map = source_nl_map or _IBAN_SOURCE_NL
        return {
            "value": val_str,
            "value_display": mask_iban_for_log(val_str) if val_str else val_str,
            "source": src,
            "source_nl": _nl(src, nl_map),
            "confidence": conf,
            "label": str(fc.label or "").strip() or None,
            "context_preview": preview,
        }

    return {
        "value": val_str,
        "value_display": val_str,
        "source": src,
        "source_nl": src,
        "confidence": conf,
        "label": str(fc.label or "").strip() or None,
        "context_preview": preview,
    }


def build_ident_field_diag_block(
    snap: dict[str, Any],
    field: str,
    *,
    payment_fallback: str | None = None,
) -> dict[str, Any]:
    """Diagnostics-weergave: ``snap[field]`` (profiel/tabel) gaat vóór verouderde ``*_result``."""
    legacy = str(snap.get(field) or "").strip() or None
    if not legacy and payment_fallback:
        legacy = str(payment_fallback).strip() or None
    extraction_source = str(snap.get("extraction_source") or "").strip().lower()
    profile_fields = snap.get("profile_fields")
    from_profile = extraction_source == "profile" or (
        isinstance(profile_fields, list) and field in profile_fields
    )

    fr_raw = snap.get(f"{field}_result")
    if not isinstance(fr_raw, dict):
        return {
            "value": legacy,
            "needs_attention": not legacy,
            "status_nl": "Via extractieprofiel" if legacy and from_profile else (
                "Aanwezig" if legacy else "Ontbreekt"
            ),
            "candidates": [],
            "resolved_source": "profile" if from_profile else None,
        }

    field_id: FieldId = (
        "invoice_number" if field == "invoice_number" else "customer_number"
    )
    fr = field_result_from_ident(fr_raw, field_id=field_id)
    st = fr.status
    absence_state = str(fr_raw.get("absence_state") or "").strip()
    cands_out: list[dict[str, Any]] = [
        map_field_candidate_for_diag(c, field_id=field_id) for c in fr.candidates
    ]

    if (
        field == "customer_number"
        and absence_state == "NOT_PRESENT_SUPPLIER_LEVEL"
        and fr_raw.get("user_selected")
        and not str(fr_raw.get("value") or legacy or "").strip()
    ):
        return {
            "value": None,
            "value_display": "Geen klantnummer",
            "status": "confirmed",
            "needs_attention": False,
            "status_nl": "Geen klantnummer (handmatig gekozen)",
            "candidates": cands_out,
            "resolved_source": str(fr_raw.get("source") or "USER_ABSENT_CUSTOMER"),
            **_hybrid_override_meta(fr_raw),
        }

    val = legacy or (str(fr.selected_value).strip() if fr.selected_value else None) or None
    if val and legacy and str(fr_raw.get("value") or "").strip() not in ("", val):
        st = "confirmed"

    if val:
        if from_profile:
            cands_out = [
                {
                    "value": val,
                    "value_display": val,
                    "source": "profile",
                    "source_nl": "Extractieprofiel",
                    "confidence": 95,
                    "label": None,
                    "context_preview": None,
                    "is_resolved": True,
                }
            ]
            st = "confirmed"
        else:
            matching = [c for c in cands_out if str(c.get("value") or "").strip() == val]
            if not matching or not cands_out:
                cands_out = [
                    {
                        "value": val,
                        "value_display": val,
                        "source": "resolved",
                        "source_nl": "Gekozen waarde",
                        "confidence": 95,
                        "label": None,
                        "context_preview": None,
                        "is_resolved": True,
                    },
                    *[
                        c
                        for c in cands_out
                        if str(c.get("value") or "").strip() != val
                    ],
                ]
                if st in ("confirmed", "tentative", "failed", "ambiguous", ""):
                    st = "confirmed"
            else:
                for c in cands_out:
                    c["is_resolved"] = str(c.get("value") or "").strip() == val
    elif st == "confirmed" and fr.selected_value:
        val = str(fr.selected_value).strip() or None

    needs = (
        not val
        and st in ("ambiguous", "tentative", "failed")
        and bool(cands_out)
    ) or (st in ("ambiguous", "tentative") and val and len(cands_out) > 1)

    if from_profile and val:
        status_nl = "Via extractieprofiel"
    elif st == "confirmed" and val:
        status_nl = "Aanwezig"
    elif st == "ambiguous":
        status_nl = "Meerdere kandidaten — kies in tabel"
    elif st == "tentative":
        status_nl = "Twijfelachtig — controleer"
    elif val:
        status_nl = "Aanwezig"
    else:
        status_nl = "Ontbreekt"

    return {
        "value": val,
        "status": st or None,
        "needs_attention": needs,
        "status_nl": status_nl,
        "candidates": cands_out,
        "resolved_source": "profile" if from_profile and val else None,
        **_hybrid_override_meta(fr_raw if isinstance(fr_raw, dict) else None),
    }


def amount_needs_attention(
    status: str,
    reason_code: str,
    warning_keys: list[str],
) -> bool:
    if status in _AMOUNT_NEEDS_ATTENTION:
        return True
    if reason_code in _AMOUNT_REASON_CODES:
        return True
    return bool(_AMOUNT_WARNING_KEYS.intersection(warning_keys))


def build_amount_diag_block(
    snap: dict[str, Any],
    *,
    reason_code: str,
    warning_keys: list[str],
    error_reason_nl: dict[str, str],
    warning_nl: dict[str, str],
    amount_status_nl: dict[str, str],
    amount_source_nl: dict[str, str],
) -> dict[str, Any]:
    ar_raw = snap.get("amount_result") if isinstance(snap.get("amount_result"), dict) else None
    ar_norm = normalize_amount_result_dict(ar_raw)
    amount_status = ar_norm["status"]
    amount_source = ar_norm["source"]

    engine_reason_code: str | None = None
    engine_reason_nl: str | None = None
    if reason_code in _AMOUNT_REASON_CODES:
        engine_reason_code = reason_code
        engine_reason_nl = _nl(reason_code, error_reason_nl)

    amount_needs = amount_needs_attention(amount_status, reason_code, warning_keys)
    amount_warnings_nl = [
        _nl(k, warning_nl) for k in warning_keys if k in _AMOUNT_WARNING_KEYS
    ]

    detail_nl: str | None = None
    if amount_source in _AMOUNT_CONFLICT_SOURCES:
        detail_nl = _nl(amount_source, amount_source_nl)

    fr = field_result_from_amount(ar_raw or {})
    candidates_out = [
        map_field_candidate_for_diag(c, field_id="amount", source_nl_map=amount_source_nl)
        for c in fr.candidates
    ]
    if not candidates_out:
        for c in ar_norm.get("candidates") or []:
            if isinstance(c, dict):
                candidates_out.append(
                    map_field_candidate_for_diag(
                        c, field_id="amount", source_nl_map=amount_source_nl
                    )
                )

    return {
        "status": amount_status,
        "value": ar_norm["value"],
        "value_display": _format_amount_display(ar_norm["value"]),
        "confidence": ar_norm["confidence"],
        "source": amount_source,
        "candidates": candidates_out,
        "needs_attention": amount_needs,
        "status_nl": _nl(amount_status, amount_status_nl),
        "detail_nl": detail_nl,
        "engine_reason_code": engine_reason_code,
        "engine_reason_nl": engine_reason_nl,
        "warnings_nl": amount_warnings_nl,
        **_hybrid_override_meta(ar_raw),
    }


def build_iban_diag_block(
    snap: dict[str, Any],
    *,
    payment_fallback: str | None = None,
    reason_code: str = "",
    warning_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Diagnostics voor IBAN: ``iban_result`` + legacy ``iban`` / ``all_ibans``."""
    warning_keys = warning_keys or []
    legacy = str(snap.get("iban") or "").strip() or None
    if not legacy and payment_fallback:
        legacy = str(payment_fallback).strip() or None

    iban_mismatch = bool(snap.get("iban_mismatch"))
    ocr_attempted = bool(snap.get("ocr_iban_attempted"))
    ocr_error = snap.get("ocr_iban_error")
    ocr_error_s = str(ocr_error).strip() if ocr_error else None

    fr_raw = snap.get("iban_result")
    if not isinstance(fr_raw, dict):
        all_ibans = snap.get("all_ibans")
        iban_list: list[str] = []
        if isinstance(all_ibans, list):
            for x in all_ibans:
                s = str(x or "").strip()
                if s:
                    iban_list.append(s)
        if legacy and legacy not in iban_list:
            iban_list.insert(0, legacy)
        elif legacy and not iban_list:
            iban_list = [legacy]

        cands_out = [
            {
                # Keep raw value for click/apply pipelines; mask only for display.
                "value": x,
                "value_display": mask_iban_for_log(x),
                "source": "ocr" if ocr_attempted and iban_list and x == iban_list[0] else "pdf_text",
                "source_nl": "OCR" if ocr_attempted and iban_list and x == iban_list[0] else "PDF-tekst",
                "confidence": 95 if x == legacy else 80,
                "is_resolved": x == legacy,
            }
            for x in iban_list
        ]
        iban_needs = (
            iban_mismatch
            or "iban_mismatch_supplier" in warning_keys
            or reason_code in {"missing_iban", "invalid_iban"}
            or not legacy
        )
        status_nl = "IBAN aanwezig" if legacy else "IBAN ontbreekt"
        if iban_mismatch or "iban_mismatch_supplier" in warning_keys:
            status_nl = "IBAN komt niet overeen met leverancier"
        return {
            "masked_value": mask_iban_for_log(legacy) if legacy else "<none>",
            "all_ibans_masked": [mask_iban_for_log(x) for x in iban_list],
            "value": legacy,
            "status": "confirmed" if legacy else "failed",
            "candidates": cands_out,
            "mismatch": iban_mismatch,
            "ocr_attempted": ocr_attempted,
            "ocr_error": ocr_error_s,
            "needs_attention": iban_needs,
            "status_nl": status_nl,
            "warnings_nl": [],
        }

    fr = field_result_from_iban(fr_raw)
    st = fr.status
    cands_out: list[dict[str, Any]] = [
        map_field_candidate_for_diag(c, field_id="iban") for c in fr.candidates
    ]
    for c in cands_out:
        raw = str(c.get("value") or "").strip()
        if raw:
            c["value"] = raw
            c["value_display"] = mask_iban_for_log(raw)

    val = legacy or (str(fr.selected_value).strip() if fr.selected_value else None) or None
    if val and legacy and str(fr_raw.get("value") or "").strip() not in ("", val):
        st = "confirmed"

    if val:
        matching = [c for c in cands_out if str(c.get("value") or "").strip() == str(val)]
        if not matching or not cands_out:
            cands_out = [
                {
                    "value": val,
                    "value_display": mask_iban_for_log(val),
                    "source": "resolved",
                    "source_nl": "Gekozen waarde",
                    "confidence": 95,
                    "label": None,
                    "context_preview": None,
                    "is_resolved": True,
                },
                *[
                    c
                    for c in cands_out
                    if str(c.get("value") or "").strip() != str(val)
                ],
            ]
            if st in ("confirmed", "tentative", "failed", "ambiguous", ""):
                st = "confirmed"
        else:
            for c in cands_out:
                c["is_resolved"] = str(c.get("value") or "").strip() == str(val)
    elif st == "confirmed" and fr.selected_value:
        val = str(fr.selected_value).strip() or None

    needs = (
        iban_mismatch
        or "iban_mismatch_supplier" in warning_keys
        or reason_code in {"missing_iban", "invalid_iban"}
        or (
            not val
            and st in ("ambiguous", "tentative", "failed")
            and bool(cands_out)
        )
        or (st in ("ambiguous", "tentative") and val and len(cands_out) > 1)
        or not val
    )

    if st == "confirmed" and val:
        status_nl = "IBAN aanwezig"
    elif st == "ambiguous":
        status_nl = "Meerdere IBAN's — kies in tabel"
    elif st == "tentative":
        status_nl = "Twijfelachtig — controleer IBAN"
    elif val:
        status_nl = "IBAN aanwezig"
    else:
        status_nl = "IBAN ontbreekt"
    if iban_mismatch or "iban_mismatch_supplier" in warning_keys:
        status_nl = "IBAN komt niet overeen met leverancier"

    all_masked = [str(c.get("value_display") or c.get("value") or "") for c in cands_out if c.get("value")]

    return {
        "masked_value": mask_iban_for_log(val) if val else "<none>",
        "all_ibans_masked": all_masked,
        "value": val,
        "status": st or None,
        "candidates": cands_out,
        "mismatch": iban_mismatch,
        "ocr_attempted": ocr_attempted,
        "ocr_error": ocr_error_s,
        "needs_attention": needs,
        "status_nl": status_nl,
        "warnings_nl": [],
        **_hybrid_override_meta(fr_raw if isinstance(fr_raw, dict) else None),
    }
