"""Live-Synchronisierung: authentifizierte Socket.IO-Verbindungen, Ereignisse an alle Geräte, keine Schreibwege."""

from conftest import PHONE_IP, save_round, start_game

from app.extensions import socketio


def sio(app, api_client):
    return socketio.test_client(app, flask_test_client=api_client.c)


def names(received):
    return [m["name"] for m in received]


def test_unauthenticated_socket_is_refused(app):
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = PHONE_IP
    client = socketio.test_client(app, flask_test_client=c)
    assert not client.is_connected()


def test_connect_sends_status_and_current_state(app, admin, viewer):
    gid, _ = start_game(admin)
    client = sio(app, viewer)
    assert client.is_connected()
    received = client.get_received()
    assert "system:status" in names(received)
    state = next(m for m in received if m["name"] == "game:updated")["args"][0]
    assert state["game_id"] == gid and state["state"]["game"]["status"] == "ACTIVE"


def test_round_reaches_all_viewers_without_reload(app, admin, viewer, controller):
    gid, (a, b, c) = start_game(admin)
    viewers = [sio(app, viewer), sio(app, viewer), sio(app, admin)]
    for v in viewers:
        v.get_received()
    assert save_round(controller, gid, 1, {a: 7, b: 3, c: 0}).status_code == 201
    for v in viewers:
        got = v.get_received()
        assert {"round:created", "scores:updated", "leaderboard:updated", "game:updated"} <= set(names(got))
        last = [m for m in got if m["name"] == "game:updated"][-1]["args"][0]["state"]
        assert last["leaderboard"][0]["total"] == 7


def test_finish_event_is_broadcast(app, admin, viewer):
    gid, (a, b, c) = start_game(admin, target=10)
    v = sio(app, viewer)
    v.get_received()
    save_round(admin, gid, 1, {a: 12, b: 0, c: 0})
    got = v.get_received()
    fin = next(m for m in got if m["name"] == "game:finished")["args"][0]
    assert [w["name"] for w in fin["winners"]] == ["Anna"]


def test_write_events_over_socket_are_rejected(app, admin, controller):
    gid, (a, b, c) = start_game(admin)
    client = sio(app, controller)
    client.get_received()
    client.emit("round:create", {"game_id": gid, "scores": []})
    got = client.get_received()
    err = next(m for m in got if m["name"] == "error")["args"][0]
    assert err["code"] == "write_not_allowed"
    assert admin.get(f"/api/v1/games/{gid}").json["data"]["game"]["current_round"] == 1


def test_resync_after_reconnect(app, admin, viewer):
    gid, (a, b, c) = start_game(admin)
    client = sio(app, viewer)
    client.disconnect()
    save_round(admin, gid, 1, {a: 5, b: 5, c: 5})  # verpasstes Update
    client.connect()
    client.get_received()
    client.emit("state:request")
    state = [m for m in client.get_received() if m["name"] == "game:updated"][-1]["args"][0]["state"]
    assert state["game"]["current_round"] == 2


def test_client_counts_reach_admin(app, admin, controller, ctx):
    a = sio(app, admin)
    a.get_received()
    sio(app, controller)
    got = a.get_received()
    msg = next(m for m in got if m["name"] == "client:connected")["args"][0]
    assert msg["clients"]["controller"] == 1
    assert ctx.realtime.client_summary()["total"] == 2


def test_revoked_controller_cannot_reconnect(app, admin, controller):
    admin.post("/api/v1/system/tokens/rotate", {"role": "controller"})
    assert not sio(app, controller).is_connected()
