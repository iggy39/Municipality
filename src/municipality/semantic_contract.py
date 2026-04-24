from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


SEMANTIC_EXTRACTION_PROMPT_PREFIX = (
    "extract semantic topic and entity tree from hebrew municipal document with evidence offsets only"
)


class SemanticNodeKind(StrEnum):
    TOPIC = "topic"
    ENTITY = "entity"


class SemanticEdgeRelation(StrEnum):
    RELATED = "related"
    SAME_AS = "same_as"
    REPLACED_BY = "replaced_by"
    SUPPORTS = "supports"


class SemanticRelationRole(StrEnum):
    SUBJECT = "subject"
    OBJECT = "object"
    LOCATION = "location"
    BUDGET_SCOPE = "budget_scope"
    PROGRAM_SCOPE = "program_scope"


class SemanticRejectReason(StrEnum):
    EMPTY_LABEL = "EMPTY_LABEL"
    NUMERIC_ONLY_LABEL = "NUMERIC_ONLY_LABEL"
    STOPWORDS_ONLY = "STOPWORDS_ONLY"
    TOO_GENERIC = "TOO_GENERIC"
    SPAN_OUT_OF_RANGE = "SPAN_OUT_OF_RANGE"
    SPAN_TEXT_MISMATCH = "SPAN_TEXT_MISMATCH"
    PAGE_RESOLUTION_FAILED = "PAGE_RESOLUTION_FAILED"
    MERGE_REVIEW_NEEDED = "MERGE_REVIEW_NEEDED"
    HIERARCHY_INTEGRITY = "HIERARCHY_INTEGRITY"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"


class SemanticEvidenceCategory(StrEnum):
    DECISION = "decision"
    PLAN_PROGRAM = "plan_program"
    DISCUSSION = "discussion"
    POLICY = "policy"
    BUDGET_FINANCE = "budget_finance"
    IMPLEMENTATION = "implementation"
    PROCUREMENT_LEGAL = "procurement_legal"
    PUBLIC_FEEDBACK = "public_feedback"


@dataclass(slots=True)
class SemanticEvidenceSpanCandidate:
    span_id: str
    category: SemanticEvidenceCategory
    start_offset: int
    end_offset: int
    text: str
    confidence: float = 0.0
    regex_boost: float = 0.0
    hint_terms: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SemanticMentionCandidate:
    mention_text: str
    start_offset: int
    end_offset: int
    start_page: int | None = None
    end_page: int | None = None
    confidence: float | None = None


@dataclass(slots=True)
class SemanticAliasCandidate:
    alias_label_he: str
    alias_kind: str = "surface"
    confidence: float | None = None


@dataclass(slots=True)
class SemanticNodeCandidate:
    candidate_id: str
    label_he: str
    node_kind: SemanticNodeKind
    semantic_type: str
    parent_candidate_id: str | None = None
    confidence: float | None = None
    mentions: list[SemanticMentionCandidate] = field(default_factory=list)
    aliases: list[SemanticAliasCandidate] = field(default_factory=list)
    evidence_span_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SemanticEdgeCandidate:
    source_candidate_id: str
    target_candidate_id: str
    relation_type: SemanticEdgeRelation
    confidence: float | None = None
    provenance: str = "model"


@dataclass(slots=True)
class DecisionSemanticLinkCandidate:
    decision_id: int
    node_candidate_id: str
    relation_role: SemanticRelationRole
    confidence: float | None = None
    mention_index: int | None = None


@dataclass(slots=True)
class SemanticCandidateRejectRecord:
    candidate_label_he: str | None
    candidate_label_norm: str | None
    node_kind: str | None
    semantic_type: str | None
    reason_code: SemanticRejectReason
    model_confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SemanticExtractionOutput:
    evidence_spans: list[SemanticEvidenceSpanCandidate] = field(default_factory=list)
    nodes: list[SemanticNodeCandidate] = field(default_factory=list)
    edges: list[SemanticEdgeCandidate] = field(default_factory=list)
    decision_links: list[DecisionSemanticLinkCandidate] = field(default_factory=list)
    rejects: list[SemanticCandidateRejectRecord] = field(default_factory=list)
    refusal: str | None = None


@dataclass(slots=True)
class SemanticValidationIssue:
    code: str
    path: str
    message: str


@dataclass(slots=True)
class SemanticValidationReport:
    is_valid: bool
    issues: list[SemanticValidationIssue] = field(default_factory=list)


SEMANTIC_EXTRACTION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["evidence_spans", "nodes"],
    "properties": {
        "evidence_spans": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["span_id", "category", "start_offset", "end_offset", "text"],
                "properties": {
                    "span_id": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "decision",
                            "plan_program",
                            "discussion",
                            "policy",
                            "budget_finance",
                            "implementation",
                            "procurement_legal",
                            "public_feedback",
                        ],
                    },
                    "start_offset": {"type": "integer"},
                    "end_offset": {"type": "integer"},
                    "text": {"type": "string"},
                    "confidence": {"type": ["number", "null"]},
                    "regex_boost": {"type": ["number", "null"]},
                    "hint_terms": {"type": "array", "items": {"type": "string"}},
                    "metadata": {"type": ["object", "null"]},
                },
            },
        },
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["candidate_id", "label_he", "node_kind", "semantic_type", "mentions"],
                "properties": {
                    "candidate_id": {"type": "string"},
                    "label_he": {"type": "string"},
                    "node_kind": {"type": "string", "enum": ["topic", "entity"]},
                    "semantic_type": {"type": "string"},
                    "parent_candidate_id": {"type": ["string", "null"]},
                    "confidence": {"type": ["number", "null"]},
                    "mentions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["mention_text", "start_offset", "end_offset"],
                            "properties": {
                                "mention_text": {"type": "string"},
                                "start_offset": {"type": "integer"},
                                "end_offset": {"type": "integer"},
                                "start_page": {"type": ["integer", "null"]},
                                "end_page": {"type": ["integer", "null"]},
                                "confidence": {"type": ["number", "null"]},
                            },
                        },
                    },
                    "aliases": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["alias_label_he"],
                            "properties": {
                                "alias_label_he": {"type": "string"},
                                "alias_kind": {"type": ["string", "null"]},
                                "confidence": {"type": ["number", "null"]},
                            },
                        },
                    },
                    "evidence_span_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "metadata": {"type": ["object", "null"]},
                },
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["source_candidate_id", "target_candidate_id", "relation_type"],
                "properties": {
                    "source_candidate_id": {"type": "string"},
                    "target_candidate_id": {"type": "string"},
                    "relation_type": {"type": "string", "enum": ["related", "same_as", "replaced_by", "supports"]},
                    "confidence": {"type": ["number", "null"]},
                    "provenance": {"type": ["string", "null"]},
                },
            },
        },
        "decision_links": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["decision_id", "node_candidate_id", "relation_role"],
                "properties": {
                    "decision_id": {"type": "integer"},
                    "node_candidate_id": {"type": "string"},
                    "relation_role": {
                        "type": "string",
                        "enum": ["subject", "object", "location", "budget_scope", "program_scope"],
                    },
                    "confidence": {"type": ["number", "null"]},
                    "mention_index": {"type": ["integer", "null"]},
                },
            },
        },
        "rejects": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["reason_code"],
                "properties": {
                    "candidate_label_he": {"type": ["string", "null"]},
                    "candidate_label_norm": {"type": ["string", "null"]},
                    "node_kind": {"type": ["string", "null"]},
                    "semantic_type": {"type": ["string", "null"]},
                    "reason_code": {"type": "string"},
                    "model_confidence": {"type": ["number", "null"]},
                    "metadata": {"type": ["object", "null"]},
                },
            },
        },
        "refusal": {"type": ["string", "null"]},
    },
}


def parse_semantic_model_output(payload: dict[str, Any]) -> tuple[SemanticExtractionOutput, SemanticValidationReport]:
    issues: list[SemanticValidationIssue] = []
    output = SemanticExtractionOutput()

    output.evidence_spans.extend(_parse_evidence_spans(payload.get("evidence_spans"), issues=issues))
    evidence_span_ids = {span.span_id for span in output.evidence_spans}

    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        issues.append(SemanticValidationIssue(code="nodes_missing", path="nodes", message="nodes must be a list"))
        return output, SemanticValidationReport(is_valid=False, issues=issues)

    for idx, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, dict):
            issues.append(
                SemanticValidationIssue(code="node_invalid", path=f"nodes[{idx}]", message="node must be object")
            )
            continue
        candidate_id = _as_str(raw_node.get("candidate_id"))
        label_he = _as_str(raw_node.get("label_he"))
        node_kind_raw = _as_str(raw_node.get("node_kind"))
        semantic_type = _as_str(raw_node.get("semantic_type"))
        if not candidate_id or not label_he or not node_kind_raw or not semantic_type:
            issues.append(
                SemanticValidationIssue(
                    code="node_required_fields",
                    path=f"nodes[{idx}]",
                    message="candidate_id, label_he, node_kind, semantic_type are required",
                )
            )
            continue
        try:
            node_kind = SemanticNodeKind(node_kind_raw)
        except ValueError:
            issues.append(
                SemanticValidationIssue(
                    code="node_kind_invalid",
                    path=f"nodes[{idx}].node_kind",
                    message="node_kind must be topic or entity",
                )
            )
            continue

        mentions = _parse_mentions(raw_node.get("mentions"), node_idx=idx, issues=issues)
        aliases = _parse_aliases(raw_node.get("aliases"), node_idx=idx, issues=issues)

        output.nodes.append(
            SemanticNodeCandidate(
                candidate_id=candidate_id,
                label_he=label_he,
                node_kind=node_kind,
                semantic_type=semantic_type,
                parent_candidate_id=_as_str(raw_node.get("parent_candidate_id")),
                confidence=_as_float(raw_node.get("confidence"), default=None),
                mentions=mentions,
                aliases=aliases,
                evidence_span_ids=_as_str_list(raw_node.get("evidence_span_ids")),
                metadata=_as_dict(raw_node.get("metadata")),
            )
        )

    for idx, node in enumerate(output.nodes):
        missing_refs = [span_id for span_id in node.evidence_span_ids if span_id not in evidence_span_ids]
        if missing_refs:
            issues.append(
                SemanticValidationIssue(
                    code="node_evidence_refs_missing",
                    path=f"nodes[{idx}].evidence_span_ids",
                    message=f"unknown evidence_span_ids: {', '.join(missing_refs)}",
                )
            )

    output.edges.extend(_parse_edges(payload.get("edges"), issues=issues))
    output.decision_links.extend(_parse_decision_links(payload.get("decision_links"), issues=issues))
    output.rejects.extend(_parse_rejects(payload.get("rejects"), issues=issues))
    output.refusal = _as_str(payload.get("refusal"))

    return output, SemanticValidationReport(is_valid=not issues, issues=issues)


def _parse_evidence_spans(raw_value: Any, *, issues: list[SemanticValidationIssue]) -> list[SemanticEvidenceSpanCandidate]:
    if raw_value is None:
        issues.append(
            SemanticValidationIssue(
                code="evidence_spans_missing",
                path="evidence_spans",
                message="evidence_spans must be a list",
            )
        )
        return []
    if not isinstance(raw_value, list):
        issues.append(
            SemanticValidationIssue(
                code="evidence_spans_invalid",
                path="evidence_spans",
                message="evidence_spans must be a list",
            )
        )
        return []

    out: list[SemanticEvidenceSpanCandidate] = []
    for idx, raw_span in enumerate(raw_value):
        if not isinstance(raw_span, dict):
            issues.append(
                SemanticValidationIssue(
                    code="evidence_span_invalid",
                    path=f"evidence_spans[{idx}]",
                    message="evidence span must be object",
                )
            )
            continue

        span_id = _as_str(raw_span.get("span_id"))
        category_raw = _as_str(raw_span.get("category"))
        start_offset = _as_int(raw_span.get("start_offset"))
        end_offset = _as_int(raw_span.get("end_offset"))
        text = _as_str(raw_span.get("text"))
        if not span_id or not category_raw or start_offset is None or end_offset is None or text is None:
            issues.append(
                SemanticValidationIssue(
                    code="evidence_span_required_fields",
                    path=f"evidence_spans[{idx}]",
                    message="span_id, category, start_offset, end_offset, text are required",
                )
            )
            continue

        try:
            category = SemanticEvidenceCategory(category_raw)
        except ValueError:
            issues.append(
                SemanticValidationIssue(
                    code="evidence_span_category_invalid",
                    path=f"evidence_spans[{idx}].category",
                    message="category is invalid",
                )
            )
            continue

        regex_boost = _as_float(raw_span.get("regex_boost"), default=0.0) or 0.0
        out.append(
            SemanticEvidenceSpanCandidate(
                span_id=span_id,
                category=category,
                start_offset=start_offset,
                end_offset=end_offset,
                text=text,
                confidence=_as_float(raw_span.get("confidence"), default=0.0) or 0.0,
                regex_boost=max(0.0, min(0.12, regex_boost)),
                hint_terms=_as_str_list(raw_span.get("hint_terms")),
                metadata=_as_dict(raw_span.get("metadata")),
            )
        )

    return out


def _parse_mentions(raw_value: Any, *, node_idx: int, issues: list[SemanticValidationIssue]) -> list[SemanticMentionCandidate]:
    if raw_value is None:
        return []
    if not isinstance(raw_value, list):
        issues.append(
            SemanticValidationIssue(
                code="mentions_invalid",
                path=f"nodes[{node_idx}].mentions",
                message="mentions must be a list",
            )
        )
        return []

    mentions: list[SemanticMentionCandidate] = []
    for mention_idx, raw_mention in enumerate(raw_value):
        if not isinstance(raw_mention, dict):
            issues.append(
                SemanticValidationIssue(
                    code="mention_invalid",
                    path=f"nodes[{node_idx}].mentions[{mention_idx}]",
                    message="mention must be object",
                )
            )
            continue
        mention_text = _as_str(raw_mention.get("mention_text"))
        start_offset = _as_int(raw_mention.get("start_offset"))
        end_offset = _as_int(raw_mention.get("end_offset"))
        if mention_text is None or start_offset is None or end_offset is None:
            issues.append(
                SemanticValidationIssue(
                    code="mention_required_fields",
                    path=f"nodes[{node_idx}].mentions[{mention_idx}]",
                    message="mention_text, start_offset, end_offset are required",
                )
            )
            continue
        mentions.append(
            SemanticMentionCandidate(
                mention_text=mention_text,
                start_offset=start_offset,
                end_offset=end_offset,
                start_page=_as_int(raw_mention.get("start_page")),
                end_page=_as_int(raw_mention.get("end_page")),
                confidence=_as_float(raw_mention.get("confidence"), default=None),
            )
        )
    return mentions


def _parse_aliases(raw_value: Any, *, node_idx: int, issues: list[SemanticValidationIssue]) -> list[SemanticAliasCandidate]:
    if raw_value is None:
        return []
    if not isinstance(raw_value, list):
        issues.append(
            SemanticValidationIssue(
                code="aliases_invalid",
                path=f"nodes[{node_idx}].aliases",
                message="aliases must be a list",
            )
        )
        return []

    aliases: list[SemanticAliasCandidate] = []
    for alias_idx, raw_alias in enumerate(raw_value):
        if not isinstance(raw_alias, dict):
            issues.append(
                SemanticValidationIssue(
                    code="alias_invalid",
                    path=f"nodes[{node_idx}].aliases[{alias_idx}]",
                    message="alias must be object",
                )
            )
            continue
        alias_label_he = _as_str(raw_alias.get("alias_label_he"))
        if not alias_label_he:
            issues.append(
                SemanticValidationIssue(
                    code="alias_required_fields",
                    path=f"nodes[{node_idx}].aliases[{alias_idx}]",
                    message="alias_label_he is required",
                )
            )
            continue
        aliases.append(
            SemanticAliasCandidate(
                alias_label_he=alias_label_he,
                alias_kind=_as_str(raw_alias.get("alias_kind")) or "surface",
                confidence=_as_float(raw_alias.get("confidence"), default=None),
            )
        )
    return aliases


def _parse_edges(raw_value: Any, *, issues: list[SemanticValidationIssue]) -> list[SemanticEdgeCandidate]:
    if raw_value is None:
        return []
    if not isinstance(raw_value, list):
        issues.append(SemanticValidationIssue(code="edges_invalid", path="edges", message="edges must be a list"))
        return []

    out: list[SemanticEdgeCandidate] = []
    for idx, raw_edge in enumerate(raw_value):
        if not isinstance(raw_edge, dict):
            issues.append(SemanticValidationIssue(code="edge_invalid", path=f"edges[{idx}]", message="edge must be object"))
            continue
        source_candidate_id = _as_str(raw_edge.get("source_candidate_id"))
        target_candidate_id = _as_str(raw_edge.get("target_candidate_id"))
        relation_type = _as_str(raw_edge.get("relation_type"))
        if not source_candidate_id or not target_candidate_id or not relation_type:
            issues.append(
                SemanticValidationIssue(
                    code="edge_required_fields",
                    path=f"edges[{idx}]",
                    message="source_candidate_id, target_candidate_id, relation_type are required",
                )
            )
            continue
        try:
            parsed_relation = SemanticEdgeRelation(relation_type)
        except ValueError:
            issues.append(
                SemanticValidationIssue(
                    code="edge_relation_invalid",
                    path=f"edges[{idx}].relation_type",
                    message="relation_type is invalid",
                )
            )
            continue

        out.append(
            SemanticEdgeCandidate(
                source_candidate_id=source_candidate_id,
                target_candidate_id=target_candidate_id,
                relation_type=parsed_relation,
                confidence=_as_float(raw_edge.get("confidence"), default=None),
                provenance=_as_str(raw_edge.get("provenance")) or "model",
            )
        )
    return out


def _parse_decision_links(raw_value: Any, *, issues: list[SemanticValidationIssue]) -> list[DecisionSemanticLinkCandidate]:
    if raw_value is None:
        return []
    if not isinstance(raw_value, list):
        issues.append(
            SemanticValidationIssue(code="decision_links_invalid", path="decision_links", message="decision_links must be a list")
        )
        return []

    out: list[DecisionSemanticLinkCandidate] = []
    for idx, raw_link in enumerate(raw_value):
        if not isinstance(raw_link, dict):
            issues.append(
                SemanticValidationIssue(
                    code="decision_link_invalid",
                    path=f"decision_links[{idx}]",
                    message="decision link must be object",
                )
            )
            continue
        decision_id = _as_int(raw_link.get("decision_id"))
        node_candidate_id = _as_str(raw_link.get("node_candidate_id"))
        relation_role = _as_str(raw_link.get("relation_role"))
        if decision_id is None or not node_candidate_id or not relation_role:
            issues.append(
                SemanticValidationIssue(
                    code="decision_link_required_fields",
                    path=f"decision_links[{idx}]",
                    message="decision_id, node_candidate_id, relation_role are required",
                )
            )
            continue
        try:
            parsed_role = SemanticRelationRole(relation_role)
        except ValueError:
            issues.append(
                SemanticValidationIssue(
                    code="decision_link_role_invalid",
                    path=f"decision_links[{idx}].relation_role",
                    message="relation_role is invalid",
                )
            )
            continue
        out.append(
            DecisionSemanticLinkCandidate(
                decision_id=decision_id,
                node_candidate_id=node_candidate_id,
                relation_role=parsed_role,
                confidence=_as_float(raw_link.get("confidence"), default=None),
                mention_index=_as_int(raw_link.get("mention_index")),
            )
        )
    return out


def _parse_rejects(raw_value: Any, *, issues: list[SemanticValidationIssue]) -> list[SemanticCandidateRejectRecord]:
    if raw_value is None:
        return []
    if not isinstance(raw_value, list):
        issues.append(SemanticValidationIssue(code="rejects_invalid", path="rejects", message="rejects must be a list"))
        return []

    out: list[SemanticCandidateRejectRecord] = []
    for idx, raw_reject in enumerate(raw_value):
        if not isinstance(raw_reject, dict):
            issues.append(
                SemanticValidationIssue(
                    code="reject_invalid",
                    path=f"rejects[{idx}]",
                    message="reject must be object",
                )
            )
            continue
        reason_code = _as_str(raw_reject.get("reason_code"))
        if not reason_code:
            issues.append(
                SemanticValidationIssue(
                    code="reject_required_fields",
                    path=f"rejects[{idx}]",
                    message="reason_code is required",
                )
            )
            continue
        try:
            parsed_reason = SemanticRejectReason(reason_code)
        except ValueError:
            parsed_reason = SemanticRejectReason.UNSUPPORTED_SCHEMA
            issues.append(
                SemanticValidationIssue(
                    code="reject_reason_unknown",
                    path=f"rejects[{idx}].reason_code",
                    message="reason_code is unknown; normalized to UNSUPPORTED_SCHEMA",
                )
            )
        out.append(
            SemanticCandidateRejectRecord(
                candidate_label_he=_as_str(raw_reject.get("candidate_label_he")),
                candidate_label_norm=_as_str(raw_reject.get("candidate_label_norm")),
                node_kind=_as_str(raw_reject.get("node_kind")),
                semantic_type=_as_str(raw_reject.get("semantic_type")),
                reason_code=parsed_reason,
                model_confidence=_as_float(raw_reject.get("model_confidence"), default=None),
                metadata=_as_dict(raw_reject.get("metadata")),
            )
        )
    return out


def _as_str(value: Any) -> str | None:
    if isinstance(value, str):
        compact = value.strip()
        return compact or None
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        compact = value.strip()
        if compact and compact.lstrip("-").isdigit():
            return int(compact)
    return None


def _as_float(value: Any, *, default: float | None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        compact = value.strip()
        if not compact:
            return default
        try:
            return float(compact)
        except ValueError:
            return default
    return default


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        parsed = _as_str(item)
        if parsed is not None:
            out.append(parsed)
    return out


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}
