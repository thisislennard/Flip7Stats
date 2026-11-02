"""Rollen, Sitzungen und Anfrageprüfungen.

Rollen (ohne Benutzerkonten):
- ``admin``      – Browser auf dem Laptop. Nur über einen einmaligen Anmeldelink, den die App selbst öffnet, und nur
                   solange die Anfrage tatsächlich von 127.0.0.1/::1 kommt (Socket-Adresse, kein Header).
- ``controller`` – Smartphone zur Punkteingabe (Steuerungs-QR-Code).
- ``viewer``     – Zuschauer, nur lesen (Zuschauer-QR-Code).

Ein Zugriff aus dem WLAN erhält nie Administratorrechte – auch nicht mit kopiertem Admin-Cookie.
"""

from __future__ import annotations

import ipaddress
import secrets
import threading
import time
from collections import deque
from functools import wraps
from typing import Callable, Iterable

from flask import abort, current_app, jsonify, request, session

from .tokens import matches

ADMIN, CONTROLLER, VIEWER = "admin", "controller", "viewer"
WRITERS = (ADMIN, CONTROLLER)
EVERYONE = (ADMIN, CONTROLLER, VIEWER)
SESSION_MAX_AGE_S = 24 * 3600
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def is_loopback(addr: str | None) -> bool:
    if not addr:
        return False
    try:
        return ipaddress.ip_address(addr.split("%")[0]).is_loopback
    except ValueError:
        return False


def request_is_local() -> bool:
    # remote_addr stammt aus der TCP-Verbindung; ohne ProxyFix kann kein Header sie verändern.
    return is_loopback(request.remote_addr)


def _secrets():
    return current_app.extensions["flip7"].secrets.value


def login(role: str) -> None:
    s = _secrets()
    session.clear()
    session.permanent = True
    session["role"] = role
    session["iat"] = int(time.time())
    session["csrf"] = secrets.token_urlsafe(24)
    if role == CONTROLLER:
        session["gen"] = s.controller_generation
    elif role == VIEWER:
        session["gen"] = s.viewer_generation


def session_state() -> tuple[str | None, str | None]:
    """(Rolle, Grund) der aktuellen Anfrage. Grund ist gesetzt, wenn eine vorhandene Sitzung ungültig ist."""
    role = session.get("role")
    if role not in EVERYONE:
        return None, None
    if int(time.time()) - int(session.get("iat", 0)) > SESSION_MAX_AGE_S:
        return None, "expired"
    s = _secrets()
    if role == ADMIN and not request_is_local():
        return None, "admin_remote"
    if role == CONTROLLER and session.get("gen") != s.controller_generation:
        return None, "revoked"
    if role == VIEWER and session.get("gen") != s.viewer_generation:
        return None, "revoked"
    return role, None


def current_role() -> str | None:
    return session_state()[0]


def csrf_token() -> str:
    return session.get("csrf", "")


_REASONS = {
    "expired": "Die Sitzung ist abgelaufen. Bitte den QR-Code am Laptop erneut scannen.",
    "revoked": "Der Zugang wurde am Laptop erneuert. Bitte den neuen QR-Code scannen.",
    "admin_remote": "Administratorrechte gibt es nur direkt am Laptop.",
}


def _deny(status: int, code: str, message: str):
    resp = jsonify({"ok": False, "error": {"code": code, "message": message}})
    resp.status_code = status
    return resp


def require(*roles: str) -> Callable:
    """API-Schutz: Rolle prüfen, bei Schreibzugriffen zusätzlich CSRF-Token."""
    allowed = set(roles)

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            role, reason = session_state()
            if role is None:
                return _deny(401, reason or "unauthenticated",
                             _REASONS.get(reason, "Kein Zugang. Bitte den QR-Code am Laptop scannen."))
            if role not in allowed:
                return _deny(403, "forbidden", "Für diese Aktion fehlt die Berechtigung.")
            if request.method in STATE_CHANGING:
                if not matches(request.headers.get("X-CSRF-Token"), session.get("csrf")):
                    return _deny(403, "csrf", "Sicherheitsprüfung fehlgeschlagen. Bitte die Seite neu laden.")
            return fn(*args, **kwargs)
        return wrapper
    return deco


def host_allowed(host_header: str | None, allowed: Iterable[str]) -> bool:
    """Schutz vor DNS-Rebinding: nur Aufrufe über eigene Adressen (localhost oder eigene Netzwerk-IPs)."""
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):
        host = host[1:host.find("]")] if "]" in host else host
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    return host in LOOPBACK_NAMES or host in set(allowed)


def origin_allowed() -> bool:
    """Fremde Webseiten dürfen keine Schreibanfragen schicken: Origin muss zur eigenen Adresse passen."""
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return False
    origin = request.headers.get("Origin")
    if origin is None:
        return True  # ältere Browser senden bei gleicher Herkunft teils keinen Origin; das CSRF-Token schützt trotzdem
    return origin.rstrip("/") == request.host_url.rstrip("/")


class RateLimiter:
    """Gleitendes Zeitfenster je Client-IP und Bereich (z. B. Anmeldungen, Schreibzugriffe)."""

    def __init__(self, limits: dict[str, tuple[int, float]]):
        self.limits = limits
        self._hits: dict[tuple[str, str], deque] = {}
        self._lock = threading.Lock()

    def hit(self, bucket: str, key: str) -> bool:
        limit, window = self.limits[bucket]
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault((bucket, key), deque())
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 10_000:  # Speicher begrenzen
                self._hits = {k: d for k, d in self._hits.items() if d and now - d[-1] <= window}
            return True


def page_role_or_abort(*roles: str) -> str:
    role = current_role()
    if role is None or role not in roles:
        abort(403)
    return role
