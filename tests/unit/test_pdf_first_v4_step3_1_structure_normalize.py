from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_step31_module():
    path = Path(__file__).resolve().parents[2] / "rag_eval/runs/ashdod_2025_regular_3_short_pdf/pdf_first_pipeline/scripts/step3_1_structure_normalize.py"
    spec = importlib.util.spec_from_file_location("step3_1_structure_normalize", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


step31 = _load_step31_module()


def test_visual_attribution_dash_row_extracts_bounded_headline() -> None:
    candidate = step31._visual_headline_candidate(
        {
            "visual_role": "body_text",
            "region_id": "r1",
            "block_ids": ["b1"],
            "text": "חברת מועצה פלונית – טיפול במפגעי רעש באזור מגורים .2",
            "confidence": 0.82,
        }
    )

    assert candidate is not None
    assert candidate["header_text"] == "טיפול במפגעי רעש באזור מגורים"


def test_visual_speaker_label_dash_is_not_headline() -> None:
    candidate = step31._visual_headline_candidate(
        {
            "visual_role": "structured_header",
            "region_id": "r1",
            "block_ids": ["b1"],
            "text": ":מר פלוני – היו\"ר .אני מעביר את רשות הדיבור לחבר המועצה הבא",
            "confidence": 0.86,
        }
    )

    assert candidate is None


def test_visual_region_with_multiple_explicit_markers_splits_candidates() -> None:
    candidates = step31._visual_headline_candidates(
        {
            "visual_role": "structured_row",
            "region_id": "r1",
            "block_ids": ["b1"],
            "text": "שאילתה של חברה בנושא טיפול במפגעי רעש. שאילתה של חבר בנושא שיפוץ חוף עירוני.",
            "confidence": 0.82,
        }
    )

    assert [candidate["header_text"] for candidate in candidates] == [
        "שאילתה של חברה בנושא טיפול במפגעי רעש",
        "שאילתה של חבר בנושא שיפוץ חוף עירוני",
    ]


def test_visual_repeated_page_chrome_is_not_headline() -> None:
    candidate = step31._visual_headline_candidate(
        {
            "visual_role": "section_heading",
            "region_id": "r1",
            "block_ids": ["b1"],
            "text": "פרוטוקול ישיבות המועצה 35 ישיבה מן המניין מתאריך 11.5.2026",
            "confidence": 0.9,
        }
    )

    assert candidate is None


def test_normalize_structure_adds_visual_headline_units_from_generic_report(tmp_path: Path) -> None:
    visual_report = {
        "results": [
            {
                "page": 4,
                "visual_regions": [
                    {
                        "region_id": "p4_r7",
                        "visual_role": "structured_row",
                        "block_ids": ["p4_b7"],
                        "text": "חבר מועצה אלמוני – שיפור בטיחות הולכי רגל ליד בתי ספר .1",
                        "confidence": 0.84,
                    }
                ],
            }
        ]
    }
    visual_path = tmp_path / "visual_layout_report.json"
    visual_path.write_text(json.dumps(visual_report, ensure_ascii=False), encoding="utf-8")

    structure_units = step31._normalize_structure(
        [
            {
                "semantic_unit_id": "u1",
                "source_window_id": "w1",
                "page": 4,
                "source_region_ids": ["p4_r8"],
                "source_block_ids": ["p4_b8"],
                "raw_text": "נאום ארוך של חברי מועצה שאינו כותרת עצמאית",
                "explicit_actions": [],
            }
        ],
        semantic_payload={"input_visual_layout_report": str(visual_path)},
    )

    visual_units = [unit for unit in structure_units if unit["structure_evidence"]["split_reason"] == "visual_bounded_headline_candidate"]
    assert len(visual_units) == 1
    assert visual_units[0]["header_text"] == "שיפור בטיחות הולכי רגל ליד בתי ספר"
    assert visual_units[0]["source_region_ids"] == ["p4_r7"]
    assert visual_units[0]["structural_role"] == "outline_item"


def test_structural_starts_does_not_split_conjoined_numeric_reference() -> None:
    text = (
        "בתחילת דברי ההסבר לתקציב מוזכרת המלחמה, אירועי המלחמה - "
        "זה חוזר כמה פעמים, סעיף 2 ו-3. , אין שום סעיף בתקציב הזה"
    )

    assert step31._structural_starts(text) == [0]
    assert step31._structural_starts("סעיף 741/370 - להלן סעיף 23.") == [0]


def test_first_short_protocol_header_does_not_self_link_as_continuation() -> None:
    structure_units = step31._normalize_structure(
        [
            {
                "semantic_unit_id": "p1_w1_u1",
                "source_window_id": "p1_w1",
                "page": 1,
                "source_region_ids": ["p1_r1", "p1_r2"],
                "source_block_ids": ["p1_b1", "p1_b2"],
                "raw_text": "עיריית באר שבע פרוטוקול אישור מועצה מן המניין מס' 50 מיום ראשון, ח' סיון תשפ\"ו 24.5.26",
                "explicit_actions": [],
            }
        ],
        semantic_payload={},
    )

    assert len(structure_units) == 1
    assert structure_units[0]["structural_role"] == "metadata"
    assert structure_units[0]["continuation_of_unit_id"] is None
    assert structure_units[0]["topic_assignment_eligible"] is False
    assert step31._validate_structure(structure_units)["accept_for_next_step"] is True
