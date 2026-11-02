"""Datenbankmodelle (SQLAlchemy 2)."""

from datetime import UTC, datetime

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Feste Namenskonvention, damit Alembic-Migrationen (Batch-Modus für SQLite) stabile Constraint-Namen haben.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def utcnow() -> datetime:
    """Zeitstempel in UTC ohne Zeitzonenangabe (SQLite speichert keine Zeitzonen)."""
    return datetime.now(UTC).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


from .game import Game, GameStatus  # noqa: E402
from .game_night import GameNight, NightStatus  # noqa: E402
from .game_player import GamePlayer  # noqa: E402
from .player import Player  # noqa: E402
from .round import Round  # noqa: E402
from .round_score import RoundScore  # noqa: E402
from .events import GameEvent, IdempotencyRequest  # noqa: E402

__all__ = ["Base", "Game", "GameStatus", "GameNight", "NightStatus", "GamePlayer", "Player", "Round", "RoundScore", "GameEvent",
           "IdempotencyRequest", "utcnow", "iso"]
