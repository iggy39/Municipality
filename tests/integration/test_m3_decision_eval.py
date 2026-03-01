from __future__ import annotations

from pathlib import Path

from municipality.eval_decisions import evaluate_decision_cases, load_decision_eval_set


def test_m3_decision_eval_meets_quality_thresholds() -> None:
    cases = load_decision_eval_set(Path("eval/gold/m3_decision_eval_set.json"))
    summary, case_results = evaluate_decision_cases(cases)

    assert case_results
    assert summary.decision_precision >= 0.95
    assert summary.decision_recall >= 0.95
    assert summary.vote_precision >= 0.95
    assert summary.vote_recall >= 0.95
    assert summary.citation_coverage == 1.0
