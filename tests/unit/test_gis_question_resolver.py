from __future__ import annotations

from municipality.gis_question_resolver import resolve_geo_intent, resolve_gis_focus
from municipality.rag_dashboard_adapter import build_dashboard_payload_from_ask_result


def test_parcel_question_resolves_parcel_context() -> None:
    result = resolve_geo_intent("מה קורה סביב גוש 7103 חלקה 43?")

    assert result.intent == "parcel_context"
    assert result.needs_gis is True
    assert result.focus is not None
    assert result.focus.focus_type == "parcel"
    assert result.focus.gush == "7103"
    assert result.focus.helka == "43"


def test_plan_status_question_resolves_plan_focus() -> None:
    result = resolve_geo_intent("מה הסטטוס של תכנית 101-0057273?")

    assert result.intent == "plan_status"
    assert result.focus is not None
    assert result.focus.focus_type == "plan"
    assert result.focus.plan_number == "101-0057273"


def test_development_near_place_question_resolves_place_focus() -> None:
    result = resolve_geo_intent("אילו פרויקטי מגורים חדשים מתוכננים ליד פארק לכיש?")

    assert result.intent == "development_near_place"
    assert result.needs_gis is True
    assert result.focus is not None
    assert result.focus.focus_type == "place"
    assert result.focus.place_query == "פארק לכיש"


def test_services_near_relative_place_marks_unresolved_focus() -> None:
    result = resolve_geo_intent("אילו תחנות תחבורה ובתי ספר ליד הבית שלי?")

    assert result.intent == "services_near_place"
    assert result.focus is not None
    assert result.focus.focus_type == "unresolved_place"
    assert result.focus.unresolved_reason == "resident_relative_place"


def test_public_participation_question_keeps_plan_focus() -> None:
    result = resolve_geo_intent("מתי אפשר להגיש התנגדות לתכנית 101-0057273 באזור שלי?")

    assert result.intent == "planning_public_participation"
    assert result.focus is not None
    assert result.focus.focus_type == "plan"
    assert result.focus.plan_number == "101-0057273"


def test_unknown_question_does_not_require_gis() -> None:
    result = resolve_geo_intent("מה הוחלט לגבי תקציב התרבות?")

    assert result.intent == "unknown"
    assert result.needs_gis is False
    assert result.focus is None


def test_mikveh_question_resolves_religious_service_map_focus() -> None:
    result = resolve_geo_intent("מה הוחלט לגבי הקמה והפעלה של מקווה טהרה ברחוב ספיר ברובע י\"ז באשדוד?")

    assert result.intent == "public_service_facility_context"
    assert result.needs_gis is True
    assert result.focus is not None
    assert result.focus.focus_type == "place"
    assert result.focus.address_query == "רחוב ספיר"
    assert "religious_services" in result.resident_layer_keys
    assert result.govmap_layer_aliases == ("mikve", "neighborhoods_area")
    assert "parcels_cadaster" not in result.resident_layer_keys


def test_focus_resolver_accepts_gershayim_place_variants() -> None:
    focus = resolve_gis_focus("מה קרה באזור רובע ט״ו?")

    assert focus is not None
    assert focus.focus_type == "place"
    assert focus.place_query == "רובע ט״ו"


def test_dashboard_payload_carries_geo_intent_resolution() -> None:
    geo_intent = resolve_geo_intent("מה קורה סביב גוש 7103 חלקה 43?")
    payload = build_dashboard_payload_from_ask_result(
        question="מה קורה סביב גוש 7103 חלקה 43?",
        municipality_id="tel_aviv",
        ask_payload={"status": "answer", "answer": "נמצאו נתוני GIS סביב החלקה.", "citations": []},
        filters={},
        geo_intent_resolution=geo_intent.to_payload(),
    )

    assert payload["state"]["search_intent"] == "parcel_context"
    assert payload["state"]["intent_resolution"]["geo"]["focus"] == {
        "focus_type": "parcel",
        "confidence_label": "גבוהה",
        "gush": "7103",
        "helka": "43",
        "matched_text": "7103/43",
    }


def test_dashboard_error_payload_keeps_mikveh_gis_intent() -> None:
    geo_intent = resolve_geo_intent("מה הוחלט לגבי מקווה טהרה ברחוב ספיר באשדוד?").to_payload()
    geo_intent["map_context"] = {
        "status": "focus_needs_lookup",
        "focus": geo_intent["focus"],
        "layers": [],
        "caveats": ["יש לפתור את שם המקום או כתובת לפני טעינת שכבות GIS סביבו."],
    }
    payload = build_dashboard_payload_from_ask_result(
        question="מה הוחלט לגבי מקווה טהרה ברחוב ספיר באשדוד?",
        municipality_id="ashdod",
        ask_payload={"status": "answer", "answer": "נמצא הסכם בנושא מקווה.", "citations": []},
        filters={},
        geo_intent_resolution=geo_intent,
    )

    geo = payload["state"]["intent_resolution"]["geo"]
    assert geo["govmap_layer_aliases"][:1] == ["mikve"]
    assert payload["main_civic_workspace"]["map_context"]["status"] == "focus_needs_lookup"
    assert payload["contracts"]["map_entities"][0]["label"] == "רחוב ספיר"


def test_dashboard_payload_fuses_map_context_into_visible_state() -> None:
    geo_payload = resolve_geo_intent("מה קורה סביב גוש 7103 חלקה 43?").to_payload()
    geo_payload["map_context"] = {
        "status": "found",
        "focus": geo_payload["focus"],
        "municipality_code": "5000",
        "layers": [
            {"layer_key": "selected_parcel", "status": "found", "items": [], "count": 1},
            {"layer_key": "context_roads", "status": "context_only", "items": [], "count": 5},
        ],
        "caveats": ["שכבות OSM מוצגות כהקשר בלבד ואינן מקור רשמי."],
    }
    geo_payload["municipality_scope"] = {
        "requested_muni": "ashdod",
        "gis_municipality_code": "5000",
        "gis_municipality_slug": "tel_aviv",
        "effective_muni": "tel_aviv",
        "mismatch": True,
        "caveat_he": "השאלה מוקדה לפי מיקום ה-GIS, ולכן חיפוש המסמכים הותאם לרשות שעלתה מהמפה במקום לרשות שנשלחה מהדמו.",
    }

    payload = build_dashboard_payload_from_ask_result(
        question="מה קורה סביב גוש 7103 חלקה 43?",
        municipality_id="tel_aviv",
        ask_payload={"status": "answer", "answer": "נמצאו נתוני GIS סביב החלקה.", "citations": []},
        filters={},
        geo_intent_resolution=geo_payload,
    )

    assert payload["main_civic_workspace"]["map_context"]["municipality_code"] == "5000"
    assert payload["state"]["selected_map_entity_id"] == "entity_gis_focus_parcel"
    assert payload["contracts"]["map_entities"][0]["label"] == "גוש 7103 חלקה 43"
    assert payload["contracts"]["map_entities"][0]["selected"] is True
    assert any("שכבות OSM" in limitation for limitation in payload["end_detail_drawer"]["limitations"])
    assert any("מיקום ה-GIS" in limitation for limitation in payload["end_detail_drawer"]["limitations"])
