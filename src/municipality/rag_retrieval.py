from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from municipality.chunking import normalize_for_search
from municipality.embeddings import EmbeddingReranker
from municipality.query_rewrite import QueryRewriteResult, QueryRewriteService
from municipality.rag_arch import RagArchitectureConfig
from municipality.rag_observability import build_retrieval_set_id, hash_text, log_rag_event


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
RETRIEVAL_VERSION = f"hierarchy_hebrew_rag_{RagArchitectureConfig.from_env().version}"


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
    semantic_match_count: int = 0
    semantic_boost: float = 0.0
    semantic_node_ids: list[int] = field(default_factory=list)
    semantic_nodes: list[Any] = field(default_factory=list)
    primary_topic: str | None = None
    secondary_topics: list[str] = field(default_factory=list)
    section_path: list[str] = field(default_factory=list)
    artifact_kind: str | None = None


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
    def __init__(self, *, search_service, reranker: EmbeddingReranker | None = None, query_rewriter: QueryRewriteService | None = None):
        self.search_service = search_service
        self.reranker = reranker
        self.query_rewriter = query_rewriter or QueryRewriteService()

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
        retrieval_strategy: str | None = None,
        document_ids: list[int] | None = None,
        document_version_ids: list[int] | None = None,
        ask_request_id: str | None = None,
    ) -> RagRetrievalResult:
        normalized_query = normalize_for_search(query)
        effective_top_k = max(1, top_k)
        requested_source_kinds = _normalize_source_kinds(source_kinds)
        scoped_document_ids = _normalize_positive_ints(document_ids)
        scoped_document_version_ids = _normalize_positive_ints(document_version_ids)
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
            retrieval_version=RETRIEVAL_VERSION,
            scope_filters=_retrieval_scope_filters(
                municipality_slug=municipality_slug,
                year=year,
                topic=topic,
                semantic_node_id=semantic_node_id,
                semantic_label=semantic_label,
                semantic_mode=semantic_mode,
                document_ids=scoped_document_ids,
                document_version_ids=scoped_document_version_ids,
            ),
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
                    "decision_match_count": 0,
                    "top_decision_matches": [],
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

        rewrite_result = self.query_rewriter.rewrite_for_retrieval(query=query)
        effective_strategy = _effective_retrieval_strategy(retrieval_strategy, rewrite_result)
        topic_terms = [*rewrite_result.semantic_terms, *rewrite_result.header_terms]

        if self.reranker is not None:
            initial_limit = max(self.reranker.initial_candidate_limit(top_k=effective_top_k), effective_top_k * 3)
        else:
            initial_limit = max(effective_top_k * 3, effective_top_k)
        hits = _run_retrieval_plan(
            search_service=self.search_service,
            query=rewrite_result.rewritten_query or query,
            municipality_slug=municipality_slug,
            year=year,
            topic=topic,
            semantic_node_id=semantic_node_id,
            semantic_label=semantic_label,
            semantic_mode=semantic_mode,
            initial_limit=initial_limit,
            rewrite_result=rewrite_result,
            retrieval_strategy=effective_strategy,
            topic_terms=topic_terms,
            scoped_document_ids=scoped_document_ids,
            document_version_ids=scoped_document_version_ids,
        )

        selected_hits = _dedupe_hits(hits)
        if requested_source_kinds:
            selected_hits = [hit for hit in selected_hits if hit.source_type in requested_source_kinds]

        missing_source_kinds = set(requested_source_kinds) - {hit.source_type for hit in selected_hits}
        for source_kind in sorted(missing_source_kinds):
            source_hits = self.search_service.search(
                query=rewrite_result.rewritten_query or query,
                municipality_slug=municipality_slug,
                source_type=source_kind,
                year=year,
                topic=topic,
                semantic_node_id=semantic_node_id,
                semantic_label=semantic_label,
                semantic_mode=semantic_mode,
                artifact_kinds=_source_artifact_kinds(
                    source_kind=source_kind,
                    rewrite_result=rewrite_result,
                    retrieval_strategy=effective_strategy,
                ),
                topic_terms=topic_terms,
                document_ids=scoped_document_ids,
                document_version_ids=scoped_document_version_ids,
                limit=max(effective_top_k, min(initial_limit, effective_top_k * 6)),
            )
            selected_hits.extend(source_hits)
            selected_hits = _dedupe_hits(selected_hits)

        broad_decision_query = _is_broad_decision_query(normalized_query)

        lexical_top_k_chunk_ids = [str(row.chunk_id) for row in selected_hits[:effective_top_k]]
        rerank_stats: dict[str, Any] = {"enabled": False}
        if self.reranker is not None and selected_hits:
            selected_hits, rerank_stats = self.reranker.rerank_hits(
                query=query,
                hits=selected_hits,
                top_k=effective_top_k,
            )

        selected_hits.sort(key=lambda row: row.score, reverse=True)
        if requested_source_kinds:
            selected_hits = _ensure_requested_source_coverage(selected_hits, requested_source_kinds, limit=effective_top_k)
        contexts = [_to_context(row) for row in selected_hits[:effective_top_k]]
        retrieval_set_id = build_retrieval_set_id(
            normalized_query=normalized_query,
            requested_source_kinds=requested_source_kinds,
            top_k=effective_top_k,
            chunk_ids=[row.chunk_id for row in contexts],
            retrieval_version=RETRIEVAL_VERSION,
            scope_filters=_retrieval_scope_filters(
                municipality_slug=municipality_slug,
                year=year,
                topic=topic,
                semantic_node_id=semantic_node_id,
                semantic_label=semantic_label,
                semantic_mode=semantic_mode,
                document_ids=scoped_document_ids,
                document_version_ids=scoped_document_version_ids,
            ),
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
                "query_rewrite": {
                    "rewritten_query": rewrite_result.rewritten_query,
                    "lexical_terms": list(rewrite_result.lexical_terms),
                    "semantic_terms": list(rewrite_result.semantic_terms),
                    "header_terms": list(rewrite_result.header_terms),
                    "artifact_kind_priority": list(rewrite_result.artifact_kind_priority),
                    "retrieval_strategy": effective_strategy,
                    "use_neighbors": rewrite_result.use_neighbors,
                    "route_reason": rewrite_result.route_reason,
                    "provider": rewrite_result.provider,
                    "model": rewrite_result.model,
                },
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


def _effective_retrieval_strategy(requested_strategy: str | None, rewrite_result: QueryRewriteResult) -> str:
    normalized = str(requested_strategy or "auto").strip().casefold()
    if normalized in {"headers", "segments", "neighbors", "full_doc"}:
        return normalized
    if rewrite_result.retrieval_strategy in {"headers", "segments", "neighbors", "full_doc"}:
        return rewrite_result.retrieval_strategy
    return "segments"


def _run_retrieval_plan(
    *,
    search_service,
    query: str,
    municipality_slug: str | None,
    year: int | None,
    topic: str | None,
    semantic_node_id: int | None,
    semantic_label: str | None,
    semantic_mode: str,
    initial_limit: int,
    rewrite_result: QueryRewriteResult,
    retrieval_strategy: str,
    topic_terms: list[str],
    scoped_document_ids: list[int],
    document_version_ids: list[int],
) -> list[Any]:
    plans = _artifact_kind_plans(rewrite_result=rewrite_result, retrieval_strategy=retrieval_strategy)
    selected_hits: list[Any] = []
    selected_hits.extend(
        search_service.search(
            query=query,
            municipality_slug=municipality_slug,
            source_type=None,
            year=year,
            topic=topic,
            semantic_node_id=semantic_node_id,
            semantic_label=semantic_label,
            semantic_mode=semantic_mode,
            artifact_kinds=plans[0],
            topic_terms=topic_terms,
            document_ids=scoped_document_ids,
            document_version_ids=document_version_ids,
            limit=initial_limit,
        )
    )
    selected_hits = _dedupe_hits(selected_hits)

    for plan_index, artifact_kinds in enumerate(plans[1:], start=1):
        plan_document_ids = list(scoped_document_ids)
        if not plan_document_ids and plan_index == 1 and rewrite_result.use_neighbors:
            plan_document_ids = [
                hit.document_id
                for hit in selected_hits[: max(1, min(8, len(selected_hits)))]
                if getattr(hit, "document_id", None)
            ]
        selected_hits.extend(
            search_service.search(
                query=query,
                municipality_slug=municipality_slug,
                source_type=None,
                year=year,
                topic=topic,
                semantic_node_id=semantic_node_id,
                semantic_label=semantic_label,
                semantic_mode=semantic_mode,
                artifact_kinds=artifact_kinds,
                topic_terms=topic_terms,
                document_ids=plan_document_ids,
                document_version_ids=document_version_ids,
                limit=max(1, initial_limit // 2),
            )
        )
        selected_hits = _dedupe_hits(selected_hits)
    return selected_hits


def _artifact_kind_plans(*, rewrite_result: QueryRewriteResult, retrieval_strategy: str) -> list[list[str]]:
    priority = [kind for kind in rewrite_result.artifact_kind_priority if kind]
    if not priority:
        priority = ["decision_unit", "section_unit", "header_plus_opening", "context_window", "header_anchor", "document_profile"]
    if retrieval_strategy == "headers":
        return [
            [kind for kind in priority if kind in {"document_profile", "header_anchor", "header_plus_opening", "section_summary"}] or ["header_anchor", "document_profile"],
            ["section_unit", "decision_unit"],
            ["context_window"],
        ]
    if retrieval_strategy == "neighbors":
        return [
            [kind for kind in priority if kind in {"decision_unit", "section_unit", "context_window", "header_plus_opening"}] or ["decision_unit", "section_unit", "context_window"],
            ["context_window"],
            ["header_anchor", "document_profile"],
        ]
    if retrieval_strategy == "full_doc":
        return [["document_profile"], ["header_anchor", "section_summary"], ["decision_unit", "section_unit"]]
    return [
        [kind for kind in priority if kind in {"decision_unit", "section_unit", "header_plus_opening"}] or ["decision_unit", "section_unit", "header_plus_opening"],
        ["context_window"],
        ["header_anchor", "document_profile"],
    ]


def _source_artifact_kinds(*, source_kind: str, rewrite_result: QueryRewriteResult, retrieval_strategy: str) -> list[str]:
    if source_kind == "pdf_first_protocol":
        return ["pdf_first_retrieval_chunk"]
    return _artifact_kind_plans(rewrite_result=rewrite_result, retrieval_strategy=retrieval_strategy)[0]


def _ensure_requested_source_coverage(hits: list[Any], requested_source_kinds: list[str], *, limit: int) -> list[Any]:
    if limit <= 0 or not hits or not requested_source_kinds:
        return hits
    requested = [source_kind for source_kind in requested_source_kinds if source_kind]
    top_hits = list(hits[:limit])
    covered = {getattr(hit, "source_type", None) for hit in top_hits}
    missing = [source_kind for source_kind in requested if source_kind not in covered]
    if not missing:
        return hits

    remaining = list(top_hits)
    used_ids = {str(getattr(hit, "chunk_id", "")) for hit in remaining}
    for source_kind in missing:
        replacement = next(
            (
                hit
                for hit in hits
                if getattr(hit, "source_type", None) == source_kind and str(getattr(hit, "chunk_id", "")) not in used_ids
            ),
            None,
        )
        if replacement is None:
            continue
        weakest_index = next(
            (idx for idx in range(len(remaining) - 1, -1, -1) if getattr(remaining[idx], "source_type", None) != source_kind),
            None,
        )
        if weakest_index is None:
            continue
        used_ids.discard(str(getattr(remaining[weakest_index], "chunk_id", "")))
        remaining[weakest_index] = replacement
        used_ids.add(str(getattr(replacement, "chunk_id", "")))

    tail = [hit for hit in hits[limit:] if str(getattr(hit, "chunk_id", "")) not in used_ids]
    return [*remaining, *tail]


def _normalize_source_kinds(source_kinds: list[str] | None) -> list[str]:
    if not source_kinds:
        return []
    accepted = {"protocol", "pdf_first_protocol", "attachment", "other"}
    out: list[str] = []
    seen: set[str] = set()
    for source_kind in source_kinds:
        normalized = (source_kind or "").strip().casefold()
        if normalized in accepted and normalized not in seen:
            out.append(normalized)
            seen.add(normalized)
    return out


def _normalize_positive_ints(values: list[int] | None) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            continue
        if normalized <= 0 or normalized in seen:
            continue
        out.append(normalized)
        seen.add(normalized)
    return out


def _retrieval_scope_filters(
    *,
    municipality_slug: str | None,
    year: int | None,
    topic: str | None,
    semantic_node_id: int | None,
    semantic_label: str | None,
    semantic_mode: str,
    document_ids: list[int],
    document_version_ids: list[int],
) -> dict[str, Any]:
    return {
        "municipality_slug": municipality_slug or None,
        "year": int(year) if year is not None else None,
        "topic": str(topic or "").strip() or None,
        "semantic_node_id": int(semantic_node_id) if semantic_node_id is not None else None,
        "semantic_label": str(semantic_label or "").strip() or None,
        "semantic_mode": str(semantic_mode or "off").strip().casefold() or "off",
        "document_ids": list(document_ids),
        "document_version_ids": list(document_version_ids),
    }


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
        semantic_match_count=int(getattr(hit, "semantic_match_count", 0) or 0),
        semantic_boost=float(getattr(hit, "semantic_boost", 0.0) or 0.0),
        semantic_node_ids=list(getattr(hit, "semantic_node_ids", []) or []),
        semantic_nodes=list(getattr(hit, "semantic_nodes", []) or []),
        primary_topic=getattr(hit, "primary_topic", None),
        secondary_topics=list(getattr(hit, "secondary_topics", []) or []),
        section_path=list(getattr(hit, "section_path", []) or []),
        artifact_kind=getattr(hit, "artifact_kind", None),
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
