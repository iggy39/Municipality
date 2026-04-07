from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.chunking import build_chunks
from municipality.decision_context import DecisionContextService
from municipality.decisions import DecisionExtractionService
from municipality.embeddings import ChunkEmbeddingService
from municipality.extraction import PdfTextExtractor
from municipality.models import Document, DocumentVersion, ExtractedDocument, PipelineRun, PipelineRunStep, SourceSite
from municipality.search import SearchService
from municipality.semantic_service import SemanticService
from municipality.storage import RawStorage


@dataclass(slots=True)
class SemanticEnrichmentPolicy:
    enabled: bool = True
    allowed_source_kinds: tuple[str, ...] = ("protocol",)
    min_quality_score: float = 0.55
    min_text_chars: int = 200

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> SemanticEnrichmentPolicy:
        source = env if env is not None else os.environ
        enabled = _env_bool(source.get("SEMANTIC_ENRICHMENT_ENABLED"), default=True)
        allowed_raw = (source.get("SEMANTIC_ALLOWED_SOURCE_KINDS") or "protocol").strip()
        allowed_source_kinds = tuple(
            kind for kind in ((part or "").strip().casefold() for part in allowed_raw.split(",")) if kind
        ) or ("protocol",)
        return cls(
            enabled=enabled,
            allowed_source_kinds=allowed_source_kinds,
            min_quality_score=_env_float(source.get("SEMANTIC_MIN_QUALITY_SCORE"), default=0.55, min_value=0.0, max_value=1.0),
            min_text_chars=_env_int(source.get("SEMANTIC_MIN_TEXT_CHARS"), default=200, min_value=0, max_value=20000),
        )

    def evaluate(
        self,
        *,
        source_kind: str,
        quality_score: float | None,
        extracted_text: str,
        chunk_count: int,
    ) -> tuple[bool, str]:
        if not self.enabled:
            return False, "DISABLED_BY_POLICY"
        normalized_source_kind = (source_kind or "").strip().casefold()
        if normalized_source_kind not in set(self.allowed_source_kinds):
            return False, f"SKIP_SOURCE_KIND:{normalized_source_kind or 'unknown'}"
        if chunk_count <= 0:
            return False, "NO_CHUNKS"
        text_length = len((extracted_text or "").strip())
        if text_length < self.min_text_chars:
            return False, f"TEXT_TOO_SHORT:{text_length}"
        score_value = 0.0 if quality_score is None else float(quality_score)
        if score_value < self.min_quality_score:
            return False, f"LOW_QUALITY_SCORE:{score_value:.2f}"
        return True, "RUN"


class ProcessingService:
    def __init__(
        self,
        *,
        session: Session,
        storage_root: Path,
        extractor: PdfTextExtractor | None = None,
        decision_extraction: DecisionExtractionService | None = None,
        decision_context_service: DecisionContextService | None = None,
        chunk_embedding_service: ChunkEmbeddingService | None = None,
        semantic_service: SemanticService | None = None,
        semantic_policy: SemanticEnrichmentPolicy | None = None,
    ):
        self.session = session
        self.storage = RawStorage(storage_root)
        self.extractor = extractor or PdfTextExtractor()
        self.search = SearchService(session)
        self.decision_extraction = decision_extraction or DecisionExtractionService(session)
        self.decision_context_service = decision_context_service or DecisionContextService(session)
        self.chunk_embedding_service = chunk_embedding_service or ChunkEmbeddingService(session)
        self.semantic_service = semantic_service or SemanticService(session)
        self.semantic_policy = semantic_policy or SemanticEnrichmentPolicy.from_env()

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

                source_kind = _source_kind_for_document(document.doc_kind)
                self.decision_extraction.process_document(
                    document=document,
                    document_version=document_version,
                    extracted_text=extraction.full_text,
                    citation_map=extraction.citation_map,
                    source_kind=source_kind,
                )

                if not extraction.ok or extracted_row.id is None:
                    step.status = "failed"
                    step.detail = extraction.error_code or "EXTRACTION_FAILED"
                    continue

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

                embedding_step = PipelineRunStep(
                    run_id=run.id,
                    step_name="chunk_embedding_index",
                    status="running",
                    item_ref=document.canonical_url,
                )
                self.session.add(embedding_step)
                self.session.flush()

                try:
                    embedding_result = self.chunk_embedding_service.index_chunks(chunks=chunks)
                    if not embedding_result.enabled:
                        embedding_step.status = "skipped"
                        embedding_step.detail = embedding_result.error_text or "DISABLED"
                    elif embedding_result.error_text:
                        embedding_step.status = "failed"
                        embedding_step.detail = embedding_result.error_text
                    else:
                        embedding_step.status = "completed"
                        embedding_step.detail = (
                            f"created={embedding_result.created}; cached={embedding_result.cached}; total={embedding_result.total}"
                        )
                except Exception as exc:
                    embedding_step.status = "failed"
                    embedding_step.detail = f"UNEXPECTED_ERROR:{exc.__class__.__name__}"

                semantic_step = PipelineRunStep(
                    run_id=run.id,
                    step_name="semantic_enrichment",
                    status="running",
                    item_ref=document.canonical_url,
                )
                self.session.add(semantic_step)
                self.session.flush()

                should_run_semantic, semantic_reason = self.semantic_policy.evaluate(
                    source_kind=source_kind,
                    quality_score=quality_score,
                    extracted_text=extraction.full_text,
                    chunk_count=len(chunks),
                )
                if not should_run_semantic:
                    semantic_step.status = "skipped"
                    semantic_step.detail = semantic_reason
                else:
                    try:
                        with self.session.begin_nested():
                            semantic_result = self.semantic_service.run_for_document(
                                source_site_id=document.source_site_id,
                                document_id=document.id,
                                document_version_id=document_version.id,
                                source_kind=source_kind,
                                extracted_text=extraction.full_text,
                                citation_map=extraction.citation_map,
                            )

                        semantic_step.status = "completed" if semantic_result.status == "completed" else "failed"
                        semantic_step.detail = (
                            f"run_id={semantic_result.run_id}; status={semantic_result.status}; "
                            f"from_cache={semantic_result.from_cache}; api_calls={semantic_result.api_call_count}; "
                            f"accepted_nodes={semantic_result.accepted_nodes}; aliases={semantic_result.aliases}; "
                            f"mentions={semantic_result.mentions}; chunk_links={semantic_result.chunk_links}; "
                            f"decision_links={semantic_result.decision_links}; rejects={semantic_result.reject_rows}; "
                            f"validation_issues={semantic_result.validation_issues}"
                        )
                    except Exception as exc:
                        semantic_step.status = "failed"
                        semantic_step.detail = f"UNEXPECTED_ERROR:{exc.__class__.__name__}"

                decision_context_step = PipelineRunStep(
                    run_id=run.id,
                    step_name="decision_context_linking",
                    status="running",
                    item_ref=document.canonical_url,
                )
                self.session.add(decision_context_step)
                self.session.flush()

                try:
                    with self.session.begin_nested():
                        context_result = self.decision_context_service.process_document(
                            source_document_id=document.id,
                            document_version_id=document_version.id,
                            source_kind=source_kind,
                        )
                    decision_context_step.status = "completed"
                    decision_context_step.detail = f"contexts={int(context_result.get('contexts', 0))}"
                except Exception as exc:
                    decision_context_step.status = "failed"
                    decision_context_step.detail = f"UNEXPECTED_ERROR:{exc.__class__.__name__}"
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


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(value: str | None, *, default: int, min_value: int, max_value: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(min_value, min(parsed, max_value))


def _env_float(value: str | None, *, default: float, min_value: float, max_value: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(min_value, min(parsed, max_value))
