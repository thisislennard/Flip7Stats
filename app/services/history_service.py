"""Spielhistorie und Export (CSV/JSON)."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..extensions import Database
from ..models import Game, GamePlayer, GameStatus, Round
from .game_service import snapshot

HISTORY_STATUSES = (GameStatus.FINISHED, GameStatus.CANCELLED)


class HistoryService:
    def __init__(self, db: Database):
        self.db = db

    def _games(self, game_ids: list[int] | None = None) -> list[dict]:
        with self.db.read() as s:
            q = (select(Game).where(Game.status.in_(HISTORY_STATUSES))
                 .options(selectinload(Game.players).joinedload(GamePlayer.player),
                          selectinload(Game.rounds).selectinload(Round.scores))
                 .order_by(Game.created_at))
            if game_ids is not None:
                q = q.where(Game.id.in_(game_ids))
            return [snapshot(g) for g in s.scalars(q).all()]

    def export_json(self, game_ids: list[int] | None = None) -> dict:
        games = self._games(game_ids)
        return {
            "format": "flip7live-history",
            "version": 1,
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "games": [{
                "id": g["game"]["id"],
                "name": g["game"]["name"],
                "night_id": g["game"]["night_id"],
                "night_number": g["game"]["night_number"],
                "status": g["game"]["status"],
                "target_score": g["game"]["target_score"],
                "created_at": g["game"]["created_at"],
                "started_at": g["game"]["started_at"],
                "finished_at": g["game"]["finished_at"],
                "rounds_played": g["game"]["rounds_played"],
                "players": g["players"],
                "final_ranking": g["leaderboard"],
                "winners": g["winners"],
                "rounds": g["rounds"],
            } for g in games],
        }

    def export_csv(self, game_ids: list[int] | None = None) -> str:
        """Eine Zeile je Spieler und Runde – lässt sich direkt in Excel öffnen (Semikolon, UTF-8 mit BOM)."""
        buf = io.StringIO()
        buf.write("﻿")
        w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
        w.writerow(["Spiel-ID", "Spieleabend-ID", "Partie", "Spielname", "Status", "Punkteziel", "Gestartet (UTC)", "Beendet (UTC)",
                    "Runde", "Spieler", "Rundenpunkte", "Gesamt nach Runde", "Endstand", "Endrang", "Gewinner"])
        for g in self._games(game_ids):
            game = g["game"]
            names = {p["player_id"]: p["name"] for p in g["players"]}
            final = {r["player_id"]: r for r in g["leaderboard"]}
            winners = {w_["player_id"] for w_ in g["winners"]}
            running = {pid: 0 for pid in names}
            for rnd in g["rounds"]:
                for pid, name in names.items():
                    pts = rnd["scores"].get(str(pid))  # fehlt = Runde nicht mitgespielt
                    running[pid] += pts or 0
                    w.writerow([game["id"], game["night_id"] or "", game["night_number"] or "", game["name"] or "",
                                game["status"], game["target_score"],
                                game["started_at"] or "", game["finished_at"] or "", rnd["round_number"], name, "nicht mitgespielt" if pts is None else pts,
                                running[pid], final[pid]["total"], final[pid]["rank"],
                                "ja" if pid in winners else "nein"])
        return buf.getvalue()
