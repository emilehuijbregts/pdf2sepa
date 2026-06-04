"""Canonical application helper for resolved field outcomes."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from logic.validation import clean_iban
from parser.field_adapters import canonicalize_legacy_result_dict, field_result_to_legacy_dict
from parser.field_model import FieldId, FieldResult

_RESULT_KEY_BY_FIELD: dict[FieldId, str] = {
    "amount": "amount_result",
    "invoice_number": "invoice_number_result",
    "customer_number": "customer_number_result",
    "iban": "iban_result",
    "vat_number": "vat_number_result",
    "kvk_number": "kvk_number_result",
    "invoice_date": "invoice_date_result",
    "email_domain": "email_domain_result",
}

_LEGACY_VALUE_KEY_BY_FIELD: dict[FieldId, str] = {
    "amount": "amount",
    "invoice_number": "invoice_number",
    "customer_number": "customer_number",
    "iban": "iban",
    "vat_number": "vat_number",
    "kvk_number": "kvk_number",
    "invoice_date": "invoice_date",
    "email_domain": "email_domain",
}


class _ResolvedFieldUiSink(Protocol):
    def apply_resolved_field_result(
        self,
        *,
        field_id: FieldId,
        result: dict[str, Any],
        display_value: str,
    ) -> None: ...


def result_snapshot_key_for_field(field_id: FieldId) -> str:
    return _RESULT_KEY_BY_FIELD[field_id]


def legacy_value_key_for_field(field_id: FieldId) -> str:
    return _LEGACY_VALUE_KEY_BY_FIELD[field_id]


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError, InvalidOperation):
        return None


def _legacy_scalar_value(field_id: FieldId, selected_value: Any) -> Any:
    if selected_value is None:
        return None
    if field_id == "amount":
        return _to_float_or_none(selected_value)
    if field_id == "iban":
        return clean_iban(str(selected_value))
    return str(selected_value).strip()


def _display_value(field_id: FieldId, selected_value: Any) -> str:
    if selected_value is None:
        return ""
    if field_id == "amount":
        return str(selected_value)
    if field_id == "iban":
        return clean_iban(str(selected_value))
    return str(selected_value).strip()


def _canonical_result_dict(field_id: FieldId, result: FieldResult | dict[str, Any]) -> dict[str, Any]:
    if isinstance(result, FieldResult):
        raw = field_result_to_legacy_dict(result)
    elif isinstance(result, dict):
        raw = dict(result)
    else:
        raise TypeError("result must be FieldResult or dict")
    return canonicalize_legacy_result_dict(raw, field_id=field_id, resolver_finalized=True)


def apply_resolved_field_result(
    invoice: dict,
    field_id: FieldId,
    result: FieldResult | dict[str, Any],
    *,
    snapshot: dict | None = None,
    row=None,
    preserve_null_scalar: bool = False,
) -> None:
    """Apply a resolver-final result to invoice, snapshot and optional UI row sink."""
    resolved = _canonical_result_dict(field_id, result)
    result_key = _RESULT_KEY_BY_FIELD[field_id]
    legacy_key = _LEGACY_VALUE_KEY_BY_FIELD[field_id]

    invoice[result_key] = deepcopy(resolved)
    selected = resolved.get("selected_value")
    scalar = _legacy_scalar_value(field_id, selected)
    if scalar is None:
        if preserve_null_scalar:
            invoice[legacy_key] = None
        else:
            invoice.pop(legacy_key, None)
    else:
        invoice[legacy_key] = scalar

    if field_id == "amount":
        invoice["amount_confidence"] = int(resolved.get("confidence") or 0)
        invoice["amount_status"] = str(resolved.get("status") or "failed")
        invoice["amount_source"] = str(resolved.get("source") or "")

    if isinstance(snapshot, dict):
        snapshot[result_key] = deepcopy(resolved)
        if scalar is None:
            if preserve_null_scalar:
                snapshot[legacy_key] = None
            else:
                snapshot.pop(legacy_key, None)
        else:
            snapshot[legacy_key] = scalar

    if row is not None:
        display_value = _display_value(field_id, selected)
        if callable(row):
            row(field_id=field_id, result=deepcopy(resolved), display_value=display_value)
        elif hasattr(row, "apply_resolved_field_result"):
            row.apply_resolved_field_result(
                field_id=field_id,
                result=deepcopy(resolved),
                display_value=display_value,
            )
