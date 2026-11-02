"""Statistiken – ausschließlich aus gespeicherten Rundendaten berechnet.

Gleichstand in der Siegstatistik: Haben am Spielende mehrere Spieler die höchste Punktzahl, erhält **jeder** von
ihnen einen Sieg (``wins``); zusätzlich wird er als geteilter Sieg gezählt (``shared_wins``). Die Siegquote ist
``wins / games_played``. Abgebrochene Spiele zählen nicht.

Führungswechsel: Nach jeder Runde wird der alleinige Führende bestimmt. Ein Wechsel liegt vor, wenn ein anderer
Spieler alleiniger Führender wird als der bisherige. Gleichstände an der Spitze unterbrechen eine Führung nicht und
zählen nicht als Wechsel; die erste Führung ist kein Wechsel.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Game, GameNight, GamePlayer, GameStatus, NightStatus, Round
from . import scoring_service as scoring
from .scoring_service import Participant, RoundData


def lead_changes(participants: list[Participant], rounds: list[RoundData]) -> list[dict]:
    series = scoring.cumulative(participants, rounds)
    numbers = [r.number for r in sorted(rounds, key=lambda x: x.number)]
    changes: list[dict] = []
    current: int | None = None
    for i, number in enumerate(numbers):
        values = {pid: s[i] for pid, s in series.items()}
        top = max(values.values(), default=0)
        at_top = [pid for pid, v in values.items() if v == top]
        if top <= 0 or len(at_top) != 1:
            continue
        leader = at_top[0]
        if current is not None and leader != current:
            changes.append({"round_number": number, "player_id": leader, "previous_player_id": current})
        current = leader
    return changes


def game_statistics(participants: list[Participant], rounds: list[RoundData], target: int) -> dict:
    board = scoring.leaderboard(participants, rounds, target)
    rank_of = {row["player_id"]: row["rank"] for row in board}
    per_player = []
    for p in participants:
        pts = [r.scores[p.player_id] for r in rounds if p.player_id in r.scores]
        per_player.append({
            "player_id": p.player_id,
            "name": p.name,
            "total": sum(pts),
            "rank": rank_of.get(p.player_id),
            "best_round": max(pts) if pts else None,
            "worst_round": min(pts) if pts else None,
            "average_round": round(sum(pts) / len(pts), 1) if pts else None,
            "rounds_played": len(pts),
            "zero_rounds": sum(1 for x in pts if x == 0),
        })
    series = scoring.cumulative(participants, rounds)
    ordered = sorted(rounds, key=lambda x: x.number)
    changes = lead_changes(participants, rounds)
    return {
        "rounds_played": len(rounds),
        "players": per_player,
        "lead_changes": changes,
        "lead_change_count": len(changes),
        "chart": {
            "labels": [r.number for r in ordered],
            "series": [{"player_id": p.player_id, "name": p.name, "data": series[p.player_id]} for p in participants],
        },
    }


def participants_of(game: Game) -> list[Participant]:
    return [Participant(gp.player_id, gp.player.display_name, gp.display_order) for gp in game.players]


def rounds_of(game: Game) -> list[RoundData]:
    return [RoundData(r.id, r.round_number, {s.player_id: s.points for s in r.scores}) for r in game.rounds]


def overall_statistics(session: Session) -> dict:
    games = session.scalars(
        select(Game).where(Game.status == GameStatus.FINISHED)
        .options(selectinload(Game.players).joinedload(GamePlayer.player),
                 selectinload(Game.rounds).selectinload(Round.scores))
    ).all()
    stats: dict[int, dict] = {}
    total_rounds = 0
    best_round: dict | None = None
    best_final: dict | None = None
    for game in games:
        parts = participants_of(game)
        rds = rounds_of(game)
        total_rounds += len(rds)
        board = scoring.leaderboard(parts, rds, game.target_score)
        winners = scoring.leaders(board)
        for p in parts:
            s = stats.setdefault(p.player_id, {
                "player_id": p.player_id, "name": p.name, "games_played": 0, "wins": 0, "shared_wins": 0,
                "final_scores": [], "round_points": [],
            })
            s["name"] = p.name
            s["games_played"] += 1
            if p.player_id in winners:
                s["wins"] += 1
                if len(winners) > 1:
                    s["shared_wins"] += 1
            final = next(row["total"] for row in board if row["player_id"] == p.player_id)
            s["final_scores"].append(final)
            if best_final is None or final > best_final["points"]:
                best_final = {"points": final, "player_id": p.player_id, "name": p.name, "game_id": game.id}
            for r in rds:
                if p.player_id in r.scores:
                    pts = r.scores[p.player_id]
                    s["round_points"].append(pts)
                    if best_round is None or pts > best_round["points"]:
                        best_round = {"points": pts, "player_id": p.player_id, "name": p.name, "game_id": game.id,
                                      "round_number": r.number}
    players = []
    for s in stats.values():
        finals, rp = s.pop("final_scores"), s.pop("round_points")
        s.update({
            "win_rate": round(s["wins"] / s["games_played"] * 100, 1) if s["games_played"] else 0.0,
            "average_final_score": round(sum(finals) / len(finals), 1) if finals else None,
            "average_round_points": round(sum(rp) / len(rp), 1) if rp else None,
            "highest_final_score": max(finals) if finals else None,
            "best_round": max(rp) if rp else None,
            "rounds_played": len(rp),
        })
        players.append(s)
    # Spieleabende: Abendsieger ist, wer an einem beendeten Abend die meisten Partien gewonnen hat (Gleichstand → alle).
    from .night_service import night_summary
    nights = session.scalars(
        select(GameNight).where(GameNight.status == NightStatus.FINISHED)
        .options(selectinload(GameNight.games).selectinload(Game.players).joinedload(GamePlayer.player),
                 selectinload(GameNight.games).selectinload(Game.rounds).selectinload(Round.scores))).all()
    by_id = {p["player_id"]: p for p in players}
    for p in players:
        p.update({"nights_played": 0, "night_wins": 0})
    nights_counted = 0
    for night in nights:
        table = [r for r in night_summary(night)["standings"] if r["games_played"] > 0]
        if not table:
            continue
        nights_counted += 1
        top = max(r["wins"] for r in table)
        for r in table:
            row = by_id.get(r["player_id"])
            if row is None:
                continue
            row["nights_played"] += 1
            if top > 0 and r["wins"] == top:
                row["night_wins"] += 1
    players.sort(key=lambda s: (-s["wins"], -s["win_rate"], s["name"].casefold()))
    return {
        "nights_played": nights_counted,
        "games_played": len(games),
        "total_rounds": total_rounds,
        "best_round": best_round,
        "highest_final_score": best_final,
        "players": players,
        "tie_rule": "Bei Gleichstand an der Spitze erhält jeder beteiligte Spieler einen Sieg (zusätzlich als "
                    "geteilter Sieg gezählt). Abgebrochene Spiele zählen nicht.",
    }
