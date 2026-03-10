from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


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
    expected_grounded: RagExpectedGroundedAnswer
    expected_refusal: RagExpectedRefusal


@dataclass(slots=True)
class RagEvalSet:
    description: str | None
    cases: list[RagEvalCase]


def load_rag_eval_set(path: Path) -> RagEvalSet:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases: list[RagEvalCase] = []

    for row in payload.get("cases", []):
        required_evidence: list[RagEvidenceReference] = []
        for evidence in row.get("required_evidence", []):
            if not isinstance(evidence, dict):
                continue
            chunk_id = _as_str(evidence.get("chunk_id"))
            source_kind = _as_str(evidence.get("source_kind"))
            document_id = _as_int(evidence.get("document_id"))
            if not chunk_id or not source_kind or document_id is None:
                continue

            required_evidence.append(
                RagEvidenceReference(
                    source_kind=source_kind,
                    chunk_id=chunk_id,
                    document_id=document_id,
                    document_version_id=_as_int(evidence.get("document_version_id")),
                    citation_label=_as_str(evidence.get("citation_label")),
                )
            )

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
                expected_grounded=grounded,
                expected_refusal=refusal,
            )
        )

    return RagEvalSet(
        description=_as_str(payload.get("description")),
        cases=cases,
    )


def required_source_kinds(case: RagEvalCase) -> set[str]:
    return {evidence.source_kind for evidence in case.required_evidence}


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return str(value)


def _as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
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
