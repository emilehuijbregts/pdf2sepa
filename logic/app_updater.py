"""Standalone updater for PDF2SEPA (replaces app/ while preserving data/)."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from logic.auto_update import UPDATER_EXE_NAME, UpdateInfo, download_update
from logic.update_qt_bootstrap import bootstrap_pyside6


def _configure_logging(install_root: Path) -> logging.Logger:
    log_dir = install_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "update.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    return logging.getLogger("pdf2sepa.updater")


def _wait_for_pid(pid: int, timeout_sec: float = 120.0) -> None:
    if pid <= 0:
        return
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.5)
    raise TimeoutError(f"Process {pid} did not exit within {timeout_sec}s")


def _wait_for_pids(*pids: int, timeout_sec: float = 120.0) -> None:
    for pid in pids:
        if pid > 0:
            _wait_for_pid(pid, timeout_sec=timeout_sec)


def _release_cwd_lock(app_dir: Path, install_root: Path) -> None:
    """Windows blocks renaming a directory that is any process's current directory."""
    install_root.mkdir(parents=True, exist_ok=True)
    try:
        cwd = Path.cwd().resolve()
        app_resolved = app_dir.resolve()
    except OSError:
        os.chdir(install_root)
        return
    if cwd == app_resolved or app_resolved in cwd.parents:
        os.chdir(install_root)


def _rename_with_retry(
    src: Path,
    dst: Path,
    *,
    attempts: int = 30,
    delay_sec: float = 1.0,
) -> None:
    last_exc: PermissionError | None = None
    for attempt in range(attempts):
        try:
            src.rename(dst)
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                time.sleep(delay_sec)
    if last_exc is not None:
        raise last_exc
    raise PermissionError(f"Could not rename {src} -> {dst}")


def _replace_app_from_staging(staging_dir: Path, app_dir: Path) -> None:
    """Fallback when app/ cannot be renamed: replace contents in place."""
    _clear_dir_contents(app_dir)
    for child in staging_dir.iterdir():
        dest = app_dir / child.name
        if child.is_dir():
            shutil.copytree(child, dest)
        else:
            shutil.copy2(child, dest)


def _terminate_gui_updater() -> None:
    """Exit immediately so Qt DLLs from app/_internal are released without teardown delay."""
    os._exit(0)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _backup_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def _backup_data(data_dir: Path, backups_dir: Path) -> Path | None:
    if not data_dir.is_dir():
        return None
    dest = backups_dir / f"data_{_timestamp()}"
    _backup_tree(data_dir, dest)
    return dest


def _backup_app(app_dir: Path, backups_dir: Path, label: str) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    dest = backups_dir / f"app_{label}_{_timestamp()}"
    _backup_tree(app_dir, dest)
    return dest


def _clear_dir_contents(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _extract_zip(zip_path: Path, app_dir: Path) -> None:
    app_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(app_dir)


def _extract_zip_to_staging(zip_path: Path, staging_dir: Path) -> None:
    """Extract update zip to a temporary directory for verification before swap."""
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    _extract_zip(zip_path, staging_dir)


def _swap_staged_app(
    staging_dir: Path,
    app_dir: Path,
    backups_dir: Path,
    install_root: Path,
) -> Path | None:
    """Atomically replace app_dir with verified staging contents via rename.

    Returns the backup path of the previous app install, if any.
    """
    logger = logging.getLogger("pdf2sepa.updater")
    _release_cwd_lock(app_dir, install_root)

    if not app_dir.is_dir():
        _rename_with_retry(staging_dir, app_dir)
        return None

    retired_dir = install_root / f"app_retired_{_timestamp()}"
    try:
        _rename_with_retry(app_dir, retired_dir)
    except PermissionError:
        logger.warning(
            "Could not rename %s aside; falling back to in-place replace",
            app_dir,
        )
        app_backup = _backup_app(app_dir, backups_dir, "pre_update")
        _replace_app_from_staging(staging_dir, app_dir)
        shutil.rmtree(staging_dir, ignore_errors=True)
        return app_backup

    try:
        _rename_with_retry(staging_dir, app_dir)
    except Exception:
        logger.exception("Staging swap failed; restoring previous app directory")
        if not app_dir.exists() and retired_dir.exists():
            _rename_with_retry(retired_dir, app_dir)
        raise

    app_backup = _backup_app(retired_dir, backups_dir, "pre_update")
    shutil.rmtree(retired_dir, ignore_errors=True)
    return app_backup


def _find_python_runtime_dll(internal_dir: Path) -> Path | None:
    for candidate in sorted(internal_dir.glob("python3*.dll")):
        if candidate.is_file():
            return candidate
    return None


def _verify_app(app_dir: Path) -> None:
    exe = app_dir / "PDF2SEPA.exe"
    internal = app_dir / "_internal"
    if not exe.is_file():
        raise FileNotFoundError(f"Missing {exe}")
    if not internal.is_dir():
        raise FileNotFoundError(f"Missing {internal}")
    python_dll = _find_python_runtime_dll(internal)
    if python_dll is None:
        raise FileNotFoundError(f"Missing Python runtime DLL in {internal}")


def _is_app_healthy(app_dir: Path) -> bool:
    try:
        _verify_app(app_dir)
    except (FileNotFoundError, OSError):
        return False
    return True


def _relocate_updater_to_install_root(app_dir: Path, install_root: Path) -> None:
    """Move updater out of app/ so future swaps do not lock it inside app/."""
    bundled_updater = app_dir / UPDATER_EXE_NAME
    if not bundled_updater.is_file():
        return
    target = install_root / UPDATER_EXE_NAME
    install_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundled_updater, target)
    bundled_updater.unlink(missing_ok=True)


def _attempt_rollback(
    *,
    app_dir: Path,
    backups_dir: Path,
    install_root: Path,
    logger: logging.Logger,
    restart: bool,
) -> bool:
    latest_backup = sorted(backups_dir.glob("app_pre_update_*"), reverse=True)
    app_backup = latest_backup[0] if latest_backup else None
    if app_backup is None or not app_backup.is_dir() or _is_app_healthy(app_dir):
        return False
    try:
        _rollback_app(app_backup, app_dir)
        _relocate_updater_to_install_root(app_dir, install_root)
        logger.info("Rolled back to %s", app_backup)
        if restart:
            _restart_app(app_dir)
            logger.info("Restarted app from rollback at %s", app_dir / "PDF2SEPA.exe")
        return True
    except Exception:
        logger.exception("Rollback failed")
        return False


def _rollback_app(backup_dir: Path, app_dir: Path) -> None:
    if not backup_dir.is_dir():
        raise FileNotFoundError(f"Backup missing: {backup_dir}")
    _clear_dir_contents(app_dir)
    for child in backup_dir.iterdir():
        dest = app_dir / child.name
        if child.is_dir():
            shutil.copytree(child, dest)
        else:
            shutil.copy2(child, dest)


def _restart_app(app_dir: Path) -> None:
    exe = app_dir / "PDF2SEPA.exe"
    subprocess.Popen([str(exe)], cwd=str(app_dir), close_fds=True)


def _apply_update(zip_path: Path, app_dir: Path, install_root: Path) -> None:
    """Apply an update atomically: verify in staging, then swap into app_dir."""
    backups_dir = install_root / "backups"
    staging_dir = install_root / f"temp_app_{_timestamp()}"
    logger = logging.getLogger("pdf2sepa.updater")

    try:
        logger.info("Extracting update to staging dir %s", staging_dir)
        _extract_zip_to_staging(zip_path, staging_dir)
        _verify_app(staging_dir)
        logger.info("Staging verification succeeded for %s", staging_dir)
        app_backup = _swap_staged_app(staging_dir, app_dir, backups_dir, install_root)
        if app_backup is not None:
            logger.info("Previous app backed up to %s", app_backup)
        _verify_app(app_dir)
        _relocate_updater_to_install_root(app_dir, install_root)
        logger.info("Post-swap verification succeeded for %s", app_dir)
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def _log_file_hint(install_root: Path) -> str:
    return str(install_root / "logs" / "update.log")


def _updater_executable() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve().parent.parent / "packaging" / "updater_main.py"


def _spawn_headless_install(
    *,
    zip_path: Path,
    app_dir: Path,
    install_root: Path,
    pid: int,
    parent_pid: int,
) -> None:
    """Start install in a fresh process without Qt DLLs loaded from app/."""
    updater = _updater_executable()
    command = [
        str(updater),
        "--zip",
        str(zip_path),
        "--pid",
        str(pid),
        "--parent-pid",
        str(parent_pid),
        "--app-dir",
        str(app_dir),
        "--install-root",
        str(install_root),
        "--no-gui",
    ]
    if not getattr(sys, "frozen", False):
        command = [sys.executable, *command]
    subprocess.Popen(
        command,
        cwd=str(install_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        close_fds=True,
    )


def _show_windows_error(title: str, message: str) -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except Exception:
        pass


def _run_with_gui(
    *,
    zip_path: Path | None,
    update_info: UpdateInfo | None,
    app_dir: Path,
    install_root: Path,
    pid: int,
    logger: logging.Logger,
) -> int:
    bootstrap_pyside6(install_root)

    from PySide6.QtWidgets import QApplication

    from ui.update_progress_window import UpdateProgressWindow

    app = QApplication(sys.argv)
    version = update_info.version if update_info is not None else ""
    window = UpdateProgressWindow(version=version)
    window.show_downloading()
    app.processEvents()

    backups_dir = install_root / "backups"
    resolved_zip = zip_path
    try:
        if resolved_zip is None:
            if update_info is None:
                raise ValueError("Missing update download info")
            window.show_downloading()
            app.processEvents()

            def _on_progress(done: int, total: int) -> None:
                window.set_download_progress(done, total)
                app.processEvents()

            resolved_zip = download_update(update_info, progress_cb=_on_progress)

        # Install in a separate process: this GUI process loads Qt DLLs from
        # app/_internal, which locks files on Windows and blocks app/ replacement.
        window.show_installing()
        app.processEvents()
        parent_pid = os.getpid()
        logger.info(
            "Handing off install to headless updater (parent_pid=%s, target_pid=%s)",
            parent_pid,
            pid,
        )
        _spawn_headless_install(
            zip_path=resolved_zip,
            app_dir=app_dir,
            install_root=install_root,
            pid=pid,
            parent_pid=parent_pid,
        )
        window.close_on_success()
        app.processEvents()
        _terminate_gui_updater()
    except Exception:
        logger.exception("Update failed")
        _attempt_rollback(
            app_dir=app_dir,
            backups_dir=backups_dir,
            install_root=install_root,
            logger=logger,
            restart=True,
        )
        window.show_error(
            "De update is mislukt. PDF2SEPA blijft op de huidige versie werken.\n\n"
            f"Zie {_log_file_hint(install_root)} voor details."
        )
        app.exec()
        return 1


def run_update(
    *,
    zip_path: Path | None,
    update_info: UpdateInfo | None,
    app_dir: Path,
    install_root: Path,
    pid: int,
    parent_pid: int = 0,
    use_gui: bool = True,
) -> int:
    logger = _configure_logging(install_root)
    logger.info(
        "Updater started zip=%s url=%s app_dir=%s",
        zip_path,
        update_info.url if update_info else None,
        app_dir,
    )

    if zip_path is not None and not zip_path.is_file():
        logger.error("Zip not found: %s", zip_path)
        return 1

    if zip_path is None and update_info is None:
        logger.error("Neither zip path nor update info provided")
        return 1

    if use_gui and sys.platform.startswith("win"):
        try:
            return _run_with_gui(
                zip_path=zip_path,
                update_info=update_info,
                app_dir=app_dir,
                install_root=install_root,
                pid=pid,
                logger=logger,
            )
        except Exception:
            logger.exception("GUI update flow failed; falling back to headless mode")

    if zip_path is None:
        if update_info is None:
            logger.error("Missing update download info")
            return 1
        try:
            zip_path = download_update(update_info)
        except Exception:
            logger.exception("Download failed")
            return 1

    try:
        if parent_pid > 0:
            logger.info("Waiting for GUI updater process %s to exit", parent_pid)
        if pid > 0:
            logger.info("Waiting for target process %s to exit", pid)
        _wait_for_pids(pid, parent_pid)
        _release_cwd_lock(app_dir, install_root)
        if sys.platform.startswith("win"):
            logger.info("Waiting for Windows to release file handles on %s", app_dir)
            time.sleep(2.0)
    except TimeoutError as exc:
        logger.error("%s", exc)
        return 1

    try:
        backups_dir = install_root / "backups"
        data_dir = install_root / "data"
        _backup_data(data_dir, backups_dir)
        _apply_update(zip_path, app_dir, install_root)
        logger.info("Update applied successfully")
        _restart_app(app_dir)
        logger.info("Restarted app from %s", app_dir / "PDF2SEPA.exe")
        return 0
    except Exception:
        logger.exception("Update failed")
        _attempt_rollback(
            app_dir=app_dir,
            backups_dir=backups_dir,
            install_root=install_root,
            logger=logger,
            restart=True,
        )
        _show_windows_error(
            "Update mislukt",
            (
                "De update is mislukt. PDF2SEPA blijft op de huidige versie werken.\n\n"
                f"Zie {_log_file_hint(install_root)} voor details."
            ),
        )
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF2SEPA updater")
    parser.add_argument("--zip", help="Path to update zip (optional if --url is set)")
    parser.add_argument("--url", help="Update download URL")
    parser.add_argument("--sha256", help="Expected SHA256 of update zip")
    parser.add_argument("--version", help="Target update version")
    parser.add_argument("--pid", type=int, default=0, help="PID to wait for")
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=0,
        help="GUI updater PID to wait for before applying (releases DLL locks)",
    )
    parser.add_argument("--app-dir", required=True, help="App install directory")
    parser.add_argument("--install-root", required=True, help="PDF2SEPA root directory")
    parser.add_argument("--no-gui", action="store_true", help="Run without progress window")
    args = parser.parse_args()

    zip_path = Path(args.zip) if args.zip else None
    update_info: UpdateInfo | None = None
    if args.url and args.sha256 and args.version:
        update_info = UpdateInfo(version=args.version, url=args.url, sha256=args.sha256)

    code = run_update(
        zip_path=zip_path,
        update_info=update_info,
        app_dir=Path(args.app_dir),
        install_root=Path(args.install_root),
        pid=args.pid,
        parent_pid=args.parent_pid,
        use_gui=not args.no_gui,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
