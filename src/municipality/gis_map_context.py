from __future__ import annotations

import json
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from municipality.gis_provenance import source_fragment_from_row


SOURCE_COLUMNS = """
sr.source_id AS source_id,
sr.name_he AS source_name_he,
sr.name_en AS source_name_en,
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

DEFAULT_RADIUS_M = 750.0
DEFAULT_LAYER_LIMIT = 5


def build_map_context(session: Session, geo_intent_resolution: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Build source-backed GIS context for a resolved resident GIS question.

    This function deliberately returns compact feature summaries, not full
    geometries. It is intended for answer/map orchestration; detail endpoints
    still own geometry expansion.
    """

    focus = _focus_payload(geo_intent_resolution)
    if not focus:
        return None

    focus_type = str(focus.get("focus_type") or "").strip()
    if focus_type in {"unresolved_place", "place"}:
        return {
            "status": "focus_unresolved" if focus_type == "unresolved_place" else "focus_needs_lookup",
            "focus": dict(focus),
            "layers": [],
            "caveats": ["יש לפתור את שם המקום או כתובת לפני טעינת שכבות GIS סביבו."],
        }

    if focus_type == "parcel":
        return _parcel_map_context(session, focus)
    if focus_type == "plan":
        return _plan_map_context(session, focus)
    if focus_type == "point":
        return _point_map_context(session, focus)
    return None


def _parcel_map_context(session: Session, focus: Mapping[str, Any]) -> dict[str, Any]:
    gush = str(focus.get("gush") or "").strip()
    helka = str(focus.get("helka") or "").strip()
    parcel = _first_row(
        session,
        """
        /* map_context:parcel_focus */
        SELECT p.id, p.source_id AS feature_source_id, p.provenance_id,
               p.gush, p.helka, p.parcel_label, p.validation_status,
               ST_X(ST_Centroid(p.geom)) AS lon,
               ST_Y(ST_Centroid(p.geom)) AS lat,
               ST_AsGeoJSON(ST_Centroid(p.geom)) AS geometry,
               {source_columns}
        FROM parcels p
        JOIN source_registry sr ON sr.source_id = p.source_id
        WHERE p.gush = :gush AND p.helka = :helka
        ORDER BY p.fetched_at DESC
        LIMIT 1
        """.format(source_columns=SOURCE_COLUMNS),
        {"gush": gush, "helka": helka},
    )
    if parcel is None:
        return {"status": "focus_not_found", "focus": dict(focus), "layers": [_empty_layer("selected_parcel", "not_found")], "caveats": ["לא נמצאה חלקה תואמת במאגר הנוכחי."]}
    lon = _float_or_none(parcel.get("lon"))
    lat = _float_or_none(parcel.get("lat"))
    return _context_for_point(
        session,
        lon=lon,
        lat=lat,
        focus=dict(focus),
        selected_layer=_layer("selected_parcel", "found", [_feature(parcel, "parcel")]),
    )


def _plan_map_context(session: Session, focus: Mapping[str, Any]) -> dict[str, Any]:
    plan_number = str(focus.get("plan_number") or "").strip()
    plan = _first_row(
        session,
        """
        /* map_context:plan_focus */
        SELECT p.id, p.source_id AS feature_source_id, p.provenance_id,
               p.plan_number, p.plan_name, p.validation_status, p.metadata,
               ST_X(ST_Centroid(p.geom)) AS lon,
               ST_Y(ST_Centroid(p.geom)) AS lat,
               ST_AsGeoJSON(ST_Centroid(p.geom)) AS geometry,
               {source_columns}
        FROM plans p
        JOIN source_registry sr ON sr.source_id = p.source_id
        WHERE p.plan_number = :plan_number
        ORDER BY p.fetched_at DESC
        LIMIT 1
        """.format(source_columns=SOURCE_COLUMNS),
        {"plan_number": plan_number},
    )
    if plan is None:
        return {"status": "focus_not_found", "focus": dict(focus), "layers": [_empty_layer("selected_plan", "not_found")], "caveats": ["לא נמצאה תכנית תואמת ב-XPLAN שנטען למערכת."]}
    lon = _float_or_none(plan.get("lon"))
    lat = _float_or_none(plan.get("lat"))
    return _context_for_point(
        session,
        lon=lon,
        lat=lat,
        focus=dict(focus),
        selected_layer=_layer("selected_plan", "found", [_feature(plan, "plan")]),
    )


def _point_map_context(session: Session, focus: Mapping[str, Any]) -> dict[str, Any]:
    lon = _float_or_none(focus.get("lon"))
    lat = _float_or_none(focus.get("lat"))
    return _context_for_point(session, lon=lon, lat=lat, focus=dict(focus), selected_layer=None)


def _context_for_point(
    session: Session,
    *,
    lon: float | None,
    lat: float | None,
    focus: dict[str, Any],
    selected_layer: dict[str, Any] | None,
) -> dict[str, Any]:
    if lon is None or lat is None:
        return {"status": "focus_geometry_unavailable", "focus": focus, "layers": [], "caveats": ["לא ניתן לחשב מרכז GIS עבור מוקד השאלה."]}

    municipality = _municipality_layer(session, lon=lon, lat=lat)
    municipality_code = _first_feature_value(municipality, "municipality_code")
    layers: list[dict[str, Any]] = []
    if selected_layer is not None:
        layers.append(selected_layer)
    layers.append(municipality)
    layers.extend(
        [
            _covering_polygon_layer(session, "plans", lon=lon, lat=lat, municipality_code=municipality_code),
            _covering_polygon_layer(session, "neighborhoods", lon=lon, lat=lat, municipality_code=municipality_code),
            _nearby_poi_layer(session, "transport_stops", lon=lon, lat=lat, categories=("transport_stop",), municipality_code=municipality_code),
            _nearby_poi_layer(session, "schools", lon=lon, lat=lat, categories=("school", "municipal_school", "kindergarten"), municipality_code=municipality_code),
            _context_geometry_layer(session, "context_roads", lon=lon, lat=lat, municipality_code=municipality_code),
            _context_geometry_layer(session, "context_landuse", lon=lon, lat=lat, municipality_code=municipality_code),
            _building_layer(session, lon=lon, lat=lat, municipality_code=municipality_code),
        ]
    )
    return {
        "status": "found",
        "focus": focus,
        "point": {"lon": lon, "lat": lat},
        "municipality_code": municipality_code,
        "layers": layers,
        "caveats": _context_caveats(layers),
    }


def _municipality_layer(session: Session, *, lon: float, lat: float) -> dict[str, Any]:
    rows = _all_rows(
        session,
        """
        /* map_context:municipality */
        SELECT b.id, b.source_id AS feature_source_id, b.provenance_id,
               b.municipality_code, b.municipality_name_he,
               b.municipality_name_he AS label, {source_columns}
        FROM official_municipal_boundaries b
        JOIN source_registry sr ON sr.source_id = b.source_id
        WHERE ST_Covers(b.geom, ST_SetSRID(ST_Point(:lon, :lat), 4326))
        ORDER BY b.fetched_at DESC
        LIMIT 1
        """.format(source_columns=SOURCE_COLUMNS),
        {"lon": lon, "lat": lat},
    )
    return _layer("municipality", "found" if rows else "not_found", [_feature(row, "municipality") for row in rows])


def _covering_polygon_layer(session: Session, layer_key: str, *, lon: float, lat: float, municipality_code: str | None) -> dict[str, Any]:
    if layer_key == "plans":
        columns = "p.plan_number, p.plan_name, p.plan_name AS label, p.validation_status"
    elif layer_key == "neighborhoods":
        columns = "p.municipality_code, p.name_he, p.name_en, p.name_he AS label, p.validation_status"
    else:
        return _empty_layer(layer_key, "not_found")
    rows = _all_rows(
        session,
        f"""
        /* map_context:{layer_key} */
        SELECT p.id, p.source_id AS feature_source_id, p.provenance_id,
               {columns}, {SOURCE_COLUMNS}
        FROM {layer_key} p
        JOIN source_registry sr ON sr.source_id = p.source_id
        WHERE ST_Covers(p.geom, ST_SetSRID(ST_Point(:lon, :lat), 4326))
        ORDER BY p.fetched_at DESC
        LIMIT :limit
        """,
        {"lon": lon, "lat": lat, "limit": DEFAULT_LAYER_LIMIT},
    )
    return _layer_with_coverage(session, layer_key, rows, feature_type=layer_key.removesuffix("s"), municipality_code=municipality_code)


def _nearby_poi_layer(session: Session, layer_key: str, *, lon: float, lat: float, categories: tuple[str, ...], municipality_code: str | None) -> dict[str, Any]:
    category_params = {f"category_{idx}": category for idx, category in enumerate(categories)}
    category_sql = ", ".join(f":category_{idx}" for idx in range(len(categories)))
    rows = _all_rows(
        session,
        f"""
        /* map_context:{layer_key} */
        SELECT p.id, p.source_id AS feature_source_id, p.provenance_id,
               p.poi_category, p.name_he, p.name_en, p.official_identifier,
               COALESCE(p.name_he, p.name_en, p.official_identifier, p.poi_category) AS label,
               p.validation_status,
               ST_Distance(p.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)) AS distance_m,
               {SOURCE_COLUMNS}
        FROM poi_points p
        JOIN source_registry sr ON sr.source_id = p.source_id
        WHERE p.poi_category IN ({category_sql})
          AND ST_DWithin(p.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039), :radius_m)
        ORDER BY distance_m ASC
        LIMIT :limit
        """,
        {"lon": lon, "lat": lat, "radius_m": DEFAULT_RADIUS_M, "limit": DEFAULT_LAYER_LIMIT, **category_params},
    )
    return _layer_with_coverage(session, layer_key, rows, feature_type="poi", municipality_code=municipality_code, coverage_layer_key="poi_points")


def _context_geometry_layer(session: Session, context_layer: str, *, lon: float, lat: float, municipality_code: str | None) -> dict[str, Any]:
    rows = _all_rows(
        session,
        """
        /* map_context:context_geometry */
        SELECT g.id, g.source_id AS feature_source_id, g.provenance_id,
               g.context_layer, g.geometry_type, g.category, g.name AS label,
               g.validation_status,
               ST_Distance(g.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)) AS distance_m,
               {source_columns}
        FROM context_geometries g
        JOIN source_registry sr ON sr.source_id = g.source_id
        WHERE g.context_layer = :context_layer
          AND ST_DWithin(g.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039), :radius_m)
        ORDER BY distance_m ASC
        LIMIT :limit
        """.format(source_columns=SOURCE_COLUMNS),
        {"context_layer": context_layer, "lon": lon, "lat": lat, "radius_m": DEFAULT_RADIUS_M, "limit": DEFAULT_LAYER_LIMIT},
    )
    return _layer_with_coverage(session, context_layer, rows, feature_type="context_geometry", municipality_code=municipality_code, coverage_layer_key="context_geometries")


def _building_layer(session: Session, *, lon: float, lat: float, municipality_code: str | None) -> dict[str, Any]:
    rows = _all_rows(
        session,
        """
        /* map_context:buildings */
        SELECT b.id, b.source_id AS feature_source_id, b.provenance_id,
               b.source_object_id AS label, b.municipality_code, b.validation_status,
               ST_Distance(ST_PointOnSurface(b.geom_2039), ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)) AS distance_m,
               {source_columns}
        FROM buildings b
        JOIN source_registry sr ON sr.source_id = b.source_id
        WHERE ST_DWithin(b.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039), :radius_m)
        ORDER BY distance_m ASC
        LIMIT :limit
        """.format(source_columns=SOURCE_COLUMNS),
        {"lon": lon, "lat": lat, "radius_m": DEFAULT_RADIUS_M, "limit": DEFAULT_LAYER_LIMIT},
    )
    return _layer_with_coverage(session, "buildings", rows, feature_type="building", municipality_code=municipality_code)


def _layer_with_coverage(
    session: Session,
    layer_key: str,
    rows: list[Mapping[str, Any]],
    *,
    feature_type: str,
    municipality_code: str | None,
    coverage_layer_key: str | None = None,
) -> dict[str, Any]:
    if rows:
        return _layer(layer_key, _status_from_rows(rows), [_feature(row, feature_type) for row in rows])
    coverage = _coverage(session, municipality_code=municipality_code, layer_key=coverage_layer_key or layer_key)
    status = str(coverage.get("status") or "not_found") if coverage else "not_found"
    out = _empty_layer(layer_key, status)
    if coverage:
        out["coverage"] = coverage
    return out


def _coverage(session: Session, *, municipality_code: str | None, layer_key: str) -> dict[str, Any] | None:
    if not municipality_code:
        return None
    row = _first_row(
        session,
        """
        /* map_context:coverage */
        SELECT lc.municipality_code, lc.municipality_name_he, lc.layer_key, lc.status,
               lc.source_id AS feature_source_id, lc.source_id, lc.feature_count,
               {source_columns}
        FROM layer_coverage lc
        LEFT JOIN source_registry sr ON sr.source_id = lc.source_id
        WHERE lc.municipality_code = :municipality_code AND lc.layer_key = :layer_key
        LIMIT 1
        """.format(source_columns=SOURCE_COLUMNS),
        {"municipality_code": municipality_code, "layer_key": layer_key},
    )
    if row is None:
        return None
    return {
        "municipality_code": row.get("municipality_code"),
        "layer_key": row.get("layer_key"),
        "status": row.get("status"),
        "source_id": row.get("source_id"),
        "feature_count": int(row.get("feature_count") or 0),
        "source": source_fragment_from_row(row) if row.get("source_id") else None,
    }


def _feature(row: Mapping[str, Any], feature_type: str) -> dict[str, Any]:
    payload = {
        "id": str(row.get("id")),
        "feature_type": feature_type,
        "source_id": row.get("feature_source_id") or row.get("source_id"),
        "provenance_id": str(row.get("provenance_id")),
        "source": source_fragment_from_row({**dict(row), "source_id": row.get("source_id") or row.get("feature_source_id")}),
        "label": row.get("label") or row.get("parcel_label") or row.get("plan_name") or row.get("plan_number") or row.get("name_he") or row.get("municipality_name_he"),
    }
    for key in ("gush", "helka", "plan_number", "plan_name", "municipality_code", "municipality_name_he", "poi_category", "distance_m", "validation_status"):
        if row.get(key) is not None:
            payload[key] = row.get(key)
    if row.get("geometry") is not None:
        payload["geometry_detail"] = "centroid"
    metadata = _normalize_metadata(row.get("metadata"))
    if feature_type == "plan" and isinstance(metadata.get("plan"), dict):
        payload["plan_metadata"] = metadata["plan"]
    return payload


def _normalize_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _layer(layer_key: str, status: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"layer_key": layer_key, "status": status, "items": items, "count": len(items)}


def _empty_layer(layer_key: str, status: str) -> dict[str, Any]:
    return {"layer_key": layer_key, "status": status, "items": [], "count": 0}


def _status_from_rows(rows: list[Mapping[str, Any]]) -> str:
    statuses = {str((row.get("display_status") or "")).strip() for row in rows}
    if statuses == {"context_only"}:
        return "context_only"
    if statuses == {"municipal_license_under_review"}:
        return "municipal_license_under_review"
    return "found"


def _context_caveats(layers: list[dict[str, Any]]) -> list[str]:
    statuses = {str(layer.get("status") or "") for layer in layers}
    caveats: list[str] = []
    if "context_only" in statuses:
        caveats.append("שכבות OSM מוצגות כהקשר בלבד ואינן מקור רשמי.")
    if "municipal_license_under_review" in statuses:
        caveats.append("שכבות עירוניות מוצגות בבדיקת רישיון ואינן מסומנות כשימוש רשמי מאושר.")
    if any(status in statuses for status in ("not_found", "not_ingested", "not_enabled")):
        caveats.append("חלק מהשכבות חסרות או טרם נטענו; אין להסיק מהיעדר שכבה שאין פעילות באזור.")
    return caveats


def _first_feature_value(layer: dict[str, Any], key: str) -> Any | None:
    items = layer.get("items") if isinstance(layer.get("items"), list) else []
    if not items:
        return None
    return items[0].get(key)


def _focus_payload(geo_intent_resolution: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(geo_intent_resolution, Mapping):
        return None
    focus = geo_intent_resolution.get("focus")
    return focus if isinstance(focus, Mapping) else None


def _first_row(session: Session, sql: str, params: Mapping[str, Any]) -> Mapping[str, Any] | None:
    try:
        return session.execute(text(sql), dict(params)).mappings().first()
    except SQLAlchemyError:
        _safe_rollback(session)
        return None


def _all_rows(session: Session, sql: str, params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    try:
        return list(session.execute(text(sql), dict(params)).mappings().all())
    except SQLAlchemyError:
        _safe_rollback(session)
        return []


def _safe_rollback(session: Session) -> None:
    rollback = getattr(session, "rollback", None)
    if callable(rollback):
        rollback()


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
