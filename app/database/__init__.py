"""Datenbankmigrationen (Alembic), beim Start automatisch ausgeführt – ohne alembic.ini und Kommandozeile."""

from __future__ import annotations

import logging
from typing import Callable

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

from ..config import resource_path

log = logging.getLogger(__name__)


class MigrationError(RuntimeError):
    """Migration fehlgeschlagen; die Datenbank wurde aus der Sicherung wiederhergestellt (falls vorhanden)."""


def _config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(resource_path("app", "database", "migrations")))
    cfg.attributes["engine"] = engine
    return cfg


def head_revision(engine: Engine) -> str:
    return ScriptDirectory.from_config(_config(engine)).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def known_revisions(engine: Engine) -> set[str]:
    return {s.revision for s in ScriptDirectory.from_config(_config(engine)).walk_revisions()}


def needs_upgrade(engine: Engine) -> bool:
    return current_revision(engine) != head_revision(engine)


def upgrade(engine: Engine, before: Callable[[], object] | None = None) -> bool:
    """Bringt die Datenbank auf den neuesten Stand. ``before`` (z. B. Sicherung) läuft nur, wenn wirklich migriert
    wird und bereits Daten existieren. Gibt zurück, ob migriert wurde."""
    current = current_revision(engine)
    head = head_revision(engine)
    if current == head:
        return False
    if current is not None and current not in known_revisions(engine):
        raise MigrationError(
            f"Die Datenbank stammt von einer neueren Flip-7-Live-Version (Stand {current}). "
            "Bitte die neuere Version verwenden.")
    if before is not None and current is not None:
        before()
    log.info("Datenbankmigration %s → %s", current or "leer", head)
    command.upgrade(_config(engine), "head")
    return True
