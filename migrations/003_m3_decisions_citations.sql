CREATE TABLE IF NOT EXISTS meeting (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_site_id INTEGER NOT NULL,
  meeting_external_id VARCHAR(128) NOT NULL,
  title_he TEXT NOT NULL,
  committee_name TEXT,
  meeting_kind VARCHAR(32),
  meeting_code VARCHAR(64),
  meeting_date VARCHAR(32),
  parse_confidence REAL,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (source_site_id) REFERENCES source_site(id),
  CONSTRAINT uq_meeting_site_external UNIQUE (source_site_id, meeting_external_id)
);

CREATE TABLE IF NOT EXISTS decision (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id INTEGER NOT NULL,
  source_document_id INTEGER NOT NULL,
  decision_number VARCHAR(64),
  agenda_item TEXT,
  decision_text TEXT NOT NULL,
  decision_signature_norm VARCHAR(255) NOT NULL,
  parser_confidence REAL NOT NULL,
  is_public INTEGER NOT NULL DEFAULT 1,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (meeting_id) REFERENCES meeting(id),
  FOREIGN KEY (source_document_id) REFERENCES document(id)
);

CREATE TABLE IF NOT EXISTS vote (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id INTEGER NOT NULL,
  for_count INTEGER,
  against_count INTEGER,
  abstain_count INTEGER,
  unanimous INTEGER,
  is_uncertain INTEGER NOT NULL DEFAULT 0,
  confidence REAL NOT NULL DEFAULT 0,
  raw_text TEXT,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (decision_id) REFERENCES decision(id),
  CONSTRAINT uq_vote_decision UNIQUE (decision_id)
);

CREATE TABLE IF NOT EXISTS decision_citation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id INTEGER NOT NULL,
  document_id INTEGER NOT NULL,
  document_version_id INTEGER,
  source_type VARCHAR(32) NOT NULL,
  page_number INTEGER NOT NULL,
  start_offset INTEGER NOT NULL,
  end_offset INTEGER NOT NULL,
  anchor_label VARCHAR(128),
  anchor_text TEXT,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (decision_id) REFERENCES decision(id),
  FOREIGN KEY (document_id) REFERENCES document(id),
  FOREIGN KEY (document_version_id) REFERENCES document_version(id)
);

CREATE TABLE IF NOT EXISTS meeting_document_link (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id INTEGER NOT NULL,
  document_id INTEGER NOT NULL,
  source_type VARCHAR(32) NOT NULL,
  provenance VARCHAR(32) NOT NULL,
  is_primary INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (meeting_id) REFERENCES meeting(id),
  FOREIGN KEY (document_id) REFERENCES document(id),
  CONSTRAINT uq_meeting_document_link UNIQUE (meeting_id, document_id)
);

CREATE TABLE IF NOT EXISTS decision_document_link (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id INTEGER NOT NULL,
  document_id INTEGER NOT NULL,
  source_type VARCHAR(32) NOT NULL,
  provenance VARCHAR(32) NOT NULL,
  metadata_json TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (decision_id) REFERENCES decision(id),
  FOREIGN KEY (document_id) REFERENCES document(id),
  CONSTRAINT uq_decision_document_link UNIQUE (decision_id, document_id)
);

CREATE INDEX IF NOT EXISTS ix_meeting_source_site_id ON meeting(source_site_id);
CREATE INDEX IF NOT EXISTS ix_meeting_external_id ON meeting(meeting_external_id);
CREATE INDEX IF NOT EXISTS ix_decision_meeting_id ON decision(meeting_id);
CREATE INDEX IF NOT EXISTS ix_decision_source_document_id ON decision(source_document_id);
CREATE INDEX IF NOT EXISTS ix_decision_signature_norm ON decision(decision_signature_norm);
CREATE INDEX IF NOT EXISTS ix_vote_decision_id ON vote(decision_id);
CREATE INDEX IF NOT EXISTS ix_decision_citation_decision_id ON decision_citation(decision_id);
CREATE INDEX IF NOT EXISTS ix_decision_citation_document_id ON decision_citation(document_id);
CREATE INDEX IF NOT EXISTS ix_decision_citation_page_number ON decision_citation(page_number);
CREATE INDEX IF NOT EXISTS ix_meeting_document_link_meeting_id ON meeting_document_link(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_document_link_document_id ON meeting_document_link(document_id);
CREATE INDEX IF NOT EXISTS ix_decision_document_link_decision_id ON decision_document_link(decision_id);
CREATE INDEX IF NOT EXISTS ix_decision_document_link_document_id ON decision_document_link(document_id);
