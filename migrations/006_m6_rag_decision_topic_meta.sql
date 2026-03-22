CREATE TABLE IF NOT EXISTS rag_decision_summary_topic_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_hash TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    summary_he TEXT NOT NULL,
    topic_granularity_level INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(question_hash, chunk_id, summary_he)
);

CREATE INDEX IF NOT EXISTS ix_rag_decision_summary_topic_meta_question_hash
ON rag_decision_summary_topic_meta(question_hash);

CREATE INDEX IF NOT EXISTS ix_rag_decision_summary_topic_meta_chunk_id
ON rag_decision_summary_topic_meta(chunk_id);
