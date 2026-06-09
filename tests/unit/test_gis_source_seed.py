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

    assert {"4000", "9000", "3000", "0070", "5000", "8310"} <= municipality_codes
    assert {"not_found", "not_ingested", "not_enabled"} <= statuses


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
