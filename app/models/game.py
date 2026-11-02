from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base, utcnow


class GameStatus(StrEnum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    FINISHED = "FINISHED"
    CANCELLED = "CANCELLED"


OPEN_STATUSES = (GameStatus.CREATED, GameStatus.ACTIVE)


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        CheckConstraint("target_score > 0", name="target_positive"),
        CheckConstraint("status IN ('CREATED','ACTIVE','FINISHED','CANCELLED')", name="status_valid"),
        Index("ix_games_status", "status"),
        Index("ix_games_created_at", "created_at"),
        Index("uq_games_single_active", "status", unique=True, sqlite_where=text("status = 'ACTIVE'")),
        Index("ix_games_night_id", "night_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(80))
    target_score: Mapped[int] = mapped_column(Integer, default=200)
    status: Mapped[str] = mapped_column(String(16), default=GameStatus.CREATED)
    # Nummer der als Nächstes einzugebenden Runde (1 = noch keine Runde gespielt).
    current_round: Mapped[int] = mapped_column(Integer, default=1)
    # Automatisches Spielende beim Erreichen des Punkteziels (aus nach manueller Fortsetzung ohne neues Ziel).
    auto_finish: Mapped[bool] = mapped_column(Boolean, default=True)
    # Warum das Spiel endete: "target" (Punkteziel) oder "manual".
    finish_reason: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Spieleabend, zu dem die Partie gehört (leer bei Einzelspielen), und ihre Nummer an diesem Abend.
    night_id: Mapped[int | None] = mapped_column(ForeignKey("game_nights.id", ondelete="CASCADE"))
    night_number: Mapped[int | None] = mapped_column(Integer)

    players: Mapped[list["GamePlayer"]] = relationship(
        back_populates="game", cascade="all, delete-orphan", order_by="GamePlayer.display_order")
    rounds: Mapped[list["Round"]] = relationship(
        back_populates="game", cascade="all, delete-orphan", order_by="Round.round_number")
    night: Mapped["GameNight | None"] = relationship(back_populates="games")

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES


from .game_night import GameNight  # noqa: E402
from .game_player import GamePlayer  # noqa: E402
from .round import Round  # noqa: E402
