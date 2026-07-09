"""
Profielgestuurde extractie van bedrag, factuurnummer, klantnummer en IBAN uit factuur-raw_text.

Execution-only: learning lives in ``parser.profile_learner``; strategies in ``parser.profile_strategy_engine``.
"""

from __future__ import annotations

from typing import Any

from decimal import Decimal

from logic.validation import clean_iban
from parser.field_model import ALL_FIELD_IDS
from parser.profile_strategy_engine import (
    STRATEGY_NAMES,
    amount_decimal_matches,
    apply_strategy,
    confirmed_amount_decimal,
    execute_spec,
    extract_derived_excl_plus_vat,
    find_label_line,
    is_valid_field_spec,
    run_runtime_fallback,
    split_lines,
    values_match,
)

FIELD_KEYS = ALL_FIELD_IDS

# Backward-compatible alias for suppliers.json and tests.
STRATEGIES = STRATEGY_NAMES

_AMOUNT_TOLERANCE = Decimal("0.01")


def _field_spec(profile: dict[str, Any], field: str) -> dict[str, Any] | None:
    spec = profile.get(field)
    if not isinstance(spec, dict):
        return None
    if not is_valid_field_spec(spec, field):  # type: ignore[arg-type]
        return None
    return spec


def extract_amount_with_field_spec(
    lines: list[str],
    field_spec: dict[str, Any],
) -> Decimal | None:
    """Extract one amount field spec from pre-split lines (profile execution)."""
    raw = "\n".join(lines)
    val = execute_spec(raw, "amount", field_spec)
    return confirmed_amount_decimal(val)


def amount_field_spec_matches(
    lines: list[str],
    field_spec: dict[str, Any],
    expected: Decimal | float | str,
) -> bool:
    target = confirmed_amount_decimal(expected)
    if target is None:
        return False
    ext = extract_amount_with_field_spec(lines, field_spec)
    return amount_decimal_matches(ext, target)


# Re-export engine helpers for backward compatibility in tests/learner.
_split_lines = split_lines
_find_label_line = find_label_line
_apply_strategy = apply_strategy
_extract_derived_excl_plus_vat = extract_derived_excl_plus_vat
_confirmed_amount_decimal = confirmed_amount_decimal
_amount_decimal_matches = amount_decimal_matches


def extract_with_profile(raw_text: str, profile: dict[str, Any]) -> dict[str, float | str | None]:
    """Extract profile fields using a supplier profile (hybrid: spec first, fallback on failure)."""
    out: dict[str, float | str | None] = {
        "amount": None,
        "invoice_number": None,
        "customer_number": None,
        "iban": None,
        "vat_number": None,
        "kvk_number": None,
        "invoice_date": None,
        "email_domain": None,
    }
    for field in FIELD_KEYS:
        spec = _field_spec(profile, field)
        if spec is None:
            continue
        val = execute_spec(raw_text, field, spec)  # type: ignore[arg-type]
        if val is None and spec.get("confirmed_value") is not None:
            fallback = run_runtime_fallback(
                field, raw_text, spec.get("confirmed_value")
            )  # type: ignore[arg-type]
            if fallback.value is not None:
                val = fallback.value
        if val is not None:
            out[field] = val  # type: ignore[assignment]
    return out


def _merge_confirmed(
    profile: dict[str, Any],
    confirmed: dict[str, Any] | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in FIELD_KEYS:
        if confirmed and field in confirmed and confirmed[field] is not None:
            if field == "amount":
                out[field] = confirmed[field]
            elif field == "iban":
                v = clean_iban(str(confirmed[field]))
                if v:
                    out[field] = v
            else:
                v = str(confirmed[field]).strip()
                if v:
                    out[field] = v
            continue
        spec = _field_spec(profile, field)
        if spec and spec.get("confirmed_value") is not None:
            out[field] = spec["confirmed_value"]
    return out


def validate_profile_structure(raw_text: str, profile: dict[str, Any]) -> bool:
    """Runtime check: persisted specs extract a value on this document (any amount/number)."""
    extracted = extract_with_profile(raw_text, profile)
    for field in FIELD_KEYS:
        if field not in profile or not _field_spec(profile, field):
            continue
        if extracted.get(field) is None:
            return False
    return True


def validate_profile(
    raw_text: str,
    profile: dict[str, Any],
    confirmed: dict[str, Any] | None = None,
) -> bool:
    """Return True if extract_with_profile matches confirmed for every profile field."""
    merged = _merge_confirmed(profile, confirmed)
    extracted = extract_with_profile(raw_text, profile)
    for field in FIELD_KEYS:
        if field not in profile or not _field_spec(profile, field):
            continue
        if field not in merged:
            return False
        if not values_match(field, extracted.get(field), merged[field]):
            return False
    return True
