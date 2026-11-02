"""Automatischer Netzwerkmanager: wählt den sinnvollsten Verbindungsweg und überwacht Netzwerkwechsel.

Priorität (Modus „Automatisch“):
1. Bestehendes WLAN, 2. eigener Windows-Hotspot, 3. alternative Wege anzeigen (Smartphone-Hotspot, LAN),
4. Einzelgerätebetrieb (nur Laptop). Eine LAN-Verbindung wird wie ein bestehendes Netz angeboten.

Der Server lauscht auf allen Schnittstellen; der Manager bestimmt nur, welche Adresse im QR-Code steht. Dadurch
bleibt der Server bei einem Netzwerkwechsel erreichbar und Clients verbinden sich automatisch neu.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable

from . import address_resolver, wifi_detector
from .address_resolver import Interface
from .hotspot_manager import HotspotManager, HotspotStatus

log = logging.getLogger(__name__)

MODE_LABELS = {
    "wlan": "Bestehendes WLAN",
    "ethernet": "Netzwerkkabel (LAN)",
    "network": "Lokales Netzwerk",
    "hotspot": "Windows-Hotspot",
    "local": "Nur dieser Laptop",
}
POLL_S = 3.0
FULL_SCAN_S = 60.0


@dataclass
class NetworkState:
    mode: str = "local"
    primary: Interface | None = None
    interfaces: list[Interface] = field(default_factory=list)
    ssid: str | None = None
    hotspot: HotspotStatus = field(default_factory=HotspotStatus)
    warnings: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    source: str = "fallback"
    checked_at: str = ""
    reachable: bool | None = None

    def key(self) -> tuple:
        return (self.mode, self.primary.ip if self.primary else None, tuple(sorted(i.ip for i in self.interfaces)),
                self.hotspot.state, self.primary.category if self.primary else None)


def _hints(mode: str, hotspot: HotspotStatus) -> list[str]:
    if mode == "wlan":
        return ["Das Smartphone muss im selben WLAN sein.",
                "In Gast-WLANs oder bei aktivierter „Client-Isolation“ können sich Geräte oft nicht gegenseitig "
                "erreichen – dann den Hotspot (Laptop oder Smartphone) verwenden."]
    if mode == "network":
        return ["Das Smartphone muss im selben Netzwerk wie dieser Laptop sein.",
                "In Gast-WLANs oder bei aktivierter „Client-Isolation“ können sich Geräte oft nicht gegenseitig "
                "erreichen – dann den Hotspot (Laptop oder Smartphone) verwenden."]
    if mode == "ethernet":
        return ["Das Smartphone muss im WLAN desselben Routers sein, an dem der Laptop per Kabel hängt."]
    if mode == "hotspot":
        return [f"Smartphone mit dem WLAN „{hotspot.ssid}“ verbinden (WLAN-QR-Code scannen), danach den "
                "Steuerungs-QR-Code scannen."]
    return [
        "Hotspot des Smartphones einschalten und den Laptop mit diesem WLAN verbinden – Flip 7 Live erkennt das "
        "automatisch. Internet wird dafür nicht benötigt.",
        "Oder den Windows-Hotspot manuell einschalten (Schaltfläche „Windows-Hotspot-Einstellungen“).",
        "Ohne Verbindung funktioniert die Anzeige trotzdem: Punkte können direkt am Laptop eingegeben werden.",
    ]


class NetworkManager:
    def __init__(self, settings: Callable[[], object], hotspot: HotspotManager | None = None,
                 scanner: Callable[[], tuple[list[Interface], str]] = address_resolver.scan,
                 wifi: Callable[[], list[wifi_detector.WifiInfo]] = wifi_detector.detect,
                 fingerprint: Callable[[], tuple] = address_resolver.quick_fingerprint):
        self._settings = settings
        self.hotspot = hotspot or HotspotManager()
        self._scan = scanner
        self._wifi = wifi
        self._fingerprint = fingerprint
        self.port = 0
        self.state = NetworkState()
        self._listeners: list[Callable[[NetworkState], None]] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._hotspot_attempted = False

    def on_change(self, fn: Callable[[NetworkState], None]) -> None:
        self._listeners.append(fn)

    # ------------------------------------------------------------------ Erkennung

    def _select(self, interfaces: list[Interface], mode_setting: str) -> tuple[str, Interface | None]:
        wifi = [i for i in interfaces if i.kind == "wifi"]
        wired = [i for i in interfaces if i.kind == "ethernet"]
        other = [i for i in interfaces if i.kind == "other"]
        hotspot = [i for i in interfaces if i.kind == "hotspot"]
        # Private Netze zuerst: dort lässt die Firewall Verbindungen eher zu.
        wifi.sort(key=lambda i: i.category == "Public")
        wired.sort(key=lambda i: i.category == "Public")
        existing = [("wlan", i) for i in wifi] + [("ethernet", i) for i in wired] + [("network", i) for i in other]
        hs = [("hotspot", i) for i in hotspot]
        order = hs + existing if mode_setting == "prefer_hotspot" else existing + hs
        return order[0] if order else ("local", None)

    @staticmethod
    def _mark_hotspot(interfaces: list[Interface], hotspot: HotspotStatus) -> list[Interface]:
        """Bei aktivem Windows-Hotspot ist dessen Adapter oft ein zweiter, gleich benannter WLAN-Adapter (z. B. „WLAN 2“,
        gemessen auf Intel Wi-Fi 7). Erkennbar ist er daran, dass er kein Netzwerkprofil hat – das verbundene WLAN hat
        immer eins. Keine feste IP-Annahme."""
        if not hotspot.active:
            return interfaces
        return [replace(i, kind="hotspot") if i.kind in ("wifi", "other") and not i.profile_name else i for i in interfaces]

    def _wants_hotspot(self, mode_setting: str, mode: str) -> bool:
        if mode_setting == "wlan_only":
            return False
        if mode_setting == "prefer_hotspot":
            return mode != "hotspot"
        return mode == "local"

    def refresh(self, try_hotspot: bool = True) -> NetworkState:
        """Vollständige Prüfung. ``try_hotspot``: bei Bedarf einen Hotspot-Start versuchen (Start, Button)."""
        with self._lock:
            settings = self._settings()
            mode_setting = getattr(settings, "network_mode", "auto")
            hotspot = self.hotspot.status()
            interfaces, source = self._scan()
            interfaces = self._mark_hotspot(interfaces, hotspot)
            mode, primary = self._select(interfaces, mode_setting)
            if try_hotspot and self._wants_hotspot(mode_setting, mode):
                self._hotspot_attempted = True
                hotspot = self.hotspot.start(settings.hotspot_ssid, settings.hotspot_password)
                if hotspot.active:
                    interfaces, source = self._wait_for_hotspot_interface(hotspot)
                    mode, primary = self._select(interfaces, mode_setting)
            ssid = None
            if mode == "wlan":
                infos = [w for w in self._wifi() if w.connected and w.ssid]
                ssid = infos[0].ssid if infos else (primary.profile_name if primary else None)
            elif mode == "hotspot":
                ssid = hotspot.ssid
            warnings = []
            if primary and primary.category == "Public" and mode != "hotspot":
                warnings.append(
                    f"Das Netzwerk „{primary.profile_name or primary.name}“ ist in Windows als „Öffentlich“ "
                    "eingestuft. Die Windows-Firewall blockiert dann meist Verbindungen vom Smartphone. Abhilfe: "
                    "Einstellungen → Netzwerk → „Netzwerk auf Privat stellen und freigeben“.")
            if mode == "local" and try_hotspot and self._wants_hotspot(mode_setting, mode) and hotspot.message:
                warnings.append(hotspot.message)
            state = NetworkState(mode=mode, primary=primary, interfaces=interfaces, ssid=ssid, hotspot=hotspot,
                                 warnings=warnings, hints=_hints(mode, hotspot), source=source,
                                 checked_at=datetime.now().astimezone().isoformat(timespec="seconds"))
            state.reachable = self.check_reachable(state)
            changed = state.key() != self.state.key()
            self.state = state
            log.info("Netzwerk: %s (%s)%s", MODE_LABELS[mode], primary.ip if primary else "keine Adresse",
                     " – geändert" if changed else "")
        if changed:
            self._notify(state)
        return state

    def _wait_for_hotspot_interface(self, hotspot: HotspotStatus, timeout: float = 12.0) -> tuple[list[Interface], str]:
        deadline = time.monotonic() + timeout
        interfaces, source = self._scan()
        interfaces = self._mark_hotspot(interfaces, hotspot)
        while not any(i.kind == "hotspot" for i in interfaces) and time.monotonic() < deadline:
            time.sleep(1.0)
            interfaces, source = self._scan()
            interfaces = self._mark_hotspot(interfaces, hotspot)
        return interfaces, source

    def check_reachable(self, state: NetworkState | None = None) -> bool | None:
        """Antwortet der eigene Server unter der QR-Adresse? (Prüft Bindung/Port; die Firewall prüft nur das Handy.)"""
        state = state or self.state
        if not self.port or state.primary is None:
            return None
        try:
            url = f"http://{state.primary.ip}:{self.port}/api/v1/system/ping"
            with urllib.request.urlopen(url, timeout=3) as resp:
                return resp.status == 200
        except OSError:
            return False

    def _notify(self, state: NetworkState) -> None:
        for fn in list(self._listeners):
            try:
                fn(state)
            except Exception:  # noqa: BLE001
                log.exception("Fehler beim Verteilen eines Netzwerkwechsels")

    # ------------------------------------------------------------------ Überwachung

    def start_monitor(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._monitor, name="network-monitor", daemon=True)
        self._thread.start()

    def _monitor(self) -> None:
        last_fp = self._fingerprint()
        last_full = time.monotonic()
        while not self._stop.wait(POLL_S):
            try:
                fp = self._fingerprint()
                if fp != last_fp or time.monotonic() - last_full > FULL_SCAN_S:
                    if fp != last_fp:
                        log.info("Netzwerkänderung erkannt")
                        time.sleep(1.5)  # DHCP/Profil kurz zur Ruhe kommen lassen
                    last_fp = self._fingerprint()
                    last_full = time.monotonic()
                    self.refresh(try_hotspot=False)
            except Exception:  # noqa: BLE001 – die Überwachung darf nie dauerhaft ausfallen
                log.exception("Netzwerküberwachung")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.hotspot.stop_if_started()

    # ------------------------------------------------------------------ Ausgabe

    def allowed_hosts(self) -> set[str]:
        hosts = {i.ip for i in self.state.interfaces}
        hosts.update(self._fingerprint())
        return hosts

    def base_url(self) -> str:
        host = self.state.primary.ip if self.state.primary else "127.0.0.1"
        return f"http://{host}:{self.port}"

    def to_dict(self) -> dict:
        st = self.state
        return {
            "mode": st.mode,
            "mode_label": MODE_LABELS[st.mode],
            "mode_setting": getattr(self._settings(), "network_mode", "auto"),
            "ip": st.primary.ip if st.primary else None,
            "port": self.port,
            "base_url": self.base_url(),
            "ssid": st.ssid,
            "adapter": st.primary.to_dict() if st.primary else None,
            "interfaces": [i.to_dict() for i in st.interfaces],
            "hotspot": st.hotspot.to_dict(),
            "warnings": st.warnings,
            "hints": st.hints,
            "detection": st.source,
            "checked_at": st.checked_at,
            "reachable": st.reachable,
        }
