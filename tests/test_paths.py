"""Tests for logic/paths.py (bootstrap gegevensmap)."""

from __future__ import annotations

import json
from pathlib import Path

from logic.paths import (
    bootstrap_config_path,
    default_user_data_dir,
    read_user_data_root,
    write_user_data_root,
)


def test_default_user_data_dir(tmp_path: Path) -> None:
    app_base = tmp_path / "app"
    app_base.mkdir()
    assert default_user_data_dir(app_base) == (app_base / "data").resolve()


def test_bootstrap_path_dev_under_data(tmp_path: Path) -> None:
    app_base = tmp_path / "repo"
    app_base.mkdir()
    p = bootstrap_config_path(app_base)
    assert p == app_base / "data" / "data_root.json"


def test_read_user_data_root_missing_bootstrap_uses_default(tmp_path: Path) -> None:
    app_base = tmp_path / "app"
    app_base.mkdir()
    assert read_user_data_root(app_base) == default_user_data_dir(app_base)


def test_read_write_roundtrip(tmp_path: Path) -> None:
    app_base = tmp_path / "app"
    app_base.mkdir()
    target = tmp_path / "server_data"
    target.mkdir()
    assert write_user_data_root(target, app_base) is True
    assert read_user_data_root(app_base) == target.resolve()
    boot = bootstrap_config_path(app_base)
    data = json.loads(boot.read_text(encoding="utf-8"))
    assert data["user_data_directory"] == str(target.resolve())
