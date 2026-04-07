from __future__ import annotations

import hashlib
import json
import importlib
import math
import os
from dataclasses import dataclass, replace as dataclass_replace
from datetime import datetime
from typing import Any, Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.chunking import normalize_for_search
from municipality.models import ChunkEmbedding, DecisionEmbedding, QueryEmbeddingCache


OPENAI_EMBEDDING_PROVIDER = "OpenAI"
LOCAL_HASH_EMBEDDING_PROVIDER = "LocalHash"
DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_LOCAL_HASH_MODEL = "local-hash-multilingual-v1"
DEFAULT_RERANK_WEIGHT = 0.35
DEFAULT_RERANK_CANDIDATE_MULTIPLIER = 12
DEFAULT_RERANK_MIN_CANDIDATES = 60
DEFAULT_RERANK_MAX_CANDIDATES = 120
DEFAULT_EMBED_BATCH_SIZE = 96


@dataclass(slots=True)
class EmbeddingConfig:
    enabled: bool = True
    provider: str = "openai"
    api_key: str | None = None
    model_name: str = DEFAULT_OPENAI_EMBEDDING_MODEL
    dimensions: int | None = None
    rerank_weight: float = DEFAULT_RERANK_WEIGHT
    rerank_candidate_multiplier: int = DEFAULT_RERANK_CANDIDATE_MULTIPLIER
    rerank_min_candidates: int = DEFAULT_RERANK_MIN_CANDIDATES
    rerank_max_candidates: int = DEFAULT_RERANK_MAX_CANDIDATES
    batch_size: int = DEFAULT_EMBED_BATCH_SIZE
    query_cache_enabled: bool = True

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> EmbeddingConfig:
        source = env if env is not None else os.environ
        provider = _normalize_embedding_provider(source.get("RAG_EMBEDDING_PROVIDER"))
        default_model = DEFAULT_LOCAL_HASH_MODEL if provider == "local_hash" else DEFAULT_OPENAI_EMBEDDING_MODEL
        return cls(
            enabled=_env_bool(source.get("RAG_EMBEDDING_RERANK_ENABLED"), default=True),
            provider=provider,
            api_key=(source.get("OPENAI_API_KEY") or "").strip() or None,
            model_name=(
                source.get("OPENAI_EMBEDDING_MODEL")
                or source.get("LOCAL_EMBEDDING_MODEL")
                or default_model
            ).strip()
            or default_model,
            dimensions=_env_optional_int(source.get("OPENAI_EMBEDDING_DIMENSIONS"), min_value=64, max_value=3072),
            rerank_weight=_env_float(
                source.get("RAG_EMBEDDING_RERANK_WEIGHT"),
                default=DEFAULT_RERANK_WEIGHT,
                min_value=0.0,
                max_value=1.0,
            ),
            rerank_candidate_multiplier=_env_int(
                source.get("RAG_EMBEDDING_CANDIDATE_MULTIPLIER"),
                default=DEFAULT_RERANK_CANDIDATE_MULTIPLIER,
                min_value=2,
                max_value=40,
            ),
            rerank_min_candidates=_env_int(
                source.get("RAG_EMBEDDING_MIN_CANDIDATES"),
                default=DEFAULT_RERANK_MIN_CANDIDATES,
                min_value=10,
                max_value=400,
            ),
            rerank_max_candidates=_env_int(
                source.get("RAG_EMBEDDING_MAX_CANDIDATES"),
                default=DEFAULT_RERANK_MAX_CANDIDATES,
                min_value=10,
                max_value=400,
            ),
            batch_size=_env_int(
                source.get("OPENAI_EMBEDDING_BATCH_SIZE"),
                default=DEFAULT_EMBED_BATCH_SIZE,
                min_value=1,
                max_value=512,
            ),
            query_cache_enabled=_env_bool(source.get("RAG_QUERY_EMBEDDING_CACHE_ENABLED"), default=True),
        )

    @property
    def effective_dimensions(self) -> int:
        return self.dimensions or _default_dimensions_for_model(self.model_name)


@dataclass(slots=True)
class ChunkEmbeddingIndexResult:
    enabled: bool
    created: int = 0
    cached: int = 0
    total: int = 0
    error_text: str | None = None


@dataclass(slots=True)
class ChunkEmbeddingLookupResult:
    vectors_by_chunk_id: dict[str, list[float]]
    created: int = 0
    cached: int = 0
    error_text: str | None = None


class EmbeddingModelClient(Protocol):
    @property
    def provider_name(self) -> str:
        raise NotImplementedError

    @property
    def model_name(self) -> str:
        raise NotImplementedError

    @property
    def dimensions(self) -> int:
        raise NotImplementedError

    def is_configured(self) -> bool:
        raise NotImplementedError

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError


class OpenAIEmbeddingClient:
    def __init__(self, *, config: EmbeddingConfig | None = None):
        self.config = config or EmbeddingConfig.from_env()
        self._client: Any | None = None
        if self.config.api_key:
            try:
                openai_module = importlib.import_module("openai")
            except ImportError:  # pragma: no cover - exercised only when dependency missing
                openai_module = None
            openai_cls = getattr(openai_module, "OpenAI", None) if openai_module is not None else None
            if openai_cls is not None:
                self._client = openai_cls(api_key=self.config.api_key)

    @property
    def provider_name(self) -> str:
        return OPENAI_EMBEDDING_PROVIDER

    @property
    def model_name(self) -> str:
        return self.config.model_name

    @property
    def dimensions(self) -> int:
        return self.config.effective_dimensions

    def is_configured(self) -> bool:
        return bool(self.config.enabled and self.config.provider == "openai" and self._client is not None)

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.is_configured() or not texts:
            return []
        client = self._client
        if client is None:
            return []

        vectors: list[list[float]] = []
        batch_size = max(1, int(self.config.batch_size))
        for start in range(0, len(texts), batch_size):
            batch = [text for text in texts[start : start + batch_size] if str(text or "").strip()]
            if not batch:
                continue

            kwargs: dict[str, Any] = {
                "model": self.model_name,
                "input": batch,
                "encoding_format": "float",
            }
            if self.config.dimensions is not None:
                kwargs["dimensions"] = self.config.dimensions

            response = client.embeddings.create(**kwargs)
            data = list(getattr(response, "data", []) or [])
            data.sort(key=lambda item: int(getattr(item, "index", 0) or 0))
            for item in data:
                vector = getattr(item, "embedding", None)
                if isinstance(vector, list) and vector:
                    vectors.append([float(value) for value in vector])
        return vectors


class LocalHashEmbeddingClient:
    def __init__(self, *, config: EmbeddingConfig | None = None):
        self.config = config or EmbeddingConfig.from_env()

    @property
    def provider_name(self) -> str:
        return LOCAL_HASH_EMBEDDING_PROVIDER

    @property
    def model_name(self) -> str:
        return self.config.model_name or DEFAULT_LOCAL_HASH_MODEL

    @property
    def dimensions(self) -> int:
        return self.config.effective_dimensions

    def is_configured(self) -> bool:
        return bool(self.config.enabled and self.config.provider == "local_hash")

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [_hash_embed_text(str(text or ""), dimensions=self.dimensions) for text in texts if str(text or "").strip()]


def build_embedding_client(*, config: EmbeddingConfig | None = None) -> EmbeddingModelClient:
    resolved_config = config or EmbeddingConfig.from_env()
    if resolved_config.provider == "local_hash":
        return LocalHashEmbeddingClient(config=resolved_config)
    return OpenAIEmbeddingClient(config=resolved_config)


class ChunkEmbeddingService:
    def __init__(
        self,
        session: Session,
        *,
        model_client: EmbeddingModelClient | None = None,
        config: EmbeddingConfig | None = None,
    ):
        self.session = session
        self.config = config or EmbeddingConfig.from_env()
        self.model_client = model_client or build_embedding_client(config=self.config)

    def is_enabled(self) -> bool:
        return self.config.enabled and self.model_client.is_configured()

    def initial_candidate_limit(self, *, top_k: int) -> int:
        base = max(top_k * self.config.rerank_candidate_multiplier, self.config.rerank_min_candidates)
        return max(top_k, min(base, self.config.rerank_max_candidates))

    def embed_query(self, query: str, *, query_kind: str = "ask") -> list[float] | None:
        text = str(query or "").strip()
        if not self.is_enabled() or not text:
            return None
        if self.config.query_cache_enabled:
            cached = self._load_query_embedding(query=text, query_kind=query_kind)
            if cached is not None:
                return cached
        try:
            vectors = self.model_client.embed_texts([text])
        except Exception:  # noqa: BLE001
            return None
        vector = vectors[0] if vectors else None
        if vector is not None and self.config.query_cache_enabled:
            self._store_query_embedding(query=text, query_kind=query_kind, vector=vector)
        return vector

    def index_chunks(self, *, chunks: Sequence[dict[str, Any]]) -> ChunkEmbeddingIndexResult:
        items = [
            (str(chunk.get("chunk_id") or "").strip(), str(chunk.get("chunk_text") or "").strip())
            for chunk in chunks
        ]
        return self._ensure_chunk_embeddings(items)

    def ensure_embeddings_for_hits(self, *, hits: Sequence[Any]) -> ChunkEmbeddingLookupResult:
        chunk_ids = [str(getattr(hit, "chunk_id", "") or "").strip() for hit in hits if getattr(hit, "chunk_id", None)]
        cached_vectors = self.load_vectors(chunk_ids)
        missing_rows: list[tuple[str, str]] = []
        for hit in hits:
            chunk_id = str(getattr(hit, "chunk_id", "") or "").strip()
            if not chunk_id or chunk_id in cached_vectors:
                continue
            chunk_text = str(getattr(hit, "chunk_text", "") or "").strip()
            if not chunk_text:
                continue
            missing_rows.append((chunk_id, chunk_text))

        created = 0
        error_text = None
        if missing_rows:
            created_result = self._ensure_chunk_embeddings(missing_rows)
            created = created_result.created
            error_text = created_result.error_text
            cached_vectors = self.load_vectors(chunk_ids)

        return ChunkEmbeddingLookupResult(
            vectors_by_chunk_id=cached_vectors,
            created=created,
            cached=len(cached_vectors) - created if cached_vectors else 0,
            error_text=error_text,
        )

    def load_vectors(self, chunk_ids: Sequence[str]) -> dict[str, list[float]]:
        normalized_ids = [str(chunk_id or "").strip() for chunk_id in chunk_ids if str(chunk_id or "").strip()]
        if not normalized_ids:
            return {}

        rows = self.session.execute(
            select(ChunkEmbedding).where(
                ChunkEmbedding.chunk_id.in_(normalized_ids),
                ChunkEmbedding.model_provider == self.model_client.provider_name,
                ChunkEmbedding.model_name == self.model_client.model_name,
                ChunkEmbedding.dimensions == self.model_client.dimensions,
            )
        ).scalars().all()

        out: dict[str, list[float]] = {}
        for row in rows:
            try:
                parsed = json.loads(row.embedding_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, list) or not parsed:
                continue
            out[str(row.chunk_id)] = [float(value) for value in parsed]
        return out

    def delete_chunk_embeddings(self, *, chunk_ids: Sequence[str]) -> None:
        normalized_ids = [str(chunk_id or "").strip() for chunk_id in chunk_ids if str(chunk_id or "").strip()]
        if not normalized_ids:
            return
        self.session.query(ChunkEmbedding).filter(ChunkEmbedding.chunk_id.in_(normalized_ids)).delete(
            synchronize_session=False
        )

    def _load_query_embedding(self, *, query: str, query_kind: str) -> list[float] | None:
        text_hash, normalized = _query_cache_identity(query)
        try:
            row = self.session.execute(
                select(QueryEmbeddingCache).where(
                    QueryEmbeddingCache.text_hash == text_hash,
                    QueryEmbeddingCache.query_kind == query_kind,
                    QueryEmbeddingCache.model_provider == self.model_client.provider_name,
                    QueryEmbeddingCache.model_name == self.model_client.model_name,
                    QueryEmbeddingCache.dimensions == self.model_client.dimensions,
                )
            ).scalar_one_or_none()
        except Exception:  # noqa: BLE001
            self.session.rollback()
            return None
        if row is None or row.normalized_text != normalized:
            return None
        try:
            parsed = json.loads(row.embedding_json)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, list) or not parsed:
            return None
        return [float(value) for value in parsed]

    def _store_query_embedding(self, *, query: str, query_kind: str, vector: list[float]) -> None:
        text_hash, normalized = _query_cache_identity(query)
        try:
            row = self.session.execute(
                select(QueryEmbeddingCache).where(
                    QueryEmbeddingCache.text_hash == text_hash,
                    QueryEmbeddingCache.query_kind == query_kind,
                    QueryEmbeddingCache.model_provider == self.model_client.provider_name,
                    QueryEmbeddingCache.model_name == self.model_client.model_name,
                    QueryEmbeddingCache.dimensions == self.model_client.dimensions,
                )
            ).scalar_one_or_none()
            now = datetime.utcnow()
            if row is None:
                row = QueryEmbeddingCache(
                    text_hash=text_hash,
                    normalized_text=normalized,
                    query_kind=query_kind,
                    model_provider=self.model_client.provider_name,
                    model_name=self.model_client.model_name,
                    dimensions=self.model_client.dimensions,
                    embedding_json=json.dumps(vector, separators=(",", ":")),
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(row)
            else:
                row.normalized_text = normalized
                row.embedding_json = json.dumps(vector, separators=(",", ":"))
                row.updated_at = now
            self.session.flush()
        except Exception:  # noqa: BLE001
            self.session.rollback()

    def _ensure_chunk_embeddings(self, rows: Sequence[tuple[str, str]]) -> ChunkEmbeddingIndexResult:
        normalized_rows: list[tuple[str, str]] = []
        seen_chunk_ids: set[str] = set()
        for chunk_id, chunk_text in rows:
            chunk_id_value = str(chunk_id or "").strip()
            chunk_text_value = str(chunk_text or "").strip()
            if not chunk_id_value or not chunk_text_value or chunk_id_value in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id_value)
            normalized_rows.append((chunk_id_value, chunk_text_value))

        if not normalized_rows:
            return ChunkEmbeddingIndexResult(enabled=self.is_enabled(), total=0)
        if not self.is_enabled():
            return ChunkEmbeddingIndexResult(enabled=False, total=len(normalized_rows))

        existing_rows = self.session.execute(
            select(ChunkEmbedding.chunk_id).where(
                ChunkEmbedding.chunk_id.in_([chunk_id for chunk_id, _ in normalized_rows]),
                ChunkEmbedding.model_provider == self.model_client.provider_name,
                ChunkEmbedding.model_name == self.model_client.model_name,
                ChunkEmbedding.dimensions == self.model_client.dimensions,
            )
        ).all()
        existing_chunk_ids = {str(chunk_id) for chunk_id, in existing_rows}
        missing_rows = [(chunk_id, text) for chunk_id, text in normalized_rows if chunk_id not in existing_chunk_ids]
        if not missing_rows:
            return ChunkEmbeddingIndexResult(
                enabled=True,
                created=0,
                cached=len(existing_chunk_ids),
                total=len(normalized_rows),
            )

        try:
            vectors = self.model_client.embed_texts([text for _chunk_id, text in missing_rows])
        except Exception as exc:  # noqa: BLE001
            return ChunkEmbeddingIndexResult(
                enabled=True,
                created=0,
                cached=len(existing_chunk_ids),
                total=len(normalized_rows),
                error_text=f"{exc.__class__.__name__}:{exc}",
            )

        if len(vectors) != len(missing_rows):
            return ChunkEmbeddingIndexResult(
                enabled=True,
                created=0,
                cached=len(existing_chunk_ids),
                total=len(normalized_rows),
                error_text="embedding vector count mismatch",
            )

        now = datetime.utcnow()
        for (chunk_id, _text), vector in zip(missing_rows, vectors, strict=True):
            self.session.add(
                ChunkEmbedding(
                    chunk_id=chunk_id,
                    model_provider=self.model_client.provider_name,
                    model_name=self.model_client.model_name,
                    dimensions=len(vector),
                    embedding_json=json.dumps(vector, separators=(",", ":")),
                    created_at=now,
                    updated_at=now,
                )
            )
        self.session.flush()
        return ChunkEmbeddingIndexResult(
            enabled=True,
            created=len(missing_rows),
            cached=len(existing_chunk_ids),
            total=len(normalized_rows),
        )


class EmbeddingReranker:
    def __init__(self, embedding_service: ChunkEmbeddingService):
        self.embedding_service = embedding_service
        self.config = embedding_service.config

    def initial_candidate_limit(self, *, top_k: int) -> int:
        return self.embedding_service.initial_candidate_limit(top_k=top_k)

    def rerank_hits(
        self,
        *,
        query: str,
        hits: Sequence[Any],
        top_k: int,
    ) -> tuple[list[Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": self.embedding_service.is_enabled(),
            "created_chunk_embeddings": 0,
            "available_chunk_embeddings": 0,
            "candidate_count": 0,
            "query_embedding_used": False,
        }
        if not hits:
            return list(hits), stats
        if not self.embedding_service.is_enabled():
            return list(hits), stats

        candidate_limit = min(len(hits), self.initial_candidate_limit(top_k=top_k))
        candidate_hits = list(hits[:candidate_limit])
        stats["candidate_count"] = len(candidate_hits)

        query_vector = self.embedding_service.embed_query(query)
        if not query_vector:
            stats["error_text"] = "query_embedding_unavailable"
            return list(hits), stats
        stats["query_embedding_used"] = True

        lookup = self.embedding_service.ensure_embeddings_for_hits(hits=candidate_hits)
        vectors_by_chunk_id = lookup.vectors_by_chunk_id
        stats["created_chunk_embeddings"] = lookup.created
        stats["available_chunk_embeddings"] = len(vectors_by_chunk_id)
        if lookup.error_text:
            stats["error_text"] = lookup.error_text
        if not vectors_by_chunk_id:
            return list(hits), stats

        similarities: list[float] = []
        reranked_hits: list[Any] = []
        weight = float(self.config.rerank_weight)
        for hit in candidate_hits:
            similarity = _clamp_similarity(_cosine_similarity(query_vector, vectors_by_chunk_id.get(str(hit.chunk_id))))
            if similarity > 0.0:
                similarities.append(similarity)
            blended_score = ((1.0 - weight) * float(getattr(hit, "score", 0.0) or 0.0)) + (weight * similarity)
            reranked_hits.append(_copy_hit_with_score(hit, score=round(blended_score, 6)))

        reranked_hits.sort(key=lambda item: float(getattr(item, "score", 0.0) or 0.0), reverse=True)
        if similarities:
            stats["similarity_stats"] = {
                "max": round(max(similarities), 6),
                "min": round(min(similarities), 6),
                "avg": round(sum(similarities) / len(similarities), 6),
            }

        remaining_hits = list(hits[candidate_limit:])
        return [*reranked_hits, *remaining_hits], stats


def _copy_hit_with_score(hit: Any, *, score: float) -> Any:
    try:
        return dataclass_replace(hit, score=score)
    except TypeError:
        setattr(hit, "score", score)
        return hit


def _cosine_similarity(query_vector: list[float], chunk_vector: list[float] | None) -> float:
    if not query_vector or not chunk_vector or len(query_vector) != len(chunk_vector):
        return 0.0
    numerator = sum(left * right for left, right in zip(query_vector, chunk_vector, strict=True))
    left_norm = math.sqrt(sum(value * value for value in query_vector))
    right_norm = math.sqrt(sum(value * value for value in chunk_vector))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _clamp_similarity(value: float) -> float:
    return max(0.0, min(1.0, float(value or 0.0)))


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _env_optional_int(value: str | None, *, min_value: int, max_value: int) -> int | None:
    if value is None or not value.strip():
        return None
    parsed = _env_int(value, default=min_value, min_value=min_value, max_value=max_value)
    return parsed


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


def _normalize_embedding_provider(value: str | None) -> str:
    normalized = (value or "openai").strip().casefold()
    if normalized in {"local", "local_hash", "hash", "local-hash"}:
        return "local_hash"
    return "openai"


def _default_dimensions_for_model(model_name: str) -> int:
    normalized = (model_name or "").strip().casefold()
    if normalized.startswith("local-hash"):
        return 512
    if normalized.endswith("3-large"):
        return 3072
    return 1536


def _query_cache_identity(query: str) -> tuple[str, str]:
    normalized = normalize_for_search(str(query or ""))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest, normalized


def _hash_embed_text(text: str, *, dimensions: int) -> list[float]:
    normalized = normalize_for_search(text)
    tokens = [token for token in normalized.split() if token]
    if not tokens:
        return [0.0] * max(1, dimensions)
    vector = [0.0] * max(1, dimensions)
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % len(vector)
        sign = -1.0 if digest[4] % 2 else 1.0
        weight = 1.0 + ((digest[5] % 5) / 10.0)
        vector[index] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0.0:
        return vector
    return [value / norm for value in vector]
