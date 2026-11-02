"""Selbst-Installation der EXE – ohne zusätzliche Setup-Datei und ohne Administratorrechte.

- Installieren: Kopie nach ``%LOCALAPPDATA%\\Programs\\Flip7Live``, Verknüpfungen auf dem Desktop und im Startmenü,
  Eintrag unter *Einstellungen › Apps* (Deinstallieren).
- Aktualisieren: Eine neuere EXE ersetzt die installierte (laufende Instanz wird vorher sauber beendet).
- Deinstallieren (``Flip7Live.exe --uninstall``): fragt, ob auch Spielstände und Einstellungen gelöscht werden sollen.

Nur Bordmittel: Registry über ``winreg``, Verknüpfungen über PowerShell (``WScript.Shell``), Löschen des eigenen
Programmordners nach dem Beenden über ``cmd``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

from .. import __version__
from ..config import APP_TITLE, Paths, default_home, resource_path

log = logging.getLogger(__name__)

APP_ID = "Flip7Live"
EXE_NAME = "Flip7Live.exe"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

MB_YESNO, MB_YESNOCANCEL, MB_OK = 0x4, 0x3, 0x0
MB_ICONQUESTION, MB_ICONINFO, MB_ICONWARNING, MB_ICONERROR = 0x20, 0x40, 0x30, 0x10
MB_DEFBUTTON2, MB_SETFOREGROUND = 0x100, 0x10000
IDYES, IDNO, IDCANCEL = 6, 7, 2


def install_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs" / APP_ID


def installed_exe() -> Path:
    return install_dir() / EXE_NAME


def ask(text: str, flags: int) -> int:
    import ctypes
    return ctypes.windll.user32.MessageBoxW(None, text, APP_TITLE, flags | MB_SETFOREGROUND)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve() or (a.exists() and b.exists() and os.path.samefile(a, b))
    except OSError:
        return False


def is_running_installed() -> bool:
    return _same_file(Path(sys.executable), installed_exe())


def installed_version() -> str | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            return winreg.QueryValueEx(key, "DisplayVersion")[0]
    except OSError:
        return None


def _version_tuple(v: str | None) -> tuple:
    try:
        return tuple(int(x) for x in (v or "0").split("."))
    except ValueError:
        return (0,)


# ---------------------------------------------------------------- Laufende Instanz

def stop_running_instance(paths: Paths, timeout: float = 20) -> bool:
    """Bittet eine laufende Instanz (gleicher Datenordner) per IPC, sich zu beenden. True = keine Instanz mehr."""
    try:
        info = json.loads(paths.instance_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{int(info['port'])}/internal/shutdown", data=b"{}",
                                     method="POST", headers={"Content-Type": "application/json",
                                                             "X-Flip7-IPC": info["ipc_secret"]})
        urllib.request.urlopen(req, timeout=5).close()
    except (OSError, ValueError, KeyError):
        return not paths.instance_file.exists()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not paths.instance_file.exists():
            time.sleep(1)  # Prozess gibt die EXE-Datei frei
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------- Installation

def _powershell(script: str) -> bool:
    try:
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                              capture_output=True, text=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("PowerShell nicht ausführbar: %s", exc)
        return False
    if proc.returncode:
        log.warning("PowerShell-Fehler: %s", proc.stderr.strip()[:300])
    return proc.returncode == 0


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _shortcut_paths_script() -> str:
    return ("$desk = [Environment]::GetFolderPath('Desktop'); "
            "$menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Flip 7 Live.lnk'; "
            "$links = @((Join-Path $desk 'Flip 7 Live.lnk'), $menu); ")


def create_shortcuts(exe: Path) -> bool:
    icon = exe.parent / "Flip7Live.ico"
    script = (_shortcut_paths_script() +
              "$sh = New-Object -ComObject WScript.Shell; "
              "foreach ($l in $links) { $s = $sh.CreateShortcut($l); "
              f"$s.TargetPath = {_q(str(exe))}; $s.WorkingDirectory = {_q(str(exe.parent))}; "
              f"$s.IconLocation = {_q(str(icon) + ',0')}; "
              "$s.Description = 'Live-Punkteanzeige für Flip 7'; $s.Save() }")
    return _powershell(script)


def remove_shortcuts() -> bool:
    return _powershell(_shortcut_paths_script() + "foreach ($l in $links) { Remove-Item -LiteralPath $l -ErrorAction SilentlyContinue }")


def _register(exe: Path) -> None:
    import winreg
    size_kb = sum(f.stat().st_size for f in exe.parent.iterdir() if f.is_file()) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in {
            "DisplayName": "Flip 7 Live",
            "DisplayVersion": __version__,
            "Publisher": "Flip 7 Live",
            "DisplayIcon": f"{exe.parent / 'Flip7Live.ico'}",
            "InstallLocation": str(exe.parent),
            "UninstallString": f'"{exe}" --uninstall',
            "URLInfoAbout": "https://github.com/thisislennard/Flip7Stats",
            "InstallDate": date.today().strftime("%Y%m%d"),
        }.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in {"NoModify": 1, "NoRepair": 1, "EstimatedSize": size_kb}.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)


def _unregister() -> None:
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


def install(source: Path) -> Path:
    """Kopiert die EXE (und das Icon) in den Programmordner, legt Verknüpfungen und den Apps-Eintrag an."""
    target_dir = install_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    exe = target_dir / EXE_NAME
    tmp = target_dir / (EXE_NAME + ".neu")
    shutil.copy2(source, tmp)
    for attempt in range(20):  # die alte EXE kann nach dem Beenden noch kurz gesperrt sein
        try:
            os.replace(tmp, exe)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.5)
    shutil.copy2(resource_path("app", "static", "assets", "flip7live.ico"), target_dir / "Flip7Live.ico")
    _register(exe)
    if not create_shortcuts(exe):
        log.warning("Verknüpfungen konnten nicht angelegt werden")
    log.info("Installiert nach %s (Version %s)", exe, __version__)
    return exe


def launch(exe: Path) -> None:
    subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=DETACHED_PROCESS, close_fds=True)


def maybe_install() -> bool:
    """Beim Start einer nicht installierten EXE: installieren/aktualisieren anbieten.

    Rückgabe True = dieser Prozess soll sich beenden (die installierte Version wurde gestartet)."""
    if not getattr(sys, "frozen", False) or sys.platform != "win32" or is_running_installed():
        return False
    me = Path(sys.executable)
    target = installed_exe()
    current = installed_version() if target.exists() else None
    paths = Paths(default_home())
    if current is None:
        choice = ask("Flip 7 Live jetzt installieren?\n\n"
                     "• Verknüpfungen auf dem Desktop und im Startmenü\n"
                     "• Deinstallieren jederzeit über Windows-Einstellungen › Apps\n"
                     "• Keine Administratorrechte nötig\n\n"
                     "„Nein“ startet Flip 7 Live einmalig ohne Installation.", MB_YESNOCANCEL | MB_ICONQUESTION)
        if choice == IDCANCEL:
            return True
        if choice == IDNO:
            return False
    else:
        newer = _version_tuple(__version__) > _version_tuple(current)
        text = (f"Flip 7 Live {current} ist installiert. Diese Datei ist Version {__version__}.\n\n"
                + ("Jetzt aktualisieren? Spielstände und Einstellungen bleiben erhalten."
                   if newer else "Installierte Version durch diese Datei ersetzen?"))
        choice = ask(text, MB_YESNOCANCEL | MB_ICONQUESTION | (0 if newer else MB_DEFBUTTON2))
        if choice == IDCANCEL:
            return True
        if choice == IDNO:
            launch(target)  # einfach die installierte Version starten
            return True
        if not stop_running_instance(paths):
            ask("Flip 7 Live läuft noch und konnte nicht beendet werden. Bitte zuerst schließen und erneut versuchen.",
                MB_OK | MB_ICONWARNING)
            return True
    try:
        exe = install(me)
    except OSError as exc:
        log.exception("Installation fehlgeschlagen")
        ask(f"Die Installation ist fehlgeschlagen:\n{exc}", MB_OK | MB_ICONERROR)
        return False
    launch(exe)
    return True


# ---------------------------------------------------------------- Deinstallation

def uninstall() -> int:
    paths = Paths(default_home())
    if ask("Flip 7 Live deinstallieren?", MB_YESNO | MB_ICONQUESTION) != IDYES:
        return 0
    if not stop_running_instance(paths):
        ask("Flip 7 Live läuft noch und konnte nicht beendet werden. Bitte zuerst schließen und erneut versuchen.",
            MB_OK | MB_ICONWARNING)
        return 1
    delete_data = ask("Auch alle Spielstände, Statistiken, Einstellungen und Sicherungen löschen?\n\n"
                      f"{paths.root}\n\n„Nein“ behält sie für eine spätere Neuinstallation.",
                      MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) == IDYES
    from .. import system_integration as si
    from ..network import firewall
    try:
        si.set_autostart(False)
    except OSError:
        pass
    remove_shortcuts()
    _unregister()
    exe = installed_exe()
    if firewall.check().get("state") in ("allowed", "blocked") and ask(
            "Auch die Firewall-Freigaben von Flip 7 Live entfernen?\n\n"
            "Windows fragt dafür gleich einmal nach Administratorrechten („Windows PowerShell erfordert Ihre "
            "Berechtigung“). „Nein“ lässt sie stehen – sie schaden nicht.", MB_YESNO | MB_ICONQUESTION) == IDYES:
        firewall.request_removal()  # eine UAC-Abfrage
    if delete_data and paths.root.exists():
        shutil.rmtree(paths.root, ignore_errors=True)
    # Programmordner nach dem Beenden dieses Prozesses löschen (die laufende EXE ist bis dahin gesperrt).
    if install_dir().exists():
        subprocess.Popen(f'cmd /c timeout /t 3 /nobreak >nul & rmdir /s /q "{install_dir()}"',
                         creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS, close_fds=True)
    ask("Flip 7 Live wurde deinstalliert." + ("" if delete_data else
        "\n\nSpielstände und Einstellungen sind erhalten geblieben."), MB_OK | MB_ICONINFO)
    log.info("Deinstalliert (Daten gelöscht: %s, EXE: %s)", delete_data, exe)
    return 0
