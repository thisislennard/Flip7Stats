"""Serverseitige Eingabeprüfung aller Schreibanfragen (Pydantic).

Punkte müssen echte ganze Zahlen sein: keine Texte, keine Kommazahlen, keine Wahrheitswerte. Ein leeres Feld
kommt als ``null`` an und wird abgelehnt – es wird nie stillschweigend als 0 gewertet.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, field_validator

IdempotencyKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,64}$")]
PlayerName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
GameName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _unique(ids: list[int]) -> list[int]:
    if len(ids) != len(set(ids)):
        raise ValueError("Ein Spieler ist doppelt aufgeführt.")
    return ids


class GameCreate(_Strict):
    name: GameName | None = None
    target_score: StrictInt | None = Field(None, ge=1, le=10_000)
    player_ids: list[StrictInt] = Field(default_factory=list, max_length=18)

    _check_ids = field_validator("player_ids")(_unique)


class GameUpdate(_Strict):
    name: GameName | None = None
    target_score: StrictInt | None = Field(None, ge=1, le=10_000)
    player_ids: list[StrictInt] | None = Field(None, max_length=18)

    @field_validator("player_ids")
    @classmethod
    def _ids(cls, v: list[int] | None) -> list[int] | None:
        return _unique(v) if v is not None else v


class GameContinue(_Strict):
    target_score: StrictInt | None = Field(None, ge=1, le=10_000)


class NightCreate(_Strict):
    name: GameName | None = None
    target_score: StrictInt | None = Field(None, ge=1, le=10_000)
    player_ids: list[StrictInt] = Field(min_length=2, max_length=18)

    _check_ids = field_validator("player_ids")(_unique)


class NightUpdate(_Strict):
    name: GameName | None = None
    target_score: StrictInt | None = Field(None, ge=1, le=10_000)


class NextGame(_Strict):
    player_ids: list[StrictInt] | None = Field(None, min_length=2, max_length=18)
    target_score: StrictInt | None = Field(None, ge=1, le=10_000)

    @field_validator("player_ids")
    @classmethod
    def _ids(cls, v: list[int] | None) -> list[int] | None:
        return _unique(v) if v is not None else v


class PlayerCreate(_Strict):
    display_name: PlayerName


class PlayerUpdate(_Strict):
    display_name: PlayerName


class ScoreEntry(_Strict):
    player_id: StrictInt
    points: StrictInt | None  # None = Feld leer gelassen → Runde nicht mitgespielt


class RoundCreate(_Strict):
    idempotency_key: IdempotencyKey
    round_number: StrictInt = Field(ge=1)
    scores: list[ScoreEntry] = Field(min_length=1, max_length=18)


class RoundUpdate(_Strict):
    idempotency_key: IdempotencyKey | None = None
    scores: list[ScoreEntry] = Field(min_length=1, max_length=18)


class SettingsUpdate(_Strict):
    autostart: bool | None = None
    open_browser: bool | None = None
    default_target_score: StrictInt | None = Field(None, ge=1, le=10_000)
    default_player_count: StrictInt | None = Field(None, ge=2, le=18)
    network_mode: Literal["auto", "wlan_only", "prefer_hotspot"] | None = None
    port: StrictInt | None = Field(None, ge=1024, le=65535)
    hotspot_ssid: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)] | None = None
    hotspot_password: Annotated[str, StringConstraints(min_length=8, max_length=63)] | None = None
    max_round_points: StrictInt | None = Field(None, ge=1, le=100_000)
    backup_keep: StrictInt | None = Field(None, ge=1, le=500)
    setup_completed: bool | None = None


class BackupRestore(_Strict):
    name: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,120}\.db$")]


class TokenRotate(_Strict):
    role: Literal["controller", "viewer"]


def error_details(exc) -> list[dict]:
    """Pydantic-Fehler in eine knappe, verständliche Liste übersetzen (ohne interne Typnamen)."""
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        out.append({"field": loc, "message": _german(err)})
    return out


def _german(err: dict) -> str:
    t = err.get("type", "")
    ctx = err.get("ctx") or {}
    match t:
        case "int_type" | "int_parsing":
            return "Bitte eine ganze Zahl eingeben."
        case "greater_than_equal":
            return f"Der Wert muss mindestens {ctx.get('ge')} sein."
        case "less_than_equal":
            return f"Der Wert darf höchstens {ctx.get('le')} sein."
        case "string_too_short":
            return "Bitte einen Text eingeben."
        case "string_too_long":
            return "Der Text ist zu lang."
        case "missing":
            return "Angabe fehlt."
        case "extra_forbidden":
            return "Unbekanntes Feld."
        case "string_pattern_mismatch":
            return "Ungültiges Format."
        case "too_short":
            return "Zu wenige Einträge."
        case "too_long":
            return "Zu viele Einträge."
        case "value_error":
            return str(ctx.get("error", "Ungültiger Wert."))
        case _:
            return "Ungültiger Wert."
