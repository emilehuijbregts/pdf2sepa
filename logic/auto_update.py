"""PDF2SEPA auto-update: manifest fetch, download, and updater launch."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from logic.runtime_paths import app_root, install_root
from version import __version__

logger = logging.getLogger(__name__)

UPDATE_MANIFEST_URL = (
    "https://github.com/emilehuijbregts/pdf2sepa/releases/latest/download/latest.json"
)
_FETCH_TIMEOUT_SEC = 8
ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    sha256: str


def version_tuple(version: str) -> tuple[int, ...]:
    core = version.strip().split("-", 1)[0]
    parts: list[int] = []
    for segment in core.split("."):
        if not segment.isdigit():
            break
        parts.append(int(segment))
    return tuple(parts)


def is_newer_version(remote: str, local: str) -> bool:
    return version_tuple(remote) > version_tuple(local)


def fetch_latest_manifest(url: str = UPDATE_MANIFEST_URL) -> dict[str, str] | None:
    req = urllib.request.Request(url, headers={"User-Agent": "PDF2SEPA-updater"})
    try:
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8-sig")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        version = str(data.get("version") or "").strip()
        download_url = str(data.get("url") or "").strip()
        digest = str(data.get("sha256") or "").strip().lower()
        if not version or not download_url or not digest:
            return None
        return {"version": version, "url": download_url, "sha256": digest}
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
        logger.info("Update manifest fetch failed", exc_info=True)
        return None


def check_for_update() -> UpdateInfo | None:
    manifest = fetch_latest_manifest()
    if manifest is None:
        return None
    remote_version = manifest["version"]
    if not is_newer_version(remote_version, __version__):
        return None
    return UpdateInfo(
        version=remote_version,
        url=manifest["url"],
        sha256=manifest["sha256"],
    )


def verify_sha256(path: Path, expected: str) -> bool:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower() == expected.lower()


def download_update(
    info: UpdateInfo,
    dest_dir: Path | None = None,
    *,
    progress_cb: ProgressCallback | None = None,
) -> Path:
    target_dir = dest_dir or Path(tempfile.gettempdir())
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / f"PDF2SEPA-update-{info.version}.zip"
    req = urllib.request.Request(info.url, headers={"User-Agent": "PDF2SEPA-updater"})
    with urllib.request.urlopen(req, timeout=120) as resp, zip_path.open("wb") as out:
        total_header = resp.headers.get("Content-Length")
        total = int(total_header) if total_header and total_header.isdigit() else 0
        downloaded = 0
        if progress_cb is not None:
            progress_cb(0, total)
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            if progress_cb is not None:
                progress_cb(downloaded, total)
    if not verify_sha256(zip_path, info.sha256):
        zip_path.unlink(missing_ok=True)
        raise ValueError("Downloaded update failed SHA256 verification")
    return zip_path


UPDATER_DIR_NAME = "updater"
UPDATER_PENDING_DIR_NAME = "updater_pending"
UPDATER_EXE_NAME = "PDF2SEPAUpdater.exe"


def _is_onedir_updater(path: Path) -> bool:
    return path.is_file() and (path.parent / "_internal").is_dir()


def _onedir_updater_dir(root: Path) -> Path | None:
    candidate = root / UPDATER_DIR_NAME
    if _is_onedir_updater(candidate / UPDATER_EXE_NAME):
        return candidate
    return None


def _legacy_updater_exe_path() -> Path | None:
    candidate = app_root() / UPDATER_EXE_NAME
    return candidate if candidate.is_file() else None


def _legacy_root_updater_exe_path() -> Path | None:
    candidate = install_root() / UPDATER_EXE_NAME
    return candidate if candidate.is_file() else None


def _bundled_onedir_updater_dir() -> Path | None:
    return _onedir_updater_dir(app_root())


def _remove_legacy_onefile_updater(root: Path) -> None:
    legacy = root / UPDATER_EXE_NAME
    if legacy.is_file():
        legacy.unlink(missing_ok=True)
        logger.info("Removed legacy onefile updater at %s", legacy)


def _cleanup_updater_retired_dirs(root: Path) -> None:
    for retired in root.glob("updater_retired_*"):
        if retired.is_dir():
            shutil.rmtree(retired, ignore_errors=True)


def apply_pending_updater_refresh(root: Path | None = None) -> bool:
    """Apply a staged onedir updater refresh when no updater process is running."""
    install = root or install_root()
    pending_dir = install / UPDATER_PENDING_DIR_NAME
    pending_exe = pending_dir / UPDATER_EXE_NAME
    if not _is_onedir_updater(pending_exe):
        return False

    target_dir = install / UPDATER_DIR_NAME
    retired_dir = install / f"updater_retired_{pending_exe.stat().st_mtime_ns}"

    try:
        if target_dir.exists():
            _rename_with_retry_updater(target_dir, retired_dir)
        pending_dir.rename(target_dir)
        _remove_legacy_onefile_updater(install)
        _cleanup_updater_retired_dirs(install)
        if retired_dir.exists():
            shutil.rmtree(retired_dir, ignore_errors=True)
        logger.info("Applied pending updater refresh to %s", target_dir)
        return True
    except (OSError, PermissionError):
        logger.warning("Pending updater refresh deferred", exc_info=True)
        if not target_dir.exists() and retired_dir.exists():
            try:
                _rename_with_retry_updater(retired_dir, target_dir)
            except OSError:
                logger.warning(
                    "Could not restore updater directory after failed refresh",
                    exc_info=True,
                )
        return False


def _rename_with_retry_updater(
    src: Path,
    dst: Path,
    *,
    attempts: int = 10,
    delay_sec: float = 0.5,
) -> None:
    last_exc: OSError | None = None
    for attempt in range(attempts):
        try:
            src.rename(dst)
            return
        except (PermissionError, OSError) as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                time.sleep(delay_sec)
    if last_exc is not None:
        raise last_exc
    raise OSError(f"Could not rename {src} -> {dst}")


def updater_exe_path() -> Path:
    """Return the onedir updater executable path."""
    root = install_root()
    onedir = _onedir_updater_dir(root)
    if onedir is not None:
        return onedir / UPDATER_EXE_NAME
    bundled = _bundled_onedir_updater_dir()
    if bundled is not None:
        return bundled / UPDATER_EXE_NAME
    raise FileNotFoundError(
        f"Missing onedir updater executable under {root / UPDATER_DIR_NAME}"
    )


def ensure_updater_at_install_root() -> Path:
    """Return onedir updater executable, applying pending refresh and migrations."""
    root = install_root()
    apply_pending_updater_refresh(root)

    onedir = _onedir_updater_dir(root)
    if onedir is not None:
        _remove_legacy_onefile_updater(root)
        return onedir / UPDATER_EXE_NAME

    bundled_onedir = _bundled_onedir_updater_dir()
    if bundled_onedir is not None:
        target_dir = root / UPDATER_DIR_NAME
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(bundled_onedir, target_dir)
        shutil.rmtree(bundled_onedir)
        _remove_legacy_onefile_updater(root)
        (app_root() / UPDATER_EXE_NAME).unlink(missing_ok=True)
        return target_dir / UPDATER_EXE_NAME

    raise FileNotFoundError(
        "Missing onedir updater. Reinstall PDF2SEPA or wait for the next update cycle."
    )


def launch_updater(info: UpdateInfo) -> None:
    updater = ensure_updater_at_install_root()
    pid = os.getpid()
    app_dir = str(install_root() / "app")
    install = str(install_root())
    subprocess.Popen(
        [
            str(updater),
            "--url",
            info.url,
            "--sha256",
            info.sha256,
            "--version",
            info.version,
            "--pid",
            str(pid),
            "--app-dir",
            app_dir,
            "--install-root",
            install,
        ],
        cwd=str(updater.parent),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        close_fds=True,
    )


def offer_update_if_available(*, auto_accept: bool = False) -> bool:
    """Return True if an update was accepted and the updater was launched.

    When auto_accept=True, the app will launch the updater without prompting.
    Failures are handled fail-safe (no update, app continues).
    """
    if not sys.platform.startswith("win"):
        return False
    info = check_for_update()
    if info is None:
        return False
    if not auto_accept:
        from ui.message_box import ask_yes_no

        if not ask_yes_no(
            None,
            "Update beschikbaar",
            (
                f"Er is een nieuwe versie beschikbaar ({info.version}).\n"
                f"Huidige versie: {__version__}\n\n"
                "Wil je nu updaten? Je gegevens en instellingen blijven behouden."
            ),
        ):
            return False

    try:
        launch_updater(info)
    except Exception:
        logger.exception("Update start failed")
        if not auto_accept:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(
                None,
                "Update mislukt",
                (
                    "De update kon niet worden gestart.\n\n"
                    "Je huidige versie van PDF2SEPA blijft gewoon werken.\n"
                    "Je kunt het later opnieuw proberen via het opnieuw starten van PDF2SEPA."
                ),
            )
        return False

    return True
