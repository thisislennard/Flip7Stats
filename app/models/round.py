from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base, utcnow


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (
        UniqueConstraint("game_id", "round_number"),
        CheckConstraint("round_number > 0", name="number_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    round_number: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    game: Mapped["Game"] = relationship(back_populates="rounds")
    scores: Mapped[list["RoundScore"]] = relationship(back_populates="round", cascade="all, delete-orphan",
                                                      lazy="selectin")


from .game import Game  # noqa: E402
from .round_score import RoundScore  # noqa: E402
