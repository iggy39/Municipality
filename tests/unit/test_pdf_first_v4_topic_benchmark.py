from __future__ import annotations

from municipality.pdf_first_v4_topic_benchmark import TopicAssignmentSnapshot, build_hard_row_benchmark, score_hard_row_benchmark


def test_builds_hard_row_case_for_candidate_assignment() -> None:
    snapshot = TopicAssignmentSnapshot(
        source_path="/tmp/topic_assignments.json",
        assignments=[
            {
                "structure_unit_id": "s1",
                "root_topic_id": "root_agenda_queries",
                "root_label_he": "סדר יום ושאילתות",
                "topic_node_status": "candidate",
                "topic_review_status": "needs_review",
                "is_topic_bearing": True,
                "topic_subject_he": "כיכר רמון בעיר ודרך מנחם בגין",
            }
        ],
        items_by_id={"s1": {"unit_raw_text": "שאילתה בנושא כיכר רמון בעיר ודרך מנחם בגין"}},
    )

    payload = build_hard_row_benchmark(snapshots=[snapshot])

    assert payload["case_count"] == 1
    assert "topic_bearing_procedural_root" in payload["cases"][0]["hard_reasons"]


def test_scores_judged_case_against_prediction() -> None:
    snapshot = TopicAssignmentSnapshot(
        source_path="/tmp/topic_assignments.json",
        assignments=[{"structure_unit_id": "s1", "root_topic_id": "root_transport_safety", "topic_node_status": "active", "is_topic_bearing": True}],
        items_by_id={"s1": {"unit_raw_text": "כיכר רמון"}},
    )
    benchmark = {
        "cases": [
            {
                "source_path": "/tmp/topic_assignments.json",
                "structure_unit_id": "s1",
                "expected": {"root_topic_id": "root_transport_safety", "topic_node_status": "active", "is_topic_bearing": True},
            }
        ]
    }

    report = score_hard_row_benchmark(benchmark=benchmark, snapshots=[snapshot])

    assert report["judged_count"] == 1
    assert report["pass_count"] == 1
