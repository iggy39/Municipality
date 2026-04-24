from __future__ import annotations

from municipality.semantic_prompt import build_semantic_evidence_packet, build_semantic_model_request


def test_evidence_packet_uses_soft_category_hints_for_non_decision_lines() -> None:
    text_value = "\n".join(
        [
            "רקע תכנוני",
            "הרחבת תוכנית תחבורה בשכונת המרינה לשנים הקרובות.",
            "נדרש מעקב ביצוע לפי לוח זמנים.",
        ]
    )
    citation_map = [{"start": 0, "end": len(text_value), "page": 1}]

    packet = build_semantic_evidence_packet(extracted_text=text_value, citation_map=citation_map)

    line = next(item for item in packet.evidence_lines if "הרחבת תוכנית תחבורה" in item.text)
    assert "plan_program" in (line.category_hints or [])
    assert line.regex_boost > 0.0
    assert packet.artifact_evidence == []


def test_evidence_packet_caps_regex_boost() -> None:
    text_value = "תקציב תקציב תקציב תקציב הקצאה מימון עלות תקציב"
    citation_map = [{"start": 0, "end": len(text_value), "page": 1}]

    packet = build_semantic_evidence_packet(extracted_text=text_value, citation_map=citation_map)

    assert packet.evidence_lines
    assert packet.evidence_lines[0].regex_boost <= 0.12


def test_model_request_includes_multicategory_detection_instruction() -> None:
    text_value = "סיכום\nהרחבת תוכנית תחבורה בשכונה הצפונית"
    citation_map = [{"start": 0, "end": len(text_value), "page": 1}]
    packet = build_semantic_evidence_packet(extracted_text=text_value, citation_map=citation_map)

    request_payload = build_semantic_model_request(
        document_version_id=10,
        source_kind="protocol",
        evidence_packet=packet,
    )

    system_instruction = request_payload["system_instruction"]
    assert "Perform BOTH tasks" in system_instruction
    assert "Allowed categories:" in system_instruction

    rules = request_payload["rules"]
    assert any("Do not rely only on explicit decision wording" in rule for rule in rules)
    assert any("artifact_evidence" in rule for rule in rules)
    assert request_payload["selection_strategy"]["category_hint_policy"] == "soft_boost_only"
    assert "category_hint_lexicon" in request_payload["input_packet"]


def test_evidence_packet_can_include_artifact_evidence() -> None:
    text_value = "הוחלט לבצע הרחבת חניה ליד בית הספר"
    citation_map = [{"start": 0, "end": len(text_value), "page": 1}]

    packet = build_semantic_evidence_packet(
        extracted_text=text_value,
        citation_map=citation_map,
        artifact_records=[
            {
                "artifact_id": "art-1",
                "artifact_kind": "decision_unit",
                "header_path": ["פרוטוקול ועדת בטיחות", "הרחבת חניה ליד בית הספר"],
                "body_text": text_value,
                "start_offset": 0,
                "end_offset": len(text_value),
                "start_page": 1,
                "end_page": 1,
                "primary_topic": "תחבורה ובטיחות > הרחבת חניה ליד בית הספר",
                "section_summary": "הרחבת חניה ליד בית הספר",
            }
        ],
    )

    assert packet.artifact_evidence
    assert packet.artifact_evidence[0]["artifact_kind"] == "decision_unit"
