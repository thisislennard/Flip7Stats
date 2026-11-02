"""Gemeinsam genutzte Infrastruktur: Datenbankzugang und Socket.IO-Server."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from flask_socketio import SocketIO
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

# Threading-Modus: läuft unter Windows ohne eventlet/gevent; WebSockets über simple-websocket.
socketio = SocketIO(async_mode="threading", logger=False, engineio_logger=False)


class DatabaseBusy(RuntimeError):
    """Die Datenbank blieb trotz Wartezeit gesperrt."""


def _sqlite_pragmas(dbapi_conn: sqlite3.Connection, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


class Database:
    """SQLite-Zugriff. Alle Schreibvorgänge laufen serialisiert in einer Transaktion (``write()``).

    Die Schreibsperre stellt außerdem sicher, dass eine Wiederherstellung oder das Beenden nie mitten in einen
    Schreibvorgang fällt.
    """

    def __init__(self, path: Path):
        self.path = path
        self.write_lock = threading.RLock()
        self.engine: Engine = create_engine(
            f"sqlite:///{path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        event.listen(self.engine, "connect", _sqlite_pragmas)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def read(self) -> Iterator[Session]:
        with self._factory() as s:
            yield s

    @contextmanager
    def write(self) -> Iterator[Session]:
        """Transaktion mit Sperre. Ist die Datei kurz gesperrt (z. B. Virenscanner), wartet SQLite bis zu 10 s
        (busy_timeout); bleibt sie gesperrt, wird die Transaktion zurückgerollt und ``DatabaseBusy`` gemeldet."""
        with self.write_lock, self._factory() as s:
            try:
                with s.begin():
                    yield s
            except OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    raise DatabaseBusy("Die Datenbank ist vorübergehend gesperrt. Bitte erneut versuchen.") from exc
                raise

    def check(self) -> dict:
        try:
            with self.engine.connect() as c:
                mode = c.exec_driver_sql("PRAGMA journal_mode").scalar()
                ok = c.exec_driver_sql("PRAGMA quick_check").scalar()
            return {"ok": ok == "ok", "journal_mode": mode, "path": str(self.path),
                    "size_bytes": self.path.stat().st_size if self.path.exists() else 0}
        except Exception as exc:  # noqa: BLE001 – Diagnose soll nie selbst abstürzen
            return {"ok": False, "error": type(exc).__name__, "path": str(self.path)}

    def dispose(self) -> None:
        with self.write_lock:
            self.engine.dispose()
