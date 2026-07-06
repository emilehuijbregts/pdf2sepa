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

# ---------------------------------------------------------------------------
# App-engine data (shipped config, NOT customer data) — phase 2
# Path resolution in frozen mode still needs a follow-up code change.
# ---------------------------------------------------------------------------
# _engine_bundle = project_root / "data" / "strategy_engine_bundle.json"
# if _engine_bundle.is_file():
#     datas += [(str(_engine_bundle), "data")]

# ---------------------------------------------------------------------------
# Tesseract OCR (Windows binaries) — phase 2
# Expected layout after installer:
#   app/tesseract/tesseract.exe
#   app/tesseract/*.dll
#   app/tesseract/tessdata/nld.traineddata
#   app/tesseract/tessdata/eng.traineddata
# ---------------------------------------------------------------------------
# tesseract_root = project_root / "packaging" / "tesseract"
# _tesseract_exe = tesseract_root / "tesseract.exe"
# if _tesseract_exe.is_file():
#     binaries += [(str(_tesseract_exe), "tesseract")]
#     binaries += [
#         (str(dll), "tesseract")
#         for dll in sorted(tesseract_root.glob("*.dll"))
#     ]
# _tessdata_dir = tesseract_root / "tessdata"
# if _tessdata_dir.is_dir():
#     datas += [(str(_tessdata_dir), os.path.join("tesseract", "tessdata"))]

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
