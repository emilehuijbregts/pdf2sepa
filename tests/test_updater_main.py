"""Tests for packaging/updater_main.py and logic/app_updater.py."""

from __future__ import annotations

import zipfile
from pathlib import Path

from logic.app_updater import (
    _apply_update,
    _extract_zip,
    _verify_app,
    run_update,
)


def _write_valid_app(app_dir: Path, *, version: str) -> None:
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "PDF2SEPA.exe").write_text(f"version={version}", encoding="utf-8")
    (app_dir / "_internal").mkdir(parents=True, exist_ok=True)
    (app_dir / "_internal" / "marker.txt").write_text(version, encoding="utf-8")


def _write_update_zip(zip_path: Path, *, version: str, include_exe: bool = True) -> None:
    with zipfile.ZipFile(zip_path, "w") as zf:
        if include_exe:
            zf.writestr("PDF2SEPA.exe", f"version={version}")
        zf.writestr("_internal/marker.txt", version)


def test_extract_zip_and_verify_app(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    zip_path = tmp_path / "update.zip"
    _write_update_zip(zip_path, version="new")
    _extract_zip(zip_path, app_dir)
    _verify_app(app_dir)


def test_apply_update_replaces_existing_app(tmp_path: Path) -> None:
    install_root = tmp_path / "PDF2SEPA"
    app_dir = install_root / "app"
    zip_path = tmp_path / "update.zip"

    _write_valid_app(app_dir, version="old")
    _write_update_zip(zip_path, version="new")

    _apply_update(zip_path, app_dir, install_root)

    assert (app_dir / "PDF2SEPA.exe").read_text(encoding="utf-8") == "version=new"
    assert (app_dir / "_internal" / "marker.txt").read_text(encoding="utf-8") == "new"


def test_run_update_keeps_old_app_when_zip_is_invalid(tmp_path: Path, monkeypatch) -> None:
    install_root = tmp_path / "PDF2SEPA"
    app_dir = install_root / "app"
    zip_path = tmp_path / "update.zip"

    _write_valid_app(app_dir, version="old")
    _write_update_zip(zip_path, version="broken", include_exe=False)

    monkeypatch.setattr("logic.app_updater._wait_for_pid", lambda _pid: None)
    monkeypatch.setattr("logic.app_updater._restart_app", lambda _app_dir: None)

    result = run_update(
        zip_path=zip_path,
        app_dir=app_dir,
        install_root=install_root,
        pid=0,
    )

    assert result == 1
    assert (app_dir / "PDF2SEPA.exe").read_text(encoding="utf-8") == "version=old"


def test_run_update_applies_valid_zip(tmp_path: Path, monkeypatch) -> None:
    install_root = tmp_path / "PDF2SEPA"
    app_dir = install_root / "app"
    zip_path = tmp_path / "update.zip"

    _write_valid_app(app_dir, version="old")
    _write_update_zip(zip_path, version="new")

    restarted: list[Path] = []

    monkeypatch.setattr("logic.app_updater._wait_for_pid", lambda _pid: None)
    monkeypatch.setattr("logic.app_updater._restart_app", lambda app_path: restarted.append(app_path))

    result = run_update(
        zip_path=zip_path,
        app_dir=app_dir,
        install_root=install_root,
        pid=0,
    )

    assert result == 0
    assert (app_dir / "PDF2SEPA.exe").read_text(encoding="utf-8") == "version=new"
    assert restarted == [app_dir]
