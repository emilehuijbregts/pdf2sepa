"""Learn and persist supplier credit_profile from explicit user confirmation only."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from logic.payment_amounts import amount_to_decimal
from parser.field_model import FieldId, FieldResult, field_result_from_result_dict, is_resolver_final_field_result
from parser.profile_learner import _learn_field_spec
from parser.supplier_db import SupplierDB, supplier_key_from_name

CreditProfileFieldStatus = Literal["learned", "failed", "skipped"]

_CREDIT_LEARN_FIELDS: tuple[FieldId, ...] = ("amount", "invoice_number")

_FIELD_LABELS_NL: dict[str, str] = {
    "amount": "Bedrag",
    "credit_number": "Creditnummer",
}


@dataclass(frozen=True)
class CreditProfileFieldOutcome:
    field_id: str
    status: CreditProfileFieldStatus
    detail: str = ""


@dataclass(frozen=True)
class CreditProfileLearnResult:
    saved: bool
    profile: dict[str, Any] | None
    message: str
    confirmed: dict[str, Any]
    field_outcomes: tuple[CreditProfileFieldOutcome, ...] = ()


def credit_profile_learning_block_reason(
    snapshot: dict[str, Any],
    *,
    source_file: str | None,
    supplier_key: str | None,
) -> str | None:
    """``None`` = credit profile save may proceed; else block reason code."""
    if not isinstance(snapshot, dict):
        return "no_snapshot"
    if str(snapshot.get("type") or "") != "credit_note":
        return "not_credit_note"
    match_status = str(snapshot.get("match_status") or "").strip()
    if match_status not in ("confirmed", "needs_review"):
        return "match_not_eligible"
    if not str(supplier_key or "").strip():
        return "no_supplier_key"
    if not source_file:
        return "no_source_file"
    if not Path(source_file).is_file():
        return "pdf_not_found"
    return None


def can_offer_credit_profile_learning(
    snapshot: dict[str, Any],
    *,
    source_file: str | None,
    supplier_key: str | None,
) -> bool:
    return (
        credit_profile_learning_block_reason(
            snapshot,
            source_file=source_file,
            supplier_key=supplier_key,
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
    inv = str(confirmed.get("invoice_number") or confirmed.get("credit_number") or "").strip()
    if inv:
        out["credit_number"] = inv
    return out


def _field_result_for_learning(
    field_id: FieldId,
    confirmed_value: Any,
    result_snapshot: dict[str, Any] | None,
) -> FieldResult | None:
    if isinstance(result_snapshot, dict):
        fr = field_result_from_result_dict(result_snapshot, field_id=field_id)
        if fr is not None and is_resolver_final_field_result(fr):
            if confirmed_value is not None:
                fr = FieldResult(
                    field_id=fr.field_id,
                    selected_value=confirmed_value,
                    source=fr.source or "user_confirmed",
                    status="confirmed",
                    confidence=max(int(fr.confidence or 0), 90),
                    candidates=fr.candidates,
                    context=fr.context,
                    user_selected=True,
                    user_overridden=True,
                    override_reason="credit_profile_save",
                    resolver_finalized=True,
                )
            return fr
    if confirmed_value is None:
        return None
    return FieldResult(
        field_id=field_id,
        selected_value=confirmed_value,
        source="user_confirmed",
        status="confirmed",
        confidence=90,
        candidates=[],
        user_selected=True,
        user_overridden=True,
        override_reason="credit_profile_save",
        resolver_finalized=True,
    )


def _compute_outcomes(
    norm: dict[str, Any],
    profile: dict[str, Any] | None,
) -> tuple[CreditProfileFieldOutcome, ...]:
    prof = profile if isinstance(profile, dict) else {}
    outcomes: list[CreditProfileFieldOutcome] = []
    mapping = (("amount", "amount"), ("credit_number", "credit_number"))
    for norm_key, profile_key in mapping:
        if norm_key not in norm:
            outcomes.append(CreditProfileFieldOutcome(field_id=profile_key, status="skipped"))
            continue
        spec = prof.get(profile_key)
        if isinstance(spec, dict) and (spec.get("label") or spec.get("strategy") == "derived_excl_plus_vat"):
            outcomes.append(
                CreditProfileFieldOutcome(
                    field_id=profile_key,
                    status="learned",
                    detail="geleerd en opgeslagen",
                )
            )
        else:
            detail = (
                "kon niet aan een vast label in de PDF worden gekoppeld."
                if profile_key == "amount"
                else "kon niet automatisch worden afgeleid uit de bevestigde waarde."
            )
            outcomes.append(
                CreditProfileFieldOutcome(field_id=profile_key, status="failed", detail=detail)
            )
    return tuple(outcomes)


def _format_message(
    outcomes: tuple[CreditProfileFieldOutcome, ...],
    *,
    saved: bool,
) -> str:
    lines: list[str] = []
    if saved:
        lines.append("Creditprofiel opgeslagen.")
    else:
        lines.append("Creditprofiel kon niet worden opgeslagen.")
    for outcome in outcomes:
        if outcome.status == "skipped":
            continue
        label = _FIELD_LABELS_NL.get(outcome.field_id, outcome.field_id)
        if outcome.status == "learned":
            lines.append(f"{label}: geleerd en opgeslagen.")
        else:
            lines.append(f"{label}: {outcome.detail}")
    return "\n".join(lines)


def learn_credit_profile_from_confirmation(
    *,
    raw_text: str,
    source_file: str,
    supplier_key: str,
    confirmed: dict[str, Any],
    amount_result: dict[str, Any] | None = None,
    invoice_number_result: dict[str, Any] | None = None,
    explicit_user_action: bool = False,
) -> dict[str, Any] | None:
    """Derive credit_profile specs from user-confirmed credit document fields."""
    if not explicit_user_action:
        return None
    norm = _normalize_confirmed(confirmed)
    if not norm:
        return None

    profile: dict[str, Any] = {"learned_from": Path(source_file or "").name}

    amount_fr = _field_result_for_learning("amount", norm.get("amount"), amount_result)
    if amount_fr is not None:
        spec = _learn_field_spec(raw_text, "amount", amount_fr)
        if spec is not None:
            profile["amount"] = spec

    credit_val = norm.get("credit_number")
    credit_fr = _field_result_for_learning("invoice_number", credit_val, invoice_number_result)
    if credit_fr is not None:
        spec = _learn_field_spec(raw_text, "invoice_number", credit_fr)
        if spec is not None:
            profile["credit_number"] = spec

    if not any(k in profile for k in ("amount", "credit_number")):
        return None
    return profile


def confirm_credit_profile_fields(
    *,
    raw_text: str,
    source_file: str,
    supplier_key: str,
    confirmed: dict[str, Any],
    db: SupplierDB,
    save_profile: bool,
    amount_result: dict[str, Any] | None = None,
    invoice_number_result: dict[str, Any] | None = None,
    explicit_user_action: bool = False,
) -> CreditProfileLearnResult:
    """Learn and optionally persist credit_profile for a supplier (explicit action only)."""
    norm = _normalize_confirmed(confirmed)
    key = str(supplier_key or "").strip()
    empty: tuple[CreditProfileFieldOutcome, ...] = ()

    if not key:
        return CreditProfileLearnResult(
            saved=False,
            profile=None,
            message="Supplier key ontbreekt.",
            confirmed=norm,
            field_outcomes=empty,
        )

    if not save_profile or not explicit_user_action:
        return CreditProfileLearnResult(
            saved=False,
            profile=None,
            message="Velden bevestigd (creditprofiel niet opgeslagen).",
            confirmed=norm,
            field_outcomes=empty,
        )

    profile = learn_credit_profile_from_confirmation(
        raw_text=raw_text,
        source_file=source_file,
        supplier_key=key,
        confirmed=confirmed,
        amount_result=amount_result,
        invoice_number_result=invoice_number_result,
        explicit_user_action=True,
    )
    outcomes = _compute_outcomes(norm, profile)

    if profile is None or not any(o.status == "learned" for o in outcomes):
        return CreditProfileLearnResult(
            saved=False,
            profile=profile,
            message=_format_message(outcomes, saved=False),
            confirmed=norm,
            field_outcomes=outcomes,
        )

    saved = db.save_credit_profile(
        key,
        profile,
        raw_text=raw_text,
        explicit_user_action=True,
    )
    if not saved:
        return CreditProfileLearnResult(
            saved=False,
            profile=profile,
            message="Creditprofiel kon niet worden opgeslagen (validatie mislukt).",
            confirmed=norm,
            field_outcomes=outcomes,
        )

    stored = db.get_credit_profile(key)
    return CreditProfileLearnResult(
        saved=True,
        profile=stored,
        message=_format_message(outcomes, saved=True),
        confirmed=norm,
        field_outcomes=outcomes,
    )


def supplier_key_for_matched_invoice(inv: dict[str, Any]) -> str | None:
    """Resolve stable supplier key from a matched invoice dict."""
    key = str(inv.get("supplier_key") or "").strip()
    if key:
        return key
    name = str(inv.get("supplier_name") or "").strip()
    if not name:
        return None
    derived = supplier_key_from_name(name)
    return derived or None
