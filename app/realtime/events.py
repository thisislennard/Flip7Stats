"""Socket.IO-Ereignisse. Verbindungen sind nur mit gültiger Sitzung möglich; über den Socket wird nur gelesen.

Alle Schreibvorgänge laufen über die REST-API (mit CSRF-Schutz und Idempotenz). Jedes vom Client gesendete
Ereignis außer ``state:request`` wird mit einer Fehlermeldung beantwortet.
"""

from __future__ import annotations

import logging

from flask import current_app, request
from flask_socketio import ConnectionRefusedError, emit, join_room

from ..extensions import socketio
from ..security.authentication import ADMIN, WRITERS, host_allowed, session_state

log = logging.getLogger(__name__)


def _rt():
    return current_app.extensions["flip7"]


@socketio.on("connect")
def on_connect(auth=None):
    ctx = _rt()
    if not host_allowed(request.host, ctx.network.allowed_hosts()):
        raise ConnectionRefusedError("host_not_allowed")
    role, reason = session_state()
    if role is None:
        raise ConnectionRefusedError(reason or "unauthenticated")
    join_room("all")
    if role in WRITERS:
        join_room("writers")
    if role == ADMIN:
        join_room("admin")
    ctx.realtime.add_client(request.sid, role, request.remote_addr or "")
    emit("system:status", {"connected": True, "role": role, "server_version": ctx.version})
    _send_state()


@socketio.on("disconnect")
def on_disconnect(*_args):
    _rt().realtime.remove_client(request.sid)


def _send_state():
    ctx = _rt()
    gid = ctx.games.display_game_id()
    state = ctx.games.snapshot(gid) if gid else None
    emit("game:updated", {"game_id": gid, "state": state})


@socketio.on("state:request")
def on_state_request(*_args):
    """Nach einer Wiederverbindung: aktuellen Stand neu anfordern."""
    role, _ = session_state()
    if role is None:
        emit("error", {"code": "unauthenticated", "message": "Sitzung ungültig."})
        return
    _send_state()


@socketio.on("*")
def on_other(event, *_args):
    log.info("Abgewiesenes Socket-Ereignis %r von %s", event, request.remote_addr)
    emit("error", {"code": "write_not_allowed", "event": str(event)[:40],
                   "message": "Über die Live-Verbindung sind keine Änderungen möglich."})
