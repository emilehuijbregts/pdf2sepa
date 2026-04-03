"""Bootstrap-locatie voor de gekozen gegevensmap (instellingen, leveranciers).

Ontwikkeling: ``{app_base}/data/data_root.json``. Gebundelde app (PyInstaller): `%LOCALAPPDATA%/PDF2SEPA/data_root.json`.

Handmatig testen: in Instellingen een UNC-pad kiezen (bijv. ``\\\\server\\share\\PDF2SEPA``);
de map moet voor de Windows-gebruiker schrijfbaar zijn.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def bootstrap_config_path(app_base: Path) -> Path:
    if getattr(sys, "frozen", False):
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "PDF2SEPA" / "data_root.json"
        return Path.home() / "AppData" / "Local" / "PDF2SEPA" / "data_root.json"
    return app_base / "data" / "data_root.json"


def default_user_data_dir(app_base: Path) -> Path:
    """Standaard gegevensmap (zelfde als historisch ``app_base/data``)."""
    return (app_base / "data").resolve()


def read_user_data_root(app_base: Path) -> Path:
    """Lees gegevensmap uit bootstrap; bij ontbreken of fout → ``default_user_data_dir``."""
    p = bootstrap_config_path(app_base)
    fallback = default_user_data_dir(app_base)
    try:
        if not p.exists():
            return fallback
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            return fallback
        s = str(data.get("user_data_directory") or "").strip()
        if not s:
            return fallback
        return Path(s).expanduser().resolve()
    except Exception:
        logger.debug("Bootstrap gegevensmap lezen mislukt", exc_info=True)
        return fallback


def write_user_data_root(directory: Path, app_base: Path) -> bool:
    """Schrijf absolute gegevensmap naar bootstrap-bestand."""
    path = bootstrap_config_path(app_base)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        payload = {"user_data_directory": str(directory.resolve())}
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(path))
        return True
    except OSError:
        logger.debug("Bootstrap gegevensmap schrijven mislukt", exc_info=True)
        return False
