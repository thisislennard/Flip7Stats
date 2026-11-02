from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base


class GamePlayer(Base):
    __tablename__ = "game_players"
    __table_args__ = (
        UniqueConstraint("game_id", "player_id"),
        Index("ix_game_players_player_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="RESTRICT"))
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    game: Mapped["Game"] = relationship(back_populates="players")
    player: Mapped["Player"] = relationship(lazy="joined")


from .game import Game  # noqa: E402
from .player import Player  # noqa: E402
