"""Alembic-Umgebung: nutzt die Engine der laufenden Anwendung (config.attributes["engine"])."""

from alembic import context

from app.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    engine = config.attributes["engine"]
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite kann ALTER TABLE nur eingeschränkt
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
