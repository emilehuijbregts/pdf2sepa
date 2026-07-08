"""Standalone updater for PDF2SEPA (replaces app/ while preserving data/)."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path


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


def run_update(
    *,
    zip_path: Path,
    app_dir: Path,
    install_root: Path,
    pid: int,
) -> int:
    logger = _configure_logging(install_root)
    backups_dir = install_root / "backups"
    data_dir = install_root / "data"

    logger.info("Updater started zip=%s app_dir=%s", zip_path, app_dir)
    if not zip_path.is_file():
        logger.error("Zip not found: %s", zip_path)
        return 1

    try:
        _wait_for_pid(pid)
    except TimeoutError as exc:
        logger.error("%s", exc)
        return 1

    try:
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
    parser.add_argument("--zip", required=True, help="Path to update zip")
    parser.add_argument("--pid", type=int, default=0, help="PID to wait for")
    parser.add_argument("--app-dir", required=True, help="App install directory")
    parser.add_argument("--install-root", required=True, help="PDF2SEPA root directory")
    args = parser.parse_args()
    code = run_update(
        zip_path=Path(args.zip),
        app_dir=Path(args.app_dir),
        install_root=Path(args.install_root),
        pid=args.pid,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
