from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from sqlalchemy import and_, inspect, select, text
from sqlalchemy.orm import Session

from municipality.chunking import build_trigrams, normalize_for_search
from municipality.embeddings import ChunkEmbeddingService
from municipality.models import (
    ArtifactTopicAnnotation,
    ArtifactSemanticLink,
    AssetManifest,
    Document,
    RetrievalArtifact,
    RetrievalArtifactEmbedding,
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
    TOPIC_TEXT_WEIGHT,
    ARTIFACT_KIND_PRIORITY_WEIGHT,
    EMBEDDING_CANDIDATE_WEIGHT,
    SEARCH_EMBEDDING_CANDIDATE_LIMIT,
    SearchHit,
    SemanticDebugNode,
    _build_snippet,
    _clamp,
    _descendant_node_ids,
    _fts_rank_to_score,
    _semantic_path_labels,
    _semantic_text_match_score,
)
from municipality.topic_label_quality import is_low_quality_topic_label, sanitize_topic_path_label


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
    def __init__(self, session: Session, *, embedding_service: ChunkEmbeddingService | None = None):
        self.session = session
        self.embedding_service = embedding_service or ChunkEmbeddingService(session)

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
            if self._topic_annotations_available():
                self.session.query(ArtifactTopicAnnotation).filter(
                    ArtifactTopicAnnotation.artifact_id.in_(existing_ids)
                ).delete(synchronize_session=False)
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
        artifact_kinds: list[str] | None = None,
        topic_terms: list[str] | None = None,
        document_ids: list[int] | None = None,
        document_version_ids: list[int] | None = None,
        limit: int = 20,
    ) -> list[SearchHit]:
        normalized_query = normalize_for_search(query)
        if not normalized_query:
            return []
        normalized_topic_terms = [normalize_for_search(term) for term in (topic_terms or []) if normalize_for_search(term)]

        normalized_semantic_label = normalize_for_search(semantic_label) if semantic_label else None
        semantic_mode_normalized = (semantic_mode or "off").strip().casefold()
        if semantic_mode_normalized not in {"boost", "filter", "off"}:
            semantic_mode_normalized = "off"
        semantic_scoring_enabled = semantic_mode_normalized != "off"
        explicit_semantic_filter = semantic_scoring_enabled and (
            semantic_node_id is not None or bool(normalized_semantic_label)
        )

        candidate_scores = self._collect_candidate_scores(
            normalized_query,
            normalized_topic_terms=normalized_topic_terms,
            source_type=source_type,
        )
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

        topic_annotations_available = self._topic_annotations_available()
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
        if topic_annotations_available:
            stmt = stmt.add_columns(ArtifactTopicAnnotation).outerjoin(
                ArtifactTopicAnnotation,
                and_(
                    ArtifactTopicAnnotation.artifact_id == RetrievalArtifact.artifact_id,
                    ArtifactTopicAnnotation.classifier_route.contains("dictalm"),
                ),
            )
        if municipality_slug:
            stmt = stmt.where(SourceSite.municipality_slug == municipality_slug)
        if source_type:
            stmt = stmt.where(RetrievalArtifact.source_kind == source_type)
        if year:
            year_text = str(year)
            stmt = stmt.where((Document.title_he.contains(year_text)) | (Document.canonical_url.contains(year_text)))
        if topic:
            if topic_annotations_available:
                stmt = stmt.where(
                    (Document.title_he.contains(topic))
                    | (Document.canonical_url.contains(topic))
                    | (RetrievalArtifact.retrieval_text.contains(topic))
                    | (AssetManifest.source_node_external_id.contains(topic))
                    | (ArtifactTopicAnnotation.primary_topic_he.contains(topic))
                )
            else:
                stmt = stmt.where(
                    (Document.title_he.contains(topic))
                    | (Document.canonical_url.contains(topic))
                    | (RetrievalArtifact.retrieval_text.contains(topic))
                    | (AssetManifest.source_node_external_id.contains(topic))
                )
        if artifact_kinds:
            normalized_kinds = [str(kind).strip() for kind in artifact_kinds if str(kind).strip()]
            if normalized_kinds:
                stmt = stmt.where(RetrievalArtifact.artifact_kind.in_(normalized_kinds))
        if document_ids:
            normalized_document_ids = [int(document_id) for document_id in document_ids if int(document_id) > 0]
            if normalized_document_ids:
                stmt = stmt.where(RetrievalArtifact.document_id.in_(normalized_document_ids))
        if document_version_ids:
            normalized_document_version_ids = [
                int(document_version_id) for document_version_id in document_version_ids if int(document_version_id) > 0
            ]
            if normalized_document_version_ids:
                stmt = stmt.where(RetrievalArtifact.document_version_id.in_(normalized_document_version_ids))

        rows = self.session.execute(stmt).all()
        query_trigrams = build_trigrams(normalized_query)
        query_trigram_count = max(1, len(query_trigrams))
        hits: list[SearchHit] = []
        kind_priority = {kind: index for index, kind in enumerate(artifact_kinds or [])}
        for row in rows:
            if topic_annotations_available:
                artifact, document, source_site, manifest, topic_annotation = row
            else:
                artifact, document, source_site, manifest = row
                topic_annotation = None
            scores = candidate_scores.get(str(artifact.artifact_id), {})
            fts_score = _fts_rank_to_score(scores.get("fts_rank"))
            trigram_overlap = scores.get("trigram_overlap", 0.0)
            trigram_score = min(1.0, trigram_overlap / max(query_trigram_count, int(artifact.trigram_count or 0), 1))
            embedding_similarity = float(scores.get("embedding_similarity") or 0.0)
            lexical_score = (LEXICAL_FTS_WEIGHT * fts_score) + (LEXICAL_TRIGRAM_WEIGHT * trigram_score)
            hybrid_score = min(1.0, lexical_score + (EMBEDDING_CANDIDATE_WEIGHT * embedding_similarity))
            semantic_rows = semantic_by_artifact.get(str(artifact.artifact_id), [])
            semantic_match_count = len(semantic_rows)
            if semantic_mode_normalized == "filter" and explicit_semantic_filter and semantic_match_count == 0:
                continue

            semantic_boost = 0.0
            topic_boost = self._topic_score(
                annotation=topic_annotation,
                normalized_query=normalized_query,
                normalized_topic_terms=normalized_topic_terms,
            )
            kind_boost = 0.0
            if kind_priority and artifact.artifact_kind in kind_priority:
                denominator = max(1, len(kind_priority) - 1)
                kind_boost = ARTIFACT_KIND_PRIORITY_WEIGHT * (1.0 - (kind_priority[artifact.artifact_kind] / max(denominator, 1)))
            score = min(1.0, hybrid_score + topic_boost + kind_boost)
            if semantic_scoring_enabled:
                semantic_overlap_score = max(
                    (row.link_confidence * row.match_score for row in semantic_rows),
                    default=0.0,
                )
                specificity_prior = max((row.specificity_score for row in semantic_rows), default=0.0)
                semantic_boost = (SEMANTIC_OVERLAP_WEIGHT * semantic_overlap_score) + (
                    SEMANTIC_SPECIFICITY_WEIGHT * specificity_prior
                )
                score = min(1.0, score + semantic_boost)

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
                    document_version_id=artifact.document_version_id,
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
                    primary_topic=_annotation_primary_topic(topic_annotation),
                    secondary_topics=_annotation_secondary_topics(topic_annotation),
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
        topic_annotations_available = self._topic_annotations_available()
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
        if topic_annotations_available:
            stmt = stmt.add_columns(ArtifactTopicAnnotation).outerjoin(
                ArtifactTopicAnnotation,
                and_(
                    ArtifactTopicAnnotation.artifact_id == RetrievalArtifact.artifact_id,
                    ArtifactTopicAnnotation.classifier_route.contains("dictalm"),
                ),
            )
        rows = self.session.execute(stmt).all()
        by_id: dict[str, SearchHit] = {}
        for row in rows:
            if topic_annotations_available:
                artifact, document, source_site, manifest, topic_annotation = row
            else:
                artifact, document, source_site, manifest = row
                topic_annotation = None
            artifact_id = str(artifact.artifact_id)
            by_id[artifact_id] = SearchHit(
                chunk_id=artifact_id,
                score=round(float((score_by_chunk_id or {}).get(artifact_id, 0.0)), 6),
                snippet=_build_snippet(artifact.retrieval_text, artifact.retrieval_text[:80]),
                citation=artifact.citation_label,
                source_type=artifact.source_kind,
                document_id=document.id,
                document_version_id=artifact.document_version_id,
                document_url=document.canonical_url,
                document_title=document.title_he,
                municipality_slug=source_site.municipality_slug,
                meeting_external_id=manifest.source_node_external_id if manifest else None,
                start_offset=artifact.start_offset,
                end_offset=artifact.end_offset,
                start_page=artifact.start_page,
                end_page=artifact.end_page,
                primary_topic=_annotation_primary_topic(topic_annotation),
                secondary_topics=_annotation_secondary_topics(topic_annotation),
                chunk_text=artifact.retrieval_text,
                section_path=_loads_json_list(artifact.header_path_json),
                artifact_kind=artifact.artifact_kind,
            )
        return [by_id[artifact_id] for artifact_id in normalized_ids if artifact_id in by_id]

    def _collect_candidate_scores(
        self,
        normalized_query: str,
        *,
        normalized_topic_terms: list[str],
        source_type: str | None = None,
    ) -> dict[str, dict[str, float]]:
        candidate_scores: dict[str, dict[str, float]] = {}
        tokens = [token for token in normalized_query.split(" ") if token]
        fts_query = " OR ".join(tokens[:FTS_TOKEN_LIMIT])
        if fts_query:
            try:
                if source_type:
                    rows = self.session.execute(
                        text(
                            "SELECT artifact_fts.artifact_id, bm25(artifact_fts) AS rank FROM artifact_fts "
                            "JOIN retrieval_artifact ON retrieval_artifact.artifact_id = artifact_fts.artifact_id "
                            "WHERE artifact_fts MATCH :q AND retrieval_artifact.source_kind = :source_type "
                            f"LIMIT {SEARCH_FTS_CANDIDATE_LIMIT}"
                        ),
                        {"q": fts_query, "source_type": source_type},
                    ).mappings().all()
                else:
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
            if source_type:
                rows = self.session.execute(
                    text(
                        "SELECT retrieval_artifact_trigram.artifact_id, COUNT(*) AS overlap FROM retrieval_artifact_trigram "
                        "JOIN retrieval_artifact ON retrieval_artifact.artifact_id = retrieval_artifact_trigram.artifact_id "
                        f"WHERE trigram IN ({placeholders}) AND retrieval_artifact.source_kind = :source_type "
                        "GROUP BY retrieval_artifact_trigram.artifact_id "
                        f"ORDER BY overlap DESC LIMIT {SEARCH_TRIGRAM_CANDIDATE_LIMIT}"
                    ),
                    {**params, "source_type": source_type},
                ).mappings().all()
            else:
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

        query_vector = self.embedding_service.embed_query(normalized_query, query_kind="search")
        if query_vector:
            embedding_stmt = select(RetrievalArtifactEmbedding.artifact_id, RetrievalArtifactEmbedding.embedding_json).where(
                RetrievalArtifactEmbedding.model_provider == self.embedding_service.model_client.provider_name,
                RetrievalArtifactEmbedding.model_name == self.embedding_service.model_client.model_name,
                RetrievalArtifactEmbedding.dimensions == self.embedding_service.model_client.dimensions,
            )
            if source_type:
                embedding_stmt = embedding_stmt.join(
                    RetrievalArtifact,
                    RetrievalArtifact.artifact_id == RetrievalArtifactEmbedding.artifact_id,
                ).where(RetrievalArtifact.source_kind == source_type)
            embedding_rows = self.session.execute(embedding_stmt).all()
            embedding_hits: list[tuple[float, str]] = []
            for artifact_id, embedding_json in embedding_rows:
                vector = _loads_embedding_vector(embedding_json)
                similarity = _embedding_similarity(query_vector, vector)
                if similarity <= 0.0:
                    continue
                embedding_hits.append((similarity, str(artifact_id)))
            embedding_hits.sort(key=lambda row: row[0], reverse=True)
            for similarity, artifact_id in embedding_hits[:SEARCH_EMBEDDING_CANDIDATE_LIMIT]:
                candidate_scores.setdefault(artifact_id, {})["embedding_similarity"] = round(similarity, 6)

        if self._topic_annotations_available():
            for topic_term in [normalized_query, *normalized_topic_terms]:
                if not topic_term:
                    continue
                topic_stmt = (
                    select(ArtifactTopicAnnotation.artifact_id, ArtifactTopicAnnotation.primary_topic_norm)
                    .join(RetrievalArtifact, RetrievalArtifact.artifact_id == ArtifactTopicAnnotation.artifact_id)
                    .where(ArtifactTopicAnnotation.primary_topic_norm.is_not(None))
                    .where(ArtifactTopicAnnotation.classifier_route.contains("dictalm"))
                    .where(ArtifactTopicAnnotation.primary_topic_norm.contains(topic_term))
                )
                if source_type:
                    topic_stmt = topic_stmt.where(RetrievalArtifact.source_kind == source_type)
                topic_rows = self.session.execute(topic_stmt.limit(SEARCH_FALLBACK_CONTAINS_LIMIT)).all()
                for artifact_id, _primary_topic_norm in topic_rows:
                    entry = candidate_scores.setdefault(str(artifact_id), {})
                    entry["topic_match"] = max(float(entry.get("topic_match") or 0.0), 1.0)

        if candidate_scores:
            return candidate_scores

        fallback_stmt = select(RetrievalArtifact.artifact_id).where(RetrievalArtifact.retrieval_text_norm.contains(normalized_query))
        if source_type:
            fallback_stmt = fallback_stmt.where(RetrievalArtifact.source_kind == source_type)
        fallback_rows = self.session.execute(fallback_stmt.limit(SEARCH_FALLBACK_CONTAINS_LIMIT)).scalars().all()
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
            .where(SemanticNode.status == "active")
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
            if is_low_quality_topic_label(str(node.pref_label_he or "")):
                continue
            labels = [str(node.pref_label_norm or "").strip()]
            labels.extend(
                str(label or "").strip()
                for label in aliases_by_node.get(int(node.id), [])
                if str(label or "").strip() and not is_low_quality_topic_label(str(label or ""))
            )
            labels.extend(
                label
                for label in _semantic_path_labels(node=node, nodes_by_id=nodes_by_id)
                if label and not is_low_quality_topic_label(label)
            )

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

    def _topic_annotations_available(self) -> bool:
        try:
            return bool(inspect(self.session.get_bind()).has_table("artifact_topic_annotation"))
        except Exception:  # noqa: BLE001
            self.session.rollback()
            return False

    def _topic_score(
        self,
        *,
        annotation: ArtifactTopicAnnotation | None,
        normalized_query: str,
        normalized_topic_terms: list[str],
    ) -> float:
        if annotation is None:
            return 0.0
        labels = []
        primary = _annotation_primary_topic(annotation)
        if primary:
            labels.append(normalize_for_search(primary))
        labels.extend(normalize_for_search(label) for label in _annotation_secondary_topics(annotation) if normalize_for_search(label))
        labels = [label for label in labels if label]
        if not labels:
            return 0.0

        best = _semantic_text_match_score(normalized_query, labels)
        for term in normalized_topic_terms:
            best = max(best, _semantic_text_match_score(term, labels))
        return round(TOPIC_TEXT_WEIGHT * best, 6)


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


def _annotation_primary_topic(annotation: ArtifactTopicAnnotation | None) -> str | None:
    if annotation is None or not _annotation_is_dictalm_backed(annotation):
        return None
    return sanitize_topic_path_label(annotation.primary_topic_he)


def _annotation_secondary_topics(annotation: ArtifactTopicAnnotation | None) -> list[str]:
    if annotation is None or not _annotation_is_dictalm_backed(annotation) or not annotation.secondary_topics_json:
        return []
    try:
        parsed = json.loads(annotation.secondary_topics_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [
        sanitized
        for value in [str(item).strip() for item in parsed if str(item).strip()]
        for sanitized in [sanitize_topic_path_label(value)]
        if sanitized and not is_low_quality_topic_label(sanitized)
    ]


def _annotation_is_dictalm_backed(annotation: ArtifactTopicAnnotation) -> bool:
    return "dictalm" in str(annotation.classifier_route or "").casefold()


def _loads_embedding_vector(value: str | None) -> list[float] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    try:
        return [float(item) for item in parsed]
    except (TypeError, ValueError):
        return None


def _embedding_similarity(query_vector: list[float], artifact_vector: list[float] | None) -> float:
    if not query_vector or not artifact_vector or len(query_vector) != len(artifact_vector):
        return 0.0
    numerator = sum(left * right for left, right in zip(query_vector, artifact_vector, strict=True))
    query_norm = sum(value * value for value in query_vector) ** 0.5
    artifact_norm = sum(value * value for value in artifact_vector) ** 0.5
    if query_norm <= 0.0 or artifact_norm <= 0.0:
        return 0.0
    return max(0.0, min(1.0, numerator / (query_norm * artifact_norm)))
