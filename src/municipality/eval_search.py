from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SearchEvalCase:
    case_id: str
    query: str
    top_k: int
    expected_source_type: str | None = None
    expected_document_title_contains: str | None = None
    expected_meeting_external_id: str | None = None
    filter_source_type: str | None = None
    filter_year: int | None = None
    filter_topic: str | None = None


@dataclass(slots=True)
class SearchEvalCaseResult:
    case_id: str
    query: str
    matched: bool
    latency_ms: float
    top_k: int
    returned_count: int


@dataclass(slots=True)
class SearchEvalSummary:
    total_queries: int
    matched_queries: int
    hit_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    max_latency_ms: float


def load_search_eval_set(file_path: Path) -> list[SearchEvalCase]:
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    rows = payload.get("queries", [])
    cases: list[SearchEvalCase] = []
    for row in rows:
        cases.append(
            SearchEvalCase(
                case_id=str(row["case_id"]),
                query=str(row["query"]),
                top_k=int(row.get("top_k", 5)),
                expected_source_type=row.get("expected_source_type"),
                expected_document_title_contains=row.get("expected_document_title_contains"),
                expected_meeting_external_id=row.get("expected_meeting_external_id"),
                filter_source_type=row.get("filter_source_type"),
                filter_year=int(row["filter_year"]) if row.get("filter_year") is not None else None,
                filter_topic=row.get("filter_topic"),
            )
        )
    return cases


def evaluate_search_cases(search_service, cases: list[SearchEvalCase]) -> tuple[SearchEvalSummary, list[SearchEvalCaseResult]]:
    results: list[SearchEvalCaseResult] = []
    for case in cases:
        started = time.perf_counter()
        hits = search_service.search(
            query=case.query,
            source_type=case.filter_source_type,
            year=case.filter_year,
            topic=case.filter_topic,
            limit=max(case.top_k, 1),
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        top_hits = hits[: case.top_k]
        matched = any(_is_expected_hit(hit, case) for hit in top_hits)
        results.append(
            SearchEvalCaseResult(
                case_id=case.case_id,
                query=case.query,
                matched=matched,
                latency_ms=round(elapsed_ms, 3),
                top_k=case.top_k,
                returned_count=len(hits),
            )
        )

    latencies = [result.latency_ms for result in results]
    matched_queries = sum(1 for result in results if result.matched)
    total = len(results)
    summary = SearchEvalSummary(
        total_queries=total,
        matched_queries=matched_queries,
        hit_rate=(matched_queries / total) if total else 0.0,
        avg_latency_ms=_avg(latencies),
        p95_latency_ms=_p95(latencies),
        max_latency_ms=max(latencies) if latencies else 0.0,
    )
    return summary, results


def _is_expected_hit(hit, case: SearchEvalCase) -> bool:
    if case.expected_source_type and hit.source_type != case.expected_source_type:
        return False
    if case.expected_document_title_contains and case.expected_document_title_contains not in hit.document_title:
        return False
    if case.expected_meeting_external_id and hit.meeting_external_id != case.expected_meeting_external_id:
        return False
    return True


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return round(ordered[idx], 3)
