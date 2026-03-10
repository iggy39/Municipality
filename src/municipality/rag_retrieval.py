from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from municipality.chunking import normalize_for_search


@dataclass(slots=True)
class RagContextChunk:
    chunk_id: str
    score: float
    snippet: str
    citation: str | None
    source_kind: str
    document_id: int
    document_title: str
    document_url: str
    municipality_slug: str
    meeting_external_id: str | None
    start_page: int | None
    end_page: int | None


@dataclass(slots=True)
class RagRetrievalResult:
    query: str
    normalized_query: str
    top_k: int
    requested_source_kinds: list[str]
    contexts: list[RagContextChunk]

    @property
    def source_kinds(self) -> set[str]:
        return {context.source_kind for context in self.contexts}


class RagRetrievalService:
    def __init__(self, *, search_service):
        self.search_service = search_service

    def retrieve(
        self,
        *,
        query: str,
        top_k: int = 8,
        municipality_slug: str | None = None,
        source_kinds: list[str] | None = None,
        year: int | None = None,
        topic: str | None = None,
        semantic_mode: str = "off",
    ) -> RagRetrievalResult:
        normalized_query = normalize_for_search(query)
        effective_top_k = max(1, top_k)
        requested_source_kinds = _normalize_source_kinds(source_kinds)

        if not normalized_query:
            return RagRetrievalResult(
                query=query,
                normalized_query=normalized_query,
                top_k=effective_top_k,
                requested_source_kinds=requested_source_kinds,
                contexts=[],
            )

        initial_limit = max(effective_top_k * 3, effective_top_k)
        hits = self.search_service.search(
            query=query,
            municipality_slug=municipality_slug,
            source_type=None,
            year=year,
            topic=topic,
            semantic_mode=semantic_mode,
            limit=initial_limit,
        )

        selected_hits = _dedupe_hits(hits)
        if requested_source_kinds:
            selected_hits = [hit for hit in selected_hits if hit.source_type in requested_source_kinds]

        missing_source_kinds = set(requested_source_kinds) - {hit.source_type for hit in selected_hits}
        for source_kind in sorted(missing_source_kinds):
            source_hits = self.search_service.search(
                query=query,
                municipality_slug=municipality_slug,
                source_type=source_kind,
                year=year,
                topic=topic,
                semantic_mode=semantic_mode,
                limit=effective_top_k,
            )
            selected_hits.extend(source_hits)
            selected_hits = _dedupe_hits(selected_hits)

        selected_hits.sort(key=lambda row: row.score, reverse=True)
        contexts = [_to_context(row) for row in selected_hits[:effective_top_k]]
        return RagRetrievalResult(
            query=query,
            normalized_query=normalized_query,
            top_k=effective_top_k,
            requested_source_kinds=requested_source_kinds,
            contexts=contexts,
        )


def _normalize_source_kinds(source_kinds: list[str] | None) -> list[str]:
    if not source_kinds:
        return []
    accepted = {"protocol", "attachment", "other"}
    out: list[str] = []
    seen: set[str] = set()
    for source_kind in source_kinds:
        normalized = (source_kind or "").strip().casefold()
        if normalized in accepted and normalized not in seen:
            out.append(normalized)
            seen.add(normalized)
    return out


def _dedupe_hits(hits: list[Any]) -> list[Any]:
    by_chunk_id: dict[str, Any] = {}
    for hit in hits:
        existing = by_chunk_id.get(hit.chunk_id)
        if existing is None or hit.score > existing.score:
            by_chunk_id[hit.chunk_id] = hit
    deduped = list(by_chunk_id.values())
    deduped.sort(key=lambda row: row.score, reverse=True)
    return deduped


def _to_context(hit) -> RagContextChunk:
    return RagContextChunk(
        chunk_id=hit.chunk_id,
        score=hit.score,
        snippet=hit.snippet,
        citation=hit.citation,
        source_kind=hit.source_type,
        document_id=hit.document_id,
        document_title=hit.document_title,
        document_url=hit.document_url,
        municipality_slug=hit.municipality_slug,
        meeting_external_id=hit.meeting_external_id,
        start_page=hit.start_page,
        end_page=hit.end_page,
    )
