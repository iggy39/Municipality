from __future__ import annotations

from municipality.topic_subjects import apply_event_grouping, preclassify_trivial_subject_payload, process_topic_subject_payload, topic_subject_prompt_payload
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
    assert any("exactly one primary subject" in requirement for requirement in payload["requirements"])
    assert all(not example["subject_root_label_he"].startswith("חינוך") for example in payload["topic_aware_examples_not_a_codelist"])


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
    assert subjects[0].subject_payload["subject_root_label_he"] == "אישור"
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
    assert inquiry_quality.subject_child_by_dicta == "בקשת מידע"
    assert question_quality.row_role == "dependent_detail"
    assert question_quality.anchor_status == "linked_to_validated_anchor"


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


def test_process_topic_subject_payload_keeps_agenda_proposal_subject() -> None:
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
    assert subjects[0].subject_payload["subject_root_label_he"] == "הצעה"
    assert subjects[0].subject_payload["subject_child_label_he"] == "הצעה לסדר יום"
    assert quality.subject_root_by_dicta == "הצעה"
    assert quality.subject_child_by_dicta == "הצעה לסדר יום"


def _artifact_dataclass(*, real_text: str, topic_label_he: str, artifact_id: str = "artifact-test", source_ordinal: int = 1) -> TopicDecisionArtifact:
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
        source_title="מקור בדיקה",
        source_url="https://example.local/doc.pdf",
        artifact_kind="pdf_first_v4_retrieval_chunk",
        start_page=1,
        end_page=1,
        header_path=["שורש"],
        real_text=real_text,
        retrieval_text=real_text,
        topic_confidence=0.9,
        existing_decision_candidate_id=None,
        metadata={},
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
