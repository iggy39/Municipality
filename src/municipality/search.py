from __future__ import annotations

from typing import Any, Sequence

from municipality.artifact_search import ArtifactSearchService
from municipality.chunking import normalize_for_search
from municipality.search_common import (
    FTS_TOKEN_LIMIT,
    LEXICAL_FTS_WEIGHT,
    LEXICAL_TRIGRAM_WEIGHT,
    SEARCH_FALLBACK_CONTAINS_LIMIT,
    SEARCH_FTS_CANDIDATE_LIMIT,
    SEARCH_TRIGRAM_CANDIDATE_LIMIT,
    SEMANTIC_OVERLAP_WEIGHT,
    SEMANTIC_SPECIFICITY_WEIGHT,
    SearchHit,
    SemanticDebugNode,
    _build_snippet,
    _clamp,
    _descendant_node_ids,
    _fts_rank_to_score,
    _semantic_path_labels,
    _semantic_text_match_score,
    search_thresholds_snapshot,
)


class SearchService(ArtifactSearchService):
    def replace_document_chunks(
        self,
        *,
        document_id: int,
        document_version_id: int,
        extracted_document_id: int,
        source_kind: str,
        chunks: Sequence[dict[str, Any]],
    ) -> None:
        artifacts: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks):
            chunk_id = str(chunk.get("chunk_id") or "").strip()
            chunk_text = str(chunk.get("chunk_text") or "").strip()
            if not chunk_id or not chunk_text:
                continue
            artifacts.append(
                {
                    "artifact_id": chunk_id,
                    "source_kind": source_kind,
                    "artifact_kind": chunk.get("artifact_kind") or "section_unit",
                    "ordinal": int(chunk.get("chunk_index") or index),
                    "title_he": chunk.get("title_he"),
                    "committee_name": chunk.get("committee_name"),
                    "meeting_date": chunk.get("meeting_date"),
                    "header_path_json": chunk.get("header_path_json") or "[]",
                    "body_text": chunk.get("body_text") or chunk_text,
                    "retrieval_text": chunk_text,
                    "retrieval_text_norm": chunk.get("chunk_text_norm") or normalize_for_search(chunk_text),
                    "start_offset": int(chunk.get("start_offset") or 0),
                    "end_offset": int(chunk.get("end_offset") or len(chunk_text)),
                    "start_page": chunk.get("start_page"),
                    "end_page": chunk.get("end_page"),
                    "citation_label": chunk.get("citation_label"),
                    "trigrams": list(chunk.get("trigrams") or []),
                    "trigram_count": int(chunk.get("trigram_count") or 0),
                    "metadata_json": chunk.get("metadata_json") or "{}",
                    "section_id": chunk.get("section_id"),
                }
            )

        self.replace_document_artifacts(
            document_id=document_id,
            document_version_id=document_version_id,
            extracted_document_id=extracted_document_id,
            artifacts=artifacts,
        )
