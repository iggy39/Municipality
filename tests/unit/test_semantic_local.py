from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.chunking import build_chunks
from municipality.migrations import apply_all
from municipality.models import Decision, DecisionCitation, Document, DocumentVersion, ExtractedDocument, Meeting, SemanticNode, SourceSite
from municipality.search import SearchService
from municipality.semantic_service import SemanticService


TEXT = (
    "מהות הבקשה: בקשה להסכם רשות עם העמותה להפעלת מרכז קהילתי. "
    "הוחלט לאשר את הסכם הרשות עם העמותה."
)


def test_semantic_service_replaces_external_run_with_local_topic_matching(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'semantic-local.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        document, version = _seed_protocol_with_decision(session)
        result = SemanticService(session).run_for_document(
            source_site_id=document.source_site_id,
            document_id=document.id,
            document_version_id=version.id,
            source_kind="protocol",
            extracted_text=TEXT,
            citation_map=[{"start": 0, "end": len(TEXT), "page": 1}],
        )
        session.commit()

        assert result.status == "completed"
        assert result.api_call_count == 0
        assert result.accepted_nodes == 1
        assert result.decision_links == 1
        assert result.chunk_links >= 1

        cached = SemanticService(session).run_for_document(
            source_site_id=document.source_site_id,
            document_id=document.id,
            document_version_id=version.id,
            source_kind="protocol",
            extracted_text=TEXT,
            citation_map=[{"start": 0, "end": len(TEXT), "page": 1}],
        )

        topic_labels = session.execute(
            select(SemanticNode.pref_label_he).where(SemanticNode.source_site_id == document.source_site_id)
        ).scalars().all()

        assert cached.from_cache is True
        assert "הסכם רשות" in topic_labels


def _seed_protocol_with_decision(session: Session) -> tuple[Document, DocumentVersion]:
    site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
    session.add(site)
    session.flush()

    document = Document(
        source_site_id=site.id,
        document_external_id="doc-semantic-local",
        canonical_url="https://example.local/protocol-semantic.pdf",
        title_he="פרוטוקול ועדת נכסים",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    session.add(document)
    session.flush()

    version = DocumentVersion(
        document_id=document.id,
        sha256="2" * 64,
        byte_size=len(TEXT.encode("utf-8")),
        storage_uri="storage/raw/protocol-semantic.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add(version)
    session.flush()

    extracted = ExtractedDocument(
        document_version_id=version.id,
        parser_name="stub",
        parser_version="1",
        status="completed",
        extracted_text=TEXT,
        page_count=1,
        pages_json="[]",
        citation_map_json="[]",
        quality_score=1.0,
        quality_flags_json="[]",
        quality_summary_json="{}",
        error_code=None,
        warning_text=None,
    )
    session.add(extracted)
    session.flush()

    meeting = Meeting(
        source_site_id=site.id,
        meeting_external_id="meeting-semantic-local",
        title_he="ועדת נכסים",
    )
    session.add(meeting)
    session.flush()

    decision = Decision(
        meeting_id=meeting.id,
        source_document_id=document.id,
        agenda_item="בקשה להסכם רשות עם העמותה",
        decision_text="הוחלט לאשר את הסכם הרשות עם העמותה.",
        decision_signature_norm="הוחלט לאשר את הסכם הרשות עם העמותה",
        parser_confidence=0.95,
        is_public=True,
    )
    session.add(decision)
    session.flush()

    chunks = build_chunks(
        document_version_id=version.id,
        text=TEXT,
        citation_map=[{"start": 0, "end": len(TEXT), "page": 1}],
        source_kind="protocol",
    )
    SearchService(session).replace_document_chunks(
        document_id=document.id,
        document_version_id=version.id,
        extracted_document_id=extracted.id,
        source_kind="protocol",
        chunks=chunks,
    )
    session.flush()

    session.add(
        DecisionCitation(
            decision_id=decision.id,
            document_id=document.id,
            document_version_id=version.id,
            source_type="protocol",
            page_number=1,
            start_offset=0,
            end_offset=len(TEXT),
            anchor_label="p.1",
            anchor_text="הוחלט לאשר את הסכם הרשות עם העמותה",
        )
    )
    session.flush()
    return document, version
