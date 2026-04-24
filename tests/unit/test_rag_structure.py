from __future__ import annotations

import json

from municipality.rag_structure import build_structured_document


def test_build_structured_document_preserves_header_path_in_artifacts() -> None:
    text = "\n".join(
        [
            "פרוטוקול ועדת תכנון ובנייה 14.05.2026",
            "נושא 6",
            "התנגדויות לתכנית 101-1234567",
            "החלטה",
            "הוועדה מאשרת בכפוף לתיקונים.",
        ]
    )
    citation_map = [{"start": 0, "end": len(text), "page": 1}]

    result = build_structured_document(
        document_version_id=17,
        document_title="פרוטוקול ועדת תכנון ובנייה 14.05.2026",
        text=text,
        citation_map=citation_map,
        source_kind="protocol",
    )

    decision_artifacts = [artifact for artifact in result.artifacts if artifact["artifact_kind"] == "decision_unit"]

    assert decision_artifacts
    header_path = json.loads(decision_artifacts[0]["header_path_json"])
    assert header_path[-2:] == ["התנגדויות לתכנית 101-1234567", "החלטה"]
    assert "מסלול כותרות:" in decision_artifacts[0]["retrieval_text"]
    assert "הוועדה מאשרת בכפוף לתיקונים" in decision_artifacts[0]["retrieval_text"]
    assert result.metadata["committee_name"] == "ועדת תכנון ובנייה"
    assert result.metadata["meeting_date"] == "2026-05-14"


def test_build_structured_document_emits_document_profile_and_header_anchor() -> None:
    text = "\n".join(
        [
            "פרוטוקול ועדת חינוך",
            "סעיף 1",
            "שדרוג מערכות בטיחות בבתי ספר",
            "הוחלט לבצע סקר בטיחות נוסף.",
        ]
    )
    citation_map = [{"start": 0, "end": len(text), "page": 2}]

    result = build_structured_document(
        document_version_id=21,
        document_title="פרוטוקול ועדת חינוך",
        text=text,
        citation_map=citation_map,
        source_kind="protocol",
    )

    kinds = {artifact["artifact_kind"] for artifact in result.artifacts}

    assert "document_profile" in kinds
    assert "header_anchor" in kinds
    assert "header_plus_opening" in kinds
    assert "context_window" in kinds
    assert kinds.intersection({"section_unit", "decision_unit"})
