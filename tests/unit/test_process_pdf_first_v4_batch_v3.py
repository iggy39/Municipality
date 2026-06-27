from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import process_pdf_first_v4_batch as batch  # noqa: E402


def _write_topic_assignments(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "step": "step4_v4_global_topic_assignment",
                "items": [
                    {"structure_unit_id": "u0", "row_type": "topic_item", "unit_raw_text": "שאילתה בנושא כיכר רמון ודרך מנחם בגין"},
                    {"structure_unit_id": "u1", "row_type": "topic_item", "unit_raw_text": "גב' מזל אסולין- רכזת וועדה על סדר היום"},
                    {"structure_unit_id": "u2", "row_type": "topic_item", "unit_raw_text": "סיוע להבטחת ביטחון תזונתי"},
                ],
                "topic_assignments": [
                    {"structure_unit_id": "u0", "topic_node_status": "candidate", "root_topic_id": "root_geo", "is_topic_bearing": True, "topic_subject_he": "כיכר רמון ודרך מנחם בגין"},
                    {"structure_unit_id": "u1", "topic_node_status": "active", "root_topic_id": "root_people_roles", "is_topic_bearing": True, "topic_subject_he": "גב' מזל אסולין"},
                    {"structure_unit_id": "u2", "topic_node_status": "active", "root_topic_id": "root_welfare_social", "is_topic_bearing": True, "topic_subject_he": "סיוע לביטחון תזונתי"},
                ],
            }
        ),
        encoding="utf-8",
    )


def test_v3_selection_prioritizes_problem_rows_and_respects_limit(tmp_path: Path) -> None:
    topic_assignments = tmp_path / "topic_assignments.json"
    _write_topic_assignments(topic_assignments)

    selection = batch._write_topic_subject_v3_selection(
        topic_assignments_json=topic_assignments,
        output_path=tmp_path / "selected_rows.json",
        mode="problem_rows",
        max_rows=1,
    )

    assert [(row["source_ordinal"], row["reason"]) for row in selection["selected_rows"]] == [(0, "candidate_or_review")]


def test_v3_selection_includes_weak_person_subject_without_limit(tmp_path: Path) -> None:
    topic_assignments = tmp_path / "topic_assignments.json"
    _write_topic_assignments(topic_assignments)

    selection = batch._write_topic_subject_v3_selection(
        topic_assignments_json=topic_assignments,
        output_path=tmp_path / "selected_rows.json",
        mode="problem_rows",
        max_rows=0,
    )

    assert [(row["source_ordinal"], row["reason"]) for row in selection["selected_rows"]] == [
        (0, "candidate_or_review"),
        (1, "weak_or_dialogue_subject"),
    ]
