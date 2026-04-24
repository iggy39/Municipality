CREATE TABLE IF NOT EXISTS artifact_topic_annotation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id VARCHAR(64) NOT NULL,
  structural_topic_he TEXT,
  structural_topic_norm TEXT,
  primary_topic_he TEXT,
  primary_topic_norm TEXT,
  secondary_topics_json TEXT,
  section_summary TEXT,
  classifier_confidence REAL,
  classifier_route VARCHAR(64),
  provider_name VARCHAR(64),
  model_name VARCHAR(128),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (artifact_id) REFERENCES retrieval_artifact(artifact_id),
  CONSTRAINT uq_artifact_topic_annotation_artifact_id UNIQUE (artifact_id)
);

CREATE INDEX IF NOT EXISTS ix_artifact_topic_annotation_primary_norm
ON artifact_topic_annotation(primary_topic_norm);

CREATE INDEX IF NOT EXISTS ix_artifact_topic_annotation_route
ON artifact_topic_annotation(classifier_route);
