"""Windows-Integration: Einzelinstanz, Port belegt, Zweitstart-IPC, Konfiguration, sauberes Beenden."""

import json
import socket
import sys

import pytest

from app import system_integration as si
from app.config import Paths, SecretsStore, SettingsStore
from app.server import bind_socket


@pytest.mark.skipif(sys.platform != "win32", reason="benannter Mutex nur unter Windows")
def test_single_instance_mutex():
    first = si.SingleInstance(name="Local\\Flip7Live-Test-Mutex")
    second = si.SingleInstance(name="Local\\Flip7Live-Test-Mutex")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_busy_port_falls_back_to_next_free_port():
    blocker = bind_socket("127.0.0.1", 0)
    busy = blocker.getsockname()[1]
    try:
        sock = bind_socket("127.0.0.1", busy, attempts=5)
        assert sock.getsockname()[1] != busy
        sock.close()
    finally:
        blocker.close()


def test_corrupt_settings_file_is_recovered(tmp_path):
    paths = Paths(tmp_path).ensure()
    paths.settings_file.write_text("{ kaputt", encoding="utf-8")
    store = SettingsStore(paths)
    assert store.value.default_target_score == 200
    assert store.recovered_from and store.recovered_from.exists()
    assert json.loads(paths.settings_file.read_text(encoding="utf-8"))["port"] == 5000


def test_invalid_values_in_settings_are_recovered(tmp_path):
    paths = Paths(tmp_path).ensure()
    paths.settings_file.write_text(json.dumps({"port": 12, "default_target_score": -5}), encoding="utf-8")
    assert SettingsStore(paths).value.port == 5000


def test_settings_persist_and_hotspot_password_is_generated(tmp_path):
    paths = Paths(tmp_path).ensure()
    s = SettingsStore(paths)
    pw = s.value.hotspot_password
    assert len(pw) >= 8
    s.update(default_target_score=300)
    again = SettingsStore(paths)
    assert again.value.default_target_score == 300 and again.value.hotspot_password == pw


def test_token_rotation_changes_token_and_generation(tmp_path):
    paths = Paths(tmp_path).ensure()
    sec = SecretsStore(paths)
    old = sec.value.controller_token
    sec.rotate("controller")
    assert sec.value.controller_token != old and sec.value.controller_generation == 2
    assert len(old) >= 32


def test_internal_open_requires_secret_and_loopback(app, ctx):
    local = app.test_client()
    assert local.post("/internal/open", json={}).status_code == 403
    r = local.post("/internal/open", json={"next": "/dashboard"}, headers={"X-Flip7-IPC": ctx.ipc_secret})
    assert r.status_code == 200 and "/admin/login?once=" in r.json["url"] and "next=/dashboard" in r.json["url"]
    lan = app.test_client()
    lan.environ_base["REMOTE_ADDR"] = "192.168.178.77"
    assert lan.post("/internal/open", json={}, headers={"X-Flip7-IPC": ctx.ipc_secret}).status_code == 403


def test_open_redirect_is_prevented(app, ctx):
    c = app.test_client()
    r = c.get(f"/admin/login?once={ctx.admin_tokens.issue()}&next=https://evil.example")
    assert r.headers["Location"].endswith("/")
    r2 = app.test_client().get(f"/admin/login?once={ctx.admin_tokens.issue()}&next=/settings%23netzwerk")
    assert r2.headers["Location"].endswith("/settings#netzwerk")


def test_shutdown_endpoint_calls_callback(admin, ctx):
    import threading
    done = threading.Event()
    ctx.shutdown_callback = done.set
    assert admin.post("/api/v1/system/shutdown").status_code == 200
    assert done.wait(3)


def test_pages_render_for_roles(admin, controller, viewer, app):
    for path in ("/", "/setup", "/dashboard", "/controller", "/history", "/stats", "/settings"):
        assert admin.get(path).status_code == 200, path
    assert controller.get("/settings").status_code == 403
    assert controller.get("/controller").status_code == 200
    assert viewer.get("/").status_code == 302
    assert viewer.get("/stats").status_code == 200
    local_no_session = app.test_client().get("/")
    assert local_no_session.status_code == 401 and "Infobereich" in local_no_session.data.decode()


def test_setup_wizard_admin_only_and_completion_flag(admin, controller, ctx):
    assert ctx.settings.value.setup_completed is False
    assert admin.get("/welcome").status_code == 200
    assert controller.get("/welcome").status_code == 403
    assert admin.patch("/api/v1/system/settings", {"setup_completed": True}).json["data"]["setup_completed"] is True
    assert ctx.settings.value.setup_completed is True


def test_app_icon_everywhere(admin):
    html = admin.get("/").data.decode()
    for ref in ("assets/favicon.svg", "assets/icon-32.png", "apple-touch-icon.png", "manifest.webmanifest", "brand__icon"):
        assert ref in html, ref
    for f in ("favicon.svg", "icon-32.png", "icon-64.png", "icon-192.png", "icon-512.png", "apple-touch-icon.png",
              "flip7live.ico", "manifest.webmanifest"):
        assert admin.get(f"/static/assets/{f}").status_code == 200, f


def test_no_external_urls_in_frontend():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for f in list((root / "templates").glob("*.html")) + list((root / "static" / "js").rglob("*.js")) + \
            list((root / "static" / "css").rglob("*.css")):
        if "vendor" in f.parts:
            continue
        text = f.read_text(encoding="utf-8")
        for token in ("https://", "http://cdn", "fonts.googleapis"):
            if token in text:
                offenders.append(f"{f.name}: {token}")
    assert offenders == []


def test_socket_reuse_is_exclusive_on_windows():
    if sys.platform != "win32":
        pytest.skip("nur Windows")
    a = bind_socket("127.0.0.1", 0)
    port = a.getsockname()[1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    with pytest.raises(OSError):
        s.bind(("127.0.0.1", port))
    s.close()
    a.close()
