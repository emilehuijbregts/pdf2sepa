"""Tests for logic/auto_update.py."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from logic.auto_update import (
    UpdateInfo,
    is_newer_version,
    offer_update_if_available,
    version_tuple,
)


def _install_fake_message_box(monkeypatch: pytest.MonkeyPatch, *, accept: bool) -> None:
    class FakeMessageBox:
        class StandardButton:
            Yes = 1
            No = 2

        @staticmethod
        def question(*_args, **_kwargs):
            return FakeMessageBox.StandardButton.Yes if accept else FakeMessageBox.StandardButton.No

        @staticmethod
        def information(*_args, **_kwargs):
            return None

        @staticmethod
        def warning(*_args, **_kwargs):
            return None

    fake_widgets = types.ModuleType("PySide6.QtWidgets")
    fake_widgets.QMessageBox = FakeMessageBox
    fake_pyside6 = types.ModuleType("PySide6")
    fake_pyside6.QtWidgets = fake_widgets
    monkeypatch.setitem(sys.modules, "PySide6", fake_pyside6)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", fake_widgets)


def test_version_tuple_parses_semver() -> None:
    assert version_tuple("1.0.2") == (1, 0, 2)


def test_version_tuple_strips_prerelease_suffix() -> None:
    assert version_tuple("1.0.1-test2") == (1, 0, 1)


def test_is_newer_version() -> None:
    assert is_newer_version("1.0.2", "1.0.1")
    assert not is_newer_version("1.0.1", "1.0.2")
    assert not is_newer_version("1.0.1", "1.0.1")


def test_offer_update_if_available_returns_false_when_no_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("logic.auto_update.check_for_update", lambda: None)

    assert offer_update_if_available(auto_accept=False) is False


def test_offer_update_if_available_returns_false_when_user_declines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "logic.auto_update.check_for_update",
        lambda: UpdateInfo(version="9.9.9", url="https://example.com/update.zip", sha256="abc"),
    )
    _install_fake_message_box(monkeypatch, accept=False)

    assert offer_update_if_available(auto_accept=False) is False


def test_offer_update_if_available_launches_updater_when_user_accepts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "logic.auto_update.check_for_update",
        lambda: UpdateInfo(version="9.9.9", url="https://example.com/update.zip", sha256="abc"),
    )
    _install_fake_message_box(monkeypatch, accept=True)

    zip_path = tmp_path / "update.zip"
    zip_path.write_bytes(b"zip")

    monkeypatch.setattr("logic.auto_update.download_update", lambda _info: zip_path)
    launch_updater = MagicMock()
    monkeypatch.setattr("logic.auto_update.launch_updater", launch_updater)

    assert offer_update_if_available(auto_accept=False) is True
    launch_updater.assert_called_once_with(zip_path)
