from __future__ import annotations

from municipality.extraction import parse_extracted_text, score_extraction_quality


HE_WELFARE_COMMITTEE = "ועדת רווחה"
HE_LINE_A = "שורה א"
HE_PAGE_TWO = "עמוד שני"
HE_LINE_B = "שורה ב"
HE_SHORT_TEXT = "טקסט קצר"
HE_MORE = "עוד"


def test_parse_extracted_text_strips_bidi_marks_and_keeps_pages() -> None:
    raw = (
        f"\u202b{HE_WELFARE_COMMITTEE}\u202c\n"
        f"{HE_LINE_A}\f"
        f"\u202b{HE_PAGE_TWO}\u202c\n"
        f"{HE_LINE_B}"
    )
    full_text, pages, citation_map = parse_extracted_text(raw)

    assert "\u202b" not in full_text
    assert len(pages) == 2
    assert pages[0].page == 1
    assert pages[1].page == 2
    assert pages[0].reading_direction == "rtl"
    assert pages[0].layout_blocks[0]["text"] == HE_WELFARE_COMMITTEE
    assert citation_map[0]["page"] == 1
    assert citation_map[1]["page"] == 2


def test_quality_flags_short_text_for_ocr_candidate() -> None:
    full_text, pages, _ = parse_extracted_text(f"{HE_SHORT_TEXT}\f{HE_MORE}")
    score, flags, summary = score_extraction_quality(full_text, pages)

    assert score < 0.8
    assert "TOO_SHORT" in flags
    assert "OCR_CANDIDATE" in flags
    assert summary["total_pages"] == 2
