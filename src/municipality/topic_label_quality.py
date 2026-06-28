from __future__ import annotations

import re

from municipality.chunking import normalize_for_search


HEBREW_TOPIC_TOKEN_RE = re.compile(r"[\u0590-\u05FF]{2,}")
SECTION_PREFIX_RE = re.compile(r"^(?:סעיף|סעיפים|ס['׳]?|סע[׳']?)\s*[\d/]+(?:\.\d+)?\s*[.)\]:\-–]*\s*")


def compact_topic_label(value: str | None) -> str:
    text = re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", str(value or ""))
    return " ".join(text.split()).strip()


def strip_topic_carrier_prefixes(value: str | None) -> str:
    """Remove reusable agenda/table carriers while preserving evidence text elsewhere."""

    text = compact_topic_label(value)
    if not text:
        return ""
    text = SECTION_PREFIX_RE.sub("", text).strip()
    text = re.sub(r"^\(?\s*\d+(?:\.\d+)?\s*\)?\s*[.)\]:\-–]*\s*", "", text).strip()
    text = re.sub(r"^(?:הנדון|נידון|נדון)\s*[:.\-–]?\s*", "", text).strip()
    text = re.sub(r"^בנושא\s*[:.\-–]?\s*", "", text).strip()
    text = re.sub(r"^נושא\s*[:.\-–]\s*", "", text).strip()
    return compact_topic_label(text)


def canonicalize_topic_label(value: str | None, *, root_label_he: str | None = None, evidence_text: str | None = None) -> tuple[str | None, str | None]:
    """Return a short reusable topic label, while leaving evidence text untouched.

    The extractor often sees full agenda clauses, legal text, OCR debris, or
    speaker prose. This function keeps the semantic subject and strips the
    carrier/context so the final topic can be reused across documents.
    """

    raw = compact_topic_label(value)
    if not raw:
        return None, "empty_label"

    evidence = compact_topic_label(evidence_text)
    combined_norm = normalize_for_search(" ".join(part for part in [raw, evidence] if part))
    root_norm = normalize_for_search(root_label_he or "")
    label = _basic_label_cleanup(raw)
    label = _repair_ocr_truncated_tail(label=label, evidence=evidence)
    label_norm = normalize_for_search(label)

    mapped = _semantic_canonical_label(label=label, label_norm=label_norm, combined_norm=combined_norm, root_norm=root_norm)
    if mapped:
        return mapped, "semantic_canonicalized" if normalize_for_search(mapped) != label_norm else None

    label = _trim_contextual_tail(label)
    label = _basic_label_cleanup(label)
    label = _repair_ocr_truncated_tail(label=label, evidence=evidence)
    label_norm = normalize_for_search(label)
    mapped = _semantic_canonical_label(label=label, label_norm=label_norm, combined_norm=normalize_for_search(" ".join([label, evidence])), root_norm=root_norm)
    if mapped:
        return mapped, "semantic_canonicalized" if normalize_for_search(mapped) != label_norm else None

    if _procedural_or_dialogue_only(label, root_norm=root_norm):
        return None, "procedural_or_dialogue_label"
    if _label_still_noisy(label):
        shortened = _shorten_generic_label(label)
        if shortened and not _label_still_noisy(shortened) and not _procedural_or_dialogue_only(shortened, root_norm=root_norm):
            return shortened, "generic_shortened"
        return None, "unresolved_noisy_label"
    return label or None, None


def _basic_label_cleanup(value: str) -> str:
    label = strip_topic_carrier_prefixes(value)
    label = re.sub(r"\s*([–-])\s*", r" \1 ", label)
    label = re.sub(r"\s*\.\s*(?=[\u0590-\u05FF])", " ", label)
    label = re.sub(r"\bבנו\s+['\"׳״]?שא['\"׳״]?\b", "בנושא", label)
    label = re.sub(r"\bו\s+עדת\b", "ועדת", label)
    label = re.sub(r"\bה\s+חלטה\b", "החלטה", label)
    label = re.sub(r"\bעירו\s+נית\b", "עירונית", label)
    label = strip_topic_carrier_prefixes(label)
    label = re.sub(r"^(?:שאילת[אה]|שאילתה)\s+של\b.{0,120}?\s+בנושא\s+", "", label).strip()
    label = re.sub(r"^(?:שאילת[אה]|שאילתה)\s+בנושא\s+", "", label).strip()
    label = re.sub(r"^(?:הצעה\s+לסדר(?:\s+יום)?|נושא\s+לדיון)\s*[-–:]?\s*", "", label).strip()
    label = re.sub(r"^(?:פרוטוקול\s+מישיבת|פרוטוקול\s+ישיבת|פרוטוקול)\s+", "", label).strip()
    label = re.sub(r"^(?:אישור|בקשה\s+לאישור)\s+", "", label).strip()
    label = re.sub(r"\(?\s*עמ(?:וד)?\s*['\"׳״]?\s*\d+.*$", "", label).strip()
    label = re.sub(r"\b\d{1,4}/\d{2,4}\b", " ", label)
    label = re.sub(r"\b(?:לשנת|בשנת|שנת|תקציב)\s*\d{4}\b", lambda match: "תקציב" if match.group(0).startswith("תקציב") else " ", label)
    label = re.sub(r"\b\d{4}\s*(?:לשנת|בשנת|שנת)?\b", " ", label)
    label = re.sub(r"\b\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\b", " ", label)
    label = re.sub(r"\s+(?:מס|מספר)\s*[/\d].*$", "", label).strip()
    label = re.sub(r"\b(?:גוש|חלקה|ח\"?ח|מגרש)\b.*$", "", label).strip()
    label = re.sub(r"\b(?:בסך\s*)?\d+(?:[.,]\d+)?\s*(?:%|מיליון|מליון|שח|ש\"ח|₪)\b", " ", label)
    label = re.sub(r"\s+[-–]\s*(?:עו\"?ד|מר|גב'?|גברת|ד\"?ר|הרב)\b.*$", "", label).strip()
    label = re.sub(r"\([^)]{0,80}(?:עו\"?ד|מר|גב'?|גברת|ד\"?ר|הרב|ביטון|צור|רוזנטל|עודד)[^)]*\)\s*$", "", label).strip()
    label = re.sub(r"\s+(?:מחליטים|מאשרים|הוחלט|הצביעו)\b.*$", "", label).strip()
    label = re.sub(r"\s+מצ[\"'״]?ל\b.*$", "", label).strip()
    label = re.sub(r"\s+הוסר\s+מסדר\s+היום\b.*$", "", label).strip()
    label = re.sub(r"\s+ירד\s+מסדר\s+היום\b.*$", "", label).strip()
    label = re.sub(r"\s+גב[’']?דינה\b.*$", "", label).strip()
    label = re.sub(r"\s+ד[\"”]?\s*ר\b.*$", "", label).strip()
    label = re.sub(r"\bל\s+(?=[\u0590-\u05FF])", "ל", label)
    label = re.sub(r"\bהישגי\s+ות\b", "הישגיות", label)
    label = re.sub(r"\s*/\s*['\"׳״]?\s*", " ", label)
    label = re.sub(r"\s*,\s*", ", ", label)
    if sum(label.count(mark) for mark in ('"', "״", "“", "”")) % 2 == 1:
        label = re.sub(r"(?<=[\s,;:])['\"׳״“”](?=ו?[\u0590-\u05FF])", "", label)
    label = re.sub(r"\s+", " ", label).strip(" *•'\"׳״[]{}() -–:.,")
    if label.count("(") != label.count(")"):
        label = label.replace("(", " ").replace(")", " ")
    label = re.sub(r"^ל(?=(?:נוהל|הסכם|הקצאה|הקצאת|אישור|ביטול|מינוי|עדכון)\b)", "", label).strip()
    label = re.sub(r"\s+", " ", label).strip(" *•'\"׳״[]{}() -–:.,")
    return label[:220]


def _commercial_center_renovation_label(text: str) -> str | None:
    if "שיפוץ מרכז מסחרי" not in text:
        return None
    return "שיפוץ מרכז מסחרי"


def _semantic_essence_from_detail_tail(label: str) -> str | None:
    """Keep the reusable activity/topic and leave facets such as cost as evidence."""

    compact = compact_topic_label(label)
    parts = [compact_topic_label(part) for part in re.split(r"[,;]", compact, maxsplit=1) if compact_topic_label(part)]
    if len(parts) < 2:
        return None
    first, tail = parts[0], parts[1]
    first_norm = normalize_for_search(first)
    tail_norm = normalize_for_search(tail)
    if not first_norm or not tail_norm:
        return None
    detail_terms = (
        "עלות",
        "עלויות",
        "תעריף",
        "תעריפים",
        "אגרה",
        "אגרות",
        "הנחה",
        "הנחות",
        "זכאים",
        "מנוי",
        "מנויים",
        "תשלום",
        "תשלומים",
        "תקציב",
    )
    if first_norm.startswith("פעילות ") and any(term in tail_norm for term in detail_terms):
        return first
    return None


def _repair_ocr_truncated_tail(*, label: str, evidence: str) -> str:
    compact = compact_topic_label(label)
    compact_evidence = compact_topic_label(evidence)
    if not compact or not compact_evidence or len(topic_quality_tokens(compact)) < 3:
        return compact
    label_pattern = re.escape(compact).replace(r"\ ", r"\s+")
    match = re.search(rf"{label_pattern}\s*\d{{1,3}}\s*['\"׳״“”]?\s*([\u0590-\u05FF]{{2,}})\b", compact_evidence)
    if not match:
        return compact
    tail = match.group(1)
    if tail in {"של", "את", "עם", "על", "אל", "או"}:
        return compact
    return compact_topic_label(f"{compact} {tail}")


def _semantic_canonical_label(*, label: str, label_norm: str, combined_norm: str, root_norm: str) -> str | None:
    text = label_norm
    full_text = combined_norm or label_norm
    detail_essence = _semantic_essence_from_detail_tail(label)
    if detail_essence:
        return detail_essence
    if "זכויות אויר" in text:
        return "זכויות אוויר"
    if "זכויות אוויר" in text:
        return "זכויות אוויר"
    if re.search(r"ה\s*צפות|הצפות", text) and any(term in text for term in ["תשתיות", "נזקי", "פיצוי", "טיפול"]):
        return "הצפות וטיפול בתשתיות"
    if any(term in full_text for term in ["מכירת דירות", "שיווק דירות"]) and any(term in full_text for term in ["פרסום", "אסור", "מגזר"]):
        return "פרסום מכירת דירות"
    if any(term in full_text for term in ["מקדמה", "מקדמות"]) and any(term in full_text for term in ["עמותה", "עמותות", "מוסד"]) and "נוהל תמיכות" not in full_text:
        return "מקדמות תמיכה לעמותות"
    if "הסכם הגג" in full_text:
        return "הסכם הגג"
    if "ארנונה" in text and ("תת סיווג" in text or ("תת" in text and "סיווג" in text)) and any(term in text for term in ["הוספת", "להוסיף"]):
        if "צו הארנונה" in text:
            return "הוספת תת סיווג בצו הארנונה"
        return "הוספת תת סיווג ארנונה"
    if "ארנונה" in text:
        tax_label = _term_centered_topic_label(label, anchor="ארנונה", before=3, after=4)
        if tax_label:
            return tax_label
    if "ועדת תרומות" in full_text or "ועדת התרומות" in full_text:
        return "ועדת תרומות"
    if "ועדת קליטה" in full_text:
        return "ועדת קליטה"
    if any(term in text for term in ["נגע הסמים", "סמים מסוכנים", "מאבק בסמים"]):
        return "ועדה למאבק בנגע הסמים המסוכנים"
    if "גללי כלבים" in text:
        return "טיפול בגללי כלבים"
    if "חתולי רחוב" in text and any(term in text for term in ["גורים יונקים", "גורי"]):
        return "טיפול בגורי חתולי רחוב"
    if "מסרונים" in text and any(term in text for term in ["תושבים", "תושבי העיר"]):
        return "מאגר מסרונים לתושבים" if "מאגר" in text else "מסרונים לתושבים"
    if any(term in text for term in ["מסר וידאו", "מסרי וידאו"]) and any(term in text for term in ["תושבים", "תושבי העיר"]):
        return "מסרי וידאו לתושבים"
    if "חניון מוניציפלי חכם" in text:
        return "הקמת חניון מוניציפלי חכם"
    if "מבנים מסוכנים" in text and any(term in text for term in ["בדיקת", "בדיקה"]):
        return "בדיקת מבנים מסוכנים"
    if any(term in text for term in ["אלימות", "פשע", "פשיעה"]) and "רחובות העיר" in text:
        return "אלימות ופשע ברחובות העיר"
    commercial_center_label = _commercial_center_renovation_label(text)
    if commercial_center_label:
        return commercial_center_label
    sport_terms = ["ספורט", "כדורסל", "כדורגל", "כדוריד", "אליפות", "גביע", "ליגה", "קבוצת ספורט"]
    if "מענקי" in full_text and any(term in full_text for term in ["הישגיות", "הישגי"]) and "ספורט" in full_text:
        return "מענקי הישגיות ספורט"
    if any(term in full_text for term in ["מענק", "מענקי", "מענקים", "תמיכה", "תמיכות"]) and any(term in full_text for term in sport_terms):
        return "מענקי ספורט"
    if any(term in text for term in ["תבחין", "תבחינים", "קריטריון", "קריטריונים"]) and "תמיכות" in root_norm:
        return "תבחינים לתמיכות"
    if any(term in text for term in ["מענק עליה לשלב", "מענק עלייה לשלב"]):
        return "מענק עליה לשלב ב" if "שלב ב" in text else "מענק עליה לשלב"
    if "קבוצת כדורסל" in text and "מענק" in text:
        return "מענק לקבוצת כדורסל"
    if any(term in full_text for term in ["אלרגיות מסכנות חיים", "מזרקי אפיפן", "אפיפן"]):
        return "אלרגיות ואפיפן במרחב הציבורי" if "מרחב הציבורי" in full_text else "אלרגיות ואפיפן"
    if "לוחות פרסום אלקטרוניים" in text:
        return "לוחות פרסום אלקטרוניים"
    if "מבנה יביל" in text:
        return "הנחת מבנה יביל" if "הנחת" in text else "מבנה יביל"
    if any(term in text for term in ["מבני ציבור", "מבנה ציבור"]) and any(term in text for term in ["מתנסים", "מתנ סים", "מתנ\"סים"]):
        return "בניית מבני ציבור ומתנ\"סים"
    if "זרימת" in text and "ביוב" in text and "חוף" in text:
        return "זרימת מי ביוב בחוף"
    if any(term in text for term in ["תחנת שאיבת ביוב", "מאגר חירום לביוב"]):
        return "תחנת שאיבת ביוב ומאגר חירום"
    if any(term in text for term in ["ניהול המים", "תאגיד המים", "מים וביוב"]):
        return "ניהול מים וביוב"
    if any(term in text for term in ["זיהום אויר", "זיהום אוויר"]) and any(term in text for term in ["ריחות", "ריח"]):
        return "זיהום אוויר וריחות"
    if "מזרקה" in text:
        return "השבתת מזרקה בפארק" if any(term in text for term in ["השבתת", "מושבת", "מושבתת"]) else "מזרקה בפארק"
    if "אגם" in text and "מרינה" in text:
        return "טיפול באגם המרינה"
    if any(term in text for term in ["חוף", "חופי", "חופים"]) and "פעילות" in text:
        return "פעילות בחופים"
    if "שיפוץ" in full_text and any(term in full_text for term in ["חוף", "חופי", "חופים"]):
        return "שיפוץ חוף"
    if "זיהום" in text and "שפכים" in text and any(term in text for term in ["ים", "חוף", "חופי"]):
        return "זיהום ים משפכים"
    if "רעידת אדמה" in text:
        return "מוכנות לרעידת אדמה ומערכת התראה" if "מערכת התראה" in full_text else "מוכנות לרעידת אדמה"
    if "שדרוג תבחינים" in full_text and "שיפוץ חזיתות" in full_text:
        return "תבחינים לשיפוץ חזיתות"
    if "ציוד מגן אישי" in full_text and any(term in full_text for term in ["עובד", "עובדים", "עובדי"]):
        return "ציוד מגן אישי לעובדים"
    if any(term in text for term in ["מקור לכיסוי", "כיסוי גרעון", "כיסוי גירעון", "גרעון", "גירעון"]) and any(term in text for term in ["תחבורה ציבורית", "פרויקט התחבורה"]):
        return "מימון פרויקט תחבורה ציבורית"
    if "רב ראשי" in text and any(term in text for term in ["בחירת", "לבחירת", "גוף בוחר", "הרכבת"]):
        return "בחירת רב ראשי"
    if any(term in text for term in ["מינויים ושינויים", "שינויים בהרכב", "שינוי בהרכב"]) and any(term in text for term in ["ועדת מכרזים", "וועדת מכרזים"]):
        return "מינויים ושינויים בוועדת מכרזים"
    if any(term in full_text for term in ["תאגידים עירוניים", "תאגיד עירוני", "חברות עירוניות", "חברה עירונית"]) and any(term in full_text for term in ["מבקר המדינה", "דו\"ח מבקר", "דוח מבקר", "ביקורת"]):
        return "תיקון התנהלות בתאגידים עירוניים בעקבות דו\"ח מבקר המדינה"
    if any(term in full_text for term in ["חוק עזר", "חוק העזר"]) and "סלילת רחובות" in full_text:
        return "תיקון חוק עזר סלילת רחובות"
    if any(term in full_text for term in ["הסתייגות", "קיצוץ", "מתקציב", "בתקציב"]) and any(term in full_text for term in ["תכנית שכונה כעיר", "תוכנית שכונה כעיר"]):
        return "קיצוץ תקציב תכנית שכונה כעיר"
    if "פטור לנכס" in text and "נזק מלחמה" in text:
        return "פטור לנכס בשל נזק מלחמה"
    if "תאונות דרכים" in text or ("תאונות" in text and any(term in text for term in ["נפגעים", "הרוגים"])):
        return "תאונות דרכים"
    if any(term in text for term in ["כלים חשמליים ממונעים", "אופניים חשמליים", "קורקינטים"]):
        return "אכיפה והסדרת כלים חשמליים ממונעים" if any(term in text for term in ["אכיפה", "הסדרה", "הסדרת"]) else "כלים חשמליים ממונעים"
    if "לוח מודעות" in text:
        return "לוח מודעות אלקטרוני"
    if "החזר תשלומי הורים" in text:
        return "החזר תשלומי הורים"
    if "ביטחון תזונתי" in text or "בטחון תזונתי" in text:
        return "סיוע לביטחון תזונתי"
    if "חממת" in text and "תקציב" in text:
        return "מימון חממה מהתקציב העירוני"
    if "הטבות" in text and "מדד" in text and "חברתי" in text:
        return "הטבות בעקבות ירידה במדד חברתי-כלכלי"
    if "מועצה דתית" in text:
        return "מינוי מועצה דתית" if "מינוי" in text else "מועצה דתית"
    if "תצהיר" in text and any(term in text for term in ["חברי המועצה", "חבר מועצה"]):
        return "תצהיר ניגוד עניינים לחברי מועצה"
    if "מיגור תופעת האלימות" in text:
        return "ועדה למיגור תופעת האלימות" if "ועדה" in full_text else "מיגור תופעת האלימות"
    if "מכרז פומבי" in text and "השכרת מבנה" in text:
        return "התקשרות להשכרת מבנה"
    if "אישור התקשרות" in text and "השכרת מבנה" in text:
        return "התקשרות להשכרת מבנה"
    if any(term in text for term in ["ניהול משא ומתן", "ניהול מו מ", "ניהול מו\"מ"]) and any(term in text for term in ["התקשרות", "חוזה"]) and any(term in text for term in ["ללא מכרז", "פטור ממכרז"]):
        return "ניהול משא ומתן להתקשרות ללא מכרז"
    if any(term in full_text for term in ["וטרינרית", "וטרינרי", "וטרינר"]):
        if "תקציב" in full_text and any(term in full_text for term in ["שעות פעילות", "פעילות"]):
            return "שעות פעילות ותקציב מחלקה וטרינרית"
        if any(term in full_text for term in ["מרפאות חוץ", "מרפאה", "מרפאות"]) and any(term in full_text for term in ["הסכם", "הסכמים", "התקשרות"]):
            return "הסכמים עם מרפאות וטרינריות"
    if "הסכם רשות" in text and any(term in text for term in ["עמותה", "עמותת", "עמותות"]):
        if any(term in text for term in ["גני ילדים", "גנ י", "גנ\"י", "גני", "בית ספר", "כיתות"]):
            return "הסכם רשות למוסדות חינוך"
        return "הסכם רשות לעמותה"
    if "הסכם" in text and any(term in text for term in ["עמותה", "עמותת", "עמותות", "מלכ ר", "חל צ"]):
        if any(term in text for term in ["גני ילדים", "גנ י", "גנ\"י", "גני", "בית ספר", "כיתות", "חינוך"]):
            return "הסכם רשות למוסדות חינוך"
        return "הסכם עם עמותה"
    if "הסכם פיתוח" in text:
        return "הסכם פיתוח"
    if "הסכם שכירות" in text and "מתקן שידור" in text:
        return "הסכם שכירות למתקן שידור"
    if "הסכם חכירה" in text and "דיור בר השגה" in text:
        return "הסכם חכירה לדיור בר השגה"
    if "הסכם בין" in text and "חברה עירונית" in text:
        return "הסכם עם חברה עירונית"
    if "הסכם" in text and "החברה העירונית לתיירות" in text:
        return "הסכם עם חברה עירונית לתיירות"
    if any(term in text for term in ["הקצאה", "הקצאת", "הקצאות", "הסדרת שימוש", "רשות שימוש"]):
        if "פעילות רב תכליתית" in text:
            return "הקצאה לפעילות רב תכליתית"
        if "בית כנסת" in text:
            if "מרכז רוחני" in text:
                if "ביטול" in text and "ועדת הקצאות" in text:
                    return "ביטול החלטת ועדת הקצאות עבור בית כנסת ומרכז רוחני"
                return "הקצאה לבית כנסת ומרכז רוחני"
            return "הקצאת מקרקעין לבית כנסת"
        if any(term in text for term in ["עמותה", "עמותת", "עמותות"]):
            return "הקצאת מקרקעין לעמותה"
        if any(term in text for term in ["מקרקעין", "מבנה", "מגרש"]):
            return "הקצאת מקרקעין"
    if "ייעוד" in text and "מקרקעין" in text and any(term in text for term in ["כיתת גן", "גן ילדים"]):
        return "ייעוד מקרקעין לכיתת גן"
    if "מקרקעין" in text and any(term in text for term in ["הצטרפות", "עשייה", "פעולה"]):
        return "פעולה במקרקעין"
    if any(term in text for term in ["וועדה המחוזית לתכנון", "הוועדה המחוזית לתכנון", "ועדה המחוזית לתכנון", "ועדה מחוזית לתכנון", "תכנון ולבניה", "תכנון ובנייה"]):
        if any(term in text for term in ["נציג", "כנציג", "לשמש כנציג"]):
            return "נציג בוועדה מחוזית לתכנון ובנייה"
    if any(term in text for term in ["רשמת הנכסים", "רשם הנכסים"]):
        return "הסמכת רשם נכסים" if any(term in text for term in ["הסמכת", "להסמיך"]) else "רשם נכסים"
    if any(term in text for term in ["איחוד וחלוקה", "זכויות בנייה", "זכויות בניה"]) and any(term in text for term in ["תכנית", "תוכנית"]):
        return "תוכנית איחוד וחלוקה וזכויות בנייה"
    if any(term in text for term in ["פינוי בינוי", "פינוי-בינוי"]) and any(term in text for term in ["תכנית", "תוכנית", "פרויקט", "פרוייקט", "מתחם"]):
        match = re.search(r"(?:מתחם|ברחוב)\s+([^,.;:]{2,50})", label)
        suffix = compact_topic_label(match.group(1)) if match else ""
        return f"פינוי בינוי {suffix}".strip()
    if "חניית קרוואנים" in text or "חניית קרוונים" in text:
        return "תוכנית לפתרון חניית קרוואנים"
    if "מתחם פעילות" in text and any(term in text for term in ["הקמה", "הפעלת", "ימי הולדת"]):
        return "הקמת מתחם פעילות"
    if text.startswith("מינוי") and "לתפקיד" in text:
        match = re.search(r"לתפקיד\s+([^,.;:()]{3,80})", label)
        if match:
            title = compact_topic_label(match.group(1))
            words = topic_quality_tokens(title)[:4]
            if words:
                return "מינוי " + " ".join(words)
    if "מינוי" in text and any(term in text for term in ["מנכ", "מ נכ", "נכ\"ל"]) and any(term in text for term in ["חב", "חברה", "תאגיד", "יובלים"]):
        return "מינוי מנכ\"ל חברה עירונית"
    if "שימוע" in text and "מהנדס העיר" in text:
        return "שימוע למהנדס העיר"
    if "חידוש כהונה" in text and "דירקטוריון" in text:
        return "חידוש כהונה בדירקטוריון"
    if "דירקטוריון" in text and any(term in text for term in ["מינוי", "מינויים"]):
        return "מינוי בדירקטוריון"
    if "מחיקת חובות" in text:
        return "מחיקת חובות"
    if "טיפולים פרא רפואיים" in full_text or "טיפולים פרה רפואיים" in full_text:
        return "טיפולים פרא רפואיים"
    if any(term in text for term in ["ועדות אפיון וזכאות", "אפיון וזכאות"]):
        return "ועדות אפיון וזכאות"
    if any(term in text for term in ["השקעה בחינוך", "השקעה נמוכה בחינוך", "ההשקעה הנמוכה בחינוך"]):
        return "השקעה בחינוך"
    if "כיתות" in full_text and any(term in full_text for term in ["אזרחים ותיקים", "אזרחים וותיקים"]):
        return "כיתות אזרחים ותיקים"
    if any(term in text for term in ["מעבר בית הספר", "מעבר בית ספר"]):
        return "מעבר בית ספר"
    if "פרוטוקול חינוך" in full_text or re.search(r"\bחינוך\s+מס\b", text):
        return "ועדת חינוך"
    if "פרס חינוך" in full_text:
        return "פרס חינוך"
    if any(term in text for term in ["אמנות מקומית", "אומנות מקומית", "אמנים מקומיים", "אומנים מקומיים"]):
        return "אמנות מקומית"
    if any(term in text for term in ["בני נוער", "נוער", "צעירים"]) and any(term in text for term in ["קבוצת", "מועדון", "קבוצת ספורט", "קבוצת כדורגל", "עירוני"]) and any(term in text for term in ["חלופה", "סגירת", "סגירה"]):
        return "חלופה לבני נוער בעקבות סגירת קבוצת ספורט"
    if any(term in text for term in ["קשישים עריריים", "בדידות קשישים"]):
        return "טיפול בקשישים עריריים"
    if "פרסים עירוניים" in text:
        return "פרסים עירוניים"
    if "פרס התיאטרון" in text:
        return "פרסים עירוניים"
    if "סקירה שנתית" in text and "מפקד תחנת" in text:
        station_match = re.search(r"סקירה\s+שנתית\s+של\s+מפקד\s+תחנת\s+(.+)", label)
        station_tail = station_match.group(1).strip(" .,:;()[]{}\"'׳״-–0123456789") if station_match else ""
        station = re.split(r"\s+(?:סנ[\"׳״']?צ|סנצ|רפ[\"׳״']?ק|רפ\s+ק|ניצב|פקד|רב\s+פקד)\b|\d+", station_tail, maxsplit=1)[0].strip(" .,:;()[]{}\"'׳״-–")
        return f"סקירה שנתית של מפקד תחנת {station}" if station else "סקירה שנתית של מפקד תחנת משטרה"
    if "ועדת רווחה" in text:
        return "ועדת רווחה"
    if "ועדת מלגות" in text:
        return "ועדת מלגות השכלה גבוהה" if "השכלה גבוהה" in text else "ועדת מלגות"
    if "אגרת שילוט" in text and any(term in text for term in ["פטור", "פטורים"]):
        return "פטור מאגרת שילוט לבעלי עסקים" if "עסקים" in text else "פטור מאגרת שילוט"
    if "עדכון שכר" in text:
        if "עוזר בכיר" in text:
            return "עדכון שכר עוזר בכיר"
        if "מנכ" in text:
            return "עדכון שכר בכירים"
        return "עדכון שכר"
    if any(term in text for term in ["טבלת ניקוד ענפי הספורט", "ניקוד ענפי הספורט", "עדכון ניקוד"]):
        return "ניקוד תמיכות ספורט"
    if "חילופי גברי" in text and any(term in text for term in ["ועדות", "דירקטוריונים"]):
        return "חילופי גברי בוועדות ודירקטוריונים"
    if "אבטחת מידע" in text and any(term in text for term in ["הגנת הפרטיות", "הגנת פרטיות"]):
        return "אבטחת מידע והגנת פרטיות"
    if "מדעניות העתיד" in text:
        return "תוכנית מדעניות העתיד"
    if "מיגון העיר" in text:
        return "סיוע במיגון העיר" if "סיוע" in full_text else "מיגון העיר"
    if "מיגון" in text:
        return "מיגון"
    if "פטור ממכרז" in text and "ביטוח אחריות נושאי משרה" in text:
        return "התקשרות לביטוח אחריות נושאי משרה"
    if "הקמת פסל" in text:
        if "ציבורי" in text or any(term in text for term in ["ע ש", "ע\"ש", "על שם"]):
            return "הקמת פסל ציבורי"
        return "הקמת פסל"
    if "תרומ" in text and "הקמת פסל" in full_text:
        return "הקמת פסל ציבורי"
    if "פאנלים סולאריים" in text:
        return "התקנת פאנלים סולאריים"
    if "פאנלים סולריים" in text:
        return "התקנת פאנלים סולאריים"
    if "מצב המחזור" in text or ("מחזור" in text and "הסברה" in text):
        return "מחזור והסברה סביבתית"
    if "יקירי העיר" in text:
        return "בחירת יקירי העיר"
    if "תיקון טעות" in text and "פרוטוקול" in text:
        return None
    if "נסיעות בתפקיד" in text or (("יציאה" in text or "יציאת" in text) and "לחו" in text):
        return "נסיעה בתפקיד לחו\"ל"
    if "נסיעה" in text and "משלחת" in text:
        return "נסיעת משלחת עירונית"
    if "העתקת" in text and "אלתא" in text:
        return "העתקת פעילות מפעל אלתא"
    if "דרי רחוב" in text and any(term in text for term in ["טיפול", "טיפול העירייה"]):
        return "טיפול בדרי רחוב"
    if any(term in text for term in ["נוהל תמיכות", "לנוהל תמיכות"]) and any(term in text for term in ["עמותות", "עמותה", "עמותת"]):
        return "נוהל תמיכות לעמותות"
    if "הרשאות עיריית" in text or "הרשאות עירייה" in text:
        return "הרשאות עירייה"
    if any(term in text for term in ["ועדת הנחות במיסים", "ועדת הנחות במסים"]):
        return "ועדת הנחות במיסים"
    return None


def _trim_contextual_tail(label: str) -> str:
    text = compact_topic_label(label)
    text = _prefer_semantic_action_segment(text)
    text = _strip_over_specific_status_tail(text)
    split_patterns = [
        r"\s+בקשת(?:ו|ה|ם|ן)?\s+של\b",
        r"\s+לבקשת(?:ו|ה|ם|ן)?\s+של\b",
        r"\s+מאת\b",
        r"\s+על\s+ידי\b",
        r"\s+כמפורט\b",
        r"\s+לאור\b",
        r"\s+בהתאם\b",
        r"\s+כאשר\b",
        r"\s+שנערך\b",
        r"\s+שנערכה\b",
        r"\s+אשר\b",
    ]
    for pattern in split_patterns:
        text = re.split(pattern, text, maxsplit=1)[0].strip()
    text = re.split(r"\s+[–-]\s+", text, maxsplit=1)[0].strip()
    if len(topic_quality_tokens(text)) > 8:
        sentence_part = re.split(r"[.;:]", text, maxsplit=1)[0].strip()
        if sentence_part:
            text = sentence_part
    return text


def _term_centered_topic_label(label: str, *, anchor: str, before: int, after: int) -> str | None:
    """Build a reusable label around an anchored municipal term without enumerating neighboring words."""

    words = re.findall(r"[\w\u0590-\u05FF'\"׳״-]+", compact_topic_label(label))
    if not words:
        return None
    normalized_words = [normalize_for_search(word) for word in words]
    anchor_norm = normalize_for_search(anchor)
    anchor_index = next((index for index, word in enumerate(normalized_words) if anchor_norm in word), -1)
    if anchor_index < 0:
        return None
    start = max(0, anchor_index - max(0, int(before)))
    end = min(len(words), anchor_index + max(1, int(after)) + 1)
    phrase_words = words[start:end]
    if len(phrase_words) >= 3 and normalize_for_search(phrase_words[0]) in {"הצעת", "בקשת", "המלצת"}:
        phrase_words = phrase_words[2:]
    for index, word in enumerate(list(phrase_words[:-1])):
        normalized_word = normalize_for_search(word)
        if index > 0 and normalized_word.startswith("ל") and len(normalized_word) >= 4:
            phrase_words = phrase_words[index:]
            break
    phrase = compact_topic_label(" ".join(phrase_words))
    phrase = re.sub(r"\b(?:לשנת|בשנת|שנת)\s*\d{4}\b", " ", phrase)
    phrase = re.sub(r"\b\d{4}\b", " ", phrase)
    phrase = re.sub(r"\bחדש(?:ה|ים|ות)?\b$", "", phrase).strip()
    phrase = re.sub(r"^ל(?=עדכון\b)", "", phrase).strip()
    phrase = compact_topic_label(phrase).strip(" *•'\"׳״[]{}() -–:.,")
    if len(topic_quality_tokens(phrase)) < 2:
        return None
    return phrase[:80]


def _strip_over_specific_status_tail(label: str) -> str:
    text = compact_topic_label(label)
    if not text:
        return ""
    patterns = (
        r"\s+(?:המושבת(?:ת|ים|ות)?|מושבת(?:ת|ים|ות)?)\s+(?:מזה|למעלה\s+מ?|יותר\s+מ?|מעל|כבר|במשך)\b.*$",
        r"\s+ש(?:הושבת(?:ה|ו)?|מושבת(?:ת|ים|ות)?|סגור(?:ה|ים|ות)?|אינו\s+פעיל|אינה\s+פעילה|אינם\s+פעילים|אינן\s+פעילות)\b.*$",
        r"\s+ש(?:אינו|אינה|אינם|אינן)\s+(?:פעיל(?:ה|ים|ות)?|תקין(?:ה|ים|ות)?)\b.*$",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        prefix = compact_topic_label(text[: match.start()])
        if _has_reusable_action_subject(prefix):
            return prefix
    return text


def _has_reusable_action_subject(label: str) -> bool:
    normalized = normalize_for_search(label)
    if len(topic_quality_tokens(normalized)) < 3:
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


def _prefer_semantic_action_segment(label: str) -> str:
    text = compact_topic_label(label)
    parts = [part.strip(" '\"׳״") for part in re.split(r"\s+[–-]\s+", text, maxsplit=1)]
    action_terms = (
        "הסכם",
        "הסכמים",
        "התקשרות",
        "מכרז",
        "הקצאה",
        "הקצאת",
        "הסדרת",
        "מינוי",
        "עדכון",
        "נוהל",
        "טיפול",
        "מעקב",
        "ביטול",
    )
    if len(parts) == 2:
        left_norm = normalize_for_search(parts[0])
        right_norm = normalize_for_search(parts[1])
        left_has_action = any(term in left_norm for term in action_terms)
        right_has_action = any(term in right_norm for term in action_terms)
        left_looks_like_carrier = any(term in left_norm for term in ["מחלקה", "אגף", "מינהל", "מנהל", "חברה", "עמותה", "עמותת"]) or len(topic_quality_tokens(left_norm)) <= 3
        if right_has_action and left_looks_like_carrier and not left_has_action:
            return parts[1]
    return text


def _shorten_generic_label(label: str) -> str | None:
    tokens = topic_quality_tokens(label)
    if len(tokens) <= 8 and len(label) <= 80:
        return label
    text = _trim_contextual_tail(label)
    tokens = topic_quality_tokens(text)
    if len(tokens) > 8:
        return None
    return text or None


def _label_still_noisy(label: str) -> bool:
    compact = compact_topic_label(label)
    if not compact:
        return True
    tokens = topic_quality_tokens(compact)
    if len(tokens) > 8 or len(compact) > 80:
        return True
    if len(re.findall(r"\d", compact)) > 2:
        return True
    if re.search(r"[/\\\[\]{}]", compact):
        return True
    if re.search(r"\b(?:עמ|עמוד|מספר|מיום|מתאריך)\b", normalize_for_search(compact)):
        return True
    normalized = normalize_for_search(compact)
    if _looks_like_legal_or_table_carrier_label(normalized):
        return True
    if re.search(r"(?:^|\s)(?:מר|גב'?|גברת|ד\"?ר|עו\"?ד|הרב)\b", compact) and len(tokens) > 4:
        return True
    return False


def _procedural_or_dialogue_only(label: str, *, root_norm: str) -> bool:
    normalized = normalize_for_search(label)
    if not normalized:
        return True
    tokens = set(topic_quality_tokens(normalized))
    dialogue_tokens = {"אני", "אנחנו", "אתה", "את", "תודה", "בבקשה", "נכון", "ענתה"}
    dialogue_phrases = ["אפשר לקיים דיון", "יו ר הישיבה", "יור הישיבה"]
    if (tokens & dialogue_tokens or any(term in normalized for term in dialogue_phrases)) and not any(term in normalized for term in ["בנושא", "הסכם", "תקציב", "מינוי", "הקצאה", "תוכנית", "תכנית"]):
        return True
    procedural = ["סדר היום", "סדר יום", "נוכחות", "הצבעה", "נאומים מהמקום", "הישיבה נעולה"]
    if any(term in normalized for term in procedural):
        return True
    if any(term in normalized for term in ["שנוגע", "שנוגעת", "שקשור", "שקשורה"]):
        return True
    if "חברי האופוזיציה שלא נכחו בדיון" in normalized and len(topic_quality_tokens(normalized)) <= 10:
        return True
    if "מחייב אישור מועצת עיר" in normalized or "מחייב אישור מועצה" in normalized:
        return True
    if any(term in normalized for term in ["פרוטוקולים של הועדות", "פרוטוקולים של הוועדות", "כוחם יפה", "כחם יפה"]):
        return True
    if normalized.startswith(("שהתקבלו", "שנתקבלו")) and "פרוטוקול" in normalized:
        return True
    if normalized in {"דברי ראש העיר", "דבר ראש העיר", "דברי ראש העירייה", "דבר ראש העירייה", "עדכוני ראש העיר", "עדכון ראש העיר"}:
        return True
    if normalized in {"הצעה לסדר", "שאילתה", "שאילתא", "אישור", "פרוטוקול", "דיון", "החלטה", "ועדה מקצועית", "הוועדה המקצועית"}:
        return True
    if _looks_like_legal_or_table_carrier_label(normalized):
        return True
    if normalized.startswith("מחדש לפני ועדת"):
        return True
    if "התייחסות הוועדה" in normalized and len(topic_quality_tokens(normalized)) <= 5:
        return True
    if "פרוטוקול" in normalized and "ועדת" in normalized:
        return True
    if "ועדת" in normalized and any(term in normalized for term in ["מישיבת", "מיום", "מס"]):
        return True
    if "תיקון טעות" in normalized and "פרוטוקול" in normalized:
        return True
    return False


def _looks_like_legal_or_table_carrier_label(normalized: str) -> bool:
    if not normalized:
        return True
    if normalized in {"להלן", "בסעיף", "סעיף", "תיקון סעיף", "חוק העזר העיקרי", "החוק העיקרי"}:
        return True
    if re.fullmatch(r"(?:סעיף|סעיפים|ס['׳]?|סע[׳']?)\s*\d+(?:\.\d+)?", normalized):
        return True
    if normalized.startswith(("להלן ", "בסעיף ")) and len(topic_quality_tokens(normalized)) <= 4:
        return True
    table_tokens = set(topic_quality_tokens(normalized))
    if len(table_tokens & {"קוד", "תאור", "תיאור", "החלטה", "תוקף", "סטאטוס", "סטטוס"}) >= 4:
        return True
    if normalized.startswith("שאילתה של") and re.search(r"\bבנושא\b\s*$", normalized):
        return True
    if any(term in normalized for term in ["מי בעד", "מי נגד", "הצעת ההחלטה", "אפשר לקיים דיון בהזדמנות אחרת"]):
        return True
    return False

LOW_QUALITY_TOPIC_EXACT = {
    normalize_for_search("החלטות עירוניות"),
    normalize_for_search("החלטה ענפית"),
    normalize_for_search("החלטה כללית"),
    normalize_for_search("עיקרי ההחלטה"),
    normalize_for_search("החלטה"),
    normalize_for_search("תוכן ההחלטה"),
    normalize_for_search("נושא ההחלטה"),
    normalize_for_search("נושא כללי"),
    normalize_for_search("ללא תיוג סמנטי"),
    normalize_for_search("מכותבים"),
    normalize_for_search("מכותבים תוכן ההחלטה"),
}

LOW_QUALITY_TOPIC_PHRASES = (
    normalize_for_search("עיקרי ההחלטה"),
    normalize_for_search("תוכן ההחלטה"),
    normalize_for_search("נושא ההחלטה"),
    normalize_for_search("מכותבים"),
    normalize_for_search("עדכון ניקוד"),
)

LOW_QUALITY_TOPIC_TOKEN_SETS = (
    {"עיקרי", "החלטה"},
    {"עיקרי", "החלטות"},
    {"תוכן", "החלטה"},
    {"תוכן", "החלטות"},
    {"מכותבים", "החלטה"},
    {"מכותבים", "החלטות"},
    {"עדכון", "ניקוד"},
)

LOW_QUALITY_TOPIC_LEAD_TOKENS = {
    "מכותבים",
    "עיקרי",
    "תוכן",
    "חתימות",
    "נספחים",
}


def topic_quality_tokens(value: str) -> list[str]:
    normalized = normalize_for_search(" ".join(str(value or "").split()))
    if not normalized:
        return []
    return [token for token in HEBREW_TOPIC_TOKEN_RE.findall(normalized) if token]


def is_low_quality_topic_label(value: str | None) -> bool:
    compact = compact_topic_label(value)
    if not compact:
        return True

    normalized = normalize_for_search(compact)
    if not normalized:
        return True
    if normalized in LOW_QUALITY_TOPIC_EXACT:
        return True
    if any(phrase in normalized for phrase in LOW_QUALITY_TOPIC_PHRASES):
        return True

    tokens = topic_quality_tokens(normalized)
    if not tokens:
        return True

    token_set = set(tokens)
    if any(pattern.issubset(token_set) for pattern in LOW_QUALITY_TOPIC_TOKEN_SETS):
        return True
    if tokens[0] in LOW_QUALITY_TOPIC_LEAD_TOKENS and len(tokens) <= 4:
        return True
    if _label_still_noisy(compact):
        canonical, reason = canonicalize_topic_label(compact)
        if not canonical or reason in {"unresolved_noisy_label", "procedural_or_dialogue_label"}:
            return True
    return False


def sanitize_topic_path_label(value: str | None) -> str | None:
    parts = [part.strip() for part in str(value or "").split(">") if part and part.strip()]
    if not parts:
        compact = " ".join(str(value or "").split()).strip()
        return None if is_low_quality_topic_label(compact) else (compact or None)

    cleaned_parts: list[str] = []
    for part in parts:
        compact = " ".join(part.split()).strip()
        if not compact or is_low_quality_topic_label(compact):
            continue
        cleaned_parts.append(compact)

    if not cleaned_parts:
        return None
    return " > ".join(cleaned_parts)
