"""Spieleabend: gemeinsame Punktzahl, mehrere Partien, Gewinner je Partie, Abend-Endstand nach Siegen."""

from conftest import make_players, save_round


def start_night(api, names=("Anna", "Ben", "Cem"), target=50):
    ids = make_players(api, *names)
    r = api.post("/api/v1/nights", {"name": "Freitag", "target_score": target, "player_ids": ids})
    assert r.status_code == 201, r.json
    return r.json["data"], ids


def test_night_starts_first_game_with_its_target(admin):
    state, ids = start_night(admin, target=75)
    assert state["game"]["status"] == "ACTIVE" and state["game"]["target_score"] == 75
    assert state["game"]["night_number"] == 1
    assert state["night"]["name"] == "Freitag" and state["night"]["target_score"] == 75


def test_game_ends_at_night_target_then_next_game(admin, viewer):
    state, (a, b, c) = start_night(admin)
    gid = state["game"]["id"]
    s = save_round(admin, gid, 1, {a: 60, b: 10, c: 0}).json["data"]["state"]
    assert s["game"]["status"] == "FINISHED" and s["winners"][0]["name"] == "Anna"
    assert s["night"]["standings"][0]["name"] == "Anna" and s["night"]["standings"][0]["wins"] == 1
    # Dashboard zeigt weiter die beendete Partie (Gewinner), bis die nächste startet
    assert viewer.get("/api/v1/games/current?display=1").json["data"]["game"]["id"] == gid
    nxt = admin.post(f"/api/v1/nights/{state['night']['id']}/next-game").json["data"]
    assert nxt["game"]["night_number"] == 2 and nxt["game"]["status"] == "ACTIVE"
    assert [p["player_id"] for p in nxt["players"]] == [a, b, c]


def test_next_game_blocked_while_game_running(admin):
    state, _ = start_night(admin)
    r = admin.post(f"/api/v1/nights/{state['night']['id']}/next-game")
    assert r.status_code == 409


def test_night_standings_count_wins_and_ties(admin):
    state, (a, b, c) = start_night(admin)
    nid = state["night"]["id"]
    save_round(admin, state["game"]["id"], 1, {a: 60, b: 0, c: 0})        # Anna
    g2 = admin.post(f"/api/v1/nights/{nid}/next-game").json["data"]["game"]["id"]
    save_round(admin, g2, 1, {a: 55, b: 55, c: 0})                         # Anna + Ben (Gleichstand)
    g3 = admin.post(f"/api/v1/nights/{nid}/next-game").json["data"]["game"]["id"]
    save_round(admin, g3, 1, {a: 1, b: 2, c: 3})
    admin.post(f"/api/v1/games/{g3}/cancel")                               # zählt nicht
    summary = admin.post(f"/api/v1/nights/{nid}/finish").json["data"]
    table = {r["name"]: r for r in summary["standings"]}
    assert summary["status"] == "FINISHED"
    assert (table["Anna"]["wins"], table["Ben"]["wins"], table["Ben"]["shared_wins"], table["Cem"]["wins"]) == (2, 1, 1, 0)
    assert table["Anna"]["rank"] == 1 and table["Anna"]["games_played"] == 2
    assert summary["leaders"] == ["Anna"] and summary["games_finished"] == 2
    overall = admin.get("/api/v1/statistics/overall").json["data"]
    anna = next(p for p in overall["players"] if p["name"] == "Anna")
    assert overall["nights_played"] == 1 and anna["night_wins"] == 1 and anna["nights_played"] == 1


def test_equal_wins_are_a_tie_regardless_of_points(admin):
    state, (a, b, c) = start_night(admin)
    nid = state["night"]["id"]
    save_round(admin, state["game"]["id"], 1, {a: 200, b: 0, c: 0})       # Anna, viele Punkte
    g2 = admin.post(f"/api/v1/nights/{nid}/next-game").json["data"]["game"]["id"]
    save_round(admin, g2, 1, {a: 0, b: 51, c: 0})                          # Ben, knapp
    summary = admin.post(f"/api/v1/nights/{nid}/finish").json["data"]
    table = {r["name"]: r for r in summary["standings"]}
    assert table["Anna"]["rank"] == table["Ben"]["rank"] == 1 and table["Cem"]["rank"] == 3
    assert sorted(summary["leaders"]) == ["Anna", "Ben"]
    overall = {p["name"]: p for p in admin.get("/api/v1/statistics/overall").json["data"]["players"]}
    assert overall["Anna"]["night_wins"] == overall["Ben"]["night_wins"] == 1


def test_finish_night_requires_running_game_to_end(admin):
    state, (a, b, c) = start_night(admin)
    save_round(admin, state["game"]["id"], 1, {a: 1, b: 2, c: 3})
    r = admin.post(f"/api/v1/nights/{state['night']['id']}/finish")
    assert r.status_code == 409 and r.json["error"]["code"] == "game_running"


def test_empty_started_game_is_dropped_when_night_ends(admin):
    state, (a, b, c) = start_night(admin)
    nid = state["night"]["id"]
    save_round(admin, state["game"]["id"], 1, {a: 60, b: 0, c: 0})
    admin.post(f"/api/v1/nights/{nid}/next-game")
    summary = admin.post(f"/api/v1/nights/{nid}/finish").json["data"]
    assert [g["number"] for g in summary["games"]] == [1]
    assert admin.get("/api/v1/games/current").json["data"] is None


def test_only_one_active_night(admin):
    start_night(admin)
    ids = [p["id"] for p in admin.get("/api/v1/players").json["data"]]
    assert admin.post("/api/v1/nights", {"player_ids": ids[:2]}).status_code == 409


def test_target_change_applies_to_next_game(admin):
    state, (a, b, c) = start_night(admin, target=50)
    nid = state["night"]["id"]
    save_round(admin, state["game"]["id"], 1, {a: 60, b: 0, c: 0})
    admin.patch(f"/api/v1/nights/{nid}", {"target_score": 120})
    nxt = admin.post(f"/api/v1/nights/{nid}/next-game").json["data"]
    assert nxt["game"]["target_score"] == 120


def test_viewer_cannot_control_night(admin, viewer):
    state, _ = start_night(admin)
    assert viewer.post(f"/api/v1/nights/{state['night']['id']}/finish").status_code == 403


def test_delete_finished_night_admin_only(admin, controller):
    state, (a, b, c) = start_night(admin)
    nid = state["night"]["id"]
    save_round(admin, state["game"]["id"], 1, {a: 60, b: 0, c: 0})
    assert admin.delete(f"/api/v1/nights/{nid}").status_code == 409
    admin.post(f"/api/v1/nights/{nid}/finish")
    assert controller.delete(f"/api/v1/nights/{nid}").status_code == 403
    assert admin.delete(f"/api/v1/nights/{nid}").status_code == 200
    assert admin.get(f"/api/v1/games/{state['game']['id']}").status_code == 404
