"""Serverseitige Eingabeprüfung aller Schreibanfragen.

Punkte müssen echte ganze Zahlen sein: keine Texte, keine Kommazahlen, keine Wahrheitswerte. Ein leeres Feld kommt als
``null`` an und bedeutet „Runde nicht mitgespielt“ – es wird nie stillschweigend als 0 gewertet.
"""

from __future__ import annotations

from .schema import F, Model, ValidationError  # noqa: F401  (ValidationError für Aufrufer)

_KEY = r"^[A-Za-z0-9_-]{8,64}$"
_TARGET = dict(ge=1, le=10_000)


def _ids(*, required: bool = False, min_len: int | None = None) -> F:
    item = F("int")
    if required:
        return F("list", item=item, min_len=min_len, max_len=18, unique=True)
    return F("list", item=item, min_len=min_len, max_len=18, unique=True, optional=True, default=None)


class GameCreate(Model):
    name = F("str", strip=True, max_len=80, optional=True, default=None)
    target_score = F("int", optional=True, default=None, **_TARGET)
    player_ids = F("list", item=F("int"), max_len=18, unique=True, factory=list)


class GameUpdate(Model):
    name = F("str", strip=True, max_len=80, optional=True, default=None)
    target_score = F("int", optional=True, default=None, **_TARGET)
    player_ids = _ids()


class GameContinue(Model):
    target_score = F("int", optional=True, default=None, **_TARGET)


class NightCreate(Model):
    name = F("str", strip=True, max_len=80, optional=True, default=None)
    target_score = F("int", optional=True, default=None, **_TARGET)
    player_ids = _ids(required=True, min_len=2)


class NightUpdate(Model):
    name = F("str", strip=True, max_len=80, optional=True, default=None)
    target_score = F("int", optional=True, default=None, **_TARGET)


class NextGame(Model):
    player_ids = _ids(min_len=2)
    target_score = F("int", optional=True, default=None, **_TARGET)


class PlayerCreate(Model):
    display_name = F("str", strip=True, min_len=1, max_len=40)


class PlayerUpdate(Model):
    display_name = F("str", strip=True, min_len=1, max_len=40)


class ScoreEntry(Model):
    player_id = F("int")
    points = F("int", optional=True)  # Pflichtfeld; None = Feld leer gelassen → Runde nicht mitgespielt


class RoundCreate(Model):
    idempotency_key = F("str", pattern=_KEY)
    round_number = F("int", ge=1)
    scores = F("list", item=F(ScoreEntry), min_len=1, max_len=18)


class RoundUpdate(Model):
    idempotency_key = F("str", pattern=_KEY, optional=True, default=None)
    scores = F("list", item=F(ScoreEntry), min_len=1, max_len=18)


class SettingsUpdate(Model):
    autostart = F("bool", optional=True, default=None)
    open_browser = F("bool", optional=True, default=None)
    default_target_score = F("int", optional=True, default=None, **_TARGET)
    default_player_count = F("int", optional=True, default=None, ge=2, le=18)
    network_mode = F("choice", choices=("auto", "wlan_only", "prefer_hotspot"), optional=True, default=None)
    port = F("int", optional=True, default=None, ge=1024, le=65535)
    hotspot_ssid = F("str", strip=True, min_len=1, max_len=32, optional=True, default=None)
    hotspot_password = F("str", min_len=8, max_len=63, optional=True, default=None)
    max_round_points = F("int", optional=True, default=None, ge=1, le=100_000)
    backup_keep = F("int", optional=True, default=None, ge=1, le=500)
    setup_completed = F("bool", optional=True, default=None)


class BackupRestore(Model):
    name = F("str", pattern=r"^[A-Za-z0-9_.-]{1,120}\.db$")


class TokenRotate(Model):
    role = F("choice", choices=("controller", "viewer"))


def error_details(exc) -> list[dict]:
    """Prüffehler in eine knappe, verständliche Liste übersetzen (ohne interne Typnamen)."""
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
        case "bool_type":
            return "Bitte ja oder nein angeben."
        case "string_type":
            return "Bitte einen Text eingeben."
        case "list_type" | "model_type":
            return "Ungültiger Wert."
        case "literal_error":
            return "Ungültige Auswahl."
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
