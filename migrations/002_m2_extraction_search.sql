CREATE TABLE IF NOT EXISTS extracted_document (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_version_id INTEGER NOT NULL,
  parser_name VARCHAR(64) NOT NULL,
  parser_version VARCHAR(32),
  status VARCHAR(32) NOT NULL,
  extracted_text TEXT,
  page_count INTEGER NOT NULL DEFAULT 0,
  pages_json TEXT,
  citation_map_json TEXT,
  quality_score REAL,
  quality_flags_json TEXT,
  quality_summary_json TEXT,
  error_code VARCHAR(64),
  warning_text TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (document_version_id) REFERENCES document_version(id),
  CONSTRAINT uq_extracted_document_docver UNIQUE (document_version_id)
);

CREATE TABLE IF NOT EXISTS text_chunk (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chunk_id VARCHAR(64) NOT NULL,
  document_id INTEGER NOT NULL,
  document_version_id INTEGER NOT NULL,
  extracted_document_id INTEGER NOT NULL,
  source_kind VARCHAR(32) NOT NULL,
  chunk_index INTEGER NOT NULL,
  chunk_text TEXT NOT NULL,
  chunk_text_norm TEXT NOT NULL,
  start_offset INTEGER NOT NULL,
  end_offset INTEGER NOT NULL,
  start_page INTEGER,
  end_page INTEGER,
  citation_label VARCHAR(64),
  trigram_count INTEGER NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (document_id) REFERENCES document(id),
  FOREIGN KEY (document_version_id) REFERENCES document_version(id),
  FOREIGN KEY (extracted_document_id) REFERENCES extracted_document(id),
  CONSTRAINT uq_text_chunk_chunk_id UNIQUE (chunk_id)
);

CREATE TABLE IF NOT EXISTS chunk_trigram (
  chunk_id VARCHAR(64) NOT NULL,
  trigram VARCHAR(16) NOT NULL,
  PRIMARY KEY (chunk_id, trigram),
  FOREIGN KEY (chunk_id) REFERENCES text_chunk(chunk_id)
);

CREATE INDEX IF NOT EXISTS ix_extracted_document_docver_id ON extracted_document(document_version_id);
CREATE INDEX IF NOT EXISTS ix_extracted_document_status ON extracted_document(status);
CREATE INDEX IF NOT EXISTS ix_text_chunk_docver_id ON text_chunk(document_version_id);
CREATE INDEX IF NOT EXISTS ix_text_chunk_document_id ON text_chunk(document_id);
CREATE INDEX IF NOT EXISTS ix_text_chunk_source_kind ON text_chunk(source_kind);
CREATE INDEX IF NOT EXISTS ix_chunk_trigram_trigram ON chunk_trigram(trigram);

CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
  chunk_id UNINDEXED,
  chunk_text,
  tokenize='unicode61'
);
