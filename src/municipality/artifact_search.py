from __future__ import annotations

import json
from typing import Any

from sqlalchemy import and_, select, text
from sqlalchemy.orm import Session

from municipality.chunking import build_trigrams, normalize_for_search
from municipality.models import AssetManifest, Document, RetrievalArtifact, SourceSite
from municipality.search import LEXICAL_FTS_WEIGHT, LEXICAL_TRIGRAM_WEIGHT, SearchHit


FTS_TOKEN_LIMIT = 8
SEARCH_FTS_CANDIDATE_LIMIT = 250
SEARCH_TRIGRAM_CANDIDATE_LIMIT = 250
SEARCH_FALLBACK_CONTAINS_LIMIT = 100


class ArtifactSearchService:
    def __init__(self, session: Session):
        self.session = session

    def replace_document_artifacts(
        self,
        *,
        document_id: int,
        document_version_id: int,
        extracted_document_id: int,
        artifacts: list[dict[str, Any]],
    ) -> None:
        existing_ids = list(
            self.session.execute(
                select(RetrievalArtifact.artifact_id).where(RetrievalArtifact.document_version_id == document_version_id)
            ).scalars().all()
        )
        if existing_ids:
            self.session.query(RetrievalArtifact).filter(RetrievalArtifact.document_version_id == document_version_id).delete()
            self.session.execute(text("DELETE FROM artifact_fts WHERE artifact_id = :artifact_id"), [{"artifact_id": artifact_id} for artifact_id in existing_ids])
            self.session.execute(
                text("DELETE FROM retrieval_artifact_trigram WHERE artifact_id = :artifact_id"),
                [{"artifact_id": artifact_id} for artifact_id in existing_ids],
            )

        for artifact in artifacts:
            row = RetrievalArtifact(
                artifact_id=artifact["artifact_id"],
                document_id=document_id,
                document_version_id=document_version_id,
                extracted_document_id=extracted_document_id,
                section_id=artifact.get("section_id"),
                source_kind=artifact["source_kind"],
                artifact_kind=artifact["artifact_kind"],
                ordinal=artifact["ordinal"],
                title_he=artifact.get("title_he"),
                committee_name=artifact.get("committee_name"),
                meeting_date=artifact.get("meeting_date"),
                header_path_json=artifact["header_path_json"],
                body_text=artifact["body_text"],
                retrieval_text=artifact["retrieval_text"],
                retrieval_text_norm=artifact["retrieval_text_norm"],
                start_offset=artifact["start_offset"],
                end_offset=artifact["end_offset"],
                start_page=artifact.get("start_page"),
                end_page=artifact.get("end_page"),
                citation_label=artifact.get("citation_label"),
                trigram_count=artifact["trigram_count"],
                metadata_json=artifact.get("metadata_json"),
            )
            self.session.add(row)
            self.session.execute(
                text("INSERT INTO artifact_fts (artifact_id, retrieval_text) VALUES (:artifact_id, :retrieval_text)"),
                {"artifact_id": artifact["artifact_id"], "retrieval_text": artifact["retrieval_text"]},
            )
            trigram_rows = [
                {"artifact_id": artifact["artifact_id"], "trigram": trigram}
                for trigram in artifact.get("trigrams", [])
            ]
            if trigram_rows:
                self.session.execute(
                    text("INSERT INTO retrieval_artifact_trigram (artifact_id, trigram) VALUES (:artifact_id, :trigram)"),
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
        semantic_mode: str = "off",
        limit: int = 20,
    ) -> list[SearchHit]:
        del semantic_node_id, semantic_label, semantic_mode
        normalized_query = normalize_for_search(query)
        if not normalized_query:
            return []

        candidate_scores = self._collect_candidate_scores(normalized_query)
        if not candidate_scores:
            return []

        artifact_ids = list(candidate_scores.keys())
        stmt = (
            select(RetrievalArtifact, Document, SourceSite, AssetManifest)
            .join(Document, RetrievalArtifact.document_id == Document.id)
            .join(SourceSite, Document.source_site_id == SourceSite.id)
            .outerjoin(
                AssetManifest,
                and_(
                    AssetManifest.source_site_id == Document.source_site_id,
                    AssetManifest.asset_external_id == Document.document_external_id,
                ),
            )
            .where(RetrievalArtifact.artifact_id.in_(artifact_ids))
        )
        if municipality_slug:
            stmt = stmt.where(SourceSite.municipality_slug == municipality_slug)
        if source_type:
            stmt = stmt.where(RetrievalArtifact.source_kind == source_type)
        if year:
            year_text = str(year)
            stmt = stmt.where((Document.title_he.contains(year_text)) | (Document.canonical_url.contains(year_text)))
        if topic:
            stmt = stmt.where(
                (Document.title_he.contains(topic))
                | (Document.canonical_url.contains(topic))
                | (RetrievalArtifact.retrieval_text.contains(topic))
            )

        rows = self.session.execute(stmt).all()
        query_trigrams = build_trigrams(normalized_query)
        query_trigram_count = max(1, len(query_trigrams))
        hits: list[SearchHit] = []
        for artifact, document, source_site, manifest in rows:
            scores = candidate_scores.get(str(artifact.artifact_id), {})
            fts_score = _fts_rank_to_score(scores.get("fts_rank"))
            trigram_overlap = scores.get("trigram_overlap", 0.0)
            trigram_score = min(1.0, trigram_overlap / max(query_trigram_count, int(artifact.trigram_count or 0), 1))
            lexical_score = (LEXICAL_FTS_WEIGHT * fts_score) + (LEXICAL_TRIGRAM_WEIGHT * trigram_score)
            hits.append(
                SearchHit(
                    chunk_id=str(artifact.artifact_id),
                    score=round(lexical_score, 6),
                    snippet=_build_snippet(artifact.retrieval_text, query),
                    citation=artifact.citation_label,
                    source_type=artifact.source_kind,
                    document_id=document.id,
                    document_url=document.canonical_url,
                    document_title=document.title_he,
                    municipality_slug=source_site.municipality_slug,
                    meeting_external_id=manifest.source_node_external_id if manifest else None,
                    start_offset=artifact.start_offset,
                    end_offset=artifact.end_offset,
                    start_page=artifact.start_page,
                    end_page=artifact.end_page,
                    chunk_text=artifact.retrieval_text,
                    section_path=_loads_json_list(artifact.header_path_json),
                    artifact_kind=artifact.artifact_kind,
                )
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    def hydrate_hits_by_chunk_ids(self, *, chunk_ids: list[str], score_by_chunk_id: dict[str, float] | None = None) -> list[SearchHit]:
        normalized_ids = [str(chunk_id or "").strip() for chunk_id in chunk_ids if str(chunk_id or "").strip()]
        if not normalized_ids:
            return []
        stmt = (
            select(RetrievalArtifact, Document, SourceSite, AssetManifest)
            .join(Document, RetrievalArtifact.document_id == Document.id)
            .join(SourceSite, Document.source_site_id == SourceSite.id)
            .outerjoin(
                AssetManifest,
                and_(
                    AssetManifest.source_site_id == Document.source_site_id,
                    AssetManifest.asset_external_id == Document.document_external_id,
                ),
            )
            .where(RetrievalArtifact.artifact_id.in_(normalized_ids))
        )
        rows = self.session.execute(stmt).all()
        by_id: dict[str, SearchHit] = {}
        for artifact, document, source_site, manifest in rows:
            artifact_id = str(artifact.artifact_id)
            by_id[artifact_id] = SearchHit(
                chunk_id=artifact_id,
                score=round(float((score_by_chunk_id or {}).get(artifact_id, 0.0)), 6),
                snippet=_build_snippet(artifact.retrieval_text, artifact.retrieval_text[:80]),
                citation=artifact.citation_label,
                source_type=artifact.source_kind,
                document_id=document.id,
                document_url=document.canonical_url,
                document_title=document.title_he,
                municipality_slug=source_site.municipality_slug,
                meeting_external_id=manifest.source_node_external_id if manifest else None,
                start_offset=artifact.start_offset,
                end_offset=artifact.end_offset,
                start_page=artifact.start_page,
                end_page=artifact.end_page,
                chunk_text=artifact.retrieval_text,
                section_path=_loads_json_list(artifact.header_path_json),
                artifact_kind=artifact.artifact_kind,
            )
        return [by_id[artifact_id] for artifact_id in normalized_ids if artifact_id in by_id]

    def _collect_candidate_scores(self, normalized_query: str) -> dict[str, dict[str, float]]:
        candidate_scores: dict[str, dict[str, float]] = {}
        tokens = [token for token in normalized_query.split(" ") if token]
        fts_query = " OR ".join(tokens[:FTS_TOKEN_LIMIT])
        if fts_query:
            try:
                rows = self.session.execute(
                    text(
                        "SELECT artifact_id, bm25(artifact_fts) AS rank FROM artifact_fts "
                        f"WHERE artifact_fts MATCH :q LIMIT {SEARCH_FTS_CANDIDATE_LIMIT}"
                    ),
                    {"q": fts_query},
                ).mappings().all()
                for row in rows:
                    candidate_scores.setdefault(str(row["artifact_id"]), {})["fts_rank"] = float(row["rank"])
            except Exception:
                pass

        query_trigrams = sorted(build_trigrams(normalized_query))
        if query_trigrams:
            params = {f"t{idx}": trigram for idx, trigram in enumerate(query_trigrams)}
            placeholders = ",".join(f":t{idx}" for idx in range(len(query_trigrams)))
            rows = self.session.execute(
                text(
                    "SELECT artifact_id, COUNT(*) AS overlap FROM retrieval_artifact_trigram "
                    f"WHERE trigram IN ({placeholders}) GROUP BY artifact_id "
                    f"ORDER BY overlap DESC LIMIT {SEARCH_TRIGRAM_CANDIDATE_LIMIT}"
                ),
                params,
            ).mappings().all()
            for row in rows:
                candidate_scores.setdefault(str(row["artifact_id"]), {})["trigram_overlap"] = float(row["overlap"])

        if candidate_scores:
            return candidate_scores

        fallback_rows = self.session.execute(
            select(RetrievalArtifact.artifact_id)
            .where(RetrievalArtifact.retrieval_text_norm.contains(normalized_query))
            .limit(SEARCH_FALLBACK_CONTAINS_LIMIT)
        ).scalars().all()
        for artifact_id in fallback_rows:
            candidate_scores[str(artifact_id)] = {"fts_rank": 1.0, "trigram_overlap": 0.0}
        return candidate_scores


def _fts_rank_to_score(raw_rank: float | None) -> float:
    if raw_rank is None:
        return 0.0
    return max(0.0, min(1.0, 1.0 / (1.0 + abs(float(raw_rank)))))


def _build_snippet(text_value: str, query: str, window: int = 220) -> str:
    text_compact = " ".join(str(text_value or "").split())
    if not text_compact:
        return ""
    query_norm = normalize_for_search(query)
    text_norm = normalize_for_search(text_compact)
    index = text_norm.find(query_norm) if query_norm else -1
    if index < 0:
        return text_compact[:window].strip()
    start = max(0, index - (window // 3))
    end = min(len(text_compact), start + window)
    snippet = text_compact[start:end].strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(text_compact):
        snippet = f"{snippet}..."
    return snippet


def _loads_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]
