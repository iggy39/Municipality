from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.chunking import build_chunks
from municipality.extraction import parse_extracted_text
from municipality.migrations import apply_all
from municipality.models import (
    ChunkSemanticLink,
    Document,
    DocumentVersion,
    ExtractedDocument,
    SemanticAlias,
    SemanticNode,
    SourceSite,
    TextChunk,
)
from municipality.search import SearchService


HE_TRANSPORT_TEXT = "הוחלט לקדם פרויקט תחבורה עירונית מרכזית בעיר."
HE_CLEAN_TEXT = "הוחלט לקדם פרויקט ניקיון שכונתי מתמשך בעיר."


def test_m4_search_supports_semantic_boost_and_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "m4_search.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        search_service = SearchService(session)
        transport_node_id, transport_doc_id = _seed_semantic_search_fixture(session, search_service)

        filtered_hits = search_service.search(
            query="פרויקט",
            semantic_node_id=transport_node_id,
            semantic_mode="filter",
            limit=10,
        )
        assert filtered_hits
        assert all(transport_node_id in hit.semantic_node_ids for hit in filtered_hits)
        assert {hit.document_id for hit in filtered_hits} == {transport_doc_id}

        label_filtered_hits = search_service.search(
            query="פרויקט",
            semantic_label="תחבורה",
            semantic_mode="filter",
            limit=10,
        )
        assert label_filtered_hits
        assert {hit.document_id for hit in label_filtered_hits} == {transport_doc_id}

        boosted_hits = search_service.search(
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

        lexical_only_hits = search_service.search(
            query="פרויקט",
            semantic_node_id=transport_node_id,
            semantic_mode="off",
            limit=10,
        )
        assert lexical_only_hits
        assert all(hit.semantic_boost == 0.0 for hit in lexical_only_hits)
        assert all(hit.semantic_match_count == 0 for hit in lexical_only_hits)
        assert all(not hit.semantic_nodes for hit in lexical_only_hits)


def _seed_semantic_search_fixture(session: Session, search_service: SearchService) -> tuple[int, int]:
    site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
    session.add(site)
    session.flush()

    transport_doc = Document(
        source_site_id=site.id,
        document_external_id="doc:transport",
        canonical_url="https://example.local/transport.pdf",
        title_he="פרוטוקול תחבורה",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    clean_doc = Document(
        source_site_id=site.id,
        document_external_id="doc:clean",
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
        sha256="3" * 64,
        byte_size=16,
        storage_uri="tree/ashdod/transport.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    clean_ver = DocumentVersion(
        document_id=clean_doc.id,
        sha256="4" * 64,
        byte_size=16,
        storage_uri="tree/ashdod/clean.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add_all([transport_ver, clean_ver])
    session.flush()

    transport_extracted_id = _upsert_extracted_document(session, transport_ver.id, HE_TRANSPORT_TEXT)
    clean_extracted_id = _upsert_extracted_document(session, clean_ver.id, HE_CLEAN_TEXT)

    _index_text_for_document(
        search_service,
        document_id=transport_doc.id,
        document_version_id=transport_ver.id,
        extracted_document_id=transport_extracted_id,
        raw_text=HE_TRANSPORT_TEXT,
    )
    _index_text_for_document(
        search_service,
        document_id=clean_doc.id,
        document_version_id=clean_ver.id,
        extracted_document_id=clean_extracted_id,
        raw_text=HE_CLEAN_TEXT,
    )

    transport_chunk = session.execute(
        select(TextChunk).where(TextChunk.document_version_id == transport_ver.id)
    ).scalar_one()
    clean_chunk = session.execute(
        select(TextChunk).where(TextChunk.document_version_id == clean_ver.id)
    ).scalar_one()

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
            ChunkSemanticLink(
                chunk_id=transport_chunk.chunk_id,
                semantic_node_id=transport_node.id,
                confidence=0.95,
                source_mention_id=None,
                metadata_json=None,
            ),
            ChunkSemanticLink(
                chunk_id=clean_chunk.chunk_id,
                semantic_node_id=clean_node.id,
                confidence=0.9,
                source_mention_id=None,
                metadata_json=None,
            ),
        ]
    )
    session.commit()
    return transport_node.id, transport_doc.id


def _upsert_extracted_document(session: Session, document_version_id: int, raw_text: str) -> int:
    full_text, pages, citation_map = parse_extracted_text(raw_text)
    row = ExtractedDocument(
        document_version_id=document_version_id,
        parser_name="stub",
        parser_version="test",
        status="completed",
        extracted_text=full_text,
        page_count=len(pages),
        pages_json=json.dumps(
            [
                {
                    "page": page.page,
                    "text": page.text,
                    "start_offset": page.start_offset,
                    "end_offset": page.end_offset,
                }
                for page in pages
            ],
            ensure_ascii=False,
        ),
        citation_map_json=json.dumps(citation_map),
        quality_score=1.0,
        quality_flags_json=json.dumps([]),
        quality_summary_json=json.dumps({}),
        error_code=None,
        warning_text=None,
        updated_at=datetime.utcnow(),
    )
    session.add(row)
    session.flush()
    return row.id


def _index_text_for_document(
    search_service: SearchService,
    *,
    document_id: int,
    document_version_id: int,
    extracted_document_id: int,
    raw_text: str,
) -> None:
    full_text, _pages, citation_map = parse_extracted_text(raw_text)
    chunks = build_chunks(
        document_version_id=document_version_id,
        text=full_text,
        citation_map=citation_map,
        source_kind="protocol",
    )
    search_service.replace_document_chunks(
        document_id=document_id,
        document_version_id=document_version_id,
        extracted_document_id=extracted_document_id,
        source_kind="protocol",
        chunks=chunks,
    )
