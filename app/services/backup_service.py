"""Sicherungen der SQLite-Datenbank.

Gesichert wird ausschließlich über die SQLite-Online-Backup-API – sie liefert auch im WAL-Modus einen
konsistenten Stand (die Datei wird nie einfach kopiert). Wiederherstellung läuft unter der Schreibsperre der
Anwendung, damit kein Speichervorgang überschrieben wird, und legt vorher eine Sicherheitskopie an.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import Paths
from ..extensions import Database

log = logging.getLogger(__name__)

_NAME = re.compile(r"^flip7live-(\d{8}-\d{6})-([a-z]+)(?:-(\d+))?\.db$")
REQUIRED_TABLES = {"games", "players", "game_players", "rounds", "round_scores", "alembic_version"}
REASONS = {
    "manual": "Manuell",
    "auto": "Automatisch",
    "finish": "Spielende",
    "migration": "Vor Datenbank-Update",
    "prerestore": "Vor Wiederherstellung",
    "startup": "Programmstart",
    "predelete": "Vor Löschen eines Spielers",
    "prereset": "Vor Löschen aller Daten",
}
AUTO_INTERVAL_S = 10 * 60


def _age_key(path: Path) -> tuple[str, int]:
    """Zeitstempel + laufende Nummer (…-manual.db ist älter als …-manual-1.db aus derselben Sekunde)."""
    m = _NAME.match(path.name)
    return (m.group(1), int(m.group(3) or 0)) if m else ("", 0)


class BackupError(RuntimeError):
    pass


class BackupService:
    def __init__(self, db: Database, paths: Paths, keep: Callable[[], int],
                 known_revisions: Callable[[], set[str]]):
        self.db = db
        self.paths = paths
        self._keep = keep
        self._known_revisions = known_revisions
        self._last_auto = 0.0
        self._lock = threading.Lock()

    def _target(self, reason: str) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        # Laufende Nummer immer höher als alle vorhandenen derselben Sekunde – auch wenn ältere schon gelöscht sind.
        same = [_age_key(p)[1] for p in self.paths.backups.glob(f"flip7live-{stamp}-*.db") if _NAME.match(p.name)]
        if not same:
            return self.paths.backups / f"flip7live-{stamp}-{reason}.db"
        return self.paths.backups / f"flip7live-{stamp}-{reason}-{max(same) + 1}.db"

    def create(self, reason: str = "manual") -> dict:
        if reason not in REASONS:
            raise ValueError(reason)
        if not self.db.path.exists():
            raise BackupError("Es gibt noch keine Datenbank, die gesichert werden könnte.")
        target = self._target(reason)
        tmp = target.with_suffix(".tmp")
        with self.db.write_lock, self._lock:  # gleiche Reihenfolge wie restore() → keine Verklemmung
            src = sqlite3.connect(self.db.path)
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            os.replace(tmp, target)
        log.info("Sicherung erstellt: %s", target.name)
        self._prune()
        return self._info(target)

    def maybe_auto(self, reason: str = "auto") -> dict | None:
        """Regelmäßige Sicherung bei Spieländerungen – höchstens alle 10 Minuten (Spielende immer)."""
        now = time.monotonic()
        if reason != "finish" and now - self._last_auto < AUTO_INTERVAL_S:
            return None
        self._last_auto = now
        try:
            return self.create(reason)
        except Exception:  # noqa: BLE001 – eine fehlgeschlagene Hintergrundsicherung darf das Spiel nicht stören
            log.exception("Automatische Sicherung fehlgeschlagen")
            return None

    def _prune(self) -> None:
        keep = self._keep()
        files = sorted((p for p in self.paths.backups.glob("flip7live-*.db") if _NAME.match(p.name)),
                       key=_age_key, reverse=True)
        for old in files[keep:]:
            try:
                old.unlink()
            except OSError:
                log.warning("Alte Sicherung %s konnte nicht gelöscht werden", old.name)

    def _info(self, path: Path) -> dict:
        m = _NAME.match(path.name)
        created = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S") if m else datetime.fromtimestamp(path.stat().st_mtime)
        reason = m.group(2) if m else "manual"
        return {"name": path.name, "created_at": created.isoformat(timespec="seconds"), "reason": reason,
                "reason_label": REASONS.get(reason, reason), "size_bytes": path.stat().st_size}

    def list(self) -> list[dict]:
        files = sorted(self.paths.backups.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [self._info(p) for p in files]

    def _resolve(self, name: str) -> Path:
        path = (self.paths.backups / name).resolve()
        if path.parent != self.paths.backups.resolve() or not path.is_file():
            raise BackupError("Diese Sicherung gibt es nicht.")
        return path

    def validate(self, name: str) -> dict:
        path = self._resolve(name)
        try:
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            return {"ok": False, "error": f"Datei lässt sich nicht öffnen ({exc})."}
        try:
            check = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                return {"ok": False, "error": "Die Sicherung ist beschädigt."}
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = REQUIRED_TABLES - tables
            if missing:
                return {"ok": False, "error": "Keine Flip-7-Live-Sicherung (Tabellen fehlen)."}
            revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            revision = revision[0] if revision else None
            if revision not in self._known_revisions():
                return {"ok": False, "error": "Die Sicherung stammt von einer neueren Programmversion."}
            games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
            return {"ok": True, "revision": revision, "games": games}
        except sqlite3.DatabaseError as exc:
            return {"ok": False, "error": f"Keine gültige Datenbankdatei ({exc})."}
        finally:
            conn.close()

    def restore(self, name: str, after: Callable[[], object] | None = None) -> dict:
        """Stellt eine geprüfte Sicherung wieder her. ``after`` (Migration auf den aktuellen Stand) läuft noch unter
        der Schreibsperre."""
        result = self.validate(name)
        if not result["ok"]:
            raise BackupError(result["error"])
        path = self._resolve(name)
        with self.db.write_lock:
            safety = self.create("prerestore")
            src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            dst = sqlite3.connect(self.db.path)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            self.db.engine.dispose()  # alte Verbindungen verwerfen, neue sehen den wiederhergestellten Stand
            if after is not None:
                after()
        log.info("Sicherung %s wiederhergestellt (Sicherheitskopie: %s)", name, safety["name"])
        return {"restored": name, "safety_backup": safety["name"], "games": result["games"]}
