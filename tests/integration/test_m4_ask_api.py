from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi.responses import HTMLResponse
from starlette.requests import Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.api import (
    AskRequest,
    PDF_FIRST_ASK_SOURCE_TYPES,
    _ask_effective_scope,
    _enrich_pdf_first_reference_contexts,
    _run_ask,
    ask_playground_page,
    debug_playground_page,
    topic_tree_cache,
)
from municipality.chunking import build_chunks, normalize_for_search
from municipality.extraction import parse_extracted_text
from municipality.migrations import apply_all
from municipality.models import Document, DocumentVersion, ExtractedDocument, RetrievalArtifact, SourceSite
from municipality.rag_llm import MockRagProvider, RagLlmConfig, build_rag_llm_client
from municipality.rag_retrieval import RagContextChunk
from municipality.rag_dashboard_mock import CURRENT_QUESTION
from municipality.search import SearchService


HE_PDF_FIRST_TEXT = "הוחלט לאשר צעדי בטיחות בדרכים ברחבי העיר."
PDF_FIRST_SOURCE_TYPES = list(PDF_FIRST_ASK_SOURCE_TYPES)


def test_m4_ask_ui_playground_exposes_rtl_dashboard() -> None:
    page = ask_playground_page()
    assert isinstance(page, HTMLResponse)

    body = bytes(page.body).decode("utf-8")
    assert 'lang="he"' in body
    assert 'dir="rtl"' in body
    assert 'id="ask-playground-form"' in body
    assert 'class="SearchHeader"' in body
    assert 'class="startDiscoveryPanel panelCard"' in body
    assert 'class="mainCivicWorkspace"' in body
    assert 'class="endDetailDrawer panelCard"' in body
    assert CURRENT_QUESTION in body
    assert "חיפושים פופולריים" in body
    assert "מסננים" in body
    assert "מנהל" in body
    assert "תשובה" in body
    assert "החלטות עיקריות" in body
    assert "נושאים קשורים" in body
    assert "מקרא" in body
    assert "ציר זמן" in body
    assert "תכנון ובנייה" in body
    assert 'id="filter-modal" class="filterDialog" hidden' in body
    assert 'id="source-type-options" class="sourceTypeOptions"' in body
    assert 'type="checkbox" name="source_types" value="protocol"' in body
    assert 'type="checkbox" name="source_types" value="budget"' in body
    assert 'id="popular-popover" class="popularPopover" hidden' in body
    assert ".questionSearch input" in body
    assert "text-align: right;" in body
    assert "padding-inline-start: 24px;" in body
    assert "padding-inline-end: 58px;" in body
    assert ".decisionRow" in body
    assert "grid-template-columns: auto minmax(0, 1fr) auto;" in body
    assert ".sourceLink" in body
    assert "white-space: nowrap;" in body
    assert "align-items: center;" in body
    assert "אישור להפקדה בתנאים" in body
    assert "נקבעו תנאים להמשך קידום התכנית והפקדתה." in body
    assert "נדרש עדכון בדו״ח ההשפעה על הסביבה לפני שלב ההפקדה הסופית." in body
    assert "הצגת התכנית לציבור ושמיעת ההתנגדויות בכפוף לפרסום הודעה כדין." in body
    assert 'id="ask-debug-mode"' in body
    assert 'id="ask-playground-thresholds"' in body
    assert 'id="ask-playground-thresholds-json"' in body
    assert 'id="ask-show-extended"' in body
    assert 'id="ask-playground-extended-panel"' in body
    assert 'id="ask-playground-answer-sections"' in body
    assert 'id="ask-playground-provider-warning"' in body
    assert "isAlmostEqualText" in body
    assert "hasMeaningfulExtraInfo" in body
    assert "topic-root" in body
    assert "topic-child" in body
    assert "debug mode (show PDF-first debug)" in body
    assert "DASHBOARD_QUERY_ENDPOINT" in body
    assert "debug_mode" in body
    assert "ראיות ומקורות" not in body
    assert "מקציר" not in body
    assert "שאלה אחת במרכז" not in body
    assert "POC" not in body
    assert "scopeChip" not in body
    assert "source_types (retrieval filter)" not in body
    assert "required_source_types (coverage gate)" not in body
    assert '"/topic/tree/cache"' not in body


def test_m4_debug_ui_exposes_previous_ask_playground() -> None:
    page = debug_playground_page()
    assert isinstance(page, HTMLResponse)

    body = bytes(page.body).decode("utf-8")
    assert 'lang="en"' in body
    assert 'dir="ltr"' in body
    assert "Ask Playground" in body
    assert 'id="ask-playground-form"' in body
    assert 'id="ask-debug-mode"' in body
    assert 'id="ask-playground-thresholds"' in body
    assert 'id="ask-playground-thresholds-json"' in body
    assert 'id="ask-show-extended"' in body
    assert 'id="ask-playground-extended-panel"' in body
    assert 'id="ask-playground-answer-sections"' in body
    assert 'id="ask-playground-provider-warning"' in body
    assert "isAlmostEqualText" in body
    assert "hasMeaningfulExtraInfo" in body
    assert "topic-root" in body
    assert "topic-child" in body
    assert "debug mode (show PDF-first debug)" in body
    assert 'fetch("/ask"' in body
    assert "debug_mode" in body
    assert "source_types (retrieval filter)" not in body
    assert "required_source_types (coverage gate)" not in body
    assert '"/topic/tree/cache"' not in body


def test_m4_ask_api_returns_pdf_first_answer_with_citation_contract(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "m4_pdf_first_ask_answer.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        chunk_id, document_version_id = _seed_pdf_first_chunk(session)
        monkeypatch.setenv("RAG_ASK_DOCUMENT_VERSION_IDS", str(document_version_id))

        provider = MockRagProvider(
            responses_by_call_type={
                "answer": json.dumps(
                    {
                        "answer_status": "answer",
                        "topic_he": "בטיחות בדרכים",
                        "outcome_type": "approved",
                        "answer_he": "הוחלט לאשר צעדי בטיחות בדרכים.",
                        "claims": [
                            {
                                "text_he": "הוחלט לאשר צעדי בטיחות בדרכים",
                                "supporting_chunk_ids": [chunk_id],
                                "quoted_evidence": "הוחלט לאשר צעדי בטיחות בדרכים",
                            }
                        ],
                        "confidence": 0.92,
                        "limitations": ["מבוסס על קטע PDF-first אחד"],
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
        assert payload["answer_sections"]
        assert payload["extended_answer_sections"]
        assert payload["refusal"] is None
        assert {row["source_type"] for row in payload["citations"]} == {"pdf_first_protocol"}
        assert payload["citations"][0]["source_type_label_he"] == "פרוטוקול"
        assert "PDF-first" in payload["citations"][0]["source_type_semantic_description_he"]
        assert payload["citations"][0]["chunk_id"] == chunk_id
        assert payload["citations"][0]["document"]["version_id"] == document_version_id
        assert payload["retrieval"]["requested_source_types"] == PDF_FIRST_SOURCE_TYPES
        assert payload["retrieval"]["requested_source_type_details"][0]["label_he"] == "פרוטוקול"
        assert payload["retrieval"]["forced_pdf_first_scope"] is True
        assert payload["retrieval"]["effective_document_version_ids"] == [document_version_id]
        assert payload["scoring"]["answer_generation_route"] == "pdf_first_dictalm"
        thresholds = payload["debug"]["thresholds"]
        assert thresholds["debug_payload_kind"] == "pdf_first_debug"
        assert thresholds["obsolete_legacy_thresholds_hidden"] is True
        assert payload["debug"]["retrieval_trace"]["pdf_first_direct"] is True


def test_m4_ask_api_returns_pdf_first_refusal_when_scope_has_no_chunks(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "m4_pdf_first_ask_refusal.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))
    monkeypatch.setenv("RAG_ASK_DOCUMENT_VERSION_IDS", "999")

    with Session(engine) as session:
        llm_client = build_rag_llm_client(
            config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}),
            provider=MockRagProvider(),
        )
        payload = _run_ask(
            request=AskRequest(question="מה אושר בעיר?", top_k=8, semantic_mode="off", debug_mode=True),
            db=session,
            llm_client=llm_client,
        )

        assert payload["status"] == "refusal"
        assert payload["answer"] is None
        assert payload["citations"] == []
        assert payload["retrieval"]["requested_source_types"] == PDF_FIRST_SOURCE_TYPES
        assert payload["retrieval"]["forced_pdf_first_scope"] is True
        assert payload["refusal"] is not None
        assert payload["refusal"]["reason_code"] == "INSUFFICIENT_EVIDENCE"
        assert payload["refusal"]["missing_source_types"] == PDF_FIRST_SOURCE_TYPES


def test_m4_ask_scope_resolves_numeric_question_date_to_pdf_first_document(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "m4_pdf_first_ask_date_scope.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))
    monkeypatch.delenv("RAG_ASK_DOCUMENT_VERSION_IDS", raising=False)

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()
        target_docver = _seed_pdf_first_scope_artifact(
            session,
            site_id=site.id,
            title="ישיבת-מועצה-2-22-מיום-02-02-22-pdfua",
            canonical_url="https://example.local/regular-2-22-02-02-22.pdf",
            source_kind="pdf_first_protocol",
            meeting_date="02/02/2022",
        )
        _seed_pdf_first_scope_artifact(
            session,
            site_id=site.id,
            title="ישיבת-מועצה-3-22-מיום-02-03-22-pdfua",
            canonical_url="https://example.local/regular-3-22-02-03-22.pdf",
            source_kind="pdf_first_protocol",
            meeting_date="02/03/2022",
        )
        session.commit()

        scope = _ask_effective_scope(
            AskRequest(question="מה נדון בישיבת מועצה מיום 2.2.2022?", muni="ashdod"),
            db=session,
        )

    assert scope["source_types"] == PDF_FIRST_SOURCE_TYPES
    assert scope["document_version_ids"] == [target_docver]
    assert scope["scope_reason"] == "question_date_match"
    assert scope["date_scope"]["applied"] is True
    assert scope["date_scope"]["extracted_dates"] == ["2022-02-02"]


def test_m4_ask_scope_uses_selected_source_types_as_allowed_filters() -> None:
    scope = _ask_effective_scope(
        AskRequest(question="מה קרה ליד פארק לכיש?", source_types=["protocol", "budget"]),
    )

    assert scope["source_types"] == ["protocol", "pdf_first_protocol", "pdf_first_v4_protocol", "budget"]
    assert scope["required_source_types"] == []
    assert scope["forced_pdf_first_scope"] is False
    assert scope["scope_reason"] == "request_source_types"


def test_m4_ask_scope_blocks_unmatched_numeric_question_date(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "m4_pdf_first_ask_date_no_match.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))
    monkeypatch.delenv("RAG_ASK_DOCUMENT_VERSION_IDS", raising=False)

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()
        _seed_pdf_first_scope_artifact(
            session,
            site_id=site.id,
            title="ישיבת-מועצה-2-22-מיום-02-02-22-pdfua",
            canonical_url="https://example.local/regular-2-22-02-02-22.pdf",
            source_kind="pdf_first_protocol",
            meeting_date="02/02/2022",
        )
        session.commit()

        scope = _ask_effective_scope(
            AskRequest(question="מה נדון בישיבת מועצה מיום 31.12.2099?", muni="ashdod"),
            db=session,
        )

    assert scope["document_version_ids"] == []
    assert scope["scope_reason"] == "question_date_no_match"
    assert scope["date_scope"]["applied"] is False
    assert scope["date_scope"]["extracted_dates"] == ["2099-12-31"]


def test_topic_tree_cache_endpoint_is_retired(tmp_path: Path) -> None:
    db_path = tmp_path / "m4_topic_tree_cache_retired.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))
    request = Request({"type": "http", "method": "GET", "path": "/topic/tree/cache", "headers": []})

    with Session(engine) as session:
        payload = topic_tree_cache(request=request, limit=5000, db=session)

    assert payload["count"] == 0
    assert payload["roots"] == []
    assert payload["status"] == "retired"
    assert payload["replacement_endpoint"] == "/semantic/tree"


def test_pdf_first_reference_enrichment_links_matching_attachment_only(tmp_path: Path) -> None:
    db_path = tmp_path / "m4_pdf_first_reference_enrichment.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()
        protocol_doc, protocol_version = _seed_document_with_version(
            session,
            site_id=site.id,
            external_id="doc:protocol-reference",
            title="פרוטוקול מועצה",
            doc_kind="protocol_full",
        )
        attachment_doc, attachment_version = _seed_document_with_version(
            session,
            site_id=site.id,
            external_id="doc:attachment-reference",
            title="נספח ועדת נכסים",
            doc_kind="attachment",
        )
        unrelated_doc, unrelated_version = _seed_document_with_version(
            session,
            site_id=site.id,
            external_id="doc:attachment-unrelated",
            title="נספח ועדת נכסים אחר",
            doc_kind="attachment",
        )
        protocol_extracted_id = _insert_extracted_document(
            session,
            document_version_id=protocol_version.id,
            raw_text="פרוטוקול מישיבת ועדת נכסים מס1/22 מיום 17.1.22 - מצ\"ל",
        )
        attachment_extracted_id = _insert_extracted_document(
            session,
            document_version_id=attachment_version.id,
            raw_text="פרוטוקול מישיבת ועדת נכסים מס1/22 מיום 17.1.22 פירוט שימוש בנכס עירוני מחליטים לאשר שימוש בנכס",
        )
        unrelated_extracted_id = _insert_extracted_document(
            session,
            document_version_id=unrelated_version.id,
            raw_text="פרוטוקול מישיבת ועדת נכסים מס2/22 מיום 14.2.22 פירוט אחר",
        )
        _insert_pdf_first_artifact(
            session,
            artifact_id="pf-protocol-reference",
            document_id=protocol_doc.id,
            document_version_id=protocol_version.id,
            extracted_document_id=protocol_extracted_id,
            source_kind="pdf_first_protocol",
            title="ועדת נכסים",
            body="פרוטוקול מישיבת ועדת נכסים מס1/22 מיום 17.1.22 - מצ\"ל",
        )
        _insert_pdf_first_artifact(
            session,
            artifact_id="pf-attachment-reference",
            document_id=attachment_doc.id,
            document_version_id=attachment_version.id,
            extracted_document_id=attachment_extracted_id,
            source_kind="pdf_first_attachment",
            title="ועדת נכסים",
            body="פרוטוקול מישיבת ועדת נכסים מס1/22 מיום 17.1.22 פירוט שימוש בנכס עירוני מחליטים לאשר שימוש בנכס",
        )
        _insert_pdf_first_artifact(
            session,
            artifact_id="pf-attachment-unrelated",
            document_id=unrelated_doc.id,
            document_version_id=unrelated_version.id,
            extracted_document_id=unrelated_extracted_id,
            source_kind="pdf_first_attachment",
            title="ועדת נכסים",
            body="פרוטוקול מישיבת ועדת נכסים מס2/22 מיום 14.2.22 פירוט אחר",
        )
        session.commit()

        protocol_context = RagContextChunk(
            chunk_id="pf-protocol-reference",
            score=0.9,
            snippet="פרוטוקול מישיבת ועדת נכסים מס1/22 מיום 17.1.22 - מצ\"ל",
            citation="p.1",
            source_kind="pdf_first_protocol",
            document_id=protocol_doc.id,
            document_version_id=protocol_version.id,
            document_title=protocol_doc.title_he,
            document_url=f"/document-versions/{protocol_version.id}/source.pdf",
            municipality_slug="ashdod",
            start_page=1,
            end_page=1,
            chunk_text="raw_pdf_text: פרוטוקול מישיבת ועדת נכסים מס1/22 מיום 17.1.22 - מצ\"ל",
            primary_topic="ועדת נכסים",
            section_path=["ועדת נכסים", "outline_item"],
            artifact_kind="pdf_first_retrieval_chunk",
        )
        contexts, debug = _enrich_pdf_first_reference_contexts(
            db=session,
            contexts=[protocol_context],
            municipality_slug="ashdod",
            allowed_document_version_ids=None,
        )

    assert debug["applied"] is True
    assert debug["enrichment_map"] == {"pf-protocol-reference": ["pf-attachment-reference"]}
    assert [context.chunk_id for context in contexts] == ["pf-protocol-reference", "pf-attachment-reference"]
    assert contexts[1].source_kind == "pdf_first_attachment"
    assert contexts[1].chunk_id != "pf-attachment-unrelated"


def test_pdf_first_reference_enrichment_links_same_protocol_detail_by_subject(tmp_path: Path) -> None:
    db_path = tmp_path / "m4_pdf_first_same_protocol_detail.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()
        protocol_doc, protocol_version = _seed_document_with_version(
            session,
            site_id=site.id,
            external_id="doc:protocol-question-detail",
            title="פרוטוקול מועצה",
            doc_kind="protocol_full",
        )
        extracted_id = _insert_extracted_document(
            session,
            document_version_id=protocol_version.id,
            raw_text="שאילתא של ד\"ר לחמני בנושא \"מינוי מועצה דתית\" - מצ\"ל",
        )
        _insert_pdf_first_artifact(
            session,
            artifact_id="pf-question-header",
            document_id=protocol_doc.id,
            document_version_id=protocol_version.id,
            extracted_document_id=extracted_id,
            source_kind="pdf_first_protocol",
            title="שאילתות",
            body="שאילתא של ד\"ר לחמני בנושא \"מינוי מועצה דתית\" - מצ\"ל",
        )
        _insert_pdf_first_artifact(
            session,
            artifact_id="pf-question-detail",
            document_id=protocol_doc.id,
            document_version_id=protocol_version.id,
            extracted_document_id=extracted_id,
            source_kind="pdf_first_protocol",
            title="דת ושירותי דת",
            body=(
                "שאילתא של ד\"ר לחמני בנושא \"מינוי מועצה דתית\" גב' דינה בר אולפן מקריאה "
                "את תשובת ראש העיר לשאילתא של ד\"ר לחמני בנושא \"מינוי מועצה דתית\""
            ),
        )
        _insert_pdf_first_artifact(
            session,
            artifact_id="pf-question-unrelated",
            document_id=protocol_doc.id,
            document_version_id=protocol_version.id,
            extracted_document_id=extracted_id,
            source_kind="pdf_first_protocol",
            title="שאילתות",
            body="שאילתה של עו\"ד גלבר בנושא \"תקציב מכבי אשדוד\" תשובת ראש העיר",
        )
        session.commit()

        protocol_context = RagContextChunk(
            chunk_id="pf-question-header",
            score=0.9,
            snippet="שאילתא של ד\"ר לחמני בנושא \"מינוי מועצה דתית\" - מצ\"ל",
            citation="p.1",
            source_kind="pdf_first_protocol",
            document_id=protocol_doc.id,
            document_version_id=protocol_version.id,
            document_title=protocol_doc.title_he,
            document_url=f"/document-versions/{protocol_version.id}/source.pdf",
            municipality_slug="ashdod",
            start_page=1,
            end_page=1,
            chunk_text="raw_pdf_text: שאילתא של ד\"ר לחמני בנושא \"מינוי מועצה דתית\" - מצ\"ל",
            primary_topic="שאילתות",
            section_path=["שאילתות", "outline_item"],
            artifact_kind="pdf_first_retrieval_chunk",
        )
        contexts, debug = _enrich_pdf_first_reference_contexts(
            db=session,
            contexts=[protocol_context],
            municipality_slug="ashdod",
            allowed_document_version_ids=None,
        )

    assert debug["applied"] is True
    assert debug["enrichment_map"] == {"pf-question-header": ["pf-question-detail"]}
    assert [context.chunk_id for context in contexts] == ["pf-question-header", "pf-question-detail"]
    assert contexts[1].source_kind == "pdf_first_protocol"
    assert contexts[1].chunk_id != "pf-question-unrelated"


def _seed_pdf_first_chunk(session: Session) -> tuple[str, int]:
    site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
    session.add(site)
    session.flush()

    document = Document(
        source_site_id=site.id,
        document_external_id="doc:m4:ask:pdf-first",
        canonical_url="https://example.local/ask-pdf-first.pdf",
        title_he="פרוטוקול PDF-first",
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
        storage_uri="tree/ashdod/ask-pdf-first.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add(version)
    session.flush()

    extracted_id = _insert_extracted_document(session, document_version_id=version.id, raw_text=HE_PDF_FIRST_TEXT)
    _index_pdf_first_chunks(
        SearchService(session),
        document_id=document.id,
        document_version_id=version.id,
        extracted_document_id=extracted_id,
        raw_text=HE_PDF_FIRST_TEXT,
    )

    chunk_id = session.execute(
        select(RetrievalArtifact.artifact_id)
        .where(RetrievalArtifact.document_version_id == version.id)
        .where(RetrievalArtifact.source_kind == "pdf_first_protocol")
        .order_by(RetrievalArtifact.ordinal.asc())
    ).scalars().first()
    if chunk_id is None:
        raise AssertionError("seeded PDF-first ask chunk was not created")

    session.commit()
    return chunk_id, version.id


def _seed_document_with_version(
    session: Session,
    *,
    site_id: int,
    external_id: str,
    title: str,
    doc_kind: str,
) -> tuple[Document, DocumentVersion]:
    document = Document(
        source_site_id=site_id,
        document_external_id=external_id,
        canonical_url=f"https://example.local/{external_id}.pdf",
        title_he=title,
        doc_kind=doc_kind,
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    session.add(document)
    session.flush()
    version = DocumentVersion(
        document_id=document.id,
        sha256=(external_id.replace(":", "") * 64)[:64],
        byte_size=10,
        storage_uri=f"tree/ashdod/{external_id}.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add(version)
    session.flush()
    return document, version


def _insert_pdf_first_artifact(
    session: Session,
    *,
    artifact_id: str,
    document_id: int,
    document_version_id: int,
    extracted_document_id: int,
    source_kind: str,
    title: str,
    body: str,
) -> None:
    retrieval_text = f"canonical_topic_label_he: {title}\nstructural_role: outline_item\nraw_pdf_text: {body}"
    artifact = RetrievalArtifact(
        artifact_id=artifact_id,
        document_id=document_id,
        document_version_id=document_version_id,
        extracted_document_id=extracted_document_id,
        section_id=None,
        source_kind=source_kind,
        artifact_kind="pdf_first_retrieval_chunk",
        ordinal=1,
        title_he=title,
        committee_name=None,
        meeting_date=None,
        header_path_json=json.dumps([title, "outline_item"], ensure_ascii=False),
        body_text=body,
        retrieval_text=retrieval_text,
        retrieval_text_norm=normalize_for_search(retrieval_text),
        start_offset=0,
        end_offset=len(body),
        start_page=1,
        end_page=1,
        citation_label="p.1",
        trigram_count=1,
        metadata_json=None,
    )
    session.add(artifact)
    session.flush()


def _seed_pdf_first_scope_artifact(
    session: Session,
    *,
    site_id: int,
    title: str,
    canonical_url: str,
    source_kind: str,
    meeting_date: str,
) -> int:
    document = Document(
        source_site_id=site_id,
        document_external_id=f"doc:{title}",
        canonical_url=canonical_url,
        title_he=title,
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    session.add(document)
    session.flush()
    version = DocumentVersion(
        document_id=document.id,
        sha256=(str(document.id) * 64)[:64],
        byte_size=10,
        storage_uri=f"tree/ashdod/{title}.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add(version)
    session.flush()
    extracted_id = _insert_extracted_document(
        session,
        document_version_id=version.id,
        raw_text=HE_PDF_FIRST_TEXT,
    )
    artifact = RetrievalArtifact(
        artifact_id=f"scope-{version.id}",
        document_id=document.id,
        document_version_id=version.id,
        extracted_document_id=extracted_id,
        section_id=None,
        source_kind=source_kind,
        artifact_kind="pdf_first_retrieval_chunk",
        ordinal=1,
        title_he="scope fixture",
        committee_name=None,
        meeting_date=meeting_date,
        header_path_json=json.dumps(["scope fixture"]),
        body_text=HE_PDF_FIRST_TEXT,
        retrieval_text=HE_PDF_FIRST_TEXT,
        retrieval_text_norm=HE_PDF_FIRST_TEXT,
        start_offset=0,
        end_offset=len(HE_PDF_FIRST_TEXT),
        start_page=1,
        end_page=1,
        citation_label="p.1",
        trigram_count=1,
        metadata_json=None,
    )
    session.add(artifact)
    session.flush()
    return int(version.id)


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


def _index_pdf_first_chunks(
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
        source_kind="pdf_first_protocol",
    )
    for chunk in chunks:
        chunk["artifact_kind"] = "pdf_first_retrieval_chunk"
        chunk["title_he"] = "בטיחות בדרכים"
        chunk["header_path_json"] = json.dumps(["בטיחות בדרכים", "vote_or_result"], ensure_ascii=False)
    search_service.replace_document_chunks(
        document_id=document_id,
        document_version_id=document_version_id,
        extracted_document_id=extracted_document_id,
        source_kind="pdf_first_protocol",
        chunks=chunks,
    )
