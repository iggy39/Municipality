from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from sqlalchemy import and_, select, text
from sqlalchemy.orm import Session

from municipality.chunking import build_trigrams, normalize_for_search
from municipality.models import (
    ArtifactSemanticLink,
    AssetManifest,
    Document,
    RetrievalArtifact,
    SemanticAlias,
    SemanticNode,
    SourceSite,
)
from municipality.search_common import (
    FTS_TOKEN_LIMIT,
    LEXICAL_FTS_WEIGHT,
    LEXICAL_TRIGRAM_WEIGHT,
    SEARCH_FALLBACK_CONTAINS_LIMIT,
    SEARCH_FTS_CANDIDATE_LIMIT,
    SEARCH_TRIGRAM_CANDIDATE_LIMIT,
    SEMANTIC_OVERLAP_WEIGHT,
    SEMANTIC_SPECIFICITY_WEIGHT,
    SearchHit,
    SemanticDebugNode,
    _build_snippet,
    _clamp,
    _descendant_node_ids,
    _fts_rank_to_score,
    _semantic_path_labels,
    _semantic_text_match_score,
)


@dataclass(slots=True)
class _ArtifactSemanticMatchScore:
    node_id: int
    label_he: str
    node_kind: str
    semantic_type: str
    link_confidence: float
    node_confidence: float
    specificity_score: float
    match_score: float


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
            self.session.query(ArtifactSemanticLink).filter(ArtifactSemanticLink.artifact_id.in_(existing_ids)).delete(
                synchronize_session=False
            )
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
        normalized_query = normalize_for_search(query)
        if not normalized_query:
            return []

        normalized_semantic_label = normalize_for_search(semantic_label) if semantic_label else None
        semantic_mode_normalized = (semantic_mode or "off").strip().casefold()
        if semantic_mode_normalized not in {"boost", "filter", "off"}:
            semantic_mode_normalized = "off"
        semantic_scoring_enabled = semantic_mode_normalized != "off"
        explicit_semantic_filter = semantic_scoring_enabled and (
            semantic_node_id is not None or bool(normalized_semantic_label)
        )

        candidate_scores = self._collect_candidate_scores(normalized_query)
        if not candidate_scores:
            return []

        artifact_ids = list(candidate_scores.keys())
        semantic_by_artifact: dict[str, list[Any]] = {}
        if semantic_scoring_enabled:
            semantic_by_artifact = self._collect_semantic_scores(
                artifact_ids=artifact_ids,
                normalized_query=normalized_query,
                semantic_node_id=semantic_node_id,
                normalized_semantic_label=normalized_semantic_label,
                explicit_semantic_filter=explicit_semantic_filter,
            )

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
                | (AssetManifest.source_node_external_id.contains(topic))
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
            semantic_rows = semantic_by_artifact.get(str(artifact.artifact_id), [])
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
                    chunk_id=str(artifact.artifact_id),
                    score=round(score, 6),
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
                    semantic_match_count=semantic_match_count,
                    semantic_node_ids=[row.node_id for row in semantic_rows],
                    semantic_boost=round(semantic_boost, 6),
                    semantic_nodes=semantic_nodes,
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

    def _collect_semantic_scores(
        self,
        *,
        artifact_ids: list[str],
        normalized_query: str,
        semantic_node_id: int | None,
        normalized_semantic_label: str | None,
        explicit_semantic_filter: bool,
    ) -> dict[str, list[Any]]:
        if not artifact_ids:
            return {}

        rows = self.session.execute(
            select(ArtifactSemanticLink, SemanticNode)
            .join(SemanticNode, ArtifactSemanticLink.semantic_node_id == SemanticNode.id)
            .where(ArtifactSemanticLink.artifact_id.in_(artifact_ids))
        ).all()
        if not rows:
            return {}

        nodes_by_id: dict[int, SemanticNode] = {int(node.id): node for _link, node in rows}
        pending_parent_ids = {
            int(node.parent_node_id)
            for node in nodes_by_id.values()
            if node.parent_node_id is not None and int(node.parent_node_id) not in nodes_by_id
        }
        while pending_parent_ids:
            ancestor_rows = self.session.execute(
                select(SemanticNode).where(SemanticNode.id.in_(sorted(pending_parent_ids)))
            ).scalars().all()
            next_parent_ids: set[int] = set()
            for ancestor in ancestor_rows:
                ancestor_id = int(ancestor.id)
                if ancestor_id in nodes_by_id:
                    continue
                nodes_by_id[ancestor_id] = ancestor
                if ancestor.parent_node_id is not None and int(ancestor.parent_node_id) not in nodes_by_id:
                    next_parent_ids.add(int(ancestor.parent_node_id))
            pending_parent_ids = next_parent_ids

        node_ids = sorted(nodes_by_id)
        alias_rows = self.session.execute(
            select(SemanticAlias.semantic_node_id, SemanticAlias.alias_label_norm).where(
                SemanticAlias.semantic_node_id.in_(node_ids)
            )
        ).all()
        aliases_by_node: dict[int, list[str]] = {}
        for alias_node_id, alias_label_norm in alias_rows:
            aliases_by_node.setdefault(int(alias_node_id), []).append(str(alias_label_norm or "").strip())

        children_by_parent: dict[int, list[int]] = {}
        for node in nodes_by_id.values():
            if node.parent_node_id is None:
                continue
            children_by_parent.setdefault(int(node.parent_node_id), []).append(int(node.id))

        explicit_root_ids: set[int] = set()
        if semantic_node_id is not None and int(semantic_node_id) in nodes_by_id:
            explicit_root_ids.add(int(semantic_node_id))
        if normalized_semantic_label:
            for node_id, node in nodes_by_id.items():
                selector_labels = [str(node.pref_label_norm or "").strip()]
                selector_labels.extend(str(label or "").strip() for label in aliases_by_node.get(node_id, []))
                selector_labels.extend(_semantic_path_labels(node=node, nodes_by_id=nodes_by_id))
                if _semantic_text_match_score(normalized_semantic_label, selector_labels) > 0.0:
                    explicit_root_ids.add(node_id)
        explicit_node_ids = _descendant_node_ids(root_ids=explicit_root_ids, children_by_parent=children_by_parent)

        scored_by_artifact: dict[str, dict[int, Any]] = {}
        for link, node in rows:
            labels = [str(node.pref_label_norm or "").strip()]
            labels.extend(str(label or "").strip() for label in aliases_by_node.get(int(node.id), []))
            labels.extend(_semantic_path_labels(node=node, nodes_by_id=nodes_by_id))

            query_score = _semantic_text_match_score(normalized_query, labels)
            explicit_score = 0.0
            if int(node.id) in explicit_node_ids:
                explicit_score = 1.0

            match_score = explicit_score if explicit_semantic_filter else query_score
            if match_score <= 0.0:
                continue

            candidate_row = _ArtifactSemanticMatchScore(
                node_id=int(node.id),
                label_he=str(node.pref_label_he),
                node_kind=str(node.node_kind),
                semantic_type=str(node.semantic_type),
                link_confidence=_clamp(link.confidence),
                node_confidence=_clamp(node.confidence),
                specificity_score=_clamp(node.specificity_score),
                match_score=_clamp(match_score),
            )

            artifact_id = str(link.artifact_id)
            bucket = scored_by_artifact.setdefault(artifact_id, {})
            existing = bucket.get(int(node.id))
            if existing is None:
                bucket[int(node.id)] = candidate_row
                continue

            existing_signal = existing.link_confidence * existing.match_score
            candidate_signal = candidate_row.link_confidence * candidate_row.match_score
            if candidate_signal > existing_signal:
                bucket[int(node.id)] = candidate_row

        return {
            artifact_id: sorted(
                node_rows.values(),
                key=lambda row: (
                    row.link_confidence * row.match_score,
                    row.specificity_score,
                ),
                reverse=True,
            )
            for artifact_id, node_rows in scored_by_artifact.items()
        }
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
