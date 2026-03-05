from __future__ import annotations

from municipality.semantic_canonicalization import SemanticCanonicalizer
from municipality.semantic_contract import SemanticRejectReason


def test_validate_mention_span_accepts_grounded_offsets_and_pages() -> None:
    canonicalizer = SemanticCanonicalizer()
    text_value = "הוחלט לשדרג את רחוב הרצל בעיר אשדוד"
    mention = "רחוב הרצל"
    start = text_value.index(mention)
    end = start + len(mention)
    citation_map = [{"start": 0, "end": len(text_value), "page": 3}]

    result = canonicalizer.validate_mention_span(
        extracted_text=text_value,
        citation_map=citation_map,
        mention_text=mention,
        start_offset=start,
        end_offset=end,
    )

    assert result.is_valid is True
    assert result.start_page == 3
    assert result.end_page == 3
    assert result.reason_code is None


def test_validate_mention_span_rejects_out_of_range_offsets() -> None:
    canonicalizer = SemanticCanonicalizer()
    text_value = "תוכן קצר"

    result = canonicalizer.validate_mention_span(
        extracted_text=text_value,
        citation_map=[],
        mention_text="תוכן",
        start_offset=10,
        end_offset=20,
    )

    assert result.is_valid is False
    assert result.reason_code == SemanticRejectReason.SPAN_OUT_OF_RANGE


def test_validate_mention_span_can_require_page_resolution() -> None:
    canonicalizer = SemanticCanonicalizer()
    text_value = "החלטה בנושא תכנית פיתוח"
    mention = "תכנית פיתוח"
    start = text_value.index(mention)
    end = start + len(mention)

    result = canonicalizer.validate_mention_span(
        extracted_text=text_value,
        citation_map=[],
        mention_text=mention,
        start_offset=start,
        end_offset=end,
        allow_unresolved_page=False,
    )

    assert result.is_valid is False
    assert result.reason_code == SemanticRejectReason.PAGE_RESOLUTION_FAILED
