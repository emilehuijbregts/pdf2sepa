"""Bevestigen van factuurvelden en optioneel leren van extractieprofielen (engine-laag, testbaar)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from logic.payment_amounts import amount_to_decimal, format_eur_xml
from parser.field_model import ALL_FIELD_IDS, CORE_PROFILE_FIELD_KEYS, FieldId
from parser.profile_learner import (
    AMOUNT_LEARN_FIELDS,
    IDENTIFICATION_LEARN_FIELDS,
    get_last_strategy_results,
    learn_profile_from_resolved_fields,
    prepare_learnable_field_results,
)
from parser.profile_strategy_engine import is_valid_field_spec
from parser.supplier_db import SupplierDB, customer_number_profile_locked

ProfileFieldStatus = Literal["learned", "failed", "skipped"]

_PROFILE_LEARN_FIELD_KEYS: tuple[str, ...] = (
    *IDENTIFICATION_LEARN_FIELDS,
    *AMOUNT_LEARN_FIELDS,
)

_FIELD_LABELS_NL: dict[str, str] = {
    "invoice_number": "Factuurnummer",
    "customer_number": "Klantnummer",
    "amount": "Bedrag",
}


@dataclass(frozen=True)
class ProfileFieldOutcome:
    field_id: str
    status: ProfileFieldStatus
    detail: str = ""
    strategy_trace: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProfileLearnResult:
    saved: bool
    profile: dict[str, Any] | None
    message: str
    confirmed: dict[str, Any]
    field_outcomes: tuple[ProfileFieldOutcome, ...] = ()


def profile_field_keys_missing(stored_profile: dict[str, Any] | None) -> list[str]:
    """Velden die in ``suppliers.json`` nog ontbreken in het extractieprofiel."""
    if not isinstance(stored_profile, dict):
        return list(CORE_PROFILE_FIELD_KEYS)
    missing: list[str] = []
    for key in CORE_PROFILE_FIELD_KEYS:
        spec = stored_profile.get(key)
        if not isinstance(spec, dict) or not is_valid_field_spec(spec, key):  # type: ignore[arg-type]
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
    raw_cust = confirmed.get("customer_number")
    if isinstance(raw_cust, dict):
        from parser.supplier_db import CUSTOMER_NUMBER_MODE_NONE, infer_customer_number_mode_from_result

        if infer_customer_number_mode_from_result(raw_cust) != CUSTOMER_NUMBER_MODE_NONE:
            pass
    else:
        cust = str(raw_cust or "").strip()
        if cust:
            out["customer_number"] = cust
    vat = str(confirmed.get("vat_number") or "").strip()
    if vat:
        out["vat_number"] = vat
    kvk = str(confirmed.get("kvk_number") or "").strip()
    if kvk:
        out["kvk_number"] = kvk
    dom = str(confirmed.get("email_domain") or "").strip()
    if dom:
        out["email_domain"] = dom
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


def _compute_profile_field_outcomes(
    norm: dict[str, Any],
    profile: dict[str, Any] | None,
) -> tuple[ProfileFieldOutcome, ...]:
    """Per-field learn status (identification and amount domains are independent)."""
    prof = profile if isinstance(profile, dict) else {}
    traces = get_last_strategy_results()
    outcomes: list[ProfileFieldOutcome] = []
    for field_id in _PROFILE_LEARN_FIELD_KEYS:
        trace_dict = traces.get(field_id)  # type: ignore[arg-type]
        strategy_trace = trace_dict.to_dict() if trace_dict is not None else None
        if field_id not in norm:
            outcomes.append(
                ProfileFieldOutcome(field_id=field_id, status="skipped", strategy_trace=strategy_trace)
            )
            continue
        spec = prof.get(field_id)
        if isinstance(spec, dict) and is_valid_field_spec(spec, field_id):  # type: ignore[arg-type]
            outcomes.append(
                ProfileFieldOutcome(
                    field_id=field_id,
                    status="learned",
                    detail="geleerd en opgeslagen",
                    strategy_trace=strategy_trace,
                )
            )
        else:
            if field_id == "amount":
                detail = (
                    "kon niet aan een vast label in de PDF worden gekoppeld. "
                    "Controleer het totaal op de factuur en probeer opnieuw, "
                    "of kies het bedrag via Diagnostics vóór «Profiel aanmaken»."
                )
            else:
                detail = "kon niet automatisch worden afgeleid uit de bevestigde waarde."
            if strategy_trace and strategy_trace.get("validation_trace"):
                detail = f"{detail} (trace: {', '.join(strategy_trace['validation_trace'][:3])})"
            outcomes.append(
                ProfileFieldOutcome(
                    field_id=field_id,
                    status="failed",
                    detail=detail,
                    strategy_trace=strategy_trace,
                )
            )
    return tuple(outcomes)


def _format_profile_learn_messages(
    outcomes: tuple[ProfileFieldOutcome, ...],
    supplier_name: str,
    *,
    saved: bool,
) -> str:
    """Per-field NL lines; no combined ident+amount failure sentence."""
    lines: list[str] = []
    learned = [o for o in outcomes if o.status == "learned"]
    failed = [o for o in outcomes if o.status == "failed"]

    if saved:
        if learned and failed:
            header = f"Profiel (deels) opgeslagen voor {supplier_name}."
        elif learned:
            header = f"Profiel opgeslagen voor {supplier_name}."
        else:
            header = f"Profiel opgeslagen voor {supplier_name}."
        lines.append(header)
    elif failed and not learned:
        lines.append("Profiel kon niet worden opgeslagen.")
    else:
        lines.append(f"Profiel kon niet volledig worden opgeslagen voor {supplier_name}.")

    for outcome in outcomes:
        if outcome.status == "skipped":
            continue
        label = _FIELD_LABELS_NL.get(outcome.field_id, outcome.field_id)
        if outcome.status == "learned":
            lines.append(f"{label}: geleerd en opgeslagen.")
        else:
            lines.append(f"{label}: {outcome.detail}")

    return "\n".join(lines)


def merge_extraction_profiles(existing: dict[str, Any] | None, learned: dict[str, Any]) -> dict[str, Any]:
    """Behoud bestaande velden; overschrijf met nieuw geleerde velden."""
    out: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    lf = learned.get("learned_from")
    if lf:
        out["learned_from"] = lf
    cust_locked = customer_number_profile_locked(existing)
    for key in ALL_FIELD_IDS:
        if key == "customer_number" and cust_locked:
            continue
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
    Identification and amount domains learn and save independently.
    """
    norm = _normalize_confirmed(confirmed)
    name = str(supplier_name or "").strip()
    empty_outcomes: tuple[ProfileFieldOutcome, ...] = ()

    if not name:
        return ProfileLearnResult(
            saved=False,
            profile=None,
            message="Leveranciersnaam ontbreekt.",
            confirmed=norm,
            field_outcomes=empty_outcomes,
        )

    if not save_profile:
        return ProfileLearnResult(
            saved=False,
            profile=None,
            message="Velden bevestigd (profiel niet opgeslagen).",
            confirmed=norm,
            field_outcomes=empty_outcomes,
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
        from parser.supplier_db import CUSTOMER_NUMBER_MODE_NONE, infer_customer_number_mode_from_result

        if infer_customer_number_mode_from_result(customer_number_result) == CUSTOMER_NUMBER_MODE_NONE:
            saved = db.set_customer_number_mode(name, CUSTOMER_NUMBER_MODE_NONE)
            return ProfileLearnResult(
                saved=saved,
                profile=db.get_extraction_profile(name),
                message=(
                    f"Profiel opgeslagen voor {name} (geen klantnummer)."
                    if saved
                    else "Kon leveranciersprofiel niet opslaan."
                ),
                confirmed=norm,
                field_outcomes=empty_outcomes,
            )
        outcomes = _compute_profile_field_outcomes(norm, None)
        if not norm:
            msg = "Profiel kon niet automatisch worden afgeleid uit de bevestigde waarden."
        else:
            msg = _format_profile_learn_messages(outcomes, name, saved=False)
        return ProfileLearnResult(
            saved=False,
            profile=None,
            message=msg,
            confirmed=norm,
            field_outcomes=outcomes,
        )

    outcomes = _compute_profile_field_outcomes(norm, profile)
    has_learned = any(o.status == "learned" for o in outcomes)

    if not has_learned:
        return ProfileLearnResult(
            saved=False,
            profile=profile,
            message=_format_profile_learn_messages(outcomes, name, saved=False),
            confirmed=norm,
            field_outcomes=outcomes,
        )

    existing = db.get_extraction_profile(name)
    profile = merge_extraction_profiles(existing, profile)

    saved = db.save_extraction_profile(
        name,
        profile,
        raw_text=raw_text,
        customer_number_result=customer_number_result,
    )
    if not saved:
        return ProfileLearnResult(
            saved=False,
            profile=profile,
            message="Profiel kon niet worden opgeslagen (validatie mislukt).",
            confirmed=norm,
            field_outcomes=outcomes,
        )

    from parser.supplier_db import (
        CUSTOMER_NUMBER_MODE_NONE,
        customer_number_mode_from_profile,
        infer_customer_number_mode_from_result,
    )

    if (
        infer_customer_number_mode_from_result(customer_number_result) == CUSTOMER_NUMBER_MODE_NONE
        and customer_number_mode_from_profile(profile) != CUSTOMER_NUMBER_MODE_NONE
    ):
        db.set_customer_number_mode(name, CUSTOMER_NUMBER_MODE_NONE)
        profile = db.get_extraction_profile(name) or profile

    cust = norm.get("customer_number")
    iban_s = str(iban or "").strip()
    if cust and iban_s:
        db.merge_or_add_supplier(
            name,
            iban_s,
            str(cust),
            vat_number=str(norm.get("vat_number") or "").strip() or None,
            kvk_number=str(norm.get("kvk_number") or "").strip() or None,
            email_domain=str(norm.get("email_domain") or "").strip() or None,
        )

    return ProfileLearnResult(
        saved=True,
        profile=profile,
        message=_format_profile_learn_messages(outcomes, name, saved=True),
        confirmed=norm,
        field_outcomes=outcomes,
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
