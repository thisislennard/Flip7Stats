from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from . import Base, utcnow


def name_key(display_name: str) -> str:
    """Vergleichsschlüssel für Namen: „ Anna “ und „anna“ sind derselbe Spieler."""
    return " ".join(display_name.split()).casefold()


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(40))
    # Eindeutig, damit „Spieler aus früheren Spielen übernehmen“ nicht zu Dubletten führt. Kein Schlüssel für Beziehungen.
    name_key: Mapped[str] = mapped_column(String(40), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
