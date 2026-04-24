from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from municipality.artifact_embeddings import ArtifactEmbeddingService
from municipality.artifact_search import ArtifactSearchService
from municipality.embeddings import ChunkEmbeddingService
from sqlalchemy import select

from municipality.models import DocumentSection, RetrievalArtifact
from municipality.rag_structure import build_structured_document


@dataclass(slots=True)
class StructuredIndexingResult:
    section_count: int
    artifact_count: int
    metadata: dict[str, Any]
    embedding_created: int = 0
    embedding_cached: int = 0
    embedding_enabled: bool = False
    embedding_error_text: str | None = None


class StructuredIndexingService:
    def __init__(
        self,
        session: Session,
        *,
        search_service: ArtifactSearchService | None = None,
        embedding_service: ChunkEmbeddingService | None = None,
    ):
        self.session = session
        self.search_service = search_service or ArtifactSearchService(session)
        self.embedding_service = embedding_service or ArtifactEmbeddingService(session)

    def replace_document_structure(
        self,
        *,
        document_id: int,
        document_version_id: int,
        extracted_document_id: int,
        document_title: str,
        text: str,
        citation_map: list[dict[str, int]],
        source_kind: str,
        pages: list[Any] | None = None,
    ) -> StructuredIndexingResult:
        build_result = build_structured_document(
            document_version_id=document_version_id,
            document_title=document_title,
            text=text,
            citation_map=citation_map,
            source_kind=source_kind,
            pages=pages,
        )

        existing_artifact_ids = list(
            self.session.execute(
                select(RetrievalArtifact.artifact_id).where(RetrievalArtifact.document_version_id == document_version_id)
            ).scalars().all()
        )

        self.session.query(DocumentSection).filter(DocumentSection.document_version_id == document_version_id).delete()
        self.embedding_service.delete_chunk_embeddings(chunk_ids=existing_artifact_ids)
        for section in build_result.sections:
            self.session.add(
                DocumentSection(
                    section_id=section["section_id"],
                    document_id=document_id,
                    document_version_id=document_version_id,
                    extracted_document_id=extracted_document_id,
                    parent_section_id=section.get("parent_section_id"),
                    source_kind=section["source_kind"],
                    node_type=section["node_type"],
                    header_text=section["header_text"],
                    header_text_norm=section["header_text_norm"],
                    header_level=section["header_level"],
                    section_path_json=section["section_path_json"],
                    body_text=section.get("body_text"),
                    start_offset=section["start_offset"],
                    end_offset=section["end_offset"],
                    start_page=section.get("start_page"),
                    end_page=section.get("end_page"),
                    ordinal=section["ordinal"],
                    confidence=section["confidence"],
                    metadata_json=section.get("metadata_json"),
                )
            )

        self.search_service.replace_document_artifacts(
            document_id=document_id,
            document_version_id=document_version_id,
            extracted_document_id=extracted_document_id,
            artifacts=build_result.artifacts,
        )
        embedding_result = self.embedding_service.index_chunks(
            chunks=[
                {
                    "chunk_id": artifact["artifact_id"],
                    "chunk_text": artifact["retrieval_text"],
                }
                for artifact in build_result.artifacts
            ]
        )
        return StructuredIndexingResult(
            section_count=len(build_result.sections),
            artifact_count=len(build_result.artifacts),
            metadata=build_result.metadata,
            embedding_created=embedding_result.created,
            embedding_cached=embedding_result.cached,
            embedding_enabled=embedding_result.enabled,
            embedding_error_text=embedding_result.error_text,
        )
