"""Bevestigen van factuurvelden en optioneel leren van extractieprofielen (engine-laag, testbaar)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from logic.payment_amounts import amount_to_decimal, format_eur_xml
from parser.field_model import ALL_FIELD_IDS, CORE_PROFILE_FIELD_KEYS, FieldId
from parser.profile_learner import (
    learn_profile_from_resolved_fields,
    prepare_learnable_field_results,
)
from parser.supplier_db import SupplierDB


@dataclass(frozen=True)
class ProfileLearnResult:
    saved: bool
    profile: dict[str, Any] | None
    message: str
    confirmed: dict[str, Any]


def profile_field_keys_missing(stored_profile: dict[str, Any] | None) -> list[str]:
    """Velden die in ``suppliers.json`` nog ontbreken in het extractieprofiel."""
    if not isinstance(stored_profile, dict):
        return list(CORE_PROFILE_FIELD_KEYS)
    missing: list[str] = []
    for key in CORE_PROFILE_FIELD_KEYS:
        spec = stored_profile.get(key)
        if not isinstance(spec, dict) or not spec.get("label") or not spec.get("strategy"):
            missing.append(key)
    return missing


def profile_learning_block_reason(
    snapshot: dict[str, Any],
    *,
    source_file: str | None,
    amount_resolved: bool,
    stored_profile: dict[str, Any] | None = None,
) -> str | None:
    """
    ``None`` = profiel-flow mag; anders een korte code voor logging/tooltips.

    UI-workflow: leverancier in DB → bedrag gekozen → profiel leren/aanvullen.
    """
    if not isinstance(snapshot, dict):
        return "no_snapshot"
    match_status = str(snapshot.get("match_status") or "").strip()
    if match_status not in ("confirmed", "needs_review"):
        return "match_not_eligible"
    extraction_source = str(snapshot.get("extraction_source") or "").strip().lower()
    missing = profile_field_keys_missing(stored_profile)
    if extraction_source == "profile" and missing:
        pass
    elif extraction_source and extraction_source != "generic":
        return "already_profile"
    if not source_file:
        return "no_source_file"
    if not Path(source_file).is_file():
        return "pdf_not_found"
    return None


def can_offer_profile_learning(
    snapshot: dict[str, Any],
    *,
    source_file: str | None,
    amount_resolved: bool = False,
    stored_profile: dict[str, Any] | None = None,
) -> bool:
    """Of UI profiel-bevestiging mag aanbieden (zie ``profile_learning_block_reason``)."""
    return (
        profile_learning_block_reason(
            snapshot,
            source_file=source_file,
            amount_resolved=amount_resolved,
            stored_profile=stored_profile,
        )
        is None
    )


def _normalize_confirmed(confirmed: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    raw_amt = confirmed.get("amount")
    if raw_amt is not None and str(raw_amt).strip():
        try:
            dec = amount_to_decimal(raw_amt)
            if dec > Decimal("0.00"):
                out["amount"] = dec
        except (TypeError, ValueError, InvalidOperation):
            pass
    inv = str(confirmed.get("invoice_number") or "").strip()
    if inv:
        out["invoice_number"] = inv
    cust = str(confirmed.get("customer_number") or "").strip()
    if cust:
        out["customer_number"] = cust
    return out


def _legacy_result_dicts(
    *,
    amount_result: dict[str, Any] | None,
    invoice_number_result: dict[str, Any] | None,
    customer_number_result: dict[str, Any] | None,
    iban_result: dict[str, Any] | None,
) -> dict[FieldId, dict[str, Any] | None]:
    return {
        "amount": amount_result if isinstance(amount_result, dict) else None,
        "invoice_number": invoice_number_result if isinstance(invoice_number_result, dict) else None,
        "customer_number": customer_number_result if isinstance(customer_number_result, dict) else None,
        "iban": iban_result if isinstance(iban_result, dict) else None,
    }


def merge_extraction_profiles(existing: dict[str, Any] | None, learned: dict[str, Any]) -> dict[str, Any]:
    """Behoud bestaande velden; overschrijf met nieuw geleerde velden."""
    out: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    lf = learned.get("learned_from")
    if lf:
        out["learned_from"] = lf
    for key in ALL_FIELD_IDS:
        if key in learned and isinstance(learned.get(key), dict):
            out[key] = learned[key]
    return out


def confirm_invoice_fields(
    *,
    raw_text: str,
    source_file: str,
    supplier_name: str,
    confirmed: dict[str, Any],
    db: SupplierDB,
    save_profile: bool,
    iban: str | None = None,
    post_resolve_snapshot: dict[str, Any] | None = None,
    amount_result: dict[str, Any] | None = None,
    invoice_number_result: dict[str, Any] | None = None,
    customer_number_result: dict[str, Any] | None = None,
    iban_result: dict[str, Any] | None = None,
) -> ProfileLearnResult:
    """
    Bevestig velden; leer en sla optioneel extractieprofiel op.

    Orchestrator: prepare learnable FieldResults → learn → save.
    """
    norm = _normalize_confirmed(confirmed)
    name = str(supplier_name or "").strip()
    if not name:
        return ProfileLearnResult(
            saved=False,
            profile=None,
            message="Leveranciersnaam ontbreekt.",
            confirmed=norm,
        )

    if not save_profile:
        return ProfileLearnResult(
            saved=False,
            profile=None,
            message="Velden bevestigd (profiel niet opgeslagen).",
            confirmed=norm,
        )

    snap = post_resolve_snapshot if isinstance(post_resolve_snapshot, dict) else {}
    iban_res = iban_result
    if iban_res is None and isinstance(snap.get("iban_result"), dict):
        iban_res = snap["iban_result"]

    learnable = prepare_learnable_field_results(
        snap,
        dialog_confirmed=norm,
        legacy_result_dicts=_legacy_result_dicts(
            amount_result=amount_result,
            invoice_number_result=invoice_number_result,
            customer_number_result=customer_number_result,
            iban_result=iban_res,
        ),
    )

    profile = learn_profile_from_resolved_fields(
        raw_text=raw_text,
        source_file=source_file,
        field_results=learnable,
    )

    if profile is None:
        return ProfileLearnResult(
            saved=False,
            profile=None,
            message="Profiel kon niet automatisch worden afgeleid uit de bevestigde waarden.",
            confirmed=norm,
        )

    if norm.get("amount") is not None and "amount" not in profile:
        return ProfileLearnResult(
            saved=False,
            profile=profile,
            message=(
                "Factuurnummer/klantnummer zijn geleerd, maar het bedrag kon niet aan een "
                "vast label in de PDF worden gekoppeld. Controleer het totaal op de factuur "
                "en probeer opnieuw, of kies het bedrag via Diagnostics vóór «Profiel aanmaken»."
            ),
            confirmed=norm,
        )

    existing = db.get_extraction_profile(name)
    profile = merge_extraction_profiles(existing, profile)

    saved = db.save_extraction_profile(name, profile, raw_text=raw_text)
    if not saved:
        return ProfileLearnResult(
            saved=False,
            profile=profile,
            message="Profiel kon niet worden opgeslagen (validatie mislukt).",
            confirmed=norm,
        )

    cust = norm.get("customer_number")
    iban_s = str(iban or "").strip()
    if cust and iban_s:
        db.merge_or_add_supplier(name, iban_s, str(cust))

    return ProfileLearnResult(
        saved=True,
        profile=profile,
        message=f"Profiel opgeslagen voor {name}.",
        confirmed=norm,
    )


def confirmed_amount_xml(confirmed: dict[str, Any]) -> str | None:
    """XML-formaat bedrag uit genormaliseerde confirmed dict."""
    amt = confirmed.get("amount")
    if amt is None:
        return None
    try:
        return format_eur_xml(amount_to_decimal(amt))
    except (TypeError, ValueError, InvalidOperation):
        return None
