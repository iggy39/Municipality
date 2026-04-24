from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.migrations import apply_all
from municipality.models import ArtifactTopicAnnotation, Document, DocumentVersion, ExtractedDocument, SourceSite
from municipality.artifact_search import ArtifactSearchService
from municipality.topic_classifier import ArtifactTopicClassifier


def test_topic_classifier_persists_structural_and_primary_topics(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'topic-classifier.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()

        document = Document(
            source_site_id=site.id,
            document_external_id="doc:traffic",
            canonical_url="https://example.local/traffic.pdf",
            title_he="פרוטוקול ועדת בטיחות",
            doc_kind="protocol_full",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        session.add(document)
        session.flush()

        version = DocumentVersion(
            document_id=document.id,
            sha256="1" * 64,
            byte_size=10,
            storage_uri="traffic.pdf",
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
            extracted_text="",
            page_count=1,
            pages_json="[]",
            citation_map_json="[]",
            updated_at=datetime.utcnow(),
        )
        session.add(extracted)
        session.flush()

        ArtifactSearchService(session).replace_document_artifacts(
            document_id=document.id,
            document_version_id=version.id,
            extracted_document_id=extracted.id,
            artifacts=[
                {
                    "artifact_id": "art-1",
                    "source_kind": "protocol",
                    "artifact_kind": "decision_unit",
                    "ordinal": 1,
                    "title_he": document.title_he,
                    "committee_name": "ועדת בטיחות",
                    "meeting_date": "2026-01-15",
                    "header_path_json": json.dumps([document.title_he, "נושא 2", "הרחבת החניה ליד בית הספר"], ensure_ascii=False),
                    "body_text": "הוחלט לקדם הרחבת חניה ליד בית הספר ולהוסיף אכיפה בשעות הבוקר.",
                    "retrieval_text": "הוחלט לקדם הרחבת חניה ליד בית הספר ולהוסיף אכיפה בשעות הבוקר.",
                    "retrieval_text_norm": "הוחלט לקדם הרחבת חניה ליד בית הספר ולהוסיף אכיפה בשעות הבוקר.",
                    "start_offset": 0,
                    "end_offset": 68,
                    "start_page": 1,
                    "end_page": 1,
                    "citation_label": "p.1",
                    "trigrams": [],
                    "trigram_count": 0,
                    "metadata_json": json.dumps({}),
                    "section_id": "sec-1",
                }
            ],
        )
        classifier = ArtifactTopicClassifier(session)

        touched = classifier.annotate_document_version(document_version_id=version.id)
        session.commit()

        row = session.execute(select(ArtifactTopicAnnotation).where(ArtifactTopicAnnotation.artifact_id == "art-1")).scalar_one()
        assert touched == 1
        assert row.structural_topic_he is not None
        assert row.primary_topic_he is not None
        assert "חניה" in row.primary_topic_he
