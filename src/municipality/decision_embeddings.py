from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.chunking import normalize_for_search
from municipality.embeddings import ChunkEmbeddingService, EmbeddingConfig, _cosine_similarity
from municipality.models import Decision, DecisionCitation, DecisionEmbedding, DecisionRequestContext, Document, SourceSite


@dataclass(slots=True)
class DecisionMatch:
    decision_id: int
    similarity: float
    document_id: int
    document_title: str
    decision_text: str
    agenda_item: str | None
    subject_topic_he: str | None
    citation_chunk_ids: list[str]


class DecisionEmbeddingService:
    def __init__(self, session: Session, *, embedding_service: ChunkEmbeddingService | None = None, config: EmbeddingConfig | None = None):
        self.session = session
        self.embedding_service = embedding_service or ChunkEmbeddingService(session, config=config)
        self.config = self.embedding_service.config
        self.min_similarity = _env_float(os.getenv("RAG_DECISION_MATCH_MIN_SIMILARITY"), default=0.62, min_value=0.0, max_value=1.0)
        self.top_matches = _env_int(os.getenv("RAG_DECISION_MATCH_TOP_N"), default=3, min_value=1, max_value=12)

    def is_enabled(self) -> bool:
        return self.embedding_service.is_enabled()

    def index_document_decisions(self, *, document_id: int) -> int:
        rows = self.session.execute(
            select(Decision.id, Decision.agenda_item, Decision.decision_text, DecisionRequestContext.subject_topic_he)
            .outerjoin(DecisionRequestContext, DecisionRequestContext.decision_id == Decision.id)
            .where(Decision.source_document_id == document_id, Decision.is_public.is_(True))
        ).all()
        return self._ensure_embeddings(rows)

    def match_decisions(
        self,
        *,
        query: str,
        municipality_slug: str | None = None,
        year: int | None = None,
        topic: str | None = None,
        limit: int | None = None,
    ) -> list[DecisionMatch]:
        if not self.is_enabled():
            return []
        query_vector = self.embedding_service.embed_query(query, query_kind="decision_retrieval")
        if not query_vector:
            return []

        stmt = (
            select(
                Decision.id,
                Decision.agenda_item,
                Decision.decision_text,
                Decision.source_document_id,
                Document.title_he,
                DecisionRequestContext.subject_topic_he,
            )
            .join(Document, Document.id == Decision.source_document_id)
            .join(SourceSite, SourceSite.id == Document.source_site_id)
            .outerjoin(DecisionRequestContext, DecisionRequestContext.decision_id == Decision.id)
            .where(Decision.is_public.is_(True))
            .order_by(Decision.id.desc())
        )
        if municipality_slug:
            stmt = stmt.where(SourceSite.municipality_slug == municipality_slug)
        if year is not None:
            year_text = str(year)
            stmt = stmt.where((Document.title_he.contains(year_text)) | (Document.canonical_url.contains(year_text)))
        if topic:
            stmt = stmt.where(
                (Decision.agenda_item.contains(topic))
                | (Decision.decision_text.contains(topic))
                | (DecisionRequestContext.subject_topic_he.contains(topic))
            )

        decision_rows = self.session.execute(stmt).all()
        if not decision_rows:
            return []

        self._ensure_embeddings(decision_rows)
        vectors_by_id = self._load_vectors([int(row[0]) for row in decision_rows])
        if not vectors_by_id:
            return []

        citations_by_decision = self._load_citation_chunk_ids([int(row[0]) for row in decision_rows])
        matches: list[DecisionMatch] = []
        for decision_id, agenda_item, decision_text, document_id, document_title, subject_topic_he in decision_rows:
            vector = vectors_by_id.get(int(decision_id))
            similarity = _cosine_similarity(query_vector, vector)
            if similarity < self.min_similarity:
                continue
            matches.append(
                DecisionMatch(
                    decision_id=int(decision_id),
                    similarity=round(float(similarity), 6),
                    document_id=int(document_id),
                    document_title=str(document_title or ""),
                    decision_text=str(decision_text or ""),
                    agenda_item=str(agenda_item) if agenda_item else None,
                    subject_topic_he=str(subject_topic_he) if subject_topic_he else None,
                    citation_chunk_ids=citations_by_decision.get(int(decision_id), []),
                )
            )
        matches.sort(key=lambda item: item.similarity, reverse=True)
        max_matches = limit if limit is not None else self.top_matches
        return matches[:max_matches]

    def _ensure_embeddings(self, rows: Sequence[Any]) -> int:
        normalized_rows: list[tuple[int, str]] = []
        seen_ids: set[int] = set()
        for row in rows:
            decision_id = int(row[0])
            if decision_id in seen_ids:
                continue
            seen_ids.add(decision_id)
            agenda_item = str(row[1] or "").strip()
            decision_text = str(row[2] or "").strip()
            subject_topic_he = str(row[5] or "").strip() if len(row) > 5 else ""
            composite = "\n".join(part for part in [subject_topic_he, agenda_item, decision_text] if part)
            if composite:
                normalized_rows.append((decision_id, composite))
        if not normalized_rows or not self.is_enabled():
            return 0
        existing = self.session.execute(
            select(DecisionEmbedding.decision_id).where(
                DecisionEmbedding.decision_id.in_([decision_id for decision_id, _ in normalized_rows]),
                DecisionEmbedding.model_provider == self.embedding_service.model_client.provider_name,
                DecisionEmbedding.model_name == self.embedding_service.model_client.model_name,
                DecisionEmbedding.dimensions == self.embedding_service.model_client.dimensions,
            )
        ).all()
        existing_ids = {int(decision_id) for decision_id, in existing}
        missing_rows = [(decision_id, text) for decision_id, text in normalized_rows if decision_id not in existing_ids]
        if not missing_rows:
            return 0
        vectors = self.embedding_service.model_client.embed_texts([text for _decision_id, text in missing_rows])
        if len(vectors) != len(missing_rows):
            return 0
        now = datetime.utcnow()
        for (decision_id, _text), vector in zip(missing_rows, vectors, strict=True):
            self.session.add(
                DecisionEmbedding(
                    decision_id=decision_id,
                    model_provider=self.embedding_service.model_client.provider_name,
                    model_name=self.embedding_service.model_client.model_name,
                    dimensions=len(vector),
                    embedding_json=json.dumps(vector, separators=(",", ":")),
                    created_at=now,
                    updated_at=now,
                )
            )
        self.session.flush()
        return len(missing_rows)

    def _load_vectors(self, decision_ids: Sequence[int]) -> dict[int, list[float]]:
        if not decision_ids:
            return {}
        rows = self.session.execute(
            select(DecisionEmbedding).where(
                DecisionEmbedding.decision_id.in_(list(decision_ids)),
                DecisionEmbedding.model_provider == self.embedding_service.model_client.provider_name,
                DecisionEmbedding.model_name == self.embedding_service.model_client.model_name,
                DecisionEmbedding.dimensions == self.embedding_service.model_client.dimensions,
            )
        ).scalars().all()
        out: dict[int, list[float]] = {}
        for row in rows:
            try:
                parsed = json.loads(row.embedding_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, list) or not parsed:
                continue
            out[int(row.decision_id)] = [float(value) for value in parsed]
        return out

    def _load_citation_chunk_ids(self, decision_ids: Sequence[int]) -> dict[int, list[str]]:
        if not decision_ids:
            return {}
        rows = self.session.execute(
            select(DecisionCitation.decision_id, DecisionCitation.document_id, DecisionCitation.start_offset, DecisionCitation.end_offset)
            .where(DecisionCitation.decision_id.in_(list(decision_ids)))
            .order_by(DecisionCitation.decision_id.asc(), DecisionCitation.start_offset.asc())
        ).all()
        out: dict[int, list[str]] = {}
        if not rows:
            return out
        from municipality.models import TextChunk

        for decision_id, document_id, start_offset, end_offset in rows:
            chunk_rows = self.session.execute(
                select(TextChunk.chunk_id)
                .where(
                    TextChunk.document_id == int(document_id),
                    TextChunk.source_kind == "protocol",
                    TextChunk.start_offset < int(end_offset),
                    int(start_offset) < TextChunk.end_offset,
                )
                .order_by(TextChunk.chunk_index.asc())
            ).scalars().all()
            bucket = out.setdefault(int(decision_id), [])
            for chunk_id in chunk_rows:
                chunk_id_value = str(chunk_id)
                if chunk_id_value not in bucket:
                    bucket.append(chunk_id_value)
        return out


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
