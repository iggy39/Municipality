CREATE TABLE IF NOT EXISTS artifact_semantic_link (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id VARCHAR(64) NOT NULL,
  semantic_node_id INTEGER NOT NULL,
  confidence REAL NOT NULL,
  source_mention_id INTEGER,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (artifact_id) REFERENCES retrieval_artifact(artifact_id),
  FOREIGN KEY (semantic_node_id) REFERENCES semantic_node(id),
  FOREIGN KEY (source_mention_id) REFERENCES semantic_mention(id),
  CONSTRAINT uq_artifact_semantic_link_pair UNIQUE (artifact_id, semantic_node_id)
);

CREATE INDEX IF NOT EXISTS ix_artifact_semantic_link_artifact_id
ON artifact_semantic_link(artifact_id);

CREATE INDEX IF NOT EXISTS ix_artifact_semantic_link_semantic_node_id
ON artifact_semantic_link(semantic_node_id);
