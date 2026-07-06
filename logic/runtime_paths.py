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


def _meipass() -> Path | None:
    raw = getattr(sys, "_MEIPASS", None)
    return Path(raw) if raw else None


def bundled_engine_data_path(filename: str) -> Path:
    """Shipped parser config (strategy bundle, etc.) — dev repo or PyInstaller _internal."""
    if is_frozen():
        base = _meipass() or (app_root() / "_internal")
        return base / "data" / filename
    return Path(__file__).resolve().parent.parent / "data" / filename


def _tesseract_dir_candidates() -> list[Path]:
    root = app_root()
    candidates: list[Path] = []
    meipass = _meipass()
    if meipass is not None:
        candidates.append(meipass / "tesseract")
    if is_frozen():
        candidates.append(root / "_internal" / "tesseract")
        candidates.append(root / "tesseract")
    candidates.append(root / "packaging" / "tesseract")
    return candidates


def tesseract_path() -> Path | None:
    for directory in _tesseract_dir_candidates():
        for name in ("tesseract.exe", "tesseract"):
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def configure_tesseract_runtime() -> Path | None:
    """Wire bundled Tesseract for PyMuPDF OCR and pytesseract. No-op if not bundled."""
    exe = tesseract_path()
    if exe is None:
        return None

    tess_dir = exe.parent
    tessdata = tess_dir / "tessdata"
    if tessdata.is_dir():
        os.environ.setdefault("TESSDATA_PREFIX", str(tess_dir))

    tess_str = str(tess_dir)
    path_env = os.environ.get("PATH", "")
    if not any(part == tess_str for part in path_env.split(os.pathsep) if part):
        os.environ["PATH"] = tess_str + os.pathsep + path_env

    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = str(exe)
    except ImportError:
        pass

    return exe


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
