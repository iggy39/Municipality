from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.chunking import normalize_for_search
from municipality.models import ArtifactSemanticLink, ArtifactTopicAnnotation, RetrievalArtifact, SemanticNode
from municipality.topic_label_quality import is_low_quality_topic_label, sanitize_topic_path_label


@dataclass(slots=True)
class TopicDataMaintenanceStats:
    artifact_annotations_scanned: int = 0
    artifact_annotations_updated: int = 0
    semantic_nodes_scanned: int = 0
    semantic_nodes_deprecated: int = 0


def cleanup_low_quality_topic_data(
    session: Session,
    *,
    document_ids: list[int] | None = None,
    document_version_ids: list[int] | None = None,
    dry_run: bool = False,
) -> TopicDataMaintenanceStats:
    artifact_ids = _scoped_artifact_ids(
        session=session,
        document_ids=document_ids,
        document_version_ids=document_version_ids,
    )
    semantic_node_ids = _scoped_semantic_node_ids(
        session=session,
        document_ids=document_ids,
        document_version_ids=document_version_ids,
    )

    stats = TopicDataMaintenanceStats()
    now = datetime.utcnow()

    annotation_stmt = select(ArtifactTopicAnnotation)
    if artifact_ids is not None:
        if not artifact_ids:
            return stats
        annotation_stmt = annotation_stmt.where(ArtifactTopicAnnotation.artifact_id.in_(artifact_ids))

    annotation_rows = session.execute(annotation_stmt).scalars().all()
    for row in annotation_rows:
        stats.artifact_annotations_scanned += 1
        structural_topic_he = sanitize_topic_path_label(row.structural_topic_he)
        primary_topic_he = sanitize_topic_path_label(row.primary_topic_he)
        secondary_topics = _sanitize_secondary_topics(row.secondary_topics_json)

        structural_topic_norm = normalize_for_search(structural_topic_he or "") or None
        primary_topic_norm = normalize_for_search(primary_topic_he or "") or None
        secondary_topics_json = json.dumps(secondary_topics, ensure_ascii=False) if secondary_topics else None

        if (
            row.structural_topic_he == structural_topic_he
            and row.structural_topic_norm == structural_topic_norm
            and row.primary_topic_he == primary_topic_he
            and row.primary_topic_norm == primary_topic_norm
            and row.secondary_topics_json == secondary_topics_json
        ):
            continue

        stats.artifact_annotations_updated += 1
        if dry_run:
            continue

        row.structural_topic_he = structural_topic_he
        row.structural_topic_norm = structural_topic_norm
        row.primary_topic_he = primary_topic_he
        row.primary_topic_norm = primary_topic_norm
        row.secondary_topics_json = secondary_topics_json
        row.updated_at = now

    node_stmt = select(SemanticNode).where(SemanticNode.node_kind == "topic")
    if semantic_node_ids is not None:
        if not semantic_node_ids:
            return stats
        node_stmt = node_stmt.where(SemanticNode.id.in_(semantic_node_ids))

    semantic_rows = session.execute(node_stmt).scalars().all()
    for row in semantic_rows:
        stats.semantic_nodes_scanned += 1
        if not is_low_quality_topic_label(row.pref_label_he):
            continue
        if str(row.status or "").strip().casefold() == "deprecated":
            continue

        stats.semantic_nodes_deprecated += 1
        if dry_run:
            continue

        row.status = "deprecated"
        row.updated_at = now

    return stats


def _scoped_artifact_ids(
    *,
    session: Session,
    document_ids: list[int] | None,
    document_version_ids: list[int] | None,
) -> list[str] | None:
    if not document_ids and not document_version_ids:
        return None

    stmt = select(RetrievalArtifact.artifact_id)
    if document_ids:
        stmt = stmt.where(RetrievalArtifact.document_id.in_(sorted(set(int(value) for value in document_ids))))
    if document_version_ids:
        stmt = stmt.where(
            RetrievalArtifact.document_version_id.in_(sorted(set(int(value) for value in document_version_ids)))
        )
    return list(session.execute(stmt).scalars().all())


def _scoped_semantic_node_ids(
    *,
    session: Session,
    document_ids: list[int] | None,
    document_version_ids: list[int] | None,
) -> list[int] | None:
    if not document_ids and not document_version_ids:
        return None

    stmt = select(ArtifactSemanticLink.semantic_node_id).join(
        RetrievalArtifact,
        RetrievalArtifact.artifact_id == ArtifactSemanticLink.artifact_id,
    )
    if document_ids:
        stmt = stmt.where(RetrievalArtifact.document_id.in_(sorted(set(int(value) for value in document_ids))))
    if document_version_ids:
        stmt = stmt.where(
            RetrievalArtifact.document_version_id.in_(sorted(set(int(value) for value in document_version_ids)))
        )
    return sorted({int(value) for value in session.execute(stmt).scalars().all() if value is not None})


def _sanitize_secondary_topics(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        sanitized = sanitize_topic_path_label(str(item).strip())
        if not sanitized:
            continue
        normalized = normalize_for_search(sanitized)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(sanitized)
    return out
