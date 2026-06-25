from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.chunking import normalize_for_search
from municipality.models import (
    ArtifactSemanticLink,
    ArtifactTopicAnnotation,
    Document,
    RetrievalArtifact,
    SemanticAlias,
    SemanticNode,
    SourceSite,
)
from municipality.semantic_extractor import DEFAULT_OLLAMA_BASE_URL
from municipality.topic_label_quality import is_low_quality_topic_label


PDF_FIRST_SOURCE_KINDS = {"pdf_first_protocol", "pdf_first_attachment"}
BRIDGE_PROVENANCE = "pdf_first_topic_bridge"
DEFAULT_DICTALM_MODEL = "dicta-il/DictaLM-3.0-24B-Thinking:bf16"

CONTROLLED_TOPIC_ROOTS = (
    "סדר יום ושאילתות",
    "עדכוני ראש העיר",
    "הצעות לסדר",
    "הקצאות ושימושים",
    "הסכמים והתקשרויות",
    "תמיכות",
    "תקציב וכספים",
    "תכנון ובנייה",
    "תחבורה ובטיחות",
    "חינוך",
    "רווחה ושירותים חברתיים",
    "תרבות וספורט",
    "תשתיות וסביבה",
    "דת ושירותי דת",
    "מנהל עירוני ומינויים",
    "ביטחון ואכיפה",
    "אישורי נסיעות",
    "שמירה והיטלים",
    "נכסים ומרכזים מסחריים",
    "כוח אדם ועובדים",
)

STRUCTURAL_ROLE_LABELS = {
    "body",
    "continuation",
    "outline_item",
    "section_heading",
    "vote_or_result",
    "task_row",
    "metadata",
}


@dataclass(slots=True)
class BridgeConfig:
    municipality_slug: str | None = None
    source_kinds: tuple[str, ...] = ("pdf_first_protocol", "pdf_first_attachment")
    min_artifact_count: int = 1
    llm_cleanup: bool = False
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    model_name: str = DEFAULT_DICTALM_MODEL
    timeout_seconds: float = 180.0
    llm_batch_size: int = 35
    write: bool = False
    replace_links: bool = False


@dataclass(slots=True)
class TopicCandidate:
    label: str
    norm: str
    artifact_ids: list[str] = field(default_factory=list)
    sample_texts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CleanTopic:
    input_norm: str
    canonical_label: str | None
    root_label: str | None
    child_label: str | None
    confidence: float
    route: str
    reject_reason: str | None = None

    @property
    def accepted(self) -> bool:
        return bool(self.root_label or self.canonical_label) and not self.reject_reason


@dataclass(slots=True)
class BridgeResult:
    mode: str
    candidate_label_count: int
    cleaned_label_count: int
    rejected_label_count: int
    artifact_count: int
    link_count: int
    node_count: int
    alias_count: int
    sample_nodes: list[str]
    sample_rejects: list[dict[str, str]]
    llm_cleanup: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "candidate_label_count": self.candidate_label_count,
            "cleaned_label_count": self.cleaned_label_count,
            "rejected_label_count": self.rejected_label_count,
            "artifact_count": self.artifact_count,
            "link_count": self.link_count,
            "node_count": self.node_count,
            "alias_count": self.alias_count,
            "sample_nodes": self.sample_nodes,
            "sample_rejects": self.sample_rejects,
            "llm_cleanup": self.llm_cleanup,
        }


class LabelCleaner(Protocol):
    def clean(self, candidates: list[TopicCandidate]) -> tuple[dict[str, CleanTopic], dict[str, Any]]:
        raise NotImplementedError


class DictaLmOllamaLabelCleaner:
    def __init__(self, *, base_url: str, model_name: str, timeout_seconds: float, batch_size: int = 35):
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.batch_size = max(1, int(batch_size))

    def clean(self, candidates: list[TopicCandidate]) -> tuple[dict[str, CleanTopic], dict[str, Any]]:
        cleaned: dict[str, CleanTopic] = {}
        errors: list[str] = []
        for start in range(0, len(candidates), self.batch_size):
            batch = candidates[start : start + self.batch_size]
            batch_cleaned, batch_error = self._clean_batch(batch)
            cleaned.update(batch_cleaned)
            if batch_error:
                errors.append(batch_error)
        return cleaned, {
            "enabled": True,
            "applied": bool(cleaned),
            "model": self.model_name,
            "input_count": len(candidates),
            "output_count": len(cleaned),
            "batch_size": self.batch_size,
            "error_count": len(errors),
            "sample_errors": errors[:3],
        }

    def _clean_batch(self, candidates: list[TopicCandidate]) -> tuple[dict[str, CleanTopic], str | None]:
        payload = _dictalm_cleanup_payload(candidates)
        body = {
            "model": self.model_name,
            "stream": False,
            "think": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": "/no_think\nClean Hebrew municipal topic labels. Return strict JSON only."},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "options": {"temperature": 0},
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(f"{self.base_url}/api/chat", json=body)
                response.raise_for_status()
                response_payload = response.json()
        except Exception as exc:  # noqa: BLE001
            return {}, f"{exc.__class__.__name__}:{exc}"

        content = _ollama_content(response_payload)
        parsed = _loads_json_object(content or "")
        if not isinstance(parsed, dict):
            return {}, "invalid_json"

        cleaned: dict[str, CleanTopic] = {}
        rows = parsed.get("topics")
        if not isinstance(rows, list):
            return {}, "missing_topics_array"

        allowed_norms = {candidate.norm for candidate in candidates}
        for row in rows:
            if not isinstance(row, dict):
                continue
            input_label = str(row.get("input_label") or "").strip()
            input_norm = normalize_for_search(input_label)
            if input_norm not in allowed_norms:
                input_norm = normalize_for_search(str(row.get("input_norm") or ""))
            if input_norm not in allowed_norms:
                continue

            clean_topic = _clean_topic_from_model_row(input_norm=input_norm, row=row)
            if clean_topic is not None:
                cleaned[input_norm] = clean_topic

        return cleaned, None


def build_pdf_first_semantic_topic_tree(
    session: Session,
    *,
    config: BridgeConfig | None = None,
    cleaner: LabelCleaner | None = None,
) -> BridgeResult:
    config = config or BridgeConfig()
    rows = _load_pdf_first_topic_rows(session=session, config=config)
    candidates, artifact_candidate_norms = _collect_candidates(rows)
    if config.min_artifact_count > 1:
        allowed_norms = {candidate.norm for candidate in candidates if len(candidate.artifact_ids) >= config.min_artifact_count}
        candidates = [candidate for candidate in candidates if candidate.norm in allowed_norms]
        artifact_candidate_norms = {
            artifact_id: [norm for norm in norms if norm in allowed_norms]
            for artifact_id, norms in artifact_candidate_norms.items()
        }
        artifact_candidate_norms = {artifact_id: norms for artifact_id, norms in artifact_candidate_norms.items() if norms}
    deterministic_cleaned = {candidate.norm: _deterministic_clean_topic(candidate) for candidate in candidates}

    llm_info: dict[str, Any] = {"enabled": False, "applied": False}
    if config.llm_cleanup and candidates:
        active_cleaner = cleaner or DictaLmOllamaLabelCleaner(
            base_url=config.ollama_base_url,
            model_name=config.model_name,
            timeout_seconds=config.timeout_seconds,
            batch_size=config.llm_batch_size,
        )
        llm_cleaned, llm_info = active_cleaner.clean(candidates)
        for norm, clean_topic in llm_cleaned.items():
            if clean_topic.accepted:
                deterministic_cleaned[norm] = clean_topic
            elif norm in deterministic_cleaned and not deterministic_cleaned[norm].accepted:
                deterministic_cleaned[norm] = clean_topic

    candidate_by_norm = {candidate.norm: candidate for candidate in candidates}
    accepted_by_norm = {
        norm: topic
        for norm, topic in deterministic_cleaned.items()
        if topic.accepted
        and _valid_topic_label(topic.root_label or topic.canonical_label)
        and _valid_bridge_source_label(candidate_by_norm.get(norm).label if candidate_by_norm.get(norm) else None)
    }
    rejected = [topic for topic in deterministic_cleaned.values() if not topic.accepted]

    artifact_topics = _best_topic_by_artifact(
        artifact_candidate_norms=artifact_candidate_norms,
        accepted_by_norm=accepted_by_norm,
    )

    node_specs = _node_specs_for_artifact_topics(artifact_topics)
    link_count = len(artifact_topics)
    alias_count = _planned_alias_count(candidates=candidates, accepted_by_norm=accepted_by_norm)

    if config.write:
        _persist_bridge(
            session=session,
            rows=rows,
            candidates=candidates,
            artifact_topics=artifact_topics,
            accepted_by_norm=accepted_by_norm,
            replace_links=config.replace_links,
        )
        session.commit()
    else:
        session.rollback()

    sample_nodes = sorted({spec["display"] for spec in node_specs})[:12]
    sample_rejects = [
        {"label": _candidate_label_for_norm(candidates, topic.input_norm), "reason": topic.reject_reason or "unknown"}
        for topic in rejected[:8]
    ]
    return BridgeResult(
        mode="write" if config.write else "dry_run",
        candidate_label_count=len(candidates),
        cleaned_label_count=len(accepted_by_norm),
        rejected_label_count=len(rejected),
        artifact_count=len(rows),
        link_count=link_count,
        node_count=len(node_specs),
        alias_count=alias_count,
        sample_nodes=sample_nodes,
        sample_rejects=sample_rejects,
        llm_cleanup=llm_info,
    )


def _load_pdf_first_topic_rows(*, session: Session, config: BridgeConfig) -> list[tuple[ArtifactTopicAnnotation, RetrievalArtifact, Document, SourceSite]]:
    stmt = (
        select(ArtifactTopicAnnotation, RetrievalArtifact, Document, SourceSite)
        .join(RetrievalArtifact, RetrievalArtifact.artifact_id == ArtifactTopicAnnotation.artifact_id)
        .join(Document, Document.id == RetrievalArtifact.document_id)
        .join(SourceSite, SourceSite.id == Document.source_site_id)
        .where(RetrievalArtifact.source_kind.in_(tuple(config.source_kinds)))
        .where(ArtifactTopicAnnotation.classifier_route.contains("dictalm"))
        .order_by(RetrievalArtifact.document_version_id.asc(), RetrievalArtifact.ordinal.asc())
    )
    if config.municipality_slug:
        stmt = stmt.where(SourceSite.municipality_slug == config.municipality_slug)
    rows = session.execute(stmt).all()
    return [row for row in rows]


def _collect_candidates(
    rows: list[tuple[ArtifactTopicAnnotation, RetrievalArtifact, Document, SourceSite]],
) -> tuple[list[TopicCandidate], dict[str, list[str]]]:
    by_norm: dict[str, TopicCandidate] = {}
    artifact_candidate_norms: dict[str, list[str]] = {}

    for annotation, artifact, _document, _site in rows:
        labels = _artifact_candidate_labels(annotation=annotation, artifact=artifact)
        for label in labels:
            norm = normalize_for_search(label)
            if not norm:
                continue
            artifact_candidate_norms.setdefault(str(artifact.artifact_id), [])
            if norm not in artifact_candidate_norms[str(artifact.artifact_id)]:
                artifact_candidate_norms[str(artifact.artifact_id)].append(norm)
            candidate = by_norm.get(norm)
            if candidate is None:
                candidate = TopicCandidate(label=label, norm=norm)
                by_norm[norm] = candidate
            if artifact.artifact_id not in candidate.artifact_ids:
                candidate.artifact_ids.append(str(artifact.artifact_id))
            sample = _sample_text_for_artifact(annotation=annotation, artifact=artifact)
            if sample and sample not in candidate.sample_texts and len(candidate.sample_texts) < 3:
                candidate.sample_texts.append(sample)

    return sorted(by_norm.values(), key=lambda row: (-len(row.artifact_ids), row.label)), artifact_candidate_norms


def _artifact_candidate_labels(*, annotation: ArtifactTopicAnnotation, artifact: RetrievalArtifact) -> list[str]:
    labels: list[str] = []
    for value in [annotation.primary_topic_he, annotation.structural_topic_he, *_loads_json_list(annotation.secondary_topics_json)]:
        label = _compact_label(value)
        if label:
            labels.append(label)
    for value in _loads_json_list(artifact.header_path_json):
        label = _compact_label(value)
        if label and label not in STRUCTURAL_ROLE_LABELS:
            labels.append(label)
    for label in _visible_topic_candidates(str(artifact.retrieval_text or artifact.body_text or "")):
        if label:
            labels.append(label)

    out: list[str] = []
    seen: set[str] = set()
    for label in labels:
        cleaned = _strip_topic_noise(label)
        if not cleaned:
            continue
        norm = normalize_for_search(cleaned)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(cleaned)
    return out


def _sample_text_for_artifact(*, annotation: ArtifactTopicAnnotation, artifact: RetrievalArtifact) -> str | None:
    for value in [annotation.section_summary, artifact.body_text, artifact.retrieval_text]:
        compact = " ".join(str(value or "").split())
        if compact:
            return compact[:260]
    return None


def _deterministic_clean_topic(candidate: TopicCandidate) -> CleanTopic:
    label = _strip_topic_noise(candidate.label)
    if not _valid_topic_label(label):
        return CleanTopic(
            input_norm=candidate.norm,
            canonical_label=None,
            root_label=None,
            child_label=None,
            confidence=0.0,
            route="deterministic_reject",
            reject_reason="low_quality_or_noisy_label",
        )

    controlled_root = _controlled_root_for_label(label)
    if controlled_root and normalize_for_search(controlled_root) == normalize_for_search(label):
        return CleanTopic(
            input_norm=candidate.norm,
            canonical_label=controlled_root,
            root_label=controlled_root,
            child_label=None,
            confidence=0.82,
            route="deterministic_controlled_root",
        )

    inferred_root = controlled_root or _infer_root_from_label_and_samples(label, candidate.sample_texts)
    child_label = label
    if inferred_root and normalize_for_search(inferred_root) == normalize_for_search(label):
        child_label = None
    return CleanTopic(
        input_norm=candidate.norm,
        canonical_label=label,
        root_label=inferred_root or label,
        child_label=child_label,
        confidence=0.68 if inferred_root else 0.58,
        route="deterministic_clean_label",
    )


def _dictalm_cleanup_payload(candidates: list[TopicCandidate]) -> dict[str, Any]:
    return {
        "task": "Clean and canonicalize Hebrew municipal topic labels for a semantic topic tree.",
        "rules": [
            "Use only the supplied labels and evidence snippets.",
            "Reject OCR garbage, overlong sentence fragments, procedural boilerplate, and generic labels.",
            "Never output נושא כללי.",
            "Return concise Hebrew labels, usually 2-6 words.",
            "Map each accepted label to a stable root topic and optional child topic.",
            "If the input is already a broad root, set child_label_he to null.",
        ],
        "allowed_root_labels": list(CONTROLLED_TOPIC_ROOTS),
        "response_schema": {
            "topics": [
                {
                    "input_label": "string",
                    "input_norm": "string",
                    "decision": "accept|reject",
                    "root_label_he": "string|null",
                    "child_label_he": "string|null",
                    "canonical_label_he": "string|null",
                    "aliases_he": ["string"],
                    "confidence": "number 0..1",
                    "reject_reason": "string|null",
                }
            ]
        },
        "candidates": [
            {
                "input_label": candidate.label,
                "input_norm": candidate.norm,
                "artifact_count": len(candidate.artifact_ids),
                "examples": candidate.sample_texts[:2],
            }
            for candidate in candidates[:220]
        ],
    }


def _clean_topic_from_model_row(*, input_norm: str, row: dict[str, Any]) -> CleanTopic | None:
    decision = str(row.get("decision") or "").strip().casefold()
    confidence = _clamp_float(row.get("confidence"), default=0.0)
    if decision == "reject":
        return CleanTopic(
            input_norm=input_norm,
            canonical_label=None,
            root_label=None,
            child_label=None,
            confidence=confidence,
            route="dictalm_cleanup_reject",
            reject_reason=str(row.get("reject_reason") or "dictalm_rejected").strip() or "dictalm_rejected",
        )

    root = _strip_topic_noise(row.get("root_label_he"))
    child = _strip_topic_noise(row.get("child_label_he"))
    canonical = _strip_topic_noise(row.get("canonical_label_he")) or child or root
    if not _valid_topic_label(root or canonical):
        return None
    if child and not _valid_topic_label(child):
        child = None
    if child and root and normalize_for_search(child) == normalize_for_search(root):
        child = None
    return CleanTopic(
        input_norm=input_norm,
        canonical_label=canonical,
        root_label=root or canonical,
        child_label=child,
        confidence=max(0.01, confidence),
        route="dictalm_cleanup_accept",
    )


def _best_topic_by_artifact(
    *,
    artifact_candidate_norms: dict[str, list[str]],
    accepted_by_norm: dict[str, CleanTopic],
) -> dict[str, CleanTopic]:
    out: dict[str, CleanTopic] = {}
    for artifact_id, norms in artifact_candidate_norms.items():
        accepted = [accepted_by_norm[norm] for norm in norms if norm in accepted_by_norm]
        if not accepted:
            continue
        accepted.sort(
            key=lambda topic: (
                1 if topic.child_label else 0,
                topic.confidence,
                len(topic.canonical_label or ""),
            ),
            reverse=True,
        )
        out[artifact_id] = accepted[0]
    return out


def _node_specs_for_artifact_topics(artifact_topics: dict[str, CleanTopic]) -> list[dict[str, Any]]:
    specs: dict[tuple[str, str | None], dict[str, Any]] = {}
    for topic in artifact_topics.values():
        root = topic.root_label or topic.canonical_label
        if not root:
            continue
        root_key = (normalize_for_search(root), None)
        specs[root_key] = {"display": root, "parent_norm": None, "depth": 0}
        if topic.child_label:
            child_key = (normalize_for_search(topic.child_label), normalize_for_search(root))
            specs[child_key] = {"display": topic.child_label, "parent_norm": normalize_for_search(root), "depth": 1}
    return list(specs.values())


def _persist_bridge(
    *,
    session: Session,
    rows: list[tuple[ArtifactTopicAnnotation, RetrievalArtifact, Document, SourceSite]],
    candidates: list[TopicCandidate],
    artifact_topics: dict[str, CleanTopic],
    accepted_by_norm: dict[str, CleanTopic],
    replace_links: bool,
) -> None:
    if not artifact_topics:
        return
    artifact_to_row = {str(artifact.artifact_id): (artifact, document, site) for _annotation, artifact, document, site in rows}
    site_ids = sorted({int(site.id) for _annotation, _artifact, _document, site in rows})
    existing_nodes = session.execute(select(SemanticNode).where(SemanticNode.source_site_id.in_(site_ids))).scalars().all()
    nodes_by_hash = {(int(node.source_site_id), str(node.node_key_hash)): node for node in existing_nodes}
    nodes_by_site_parent_norm: dict[tuple[int, str | None, str], SemanticNode] = {}
    now = datetime.utcnow()

    root_nodes: dict[tuple[int, str], SemanticNode] = {}
    leaf_nodes_by_artifact: dict[str, SemanticNode] = {}

    bridge_node_ids = [int(node.id) for node in existing_nodes if str(node.metadata_json or "").find(BRIDGE_PROVENANCE) >= 0]
    if bridge_node_ids:
        session.query(SemanticAlias).filter(SemanticAlias.semantic_node_id.in_(bridge_node_ids)).delete(synchronize_session=False)

    for artifact_id, topic in artifact_topics.items():
        row = artifact_to_row.get(artifact_id)
        if row is None:
            continue
        artifact, _document, site = row
        site_id = int(site.id)
        root_label = topic.root_label or topic.canonical_label
        if not root_label:
            continue
        root_node = _upsert_node(
            session=session,
            nodes_by_hash=nodes_by_hash,
            source_site_id=site_id,
            label=root_label,
            semantic_type="pdf_first_topic_root",
            parent_node=None,
            support_count=_support_count_for_topic(artifact_topics, root_label=root_label, child_label=None),
            confidence=topic.confidence,
            document_version_id=int(artifact.document_version_id),
            now=now,
        )
        root_nodes[(site_id, normalize_for_search(root_label))] = root_node
        nodes_by_site_parent_norm[(site_id, None, normalize_for_search(root_label))] = root_node
        leaf_node = root_node
        if topic.child_label:
            leaf_node = _upsert_node(
                session=session,
                nodes_by_hash=nodes_by_hash,
                source_site_id=site_id,
                label=topic.child_label,
                semantic_type="pdf_first_topic",
                parent_node=root_node,
                support_count=_support_count_for_topic(artifact_topics, root_label=root_label, child_label=topic.child_label),
                confidence=topic.confidence,
                document_version_id=int(artifact.document_version_id),
                now=now,
            )
        leaf_nodes_by_artifact[artifact_id] = leaf_node

    if replace_links:
        session.query(ArtifactSemanticLink).filter(
            ArtifactSemanticLink.metadata_json.like(f"%{BRIDGE_PROVENANCE}%")
        ).delete(synchronize_session=False)

    _upsert_aliases(session=session, candidates=candidates, accepted_by_norm=accepted_by_norm, leaf_nodes_by_artifact=leaf_nodes_by_artifact)
    _upsert_artifact_links(session=session, artifact_topics=artifact_topics, leaf_nodes_by_artifact=leaf_nodes_by_artifact)
    session.flush()


def _upsert_node(
    *,
    session: Session,
    nodes_by_hash: dict[tuple[int, str], SemanticNode],
    source_site_id: int,
    label: str,
    semantic_type: str,
    parent_node: SemanticNode | None,
    support_count: int,
    confidence: float,
    document_version_id: int,
    now: datetime,
) -> SemanticNode:
    label_norm = normalize_for_search(label)
    parent_norm = normalize_for_search(parent_node.pref_label_he) if parent_node is not None else ""
    node_hash = _node_key_hash(parent_norm=parent_norm, label_norm=label_norm, semantic_type=semantic_type)
    row = nodes_by_hash.get((source_site_id, node_hash))
    if row is None:
        row = SemanticNode(
            source_site_id=source_site_id,
            node_key_hash=node_hash,
            node_kind="topic",
            semantic_type=semantic_type,
            pref_label_he=label,
            pref_label_norm=label_norm,
            parent_node_id=parent_node.id if parent_node is not None else None,
            depth=1 if parent_node is not None else 0,
            specificity_score=0.72 if parent_node is not None else 0.54,
            confidence=confidence,
            support_count=support_count,
            status="active",
            first_seen_document_version_id=document_version_id,
            last_seen_document_version_id=document_version_id,
            metadata_json=json.dumps({"provenance": BRIDGE_PROVENANCE}, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        nodes_by_hash[(source_site_id, node_hash)] = row
    else:
        row.pref_label_he = label
        row.pref_label_norm = label_norm
        row.node_kind = "topic"
        row.semantic_type = semantic_type
        row.parent_node_id = parent_node.id if parent_node is not None else None
        row.depth = 1 if parent_node is not None else 0
        row.specificity_score = max(float(row.specificity_score or 0.0), 0.72 if parent_node is not None else 0.54)
        row.confidence = max(float(row.confidence or 0.0), confidence)
        row.support_count = max(int(row.support_count or 0), support_count)
        row.status = "active"
        row.last_seen_document_version_id = document_version_id
        row.metadata_json = json.dumps({"provenance": BRIDGE_PROVENANCE}, ensure_ascii=False)
        row.updated_at = now
    return row


def _upsert_aliases(
    *,
    session: Session,
    candidates: list[TopicCandidate],
    accepted_by_norm: dict[str, CleanTopic],
    leaf_nodes_by_artifact: dict[str, SemanticNode],
) -> None:
    node_by_candidate_norm: dict[str, SemanticNode] = {}
    for candidate in candidates:
        topic = accepted_by_norm.get(candidate.norm)
        if topic is None:
            continue
        for artifact_id in candidate.artifact_ids:
            node = leaf_nodes_by_artifact.get(artifact_id)
            if node is not None:
                node_by_candidate_norm[candidate.norm] = node
                break
    for candidate in candidates:
        node = node_by_candidate_norm.get(candidate.norm)
        if node is None:
            continue
        if not _valid_alias_label(candidate.label):
            continue
        alias_norm = normalize_for_search(candidate.label)
        if not alias_norm:
            continue
        alias_hash = hashlib.sha1(f"{node.id}|{alias_norm}".encode("utf-8")).hexdigest()
        existing = session.execute(
            select(SemanticAlias).where(
                SemanticAlias.semantic_node_id == node.id,
                SemanticAlias.alias_hash == alias_hash,
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                SemanticAlias(
                    semantic_node_id=node.id,
                    alias_hash=alias_hash,
                    alias_label_he=candidate.label,
                    alias_label_norm=alias_norm,
                    alias_kind="legacy",
                    confidence=0.62,
                    first_seen_document_version_id=node.first_seen_document_version_id,
                    last_seen_document_version_id=node.last_seen_document_version_id,
                    metadata_json=json.dumps({"provenance": BRIDGE_PROVENANCE}, ensure_ascii=False),
                )
            )
        else:
            existing.alias_label_he = candidate.label
            existing.alias_label_norm = alias_norm
            existing.confidence = max(float(existing.confidence or 0.0), 0.62)
            existing.metadata_json = json.dumps({"provenance": BRIDGE_PROVENANCE}, ensure_ascii=False)


def _upsert_artifact_links(
    *,
    session: Session,
    artifact_topics: dict[str, CleanTopic],
    leaf_nodes_by_artifact: dict[str, SemanticNode],
) -> None:
    for artifact_id, topic in artifact_topics.items():
        node = leaf_nodes_by_artifact.get(artifact_id)
        if node is None:
            continue
        existing = session.execute(
            select(ArtifactSemanticLink).where(
                ArtifactSemanticLink.artifact_id == artifact_id,
                ArtifactSemanticLink.semantic_node_id == node.id,
            )
        ).scalar_one_or_none()
        metadata = json.dumps(
            {
                "provenance": BRIDGE_PROVENANCE,
                "route": topic.route,
                "root_label": topic.root_label,
                "child_label": topic.child_label,
            },
            ensure_ascii=False,
        )
        if existing is None:
            session.add(
                ArtifactSemanticLink(
                    artifact_id=artifact_id,
                    semantic_node_id=node.id,
                    confidence=max(0.01, min(1.0, topic.confidence)),
                    source_mention_id=None,
                    metadata_json=metadata,
                )
            )
        else:
            existing.confidence = max(float(existing.confidence or 0.0), topic.confidence)
            existing.metadata_json = metadata


def _support_count_for_topic(artifact_topics: dict[str, CleanTopic], *, root_label: str, child_label: str | None) -> int:
    root_norm = normalize_for_search(root_label)
    child_norm = normalize_for_search(child_label or "")
    count = 0
    for topic in artifact_topics.values():
        if normalize_for_search(topic.root_label or topic.canonical_label or "") != root_norm:
            continue
        if child_label and normalize_for_search(topic.child_label or "") != child_norm:
            continue
        count += 1
    return count


def _planned_alias_count(*, candidates: list[TopicCandidate], accepted_by_norm: dict[str, CleanTopic]) -> int:
    return sum(1 for candidate in candidates if candidate.norm in accepted_by_norm)


def _candidate_label_for_norm(candidates: list[TopicCandidate], norm: str) -> str:
    for candidate in candidates:
        if candidate.norm == norm:
            return candidate.label
    return norm


def _node_key_hash(*, parent_norm: str, label_norm: str, semantic_type: str) -> str:
    return hashlib.sha1(f"{semantic_type}|{parent_norm}|{label_norm}".encode("utf-8")).hexdigest()


def _valid_topic_label(value: str | None) -> bool:
    label = _strip_topic_noise(value)
    if not label:
        return False
    if normalize_for_search(label) == normalize_for_search("נושא כללי"):
        return False
    if is_low_quality_topic_label(label):
        return False
    tokens = re.findall(r"[א-ת]{2,}", label)
    if len(tokens) < 2 and normalize_for_search(label) not in {normalize_for_search("חינוך"), normalize_for_search("תמיכות")}:
        return False
    if len(label) > 90:
        return False
    if _looks_like_sentence_fragment(label):
        return False
    return True


def _valid_alias_label(value: str | None) -> bool:
    label = _strip_topic_noise(value)
    if not label:
        return False
    if label in STRUCTURAL_ROLE_LABELS:
        return False
    if normalize_for_search(label) in {normalize_for_search("נושא כללי"), normalize_for_search("ללא תיוג סמנטי")}:
        return False
    if is_low_quality_topic_label(label):
        return False
    if len(label) > 70 or _looks_like_sentence_fragment(label):
        return False
    if any(len(token) == 1 for token in label.split()):
        return False
    return bool(re.findall(r"[א-ת]{2,}", label))


def _valid_bridge_source_label(value: str | None) -> bool:
    label = _strip_topic_noise(value)
    if not label:
        return False
    if label in STRUCTURAL_ROLE_LABELS:
        return False
    if normalize_for_search(label) == normalize_for_search("שאילתות"):
        return True
    if any(len(token) == 1 for token in label.split()):
        return False
    return _valid_topic_label(label)


def _strip_topic_noise(value: Any) -> str | None:
    label = " ".join(str(value or "").replace("‫", " ").replace("‬", " ").split()).strip()
    if not label or label.lower() in {"none", "null"}:
        return None
    label = re.sub(r"^סעיף\s*\d+\s*[:.)-]*\s*", "", label).strip()
    label = re.sub(r"^[\d\s'.:()\-–]+", "", label).strip()
    label = re.sub(r"^פרוטוקול\s+מישיבת\s+", "", label).strip()
    label = re.sub(r"^מישיבת\s+", "", label).strip()
    label = re.sub(r"^פרוטוקול\s+", "", label).strip()
    label = re.split(r"\s+מחליטים\b|\s+מאשרים\b|\s+הוחלט\b", label, maxsplit=1)[0].strip()
    label = re.split(r"\s+[–-]\s+", label, maxsplit=1)[0].strip()
    label = label.strip(" .,:;()[]{}\"'׳״-–")
    label = re.sub(r"\s+", " ", label).strip()
    if len(label) > 90:
        label = " ".join(label.split()[:10]).strip()
    return label or None


def _compact_label(value: Any) -> str | None:
    return _strip_topic_noise(value)


def _controlled_root_for_label(label: str) -> str | None:
    label_norm = normalize_for_search(label)
    if not label_norm:
        return None
    for root in CONTROLLED_TOPIC_ROOTS:
        root_norm = normalize_for_search(root)
        if label_norm == root_norm:
            return root
        root_tokens = set(root_norm.split())
        label_tokens = set(label_norm.split())
        if root_tokens and label_tokens and len(root_tokens & label_tokens) >= min(2, len(root_tokens)):
            return root
    return None


def _infer_root_from_label_and_samples(label: str, samples: list[str]) -> str | None:
    combined = normalize_for_search(" ".join([label, *samples]))
    keyword_roots = [
        ("הסכמים והתקשרויות", ("הסכם", "התקשרות", "מכרז")),
        ("הקצאות ושימושים", ("הקצאה", "הקצאת", "רשות שימוש", "מקרקעין", "עמותה")),
        ("תקציב וכספים", ("תקציב", "תבר", "כספים", "מימון")),
        ("תכנון ובנייה", ("תכנון", "בנייה", "בינוי", "היטל השבחה", "תבע")),
        ("תחבורה ובטיחות", ("תחבורה", "תמרור", "כביש", "חניה", "חניון")),
        ("דת ושירותי דת", ("דתית", "מועצה דתית", "בית כנסת")),
        ("מנהל עירוני ומינויים", ("מינוי", "מועצה", "ועדה", "עובדים")),
        ("תרבות וספורט", ("ספורט", "תרבות", "מגרש")),
        ("חינוך", ("חינוך", "בית ספר", "גן", "צהרון")),
        ("תמיכות", ("תמיכה", "תמיכות", "מלגה", "מלגות")),
        ("ביטחון ואכיפה", ("ביטחון", "אכיפה", "קורונה")),
    ]
    for root, keywords in keyword_roots:
        if any(normalize_for_search(keyword) in combined for keyword in keywords):
            return root
    return None


def _looks_like_sentence_fragment(label: str) -> bool:
    normalized = normalize_for_search(label)
    if len(label.split()) >= 9:
        return True
    bad_terms = {"מחליטים", "מאשרים", "הוחלט", "נספח", "פקודת", "סעיף"}
    return sum(1 for term in bad_terms if term in normalized) >= 2


def _visible_topic_candidates(value: str) -> list[str]:
    compact = " ".join(str(value or "").split())
    if not compact:
        return []
    patterns = [
        r"שאיל(?:תא|תה)\s+[^.;\n]{0,120}?בנושא\s+[\"'׳״”]*([^\"'׳״”();.\n]{3,120})",
        r"פרוטוקול\s+מישיבת\s+([^.;:\n]{3,120})",
        r"פרוטוקול\s+ועדת\s+([^.;:\n]{3,120})",
        r"((?:הסכם|אישור הסכם)(?:\s+רשות|\s+שכירות)?\s+בין\s+עיריית\s+[^.;:,\n]{8,140}?)(?=\s+מחליטים|\s+הנושאים|\s+\*|\s+–|,|$)",
    ]
    out: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, compact):
            label = _strip_topic_noise(match.group(1))
            if label and label not in out:
                out.append(label)
    return out


def _loads_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def _ollama_content(payload: dict[str, Any]) -> str | None:
    message = payload.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return _clean_model_text(message["content"])
    response = payload.get("response")
    if isinstance(response, str):
        return _clean_model_text(response)
    return None


def _clean_model_text(value: str) -> str:
    text = str(value or "").strip()
    if "</think>" in text:
        text = text.split("</think>")[-1].strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()


def _loads_json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(value[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))
