"""Erkennung der Netzwerkadapter und Auswahl geeigneter IPv4-Adressen.

Ungeeignet sind: Loopback, nicht verbundene Adapter, selbst zugewiesene Adressen (169.254.x.x), Adressen im
Zustand „Tentative“ sowie virtuelle Tunnel-/VPN-/VM-Adapter. Der Windows-Hotspot-Adapter („Wi-Fi Direct Virtual
Adapter“) ist zwar virtuell, wird aber als Hotspot erkannt und behalten.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import asdict, dataclass

from . import powershell

log = logging.getLogger(__name__)

SCAN_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$ad = @(Get-NetAdapter -IncludeHidden | Select-Object ifIndex, Name, InterfaceDescription,
        @{n='Status';e={[string]$_.Status}}, MediaType, PhysicalMediaType, Virtual, HardwareInterface)
$ip = @(Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceIndex, InterfaceAlias, IPAddress, PrefixLength,
        @{n='AddressState';e={[string]$_.AddressState}})
$pr = @(Get-NetConnectionProfile | Select-Object InterfaceIndex, Name,
        @{n='NetworkCategory';e={[string]$_.NetworkCategory}}, @{n='IPv4Connectivity';e={[string]$_.IPv4Connectivity}})
[pscustomobject]@{ adapters = $ad; addresses = $ip; profiles = $pr } | ConvertTo-Json -Depth 4 -Compress
"""

# Teilstrings in der Adapterbeschreibung, die auf Tunnel, VPN oder virtuelle Maschinen hindeuten.
VIRTUAL_HINTS = (
    "hyper-v", "vethernet", "virtualbox", "vmware", "wsl", "tap-windows", "openvpn", "wireguard", "wintun",
    "tailscale", "zerotier", "teredo", "isatap", "6to4", "pseudo-interface", "loopback", "vpn", "secure client",
    "npcap", "fortinet", "cisco anyconnect", "globalprotect", "docker", "kernel debug",
)
HOTSPOT_HINTS = ("wi-fi direct virtual adapter", "wifi direct virtual adapter")


@dataclass(frozen=True)
class Interface:
    index: int
    name: str
    description: str
    kind: str              # "wifi" | "ethernet" | "hotspot" | "other"
    ip: str
    prefix_length: int
    profile_name: str | None = None
    category: str | None = None   # "Public" | "Private" | "DomainAuthenticated"

    @property
    def subnet(self) -> str:
        return str(ipaddress.ip_interface(f"{self.ip}/{self.prefix_length}").network)

    def to_dict(self) -> dict:
        return {**asdict(self), "subnet": self.subnet}


def _usable_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.version == 4 and not (addr.is_loopback or addr.is_link_local or addr.is_multicast
                                       or addr.is_unspecified)


def classify(adapter: dict) -> str | None:
    """Adaptertyp oder ``None`` für ungeeignete Adapter."""
    desc = f"{adapter.get('InterfaceDescription') or ''} {adapter.get('Name') or ''}".lower()
    if any(h in desc for h in HOTSPOT_HINTS):
        return "hotspot"
    if any(h in desc for h in VIRTUAL_HINTS):
        return None
    media = f"{adapter.get('PhysicalMediaType') or ''} {adapter.get('MediaType') or ''}".lower()
    if "bluetooth" in media or "wireless wan" in media:
        return None
    if "802.11" in media or "wireless lan" in media:
        return "wifi"
    if "802.3" in media:
        return "ethernet" if adapter.get("HardwareInterface", True) else None
    return "other" if not adapter.get("Virtual") else None


def parse_scan(data: dict) -> list[Interface]:
    adapters = {a.get("ifIndex"): a for a in data.get("adapters") or [] if isinstance(a, dict)}
    profiles = {p.get("InterfaceIndex"): p for p in data.get("profiles") or [] if isinstance(p, dict)}
    result: list[Interface] = []
    for addr in data.get("addresses") or []:
        if not isinstance(addr, dict):
            continue
        ip = str(addr.get("IPAddress") or "")
        if not _usable_ip(ip) or str(addr.get("AddressState")) not in ("Preferred", "4"):
            continue
        adapter = adapters.get(addr.get("InterfaceIndex"))
        if adapter is None or str(adapter.get("Status")) != "Up":
            continue
        kind = classify(adapter)
        if kind is None:
            continue
        prof = profiles.get(addr.get("InterfaceIndex")) or {}
        result.append(Interface(
            index=int(addr.get("InterfaceIndex")), name=str(adapter.get("Name") or addr.get("InterfaceAlias") or ""),
            description=str(adapter.get("InterfaceDescription") or ""), kind=kind, ip=ip,
            prefix_length=int(addr.get("PrefixLength") or 24), profile_name=prof.get("Name"),
            category=prof.get("NetworkCategory"),
        ))
    return result


def fallback_interfaces() -> list[Interface]:
    """Ohne PowerShell: Adressen über den Rechnernamen ermitteln (Typ unbekannt)."""
    ips: list[str] = []
    try:
        ips = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    seen, result = set(), []
    for i, ip in enumerate(ips):
        if _usable_ip(ip) and ip not in seen:
            seen.add(ip)
            result.append(Interface(index=1000 + i, name="Netzwerk", description="", kind="other", ip=ip,
                                    prefix_length=24))
    return result


def quick_fingerprint() -> tuple[str, ...]:
    """Billiger Vergleichswert für die Hintergrundüberwachung – ändert sich bei neuer IP/neuem Netz."""
    try:
        return tuple(sorted(socket.gethostbyname_ex(socket.gethostname())[2]))
    except OSError:
        return ()


def scan() -> tuple[list[Interface], str]:
    """Liefert (Schnittstellen, Quelle). Quelle ist "powershell" oder "fallback"."""
    if powershell.available():
        try:
            return parse_scan(powershell.run_json(SCAN_SCRIPT)), "powershell"
        except powershell.PowerShellError as exc:
            log.warning("Adaptererkennung über PowerShell fehlgeschlagen: %s", exc)
    return fallback_interfaces(), "fallback"
