from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.api import AskRequest, _run_ask, ask_playground_page
from municipality.chunking import build_chunks
from municipality.extraction import parse_extracted_text
from municipality.migrations import apply_all
from municipality.models import (
    ChunkSemanticLink,
    Document,
    DocumentVersion,
    ExtractedDocument,
    SemanticNode,
    SourceSite,
    TextChunk,
)
from municipality.rag_llm import MockRagProvider, RagLlmConfig, build_rag_llm_client
from municipality.search import SearchService


HE_PROTOCOL_TEXT = "הוחלט לאשר צעדי בטיחות בדרכים ברחבי העיר."
HE_ATTACHMENT_TEXT = "מועצת העיר אישרה הסכם מול עמותת לגישור חברה ויהדות."


def test_m4_ask_ui_playground_exposes_debug_threshold_panels() -> None:
    page = ask_playground_page()
    assert isinstance(page, HTMLResponse)

    body = bytes(page.body).decode("utf-8")
    assert 'lang="en"' in body
    assert 'dir="ltr"' in body
    assert 'id="ask-playground-form"' in body
    assert 'id="ask-debug-mode"' in body
    assert 'id="ask-playground-thresholds"' in body
    assert 'id="ask-playground-thresholds-json"' in body
    assert 'id="ask-show-extended"' in body
    assert 'id="ask-playground-extended-panel"' in body
    assert 'id="ask-playground-answer-sections"' in body
    assert "isAlmostEqualText" in body
    assert "hasMeaningfulExtraInfo" in body
    assert "topic-root" in body
    assert "topic-child" in body
    assert 'id="ask-playground-topic-tree-panel"' in body
    assert 'id="ask-playground-topic-tree-body"' in body
    assert '"/topic/tree/cache"' in body
    assert "debug mode (show thresholds)" in body
    assert 'fetch("/ask"' in body
    assert "debug_mode" in body


def test_m4_ask_api_returns_answer_with_citation_contract(tmp_path: Path) -> None:
    db_path = tmp_path / "m4_ask_answer.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        protocol_chunk_id, attachment_chunk_id = _seed_mixed_source_chunks(session)

        provider = MockRagProvider(
            responses_by_call_type={
                "answer": json.dumps(
                    {
                        "answer": "אושרו צעדי בטיחות, ובנוסף אושר הסכם עירוני.",
                        "limitations": ["מבוסס על שני קטעים נשלפים"],
                        "claims": [
                            {
                                "text": "אושרו צעדי בטיחות",
                                "citation_chunk_ids": [protocol_chunk_id],
                            },
                            {
                                "text": "אושר הסכם עירוני",
                                "citation_chunk_ids": [attachment_chunk_id],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                "verify": json.dumps(
                    {
                        "all_supported": True,
                        "claims": [
                            {
                                "text": "אושרו צעדי בטיחות",
                                "supported": True,
                                "citation_chunk_ids": [protocol_chunk_id],
                            },
                            {
                                "text": "אושר הסכם עירוני",
                                "supported": True,
                                "citation_chunk_ids": [attachment_chunk_id],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
            }
        )
        llm_client = build_rag_llm_client(
            config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}),
            provider=provider,
        )
        payload = _run_ask(
            request=AskRequest(
                question="מה אושר בעיר?",
                top_k=8,
                source_types=["protocol", "attachment"],
                required_source_types=["protocol", "attachment"],
                semantic_mode="off",
                debug_mode=True,
            ),
            db=session,
            llm_client=llm_client,
        )

        assert isinstance(payload["ask_request_id"], str)
        assert payload["ask_request_id"]
        assert payload["status"] == "answer"
        assert payload["answer"]
        assert payload["extended_answer"]
        assert isinstance(payload["answer_sections"], list)
        assert payload["answer_sections"]
        assert isinstance(payload["extended_answer_sections"], list)
        assert payload["extended_answer_sections"]
        assert len(payload["extended_answer_sections"]) == len(payload["answer_sections"])
        assert payload["refusal"] is None
        assert payload["citations"]
        assert isinstance(payload["retrieval"]["retrieval_set_id"], str)
        assert payload["retrieval"]["retrieval_set_id"]
        assert payload["retrieval"]["semantic_mode"] == "off"
        assert payload["retrieval"]["semantic_node_id"] is None
        assert payload["retrieval"]["semantic_label"] is None
        assert {row["source_type"] for row in payload["citations"]} == {"protocol", "attachment"}
        assert all(row["document"]["id"] for row in payload["citations"])
        assert all(row["document"]["title"] for row in payload["citations"])
        assert all(row["document"]["url"] for row in payload["citations"])
        assert all(row["start_page"] is not None for row in payload["citations"])
        assert all(row["end_page"] is not None for row in payload["citations"])
        assert isinstance(payload["limitations"], list)
        assert payload["debug"]["enabled"] is True
        thresholds = payload["debug"]["thresholds"]
        assert isinstance(payload["debug"].get("answering_trace"), dict)
        assert payload["debug"]["answering_trace"].items() >= payload["scoring"].items()
        assert "similarity_external_api_called" in payload["debug"]["answering_trace"]
        assert "timing_ms" in payload["debug"]["answering_trace"]
        assert isinstance(payload["debug"].get("timing_ms"), dict)
        assert thresholds["request_validation"]["top_k_min"] == 1
        assert thresholds["request_validation"]["top_k_max"] == 50
        assert "retrieval" in thresholds
        assert "answering" in thresholds
        assert "llm" in thresholds


def test_m4_ask_api_returns_refusal_with_reason_code_when_source_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "m4_ask_refusal.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        _seed_mixed_source_chunks(session)

        llm_client = build_rag_llm_client(
            config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}),
            provider=MockRagProvider(),
        )
        payload = _run_ask(
            request=AskRequest(
                question="מה אושר בעיר?",
                top_k=8,
                source_types=["protocol"],
                required_source_types=["protocol", "attachment"],
                semantic_mode="off",
            ),
            db=session,
            llm_client=llm_client,
        )

        assert isinstance(payload["ask_request_id"], str)
        assert payload["ask_request_id"]
        assert payload["status"] == "refusal"
        assert payload["answer"] is None
        assert payload["extended_answer"] is None
        assert payload["answer_sections"] == []
        assert payload["extended_answer_sections"] == []
        assert payload["citations"] == []
        assert isinstance(payload["retrieval"]["retrieval_set_id"], str)
        assert payload["retrieval"]["retrieval_set_id"]
        assert payload["retrieval"]["semantic_mode"] == "off"
        assert payload["retrieval"]["semantic_node_id"] is None
        assert payload["retrieval"]["semantic_label"] is None
        assert payload["refusal"] is not None
        assert payload["refusal"]["reason_code"] == "MISSING_ATTACHMENT_EVIDENCE"
        assert "אין מספיק ראיות" in (payload["refusal"]["message_he"] or "")
        assert payload["refusal"]["missing_source_types"] == ["attachment"]


def test_m4_ask_api_semantic_filter_preserves_citation_first_refusal(tmp_path: Path) -> None:
    db_path = tmp_path / "m4_ask_semantic_filter.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        protocol_chunk_id, _attachment_chunk_id = _seed_mixed_source_chunks(session)

        protocol_chunk = session.execute(
            select(TextChunk).where(TextChunk.chunk_id == protocol_chunk_id)
        ).scalar_one()
        protocol_document = session.execute(
            select(Document).where(Document.id == protocol_chunk.document_id)
        ).scalar_one()

        semantic_node = SemanticNode(
            source_site_id=protocol_document.source_site_id,
            node_key_hash="9" * 40,
            node_kind="topic",
            semantic_type="road_safety",
            pref_label_he="בטיחות בדרכים",
            pref_label_norm="בטיחות בדרכים",
            parent_node_id=None,
            depth=0,
            specificity_score=0.81,
            confidence=0.91,
            support_count=1,
            status="active",
            first_seen_document_version_id=protocol_chunk.document_version_id,
            last_seen_document_version_id=protocol_chunk.document_version_id,
            metadata_json=json.dumps({"seed": True}),
            updated_at=datetime.utcnow(),
        )
        session.add(semantic_node)
        session.flush()

        session.add(
            ChunkSemanticLink(
                chunk_id=protocol_chunk_id,
                semantic_node_id=semantic_node.id,
                confidence=0.95,
                source_mention_id=None,
                metadata_json=json.dumps({"seed": True}),
            )
        )
        session.commit()

        llm_client = build_rag_llm_client(
            config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}),
            provider=MockRagProvider(),
        )
        payload = _run_ask(
            request=AskRequest(
                question="מה אושר בעיר?",
                top_k=8,
                source_types=["protocol", "attachment"],
                required_source_types=["protocol", "attachment"],
                semantic_mode="filter",
                semantic_label="בטיחות",
            ),
            db=session,
            llm_client=llm_client,
        )

        assert payload["status"] == "refusal"
        assert payload["answer"] is None
        assert payload["citations"] == []
        assert payload["retrieval"]["semantic_mode"] == "filter"
        assert payload["retrieval"]["semantic_label"] == "בטיחות"
        assert payload["retrieval"]["semantic_node_id"] is None
        assert payload["retrieval"]["source_types"] == ["protocol"]
        assert payload["refusal"] is not None
        assert payload["refusal"]["reason_code"] == "MISSING_ATTACHMENT_EVIDENCE"
        assert payload["refusal"]["missing_source_types"] == ["attachment"]


def _seed_mixed_source_chunks(session: Session) -> tuple[str, str]:
    site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
    session.add(site)
    session.flush()

    protocol_doc = Document(
        source_site_id=site.id,
        document_external_id="doc:m4:ask:protocol",
        canonical_url="https://example.local/ask-protocol.pdf",
        title_he="פרוטוקול בטיחות",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    attachment_doc = Document(
        source_site_id=site.id,
        document_external_id="doc:m4:ask:attachment",
        canonical_url="https://example.local/ask-attachment.pdf",
        title_he="נספח הסכם",
        doc_kind="attachment",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    session.add_all([protocol_doc, attachment_doc])
    session.flush()

    protocol_ver = DocumentVersion(
        document_id=protocol_doc.id,
        sha256="1" * 64,
        byte_size=10,
        storage_uri="tree/ashdod/ask-protocol.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    attachment_ver = DocumentVersion(
        document_id=attachment_doc.id,
        sha256="2" * 64,
        byte_size=10,
        storage_uri="tree/ashdod/ask-attachment.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add_all([protocol_ver, attachment_ver])
    session.flush()

    protocol_extracted_id = _insert_extracted_document(session, document_version_id=protocol_ver.id, raw_text=HE_PROTOCOL_TEXT)
    attachment_extracted_id = _insert_extracted_document(
        session,
        document_version_id=attachment_ver.id,
        raw_text=HE_ATTACHMENT_TEXT,
    )

    search_service = SearchService(session)
    _index_chunks(
        search_service,
        document_id=protocol_doc.id,
        document_version_id=protocol_ver.id,
        extracted_document_id=protocol_extracted_id,
        raw_text=HE_PROTOCOL_TEXT,
        source_kind="protocol",
    )
    _index_chunks(
        search_service,
        document_id=attachment_doc.id,
        document_version_id=attachment_ver.id,
        extracted_document_id=attachment_extracted_id,
        raw_text=HE_ATTACHMENT_TEXT,
        source_kind="attachment",
    )

    protocol_chunk = session.execute(
        select(TextChunk.chunk_id)
        .where(TextChunk.document_version_id == protocol_ver.id)
        .order_by(TextChunk.chunk_index.asc())
    ).scalars().first()
    attachment_chunk = session.execute(
        select(TextChunk.chunk_id)
        .where(TextChunk.document_version_id == attachment_ver.id)
        .order_by(TextChunk.chunk_index.asc())
    ).scalars().first()

    if protocol_chunk is None or attachment_chunk is None:
        raise AssertionError("seeded M4 ask chunks were not created")

    session.commit()
    return protocol_chunk, attachment_chunk


def _insert_extracted_document(session: Session, *, document_version_id: int, raw_text: str) -> int:
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


def _index_chunks(
    search_service: SearchService,
    *,
    document_id: int,
    document_version_id: int,
    extracted_document_id: int,
    raw_text: str,
    source_kind: str,
) -> None:
    full_text, _pages, citation_map = parse_extracted_text(raw_text)
    chunks = build_chunks(
        document_version_id=document_version_id,
        text=full_text,
        citation_map=citation_map,
        source_kind=source_kind,
    )
    search_service.replace_document_chunks(
        document_id=document_id,
        document_version_id=document_version_id,
        extracted_document_id=extracted_document_id,
        source_kind=source_kind,
        chunks=chunks,
    )
