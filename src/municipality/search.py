from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, select, text
from sqlalchemy.orm import Session

from municipality.chunking import build_trigrams, normalize_for_search
from municipality.models import (
    AssetManifest,
    ChunkEmbedding,
    ChunkSemanticLink,
    Document,
    SemanticAlias,
    SemanticNode,
    SourceSite,
    TextChunk,
)


SEMANTIC_SUBSTRING_MATCH_SCORE = 0.92
SEMANTIC_TOKEN_OVERLAP_MIN = 0.6
SEMANTIC_TOKEN_OVERLAP_BASE = 0.55
SEMANTIC_TOKEN_OVERLAP_SCALE = 0.35

LEXICAL_FTS_WEIGHT = 0.72
LEXICAL_TRIGRAM_WEIGHT = 0.28
SEMANTIC_OVERLAP_WEIGHT = 0.25
SEMANTIC_SPECIFICITY_WEIGHT = 0.10

FTS_TOKEN_LIMIT = 8
SEARCH_FTS_CANDIDATE_LIMIT = 250
SEARCH_TRIGRAM_CANDIDATE_LIMIT = 250
SEARCH_FALLBACK_CONTAINS_LIMIT = 100


@dataclass(slots=True)
class SemanticDebugNode:
    id: int
    label: str
    kind: str
    semantic_type: str
    confidence: float


@dataclass(slots=True)
class _SemanticMatchScore:
    node_id: int
    label_he: str
    node_kind: str
    semantic_type: str
    link_confidence: float
    node_confidence: float
    specificity_score: float
    match_score: float


def _semantic_text_match_score(query_norm: str, labels: list[str]) -> float:
    if not query_norm:
        return 0.0

    best = 0.0
    query_tokens = [token for token in query_norm.split(" ") if token]
    for label in labels:
        if not label:
            continue
        if label == query_norm:
            return 1.0
        if query_norm in label or label in query_norm:
            best = max(best, SEMANTIC_SUBSTRING_MATCH_SCORE)
            continue
        label_tokens = [token for token in label.split(" ") if token]
        overlap = _token_overlap(query_tokens, label_tokens)
        if overlap >= SEMANTIC_TOKEN_OVERLAP_MIN:
            best = max(best, SEMANTIC_TOKEN_OVERLAP_BASE + (SEMANTIC_TOKEN_OVERLAP_SCALE * overlap))
    return min(1.0, best)


def search_thresholds_snapshot() -> dict[str, Any]:
    return {
        "semantic_text_match": {
            "substring_match_score": SEMANTIC_SUBSTRING_MATCH_SCORE,
            "token_overlap_min": SEMANTIC_TOKEN_OVERLAP_MIN,
            "token_overlap_base": SEMANTIC_TOKEN_OVERLAP_BASE,
            "token_overlap_scale": SEMANTIC_TOKEN_OVERLAP_SCALE,
        },
        "score_weights": {
            "lexical_fts_weight": LEXICAL_FTS_WEIGHT,
            "lexical_trigram_weight": LEXICAL_TRIGRAM_WEIGHT,
            "semantic_overlap_weight": SEMANTIC_OVERLAP_WEIGHT,
            "semantic_specificity_weight": SEMANTIC_SPECIFICITY_WEIGHT,
        },
        "candidate_limits": {
            "fts_token_limit": FTS_TOKEN_LIMIT,
            "fts_candidate_limit": SEARCH_FTS_CANDIDATE_LIMIT,
            "trigram_candidate_limit": SEARCH_TRIGRAM_CANDIDATE_LIMIT,
            "fallback_contains_limit": SEARCH_FALLBACK_CONTAINS_LIMIT,
        },
        "semantic_modes": ["off", "boost", "filter"],
        "semantic_filter_rule": "filter mode with explicit semantic selector drops chunks with semantic_match_count == 0",
    }


def _token_overlap(left: list[str], right: list[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set.intersection(right_set)) / max(len(left_set), len(right_set), 1)


def _clamp(value: float | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value)))


@dataclass(slots=True)
class SearchHit:
    chunk_id: str
    score: float
    snippet: str
    citation: str | None
    source_type: str
    document_id: int
    document_url: str
    document_title: str
    municipality_slug: str
    meeting_external_id: str | None
    start_offset: int | None
    end_offset: int | None
    start_page: int | None
    end_page: int | None
    semantic_match_count: int = 0
    semantic_node_ids: list[int] = field(default_factory=list)
    semantic_boost: float = 0.0
    semantic_nodes: list[SemanticDebugNode] = field(default_factory=list)
    chunk_text: str = ""


class SearchService:
    def __init__(self, session: Session):
        self.session = session

    def replace_document_chunks(
        self,
        *,
        document_id: int,
        document_version_id: int,
        extracted_document_id: int,
        source_kind: str,
        chunks: list[dict],
    ) -> None:
        existing_chunk_ids: list[str] = list(
            self.session.execute(
                select(TextChunk.chunk_id).where(TextChunk.document_version_id == document_version_id)
            ).scalars().all()
        )
        if existing_chunk_ids:
            self.session.query(TextChunk).filter(TextChunk.document_version_id == document_version_id).delete()
            self.session.query(ChunkEmbedding).filter(ChunkEmbedding.chunk_id.in_(existing_chunk_ids)).delete(
                synchronize_session=False
            )
            self._delete_fts(existing_chunk_ids)
            self._delete_trigrams(existing_chunk_ids)

        for chunk in chunks:
            row = TextChunk(
                chunk_id=chunk["chunk_id"],
                document_id=document_id,
                document_version_id=document_version_id,
                extracted_document_id=extracted_document_id,
                source_kind=source_kind,
                chunk_index=chunk["chunk_index"],
                chunk_text=chunk["chunk_text"],
                chunk_text_norm=chunk["chunk_text_norm"],
                start_offset=chunk["start_offset"],
                end_offset=chunk["end_offset"],
                start_page=chunk["start_page"],
                end_page=chunk["end_page"],
                citation_label=chunk["citation_label"],
                trigram_count=chunk["trigram_count"],
            )
            self.session.add(row)
            self.session.execute(
                text("INSERT INTO chunk_fts (chunk_id, chunk_text) VALUES (:chunk_id, :chunk_text)"),
                {"chunk_id": chunk["chunk_id"], "chunk_text": chunk["chunk_text"]},
            )
            trigram_rows = [{"chunk_id": chunk["chunk_id"], "trigram": tri} for tri in chunk["trigrams"]]
            if trigram_rows:
                self.session.execute(
                    text("INSERT INTO chunk_trigram (chunk_id, trigram) VALUES (:chunk_id, :trigram)"),
                    trigram_rows,
                )

    def search(
        self,
        *,
        query: str,
        municipality_slug: str | None = None,
        source_type: str | None = None,
        year: int | None = None,
        topic: str | None = None,
        semantic_node_id: int | None = None,
        semantic_label: str | None = None,
        semantic_mode: str = "boost",
        limit: int = 20,
    ) -> list[SearchHit]:
        normalized_query = normalize_for_search(query)
        if not normalized_query:
            return []

        normalized_semantic_label = normalize_for_search(semantic_label) if semantic_label else None
        semantic_mode_normalized = (semantic_mode or "boost").strip().casefold()
        if semantic_mode_normalized not in {"boost", "filter", "off"}:
            semantic_mode_normalized = "boost"
        semantic_scoring_enabled = semantic_mode_normalized != "off"
        explicit_semantic_filter = semantic_scoring_enabled and (
            semantic_node_id is not None or bool(normalized_semantic_label)
        )

        candidate_scores = self._collect_candidate_scores(normalized_query)
        if not candidate_scores:
            return []

        chunk_ids = list(candidate_scores.keys())
        semantic_by_chunk: dict[str, list[_SemanticMatchScore]] = {}
        semantic_by_chunk = self._collect_semantic_scores(
            chunk_ids=chunk_ids,
            normalized_query=normalized_query,
            semantic_node_id=semantic_node_id,
            normalized_semantic_label=normalized_semantic_label,
            explicit_semantic_filter=explicit_semantic_filter,
        )
        stmt = (
            select(TextChunk, Document, SourceSite, AssetManifest)
            .join(Document, TextChunk.document_id == Document.id)
            .join(SourceSite, Document.source_site_id == SourceSite.id)
            .outerjoin(
                AssetManifest,
                and_(
                    AssetManifest.source_site_id == Document.source_site_id,
                    AssetManifest.asset_external_id == Document.document_external_id,
                ),
            )
            .where(TextChunk.chunk_id.in_(chunk_ids))
        )

        if municipality_slug:
            stmt = stmt.where(SourceSite.municipality_slug == municipality_slug)
        if source_type:
            stmt = stmt.where(TextChunk.source_kind == source_type)
        if year:
            year_text = str(year)
            stmt = stmt.where(
                (Document.title_he.contains(year_text))
                | (Document.canonical_url.contains(year_text))
            )
        if topic:
            stmt = stmt.where(
                (Document.title_he.contains(topic))
                | (Document.canonical_url.contains(topic))
                | (AssetManifest.source_node_external_id.contains(topic))
            )

        rows = self.session.execute(stmt).all()
        query_trigrams = build_trigrams(normalized_query)
        query_trigram_count = max(1, len(query_trigrams))

        hits: list[SearchHit] = []
        for chunk, document, source_site, manifest in rows:
            scores = candidate_scores.get(chunk.chunk_id, {})
            fts_rank = scores.get("fts_rank")
            trigram_overlap = scores.get("trigram_overlap", 0)
            fts_score = _fts_rank_to_score(fts_rank)
            trigram_score = min(1.0, trigram_overlap / max(query_trigram_count, chunk.trigram_count, 1))
            lexical_score = (LEXICAL_FTS_WEIGHT * fts_score) + (LEXICAL_TRIGRAM_WEIGHT * trigram_score)

            semantic_rows = semantic_by_chunk.get(chunk.chunk_id, [])
            semantic_match_count = len(semantic_rows)
            if semantic_mode_normalized == "filter" and explicit_semantic_filter and semantic_match_count == 0:
                continue

            semantic_boost = 0.0
            score = lexical_score
            if semantic_scoring_enabled:
                semantic_overlap_score = max(
                    (row.link_confidence * row.match_score for row in semantic_rows),
                    default=0.0,
                )
                specificity_prior = max((row.specificity_score for row in semantic_rows), default=0.0)
                semantic_boost = (SEMANTIC_OVERLAP_WEIGHT * semantic_overlap_score) + (
                    SEMANTIC_SPECIFICITY_WEIGHT * specificity_prior
                )
                score = min(1.0, lexical_score + semantic_boost)

            semantic_nodes = [
                SemanticDebugNode(
                    id=row.node_id,
                    label=row.label_he,
                    kind=row.node_kind,
                    semantic_type=row.semantic_type,
                    confidence=row.link_confidence,
                )
                for row in semantic_rows
            ]
            hits.append(
                SearchHit(
                    chunk_id=chunk.chunk_id,
                    score=round(score, 6),
                    snippet=_build_snippet(chunk.chunk_text, query),
                    citation=chunk.citation_label,
                    source_type=chunk.source_kind,
                    document_id=document.id,
                    document_url=document.canonical_url,
                    document_title=document.title_he,
                    municipality_slug=source_site.municipality_slug,
                    meeting_external_id=manifest.source_node_external_id if manifest else None,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                    start_page=chunk.start_page,
                    end_page=chunk.end_page,
                    semantic_match_count=semantic_match_count,
                    semantic_node_ids=[row.node_id for row in semantic_rows],
                    semantic_boost=round(semantic_boost, 6),
                    semantic_nodes=semantic_nodes,
                    chunk_text=chunk.chunk_text,
                )
            )

        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    def hydrate_hits_by_chunk_ids(self, *, chunk_ids: list[str], score_by_chunk_id: dict[str, float] | None = None) -> list[SearchHit]:
        normalized_ids = [str(chunk_id or "").strip() for chunk_id in chunk_ids if str(chunk_id or "").strip()]
        if not normalized_ids:
            return []
        stmt = (
            select(TextChunk, Document, SourceSite, AssetManifest)
            .join(Document, TextChunk.document_id == Document.id)
            .join(SourceSite, Document.source_site_id == SourceSite.id)
            .outerjoin(
                AssetManifest,
                and_(
                    AssetManifest.source_site_id == Document.source_site_id,
                    AssetManifest.asset_external_id == Document.document_external_id,
                ),
            )
            .where(TextChunk.chunk_id.in_(normalized_ids))
        )
        rows = self.session.execute(stmt).all()
        by_id: dict[str, SearchHit] = {}
        for chunk, document, source_site, manifest in rows:
            chunk_id = str(chunk.chunk_id)
            by_id[chunk_id] = SearchHit(
                chunk_id=chunk_id,
                score=round(float((score_by_chunk_id or {}).get(chunk_id, 0.0)), 6),
                snippet=_build_snippet(chunk.chunk_text, chunk.chunk_text[:80]),
                citation=chunk.citation_label,
                source_type=chunk.source_kind,
                document_id=document.id,
                document_url=document.canonical_url,
                document_title=document.title_he,
                municipality_slug=source_site.municipality_slug,
                meeting_external_id=manifest.source_node_external_id if manifest else None,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                start_page=chunk.start_page,
                end_page=chunk.end_page,
                chunk_text=chunk.chunk_text,
            )
        return [by_id[chunk_id] for chunk_id in normalized_ids if chunk_id in by_id]

    def _collect_candidate_scores(self, normalized_query: str) -> dict[str, dict[str, float]]:
        candidate_scores: dict[str, dict[str, float]] = {}

        tokens = [token for token in normalized_query.split(" ") if token]
        fts_query = " OR ".join(tokens[:FTS_TOKEN_LIMIT])
        if fts_query:
            try:
                rows = self.session.execute(
                    text(
                        "SELECT chunk_id, bm25(chunk_fts) AS rank FROM chunk_fts "
                        f"WHERE chunk_fts MATCH :q LIMIT {SEARCH_FTS_CANDIDATE_LIMIT}"
                    ),
                    {"q": fts_query},
                ).mappings().all()
                for row in rows:
                    score = candidate_scores.setdefault(row["chunk_id"], {})
                    score["fts_rank"] = float(row["rank"])
            except Exception:
                pass

        query_trigrams = sorted(build_trigrams(normalized_query))
        if query_trigrams:
            params = {f"t{idx}": tri for idx, tri in enumerate(query_trigrams)}
            placeholders = ",".join(f":t{idx}" for idx in range(len(query_trigrams)))
            rows = self.session.execute(
                text(
                    "SELECT chunk_id, COUNT(*) AS overlap FROM chunk_trigram "
                    f"WHERE trigram IN ({placeholders}) "
                    f"GROUP BY chunk_id ORDER BY overlap DESC LIMIT {SEARCH_TRIGRAM_CANDIDATE_LIMIT}"
                ),
                params,
            ).mappings().all()
            for row in rows:
                score = candidate_scores.setdefault(row["chunk_id"], {})
                score["trigram_overlap"] = float(row["overlap"])

        if candidate_scores:
            return candidate_scores

        fallback_rows = self.session.execute(
            select(TextChunk.chunk_id)
            .where(TextChunk.chunk_text_norm.contains(normalized_query))
            .limit(SEARCH_FALLBACK_CONTAINS_LIMIT)
        ).scalars().all()
        for chunk_id in fallback_rows:
            candidate_scores[chunk_id] = {"fts_rank": 1.0, "trigram_overlap": 0.0}
        return candidate_scores

    def _collect_semantic_scores(
        self,
        *,
        chunk_ids: list[str],
        normalized_query: str,
        semantic_node_id: int | None,
        normalized_semantic_label: str | None,
        explicit_semantic_filter: bool,
    ) -> dict[str, list[_SemanticMatchScore]]:
        if not chunk_ids:
            return {}

        rows = self.session.execute(
            select(ChunkSemanticLink, SemanticNode)
            .join(SemanticNode, ChunkSemanticLink.semantic_node_id == SemanticNode.id)
            .where(ChunkSemanticLink.chunk_id.in_(chunk_ids))
        ).all()
        if not rows:
            return {}

        node_ids = sorted({node.id for _link, node in rows})
        alias_rows = self.session.execute(
            select(SemanticAlias.semantic_node_id, SemanticAlias.alias_label_norm).where(
                SemanticAlias.semantic_node_id.in_(node_ids)
            )
        ).all()
        aliases_by_node: dict[int, list[str]] = {}
        for alias_node_id, alias_label_norm in alias_rows:
            aliases_by_node.setdefault(alias_node_id, []).append(alias_label_norm)

        scored_by_chunk: dict[str, dict[int, _SemanticMatchScore]] = {}
        for link, node in rows:
            labels = [node.pref_label_norm]
            labels.extend(aliases_by_node.get(node.id, []))

            query_score = _semantic_text_match_score(normalized_query, labels)
            explicit_score = 0.0
            if semantic_node_id is not None and node.id == semantic_node_id:
                explicit_score = 1.0
            if normalized_semantic_label:
                explicit_score = max(explicit_score, _semantic_text_match_score(normalized_semantic_label, labels))

            match_score = explicit_score if explicit_semantic_filter else query_score
            if match_score <= 0.0:
                continue

            candidate_row = _SemanticMatchScore(
                node_id=node.id,
                label_he=node.pref_label_he,
                node_kind=node.node_kind,
                semantic_type=node.semantic_type,
                link_confidence=_clamp(link.confidence),
                node_confidence=_clamp(node.confidence),
                specificity_score=_clamp(node.specificity_score),
                match_score=_clamp(match_score),
            )

            bucket = scored_by_chunk.setdefault(link.chunk_id, {})
            existing = bucket.get(node.id)
            if existing is None:
                bucket[node.id] = candidate_row
                continue

            existing_signal = existing.link_confidence * existing.match_score
            candidate_signal = candidate_row.link_confidence * candidate_row.match_score
            if candidate_signal > existing_signal:
                bucket[node.id] = candidate_row

        return {
            chunk_id: sorted(
                node_rows.values(),
                key=lambda row: (
                    row.link_confidence * row.match_score,
                    row.specificity_score,
                ),
                reverse=True,
            )
            for chunk_id, node_rows in scored_by_chunk.items()
        }

    def _delete_fts(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        params = {f"c{idx}": chunk_id for idx, chunk_id in enumerate(chunk_ids)}
        placeholders = ",".join(f":c{idx}" for idx in range(len(chunk_ids)))
        self.session.execute(
            text(f"DELETE FROM chunk_fts WHERE chunk_id IN ({placeholders})"),
            params,
        )

    def _delete_trigrams(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        params = {f"c{idx}": chunk_id for idx, chunk_id in enumerate(chunk_ids)}
        placeholders = ",".join(f":c{idx}" for idx in range(len(chunk_ids)))
        self.session.execute(
            text(f"DELETE FROM chunk_trigram WHERE chunk_id IN ({placeholders})"),
            params,
        )


def _fts_rank_to_score(raw_rank: float | None) -> float:
    if raw_rank is None:
        return 0.0
    clipped = max(0.0, raw_rank)
    return 1.0 / (1.0 + clipped)


def _build_snippet(text_value: str, query: str, window: int = 220) -> str:
    if not text_value:
        return ""
    normalized_query = query.strip()
    if not normalized_query:
        snippet = text_value[:window]
        return snippet if len(text_value) <= window else f"{snippet}..."

    idx = text_value.casefold().find(normalized_query.casefold())
    if idx < 0:
        snippet = text_value[:window]
        return snippet if len(text_value) <= window else f"{snippet}..."

    start = max(0, idx - (window // 3))
    end = min(len(text_value), idx + len(normalized_query) + (window // 2))
    snippet = text_value[start:end].strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(text_value):
        snippet = f"{snippet}..."
    return snippet
