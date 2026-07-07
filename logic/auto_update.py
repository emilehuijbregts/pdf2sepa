"""PDF2SEPA auto-update: manifest fetch, download, and updater launch."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from logic.runtime_paths import app_root, install_root
from version import __version__

logger = logging.getLogger(__name__)

UPDATE_MANIFEST_URL = (
    "https://github.com/emilehuijbregts/pdf2sepa/releases/latest/download/latest.json"
)
_FETCH_TIMEOUT_SEC = 8


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
            raw = resp.read().decode("utf-8")
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
        logger.debug("Update manifest fetch failed", exc_info=True)
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


def download_update(info: UpdateInfo, dest_dir: Path | None = None) -> Path:
    target_dir = dest_dir or Path(tempfile.gettempdir())
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / f"PDF2SEPA-update-{info.version}.zip"
    req = urllib.request.Request(info.url, headers={"User-Agent": "PDF2SEPA-updater"})
    with urllib.request.urlopen(req, timeout=120) as resp, zip_path.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    if not verify_sha256(zip_path, info.sha256):
        zip_path.unlink(missing_ok=True)
        raise ValueError("Downloaded update failed SHA256 verification")
    return zip_path


def updater_exe_path() -> Path:
    root = app_root()
    candidate = root / "PDF2SEPAUpdater.exe"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"Missing updater executable: {candidate}")


def launch_updater(zip_path: Path) -> None:
    updater = updater_exe_path()
    pid = os.getpid()
    app_dir = str(install_root() / "app")
    install = str(install_root())
    subprocess.Popen(
        [
            str(updater),
            "--zip",
            str(zip_path),
            "--pid",
            str(pid),
            "--app-dir",
            app_dir,
            "--install-root",
            install,
        ],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        close_fds=True,
    )


def offer_update_if_available(*, auto_accept: bool = False) -> bool:
    """Return True if an update was accepted and the updater was launched.

    When auto_accept=True, the app will download and launch the updater without
    prompting. Failures are handled fail-safe (no update, app continues).
    """
    if not sys.platform.startswith("win"):
        return False
    info = check_for_update()
    if info is None:
        return False
    if not auto_accept:
        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            None,
            "Update beschikbaar",
            (
                f"Er is een nieuwe versie beschikbaar ({info.version}).\n"
                f"Huidige versie: {__version__}\n\n"
                "Wil je nu updaten? Uw gegevens blijven behouden."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False

    try:
        zip_path = download_update(info)
        launch_updater(zip_path)
    except Exception:
        logger.exception("Update start failed")
        if not auto_accept:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(
                None,
                "Update mislukt",
                "De update kon niet worden gestart. Probeer het later opnieuw.",
            )
        return False

    return True
