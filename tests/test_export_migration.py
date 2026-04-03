"""Tests for legacy export_dir migration (gegevensmap vs oude project/exports)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from logic.settings import DEFAULT_SETTINGS, apply_legacy_export_dir_migration


def test_migration_pins_absolute_when_legacy_exports_exists(tmp_path: Path) -> None:
    app_base = tmp_path / "app"
    app_base.mkdir()
    (app_base / "exports").mkdir()
    user_data = app_base / "data"
    user_data.mkdir()

    settings = deepcopy(DEFAULT_SETTINGS)
    assert settings["export_dir"] == "exports"

    assert apply_legacy_export_dir_migration(settings, user_data_dir=user_data, app_base=app_base) is True
    assert settings["export_dir"] == str((app_base / "exports").resolve())


def test_no_migration_when_data_exports_already_exists(tmp_path: Path) -> None:
    app_base = tmp_path / "app"
    app_base.mkdir()
    (app_base / "exports").mkdir()
    user_data = app_base / "data"
    user_data.mkdir()
    (user_data / "exports").mkdir()

    settings = deepcopy(DEFAULT_SETTINGS)
    assert apply_legacy_export_dir_migration(settings, user_data_dir=user_data, app_base=app_base) is False
    assert settings["export_dir"] == "exports"


def test_no_migration_when_export_not_default(tmp_path: Path) -> None:
    app_base = tmp_path / "app"
    app_base.mkdir()
    user_data = app_base / "data"
    user_data.mkdir()

    settings = {**DEFAULT_SETTINGS, "export_dir": "custom"}
    assert apply_legacy_export_dir_migration(settings, user_data_dir=user_data, app_base=app_base) is False


def test_no_migration_when_legacy_exports_missing(tmp_path: Path) -> None:
    app_base = tmp_path / "app"
    app_base.mkdir()
    user_data = app_base / "data"
    user_data.mkdir()

    settings = deepcopy(DEFAULT_SETTINGS)
    assert apply_legacy_export_dir_migration(settings, user_data_dir=user_data, app_base=app_base) is False
