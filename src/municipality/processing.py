from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.chunking import build_chunks
from municipality.extraction import PdfTextExtractor
from municipality.models import Document, DocumentVersion, ExtractedDocument, PipelineRun, PipelineRunStep, SourceSite
from municipality.search import SearchService
from municipality.storage import RawStorage


class ProcessingService:
    def __init__(
        self,
        *,
        session: Session,
        storage_root: Path,
        extractor: PdfTextExtractor | None = None,
    ):
        self.session = session
        self.storage = RawStorage(storage_root)
        self.extractor = extractor or PdfTextExtractor()
        self.search = SearchService(session)

    def run(self, doc_id: int | None = None, municipality_slug: str | None = None) -> int:
        run = PipelineRun(
            run_type="process",
            municipality_slug=municipality_slug or "all",
            status="running",
        )
        self.session.add(run)
        self.session.flush()

        stmt = (
            select(DocumentVersion, Document, SourceSite)
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(SourceSite, SourceSite.id == Document.source_site_id)
            .where(Document.mime_hint == "application/pdf")
            .order_by(DocumentVersion.id.asc())
        )
        if doc_id is not None:
            stmt = stmt.where(Document.id == doc_id)
        if municipality_slug:
            stmt = stmt.where(SourceSite.municipality_slug == municipality_slug)

        rows = self.session.execute(stmt).all()
        for document_version, document, _source_site in rows:
            step = PipelineRunStep(
                run_id=run.id,
                step_name="extract_chunk_index",
                status="running",
                item_ref=document.canonical_url,
            )
            self.session.add(step)
            self.session.flush()

            try:
                payload = self.storage.read(document_version.storage_uri)
                extraction = self.extractor.extract(payload)
                extracted_row = self._upsert_extracted_document(document_version.id, extraction)
                self.session.flush()

                if not extraction.ok or extracted_row.id is None:
                    step.status = "failed"
                    step.detail = extraction.error_code or "EXTRACTION_FAILED"
                    continue

                source_kind = _source_kind_for_document(document.doc_kind)
                chunks = build_chunks(
                    document_version_id=document_version.id,
                    text=extraction.full_text,
                    citation_map=extraction.citation_map,
                    source_kind=source_kind,
                )
                self.search.replace_document_chunks(
                    document_id=document.id,
                    document_version_id=document_version.id,
                    extracted_document_id=extracted_row.id,
                    source_kind=source_kind,
                    chunks=chunks,
                )

                quality_score = extraction.quality_score if extraction.quality_score is not None else 0.0
                flags = ",".join(extraction.quality_flags)
                step.status = "completed"
                step.detail = f"chunks={len(chunks)}; quality={quality_score:.2f}; flags={flags or 'NONE'}"
            except Exception as exc:
                step.status = "failed"
                step.detail = f"UNEXPECTED_ERROR:{exc.__class__.__name__}"

        run.status = "completed"
        run.finished_at = datetime.utcnow()
        self.session.commit()
        return run.id

    def _upsert_extracted_document(self, document_version_id: int, extraction) -> ExtractedDocument:
        existing = self.session.execute(
            select(ExtractedDocument).where(ExtractedDocument.document_version_id == document_version_id)
        ).scalar_one_or_none()
        if existing is None:
            existing = ExtractedDocument(
                document_version_id=document_version_id,
                parser_name=extraction.parser_name,
                parser_version=extraction.parser_version,
                status="completed" if extraction.ok else "failed",
            )
            self.session.add(existing)

        pages = [
            {
                "page": page.page,
                "text": page.text,
                "start_offset": page.start_offset,
                "end_offset": page.end_offset,
            }
            for page in extraction.pages
        ]

        existing.parser_name = extraction.parser_name
        existing.parser_version = extraction.parser_version
        existing.status = "completed" if extraction.ok else "failed"
        existing.extracted_text = extraction.full_text
        existing.page_count = len(extraction.pages)
        existing.pages_json = json.dumps(pages, ensure_ascii=False)
        existing.citation_map_json = json.dumps(extraction.citation_map)
        existing.quality_score = extraction.quality_score
        existing.quality_flags_json = json.dumps(extraction.quality_flags)
        existing.quality_summary_json = json.dumps(extraction.quality_summary)
        existing.error_code = extraction.error_code
        existing.warning_text = extraction.warning_text
        existing.updated_at = datetime.utcnow()
        return existing


def _source_kind_for_document(doc_kind: str) -> str:
    if doc_kind.startswith("protocol"):
        return "protocol"
    if doc_kind == "attachment":
        return "attachment"
    return "other"
