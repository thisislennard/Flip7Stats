from __future__ import annotations

from ..security import validation as v
from ..security.authentication import EVERYONE, WRITERS, require
from . import actor, api, ctx, ok, parse


@api.get("/games/<int:game_id>/rounds")
@require(*EVERYONE)
def list_rounds(game_id: int):
    return ok(ctx().games.snapshot(game_id)["rounds"])


@api.post("/games/<int:game_id>/rounds")
@require(*WRITERS)
def create_round(game_id: int):
    c = ctx()
    change = c.games.create_round(game_id, parse(v.RoundCreate), actor())
    if not change.replayed:
        c.after_change(change, "finish" if "game:finished" in change.events else "auto")
    # Wiederholte Anfrage (gleicher Idempotenzschlüssel): dieselbe Runde, keine neue – mit aktuellem Stand.
    return ok({**change.data, "state": c.games.snapshot(game_id)}, 200 if change.replayed else 201,
              replayed=change.replayed)


@api.patch("/rounds/<int:round_id>")
@require(*WRITERS)
def update_round(round_id: int):
    c = ctx()
    change = c.games.update_round(round_id, parse(v.RoundUpdate), actor())
    if not change.replayed:
        c.after_change(change, "auto")
    return ok({**change.data, "state": c.games.snapshot(change.game_id)}, replayed=change.replayed)


@api.delete("/rounds/<int:round_id>")
@require(*WRITERS)
def delete_round(round_id: int):
    c = ctx()
    change = c.games.delete_round(round_id, actor())
    c.after_change(change, "auto")
    return ok({**change.data, "state": c.games.snapshot(change.game_id)})
