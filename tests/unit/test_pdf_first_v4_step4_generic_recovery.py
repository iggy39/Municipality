from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_step4_module():
    path = Path(__file__).resolve().parents[2] / "rag_eval/runs/ashdod_2025_regular_3_short_pdf/pdf_first_pipeline/scripts/step4_v4_global_topic_assign.py"
    spec = importlib.util.spec_from_file_location("step4_v4_global_topic_assign", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


step4 = _load_step4_module()


def test_pure_agenda_section_heading_is_container() -> None:
    assert step4._looks_like_container_heading("סעיף1 :\u202b :הנושאים לדיון\u202b שאילתות") is True
    assert step4._looks_like_container_heading("הנושאים לדיון :שאילתות") is True


def test_numeric_prefixed_deferred_reply_is_non_topic() -> None:
    assert step4._looks_like_no_topic_continuation("22 ) – .תועבר אליך בהמשך") is True


def test_noisy_query_carrier_extracts_semantic_subject() -> None:
    contract = step4._topic_contract_from_headline(
        'שאילתא של ד"ר לחמני בנו "שא "העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל',
        structural_role="continuation",
        packet_role="protocol",
    )

    assert contract["is_topic_bearing"] is True
    assert "העמדת לוח מודעות" in contract["topic_subject_he"]


def test_procedural_dialogue_fragment_is_non_topic() -> None:
    reason = step4._non_topic_protocol_reason(
        headline="שניות מיקרופון אני אתן לך אישור לדבר",
        raw_text="שניות מיקרופון : עו\"ד כנפו אני אתן לך אישור, תדברי. אני לא נותן לך אפשרות כזאת",
        structural_role="body",
        packet_role="protocol",
    )

    assert reason == "procedural_dialogue_fragment"


def test_procedural_dialogue_detection_is_role_independent() -> None:
    reason = step4._non_topic_protocol_reason(
        headline="שניות מיקרופון אני רוצה לדבר גם אני רוצה לדבר",
        raw_text="שניות מיקרופון אני רוצה לדבר גם אני רוצה לדבר אני לא נותן לך אפשרות",
        structural_role="section_heading",
        packet_role="protocol",
    )

    assert reason == "procedural_dialogue_fragment"


def test_previous_decision_continuation_without_subject_is_non_topic() -> None:
    assert step4._looks_like_no_topic_continuation("הוועדה עומדת מאחורי החלטתה מהדיון הקודם ולמען הסדר הטוב") is True


def test_short_domain_subject_is_not_no_topic_continuation() -> None:
    assert step4._looks_like_no_topic_continuation("הקלטת עובדים") is False
    assert step4._non_topic_protocol_reason(headline="הקלטת עובדים", raw_text='שאילתה בנושא "הקלטת עובדים"', structural_role="continuation", packet_role="protocol") is None


def test_body_transcript_without_explicit_marker_has_no_topic_provenance() -> None:
    unit = {
        "structural_role": "body",
        "raw_text": "מר כהן אני מבקש לומר שהמשטרה הגיעה בבוקר והיה דיון ארוך על הבניין הישן ועל שריפה אפשרית",
    }

    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="המשטרה הגיעה בבוקר והיה דיון ארוך",
        raw_text=unit["raw_text"],
        topic_context_source="unit_heading",
        headline_source="raw_extracted",
        packet_role="protocol",
    ) == "body_without_headline_topic_provenance"


def test_long_outline_transcript_window_is_not_standalone_topic() -> None:
    raw_text = " ".join(["פרוטוקול ישיבה מן המניין", "מר לוי אמר שהצעה לסדר נמשכה זמן רב", "ומכאן המשיך דיון ארוך מאוד על נושאים שונים"] * 8)
    unit = {"structural_role": "outline_item", "raw_text": raw_text}

    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="פרוטוקול ישיבה מן המניין מר לוי אמר שהצעה לסדר נמשכה זמן רב",
        raw_text=raw_text,
        topic_context_source="unit_heading",
        headline_source="raw_extracted",
        packet_role="protocol",
    ) == "transcript_window_without_bounded_headline"


def test_explicit_local_topic_marker_allows_body_topic_extraction() -> None:
    raw_text = 'שאילתה בנושא "הקלטת עובדים" נשאלה על ידי חבר מועצה'
    unit = {"structural_role": "continuation", "raw_text": raw_text}

    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="הקלטת עובדים",
        raw_text=raw_text,
        topic_context_source="unit_heading",
        headline_source="raw_extracted",
        packet_role="protocol",
    ) is None


def test_incidental_body_marker_in_long_transcript_does_not_create_topic() -> None:
    raw_text = " ".join(["פרוטוקול ישיבה מן המניין", "רשות המים התריעה בנושא הזיהום אבל הדובר המשיך בדיון ארוך"] * 12)
    unit = {"structural_role": "outline_item", "raw_text": raw_text}

    assert step4._has_bounded_raw_topic_marker(raw_text) is False
    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="פרוטוקול ישיבה מן המניין",
        raw_text=raw_text,
        topic_context_source="unit_heading",
        headline_source="raw_extracted",
        packet_role="protocol",
    ) == "transcript_window_without_bounded_headline"


def test_incidental_strong_marker_in_long_transcript_does_not_create_topic() -> None:
    raw_text = " ".join(["פרוטוקול ישיבה מן המניין", "מר לוי אמר שהשאילתה בנושא העצים כבר נדונה", "ומכאן המשיך דיון ארוך"] * 10)
    unit = {"structural_role": "outline_item", "raw_text": raw_text}

    assert step4._has_bounded_raw_topic_marker(raw_text) is False
    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="שאילתה בנושא העצים כבר נדונה",
        raw_text=raw_text,
        topic_context_source="unit_heading",
        headline_source="raw_extracted",
        packet_role="protocol",
    ) == "transcript_window_without_bounded_headline"


def test_cleaned_continuation_without_source_is_not_topic() -> None:
    unit = {"structural_role": "task_row", "raw_text": "מר כהן ממשיך לדבר על בית הספר והעירייה"}

    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="בית הספר הוא מיקרוקוסמוס של החברה בישראל",
        raw_text=unit["raw_text"],
        topic_context_source="unit_heading",
        headline_source="none",
        packet_role="protocol",
    ) == "missing_topic_headline_provenance"


def test_committee_protocol_carrier_is_not_standalone_topic() -> None:
    assert step4._looks_like_procedural_carrier_heading("פרוטוקול ועדת תמיכות") is True
    assert step4._non_topic_protocol_reason(headline="פרוטוקול ועדת תמיכות", raw_text="פרוטוקול ועדת תמיכות", structural_role="outline_item", packet_role="protocol") == "procedural_carrier_heading"


def test_explicit_visual_header_is_not_overwritten_by_raw_region_repair() -> None:
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "page": 1,
            "header_text": "בניינים מסוכנים בעיר",
            "raw_text": "חבר מועצה פלוני – בניינים מסוכנים בעיר .1 :מר היו\"ר .הצעה לסדר יום",
            "structural_role": "outline_item",
        },
        facts=[],
        max_raw_chars=700,
        attachment_contexts=[],
        document_context={"packet_role": "protocol"},
        topic_context={"topic_identification_text": "בניינים מסוכנים בעיר", "context_source": "unit_heading"},
        document_child_candidates=[],
        topic_tree=step4.global_topic_tree_payload(),
        topic_index={},
    )

    assert item["topic_headline_he"] == "בניינים מסוכנים בעיר"
    assert item["topic_identification_context"] == "בניינים מסוכנים בעיר"


def test_weak_body_agenda_candidate_becomes_evidence_only_fragment() -> None:
    row = {
        "packet_role": "protocol",
        "structure_unit_id": "u1",
        "semantic_unit_id": "u1",
        "structural_role": "body",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "topic_node_status": "candidate",
        "root_topic_id": "root_agenda_queries",
        "root_label_he": "סדר יום ושאילתות",
        "topic_subject_he": "השכירות, נושא מאוד חשוב, מעבר לדירות למגורים",
        "topic_headline_he": "השכירות, נושא מאוד חשוב, מעבר לדירות למגורים",
        "topic_assignment_route": "deterministic_v4_candidate_review:topic_subject_collapsed_to_procedural_root",
    }

    normalized = step4._normalize_protocol_non_topic_assignments([row])[0]

    assert normalized["row_type"] == "fragment"
    assert normalized["is_topic_bearing"] is False
    assert normalized["topic_node_status"] == "active"
    assert normalized["topic_subject_he"] is None


def test_unknown_sports_root_aligns_to_allowed_culture_sport_root() -> None:
    item = {
        "is_topic_bearing": True,
        "document_context": {"packet_role": "protocol"},
        "topic_headline_he": "הפועל אשדוד כדוריד מחזיקת גביע המדינה ואלופת המדינה",
    }
    parsed = {
        "root_topic_id": "root_sports_culture",
        "topic_supporting_quote_he": "הפועל אשדוד כדוריד מחזיקת גביע המדינה",
        "rationale_he": "נושא זה עוסק בקבוצת ספורט מקומית ובהישגיה",
    }

    assert step4._align_unknown_dicta_root(item=item, parsed=parsed, subject=item["topic_headline_he"]) == "root_culture_sport"


def test_procedural_dicta_root_recovers_local_economy_root() -> None:
    item = {
        "is_topic_bearing": True,
        "document_context": {"packet_role": "protocol"},
        "raw_text": "שאילתה בנושא העתקת חלק ניכר ממפעל אלתא באשדוד לבאר שבע ומעבר עובדים",
        "root_topic_candidates": [],
    }

    recovered = step4._recover_root_only_topic(
        item=item,
        parsed={"root_topic_id": "root_agenda_queries"},
        subject="העתקת חלק ניכר ממפעל אלתא באשדוד לבאר שבע ומעבר עובדים",
        fallback_root_topic_id="root_agenda_queries",
    )

    assert recovered == "root_local_economy"


def test_procedural_dicta_root_recovers_transport_root_from_subject_span() -> None:
    item = {
        "is_topic_bearing": True,
        "document_context": {"packet_role": "protocol"},
        "raw_text": "שאילתא בנושא העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
        "root_topic_candidates": [],
    }

    recovered = step4._recover_root_only_topic(
        item=item,
        parsed={"root_topic_id": "root_agenda_queries"},
        subject="העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
        fallback_root_topic_id="root_transport_safety",
    )

    assert recovered == "root_transport_safety"


def test_body_root_evidence_prefers_transport_over_weak_infrastructure() -> None:
    item = {
        "document_context": {"packet_role": "protocol"},
        "structural_role": "task_row",
        "topic_subject_he": "עדכון ראש העיר",
        "topic_headline_he": "עדכון ראש העיר",
        "raw_text": "המשטרה העבירה תחקיר, ונפגש עם יו\"ר הרלב\"ד ומשרד התחבורה כדי למנוע תאונות דרכים בכביש ובצמתים",
    }

    assert step4._strong_body_evidence_root(item) == "root_transport_safety"


def test_single_hebrew_keyword_alignment_requires_whole_token() -> None:
    assert step4._alignment_phrase_score("רב", step4._norm("יו\"ר הרלב\"ד הארצי")) == 0.0
    assert step4._alignment_phrase_score("מים", step4._norm("מתחת לשמים הפתוחים")) == 0.0
    assert step4._alignment_phrase_score("משטרה", step4._norm("בדיקות של המשטרה")) == 0.9
