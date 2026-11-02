"""Spieleabende: mehrere Partien mit gemeinsamer Punktzahl, Abend-Endstand nach Partiesiegen.

Abendsieger ist, wer die meisten Partien gewonnen hat. Bei gleich vielen Siegen gibt es einen Gleichstand (alle
Beteiligten sind Abendsieger); die Gesamtpunkte werden angezeigt, entscheiden aber nicht.

Siege zählen wie in der Langzeitstatistik: Endet eine Partie mit Gleichstand an der Spitze, erhält jeder beteiligte
Spieler einen Sieg (zusätzlich als geteilter Sieg ausgewiesen). Abgebrochene Partien zählen nicht.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..extensions import Database
from ..models import Game, GameNight, GamePlayer, GameStatus, NightStatus, Round, iso, utcnow
from ..models.game import OPEN_STATUSES
from ..security import validation as v
from . import Conflict, Invalid, NotFound
from . import scoring_service as scoring
from . import statistics_service as stats
from .game_service import MIN_PLAYERS, Change, GameService, _event


def _load_night(s: Session, night_id: int) -> GameNight:
    night = s.scalar(
        select(GameNight).where(GameNight.id == night_id)
        .options(selectinload(GameNight.games).selectinload(Game.players).joinedload(GamePlayer.player),
                 selectinload(GameNight.games).selectinload(Game.rounds).selectinload(Round.scores)))
    if night is None:
        raise NotFound("Diesen Spieleabend gibt es nicht (mehr).")
    return night


def night_summary(night: GameNight) -> dict:
    """Abend-Endstand: Partiesiege je Spieler, dazu Partien, Punkte und beste Runde."""
    standings: dict[int, dict] = {}
    games = []
    for game in night.games:
        parts, rds = stats.participants_of(game), stats.rounds_of(game)
        board = scoring.leaderboard(parts, rds, game.target_score)
        winners = scoring.leaders(board) if game.status == GameStatus.FINISHED else []
        games.append({
            "id": game.id, "number": game.night_number, "status": game.status, "rounds_played": len(rds),
            "winners": [r["name"] for r in board if r["player_id"] in winners],
            "top_total": board[0]["total"] if board else 0, "started_at": iso(game.started_at),
            "finished_at": iso(game.finished_at),
        })
        if game.status == GameStatus.CANCELLED:
            continue
        for p in parts:
            row = standings.setdefault(p.player_id, {"player_id": p.player_id, "name": p.name, "wins": 0,
                                                     "shared_wins": 0, "games_played": 0, "total_points": 0,
                                                     "best_round": None})
            row["name"] = p.name
            total = next(r["total"] for r in board if r["player_id"] == p.player_id)
            row["total_points"] += total
            pts = [r.scores[p.player_id] for r in rds if p.player_id in r.scores]
            if pts and (row["best_round"] is None or max(pts) > row["best_round"]):
                row["best_round"] = max(pts)
            if game.status == GameStatus.FINISHED:
                row["games_played"] += 1
                if p.player_id in winners:
                    row["wins"] += 1
                    if len(winners) > 1:
                        row["shared_wins"] += 1
    # Platz nur nach Partiesiegen (gleiche Siege = gleicher Platz); Punkte sortieren nur innerhalb des Platzes.
    table = sorted(standings.values(), key=lambda r: (-r["wins"], -r["total_points"], r["name"].casefold()))
    prev, rank = None, 0
    for i, row in enumerate(table, start=1):
        key = row["wins"]
        if key != prev:
            rank, prev = i, key
        row["rank"] = rank
    top_wins = table[0]["wins"] if table else 0
    leaders = [r["name"] for r in table if top_wins > 0 and r["wins"] == top_wins]
    finished = [g for g in games if g["status"] == GameStatus.FINISHED]
    return {
        "id": night.id,
        "name": night.name,
        "target_score": night.target_score,
        "status": night.status,
        "created_at": iso(night.created_at),
        "finished_at": iso(night.finished_at),
        "games": games,
        "games_finished": len(finished),
        "current_number": max((g["number"] or 0 for g in games), default=0),
        "standings": table,
        "leaders": leaders,
    }


class NightService:
    def __init__(self, db: Database, games: GameService):
        self.db = db
        self.games = games

    @staticmethod
    def _active(s: Session) -> GameNight | None:
        return s.scalar(select(GameNight).where(GameNight.status == NightStatus.ACTIVE))

    def active_night_id(self) -> int | None:
        with self.db.read() as s:
            n = self._active(s)
            return n.id if n else None

    def _new_game(self, s: Session, night: GameNight, player_ids: list[int], actor: str) -> Game:
        if len(player_ids) < MIN_PLAYERS:
            raise Invalid(f"Für eine Partie werden mindestens {MIN_PLAYERS} Spieler benötigt.")
        if s.scalar(select(Game.id).where(Game.status.in_(OPEN_STATUSES))):
            raise Conflict("Es läuft noch eine Partie. Bitte zuerst beenden oder abbrechen.", code="open_game_exists")
        number = max((g.night_number or 0 for g in night.games), default=0) + 1
        game = Game(name=None, target_score=night.target_score, status=GameStatus.ACTIVE, current_round=1,
                    auto_finish=True, night_id=night.id, night_number=number, started_at=utcnow())
        s.add(game)
        s.flush()
        self.games._set_players(s, game, player_ids)
        _event(s, game, "game_started", actor, night_id=night.id, night_number=number,
               target_score=game.target_score, player_ids=player_ids)
        return game

    def start_night(self, data: v.NightCreate, default_target: int, actor: str) -> Change:
        with self.db.write() as s:
            if self._active(s):
                raise Conflict("Es läuft bereits ein Spieleabend. Bitte zuerst beenden.", code="open_night_exists")
            if s.scalar(select(Game.id).where(Game.status.in_(OPEN_STATUSES))):
                raise Conflict("Es gibt noch ein offenes Spiel. Bitte zuerst beenden oder abbrechen.",
                               code="open_game_exists")
            night = GameNight(name=data.name or None, target_score=data.target_score or default_target,
                              status=NightStatus.ACTIVE)
            s.add(night)
            s.flush()
            game = self._new_game(s, night, data.player_ids, actor)
            return Change(game.id, ["game:created", "game:started", "game:updated"])

    def next_game(self, night_id: int, data: v.NextGame, actor: str) -> Change:
        with self.db.write() as s:
            night = _load_night(s, night_id)
            if night.status != NightStatus.ACTIVE:
                raise Conflict("Dieser Spieleabend ist bereits beendet.", code="invalid_status")
            last = night.games[-1] if night.games else None
            if data.target_score is not None:
                night.target_score = data.target_score
            player_ids = data.player_ids or ([gp.player_id for gp in last.players] if last else [])
            game = self._new_game(s, night, player_ids, actor)
            return Change(game.id, ["game:created", "game:started", "game:updated"])

    def update_night(self, night_id: int, data: v.NightUpdate) -> Change:
        """Name und Punktzahl ändern. Eine neue Punktzahl gilt ab der nächsten Partie (und für die laufende, solange
        darin noch keine Runde gespielt wurde)."""
        with self.db.write() as s:
            night = _load_night(s, night_id)
            if data.name is not None:
                night.name = data.name or None
            if data.target_score is not None:
                night.target_score = data.target_score
                current = night.games[-1] if night.games else None
                if current and current.status == GameStatus.ACTIVE and not current.rounds:
                    current.target_score = data.target_score
            last = night.games[-1].id if night.games else None
            return Change(last, ["game:updated"] if last else [])

    def finish_night(self, night_id: int, actor: str) -> Change:
        with self.db.write() as s:
            night = _load_night(s, night_id)
            if night.status != NightStatus.ACTIVE:
                raise Conflict("Dieser Spieleabend ist bereits beendet.", code="invalid_status")
            current = night.games[-1] if night.games else None
            if current and current.status in OPEN_STATUSES:
                if current.rounds:
                    raise Conflict("Die aktuelle Partie läuft noch. Bitte zuerst die Partie beenden oder abbrechen.",
                                   code="game_running")
                night.games.remove(current)  # leere, gerade begonnene Partie verwerfen
                s.flush()
            night.status = NightStatus.FINISHED
            night.finished_at = utcnow()
            last = night.games[-1] if night.games else None
            if last:
                _event(s, last, "night_finished", actor, night_id=night.id)
            return Change(last.id if last else None, ["game:updated"] if last else [])

    def delete_night(self, night_id: int) -> None:
        with self.db.write() as s:
            night = s.get(GameNight, night_id)
            if night is None:
                raise NotFound("Diesen Spieleabend gibt es nicht (mehr).")
            if night.status == NightStatus.ACTIVE:
                raise Conflict("Ein laufender Spieleabend kann nicht gelöscht werden.", code="invalid_status")
            s.delete(night)

    def summary(self, night_id: int) -> dict:
        with self.db.read() as s:
            return night_summary(_load_night(s, night_id))

    def list_nights(self, order: str = "desc") -> list[dict]:
        with self.db.read() as s:
            q = (select(GameNight)
                 .options(selectinload(GameNight.games).selectinload(Game.players).joinedload(GamePlayer.player),
                          selectinload(GameNight.games).selectinload(Game.rounds).selectinload(Round.scores))
                 .order_by(GameNight.created_at.asc() if order == "asc" else GameNight.created_at.desc()))
            return [night_summary(n) for n in s.scalars(q).all()]
