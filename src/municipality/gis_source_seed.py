from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

DEFAULT_SEED_PATH = Path("config/gis/source_registry.seed.yaml")
FORBIDDEN_OFFICIAL_PROVIDERS = {"osm", "overture", "google"}
FORBIDDEN_OFFICIAL_SOURCE_KINDS = {"osm", "overture", "google", "user_selected", "community"}
VALID_PROVENANCE_LEVELS = {
    "official_national",
    "official_municipal",
    "municipal",
    "context",
    "community",
    "commercial",
    "user_selected",
    "unknown",
}
VALID_REUSE_STATUSES = {
    "verified",
    "legal_approved",
    "municipal_license_under_review",
    "restricted",
    "unknown",
    "not_reusable",
}
VALID_GEOMETRY_STATUSES = {
    "official_geometry",
    "derived_geometry",
    "context_geometry",
    "schematic_only",
    "none",
    "unknown",
}
VALID_DISPLAY_STATUSES = {
    "official",
    "official_with_caveat",
    "municipal_open",
    "municipal_license_under_review",
    "context_only",
    "temporary_input_only",
    "unavailable",
}
VALID_COVERAGE_STATUSES = {
    "national_available",
    "municipal_open_available",
    "municipal_license_under_review",
    "context_available",
    "not_found",
    "not_enabled",
    "not_ingested",
}


class SourceSeedValidationError(ValueError):
    pass


def load_seed_file(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SourceSeedValidationError("seed file must contain a mapping")
    return payload


def validate_seed_payload(payload: dict[str, Any]) -> None:
    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        raise SourceSeedValidationError("sources must be a list")

    seen_source_ids: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise SourceSeedValidationError(f"sources[{index}] must be a mapping")
        source_id = _required_text(source, "source_id", f"sources[{index}]")
        if source_id in seen_source_ids:
            raise SourceSeedValidationError(f"duplicate source_id: {source_id}")
        seen_source_ids.add(source_id)

        provider_key = str(source.get("provider_key", "")).lower()
        source_kind = str(source.get("source_kind", "")).lower()
        _required_text(source, "provider_key", f"sources[{index}]")
        _required_text(source, "source_kind", f"sources[{index}]")
        _required_text(source, "name_he", f"sources[{index}]")
        _validate_choice(source, "provenance_level", VALID_PROVENANCE_LEVELS, f"sources[{index}]")
        _validate_choice(source, "reuse_status", VALID_REUSE_STATUSES, f"sources[{index}]")
        if "geometry_status" in source:
            _validate_choice(source, "geometry_status", VALID_GEOMETRY_STATUSES, f"sources[{index}]")
        _validate_choice(source, "display_status", VALID_DISPLAY_STATUSES, f"sources[{index}]")
        if source.get("display_as_official") is True and (
            provider_key in FORBIDDEN_OFFICIAL_PROVIDERS or source_kind in FORBIDDEN_OFFICIAL_SOURCE_KINDS
        ):
            raise SourceSeedValidationError(
                f"display_as_official=true is not allowed for provider/source kind: {source_id}"
            )

    coverage_rows = payload.get("coverage", [])
    if coverage_rows is not None and not isinstance(coverage_rows, list):
        raise SourceSeedValidationError("coverage must be a list")
    for index, row in enumerate(coverage_rows or []):
        if not isinstance(row, dict):
            raise SourceSeedValidationError(f"coverage[{index}] must be a mapping")
        _required_text(row, "municipality_code", f"coverage[{index}]")
        _required_text(row, "layer_key", f"coverage[{index}]")
        _validate_choice(row, "status", VALID_COVERAGE_STATUSES, f"coverage[{index}]")


def seed_from_file(engine: Engine, path: Path = DEFAULT_SEED_PATH) -> None:
    payload = load_seed_file(path)
    validate_seed_payload(payload)
    seed_payload(engine, payload)


def seed_payload(engine: Engine, payload: dict[str, Any]) -> None:
    with engine.begin() as connection:
        for source in payload.get("sources", []):
            connection.execute(
                text(
                    """
                    INSERT INTO source_registry (
                      source_id, provider_key, source_kind, name_he, name_en, owner_name, owner_url,
                      source_url, municipality_code, layer_keys, provenance_level, reuse_status,
                      geometry_status, display_status, source_is_official, reuse_is_verified,
                      display_as_official, legal_review_approved, enabled, license_name,
                      license_url, attribution, notes, metadata, updated_at
                    ) VALUES (
                      :source_id, :provider_key, :source_kind, :name_he, :name_en, :owner_name, :owner_url,
                      :source_url, :municipality_code, :layer_keys, :provenance_level, :reuse_status,
                      :geometry_status, :display_status, :source_is_official, :reuse_is_verified,
                      :display_as_official, :legal_review_approved, :enabled, :license_name,
                      :license_url, :attribution, :notes, CAST(:metadata AS jsonb), now()
                    )
                    ON CONFLICT (source_id) DO UPDATE SET
                      provider_key = EXCLUDED.provider_key,
                      source_kind = EXCLUDED.source_kind,
                      name_he = EXCLUDED.name_he,
                      name_en = EXCLUDED.name_en,
                      owner_name = EXCLUDED.owner_name,
                      owner_url = EXCLUDED.owner_url,
                      source_url = EXCLUDED.source_url,
                      municipality_code = EXCLUDED.municipality_code,
                      layer_keys = EXCLUDED.layer_keys,
                      provenance_level = EXCLUDED.provenance_level,
                      reuse_status = EXCLUDED.reuse_status,
                      geometry_status = EXCLUDED.geometry_status,
                      display_status = EXCLUDED.display_status,
                      source_is_official = EXCLUDED.source_is_official,
                      reuse_is_verified = EXCLUDED.reuse_is_verified,
                      display_as_official = EXCLUDED.display_as_official,
                      legal_review_approved = EXCLUDED.legal_review_approved,
                      enabled = EXCLUDED.enabled,
                      license_name = EXCLUDED.license_name,
                      license_url = EXCLUDED.license_url,
                      attribution = EXCLUDED.attribution,
                      notes = EXCLUDED.notes,
                      metadata = EXCLUDED.metadata,
                      updated_at = now()
                    """
                ),
                _source_params(source),
            )

        for row in payload.get("coverage", []):
            connection.execute(
                text(
                    """
                    INSERT INTO layer_coverage (
                      municipality_code, municipality_name_he, layer_key, status, source_id,
                      feature_count, last_successful_ingest_at, updated_at
                    ) VALUES (
                      :municipality_code, :municipality_name_he, :layer_key, :status, :source_id,
                      :feature_count, :last_successful_ingest_at, now()
                    )
                    ON CONFLICT (municipality_code, layer_key) DO UPDATE SET
                      municipality_name_he = EXCLUDED.municipality_name_he,
                      status = EXCLUDED.status,
                      source_id = EXCLUDED.source_id,
                      feature_count = EXCLUDED.feature_count,
                      last_successful_ingest_at = EXCLUDED.last_successful_ingest_at,
                      updated_at = now()
                    """
                ),
                {
                    "municipality_code": row["municipality_code"],
                    "municipality_name_he": row.get("municipality_name_he"),
                    "layer_key": row["layer_key"],
                    "status": row["status"],
                    "source_id": row.get("source_id"),
                    "feature_count": int(row.get("feature_count", 0)),
                    "last_successful_ingest_at": row.get("last_successful_ingest_at"),
                },
            )


def _source_params(source: dict[str, Any]) -> dict[str, Any]:
    layer_keys = source.get("layer_keys", [])
    if not isinstance(layer_keys, list):
        raise SourceSeedValidationError(f"layer_keys must be a list for {source.get('source_id')}")
    return {
        "source_id": source["source_id"],
        "provider_key": source["provider_key"],
        "source_kind": source["source_kind"],
        "name_he": source["name_he"],
        "name_en": source.get("name_en"),
        "owner_name": source.get("owner_name"),
        "owner_url": source.get("owner_url"),
        "source_url": source.get("source_url"),
        "municipality_code": source.get("municipality_code"),
        "layer_keys": layer_keys,
        "provenance_level": source["provenance_level"],
        "reuse_status": source["reuse_status"],
        "geometry_status": source.get("geometry_status", "unknown"),
        "display_status": source["display_status"],
        "source_is_official": bool(source.get("source_is_official", False)),
        "reuse_is_verified": bool(source.get("reuse_is_verified", False)),
        "display_as_official": bool(source.get("display_as_official", False)),
        "legal_review_approved": bool(source.get("legal_review_approved", False)),
        "enabled": bool(source.get("enabled", True)),
        "license_name": source.get("license_name"),
        "license_url": source.get("license_url"),
        "attribution": source.get("attribution"),
        "notes": source.get("notes"),
        "metadata": json.dumps(source.get("metadata", {}), ensure_ascii=False),
    }


def _required_text(mapping: dict[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SourceSeedValidationError(f"{label}.{key} is required")
    return value


def _validate_choice(mapping: dict[str, Any], key: str, allowed: set[str], label: str) -> str:
    value = _required_text(mapping, key, label)
    if value not in allowed:
        raise SourceSeedValidationError(f"{label}.{key} has invalid value: {value}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed GIS source registry and coverage rows.")
    parser.add_argument("seed_path", nargs="?", default=os.getenv("SOURCE_REGISTRY_SEED", str(DEFAULT_SEED_PATH)))
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    engine = create_engine(database_url, future=True)
    seed_from_file(engine, Path(args.seed_path))


if __name__ == "__main__":
    main()
