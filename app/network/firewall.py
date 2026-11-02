"""Windows-Firewall: Zustand der eigenen Freigabe prüfen und – nur nach ausdrücklicher Zustimmung – eine Regel für
private Netzwerke anlegen, auf Wunsch zusammen mit dem Umstellen des aktuellen Netzwerks von „Öffentlich“ auf
„Privat“. Beides braucht Administratorrechte; Windows fragt genau einmal per UAC nach."""

from __future__ import annotations

import ctypes
import logging
import sys

from . import powershell

log = logging.getLogger(__name__)

RULE_NAME = "Flip 7 Live"

_CHECK = r"""
$ErrorActionPreference = 'SilentlyContinue'
$rules = @(Get-NetFirewallApplicationFilter -Program $env:FLIP7_PROGRAM | Get-NetFirewallRule |
  Where-Object { $_.Direction -eq 'Inbound' } |
  Select-Object DisplayName, @{n='Enabled';e={[string]$_.Enabled}}, @{n='Action';e={[string]$_.Action}},
                @{n='Profile';e={[string]$_.Profile}})
[pscustomobject]@{ ok = $true; rules = $rules } | ConvertTo-Json -Depth 3 -Compress
"""


def program_path() -> str:
    return sys.executable


def check(runner=None) -> dict:
    """Ergebnis: state = "allowed" | "blocked" | "none" | "unknown"; profiles mit erlaubter Freigabe."""
    if not powershell.available():
        return {"state": "unknown", "profiles": [], "program": program_path()}
    try:
        data = (runner or powershell.run_json)(_CHECK, env={"FLIP7_PROGRAM": program_path()}, timeout=20)
    except powershell.PowerShellError:
        return {"state": "unknown", "profiles": [], "program": program_path()}
    rules = [r for r in data.get("rules") or [] if isinstance(r, dict) and r.get("Enabled") == "True"]
    allowed = [r for r in rules if r.get("Action") == "Allow"]
    blocked = [r for r in rules if r.get("Action") == "Block"]
    profiles = sorted({p.strip() for r in allowed for p in str(r.get("Profile", "")).split(",") if p.strip()})
    state = "blocked" if blocked and not allowed else "allowed" if allowed else "none"
    return {"state": state, "profiles": profiles, "program": program_path()}


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def setup_script(port: int | None, private_interface: int | None) -> str:
    """Erhöhtes PowerShell-Skript: optional Netzwerk → Privat, optional Regel (nur privat, nur dieser Port).

    Vor dem Anlegen werden alle eingehenden Regeln *dieses Programms* entfernt – auch die Block-Regeln, die Windows
    anlegt, wenn die eigene Firewall-Abfrage abgelehnt wurde (Block hat Vorrang vor Allow)."""
    cmds = []
    if private_interface is not None:
        cmds.append(f"Set-NetConnectionProfile -InterfaceIndex {int(private_interface)} -NetworkCategory Private")
    if port is not None:
        prog = _ps_quote(program_path())
        cmds += [
            f"Get-NetFirewallApplicationFilter -Program {prog} | Get-NetFirewallRule | "
            "Where-Object { $_.Direction -eq 'Inbound' } | Remove-NetFirewallRule",
            f"New-NetFirewallRule -DisplayName {_ps_quote(RULE_NAME)} -Direction Inbound -Action Allow "
            f"-Program {prog} -Profile Private -Protocol TCP -LocalPort {int(port)} | Out-Null",
        ]
    return "; ".join(cmds)


def _elevated(script: str, what: str) -> bool:
    """PowerShell-Skript mit UAC-Abfrage starten. True = Abfrage wurde angezeigt."""
    if not powershell.available() or not script:
        return False
    # Klartext statt -EncodedCommand: erhöhte, base64-kodierte Skripte stuft Virenschutz gern als verdächtig ein.
    # Pfade enthalten unter Windows keine doppelten Anführungszeichen; einfache werden in _ps_quote verdoppelt.
    args = f'-NoProfile -NonInteractive -WindowStyle Hidden -Command "{script}"'
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", args, None, 0)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    log.info("%s angefordert (ShellExecute %s)", what, rc)
    return rc > 32


def request_setup(port: int | None, private_interface: int | None = None) -> bool:
    """Startet das Skript mit UAC-Abfrage. True = Abfrage wurde angezeigt; ob zugestimmt wurde, zeigen check() und
    die nächste Netzwerkprüfung."""
    if port is None and private_interface is None:
        return False
    return _elevated(setup_script(port, private_interface),
                     f"Firewall/Netzwerkprofil (Regel: {port is not None}, privat: {private_interface is not None})")


def removal_script() -> str:
    return (f"Get-NetFirewallApplicationFilter -Program {_ps_quote(program_path())} | Get-NetFirewallRule | "
            "Remove-NetFirewallRule")


def request_removal() -> bool:
    """Beim Deinstallieren: alle Regeln dieses Programms entfernen (eine UAC-Abfrage)."""
    return _elevated(removal_script(), "Entfernen der Firewall-Regeln")
