"""Resolve canonical IBAN from invoice dict (iban_result → legacy iban)."""

from __future__ import annotations

from typing import Any

from logic.validation import clean_iban


def resolved_iban_from_invoice(inv: dict[str, Any]) -> str:
    ir = inv.get("iban_result")
    if isinstance(ir, dict):
        val = clean_iban(str(ir.get("value") or ""))
        if val:
            return val
    return clean_iban(str(inv.get("iban") or ""))
