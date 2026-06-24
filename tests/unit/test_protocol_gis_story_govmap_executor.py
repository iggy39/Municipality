from __future__ import annotations

import json

import httpx

from municipality.govmap_client import GovMapClient
from municipality.protocol_gis_story_govmap_executor import StoryGovMapExecutionOptions, execute_story_govmap_queries


def _story_report() -> dict:
    return {
        "stories": [
            {
                "story_id": "story_1",
                "title_he": "סיפור בדיקה",
                "municipality_slug": "ashdod",
                "traffic_light": "yellow",
                "timeline_events": [{"id": "event_1", "event_id": "event_1", "artifact_id": "artifact_1"}],
            }
        ]
    }


def _link_report() -> dict:
    return {
        "links": [
            {
                "event_id": "event_1",
                "artifact_id": "artifact_1",
                "candidates": [
                    {
                        "layer_key": "public_buildings_assets",
                        "layer_display_name_he": "מבני ציבור ונכסים עירוניים",
                        "confidence_label": "medium",
                        "real_gis_query": {
                            "provider": "govmap",
                            "status": "ready",
                            "executable": True,
                            "query_kind": "geocode_then_spatial_layer_query",
                            "blocked_reasons": [],
                            "search_text": "אשדוד",
                            "alternate_search_texts": ["ashdod"],
                            "search_datatypes": ["settlement"],
                            "map_layer_aliases": ["public_institutions_survey"],
                        },
                    }
                ],
            }
        ]
    }


def test_story_govmap_executor_dry_run_keeps_artifact_only_plan() -> None:
    report = execute_story_govmap_queries(story_report=_story_report(), link_report=_link_report(), options=StoryGovMapExecutionOptions(live=False))

    assert report["source"]["live_execution"] is False
    assert report["status_counts"] == {"not_executed_live_disabled": 1}
    query = report["stories"][0]["queries"][0]
    assert query["layer_key"] == "public_buildings_assets"
    assert query["map_layer_aliases"] == ["public_institutions_survey"]
    assert query["features"] == []


def test_story_govmap_executor_runs_mocked_live_search_and_spatial_lookup() -> None:
    captured_spatial_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if str(request.url).endswith("/api/search-service/api-search"):
            assert body["searchText"] == "אשדוד"
            return httpx.Response(200, json={"results": [{"type": "settlement", "centroid": "POINT (167655.36 635701.63)", "originalText": "אשדוד"}], "resultsCount": 1})
        if str(request.url).endswith("/api/spatial-analysis/layer-features-by-location"):
            captured_spatial_body.update(body)
            return httpx.Response(200, json={"layers": {"public_institutions_survey": [{"id": "11", "attributes": {"name": "מבנה ציבור"}}]}})
        raise AssertionError(str(request.url))

    client = GovMapClient(api_key="test-token", origin="https://horizonscanninglab.org", transport=httpx.MockTransport(handler))
    report = execute_story_govmap_queries(story_report=_story_report(), link_report=_link_report(), options=StoryGovMapExecutionOptions(live=True), client=client)

    query = report["stories"][0]["queries"][0]
    assert report["status_counts"] == {"loaded_real_geometry": 1}
    assert query["center"] == {"x": 167655.36, "y": 635701.63, "source_text": "אשדוד", "search_scope": "typed", "provider": "govmap_search", "match_quality": "primary_typed_geocode"}
    assert query["geocode_match_quality"] == "primary_typed_geocode"
    assert query["feature_count"] == 1
    assert query["features"][0]["attributes"] == {"name": "מבנה ציבור"}
    assert captured_spatial_body["data"]["geometry"] == "POINT(167655.36 635701.63)"
    assert captured_spatial_body["data"]["layers"][0]["name"] == "public_institutions_survey"


def test_story_govmap_executor_uses_osm_before_city_level_fallback() -> None:
    captured_spatial_body: dict[str, object] = {}

    def govmap_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if str(request.url).endswith("/api/search-service/api-search"):
            if body["searchText"] == "ashdod":
                return httpx.Response(200, json={"results": [{"type": "settlement", "centroid": "POINT (167655.36 635701.63)", "originalText": "אשדוד"}], "resultsCount": 1})
            return httpx.Response(200, json={"results": [], "resultsCount": 0})
        if str(request.url).endswith("/api/spatial-analysis/layer-features-by-location"):
            captured_spatial_body.update(body)
            return httpx.Response(200, json={"layers": {"public_institutions_survey": [{"id": "11", "attributes": {"name": "מבנה ציבור"}}]}})
        raise AssertionError(str(request.url))

    def osm_handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://nominatim.openstreetmap.org/search")
        return httpx.Response(200, json=[{"lat": "31.797", "lon": "34.646", "display_name": "המשכן לאמנויות הבמה, אשדוד"}])

    client = GovMapClient(api_key="test-token", origin="https://horizonscanninglab.org", transport=httpx.MockTransport(govmap_handler))
    link_report = _link_report()
    query_plan = link_report["links"][0]["candidates"][0]["real_gis_query"]
    query_plan["search_text"] = "המשכן לאומנויות הבמה אשדוד"
    query_plan["alternate_search_texts"] = ["המשכן לאומנויות הבמה ashdod", "ashdod"]
    report = execute_story_govmap_queries(
        story_report=_story_report(),
        link_report=link_report,
        options=StoryGovMapExecutionOptions(live=True, osm_transport=httpx.MockTransport(osm_handler)),
        client=client,
    )

    query = report["stories"][0]["queries"][0]
    assert query["status"] == "loaded_real_geometry"
    assert query["geocode_provider"] == "osm_nominatim"
    assert query["geocode_match_quality"] == "external_open_data_candidate"
    assert query["center"]["manual_verification_required"] is True
    assert captured_spatial_body["data"]["geometry"].startswith("POINT(")
