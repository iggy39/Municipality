from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_child_shadow_module():
    path = Path(__file__).resolve().parents[2] / "rag_eval/runs/ashdod_2025_regular_3_short_pdf/pdf_first_pipeline/scripts/step4_7_v4_shadow_child_taxonomy.py"
    spec = importlib.util.spec_from_file_location("step4_7_v4_shadow_child_taxonomy", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


child_shadow = _load_child_shadow_module()


def _item(parent: str, root_topic_id: str, final_topic: str, *, current_child: str | None = None) -> dict:
    return {
        "item_id": "I001",
        "root_topic_id": root_topic_id,
        "parent": parent,
        "final_topic": final_topic,
        "current_child_labels_from_step4": [current_child] if current_child else [],
        "examples": [{"protocol": "A1", "page": 1, "raw_topic": final_topic, "evidence": final_topic}],
    }


def _suggestion(decision: str, child: str | None) -> dict:
    return {"item_id": "I001", "decision": decision, "child_label_he": child, "confidence": 0.9, "reason_he": "test"}


def test_rejects_child_same_as_final_topic() -> None:
    item = _item("דת ושירותי דת", "root_religious_services", "מינוי מועצה דתית")

    result = child_shadow.validate_shadow_child_suggestion(item=item, suggestion=_suggestion("propose_new_child", "מינוי מועצה דתית"))

    assert result["validation_status"] == "REJECTED"
    assert "child_too_close_to_final_topic" in result["validation_issues"]


def test_rejects_near_leaf_paraphrase_child() -> None:
    item = _item("תכנון ובנייה", "root_planning_building", "תבחינים לשיפוץ חזיתות")

    result = child_shadow.validate_shadow_child_suggestion(item=item, suggestion=_suggestion("propose_new_child", "תבחינים לשיפוץ חזיתות מבנים"))

    assert result["validation_status"] == "REJECTED"
    assert "child_too_close_to_final_topic" in result["validation_issues"]


def test_rejects_pluralized_leaf_paraphrase_child() -> None:
    item = _item("תחבורה ובטיחות", "root_transport_safety", "מימון פרויקט תחבורה ציבורית")

    result = child_shadow.validate_shadow_child_suggestion(item=item, suggestion=_suggestion("propose_new_child", "מימון פרויקטי תחבורה ציבורית"))

    assert result["validation_status"] == "REJECTED"
    assert "child_too_close_to_final_topic" in result["validation_issues"]


def test_rejects_committee_management_child() -> None:
    item = _item("הקצאות ושימושים", "root_allocations", "ועדת הקצאות מקצועית")

    result = child_shadow.validate_shadow_child_suggestion(item=item, suggestion=_suggestion("propose_new_child", "ניהול ועדות הקצאות"))

    assert result["validation_status"] == "REJECTED"
    assert "committee_child_not_mid_level_taxonomy" in result["validation_issues"]


def test_rejects_unapproved_forced_existing_child() -> None:
    item = _item("ביטחון ואכיפה", "root_security_enforcement", "ועדה למיגור תופעת האלימות")

    result = child_shadow.validate_shadow_child_suggestion(item=item, suggestion=_suggestion("use_existing_child", "סיוע במיגון העיר"), approved_existing_children={"root_security_enforcement": []})

    assert result["validation_status"] == "REJECTED"
    assert "not_an_approved_existing_child" in result["validation_issues"]


def test_accepts_broader_reusable_public_safety_child() -> None:
    item = _item("ביטחון ואכיפה", "root_security_enforcement", "אלרגיות ואפיפן במרחב הציבורי")

    result = child_shadow.validate_shadow_child_suggestion(item=item, suggestion=_suggestion("propose_new_child", "בטיחות ובריאות הציבור"))

    assert result["validation_status"] == "PASS"
    assert result["validated_child"] == "בטיחות ובריאות הציבור"


def test_accepts_broader_animal_welfare_child() -> None:
    item = _item("רווחה ושירותים חברתיים", "root_welfare_social", "טיפול בגורי חתולי רחוב")

    result = child_shadow.validate_shadow_child_suggestion(item=item, suggestion=_suggestion("propose_new_child", "טיפול בבעלי חיים נטושים"))

    assert result["validation_status"] == "PASS"
    assert result["validated_child"] == "טיפול בבעלי חיים נטושים"



def test_no_child_safe_is_valid_without_child() -> None:
    item = _item("כוח אדם ועובדים", "root_hr_labor", "הקלטת עובדים")

    result = child_shadow.validate_shadow_child_suggestion(item=item, suggestion=_suggestion("no_child_safe", None))

    assert result["validation_status"] == "NO_CHILD_SAFE"
    assert result["validated_child"] is None
