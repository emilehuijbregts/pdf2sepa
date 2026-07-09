# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onefile spec for PDF2SEPAUpdater.exe"""

from __future__ import annotations

import os
from pathlib import Path

block_cipher = None

distpath = os.path.join(SPECPATH, "dist")
workpath = os.path.join(SPECPATH, "build")

project_root = Path(SPECPATH).resolve().parent
entry_script = project_root / "packaging" / "updater_main.py"

a = Analysis(
    [str(entry_script)],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "ui.update_progress_window",
        "logic.update_qt_bootstrap",
        "logic.auto_update",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests", "scripts"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PDF2SEPAUpdater",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
