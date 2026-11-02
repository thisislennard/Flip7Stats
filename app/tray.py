"""Symbol im Windows-Infobereich: Dashboard öffnen, Verbindung anzeigen, Netzwerk erneut prüfen, Beenden."""

from __future__ import annotations

import logging
import threading
import webbrowser
from typing import Callable

log = logging.getLogger(__name__)


def make_icon_image(size: int = 64):
    """App-Icon (gelbe Karte mit „7“) – dieselbe Datei wie für EXE, Browser und Handy."""
    from PIL import Image

    from .config import resource_path
    return Image.open(resource_path("app", "static", "assets", f"icon-{size}.png"))


class Tray:
    def __init__(self, ctx, on_quit: Callable[[], None]):
        import pystray
        self.ctx = ctx
        self._on_quit = on_quit
        menu = pystray.Menu(
            pystray.MenuItem("Dashboard öffnen", self._open_dashboard, default=True),
            pystray.MenuItem("Verbindung anzeigen", self._open_connection),
            pystray.MenuItem("Netzwerk erneut prüfen", self._refresh),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", self._quit),
        )
        self.icon = pystray.Icon("Flip7Live", make_icon_image(), "Flip 7 Live", menu)

    def _open(self, path: str) -> None:
        webbrowser.open(self.ctx.admin_login_url(path))

    def _open_dashboard(self, *_):
        self._open("/dashboard")

    def _open_connection(self, *_):
        self._open("/settings#netzwerk")

    def _refresh(self, *_):
        def work():
            state = self.ctx.network.refresh(try_hotspot=True)
            ip = state.primary.ip if state.primary else None
            text = (f"Verbindung: {self.ctx.network.to_dict()['mode_label']} – {ip}:{self.ctx.network.port}" if ip
                    else "Kein Netzwerk gefunden – siehe „Verbindung anzeigen“.")
            self.notify(text)
        threading.Thread(target=work, name="tray-refresh", daemon=True).start()

    def notify(self, text: str) -> None:
        try:
            self.icon.notify(text, "Flip 7 Live")
        except Exception:  # noqa: BLE001 – Benachrichtigungen sind optional
            log.debug("Benachrichtigung nicht möglich", exc_info=True)

    def _quit(self, *_):
        self._on_quit()

    def run(self) -> None:
        """Blockiert bis stop()."""
        self.icon.run()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:  # noqa: BLE001
            pass
