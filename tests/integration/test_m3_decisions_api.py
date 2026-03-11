from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.api import decision_card_page, decision_detail, meeting_detail
from municipality.decisions import DecisionExtractionService
from municipality.extraction import PdfExtractionResult, parse_extracted_text, score_extraction_quality
from municipality.migrations import apply_all
from municipality.models import (
    AssetManifest,
    Decision,
    DecisionCitation,
    DecisionDocumentLink,
    Document,
    DocumentVersion,
    Meeting,
    SourceSite,
    TaxonomyNode,
)
from municipality.processing import ProcessingService


HE_AGENDA_ALIGNMENT = "סעיף התאמה"
HE_COUNCIL_MEETINGS = "ישיבות מועצה"
HE_REGULAR_MEETING_TITLE = "ישיבת מועצה רגילה 12/26 01.03.2026"
HE_ATTACHMENT_TITLE = "נספח תקציבי - מרץ 2026"
HE_PROTOCOL_EXTRACTED_TEXT = (
    "ישיבת מועצה רגילה מס' 12/26 "
    "בתאריך 01.03.2026\n"
    "סיכום והחלטות\n"
    "1. אישור תקציב החינוך "
    "לשנת 2026 הוחלט לאשר "
    "את התקציב. בעד/נגד/נמנע: 9/1/0\n"
    "2. מינוי ועדת ביקורת "
    "אושר פה אחד\n"
    "3. המשך טיפול בתחבורה "
    "עירונית בעד ונגד "
    "ללא מספרים\n"
)
HE_ATTACHMENT_EXTRACTED_TEXT = (
    "נספח תקציבי\n"
    "פירוט סעיפים ומועדים"
)


class StubExtractor:
    def __init__(self, by_uri: dict[str, str]):
        self.by_uri = by_uri

    def extract(self, payload: bytes) -> PdfExtractionResult:
        uri = payload.decode("utf-8")
        raw_text = self.by_uri[uri]
        full_text, pages, citation_map = parse_extracted_text(raw_text)
        quality_score, quality_flags, quality_summary = score_extraction_quality(full_text, pages)
        return PdfExtractionResult(
            ok=True,
            parser_name="stub",
            parser_version="test",
            full_text=full_text,
            pages=pages,
            citation_map=citation_map,
            quality_score=quality_score,
            quality_flags=quality_flags,
            quality_summary=quality_summary,
            error_code=None,
            warning_text=None,
        )


class StubFallbackClient:
    def is_configured(self) -> bool:
        return True

    def extract_decisions(self, *, source_text: str, text_offset: int = 0) -> list[dict]:
        rows: list[dict] = []
        cursor = 0
        for line in source_text.splitlines():
            start = source_text.find(line, cursor)
            if start < 0:
                continue
            end = start + len(line)
            cursor = end
            compact = line.strip()
            if compact.startswith("1. "):
                rows.append(
                    {
                        "decision_text": compact[3:],
                        "decision_number": "1",
                        "agenda_item": HE_AGENDA_ALIGNMENT,
                        "confidence": 0.95,
                        "start_offset": text_offset + start + 3,
                        "end_offset": text_offset + end,
                        "vote": {"for_count": 9, "against_count": 1, "abstain_count": 0, "unanimous": False},
                    }
                )
            elif compact.startswith("2. "):
                rows.append(
                    {
                        "decision_text": compact[3:],
                        "decision_number": "2",
                        "agenda_item": HE_AGENDA_ALIGNMENT,
                        "confidence": 0.95,
                        "start_offset": text_offset + start + 3,
                        "end_offset": text_offset + end,
                        "vote": {"for_count": None, "against_count": None, "abstain_count": None, "unanimous": True},
                    }
                )
        return rows


def test_m3_processing_builds_public_cited_decisions_and_api_payloads(tmp_path: Path) -> None:
    db_path = tmp_path / "m3.db"
    storage_root = tmp_path / "raw"
    storage_root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()

        session.add_all(
            [
                TaxonomyNode(
                    source_site_id=site.id,
                    node_external_id="topic:council",
                    parent_external_id=None,
                    title_he=HE_COUNCIL_MEETINGS,
                    canonical_url="https://example.local/topic",
                    node_type="topic_folder",
                    depth=1,
                    count_hint=None,
                    crawl_run_id=1,
                    discovered_at=datetime.utcnow(),
                ),
                TaxonomyNode(
                    source_site_id=site.id,
                    node_external_id="meeting:2026-03-01",
                    parent_external_id="topic:council",
                    title_he=HE_REGULAR_MEETING_TITLE,
                    canonical_url="https://example.local/meeting-2026-03-01",
                    node_type="meeting_folder",
                    depth=2,
                    count_hint=None,
                    crawl_run_id=1,
                    discovered_at=datetime.utcnow(),
                ),
            ]
        )
        session.flush()

        protocol_doc = Document(
            source_site_id=site.id,
            document_external_id="doc:protocol:m3",
            canonical_url="https://example.local/protocol-m3.pdf",
            title_he=HE_REGULAR_MEETING_TITLE,
            doc_kind="protocol_full",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        attachment_doc = Document(
            source_site_id=site.id,
            document_external_id="doc:attach:m3",
            canonical_url="https://example.local/attachment-m3.pdf",
            title_he=HE_ATTACHMENT_TITLE,
            doc_kind="attachment",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        session.add_all([protocol_doc, attachment_doc])
        session.flush()

        protocol_ver = DocumentVersion(
            document_id=protocol_doc.id,
            sha256="e" * 64,
            byte_size=11,
            storage_uri="tree/ashdod/protocol-m3.pdf",
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
        attachment_ver = DocumentVersion(
            document_id=attachment_doc.id,
            sha256="f" * 64,
            byte_size=10,
            storage_uri="tree/ashdod/attachment-m3.pdf",
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
        session.add_all([protocol_ver, attachment_ver])
        session.flush()

        session.add_all(
            [
                AssetManifest(
                    source_site_id=site.id,
                    source_node_external_id="meeting:2026-03-01",
                    asset_external_id="doc:protocol:m3",
                    asset_url=protocol_doc.canonical_url,
                    asset_kind="protocol_full",
                    title_he=protocol_doc.title_he,
                    mime_hint="application/pdf",
                    crawl_run_id=1,
                    discovered_at=datetime.utcnow(),
                ),
                AssetManifest(
                    source_site_id=site.id,
                    source_node_external_id="meeting:2026-03-01",
                    asset_external_id="doc:attach:m3",
                    asset_url=attachment_doc.canonical_url,
                    asset_kind="attachment",
                    title_he=attachment_doc.title_he,
                    mime_hint="application/pdf",
                    crawl_run_id=1,
                    discovered_at=datetime.utcnow(),
                ),
            ]
        )
        session.commit()

        (storage_root / "tree/ashdod").mkdir(parents=True, exist_ok=True)
        (storage_root / protocol_ver.storage_uri).write_bytes(protocol_ver.storage_uri.encode("utf-8"))
        (storage_root / attachment_ver.storage_uri).write_bytes(attachment_ver.storage_uri.encode("utf-8"))

        extractor = StubExtractor(
            {
                protocol_ver.storage_uri: HE_PROTOCOL_EXTRACTED_TEXT,
                attachment_ver.storage_uri: HE_ATTACHMENT_EXTRACTED_TEXT,
            }
        )
        decision_service = DecisionExtractionService(
            session,
            fallback_client=StubFallbackClient(),
            low_confidence_threshold=0.95,
        )
        processor = ProcessingService(
            session=session,
            storage_root=storage_root,
            extractor=extractor,
            decision_extraction=decision_service,
        )
        processor.run()

        meeting = session.execute(select(Meeting)).scalar_one()
        decisions = session.execute(select(Decision).where(Decision.meeting_id == meeting.id)).scalars().all()
        assert len(decisions) >= 2

        for decision in decisions:
            metadata = json.loads(decision.metadata_json or "{}")
            assert "fallback_used" in metadata
            assert "fallback_validation_reasons" in metadata
            assert metadata.get("fallback_used") is False
            assert metadata.get("api_model_used") is True
            assert metadata.get("api_model_provider") == "Bytez"
            assert metadata.get("api_model_name") == "google/gemini-2.5-pro"
            if decision.is_public:
                citations = session.execute(
                    select(DecisionCitation).where(DecisionCitation.decision_id == decision.id)
                ).scalars().all()
                assert citations

        assert any(json.loads(d.metadata_json or "{}").get("api_model_status") == "accepted" for d in decisions)

        linked_docs = session.execute(select(DecisionDocumentLink)).scalars().all()
        assert any(link.source_type == "attachment" for link in linked_docs)

        meeting_payload = meeting_detail(meeting.id, db=session)
        assert meeting_payload["meeting"]["meeting_external_id"] == "meeting:2026-03-01"
        assert meeting_payload["decisions"]
        assert all(d["citation_count"] >= 1 for d in meeting_payload["decisions"])

        model_decision = next(
            d for d in decisions if json.loads(d.metadata_json or "{}").get("api_model_status") == "accepted"
        )
        decision_payload = decision_detail(model_decision.id, db=session)
        assert decision_payload["citations"]
        assert decision_payload["decision"]["metadata"]["api_model_provider"] == "Bytez"
        assert decision_payload["linked_documents"]

        page = decision_card_page(model_decision.id, db=session)
        assert isinstance(page, HTMLResponse)
        body = bytes(page.body).decode("utf-8")
        assert 'dir="rtl"' in body
        assert "Fallback Bytez" not in body
        assert 'id="ask-panel"' in body
        assert 'id="ask-form"' in body
        assert 'fetch("/ask"' in body
        assert 'defaultMuni = "ashdod"' in body
        assert f'defaultTopic = "{HE_AGENDA_ALIGNMENT}"' in body
