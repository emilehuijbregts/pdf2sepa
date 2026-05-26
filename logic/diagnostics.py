"""Read-only diagnostics mapper: invoice snapshot + payment/decision → structured UI dict."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from logic.payment_amounts import amount_to_decimal, format_eur_xml
from logic.payment_amounts import amount_to_decimal, resolved_payment_amount_for_export
from logic.profile_learning import can_offer_profile_learning
from logic.validation import mask_iban_for_log

# --- NL label maps (single source for diagnostics popup; UI may import these) ---

AMOUNT_STATUS_NL: dict[str, str] = {
    "confirmed": "Bedrag gevonden met hoge zekerheid",
    "tentative": "Bedrag gevonden maar lage zekerheid — controleer",
    "ambiguous": "Meerdere bedragen gevonden, geen winnaar — kies zelf",
    "failed": "Geen enkel bedrag gevonden in de PDF-tekst",
}

AMOUNT_SOURCE_NL: dict[str, str] = {
    "total_label_payable": "Totaal te betalen",
    "total_label_invoice": "Factuurbedrag",
    "total_label_generic": "Totaal",
    "total_label_excl": "Totaal excl. BTW",
    "total_line_hint": "Totaalregel (fallback)",
    "total_label_sum": "Label: totaal",
    "vat_summary": "BTW-overzicht (tabel/regel)",
    "fallback_last_token": "Laatste bedrag in document (onzeker)",
    "INCL_CONFLICT": 'Meerdere "incl. BTW"-bedragen gevonden die niet overeenkomen',
    "GENERIC_TOTAL_CONFLICT": "Gevonden totaal lijkt te laag vergeleken met andere bedragen",
    "CONFLICTING_HIGH_CONFIDENCE": "Conflicterende totalen met hoge zekerheid",
    "LOAD_FAILED": "PDF niet geladen — geen bedragextractie",
}

MATCH_STATUS_NL: dict[str, str] = {
    "confirmed": "Leverancier zeker herkend (≥2 kernkenmerken of equivalente regel)",
    "needs_review": "Leverancier gedeeltelijk herkend — controleer",
    "unmatched": "Leverancier niet gevonden in database",
    "no_hint": "Geen leveranciersnaam gevonden in PDF",
    "new": "Nieuwe leverancier (lege DB + plausibel IBAN)",
    "load_failed": "PDF niet geladen — matching overgeslagen",
}

LOAD_ERROR_NL: dict[str, str] = {
    "read_failed": "PDF kon niet worden geopend of gelezen",
    "no_text": "PDF heeft geen tekstlaag (mogelijk gescand)",
}

ERROR_REASON_NL: dict[str, str] = {
    "no_supplier_hint": "Geen leveranciersnaam herkend in PDF; voeg een alias toe of vul handmatig in.",
    "unmatched_supplier": "Leverancier niet gevonden in database; controleer IBAN of aliassen.",
    "needs_review": "Slechts 1 kenmerk gevonden; bevestig de leverancier handmatig.",
    "missing_supplier_name": "Interne fout: leveranciersnaam ontbreekt.",
    "missing_amount": "Bedrag ontbreekt of niet leesbaar in PDF.",
    "amount_ambiguous": "Meerdere bedragen gevonden — selecteer het juiste bedrag.",
    "amount_uncertain": "Bedrag niet met voldoende zekerheid uit de PDF af te leiden — controleer het totaal of vul handmatig in.",
    "amount_failed": "Bedragextractie is mislukt; controleer de factuur handmatig.",
    "credit_note_only": "Alleen creditnota's zonder bijbehorende factuur.",
    "credit_exceeds_available_invoices": "Creditnota past niet bij beschikbare factuurbedragen.",
    "credit_exceeds_invoice_total": "Creditnota's overschrijden het factuurbedrag.",
    "zero_amount": "Te betalen bedrag is nul na korting/credit.",
    "negative_amount": "Te betalen bedrag is negatief.",
    "missing_iban": "IBAN ontbreekt in PDF of niet ingevuld.",
    "invalid_iban": "IBAN is ongeldig.",
    "pdf_read_failed": "PDF kon niet worden gelezen (bestand beschadigd, versleuteld of geen geldige PDF).",
    "pdf_no_text": "PDF bevat geen uitleesbare tekst (vaak een scan); los dit op in de brondocumenten of voeg tekst toe.",
}

WARNING_NL: dict[str, str] = {
    "no_excl_vat_amount_discount_skipped": "Geen bedrag excl. BTW; korting niet toegepast.",
    "iban_mismatch_supplier": "IBAN komt niet overeen met bekende leverancier — controleer naam en rekening.",
    "supplier_term_not_applied": "Leverancier niet automatisch bevestigd → betaaltermijn niet toegepast.",
    "missing_invoice_date": "Factuurdatum onbekend; vul handmatig in voor 'op uiterste datum'.",
    "amount_low_confidence": "Bedrag is onduidelijk (mogelijk verkeerd) — controleer de factuur.",
    "amount_tentative": "Voorlopig bedrag (hoogste betrouwbaarheid) — controleer vóór betaling.",
    "amount_ambiguous": "Meerdere bedragen gevonden — selecteer het juiste bedrag.",
    "amount_uncertain": "Bedrag niet met voldoende zekerheid uit de PDF af te leiden — controleer het totaal of vul handmatig in.",
}

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

_IBAN_REASON_CODES = frozenset({"missing_iban", "invalid_iban"})

_SUPPLIER_NEEDS_ATTENTION = frozenset(
    {"needs_review", "unmatched", "no_hint", "new", "load_failed"}
)

_AMOUNT_NEEDS_ATTENTION = frozenset({"tentative", "ambiguous", "failed"})

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
    "supplier_hint",
    "match_status",
    "supplier_match_source",
    "match_info",
    "db_core_matches",
    "db_core_match_count",
    "match_signals",
    "iban",
    "all_ibans",
    "iban_mismatch",
    "ocr_iban_attempted",
    "ocr_iban_error",
    "amount_result",
    "invoice_number_result",
    "customer_number_result",
    "invoice_number",
    "customer_number",
    "invoice_date_source",
    "type",
    "extraction_source",
    "profile_fields",
    "pdf_customer_number",
)

_CONTEXT_PREVIEW_MAX = 80


def _nl(code: str, mapping: dict[str, str]) -> str:
    s = str(code or "").strip()
    if not s:
        return ""
    return mapping.get(s, s)


def _parse_warnings(pipe_str: str) -> list[str]:
    return [p.strip() for p in str(pipe_str or "").split("|") if p.strip()]


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


def _normalize_amount_result(ar: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ar, dict):
        return {
            "status": "failed",
            "source": "",
            "value": None,
            "confidence": 0,
            "candidates": [],
        }
    status = str(ar.get("status") or ar.get("amount_status") or "failed").strip() or "failed"
    source = str(ar.get("source") or "").strip()
    val = ar.get("value")
    if val is None:
        val = ar.get("selected_amount")
    conf = ar.get("confidence")
    if conf is None:
        conf = ar.get("amount_confidence")
    try:
        confidence = int(conf) if conf is not None else 0
    except (TypeError, ValueError):
        confidence = 0
    cands = ar.get("candidates")
    if not isinstance(cands, list):
        cands = []
    return {
        "status": status,
        "source": source,
        "value": str(val) if val is not None else None,
        "confidence": confidence,
        "candidates": cands,
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
        labels.append("IBAN")
    if match_info.get("customer_code_match"):
        labels.append("Klantnummer")
    if match_info.get("kvk_match"):
        labels.append("KvK")
    if match_info.get("vat_match"):
        labels.append("BTW")
    if match_info.get("email_domain_match"):
        labels.append("E-maildomein")
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


def _map_ident_candidate(cand: dict[str, Any]) -> dict[str, Any]:
    val_str = str(cand.get("value") or "").strip()
    src = str(cand.get("source") or "").strip()
    ctx = str(cand.get("context") or "")
    preview = ctx[:_CONTEXT_PREVIEW_MAX] if ctx else None
    if ctx and len(ctx) > _CONTEXT_PREVIEW_MAX:
        preview = ctx[:_CONTEXT_PREVIEW_MAX] + "…"
    try:
        conf = int(cand.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0
    return {
        "value": val_str,
        "value_display": val_str,
        "source": src,
        "source_nl": src,
        "confidence": conf,
        "label": str(cand.get("label") or "").strip() or None,
        "context_preview": preview,
    }


def _ident_field_diag_block(
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

    fr = snap.get(f"{field}_result")
    if not isinstance(fr, dict):
        return {
            "value": legacy,
            "needs_attention": not legacy,
            "status_nl": "Via extractieprofiel" if legacy and from_profile else (
                "Aanwezig" if legacy else "Ontbreekt"
            ),
            "candidates": [],
            "resolved_source": "profile" if from_profile else None,
        }

    st = str(fr.get("status") or "").strip().lower()
    cands_out: list[dict[str, Any]] = []
    for c in fr.get("candidates") or []:
        if isinstance(c, dict):
            cands_out.append(_map_ident_candidate(c))

    # Waarde in tabel/omschrijving (legacy) is leidend — niet oude result.value.
    val = legacy or str(fr.get("value") or "").strip() or None
    if val and legacy and str(fr.get("value") or "").strip() not in ("", val):
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
    elif st == "confirmed" and fr.get("value"):
        val = str(fr.get("value") or "").strip() or None

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
    }


def _map_candidate(cand: dict[str, Any]) -> dict[str, Any]:
    raw_val = cand.get("value")
    val_str = str(raw_val) if raw_val is not None else ""
    src = str(cand.get("source") or "").strip()
    ctx = str(cand.get("context") or "")
    preview = ctx[:_CONTEXT_PREVIEW_MAX] if ctx else None
    if ctx and len(ctx) > _CONTEXT_PREVIEW_MAX:
        preview = ctx[:_CONTEXT_PREVIEW_MAX] + "…"
    try:
        conf = int(cand.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0
    return {
        "value": val_str,
        "value_display": _format_amount_display(raw_val),
        "source": src,
        "source_nl": _nl(src, AMOUNT_SOURCE_NL),
        "confidence": conf,
        "type": str(cand.get("type") or "unknown"),
        "context_preview": preview,
    }


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


def _amount_needs_attention(
    status: str,
    reason_code: str,
    warning_keys: list[str],
) -> bool:
    if status in _AMOUNT_NEEDS_ATTENTION:
        return True
    if reason_code in _AMOUNT_REASON_CODES:
        return True
    return bool(_AMOUNT_WARNING_KEYS.intersection(warning_keys))


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


def build_invoice_diagnostics_snapshot(invoice: dict) -> dict:
    """Compacte, JSON-serialiseerbare subset voor opslag op tabelrij."""
    snap: dict[str, Any] = {}
    for key in _SNAPSHOT_FIELDS:
        if key not in invoice:
            continue
        if key == "amount_result":
            ar = invoice.get("amount_result")
            snap[key] = copy.deepcopy(ar) if isinstance(ar, dict) else ar
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
    amount_source = ar_norm["source"]
    reason_code = str(dec.get("reason_code") or "").strip()
    decision_status = str(dec.get("status") or "").strip() or None

    amount_warnings_nl = [_nl(k, WARNING_NL) for k in warning_keys if k in _AMOUNT_WARNING_KEYS]
    iban_warnings_nl = [_nl(k, WARNING_NL) for k in warning_keys if k == "iban_mismatch_supplier"]

    engine_reason_code: str | None = None
    engine_reason_nl: str | None = None
    if reason_code in _AMOUNT_REASON_CODES:
        engine_reason_code = reason_code
        engine_reason_nl = _nl(reason_code, ERROR_REASON_NL)

    amount_needs = _amount_needs_attention(amount_status, reason_code, warning_keys)

    detail_nl: str | None = None
    if amount_source in _AMOUNT_CONFLICT_SOURCES:
        detail_nl = _nl(amount_source, AMOUNT_SOURCE_NL)

    candidates_out = []
    for c in ar_norm["candidates"]:
        if isinstance(c, dict):
            candidates_out.append(_map_candidate(c))

    inv_no = str(snap.get("invoice_number") or "").strip()
    if not inv_no and pay:
        inv_no = str(pay.get("invoice_number") or "").strip()
    inv_no_val = inv_no or None

    cust_no = str(snap.get("customer_number") or "").strip()
    cust_empty = not cust_no
    cust_val = cust_no or None

    iban_raw = str(snap.get("iban") or "").strip()
    all_ibans = snap.get("all_ibans")
    iban_list: list[str] = []
    if isinstance(all_ibans, list):
        for x in all_ibans:
            s = str(x or "").strip()
            if s:
                iban_list.append(s)
    if iban_raw and iban_raw not in iban_list:
        iban_list.insert(0, iban_raw)
    elif iban_raw and not iban_list:
        iban_list = [iban_raw]

    iban_mismatch = bool(snap.get("iban_mismatch"))
    iban_needs = (
        iban_mismatch
        or "iban_mismatch_supplier" in warning_keys
        or reason_code in _IBAN_REASON_CODES
        or not iban_raw
    )

    ocr_attempted = bool(snap.get("ocr_iban_attempted"))
    ocr_error = snap.get("ocr_iban_error")
    ocr_error_s = str(ocr_error).strip() if ocr_error else None

    iban_status_nl = "IBAN aanwezig" if iban_raw else "IBAN ontbreekt"
    if iban_mismatch or "iban_mismatch_supplier" in warning_keys:
        iban_status_nl = "IBAN komt niet overeen met leverancier"

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
            "status_nl": _nl(match_status, MATCH_STATUS_NL) if match_status else "",
            "detail_nl": _supplier_detail_nl(snap, match_status),
        },
        "amount": {
            "status": amount_status,
            "value": ar_norm["value"],
            "value_display": _format_amount_display(ar_norm["value"]),
            "confidence": ar_norm["confidence"],
            "source": amount_source,
            "candidates": candidates_out,
            "needs_attention": amount_needs,
            "status_nl": _nl(amount_status, AMOUNT_STATUS_NL),
            "detail_nl": detail_nl,
            "engine_reason_code": engine_reason_code,
            "engine_reason_nl": engine_reason_nl,
            "warnings_nl": amount_warnings_nl,
        },
        "invoice_number": _ident_field_diag_block(
            snap,
            "invoice_number",
            payment_fallback=str(pay.get("invoice_number") or "").strip() or None if pay else None,
        ),
        "customer_number": _ident_field_diag_block(snap, "customer_number"),
        "iban": {
            "masked_value": mask_iban_for_log(iban_raw) if iban_raw else "<none>",
            "all_ibans_masked": [mask_iban_for_log(x) for x in iban_list],
            "candidates": [
                {
                    "value": mask_iban_for_log(x),
                    "value_display": mask_iban_for_log(x),
                    "source": "ocr" if ocr_attempted and x == iban_list[0] else "pdf_text",
                    "source_nl": "OCR" if ocr_attempted and x == iban_list[0] else "PDF-tekst",
                    "confidence": 95 if x == iban_raw else 80,
                    "is_resolved": x == iban_raw,
                }
                for x in iban_list
            ],
            "mismatch": iban_mismatch,
            "ocr_attempted": ocr_attempted,
            "ocr_error": ocr_error_s,
            "needs_attention": iban_needs,
            "status_nl": iban_status_nl,
            "warnings_nl": iban_warnings_nl,
        },
        "general": {
            "load_error": load_error_s,
            "load_error_nl": _nl(load_error_s, LOAD_ERROR_NL) if load_error_s else None,
            "decision_status": decision_status,
            "decision_reason_code": reason_code or None,
            "decision_reason_nl": _nl(reason_code, ERROR_REASON_NL) if reason_code else None,
            "decision_reason_detail": str(dec.get("reason_detail") or "").strip() or None,
        },
        "action_suggestions": action_suggestions,
        "overall_status": overall_status,
    }
