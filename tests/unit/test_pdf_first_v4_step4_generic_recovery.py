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


def test_order_proposal_discussion_subject_is_trimmed() -> None:
    contract = step4._topic_contract_from_headline(
        "הצעה לסדר שעסקה בנושא המיגון. וביקשנו לקיים דיון נוסף בוועדת החירום",
        structural_role="outline_item",
        packet_role="protocol",
    )

    assert contract["is_topic_bearing"] is True
    assert contract["topic_subject_he"] == "המיגון"


def test_multi_item_agenda_tail_is_protocol_listing() -> None:
    text = "4 מינוי נציג העירייה לוועדה הציבורית לפי תמא.10 )30 ' (עמ2024 דוח פניות ותלונות הציבור.11 )31 ' (עמ2024 דוח תאגידים לשנת.12 )34 'נאומים מהמקום (עמ.13"

    assert step4._looks_like_protocol_listing(text) is True
    assert step4._non_topic_protocol_reason(
        headline="מינוי נציג העירייה לוועדה הציבורית לפי תמא",
        raw_text=text,
        structural_role="outline_item",
        packet_role="protocol",
    ) == "protocol_listing"


def test_contract_approval_procedure_without_appendix_is_non_topic() -> None:
    assert step4._looks_like_contract_approval_procedure_fragment(
        "יובאו לאישור ועדת התקשרויות עליונה ואישור ההתקשרות בחוזה ללא מכרז"
    ) is True


def test_personal_topic_reference_is_non_topic() -> None:
    reason = step4._non_topic_protocol_reason(
        headline="שלי היום היא בנושא תיקון התנהלות בתאגידים העירוניים",
        raw_text="ההצעה שלי היום היא בנושא תיקון התנהלות בתאגידים העירוניים ואני רוצה להסביר",
        structural_role="body",
        packet_role="protocol",
    )

    assert reason == "personal_topic_reference"


def test_order_proposal_speaker_reply_is_non_topic() -> None:
    reason = step4._non_topic_protocol_reason(
        headline="יו\"ר הישיבה-מר זמיר את צודקת הוא באמת בהמשך של זה",
        raw_text="הצעה לסדר : יו\"ר הישיבה-מר זמיר .את צודקת. הוא באמת בהמשך של זה",
        structural_role="outline_item",
        packet_role="protocol",
    )

    assert reason == "speaker_reference_heading"


def test_appointment_heading_prefers_minnui_phrase() -> None:
    heading = step4._extract_heading_from_text(
        "עוברים לנושא הבא, נושא מס . נמל התעופה בן גוריון2/ מינוי נציג העירייה לוועדה הציבורית לפי תמ\"א בנמל התעופה בן גוריון"
    )

    assert heading == "מינוי נציג העירייה לוועדה הציבורית לפי תמ\"א בנמל התעופה בן גוריון"


def test_subject_cleaning_strips_years_to_metadata() -> None:
    assert step4.clean_protocol_subject_text("תקציב 2026") == "תקציב"
    assert step4.clean_protocol_subject_text("הצעת התקציב הרגיל והבלתי רגיל לשנת 2026") == "הצעת התקציב הרגיל והבלתי רגיל"
    assert step4.clean_protocol_subject_text("מכרז פומבי מס 89/2025 להשכרת מבנה") == "מכרז פומבי להשכרת מבנה"
    assert step4.clean_protocol_subject_text("צעדים לתיקון בעקבות דו\"ח .מבקר המדינה") == "צעדים לתיקון בעקבות דו\"ח מבקר המדינה"
    assert step4.clean_topic_label("תקציב 2026") == "תקציב"


def test_canonical_topic_label_shortens_reusable_subjects() -> None:
    cases = [
        (
            "הרכבת גוף בוחר לבחירת רב ראשי אורתודוכסי",
            "בחירת רב ראשי",
        ),
        (
            "התשע\"ה א \"פטור לנכס שאינו ראוי לשימוש בשל נזק מלחמה א",
            "פטור לנכס בשל נזק מלחמה",
        ),
        (
            "להשכרת מבנה למטרת ניהול והפעלת/2025 ' מכרז פומבי מס- . אישור התקשרות עם זוכה",
            "התקשרות להשכרת מבנה",
        ),
        (
            "הסכם רשות בין עיריית אשדוד לבין עמותת מרכז לחינוך תורני לב שמחה דחסידי גור אשדוד - הסדרת שימוש לצורך הפעלת גני ילדים בגוש ח\"ח",
            "הסכם רשות למוסדות חינוך",
        ),
        (
            "עדכון שכר ל- 65% משכר מנכ\"ל לעוזר בכיר לראש הרשות שמואל דוד",
            "עדכון שכר עוזר בכיר",
        ),
        (
            "טבלת ניקוד ענפי הספורט בעיר אשדוד עפ\"י דירוג עדיפות הענף ורמת הישגיותו בוגרים נוער ילדים דירוג עדיפות 1 2 3 4 5 6",
            "ניקוד תמיכות ספורט",
        ),
        (
            "חילופי גברי בוועדות ובדירקטוריונים– .)(דינה ,השתתפו בזום",
            "חילופי גברי בוועדות ודירקטוריונים",
        ),
        (
            "המחלקה הווטרינרית– הסכמים עם מרפאות חוץ ( בטיפול בחתולים",
            "הסכמים עם מרפאות וטרינריות",
        ),
        (
            "טיפול העירייה בדרי רחוב שבתחומה",
            "טיפול בדרי רחוב",
        ),
        (
            "אבטחת מידע פנים והגנת פרטיות מפני מתקפות סייבר",
            "אבטחת מידע והגנת פרטיות",
        ),
        (
            "כנפי רוח\" הקצאה לפעילות רב תכליתית – מבקש ההגדרה לכך",
            "הקצאה לפעילות רב תכליתית",
        ),
        (
            "לנוהל תמיכות לעמותות אשר הגישו בקשה למקדמה בהתאם לנהלים",
            "נוהל תמיכות לעמותות",
        ),
        (
            "מינוי מנכ\"ל חב' יובלים",
            "מינוי מנכ\"ל חברה עירונית",
        ),
        (
            "פרוטוקול מישיבת ו עדת תמיכות מקצועית מס 22 מיום.7.22",
            "ועדת תמיכות מקצועית",
        ),
        (
            "אישור נסיעה למשלחת ראש העיר לברצלונה",
            "נסיעת משלחת עירונית",
        ),
        (
            "הנחת מבנה יביל ברחוב האדמור מבעל\"ז",
            "הנחת מבנה יביל",
        ),
        (
            "אי בניית מבני ציבור מתנ\"סים בעשור האחרון",
            "בניית מבני ציבור ומתנ\"סים",
        ),
        (
            "זרימת מי ביוב בחוף יא",
            "זרימת מי ביוב בחוף",
        ),
        (
            "בקשה לשדרוג תבחינים - שיפוץ חזיתות אינג’ :כצנלסון ביקשנו לשדרג את התבחינים",
            "תבחינים לשיפוץ חזיתות",
        ),
        (
            "הנדון המלצה ל מענקי הישגי ות– ספורט א להלן רשימת ההישגים והמענקים לאישורכם",
            "מענקי הישגיות ספורט",
        ),
        (
            "ציוד מגן אישי שמתוקצב פר עובד, פר מקצוע",
            "ציוד מגן אישי לעובדים",
        ),
        (
            "סיוע להבטחת ביטחון תזונתי על רקע משבר הקורונה",
            "סיוע לביטחון תזונתי",
        ),
        (
            "חממת שטראוס מהתקציב העירוני",
            "מימון חממה מהתקציב העירוני",
        ),
        (
            "מה ההטבות שהתקבלו בעקבות הירידה במדד החברתי-כלכלי",
            "הטבות בעקבות ירידה במדד חברתי-כלכלי",
        ),
        (
            "אישור הרשאות עיריית אשדוד",
            "הרשאות עירייה",
        ),
        (
            "חידוש כהונה לעו\"ד אלי נכט בדירקטוריון יובלים",
            "חידוש כהונה בדירקטוריון",
        ),
        (
            "המאגר שממנו נשלחים מסרונים לתושבי העיר בנושאים שונים",
            "מאגר מסרונים לתושבים",
        ),
        (
            "מסר וידאו שנשלח לתושבים",
            "מסרי וידאו לתושבים",
        ),
        (
            "לוחות פרסום אלקטרוניים בעיר",
            "לוחות פרסום אלקטרוניים",
        ),
        (
            "מוכנות עיריית אשדוד לרעידת אדמה והתקנת מערכת התראה",
            "מוכנות לרעידת אדמה ומערכת התראה",
        ),
        (
            "זיהום ים משפכים בחופי המצודה ומנחל לכיש",
            "זיהום ים משפכים",
        ),
        (
            "פרוטוקול מישיבת ועדת הועדה למיגור תופעת האלימות מס 21 מיום",
            "ועדה למיגור תופעת האלימות",
        ),
        (
            "אופן הטיפול בגורים יונקים של חתולי רחוב",
            "טיפול בגורי חתולי רחוב",
        ),
        (
            "המח' הווטרינרית- שעות פעילות ותקציב המחלקה",
            "שעות פעילות ותקציב מחלקה וטרינרית",
        ),
        (
            "מדעניות העתיד של העיר אשדוד",
            "תוכנית מדעניות העתיד",
        ),
        (
            "סיוע ממשלת ישראל במיגון העיר אשדוד",
            "סיוע במיגון העיר",
        ),
        (
            "הסכם עם החברה העירונית לתיירות אשדוד",
            "הסכם עם חברה עירונית לתיירות",
        ),
        (
            "בקשה לאישור מועצה להתקשרות בפטור ממכרז- ביטוח אחריות נושאי משרה",
            "התקשרות לביטוח אחריות נושאי משרה",
        ),
        (
            "הקמת פסל ע\"ש יאשה אליאשוילי",
            "הקמת פסל ציבורי",
        ),
        (
            "שימוע למהנדס העיר ד\"ר לחמני המהנדס כרגע עדיין נמצא בתפקיד",
            "שימוע למהנדס העיר",
        ),
        (
            "אלרגיות מסכנות חיים ומזרקי אפיפן במרחב הציבורי",
            "אלרגיות ואפיפן במרחב הציבורי",
        ),
        (
            "אלרגיות מסכנות דיון עפ\"י",
            "אלרגיות ואפיפן במרחב הציבורי",
        ),
        (
            "תרומת זורב גגולשוילי",
            "הקמת פסל ציבורי",
        ),
    ]

    for raw, expected in cases:
        evidence = "חיים ומזרקי אפיפן במרחב הציבורי" if raw.startswith("אלרגיות מסכנות דיון") else "תרומה להקמת פסל ע\"ש יאשה" if raw.startswith("תרומת") else None
        canonical, _reason = step4.canonicalize_topic_label(raw, evidence_text=evidence)
        assert canonical == expected


def test_canonical_topic_label_rejects_dialogue_only() -> None:
    canonical, reason = step4.canonicalize_topic_label("יו\"ר הישיבה .זה מועצת העיר, נכון")

    assert canonical is None
    assert reason == "procedural_or_dialogue_label"


def test_canonical_topic_label_rejects_clause_fragment() -> None:
    canonical, reason = step4.canonicalize_topic_label("כוח אדם, שנוגעת לכוח אדם")

    assert canonical is None
    assert reason == "procedural_or_dialogue_label"


def test_canonical_topic_label_rejects_generic_committee_carrier() -> None:
    canonical, reason = step4.canonicalize_topic_label("פרוטוקול ועדה מקצועית")

    assert canonical is None
    assert reason == "procedural_or_dialogue_label"


def test_canonical_topic_label_rejects_protocol_decision_fragment() -> None:
    canonical, reason = step4.canonicalize_topic_label("שהתקבלו בפרוטוקולים של הועדות כוחם יפה והם בתוקף")

    assert canonical is None
    assert reason == "procedural_or_dialogue_label"


def test_canonical_topic_label_rejects_decision_carriers() -> None:
    for raw in ["החלטה", "ה חלטה", "חמור זה התייחסות הוועדה ל", "מחדש לפני ועדת התמיכות"]:
        canonical, reason = step4.canonicalize_topic_label(raw)

        assert canonical is None
        assert reason == "procedural_or_dialogue_label"


def test_canonical_topic_label_does_not_use_unrelated_evidence_subject() -> None:
    canonical, _reason = step4.canonicalize_topic_label(
        "מינוי גב' איילה גיני לתפקיד מנהלת אגף פרט, בקרה ונוכחות במנהל משאבי אנוש",
        evidence_text="הסכם רשות בין עיריית אשדוד לבין עמותת מרכז לחינוך תורני",
    )

    assert canonical != "הסכם רשות למוסדות חינוך"


def test_subject_scoped_protocol_mode_detected_from_title_text() -> None:
    units = [
        {
            "structure_unit_id": "u1",
            "raw_text": "פרוטוקול ישיבה לא מן המניין מס 31 תקציב - 2- :סדר הישיבה 2026 הצעת התקציב הרגיל והבלתי רגיל לשנת ************************",
        },
        *[
            {
                "structure_unit_id": f"u{i}",
                "raw_text": ":גב' כהן . דברי הסבר : היו\"ר-מר לוי . תגובה",
            }
            for i in range(2, 8)
        ],
    ]
    context = step4._document_context(input_pdf=None, packet_role="protocol", units=units)

    assert context["protocol_subject_he"] == "הצעת התקציב הרגיל והבלתי רגיל"
    assert context["topic_carrier_mode"] == "subject_scoped_transcript_topics"
    assert "2026" in context["temporal_metadata"]


def test_normal_protocol_without_subject_stays_headline_mode() -> None:
    units = [
        {
            "structure_unit_id": "u1",
            "raw_text": "פרוטוקול ישיבה מן המניין מס 35 :סדר הישיבה שאילתות הצעות לסדר אישורים",
        },
        {
            "structure_unit_id": "u2",
            "raw_text": "שאילתה: יובל צלנר, חבר מועצה",
        },
    ]
    context = step4._document_context(input_pdf=None, packet_role="protocol", units=units)

    assert context["protocol_subject_he"] is None
    assert context["topic_carrier_mode"] == "headline_topics"


def test_protocol_subject_rejects_transcript_fragment() -> None:
    units = [
        {
            "structure_unit_id": "u1",
            "raw_text": "ישיבה לא מן המניין מס 28 ועכשיו אני אחזור לדוח שיש בפניכם דוחות ביקורת וגם ב- כמו הדוחות הקודמים בכל שנה, דנים ב2024 לשנת53 הדוח הזה, דוח מספר בדיקה",
        }
    ]
    context = step4._document_context(input_pdf=None, packet_role="protocol", units=units)

    assert context["protocol_subject_he"] is None
    assert context["topic_carrier_mode"] == "headline_topics"


def test_subject_scoped_anchor_extracts_hidden_topic_without_year() -> None:
    unit = {
        "structure_unit_id": "u1",
        "structural_role": "outline_item",
        "raw_text": "סעיף81213/786/6 סעיף תקציבי :גב' קשת . שח' בתקציב המשפחתונים לנשים עובדות200,000 ההסתייגות היא בעניין קיצוץ של : היו\"ר-מר שפירא .11 להלן",
    }
    context = {
        "packet_role": "protocol",
        "protocol_subject_he": "הצעת התקציב הרגיל והבלתי רגיל",
        "topic_carrier_mode": "subject_scoped_transcript_topics",
    }

    anchor = step4._subject_scoped_transcript_topic_anchor(unit=unit, document_context=context)

    assert anchor is not None
    assert anchor["topic_subject_he"] == "תקציב המשפחתונים לנשים עובדות"
    assert "2026" not in anchor["topic_subject_he"]


def test_subject_scoped_anchor_rejects_body_budget_clause() -> None:
    unit = {
        "structure_unit_id": "u1",
        "structural_role": "body",
        "raw_text": ":מר גילצר . תודה רבה על ההזדמנות להציג את הדוחות הכספיים. "
        + " ".join(["העירייה פעלה באחריות כלכלית"] * 16)
        + " התנהלה בתקציב המשכי והשלכות המלחמה ונבעה מהליך התכנסות.",
    }
    context = {
        "packet_role": "protocol",
        "protocol_subject_he": "דוחות כספיים מבוקרים",
        "topic_carrier_mode": "subject_scoped_transcript_topics",
    }

    assert step4._subject_scoped_transcript_topic_anchor(unit=unit, document_context=context) is None


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


def test_short_continuation_without_local_marker_is_not_standalone_topic() -> None:
    raw_text = "2026 יפו מתן פטור מאגרות בעקבות המלחמה הוראת שעה הסעיף הבא חוק העזר לתל אביב"
    unit = {"structural_role": "continuation", "raw_text": raw_text}

    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="יפו מתן פטור מאגרות בעקבות המלחמה",
        raw_text=raw_text,
        topic_context_source="unit_heading",
        headline_source="raw_extracted",
        packet_role="protocol",
    ) == "transcript_window_without_bounded_headline"


def test_legal_boilerplate_fragment_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="לפקודת העיריות המועצה החליטה בתוקף סמכותה לפי סעיף",
        raw_text="לפקודת העיריות המועצה החליטה בתוקף סמכותה לפי סעיף",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "legal_boilerplate_fragment"


def test_transcript_thank_you_fragment_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="בדקתי ושלחתי לה תודה על טיפול מסור וסבלני אז תודה לך",
        raw_text="בדקתי, הראשון שבהם היה בפברואר. שלחתי לה. תודה על טיפול מסור וסבלני אז תודה לך",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "transcript_speech_fragment"


def test_short_speech_fragment_without_subject_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="היום, אני אגיד לך על",
        raw_text="היום, אני אגיד לך על",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "transcript_speech_fragment"


def test_visual_instruction_fragment_without_subject_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="רועי ואורנה ?עוד מישהו מחוץ מרועי ומאורנה .אני מזכיר לכם, את הסעיף התקציבי אתם צריכים להגיד כל פעם",
        raw_text="רועי ואורנה ?עוד מישהו מחוץ מרועי ומאורנה .אני מזכיר לכם, את הסעיף התקציבי אתם צריכים להגיד כל פעם",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "transcript_speech_fragment"


def test_speaker_dialogue_fragment_without_subject_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="סעיף תקציבי :גב' קשת . ההסתייגות היא בעניין קיצוץ : היו\"ר-מר שפירא .11 להלן",
        raw_text="סעיף תקציבי :גב' קשת . ההסתייגות היא בעניין קיצוץ : היו\"ר-מר שפירא .11 להלן",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "speaker_dialogue_fragment"


def test_signature_end_page_fragment_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline='תצלום סיום הפרוטוקול וחתימות יו"ר הישיבה ומנכ"ל העירייה',
        raw_text='תצלום סיום הפרוטוקול וחתימות יו"ר הישיבה ומנכ"ל העירייה',
        structural_role="continuation",
        packet_role="protocol",
    ) == "signature_or_end_page_fragment"


def test_model_non_topic_topic_item_is_normalized_to_fragment() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "topic_item",
            "topic_headline_he": "תצלום סיום הפרוטוקול וחתימות",
            "topic_subject_he": "תצלום סיום הפרוטוקול וחתימות",
            "document_context": {"packet_role": "protocol"},
            "raw_text": "תצלום סיום הפרוטוקול וחתימות",
        },
        reason="model_or_shape_non_topic",
    )

    assert row["row_type"] == "fragment"
    assert row["is_topic_bearing"] is False
    assert row["topic_subject_he"] is None


def test_non_topic_vote_row_does_not_inherit_child_candidate() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "vote_or_result",
            "topic_identification_context": "מינוי נציג ציבור בוועדת הבטיחות בדרכים",
            "topic_headline_he": "מינוי נציג ציבור בוועדת הבטיחות בדרכים",
            "document_context": {"packet_role": "protocol"},
            "raw_text": "מינוי נציג ציבור בוועדת הבטיחות בדרכים. מי בעד?",
            "candidate_child_topics": [
                {
                    "candidate_child_id": "c1",
                    "label_he": "בטיחות בדרכים",
                    "root_topic_id": "root_transport_safety",
                    "root_label_he": "תחבורה ובטיחות",
                    "confidence_hint": 0.96,
                    "evidence_source": "existing_tree",
                }
            ],
        }
    )

    assert row["is_topic_bearing"] is False
    assert row["root_topic_id"] == "root_transport_safety"
    assert row["child_label_he"] is None
    assert "inherited_candidate" not in row["topic_assignment_route"]


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


def test_query_attribution_without_subject_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="שאילתה: יובל צלנר, חבר מועצה",
        raw_text="שאילתה: יובל צלנר, חבר מועצה",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "query_attribution_only"


def test_query_intro_without_subject_is_not_topic() -> None:
    text = "שאילתה: אורנה ברביבאי, חברת מועצה : היו\"ר-מר שפירא . של חברת המועצה אורנה ברביבאי אדוני ראש העיר, שאילתה מס"
    assert step4._non_topic_protocol_reason(
        headline=text,
        raw_text=text,
        structural_role="outline_item",
        packet_role="protocol",
    ) == "query_attribution_only"


def test_query_intro_speaker_prompt_without_subject_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="אדוני ראש העיר, שאילתה ראשונה בבקשה",
        raw_text="אדוני ראש העיר, שאילתה ראשונה בבקשה",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "query_intro_only"


def test_order_proposal_intro_without_subject_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="הצעה לסדר היום, אני אגיד לך על",
        raw_text="הצעה לסדר היום, אני אגיד לך על",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "order_proposal_intro_only"


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


def test_signature_page_with_inherited_context_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="אישור מועצת העירייה וכן לחתום על חוזה להקמת מבנה הציבור",
        raw_text="הישיבה נעולה הישיבה ננעלה בשעה 18:39 תצלום חתימות יו\"ר הישיבה ומנכ\"ל העירייה",
        structural_role="body",
        packet_role="protocol",
    ) == "signature_or_end_page_fragment"


def test_appendix_contract_approval_procedure_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="ח) הנ\"ל) יובאו לאישור ועדת התקשרויות עליונה ההתקשרות בחוזה ללא מכרז כאמור בתקנה נספח ג",
        raw_text="ח) הנ\"ל) יובאו לאישור ועדת התקשרויות עליונה ההתקשרות בחוזה ללא מכרז כאמור בתקנה נספח ג",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "contract_approval_procedure_fragment"


def test_approval_to_sign_contract_cleans_to_actual_subject() -> None:
    assert step4.clean_protocol_subject_text("אישור מועצת העירייה וכן לחתום על חוזה להקמת מבנה הציבור") == "הקמת מבנה הציבור"


def test_appendix_request_prefix_cleans_to_actual_subject() -> None:
    assert step4.clean_protocol_subject_text("נספח ב . בקשה לאישור ניהול משא ומתן עם ספקים פוטנציאליים לצורך התקשרות ללא מכרז, לאור אי") == "ניהול משא ומתן עם ספקים פוטנציאליים לצורך התקשרות ללא מכרז"


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


def test_tax_discount_committee_aligns_to_finance_root() -> None:
    assert step4.infer_root_topic_id("ועדת הנחות במיסים ומוסד מתנדב") == "root_budget_finance"


def test_canonical_subjects_align_to_non_procedural_roots() -> None:
    assert step4.infer_root_topic_id("מחזור והסברה סביבתית") == "root_infrastructure_environment"
    assert step4.infer_root_topic_id("אבטחת מידע והגנת פרטיות") == "root_security_enforcement"
    assert step4.infer_root_topic_id("טיפול בדרי רחוב") == "root_welfare_social"
    assert step4.infer_root_topic_id("הנחת מבנה יביל") == "root_planning_building"
    assert step4.infer_root_topic_id("בניית מבני ציבור ומתנ\"סים") == "root_planning_building"
    assert step4.infer_root_topic_id("ציוד מגן אישי לעובדים") == "root_hr_labor"
    assert step4.infer_root_topic_id("החזר תשלומי הורים") == "root_education"
    assert step4.infer_root_topic_id("הטבות בעקבות ירידה במדד חברתי-כלכלי") == "root_budget_finance"
    assert step4.infer_root_topic_id("סיוע לביטחון תזונתי") == "root_welfare_social"
    assert step4.infer_root_topic_id("מאגר מסרונים לתושבים") == "root_administration"
    assert step4.infer_root_topic_id("מסרי וידאו לתושבים") == "root_administration"
    assert step4.infer_root_topic_id("לוחות פרסום אלקטרוניים") == "root_infrastructure_environment"
    assert step4.infer_root_topic_id("מוכנות לרעידת אדמה ומערכת התראה") == "root_security_enforcement"
    assert step4.infer_root_topic_id("ועדה למיגור תופעת האלימות") == "root_security_enforcement"
    assert step4.infer_root_topic_id("טיפול בגורי חתולי רחוב") == "root_welfare_social"
    assert step4.infer_root_topic_id("תוכנית מדעניות העתיד") == "root_education"
    assert step4.infer_root_topic_id("קביעת שיעור היטל השבחה בפרויקטים לפינוי בינוי") == "root_budget_finance"
    assert step4.infer_root_topic_id("שימוע למהנדס העיר") == "root_administration"
    assert step4.infer_root_topic_id("אלרגיות ואפיפן במרחב הציבורי") == "root_security_enforcement"
    assert step4.infer_root_topic_id("הקמת פסל ציבורי") == "root_culture_sport"
