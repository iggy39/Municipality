from __future__ import annotations

from municipality.gis_importers import (
    discover_ckan_csv_resource,
    fetch_arcgis_feature_pages,
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
