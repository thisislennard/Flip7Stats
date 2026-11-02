"""Echter Server, echte WebSocket-Verbindung (kein Polling-Fallback): prüft den Threading-Modus mit simple-websocket."""

import http.cookiejar
import json
import re
import threading
import urllib.request

import pytest
import socketio as sio_client

from app.server import ServerRunner, create_app


@pytest.fixture
def live(ctx):
    app = create_app(ctx)
    runner = ServerRunner(app, host="127.0.0.1", port=0)
    port = runner.start()
    ctx.network.port = port
    yield ctx, port
    runner.stop()


def admin_session(ctx, port):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    html = opener.open(ctx.admin_login_url("/")).read().decode()
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
    cookie = "; ".join(f"{c.name}={c.value}" for c in jar)
    return opener, csrf, cookie


def api(opener, port, csrf, method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(body or {}).encode() if method != "GET" else None,
                                 headers={"Content-Type": "application/json", "X-CSRF-Token": csrf})
    return json.loads(opener.open(req).read())


def test_real_websocket_transport_and_live_update(live):
    ctx, port = live
    opener, csrf, cookie = admin_session(ctx, port)
    ids = [api(opener, port, csrf, "POST", "/api/v1/players", {"display_name": n})["data"]["id"] for n in ("A", "B")]
    gid = api(opener, port, csrf, "POST", "/api/v1/games", {"player_ids": ids})["data"]["game"]["id"]
    api(opener, port, csrf, "POST", f"/api/v1/games/{gid}/start")

    got = threading.Event()
    states = []
    client = sio_client.Client(reconnection=False)

    @client.on("game:updated")
    def on_state(payload):
        states.append(payload)
        if payload.get("state") and payload["state"]["game"]["current_round"] == 2:
            got.set()

    client.connect(f"http://127.0.0.1:{port}", transports=["websocket"], headers={"Cookie": cookie}, wait_timeout=5)
    try:
        assert client.transport() == "websocket"
        api(opener, port, csrf, "POST", f"/api/v1/games/{gid}/rounds", {
            "idempotency_key": "abcdefgh12345678", "round_number": 1,
            "scores": [{"player_id": ids[0], "points": 9}, {"player_id": ids[1], "points": 4}]})
        assert got.wait(5), states
    finally:
        client.disconnect()


def test_websocket_without_session_is_refused(live):
    _, port = live
    client = sio_client.Client(reconnection=False)
    with pytest.raises(sio_client.exceptions.ConnectionError):
        client.connect(f"http://127.0.0.1:{port}", transports=["websocket"], wait_timeout=5)


def test_websocket_from_foreign_origin_is_refused(live):
    ctx, port = live
    _, _, cookie = admin_session(ctx, port)
    client = sio_client.Client(reconnection=False)
    with pytest.raises(sio_client.exceptions.ConnectionError):
        client.connect(f"http://127.0.0.1:{port}", transports=["websocket"], wait_timeout=5,
                       headers={"Cookie": cookie, "Origin": "http://evil.example"})
