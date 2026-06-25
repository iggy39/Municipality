from __future__ import annotations

import json

import httpx
from pyproj import Transformer

from municipality.govmap_client import DEFAULT_GOVMAP_CENTER_X, DEFAULT_GOVMAP_CENTER_Y, DEFAULT_GOVMAP_LEVEL, GOVMAP_DASHBOARD_LAYERS, GOVMAP_DEFAULT_VISIBLE_LAYER_ALIASES, GovMapClient, build_govmap_dashboard_payload


def _polygon_wkt_extent_m(wkt: str) -> tuple[float, float]:
    body = wkt[wkt.find("((") + 2 : wkt.rfind("))")]
    points = [tuple(float(part) for part in raw.strip().split()[:2]) for raw in body.split(",") if raw.strip()]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return max(xs) - min(xs), max(ys) - min(ys)


def test_govmap_client_sends_authorized_origin_and_spatial_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["origin"] = request.headers.get("Origin")
        captured["referer"] = request.headers.get("Referer")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"layers": {"bus_stops": [{"id": "1", "attributes": {"stop_name": "תחנה"}}]}})

    client = GovMapClient(api_key="test-token", origin="https://horizonscanninglab.org", transport=httpx.MockTransport(handler))
    data = client.features_by_location(x=219143.61, y=618345.06, radius_m=3000, layers=(GOVMAP_DASHBOARD_LAYERS[6],))

    assert captured["url"] == "https://www.govmap.gov.il/api/spatial-analysis/layer-features-by-location"
    assert captured["origin"] == "https://horizonscanninglab.org"
    assert captured["referer"] == "https://horizonscanninglab.org/"
    assert captured["body"] == {
        "apiToken": "test-token",
        "data": {
            "geometry": "POINT(219143.61 618345.06)",
            "radius": 3000.0,
            "layers": [{"name": "bus_stops", "fields": ["objectid", "stop_name", "stop_id", "name"]}],
        },
    }
    assert data["layers"]["bus_stops"][0]["attributes"]["stop_name"] == "תחנה"


def test_build_govmap_dashboard_payload_degrades_without_live_calls(monkeypatch) -> None:
    monkeypatch.setenv("GOVMAP_DASHBOARD_LIVE", "0")

    payload = build_govmap_dashboard_payload(radius_m=3000, profile="overview")

    assert payload["provider"] == "govmap"
    assert payload["selected_example"] == "govmap_resident_point"
    assert payload["govmap"]["spatial_status"] == "degraded"
    assert payload["govmap"]["spatial_error"] == "live_govmap_disabled"
    assert payload["query"]["center_x"] == DEFAULT_GOVMAP_CENTER_X
    assert payload["query"]["center_y"] == DEFAULT_GOVMAP_CENTER_Y
    assert payload["query"]["center_source"] == "fallback_tel_aviv"
    assert {"address", "street", "settlement"} <= set(payload["query"]["address_search_datatypes"])
    assert payload["govmap"]["iframe_url"].startswith("https://www.govmap.gov.il/?c=180428.96%2C665728.35")
    assert {layer["alias"] for layer in payload["govmap"]["layer_filters"]} == {layer.alias for layer in GOVMAP_DASHBOARD_LAYERS}
    assert {layer["label_he"] for layer in payload["govmap"]["layer_filters"]} >= {"חלקות", "תחבורה ציבורית", "מקלטים"}
    assert payload["govmap"]["default_visible_layers"] == list(GOVMAP_DEFAULT_VISIBLE_LAYER_ALIASES)
    resident_groups = payload["govmap"]["resident_layer_groups"]
    assert len(resident_groups) >= 15
    assert all(group["default_visible"] is False for group in resident_groups)
    assert all(group["layers"] for group in resident_groups)
    assert all("local_layers" not in group for group in resident_groups)
    assert any(
        group["layer_key"] == "parcels_cadaster" and any(layer["alias"] == "PARCEL_ALL" and layer["kind"] == "map_layer" for layer in group["layers"])
        for group in resident_groups
    )
    parcels_cadaster = next(group for group in resident_groups if group["layer_key"] == "parcels_cadaster")
    assert {layer["alias"] for layer in parcels_cadaster["layers"]} >= {"PARCEL_ALL", "SUB_GUSH_ALL"}
    assert "parcel_all" not in {layer["alias"] for layer in parcels_cadaster["layers"]}
    assert "sub_gush_all" not in {layer["alias"] for layer in parcels_cadaster["layers"]}
    resident_location = next(group for group in resident_groups if group["layer_key"] == "resident_location")
    assert {layer["alias"] for layer in resident_location["layers"]} == {"address", "street", "settlement"}
    assert {layer["kind"] for layer in resident_location["layers"]} == {"search_datatype"}
    assert all(layer["selectable"] is False for layer in resident_location["layers"])
    public_buildings = next(group for group in resident_groups if group["layer_key"] == "public_buildings_assets")
    assert {layer["alias"] for layer in public_buildings["layers"]} == {"public_institutions_survey"}
    assert all(layer["kind"] == "map_layer" for layer in public_buildings["layers"])
    assert all(layer["selectable"] is True for layer in public_buildings["layers"])
    assert "layer_210692" not in {layer["alias"] for group in resident_groups for layer in group["layers"]}
    assert "layer_210697" not in {layer["alias"] for group in resident_groups for layer in group["layers"]}
    playgrounds = next(group for group in resident_groups if group["layer_key"] == "playgrounds_youth_space")
    assert {layer["alias"] for layer in playgrounds["layers"]} == {"teva_ironi", "teva_ironi_nek"}
    assert {layer["status"] for layer in playgrounds["layers"]} == {"partial"}
    resident_aliases = {layer["alias"] for group in resident_groups for layer in group["layers"]}
    assert {"sport", "situr_ironi", "ravkav", "mehoziot_app_taba"} <= resident_aliases
    assert {"sport", "situr_ironi", "ravkav"} <= set(payload["govmap"]["visible_layers"])
    assert "PARCEL_ALL" in payload["govmap"]["visible_layers"]
    assert "address" not in payload["govmap"]["visible_layers"]
    assert payload["govmap"]["level"] == DEFAULT_GOVMAP_LEVEL
    assert payload["query"]["selected_area_radius_m"] == 300.0
    assert payload["govmap"]["selected_area_radius_m"] == 300.0
    assert payload["govmap"]["selected_area"]["feature_type"] == "address_radius_selected_area"
    assert payload["govmap"]["selected_area"]["radius_m"] == 300.0
    assert payload["govmap"]["selected_area"]["display_wkt"].startswith("POLYGON")
    assert payload["govmap"]["selected_area"]["geometry"]["type"] == "Polygon"
    assert payload["govmap"]["address_marker"]["feature_type"] == "address_marker"
    assert payload["govmap"]["address_marker"]["display_wkt"].startswith("POINT")
    assert payload["govmap"]["address_marker"]["house_display_wkt"].startswith("POLYGON")
    house_width_m, house_height_m = _polygon_wkt_extent_m(payload["govmap"]["address_marker"]["house_display_wkt"])
    assert 8.0 <= house_width_m <= 9.0
    assert 6.0 <= house_height_m <= 6.8
    assert payload["govmap"]["address_marker"]["geometry_kind"] == "house_marker"
    assert payload["govmap"]["address_marker"]["geometry"]["type"] == "Point"
    assert "z=8" in payload["govmap"]["iframe_url"]
    assert payload["visual_context"]["mode"] == "govmap_native"
    assert payload["basemap"]["display_status"] == "official"
    assert payload["basemap"]["tile_url"] == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    assert payload["layers"]["address_points"]["status"] == "degraded"


def test_build_govmap_dashboard_payload_resolves_municipality_center(monkeypatch) -> None:
    monkeypatch.setenv("GOVMAP_DASHBOARD_LIVE", "1")
    captured_spatial_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if str(request.url).endswith("/api/search-service/api-search"):
            if body.get("searchText") == "ashdod":
                return httpx.Response(200, json={"results": [{"type": "settlement", "centroid": "POINT (167655.36 635701.63)", "originalText": "אשדוד"}]})
            return httpx.Response(200, json={"results": [], "resultsCount": 0})
        captured_spatial_body.update(body)
        return httpx.Response(200, json={"layers": {}})

    client = GovMapClient(api_key="test-token", origin="https://horizonscanninglab.org", transport=httpx.MockTransport(handler))

    payload = build_govmap_dashboard_payload(radius_m=3000, profile="overview", municipality="ashdod", client=client)

    assert payload["query"]["center_x"] == 167655.36
    assert payload["query"]["center_y"] == 635701.63
    assert payload["query"]["center_source"] == "municipality_search"
    assert captured_spatial_body["data"]["geometry"] == "POINT(167655.36 635701.63)"


def test_address_focus_does_not_recenter_to_selected_focus_feature(monkeypatch) -> None:
    monkeypatch.setenv("GOVMAP_DASHBOARD_LIVE", "1")
    address_x = 180000.0
    address_y = 660000.0
    focus_x = 181500.0
    focus_y = 661500.0
    focus_mercator_x, focus_mercator_y = Transformer.from_crs("EPSG:2039", "EPSG:3857", always_xy=True).transform(focus_x, focus_y)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if str(request.url).endswith("/api/search-service/api-search"):
            return httpx.Response(200, json={"results": [{"type": "address", "centroid": f"POINT ({address_x} {address_y})", "originalText": "דיזנגוף 99"}]})
        if str(request.url).endswith("/api/spatial-analysis/select-feature-on-map"):
            layer_name = body.get("layer", {}).get("layerName")
            if layer_name == "mikve":
                return httpx.Response(200, json=[{"objectid": 479, "wkt": f"POINT ({focus_mercator_x} {focus_mercator_y})"}])
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"layers": {}})

    client = GovMapClient(api_key="test-token", origin="https://horizonscanninglab.org", transport=httpx.MockTransport(handler))

    payload = build_govmap_dashboard_payload(radius_m=3000, profile="question", address="דיזנגוף 99 תל אביב", focus_layer="mikve", client=client)

    assert payload["query"]["center_source"] == "address_search"
    assert payload["query"]["center_x"] == address_x
    assert payload["query"]["center_y"] == address_y
    assert payload["govmap"]["center"]["x"] == address_x
    assert payload["govmap"]["center"]["y"] == address_y
    assert abs(payload["govmap"]["selected_focus_feature"]["govmap_center"]["x"] - focus_x) < 0.01
    assert abs(payload["govmap"]["selected_focus_feature"]["govmap_center"]["y"] - focus_y) < 0.01
    assert payload["govmap"]["level"] == 10
    assert "z=10" in payload["govmap"]["iframe_url"]


def test_initial_govmap_payload_is_metadata_only(monkeypatch) -> None:
    monkeypatch.setenv("GOVMAP_DASHBOARD_LIVE", "1")
    captured_spatial_layers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if str(request.url).endswith("/api/search-service/api-search"):
            return httpx.Response(200, json={"results": [], "resultsCount": 0})
        if str(request.url).endswith("/api/spatial-analysis/select-feature-on-map"):
            return httpx.Response(200, json=[])
        captured_spatial_layers.extend(layer["name"] for layer in body["data"]["layers"])
        return httpx.Response(200, json={"layers": {}})

    client = GovMapClient(api_key="test-token", origin="https://horizonscanninglab.org", transport=httpx.MockTransport(handler))

    payload = build_govmap_dashboard_payload(radius_m=3000, profile="initial", client=client)

    assert captured_spatial_layers == []
    assert payload["govmap"]["spatial_error"] == "initial_metadata_only"
    assert payload["layers"]["address_points"]["error"] == "initial_metadata_only"
    assert payload["govmap"]["default_visible_layers"] == list(GOVMAP_DEFAULT_VISIBLE_LAYER_ALIASES)
    assert {layer["alias"] for layer in payload["govmap"]["layer_filters"]} == {layer.alias for layer in GOVMAP_DASHBOARD_LAYERS}
    assert all(group["default_visible"] is False for group in payload["govmap"]["resident_layer_groups"])
    assert all(layer["default_visible"] is False for group in payload["govmap"]["resident_layer_groups"] for layer in group["layers"])


def test_selected_govmap_area_is_address_circle_and_neighborhood_is_boundary(monkeypatch) -> None:
    monkeypatch.setenv("GOVMAP_DASHBOARD_LIVE", "1")
    selected_wkt = "MULTIPOLYGON Z (((3871846.291 3772177.428 0,3871853.642 3772180.344 0,3871852.561 3772177.648 0,3871846.291 3772177.428 0)))"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/api/search-service/api-search"):
            return httpx.Response(200, json={"results": [], "resultsCount": 0})
        if str(request.url).endswith("/api/spatial-analysis/select-feature-on-map"):
            return httpx.Response(200, json=[{"objectid": 3279, "wkt": selected_wkt}])
        return httpx.Response(200, json={"layers": {}})

    client = GovMapClient(api_key="test-token", origin="https://horizonscanninglab.org", transport=httpx.MockTransport(handler))

    payload = build_govmap_dashboard_payload(radius_m=3000, profile="initial", client=client)

    selected_area = payload["govmap"]["selected_area"]
    assert selected_area["feature_type"] == "address_radius_selected_area"
    assert selected_area["source_id"] == "govmap:address_radius"
    assert selected_area["radius_m"] == 300.0
    assert selected_area["display_srid"] == "2039"
    assert selected_area["display_wkt"].startswith("POLYGON")
    assert selected_area["geometry"]["type"] == "Polygon"
    assert payload["parcel"] == selected_area
    assert payload["govmap"]["address_marker"]["display_wkt"].startswith("POINT")
    assert payload["govmap"]["address_marker"]["house_display_wkt"].startswith("POLYGON")

    boundary = payload["govmap"]["neighborhood_boundary"]
    assert boundary["wkt"] == selected_wkt
    assert boundary["display_srid"] == "2039"
    assert boundary["display_wkt"].startswith("MULTIPOLYGON")
    assert "3871846" not in boundary["display_wkt"]
    assert " Z " not in boundary["display_wkt"]
