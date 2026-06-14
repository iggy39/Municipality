CREATE TABLE IF NOT EXISTS decision_gis_feature_link (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id INTEGER NOT NULL,
  artifact_id VARCHAR(64),
  feature_table VARCHAR(64) NOT NULL,
  feature_id TEXT NOT NULL,
  feature_type VARCHAR(32) NOT NULL,
  source_id TEXT,
  plan_number TEXT,
  feature_label TEXT,
  link_type VARCHAR(32) NOT NULL,
  match_text TEXT,
  confidence REAL NOT NULL,
  confidence_label VARCHAR(32) NOT NULL,
  is_uncertain INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (decision_id) REFERENCES decision(id),
  FOREIGN KEY (artifact_id) REFERENCES retrieval_artifact(artifact_id),
  CONSTRAINT uq_decision_gis_feature_link UNIQUE (decision_id, feature_table, feature_id, link_type, match_text)
);

CREATE INDEX IF NOT EXISTS ix_decision_gis_feature_link_decision
ON decision_gis_feature_link(decision_id);

CREATE INDEX IF NOT EXISTS ix_decision_gis_feature_link_feature
ON decision_gis_feature_link(feature_table, feature_id);

CREATE INDEX IF NOT EXISTS ix_decision_gis_feature_link_plan
ON decision_gis_feature_link(plan_number);
