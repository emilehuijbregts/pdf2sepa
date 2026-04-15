"""Golden dataset helpers (business-output snapshots).

This module provides shared normalization and extraction utilities for:
- scripts that write golden JSON files
- tests that validate pipeline output against golden truth

It intentionally stores/compares only **business output**, not UI state, XML, or traces.
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from logic.validation import clean_iban

_MONEY_Q = Decimal("0.01")


def pdf_filename(source_file: Any) -> str:
    """Return PDF filename (no directories) from a source_file value."""
    s = str(source_file or "").strip()
    if not s:
        return ""
    return Path(s).name


def money_to_str(value: Any) -> str:
    """Normalize money to a JSON string with 2 decimals (ROUND_HALF_UP)."""
    if isinstance(value, Decimal):
        d = value
    else:
        try:
            d = Decimal(str(value).strip().replace(",", "."))
        except (InvalidOperation, AttributeError) as exc:
            raise ValueError("invalid money value") from exc
    return str(d.quantize(_MONEY_Q, rounding=ROUND_HALF_UP))


def discount_pct_to_str(value: Any) -> str:
    """Normalize discount percentage to 2 decimals string."""
    if value in (None, ""):
        return money_to_str(Decimal("0"))
    return money_to_str(value)


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_iban(value: Any) -> str:
    return clean_iban(str(value or ""))


def amount_status_from_payment(payment: dict) -> str:
    """Extract parsed amount status from payment decision_trace snapshot (engine trace)."""
    dt = payment.get("decision_trace")
    if not isinstance(dt, dict):
        return ""
    snap = dt.get("reconciliation_snapshot")
    if not isinstance(snap, dict):
        return ""
    par = snap.get("parsed_amount_result")
    if not isinstance(par, dict):
        return ""
    return normalize_text(par.get("status"))


def match_status_from_payment(payment: dict) -> str:
    dt = payment.get("decision_trace")
    if not isinstance(dt, dict):
        return ""
    return normalize_text(dt.get("supplier_match_status"))


def decision_status_from_payment(payment: dict) -> str:
    dec = payment.get("decision")
    if isinstance(dec, dict):
        return normalize_text(dec.get("status"))
    # Legacy fallback
    status = normalize_text(payment.get("status")).lower()
    if status in ("ok", "confirmed", "reviewed", "matched", "new"):
        return "included"
    if status in ("needs_review", "needs review"):
        return "needs_review"
    if status:
        return "excluded"
    return ""


_SAFE_FILENAME_RE = re.compile(r"[^a-z0-9_-]+")


def safe_slug(value: Any) -> str:
    s = normalize_text(value).casefold()
    s = s.replace("&", "and")
    s = _SAFE_FILENAME_RE.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def golden_filename(
    *,
    supplier_name: Any,
    invoice_number: Any,
    source_file: Any,
) -> str:
    sup = safe_slug(supplier_name)
    inv = safe_slug(invoice_number)
    if sup and inv:
        return f"{sup}_{inv}.json"
    seed = f"{pdf_filename(source_file)}|{normalize_text(supplier_name)}|{normalize_text(invoice_number)}"
    h = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return f"{h}.json"

