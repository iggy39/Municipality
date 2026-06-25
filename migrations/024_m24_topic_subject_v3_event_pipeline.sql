CREATE TABLE topic_subject_v3_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    municipality_slug VARCHAR(64) NOT NULL,
    model_provider VARCHAR(64) NOT NULL,
    model_name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    write_mode BOOLEAN NOT NULL DEFAULT 0,
    source_artifact_count INTEGER NOT NULL DEFAULT 0,
    event_count INTEGER NOT NULL DEFAULT 0,
    candidate_subject_count INTEGER NOT NULL DEFAULT 0,
    candidate_decision_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT,
    error_text TEXT,
    started_at DATETIME NOT NULL,
    finished_at DATETIME,
    created_at DATETIME NOT NULL
);

CREATE INDEX ix_topic_subject_v3_run_municipality
ON topic_subject_v3_run (municipality_slug, started_at);

CREATE TABLE topic_subject_v3_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES topic_subject_v3_run(id),
    municipality_slug VARCHAR(64) NOT NULL,
    event_id VARCHAR(96) NOT NULL,
    event_index INTEGER NOT NULL DEFAULT 0,
    source_document_id INTEGER NOT NULL REFERENCES document(id),
    source_document_version_id INTEGER NOT NULL REFERENCES document_version(id),
    anchor_artifact_id VARCHAR(64) REFERENCES retrieval_artifact(artifact_id),
    anchor_semantic_node_id INTEGER REFERENCES semantic_node(id),
    source_ordinal_start INTEGER,
    source_ordinal_end INTEGER,
    source_page_start INTEGER,
    source_page_end INTEGER,
    action_root_label_he TEXT NOT NULL,
    action_root_label_norm TEXT NOT NULL,
    action_child_label_he TEXT,
    action_child_label_norm TEXT,
    other_action_label_he TEXT,
    other_action_label_norm TEXT,
    action_label_confidence FLOAT NOT NULL DEFAULT 0.0,
    action_label_status VARCHAR(64) NOT NULL,
    subject_matter_he TEXT NOT NULL,
    subject_matter_norm TEXT NOT NULL,
    action_details_he TEXT,
    action_evidence_quote_he TEXT,
    subject_summary_he TEXT,
    what_text_is_about_he TEXT,
    is_decision BOOLEAN NOT NULL DEFAULT 0,
    decision_label_he TEXT,
    decision_label_norm TEXT,
    decision_summary_he TEXT,
    decision_source_quote_he TEXT,
    confidence FLOAT NOT NULL DEFAULT 0.0,
    validation_status VARCHAR(32) NOT NULL,
    failure_reason TEXT,
    normalized_event_json TEXT,
    extraction_payload_json TEXT,
    judge_payload_json TEXT,
    evidence_refs_json TEXT,
    metadata_json TEXT,
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_topic_subject_v3_event_run_event UNIQUE (run_id, event_id)
);

CREATE INDEX ix_topic_subject_v3_event_run
ON topic_subject_v3_event (run_id);

CREATE INDEX ix_topic_subject_v3_event_labels
ON topic_subject_v3_event (action_root_label_norm, action_child_label_norm);

CREATE INDEX ix_topic_subject_v3_event_decision
ON topic_subject_v3_event (is_decision, decision_label_norm);

CREATE TABLE topic_subject_v3_row_quality (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES topic_subject_v3_run(id),
    municipality_slug VARCHAR(64) NOT NULL,
    event_id VARCHAR(96),
    artifact_id VARCHAR(64) NOT NULL REFERENCES retrieval_artifact(artifact_id),
    semantic_node_id INTEGER NOT NULL REFERENCES semantic_node(id),
    source_document_id INTEGER NOT NULL REFERENCES document(id),
    source_document_version_id INTEGER NOT NULL REFERENCES document_version(id),
    source_ordinal INTEGER,
    source_page_start INTEGER,
    source_page_end INTEGER,
    source_kind VARCHAR(32) NOT NULL,
    source_title TEXT,
    source_topic_label_he TEXT,
    real_text TEXT NOT NULL,
    corrected_text_he TEXT,
    row_role VARCHAR(64),
    event_role VARCHAR(64),
    topic_relevance VARCHAR(64),
    action_root_by_dicta TEXT,
    action_child_by_dicta TEXT,
    other_action_by_dicta TEXT,
    subject_matter_by_dicta TEXT,
    action_details_by_dicta TEXT,
    decision_by_dicta TEXT,
    model_prediction_json TEXT,
    judge_status VARCHAR(64),
    ground_truth_he TEXT,
    reason_for_failure TEXT,
    quality_status VARCHAR(32) NOT NULL,
    metadata_json TEXT,
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_topic_subject_v3_row_run_artifact UNIQUE (run_id, artifact_id, semantic_node_id)
);

CREATE INDEX ix_topic_subject_v3_row_run
ON topic_subject_v3_row_quality (run_id);

CREATE INDEX ix_topic_subject_v3_row_event
ON topic_subject_v3_row_quality (run_id, event_id);

CREATE INDEX ix_topic_subject_v3_row_status
ON topic_subject_v3_row_quality (quality_status);
