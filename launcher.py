"""Startpunkt von Flip7Live.exe.

Ablauf: (Selbst-Installation) → Einzelinstanz prüfen → Datenverzeichnis → Konfiguration → Datenbank (Sicherung +
Migration) → offene Spiele → Netzwerk erkennen und Modus wählen → Webserver → Erreichbarkeit prüfen → Programmfenster.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import urllib.request
import webbrowser

if getattr(sys, "frozen", False):
    # Ohne die kompilierten SQLAlchemy-Beschleuniger: unsignierte DLLs blockiert Smart App Control (build.spec lässt
    # sie weg). Muss vor dem ersten SQLAlchemy-Import gesetzt sein.
    os.environ.setdefault("DISABLE_SQLALCHEMY_CEXT_RUNTIME", "1")

from app import __version__, database
from app import system_integration as si
from app.config import APP_TITLE, Paths, SecretsStore, SettingsStore, default_home, is_frozen
from app.extensions import Database
from app.logging_setup import setup_logging
from app.network.network_manager import NetworkManager
from app.server import Flip7Context, PortUnavailable, ServerRunner, create_app

log = logging.getLogger("flip7live")


def _open_running_instance(paths: Paths, next_path: str) -> bool:
    """Zweitstart: die laufende Instanz holt ihr Fenster nach vorn (ohne Fenster: Browser mit frischem Anmeldelink)."""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            info = json.loads(paths.instance_file.read_text(encoding="utf-8"))
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(int(info.get("pid", -1)))
            req = urllib.request.Request(
                f"http://127.0.0.1:{int(info['port'])}/internal/open",
                data=json.dumps({"next": next_path}).encode(), method="POST",
                headers={"Content-Type": "application/json", "X-Flip7-IPC": info["ipc_secret"]})
            with urllib.request.urlopen(req, timeout=5) as resp:
                answer = json.loads(resp.read())
            if not answer.get("window"):
                webbrowser.open(answer["url"])
            return True
        except (OSError, ValueError, KeyError):
            time.sleep(1)  # erste Instanz startet evtl. gerade noch
    return False


def _make_window(ctx: Flip7Context, paths: Paths, first_path: str, stop: threading.Event):
    """Programmfenster vorbereiten; None, wenn WebView2 fehlt (dann Browser)."""
    from app.config import resource_path
    from app.desktop import window as win
    loader = resource_path("app", "desktop", "webview2", "WebView2Loader.dll")
    if not loader.exists() or not win.webview2_available():
        log.warning("WebView2 nicht verfügbar – Oberfläche im Browser")
        return None

    def confirm_close() -> bool:
        busy = ctx.games.open_game_id() is not None or bool(ctx.realtime.client_summary().get("controller"))
        if not busy:
            return True
        import ctypes
        answer = ctypes.windll.user32.MessageBoxW(
            None, "Flip 7 Live beenden?\n\nDas laufende Spiel bleibt gespeichert, aber verbundene Smartphones "
                  "verlieren die Verbindung, bis Flip 7 Live wieder läuft.", APP_TITLE, 0x4 | 0x30 | 0x100)
        return answer == 6  # IDYES

    return win.AppWindow(
        title=APP_TITLE, icon_path=resource_path("app", "static", "assets", "flip7live.ico"), loader_path=loader,
        user_data=paths.root / "webview", url=ctx.admin_login_url(first_path),
        own_origin=f"http://127.0.0.1:{ctx.network.port}", on_close_request=confirm_close, on_closed=stop.set,
        dev_tools=not is_frozen())


def _write_instance_file(paths: Paths, ctx: Flip7Context) -> None:
    data = {"pid": os.getpid(), "port": ctx.network.port, "ipc_secret": ctx.ipc_secret, "version": __version__}
    tmp = paths.instance_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, paths.instance_file)


def _migrate(ctx: Flip7Context) -> None:
    made: dict = {}

    def before():
        made.update(ctx.backup.create("migration"))

    try:
        database.upgrade(ctx.db.engine, before=before)
    except database.MigrationError:
        raise
    except Exception as exc:
        log.exception("Datenbankmigration fehlgeschlagen")
        if made:
            ctx.backup.restore(made["name"])
            raise database.MigrationError(
                "Die Datenbank konnte nicht aktualisiert werden. Der vorherige Stand wurde aus der Sicherung "
                f"„{made['name']}“ wiederhergestellt. Details stehen in der Logdatei.") from exc
        raise database.MigrationError("Die Datenbank konnte nicht eingerichtet werden. Details stehen in der "
                                      "Logdatei.") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="Flip7Live", description=APP_TITLE)
    parser.add_argument("--no-window", "--no-browser", dest="no_window", action="store_true",
                        help="ohne Programmfenster (Server läuft im Hintergrund; Entwicklung/Tests)")
    parser.add_argument("--browser", action="store_true", help="Oberfläche im Standardbrowser statt im Programmfenster")
    parser.add_argument("--no-tray", action="store_true", help=argparse.SUPPRESS)  # früher: Infobereich-Symbol
    parser.add_argument("--uninstall", action="store_true", help="Flip 7 Live deinstallieren")
    parser.add_argument("--portable", action="store_true", help="ohne Installationsangebot starten")
    parser.add_argument("--console", action="store_true", help="Protokoll zusätzlich in der Konsole ausgeben")
    args = parser.parse_args(argv)

    paths = Paths(default_home())

    # 0. Selbst-Installation / Deinstallation (nur die EXE)
    if is_frozen() and sys.platform == "win32":
        from app.desktop import installer
        try:
            paths.ensure()
            setup_logging(paths.log_file, console=False)
        except OSError:
            pass
        if args.uninstall:
            return installer.uninstall()
        if not args.portable and installer.maybe_install():
            return 0

    # 1. Einzelinstanz
    instance = si.SingleInstance(data_root=paths.root)
    if not instance.acquire():
        if not _open_running_instance(paths, "/dashboard"):
            si.message_box("Flip 7 Live läuft bereits, reagiert aber nicht. Bitte das Fenster schließen oder den "
                           "Computer neu starten.")
        return 0

    try:
        # 2. Datenverzeichnis
        try:
            paths.ensure()
        except OSError as exc:
            si.message_box(f"Das Datenverzeichnis {paths.root} kann nicht angelegt werden ({exc}).")
            return 1
        setup_logging(paths.log_file, console=args.console or not is_frozen())
        log.info("Flip 7 Live %s startet (EXE: %s, Daten: %s)", __version__, is_frozen(), paths.root)

        # 3. Konfiguration (beschädigte Dateien werden gesichert und ersetzt)
        settings = SettingsStore(paths)
        secrets = SecretsStore(paths)
        if settings.recovered_from:
            log.warning("Einstellungen wurden zurückgesetzt; beschädigte Datei: %s", settings.recovered_from)

        db = Database(paths.database)
        network = NetworkManager(lambda: settings.value)
        stop = threading.Event()
        ctx = Flip7Context(paths, settings, secrets, db, network)

        # 4. Datenbank prüfen / migrieren
        try:
            _migrate(ctx)
        except database.MigrationError as exc:
            si.message_box(str(exc))
            return 1

        # 5. Vorhandene Spiele
        ctx.games.prune_idempotency()
        open_id = ctx.games.open_game_id()
        if open_id:
            log.info("Offenes Spiel #%s wird wiederhergestellt", open_id)

        # 6./7. Netzwerk erkennen, Modus wählen (startet ggf. den Hotspot)
        try:
            network.refresh(try_hotspot=True)
        except Exception:  # noqa: BLE001 – ohne Netzwerk weiterstarten (Einzelgerätebetrieb)
            log.exception("Netzwerkerkennung fehlgeschlagen")

        # 8. Webserver
        app = create_app(ctx)
        runner = ServerRunner(app, port=settings.value.port)
        try:
            port = runner.start()
        except PortUnavailable as exc:
            si.message_box(f"Flip 7 Live konnte keinen freien Netzwerkport finden.\n\n{exc}")
            return 1
        network.port = port
        if port != settings.value.port:
            log.warning("Port %s belegt – Flip 7 Live nutzt Port %s", settings.value.port, port)
        _write_instance_file(paths, ctx)

        # 9. Erreichbarkeit prüfen
        network.state.reachable = network.check_reachable()
        log.info("Erreichbarkeit unter %s: %s", network.base_url(), network.state.reachable)
        network.start_monitor()

        # 10./11. Programmfenster (erster Start: Einrichtungsassistent, sonst Dashboard bzw. Startseite)
        first_path = "/welcome" if not settings.value.setup_completed else ("/dashboard" if open_id else "/")
        window = None

        def shutdown():
            stop.set()
            if window:
                window.post_close()

        ctx.shutdown_callback = shutdown
        if not args.no_window and not args.browser and sys.platform == "win32":
            window = _make_window(ctx, paths, first_path, stop)
        if window:
            ctx.open_window_callback = window.post_open
            try:
                window.run()  # blockiert bis zum Schließen
            except Exception:  # noqa: BLE001
                log.exception("Programmfenster fehlgeschlagen")
                window.failed = window.failed or "Fehler im Programmfenster"
            if window.failed and not stop.is_set():
                ctx.open_window_callback = None
                si.message_box("Das Programmfenster konnte nicht geöffnet werden – Flip 7 Live öffnet sich im "
                               f"Browser.\n\n{window.failed}", error=False)
                window = None
                webbrowser.open(ctx.admin_login_url(first_path))
        elif not args.no_window:
            webbrowser.open(ctx.admin_login_url(first_path))
        if not window:
            try:
                while not stop.wait(1):
                    pass
            except KeyboardInterrupt:
                pass

        # Sauberes Beenden: Server stoppen, laufende Schreibvorgänge abwarten, Netzwerkressourcen freigeben.
        log.info("Flip 7 Live wird beendet")
        runner.stop()
        network.stop()
        db.dispose()
        try:
            paths.instance_file.unlink()
        except OSError:
            pass
        log.info("Beendet")
        return 0
    except Exception:  # noqa: BLE001
        logging.getLogger("flip7live").exception("Unerwarteter Fehler beim Start")
        si.message_box("Flip 7 Live konnte nicht gestartet werden. Details stehen in der Logdatei:\n"
                       f"{paths.log_file}")
        return 1
    finally:
        instance.release()


if __name__ == "__main__":
    sys.exit(main())
