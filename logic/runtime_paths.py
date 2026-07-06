"""Single source of truth for runtime file paths (dev + frozen/PyInstaller)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _local_pdf2sepa_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "PDF2SEPA"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PDF2SEPA"
    return Path.home() / ".local" / "share" / "PDF2SEPA"


def install_root() -> Path:
    return _local_pdf2sepa_dir()


def backups_dir() -> Path:
    return install_root() / "backups"


def data_dir() -> Path:
    return _local_pdf2sepa_dir() / "data"


def log_dir() -> Path:
    return _local_pdf2sepa_dir() / "logs"


def deps_dir() -> Path:
    return app_root() / ".deps"


def tesseract_path() -> Path | None:
    return None


def app_icon_path() -> Path | None:
    """Bundled app icon (frozen) or packaging/icons in development."""
    root = app_root()
    for rel in (
        Path("icons") / "app_icon.png",
        Path("_internal") / "icons" / "app_icon.png",
        Path("packaging") / "icons" / "app_icon.png",
    ):
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None
