"""WLAN-Status und SSID.

``netsh wlan show interfaces`` liefert die SSID; neuere Windows-Versionen verlangen dafür teils die
Standortberechtigung. Dann wird der Name des Netzwerkprofils verwendet (bei WLAN in der Regel die SSID).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from . import powershell

log = logging.getLogger(__name__)

_SSID = re.compile(r"^\s*SSID\s*:\s*(.+?)\s*$", re.MULTILINE)
_NAME = re.compile(r"^\s*Name\s*:\s*(.+?)\s*$", re.MULTILINE)
_STATE = re.compile(r"^\s*(?:State|Status)\s*:\s*(.+?)\s*$", re.MULTILINE)
CONNECTED_WORDS = ("connected", "verbunden")


@dataclass(frozen=True)
class WifiInfo:
    adapter: str
    connected: bool
    ssid: str | None


def parse_netsh(text: str) -> list[WifiInfo]:
    """Zerlegt die (deutsche oder englische) netsh-Ausgabe in einzelne WLAN-Adapter."""
    blocks = re.split(r"\n\s*\n(?=\s*Name\s*:)", text)
    result = []
    for block in blocks:
        name = _NAME.search(block)
        if not name:
            continue
        state = _STATE.search(block)
        state_text = state.group(1).strip().lower() if state else ""
        connected = any(state_text.startswith(w) for w in CONNECTED_WORDS)
        ssid = _SSID.search(block)
        result.append(WifiInfo(adapter=name.group(1).strip(), connected=connected,
                               ssid=ssid.group(1).strip() if ssid and connected else None))
    return result


def detect() -> list[WifiInfo]:
    if not powershell.available():
        return []
    try:
        return parse_netsh(powershell.run_text(["netsh", "wlan", "show", "interfaces"]))
    except powershell.PowerShellError as exc:
        log.info("WLAN-Abfrage nicht möglich: %s", exc)
        return []
