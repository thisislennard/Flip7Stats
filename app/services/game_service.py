"""Spiel-, Spieler- und Rundenverwaltung. Jede Änderung läuft in genau einer Datenbanktransaktion.

Ergebnis jeder Änderung ist ein :class:`Change` mit der Spiel-ID und den Live-Ereignissen, die die API-Schicht
anschließend an die Geräte verschickt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..extensions import Database
from ..models import (Game, GameEvent, GamePlayer, GameStatus, IdempotencyRequest, Player, Round, RoundScore, iso,
                      utcnow)
from ..models.game import OPEN_STATUSES
from ..models.player import name_key
from ..security import validation as v
from . import Conflict, Invalid, NotFound
from . import scoring_service as scoring
from . import statistics_service as stats

MIN_PLAYERS = 2
MAX_PLAYERS = 18
IDEMPOTENCY_TTL = timedelta(days=7)
DISPLAY_FINISHED_FOR = timedelta(hours=12)


@dataclass
class Change:
    game_id: int | None
    events: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    replayed: bool = False


def _event(s: Session, game: Game, kind: str, actor: str, **payload) -> None:
    s.add(GameEvent(game_id=game.id, event_type=kind, actor=actor, payload=json.dumps(payload, ensure_ascii=False)))


def _load_game(s: Session, game_id: int) -> Game:
    game = s.scalar(
        select(Game).where(Game.id == game_id)
        .options(selectinload(Game.players).joinedload(GamePlayer.player),
                 selectinload(Game.rounds).selectinload(Round.scores))
    )
    if game is None:
        raise NotFound("Dieses Spiel gibt es nicht (mehr).")
    return game


def player_dict(p: Player) -> dict:
    return {"id": p.id, "display_name": p.display_name, "created_at": iso(p.created_at)}


class GameService:
    def __init__(self, db: Database, max_round_points: callable):
        self.db = db
        self._max_points = max_round_points  # aktueller Wert aus den Einstellungen

    # ------------------------------------------------------------------ Spieler

    def list_players(self) -> list[dict]:
        with self.db.read() as s:
            players = s.scalars(select(Player).order_by(func.lower(Player.display_name))).all()
            last_played = dict(s.execute(
                select(GamePlayer.player_id, func.max(Game.created_at))
                .join(Game, Game.id == GamePlayer.game_id).group_by(GamePlayer.player_id)).all())
            return [{**player_dict(p), "last_played_at": iso(last_played.get(p.id))} for p in players]

    def create_player(self, data: v.PlayerCreate) -> dict:
        key = name_key(data.display_name)
        with self.db.write() as s:
            existing = s.scalar(select(Player).where(Player.name_key == key))
            if existing:
                raise Conflict(f"„{existing.display_name}“ gibt es schon – bitte aus der Liste übernehmen.",
                               code="player_exists", details={"player": player_dict(existing)})
            p = Player(display_name=" ".join(data.display_name.split()), name_key=key)
            s.add(p)
            s.flush()
            return player_dict(p)

    def rename_player(self, player_id: int, data: v.PlayerUpdate) -> Change:
        key = name_key(data.display_name)
        with self.db.write() as s:
            p = s.get(Player, player_id)
            if p is None:
                raise NotFound("Diesen Spieler gibt es nicht.")
            clash = s.scalar(select(Player).where(Player.name_key == key, Player.id != player_id))
            if clash:
                raise Conflict(f"Der Name „{clash.display_name}“ ist schon vergeben.", code="player_exists")
            p.display_name = " ".join(data.display_name.split())
            p.name_key = key
            open_game = self._open_game(s)
            affected = open_game.id if open_game and any(gp.player_id == player_id for gp in open_game.players) else None
            return Change(affected, ["game:updated"] if affected else [], {"player": player_dict(p)})

    # ------------------------------------------------------------------ Spiele

    @staticmethod
    def _open_game(s: Session) -> Game | None:
        """Das laufende (ACTIVE) oder vorbereitete (CREATED) Hauptspiel."""
        return s.scalar(select(Game).where(Game.status.in_(OPEN_STATUSES))
                        .order_by((Game.status == GameStatus.ACTIVE).desc(), Game.created_at.desc()))

    def open_game_id(self) -> int | None:
        with self.db.read() as s:
            g = self._open_game(s)
            return g.id if g else None

    def display_game_id(self) -> int | None:
        """Was Dashboard und Steuerung zeigen: das offene Spiel, sonst das zuletzt beendete (Gewinneranzeige bleibt
        stehen, bis ein neues Spiel angelegt wird – höchstens 12 Stunden)."""
        with self.db.read() as s:
            g = self._open_game(s)
            if g:
                return g.id
            from ..models import GameNight, NightStatus
            night = s.scalar(select(GameNight).where(GameNight.status == NightStatus.ACTIVE))
            if night:  # zwischen zwei Partien eines laufenden Abends: letzte Partie (Gewinner + „Nächste Partie“)
                last_in_night = s.scalar(select(Game).where(Game.night_id == night.id)
                                         .order_by(Game.night_number.desc()).limit(1))
                if last_in_night:
                    return last_in_night.id
            last = s.scalar(select(Game).where(Game.status == GameStatus.FINISHED)
                            .order_by(Game.finished_at.desc()).limit(1))
            if last and last.finished_at and utcnow() - last.finished_at < DISPLAY_FINISHED_FOR:
                return last.id
            return None

    def _set_players(self, s: Session, game: Game, player_ids: list[int]) -> None:
        if len(player_ids) > MAX_PLAYERS:
            raise Invalid(f"Höchstens {MAX_PLAYERS} Spieler.")
        found = {p.id for p in s.scalars(select(Player).where(Player.id.in_(player_ids)))}
        missing = [pid for pid in player_ids if pid not in found]
        if missing:
            raise Invalid("Mindestens ein Spieler existiert nicht.", details={"player_ids": missing})
        current = {gp.player_id: gp for gp in game.players}
        for pid in list(current):
            if pid not in player_ids:
                game.players.remove(current[pid])
        s.flush()
        for order, pid in enumerate(player_ids):
            if pid in current:
                current[pid].display_order = order
            else:
                game.players.append(GamePlayer(player_id=pid, display_order=order))
        s.flush()

    def create_game(self, data: v.GameCreate, default_target: int, actor: str) -> Change:
        with self.db.write() as s:
            existing = self._open_game(s)
            if existing:
                raise Conflict("Es gibt bereits ein offenes Spiel. Bitte zuerst beenden oder abbrechen.",
                               code="open_game_exists", details={"game_id": existing.id})
            game = Game(name=data.name or None, target_score=data.target_score or default_target,
                        status=GameStatus.CREATED, current_round=1, auto_finish=True)
            s.add(game)
            s.flush()
            self._set_players(s, game, data.player_ids)
            _event(s, game, "game_created", actor, target_score=game.target_score, player_ids=data.player_ids)
            return Change(game.id, ["game:created", "game:updated"])

    def update_game(self, game_id: int, data: v.GameUpdate, actor: str) -> Change:
        with self.db.write() as s:
            game = _load_game(s, game_id)
            changes: dict = {}
            if data.name is not None and data.name != (game.name or ""):
                if game.status == GameStatus.CANCELLED:
                    raise Conflict("Abgebrochene Spiele können nicht mehr bearbeitet werden.")
                changes["name"] = [game.name, data.name or None]
                game.name = data.name or None
            if data.target_score is not None and data.target_score != game.target_score:
                if game.status != GameStatus.CREATED:
                    raise Conflict("Das Punkteziel kann nur vor Spielbeginn geändert werden "
                                   "(danach über „Spiel fortsetzen“).", code="game_started")
                changes["target_score"] = [game.target_score, data.target_score]
                game.target_score = data.target_score
            if data.player_ids is not None:
                if game.status != GameStatus.CREATED:
                    raise Conflict("Spieler können nur vor Spielbeginn geändert werden.", code="game_started")
                before = [gp.player_id for gp in game.players]
                if before != data.player_ids:
                    self._set_players(s, game, data.player_ids)
                    changes["player_ids"] = [before, data.player_ids]
            if changes:
                _event(s, game, "game_updated", actor, changes=changes)
            return Change(game.id, ["game:updated"] if changes else [])

    def start_game(self, game_id: int, actor: str) -> Change:
        with self.db.write() as s:
            game = _load_game(s, game_id)
            if game.status != GameStatus.CREATED:
                raise Conflict("Das Spiel wurde bereits gestartet.", code="invalid_status")
            if len(game.players) < MIN_PLAYERS:
                raise Invalid(f"Für den Start werden mindestens {MIN_PLAYERS} Spieler benötigt.")
            if s.scalar(select(Game.id).where(Game.status == GameStatus.ACTIVE, Game.id != game.id)):
                raise Conflict("Es läuft bereits ein anderes Spiel.", code="open_game_exists")
            game.status = GameStatus.ACTIVE
            game.started_at = utcnow()
            _event(s, game, "game_started", actor)
            return Change(game.id, ["game:started", "game:updated"])

    def finish_game(self, game_id: int, actor: str) -> Change:
        with self.db.write() as s:
            game = _load_game(s, game_id)
            if game.status != GameStatus.ACTIVE:
                raise Conflict("Nur ein laufendes Spiel kann beendet werden.", code="invalid_status")
            game.status = GameStatus.FINISHED
            game.finish_reason = "manual"
            game.finished_at = utcnow()
            _event(s, game, "game_finished", actor, reason="manual")
            return Change(game.id, ["game:finished", "game:updated"])

    def cancel_game(self, game_id: int, actor: str) -> Change:
        with self.db.write() as s:
            game = _load_game(s, game_id)
            if not game.is_open:
                raise Conflict("Nur ein offenes Spiel kann abgebrochen werden.", code="invalid_status")
            game.status = GameStatus.CANCELLED
            game.finished_at = utcnow()
            game.finish_reason = None
            _event(s, game, "game_cancelled", actor)
            return Change(game.id, ["game:updated"])

    def continue_game(self, game_id: int, data: v.GameContinue, actor: str) -> Change:
        """Manuelle Fortsetzung nach Spielende (Hausregeln). Mit neuem Punkteziel endet das Spiel wieder automatisch,
        sobald es erreicht wird; ohne neues Ziel nur noch manuell."""
        with self.db.write() as s:
            game = _load_game(s, game_id)
            if game.status != GameStatus.FINISHED:
                raise Conflict("Nur ein beendetes Spiel kann fortgesetzt werden.", code="invalid_status")
            other = self._open_game(s)
            if other and other.id != game.id:
                raise Conflict("Es gibt bereits ein anderes offenes Spiel.", code="open_game_exists",
                               details={"game_id": other.id})
            board = scoring.leaderboard(stats.participants_of(game), stats.rounds_of(game), game.target_score)
            top = board[0]["total"] if board else 0
            if data.target_score is not None:
                if data.target_score <= top:
                    raise Invalid(f"Das neue Punkteziel muss über dem aktuellen Höchststand ({top}) liegen.")
                game.target_score = data.target_score
                game.auto_finish = True
            else:
                game.auto_finish = False
            game.status = GameStatus.ACTIVE
            game.finished_at = None
            game.finish_reason = None
            _event(s, game, "game_continued", actor, target_score=game.target_score, auto_finish=game.auto_finish)
            return Change(game.id, ["game:started", "game:updated"])

    def delete_game(self, game_id: int, actor: str) -> Change:
        with self.db.write() as s:
            game = s.get(Game, game_id)
            if game is None:
                raise NotFound("Dieses Spiel gibt es nicht (mehr).")
            if game.is_open:
                raise Conflict("Ein offenes Spiel kann nicht gelöscht werden – bitte zuerst beenden oder abbrechen.",
                               code="invalid_status")
            s.delete(game)
            return Change(game_id, [])

    # ------------------------------------------------------------------ Runden

    def _validate_scores(self, game: Game, entries: list[v.ScoreEntry]) -> dict[int, int]:
        """Prüft die Punkte einer Runde. Leeres Feld (``None``/fehlt) = Runde nicht mitgespielt: kein Punkteeintrag,
        zählt weder als Runde noch als 0-Runde. Mindestens ein Spieler muss Punkte haben."""
        names = {gp.player_id: gp.player.display_name for gp in game.players}
        given = {e.player_id: e.points for e in entries}
        if len(given) != len(entries):
            raise Invalid("Ein Spieler ist doppelt aufgeführt.")
        unknown = [pid for pid in given if pid not in names]
        if unknown:
            raise Invalid("Punkte für Spieler, die nicht mitspielen.", details={"player_ids": unknown})
        played = {pid: pts for pid, pts in given.items() if pts is not None}
        if not played:
            raise Invalid("Bitte für mindestens einen Spieler Punkte eintragen (auch 0).", code="missing_points",
                          details={"players": list(names.values())})
        limit = self._max_points()
        too_high = [names[pid] for pid, pts in played.items() if pts > limit]
        if too_high:
            raise Invalid(f"Mehr als {limit} Punkte in einer Runde sind nicht plausibel: " + ", ".join(too_high),
                          code="points_out_of_range", details={"players": too_high, "max": limit})
        if any(pts < 0 for pts in played.values()):
            raise Invalid("Negative Punkte sind nicht erlaubt.", code="points_out_of_range")
        return played

    def _recalculate(self, s: Session, game: Game, actor: str) -> list[str]:
        """Aktuelle Runde und Spielende nach jeder Rundenänderung neu bestimmen."""
        s.flush()
        s.refresh(game, ["rounds"])
        game.current_round = len(game.rounds) + 1
        board = scoring.leaderboard(stats.participants_of(game), stats.rounds_of(game), game.target_score)
        reached = scoring.target_reached(board, game.target_score)
        events: list[str] = []
        if game.status == GameStatus.ACTIVE and game.auto_finish and reached:
            game.status = GameStatus.FINISHED
            game.finish_reason = "target"
            game.finished_at = utcnow()
            _event(s, game, "game_finished", actor, reason="target", winners=scoring.leaders(board))
            events.append("game:finished")
        elif game.status == GameStatus.FINISHED and game.finish_reason == "target" and not reached:
            # Korrektur hat das Spielende rückgängig gemacht → weiterspielen, sofern kein anderes Spiel läuft.
            if s.scalar(select(Game.id).where(Game.status.in_(OPEN_STATUSES), Game.id != game.id)) is None:
                game.status = GameStatus.ACTIVE
                game.finished_at = None
                game.finish_reason = None
                _event(s, game, "game_reopened", actor, reason="correction")
                events.append("game:started")
            else:
                game.finish_reason = "manual"
        return events

    def _idempotent(self, s: Session, key: str | None, scope: str) -> dict | None:
        if not key:
            return None
        rec = s.get(IdempotencyRequest, key)
        if rec is None:
            return None
        if rec.scope != scope:
            raise Conflict("Dieser Speichervorgang wurde bereits für etwas anderes verwendet.", code="idempotency_misuse")
        return json.loads(rec.response)

    def create_round(self, game_id: int, data: v.RoundCreate, actor: str) -> Change:
        scope = f"round:create:{game_id}"
        try:
            with self.db.write() as s:
                previous = self._idempotent(s, data.idempotency_key, scope)
                if previous is not None:
                    return Change(game_id, [], previous, replayed=True)
                game = _load_game(s, game_id)
                if game.status != GameStatus.ACTIVE:
                    raise Conflict("In diesem Spiel können keine Runden mehr gespeichert werden.", code="invalid_status")
                if data.round_number != game.current_round:
                    raise Conflict(f"Runde {data.round_number} ist nicht mehr aktuell – inzwischen ist Runde "
                                   f"{game.current_round} dran. Die Anzeige wurde aktualisiert.",
                                   code="round_mismatch", details={"current_round": game.current_round})
                scores = self._validate_scores(game, data.scores)
                rnd = Round(game_id=game.id, round_number=game.current_round,
                            scores=[RoundScore(player_id=pid, points=pts) for pid, pts in scores.items()])
                s.add(rnd)
                s.flush()
                _event(s, game, "round_created", actor, round_id=rnd.id, round_number=rnd.round_number,
                       scores={str(k): val for k, val in scores.items()})
                events = ["round:created", "scores:updated", "leaderboard:updated", "game:updated"]
                events += self._recalculate(s, game, actor)
                result = {"round": {"id": rnd.id, "round_number": rnd.round_number}}
                s.add(IdempotencyRequest(key=data.idempotency_key, scope=scope, status_code=201,
                                         response=json.dumps(result)))
                return Change(game.id, events, result)
        except IntegrityError:
            # Zwei Geräte haben gleichzeitig dieselbe Runde gespeichert (eindeutige Rundennummer verletzt).
            raise Conflict("Diese Runde wurde gerade von einem anderen Gerät gespeichert. Die Anzeige wurde "
                           "aktualisiert.", code="round_mismatch")

    def _load_round(self, s: Session, round_id: int) -> tuple[Round, Game]:
        rnd = s.get(Round, round_id)
        if rnd is None:
            raise NotFound("Diese Runde gibt es nicht (mehr).")
        game = _load_game(s, rnd.game_id)
        if game.status not in (GameStatus.ACTIVE, GameStatus.FINISHED):
            raise Conflict("Runden dieses Spiels können nicht mehr geändert werden.", code="invalid_status")
        return rnd, game

    def update_round(self, round_id: int, data: v.RoundUpdate, actor: str) -> Change:
        scope = f"round:update:{round_id}"
        with self.db.write() as s:
            previous = self._idempotent(s, data.idempotency_key, scope)
            if previous is not None:
                return Change(previous.get("game_id"), [], previous, replayed=True)
            rnd, game = self._load_round(s, round_id)
            scores = self._validate_scores(game, data.scores)
            before = {sc.player_id: sc.points for sc in rnd.scores}
            existing = {sc.player_id: sc for sc in rnd.scores}
            for pid, sc in existing.items():
                if pid not in scores:
                    rnd.scores.remove(sc)  # jetzt „nicht mitgespielt“
            s.flush()
            for pid, pts in scores.items():
                if pid in existing:
                    existing[pid].points = pts
                else:
                    rnd.scores.append(RoundScore(player_id=pid, points=pts))
            rnd.updated_at = utcnow()
            _event(s, game, "round_updated", actor, round_id=rnd.id, round_number=rnd.round_number,
                   before={str(k): val for k, val in before.items()}, after={str(k): val for k, val in scores.items()})
            events = ["round:updated", "scores:updated", "leaderboard:updated", "game:updated"]
            events += self._recalculate(s, game, actor)
            result = {"round": {"id": rnd.id, "round_number": rnd.round_number}, "game_id": game.id}
            if data.idempotency_key:
                s.add(IdempotencyRequest(key=data.idempotency_key, scope=scope, status_code=200,
                                         response=json.dumps(result)))
            return Change(game.id, events, result)

    def delete_round(self, round_id: int, actor: str) -> Change:
        with self.db.write() as s:
            rnd, game = self._load_round(s, round_id)
            number = rnd.round_number
            before = {str(sc.player_id): sc.points for sc in rnd.scores}
            s.delete(rnd)
            s.flush()
            # Nachfolgende Runden aufrücken lassen, damit die Nummerierung lückenlos bleibt.
            later = s.scalars(select(Round).where(Round.game_id == game.id, Round.round_number > number)
                              .order_by(Round.round_number)).all()
            for r in later:
                r.round_number -= 1
                s.flush()
            _event(s, game, "round_deleted", actor, round_number=number, scores=before)
            events = ["round:deleted", "scores:updated", "leaderboard:updated", "game:updated"]
            events += self._recalculate(s, game, actor)
            return Change(game.id, events, {"deleted_round_number": number})

    def prune_idempotency(self) -> int:
        with self.db.write() as s:
            res = s.execute(delete(IdempotencyRequest).where(IdempotencyRequest.created_at < utcnow() - IDEMPOTENCY_TTL))
            return res.rowcount or 0

    # ------------------------------------------------------------------ Lesen

    def list_games(self, status: str | None = None, order: str = "desc") -> list[dict]:
        with self.db.read() as s:
            q = select(Game).options(selectinload(Game.players).joinedload(GamePlayer.player),
                                     selectinload(Game.rounds).selectinload(Round.scores))
            if status:
                q = q.where(Game.status.in_([x.strip().upper() for x in status.split(",")]))
            q = q.order_by(Game.created_at.asc() if order == "asc" else Game.created_at.desc())
            return [summary(g) for g in s.scalars(q).all()]

    def snapshot(self, game_id: int) -> dict:
        with self.db.read() as s:
            game = _load_game(s, game_id)
            state = snapshot(game)
            state["night"] = None
            if game.night_id:
                from .night_service import _load_night, night_summary
                state["night"] = night_summary(_load_night(s, game.night_id))
            return state

    def events(self, game_id: int) -> list[dict]:
        with self.db.read() as s:
            _load_game(s, game_id)
            rows = s.scalars(select(GameEvent).where(GameEvent.game_id == game_id).order_by(GameEvent.id)).all()
            return [{"id": e.id, "type": e.event_type, "actor": e.actor, "payload": json.loads(e.payload),
                     "created_at": iso(e.created_at)} for e in rows]


def game_dict(game: Game) -> dict:
    return {
        "id": game.id,
        "name": game.name,
        "target_score": game.target_score,
        "status": game.status,
        "current_round": game.current_round,
        "rounds_played": len(game.rounds),
        "player_count": len(game.players),
        "auto_finish": game.auto_finish,
        "finish_reason": game.finish_reason,
        "created_at": iso(game.created_at),
        "started_at": iso(game.started_at),
        "finished_at": iso(game.finished_at),
        "night_id": game.night_id,
        "night_number": game.night_number,
    }


def summary(game: Game) -> dict:
    parts, rds = stats.participants_of(game), stats.rounds_of(game)
    board = scoring.leaderboard(parts, rds, game.target_score)
    top = scoring.leaders(board)
    return {**game_dict(game),
            "players": [{"player_id": p.player_id, "name": p.name} for p in parts],
            "leaderboard": board,
            "winners": [r["name"] for r in board if r["player_id"] in top] if game.status == GameStatus.FINISHED else []}


def snapshot(game: Game) -> dict:
    """Vollständiger, serverseitig berechneter Spielstand – die einzige Wahrheit für alle Anzeigen."""
    parts, rds = stats.participants_of(game), stats.rounds_of(game)
    board = scoring.leaderboard(parts, rds, game.target_score)
    top = scoring.leaders(board)
    names = {p.player_id: p.name for p in parts}
    return {
        "game": game_dict(game),
        "players": [{"player_id": p.player_id, "name": p.name, "order": p.order} for p in parts],
        "leaderboard": board,
        "leaders": [{"player_id": pid, "name": names[pid]} for pid in top],
        "tie": len(top) > 1,
        "winners": [{"player_id": pid, "name": names[pid]} for pid in top] if game.status == GameStatus.FINISHED else [],
        "rounds": [{"id": r.id, "round_number": r.round_number, "created_at": iso(r.created_at),
                    "updated_at": iso(r.updated_at), "scores": {str(s.player_id): s.points for s in r.scores}}
                   for r in game.rounds],
        "statistics": stats.game_statistics(parts, rds, game.target_score),
    }
