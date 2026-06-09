from __future__ import annotations

import json
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from municipality.gis_provenance import source_fragment_from_row


SOURCE_COLUMNS = """
sr.source_id AS source_id,
sr.name_he,
sr.name_en,
sr.provider_key,
sr.provenance_level,
sr.reuse_status,
sr.display_status,
sr.source_is_official,
sr.reuse_is_verified,
sr.display_as_official,
sr.attribution,
sr.license_name,
sr.license_url
"""


def build_dashboard_gis_map_payload(
    session: Session,
    *,
    gush: str = "7103",
    helka: str = "43",
    radius_m: float = 750.0,
    poi_limit: int = 30,
) -> dict[str, Any]:
    try:
        parcel = _parcel_row(session, gush=gush, helka=helka)
        if parcel is None:
            return _not_found_payload(gush=gush, helka=helka, radius_m=radius_m)
        center = _center_from_geometry(parcel.get("centroid") or parcel.get("geometry"))
        if center is None:
            return _unavailable_payload(gush=gush, helka=helka, radius_m=radius_m, reason="parcel_centroid_unavailable")
        pois = _nearby_poi_rows(session, lon=center["lon"], lat=center["lat"], radius_m=radius_m, limit=poi_limit)
    except SQLAlchemyError as exc:
        return _unavailable_payload(gush=gush, helka=helka, radius_m=radius_m, reason=exc.__class__.__name__)

    parcel_feature = _parcel_feature(parcel)
    poi_features = [_poi_feature(row) for row in pois]
    return {
        "status": "found",
        "real_gis_available": True,
        "query": {"gush": str(gush), "helka": str(helka), "radius_m": float(radius_m)},
        "title_he": f"מפת GIS לדוגמה: גוש {parcel_feature['gush']} חלקה {parcel_feature['helka']}",
        "center": center,
        "zoom": 17,
        "basemap": {
            "provider": "OpenStreetMap",
            "display_status": "context_only",
            "attribution": "OpenStreetMap contributors",
            "tile_url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        },
        "parcel": parcel_feature,
        "nearby_pois": {"count": len(poi_features), "items": poi_features},
        "legend": [
            {"id": "parcel", "label": "חלקה מאומתת", "kind": "polygon", "color": "#14532d"},
            {"id": "school", "label": "מוסדות חינוך", "kind": "point", "color": "#f59e0b"},
            {"id": "transport_stop", "label": "תחנות תחבורה", "kind": "point", "color": "#0b68d1"},
        ],
        "caveats": [
            "רקע המפה הוא OpenStreetMap ומשמש שכבת הקשר בלבד.",
            "החלקה נטענה דרך fallback ציבורי של GovMap משום שקובץ MAPI חסום בסביבת ה-POC.",
        ],
    }


def _parcel_row(session: Session, *, gush: str, helka: str) -> Mapping[str, Any] | None:
    row = session.execute(
        text(
            f"""
            SELECT p.id,
                   p.source_id AS feature_source_id,
                   p.provenance_id,
                   p.source_object_id,
                   p.gush,
                   p.helka,
                   p.parcel_label,
                   p.validation_status,
                   ST_AsGeoJSON(p.geom) AS geometry,
                   ST_AsGeoJSON(ST_Centroid(p.geom)) AS centroid,
                   {SOURCE_COLUMNS}
            FROM parcels p
            JOIN source_registry sr ON sr.source_id = p.source_id
            WHERE p.gush = :gush AND p.helka = :helka
            ORDER BY p.fetched_at DESC
            LIMIT 1
            """
        ),
        {"gush": str(gush).strip(), "helka": str(helka).strip()},
    ).mappings().first()
    return row


def _nearby_poi_rows(session: Session, *, lon: float, lat: float, radius_m: float, limit: int) -> list[Mapping[str, Any]]:
    rows = session.execute(
        text(
            f"""
            SELECT p.id,
                   p.source_id AS feature_source_id,
                   p.provenance_id,
                   p.source_object_id,
                   p.poi_category,
                   p.name_he,
                   p.name_en,
                   p.official_identifier,
                   ST_AsGeoJSON(p.geom) AS geometry,
                   ST_Distance(
                     p.geom_2039,
                     ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)
                   ) AS distance_m,
                   {SOURCE_COLUMNS}
            FROM poi_points p
            JOIN source_registry sr ON sr.source_id = p.source_id
            WHERE ST_DWithin(
              p.geom_2039,
              ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039),
              :radius_m
            )
            ORDER BY distance_m ASC
            LIMIT :limit
            """
        ),
        {"lon": lon, "lat": lat, "radius_m": float(radius_m), "limit": int(limit)},
    ).mappings().all()
    return list(rows)


def _parcel_feature(row: Mapping[str, Any]) -> dict[str, Any]:
    geometry = _load_geojson(row.get("geometry"))
    return {
        "id": str(row.get("id")),
        "feature_type": "parcel",
        "source_id": row.get("feature_source_id"),
        "provenance_id": str(row.get("provenance_id")),
        "source": source_fragment_from_row(row),
        "source_object_id": row.get("source_object_id"),
        "gush": row.get("gush"),
        "helka": row.get("helka"),
        "label": row.get("parcel_label") or f"{row.get('gush')} / {row.get('helka')}",
        "validation_status": row.get("validation_status"),
        "geometry": geometry,
    }


def _poi_feature(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id")),
        "feature_type": "poi",
        "source_id": row.get("feature_source_id"),
        "provenance_id": str(row.get("provenance_id")),
        "source": source_fragment_from_row(row),
        "source_object_id": row.get("source_object_id"),
        "poi_category": row.get("poi_category"),
        "name_he": row.get("name_he"),
        "name_en": row.get("name_en"),
        "official_identifier": row.get("official_identifier"),
        "distance_m": _float_or_none(row.get("distance_m")),
        "geometry": _load_geojson(row.get("geometry")),
    }


def _center_from_geometry(value: Any) -> dict[str, float] | None:
    geometry = _load_geojson(value)
    if not geometry:
        return None
    if geometry.get("type") == "Point":
        coordinates = geometry.get("coordinates") or []
        if len(coordinates) >= 2:
            return {"lon": float(coordinates[0]), "lat": float(coordinates[1])}
    points = list(_iter_points(geometry.get("coordinates")))
    if not points:
        return None
    return {
        "lon": sum(point[0] for point in points) / len(points),
        "lat": sum(point[1] for point in points) / len(points),
    }


def _load_geojson(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _iter_points(coordinates: Any):
    if isinstance(coordinates, list) and len(coordinates) >= 2 and all(isinstance(value, (int, float)) for value in coordinates[:2]):
        yield float(coordinates[0]), float(coordinates[1])
        return
    if isinstance(coordinates, list):
        for item in coordinates:
            yield from _iter_points(item)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _not_found_payload(*, gush: str, helka: str, radius_m: float) -> dict[str, Any]:
    return {
        "status": "not_found",
        "real_gis_available": False,
        "query": {"gush": str(gush), "helka": str(helka), "radius_m": float(radius_m)},
        "message_he": "לא נמצאה חלקת GIS תואמת לטעינה במפה.",
    }


def _unavailable_payload(*, gush: str, helka: str, radius_m: float, reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "real_gis_available": False,
        "query": {"gush": str(gush), "helka": str(helka), "radius_m": float(radius_m)},
        "reason": reason,
        "message_he": "שכבת ה-GIS אינה זמינה כרגע; המפה הסכמטית נשארת כגיבוי.",
    }
