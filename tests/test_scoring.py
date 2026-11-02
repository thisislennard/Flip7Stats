from app.services import scoring_service as scoring
from app.services.scoring_service import Participant, RoundData

P = [Participant(1, "Anna", 0), Participant(2, "Ben", 1), Participant(3, "Cem", 2)]


def rounds(*rows):
    return [RoundData(i, i, dict(zip((1, 2, 3), row))) for i, row in enumerate(rows, start=1)]


def test_totals_are_summed_from_rounds():
    assert scoring.totals(P, rounds((10, 20, 0), (5, 0, 7))) == {1: 15, 2: 20, 3: 7}


def test_leaderboard_sorted_descending_with_shared_ranks():
    board = scoring.leaderboard(P, rounds((30, 30, 10)), 200)
    assert [(r["name"], r["rank"]) for r in board] == [("Anna", 1), ("Ben", 1), ("Cem", 3)]


def test_tie_keeps_seat_order():
    board = scoring.leaderboard(P, rounds((5, 5, 5)), 200)
    assert [r["player_id"] for r in board] == [1, 2, 3]
    assert {r["rank"] for r in board} == {1}


def test_progress_bar_is_capped_but_score_is_not():
    board = scoring.leaderboard(P, rounds((150, 0, 0), (100, 0, 0)), 200)
    anna = board[0]
    assert anna["total"] == 250
    assert anna["progress"] == 125.0
    assert anna["progress_bar"] == 100.0
    assert anna["to_target"] == 0


def test_last_round_points_and_distance_to_target():
    board = {r["player_id"]: r for r in scoring.leaderboard(P, rounds((10, 20, 30), (1, 2, 0)), 200)}
    assert board[3]["last_round_points"] == 0
    assert board[3]["to_target"] == 170


def test_leaders_and_tie_detection():
    assert scoring.leaders(scoring.leaderboard(P, rounds((10, 12, 3)), 200)) == [2]
    assert scoring.leaders(scoring.leaderboard(P, rounds((12, 12, 3)), 200)) == [1, 2]
    assert scoring.leaders(scoring.leaderboard(P, [], 200)) == []


def test_target_reached_exactly_and_exceeded():
    assert scoring.target_reached(scoring.leaderboard(P, rounds((200, 0, 0)), 200), 200)
    assert scoring.target_reached(scoring.leaderboard(P, rounds((199, 0, 205)), 200), 200)
    assert not scoring.target_reached(scoring.leaderboard(P, rounds((199, 0, 0)), 200), 200)


def test_cumulative_series():
    assert scoring.cumulative(P, rounds((1, 2, 3), (4, 5, 6))) == {1: [1, 5], 2: [2, 7], 3: [3, 9]}
