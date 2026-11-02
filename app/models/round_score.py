from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base


class RoundScore(Base):
    __tablename__ = "round_scores"
    __table_args__ = (
        UniqueConstraint("round_id", "player_id"),
        CheckConstraint("points >= 0", name="points_not_negative"),
        Index("ix_round_scores_player_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"))
    points: Mapped[int] = mapped_column(Integer)

    round: Mapped["Round"] = relationship(back_populates="scores")


from .round import Round  # noqa: E402
