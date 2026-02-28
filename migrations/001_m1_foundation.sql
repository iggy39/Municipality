CREATE TABLE IF NOT EXISTS source_site (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  municipality_slug VARCHAR(64) NOT NULL,
  name VARCHAR(255) NOT NULL,
  root_url TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pipeline_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_type VARCHAR(32) NOT NULL,
  municipality_slug VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME,
  error_summary TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_run_step (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  step_name VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  item_ref TEXT,
  detail TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (run_id) REFERENCES pipeline_run(id)
);

CREATE TABLE IF NOT EXISTS taxonomy_node (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_site_id INTEGER NOT NULL,
  node_external_id VARCHAR(128) NOT NULL,
  parent_external_id VARCHAR(128),
  title_he TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  node_type VARCHAR(32) NOT NULL,
  depth INTEGER NOT NULL,
  count_hint INTEGER,
  crawl_run_id INTEGER NOT NULL,
  discovered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (source_site_id) REFERENCES source_site(id),
  FOREIGN KEY (crawl_run_id) REFERENCES pipeline_run(id),
  CONSTRAINT uq_taxonomy_site_external UNIQUE (source_site_id, node_external_id)
);

CREATE TABLE IF NOT EXISTS document (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_site_id INTEGER NOT NULL,
  document_external_id VARCHAR(128) NOT NULL,
  canonical_url TEXT NOT NULL,
  title_he TEXT NOT NULL,
  doc_kind VARCHAR(32) NOT NULL,
  mime_hint VARCHAR(128),
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (source_site_id) REFERENCES source_site(id),
  CONSTRAINT uq_document_canonical_url UNIQUE (canonical_url)
);

CREATE TABLE IF NOT EXISTS document_version (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL,
  sha256 VARCHAR(64) NOT NULL,
  byte_size INTEGER NOT NULL,
  storage_uri TEXT NOT NULL,
  fetched_http_status INTEGER,
  fetched_mime VARCHAR(128),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (document_id) REFERENCES document(id),
  CONSTRAINT uq_document_version_hash UNIQUE (document_id, sha256)
);

CREATE TABLE IF NOT EXISTS asset_manifest (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_site_id INTEGER NOT NULL,
  source_node_external_id VARCHAR(128) NOT NULL,
  asset_external_id VARCHAR(128) NOT NULL,
  asset_url TEXT NOT NULL,
  asset_kind VARCHAR(32) NOT NULL,
  title_he TEXT NOT NULL,
  mime_hint VARCHAR(128) NOT NULL,
  crawl_run_id INTEGER NOT NULL,
  discovered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (source_site_id) REFERENCES source_site(id),
  FOREIGN KEY (crawl_run_id) REFERENCES pipeline_run(id),
  CONSTRAINT uq_asset_manifest_site_external UNIQUE (source_site_id, asset_external_id)
);

CREATE INDEX IF NOT EXISTS ix_taxonomy_node_external_id ON taxonomy_node(node_external_id);
CREATE INDEX IF NOT EXISTS ix_taxonomy_canonical_url ON taxonomy_node(canonical_url);
CREATE INDEX IF NOT EXISTS ix_document_version_sha256 ON document_version(sha256);
CREATE INDEX IF NOT EXISTS ix_document_version_document_id ON document_version(document_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_run_step_run_id ON pipeline_run_step(run_id);
CREATE INDEX IF NOT EXISTS ix_asset_manifest_crawl_run_id ON asset_manifest(crawl_run_id);
