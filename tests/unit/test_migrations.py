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
        meeting = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='meeting'"))
        assert meeting.first() is not None
        decision = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='decision'"))
        assert decision.first() is not None
        citation = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='decision_citation'"))
        assert citation.first() is not None
        semantic_run = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='semantic_document_run'")
        )
        assert semantic_run.first() is not None
        semantic_node = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='semantic_node'"))
        assert semantic_node.first() is not None
        summary_topic_meta = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='rag_decision_summary_topic_meta'")
        )
        assert summary_topic_meta.first() is not None
        decision_request_context = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='decision_request_context'")
        )
        assert decision_request_context.first() is not None
        chunk_embedding = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_embedding'"))
        assert chunk_embedding.first() is not None
        decision_extraction_cache = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='decision_extraction_cache'")
        )
        assert decision_extraction_cache.first() is not None
        query_embedding_cache = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='query_embedding_cache'")
        )
        assert query_embedding_cache.first() is not None
        decision_embedding = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='decision_embedding'")
        )
        assert decision_embedding.first() is not None
        rag_answer_cache = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='rag_answer_cache'")
        )
        assert rag_answer_cache.first() is not None
