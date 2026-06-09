from __future__ import annotations

from municipality.gis_importers import (
    GOVMAP_GET_SHAPE_URL,
    GOVMAP_PARCEL_AUTOCOMPLETE_URL,
    _geojson_polygon_from_wkt,
    _mot_bus_stop_values,
    _school_lon_lat,
    discover_ckan_csv_resource,
    fetch_arcgis_feature_pages,
    fetch_govmap_public_parcel,
    normalize_municipality_code,
    resolve_parcel_fields,
)


def test_normalize_municipality_code_preserves_four_digit_codes() -> None:
    assert normalize_municipality_code("70") == "0070"
    assert normalize_municipality_code("5000") == "5000"


def test_arcgis_pagination_uses_offsets_until_transfer_limit_clears() -> None:
    calls: list[dict[str, object]] = []

    def fetch_json(url: str, params: dict[str, object]) -> dict[str, object]:
        calls.append({"url": url, **params})
        offset = int(params["resultOffset"])
        if offset == 0:
            return {"type": "FeatureCollection", "features": [{"id": 1}], "exceededTransferLimit": True}
        return {"type": "FeatureCollection", "features": [{"id": 2}], "exceededTransferLimit": False}

    pages = fetch_arcgis_feature_pages(service_url="https://example.test/arcgis", fetch_json=fetch_json, page_size=1)

    assert len(pages) == 2
    assert calls[0]["resultOffset"] == 0
    assert calls[1]["resultOffset"] == 1
    assert calls[0]["f"] == "geojson"


def test_arcgis_pagination_accepts_source_where_clause() -> None:
    calls: list[dict[str, object]] = []

    def fetch_json(url: str, params: dict[str, object]) -> dict[str, object]:
        calls.append(params)
        return {"type": "FeatureCollection", "features": []}

    fetch_arcgis_feature_pages(service_url="https://example.test/arcgis", fetch_json=fetch_json, where="pl_number='101-0057273'")

    assert calls[0]["where"] == "pl_number='101-0057273'"


def test_resolve_parcel_fields_handles_known_variants() -> None:
    mapping = resolve_parcel_fields(["OBJECTID", "GUSH_NUM", "PARCEL_NUM"])

    assert mapping["source_object_id"] == "OBJECTID"
    assert mapping["gush"] == "GUSH_NUM"
    assert mapping["helka"] == "PARCEL_NUM"


def test_discover_ckan_csv_resource_prefers_csv_url() -> None:
    resource = discover_ckan_csv_resource(
        {
            "result": {
                "resources": [
                    {"format": "JSON", "url": "https://example.test/data.json"},
                    {"format": "CSV", "url": "https://example.test/data.csv", "name": "schools"},
                ]
            }
        }
    )

    assert resource["url"] == "https://example.test/data.csv"


def test_school_lon_lat_handles_actual_moe_utm_fields_as_wgs84() -> None:
    lon_lat = _school_lon_lat(
        {
            "SEMEL_MOSAD": "1362",
            "SHEM_MOSAD": "גן שלוה",
            "ITM_X": "209506",
            "ITM_Y": "739146",
            "UTM_X": "35.0975647218044",
            "UTM_Y": "32.7464934060436",
        }
    )

    assert lon_lat == (35.0975647218044, 32.7464934060436)


def test_mot_bus_stop_values_handles_actual_bus_stop_fields() -> None:
    stop = _mot_bus_stop_values(
        {
            "StationId": 2228,
            "CityCode": 472,
            "CityName": "אבו גוש",
            "Lat": 31.806622,
            "Long": 35.114168,
        }
    )

    assert stop == ("2228", 35.114168, 31.806622)


def test_fetch_govmap_public_parcel_resolves_exact_result_and_shape() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def post_json(url: str, payload: dict[str, object]) -> object:
        calls.append((url, payload))
        if url == GOVMAP_PARCEL_AUTOCOMPLETE_URL:
            return {
                "results": [
                    {"id": "parcel|LAYER_PARCEL_ALL|568901", "text": "גוש 5103 חלקה 43", "type": "parcel"},
                    {"id": "parcel|LAYER_PARCEL_ALL|1063604", "text": "גוש 7103 חלקה 43", "type": "parcel"},
                ]
            }
        assert url == GOVMAP_GET_SHAPE_URL
        assert payload == {"idType": "parcel", "id": "1063604"}
        return "MULTIPOLYGON(((0 0,1 0,1 1,0 0)))"

    parcel = fetch_govmap_public_parcel(gush="07103", helka="43", post_json=post_json)

    assert parcel is not None
    assert parcel["gush"] == "7103"
    assert parcel["helka"] == "43"
    assert parcel["source_object_id"] == "1063604"
    assert calls[0][0] == GOVMAP_PARCEL_AUTOCOMPLETE_URL
    assert calls[1][0] == GOVMAP_GET_SHAPE_URL


def test_geojson_polygon_from_wkt_handles_govmap_multipolygon() -> None:
    geometry = _geojson_polygon_from_wkt("MULTIPOLYGON(((3871846.291 3772177.428,3871853.642 3772180.344,3871852.561 3772177.648,3871846.291 3772177.428)))")

    assert geometry["type"] == "MultiPolygon"
    assert geometry["coordinates"][0][0][0] == [3871846.291, 3772177.428]
