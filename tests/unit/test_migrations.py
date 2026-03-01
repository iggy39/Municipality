from pathlib import Path

from sqlalchemy import create_engine, text

from municipality.migrations import apply_all


def test_migrations_apply_and_reapply_cleanly(tmp_path: Path) -> None:
    db_path = tmp_path / "m1.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)

    migrations_dir = Path("migrations")
    apply_all(engine, migrations_dir)
    apply_all(engine, migrations_dir)

    with engine.connect() as conn:
        row = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='document_version'"))
        assert row.first() is not None
        extracted = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='extracted_document'"))
        assert extracted.first() is not None
        chunk = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='text_chunk'"))
        assert chunk.first() is not None
