from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.orm import Session


class MissingProvenanceError(ValueError):
    pass


def build_source_fragment(session: Session, source_id: str) -> dict[str, Any]:
    row = session.execute(
        text(
            """
            SELECT source_id, name_he, name_en, provider_key, provenance_level, reuse_status,
                   display_status, source_is_official, reuse_is_verified, display_as_official,
                   attribution, license_name, license_url
            FROM source_registry
            WHERE source_id = :source_id
            """
        ),
        {"source_id": source_id},
    ).mappings().first()
    if row is None:
        raise MissingProvenanceError(f"source not found for feature: {source_id}")
    return source_fragment_from_row(row)


def source_fragment_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_id": row["source_id"],
        "name_he": row.get("source_name_he") or row.get("name_he"),
        "name_en": row.get("source_name_en") or row.get("name_en"),
        "provider_key": row.get("provider_key"),
        "provenance_level": _string_value(row.get("provenance_level")),
        "reuse_status": _string_value(row.get("reuse_status")),
        "display_status": _string_value(row.get("display_status")),
        "source_is_official": bool(row.get("source_is_official")),
        "reuse_is_verified": bool(row.get("reuse_is_verified")),
        "display_as_official": bool(row.get("display_as_official")),
        "attribution": row.get("attribution"),
        "license_name": row.get("license_name"),
        "license_url": row.get("license_url"),
    }


def build_feature_response(row: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    if not row.get("source_id"):
        raise MissingProvenanceError("feature response requires source_id")
    if not row.get("provenance_id"):
        raise MissingProvenanceError("feature response requires provenance_id")
    if not source.get("source_id"):
        raise MissingProvenanceError("feature response requires source fragment")
    return {
        "id": str(row.get("id")),
        "source_id": row.get("source_id"),
        "provenance_id": str(row.get("provenance_id")),
        "source": dict(source),
    }


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
