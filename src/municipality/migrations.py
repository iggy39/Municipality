from __future__ import annotations

from datetime import datetime
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_sql_migration(engine: Engine, migration_path: Path) -> None:
    sql = migration_path.read_text(encoding="utf-8")
    statements = [part.strip() for part in sql.split(";") if part.strip()]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        connection.execute(
            text(
                "INSERT INTO _schema_migration (filename, applied_at) VALUES (:filename, :applied_at)"
            ),
            {"filename": migration_path.name, "applied_at": datetime.utcnow().isoformat()},
        )


def apply_all(engine: Engine, migrations_dir: Path) -> None:
    migration_paths = sorted(migrations_dir.glob("*.sql"))
    _ensure_schema_migration_table(engine)
    applied = _applied_migration_names(engine)
    if not applied and _database_has_user_tables(engine):
        _mark_all_migrations_applied(engine, migration_paths)
        return

    for path in migration_paths:
        if path.name in applied:
            continue
        apply_sql_migration(engine, path)
        applied.add(path.name)


def _ensure_schema_migration_table(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS _schema_migration ("
                "filename TEXT PRIMARY KEY, "
                "applied_at TEXT NOT NULL"
                ")"
            )
        )


def _applied_migration_names(engine: Engine) -> set[str]:
    with engine.begin() as connection:
        rows = connection.execute(text("SELECT filename FROM _schema_migration")).all()
    return {str(row[0]) for row in rows if row and str(row[0]).strip()}


def _database_has_user_tables(engine: Engine) -> bool:
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != '_schema_migration'"
            )
        ).all()
    return bool(rows)


def _mark_all_migrations_applied(engine: Engine, migration_paths: list[Path]) -> None:
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM _schema_migration"))
        connection.execute(
            text("INSERT INTO _schema_migration (filename, applied_at) VALUES (:filename, :applied_at)"),
            [
                {"filename": path.name, "applied_at": datetime.utcnow().isoformat()}
                for path in migration_paths
            ],
        )
