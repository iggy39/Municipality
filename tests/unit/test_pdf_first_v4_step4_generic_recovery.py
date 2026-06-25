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


def test_numbered_legal_tax_clause_is_not_protocol_listing() -> None:
    text = "4 )קבע מנהל הארנונה כי בשל נזק מלחמה נהרס נכס. 1( לא ישולם בגין נכס כאמור היטל. 2( נזק מלחמה כהגדרתו בחוק מס רכוש. 197 ', עמ8 ' דיני מדינת ישראל"

    assert step4._looks_like_protocol_listing(text) is False


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
    assert step4.clean_protocol_subject_text("תיקון מעלית המשכן לאומנויות הבמה המושבתת למעלה משנה וחצי") == "תיקון מעלית המשכן לאומנויות הבמה"
    assert step4.clean_protocol_subject_text("טיפול במערכת מיזוג במבנה ציבור שאינה פעילה מזה חודשים") == "טיפול במערכת מיזוג במבנה ציבור"
    assert step4.clean_protocol_subject_text("מזרקה בפארק המושבתת למעלה מחודש") == "מזרקה בפארק המושבתת למעלה מחודש"
    assert step4.clean_topic_label("תקציב 2026") == "תקציב"


def test_canonical_topic_label_shortens_reusable_subjects() -> None:
    cases = [
        (
            "הרכבת גוף בוחר לבחירת רב ראשי אורתודוכסי",
            "בחירת רב ראשי",
        ),
        (
            "עתיד מינויים ושינויים בוועדת מכרזים",
            "מינויים ושינויים בוועדת מכרזים",
        ),
        (
            "צעדים לתיקון ההתנהלות בתאגידים עירוניים בעקבות דו\"ח מבקר המדינה",
            "תיקון התנהלות בתאגידים עירוניים בעקבות דו\"ח מבקר המדינה",
        ),
        (
            "להלן–יפו (סלילת רחובות), תשע\"ב-בחוק עזר לתל–אביב .1 1 תיקון סעיף – 1 חוק העזר העיקרי), בסעיף",
            "תיקון חוק עזר סלילת רחובות",
        ),
        (
            "ההסתייגות היא בנושא תכנית שכונה כעיר, קיצוץ מתקציב התכנית לצמצום פערים",
            "קיצוץ תקציב תכנית שכונה כעיר",
        ),
        (
            "הצעה לסדר שעסקה בנושא המיגון",
            "מיגון",
        ),
        (
            "פרטי יציאת ראש העיר לחו\"ל מטעם הקונגרס היהודי",
            "נסיעה בתפקיד לחו\"ל",
        ),
        (
            "ה צפות חוזרות ונשנות ברחבי העיר- טיפול בתשתיות ופיצוי התושבים בעקבות נזקי ההצפות",
            "הצפות וטיפול בתשתיות",
        ),
        (
            "תיקון מעלית המשכן לאומנויות הבמה המושבתת למעלה משנה וחצי",
            "תיקון מעלית המשכן לאומנויות הבמה",
        ),
        (
            "הוספת תקנות עיר ירוקה להתקנת פאנלים סולאריים על גגות מבנים",
            "התקנת פאנלים סולאריים",
        ),
        (
            "פרסום אסור של מכירת דירות למגזר הציבור החרדי מחסידות בעלזא",
            "פרסום מכירת דירות",
        ),
        (
            "החלטה מספר2 – מקדמות לשנת2022 הועדה המקצועית ממליצה על תשלום מקדמה למוסד הפועל כעמותה",
            "מקדמות תמיכה לעמותות",
        ),
        (
            "פרוטוקול מישיבת ועדת תרומות מיום19.12.21 - עמותת אתנה",
            "ועדת תרומות",
        ),
        (
            "פרוטוקול מישיבת ועדת קליטה מיום29.11.21",
            "ועדת קליטה",
        ),
        (
            "פרוטוקול מישיבת הועדה למאבק בנגע הסמים המסוכנים מס1/21 מיום",
            "ועדה למאבק בנגע הסמים המסוכנים",
        ),
        (
            "מינוי מ 'נכ\"ל חב \"יובלים",
            "מינוי מנכ\"ל חברה עירונית",
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
            "דיון חוזר לאישור הסכם בין העירייה לבין עמותת משכנות שמעון בגוש חלקה ברחוב ברקת",
            "הסכם עם עמותה",
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
            "קריאת רחוב על שמו של זאב רווח ז\"ל",
            "קריאת רחוב על שמו של זאב רווח ז\"ל",
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
        (
            "פרוטוקול למחיקת חובות מס7.25 מיום25.12",
            "מחיקת חובות",
        ),
        (
            "שינוי קריטריונים בביטוח לאומי למתן טיפולים פרא רפואיים",
            "טיפולים פרא רפואיים",
        ),
        (
            "עדכון תקנוני הפרסים העירוניים ואישור תקנון פרס התיאטרון",
            "פרסים עירוניים",
        ),
        (
            "מינוי מ\"מ ראש העיר כדירקטור ויו\"ר דירקטוריון החברה העירונית לאנרגיה",
            "מינוי בדירקטוריון",
        ),
        (
            "אישור אינג' שמעון כצנלסון לשמש כנציג עיריית אשדוד בדיוני הוועדה המחוזית לתכנון ובנייה",
            "נציג בוועדה מחוזית לתכנון ובנייה",
        ),
        (
            "הסמכת עו )ד מירב ביטון לרשמת הנכסים של עיריית אשדוד (בן עדי",
            "הסמכת רשם נכסים",
        ),
        (
            "תחנת שאיבת ביוב ומאגר חירום לביוב ברובע יג",
            "תחנת שאיבת ביוב ומאגר חירום",
        ),
        (
            "זיהום אויר חמור ותלונות חוזרות על ריחות קשים ברחבי העיר",
            "זיהום אוויר וריחות",
        ),
        (
            "השבתת המזרקה המוזיקלית בפארק אשדוד ים",
            "השבתת מזרקה בפארק",
        ),
        (
            "ההשקעה הנמוכה בחינוך באשדוד- נתוני מחקר, תלונות הורים והצורך בתיקון מידי",
            "השקעה בחינוך",
        ),
        (
            "אופן זימון הורים לוועדות אפיון וזכאות",
            "ועדות אפיון וזכאות",
        ),
        (
            "ייעוד המקרקעין מבנים ומוסדות ציבור, למטרת הפעלת כיתת גן ילדים אחת",
            "ייעוד מקרקעין לכיתת גן",
        ),
        (
            "טיפול בבדידות קשישים עריריים, ניטור צריכת מים, לחצני מצוקה וחיישני תנועה",
            "טיפול בקשישים עריריים",
        ),
        (
            "מתן במה לאמנות מקומית ואמנים מקומיים בחגיגות ה לאשדוד",
            "אמנות מקומית",
        ),
        (
            "חלופה למאו ת בני נוער במקרה של סגירת קבוצת עירוני אשדוד",
            "חלופה לבני נוער בעקבות סגירת קבוצת ספורט",
        ),
        (
            "תכנית מס-1445311 איחוד וחלוקה ותוספת זכויות בנייה למבנה בית הדר",
            "תוכנית איחוד וחלוקה וזכויות בנייה",
        ),
        (
            "הסמכת החברה לתיירות אשדוד למימוש התוכנית לפתרון חניית קרוואנים",
            "תוכנית לפתרון חניית קרוואנים",
        ),
        (
            "כשל מתמשך בטיפול באגם המרינה ובחינת העברת האחריות",
            "טיפול באגם המרינה",
        ),
        (
            "לפעילות מלאה בחופי אשדוד",
            "פעילות בחופים",
        ),
        (
            "בע\"מ (מאור פוקס )הצטרפות ועשייה במקרקעין",
            "פעולה במקרקעין",
        ),
    ]

    for raw, expected in cases:
        evidence = "חיים ומזרקי אפיפן במרחב הציבורי" if raw.startswith("אלרגיות מסכנות דיון") else "תרומה להקמת פסל ע\"ש יאשה" if raw.startswith("תרומת") else None
        canonical, _reason = step4.canonicalize_topic_label(raw, evidence_text=evidence)
        assert canonical == expected


def test_residual_transcript_fragments_are_non_topics() -> None:
    cases = [
        ".כל הדברים האלה, נראה את המשמעויות, ואני מקווה שנקבל החלטות מושכלות",
        ",זה אחד האישורים שהיא צריכה לקבל. בגלל שאנחנו רוצים להקדים את זה ולעשות את זה",
        ") חברי האופוזיציה שלא נכחו בדיון את מינוי",
        ") חברי האופוזיציה שלא נכחו בדיון את פרוטוקול הוועדה למאבק בנגע הסמים",
        "הצעה לסדר הבאה. אין הצעה, אנחנו רק פועלים כמו שאנחנו פועלים. אנחנו נעשה אותו גם בלי קשר",
        "עו\"ד גבי כנפו. זה מחייב אישור מועצת עיר",
        "גב' דינה בר-אולפן: יצורף לפרוטוקול, אם תהיה תשובה במשך הישיבה תעביר אותה התשובה הועברה במייל",
    ]

    for text in cases:
        assert step4._non_topic_protocol_reason(
            headline=text,
            raw_text=text,
            structural_role="body",
            packet_role="protocol",
        ) in {
            "deliberation_continuation_fragment",
            "approval_timing_fragment",
            "attendance_context_fragment",
            "no_proposal_or_agenda_action_fragment",
            "approval_requirement_fragment",
            "reply_attachment_procedure_fragment",
        }


def test_unsupported_model_subject_in_protocol_listing_is_non_topic() -> None:
    item = {
        "structure_unit_id": "u_listing",
        "semantic_unit_id": "u_listing",
        "document_context": {"packet_role": "protocol"},
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "34.פרוטוקול מישיבת ועדת מחיקת חובות מס6/21 מיום2.12.21 – מצ\"ל 35. פרוטוקול מישיבת ועדת הנחות במיסים ומוסד מתנדב מס5/21 מיום3.1.22 – מצ\"ל 36. פרוטוקול מישיבת הועדה למאבק בנגע הסמים המסוכנים מס1/21 מיום13.10.21 – מצ\"ל",
        "topic_identification_context": "פרוטוקול מישיבת ועדת מחיקת חובות, ועדת הנחות במיסים והועדה למאבק בנגע הסמים",
        "topic_headline_he": "פרוטוקולים של ועדות",
        "topic_subject_he": "פרוטוקולים של ועדות",
    }
    parsed = {
        "root_topic_id": "root_hr_labor",
        "is_topic_bearing": True,
        "topic_subject_he": "עדכון שכר ל- 65% משכר מנכ\"ל לעוזר בכיר",
        "clean_subject_he": "עדכון שכר ל- 65% משכר מנכ\"ל לעוזר בכיר",
        "topic_supporting_quote_he": "עדכון שכר ל- 65% משכר מנכ\"ל לעוזר בכיר",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["is_topic_bearing"] is False
    assert assignment["topic_subject_he"] is None


def test_dicta_authoritative_keeps_valid_dicta_root_over_policy() -> None:
    item = {
        "structure_unit_id": "u_auth_policy",
        "semantic_unit_id": "u_auth_policy",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_authoritative",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "מינוי מנכ\"ל חב' יובלים",
        "topic_identification_context": "מינוי מנכ\"ל חב' יובלים",
        "topic_headline_he": "מינוי מנכ\"ל חב' יובלים",
        "topic_subject_he": "מינוי מנכ\"ל חב' יובלים",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_administration"},
        "root_topic_candidates": [{"root_topic_id": "root_administration", "score": 0.9}],
        "candidate_child_topics": [],
    }
    parsed = {
        "root_topic_id": "root_culture_sport",
        "is_topic_bearing": True,
        "topic_subject_he": "מינוי מנכ\"ל חב' יובלים",
        "clean_subject_he": "מינוי מנכ\"ל חב' יובלים",
        "topic_supporting_quote_he": "מינוי מנכ\"ל חב' יובלים",
        "confidence": 0.91,
        "rationale_he": "בדיקת מצב סמכותי: יש להשתמש בשורש שהמודל בחר.",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_culture_sport"
    assert assignment["topic_node_status"] == "active"
    assert assignment["root_adjudication_decision"] == "dicta_authoritative_root"
    assert assignment["dicta_raw_root_topic_id"] == "root_culture_sport"
    assert assignment["dicta_raw_subject_he"] == "מינוי מנכ\"ל חב' יובלים"


def test_dicta_authoritative_does_not_canonical_reparent_valid_root() -> None:
    item = {
        "structure_unit_id": "u_auth_canonical",
        "semantic_unit_id": "u_auth_canonical",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_authoritative",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "שאילתא בנושא מעקב זכויות אויר",
        "topic_identification_context": "מעקב זכויות אויר",
        "topic_headline_he": "מעקב זכויות אויר",
        "topic_subject_he": "מעקב זכויות אויר",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_planning_building"},
        "root_topic_candidates": [{"root_topic_id": "root_planning_building", "score": 0.9}],
        "candidate_child_topics": [],
    }
    parsed = {
        "root_topic_id": "root_agenda_queries",
        "is_topic_bearing": True,
        "topic_subject_he": "מעקב זכויות אויר",
        "clean_subject_he": "מעקב זכויות אויר",
        "topic_supporting_quote_he": "שאילתא בנושא מעקב זכויות אויר",
        "confidence": 0.88,
        "rationale_he": "בדיקת מצב סמכותי: שורש המודל נשמר גם אם ניקוי התווית מזהה תחום אחר.",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_agenda_queries"
    assert assignment["topic_subject_he"] == "זכויות אוויר"
    assert "canonical_root_reparent" not in assignment["topic_assignment_route"]
    assert assignment["topic_node_status"] == "active"


def test_contextual_payload_includes_child_choices_without_full_policy_dump() -> None:
    item = {
        "structure_unit_id": "u_contextual_payload",
        "semantic_unit_id": "u_contextual_payload",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "שאילתא בנושא פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_identification_context": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_headline_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_subject_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "root_topic_candidates": [
            {"root_topic_id": "root_mayor_updates", "root_label_he": "עדכוני ראש העיר", "score": 0.9},
            {"root_topic_id": "root_travel_approvals", "root_label_he": "אישורי נסיעות", "score": 0.84},
        ],
        "candidate_child_topics": [
            {
                "candidate_child_id": "child_travel",
                "label_he": "נסיעה בתפקיד לחו\"ל",
                "root_topic_id": "root_travel_approvals",
                "root_label_he": "אישורי נסיעות",
                "evidence_quote_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
                "evidence_source": "heading",
                "confidence_hint": 0.68,
                "aliases_he": ["פרטי יציאה לחו\"ל"],
            }
        ],
        "topic_policy_matches": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_payload",
        "is_standalone_topic": True,
        "agenda_status_he": None,
        "agenda_carrier_he": "שאילתא",
        "primary_action_he": "נסיעה בתפקיד לחו\"ל",
        "service_domain_he": None,
        "clean_subject_he": "נסיעה בתפקיד לחו\"ל",
        "event_summary_he": "בירור פרטי יציאה של סגן ראש העיר לחו\"ל",
        "supporting_quote_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "confidence": 0.8,
    }

    payload = step4._contextual_classification_payload(item=item, normalized_event=normalized_event, topic_tree=step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[]))

    assert "topic_policies" not in payload
    assert payload["candidate_child_choices"][0]["root_topic_id"] == "root_travel_approvals"
    travel_root = next(root for root in payload["allowed_root_topics"] if root["root_topic_id"] == "root_travel_approvals")
    assert any(example["child_label_he"] == "נסיעות עבודה לחו\"ל" for example in travel_root["child_examples"])


def test_thinking_model_keeps_thinking_prompt_and_small_model_no_think() -> None:
    assert step4._model_thinking_enabled("dicta-il/DictaLM-3.0-24B-Thinking:bf16") is True
    assert step4._model_thinking_enabled("dicta-il/DictaLM-3.0-1.7B-Thinking:latest") is False
    assert step4._model_system_prompt(model="dicta-il/DictaLM-3.0-24B-Thinking:bf16", prompt="/no_think\nReturn JSON only.") == "Return JSON only."
    assert step4._model_system_prompt(model="dicta-il/DictaLM-3.0-1.7B-Thinking:latest", prompt="Return JSON only.") == "/no_think\nReturn JSON only."


def test_contextual_accepts_unwrapped_single_assignment_json() -> None:
    item = {
        "structure_unit_id": "u_contextual_unwrapped",
        "semantic_unit_id": "u_contextual_unwrapped",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "דיון בנושא תקציב הגיל הרך",
        "topic_identification_context": "תקציב הגיל הרך",
        "topic_headline_he": "תקציב הגיל הרך",
        "topic_subject_he": "תקציב הגיל הרך",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_budget_finance"},
        "root_topic_candidates": [{"root_topic_id": "root_budget_finance", "score": 0.9}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_unwrapped",
        "standalone_event": True,
        "agenda_disposition": "discussed",
        "event_status": "discussed",
        "indexability_status": "standalone_topic",
        "is_standalone_topic": True,
        "agenda_status_he": None,
        "agenda_carrier_he": None,
        "municipal_action_he": "דיון תקציבי",
        "semantic_subject_he": "תקציב הגיל הרך",
        "primary_action_he": "דיון תקציבי",
        "service_domain_he": "חינוך",
        "classification_basis": "primary_action",
        "clean_subject_he": "תקציב הגיל הרך",
        "event_summary_he": "דיון תקציבי בנושא הגיל הרך.",
        "supporting_quote_he": "תקציב הגיל הרך",
        "confidence": 0.95,
    }
    response = {
        "structure_unit_id": "u_contextual_unwrapped",
        "agenda_disposition": "discussed",
        "event_status": "discussed",
        "indexability_status": "standalone_topic",
        "root_topic_id": "root_budget_finance",
        "is_topic_bearing": True,
        "topic_subject_he": "תקציב הגיל הרך",
        "clean_subject_he": "תקציב הגיל הרך",
        "classification_basis": "primary_action",
        "topic_supporting_quote_he": "תקציב הגיל הרך",
        "confidence": 0.95,
        "rationale_he": "הפעולה היא תקציבית.",
        "why_not_other_roots_he": "בדיקה",
        "competing_roots": [],
    }

    assert step4._missing_contextual_classification_fields(response) == []
    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert parsed["contextual_schema_valid"] is True
    assert assignment["root_topic_id"] == "root_budget_finance"
    assert assignment["topic_node_status"] == "active"


def test_contextual_schema_defaults_use_normalized_subject_before_validation() -> None:
    item = {
        "structure_unit_id": "u_contextual_defaults",
        "semantic_unit_id": "u_contextual_defaults",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "דיון בנושא הקמת מתנ\"ס ברובע ט\"ו",
        "topic_identification_context": "הקמת מתנ\"ס ברובע ט\"ו",
        "topic_headline_he": "הקמת מתנ\"ס ברובע ט\"ו",
        "topic_subject_he": "הקמת מתנ\"ס ברובע ט\"ו",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_planning_building"},
        "root_topic_candidates": [{"root_topic_id": "root_planning_building", "score": 0.9}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_defaults",
        "agenda_disposition": "discussed",
        "event_status": "discussed",
        "indexability_status": "standalone_topic",
        "clean_subject_he": "הקמת מתנ\"ס ברובע ט\"ו",
        "semantic_subject_he": "הקמת מתנ\"ס ברובע ט\"ו",
        "primary_action_he": "הקמת מתנ\"ס",
        "classification_basis": "primary_action",
        "supporting_quote_he": "דיון בנושא הקמת מתנ\"ס ברובע ט\"ו",
    }
    response = {
        "assignments": [
            {
                "structure_unit_id": "u_contextual_defaults",
                "root_topic_id": "root_planning_building",
                "is_topic_bearing": True,
                "confidence": 0.9,
            }
        ]
    }

    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert parsed["contextual_schema_valid"] is True
    assert assignment["topic_subject_he"] == "הקמת מתנ\"ס ברובע ט\"ו"
    assert assignment["topic_node_status"] == "active"


def test_quote_support_is_tolerant_to_ocr_quote_damage() -> None:
    item = {
        "raw_text": "העמדת לוח מודעות אלקטרוני \"בצומת הרחובות שדרות בגין ושדרות הרצל",
        "topic_identification_context": "שאילתא בנושא העמדת לוח מודעות אלקטרוני \"בצומת הרחובות שדרות בגין ושדרות הרצל",
        "explicit_actions": [],
        "referenced_attachment_contexts": [],
    }

    assert step4._quote_supported_by_item(
        item=item,
        quote="העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
    ) is True


def test_active_conversational_subject_is_demoted_to_non_topic() -> None:
    row = {
        "structure_unit_id": "u_dialogue_subject",
        "semantic_unit_id": "u_dialogue_subject",
        "packet_role": "protocol",
        "row_type": "topic_item",
        "structural_role": "body",
        "is_topic_bearing": True,
        "topic_node_status": "active",
        "root_topic_id": "root_administration",
        "root_label_he": "מנהל עירוני ומינויים",
        "topic_subject_he": "נאמר שגם ככה זה קיים בישיבות",
        "topic_headline_he": "נאמר שגם ככה זה קיים בישיבות",
        "topic_identification_context": "נאמר שגם ככה זה קיים בישיבות",
        "topic_assignment_route": "dictalm_v4_global_tree:dicta_contextual_root:root_only",
        "dicta_contextual_indexability_status": "standalone_topic",
        "dicta_contextual_event_status": "discussed",
    }

    normalized = step4._normalize_protocol_non_topic_assignments([row])[0]

    assert normalized["is_topic_bearing"] is False
    assert normalized["topic_subject_he"] is None
    assert "post_non_topic:active_conversational_subject" in normalized["topic_assignment_route"]


def test_dialogue_exchange_subject_with_commitment_word_is_demoted() -> None:
    row = {
        "structure_unit_id": "u_dialogue_commitment",
        "semantic_unit_id": "u_dialogue_commitment",
        "packet_role": "protocol",
        "row_type": "topic_item",
        "structural_role": "continuation",
        "is_topic_bearing": True,
        "topic_node_status": "active",
        "root_topic_id": "root_budget_finance",
        "root_label_he": "תקציב וכספים",
        "topic_subject_he": "נכתב כתב ההתחייבות, אחרי ההסכמה אתו",
        "topic_headline_he": "נכתב כתב ההתחייבות, אחרי ההסכמה אתו ד\"ר כהן?: למה הוא תרם את הקומה מרצונו הטוב עו\"ד לוי: כן",
        "topic_identification_context": "נכתב כתב ההתחייבות, אחרי ההסכמה אתו ד\"ר כהן?: למה הוא תרם את הקומה מרצונו הטוב עו\"ד לוי: כן",
        "topic_assignment_route": "dictalm_v4_global_tree:dicta_contextual_root:root_only",
        "dicta_contextual_indexability_status": "standalone_topic",
        "dicta_contextual_event_status": "discussed",
    }

    normalized = step4._normalize_protocol_non_topic_assignments([row])[0]

    assert normalized["is_topic_bearing"] is False
    assert "post_non_topic:active_conversational_subject" in normalized["topic_assignment_route"]


def test_model_error_fallback_is_review_candidate_without_strong_evidence() -> None:
    item = {
        "structure_unit_id": "u_model_error_fallback_weak",
        "semantic_unit_id": "u_model_error_fallback_weak",
        "document_context": {"packet_role": "protocol"},
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "דיון בנושא תקציב הגיל הרך",
        "topic_identification_context": "תקציב הגיל הרך",
        "topic_headline_he": "תקציב הגיל הרך",
        "topic_subject_he": "תקציב הגיל הרך",
        "root_topic_candidates": [{"root_topic_id": "root_budget_finance", "score": 0.72}],
        "candidate_child_topics": [],
    }

    assignment = step4._fallback_assignment(item, reason="model_error")

    assert assignment["topic_node_status"] == "candidate"
    assert assignment["topic_reject_reason"] == "non_blocking_topic_review:model_error"


def test_candidate_review_prefers_supported_non_procedural_root() -> None:
    item = {
        "structure_unit_id": "u_notice_board",
        "semantic_unit_id": "u_notice_board",
        "document_context": {"packet_role": "protocol"},
        "packet_role": "protocol",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "שאילתא בנושא העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
        "topic_identification_context": "העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
        "topic_headline_he": "העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
        "topic_subject_he": "העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
        "root_topic_candidates": [
            {"root_topic_id": "root_transport_safety", "score": 0.8428, "matched_terms": ["צומת", "רחובות", "שדרות"]},
            {"root_topic_id": "root_agenda_queries", "score": 0.54, "matched_terms": ["לוח", "מודעות"]},
        ],
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_agenda_queries", "confidence": 0.54},
        "candidate_child_topics": [],
    }

    assignment = step4._candidate_review_assignment(item=item, reason="dicta_conservative_review")

    assert assignment["root_topic_id"] != "root_agenda_queries"
    assert assignment["topic_node_status"] == "candidate"


def test_prompt_schema_converts_to_ollama_json_schema() -> None:
    schema = step4._json_schema_from_prompt_schema(
        {
            "status": "accepted|needs_review|rejected",
            "confidence": 0.0,
            "items": [{"name": "string", "enabled": "boolean|null"}],
        }
    )

    assert schema["type"] == "object"
    assert schema["properties"]["status"]["enum"] == ["accepted", "needs_review", "rejected"]
    assert schema["properties"]["items"]["items"]["properties"]["enabled"]["type"] == ["boolean", "null"]


def test_model_error_fallback_can_be_active_with_strong_deterministic_policy() -> None:
    item = {
        "structure_unit_id": "u_model_error_fallback_strong",
        "semantic_unit_id": "u_model_error_fallback_strong",
        "document_context": {"packet_role": "protocol"},
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "דיון בנושא תקציב הגיל הרך",
        "topic_identification_context": "תקציב הגיל הרך",
        "topic_headline_he": "תקציב הגיל הרך",
        "topic_subject_he": "תקציב הגיל הרך",
        "deterministic_topic_decision": {
            "action": "choose_existing_topic",
            "needs_dicta": False,
            "root_topic_id": "root_budget_finance",
            "policy_id": "budget_line_or_reserve",
            "confidence": 0.95,
        },
        "root_topic_candidates": [{"root_topic_id": "root_budget_finance", "score": 0.95, "policy_id": "budget_line_or_reserve"}],
        "candidate_child_topics": [],
    }

    assignment = step4._fallback_assignment(item, reason="model_error")

    assert assignment["topic_node_status"] == "active"
    assert assignment["topic_reject_reason"] is None
    assert "candidate_review:model_error" not in assignment["topic_assignment_route"]


def test_model_error_fallback_can_be_active_with_strong_child_decision_without_policy() -> None:
    item = {
        "structure_unit_id": "u_model_error_child_decision",
        "semantic_unit_id": "u_model_error_child_decision",
        "document_context": {"packet_role": "protocol"},
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "דו\"ח חובה על נסיעות בתפקיד לחו\"ל מטעם הרשות",
        "topic_identification_context": "דו\"ח חובה על נסיעות בתפקיד לחו\"ל מטעם הרשות",
        "topic_headline_he": "דו\"ח חובה על נסיעות בתפקיד לחו\"ל מטעם הרשות",
        "topic_subject_he": "נסיעה בתפקיד לחו\"ל",
        "deterministic_topic_decision": {
            "action": "choose_existing_topic",
            "needs_dicta": False,
            "root_topic_id": "root_travel_approvals",
            "child_choice_id": "child_travel",
            "confidence": 0.9,
            "matched_terms": ["נסיעה בתפקיד לחו\"ל"],
        },
        "root_topic_candidates": [{"root_topic_id": "root_travel_approvals", "score": 0.9}],
        "candidate_child_topics": [],
    }

    assignment = step4._fallback_assignment(item, reason="contextual_model_error")

    assert assignment["topic_node_status"] == "active"
    assert assignment["topic_reject_reason"] is None
    assert "candidate_review:contextual_model_error" not in assignment["topic_assignment_route"]


def test_contextual_incomplete_schema_high_confidence_root_can_be_active() -> None:
    item = {
        "structure_unit_id": "u_contextual_incomplete_schema_strong",
        "semantic_unit_id": "u_contextual_incomplete_schema_strong",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "body",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "ההסתייגות היא בנושא תכנית שכונה כעיר, קיצוץ מתקציב התכנית לצמצום פערים",
        "topic_identification_context": "ההסתייגות היא בנושא תכנית שכונה כעיר, קיצוץ מתקציב התכנית לצמצום פערים",
        "topic_headline_he": "הסתייגות בנושא תכנית שכונה כעיר",
        "topic_subject_he": "תכנית שכונה כעיר",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_planning_building", "confidence": 0.84},
        "root_topic_candidates": [{"root_topic_id": "root_planning_building", "score": 0.84}],
        "candidate_child_topics": [],
    }
    parsed = {
        "contextual_mode": "dicta_contextual",
        "contextual_schema_valid": False,
        "root_topic_id": "root_budget_finance",
        "is_topic_bearing": True,
        "topic_subject_he": "תכנית שכונה כעיר",
        "clean_subject_he": "תכנית שכונה כעיר",
        "primary_action_he": "קיצוץ תקציב",
        "topic_supporting_quote_he": "ההסתייגות היא בנושא תכנית שכונה כעיר",
        "confidence": 0.9,
        "rationale_he": "הנושא הוא הסתייגות תקציבית.",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_budget_finance"
    assert assignment["topic_subject_he"] == "קיצוץ תקציב תכנית שכונה כעיר"
    assert assignment["topic_node_status"] == "active"
    assert assignment["topic_reject_reason"] is None
    assert "dicta_contextual_root_recovered:dicta_contextual_incomplete_schema_root" in assignment["topic_assignment_route"]


def test_contextual_missing_confidence_can_be_active_with_strong_child_evidence() -> None:
    item = {
        "structure_unit_id": "u_contextual_missing_confidence_child",
        "semantic_unit_id": "u_contextual_missing_confidence_child",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "body",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "חילופי גברי בוועדות ובדירקטוריונים",
        "topic_identification_context": "חילופי גברי בוועדות ובדירקטוריונים",
        "topic_headline_he": "חילופי גברי בוועדות ובדירקטוריונים",
        "topic_subject_he": "חילופי גברי בוועדות ובדירקטוריונים",
        "deterministic_topic_decision": {
            "action": "needs_judge",
            "needs_dicta": True,
            "reason": "ambiguous_candidates",
            "root_topic_id": "root_administration",
            "child_choice_id": "child_replacement",
            "confidence": 0.94,
            "matched_terms": ["חילופי", "גברי"],
        },
        "root_topic_candidates": [{"root_topic_id": "root_administration", "score": 0.94}],
        "candidate_child_topics": [
            {
                "candidate_child_id": "child_replacement",
                "root_topic_id": "root_administration",
                "root_label_he": "מנהל עירוני ומינויים",
                "label_he": "חילופי גברי",
                "evidence_source": "existing_tree",
                "evidence_quote_he": "חילופי גברי בוועדות ובדירקטוריונים",
                "confidence_hint": 0.94,
                "aliases_he": [],
            }
        ],
    }
    parsed = {
        "contextual_mode": "dicta_contextual",
        "contextual_schema_valid": True,
        "root_topic_id": "root_administration",
        "is_topic_bearing": True,
        "topic_subject_he": "חילופי גברי בוועדות ובדירקטוריונים",
        "clean_subject_he": "חילופי גברי בוועדות ובדירקטוריונים",
        "topic_supporting_quote_he": "חילופי גברי בוועדות ובדירקטוריונים",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_administration"
    assert assignment["child_label_he"] == "חילופי גברי"
    assert assignment["topic_node_status"] == "active"
    assert assignment["topic_reject_reason"] is None
    assert "dicta_contextual_root_recovered:dicta_contextual_missing_confidence_root" in assignment["topic_assignment_route"]


def test_contextual_missing_confidence_is_not_authoritative() -> None:
    item = {
        "structure_unit_id": "u_contextual_untrusted",
        "semantic_unit_id": "u_contextual_untrusted",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "שאילתא בנושא פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_identification_context": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_headline_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_subject_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_mayor_updates"},
        "root_topic_candidates": [{"root_topic_id": "root_mayor_updates", "score": 0.9}],
        "candidate_child_topics": [],
    }
    parsed = {
        "contextual_mode": "dicta_contextual",
        "contextual_schema_valid": False,
        "root_topic_id": "root_mayor_updates",
        "is_topic_bearing": True,
        "topic_subject_he": "נסיעה בתפקיד לחו\"ל",
        "clean_subject_he": "נסיעה בתפקיד לחו\"ל",
        "topic_supporting_quote_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_travel_approvals"
    assert assignment["topic_node_status"] == "candidate"
    assert assignment["dicta_confidence"] is None


def test_contextual_high_confidence_schema_valid_root_is_authoritative() -> None:
    item = {
        "structure_unit_id": "u_contextual_trusted",
        "semantic_unit_id": "u_contextual_trusted",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "שאילתא בנושא פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_identification_context": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_headline_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_subject_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_mayor_updates"},
        "root_topic_candidates": [{"root_topic_id": "root_mayor_updates", "score": 0.9}],
        "candidate_child_topics": [],
    }
    parsed = {
        "contextual_mode": "dicta_contextual",
        "contextual_schema_valid": True,
        "root_topic_id": "root_travel_approvals",
        "is_topic_bearing": True,
        "topic_subject_he": "נסיעה בתפקיד לחו\"ל",
        "clean_subject_he": "נסיעה בתפקיד לחו\"ל",
        "topic_supporting_quote_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "confidence": 0.86,
        "rationale_he": "הפעולה המרכזית היא נסיעה בתפקיד לחו\"ל ולכן שורש אישורי נסיעות מתאים יותר מעדכוני ראש העיר.",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_travel_approvals"
    assert assignment["topic_node_status"] == "active"
    assert assignment["root_adjudication_decision"] == "dicta_contextual_root"


def test_contextual_non_indexable_conflict_becomes_review_candidate() -> None:
    item = {
        "structure_unit_id": "u_contextual_status_conflict",
        "semantic_unit_id": "u_contextual_status_conflict",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "סעיף בנושא הסכם רשות שנדון בהקשר סטטוס בלבד",
        "topic_identification_context": "הסכם רשות לעמותה",
        "topic_headline_he": "הסכם רשות לעמותה",
        "topic_subject_he": "הסכם רשות לעמותה",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_agreements"},
        "root_topic_candidates": [{"root_topic_id": "root_agreements", "score": 0.9}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_status_conflict",
        "standalone_event": False,
        "agenda_disposition": "deferred",
        "event_status": "deferred",
        "indexability_status": "duplicate_reference",
        "is_standalone_topic": False,
        "agenda_status_he": "סטטוס בלבד",
        "agenda_carrier_he": None,
        "municipal_action_he": None,
        "semantic_subject_he": "הסכם רשות לעמותה",
        "primary_action_he": None,
        "service_domain_he": None,
        "clean_subject_he": "הסכם רשות לעמותה",
        "duplicate_of_unit_id": "u_original_agreement",
        "event_summary_he": "שורת סטטוס לגבי הסכם רשות לעמותה",
        "supporting_quote_he": "הסכם רשות לעמותה",
        "confidence": 0.9,
    }
    response = {
        "assignments": [
            {
                "structure_unit_id": "u_contextual_status_conflict",
                "agenda_disposition": "deferred",
                "event_status": "deferred",
                "indexability_status": "duplicate_reference",
                "root_topic_id": "root_agreements",
                "is_topic_bearing": True,
                "topic_subject_he": "הסכם רשות לעמותה",
                "clean_subject_he": "הסכם רשות לעמותה",
                "classification_basis": "primary_action",
                "duplicate_of_unit_id": "u_original_agreement",
                "topic_supporting_quote_he": "הסכם רשות לעמותה",
                "confidence": 0.91,
                "rationale_he": "בדיקה סינתטית: המודל סימן גם סטטוס לא יבוא וגם נושא פעיל.",
                "why_not_other_roots_he": "בדיקה",
                "competing_roots": [],
            }
        ]
    }

    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["is_topic_bearing"] is True
    assert assignment["topic_node_status"] == "candidate"
    assert assignment["dicta_contextual_event_status"] == "deferred"
    assert assignment["dicta_contextual_indexability_status"] == "duplicate_reference"
    assert assignment["dicta_contextual_consistency_warning"] == "topic_bearing_with_non_indexable_status:duplicate_reference"


def test_contextual_optional_audit_fields_do_not_block_authoritative_root() -> None:
    item = {
        "structure_unit_id": "u_contextual_optional_missing",
        "semantic_unit_id": "u_contextual_optional_missing",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "דיון בנושא תקציב הגיל הרך",
        "topic_identification_context": "תקציב הגיל הרך",
        "topic_headline_he": "תקציב הגיל הרך",
        "topic_subject_he": "תקציב הגיל הרך",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_budget_finance"},
        "root_topic_candidates": [{"root_topic_id": "root_budget_finance", "score": 0.9}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_optional_missing",
        "standalone_event": True,
        "agenda_disposition": "approved",
        "event_status": "approved",
        "indexability_status": "standalone_topic",
        "is_standalone_topic": True,
        "agenda_status_he": None,
        "agenda_carrier_he": None,
        "municipal_action_he": "אישור תקציב",
        "semantic_subject_he": "תקציב הגיל הרך",
        "primary_action_he": "אישור תקציב",
        "service_domain_he": "חינוך",
        "classification_basis": "primary_action",
        "clean_subject_he": "תקציב הגיל הרך",
        "event_summary_he": "דיון תקציבי בנושא הגיל הרך.",
        "supporting_quote_he": "תקציב הגיל הרך",
        "confidence": 0.95,
    }
    response = {
        "assignments": [
            {
                "structure_unit_id": "u_contextual_optional_missing",
                "root_topic_id": "root_budget_finance",
                "is_topic_bearing": True,
                "topic_subject_he": "תקציב הגיל הרך",
                "confidence": 0.95,
            }
        ]
    }

    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert parsed["contextual_schema_valid"] is True
    assert "rationale_he" in assignment["dicta_contextual_missing_optional_fields"]
    assert "competing_roots" in assignment["dicta_contextual_missing_optional_fields"]
    assert assignment["dicta_contextual_rationale_fallback"] is True
    assert assignment["topic_node_status"] == "active"
    assert assignment["root_topic_id"] == "root_budget_finance"


def test_contextual_deferred_standalone_topic_remains_importable() -> None:
    item = {
        "structure_unit_id": "u_contextual_deferred_budget",
        "semantic_unit_id": "u_contextual_deferred_budget",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "הסתייגות בעניין קיצוץ בתקציב המשפחתונים לנשים עובדות נדחתה",
        "topic_identification_context": "תקציב המשפחתונים לנשים עובדות",
        "topic_headline_he": "תקציב המשפחתונים לנשים עובדות",
        "topic_subject_he": "תקציב המשפחתונים לנשים עובדות",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_budget_finance"},
        "root_topic_candidates": [{"root_topic_id": "root_budget_finance", "score": 0.98}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_deferred_budget",
        "standalone_event": False,
        "agenda_disposition": "deferred",
        "event_status": "deferred",
        "indexability_status": "standalone_topic",
        "is_standalone_topic": False,
        "agenda_status_he": "נדחתה",
        "agenda_carrier_he": None,
        "municipal_action_he": None,
        "semantic_subject_he": "תקציב המשפחתונים לנשים עובדות",
        "primary_action_he": "קיצוץ תקציבי",
        "service_domain_he": None,
        "classification_basis": "primary_action",
        "clean_subject_he": "תקציב המשפחתונים לנשים עובדות",
        "event_summary_he": "הסתייגות תקציבית שנדחתה אך יש לה נושא תקציבי עצמאי.",
        "supporting_quote_he": "קיצוץ בתקציב המשפחתונים לנשים עובדות",
        "confidence": 0.96,
    }
    response = {
        "assignments": [
            {
                "structure_unit_id": "u_contextual_deferred_budget",
                "agenda_disposition": "deferred",
                "event_status": "deferred",
                "indexability_status": "standalone_topic",
                "root_topic_id": "root_budget_finance",
                "is_topic_bearing": False,
                "topic_subject_he": "תקציב המשפחתונים לנשים עובדות",
                "clean_subject_he": "תקציב המשפחתונים לנשים עובדות",
                "primary_action_he": "קיצוץ תקציבי",
                "service_domain_he": None,
                "classification_basis": "primary_action",
                "topic_supporting_quote_he": "קיצוץ בתקציב המשפחתונים לנשים עובדות",
                "confidence": 0.96,
                "rationale_he": "הדיספוזיציה היא דחייה, אך הנושא התקציבי עצמו עצמאי ובר סיווג.",
                "why_not_other_roots_he": "הבסיס הוא פעולה תקציבית ולא שירות ישיר.",
                "competing_roots": [],
            }
        ]
    }

    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["is_topic_bearing"] is True
    assert assignment["root_topic_id"] == "root_budget_finance"
    assert assignment["topic_node_status"] == "active"
    assert assignment["dicta_contextual_event_status"] == "deferred"
    assert assignment["dicta_contextual_indexability_status"] == "standalone_topic"
    assert assignment["dicta_contextual_consistency_warning"] is None


def test_contextual_unsupported_duplicate_reference_reverts_to_standalone_topic() -> None:
    item = {
        "structure_unit_id": "u_contextual_nonindexable_budget",
        "semantic_unit_id": "u_contextual_nonindexable_budget",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "הסתייגות בעניין קיצוץ בתקציב המשפחתונים לנשים עובדות",
        "topic_identification_context": "תקציב המשפחתונים לנשים עובדות",
        "topic_headline_he": "תקציב המשפחתונים לנשים עובדות",
        "topic_subject_he": "תקציב המשפחתונים לנשים עובדות",
        "topic_policy_matches": [{"policy_id": "budget_line_or_reserve", "root_topic_id": "root_budget_finance"}],
        "deterministic_topic_decision": {"needs_dicta": False, "reason": "strong_policy_match", "root_topic_id": "root_budget_finance"},
        "root_topic_candidates": [{"root_topic_id": "root_budget_finance", "score": 0.98, "policy_id": "budget_line_or_reserve"}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_nonindexable_budget",
        "standalone_event": False,
        "agenda_disposition": "discussed",
        "event_status": "discussed",
        "indexability_status": "duplicate_reference",
        "is_standalone_topic": False,
        "agenda_status_he": None,
        "agenda_carrier_he": None,
        "municipal_action_he": None,
        "semantic_subject_he": "תקציב המשפחתונים לנשים עובדות",
        "primary_action_he": None,
        "service_domain_he": None,
        "classification_basis": "service_domain",
        "clean_subject_he": "תקציב המשפחתונים לנשים עובדות",
        "event_summary_he": "הסתייגות תקציבית עם נושא עצמאי.",
        "supporting_quote_he": "בתקציב המשפחתונים לנשים עובדות",
        "confidence": 0.96,
    }
    response = {
        "assignments": [
            {
                "structure_unit_id": "u_contextual_nonindexable_budget",
                "agenda_disposition": "discussed",
                "event_status": "discussed",
                "indexability_status": "duplicate_reference",
                "root_topic_id": "root_budget_finance",
                "is_topic_bearing": False,
                "topic_subject_he": "תקציב וכספים",
                "clean_subject_he": "תקציב וכספים",
                "primary_action_he": None,
                "service_domain_he": None,
                "classification_basis": "unclear",
                "policy_id": "budget_line_or_reserve",
                "topic_supporting_quote_he": "בתקציב המשפחתונים לנשים עובדות",
                "confidence": 0.98,
                "rationale_he": "המודל בחר שורש תקציבי אבל סימן בטעות שהשורה אינה אינדקסבילית.",
                "why_not_other_roots_he": "פעולה תקציבית.",
                "competing_roots": [],
            }
        ]
    }

    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["is_topic_bearing"] is True
    assert assignment["root_topic_id"] == "root_budget_finance"
    assert assignment["topic_subject_he"] == "תקציב המשפחתונים לנשים עובדות"
    assert assignment["topic_node_status"] == "active"
    assert assignment["dicta_contextual_indexability_status"] == "standalone_topic"
    assert assignment["dicta_contextual_original_indexability_status"] == "duplicate_reference"
    assert assignment["dicta_contextual_indexability_override_reason"] == "unsupported_duplicate_reference"
    assert assignment["dicta_contextual_consistency_warning"] is None


def test_contextual_action_domain_conflict_adds_competing_root_and_clear_rationale() -> None:
    item = {
        "structure_unit_id": "u_contextual_action_domain_conflict",
        "semantic_unit_id": "u_contextual_action_domain_conflict",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "קיצוץ משמעותי בתקציב גני ילדים",
        "topic_identification_context": "גני ילדים",
        "topic_headline_he": "גני ילדים",
        "topic_subject_he": "גני ילדים",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_education"},
        "root_topic_candidates": [{"root_topic_id": "root_education", "score": 0.9}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_action_domain_conflict",
        "standalone_event": True,
        "agenda_disposition": "discussed",
        "event_status": "discussed",
        "indexability_status": "standalone_topic",
        "is_standalone_topic": True,
        "agenda_status_he": None,
        "agenda_carrier_he": None,
        "municipal_action_he": "קיצוץ תקציבי",
        "semantic_subject_he": "גני ילדים",
        "primary_action_he": "קיצוץ תקציבי",
        "service_domain_he": "חינוך",
        "classification_basis": "primary_action",
        "clean_subject_he": "גני ילדים",
        "event_summary_he": "קיצוץ תקציבי בתחום גני הילדים.",
        "supporting_quote_he": "קיצוץ משמעותי בתקציב גני ילדים",
        "confidence": 0.97,
    }
    response = {
        "assignments": [
            {
                "structure_unit_id": "u_contextual_action_domain_conflict",
                "agenda_disposition": "discussed",
                "event_status": "discussed",
                "indexability_status": "standalone_topic",
                "root_topic_id": "root_education",
                "is_topic_bearing": True,
                "topic_subject_he": "גני ילדים",
                "clean_subject_he": "גני ילדים",
                "primary_action_he": "קיצוץ תקציבי",
                "service_domain_he": "חינוך",
                "classification_basis": "primary_action",
                "topic_supporting_quote_he": "קיצוץ משמעותי בתקציב גני ילדים",
                "confidence": 0.97,
                "rationale_he": "המודל בחר חינוך לפי תחום השירות.",
                "why_not_other_roots_he": "בדיקה",
                "competing_roots": [],
            }
        ]
    }

    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_budget_finance"
    assert any(row["root_topic_id"] == "root_education" for row in assignment["dicta_contextual_competing_roots"])
    assert "שורש המודל המקורי היה חינוך" in assignment["rationale_he"]
    assert "שכבת האימות בחרה בשורש תקציב וכספים" in assignment["rationale_he"]


def test_contextual_exit_publication_is_not_travel_action() -> None:
    assert step4._contextual_action_root_from_text("יציאה לפרסום לקבלת המלצות למועמדים") is None
    assert step4._contextual_action_root_from_text("פרטי יציאת ראש העיר לחו\"ל מטעם העירייה") == "root_travel_approvals"


def test_contextual_committee_domain_beats_generic_governance_carrier() -> None:
    assert step4._contextual_action_root_from_text("פרוטוקול מישיבת ועדת רווחה") == "root_welfare_social"
    assert step4._contextual_action_root_from_text("פרוטוקול מישיבת הועדה למאבק בנגע הסמים המסוכנים") == "root_security_enforcement"
    assert step4._contextual_action_root_from_text("עדכון תבחיני ספורט") == "root_supports"
    assert step4._contextual_action_root_from_text("מינויים ושינויים בוועדת מכרזים") == "root_administration"


def test_generic_committee_protocol_carrier_is_demoted_but_domain_committee_survives() -> None:
    generic = "פרוטוקול הוועדה המקצועית מספר"
    domain = "פרוטוקול מישיבת ועדת רווחה"

    assert step4._looks_like_procedural_carrier_heading(generic) is True
    assert step4._non_topic_protocol_reason(headline=generic, raw_text=generic, structural_role="outline_item", packet_role="protocol") == "procedural_carrier_heading"
    assert step4._looks_like_procedural_carrier_heading(domain) is False
    assert step4._non_topic_protocol_reason(headline=domain, raw_text=domain, structural_role="outline_item", packet_role="protocol") is None


def test_contextual_removed_agenda_topic_becomes_non_blocking_candidate() -> None:
    item = {
        "structure_unit_id": "u_contextual_removed_agenda",
        "semantic_unit_id": "u_contextual_removed_agenda",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "סעיפי הסכמי רשות ופיתוח הוסרו מסדר היום",
        "topic_identification_context": "סעיפי הסכמי רשות ופיתוח",
        "topic_headline_he": "סעיפי הסכמי רשות ופיתוח",
        "topic_subject_he": "סעיפי הסכמי רשות ופיתוח",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_agreements"},
        "root_topic_candidates": [{"root_topic_id": "root_agreements", "score": 0.9}],
        "candidate_child_topics": [],
    }
    parsed = {
        "contextual_mode": "dicta_contextual",
        "contextual_schema_valid": True,
        "contextual_agenda_disposition": "removed",
        "contextual_event_status": "removed",
        "contextual_indexability_status": "standalone_topic",
        "root_topic_id": "root_agreements",
        "is_topic_bearing": True,
        "topic_subject_he": "סעיפי הסכמי רשות ופיתוח",
        "clean_subject_he": "סעיפי הסכמי רשות ופיתוח",
        "classification_basis": "primary_action",
        "topic_supporting_quote_he": "סעיפי הסכמי רשות ופיתוח הוסרו מסדר היום",
        "confidence": 0.93,
        "rationale_he": "הנושא עצמו הוא הסכמי רשות ופיתוח, אך הוא סומן כמוסר מסדר היום.",
        "why_not_other_roots_he": "הפעולה היא הסכמית.",
        "competing_roots": [],
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_agreements"
    assert assignment["topic_node_status"] == "candidate"
    assert assignment["topic_reject_reason"] == "non_blocking_topic_review:dicta_contextual_removed_agenda_topic"
    assert "contextual_agenda_review" in assignment["topic_assignment_route"]


def test_contextual_approved_non_standalone_event_can_be_active() -> None:
    item = {
        "structure_unit_id": "u_contextual_approved_fragment",
        "semantic_unit_id": "u_contextual_approved_fragment",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "פרוטוקול מישיבת ועדת שמות אושר",
        "topic_identification_context": "פרוטוקול מישיבת ועדת שמות",
        "topic_headline_he": "פרוטוקול מישיבת ועדת שמות",
        "topic_subject_he": "ועדת שמות",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_administration"},
        "root_topic_candidates": [{"root_topic_id": "root_administration", "score": 0.9}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_approved_fragment",
        "standalone_event": False,
        "agenda_disposition": "approved",
        "event_status": "approved",
        "indexability_status": "standalone_topic",
        "is_standalone_topic": False,
        "agenda_status_he": "אושר",
        "agenda_carrier_he": "פרוטוקול ועדה",
        "municipal_action_he": None,
        "semantic_subject_he": "ועדת שמות",
        "primary_action_he": None,
        "service_domain_he": None,
        "classification_basis": "governance_status",
        "clean_subject_he": "ועדת שמות",
        "event_summary_he": "אישור פרוטוקול ועדת שמות",
        "supporting_quote_he": "פרוטוקול מישיבת ועדת שמות אושר",
        "confidence": 0.9,
    }
    response = {
        "assignments": [
            {
                "structure_unit_id": "u_contextual_approved_fragment",
                "agenda_disposition": "approved",
                "event_status": "approved",
                "indexability_status": "standalone_topic",
                "root_topic_id": "root_administration",
                "is_topic_bearing": True,
                "topic_subject_he": "ועדת שמות",
                "clean_subject_he": "ועדת שמות",
                "classification_basis": "governance_status",
                "topic_supporting_quote_he": "פרוטוקול מישיבת ועדת שמות אושר",
                "confidence": 0.91,
                "rationale_he": "אישור פרוטוקול ועדת שמות הוא פעולה מועצתית בתחום מנהל עירוני.",
                "why_not_other_roots_he": "אין שורש מתאים יותר מהתחום המנהלי.",
                "competing_roots": [],
            }
        ]
    }

    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["is_topic_bearing"] is True
    assert assignment["topic_node_status"] == "active"
    assert assignment["dicta_contextual_event_status"] == "approved"
    assert assignment["dicta_contextual_consistency_warning"] is None


def test_contextual_low_confidence_valid_root_is_preserved_as_candidate() -> None:
    item = {
        "structure_unit_id": "u_contextual_low_conf_root",
        "semantic_unit_id": "u_contextual_low_conf_root",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "פרוטוקול מישיבת ועדת שמות מס 4/21 מיום 26.12.21",
        "topic_identification_context": "פרוטוקול מישיבת ועדת שמות",
        "topic_headline_he": "פרוטוקול מישיבת ועדת שמות",
        "topic_subject_he": "פרוטוקול מישיבת ועדת שמות",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "weak_candidate", "root_topic_id": "root_administration"},
        "root_topic_candidates": [{"root_topic_id": "root_administration", "score": 0.45}],
        "candidate_child_topics": [],
    }
    parsed = {
        "contextual_mode": "dicta_contextual",
        "contextual_schema_valid": True,
        "contextual_event_status": "approved",
        "contextual_indexability_status": "standalone_topic",
        "root_topic_id": "root_administration",
        "is_topic_bearing": True,
        "topic_subject_he": "ועדת שמות",
        "clean_subject_he": "ועדת שמות",
        "classification_basis": "governance_status",
        "topic_supporting_quote_he": "פרוטוקול מישיבת ועדת שמות",
        "confidence": 0.5,
        "rationale_he": "הנושא הוא פרוטוקול של ועדת שמות בתחום מנהל עירוני.",
        "why_not_other_roots_he": "אין בסיס לתמיכות או לשורש אחר.",
        "competing_roots": [{"root_topic_id": "root_supports", "confidence": 0.42, "reason_he": "שורש מתחרה חלש בלבד."}],
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_administration"
    assert assignment["topic_node_status"] == "candidate"
    assert assignment["topic_reject_reason"] == "non_blocking_topic_review:dicta_contextual_low_confidence_root"
    assert assignment["dicta_contextual_competing_roots"][0]["root_topic_id"] == "root_supports"


def test_contextual_rejected_generic_committee_label_becomes_non_topic() -> None:
    item = {
        "structure_unit_id": "u_contextual_bad_label_valid_root",
        "semantic_unit_id": "u_contextual_bad_label_valid_root",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "פרוטוקול מישיבת ועדה מקצועית מס 9/21 מיום",
        "topic_identification_context": "פרוטוקול מישיבת ועדה מקצועית מס 9/21 מיום",
        "topic_headline_he": "פרוטוקול מישיבת ועדה מקצועית מס 9/21 מיום",
        "topic_subject_he": "פרוטוקול מישיבת ועדה מקצועית מס 9/21 מיום",
        "deterministic_topic_decision": {"needs_dicta": False, "reason": "strong_policy_match", "root_topic_id": "root_supports"},
        "root_topic_candidates": [{"root_topic_id": "root_supports", "score": 0.98}],
        "candidate_child_topics": [],
    }
    parsed = {
        "contextual_mode": "dicta_contextual",
        "contextual_schema_valid": True,
        "contextual_event_status": "approved",
        "contextual_indexability_status": "standalone_topic",
        "root_topic_id": "root_supports",
        "is_topic_bearing": True,
        "topic_subject_he": "פרוטוקול מישיבת ועדה מקצועית מס 9 21 מיום",
        "clean_subject_he": "פרוטוקול מישיבת ועדה מקצועית מס 9 21 מיום",
        "classification_basis": "service_domain",
        "topic_supporting_quote_he": "פרוטוקול מישיבת ועדה מקצועית מס 9/21 מיום",
        "confidence": 0.98,
        "rationale_he": "הוועדה עוסקת בתמיכות ולכן השורש תקף גם אם הכותרת דורשת ניקוי ידני.",
        "why_not_other_roots_he": "אין שורש מתאים יותר.",
        "competing_roots": [],
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)
    normalized = step4._normalize_protocol_non_topic_assignments([assignment])[0]

    assert normalized["is_topic_bearing"] is False
    assert normalized["row_type"] == "fragment"
    assert normalized["root_topic_id"] == "root_agenda_queries"
    assert normalized["topic_node_status"] == "active"
    assert normalized["topic_subject_he"] is None
    assert normalized["topic_reject_reason"] is None
    assert "post_non_topic:procedural_carrier_heading" in normalized["topic_assignment_route"]


def test_rejected_procedural_subject_can_be_demoted_after_assignment() -> None:
    row = {
        "topic_reject_reason": "non_blocking_topic_review:topic_subject_rejected:procedural_or_dialogue_label",
        "topic_subject_he": None,
        "topic_headline_he": "תיקון טעות קולמוס בפרוטוקול מועצה",
        "topic_identification_context": "תיקון טעות קולמוס בפרוטוקול מועצה",
        "topic_supporting_quote_he": "תיקון טעות קולמוס בפרוטוקול מועצה",
        "structural_role": "outline_item",
        "packet_role": "protocol",
        "topic_node_status": "candidate",
        "root_topic_id": "root_administration",
    }

    assert step4._post_assignment_non_topic_reason(row) == "rejected_procedural_subject"


def test_rejected_supported_agreement_subject_is_not_demoted_to_fragment() -> None:
    row = {
        "topic_reject_reason": "non_blocking_topic_review:topic_subject_rejected:unresolved_noisy_label",
        "topic_subject_he": None,
        "raw_topic_subject_he": "דיון חוזר לאישור הסכם בין העירייה לבין עמותת משכנות שמעון בגוש חלקה",
        "topic_headline_he": "דיון חוזר לאישור הסכם בין העירייה לבין עמותת משכנות שמעון בגוש חלקה",
        "topic_identification_context": "דיון חוזר לאישור הסכם בין העירייה לבין עמותת משכנות שמעון בגוש חלקה",
        "topic_supporting_quote_he": "לאשר הסכם בין העירייה לבין עמותת משכנות שמעון",
        "structural_role": "body",
        "packet_role": "protocol",
        "topic_node_status": "candidate",
        "root_topic_id": "root_agreements",
    }

    assert step4._post_assignment_non_topic_reason(row) is None


def test_rejected_budget_tbr_subject_gets_compact_repair_candidate() -> None:
    repaired, reason = step4._repair_rejected_topic_subject(
        raw_subject="ביטול תב\"ר פארק בין מגדלי היוקרה החדשים בכיכר המדינה",
        item={
            "topic_subject_he": "ביטול תב\"ר פארק בין מגדלי היוקרה החדשים בכיכר המדינה",
            "topic_headline_he": "ביטול תב\"ר פארק בין מגדלי היוקרה החדשים בכיכר המדינה",
            "topic_identification_context": "ביטול תב\"ר פארק בין מגדלי היוקרה החדשים בכיכר המדינה",
            "raw_text": "ביטול תב\"ר פארק בין מגדלי היוקרה החדשים בכיכר המדינה",
        },
        root_label="תקציב וכספים",
        quote="ביטול תב\"ר פארק בין מגדלי היוקרה החדשים בכיכר המדינה",
    )

    assert repaired == "ביטול תב\"ר פארק בכיכר המדינה"
    assert reason == "subject_repaired:alternate_source"


def test_rejected_procurement_subject_uses_action_span_repair() -> None:
    raw = "הסבר כללי. ניהול משא ומתן עם ספקים פוטנציאליים לצורך התקשרות ללא מכרז, לאור אי קבלת הצעות"
    repaired, reason = step4._repair_rejected_topic_subject(
        raw_subject=raw,
        item={
            "topic_subject_he": raw,
            "topic_headline_he": raw,
            "topic_identification_context": raw,
            "raw_text": raw,
        },
        root_label="הסכמים והתקשרויות",
        quote=raw,
    )

    assert repaired == "ניהול משא ומתן להתקשרות ללא מכרז"
    assert reason == "subject_repaired:semantic_canonicalized"


def test_rejected_agreement_subject_uses_generic_party_type_repair() -> None:
    raw = "דיון חוזר לאישור הסכם בין העירייה לבין עמותת משכנות שמעון וברית מרים בגוש חלקה ברחוב ברקת מחליטים פה אחד"
    repaired, reason = step4._repair_rejected_topic_subject(
        raw_subject=raw,
        item={
            "topic_subject_he": raw,
            "topic_headline_he": raw,
            "topic_identification_context": raw,
            "raw_text": raw,
        },
        root_label="הסכמים והתקשרויות",
        quote=raw,
    )

    assert repaired == "הסכם עם עמותה"
    assert reason in {"subject_repaired:semantic_canonicalized", "subject_repaired:alternate_source"}


def test_rejected_public_building_subject_uses_generic_facility_repair() -> None:
    raw = "המתנ\"ס שתושבי רובע ט\"ו מחכים לו כל כך הרבה שנים"
    repaired, reason = step4._repair_rejected_topic_subject(
        raw_subject=raw,
        item={
            "topic_subject_he": raw,
            "topic_headline_he": raw,
            "topic_identification_context": raw,
            "raw_text": raw,
        },
        root_label="תכנון ובנייה",
        quote=raw,
    )

    assert repaired == "מתנ\"ס רובע ט\"ו"
    assert reason == "subject_repaired:alternate_source"


def test_rejected_planning_subject_uses_reusable_renewal_label() -> None:
    raw = "פרוייקט פינוי בינוי ברחוב הרב מימון, תכנית 0778050, רובע ב, אשדוד"
    repaired, reason = step4._repair_rejected_topic_subject(
        raw_subject=raw,
        item={
            "topic_subject_he": raw,
            "topic_headline_he": raw,
            "topic_identification_context": raw,
            "raw_text": raw,
        },
        root_label="תכנון ובנייה",
        quote=raw,
    )

    assert repaired == "פינוי בינוי הרב מימון"
    assert reason == "subject_repaired:semantic_canonicalized"


def test_query_carrier_extracts_semantic_subject_from_dangerous_building_query() -> None:
    contract = step4._topic_contract_from_headline(
        "שאילתה בנושא בדיקת מבנים מסוכנים ברחבי העיר",
        structural_role="outline_item",
        packet_role="protocol",
    )

    assert contract["is_topic_bearing"] is True
    assert contract["topic_subject_he"] == "בדיקת מבנים מסוכנים ברחבי העיר"


def test_canonical_topic_label_rejects_dialogue_only() -> None:
    canonical, reason = step4.canonicalize_topic_label("יו\"ר הישיבה .זה מועצת העיר, נכון")

    assert canonical is None
    assert reason == "procedural_or_dialogue_label"


def test_procedural_meeting_order_heading_is_non_topic() -> None:
    assert step4._looks_like_container_heading("ישיבת המועצה סדר") is True
    assert step4._non_topic_protocol_reason(headline="ישיבת המועצה סדר", raw_text="ישיבת המועצה סדר", structural_role="outline_item", packet_role="protocol") == "container_heading"


def test_personal_announcement_handoff_is_non_topic() -> None:
    text = "דברי ראש העיר במקום דברי ראש העיר- עו\"ד גבי כנפו יו\"ר המועצה נותן למר לאוניד גלמן את זכות הדיבור למסירת הודעה אישית"

    assert step4._non_topic_protocol_reason(headline=text, raw_text=text, structural_role="outline_item", packet_role="protocol") == "personal_announcement_handoff"


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


def test_canonical_topic_label_rejects_legal_and_table_carriers() -> None:
    for raw in ["להלן", "בסעיף", "תיקון סעיף", "קוד תאור החלטה תוקף סטאטוס", "שאילתה של עורכת דין גלבר בנושא", "מי בעד הצעת ההחלטה"]:
        canonical, reason = step4.canonicalize_topic_label(raw)

        assert canonical is None
        assert reason in {"procedural_or_dialogue_label", "unresolved_noisy_label"}


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


def test_section47_email_approval_carrier_is_not_topic_bearing() -> None:
    text = "התקבל אישור מועצה במייל ב- 24.5.26 לפי סעיף47 לתקנון בדבר ישיבות מועצה"

    assert step4._topic_contract_from_headline(text, structural_role="continuation", packet_role="protocol")["is_topic_bearing"] is False
    assert step4._non_topic_protocol_reason(headline=text, raw_text=text, structural_role="continuation", packet_role="protocol") == "procedural_packet_carrier"


def test_section47_budget_packet_extracts_budget_subject() -> None:
    raw_text = (
        "סעיף47 לתקנון בדבר ישיבות המועצה לאחר אישור היועץ המשפטי .2026 "
        "תקצוב אירוע חגיגות אליפות לשנת 2026 – עדכון תקציב לשנת 2026 "
        "לעירייה לאשר אישור המועצה לעדכון נדרש עפ\"י דין"
    )
    headline = step4._extract_heading_from_text(raw_text)
    unit = {"structural_role": "outline_item", "raw_text": raw_text}

    assert "תקצוב אירוע" in headline
    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline=headline,
        raw_text=raw_text,
        topic_context_source="unit_heading",
        headline_source="raw_extracted",
        packet_role="protocol",
    ) is None

    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={"structure_unit_id": "u1", "semantic_unit_id": "u1", **unit},
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": headline, "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )

    assert item["row_type"] == "topic_item"
    assert item["deterministic_topic_decision"]["root_topic_id"] == "root_budget_finance"
    assert item["deterministic_topic_decision"]["action"] == "choose_existing_topic"


def test_bounded_split_lease_continuation_is_topic_item() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "continuation",
            "raw_text": "4 .. עסקאות חכירה מול רמ\"י",
            "structure_evidence": {"split_reason": "multiple_structural_starts"},
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "עסקאות חכירה מול רמ\"י", "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )

    assert item["row_type"] == "topic_item"
    assert item["skip_model_assignment"] is False
    assert item["deterministic_topic_decision"]["root_topic_id"] == "root_allocations"


def test_contextual_prefilter_only_allows_strong_policy_matches() -> None:
    item = {
        "row_type": "topic_item",
        "raw_text": "תקציב הגיל הרך",
        "topic_subject_he": "תקציב הגיל הרך",
        "document_context": {"packet_role": "protocol"},
        "deterministic_topic_decision": {
            "action": "choose_existing_topic",
            "needs_dicta": False,
            "reason": "strong_policy_match",
            "root_topic_id": "root_budget_finance",
            "confidence": 0.98,
        },
    }

    assert step4._preassign_contextual_with_candidate_finder(item) is True

    assert step4._preassign_contextual_with_candidate_finder(
        {**item, "deterministic_topic_decision": {**item["deterministic_topic_decision"], "reason": "strong_root_match"}}
    ) is False
    assert step4._preassign_contextual_with_candidate_finder(
        {**item, "deterministic_topic_decision": {"action": "needs_judge", "reason": "ambiguous_candidates", "root_topic_id": "root_budget_finance", "confidence": 0.84}}
    ) is False
    assert step4._preassign_contextual_with_candidate_finder(
        {**item, "deterministic_topic_decision": {**item["deterministic_topic_decision"], "confidence": 0.91}}
    ) is False


def test_contextual_model_omission_uses_strong_child_tree_evidence() -> None:
    row = step4._fallback_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "topic_item",
            "structural_role": "continuation",
            "raw_text": "4 . קריאת רחוב על שמו של זאב רווח ז\"ל",
            "topic_headline_he": "קריאת רחוב על שמו של זאב רווח ז\"ל",
            "topic_subject_he": "קריאת רחוב על שמו של זאב רווח ז\"ל",
            "is_topic_bearing": True,
            "document_context": {"packet_role": "protocol"},
            "deterministic_topic_decision": {
                "action": "needs_judge",
                "needs_dicta": True,
                "reason": "ambiguous_candidates",
                "root_topic_id": "root_administration",
                "confidence": 0.9,
            },
            "root_topic_candidates": [{"root_topic_id": "root_administration", "score": 0.9}],
            "candidate_child_topics": [
                {
                    "candidate_child_id": "u1_child_1",
                    "root_topic_id": "root_administration",
                    "root_label_he": "מנהל עירוני ומינויים",
                    "label_he": "שמות והנצחה",
                    "evidence_source": "existing_tree",
                    "evidence_quote_he": "קריאת רחוב על שמו של זאב רווח ז\"ל",
                    "confidence_hint": 0.94,
                    "aliases_he": ["קריאת רחוב"],
                }
            ],
        },
        reason="contextual_model_omitted_unit",
    )

    assert row["topic_node_status"] == "active"
    assert row["topic_subject_he"] == "קריאת רחוב על שמו של זאב רווח ז\"ל"
    assert row["child_label_he"] == "שמות והנצחה"
    assert row["topic_reject_reason"] is None


def test_db_free_tree_exposes_curated_child_aliases() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    admin_root = next(row for row in tree["root_topics"] if row["root_topic_id"] == "root_administration")
    naming_child = next(child for child in admin_root["children"] if child["child_label_he"] == "שמות והנצחה")

    assert "קריאת רחוב" in naming_child["aliases_he"]


def test_curated_child_alias_supports_contextual_root_and_child() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "outline_item",
            "raw_text": "4 . קריאת רחוב על שמו של זאב רווח ז\"ל",
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "קריאת רחוב על שמו של זאב רווח ז\"ל", "context_source": "unit_heading"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )
    item["dicta_mode"] = "dicta_contextual"
    parsed = {
        "structure_unit_id": "u1",
        "root_topic_id": "root_administration",
        "is_topic_bearing": True,
        "topic_subject_he": "קריאת רחוב על שמו של זאב רווח ז\"ל",
        "clean_subject_he": "קריאת רחוב על שמו של זאב רווח ז\"ל",
        "confidence": 0.9,
        "rationale_he": "נושא שמות והנצחה במרחב העירוני.",
        "topic_supporting_quote_he": "קריאת רחוב על שמו של זאב רווח ז\"ל",
        "indexability_status": "standalone_topic",
        "event_status": "discussed",
        "agenda_disposition": "discussed",
        "classification_basis": "service_domain",
        "competing_roots": [],
        "contextual_schema_valid": True,
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert any(child["label_he"] == "שמות והנצחה" for child in item["candidate_child_topics"])
    assert assignment["root_topic_id"] == "root_administration"
    assert assignment["child_label_he"] == "שמות והנצחה"


def test_lease_right_cancellation_routes_to_allocations_despite_agreement_word() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "outline_item",
            "raw_text": "8 . אישור הסכם לביטול זכות חכירה . 9 '. החלפת שטחי ציבור בתוכנית מס605-1535301 ברח' הורקנוס",
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "אישור הסכם לביטול זכות חכירה . 9 '. החלפת שטחי ציבור בתוכנית מס605-1535301 ברח' הורקנוס", "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )
    row = step4._assignment_from_candidate_finder(item)

    assert row["root_topic_id"] == "root_allocations"
    assert row["topic_subject_he"] == "ביטול זכות חכירה"
    assert row["topic_node_status"] == "active"
    assert "candidate_review" not in row["topic_assignment_route"]


def test_senior_officer_appointment_trims_tender_tail() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "outline_item",
            "raw_text": "סעיף47 לתקנון בדבר ישיבות המועצה לאחר אישור היועץ המשפטי לעירייה לאשר,מינוי שכר ואצילת סמכויות למהנדס העיר מר פלוני שנבחר במכרז ביום13.5.",
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "מינוי שכר ואצילת סמכויות למהנדס העיר מר פלוני שנבחר במכרז ביום", "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )
    row = step4._assignment_from_candidate_finder(item)

    assert row["root_topic_id"] == "root_administration"
    assert row["topic_subject_he"] == "מינוי שכר ואצילת סמכויות למהנדס העיר"
    assert "topic_subject_rejected" not in row["topic_assignment_route"]


def test_general_committee_protocol_approval_preserves_action_and_routes_to_administration() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    raw_text = "10 . אישור החלטות בפרוטוקולים של ועדות העירייה :"
    headline = step4._extract_heading_from_text(raw_text)
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "outline_item",
            "raw_text": raw_text,
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": headline, "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )
    row = step4._assignment_from_candidate_finder(item)

    assert headline == "אישור החלטות בפרוטוקולים של ועדות העירייה"
    assert row["root_topic_id"] == "root_administration"
    assert row["topic_node_status"] == "active"


def test_debt_writeoff_protocol_label_keeps_subject_and_existing_child() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "outline_item",
            "raw_text": "12 . 'פרוטוקול למחיקת חובות מס7.25 מיום25.12.",
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "פרוטוקול למחיקת חובות מס7.25 מיום25.12", "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )
    row = step4._assignment_from_candidate_finder(item)

    assert row["topic_subject_he"] == "מחיקת חובות"
    assert row["root_topic_id"] == "root_budget_finance"
    assert row["child_label_he"] == "גביית חובות"
    assert row["topic_node_status"] == "active"


def test_paramedical_benefits_question_routes_to_welfare_subject() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    raw_text = (
        "סעיף6 : שינוי קריטריונים בביטוח לאומי למתן - שאילתא בנושא 1.5 מצ\"ל "
        "טיפולים פרא רפואיים, בקשתו של חבר מועצה"
    )
    item = step4._build_item(
        unit={"structure_unit_id": "u1", "semantic_unit_id": "u1", "structural_role": "outline_item", "raw_text": raw_text},
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "שינוי קריטריונים בביטוח לאומי למתן - שאילתא בנושא", "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )
    row = step4._assignment_from_candidate_finder(item)

    assert row["topic_subject_he"] == "טיפולים פרא רפואיים"
    assert row["root_topic_id"] == "root_welfare_social"
    assert row["topic_node_status"] == "active"


def test_municipal_prizes_subject_matches_existing_culture_child() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "outline_item",
            "raw_text": ")22 'עדכון תקנוני הפרסים העירוניים ואישור תקנון פרס התיאטרון (עמ.8",
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "עדכון תקנוני הפרסים העירוניים ואישור תקנון פרס התיאטרון", "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )
    row = step4._assignment_from_candidate_finder(item)

    assert row["topic_subject_he"] == "פרסים עירוניים"
    assert row["root_topic_id"] == "root_culture_sport"
    assert row["child_label_he"] == "פרסים עירוניים"
    assert row["topic_node_status"] == "active"


def test_urban_renewal_heading_routes_to_planning() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "continuation",
            "raw_text": "6 .. בקשה לקידום מתחם התחדשות עירונית גן סיאטל בותמ\"ל",
            "structure_evidence": {"split_reason": "multiple_structural_starts"},
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "בקשה לקידום מתחם התחדשות עירונית גן סיאטל בותמ\"ל", "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )

    assert item["row_type"] == "topic_item"
    assert item["deterministic_topic_decision"]["root_topic_id"] == "root_planning_building"


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


def test_vote_dialogue_table_and_incomplete_query_fragments_are_non_topic() -> None:
    cases = [
        (
            "הצעה לסדר ?שלך, אנחנו רוצים להצביע. מי בעד הצעת ההחלטה",
            "vote_or_discussion_procedure_fragment",
        ),
        (
            "הצעה לסדר, ענת ענתה. אפשר לקיים דיון בהזדמנות אחרת,",
            "vote_or_discussion_procedure_fragment",
        ),
        (
            "החלטות קוד תאור החלטה תוקף סטאטו ס",
            "table_header_or_schema_fragment",
        ),
        (
            "שאילתה של עור כת דין גלבר בנושא-",
            "incomplete_query_topic_marker",
        ),
    ]

    for text, expected in cases:
        assert step4._non_topic_protocol_reason(headline=text, raw_text=text, structural_role="outline_item", packet_role="protocol") == expected


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


def test_fragment_body_with_strong_parking_evidence_gets_transport_root_only() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "body",
            "topic_identification_context": "יש למנהלת אגף החנייה סמכות לחלוקת תווי חנייה",
            "topic_headline_he": "",
            "document_context": {"packet_role": "protocol"},
            "raw_text": "יש למנהלת אגף החנייה סמכות, אבל זה לא בהתאם לנוהל; חלוקה של תווי חנייה בניגוד לנהלים עירוניים",
        },
        reason="body_without_headline_topic_provenance",
    )

    assert row["is_topic_bearing"] is False
    assert row["root_topic_id"] == "root_transport_safety"
    assert row["child_label_he"] is None
    assert row["topic_assignment_confidence"] == 0.55
    assert row["topic_assignment_route"].endswith("strong_body_evidence_root")


def test_split_header_fragment_ignores_copied_action_evidence_for_root_override() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "continuation",
            "topic_identification_context": "ישיבת מועצה מספר 75",
            "topic_headline_he": "",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": "ישיבת מועצה מספר 75 מתאריך 31.8.2023",
            "raw_text": "ישיבת מועצה מספר 75\nמאפשר חלוקה של תווי חנייה בניגוד לנהלים עירוניים",
        },
        reason="missing_topic_headline_provenance",
    )

    assert row["root_topic_id"] == "root_agenda_queries"
    assert row["topic_assignment_confidence"] == 0.35
    assert not row["topic_assignment_route"].endswith("strong_body_evidence_root")


def test_short_protocol_header_listing_does_not_get_topic_root_context() -> None:
    raw_text = (
        "פרוטוקול ישיבות המועצה העשרים ושתיים פרוטוקול ישיבה לא מן המניין "
        "מתאריך ט' בטבת תשפ\"ו תקציב 2026 - סדר הישיבה הצעת התקציב הרגיל והבלתי רגיל לשנת 2026"
    )
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "body",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": raw_text,
            "raw_text": raw_text,
        },
        reason="body_without_headline_topic_provenance",
    )

    assert row["root_topic_id"] == "root_agenda_queries"
    assert "strong_body_evidence_root" not in row["topic_assignment_route"]


def test_non_topic_tender_report_row_gets_procurement_root_context() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "continuation",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": "דוח ועדה למסירת עבודות הפטורות ממכרז מישיבה מס 42",
            "raw_text": "דוח ועדה למסירת עבודות הפטורות ממכרז מישיבה מס 42",
        },
        reason="transcript_window_without_bounded_headline",
    )

    assert row["root_topic_id"] == "root_agreements"
    assert row["child_label_he"] is None
    assert "strong_body_evidence_root" in row["topic_assignment_route"]


def test_long_transcript_fragment_does_not_get_procurement_root_context() -> None:
    transcript = " ".join(
        [
            "פרוטוקול ישיבות המועצה פרוטוקול ישיבה מן המניין מתאריך יט בחשון",
            "מר כהן ראש העירייה שאל על בקשה לאישור ניהול משא ומתן עם ספקים פוטנציאליים לצורך התקשרות ללא מכרז",
            "גב' לוי השיבה שהדיון נמשך והדוברים עברו לנושאים נוספים ללא כותרת עצמאית",
        ]
        * 9
    )
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "body",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": transcript,
            "raw_text": transcript,
        },
        reason="body_without_headline_topic_provenance",
    )

    assert row["root_topic_id"] == "root_agenda_queries"
    assert row["topic_assignment_confidence"] == 0.35
    assert "strong_body_evidence_root" not in row["topic_assignment_route"]


def test_protocol_agenda_listing_does_not_get_land_allocation_root_context() -> None:
    listing = (
        "פרוטוקול ישיבות המועצה העשרים ושתיים פרוטוקול ישיבה מן המניין "
        "מתאריך ו' באדר תשפ\"ו סדר הישיבה שאילתות עמ 3 הצעות לסדר היום עמ 10 "
        "פרוטוקול ועדת נכסים עמ 16 פרוטוקול ועדת הקצאת מקרקעין מס 9/26 עמ 17 "
        "פרוטוקול ועדת כספים עמ 23 פרוטוקול ועדת תמיכות עמ 25 מינויים ושינויים בתאגידים"
    )
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "body",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": listing,
            "raw_text": listing,
        },
        reason="body_without_headline_topic_provenance",
    )

    assert row["root_topic_id"] == "root_agenda_queries"
    assert row["topic_assignment_confidence"] == 0.35
    assert "strong_body_evidence_root" not in row["topic_assignment_route"]


def test_non_topic_environmental_report_row_gets_environment_root_context() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "continuation",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": "דוח ועדת איכות הסביבה מישיבה מס 8",
            "raw_text": "דוח ועדת איכות הסביבה מישיבה מס 8",
        },
        reason="transcript_window_without_bounded_headline",
    )

    assert row["root_topic_id"] == "root_infrastructure_environment"
    assert row["child_label_he"] is None


def test_non_topic_environmental_bylaw_row_gets_environment_root_context() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "body",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": "הצעת חוק עזר למניעת רעש והארכת הוראת השעה לפינוי אשפה בהתאם לאישור השרה להגנת הסביבה",
            "raw_text": "הצעת חוק עזר למניעת רעש והארכת הוראת השעה לפינוי אשפה בהתאם לאישור השרה להגנת הסביבה",
        },
        reason="body_without_headline_topic_provenance",
    )

    assert row["root_topic_id"] == "root_infrastructure_environment"
    assert row["child_label_he"] is None


def test_container_with_strong_environmental_bylaw_evidence_gets_environment_root_context() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "container",
            "structural_role": "body",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": "חוק העזר מובא לידיעה בהתאם לאישור השרה להגנת הסביבה בנושא פינוי אשפה",
            "raw_text": "חוק העזר מובא לידיעה בהתאם לאישור השרה להגנת הסביבה בנושא פינוי אשפה",
        },
        reason="container_heading",
    )

    assert row["root_topic_id"] == "root_infrastructure_environment"
    assert row["row_type"] == "container"
    assert row["child_label_he"] is None


def test_non_topic_committee_appointment_continuation_gets_admin_root_context() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "body",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": "להאריך את מינויה של אדריכלית פלונית כממלאת מקום בוועדה עד סוף השנה",
            "raw_text": "להאריך את מינויה של אדריכלית פלונית כממלאת מקום בוועדה עד סוף השנה",
        },
        reason="body_without_headline_topic_provenance",
    )

    assert row["root_topic_id"] == "root_administration"
    assert row["child_label_he"] is None


def test_unsupported_weak_candidate_does_not_choose_arbitrary_root_when_dicta_disabled() -> None:
    row = step4._candidate_review_assignment(
        item={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "topic_item",
            "structural_role": "outline_item",
            "is_topic_bearing": True,
            "topic_identification_context": "דוחות המובאים לאישור המועצה",
            "topic_headline_he": "דוחות המובאים לאישור המועצה",
            "document_context": {"packet_role": "protocol"},
            "raw_text": "דוחות המובאים לאישור המועצה",
            "deterministic_topic_decision": {
                "needs_dicta": True,
                "root_topic_id": "root_religious_services",
                "confidence": 0.45,
                "reason": "weak_candidate",
            },
            "root_topic_candidates": [
                {"root_topic_id": "root_religious_services", "root_label_he": "דת ושירותי דת", "score": 0.45, "matched_terms": []}
            ],
        },
        reason="dicta_disabled",
    )

    assert row["root_topic_id"] == "root_agenda_queries"
    assert row["row_type"] == "fragment"
    assert "unsupported_weak_candidate" in row["topic_assignment_route"]


def test_mayor_office_employment_subject_prefers_hr_over_mayor_updates() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "outline_item",
            "raw_text": "3.2 אישור מועצת העירייה להעסקת עובד במשרת אמון בלשכת ראש העיר",
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "3.2 אישור מועצת העירייה להעסקת עובד במשרת אמון בלשכת ראש העיר", "context_source": "unit_heading"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )

    decision = item["deterministic_topic_decision"]
    assert decision["action"] == "choose_existing_topic"
    assert decision["root_topic_id"] == "root_hr_labor"
    assert decision["policy_id"] == "hr_employment_conditions"


def test_model_omission_uses_candidate_review_instead_of_active_guess() -> None:
    row = step4._fallback_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "topic_item",
            "structural_role": "body",
            "is_topic_bearing": True,
            "topic_subject_he": "מקור לכיסוי גירעון תקציבי",
            "topic_identification_context": "מקור לכיסוי גירעון תקציבי",
            "topic_headline_he": "מקור לכיסוי גירעון תקציבי",
            "document_context": {"packet_role": "protocol"},
            "raw_text": "דיון בנושא מקור לכיסוי גירעון תקציבי בפרויקט עירוני",
            "deterministic_topic_decision": {
                "needs_dicta": True,
                "root_topic_id": "root_budget_finance",
                "reason": "ambiguous_candidates",
            },
            "root_topic_candidates": [
                {"root_topic_id": "root_budget_finance", "root_label_he": "תקציב וכספים", "score": 0.8}
            ],
        },
        reason="model_omitted_unit",
    )

    assert row["root_topic_id"] == "root_budget_finance"
    assert row["topic_node_status"] == "candidate"
    assert row["child_label_he"] is None
    assert row["topic_reject_reason"] == "non_blocking_topic_review:model_omitted_unit:ambiguous_candidates"


def test_policy_match_inside_dialogue_fragment_is_non_topic() -> None:
    row = step4._assignment_from_candidate_finder(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "topic_item",
            "structural_role": "outline_item",
            "is_topic_bearing": True,
            "topic_subject_he": "לגבי תקציב, אם צריך, אולי לא צריך",
            "topic_identification_context": "לגבי תקציב, אם צריך, אולי לא צריך",
            "topic_headline_he": "לגבי תקציב, אם צריך, אולי לא צריך",
            "document_context": {"packet_role": "protocol"},
            "raw_text": "הצעה לסדר, איך אנחנו יכולים לקבל החלטות לגבי תקציב, אם צריך, אולי לא צריך",
            "deterministic_topic_decision": {
                "action": "choose_existing_topic",
                "needs_dicta": False,
                "confidence": 0.98,
                "reason": "strong_policy_match",
                "root_topic_id": "root_budget_finance",
                "root_label_he": "תקציב וכספים",
                "policy_id": "budget_line_or_reserve",
            },
            "root_topic_candidates": [],
            "candidate_child_topics": [],
        }
    )

    assert row["is_topic_bearing"] is False
    assert row["topic_assignment_route"].endswith("policy_match_inside_dialogue_fragment")


def test_contextual_valid_root_survives_noisy_post_cleanup() -> None:
    row = {
        "structure_unit_id": "u_contextual_noisy_heading",
        "semantic_unit_id": "u_contextual_noisy_heading",
        "packet_role": "protocol",
        "row_type": "topic_item",
        "structural_role": "outline_item",
        "is_topic_bearing": True,
        "topic_node_status": "active",
        "topic_subject_he": "מינוי מנכ\"ל חב' יובלים",
        "topic_headline_he": "מינוי מנכ\"ל חב' יובלים",
        "topic_identification_context": "מינוי מנכ\"ל חב' יובלים",
        "root_topic_id": "root_administration",
        "root_label_he": "מנהל עירוני ומינויים",
        "dicta_contextual_indexability_status": "standalone_topic",
        "dicta_contextual_event_status": "approved",
        "topic_assignment_route": "dictalm_v4_global_tree:dicta_contextual_root:child_choice:heading:canonical_topic_label",
    }

    normalized = step4._normalize_protocol_non_topic_assignments([row])[0]

    assert normalized["is_topic_bearing"] is True
    assert normalized["row_type"] == "topic_item"
    assert "post_non_topic" not in normalized["topic_assignment_route"]


def test_order_proposal_dialogue_only_is_non_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="הצעה לסדר, קיבלת תשובה, זה לא עובד ככה",
        raw_text="הצעה לסדר, קיבלת תשובה, זה לא עובד ככה",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "order_proposal_intro_only"


def test_policy_root_attaches_existing_child_candidate() -> None:
    row = step4._assignment_from_candidate_finder(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "topic_item",
            "structural_role": "outline_item",
            "is_topic_bearing": True,
            "topic_subject_he": "גני ילדים",
            "topic_identification_context": "גני ילדים",
            "topic_headline_he": "גני ילדים",
            "document_context": {"packet_role": "protocol"},
            "raw_text": "גני ילדים",
            "deterministic_topic_decision": {
                "action": "choose_existing_topic",
                "needs_dicta": False,
                "confidence": 0.98,
                "reason": "strong_policy_match",
                "root_topic_id": "root_education",
                "root_label_he": "חינוך",
                "policy_id": "parent_payments_education",
            },
            "root_topic_candidates": [],
            "candidate_child_topics": [
                {
                    "candidate_child_id": "child-education",
                    "label_he": "מוסדות חינוך",
                    "root_topic_id": "root_education",
                    "root_label_he": "חינוך",
                    "evidence_quote_he": "גני ילדים",
                    "evidence_source": "existing_tree",
                    "confidence_hint": 0.94,
                    "aliases_he": ["גני ילדים"],
                }
            ],
        }
    )

    assert row["root_topic_id"] == "root_education"
    assert row["child_label_he"] == "מוסדות חינוך"


def test_semantic_child_facets_return_existing_closed_list_children() -> None:
    topic_tree = {
        "root_topics": [
            {
                "root_topic_id": "root_budget_finance",
                "root_label_he": "תקציב וכספים",
                "children": [
                    {"child_topic_id": "c1", "child_label_he": "מימון פרויקטים עירוניים", "status": "active"},
                    {"child_topic_id": "c2", "child_label_he": "הנחות ופטורים", "status": "active"},
                    {"child_topic_id": "c3", "child_label_he": "תקצוב שירותים עירוניים", "status": "active"},
                ],
            },
            {
                "root_topic_id": "root_agreements",
                "root_label_he": "הסכמים והתקשרויות",
                "children": [{"child_topic_id": "c4", "child_label_he": "מכרזים והתקשרויות", "status": "active"}],
            },
            {
                "root_topic_id": "root_security_enforcement",
                "root_label_he": "ביטחון ואכיפה",
                "children": [{"child_topic_id": "c5", "child_label_he": "מוכנות לחירום", "status": "active"}],
            },
        ]
    }

    tbr = step4._candidate_child_topics(unit_id="u1", text="קרצוף כבישים, תב\"ר", evidence_text="קרצוף כבישים, תב\"ר", outline_title="", explicit_actions=[], document_context={"packet_role": "protocol"}, document_child_candidates=[], referenced_attachment_contexts=[], topic_tree=topic_tree, structural_role="outline_item")
    exemption = step4._candidate_child_topics(unit_id="u2", text="פטור לנכס שאינו ראוי לשימוש ולא ישולם היטל", evidence_text="פטור לנכס שאינו ראוי לשימוש ולא ישולם היטל", outline_title="", explicit_actions=[], document_context={"packet_role": "protocol"}, document_child_candidates=[], referenced_attachment_contexts=[], topic_tree=topic_tree, structural_role="outline_item")
    tender = step4._candidate_child_topics(unit_id="u3", text="אישור התקשרות עם זוכה במכרז פומבי", evidence_text="אישור התקשרות עם זוכה במכרז פומבי", outline_title="", explicit_actions=[], document_context={"packet_role": "protocol"}, document_child_candidates=[], referenced_attachment_contexts=[], topic_tree=topic_tree, structural_role="outline_item")
    emergency = step4._candidate_child_topics(unit_id="u4", text="דיון בנושא מיגון וחירום", evidence_text="דיון בנושא מיגון וחירום", outline_title="", explicit_actions=[], document_context={"packet_role": "protocol"}, document_child_candidates=[], referenced_attachment_contexts=[], topic_tree=topic_tree, structural_role="outline_item")

    assert any(row["label_he"] == "מימון פרויקטים עירוניים" for row in tbr)
    assert any(row["label_he"] == "הנחות ופטורים" for row in exemption)
    assert any(row["label_he"] == "מכרזים והתקשרויות" for row in tender)
    assert any(row["label_he"] == "מוכנות לחירום" for row in emergency)


def test_rejected_procedural_topic_subject_clears_child_label() -> None:
    raw = "שהתקבלו בפרוטוקולים של הועדות כוחם יפה והם בתוקף"
    row = step4._assignment_payload(
        item={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "topic_item",
            "structural_role": "outline_item",
            "document_context": {"packet_role": "protocol"},
            "topic_identification_context": raw,
            "topic_headline_he": raw,
            "raw_text": raw,
            "source_region_ids": [],
            "source_block_ids": [],
        },
        root_topic_id="root_agreements",
        root_label="הסכמים והתקשרויות",
        child_label="מכרזים והתקשרויות",
        raw_child_label="מכרזים והתקשרויות",
        status="active",
        reject_reason=None,
        aliases=[],
        confidence=0.9,
        quote=raw,
        route="test",
        rationale_he="test",
        parsed_contract={"is_topic_bearing": True, "topic_subject_he": raw},
    )

    assert row["is_topic_bearing"] is False
    assert row["child_label_he"] is None


def test_dicta_prompt_tree_is_compact_and_child_context_is_per_item() -> None:
    payload = step4._topic_tree_prompt_payload(
        {
            "topic_tree_version": "test",
            "root_topics": [
                {
                    "root_topic_id": "root_budget_finance",
                    "root_label_he": "תקציב וכספים",
                    "keywords": ["תקציב"],
                    "profile": {"positive_examples": [{"quote_he": "long example"}]},
                    "children": [
                        {
                            "child_topic_id": "c1",
                            "child_label_he": "תקצוב שירותים עירוניים",
                            "profile": {"positive_examples": [{"quote_he": "child example"}]},
                        }
                    ],
                }
            ],
        }
    )

    assert payload["root_topics"][0]["child_count"] == 1
    assert "children" not in payload["root_topics"][0]
    assert "profile" not in payload["root_topics"][0]


def test_model_prompt_adds_compact_child_choices() -> None:
    items = step4._items_for_model_prompt(
        [
            {
                "structure_unit_id": "u1",
                "candidate_child_topics": [
                    {
                        "candidate_child_id": "child-1",
                        "root_topic_id": "root_budget_finance",
                        "root_label_he": "תקציב וכספים",
                        "label_he": "תקצוב שירותים עירוניים",
                        "evidence_quote_he": "תקציב הגיל הרך",
                        "evidence_source": "existing_tree",
                        "confidence_hint": 0.9,
                        "profile": {"summary_he": "long child profile"},
                    }
                ],
            }
        ]
    )

    assert items[0]["candidate_child_choices"][0]["candidate_child_id"] == "child-1"
    assert items[0]["candidate_child_choices"][0]["label_he"] == "תקצוב שירותים עירוניים"
    assert "profile" not in items[0]["candidate_child_choices"][0]


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
    assert step4._looks_like_procedural_carrier_heading("פרוטוקול הוועדה המקצועית מספר") is True
    assert step4._non_topic_protocol_reason(headline="פרוטוקול הוועדה המקצועית מספר", raw_text="פרוטוקול הוועדה המקצועית מספר", structural_role="outline_item", packet_role="protocol") == "procedural_carrier_heading"
    assert step4._looks_like_procedural_carrier_heading("פרוטוקול ועדת תמיכות") is False


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


def test_query_carrier_person_dash_subject_prefers_real_subject() -> None:
    contract = step4._topic_contract_from_headline(
        "שאילתה: יובל צלנר, חבר המועצה – בעיית המפונים שבתיהם נפגעו",
        structural_role="outline_item",
        packet_role="protocol",
    )

    assert contract["is_topic_bearing"] is True
    assert contract["agenda_carrier_he"] == "שאילתה"
    assert contract["topic_subject_he"] == "בעיית המפונים שבתיהם נפגעו"
    assert step4.infer_root_topic_id(contract["topic_subject_he"]) == "root_welfare_social"


def test_query_subject_after_benosheh_prefers_evacuated_residents() -> None:
    contract = step4._topic_contract_from_headline(
        "שאילתה של יובל צלנר בנושא פינוי תושבים מבתים שנפגעו",
        structural_role="outline_item",
        packet_role="protocol",
    )

    assert contract["is_topic_bearing"] is True
    assert contract["topic_subject_he"] == "פינוי תושבים מבתים שנפגעו"
    assert step4.infer_root_topic_id(contract["topic_subject_he"]) == "root_welfare_social"


def test_order_proposal_person_dash_subject_prefers_real_subject() -> None:
    contract = step4._topic_contract_from_headline(
        "הצעה לסדר: חברת מועצה – טיפול בדרי רחוב",
        structural_role="outline_item",
        packet_role="protocol",
    )

    assert contract["is_topic_bearing"] is True
    assert contract["agenda_carrier_he"] == "הצעה לסדר"
    assert contract["topic_subject_he"] == "טיפול בדרי רחוב"
    assert step4.infer_root_topic_id(contract["topic_subject_he"]) == "root_welfare_social"


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


def test_child_only_judge_applies_same_root_existing_child() -> None:
    item = {
        "structure_unit_id": "u1",
        "semantic_unit_id": "u1",
        "document_context": {"packet_role": "protocol"},
        "topic_identification_context": "פטור לנכס שאינו ראוי לשימוש בשל נזק מלחמה",
        "topic_headline_he": "פטור לנכס שאינו ראוי לשימוש בשל נזק מלחמה",
        "topic_subject_he": "פטור לנכס בשל נזק מלחמה",
        "raw_text": "פטור לנכס שאינו ראוי לשימוש בשל נזק מלחמה",
        "explicit_actions": [],
    }
    assignment = {
        "structure_unit_id": "u1",
        "root_topic_id": "root_budget_finance",
        "root_label_he": "תקציב וכספים",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "topic_node_status": "active",
        "topic_supporting_quote_he": "פטור לנכס שאינו ראוי לשימוש בשל נזק מלחמה",
        "topic_assignment_route": "dictalm_v4_global_tree:root_only",
    }
    candidate = {
        "candidate_child_id": "c1",
        "root_topic_id": "root_budget_finance",
        "root_label_he": "תקציב וכספים",
        "label_he": "הנחות ופטורים",
        "evidence_source": "existing_tree",
        "evidence_quote_he": "פטור לנכס שאינו ראוי לשימוש בשל נזק מלחמה",
        "confidence_hint": 0.9,
        "aliases_he": [],
    }

    updated, error = step4._assignment_with_child_only_decision(
        item=item,
        assignment=assignment,
        decision={"structure_unit_id": "u1", "child_choice_id": "c1", "confidence": 0.88, "rationale_he": "הפריט עוסק בפטור"},
        candidate_child_choices=[candidate],
    )

    assert error is None
    assert updated["root_topic_id"] == "root_budget_finance"
    assert updated["child_label_he"] == "הנחות ופטורים"
    assert ":child_only_dicta:existing_tree" in updated["topic_assignment_route"]


def test_topic_subject_v3_hint_requires_accepted_entailed_primary_anchor() -> None:
    row = {
        "artifact_id": "json_900000_49_s0009_01_5f79c9055981",
        "quality_status": "accepted",
        "row_role": "action_anchor",
        "event_role": "primary",
        "source_ordinal": 49,
        "raw_text_he": "שאילתה בנושא מוסדות חינוך לציבור הדתי לאומי",
        "event_payload": {
            "matter_he": "מוסדות חינוך לציבור הדתי לאומי",
            "action_type_he": "מענה לשאילתה",
            "event_phase": "response_given",
            "v3_evidence_entailment": {
                "entailment_status": "entailed",
                "field_assessments": {
                    "matter_he": {"status": "entailed", "source_quote_he": "שאילתה בנושא מוסדות חינוך לציבור הדתי לאומי"},
                    "action_type_he": {"status": "entailed", "source_quote_he": "הוקראה תשובת ראש העיר"},
                },
            },
        },
    }

    hint = step4._topic_subject_v3_hint_from_row(row)

    assert hint is not None
    assert hint["matter_he"] == "מוסדות חינוך לציבור הדתי לאומי"
    assert step4._topic_subject_v3_hint_from_row({**row, "quality_status": "needs_review"}) is None
    keys = step4._topic_subject_v3_hint_keys(row=row)
    assert keys[:2] == ["unit:s0009_01_5f79c9055981", "ordinal:49"]
    assert keys[2].startswith("raw:")


def test_topic_subject_v3_hint_matches_unit_id_without_root_overwrite() -> None:
    hint = {
        "source": "topic_subject_v3",
        "matter_he": "מוסדות חינוך לציבור הדתי לאומי",
        "action_type_he": "מענה לשאילתה",
        "event_phase": "response_given",
        "source_quote_he": "שאילתה בנושא מוסדות חינוך לציבור הדתי לאומי",
    }
    index = {"unit:s0009_01_5f79c9055981": [hint]}

    hints = step4._topic_subject_v3_hints_for_unit(
        unit={},
        unit_id="s0009_01_5f79c9055981",
        raw_text="שאילתה בנושא מוסדות חינוך לציבור הדתי לאומי",
        hint_index=index,
    )

    assert hints == [hint]
    assert "root_topic_id" not in hints[0]


def test_child_only_judge_rejects_cross_root_choice() -> None:
    item = {
        "structure_unit_id": "u1",
        "document_context": {"packet_role": "protocol"},
        "topic_identification_context": "עתיד מינויים ושינויים בוועדת מכרזים",
        "topic_headline_he": "עתיד מינויים ושינויים בוועדת מכרזים",
        "raw_text": "עתיד מינויים ושינויים בוועדת מכרזים",
        "explicit_actions": [],
    }
    assignment = {
        "structure_unit_id": "u1",
        "root_topic_id": "root_administration",
        "root_label_he": "מנהל עירוני ומינויים",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "topic_node_status": "active",
        "topic_assignment_route": "dictalm_v4_global_tree:root_only",
    }
    candidate = {
        "candidate_child_id": "c_wrong_root",
        "root_topic_id": "root_agreements",
        "root_label_he": "הסכמים והתקשרויות",
        "label_he": "מכרזים והתקשרויות",
        "evidence_source": "existing_tree",
        "evidence_quote_he": "ועדת מכרזים",
    }

    updated, error = step4._assignment_with_child_only_decision(
        item=item,
        assignment=assignment,
        decision={"structure_unit_id": "u1", "child_choice_id": "c_wrong_root", "confidence": 0.9},
        candidate_child_choices=[candidate],
    )

    assert updated["root_topic_id"] == "root_administration"
    assert updated.get("child_label_he") is None
    assert error["error_code"] == "CHILD_MODEL_ROOT_MISMATCH"


def test_child_only_candidate_choices_are_fixed_to_assignment_root() -> None:
    item = {
        "candidate_child_topics": [
            {
                "candidate_child_id": "c_wrong_root",
                "root_topic_id": "root_agreements",
                "root_label_he": "הסכמים והתקשרויות",
                "label_he": "מכרזים והתקשרויות",
                "evidence_source": "existing_tree",
            }
        ]
    }
    assignment = {"root_topic_id": "root_administration", "root_label_he": "מנהל עירוני ומינויים"}

    assert step4._fixed_root_child_candidate_rows(item=item, assignment=assignment) == []


def test_committee_appointments_do_not_become_procurement_child() -> None:
    item = {
        "structure_unit_id": "u1",
        "semantic_unit_id": "u1",
        "document_context": {"packet_role": "protocol"},
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "skip_model_assignment": False,
        "topic_identification_context": "עתיד מינויים ושינויים בוועדת מכרזים",
        "topic_headline_he": "עתיד מינויים ושינויים בוועדת מכרזים",
        "topic_subject_he": "עתיד מינויים ושינויים בוועדת מכרזים",
        "raw_text": "עתיד מינויים ושינויים בוועדת מכרזים",
        "explicit_actions": [],
        "candidate_child_topics": [
            {
                "candidate_child_id": "c_tenders",
                "root_topic_id": "root_agreements",
                "root_label_he": "הסכמים והתקשרויות",
                "label_he": "מכרזים והתקשרויות",
                "evidence_source": "existing_tree",
                "evidence_quote_he": "עתיד מינויים ושינויים בוועדת מכרזים",
                "confidence_hint": 0.86,
            }
        ],
        "root_topic_candidates": [
            {"root_topic_id": "root_agreements", "score": 0.84},
            {"root_topic_id": "root_administration", "score": 0.84},
        ],
    }
    parsed = {
        "root_topic_id": "root_agreements",
        "is_topic_bearing": True,
        "topic_subject_he": "עתיד מינויים ושינויים בוועדת מכרזים",
        "clean_subject_he": "עתיד מינויים ושינויים בוועדת מכרזים",
        "topic_supporting_quote_he": "עתיד מינויים ושינויים בוועדת מכרזים",
        "confidence": 0.82,
        "rationale_he": "הנושא עוסק בוועדת מכרזים",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_administration"
    assert assignment["child_label_he"] is None
    assert "committee_governance_override" in assignment["root_adjudication_decision"]


def test_valid_dicta_root_is_not_overridden_by_canonical_root_guess() -> None:
    item = {
        "structure_unit_id": "u1",
        "semantic_unit_id": "u1",
        "document_context": {"packet_role": "protocol"},
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "skip_model_assignment": False,
        "topic_identification_context": "חזרת מיזם חופי לפעילות מלאה בחופי העיר",
        "topic_headline_he": "חזרת מיזם חופי לפעילות מלאה בחופי העיר",
        "topic_subject_he": "חזרת מיזם חופי לפעילות מלאה בחופי העיר",
        "raw_text": "חזרת מיזם חופי לפעילות מלאה בחופי העיר",
        "explicit_actions": [],
        "candidate_child_topics": [],
        "root_topic_candidates": [],
        "deterministic_topic_decision": {"action": "low_confidence", "needs_dicta": True, "reason": "no_candidate"},
    }
    parsed = {
        "root_topic_id": "root_culture_sport",
        "is_topic_bearing": True,
        "topic_subject_he": "חזרת מיזם חופי לפעילות מלאה בחופי העיר",
        "clean_subject_he": "חזרת מיזם חופי לפעילות מלאה בחופי העיר",
        "topic_supporting_quote_he": "חזרת מיזם חופי לפעילות מלאה בחופי העיר",
        "confidence": 0.82,
        "rationale_he": "מיזם פנאי בחופים",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_culture_sport"
    assert assignment["topic_subject_he"] == "פעילות בחופים"


def test_child_only_judge_skips_non_topic_rows(monkeypatch) -> None:
    def fail_call(**_kwargs):
        raise AssertionError("child-only judge should not be called for non-topic rows")

    monkeypatch.setattr(step4, "_call_child_only_dictalm", fail_call)
    assignments, errors, count = step4._apply_child_only_dicta_judgements(
        assignments=[
            {
                "structure_unit_id": "u1",
                "root_topic_id": "root_budget_finance",
                "root_label_he": "תקציב וכספים",
                "row_type": "fragment",
                "is_topic_bearing": False,
                "topic_node_status": "active",
            }
        ],
        items=[
            {
                "structure_unit_id": "u1",
                "candidate_child_topics": [
                    {
                        "candidate_child_id": "c1",
                        "root_topic_id": "root_budget_finance",
                        "label_he": "הנחות ופטורים",
                        "evidence_source": "existing_tree",
                    }
                ],
            }
        ],
        model="unused",
        base_url="http://localhost:1",
        timeout_seconds=1.0,
        model_call_dir=Path("/tmp"),
    )

    assert count == 0
    assert errors == []
    assert assignments[0].get("child_label_he") is None


def test_child_only_judge_runs_for_active_root_only_same_root_candidate(monkeypatch) -> None:
    def fake_call(**kwargs):
        assert kwargs["assignment"]["root_topic_id"] == "root_budget_finance"
        assert kwargs["candidate_child_choices"][0]["candidate_child_id"] == "c_budget"
        return {"decisions": [{"structure_unit_id": "u1", "fixed_root_topic_id": "root_budget_finance", "child_choice_id": "c_budget", "confidence": 0.91, "rationale_he": "תקצוב שירות עירוני"}]}

    monkeypatch.setattr(step4, "_call_child_only_dictalm", fake_call)
    assignments, errors, count = step4._apply_child_only_dicta_judgements(
        assignments=[
            {
                "structure_unit_id": "u1",
                "root_topic_id": "root_budget_finance",
                "root_label_he": "תקציב וכספים",
                "row_type": "topic_item",
                "is_topic_bearing": True,
                "topic_node_status": "active",
                "topic_supporting_quote_he": "תקציב שירותי גיל הרך",
                "topic_assignment_route": "dictalm_v4_global_tree:root_only",
            }
        ],
        items=[
            {
                "structure_unit_id": "u1",
                "semantic_unit_id": "u1",
                "document_context": {"packet_role": "protocol"},
                "structural_role": "outline_item",
                "topic_identification_context": "תקציב שירותי גיל הרך",
                "topic_headline_he": "תקציב שירותי גיל הרך",
                "topic_subject_he": "תקציב שירותי גיל הרך",
                "raw_text": "תקציב שירותי גיל הרך",
                "explicit_actions": [],
                "candidate_child_topics": [
                    {
                        "candidate_child_id": "c_budget",
                        "root_topic_id": "root_budget_finance",
                        "root_label_he": "תקציב וכספים",
                        "label_he": "תקצוב שירותים עירוניים",
                        "evidence_source": "existing_tree",
                        "evidence_quote_he": "תקציב שירותי גיל הרך",
                        "confidence_hint": 0.86,
                    },
                    {
                        "candidate_child_id": "c_wrong_root",
                        "root_topic_id": "root_agreements",
                        "root_label_he": "הסכמים והתקשרויות",
                        "label_he": "מכרזים והתקשרויות",
                        "evidence_source": "existing_tree",
                        "evidence_quote_he": "תקציב שירותי גיל הרך",
                        "confidence_hint": 0.9,
                    },
                ],
            }
        ],
        model="unused",
        base_url="http://localhost:1",
        timeout_seconds=1.0,
        model_call_dir=Path("/tmp"),
    )

    assert count == 1
    assert errors == []
    assert assignments[0]["root_topic_id"] == "root_budget_finance"
    assert assignments[0]["child_label_he"] == "תקצוב שירותים עירוניים"
    assert ":child_only_dicta:existing_tree" in assignments[0]["topic_assignment_route"]
