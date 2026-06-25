from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module(relative_path: str, name: str):
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


step5 = _load_module(
    "rag_eval/runs/ashdod_2025_regular_3_short_pdf/pdf_first_pipeline/scripts/step5_v4_build_retrieval_chunks.py",
    "step5_v4_build_retrieval_chunks",
)
importer = _load_module("scripts/import_pdf_first_v4_retrieval_chunks.py", "import_pdf_first_v4_retrieval_chunks")


def test_step5_chunk_exposes_v3_structure_text_and_span_contract() -> None:
    raw_text = "רקע כללי על ההתקשרות. המועצה מאשרים פה אחד את ההסכם להפעלת השירות."
    chunk = step5._build_chunk(
        unit={
            "structure_unit_id": "s0001_01_test",
            "semantic_unit_id": "s0001_01_test",
            "source_semantic_unit_ids": ["u1"],
            "source_window_id": "p1_w1",
            "page": 3,
            "source_region_ids": ["p3_r1"],
            "source_block_ids": ["p3_b1"],
            "raw_text": raw_text,
            "summary_he": "אישור הסכם להפעלת השירות",
            "structural_role": "body",
            "section_id": "section_test",
            "section_number": "1",
            "fragment_index": 1,
            "fragment_count": 1,
            "structure_evidence": {"split_reason": "single_fragment"},
        },
        assignment={
            "structure_unit_id": "s0001_01_test",
            "root_topic_id": "root_agreements",
            "root_label_he": "הסכמים והתקשרויות",
            "topic_node_status": "active",
            "topic_assignment_confidence": 0.91,
            "topic_supporting_quote_he": "המועצה מאשרים פה אחד את ההסכם להפעלת השירות",
            "topic_assignment_route": "test_route",
            "is_topic_bearing": True,
            "row_type": "topic_item",
        },
        entity_facts=[],
        ordinal=0,
        retrieval_set_id="rset_test",
        source_title="source.pdf",
        source_url="/tmp/source.pdf",
        document_version_id=123,
    )

    assert chunk["raw_text"] == raw_text
    assert chunk["corrected_text_he"]
    assert chunk["structure_metadata"]["structure_unit_id"] == "s0001_01_test"
    assert chunk["structure_metadata"]["source_block_ids"] == ["p3_b1"]
    assert chunk["topic_assignment"]["root_topic_id"] == "root_agreements"
    assert chunk["topic_assignment"]["is_topic_bearing"] is True

    roles = {span["span_role"] for span in chunk["spans"]}
    assert "action_candidate" in roles
    assert "outcome_candidate" in roles
    assert all(span["raw_text"] == raw_text[span["char_start"] : span["char_end"]] for span in chunk["spans"])


def test_import_metadata_preserves_v3_contract_fields() -> None:
    chunk = {
        "retrieval_artifact_id": "rc_test",
        "retrieval_set_id": "rset_test",
        "structure_unit_id": "s1",
        "semantic_unit_id": "s1",
        "source_semantic_unit_ids": ["u1"],
        "source_window_id": "p1_w1",
        "source_region_ids": ["p1_r1"],
        "source_block_ids": ["p1_b1"],
        "structural_role": "body",
        "section_id": "section_1",
        "section_number": "1",
        "source_page": 4,
        "page": 4,
        "raw_text": "טקסט מקור",
        "corrected_text_he": "טקסט מקור",
        "summary_he": "סיכום",
        "structure_metadata": {"structure_unit_id": "s1", "source_block_ids": ["p1_b1"]},
        "spans": [{"span_id": "span_1", "span_role": "action_candidate", "raw_text": "טקסט מקור"}],
        "topic_assignment": {"root_topic_id": "root_agreements", "topic_node_status": "active"},
    }

    metadata = importer._metadata_for_chunk(chunk)

    assert metadata["body_text_source"] == "chunk.raw_text"
    assert metadata["retrieval_text_source"] == "chunk.chunk_text"
    assert metadata["corrected_text_he"] == "טקסט מקור"
    assert metadata["structure_metadata"]["source_block_ids"] == ["p1_b1"]
    assert metadata["spans"][0]["span_role"] == "action_candidate"
    assert metadata["topic_assignment"]["root_topic_id"] == "root_agreements"
