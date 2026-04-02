"""
Applicatie-instellingen: SEPA-betaler (naam/IBAN/BIC in JSON-sleutel ``debtor``), exportmap.

Merge en defaults zitten hier; het Instellingen-paneel in de app schrijft door naar
``data/settings.json``.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

from logic.payment_engine import clean_iban, is_plausible_iban

DEFAULT_SETTINGS: dict[str, Any] = {
    "debtor": {
        "name": "",
        "iban": "",
        "bic": "",
    },
    "export_dir": "exports",
    "last_invoice_dir": "",
}

# Volgorde voor validatie (expliciet i.p.v. dict-iteratie).
REQUIRED_DEBTOR_KEYS: tuple[str, ...] = ("name", "iban", "bic")

REQUIRED_DEBTOR_MESSAGES: dict[str, str] = {
    "name": "Uw naam of bedrijfsnaam ontbreekt. Vul deze in via Instellingen.",
    "iban": "Uw IBAN ontbreekt of is ongeldig. Vul dit in via Instellingen.",
    "bic": "Uw BIC ontbreekt. Vul dit in via Instellingen.",
}

DEBTOR_MISSING_KEY_FALLBACK: str = "Onbekend veld ontbreekt: {key}"

# Metadata voor optionele instellingen in de UI (exportmap, e-mail, …); niet verplicht bij export.
_OPTIONAL_DEBTOR_FIELDS: tuple[tuple[str, str, str], ...] = ()


def merge_debtor_with_defaults(debtor: Any) -> dict[str, str]:
    """Voeg alle sleutels van ``DEFAULT_SETTINGS['debtor']`` toe; ontbrekende → lege string."""
    template: dict[str, Any] = DEFAULT_SETTINGS["debtor"]
    if not isinstance(debtor, dict):
        return {k: "" for k in template}
    merged: dict[str, str] = {}
    for key in template:
        if key in debtor:
            val = debtor[key]
            merged[key] = "" if val is None else str(val).strip()
        else:
            merged[key] = ""
    return merged


def normalize_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Pas debtor-merge en minimum-defaults toe op een geladen settings-dict."""
    out: dict[str, Any] = dict(data)
    out["debtor"] = merge_debtor_with_defaults(out.get("debtor"))
    exp = out.get("export_dir")
    if not isinstance(exp, str) or not str(exp).strip():
        out["export_dir"] = str(DEFAULT_SETTINGS["export_dir"])
    lid = out.get("last_invoice_dir")
    if lid is None or not isinstance(lid, str):
        out["last_invoice_dir"] = ""
    else:
        out["last_invoice_dir"] = str(lid).strip()
    return out


def resolve_settings_path(
    raw: str,
    *,
    base_dir: Path,
) -> Path:
    """Maak een opslagpad (zoals export_dir of last_invoice_dir) absoluut t.o.v. ``base_dir``."""
    p = Path(str(raw or "").strip())
    if not p.is_absolute():
        p = base_dir / p
    return p


def validate_debtor_for_export(debtor: Any) -> Optional[str]:
    """Controleer verplichte debtor-velden voor SEPA-export. ``None`` = ok, anders fouttekst."""
    if not isinstance(debtor, dict):
        return "Uw gegevens ontbreken. Vul deze in via Instellingen."
    for key in REQUIRED_DEBTOR_KEYS:
        if key not in debtor:
            return DEBTOR_MISSING_KEY_FALLBACK.format(key=key)
    if not str(debtor.get("name") or "").strip():
        return REQUIRED_DEBTOR_MESSAGES["name"]
    iban = clean_iban(str(debtor.get("iban") or ""))
    if not iban or not is_plausible_iban(iban):
        return REQUIRED_DEBTOR_MESSAGES["iban"]
    if not str(debtor.get("bic") or "").strip():
        return REQUIRED_DEBTOR_MESSAGES["bic"]
    return None


def load_settings(path: str = "data/settings.json") -> dict[str, Any]:
    """Laad het settings-bestand. Ontbreekt of corrupt, dan defaults en normalisatie."""
    p = Path(path)
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        data = normalize_settings(deepcopy(DEFAULT_SETTINGS))
        save_settings(data, path)
        return data
    except OSError:
        return normalize_settings(deepcopy(DEFAULT_SETTINGS))

    try:
        parsed = json.loads(raw or "")
        if not isinstance(parsed, dict):
            raise ValueError("top-level must be a dict")
    except Exception:
        data = normalize_settings(deepcopy(DEFAULT_SETTINGS))
        save_settings(data, path)
        return data

    return normalize_settings(parsed)


def save_settings(settings: dict[str, Any], path: str = "data/settings.json") -> bool:
    """Sla het settings-dict op als JSON. Bij IO-fout False, anders True."""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(settings, indent=2, ensure_ascii=False)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
            f.write("\n")
    except OSError:
        return False
    return True
