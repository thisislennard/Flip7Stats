"""Anwendungskontext, Flask-App-Fabrik und Webserver.

Der Kontext (:class:`Flip7Context`) bündelt alle Dienste einer laufenden Instanz. Die App-Fabrik verdrahtet
Seiten, API, Socket.IO und die Sicherheitsprüfungen, die vor jeder Anfrage laufen.
"""

from __future__ import annotations

import logging
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable
from urllib.parse import quote

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.serving import ThreadedWSGIServer

from . import __version__, database
from . import system_integration as si
from .config import APP_TITLE, Paths, SecretsStore, SettingsStore, resource_path
from .extensions import Database, DatabaseBusy, socketio
from .logging_setup import redact
from .network import qr_manager
from .network.network_manager import NetworkManager
from .security import authentication as auth
from .security.tokens import OneTimeTokens, matches
from .services import ServiceError
from .services.backup_service import BackupService
from .services.game_service import Change, GameService
from .services.history_service import HistoryService
from .services.night_service import NightService
from .services.realtime_service import RealtimeService

log = logging.getLogger(__name__)
access_log = logging.getLogger("flip7live.access")

RATE_LIMITS = {
    "api": (600, 60.0),    # alle Anfragen je IP und Minute
    "write": (120, 60.0),  # Schreibzugriffe
    "auth": (20, 60.0),    # Zugangsschlüssel-Prüfungen (gegen Ausprobieren)
}


@dataclass
class Flip7Context:
    paths: Paths
    settings: SettingsStore
    secrets: SecretsStore
    db: Database
    network: NetworkManager
    version: str = __version__
    started_at: float = field(default_factory=time.time)
    ipc_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    shutdown_callback: Callable[[], None] | None = None

    def __post_init__(self):
        self.games = GameService(self.db, lambda: self.settings.value.max_round_points)
        self.history = HistoryService(self.db)
        self.nights = NightService(self.db, self.games)
        self.backup = BackupService(self.db, self.paths, lambda: self.settings.value.backup_keep,
                                    lambda: database.known_revisions(self.db.engine))
        self.realtime = RealtimeService(socketio, self.games)
        self.admin_tokens = OneTimeTokens(ttl_seconds=120)
        self.limiter = auth.RateLimiter(RATE_LIMITS)
        self.network.on_change(lambda _state: self.publish_network())

    # ------------------------------------------------------------------ Änderungen

    def after_change(self, change: Change, backup_reason: str | None = None) -> None:
        self.realtime.broadcast(change)
        if backup_reason:
            threading.Thread(target=self.backup.maybe_auto, args=(backup_reason,), name="backup", daemon=True).start()

    def restore_backup(self, name: str) -> dict:
        result = self.backup.restore(name, after=lambda: database.upgrade(self.db.engine))
        self.realtime.broadcast_reset("restore")
        return result

    def request_shutdown(self) -> None:
        if self.shutdown_callback:
            threading.Timer(0.5, self.shutdown_callback).start()

    # ------------------------------------------------------------------ Adressen

    def admin_login_url(self, next_path: str = "/") -> str:
        return (f"http://127.0.0.1:{self.network.port}/admin/login?once={self.admin_tokens.issue()}"
                f"&next={quote(next_path)}")

    def network_info(self, role: str | None) -> dict:
        full = self.network.to_dict()
        base = full["base_url"]
        sec = self.secrets.value
        info = {k: full[k] for k in ("mode", "mode_label", "ip", "port", "ssid", "checked_at", "base_url",
                                     "reachable")}
        info["clients"] = self.realtime.client_summary()
        if role in auth.WRITERS:
            info["viewer_url"] = f"{base}/dashboard?key={sec.viewer_token}"
            info.update({k: full[k] for k in ("warnings", "hints", "hotspot", "mode_setting")})
            hs = full["hotspot"]
            if hs["active"] and hs["ssid"] and hs["ssid"] == self.settings.value.hotspot_ssid:
                info["wifi_qr"] = qr_manager.wifi_payload(hs["ssid"], self.settings.value.hotspot_password)
        if role == auth.ADMIN:
            info["controller_url"] = f"{base}/controller?key={sec.controller_token}"
            info.update({k: full[k] for k in ("interfaces", "adapter", "detection")})
            info["hotspot_password"] = self.settings.value.hotspot_password
        return info

    def publish_network(self) -> None:
        if socketio.server is None:  # beim Start meldet der Netzwerkmanager sich vor dem Webserver
            return
        for room, role in (("all", auth.VIEWER), ("writers", auth.CONTROLLER), ("admin", auth.ADMIN)):
            socketio.emit("network:changed", self.network_info(role), to=room)

    # ------------------------------------------------------------------ Diagnose

    def status(self, full: bool = False) -> dict:
        net = self.network.state
        data = {
            "server": "running",
            "version": self.version,
            "uptime_seconds": int(time.time() - self.started_at),
            "port": self.network.port,
            "network_mode": net.mode,
            "ip": net.primary.ip if net.primary else None,
            "clients": self.realtime.client_summary(),
            "last_sync": self.realtime.last_sync,
            "open_game_id": self.games.open_game_id(),
        }
        if full:
            data["database"] = self.db.check()
            data["database"]["revision"] = database.current_revision(self.db.engine)
            data["client_list"] = self.realtime.client_list()
            data["data_dir"] = str(self.paths.root)
            data["log_file"] = str(self.paths.log_file)
            data["settings_recovered"] = bool(self.settings.recovered_from)
        return data

    def settings_info(self) -> dict:
        s = self.settings.value
        return {**s.model_dump(), "autostart": si.autostart_enabled(), "autostart_available": si.frozen(),
                "active_port": self.network.port, "data_dir": str(self.paths.root),
                "database": str(self.paths.database), "backups_dir": str(self.paths.backups),
                "log_file": str(self.paths.log_file), "version": self.version}


# ---------------------------------------------------------------------- App-Fabrik

PAGES_NEXT = {"/", "/setup", "/dashboard", "/controller", "/history", "/stats", "/settings", "/welcome"}


def safe_next(target: str | None) -> str:
    """Nur eigene Seiten als Rücksprungziel (kein offener Redirect); ein Anker (#netzwerk) ist erlaubt."""
    target = target or "/"
    return target if target.split("#", 1)[0] in PAGES_NEXT else "/"


def create_app(ctx: Flip7Context) -> Flask:
    app = Flask(__name__, template_folder=str(resource_path("app", "templates")),
                static_folder=str(resource_path("app", "static")), static_url_path="/static")
    app.config.update(
        SECRET_KEY=ctx.secrets.value.secret_key,
        SESSION_COOKIE_NAME="flip7live_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=auth.SESSION_MAX_AGE_S),
        MAX_CONTENT_LENGTH=64 * 1024,
        JSON_SORT_KEYS=False,
        SEND_FILE_MAX_AGE_DEFAULT=0,  # no-cache + ETag: nach Änderungen/Updates nie veraltete Skripte (ES-Module ohne ?v=)
    )
    app.extensions["flip7"] = ctx

    from .api import api
    app.register_blueprint(api)
    # Handler VOR init_app importieren: dann merkt sich Flask-SocketIO sie und registriert sie bei jeder App-Instanz
    # (danach importiert, landeten sie nur beim ersten Server – weitere Instanzen nähmen Verbindungen ohne Prüfung an).
    from .realtime import events  # noqa: F401
    socketio.init_app(app)

    _register_guards(app, ctx)
    _register_pages(app, ctx)
    _register_errors(app)
    return app


def _client_key() -> str:
    return request.remote_addr or "?"


def _register_guards(app: Flask, ctx: Flip7Context) -> None:
    @app.before_request
    def guards():
        request.environ["flip7.start"] = time.perf_counter()
        if not auth.host_allowed(request.host, ctx.network.allowed_hosts()):
            # Fremder Hostname (DNS-Rebinding) – Netzwerk neu prüfen, falls sich nur die eigene IP geändert hat.
            if not auth.host_allowed(request.host, ctx.network.allowed_hosts() | set(_live_ips())):
                return _error(421, "host_not_allowed", "Unbekannte Adresse.")
        if not ctx.limiter.hit("api", _client_key()):
            return _error(429, "rate_limited", "Zu viele Anfragen. Bitte kurz warten.")
        if request.method in auth.STATE_CHANGING:
            if not ctx.limiter.hit("write", _client_key()):
                return _error(429, "rate_limited", "Zu viele Änderungen in kurzer Zeit. Bitte kurz warten.")
            if not auth.origin_allowed():
                return _error(403, "origin", "Anfrage von einer fremden Seite abgelehnt.")
        if request.method == "GET" and request.path in ("/controller", "/dashboard") and "key" in request.args:
            return _exchange_key(ctx)
        return None

    @app.after_request
    def headers(resp: Response):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"  # Zugangsschlüssel aus URLs nie weitergeben
        if request.path.startswith("/api/") or not request.path.startswith("/static/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        start = request.environ.get("flip7.start")
        if not request.path.startswith("/static/"):
            ms = (time.perf_counter() - start) * 1000 if start else 0
            access_log.info("%s %s %s %s %.0fms", _client_key(), request.method, redact(request.full_path.rstrip("?")),
                            resp.status_code, ms)
        return resp


def _live_ips() -> list[str]:
    try:
        return socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        return []


def _exchange_key(ctx: Flip7Context):
    """QR-Code-Link → Sitzung. Der Schlüssel verschwindet danach aus der Adresszeile (Weiterleitung)."""
    role_now = auth.current_role()
    target = request.path
    if role_now == auth.ADMIN:
        return redirect(target)
    if not ctx.limiter.hit("auth", _client_key()):
        return _page_error(429, "Zu viele Versuche. Bitte eine Minute warten.")
    key = request.args.get("key", "")
    sec = ctx.secrets.value
    if target == "/controller" and matches(key, sec.controller_token):
        auth.login(auth.CONTROLLER)
    elif target == "/dashboard" and matches(key, sec.viewer_token):
        if role_now != auth.CONTROLLER:  # Steuerung behält ihre Rechte, wenn sie den Zuschauer-Code scannt
            auth.login(auth.VIEWER)
    else:
        log.warning("Ungültiger Zugangsschlüssel von %s für %s", _client_key(), target)
        return _page_error(403, "Dieser QR-Code ist ungültig oder wurde am Laptop erneuert. Bitte den aktuellen "
                                "QR-Code auf dem Laptop scannen.")
    return redirect(target)


def _render(template: str, role: str, **kw):
    return render_template(template, role=role, csrf=auth.csrf_token(), version=__version__, app_title=APP_TITLE,
                           **kw)


def _register_pages(app: Flask, ctx: Flip7Context) -> None:
    def page(roles: tuple[str, ...]):
        role, reason = auth.session_state()
        if role is None:
            if auth.request_is_local():
                return None, _page_error(401, "Bitte Flip 7 Live über das Symbol im Infobereich (unten rechts) "
                                              "mit „Dashboard öffnen“ aufrufen.", local=True)
            msg = {"expired": "Die Sitzung ist abgelaufen.", "revoked": "Der Zugang wurde am Laptop erneuert.",
                   "admin_remote": "Administratorrechte gibt es nur direkt am Laptop."}.get(reason, "Kein Zugang.")
            return None, _page_error(401, msg + " Bitte den QR-Code auf dem Laptop scannen.")
        if role not in roles:
            if role == auth.VIEWER:
                return None, redirect(url_for("dashboard"))
            return None, _page_error(403, "Diese Seite ist nur am Laptop verfügbar.")
        return role, None

    @app.get("/")
    def index():
        role, err = page(auth.WRITERS)
        return err or _render("index.html", role, page="index")

    @app.get("/setup")
    def setup():
        role, err = page(auth.WRITERS)
        return err or _render("setup.html", role, page="setup",
                              default_target=ctx.settings.value.default_target_score,
                              default_players=ctx.settings.value.default_player_count)

    @app.get("/dashboard")
    def dashboard():
        role, err = page(auth.EVERYONE)
        return err or _render("dashboard.html", role, page="dashboard")

    @app.get("/controller")
    def controller():
        role, err = page(auth.WRITERS)
        return err or _render("controller.html", role, page="controller",
                              max_points=ctx.settings.value.max_round_points)

    @app.get("/history")
    def history():
        role, err = page(auth.EVERYONE)
        return err or _render("history.html", role, page="history")

    @app.get("/stats")
    def stats():
        role, err = page(auth.EVERYONE)
        return err or _render("stats.html", role, page="stats")

    @app.get("/welcome")
    def welcome():
        """Einrichtungsassistent (öffnet sich beim ersten Start automatisch)."""
        role, err = page((auth.ADMIN,))
        return err or _render("welcome.html", role, page="welcome")

    @app.get("/settings")
    def settings():
        role, err = page((auth.ADMIN,))
        return err or _render("settings.html", role, page="settings")

    @app.get("/admin/login")
    def admin_login():
        """Einmaliger Anmeldelink, den nur die App selbst im Browser des Laptops öffnet."""
        if not auth.request_is_local():
            return _page_error(403, "Administratorrechte gibt es nur direkt am Laptop.")
        if not ctx.limiter.hit("auth", _client_key()):
            return _page_error(429, "Zu viele Versuche. Bitte eine Minute warten.")
        if not ctx.admin_tokens.consume(request.args.get("once")):
            if auth.current_role() == auth.ADMIN:
                return redirect("/")
            return _page_error(403, "Dieser Anmeldelink ist abgelaufen. Bitte über das Symbol im Infobereich "
                                    "„Dashboard öffnen“ wählen.", local=True)
        auth.login(auth.ADMIN)
        return redirect(safe_next(request.args.get("next")))

    @app.post("/internal/open")
    def internal_open():
        """Zweiter Programmstart fragt hier nach einem frischen Anmeldelink (nur lokal, mit Instanz-Geheimnis)."""
        if not auth.request_is_local() or not matches(request.headers.get("X-Flip7-IPC"), ctx.ipc_secret):
            return _error(403, "forbidden", "Nicht erlaubt.")
        target = (request.get_json(silent=True) or {}).get("next")
        return jsonify({"ok": True, "url": ctx.admin_login_url(safe_next(target))})


def _error(status: int, code: str, message: str, details: dict | None = None):
    body = {"ok": False, "error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    resp = jsonify(body)
    resp.status_code = status
    return resp


def _page_error(status: int, message: str, local: bool = False):
    return render_template("error.html", status=status, message=message, local=local, app_title=APP_TITLE,
                           version=__version__, role=None, csrf=""), status


def _register_errors(app: Flask) -> None:
    def wants_json() -> bool:
        return request.path.startswith("/api/")

    @app.errorhandler(ServiceError)
    def service_error(exc: ServiceError):
        return _error(exc.status, exc.code, exc.message, exc.details or None)

    @app.errorhandler(DatabaseBusy)
    def busy(exc: DatabaseBusy):
        log.warning("Datenbank gesperrt: %s", exc)
        return _error(503, "database_busy", str(exc))

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        messages = {404: "Nicht gefunden.", 405: "Methode nicht erlaubt.", 403: "Kein Zugriff.",
                    413: "Anfrage zu groß.", 400: "Ungültige Anfrage."}
        msg = messages.get(exc.code or 500, "Fehler.")
        if wants_json():
            return _error(exc.code or 500, (exc.name or "error").lower().replace(" ", "_"), msg)
        return _page_error(exc.code or 500, msg)

    @app.errorhandler(Exception)
    def unexpected(exc: Exception):
        log.exception("Unerwarteter Fehler bei %s %s", request.method, redact(request.path))
        msg = "Ein unerwarteter Fehler ist aufgetreten. Details stehen in der Logdatei."
        if wants_json():
            return _error(500, "internal_error", msg)
        return _page_error(500, msg)


# ---------------------------------------------------------------------- Webserver


class PortUnavailable(RuntimeError):
    pass


def bind_socket(host: str, preferred: int, attempts: int = 11) -> socket.socket:
    """Exklusiv binden (unter Windows würde SO_REUSEADDR einen belegten Port „stehlen“). Bei belegtem Port
    die nächsten Ports versuchen."""
    last: OSError | None = None
    for port in range(preferred, preferred + attempts):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform == "win32":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            sock.listen(128)
            return sock
        except OSError as exc:
            sock.close()
            last = exc
            log.warning("Port %s ist belegt (%s) – versuche den nächsten", port, exc.strerror or exc)
    raise PortUnavailable(f"Kein freier Port zwischen {preferred} und {preferred + attempts - 1} ({last}).")


class ServerRunner:
    def __init__(self, app: Flask, host: str = "0.0.0.0", port: int = 5000):
        self.app = app
        self.host = host
        self.preferred_port = port
        self.port = 0
        self._server: ThreadedWSGIServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        sock = bind_socket(self.host, self.preferred_port)
        self.port = sock.getsockname()[1]
        try:
            self._server = ThreadedWSGIServer(self.host, self.port, self.app, fd=sock.fileno())
        finally:
            sock.close()  # Werkzeug arbeitet mit einem Duplikat des Sockets
        self._thread = threading.Thread(target=self._server.serve_forever, name="webserver", daemon=True)
        self._thread.start()
        log.info("Webserver läuft auf Port %s", self.port)
        return self.port

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Webserver beendet")

