DROP TABLE IF EXISTS decision_embedding;
DROP TABLE IF EXISTS chunk_embedding;
DROP TABLE IF EXISTS chunk_semantic_link;
DROP TABLE IF EXISTS chunk_trigram;
DROP TABLE IF EXISTS chunk_fts;
DROP TABLE IF EXISTS text_chunk;
DROP TABLE IF EXISTS decision_request_context_legacy;
DROP TABLE IF EXISTS decision_request_context;

CREATE TABLE IF NOT EXISTS decision_request_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL,
    source_document_id INTEGER NOT NULL,
    request_subject_he TEXT,
    subject_topic_he TEXT,
    address_he TEXT,
    gush TEXT,
    helka TEXT,
    migrash TEXT,
    source_artifact_ids_json TEXT,
    confidence REAL,
    metadata_json TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(decision_id),
    FOREIGN KEY(decision_id) REFERENCES decision(id),
    FOREIGN KEY(source_document_id) REFERENCES document(id)
);

CREATE INDEX IF NOT EXISTS ix_decision_request_context_source_document_id
ON decision_request_context(source_document_id);

CREATE INDEX IF NOT EXISTS ix_decision_request_context_subject_topic_he
ON decision_request_context(subject_topic_he);
