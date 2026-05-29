from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import municipality.artifact_search as artifact_search
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from municipality.artifact_search import ArtifactSearchService
from municipality.chunking import build_trigrams, normalize_for_search
from municipality.migrations import apply_all
from municipality.models import (
    ArtifactTopicAnnotation,
    ArtifactSemanticLink,
    Document,
    DocumentVersion,
    ExtractedDocument,
    SemanticAlias,
    SemanticNode,
    SourceSite,
)


HE_TRANSPORT_TEXT = "הוחלט לקדם פרויקט תחבורה עירונית מרכזית בעיר."
HE_CLEAN_TEXT = "הוחלט לקדם פרויקט ניקיון שכונתי מתמשך בעיר."


def test_artifact_search_supports_semantic_boost_and_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "artifact_search.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        service = ArtifactSearchService(session)
        transport_node_id, transport_doc_id = _seed_artifact_search_fixture(session, service)

        filtered_hits = service.search(
            query="פרויקט",
            semantic_node_id=transport_node_id,
            semantic_mode="filter",
            limit=10,
        )
        assert filtered_hits
        assert all(transport_node_id in hit.semantic_node_ids for hit in filtered_hits)
        assert {hit.document_id for hit in filtered_hits} == {transport_doc_id}

        label_filtered_hits = service.search(
            query="פרויקט",
            semantic_label="תחבורה",
            semantic_mode="filter",
            limit=10,
        )
        assert label_filtered_hits
        assert {hit.document_id for hit in label_filtered_hits} == {transport_doc_id}

        boosted_hits = service.search(
            query="פרויקט",
            semantic_node_id=transport_node_id,
            semantic_mode="boost",
            limit=10,
        )
        assert len(boosted_hits) >= 2
        assert boosted_hits[0].document_id == transport_doc_id
        assert boosted_hits[0].semantic_boost > 0.0
        assert boosted_hits[0].semantic_match_count >= 1
        assert any(hit.semantic_boost == 0.0 for hit in boosted_hits if hit.document_id != transport_doc_id)

        lexical_only_hits = service.search(
            query="פרויקט",
            semantic_node_id=transport_node_id,
            semantic_mode="off",
            limit=10,
        )
        assert lexical_only_hits
        assert all(hit.semantic_boost == 0.0 for hit in lexical_only_hits)
        assert all(hit.semantic_match_count == 0 for hit in lexical_only_hits)


def test_artifact_search_uses_topic_annotations_for_topic_terms(tmp_path: Path) -> None:
    db_path = tmp_path / "artifact_search_topic.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        service = ArtifactSearchService(session)
        _transport_node_id, transport_doc_id = _seed_artifact_search_fixture(session, service)
        session.add(
            ArtifactTopicAnnotation(
                artifact_id="artifact-transport",
                structural_topic_he="תחבורה ובטיחות > תחבורה עירונית",
                structural_topic_norm="תחבורה ובטיחות תחבורה עירונית",
                primary_topic_he="תחבורה ובטיחות > תחבורה עירונית",
                primary_topic_norm="תחבורה ובטיחות תחבורה עירונית",
                secondary_topics_json=json.dumps(["תחבורה עירונית"], ensure_ascii=False),
                section_summary="קידום פרויקט תחבורה עירונית",
                classifier_confidence=0.88,
                classifier_route="deterministic_structural_refine",
                provider_name=None,
                model_name=None,
            )
        )
        session.commit()

        hits = service.search(
            query="חניה",
            topic_terms=["תחבורה עירונית"],
            artifact_kinds=["section_unit"],
            limit=10,
        )

        assert hits
        assert hits[0].document_id == transport_doc_id
        assert hits[0].primary_topic == "תחבורה ובטיחות > תחבורה עירונית"


def test_artifact_search_hides_low_quality_topic_annotations() -> None:
    annotation = ArtifactTopicAnnotation(
        artifact_id="artifact-1",
        primary_topic_he="החלטות עירוניות",
        secondary_topics_json=json.dumps(["עיקרי ההחלטה", "תחבורה עירונית"], ensure_ascii=False),
    )

    assert artifact_search._annotation_primary_topic(annotation) is None
    assert artifact_search._annotation_secondary_topics(annotation) == ["תחבורה עירונית"]


def _seed_artifact_search_fixture(session: Session, service: ArtifactSearchService) -> tuple[int, int]:
    site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
    session.add(site)
    session.flush()

    transport_doc = Document(
        source_site_id=site.id,
        document_external_id="doc:artifact:transport",
        canonical_url="https://example.local/transport.pdf",
        title_he="פרוטוקול תחבורה",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    clean_doc = Document(
        source_site_id=site.id,
        document_external_id="doc:artifact:clean",
        canonical_url="https://example.local/clean.pdf",
        title_he="פרוטוקול ניקיון",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    session.add_all([transport_doc, clean_doc])
    session.flush()

    transport_ver = DocumentVersion(
        document_id=transport_doc.id,
        sha256="7" * 64,
        byte_size=16,
        storage_uri="tree/ashdod/transport.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    clean_ver = DocumentVersion(
        document_id=clean_doc.id,
        sha256="8" * 64,
        byte_size=16,
        storage_uri="tree/ashdod/clean.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add_all([transport_ver, clean_ver])
    session.flush()

    transport_extracted = ExtractedDocument(
        document_version_id=transport_ver.id,
        parser_name="stub",
        parser_version="test",
        status="completed",
        extracted_text=HE_TRANSPORT_TEXT,
        page_count=1,
        pages_json=json.dumps([]),
        citation_map_json=json.dumps([{"start": 0, "end": len(HE_TRANSPORT_TEXT), "page": 1}]),
        updated_at=datetime.utcnow(),
    )
    clean_extracted = ExtractedDocument(
        document_version_id=clean_ver.id,
        parser_name="stub",
        parser_version="test",
        status="completed",
        extracted_text=HE_CLEAN_TEXT,
        page_count=1,
        pages_json=json.dumps([]),
        citation_map_json=json.dumps([{"start": 0, "end": len(HE_CLEAN_TEXT), "page": 1}]),
        updated_at=datetime.utcnow(),
    )
    session.add_all([transport_extracted, clean_extracted])
    session.flush()

    transport_artifact_id = "artifact-transport"
    clean_artifact_id = "artifact-clean"
    service.replace_document_artifacts(
        document_id=transport_doc.id,
        document_version_id=transport_ver.id,
        extracted_document_id=transport_extracted.id,
        artifacts=[_artifact_payload(artifact_id=transport_artifact_id, body_text=HE_TRANSPORT_TEXT)],
    )
    service.replace_document_artifacts(
        document_id=clean_doc.id,
        document_version_id=clean_ver.id,
        extracted_document_id=clean_extracted.id,
        artifacts=[_artifact_payload(artifact_id=clean_artifact_id, body_text=HE_CLEAN_TEXT)],
    )

    transport_node = SemanticNode(
        source_site_id=site.id,
        node_key_hash="a" * 40,
        node_kind="topic",
        semantic_type="transport_program",
        pref_label_he="תחבורה עירונית",
        pref_label_norm="תחבורה עירונית",
        parent_node_id=None,
        depth=0,
        specificity_score=0.87,
        confidence=0.9,
        support_count=2,
        status="active",
        first_seen_document_version_id=transport_ver.id,
        last_seen_document_version_id=transport_ver.id,
        metadata_json=None,
        updated_at=datetime.utcnow(),
    )
    clean_node = SemanticNode(
        source_site_id=site.id,
        node_key_hash="b" * 40,
        node_kind="topic",
        semantic_type="sanitation_program",
        pref_label_he="ניקיון שכונתי",
        pref_label_norm="ניקיון שכונתי",
        parent_node_id=None,
        depth=0,
        specificity_score=0.72,
        confidence=0.82,
        support_count=1,
        status="active",
        first_seen_document_version_id=clean_ver.id,
        last_seen_document_version_id=clean_ver.id,
        metadata_json=None,
        updated_at=datetime.utcnow(),
    )
    session.add_all([transport_node, clean_node])
    session.flush()

    session.add(
        SemanticAlias(
            semantic_node_id=transport_node.id,
            alias_hash="c" * 40,
            alias_label_he="פרויקט תחבורה",
            alias_label_norm="פרויקט תחבורה",
            alias_kind="surface",
            confidence=0.7,
            first_seen_document_version_id=transport_ver.id,
            last_seen_document_version_id=transport_ver.id,
            metadata_json=None,
        )
    )
    session.add_all(
        [
            ArtifactSemanticLink(
                artifact_id=transport_artifact_id,
                semantic_node_id=transport_node.id,
                confidence=0.95,
                source_mention_id=None,
                metadata_json=None,
            ),
            ArtifactSemanticLink(
                artifact_id=clean_artifact_id,
                semantic_node_id=clean_node.id,
                confidence=0.9,
                source_mention_id=None,
                metadata_json=None,
            ),
        ]
    )
    session.commit()
    return transport_node.id, transport_doc.id


def _artifact_payload(*, artifact_id: str, body_text: str) -> dict[str, object]:
    retrieval_text = f"כותרת מסמך: מסמך\nמסלול כותרות: מסמך > סעיף\nטקסט:\n{body_text}"
    trigrams = sorted(build_trigrams(retrieval_text))
    return {
        "artifact_id": artifact_id,
        "source_kind": "protocol",
        "artifact_kind": "section_unit",
        "ordinal": 1,
        "title_he": "מסמך",
        "committee_name": None,
        "meeting_date": None,
        "header_path_json": json.dumps(["מסמך", "סעיף"], ensure_ascii=False),
        "body_text": body_text,
        "retrieval_text": retrieval_text,
        "retrieval_text_norm": normalize_for_search(retrieval_text),
        "start_offset": 0,
        "end_offset": len(body_text),
        "start_page": 1,
        "end_page": 1,
        "citation_label": "p.1",
        "trigrams": trigrams,
        "trigram_count": len(trigrams),
        "metadata_json": json.dumps({}),
        "section_id": None,
    }
