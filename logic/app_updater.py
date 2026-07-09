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

from logic.auto_update import UpdateInfo, download_update
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


def _swap_staged_app(staging_dir: Path, app_dir: Path, backups_dir: Path) -> Path | None:
    """Atomically replace app_dir with verified staging contents.

    Returns the backup path of the previous app install, if any.
    """
    app_backup: Path | None = None
    if app_dir.is_dir():
        app_backup = _backup_app(app_dir, backups_dir, "pre_update")
        shutil.rmtree(app_dir)
    shutil.move(str(staging_dir), str(app_dir))
    return app_backup


def _verify_app(app_dir: Path) -> None:
    exe = app_dir / "PDF2SEPA.exe"
    internal = app_dir / "_internal"
    if not exe.is_file():
        raise FileNotFoundError(f"Missing {exe}")
    if not internal.is_dir():
        raise FileNotFoundError(f"Missing {internal}")


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
        app_backup = _swap_staged_app(staging_dir, app_dir, backups_dir)
        if app_backup is not None:
            logger.info("Previous app backed up to %s", app_backup)
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def _log_file_hint(install_root: Path) -> str:
    return str(install_root / "logs" / "update.log")


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

        logger.info("Waiting for process %s to exit", pid)
        _wait_for_pid(pid)

        window.show_installing()
        app.processEvents()

        data_dir = install_root / "data"
        _backup_data(data_dir, backups_dir)
        _apply_update(resolved_zip, app_dir, install_root)
        logger.info("Update applied successfully")

        window.show_restarting()
        app.processEvents()
        _restart_app(app_dir)
        logger.info("Restarted app from %s", app_dir / "PDF2SEPA.exe")
        window.close_on_success()
        app.processEvents()
        return 0
    except Exception:
        logger.exception("Update failed")
        latest_backup = sorted(backups_dir.glob("app_pre_update_*"), reverse=True)
        app_backup = latest_backup[0] if latest_backup else None
        if app_backup is not None and app_backup.is_dir() and not (app_dir / "PDF2SEPA.exe").is_file():
            try:
                _rollback_app(app_backup, app_dir)
                logger.info("Rolled back to %s", app_backup)
                _restart_app(app_dir)
                logger.info("Restarted app from rollback at %s", app_dir / "PDF2SEPA.exe")
            except Exception:
                logger.exception("Rollback failed")
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
        _wait_for_pid(pid)
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
        latest_backup = sorted(backups_dir.glob("app_pre_update_*"), reverse=True)
        app_backup = latest_backup[0] if latest_backup else None
        if app_backup is not None and app_backup.is_dir() and not (app_dir / "PDF2SEPA.exe").is_file():
            try:
                _rollback_app(app_backup, app_dir)
                logger.info("Rolled back to %s", app_backup)
                _restart_app(app_dir)
                logger.info("Restarted app from rollback at %s", app_dir / "PDF2SEPA.exe")
            except Exception:
                logger.exception("Rollback failed")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF2SEPA updater")
    parser.add_argument("--zip", help="Path to update zip (optional if --url is set)")
    parser.add_argument("--url", help="Update download URL")
    parser.add_argument("--sha256", help="Expected SHA256 of update zip")
    parser.add_argument("--version", help="Target update version")
    parser.add_argument("--pid", type=int, default=0, help="PID to wait for")
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
        use_gui=not args.no_gui,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
