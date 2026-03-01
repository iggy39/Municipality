from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, select, text
from sqlalchemy.orm import Session

from municipality.chunking import build_trigrams, normalize_for_search
from municipality.models import AssetManifest, Document, SourceSite, TextChunk


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
    start_page: int | None
    end_page: int | None


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
        existing_chunk_ids = self.session.execute(
            select(TextChunk.chunk_id).where(TextChunk.document_version_id == document_version_id)
        ).scalars().all()
        if existing_chunk_ids:
            self.session.query(TextChunk).filter(TextChunk.document_version_id == document_version_id).delete()
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
        limit: int = 20,
    ) -> list[SearchHit]:
        normalized_query = normalize_for_search(query)
        if not normalized_query:
            return []

        candidate_scores = self._collect_candidate_scores(normalized_query)
        if not candidate_scores:
            return []

        chunk_ids = list(candidate_scores.keys())
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
            score = (0.72 * fts_score) + (0.28 * trigram_score)
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
                    start_page=chunk.start_page,
                    end_page=chunk.end_page,
                )
            )

        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    def _collect_candidate_scores(self, normalized_query: str) -> dict[str, dict[str, float]]:
        candidate_scores: dict[str, dict[str, float]] = {}

        tokens = [token for token in normalized_query.split(" ") if token]
        fts_query = " OR ".join(tokens[:8])
        if fts_query:
            try:
                rows = self.session.execute(
                    text(
                        "SELECT chunk_id, bm25(chunk_fts) AS rank FROM chunk_fts "
                        "WHERE chunk_fts MATCH :q LIMIT 250"
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
                    "GROUP BY chunk_id ORDER BY overlap DESC LIMIT 250"
                ),
                params,
            ).mappings().all()
            for row in rows:
                score = candidate_scores.setdefault(row["chunk_id"], {})
                score["trigram_overlap"] = float(row["overlap"])

        if candidate_scores:
            return candidate_scores

        fallback_rows = self.session.execute(
            select(TextChunk.chunk_id).where(TextChunk.chunk_text_norm.contains(normalized_query)).limit(100)
        ).scalars().all()
        for chunk_id in fallback_rows:
            candidate_scores[chunk_id] = {"fts_rank": 1.0, "trigram_overlap": 0.0}
        return candidate_scores

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
