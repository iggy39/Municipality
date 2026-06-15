CREATE TABLE IF NOT EXISTS topic_decision_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  municipality_slug VARCHAR(64) NOT NULL,
  model_provider VARCHAR(64) NOT NULL,
  model_name VARCHAR(128) NOT NULL,
  status VARCHAR(32) NOT NULL,
  write_mode INTEGER NOT NULL DEFAULT 0,
  source_artifact_count INTEGER NOT NULL DEFAULT 0,
  extraction_count INTEGER NOT NULL DEFAULT 0,
  accepted_decision_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT,
  error_text TEXT,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topic_decision (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  municipality_slug VARCHAR(64) NOT NULL,
  artifact_id VARCHAR(64) NOT NULL,
  semantic_node_id INTEGER NOT NULL,
  decision_index INTEGER NOT NULL DEFAULT 0,
  root_topic_id VARCHAR(128),
  child_topic_id VARCHAR(128),
  topic_label_he TEXT NOT NULL,
  source_kind VARCHAR(32) NOT NULL,
  source_document_id INTEGER NOT NULL,
  source_document_version_id INTEGER NOT NULL,
  source_page_start INTEGER,
  source_page_end INTEGER,
  source_title TEXT,
  decision_title_he TEXT,
  decision_text_he TEXT,
  decision_summary_he TEXT,
  decision_kind_code VARCHAR(64) NOT NULL,
  decision_kind_label_he TEXT NOT NULL,
  outcome_status_code VARCHAR(64) NOT NULL,
  outcome_status_label_he TEXT NOT NULL,
  legal_effect_code VARCHAR(64) NOT NULL,
  legal_effect_label_he TEXT NOT NULL,
  primary_time_kind VARCHAR(64),
  primary_time_start VARCHAR(32),
  primary_time_end VARCHAR(32),
  primary_time_precision VARCHAR(32),
  primary_time_label_he TEXT,
  confidence REAL NOT NULL DEFAULT 0,
  validation_status VARCHAR(32) NOT NULL,
  failure_reason TEXT,
  source_quote_he TEXT,
  time_anchors_json TEXT,
  evidence_refs_json TEXT,
  resident_evidence_links_json TEXT,
  limitations_json TEXT,
  dicta_payload_json TEXT,
  judge_payload_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (run_id) REFERENCES topic_decision_run(id),
  FOREIGN KEY (artifact_id) REFERENCES retrieval_artifact(artifact_id),
  FOREIGN KEY (semantic_node_id) REFERENCES semantic_node(id),
  FOREIGN KEY (source_document_id) REFERENCES document(id),
  FOREIGN KEY (source_document_version_id) REFERENCES document_version(id),
  CONSTRAINT uq_topic_decision_run_artifact_index UNIQUE (run_id, artifact_id, semantic_node_id, decision_index)
);

CREATE TABLE IF NOT EXISTS topic_decision_quality_report (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  artifact_id VARCHAR(64) NOT NULL,
  semantic_node_id INTEGER NOT NULL,
  topic_label_he TEXT NOT NULL,
  real_text TEXT NOT NULL,
  decision_by_dicta TEXT,
  decision_ground_truth TEXT NOT NULL,
  reason_for_failure TEXT,
  status VARCHAR(32) NOT NULL,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (run_id) REFERENCES topic_decision_run(id),
  FOREIGN KEY (artifact_id) REFERENCES retrieval_artifact(artifact_id),
  FOREIGN KEY (semantic_node_id) REFERENCES semantic_node(id)
);

CREATE INDEX IF NOT EXISTS ix_topic_decision_run_municipality
ON topic_decision_run(municipality_slug, started_at);

CREATE INDEX IF NOT EXISTS ix_topic_decision_artifact
ON topic_decision(artifact_id);

CREATE INDEX IF NOT EXISTS ix_topic_decision_semantic_node
ON topic_decision(semantic_node_id);

CREATE INDEX IF NOT EXISTS ix_topic_decision_codelists
ON topic_decision(decision_kind_code, outcome_status_code, legal_effect_code);

CREATE INDEX IF NOT EXISTS ix_topic_decision_quality_run
ON topic_decision_quality_report(run_id);
