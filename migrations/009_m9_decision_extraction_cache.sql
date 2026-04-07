CREATE TABLE IF NOT EXISTS decision_extraction_cache (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_version_id INTEGER NOT NULL,
  cache_key VARCHAR(64) NOT NULL,
  strategy VARCHAR(32) NOT NULL,
  selected_mode VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL,
  candidate_count INTEGER NOT NULL DEFAULT 0,
  quality_score REAL,
  quality_reasons_json TEXT,
  candidates_json TEXT,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(document_version_id, cache_key),
  FOREIGN KEY(document_version_id) REFERENCES document_version(id)
);

CREATE INDEX IF NOT EXISTS ix_decision_extraction_cache_docver
ON decision_extraction_cache(document_version_id);

CREATE INDEX IF NOT EXISTS ix_decision_extraction_cache_status
ON decision_extraction_cache(status);
