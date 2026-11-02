import sqlite3

import pytest
from conftest import save_round, start_game

from app import database
from app.services.backup_service import BackupError


def test_manual_backup_is_consistent_and_valid(admin, ctx):
    gid, (a, b, c) = start_game(admin)
    save_round(admin, gid, 1, {a: 10, b: 20, c: 30})
    r = admin.post("/api/v1/system/backups")
    assert r.status_code == 201
    name = r.json["data"]["name"]
    check = ctx.backup.validate(name)
    assert check["ok"] and check["games"] == 1
    conn = sqlite3.connect(ctx.paths.backups / name)
    assert conn.execute("SELECT COUNT(*) FROM round_scores").fetchone()[0] == 3  # WAL-Inhalt enthalten
    conn.close()


def test_restore_brings_back_old_state(admin, ctx, viewer):
    gid, (a, b, c) = start_game(admin)
    save_round(admin, gid, 1, {a: 10, b: 20, c: 30})
    name = admin.post("/api/v1/system/backups").json["data"]["name"]
    save_round(admin, gid, 2, {a: 99, b: 0, c: 0})
    r = admin.post("/api/v1/system/backups/restore", {"name": name})
    assert r.status_code == 200 and "-prerestore" in r.json["data"]["safety_backup"]
    state = viewer.get(f"/api/v1/games/{gid}").json["data"]
    assert state["game"]["current_round"] == 2 and state["leaderboard"][0]["total"] == 30


def test_invalid_backup_files_are_rejected(admin, ctx):
    (ctx.paths.backups / "kaputt.db").write_bytes(b"das ist keine datenbank")
    r = admin.post("/api/v1/system/backups/restore", {"name": "kaputt.db"})
    assert r.status_code == 422 and r.json["error"]["code"] == "backup_invalid"
    other = sqlite3.connect(ctx.paths.backups / "fremd.db")
    other.execute("CREATE TABLE x (a)")
    other.commit()
    other.close()
    assert admin.post("/api/v1/system/backups/restore", {"name": "fremd.db"}).status_code == 422


def test_path_traversal_is_rejected(admin, ctx):
    assert admin.post("/api/v1/system/backups/restore", {"name": "../data/flip7live.db"}).status_code == 422
    with pytest.raises(BackupError):
        ctx.backup.validate("..\\data\\flip7live.db")


def test_old_backups_are_pruned(ctx):
    ctx.settings.update(backup_keep=3)
    for _ in range(5):
        ctx.backup.create("manual")
    assert len(ctx.backup.list()) == 3


def test_automatic_backup_on_game_end(admin, ctx):
    gid, (a, b, c) = start_game(admin, target=10)
    save_round(admin, gid, 1, {a: 20, b: 0, c: 0})
    import time
    for _ in range(50):
        if any(b_["reason"] == "finish" for b_ in ctx.backup.list()):
            break
        time.sleep(0.05)
    assert any(b_["reason"] == "finish" for b_ in ctx.backup.list())


def test_backup_from_newer_version_is_rejected(ctx):
    name = ctx.backup.create("manual")["name"]
    conn = sqlite3.connect(ctx.paths.backups / name)
    conn.execute("UPDATE alembic_version SET version_num = '9999'")
    conn.commit()
    conn.close()
    assert not ctx.backup.validate(name)["ok"]


def test_failed_migration_restores_previous_state(ctx, monkeypatch):
    import launcher
    gid_before = ctx.games.list_games()
    calls = []

    def broken_upgrade(engine, before=None):
        before()  # Sicherung wie bei einer echten Migration
        calls.append(True)
        raise RuntimeError("Migration kaputt")

    monkeypatch.setattr(database, "upgrade", broken_upgrade)
    with pytest.raises(database.MigrationError, match="wiederhergestellt"):
        launcher._migrate(ctx)
    assert calls and any(b["reason"] == "migration" for b in ctx.backup.list())
    assert ctx.games.list_games() == gid_before


def test_migrations_match_models(ctx):
    """Das Datenbankschema aus den Migrationen entspricht exakt den Modellen."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app.models import Base
    with ctx.db.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
    assert database.current_revision(ctx.db.engine) == database.head_revision(ctx.db.engine)
