"""Bootstrap PySide6 from the installed app bundle for the standalone updater."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def bootstrap_pyside6(install_root: Path) -> None:
    """Make PySide6 importable from the existing app/_internal directory."""
    internal = install_root / "app" / "_internal"
    if not internal.is_dir():
        raise FileNotFoundError(f"Missing app internal directory: {internal}")

    internal_str = str(internal)
    if internal_str not in sys.path:
        sys.path.insert(0, internal_str)

    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(internal_str)

    plugins = internal / "PySide6" / "plugins"
    if plugins.is_dir():
        os.environ.setdefault("QT_PLUGIN_PATH", str(plugins))

    pyside6_dir = internal / "PySide6"
    if pyside6_dir.is_dir() and sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(pyside6_dir))
