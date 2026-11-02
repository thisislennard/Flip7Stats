from conftest import make_players, save_round, start_game


def finish(api, gid):
    assert api.post(f"/api/v1/games/{gid}/finish").status_code == 200


def test_delete_player_removes_scores_and_recalculates(admin):
    gid, (a, b, c) = start_game(admin)
    save_round(admin, gid, 1, {a: 10, b: 50, c: 5})
    save_round(admin, gid, 2, {a: None, b: 30, c: None})  # nur Ben hat gespielt → Runde fällt danach weg
    save_round(admin, gid, 3, {a: 7, b: 1, c: 2})
    finish(admin, gid)
    players = {p["display_name"]: p for p in admin.get("/api/v1/players").json["data"]}
    assert players["Ben"]["games_played"] == 1

    r = admin.delete(f"/api/v1/players/{b}")
    assert r.status_code == 200 and r.json["data"]["games"] == 1 and r.json["data"]["backup"]
    assert "Ben" not in {p["display_name"] for p in admin.get("/api/v1/players").json["data"]}
    state = admin.get(f"/api/v1/games/{gid}").json["data"]
    assert [x["name"] for x in state["players"]] == ["Anna", "Cem"]
    assert [rnd["round_number"] for rnd in state["rounds"]] == [1, 2]  # leere Runde entfernt, lückenlos
    assert state["leaderboard"][0]["name"] == "Anna" and state["leaderboard"][0]["total"] == 17
    assert any(b_["reason"] == "predelete" for b_ in admin.get("/api/v1/system/backups").json["data"])


def test_player_in_running_game_cannot_be_deleted(admin):
    gid, (a, b, c) = start_game(admin)
    r = admin.delete(f"/api/v1/players/{a}")
    assert r.status_code == 409 and r.json["error"]["code"] == "player_in_use"


def test_player_without_games_is_deleted_and_name_is_free_again(admin):
    (x,) = make_players(admin, "Xaver")
    assert admin.delete(f"/api/v1/players/{x}").status_code == 200
    assert admin.post("/api/v1/players", {"display_name": "Xaver"}).status_code == 201


def test_only_admin_may_delete(controller, viewer, admin):
    (x,) = make_players(admin, "Yvonne")
    assert controller.delete(f"/api/v1/players/{x}").status_code == 403
    assert viewer.delete(f"/api/v1/players/{x}").status_code == 403
    assert controller.post("/api/v1/system/reset", {"confirm": "LÖSCHEN"}).status_code == 403


def test_reset_requires_confirmation_and_wipes_everything(admin):
    gid, (a, b, c) = start_game(admin)
    save_round(admin, gid, 1, {a: 10, b: 20, c: 30})
    assert admin.post("/api/v1/system/reset", {"confirm": "ja"}).status_code == 422
    r = admin.post("/api/v1/system/reset", {"confirm": "löschen"})
    assert r.status_code == 200 and r.json["data"]["players"] == 3 and r.json["data"]["backup"]
    assert admin.get("/api/v1/players").json["data"] == []
    assert admin.get("/api/v1/history").json["data"] == []
    assert admin.get("/api/v1/games/current").json["data"] is None
    settings = admin.get("/api/v1/system/settings").json["data"]
    assert settings["default_target_score"] == 200  # Einstellungen bleiben
