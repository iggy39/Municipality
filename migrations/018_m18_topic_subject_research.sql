CREATE TABLE IF NOT EXISTS topic_subject_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  municipality_slug VARCHAR(64) NOT NULL,
  model_provider VARCHAR(64) NOT NULL,
  model_name VARCHAR(128) NOT NULL,
  status VARCHAR(32) NOT NULL,
  write_mode INTEGER NOT NULL DEFAULT 0,
  source_artifact_count INTEGER NOT NULL DEFAULT 0,
  extraction_count INTEGER NOT NULL DEFAULT 0,
  candidate_subject_count INTEGER NOT NULL DEFAULT 0,
  candidate_decision_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT,
  error_text TEXT,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topic_subject (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  municipality_slug VARCHAR(64) NOT NULL,
  artifact_id VARCHAR(64) NOT NULL,
  semantic_node_id INTEGER NOT NULL,
  subject_index INTEGER NOT NULL DEFAULT 0,
  root_topic_id VARCHAR(128),
  child_topic_id VARCHAR(128),
  topic_label_he TEXT NOT NULL,
  source_kind VARCHAR(32) NOT NULL,
  source_document_id INTEGER NOT NULL,
  source_document_version_id INTEGER NOT NULL,
  source_ordinal INTEGER,
  source_page_start INTEGER,
  source_page_end INTEGER,
  source_title TEXT,
  subject_root_label_he TEXT NOT NULL,
  subject_root_label_norm TEXT NOT NULL,
  subject_child_label_he TEXT NOT NULL,
  subject_child_label_norm TEXT NOT NULL,
  subject_summary_he TEXT,
  what_text_is_about_he TEXT,
  subject_status VARCHAR(32) NOT NULL DEFAULT 'candidate',
  is_decision INTEGER NOT NULL DEFAULT 0,
  decision_label_he TEXT,
  decision_label_norm TEXT,
  decision_summary_he TEXT,
  decision_source_quote_he TEXT,
  confidence REAL NOT NULL DEFAULT 0,
  validation_status VARCHAR(32) NOT NULL,
  failure_reason TEXT,
  evidence_refs_json TEXT,
  resident_evidence_links_json TEXT,
  neighbor_contexts_json TEXT,
  dicta_payload_json TEXT,
  judge_payload_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (run_id) REFERENCES topic_subject_run(id),
  FOREIGN KEY (artifact_id) REFERENCES retrieval_artifact(artifact_id),
  FOREIGN KEY (semantic_node_id) REFERENCES semantic_node(id),
  FOREIGN KEY (source_document_id) REFERENCES document(id),
  FOREIGN KEY (source_document_version_id) REFERENCES document_version(id),
  CONSTRAINT uq_topic_subject_run_artifact_index UNIQUE (run_id, artifact_id, semantic_node_id, subject_index)
);

CREATE TABLE IF NOT EXISTS topic_subject_quality_report (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  artifact_id VARCHAR(64) NOT NULL,
  semantic_node_id INTEGER NOT NULL,
  topic_label_he TEXT NOT NULL,
  real_text TEXT NOT NULL,
  what_text_is_about_he TEXT,
  subject_root_by_dicta TEXT,
  subject_child_by_dicta TEXT,
  decision_by_dicta TEXT,
  my_judgment TEXT NOT NULL,
  ground_truth TEXT NOT NULL,
  reason_for_failure TEXT,
  status VARCHAR(32) NOT NULL,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (run_id) REFERENCES topic_subject_run(id),
  FOREIGN KEY (artifact_id) REFERENCES retrieval_artifact(artifact_id),
  FOREIGN KEY (semantic_node_id) REFERENCES semantic_node(id)
);

CREATE INDEX IF NOT EXISTS ix_topic_subject_run_municipality
ON topic_subject_run(municipality_slug, started_at);

CREATE INDEX IF NOT EXISTS ix_topic_subject_artifact
ON topic_subject(artifact_id);

CREATE INDEX IF NOT EXISTS ix_topic_subject_semantic_node
ON topic_subject(semantic_node_id);

CREATE INDEX IF NOT EXISTS ix_topic_subject_labels
ON topic_subject(subject_root_label_norm, subject_child_label_norm);

CREATE INDEX IF NOT EXISTS ix_topic_subject_decision
ON topic_subject(is_decision, decision_label_norm);

CREATE INDEX IF NOT EXISTS ix_topic_subject_quality_run
ON topic_subject_quality_report(run_id);
