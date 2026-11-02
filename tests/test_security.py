import time

from conftest import PHONE_IP, idem, make_players, start_game

from app.security.authentication import host_allowed


def test_no_session_no_access(app):
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = PHONE_IP
    assert c.get("/api/v1/games").status_code == 401
    assert c.post("/api/v1/players", json={"display_name": "X"}).status_code == 401
    assert c.get("/controller").status_code == 401


def test_invalid_token_is_rejected(app):
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = PHONE_IP
    r = c.get("/controller?key=falsch")
    assert r.status_code == 403
    assert c.get("/api/v1/games").status_code == 401


def test_viewer_cannot_write(viewer, admin):
    gid, (a, b, c) = start_game(admin)
    assert viewer.get(f"/api/v1/games/{gid}").status_code == 200
    r = viewer.post(f"/api/v1/games/{gid}/rounds", {
        "idempotency_key": idem(), "round_number": 1,
        "scores": [{"player_id": a, "points": 1}, {"player_id": b, "points": 1}, {"player_id": c, "points": 1}]})
    assert r.status_code == 403
    assert viewer.post("/api/v1/players", {"display_name": "Hacker"}).status_code == 403
    assert viewer.get("/controller").status_code == 302  # Zuschauer werden zum Dashboard geleitet


def test_controller_can_write_but_not_admin_functions(controller):
    assert make_players(controller, "Anna")
    assert controller.get("/api/v1/system/settings").status_code == 403
    assert controller.post("/api/v1/system/backups").status_code == 403
    assert controller.get("/settings").status_code == 403


def test_write_without_csrf_token_is_rejected(controller):
    r = controller.post("/api/v1/players", {"display_name": "Anna"}, csrf=False)
    assert r.status_code == 403 and r.json["error"]["code"] == "csrf"
    r = controller.post("/api/v1/players", {"display_name": "Anna"}, headers={"X-CSRF-Token": "falsch"})
    assert r.status_code == 403


def test_cross_site_requests_are_rejected(controller):
    r = controller.post("/api/v1/players", {"display_name": "Anna"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json["error"]["code"] == "origin"
    r = controller.post("/api/v1/players", {"display_name": "Anna"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_form_posts_are_rejected(controller):
    """Ohne JSON keine Schreibvorgänge (verhindert einfache Formular-Angriffe)."""
    from conftest import _csrf
    r = controller.c.post("/api/v1/players", data={"display_name": "Anna"},
                          headers={"X-CSRF-Token": _csrf(controller.c)})
    assert r.status_code == 422 and r.json["error"]["code"] == "unsupported_media_type"


def test_unknown_host_header_is_rejected(app):
    """DNS-Rebinding: fremder Hostname zeigt auf 127.0.0.1 – trotzdem kein Zugriff."""
    c = app.test_client()
    r = c.get("/api/v1/system/ping", headers={"Host": "evil.example:5000"})
    assert r.status_code == 421


def test_host_allowed_parsing():
    assert host_allowed("localhost:5000", [])
    assert host_allowed("127.0.0.1:5000", [])
    assert host_allowed("[::1]:5000", [])
    assert host_allowed("192.168.178.50:5000", ["192.168.178.50"])
    assert not host_allowed("192.168.178.51:5000", ["192.168.178.50"])
    assert not host_allowed("evil.example", [])
    assert not host_allowed(None, [])


def test_admin_requires_one_time_link_and_loopback(app, ctx):
    lan = app.test_client()
    lan.environ_base["REMOTE_ADDR"] = PHONE_IP
    token = ctx.admin_tokens.issue()
    assert lan.get(f"/admin/login?once={token}").status_code == 403
    local = app.test_client()
    assert local.get(f"/admin/login?once={token}").status_code == 302
    assert local.get(f"/admin/login?once={token}").status_code in (302,)  # bereits Admin → weiter
    other = app.test_client()
    assert other.get(f"/admin/login?once={token}").status_code == 403  # Einmal-Link verbraucht


def test_admin_cookie_from_wlan_gets_no_admin_rights(admin, app):
    """Ein kopiertes Admin-Cookie nützt aus dem WLAN nichts – Rolle hängt an der Socket-Adresse."""
    cookie = admin.c.get_cookie("flip7live_session")
    thief = app.test_client()
    thief.environ_base["REMOTE_ADDR"] = PHONE_IP
    thief.set_cookie("flip7live_session", cookie.value)
    r = thief.get("/api/v1/system/settings")
    assert r.status_code == 401 and r.json["error"]["code"] == "admin_remote"


def test_spoofed_forwarded_header_does_not_grant_admin(app, ctx):
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = PHONE_IP
    r = c.get(f"/admin/login?once={ctx.admin_tokens.issue()}", headers={"X-Forwarded-For": "127.0.0.1"})
    assert r.status_code == 403


def test_expired_session(controller, monkeypatch):
    with controller.c.session_transaction() as s:
        s["iat"] = int(time.time()) - 25 * 3600  # Sitzung älter als 24 h
    r = controller.get("/api/v1/games")
    assert r.status_code == 401 and r.json["error"]["code"] == "expired"


def test_session_cookie_expires_by_signature(controller, monkeypatch):
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 25 * 3600)
    assert controller.get("/api/v1/games").status_code == 401


def test_rotated_token_revokes_controller_sessions(controller, admin, app, ctx):
    old = ctx.secrets.value.controller_token
    r = admin.post("/api/v1/system/tokens/rotate", {"role": "controller"})
    assert r.status_code == 200
    assert controller.get("/api/v1/games").json["error"]["code"] == "revoked"
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = PHONE_IP
    assert c.get(f"/controller?key={old}").status_code == 403


def test_manipulated_requests(controller, admin):
    gid, (a, b, c) = start_game(admin)
    # unbekannte Felder, falsche Typen, SQL-artige Eingaben
    assert controller.post(f"/api/v1/games/{gid}/rounds", {"idempotency_key": idem(), "round_number": 1,
                                                           "scores": [], "admin": True}).status_code == 422
    assert controller.post(f"/api/v1/games/{gid}/rounds", {"idempotency_key": "x'; DROP TABLE games;--",
                                                           "round_number": 1, "scores": [
                                                               {"player_id": a, "points": 1}]}).status_code == 422
    r = controller.post("/api/v1/players", {"display_name": "Robert'); DROP TABLE players;--"})
    assert r.status_code == 201  # wird als normaler Name gespeichert (parametrisierte Abfragen)
    assert len(admin.get("/api/v1/players").json["data"]) == 4
    assert controller.c.post("/api/v1/players", data="kein json", content_type="text/plain",
                             headers={"X-CSRF-Token": "x"}).status_code == 403


def test_tokens_are_not_written_to_logs(app, ctx, caplog):
    import logging
    caplog.set_level(logging.INFO)
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = PHONE_IP
    token = ctx.secrets.value.controller_token
    c.get(f"/controller?key={token}")
    c.get("/controller?key=falsch-geraten")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert token not in text
    assert "falsch-geraten" not in text
    assert "key=***" in text


def test_rate_limit_on_key_guessing(app):
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = "192.168.178.99"
    codes = [c.get(f"/controller?key=versuch{i}").status_code for i in range(25)]
    assert codes[0] == 403 and codes[-1] == 429


def test_security_headers(admin):
    r = admin.get("/api/v1/system/status")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
