from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.chunking import normalize_for_search
from municipality.models import SemanticCandidateReject, SemanticDocumentRun, SemanticMention, SemanticNode


@dataclass(slots=True)
class SemanticExpectedNode:
    label: str
    aliases: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SemanticExpectedEvidence:
    chunk_id: str | None = None
    source_kind: str | None = None


@dataclass(slots=True)
class SemanticRetrievalCase:
    case_id: str
    query: str
    top_k: int
    semantic_mode: str = "boost"
    semantic_node_id: int | None = None
    semantic_label: str | None = None
    filter_municipality: str | None = None
    filter_source_type: str | None = None
    filter_year: int | None = None
    filter_topic: str | None = None
    expected_document_title_contains: str | None = None
    expected_source_type: str | None = None
    expected_semantic_nodes: list[SemanticExpectedNode] = field(default_factory=list)
    expected_evidence: list[SemanticExpectedEvidence] = field(default_factory=list)
    expected_min_semantic_match_count: int = 1


@dataclass(slots=True)
class SemanticNegativeCase:
    case_id: str
    query: str
    expected_reject_reason: str
    disallowed_node_labels: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SemanticRunQualityThresholds:
    one_call_compliance_min: float = 1.0
    evidence_backed_active_nodes_min: float = 1.0
    semantic_hit_rate_min: float = 0.8
    retrieval_lift_min: float = 0.0


@dataclass(slots=True)
class SemanticEvalSet:
    retrieval_cases: list[SemanticRetrievalCase]
    negative_cases: list[SemanticNegativeCase]
    run_quality_thresholds: SemanticRunQualityThresholds


@dataclass(slots=True)
class SemanticRetrievalCaseResult:
    case_id: str
    query: str
    semantic_matched: bool
    lexical_matched: bool
    semantic_result_count: int
    lexical_result_count: int
    semantic_top_score: float
    lexical_top_score: float


@dataclass(slots=True)
class SemanticEvalSummary:
    total_retrieval_cases: int
    semantic_matched_cases: int
    lexical_matched_cases: int
    semantic_hit_rate: float
    lexical_hit_rate: float
    retrieval_lift: float
    one_call_compliance_rate: float
    evidence_backed_active_node_rate: float
    duplicate_active_node_rate: float
    avg_active_specificity: float
    negative_reject_hit_rate: float
    thresholds_passed: bool


def load_semantic_eval_set(path: Path) -> SemanticEvalSet:
    payload = json.loads(path.read_text(encoding="utf-8"))

    retrieval_cases: list[SemanticRetrievalCase] = []
    for row in payload.get("retrieval_cases", []):
        expected_nodes: list[SemanticExpectedNode] = []
        for expected_node in row.get("expected_semantic_nodes", []):
            if not isinstance(expected_node, dict):
                continue
            label = _as_str(expected_node.get("label"))
            if not label:
                continue
            expected_nodes.append(
                SemanticExpectedNode(
                    label=label,
                    aliases=_as_str_list(expected_node.get("aliases")),
                )
            )

        expected_evidence: list[SemanticExpectedEvidence] = []
        for evidence in row.get("expected_evidence", []):
            if not isinstance(evidence, dict):
                continue
            expected_evidence.append(
                SemanticExpectedEvidence(
                    chunk_id=_as_str(evidence.get("chunk_id")),
                    source_kind=_as_str(evidence.get("source_kind")),
                )
            )

        retrieval_cases.append(
            SemanticRetrievalCase(
                case_id=str(row["case_id"]),
                query=str(row["query"]),
                top_k=max(1, int(row.get("top_k", 5))),
                semantic_mode=_as_str(row.get("semantic_mode")) or "boost",
                semantic_node_id=_as_int(row.get("semantic_node_id")),
                semantic_label=_as_str(row.get("semantic_label")),
                filter_municipality=_as_str(row.get("filter_municipality")),
                filter_source_type=_as_str(row.get("filter_source_type")),
                filter_year=_as_int(row.get("filter_year")),
                filter_topic=_as_str(row.get("filter_topic")),
                expected_document_title_contains=_as_str(row.get("expected_document_title_contains")),
                expected_source_type=_as_str(row.get("expected_source_type")),
                expected_semantic_nodes=expected_nodes,
                expected_evidence=expected_evidence,
                expected_min_semantic_match_count=max(0, int(row.get("expected_min_semantic_match_count", 1))),
            )
        )

    negative_cases: list[SemanticNegativeCase] = []
    for row in payload.get("negative_cases", []):
        negative_cases.append(
            SemanticNegativeCase(
                case_id=str(row["case_id"]),
                query=str(row["query"]),
                expected_reject_reason=str(row["expected_reject_reason"]),
                disallowed_node_labels=_as_str_list(row.get("disallowed_node_labels")),
            )
        )

    threshold_payload = payload.get("run_quality_thresholds", {})
    thresholds = SemanticRunQualityThresholds(
        one_call_compliance_min=float(threshold_payload.get("one_call_compliance_min", 1.0)),
        evidence_backed_active_nodes_min=float(threshold_payload.get("evidence_backed_active_nodes_min", 1.0)),
        semantic_hit_rate_min=float(threshold_payload.get("semantic_hit_rate_min", 0.8)),
        retrieval_lift_min=float(threshold_payload.get("retrieval_lift_min", 0.0)),
    )

    return SemanticEvalSet(
        retrieval_cases=retrieval_cases,
        negative_cases=negative_cases,
        run_quality_thresholds=thresholds,
    )


def evaluate_semantic_eval_set(
    *,
    search_service,
    session: Session,
    eval_set: SemanticEvalSet,
) -> tuple[SemanticEvalSummary, list[SemanticRetrievalCaseResult]]:
    retrieval_results: list[SemanticRetrievalCaseResult] = []
    semantic_matched = 0
    lexical_matched = 0

    for case in eval_set.retrieval_cases:
        semantic_hits = search_service.search(
            query=case.query,
            municipality_slug=case.filter_municipality,
            source_type=case.filter_source_type,
            year=case.filter_year,
            topic=case.filter_topic,
            semantic_node_id=case.semantic_node_id,
            semantic_label=case.semantic_label,
            semantic_mode=case.semantic_mode,
            limit=case.top_k,
        )
        lexical_hits = search_service.search(
            query=case.query,
            municipality_slug=case.filter_municipality,
            source_type=case.filter_source_type,
            year=case.filter_year,
            topic=case.filter_topic,
            semantic_node_id=case.semantic_node_id,
            semantic_label=case.semantic_label,
            semantic_mode="off",
            limit=case.top_k,
        )

        semantic_case_match = _retrieval_case_matches(
            hits=semantic_hits,
            case=case,
            require_semantic=True,
        )
        lexical_case_match = _retrieval_case_matches(
            hits=lexical_hits,
            case=case,
            require_semantic=False,
        )
        if semantic_case_match:
            semantic_matched += 1
        if lexical_case_match:
            lexical_matched += 1

        retrieval_results.append(
            SemanticRetrievalCaseResult(
                case_id=case.case_id,
                query=case.query,
                semantic_matched=semantic_case_match,
                lexical_matched=lexical_case_match,
                semantic_result_count=len(semantic_hits),
                lexical_result_count=len(lexical_hits),
                semantic_top_score=(semantic_hits[0].score if semantic_hits else 0.0),
                lexical_top_score=(lexical_hits[0].score if lexical_hits else 0.0),
            )
        )

    total_retrieval_cases = len(eval_set.retrieval_cases)
    semantic_hit_rate = (semantic_matched / total_retrieval_cases) if total_retrieval_cases else 1.0
    lexical_hit_rate = (lexical_matched / total_retrieval_cases) if total_retrieval_cases else 1.0
    retrieval_lift = semantic_hit_rate - lexical_hit_rate

    one_call_compliance_rate = _one_call_compliance_rate(session)
    evidence_backed_active_node_rate = _evidence_backed_active_node_rate(session)
    duplicate_active_node_rate = _duplicate_active_node_rate(session)
    avg_active_specificity = _avg_active_specificity(session)
    negative_reject_hit_rate = _negative_reject_hit_rate(session, eval_set.negative_cases)

    thresholds = eval_set.run_quality_thresholds
    thresholds_passed = (
        one_call_compliance_rate >= thresholds.one_call_compliance_min
        and evidence_backed_active_node_rate >= thresholds.evidence_backed_active_nodes_min
        and semantic_hit_rate >= thresholds.semantic_hit_rate_min
        and retrieval_lift >= thresholds.retrieval_lift_min
    )

    summary = SemanticEvalSummary(
        total_retrieval_cases=total_retrieval_cases,
        semantic_matched_cases=semantic_matched,
        lexical_matched_cases=lexical_matched,
        semantic_hit_rate=round(semantic_hit_rate, 4),
        lexical_hit_rate=round(lexical_hit_rate, 4),
        retrieval_lift=round(retrieval_lift, 4),
        one_call_compliance_rate=round(one_call_compliance_rate, 4),
        evidence_backed_active_node_rate=round(evidence_backed_active_node_rate, 4),
        duplicate_active_node_rate=round(duplicate_active_node_rate, 4),
        avg_active_specificity=round(avg_active_specificity, 4),
        negative_reject_hit_rate=round(negative_reject_hit_rate, 4),
        thresholds_passed=thresholds_passed,
    )
    return summary, retrieval_results


def _retrieval_case_matches(*, hits: list, case: SemanticRetrievalCase, require_semantic: bool) -> bool:
    expected_labels = {
        normalize_for_search(node.label)
        for node in case.expected_semantic_nodes
        if normalize_for_search(node.label)
    }
    for node in case.expected_semantic_nodes:
        expected_labels.update(
            normalize_for_search(alias)
            for alias in node.aliases
            if normalize_for_search(alias)
        )

    required_semantic_count = (
        case.expected_min_semantic_match_count
        if case.expected_min_semantic_match_count > 0
        else 1
    )

    for hit in hits[: case.top_k]:
        if case.expected_source_type and hit.source_type != case.expected_source_type:
            continue
        if case.expected_document_title_contains and case.expected_document_title_contains not in hit.document_title:
            continue
        if case.expected_evidence and not _hit_matches_expected_evidence(hit=hit, expected_evidence=case.expected_evidence):
            continue

        if require_semantic:
            if hit.semantic_match_count < required_semantic_count:
                continue
            if expected_labels:
                hit_labels = {
                    normalize_for_search(node.label)
                    for node in hit.semantic_nodes
                    if normalize_for_search(node.label)
                }
                if hit_labels.isdisjoint(expected_labels):
                    continue

        return True
    return False


def _hit_matches_expected_evidence(*, hit, expected_evidence: list[SemanticExpectedEvidence]) -> bool:
    for evidence in expected_evidence:
        chunk_ok = evidence.chunk_id is None or hit.chunk_id == evidence.chunk_id
        source_ok = evidence.source_kind is None or hit.source_type == evidence.source_kind
        if chunk_ok and source_ok:
            return True
    return False


def _one_call_compliance_rate(session: Session) -> float:
    completed_runs = session.execute(
        select(SemanticDocumentRun).where(SemanticDocumentRun.status == "completed")
    ).scalars().all()
    if not completed_runs:
        return 1.0
    compliant = sum(1 for run in completed_runs if run.api_call_count <= 1)
    return compliant / len(completed_runs)


def _evidence_backed_active_node_rate(session: Session) -> float:
    active_node_ids = session.execute(
        select(SemanticNode.id).where(SemanticNode.status == "active")
    ).scalars().all()
    if not active_node_ids:
        return 1.0

    mention_node_ids = set(
        session.execute(
            select(SemanticMention.semantic_node_id).where(
                SemanticMention.semantic_node_id.in_(active_node_ids)
            )
        ).scalars().all()
    )
    backed_count = sum(1 for node_id in active_node_ids if node_id in mention_node_ids)
    return backed_count / len(active_node_ids)


def _duplicate_active_node_rate(session: Session) -> float:
    active_nodes = session.execute(
        select(SemanticNode).where(SemanticNode.status == "active")
    ).scalars().all()
    if not active_nodes:
        return 0.0

    by_key: dict[tuple[int, int | None, str, str, str], int] = {}
    for node in active_nodes:
        key = (
            node.source_site_id,
            node.parent_node_id,
            node.node_kind,
            node.semantic_type,
            node.pref_label_norm,
        )
        by_key[key] = by_key.get(key, 0) + 1

    duplicates = sum(count - 1 for count in by_key.values() if count > 1)
    return duplicates / len(active_nodes)


def _avg_active_specificity(session: Session) -> float:
    active_scores = session.execute(
        select(SemanticNode.specificity_score).where(SemanticNode.status == "active")
    ).scalars().all()
    if not active_scores:
        return 0.0
    return sum(float(score) for score in active_scores) / len(active_scores)


def _negative_reject_hit_rate(session: Session, cases: list[SemanticNegativeCase]) -> float:
    if not cases:
        return 1.0

    reject_rows = session.execute(
        select(SemanticCandidateReject.reason_code, SemanticCandidateReject.candidate_label_norm)
    ).all()

    matched_cases = 0
    for case in cases:
        query_norm = normalize_for_search(case.query)
        disallowed_norms = {
            normalize_for_search(label)
            for label in case.disallowed_node_labels
            if normalize_for_search(label)
        }
        case_matched = False

        for reason_code, candidate_label_norm in reject_rows:
            if reason_code != case.expected_reject_reason:
                continue
            label_norm = normalize_for_search(candidate_label_norm or "")
            if disallowed_norms and label_norm in disallowed_norms:
                case_matched = True
                break
            if query_norm and label_norm and (
                query_norm == label_norm or query_norm in label_norm or label_norm in query_norm
            ):
                case_matched = True
                break

        if case_matched:
            matched_cases += 1

    return matched_cases / len(cases)


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return str(value)


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        normalized = _as_str(item)
        if normalized:
            out.append(normalized)
    return out
