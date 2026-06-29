from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from municipality.chunking import normalize_for_search
from municipality.topic_label_quality import strip_topic_carrier_prefixes


@dataclass(frozen=True, slots=True)
class TopicPolicy:
    policy_id: str
    root_topic_id: str
    description_he: str
    priority: int
    required_any: tuple[tuple[str, ...], ...] = ()
    positive_terms: tuple[str, ...] = ()
    negative_terms: tuple[str, ...] = ()


TOPIC_POLICIES: tuple[TopicPolicy, ...] = (
    TopicPolicy(
        policy_id="building_system_repair",
        root_topic_id="root_planning_building",
        description_he="תיקון, השבתה, תקלה או תחזוקה של מערכת בניין/מתקן ציבורי מסווגים כתכנון ובנייה כל עוד אין שורש תחזוקה ייעודי.",
        priority=110,
        required_any=(("מעלית", "מעליות", "תחזוקה", "ליקוי", "תקלה", "מושבת", "מושבתת"), ("תיקון", "תחזוקה", "ליקוי", "תקלה", "מושבת", "מושבתת")),
    ),
    TopicPolicy(
        policy_id="commercial_center_subject",
        root_topic_id="root_commerce_assets",
        description_he="קניון, מרכז מסחרי או מתחם מסחרי מסווגים לנכסים ומרכזים מסחריים, לא לתכנון כללי.",
        priority=105,
        required_any=(("קניון", "מרכז מסחרי", "מרכזים מסחריים", "מתחם מסחרי"),),
    ),
    TopicPolicy(
        policy_id="public_building_or_facade_planning",
        root_topic_id="root_planning_building",
        description_he="מבנים יבילים, מבני ציבור, מתנ\"סים ושיפוץ חזיתות מסווגים לתכנון ובנייה.",
        priority=106,
        required_any=(("מבנה יביל", "מבני ציבור", "מבנה ציבור", "מתנ\"סים", "מתנסים", "שיפוץ חזיתות", "חזיתות"),),
    ),
    TopicPolicy(
        policy_id="public_site_renovation_planning",
        root_topic_id="root_planning_building",
        description_he="שיפוץ, שדרוג או פיתוח של אתר ציבורי מסווגים לתכנון ובנייה.",
        priority=106,
        required_any=(("שיפוץ", "שדרוג", "פיתוח"), ("חוף", "חופי", "חופים", "פארק", "מתקן ציבורי", "אתר ציבורי")),
    ),
    TopicPolicy(
        policy_id="procurement_or_tender_action",
        root_topic_id="root_agreements",
        description_he="כאשר פעולת האג'נדה היא מכרז, ביטול מכרז, התקשרות, הרשאה או פטור ממכרז, מסווגים לפי פעולת ההתקשרות גם אם מוזכר תחום שירות כמו חינוך.",
        priority=104,
        required_any=(("ביטול מכרז", "מכרז פומבי", "פטור ממכרז", "פטורים ממכרז", "פטורות ממכרז", "עבודות הפטורות ממכרז", "התקשרות", "הרשאה"),),
    ),
    TopicPolicy(
        policy_id="work_travel_approval",
        root_topic_id="root_travel_approvals",
        description_he="אישור נסיעת עבודה, השתתפות בסמינר בחו״ל או הוצאות נסיעה מסווגים לאישורי נסיעות גם אם תוכן הסמינר שייך לתחום מקצועי אחר.",
        priority=104,
        required_any=(("נסיעה", "נסיעת", "אישור נסיעה", "חו\"ל", "ארה\"ב", "הוצאות נסיעה"), ("אישור", "לאישור", "השתתפות", "הוצאות נסיעה", "עלויות השתתפות")),
    ),
    TopicPolicy(
        policy_id="resident_messaging_administration",
        root_topic_id="root_administration",
        description_he="מאגרי מסרונים, מסרי וידאו והודעות לתושבים מסווגים למנהל עירוני.",
        priority=104,
        required_any=(("מסרונים", "מסר וידאו", "מסרי וידאו"), ("תושבים", "תושבי העיר")),
    ),
    TopicPolicy(
        policy_id="municipal_committee_protocol_decision_approval",
        root_topic_id="root_administration",
        description_he="אישור החלטות בפרוטוקולים של ועדות עירוניות כלליות הוא נושא מנהל עירוני; ועדה נושאית מפורשת עדיין מסווגת לפי התחום הנושאי שלה.",
        priority=105,
        required_any=(("אישור החלטות", "אישור החלטת"), ("פרוטוקולים של ועדות העירייה", "פרוטוקולים של ועדות המועצה", "ועדות העירייה", "ועדות המועצה")),
    ),
    TopicPolicy(
        policy_id="city_engineer_hearing_administration",
        root_topic_id="root_administration",
        description_he="שימוע למהנדס העיר מסווג למנהל עירוני ומינויים.",
        priority=104,
        required_any=(("שימוע",), ("מהנדס העיר",)),
    ),
    TopicPolicy(
        policy_id="electronic_advertising_signs_infrastructure",
        root_topic_id="root_infrastructure_environment",
        description_he="לוחות מודעות/פרסום אלקטרוניים מסווגים לתשתיות וסביבה; צומת או רחוב הם מיקום בלבד.",
        priority=104,
        required_any=(("לוח מודעות אלקטרוני", "לוחות מודעות אלקטרוניים", "מודעות אלקטרוני", "מודעות אלקטרוניים", "לוחות פרסום אלקטרוניים"),),
    ),
    TopicPolicy(
        policy_id="solar_renewable_energy_environment",
        root_topic_id="root_infrastructure_environment",
        description_he="פאנלים סולאריים, אנרגיה מתחדשת ואנרגיה ירוקה מסווגים לתשתיות וסביבה גם כשההתקנה היא על גגות מבנים.",
        priority=109,
        required_any=(("פאנלים סולאריים", "פאנלים סולריים", "פאנל סולארי", "פאנל סולרי", "סולארי", "סולרי", "אנרגיה מתחדשת", "אנרגיות מתחדשות", "אנרגיה ירוקה"),),
    ),
    TopicPolicy(
        policy_id="planning_committee_domain_representation",
        root_topic_id="root_planning_building",
        description_he="ייצוג עירוני בדיוני ועדה מחוזית לתכנון ובנייה מסווג לפי תחום הדיון התכנוני; מינוי חבר ועדה כללי נשאר מנהל עירוני.",
        priority=114,
        required_any=(("נציג", "כנציג", "לשמש כנציג"), ("דיוני הוועדה המחוזית", "וועדה מחוזית לתכנון", "ועדה מחוזית לתכנון", "תכנון ובנייה", "תכנון ולבניה")),
        negative_terms=("מינוי חבר", "מינוי חברה", "מינויים ושינויים"),
    ),
    TopicPolicy(
        policy_id="asset_registrar_authorization_assets",
        root_topic_id="root_commerce_assets",
        description_he="הסמכת רשם/רשמת נכסים מסווגת לפי תחום הנכסים ולא כמינוי מנהלי כללי.",
        priority=114,
        required_any=(("הסמכת", "להסמיך"), ("רשמת הנכסים", "רשם הנכסים", "נכסי העירייה", "נכסי העיריה")),
    ),
    TopicPolicy(
        policy_id="electric_mobility_enforcement_security",
        root_topic_id="root_security_enforcement",
        description_he="אכיפה או הסדרה של כלים חשמליים ממונעים היא נושא ביטחון ואכיפה כאשר מוקד הסעיף הוא רגולציה/אכיפה ולא תכנון תחבורתי.",
        priority=114,
        required_any=(("אכיפה", "הסדרה", "הסדרת"), ("כלים חשמליים ממונעים", "כלים חשמליים", "קורקינטים", "אופניים חשמליים")),
    ),
    TopicPolicy(
        policy_id="sewage_water_utility_infrastructure",
        root_topic_id="root_infrastructure_environment",
        description_he="תחנות שאיבת ביוב, מאגרי חירום לביוב, ניהול מים ותאגידי מים וביוב מסווגים לתשתיות וסביבה.",
        priority=114,
        required_any=(("ביוב", "מים וביוב", "תאגיד המים", "ניהול המים", "תחנת שאיבת", "מאגר חירום"),),
    ),
    TopicPolicy(
        policy_id="religious_council_services",
        root_topic_id="root_religious_services",
        description_he="מועצה דתית/מועצות דתיות ושירותיהן מסווגים לדת ושירותי דת, גם כאשר הפעולה היא מינוי או הרכב המועצה.",
        priority=116,
        required_any=(("מועצה דתית", "מועצות דתיות", "מועצות דתית"),),
    ),
    TopicPolicy(
        policy_id="air_pollution_odors_environment",
        root_topic_id="root_infrastructure_environment",
        description_he="זיהום אוויר, ריחות קשים ומפגעי ריח עירוניים מסווגים לתשתיות וסביבה.",
        priority=114,
        required_any=(("זיהום אויר", "זיהום אוויר", "ריחות קשים", "מפגעי ריח", "ריחות"),),
    ),
    TopicPolicy(
        policy_id="park_fountain_infrastructure",
        root_topic_id="root_infrastructure_environment",
        description_he="השבתה או תפעול של מזרקות ומתקני פארק ציבוריים מסווגים לתשתיות וסביבה.",
        priority=113,
        required_any=(("מזרקה", "מזרקות", "המזרקה המוזיקלית"),),
    ),
    TopicPolicy(
        policy_id="right_of_use_agreement_action",
        root_topic_id="root_agreements",
        description_he="כאשר הנושא מנוסח כהסכם/הסדרת רשות שימוש, מותר להישאר בשורש הסכמים והתקשרויות במקום לכפות הקצאה.",
        priority=101,
        required_any=(("הסכם", "הסכמי", "הסדרת"), ("רשות שימוש", "שימוש")),
    ),
    TopicPolicy(
        policy_id="permission_or_development_agreement_action",
        root_topic_id="root_agreements",
        description_he="הסכמי רשות, הסכמי פיתוח או הסכם להקמה והפעלה מסווגים לפי פעולת ההסכם גם כאשר מוזכר תחום שירות כמו דת.",
        priority=101,
        required_any=(("הסכמי רשות", "הסכמי פיתוח", "רשות ופיתוח", "הסכם להקמה", "הסכמי רשות ופיתוח"),),
    ),
    TopicPolicy(
        policy_id="lease_land_use_agreement_action",
        root_topic_id="root_agreements",
        description_he="חכירה, שינוי מטרת חכירה או חוזה חכירה הם נושאי הסכם/שימוש בקרקע ולא טקסט פרוצדורלי בלבד.",
        priority=102,
        required_any=(("חכירה", "החכרת", "מטרת חכירה", "חוזה חכירה"),),
    ),
    TopicPolicy(
        policy_id="education_use_agreement_action",
        root_topic_id="root_agreements",
        description_he="הסכם רשות/שימוש עם עמותה להפעלת מוסדות חינוך מסווג לפי פעולת ההסכם, גם אם תחום השירות הוא חינוך.",
        priority=114,
        required_any=(("הסכם רשות", "הסדרת הקצאה", "הסדרת שימוש", "רשות שימוש"), ("עמותה", "עמותת", "עמותות"), ("גן", "גני", "גנ\"י", "גני ילדים", "בית ספר", "מוסדות חינוך")),
    ),
    TopicPolicy(
        policy_id="allocations_committee_or_land_allocation",
        root_topic_id="root_allocations",
        description_he="ועדות הקצאות, הקצאות קרקע ושימוש במקרקעין מסווגים להקצאות ושימושים, ולא לתמיכות.",
        priority=100,
        required_any=(("ועדת הקצאות", "ועדת משנה להקצאות", "הקצאות מקצועית", "הקצאות קרקע", "שימוש במקרקעין", "הקצאת"),),
    ),
    TopicPolicy(
        policy_id="school_or_education_program",
        root_topic_id="root_education",
        description_he="מוסדות חינוך, בתי ספר, גנים ותוכניות בבתי ספר מסווגים לחינוך, אלא אם פעולת האג'נדה היא מכרז/התקשרות.",
        priority=96,
        required_any=(("מוסדות חינוך", "בית ספר", "בתי ספר", "בתי הספר", "ביה\"ס", "ביה ס", "כיתות", "גנים", "חרמות בבתי הספר", "תלמידים"),),
        negative_terms=("ביטול מכרז", "מכרז פומבי", "התקשרות", "פטור ממכרז"),
    ),
    TopicPolicy(
        policy_id="education_award_subject",
        root_topic_id="root_education",
        description_he="פרסי חינוך ותוכניות הוקרה חינוכיות מסווגים לחינוך.",
        priority=113,
        required_any=(("פרס חינוך", "פרסי חינוך", "חינוך פורץ דרך"),),
    ),
    TopicPolicy(
        policy_id="parent_payments_education",
        root_topic_id="root_education",
        description_he="תשלומי הורים, צהרונים וגני ילדים מסווגים לחינוך.",
        priority=97,
        required_any=(("תשלומי הורים", "צהרונים", "גני ילדים"),),
    ),
    TopicPolicy(
        policy_id="education_quality_or_committee_access",
        root_topic_id="root_education",
        description_he="השקעה בחינוך, ועדות אפיון וזכאות, מעבר בית ספר ופרוטוקולי חינוך מסווגים לחינוך.",
        priority=114,
        required_any=(("השקעה בחינוך", "השקעה נמוכה בחינוך", "השקעה הנמוכה בחינוך", "ועדות אפיון וזכאות", "אפיון וזכאות", "מעבר בית הספר", "מעבר בית ספר", "פרוטוקול חינוך"),),
    ),
    TopicPolicy(
        policy_id="kindergarten_land_use_education",
        root_topic_id="root_education",
        description_he="כאשר ייעוד מקרקעין מתואר במפורש להפעלת כיתת גן/גן ילדים, תחום השירות הדומיננטי הוא חינוך.",
        priority=113,
        required_any=(("ייעוד המקרקעין", "ייעוד מקרקעין", "מקרקעין"), ("כיתת גן", "גן ילדים", "גני ילדים")),
    ),
    TopicPolicy(
        policy_id="roof_agreement_planning",
        root_topic_id="root_planning_building",
        description_he="הסכם הגג ונתוני יחידות דיור במסגרתו מסווגים לתכנון ובנייה; מספרי יחידות, זכאים והגרלות הם מטאדאטה.",
        priority=114,
        required_any=(("הסכם הגג",),),
    ),
    TopicPolicy(
        policy_id="science_program_education",
        root_topic_id="root_education",
        description_he="תוכניות מדעניות/מדעני העתיד מסווגות לחינוך.",
        priority=97,
        required_any=(("מדעניות העתיד", "מדעני העתיד"),),
    ),
    TopicPolicy(
        policy_id="municipal_tax_or_fee_finance",
        root_topic_id="root_budget_finance",
        description_he="ארנונה, אגרות, תעריפים, הנחות במסים, סיווגי מס והכנסות עירוניות מסווגים לתקציב וכספים.",
        priority=96,
        required_any=(("ארנונה", "צו ארנונה", "אגרה", "אגרות", "תעריף", "סיווג", "מסים", "מיסים", "מיסוי", "הנחות במיסים", "הנחות במסים"),),
        negative_terms=("היטל שמירה", "שירותי שמירה"),
    ),
    TopicPolicy(
        policy_id="fiscal_exemption_or_relief_finance",
        root_topic_id="root_budget_finance",
        description_he="פטורים, הנחות, אי-גבייה או הקלות בתשלום עירוני מסווגים לפי הפעולה הכספית, גם כשהאובייקט הוא נכס.",
        priority=99,
        required_any=(("פטור", "פטורים", "הנחה", "הנחות", "לא ישולם", "לא תשולם", "אי גבייה", "אי-גבייה"), ("ארנונה", "היטל", "אגרה", "מס", "תשלום", "נכס")),
        negative_terms=("היטל שמירה", "שירותי שמירה"),
    ),
    TopicPolicy(
        policy_id="municipal_bylaw_business_regulation",
        root_topic_id="root_local_economy",
        description_he="חוקי עזר או תיקוני חוק עזר שמסדירים פעילות עסקית, מסחר, רוכלות או רישוי עסקים מסווגים לכלכלה ותעסוקה מקומית, אלא אם מופיעה פעולה כספית/תחבורתית/סביבתית מפורשת.",
        priority=94,
        required_any=(("חוק עזר", "תיקון התוספת", "תיקון סעיף"), ("רוכלות", "רוכל", "רישוי עסקים", "עסקים", "מסחר", "דוכן", "דוכנים")),
        negative_terms=("ארנונה", "היטל", "אגרה", "סלילת רחובות", "שמירה", "סביבה", "אבטחה"),
    ),
    TopicPolicy(
        policy_id="betterment_levy_finance",
        root_topic_id="root_budget_finance",
        description_he="היטל השבחה מסווג לתקציב וכספים, לא לשמירה והיטלים.",
        priority=98,
        required_any=(("היטל השבחה",),),
    ),
    TopicPolicy(
        policy_id="socioeconomic_index_benefits_finance",
        root_topic_id="root_budget_finance",
        description_he="הטבות הנובעות ממדד חברתי-כלכלי מסווגות לתקציב וכספים.",
        priority=97,
        required_any=(("הטבות",), ("מדד חברתי", "חברתי-כלכלי", "חברתי כלכלי")),
    ),
    TopicPolicy(
        policy_id="budget_line_or_reserve",
        root_topic_id="root_budget_finance",
        description_he="סעיפי תקציב, עתודות, תשלומים בלתי רגילים ותשלומי רשות מסווגים לתקציב וכספים.",
        priority=95,
        required_any=(("סעיף תקציבי", "תב\"ר", "תבר", "עתודה", "תשלומים בלתי רגילים", "תשלומי רשות", "תקציב", "תקצוב", "עדכון תקציב"),),
    ),
    TopicPolicy(
        policy_id="municipal_budget_update_action",
        root_topic_id="root_budget_finance",
        description_he="תקצוב, עדכון תקציב או אישור מועצה לעדכון תקציבי מסווגים לתקציב וכספים, גם כאשר תחום השירות הוא תרבות או ספורט.",
        priority=113,
        required_any=(("תקצוב", "עדכון תקציב", "אישור תקציבי", "עדכון תקציבי"),),
    ),
    TopicPolicy(
        policy_id="budget_objection_or_cut_action",
        root_topic_id="root_budget_finance",
        description_he="הסתייגות, קיצוץ או העברה מתקציב הם פעולת תקציב וכספים גם כאשר שם התוכנית שייך לתכנון, רווחה או תחום שירות אחר.",
        priority=115,
        required_any=(("הסתייגות", "קיצוץ", "מקצצים", "העברה תקציבית", "העברת תקציב"), ("תקציב", "מתקציב", "בתקציב", "תכנית מתוקצבת", "תוכנית מתוקצבת")),
    ),
    TopicPolicy(
        policy_id="municipal_debt_writeoff_finance",
        root_topic_id="root_budget_finance",
        description_he="מחיקת חובות או פרוטוקול למחיקת חובות מסווגים לתקציב וכספים.",
        priority=112,
        required_any=(("מחיקת חובות", "למחיקת חובות"),),
    ),
    TopicPolicy(
        policy_id="road_safety_traffic_calming",
        root_topic_id="root_transport_safety",
        description_he="תאונות דרכים, פסי האטה, באמפרים, מהירות ובטיחות בדרכים מסווגים לתחבורה ובטיחות.",
        priority=96,
        required_any=(("תאונות דרכים", "אפס תאונות", "תאונות", "בטיחות בדרכים"), ("באמפר", "באמפרים", "פס האטה", "פסי האטה", "מהירות", "תחבורתית", "כביש")),
    ),
    TopicPolicy(
        policy_id="street_paving_bylaw_transport",
        root_topic_id="root_transport_safety",
        description_he="תיקון חוק עזר בנושא סלילת רחובות מסווג לתחבורה ובטיחות משום שהחוק מסדיר תשתית רחובות.",
        priority=112,
        required_any=(("חוק עזר", "תיקון סעיף", "חוק העזר"), ("סלילת רחובות", "סלילת רחוב", "רחובות")),
    ),
    TopicPolicy(
        policy_id="environmental_bylaw_or_waste_collection",
        root_topic_id="root_infrastructure_environment",
        description_he="חוקי עזר, הוראות שעה או החלטות העוסקים במניעת רעש, פינוי אשפה, מפגעים או אישור הגנת הסביבה מסווגים לתשתיות וסביבה.",
        priority=111,
        required_any=(("חוק עזר", "הוראת שעה", "למניעת מפגעים"), ("מניעת רעש", "פינוי אשפה", "אשפה", "מפגעים", "הגנת הסביבה", "איכות הסביבה")),
    ),
    TopicPolicy(
        policy_id="environmental_committee_or_report",
        root_topic_id="root_infrastructure_environment",
        description_he="דוח או ועדה בנושא איכות/הגנת הסביבה מסווגים לתשתיות וסביבה.",
        priority=110,
        required_any=(("ועדת איכות הסביבה", "דוח ועדת איכות הסביבה", "הגנת הסביבה", "איכות הסביבה"),),
    ),
    TopicPolicy(
        policy_id="parking_permits_or_arrangements_transport",
        root_topic_id="root_transport_safety",
        description_he="תווי חניה, חנייה, חניונים והסדרי חניה מסווגים לתחבורה ובטיחות.",
        priority=96,
        required_any=(("תו חניה", "תווי חניה", "תו חנייה", "תווי חנייה", "הסדרי חניה", "הסדרי חנייה", "חניון", "חניונים"),),
    ),
    TopicPolicy(
        policy_id="domestic_violence_default_welfare",
        root_topic_id="root_welfare_social",
        description_he="אלימות במשפחה מסווגת כברירת מחדל לרווחה ושירותים חברתיים; רק ראיות אכיפה מפורשות מעבירות לביטחון ואכיפה.",
        priority=95,
        required_any=(("אלימות במשפחה",),),
        negative_terms=("משטרה", "אכיפה", "מעצר", "כתב אישום", "סיור", "פיקוח"),
    ),
    TopicPolicy(
        policy_id="public_violence_crime_security",
        root_topic_id="root_security_enforcement",
        description_he="אלימות, פשע או פשיעה במרחב הציבורי מסווגים לביטחון ואכיפה; מילים כמו רחוב/רחובות הן הקשר מקום ולא תחבורה.",
        priority=114,
        required_any=(("אלימות", "פשע", "פשיעה"), ("רחוב", "רחובות", "רחובות העיר", "מרחב ציבורי", "בעיר", "קהילתי", "קהילתית")),
        negative_terms=("אלימות במשפחה",),
    ),
    TopicPolicy(
        policy_id="homelessness_welfare_subject",
        root_topic_id="root_welfare_social",
        description_he="דרי רחוב וחסרי בית מסווגים לרווחה ושירותים חברתיים, גם כאשר מופיעה המילה רחוב.",
        priority=113,
        required_any=(("דרי רחוב", "חסרי בית"),),
    ),
    TopicPolicy(
        policy_id="food_security_welfare_subject",
        root_topic_id="root_welfare_social",
        description_he="ביטחון תזונתי הוא נושא רווחה, לא ביטחון ואכיפה.",
        priority=114,
        required_any=(("ביטחון תזונתי", "בטחון תזונתי"),),
    ),
    TopicPolicy(
        policy_id="evacuee_resident_support_welfare",
        root_topic_id="root_welfare_social",
        description_he="סיוע או טיפול בתושבים מפונים, משפחות שפונו או נפגעי אירוע/אסון מסווגים לרווחה ושירותים חברתיים, אלא אם הראיה עוסקת במפורש בתיקון מבנה או היתר בנייה.",
        priority=113,
        required_any=(("מפונים", "מפונה", "שפונו", "פונו", "פינוי תושבים", "תושבים שפונו", "משפחות שפונו", "נפגעי אסון", "נפגעי מלחמה"),),
        negative_terms=("היתר בנייה", "היתר בניה", "תיקון מבנה", "מבנה מסוכן", "מבנים מסוכנים"),
    ),
    TopicPolicy(
        policy_id="paramedical_treatment_benefits_welfare",
        root_topic_id="root_welfare_social",
        description_he="זכאות או קריטריונים לטיפולים פרא-רפואיים או לשירותי ביטוח לאומי מסווגים לרווחה ושירותים חברתיים כל עוד אין שורש בריאות ייעודי.",
        priority=112,
        required_any=(("טיפולים פרא רפואיים", "טיפולים פרה רפואיים", "ביטוח לאומי"),),
    ),
    TopicPolicy(
        policy_id="elderly_loneliness_monitoring_welfare",
        root_topic_id="root_welfare_social",
        description_he="טיפול בבדידות קשישים, לחצני מצוקה, חיישני תנועה וביקורי רווחה לקשישים מסווגים לרווחה ושירותים חברתיים.",
        priority=114,
        required_any=(("קשישים", "קשישים עריריים", "בדידות קשישים"), ("לחצני מצוקה", "חיישני תנועה", "ניטור", "ביקורים תקופתיים", "בדידות")),
    ),
    TopicPolicy(
        policy_id="street_cats_welfare_subject",
        root_topic_id="root_welfare_social",
        description_he="חתולי רחוב וגורי חתולים מסווגים לרווחה ושירותים חברתיים, ולא לתחבורה בגלל המילה רחוב.",
        priority=114,
        required_any=(("חתולי רחוב", "גורי חתולי רחוב"),),
    ),
    TopicPolicy(
        policy_id="environmental_recycling_subject",
        root_topic_id="root_infrastructure_environment",
        description_he="מחזור, מיחזור והסברה סביבתית מסווגים לתשתיות וסביבה.",
        priority=112,
        required_any=(("מחזור", "מיחזור", "הסברה סביבתית", "סביבתית", "סביבתי"),),
    ),
    TopicPolicy(
        policy_id="urban_greening_public_space_environment",
        root_topic_id="root_infrastructure_environment",
        description_he="נטיעה, שתילה, כריתה או טיפול בעצים ובצמחייה במרחב הציבורי מסווגים לתשתיות וסביבה.",
        priority=112,
        required_any=(("עץ", "עצים", "פיקוס", "שתילת", "נטיעת", "נטיעה", "כריתת", "גיזום", "צמחייה", "צמחיה"), ("מרחב ציבורי", "רחוב", "רחובות", "פארק", "גינה", "עירוני", "בעיר")),
    ),
    TopicPolicy(
        policy_id="information_security_subject",
        root_topic_id="root_security_enforcement",
        description_he="אבטחת מידע, הגנת פרטיות וסייבר מסווגים לביטחון ואכיפה.",
        priority=112,
        required_any=(("אבטחת מידע", "סייבר", "הגנת פרטיות", "הגנת הפרטיות"),),
    ),
    TopicPolicy(
        policy_id="workplace_surveillance_privacy",
        root_topic_id="root_hr_labor",
        description_he="מצלמות מעקב או ניטור במשרדי העירייה מסווגים לפרטיות עובדים/כוח אדם, לא לסדר יום כללי.",
        priority=112,
        required_any=(("מצלמות מעקב", "מעקב נסתר", "מצלמות נסתרות", "ניטור"), ("משרדי העירייה", "מנהלות", "עובדים", "עובדי עירייה")),
    ),
    TopicPolicy(
        policy_id="emergency_committee_drill_security",
        root_topic_id="root_security_enforcement",
        description_he="דיוני מל״ח, פיקוד העורף, תרגילי חירום ואתרי הרס מסווגים לביטחון ואכיפה/מוכנות לחירום.",
        priority=112,
        required_any=(("מל\"ח", "פקע\"ר", "פיקוד העורף", "יקל\"ר", "אתרי הרס", "תרגיל חירום", "ועדת חירום", "מוכנות לחירום", "מערך חירום", "פעילות חבלנית עוינת"),),
        negative_terms=("מלגה", "מלגות"),
    ),
    TopicPolicy(
        policy_id="municipal_protection_emergency_security",
        root_topic_id="root_security_enforcement",
        description_he="מיגון עירוני, מקלטים ודיוני חירום על הגנה אזרחית מסווגים לביטחון ואכיפה/מוכנות לחירום.",
        priority=112,
        required_any=(("מיגון", "מקלט", "מקלטים", "מרחב מוגן", "מרחבים מוגנים"),),
        negative_terms=("ציוד מגן אישי",),
    ),
    TopicPolicy(
        policy_id="flooding_drainage_infrastructure",
        root_topic_id="root_infrastructure_environment",
        description_he="הצפות, ניקוז, ניהול נגר ותשתיות מים/צנרת מסווגים לתשתיות וסביבה, לא לתכנון ובנייה כללי.",
        priority=112,
        required_any=(("הצפות", "הצפה", "ניקוז", "ניהול נגר"), ("תשתיות", "צינורות", "קווים", "ניקוז", "נגר", "מים")),
    ),
    TopicPolicy(
        policy_id="scholarship_grants_supports",
        root_topic_id="root_supports",
        description_he="מלגות, תקני מלגה והשלמות מלגה הן תמיכות/הטבות כספיות עירוניות, גם כשהנהנים הם סטודנטים.",
        priority=112,
        required_any=(("מלגה", "מלגות", "מלגת", "תקני מלגה", "מפעל הפיס", "פר\"ח"),),
    ),
    TopicPolicy(
        policy_id="sport_support_criteria",
        root_topic_id="root_supports",
        description_he="תבחינים, ניקוד או תקציב לחלוקת תמיכות ספורט מסווגים לתמיכות, גם כשהתחום המקצועי הוא ספורט.",
        priority=112,
        required_any=(("תבחין", "תבחינים", "שיטת ניקוד", "ניקוד", "תקציב", "תקצוב"), ("רשות הספורט", "סל הספורט", "קבוצות ספורט", "עמותות", "מסגרות ספורט", "ליגה")),
    ),
    TopicPolicy(
        policy_id="public_transport_project_funding",
        root_topic_id="root_transport_safety",
        description_he="מימון, גירעון או תקצוב של פרויקט תחבורה ציבורית מסווגים לתחבורה ובטיחות, עם היבט כספי משני.",
        priority=112,
        required_any=(("תחבורה ציבורית", "פרויקט התחבורה", "משרד התחבורה", "כיכר רמון"), ("גרעון", "גירעון", "מימון", "תקציב", "מקור לכיסוי", "כיסוי")),
    ),
    TopicPolicy(
        policy_id="customer_credit_refund_finance",
        root_topic_id="root_budget_finance",
        description_he="יתרות זכות, החזר כספים ללקוח ודרישות תשלום מסווגים לתקציב וכספים.",
        priority=111,
        required_any=(("יתרת זכות", "יתרות זכות", "החזר כספים", "דרישת התשלום", "דרישת תשלום", "לקוח"),),
    ),
    TopicPolicy(
        policy_id="domestic_violence_enforcement_explicit",
        root_topic_id="root_security_enforcement",
        description_he="אלימות במשפחה עם ראיות מפורשות למשטרה, אכיפה, מעצר או פיקוח מסווגת לביטחון ואכיפה.",
        priority=112,
        required_any=(("אלימות במשפחה",), ("משטרה", "אכיפה", "מעצר", "כתב אישום", "סיור", "פיקוח")),
    ),
    TopicPolicy(
        policy_id="emergency_preparedness_security",
        root_topic_id="root_security_enforcement",
        description_he="מוכנות לרעידת אדמה ומערכות התראה מסווגות לביטחון ואכיפה.",
        priority=111,
        required_any=(("רעידת אדמה", "מערכת התראה"),),
    ),
    TopicPolicy(
        policy_id="allergy_epipen_public_safety",
        root_topic_id="root_security_enforcement",
        description_he="אלרגיות מסכנות חיים ומזרקי אפיפן במרחב הציבורי מסווגים לביטחון ואכיפה כבטיחות ציבורית.",
        priority=111,
        required_any=(("אלרגיות מסכנות חיים", "מזרקי אפיפן", "אפיפן"),),
    ),
    TopicPolicy(
        policy_id="violence_reduction_security",
        root_topic_id="root_security_enforcement",
        description_he="מיגור תופעת האלימות מסווג לביטחון ואכיפה גם כאשר הוא מנוסח כוועדה.",
        priority=111,
        required_any=(("מיגור תופעת האלימות", "מיגור אלימות", "ועדה למיגור אלימות", "הוועדה למיגור אלימות"),),
    ),
    TopicPolicy(
        policy_id="youth_public_order",
        root_topic_id="root_security_enforcement",
        description_he="מפגשי בני נוער בפארקים בלילות הם עניין סדר ציבורי/אכיפה, לא תחבורה ובטיחות.",
        priority=94,
        required_any=(("מפגשי בני נוער", "בני נוער בפארקים", "פארקים בלילות", "התמודדות עם מפגשי בני נוער"),),
    ),
    TopicPolicy(
        policy_id="hr_employment_conditions",
        root_topic_id="root_hr_labor",
        description_he="עובדים, כוח אדם, עבודה נוספת, שכר ומועד תחילת עבודה מסווגים לכוח אדם ועובדים.",
        priority=93,
        required_any=(("עובדים מושאלים", "עובדים זמניים", "כוח אדם", "כח אדם", "עבודה נוספת לעובדי", "העסקת עובד", "העסקת עובדים", "משרת אמון", "משרות אמון", "חוזה אישי", "נהג ראש העיר", "שכרו", "תשלום שכרו", "תחילת עבודתו", "מועד תחילת עבודתו"),),
    ),
    TopicPolicy(
        policy_id="senior_municipal_officer_appointment",
        root_topic_id="root_administration",
        description_he="מינוי או אצילת סמכויות לבעל תפקיד סטטוטורי בכיר בעירייה מסווגים למנהל עירוני ומינויים; שכר הוא היבט תומך בלבד.",
        priority=111,
        required_any=(("מינוי", "אצילת סמכויות", "העברת סמכויות"), ("מהנדס העיר", "גזבר העירייה", "מנכ\"ל העירייה", "מנכל העירייה")),
    ),
    TopicPolicy(
        policy_id="municipal_role_appointment_administration",
        root_topic_id="root_administration",
        description_he="מינוי או איוש תפקיד עירוני מסווג למנהל עירוני ומינויים גם אם מוזכר מכרז כחלק מהליך האיוש.",
        priority=114,
        required_any=(("מינוי", "איוש", "פרסום מכרז"), ("לתפקיד", "תפקיד", "דובר", "בעל תפקיד")),
    ),
    TopicPolicy(
        policy_id="committee_membership_change_administration",
        root_topic_id="root_administration",
        description_he="מינויים ושינויים בהרכב ועדה הם ממשל ומינויים גם כאשר שם הוועדה כולל מכרזים; רק פעולת התקשרות/מכרז נשארת בהסכמים.",
        priority=115,
        required_any=(("מינויים ושינויים", "שינויים בהרכב", "שינוי בהרכב", "חילופי גברי", "הרכב"), ("ועדה", "וועדה", "ועדת", "וועדת", "דירקטוריון")),
        negative_terms=("מכרז פומבי", "ביטול מכרז", "פטור ממכרז", "התקשרות", "הסכם"),
    ),
    TopicPolicy(
        policy_id="committee_appointment_or_membership_administration",
        root_topic_id="root_administration",
        description_he="מינוי, הארכת מינוי, כהונה או חברות בוועדה/דירקטוריון מסווגים למנהל עירוני ומינויים.",
        priority=110,
        required_any=(("מינוי", "מינויים", "מינוי חבר", "הארכת מינוי", "הארכת המינויים", "להאריך את מינוי", "להאריך את מינויה", "להאריך את מינויו", "מינויו", "מינויה", "כהונה", "חברות"), ("ועדה", "וועדה", "ועדת", "דירקטוריון", "תאגידים", "איגודים")),
        negative_terms=("ועדת מכרזים", "וועדת מכרזים", "בטיחות בדרכים"),
    ),
    TopicPolicy(
        policy_id="director_board_appointment_administration",
        root_topic_id="root_administration",
        description_he="מינוי כדירקטור או לתפקיד בדירקטוריון מסווג למנהל עירוני ומינויים.",
        priority=111,
        required_any=(("מינוי",), ("דירקטור", "דירקטוריון", "יו\"ר דירקטוריון")),
    ),
    TopicPolicy(
        policy_id="council_governance_attendance",
        root_topic_id="root_administration",
        description_he="נוכחות, איחורים והיעדרויות של חברי מועצה בישיבות מליאה/ועדות הם נושא מנהל עירוני.",
        priority=93,
        required_any=(("חברי מועצה", "חברת מועצה", "מועצת העיר"), ("איחורים", "היעדרויות", "נוכחות", "ישיבות מליאה", "ועדות")),
    ),
    TopicPolicy(
        policy_id="municipal_corporation_audit_governance",
        root_topic_id="root_administration",
        description_he="תיקון התנהלות, ביקורת או דו״ח מבקר על תאגידים עירוניים מסווגים למנהל עירוני ומינויים כממשל תאגידי עירוני.",
        priority=112,
        required_any=(("תאגידים עירוניים", "תאגיד עירוני", "חברות עירוניות", "חברה עירונית"), ("מבקר המדינה", "דו\"ח מבקר", "דוח מבקר", "ביקורת", "התנהלות", "תיקון ההתנהלות")),
    ),
    TopicPolicy(
        policy_id="local_economy_industry_employment",
        root_topic_id="root_local_economy",
        description_he="מפעלים, תעשייה, עסקים, מקומות עבודה ותעסוקה מקומית מסווגים לכלכלה ותעסוקה מקומית.",
        priority=92,
        required_any=(("מפעל", "מפעלים", "תעשייה", "תעשיה", "עסקים", "מקומות עבודה", "תעסוקה"), ("אשדוד", "בעיר", "תושבי העיר", "מקומי", "מקומית", "אזור תעשייה", "אזור תעשיה")),
    ),
    TopicPolicy(
        policy_id="sports_and_recreation_subject",
        root_topic_id="root_culture_sport",
        description_he="קבוצות ספורט, ליגות, אליפויות, גביעים וענפי ספורט מסווגים לתרבות וספורט.",
        priority=92,
        required_any=(("ספורט", "כדורגל", "כדוריד", "כדורסל", "ליגה", "אליפות", "גביע", "קבוצת ספורט", "ספורטאי"),),
    ),
    TopicPolicy(
        policy_id="sports_grant_support",
        root_topic_id="root_supports",
        description_he="מענקים או תמיכות לקבוצות וענפי ספורט מסווגים כתמיכות, כאשר הספורט הוא תחום השירות.",
        priority=115,
        required_any=(("מענק", "מענקי", "מענקים", "תמיכה", "תמיכות"), ("ספורט", "כדורסל", "כדורגל", "כדוריד", "ליגה", "אליפות", "גביע", "קבוצת ספורט")),
    ),
    TopicPolicy(
        policy_id="recreation_facility_activity",
        root_topic_id="root_culture_sport",
        description_he="פעילות, מנויים והנחות במתקני פנאי וספורט מסווגים לתרבות וספורט.",
        priority=105,
        required_any=(("קאנטרי", "בריכה", "מרכז ספורט", "מתקן ספורט"), ("פעילות", "מנוי", "מנויים", "הנחות", "עלויות")),
    ),
    TopicPolicy(
        policy_id="youth_sports_club_alternative_culture",
        root_topic_id="root_culture_sport",
        description_he="חלופה או פעילות לבני נוער בעקבות סגירת קבוצת/מועדון ספורט מסווגת לתרבות וספורט.",
        priority=114,
        required_any=(("בני נוער", "נוער", "צעירים"), ("קבוצת", "מועדון", "קבוצת ספורט", "קבוצת כדורגל", "עירוני"), ("חלופה", "סגירת", "סגירה", "במקרה של סגירת")),
    ),
    TopicPolicy(
        policy_id="local_art_culture_subject",
        root_topic_id="root_culture_sport",
        description_he="מתן במה לאמנות מקומית ולאמנים מקומיים מסווג לתרבות וספורט.",
        priority=113,
        required_any=(("אמנות מקומית", "אומנות מקומית", "אמנים מקומיים", "אומנים מקומיים"),),
    ),
    TopicPolicy(
        policy_id="land_lease_or_rights_allocation",
        root_topic_id="root_allocations",
        description_he="עסקאות חכירה, רמ\"י, ויתור על זכויות חכירה או זכויות במגרש/גוש/חלקה מסווגים להקצאות ושימושים כאשר אינם מנוסחים כהסכם התקשרות עצמאי.",
        priority=111,
        required_any=(("עסקאות חכירה", "זכות חכירה", "זכויות חכירה", "רמ\"י", "רמי", "רשות מקרקעי ישראל", "מגרש", "גוש", "חלקה"),),
        negative_terms=("הסכם", "הסכמי", "החלקה", "החלקה על הקרח"),
    ),
    TopicPolicy(
        policy_id="lease_right_cancellation_allocation",
        root_topic_id="root_allocations",
        description_he="ביטול או ויתור על זכות חכירה הוא שינוי בזכות מקרקעין ולכן מסווג להקצאות ושימושים, גם כשהמסמך המשפטי נקרא הסכם.",
        priority=114,
        required_any=(("ביטול זכות חכירה", "לביטול זכות חכירה", "ויתור על זכות חכירה", "ויתור על חלק מזכות חכירה"),),
    ),
    TopicPolicy(
        policy_id="urban_renewal_planning",
        root_topic_id="root_planning_building",
        description_he="התחדשות עירונית, פינוי-בינוי או קידום מתחם בותמ\"ל הם נושאי תכנון ובנייה.",
        priority=112,
        required_any=(("התחדשות עירונית", "פינוי בינוי", "פינוי-בינוי", "ותמ\"ל", "ותמל", "מתחם התחדשות"),),
        negative_terms=("היטל השבחה",),
    ),
    TopicPolicy(
        policy_id="statutory_plan_rights_planning",
        root_topic_id="root_planning_building",
        description_he="תכנית/תוכנית סטטוטורית הכוללת איחוד וחלוקה או תוספת זכויות בנייה מסווגת לתכנון ובנייה.",
        priority=114,
        required_any=(("תכנית", "תוכנית", "תכנית מס", "תוכנית מס"), ("איחוד וחלוקה", "זכויות בנייה", "זכויות בניה", "תוספת זכויות")),
    ),
    TopicPolicy(
        policy_id="air_rights_planning",
        root_topic_id="root_planning_building",
        description_he="זכויות אוויר/זכויות בנייה הן זכויות תכנוניות ולכן מסווגות לתכנון ובנייה גם כאשר הן מופיעות בשאילתה.",
        priority=114,
        required_any=(("זכויות אוויר", "זכויות אויר", "זכויות בנייה", "זכויות בניה"),),
    ),
    TopicPolicy(
        policy_id="housing_sale_marketing_planning",
        root_topic_id="root_planning_building",
        description_he="פרסום, שיווק או מכירה של דירות לקבוצת אוכלוסייה מסווגים לתכנון ובנייה/דיור כאשר אין פעולה כספית או התקשרותית מפורשת.",
        priority=113,
        required_any=(("דירות", "דיור", "מכירת דירות", "שיווק דירות"), ("פרסום", "מכירה", "מכירת", "שיווק", "מגזר")),
        negative_terms=("הסכם", "התקשרות", "תקציב", "ארנונה"),
    ),
    TopicPolicy(
        policy_id="activity_complex_or_caravan_parking_plan_planning",
        root_topic_id="root_planning_building",
        description_he="תוכנית/הקמה של מתחם פעילות או פתרון חניית קרוואנים מסווגים לתכנון ובנייה כאשר מוקד הסעיף הוא מימוש תכנית או שימוש בשטח.",
        priority=113,
        required_any=(("תוכנית", "תכנית", "הקמה", "מימוש התוכנית", "מתחם פעילות", "חניית קרוואנים"), ("מתחם", "שטח", "חניית קרוואנים", "קרוואנים")),
    ),
    TopicPolicy(
        policy_id="waterfront_public_site_operation_planning",
        root_topic_id="root_planning_building",
        description_he="תפעול, אחריות או החזרת פעילות של אתר ציבורי חופי/מרינה/אגם מסווגים לתכנון ובנייה בהיעדר שורש ייעודי לניהול אתרים ציבוריים.",
        priority=112,
        required_any=(("חוף", "חופי", "חופים", "מרינה", "אגם"), ("פעילות", "טיפול", "אחריות", "העברת האחריות", "תפעול", "כשל מתמשך")),
        negative_terms=("מיזם", "יוזמה"),
    ),
    TopicPolicy(
        policy_id="public_sculpture_culture",
        root_topic_id="root_culture_sport",
        description_he="הקמת פסלים ציבוריים מסווגת לתרבות וספורט.",
        priority=92,
        required_any=(("פסל", "פסל ציבורי", "הקמת פסל"),),
    ),
    TopicPolicy(
        policy_id="municipal_culture_prizes",
        root_topic_id="root_culture_sport",
        description_he="פרסים עירוניים, תקנוני פרסים ופרסי תיאטרון מסווגים לתרבות וספורט.",
        priority=105,
        required_any=(("פרסים עירוניים", "תקנוני הפרסים", "תקנון פרס", "פרס התיאטרון"),),
    ),
    TopicPolicy(
        policy_id="child_status_committee_or_welfare_committee",
        root_topic_id="root_welfare_social",
        description_he="ועדה לקידום מעמד הילד ווועדת רווחה מסווגות לרווחה ושירותים חברתיים, לא למנהל כללי.",
        priority=92,
        required_any=(("ועדה לקידום מעמד הילד", "קידום מעמד הילד", "ועדת רווחה", "רווחה"),),
    ),
    TopicPolicy(
        policy_id="guard_services_and_levy",
        root_topic_id="root_guard_services",
        description_he="שירותי שמירה והיטל שמירה מסווגים לשמירה והיטלים.",
        priority=91,
        required_any=(("שירותי שמירה", "היטל שמירה", "גביית היטל שמירה"),),
    ),
    TopicPolicy(
        policy_id="support_committee",
        root_topic_id="root_supports",
        description_he="ועדת תמיכות או ועדת משנה לתמיכות מסווגות לתמיכות.",
        priority=90,
        required_any=(("ועדת תמיכות", "ועדת משנה לתמיכות", "תמיכות"),),
        negative_terms=("ועדת הקצאות", "הקצאות קרקע"),
    ),
    TopicPolicy(
        policy_id="support_advance_or_donation_committee",
        root_topic_id="root_supports",
        description_he="מקדמות לעמותות, ועדת תרומות ופרוטוקולי תרומות מסווגים לתמיכות ולא לתקציב כללי.",
        priority=113,
        required_any=(("מקדמה", "מקדמות", "ועדת תרומות", "תרומה", "תרומות"), ("עמותה", "עמותות", "מוסד", "מוסדות", "ועדת תרומות")),
    ),
    TopicPolicy(
        policy_id="absorption_committee_welfare",
        root_topic_id="root_welfare_social",
        description_he="ועדת קליטה וקליטת תושבים/עולים מסווגות לרווחה ושירותים חברתיים כאשר אינן מנוסחות כתמיכה כספית מפורשת.",
        priority=113,
        required_any=(("ועדת קליטה", "קליטת עולים", "קליטת תושבים", "קליטה"),),
        negative_terms=("תמיכה", "תמיכות", "מקדמה", "מקדמות", "תקציב"),
    ),
    TopicPolicy(
        policy_id="drug_abuse_committee_security",
        root_topic_id="root_security_enforcement",
        description_he="ועדה למאבק בנגע הסמים או מניעת שימוש בסמים מסווגות לביטחון ואכיפה/מניעה ציבורית ולא למנהל כללי.",
        priority=114,
        required_any=(("נגע הסמים", "סמים מסוכנים", "מאבק בסמים", "מניעת סמים"),),
    ),
    TopicPolicy(
        policy_id="land_action_real_estate_allocation",
        root_topic_id="root_allocations",
        description_he="פעולה/עשייה במקרקעין או הצטרפות לפעולה במקרקעין מסווגת להקצאות ושימושים.",
        priority=113,
        required_any=(("מקרקעין",), ("הצטרפות", "עשייה", "פעולה", "ייעוד")),
        negative_terms=("כיתת גן", "גן ילדים", "גני ילדים"),
    ),
    TopicPolicy(
        policy_id="municipal_audit_carrier_only",
        root_topic_id="root_administration",
        description_he="דו\"ח ביקורת או ועדת ביקורת ללא תחום נושאי נקי מסווגים למנהל עירוני כמנגנון ביקורת.",
        priority=40,
        required_any=(("דוח ביקורת", "דו\"ח ביקורת", "ועדת ביקורת", "ביקורת"),),
    ),
)


def topic_policy_prompt_payload() -> list[dict[str, Any]]:
    return [
        {
            "policy_id": policy.policy_id,
            "root_topic_id": policy.root_topic_id,
            "description_he": policy.description_he,
            "priority": policy.priority,
            "required_any": [list(group) for group in policy.required_any],
            "negative_terms": list(policy.negative_terms),
        }
        for policy in TOPIC_POLICIES
    ]


def topic_policy_matches(text: str, *, limit: int = 3) -> list[dict[str, Any]]:
    normalized = normalize_for_search(text or "")
    if not normalized:
        return []
    matches: list[dict[str, Any]] = []
    for policy in TOPIC_POLICIES:
        blocked = [term for term in policy.negative_terms if _term_in_text(term, normalized)]
        if blocked:
            continue
        required_hits: list[str] = []
        missing_group = False
        for group in policy.required_any:
            group_hits = [term for term in group if _term_in_text(term, normalized)]
            if not group_hits:
                missing_group = True
                break
            required_hits.extend(group_hits)
        if missing_group:
            continue
        positive_hits = [term for term in policy.positive_terms if _term_in_text(term, normalized)]
        all_hits = _dedupe([*required_hits, *positive_hits])
        score = float(policy.priority) + min(9, len(all_hits)) / 10.0
        matches.append(
            {
                "policy_id": policy.policy_id,
                "root_topic_id": policy.root_topic_id,
                "description_he": policy.description_he,
                "matched_terms": all_hits,
                "priority": policy.priority,
                "score": round(score, 3),
            }
        )
    matches.sort(key=lambda row: (float(row["score"]), int(row["priority"]), len(row.get("matched_terms") or [])), reverse=True)
    return matches[: max(1, int(limit))]


def best_topic_policy_match(text: str) -> dict[str, Any] | None:
    matches = topic_policy_matches(text, limit=1)
    return matches[0] if matches else None


def adjudicate_root_topic(*, subject: str, dicta_root_topic_id: str | None, fallback_root_topic_id: str | None = None) -> dict[str, Any]:
    match = best_topic_policy_match(subject)
    if match:
        root_topic_id = str(match["root_topic_id"])
        if dicta_root_topic_id:
            decision = "dicta_agreed_with_policy" if root_topic_id == str(dicta_root_topic_id or "") else "policy_corrected_dicta_root"
        else:
            decision = "policy_agreed_with_fallback" if root_topic_id == str(fallback_root_topic_id or "") else "policy_corrected_fallback_root"
        return {
            "root_topic_id": root_topic_id,
            "policy_id": match["policy_id"],
            "policy_description_he": match["description_he"],
            "matched_terms": match.get("matched_terms") or [],
            "decision": decision,
            "dicta_root_topic_id": dicta_root_topic_id,
            "fallback_root_topic_id": fallback_root_topic_id,
        }
    root_topic_id = dicta_root_topic_id or fallback_root_topic_id
    return {
        "root_topic_id": root_topic_id,
        "policy_id": None,
        "policy_description_he": None,
        "matched_terms": [],
        "decision": "dicta_root_used_no_policy" if dicta_root_topic_id else "fallback_root_used_no_policy",
        "dicta_root_topic_id": dicta_root_topic_id,
        "fallback_root_topic_id": fallback_root_topic_id,
    }


def clean_protocol_subject_text(value: Any) -> str:
    text = strip_topic_carrier_prefixes(str(value or ""))
    if not text:
        return ""
    text = text.strip(" *•")
    text = re.sub(r"\bבנו\s+[\"'׳״]?שא[\"'׳״]?\b", "בנושא", text)
    text = re.sub(r"בבת\(\s*,?\s*\)י\s+הספר", "בבתי הספר", text)
    text = re.sub(r"בתי\s+ספרים\b", "בתי ספר", text)
    text = re.sub(r"^\)?\(?\s*נספח\s+[\u0590-\u05FF]\s*[.)]?\s*", "", text)
    text = strip_topic_carrier_prefixes(text)
    text = re.sub(r"^(?:\)?\([^)]{0,30}\)\s*)?(?:מתאריך\s+)?\d+(?:\.\d+)?\s*['׳״.\-–,\s]*", "", text)
    text = re.sub(r"^מתאריך\s+\d+(?:\.\d+)?\s*['׳״.\-–,\s]*", "", text)
    text = re.sub(r"^(?:שאילת[אה]|שאילתה)\s+של\b.{0,120}?\s*בנושא\s+", "", text)
    text = re.sub(r"^(?:שאילת[אה]|שאילתה)\s+בנושא\s+", "", text)
    text = re.sub(r"^אישור\s+מועצת\s+העירייה\s+וכן\s+לחתום\s+על\s+חוזה\s+ל", "", text)
    text = re.sub(r"^בקשה\s+לאישור\s+", "", text)
    text = re.sub(r"^(?:אישור\s+)?הסכם\s+ל(?=ביטול\s+זכות\s+חכירה\b)", "", text)
    text = re.sub(r"^(ביטול\s+זכות\s+חכירה\b)\s*[.;:]\s*\d+(?:\.\d+)?\s*['׳״.]?.*$", r"\1", text)
    text = re.sub(r"^(?:הצעה\s+לסדר(?:\s+יום)?|נושא\s+לדיון)\s*[-–:]?\s+", "", text)
    text = re.sub(r"^(?:מצ\"?ל\s*[-–]?\s*)+", "", text)
    text = re.sub(r"\s*[-–]?\s*מצ\"?ל.*$", "", text)
    text = re.sub(r"\s+מצורפ(?:ת|ות|ים)?\b.*$", "", text)
    text = re.sub(r",?\s+בקשת(?:ו|ה|ם|ן)?\s+של\b.*$", "", text)
    text = re.sub(r",?\s+בקשה\s+של\b.*$", "", text)
    text = re.sub(r"\s*\)?\s*בקשת(?:ו|ה|ם|ן)?\s+של\b.*$", "", text)
    text = re.sub(r"\s*\)?\s*בקשה\s+של\b.*$", "", text)
    text = re.sub(r",?\s+לבקשת(?:ו|ה|ם|ן)?\s+של\b.*$", "", text)
    text = re.sub(r",?\s+בבקשה\b.*$", "", text)
    text = re.sub(r",?\s+לאור\s+אי\b.*$", "", text)
    text = re.sub(r",?\s+מאת\b.*$", "", text)
    text = re.sub(r",?\s+על\s+ידי\b.*$", "", text)
    text = re.sub(r"\s+הוקראה\b.*$", "", text)
    text = re.sub(r"\s+השאלה\b.*$", "", text)
    text = re.sub(r"\s+התשובה\b.*$", "", text)
    text = re.sub(r"\s+השנה\s+לעומת\s+שנה\s+שעברה\b.*$", "", text)
    text = re.sub(r"\(?\b\d{1,2}\.\d{1,2}\.\d{2,4}\b\)?", " ", text)
    text = _strip_temporal_subject_parts(text)
    text = re.sub(r"['׳״]+(?=[\u0590-\u05FF])", "", text)
    text = re.sub(r"\s+156\d+\b.*$", "", text)
    text = _best_repeated_subject(text)
    text = re.sub(r"\s*\.\s*(?=[\u0590-\u05FF])", " ", text)
    text = re.sub(r"(?<=[\u0590-\u05FF])\d+\b", "", text)
    text = re.sub(r"\s*\.\s*[^.]{0,100}?מופיעים\s+בפרוטוקול\s+המלא.*$", "", text)
    text = re.sub(r"\s+משימות\b.*$", "", text)
    text = re.sub(r"^(?:דו\"?ח\s+)?ביקורת\s+(?=התמודדות|טיפול|מניעת|בדיקת|קידום)", "", text)
    text = re.sub(r"\s*[-–.]?\s*הצעה\s+לסדר.*$", "", text)
    text = re.sub(r"\s*[-–]\s*דיון\s+עפ\"?י\b.*$", "", text)
    text = re.sub(r"\s+מס\s*\d+(?:\.\d+)?(?:\s+מתאריך.*)?$", "", text)
    text = re.sub(r"\s+שנבחר(?:ה)?\s+במכרז\b.*$", "", text)
    text = re.sub(r"(מהנדס העיר|גזבר העירייה|מנכ\"ל העירייה|מנכל העירייה)\s+(?:(?:מר|גב'|גברת|ד\"ר|עו\"ד)\s+)?[\u0590-\u05FF]{2,}(?:\s+[\u0590-\u05FF]{2,}){0,2}(?=\s|$)", r"\1", text)
    text = _strip_over_specific_subject_status_tail(text)
    text = re.sub(r"\s*\d+(?:\.\d+)?\s*$", "", text)
    text = re.sub(r"\s+מס\s*$", "", text)
    text = re.sub(r"\s+מתאריך\b.*$", "", text)
    text = re.sub(r"\s*\([^)]{0,40}(?:מצ\"?ל|שרת|ביטון|צור|בראנץ|עודד\s+לוי)[^)]*\)?\s*$", "", text)
    text = re.sub(r"\s+(?:חברי\s+המועצה\s+)?מאשרים\b.*$", "", text)
    text = re.sub(r"\s+הצביעו\b.*$", "", text)
    text = _repair_unbalanced_parenthesis(text)
    return _compact(text).strip(" *•'\"׳״[]-–:.,")[:220]


def _strip_over_specific_subject_status_tail(text: str) -> str:
    compact = _compact(text)
    if not compact:
        return ""
    patterns = (
        r"\s+(?:המושבת(?:ת|ים|ות)?|מושבת(?:ת|ים|ות)?)\s+(?:מזה|למעלה\s+מ?|יותר\s+מ?|מעל|כבר|במשך)\b.*$",
        r"\s+ש(?:הושבת(?:ה|ו)?|מושבת(?:ת|ים|ות)?|סגור(?:ה|ים|ות)?|אינו\s+פעיל|אינה\s+פעילה|אינם\s+פעילים|אינן\s+פעילות)\b.*$",
        r"\s+ש(?:אינו|אינה|אינם|אינן)\s+(?:פעיל(?:ה|ים|ות)?|תקין(?:ה|ים|ות)?)\b.*$",
    )
    for pattern in patterns:
        match = re.search(pattern, compact)
        if not match:
            continue
        prefix = _compact(compact[: match.start()])
        if _has_reusable_action_subject(prefix):
            return prefix
    return compact


def _has_reusable_action_subject(text: str) -> bool:
    normalized = normalize_for_search(text)
    if len(re.findall(r"[\u0590-\u05FF]{2,}", normalized)) < 3:
        return False
    action_terms = (
        "תיקון",
        "תחזוקה",
        "תחזוקת",
        "טיפול",
        "פתיחה",
        "פתיחת",
        "חידוש",
        "שיקום",
        "החלפה",
        "שדרוג",
        "הסדרה",
        "הסדרת",
        "הפעלת",
        "הקמת",
        "התקנת",
        "אכיפה",
        "מניעת",
    )
    return any(term in normalized for term in action_terms)


def _strip_temporal_subject_parts(text: str) -> str:
    text = re.sub(r"\b\d{1,4}/\d{4}\b", " ", text)
    text = re.sub(r"\b(?:לשנת|בשנת|שנת|שנה|תקציב)\s*\d{4}\b", lambda match: "תקציב" if match.group(0).startswith("תקציב") else " ", text)
    text = re.sub(r"\b\d{4}\s*(?:לשנת|בשנת|שנת)?\b", " ", text)
    text = re.sub(r"\s+(?:לשנת|בשנת|שנת)\s*$", "", text)
    text = re.sub(r"\s*/\s*[\"'׳״]?\s*", " ", text)
    text = re.sub(r"(מכרז\s+פומבי)\s+מס\b\s*[-–]?\s*", r"\1 ", text)
    return _compact(text)


def _best_repeated_subject(text: str) -> str:
    compact = _compact(text)
    if "החלטות" not in compact:
        return compact
    before, after = compact.split("החלטות", 1)
    before = _compact(before).strip(" '\"׳״()[]-–:.,")
    after = _compact(after).strip(" '\"׳״()[]-–:.,")
    if before == "אישור":
        return compact
    if after and len(_hebrew_tokens(after)) >= 3 and not _looks_like_vote_fragment(after):
        return after
    return before or compact


def _repair_unbalanced_parenthesis(text: str) -> str:
    compact = _compact(text).strip()
    if compact.count("(") == compact.count(")"):
        return compact
    if compact.count("(") == compact.count(")") + 1 and compact.rfind("(") > max(3, len(compact) // 3):
        return f"{compact})"
    return compact


def _term_in_text(term: str, normalized_text: str) -> bool:
    normalized_term = normalize_for_search(term)
    if not normalized_term:
        return False
    term_tokens = _hebrew_tokens(normalized_term)
    if len(term_tokens) == 1:
        needle = term_tokens[0]
        return any(_hebrew_token_matches_term(token=token, term=needle) for token in _hebrew_tokens(normalized_text))
    return normalized_term in normalized_text


def _hebrew_token_matches_term(*, token: str, term: str) -> bool:
    stripped = token
    while stripped:
        if stripped == term:
            return True
        if term.endswith("ה") and len(term) >= 4 and stripped == f"{term[:-1]}ת":
            return True
        if len(stripped) <= len(term) or stripped[0] not in "ובכלמהש":
            return False
        stripped = stripped[1:]
    return False


def _compact(value: Any) -> str:
    return " ".join(re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", str(value or "")).split())


def _hebrew_tokens(value: str) -> list[str]:
    return re.findall(r"[\u0590-\u05FF]{2,}", value or "")


def _looks_like_vote_fragment(text: str) -> bool:
    compact = _compact(text)
    return any(term in compact for term in ["הצביעו נגד", "הצביעו בעד", "לא השתתפו בהצבעה", "נמנע", "נמנעו"])


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = normalize_for_search(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out
