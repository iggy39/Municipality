from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.chunking import normalize_for_search
from municipality.decision_context import DecisionContextService
from municipality.embeddings import ChunkEmbeddingService
from municipality.models import (
    ChunkSemanticLink,
    Decision,
    DecisionRequestContext,
    DecisionSemanticLink,
    SemanticAlias,
    SemanticDocumentRun,
    SemanticMention,
    SemanticNode,
    TextChunk,
)
from municipality.semantic_canonicalization import SemanticCanonicalizer


SEMANTIC_STRATEGY_LOCAL = "embedding_topic"
SEMANTIC_STRATEGY_EXTERNAL = "external_llm"
LOCAL_SEMANTIC_PROVIDER = "LocalTopicMatcher"
LOCAL_SEMANTIC_VERSION = "topic_centroid_v1"
DEFAULT_TOPIC_MIN_SIMILARITY = 0.78
DEFAULT_TOPIC_MIN_SIMILARITY_WITH_LEXICAL = 0.72
DEFAULT_TOPIC_MAX_CHUNKS = 8
DEFAULT_TOPIC_MAX_PER_CHUNK = 3
GENERIC_TOPICS = {
    normalize_for_search("נושא כללי"),
    normalize_for_search("החלטה כללית"),
    normalize_for_search("החלטות עירוניות"),
}


def semantic_enrichment_strategy_from_env(env: dict[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    normalized = (source.get("SEMANTIC_ENRICHMENT_STRATEGY") or SEMANTIC_STRATEGY_LOCAL).strip().casefold()
    if normalized in {SEMANTIC_STRATEGY_EXTERNAL, "legacy", "external", "llm"}:
        return SEMANTIC_STRATEGY_EXTERNAL
    return SEMANTIC_STRATEGY_LOCAL


@dataclass(slots=True)
class LocalSemanticRunResult:
    run: SemanticDocumentRun
    from_cache: bool
    evidence_spans: int
    node_candidates: int
    accepted_nodes: int
    validation_issues: int
    aliases: int = 0
    mentions: int = 0
    edges: int = 0
    decision_links: int = 0
    chunk_links: int = 0
    reject_rows: int = 0


@dataclass(slots=True)
class _TopicSeed:
    label_he: str
    label_norm: str
    source_chunk_ids: set[str] = field(default_factory=set)
    decision_ids: set[int] = field(default_factory=set)
    alias_specs: list[tuple[str, str]] = field(default_factory=list)
    confidence_values: list[float] = field(default_factory=list)

    @property
    def support_count(self) -> int:
        return max(len(self.decision_ids), len(self.confidence_values), 1)

    @property
    def confidence(self) -> float:
        if not self.confidence_values:
            return 0.65
        return round(sum(self.confidence_values) / len(self.confidence_values), 4)


class LocalSemanticTopicMatcher:
    def __init__(self, session: Session, *, canonicalizer: SemanticCanonicalizer | None = None):
        self.session = session
        self.canonicalizer = canonicalizer or SemanticCanonicalizer()
        self.embedding_service = ChunkEmbeddingService(session)
        self.min_similarity = _env_float(
            os.getenv("SEMANTIC_TOPIC_MIN_SIMILARITY"),
            default=DEFAULT_TOPIC_MIN_SIMILARITY,
            min_value=0.45,
            max_value=0.99,
        )
        self.min_similarity_with_lexical = _env_float(
            os.getenv("SEMANTIC_TOPIC_MIN_SIMILARITY_WITH_LEXICAL_SUPPORT"),
            default=DEFAULT_TOPIC_MIN_SIMILARITY_WITH_LEXICAL,
            min_value=0.35,
            max_value=0.99,
        )
        self.max_chunks_per_topic = _env_int(
            os.getenv("SEMANTIC_TOPIC_MAX_CHUNKS_PER_TOPIC"),
            default=DEFAULT_TOPIC_MAX_CHUNKS,
            min_value=1,
            max_value=40,
        )
        self.max_topics_per_chunk = _env_int(
            os.getenv("SEMANTIC_TOPIC_MAX_TOPICS_PER_CHUNK"),
            default=DEFAULT_TOPIC_MAX_PER_CHUNK,
            min_value=1,
            max_value=10,
        )

    @property
    def provider_name(self) -> str:
        return LOCAL_SEMANTIC_PROVIDER

    @property
    def model_name(self) -> str:
        return f"{LOCAL_SEMANTIC_VERSION}:{self.embedding_service.model_client.model_name}:{self.embedding_service.model_client.dimensions}"

    def run_for_document(
        self,
        *,
        source_site_id: int,
        document_id: int,
        document_version_id: int,
        source_kind: str,
    ) -> LocalSemanticRunResult:
        prompt_hash = hashlib.sha256(
            (
                f"{SEMANTIC_STRATEGY_LOCAL}|{source_kind}|{self.model_name}|{self.min_similarity}|"
                f"{self.min_similarity_with_lexical}|{self.max_chunks_per_topic}|{self.max_topics_per_chunk}"
            ).encode("utf-8")
        ).hexdigest()
        existing = self.session.execute(
            select(SemanticDocumentRun).where(
                SemanticDocumentRun.document_version_id == document_version_id,
                SemanticDocumentRun.prompt_hash == prompt_hash,
                SemanticDocumentRun.model_provider == self.provider_name,
                SemanticDocumentRun.model_name == self.model_name,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _result_from_run(existing, from_cache=True)

        run = SemanticDocumentRun(
            document_version_id=document_version_id,
            prompt_hash=prompt_hash,
            model_provider=self.provider_name,
            model_name=self.model_name,
            status="running",
            api_call_count=0,
            started_at=datetime.utcnow(),
        )
        self.session.add(run)
        self.session.flush()

        warnings: list[str] = []
        stats = _empty_stats()
        accepted_nodes: list[dict[str, Any]] = []
        if source_kind != "protocol":
            warnings.append(f"unsupported_source_kind:{source_kind}")
        else:
            DecisionContextService(self.session).process_document(
                source_document_id=document_id,
                document_version_id=document_version_id,
                source_kind=source_kind,
            )
            chunks = self.session.execute(
                select(TextChunk)
                .where(
                    TextChunk.document_id == document_id,
                    TextChunk.document_version_id == document_version_id,
                    TextChunk.source_kind == source_kind,
                )
                .order_by(TextChunk.chunk_index.asc())
            ).scalars().all()
            seeds = self._collect_topic_seeds(document_id=document_id)
            stats, accepted_nodes, warnings = self._persist_matches(
                source_site_id=source_site_id,
                document_id=document_id,
                document_version_id=document_version_id,
                chunks=chunks,
                seeds=seeds,
                warnings=warnings,
            )

        run.status = "completed"
        run.extraction_payload_json = json.dumps({"strategy": SEMANTIC_STRATEGY_LOCAL, "stats": stats}, ensure_ascii=False)
        run.validation_report_json = json.dumps({"is_valid": True, "issues": []}, ensure_ascii=False)
        run.canonicalization_report_json = json.dumps(
            {"accepted_nodes": accepted_nodes, "rejected_nodes": [], "warnings": warnings},
            ensure_ascii=False,
        )
        run.finished_at = datetime.utcnow()
        self.session.flush()
        return _result_from_run(run, from_cache=False)

    def _collect_topic_seeds(self, *, document_id: int) -> list[_TopicSeed]:
        rows = self.session.execute(
            select(DecisionRequestContext).where(DecisionRequestContext.source_document_id == document_id)
        ).scalars().all()
        grouped: dict[str, _TopicSeed] = {}
        for row in rows:
            label_he = str(row.subject_topic_he or "").strip()
            label_norm = normalize_for_search(label_he)
            if not label_norm or label_norm in GENERIC_TOPICS:
                continue
            seed = grouped.get(label_norm)
            if seed is None:
                seed = _TopicSeed(label_he=label_he, label_norm=label_norm)
                grouped[label_norm] = seed
            elif len(label_he) > len(seed.label_he):
                seed.label_he = label_he
            if row.decision_id is not None:
                seed.decision_ids.add(int(row.decision_id))
            seed.confidence_values.append(_clamp(float(row.confidence or 0.65)))
            seed.source_chunk_ids.update(_loads_json_list(row.source_chunk_ids_json))
            request_subject = str(row.request_subject_he or "").strip()
            request_subject_norm = normalize_for_search(request_subject)
            if request_subject and request_subject_norm:
                seed.alias_specs.append((request_subject, request_subject_norm))
        return sorted(grouped.values(), key=lambda item: (item.label_norm, item.label_he))

    def _persist_matches(
        self,
        *,
        source_site_id: int,
        document_id: int,
        document_version_id: int,
        chunks: list[TextChunk],
        seeds: list[_TopicSeed],
        warnings: list[str],
    ) -> tuple[dict[str, int], list[dict[str, Any]], list[str]]:
        chunk_ids = [str(chunk.chunk_id) for chunk in chunks]
        if chunk_ids:
            self.session.query(ChunkSemanticLink).filter(ChunkSemanticLink.chunk_id.in_(chunk_ids)).delete(synchronize_session=False)
            self.session.query(SemanticMention).filter(SemanticMention.document_version_id == document_version_id).delete(synchronize_session=False)
        decision_ids = self.session.execute(select(Decision.id).where(Decision.source_document_id == document_id)).scalars().all()
        if decision_ids:
            self.session.query(DecisionSemanticLink).filter(DecisionSemanticLink.decision_id.in_(decision_ids)).delete(synchronize_session=False)
        self.session.flush()
        if not chunks:
            return _empty_stats(), [], [*warnings, "no_protocol_chunks"]
        if not seeds:
            return _empty_stats(), [], [*warnings, "no_subject_topics"]

        vectors_by_chunk = self.embedding_service.load_vectors(chunk_ids)
        candidate_links: dict[str, list[tuple[int, float, dict[str, Any]]]] = {}
        accepted_nodes: list[dict[str, Any]] = []
        alias_count = 0
        decision_link_count = 0
        evidence_spans = 0

        for seed in seeds:
            node_row, node_payload = self._upsert_topic_node(
                source_site_id=source_site_id,
                document_version_id=document_version_id,
                seed=seed,
                chunk_count=len(chunks),
            )
            accepted_nodes.append(node_payload)
            alias_count += self._upsert_aliases(node_row=node_row, document_version_id=document_version_id, seed=seed)
            decision_link_count += self._upsert_decision_links(node_row=node_row, seed=seed)
            evidence_spans += len(seed.source_chunk_ids)

            links = _candidate_links_for_seed(
                node_id=node_row.id,
                seed=seed,
                chunks=chunks,
                vectors_by_chunk=vectors_by_chunk,
                min_similarity=self.min_similarity,
                min_similarity_with_lexical=self.min_similarity_with_lexical,
                max_chunks_per_topic=self.max_chunks_per_topic,
            )
            for chunk_id, confidence, metadata in links:
                candidate_links.setdefault(chunk_id, []).append((node_row.id, confidence, metadata))

        chunk_link_count = 0
        for chunk_id, items in candidate_links.items():
            ranked = sorted(items, key=lambda row: row[1], reverse=True)[: self.max_topics_per_chunk]
            for semantic_node_id, confidence, metadata in ranked:
                self.session.add(
                    ChunkSemanticLink(
                        chunk_id=chunk_id,
                        semantic_node_id=semantic_node_id,
                        confidence=confidence,
                        source_mention_id=None,
                        metadata_json=json.dumps(metadata, ensure_ascii=False),
                    )
                )
                chunk_link_count += 1
        self.session.flush()
        return {
            "evidence_spans": evidence_spans,
            "node_candidates": len(seeds),
            "accepted_nodes": len(accepted_nodes),
            "validation_issues": 0,
            "aliases": alias_count,
            "mentions": 0,
            "edges": 0,
            "decision_links": decision_link_count,
            "chunk_links": chunk_link_count,
            "reject_rows": 0,
        }, accepted_nodes, warnings

    def _upsert_topic_node(self, *, source_site_id: int, document_version_id: int, seed: _TopicSeed, chunk_count: int) -> tuple[SemanticNode, dict[str, Any]]:
        node_key_hash = self.canonicalizer.node_key_hash(
            source_site_id=source_site_id,
            node_kind="topic",
            semantic_type="request_topic",
            parent_node_key_hash_or_root="root",
            pref_label_norm=seed.label_norm,
        )
        row = self.session.execute(
            select(SemanticNode).where(SemanticNode.source_site_id == source_site_id, SemanticNode.node_key_hash == node_key_hash)
        ).scalar_one_or_none()
        now = datetime.utcnow()
        specificity = self.canonicalizer.compute_specificity_score(
            label_norm=seed.label_norm,
            support_count=seed.support_count,
            evidence_span_density=min(1.0, len(seed.source_chunk_ids) / max(chunk_count, 1)),
            parent_label_norm=None,
        )
        if row is None:
            row = SemanticNode(
                source_site_id=source_site_id,
                node_key_hash=node_key_hash,
                node_kind="topic",
                semantic_type="request_topic",
                pref_label_he=seed.label_he,
                pref_label_norm=seed.label_norm,
                parent_node_id=None,
                depth=1,
                specificity_score=specificity,
                confidence=seed.confidence,
                support_count=seed.support_count,
                status="active",
                first_seen_document_version_id=document_version_id,
                last_seen_document_version_id=document_version_id,
                metadata_json=None,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
            self.session.flush()
        row.pref_label_he = seed.label_he
        row.pref_label_norm = seed.label_norm
        row.depth = 1
        row.specificity_score = specificity
        row.confidence = max(_clamp(float(row.confidence or 0.0)), seed.confidence)
        row.support_count = max(int(row.support_count or 0), seed.support_count)
        row.status = "active"
        if row.first_seen_document_version_id is None:
            row.first_seen_document_version_id = document_version_id
        row.last_seen_document_version_id = document_version_id
        row.metadata_json = json.dumps({"strategy": SEMANTIC_STRATEGY_LOCAL, "source_chunk_ids": sorted(seed.source_chunk_ids)}, ensure_ascii=False)
        row.updated_at = now
        self.session.flush()
        return row, {
            "candidate_id": seed.label_norm,
            "label_he": seed.label_he,
            "label_norm": seed.label_norm,
            "node_kind": "topic",
            "semantic_type": "request_topic",
            "parent_candidate_id": None,
            "depth": 1,
            "specificity_score": specificity,
            "confidence": seed.confidence,
            "confidence_source": "decision_context_topic_match",
            "support_count": seed.support_count,
            "status": "active",
            "node_key_hash": node_key_hash,
            "reject_reason": None,
        }

    def _upsert_aliases(self, *, node_row: SemanticNode, document_version_id: int, seed: _TopicSeed) -> int:
        specs = [(seed.label_he, seed.label_norm, "surface")]
        specs.extend((label_he, label_norm, "request_subject") for label_he, label_norm in seed.alias_specs)
        touched = 0
        existing = self.session.execute(select(SemanticAlias).where(SemanticAlias.semantic_node_id == node_row.id)).scalars().all()
        by_hash = {row.alias_hash: row for row in existing}
        for alias_label_he, alias_label_norm, alias_kind in specs:
            alias_hash = self.canonicalizer.alias_hash(semantic_node_id=node_row.id, alias_label_norm=alias_label_norm)
            row = by_hash.get(alias_hash)
            if row is None:
                row = SemanticAlias(
                    semantic_node_id=node_row.id,
                    alias_hash=alias_hash,
                    alias_label_he=alias_label_he,
                    alias_label_norm=alias_label_norm,
                    alias_kind=alias_kind,
                    confidence=node_row.confidence,
                    first_seen_document_version_id=document_version_id,
                    last_seen_document_version_id=document_version_id,
                    metadata_json=None,
                )
                self.session.add(row)
            row.alias_label_he = alias_label_he
            row.alias_label_norm = alias_label_norm
            row.alias_kind = alias_kind
            row.confidence = max(_clamp(float(row.confidence or 0.0)), node_row.confidence)
            if row.first_seen_document_version_id is None:
                row.first_seen_document_version_id = document_version_id
            row.last_seen_document_version_id = document_version_id
            row.metadata_json = json.dumps({"strategy": SEMANTIC_STRATEGY_LOCAL}, ensure_ascii=False)
            touched += 1
        self.session.flush()
        return touched

    def _upsert_decision_links(self, *, node_row: SemanticNode, seed: _TopicSeed) -> int:
        count = 0
        for decision_id in sorted(seed.decision_ids):
            self.session.add(
                DecisionSemanticLink(
                    decision_id=decision_id,
                    semantic_node_id=node_row.id,
                    relation_role="subject_topic",
                    confidence=seed.confidence,
                    source_mention_id=None,
                    metadata_json=json.dumps({"strategy": SEMANTIC_STRATEGY_LOCAL}, ensure_ascii=False),
                )
            )
            count += 1
        self.session.flush()
        return count


def _candidate_links_for_seed(*, node_id: int, seed: _TopicSeed, chunks: list[TextChunk], vectors_by_chunk: dict[str, list[float]], min_similarity: float, min_similarity_with_lexical: float, max_chunks_per_topic: int) -> list[tuple[str, float, dict[str, Any]]]:
    out: list[tuple[str, float, dict[str, Any]]] = []
    anchor_ids = [str(chunk_id) for chunk_id in sorted(seed.source_chunk_ids)]
    for chunk_id in anchor_ids:
        out.append((chunk_id, max(seed.confidence, 0.72), {"strategy": SEMANTIC_STRATEGY_LOCAL, "match_method": "decision_context_anchor", "topic_label": seed.label_he}))
    centroid = _mean_vector([vectors_by_chunk[chunk_id] for chunk_id in anchor_ids if chunk_id in vectors_by_chunk])
    if not centroid:
        return out
    ranked: list[tuple[float, str, float, float]] = []
    for chunk in chunks:
        chunk_id = str(chunk.chunk_id)
        if chunk_id in seed.source_chunk_ids:
            continue
        vector = vectors_by_chunk.get(chunk_id)
        if not vector:
            continue
        similarity = _cosine_similarity(centroid, vector)
        lexical_overlap = _topic_overlap(seed=seed, chunk=chunk)
        if similarity < min_similarity and not (lexical_overlap > 0.0 and similarity >= min_similarity_with_lexical):
            continue
        ranked.append(((0.85 * similarity) + (0.15 * lexical_overlap), chunk_id, similarity, lexical_overlap))
    for blended, chunk_id, similarity, lexical_overlap in sorted(ranked, reverse=True)[:max_chunks_per_topic]:
        out.append((chunk_id, round(_clamp(blended), 4), {"strategy": SEMANTIC_STRATEGY_LOCAL, "match_method": "embedding_centroid", "topic_label": seed.label_he, "similarity": round(similarity, 6), "lexical_overlap": round(lexical_overlap, 6)}))
    return out


def _result_from_run(run: SemanticDocumentRun, *, from_cache: bool) -> LocalSemanticRunResult:
    payload = _loads_json_dict(run.extraction_payload_json)
    stats = payload.get("stats") if isinstance(payload, dict) else {}
    if not isinstance(stats, dict):
        stats = {}
    return LocalSemanticRunResult(
        run=run,
        from_cache=from_cache,
        evidence_spans=int(stats.get("evidence_spans") or 0),
        node_candidates=int(stats.get("node_candidates") or 0),
        accepted_nodes=int(stats.get("accepted_nodes") or 0),
        validation_issues=int(stats.get("validation_issues") or 0),
        aliases=int(stats.get("aliases") or 0),
        mentions=int(stats.get("mentions") or 0),
        edges=int(stats.get("edges") or 0),
        decision_links=int(stats.get("decision_links") or 0),
        chunk_links=int(stats.get("chunk_links") or 0),
        reject_rows=int(stats.get("reject_rows") or 0),
    )


def _empty_stats() -> dict[str, int]:
    return {"evidence_spans": 0, "node_candidates": 0, "accepted_nodes": 0, "validation_issues": 0, "aliases": 0, "mentions": 0, "edges": 0, "decision_links": 0, "chunk_links": 0, "reject_rows": 0}


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


def _loads_json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    length = len(vectors[0])
    totals = [0.0] * length
    for vector in vectors:
        if len(vector) != length:
            return []
        for idx, value in enumerate(vector):
            totals[idx] += float(value)
    return [value / len(vectors) for value in totals]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return max(0.0, min(1.0, numerator / (left_norm * right_norm)))


def _topic_overlap(*, seed: _TopicSeed, chunk: TextChunk) -> float:
    chunk_tokens = {token for token in normalize_for_search(str(chunk.chunk_text or "")).split() if token}
    topic_tokens = {token for token in seed.label_norm.split() if token}
    for _label_he, label_norm in seed.alias_specs:
        topic_tokens.update(token for token in label_norm.split() if token)
    if not chunk_tokens or not topic_tokens:
        return 0.0
    return len(topic_tokens.intersection(chunk_tokens)) / max(len(topic_tokens), 1)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


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
