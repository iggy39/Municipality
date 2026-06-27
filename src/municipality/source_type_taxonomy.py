from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class SourceTypeMetadata:
    code: str
    label_he: str
    plural_label_he: str
    filter_label_he: str
    semantic_notes_en: str
    semantic_description_he: str


SOURCE_TYPE_TAXONOMY: tuple[SourceTypeMetadata, ...] = (
    SourceTypeMetadata(
        code="protocol",
        label_he="פרוטוקול",
        plural_label_he="פרוטוקולים",
        filter_label_he="פרוטוקולים",
        semantic_notes_en="Meeting protocols, council minutes, committee protocols, and formal municipal meeting records.",
        semantic_description_he="פרוטוקולים של ישיבות עירייה, מועצה או ועדות. מקור זה מתאים להבנת דיונים, החלטות, הצבעות, מעקב אחר ביצוע והקשר רשמי של ישיבה.",
    ),
    SourceTypeMetadata(
        code="attachment",
        label_he="נספח",
        plural_label_he="נספחים",
        filter_label_he="נספחים",
        semantic_notes_en="Attachments and supporting files linked to protocols or other municipal source documents.",
        semantic_description_he="נספחים למסמכים עירוניים, כגון מצגות, טבלאות, מסמכי רקע או קבצים תומכים. מקור זה נותן הקשר נוסף ואינו תמיד החלטה רשמית בפני עצמו.",
    ),
    SourceTypeMetadata(
        code="budget",
        label_he="תקציב",
        plural_label_he="תקציבים",
        filter_label_he="תקציבים / מסמכי תקציב",
        semantic_notes_en="Annual budget books, proposed budget, approved budget.",
        semantic_description_he="מסמכי תקציב שנתיים, הצעות תקציב ותקציב מאושר. מקור זה מתאים להבנת הקצאות כספיות, סדרי עדיפויות ותכנון תקציבי של הרשות.",
    ),
    SourceTypeMetadata(
        code="financial_report",
        label_he="דוח כספי",
        plural_label_he="דוחות כספיים",
        filter_label_he="דוחות כספיים",
        semantic_notes_en="Annual or quarterly financial statements.",
        semantic_description_he="דוחות כספיים שנתיים או רבעוניים. מקור זה מתאים לבדיקת ביצוע כספי בפועל, הכנסות, הוצאות, יתרות ומצב פיננסי מדווח.",
    ),
    SourceTypeMetadata(
        code="audit_report",
        label_he="דוח ביקורת",
        plural_label_he="דוחות ביקורת",
        filter_label_he="דוחות ביקורת / דוחות מבקר העירייה",
        semantic_notes_en="Municipal comptroller / internal audit reports.",
        semantic_description_he="דוחות ביקורת של מבקר העירייה או ביקורת פנימית. מקור זה מתאים לזיהוי ליקויים, המלצות, סיכונים, כשלים תפעוליים וממצאי פיקוח.",
    ),
    SourceTypeMetadata(
        code="tender",
        label_he="מכרז",
        plural_label_he="מכרזים",
        filter_label_he="מכרזים",
        semantic_notes_en="Procurement, infrastructure, services, supplier tenders.",
        semantic_description_he="מכרזי רכש, תשתיות, שירותים וספקים. מקור זה מתאים להבנת תהליכי התקשרות, עבודות מתוכננות, תנאי סף, זוכים ולוחות זמנים של מכרזים.",
    ),
    SourceTypeMetadata(
        code="job_tender",
        label_he="מכרז כוח אדם",
        plural_label_he="מכרזי כוח אדם",
        filter_label_he="מכרזי כוח אדם / דרושים",
        semantic_notes_en="Jobs, HR tenders, public recruitment notices.",
        semantic_description_he="מכרזי כוח אדם, מודעות דרושים ופרסומי גיוס ציבוריים. מקור זה מתאים להבנת משרות, תנאי קבלה, מבנה ארגוני ותהליכי איוש.",
    ),
    SourceTypeMetadata(
        code="foi_transparency",
        label_he="חופש מידע ושקיפות",
        plural_label_he="חופש מידע ושקיפות",
        filter_label_he="שקיפות וחופש מידע",
        semantic_notes_en="FOI pages, transparency reports, public-information disclosures.",
        semantic_description_he="עמודי חופש מידע, דוחות שקיפות ופרסומי מידע לציבור. מקור זה מתאים למציאת מידע שהרשות מפרסמת כחובת שקיפות או כמענה לציבור.",
    ),
    SourceTypeMetadata(
        code="bylaw",
        label_he="חוק עזר",
        plural_label_he="חוקי עזר עירוניים",
        filter_label_he="חוקי עזר עירוניים",
        semantic_notes_en="Municipal bylaws / ordinances.",
        semantic_description_he="חוקי עזר עירוניים ותקנות מקומיות. מקור זה מתאים להבנת סמכויות, חובות, איסורים, אכיפה ותשלומים שנקבעו ברמה העירונית.",
    ),
    SourceTypeMetadata(
        code="form_service",
        label_he="טופס או שירות מקוון",
        plural_label_he="טפסים ושירותים מקוונים",
        filter_label_he="טפסים ושירותים מקוונים",
        semantic_notes_en="Forms, applications, resident service requests.",
        semantic_description_he="טפסים, בקשות ושירותים מקוונים לתושבים. מקור זה מתאים להבנת איך מגישים בקשה, אילו פרטים נדרשים ומהו מסלול השירות לתושב.",
    ),
    SourceTypeMetadata(
        code="work_plan",
        label_he="תוכנית עבודה",
        plural_label_he="תוכניות עבודה",
        filter_label_he="תוכניות עבודה",
        semantic_notes_en="Annual work plans, authority work plans, strategic plans if present.",
        semantic_description_he="תוכניות עבודה שנתיות, תוכניות רשות ותוכניות אסטרטגיות אם קיימות. מקור זה מתאים להבנת יעדים, משימות, מדדי ביצוע ותכנון עתידי.",
    ),
    SourceTypeMetadata(
        code="support_allocation",
        label_he="תמיכה או הקצאה",
        plural_label_he="תמיכות והקצאות",
        filter_label_he="תמיכות והקצאות",
        semantic_notes_en="Grants, allocations, nonprofit support, facility/land allocations.",
        semantic_description_he="תמיכות, מענקים והקצאות לעמותות, גופים ציבוריים, מתקנים או קרקע. מקור זה מתאים להבנת חלוקת משאבים, תנאי זכאות והחלטות הקצאה.",
    ),
    SourceTypeMetadata(
        code="arnona_order",
        label_he="צו ארנונה",
        plural_label_he="צווי ארנונה",
        filter_label_he="צווי ארנונה / תעריפי ארנונה",
        semantic_notes_en="Annual tax orders and rates.",
        semantic_description_he="צווי ארנונה שנתיים ותעריפי ארנונה. מקור זה מתאים להבנת סיווגי נכסים, שיעורי חיוב, הנחות, שינויים ותוקף חיובי הארנונה.",
    ),
    SourceTypeMetadata(
        code="data_registry",
        label_he="מאגר מידע",
        plural_label_he="מאגרי מידע",
        filter_label_he="מאגרי מידע / רשימות ומאגרים",
        semantic_notes_en="Information databases, supplier/adviser registries, public registries.",
        semantic_description_he="מאגרי מידע, רשימות ספקים, יועצים, רישומים ציבוריים ומאגרים עירוניים. מקור זה מתאים למציאת רשימות מובנות, רישומים ומידע קטלוגי.",
    ),
    SourceTypeMetadata(
        code="public_notice",
        label_he="הודעה לציבור",
        plural_label_he="הודעות לציבור",
        filter_label_he="הודעות לציבור / פרסומים לציבור",
        semantic_notes_en="Public notices, planning notices, statutory notices.",
        semantic_description_he="הודעות לציבור, הודעות תכנון ופרסומים סטטוטוריים. מקור זה מתאים להבנת פרסום רשמי, הזמנה להתנגדויות, מועדים והודעות מחייבות לציבור.",
    ),
    SourceTypeMetadata(
        code="procedure",
        label_he="נוהל",
        plural_label_he="נהלים",
        filter_label_he="נהלים / אמנות שירות",
        semantic_notes_en="Internal/public procedures, service charters, policy procedures.",
        semantic_description_he="נהלים פנימיים או ציבוריים, אמנות שירות ונהלי מדיניות. מקור זה מתאים להבנת תהליך עבודה, אחריות, שלבים, סטנדרט שירות וכללי טיפול.",
    ),
    SourceTypeMetadata(
        code="pdf_first_protocol",
        label_he="פרוטוקול",
        plural_label_he="פרוטוקולים",
        filter_label_he="פרוטוקולים",
        semantic_notes_en="PDF-first normalized protocol artifact used by the retrieval pipeline.",
        semantic_description_he="פרוטוקול שעובד במסלול PDF-first. מבחינה סמנטית זהו פרוטוקול, אך הקוד הפנימי משקף את מקור העיבוד הטכני.",
    ),
    SourceTypeMetadata(
        code="pdf_first_attachment",
        label_he="נספח",
        plural_label_he="נספחים",
        filter_label_he="נספחים",
        semantic_notes_en="PDF-first normalized attachment artifact used by the retrieval pipeline.",
        semantic_description_he="נספח שעובד במסלול PDF-first. מבחינה סמנטית זהו נספח למסמך עירוני, אך הקוד הפנימי משקף את מקור העיבוד הטכני.",
    ),
    SourceTypeMetadata(
        code="pdf_first_v4_protocol",
        label_he="פרוטוקול",
        plural_label_he="פרוטוקולים",
        filter_label_he="פרוטוקולים",
        semantic_notes_en="PDF-first V4 normalized protocol artifact used by the retrieval pipeline.",
        semantic_description_he="פרוטוקול שעובד במסלול PDF-first V4. מבחינה סמנטית זהו פרוטוקול, אך הקוד הפנימי משקף גרסת עיבוד טכנית.",
    ),
    SourceTypeMetadata(
        code="pdf_first_v4_attachment",
        label_he="נספח",
        plural_label_he="נספחים",
        filter_label_he="נספחים",
        semantic_notes_en="PDF-first V4 normalized attachment artifact used by the retrieval pipeline.",
        semantic_description_he="נספח שעובד במסלול PDF-first V4. מבחינה סמנטית זהו נספח למסמך עירוני, אך הקוד הפנימי משקף גרסת עיבוד טכנית.",
    ),
)

SOURCE_TYPE_BY_CODE = {row.code: row for row in SOURCE_TYPE_TAXONOMY}
COMBINED_SOURCE_TYPE_FILTERS_HE = {
    "פרוטוקולים ונספחים": ("protocol", "attachment"),
}

PUBLIC_SOURCE_TYPE_FILTER_CODES = (
    "protocol",
    "budget",
    "financial_report",
    "audit_report",
    "tender",
    "job_tender",
    "foi_transparency",
    "bylaw",
    "form_service",
    "work_plan",
    "support_allocation",
    "arnona_order",
    "data_registry",
    "public_notice",
    "procedure",
)


def source_type_metadata(code: str | None) -> SourceTypeMetadata:
    normalized = str(code or "").strip()
    known = SOURCE_TYPE_BY_CODE.get(normalized)
    if known is not None:
        return known
    fallback_label = normalized or "unknown"
    return SourceTypeMetadata(
        code=fallback_label,
        label_he=fallback_label,
        plural_label_he=fallback_label,
        filter_label_he=fallback_label,
        semantic_notes_en="Unrecognized source type code.",
        semantic_description_he="סוג מקור לא מזוהה. יש להשתמש בזהירות עד שיוגדר במילון סוגי המקורות.",
    )


def source_type_metadata_payload(code: str | None) -> dict[str, str]:
    return asdict(source_type_metadata(code))


def source_type_display_fields(code: str | None) -> dict[str, str]:
    meta = source_type_metadata(code)
    return {
        "source_type_label_he": meta.label_he,
        "source_type_plural_label_he": meta.plural_label_he,
        "source_type_filter_label_he": meta.filter_label_he,
        "source_type_semantic_notes_en": meta.semantic_notes_en,
        "source_type_semantic_description_he": meta.semantic_description_he,
    }


def source_type_filter_options(codes: Iterable[str] | None = None) -> list[dict[str, str]]:
    selected_codes = tuple(codes) if codes is not None else PUBLIC_SOURCE_TYPE_FILTER_CODES
    return [
        {
            "value": meta.code,
            "label_he": meta.filter_label_he,
            "singular_label_he": meta.label_he,
            "plural_label_he": meta.plural_label_he,
            "semantic_notes_en": meta.semantic_notes_en,
            "semantic_description_he": meta.semantic_description_he,
        }
        for meta in (source_type_metadata(code) for code in selected_codes)
    ]


def source_type_taxonomy_payload() -> list[dict[str, str]]:
    return [source_type_metadata_payload(row.code) for row in SOURCE_TYPE_TAXONOMY]


def source_type_code_from_label(value: str | None) -> str | None:
    compact = " ".join(str(value or "").split())
    if not compact:
        return None
    if compact in SOURCE_TYPE_BY_CODE:
        return compact
    for meta in SOURCE_TYPE_TAXONOMY:
        labels = {meta.label_he, meta.plural_label_he, meta.filter_label_he}
        labels.update(part.strip() for part in meta.filter_label_he.split("/") if part.strip())
        if compact in labels:
            return meta.code
    return None


def source_type_codes_from_filter_value(value: str | None) -> list[str]:
    compact = " ".join(str(value or "").split())
    if not compact:
        return []
    combined = COMBINED_SOURCE_TYPE_FILTERS_HE.get(compact)
    if combined is not None:
        return list(combined)
    code = source_type_code_from_label(compact)
    return [code] if code is not None else []
