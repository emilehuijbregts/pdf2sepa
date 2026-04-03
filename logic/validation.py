"""Gedeelde validatiefuncties voor IBAN/BIC en andere betaalgegevens."""

from __future__ import annotations

import re


def _iban_mod97_valid(iban: str) -> bool:
    """ISO 13616 mod-97 check. Returns True for valid IBANs."""
    try:
        rearranged = iban[4:] + iban[:4]
        numeric = ""
        for ch in rearranged:
            if ch.isdigit():
                numeric += ch
            else:
                numeric += str(ord(ch) - 55)
        return int(numeric) % 97 == 1
    except Exception:
        return False


def clean_iban(iban: str | None) -> str:
    """Normaliseer IBAN: verwijder whitespace, uppercase."""
    try:
        s = str(iban or "")
        s = re.sub(r"\s+", "", s)
        return s.upper().strip()
    except Exception:
        return ""


def mask_iban_for_log(iban: str | None) -> str:
    """Voor logging: laat landcode + laatste 4 zien, geen volledige IBAN."""
    c = clean_iban(iban)
    if not c:
        return "<none>"
    if len(c) <= 6:
        return c[:2] + "…"
    return f"{c[:2]}…{c[-4:]}"


def is_plausible_iban(iban: str) -> bool:
    """IBAN validation: format check + mod-97 checksum."""
    if len(iban) < 15 or len(iban) > 34:
        return False
    if not re.fullmatch(r"[A-Z]{2}[0-9A-Z]{13,32}", iban):
        return False
    return _iban_mod97_valid(iban)
