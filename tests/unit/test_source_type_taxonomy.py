from __future__ import annotations

from municipality.source_type_taxonomy import SOURCE_TYPE_BY_CODE, source_type_code_from_label, source_type_codes_from_filter_value, source_type_filter_options, source_type_metadata_payload


def test_source_type_taxonomy_contains_public_labels_and_semantic_context() -> None:
    required_codes = {
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
    }

    assert required_codes.issubset(SOURCE_TYPE_BY_CODE)
    for code in required_codes:
        payload = source_type_metadata_payload(code)
        assert payload["code"] == code
        assert payload["label_he"]
        assert payload["plural_label_he"]
        assert payload["filter_label_he"]
        assert payload["semantic_notes_en"]
        assert payload["semantic_description_he"]


def test_source_type_filter_options_keep_english_values_and_hebrew_labels() -> None:
    options = source_type_filter_options(["budget", "audit_report"])

    assert options == [
        {
            "value": "budget",
            "label_he": "תקציבים / מסמכי תקציב",
            "singular_label_he": "תקציב",
            "plural_label_he": "תקציבים",
            "semantic_notes_en": "Annual budget books, proposed budget, approved budget.",
            "semantic_description_he": "מסמכי תקציב שנתיים, הצעות תקציב ותקציב מאושר. מקור זה מתאים להבנת הקצאות כספיות, סדרי עדיפויות ותכנון תקציבי של הרשות.",
        },
        {
            "value": "audit_report",
            "label_he": "דוחות ביקורת / דוחות מבקר העירייה",
            "singular_label_he": "דוח ביקורת",
            "plural_label_he": "דוחות ביקורת",
            "semantic_notes_en": "Municipal comptroller / internal audit reports.",
            "semantic_description_he": "דוחות ביקורת של מבקר העירייה או ביקורת פנימית. מקור זה מתאים לזיהוי ליקויים, המלצות, סיכונים, כשלים תפעוליים וממצאי פיקוח.",
        },
    ]


def test_source_type_code_from_label_accepts_codes_and_hebrew_labels() -> None:
    assert source_type_code_from_label("budget") == "budget"
    assert source_type_code_from_label("תקציבים") == "budget"
    assert source_type_code_from_label("מסמכי תקציב") == "budget"
    assert source_type_code_from_label("דוחות מבקר העירייה") == "audit_report"
    assert source_type_codes_from_filter_value("פרוטוקולים ונספחים") == ["protocol", "attachment"]
