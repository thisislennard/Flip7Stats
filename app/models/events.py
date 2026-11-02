"""Nachvollziehbarkeit (game_events) und Schutz vor Doppelspeicherung (idempotency_requests)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base, utcnow


class GameEvent(Base):
    """Protokoll aller Änderungen an einem Spiel, inkl. alter und neuer Werte bei Korrekturen."""

    __tablename__ = "game_events"
    __table_args__ = (Index("ix_game_events_game_id", "game_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[str] = mapped_column(Text, default="{}")
    actor: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IdempotencyRequest(Base):
    """Bereits verarbeitete Schreibanfrage: dieselbe Anfrage erneut gesendet → gespeicherte Antwort statt Doppelrunde."""

    __tablename__ = "idempotency_requests"
    __table_args__ = (Index("ix_idempotency_requests_created_at", "created_at"),)

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(80))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
