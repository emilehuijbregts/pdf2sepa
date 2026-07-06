# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for PDF2SEPA (Windows onedir GUI build).

Run from repository root (future phase — not executed in packaging-prep):
    pyinstaller packaging/pdf2sepa.spec

Customer data (settings.json, suppliers.json, overrides) must NEVER be bundled.
See packaging/README.md for deployment layout under %LOCALAPPDATA%/PDF2SEPA/.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, collect_submodules

block_cipher = None

# SPECPATH = directory containing this .spec file (packaging/).
distpath = os.path.join(SPECPATH, "dist")
workpath = os.path.join(SPECPATH, "build")

project_root = Path(SPECPATH).resolve().parent
entry_script = project_root / "main.py"
_icon_dir = project_root / "packaging" / "icons"
_icon_png = _icon_dir / "app_icon.png"
_icon_ico = _icon_dir / "app_icon.ico"

# ---------------------------------------------------------------------------
# Customer / dev data — NEVER bundle (installer manages %LOCALAPPDATA%/PDF2SEPA/data)
# ---------------------------------------------------------------------------
# data/settings.json
# data/suppliers.json
# data/amount_overrides.json
# data/credit_overrides.json
# data/user_approvals.json
# data/exports/, logs/, backups/

# ---------------------------------------------------------------------------
# Collect runtime dependencies
# ---------------------------------------------------------------------------
binaries: list[tuple[str, str]] = []
datas: list[tuple[str, str]] = []
hiddenimports: list[str] = [
    # i18n registries (static imports, explicit for safety)
    "ui.i18n.languages.en",
    "ui.i18n.languages.nl",
    # PDF / OCR stack
    "fitz",
    "pdfplumber",
    "lxml.etree",
    "PIL",
    "pytesseract",
]

# PySide6 — Qt plugins and platform binaries
_pyside6_datas, _pyside6_binaries, _pyside6_hidden = collect_all("PySide6")
datas += _pyside6_datas
binaries += _pyside6_binaries
hiddenimports += _pyside6_hidden

# PyMuPDF (fitz)
_fitz_datas, _fitz_binaries, _fitz_hidden = collect_all("fitz")
datas += _fitz_datas
binaries += _fitz_binaries
hiddenimports += _fitz_hidden

# pdfplumber + pdfminer.six (transitive)
hiddenimports += collect_submodules("pdfminer")

# lxml — native extension
binaries += collect_dynamic_libs("lxml")

# App icon (window + Windows .exe)
if _icon_png.is_file():
    datas += [(str(_icon_png), "icons")]

# ---------------------------------------------------------------------------
# App-engine data (shipped config, NOT customer data)
# Resolved at runtime via logic.runtime_paths.bundled_engine_data_path()
# ---------------------------------------------------------------------------
_engine_bundle = project_root / "data" / "strategy_engine_bundle.json"
if _engine_bundle.is_file():
    datas += [(str(_engine_bundle), "data")]

# ---------------------------------------------------------------------------
# Tesseract OCR (Windows binaries)
# Place tesseract.exe, DLLs and tessdata/ under packaging/tesseract/ before build.
# ---------------------------------------------------------------------------
tesseract_root = project_root / "packaging" / "tesseract"
_tesseract_exe = tesseract_root / "tesseract.exe"
if _tesseract_exe.is_file():
    binaries += [(str(_tesseract_exe), "tesseract")]
    binaries += [
        (str(dll), "tesseract")
        for dll in sorted(tesseract_root.glob("*.dll"))
    ]
_tessdata_dir = tesseract_root / "tessdata"
if _tessdata_dir.is_dir() and any(_tessdata_dir.glob("*.traineddata")):
    datas += [(str(_tessdata_dir), os.path.join("tesseract", "tessdata"))]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [str(entry_script)],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tests",
        "scripts",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PDF2SEPA",
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
    icon=str(_icon_ico) if _icon_ico.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PDF2SEPA",
)
