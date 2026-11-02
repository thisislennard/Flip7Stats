from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base, utcnow


class NightStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FINISHED = "FINISHED"


class GameNight(Base):
    """Ein Spieleabend: mehrere Partien mit derselben Punktzahl (Punkteziel je Partie)."""

    __tablename__ = "game_nights"
    __table_args__ = (
        CheckConstraint("target_score > 0", name="target_positive"),
        CheckConstraint("status IN ('ACTIVE','FINISHED')", name="status_valid"),
        Index("ix_game_nights_created_at", "created_at"),
        Index("uq_game_nights_single_active", "status", unique=True, sqlite_where=text("status = 'ACTIVE'")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(80))
    target_score: Mapped[int] = mapped_column(Integer, default=200)
    status: Mapped[str] = mapped_column(String(16), default=NightStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    games: Mapped[list["Game"]] = relationship(back_populates="night", order_by="Game.night_number",
                                               cascade="all, delete-orphan")


from .game import Game  # noqa: E402
