# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build recipe: `python build_exe.py` or `pyinstaller renewaltracker.spec`.

Produces a single self-contained executable (dist/RenewalTracker or
dist/RenewalTracker.exe) that needs no Python installation. Data lives in the
user's profile (see renewaltracker/paths.py), never inside the bundle.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
here = os.path.abspath(os.getcwd())

hidden = (
    collect_submodules("renewaltracker")
    + collect_submodules("sqlalchemy.dialects.sqlite")
    + ["sqlalchemy.sql.default_comparator", "dateutil.relativedelta", "dateutil.parser"]
)

a = Analysis(
    ["run.py"],
    pathex=[here],
    binaries=[],
    datas=[(os.path.join(here, "renewaltracker", "static"), os.path.join("renewaltracker", "static"))],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "tkinter", "unittest", "playwright"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
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
    name="RenewalTracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # shows the URL and lets Ctrl+C stop the server
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
