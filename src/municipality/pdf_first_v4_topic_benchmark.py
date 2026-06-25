from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from municipality.chunking import normalize_for_search
from municipality.topic_label_quality import is_low_quality_topic_label


BENCHMARK_SCHEMA_VERSION = "pdf_first_v4_step4_hard_rows_v1"
PROCEDURAL_ROOT_IDS = {"root_agenda_queries", "root_order_proposals"}


@dataclass(frozen=True, slots=True)
class TopicAssignmentSnapshot:
    source_path: str
    assignments: list[dict[str, Any]]
    items_by_id: dict[str, dict[str, Any]]


def load_topic_assignment_snapshot(path: str | Path) -> TopicAssignmentSnapshot:
    source_path = str(Path(path).expanduser().resolve())
    payload = json.loads(Path(source_path).read_text(encoding="utf-8"))
    assignments = [row for row in payload.get("topic_assignments") or [] if isinstance(row, dict)]
    items_by_id = {
        str(row.get("structure_unit_id") or ""): row
        for row in payload.get("items") or []
        if isinstance(row, dict) and str(row.get("structure_unit_id") or "")
    }
    return TopicAssignmentSnapshot(source_path=source_path, assignments=assignments, items_by_id=items_by_id)


def build_hard_row_benchmark(*, snapshots: list[TopicAssignmentSnapshot], existing_cases: list[dict[str, Any]] | None = None, limit: int | None = None) -> dict[str, Any]:
    existing_by_key = {_case_key(row): row for row in existing_cases or [] if _case_key(row)}
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for snapshot in snapshots:
        for assignment in snapshot.assignments:
            reasons = hard_row_reasons(assignment)
            if not reasons:
                continue
            case = _case_from_assignment(snapshot=snapshot, assignment=assignment, reasons=reasons)
            key = _case_key(case)
            if not key or key in seen:
                continue
            seen.add(key)
            previous = existing_by_key.get(key) or {}
            case["expected"] = dict(previous.get("expected") or _empty_expected())
            case["judge_notes"] = str(previous.get("judge_notes") or "")
            cases.append(case)
            if limit is not None and len(cases) >= max(0, int(limit)):
                return _benchmark_payload(cases)
    return _benchmark_payload(cases)


def score_hard_row_benchmark(*, benchmark: dict[str, Any], snapshots: list[TopicAssignmentSnapshot]) -> dict[str, Any]:
    predictions = _prediction_index(snapshots)
    rows: list[dict[str, Any]] = []
    judged_count = 0
    pass_count = 0
    for case in benchmark.get("cases") or []:
        if not isinstance(case, dict):
            continue
        expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
        if not _has_judgement(expected):
            rows.append({**case, "score_status": "unjudged", "failure_reasons": []})
            continue
        judged_count += 1
        prediction = predictions.get(_case_key(case)) or predictions.get(str(case.get("case_id") or ""))
        failures = _score_prediction(expected=expected, prediction=prediction)
        if not failures:
            pass_count += 1
        rows.append({**case, "prediction": prediction, "score_status": "pass" if not failures else "fail", "failure_reasons": failures})
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "case_count": len(rows),
        "judged_count": judged_count,
        "pass_count": pass_count,
        "fail_count": judged_count - pass_count,
        "pass_rate": round(pass_count / judged_count, 4) if judged_count else None,
        "cases": rows,
    }


def hard_row_reasons(assignment: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    status = str(assignment.get("topic_node_status") or "")
    review_status = str(assignment.get("topic_review_status") or "")
    root_topic_id = str(assignment.get("root_topic_id") or "")
    reject_reason = str(assignment.get("topic_reject_reason") or "")
    subject = str(assignment.get("topic_subject_he") or assignment.get("raw_topic_subject_he") or "")
    decision = assignment.get("deterministic_topic_decision") if isinstance(assignment.get("deterministic_topic_decision"), dict) else {}
    decision_reason = str((decision or {}).get("reason") or "")

    if status == "candidate" or review_status == "needs_review":
        reasons.append("non_blocking_review")
    if status == "needs_review":
        reasons.append("blocking_review_status")
    if reject_reason:
        reasons.append(f"reject:{reject_reason[:80]}")
    if bool(assignment.get("is_topic_bearing")) and root_topic_id in PROCEDURAL_ROOT_IDS:
        reasons.append("topic_bearing_procedural_root")
    if subject and is_low_quality_topic_label(subject):
        reasons.append("low_quality_subject")
    if decision_reason in {"weak_candidate", "ambiguous_candidates"}:
        reasons.append(f"deterministic_{decision_reason}")
    return _dedupe(reasons)


def _benchmark_payload(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "purpose": "DB-free Step 4 hard-row benchmark. Fill expected.* fields manually, then run score.",
        "case_count": len(cases),
        "cases": cases,
    }


def _case_from_assignment(*, snapshot: TopicAssignmentSnapshot, assignment: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    unit_id = str(assignment.get("structure_unit_id") or "")
    item = snapshot.items_by_id.get(unit_id, {})
    source_text = _first_text(
        item.get("unit_raw_text"),
        item.get("raw_text"),
        assignment.get("topic_supporting_quote_he"),
        assignment.get("topic_identification_context"),
    )
    return {
        "case_id": f"{unit_id}:{_short_norm(source_text)}",
        "source_path": snapshot.source_path,
        "structure_unit_id": unit_id,
        "source_page": assignment.get("source_page") or item.get("source_page"),
        "row_type": assignment.get("row_type"),
        "hard_reasons": reasons,
        "source_text": source_text,
        "prediction": _prediction_from_assignment(assignment),
        "expected": _empty_expected(),
        "judge_notes": "",
    }


def _prediction_index(snapshots: list[TopicAssignmentSnapshot]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        for assignment in snapshot.assignments:
            unit_id = str(assignment.get("structure_unit_id") or "")
            item = snapshot.items_by_id.get(unit_id, {})
            source_text = _first_text(item.get("unit_raw_text"), item.get("raw_text"), assignment.get("topic_supporting_quote_he"), assignment.get("topic_identification_context"))
            key = f"{snapshot.source_path}::{unit_id}"
            out[key] = _prediction_from_assignment(assignment)
            fallback_key = f"{unit_id}:{_short_norm(source_text)}"
            out.setdefault(fallback_key, _prediction_from_assignment(assignment))
    return out


def _prediction_from_assignment(assignment: dict[str, Any]) -> dict[str, Any]:
    return {
        "root_topic_id": assignment.get("root_topic_id"),
        "root_label_he": assignment.get("root_label_he"),
        "child_label_he": assignment.get("child_label_he"),
        "topic_subject_he": assignment.get("topic_subject_he"),
        "topic_node_status": assignment.get("topic_node_status"),
        "topic_review_status": assignment.get("topic_review_status"),
        "is_topic_bearing": assignment.get("is_topic_bearing"),
        "topic_assignment_route": assignment.get("topic_assignment_route"),
        "topic_reject_reason": assignment.get("topic_reject_reason"),
    }


def _score_prediction(*, expected: dict[str, Any], prediction: dict[str, Any] | None) -> list[str]:
    if prediction is None:
        return ["missing_prediction"]
    failures: list[str] = []
    checks = (
        ("root_topic_id", "root_topic_id"),
        ("child_label_he", "child_label_he"),
        ("topic_subject_he", "topic_subject_he"),
        ("is_topic_bearing", "is_topic_bearing"),
    )
    for expected_key, prediction_key in checks:
        expected_value = expected.get(expected_key)
        if expected_value in (None, "", "any"):
            continue
        predicted_value = prediction.get(prediction_key)
        if isinstance(expected_value, bool):
            if bool(predicted_value) != expected_value:
                failures.append(f"{expected_key}:expected_{expected_value}_got_{predicted_value}")
            continue
        if normalize_for_search(str(predicted_value or "")) != normalize_for_search(str(expected_value)):
            failures.append(f"{expected_key}:expected_{expected_value}_got_{predicted_value}")
    expected_status = str(expected.get("topic_node_status") or "")
    if expected_status and expected_status != "any" and str(prediction.get("topic_node_status") or "") != expected_status:
        failures.append(f"topic_node_status:expected_{expected_status}_got_{prediction.get('topic_node_status')}")
    return failures


def _case_key(row: dict[str, Any]) -> str:
    source_path = str(row.get("source_path") or "")
    unit_id = str(row.get("structure_unit_id") or "")
    if source_path and unit_id:
        return f"{source_path}::{unit_id}"
    return str(row.get("case_id") or "")


def _empty_expected() -> dict[str, Any]:
    return {
        "root_topic_id": "",
        "child_label_he": "",
        "topic_subject_he": "",
        "topic_node_status": "",
        "is_topic_bearing": "",
    }


def _has_judgement(expected: dict[str, Any]) -> bool:
    return any(value not in (None, "", "any") for value in expected.values())


def _first_text(*values: Any) -> str:
    for value in values:
        text = " ".join(str(value or "").split())
        if text:
            return text
    return ""


def _short_norm(value: str) -> str:
    normalized = normalize_for_search(value)[:80]
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
