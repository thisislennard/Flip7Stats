"""Windows-Mobiler-Hotspot über die WinRT-Tethering-API (NetworkOperatorTetheringManager).

Einschränkungen (dokumentiert in docs/ARCHITECTURE.md):
- Windows teilt beim mobilen Hotspot immer eine bestehende Verbindung. Ohne jede Netzwerkverbindung lässt sich der
  Hotspot auf vielen Geräten nicht starten. Das wird ehrlich gemeldet, nie ein Erfolg vorgetäuscht.
- Gruppenrichtlinien, Treiber oder Hardware können den Hotspot sperren (Fähigkeit ≠ "Enabled").
- Der veraltete "Hosted Network"-Weg (netsh wlan hostednetwork) wird bewusst nicht verwendet.

Erfolg gilt erst, wenn Windows den Zustand "On" meldet. Die Adresse des Hotspots (meist 192.168.137.1) ermittelt
anschließend die normale Adaptererkennung – es wird keine IP fest angenommen.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass

from . import powershell

log = logging.getLogger(__name__)

SETTINGS_URI = "ms-settings:network-mobilehotspot"

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$out = [ordered]@{ ok = $false; action = $env:FLIP7_ACTION }
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $ext = [System.WindowsRuntimeSystemExtensions].GetMethods()
  $asTaskOp = $ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
                                    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
  $asTaskAct = $ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
                                     $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } | Select-Object -First 1
  function AwaitOp($op, [Type]$t) {
    $task = $asTaskOp.MakeGenericMethod($t).Invoke($null, @($op))
    if (-not $task.Wait(30000)) { throw 'Zeitüberschreitung' }
    $task.Result
  }
  function AwaitAct($a) {
    $task = $asTaskAct.Invoke($null, @($a))
    if (-not $task.Wait(30000)) { throw 'Zeitüberschreitung' }
  }
  $NI = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
  $TM = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
  $RT = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
  $profile = $NI::GetInternetConnectionProfile()
  $out.source = 'internet'
  if (-not $profile) {
    $profile = @($NI::GetConnectionProfiles() | Where-Object { $_.GetNetworkConnectivityLevel() -ne 0 })[0]
    $out.source = if ($profile) { 'local' } else { 'none' }
  }
  if (-not $profile) {
    $out.capability = 'NoSourceConnection'
    $out.state = 'Off'
  } else {
    $out.capability = [string]$TM::GetTetheringCapabilityFromConnectionProfile($profile)
    $m = $TM::CreateFromConnectionProfile($profile)
    if ($env:FLIP7_ACTION -eq 'start' -and $out.capability -eq 'Enabled') {
      $cfg = $m.GetCurrentAccessPointConfiguration()
      if ($cfg.Ssid -ne $env:FLIP7_SSID -or $cfg.Passphrase -ne $env:FLIP7_PASS) {
        $cfg.Ssid = $env:FLIP7_SSID
        $cfg.Passphrase = $env:FLIP7_PASS
        AwaitAct ($m.ConfigureAccessPointAsync($cfg))
      }
      if ([string]$m.TetheringOperationalState -ne 'On') {
        $res = AwaitOp ($m.StartTetheringAsync()) $RT
        $out.result = [string]$res.Status
        $out.message = $res.AdditionalErrorMessage
      }
    } elseif ($env:FLIP7_ACTION -eq 'stop' -and [string]$m.TetheringOperationalState -ne 'Off') {
      $res = AwaitOp ($m.StopTetheringAsync()) $RT
      $out.result = [string]$res.Status
      $out.message = $res.AdditionalErrorMessage
    }
    $out.state = [string]$m.TetheringOperationalState
    $out.clients = $m.ClientCount
    $out.ssid = $m.GetCurrentAccessPointConfiguration().Ssid
  }
  $out.ok = $true
} catch {
  $out.error = $_.Exception.Message
}
[pscustomobject]$out | ConvertTo-Json -Compress
"""

CAPABILITY_TEXT = {
    "Enabled": "Der Windows-Hotspot ist auf diesem Gerät verfügbar.",
    "NoSourceConnection": "Windows kann den mobilen Hotspot nur starten, wenn der Laptop selbst mit einem Netzwerk "
                          "verbunden ist (WLAN, LAN oder Mobilfunk). Ohne Verbindung bitte den Hotspot des Smartphones "
                          "nutzen.",
    "DisabledByGroupPolicy": "Der Hotspot ist durch eine Richtlinie (z. B. Firmen-IT) gesperrt.",
    "DisabledByHardwareLimitation": "Der WLAN-Adapter dieses Geräts unterstützt keinen Hotspot.",
    "DisabledByOperator": "Der Hotspot ist durch den Mobilfunkanbieter gesperrt.",
    "DisabledBySku": "Diese Windows-Edition unterstützt keinen Hotspot.",
    "DisabledByRequiredAppNotInstalled": "Für den Hotspot fehlt eine erforderliche App des Anbieters.",
    "DisabledDueToUnknownCause": "Windows meldet den Hotspot als nicht verfügbar (Ursache unbekannt).",
    "DisabledBySystemCapability": "Der Hotspot ist durch eine Systemeinstellung deaktiviert.",
    "Unsupported": "Hotspot-Steuerung ist nur unter Windows möglich.",
    "Unknown": "Der Hotspot-Status konnte nicht ermittelt werden.",
}


@dataclass
class HotspotStatus:
    capability: str = "Unknown"
    state: str = "Off"          # "On" | "Off" | "InTransition" | "Unknown"
    ssid: str | None = None
    clients: int = 0
    source: str | None = None   # "internet" | "local" | "none"
    message: str | None = None
    started_by_app: bool = False

    @property
    def available(self) -> bool:
        return self.capability == "Enabled"

    @property
    def active(self) -> bool:
        return self.state == "On"

    def to_dict(self) -> dict:
        return {"capability": self.capability, "capability_text": CAPABILITY_TEXT.get(self.capability, self.capability),
                "available": self.available, "state": self.state, "active": self.active, "ssid": self.ssid,
                "clients": self.clients, "source": self.source, "message": self.message,
                "started_by_app": self.started_by_app}


class HotspotManager:
    def __init__(self, runner=None):
        self._run = runner or powershell.run_json
        self.started_by_app = False

    def _call(self, action: str, ssid: str = "", password: str = "", timeout: float = 45) -> dict:
        if not powershell.available() and self._run is powershell.run_json:
            return {"ok": True, "capability": "Unsupported", "state": "Off"}
        try:
            return self._run(_SCRIPT, env={"FLIP7_ACTION": action, "FLIP7_SSID": ssid, "FLIP7_PASS": password},
                             timeout=timeout)
        except powershell.PowerShellError as exc:
            return {"ok": False, "error": str(exc)}

    def _status(self, data: dict) -> HotspotStatus:
        if not data.get("ok"):
            return HotspotStatus(capability="Unknown", state="Unknown",
                                 message="Der Hotspot-Status konnte nicht abgefragt werden.",
                                 started_by_app=self.started_by_app)
        return HotspotStatus(capability=str(data.get("capability") or "Unknown"), state=str(data.get("state") or "Off"),
                             ssid=data.get("ssid"), clients=int(data.get("clients") or 0), source=data.get("source"),
                             started_by_app=self.started_by_app)

    def status(self) -> HotspotStatus:
        return self._status(self._call("status", timeout=20))

    def start(self, ssid: str, password: str) -> HotspotStatus:
        """Versucht den Start und prüft den tatsächlichen Zustand. Gibt nie „aktiv“ zurück, wenn Windows das nicht meldet."""
        data = self._call("start", ssid, password)
        status = self._status(data)
        if not data.get("ok"):
            status.message = "Windows hat den Hotspot-Start abgelehnt: " + str(data.get("error") or "unbekannter Fehler")
            log.warning("Hotspot-Start fehlgeschlagen: %s", data.get("error"))
            return status
        if not status.available:
            status.message = CAPABILITY_TEXT.get(status.capability, "Hotspot nicht verfügbar.")
            return status
        if status.active:
            self.started_by_app = self.started_by_app or data.get("result") is not None
            status.started_by_app = self.started_by_app
            status.message = f"Hotspot „{status.ssid}“ ist aktiv."
            log.info("Hotspot aktiv (SSID %s)", status.ssid)
        else:
            detail = data.get("message") or data.get("result") or "unbekannte Ursache"
            status.message = f"Windows hat den Hotspot nicht gestartet ({detail})."
            log.warning("Hotspot nicht gestartet: %s", detail)
        return status

    def stop_if_started(self) -> None:
        """Beim Beenden: nur einen Hotspot stoppen, den Flip 7 Live selbst gestartet hat."""
        if self.started_by_app:
            data = self._call("stop", timeout=20)
            log.info("Hotspot gestoppt: %s", data.get("state"))
            self.started_by_app = False

    @staticmethod
    def open_settings() -> bool:
        """Öffnet die Windows-Einstellungsseite „Mobiler Hotspot“ für die manuelle Aktivierung."""
        if not powershell.available():
            return False
        try:
            os.startfile(SETTINGS_URI)  # type: ignore[attr-defined]
            return True
        except OSError:
            try:
                subprocess.Popen(["explorer.exe", SETTINGS_URI], creationflags=powershell.CREATE_NO_WINDOW)
                return True
            except OSError:
                return False
