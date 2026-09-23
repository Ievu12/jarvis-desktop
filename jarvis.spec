# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build specification for JARVIS desktop (jarvis.gui.app).

Produces a single-directory build (not --onefile) at dist/JARVIS/ -
onefile is deliberately avoided: it self-extracts to a temp directory on
every launch, which is slower to start and makes the auto-update
system's file-replace-in-place approach (see jarvis.gui.updater
.install_update()) awkward, since there is no persistent installation
directory to update. See RELEASE.md for how this file is invoked as
part of a release build.

Run manually with: pyinstaller jarvis.spec
"""

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ["jarvis_launcher.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        "customtkinter",
        "darkdetect",
        "speech_recognition",
        "pyttsx3",
        "pyttsx3.drivers",
        "pyttsx3.drivers.sapi5",
        "win32com",
        "win32com.client",
        "pythoncom",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="JARVIS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name="JARVIS",
)
