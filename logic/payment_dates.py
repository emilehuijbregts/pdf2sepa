"""Datumlogica voor betaalmodi (direct / due / manual) en ISO-validatie."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Literal

DateMode = Literal["direct", "due", "manual"]


def parse_iso_date(s: str | None) -> date | None:
    if not s or not str(s).strip():
        return None
    t = str(s).strip()
    try:
        return datetime.strptime(t, "%Y-%m-%d").date()
    except ValueError:
        return None


def is_valid_iso_date_str(s: str | None) -> bool:
    return parse_iso_date(s) is not None


def format_date_nl_from_iso(iso: str | None) -> str:
    """``YYYY-MM-DD`` → ``DD-MM-YYYY``; lege string bij ontbrekend of ongeldig."""
    d = parse_iso_date(iso)
    if d is None:
        return ""
    return f"{d.day:02d}-{d.month:02d}-{d.year}"


def parse_ui_date_to_iso(s: str | None) -> str | None:
    """
    Accepteert ``YYYY-MM-DD``, daarna ``DD-MM-YYYY`` en ``DD/MM/YYYY`` (en varianten met `.`).
    Retourneert alleen ``YYYY-MM-DD`` of ``None``.
    """
    if not s or not str(s).strip():
        return None
    t = str(s).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        return t if parse_iso_date(t) else None
    for sep in ("-", "/", "."):
        m = re.fullmatch(rf"(\d{{2}}){re.escape(sep)}(\d{{2}}){re.escape(sep)}(\d{{4}})", t)
        if not m:
            continue
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        return candidate.isoformat()
    return None


def is_weekend(d: date) -> bool:
    """Zaterdag of zondag (ISO weekday: maandag=1, zondag=7)."""
    return d.weekday() >= 5


def execution_date_for_direct(session: date) -> str:
    return session.isoformat()


def execution_date_for_due(
    invoice_date_iso: str | None,
    term_days_zero_based: int,
    session: date,
) -> str | None:
    """
    Uiterste betaaldatum: invoice + term_days, minimaal session.
    Returns None if invoice_date_iso ontbreekt of ongeldig is.
    """
    inv = parse_iso_date(invoice_date_iso)
    if inv is None:
        return None
    try:
        td = int(term_days_zero_based)
    except (TypeError, ValueError):
        td = 0
    target = inv + timedelta(days=td)
    final = target if target > session else session
    return final.isoformat()
