"""Pfade, Einstellungen und Geheimnisse von Flip 7 Live.

Alle persistenten Daten liegen unter ``%LOCALAPPDATA%\\Flip7Live`` (überschreibbar per Umgebungsvariable
``FLIP7LIVE_HOME``, z. B. für Tests). Nie im PyInstaller-Entpackungsverzeichnis.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from .security.schema import F, Model, ValidationError

APP_NAME = "Flip7Live"
APP_TITLE = "Flip 7 Live"

log = logging.getLogger(__name__)


def resource_path(*parts: str) -> Path:
    """Pfad zu mitgelieferten Dateien (Templates, Static, Migrationen) – im Build aus dem Entpackungsordner."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def default_home() -> Path:
    override = os.environ.get("FLIP7LIVE_HOME")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / APP_NAME if local else Path.home() / f".{APP_NAME.lower()}"


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def database(self) -> Path:
        return self.data / "flip7live.db"

    @property
    def settings_file(self) -> Path:
        return self.config / "settings.json"

    @property
    def secrets_file(self) -> Path:
        return self.config / "secrets.json"

    @property
    def instance_file(self) -> Path:
        """Port und IPC-Geheimnis der laufenden Instanz (für den Zweitstart)."""
        return self.config / "instance.json"

    @property
    def log_file(self) -> Path:
        return self.logs / "application.log"

    def ensure(self) -> "Paths":
        for p in (self.data, self.config, self.logs, self.backups):
            p.mkdir(parents=True, exist_ok=True)
        return self


def _generate_hotspot_password() -> str:
    # Ohne leicht verwechselbare Zeichen (0/O, 1/l/I), damit man es auch abtippen kann.
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


NetworkMode = Literal["auto", "wlan_only", "prefer_hotspot"]


class Settings(Model):
    """Benutzereinstellungen (``settings.json``)."""

    extra = "ignore"
    autostart = F("bool", default=False)
    open_browser = F("bool", default=True)
    default_target_score = F("int", default=200, ge=1, le=10_000)
    default_player_count = F("int", default=4, ge=2, le=18)
    language = F("choice", choices=("de",), default="de")
    network_mode = F("choice", choices=("auto", "wlan_only", "prefer_hotspot"), default="auto")
    port = F("int", default=5000, ge=1024, le=65535)
    hotspot_ssid = F("str", default="Flip7-Live", min_len=1, max_len=32)
    hotspot_password = F("str", factory=_generate_hotspot_password, min_len=8, max_len=63)
    max_round_points = F("int", default=500, ge=1, le=100_000)
    backup_keep = F("int", default=20, ge=1, le=500)
    # Einrichtungsassistent beim ersten Start schon durchlaufen?
    setup_completed = F("bool", default=False)


class Secrets(Model):
    """Geheimnisse (``secrets.json``) – werden nie geloggt."""

    extra = "ignore"
    secret_key = F("str", factory=lambda: secrets.token_hex(32))
    controller_token = F("str", factory=lambda: secrets.token_urlsafe(24))
    viewer_token = F("str", factory=lambda: secrets.token_urlsafe(24))
    controller_generation = F("int", default=1)
    viewer_generation = F("int", default=1)


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class _JsonStore[T: Model]:
    """Lädt/speichert ein Modell als JSON. Eine beschädigte Datei wird beiseitegelegt und durch Standardwerte ersetzt."""

    def __init__(self, path: Path, model: type[T]):
        self.path = path
        self.model = model
        self._lock = threading.Lock()
        self.recovered_from: Path | None = None
        self.value: T = self._load()

    def _load(self) -> T:
        if not self.path.exists():
            value = self.model()
            _atomic_write_json(self.path, value.model_dump())
            return value
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))  # auch mit BOM (z. B. aus PowerShell)
            if not isinstance(raw, dict):
                raise ValueError("Kein JSON-Objekt")
            value = self.model.model_validate(raw)
        except (OSError, ValueError, ValidationError) as exc:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            broken = self.path.with_name(f"{self.path.stem}.defekt-{stamp}{self.path.suffix}")
            try:
                os.replace(self.path, broken)
                self.recovered_from = broken
            except OSError:
                pass
            log.warning("Konfigurationsdatei %s war beschädigt (%s) – Standardwerte verwendet, Original: %s",
                        self.path.name, type(exc).__name__, broken.name)
            value = self.model()
        # Neu hinzugekommene Felder (z. B. erzeugtes Hotspot-Passwort) sofort festschreiben.
        _atomic_write_json(self.path, value.model_dump())
        return value

    def update(self, **changes) -> T:
        with self._lock:
            merged = {**self.value.model_dump(), **changes}
            value = self.model.model_validate(merged)
            _atomic_write_json(self.path, value.model_dump())
            self.value = value
            return value


class SettingsStore(_JsonStore[Settings]):
    def __init__(self, paths: Paths):
        super().__init__(paths.settings_file, Settings)


class SecretsStore(_JsonStore[Secrets]):
    def __init__(self, paths: Paths):
        super().__init__(paths.secrets_file, Secrets)

    def rotate(self, role: Literal["controller", "viewer"]) -> Secrets:
        cur = self.value
        if role == "controller":
            return self.update(controller_token=secrets.token_urlsafe(24),
                               controller_generation=cur.controller_generation + 1)
        return self.update(viewer_token=secrets.token_urlsafe(24), viewer_generation=cur.viewer_generation + 1)
