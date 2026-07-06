"""Tests for packaging/updater_main.py."""

from __future__ import annotations

import zipfile
from pathlib import Path

from logic.app_updater import _extract_zip, _verify_app


def test_extract_zip_and_verify_app(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    zip_path = tmp_path / "update.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("PDF2SEPA.exe", b"exe")
        zf.writestr("_internal/foo.dll", b"dll")
    _extract_zip(zip_path, app_dir)
    _verify_app(app_dir)
