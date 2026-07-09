"""Tests for logic/auto_update.py."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from logic.auto_update import (
    UpdateInfo,
    download_update,
    is_newer_version,
    launch_updater,
    offer_update_if_available,
    version_tuple,
)


def _install_fake_ask_yes_no(monkeypatch: pytest.MonkeyPatch, *, accept: bool) -> None:
    fake_module = types.ModuleType("ui.message_box")
    fake_module.ask_yes_no = lambda *_args, **_kwargs: accept
    monkeypatch.setitem(sys.modules, "ui.message_box", fake_module)


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
    info = UpdateInfo(version="9.9.9", url="https://example.com/update.zip", sha256="abc")
    monkeypatch.setattr("logic.auto_update.check_for_update", lambda: info)
    _install_fake_ask_yes_no(monkeypatch, accept=False)

    assert offer_update_if_available(auto_accept=False) is False


def test_offer_update_if_available_launches_updater_when_user_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    info = UpdateInfo(version="9.9.9", url="https://example.com/update.zip", sha256="abc")
    monkeypatch.setattr("logic.auto_update.check_for_update", lambda: info)
    _install_fake_ask_yes_no(monkeypatch, accept=True)

    launch_updater = MagicMock()
    monkeypatch.setattr("logic.auto_update.launch_updater", launch_updater)

    assert offer_update_if_available(auto_accept=False) is True
    launch_updater.assert_called_once_with(info)


def test_launch_updater_passes_manifest_args(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    info = UpdateInfo(version="2.0.0", url="https://example.com/update.zip", sha256="deadbeef")
    updater_exe = tmp_path / "PDF2SEPAUpdater.exe"
    updater_exe.write_text("updater", encoding="utf-8")

    popen = MagicMock()
    monkeypatch.setattr("logic.auto_update.updater_exe_path", lambda: updater_exe)
    monkeypatch.setattr("logic.auto_update.subprocess.Popen", popen)
    monkeypatch.setattr("logic.auto_update.os.getpid", lambda: 4242)
    monkeypatch.setattr("logic.auto_update.install_root", lambda: tmp_path / "PDF2SEPA")

    launch_updater(info)

    args, kwargs = popen.call_args
    command = args[0]
    assert str(updater_exe) in command
    assert "--url" in command
    assert info.url in command
    assert "--sha256" in command
    assert info.sha256 in command
    assert "--version" in command
    assert info.version in command
    assert "--pid" in command
    assert "4242" in command


def test_download_update_reports_progress(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    info = UpdateInfo(version="2.0.0", url="https://example.com/update.zip", sha256="abc")

    class FakeResponse:
        headers = {"Content-Length": "8"}

        def read(self, size: int = -1) -> bytes:
            if not hasattr(self, "_done"):
                self._done = True
                return b"12345678"
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("logic.auto_update.urllib.request.urlopen", lambda *_a, **_k: FakeResponse())
    monkeypatch.setattr("logic.auto_update.verify_sha256", lambda _path, _digest: True)

    progress: list[tuple[int, int]] = []

    zip_path = download_update(info, dest_dir=tmp_path, progress_cb=lambda done, total: progress.append((done, total)))

    assert zip_path.is_file()
    assert progress[0] == (0, 8)
    assert progress[-1] == (8, 8)
