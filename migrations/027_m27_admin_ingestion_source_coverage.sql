CREATE TABLE IF NOT EXISTS ingestion_source_coverage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  municipality_slug VARCHAR(64) NOT NULL,
  municipality_name_he TEXT,
  source_type VARCHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'unknown',
  notes TEXT,
  source_url TEXT,
  checked_at DATETIME,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_ingestion_source_coverage_muni_type UNIQUE (municipality_slug, source_type),
  CONSTRAINT chk_ingestion_source_coverage_status CHECK (status IN ('available', 'missing', 'unknown'))
);

CREATE INDEX IF NOT EXISTS ix_ingestion_source_coverage_municipality
ON ingestion_source_coverage(municipality_slug);

CREATE INDEX IF NOT EXISTS ix_ingestion_source_coverage_status
ON ingestion_source_coverage(status);
