CREATE TABLE IF NOT EXISTS semantic_document_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_version_id INTEGER NOT NULL,
  prompt_hash VARCHAR(64) NOT NULL,
  model_provider VARCHAR(64) NOT NULL,
  model_name VARCHAR(128) NOT NULL,
  status VARCHAR(32) NOT NULL,
  api_call_count INTEGER NOT NULL DEFAULT 0,
  request_tokens INTEGER,
  response_tokens INTEGER,
  error_code VARCHAR(64),
  error_text TEXT,
  extraction_payload_json TEXT,
  validation_report_json TEXT,
  canonicalization_report_json TEXT,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (document_version_id) REFERENCES document_version(id),
  CONSTRAINT uq_semantic_document_run_call_key UNIQUE (document_version_id, prompt_hash, model_provider, model_name)
);

CREATE TABLE IF NOT EXISTS semantic_node (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_site_id INTEGER NOT NULL,
  node_key_hash VARCHAR(40) NOT NULL,
  node_kind VARCHAR(16) NOT NULL,
  semantic_type VARCHAR(64) NOT NULL,
  pref_label_he TEXT NOT NULL,
  pref_label_norm VARCHAR(255) NOT NULL,
  parent_node_id INTEGER,
  depth INTEGER NOT NULL,
  specificity_score REAL NOT NULL,
  confidence REAL NOT NULL,
  support_count INTEGER NOT NULL DEFAULT 0,
  status VARCHAR(16) NOT NULL,
  first_seen_document_version_id INTEGER,
  last_seen_document_version_id INTEGER,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (source_site_id) REFERENCES source_site(id),
  FOREIGN KEY (parent_node_id) REFERENCES semantic_node(id),
  FOREIGN KEY (first_seen_document_version_id) REFERENCES document_version(id),
  FOREIGN KEY (last_seen_document_version_id) REFERENCES document_version(id),
  CONSTRAINT uq_semantic_node_site_hash UNIQUE (source_site_id, node_key_hash),
  CONSTRAINT uq_semantic_node_hierarchy_label UNIQUE (source_site_id, parent_node_id, node_kind, semantic_type, pref_label_norm)
);

CREATE TABLE IF NOT EXISTS semantic_alias (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  semantic_node_id INTEGER NOT NULL,
  alias_hash VARCHAR(40) NOT NULL,
  alias_label_he TEXT NOT NULL,
  alias_label_norm VARCHAR(255) NOT NULL,
  alias_kind VARCHAR(24) NOT NULL,
  confidence REAL NOT NULL,
  first_seen_document_version_id INTEGER,
  last_seen_document_version_id INTEGER,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (semantic_node_id) REFERENCES semantic_node(id),
  FOREIGN KEY (first_seen_document_version_id) REFERENCES document_version(id),
  FOREIGN KEY (last_seen_document_version_id) REFERENCES document_version(id),
  CONSTRAINT uq_semantic_alias_node_hash UNIQUE (semantic_node_id, alias_hash)
);

CREATE TABLE IF NOT EXISTS semantic_edge (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_node_id INTEGER NOT NULL,
  target_node_id INTEGER NOT NULL,
  relation_type VARCHAR(24) NOT NULL,
  confidence REAL NOT NULL,
  provenance VARCHAR(24) NOT NULL,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (source_node_id) REFERENCES semantic_node(id),
  FOREIGN KEY (target_node_id) REFERENCES semantic_node(id),
  CONSTRAINT uq_semantic_edge_triplet UNIQUE (source_node_id, target_node_id, relation_type)
);

CREATE TABLE IF NOT EXISTS semantic_mention (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  semantic_node_id INTEGER NOT NULL,
  document_id INTEGER NOT NULL,
  document_version_id INTEGER NOT NULL,
  source_kind VARCHAR(32) NOT NULL,
  start_offset INTEGER NOT NULL,
  end_offset INTEGER NOT NULL,
  start_page INTEGER,
  end_page INTEGER,
  mention_text TEXT NOT NULL,
  mention_text_norm TEXT NOT NULL,
  mention_confidence REAL NOT NULL,
  evidence_hash VARCHAR(40) NOT NULL,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (semantic_node_id) REFERENCES semantic_node(id),
  FOREIGN KEY (document_id) REFERENCES document(id),
  FOREIGN KEY (document_version_id) REFERENCES document_version(id),
  CONSTRAINT uq_semantic_mention_doc_node_span UNIQUE (document_version_id, semantic_node_id, start_offset, end_offset)
);

CREATE TABLE IF NOT EXISTS decision_semantic_link (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id INTEGER NOT NULL,
  semantic_node_id INTEGER NOT NULL,
  relation_role VARCHAR(32) NOT NULL,
  confidence REAL NOT NULL,
  source_mention_id INTEGER,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (decision_id) REFERENCES decision(id),
  FOREIGN KEY (semantic_node_id) REFERENCES semantic_node(id),
  FOREIGN KEY (source_mention_id) REFERENCES semantic_mention(id),
  CONSTRAINT uq_decision_semantic_link_triplet UNIQUE (decision_id, semantic_node_id, relation_role)
);

CREATE TABLE IF NOT EXISTS chunk_semantic_link (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chunk_id VARCHAR(64) NOT NULL,
  semantic_node_id INTEGER NOT NULL,
  confidence REAL NOT NULL,
  source_mention_id INTEGER,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (chunk_id) REFERENCES text_chunk(chunk_id),
  FOREIGN KEY (semantic_node_id) REFERENCES semantic_node(id),
  FOREIGN KEY (source_mention_id) REFERENCES semantic_mention(id),
  CONSTRAINT uq_chunk_semantic_link_pair UNIQUE (chunk_id, semantic_node_id)
);

CREATE TABLE IF NOT EXISTS semantic_candidate_reject (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  semantic_document_run_id INTEGER NOT NULL,
  candidate_label_he TEXT,
  candidate_label_norm VARCHAR(255),
  node_kind VARCHAR(16),
  semantic_type VARCHAR(64),
  reason_code VARCHAR(64) NOT NULL,
  model_confidence REAL,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (semantic_document_run_id) REFERENCES semantic_document_run(id)
);

CREATE INDEX IF NOT EXISTS ix_semantic_document_run_status ON semantic_document_run(status);

CREATE INDEX IF NOT EXISTS ix_semantic_node_parent_node_id ON semantic_node(parent_node_id);
CREATE INDEX IF NOT EXISTS ix_semantic_node_node_kind ON semantic_node(node_kind);
CREATE INDEX IF NOT EXISTS ix_semantic_node_semantic_type ON semantic_node(semantic_type);
CREATE INDEX IF NOT EXISTS ix_semantic_node_depth ON semantic_node(depth);
CREATE INDEX IF NOT EXISTS ix_semantic_node_status ON semantic_node(status);
CREATE INDEX IF NOT EXISTS ix_semantic_node_pref_label_norm ON semantic_node(pref_label_norm);

CREATE INDEX IF NOT EXISTS ix_semantic_alias_label_norm ON semantic_alias(alias_label_norm);
CREATE INDEX IF NOT EXISTS ix_semantic_alias_kind ON semantic_alias(alias_kind);

CREATE INDEX IF NOT EXISTS ix_semantic_edge_source_node_id ON semantic_edge(source_node_id);
CREATE INDEX IF NOT EXISTS ix_semantic_edge_target_node_id ON semantic_edge(target_node_id);
CREATE INDEX IF NOT EXISTS ix_semantic_edge_relation_type ON semantic_edge(relation_type);

CREATE INDEX IF NOT EXISTS ix_semantic_mention_document_id ON semantic_mention(document_id);
CREATE INDEX IF NOT EXISTS ix_semantic_mention_semantic_node_id ON semantic_mention(semantic_node_id);
CREATE INDEX IF NOT EXISTS ix_semantic_mention_evidence_hash ON semantic_mention(evidence_hash);

CREATE INDEX IF NOT EXISTS ix_decision_semantic_link_decision_id ON decision_semantic_link(decision_id);
CREATE INDEX IF NOT EXISTS ix_decision_semantic_link_semantic_node_id ON decision_semantic_link(semantic_node_id);
CREATE INDEX IF NOT EXISTS ix_decision_semantic_link_relation_role ON decision_semantic_link(relation_role);

CREATE INDEX IF NOT EXISTS ix_chunk_semantic_link_chunk_id ON chunk_semantic_link(chunk_id);
CREATE INDEX IF NOT EXISTS ix_chunk_semantic_link_semantic_node_id ON chunk_semantic_link(semantic_node_id);

CREATE INDEX IF NOT EXISTS ix_semantic_candidate_reject_run_id ON semantic_candidate_reject(semantic_document_run_id);
CREATE INDEX IF NOT EXISTS ix_semantic_candidate_reject_reason_code ON semantic_candidate_reject(reason_code);
