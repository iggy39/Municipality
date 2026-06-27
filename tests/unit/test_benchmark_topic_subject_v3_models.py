from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from municipality.topic_subjects import build_topic_subject_v3_event_contexts, topic_subject_v3_source_context

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from benchmark_topic_subject_v3_models import _benchmark_output_dir, _json_benchmark_items  # noqa: E402


def test_json_benchmark_items_keep_full_document_context(tmp_path: Path) -> None:
    structure_path = tmp_path / "structure_units.json"
    topic_assignments_path = tmp_path / "topic_assignments.json"
    structure_path.write_text(
        json.dumps(
            {
                "structure_units": [
                    {"structure_unit_id": "u0", "raw_text": "previous context row"},
                    {"structure_unit_id": "u1", "raw_text": "selected target row"},
                    {"structure_unit_id": "u2", "raw_text": "next context row"},
                ]
            }
        ),
        encoding="utf-8",
    )
    topic_assignments_path.write_text(
        json.dumps(
            {
                "step": "step4_v4_global_topic_assignment",
                "input_structure_units_json": str(structure_path),
                "items": [
                    {"structure_unit_id": "u0", "is_topic_bearing": False, "child_label_he": "context"},
                    {"structure_unit_id": "u1", "is_topic_bearing": True, "child_label_he": "target"},
                    {"structure_unit_id": "u2", "is_topic_bearing": False, "child_label_he": "context"},
                ],
            }
        ),
        encoding="utf-8",
    )

    items = _json_benchmark_items(paths=[topic_assignments_path], municipality_slug="json", json_rows=[1], max_samples=0)

    assert len(items) == 1
    assert [artifact.source_ordinal for artifact in items[0].context_artifacts] == [0, 1, 2]

    context = build_topic_subject_v3_event_contexts(
        artifacts=[items[0].artifact],
        context_artifacts=items[0].context_artifacts,
        max_context_rows=5,
    )[0]
    source_context = topic_subject_v3_source_context(context=context, max_text_chars=1000)

    assert [row.source_ordinal for row in context.rows] == [0, 1, 2]
    assert [row["raw_text"] for row in source_context["nearby_rows"]] == ["previous context row", "next context row"]


def test_json_benchmark_items_support_exact_path_index_row_specs(tmp_path: Path) -> None:
    paths: list[Path] = []
    for path_index in range(2):
        structure_path = tmp_path / f"structure_units_{path_index}.json"
        topic_assignments_path = tmp_path / f"topic_assignments_{path_index}.json"
        structure_path.write_text(
            json.dumps(
                {
                    "structure_units": [
                        {"structure_unit_id": f"u{path_index}_0", "raw_text": f"path {path_index} row 0"},
                        {"structure_unit_id": f"u{path_index}_1", "raw_text": f"path {path_index} row 1"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        topic_assignments_path.write_text(
            json.dumps(
                {
                    "step": "step4_v4_global_topic_assignment",
                    "input_structure_units_json": str(structure_path),
                    "items": [
                        {"structure_unit_id": f"u{path_index}_0", "is_topic_bearing": True, "child_label_he": "row0"},
                        {"structure_unit_id": f"u{path_index}_1", "is_topic_bearing": True, "child_label_he": "row1"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        paths.append(topic_assignments_path)

    items = _json_benchmark_items(
        paths=paths,
        municipality_slug="json",
        json_rows=[],
        json_row_specs=[(0, 1), (1, 0)],
        max_samples=0,
    )

    assert [(item.sample_label, item.artifact.source_ordinal, item.artifact.real_text) for item in items] == [
        ("json:json0:ord1", 1, "path 0 row 1"),
        ("json:json1:ord0", 0, "path 1 row 0"),
    ]


def test_benchmark_output_dir_can_be_fixed(tmp_path: Path) -> None:
    args = SimpleNamespace(output_dir=tmp_path / "fixed_v3", output_root=tmp_path / "unused", model="dicta", small_model="dicta")

    assert _benchmark_output_dir(args=args, benchmark_items=[]) == tmp_path / "fixed_v3"
