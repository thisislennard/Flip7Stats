# PyInstaller-Konfiguration für Flip7Live.exe (eine Datei, ohne Konsolenfenster, mit App-Icon).
# Aufruf über scripts/build_windows.ps1 – oder direkt: pyinstaller --noconfirm --clean build.spec
# -*- mode: python ; coding: utf-8 -*-

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH)
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")).group(1)
NUMS = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:4])

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMS, prodvers=NUMS),
    kids=[
        StringFileInfo([StringTable("040704B0", [
            StringStruct("CompanyName", "Flip 7 Live"),
            StringStruct("FileDescription", "Flip 7 Live – Live-Punkteanzeige für Flip 7"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "Flip7Live"),
            StringStruct("OriginalFilename", "Flip7Live.exe"),
            StringStruct("ProductName", "Flip 7 Live"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0407, 1200])]),
    ],
)

datas = [
    (str(ROOT / "app" / "templates"), "app/templates"),
    (str(ROOT / "app" / "static"), "app/static"),
    (str(ROOT / "app" / "database" / "migrations"), "app/database/migrations"),
]

hiddenimports = (
    collect_submodules("app")
    + ["engineio.async_drivers.threading", "simple_websocket", "pystray._win32", "sqlalchemy.dialects.sqlite",
       "alembic.runtime.migration", "alembic.ddl.sqlite"]
)

a = Analysis(
    ["launcher.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "playwright", "eventlet", "gevent"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Flip7Live",
    icon=str(ROOT / "app" / "static" / "assets" / "flip7live.ico"),
    version=version_info,
    console=False,
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
)
