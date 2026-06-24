from __future__ import annotations

from municipality.protocol_gis_story_builder import build_gis_connection_summary, build_protocol_gis_stories


def _candidate(layer_key: str, *, name: str = "שכבה", status: str = "ready") -> dict[str, object]:
    return {
        "layer_key": layer_key,
        "layer_display_name_he": name,
        "linking_mode": "subject_event_layer_inference",
        "real_gis_query": {"status": status, "query_kind": "geocode_then_spatial_layer_query", "map_layer_aliases": [f"alias_{layer_key}"]},
    }


def _link(
    *,
    artifact_id: str,
    event_id: str,
    municipality_slug: str = "ashdod",
    status: str,
    matter: str,
    text: str,
    layer_key: str = "education_facilities",
    validation_status: str = "accepted",
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "artifact_id": artifact_id,
        "municipality_slug": municipality_slug,
        "source_row": f"{municipality_slug}:1",
        "validation_status": validation_status,
        "event_status": status,
        "action_type_he": "אישור" if status == "approved" else "בקשה",
        "matter_he": matter,
        "outcome_he": "approved" if status == "approved" else "",
        "source_text": text,
        "evidence_quotes": [text[:80]],
        "source_artifact_file": f"/tmp/{artifact_id}/v3_events.json",
        "candidates": [_candidate(layer_key, name="בתי ספר וגני ילדים")],
        "supported_archetypes": [],
        "location_hints": [{"label_he": "אשדוד", "is_mock": False}],
        "source_provenance": {},
    }


def test_story_builder_groups_related_events_and_marks_green_progress() -> None:
    payload = {
        "links": [
            _link(
                artifact_id="protocol-a",
                event_id="event-a",
                status="open_request",
                matter="בקשה להקמת גן ילדים חדש בשכונת הדרים",
                text="01.01.2024 בקשה להקמת גן ילדים חדש בשכונת הדרים",
            ),
            _link(
                artifact_id="protocol-b",
                event_id="event-b",
                status="approved",
                matter="אישור הקמת גן ילדים חדש בשכונת הדרים",
                text="01.03.2024 אישור הקמת גן ילדים חדש בשכונת הדרים",
            ),
        ]
    }

    report = build_protocol_gis_stories(payload)

    assert report["story_count"] == 1
    story = report["stories"][0]
    assert story["traffic_light"] == "green"
    assert story["progression"] == "advanced"
    assert story["event_count"] == 2
    assert [event["event_status"] for event in story["timeline_events"]] == ["open_request", "approved"]
    assert story["human_judgement"]["judgement"] == "strong_story"


def test_story_builder_splits_unrelated_subjects_under_same_municipality() -> None:
    payload = {
        "links": [
            _link(
                artifact_id="protocol-education",
                event_id="event-education",
                status="approved",
                matter="אישור הקמת גן ילדים חדש",
                text="01.03.2024 אישור הקמת גן ילדים חדש",
                layer_key="education_facilities",
            ),
            _link(
                artifact_id="protocol-welfare",
                event_id="event-welfare",
                status="approved",
                matter="אישור סיוע לקשישים במרכז יום",
                text="01.03.2024 אישור סיוע לקשישים במרכז יום",
                layer_key="welfare_health_services",
            ),
        ]
    }

    report = build_protocol_gis_stories(payload)

    assert report["story_count"] == 2
    assert {story["topic_family"] for story in report["stories"]} == {"layer:education_facilities", "layer:welfare_health_services"}


def test_story_builder_marks_mock_dates_for_missing_protocol_dates() -> None:
    payload = {
        "links": [
            _link(
                artifact_id="protocol-a",
                event_id="event-a",
                status="open_request",
                matter="בקשה לטיפול במדרכות ברחוב הרצל",
                text="בקשה לטיפול במדרכות ברחוב הרצל",
                layer_key="roads_parking_public_works",
            ),
            _link(
                artifact_id="protocol-b",
                event_id="event-b",
                status="discussed",
                matter="דיון בטיפול במדרכות ברחוב הרצל",
                text="דיון בטיפול במדרכות ברחוב הרצל",
                layer_key="roads_parking_public_works",
            ),
        ]
    }

    story = build_protocol_gis_stories(payload)["stories"][0]

    assert story["traffic_light"] == "yellow"
    assert story["has_mock_completion"] is True
    assert all(event["date_is_mock"] for event in story["timeline_events"])
    assert any("timeline_events" in field for field in story["mock_fields"])


def test_story_builder_uses_v3_primary_time_without_mock_date() -> None:
    payload = {
        "links": [
            {
                **_link(
                    artifact_id="protocol-a",
                    event_id="event-a",
                    status="open_request",
                    matter="בקשה לטיפול במדרכות ברחוב הרצל",
                    text="בקשה לטיפול במדרכות ברחוב הרצל",
                    layer_key="roads_parking_public_works",
                ),
                "primary_time": {
                    "start": "2025-12-29",
                    "kind": "protocol_date",
                    "date_source": "protocol_date_context",
                },
            }
        ]
    }

    story = build_protocol_gis_stories(payload)["stories"][0]
    event = story["timeline_events"][0]

    assert event["date"] == "2025-12-29"
    assert event["date_is_mock"] is False
    assert event["date_source"] == "protocol_date_context"
    assert event["mock_fields"] == []
    assert event["inferred_fields"] == ["date"]


def test_story_builder_parses_compact_protocol_date_from_source_paths() -> None:
    payload = {
        "links": [
            {
                **_link(
                    artifact_id="protocol-a",
                    event_id="event-a",
                    status="discussed",
                    matter="דיון בטיפול במדרכות ברחוב הרצל",
                    text="דיון בטיפול במדרכות ברחוב הרצל",
                    layer_key="roads_parking_public_works",
                ),
                "source_provenance": {
                    "source_paths": {
                        "protocol_run_dir": "/tmp/protocol_31_20251229_2cdaf368_v1",
                    }
                },
            }
        ]
    }

    event = build_protocol_gis_stories(payload)["stories"][0]["timeline_events"][0]

    assert event["date"] == "2025-12-29"
    assert event["date_is_mock"] is False


def test_story_builder_excludes_failed_and_unknown_empty_rows() -> None:
    payload = {
        "links": [
            _link(
                artifact_id="protocol-good",
                event_id="event-good",
                status="approved",
                matter="אישור הקמת פארק חדש",
                text="01.03.2024 אישור הקמת פארק חדש",
                layer_key="playgrounds_youth_space",
            ),
            _link(
                artifact_id="protocol-failed",
                event_id="event-failed",
                status="approved",
                matter="אישור הקמת פארק חדש",
                text="01.03.2024 אישור הקמת פארק חדש",
                validation_status="failed",
            ),
            _link(
                artifact_id="protocol-empty",
                event_id="event-empty",
                municipality_slug="mock_unknown_municipality",
                status="unknown",
                matter="",
                text="",
            ),
        ]
    }

    report = build_protocol_gis_stories(payload)

    assert report["source"]["unique_event_count"] == 1
    assert report["source"]["excluded_by_reason"] == {"failed_validation": 1, "missing_real_municipality": 1}
    assert report["story_count"] == 1


def test_story_builder_dedupes_same_artifact_status_and_matter() -> None:
    first = _link(
        artifact_id="protocol-a",
        event_id="event-a1",
        status="open_request",
        matter="בקשה לתיקון מעלית המשכן לאומנויות הבמה",
        text="02.02.2025 בקשה לתיקון מעלית המשכן לאומנויות הבמה",
        layer_key="culture_sport_leisure",
    )
    second = {**first, "event_id": "event-a2"}

    report = build_protocol_gis_stories({"links": [first, second]})

    assert report["source"]["unique_event_count"] == 1
    assert report["stories"][0]["event_count"] == 1


def test_connection_summary_counts_ready_and_blocked_queries() -> None:
    payload = {
        "links": [
            {
                **_link(
                    artifact_id="protocol-a",
                    event_id="event-a",
                    status="approved",
                    matter="אישור הקמת גן ילדים חדש",
                    text="01.03.2024 אישור הקמת גן ילדים חדש",
                ),
                "candidates": [_candidate("education_facilities"), _candidate("municipal_boundaries", status="blocked")],
            }
        ]
    }

    summary = build_gis_connection_summary(payload)

    by_key = {row["layer_key"]: row for row in summary["layers"]}
    assert by_key["education_facilities"]["ready_govmap_query_count"] == 1
    assert by_key["municipal_boundaries"]["blocked_govmap_query_count"] == 1
