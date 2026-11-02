from __future__ import annotations

from ..security.authentication import EVERYONE, require
from ..services.statistics_service import overall_statistics
from . import api, ctx, ok


@api.get("/games/<int:game_id>/leaderboard")
@require(*EVERYONE)
def leaderboard(game_id: int):
    state = ctx().games.snapshot(game_id)
    return ok({"leaderboard": state["leaderboard"], "leaders": state["leaders"], "tie": state["tie"],
               "winners": state["winners"], "target_score": state["game"]["target_score"]})


@api.get("/games/<int:game_id>/statistics")
@require(*EVERYONE)
def game_statistics(game_id: int):
    return ok(ctx().games.snapshot(game_id)["statistics"])


@api.get("/statistics/overall")
@require(*EVERYONE)
def overall():
    with ctx().db.read() as s:
        return ok(overall_statistics(s))
