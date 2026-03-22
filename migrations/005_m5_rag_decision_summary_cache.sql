CREATE TABLE IF NOT EXISTS rag_decision_summary_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_hash TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    protocol_title TEXT NOT NULL,
    topic_name TEXT NOT NULL,
    summary_he TEXT NOT NULL,
    model_provider TEXT,
    model_name TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(question_hash, chunk_id, summary_he)
);

CREATE INDEX IF NOT EXISTS ix_rag_decision_summary_cache_question_hash
ON rag_decision_summary_cache(question_hash);

CREATE INDEX IF NOT EXISTS ix_rag_decision_summary_cache_chunk_id
ON rag_decision_summary_cache(chunk_id);
