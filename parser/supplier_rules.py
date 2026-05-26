# Regels per leverancier (mapping/heuristiek) om velden uit PDF's correct te interpreteren komen hier.

from __future__ import annotations

import re


def detect_supplier(text: str | None, suppliers_list: list[dict]) -> dict | None:
    """
    Detecteer leverancier in tekst op basis van `name` en `aliases`.

    - Case-insensitive via lower()
    - Return supplier dict als gevonden, anders None
    """

    if not text:
        return None

    haystack = text.lower()

    for supplier in suppliers_list:
        name = (supplier.get("name") or "").strip()
        aliases = supplier.get("aliases") or []

        candidates = [name, *list(aliases)]
        for cand in candidates:
            needle = (cand or "").strip().lower()
            if needle and needle in haystack:
                return supplier

    return None


def extract_supplier_name_hint(text: str | None) -> str | None:
    """
    Heuristiek: zoek een rechtsvorm-token (BV/B.V./NV/VOF) en geef
    maximaal 3 woorden ervoor + het rechtsvorm-token terug.
    """

    if not text:
        return None

    # We strip punctuation from tokens below, so include stripped variants too.
    legal_forms = {"bv", "b.v", "b.v.", "nv", "n.v", "n.v.", "vof"}

    tokens = text.split()
    for i, tok in enumerate(tokens):
        stripped = tok.strip(",.;:()[]{}").lower()
        if stripped in legal_forms:
            start = max(0, i - 3)
            return " ".join(tokens[start : i + 1]).strip() or None

    # Alternatief patroon: "Handelsnaam: <naam>"
    # (eerste gok; bedoeld als hint, niet als definitieve validatie)
    for line in text.splitlines():
        if "handelsnaam" in line.lower():
            parts = line.split(":", 1)
            if len(parts) == 2:
                cand = parts[1].strip()
                return cand or None

    # Premie/herinnering: "t.n.v. Polaris Werk, Vitaal en Verzekeren over te maken"
    m_tnv = re.search(
        r"(?i)\bt\.?\s*n\.?\s*v\.?\s+(.+?)\s+over\s+te\s+maken",
        text,
    )
    if m_tnv:
        cand = str(m_tnv.group(1) or "").strip().rstrip(",.;")
        if cand:
            return cand

    return None
