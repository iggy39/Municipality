from __future__ import annotations

from municipality.decisions import _parse_decision_candidates, _parse_vote, _validate_fallback_output


HE_VOTE_SLASH_TEXT = (
    "הוחלט לאשר תקציב. "
    "בעד/נגד/נמנע: 9/1/0"
)
HE_VOTE_COUNTS_TEXT = (
    "תוצאה: בעד 7 "
    "נגד 2 נמנעים 1"
)
HE_UNANIMOUS_TEXT = "הסעיף אושר פה אחד"
HE_UNCERTAIN_TEXT = (
    "התקיים דיון: "
    "בעד ונגד אך ללא מספרים"
)
HE_MEETING_HEADING = "ישיבת מועצה רגילה"
HE_SUMMARY_AND_DECISIONS = "סיכום והחלטות"
HE_DECISION_1 = (
    "אישור תקציב החינוך "
    "הוחלט לאשר. בעד/נגד/נמנע: 9/1/0"
)
HE_DECISION_2 = "מינוי ועדת ביקורת אושר פה אחד"
HE_BUDGET_APPROVAL = "אישור תקציב"


def test_parse_vote_supports_counts_and_unanimous_patterns() -> None:
    slash = _parse_vote(HE_VOTE_SLASH_TEXT)
    counts = _parse_vote(HE_VOTE_COUNTS_TEXT)
    unanimous = _parse_vote(HE_UNANIMOUS_TEXT)
    uncertain = _parse_vote(HE_UNCERTAIN_TEXT)

    assert slash is not None
    assert slash["for_count"] == 9
    assert slash["against_count"] == 1
    assert slash["abstain_count"] == 0

    assert counts is not None
    assert counts["for_count"] == 7
    assert counts["against_count"] == 2
    assert counts["abstain_count"] == 1

    assert unanimous is not None
    assert unanimous["unanimous"] is True

    assert uncertain is not None
    assert uncertain["is_uncertain"] is True


def test_parse_decision_candidates_detects_numbered_decisions() -> None:
    text = "\n".join(
        [
            HE_MEETING_HEADING,
            HE_SUMMARY_AND_DECISIONS,
            f"1. {HE_DECISION_1}",
            f"2. {HE_DECISION_2}",
        ]
    )

    candidates = _parse_decision_candidates(text)

    assert len(candidates) >= 2
    assert candidates[0].decision_number == "1"
    assert HE_BUDGET_APPROVAL in candidates[0].decision_text
    assert candidates[0].vote_hint is not None


def test_validate_fallback_rejects_text_from_different_row() -> None:
    source_window = (
        "החלטה ראשונה בנושא הקצאה זמנית של כלל חסידי. "
        "הצעת המחיר אושרה. "
        "החלטה שניה מאשרים הצעת מחיר להזזת מבנים."
    )
    payload = {
        "decision_text": "החלטה שניה מאשרים הצעת מחיר להזזת מבנים",
        "span_start": 45,
        "span_end": 86,
    }

    accepted, reasons = _validate_fallback_output(
        payload=payload,
        source_window=source_window,
        row_text="החלטה ראשונה בנושא הקצאה זמנית של כלל חסידי",
    )

    assert accepted is False
    assert "fallback_not_aligned_to_row" in reasons
