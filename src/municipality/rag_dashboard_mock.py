from __future__ import annotations

from copy import deepcopy
from typing import Any


CURRENT_QUESTION = "מה הוחלט לגבי תכנית רובע טו?"


def _evidence_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": "evidence_rova_tet_vav_protocol_1",
            "source_type": "protocol",
            "source_title": "פרוטוקול מועצה 23.06.2024",
            "source_url": "https://example.local/protocols/2024-06-23.pdf",
            "retrieval_artifact_id": "artifact_protocol_decision_unit_478",
            "artifact_kind": "decision_unit",
            "retrieval_set_id": "retrieval_set_rova_tet_vav_2024",
            "header_path": ["מועצת העיר", "תכנון ובנייה", "תכנית רובע טו"],
            "page_span": {"start": 7, "end": 7},
            "start_offset": 1023,
            "end_offset": 1188,
            "bbox": [120, 220, 510, 270],
            "text": "הוועדה המקומית מאשרת את הפקדת תכנית רובע טו בתנאים המפורטים בהחלטה.",
            "confidence_label": "גבוהה",
            "extraction_warnings": [],
        },
        {
            "id": "evidence_rova_tet_vav_protocol_2",
            "source_type": "protocol",
            "source_title": "פרוטוקול ועדת משנה לתכנון ובנייה 02.07.2024",
            "source_url": "https://example.local/protocols/2024-07-02.pdf",
            "retrieval_artifact_id": "artifact_protocol_decision_unit_1123",
            "artifact_kind": "decision_unit",
            "retrieval_set_id": "retrieval_set_rova_tet_vav_2024",
            "header_path": ["ועדת משנה", "תכנון ובנייה", "תנאי הפקדה"],
            "page_span": {"start": 11, "end": 12},
            "start_offset": 512,
            "end_offset": 780,
            "bbox": [88, 178, 530, 344],
            "text": "נקבעו תנאים להמשך קידום התכנית והפקדתה, לרבות השלמת מסמכים לפני פרסום.",
            "confidence_label": "גבוהה",
            "extraction_warnings": [],
        },
        {
            "id": "evidence_rova_tet_vav_environment_1",
            "source_type": "attachment",
            "source_title": "נספח סביבתי לתכנית רובע טו",
            "source_url": "https://example.local/attachments/rova-tet-vav-environment.pdf",
            "retrieval_artifact_id": "artifact_attachment_environment_unit_31",
            "artifact_kind": "attachment_section",
            "retrieval_set_id": "retrieval_set_rova_tet_vav_2024",
            "header_path": ["נספחים", "השפעה סביבתית", "עדכון דו״ח"],
            "page_span": {"start": 3, "end": 4},
            "start_offset": 244,
            "end_offset": 601,
            "bbox": [96, 190, 498, 390],
            "text": "נדרש עדכון בדו״ח ההשפעה על הסביבה לפני שלב ההפקדה הסופית.",
            "confidence_label": "בינונית",
            "extraction_warnings": ["הנספח מוצג כמקור תומך ולא כהחלטה עצמאית."],
        },
        {
            "id": "evidence_rova_tet_vav_publication_1",
            "source_type": "protocol",
            "source_title": "פרוטוקול מועצה 15.07.2024",
            "source_url": "https://example.local/protocols/2024-07-15.pdf",
            "retrieval_artifact_id": "artifact_protocol_publication_unit_15",
            "artifact_kind": "procedural_action",
            "retrieval_set_id": "retrieval_set_rova_tet_vav_2024",
            "header_path": ["מועצת העיר", "פרסום לציבור", "שמיעת התנגדויות"],
            "page_span": {"start": 5, "end": 5},
            "start_offset": 722,
            "end_offset": 930,
            "bbox": [112, 300, 520, 358],
            "text": "הצגת התכנית לציבור ושמיעת ההתנגדויות תבוצע בכפוף לפרסום הודעה כדין.",
            "confidence_label": "גבוהה",
            "extraction_warnings": [],
        },
    ]


def _resident_evidence_link(evidence_ref: str) -> dict[str, str]:
    return {
        "label_he": "מקור",
        "icon": "document-link",
        "evidence_ref": evidence_ref,
    }


def _decisions() -> list[dict[str, Any]]:
    specs = [
        (
            "decision_rova_tet_vav_1",
            "אישור להפקדה בתנאים",
            "אישור להפקדה בתנאים",
            "APPROVAL",
            "evidence_rova_tet_vav_protocol_1",
            "2024-06-23",
            "meeting_date",
        ),
        (
            "decision_rova_tet_vav_2",
            "תנאי קידום והפקדה",
            "נקבעו תנאים להמשך קידום התכנית והפקדתה.",
            "APPROVAL",
            "evidence_rova_tet_vav_protocol_2",
            "2024-07-02",
            "decision_date",
        ),
        (
            "decision_rova_tet_vav_3",
            "עדכון דו״ח השפעה סביבתית",
            "נדרש עדכון בדו״ח ההשפעה על הסביבה לפני שלב ההפקדה הסופית.",
            "REQUEST_FOR_INFO",
            "PENDING",
            "evidence_rova_tet_vav_environment_1",
            "2024-07-02",
            "review_date",
        ),
        (
            "decision_rova_tet_vav_4",
            "פרסום ושמיעת התנגדויות",
            "הצגת התכנית לציבור ושמיעת ההתנגדויות בכפוף לפרסום הודעה כדין.",
            "PUBLICATION",
            "NOTED",
            "evidence_rova_tet_vav_publication_1",
            "2024-07-15",
            "publication_date",
        ),
    ]

    rows: list[dict[str, Any]] = []
    for spec in specs:
        if len(spec) == 7:
            decision_id, title, summary, decision_kind_code, evidence_ref, date_value, time_kind = spec
            outcome_status_code = "APPROVED_WITH_CONDITIONS"
        else:
            decision_id, title, summary, decision_kind_code, outcome_status_code, evidence_ref, date_value, time_kind = spec
        rows.append(
            {
                "id": decision_id,
                "title": title,
                "raw_decision_text": summary,
                "summary": summary,
                "primary_time": {
                    "start": date_value,
                    "end": None,
                    "precision": "day",
                    "kind": time_kind,
                    "label_he": "תאריך עוגן",
                    "confidence_label": "גבוהה",
                    "evidence_refs": [evidence_ref],
                },
                "time_anchors": [
                    {
                        "kind": time_kind,
                        "start": date_value,
                        "end": None,
                        "precision": "day",
                        "label_he": "תאריך עוגן",
                        "confidence_label": "גבוהה",
                        "evidence_refs": [evidence_ref],
                    }
                ],
                "decision_kind": {
                    "code": decision_kind_code,
                    "label_he": "אישור" if decision_kind_code == "APPROVAL" else "פעולה תהליכית",
                    "vocabulary": "municipal-decision-kind:v1",
                    "raw_text": summary,
                    "confidence_label": "גבוהה",
                },
                "outcome_status": {
                    "code": outcome_status_code,
                    "label_he": _outcome_status_label(outcome_status_code),
                    "vocabulary": "municipal-outcome-status:v1",
                    "raw_text": summary,
                    "confidence_label": "גבוהה",
                },
                "legal_effect": {
                    "code": "PROCEDURAL" if decision_kind_code in {"PUBLICATION", "REQUEST_FOR_INFO"} else "BINDING",
                    "label_he": "תהליכי" if decision_kind_code in {"PUBLICATION", "REQUEST_FOR_INFO"} else "מחייב",
                    "vocabulary": "municipal-legal-effect:v1",
                    "confidence_label": "בינונית",
                },
                "topic_ids": ["topic_rova_tet_vav_plan"],
                "entity_ids": ["entity_rova_tet_vav_selected_area"],
                "timeline_event_ids": [f"event_{date_value.replace('-', '_')}"],
                "evidence_refs": [evidence_ref],
                "resident_evidence_links": [_resident_evidence_link(evidence_ref)],
                "normalized_by": "mock",
                "curation_status": "unreviewed",
                "limitations": [],
            }
        )
    return rows


def _outcome_status_label(code: str) -> str:
    labels = {
        "APPROVED_WITH_CONDITIONS": "אושר בתנאים",
        "PENDING": "בהמשך טיפול",
        "NOTED": "נרשם",
    }
    return labels.get(code, "לא ידוע")


def _topic_node(
    *,
    topic_id: str,
    label: str,
    primary_category_id: str,
    mention_count: int,
    decision_count: int,
    parent_id: str | None = None,
    child_ids: list[str] | None = None,
    depth: int = 1,
) -> dict[str, Any]:
    return {
        "id": topic_id,
        "label": label,
        "origin": "hybrid",
        "curation_status": "edited",
        "generated_label": label,
        "curated_label": label,
        "topic_type": {
            "code": "PLANNING_PROJECT" if "תכנית" in label else "CATEGORY",
            "label_he": "פרויקט תכנון" if "תכנית" in label else "קטגוריה",
            "vocabulary": "municipal-topic-type:v1",
        },
        "category_ids": [primary_category_id],
        "primary_category_id": primary_category_id,
        "parent_id": parent_id,
        "child_ids": child_ids or [],
        "sibling_ids": [],
        "path_ids": list(dict.fromkeys(value for value in ["topic_root_planning_building", parent_id, topic_id] if value)),
        "depth": depth,
        "sort_order": mention_count,
        "mention_count": mention_count,
        "decision_count": decision_count,
        "recent_activity_at": "2024-07-15",
        "time_rollup": {
            "first_seen": "2024-05-10",
            "last_seen": "2024-07-15",
            "active_years": [2024],
        },
        "decision_rollups": {
            "decision_kind": {"APPROVAL": 2, "PUBLICATION": 1, "REQUEST_FOR_INFO": 1},
            "outcome_status": {"APPROVED_WITH_CONDITIONS": 2, "PENDING": 1},
            "legal_effect": {"BINDING": 2, "PROCEDURAL": 2},
        },
        "merged_from_topic_ids": [],
        "split_from_topic_id": None,
        "hidden_from_public": False,
        "confidence_label": "גבוהה",
        "evidence_refs": ["evidence_rova_tet_vav_protocol_1"],
    }


def _related_topics() -> list[dict[str, str]]:
    return [
        {
            "id": "related_rova_tet_vav",
            "label": "רובע טו",
            "relation_type": "geography",
            "relation_label_he": "גאוגרפיה",
            "target_topic_id": "topic_rova_tet_vav",
        },
        {
            "id": "related_rova_tet_vav_plan",
            "label": "תכנית רובע טו",
            "relation_type": "topic",
            "relation_label_he": "נושא",
            "target_topic_id": "topic_rova_tet_vav_plan",
        },
        {
            "id": "related_planning",
            "label": "תכנון ובנייה",
            "relation_type": "category",
            "relation_label_he": "קטגוריה",
            "target_topic_id": "topic_root_planning_building",
        },
        {
            "id": "related_detailed_plans",
            "label": "תוכניות מפורטות",
            "relation_type": "topic",
            "relation_label_he": "נושא",
            "target_topic_id": "topic_detailed_plans",
        },
        {
            "id": "related_public_transport",
            "label": "תחבורה ציבורית",
            "relation_type": "topic",
            "relation_label_he": "נושא",
            "target_topic_id": "topic_public_transport",
        },
        {
            "id": "related_open_spaces",
            "label": "שטחים פתוחים",
            "relation_type": "topic",
            "relation_label_he": "נושא",
            "target_topic_id": "topic_open_spaces",
        },
        {
            "id": "related_environmental_impact",
            "label": "השפעה סביבתית",
            "relation_type": "topic",
            "relation_label_he": "נושא",
            "target_topic_id": "topic_environmental_impact",
        },
    ]


def _timeline_events() -> list[dict[str, Any]]:
    return [
        {
            "id": "event_2024_05_10",
            "date": "2024-05-10",
            "date_label": "10.05.2024",
            "title": "דיונים מקדימים",
            "summary": "פרסום תכנית",
            "selected": False,
            "decision_ids": [],
            "evidence_refs": [],
        },
        {
            "id": "event_2024_05_28",
            "date": "2024-05-28",
            "date_label": "28.05.2024",
            "title": "דיון ציבורי",
            "summary": "הצגת התכנית",
            "selected": False,
            "decision_ids": [],
            "evidence_refs": [],
        },
        {
            "id": "event_2024_06_23",
            "date": "2024-06-23",
            "date_label": "23.06.2024",
            "title": "ישיבה מס׳ 478",
            "summary": "אישור להפקדה",
            "selected": True,
            "decision_ids": ["decision_rova_tet_vav_1"],
            "evidence_refs": ["evidence_rova_tet_vav_protocol_1"],
        },
        {
            "id": "event_2024_07_02",
            "date": "2024-07-02",
            "date_label": "02.07.2024",
            "title": "החלטה מס׳ 1123",
            "summary": "אישור התנאים",
            "selected": False,
            "decision_ids": ["decision_rova_tet_vav_2", "decision_rova_tet_vav_3"],
            "evidence_refs": ["evidence_rova_tet_vav_protocol_2", "evidence_rova_tet_vav_environment_1"],
        },
        {
            "id": "event_2024_07_15",
            "date": "2024-07-15",
            "date_label": "15.07.2024",
            "title": "פרסום להפקדה",
            "summary": "התחלת תהליך",
            "selected": False,
            "decision_ids": ["decision_rova_tet_vav_4"],
            "evidence_refs": ["evidence_rova_tet_vav_publication_1"],
        },
    ]


def _map_entities() -> list[dict[str, Any]]:
    return [
        {
            "id": "entity_rova_tet_vav_selected_area",
            "label": "רובע טו",
            "entity_type": {
                "code": "PLANNING_AREA",
                "label_he": "אזור תכנון",
                "vocabulary": "municipal-entity-type:v1",
            },
            "spatial_representation": "schematic",
            "schematic_shape": {
                "kind": "polygon",
                "coordinates": [[336, 330], [377, 238], [455, 250], [518, 287], [491, 344], [426, 341], [404, 392]],
            },
            "real_geometry": None,
            "geometry_provenance": None,
            "confidence_label": "בינונית",
            "uncertainty_reasons": ["מיקום סכמטי לצורכי המחשה בלבד"],
            "active_from": "2024-05-10",
            "active_to": None,
            "activity_score": 87,
            "is_recent_high_activity": True,
            "topic_ids": ["topic_rova_tet_vav_plan"],
            "decision_ids": ["decision_rova_tet_vav_1", "decision_rova_tet_vav_2"],
            "evidence_refs": ["evidence_rova_tet_vav_protocol_1"],
        },
        {
            "id": "entity_rova_tet_vav_transit_route",
            "label": "תוואי תחבורה ציבורית",
            "entity_type": {
                "code": "TRANSIT_ROUTE",
                "label_he": "תחבורה ציבורית",
                "vocabulary": "municipal-entity-type:v1",
            },
            "spatial_representation": "schematic",
            "schematic_shape": {"kind": "polyline", "coordinates": [[525, 0], [548, 184], [600, 328], [562, 518]]},
            "real_geometry": None,
            "geometry_provenance": None,
            "confidence_label": "בינונית",
            "uncertainty_reasons": ["תוואי סכמטי ולא GIS"],
            "active_from": "2024-05-28",
            "active_to": None,
            "activity_score": 62,
            "is_recent_high_activity": True,
            "topic_ids": ["topic_public_transport"],
            "decision_ids": [],
            "evidence_refs": [],
        },
    ]


def build_mock_rag_dashboard_payload() -> dict[str, Any]:
    """Return a backend-owned mock payload shaped like the future dashboard API."""

    evidence = _evidence_rows()
    decisions = _decisions()
    topics = [
        _topic_node(
            topic_id="topic_root_planning_building",
            label="תכנון ובנייה",
            primary_category_id="planning",
            mention_count=342,
            decision_count=41,
            child_ids=["topic_master_plans", "topic_detailed_plans", "topic_permits"],
            depth=0,
        ),
        _topic_node(
            topic_id="topic_rova_tet_vav_plan",
            label="תכנית רובע טו",
            primary_category_id="planning",
            mention_count=23,
            decision_count=7,
            parent_id="topic_detailed_plans",
            depth=2,
        ),
    ]

    return {
        "ui_copy": {
            "document_title": "לוח מחוונים עירוני",
            "municipality_brand": "עירייה",
            "header": {
                "search_aria_label": "שאלת חיפוש",
                "search_submit_label": "חיפוש",
                "popular_searches_button": "חיפושים פופולריים",
                "filters_button": "מסננים",
                "admin_button": "מנהל",
                "admin_aria_label": "מנהל",
            },
            "answer_drawer": {
                "close_label": "סגירת תשובה",
                "title": "תשובה",
                "question_prefix": "שאלה:",
                "brief_title": "תקציר",
                "confidence_label": "ביטחון התשובה:",
                "decisions_title": "החלטות עיקריות",
                "related_topics_title": "נושאים קשורים",
                "limitations_title": "מגבלות",
                "source_link_label": "מקור",
            },
            "map": {
                "title": "מפה סכמטית של רובע טו",
                "description": "ים במערב, רשת רחובות בהירה, אזור נבחר סגול, תוואי תחבורה ציבורית כחול ופארקים ירוקים.",
                "provenance_label": "מפה סכמטית בלבד",
                "provenance_description": "אין גיאומטריית GIS מאומתת; המיקומים והצורות מוצגים להמחשה בלבד על בסיס הראיות.",
                "sea_label": "חוף הים",
                "legend_title": "מקרא",
                "control_labels": ["מרכז מפה", "התקרבות", "התרחקות", "שכבות מפה"],
                "area_labels": ["צפון העיר", "מרכז העיר", "מערב העיר", "מזרח העיר", "דרום העיר"],
                "marker_labels": ["תחנת תחבורה ציבורית", "פארק", "מבנה ציבור"],
            },
            "timeline": {
                "title": "ציר זמן",
                "previous_label": "אירוע קודם",
                "next_label": "אירוע הבא",
            },
            "start_discovery_panel": {
                "categories_title": "קטגוריות",
                "hot_topics_title": "נושאים בולטים",
                "topic_tree_title": "עץ נושאים",
                "show_more_label": "הצג עוד",
                "show_full_tree_label": "הצג כל העץ",
            },
            "filter_modal": {
                "title": "סינון תוצאות",
                "close_label": "סגירת מסננים",
                "sections": ["אזור", "טווח זמן", "קטגוריה", "סוגי מקורות", "ודאות"],
                "reset_label": "איפוס",
                "apply_label": "החל סינון",
                "options": {
                    "area": ["כל העיר", "רובע טו"],
                    "time_range": ["2024", "כל השנים"],
                    "category": ["תכנון ובנייה", "תחבורה"],
                    "source_types": ["פרוטוקולים ונספחים", "פרוטוקולים"],
                    "confidence": ["גבוהה ובינונית", "כל הרמות"],
                },
            },
            "popular_searches": {
                "title": "חיפושים פופולריים",
                "choices": [CURRENT_QUESTION],
            },
        },
        "state": {
            "municipality_id": "ashdod",
            "current_question": CURRENT_QUESTION,
            "search_intent": "decision_about_topic",
            "intent_resolution": {
                "intent": "decision_about_topic",
                "confidence_label": "גבוהה",
                "matched_terms": ["תכנית רובע טו", "הוחלט"],
            },
            "selected_time_range": {"start": "2024-05-10", "end": "2024-07-15"},
            "selected_category_id": "planning",
            "selected_topic_node_id": "topic_rova_tet_vav_plan",
            "selected_map_entity_id": "entity_rova_tet_vav_selected_area",
            "selected_timeline_event_id": "event_2024_06_23",
            "current_answer_id": "answer_rova_tet_vav_2024",
            "active_detail_drawer_mode": "answer",
            "confidence_filter": "medium_and_high",
            "source_type_filter": ["protocol", "attachment"],
            "filter_modal_open": False,
            "active_filter_count": 0,
            "popular_searches_open": False,
            "cached_answer_id": "cached_answer_rova_tet_vav_2024",
            "map_mode": "schematic",
            "generation_status": "mock_answer_ready",
            "login_state": "anonymous",
        },
        "start_discovery_panel": {
            "categories": [
                {"id": "planning", "label": "תכנון ובנייה", "count": 342, "selected": True},
                {"id": "transport", "label": "תחבורה", "count": 128, "selected": False},
                {"id": "education", "label": "חינוך", "count": 95, "selected": False},
                {"id": "welfare", "label": "רווחה", "count": 76, "selected": False},
                {"id": "environment", "label": "סביבה", "count": 64, "selected": False},
            ],
            "hot_topics": [
                {"id": "topic_rova_tet_vav_plan", "label": "תכנית רובע טו", "count": 23, "selected": True},
                {"id": "topic_light_rail", "label": "הקמת קו רכבת קלה", "count": 18, "selected": False},
                {"id": "topic_lachish_park", "label": "שדרוג פארק לכיש", "count": 14, "selected": False},
                {"id": "topic_master_plan_new", "label": "תכנית מתאר חדשה", "count": 11, "selected": False},
            ],
            "focused_topic_tree_context": {
                "selected_topic_id": "topic_detailed_plans",
                "root": {"id": "topic_root_planning_building", "label": "תכנון ובנייה"},
                "parent": {"id": "topic_root_planning_building", "label": "תכנון ובנייה"},
                "breadcrumbs": [
                    {"id": "topic_root_planning_building", "label": "תכנון ובנייה"},
                    {"id": "topic_detailed_plans", "label": "תוכניות מפורטות"},
                ],
                "siblings": [],
                "children": [
                    {"id": "topic_master_plans", "label": "תוכניות מתאר", "count": 12, "selected": False},
                    {"id": "topic_detailed_plans", "label": "תוכניות מפורטות", "count": 23, "selected": True},
                    {"id": "topic_permits", "label": "היתרים", "count": 7, "selected": False},
                ],
                "nearby_topics": [],
                "collapsed_categories": [{"id": "transport", "label": "תחבורה"}],
            },
        },
        "main_civic_workspace": {
            "map": {
                "spatial_representation": "schematic",
                "label": "מפה סכמטית",
                "real_geometry": None,
                "geometry_provenance": None,
                "provenance": {
                    "status": "schematic_only",
                    "label_he": "מפה סכמטית בלבד",
                    "description_he": "אין גיאומטריית GIS מאומתת; המיקומים והצורות מוצגים להמחשה בלבד על בסיס הראיות.",
                    "source_evidence_refs": [],
                },
                "entities": _map_entities(),
                "legend": [
                    {"id": "selected_area", "label": "חלקה נבחרת"},
                    {"id": "planning_boundary", "label": "תכנית / גבול תכנון"},
                    {"id": "neighborhood_boundary", "label": "שכונה / גבול עירוני"},
                    {"id": "public_transport", "label": "תחבורה ציבורית"},
                    {"id": "schools", "label": "מוסד חינוך"},
                    {"id": "context_buildings", "label": "מבנה הקשר OSM"},
                    {"id": "context_pois", "label": "מוקד הקשר OSM"},
                ],
                "real_gis_available": False,
            },
            "timeline": {
                "selected_event_id": "event_2024_06_23",
                "events": _timeline_events(),
            },
        },
        "end_detail_drawer": {
            "mode": "answer",
            "answer_id": "answer_rova_tet_vav_2024",
            "title": "תשובה",
            "question": CURRENT_QUESTION,
            "brief": "הוועדה המקומית אישרה להפקדה את תכנית רובע טו כולל שימושים מעורבים, מגורים, מסחר, שטחי ציבור פתוחים ודרכי תחבורה. ההפקדה נקבעה להתקדם בהתאמה לתנאים המפורטים בהחלטה.",
            "confidence_label": "גבוהה",
            "decisions": decisions,
            "related_topics": _related_topics(),
            "limitations": ["ייתכנו שינויים בהחלטות עד לאישור סופי. מומלץ לפתוח את המקור לפני הסקת מסקנות."],
        },
        "contracts": {
            "topic_nodes": topics,
            "decisions": decisions,
            "evidence": evidence,
            "map_entities": _map_entities(),
            "related_topics": _related_topics(),
        },
        "evidence": evidence,
    }


def get_mock_rag_dashboard_evidence(evidence_id: str) -> dict[str, Any] | None:
    for row in _evidence_rows():
        if row["id"] == evidence_id:
            return deepcopy(row)
    return None


def get_mock_rag_dashboard_payload() -> dict[str, Any]:
    return deepcopy(build_mock_rag_dashboard_payload())


def apply_mock_rag_dashboard_interaction(
    *,
    state: dict[str, Any] | None,
    interaction: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload = get_mock_rag_dashboard_payload()
    next_state = dict(payload["state"])
    if isinstance(state, dict):
        for key in payload["state"]:
            if key in state:
                next_state[key] = state[key]

    if not isinstance(interaction, dict):
        return None

    interaction_type = str(interaction.get("type") or "").strip()
    interaction_id = str(interaction.get("id") or "").strip()
    if not interaction_type:
        return None

    category_ids = {row["id"] for row in payload["start_discovery_panel"]["categories"]}
    hot_topic_ids = {row["id"] for row in payload["start_discovery_panel"]["hot_topics"]}
    tree_ids = {row["id"] for row in payload["start_discovery_panel"]["focused_topic_tree_context"]["children"]}
    event_ids = {row["id"] for row in payload["main_civic_workspace"]["timeline"]["events"]}
    entity_ids = {row["id"] for row in payload["main_civic_workspace"]["map"]["entities"]}
    evidence_by_id = {row["id"]: row for row in payload["evidence"]}
    related_by_id = {row["id"]: row for row in payload["end_detail_drawer"]["related_topics"]}
    related_target_ids = {row["target_topic_id"] for row in payload["end_detail_drawer"]["related_topics"]}

    if interaction_type == "select_category":
        if interaction_id not in category_ids:
            return None
        next_state["selected_category_id"] = interaction_id
        next_state["active_detail_drawer_mode"] = "category"
    elif interaction_type == "select_hot_topic":
        if interaction_id not in hot_topic_ids:
            return None
        next_state["selected_topic_node_id"] = interaction_id
        next_state["active_detail_drawer_mode"] = "topic"
    elif interaction_type == "select_topic_tree_node":
        if interaction_id not in tree_ids:
            return None
        next_state["selected_topic_node_id"] = interaction_id
        payload["start_discovery_panel"]["focused_topic_tree_context"]["selected_topic_id"] = interaction_id
        next_state["active_detail_drawer_mode"] = "topicTree"
    elif interaction_type == "select_timeline_event":
        if interaction_id not in event_ids:
            return None
        next_state["selected_timeline_event_id"] = interaction_id
        next_state["active_detail_drawer_mode"] = "timelineEvent"
    elif interaction_type == "select_map_entity":
        if interaction_id not in entity_ids:
            return None
        next_state["selected_map_entity_id"] = interaction_id
        next_state["active_detail_drawer_mode"] = "mapEntity"
    elif interaction_type == "select_related_topic":
        related = related_by_id.get(interaction_id)
        topic_id = related["target_topic_id"] if related else interaction_id
        if topic_id not in related_target_ids and topic_id not in hot_topic_ids and topic_id not in tree_ids:
            return None
        next_state["selected_topic_node_id"] = topic_id
        next_state["active_detail_drawer_mode"] = "topic"
    elif interaction_type == "open_evidence":
        if interaction_id not in evidence_by_id:
            return None
        next_state["active_detail_drawer_mode"] = "evidencePreview"
        next_state["selected_evidence_id"] = interaction_id
        payload["evidence_preview"] = evidence_by_id[interaction_id]
    elif interaction_type == "apply_filters":
        filters = interaction.get("filters") if isinstance(interaction.get("filters"), dict) else {}
        next_state["filter_modal_open"] = False
        next_state["active_filter_count"] = sum(1 for value in filters.values() if value not in (None, "", [], {}))
        next_state["active_filter_summary"] = filters
        category_id = _category_id_from_filter(filters.get("category"))
        if category_id in category_ids:
            next_state["selected_category_id"] = category_id
    elif interaction_type == "reset_filters":
        next_state["filter_modal_open"] = False
        next_state["active_filter_count"] = 0
        next_state["active_filter_summary"] = {}
    elif interaction_type == "select_popular_search":
        choices = set(payload["ui_copy"]["popular_searches"]["choices"])
        if interaction_id not in choices:
            return None
        next_state["current_question"] = interaction_id
        next_state["cached_answer_id"] = "cached_answer_rova_tet_vav_2024"
        next_state["active_detail_drawer_mode"] = "answer"
    else:
        return None

    payload["state"] = next_state
    _apply_selection_flags(payload)
    payload["interaction_result"] = {
        "type": interaction_type,
        "id": interaction_id,
        "status": "applied",
    }
    return payload


def _apply_selection_flags(payload: dict[str, Any]) -> None:
    state = payload["state"]
    for row in payload["start_discovery_panel"]["categories"]:
        row["selected"] = row["id"] == state.get("selected_category_id")
    for row in payload["start_discovery_panel"]["hot_topics"]:
        row["selected"] = row["id"] == state.get("selected_topic_node_id")
    for row in payload["start_discovery_panel"]["focused_topic_tree_context"]["children"]:
        row["selected"] = row["id"] == state.get("selected_topic_node_id")
    for row in payload["main_civic_workspace"]["timeline"]["events"]:
        row["selected"] = row["id"] == state.get("selected_timeline_event_id")
    payload["main_civic_workspace"]["timeline"]["selected_event_id"] = state.get("selected_timeline_event_id")
    for row in payload["main_civic_workspace"]["map"]["entities"]:
        row["selected"] = row["id"] == state.get("selected_map_entity_id")


def _category_id_from_filter(value: Any) -> str | None:
    compact = " ".join(str(value or "").split())
    labels = {
        "תכנון ובנייה": "planning",
        "תחבורה": "transport",
        "חינוך": "education",
        "רווחה": "welfare",
        "סביבה": "environment",
    }
    return labels.get(compact, compact if compact in set(labels.values()) else None)
