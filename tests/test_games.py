from conftest import make_players, save_round, start_game


def test_create_game_with_defaults(admin, ctx):
    ids = make_players(admin, "Anna", "Ben")
    r = admin.post("/api/v1/games", {"player_ids": ids})
    assert r.status_code == 201
    game = r.json["data"]["game"]
    assert game["status"] == "CREATED"
    assert game["target_score"] == 200
    assert game["current_round"] == 1
    assert game["name"] is None
    assert [p["name"] for p in r.json["data"]["players"]] == ["Anna", "Ben"]


def test_target_score_can_be_changed_before_start_only(admin):
    ids = make_players(admin, "Anna", "Ben")
    gid = admin.post("/api/v1/games", {"player_ids": ids}).json["data"]["game"]["id"]
    r = admin.patch(f"/api/v1/games/{gid}", {"target_score": 300})
    assert r.json["data"]["game"]["target_score"] == 300
    admin.post(f"/api/v1/games/{gid}/start")
    r = admin.patch(f"/api/v1/games/{gid}", {"target_score": 100})
    assert r.status_code == 409


def test_players_add_remove_reorder_before_start(admin):
    a, b, c = make_players(admin, "Anna", "Ben", "Cem")
    gid = admin.post("/api/v1/games", {"player_ids": [a, b]}).json["data"]["game"]["id"]
    r = admin.patch(f"/api/v1/games/{gid}", {"player_ids": [c, a]})
    assert [p["name"] for p in r.json["data"]["players"]] == ["Cem", "Anna"]
    r = admin.patch(f"/api/v1/games/{gid}", {"player_ids": [a, b, c]})
    assert [p["name"] for p in r.json["data"]["players"]] == ["Anna", "Ben", "Cem"]


def test_start_requires_two_players(admin):
    (a,) = make_players(admin, "Solo")
    gid = admin.post("/api/v1/games", {"player_ids": [a]}).json["data"]["game"]["id"]
    r = admin.post(f"/api/v1/games/{gid}/start")
    assert r.status_code == 422


def test_only_one_open_game(admin):
    start_game(admin)
    ids = [p["id"] for p in admin.get("/api/v1/players").json["data"]]
    r = admin.post("/api/v1/games", {"player_ids": ids[:2]})
    assert r.status_code == 409
    assert r.json["error"]["code"] == "open_game_exists"


def test_player_names_are_unique_and_reusable(admin):
    (a,) = make_players(admin, "Anna")
    r = admin.post("/api/v1/players", {"display_name": "  anna "})
    assert r.status_code == 409
    assert r.json["error"]["details"]["player"]["id"] == a
    # Spieler aus früheren Spielen übernehmen: gleiche ID in zwei Spielen
    gid, ids = start_game(admin, names=("Ben", "Cem"))
    admin.post(f"/api/v1/games/{gid}/finish")
    r = admin.post("/api/v1/games", {"player_ids": [a, ids[0]]})
    assert r.status_code == 201


def test_rename_player_keeps_identity(admin):
    gid, (a, b, c) = start_game(admin)
    save_round(admin, gid, 1, {a: 10, b: 5, c: 0})
    r = admin.patch(f"/api/v1/players/{a}", {"display_name": "Annika"})
    assert r.status_code == 200
    state = admin.get(f"/api/v1/games/{gid}").json["data"]
    assert state["leaderboard"][0]["name"] == "Annika"
    assert state["leaderboard"][0]["total"] == 10


def test_manual_finish_and_continue(admin):
    gid, (a, b, c) = start_game(admin)
    save_round(admin, gid, 1, {a: 10, b: 5, c: 0})
    r = admin.post(f"/api/v1/games/{gid}/finish")
    assert r.json["data"]["game"]["status"] == "FINISHED"
    assert r.json["data"]["game"]["finish_reason"] == "manual"
    assert [w["name"] for w in r.json["data"]["winners"]] == ["Anna"]
    # versehentlich beendet → fortsetzen
    r = admin.post(f"/api/v1/games/{gid}/continue", {})
    assert r.json["data"]["game"]["status"] == "ACTIVE"
    assert save_round(admin, gid, 2, {a: 1, b: 1, c: 1}).status_code == 201


def test_cancel_game(admin):
    gid, _ = start_game(admin)
    r = admin.post(f"/api/v1/games/{gid}/cancel")
    assert r.json["data"]["game"]["status"] == "CANCELLED"
    assert admin.get("/api/v1/games/current").json["data"] is None


def test_game_is_restored_after_restart(admin, ctx, app):
    """Neustart: neue Datenbankverbindung, das laufende Spiel ist weiterhin offen und vollständig."""
    gid, (a, b, c) = start_game(admin)
    save_round(admin, gid, 1, {a: 12, b: 7, c: 3})
    ctx.db.dispose()
    from app.extensions import Database
    from app.services.game_service import GameService
    fresh = GameService(Database(ctx.paths.database), lambda: 500)
    assert fresh.open_game_id() == gid
    state = fresh.snapshot(gid)
    assert state["game"]["current_round"] == 2
    assert [r["total"] for r in state["leaderboard"]] == [12, 7, 3]
    fresh.db.dispose()


def test_delete_history_game_requires_admin_and_closed_game(admin, controller):
    gid, _ = start_game(admin)
    assert admin.delete(f"/api/v1/games/{gid}").status_code == 409
    admin.post(f"/api/v1/games/{gid}/finish")
    assert controller.delete(f"/api/v1/games/{gid}").status_code == 403
    assert admin.delete(f"/api/v1/games/{gid}").status_code == 200
    assert admin.get(f"/api/v1/games/{gid}").status_code == 404
