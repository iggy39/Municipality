from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx

from municipality import topic_subjects as topic_subjects_module
from municipality.topic_subjects import (
    OllamaTopicSubjectClient,
    MockTopicSubjectV3Client,
    TopicSubjectResearchConfig,
    apply_event_block_links,
    apply_event_grouping,
    build_topic_subject_v3_event_contexts,
    build_topic_subject_event_blocks,
    corrected_hebrew_text,
    extract_subjects_from_event_blocks,
    is_countable_subject_event,
    is_dependent_detail_candidate,
    normalize_topic_subject_v3_event_payload,
    preclassify_trivial_subject_payload,
    process_topic_subject_v3_context,
    process_topic_subject_payload,
    quality_report_markdown,
    topic_subject_prompt_payload,
    topic_subject_v3_extraction_payload,
    topic_subject_v3_event_to_dict,
    topic_subject_v3_normalization_payload,
    topic_subject_v3_row_quality_from_payloads,
    validate_topic_subject_v3_event_payload,
)
from municipality.topic_decisions import TopicDecisionArtifact


REQUEST_TEXT = "אודה לאישור מועצת העיר לנסיעה המקצועית, כולל עלויות השתתפות ונסיעה."
DECISION_TEXT = "חברי המועצה מאשרים פה אחד את הנסיעה המקצועית ואת מימון העלויות."
CONDITION_TEXT = "בהזמנות הנגזרות: מחוזה חתום כדין הנגזר ממכרז כדין או במסלול פטור ממכרז מאושר בידי היועמ\"ש, החלטות של ועדת רכש, הצעות מחיר מאושרות כדין, הקצבות ותמיכות, הסכום ללא הגבלה."
DELEGATION_REQUEST_TEXT = "אבקש את אישור מועצת העיר להאצלת סמכויות חתימה לגב' לינור כהן כממ חשבת מינהל תפעול בהתאם למפורט: הזמנות עד לסכום 10,000 שח."


def test_topic_subject_prompt_includes_topic_aware_non_closed_examples() -> None:
    artifact = _artifact_dataclass(real_text=REQUEST_TEXT, topic_label_he="חינוך")

    payload = topic_subject_prompt_payload(artifact=artifact, max_text_chars=1000)

    assert payload["known_topic"]["topic_label_he"] == "חינוך"
    assert "topic_aware_examples_not_a_codelist" in payload
    assert any(example["known_topic"] == "חינוך" for example in payload["topic_aware_examples_not_a_codelist"])
    assert any("not closed lists" in requirement or "not a closed list" in requirement for requirement in payload["requirements"])
    assert any("exactly one primary action" in requirement for requirement in payload["requirements"])
    assert all(not example["subject_root_label_he"].startswith("חינוך") for example in payload["topic_aware_examples_not_a_codelist"])
    assert payload["corrected_evidence_text"]


def test_process_topic_subject_payload_accepts_open_request_subject_without_decision() -> None:
    artifact = _artifact_dataclass(real_text=REQUEST_TEXT, topic_label_he="חינוך")

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                {
                    "subject_root_label_he": "בקשה",
                    "subject_child_label_he": "בקשת אישור נסיעה",
                    "subject_object_he": "נסיעה מקצועית",
                    "subject_details_he": "עלויות השתתפות ונסיעה",
                    "subject_summary_he": "בקשה לאישור נסיעה מקצועית",
                    "what_text_is_about_he": "הטקסט מבקש אישור לנסיעה מקצועית ועלויותיה.",
                    "is_decision": False,
                    "decision": None,
                    "confidence": 0.86,
                    "rationale_he": "יש בקשה אך אין החלטה.",
                }
            ],
            "overall_summary_he": "בקשת אישור נסיעה",
            "rationale_he": "open subject",
        },
    )

    assert len(subjects) == 1
    assert subjects[0].validation_status == "accepted"
    assert subjects[0].subject_payload["subject_root_label_he"] == "בקשה"
    assert subjects[0].subject_payload["subject_child_label_he"] == "בקשת אישור"
    assert subjects[0].subject_payload["subject_object_he"] == "נסיעה מקצועית"
    assert subjects[0].subject_payload["is_decision"] is False
    assert quality.my_judgment == "subject_candidate"
    assert quality.decision_by_dicta == ""


def test_process_topic_subject_payload_rejects_request_misclassified_as_decision() -> None:
    artifact = _artifact_dataclass(real_text=REQUEST_TEXT, topic_label_he="חינוך")

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                {
                    "subject_root_label_he": "אישור נסיעה מקצועית",
                    "subject_child_label_he": "אישור נסיעה",
                    "subject_object_he": "נסיעה מקצועית",
                    "subject_details_he": "בקשת אישור ללא החלטה",
                    "subject_summary_he": "אישור נסיעה מקצועית",
                    "what_text_is_about_he": "הטקסט מבקש אישור לנסיעה מקצועית.",
                    "is_decision": True,
                    "decision": {
                        "decision_label_he": "אישור נסיעה",
                        "decision_summary_he": "אישור נסיעה מקצועית",
                        "source_quote_he": "אודה לאישור מועצת העיר לנסיעה המקצועית",
                        "confidence": 0.8,
                        "limitations": [],
                    },
                    "confidence": 0.8,
                    "rationale_he": "misclassified request",
                }
            ]
        },
    )

    assert len(subjects) == 1
    assert subjects[0].validation_status == "decision_rejected"
    assert subjects[0].subject_payload["is_decision"] is False
    assert subjects[0].subject_payload["rejected_decision"]["decision_label_he"] == "אישור נסיעה"
    assert quality.my_judgment == "subject_candidate_with_rejected_decision"
    assert "request_or_proposal_without_decision_outcome" in quality.reason_for_failure


def test_process_topic_subject_payload_accepts_open_decision_label_when_grounded() -> None:
    artifact = _artifact_dataclass(real_text=DECISION_TEXT, topic_label_he="חינוך")

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                {
                    "subject_root_label_he": "אישור",
                    "subject_child_label_he": "אישור מימון נסיעה מקצועית",
                    "subject_object_he": "נסיעה מקצועית",
                    "subject_details_he": "מימון העלויות",
                    "subject_summary_he": "אישור נסיעה מקצועית ומימון העלויות",
                    "what_text_is_about_he": "הטקסט מאשר את הנסיעה ואת מימון העלויות.",
                    "is_decision": True,
                    "decision": {
                        "decision_label_he": "אישור מימון נסיעה מקצועית",
                        "decision_summary_he": "אישור הנסיעה ומימון העלויות",
                        "source_quote_he": "חברי המועצה מאשרים פה אחד את הנסיעה המקצועית ואת מימון העלויות",
                        "confidence": 0.91,
                        "limitations": [],
                    },
                    "confidence": 0.91,
                    "rationale_he": "יש אישור מפורש.",
                }
            ]
        },
    )

    assert len(subjects) == 1
    assert subjects[0].validation_status == "accepted"
    assert subjects[0].subject_payload["subject_root_label_he"] == "אישור החלטה"
    assert subjects[0].subject_payload["subject_child_label_he"] == ""
    assert subjects[0].subject_payload["decision"]["decision_label_he"] == "אישור מימון נסיעה מקצועית"
    assert quality.my_judgment == "decision_candidate"
    assert "אישור מימון נסיעה מקצועית" in quality.decision_by_dicta


def test_process_topic_subject_payload_turns_condition_list_into_action_subject() -> None:
    artifact = _artifact_dataclass(real_text=CONDITION_TEXT, topic_label_he="הסכמים והתקשרויות")

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                {
                    "subject_root_label_he": "האצלת סמכויות",
                    "subject_child_label_he": "תנאי חתימה",
                    "subject_object_he": "הסכמים והתקשרויות",
                    "subject_details_he": "הזמנות, חוזים, ועדת רכש ותמיכות",
                    "subject_summary_he": "תנאים להזמנות והתקשרויות",
                    "what_text_is_about_he": "הטקסט מפרט תנאים והיקף תחולה להזמנות והתקשרויות.",
                    "is_decision": False,
                    "decision": None,
                    "confidence": 0.83,
                    "rationale_he": "lists scope",
                }
            ]
        },
    )

    assert len(subjects) == 1
    assert subjects[0].validation_status == "accepted"
    assert subjects[0].subject_payload["subject_root_label_he"] == "הגדרה"
    assert subjects[0].subject_payload["subject_child_label_he"] == "הגדרת תנאים"
    assert "חוזים" in subjects[0].subject_payload["subject_object_he"] or "הסכמים" in subjects[0].subject_payload["subject_object_he"]
    assert quality.subject_root_by_dicta == "הגדרה"
    assert quality.subject_child_by_dicta == "הגדרת תנאים"


def test_event_grouping_links_scope_detail_to_previous_action_anchor() -> None:
    anchor_artifact = _artifact_dataclass(
        artifact_id="artifact-anchor",
        real_text=DELEGATION_REQUEST_TEXT,
        topic_label_he="האצלת סמכויות חתימה",
        source_ordinal=2,
    )
    detail_artifact = _artifact_dataclass(
        artifact_id="artifact-detail",
        real_text=CONDITION_TEXT,
        topic_label_he="הסכמים והתקשרויות",
        source_ordinal=3,
    )
    anchor_subjects, anchor_quality = process_topic_subject_payload(
        artifact=anchor_artifact,
        model_payload={
            "subjects": [
                {
                    "subject_root_label_he": "בקשה",
                    "subject_child_label_he": "בקשת אישור",
                    "subject_object_he": "האצלת סמכויות חתימה לגב' לינור כהן",
                    "subject_details_he": "הזמנות עד לסכום 10,000 שח",
                    "subject_summary_he": "בקשה לאישור האצלת סמכויות חתימה",
                    "what_text_is_about_he": "בקשה לאישור האצלת סמכויות חתימה.",
                    "is_decision": False,
                    "decision": None,
                    "confidence": 0.9,
                    "rationale_he": "anchor",
                }
            ]
        },
    )
    detail_subjects, detail_quality = process_topic_subject_payload(
        artifact=detail_artifact,
        model_payload={
            "subjects": [
                {
                    "subject_root_label_he": "הגדרה",
                    "subject_child_label_he": "הגדרת תנאים",
                    "subject_object_he": "הסכמים והתקשרויות",
                    "subject_details_he": "חוזה חתום, מכרז, ועדת רכש ותמיכות",
                    "subject_summary_he": "פירוט תנאים",
                    "what_text_is_about_he": "הטקסט מפרט תנאים והיקף תחולה.",
                    "is_decision": False,
                    "decision": None,
                    "confidence": 0.88,
                    "rationale_he": "detail",
                }
            ]
        },
    )

    apply_event_grouping(
        artifacts=[anchor_artifact, detail_artifact],
        extracted=anchor_subjects + detail_subjects,
        quality_rows=[anchor_quality, detail_quality],
    )

    assert anchor_quality.row_role == "action_anchor"
    assert anchor_quality.anchor_status == "validated_anchor"
    assert anchor_quality.event_topic == "האצלת סמכויות חתימה"
    assert anchor_quality.subject_child_by_dicta == "בקשה להאצלת סמכויות"
    assert detail_quality.row_role == "dependent_detail"
    assert detail_quality.anchor_status == "linked_to_validated_anchor"
    assert detail_quality.event_topic == "האצלת סמכויות חתימה"
    assert detail_quality.subject_root_by_dicta == "בקשה"
    assert detail_quality.subject_child_by_dicta == "בקשה להאצלת סמכויות"
    assert detail_quality.link_confidence >= 0.62


def test_preclassify_trivial_subject_payload_handles_date_reference_and_signature() -> None:
    date_payload = preclassify_trivial_subject_payload(_artifact_dataclass(real_text='כ"ח כסלו תשפ"ה', topic_label_he="האצלת סמכויות חתימה"))
    ref_payload = preclassify_trivial_subject_payload(_artifact_dataclass(real_text="29 דצמבר24 :בתשובתך אנא ציין /סמ07-02-01-075-2024", topic_label_he="סדר יום ושאילתות"))
    signature_payload = preclassify_trivial_subject_payload(_artifact_dataclass(real_text="בכבוד רב, עודד לוי גזבר העירייה", topic_label_he="האצלת סמכויות חתימה"))

    assert date_payload is not None
    assert date_payload["subjects"] == []
    assert date_payload["non_subject"] is True
    assert date_payload["artifact_role"] == "document_date"
    assert ref_payload is not None
    assert ref_payload["subjects"] == []
    assert ref_payload["artifact_role"] == "reference_number"
    assert signature_payload is not None
    assert signature_payload["subjects"] == []
    assert signature_payload["artifact_role"] == "signature_footer"


def test_process_topic_subject_payload_reports_non_subject_document_fragment() -> None:
    artifact = _artifact_dataclass(real_text='כ"ח כסלו תשפ"ה', topic_label_he="האצלת סמכויות חתימה")
    payload = preclassify_trivial_subject_payload(artifact)
    assert payload is not None

    subjects, quality = process_topic_subject_payload(artifact=artifact, model_payload=payload)

    assert subjects == []
    assert quality.my_judgment == "non_subject_document_fragment"
    assert quality.status == "non_subject"
    assert quality.artifact_role == "document_date"
    assert quality.topic_relevance == "not_topic_bearing"


def test_preclassify_contact_info_as_non_subject() -> None:
    payload = preclassify_trivial_subject_payload(
        _artifact_dataclass(real_text="רחוב הגדוד העיברי10 אשדוד, ת.ד28 מיקוד77100 טלפון08-8545231/3 פקס08-8677794", topic_label_he="סדר יום ושאילתות")
    )

    assert payload is not None
    assert payload["subjects"] == []
    assert payload["artifact_role"] == "contact_info"


def test_preclassify_attachment_references_and_agenda_markers_as_non_subject() -> None:
    attachment_payload = preclassify_trivial_subject_payload(_artifact_dataclass(real_text='25 ) - מצ"ל 1.', topic_label_he="סדר יום ושאילתות"))
    named_attachment_payload = preclassify_trivial_subject_payload(_artifact_dataclass(real_text='28 )(ביטון- מצ"ל', topic_label_he="סדר יום ושאילתות"))
    agenda_payload = preclassify_trivial_subject_payload(_artifact_dataclass(real_text="2. :הצעות לסדר 2.", topic_label_he="סדר יום ושאילתות"))
    topics_payload = preclassify_trivial_subject_payload(_artifact_dataclass(real_text="25 ) - מצ\"ל :הנושאים לדיון", topic_label_he="סדר יום ושאילתות"))
    ocr_agenda_payload = preclassify_trivial_subject_payload(_artifact_dataclass(real_text="ע:ל סדר היום", topic_label_he="סדר יום ושאילתות"))

    assert attachment_payload is not None
    assert attachment_payload["artifact_role"] == "attachment_reference"
    assert named_attachment_payload is not None
    assert named_attachment_payload["artifact_role"] == "attachment_reference"
    assert agenda_payload is not None
    assert agenda_payload["artifact_role"] == "agenda_marker"
    assert topics_payload is not None
    assert topics_payload["artifact_role"] in {"agenda_marker", "attachment_reference"}
    assert ocr_agenda_payload is not None
    assert ocr_agenda_payload["artifact_role"] == "agenda_marker"


def test_preclassify_does_not_hide_substantive_text_with_footer_suffix() -> None:
    payload = preclassify_trivial_subject_payload(
        _artifact_dataclass(
            real_text="2. מתן פיצוי מידי לתושבים שנגרם להם נזק וטיפול במעליות התקועות, בברכה הלן גלבר",
            topic_label_he="תכנון ובנייה",
        )
    )

    assert payload is None


def test_preclassify_protocol_and_meeting_headers_as_non_subject() -> None:
    protocol_payload = preclassify_trivial_subject_payload(
        _artifact_dataclass(real_text="פרוטוקול ועדת מלגות 16 בדצמבר 2024 מס' פרוטוקול 2024 /", topic_label_he="דת ושירותי דת")
    )
    meeting_payload = preclassify_trivial_subject_payload(
        _artifact_dataclass(
            real_text="5 פרוטוקול מישיבת ועדת מלגות שהתקיימה בתאריך 16.12.2024 משתתפים ד'ר אלינה ברדץ' יאלוב יו'ר ועדת מלגות מר עזרא שור חבר ועדה",
            topic_label_he="סדר יום ושאילתות",
        )
    )

    assert protocol_payload is not None
    assert protocol_payload["subjects"] == []
    assert protocol_payload["artifact_role"] == "protocol_header"
    assert meeting_payload is not None
    assert meeting_payload["subjects"] == []
    assert meeting_payload["artifact_role"] in {"protocol_header", "meeting_header"}


def test_preclassify_meeting_metadata_and_participant_list_as_non_subject() -> None:
    metadata_payload = preclassify_trivial_subject_payload(
        _artifact_dataclass(
            real_text="ישיבת מועצה מאושר מספר דיון 39919 התקיים בתאריך 05/03/2025 נוהל עי עוד גבי כנפו מיקום הישיבה אולם מועצת העיר תועד עי נאדין אשכנזי תאריך אישור 19/03/2025",
            topic_label_he="סדר יום ושאילתות",
        )
    )
    participants_payload = preclassify_trivial_subject_payload(
        _artifact_dataclass(
            real_text="השתתפו דר יחיאל לסרי ראש עיר לשכת ראש העיר; עוד גבי כנפו ממק רהע לשכת ממק רהע; עוד אלי נכט סגן רהע; הלן גלבר חברת מועצה",
            topic_label_he="סדר יום ושאילתות",
        )
    )
    spaced_subject_payload = preclassify_trivial_subject_payload(
        _artifact_dataclass(real_text="נ :ושא הדיון ישיבת מועצה רגילה מס 3.25 - קדנציה 12", topic_label_he="סדר יום ושאילתות")
    )
    participant_continuation_payload = preclassify_trivial_subject_payload(
        _artifact_dataclass(
            real_text="אופיר לסרי- חבר מועצה- חבר מועצה; עוד אלכסנדר אוברפלד- חבר מועצה- חבר מועצה; הלן גלבר- חברת מועצה- חבר מועצה",
            topic_label_he="סדר יום ושאילתות",
        )
    )

    assert metadata_payload is not None
    assert metadata_payload["subjects"] == []
    assert metadata_payload["artifact_role"] == "meeting_header"
    assert participants_payload is not None
    assert participants_payload["subjects"] == []
    assert participants_payload["artifact_role"] == "meeting_header"
    assert spaced_subject_payload is not None
    assert spaced_subject_payload["artifact_role"] == "meeting_header"
    assert participant_continuation_payload is not None
    assert participant_continuation_payload["artifact_role"] == "meeting_header"


def test_event_grouping_does_not_link_details_to_suspect_action_anchor() -> None:
    anchor_artifact = _artifact_dataclass(
        artifact_id="artifact-suspect-anchor",
        real_text="5 פרוטוקול מישיבת ועדת מלגות שהתקיימה בתאריך 16.12.2024 משתתפים ד'ר אלינה ברדץ' יאלוב יו'ר ועדת מלגות מר עזרא שור חבר ועדה",
        topic_label_he="סדר יום ושאילתות",
        source_ordinal=5,
    )
    detail_artifact = _artifact_dataclass(
        artifact_id="artifact-suspect-detail",
        real_text="1. עד היום רק סטודנטים תושבי אשדוד אשר פועלים במסגרות של פרח בעיר אשדוד יכלו להירשם ולקבל את ההשלמה למלגה של 10,000 שח.",
        topic_label_he="חינוך",
        source_ordinal=6,
    )
    anchor_subjects, anchor_quality = process_topic_subject_payload(
        artifact=anchor_artifact,
        model_payload={
            "subjects": [
                {
                    "subject_root_label_he": "אישור",
                    "subject_child_label_he": "אישור מתן מלגה",
                    "subject_object_he": "מלגה",
                    "subject_details_he": "",
                    "subject_summary_he": "",
                    "what_text_is_about_he": "",
                    "is_decision": False,
                    "decision": None,
                    "confidence": 0.8,
                    "rationale_he": "model false anchor",
                }
            ]
        },
    )
    detail_subjects, detail_quality = process_topic_subject_payload(
        artifact=detail_artifact,
        model_payload={
            "subjects": [
                {
                    "subject_root_label_he": "הגדרה",
                    "subject_child_label_he": "הגדרת תנאים",
                    "subject_object_he": "מלגה",
                    "subject_details_he": "תנאי זכאות וסכום מלגה",
                    "subject_summary_he": "פירוט תנאי מלגה",
                    "what_text_is_about_he": "הטקסט מפרט תנאי זכאות וסכום מלגה.",
                    "is_decision": False,
                    "decision": None,
                    "confidence": 0.8,
                    "rationale_he": "detail",
                }
            ]
        },
    )

    apply_event_grouping(
        artifacts=[anchor_artifact, detail_artifact],
        extracted=anchor_subjects + detail_subjects,
        quality_rows=[anchor_quality, detail_quality],
    )

    assert anchor_quality.row_role in {"suspect_anchor", "orphan_detail"}
    assert anchor_quality.anchor_status != "validated_anchor"
    assert detail_quality.row_role == "orphan_detail"
    assert detail_quality.linked_event_id == ""


def test_condition_text_with_exchange_terms_gets_generic_condition_child() -> None:
    artifact = _artifact_dataclass(
        real_text="הסטודנטים שיצטרפו למלגת הפיס יצטרכו לבצע בתמורה 20 שעות במסגרות של מרכז כיוונים",
        topic_label_he="תמיכות",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                {
                    "subject_root_label_he": "הגדרה",
                    "subject_child_label_he": "אישור השתתפות",
                    "subject_object_he": "מלגת מפעל הפיס",
                    "subject_details_he": "20 שעות פעילות בתמורה למלגה",
                    "subject_summary_he": "תנאי מלגה",
                    "what_text_is_about_he": "הטקסט מתאר תנאים להצטרפות למלגה.",
                    "is_decision": False,
                    "decision": None,
                    "confidence": 0.8,
                    "rationale_he": "condition",
                }
            ]
        },
    )

    assert len(subjects) == 1
    assert subjects[0].subject_payload["subject_root_label_he"] == "הגדרה"
    assert subjects[0].subject_payload["subject_child_label_he"] == "הגדרת תנאים"
    assert quality.subject_child_by_dicta == "הגדרת תנאים"


def test_event_grouping_links_split_amount_and_vote_continuations_to_validated_anchor() -> None:
    request_artifact = _artifact_dataclass(
        artifact_id="artifact-scholarship-request",
        real_text="ההצעה לאישורכם אנו מבקשים לאשר מול חברי הועדה, מתן מלגת מפעל הפיס כלומר השלמה ל10,",
        topic_label_he="תמיכות",
        source_ordinal=3,
    )
    amount_artifact = _artifact_dataclass(
        artifact_id="artifact-scholarship-amount",
        real_text="000 ₪ לסטודנטים תושבי אשדוד בלבד בשנת הלימודים הקרובה בלבד תשפה וזאת תמורת 20 שעות נוספות בקהילה",
        topic_label_he="תמיכות",
        source_ordinal=4,
    )
    decision_artifact = _artifact_dataclass(
        artifact_id="artifact-scholarship-decision",
        real_text="חברי הועדה אישרו את ההצעה למתן מלגת מפעל הפיס לסטודנטים תושבי אשדוד בתמורה ל-",
        topic_label_he="מנהל עירוני ומינויים",
        source_ordinal=5,
    )
    vote_artifact = _artifact_dataclass(
        artifact_id="artifact-scholarship-votes",
        real_text="20 שעות התנדבות במיזמים של מרכז כיוונים אשדוד דר אלינה ברדץ יאלוב בעד מר עזרא שור בעד בברכה",
        topic_label_he="תשתיות וסביבה",
        source_ordinal=6,
    )
    request_subjects, request_quality = process_topic_subject_payload(
        artifact=request_artifact,
        model_payload={"subjects": [_subject_payload(root="בקשה", child="בקשת אישור", object_text="מלגת מפעל הפיס", details="השלמה למלגה", evidence="מבקשים לאשר")]},
    )
    amount_subjects, amount_quality = process_topic_subject_payload(
        artifact=amount_artifact,
        model_payload={"subjects": [_subject_payload(root="אישור", child="אישור מלגה", object_text="מלגת מפעל הפיס", details="10,000 שח ו-20 שעות", evidence="אישור")]},
    )
    decision_subjects, decision_quality = process_topic_subject_payload(
        artifact=decision_artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="אישור",
                    child="אישור מלגה",
                    object_text="מלגת מפעל הפיס",
                    details="אישור ההצעה",
                    evidence="אישרו את ההצעה",
                    is_decision=True,
                    decision={
                        "decision_label_he": "אישור",
                        "decision_summary_he": "אישור מלגת מפעל הפיס",
                        "source_quote_he": "חברי הועדה אישרו את ההצעה למתן מלגת מפעל הפיס",
                        "confidence": 0.9,
                        "limitations": [],
                    },
                )
            ]
        },
    )
    vote_subjects, vote_quality = process_topic_subject_payload(
        artifact=vote_artifact,
        model_payload={"subjects": [_subject_payload(root="אישור", child="אישור השתתפות", object_text="שעות התנדבות", details="בעד ובעד", evidence="בעד")]},
    )

    apply_event_grouping(
        artifacts=[request_artifact, amount_artifact, decision_artifact, vote_artifact],
        extracted=request_subjects + amount_subjects + decision_subjects + vote_subjects,
        quality_rows=[request_quality, amount_quality, decision_quality, vote_quality],
    )

    assert request_quality.anchor_status == "validated_anchor"
    assert amount_quality.row_role == "dependent_detail"
    assert amount_quality.anchor_status == "linked_to_validated_anchor"
    assert decision_quality.anchor_status == "validated_anchor"
    assert vote_quality.row_role == "dependent_detail"
    assert vote_quality.anchor_status == "linked_to_validated_anchor"
    assert vote_quality.linked_event_id == decision_quality.event_id


def test_event_grouping_does_not_link_cross_topic_detail_without_shared_context() -> None:
    anchor_artifact = _artifact_dataclass(
        artifact_id="artifact-approved-school-program",
        real_text="חברי המועצה מאשרים את ההצעה להקמת תוכנית למניעת חרמות בבתי הספר",
        topic_label_he="מאבק בתופעות חברתיות בבתי ספר",
        source_ordinal=3,
    )
    unrelated_vote_artifact = _artifact_dataclass(
        artifact_id="artifact-unrelated-vote-fragment",
        real_text="בעד מר יניב קקון נגד מר מאיר אברז'ל נמנע חבר אחד בברכה",
        topic_label_he="שמות והנצחה",
        source_ordinal=4,
    )
    anchor_subjects, anchor_quality = process_topic_subject_payload(
        artifact=anchor_artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="אישור",
                    child="אישור השתתפות",
                    object_text="תוכנית למניעת חרמות בבתי הספר",
                    details="חברי המועצה מאשרים את ההצעה",
                    evidence="חברי המועצה מאשרים את ההצעה",
                    is_decision=True,
                    decision={
                        "decision_label_he": "אישור",
                        "decision_summary_he": "אישור תוכנית למניעת חרמות בבתי הספר",
                        "source_quote_he": "חברי המועצה מאשרים את ההצעה להקמת תוכנית למניעת חרמות בבתי הספר",
                        "confidence": 0.9,
                        "limitations": [],
                    },
                )
            ]
        },
    )
    vote_subjects, vote_quality = process_topic_subject_payload(
        artifact=unrelated_vote_artifact,
        model_payload={"subjects": [_subject_payload(root="אישור", child="אישור השתתפות", object_text="הצבעת חברים", details="בעד נגד נמנע", evidence="בעד")]},
    )

    apply_event_grouping(
        artifacts=[anchor_artifact, unrelated_vote_artifact],
        extracted=anchor_subjects + vote_subjects,
        quality_rows=[anchor_quality, vote_quality],
    )

    assert anchor_quality.anchor_status == "validated_anchor"
    assert vote_quality.row_role == "orphan_detail"
    assert vote_quality.anchor_status == "not_anchor"
    assert vote_quality.linked_event_id == ""
    assert "different_topic_without_shared_context" in vote_quality.link_reason


def test_event_grouping_does_not_link_protocol_header_to_previous_approval() -> None:
    approval_artifact = _artifact_dataclass(
        artifact_id="artifact-approved-protocol",
        real_text="חברי המועצה מאשרים פה אחד את הפרוטוקול 156023 05/03/2025",
        topic_label_he="סדר יום ושאילתות",
        source_ordinal=3,
    )
    next_protocol_header = _artifact_dataclass(
        artifact_id="artifact-next-protocol-header",
        real_text="סעיף19 : מתאריך 2.25 פרוטוקול ועדה מקצועית לתמיכות מס 20.02.25 החלטות פרוטוקול ועדה מקצועית לתמיכות מס 2.25",
        topic_label_he="סדר יום ושאילתות",
        source_ordinal=4,
    )
    approval_subjects, approval_quality = process_topic_subject_payload(
        artifact=approval_artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="אישור",
                    child="אישור פרוטוקול",
                    object_text="פרוטוקול 156023",
                    details="מאשרים פה אחד את הפרוטוקול",
                    evidence="מאשרים פה אחד את הפרוטוקול",
                    is_decision=True,
                    decision={
                        "decision_label_he": "אישור פרוטוקול",
                        "decision_summary_he": "אישור פרוטוקול 156023",
                        "source_quote_he": "חברי המועצה מאשרים פה אחד את הפרוטוקול 156023",
                        "confidence": 0.9,
                        "limitations": [],
                    },
                )
            ]
        },
    )
    header_subjects, header_quality = process_topic_subject_payload(
        artifact=next_protocol_header,
        model_payload={"subjects": [_subject_payload(root="אישור", child="אישור פרוטוקול", object_text="פרוטוקול ועדה מקצועית לתמיכות", details="החלטות פרוטוקול ועדה מקצועית", evidence="פרוטוקול ועדה מקצועית")]},
    )

    apply_event_grouping(
        artifacts=[approval_artifact, next_protocol_header],
        extracted=approval_subjects + header_subjects,
        quality_rows=[approval_quality, header_quality],
    )

    assert approval_quality.anchor_status == "validated_anchor"
    assert header_quality.row_role == "suspect_anchor"
    assert header_quality.anchor_status == "suspect_anchor"
    assert header_quality.linked_event_id == ""


def test_protocol_header_shape_is_not_dependent_detail_candidate() -> None:
    artifact = _artifact_dataclass(
        real_text="סעיף21 : )(ביטון 19.02.25 מתאריך 1.25 '. פרוטוקול ועדת פינויים מס10 החלטות פרוטוקול ועדת 'פינויים מס 1.25 מתאריך 19.02.",
        topic_label_he="סדר יום ושאילתות",
    )
    subjects, _quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="אישור", child="קביעת היקף", object_text="פרוטוקול ועדת פינויים", details="החלטה על אישור הפרוטוקול", evidence="פרוטוקול ועדת פינויים")]},
    )

    assert subjects
    assert not is_dependent_detail_candidate(artifact=artifact, item=subjects[0])


def test_preclassify_protocol_header_shape_without_action_as_non_subject() -> None:
    payload = preclassify_trivial_subject_payload(
        _artifact_dataclass(
            real_text="סעיף21 : )(ביטון 19.02.25 מתאריך 1.25 '. פרוטוקול ועדת פינויים מס10 החלטות פרוטוקול ועדת 'פינויים מס 1.25 מתאריך 19.02.",
            topic_label_he="סדר יום ושאילתות",
        )
    )

    assert payload is not None
    assert payload["subjects"] == []
    assert payload["non_subject"] is True
    assert payload["artifact_role"] == "protocol_header"


def test_event_grouping_excludes_naked_context_root_from_subject_tree() -> None:
    artifact = _artifact_dataclass(
        artifact_id="artifact-context-only",
        real_text="עדכוני ראש העיר",
        topic_label_he="עדכוני ראש העיר",
    )
    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="הגדרה", child="הגדרת הקשר", object_text="עדכוני ראש העיר", details="כותרת הקשר", evidence="עדכוני ראש העיר")]},
    )

    apply_event_grouping(artifacts=[artifact], extracted=subjects, quality_rows=[quality])

    assert quality.row_role == "context_detail"
    assert quality.status == "context_detail"
    assert not is_countable_subject_event(subjects[0])


def test_event_grouping_excludes_vote_metadata_from_subject_tree() -> None:
    artifact = _artifact_dataclass(
        artifact_id="artifact-vote-metadata",
        real_text="סעיף3 :הצביעו נמנע 156022 05/03/2025",
        topic_label_he="סדר יום ושאילתות",
    )
    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="נמנע", child="נמנע מהצבעה", object_text="סדר יום ושאילתות", details="הצביעו נמנע", evidence="הצביעו נמנע")]},
    )

    apply_event_grouping(artifacts=[artifact], extracted=subjects, quality_rows=[quality])

    assert quality.row_role == "vote_metadata"
    assert quality.artifact_role == "vote_metadata"
    assert not is_countable_subject_event(subjects[0])


def test_event_grouping_excludes_participant_registration_from_subject_tree() -> None:
    artifact = _artifact_dataclass(
        artifact_id="artifact-participants",
        real_text="יוסי עטר- מנכ\"ל; סימונה מורלי- מנכ\"ל; תמיר בראנץ- מבקר העירייה; מירב ביטון- מנהלת מחלקה",
        topic_label_he="סדר יום ושאילתות",
    )
    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="רישום", child="רישום משתתפים", object_text="סדר יום ושאילתות", details="רשימת משתתפים", evidence="יוסי עטר")]},
    )

    apply_event_grouping(artifacts=[artifact], extracted=subjects, quality_rows=[quality])

    assert quality.row_role == "context_detail"
    assert quality.artifact_role == "background_context"
    assert not is_countable_subject_event(subjects[0])


def test_generic_action_normalizes_to_municipal_directive_action() -> None:
    artifact = _artifact_dataclass(
        artifact_id="artifact-execution-action",
        real_text="לפעול מידית בבעיית התפוררות המרצפות, לשלוח מהנדס ולטפל בבעיית נפילת חלקי הבטון",
        topic_label_he="נכסים ומרכזים מסחריים",
    )
    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="פעולה", child="פעולה מיידית", object_text="נכסים ומרכזים מסחריים", details="שליחת מהנדס וטיפול במפגע", evidence="לפעול מידית")]},
    )

    apply_event_grouping(artifacts=[artifact], extracted=subjects, quality_rows=[quality])

    assert subjects[0].subject_payload["subject_root_label_he"] == "הנחיה"
    assert subjects[0].subject_payload["subject_child_label_he"] == "הנחיה לפעול"
    assert quality.row_role == "action_anchor"
    assert is_countable_subject_event(subjects[0])


def test_specific_exercise_without_dialogue_action_is_context_detail() -> None:
    artifact = _artifact_dataclass(
        artifact_id="artifact-exercise-action",
        real_text="התרגיל יתכלל אתר הרס כבד לרשות ותרגול אירועי פח\"ע בשיתוף משטרת אשדוד",
        topic_label_he="ביטחון ואכיפה",
    )
    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="תרגיל", child="תרגול אירועי פח\"ע", object_text="תרגיל משטרה ואכיפה", details="אתר הרס כבד ותרגול אירועי פחע", evidence="התרגיל יתכלל")]},
    )

    apply_event_grouping(artifacts=[artifact], extracted=subjects, quality_rows=[quality])

    assert subjects[0].subject_payload["subject_root_label_he"] == "תרגיל"
    assert quality.row_role == "context_detail"
    assert not is_countable_subject_event(subjects[0])


def test_coordination_meeting_formal_instruction_becomes_directive() -> None:
    artifact = _artifact_dataclass(
        artifact_id="artifact-coordination",
        real_text="יש לתאם מפגש בין הגב' רונית צור לנציגי המשפחות למציאת מענים",
        topic_label_he="רווחה ושירותים חברתיים",
    )
    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="תיאום", child="תיאום מפגש", object_text="מפגש עם נציגי המשפחות", details="לתאם מפגש למציאת מענים", evidence="לתאם מפגש")]},
    )

    apply_event_grouping(artifacts=[artifact], extracted=subjects, quality_rows=[quality])

    assert subjects[0].subject_payload["subject_root_label_he"] == "הנחיה"
    assert subjects[0].subject_payload["subject_child_label_he"] == "הנחיה לתיאום מפגש"
    assert quality.row_role == "action_anchor"
    assert is_countable_subject_event(subjects[0])


def test_event_grouping_links_split_question_rows_to_inquiry_anchor() -> None:
    inquiry_artifact = _artifact_dataclass(
        artifact_id="artifact-inquiry-anchor",
        real_text="לאור האמור אבקש לדעת",
        topic_label_he="סדר יום ושאילתות",
        source_ordinal=2,
    )
    question_artifact = _artifact_dataclass(
        artifact_id="artifact-inquiry-question",
        real_text="מי הגורם שמימן את חריגת התקציב בסך 71 מיליון שח? האם הגרעון מומן על ידי העירייה או משרד התחבורה?",
        topic_label_he="סדר יום ושאילתות",
        source_ordinal=3,
    )
    inquiry_subjects, inquiry_quality = process_topic_subject_payload(
        artifact=inquiry_artifact,
        model_payload={"subjects": [_subject_payload(root="בקשה", child="הגדרת הקשר", object_text="מקור מימון", details="אבקש לדעת", evidence="אבקש לדעת")]},
    )
    question_subjects, question_quality = process_topic_subject_payload(
        artifact=question_artifact,
        model_payload={"subjects": [_subject_payload(root="בקשה", child="בקשת מידע", object_text="מקור מימון", details="מי מימן והאם העירייה או משרד התחבורה", evidence="מי הגורם")]},
    )

    apply_event_grouping(
        artifacts=[inquiry_artifact, question_artifact],
        extracted=inquiry_subjects + question_subjects,
        quality_rows=[inquiry_quality, question_quality],
    )

    assert inquiry_quality.anchor_status == "validated_anchor"
    assert inquiry_quality.subject_root_by_dicta == "שאילתה"
    assert inquiry_quality.subject_child_by_dicta == ""
    assert question_quality.row_role == "dependent_detail"
    assert question_quality.anchor_status == "linked_to_validated_anchor"


def test_inquiry_title_normalizes_to_inquiry_action_not_generic_request() -> None:
    artifact = _artifact_dataclass(
        artifact_id="artifact-inquiry-title",
        real_text="הנדון שאילתה– מקור לכיסוי גרעון בסך 71 מיליון לפרויקט התחבורה הציבורית באשדוד",
        topic_label_he="סדר יום ושאילתות",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="בקשה", child="בקשת מידע", object_text="מקור לכיסוי גרעון בסך 71 מיליון", details="שאילתה בנושא מקור המימון", evidence="שאילתה")]},
    )

    assert subjects[0].subject_payload["subject_root_label_he"] == "שאילתה"
    assert subjects[0].subject_payload["subject_child_label_he"] == ""
    assert subjects[0].subject_payload["subject_object_he"] == "מקור לכיסוי גרעון בסך 71 מיליון"
    apply_event_grouping(artifacts=[artifact], extracted=subjects, quality_rows=[quality])
    assert quality.row_role == "action_anchor"


def test_response_to_inquiry_normalizes_action_and_repairs_subject_matter_from_context() -> None:
    response_artifact = _artifact_dataclass(
        artifact_id="artifact-inquiry-response",
        real_text="71 מלשח: במענה לשאילתא של גב' הלן גלבר להלן התייחסותי ראשית אבקש לציין כי אין מדובר בגרעון אלא בדיוק להפך. משרד התחבורה והאוצר הסכימו לתוספת תקציבית בסך 71 מלשח.",
        topic_label_he="תחבורה ובטיחות",
        source_ordinal=3,
    )
    response_artifact.neighbor_contexts = [
        {
            "relation": "previous_protocol_row",
            "artifact_id": "artifact-inquiry-title",
            "raw_text": "לכבוד ראש העיר הנדון שאילתא– מקור לכיסוי גרעון בסך 71 מיליון שח לפרויקט התחבורה הציבורית באשדוד",
            "page_span": {"start": 1, "end": 1},
            "header_path": [],
        }
    ]

    subjects, quality = process_topic_subject_payload(
        artifact=response_artifact,
        model_payload={"subjects": [_subject_payload(root="בקשה", child="בקשת מידע", object_text="תחבורה ובטיחות", details="מענה בנושא תוספת תקציבית", evidence="במענה לשאילתא")]},
    )

    assert subjects[0].subject_payload["subject_root_label_he"] == "מענה לשאילתה"
    assert subjects[0].subject_payload["subject_child_label_he"] == ""
    assert subjects[0].subject_payload["subject_object_he"] == "מקור לכיסוי גרעון בסך 71 מיליון שח לפרויקט התחבורה הציבורית באשדוד"
    assert subjects[0].subject_payload["action_root_label_he"] == "מענה לשאילתה"
    assert subjects[0].subject_payload["action_child_label_he"] == ""
    assert subjects[0].subject_payload["subject_matter_he"] == "מקור לכיסוי גרעון בסך 71 מיליון שח לפרויקט התחבורה הציבורית באשדוד"
    assert quality.subject_root_by_dicta == "מענה לשאילתה"
    assert quality.subject_object_by_dicta == "מקור לכיסוי גרעון בסך 71 מיליון שח לפרויקט התחבורה הציבורית באשדוד"
    assert quality.action_root_by_dicta == "מענה לשאילתה"
    assert quality.subject_matter_by_dicta == "מקור לכיסוי גרעון בסך 71 מיליון שח לפרויקט התחבורה הציבורית באשדוד"
    apply_event_grouping(artifacts=[response_artifact], extracted=subjects, quality_rows=[quality])
    assert quality.row_role == "action_anchor"


def test_ocr_spaced_response_to_inquiry_stays_response_action() -> None:
    artifact = _artifact_dataclass(
        artifact_id="artifact-ocr-spaced-inquiry-response",
        real_text="הנדון ניקיון העיר במענה לשאילת א של מר מאיר אברז'ל להלן התייחסות: הפקחים העירוניים הונחו לבצע פיקוח קפדני.",
        topic_label_he="מיחזור ותברואה",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                {
                    "action_root_label_he": "מענה לשאילתה",
                    "action_child_label_he": "",
                    "subject_matter_he": "ניקיון העיר וטיפול בגללי כלבים",
                    "action_details_he": "הפקחים העירוניים הונחו לבצע פיקוח קפדני",
                    "action_type_evidence_he": "במענה לשאילת א",
                    "what_text_is_about_he": "מענה לשאילתה בנושא ניקיון העיר.",
                    "is_decision": False,
                    "decision": None,
                    "confidence": 0.9,
                    "rationale_he": "test",
                }
            ]
        },
    )

    assert subjects[0].subject_payload["action_root_label_he"] == "מענה לשאילתה"
    assert subjects[0].subject_payload["subject_root_label_he"] == "מענה לשאילתה"
    assert quality.action_root_by_dicta == "מענה לשאילתה"


def test_v2_action_subject_matter_fields_populate_legacy_compatibility_fields() -> None:
    artifact = _artifact_dataclass(
        real_text="במענה לשאילתה בנושא תקצוב פרויקט התחבורה הציבורית, להלן התייחסותי.",
        topic_label_he="תחבורה ובטיחות",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                {
                    "action_root_label_he": "מענה לשאילתה",
                    "action_child_label_he": "",
                    "subject_matter_he": "תקצוב פרויקט התחבורה הציבורית",
                    "action_details_he": "התייחסות לשאילתה בנושא התקצוב",
                    "action_type_evidence_he": "במענה לשאילתה",
                    "action_summary_he": "מענה לשאילתה בנושא תקצוב פרויקט התחבורה הציבורית",
                    "what_text_is_about_he": "הטקסט הוא מענה לשאילתה בנושא תקצוב הפרויקט.",
                    "is_decision": False,
                    "decision": None,
                    "confidence": 0.9,
                    "rationale_he": "test",
                }
            ]
        },
    )

    payload = subjects[0].subject_payload
    assert payload["action_root_label_he"] == "מענה לשאילתה"
    assert payload["subject_matter_he"] == "תקצוב פרויקט התחבורה הציבורית"
    assert payload["subject_root_label_he"] == "מענה לשאילתה"
    assert payload["subject_object_he"] == "תקצוב פרויקט התחבורה הציבורית"
    assert quality.action_root_by_dicta == "מענה לשאילתה"
    assert quality.subject_matter_by_dicta == "תקצוב פרויקט התחבורה הציבורית"
    assert quality.subject_root_by_dicta == "מענה לשאילתה"
    assert quality.subject_object_by_dicta == "תקצוב פרויקט התחבורה הציבורית"


def test_approval_request_stays_approval_request_not_information_request() -> None:
    artifact = _artifact_dataclass(
        real_text="עלויות השתתפות בסמינר והוצאות הנסיעה על חשבון העירייה. אודה לאישור מועצת העיר לנסיעה הנל.",
        topic_label_he="חינוך",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="בקשה",
                    child="בקשת אישור",
                    object_text="נסיעה לסמינר",
                    details="אישור נסיעה ועלויות",
                    evidence="אודה לאישור מועצת העיר",
                )
            ]
        },
    )

    assert subjects[0].subject_payload["subject_child_label_he"] == "בקשת אישור"
    assert quality.subject_child_by_dicta == "בקשת אישור"


def test_generic_approval_decision_uses_specific_decision_root() -> None:
    artifact = _artifact_dataclass(
        real_text="חברי המועצה מאשרים פה אחד את הנסיעה המקצועית ואת מימון העלויות.",
        topic_label_he="חינוך",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="אישור", child="אישור נסיעה מקצועית", object_text="נסיעה מקצועית", details="אישור הנסיעה", evidence="מאשרים פה אחד", is_decision=True)]},
    )

    assert subjects[0].subject_payload["subject_root_label_he"] == "אישור החלטה"
    assert subjects[0].subject_payload["subject_child_label_he"] == ""
    assert quality.subject_root_by_dicta == "אישור החלטה"


def test_protocol_approval_uses_specific_protocol_root() -> None:
    artifact = _artifact_dataclass(
        real_text="חברי המועצה מאשרים פה אחד את הפרוטוקול 156023 05/03/2025",
        topic_label_he="הקצאות ושימושים",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="אישור", child="אישור פרוטוקול", object_text="פרוטוקול 156023", details="אישור הפרוטוקול", evidence="מאשרים פה אחד את הפרוטוקול", is_decision=True)]},
    )

    assert subjects[0].subject_payload["subject_root_label_he"] == "אישור פרוטוקול"
    assert subjects[0].subject_payload["subject_child_label_he"] == ""
    assert quality.subject_root_by_dicta == "אישור פרוטוקול"


def test_attachment_title_with_matzal_is_non_subject() -> None:
    payload = preclassify_trivial_subject_payload(
        _artifact_dataclass(
            real_text="15 . הודעה בדבר הפעלת שירותי שמירהוגביית היטל שמירה ( )עודד לוי- מצ\"ל",
            topic_label_he="שירותי שמירה והיטל שמירה",
        )
    )

    assert payload is not None
    assert payload["subjects"] == []
    assert payload["artifact_role"] == "attachment_reference"


def test_notification_root_normalizes_to_report_taxonomy() -> None:
    artifact = _artifact_dataclass(
        real_text="הודעה על הפעלת שירותי שמירה וגביית היטל שמירה",
        topic_label_he="שירותי שמירה והיטל שמירה",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="הודעה", child="הודעת הפעלה", object_text="שירותי שמירה", details="הפעלת שירותים וגביית היטל", evidence="הודעה על הפעלת שירותי שמירה")]},
    )

    assert subjects[0].subject_payload["subject_root_label_he"] == "דיווח"
    assert subjects[0].subject_payload["subject_child_label_he"] == ""
    assert quality.subject_root_by_dicta == "דיווח"


def test_unsupported_subject_type_evidence_is_repaired_from_raw_action_cue() -> None:
    artifact = _artifact_dataclass(
        real_text="החלטות הודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה חברי המועצה מאשרים ברוב קולות את .ההודעה",
        topic_label_he="שירותי שמירה והיטל שמירה",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="אישור",
                    child="",
                    object_text="שירותי שמירה והיטל שמירה",
                    details="אישור הודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה",
                    evidence="המועצה מאשרת את ההודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה",
                    is_decision=True,
                    decision={
                        "decision_label_he": "אישור",
                        "decision_summary_he": "אישור הודעה בדבר הפעלת שירותי שמירה",
                        "source_quote_he": "חברי המועצה מאשרים ברוב קולות את .ההודעה",
                        "confidence": 0.9,
                        "limitations": [],
                    },
                )
            ]
        },
    )

    apply_event_grouping(artifacts=[artifact], extracted=subjects, quality_rows=[quality])

    assert subjects[0].subject_payload["subject_type_evidence_he"] == "מאשרים"
    assert quality.anchor_status == "validated_anchor"
    assert quality.status == "decision_candidate"


def test_event_grouping_links_weak_title_action_anchor_to_following_approval_anchor() -> None:
    title_artifact = _artifact_dataclass(
        artifact_id="artifact-service-watch-title",
        real_text="סעיף26 : . הודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה",
        topic_label_he="שירותי שמירה והיטל שמירה",
        source_ordinal=1,
    )
    approval_artifact = _artifact_dataclass(
        artifact_id="artifact-service-watch-approval",
        real_text="15 )(עודד לוי החלטות הודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה חברי המועצה מאשרים ברוב קולות את .ההודעה הצביעו בעד: 19 חברים",
        topic_label_he="שירותי שמירה והיטל שמירה",
        source_ordinal=2,
    )
    title_subjects, title_quality = process_topic_subject_payload(
        artifact=title_artifact,
        model_payload={"subjects": [_subject_payload(root="הודעה", child="הודעת הפעלה", object_text="שירותי שמירה והיטל שמירה", details="כותרת הודעה", evidence="הודעה בדבר")]},
    )
    approval_subjects, approval_quality = process_topic_subject_payload(
        artifact=approval_artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="אישור",
                    child="",
                    object_text="שירותי שמירה והיטל שמירה",
                    details="אישור ההודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה",
                    evidence="מאשרים ברוב קולות",
                    is_decision=True,
                    decision={
                        "decision_label_he": "אישור",
                        "decision_summary_he": "אישור הודעה בדבר הפעלת שירותי שמירה",
                        "source_quote_he": "חברי המועצה מאשרים ברוב קולות את .ההודעה",
                        "confidence": 0.9,
                        "limitations": [],
                    },
                )
            ]
        },
    )

    apply_event_grouping(
        artifacts=[title_artifact, approval_artifact],
        extracted=title_subjects + approval_subjects,
        quality_rows=[title_quality, approval_quality],
    )

    assert approval_quality.row_role == "action_anchor"
    assert approval_quality.anchor_status == "validated_anchor"
    assert title_quality.row_role == "dependent_detail"
    assert title_quality.anchor_status == "linked_to_validated_anchor"
    assert title_quality.linked_event_id == approval_quality.event_id
    assert not is_countable_subject_event(title_subjects[0])


def test_event_grouping_links_suspect_title_fragment_to_following_approval_anchor() -> None:
    title_artifact = _artifact_dataclass(
        artifact_id="artifact-suspect-approval-title",
        real_text="סעיף26 : . הודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה",
        topic_label_he="שירותי שמירה והיטל שמירה",
        source_ordinal=1,
    )
    approval_artifact = _artifact_dataclass(
        artifact_id="artifact-grounded-approval",
        real_text="החלטות הודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה חברי המועצה מאשרים ברוב קולות את .ההודעה",
        topic_label_he="שירותי שמירה והיטל שמירה",
        source_ordinal=2,
    )
    title_subjects, title_quality = process_topic_subject_payload(
        artifact=title_artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="אישור",
                    child="",
                    object_text="שירותי שמירה והיטל שמירה",
                    details="המועצה מאשרת את ההודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה",
                    evidence="המועצה מאשרת את ההודעה",
                )
            ]
        },
    )
    approval_subjects, approval_quality = process_topic_subject_payload(
        artifact=approval_artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="אישור",
                    child="",
                    object_text="שירותי שמירה והיטל שמירה",
                    details="אישור ההודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה",
                    evidence="מאשרים ברוב קולות",
                    is_decision=True,
                    decision={
                        "decision_label_he": "אישור",
                        "decision_summary_he": "אישור הודעה בדבר הפעלת שירותי שמירה",
                        "source_quote_he": "חברי המועצה מאשרים ברוב קולות את .ההודעה",
                        "confidence": 0.9,
                        "limitations": [],
                    },
                )
            ]
        },
    )

    apply_event_grouping(
        artifacts=[title_artifact, approval_artifact],
        extracted=title_subjects + approval_subjects,
        quality_rows=[title_quality, approval_quality],
    )

    assert approval_quality.row_role == "action_anchor"
    assert title_quality.row_role == "dependent_detail"
    assert title_quality.subject_root_by_dicta == "אישור החלטה"
    assert title_quality.linked_event_id == approval_quality.event_id


def test_ollama_subject_client_retries_once_after_timeout(monkeypatch) -> None:
    calls = {"count": 0}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"message": {"content": "{}"}}

    class FakeHttpClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise httpx.ReadTimeout("timed out")
            return FakeResponse()

    monkeypatch.setattr(topic_subjects_module.httpx, "Client", FakeHttpClient)

    payload, error = OllamaTopicSubjectClient()._post_chat_with_timeout_retry(body={}, config=TopicSubjectResearchConfig(timeout_seconds=1.0))

    assert error is None
    assert payload == {"message": {"content": "{}"}}
    assert calls["count"] == 2


def test_process_topic_subject_payload_keeps_agenda_proposal_as_specific_root() -> None:
    artifact = _artifact_dataclass(real_text="הנדון הצעה לסדר יום - הצפות חוזרות ברחבי העיר וטיפול בתשתיות", topic_label_he="תכנון ובנייה")

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                {
                    "subject_root_label_he": "בקשה",
                    "subject_child_label_he": "בקשת אישור",
                    "subject_object_he": "הצפות חוזרות בעיר",
                    "subject_details_he": "טיפול בתשתיות ופיצוי תושבים",
                    "subject_summary_he": "הצעה לסדר יום בנושא הצפות",
                    "what_text_is_about_he": "המסמך מציג הצעה לסדר יום בנושא הצפות חוזרות.",
                    "is_decision": False,
                    "decision": None,
                    "confidence": 0.8,
                    "rationale_he": "proposal",
                }
            ]
        },
    )

    assert len(subjects) == 1
    assert subjects[0].subject_payload["subject_root_label_he"] == "הצעה לסדר יום"
    assert subjects[0].subject_payload["subject_child_label_he"] == ""
    assert quality.subject_root_by_dicta == "הצעה לסדר יום"
    assert quality.subject_child_by_dicta == ""


def test_corrected_text_repairs_common_hebrew_ocr_spacing_and_report_shows_it() -> None:
    artifact = _artifact_dataclass(
        real_text="עלויות השתתפות בסמינר והוצא .ות הנסיעה על חשבון העירייה. אודה לאישור מועצת העיר לנסיעה הנל.",
        topic_label_he="חינוך",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="בקשה", child="בקשת אישור", object_text="נסיעה לסמינר", details="עלויות השתתפות ונסיעה", evidence="אודה לאישור מועצת העיר")]},
    )
    report = quality_report_markdown([quality])

    assert subjects
    assert "הוצאות" in corrected_hebrew_text(artifact.real_text)
    assert "Corrected Text" in report
    assert "הוצאות" in quality.corrected_text


def test_committee_recommendation_is_subject_not_decision_without_approval() -> None:
    artifact = _artifact_dataclass(
        real_text="הוועדה ממליצה לשנות את התבחין בתחום רשות הספורט ומצרפת את התבחין המעודכן",
        topic_label_he="תרבות וספורט",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="עדכון",
                    child="עדכון תבחין",
                    object_text="שינוי תבחין רשות הספורט",
                    details="הוועדה ממליצה לשנות את התבחין",
                    evidence="הוועדה ממליצה לשנות",
                    is_decision=True,
                    decision={
                        "decision_label_he": "אישור",
                        "decision_summary_he": "אישור שינוי תבחין",
                        "source_quote_he": "הוועדה ממליצה לשנות את התבחין",
                        "confidence": 0.8,
                        "limitations": [],
                    },
                )
            ]
        },
    )

    assert subjects[0].subject_payload["subject_root_label_he"] == "המלצה"
    assert subjects[0].subject_payload["subject_child_label_he"] == ""
    assert subjects[0].subject_payload["is_decision"] is False
    assert quality.my_judgment == "subject_candidate_with_rejected_decision"
    assert "recommendation_without_approval_outcome" in quality.reason_for_failure


def test_recommendation_wording_overrides_model_approval_label_without_approval_outcome() -> None:
    artifact = _artifact_dataclass(
        real_text="החלטה מספר1 שינוי תבחין תקציב רשות הספורט הוועדה ממליצה לשנות את התבחין בתחום רשות הספורט ומצרפת את התבחין המעודכן",
        topic_label_he="תרבות וספורט",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="אישור החלטה",
                    child="אישור החלטת ועדה",
                    object_text="שינוי תבחין תקציב רשות הספורט",
                    details="הוועדה ממליצה לשנות את התבחין",
                    evidence="הוועדה ממליצה לשנות את התבחין",
                    is_decision=True,
                    decision={
                        "decision_label_he": "אישור החלטה",
                        "decision_summary_he": "אישור שינוי תבחין",
                        "source_quote_he": "הוועדה ממליצה לשנות את התבחין",
                        "confidence": 0.9,
                        "limitations": [],
                    },
                )
            ]
        },
    )

    assert subjects[0].subject_payload["subject_root_label_he"] == "המלצה"
    assert subjects[0].subject_payload["is_decision"] is False
    assert quality.action_root_by_dicta == "המלצה"
    assert "recommendation_without_approval_outcome" in quality.reason_for_failure


def test_approval_decision_repairs_missing_exact_source_quote_from_raw_text() -> None:
    artifact = _artifact_dataclass(
        real_text="15 )(עודד לוי החלטות הודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה חברי המועצה מאשרים ברוב קולות את ההודעה הצביעו בעד 19 חברים",
        topic_label_he="שירותי שמירה והיטל שמירה",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="אישור החלטה",
                    child="",
                    object_text="שירותי שמירה והיטל שמירה",
                    details="חברי המועצה מאשרים ברוב קולות את ההודעה",
                    evidence="מאשרים ברוב קולות",
                    is_decision=True,
                    decision={
                        "decision_label_he": "",
                        "decision_summary_he": "",
                        "source_quote_he": "",
                        "confidence": 0.8,
                        "limitations": [],
                    },
                )
            ]
        },
    )

    decision = subjects[0].subject_payload["decision"]
    assert subjects[0].validation_status == "accepted"
    assert subjects[0].subject_payload["is_decision"] is True
    assert decision["decision_label_he"] == "אישור החלטה"
    assert "חברי המועצה מאשרים ברוב קולות את ההודעה" in decision["source_quote_he"]
    assert quality.status == "decision_candidate"


def test_approval_decision_repairs_exact_quote_that_omits_action_words() -> None:
    artifact = _artifact_dataclass(
        real_text="הסכמי רשות ופיתוח להקמה והפעלת מקווה טהרה חברי המועצה מאשרים פה אחד את ( ההסכם24 )חברים 156021 05/03/2025",
        topic_label_he="הסכמי רשות ופיתוח",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="אישור החלטה",
                    child="",
                    object_text="הסכמי רשות ופיתוח להקמה והפעלת מקווה טהרה",
                    details="חברי המועצה מאשרים פה אחד את ההסכם 24",
                    evidence="מאשרים פה אחד את",
                    is_decision=True,
                    decision={
                        "decision_label_he": "אישור החלטה",
                        "decision_summary_he": "אישור הסכם 24",
                        "source_quote_he": "( ההסכם24 )חברים 156021 05/03/2025",
                        "confidence": 0.9,
                        "limitations": [],
                    },
                )
            ]
        },
    )

    assert subjects[0].validation_status == "accepted"
    assert "חברי המועצה מאשרים פה אחד את" in subjects[0].subject_payload["decision"]["source_quote_he"]
    assert quality.status == "decision_candidate"


def test_agenda_proposal_removed_from_agenda_gets_procedural_outcome_action() -> None:
    artifact = _artifact_dataclass(
        real_text="סעיף11 מתן ייעוץ מתכלל 8/2024 ביטול מכרז פומבי - הצעה לסדר בנושא מדיניות ייעוץ בקשה של עו\"ד גלבר הלן ההצעה יורדת מסדר היום ברוב קולות בעד 17 חברים נגד 6 חברים",
        topic_label_he="מכרזים והתקשרויות",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="אישור החלטה",
                    child="",
                    object_text="ביטול מכרז פומבי 8/2024",
                    details="ההצעה יורדת מסדר היום ברוב קולות",
                    evidence="מאשרים / אושר / הוחלט לאשר",
                    is_decision=True,
                    decision={
                        "decision_label_he": "אישור החלטה",
                        "decision_summary_he": "אישור ביטול מכרז",
                        "source_quote_he": "מאשרים / אושר / הוחלט לאשר",
                        "confidence": 0.9,
                        "limitations": [],
                    },
                )
            ]
        },
    )

    assert subjects[0].subject_payload["subject_root_label_he"] == "הסרה מסדר היום"
    assert subjects[0].subject_payload["subject_object_he"] == "ביטול מכרז פומבי 8/2024"
    assert subjects[0].subject_payload["is_decision"] is True
    assert subjects[0].subject_payload["decision"]["decision_label_he"] == "הסרה מסדר היום"
    assert "יורדת מסדר היום" in subjects[0].subject_payload["decision"]["source_quote_he"]
    assert quality.status == "decision_candidate"


def test_committee_referral_keeps_concrete_subject_matter_not_source_topic() -> None:
    artifact = _artifact_dataclass(
        real_text=". הצעה2.4 קריאת - לסדר רחוב על שמו של זאב רווח ז\"ל בקשתו של מר עמירם בן זקן ההצעה עוברת לדיון בוועדת שמות 156046 05/03/2025",
        topic_label_he="שמות והנצחה",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="בקשה",
                    child="",
                    object_text="שמות והנצחה",
                    details="בקשת מר עמירם בן זקן לקרוא רחוב על שמו של זאב רווח",
                    evidence="ההצעה עוברת לדיון בוועדת שמות",
                )
            ]
        },
    )

    assert subjects[0].subject_payload["subject_root_label_he"] == "הפניה לוועדה"
    assert subjects[0].subject_payload["subject_child_label_he"] == ""
    assert subjects[0].subject_payload["subject_object_he"] == "קריאת רחוב על שמו של זאב רווח ז\"ל"
    assert quality.action_root_by_dicta == "הפניה לוועדה"
    assert quality.subject_matter_by_dicta == "קריאת רחוב על שמו של זאב רווח ז\"ל"


def test_agenda_proposal_replaces_broad_source_topic_with_heading_subject_matter() -> None:
    artifact = _artifact_dataclass(
        real_text="בסד לכבוד ראש העיר הנדון הצעה לסדר יום- הצפות חוזרות ונשנות ברחבי העיר- טיפול בתשתיות ופיצוי התושבים בעקבות נזקי ההצפות",
        topic_label_he="תכנון ובנייה",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="הצעה לסדר יום", child="", object_text="תכנון ובנייה", details="הצעה בנושא תכנון ובנייה", evidence="הצעה לסדר יום")]},
    )

    assert subjects[0].subject_payload["subject_root_label_he"] == "הצעה לסדר יום"
    assert subjects[0].subject_payload["subject_object_he"] == "הצפות חוזרות ונשנות ברחבי העיר- טיפול בתשתיות ופיצוי התושבים"
    assert quality.subject_matter_by_dicta == "הצפות חוזרות ונשנות ברחבי העיר- טיפול בתשתיות ופיצוי התושבים"


def test_inquiry_title_replaces_broad_source_topic_with_inquiry_subject() -> None:
    artifact = _artifact_dataclass(
        real_text="סעיף7 : . שאילתה בנושא מתחם ההחלקה על הקרח בקניון בלו אייס ארנה, בקשתה של הגברת סופה לנדבר הוקראה תשובת ראש העיר",
        topic_label_he="מרכזים מסחריים",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="שאילתה", child="", object_text="מרכזים מסחריים", details="שאילתה בנושא מרכזים", evidence="שאילתה בנושא")]},
    )

    assert subjects[0].subject_payload["subject_root_label_he"] == "מענה לשאילתה"
    assert subjects[0].subject_payload["subject_object_he"] == "מתחם ההחלקה על הקרח בקניון בלו אייס ארנה"
    assert quality.action_root_by_dicta == "מענה לשאילתה"
    assert quality.subject_matter_by_dicta == "מתחם ההחלקה על הקרח בקניון בלו אייס ארנה"


def test_committee_decision_approval_uses_specific_approval_root() -> None:
    artifact = _artifact_dataclass(
        real_text="וועדת משנה לתמיכות מאשרת את החלטת ועדה מקצועית 9 החלטה 1",
        topic_label_he="תמיכות",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={
            "subjects": [
                _subject_payload(
                    root="אישור",
                    child="אישור החלטה",
                    object_text="החלטת ועדה מקצועית 9 החלטה 1",
                    details="וועדת משנה לתמיכות מאשרת את החלטת הוועדה המקצועית",
                    evidence="מאשרת את החלטת ועדה מקצועית",
                    is_decision=True,
                    decision={
                        "decision_label_he": "אישור החלטה",
                        "decision_summary_he": "אישור החלטת ועדה מקצועית",
                        "source_quote_he": "וועדת משנה לתמיכות מאשרת את החלטת ועדה מקצועית 9 החלטה 1",
                        "confidence": 0.9,
                        "limitations": [],
                    },
                )
            ]
        },
    )

    apply_event_grouping(artifacts=[artifact], extracted=subjects, quality_rows=[quality])

    assert subjects[0].subject_payload["subject_root_label_he"] == "אישור החלטה"
    assert subjects[0].subject_payload["subject_child_label_he"] == "אישור החלטת ועדה"
    assert quality.row_role == "action_anchor"
    assert is_countable_subject_event(subjects[0])


def test_event_grouping_links_numbered_proposal_action_to_agenda_proposal() -> None:
    proposal_artifact = _artifact_dataclass(
        artifact_id="artifact-flood-proposal",
        real_text="הנדון הצעה לסדר יום - הצפות חוזרות ברחבי העיר וטיפול בתשתיות ופיצוי התושבים",
        topic_label_he="תכנון ובנייה",
        source_ordinal=1,
    )
    detail_artifact = _artifact_dataclass(
        artifact_id="artifact-flood-detail",
        real_text="1. לפעול בהתאם לתוכנית אב שהוגשה ולהחליף צינורות באזורים המועדים להצפות",
        topic_label_he="תכנון ובנייה",
        source_ordinal=2,
    )
    proposal_subjects, proposal_quality = process_topic_subject_payload(
        artifact=proposal_artifact,
        model_payload={"subjects": [_subject_payload(root="בקשה", child="בקשת אישור", object_text="הצפות חוזרות ברחבי העיר", details="טיפול בתשתיות ופיצוי תושבים", evidence="הצעה לסדר יום")]},
    )
    detail_subjects, detail_quality = process_topic_subject_payload(
        artifact=detail_artifact,
        model_payload={"subjects": [_subject_payload(root="פעולה", child="פעולה מיידית", object_text="טיפול בתשתיות הצפה", details="החלפת צינורות", evidence="לפעול בהתאם")]},
    )

    apply_event_grouping(
        artifacts=[proposal_artifact, detail_artifact],
        extracted=proposal_subjects + detail_subjects,
        quality_rows=[proposal_quality, detail_quality],
    )

    assert proposal_quality.subject_root_by_dicta == "הצעה לסדר יום"
    assert proposal_quality.anchor_status == "validated_anchor"
    assert detail_quality.row_role == "dependent_detail"
    assert detail_quality.subject_root_by_dicta == "הצעה לסדר יום"
    assert not is_countable_subject_event(detail_subjects[0])


def test_agenda_proposal_ocr_variant_becomes_specific_root() -> None:
    artifact = _artifact_dataclass(
        real_text="הנדון הצעה סדר יום - שיפוץ מרכז מסחרי רובע ט. לאור האמור אבקש לעלות את הנושא לסדר יום הישיבה הקרובה",
        topic_label_he="נכסים ומרכזים מסחריים",
    )

    subjects, quality = process_topic_subject_payload(
        artifact=artifact,
        model_payload={"subjects": [_subject_payload(root="בקשה", child="", object_text="שיפוץ מרכז מסחרי", details="העלאת הנושא לסדר היום", evidence="אבקש לעלות את הנושא לסדר יום")]},
    )

    assert subjects[0].subject_payload["subject_root_label_he"] == "הצעה לסדר יום"
    assert subjects[0].subject_payload["subject_child_label_he"] == ""
    assert quality.subject_root_by_dicta == "הצעה לסדר יום"


def test_model_empty_background_row_is_context_detail_not_failed() -> None:
    artifact = _artifact_dataclass(
        real_text="מלגת עיריית אשדוד בשיתוף פרח ומפעל הפיס מורכבת מ-120 שעות פעילות ותמורת 10,000 שח",
        topic_label_he="תמיכות",
    )

    subjects, quality = process_topic_subject_payload(artifact=artifact, model_payload={"subjects": [], "overall_summary_he": "פרטי רקע על מלגה"})
    apply_event_grouping(artifacts=[artifact], extracted=subjects, quality_rows=[quality])

    assert subjects == []
    assert quality.status == "context_detail"
    assert quality.row_role == "context_detail"
    assert quality.reason_for_failure == ""


def test_clear_agenda_request_title_is_countable_request_subject() -> None:
    artifact = _artifact_dataclass(
        artifact_id="artifact-agenda-request-title",
        real_text="2 . יד ביד לאורך כל הדרך- מענה תומך במשפחות אלמנים ואלמנות וילדיהם באשדוד, בקשתם של מר מאיר אברז'ל ומר יניב קקון",
        topic_label_he="סיוע למשפחות",
    )
    payload = preclassify_trivial_subject_payload(artifact)

    assert payload is not None
    assert payload.get("agenda_title_subject") is True

    subjects, quality = process_topic_subject_payload(artifact=artifact, model_payload=payload)
    apply_event_grouping(artifacts=[artifact], extracted=subjects, quality_rows=[quality])

    assert subjects[0].subject_payload["subject_root_label_he"] == "בקשה"
    assert subjects[0].subject_payload["subject_child_label_he"] == "בקשת דיון"
    assert quality.row_role == "action_anchor"
    assert quality.anchor_status == "validated_anchor"
    assert is_countable_subject_event(subjects[0])


def test_clear_agenda_proposal_title_is_countable_proposal_subject() -> None:
    artifact = _artifact_dataclass(
        artifact_id="artifact-agenda-proposal-title",
        real_text="הנדון הצעה לסדר יום - הצפות חוזרות ברחבי העיר וטיפול בתשתיות",
        topic_label_he="תכנון ובנייה",
    )
    payload = preclassify_trivial_subject_payload(artifact)

    assert payload is not None
    assert payload.get("agenda_title_subject") is True

    subjects, quality = process_topic_subject_payload(artifact=artifact, model_payload=payload)
    apply_event_grouping(artifacts=[artifact], extracted=subjects, quality_rows=[quality])

    assert subjects[0].subject_payload["subject_root_label_he"] == "הצעה לסדר יום"
    assert subjects[0].subject_payload["subject_child_label_he"] == ""
    assert quality.row_role == "action_anchor"
    assert is_countable_subject_event(subjects[0])


def test_event_block_first_calls_model_once_for_title_plus_approval_block() -> None:
    title_artifact = _artifact_dataclass(
        artifact_id="artifact-block-title",
        real_text="סעיף26 : . הודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה",
        topic_label_he="שירותי שמירה והיטל שמירה",
        source_ordinal=1,
    )
    approval_artifact = _artifact_dataclass(
        artifact_id="artifact-block-approval",
        real_text="חברי המועצה מאשרים ברוב קולות את ההודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה",
        topic_label_he="שירותי שמירה והיטל שמירה",
        source_ordinal=2,
    )

    class RecordingBlockClient:
        def __init__(self) -> None:
            self.calls = []

        def extract(self, *, artifact: TopicDecisionArtifact, config: TopicSubjectResearchConfig) -> dict:
            self.calls.append(artifact)
            assert artifact.artifact_id == approval_artifact.artifact_id
            assert len(artifact.metadata["event_block_rows"]) == 2
            return {
                "subjects": [
                    _subject_payload(
                        root="אישור",
                        child="",
                        object_text="שירותי שמירה והיטל שמירה",
                        details="אישור ההודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה",
                        evidence="מאשרים ברוב קולות",
                        is_decision=True,
                        decision={
                            "decision_label_he": "אישור",
                            "decision_summary_he": "אישור הודעה בדבר הפעלת שירותי שמירה",
                            "source_quote_he": "חברי המועצה מאשרים ברוב קולות את ההודעה",
                            "confidence": 0.9,
                            "limitations": [],
                        },
                    )
                ]
            }

    blocks = build_topic_subject_event_blocks([title_artifact, approval_artifact])
    client = RecordingBlockClient()

    extracted, quality_rows = extract_subjects_from_event_blocks(event_blocks=blocks, client=client, config=TopicSubjectResearchConfig())
    apply_event_grouping(artifacts=[title_artifact, approval_artifact], extracted=extracted, quality_rows=quality_rows)
    apply_event_block_links(event_blocks=blocks, extracted=extracted, quality_rows=quality_rows)

    quality_by_id = {row.artifact_id: row for row in quality_rows}
    assert len(client.calls) == 1
    assert blocks[0].kind == "model_block"
    assert blocks[0].anchor_artifact == approval_artifact
    assert quality_by_id[approval_artifact.artifact_id].row_role == "action_anchor"
    assert quality_by_id[title_artifact.artifact_id].row_role == "dependent_detail"
    assert quality_by_id[title_artifact.artifact_id].linked_event_id == quality_by_id[approval_artifact.artifact_id].event_id


def test_event_block_builder_does_not_attach_next_section_title_to_previous_anchor() -> None:
    previous_approval = _artifact_dataclass(
        artifact_id="artifact-previous-approval",
        real_text="חברי המועצה מאשרים פה אחד את ההסכם",
        topic_label_he="הסכמים והתקשרויות",
        source_ordinal=1,
    )
    next_title = _artifact_dataclass(
        artifact_id="artifact-next-section-title",
        real_text="סעיף26 : . הודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה",
        topic_label_he="שירותי שמירה והיטל שמירה",
        source_ordinal=2,
    )
    next_approval = _artifact_dataclass(
        artifact_id="artifact-next-approval",
        real_text="חברי המועצה מאשרים ברוב קולות את ההודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה",
        topic_label_he="שירותי שמירה והיטל שמירה",
        source_ordinal=3,
    )

    blocks = build_topic_subject_event_blocks([previous_approval, next_title, next_approval])

    assert blocks[0].anchor_artifact == previous_approval
    assert [artifact.artifact_id for artifact in blocks[0].artifacts] == [previous_approval.artifact_id]
    assert blocks[1].anchor_artifact == next_approval
    assert [artifact.artifact_id for artifact in blocks[1].artifacts] == [next_title.artifact_id, next_approval.artifact_id]


def test_event_block_builder_treats_generic_section_title_as_forward_boundary() -> None:
    previous_approval = _artifact_dataclass(
        artifact_id="artifact-previous-committee-approval",
        real_text="חברי המועצה מאשרים פה אחד את ההחלטה",
        topic_label_he="חילופי גברי",
        source_ordinal=1,
    )
    next_title = _artifact_dataclass(
        artifact_id="artifact-next-generic-section-title",
        real_text="סעיף28 : . שכרו ומועד תחילת עבודתו של מנהל אגף רכש ולוגיסטיקה",
        topic_label_he="מינוי עובדים בכירים",
        source_ordinal=2,
    )
    next_approval = _artifact_dataclass(
        artifact_id="artifact-next-manager-approval",
        real_text="חברי המועצה מאשרים פה אחד את שכרו, מועד תחילת עבודתו ואישור מינויו של מנהל אגף רכש ולוגיסטיקה",
        topic_label_he="מינוי עובדים בכירים",
        source_ordinal=3,
    )

    blocks = build_topic_subject_event_blocks([previous_approval, next_title, next_approval])

    assert blocks[0].anchor_artifact == previous_approval
    assert [artifact.artifact_id for artifact in blocks[0].artifacts] == [previous_approval.artifact_id]
    assert blocks[1].anchor_artifact == next_approval
    assert [artifact.artifact_id for artifact in blocks[1].artifacts] == [next_title.artifact_id, next_approval.artifact_id]


def test_event_block_builder_attaches_split_inquiry_question_rows() -> None:
    inquiry_anchor = _artifact_dataclass(
        artifact_id="artifact-inquiry-intro",
        real_text="2022 כי נתניה תבצע את המודל לאור האמור, אבקש לדעת",
        topic_label_he="סדר יום ושאילתות",
        source_ordinal=1,
    )
    first_question = _artifact_dataclass(
        artifact_id="artifact-inquiry-question-one",
        real_text="1. מי הגורם שמימן את חריגת התקציב בסך 71 מיליון שח? האם הגרעון מומן על ידי העירייה או משרד התחבורה?",
        topic_label_he="סדר יום ושאילתות",
        source_ordinal=2,
    )
    second_question = _artifact_dataclass(
        artifact_id="artifact-inquiry-question-two",
        real_text="2. אם התשובה חיובית ביחס למימון הגרעון על ידי משרד התחבורה- מתי הושלם התקציב? ומה הסכום שהועבר?",
        topic_label_he="תחבורה ציבורית",
        source_ordinal=3,
    )

    blocks = build_topic_subject_event_blocks([inquiry_anchor, first_question, second_question])

    assert len(blocks) == 1
    assert blocks[0].anchor_artifact == inquiry_anchor
    assert [artifact.artifact_id for artifact in blocks[0].artifacts] == [
        inquiry_anchor.artifact_id,
        first_question.artifact_id,
        second_question.artifact_id,
    ]


def test_event_block_builder_attaches_condition_scope_with_incidental_approval_words() -> None:
    request_anchor = _artifact_dataclass(
        artifact_id="artifact-delegation-request-anchor",
        real_text="אבקש את אישור מועצת העיר להאצלת סמכויות חתימה לגב לינור כהן בהתאם למפורט",
        topic_label_he="האצלת סמכויות חתימה",
        source_ordinal=1,
    )
    condition_detail = _artifact_dataclass(
        artifact_id="artifact-condition-scope-detail",
        real_text="בהזמנות הנגזרות: מחוזה חתום כדין הנגזר ממכרז כדין או במסלול פטור ממכרז מאושר בידי היועמש, החלטות של ועדת רכש, הצעות מחיר מאושרות כדין, הקצבות ותמיכות, הסכום ללא הגבלה",
        topic_label_he="הסכמים והתקשרויות",
        source_ordinal=2,
    )

    blocks = build_topic_subject_event_blocks([request_anchor, condition_detail])

    assert len(blocks) == 1
    assert blocks[0].anchor_artifact == request_anchor
    assert [artifact.artifact_id for artifact in blocks[0].artifacts] == [request_anchor.artifact_id, condition_detail.artifact_id]


def test_event_block_builder_attaches_numbered_proposal_action_bullets() -> None:
    proposal_anchor = _artifact_dataclass(
        artifact_id="artifact-long-proposal-anchor",
        real_text=(
            "לכבוד ראש העיר הנדון הצעה לסדר יום בנושא הצפות חוזרות ברחבי העיר. "
            "בעקבות נזקי ההצפות בחודשי החורף ולאחר פניות רבות של תושבים, "
            "אבקש להביא את ההצעה לסדר יום כדי לדון בטיפול בתשתיות ובפיצוי תושבים שנפגעו. "
            "המסמך מפרט רקע רחב על אירועי הצפה קודמים ועל הצורך בדיון ציבורי מסודר במועצת העיר."
        ),
        topic_label_he="תכנון ובנייה",
        source_ordinal=1,
    )
    action_bullet = _artifact_dataclass(
        artifact_id="artifact-proposal-action-bullet",
        real_text="1. לפעול בהתאם לתוכנית אב שהוגשה ולהחליף צינורות באזורים המועדים להצפות",
        topic_label_he="תכנון ובנייה",
        source_ordinal=2,
    )

    blocks = build_topic_subject_event_blocks([proposal_anchor, action_bullet])

    assert len(blocks) == 1
    assert blocks[0].anchor_artifact == proposal_anchor
    assert [artifact.artifact_id for artifact in blocks[0].artifacts] == [proposal_anchor.artifact_id, action_bullet.artifact_id]


def test_event_block_empty_model_output_falls_back_for_exact_protocol_approval() -> None:
    approval_artifact = _artifact_dataclass(
        artifact_id="artifact-empty-model-protocol-approval",
        real_text="חברי המועצה מאשרים פה אחד את הפרוטוקול 156025 05/03/2025",
        topic_label_he="סדר יום ושאילתות",
    )

    class EmptyBlockClient:
        def extract(self, *, artifact: TopicDecisionArtifact, config: TopicSubjectResearchConfig) -> dict:
            return {"subjects": [], "overall_summary_he": "המודל לא החזיר נושא"}

    blocks = build_topic_subject_event_blocks([approval_artifact])
    extracted, quality_rows = extract_subjects_from_event_blocks(event_blocks=blocks, client=EmptyBlockClient(), config=TopicSubjectResearchConfig())
    apply_event_grouping(artifacts=[approval_artifact], extracted=extracted, quality_rows=quality_rows)

    assert len(extracted) == 1
    assert extracted[0].subject_payload["subject_root_label_he"] == "אישור פרוטוקול"
    assert quality_rows[0].row_role == "action_anchor"
    assert quality_rows[0].status == "decision_candidate"
    assert is_countable_subject_event(extracted[0])


def test_topic_subject_v3_normalization_payload_hides_source_topic() -> None:
    artifact = _artifact_dataclass(
        real_text="אבקש לאשר נסיעה מקצועית לכנס ארצי בנושא שירות לתושב.",
        topic_label_he="חינוך",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    payload = topic_subject_v3_normalization_payload(context=context, max_text_chars=1000)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "known_topic" not in payload
    assert "topic_label_he" not in serialized
    assert "חינוך" not in serialized
    assert payload["source_context"]["target_row"]["artifact_id"] == artifact.artifact_id


def test_topic_subject_v3_normalization_payload_keeps_concrete_desired_action_event_candidate() -> None:
    artifact = _artifact_dataclass(
        real_text="התקיים דיון על תקציב שירות עירוני. בסיכום נאמר שהמשתתפת רוצה להגדיל את התקציב.",
        topic_label_he="תקציב שירות עירוני",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    payload = topic_subject_v3_normalization_payload(context=context, max_text_chars=1000)

    assert any("concrete desired municipal change/action" in requirement for requirement in payload["requirements"])
    assert any("formal motion" in requirement for requirement in payload["requirements"])
    assert any("Mere opinion" in requirement for requirement in payload["requirements"])


def test_topic_subject_v3_extraction_payload_uses_controlled_ontology_and_threshold() -> None:
    artifact = _artifact_dataclass(real_text="במענה לשאילתה בנושא הצללה בגני משחקים נמסר כי בוצע סקר.", topic_label_he="גני ילדים")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    config = TopicSubjectResearchConfig(action_confidence_threshold=0.75)

    payload = topic_subject_v3_extraction_payload(
        context=context,
        normalized_event={"is_event": True, "matter_candidate_he": "הצללה בגני משחקים"},
        config=config,
    )

    labels = {row["label_he"] for row in payload["allowed_actions"]}
    assert "מענה לשאילתה" in labels
    assert "אחר" in labels
    assert "אישור" in labels
    assert "הסתייגות" in labels
    assert "התקשרות" in labels
    assert "אישור החלטה" not in labels
    assert any("below 0.75" in requirement for requirement in payload["requirements"])
    assert any("התקשרות" in requirement for requirement in payload["requirements"])
    assert any("action-scoped matter" in requirement for requirement in payload["requirements"])
    assert any("broad topic noun" in requirement for requirement in payload["requirements"])
    assert any("later advocacy" in requirement for requirement in payload["requirements"])
    assert any("concrete desired municipal change/action" in requirement for requirement in payload["requirements"])
    assert any("formal motion" in requirement for requirement in payload["requirements"])
    assert any("formal request wording" in requirement for requirement in payload["requirements"])
    assert any("מענה לשאילתה" in requirement and "not שאילתה" in requirement for requirement in payload["requirements"])
    assert any("initiating item/title" in requirement for requirement in payload["requirements"])
    assert any("הסתייגות" in requirement for requirement in payload["requirements"])
    assert any("formal/procedural rejection" in requirement for requirement in payload["requirements"])
    assert any("proposal_or_intent" in requirement for requirement in payload["requirements"])
    assert any("הצעה לסדר" in example["source_quote_he"] for example in payload["semantic_examples"])
    assert any("אפשר להעביר" in example["source_quote_he"] for example in payload["semantic_examples"])
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "known_topic" not in serialized
    assert "subject_matter_he" not in serialized
    assert "matter_he" in serialized
    assert "outcome" in serialized


def test_topic_subject_v3_matter_display_and_identifiers_preserve_identity_matter() -> None:
    matter = "ויתור על חלק מזכות חכירה במגרש400 הידוע כגוש38263 חלקה20"
    artifact = _artifact_dataclass(
        real_text=f"בקשה בנושא {matter}",
        topic_label_he="זכויות חכירה במקרקעין",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    payload = normalize_topic_subject_v3_event_payload(
        payload={
            "context_id": context.context_id,
            "target_artifact_id": artifact.artifact_id,
            "is_event": True,
            "action_type_he": "בקשה",
            "action_type_confidence": 0.91,
            "matter_he": matter,
            "action_quote_he": artifact.real_text,
            "outcome_is_decision": False,
            "confidence": 0.9,
        },
        context=context,
    )

    identifiers = {(item["type"], item["value_he"]): item for item in payload["matter_identifiers"]}

    assert payload["matter_he"] == matter
    assert payload["matter_display_he"] == "ויתור על חלק מזכות חכירה"
    assert identifiers[("lot", "400")]["raw_text_he"] == "במגרש400"
    assert identifiers[("block", "38263")]["raw_text_he"] == "כגוש38263"
    assert identifiers[("parcel", "20")]["canonical_he"] == "חלקה 20"


def test_topic_subject_v3_matter_identifiers_include_address_and_entity() -> None:
    matter = "הקצאת מקרקעין חלקה116 בגוש38133 ברחוב פריץ אלברט4 לעמותת אוהל בנימין עמותה רשומה580674802"
    artifact = _artifact_dataclass(real_text=f"מבוקשת {matter}", topic_label_he="הקצאת מקרקעין")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    payload = normalize_topic_subject_v3_event_payload(
        payload={
            "context_id": context.context_id,
            "target_artifact_id": artifact.artifact_id,
            "is_event": True,
            "action_type_he": "התקשרות",
            "action_type_confidence": 0.91,
            "matter_he": matter,
            "action_quote_he": artifact.real_text,
            "outcome_is_decision": False,
            "confidence": 0.9,
        },
        context=context,
    )

    identifiers = {(item["type"], item["label_he"]): item for item in payload["matter_identifiers"]}

    assert payload["matter_he"] == matter
    assert identifiers[("parcel", "חלקה")]["value_he"] == "116"
    assert identifiers[("block", "גוש")]["value_he"] == "38133"
    assert identifiers[("street_address", "רחוב")]["value_he"] == "פריץ אלברט 4"
    assert identifiers[("entity", "עמותה")]["value_he"] == "אוהל בנימין"
    assert identifiers[("registered_association_number", "עמותה רשומה")]["value_he"] == "580674802"


def test_topic_subject_model_policy_keeps_semantic_stages_on_primary_model_and_routes_helpers_to_schema_no_think_primary_model() -> None:
    small_model = "dicta-il/DictaLM-3.0-1.7B-Thinking:latest"
    default_config = TopicSubjectResearchConfig()
    config = TopicSubjectResearchConfig(small_model_name=small_model)
    legacy_config = TopicSubjectResearchConfig(small_model_name=small_model, use_schema_no_think_helpers=False)

    assert topic_subjects_module.topic_subject_model_for_stage(stage="topic_subject_v3_action_subject_extraction", config=config) == config.model_name
    assert topic_subjects_module.topic_subject_model_for_stage(stage="topic_subject_v3_event_judge", config=config) == config.model_name
    assert topic_subjects_module.topic_subject_model_for_stage(stage="unknown_new_semantic_stage", config=config) == config.model_name
    assert topic_subjects_module.topic_subject_model_for_stage(stage="topic_subject_v3_quote_repair", config=config) == config.model_name
    assert topic_subjects_module.topic_subject_model_for_stage(stage="topic_subject_v3_evidence_entailment", config=config) == config.model_name
    assert topic_subjects_module.topic_subject_model_for_stage(stage="topic_subject_v3_formal_decision_evidence_repair", config=config) == config.model_name
    assert topic_subjects_module.topic_subject_model_for_stage(stage="topic_subject_v3_json_repair", config=config) == config.model_name
    assert topic_subjects_module.topic_subject_model_for_stage(stage="topic_subject_v3_quote_repair", config=default_config) == default_config.model_name
    assert topic_subjects_module.topic_subject_model_for_stage(stage="topic_subject_v3_quote_repair", config=legacy_config) == small_model
    assert topic_subjects_module.topic_subject_stage_uses_schema_no_think(stage="topic_subject_v3_quote_repair", config=config) is True
    assert topic_subjects_module.topic_subject_stage_uses_schema_no_think(stage="topic_subject_v3_action_subject_extraction", config=config) is False
    assert topic_subjects_module.topic_subject_stage_uses_schema_no_think(stage="topic_subject_v3_quote_repair", config=legacy_config) is False
    assert topic_subjects_module.topic_subject_thinking_enabled_for_model(config.model_name) is True
    assert topic_subjects_module.topic_subject_thinking_enabled_for_model(small_model) is False
    assert topic_subjects_module.topic_subject_system_prompt("/no_think\nReturn JSON only.", think=True) == "Return JSON only."
    assert topic_subjects_module.topic_subject_system_prompt("Return JSON only.", think=False) == "/no_think\nReturn JSON only."


def test_ollama_v3_request_bodies_route_semantic_and_helper_models_with_correct_thinking_and_schema_format() -> None:
    artifact = _artifact_dataclass(real_text="אבקש לאשר נסיעה מקצועית לכנס ארצי בנושא שירות לתושב.", topic_label_he="חינוך")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    small_model = "dicta-il/DictaLM-3.0-1.7B-Thinking:latest"
    config = TopicSubjectResearchConfig(small_model_name=small_model)
    calls: list[dict] = []

    class CapturingV3Client(topic_subjects_module.OllamaTopicSubjectV3Client):
        def _post_chat_with_timeout_retry(self, *, body, config, attempts=2):  # type: ignore[no-untyped-def]
            body = {**body, "_test_attempts": attempts}
            calls.append(body)
            return {"message": {"content": "{}"}}, None

    client = CapturingV3Client()
    client.normalize_event(context=context, config=config)
    semantic_body = calls[-1]

    assert semantic_body["model"] == config.model_name
    assert semantic_body["think"] is True
    assert semantic_body["_test_attempts"] == 1
    assert "/no_think" not in semantic_body["messages"][0]["content"]

    client.assess_event_evidence(
        context=context,
        normalized_event={"is_event": True},
        event_payload={"is_event": True, "action_type_he": "בקשה", "matter_he": "נסיעה מקצועית", "outcome_is_decision": False},
        config=config,
    )
    support_body = calls[-1]

    assert support_body["model"] == config.model_name
    assert support_body["think"] is False
    assert support_body["_test_attempts"] == 2
    assert support_body["messages"][0]["content"].startswith("/no_think\n")
    assert isinstance(support_body["format"], dict)
    assert support_body["format"]["type"] == "object"
    assert support_body["format"]["properties"]["field_assessments"]["type"] == "object"
    assert "action_type_he" in support_body["format"]["properties"]["field_assessments"]["properties"]


def test_topic_subject_v3_source_context_exposes_late_action_spans() -> None:
    background = " ".join(f"רקע כללי {index}" for index in range(90))
    action = "וועדת שניים בראשות ראש העיר אשרו את השתתפות העובדת בסמינר המקצועי."
    artifact = _artifact_dataclass(real_text=f"{background} {action}", topic_label_he="חינוך")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    spans = topic_subjects_module.topic_subject_v3_text_spans(artifact_id=artifact.artifact_id, raw_text=artifact.real_text)
    payload = topic_subject_v3_normalization_payload(context=context, max_text_chars=3500)
    target_spans = payload["source_context"]["target_row"]["text_spans"]

    assert len(spans) > 1
    assert any(action in span["raw_text"] for span in spans)
    assert any(span["kind_hint"] == "overlap_window" for span in spans)
    assert target_spans == spans
    assert "primary_action_span_ids" in payload["schema"]
    assert "span_roles" in payload["schema"]


def test_topic_subject_v3_source_context_reuses_upstream_spans_and_structure_metadata() -> None:
    raw_text = "רקע קצר. ההסתייגות היא בנושא תכנית שכונה כעיר, קיצוץ מתקציב התכנית."
    artifact = _artifact_dataclass(
        real_text=raw_text,
        topic_label_he="תכנית שכונה כעיר",
        metadata={
            "artifact_metadata": {
                "corrected_text_he": "רקע קצר. ההסתייגות היא בנושא תכנית שכונה כעיר, קיצוץ מתקציב התכנית.",
                "summary_he": "סיכום שאינו ראיית מקור",
                "structural_role": "action_candidate",
                "structure_metadata": {
                    "structure_unit_id": "su-1",
                    "semantic_unit_id": "sem-1",
                    "source_block_ids": ["block-7"],
                    "structural_role": "action_candidate",
                },
                "spans": [
                    {
                        "span_id": "span-upstream-1",
                        "span_role": "background",
                        "raw_text": "רקע קצר.",
                        "corrected_text_he": "רקע קצר.",
                        "char_start": 0,
                        "char_end": 9,
                    },
                    {
                        "span_id": "span-upstream-2",
                        "span_role": "action_candidate",
                        "raw_text": "ההסתייגות היא בנושא תכנית שכונה כעיר, קיצוץ מתקציב התכנית.",
                        "corrected_text_he": "ההסתייגות היא בנושא תכנית שכונה כעיר, קיצוץ מתקציב התכנית.",
                        "source_block_ids": ["block-7"],
                    },
                ],
                "topic_assignment": {
                    "is_topic_bearing": True,
                    "status": "active",
                    "route": "test_route",
                    "confidence": 0.91,
                    "child_label_he": "אסור להיחשף בפרומפט",
                },
                "topic_headline_he": "תכנית שכונה כעיר",
                "topic_subject_he": "תכנית שכונה כעיר",
                "topic_context_source": "subject_scoped_speaker_anchor",
                "topic_headline_source": "speaker_subject_objection",
            }
        },
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    payload = topic_subject_v3_normalization_payload(context=context, max_text_chars=3500)
    target_row = payload["source_context"]["target_row"]

    assert target_row["structural_role"] == "action_candidate"
    assert target_row["structure_metadata"]["structure_unit_id"] == "su-1"
    assert target_row["upstream_summary_he"] == "סיכום שאינו ראיית מקור"
    assert target_row["upstream_subject_hint"]["topic_subject_he"] == "תכנית שכונה כעיר"
    assert target_row["upstream_subject_hint"]["topic_headline_source"] == "speaker_subject_objection"
    assert target_row["upstream_subject_hint"]["hint_policy"] == "strong_context_hint_only_not_source_evidence"
    assert target_row["upstream_topic_metadata"]["is_topic_bearing"] is True
    assert "child_label_he" not in target_row["upstream_topic_metadata"]
    assert "child_label_he" not in target_row["upstream_subject_hint"]
    assert target_row["text_spans"][0]["span_id"] == "span-upstream-1"
    assert target_row["text_spans"][1]["kind_hint"] == "action_candidate"
    assert target_row["text_spans"][1]["span_source"] == "upstream"
    assert any("summary only" in instruction for instruction in payload["source_context"]["instructions"])
    assert any("upstream_subject_hint" in instruction for instruction in payload["source_context"]["instructions"])


def test_topic_subject_v3_source_context_omits_heavy_diagnostics_from_prompt_rows() -> None:
    target = _artifact_dataclass(
        real_text="חברי המועצה דנו בנושא פיתוח פארק עירוני.",
        topic_label_he="פיתוח פארק",
        artifact_id="artifact-target",
        source_ordinal=1,
        metadata={"artifact_metadata": {"source_paths": {"protocol_run_dir": "/tmp/protocol_31_20251229_2cdaf368_v1"}}},
    )
    nearby = _artifact_dataclass(
        real_text="הדיון נמשך לאחר הצגת נתוני התכנון והתקציב.",
        topic_label_he="פיתוח פארק",
        artifact_id="artifact-nearby",
        source_ordinal=2,
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[target], context_artifacts=[target, nearby])[0]

    payload = topic_subject_v3_normalization_payload(context=context, max_text_chars=3500)
    target_row = payload["source_context"]["target_row"]
    nearby_row = payload["source_context"]["nearby_rows"][0]

    assert "source_paths" not in target_row
    assert "general_text_metadata" not in target_row
    assert "raw_date_mentions" not in target_row
    assert "text_spans" in target_row
    assert "text_spans" not in nearby_row
    assert nearby_row["raw_text"] == nearby.real_text


def test_topic_subject_v3_supported_subject_hint_uses_step4_subject_not_classifier_label() -> None:
    artifact = _artifact_dataclass(
        real_text="ההסתייגות היא בנושא תכנית שכונה כעיר, שכונות התקווה, עזרא והארגזים.",
        topic_label_he="תקציב כללי",
        metadata={
            "artifact_metadata": {
                "topic_subject_he": "תכנית שכונה כעיר, שכונות התקווה, עזרא והארגזים",
                "topic_headline_he": "תכנית שכונה כעיר, שכונות התקווה, עזרא והארגזים",
            }
        },
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    assert topic_subjects_module.topic_subject_v3_supported_topic_hint_matter(context) == "תכנית שכונה כעיר, שכונות התקווה, עזרא והארגזים"


def test_topic_subject_v3_misleading_subject_hint_is_not_supported_by_source_text() -> None:
    artifact = _artifact_dataclass(
        real_text="ההסתייגות היא בנושא תקציב המשפחתונים לנשים עובדות.",
        topic_label_he="תקציב משפחתונים",
        metadata={"artifact_metadata": {"topic_subject_he": "תכנית שכונה כעיר", "topic_headline_he": "תכנית שכונה כעיר"}},
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    assert topic_subjects_module.topic_subject_v3_supported_topic_hint_matter(context) == ""


def test_topic_subject_v3_extraction_payload_uses_generic_evidence_roles() -> None:
    artifact = _artifact_dataclass(
        real_text="התקיים דיון בנושא תקציב הגיל הרך ללא החלטה פורמלית.",
        topic_label_he="תקציב הגיל הרך",
        metadata={"artifact_metadata": {"topic_subject_he": "תקציב הגיל הרך"}},
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    payload = topic_subject_v3_extraction_payload(
        context=context,
        normalized_event={"is_event": True, "event_phase": "discussed"},
        config=TopicSubjectResearchConfig(),
    )

    allowed_labels = {action["label_he"] for action in payload["allowed_actions"]}
    assert "דיון" in allowed_labels
    assert "evidence_roles" in payload["schema"]
    assert "action_evidence_quote_he" in payload["schema"]["evidence_roles"]
    assert "event_candidates" in payload["schema"]
    assert "selected_candidate_id" in payload["schema"]
    json_schema = topic_subjects_module.topic_subject_json_schema_from_prompt_schema(payload["schema"])
    assert json_schema is not None
    assert json_schema["properties"]["selected_candidate_id"] == {"type": ["string", "null"]}
    assert any("subject/matter evidence" in requirement for requirement in payload["requirements"])
    assert any("Use דיון" in requirement for requirement in payload["requirements"])


def test_topic_subject_v3_discussion_action_maps_to_discussed_phase() -> None:
    artifact = _artifact_dataclass(real_text="התקיים דיון בנושא שירותי שמירה.", topic_label_he="שירותי שמירה")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    phase = topic_subjects_module.topic_subject_v3_event_phase(
        context=context,
        is_event=True,
        action_type="דיון",
        outcome_is_decision=False,
    )

    assert phase == "discussed"


def test_topic_subject_v3_candidate_selector_prefers_current_response_action() -> None:
    artifact = _artifact_dataclass(
        real_text="סעיף3 : שאילתה בנושא תיקון מעלית המשכן לאומנויות הבמה הוקראה תשובת ראש העיר לשאילתה של חברת מועצה.",
        topic_label_he="תיקון מעלית המשכן לאומנויות הבמה",
        metadata={"artifact_metadata": {"topic_subject_he": "תיקון מעלית המשכן לאומנויות הבמה"}},
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "שאילתה",
            "matter_he": "תיקון מעלית המשכן לאומנויות הבמה",
            "action_quote_he": "שאילתה בנושא תיקון מעלית המשכן לאומנויות הבמה",
            "outcome_is_decision": False,
            "event_candidates": [
                {
                    "candidate_id": "title",
                    "is_event": True,
                    "is_current_action": False,
                    "is_title_only": True,
                    "action_type_he": "שאילתה",
                    "matter_he": "תיקון מעלית המשכן לאומנויות הבמה",
                    "action_quote_he": "שאילתה בנושא תיקון מעלית המשכן לאומנויות הבמה",
                    "confidence": 0.9,
                },
                {
                    "candidate_id": "response",
                    "is_event": True,
                    "is_current_action": True,
                    "is_title_only": False,
                    "action_type_he": "מענה לשאילתה",
                    "matter_he": "תיקון מעלית המשכן לאומנויות הבמה",
                    "action_quote_he": "הוקראה תשובת ראש העיר לשאילתה של חברת מועצה",
                    "phase_quote_he": "הוקראה תשובת ראש העיר לשאילתה של חברת מועצה",
                    "confidence": 0.86,
                },
            ],
        },
        context=context,
    )

    assert event_payload["selected_candidate_id"] == "response"
    assert event_payload["action_type_he"] == "מענה לשאילתה"
    assert event_payload["event_phase"] == "response_given"
    assert "הוקראה תשובת" in event_payload["action_quote_he"]


def test_topic_subject_v3_lifecycle_repair_changes_inquiry_title_to_response_action() -> None:
    artifact = _artifact_dataclass(
        real_text="סעיף3 : שאילתה בנושא תיקון מעלית המשכן לאומנויות הבמה הוקראה תשובת ראש העיר לשאילתה של חברת מועצה.",
        topic_label_he="תיקון מעלית המשכן לאומנויות הבמה",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "שאילתה",
            "action_type_confidence": 0.94,
            "matter_he": "תיקון מעלית המשכן לאומנויות הבמה",
            "action_quote_he": "שאילתה בנושא תיקון מעלית המשכן לאומנויות הבמה",
            "outcome_is_decision": False,
            "confidence": 0.94,
        },
        context=context,
    )

    assert event_payload["action_type_he"] == "מענה לשאילתה"
    assert event_payload["event_phase"] == "response_given"
    assert event_payload["action_type_status"] == "repaired_response_to_inquiry_from_lifecycle_evidence"


def test_topic_subject_v3_structural_role_can_skip_clear_upstream_non_event() -> None:
    artifact = _artifact_dataclass(
        real_text="פרוטוקול ישיבה לא מן המניין מס 31",
        topic_label_he="סדר יום ושאילתות",
        metadata={"artifact_metadata": {"structural_role": "protocol_header"}},
    )

    structural = topic_subjects_module.topic_subject_v3_structural_non_event_payload(artifact)

    assert structural == {
        "row_role": "protocol_header",
        "reason": "הארטיפקט סומן במעלה הזרם ככותרת פרוטוקול.",
    }


def test_topic_subject_v3_evidence_entailment_payload_checks_action_matter_outcome() -> None:
    artifact = _artifact_dataclass(real_text="אבקש לאשר נסיעה מקצועית לכנס ארצי.", topic_label_he="חינוך")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    payload = topic_subjects_module.topic_subject_v3_evidence_entailment_payload(
        context=context,
        normalized_event={"is_event": True, "matter_candidate_he": "נסיעה מקצועית לכנס ארצי"},
        event_payload={
            "is_event": True,
            "action_type_he": "אישור",
            "matter_he": "נסיעה מקצועית לכנס ארצי",
            "outcome_is_decision": False,
        },
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["task"] == "topic_subject_v3_evidence_entailment"
    assert "action_type_he" in payload["schema"]["field_assessments"]
    assert "matter_he" in payload["schema"]["field_assessments"]
    assert "outcome" in payload["schema"]["field_assessments"]
    assert any("under 220" in requirement for requirement in payload["requirements"])
    assert any("action-scoped matter" in requirement for requirement in payload["requirements"])
    assert any("later advocacy" in requirement for requirement in payload["requirements"])
    assert any("formal/procedural rejection" in requirement for requirement in payload["requirements"])
    assert "repair_required" in payload["schema"]
    assert "known_topic" not in serialized
    assert "topic_label_he" not in serialized


def test_process_topic_subject_v3_applies_evidence_entailment_repair() -> None:
    artifact = _artifact_dataclass(real_text="אבקש לאשר נסיעה מקצועית לכנס ארצי בנושא שירות לתושב מטעם העירייה.", topic_label_he="חינוך")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    class EvidenceRepairClient(MockTopicSubjectV3Client):
        def extract_event(self, *, context, normalized_event, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "is_event": True,
                "event_key_he": "אישור: דבר שלא מופיע",
                "action_type_he": "אישור",
                "action_type_confidence": 0.92,
                "matter_he": "דבר שלא מופיע",
                "action_quote_he": context.target_artifact.real_text,
                "outcome_is_decision": False,
                "target_row_role": "action_anchor",
                "confidence": 0.9,
                "rationale_he": "over inferred",
            }

        def assess_event_evidence(self, *, context, normalized_event, event_payload, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "entailment_status": "partially_entailed",
                "repair_required": True,
                "repaired_event": {
                    "context_id": context.context_id,
                    "target_artifact_id": context.target_artifact.artifact_id,
                    "is_event": True,
                    "event_key_he": "בקשה: נסיעה מקצועית לכנס ארצי בנושא שירות לתושב",
                    "action_type_he": "בקשה",
                    "action_type_confidence": 0.88,
                    "matter_he": "נסיעה מקצועית לכנס ארצי בנושא שירות לתושב",
                    "action_quote_he": context.target_artifact.real_text,
                    "outcome_is_decision": False,
                    "target_row_role": "action_anchor",
                    "confidence": 0.86,
                    "rationale_he": "המקור מבקש אישור אך אינו מתאר אישור בפועל.",
                },
                "failure_reasons": ["action_and_matter_over_inferred"],
                "rationale_he": "repair to supported request",
            }

    event, row = process_topic_subject_v3_context(
        context=context,
        client=EvidenceRepairClient(),
        config=TopicSubjectResearchConfig(),
    )

    assert event is not None
    assert event.event_payload["action_type_he"] == "בקשה"
    assert event.event_payload["matter_he"] == "נסיעה מקצועית לכנס ארצי בנושא שירות לתושב"
    assert event.event_payload["outcome_is_decision"] is False
    assert event.event_payload["v3_evidence_entailment"]["repair_applied"] is True
    assert row.quality_status == "accepted"


def test_process_topic_subject_v3_evidence_repair_restores_action_scoped_matter() -> None:
    artifact = _artifact_dataclass(
        real_text="אבקש להביא לדיון שינוי תנאי ההתקשרות עם ספק השירות כדי להאריך את תקופת השירות.",
        topic_label_he="התקשרות עם ספק השירות",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    class NarrowMatterClient(MockTopicSubjectV3Client):
        def extract_event(self, *, context, normalized_event, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "is_event": True,
                "event_key_he": "בקשה: ספק השירות",
                "action_type_he": "בקשה",
                "action_type_confidence": 0.92,
                "matter_he": "ספק השירות",
                "action_quote_he": context.target_artifact.real_text,
                "outcome_is_decision": False,
                "target_row_role": "action_anchor",
                "confidence": 0.9,
                "rationale_he": "matter narrowed to object only",
            }

        def assess_event_evidence(self, *, context, normalized_event, event_payload, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "entailment_status": "partially_entailed",
                "repair_required": True,
                "repaired_event": {
                    **event_payload,
                    "matter_he": "שינוי תנאי ההתקשרות עם ספק השירות כדי להאריך את תקופת השירות",
                    "event_key_he": "בקשה: שינוי תנאי ההתקשרות עם ספק השירות",
                    "rationale_he": "המקור מציין את הפעולה על הספק, לא רק את שם הספק.",
                },
                "field_assessments": {
                    "action_type_he": {"status": "entailed", "source_quote_he": "אבקש להביא לדיון", "rationale_he": "בקשה נתמכת."},
                    "matter_he": {"status": "not_entailed", "source_quote_he": "שינוי תנאי ההתקשרות עם ספק השירות", "rationale_he": "החומר המקורי רחב יותר משם הספק."},
                    "outcome": {"status": "not_applicable", "source_quote_he": None, "rationale_he": "אין תוצאה."},
                },
                "failure_reasons": [],
                "rationale_he": "repair to action-scoped matter",
            }

    event, row = process_topic_subject_v3_context(
        context=context,
        client=NarrowMatterClient(),
        config=TopicSubjectResearchConfig(),
    )

    assert event is not None
    assert event.event_payload["matter_he"] == "שינוי תנאי ההתקשרות עם ספק השירות כדי להאריך את תקופת השירות"
    assert event.event_payload["v3_evidence_entailment"]["repair_applied"] is True
    assert row.quality_status == "accepted"


def test_process_topic_subject_v3_repairs_unsupported_formal_rejection_to_non_decision_action() -> None:
    artifact = _artifact_dataclass(
        real_text="הסתייגות: דרישה לביטול תוספת לתקציב הפארק. לוקחים מעניים ונותנים לעשירים. לא לאשר. לדרוש שינוי.",
        topic_label_he="ביטול תוספת לתקציב הפארק",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    class UnsupportedFormalRejectionClient(MockTopicSubjectV3Client):
        def extract_event(self, *, context, normalized_event, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "is_event": True,
                "event_key_he": "דחייה: תקציב הפארק",
                "action_type_he": "דחייה",
                "action_type_confidence": 0.94,
                "matter_he": "תקציב הפארק",
                "action_quote_he": "לא לאשר.",
                "outcome_is_decision": True,
                "outcome": {
                    "outcome_type": "rejected",
                    "outcome_label_he": "דחייה",
                    "outcome_quote_he": "לא לאשר.",
                    "confidence": 0.94,
                    "limitations": [],
                },
                "target_row_role": "action_anchor",
                "confidence": 0.94,
                "rationale_he": "unsupported formal rejection",
            }

        def repair_formal_decision_evidence(self, *, context, normalized_event, event_payload, validation_failures, config):  # type: ignore[no-untyped-def]
            return {
                **event_payload,
                "event_key_he": "בקשה: ביטול תוספת לתקציב הפארק",
                "action_type_he": "בקשה",
                "action_type_confidence": 0.88,
                "matter_he": "ביטול תוספת לתקציב הפארק",
                "action_quote_he": "הסתייגות: דרישה לביטול תוספת לתקציב הפארק",
                "outcome_is_decision": False,
                "outcome": None,
                "confidence": 0.86,
                "rationale_he": "המקור מתאר דרישה/הסתייגות ולא החלטת דחייה פורמלית.",
            }

    event, row = process_topic_subject_v3_context(
        context=context,
        client=UnsupportedFormalRejectionClient(),
        config=TopicSubjectResearchConfig(),
    )

    assert event is not None
    assert event.event_payload["action_type_he"] == "הסתייגות"
    assert event.event_payload["matter_he"] == "ביטול תוספת לתקציב הפארק"
    assert event.event_payload["outcome_is_decision"] is False
    assert event.event_payload["event_phase"] == "objection_submitted"
    assert event.event_payload["action_type_status"] == "repaired_objection_from_unsupported_formal_decision_action"
    assert "objection_without_current_formal_decision_evidence" in event.event_payload["semantic_repairs"]
    assert event.event_payload["action_quote_he"].startswith("הסתייגות")
    assert event.validation_status == "accepted"
    assert row.quality_status == "accepted"


def test_process_topic_subject_v3_fallback_repairs_when_formal_rejection_repair_repeats_error() -> None:
    artifact = _artifact_dataclass(
        real_text="בקשה לביטול תוספת לתקציב הפארק. לוקחים מעניים ונותנים לעשירים. לא לאשר. לדרוש שינוי.",
        topic_label_he="ביטול תוספת לתקציב הפארק",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    class RepeatedBadRepairClient(MockTopicSubjectV3Client):
        def extract_event(self, *, context, normalized_event, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "is_event": True,
                "event_key_he": "דחייה: תקציב הפארק",
                "action_type_he": "דחייה",
                "action_type_confidence": 0.94,
                "matter_he": "תקציב הפארק",
                "action_quote_he": "לא לאשר.",
                "outcome_is_decision": True,
                "outcome": {
                    "outcome_type": "rejected",
                    "outcome_label_he": "דחייה",
                    "outcome_quote_he": "לא לאשר.",
                    "confidence": 0.94,
                    "limitations": [],
                },
                "target_row_role": "action_anchor",
                "confidence": 0.94,
                "rationale_he": "unsupported formal rejection",
            }

        def repair_formal_decision_evidence(self, *, context, normalized_event, event_payload, validation_failures, config):  # type: ignore[no-untyped-def]
            return event_payload

    event, row = process_topic_subject_v3_context(
        context=context,
        client=RepeatedBadRepairClient(),
        config=TopicSubjectResearchConfig(),
    )

    assert event is not None
    assert event.event_payload["action_type_he"] == "בקשה"
    assert event.event_payload["matter_he"] == "ביטול תוספת לתקציב הפארק"
    assert event.event_payload["outcome_is_decision"] is False
    assert event.event_payload["event_phase"] == "open_request"
    assert event.event_payload["v3_formal_decision_repair"]["deterministic_fallback_applied"] is True
    assert event.validation_status == "accepted"
    assert row.quality_status == "accepted"


def test_process_topic_subject_v3_removes_unentailed_outcome() -> None:
    artifact = _artifact_dataclass(real_text="מתואר תרגיל משותף לרשות ולמשטרה ללא החלטת אישור מפורשת.", topic_label_he="ביטחון")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    class OutcomeOverreachClient(MockTopicSubjectV3Client):
        def extract_event(self, *, context, normalized_event, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "is_event": True,
                "event_key_he": "אחר: תרגיל משותף",
                "action_type_he": "אחר",
                "other_action_type_he": "תרגיל משותף",
                "action_type_confidence": 0.91,
                "matter_he": "תרגיל משותף לרשות ולמשטרה",
                "action_quote_he": context.target_artifact.real_text,
                "outcome_is_decision": True,
                "outcome": {
                    "outcome_type": "approved",
                    "outcome_label_he": "אישור התרגיל",
                    "outcome_summary_he": "אושר התרגיל",
                    "outcome_quote_he": context.target_artifact.real_text,
                    "confidence": 0.8,
                    "limitations": [],
                },
                "target_row_role": "action_anchor",
                "confidence": 0.88,
                "rationale_he": "overreached outcome",
            }

        def assess_event_evidence(self, *, context, normalized_event, event_payload, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "entailment_status": "partially_entailed",
                "field_assessments": {
                    "action_type_he": {"status": "entailed", "source_quote_he": context.target_artifact.real_text, "rationale_he": "supported"},
                    "matter_he": {"status": "entailed", "source_quote_he": context.target_artifact.real_text, "rationale_he": "supported"},
                    "outcome": {"status": "not_entailed", "source_quote_he": None, "rationale_he": "no approval"},
                },
                "repair_required": False,
                "repaired_event": None,
                "failure_reasons": [],
                "rationale_he": "outcome unsupported",
            }

    event, row = process_topic_subject_v3_context(
        context=context,
        client=OutcomeOverreachClient(),
        config=TopicSubjectResearchConfig(),
    )

    assert event is not None
    assert event.event_payload["outcome_is_decision"] is False
    assert event.event_payload["outcome"] is None
    assert event.event_payload["v3_evidence_entailment"]["outcome_removed_by_evidence"] is True
    assert row.outcome_by_dicta == ""
    assert row.quality_status == "accepted"


def test_topic_subject_v3_invalid_evidence_enum_becomes_uncertain_failure() -> None:
    assessment = topic_subjects_module.topic_subject_v3_normalize_evidence_assessment(
        {
            "entailment_status": "entailed",
            "field_assessments": {
                "action_type_he": {"status": "entitled"},
                "matter_he": {"status": "entailed"},
                "outcome": {"status": "not_applicable"},
            },
            "repair_required": False,
            "failure_reasons": [],
        }
    )
    failures = topic_subjects_module.topic_subject_v3_unrepaired_evidence_failures(assessment)

    assert assessment["field_assessments"]["action_type_he"]["status"] == "uncertain"
    assert "invalid_action_type_he_status:entitled" in assessment["schema_warnings"]
    assert "evidence_action_type_he_uncertain" in failures


def test_topic_subject_v3_uncertain_entailment_without_decision_outcome_can_pass_when_fields_entailed() -> None:
    assessment = topic_subjects_module.topic_subject_v3_normalize_evidence_assessment(
        {
            "entailment_status": "uncertain",
            "field_assessments": {
                "action_type_he": {"status": "entailed", "source_quote_he": "את רוצה להגדיל את התקציב."},
                "matter_he": {"status": "entailed", "source_quote_he": "תקציב של הגיל הרך"},
                "outcome": {"status": "not_entailed", "source_quote_he": None},
            },
            "repair_required": False,
            "failure_reasons": [],
        }
    )
    failures = topic_subjects_module.topic_subject_v3_unrepaired_evidence_failures(
        assessment_payload=assessment,
        event_payload={"is_event": True, "outcome_is_decision": False},
    )

    assert failures == []


def test_topic_subject_v3_evidence_assessment_recovers_top_level_field_assessments() -> None:
    assessment = topic_subjects_module.topic_subject_v3_normalize_evidence_assessment(
        {
            "entailment_status": "entailed",
            "action_type_he": {"status": "entailed", "evidence_quote_he": "דרישה לביטול"},
            "matter_he": {"status": "entailed", "evidence_quote_he": "תב\"ר פארק"},
            "outcome": {"status": "not_applicable"},
            "repair_required": False,
            "failure_reasons": [],
        }
    )

    assert assessment["field_assessments"]["action_type_he"]["status"] == "entailed"
    assert assessment["field_assessments"]["matter_he"]["status"] == "entailed"
    assert assessment["field_assessments"]["outcome"]["status"] == "not_applicable"
    assert "field_assessment_recovered_from_top_level:action_type_he" in assessment["schema_warnings"]


def test_topic_subject_v3_uncertain_evidence_status_needs_review_not_failed() -> None:
    status = topic_subjects_module.topic_subject_v3_validation_status(
        event_payload={"is_event": True},
        judge_payload={"judge_status": "accepted"},
        failure_reasons=["evidence_matter_he_uncertain"],
    )

    assert status == "needs_review"


def test_topic_subject_v3_high_confidence_judge_action_disagreement_needs_review_only() -> None:
    status = topic_subjects_module.topic_subject_v3_validation_status(
        event_payload={"is_event": True, "action_type_he": "שאילתה", "matter_he": "תקציב הגיל הרך"},
        judge_payload={
            "judge_status": "accepted",
            "judge_prediction": {"is_event": True, "action_type_he": "בקשה", "matter_he": "תקציב הגיל הרך", "confidence": 0.95},
        },
        failure_reasons=[],
    )

    assert status == "needs_review"


def test_topic_subject_v3_judge_action_disagreement_allows_evidenced_wrapper_action() -> None:
    status = topic_subjects_module.topic_subject_v3_validation_status(
        event_payload={
            "is_event": True,
            "action_type_he": "הסתייגות",
            "matter_he": "קרצוף כבישים",
            "action_quote_he": "ההסתייגות בנושא קרצוף כבישים, דרישה להוספת פירוט ביחס לכבישים בדרום העיר.",
            "outcome_is_decision": False,
        },
        judge_payload={
            "judge_status": "accepted",
            "judge_prediction": {"is_event": True, "action_type_he": "בקשה", "matter_he": "קרצוף כבישים", "confidence": 0.97},
        },
        failure_reasons=[],
    )

    assert status == "accepted"


def test_topic_subject_v3_judge_action_disagreement_requires_wrapper_evidence() -> None:
    status = topic_subjects_module.topic_subject_v3_validation_status(
        event_payload={
            "is_event": True,
            "action_type_he": "הסתייגות",
            "matter_he": "קרצוף כבישים",
            "action_quote_he": "מבקשים להוסיף פירוט ביחס לכבישים בדרום העיר.",
            "outcome_is_decision": False,
        },
        judge_payload={
            "judge_status": "accepted",
            "judge_prediction": {"is_event": True, "action_type_he": "בקשה", "matter_he": "קרצוף כבישים", "confidence": 0.97},
        },
        failure_reasons=[],
    )

    assert status == "needs_review"


def test_topic_subject_v3_normalizes_flat_outcome_fields() -> None:
    artifact = _artifact_dataclass(
        real_text="החלטה: המועצה החליטה לאשר את תקציב הפעילות השנתי.",
        topic_label_he="תקציב הפעילות השנתי",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    payload = normalize_topic_subject_v3_event_payload(
        payload={
            "context_id": context.context_id,
            "target_artifact_id": context.target_artifact.artifact_id,
            "is_event": True,
            "action_type_he": "אישור",
            "action_type_confidence": 0.94,
            "matter_he": "תקציב הפעילות השנתי",
            "action_quote_he": "החלטה: המועצה החליטה לאשר את תקציב הפעילות השנתי.",
            "outcome_is_decision": True,
            "outcome_type": "approved",
            "outcome_label_he": "אישור",
            "outcome_quote_he": "החלטה: המועצה החליטה לאשר את תקציב הפעילות השנתי.",
            "outcome_evidence_classification": "actual_result",
            "confidence": 0.93,
        },
        context=context,
    )

    assert payload["outcome"]["outcome_type"] == "approved"
    assert payload["outcome"]["outcome_label_he"] == "אישור"
    assert payload["outcome"]["outcome_quote_he"].startswith("החלטה")
    assert payload["outcome"]["outcome_evidence_classification"] == "actual_result"
    assert "flat_outcome_field_recovered:outcome_quote_he" in payload["schema_warnings"]


def test_topic_subject_v3_prefers_formal_result_quote_over_vote_count() -> None:
    artifact = _artifact_dataclass(
        real_text=(
            "חברים, לסיים את ההצבעה בבקשה. נמנעים2 , בעד22. "
            "תסיימו את ההצבעה מ א ו ש ר. "
            "החלטה: המועצה החליטה לאשר את דו\"ח הביקורת השנתי."
        ),
        topic_label_he="דו\"ח הביקורת השנתי",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    payload = normalize_topic_subject_v3_event_payload(
        payload={
            "context_id": context.context_id,
            "target_artifact_id": context.target_artifact.artifact_id,
            "is_event": True,
            "action_type_he": "אישור",
            "action_type_confidence": 0.92,
            "matter_he": "דו\"ח הביקורת השנתי",
            "action_quote_he": "נמנעים2 , בעד22.",
            "outcome_is_decision": True,
            "outcome": {
                "outcome_type": "approved",
                "outcome_label_he": "אישור",
                "outcome_quote_he": "נמנעים2 , בעד22.",
                "confidence": 0.9,
            },
            "confidence": 0.9,
        },
        context=context,
    )

    assert "החליטה לאשר" in payload["outcome"]["outcome_quote_he"]
    assert "formal_decision_outcome_without_formal_evidence" not in validate_topic_subject_v3_event_payload(context=context, event_payload=payload)


def test_topic_subject_v3_formal_evidence_accepts_spelled_approval_result() -> None:
    artifact = _artifact_dataclass(
        real_text="תסיימו את ההצבעה מ א ו ש ר.",
        topic_label_he="הצעה תקציבית",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    event_payload = {
        "is_event": True,
        "action_type_he": "אישור",
        "matter_he": "הצעה תקציבית",
        "action_quote_he": "תסיימו את ההצבעה מ א ו ש ר.",
        "outcome_is_decision": True,
        "outcome": {"outcome_type": "approved", "outcome_quote_he": "תסיימו את ההצבעה מ א ו ש ר."},
    }

    assert validate_topic_subject_v3_event_payload(context=context, event_payload=event_payload) == []


def test_topic_subject_v3_judge_prediction_recovers_flat_payload() -> None:
    prediction = topic_subjects_module.topic_subject_v3_judge_prediction(
        {
            "judge_status": "accepted",
            "action_type_he": "אישור",
            "matter_he": "דו\"ח ביקורת",
            "outcome_type": "approved",
            "outcome_label_he": "אישור",
            "outcome_quote_he": "החלטה: המועצה החליטה לאשר את דו\"ח הביקורת.",
            "confidence": 0.94,
        }
    )

    assert prediction["is_event"] is True
    assert prediction["action_type_he"] == "אישור"
    assert prediction["matter_he"] == "דו\"ח ביקורת"
    assert prediction["outcome_type"] == "approved"
    assert prediction["confidence"] == 0.94


def test_process_topic_subject_v3_reconsiders_non_event_with_grounded_action_quote() -> None:
    artifact = _artifact_dataclass(
        real_text="היו\"ר מסכם: את רוצה להגדיל את התקציב. את חושבת שהתקציב נמוך מדי.",
        topic_label_he="תקציב הגיל הרך",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    class UnderInferredClient(MockTopicSubjectV3Client):
        def extract_event(self, *, context, normalized_event, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "is_event": False,
                "matter_he": "תקציב הגיל הרך",
                "action_quote_he": "את רוצה להגדיל את התקציב.",
                "action_focus_quote_he": "את רוצה להגדיל את התקציב.",
                "target_row_role": "action_anchor",
                "confidence": 0.7,
            }

        def reconsider_non_event(self, *, context, normalized_event, extraction_payload, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "is_event": True,
                "action_type_he": "בקשה",
                "action_type_confidence": 0.92,
                "matter_he": "הגדלת תקציב הגיל הרך",
                "action_quote_he": "את רוצה להגדיל את התקציב.",
                "action_focus_quote_he": "את רוצה להגדיל את התקציב.",
                "outcome_is_decision": False,
                "target_row_role": "action_anchor",
                "confidence": 0.9,
                "rationale_he": "הציטוט מסכם פעולה עירונית רצויה קונקרטית.",
                "stage": "topic_subject_v3_non_event_reconsideration",
            }

    event, row = process_topic_subject_v3_context(
        context=context,
        client=UnderInferredClient(),
        config=TopicSubjectResearchConfig(),
    )

    assert event is not None
    assert event.event_payload["action_type_he"] == "בקשה"
    assert event.event_payload["event_phase"] == "open_request"
    assert event.event_payload["v3_non_event_reconsideration"]["repair_applied"] is True
    assert "repaired_non_event_from_grounded_action_reconsideration" in event.event_payload["semantic_repairs"]
    assert row.quality_status == "accepted"


def test_process_topic_subject_v3_locally_repairs_non_event_response_to_inquiry() -> None:
    artifact = _artifact_dataclass(
        real_text="סעיף 7: שאילתה בנושא מתחם ההחלקה על הקרח. הוקראה תשובת ראש העיר וגזבר העירייה לשאילתה של חברת המועצה.",
        topic_label_he="מתחם ההחלקה על הקרח",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    class ResponseUnderInferredClient(MockTopicSubjectV3Client):
        def extract_event(self, *, context, normalized_event, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "is_event": False,
                "matter_he": "מתחם ההחלקה על הקרח",
                "target_row_role": "action_anchor",
                "confidence": 0.7,
            }

        def reconsider_non_event(self, *, context, normalized_event, extraction_payload, config):  # type: ignore[no-untyped-def]
            raise AssertionError("local lifecycle repair should run before model reconsideration")

    event, row = process_topic_subject_v3_context(
        context=context,
        client=ResponseUnderInferredClient(),
        config=TopicSubjectResearchConfig(),
    )

    assert event is not None
    assert event.event_payload["action_type_he"] == "מענה לשאילתה"
    assert event.event_payload["event_phase"] == "response_given"
    assert event.event_payload["v3_non_event_local_repair"]["repair_reason"] == "response_to_inquiry_lifecycle_evidence"
    assert "repaired_non_event_response_to_inquiry_lifecycle" in event.event_payload["semantic_repairs"]
    assert row.quality_status == "accepted"


def test_process_topic_subject_v3_locally_repairs_non_event_explicit_objection() -> None:
    artifact = _artifact_dataclass(
        real_text="גב' קשת: ההסתייגות היא בנושא גני ילדים. בתחום גני הילדים נרשם קיצוץ משמעותי.",
        topic_label_he="גני ילדים",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    class ObjectionUnderInferredClient(MockTopicSubjectV3Client):
        def extract_event(self, *, context, normalized_event, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "is_event": False,
                "matter_he": "גני ילדים",
                "target_row_role": "action_anchor",
                "confidence": 0.7,
            }

        def reconsider_non_event(self, *, context, normalized_event, extraction_payload, config):  # type: ignore[no-untyped-def]
            raise AssertionError("local objection repair should run before model reconsideration")

    event, row = process_topic_subject_v3_context(
        context=context,
        client=ObjectionUnderInferredClient(),
        config=TopicSubjectResearchConfig(),
    )

    assert event is not None
    assert event.event_payload["action_type_he"] == "הסתייגות"
    assert event.event_payload["event_phase"] == "objection_submitted"
    assert event.event_payload["v3_non_event_local_repair"]["repair_reason"] == "explicit_objection_evidence"
    assert "repaired_non_event_explicit_objection" in event.event_payload["semantic_repairs"]
    assert row.quality_status == "accepted"


def test_topic_subject_v3_repairs_agenda_proposal_from_generic_request() -> None:
    artifact = _artifact_dataclass(
        real_text="הנדון: הצעה לסדר יום - הצפות חוזרות ברחבי העיר, טיפול בתשתיות ופיצוי התושבים.",
        topic_label_he="הצפות ותשתיות",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    normalized = normalize_topic_subject_v3_event_payload(
        payload={
            "context_id": context.context_id,
            "target_artifact_id": artifact.artifact_id,
            "is_event": True,
            "action_type_he": "בקשה",
            "action_type_confidence": 0.93,
            "matter_he": "הצפות חוזרות וטיפול בתשתיות ופיצוי התושבים",
            "action_quote_he": "הצעה לסדר יום - הצפות חוזרות ברחבי העיר",
            "outcome_is_decision": False,
            "target_row_role": "action_anchor",
        },
        context=context,
    )

    assert normalized["action_type_he"] == "הצעה לסדר יום"
    assert normalized["action_type_status"] == "repaired_agenda_proposal_from_request_shape"
    assert "repaired_agenda_proposal_from_request_shape" in normalized["semantic_repairs"]


def test_topic_subject_v3_repairs_other_to_approval_when_formal_decision_is_approved() -> None:
    artifact = _artifact_dataclass(
        real_text="מאושר - החלטה: המועצה החליטה ברוב קולות לבחור חבר מועצה כסגן בשכר לראש העירייה.",
        topic_label_he="בחירת סגן ראש עירייה",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    normalized = normalize_topic_subject_v3_event_payload(
        payload={
            "context_id": context.context_id,
            "target_artifact_id": artifact.artifact_id,
            "is_event": True,
            "action_type_he": "אחר",
            "other_action_type_he": "בחירה",
            "action_type_confidence": 0.91,
            "matter_he": "בחירת סגן בשכר לראש העירייה",
            "action_quote_he": "המועצה החליטה ברוב קולות לבחור חבר מועצה כסגן בשכר לראש העירייה",
            "outcome_is_decision": True,
            "outcome": {
                "outcome_type": "approved",
                "outcome_label_he": "מאושר",
                "outcome_quote_he": "מאושר - החלטה: המועצה החליטה ברוב קולות לבחור חבר מועצה כסגן בשכר לראש העירייה.",
                "confidence": 0.95,
                "limitations": [],
            },
            "target_row_role": "action_anchor",
        },
        context=context,
    )

    assert normalized["action_type_he"] == "אישור"
    assert normalized["other_action_type_he"] == ""
    assert normalized["action_type_status"] == "repaired_approval_from_formal_decision_outcome"
    assert normalized["event_phase"] == "decision_made"
    assert "repaired_approval_from_formal_decision_outcome" in normalized["semantic_repairs"]


def test_topic_subject_v3_normalizes_context_span_role_drift() -> None:
    normalized = topic_subjects_module.topic_subject_v3_normalize_context_event_payload(
        {
            "is_event": True,
            "span_roles": [
                {"span_id": "span-1", "span_role": "structural_metadata", "reason_he": "כותרת"},
                {"span_id": "span-2", "span_role": "unexpected_role", "reason_he": "לא ידוע"},
                {"span_id": "span-3", "span_role": "structural|background", "reason_he": "מעורב"},
            ],
        }
    )

    assert normalized["span_roles"][0]["span_role"] == "structural"
    assert normalized["span_roles"][1]["span_role"] == "not_relevant"
    assert normalized["span_roles"][2]["span_role"] == "background"
    assert "invalid_span_role:unexpected_role" in normalized["schema_warnings"]
    assert "composite_span_role_normalized:structural|background->background" in normalized["schema_warnings"]


def test_process_topic_subject_v3_evidence_model_error_keeps_judged_event_failed() -> None:
    artifact = _artifact_dataclass(real_text="חברי המועצה מאשרים פה אחד את הנסיעה המקצועית.", topic_label_he="נסיעות")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    class EvidenceErrorClient(MockTopicSubjectV3Client):
        def assess_event_evidence(self, *, context, normalized_event, event_payload, config):  # type: ignore[no-untyped-def]
            return {"error_code": "MODEL_INVALID_JSON", "error_text": "", "stage": "topic_subject_v3_evidence_entailment"}

    event, row = process_topic_subject_v3_context(
        context=context,
        client=EvidenceErrorClient(),
        config=TopicSubjectResearchConfig(),
    )

    assert event is not None
    assert event.validation_status == "failed"
    assert "evidence_entailment_model_error" in event.failure_reasons
    assert event.event_payload["v3_evidence_entailment"]["error_code"] == "MODEL_INVALID_JSON"
    assert row.quality_status == "failed"


def test_process_topic_subject_v3_repairs_bad_action_quote_from_source_spans_without_model_call() -> None:
    artifact = _artifact_dataclass(real_text="אבקש לאשר נסיעה מקצועית לכנס ארצי בנושא שירות לתושב.", topic_label_he="נסיעות")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    class BadQuoteClient(MockTopicSubjectV3Client):
        def extract_event(self, *, context, normalized_event, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "is_event": True,
                "action_type_he": "בקשה",
                "action_type_confidence": 0.92,
                "matter_he": "נסיעה מקצועית לכנס ארצי",
                "action_quote_he": "ציטוט שאינו מופיע במקור",
                "outcome_is_decision": False,
                "target_row_role": "action_anchor",
                "confidence": 0.9,
            }

        def repair_event_quotes(self, *, context, normalized_event, extraction_payload, quote_failures, config):  # type: ignore[no-untyped-def]
            raise AssertionError("local quote repair should avoid the model quote-repair call")

    event, row = process_topic_subject_v3_context(
        context=context,
        client=BadQuoteClient(),
        config=TopicSubjectResearchConfig(),
    )

    assert event is not None
    assert event.event_payload["action_quote_he"] == artifact.real_text
    assert event.event_payload["v3_quote_repair"]["deterministic_repair_applied"] is True
    assert row.quality_status == "accepted"


def test_process_topic_subject_v3_quote_repair_model_error_is_not_fatal_model_error() -> None:
    artifact = _artifact_dataclass(real_text="טקסט כללי ללא מילות התאמה לציטוט המבוקש.", topic_label_he="עדכון")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    class QuoteRepairErrorClient(MockTopicSubjectV3Client):
        def extract_event(self, *, context, normalized_event, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "is_event": True,
                "action_type_he": "דיווח",
                "action_type_confidence": 0.9,
                "matter_he": "foreign placeholder",
                "action_quote_he": "missing quote placeholder",
                "outcome_is_decision": False,
                "target_row_role": "action_anchor",
                "confidence": 0.9,
            }

        def repair_event_quotes(self, *, context, normalized_event, extraction_payload, quote_failures, config):  # type: ignore[no-untyped-def]
            return {"error_code": "MODEL_INVALID_JSON", "error_text": "bad quote repair json", "stage": "topic_subject_v3_quote_repair"}

    event, row = process_topic_subject_v3_context(
        context=context,
        client=QuoteRepairErrorClient(),
        config=TopicSubjectResearchConfig(),
    )

    assert event is not None
    assert row.quality_status == "failed"
    assert row.row_role != "model_error"
    assert "action_quote_not_grounded" in event.failure_reasons
    assert event.event_payload["v3_quote_repair"]["error_code"] == "MODEL_INVALID_JSON"


def test_process_topic_subject_v3_normalization_model_error_uses_fallback() -> None:
    artifact = _artifact_dataclass(real_text="חברי המועצה מאשרים פה אחד את הנסיעה המקצועית.", topic_label_he="נסיעות")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    class NormalizationErrorClient(MockTopicSubjectV3Client):
        def normalize_event(self, *, context, config):  # type: ignore[no-untyped-def]
            return {"error_code": "MODEL_INVALID_JSON", "error_text": "bad json", "stage": "topic_subject_v3_contextual_event_normalization"}

    event, row = process_topic_subject_v3_context(
        context=context,
        client=NormalizationErrorClient(),
        config=TopicSubjectResearchConfig(),
    )

    assert event is not None
    assert event.normalized_event["normalization_model_error"]["error_code"] == "MODEL_INVALID_JSON"
    assert event.event_payload["is_event"] is True
    assert row.quality_status == "accepted"


def test_topic_subject_v3_model_error_row_keeps_compact_raw_response() -> None:
    artifact = _artifact_dataclass(real_text="חברי המועצה דנו בנושא פיתוח פארק עירוני.", topic_label_he="פיתוח פארק")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    row = topic_subjects_module.topic_subject_v3_model_error_row_quality(
        context=context,
        stage="topic_subject_v3_action_subject_extraction",
        model_payload={
            "error_code": "MODEL_INVALID_JSON",
            "error_text": "bad json",
            "raw_payload": {
                "model": "dicta-test",
                "done": True,
                "done_reason": "stop",
                "message": {"content": "not json"},
                "prompt_eval_count": 123,
                "eval_count": 7,
            },
        },
    )

    debug_payload = row.metadata["model_error_payload"]
    assert debug_payload["error_code"] == "MODEL_INVALID_JSON"
    assert "raw_payload" not in debug_payload
    assert debug_payload["raw_response"]["model"] == "dicta-test"
    assert debug_payload["raw_response"]["content_excerpt"] == "not json"


def test_topic_subject_v3_low_confidence_controlled_action_becomes_other() -> None:
    artifact = _artifact_dataclass(real_text="אבקש לאשר נסיעה מקצועית לכנס ארצי.", topic_label_he="חינוך")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "בקשה",
            "action_subtype_he": "",
            "action_type_confidence": 0.62,
            "matter_he": "נסיעה מקצועית לכנס ארצי",
            "outcome_is_decision": False,
            "confidence": 0.7,
        },
        context=context,
        action_confidence_threshold=0.75,
    )

    assert event_payload["action_type_he"] == "אחר"
    assert event_payload["other_action_type_he"] == "בקשה"
    assert event_payload["action_type_status"] == "other_low_confidence"


def test_topic_subject_v3_inquiry_shape_repairs_generic_request_action() -> None:
    artifact = _artifact_dataclass(real_text="שאילתה בנושא עובדים מושאלים. עו\"ד גלבר מבקשת לקבל נתונים מכלל החברות העירוניות.", topic_label_he="שאילתות")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "בקשה",
            "action_type_confidence": 0.91,
            "matter_he": "עובדים מושאלים בחברות העירוניות",
            "outcome_is_decision": False,
            "confidence": 0.9,
        },
        context=context,
    )

    assert event_payload["action_type_he"] == "שאילתה"
    assert event_payload["event_phase"] == "inquiry_submitted"
    assert event_payload["action_type_status"] == "repaired_inquiry_from_request_shape"


def test_topic_subject_v3_transcript_question_does_not_repair_budget_request_to_inquiry() -> None:
    artifact = _artifact_dataclass(
        real_text="ראית את תקציב הגיל הרך? את רוצה להגדיל את התקציב ולהשקיע עוד בסייעות.",
        topic_label_he="תקציב הגיל הרך",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "בקשה",
            "action_type_confidence": 0.92,
            "matter_he": "הגדלת תקציב לגיל הרך והשקעה בסייעות",
            "outcome_is_decision": False,
            "confidence": 0.9,
        },
        context=context,
    )

    assert event_payload["action_type_he"] == "בקשה"
    assert event_payload["event_phase"] == "open_request"
    assert event_payload["action_type_status"] == "controlled_high_confidence"


def test_topic_subject_v3_transcript_objection_does_not_repair_budget_request_to_inquiry() -> None:
    artifact = _artifact_dataclass(
        real_text="אנחנו הבעלים של המבנה, נכון? הסתייגות: דרישה לביטול תב\"ר בסך פארק בין מגדלי היוקרה החדשים בכיכר המדינה. לוקחים מעניים ונותנים לעשירים. לא לאשר.",
        topic_label_he="ביטול תב\"ר פארק בין מגדלי היוקרה החדשים בכיכר המדינה",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "בקשה",
            "action_type_confidence": 0.92,
            "matter_he": "דרישה לביטול תוספת תקציב לפארק בכיכר המדינה",
            "outcome_is_decision": False,
            "confidence": 0.9,
        },
        context=context,
    )

    assert event_payload["action_type_he"] == "הסתייגות"
    assert event_payload["event_phase"] == "objection_submitted"
    assert event_payload["action_type_status"] == "repaired_objection_from_generic_non_decision_action"


def test_topic_subject_v3_objection_prefers_specific_supported_topic_over_broad_budget_matter() -> None:
    artifact = _artifact_dataclass(
        real_text=(
            "ההסתייגות היא קיצוץ של 300,000 שח מתקציב אחר. "
            "ההסתייגות היא בנושא \"תכנית שכונה כעיר\", שכונות התקווה, עזרא והארגזים, "
            "קיצוץ של מליון שח מתקציב התכנית לצמצום פערים."
        ),
        topic_label_he="תכנית שכונה כעיר, שכונות התקווה, עזרא והארגזים",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "אישור",
            "action_type_confidence": 0.98,
            "matter_he": "תקציב העירייה",
            "action_quote_he": "הסעיף הבא",
            "outcome_is_decision": False,
            "confidence": 0.95,
        },
        context=context,
    )

    assert event_payload["action_type_he"] == "הסתייגות"
    assert event_payload["matter_he"] == "תכנית שכונה כעיר, שכונות התקווה, עזרא והארגזים"
    assert "ההסתייגות היא בנושא" in event_payload["action_quote_he"]
    assert event_payload["event_phase"] == "objection_submitted"


def test_topic_subject_v3_explicit_objection_overrides_unsupported_rejection() -> None:
    artifact = _artifact_dataclass(
        real_text="ההסתייגות היא בדרישה לביטול תב\"ר לפארק. לא לאשר. לדרוש שינוי.",
        topic_label_he="ביטול תב\"ר לפארק",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "דחייה",
            "action_type_confidence": 0.94,
            "matter_he": "תב\"ר לפארק",
            "action_quote_he": "לא לאשר.",
            "outcome_is_decision": True,
            "outcome": {
                "outcome_type": "rejected",
                "outcome_label_he": "דחייה",
                "outcome_quote_he": "לא לאשר.",
                "confidence": 0.94,
                "limitations": [],
            },
            "target_row_role": "action_anchor",
            "confidence": 0.94,
        },
        context=context,
    )

    assert event_payload["action_type_he"] == "הסתייגות"
    assert event_payload["outcome_is_decision"] is False
    assert event_payload["outcome"] is None
    assert event_payload["event_phase"] == "objection_submitted"
    assert event_payload["action_type_status"] == "repaired_objection_from_unsupported_formal_decision_action"


def test_topic_subject_v3_historical_approval_in_objection_does_not_become_current_approval() -> None:
    artifact = _artifact_dataclass(
        real_text="ההסתייגות היא בדרישה לביטול תב\"ר במתחם. מדובר בתוספת לתב\"ר שאושר בשנה שעברה.",
        topic_label_he="ביטול תב\"ר במתחם",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "אישור",
            "action_type_confidence": 0.93,
            "matter_he": "תוספת לתב\"ר במתחם",
            "action_quote_he": "תוספת לתב\"ר שאושר בשנה שעברה",
            "outcome_is_decision": True,
            "outcome": {
                "outcome_type": "approved",
                "outcome_label_he": "אישור",
                "outcome_quote_he": "תוספת לתב\"ר שאושר בשנה שעברה",
                "confidence": 0.93,
                "limitations": [],
            },
            "target_row_role": "action_anchor",
            "confidence": 0.93,
        },
        context=context,
    )

    assert event_payload["action_type_he"] == "הסתייגות"
    assert event_payload["outcome_is_decision"] is False
    assert event_payload["outcome"] is None
    assert event_payload["event_phase"] == "objection_submitted"


def test_topic_subject_v3_reports_explicit_none_outcome_and_model_judge_label() -> None:
    artifact = _artifact_dataclass(real_text="אבקש את אישור מועצת העיר להאצלת סמכויות חתימה.", topic_label_he="סמכויות")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "בקשה",
            "action_type_confidence": 0.94,
            "matter_he": "האצלת סמכויות חתימה",
            "action_quote_he": "אבקש את אישור מועצת העיר",
            "outcome_is_decision": False,
            "target_row_role": "action_anchor",
            "confidence": 0.9,
        },
        context=context,
    )
    row = topic_subject_v3_row_quality_from_payloads(
        context=context,
        event_id="event-test",
        event_payload=event_payload,
        judge_payload={
            "judge_prediction": {"is_event": True, "action_type_he": "בקשה", "matter_he": "האצלת סמכויות חתימה", "outcome_type": "none"},
            "prediction_comparison": "same",
            "judge_status": "accepted",
            "failure_reasons": [],
            "row_quality": {"row_role": "action_anchor", "event_role": "primary", "quality_status": "accepted"},
        },
        validation_status="accepted",
        failure_reasons=[],
    )
    report = topic_subjects_module.topic_subject_v3_all_rows_report_markdown([row])

    assert "Model Judge" in report
    assert "Decision Outcome" in report
    assert "none" in report
    assert "open_request" in report


def test_topic_subject_v3_decision_quote_must_be_grounded_in_context_rows() -> None:
    artifact = _artifact_dataclass(real_text="חברי המועצה מאשרים פה אחד את הנסיעה המקצועית.", topic_label_he="חינוך")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "אישור",
            "action_type_confidence": 0.91,
            "matter_he": "נסיעה מקצועית",
            "action_quote_he": "חברי המועצה מאשרים פה אחד את הנסיעה המקצועית",
            "outcome_is_decision": True,
            "outcome": {
                "outcome_type": "approved",
                "outcome_label_he": "אישור",
                "outcome_summary_he": "אושרה הנסיעה המקצועית",
                "outcome_quote_he": "ציטוט שלא מופיע במקור",
                "confidence": 0.9,
                "limitations": [],
            },
            "confidence": 0.91,
        },
        context=context,
    )

    failures = validate_topic_subject_v3_event_payload(context=context, event_payload=event_payload)

    assert "outcome_quote_not_grounded" in failures


def test_topic_subject_v3_strips_wrapping_quote_marks_from_evidence_quotes() -> None:
    artifact = _artifact_dataclass(real_text="את רוצה להגדיל את התקציב. את חושבת שהתקציב נמוך מדי.", topic_label_he="תקציב")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "בקשה",
            "action_type_confidence": 0.91,
            "matter_he": "הגדלת התקציב",
            "action_quote_he": "\"את רוצה להגדיל את התקציב.\"",
            "action_focus_quote_he": "\"את רוצה להגדיל את התקציב.\"",
            "outcome_is_decision": False,
            "confidence": 0.91,
        },
        context=context,
    )

    assert event_payload["action_quote_he"] == "את רוצה להגדיל את התקציב."
    assert event_payload["action_focus_quote_he"] == "את רוצה להגדיל את התקציב."
    assert "action_quote_not_grounded" not in validate_topic_subject_v3_event_payload(context=context, event_payload=event_payload)


def test_topic_subject_v3_accepted_judge_non_event_reports_non_event_status() -> None:
    artifact = _artifact_dataclass(real_text="פרוטוקול מישיבת ועדת כספים שהתקיימה בתאריך 1.1.2025", topic_label_he="כספים")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    row = topic_subject_v3_row_quality_from_payloads(
        context=context,
        event_id="",
        event_payload={"is_event": False, "target_row_role": "structural_metadata"},
        judge_payload={
            "judge_prediction": {
                "is_event": False,
                "action_type_he": None,
                "matter_he": None,
                "outcome_type": "none",
                "confidence": 0.9,
                "rationale_he": "header",
            },
            "prediction_comparison": "same",
            "judge_status": "accepted",
            "ground_truth_he": "",
            "failure_reasons": [],
            "row_quality": {
                "row_role": "structural_metadata",
                "event_role": "not_part_of_event",
                "quality_status": "accepted",
                "ground_truth_he": "",
                "reason_for_failure": None,
            },
        },
        validation_status="non_event",
        failure_reasons=[],
    )

    assert row.quality_status == "non_event"
    assert row.judge_status == "non_event"
    assert row.prediction_comparison == "same"
    assert row.ground_truth_he


def test_topic_subject_v3_row_quality_normalizes_leaked_event_role_labels() -> None:
    artifact = _artifact_dataclass(real_text="חברי המועצה מאשרים פה אחד את הנסיעה המקצועית.", topic_label_he="נסיעות")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    for leaked_role in ("action_anchor", "open_request"):
        row = topic_subject_v3_row_quality_from_payloads(
            context=context,
            event_id="event-test",
            event_payload={
                "is_event": True,
                "action_type_he": "אישור",
                "matter_he": "נסיעה מקצועית",
                "outcome_is_decision": True,
                "target_row_role": "action_anchor",
            },
            judge_payload={
                "judge_prediction": {"is_event": True, "action_type_he": "אישור", "matter_he": "נסיעה מקצועית"},
                "prediction_comparison": "same",
                "judge_status": "accepted",
                "failure_reasons": [],
                "row_quality": {"row_role": "action_anchor", "event_role": leaked_role, "quality_status": "accepted"},
            },
            validation_status="accepted",
            failure_reasons=[],
        )

        assert row.event_role == "primary"
        assert row.event_role not in {"action_anchor", "open_request"}
        assert row.metadata["raw_event_role_by_judge"] == leaked_role

    non_event_row = topic_subject_v3_row_quality_from_payloads(
        context=context,
        event_id="",
        event_payload={"is_event": False, "target_row_role": "structural_metadata"},
        judge_payload={
            "judge_prediction": {"is_event": False},
            "prediction_comparison": "same",
            "judge_status": "accepted",
            "failure_reasons": [],
            "row_quality": {"row_role": "structural_metadata", "event_role": "open_request", "quality_status": "accepted"},
        },
        validation_status="non_event",
        failure_reasons=[],
    )

    assert non_event_row.event_role == "not_part_of_event"


def test_topic_subject_v3_context_normalization_recovers_composite_row_roles() -> None:
    anchor = _artifact_dataclass(real_text="אבקש לדון בתקציב השכונה.", topic_label_he="תקציב", artifact_id="artifact-anchor", source_ordinal=1)
    detail = _artifact_dataclass(real_text="פירוט רקע על התקציב.", topic_label_he="תקציב", artifact_id="artifact-detail", source_ordinal=2)
    context = build_topic_subject_v3_event_contexts(artifacts=[anchor, detail], max_context_rows=2)[0]

    normalized = topic_subjects_module.topic_subject_v3_normalize_context_event_payload(
        {
            "target_artifact_id": anchor.artifact_id,
            "is_event": True,
            "target_row_role": "action_anchor|event_title|dependent_detail",
            "span_roles": [],
            "row_roles": [
                {"artifact_id": anchor.artifact_id, "row_role": "action_anchor|event_title|dependent_detail", "role_reason_he": "copied schema"},
                {
                    "artifact_id": detail.artifact_id,
                    "row_role": "action_anchor|event_title|dependent_detail|decision_result|vote_metadata|document_fragment|structural_metadata|duplicate_reference|insufficient_context",
                    "role_reason_he": "copied schema",
                },
            ],
        },
        context=context,
    )

    assert normalized["target_row_role"] == "insufficient_context"
    assert normalized["row_roles"][0]["row_role"] == "insufficient_context"
    assert normalized["row_roles"][1]["row_role"] == "dependent_detail"
    assert any("row_role_normalized" in warning for warning in normalized["schema_warnings"])


def test_topic_subject_v3_context_uses_full_source_inventory_for_selected_target() -> None:
    target = _artifact_dataclass(
        real_text="סעיף21 : מינוי נציג ציבור והארכת כהונה החלטות",
        topic_label_he="מינוי נציג ציבור והארכת כהונה",
        artifact_id="artifact-heading",
        source_ordinal=63,
        metadata={"artifact_metadata": {"structure_metadata": {"structural_role": "section_heading", "section_id": "section-21"}}},
    )
    vote_result = _artifact_dataclass(
        real_text="חברי המועצה מאשרים פה אחד את מינוי נציג הציבור והארכת הכהונה 152296 08/01/2025",
        topic_label_he="vote_or_result",
        artifact_id="artifact-vote-result",
        source_ordinal=64,
        metadata={
            "artifact_metadata": {
                "structure_metadata": {"structural_role": "vote_or_result", "section_id": "section-21"},
                "topic_assignment": {"is_topic_bearing": False, "row_type": "vote_or_result"},
            }
        },
    )

    context = build_topic_subject_v3_event_contexts(
        artifacts=[target],
        context_artifacts=[target, vote_result],
        max_context_rows=5,
    )[0]
    source_context = topic_subjects_module.topic_subject_v3_source_context(context=context, max_text_chars=1000)

    assert [row.artifact_id for row in context.rows] == ["artifact-heading", "artifact-vote-result"]
    assert source_context["nearby_rows"][0]["artifact_id"] == "artifact-vote-result"
    assert "מאשרים פה אחד" in source_context["nearby_rows"][0]["raw_text"]
    assert topic_subjects_module.topic_subject_v3_quote_supported(context=context, quote="מאשרים פה אחד את מינוי נציג הציבור")


def test_topic_subject_v3_context_window_uses_context_artifacts_for_selected_target() -> None:
    previous_row = _artifact_dataclass(
        real_text="רקע קודם: חברי המועצה שאלו על נוהל תווי חניה.",
        topic_label_he="חניה",
        artifact_id="artifact-prev-row",
        source_ordinal=40,
    )
    target = _artifact_dataclass(
        real_text="שאילתה: חלוקת תווי חניה בניגוד לנהלים עירוניים",
        topic_label_he="תווי חניה",
        artifact_id="artifact-target-row",
        source_ordinal=41,
    )
    next_row = _artifact_dataclass(
        real_text="תשובה: ראש העיר הסביר שהנושא ייבדק מול מנהלת אגף החניה.",
        topic_label_he="מענה לשאילתה",
        artifact_id="artifact-next-row",
        source_ordinal=42,
    )

    context = build_topic_subject_v3_event_contexts(
        artifacts=[target],
        context_artifacts=[previous_row, target, next_row],
        max_context_rows=5,
    )[0]
    source_context = topic_subjects_module.topic_subject_v3_source_context(context=context, max_text_chars=1000)

    assert [row.artifact_id for row in context.rows] == ["artifact-prev-row", "artifact-target-row", "artifact-next-row"]
    assert [row["artifact_id"] for row in source_context["nearby_rows"]] == ["artifact-prev-row", "artifact-next-row"]
    assert "רקע קודם" in source_context["nearby_rows"][0]["raw_text"]
    assert "תשובה" in source_context["nearby_rows"][1]["raw_text"]


def test_topic_subject_v3_context_includes_attached_raw_neighbor_rows() -> None:
    target = _artifact_dataclass(
        real_text="סעיף21 : מינוי נציג ציבור והארכת כהונה החלטות",
        topic_label_he="מינוי נציג ציבור והארכת כהונה",
        artifact_id="artifact-heading",
        source_ordinal=63,
    )
    target.neighbor_contexts = [
        {
            "relation": "next_protocol_row",
            "artifact_id": "artifact-raw-next",
            "ordinal": 64,
            "page_span": {"start": 9, "end": 9},
            "header_path": ["תחבורה ובטיחות"],
            "raw_text": "חברי המועצה מאשרים פה אחד את מינוי נציג הציבור והארכת הכהונה 152296 08/01/2025",
            "decision_context_text": "חברי המועצה מאשרים פה אחד את מינוי נציג הציבור והארכת הכהונה",
        }
    ]

    context = build_topic_subject_v3_event_contexts(artifacts=[target], max_context_rows=5)[0]
    source_context = topic_subjects_module.topic_subject_v3_source_context(context=context, max_text_chars=1000)

    assert [row.artifact_id for row in context.rows] == ["artifact-heading", "artifact-raw-next"]
    assert source_context["nearby_rows"][0]["source_kind"] == "pdf_first_v4_protocol_neighbor"
    assert "מאשרים פה אחד" in source_context["nearby_rows"][0]["raw_text"]
    assert topic_subjects_module.topic_subject_v3_quote_supported(context=context, quote="מאשרים פה אחד את מינוי נציג הציבור")


def test_topic_subject_v3_repairs_decision_outcome_from_linked_result_row() -> None:
    target = _artifact_dataclass(
        real_text="סעיף21 : מינוי נציג ציבור והארכת כהונה החלטות",
        topic_label_he="מינוי נציג ציבור והארכת כהונה",
        artifact_id="artifact-heading",
        source_ordinal=63,
        metadata={"artifact_metadata": {"structure_metadata": {"structural_role": "section_heading", "section_id": "section-21"}}},
    )
    vote_result = _artifact_dataclass(
        real_text="חברי המועצה מאשרים פה אחד את מינוי נציג הציבור והארכת הכהונה 152296 08/01/2025",
        topic_label_he="vote_or_result",
        artifact_id="artifact-vote-result",
        source_ordinal=64,
        metadata={
            "artifact_metadata": {
                "structure_metadata": {"structural_role": "vote_or_result", "section_id": "section-21"},
                "topic_assignment": {"is_topic_bearing": False, "row_type": "vote_or_result"},
            }
        },
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[target], context_artifacts=[target, vote_result])[0]

    repaired = topic_subjects_module.topic_subject_v3_repair_linked_decision_outcome_locally(
        context=context,
        event_payload={
            "context_id": context.context_id,
            "target_artifact_id": target.artifact_id,
            "is_event": True,
            "action_type_he": "אחר",
            "other_action_type_he": "מינוי",
            "action_type_confidence": 0.8,
            "matter_he": "מינוי נציג ציבור והארכת כהונה",
            "action_details_he": "מינוי נציג ציבור והארכת כהונה",
            "action_quote_he": "מינוי נציג ציבור",
            "outcome_is_decision": False,
            "outcome": None,
            "target_row_role": "action_anchor",
        },
    )
    normalized = normalize_topic_subject_v3_event_payload(payload=repaired, context=context)

    assert normalized["action_type_he"] == "אישור"
    assert normalized["outcome_is_decision"] is True
    assert normalized["outcome"]["outcome_type"] == "approved"
    assert "מאשרים פה אחד" in normalized["outcome"]["outcome_quote_he"]
    assert normalized["v3_linked_outcome_local_repair"]["source_ordinal"] == 64
    assert topic_subjects_module.topic_subject_v3_quote_supported(context=context, quote=normalized["outcome"]["outcome_quote_he"])


def test_process_topic_subject_v3_applies_linked_outcome_repair_before_evidence() -> None:
    target = _artifact_dataclass(
        real_text="סעיף21 : מינוי נציג ציבור והארכת כהונה החלטות",
        topic_label_he="מינוי נציג ציבור והארכת כהונה",
        artifact_id="artifact-heading",
        source_ordinal=63,
        metadata={"artifact_metadata": {"structure_metadata": {"structural_role": "section_heading", "section_id": "section-21"}}},
    )
    vote_result = _artifact_dataclass(
        real_text="חברי המועצה מאשרים פה אחד את מינוי נציג הציבור והארכת הכהונה 152296 08/01/2025",
        topic_label_he="vote_or_result",
        artifact_id="artifact-vote-result",
        source_ordinal=64,
        metadata={
            "artifact_metadata": {
                "structure_metadata": {"structural_role": "vote_or_result", "section_id": "section-21"},
                "topic_assignment": {"is_topic_bearing": False, "row_type": "vote_or_result"},
            }
        },
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[target], context_artifacts=[target, vote_result])[0]
    evidence_payloads: list[dict[str, object]] = []

    class LinkedOutcomeClient(MockTopicSubjectV3Client):
        def extract_event(self, *, context, normalized_event, config):  # type: ignore[no-untyped-def]
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "is_event": True,
                "action_type_he": "בקשה",
                "action_type_confidence": 0.9,
                "matter_he": "מינוי נציג ציבור והארכת כהונה",
                "action_details_he": "מינוי נציג ציבור והארכת כהונה",
                "action_quote_he": "מינוי נציג ציבור",
                "outcome_is_decision": False,
                "outcome": None,
                "target_row_role": "action_anchor",
                "confidence": 0.9,
            }

        def assess_event_evidence(self, *, context, normalized_event, event_payload, config):  # type: ignore[no-untyped-def]
            evidence_payloads.append(dict(event_payload))
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "entailment_status": "entailed",
                "repair_required": False,
                "repaired_event": None,
                "field_assessments": {
                    "action_type_he": {"status": "entailed", "source_quote_he": event_payload["action_quote_he"], "rationale_he": "linked result row"},
                    "matter_he": {"status": "entailed", "source_quote_he": "מינוי נציג הציבור", "rationale_he": "same matter"},
                    "outcome": {"status": "entailed", "source_quote_he": event_payload["outcome"]["outcome_quote_he"], "rationale_he": "formal result"},
                },
                "failure_reasons": [],
                "rationale_he": "linked outcome is grounded",
            }

    event, row = process_topic_subject_v3_context(
        context=context,
        client=LinkedOutcomeClient(),
        config=TopicSubjectResearchConfig(),
    )

    assert event is not None
    assert evidence_payloads
    assert evidence_payloads[0]["outcome_is_decision"] is True
    assert event.event_payload["action_type_he"] == "אישור"
    assert event.event_payload["outcome"]["outcome_type"] == "approved"
    assert event.event_payload["v3_linked_outcome_local_repair"]["source_ordinal"] == 64
    assert row.quality_status == "accepted"


def test_topic_subject_v3_linked_outcome_repair_requires_identity_overlap() -> None:
    target = _artifact_dataclass(
        real_text="סעיף5 : תקציב גינון שכונתי",
        topic_label_he="תקציב גינון שכונתי",
        artifact_id="artifact-heading",
        source_ordinal=10,
        metadata={"artifact_metadata": {"structure_metadata": {"structural_role": "section_heading", "section_id": "section-5"}}},
    )
    unrelated_vote = _artifact_dataclass(
        real_text="חברי המועצה מאשרים פה אחד את נסיעת סגן ראש העיר לכנס מקצועי",
        topic_label_he="vote_or_result",
        artifact_id="artifact-unrelated-vote",
        source_ordinal=11,
        metadata={
            "artifact_metadata": {
                "structure_metadata": {"structural_role": "vote_or_result", "section_id": "section-5"},
                "topic_assignment": {"is_topic_bearing": False, "row_type": "vote_or_result"},
            }
        },
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[target], context_artifacts=[target, unrelated_vote])[0]

    repaired = topic_subjects_module.topic_subject_v3_repair_linked_decision_outcome_locally(
        context=context,
        event_payload={
            "context_id": context.context_id,
            "target_artifact_id": target.artifact_id,
            "is_event": True,
            "action_type_he": "בקשה",
            "action_type_confidence": 0.8,
            "matter_he": "תקציב גינון שכונתי",
            "action_details_he": "תקציב גינון שכונתי",
            "action_quote_he": "תקציב גינון שכונתי",
            "outcome_is_decision": False,
            "outcome": None,
            "target_row_role": "action_anchor",
        },
    )

    assert repaired["outcome_is_decision"] is False
    assert "v3_linked_outcome_local_repair" not in repaired


def test_topic_subject_v3_infers_missing_is_event_from_normalized_event() -> None:
    artifact = _artifact_dataclass(
        real_text="הצעה לסדר, לדון בוועדת הביטחון בראשות סגן ראש העיר סקר.",
        topic_label_he="ועדת הביטחון",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    repaired = topic_subjects_module.topic_subject_v3_apply_normalized_event_defaults(
        extraction_payload={
            "action_type_he": "דיון",
            "action_type_confidence": 0.9,
            "matter_he": "דיון בוועדת הביטחון בראשות סגן ראש העיר סקר",
        },
        normalized_event={"is_event": True, "target_row_role": "action_anchor"},
        context=context,
    )
    normalized = normalize_topic_subject_v3_event_payload(payload=repaired, context=context)

    assert normalized["is_event"] is True
    assert normalized["action_type_he"] == "הצעה לסדר יום"
    assert normalized["matter_he"] == "דיון בוועדת הביטחון בראשות סגן ראש העיר סקר"


def test_topic_subject_v3_infers_missing_is_event_from_grounded_extraction() -> None:
    artifact = _artifact_dataclass(
        real_text="ניתנה הנחייה לבחון הקמת יחידה ייעודית לטיפול בשוהי רחוב.",
        topic_label_he="שוהי רחוב",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    repaired = topic_subjects_module.topic_subject_v3_apply_normalized_event_defaults(
        extraction_payload={
            "action_type_he": "הנחיה",
            "action_type_confidence": 0.92,
            "matter_he": "הקמת יחידה ייעודית לטיפול בשוהי רחוב",
            "action_quote_he": "ניתנה הנחייה לבחון הקמת יחידה ייעודית לטיפול בשוהי רחוב.",
        },
        normalized_event={"is_event": False},
        context=context,
    )
    normalized = normalize_topic_subject_v3_event_payload(payload=repaired, context=context)

    assert normalized["is_event"] is True
    assert normalized["action_type_he"] == "הנחיה"
    assert normalized["target_row_role"] == "action_anchor"


def test_topic_subject_v3_repairs_same_row_removed_outcome() -> None:
    artifact = _artifact_dataclass(
        real_text="סעיף8: טיפול בתשתיות ופיצוי. הנושא ירד מסדר היום בשלב זה.",
        topic_label_he="טיפול בתשתיות ופיצוי",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    repaired = topic_subjects_module.topic_subject_v3_repair_same_row_decision_outcome_locally(
        context=context,
        event_payload={
            "is_event": True,
            "action_type_he": "אחר",
            "action_type_confidence": 0.7,
            "matter_he": "טיפול בתשתיות ופיצוי",
            "outcome_is_decision": False,
            "target_row_role": "action_anchor",
        },
    )
    normalized = normalize_topic_subject_v3_event_payload(payload=repaired, context=context)

    assert normalized["action_type_he"] == "הסרה מסדר היום"
    assert normalized["outcome_is_decision"] is True
    assert normalized["outcome"]["outcome_type"] == "removed"
    assert normalized["v3_same_row_outcome_local_repair"]["repair_applied"] is True


def test_topic_subject_v3_removed_agenda_proposal_preserves_action_and_matter() -> None:
    matter = "תו חניה לתושבים הגרים בסמוך למרכזים מסחריים"
    artifact = _artifact_dataclass(
        real_text=f"ההצעה לסדר- {matter} יורדת מסדר היום",
        topic_label_he="חניה",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    repaired = topic_subjects_module.topic_subject_v3_repair_same_row_decision_outcome_locally(
        context=context,
        event_payload={
            "is_event": True,
            "action_type_he": "הצעה לסדר יום",
            "action_type_confidence": 0.92,
            "matter_he": matter,
            "action_quote_he": "ההצעה לסדר",
            "outcome_is_decision": False,
            "target_row_role": "action_anchor",
        },
    )
    normalized = normalize_topic_subject_v3_event_payload(payload=repaired, context=context)

    assert normalized["action_type_he"] == "הצעה לסדר יום"
    assert normalized["matter_he"] == matter
    assert normalized["outcome_is_decision"] is True
    assert normalized["outcome"]["outcome_type"] == "removed"


def test_topic_subject_v3_removed_agenda_proposal_repairs_model_rejection_action() -> None:
    matter = "תו חניה לתושבים הגרים בסמוך למרכזים מסחריים"
    artifact = _artifact_dataclass(
        real_text=f"ההצעה לסדר- {matter} בקשתה של חברת מועצה. יורדת מסדר היום.",
        topic_label_he="חניה",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    normalized = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "דחייה",
            "action_type_confidence": 0.86,
            "matter_he": matter,
            "action_quote_he": "יורדת מסדר היום.",
            "outcome_is_decision": True,
            "outcome": {
                "outcome_type": "removed",
                "outcome_label_he": "הסרה מסדר היום",
                "outcome_quote_he": "יורדת מסדר היום.",
                "confidence": 0.94,
                "limitations": [],
            },
            "target_row_role": "action_anchor",
        },
        context=context,
    )

    assert normalized["action_type_he"] == "הצעה לסדר יום"
    assert "הצעה לסדר" in normalized["action_quote_he"]
    assert normalized["outcome"]["outcome_type"] == "removed"


def test_topic_subject_v3_weak_referral_proposal_does_not_create_outcome() -> None:
    text = "אנחנו מציעים להעביר, בהסכמה מלאה, את הדיון הזה לוועדת החינוך. אפשר להעביר את ההחלטה הזו פה אחד."
    artifact = _artifact_dataclass(real_text=text, topic_label_he="נוער מבקשי מקלט")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    repaired = topic_subjects_module.topic_subject_v3_repair_same_row_decision_outcome_locally(
        context=context,
        event_payload={
            "is_event": True,
            "action_type_he": "בקשה",
            "action_type_confidence": 0.9,
            "matter_he": "דיון בנושא נוער מבקשי מקלט",
            "outcome_is_decision": False,
            "target_row_role": "action_anchor",
        },
    )
    normalized = normalize_topic_subject_v3_event_payload(payload=repaired, context=context)

    assert normalized["action_type_he"] == "בקשה"
    assert normalized["outcome_is_decision"] is False
    assert normalized["outcome"] is None


def test_topic_subject_v3_request_outcome_repair_restores_target_action_quote() -> None:
    text = "אנחנו מציעים ,להעביר, בהסכמה מלאה, את הדיון הזה לוועדת החינוך. אפשר להעביר את ההחלטה הזו פה אחד."
    artifact = _artifact_dataclass(real_text=text, topic_label_he="נוער מבקשי מקלט")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    repaired = topic_subjects_module.repair_topic_subject_v3_request_outcome_payload(
        context=context,
        event_payload={
            "is_event": True,
            "action_type_he": "אישור",
            "action_type_confidence": 0.9,
            "matter_he": "העברת הדיון לוועדות",
            "action_quote_he": "החלטה: הוחלט פה אחד להעביר את ההצעה לוועדות",
            "outcome_is_decision": True,
            "outcome": {
                "outcome_type": "approved",
                "outcome_quote_he": "אפשר להעביר את ההחלטה הזו פה אחד",
                "confidence": 0.9,
                "limitations": [],
            },
        },
    )

    assert repaired["outcome_is_decision"] is False
    assert repaired["action_type_he"] == "בקשה"
    assert "אנחנו מציעים" in repaired["action_quote_he"]


def test_topic_subject_v3_explicit_directive_overrides_weak_proposal_in_mixed_row() -> None:
    text = (
        "יחד עם זאת, מוצע לשנות ההוראות בנוהל המכרזים. "
        "מהדו\"ח בנוגע לטיפול בדרי רחוב ניכרת עשייה משמעותית. "
        "ניתנה הנחייה לבחון הקמת יחידה ייעודית לטיפול בשוהי רחוב."
    )
    artifact = _artifact_dataclass(real_text=text, topic_label_he="ביקורת עירונית")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    normalized = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "בקשה",
            "action_type_confidence": 0.82,
            "matter_he": "נוהל מכרזים",
            "action_quote_he": "מוצע לשנות ההוראות בנוהל המכרזים",
            "outcome_is_decision": False,
            "target_row_role": "action_anchor",
        },
        context=context,
    )

    assert normalized["action_type_he"] == "הנחיה"
    assert "ניתנה הנחייה" in normalized["action_quote_he"]
    assert normalized["matter_he"] == "בחינת הקמת יחידה ייעודית לטיפול בשוהי רחוב"


def test_topic_subject_v3_strong_same_row_referral_still_creates_referred_outcome() -> None:
    artifact = _artifact_dataclass(
        real_text="הצעה לסדר בנושא קריאת רחוב על שמו של זאב רווח. ההצעה עוברת לדיון בוועדת שמות.",
        topic_label_he="שמות והנצחה",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    repaired = topic_subjects_module.topic_subject_v3_repair_same_row_decision_outcome_locally(
        context=context,
        event_payload={
            "is_event": True,
            "action_type_he": "בקשה",
            "action_type_confidence": 0.83,
            "matter_he": "קריאת רחוב על שמו של זאב רווח",
            "outcome_is_decision": False,
            "target_row_role": "action_anchor",
        },
    )
    normalized = normalize_topic_subject_v3_event_payload(payload=repaired, context=context)

    assert normalized["action_type_he"] == "הפניה לוועדה"
    assert normalized["outcome_is_decision"] is True
    assert normalized["outcome"]["outcome_type"] == "referred"


def test_topic_subject_v3_context_normalization_recovers_role_and_status_aliases() -> None:
    artifact = _artifact_dataclass(real_text="רקע כללי על הדיון.", topic_label_he="דיון", artifact_id="artifact-alias", source_ordinal=1)
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact], max_context_rows=1)[0]

    normalized = topic_subjects_module.topic_subject_v3_normalize_context_event_payload(
        {
            "target_artifact_id": artifact.artifact_id,
            "is_event": False,
            "target_row_role": "body",
            "event_status": "non_event",
            "span_roles": [],
            "row_roles": [{"artifact_id": artifact.artifact_id, "row_role": "target", "role_reason_he": "model alias"}],
        },
        context=context,
    )

    assert normalized["target_row_role"] == "insufficient_context"
    assert normalized["event_status"] == "not_event"
    assert normalized["row_roles"][0]["row_role"] == "insufficient_context"
    assert any("event_status_normalized:non_event->not_event" == warning for warning in normalized["schema_warnings"])


def test_topic_subject_v3_event_payload_normalizes_composite_row_roles() -> None:
    anchor = _artifact_dataclass(real_text="אבקש לדון בתקציב השכונה.", topic_label_he="תקציב", artifact_id="artifact-anchor", source_ordinal=1)
    detail = _artifact_dataclass(real_text="פירוט רקע על התקציב.", topic_label_he="תקציב", artifact_id="artifact-detail", source_ordinal=2)
    context = build_topic_subject_v3_event_contexts(artifacts=[anchor, detail], max_context_rows=2)[0]

    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "בקשה",
            "action_type_confidence": 0.9,
            "matter_he": "תקציב השכונה",
            "action_quote_he": "אבקש לדון בתקציב השכונה",
            "outcome_is_decision": False,
            "target_row_role": "action_anchor|event_title|dependent_detail",
            "row_roles": [
                {"artifact_id": anchor.artifact_id, "row_role": "action_anchor|event_title|dependent_detail", "event_role": "primary", "reason_he": "copied schema"},
                {"artifact_id": detail.artifact_id, "row_role": "action_anchor|event_title|dependent_detail", "event_role": "supporting", "reason_he": "copied schema"},
            ],
            "confidence": 0.9,
        },
        context=context,
    )

    assert event_payload["target_row_role"] == "action_anchor"
    assert event_payload["row_roles"][0]["row_role"] == "action_anchor"
    assert event_payload["row_roles"][1]["row_role"] == "dependent_detail"
    assert any("row_role_normalized" in warning for warning in event_payload["schema_warnings"])


def test_topic_subject_v3_evidence_status_accepts_partial_alias() -> None:
    normalized = topic_subjects_module.topic_subject_v3_normalize_evidence_assessment(
        {
            "entailment_status": "partial",
            "field_assessments": {},
            "repair_required": False,
            "failure_reasons": [],
        }
    )

    assert normalized["entailment_status"] == "partially_entailed"
    assert "invalid_entailment_status:partial" not in normalized["schema_warnings"]


def test_topic_subject_v3_evidence_status_recovers_nested_field_payload() -> None:
    normalized = topic_subjects_module.topic_subject_v3_normalize_evidence_assessment(
        {
            "entailment_status": {
                "action_type_he": {"status": "entailed", "source_quote_he": "הצעה לסדר"},
                "matter_he": {"status": "entailed", "source_quote_he": "תו חניה"},
                "outcome": {"status": "not_applicable", "source_quote_he": None},
            },
            "repair_required": False,
            "failure_reasons": [],
        }
    )

    assert normalized["entailment_status"] == "entailed"
    assert normalized["field_assessments"]["action_type_he"]["status"] == "entailed"
    assert normalized["field_assessments"]["outcome"]["status"] == "not_applicable"
    assert "entailment_status_recovered_from_field_assessments" in normalized["schema_warnings"]


def test_topic_subject_v3_row_quality_normalizes_recoverable_judge_enum_drift() -> None:
    artifact = _artifact_dataclass(real_text="אבקש לדון בתקציב השכונה.", topic_label_he="תקציב")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    row = topic_subject_v3_row_quality_from_payloads(
        context=context,
        event_id="event-test",
        event_payload={
            "is_event": True,
            "action_type_he": "בקשה",
            "matter_he": "תקציב השכונה",
            "outcome_is_decision": False,
            "target_row_role": "action_anchor",
        },
        judge_payload={
            "judge_prediction": {"is_event": True, "action_type_he": "בקשה", "matter_he": "תקציב השכונה", "outcome_type": "none"},
            "prediction_comparison": {"action_type_he": "same", "matter_he": "same", "outcome_type": "different"},
            "judge_status": "accepted",
            "failure_reasons": [],
            "row_quality": {
                "row_role": "action_anchor|event_title|dependent_detail",
                "event_role": "primary",
                "quality_status": "good",
                "ground_truth_he": "unit_test",
                "reason_for_failure": "",
            },
        },
        validation_status="accepted",
        failure_reasons=[],
    )

    assert row.quality_status == "accepted"
    assert row.row_role == "action_anchor"
    assert row.prediction_comparison == "partially_different"
    assert row.metadata["raw_quality_status_by_judge"] == "good"


def test_topic_subject_v3_event_output_includes_full_source_rows() -> None:
    anchor = _artifact_dataclass(
        artifact_id="artifact-v3-anchor",
        real_text="ביום 05/03/2025 חברי המועצה מאשרים פה אחד את הנסיעה המקצועית.",
        topic_label_he="נסיעות",
        source_ordinal=1,
        metadata={
            "artifact_metadata": {
                "source_paths": {
                    "protocol_run_dir": "/tmp/protocol-run",
                    "topic_assignment_run_dir": "/tmp/topic-run",
                }
            }
        },
    )
    detail = _artifact_dataclass(
        artifact_id="artifact-v3-detail",
        real_text="הנסיעה מיועדת לכנס מקצועי בתחום החינוך.",
        topic_label_he="חינוך",
        source_ordinal=2,
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[anchor, detail], max_context_rows=5)[0]

    event, _row = process_topic_subject_v3_context(
        context=context,
        client=MockTopicSubjectV3Client(),
        config=TopicSubjectResearchConfig(),
    )

    assert event is not None
    payload = topic_subject_v3_event_to_dict(event)
    assert payload["anchor_source_text_he"] == anchor.real_text
    assert anchor.real_text in payload["full_source_text_he"]
    assert detail.real_text in payload["full_source_text_he"]
    assert payload["event_source_rows"][0]["full_source_text_he"] == anchor.real_text
    assert payload["source_provenance"]["protocol_run_dir"] == "/tmp/protocol-run"
    assert payload["anchor_raw_text_before_cleaning_he"] == anchor.real_text
    assert payload["raw_date_mentions"][0]["raw_text"] == "05/03/2025"
    assert payload["primary_time"]["start"] == "2025-03-05"
    assert payload["primary_time"]["kind"] == "explicit_text_date"
    assert payload["event_source_rows"][0]["raw_text_before_cleaning_he"] == anchor.real_text
    assert payload["event_payload"]["action_type_he"] == "אישור"
    assert payload["event_payload"]["matter_he"]
    assert payload["event_payload"]["outcome_is_decision"] is True

    quality_payload = topic_subjects_module.topic_subject_v3_row_quality_to_dict(event.row_quality)
    assert quality_payload["source_provenance"]["protocol_run_dir"] == "/tmp/protocol-run"
    assert quality_payload["raw_date_mentions"][0]["raw_text"] == "05/03/2025"
    assert quality_payload["primary_time"]["start"] == "2025-03-05"
    assert quality_payload["raw_text_before_cleaning_he"] == anchor.real_text


def test_topic_subject_v3_primary_time_uses_protocol_path_fallback() -> None:
    artifact = _artifact_dataclass(
        real_text="דיון בנושא תחזוקת מדרכות ברחוב הרצל ללא תאריך בגוף הטקסט.",
        topic_label_he="תשתיות",
        metadata={
            "artifact_metadata": {
                "source_paths": {
                    "protocol_run_dir": "/tmp/protocol_31_20251229_2cdaf368_v1",
                }
            }
        },
    )

    primary_time = topic_subjects_module.topic_subject_v3_primary_time(
        event_payload={"action_type_he": "דיון", "matter_he": "תחזוקת מדרכות"},
        artifact=artifact,
    )

    assert primary_time["start"] == "2025-12-29"
    assert primary_time["kind"] == "protocol_date"
    assert primary_time["date_source"] == "protocol_date_context"
    assert primary_time["is_protocol_fallback"] is True


def test_topic_subject_v3_primary_time_ignores_processing_run_dates_before_document_date() -> None:
    artifact = _artifact_dataclass(
        real_text="ישיבת מועצה– מספר75/2023 ( מתאריך י\"ד באלול תשפ\"ג31.8.",
        topic_label_he="מנהל עירוני",
        source_title="step4_v4_global_topic_assignment_dicta_contextual_v50_subject_status_tail_broad_20260621",
        source_url=(
            "/Users/igor/Desktop/projects/Municipality/rag_eval/runs/jerusalem_pdf_first_v4_shadow/"
            "smoke_meeting75_page52_fix3_20260616/"
            "jerusalem_council_16_meeting_75_2023-08-31_protocol_9c1b98d985/"
            "pdf_first_pipeline/outputs/step4_v4_global_topic_assignment/topic_assignments.json"
        ),
    )

    primary_time = topic_subjects_module.topic_subject_v3_primary_time(
        event_payload={"action_type_he": "דיון", "matter_he": "תווי חניה"},
        artifact=artifact,
    )

    assert primary_time["start"] == "2023-08-31"
    assert primary_time["source_scope"] == "source_url"
    assert "2023-08-31" in primary_time["raw_text"]
    assert "20260616" not in primary_time["raw_text"]


def test_topic_subject_v3_primary_time_ignores_lone_processing_run_dates() -> None:
    artifact = _artifact_dataclass(
        real_text="שאילתה בנושא תיקון מעלית ללא תאריך מלא בגוף הטקסט.",
        topic_label_he="תשתיות",
        source_title="step4_v4_global_topic_assignment_auto_20260621",
        source_url=(
            "/Users/igor/Desktop/projects/Municipality/rag_eval/runs/ashdod_pdf_first_v4_shadow/"
            "all_protocols_generic_fix_sweep_v1_20260613/"
            "ashdod_2025_regular_3_short_pdf/step4_v4_global_topic_assignment_auto/topic_assignments.json"
        ),
    )

    primary_time = topic_subjects_module.topic_subject_v3_primary_time(
        event_payload={"action_type_he": "שאילתה", "matter_he": "תיקון מעלית"},
        artifact=artifact,
    )

    assert primary_time is None


def test_topic_subject_v3_primary_time_resolves_relative_mentions_to_protocol_date() -> None:
    artifact = _artifact_dataclass(
        real_text="לאחרונה התקבלו פניות בנושא תחזוקת מדרכות ברחוב הרצל.",
        topic_label_he="תשתיות",
        metadata={
            "artifact_metadata": {
                "source_paths": {
                    "protocol_run_dir": "/tmp/protocol_31_20251229_2cdaf368_v1",
                }
            }
        },
    )

    primary_time = topic_subjects_module.topic_subject_v3_primary_time(
        event_payload={"action_type_he": "דיון", "matter_he": "תחזוקת מדרכות", "action_quote_he": "לאחרונה התקבלו פניות"},
        artifact=artifact,
    )

    assert primary_time["start"] == "2025-12-29"
    assert primary_time["kind"] == "relative_to_protocol_date"
    assert primary_time["date_source"] == "relative_mention_resolved_to_protocol_date"
    assert primary_time["relative_kind"] == "recently"


def test_topic_subject_v3_primary_time_prefers_explicit_date_over_relative_mention() -> None:
    artifact = _artifact_dataclass(
        real_text="לאחרונה, ביום 05/03/2025, נדונה תחזוקת מדרכות ברחוב הרצל.",
        topic_label_he="תשתיות",
        metadata={
            "artifact_metadata": {
                "source_paths": {
                    "protocol_run_dir": "/tmp/protocol_31_20251229_2cdaf368_v1",
                }
            }
        },
    )

    primary_time = topic_subjects_module.topic_subject_v3_primary_time(
        event_payload={"action_type_he": "דיון", "matter_he": "תחזוקת מדרכות"},
        artifact=artifact,
    )

    assert primary_time["start"] == "2025-03-05"
    assert primary_time["kind"] == "explicit_text_date"


def test_topic_subject_v3_event_source_rows_normalize_role_leaks() -> None:
    anchor = _artifact_dataclass(
        artifact_id="artifact-v3-anchor",
        real_text="חברי המועצה מאשרים פה אחד את הנסיעה המקצועית.",
        topic_label_he="נסיעות",
        source_ordinal=1,
    )
    detail = _artifact_dataclass(
        artifact_id="artifact-v3-detail",
        real_text="הנסיעה מיועדת לכנס מקצועי בתחום החינוך.",
        topic_label_he="חינוך",
        source_ordinal=2,
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[anchor, detail], max_context_rows=5)[0]
    event = _v3_event_result(
        context=context,
        action_type="אישור",
        matter="נסיעה מקצועית",
        confidence=0.9,
        outcome_quote="חברי המועצה מאשרים פה אחד את הנסיעה המקצועית.",
    )
    event.event_payload["row_roles"] = [
        {"artifact_id": anchor.artifact_id, "row_role": "action_anchor", "event_role": "action_anchor"},
        {"artifact_id": detail.artifact_id, "row_role": "dependent_detail", "event_role": "open_request"},
    ]

    rows = topic_subjects_module.topic_subject_v3_event_source_rows(event)
    roles_by_artifact = {row["artifact_id"]: row["event_role"] for row in rows}

    assert roles_by_artifact[anchor.artifact_id] == "primary"
    assert roles_by_artifact[detail.artifact_id] == "supporting"
    assert all(event_role not in {"action_anchor", "open_request"} for event_role in roles_by_artifact.values())


def test_topic_subject_v3_consolidates_duplicate_decision_and_supporting_request() -> None:
    request = _artifact_dataclass(
        artifact_id="artifact-v3-request",
        real_text="אבקש לאשר השתתפות בסמינר חדשנות של מנהל חינוך בארהב.",
        topic_label_he="חינוך",
        source_ordinal=1,
    )
    approval = _artifact_dataclass(
        artifact_id="artifact-v3-approval",
        real_text="חברי המועצה מאשרים פה אחד השתתפות בסמינר מקצועי ללמידה חדשנית בארהב.",
        topic_label_he="נסיעות",
        source_ordinal=2,
    )
    duplicate = _artifact_dataclass(
        artifact_id="artifact-v3-duplicate",
        real_text="החלטה בנושא הסמינר המקצועי ללמידה חדשנית בארהב.",
        topic_label_he="חינוך",
        source_ordinal=3,
    )
    contexts = {context.target_artifact.artifact_id: context for context in build_topic_subject_v3_event_contexts(artifacts=[request, approval, duplicate], max_context_rows=5)}
    decision_quote = approval.real_text
    events = [
        _v3_event_result(
            context=contexts[request.artifact_id],
            action_type="אישור",
            matter="נסיעתה של מנהלת קשרי חוץ להשתתפות בסמינר חדשנות למנהלי חינוך בארהב",
            confidence=0.93,
            outcome_quote=request.real_text,
        ),
        _v3_event_result(
            context=contexts[approval.artifact_id],
            action_type="אישור",
            matter="השתתפות בסמינר מקצועי ללמידה חדשנית בארהב",
            confidence=0.91,
            outcome_quote=decision_quote,
        ),
        _v3_event_result(
            context=contexts[duplicate.artifact_id],
            action_type="אישור",
            matter="סמינר מקצועי ללמידה חדשנית בארהב",
            confidence=0.88,
            outcome_quote=decision_quote,
        ),
    ]
    events[2].validation_status = "failed"
    events[2].failure_reasons = ["action_quote_not_grounded"]
    events[2].row_quality.quality_status = "failed"
    events[2].row_quality.reason_for_failure = "action_quote_not_grounded"
    quality_rows = [event.row_quality for event in events]

    assert topic_subjects_module.topic_subject_v3_target_row_request_like_without_decision(events[0]) is True
    assert topic_subjects_module.topic_subject_v3_text_similarity("השתתפותה בסמינר מקצועי בארהב", "להשתתפות בסמינר חדשנות בארהב") >= 0.5

    selected, quality_rows = topic_subjects_module.consolidate_topic_subject_v3_events(events=events, row_quality_rows=quality_rows)
    result = topic_subjects_module.TopicSubjectV3ResearchResult(
        run_id=None,
        topic_tree={},
        artifacts=[request, approval, duplicate],
        events=selected,
        row_quality_rows=quality_rows,
        elapsed_seconds=0.0,
    )

    assert len(selected) == 1
    assert selected[0].context.target_artifact.artifact_id == approval.artifact_id
    assert selected[0].event_payload["event_identity_status"] == "primary_event"
    assert selected[0].event_payload["matter_he"] == "השתתפות בסמינר מקצועי ללמידה חדשנית בארהב"
    assert selected[0].event_payload["canonical_matter_source_artifact_id"] == approval.artifact_id
    assert result.candidate_subject_count == 1
    assert result.candidate_decision_count == 1
    quality_by_artifact = {row.artifact.artifact_id: row for row in quality_rows}
    assert quality_by_artifact[approval.artifact_id].quality_status == "accepted_primary"
    assert quality_by_artifact[request.artifact_id].event_role == "supporting_phase"
    assert quality_by_artifact[request.artifact_id].action_type_by_dicta == "בקשה"
    assert quality_by_artifact[request.artifact_id].outcome_by_dicta == ""
    assert quality_by_artifact[request.artifact_id].quality_status == "accepted_supporting_phase"
    assert quality_by_artifact[request.artifact_id].metadata["event_identity_status"] == "supporting_row_only"
    assert quality_by_artifact[duplicate.artifact_id].event_role == "duplicate_event_prediction"
    assert quality_by_artifact[duplicate.artifact_id].quality_status == "accepted_duplicate"
    assert quality_by_artifact[duplicate.artifact_id].reason_for_failure == ""
    assert quality_by_artifact[duplicate.artifact_id].metadata["suppressed_secondary_failure_reason"] == "action_quote_not_grounded"
    assert quality_by_artifact[duplicate.artifact_id].metadata["event_identity_reason"] == "same_exact_outcome_quote"
    report = topic_subjects_module.topic_subject_v3_quality_report_markdown(quality_rows)
    assert "No problematic or low-confidence rows found." in report


def test_topic_subject_v3_report_prefers_repaired_action_quote_over_focus_quote() -> None:
    artifact = _artifact_dataclass(
        real_text="'הוא מעכשיו יהיה סעיף מס84740/852/7 הסעיף הבא 'ההסתייגות היא בנושא תכנית שכונה כעיר",
        topic_label_he="תכנית שכונה כעיר",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    repaired_quote = "'ההסתייגות היא בנושא תכנית שכונה כעיר"
    row = topic_subject_v3_row_quality_from_payloads(
        context=context,
        event_id="event-test",
        event_payload={
            "is_event": True,
            "action_type_he": "הסתייגות",
            "matter_he": "תכנית שכונה כעיר",
            "action_focus_quote_he": "'הוא מעכשיו יהיה סעיף מס84740/852/7",
            "action_quote_he": repaired_quote,
            "outcome_is_decision": False,
            "target_row_role": "action_anchor",
        },
        judge_payload={
            "judge_prediction": {"is_event": True, "action_type_he": "הסתייגות", "matter_he": "תכנית שכונה כעיר"},
            "prediction_comparison": "verified_same",
            "judge_status": "accepted",
            "failure_reasons": [],
            "row_quality": {"row_role": "action_anchor", "event_role": "primary", "quality_status": "accepted"},
        },
        validation_status="accepted",
        failure_reasons=[],
    )

    assert topic_subjects_module.topic_subject_v3_evidence_quote_display(row) == repaired_quote


def test_topic_subject_v3_exports_raw_metadata_blocks_and_research_records(tmp_path) -> None:
    raw_text = "בתאריך 12.05.2026 אבקש לאשר שיפוץ ברחוב הרצל. בשכונת דקל יבוצעו עבודות."
    artifact = _artifact_dataclass(real_text=raw_text, topic_label_he="שיפוץ רחובות", source_ordinal=7)
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    event = _v3_event_result(
        context=context,
        action_type="אישור",
        matter="שיפוץ ברחוב הרצל",
        confidence=0.91,
        outcome_quote="מאושר שיפוץ ברחוב הרצל",
    )
    result = topic_subjects_module.TopicSubjectV3ResearchResult(
        run_id=None,
        topic_tree={},
        artifacts=[artifact],
        events=[event],
        row_quality_rows=[event.row_quality],
        elapsed_seconds=0.0,
    )

    event_dict = topic_subject_v3_event_to_dict(event)
    row_dict = topic_subjects_module.topic_subject_v3_row_quality_to_dict(event.row_quality)
    records = topic_subjects_module.topic_subject_v3_research_records(result)
    output_paths = topic_subjects_module.write_topic_subject_v3_outputs(output_dir=tmp_path, result=result)
    written_records = json.loads((tmp_path / "v3_research_events_subjects.json").read_text(encoding="utf-8"))

    assert any(item["raw_text"] == "12.05.2026" for item in event_dict["raw_date_mentions"])
    assert any("ברחוב הרצל" in item["raw_text"] for item in event_dict["raw_geography_mentions"])
    assert event_dict["general_text_metadata"]["scope"] == "general_text"
    assert event_dict["general_text_metadata"]["raw_text_before_cleaning_he"] == raw_text
    assert event_dict["event_metadata"]["scope"] == "event"
    assert event_dict["subject_metadata"]["scope"] == "subject"
    assert row_dict["general_text_metadata"]["source_ordinal"] == 7
    assert row_dict["event_metadata"]["action_type_he"] == "אישור"
    assert records[0]["raw_text_he"] == raw_text
    assert records[0]["general_text_metadata"]["date_mentions"][0]["raw_text"] == "12.05.2026"
    assert written_records == records
    assert output_paths["v3_research_events_subjects_json"] == str(tmp_path / "v3_research_events_subjects.json")


def test_topic_subject_v3_event_identity_ignores_model_generated_date_key() -> None:
    artifact = _artifact_dataclass(
        real_text="בתאריך 29.12.2025 ההסתייגות היא בנושא גני ילדים.",
        topic_label_he="גני ילדים",
    )
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    base_payload = {
        "is_event": True,
        "action_type_he": "הסתייגות",
        "matter_he": "גני ילדים",
        "outcome_is_decision": False,
    }
    dated_payload = {**base_payload, "event_key_he": "הסתייגות_גני_ילדים_20251229"}
    clean_payload = {**base_payload, "event_key_he": "הסתייגות_גני_ילדים"}

    dated_key = topic_subjects_module.topic_subject_v3_event_group_key(context=context, event_payload=dated_payload)
    clean_key = topic_subjects_module.topic_subject_v3_event_group_key(context=context, event_payload=clean_payload)

    assert dated_key == clean_key
    assert "20251229" not in dated_key
    assert topic_subjects_module.topic_subject_v3_event_id(context=context, event_payload=dated_payload) == topic_subjects_module.topic_subject_v3_event_id(context=context, event_payload=clean_payload)


def test_persist_topic_subject_v3_result_saves_metadata_blocks(monkeypatch) -> None:
    raw_text = "בתאריך 12.05.2026 אבקש לאשר שיפוץ ברחוב הרצל."
    artifact = _artifact_dataclass(real_text=raw_text, topic_label_he="שיפוץ רחובות", source_ordinal=7)
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    event = _v3_event_result(context=context, action_type="אישור", matter="שיפוץ ברחוב הרצל", confidence=0.91, outcome_quote="מאושר שיפוץ ברחוב הרצל")
    captured: list[SimpleNamespace] = []

    class CapturedModel(SimpleNamespace):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    class FakeSession:
        def add(self, row):  # type: ignore[no-untyped-def]
            captured.append(row)

        def flush(self) -> None:
            return None

    monkeypatch.setattr(topic_subjects_module, "TopicSubjectV3Event", CapturedModel)
    monkeypatch.setattr(topic_subjects_module, "TopicSubjectV3RowQuality", CapturedModel)

    topic_subjects_module.persist_topic_subject_v3_result(
        session=FakeSession(),
        run=SimpleNamespace(id=123, municipality_slug="test"),
        events=[event],
        row_quality_rows=[event.row_quality],
    )

    event_metadata = json.loads(captured[0].metadata_json)
    row_metadata = json.loads(captured[1].metadata_json)
    persisted_identifiers = json.loads(captured[0].matter_identifiers_json)

    assert event_metadata["model_event_key_he"]
    assert captured[0].matter_display_he == "שיפוץ ברחוב הרצל"
    assert persisted_identifiers[0]["type"] == "street_address"
    assert persisted_identifiers[0]["value_he"] == "הרצל"
    assert "2026" not in event_metadata["canonical_event_group_key"]
    assert event_metadata["general_text_metadata"]["raw_text_before_cleaning_he"] == raw_text
    assert event_metadata["event_metadata"]["action_type_he"] == "אישור"
    assert event_metadata["subject_metadata"]["matter_he"] == "שיפוץ ברחוב הרצל"
    assert event_metadata["subject_metadata"]["matter_display_he"] == "שיפוץ ברחוב הרצל"
    assert event_metadata["raw_date_mentions"][0]["raw_text"] == "12.05.2026"
    assert any("ברחוב הרצל" in item["raw_text"] for item in event_metadata["raw_geography_mentions"])
    assert event_metadata["event_source_rows"][0]["artifact_id"] == artifact.artifact_id
    assert row_metadata["general_text_metadata"]["source_ordinal"] == 7
    assert row_metadata["raw_text_before_cleaning_he"] == raw_text
    assert any("ברחוב הרצל" in item["raw_text"] for item in row_metadata["raw_geography_mentions"])


def test_benchmark_outputs_include_research_events_subjects(tmp_path) -> None:
    benchmark_module = _benchmark_topic_subject_v3_models_module()
    raw_text = "בתאריך 12.05.2026 אבקש לאשר שיפוץ ברחוב הרצל."
    artifact = _artifact_dataclass(real_text=raw_text, topic_label_he="שיפוץ רחובות", source_ordinal=7)
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    event = _v3_event_result(context=context, action_type="אישור", matter="שיפוץ ברחוב הרצל", confidence=0.91, outcome_quote="מאושר שיפוץ ברחוב הרצל")

    benchmark_module._write_outputs(
        output_dir=tmp_path,
        events=[event],
        quality_rows=[event.row_quality],
        samples=["test:7"],
        selection_summary=[{"selected_rows": 1}],
        model="primary",
        small_model="primary",
    )
    records = json.loads((tmp_path / "v3_research_events_subjects.json").read_text(encoding="utf-8"))

    assert records[0]["artifact_id"] == artifact.artifact_id
    assert records[0]["general_text_metadata"]["date_mentions"][0]["raw_text"] == "12.05.2026"
    assert any("ברחוב הרצל" in item["raw_text"] for item in records[0]["general_text_metadata"]["geography_mentions"])


def test_topic_subject_v3_quote_window_keeps_objection_cue_after_matter() -> None:
    quote = topic_subjects_module.topic_subject_v3_quote_window_around_hint(
        text="סעיף81213/786/6 סעיף תקציבי שח' בתקציב המשפחתונים לנשים עובדות200,000 ההסתייגות היא בעניין קיצוץ של להלן",
        hint="תקציב המשפחתונים לנשים עובדות",
    )

    assert "תקציב המשפחתונים לנשים עובדות" in quote
    assert "ההסתייגות" in quote


def test_topic_subject_v3_missing_action_confidence_fallback_is_visible() -> None:
    artifact = _artifact_dataclass(real_text="חברי המועצה מאשרים פה אחד את הנסיעה המקצועית.", topic_label_he="נסיעות")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]

    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "אישור",
            "matter_he": "נסיעה מקצועית",
            "outcome_is_decision": True,
            "outcome": {
                "outcome_type": "approved",
                "outcome_label_he": "אישור",
                "outcome_quote_he": artifact.real_text,
                "confidence": 0.9,
                "limitations": [],
            },
            "confidence": 0.91,
        },
        context=context,
    )

    assert event_payload["action_type_he"] == "אישור"
    assert event_payload["action_type_confidence"] == 0.91
    assert event_payload["schema_warnings"] == ["missing_action_type_confidence_used_event_confidence"]


def test_topic_subject_v3_approval_without_decision_outcome_is_allowed() -> None:
    artifact = _artifact_dataclass(real_text="פרוטוקול ועדת משנה להקצאות קרקע ללא ציטוט החלטה מפורש.", topic_label_he="הקצאות")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    event_payload = {
        "is_event": True,
        "action_type_he": "אישור",
        "matter_he": "הקצאת קרקעות",
        "outcome_is_decision": False,
        "target_row_role": "action_anchor",
    }
    row = topic_subject_v3_row_quality_from_payloads(
        context=context,
        event_id="event-test",
        event_payload=event_payload,
        judge_payload={
            "judge_prediction": {"is_event": True, "action_type_he": "אישור", "matter_he": "הקצאת קרקעות", "outcome_type": "none"},
            "prediction_comparison": "same",
            "judge_status": "accepted",
            "failure_reasons": [],
            "row_quality": {"row_role": "action_anchor", "event_role": "primary", "quality_status": "accepted"},
        },
        validation_status="accepted",
        failure_reasons=[],
    )

    assert row.reason_for_failure == ""
    assert validate_topic_subject_v3_event_payload(context=context, event_payload=event_payload) == []
    assert topic_subjects_module.topic_subject_v3_row_needs_quality_report(row) is False


def test_topic_subject_v3_rejection_action_without_outcome_is_quality_issue() -> None:
    artifact = _artifact_dataclass(real_text="חברת מועצה מבקשת לא לאשר את ההצעה ולדרוש שינוי.", topic_label_he="דיון בהצעה")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    event_payload = {
        "is_event": True,
        "action_type_he": "דחייה",
        "matter_he": "ההצעה",
        "action_quote_he": "לא לאשר את ההצעה",
        "outcome_is_decision": False,
        "target_row_role": "action_anchor",
    }

    failures = validate_topic_subject_v3_event_payload(context=context, event_payload=event_payload)

    assert "formal_decision_action_without_decision_outcome" in failures


def test_topic_subject_v3_rejection_outcome_needs_formal_decision_evidence() -> None:
    artifact = _artifact_dataclass(real_text="חברת מועצה מבקשת לא לאשר את ההצעה ולדרוש שינוי.", topic_label_he="דיון בהצעה")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "דחייה",
            "action_type_confidence": 0.9,
            "matter_he": "ההצעה",
            "action_quote_he": "לא לאשר את ההצעה",
            "outcome_is_decision": True,
            "outcome": {
                "outcome_type": "rejected",
                "outcome_label_he": "דחייה",
                "outcome_quote_he": "לא לאשר את ההצעה",
                "confidence": 0.9,
                "limitations": [],
            },
            "target_row_role": "action_anchor",
        },
        context=context,
    )

    failures = validate_topic_subject_v3_event_payload(context=context, event_payload=event_payload)

    assert "formal_decision_outcome_without_formal_evidence" in failures


def test_topic_subject_v3_formal_rejection_outcome_passes_formal_decision_evidence() -> None:
    artifact = _artifact_dataclass(real_text="החלטה: הוחלט שלא לאשר את ההצעה.", topic_label_he="דיון בהצעה")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "דחייה",
            "action_type_confidence": 0.9,
            "matter_he": "ההצעה",
            "action_quote_he": "הוחלט שלא לאשר את ההצעה",
            "outcome_is_decision": True,
            "outcome": {
                "outcome_type": "rejected",
                "outcome_label_he": "דחייה",
                "outcome_quote_he": "הוחלט שלא לאשר את ההצעה",
                "confidence": 0.9,
                "limitations": [],
            },
            "target_row_role": "action_anchor",
        },
        context=context,
    )

    failures = validate_topic_subject_v3_event_payload(context=context, event_payload=event_payload)

    assert "formal_decision_outcome_without_formal_evidence" not in failures


def test_topic_subject_v3_mixed_protocol_header_with_decision_is_not_structural_non_event() -> None:
    artifact = _artifact_dataclass(
        real_text=(
            "פרוטוקול ישיבות המועצה העשרים ישיבה מיוחדת מתאריך י\"ח בסיוון תשע\"ז - 5 - "
            ". מ א ו ש ר-החלטה: העברת סגן ראש עירייה מכהונתו ובחירת סגן במקומו. "
            "המועצה החליטה ברוב קולות, בתוקף סמכותה לפי החוק, להעביר את סגן ראש העירייה מכהונתו, "
            "לבחור חבר מועצה כסגן בשכר לראש העירייה, ולאשר את אצילת סמכויות ראש העירייה."
        ),
        topic_label_he="מנהל עירוני ומינויים",
    )

    structural = topic_subjects_module.topic_subject_v3_structural_non_event_payload(artifact)

    assert structural is None


def test_topic_subject_v3_agreement_action_without_decision_is_not_approval_failure() -> None:
    artifact = _artifact_dataclass(real_text="הסכם הסדרת רשות שימוש להפעלת מתקן שידור בבית העירייה.", topic_label_he="הסכמים")
    context = build_topic_subject_v3_event_contexts(artifacts=[artifact])[0]
    event_payload = normalize_topic_subject_v3_event_payload(
        payload={
            "is_event": True,
            "action_type_he": "התקשרות",
            "action_type_confidence": 0.86,
            "matter_he": "רשות שימוש להפעלת מתקן שידור בבית העירייה",
            "action_quote_he": "הסכם הסדרת רשות שימוש להפעלת מתקן שידור בבית העירייה",
            "outcome_is_decision": False,
            "target_row_role": "action_anchor",
            "confidence": 0.86,
        },
        context=context,
    )

    failures = validate_topic_subject_v3_event_payload(context=context, event_payload=event_payload)
    row = topic_subject_v3_row_quality_from_payloads(
        context=context,
        event_id="event-test",
        event_payload=event_payload,
        judge_payload={
            "judge_prediction": {"is_event": True, "action_type_he": "התקשרות", "matter_he": "רשות שימוש להפעלת מתקן שידור", "outcome_type": "none"},
            "prediction_comparison": "same",
            "judge_status": "accepted",
            "failure_reasons": [],
            "row_quality": {"row_role": "action_anchor", "event_role": "primary", "quality_status": "accepted"},
        },
        validation_status="accepted",
        failure_reasons=failures,
    )

    assert event_payload["action_type_he"] == "התקשרות"
    assert "approval_action_without_decision_outcome" not in failures
    assert row.quality_status == "accepted"


def test_topic_subject_v3_long_meeting_participant_header_is_structural_non_event() -> None:
    participant_text = (
        "ישיבת מועצת העיר תל אביב-יפו מיוחדת מתאריך ח בשבט תשעט היו\"ר רון חולדאי השתתפו "
        + " ".join(f"חבר מועצה {index}" for index in range(90))
        + " מנכ\"ל העירייה נכחו בישיבה היועמ\"ש גזבר העירייה מזכירת המועצה סטנוגרמה הישיבה נפתחה בשעה 18:08"
    )
    artifact = _artifact_dataclass(real_text=participant_text, topic_label_he="פרוטוקול")

    structural = topic_subjects_module.topic_subject_v3_structural_non_event_payload(artifact)

    assert structural is not None
    assert structural["row_role"] == "meeting_header"


def test_topic_subject_v3_meeting_opening_with_participants_is_structural_non_event() -> None:
    artifact = _artifact_dataclass(
        real_text=(
            "2019) מתאריך ח' בשבט תשע\"ט היו\"ר ר. חולדאי : השתתפו ח. אריאלי ר. אלקבץ "
            "מנכ\"ל העירייה מנחם לייבה :נכחו בישיבה היועמ\"ש גזבר העירייה "
            "18:08 :הישיבה נפתחה בשעה סטנוגרמה מזכירת המועצה"
        ),
        topic_label_he="פרוטוקול",
    )

    structural = topic_subjects_module.topic_subject_v3_structural_non_event_payload(artifact)

    assert structural is not None
    assert structural["row_role"] == "meeting_header"


def test_topic_subject_v3_decision_row_with_closing_footer_is_not_structural_header() -> None:
    artifact = _artifact_dataclass(
        real_text=(
            "ליאור שפירא נבחר להיות יו\"ר המועצה החלטה: מחליטים, בהתאם לסעיף 130 "
            "לפקודת העיריות, לבחור ברוב קולות את מר ליאור שפירא להיות יו\"ר מועצת העירייה. "
            "הישיבה ננעלה בשעה 18:10 מנכ\"ל יו\"ר ערכה מזכירת המועצה"
        ),
        topic_label_he="בחירת יו\"ר",
    )

    structural = topic_subjects_module.topic_subject_v3_structural_non_event_payload(artifact)

    assert structural is None


def test_topic_subject_v3_legal_meeting_basis_fragment_is_structural_non_event() -> None:
    artifact = _artifact_dataclass(
        real_text=":סדר היום לפקודת העיריות130 ישיבה מיוחדת לפי סעיף",
        topic_label_he="סדר יום ושאילתות",
    )

    structural = topic_subjects_module.topic_subject_v3_structural_non_event_payload(artifact)

    assert structural is not None
    assert structural["row_role"] == "legal_meeting_basis_fragment"


def _v3_event_result(
    *,
    context: topic_subjects_module.TopicSubjectV3EventContext,
    action_type: str,
    matter: str,
    confidence: float,
    outcome_quote: str,
) -> topic_subjects_module.TopicSubjectV3EventResult:
    outcome_is_decision = bool(outcome_quote)
    payload = normalize_topic_subject_v3_event_payload(
        payload={
            "context_id": context.context_id,
            "target_artifact_id": context.target_artifact.artifact_id,
            "is_event": True,
            "event_key_he": f"{action_type}: {matter}",
            "action_type_he": action_type,
            "action_type_confidence": confidence,
            "matter_he": matter,
            "action_quote_he": context.target_artifact.real_text,
            "outcome_is_decision": outcome_is_decision,
            "outcome": {
                "outcome_type": "approved" if outcome_is_decision else "none",
                "outcome_label_he": "אישור" if outcome_is_decision else None,
                "outcome_summary_he": outcome_quote if outcome_is_decision else None,
                "outcome_quote_he": outcome_quote if outcome_is_decision else None,
                "confidence": confidence if outcome_is_decision else 0.0,
                "limitations": [],
            },
            "target_row_role": "action_anchor",
            "row_roles": [
                {
                    "artifact_id": artifact.artifact_id,
                    "row_role": "action_anchor" if artifact.artifact_id == context.target_artifact.artifact_id else "dependent_detail",
                    "event_role": "primary" if artifact.artifact_id == context.target_artifact.artifact_id else "supporting",
                    "reason_he": "unit_test",
                }
                for artifact in context.rows
            ],
            "confidence": confidence,
            "rationale_he": "unit_test",
        },
        context=context,
    )
    judge_payload = {
        "judge_prediction": {
            "is_event": True,
            "action_type_he": action_type,
            "matter_he": matter,
            "outcome_type": "approved" if outcome_is_decision else "none",
            "outcome_label_he": "אישור" if outcome_is_decision else None,
            "outcome_quote_he": outcome_quote or None,
            "confidence": confidence,
            "rationale_he": "unit_test",
        },
        "prediction_comparison": "same",
        "event_identity_status": "new_event",
        "judge_status": "accepted",
        "ground_truth_he": "unit_test",
        "reason_for_failure": "",
        "failure_reasons": [],
        "row_quality": {
            "row_role": "action_anchor",
            "event_role": "primary",
            "quality_status": "accepted",
            "ground_truth_he": "unit_test",
            "reason_for_failure": "",
        },
    }
    event_id = topic_subjects_module.topic_subject_v3_event_id(context=context, event_payload=payload)
    row_quality = topic_subject_v3_row_quality_from_payloads(
        context=context,
        event_id=event_id,
        event_payload=payload,
        judge_payload=judge_payload,
        validation_status="accepted",
        failure_reasons=[],
    )
    return topic_subjects_module.TopicSubjectV3EventResult(
        event_id=event_id,
        event_index=0,
        context=context,
        event_payload=payload,
        normalized_event={"is_event": True},
        extraction_payload=payload,
        judge_payload=judge_payload,
        validation_status="accepted",
        failure_reasons=[],
        row_quality=row_quality,
    )


def _benchmark_topic_subject_v3_models_module():
    module_path = Path("/Users/igor/Desktop/projects/Municipality/scripts/benchmark_topic_subject_v3_models.py")
    spec = importlib.util.spec_from_file_location("benchmark_topic_subject_v3_models_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _experiment_topic_subject_v3_profiles_module():
    module_path = Path("/Users/igor/Desktop/projects/Municipality/scripts/experiment_topic_subject_v3_profiles_real_rows.py")
    spec = importlib.util.spec_from_file_location("experiment_topic_subject_v3_profiles_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_topic_subject_v3_profile_enum_checker_accepts_recoverable_values() -> None:
    module = _experiment_topic_subject_v3_profiles_module()

    invalid = module._invalid_enum_fields(
        {
            "target_row_role": "body",
            "event_status": "non_event",
            "row_roles": [{"row_role": "action_anchor|event_title|dependent_detail"}, {"row_role": "target"}],
            "entailment_status": {
                "action_type_he": {"status": "entailed"},
                "matter_he": {"status": "entailed"},
                "outcome": {"status": "not_applicable"},
            },
            "prediction_comparison": "same|model_invalidated|judge_uncertain",
            "event_identity_status": "new_event_best_guess",
            "outcome_he": "אושרה החלטה לאשר את דו\"ח מבקרת העירייה",
            "evidence_roles": {"subject_hint_relation": "supports_matter|conflicts_with_raw_text|subject_only|not_relevant|null"},
            "outcome_evidence_classification": "approved",
            "outcome": {"outcome_evidence_classification": "request_for_outcome"},
            "field_assessments": {"outcome": {"outcome_evidence_classification": "actual_outcome"}},
            "span_roles": [{"span_role": "subject|matter_candidate"}, {"span_role": "structural|date"}],
            "row_quality": {"quality_status": "high"},
        },
        {
            "target_row_role": "action_anchor|event_title|dependent_detail|decision_result|vote_metadata|document_fragment|structural_metadata|duplicate_reference|insufficient_context",
            "event_status": "open_request|discussed|approved|rejected|deferred|removed|referred|reported|not_event|unknown",
            "row_roles": [{"row_role": "action_anchor|event_title|dependent_detail|decision_result|vote_metadata|document_fragment|structural_metadata|duplicate_reference|insufficient_context"}],
            "entailment_status": "entailed|partially_entailed|not_entailed|uncertain",
            "prediction_comparison": "same|partially_different|different|model_invalid|judge_uncertain",
            "event_identity_status": "new_event|same_as_existing_event|supporting_row_only|duplicate_prediction|unknown",
            "outcome_he": "approved|rejected|referred|removed|deferred|reported|none|unknown|null",
            "evidence_roles": {"subject_hint_relation": "supports_matter|conflicts_with_raw_text|subject_only|not_relevant|null"},
            "outcome_evidence_classification": "actual_result|proposal_or_intent|ambiguous_agreement|not_outcome|null",
            "outcome": {"outcome_evidence_classification": "actual_result|proposal_or_intent|ambiguous_agreement|not_outcome|null"},
            "field_assessments": {"outcome": {"outcome_evidence_classification": "actual_result|proposal_or_intent|ambiguous_agreement|not_outcome|null"}},
            "span_roles": [{"span_role": "structural|background|action_candidate|outcome_candidate|supporting_context|not_relevant"}],
            "row_quality": {"quality_status": "accepted|needs_review|failed|non_event"},
        },
    )

    assert invalid == []


def test_topic_subject_v3_profile_stage_problems_ignore_optional_recovery_failure_after_usable_prediction() -> None:
    module = _experiment_topic_subject_v3_profiles_module()

    problems = module._stage_problems(
        stage_calls=[
            {
                "stage": "topic_subject_v3_non_event_reconsideration",
                "done_reason": "length",
                "quality": {"errors": ["response_not_parsed_as_json_object"]},
            },
            {
                "stage": "topic_subject_v3_action_subject_extraction",
                "done_reason": "length",
                "quality": {"errors": ["response_not_parsed_as_json_object"]},
            },
        ],
        prediction={"quality_status": "accepted"},
    )

    assert problems == [
        "topic_subject_v3_action_subject_extraction response_not_parsed_as_json_object",
        "topic_subject_v3_action_subject_extraction done_reason=length",
    ]


def _artifact_dataclass(
    *,
    real_text: str,
    topic_label_he: str,
    artifact_id: str = "artifact-test",
    source_ordinal: int = 1,
    metadata: dict | None = None,
    source_title: str = "מקור בדיקה",
    source_url: str = "https://example.local/doc.pdf",
) -> TopicDecisionArtifact:
    return TopicDecisionArtifact(
        artifact_id=artifact_id,
        semantic_node_id=1,
        topic_label_he=topic_label_he,
        root_topic_id="root_test",
        root_label_he="שורש",
        child_topic_id=None,
        child_label_he=None,
        source_kind="pdf_first_v4_protocol",
        source_document_id=1,
        source_document_version_id=1,
        source_ordinal=source_ordinal,
        source_title=source_title,
        source_url=source_url,
        artifact_kind="pdf_first_v4_retrieval_chunk",
        start_page=1,
        end_page=1,
        header_path=["שורש"],
        real_text=real_text,
        retrieval_text=real_text,
        topic_confidence=0.9,
        existing_decision_candidate_id=None,
        metadata=metadata or {},
    )


def _subject_payload(
    *,
    root: str,
    child: str,
    object_text: str,
    details: str,
    evidence: str,
    is_decision: bool = False,
    decision: dict | None = None,
) -> dict:
    return {
        "subject_root_label_he": root,
        "subject_child_label_he": child,
        "subject_object_he": object_text,
        "subject_details_he": details,
        "subject_type_evidence_he": evidence,
        "subject_summary_he": details,
        "what_text_is_about_he": details,
        "is_decision": is_decision,
        "decision": decision,
        "confidence": 0.8,
        "rationale_he": "test",
    }
