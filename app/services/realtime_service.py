"""Verteilt Änderungen per Socket.IO an alle berechtigten Geräte.

Der Server ist die einzige Wahrheit: Nach jeder Änderung wird der Spielstand neu aus der Datenbank berechnet und
vollständig verschickt (``game:updated``). Die spezifischen Ereignisse (``round:created`` …) tragen zusätzlich die
jeweils relevanten Teile, damit eine Oberfläche gezielt reagieren (z. B. animieren) kann.

Räume: ``all`` (jede gültige Sitzung), ``writers`` (Admin + Steuerung), ``admin`` (nur Laptop).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable

from flask_socketio import SocketIO

from .game_service import Change, GameService

log = logging.getLogger(__name__)


class RealtimeService:
    def __init__(self, socketio: SocketIO, games: GameService):
        self.socketio = socketio
        self.games = games
        self.clients: dict[str, dict] = {}
        self.last_sync: str | None = None
        self._lock = threading.Lock()
        self.admin_status: Callable[[], dict] | None = None

    # ------------------------------------------------------------------ Clients

    def add_client(self, sid: str, role: str, ip: str) -> None:
        with self._lock:
            self.clients[sid] = {"role": role, "ip": ip, "connected_at": datetime.now().astimezone().isoformat(
                timespec="seconds")}
        self._emit_clients("client:connected", sid)

    def remove_client(self, sid: str) -> None:
        with self._lock:
            info = self.clients.pop(sid, None)
        if info:
            self._emit_clients("client:disconnected", sid)

    def client_summary(self) -> dict:
        with self._lock:
            roles = [c["role"] for c in self.clients.values()]
        return {"total": len(roles), "admin": roles.count("admin"), "controller": roles.count("controller"),
                "viewer": roles.count("viewer")}

    def client_list(self) -> list[dict]:
        with self._lock:
            return [dict(c) for c in self.clients.values()]

    def _emit_clients(self, event: str, sid: str) -> None:
        self.socketio.emit(event, {"clients": self.client_summary()}, to="admin")

    # ------------------------------------------------------------------ Spiel

    def touch(self) -> None:
        self.last_sync = datetime.now().astimezone().isoformat(timespec="seconds")

    def broadcast(self, change: Change) -> None:
        if change.game_id is None or not change.events:
            return
        try:
            state = self.games.snapshot(change.game_id)
        except Exception:  # noqa: BLE001 – z. B. Spiel gerade gelöscht
            log.exception("Spielstand für Live-Update nicht ermittelbar")
            return
        gid = change.game_id
        payloads = {
            "game:created": {"game_id": gid, "game": state["game"]},
            "game:started": {"game_id": gid, "game": state["game"]},
            "game:finished": {"game_id": gid, "game": state["game"], "winners": state["winners"], "tie": state["tie"]},
            "round:created": {"game_id": gid, **change.data},
            "round:updated": {"game_id": gid, **change.data},
            "round:deleted": {"game_id": gid, **change.data},
            "scores:updated": {"game_id": gid, "rounds": state["rounds"]},
            "leaderboard:updated": {"game_id": gid, "leaderboard": state["leaderboard"], "leaders": state["leaders"],
                                    "tie": state["tie"]},
            "game:updated": {"game_id": gid, "state": state},
        }
        # game:updated als Letztes: trägt den vollständigen Stand, auf den sich alle Anzeigen verlassen.
        ordered = [e for e in change.events if e != "game:updated"] + ["game:updated"]
        for event in ordered:
            self.socketio.emit(event, payloads.get(event, {"game_id": gid}), to="all")
        self.touch()

    def broadcast_reset(self, reason: str) -> None:
        """Nach Wiederherstellung o. Ä.: alle Geräte holen den Stand neu."""
        self.socketio.emit("system:status", {"reload": True, "reason": reason}, to="all")
        self.touch()

    def emit_network(self, public: dict, admin: dict) -> None:
        self.socketio.emit("network:changed", public, to="all")
        self.socketio.emit("network:changed", admin, to="admin")

    def emit_status(self, status: dict) -> None:
        self.socketio.emit("system:status", status, to="admin")
