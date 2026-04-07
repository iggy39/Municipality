from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import httpx
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, inspect, select

from municipality.chunking import normalize_for_search
from municipality.db import build_engine, build_session_factory
from municipality.embeddings import ChunkEmbeddingService, EmbeddingReranker
from municipality.fetcher import AssetFetcher
from municipality.migrations import apply_all
from municipality.models import (
    Decision,
    DecisionCitation,
    DecisionDocumentLink,
    DecisionRequestContext,
    Document,
    Meeting,
    MeetingDocumentLink,
    PipelineRun,
    PipelineRunStep,
    ChunkEmbedding,
    RagAnswerCache,
    SemanticAlias,
    SemanticCandidateReject,
    SemanticDocumentRun,
    SemanticMention,
    SemanticNode,
    ChunkSemanticLink,
    DecisionSemanticLink,
    RagDecisionSummaryCache,
    RagDecisionSummaryTopicMeta,
    SourceSite,
    TextChunk,
    Vote,
)
from municipality.pipeline import PipelineService
from municipality.processing import ProcessingService
from municipality.rag_answer_cache import RagAnswerCacheService
from municipality.rag_answering import RagAnswerResult, RagAnsweringService, RagCitation, rag_answering_thresholds_snapshot
from municipality.rag_llm import RagLlmClient, RagLlmConfig, build_rag_llm_client
from municipality.rag_observability import (
    audit_sample_rate,
    hash_text,
    log_rag_event,
    new_ask_request_id,
    should_sample_audit,
)
from municipality.rag_retrieval import RagContextChunk, RagRetrievalResult, RagRetrievalService
from municipality.search import SearchService, search_thresholds_snapshot


def _default_html_fetcher(url: str) -> str:
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


engine = build_engine()
SessionLocal = build_session_factory(engine)
app = FastAPI(title="Municipality API")


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _default_fallback_metadata() -> dict:
    return {
        "fallback_used": False,
        "fallback_reason": None,
        "fallback_provider": None,
        "fallback_model": None,
        "fallback_invoked_at": None,
        "fallback_validation_status": None,
        "fallback_validation_reasons": [],
    }


def _fallback_metadata_from_json(metadata_json: str | None) -> dict:
    metadata = _default_fallback_metadata()
    if not metadata_json:
        return metadata
    try:
        payload = json.loads(metadata_json)
    except json.JSONDecodeError:
        return metadata
    if not isinstance(payload, dict):
        return metadata
    merged = dict(payload)
    for key, value in metadata.items():
        merged.setdefault(key, value)
    metadata = merged
    if not isinstance(metadata["fallback_validation_reasons"], list):
        metadata["fallback_validation_reasons"] = []
    if "api_model_reasons" in metadata and not isinstance(metadata["api_model_reasons"], list):
        metadata["api_model_reasons"] = []
    return metadata


def _loads_json(value: str | None) -> dict | list | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, (dict, list)):
        return payload
    return None


def _semantic_node_payload(node: SemanticNode, *, child_count: int = 0) -> dict:
    return {
        "id": node.id,
        "label": node.pref_label_he,
        "label_norm": node.pref_label_norm,
        "kind": node.node_kind,
        "semantic_type": node.semantic_type,
        "depth": node.depth,
        "parent_id": node.parent_node_id,
        "specificity_score": node.specificity_score,
        "confidence": node.confidence,
        "support_count": node.support_count,
        "status": node.status,
        "child_count": child_count,
    }


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    muni: str | None = None
    top_k: int = Field(default=8, ge=1, le=50)
    source_types: list[str] | None = None
    required_source_types: list[str] | None = None
    year: int | None = None
    topic: str | None = None
    semantic_node_id: int | None = None
    semantic_label: str | None = None
    semantic_mode: str = "off"
    debug_mode: bool = False


def _active_embedding_cache_summary(*, db) -> dict[str, Any]:
    embedding_service = ChunkEmbeddingService(db)
    model_client = embedding_service.model_client
    model_provider = model_client.provider_name
    model_name = model_client.model_name
    dimensions = model_client.dimensions

    total_chunks = int(db.execute(select(func.count(TextChunk.id))).scalar_one() or 0)
    embedded_chunks = int(
        db.execute(
            select(func.count(func.distinct(ChunkEmbedding.chunk_id))).where(
                ChunkEmbedding.model_provider == model_provider,
                ChunkEmbedding.model_name == model_name,
                ChunkEmbedding.dimensions == dimensions,
            )
        ).scalar_one()
        or 0
    )

    by_source_rows = db.execute(
        select(
            TextChunk.source_kind,
            func.count(func.distinct(TextChunk.chunk_id)),
            func.count(func.distinct(ChunkEmbedding.chunk_id)),
        )
        .select_from(TextChunk)
        .outerjoin(
            ChunkEmbedding,
            and_(
                ChunkEmbedding.chunk_id == TextChunk.chunk_id,
                ChunkEmbedding.model_provider == model_provider,
                ChunkEmbedding.model_name == model_name,
                ChunkEmbedding.dimensions == dimensions,
            ),
        )
        .group_by(TextChunk.source_kind)
        .order_by(TextChunk.source_kind.asc())
    ).all()

    by_source_kind = []
    for source_kind, source_total, source_embedded in by_source_rows:
        total_value = int(source_total or 0)
        embedded_value = int(source_embedded or 0)
        missing_value = max(0, total_value - embedded_value)
        by_source_kind.append(
            {
                "source_type": str(source_kind or "unknown"),
                "total_chunks": total_value,
                "embedded_chunks": embedded_value,
                "missing_chunks": missing_value,
                "coverage_rate": round((embedded_value / total_value), 4) if total_value else 0.0,
            }
        )

    missing_chunks = max(0, total_chunks - embedded_chunks)
    return {
        "enabled": embedding_service.is_enabled(),
        "provider": model_provider,
        "model": model_name,
        "dimensions": dimensions,
        "total_chunks": total_chunks,
        "embedded_chunks": embedded_chunks,
        "missing_chunks": missing_chunks,
        "coverage_rate": round((embedded_chunks / total_chunks), 4) if total_chunks else 0.0,
        "by_source_type": by_source_kind,
    }


def _rerank_debug_summary(*, retrieval_result: RagRetrievalResult) -> dict[str, Any]:
    retrieval_trace = dict(retrieval_result.debug_info)
    lexical_top_k_chunk_ids = [
        str(chunk_id)
        for chunk_id in retrieval_trace.get("lexical_top_k_chunk_ids", [])
        if str(chunk_id).strip()
    ]
    reranked_top_k_chunk_ids = [
        str(chunk_id)
        for chunk_id in retrieval_trace.get("reranked_top_k_chunk_ids", [])
        if str(chunk_id).strip()
    ]
    embedding_rerank = retrieval_trace.get("embedding_rerank")
    if not isinstance(embedding_rerank, dict):
        embedding_rerank = {"enabled": False}

    lexical_top_k_set = set(lexical_top_k_chunk_ids)
    overlap_count = sum(1 for chunk_id in reranked_top_k_chunk_ids if chunk_id in lexical_top_k_set)
    changed_count = sum(
        1
        for index, chunk_id in enumerate(reranked_top_k_chunk_ids)
        if index >= len(lexical_top_k_chunk_ids) or lexical_top_k_chunk_ids[index] != chunk_id
    )
    top_k_count = max(len(reranked_top_k_chunk_ids), 1)
    candidate_count = int(embedding_rerank.get("candidate_count") or 0)
    available_chunk_embeddings = int(embedding_rerank.get("available_chunk_embeddings") or 0)

    return {
        "candidate_limit": int(retrieval_trace.get("candidate_limit") or 0),
        "broad_decision_query": bool(retrieval_trace.get("broad_decision_query") is True),
        "decision_match_count": int(retrieval_trace.get("decision_match_count") or 0),
        "top_decision_matches": list(retrieval_trace.get("top_decision_matches") or []),
        "lexical_top_k_chunk_ids": lexical_top_k_chunk_ids,
        "reranked_top_k_chunk_ids": reranked_top_k_chunk_ids,
        "overlap_count": overlap_count,
        "changed_count": changed_count,
        "lexical_top_k_hit_rate": round((overlap_count / top_k_count), 4),
        "candidate_embedding_hit_rate": round((available_chunk_embeddings / max(candidate_count, 1)), 4)
        if candidate_count
        else 0.0,
        "embedding_rerank": embedding_rerank,
    }


def _collapse_duplicate_answering_contexts(
    *,
    embedding_service: ChunkEmbeddingService,
    question: str,
    contexts: list[RagContextChunk],
) -> tuple[list[RagContextChunk], dict[str, Any]]:
    stats = {"enabled": embedding_service.is_enabled(), "before_count": len(contexts), "after_count": len(contexts), "dropped_chunk_ids": []}
    if len(contexts) <= 2 or not embedding_service.is_enabled():
        return contexts, stats
    query_vector = embedding_service.embed_query(question, query_kind="answer_context_dedupe")
    if not query_vector:
        return contexts, stats
    lookup = embedding_service.ensure_embeddings_for_hits(hits=contexts)
    vectors_by_chunk = lookup.vectors_by_chunk_id
    if not vectors_by_chunk:
        return contexts, stats
    threshold = 0.965
    ranked_contexts = sorted(
        contexts,
        key=lambda item: (
            _cosine_similarity_api(query_vector, vectors_by_chunk.get(str(item.chunk_id))),
            float(item.score or 0.0),
        ),
        reverse=True,
    )
    kept: list[RagContextChunk] = []
    dropped: list[str] = []
    for context in ranked_contexts:
        vector = vectors_by_chunk.get(str(context.chunk_id))
        if vector is None:
            kept.append(context)
            continue
        is_duplicate = False
        for existing in kept:
            if context.source_kind != existing.source_kind or context.document_id != existing.document_id:
                continue
            existing_vector = vectors_by_chunk.get(str(existing.chunk_id))
            if existing_vector is None:
                continue
            if _cosine_similarity_api(vector, existing_vector) >= threshold:
                is_duplicate = True
                break
        if is_duplicate:
            dropped.append(str(context.chunk_id))
            continue
        kept.append(context)
    kept_ids = {str(context.chunk_id) for context in kept}
    ordered_kept = [context for context in contexts if str(context.chunk_id) in kept_ids]
    stats["after_count"] = len(ordered_kept)
    stats["dropped_chunk_ids"] = dropped
    return ordered_kept, stats


def _low_relevance_embedding_refusal(
    *,
    embedding_service: ChunkEmbeddingService,
    question: str,
    retrieval_result: RagRetrievalResult,
) -> dict[str, Any] | None:
    if not retrieval_result.contexts or not embedding_service.is_enabled():
        return None
    query_vector = embedding_service.embed_query(question, query_kind="low_relevance_gate")
    if not query_vector:
        return None
    lookup = embedding_service.ensure_embeddings_for_hits(hits=retrieval_result.contexts)
    vectors_by_chunk = lookup.vectors_by_chunk_id
    if not vectors_by_chunk:
        return None
    chunk_similarities = [
        _cosine_similarity_api(query_vector, vectors_by_chunk.get(str(context.chunk_id)))
        for context in retrieval_result.contexts
        if str(context.chunk_id) in vectors_by_chunk
    ]
    if not chunk_similarities:
        return None
    decision_matches = retrieval_result.debug_info.get("top_decision_matches") if isinstance(retrieval_result.debug_info, dict) else []
    top_decision_similarity = 0.0
    if isinstance(decision_matches, list):
        for item in decision_matches:
            if isinstance(item, dict):
                top_decision_similarity = max(top_decision_similarity, float(item.get("similarity") or 0.0))
    lexical_max = max(float(context.score or 0.0) for context in retrieval_result.contexts)
    max_chunk_similarity = max(chunk_similarities)
    avg_chunk_similarity = sum(chunk_similarities) / max(len(chunk_similarities), 1)
    if max(max_chunk_similarity, top_decision_similarity) >= 0.18 or lexical_max >= 0.2:
        return None
    return {
        "reason": "low_embedding_relevance",
        "chunk_max_similarity": round(max_chunk_similarity, 6),
        "chunk_avg_similarity": round(avg_chunk_similarity, 6),
        "top_decision_similarity": round(top_decision_similarity, 6),
        "lexical_max_score": round(lexical_max, 6),
    }


def _answer_result_to_cache_payload(answer_result: RagAnswerResult) -> dict[str, Any]:
    return {
        "status": answer_result.status,
        "answer": answer_result.answer,
        "extended_answer": answer_result.extended_answer,
        "answer_sections": list(answer_result.answer_sections),
        "extended_answer_sections": list(answer_result.extended_answer_sections),
        "citations": [
            {
                "chunk_id": citation.chunk_id,
                "source_kind": citation.source_kind,
                "citation_label": citation.citation_label,
                "document_id": citation.document_id,
                "document_title": citation.document_title,
                "document_url": citation.document_url,
                "start_page": citation.start_page,
                "end_page": citation.end_page,
                "score": citation.score,
            }
            for citation in answer_result.citations
        ],
        "claim_assessments": list(answer_result.claim_assessments),
        "limitations": list(answer_result.limitations),
        "refusal_reason_code": answer_result.refusal_reason_code,
        "refusal_message_he": answer_result.refusal_message_he,
        "missing_source_kinds": list(answer_result.missing_source_kinds),
        "provider": answer_result.provider,
        "model": answer_result.model,
        "scoring": dict(answer_result.scoring),
    }


def _answer_result_from_cache_payload(payload: dict[str, Any]) -> RagAnswerResult | None:
    if not isinstance(payload, dict) or payload.get("status") != "answer":
        return None
    citations_payload = payload.get("citations") if isinstance(payload.get("citations"), list) else []
    citations: list[RagCitation] = []
    for row in citations_payload:
        if not isinstance(row, dict):
            continue
        citations.append(
            RagCitation(
                chunk_id=str(row.get("chunk_id") or ""),
                source_kind=str(row.get("source_kind") or ""),
                citation_label=row.get("citation_label") if isinstance(row.get("citation_label"), str) else None,
                document_id=int(row.get("document_id") or 0),
                document_title=str(row.get("document_title") or ""),
                document_url=str(row.get("document_url") or ""),
                start_page=int(row.get("start_page")) if row.get("start_page") is not None else None,
                end_page=int(row.get("end_page")) if row.get("end_page") is not None else None,
                score=float(row.get("score") or 0.0),
            )
        )
    return RagAnswerResult(
        status="answer",
        answer=payload.get("answer") if isinstance(payload.get("answer"), str) else None,
        extended_answer=payload.get("extended_answer") if isinstance(payload.get("extended_answer"), str) else None,
        answer_sections=list(payload.get("answer_sections") or []),
        extended_answer_sections=list(payload.get("extended_answer_sections") or []),
        citations=citations,
        claim_assessments=list(payload.get("claim_assessments") or []),
        limitations=list(payload.get("limitations") or []),
        refusal_reason_code=None,
        refusal_message_he=None,
        missing_source_kinds=[],
        provider=payload.get("provider") if isinstance(payload.get("provider"), str) else None,
        model=payload.get("model") if isinstance(payload.get("model"), str) else None,
        scoring=dict(payload.get("scoring") or {}),
    )


def _cosine_similarity_api(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _llm_thresholds_payload(llm_client: RagLlmClient) -> dict:
    provider = llm_client.provider
    timeout_seconds = getattr(provider, "timeout_seconds", None)
    config_snapshot = RagLlmConfig.from_env()
    return {
        "call_order": ["answer", "verify", "refuse"],
        "temperature_default": 0.0,
        "provider": provider.provider_name,
        "model": provider.model_name,
        "call_timeout_seconds": timeout_seconds,
        "default_timeout_seconds": config_snapshot.timeout_seconds,
        "prompt_prefixes": llm_client.prompt_prefixes.as_dict(),
        "missing_prefix_policy": "fail_fast",
    }


def _ask_thresholds_payload(*, request: AskRequest, llm_client: RagLlmClient) -> dict:
    return {
        "request_validation": {
            "top_k_min": 1,
            "top_k_max": 50,
            "top_k_effective": max(1, request.top_k),
        },
        "retrieval": search_thresholds_snapshot(),
        "answering": rag_answering_thresholds_snapshot(),
        "llm": _llm_thresholds_payload(llm_client),
    }


def _normalized_answering_trace(scoring: dict | None) -> dict:
    trace = dict(scoring or {})
    trace.setdefault("semantic_scoring_source", "not_reached")
    trace.setdefault("verify_route", "not_reached")
    trace.setdefault("fallback_verify_attempted", False)
    trace.setdefault("fallback_provider", "none")
    trace.setdefault("similarity_external_api_called", False)
    trace.setdefault("answer_external_api_called", False)
    trace.setdefault("external_call_count", 0)
    timing_payload = trace.get("timing_ms")
    if not isinstance(timing_payload, dict):
        trace["timing_ms"] = {}
    return trace


def _persist_decision_summary_cache(
    *,
    db,
    question_hash: str,
    answer_sections: list[dict],
    protocol_title_by_chunk_id: dict[str, str] | None,
    provider: str | None,
    model: str | None,
) -> None:
    if not answer_sections:
        return

    if not inspect(db.get_bind()).has_table("rag_decision_summary_cache"):
        log_rag_event(
            "rag.ask.summary_cache.skip",
            reason="missing_table",
            question_hash=question_hash,
        )
        return

    now = datetime.utcnow()
    title_by_chunk = protocol_title_by_chunk_id or {}
    has_topic_meta_table = inspect(db.get_bind()).has_table("rag_decision_summary_topic_meta")
    for section in answer_sections:
        protocol_title = str(section.get("protocol_title") or "").strip()
        raw_topic_name = str(section.get("topic_name") or "").strip() or "נושא כללי"
        topic_name = _cache_topic_name(raw_topic_name)
        topic_granularity_level = _as_int_in_range(section.get("topic_granularity_level"), min_value=1, max_value=10)
        summaries = section.get("summaries")
        if isinstance(summaries, list):
            summary_list = [str(row).strip() for row in summaries if isinstance(row, str) and row.strip()]
        else:
            text_value = str(section.get("text") or "").strip()
            summary_list = [text_value] if text_value else []

        chunk_ids = section.get("chunk_ids")
        if not isinstance(chunk_ids, list):
            continue
        normalized_chunk_ids = [str(chunk_id).strip() for chunk_id in chunk_ids if str(chunk_id).strip()]
        if not normalized_chunk_ids or not summary_list:
            continue

        for chunk_id in normalized_chunk_ids:
            resolved_protocol_title = str(title_by_chunk.get(chunk_id) or protocol_title or "").strip() or "פרוטוקול"
            for summary in summary_list:
                effective_topic_name = topic_name
                if normalize_for_search(effective_topic_name) in {
                    normalize_for_search(PLACEHOLDER_TOPIC_LABEL),
                    normalize_for_search("נושא כללי"),
                }:
                    inferred = _infer_topic_path_from_summary(
                        summary_he=summary,
                        protocol_title=resolved_protocol_title,
                    )
                    if inferred:
                        effective_topic_name = _cache_topic_name(inferred)

                existing = db.execute(
                    select(RagDecisionSummaryCache).where(
                        RagDecisionSummaryCache.question_hash == question_hash,
                        RagDecisionSummaryCache.chunk_id == chunk_id,
                        RagDecisionSummaryCache.summary_he == summary,
                    )
                ).scalar_one_or_none()
                if existing is None:
                    db.add(
                        RagDecisionSummaryCache(
                            question_hash=question_hash,
                            chunk_id=chunk_id,
                            protocol_title=resolved_protocol_title,
                            topic_name=effective_topic_name,
                            summary_he=summary,
                            model_provider=provider,
                            model_name=model,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                else:
                    existing.protocol_title = resolved_protocol_title or existing.protocol_title
                    existing.topic_name = effective_topic_name or existing.topic_name
                    existing.model_provider = provider or existing.model_provider
                    existing.model_name = model or existing.model_name
                    existing.updated_at = now

                if has_topic_meta_table and topic_granularity_level is not None:
                    meta = db.execute(
                        select(RagDecisionSummaryTopicMeta).where(
                            RagDecisionSummaryTopicMeta.question_hash == question_hash,
                            RagDecisionSummaryTopicMeta.chunk_id == chunk_id,
                            RagDecisionSummaryTopicMeta.summary_he == summary,
                        )
                    ).scalar_one_or_none()
                    if meta is None:
                        db.add(
                            RagDecisionSummaryTopicMeta(
                                question_hash=question_hash,
                                chunk_id=chunk_id,
                                summary_he=summary,
                                topic_granularity_level=topic_granularity_level,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                    else:
                        meta.topic_granularity_level = topic_granularity_level
                        meta.updated_at = now


def _as_int_in_range(value: Any, *, min_value: int, max_value: int) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < min_value or parsed > max_value:
        return None
    return parsed


def _cache_topic_name(topic_name: str) -> str:
    root_topic, child_topic = _split_topic_path(topic_name)
    root_topic = _sanitize_topic_label(root_topic, min_tokens=1) or "ללא תיוג סמנטי"
    child_topic = _sanitize_topic_label(child_topic, min_tokens=2)
    if not child_topic or child_topic == "החלטה כללית":
        return root_topic

    normalized_child = child_topic.strip()
    if normalized_child in {"הזמנה תקציבית", "הצעת מחיר", "אישור תקציבי", "אישור הצעה"}:
        return root_topic

    child_tokens = [token for token in normalized_child.split() if token]
    if len(child_tokens) < 2:
        return root_topic
    if len(child_tokens) > 4:
        child_tokens = child_tokens[:4]
    return f"{root_topic} > {' '.join(child_tokens)}"


TOPIC_LABEL_STOP_TOKENS = {
    "פרוטוקול",
    "ועדה",
    "ועדת",
    "הועדה",
    "הוועדה",
    "מס",
    "מספר",
    "ישיבה",
    "דיון",
    "דיונים",
    "החלטה",
    "החלטות",
    "אישור",
    "מאשר",
    "מאשרים",
    "עיר",
    "בעיר",
    "עירייה",
    "העירייה",
}


def _sanitize_topic_label(value: str, *, min_tokens: int = 2, max_tokens: int = 4) -> str:
    normalized = normalize_for_search(str(value or ""))
    if not normalized:
        return ""
    tokens = [
        token
        for token in re.findall(r"[\u0590-\u05FF]{2,}", normalized)
        if token not in TOPIC_LABEL_STOP_TOKENS
    ]
    if len(tokens) < min_tokens:
        return ""
    return " ".join(tokens[:max_tokens]).strip()


def _split_topic_path(topic_name: str | None) -> tuple[str, str]:
    parts = [part.strip() for part in str(topic_name or "").split(">") if part and part.strip()]
    if len(parts) >= 2:
        return parts[0], " > ".join(parts[1:])
    if parts:
        return parts[0], "החלטה כללית"
    return "נושא כללי", "החלטה כללית"


TOPIC_VARIANT_DETAIL_TOKENS = {
    "קיימים",
    "מוארים",
    "חדש",
    "חדשים",
    "חדשה",
    "זמני",
    "זמנית",
    "ראשון",
    "שני",
    "נוסף",
    "נוספת",
    "נוספים",
    "נוספות",
}
TOPIC_MERGE_TOKEN_NORMALIZATION = {
    "בבקשות": "בקשות",
    "להקצאת": "להקצאה",
    "בהקצאת": "הקצאה",
}
PLACEHOLDER_TOPIC_LABEL = "ללא תיוג סמנטי"


def _topic_granularity_heuristic(topic: str) -> int:
    tokens = _topic_merge_tokens(topic)
    if not tokens:
        return 5
    if len(tokens) == 1:
        return 3
    if len(tokens) == 2:
        if tokens[1] in TOPIC_VARIANT_DETAIL_TOKENS:
            return 7
        return 5
    if len(tokens) == 3:
        return 7
    return 8


def _merge_head_phrase(topic: str, *, granularity_level: int) -> str:
    topic_norm = normalize_for_search(_sanitize_topic_label(topic, min_tokens=1, max_tokens=6))
    if topic_norm == normalize_for_search(PLACEHOLDER_TOPIC_LABEL):
        return PLACEHOLDER_TOPIC_LABEL

    tokens = _topic_merge_tokens(topic)
    if not tokens:
        return PLACEHOLDER_TOPIC_LABEL

    if tokens[0] in {"ללא", "בקשה", "בקשות"} and len(tokens) >= 2:
        if tokens[0] == "ללא" and tokens[1] == "תיוג":
            return PLACEHOLDER_TOPIC_LABEL
        return " ".join(tokens[:2])

    if len(tokens) >= 2 and tokens[0] == "בקשה" and tokens[1] in {"להקצאה", "להקצאת"}:
        return "בקשה להקצאה"

    if len(tokens) >= 2 and tokens[1] in TOPIC_VARIANT_DETAIL_TOKENS and granularity_level >= 6:
        return tokens[0]
    if len(tokens) >= 2:
        return " ".join(tokens[:2])
    return tokens[0]


def _topic_lexical_overlap(topic_a: str, topic_b: str) -> int:
    tokens_a = set(_topic_merge_tokens(topic_a))
    tokens_b = set(_topic_merge_tokens(topic_b))
    if not tokens_a or not tokens_b:
        return 0
    return len(tokens_a.intersection(tokens_b))


def _topic_merge_tokens(topic: str) -> list[str]:
    tokens = [token for token in _sanitize_topic_label(topic, min_tokens=1, max_tokens=6).split() if token]
    if not tokens:
        return []

    out: list[str] = []
    for token in tokens:
        out.append(TOPIC_MERGE_TOKEN_NORMALIZATION.get(token, token))
    return [token for token in out if token]


def _object_root_from_topic_parts(*, root_topic: str, child_topic: str) -> str | None:
    combined_tokens = set(_topic_merge_tokens(f"{root_topic} {child_topic}"))
    if not combined_tokens:
        return None

    if any(token in combined_tokens for token in {"הסכם", "הסכמים", "חוזה", "חוזים", "רשות"}):
        return "הסכמים"
    if any(token in combined_tokens for token in {"תמרור", "תמרורים"}):
        return "תמרורים"
    if any(token in combined_tokens for token in {"הקצאה", "הקצאות", "להקצאה", "בקשה", "בקשות"}):
        return "הקצאות"
    if any(token in combined_tokens for token in {"ניקיון"}):
        return "ניקיון"
    if any(token in combined_tokens for token in {"אבטחה", "אבטחת"}):
        return "אבטחה"
    return None


def _canonicalize_topic_child(*, root_topic: str, child_topic: str) -> str:
    child_norm = normalize_for_search(child_topic)
    root_norm = normalize_for_search(root_topic)
    if not child_norm:
        return child_topic

    if root_norm == normalize_for_search("הקצאות"):
        if "עמות" in child_norm and "הקצא" in child_norm:
            return "הקצאה לעמותה"
        if "קרקע" in child_norm and "מבנ" in child_norm:
            return "הקצאת קרקעות ומבנים"
        if child_norm in {
            normalize_for_search("בקשה להקצאה"),
            normalize_for_search("החלטת הקצאות"),
            normalize_for_search("פרסום בעיתונות"),
            normalize_for_search("החלטה כללית"),
        }:
            return "אישור הקצאה"

    if root_norm == normalize_for_search("הסכמים"):
        if "הסכם" in child_norm and "רשות" in child_norm:
            if "עמות" in child_norm:
                return "הסכם רשות לעמותה"
            if "עירייה" in child_norm:
                return "הסכם רשות לעירייה"
            return "הסכם רשות"

    return child_topic


def _merge_root_payload_into_target(*, target: dict[str, Any], source: dict[str, Any]) -> None:
    target["count"] += int(source["count"])
    target["protocols"].update(source["protocols"])

    source_last_seen = source.get("last_seen_at")
    target_last_seen = target.get("last_seen_at")
    if source_last_seen and (not target_last_seen or str(source_last_seen) > str(target_last_seen)):
        target["last_seen_at"] = source_last_seen

    for topic, source_variant in source["variants"].items():
        target_variant = target["variants"].setdefault(
            topic,
            {
                "count": 0,
                "last_seen_at": None,
                "protocols": set(),
                "avg_granularity": 0.0,
                "samples": 0,
            },
        )
        previous_samples = int(target_variant["samples"])
        source_samples = int(source_variant["samples"])
        total_samples = previous_samples + source_samples
        if total_samples > 0:
            target_variant["avg_granularity"] = (
                (float(target_variant["avg_granularity"]) * previous_samples)
                + (float(source_variant["avg_granularity"]) * source_samples)
            ) / total_samples
        target_variant["samples"] = total_samples
        target_variant["count"] += int(source_variant["count"])
        target_variant["protocols"].update(source_variant["protocols"])

        source_variant_last_seen = source_variant.get("last_seen_at")
        target_variant_last_seen = target_variant.get("last_seen_at")
        if source_variant_last_seen and (
            not target_variant_last_seen or str(source_variant_last_seen) > str(target_variant_last_seen)
        ):
            target_variant["last_seen_at"] = source_variant_last_seen


def _coalesce_sparse_single_token_roots(global_roots: dict[str, dict[str, Any]]) -> None:
    root_topics = list(global_roots.keys())
    for topic in root_topics:
        source_payload = global_roots.get(topic)
        if source_payload is None:
            continue

        source_tokens = _topic_merge_tokens(topic)
        if len(source_tokens) != 1:
            continue
        if int(source_payload["count"]) > 2:
            continue

        token = source_tokens[0]
        best_target_topic: str | None = None
        best_target_score = -1.0
        for candidate_topic, candidate_payload in global_roots.items():
            if candidate_topic == topic:
                continue
            candidate_tokens = _topic_merge_tokens(candidate_topic)
            if len(candidate_tokens) < 2:
                continue
            if token not in set(candidate_tokens):
                continue

            protocol_overlap = len(source_payload["protocols"].intersection(candidate_payload["protocols"]))
            score = float(candidate_payload["count"]) + (0.6 * protocol_overlap)
            if score > best_target_score:
                best_target_score = score
                best_target_topic = candidate_topic

        if not best_target_topic:
            continue
        target_payload = global_roots.get(best_target_topic)
        if target_payload is None:
            continue
        _merge_root_payload_into_target(target=target_payload, source=source_payload)
        global_roots.pop(topic, None)


def _infer_topic_path_from_summary(*, summary_he: str, protocol_title: str) -> str | None:
    summary_norm = normalize_for_search(summary_he)
    title_norm = normalize_for_search(protocol_title)
    combined = f"{summary_norm} {title_norm}".strip()
    if not combined:
        return None

    if "הסכם" in combined or "חוזה" in combined or "רשות" in combined:
        child = "הסכם רשות"
        if any(token in combined for token in {"לעמותה", "עמותה", "עמותת", "עמותות"}):
            child = "הסכם רשות לעמותה"
        elif any(token in combined for token in {"לעירייה", "עירייה", "העירייה"}):
            child = "הסכם רשות לעירייה"
        return f"הסכמים > {child}"

    if "הקצאה" in combined or "הקצאות" in combined or "עמותה" in combined:
        return "הקצאות > הקצאה לעמותה"

    if "תמרור" in combined or "תמרורים" in combined:
        if "מוארים" in combined:
            return "תמרורים > תמרורים מוארים"
        if "קיימים" in combined:
            return "תמרורים > תמרורים קיימים"
        return "תמרורים > תמרורים עירוניים"

    if "ניקיון" in combined:
        return "ניקיון > ניקיון מוסדות"

    if "אבטחה" in combined:
        return "אבטחה > אבטחת מוסדות"

    return "החלטות עירוניות > החלטה ענפית"


PROTOCOL_HEADLINE_RE = re.compile(r"(?:^|\n)\s*([^:\n]{6,120})\s*:")
IGNORED_PROTOCOL_HEADLINES = {
    "מהלך הדיון",
    "החלטות",
    "נוכחים",
    "נעדרו",
    "סיכום והחלטות",
    "השתתפו",
    "נושא הוועדה",
}
HEADLINE_VALUE_FROM_RIGHT_LABELS = {
    "נושא הוועדה",
    "נושא",
    "נושא הדיון",
    "מהות הבקשה",
}
IGNORED_HEADLINE_PREFIXES = (
    "להלן",
    "זומנו",
    "נוכחים",
    "נעדרו",
    "השתתפו",
    "החלטות",
    "סיכום",
    "מהלך",
)
TOPIC_CUE_TOKENS = {
    "פינויים",
    "פינוי",
    "מתחם",
    "רובע",
    "בטיחות",
    "תמרורים",
    "תמרור",
    "חציה",
    "התמכרות",
    "סמים",
    "סם",
    "תרופות",
    "קופות",
    "בריאות",
    "הכשרה",
    "הכשרות",
    "חינוך",
    "תחבורה",
    "תנועה",
    "עמותת",
    "עמותות",
    "הסכם",
    "הסכמים",
    "חוזה",
    "חוזים",
    "הקצאה",
    "הקצאות",
    "בקשה",
    "בקשות",
    "רשות",
}

PROCEDURAL_ALLOCATION_NEIGHBOR_PATTERNS = [
    re.compile(r"מאשרים\s+החלטת\s+הועדה\s+המקצועית\s+להקצאות\s+קרקע"),
    re.compile(r"מאשרים\s+ביצוע\s+פרסום\s+(?:זמני|ראשון|שני)\s+בעיתונות"),
    re.compile(r"פרסום\s+שני\s+בעיתונות"),
]
NEIGHBOR_METADATA_CUE_TOKENS = {
    "מהות הבקשה",
    "גוש",
    "חלקה",
    "מגרש",
    "כתובת",
    "שטח",
    "שימושים",
    "תאור",
}
PROTOCOL_NEIGHBOR_LOOKBACK_CHUNKS = 8
PROTOCOL_NEIGHBOR_LOOKAHEAD_CHUNKS = 2
PROTOCOL_NEIGHBOR_MAX_ADDED = 80


def _context_is_procedural_allocation_target(context: RagContextChunk) -> bool:
    if context.source_kind != "protocol":
        return False
    text = normalize_for_search(f"{context.chunk_text} {context.snippet}")
    if not text or "הקצא" not in text:
        return False
    for pattern in PROCEDURAL_ALLOCATION_NEIGHBOR_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _is_neighbor_metadata_candidate(text_value: str) -> bool:
    normalized = normalize_for_search(text_value)
    if not normalized:
        return False
    return any(token in normalized for token in NEIGHBOR_METADATA_CUE_TOKENS)


def _augment_protocol_neighbor_contexts(
    *,
    db,
    contexts: list[RagContextChunk],
) -> list[RagContextChunk]:
    if not contexts:
        return contexts

    existing_chunk_ids = {str(context.chunk_id) for context in contexts if context.chunk_id}
    template_by_doc: dict[int, RagContextChunk] = {}
    for context in contexts:
        if context.source_kind != "protocol":
            continue
        template_by_doc.setdefault(int(context.document_id), context)

    target_contexts = [context for context in contexts if _context_is_procedural_allocation_target(context)]
    if not target_contexts:
        return contexts

    target_chunk_ids = [str(context.chunk_id) for context in target_contexts if context.chunk_id]
    if not target_chunk_ids:
        return contexts

    target_rows = db.execute(
        select(TextChunk.chunk_id, TextChunk.document_id, TextChunk.chunk_index)
        .where(TextChunk.chunk_id.in_(target_chunk_ids))
        .where(TextChunk.source_kind == "protocol")
    ).all()
    if not target_rows:
        return contexts

    added_rows: dict[str, Any] = {}
    for chunk_id, document_id, chunk_index in target_rows:
        if chunk_index is None:
            continue
        try:
            index_value = int(chunk_index)
        except (TypeError, ValueError):
            continue

        neighbor_rows = db.execute(
            select(
                TextChunk.chunk_id,
                TextChunk.document_id,
                TextChunk.chunk_index,
                TextChunk.chunk_text,
                TextChunk.start_page,
                TextChunk.end_page,
                TextChunk.citation_label,
            )
            .where(TextChunk.document_id == int(document_id))
            .where(TextChunk.source_kind == "protocol")
            .where(TextChunk.chunk_index >= index_value - PROTOCOL_NEIGHBOR_LOOKBACK_CHUNKS)
            .where(TextChunk.chunk_index <= index_value + PROTOCOL_NEIGHBOR_LOOKAHEAD_CHUNKS)
            .order_by(TextChunk.chunk_index.asc())
        ).all()

        for row in neighbor_rows:
            neighbor_chunk_id = str(row[0])
            if neighbor_chunk_id in existing_chunk_ids or neighbor_chunk_id in added_rows:
                continue
            neighbor_text = str(row[3] or "")
            if not _is_neighbor_metadata_candidate(neighbor_text):
                continue
            added_rows[neighbor_chunk_id] = row
            if len(added_rows) >= PROTOCOL_NEIGHBOR_MAX_ADDED:
                break
        if len(added_rows) >= PROTOCOL_NEIGHBOR_MAX_ADDED:
            break

    if not added_rows:
        return contexts

    semantic_labels_by_chunk: dict[str, list[str]] = {}
    semantic_rows = db.execute(
        select(ChunkSemanticLink.chunk_id, SemanticNode.pref_label_he)
        .join(SemanticNode, SemanticNode.id == ChunkSemanticLink.semantic_node_id)
        .where(ChunkSemanticLink.chunk_id.in_(list(added_rows.keys())))
        .where(SemanticNode.node_kind == "topic")
    ).all()
    for chunk_id, label_he in semantic_rows:
        key = str(chunk_id)
        label = str(label_he or "").strip()
        if not label:
            continue
        bucket = semantic_labels_by_chunk.setdefault(key, [])
        normalized = normalize_for_search(label)
        if normalized and normalized not in {normalize_for_search(existing) for existing in bucket}:
            bucket.append(label)

    added_contexts: list[RagContextChunk] = []
    for row in added_rows.values():
        neighbor_chunk_id = str(row[0])
        document_id = int(row[1])
        chunk_index = int(row[2]) if row[2] is not None else None
        chunk_text = str(row[3] or "")
        start_page = int(row[4]) if row[4] is not None else None
        end_page = int(row[5]) if row[5] is not None else None
        citation_label = str(row[6] or "").strip()

        template = template_by_doc.get(document_id)
        if template is None:
            continue

        compact = " ".join(chunk_text.split())
        snippet = compact[:300]
        if not citation_label:
            citation_label = f"p.{start_page}" if start_page is not None else template.citation

        added_contexts.append(
            RagContextChunk(
                chunk_id=neighbor_chunk_id,
                score=max(0.05, float(template.score) * 0.62),
                snippet=snippet,
                citation=citation_label,
                source_kind="protocol",
                document_id=document_id,
                document_title=template.document_title,
                document_url=template.document_url,
                municipality_slug=template.municipality_slug,
                meeting_external_id=template.meeting_external_id,
                start_page=start_page,
                end_page=end_page,
                chunk_index=chunk_index,
                chunk_text=chunk_text,
                semantic_topic_labels=semantic_labels_by_chunk.get(neighbor_chunk_id, []),
            )
        )

    if not added_contexts:
        return contexts
    return [*contexts, *added_contexts]


def _normalize_headline_text(value: str) -> str:
    cleaned = " ".join(str(value or "").split())
    cleaned = cleaned.replace('"', "").replace("׳", "'").replace("״", "")
    cleaned = cleaned.strip("-–:;,.()[]{} ")
    return cleaned


def _is_ignored_headline(value: str) -> bool:
    normalized = _normalize_headline_text(value)
    if not normalized:
        return True
    if normalized in IGNORED_PROTOCOL_HEADLINES:
        return True
    lowered = normalized.casefold()
    if lowered.startswith("לגבי"):
        return True
    if lowered.startswith(IGNORED_HEADLINE_PREFIXES):
        return True
    return False


def _headline_has_topic_cue(value: str) -> bool:
    normalized = normalize_for_search(value)
    if not normalized:
        return False

    tokens = [token for token in normalized.split(" ") if token]
    if len(tokens) < 2:
        return False
    return any(token in TOPIC_CUE_TOKENS for token in tokens)


def _headline_candidates_from_chunk_text(text: str) -> list[str]:
    if not text:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for raw_line in str(text).splitlines():
        line = " ".join(raw_line.split())
        if not line or ":" not in line:
            continue

        left, right = line.split(":", 1)
        left_norm = _normalize_headline_text(left)
        right_norm = _normalize_headline_text(right)

        candidate = left_norm
        if _normalize_headline_text(left_norm) in HEADLINE_VALUE_FROM_RIGHT_LABELS and right_norm:
            candidate = right_norm

        candidate = _normalize_headline_text(candidate)
        if not candidate:
            continue
        if _is_ignored_headline(candidate):
            continue
        if len(candidate) > 80:
            continue
        if not _headline_has_topic_cue(candidate):
            continue

        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)

    if not out:
        for match in PROTOCOL_HEADLINE_RE.finditer(text):
            candidate = _normalize_headline_text(match.group(1))
            if not candidate or _is_ignored_headline(candidate):
                continue
            if not _headline_has_topic_cue(candidate):
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate)
    return out


def _load_protocol_subject_anchors(
    *,
    db,
    protocol_document_ids: list[int],
) -> dict[int, list[str]]:
    normalized_ids: set[int] = set()
    for doc_id in protocol_document_ids:
        try:
            normalized = int(doc_id)
        except (TypeError, ValueError):
            continue
        if normalized > 0:
            normalized_ids.add(normalized)

    unique_document_ids = sorted(normalized_ids)
    if not unique_document_ids:
        return {}

    rows = db.execute(
        select(TextChunk.document_id, TextChunk.chunk_index, TextChunk.chunk_text)
        .where(TextChunk.document_id.in_(unique_document_ids))
        .where(TextChunk.source_kind == "protocol")
        .order_by(TextChunk.document_id.asc(), TextChunk.chunk_index.asc())
    ).all()

    scored: dict[int, dict[str, float]] = {}
    for document_id, chunk_index, chunk_text in rows:
        candidates = _headline_candidates_from_chunk_text(str(chunk_text or ""))
        if not candidates:
            continue
        index_value = int(chunk_index or 0)
        position_score = 1.0 / max(1, index_value + 1)
        bucket = scored.setdefault(int(document_id), {})
        for candidate in candidates:
            bucket[candidate] = bucket.get(candidate, 0.0) + position_score

    out: dict[int, list[str]] = {}
    for document_id, candidate_scores in scored.items():
        ordered = [
            candidate
            for candidate, _ in sorted(candidate_scores.items(), key=lambda row: row[1], reverse=True)
        ]
        if ordered:
            out[document_id] = ordered[:5]
    return out


def _load_protocol_semantic_topic_labels(
    *,
    db,
    protocol_document_ids: list[int],
) -> dict[int, list[str]]:
    normalized_ids: set[int] = set()
    for doc_id in protocol_document_ids:
        try:
            normalized = int(doc_id)
        except (TypeError, ValueError):
            continue
        if normalized > 0:
            normalized_ids.add(normalized)

    unique_document_ids = sorted(normalized_ids)
    if not unique_document_ids:
        return {}

    rows = db.execute(
        select(TextChunk.document_id, SemanticNode.pref_label_he, ChunkSemanticLink.confidence)
        .join(ChunkSemanticLink, ChunkSemanticLink.chunk_id == TextChunk.chunk_id)
        .join(SemanticNode, SemanticNode.id == ChunkSemanticLink.semantic_node_id)
        .where(TextChunk.document_id.in_(unique_document_ids))
        .where(TextChunk.source_kind == "protocol")
        .where(SemanticNode.node_kind == "topic")
    ).all()

    scored: dict[int, dict[str, float]] = {}
    for document_id, label_he, confidence in rows:
        label = _sanitize_topic_label(str(label_he or ""), min_tokens=2)
        if not label:
            continue
        bucket = scored.setdefault(int(document_id), {})
        bucket[label] = bucket.get(label, 0.0) + max(0.0, min(1.0, float(confidence or 0.0)))

    out: dict[int, list[str]] = {}
    for document_id, label_scores in scored.items():
        ordered = [
            label
            for label, _ in sorted(label_scores.items(), key=lambda row: row[1], reverse=True)
            if label
        ]
        if ordered:
            out[document_id] = ordered[:6]
    return out


def _load_decision_request_contexts_by_chunk(
    *,
    db,
    contexts: list[RagContextChunk],
) -> dict[str, dict[str, Any]]:
    if not contexts:
        return {}
    if not inspect(db.get_bind()).has_table("decision_request_context"):
        return {}

    protocol_contexts = [
        context
        for context in contexts
        if context.source_kind == "protocol" and context.document_id and context.chunk_id
    ]
    if not protocol_contexts:
        return {}

    document_ids = sorted({int(context.document_id) for context in protocol_contexts})
    rows = db.execute(
        select(
            DecisionRequestContext.decision_id,
            DecisionRequestContext.source_document_id,
            DecisionRequestContext.request_subject_he,
            DecisionRequestContext.subject_topic_he,
            DecisionRequestContext.address_he,
            DecisionRequestContext.gush,
            DecisionRequestContext.helka,
            DecisionRequestContext.migrash,
            DecisionRequestContext.source_chunk_ids_json,
            DecisionRequestContext.confidence,
            Decision.agenda_item,
            Decision.decision_text,
            DecisionCitation.start_offset,
            DecisionCitation.end_offset,
        )
        .join(Decision, Decision.id == DecisionRequestContext.decision_id)
        .join(DecisionCitation, DecisionCitation.decision_id == DecisionRequestContext.decision_id)
        .where(DecisionRequestContext.source_document_id.in_(document_ids))
        .where(DecisionCitation.document_id.in_(document_ids))
        .where(DecisionCitation.source_type == "protocol")
    ).all()

    contexts_by_document: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        source_chunk_ids = _loads_json(row[8])
        contexts_by_document.setdefault(int(row[1]), []).append(
            {
                "decision_id": int(row[0]),
                "source_document_id": int(row[1]),
                "request_subject_he": row[2],
                "subject_topic_he": row[3],
                "address_he": row[4],
                "gush": row[5],
                "helka": row[6],
                "migrash": row[7],
                "source_chunk_ids": source_chunk_ids if isinstance(source_chunk_ids, list) else [],
                "confidence": float(row[9] or 0.0),
                "agenda_item": row[10],
                "decision_text": row[11],
                "start_offset": int(row[12]) if row[12] is not None else None,
                "end_offset": int(row[13]) if row[13] is not None else None,
            }
        )

    out: dict[str, dict[str, Any]] = {}
    for context in protocol_contexts:
        candidates = contexts_by_document.get(int(context.document_id), [])
        if not candidates:
            continue

        best_payload: dict[str, Any] | None = None
        best_score = float("-inf")
        for candidate in candidates:
            score = float(candidate.get("confidence") or 0.0)
            if str(context.chunk_id) in {str(item) for item in candidate.get("source_chunk_ids") or []}:
                score += 2.0
            start_offset = candidate.get("start_offset")
            end_offset = candidate.get("end_offset")
            if (
                start_offset is not None
                and end_offset is not None
                and context.start_offset is not None
                and context.end_offset is not None
            ):
                if context.start_offset < end_offset and start_offset < context.end_offset:
                    score += 1.0
                else:
                    distance = min(
                        abs(int(context.start_offset) - int(start_offset)),
                        abs(int(context.end_offset) - int(end_offset)),
                    )
                    if distance <= 2200:
                        score += max(0.0, 0.5 - (distance / 5000.0))
            if score > best_score:
                best_score = score
                best_payload = candidate

        if best_payload is None:
            continue
        out[str(context.chunk_id)] = dict(best_payload)
    return out


def _decision_request_context_payload(*, db, decision_id: int) -> dict[str, Any] | None:
    if not inspect(db.get_bind()).has_table("decision_request_context"):
        return None

    row = db.execute(
        select(DecisionRequestContext).where(DecisionRequestContext.decision_id == decision_id)
    ).scalar_one_or_none()
    if row is None:
        return None

    source_chunk_ids = _loads_json(row.source_chunk_ids_json)
    return {
        "request_subject_he": row.request_subject_he,
        "subject_topic_he": row.subject_topic_he,
        "address_he": row.address_he,
        "gush": row.gush,
        "helka": row.helka,
        "migrash": row.migrash,
        "source_chunk_ids": source_chunk_ids if isinstance(source_chunk_ids, list) else [],
        "confidence": row.confidence,
    }


def _load_cached_topic_tree(
    *,
    db,
    protocol_titles: list[str],
) -> dict[str, list[str]]:
    normalized_titles = sorted({str(title).strip() for title in protocol_titles if str(title).strip()})
    if not normalized_titles:
        return {}

    if not inspect(db.get_bind()).has_table("rag_decision_summary_cache"):
        return {}

    rows = db.execute(
        select(
            RagDecisionSummaryCache.protocol_title,
            RagDecisionSummaryCache.topic_name,
            RagDecisionSummaryCache.updated_at,
        ).where(RagDecisionSummaryCache.protocol_title.in_(normalized_titles))
    ).all()

    tree_scores: dict[str, dict[str, float]] = {}
    for protocol_title, topic_name, updated_at in rows:
        protocol_key = str(protocol_title or "").strip()
        if not protocol_key:
            continue
        root_topic, child_topic = _split_topic_path(topic_name)
        root_topic = _sanitize_topic_label(root_topic, min_tokens=1)
        child_topic = _sanitize_topic_label(child_topic, min_tokens=2)
        if not root_topic:
            root_topic = "ללא תיוג סמנטי"
        if not child_topic or child_topic == "החלטה כללית":
            continue
        score = 1.0
        if isinstance(updated_at, datetime):
            age_days = max(0.0, (datetime.utcnow() - updated_at).total_seconds() / 86400.0)
            score = max(0.2, 1.0 - min(age_days / 120.0, 0.8))

        protocol_bucket = tree_scores.setdefault(protocol_key, {})
        protocol_bucket[child_topic] = protocol_bucket.get(child_topic, 0.0) + score

    out: dict[str, list[str]] = {}
    for protocol_title, child_scores in tree_scores.items():
        ordered_children = [
            child
            for child, _ in sorted(child_scores.items(), key=lambda row: row[1], reverse=True)
            if child and child != "החלטה כללית"
        ]
        if ordered_children:
            out[protocol_title] = ordered_children[:50]
    return out


def _run_ask(
    *,
    request: AskRequest,
    db,
    llm_client: RagLlmClient | None = None,
) -> dict:
    request_started = time.perf_counter()
    ask_request_id = new_ask_request_id()
    question_hash = hash_text(request.question)
    log_rag_event(
        "rag.ask.request",
        ask_request_id=ask_request_id,
        question_hash=question_hash,
        top_k=request.top_k,
        municipality_slug=request.muni,
        source_types=request.source_types or [],
        required_source_types=request.required_source_types or [],
        year=request.year,
        topic=request.topic,
        semantic_node_id=request.semantic_node_id,
        semantic_label=request.semantic_label,
        semantic_mode=request.semantic_mode,
    )

    embedding_service = ChunkEmbeddingService(db)
    retrieval_service = RagRetrievalService(
        search_service=SearchService(db),
        reranker=EmbeddingReranker(embedding_service),
    )
    retrieval_started = time.perf_counter()
    retrieval_result = retrieval_service.retrieve(
        query=request.question,
        top_k=request.top_k,
        municipality_slug=request.muni,
        source_kinds=request.source_types,
        year=request.year,
        topic=request.topic,
        semantic_node_id=request.semantic_node_id,
        semantic_label=request.semantic_label,
        semantic_mode=request.semantic_mode,
        ask_request_id=ask_request_id,
    )
    retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000.0, 3)

    answering_contexts = _augment_protocol_neighbor_contexts(
        db=db,
        contexts=list(retrieval_result.contexts),
    )
    answering_contexts, context_dedupe_stats = _collapse_duplicate_answering_contexts(
        embedding_service=embedding_service,
        question=request.question,
        contexts=answering_contexts,
    )
    retrieval_for_answering = RagRetrievalResult(
        query=retrieval_result.query,
        normalized_query=retrieval_result.normalized_query,
        top_k=retrieval_result.top_k,
        retrieval_set_id=retrieval_result.retrieval_set_id,
        requested_source_kinds=list(retrieval_result.requested_source_kinds),
        contexts=answering_contexts,
        debug_info={**dict(retrieval_result.debug_info), "answer_context_dedupe": context_dedupe_stats},
    )

    protocol_titles = [
        context.document_title
        for context in retrieval_for_answering.contexts
        if context.source_kind == "protocol" and context.document_title
    ]
    protocol_document_ids = [
        context.document_id
        for context in retrieval_for_answering.contexts
        if context.source_kind == "protocol"
    ]
    cached_topic_tree = _load_cached_topic_tree(
        db=db,
        protocol_titles=protocol_titles,
    )
    protocol_subject_anchors = _load_protocol_subject_anchors(
        db=db,
        protocol_document_ids=protocol_document_ids,
    )
    protocol_semantic_topic_labels = _load_protocol_semantic_topic_labels(
        db=db,
        protocol_document_ids=protocol_document_ids,
    )
    decision_request_context_by_chunk = _load_decision_request_contexts_by_chunk(
        db=db,
        contexts=retrieval_for_answering.contexts,
    )

    resolved_llm_client = llm_client or build_rag_llm_client()
    answering_service = RagAnsweringService(llm_client=resolved_llm_client)
    answer_cache_service = RagAnswerCacheService(db, embedding_service=embedding_service)
    answering_started = time.perf_counter()
    answer_result: RagAnswerResult | None = None
    cache_hit = answer_cache_service.lookup(
        query=request.question,
        retrieval_set_id=retrieval_result.retrieval_set_id,
    )
    if cache_hit is not None:
        answer_result = _answer_result_from_cache_payload(cache_hit.payload)
        if answer_result is None:
            cache_hit = None
        else:
            answer_result.scoring = {
                **dict(answer_result.scoring),
                "semantic_answer_cache_hit": True,
                "semantic_answer_cache_similarity": cache_hit.similarity,
                "semantic_answer_cache_id": cache_hit.cache_id,
                "answer_external_api_called": False,
                "external_call_count": 0,
            }
    if cache_hit is None:
        low_relevance_gate = _low_relevance_embedding_refusal(
            embedding_service=embedding_service,
            question=request.question,
            retrieval_result=retrieval_for_answering,
        )
        if low_relevance_gate is not None:
            answer_result = answering_service._build_refusal(
                question=request.question,
                retrieval=retrieval_for_answering,
                reason_code="INSUFFICIENT_EVIDENCE",
                missing_source_kinds=[],
                ask_request_id=ask_request_id,
                scoring={
                    "low_relevance_gate": low_relevance_gate,
                    "answer_external_api_called": False,
                    "external_call_count": 0,
                },
            )
        else:
            answer_result = answering_service.compose(
                question=request.question,
                retrieval=retrieval_for_answering,
                required_source_kinds=request.required_source_types,
                cached_topic_tree=cached_topic_tree,
                protocol_subject_anchors=protocol_subject_anchors,
                protocol_semantic_topic_labels=protocol_semantic_topic_labels,
                decision_request_context_by_chunk=decision_request_context_by_chunk,
                ask_request_id=ask_request_id,
            )
    if answer_result is None:
        raise HTTPException(status_code=500, detail="ask_answer_result_missing")
    answering_ms = round((time.perf_counter() - answering_started) * 1000.0, 3)
    total_ms = round((time.perf_counter() - request_started) * 1000.0, 3)

    limitations = list(answer_result.limitations)
    if answer_result.status == "answer" and len(retrieval_result.source_kinds) <= 1:
        limitations.append("הראיות חלקיות ומבוססות על סוג מקור אחד בלבד.")

    if answer_result.status == "answer":
        protocol_title_by_chunk_id = {
            str(context.chunk_id): str(context.document_title)
            for context in retrieval_for_answering.contexts
            if context.source_kind == "protocol" and context.chunk_id and context.document_title
        }
        if not bool(answer_result.scoring.get("semantic_answer_cache_hit")):
            answer_cache_service.store(
                query=request.question,
                query_hash=question_hash,
                retrieval_set_id=retrieval_result.retrieval_set_id,
                answer_provider=answer_result.provider,
                answer_model=answer_result.model,
                answer_payload=_answer_result_to_cache_payload(answer_result),
            )
        _persist_decision_summary_cache(
            db=db,
            question_hash=question_hash,
            answer_sections=answer_result.answer_sections,
            protocol_title_by_chunk_id=protocol_title_by_chunk_id,
            provider=answer_result.provider,
            model=answer_result.model,
        )
        db.commit()

    citations_payload = [
        {
            "chunk_id": citation.chunk_id,
            "source_type": citation.source_kind,
            "citation": citation.citation_label,
            "start_page": citation.start_page,
            "end_page": citation.end_page,
            "document": {
                "id": citation.document_id,
                "title": citation.document_title,
                "url": citation.document_url,
            },
            "score": citation.score,
        }
        for citation in answer_result.citations
    ]

    refusal_payload = None
    if answer_result.status == "refusal":
        refusal_payload = {
            "reason_code": answer_result.refusal_reason_code,
            "message_he": answer_result.refusal_message_he,
            "missing_source_types": answer_result.missing_source_kinds,
        }

    response_payload = {
        "ask_request_id": ask_request_id,
        "status": answer_result.status,
        "question": request.question,
        "answer": answer_result.answer,
        "extended_answer": answer_result.extended_answer,
        "answer_sections": list(answer_result.answer_sections),
        "extended_answer_sections": list(answer_result.extended_answer_sections),
        "citations": citations_payload,
        "claim_assessments": list(answer_result.claim_assessments),
        "limitations": limitations,
        "refusal": refusal_payload,
        "scoring": dict(answer_result.scoring),
        "retrieval": {
            "retrieval_set_id": retrieval_result.retrieval_set_id,
            "count": len(retrieval_result.contexts),
            "source_types": sorted(retrieval_result.source_kinds),
            "requested_source_types": retrieval_result.requested_source_kinds,
            "top_k": retrieval_result.top_k,
            "semantic_mode": request.semantic_mode,
            "semantic_node_id": request.semantic_node_id,
            "semantic_label": request.semantic_label,
        },
        "model": {
            "provider": answer_result.provider,
            "name": answer_result.model,
        },
    }
    if request.debug_mode:
        trace_payload = _normalized_answering_trace(answer_result.scoring)
        retrieval_trace = dict(retrieval_result.debug_info)
        timing_breakdown = {
            "request_total": total_ms,
            "retrieval": retrieval_ms,
            "answering": answering_ms,
            "answering_stages": trace_payload.get("timing_ms") if isinstance(trace_payload.get("timing_ms"), dict) else {},
        }
        response_payload["debug"] = {
            "enabled": True,
            "thresholds": _ask_thresholds_payload(request=request, llm_client=resolved_llm_client),
            "answering_trace": trace_payload,
            "retrieval_trace": retrieval_trace,
            "timing_ms": timing_breakdown,
            "pipeline": {
                "stage_order": [
                    "retrieve",
                    "answer",
                    "deterministic_similarity",
                    "verify_fallback_if_low",
                    "refuse_if_needed",
                ],
                "effective_required_source_types": request.required_source_types
                or retrieval_result.requested_source_kinds,
            },
        }

    log_rag_event(
        "rag.ask.response",
        ask_request_id=ask_request_id,
        question_hash=question_hash,
        status=answer_result.status,
        retrieval_set_id=retrieval_result.retrieval_set_id,
        retrieval_count=len(retrieval_result.contexts),
        retrieval_source_types=sorted(retrieval_result.source_kinds),
        citation_count=len(citations_payload),
        refusal_reason_code=answer_result.refusal_reason_code,
        provider=answer_result.provider,
        model=answer_result.model,
    )

    sample_rate = audit_sample_rate()
    if should_sample_audit(rate=sample_rate):
        log_rag_event(
            "rag.ask.audit_sample",
            ask_request_id=ask_request_id,
            question_hash=question_hash,
            sample_rate=sample_rate,
            status=answer_result.status,
            retrieval_set_id=retrieval_result.retrieval_set_id,
            citation_chunk_ids=[citation["chunk_id"] for citation in citations_payload],
            refusal_reason_code=answer_result.refusal_reason_code,
            missing_source_types=answer_result.missing_source_kinds,
            answer_preview=(answer_result.answer or "")[:280],
        )

    return response_payload


def _decision_payload(decision_id: int, db) -> dict | None:
    decision = db.execute(
        select(Decision).where(Decision.id == decision_id, Decision.is_public.is_(True))
    ).scalar_one_or_none()
    if decision is None:
        return None

    meeting = db.execute(select(Meeting).where(Meeting.id == decision.meeting_id)).scalar_one_or_none()
    municipality_slug = None
    if meeting is not None:
        site = db.execute(select(SourceSite).where(SourceSite.id == meeting.source_site_id)).scalar_one_or_none()
        municipality_slug = site.municipality_slug if site else None

    citations = db.execute(
        select(DecisionCitation, Document)
        .join(Document, Document.id == DecisionCitation.document_id)
        .where(DecisionCitation.decision_id == decision.id)
        .order_by(DecisionCitation.page_number.asc(), DecisionCitation.start_offset.asc())
    ).all()

    vote = db.execute(select(Vote).where(Vote.decision_id == decision.id)).scalar_one_or_none()
    linked_docs = db.execute(
        select(DecisionDocumentLink, Document)
        .join(Document, Document.id == DecisionDocumentLink.document_id)
        .where(DecisionDocumentLink.decision_id == decision.id)
        .order_by(Document.id.asc())
    ).all()

    return {
        "decision": {
            "id": decision.id,
            "meeting_id": decision.meeting_id,
            "meeting_external_id": meeting.meeting_external_id if meeting else None,
            "municipality": municipality_slug,
            "decision_number": decision.decision_number,
            "agenda_item": decision.agenda_item,
            "decision_text": decision.decision_text,
            "parser_confidence": decision.parser_confidence,
            "source_type": "protocol",
            "metadata": _fallback_metadata_from_json(decision.metadata_json),
            "request_context": _decision_request_context_payload(db=db, decision_id=decision.id),
        },
        "votes": [
            {
                "for_count": vote.for_count,
                "against_count": vote.against_count,
                "abstain_count": vote.abstain_count,
                "unanimous": vote.unanimous,
                "is_uncertain": vote.is_uncertain,
                "confidence": vote.confidence,
                "raw_text": vote.raw_text,
            }
            for vote in [vote]
            if vote is not None
        ],
        "citations": [
            {
                "id": citation.id,
                "document": {
                    "id": document.id,
                    "title": document.title_he,
                    "url": document.canonical_url,
                    "source_type": citation.source_type,
                },
                "page": citation.page_number,
                "start_offset": citation.start_offset,
                "end_offset": citation.end_offset,
                "anchor_label": citation.anchor_label,
                "anchor_text": citation.anchor_text,
            }
            for citation, document in citations
        ],
        "linked_documents": [
            {
                "id": document.id,
                "title": document.title_he,
                "url": document.canonical_url,
                "doc_kind": document.doc_kind,
                "source_type": link.source_type,
                "provenance": link.provenance,
            }
            for link, document in linked_docs
        ],
    }


@app.on_event("startup")
def startup() -> None:
    apply_all(engine, Path("migrations"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/crawl/run")
def crawl_run(muni: str, root_url: str, db=Depends(get_db)) -> dict[str, int]:
    service = PipelineService(
        session=db,
        html_fetcher=_default_html_fetcher,
        fetcher=AssetFetcher(),
        storage_root=Path("storage/raw"),
    )
    run_id = service.run_crawl(muni, root_url)
    return {"run_id": run_id}


@app.post("/process/run")
def process_run(doc_id: int | None = None, muni: str | None = None, db=Depends(get_db)) -> dict[str, int]:
    service = ProcessingService(
        session=db,
        storage_root=Path("storage/raw"),
    )
    run_id = service.run(doc_id=doc_id, municipality_slug=muni)
    return {"run_id": run_id}


@app.get("/search")
def search(
    q: str,
    muni: str | None = None,
    source_type: str | None = None,
    year: int | None = None,
    topic: str | None = None,
    semantic_node_id: int | None = None,
    semantic_label: str | None = None,
    semantic_mode: str = "boost",
    include_semantic_debug: bool = False,
    limit: int = 20,
    db=Depends(get_db),
) -> dict:
    service = SearchService(db)
    hits = service.search(
        query=q,
        municipality_slug=muni,
        source_type=source_type,
        year=year,
        topic=topic,
        semantic_node_id=semantic_node_id,
        semantic_label=semantic_label,
        semantic_mode=semantic_mode,
        limit=max(1, min(limit, 50)),
    )
    results = [
        {
            "chunk_id": hit.chunk_id,
            "score": hit.score,
            "snippet": hit.snippet,
            "citation": hit.citation,
            "source_type": hit.source_type,
            "document": {
                "id": hit.document_id,
                "title": hit.document_title,
                "url": hit.document_url,
            },
            "municipality": hit.municipality_slug,
            "meeting_external_id": hit.meeting_external_id,
            "start_page": hit.start_page,
            "end_page": hit.end_page,
            "semantic_match_count": hit.semantic_match_count,
            "semantic_boost": hit.semantic_boost,
        }
        for hit in hits
    ]

    if include_semantic_debug:
        for idx, hit in enumerate(hits):
            results[idx]["semantic_nodes"] = [
                {
                    "id": node.id,
                    "label": node.label,
                    "kind": node.kind,
                    "type": node.semantic_type,
                    "confidence": node.confidence,
                }
                for node in hit.semantic_nodes
            ]
            results[idx]["semantic_node_ids"] = list(hit.semantic_node_ids)

    return {
        "query": q,
        "count": len(hits),
        "results": results,
    }


@app.post("/ask")
def ask(request: AskRequest, db=Depends(get_db)) -> dict:
    return _run_ask(request=request, db=db)


@app.post("/ask/debug/retrieval")
def ask_debug_retrieval(request: AskRequest, db=Depends(get_db)) -> dict:
    started = time.perf_counter()
    retrieval_service = RagRetrievalService(
        search_service=SearchService(db),
        reranker=EmbeddingReranker(ChunkEmbeddingService(db)),
    )
    retrieval_result = retrieval_service.retrieve(
        query=request.question,
        top_k=request.top_k,
        municipality_slug=request.muni,
        source_kinds=request.source_types,
        year=request.year,
        topic=request.topic,
        semantic_node_id=request.semantic_node_id,
        semantic_label=request.semantic_label,
        semantic_mode=request.semantic_mode,
    )
    retrieval_ms = round((time.perf_counter() - started) * 1000.0, 3)
    rerank_summary = _rerank_debug_summary(retrieval_result=retrieval_result)
    return {
        "query": request.question,
        "retrieval": {
            "retrieval_set_id": retrieval_result.retrieval_set_id,
            "count": len(retrieval_result.contexts),
            "source_types": sorted(retrieval_result.source_kinds),
            "requested_source_types": retrieval_result.requested_source_kinds,
            "top_k": retrieval_result.top_k,
            "semantic_mode": request.semantic_mode,
            "semantic_node_id": request.semantic_node_id,
            "semantic_label": request.semantic_label,
        },
        "embedding_cache": _active_embedding_cache_summary(db=db),
        "rerank": rerank_summary,
        "timing_ms": {
            "retrieval": retrieval_ms,
        },
        "results": [
            {
                "chunk_id": context.chunk_id,
                "score": context.score,
                "citation": context.citation,
                "source_type": context.source_kind,
                "document": {
                    "id": context.document_id,
                    "title": context.document_title,
                    "url": context.document_url,
                },
                "semantic_topic_labels": list(context.semantic_topic_labels),
            }
            for context in retrieval_result.contexts
        ],
    }


@app.get("/ui/ask", response_class=HTMLResponse)
def ask_playground_page() -> HTMLResponse:
    html_page = """
<!doctype html>
<html lang="en" dir="ltr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Ask Playground</title>
  <style>
    :root {
      --bg-a: #f3f7fb;
      --bg-b: #fcfaf4;
      --ink: #15313c;
      --muted: #536b75;
      --panel: #ffffff;
      --line: #d6e3ec;
      --accent: #0b7380;
      --warn: #a4511a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Inter", "Segoe UI", "Helvetica Neue", sans-serif;
      color: var(--ink);
      background: radial-gradient(1000px 420px at 100% -10%, #dceff7 0%, transparent 70%),
                  radial-gradient(820px 340px at -8% 110%, #f9ecd3 0%, transparent 70%),
                  linear-gradient(135deg, var(--bg-a), var(--bg-b));
      min-height: 100vh;
      padding: 22px;
    }
    .card {
      max-width: 980px;
      margin: 0 auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 24px;
      box-shadow: 0 16px 34px rgba(21, 49, 60, 0.11);
    }
    h1 { margin: 0 0 8px; font-size: 1.62rem; }
    p { margin: 0; line-height: 1.7; }
    .muted { color: var(--muted); font-size: 0.92rem; }
    .ask-form { margin-top: 14px; display: grid; gap: 12px; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 10px;
    }
    .field { display: grid; gap: 6px; }
    .field label, .group legend { font-weight: 600; font-size: 0.92rem; }
    textarea,
    input,
    select {
      width: 100%;
      border: 1px solid #bfd3de;
      border-radius: 10px;
      padding: 8px 10px;
      font-family: inherit;
      color: inherit;
      background: #fff;
    }
    textarea {
      min-height: 108px;
      line-height: 1.65;
      resize: vertical;
      direction: rtl;
      text-align: right;
    }
    .he-input { direction: rtl; text-align: right; }
    .group {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px;
      margin: 0;
    }
    .checks { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 8px; }
    .checks label { display: flex; align-items: center; gap: 6px; font-size: 0.92rem; }
    .checks input { width: auto; }
    .actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    button {
      border: 1px solid #0e6a76;
      background: var(--accent);
      color: #fff;
      border-radius: 999px;
      padding: 8px 18px;
      font-family: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    button:hover { background: #095f6a; }
    .output {
      margin-top: 14px;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: #fff;
    }
    .output.refusal { border-color: #ebd6c5; background: #fff8f2; }
    .answer-text, .refusal-text { direction: rtl; text-align: right; white-space: pre-line; }
    .answer-sections { margin-top: 10px; display: grid; gap: 10px; }
    .topic-tree-body { display: grid; gap: 10px; }
    .protocol-group { border: 1px solid #d7e5ee; border-radius: 11px; padding: 10px; background: #fdfefe; }
    .protocol-group > h4 { margin: 0 0 8px; font-size: 0.96rem; color: #23424f; }
    .topic-root, .topic-child { border: 1px solid #e0eaf0; border-radius: 9px; background: #fff; }
    .topic-root + .topic-root { margin-top: 8px; }
    .topic-root > summary, .topic-child > summary {
      cursor: pointer;
      list-style: none;
      padding: 8px 10px;
      font-weight: 600;
      color: #2f4d58;
      border-bottom: 1px solid #ecf2f7;
    }
    .topic-root > summary::-webkit-details-marker, .topic-child > summary::-webkit-details-marker { display: none; }
    .topic-root > summary::before, .topic-child > summary::before { content: "▾ "; color: #668390; }
    .topic-root:not([open]) > summary::before, .topic-child:not([open]) > summary::before { content: "▸ "; }
    .topic-root-content { padding: 8px; display: grid; gap: 8px; }
    .topic-child-content { padding: 8px 10px; display: grid; gap: 8px; }
    .answer-section { border: 1px solid #dde7ee; border-radius: 10px; padding: 10px; background: #fbfdff; }
    .answer-section h4 { margin: 0 0 4px; font-size: 0.98rem; }
    .answer-section .topic { margin: 0 0 6px; color: #4c5963; font-size: 0.88rem; }
    .answer-section .text { margin: 0; direction: rtl; text-align: right; }
    .answer-section .extended { margin: 8px 0 0; direction: rtl; text-align: right; color: #30404a; border-top: 1px dashed #d6e4ec; padding-top: 8px; }
    .decision-sources { margin: 0; padding-inline-start: 18px; }
    .decision-sources li { margin: 4px 0; }
    ul { margin: 8px 0 0; padding-inline-start: 20px; }
    li { margin: 6px 0; line-height: 1.5; }
    a { color: #00696f; text-decoration: none; border-bottom: 1px dotted #8fbac2; }
    a:hover { border-bottom-style: solid; }
    pre {
      margin: 8px 0 0;
      border-radius: 10px;
      border: 1px solid #d9e6ee;
      background: #f9fbfd;
      padding: 10px;
      overflow-x: auto;
      white-space: pre-wrap;
      font-size: 0.84rem;
      line-height: 1.45;
      direction: ltr;
      text-align: left;
      max-height: 260px;
    }
    .hidden { display: none; }
  </style>
</head>
<body>
  <article class="card">
    <h1>Ask Playground</h1>
    <p class="muted">General UI for <code>POST /ask</code>. Fill optional filters and inspect full debug thresholds for retrieval, answering, and LLM stages.</p>
    <form id="ask-playground-form" class="ask-form">
      <div class="field">
        <label for="ask-question">Question (Hebrew recommended)</label>
        <textarea id="ask-question" name="question" required placeholder="מה הוחלט בעיר?">מה הוחלט בעיר?</textarea>
      </div>
      <section class="grid">
        <div class="field">
          <label for="ask-muni">muni</label>
          <input id="ask-muni" name="muni" type="text" value="ashdod" />
        </div>
        <div class="field">
          <label for="ask-topic">topic</label>
          <input id="ask-topic" class="he-input" name="topic" type="text" placeholder="optional" />
        </div>
        <div class="field">
          <label for="ask-year">year</label>
          <input id="ask-year" name="year" type="number" min="2000" max="2100" placeholder="optional" />
        </div>
        <div class="field">
          <label for="ask-top-k">top_k</label>
          <input id="ask-top-k" name="top_k" type="number" min="1" max="50" value="8" />
          <span class="muted">How many chunks retrieval returns before answering (higher can be slower and costlier).</span>
        </div>
      </section>
      <fieldset class="group">
        <legend>source_types (retrieval filter)</legend>
        <div class="checks">
          <label><input type="checkbox" name="source_types" value="protocol" checked />protocol</label>
          <label><input type="checkbox" name="source_types" value="attachment" />attachment</label>
        </div>
      </fieldset>
      <fieldset class="group">
        <legend>required_source_types (coverage gate)</legend>
        <div class="checks">
          <label><input type="checkbox" name="required_source_types" value="protocol" checked />protocol</label>
          <label><input type="checkbox" name="required_source_types" value="attachment" />attachment</label>
        </div>
      </fieldset>
      <section class="grid">
        <div class="field">
          <label for="ask-semantic-mode">semantic_mode</label>
          <select id="ask-semantic-mode" name="semantic_mode">
            <option value="off" selected>off</option>
            <option value="boost">boost</option>
            <option value="filter">filter</option>
          </select>
        </div>
        <div class="field">
          <label for="ask-semantic-node-id">semantic_node_id</label>
          <input id="ask-semantic-node-id" name="semantic_node_id" type="number" min="1" placeholder="optional" />
        </div>
        <div class="field">
          <label for="ask-semantic-label">semantic_label</label>
          <input id="ask-semantic-label" class="he-input" name="semantic_label" type="text" placeholder="optional" />
        </div>
      </section>
      <div class="actions">
        <label class="muted"><input id="ask-debug-mode" type="checkbox" checked /> debug mode (show thresholds)</label>
        <label class="muted"><input id="ask-show-extended" type="checkbox" /> show extended answer</label>
        <button type="submit">Send Ask Request</button>
        <span id="ask-playground-status" class="muted" aria-live="polite"></span>
      </div>
    </form>

    <section id="ask-playground-answer" class="output hidden">
      <h3>Answer</h3>
      <p id="ask-playground-answer-text" class="answer-text"></p>
      <div id="ask-playground-answer-sections" class="answer-sections"></div>
      <details id="ask-playground-extended-panel" class="hidden">
        <summary>Extended answer (aggregate fallback)</summary>
        <p id="ask-playground-extended-text" class="answer-text"></p>
      </details>
      <ul id="ask-playground-limitations"></ul>
    </section>

    <section id="ask-playground-refusal" class="output refusal hidden">
      <h3>Refusal</h3>
      <p id="ask-playground-refusal-text" class="refusal-text"></p>
    </section>

    <section id="ask-playground-citations" class="output hidden">
      <h3>Citations</h3>
      <ul id="ask-playground-citations-list"></ul>
    </section>

    <section id="ask-playground-thresholds" class="output hidden">
      <h3>Thresholds and calculation rules</h3>
      <pre id="ask-playground-thresholds-json"></pre>
    </section>

    <section id="ask-playground-trace" class="output hidden">
      <h3>Similarity / Fallback Trace</h3>
      <ul id="ask-playground-trace-list"></ul>
    </section>

    <section class="output">
      <h3>Debug</h3>
      <ul id="ask-playground-meta"></ul>
      <details>
        <summary>Raw JSON</summary>
        <pre id="ask-playground-json"></pre>
      </details>
    </section>

    <section id="ask-playground-topic-tree-panel" class="output hidden">
      <h3>Full Topic Tree (DB cache)</h3>
      <div id="ask-playground-topic-tree-body" class="topic-tree-body"></div>
    </section>
  </article>
  <script>
    (() => {
      const form = document.getElementById("ask-playground-form");
      if (!form) {
        return;
      }

      const questionInput = document.getElementById("ask-question");
      const muniInput = document.getElementById("ask-muni");
      const topicInput = document.getElementById("ask-topic");
      const yearInput = document.getElementById("ask-year");
      const topKInput = document.getElementById("ask-top-k");
      const semanticModeInput = document.getElementById("ask-semantic-mode");
      const semanticNodeIdInput = document.getElementById("ask-semantic-node-id");
      const semanticLabelInput = document.getElementById("ask-semantic-label");
      const debugModeInput = document.getElementById("ask-debug-mode");
      const showExtendedInput = document.getElementById("ask-show-extended");

      const statusNode = document.getElementById("ask-playground-status");
      const answerPanel = document.getElementById("ask-playground-answer");
      const answerText = document.getElementById("ask-playground-answer-text");
      const answerSectionsNode = document.getElementById("ask-playground-answer-sections");
      const extendedPanel = document.getElementById("ask-playground-extended-panel");
      const extendedText = document.getElementById("ask-playground-extended-text");
      const limitationsList = document.getElementById("ask-playground-limitations");
      const refusalPanel = document.getElementById("ask-playground-refusal");
      const refusalText = document.getElementById("ask-playground-refusal-text");
      const citationsPanel = document.getElementById("ask-playground-citations");
      const citationsList = document.getElementById("ask-playground-citations-list");
      const thresholdsPanel = document.getElementById("ask-playground-thresholds");
      const thresholdsJson = document.getElementById("ask-playground-thresholds-json");
      const tracePanel = document.getElementById("ask-playground-trace");
      const traceList = document.getElementById("ask-playground-trace-list");
      const metaList = document.getElementById("ask-playground-meta");
      const rawJson = document.getElementById("ask-playground-json");
      const topicTreePanel = document.getElementById("ask-playground-topic-tree-panel");
      const topicTreeBody = document.getElementById("ask-playground-topic-tree-body");
      let lastExtendedAnswer = null;
      let lastAnswerSections = [];
      let lastExtendedSections = [];
      let lastAnswerText = "";
      let lastCitations = [];

      const hide = (node) => {
        if (node) {
          node.classList.add("hidden");
        }
      };
      const show = (node) => {
        if (node) {
          node.classList.remove("hidden");
        }
      };
      const resetList = (node) => {
        if (node) {
          node.innerHTML = "";
        }
      };
      const appendItem = (node, text) => {
        if (!node || !text) {
          return;
        }
        const item = document.createElement("li");
        item.textContent = text;
        node.appendChild(item);
      };

      const RELATIONAL_TOKENS = new Set([
        "לגבי", "בנוגע", "באשר", "בנושא", "עבור", "בעקבות", "בתחום", "בתחומי", "לצורך", "עם", "מול", "בין",
        "ללא", "תוך", "כדי", "לשם", "של", "על", "אל", "את", "מן", "מ", "בהמשך"
      ]);
      const NON_INFORMATIONAL_TOKENS = new Set([
        "אחראי", "אחראית", "אחריות", "מזכירת", "הועדה", "ועדה", "יו", "ר", "מנכ", "ל", "מח", "מחלקת",
        "תנועה", "תחבורה", "גב", "ד", "ר", "עו", "ד", "שוטף", "בהתאם", "יוכנו", "להלן"
      ]);

      const sanitizeInformationalText = (value) => {
        let cleaned = normalizeText(value);
        if (!cleaned) {
          return "";
        }

        cleaned = cleaned.replace(/_{3,}.*/g, "").trim();
        cleaned = cleaned.replace(/\\s[-–]\\s(?:מח|מטה|משרד|ועדה|יו|מנכ|מנהל|מנהלת).*/g, "").trim();
        cleaned = cleaned.replace(/\\b(?:מזכירת|יו"ר|מנכ"ל|מנהלת|מח'|מחלקת)\\b.*$/g, "").trim();
        return cleaned;
      };

      const normalizeText = (value) => String(value || "").replace(/\\s+/g, " ").trim().toLowerCase();
      const tokenizeText = (value) => {
        const normalized = normalizeText(value);
        if (!normalized) {
          return [];
        }
        return normalized.match(/[\u0590-\u05FF]{2,}|[a-z0-9]{2,}/g) || [];
      };
      const tokenizeMeaningfulText = (value) => {
        return tokenizeText(value).filter(
          (token) => token.length >= 3 && !RELATIONAL_TOKENS.has(token) && !NON_INFORMATIONAL_TOKENS.has(token)
        );
      };
      const isAlmostEqualText = (left, right) => {
        const leftNorm = normalizeText(left);
        const rightNorm = normalizeText(right);
        if (!leftNorm || !rightNorm) {
          return false;
        }
        if (leftNorm === rightNorm) {
          return true;
        }

        const leftTokens = new Set(tokenizeText(leftNorm));
        const rightTokens = new Set(tokenizeText(rightNorm));
        if (leftTokens.size === 0 || rightTokens.size === 0) {
          return false;
        }

        let overlapCount = 0;
        for (const token of leftTokens) {
          if (rightTokens.has(token)) {
            overlapCount += 1;
          }
        }

        const overlap = overlapCount / Math.max(leftTokens.size, rightTokens.size, 1);
        const lenRatio = Math.min(leftNorm.length, rightNorm.length) / Math.max(leftNorm.length, rightNorm.length, 1);
        return (overlap >= 0.92 && lenRatio >= 0.82) || overlap >= 0.97;
      };
      const hasMeaningfulExtraInfo = (baseText, extendedText) => {
        if (!extendedText || !String(extendedText).trim()) {
          return false;
        }
        const baseSanitized = sanitizeInformationalText(baseText);
        const extendedSanitized = sanitizeInformationalText(extendedText);
        if (!extendedSanitized) {
          return false;
        }
        if (isAlmostEqualText(baseSanitized, extendedSanitized)) {
          return false;
        }

        const baseMeaningful = new Set(tokenizeMeaningfulText(baseSanitized));
        const extMeaningful = new Set(tokenizeMeaningfulText(extendedSanitized));
        const novelTokens = [];
        for (const token of extMeaningful) {
          if (!baseMeaningful.has(token)) {
            novelTokens.push(token);
          }
        }

        if (novelTokens.length < 4) {
          return false;
        }

        const baseNorm = normalizeText(baseSanitized);
        const extNorm = normalizeText(extendedSanitized);
        if ((extNorm.length - baseNorm.length) < 40) {
          return false;
        }
        const growthRatio = extNorm.length / Math.max(baseNorm.length, 1);
        const noveltyRatio = novelTokens.length / Math.max(extMeaningful.size, 1);
        if (growthRatio < 1.3 && noveltyRatio < 0.35) {
          return false;
        }
        if (growthRatio < 1.35 && !(noveltyRatio >= 0.45 && novelTokens.length >= 6)) {
          return false;
        }
        return true;
      };
      const splitTopicPath = (topicValue, fallbackText) => {
        const rawTopic = String(topicValue || "").trim();
        const parts = rawTopic.split(">").map((part) => part.trim()).filter(Boolean);
        if (parts.length >= 2) {
          return {
            root: parts[0],
            child: parts.slice(1).join(" > "),
          };
        }

        const fallbackWords = String(fallbackText || "").trim().split(/\\s+/).filter(Boolean);
        return {
          root: parts[0] || "נושא כללי",
          child: fallbackWords.slice(0, 4).join(" ") || "החלטה",
        };
      };
      const citationByChunkId = () => {
        const byChunk = new Map();
        const rows = Array.isArray(lastCitations) ? lastCitations : [];
        for (const row of rows) {
          if (!row || typeof row !== "object") {
            continue;
          }
          const chunkId = typeof row.chunk_id === "string" ? row.chunk_id : "";
          if (!chunkId || byChunk.has(chunkId)) {
            continue;
          }
          byChunk.set(chunkId, row);
        }
        return byChunk;
      };

      const renderAnswerSections = () => {
        if (!answerSectionsNode) {
          return;
        }
        answerSectionsNode.innerHTML = "";

        const sections = Array.isArray(lastAnswerSections) ? lastAnswerSections : [];
        const extendedSections = Array.isArray(lastExtendedSections) ? lastExtendedSections : [];
        const shouldShowExtended = Boolean(showExtendedInput && showExtendedInput.checked);
        const citationMap = citationByChunkId();

        const protocolOrder = [];
        const protocolBuckets = new Map();

        for (let idx = 0; idx < sections.length; idx += 1) {
          const section = sections[idx];
          if (!section || typeof section !== "object") {
            continue;
          }

          const protocolTitle = typeof section.protocol_title === "string" ? section.protocol_title : "פרוטוקול";
          const conciseText = typeof section.text === "string" ? section.text : "";
          if (!conciseText.trim()) {
            continue;
          }

          const topic = typeof section.topic_name === "string" ? section.topic_name : "נושא כללי";
          const topicPath = splitTopicPath(topic, conciseText);
          const extSection = extendedSections[idx];
          const extText = extSection && typeof extSection === "object" && typeof extSection.text === "string"
            ? extSection.text
            : "";
          const chunkIds = Array.isArray(section.chunk_ids) ? section.chunk_ids : [];

          if (!protocolBuckets.has(protocolTitle)) {
            protocolBuckets.set(protocolTitle, []);
            protocolOrder.push(protocolTitle);
          }
          protocolBuckets.get(protocolTitle).push({
            root: topicPath.root,
            child: topicPath.child,
            text: conciseText,
            extText,
            chunkIds,
          });
        }

        for (const protocolTitle of protocolOrder) {
          const protocolNode = document.createElement("section");
          protocolNode.className = "protocol-group";

          const protocolHeader = document.createElement("h4");
          protocolHeader.textContent = protocolTitle;
          protocolNode.appendChild(protocolHeader);

          const rows = protocolBuckets.get(protocolTitle) || [];
          const rootOrder = [];
          const rootBuckets = new Map();
          for (const row of rows) {
            if (!rootBuckets.has(row.root)) {
              rootBuckets.set(row.root, []);
              rootOrder.push(row.root);
            }
            rootBuckets.get(row.root).push(row);
          }

          for (const rootLabel of rootOrder) {
            const rootDetails = document.createElement("details");
            rootDetails.className = "topic-root";
            rootDetails.open = true;

            const rootSummary = document.createElement("summary");
            rootSummary.textContent = rootLabel;
            rootDetails.appendChild(rootSummary);

            const rootContent = document.createElement("div");
            rootContent.className = "topic-root-content";

            const rowsForRoot = rootBuckets.get(rootLabel) || [];
            for (const row of rowsForRoot) {
              const childDetails = document.createElement("details");
              childDetails.className = "topic-child";
              childDetails.open = true;

              const childSummary = document.createElement("summary");
              childSummary.textContent = row.child;
              childDetails.appendChild(childSummary);

              const childContent = document.createElement("div");
              childContent.className = "topic-child-content";

              const textNode = document.createElement("p");
              textNode.className = "text";
              textNode.textContent = row.text;
              childContent.appendChild(textNode);

              if (shouldShowExtended && hasMeaningfulExtraInfo(row.text, row.extText)) {
                const extNode = document.createElement("p");
                extNode.className = "extended";
                extNode.textContent = `הרחבה: ${row.extText}`;
                childContent.appendChild(extNode);
              }

              const sourceRows = [];
              for (const chunkId of row.chunkIds) {
                if (typeof chunkId !== "string") {
                  continue;
                }
                const citation = citationMap.get(chunkId);
                if (citation) {
                  sourceRows.push(citation);
                }
              }
              if (sourceRows.length > 0) {
                const sourceTitle = document.createElement("p");
                sourceTitle.className = "topic";
                sourceTitle.textContent = "מקורות:";
                childContent.appendChild(sourceTitle);

                const sourceList = document.createElement("ul");
                sourceList.className = "decision-sources";
                for (const citation of sourceRows) {
                  const doc = citation.document && typeof citation.document === "object" ? citation.document : {};
                  const page = Number.isFinite(Number(citation.start_page)) ? Number(citation.start_page) : null;
                  const hrefBase = doc.url || "#";
                  const sourceItem = document.createElement("li");
                  const sourceLink = document.createElement("a");
                  sourceLink.href = page ? `${hrefBase}#page=${page}` : hrefBase;
                  sourceLink.target = "_blank";
                  sourceLink.rel = "noopener";
                  sourceLink.textContent = citation.citation || citation.chunk_id || "source";
                  sourceItem.appendChild(sourceLink);

                  const sourceMeta = document.createElement("span");
                  sourceMeta.className = "muted";
                  sourceMeta.textContent = ` [${citation.source_type || "source"}] ${doc.title || "document"}`;
                  sourceItem.appendChild(sourceMeta);

                  sourceList.appendChild(sourceItem);
                }
                childContent.appendChild(sourceList);
              }

              childDetails.appendChild(childContent);
              rootContent.appendChild(childDetails);
            }

            rootDetails.appendChild(rootContent);
            protocolNode.appendChild(rootDetails);
          }

          answerSectionsNode.appendChild(protocolNode);
        }
      };

      const renderExtendedAnswer = () => {
        const shouldShow = Boolean(showExtendedInput && showExtendedInput.checked);
        const hasExtended = typeof lastExtendedAnswer === "string" && lastExtendedAnswer.trim().length > 0;
        const hasSections = Array.isArray(lastAnswerSections) && lastAnswerSections.length > 0;
        if (!extendedPanel || !extendedText) {
          return;
        }

        if (hasSections) {
          extendedText.textContent = "";
          hide(extendedPanel);
          return;
        }

        if (shouldShow && hasExtended && hasMeaningfulExtraInfo(lastAnswerText, lastExtendedAnswer)) {
          extendedText.textContent = lastExtendedAnswer;
          show(extendedPanel);
          return;
        }
        extendedText.textContent = "";
        hide(extendedPanel);
      };

      const renderTopicTree = (payload) => {
        if (!topicTreePanel || !topicTreeBody) {
          return;
        }
        topicTreeBody.innerHTML = "";

        const roots = Array.isArray(payload && payload.roots) ? payload.roots : [];
        if (roots.length === 0) {
          hide(topicTreePanel);
          return;
        }

        for (const root of roots) {
          if (!root || typeof root !== "object") {
            continue;
          }
          const rootTopic = typeof root.topic === "string" ? root.topic : "נושא כללי";
          const rootCount = Number.isFinite(Number(root.count)) ? Number(root.count) : 0;
          const protocolCount = Number.isFinite(Number(root.protocol_count)) ? Number(root.protocol_count) : 0;
          const children = Array.isArray(root.children) ? root.children : [];

          const rootBlock = document.createElement("section");
          rootBlock.className = "protocol-group";

          const rootHeader = document.createElement("h4");
          rootHeader.textContent = `${rootTopic} (${rootCount})`;
          rootBlock.appendChild(rootHeader);

          const rootMeta = document.createElement("p");
          rootMeta.className = "muted";
          rootMeta.textContent = `protocol coverage: ${protocolCount}`;
          rootBlock.appendChild(rootMeta);

          const rootContent = document.createElement("div");
          rootContent.className = "topic-root-content";

          for (const child of children) {
            if (!child || typeof child !== "object") {
              continue;
            }
            const childTopic = typeof child.topic === "string" ? child.topic : "החלטה כללית";
            const childCount = Number.isFinite(Number(child.count)) ? Number(child.count) : 0;
            const childProtocolCount = Number.isFinite(Number(child.protocol_count)) ? Number(child.protocol_count) : 0;
            const childGranularity = Number.isFinite(Number(child.avg_granularity))
              ? Number(child.avg_granularity)
              : null;

            const childDetails = document.createElement("details");
            childDetails.className = "topic-child";
            childDetails.open = false;

            const childSummary = document.createElement("summary");
            childSummary.textContent = `${childTopic} (${childCount})`;
            childDetails.appendChild(childSummary);

            const childContent = document.createElement("div");
            childContent.className = "topic-child-content";
            const note = document.createElement("p");
            note.className = "muted";
            const granularityText = childGranularity === null ? "-" : childGranularity.toFixed(2);
            note.textContent = `support: ${childCount}; protocols: ${childProtocolCount}; granularity: ${granularityText}`;
            childContent.appendChild(note);
            childDetails.appendChild(childContent);

            rootContent.appendChild(childDetails);
          }

          rootBlock.appendChild(rootContent);

          topicTreeBody.appendChild(rootBlock);
        }

        show(topicTreePanel);
      };

      const refreshTopicTree = async () => {
        if (!topicTreePanel || !topicTreeBody) {
          return;
        }
        try {
          const response = await fetch("/topic/tree/cache", {
            method: "GET",
            headers: { "Accept": "application/json" },
          });
          if (!response.ok) {
            throw new Error("topic_tree_request_failed");
          }
          const payload = await response.json();
          renderTopicTree(payload);
        } catch (_err) {
          hide(topicTreePanel);
        }
      };

      if (showExtendedInput) {
        showExtendedInput.addEventListener("change", () => {
          renderAnswerSections();
          renderExtendedAnswer();
        });
      }
      const getCheckedValues = (name) => {
        return Array.from(document.querySelectorAll(`input[name=\"${name}\"]:checked`)).map((box) => box.value);
      };
      const cleanText = (value) => {
        const compact = String(value || "").trim();
        return compact || null;
      };

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const question = cleanText(questionInput.value);
        if (!question) {
          statusNode.textContent = "Please enter a question before submitting.";
          return;
        }

        const topKRaw = parseInt(topKInput.value || "8", 10);
        const topK = Number.isFinite(topKRaw) ? Math.max(1, Math.min(50, topKRaw)) : 8;
        const payload = {
          question: question,
          top_k: topK,
          semantic_mode: semanticModeInput.value || "off",
          debug_mode: Boolean(debugModeInput && debugModeInput.checked),
        };

        const sourceTypes = getCheckedValues("source_types");
        const requiredSourceTypes = getCheckedValues("required_source_types");
        const muni = cleanText(muniInput.value);
        const topic = cleanText(topicInput.value);
        const yearRaw = parseInt(yearInput.value || "", 10);
        const semanticNodeIdRaw = parseInt(semanticNodeIdInput.value || "", 10);
        const semanticLabel = cleanText(semanticLabelInput.value);

        if (sourceTypes.length > 0) {
          payload.source_types = sourceTypes;
        }
        if (requiredSourceTypes.length > 0) {
          payload.required_source_types = requiredSourceTypes;
        }
        if (muni) {
          payload.muni = muni;
        }
        if (topic) {
          payload.topic = topic;
        }
        if (Number.isFinite(yearRaw)) {
          payload.year = Math.max(2000, Math.min(2100, yearRaw));
        }
        if (Number.isFinite(semanticNodeIdRaw) && semanticNodeIdRaw > 0) {
          payload.semantic_node_id = semanticNodeIdRaw;
        }
        if (semanticLabel) {
          payload.semantic_label = semanticLabel;
        }

        statusNode.textContent = "Submitting ask request...";
        hide(answerPanel);
        hide(refusalPanel);
        hide(citationsPanel);
        hide(thresholdsPanel);
        hide(tracePanel);
        hide(extendedPanel);
        resetList(limitationsList);
        resetList(citationsList);
        resetList(traceList);
        resetList(metaList);
        if (answerSectionsNode) {
          answerSectionsNode.innerHTML = "";
        }
        rawJson.textContent = "";
        thresholdsJson.textContent = "";
        if (extendedText) {
          extendedText.textContent = "";
        }
        lastExtendedAnswer = null;
        lastAnswerSections = [];
        lastExtendedSections = [];
        lastAnswerText = "";
        lastCitations = [];

        try {
          const response = await fetch("/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          if (!response.ok) {
            const errorBody = await response.text().catch(() => "");
            const errorSuffix = errorBody ? `: ${errorBody.slice(0, 220)}` : "";
            throw new Error(`HTTP ${response.status}${errorSuffix}`);
          }

          const data = await response.json();
          rawJson.textContent = JSON.stringify(data, null, 2);

          const retrieval = data.retrieval && typeof data.retrieval === "object" ? data.retrieval : {};
          const model = data.model && typeof data.model === "object" ? data.model : {};
          appendItem(metaList, `ask_request_id: ${data.ask_request_id || "-"}`);
          appendItem(metaList, `status: ${data.status || "-"}`);
          appendItem(metaList, `retrieval_set_id: ${retrieval.retrieval_set_id || "-"}`);
          appendItem(metaList, `retrieval_count: ${retrieval.count || 0}`);
          appendItem(metaList, `retrieved_source_types: ${(retrieval.source_types || []).join(", ") || "-"}`);
          appendItem(metaList, `model: ${model.provider || "-"} / ${model.name || "-"}`);

          const debugPayload = data.debug && typeof data.debug === "object" ? data.debug : null;
          const debugTiming = debugPayload && debugPayload.timing_ms && typeof debugPayload.timing_ms === "object"
            ? debugPayload.timing_ms
            : null;
          if (debugTiming) {
            appendItem(metaList, `timing_ms.request_total: ${debugTiming.request_total ?? "-"}`);
            appendItem(metaList, `timing_ms.retrieval: ${debugTiming.retrieval ?? "-"}`);
            appendItem(metaList, `timing_ms.answering: ${debugTiming.answering ?? "-"}`);
          }

          const retrievalTrace = debugPayload && debugPayload.retrieval_trace && typeof debugPayload.retrieval_trace === "object"
            ? debugPayload.retrieval_trace
            : null;
          const embeddingRerank = retrievalTrace && retrievalTrace.embedding_rerank && typeof retrievalTrace.embedding_rerank === "object"
            ? retrievalTrace.embedding_rerank
            : null;
          if (embeddingRerank) {
            appendItem(metaList, `embedding_rerank.enabled: ${embeddingRerank.enabled === true ? "yes" : "no"}`);
            appendItem(metaList, `embedding_rerank.query_embedding_used: ${embeddingRerank.query_embedding_used === true ? "yes" : "no"}`);
            appendItem(metaList, `embedding_rerank.candidate_count: ${embeddingRerank.candidate_count ?? "-"}`);
            appendItem(metaList, `embedding_rerank.available_chunk_embeddings: ${embeddingRerank.available_chunk_embeddings ?? "-"}`);
            appendItem(metaList, `embedding_rerank.created_chunk_embeddings: ${embeddingRerank.created_chunk_embeddings ?? "-"}`);
          }

          const thresholds = data.debug && data.debug.thresholds ? data.debug.thresholds : null;
          if (thresholds) {
            thresholdsJson.textContent = JSON.stringify(thresholds, null, 2);
            show(thresholdsPanel);
          }

          const trace = debugPayload && debugPayload.answering_trace && typeof debugPayload.answering_trace === "object"
            ? debugPayload.answering_trace
            : null;
          if (trace) {
            appendItem(traceList, `semantic_scoring_source: ${trace.semantic_scoring_source || "-"}`);
            appendItem(traceList, `verify_route: ${trace.verify_route || "-"}`);
            appendItem(traceList, `deterministic_would_refuse: ${trace.deterministic_would_refuse === true ? "yes" : "no"}`);
            appendItem(traceList, `fallback_verify_attempted: ${trace.fallback_verify_attempted === true ? "yes" : "no"}`);
            appendItem(traceList, `fallback_provider: ${trace.fallback_provider || "-"}`);
            appendItem(traceList, `similarity_external_api_called: ${trace.similarity_external_api_called === true ? "yes" : "no"}`);
            appendItem(traceList, `answer_external_api_called: ${trace.answer_external_api_called === true ? "yes" : "no"}`);
            appendItem(traceList, `external_call_count: ${trace.external_call_count ?? "-"}`);
            appendItem(traceList, `fallback_overrode_deterministic_low: ${trace.fallback_overrode_deterministic_low === true ? "yes" : "no"}`);
            const stageTiming = trace.timing_ms && typeof trace.timing_ms === "object" ? trace.timing_ms : null;
            if (stageTiming) {
              appendItem(
                traceList,
                `timing_ms: answer_call=${stageTiming.answer_call ?? "-"}, deterministic_similarity=${stageTiming.deterministic_similarity ?? "-"}, fallback_verify=${stageTiming.fallback_verify ?? "-"}, compose_total=${stageTiming.compose_total ?? "-"}`,
              );
            }
            show(tracePanel);
          } else {
            hide(tracePanel);
          }

          if (data.status === "answer") {
            const sections = Array.isArray(data.answer_sections) ? data.answer_sections : [];
            const extendedSections = Array.isArray(data.extended_answer_sections) ? data.extended_answer_sections : [];
            const citations = Array.isArray(data.citations) ? data.citations : [];
            lastAnswerSections = sections;
            lastExtendedSections = extendedSections;
            lastCitations = citations;

            if (sections.length > 0) {
              answerText.textContent = "";
              hide(answerText);
            } else {
              answerText.textContent = data.answer || "";
              show(answerText);
            }
            lastAnswerText = typeof data.answer === "string" ? data.answer : "";
            lastExtendedAnswer = typeof data.extended_answer === "string" ? data.extended_answer : null;
            renderAnswerSections();
            renderExtendedAnswer();
            const limitations = Array.isArray(data.limitations) ? data.limitations : [];
            if (limitations.length === 0) {
              appendItem(limitationsList, "No limitations were returned.");
            } else {
              for (const limitation of limitations) {
                appendItem(limitationsList, limitation);
              }
            }

            if (citations.length === 0) {
              appendItem(citationsList, "No citations were returned.");
            } else {
              for (const citation of citations) {
                const doc = citation.document && typeof citation.document === "object" ? citation.document : {};
                const page = Number.isFinite(Number(citation.start_page)) ? Number(citation.start_page) : null;
                const hrefBase = doc.url || "#";
                const anchor = document.createElement("a");
                anchor.href = page ? `${hrefBase}#page=${page}` : hrefBase;
                anchor.target = "_blank";
                anchor.rel = "noopener";
                anchor.textContent = citation.citation || citation.chunk_id || "source";

                const meta = document.createElement("span");
                meta.className = "muted";
                meta.textContent = ` [${citation.source_type || "source"}] ${doc.title || "document"}`;

                const item = document.createElement("li");
                item.appendChild(anchor);
                item.appendChild(meta);
                citationsList.appendChild(item);
              }
            }

            show(answerPanel);
            show(citationsPanel);
            hide(refusalPanel);
            statusNode.textContent = "Received grounded answer with citations.";
            await refreshTopicTree();
            return;
          }

          const refusalPayload = data.refusal && typeof data.refusal === "object" ? data.refusal : {};
          const missingTypes = Array.isArray(refusalPayload.missing_source_types)
            ? refusalPayload.missing_source_types.filter((value) => typeof value === "string" && value)
            : [];
          const refusalMessage = refusalPayload.message_he || "אין מספיק ראיות כדי להשיב.";
          const reasonCode = refusalPayload.reason_code || "-";
          const missingHint = missingTypes.length ? ` Missing sources: ${missingTypes.join(", ")}.` : "";
          refusalText.textContent = `${refusalMessage}${missingHint}`;
          appendItem(metaList, `reason_code: ${reasonCode}`);

          hide(answerPanel);
          hide(citationsPanel);
          show(refusalPanel);
          statusNode.textContent = "Model refused due to evidence policy.";
          await refreshTopicTree();
        } catch (_err) {
          console.error("ask_playground_request_failed", _err);
          const errorMessage = _err instanceof Error ? _err.message : String(_err || "unknown_error");
          hide(answerPanel);
          hide(citationsPanel);
          hide(refusalPanel);
          hide(thresholdsPanel);
          hide(tracePanel);
          hide(extendedPanel);
          statusNode.textContent = `Request failed: ${errorMessage}`;
        }
      });
      refreshTopicTree();
    })();
  </script>
</body>
</html>
"""
    return HTMLResponse(html_page)


@app.get("/semantic/tree")
def semantic_tree(
    muni: str | None = None,
    root_id: int | None = None,
    depth: int | None = None,
    kind: str | None = None,
    db=Depends(get_db),
) -> dict:
    stmt = select(SemanticNode)
    if muni:
        stmt = stmt.join(SourceSite, SourceSite.id == SemanticNode.source_site_id).where(
            SourceSite.municipality_slug == muni
        )
    if kind:
        stmt = stmt.where(SemanticNode.node_kind == kind)

    nodes = db.execute(
        stmt.order_by(SemanticNode.depth.asc(), SemanticNode.pref_label_norm.asc(), SemanticNode.id.asc())
    ).scalars().all()
    nodes_by_id = {node.id: node for node in nodes}
    if root_id is not None and root_id not in nodes_by_id:
        raise HTTPException(status_code=404, detail="semantic_root_not_found")

    children_by_parent: dict[int, list[int]] = {}
    for node in nodes:
        if node.parent_node_id is None:
            continue
        children_by_parent.setdefault(node.parent_node_id, []).append(node.id)

    included_ids: set[int]
    if root_id is not None:
        max_relative_depth = max(0, depth) if depth is not None else None
        included_ids = {root_id}
        frontier: list[tuple[int, int]] = [(root_id, 0)]
        while frontier:
            parent_id, relative_depth = frontier.pop(0)
            if max_relative_depth is not None and relative_depth >= max_relative_depth:
                continue
            for child_id in children_by_parent.get(parent_id, []):
                if child_id in included_ids:
                    continue
                included_ids.add(child_id)
                frontier.append((child_id, relative_depth + 1))
    else:
        included_ids = {node.id for node in nodes}
        if depth is not None:
            max_depth = max(0, depth)
            included_ids = {node.id for node in nodes if node.depth <= max_depth}

    items = []
    for node in nodes:
        if node.id not in included_ids:
            continue
        child_count = sum(1 for child_id in children_by_parent.get(node.id, []) if child_id in included_ids)
        items.append(_semantic_node_payload(node, child_count=child_count))

    return {
        "count": len(items),
        "items": items,
    }


@app.get("/semantic/node/{node_id}")
def semantic_node_detail(node_id: int, db=Depends(get_db)) -> dict:
    node = db.execute(select(SemanticNode).where(SemanticNode.id == node_id)).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="semantic_node_not_found")

    parent = None
    if node.parent_node_id is not None:
        parent = db.execute(select(SemanticNode).where(SemanticNode.id == node.parent_node_id)).scalar_one_or_none()

    aliases = db.execute(
        select(SemanticAlias)
        .where(SemanticAlias.semantic_node_id == node.id)
        .order_by(SemanticAlias.alias_kind.asc(), SemanticAlias.alias_label_norm.asc())
    ).scalars().all()
    children = db.execute(
        select(SemanticNode)
        .where(SemanticNode.parent_node_id == node.id)
        .order_by(SemanticNode.pref_label_norm.asc(), SemanticNode.id.asc())
    ).scalars().all()

    decision_rows = db.execute(
        select(DecisionSemanticLink, Decision, Document, Meeting)
        .join(Decision, Decision.id == DecisionSemanticLink.decision_id)
        .join(Document, Document.id == Decision.source_document_id)
        .join(Meeting, Meeting.id == Decision.meeting_id)
        .where(DecisionSemanticLink.semantic_node_id == node.id)
        .order_by(Decision.id.asc())
    ).all()

    chunk_rows = db.execute(
        select(ChunkSemanticLink, TextChunk, Document)
        .join(TextChunk, TextChunk.chunk_id == ChunkSemanticLink.chunk_id)
        .join(Document, Document.id == TextChunk.document_id)
        .where(ChunkSemanticLink.semantic_node_id == node.id)
        .order_by(TextChunk.document_id.asc(), TextChunk.chunk_index.asc())
    ).all()

    mention_rows = db.execute(
        select(SemanticMention, Document)
        .join(Document, Document.id == SemanticMention.document_id)
        .where(SemanticMention.semantic_node_id == node.id)
        .order_by(SemanticMention.id.desc())
        .limit(30)
    ).all()

    return {
        "node": _semantic_node_payload(node, child_count=len(children)),
        "parent": _semantic_node_payload(parent, child_count=0) if parent else None,
        "children": [_semantic_node_payload(child, child_count=0) for child in children],
        "aliases": [
            {
                "id": alias.id,
                "label": alias.alias_label_he,
                "label_norm": alias.alias_label_norm,
                "kind": alias.alias_kind,
                "confidence": alias.confidence,
            }
            for alias in aliases
        ],
        "linked_decisions": [
            {
                "decision_id": decision.id,
                "relation_role": link.relation_role,
                "confidence": link.confidence,
                "source_mention_id": link.source_mention_id,
                "decision_text": decision.decision_text,
                "is_public": bool(decision.is_public),
                "meeting_id": decision.meeting_id,
                "meeting_external_id": meeting.meeting_external_id,
                "document": {
                    "id": document.id,
                    "title": document.title_he,
                    "url": document.canonical_url,
                },
            }
            for link, decision, document, meeting in decision_rows
        ],
        "linked_chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "confidence": link.confidence,
                "source_mention_id": link.source_mention_id,
                "source_type": chunk.source_kind,
                "citation": chunk.citation_label,
                "start_page": chunk.start_page,
                "end_page": chunk.end_page,
                "document": {
                    "id": document.id,
                    "title": document.title_he,
                    "url": document.canonical_url,
                },
                "snippet": chunk.chunk_text[:240],
            }
            for link, chunk, document in chunk_rows
        ],
        "mentions": [
            {
                "id": mention.id,
                "document_id": mention.document_id,
                "document_version_id": mention.document_version_id,
                "start_offset": mention.start_offset,
                "end_offset": mention.end_offset,
                "start_page": mention.start_page,
                "end_page": mention.end_page,
                "mention_text": mention.mention_text,
                "mention_confidence": mention.mention_confidence,
                "evidence_hash": mention.evidence_hash,
                "citation": (
                    f"p.{mention.start_page}"
                    if mention.start_page is not None and mention.start_page == mention.end_page
                    else (
                        f"pp.{mention.start_page}-{mention.end_page}"
                        if mention.start_page is not None and mention.end_page is not None
                        else None
                    )
                ),
                "document": {
                    "id": document.id,
                    "title": document.title_he,
                    "url": document.canonical_url,
                },
            }
            for mention, document in mention_rows
        ],
    }


@app.get("/topic/tree/cache")
def topic_tree_cache(*, limit: int = 5000, db=Depends(get_db)) -> dict:
    if not inspect(db.get_bind()).has_table("rag_decision_summary_cache"):
        return {"count": 0, "roots": []}

    effective_limit = max(100, min(10000, int(limit)))
    rows = db.execute(
        select(
            RagDecisionSummaryCache.question_hash,
            RagDecisionSummaryCache.chunk_id,
            RagDecisionSummaryCache.summary_he,
            RagDecisionSummaryCache.protocol_title,
            RagDecisionSummaryCache.topic_name,
            RagDecisionSummaryCache.updated_at,
        )
        .order_by(RagDecisionSummaryCache.updated_at.desc(), RagDecisionSummaryCache.id.desc())
        .limit(effective_limit)
    ).all()

    granularity_by_key: dict[tuple[str, str, str], int] = {}
    if rows and inspect(db.get_bind()).has_table("rag_decision_summary_topic_meta"):
        question_hashes = sorted({str(question_hash) for question_hash, *_ in rows if str(question_hash)})
        if question_hashes:
            meta_rows = db.execute(
                select(
                    RagDecisionSummaryTopicMeta.question_hash,
                    RagDecisionSummaryTopicMeta.chunk_id,
                    RagDecisionSummaryTopicMeta.summary_he,
                    RagDecisionSummaryTopicMeta.topic_granularity_level,
                ).where(RagDecisionSummaryTopicMeta.question_hash.in_(question_hashes))
            ).all()
            for question_hash, chunk_id, summary_he, granularity in meta_rows:
                level = _as_int_in_range(granularity, min_value=1, max_value=10)
                if level is None:
                    continue
                granularity_by_key[(str(question_hash), str(chunk_id), str(summary_he))] = level

    global_roots: dict[str, dict[str, Any]] = {}
    seen_cache_rows: set[tuple[str, str]] = set()
    for question_hash, chunk_id, summary_he, protocol_title, topic_name, updated_at in rows:
        topic_name_value = str(topic_name or "").strip()
        if normalize_for_search(topic_name_value) in {
            "",
            normalize_for_search(PLACEHOLDER_TOPIC_LABEL),
            normalize_for_search("נושא כללי"),
        }:
            inferred = _infer_topic_path_from_summary(
                summary_he=str(summary_he or ""),
                protocol_title=str(protocol_title or ""),
            )
            if inferred:
                topic_name_value = inferred

        topic_key = normalize_for_search(topic_name_value)
        dedupe_key = (str(chunk_id or ""), topic_key)
        if not dedupe_key[0] or not dedupe_key[1] or dedupe_key in seen_cache_rows:
            continue
        seen_cache_rows.add(dedupe_key)

        protocol_key = str(protocol_title or "").strip() or "פרוטוקול"
        root_topic, child_topic = _split_topic_path(topic_name_value)
        root_topic = _sanitize_topic_label(root_topic, min_tokens=1) or PLACEHOLDER_TOPIC_LABEL
        child_topic = _sanitize_topic_label(child_topic, min_tokens=1)
        if normalize_for_search(child_topic) in {
            normalize_for_search("החלטה כללית"),
            normalize_for_search("כללית"),
        }:
            child_topic = ""
        variant_topic = child_topic or root_topic

        level_key = (str(question_hash or ""), str(chunk_id or ""), str(summary_he or ""))
        granularity_level = granularity_by_key.get(level_key)
        if granularity_level is None:
            granularity_level = _topic_granularity_heuristic(variant_topic)

        object_root = _object_root_from_topic_parts(root_topic=root_topic, child_topic=variant_topic)
        if object_root:
            merged_root = object_root
        else:
            merged_root = _merge_head_phrase(variant_topic, granularity_level=granularity_level)

        variant_topic = _canonicalize_topic_child(
            root_topic=merged_root,
            child_topic=variant_topic,
        )
        root_bucket = global_roots.setdefault(
            merged_root,
            {
                "count": 0,
                "last_seen_at": None,
                "protocols": set(),
                "variants": {},
            },
        )
        root_bucket["count"] += 1
        root_bucket["protocols"].add(protocol_key)
        if isinstance(updated_at, datetime):
            iso = updated_at.isoformat()
            if root_bucket["last_seen_at"] is None or iso > root_bucket["last_seen_at"]:
                root_bucket["last_seen_at"] = iso

        lexical_overlap = _topic_lexical_overlap(variant_topic, merged_root)
        variant_tokens = _topic_merge_tokens(variant_topic)
        keep_as_variant = (
            normalize_for_search(merged_root) != normalize_for_search(PLACEHOLDER_TOPIC_LABEL)
            and
            normalize_for_search(variant_topic) != normalize_for_search(merged_root)
            and (
                granularity_level <= 6
                or lexical_overlap >= 2
                or len(variant_tokens) >= 2
            )
        )
        if not keep_as_variant:
            continue

        variant_bucket = root_bucket["variants"].setdefault(
            variant_topic,
            {
                "count": 0,
                "last_seen_at": None,
                "protocols": set(),
                "avg_granularity": 0.0,
                "samples": 0,
            },
        )
        variant_bucket["count"] += 1
        variant_bucket["protocols"].add(protocol_key)
        variant_bucket["samples"] += 1
        variant_bucket["avg_granularity"] += (granularity_level - variant_bucket["avg_granularity"]) / variant_bucket["samples"]
        if isinstance(updated_at, datetime):
            iso = updated_at.isoformat()
            if variant_bucket["last_seen_at"] is None or iso > variant_bucket["last_seen_at"]:
                variant_bucket["last_seen_at"] = iso

    _coalesce_sparse_single_token_roots(global_roots)

    root_items: list[dict[str, Any]] = []
    for root_topic, root_payload in sorted(global_roots.items(), key=lambda row: row[1]["count"], reverse=True):
        child_items = [
            {
                "topic": variant_topic,
                "count": int(variant_payload["count"]),
                "last_seen_at": variant_payload["last_seen_at"],
                "protocol_count": len(variant_payload["protocols"]),
                "avg_granularity": round(float(variant_payload["avg_granularity"]), 2),
            }
            for variant_topic, variant_payload in sorted(
                root_payload["variants"].items(),
                key=lambda row: row[1]["count"],
                reverse=True,
            )
        ]
        root_items.append(
            {
                "topic": root_topic,
                "count": int(root_payload["count"]),
                "last_seen_at": root_payload["last_seen_at"],
                "protocol_count": len(root_payload["protocols"]),
                "children": child_items,
            }
        )

    return {
        "count": len(root_items),
        "roots": root_items,
    }


@app.get("/semantic/runs/{document_version_id}")
def semantic_runs(document_version_id: int, db=Depends(get_db)) -> dict:
    runs = db.execute(
        select(SemanticDocumentRun)
        .where(SemanticDocumentRun.document_version_id == document_version_id)
        .order_by(SemanticDocumentRun.started_at.desc(), SemanticDocumentRun.id.desc())
    ).scalars().all()
    if not runs:
        return {
            "document_version_id": document_version_id,
            "count": 0,
            "runs": [],
        }

    run_ids = [run.id for run in runs]
    reject_rows = db.execute(
        select(SemanticCandidateReject.semantic_document_run_id, SemanticCandidateReject.reason_code).where(
            SemanticCandidateReject.semantic_document_run_id.in_(run_ids)
        )
    ).all()
    reject_histogram_by_run: dict[int, dict[str, int]] = {}
    for run_id, reason_code in reject_rows:
        bucket = reject_histogram_by_run.setdefault(run_id, {})
        bucket[reason_code] = bucket.get(reason_code, 0) + 1

    runs_payload = []
    for run in runs:
        validation_payload = _loads_json(run.validation_report_json)
        validation_dict = validation_payload if isinstance(validation_payload, dict) else {}
        issues_payload_raw = validation_dict.get("issues")
        issues_payload: list = issues_payload_raw if isinstance(issues_payload_raw, list) else []

        canonical_payload = _loads_json(run.canonicalization_report_json)
        canonical_dict = canonical_payload if isinstance(canonical_payload, dict) else {}
        accepted_nodes_raw = canonical_dict.get("accepted_nodes")
        rejected_nodes_raw = canonical_dict.get("rejected_nodes")
        warnings_raw = canonical_dict.get("warnings")
        accepted_nodes: list = accepted_nodes_raw if isinstance(accepted_nodes_raw, list) else []
        rejected_nodes: list = rejected_nodes_raw if isinstance(rejected_nodes_raw, list) else []
        warnings: list = warnings_raw if isinstance(warnings_raw, list) else []

        runs_payload.append(
            {
                "run_id": run.id,
                "status": run.status,
                "api_call_count": run.api_call_count,
                "prompt_hash": run.prompt_hash,
                "model_provider": run.model_provider,
                "model_name": run.model_name,
                "request_tokens": run.request_tokens,
                "response_tokens": run.response_tokens,
                "error_code": run.error_code,
                "error_text": run.error_text,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "validation": {
                    "is_valid": bool(validation_dict.get("is_valid")),
                    "issue_count": len(issues_payload),
                    "issues": issues_payload,
                },
                "canonicalization": {
                    "accepted_nodes": len(accepted_nodes),
                    "rejected_nodes": len(rejected_nodes),
                    "warning_count": len(warnings),
                    "warnings": warnings,
                },
                "reject_reason_histogram": reject_histogram_by_run.get(run.id, {}),
            }
        )

    return {
        "document_version_id": document_version_id,
        "count": len(runs_payload),
        "runs": runs_payload,
    }


@app.get("/meeting/{meeting_id}")
def meeting_detail(meeting_id: int, db=Depends(get_db)) -> dict:
    meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=404, detail="meeting_not_found")

    site = db.execute(select(SourceSite).where(SourceSite.id == meeting.source_site_id)).scalar_one_or_none()
    decisions = db.execute(
        select(Decision)
        .where(Decision.meeting_id == meeting.id, Decision.is_public.is_(True))
        .order_by(Decision.id.asc())
    ).scalars().all()
    links = db.execute(
        select(MeetingDocumentLink, Document)
        .join(Document, Document.id == MeetingDocumentLink.document_id)
        .where(MeetingDocumentLink.meeting_id == meeting.id)
        .order_by(MeetingDocumentLink.is_primary.desc(), Document.id.asc())
    ).all()

    decisions_payload = []
    for decision in decisions:
        vote = db.execute(select(Vote).where(Vote.decision_id == decision.id)).scalar_one_or_none()
        citation_count = db.execute(
            select(DecisionCitation.id).where(DecisionCitation.decision_id == decision.id)
        ).scalars().all()
        decisions_payload.append(
            {
                "id": decision.id,
                "decision_number": decision.decision_number,
                "agenda_item": decision.agenda_item,
                "decision_text": decision.decision_text,
                "parser_confidence": decision.parser_confidence,
                "citation_count": len(citation_count),
                "source_type": "protocol",
                "vote": {
                    "for_count": vote.for_count,
                    "against_count": vote.against_count,
                    "abstain_count": vote.abstain_count,
                    "unanimous": vote.unanimous,
                    "is_uncertain": vote.is_uncertain,
                }
                if vote
                else None,
                "metadata": _fallback_metadata_from_json(decision.metadata_json),
                "request_context": _decision_request_context_payload(db=db, decision_id=decision.id),
            }
        )

    return {
        "meeting": {
            "id": meeting.id,
            "municipality": site.municipality_slug if site else None,
            "meeting_external_id": meeting.meeting_external_id,
            "title_he": meeting.title_he,
            "committee_name": meeting.committee_name,
            "meeting_kind": meeting.meeting_kind,
            "meeting_code": meeting.meeting_code,
            "meeting_date": meeting.meeting_date,
            "parse_confidence": meeting.parse_confidence,
            "summary": None,
        },
        "documents": [
            {
                "id": document.id,
                "title": document.title_he,
                "url": document.canonical_url,
                "doc_kind": document.doc_kind,
                "source_type": link.source_type,
                "provenance": link.provenance,
                "is_primary": bool(link.is_primary),
            }
            for link, document in links
        ],
        "decisions": decisions_payload,
    }


@app.get("/decision/{decision_id}")
def decision_detail(decision_id: int, db=Depends(get_db)) -> dict:
    payload = _decision_payload(decision_id, db)
    if payload is None:
        raise HTTPException(status_code=404, detail="decision_not_found")
    return payload


@app.get("/ui/decision/{decision_id}", response_class=HTMLResponse)
def decision_card_page(decision_id: int, db=Depends(get_db)) -> HTMLResponse:
    payload = _decision_payload(decision_id, db)
    if payload is None:
        raise HTTPException(status_code=404, detail="decision_not_found")

    decision = payload["decision"]
    metadata = decision["metadata"]
    votes = payload["votes"]
    citations = payload["citations"]
    linked_documents = payload["linked_documents"]

    fallback_badge = ""
    if metadata.get("fallback_used"):
        fallback_badge = "<span class='badge fallback'>Fallback Bytez</span>"

    vote_html = "<div class='muted'>לא זוהו נתוני הצבעה.</div>"
    if votes:
        vote = votes[0]
        if vote.get("unanimous"):
            vote_html = "<div class='vote'>אושר פה אחד</div>"
        elif vote.get("is_uncertain"):
            vote_html = f"<div class='vote uncertain'>זוהתה הצבעה לא ודאית: {html.escape(vote.get('raw_text') or '')}</div>"
        else:
            vote_html = (
                "<div class='vote'>"
                f"בעד: {vote.get('for_count')} | נגד: {vote.get('against_count')} | נמנע: {vote.get('abstain_count')}"
                "</div>"
            )

    citation_parts: list[str] = []
    for item in citations:
        anchor_label = item.get("anchor_label") or f"עמוד {item['page']}"
        citation_parts.append(
            "<li>"
            f"<a href='{html.escape(item['document']['url'])}#page={item['page']}' target='_blank' rel='noopener'>"
            f"{html.escape(anchor_label)}"
            "</a>"
            f" <span class='muted'>{html.escape(item['document']['title'])}</span>"
            "</li>"
        )
    citation_items = "".join(citation_parts)

    document_items = "".join(
        (
            "<li>"
            f"<a href='{html.escape(item['url'])}' target='_blank' rel='noopener'>{html.escape(item['title'])}</a>"
            f" <span class='muted'>[{html.escape(item['source_type'])} | {html.escape(item['provenance'])}]</span>"
            "</li>"
        )
        for item in linked_documents
    )
    default_muni_json = json.dumps(decision.get("municipality") or "", ensure_ascii=False)
    default_topic_json = json.dumps(decision.get("agenda_item") or "", ensure_ascii=False)

    html_page = f"""
<!doctype html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>כרטיס החלטה {decision['id']}</title>
  <style>
    :root {{
      --bg-a: #f3f8f6;
      --bg-b: #fdfaf2;
      --ink: #15343b;
      --muted: #53707a;
      --panel: rgba(255,255,255,0.82);
      --line: #d7e6df;
      --accent: #0f7b80;
      --warn: #b65b1d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Heebo", "Rubik", sans-serif;
      color: var(--ink);
      background: radial-gradient(1200px 500px at 100% -10%, #d8ece7 0%, transparent 70%),
                  radial-gradient(1000px 400px at -10% 110%, #f9ead0 0%, transparent 70%),
                  linear-gradient(130deg, var(--bg-a), var(--bg-b));
      min-height: 100vh;
      padding: 22px;
    }}
    .card {{
      max-width: 900px;
      margin: 0 auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 24px;
      box-shadow: 0 14px 36px rgba(21, 52, 59, 0.11);
      backdrop-filter: blur(2px);
    }}
    h1 {{ margin: 0 0 10px; font-size: 1.7rem; line-height: 1.35; }}
    h2 {{ margin: 18px 0 8px; font-size: 1.05rem; }}
    p {{ margin: 0; line-height: 1.8; }}
    ul {{ margin: 8px 0 0; padding-inline-start: 20px; }}
    li {{ margin: 6px 0; line-height: 1.5; }}
    .top {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }}
    .badge {{
      display: inline-block;
      border-radius: 999px;
      font-size: 0.82rem;
      padding: 5px 10px;
      border: 1px solid var(--line);
      background: #e9f2ee;
    }}
    .badge.fallback {{ border-color: #f1c8a2; background: #fff2e5; color: #8a430f; }}
    .muted {{ color: var(--muted); font-size: 0.9rem; }}
    .vote {{ margin-top: 8px; font-weight: 600; color: var(--accent); }}
    .vote.uncertain {{ color: var(--warn); }}
    .grid {{ display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); margin-top: 14px; }}
    .panel {{ border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; background: #fff; }}
    .ask-panel {{ margin-top: 14px; }}
    .ask-form {{ display: grid; gap: 8px; margin-top: 8px; }}
    .ask-form label {{ font-weight: 600; font-size: 0.92rem; }}
    .ask-form textarea {{
      width: 100%;
      min-height: 104px;
      border: 1px solid #bfd3cb;
      border-radius: 10px;
      padding: 10px;
      font-family: inherit;
      line-height: 1.6;
      resize: vertical;
    }}
    .ask-controls {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
    .ask-controls input {{
      width: 84px;
      border: 1px solid #bfd3cb;
      border-radius: 8px;
      padding: 6px 8px;
      font-family: inherit;
    }}
    .ask-controls button {{
      border: 1px solid #0e6d72;
      background: #0f7b80;
      color: #fff;
      border-radius: 999px;
      padding: 8px 16px;
      font-family: inherit;
      font-weight: 600;
      cursor: pointer;
    }}
    .ask-controls button:hover {{ background: #0c666b; }}
    .ask-output {{ margin-top: 10px; border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; }}
    .ask-output.refusal {{ border-color: #e6d1be; background: #fff8f2; }}
    .ask-output h3 {{ margin: 0 0 8px; font-size: 0.98rem; }}
    #ask-answer-text {{ direction: rtl; text-align: right; white-space: pre-line; }}
    .answer-sections {{ margin-top: 10px; display: grid; gap: 10px; }}
    .protocol-group {{ border: 1px solid #d7e5ee; border-radius: 11px; padding: 10px; background: #fdfefe; }}
    .protocol-group > h4 {{ margin: 0 0 8px; font-size: 0.96rem; color: #23424f; }}
    .topic-root, .topic-child {{ border: 1px solid #e0eaf0; border-radius: 9px; background: #fff; }}
    .topic-root + .topic-root {{ margin-top: 8px; }}
    .topic-root > summary, .topic-child > summary {{
      cursor: pointer;
      list-style: none;
      padding: 8px 10px;
      font-weight: 600;
      color: #2f4d58;
      border-bottom: 1px solid #ecf2f7;
    }}
    .topic-root > summary::-webkit-details-marker, .topic-child > summary::-webkit-details-marker {{ display: none; }}
    .topic-root > summary::before, .topic-child > summary::before {{ content: "▾ "; color: #668390; }}
    .topic-root:not([open]) > summary::before, .topic-child:not([open]) > summary::before {{ content: "▸ "; }}
    .topic-root-content {{ padding: 8px; display: grid; gap: 8px; }}
    .topic-child-content {{ padding: 8px 10px; display: grid; gap: 8px; }}
    .answer-section {{ border: 1px solid #dde7ee; border-radius: 10px; padding: 10px; background: #fbfdff; }}
    .answer-section h4 {{ margin: 0 0 4px; font-size: 0.98rem; }}
    .answer-section .topic {{ margin: 0 0 6px; color: #4c5963; font-size: 0.88rem; }}
    .answer-section .text {{ margin: 0; direction: rtl; text-align: right; }}
    .decision-sources {{ margin: 0; padding-inline-start: 18px; }}
    .decision-sources li {{ margin: 4px 0; }}
    pre {{
      margin: 0;
      border: 1px solid #d5e4ea;
      border-radius: 10px;
      padding: 10px;
      background: #f8fbfd;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 0.82rem;
      line-height: 1.45;
      direction: ltr;
      text-align: left;
      max-height: 260px;
      overflow: auto;
    }}
    .hidden {{ display: none; }}
    a {{ color: #00696f; text-decoration: none; border-bottom: 1px dotted #86b7bb; }}
    a:hover {{ border-bottom-style: solid; }}
    @media (max-width: 640px) {{
      body {{ padding: 14px; }}
      .card {{ padding: 16px; border-radius: 14px; }}
    }}
  </style>
</head>
<body>
  <article class="card">
    <div class="top">
      <span class="badge">החלטה #{decision['id']}</span>
      <span class="badge">פגישת מקור: {html.escape(str(decision.get('meeting_external_id') or '-'))}</span>
      {fallback_badge}
    </div>
    <h1>{html.escape(decision.get('decision_text') or '')}</h1>
    <p class="muted">סעיף: {html.escape(decision.get('agenda_item') or 'סעיף לא מזוהה')}</p>
    <h2>תוצאת הצבעה</h2>
    {vote_html}
    <section class="grid">
      <section class="panel">
        <h2>ציטוטים מאומתים</h2>
        <ul>{citation_items or '<li class="muted">לא נמצאו ציטוטים.</li>'}</ul>
      </section>
      <section class="panel">
        <h2>מסמכים קשורים</h2>
        <ul>{document_items or '<li class="muted">לא נמצאו מסמכים קשורים.</li>'}</ul>
      </section>
    </section>
    <section class="panel ask-panel" id="ask-panel">
      <h2>שאלו על הישיבה (ציטוטים בלבד)</h2>
      <p class="muted">השאלה נשלחת אל <code>POST /ask</code> עם הקשר הישיבה והסעיף הנוכחי.</p>
      <form id="ask-form" class="ask-form">
        <label for="ask-question">שאלה</label>
        <textarea id="ask-question" name="question" required placeholder="לדוגמה: אילו צעדים אושרו ומה הראיות לכך?"></textarea>
        <div class="ask-controls">
          <label for="ask-top-k" class="muted">top_k</label>
          <input id="ask-top-k" name="top_k" type="number" min="1" max="50" value="8" />
          <label class="muted"><input id="ask-debug-mode" type="checkbox" checked /> מצב debug (ספי חישוב)</label>
          <button type="submit">שאל</button>
        </div>
      </form>
      <p id="ask-status" class="muted" aria-live="polite"></p>
      <section id="ask-answer-panel" class="ask-output hidden">
        <h3>תשובה</h3>
        <p id="ask-answer-text"></p>
        <div id="ask-answer-sections" class="answer-sections"></div>
        <ul id="ask-limitations"></ul>
      </section>
      <section id="ask-refusal-panel" class="ask-output refusal hidden">
        <h3>מצב סירוב</h3>
        <p id="ask-refusal-text"></p>
      </section>
      <section id="ask-citations-panel" class="ask-output hidden">
        <h3>ציטוטים תומכים</h3>
        <ul id="ask-citations-list"></ul>
      </section>
      <section id="ask-thresholds-panel" class="ask-output hidden">
        <h3>Debug thresholds</h3>
        <pre id="ask-thresholds-json"></pre>
      </section>
    </section>
    <p class="muted" style="margin-top: 12px;">סטטוס אימות fallback: {html.escape(str(metadata.get('fallback_validation_status')))}</p>
  </article>
  <script>
    (() => {{
      const form = document.getElementById("ask-form");
      if (!form) {{
        return;
      }}

      const questionInput = document.getElementById("ask-question");
      const topKInput = document.getElementById("ask-top-k");
      const debugModeInput = document.getElementById("ask-debug-mode");
      const statusNode = document.getElementById("ask-status");
      const answerPanel = document.getElementById("ask-answer-panel");
      const answerText = document.getElementById("ask-answer-text");
      const answerSectionsNode = document.getElementById("ask-answer-sections");
      const limitationsList = document.getElementById("ask-limitations");
      const refusalPanel = document.getElementById("ask-refusal-panel");
      const refusalText = document.getElementById("ask-refusal-text");
      const citationsPanel = document.getElementById("ask-citations-panel");
      const citationsList = document.getElementById("ask-citations-list");
      const thresholdsPanel = document.getElementById("ask-thresholds-panel");
      const thresholdsJson = document.getElementById("ask-thresholds-json");

      const defaultMuni = {default_muni_json};
      const defaultTopic = {default_topic_json};
      const requiredSourceTypes = ["protocol", "attachment"];

      const hide = (node) => {{
        if (node) {{
          node.classList.add("hidden");
        }}
      }};
      const show = (node) => {{
        if (node) {{
          node.classList.remove("hidden");
        }}
      }};

      const clearList = (listNode) => {{
        if (listNode) {{
          listNode.innerHTML = "";
        }}
      }};

      const appendListItem = (listNode, textValue) => {{
        if (!listNode || !textValue) {{
          return;
        }}
        const item = document.createElement("li");
        item.textContent = textValue;
        listNode.appendChild(item);
      }};

      const splitTopicPath = (topicValue, fallbackText) => {{
        const rawTopic = String(topicValue || "").trim();
        const parts = rawTopic.split(">").map((part) => part.trim()).filter(Boolean);
        if (parts.length >= 2) {{
          return {{ root: parts[0], child: parts.slice(1).join(" > ") }};
        }}
        const fallbackWords = String(fallbackText || "").trim().split(/\\s+/).filter(Boolean);
        return {{
          root: parts[0] || "נושא כללי",
          child: fallbackWords.slice(0, 4).join(" ") || "החלטה",
        }};
      }};

      form.addEventListener("submit", async (event) => {{
        event.preventDefault();
        const question = (questionInput.value || "").trim();
        if (!question) {{
          statusNode.textContent = "יש להזין שאלה לפני השליחה.";
          return;
        }}

        const topKRaw = parseInt(topKInput.value || "8", 10);
        const topK = Number.isFinite(topKRaw) ? Math.max(1, Math.min(50, topKRaw)) : 8;
        const payload = {{
          question: question,
          top_k: topK,
          source_types: requiredSourceTypes,
          required_source_types: requiredSourceTypes,
          semantic_mode: "off",
          debug_mode: Boolean(debugModeInput && debugModeInput.checked),
        }};
        if (defaultMuni) {{
          payload.muni = defaultMuni;
        }}
        if (defaultTopic) {{
          payload.topic = defaultTopic;
        }}

        statusNode.textContent = "שולח שאלה...";
        hide(answerPanel);
        hide(refusalPanel);
        hide(citationsPanel);
        hide(thresholdsPanel);
        clearList(limitationsList);
        clearList(citationsList);
        if (answerSectionsNode) {{
          answerSectionsNode.innerHTML = "";
        }}
        if (thresholdsJson) {{
          thresholdsJson.textContent = "";
        }}

        try {{
          const response = await fetch("/ask", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify(payload),
          }});
          if (!response.ok) {{
            const errorBody = await response.text().catch(() => "");
            const errorSuffix = errorBody ? `: ${{errorBody.slice(0, 220)}}` : "";
            throw new Error(`HTTP ${{response.status}}${{errorSuffix}}`);
          }}

          const data = await response.json();
          const debugPayload = data.debug && typeof data.debug === "object" ? data.debug : null;
          if (debugPayload && debugPayload.thresholds && thresholdsJson) {{
            thresholdsJson.textContent = JSON.stringify(debugPayload.thresholds, null, 2);
            show(thresholdsPanel);
          }} else {{
            hide(thresholdsPanel);
          }}
          if (data.status === "answer") {{
            const sections = Array.isArray(data.answer_sections) ? data.answer_sections : [];
            const citationRows = Array.isArray(data.citations) ? data.citations : [];
            if (sections.length > 0) {{
              answerText.textContent = "";
              hide(answerText);
            }} else {{
              answerText.textContent = data.answer || "";
              show(answerText);
            }}

            if (answerSectionsNode) {{
              answerSectionsNode.innerHTML = "";
              const citationByChunk = new Map();
              for (const citation of citationRows) {{
                if (!citation || typeof citation !== "object") {{
                  continue;
                }}
                const chunkId = typeof citation.chunk_id === "string" ? citation.chunk_id : "";
                if (!chunkId || citationByChunk.has(chunkId)) {{
                  continue;
                }}
                citationByChunk.set(chunkId, citation);
              }}

              const protocolOrder = [];
              const protocolBuckets = new Map();
              for (const section of sections) {{
                if (!section || typeof section !== "object") {{
                  continue;
                }}
                const protocolTitle = typeof section.protocol_title === "string" ? section.protocol_title : "פרוטוקול";
                const topic = typeof section.topic_name === "string" ? section.topic_name : "נושא כללי";
                const text = typeof section.text === "string" ? section.text : "";
                if (!text.trim()) {{
                  continue;
                }}
                const path = splitTopicPath(topic, text);
                const chunkIds = Array.isArray(section.chunk_ids) ? section.chunk_ids : [];

                if (!protocolBuckets.has(protocolTitle)) {{
                  protocolBuckets.set(protocolTitle, []);
                  protocolOrder.push(protocolTitle);
                }}
                protocolBuckets.get(protocolTitle).push({{
                  root: path.root,
                  child: path.child,
                  text,
                  chunkIds,
                }});
              }}

              for (const protocolTitle of protocolOrder) {{
                const protocolNode = document.createElement("section");
                protocolNode.className = "protocol-group";

                const protocolHeader = document.createElement("h4");
                protocolHeader.textContent = protocolTitle;
                protocolNode.appendChild(protocolHeader);

                const rows = protocolBuckets.get(protocolTitle) || [];
                const rootOrder = [];
                const rootBuckets = new Map();
                for (const row of rows) {{
                  if (!rootBuckets.has(row.root)) {{
                    rootBuckets.set(row.root, []);
                    rootOrder.push(row.root);
                  }}
                  rootBuckets.get(row.root).push(row);
                }}

                for (const rootLabel of rootOrder) {{
                  const rootDetails = document.createElement("details");
                  rootDetails.className = "topic-root";
                  rootDetails.open = true;

                  const rootSummary = document.createElement("summary");
                  rootSummary.textContent = rootLabel;
                  rootDetails.appendChild(rootSummary);

                  const rootContent = document.createElement("div");
                  rootContent.className = "topic-root-content";
                  for (const row of rootBuckets.get(rootLabel) || []) {{
                    const childDetails = document.createElement("details");
                    childDetails.className = "topic-child";
                    childDetails.open = true;

                    const childSummary = document.createElement("summary");
                    childSummary.textContent = row.child;
                    childDetails.appendChild(childSummary);

                    const childContent = document.createElement("div");
                    childContent.className = "topic-child-content";

                    const textNode = document.createElement("p");
                    textNode.className = "text";
                    textNode.textContent = row.text;
                    childContent.appendChild(textNode);

                    const sourceRows = [];
                    for (const chunkId of row.chunkIds) {{
                      if (typeof chunkId !== "string") {{
                        continue;
                      }}
                      const citation = citationByChunk.get(chunkId);
                      if (citation) {{
                        sourceRows.push(citation);
                      }}
                    }}
                    if (sourceRows.length > 0) {{
                      const sourceTitle = document.createElement("p");
                      sourceTitle.className = "topic";
                      sourceTitle.textContent = "מקורות:";
                      childContent.appendChild(sourceTitle);

                      const sourceList = document.createElement("ul");
                      sourceList.className = "decision-sources";
                      for (const citation of sourceRows) {{
                        const documentPayload = citation.document && typeof citation.document === "object" ? citation.document : {{}};
                        const page = Number.isFinite(Number(citation.start_page)) ? Number(citation.start_page) : null;
                        const hrefBase = documentPayload.url || "#";
                        const sourceItem = document.createElement("li");
                        const sourceLink = document.createElement("a");
                        sourceLink.href = page ? `${{hrefBase}}#page=${{page}}` : hrefBase;
                        sourceLink.target = "_blank";
                        sourceLink.rel = "noopener";
                        sourceLink.textContent = citation.citation || (page ? `עמוד ${{page}}` : "מקור");
                        sourceItem.appendChild(sourceLink);

                        const sourceMeta = document.createElement("span");
                        sourceMeta.className = "muted";
                        const sourceType = citation.source_type || "source";
                        const title = documentPayload.title || "מסמך";
                        sourceMeta.textContent = ` [${{sourceType}}] ${{title}}`;
                        sourceItem.appendChild(sourceMeta);
                        sourceList.appendChild(sourceItem);
                      }}
                      childContent.appendChild(sourceList);
                    }}

                    childDetails.appendChild(childContent);
                    rootContent.appendChild(childDetails);
                  }}
                  rootDetails.appendChild(rootContent);
                  protocolNode.appendChild(rootDetails);
                }}

                answerSectionsNode.appendChild(protocolNode);
              }}
            }}
            const limitations = Array.isArray(data.limitations) ? data.limitations : [];
            if (limitations.length === 0) {{
              appendListItem(limitationsList, "לא צוינו מגבלות נוספות.");
            }} else {{
              for (const limitation of limitations) {{
                appendListItem(limitationsList, limitation);
              }}
            }}

            if (citationRows.length === 0) {{
              appendListItem(citationsList, "לא הוחזרו ציטוטים.");
            }} else {{
              for (const citation of citationRows) {{
                const documentPayload = citation.document && typeof citation.document === "object" ? citation.document : {{}};
                const page = Number.isFinite(Number(citation.start_page)) ? Number(citation.start_page) : null;
                const hrefBase = documentPayload.url || "#";
                const link = document.createElement("a");
                link.href = page ? `${{hrefBase}}#page=${{page}}` : hrefBase;
                link.target = "_blank";
                link.rel = "noopener";
                link.textContent = citation.citation || (page ? `עמוד ${{page}}` : "מקור");

                const meta = document.createElement("span");
                meta.className = "muted";
                const sourceType = citation.source_type || "source";
                const title = documentPayload.title || "מסמך";
                meta.textContent = ` [${{sourceType}}] ${{title}}`;

                const item = document.createElement("li");
                item.appendChild(link);
                item.appendChild(meta);
                citationsList.appendChild(item);
              }}
            }}

            show(answerPanel);
            show(citationsPanel);
            hide(refusalPanel);
            statusNode.textContent = "התקבלה תשובה מבוססת ציטוטים.";
            return;
          }}

          const refusalPayload = data.refusal && typeof data.refusal === "object" ? data.refusal : {{}};
          const refusalMessage = refusalPayload.message_he || "אין מספיק ראיות כדי להשיב.";
          const missingTypes = Array.isArray(refusalPayload.missing_source_types)
            ? refusalPayload.missing_source_types.filter((value) => typeof value === "string" && value)
            : [];
          const missingHint = missingTypes.length ? ` חסרים: ${{missingTypes.join(", ")}}.` : "";
          refusalText.textContent = `${{refusalMessage}}${{missingHint}}`;

          show(refusalPanel);
          hide(answerPanel);
          hide(citationsPanel);
          statusNode.textContent = "המערכת סירבה להשיב בגלל חוסר ראיות מספק.";
        }} catch (_err) {{
          console.error("ask_inline_panel_request_failed", _err);
          const errorMessage = _err instanceof Error ? _err.message : String(_err || "unknown_error");
          hide(answerPanel);
          hide(refusalPanel);
          hide(citationsPanel);
          hide(thresholdsPanel);
          statusNode.textContent = `שליחת השאלה נכשלה: ${{errorMessage}}`;
        }}
      }});
    }})();
  </script>
</body>
</html>
"""
    return HTMLResponse(html_page)


@app.get("/runs/{run_id}")
def run_status(run_id: int, db=Depends(get_db)) -> dict:
    run = db.execute(select(PipelineRun).where(PipelineRun.id == run_id)).scalar_one_or_none()
    if not run:
        return {"error": "not_found", "run_id": run_id}
    steps = db.execute(select(PipelineRunStep).where(PipelineRunStep.run_id == run_id)).scalars().all()
    return {
        "run_id": run.id,
        "status": run.status,
        "run_type": run.run_type,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "steps": [
            {"id": step.id, "step": step.step_name, "status": step.status, "item_ref": step.item_ref, "detail": step.detail}
            for step in steps
        ],
    }
