"""Testumgebung: jede Testfunktion bekommt ein eigenes Datenverzeichnis und eine eigene Datenbank.

Das Netzwerk wird über austauschbare Erkennungsfunktionen simuliert – in Tests läuft nie PowerShell und es wird
nie ein echter Hotspot gestartet.
"""

from __future__ import annotations

import itertools
import uuid

import pytest

from app import database
from app.config import Paths, SecretsStore, SettingsStore
from app.extensions import Database
from app.network.address_resolver import Interface
from app.network.hotspot_manager import HotspotManager
from app.network.network_manager import NetworkManager
from app.network.wifi_detector import WifiInfo
from app.server import Flip7Context, create_app

LAN_IP = "192.168.178.50"
PHONE_IP = "192.168.178.77"


class FakeNet:
    """Steuerbare Netzwerkumgebung für Tests."""

    def __init__(self):
        self.interfaces = [Interface(17, "WLAN", "Intel Wi-Fi", "wifi", LAN_IP, 24, "Heimnetz", "Private")]
        self.wifi = [WifiInfo("WLAN", True, "Heimnetz")]
        self.hotspot = {"ok": True, "capability": "Enabled", "state": "Off", "ssid": "Flip7-Live", "source": "internet"}
        self.hotspot_calls: list[str] = []
        self.start_result = {"result": "Success", "state": "On"}

    def scan(self):
        return list(self.interfaces), "test"

    def run_hotspot(self, _script, env=None, timeout=0):
        action = (env or {}).get("FLIP7_ACTION")
        self.hotspot_calls.append(action)
        data = dict(self.hotspot)
        if action == "start" and data["capability"] == "Enabled" and data["state"] != "On":
            data.update(self.start_result)  # wie das echte Skript: nur starten, wenn noch nicht an
            if data.get("state") == "On":
                self.hotspot["state"] = "On"
                self.interfaces.append(Interface(40, "LAN-Verbindung* 10", "Microsoft Wi-Fi Direct Virtual Adapter #2",
                                                 "hotspot", "192.168.137.1", 24))
        return data


@pytest.fixture
def fake_net():
    return FakeNet()


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("FLIP7LIVE_HOME", str(tmp_path))
    return Paths(tmp_path).ensure()


@pytest.fixture
def ctx(paths, fake_net):
    settings = SettingsStore(paths)
    secrets = SecretsStore(paths)
    db = Database(paths.database)
    database.upgrade(db.engine)
    net = NetworkManager(lambda: settings.value, hotspot=HotspotManager(runner=fake_net.run_hotspot),
                         scanner=fake_net.scan, wifi=lambda: list(fake_net.wifi), fingerprint=lambda: ())
    c = Flip7Context(paths, settings, secrets, db, net)
    net.port = 5000
    net.check_reachable = lambda state=None: True  # kein echter Server im Unit-Test
    net.refresh(try_hotspot=False)
    yield c
    db.dispose()


@pytest.fixture
def app(ctx):
    a = create_app(ctx)
    a.config["TESTING"] = True
    return a


def _csrf(client) -> str:
    with client.session_transaction() as s:
        return s.get("csrf", "")


class ApiClient:
    """Test-Client mit CSRF-Header und JSON-Hilfen."""

    def __init__(self, client):
        self.c = client

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def _send(self, method, url, json=None, csrf=True, **kw):
        headers = kw.pop("headers", {})
        if csrf:
            headers.setdefault("X-CSRF-Token", _csrf(self.c))
        return self.c.open(url, method=method, json=json, headers=headers, **kw)

    def post(self, url, json=None, **kw):
        return self._send("POST", url, json if json is not None else {}, **kw)

    def patch(self, url, json=None, **kw):
        return self._send("PATCH", url, json, **kw)

    def delete(self, url, **kw):
        return self._send("DELETE", url, **kw)


@pytest.fixture
def admin(app, ctx):
    client = app.test_client()
    r = client.get(f"/admin/login?once={ctx.admin_tokens.issue()}")
    assert r.status_code == 302
    return ApiClient(client)


@pytest.fixture
def controller(app, ctx):
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = PHONE_IP
    r = client.get(f"/controller?key={ctx.secrets.value.controller_token}")
    assert r.status_code == 302 and "key=" not in r.headers["Location"]
    return ApiClient(client)


@pytest.fixture
def viewer(app, ctx):
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "192.168.178.88"
    r = client.get(f"/dashboard?key={ctx.secrets.value.viewer_token}")
    assert r.status_code == 302
    return ApiClient(client)


_ids = itertools.count(1)


def idem() -> str:
    return uuid.uuid4().hex


def make_players(api, *names) -> list[int]:
    ids = []
    for n in names:
        r = api.post("/api/v1/players", {"display_name": n})
        assert r.status_code == 201, r.json
        ids.append(r.json["data"]["id"])
    return ids


def start_game(api, names=("Anna", "Ben", "Cem"), target=200) -> tuple[int, list[int]]:
    ids = make_players(api, *names)
    r = api.post("/api/v1/games", {"name": "Testabend", "target_score": target, "player_ids": ids})
    assert r.status_code == 201, r.json
    gid = r.json["data"]["game"]["id"]
    r = api.post(f"/api/v1/games/{gid}/start")
    assert r.status_code == 200, r.json
    return gid, ids


def save_round(api, gid, number, points: dict[int, int | None], key=None):
    return api.post(f"/api/v1/games/{gid}/rounds", {
        "idempotency_key": key or idem(), "round_number": number,
        "scores": [{"player_id": pid, "points": pts} for pid, pts in points.items()]})
