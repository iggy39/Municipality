CREATE TABLE IF NOT EXISTS query_embedding_cache (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text_hash VARCHAR(64) NOT NULL,
  normalized_text TEXT NOT NULL,
  query_kind VARCHAR(32) NOT NULL,
  model_provider VARCHAR(64) NOT NULL,
  model_name VARCHAR(128) NOT NULL,
  dimensions INTEGER NOT NULL,
  embedding_json TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(text_hash, query_kind, model_provider, model_name, dimensions)
);

CREATE INDEX IF NOT EXISTS ix_query_embedding_cache_lookup
ON query_embedding_cache(text_hash, query_kind, model_provider, model_name, dimensions);

CREATE TABLE IF NOT EXISTS decision_embedding (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id INTEGER NOT NULL,
  model_provider VARCHAR(64) NOT NULL,
  model_name VARCHAR(128) NOT NULL,
  dimensions INTEGER NOT NULL,
  embedding_json TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(decision_id, model_provider, model_name, dimensions),
  FOREIGN KEY(decision_id) REFERENCES decision(id)
);

CREATE INDEX IF NOT EXISTS ix_decision_embedding_lookup
ON decision_embedding(decision_id, model_provider, model_name, dimensions);

CREATE TABLE IF NOT EXISTS rag_answer_cache (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  retrieval_set_id VARCHAR(96) NOT NULL,
  query_hash VARCHAR(64) NOT NULL,
  normalized_query TEXT NOT NULL,
  embedding_provider VARCHAR(64) NOT NULL,
  embedding_model VARCHAR(128) NOT NULL,
  embedding_dimensions INTEGER NOT NULL,
  query_embedding_json TEXT NOT NULL,
  answer_provider VARCHAR(64),
  answer_model VARCHAR(128),
  answer_payload_json TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_rag_answer_cache_retrieval
ON rag_answer_cache(retrieval_set_id, embedding_provider, embedding_model, embedding_dimensions);
