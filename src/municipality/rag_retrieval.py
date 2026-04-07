from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from municipality.chunking import normalize_for_search
from municipality.embeddings import EmbeddingReranker
from municipality.rag_observability import build_retrieval_set_id, hash_text, log_rag_event


DECISION_PROBE_QUERY = "החלטות הוחלט אושר אושרה אושרו מאשרים"
HEBREW_TOKEN_RE = re.compile(r"[\u0590-\u05FF]{2,}")
DECISION_QUERY_MARKERS = {
    "הוחלט",
    "החלטה",
    "החלטות",
    "אושר",
    "אושרה",
    "אושרו",
    "מאשר",
    "מאשרים",
}
BROAD_SCOPE_TOKENS = {
    "עיר",
    "בעיר",
    "העיר",
    "עירייה",
    "העירייה",
    "בישיבה",
    "בוועדה",
}
DECISION_TEXT_MARKERS = {
    "הוחלט",
    "החלטה",
    "החלטות",
    "אושר",
    "אושרה",
    "אושרו",
    "אישר",
    "אישרה",
    "מאשר",
    "מאשרים",
}
BOILERPLATE_DECISION_PATTERNS = [
    re.compile(r"פרסום\s+(?:זמני|ראשון|שני)(?:\s+ו(?:זמני|ראשון|שני))?\s+בעיתונות"),
    re.compile(r"בית\s+העירייה"),
    re.compile(r"רח\s*['\"]"),
    re.compile(r"ת\s*\.?\s*ד\s*\.?"),
]


@dataclass(slots=True)
class RagContextChunk:
    chunk_id: str
    score: float
    snippet: str
    source_kind: str
    document_id: int
    document_title: str
    document_url: str
    municipality_slug: str
    citation: str | None = None
    meeting_external_id: str | None = None
    start_page: int | None = None
    end_page: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    chunk_index: int | None = None
    chunk_text: str = ""
    semantic_topic_labels: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RagRetrievalResult:
    query: str
    normalized_query: str
    top_k: int
    retrieval_set_id: str
    requested_source_kinds: list[str]
    contexts: list[RagContextChunk]
    debug_info: dict[str, Any] = field(default_factory=dict)

    @property
    def source_kinds(self) -> set[str]:
        return {context.source_kind for context in self.contexts}


class RagRetrievalService:
    def __init__(self, *, search_service, reranker: EmbeddingReranker | None = None):
        self.search_service = search_service
        self.reranker = reranker

    def retrieve(
        self,
        *,
        query: str,
        top_k: int = 8,
        municipality_slug: str | None = None,
        source_kinds: list[str] | None = None,
        year: int | None = None,
        topic: str | None = None,
        semantic_node_id: int | None = None,
        semantic_label: str | None = None,
        semantic_mode: str = "off",
        ask_request_id: str | None = None,
    ) -> RagRetrievalResult:
        normalized_query = normalize_for_search(query)
        effective_top_k = max(1, top_k)
        requested_source_kinds = _normalize_source_kinds(source_kinds)
        query_hash = hash_text(query)

        log_rag_event(
            "rag.retrieval.start",
            ask_request_id=ask_request_id,
            query_hash=query_hash,
            top_k=effective_top_k,
            requested_source_types=requested_source_kinds,
            municipality_slug=municipality_slug,
            year=year,
            topic=topic,
            semantic_node_id=semantic_node_id,
            semantic_label=semantic_label,
            semantic_mode=semantic_mode,
        )

        empty_retrieval_set_id = build_retrieval_set_id(
            normalized_query=normalized_query,
            requested_source_kinds=requested_source_kinds,
            top_k=effective_top_k,
            chunk_ids=[],
        )

        if not normalized_query:
            result = RagRetrievalResult(
                query=query,
                normalized_query=normalized_query,
                top_k=effective_top_k,
                retrieval_set_id=empty_retrieval_set_id,
                requested_source_kinds=requested_source_kinds,
                contexts=[],
                debug_info={
                    "candidate_limit": 0,
                    "broad_decision_query": False,
                    "lexical_top_k_chunk_ids": [],
                    "reranked_top_k_chunk_ids": [],
                    "embedding_rerank": {"enabled": False},
                },
            )
            log_rag_event(
                "rag.retrieval.result",
                ask_request_id=ask_request_id,
                query_hash=query_hash,
                retrieval_set_id=result.retrieval_set_id,
                context_count=0,
                requested_source_types=requested_source_kinds,
                retrieved_source_types=[],
                score_stats={"max": None, "min": None, "avg": None},
            )
            return result

        if self.reranker is not None:
            initial_limit = max(self.reranker.initial_candidate_limit(top_k=effective_top_k), effective_top_k * 3)
        else:
            initial_limit = max(effective_top_k * 3, effective_top_k)
        hits = self.search_service.search(
            query=query,
            municipality_slug=municipality_slug,
            source_type=None,
            year=year,
            topic=topic,
            semantic_node_id=semantic_node_id,
            semantic_label=semantic_label,
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
                semantic_node_id=semantic_node_id,
                semantic_label=semantic_label,
                semantic_mode=semantic_mode,
                limit=max(effective_top_k, min(initial_limit, effective_top_k * 6)),
            )
            selected_hits.extend(source_hits)
            selected_hits = _dedupe_hits(selected_hits)

        broad_decision_query = _is_broad_decision_query(normalized_query)
        if broad_decision_query:
            selected_hits = _augment_protocol_decision_hits(
                selected_hits=selected_hits,
                search_service=self.search_service,
                municipality_slug=municipality_slug,
                requested_source_kinds=requested_source_kinds,
                year=year,
                topic=topic,
                semantic_node_id=semantic_node_id,
                semantic_label=semantic_label,
                semantic_mode=semantic_mode,
                top_k=effective_top_k,
            )

        lexical_top_k_chunk_ids = [str(row.chunk_id) for row in selected_hits[:effective_top_k]]
        rerank_stats: dict[str, Any] = {"enabled": False}
        if self.reranker is not None and selected_hits:
            selected_hits, rerank_stats = self.reranker.rerank_hits(
                query=query,
                hits=selected_hits,
                top_k=effective_top_k,
            )

        selected_hits.sort(key=lambda row: row.score, reverse=True)
        if broad_decision_query:
            selected_hits = _prioritize_protocol_document_coverage(selected_hits, limit=effective_top_k)
        contexts = [_to_context(row) for row in selected_hits[:effective_top_k]]
        retrieval_set_id = build_retrieval_set_id(
            normalized_query=normalized_query,
            requested_source_kinds=requested_source_kinds,
            top_k=effective_top_k,
            chunk_ids=[row.chunk_id for row in contexts],
        )
        result = RagRetrievalResult(
            query=query,
            normalized_query=normalized_query,
            top_k=effective_top_k,
            retrieval_set_id=retrieval_set_id,
            requested_source_kinds=requested_source_kinds,
            contexts=contexts,
            debug_info={
                "candidate_limit": initial_limit,
                "broad_decision_query": broad_decision_query,
                "lexical_top_k_chunk_ids": lexical_top_k_chunk_ids,
                "reranked_top_k_chunk_ids": [str(row.chunk_id) for row in contexts],
                "embedding_rerank": dict(rerank_stats),
            },
        )
        scores = [row.score for row in contexts]
        score_stats = {
            "max": round(max(scores), 6) if scores else None,
            "min": round(min(scores), 6) if scores else None,
            "avg": round((sum(scores) / len(scores)), 6) if scores else None,
        }
        log_rag_event(
            "rag.retrieval.result",
            ask_request_id=ask_request_id,
            query_hash=query_hash,
            retrieval_set_id=result.retrieval_set_id,
            context_count=len(contexts),
            requested_source_types=requested_source_kinds,
            retrieved_source_types=sorted(result.source_kinds),
            chunk_ids=[row.chunk_id for row in contexts],
            score_stats=score_stats,
            embedding_rerank=rerank_stats,
        )
        return result


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
    semantic_topic_labels: list[str] = []
    seen_topic_labels: set[str] = set()
    for node in getattr(hit, "semantic_nodes", []) or []:
        node_kind = str(getattr(node, "kind", "") or "").strip().casefold()
        if node_kind != "topic":
            continue
        label = str(getattr(node, "label", "") or "").strip()
        if not label:
            continue
        key = normalize_for_search(label)
        if not key or key in seen_topic_labels:
            continue
        seen_topic_labels.add(key)
        semantic_topic_labels.append(label)

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
        start_offset=getattr(hit, "start_offset", None),
        end_offset=getattr(hit, "end_offset", None),
        start_page=hit.start_page,
        end_page=hit.end_page,
        chunk_index=getattr(hit, "chunk_index", None),
        chunk_text=getattr(hit, "chunk_text", "") or hit.snippet,
        semantic_topic_labels=semantic_topic_labels,
    )


def _hebrew_tokens(value: str) -> list[str]:
    return [token for token in HEBREW_TOKEN_RE.findall(value) if len(token) >= 2]


def _is_broad_decision_query(normalized_query: str) -> bool:
    tokens = set(_hebrew_tokens(normalized_query))
    if not tokens:
        return False
    has_decision_marker = bool(tokens.intersection(DECISION_QUERY_MARKERS))
    if not has_decision_marker:
        return False
    if bool(tokens.intersection(BROAD_SCOPE_TOKENS)):
        return True
    return len(tokens) <= 3


def _hit_contains_decision_marker(hit) -> bool:
    text_blob = normalize_for_search(f"{getattr(hit, 'chunk_text', '')} {getattr(hit, 'snippet', '')}")
    if not text_blob:
        return False
    tokens = set(_hebrew_tokens(text_blob))
    return bool(tokens.intersection(DECISION_TEXT_MARKERS))


def _is_boilerplate_decision_text(value: str) -> bool:
    text = normalize_for_search(value)
    if not text:
        return False
    for pattern in BOILERPLATE_DECISION_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _decision_hit_quality_score(hit) -> float:
    score = float(getattr(hit, "score", 0.0) or 0.0)
    text = f"{getattr(hit, 'chunk_text', '')} {getattr(hit, 'snippet', '')}".strip()
    text_norm = normalize_for_search(text)
    if _is_boilerplate_decision_text(text_norm):
        score -= 0.4

    semantic_nodes = getattr(hit, "semantic_nodes", None) or []
    has_topic_semantic = any(
        str(getattr(node, "kind", "") or "").strip().casefold() == "topic"
        for node in semantic_nodes
    )
    if has_topic_semantic:
        score += 0.15

    if text_norm and len(text_norm) >= 220:
        score += 0.04
    return score


def _augment_protocol_decision_hits(
    *,
    selected_hits: list[Any],
    search_service,
    municipality_slug: str | None,
    requested_source_kinds: list[str],
    year: int | None,
    topic: str | None,
    semantic_node_id: int | None,
    semantic_label: str | None,
    semantic_mode: str,
    top_k: int,
) -> list[Any]:
    if requested_source_kinds and "protocol" not in requested_source_kinds:
        return selected_hits

    protocol_hits = [hit for hit in selected_hits if hit.source_type == "protocol"]
    if len(protocol_hits) <= 1:
        return selected_hits

    target_doc_ids: list[int] = []
    seen_doc_ids: set[int] = set()
    for hit in protocol_hits:
        if hit.document_id in seen_doc_ids:
            continue
        target_doc_ids.append(hit.document_id)
        seen_doc_ids.add(hit.document_id)
        if len(target_doc_ids) >= top_k:
            break

    if len(target_doc_ids) <= 1:
        return selected_hits

    decision_probe_hits = search_service.search(
        query=DECISION_PROBE_QUERY,
        municipality_slug=municipality_slug,
        source_type="protocol",
        year=year,
        topic=topic,
        semantic_node_id=semantic_node_id,
        semantic_label=semantic_label,
        semantic_mode=semantic_mode,
        limit=max(top_k * 6, top_k),
    )
    decision_probe_hits = _dedupe_hits(decision_probe_hits)

    best_by_doc_id: dict[int, Any] = {}
    target_doc_set = set(target_doc_ids)
    for hit in decision_probe_hits:
        if hit.document_id not in target_doc_set:
            continue
        if not _hit_contains_decision_marker(hit):
            continue
        existing = best_by_doc_id.get(hit.document_id)
        if existing is None or _decision_hit_quality_score(hit) > _decision_hit_quality_score(existing):
            best_by_doc_id[hit.document_id] = hit

    augmented: list[Any] = []
    seen_chunk_ids: set[str] = set()

    non_protocol_hits = [hit for hit in selected_hits if hit.source_type != "protocol"]
    protocol_hits_by_doc: dict[int, list[Any]] = {}
    for hit in selected_hits:
        if hit.source_type != "protocol":
            continue
        protocol_hits_by_doc.setdefault(hit.document_id, []).append(hit)

    for doc_id in target_doc_ids:
        replacement = best_by_doc_id.get(doc_id)
        existing_doc_hits = protocol_hits_by_doc.get(doc_id, [])
        if replacement is None and existing_doc_hits:
            replacement = max(existing_doc_hits, key=_decision_hit_quality_score)
        if replacement is None:
            continue
        if replacement.chunk_id in seen_chunk_ids:
            continue
        augmented.append(replacement)
        seen_chunk_ids.add(replacement.chunk_id)

    for hit in selected_hits:
        if hit.source_type == "protocol" and hit.document_id in target_doc_set:
            continue
        if hit.chunk_id in seen_chunk_ids:
            continue
        augmented.append(hit)
        seen_chunk_ids.add(hit.chunk_id)

    for hit in non_protocol_hits:
        if hit.chunk_id in seen_chunk_ids:
            continue
        augmented.append(hit)
        seen_chunk_ids.add(hit.chunk_id)

    for doc_id in target_doc_ids:
        candidate = best_by_doc_id.get(doc_id)
        if candidate is None or candidate.chunk_id in seen_chunk_ids:
            continue
        augmented.append(candidate)
        seen_chunk_ids.add(candidate.chunk_id)

    return _dedupe_hits(augmented)


def _prioritize_protocol_document_coverage(hits: list[Any], *, limit: int) -> list[Any]:
    if limit <= 1:
        return hits

    ordered: list[Any] = []
    seen_chunk_ids: set[str] = set()
    protocol_doc_order: list[int] = []
    seen_protocol_doc_ids: set[int] = set()
    for hit in hits:
        if hit.source_type != "protocol":
            continue
        if hit.document_id in seen_protocol_doc_ids:
            continue
        protocol_doc_order.append(hit.document_id)
        seen_protocol_doc_ids.add(hit.document_id)

    for document_id in protocol_doc_order:
        document_hits = [hit for hit in hits if hit.source_type == "protocol" and hit.document_id == document_id]
        if not document_hits:
            continue
        preferred = max(document_hits, key=_decision_hit_quality_score)
        if preferred.chunk_id in seen_chunk_ids:
            continue
        ordered.append(preferred)
        seen_chunk_ids.add(preferred.chunk_id)
        if len(ordered) >= limit:
            return ordered

    for hit in hits:
        if hit.chunk_id in seen_chunk_ids:
            continue
        ordered.append(hit)
        seen_chunk_ids.add(hit.chunk_id)
        if len(ordered) >= limit:
            break

    return ordered
