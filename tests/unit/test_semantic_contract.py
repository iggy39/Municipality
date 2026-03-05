from __future__ import annotations

from municipality.semantic_contract import SemanticEvidenceCategory, parse_semantic_model_output


def test_parse_semantic_model_output_parses_evidence_spans_and_refs() -> None:
    payload = {
        "evidence_spans": [
            {
                "span_id": "s1",
                "category": "decision",
                "start_offset": 10,
                "end_offset": 30,
                "text": "הוחלט לאשר תקציב",
                "confidence": 0.82,
                "regex_boost": 0.06,
                "hint_terms": ["הוחלט", "תקציב"],
            }
        ],
        "nodes": [
            {
                "candidate_id": "n1",
                "label_he": "אישור תקציב",
                "node_kind": "topic",
                "semantic_type": "budget_item",
                "confidence": 0.8,
                "mentions": [
                    {
                        "mention_text": "אישור תקציב",
                        "start_offset": 11,
                        "end_offset": 22,
                        "confidence": 0.77,
                    }
                ],
                "evidence_span_ids": ["s1"],
            }
        ],
    }

    output, report = parse_semantic_model_output(payload)

    assert report.is_valid is True
    assert len(output.evidence_spans) == 1
    assert output.evidence_spans[0].category == SemanticEvidenceCategory.DECISION
    assert output.nodes[0].evidence_span_ids == ["s1"]


def test_parse_semantic_model_output_reports_missing_evidence_ref() -> None:
    payload = {
        "evidence_spans": [],
        "nodes": [
            {
                "candidate_id": "n1",
                "label_he": "דיון תכנוני",
                "node_kind": "topic",
                "semantic_type": "plan",
                "mentions": [
                    {
                        "mention_text": "דיון תכנוני",
                        "start_offset": 0,
                        "end_offset": 10,
                    }
                ],
                "evidence_span_ids": ["s-missing"],
            }
        ],
    }

    _output, report = parse_semantic_model_output(payload)

    assert report.is_valid is False
    assert any(issue.code == "node_evidence_refs_missing" for issue in report.issues)
