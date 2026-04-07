CREATE TABLE IF NOT EXISTS chunk_embedding (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chunk_id VARCHAR(64) NOT NULL,
  model_provider VARCHAR(64) NOT NULL,
  model_name VARCHAR(128) NOT NULL,
  dimensions INTEGER NOT NULL,
  embedding_json TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (chunk_id) REFERENCES text_chunk(chunk_id),
  CONSTRAINT uq_chunk_embedding_model UNIQUE (chunk_id, model_provider, model_name, dimensions)
);

CREATE INDEX IF NOT EXISTS ix_chunk_embedding_chunk_id ON chunk_embedding(chunk_id);
CREATE INDEX IF NOT EXISTS ix_chunk_embedding_model ON chunk_embedding(model_provider, model_name, dimensions);
