from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from municipality.chunking import normalize_for_search


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
        policy_id="procurement_or_tender_action",
        root_topic_id="root_agreements",
        description_he="כאשר פעולת האג'נדה היא מכרז, ביטול מכרז, התקשרות, הרשאה או פטור ממכרז, מסווגים לפי פעולת ההתקשרות גם אם מוזכר תחום שירות כמו חינוך.",
        priority=104,
        required_any=(("ביטול מכרז", "מכרז פומבי", "פטור ממכרז", "התקשרות", "הרשאה"),),
    ),
    TopicPolicy(
        policy_id="resident_messaging_administration",
        root_topic_id="root_administration",
        description_he="מאגרי מסרונים, מסרי וידאו והודעות לתושבים מסווגים למנהל עירוני.",
        priority=104,
        required_any=(("מסרונים", "מסר וידאו", "מסרי וידאו"), ("תושבים", "תושבי העיר")),
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
        description_he="לוחות פרסום אלקטרוניים מסווגים לתשתיות וסביבה.",
        priority=104,
        required_any=(("לוחות פרסום אלקטרוניים",),),
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
        required_any=(("מוסדות חינוך", "בית ספר", "בתי ספר", "בתי הספר", "גנים", "חרמות בבתי הספר", "תלמידים"),),
        negative_terms=("ביטול מכרז", "מכרז פומבי", "התקשרות", "פטור ממכרז"),
    ),
    TopicPolicy(
        policy_id="parent_payments_education",
        root_topic_id="root_education",
        description_he="תשלומי הורים, צהרונים וגני ילדים מסווגים לחינוך.",
        priority=97,
        required_any=(("תשלומי הורים", "צהרונים", "גני ילדים"),),
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
        required_any=(("סעיף תקציבי", "תב\"ר", "תבר", "עתודה", "תשלומים בלתי רגילים", "תשלומי רשות", "תקציב"),),
    ),
    TopicPolicy(
        policy_id="road_safety_traffic_calming",
        root_topic_id="root_transport_safety",
        description_he="תאונות דרכים, פסי האטה, באמפרים, מהירות ובטיחות בדרכים מסווגים לתחבורה ובטיחות.",
        priority=96,
        required_any=(("תאונות דרכים", "אפס תאונות", "תאונות", "בטיחות בדרכים"), ("באמפר", "באמפרים", "פס האטה", "פסי האטה", "מהירות", "תחבורתית", "כביש")),
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
        policy_id="information_security_subject",
        root_topic_id="root_security_enforcement",
        description_he="אבטחת מידע, הגנת פרטיות וסייבר מסווגים לביטחון ואכיפה.",
        priority=112,
        required_any=(("אבטחת מידע", "סייבר", "הגנת פרטיות", "הגנת הפרטיות"),),
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
        required_any=(("מיגור תופעת האלימות",),),
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
        required_any=(("עובדים מושאלים", "עובדים זמניים", "כוח אדם", "כח אדם", "עבודה נוספת לעובדי", "שכרו", "תחילת עבודתו", "מועד תחילת עבודתו"),),
    ),
    TopicPolicy(
        policy_id="council_governance_attendance",
        root_topic_id="root_administration",
        description_he="נוכחות, איחורים והיעדרויות של חברי מועצה בישיבות מליאה/ועדות הם נושא מנהל עירוני.",
        priority=93,
        required_any=(("חברי מועצה", "חברת מועצה", "מועצת העיר"), ("איחורים", "היעדרויות", "נוכחות", "ישיבות מליאה", "ועדות")),
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
        policy_id="public_sculpture_culture",
        root_topic_id="root_culture_sport",
        description_he="הקמת פסלים ציבוריים מסווגת לתרבות וספורט.",
        priority=92,
        required_any=(("פסל", "פסל ציבורי", "הקמת פסל"),),
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
    text = _compact(value)
    if not text:
        return ""
    text = text.strip(" *•")
    text = re.sub(r"\bבנו\s+[\"'׳״]?שא[\"'׳״]?\b", "בנושא", text)
    text = re.sub(r"בבת\(\s*,?\s*\)י\s+הספר", "בבתי הספר", text)
    text = re.sub(r"בתי\s+ספרים\b", "בתי ספר", text)
    text = re.sub(r"^\)?\(?\s*נספח\s+[\u0590-\u05FF]\s*[.)]?\s*", "", text)
    text = re.sub(r"^(?:סעיף\s*)?\d+(?:\.\d+)?\s*[.)]?\s*:?\s*", "", text)
    text = re.sub(r"^(?:\)?\([^)]{0,30}\)\s*)?(?:מתאריך\s+)?\d+(?:\.\d+)?\s*['׳״.\-–,\s]*", "", text)
    text = re.sub(r"^מתאריך\s+\d+(?:\.\d+)?\s*['׳״.\-–,\s]*", "", text)
    text = re.sub(r"^(?:שאילת[אה]|שאילתה)\s+של\b.{0,120}?\s*בנושא\s+", "", text)
    text = re.sub(r"^(?:שאילת[אה]|שאילתה)\s+בנושא\s+", "", text)
    text = re.sub(r"^אישור\s+מועצת\s+העירייה\s+וכן\s+לחתום\s+על\s+חוזה\s+ל", "", text)
    text = re.sub(r"^בקשה\s+לאישור\s+", "", text)
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
    text = re.sub(r"\s*\d+(?:\.\d+)?\s*$", "", text)
    text = re.sub(r"\s+מס\s*$", "", text)
    text = re.sub(r"\s+מתאריך\b.*$", "", text)
    text = re.sub(r"\s*\([^)]{0,40}(?:מצ\"?ל|שרת|ביטון|צור|בראנץ|עודד\s+לוי)[^)]*\)?\s*$", "", text)
    text = re.sub(r"\s+(?:חברי\s+המועצה\s+)?מאשרים\b.*$", "", text)
    text = re.sub(r"\s+הצביעו\b.*$", "", text)
    text = _repair_unbalanced_parenthesis(text)
    return _compact(text).strip(" *•'\"׳״[]-–:.,")[:220]


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
    return bool(normalized_term and normalized_term in normalized_text)


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
