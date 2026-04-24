from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any

from municipality.chunking import normalize_for_search
from municipality.rag_llm import RAG_CALL_REWRITE, RagLlmClient, build_rag_llm_client


HEADER_HINT_TERMS = (
    "החלטה",
    "החלטות",
    "נושא",
    "סעיף",
    "ועדה",
    "ועדת",
    "פרוטוקול",
)
ENTITY_TOKEN_RE = re.compile(r"[\u0590-\u05FF\"'\-]{2,}")
BROAD_QUERY_MARKERS = (
    "באיזה פרוטוקול",
    "איזה פרוטוקול",
    "באיזה נושא",
    "איזה נושא",
)
FOCUSED_DECISION_MARKERS = (
    "האם אושר",
    "האם אושרה",
    "האם נדחה",
    "מה אושר",
    "מה נדחה",
    "מה הוחלט לגבי",
)


@dataclass(slots=True)
class QueryRewriteResult:
    original_query: str
    rewritten_query: str
    lexical_terms: list[str] = field(default_factory=list)
    semantic_terms: list[str] = field(default_factory=list)
    header_terms: list[str] = field(default_factory=list)
    artifact_kind_priority: list[str] = field(default_factory=list)
    retrieval_strategy: str = "segments"
    use_neighbors: bool = False
    use_document_fallback: bool = True
    provider: str | None = None
    model: str | None = None
    route_reason: str | None = None


class QueryRewriteService:
    def __init__(self, *, llm_client: RagLlmClient | None = None):
        self.llm_client = llm_client or build_rag_llm_client()

    def rewrite_for_retrieval(self, *, query: str) -> QueryRewriteResult:
        deterministic = _deterministic_rewrite(query)
        provider_name = str(getattr(self.llm_client.provider, "provider_name", "") or "").strip().casefold()
        if provider_name != "ai21":
            return deterministic

        result = self.llm_client.generate(
            call_type=RAG_CALL_REWRITE,
            instruction=(
                "rewrite the hebrew municipal query into retrieval control json. "
                "infer likely header phrases, lexical terms, semantic terms, artifact kind priority, and retrieval strategy. "
                "return strict json with keys rewritten_query, lexical_terms, semantic_terms, header_terms, artifact_kind_priority, retrieval_strategy, use_neighbors, use_document_fallback, route_reason."
            ),
            payload={
                "query": query,
                "allowed_artifact_kinds": [
                    "document_profile",
                    "header_anchor",
                    "header_plus_opening",
                    "section_summary",
                    "decision_unit",
                    "section_unit",
                    "context_window",
                ],
            },
            temperature=0.0,
        )
        if result.error_code or not result.text:
            deterministic.route_reason = deterministic.route_reason or result.error_code
            return deterministic
        parsed = _parse_rewrite_payload(result.text)
        if parsed is None:
            return deterministic
        parsed.provider = result.provider
        parsed.model = result.model
        return parsed


def _deterministic_rewrite(query: str) -> QueryRewriteResult:
    compact = " ".join(str(query or "").split()).strip()
    normalized = normalize_for_search(compact)
    lexical_terms = _dedupe_preserve_order(_entity_terms(compact))
    semantic_terms = [term for term in lexical_terms if len(term.split()) >= 2][:8]
    header_terms = _dedupe_preserve_order([*semantic_terms, *[term for term in HEADER_HINT_TERMS if term in normalized]])

    retrieval_strategy = "segments"
    use_neighbors = False
    artifact_kind_priority = ["decision_unit", "section_unit", "header_plus_opening", "context_window", "header_anchor", "document_profile"]
    route_reason = "focused_decision_default"
    if any(marker in normalized for marker in (normalize_for_search(item) for item in FOCUSED_DECISION_MARKERS)):
        retrieval_strategy = "segments"
        use_neighbors = True
        artifact_kind_priority = ["decision_unit", "section_unit", "header_plus_opening", "context_window", "header_anchor", "document_profile"]
        route_reason = "focused_decision_marker"
    elif any(marker in normalized for marker in (normalize_for_search(item) for item in BROAD_QUERY_MARKERS)):
        retrieval_strategy = "headers"
        use_neighbors = True
        artifact_kind_priority = ["document_profile", "header_anchor", "header_plus_opening", "section_summary", "decision_unit", "section_unit", "context_window"]
        route_reason = "broad_query"
    elif any(term in normalized for term in ("פרוטוקול", "ועדה", "נושא", "סעיף")):
        retrieval_strategy = "headers"
        artifact_kind_priority = ["header_anchor", "header_plus_opening", "document_profile", "section_unit", "decision_unit", "context_window"]
        route_reason = "header_lookup"

    return QueryRewriteResult(
        original_query=compact,
        rewritten_query=compact,
        lexical_terms=lexical_terms,
        semantic_terms=semantic_terms,
        header_terms=header_terms,
        artifact_kind_priority=artifact_kind_priority,
        retrieval_strategy=retrieval_strategy,
        use_neighbors=use_neighbors,
        use_document_fallback=True,
        route_reason=route_reason,
    )


def _parse_rewrite_payload(value: str) -> QueryRewriteResult | None:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    original_query = " ".join(str(payload.get("rewritten_query") or "").split()).strip()
    if not original_query:
        return None
    strategy = str(payload.get("retrieval_strategy") or "segments").strip().casefold() or "segments"
    if strategy not in {"headers", "segments", "neighbors", "full_doc"}:
        strategy = "segments"
    artifact_kind_priority = [
        kind
        for kind in [str(item).strip() for item in payload.get("artifact_kind_priority") or []]
        if kind
    ]
    if not artifact_kind_priority:
        artifact_kind_priority = _deterministic_rewrite(original_query).artifact_kind_priority
    return QueryRewriteResult(
        original_query=original_query,
        rewritten_query=original_query,
        lexical_terms=_dedupe_preserve_order(_as_text_list(payload.get("lexical_terms"))),
        semantic_terms=_dedupe_preserve_order(_as_text_list(payload.get("semantic_terms"))),
        header_terms=_dedupe_preserve_order(_as_text_list(payload.get("header_terms"))),
        artifact_kind_priority=artifact_kind_priority,
        retrieval_strategy=strategy,
        use_neighbors=bool(payload.get("use_neighbors")),
        use_document_fallback=bool(payload.get("use_document_fallback", True)),
        route_reason=str(payload.get("route_reason") or "ai21_rewrite").strip() or "ai21_rewrite",
    )


def _entity_terms(value: str) -> list[str]:
    tokens = [match.group(0).strip('"\'') for match in ENTITY_TOKEN_RE.finditer(value)]
    out: list[str] = []
    if value:
        out.append(" ".join(value.split()))
    out.extend(tokens)
    return out


def _as_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_for_search(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(" ".join(value.split()))
    return out
