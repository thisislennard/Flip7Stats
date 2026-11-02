"""Windows-Anbindung: Autostart, Dateien öffnen, Meldungsfenster, Einzelinstanz-Sperre."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from pathlib import Path

from .config import APP_NAME, APP_TITLE, is_frozen

log = logging.getLogger(__name__)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def frozen() -> bool:
    return is_frozen()


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    """Autostart beim Windows-Login (nur für den aktuellen Benutzer, ohne Administratorrechte)."""
    if sys.platform != "win32" or (enabled and not frozen()):
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{sys.executable}"')
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        log.exception("Autostart konnte nicht geändert werden")
        return False


def open_path(path: Path) -> bool:
    if sys.platform != "win32" or not path.exists():
        return False
    try:
        os.startfile(path)  # type: ignore[attr-defined]
        return True
    except OSError:
        return False


def open_uri(uri: str) -> bool:
    """Windows-Einstellungsseite o. Ä. öffnen (z. B. ms-settings:network-status)."""
    if sys.platform != "win32":
        return False
    try:
        os.startfile(uri)  # type: ignore[attr-defined]
        return True
    except OSError:
        return False


def message_box(text: str, title: str = APP_TITLE, error: bool = True) -> None:
    """Meldung ohne Konsole (die EXE läuft ohne Konsolenfenster)."""
    if sys.platform == "win32":
        import ctypes
        flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(None, text, title, flags | 0x10000)  # MB_SETFOREGROUND
    else:
        print(f"{title}: {text}", file=sys.stderr)


class SingleInstance:
    """Benannter Windows-Mutex (pro Benutzersitzung). Wird vom System freigegeben, auch wenn der Prozess abstürzt."""

    ERROR_ALREADY_EXISTS = 183

    def __init__(self, name: str | None = None, data_root: Path | None = None):
        if name is None:
            # Eine Instanz je Datenordner (Standard %LOCALAPPDATA%\Flip7Live): zwei Server auf denselben Daten sind
            # ausgeschlossen, ein Testlauf mit eigenem Datenordner (FLIP7LIVE_HOME) stört die echte Instanz nicht.
            key = hashlib.sha1(str(data_root or "").lower().encode()).hexdigest()[:12]
            name = f"Local\\{APP_NAME}-SingleInstance-{key}"
        self.name = name
        self._handle = None
        self._lockfile = None

    def acquire(self) -> bool:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            handle = kernel32.CreateMutexW(None, False, self.name)
            if not handle:
                return False
            if ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                return False
            self._handle = handle
            return True
        import fcntl  # Entwicklung unter Linux/macOS
        import tempfile
        self._lockfile = open(Path(tempfile.gettempdir()) / f"{APP_NAME}.lock", "w")  # noqa: SIM115
        try:
            fcntl.flock(self._lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def release(self) -> None:
        if self._handle:
            import ctypes
            ctypes.WinDLL("kernel32").CloseHandle(self._handle)
            self._handle = None
        if self._lockfile:
            self._lockfile.close()
            self._lockfile = None
