ALTER TABLE topic_subject ADD COLUMN action_root_label_he TEXT;
ALTER TABLE topic_subject ADD COLUMN action_root_label_norm TEXT;
ALTER TABLE topic_subject ADD COLUMN action_child_label_he TEXT;
ALTER TABLE topic_subject ADD COLUMN action_child_label_norm TEXT;
ALTER TABLE topic_subject ADD COLUMN subject_matter_he TEXT;
ALTER TABLE topic_subject ADD COLUMN action_details_he TEXT;

ALTER TABLE topic_subject_quality_report ADD COLUMN action_root_by_dicta TEXT;
ALTER TABLE topic_subject_quality_report ADD COLUMN action_child_by_dicta TEXT;
ALTER TABLE topic_subject_quality_report ADD COLUMN subject_matter_by_dicta TEXT;
ALTER TABLE topic_subject_quality_report ADD COLUMN action_details_by_dicta TEXT;
