CREATE TABLE IF NOT EXISTS document_section (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  section_id VARCHAR(64) NOT NULL,
  document_id INTEGER NOT NULL,
  document_version_id INTEGER NOT NULL,
  extracted_document_id INTEGER NOT NULL,
  parent_section_id VARCHAR(64),
  source_kind VARCHAR(32) NOT NULL,
  node_type VARCHAR(32) NOT NULL,
  header_text TEXT NOT NULL,
  header_text_norm TEXT NOT NULL,
  header_level INTEGER NOT NULL,
  section_path_json TEXT NOT NULL,
  body_text TEXT,
  start_offset INTEGER NOT NULL,
  end_offset INTEGER NOT NULL,
  start_page INTEGER,
  end_page INTEGER,
  ordinal INTEGER NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.0,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (document_id) REFERENCES document(id),
  FOREIGN KEY (document_version_id) REFERENCES document_version(id),
  FOREIGN KEY (extracted_document_id) REFERENCES extracted_document(id),
  CONSTRAINT uq_document_section_section_id UNIQUE (section_id)
);

CREATE INDEX IF NOT EXISTS ix_document_section_docver_id ON document_section(document_version_id);
CREATE INDEX IF NOT EXISTS ix_document_section_document_id ON document_section(document_id);
CREATE INDEX IF NOT EXISTS ix_document_section_parent_id ON document_section(parent_section_id);
CREATE INDEX IF NOT EXISTS ix_document_section_node_type ON document_section(node_type);

CREATE TABLE IF NOT EXISTS retrieval_artifact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id VARCHAR(64) NOT NULL,
  document_id INTEGER NOT NULL,
  document_version_id INTEGER NOT NULL,
  extracted_document_id INTEGER NOT NULL,
  section_id VARCHAR(64),
  source_kind VARCHAR(32) NOT NULL,
  artifact_kind VARCHAR(32) NOT NULL,
  ordinal INTEGER NOT NULL,
  title_he TEXT,
  committee_name TEXT,
  meeting_date VARCHAR(32),
  header_path_json TEXT NOT NULL,
  body_text TEXT NOT NULL,
  retrieval_text TEXT NOT NULL,
  retrieval_text_norm TEXT NOT NULL,
  start_offset INTEGER NOT NULL,
  end_offset INTEGER NOT NULL,
  start_page INTEGER,
  end_page INTEGER,
  citation_label VARCHAR(64),
  trigram_count INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (document_id) REFERENCES document(id),
  FOREIGN KEY (document_version_id) REFERENCES document_version(id),
  FOREIGN KEY (extracted_document_id) REFERENCES extracted_document(id),
  CONSTRAINT uq_retrieval_artifact_artifact_id UNIQUE (artifact_id)
);

CREATE INDEX IF NOT EXISTS ix_retrieval_artifact_docver_id ON retrieval_artifact(document_version_id);
CREATE INDEX IF NOT EXISTS ix_retrieval_artifact_document_id ON retrieval_artifact(document_id);
CREATE INDEX IF NOT EXISTS ix_retrieval_artifact_section_id ON retrieval_artifact(section_id);
CREATE INDEX IF NOT EXISTS ix_retrieval_artifact_source_kind ON retrieval_artifact(source_kind);
CREATE INDEX IF NOT EXISTS ix_retrieval_artifact_kind ON retrieval_artifact(artifact_kind);

CREATE TABLE IF NOT EXISTS retrieval_artifact_trigram (
  artifact_id VARCHAR(64) NOT NULL,
  trigram VARCHAR(16) NOT NULL,
  PRIMARY KEY (artifact_id, trigram),
  FOREIGN KEY (artifact_id) REFERENCES retrieval_artifact(artifact_id)
);

CREATE INDEX IF NOT EXISTS ix_retrieval_artifact_trigram_trigram ON retrieval_artifact_trigram(trigram);

CREATE TABLE IF NOT EXISTS retrieval_artifact_embedding (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id VARCHAR(64) NOT NULL,
  model_provider VARCHAR(64) NOT NULL,
  model_name VARCHAR(128) NOT NULL,
  dimensions INTEGER NOT NULL,
  embedding_json TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(artifact_id, model_provider, model_name, dimensions),
  FOREIGN KEY (artifact_id) REFERENCES retrieval_artifact(artifact_id)
);

CREATE INDEX IF NOT EXISTS ix_retrieval_artifact_embedding_artifact_id
ON retrieval_artifact_embedding(artifact_id, model_provider, model_name, dimensions);

CREATE VIRTUAL TABLE IF NOT EXISTS artifact_fts USING fts5(
  artifact_id UNINDEXED,
  retrieval_text,
  tokenize='unicode61'
);
