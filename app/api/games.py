from __future__ import annotations

from flask import request

from ..security import validation as v
from ..security.authentication import ADMIN, EVERYONE, WRITERS, require
from . import actor, api, ctx, ok, parse


def _respond(change, status: int = 200, backup: str | None = None):
    c = ctx()
    c.after_change(change, backup)
    return ok(c.games.snapshot(change.game_id), status)


@api.post("/games")
@require(*WRITERS)
def create_game():
    data = parse(v.GameCreate)
    change = ctx().games.create_game(data, ctx().settings.value.default_target_score, actor())
    return _respond(change, 201)


@api.get("/games")
@require(*EVERYONE)
def list_games():
    order = "asc" if request.args.get("order") == "asc" else "desc"
    return ok(ctx().games.list_games(request.args.get("status"), order))


@api.get("/games/current")
@require(*EVERYONE)
def current_game():
    """Offenes Spiel; mit ?display=1 sonst das zuletzt beendete (wie im Dashboard)."""
    games = ctx().games
    gid = games.display_game_id() if request.args.get("display") == "1" else games.open_game_id()
    return ok(ctx().games.snapshot(gid) if gid else None)


@api.get("/games/<int:game_id>")
@require(*EVERYONE)
def get_game(game_id: int):
    return ok(ctx().games.snapshot(game_id))


@api.patch("/games/<int:game_id>")
@require(*WRITERS)
def update_game(game_id: int):
    return _respond(ctx().games.update_game(game_id, parse(v.GameUpdate), actor()))


@api.post("/games/<int:game_id>/start")
@require(*WRITERS)
def start_game(game_id: int):
    return _respond(ctx().games.start_game(game_id, actor()))


@api.post("/games/<int:game_id>/finish")
@require(*WRITERS)
def finish_game(game_id: int):
    return _respond(ctx().games.finish_game(game_id, actor()), backup="finish")


@api.post("/games/<int:game_id>/cancel")
@require(*WRITERS)
def cancel_game(game_id: int):
    return _respond(ctx().games.cancel_game(game_id, actor()), backup="finish")


@api.post("/games/<int:game_id>/continue")
@require(*WRITERS)
def continue_game(game_id: int):
    return _respond(ctx().games.continue_game(game_id, parse(v.GameContinue), actor()))


@api.delete("/games/<int:game_id>")
@require(ADMIN)
def delete_game(game_id: int):
    c = ctx()
    c.backup.maybe_auto("auto")
    c.games.delete_game(game_id, actor())
    return ok({"deleted": game_id})


@api.get("/games/<int:game_id>/events")
@require(*WRITERS)
def game_events(game_id: int):
    return ok(ctx().games.events(game_id))
