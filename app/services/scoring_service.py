"""Punkteberechnung ohne Datenbankbezug: Gesamtpunkte, Rangliste, Spielende.

Flip 7 Live erkennt keine Karten – Rundenpunkte werden manuell erfasst und hier nur summiert und bewertet.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Participant:
    player_id: int
    name: str
    order: int


@dataclass(frozen=True)
class RoundData:
    round_id: int
    number: int
    scores: dict[int, int] = field(default_factory=dict)  # player_id → Punkte


def totals(participants: list[Participant], rounds: list[RoundData]) -> dict[int, int]:
    """Gesamtpunkte je Spieler – immer aus den Rundendaten berechnet, nie gespeichert."""
    result = {p.player_id: 0 for p in participants}
    for r in rounds:
        for pid, pts in r.scores.items():
            if pid in result:
                result[pid] += pts
    return result


def cumulative(participants: list[Participant], rounds: list[RoundData]) -> dict[int, list[int]]:
    """Punkteentwicklung: Gesamtpunkte nach jeder Runde (Index 0 = nach Runde 1)."""
    running = {p.player_id: 0 for p in participants}
    series: dict[int, list[int]] = {p.player_id: [] for p in participants}
    for r in sorted(rounds, key=lambda x: x.number):
        for pid in running:
            running[pid] += r.scores.get(pid, 0)
            series[pid].append(running[pid])
    return series


def leaderboard(participants: list[Participant], rounds: list[RoundData], target: int) -> list[dict]:
    """Rangliste absteigend nach Gesamtpunkten. Gleichstand → gleicher Rang (1, 1, 3 …).

    Bei gleichem Stand bleibt die Sitzreihenfolge erhalten, damit die Anzeige nicht springt.
    """
    tot = totals(participants, rounds)
    last = max(rounds, key=lambda r: r.number) if rounds else None
    rows = sorted(participants, key=lambda p: (-tot[p.player_id], p.order))
    board: list[dict] = []
    prev_total: int | None = None
    rank = 0
    for index, p in enumerate(rows, start=1):
        total = tot[p.player_id]
        if total != prev_total:
            rank = index
            prev_total = total
        progress = total / target * 100 if target > 0 else 0.0
        board.append({
            "player_id": p.player_id,
            "name": p.name,
            "rank": rank,
            "total": total,
            "last_round_points": last.scores.get(p.player_id) if last else None,
            "progress": round(progress, 1),          # tatsächlicher Wert, kann > 100 sein
            "progress_bar": round(min(progress, 100.0), 1),  # Balkenbreite, auf 100 % begrenzt
            "to_target": max(0, target - total),
            "reached_target": total >= target,
        })
    return board


def leaders(board: list[dict]) -> list[int]:
    """Spieler mit der höchsten Gesamtpunktzahl (mehrere = Gleichstand). Leer, solange niemand Punkte hat."""
    if not board or board[0]["total"] <= 0:
        return []
    top = board[0]["total"]
    return [row["player_id"] for row in board if row["total"] == top]


def target_reached(board: list[dict], target: int) -> bool:
    return any(row["total"] >= target for row in board)
