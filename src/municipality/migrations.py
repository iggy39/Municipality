from __future__ import annotations

from pathlib import Path
from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_sql_migration(engine: Engine, migration_path: Path) -> None:
    sql = migration_path.read_text(encoding="utf-8")
    statements = [part.strip() for part in sql.split(";") if part.strip()]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def apply_all(engine: Engine, migrations_dir: Path) -> None:
    for path in sorted(migrations_dir.glob("*.sql")):
        apply_sql_migration(engine, path)
