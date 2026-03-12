from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(slots=True)
class RagEvidenceReference:
    source_kind: str
    chunk_id: str
    document_id: int
    document_version_id: int | None = None
    citation_label: str | None = None


@dataclass(slots=True)
class RagExpectedGroundedAnswer:
    answer_must_include: list[str] = field(default_factory=list)
    required_chunk_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RagExpectedRefusalCase:
    missing_source_kind: str
    reason_code: str


@dataclass(slots=True)
class RagExpectedRefusal:
    must_include: list[str] = field(default_factory=list)
    cases: list[RagExpectedRefusalCase] = field(default_factory=list)


@dataclass(slots=True)
class RagEvalCase:
    case_id: str
    question: str
    top_k: int
    required_evidence: list[RagEvidenceReference]
    supporting_evidence: list[RagEvidenceReference]
    expected_grounded: RagExpectedGroundedAnswer
    expected_refusal: RagExpectedRefusal
    muni: str | None = None
    year: int | None = None
    topic: str | None = None
    semantic_mode: str = "off"


@dataclass(slots=True)
class RagEvalThresholds:
    citation_correctness_min: float = 1.0
    answer_correctness_min: float = 1.0
    refusal_correctness_min: float = 1.0


@dataclass(slots=True)
class RagEvalSet:
    description: str | None
    cases: list[RagEvalCase]
    thresholds: RagEvalThresholds


@dataclass(slots=True)
class RagEvalCaseResult:
    case_id: str
    answer_status: str
    citation_ok: bool
    answer_ok: bool
    required_chunk_ids: list[str]
    returned_chunk_ids: list[str]
    refusal_cases_total: int
    refusal_cases_passed: int
    refusal_failures: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RagEvalSummary:
    total_cases: int
    citation_correctness: float
    answer_correctness: float
    refusal_correctness: float
    evaluated_refusal_cases: int
    passed_refusal_cases: int
    bootstrap_case_id: str | None
    bootstrap_traceable: bool
    thresholds_passed: bool


def load_rag_eval_set(path: Path) -> RagEvalSet:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases: list[RagEvalCase] = []

    for row in payload.get("cases", []):
        required_evidence = _parse_evidence_references(row.get("required_evidence"))
        supporting_evidence = _parse_evidence_references(row.get("supporting_evidence"))

        grounded_payload = row.get("expected_grounded", {})
        grounded = RagExpectedGroundedAnswer(
            answer_must_include=_as_str_list(
                grounded_payload.get("answer_must_include") if isinstance(grounded_payload, dict) else None
            ),
            required_chunk_ids=_as_str_list(
                grounded_payload.get("required_chunk_ids") if isinstance(grounded_payload, dict) else None
            ),
        )

        refusal_payload = row.get("expected_refusal", {})
        refusal_cases: list[RagExpectedRefusalCase] = []
        if isinstance(refusal_payload, dict):
            for refusal_row in refusal_payload.get("cases", []):
                if not isinstance(refusal_row, dict):
                    continue
                missing_source_kind = _as_str(refusal_row.get("missing_source_kind"))
                reason_code = _as_str(refusal_row.get("reason_code"))
                if not missing_source_kind or not reason_code:
                    continue
                refusal_cases.append(
                    RagExpectedRefusalCase(
                        missing_source_kind=missing_source_kind,
                        reason_code=reason_code,
                    )
                )
        refusal = RagExpectedRefusal(
            must_include=_as_str_list(refusal_payload.get("must_include") if isinstance(refusal_payload, dict) else None),
            cases=refusal_cases,
        )

        case_id = _as_str(row.get("case_id"))
        question = _as_str(row.get("question"))
        if not case_id or not question:
            continue

        cases.append(
            RagEvalCase(
                case_id=case_id,
                question=question,
                top_k=max(1, _as_int(row.get("top_k")) or 5),
                required_evidence=required_evidence,
                supporting_evidence=supporting_evidence,
                expected_grounded=grounded,
                expected_refusal=refusal,
                muni=_as_str(row.get("muni")),
                year=_as_int(row.get("year")),
                topic=_as_str(row.get("topic")),
                semantic_mode=_as_str(row.get("semantic_mode")) or "off",
            )
        )

    thresholds_payload = payload.get("thresholds")
    thresholds_dict = thresholds_payload if isinstance(thresholds_payload, dict) else {}
    thresholds = RagEvalThresholds(
        citation_correctness_min=_as_float(thresholds_dict.get("citation_correctness_min"), 1.0),
        answer_correctness_min=_as_float(thresholds_dict.get("answer_correctness_min"), 1.0),
        refusal_correctness_min=_as_float(thresholds_dict.get("refusal_correctness_min"), 1.0),
    )

    return RagEvalSet(
        description=_as_str(payload.get("description")),
        cases=cases,
        thresholds=thresholds,
    )


def required_source_kinds(case: RagEvalCase) -> set[str]:
    return {evidence.source_kind for evidence in case.required_evidence}


def retrieval_source_kinds(case: RagEvalCase) -> set[str]:
    kinds: set[str] = set()
    kinds.update(required_source_kinds(case))
    kinds.update(evidence.source_kind for evidence in case.supporting_evidence)
    return kinds


def evaluate_rag_eval_set(
    *,
    eval_set: RagEvalSet,
    ask_fn: Callable[..., Mapping[str, Any]],
) -> tuple[RagEvalSummary, list[RagEvalCaseResult]]:
    case_results: list[RagEvalCaseResult] = []

    citation_passed = 0
    answer_passed = 0
    refusal_total = 0
    refusal_passed = 0

    for case in eval_set.cases:
        required_sources = sorted(required_source_kinds(case))
        retrieval_sources = sorted(retrieval_source_kinds(case))
        required_chunk_ids = _required_chunk_ids_for_case(case)

        response = ask_fn(
            question=case.question,
            top_k=case.top_k,
            source_types=retrieval_sources or None,
            required_source_types=required_sources or None,
            muni=case.muni,
            year=case.year,
            topic=case.topic,
            semantic_mode=case.semantic_mode,
        )

        answer_status = _as_str(response.get("status")) or ""
        returned_chunk_ids = _citation_chunk_ids(response.get("citations"))
        citation_ok = answer_status == "answer" and _contains_all(required_chunk_ids, returned_chunk_ids)

        answer_text = _as_str(response.get("answer")) or ""
        answer_ok = answer_status == "answer" and _contains_all_fragments(
            answer_text,
            case.expected_grounded.answer_must_include,
        )

        case_refusal_total, case_refusal_passed, refusal_failures = _evaluate_refusal_cases(
            case=case,
            required_sources=required_sources,
            retrieval_sources=retrieval_sources,
            ask_fn=ask_fn,
        )

        refusal_total += case_refusal_total
        refusal_passed += case_refusal_passed
        if citation_ok:
            citation_passed += 1
        if answer_ok:
            answer_passed += 1

        case_results.append(
            RagEvalCaseResult(
                case_id=case.case_id,
                answer_status=answer_status,
                citation_ok=citation_ok,
                answer_ok=answer_ok,
                required_chunk_ids=required_chunk_ids,
                returned_chunk_ids=returned_chunk_ids,
                refusal_cases_total=case_refusal_total,
                refusal_cases_passed=case_refusal_passed,
                refusal_failures=refusal_failures,
            )
        )

    total_cases = len(case_results)
    citation_correctness = (citation_passed / total_cases) if total_cases else 1.0
    answer_correctness = (answer_passed / total_cases) if total_cases else 1.0
    refusal_correctness = (refusal_passed / refusal_total) if refusal_total else 1.0

    bootstrap_case_id = eval_set.cases[0].case_id if eval_set.cases else None
    bootstrap_traceable = False
    if eval_set.cases and case_results:
        first_case = eval_set.cases[0]
        first_result = case_results[0]
        bootstrap_traceable = bool(first_case.required_evidence) and first_result.citation_ok

    thresholds = eval_set.thresholds
    thresholds_passed = (
        citation_correctness >= thresholds.citation_correctness_min
        and answer_correctness >= thresholds.answer_correctness_min
        and refusal_correctness >= thresholds.refusal_correctness_min
    )

    summary = RagEvalSummary(
        total_cases=total_cases,
        citation_correctness=round(citation_correctness, 4),
        answer_correctness=round(answer_correctness, 4),
        refusal_correctness=round(refusal_correctness, 4),
        evaluated_refusal_cases=refusal_total,
        passed_refusal_cases=refusal_passed,
        bootstrap_case_id=bootstrap_case_id,
        bootstrap_traceable=bootstrap_traceable,
        thresholds_passed=thresholds_passed,
    )
    return summary, case_results


def _required_chunk_ids_for_case(case: RagEvalCase) -> list[str]:
    if case.expected_grounded.required_chunk_ids:
        return list(case.expected_grounded.required_chunk_ids)
    return [evidence.chunk_id for evidence in case.required_evidence]


def _citation_chunk_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        chunk_id = _as_str(item.get("chunk_id"))
        if not chunk_id or chunk_id in seen:
            continue
        out.append(chunk_id)
        seen.add(chunk_id)
    return out


def _contains_all(expected: list[str], actual: list[str]) -> bool:
    if not expected:
        return True
    return set(expected).issubset(set(actual))


def _contains_all_fragments(answer_text: str, fragments: list[str]) -> bool:
    if not fragments:
        return True
    if not answer_text.strip():
        return False
    return all(fragment in answer_text for fragment in fragments)


def _evaluate_refusal_cases(
    *,
    case: RagEvalCase,
    required_sources: list[str],
    retrieval_sources: list[str],
    ask_fn: Callable[..., Mapping[str, Any]],
) -> tuple[int, int, list[str]]:
    total = 0
    passed = 0
    failures: list[str] = []

    for refusal_case in case.expected_refusal.cases:
        total += 1
        source_types = [source_kind for source_kind in retrieval_sources if source_kind != refusal_case.missing_source_kind]
        if not source_types:
            source_types = [
                source_kind
                for source_kind in ["protocol", "attachment", "other"]
                if source_kind != refusal_case.missing_source_kind
            ]
        response = ask_fn(
            question=case.question,
            top_k=case.top_k,
            source_types=source_types,
            required_source_types=required_sources or None,
            muni=case.muni,
            year=case.year,
            topic=case.topic,
            semantic_mode=case.semantic_mode,
        )

        if _refusal_response_matches(
            response=response,
            refusal_case=refusal_case,
            expected_message_fragments=case.expected_refusal.must_include,
        ):
            passed += 1
        else:
            failures.append(refusal_case.reason_code)

    return total, passed, failures


def _refusal_response_matches(
    *,
    response: Mapping[str, Any],
    refusal_case: RagExpectedRefusalCase,
    expected_message_fragments: list[str],
) -> bool:
    if (_as_str(response.get("status")) or "") != "refusal":
        return False

    refusal_payload = response.get("refusal")
    if not isinstance(refusal_payload, dict):
        return False

    reason_code = _as_str(refusal_payload.get("reason_code"))
    if reason_code != refusal_case.reason_code:
        return False

    missing_source_types = _as_str_list(refusal_payload.get("missing_source_types"))
    if refusal_case.missing_source_kind not in missing_source_types:
        return False

    refusal_message = _as_str(refusal_payload.get("message_he")) or ""
    if not _contains_all_fragments(refusal_message, expected_message_fragments):
        return False

    return True


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return str(value)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any, default: float) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        normalized = _as_str(item)
        if normalized:
            out.append(normalized)
    return out


def _parse_evidence_references(value: object) -> list[RagEvidenceReference]:
    if not isinstance(value, list):
        return []

    rows: list[RagEvidenceReference] = []
    for evidence in value:
        if not isinstance(evidence, dict):
            continue
        chunk_id = _as_str(evidence.get("chunk_id"))
        source_kind = _as_str(evidence.get("source_kind"))
        document_id = _as_int(evidence.get("document_id"))
        if not chunk_id or not source_kind or document_id is None:
            continue
        rows.append(
            RagEvidenceReference(
                source_kind=source_kind,
                chunk_id=chunk_id,
                document_id=document_id,
                document_version_id=_as_int(evidence.get("document_version_id")),
                citation_label=_as_str(evidence.get("citation_label")),
            )
        )
    return rows
