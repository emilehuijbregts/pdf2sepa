"""Apply amount overrides to a copy of matched_invoices before engine calculation.

This module is the only sanctioned point of contact between AmountOverrideStore
and the engine pipeline.  It returns a *new* list — it never mutates the original
_matched_invoices.  When no overrides are active, it returns the original list
unchanged (no copy overhead).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from logic.amount_override_store import AmountOverrideSession
from logic.credit_settlement import document_id


def apply_amount_overrides(
    matched: list[dict[str, Any]],
    session: AmountOverrideSession | None,
) -> list[dict[str, Any]]:
    """Return matched with overridden gross amounts applied.

    Only ``amount_dec`` (Decimal used by the engine) and ``amount`` (display
    string) are patched on affected documents.  All other fields are untouched.
    Documents not in the override session are returned as-is (same dict object).

    Safety contract:
    - original ``matched`` list is never mutated
    - Decimal arithmetic uses the stored Decimal directly (no string round-trip)
    - Returns the original list if session is empty (zero overhead)
    """
    if not session or not session.overrides:
        return matched
    override_map = {o.document_id: o for o in session.overrides}
    result: list[dict[str, Any]] = []
    any_patched = False
    for inv in matched:
        doc_id = document_id({"raw": inv})
        if doc_id in override_map:
            override = override_map[doc_id]
            patched = dict(inv)
            patched["amount_dec"] = override.new_amount
            patched["amount"] = str(override.new_amount)
            result.append(patched)
            any_patched = True
        else:
            result.append(inv)
    return result if any_patched else matched
