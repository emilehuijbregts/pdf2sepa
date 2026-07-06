"""Tests for logic/runtime_paths.py."""

from __future__ import annotations

import sys
from pathlib import Path

import logic.runtime_paths as runtime_paths
from logic.runtime_paths import (
    app_icon_path,
    app_root,
    data_dir,
    deps_dir,
    is_frozen,
    log_dir,
    tesseract_path,
)


def test_is_frozen_false_in_dev() -> None:
    assert is_frozen() is False


def test_app_root_points_to_project_root() -> None:
    expected = Path(__file__).resolve().parents[1]
    assert app_root() == expected


def test_deps_dir_under_app_root() -> None:
    assert deps_dir() == app_root() / ".deps"


def test_log_and_data_dirs_under_local_pdf2sepa() -> None:
    base = runtime_paths._local_pdf2sepa_dir()
    assert log_dir() == base / "logs"
    assert data_dir() == base / "data"
    assert runtime_paths.install_root() == base
    assert runtime_paths.backups_dir() == base / "backups"


def test_tesseract_path_placeholder() -> None:
    assert tesseract_path() is None


def test_app_icon_path_dev() -> None:
    icon = app_icon_path()
    assert icon is not None
    assert icon.name == "app_icon.png"
    assert icon.is_file()


def test_app_root_when_frozen(monkeypatch, tmp_path: Path) -> None:
    fake_exe = tmp_path / "PDF2SEPA.exe"
    fake_exe.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    assert app_root() == fake_exe.parent.resolve()
