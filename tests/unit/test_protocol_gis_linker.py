from __future__ import annotations

from municipality.protocol_gis_linker import link_protocol_row_to_gis, link_topic_subject_v3_events_to_gis, protocol_gis_link_report_markdown


def _row(text: str, *, artifact_id: str = "artifact-1", subject_object: str | None = None) -> dict[str, str | None]:
    return {
        "artifact_id": artifact_id,
        "municipality_slug": "ashdod",
        "subject_object_he": subject_object,
        "subject_details_he": text,
        "body_text": text,
        "retrieval_text": text,
    }


def test_cadaster_reference_prioritizes_parcels_and_planning() -> None:
    candidates = link_protocol_row_to_gis(_row("הסכם שימוש בבית לברון, גוש 2457 חלקה 12", subject_object="בית לברון"))

    cadaster = [candidate for candidate in candidates if candidate.linking_mode == "explicit_cadaster_reference"]
    assert cadaster[0].layer_key == "parcels_cadaster"
    assert cadaster[0].confidence_label == "high"
    assert cadaster[0].candidate_query == {"gush": "2457", "parcel": "12"}
    assert "planning_land_use" in {candidate.layer_key for candidate in cadaster}


def test_named_school_links_to_education_and_neighborhood_context() -> None:
    candidates = link_protocol_row_to_gis(_row("שימוש עתידי בבית ספר יד שבתאי ברובע ו' והשפעתו על הקהילה", subject_object="בית ספר יד שבתאי"))

    named = [candidate for candidate in candidates if candidate.linking_mode == "named_facility_geocoding"]
    assert named[0].extracted_signal == "בית ספר יד שבתאי"
    assert named[0].layer_key == "education_facilities"
    assert "neighborhoods_and_statistics" in {candidate.layer_key for candidate in candidates}


def test_named_square_links_to_transport_and_public_works() -> None:
    candidates = link_protocol_row_to_gis(_row("מימון פרויקט תחבורה ציבורית באזור כיכר רמון", subject_object="כיכר רמון"))

    named_layers = {candidate.layer_key for candidate in candidates if candidate.linking_mode == "named_facility_geocoding"}
    assert "roads_parking_public_works" in named_layers
    assert "public_transport_access" in named_layers


def test_citywide_dog_waste_stays_low_confidence_without_specific_place() -> None:
    candidates = link_protocol_row_to_gis(_row("טיפול עירוני בגללי כלבים וניקיון בפארקים ובגינות ציבוריות"))

    assert candidates[0].linking_mode == "citywide_context"
    assert candidates[0].confidence_label == "low"
    assert "environment_sanitation" in {candidate.layer_key for candidate in candidates}
    assert all(candidate.candidate_query == {} for candidate in candidates)


def test_mikveh_agreement_links_to_religious_services() -> None:
    candidates = link_protocol_row_to_gis(_row("אישור הסכם להקמת והפעלת מקווה טהרה", subject_object="מקווה טהרה"))

    assert candidates[0].linking_mode == "named_facility_geocoding"
    assert candidates[0].layer_key == "religious_services"


def test_report_markdown_contains_required_columns() -> None:
    candidates = link_protocol_row_to_gis(_row("מימון פרויקט תחבורה ציבורית באזור כיכר רמון", subject_object="כיכר רמון"))
    markdown = protocol_gis_link_report_markdown(candidates[:1])

    assert "| Artifact ID | Source Text | Signal | Linking Mode | GIS Group | Confidence | Reason / Uncertainty |" in markdown
    assert "כיכר רמון" in markdown


def test_v3_flooding_event_links_only_direct_resident_archetypes() -> None:
    links = link_topic_subject_v3_events_to_gis(
        [
            {
                "row": "ashdod:4:0",
                "profile": "test_profile",
                "event": {
                    "event_id": "topic_subject_v3_event_flooding",
                    "artifact_id": "artifact-flooding",
                    "validation_status": "accepted",
                    "source_provenance": {"source_title": "הצעה לסדר יום בנושא הצפות"},
                    "full_source_text_he": "הצפות חוזרות ברחבי העיר, רובעים ותיקים, שדרות הרצל, תשתיות ניקוז, קולטנים ופיצוי תושבים.",
                    "event_payload": {
                        "action_type_he": "הצעה לסדר יום",
                        "matter_he": "הצפות חוזרות וטיפול בתשתיות ניקוז ופיצוי תושבים",
                        "action_quote_he": "אבקש להביא את ההצעה לסדר יום",
                    },
                    "normalized_event": {
                        "event_status": "open_request",
                        "matter_candidate_he": "הצפות חוזרות וטיפול בתשתיות ניקוז ופיצוי תושבים",
                        "supporting_quote_he": "אבקש להביא את ההצעה לסדר יום",
                    },
                },
            }
        ],
        archetypes=[
            {
                "key": "flooding_damage_and_drainage",
                "question": "האם הרחוב שלי נמצא באזור שהוזכר בהצפות, ומה ידוע על ניקוז/צינורות/פיצוי?",
                "topic": "הצפות וניקוז ברחוב",
                "intent": "flooding_drainage_context",
                "layer_keys": ["drainage_water_sewer", "roads_parking_public_works", "parcels_cadaster", "neighborhoods_and_statistics"],
            },
            {
                "key": "school_future_near_child",
                "question": "מה קורה עם בתי הספר והגנים באזור של הילדים שלי?",
                "topic": "עתיד מוסדות חינוך בשכונה",
                "intent": "education_facility_context",
                "layer_keys": ["education_facilities", "neighborhoods_and_statistics"],
            },
        ],
    )

    assert links[0].event_id == "topic_subject_v3_event_flooding"
    assert links[0].event_status == "open_request"
    assert "drainage_water_sewer" in {candidate.layer_key for candidate in links[0].candidates}
    assert [match.key for match in links[0].supported_archetypes] == ["flooding_damage_and_drainage"]


def test_v3_non_spatial_event_has_no_direct_archetype() -> None:
    links = link_topic_subject_v3_events_to_gis(
        [
            {
                "row": "tel_aviv:21:4",
                "event": {
                    "event_id": "topic_subject_v3_event_deputy_mayor",
                    "artifact_id": "artifact-approval",
                    "validation_status": "accepted",
                    "full_source_text_he": "המועצה אישרה את הסרת סגן ראש העיר ובחירת סגן ראש עיר חדש.",
                    "event_payload": {"action_type_he": "אישור", "matter_he": "אישור שינוי בתפקיד סגן ראש העיר"},
                    "normalized_event": {"event_status": "approved"},
                },
            }
        ],
        archetypes=[
            {
                "key": "citywide_risk_hotspots",
                "question": "איפה בעיר חוזרות שאילתות על סיכונים?",
                "topic": "מוקדי סיכון עירוניים",
                "intent": "citywide_risk_hotspots_context",
                "layer_keys": ["emergency_security_services", "environment_sanitation", "neighborhoods_and_statistics"],
            }
        ],
    )

    assert {candidate.layer_key for candidate in links[0].candidates} >= {"municipal_boundaries", "public_buildings_assets", "neighborhoods_and_statistics"}
    assert all(candidate.candidate_query.get("is_mock") is True for candidate in links[0].candidates if candidate.linking_mode == "mock_municipality_context_completion")
    assert any(hint.scope == "mock_civic_anchor" and hint.is_mock for hint in links[0].location_hints)
    assert links[0].supported_archetypes == ()


def test_v3_candidate_payload_includes_ready_govmap_query_plan() -> None:
    links = link_topic_subject_v3_events_to_gis(
        [
            {
                "row": "ashdod:4:0",
                "event": {
                    "event_id": "topic_subject_v3_event_flooding",
                    "artifact_id": "artifact-flooding",
                    "validation_status": "accepted",
                    "full_source_text_he": "הצפות חוזרות ברחבי העיר, תשתיות ניקוז וקולטנים.",
                    "event_payload": {"matter_he": "הצפות חוזרות וטיפול בתשתיות ניקוז"},
                },
            }
        ],
        archetypes=[],
    )

    candidate_payloads = links[0].to_payload()["candidates"]
    drainage = next(candidate for candidate in candidate_payloads if candidate["layer_key"] == "drainage_water_sewer")

    assert drainage["real_gis_query"]["provider"] == "govmap"
    assert drainage["real_gis_query"]["status"] == "ready"
    assert drainage["real_gis_query"]["executable"] is True
    assert drainage["real_gis_query"]["map_layer_aliases"]
    assert drainage["real_gis_query"]["payload_templates"]


def test_v3_link_payload_preserves_temporal_metadata() -> None:
    links = link_topic_subject_v3_events_to_gis(
        [
            {
                "row": "ashdod:4:0",
                "event": {
                    "event_id": "topic_subject_v3_event_flooding",
                    "artifact_id": "artifact-flooding",
                    "validation_status": "accepted",
                    "full_source_text_he": "26.02.25 נדונה הצעה בנושא הצפות חוזרות.",
                    "event_payload": {"matter_he": "הצפות חוזרות וטיפול בתשתיות ניקוז"},
                    "primary_time": {"start": "2025-02-26", "kind": "explicit_text_date", "date_source": "explicit_text_date"},
                    "time_mentions": [{"raw_text": "26.02.25", "iso_date": "2025-02-26", "kind": "numeric_date"}],
                    "raw_date_mentions": [{"raw_text": "26.02.25", "iso_date": "2025-02-26", "kind": "numeric_date"}],
                },
            }
        ],
        archetypes=[],
    )

    payload = links[0].to_payload()

    assert payload["primary_time"]["start"] == "2025-02-26"
    assert payload["time_mentions"][0]["iso_date"] == "2025-02-26"
    assert payload["raw_date_mentions"][0]["raw_text"] == "26.02.25"


def test_v3_unknown_municipality_govmap_query_plan_is_blocked() -> None:
    links = link_topic_subject_v3_events_to_gis(
        [
            {
                "event": {
                    "artifact_id": "artifact-empty",
                    "validation_status": "accepted",
                    "event_payload": {},
                },
            }
        ],
        archetypes=[],
    )

    candidate_payloads = links[0].to_payload()["candidates"]

    assert links[0].municipality_slug == "mock_unknown_municipality"
    assert candidate_payloads
    assert all(candidate["real_gis_query"]["status"] == "blocked" for candidate in candidate_payloads)
    assert any("missing_real_municipality" in candidate["real_gis_query"]["blocked_reasons"] for candidate in candidate_payloads)


def test_v3_duplicate_events_prefer_accepted_profile() -> None:
    base_event = {
        "event_id": "topic_subject_v3_event_duplicate",
        "artifact_id": "artifact-duplicate",
        "full_source_text_he": "המועצה דנה בנושא כללי ללא מוקד GIS.",
        "event_payload": {"action_type_he": "דיון", "matter_he": "נושא כללי"},
    }

    links = link_topic_subject_v3_events_to_gis(
        [
            {"profile": "needs_review_profile", "event": {**base_event, "validation_status": "needs_review"}},
            {"profile": "accepted_profile", "event": {**base_event, "validation_status": "accepted"}},
        ],
        archetypes=[],
    )

    assert len(links) == 1
    assert links[0].profile == "accepted_profile"
    assert links[0].validation_status == "accepted"


def test_v3_link_payload_preserves_display_matter_and_identifiers() -> None:
    links = link_topic_subject_v3_events_to_gis(
        [
            {
                "row": "ashdod:15:11",
                "event": {
                    "event_id": "topic_subject_v3_event_land_rights",
                    "artifact_id": "artifact-land-rights",
                    "validation_status": "accepted",
                    "full_source_text_he": "ויתור על חלק מזכות חכירה במגרש400 הידוע כגוש38263 חלקה20",
                    "event_payload": {
                        "action_type_he": "בקשה",
                        "matter_he": "ויתור על חלק מזכות חכירה במגרש400 הידוע כגוש38263 חלקה20",
                        "matter_display_he": "ויתור על חלק מזכות חכירה",
                        "matter_identifiers": [
                            {"type": "lot", "label_he": "מגרש", "value_he": "400", "raw_text_he": "במגרש400", "canonical_he": "מגרש 400"},
                            {"type": "block", "label_he": "גוש", "value_he": "38263", "raw_text_he": "כגוש38263", "canonical_he": "גוש 38263"},
                            {"type": "parcel", "label_he": "חלקה", "value_he": "20", "raw_text_he": "חלקה20", "canonical_he": "חלקה 20"},
                        ],
                    },
                },
            }
        ],
        archetypes=[],
    )

    payload = links[0].to_payload()

    assert payload["matter_he"] == "ויתור על חלק מזכות חכירה במגרש400 הידוע כגוש38263 חלקה20"
    assert payload["matter_display_he"] == "ויתור על חלק מזכות חכירה"
    assert payload["matter_identifiers"][1]["canonical_he"] == "גוש 38263"
    assert "גוש38263" in payload["source_text"]
