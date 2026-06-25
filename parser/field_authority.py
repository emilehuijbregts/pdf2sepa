"""Global field authority: supplier master > user override > profile > OCR."""

from __future__ import annotations

from typing import Any

from parser.field_model import FieldCandidate, FieldId
from parser.supplier_db import CUSTOMER_NUMBER_MODE_NONE, infer_customer_number_mode_from_result

AUTHORITY_FIELD_IDS: frozenset[FieldId] = frozenset(
    {"customer_number", "invoice_number", "iban", "amount"}
)


def is_user_locked(result_dict: dict[str, Any] | None) -> bool:
    """True when the user has locked this field (OCR/profile must not overwrite)."""
    return bool(isinstance(result_dict, dict) and result_dict.get("user_overridden"))


def should_apply_profile_override(field_id: FieldId, result_dict: dict[str, Any] | None) -> bool:
    """Profile candidates may compete only when the field is not user-locked."""
    if field_id not in AUTHORITY_FIELD_IDS:
        return True
    return not is_user_locked(result_dict)


def should_enforce_none_absent(
    result_dict: dict[str, Any] | None,
    *,
    none_mode_active: bool,
) -> bool:
    """
    Supplier NONE blocks OCR reinjection unless the user locked a per-document value.

    ``none_mode_active`` is True when the supplier profile has customer_number_mode=NONE.
    """
    if not none_mode_active:
        return False
    return not is_user_locked(result_dict)


def build_user_pick(
    field_id: FieldId,
    result_dict: dict[str, Any],
    *,
    selected_value: Any | None = None,
    source: str | None = None,
    context: str | None = None,
) -> FieldCandidate | None:
    """
    Build a resolver ``user_pick`` from a legacy ``*_result`` dict.

    Returns None when the field is not user-locked.
    """
    if not is_user_locked(result_dict):
        return None

    sel = selected_value if selected_value is not None else result_dict.get("selected_value")
    src = source if source is not None else str(result_dict.get("source") or "USER_PICKED")
    ctx = context if context is not None else str(result_dict.get("context") or "")

    if field_id == "customer_number" and infer_customer_number_mode_from_result(result_dict) == CUSTOMER_NUMBER_MODE_NONE:
        from ui.field_review import CUSTOMER_ABSENT_PICK_SOURCE

        return FieldCandidate(
            value=None,
            source=src or CUSTOMER_ABSENT_PICK_SOURCE,
            confidence=100,
            context=ctx,
        )

    if sel is not None:
        return FieldCandidate(
            value=sel,
            source=src,
            confidence=100,
            context=ctx,
        )

    if field_id == "iban":
        return FieldCandidate(
            value=sel or "",
            source=src,
            confidence=100,
            context=ctx,
        )

    return None


def build_user_pick_from_legacy(
    field_id: FieldId,
    generic_fr: Any,
) -> FieldCandidate | None:
    """Build user_pick from a FieldResult (hybrid/generic resolution paths)."""
    if not generic_fr.user_overridden:
        return None
    return build_user_pick(
        field_id,
        {
            "user_overridden": True,
            "selected_value": generic_fr.selected_value,
            "source": generic_fr.source,
            "context": generic_fr.context,
        },
    )
