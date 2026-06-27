from __future__ import annotations

from municipality.admin_ingestion_coverage import canonical_source_type_code


def test_canonical_source_type_code_collapses_pipeline_aliases_and_labels() -> None:
    assert canonical_source_type_code("pdf_first_v4_protocol") == "protocol"
    assert canonical_source_type_code("protocol_full") == "protocol"
    assert canonical_source_type_code("pdf_first_v4_attachment") == "attachment"
    assert canonical_source_type_code("תקציבים") == "budget"
    assert canonical_source_type_code("new_source_kind") == "new_source_kind"
