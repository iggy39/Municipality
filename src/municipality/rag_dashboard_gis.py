from __future__ import annotations

import json
import time
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


def _limit_clause(limit: int | None) -> str:
    return "" if limit is None else "LIMIT :limit"


def _limit_params(params: dict[str, Any], limit: int | None) -> dict[str, Any]:
    if limit is None:
        return params
    return {**params, "limit": int(limit)}

JERUSALEM_PLAN_NUMBER = "101-0057273"
JERUSALEM_MUNICIPALITY_CODE = "3000"
TEL_AVIV_MUNICIPALITY_CODE = "5000"
TEL_AVIV_DEMO_GUSH = "7103"
TEL_AVIV_DEMO_HELKA = "43"
GIS_PAYLOAD_CACHE_TTL_SECONDS = 600.0
_GIS_PAYLOAD_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
INITIAL_NEARBY_PARCEL_LIMIT = 50
INITIAL_POINT_LIMIT = 45
INITIAL_BUILDING_LIMIT = 15
INITIAL_CONTEXT_GEOMETRY_LIMITS = {
    "context_roads": 25,
    "context_railways": 10,
    "context_waterways": 20,
    "context_water": 20,
    "context_landuse": 20,
}
INITIAL_MUNICIPAL_CONTEXT_LIMITS = {
    "municipal_parks": 60,
    "municipal_beaches": 20,
    "municipal_bike_paths": 50,
}
OVERVIEW_NEARBY_PARCEL_LIMIT = 150
OVERVIEW_POINT_LIMIT = 80
OVERVIEW_BUILDING_LIMIT = 80
OVERVIEW_CONTEXT_GEOMETRY_LIMITS = {
    "context_roads": None,
    "context_railways": 50,
    "context_waterways": None,
    "context_water": None,
    "context_landuse": 160,
}
OVERVIEW_MUNICIPAL_CONTEXT_LIMITS = {
    "municipal_parks": 240,
    "municipal_beaches": None,
    "municipal_bike_paths": 180,
}
OSM_CONTEXT_LAYER_LIMITS = {
    "context_roads": None,
    "context_railways": None,
    "context_waterways": None,
    "context_water": None,
    "context_landuse": None,
}
MUNICIPAL_CONTEXT_LAYER_LIMITS = {
    "municipal_parks": None,
    "municipal_beaches": None,
    "municipal_bike_paths": None,
}


def build_dashboard_gis_map_payload(
    session: Session,
    *,
    gush: str = TEL_AVIV_DEMO_GUSH,
    helka: str = TEL_AVIV_DEMO_HELKA,
    radius_m: float = 3000.0,
    poi_limit: int = 14,
    example: str = "tel_aviv_parcel",
    profile: str = "initial",
) -> dict[str, Any]:
    selected_example = _normalize_example(example)
    selected_profile = _normalize_profile(profile)
    cache_key = (
        selected_example,
        selected_profile,
        str(gush).strip(),
        str(helka).strip(),
        round(float(radius_m), 3),
        int(poi_limit),
    )
    cached_payload = _cached_gis_payload(cache_key)
    if cached_payload is not None:
        return cached_payload

    if selected_example not in {"tel_aviv_parcel", "parcel"}:
        try:
            payload = _tel_aviv_parcel_payload(session, gush=gush, helka=helka, radius_m=radius_m, poi_limit=poi_limit, profile=selected_profile)
            if payload is not None:
                _store_gis_payload(cache_key, payload)
                return payload
        except SQLAlchemyError as exc:
            payload = _unavailable_payload(gush=gush, helka=helka, radius_m=radius_m, reason=exc.__class__.__name__)
            _store_gis_payload(cache_key, payload)
            return payload

    payload = _tel_aviv_parcel_payload(session, gush=gush, helka=helka, radius_m=radius_m, poi_limit=poi_limit, profile=selected_profile)
    _store_gis_payload(cache_key, payload)
    return payload


def _cached_gis_payload(cache_key: tuple[Any, ...]) -> dict[str, Any] | None:
    entry = _GIS_PAYLOAD_CACHE.get(cache_key)
    if entry is None:
        return None
    stored_at, payload = entry
    if time.monotonic() - stored_at > GIS_PAYLOAD_CACHE_TTL_SECONDS:
        _GIS_PAYLOAD_CACHE.pop(cache_key, None)
        return None
    return payload


def _store_gis_payload(cache_key: tuple[Any, ...], payload: dict[str, Any]) -> None:
    _GIS_PAYLOAD_CACHE[cache_key] = (time.monotonic(), payload)


def _tel_aviv_parcel_payload(session: Session, *, gush: str, helka: str, radius_m: float, poi_limit: int, profile: str) -> dict[str, Any]:
    if profile == "full":
        point_limit = None
        building_limit = None
        context_layer_limits = OSM_CONTEXT_LAYER_LIMITS
        municipal_layer_limits = MUNICIPAL_CONTEXT_LAYER_LIMITS
        nearby_parcel_limit = None
        address_limit = None
    elif profile == "overview":
        point_limit = OVERVIEW_POINT_LIMIT
        building_limit = OVERVIEW_BUILDING_LIMIT
        context_layer_limits = OVERVIEW_CONTEXT_GEOMETRY_LIMITS
        municipal_layer_limits = OVERVIEW_MUNICIPAL_CONTEXT_LIMITS
        nearby_parcel_limit = OVERVIEW_NEARBY_PARCEL_LIMIT
        address_limit = 0
    else:
        point_limit = INITIAL_POINT_LIMIT
        building_limit = INITIAL_BUILDING_LIMIT
        context_layer_limits = INITIAL_CONTEXT_GEOMETRY_LIMITS
        municipal_layer_limits = INITIAL_MUNICIPAL_CONTEXT_LIMITS
        nearby_parcel_limit = INITIAL_NEARBY_PARCEL_LIMIT
        address_limit = 0
    try:
        parcel = _parcel_row(session, gush=gush, helka=helka, source_id="mapi_parcels")
        if parcel is None:
            return _not_found_payload(gush=gush, helka=helka, radius_m=radius_m)
        center = _center_from_geometry(parcel.get("centroid") or parcel.get("geometry"))
        if center is None:
            return _unavailable_payload(gush=gush, helka=helka, radius_m=radius_m, reason="parcel_centroid_unavailable")

        transport_rows, transport_total = _municipal_boundary_poi_rows_by_category(
            session,
            municipality_code=TEL_AVIV_MUNICIPALITY_CODE,
            lon=center["lon"],
            lat=center["lat"],
            category="transport_stop",
            limit=point_limit,
        )
        school_rows, school_total = _municipal_boundary_poi_rows_by_category(
            session,
            municipality_code=TEL_AVIV_MUNICIPALITY_CODE,
            lon=center["lon"],
            lat=center["lat"],
            category="school",
            limit=point_limit,
        )
        context_rows, context_total = _municipal_boundary_context_poi_rows_with_count(session, municipality_code=TEL_AVIV_MUNICIPALITY_CODE, lon=center["lon"], lat=center["lat"], limit=point_limit)
        building_rows, building_total = _municipal_boundary_building_rows_with_count(session, municipality_code=TEL_AVIV_MUNICIPALITY_CODE, lon=center["lon"], lat=center["lat"], limit=building_limit)
        context_geometry_layers = {
            layer_key: _municipal_boundary_context_geometry_rows_with_count(session, context_layer=layer_key, municipality_code=TEL_AVIV_MUNICIPALITY_CODE, lon=center["lon"], lat=center["lat"], limit=limit)
            for layer_key, limit in context_layer_limits.items()
        }
        municipal_context_layers = {
            layer_key: _municipal_boundary_context_geometry_rows_with_count(session, context_layer=layer_key, municipality_code=TEL_AVIV_MUNICIPALITY_CODE, lon=center["lon"], lat=center["lat"], limit=limit)
            for layer_key, limit in municipal_layer_limits.items()
        }
        boundary_rows = _boundary_rows_for_municipality(session, municipality_code=TEL_AVIV_MUNICIPALITY_CODE, limit=1)
        plan_rows = _polygon_rows_covering_point(session, "plans", lon=center["lon"], lat=center["lat"], limit=8)
        neighborhood_rows = _municipal_neighborhood_rows(session, lon=center["lon"], lat=center["lat"], municipality_code=TEL_AVIV_MUNICIPALITY_CODE, radius_m=None, limit=None)
        address_rows, address_total = _municipal_address_rows_with_count(session, lon=center["lon"], lat=center["lat"], municipality_code=TEL_AVIV_MUNICIPALITY_CODE, radius_m=None, limit=address_limit)
        municipal_poi_rows, municipal_poi_total = _municipal_poi_rows_with_count(session, lon=center["lon"], lat=center["lat"], municipality_code=TEL_AVIV_MUNICIPALITY_CODE, radius_m=None, limit=point_limit)
        nearby_parcel_rows, nearby_parcel_total = _mapi_parcel_rows_for_municipality_boundary(
            session,
            municipality_code=TEL_AVIV_MUNICIPALITY_CODE,
            selected_gush=gush,
            selected_helka=helka,
            lon=center["lon"],
            lat=center["lat"],
            limit=nearby_parcel_limit,
        )
        coverage = _coverage_rows_for_municipality(session, municipality_code=TEL_AVIV_MUNICIPALITY_CODE)
    except SQLAlchemyError as exc:
        return _unavailable_payload(gush=gush, helka=helka, radius_m=radius_m, reason=exc.__class__.__name__)

    parcel_feature = _parcel_feature(parcel)
    poi_features = [_poi_feature(row) for row in [*transport_rows, *school_rows]]
    return {
        "status": "found",
        "real_gis_available": True,
        "selected_example": "tel_aviv_parcel",
        "examples": _example_options(),
        "query": {"gush": str(gush), "helka": str(helka), "municipality_code": TEL_AVIV_MUNICIPALITY_CODE, "spatial_scope": "municipality_boundary", "fallback_radius_m": float(radius_m), "profile": profile},
        "title_he": f"דמו GIS תל אביב: חלקת MAPI רשמית {parcel_feature['gush']}/{parcel_feature['helka']}",
        "center": center,
        "zoom": 17,
        "focus": "tel_aviv_parcel",
        "visual_context": {"mode": "osm_tiles", "tile_opacity": 0.50, "status": "context_only"},
        "basemap": {
            "provider": "OpenStreetMap",
            "display_status": "context_only",
            "attribution": "OpenStreetMap contributors; basemap is contextual only",
            "tile_url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        },
        "parcel": parcel_feature,
        "nearby_pois": {
            "count": len(poi_features),
            "displayed_count": len(poi_features),
            "total_count": int(transport_total) + int(school_total),
            "items": poi_features,
            "categories": {
                "transport_stop": {"displayed_count": len(transport_rows), "total_count": int(transport_total), "source_id": "mot_gtfs_stops"},
                "school": {"displayed_count": len(school_rows), "total_count": int(school_total), "source_id": "moe_school_coordinates"},
            },
        },
        "layers": {
            "nearby_parcels": {"count": len(nearby_parcel_rows), "displayed_count": len(nearby_parcel_rows), "total_count": int(nearby_parcel_total), "items": [_parcel_feature(row) for row in nearby_parcel_rows], "status": "found" if nearby_parcel_rows else "not_found"},
            "plans": {"count": len(plan_rows), "displayed_count": len(plan_rows), "total_count": len(plan_rows), "items": [_plan_feature(row) for row in plan_rows], "status": "found" if plan_rows else "not_found"},
            "municipal_boundaries": {"count": len(boundary_rows), "items": [_boundary_feature(row) for row in boundary_rows], "status": "found" if boundary_rows else "not_found"},
            "neighborhoods": {"count": len(neighborhood_rows), "displayed_count": len(neighborhood_rows), "total_count": len(neighborhood_rows), "items": [_neighborhood_feature(row) for row in neighborhood_rows], "status": "municipal_license_under_review" if neighborhood_rows else "not_ingested", "message_he": "שכבת שכונות עירונית של תל אביב מוצגת רק אם נטענה בפועל, ובכל מקרה מסומנת בבדיקת רישיון."},
            "address_points": {"count": len(address_rows), "displayed_count": len(address_rows), "total_count": int(address_total), "items": [_address_feature(row) for row in address_rows], "status": "municipal_license_under_review" if address_rows else "not_loaded", "message_he": "כתובות אינן מוצגות בטעינה הראשונית כי השכבה חלקית ומטעה כתצוגה עירונית."},
            "municipal_pois": {"count": len(municipal_poi_rows), "displayed_count": len(municipal_poi_rows), "total_count": int(municipal_poi_total), "items": [_poi_feature(row) for row in municipal_poi_rows], "status": "municipal_license_under_review" if municipal_poi_rows else "not_ingested", "message_he": "מוקדים עירוניים של תל אביב מוצגים רק כמידע בבדיקת רישיון."},
            "context_pois": {"count": len(context_rows), "displayed_count": len(context_rows), "total_count": int(context_total), "items": [_context_poi_feature(row) for row in context_rows], "status": "context_only"},
            "buildings": {"count": len(building_rows), "displayed_count": len(building_rows), "total_count": int(building_total), "items": [_building_feature(row) for row in building_rows], "status": "context_only"},
            **{
                layer_key: {
                    "count": len(rows),
                    "displayed_count": len(rows),
                    "total_count": int(total),
                    "items": [_context_geometry_feature(row) for row in rows],
                    "status": "context_only" if rows else "not_ingested",
                }
                for layer_key, (rows, total) in context_geometry_layers.items()
            },
            **{
                layer_key: {
                    "count": len(rows),
                    "displayed_count": len(rows),
                    "total_count": int(total),
                    "items": [_context_geometry_feature(row) for row in rows],
                    "status": "municipal_license_under_review" if rows else "not_ingested",
                    "message_he": "שכבה עירונית של תל אביב מוצגת כמידע בבדיקת רישיון, לא כמקור רשמי מאושר לשימוש חוזר.",
                }
                for layer_key, (rows, total) in municipal_context_layers.items()
            },
        },
        "coverage": coverage,
        "legend": [
            {"id": "parcel", "label": "חלקת MAPI רשמית", "kind": "polygon", "color": "#14532d", "display_status": "official"},
            {"id": "nearby_parcels", "label": "חלקות MAPI סמוכות", "kind": "polygon", "color": "#7c6f55", "display_status": "official"},
            {"id": "municipal_boundaries", "label": "גבול תל אביב רשמי", "kind": "polygon", "color": "#0f766e", "display_status": "official"},
            {"id": "transport_stop", "label": "תחנות תחבורה רשמיות", "kind": "point", "color": "#0b68d1", "display_status": "official"},
            {"id": "school", "label": "מוסדות חינוך רשמיים", "kind": "point", "color": "#f59e0b", "display_status": "official"},
            {"id": "neighborhoods", "label": "שכונות תל אביב - בבדיקת רישיון", "kind": "polygon", "color": "#2563eb", "display_status": "municipal_license_under_review"},
            {"id": "address_points", "label": "כתובות עירוניות - בבדיקת רישיון", "kind": "point", "color": "#9333ea", "display_status": "municipal_license_under_review"},
            {"id": "municipal_pois", "label": "מוקדים עירוניים - בבדיקת רישיון", "kind": "point", "color": "#dc2626", "display_status": "municipal_license_under_review"},
            {"id": "buildings", "label": "מבני OSM - הקשר בלבד", "kind": "polygon", "color": "#f97316", "display_status": "context_only"},
            {"id": "context_pois", "label": "מוקדי OSM - הקשר בלבד", "kind": "point", "color": "#64748b", "display_status": "context_only"},
            {"id": "context_roads", "label": "דרכי OSM - הקשר בלבד", "kind": "line", "color": "#ffffff", "display_status": "context_only"},
            {"id": "context_railways", "label": "מסילות OSM - הקשר בלבד", "kind": "line", "color": "#475569", "display_status": "context_only"},
            {"id": "context_waterways", "label": "ערוצי מים OSM - הקשר בלבד", "kind": "line", "color": "#38bdf8", "display_status": "context_only"},
            {"id": "context_water", "label": "מים OSM - הקשר בלבד", "kind": "polygon", "color": "#7dd3fc", "display_status": "context_only"},
            {"id": "context_landuse", "label": "פארקים/שימושי קרקע OSM - הקשר בלבד", "kind": "polygon", "color": "#86efac", "display_status": "context_only"},
            {"id": "municipal_parks", "label": "גנים ופארקים תל אביב - בבדיקת רישיון", "kind": "polygon", "color": "#22c55e", "display_status": "municipal_license_under_review"},
            {"id": "municipal_beaches", "label": "חופי תל אביב - בבדיקת רישיון", "kind": "polygon", "color": "#0ea5e9", "display_status": "municipal_license_under_review"},
            {"id": "municipal_bike_paths", "label": "שבילי אופניים תל אביב - בבדיקת רישיון", "kind": "line", "color": "#06b6d4", "display_status": "municipal_license_under_review"},
        ],
        "caveats": [
            "החלקה מגיעה מקובץ MAPI הרשמי שהורד ידנית ונטען למערכת.",
            "מבנים ומוקדי OSM מוצגים כהקשר בלבד ואינם מקור רשמי.",
            "דרכים, מים ושימושי קרקע מ-OSM מוצגים כרקע הקשר בלבד ולא כמידע רשמי.",
            "מקורות עירוניים של תל אביב מוצגים רק אם נטענו בפועל, ומסומנים בבדיקת רישיון - לא כמקור רשמי מאומת לשימוש חוזר.",
            "שכבות עירוניות כגון שכונות, כתובות, חופים, גנים ושבילי אופניים מוצגות במעמד בדיקת רישיון עד אישור חוזר לשימוש.",
        ],
    }


def _jerusalem_plan_payload(session: Session, *, radius_m: float, poi_limit: int, example: str) -> dict[str, Any] | None:
    plan = _plan_row(session, plan_number=JERUSALEM_PLAN_NUMBER)
    if plan is None:
        return None
    center = _center_from_geometry(plan.get("centroid") or plan.get("geometry"))
    if center is None:
        return _unavailable_payload(gush="", helka="", radius_m=radius_m, reason="plan_centroid_unavailable")

    transport_rows, transport_total = _nearby_poi_rows_by_category(
        session,
        lon=center["lon"],
        lat=center["lat"],
        radius_m=radius_m,
        category="transport_stop",
        limit=max(1, poi_limit // 2),
    )
    school_rows, school_total = _nearby_poi_rows_by_category(
        session,
        lon=center["lon"],
        lat=center["lat"],
        radius_m=radius_m,
        category="school",
        limit=max(1, poi_limit - len(transport_rows)),
    )
    pois = [_poi_feature(row) for row in [*transport_rows, *school_rows]]
    boundary_rows = _boundary_rows_for_point(session, lon=center["lon"], lat=center["lat"], municipality_code=JERUSALEM_MUNICIPALITY_CODE, limit=1)
    context_rows, context_total = _context_poi_rows_with_count(session, lon=center["lon"], lat=center["lat"], radius_m=radius_m, limit=20)
    building_rows, building_total = _building_rows_with_count(session, lon=center["lon"], lat=center["lat"], radius_m=radius_m, limit=40)
    coverage = _coverage_rows_for_municipality(session, municipality_code=JERUSALEM_MUNICIPALITY_CODE)

    return {
        "status": "found",
        "real_gis_available": True,
        "selected_example": example,
        "examples": _example_options(),
        "focus": "jerusalem_plan",
        "query": {
            "plan_number": JERUSALEM_PLAN_NUMBER,
            "municipality_code": JERUSALEM_MUNICIPALITY_CODE,
            "radius_m": float(radius_m),
        },
        "title_he": f"דוגמת GIS אמיתית בירושלים: תכנית {JERUSALEM_PLAN_NUMBER}",
        "center": center,
        "zoom": 15,
        "visual_context": {"mode": "osm_tiles", "tile_opacity": 0.62, "status": "context_only"},
        "basemap": {
            "provider": "OpenStreetMap",
            "display_status": "context_only",
            "attribution": "OpenStreetMap contributors; basemap is contextual only",
            "tile_url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        },
        "parcel": None,
        "primary_feature": _plan_feature(plan),
        "nearby_pois": {
            "count": len(pois),
            "displayed_count": len(pois),
            "total_count": int(transport_total) + int(school_total),
            "items": pois,
            "categories": {
                "transport_stop": {"displayed_count": len(transport_rows), "total_count": int(transport_total), "source_id": "mot_gtfs_stops"},
                "school": {"displayed_count": len(school_rows), "total_count": int(school_total), "source_id": "moe_school_coordinates"},
            },
        },
        "layers": {
            "plans": {"count": 1, "displayed_count": 1, "total_count": 1, "items": [_plan_feature(plan)], "status": "found"},
            "municipal_boundaries": {"count": len(boundary_rows), "items": [_boundary_feature(row) for row in boundary_rows], "status": "found" if boundary_rows else "not_found"},
            "neighborhoods": {"count": 0, "items": [], "status": "not_ingested", "message_he": "לא נטענה שכבת שכונות עירונית מאומתת לירושלים."},
            "context_pois": {"count": len(context_rows), "displayed_count": len(context_rows), "total_count": int(context_total), "items": [_context_poi_feature(row) for row in context_rows], "status": "context_only"},
            "buildings": {"count": len(building_rows), "displayed_count": len(building_rows), "total_count": int(building_total), "items": [_building_feature(row) for row in building_rows], "status": "context_only"},
        },
        "coverage": coverage,
        "legend": _legend_items(),
        "caveats": [
            "כל שכבה מוצגת לפי מקור ו-provenance; אין החלפה של שכבה רשמית בנתוני הקשר.",
            "מבנים ומוקדי OSM מוצגים כהקשר בלבד ואינם שכבה רשמית.",
            "אם שכבת כיסוי מדווחת אחרת מהשורות בפועל, המפה מציגה את השורות בפועל ואת סטטוס הכיסוי בנפרד.",
        ],
    }


def _normalize_example(example: str) -> str:
    value = str(example or "tel_aviv_parcel").strip().lower()
    allowed = {item["id"] for item in _example_options()}
    return value if value in allowed else "tel_aviv_parcel"


def _normalize_profile(profile: str) -> str:
    value = str(profile or "initial").strip().lower()
    return value if value in {"initial", "overview", "full"} else "initial"


def _example_options() -> list[dict[str, str]]:
    return [
        {"id": "tel_aviv_parcel", "label_he": "תל אביב - חלקת MAPI רשמית"},
    ]


def _legend_items() -> list[dict[str, str]]:
    return [
        {"id": "plans", "label": "תכנית XPLAN רשמית", "kind": "polygon", "color": "#8b5cf6", "display_status": "official"},
        {"id": "municipal_boundaries", "label": "גבול רשות רשמי", "kind": "polygon", "color": "#0f766e", "display_status": "official"},
        {"id": "transport_stop", "label": "תחנות תחבורה רשמיות", "kind": "point", "color": "#0b68d1", "display_status": "official"},
        {"id": "school", "label": "מוסדות חינוך רשמיים", "kind": "point", "color": "#f59e0b", "display_status": "official"},
        {"id": "buildings", "label": "מבני OSM - הקשר בלבד", "kind": "polygon", "color": "#f97316", "display_status": "context_only"},
        {"id": "context_pois", "label": "מוקדי OSM - הקשר בלבד", "kind": "point", "color": "#64748b", "display_status": "context_only"},
    ]


def _parcel_row(session: Session, *, gush: str, helka: str, source_id: str | None = None) -> Mapping[str, Any] | None:
    source_filter = "AND p.source_id = :source_id" if source_id else ""
    params = {"gush": str(gush).strip(), "helka": str(helka).strip(), "source_id": source_id}
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
              {source_filter}
            ORDER BY p.fetched_at DESC
            LIMIT 1
            """
        ),
        params,
    ).mappings().first()
    return row


def _plan_row(session: Session, *, plan_number: str) -> Mapping[str, Any] | None:
    row = session.execute(
        text(
            f"""
            SELECT p.id,
                   p.source_id AS feature_source_id,
                   p.provenance_id,
                   p.plan_number,
                   p.plan_name,
                   p.validation_status,
                   ST_AsGeoJSON(p.geom) AS geometry,
                   ST_AsGeoJSON(ST_Centroid(p.geom)) AS centroid,
                   {SOURCE_COLUMNS}
            FROM plans p
            JOIN source_registry sr ON sr.source_id = p.source_id
            WHERE p.plan_number = :plan_number
            ORDER BY p.fetched_at DESC
            LIMIT 1
            """
        ),
        {"plan_number": str(plan_number).strip()},
    ).mappings().first()
    return row


def _nearby_poi_rows(session: Session, *, lon: float, lat: float, radius_m: float, limit: int | None) -> list[Mapping[str, Any]]:
    params = _limit_params({"lon": lon, "lat": lat, "radius_m": float(radius_m)}, limit)
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
            {_limit_clause(limit)}
            """
        ),
        params,
    ).mappings().all()
    return list(rows)


def _nearby_poi_rows_by_category(
    session: Session,
    *,
    lon: float,
    lat: float,
    radius_m: float,
    category: str,
    limit: int | None,
) -> tuple[list[Mapping[str, Any]], int]:
    params = _limit_params({"lon": lon, "lat": lat, "radius_m": float(radius_m), "category": category}, limit)
    count = int(
        session.execute(
            text(
                """
                SELECT count(*)
                FROM poi_points p
                WHERE p.poi_category = :category
                  AND ST_DWithin(
                    p.geom_2039,
                    ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039),
                    :radius_m
                  )
                """
            ),
            params,
        ).scalar_one()
        or 0
    )
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
            WHERE p.poi_category = :category
              AND ST_DWithin(
                p.geom_2039,
                ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039),
                :radius_m
            )
            ORDER BY distance_m ASC
            {_limit_clause(limit)}
            """
        ),
        params,
    ).mappings().all()
    return list(rows), count


def _municipal_boundary_poi_rows_by_category(
    session: Session,
    *,
    municipality_code: str,
    lon: float,
    lat: float,
    category: str,
    limit: int | None,
) -> tuple[list[Mapping[str, Any]], int]:
    params = _limit_params({"municipality_code": municipality_code, "lon": lon, "lat": lat, "category": category}, limit)
    count = int(
        session.execute(
            text(
                f"""
                SELECT count(*)
                FROM poi_points p
                WHERE p.poi_category = :category
                  AND {_municipal_boundary_filter("p")}
                """
            ),
            params,
        ).scalar_one()
        or 0
    )
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
            WHERE p.poi_category = :category
              AND {_municipal_boundary_filter("p")}
            ORDER BY distance_m ASC
            {_limit_clause(limit)}
            """
        ),
        params,
    ).mappings().all()
    return list(rows), count


def _boundary_rows_for_point(session: Session, *, lon: float, lat: float, municipality_code: str, limit: int) -> list[Mapping[str, Any]]:
    return list(
        session.execute(
            text(
                f"""
                SELECT b.id,
                       b.source_id AS feature_source_id,
                       b.provenance_id,
                       NULL AS plan_number,
                       NULL AS plan_name,
                       b.municipality_code,
                       b.municipality_name_he,
                       NULL AS name_he,
                       NULL AS name_en,
                       NULL AS source_object_id,
                       b.validation_status,
                       ST_AsGeoJSON(b.geom) AS geometry,
                       {SOURCE_COLUMNS}
                FROM official_municipal_boundaries b
                JOIN source_registry sr ON sr.source_id = b.source_id
                WHERE b.municipality_code = :municipality_code
                  AND ST_Covers(b.geom, ST_SetSRID(ST_Point(:lon, :lat), 4326))
                ORDER BY b.fetched_at DESC
                LIMIT :limit
                """
            ),
            {"lon": lon, "lat": lat, "municipality_code": municipality_code, "limit": int(limit)},
        ).mappings().all()
    )


def _boundary_rows_for_municipality(session: Session, *, municipality_code: str, limit: int) -> list[Mapping[str, Any]]:
    return list(
        session.execute(
            text(
                f"""
                SELECT b.id,
                       b.source_id AS feature_source_id,
                       b.provenance_id,
                       NULL AS plan_number,
                       NULL AS plan_name,
                       b.municipality_code,
                       b.municipality_name_he,
                       NULL AS name_he,
                       NULL AS name_en,
                       NULL AS source_object_id,
                       b.validation_status,
                       ST_AsGeoJSON(b.geom) AS geometry,
                       {SOURCE_COLUMNS}
                FROM official_municipal_boundaries b
                JOIN source_registry sr ON sr.source_id = b.source_id
                WHERE b.municipality_code = :municipality_code
                ORDER BY b.fetched_at DESC
                LIMIT :limit
                """
            ),
            {"municipality_code": municipality_code, "limit": int(limit)},
        ).mappings().all()
    )


def _municipal_boundary_filter(table_alias: str) -> str:
    return f"""
    EXISTS (
      SELECT 1
      FROM official_municipal_boundaries boundary
      WHERE boundary.municipality_code = :municipality_code
        AND ST_Covers(boundary.geom, ST_PointOnSurface({table_alias}.geom))
    )
    """


def _coverage_rows_for_municipality(session: Session, *, municipality_code: str) -> list[dict[str, Any]]:
    try:
        rows = session.execute(
            text(
                """
                SELECT lc.municipality_code,
                       lc.municipality_name_he,
                       lc.layer_key,
                       lc.status,
                       lc.source_id,
                       lc.feature_count,
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
                FROM layer_coverage lc
                LEFT JOIN source_registry sr ON sr.source_id = lc.source_id
                WHERE lc.municipality_code = :municipality_code
                ORDER BY lc.layer_key, lc.source_id
                """
            ),
            {"municipality_code": municipality_code},
        ).mappings().all()
    except SQLAlchemyError:
        return []
    return [
        {
            "municipality_code": row.get("municipality_code"),
            "municipality_name_he": row.get("municipality_name_he"),
            "layer_key": row.get("layer_key"),
            "status": row.get("status"),
            "source_id": row.get("source_id"),
            "feature_count": int(row.get("feature_count") or 0),
            "source": source_fragment_from_row(row) if row.get("source_id") else None,
        }
        for row in rows
    ]


def _nearby_mapi_parcel_rows_for_gush(
    session: Session,
    *,
    gush: str,
    selected_helka: str,
    lon: float,
    lat: float,
    limit: int | None,
) -> tuple[list[Mapping[str, Any]], int]:
    # Keep the demo generic and source-backed: display only official MAPI parcels
    # from the same cadastral block, with the selected parcel highlighted separately.
    params = _limit_params({"gush": str(gush).strip(), "selected_helka": str(selected_helka).strip(), "lon": lon, "lat": lat}, limit)
    try:
        count = int(
            session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM parcels p
                    WHERE p.source_id = 'mapi_parcels'
                      AND p.gush = :gush
                      AND p.helka <> :selected_helka
                    """
                ),
                params,
            ).scalar_one()
            or 0
        )
        rows = session.execute(
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
                       {SOURCE_COLUMNS}
                FROM parcels p
                JOIN source_registry sr ON sr.source_id = p.source_id
                WHERE p.source_id = 'mapi_parcels'
                  AND p.gush = :gush
                  AND p.helka <> :selected_helka
                ORDER BY ST_Distance(
                  p.geom_2039,
                  ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)
                ) ASC
                {_limit_clause(limit)}
                """
            ),
            params,
        ).mappings().all()
        return list(rows), count
    except SQLAlchemyError:
        return [], 0


def _mapi_parcel_rows_for_municipality_boundary(
    session: Session,
    *,
    municipality_code: str,
    selected_gush: str,
    selected_helka: str,
    lon: float,
    lat: float,
    limit: int | None,
) -> tuple[list[Mapping[str, Any]], int]:
    # Shows all already-ingested official MAPI parcels inside the official municipal boundary.
    params = _limit_params(
        {
            "municipality_code": municipality_code,
            "selected_gush": str(selected_gush).strip(),
            "selected_helka": str(selected_helka).strip(),
            "lon": lon,
            "lat": lat,
        },
        limit,
    )
    try:
        count = int(
            session.execute(
                text(
                    f"""
                    SELECT count(*)
                    FROM parcels p
                    WHERE p.source_id = 'mapi_parcels'
                      AND NOT (p.gush = :selected_gush AND p.helka = :selected_helka)
                      AND {_municipal_boundary_filter("p")}
                    """
                ),
                params,
            ).scalar_one()
            or 0
        )
        rows = session.execute(
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
                       {SOURCE_COLUMNS}
                FROM parcels p
                JOIN source_registry sr ON sr.source_id = p.source_id
                WHERE p.source_id = 'mapi_parcels'
                  AND NOT (p.gush = :selected_gush AND p.helka = :selected_helka)
                  AND {_municipal_boundary_filter("p")}
                ORDER BY ST_Distance(
                  p.geom_2039,
                  ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)
                ) ASC
                {_limit_clause(limit)}
                """
            ),
            params,
        ).mappings().all()
        return list(rows), count
    except SQLAlchemyError:
        return [], 0


def _example_layers(session: Session, *, lon: float, lat: float, radius_m: float) -> dict[str, Any]:
    plans = [_plan_feature(row) for row in _polygon_rows_covering_point(session, "plans", lon=lon, lat=lat, limit=8)]
    boundaries = [_boundary_feature(row) for row in _polygon_rows_covering_point(session, "official_municipal_boundaries", lon=lon, lat=lat, limit=3)]
    neighborhoods = [_neighborhood_feature(row) for row in _polygon_rows_covering_point(session, "neighborhoods", lon=lon, lat=lat, limit=6)]
    context_pois = [_context_poi_feature(row) for row in _context_poi_rows(session, lon=lon, lat=lat, radius_m=radius_m, limit=20)]
    buildings = [_building_feature(row) for row in _building_rows(session, lon=lon, lat=lat, radius_m=radius_m, limit=20)]
    return {
        "plans": {"count": len(plans), "items": plans},
        "municipal_boundaries": {"count": len(boundaries), "items": boundaries},
        "neighborhoods": {"count": len(neighborhoods), "items": neighborhoods},
        "context_pois": {"count": len(context_pois), "items": context_pois},
        "buildings": {"count": len(buildings), "items": buildings},
    }


def _polygon_rows_covering_point(session: Session, table_name: str, *, lon: float, lat: float, limit: int) -> list[Mapping[str, Any]]:
    columns_by_table = {
        "plans": "p.plan_number, p.plan_name, NULL AS municipality_code, NULL AS municipality_name_he, NULL AS name_he, NULL AS name_en, NULL AS source_object_id, p.validation_status",
        "official_municipal_boundaries": "NULL AS plan_number, NULL AS plan_name, p.municipality_code, p.municipality_name_he, NULL AS name_he, NULL AS name_en, NULL AS source_object_id, p.validation_status",
        "neighborhoods": "NULL AS plan_number, NULL AS plan_name, p.municipality_code, NULL AS municipality_name_he, p.name_he, p.name_en, NULL AS source_object_id, p.validation_status",
    }
    extra_columns = columns_by_table.get(table_name)
    if extra_columns is None:
        return []
    try:
        return list(
            session.execute(
                text(
                    f"""
                    SELECT p.id, p.source_id AS feature_source_id, p.provenance_id,
                           {extra_columns}, ST_AsGeoJSON(p.geom) AS geometry, {SOURCE_COLUMNS}
                    FROM {table_name} p
                    JOIN source_registry sr ON sr.source_id = p.source_id
                    WHERE ST_Covers(p.geom, ST_SetSRID(ST_Point(:lon, :lat), 4326))
                    ORDER BY p.fetched_at DESC
                    LIMIT :limit
                    """
                ),
                {"lon": lon, "lat": lat, "limit": limit},
            ).mappings().all()
        )
    except SQLAlchemyError:
        return []


def _context_poi_rows(session: Session, *, lon: float, lat: float, radius_m: float, limit: int | None) -> list[Mapping[str, Any]]:
    params = _limit_params({"lon": lon, "lat": lat, "radius_m": radius_m}, limit)
    try:
        return list(
            session.execute(
                text(
                    f"""
                    SELECT c.id, c.source_id AS feature_source_id, c.provenance_id,
                           c.source_object_id, c.poi_category, c.name AS name_he, NULL AS name_en,
                           ST_AsGeoJSON(c.geom) AS geometry,
                           ST_Distance(c.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)) AS distance_m,
                           {SOURCE_COLUMNS}
                    FROM context_pois c
                    JOIN source_registry sr ON sr.source_id = c.source_id
                    WHERE ST_DWithin(c.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039), :radius_m)
                    ORDER BY distance_m ASC
                    {_limit_clause(limit)}
                    """
                ),
                params,
            ).mappings().all()
        )
    except SQLAlchemyError:
        return []


def _context_poi_rows_with_count(session: Session, *, lon: float, lat: float, radius_m: float, limit: int | None) -> tuple[list[Mapping[str, Any]], int]:
    try:
        count = int(
            session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM context_pois c
                    WHERE ST_DWithin(c.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039), :radius_m)
                    """
                ),
                {"lon": lon, "lat": lat, "radius_m": radius_m},
            ).scalar_one()
            or 0
        )
        return _context_poi_rows(session, lon=lon, lat=lat, radius_m=radius_m, limit=limit), count
    except SQLAlchemyError:
        return [], 0


def _municipal_boundary_context_poi_rows_with_count(session: Session, *, municipality_code: str, lon: float, lat: float, limit: int | None) -> tuple[list[Mapping[str, Any]], int]:
    params = _limit_params({"municipality_code": municipality_code, "lon": lon, "lat": lat}, limit)
    try:
        count = int(
            session.execute(
                text(
                    f"""
                    SELECT count(*)
                    FROM context_pois c
                    WHERE {_municipal_boundary_filter("c")}
                    """
                ),
                params,
            ).scalar_one()
            or 0
        )
        rows = session.execute(
            text(
                f"""
                SELECT c.id, c.source_id AS feature_source_id, c.provenance_id,
                       c.source_object_id, c.poi_category, c.name AS name_he, NULL AS name_en,
                       ST_AsGeoJSON(c.geom) AS geometry,
                       ST_Distance(c.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)) AS distance_m,
                       {SOURCE_COLUMNS}
                FROM context_pois c
                JOIN source_registry sr ON sr.source_id = c.source_id
                WHERE {_municipal_boundary_filter("c")}
                ORDER BY distance_m ASC
                {_limit_clause(limit)}
                """
            ),
            params,
        ).mappings().all()
        return list(rows), count
    except SQLAlchemyError:
        return [], 0


def _building_rows(session: Session, *, lon: float, lat: float, radius_m: float, limit: int | None) -> list[Mapping[str, Any]]:
    params = _limit_params({"lon": lon, "lat": lat, "radius_m": radius_m}, limit)
    try:
        return list(
            session.execute(
                text(
                    f"""
                    SELECT b.id, b.source_id AS feature_source_id, b.provenance_id,
                           b.source_object_id, b.municipality_code, b.validation_status,
                           ST_AsGeoJSON(b.geom) AS geometry,
                           ST_Distance(ST_PointOnSurface(b.geom_2039), ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)) AS distance_m,
                           {SOURCE_COLUMNS}
                    FROM buildings b
                    JOIN source_registry sr ON sr.source_id = b.source_id
                    WHERE ST_DWithin(b.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039), :radius_m)
                    ORDER BY distance_m ASC
                    {_limit_clause(limit)}
                    """
                ),
                params,
            ).mappings().all()
        )
    except SQLAlchemyError:
        return []


def _building_rows_with_count(session: Session, *, lon: float, lat: float, radius_m: float, limit: int | None) -> tuple[list[Mapping[str, Any]], int]:
    try:
        count = int(
            session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM buildings b
                    WHERE ST_DWithin(b.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039), :radius_m)
                    """
                ),
                {"lon": lon, "lat": lat, "radius_m": radius_m},
            ).scalar_one()
            or 0
        )
        return _building_rows(session, lon=lon, lat=lat, radius_m=radius_m, limit=limit), count
    except SQLAlchemyError:
        return [], 0


def _municipal_boundary_building_rows_with_count(session: Session, *, municipality_code: str, lon: float, lat: float, limit: int | None) -> tuple[list[Mapping[str, Any]], int]:
    params = _limit_params({"municipality_code": municipality_code, "lon": lon, "lat": lat}, limit)
    try:
        count = int(
            session.execute(
                text(
                    f"""
                    SELECT count(*)
                    FROM buildings b
                    WHERE {_municipal_boundary_filter("b")}
                    """
                ),
                params,
            ).scalar_one()
            or 0
        )
        rows = session.execute(
            text(
                f"""
                SELECT b.id, b.source_id AS feature_source_id, b.provenance_id,
                       b.source_object_id, b.municipality_code, b.validation_status,
                       ST_AsGeoJSON(b.geom) AS geometry,
                       ST_Distance(ST_PointOnSurface(b.geom_2039), ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)) AS distance_m,
                       {SOURCE_COLUMNS}
                FROM buildings b
                JOIN source_registry sr ON sr.source_id = b.source_id
                WHERE {_municipal_boundary_filter("b")}
                ORDER BY distance_m ASC
                {_limit_clause(limit)}
                """
            ),
            params,
        ).mappings().all()
        return list(rows), count
    except SQLAlchemyError:
        return [], 0


def _context_geometry_rows(session: Session, *, context_layer: str, lon: float, lat: float, radius_m: float, limit: int | None) -> list[Mapping[str, Any]]:
    params = _limit_params({"context_layer": context_layer, "lon": lon, "lat": lat, "radius_m": radius_m}, limit)
    try:
        return list(
            session.execute(
                text(
                    f"""
                    SELECT g.id, g.source_id AS feature_source_id, g.provenance_id,
                           g.source_object_id, g.context_layer, g.geometry_type,
                           g.category, g.name, g.validation_status,
                           ST_AsGeoJSON(g.geom) AS geometry,
                           ST_Distance(g.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)) AS distance_m,
                           {SOURCE_COLUMNS}
                    FROM context_geometries g
                    JOIN source_registry sr ON sr.source_id = g.source_id
                    WHERE g.context_layer = :context_layer
                      AND ST_DWithin(g.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039), :radius_m)
                    ORDER BY distance_m ASC
                    {_limit_clause(limit)}
                    """
                ),
                params,
            ).mappings().all()
        )
    except SQLAlchemyError:
        return []


def _context_geometry_rows_with_count(session: Session, *, context_layer: str, lon: float, lat: float, radius_m: float, limit: int | None) -> tuple[list[Mapping[str, Any]], int]:
    try:
        count = int(
            session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM context_geometries g
                    WHERE g.context_layer = :context_layer
                      AND ST_DWithin(g.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039), :radius_m)
                    """
                ),
                {"context_layer": context_layer, "lon": lon, "lat": lat, "radius_m": radius_m},
            ).scalar_one()
            or 0
        )
        return _context_geometry_rows(session, context_layer=context_layer, lon=lon, lat=lat, radius_m=radius_m, limit=limit), count
    except SQLAlchemyError:
        return [], 0


def _municipal_boundary_context_geometry_rows_with_count(session: Session, *, context_layer: str, municipality_code: str, lon: float, lat: float, limit: int | None) -> tuple[list[Mapping[str, Any]], int]:
    params = _limit_params({"context_layer": context_layer, "municipality_code": municipality_code, "lon": lon, "lat": lat}, limit)
    try:
        count = int(
            session.execute(
                text(
                    f"""
                    SELECT count(*)
                    FROM context_geometries g
                    WHERE g.context_layer = :context_layer
                      AND {_municipal_boundary_filter("g")}
                    """
                ),
                params,
            ).scalar_one()
            or 0
        )
        rows = session.execute(
            text(
                f"""
                SELECT g.id, g.source_id AS feature_source_id, g.provenance_id,
                       g.source_object_id, g.context_layer, g.geometry_type,
                       g.category, g.name, g.validation_status,
                       ST_AsGeoJSON(g.geom) AS geometry,
                       ST_Distance(g.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)) AS distance_m,
                       {SOURCE_COLUMNS}
                FROM context_geometries g
                JOIN source_registry sr ON sr.source_id = g.source_id
                WHERE g.context_layer = :context_layer
                  AND {_municipal_boundary_filter("g")}
                ORDER BY distance_m ASC
                {_limit_clause(limit)}
                """
            ),
            params,
        ).mappings().all()
        return list(rows), count
    except SQLAlchemyError:
        return [], 0


def _municipal_neighborhood_rows(session: Session, *, lon: float, lat: float, municipality_code: str, radius_m: float | None, limit: int | None) -> list[Mapping[str, Any]]:
    params = _limit_params({"lon": lon, "lat": lat, "municipality_code": municipality_code, **({"radius_m": radius_m} if radius_m is not None else {})}, limit)
    radius_filter = "AND ST_DWithin(n.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039), :radius_m)" if radius_m is not None else ""
    try:
        return list(
            session.execute(
                text(
                    f"""
                    SELECT n.id, n.source_id AS feature_source_id, n.provenance_id,
                           NULL AS plan_number, NULL AS plan_name, n.municipality_code,
                           NULL AS municipality_name_he, n.name_he, n.name_en,
                           NULL AS source_object_id, n.validation_status,
                           ST_AsGeoJSON(n.geom) AS geometry,
                           ST_Distance(ST_PointOnSurface(n.geom_2039), ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)) AS distance_m,
                           {SOURCE_COLUMNS}
                    FROM neighborhoods n
                    JOIN source_registry sr ON sr.source_id = n.source_id
                    WHERE n.municipality_code = :municipality_code
                      {radius_filter}
                    ORDER BY ST_Covers(n.geom, ST_SetSRID(ST_Point(:lon, :lat), 4326)) DESC, distance_m ASC
                    {_limit_clause(limit)}
                    """
                ),
                params,
            ).mappings().all()
        )
    except SQLAlchemyError:
        return []


def _municipal_address_rows_with_count(session: Session, *, lon: float, lat: float, municipality_code: str, radius_m: float | None, limit: int | None) -> tuple[list[Mapping[str, Any]], int]:
    params = _limit_params({"lon": lon, "lat": lat, "municipality_code": municipality_code, **({"radius_m": float(radius_m)} if radius_m is not None else {})}, limit)
    radius_filter = "AND ST_DWithin(a.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039), :radius_m)" if radius_m is not None else ""
    try:
        count = int(
            session.execute(
                text(
                    f"""
                    SELECT count(*)
                    FROM address_points a
                    WHERE a.municipality_code = :municipality_code
                      {radius_filter}
                    """
                ),
                params,
            ).scalar_one()
            or 0
        )
        rows = session.execute(
            text(
                f"""
                SELECT a.id, a.source_id AS feature_source_id, a.provenance_id,
                       a.source_object_id, a.municipality_code, a.street_name_he,
                       a.house_number, a.full_address_he, a.validation_status,
                       ST_AsGeoJSON(a.geom) AS geometry,
                       ST_Distance(a.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)) AS distance_m,
                       {SOURCE_COLUMNS}
                FROM address_points a
                JOIN source_registry sr ON sr.source_id = a.source_id
                WHERE a.municipality_code = :municipality_code
                  {radius_filter}
                ORDER BY distance_m ASC
                {_limit_clause(limit)}
                """
            ),
            params,
        ).mappings().all()
        return list(rows), count
    except SQLAlchemyError:
        return [], 0


def _municipal_poi_rows_with_count(session: Session, *, lon: float, lat: float, municipality_code: str, radius_m: float | None, limit: int | None) -> tuple[list[Mapping[str, Any]], int]:
    params = _limit_params({"lon": lon, "lat": lat, "municipality_code": municipality_code, **({"radius_m": float(radius_m)} if radius_m is not None else {})}, limit)
    radius_filter = "AND ST_DWithin(p.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039), :radius_m)" if radius_m is not None else ""
    try:
        count = int(
            session.execute(
                text(
                    f"""
                    SELECT count(*)
                    FROM poi_points p
                    WHERE p.source_id = 'tel_aviv_open_data_discovered'
                      AND p.municipality_code = :municipality_code
                      {radius_filter}
                    """
                ),
                params,
            ).scalar_one()
            or 0
        )
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
                       ST_Distance(p.geom_2039, ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)) AS distance_m,
                       {SOURCE_COLUMNS}
                FROM poi_points p
                JOIN source_registry sr ON sr.source_id = p.source_id
                WHERE p.source_id = 'tel_aviv_open_data_discovered'
                  AND p.municipality_code = :municipality_code
                  {radius_filter}
                ORDER BY distance_m ASC
                {_limit_clause(limit)}
                """
            ),
            params,
        ).mappings().all()
        return list(rows), count
    except SQLAlchemyError:
        return [], 0


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


def _plan_feature(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id")),
        "feature_type": "plan",
        "source_id": row.get("feature_source_id"),
        "provenance_id": str(row.get("provenance_id")),
        "source": source_fragment_from_row(row),
        "plan_number": row.get("plan_number"),
        "plan_name": row.get("plan_name"),
        "validation_status": row.get("validation_status"),
        "geometry": _load_geojson(row.get("geometry")),
    }


def _boundary_feature(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id")),
        "feature_type": "municipal_boundary",
        "source_id": row.get("feature_source_id"),
        "provenance_id": str(row.get("provenance_id")),
        "source": source_fragment_from_row(row),
        "municipality_code": row.get("municipality_code"),
        "municipality_name_he": row.get("municipality_name_he"),
        "validation_status": row.get("validation_status"),
        "geometry": _load_geojson(row.get("geometry")),
    }


def _neighborhood_feature(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id")),
        "feature_type": "neighborhood",
        "source_id": row.get("feature_source_id"),
        "provenance_id": str(row.get("provenance_id")),
        "source": source_fragment_from_row(row),
        "municipality_code": row.get("municipality_code"),
        "name_he": row.get("name_he"),
        "name_en": row.get("name_en"),
        "validation_status": row.get("validation_status"),
        "geometry": _load_geojson(row.get("geometry")),
    }


def _address_feature(row: Mapping[str, Any]) -> dict[str, Any]:
    label_parts = [str(value) for value in [row.get("street_name_he"), row.get("house_number")] if value]
    return {
        "id": str(row.get("id")),
        "feature_type": "address_point",
        "source_id": row.get("feature_source_id"),
        "provenance_id": str(row.get("provenance_id")),
        "source": source_fragment_from_row(row),
        "source_object_id": row.get("source_object_id"),
        "municipality_code": row.get("municipality_code"),
        "name_he": row.get("full_address_he") or " ".join(label_parts) or "נקודת כתובת",
        "street_name_he": row.get("street_name_he"),
        "house_number": row.get("house_number"),
        "full_address_he": row.get("full_address_he"),
        "validation_status": row.get("validation_status"),
        "distance_m": _float_or_none(row.get("distance_m")),
        "geometry": _load_geojson(row.get("geometry")),
    }


def _context_poi_feature(row: Mapping[str, Any]) -> dict[str, Any]:
    feature = _poi_feature(row)
    feature["feature_type"] = "context_poi"
    return feature


def _context_geometry_feature(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id")),
        "feature_type": "context_geometry",
        "source_id": row.get("feature_source_id"),
        "provenance_id": str(row.get("provenance_id")),
        "source": source_fragment_from_row(row),
        "source_object_id": row.get("source_object_id"),
        "context_layer": row.get("context_layer"),
        "geometry_type": row.get("geometry_type"),
        "category": row.get("category"),
        "name_he": row.get("name"),
        "validation_status": row.get("validation_status"),
        "distance_m": _float_or_none(row.get("distance_m")),
        "geometry": _load_geojson(row.get("geometry")),
    }


def _building_feature(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id")),
        "feature_type": "building",
        "source_id": row.get("feature_source_id"),
        "provenance_id": str(row.get("provenance_id")),
        "source": source_fragment_from_row(row),
        "source_object_id": row.get("source_object_id"),
        "municipality_code": row.get("municipality_code"),
        "validation_status": row.get("validation_status"),
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
