from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.extraction import PdfExtractionResult, parse_extracted_text, score_extraction_quality
from municipality.migrations import apply_all
from municipality.models import AssetManifest, Document, DocumentVersion, ExtractedDocument, SourceSite, TextChunk
from municipality.processing import ProcessingService
from municipality.search import SearchService


HE_PROTOCOL_TITLE = "פרוטוקול ועדת רווחה 2026"
HE_ATTACHMENT_TITLE = "נספח תקציבי 2026"
HE_PROTOCOL_BODY = (
    "ועדת רווחה\n"
    "העשייה הענפה "
    "במנהל השירותים החברתיים"
    "\fסיכום והחלטות"
)
HE_ATTACHMENT_BODY = "נספח תקציבי\nפירוט סעיפים"
HE_QUERY_ACTIVITY = "העשייה הענפה"
HE_QUERY_BUDGET = "תקציבי"


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


def test_process_pipeline_extracts_chunks_and_supports_search(tmp_path: Path) -> None:
    db_path = tmp_path / "m2.db"
    storage_root = tmp_path / "raw"
    storage_root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()

        protocol_doc = Document(
            source_site_id=site.id,
            document_external_id="doc:protocol",
            canonical_url="https://example.local/protocol-1.pdf",
            title_he=HE_PROTOCOL_TITLE,
            doc_kind="protocol_full",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        attachment_doc = Document(
            source_site_id=site.id,
            document_external_id="doc:attachment",
            canonical_url="https://example.local/attachment-1.pdf",
            title_he=HE_ATTACHMENT_TITLE,
            doc_kind="attachment",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        session.add_all([protocol_doc, attachment_doc])
        session.flush()

        protocol_ver = DocumentVersion(
            document_id=protocol_doc.id,
            sha256="a" * 64,
            byte_size=10,
            storage_uri="tree/ashdod/protocol-1.pdf",
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
        attachment_ver = DocumentVersion(
            document_id=attachment_doc.id,
            sha256="b" * 64,
            byte_size=10,
            storage_uri="tree/ashdod/attachment-1.pdf",
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
        session.add_all([protocol_ver, attachment_ver])
        session.flush()

        session.add_all(
            [
                AssetManifest(
                    source_site_id=site.id,
                    source_node_external_id="meeting:2026-01",
                    asset_external_id="doc:protocol",
                    asset_url=protocol_doc.canonical_url,
                    asset_kind="protocol_full",
                    title_he=protocol_doc.title_he,
                    mime_hint="application/pdf",
                    crawl_run_id=1,
                    discovered_at=datetime.utcnow(),
                ),
                AssetManifest(
                    source_site_id=site.id,
                    source_node_external_id="meeting:2026-01",
                    asset_external_id="doc:attachment",
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
                protocol_ver.storage_uri: HE_PROTOCOL_BODY,
                attachment_ver.storage_uri: HE_ATTACHMENT_BODY,
            }
        )

        processor = ProcessingService(session=session, storage_root=storage_root, extractor=extractor)
        processor.run()
        processor.run()

        extracted_rows = session.execute(select(ExtractedDocument)).scalars().all()
        chunk_rows = session.execute(select(TextChunk)).scalars().all()
        assert len(extracted_rows) == 2
        assert len(chunk_rows) > 0

        search = SearchService(session)
        hits = search.search(query=HE_QUERY_ACTIVITY, municipality_slug="ashdod")
        protocol_hits = search.search(
            query=HE_QUERY_BUDGET,
            municipality_slug="ashdod",
            source_type="attachment",
        )

        assert hits
        assert hits[0].source_type == "protocol"
        assert hits[0].meeting_external_id == "meeting:2026-01"
        assert protocol_hits
        assert all(hit.source_type == "attachment" for hit in protocol_hits)
