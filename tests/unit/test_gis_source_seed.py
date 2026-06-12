from __future__ import annotations

from pathlib import Path

import pytest

from municipality.gis_source_seed import SourceSeedValidationError, load_seed_file, validate_seed_payload


def test_source_seed_rejects_duplicate_source_id() -> None:
    payload = {
        "sources": [
            _source("same"),
            _source("same"),
        ]
    }

    with pytest.raises(SourceSeedValidationError, match="duplicate source_id"):
        validate_seed_payload(payload)


def test_source_seed_rejects_invalid_status_values() -> None:
    payload = {"sources": [_source("bad", reuse_status="not-a-real-status")]}

    with pytest.raises(SourceSeedValidationError, match="reuse_status"):
        validate_seed_payload(payload)


def test_source_seed_rejects_osm_google_overture_as_official() -> None:
    for provider_key in ("osm", "google", "overture"):
        payload = {
            "sources": [
                _source(
                    f"{provider_key}_bad",
                    provider_key=provider_key,
                    source_kind=provider_key,
                    display_as_official=True,
                )
            ]
        }

        with pytest.raises(SourceSeedValidationError, match="display_as_official"):
            validate_seed_payload(payload)


def test_committed_seed_has_required_coverage_statuses_and_poc_municipalities() -> None:
    payload = load_seed_file(Path("config/gis/source_registry.seed.yaml"))
    validate_seed_payload(payload)

    coverage = payload["coverage"]
    municipality_codes = {row["municipality_code"] for row in coverage}
    statuses = {row["status"] for row in coverage}

    assert {"4000", "9000", "3000", "0070", "5000", "0831"} <= municipality_codes
    assert {"not_found", "not_ingested", "not_enabled"} <= statuses


def test_committed_osm_source_is_context_only_with_geofabrik_metadata() -> None:
    payload = load_seed_file(Path("config/gis/source_registry.seed.yaml"))
    validate_seed_payload(payload)

    osm_source = next(source for source in payload["sources"] if source["source_id"] == "osm_context")

    assert osm_source["provider_key"] == "osm"
    assert osm_source["display_status"] == "context_only"
    assert osm_source["display_as_official"] is False
    assert "geofabrik" in osm_source["source_url"].lower()
    assert osm_source["license_name"] == "ODbL 1.0"


def test_committed_seed_configures_stage_5_poc_municipal_sources() -> None:
    payload = load_seed_file(Path("config/gis/source_registry.seed.yaml"))
    validate_seed_payload(payload)

    sources = {source["source_id"]: source for source in payload["sources"]}
    required = {
        "haifa_municipal_gis_discovered": "4000",
        "beer_sheva_municipal_gis_discovered": "9000",
        "jerusalem_municipal_gis_discovered": "3000",
        "ashdod_quarter_candidate": "0070",
        "tel_aviv_open_data_discovered": "5000",
        "yeruham_municipal_gis_discovered": "0831",
    }

    for source_id, municipality_code in required.items():
        assert sources[source_id]["municipality_code"] == municipality_code
        assert sources[source_id]["display_as_official"] is False
        assert sources[source_id]["reuse_status"] == "municipal_license_under_review"
        assert sources[source_id]["metadata"]["import_config"]["municipality_code"] == municipality_code
    assert sources["tel_aviv_open_data_discovered"]["enabled"] is True
    assert sources["tel_aviv_open_data_discovered"]["display_status"] == "municipal_license_under_review"


def _source(source_id: str, **overrides: object) -> dict[str, object]:
    source: dict[str, object] = {
        "source_id": source_id,
        "provider_key": "mapi",
        "source_kind": "national_official",
        "name_he": "מקור בדיקה",
        "provenance_level": "official_national",
        "reuse_status": "verified",
        "geometry_status": "official_geometry",
        "display_status": "official",
        "display_as_official": False,
    }
    source.update(overrides)
    return source
