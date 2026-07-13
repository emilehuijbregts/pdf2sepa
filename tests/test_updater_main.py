"""Tests for packaging/updater_main.py and logic/app_updater.py."""

from __future__ import annotations

import logging
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from logic.app_updater import (
    _apply_update,
    _attempt_rollback,
    _extract_zip,
    _is_app_healthy,
    _refresh_updater,
    _release_cwd_lock,
    _relocate_updater_to_install_root,
    _rename_with_retry,
    _replace_app_from_staging,
    _resolve_app_staging_root,
    _resolve_updater_staging_root,
    _swap_staged_app,
    _verify_app,
    run_update,
)
from logic.auto_update import UPDATER_DIR_NAME, UPDATER_EXE_NAME, UpdateInfo


def _write_valid_app(app_dir: Path, *, version: str, include_python_dll: bool = True) -> None:
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "PDF2SEPA.exe").write_text(f"version={version}", encoding="utf-8")
    (app_dir / "_internal").mkdir(parents=True, exist_ok=True)
    (app_dir / "_internal" / "marker.txt").write_text(version, encoding="utf-8")
    if include_python_dll:
        (app_dir / "_internal" / "python312.dll").write_bytes(b"python-runtime")


def _write_update_zip(
    zip_path: Path,
    *,
    version: str,
    include_exe: bool = True,
    include_python_dll: bool = True,
) -> None:
    with zipfile.ZipFile(zip_path, "w") as zf:
        if include_exe:
            zf.writestr("PDF2SEPA.exe", f"version={version}")
        zf.writestr("_internal/marker.txt", version)
        if include_python_dll:
            zf.writestr("_internal/python312.dll", b"python-runtime")


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
        update_info=None,
        app_dir=app_dir,
        install_root=install_root,
        pid=0,
        use_gui=False,
    )

    assert result == 1
    assert (app_dir / "PDF2SEPA.exe").read_text(encoding="utf-8") == "version=old"


def _patch_successful_headless_exit(monkeypatch) -> None:
    monkeypatch.setattr("logic.app_updater._terminate_updater_success", lambda: None)


def test_run_update_applies_valid_zip(tmp_path: Path, monkeypatch) -> None:
    install_root = tmp_path / "PDF2SEPA"
    app_dir = install_root / "app"
    zip_path = tmp_path / "update.zip"

    _write_valid_app(app_dir, version="old")
    _write_update_zip(zip_path, version="new")

    restarted: list[Path] = []

    monkeypatch.setattr("logic.app_updater._wait_for_pid", lambda _pid: None)
    monkeypatch.setattr("logic.app_updater._restart_app", lambda app_path: restarted.append(app_path))
    _patch_successful_headless_exit(monkeypatch)

    result = run_update(
        zip_path=zip_path,
        update_info=None,
        app_dir=app_dir,
        install_root=install_root,
        pid=0,
        use_gui=False,
    )

    assert result == 0
    assert (app_dir / "PDF2SEPA.exe").read_text(encoding="utf-8") == "version=new"
    assert restarted == [app_dir]


def test_run_update_downloads_from_manifest_when_no_zip(tmp_path: Path, monkeypatch) -> None:
    install_root = tmp_path / "PDF2SEPA"
    app_dir = install_root / "app"
    zip_path = tmp_path / "update.zip"
    _write_valid_app(app_dir, version="old")
    _write_update_zip(zip_path, version="new")

    info = UpdateInfo(version="new", url="https://example.com/update.zip", sha256="abc")

    monkeypatch.setattr("logic.app_updater._wait_for_pid", lambda _pid: None)
    monkeypatch.setattr("logic.app_updater._restart_app", lambda _app_dir: None)
    monkeypatch.setattr("logic.app_updater.download_update", lambda _info, **_kwargs: zip_path)
    _patch_successful_headless_exit(monkeypatch)

    result = run_update(
        zip_path=None,
        update_info=info,
        app_dir=app_dir,
        install_root=install_root,
        pid=0,
        use_gui=False,
    )

    assert result == 0
    assert (app_dir / "PDF2SEPA.exe").read_text(encoding="utf-8") == "version=new"


def test_run_with_gui_hands_off_install_to_headless_process(tmp_path: Path, monkeypatch) -> None:
    install_root = tmp_path / "PDF2SEPA"
    app_dir = install_root / "app"
    zip_path = tmp_path / "update.zip"
    _write_valid_app(app_dir, version="old")
    zip_path.write_bytes(b"zip")

    spawn = MagicMock()
    monkeypatch.setattr("logic.app_updater.bootstrap_pyside6", lambda _root: None)
    monkeypatch.setattr("logic.app_updater._spawn_headless_install", spawn)
    monkeypatch.setattr("logic.app_updater._terminate_gui_updater", lambda: None)
    monkeypatch.setattr("logic.app_updater.os.getpid", lambda: 7777)

    fake_qt = MagicMock()
    fake_window = MagicMock()
    fake_qt.QApplication.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", fake_qt)
    monkeypatch.setitem(
        sys.modules,
        "ui.update_progress_window",
        MagicMock(UpdateProgressWindow=MagicMock(return_value=fake_window)),
    )

    from logic.app_updater import _run_with_gui

    _run_with_gui(
        zip_path=zip_path,
        update_info=None,
        app_dir=app_dir,
        install_root=install_root,
        pid=4242,
        logger=logging.getLogger("test.updater"),
    )

    spawn.assert_called_once_with(
        zip_path=zip_path,
        app_dir=app_dir,
        install_root=install_root,
        pid=4242,
        parent_pid=7777,
    )
    fake_window.close_on_success.assert_called_once()


def test_run_update_waits_for_parent_pid_before_apply(tmp_path: Path, monkeypatch) -> None:
    install_root = tmp_path / "PDF2SEPA"
    app_dir = install_root / "app"
    zip_path = tmp_path / "update.zip"
    _write_valid_app(app_dir, version="old")
    _write_update_zip(zip_path, version="new")

    waited: list[int] = []
    sleep_calls: list[float] = []

    monkeypatch.setattr(
        "logic.app_updater._wait_for_pids",
        lambda *pids: waited.extend(p for p in pids if p > 0),
    )
    monkeypatch.setattr("logic.app_updater._restart_app", lambda _app_dir: None)
    monkeypatch.setattr(
        "logic.app_updater.time.sleep",
        lambda sec: sleep_calls.append(sec),
    )
    monkeypatch.setattr(sys, "platform", "win32")
    _patch_successful_headless_exit(monkeypatch)

    result = run_update(
        zip_path=zip_path,
        update_info=None,
        app_dir=app_dir,
        install_root=install_root,
        pid=11,
        parent_pid=22,
        use_gui=False,
    )

    assert result == 0
    assert waited == [11, 22]
    assert sleep_calls == [2.0]
    assert (app_dir / "PDF2SEPA.exe").read_text(encoding="utf-8") == "version=new"


def test_release_cwd_lock_moves_out_of_app_dir(tmp_path: Path, monkeypatch) -> None:
    install_root = tmp_path / "PDF2SEPA"
    app_dir = install_root / "app"
    app_dir.mkdir(parents=True)
    monkeypatch.chdir(app_dir)

    _release_cwd_lock(app_dir, install_root)

    assert Path.cwd().resolve() == install_root.resolve()


def test_swap_falls_back_to_in_place_replace_when_rename_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_root = tmp_path / "PDF2SEPA"
    app_dir = install_root / "app"
    staging_dir = install_root / "staging"
    backups_dir = install_root / "backups"

    _write_valid_app(app_dir, version="old")
    _write_valid_app(staging_dir, version="new")

    def _fail_rename(src: Path, dst: Path, **_kwargs) -> None:
        if src == app_dir:
            raise PermissionError("locked")

    monkeypatch.setattr("logic.app_updater._rename_with_retry", _fail_rename)

    backup = _swap_staged_app(staging_dir, app_dir, backups_dir, install_root)

    assert backup is not None
    assert (app_dir / "PDF2SEPA.exe").read_text(encoding="utf-8") == "version=new"
    assert not staging_dir.exists()


def test_rename_with_retry_eventually_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    attempts = {"count": 0}

    original_rename = Path.rename

    def _flaky_rename(self: Path, target: Path) -> None:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError("locked")
        original_rename(self, target)

    monkeypatch.setattr(Path, "rename", _flaky_rename)

    _rename_with_retry(src, dst, attempts=5, delay_sec=0)

    assert not src.exists()
    assert dst.is_dir()
    assert attempts["count"] == 3


def test_run_update_uses_gui_flow_when_enabled(tmp_path: Path, monkeypatch) -> None:
    install_root = tmp_path / "PDF2SEPA"
    app_dir = install_root / "app"
    zip_path = tmp_path / "update.zip"
    _write_valid_app(app_dir, version="old")
    _write_update_zip(zip_path, version="new")

    gui_runner = MagicMock(return_value=0)
    monkeypatch.setattr("logic.app_updater._run_with_gui", gui_runner)
    monkeypatch.setattr(sys, "platform", "win32")

    result = run_update(
        zip_path=zip_path,
        update_info=None,
        app_dir=app_dir,
        install_root=install_root,
        pid=0,
        use_gui=True,
    )

    assert result == 0
    gui_runner.assert_called_once()


def test_verify_app_requires_python_dll(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    _write_valid_app(app_dir, version="old", include_python_dll=False)

    with pytest.raises(FileNotFoundError, match="Python runtime DLL"):
        _verify_app(app_dir)


def test_swap_uses_rename_not_rmtree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_root = tmp_path / "PDF2SEPA"
    app_dir = install_root / "app"
    staging_dir = install_root / "staging"
    backups_dir = install_root / "backups"

    _write_valid_app(app_dir, version="old")
    _write_valid_app(staging_dir, version="new")

    rmtree_calls: list[Path] = []
    original_rmtree = __import__("shutil").rmtree

    def _track_rmtree(path: Path, *args, **kwargs) -> None:
        rmtree_calls.append(Path(path))
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("logic.app_updater.shutil.rmtree", _track_rmtree)

    _swap_staged_app(staging_dir, app_dir, backups_dir, install_root)

    assert (app_dir / "PDF2SEPA.exe").read_text(encoding="utf-8") == "version=new"
    assert not any(call == app_dir for call in rmtree_calls)


def test_rollback_on_unhealthy_app(tmp_path: Path) -> None:
    install_root = tmp_path / "PDF2SEPA"
    app_dir = install_root / "app"
    backups_dir = install_root / "backups"

    _write_valid_app(app_dir, version="old")
    backup = backups_dir / "app_pre_update_test"
    _write_valid_app(backup, version="old")

    (app_dir / "PDF2SEPA.exe").write_text("broken", encoding="utf-8")
    (app_dir / "_internal" / "python312.dll").unlink()

    logger = logging.getLogger("test.updater")
    rolled_back = _attempt_rollback(
        app_dir=app_dir,
        backups_dir=backups_dir,
        install_root=install_root,
        logger=logger,
        restart=False,
    )

    assert rolled_back is True
    assert _is_app_healthy(app_dir)
    assert (app_dir / "PDF2SEPA.exe").read_text(encoding="utf-8") == "version=old"


def _write_nested_update_zip(
    zip_path: Path,
    *,
    version: str,
    include_updater: bool = False,
) -> None:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("app/PDF2SEPA.exe", f"version={version}")
        zf.writestr("app/_internal/marker.txt", version)
        zf.writestr("app/_internal/python312.dll", b"python-runtime")
        if include_updater:
            zf.writestr(f"{UPDATER_DIR_NAME}/{UPDATER_EXE_NAME}", f"updater={version}")
            zf.writestr(f"{UPDATER_DIR_NAME}/_internal/marker.txt", version)


def test_resolve_nested_update_zip_layout(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "app").mkdir()
    (staging / "app" / "PDF2SEPA.exe").write_text("version=new", encoding="utf-8")
    updater_dir = staging / UPDATER_DIR_NAME
    updater_dir.mkdir()
    (updater_dir / UPDATER_EXE_NAME).write_text("updater=new", encoding="utf-8")

    assert _resolve_app_staging_root(staging).name == "app"
    assert _resolve_updater_staging_root(staging) == updater_dir


def test_apply_update_refreshes_updater_from_nested_zip(tmp_path: Path) -> None:
    install_root = tmp_path / "PDF2SEPA"
    app_dir = install_root / "app"
    zip_path = tmp_path / "update.zip"

    _write_valid_app(app_dir, version="old")
    (install_root / UPDATER_EXE_NAME).write_text("legacy-updater", encoding="utf-8")
    _write_nested_update_zip(zip_path, version="new", include_updater=True)

    _apply_update(zip_path, app_dir, install_root)

    assert (app_dir / "PDF2SEPA.exe").read_text(encoding="utf-8") == "version=new"
    updater_exe = install_root / UPDATER_DIR_NAME / UPDATER_EXE_NAME
    assert updater_exe.read_text(encoding="utf-8") == "updater=new"
    assert not (install_root / UPDATER_EXE_NAME).exists()


def test_refresh_updater_replaces_existing_tree(tmp_path: Path) -> None:
    install_root = tmp_path / "PDF2SEPA"
    staging = tmp_path / "staging_updater"
    staging.mkdir()
    (staging / UPDATER_EXE_NAME).write_text("fresh", encoding="utf-8")

    target_dir = install_root / UPDATER_DIR_NAME
    target_dir.mkdir(parents=True)
    (target_dir / UPDATER_EXE_NAME).write_text("old", encoding="utf-8")
    (install_root / UPDATER_EXE_NAME).write_text("legacy", encoding="utf-8")

    _refresh_updater(staging, install_root)

    assert (target_dir / UPDATER_EXE_NAME).read_text(encoding="utf-8") == "fresh"
    assert not (install_root / UPDATER_EXE_NAME).exists()


def test_relocate_updater_to_install_root(tmp_path: Path) -> None:
    install_root = tmp_path / "PDF2SEPA"
    app_dir = install_root / "app"
    _write_valid_app(app_dir, version="new")
    (app_dir / UPDATER_EXE_NAME).write_text("updater", encoding="utf-8")

    _relocate_updater_to_install_root(app_dir, install_root)

    assert (install_root / UPDATER_DIR_NAME / UPDATER_EXE_NAME).read_text(encoding="utf-8") == "updater"
    assert not (app_dir / UPDATER_EXE_NAME).exists()
    assert not (install_root / UPDATER_EXE_NAME).exists()
