from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.chunking import normalize_for_search
from municipality.embeddings import ChunkEmbeddingService, _cosine_similarity
from municipality.models import RagAnswerCache


@dataclass(slots=True)
class RagAnswerCacheHit:
    payload: dict[str, Any]
    similarity: float
    cache_id: int


class RagAnswerCacheService:
    def __init__(self, session: Session, *, embedding_service: ChunkEmbeddingService | None = None):
        self.session = session
        self.embedding_service = embedding_service or ChunkEmbeddingService(session)
        self.enabled = _env_bool(os.getenv("RAG_ANSWER_CACHE_ENABLED"), default=True)
        self.min_similarity = _env_float(os.getenv("RAG_ANSWER_CACHE_MIN_SIMILARITY"), default=0.985, min_value=0.7, max_value=1.0)
        self.max_candidates = _env_int(os.getenv("RAG_ANSWER_CACHE_MAX_CANDIDATES"), default=12, min_value=1, max_value=50)

    def is_enabled(self) -> bool:
        return self.enabled and self.embedding_service.is_enabled()

    def lookup(self, *, query: str, retrieval_set_id: str) -> RagAnswerCacheHit | None:
        if not self.is_enabled() or not retrieval_set_id:
            return None
        query_vector = self.embedding_service.embed_query(query, query_kind="answer_cache")
        if not query_vector:
            return None
        try:
            rows = self.session.execute(
                select(RagAnswerCache)
                .where(
                    RagAnswerCache.retrieval_set_id == retrieval_set_id,
                    RagAnswerCache.embedding_provider == self.embedding_service.model_client.provider_name,
                    RagAnswerCache.embedding_model == self.embedding_service.model_client.model_name,
                    RagAnswerCache.embedding_dimensions == self.embedding_service.model_client.dimensions,
                )
                .order_by(RagAnswerCache.updated_at.desc(), RagAnswerCache.id.desc())
                .limit(self.max_candidates)
            ).scalars().all()
        except Exception:  # noqa: BLE001
            self.session.rollback()
            return None
        best_payload: dict[str, Any] | None = None
        best_cache_id: int | None = None
        best_similarity = 0.0
        for row in rows:
            try:
                cached_vector = json.loads(row.query_embedding_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(cached_vector, list) or not cached_vector:
                continue
            similarity = _cosine_similarity(query_vector, [float(value) for value in cached_vector])
            if similarity < self.min_similarity or similarity <= best_similarity:
                continue
            payload = _loads_json_dict(row.answer_payload_json)
            if not payload or is_degraded_answer_cache_payload(payload):
                continue
            best_payload = payload
            best_cache_id = int(row.id)
            best_similarity = similarity
        if best_payload is None or best_cache_id is None:
            return None
        return RagAnswerCacheHit(payload=best_payload, similarity=round(best_similarity, 6), cache_id=best_cache_id)

    def store(
        self,
        *,
        query: str,
        query_hash: str,
        retrieval_set_id: str,
        answer_provider: str | None,
        answer_model: str | None,
        answer_payload: dict[str, Any],
    ) -> None:
        if not self.is_enabled() or not retrieval_set_id:
            return
        query_vector = self.embedding_service.embed_query(query, query_kind="answer_cache")
        if not query_vector:
            return
        try:
            now = datetime.utcnow()
            self.session.add(
                RagAnswerCache(
                    retrieval_set_id=retrieval_set_id,
                    query_hash=query_hash,
                    normalized_query=normalize_for_search(query),
                    embedding_provider=self.embedding_service.model_client.provider_name,
                    embedding_model=self.embedding_service.model_client.model_name,
                    embedding_dimensions=self.embedding_service.model_client.dimensions,
                    query_embedding_json=json.dumps(query_vector, separators=(",", ":")),
                    answer_provider=answer_provider,
                    answer_model=answer_model,
                    answer_payload_json=json.dumps(answer_payload, ensure_ascii=False),
                    created_at=now,
                    updated_at=now,
                )
            )
            self.session.flush()
        except Exception:  # noqa: BLE001
            self.session.rollback()


def _loads_json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def is_degraded_answer_cache_payload(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return True
    scoring = payload.get("scoring")
    if not isinstance(scoring, dict):
        return False
    if bool(scoring.get("degraded_upstream_failure")):
        return True
    route = str(scoring.get("answer_generation_route") or "").strip()
    return route == "deterministic_retrieval_fallback"


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
    return max(min_value, min(max_value, parsed))


def _env_float(value: str | None, *, default: float, min_value: float, max_value: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(min_value, min(max_value, parsed))
