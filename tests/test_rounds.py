from conftest import idem, save_round, start_game


def test_save_round_updates_totals_and_next_round(admin):
    gid, (a, b, c) = start_game(admin)
    r = save_round(admin, gid, 1, {a: 25, b: 0, c: 40})
    assert r.status_code == 201
    state = r.json["data"]["state"]
    assert state["game"]["current_round"] == 2
    assert [(x["name"], x["total"], x["rank"]) for x in state["leaderboard"]] == [
        ("Cem", 40, 1), ("Anna", 25, 2), ("Ben", 0, 3)]


def test_duplicate_request_does_not_create_second_round(admin):
    gid, (a, b, c) = start_game(admin)
    key = idem()
    first = save_round(admin, gid, 1, {a: 10, b: 10, c: 10}, key=key)
    again = save_round(admin, gid, 1, {a: 10, b: 10, c: 10}, key=key)
    assert first.status_code == 201
    assert again.status_code == 200 and again.json["replayed"] is True
    assert again.json["data"]["round"]["id"] == first.json["data"]["round"]["id"]
    assert len(admin.get(f"/api/v1/games/{gid}/rounds").json["data"]) == 1


def test_second_device_saving_same_round_number_is_rejected(admin, controller):
    gid, (a, b, c) = start_game(admin)
    assert save_round(admin, gid, 1, {a: 1, b: 2, c: 3}).status_code == 201
    r = save_round(controller, gid, 1, {a: 9, b: 9, c: 9})
    assert r.status_code == 409 and r.json["error"]["code"] == "round_mismatch"
    assert len(admin.get(f"/api/v1/games/{gid}/rounds").json["data"]) == 1


def test_empty_field_means_round_not_played(admin):
    gid, (a, b, c) = start_game(admin)
    r = save_round(admin, gid, 1, {a: 10, b: None, c: 0})
    assert r.status_code == 201
    state = r.json["data"]["state"]
    assert state["rounds"][0]["scores"] == {str(a): 10, str(c): 0}
    ben = next(x for x in state["leaderboard"] if x["name"] == "Ben")
    assert ben["total"] == 0 and ben["last_round_points"] is None
    stats = {x["name"]: x for x in state["statistics"]["players"]}
    assert stats["Ben"]["rounds_played"] == 0 and stats["Ben"]["zero_rounds"] == 0
    assert stats["Cem"]["zero_rounds"] == 1


def test_missing_player_counts_as_not_played(admin):
    gid, (a, b, c) = start_game(admin)
    r = save_round(admin, gid, 1, {a: 10, b: 3})
    assert r.status_code == 201
    assert str(c) not in r.json["data"]["state"]["rounds"][0]["scores"]


def test_round_needs_at_least_one_player_with_points(admin):
    gid, (a, b, c) = start_game(admin)
    r = save_round(admin, gid, 1, {a: None, b: None, c: None})
    assert r.status_code == 422 and r.json["error"]["code"] == "missing_points"
    assert admin.get(f"/api/v1/games/{gid}").json["data"]["game"]["current_round"] == 1


def test_correction_can_switch_between_played_and_not_played(admin):
    gid, (a, b, c) = start_game(admin)
    rid = save_round(admin, gid, 1, {a: 10, b: 20, c: 30}).json["data"]["round"]["id"]
    r = admin.patch(f"/api/v1/rounds/{rid}", {"scores": [
        {"player_id": a, "points": 10}, {"player_id": b, "points": None}, {"player_id": c, "points": 30}]})
    assert r.status_code == 200
    assert r.json["data"]["state"]["rounds"][0]["scores"] == {str(a): 10, str(c): 30}
    r = admin.patch(f"/api/v1/rounds/{rid}", {"scores": [
        {"player_id": a, "points": 10}, {"player_id": b, "points": 7}, {"player_id": c, "points": 30}]})
    assert r.json["data"]["state"]["rounds"][0]["scores"] == {str(a): 10, str(b): 7, str(c): 30}


def test_csv_marks_round_not_played(admin):
    gid, (a, b, c) = start_game(admin)
    save_round(admin, gid, 1, {a: 10, b: None, c: 5})
    assert admin.post(f"/api/v1/games/{gid}/finish").status_code == 200
    csv_text = admin.get(f"/api/v1/history/export.csv?game_id={gid}").get_data(as_text=True)
    assert "Ben;nicht mitgespielt;0" in csv_text


def test_invalid_points_are_rejected(admin):
    gid, (a, b, c) = start_game(admin)
    for bad in (-1, 1.5, "10", True, 501):
        r = admin.post(f"/api/v1/games/{gid}/rounds", {
            "idempotency_key": idem(), "round_number": 1,
            "scores": [{"player_id": a, "points": bad}, {"player_id": b, "points": 0}, {"player_id": c, "points": 0}]})
        assert r.status_code == 422, bad
    assert admin.get(f"/api/v1/games/{gid}").json["data"]["game"]["current_round"] == 1


def test_game_ends_exactly_at_target(admin):
    gid, (a, b, c) = start_game(admin, target=200)
    save_round(admin, gid, 1, {a: 150, b: 20, c: 0})
    r = save_round(admin, gid, 2, {a: 50, b: 20, c: 0})
    state = r.json["data"]["state"]
    assert state["game"]["status"] == "FINISHED"
    assert state["game"]["finish_reason"] == "target"
    assert [w["name"] for w in state["winners"]] == ["Anna"]
    assert save_round(admin, gid, 3, {a: 1, b: 1, c: 1}).status_code == 409


def test_game_ends_when_target_exceeded_and_highest_total_wins(admin):
    gid, (a, b, c) = start_game(admin, target=200)
    save_round(admin, gid, 1, {a: 190, b: 195, c: 0})
    state = save_round(admin, gid, 2, {a: 30, b: 10, c: 0}).json["data"]["state"]
    assert state["game"]["status"] == "FINISHED"
    assert [w["name"] for w in state["winners"]] == ["Anna"]  # 220 vor Ben mit 205


def test_tie_at_the_end_is_shown(admin):
    gid, (a, b, c) = start_game(admin, target=100)
    state = save_round(admin, gid, 1, {a: 120, b: 120, c: 5}).json["data"]["state"]
    assert state["game"]["status"] == "FINISHED"
    assert state["tie"] is True
    assert sorted(w["name"] for w in state["winners"]) == ["Anna", "Ben"]


def test_edit_earlier_round_recalculates(admin):
    gid, (a, b, c) = start_game(admin)
    r1 = save_round(admin, gid, 1, {a: 10, b: 20, c: 30}).json["data"]["round"]["id"]
    save_round(admin, gid, 2, {a: 5, b: 5, c: 5})
    r = admin.patch(f"/api/v1/rounds/{r1}", {"scores": [
        {"player_id": a, "points": 50}, {"player_id": b, "points": 20}, {"player_id": c, "points": 30}]})
    assert r.status_code == 200
    board = r.json["data"]["state"]["leaderboard"]
    assert board[0]["name"] == "Anna" and board[0]["total"] == 55
    events = admin.get(f"/api/v1/games/{gid}/events").json["data"]
    upd = [e for e in events if e["type"] == "round_updated"][0]
    assert upd["payload"]["before"][str(a)] == 10 and upd["payload"]["after"][str(a)] == 50


def test_correction_can_undo_automatic_end(admin):
    gid, (a, b, c) = start_game(admin, target=100)
    rid = save_round(admin, gid, 1, {a: 150, b: 0, c: 0}).json["data"]["round"]["id"]
    r = admin.patch(f"/api/v1/rounds/{rid}", {"scores": [
        {"player_id": a, "points": 15}, {"player_id": b, "points": 0}, {"player_id": c, "points": 0}]})
    assert r.json["data"]["state"]["game"]["status"] == "ACTIVE"
    assert save_round(admin, gid, 2, {a: 1, b: 1, c: 1}).status_code == 201


def test_delete_round_renumbers_following_rounds(admin):
    gid, (a, b, c) = start_game(admin)
    ids = [save_round(admin, gid, n, {a: n, b: 0, c: 0}).json["data"]["round"]["id"] for n in (1, 2, 3)]
    r = admin.delete(f"/api/v1/rounds/{ids[1]}")
    assert r.status_code == 200
    state = r.json["data"]["state"]
    assert [x["round_number"] for x in state["rounds"]] == [1, 2]
    assert state["game"]["current_round"] == 3
    assert state["leaderboard"][0]["total"] == 4
    assert save_round(admin, gid, 3, {a: 0, b: 0, c: 0}).status_code == 201


def test_round_in_cancelled_game_cannot_be_changed(admin):
    gid, (a, b, c) = start_game(admin)
    rid = save_round(admin, gid, 1, {a: 1, b: 1, c: 1}).json["data"]["round"]["id"]
    admin.post(f"/api/v1/games/{gid}/cancel")
    assert admin.delete(f"/api/v1/rounds/{rid}").status_code == 409
