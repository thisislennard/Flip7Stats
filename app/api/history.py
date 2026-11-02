from __future__ import annotations

from datetime import datetime

from flask import Response, request

from ..security.authentication import EVERYONE, WRITERS, require
from . import api, ctx, ok


def _ids() -> list[int] | None:
    raw = request.args.get("game_id")
    if not raw:
        return None
    try:
        return [int(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        return []


def _filename(ext: str) -> str:
    ids = _ids()
    suffix = f"spiel-{ids[0]}" if ids and len(ids) == 1 else "historie"
    return f"flip7live-{suffix}-{datetime.now():%Y%m%d-%H%M}.{ext}"


@api.get("/history")
@require(*EVERYONE)
def history():
    order = "asc" if request.args.get("order") == "asc" else "desc"
    return ok(ctx().games.list_games("FINISHED,CANCELLED", order))


@api.get("/history/export.json")
@require(*WRITERS)
def export_json():
    import json
    body = json.dumps(ctx().history.export_json(_ids()), ensure_ascii=False, indent=2)
    return Response(body, mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{_filename("json")}"'})


@api.get("/history/export.csv")
@require(*WRITERS)
def export_csv():
    return Response(ctx().history.export_csv(_ids()), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_filename("csv")}"'})
