from __future__ import annotations

from flask import request

from ..security import validation as v
from ..security.authentication import ADMIN, EVERYONE, WRITERS, require
from . import actor, api, ctx, ok, parse


def _state(change, status: int = 200):
    c = ctx()
    c.after_change(change, "finish" if "game:finished" in change.events else None)
    return ok(c.games.snapshot(change.game_id) if change.game_id else None, status)


@api.post("/nights")
@require(*WRITERS)
def start_night():
    """Neuen Spieleabend beginnen – startet gleich die erste Partie."""
    c = ctx()
    return _state(c.nights.start_night(parse(v.NightCreate), c.settings.value.default_target_score, actor()), 201)


@api.get("/nights")
@require(*EVERYONE)
def list_nights():
    return ok(ctx().nights.list_nights("asc" if request.args.get("order") == "asc" else "desc"))


@api.get("/nights/<int:night_id>")
@require(*EVERYONE)
def get_night(night_id: int):
    return ok(ctx().nights.summary(night_id))


@api.patch("/nights/<int:night_id>")
@require(*WRITERS)
def update_night(night_id: int):
    change = ctx().nights.update_night(night_id, parse(v.NightUpdate))
    ctx().after_change(change)
    return ok(ctx().nights.summary(night_id))


@api.post("/nights/<int:night_id>/next-game")
@require(*WRITERS)
def next_game(night_id: int):
    return _state(ctx().nights.next_game(night_id, parse(v.NextGame), actor()), 201)


@api.post("/nights/<int:night_id>/finish")
@require(*WRITERS)
def finish_night(night_id: int):
    c = ctx()
    change = c.nights.finish_night(night_id, actor())
    c.after_change(change, "finish")
    return ok(c.nights.summary(night_id))


@api.delete("/nights/<int:night_id>")
@require(ADMIN)
def delete_night(night_id: int):
    c = ctx()
    c.backup.maybe_auto("auto")
    c.nights.delete_night(night_id)
    return ok({"deleted": night_id})
