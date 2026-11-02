import csv
import io

from conftest import make_players, save_round, start_game

from app.services.scoring_service import Participant, RoundData
from app.services.statistics_service import game_statistics, lead_changes

P = [Participant(1, "Anna", 0), Participant(2, "Ben", 1)]


def rounds(*rows):
    return [RoundData(i, i, dict(zip((1, 2), row))) for i, row in enumerate(rows, start=1)]


def test_lead_changes_ignore_ties_and_first_lead():
    # Anna führt, Gleichstand, Ben führt, Anna führt
    changes = lead_changes(P, rounds((10, 0), (0, 10), (0, 5), (20, 0)))
    assert [(c["round_number"], c["player_id"]) for c in changes] == [(3, 2), (4, 1)]


def test_game_statistics_per_player():
    s = game_statistics(P, rounds((10, 0), (30, 0), (0, 25)), 200)
    anna = next(p for p in s["players"] if p["name"] == "Anna")
    ben = next(p for p in s["players"] if p["name"] == "Ben")
    assert (anna["total"], anna["best_round"], anna["worst_round"], anna["average_round"], anna["zero_rounds"]) == (40, 30, 0, 13.3, 1)
    assert ben["zero_rounds"] == 2 and ben["rank"] == 2
    assert s["rounds_played"] == 3
    assert s["chart"]["labels"] == [1, 2, 3]
    assert s["chart"]["series"][0]["data"] == [10, 40, 40]


def _finished_game(api, names, rows, target=50):
    gid, ids = start_game(api, names=names, target=target)
    for n, row in enumerate(rows, start=1):
        save_round(api, gid, n, dict(zip(ids, row)))
    state = api.get(f"/api/v1/games/{gid}").json["data"]
    if state["game"]["status"] == "ACTIVE":
        api.post(f"/api/v1/games/{gid}/finish")
    return gid, ids


def test_overall_statistics_and_tie_rule(admin):
    _finished_game(admin, ("Anna", "Ben"), [(30, 10), (25, 5)])        # Anna gewinnt
    a, b = [p["id"] for p in admin.get("/api/v1/players").json["data"]]
    gid = admin.post("/api/v1/games", {"player_ids": [a, b], "target_score": 50}).json["data"]["game"]["id"]
    admin.post(f"/api/v1/games/{gid}/start")
    save_round(admin, gid, 1, {a: 60, b: 60})                           # Gleichstand
    gid3 = admin.post("/api/v1/games", {"player_ids": [a, b]}).json["data"]["game"]["id"]
    admin.post(f"/api/v1/games/{gid3}/start")
    save_round(admin, gid3, 1, {a: 0, b: 99})
    admin.post(f"/api/v1/games/{gid3}/cancel")                          # zählt nicht

    s = admin.get("/api/v1/statistics/overall").json["data"]
    assert s["games_played"] == 2 and s["total_rounds"] == 3
    anna = next(p for p in s["players"] if p["name"] == "Anna")
    ben = next(p for p in s["players"] if p["name"] == "Ben")
    assert (anna["wins"], anna["shared_wins"], anna["win_rate"]) == (2, 1, 100.0)
    assert (ben["wins"], ben["shared_wins"], ben["win_rate"]) == (1, 1, 50.0)
    assert anna["highest_final_score"] == 60 and anna["best_round"] == 60
    assert s["best_round"]["points"] == 60
    assert "Gleichstand" in s["tie_rule"]


def test_history_lists_finished_and_cancelled_sorted(admin):
    g1, _ = _finished_game(admin, ("Anna", "Ben"), [(60, 0)])
    a, b = [p["id"] for p in admin.get("/api/v1/players").json["data"]]
    g2 = admin.post("/api/v1/games", {"player_ids": [a, b]}).json["data"]["game"]["id"]
    admin.post(f"/api/v1/games/{g2}/cancel")
    desc = [g["id"] for g in admin.get("/api/v1/history").json["data"]]
    asc = [g["id"] for g in admin.get("/api/v1/history?order=asc").json["data"]]
    assert desc == [g2, g1] and asc == [g1, g2]


def test_csv_and_json_export(admin, viewer):
    gid, ids = _finished_game(admin, ("Anna", "Ben"), [(30, 0), (25, 10)])
    r = admin.get(f"/api/v1/history/export.csv?game_id={gid}")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(r.data.decode("utf-8-sig")), delimiter=";"))
    assert rows[0][0] == "Spiel-ID" and len(rows) == 1 + 2 * 2
    assert rows[-1][9] == "Ben" and rows[-1][11] == "10"
    j = admin.get("/api/v1/history/export.json").json
    assert j["format"] == "flip7live-history" and j["games"][0]["winners"][0]["name"] == "Anna"
    assert len(j["games"][0]["rounds"]) == 2
    assert viewer.get("/api/v1/history/export.csv").status_code == 403


def test_open_game_is_not_in_history(admin):
    start_game(admin)
    assert admin.get("/api/v1/history").json["data"] == []
    make_players(admin, "Zoe")
