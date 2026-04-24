from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from municipality.models import SemanticNode


SEMANTIC_SUBSTRING_MATCH_SCORE = 0.92
SEMANTIC_TOKEN_OVERLAP_MIN = 0.6
SEMANTIC_TOKEN_OVERLAP_BASE = 0.55
SEMANTIC_TOKEN_OVERLAP_SCALE = 0.35

LEXICAL_FTS_WEIGHT = 0.72
LEXICAL_TRIGRAM_WEIGHT = 0.28
SEMANTIC_OVERLAP_WEIGHT = 0.25
SEMANTIC_SPECIFICITY_WEIGHT = 0.10
TOPIC_TEXT_WEIGHT = 0.18
ARTIFACT_KIND_PRIORITY_WEIGHT = 0.08

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
    primary_topic: str | None = None
    secondary_topics: list[str] = field(default_factory=list)
    chunk_text: str = ""
    section_path: list[str] = field(default_factory=list)
    artifact_kind: str | None = None


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
            "topic_text_weight": TOPIC_TEXT_WEIGHT,
            "artifact_kind_priority_weight": ARTIFACT_KIND_PRIORITY_WEIGHT,
        },
        "candidate_limits": {
            "fts_token_limit": FTS_TOKEN_LIMIT,
            "fts_candidate_limit": SEARCH_FTS_CANDIDATE_LIMIT,
            "trigram_candidate_limit": SEARCH_TRIGRAM_CANDIDATE_LIMIT,
            "fallback_contains_limit": SEARCH_FALLBACK_CONTAINS_LIMIT,
        },
        "semantic_modes": ["off", "boost", "filter"],
        "semantic_filter_rule": "explicit semantic selectors use hierarchy-aware subtree matching; filter mode drops artifacts with semantic_match_count == 0",
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


def _semantic_path_labels(*, node: SemanticNode, nodes_by_id: dict[int, SemanticNode]) -> list[str]:
    labels: list[str] = []
    path_parts = [str(node.pref_label_norm or "").strip()]
    seen_ids = {int(node.id)}
    parent_id = node.parent_node_id
    while parent_id is not None:
        parent = nodes_by_id.get(int(parent_id))
        if parent is None or int(parent.id) in seen_ids:
            break
        seen_ids.add(int(parent.id))
        parent_label = str(parent.pref_label_norm or "").strip()
        if parent_label:
            labels.append(parent_label)
            path_parts.append(parent_label)
        parent_id = parent.parent_node_id
    if len(path_parts) >= 2:
        labels.append(" ".join(reversed(path_parts)))
    deduped: list[str] = []
    seen_labels: set[str] = set()
    for label in labels:
        key = str(label or "").strip()
        if not key or key in seen_labels:
            continue
        seen_labels.add(key)
        deduped.append(key)
    return deduped


def _descendant_node_ids(*, root_ids: set[int], children_by_parent: dict[int, list[int]]) -> set[int]:
    if not root_ids:
        return set()
    out = set(root_ids)
    frontier = list(root_ids)
    while frontier:
        parent_id = frontier.pop(0)
        for child_id in children_by_parent.get(parent_id, []):
            if child_id in out:
                continue
            out.add(child_id)
            frontier.append(child_id)
    return out


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
