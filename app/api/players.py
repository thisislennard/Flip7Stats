from __future__ import annotations

from ..security import validation as v
from ..security.authentication import EVERYONE, WRITERS, require
from . import api, ctx, ok, parse


@api.get("/players")
@require(*EVERYONE)
def list_players():
    return ok(ctx().games.list_players())


@api.post("/players")
@require(*WRITERS)
def create_player():
    return ok(ctx().games.create_player(parse(v.PlayerCreate)), 201)


@api.patch("/players/<int:player_id>")
@require(*WRITERS)
def rename_player(player_id: int):
    change = ctx().games.rename_player(player_id, parse(v.PlayerUpdate))
    ctx().after_change(change)
    return ok(change.data["player"])
