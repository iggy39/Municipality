ALTER TABLE topic_subject ADD COLUMN artifact_role VARCHAR(64);
ALTER TABLE topic_subject ADD COLUMN topic_relevance VARCHAR(64);
ALTER TABLE topic_subject_quality_report ADD COLUMN artifact_role VARCHAR(64);
ALTER TABLE topic_subject_quality_report ADD COLUMN topic_relevance VARCHAR(64);
