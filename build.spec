# PyInstaller-Konfiguration für Flip7Live.exe (eine Datei, ohne Konsolenfenster, mit App-Icon).
# Wichtig: Die EXE enthält keinen unsignierten, kompilierten Fremdcode (.pyd/.dll aus site-packages) – Smart App
# Control blockiert ihn. Python selbst (PSF-signiert) und Microsofts WebView2Loader.dll sind signiert.
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
binaries = [(str(ROOT / "app" / "desktop" / "webview2" / "WebView2Loader.dll"), "app/desktop/webview2")]

hiddenimports = (
    collect_submodules("app")
    + ["engineio.async_drivers.threading", "simple_websocket", "sqlalchemy.dialects.sqlite",
       "alembic.runtime.migration", "alembic.ddl.sqlite"]
)

a = Analysis(
    ["launcher.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "playwright", "eventlet", "gevent", "PIL", "pystray", "pydantic", "pydantic_core",
              "requests", "charset_normalizer", "markupsafe._speedups", "greenlet"],
    noarchive=False,
)
# Sicherheitsnetz: alles Kompilierte aus site-packages entfernen (reine Python-Rückfallebenen existieren).
# SQLAlchemy muss ohne C-Erweiterung installiert sein (build_windows.ps1), sonst fehlen ihre *_cy-Module.
dropped = [name for name, src, _ in a.binaries if "site-packages" in src.lower()]
if dropped:
    print("Ohne unsignierte Fremd-Binärdateien gebaut, entfernt:", ", ".join(dropped))
a.binaries = [b for b in a.binaries if "site-packages" not in b[1].lower()]
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
