from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from municipality.decisions import _decision_signature, _parse_decision_candidates, _parse_vote
from municipality.extraction import parse_extracted_text, resolve_pages_for_span


@dataclass(slots=True)
class DecisionGoldRow:
    text_contains: str
    vote: dict | None
    citation_required: bool


@dataclass(slots=True)
class DecisionEvalCase:
    case_id: str
    title: str
    raw_text: str
    expected_decisions: list[DecisionGoldRow]


@dataclass(slots=True)
class DecisionEvalCaseResult:
    case_id: str
    expected_count: int
    predicted_count: int
    matched_count: int
    citation_coverage: float


@dataclass(slots=True)
class DecisionEvalSummary:
    decision_precision: float
    decision_recall: float
    vote_precision: float
    vote_recall: float
    citation_coverage: float
    citation_correctness: float
    expected_decisions: int
    predicted_decisions: int
    matched_decisions: int
    fallback_invocations: int
    fallback_accepted: int


def load_decision_eval_set(path: Path) -> list[DecisionEvalCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("cases", [])
    cases: list[DecisionEvalCase] = []
    for row in rows:
        expected_rows: list[DecisionGoldRow] = []
        for expected in row.get("expected_decisions", []):
            expected_rows.append(
                DecisionGoldRow(
                    text_contains=str(expected["text_contains"]),
                    vote=expected.get("vote"),
                    citation_required=bool(expected.get("citation_required", True)),
                )
            )
        cases.append(
            DecisionEvalCase(
                case_id=str(row["case_id"]),
                title=str(row.get("title", row["case_id"])),
                raw_text=str(row["raw_text"]),
                expected_decisions=expected_rows,
            )
        )
    return cases


def evaluate_decision_cases(cases: list[DecisionEvalCase]) -> tuple[DecisionEvalSummary, list[DecisionEvalCaseResult]]:
    expected_total = 0
    predicted_total = 0
    matched_total = 0

    expected_vote_total = 0
    predicted_vote_total = 0
    correct_vote_total = 0

    expected_citation_total = 0
    covered_citation_total = 0

    case_results: list[DecisionEvalCaseResult] = []

    for case in cases:
        full_text, _pages, citation_map = parse_extracted_text(case.raw_text)
        predicted = _parse_decision_candidates(full_text)
        matched = _match_expected_to_predicted(case.expected_decisions, predicted)

        expected_total += len(case.expected_decisions)
        predicted_total += len(predicted)
        matched_total += len(matched)

        case_expected_citations = 0
        case_covered_citations = 0

        for expected_idx, predicted_idx in matched:
            expected = case.expected_decisions[expected_idx]
            pred = predicted[predicted_idx]

            if expected.vote is not None:
                expected_vote_total += 1
                pred_vote = pred.vote_hint or _parse_vote(pred.decision_text)
                if pred_vote is not None and not pred_vote.get("is_uncertain", False):
                    predicted_vote_total += 1
                if _vote_matches(expected.vote, pred_vote):
                    correct_vote_total += 1

            if expected.citation_required:
                case_expected_citations += 1
                expected_citation_total += 1
                pages = resolve_pages_for_span(citation_map, pred.start_offset, pred.end_offset)
                if pages:
                    case_covered_citations += 1
                    covered_citation_total += 1

        case_coverage = (
            case_covered_citations / case_expected_citations if case_expected_citations else 1.0
        )
        case_results.append(
            DecisionEvalCaseResult(
                case_id=case.case_id,
                expected_count=len(case.expected_decisions),
                predicted_count=len(predicted),
                matched_count=len(matched),
                citation_coverage=round(case_coverage, 4),
            )
        )

    decision_precision = matched_total / predicted_total if predicted_total else 0.0
    decision_recall = matched_total / expected_total if expected_total else 0.0
    vote_precision = (
        correct_vote_total / predicted_vote_total
        if predicted_vote_total
        else (1.0 if expected_vote_total == 0 else 0.0)
    )
    vote_recall = correct_vote_total / expected_vote_total if expected_vote_total else 1.0
    citation_coverage = covered_citation_total / expected_citation_total if expected_citation_total else 1.0

    summary = DecisionEvalSummary(
        decision_precision=round(decision_precision, 4),
        decision_recall=round(decision_recall, 4),
        vote_precision=round(vote_precision, 4),
        vote_recall=round(vote_recall, 4),
        citation_coverage=round(citation_coverage, 4),
        citation_correctness=round(citation_coverage, 4),
        expected_decisions=expected_total,
        predicted_decisions=predicted_total,
        matched_decisions=matched_total,
        fallback_invocations=0,
        fallback_accepted=0,
    )
    return summary, case_results


def _match_expected_to_predicted(expected_rows: list[DecisionGoldRow], predicted_rows: list) -> list[tuple[int, int]]:
    matched: list[tuple[int, int]] = []
    used_pred: set[int] = set()

    for exp_idx, expected in enumerate(expected_rows):
        expected_sig = _decision_signature(expected.text_contains)
        winner: int | None = None
        winner_score = 0.0
        for pred_idx, pred in enumerate(predicted_rows):
            if pred_idx in used_pred:
                continue
            pred_sig = _decision_signature(pred.decision_text)
            if not pred_sig:
                continue
            if expected_sig in pred_sig or pred_sig in expected_sig:
                winner = pred_idx
                winner_score = 1.0
                break

            score = _token_overlap(expected_sig, pred_sig)
            if score > winner_score:
                winner_score = score
                winner = pred_idx

        if winner is not None and winner_score >= 0.65:
            matched.append((exp_idx, winner))
            used_pred.add(winner)

    return matched


def _token_overlap(left: str, right: str) -> float:
    left_tokens = {part for part in left.split(" ") if part}
    right_tokens = {part for part in right.split(" ") if part}
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens.intersection(right_tokens)) / max(len(left_tokens), len(right_tokens), 1)


def _vote_matches(expected_vote: dict, predicted_vote: dict | None) -> bool:
    if predicted_vote is None:
        return False
    if expected_vote.get("unanimous") is True:
        return bool(predicted_vote.get("unanimous"))

    for key in ("for_count", "against_count", "abstain_count"):
        expected_value = expected_vote.get(key)
        if expected_value is None:
            continue
        if predicted_vote.get(key) != expected_value:
            return False
    return True
