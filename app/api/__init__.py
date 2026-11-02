"""REST-API unter /api/v1. Einheitliches Antwortformat:

- Erfolg: ``{"ok": true, "data": …}``
- Fehler: ``{"ok": false, "error": {"code": "…", "message": "…", "details": …}}``
"""

from __future__ import annotations

from typing import TypeVar

from flask import Blueprint, current_app, jsonify, request
from pydantic import BaseModel, ValidationError

from ..security.validation import error_details
from ..services import Invalid

api = Blueprint("api", __name__, url_prefix="/api/v1")

M = TypeVar("M", bound=BaseModel)


def ctx():
    return current_app.extensions["flip7"]


def ok(data=None, status: int = 200, **extra):
    resp = jsonify({"ok": True, "data": data, **extra})
    resp.status_code = status
    return resp


def parse(model: type[M]) -> M:
    if not request.is_json:
        raise Invalid("Anfrage muss JSON enthalten.", code="unsupported_media_type")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise Invalid("Ungültiger JSON-Inhalt.")
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        details = error_details(exc)
        raise Invalid(details[0]["message"] if len(details) == 1 else "Bitte die Eingaben prüfen.",
                      details={"fields": details}) from None


def actor() -> str:
    from ..security.authentication import current_role
    return current_role() or ""


from . import games, history, nights, players, rounds, statistics, system  # noqa: E402,F401
