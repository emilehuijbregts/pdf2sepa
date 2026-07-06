"""Tests for logic/runtime_paths.py."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import logic.runtime_paths as runtime_paths
from logic.runtime_paths import (
    app_icon_path,
    app_root,
    bundled_engine_data_path,
    configure_tesseract_runtime,
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


def test_bundled_engine_data_path_dev() -> None:
    path = bundled_engine_data_path("strategy_engine_bundle.json")
    assert path.name == "strategy_engine_bundle.json"
    assert path.is_file()


def test_bundled_engine_data_path_frozen_uses_meipass(
    monkeypatch, tmp_path: Path
) -> None:
    internal = tmp_path / "_internal" / "data"
    internal.mkdir(parents=True)
    bundle = internal / "strategy_engine_bundle.json"
    bundle.write_text("{}", encoding="utf-8")

    fake_exe = tmp_path / "PDF2SEPA.exe"
    fake_exe.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)

    assert bundled_engine_data_path("strategy_engine_bundle.json") == bundle


def test_tesseract_path_none_without_bundle() -> None:
    assert tesseract_path() is None


def test_tesseract_path_bundled_frozen(monkeypatch, tmp_path: Path) -> None:
    tess_dir = tmp_path / "_internal" / "tesseract"
    tess_dir.mkdir(parents=True)
    (tess_dir / "tesseract.exe").touch()
    (tess_dir / "tessdata").mkdir()

    fake_exe = tmp_path / "PDF2SEPA.exe"
    fake_exe.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)

    assert tesseract_path() == tess_dir / "tesseract.exe"


def test_configure_tesseract_runtime_sets_env(monkeypatch, tmp_path: Path) -> None:
    tess_dir = tmp_path / "_internal" / "tesseract"
    tess_dir.mkdir(parents=True)
    (tess_dir / "tesseract.exe").touch()
    (tess_dir / "tessdata").mkdir()

    fake_exe = tmp_path / "PDF2SEPA.exe"
    fake_exe.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin")

    configured = configure_tesseract_runtime()
    assert configured == tess_dir / "tesseract.exe"
    assert os.environ["TESSDATA_PREFIX"] == str(tess_dir)
    assert os.environ["PATH"].startswith(str(tess_dir))


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
