from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.migrations import apply_all
from municipality.models import (
    ArtifactSemanticLink,
    ArtifactTopicAnnotation,
    Document,
    DocumentVersion,
    ExtractedDocument,
    RetrievalArtifact,
    SemanticNode,
    SourceSite,
)
from municipality.topic_data_maintenance import cleanup_low_quality_topic_data


def test_cleanup_low_quality_topic_data_is_scoped_and_idempotent(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'topic-maintenance.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()

        document = Document(
            source_site_id=site.id,
            document_external_id="doc:maintenance",
            canonical_url="https://example.local/maintenance.pdf",
            title_he="פרוטוקול ועדת הקצאות",
            doc_kind="protocol_full",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        session.add(document)
        session.flush()

        version = DocumentVersion(
            document_id=document.id,
            sha256="7" * 64,
            byte_size=10,
            storage_uri="maintenance.pdf",
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
        session.add(version)
        session.flush()

        extracted = ExtractedDocument(
            document_version_id=version.id,
            parser_name="stub",
            parser_version="test",
            status="completed",
            extracted_text="foo",
            page_count=1,
            pages_json="[]",
            citation_map_json="[]",
            updated_at=datetime.utcnow(),
        )
        session.add(extracted)
        session.flush()

        artifact = RetrievalArtifact(
            artifact_id="artifact-1",
            document_id=document.id,
            document_version_id=version.id,
            extracted_document_id=extracted.id,
            source_kind="protocol",
            artifact_kind="section_unit",
            ordinal=1,
            title_he=document.title_he,
            committee_name="ועדת הקצאות",
            meeting_date="2026-01-01",
            header_path_json=json.dumps(["הקצאות", "עיקרי ההחלטה"], ensure_ascii=False),
            body_text="foo",
            retrieval_text="foo",
            retrieval_text_norm="foo",
            start_offset=0,
            end_offset=3,
            start_page=1,
            end_page=1,
            citation_label="p.1",
            trigram_count=0,
            metadata_json=json.dumps({}, ensure_ascii=False),
            section_id="sec-1",
        )
        session.add(artifact)
        session.flush()

        annotation = ArtifactTopicAnnotation(
            artifact_id=artifact.artifact_id,
            structural_topic_he="הקצאות > עיקרי ההחלטה",
            structural_topic_norm="הקצאות עיקרי ההחלטה",
            primary_topic_he="הקצאות > עיקרי ההחלטה",
            primary_topic_norm="הקצאות עיקרי ההחלטה",
            secondary_topics_json=json.dumps(["מכותבים תוכן ההחלטה", "תחבורה עירונית"], ensure_ascii=False),
            section_summary="foo",
            classifier_confidence=0.7,
            classifier_route="seed",
            updated_at=datetime.utcnow(),
        )
        session.add(annotation)

        low_quality_node = SemanticNode(
            source_site_id=site.id,
            node_key_hash="a" * 40,
            node_kind="topic",
            semantic_type="legacy",
            pref_label_he="החלטות עירוניות",
            pref_label_norm="החלטות עירוניות",
            parent_node_id=None,
            depth=0,
            specificity_score=0.2,
            confidence=0.6,
            support_count=1,
            status="active",
            first_seen_document_version_id=version.id,
            last_seen_document_version_id=version.id,
            metadata_json=None,
            updated_at=datetime.utcnow(),
        )
        valid_node = SemanticNode(
            source_site_id=site.id,
            node_key_hash="b" * 40,
            node_kind="topic",
            semantic_type="transport",
            pref_label_he="תחבורה עירונית",
            pref_label_norm="תחבורה עירונית",
            parent_node_id=None,
            depth=0,
            specificity_score=0.8,
            confidence=0.9,
            support_count=2,
            status="active",
            first_seen_document_version_id=version.id,
            last_seen_document_version_id=version.id,
            metadata_json=None,
            updated_at=datetime.utcnow(),
        )
        session.add_all([low_quality_node, valid_node])
        session.flush()

        session.add_all(
            [
                ArtifactSemanticLink(
                    artifact_id=artifact.artifact_id,
                    semantic_node_id=low_quality_node.id,
                    confidence=0.8,
                    source_mention_id=None,
                    metadata_json=None,
                ),
                ArtifactSemanticLink(
                    artifact_id=artifact.artifact_id,
                    semantic_node_id=valid_node.id,
                    confidence=0.8,
                    source_mention_id=None,
                    metadata_json=None,
                ),
            ]
        )
        session.commit()

        stats = cleanup_low_quality_topic_data(
            session,
            document_ids=[document.id],
            document_version_ids=[version.id],
        )
        session.commit()

        refreshed_annotation = session.execute(
            select(ArtifactTopicAnnotation).where(ArtifactTopicAnnotation.artifact_id == artifact.artifact_id)
        ).scalar_one()
        refreshed_nodes = session.execute(select(SemanticNode).order_by(SemanticNode.id.asc())).scalars().all()

        assert stats.artifact_annotations_updated == 1
        assert stats.semantic_nodes_deprecated == 1
        assert refreshed_annotation.structural_topic_he == "הקצאות"
        assert refreshed_annotation.structural_topic_norm == "הקצאות"
        assert refreshed_annotation.primary_topic_he == "הקצאות"
        assert refreshed_annotation.primary_topic_norm == "הקצאות"
        assert json.loads(refreshed_annotation.secondary_topics_json or "[]") == ["תחבורה עירונית"]
        assert refreshed_nodes[0].status == "deprecated"
        assert refreshed_nodes[1].status == "active"

        repeat = cleanup_low_quality_topic_data(
            session,
            document_ids=[document.id],
            document_version_ids=[version.id],
        )

        assert repeat.artifact_annotations_updated == 0
        assert repeat.semantic_nodes_deprecated == 0
