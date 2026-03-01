from __future__ import annotations

from municipality.extraction import parse_extracted_text, score_extraction_quality


def test_parse_extracted_text_strips_bidi_marks_and_keeps_pages() -> None:
    raw = "\u202bוועדת רווחה\u202c\nשורה א\f\u202bעמוד שני\u202c\nשורה ב"
    full_text, pages, citation_map = parse_extracted_text(raw)

    assert "\u202b" not in full_text
    assert len(pages) == 2
    assert pages[0].page == 1
    assert pages[1].page == 2
    assert citation_map[0]["page"] == 1
    assert citation_map[1]["page"] == 2


def test_quality_flags_short_text_for_ocr_candidate() -> None:
    full_text, pages, _ = parse_extracted_text("טקסט קצר\fעוד")
    score, flags, summary = score_extraction_quality(full_text, pages)

    assert score < 0.8
    assert "TOO_SHORT" in flags
    assert "OCR_CANDIDATE" in flags
    assert summary["total_pages"] == 2
