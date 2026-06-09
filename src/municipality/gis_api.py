from __future__ import annotations

import json
import os
import re
from typing import Any, Generator

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from municipality.db import build_engine, build_session_factory
from municipality.gis_importers import municipality_for_point
from municipality.gis_normalization import extract_cadastral_id, normalize_hebrew_text
from municipality.gis_provenance import source_fragment_from_row

engine = build_engine()
SessionLocal = build_session_factory(engine)
router = APIRouter(prefix="/v1", tags=["gis"])

GEOMETRY_DETAILS = {"none", "centroid", "bbox", "simplified", "full"}
SOURCE_COLUMNS = """
sr.name_he, sr.name_en, sr.provider_key, sr.provenance_level, sr.reuse_status,
sr.display_status, sr.source_is_official, sr.reuse_is_verified,
sr.display_as_official, sr.attribution, sr.license_name, sr.license_url
"""


def get_gis_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health")
def v1_health(db: Session = Depends(get_gis_db)) -> dict[str, Any]:
    api_status = {"status": "ok"}
    try:
        db.execute(text("SELECT 1"))
        db_status = {"status": "ok"}
    except SQLAlchemyError as exc:
        db_status = {"status": "error", "error": str(exc.__class__.__name__)}

    try:
        version = db.execute(text("SELECT postgis_full_version() AS version")).scalar_one()
        postgis_status = {"status": "ok", "version": str(version)}
    except SQLAlchemyError as exc:
        postgis_status = {"status": "unavailable", "error": str(exc.__class__.__name__)}

    return {"api": api_status, "db": db_status, "postgis": postgis_status}


@router.get("/sources")
def list_sources(
    enabled: bool | None = None,
    provider_key: str | None = None,
    provenance_level: str | None = None,
    reuse_status: str | None = None,
    display_status: str | None = None,
    municipality_code: str | None = None,
    layer_key: str | None = Query(default=None),
    db: Session = Depends(get_gis_db),
) -> dict[str, Any]:
    rows = db.execute(
        text(
            """
            SELECT source_id, provider_key, source_kind, name_he, name_en, owner_name, owner_url,
                   source_url, municipality_code, layer_keys, provenance_level, reuse_status,
                   geometry_status, display_status, source_is_official, reuse_is_verified,
                   display_as_official, legal_review_approved, enabled, license_name, license_url,
                   attribution, notes, metadata, created_at, updated_at
            FROM source_registry
            ORDER BY source_id
            """
        )
    ).mappings().all()

    items = [_source_payload(row) for row in rows]
    items = [item for item in items if _source_matches(item, enabled, provider_key, provenance_level, reuse_status, display_status, municipality_code, layer_key)]
    return {"items": items, "count": len(items)}


@router.get("/sources/{source_id}")
def get_source(source_id: str, db: Session = Depends(get_gis_db)) -> dict[str, Any]:
    row = db.execute(
        text(
            """
            SELECT source_id, provider_key, source_kind, name_he, name_en, owner_name, owner_url,
                   source_url, municipality_code, layer_keys, provenance_level, reuse_status,
                   geometry_status, display_status, source_is_official, reuse_is_verified,
                   display_as_official, legal_review_approved, enabled, license_name, license_url,
                   attribution, notes, metadata, created_at, updated_at
            FROM source_registry
            WHERE source_id = :source_id
            """
        ),
        {"source_id": source_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="source_not_found")
    return _source_payload(row)


@router.get("/coverage")
def list_coverage(
    municipality_code: str | None = None,
    layer_key: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_gis_db),
) -> dict[str, Any]:
    rows = _coverage_rows(db)
    items = [_coverage_payload(row) for row in rows]
    if municipality_code is not None:
        items = [item for item in items if item["municipality_code"] == municipality_code]
    if layer_key is not None:
        items = [item for item in items if item["layer_key"] == layer_key]
    if status is not None:
        items = [item for item in items if item["status"] == status]
    return {"items": items, "count": len(items)}


@router.get("/coverage/{municipality_code}")
def get_municipality_coverage(municipality_code: str, db: Session = Depends(get_gis_db)) -> dict[str, Any]:
    items = [_coverage_payload(row) for row in _coverage_rows(db) if row["municipality_code"] == municipality_code]
    if not items:
        raise HTTPException(status_code=404, detail="coverage_not_found")
    municipality_name_he = next((item.get("municipality_name_he") for item in items if item.get("municipality_name_he")), None)
    return {"municipality_code": municipality_code, "municipality_name_he": municipality_name_he, "layers": items}


@router.get("/plans/{plan_number}")
def get_plan(
    plan_number: str,
    include_geometry: bool = False,
    geometry_detail: str = Query(default="simplified"),
    db: Session = Depends(get_gis_db),
) -> dict[str, Any]:
    detail = _resolve_geometry_detail(include_geometry, geometry_detail)
    rows = db.execute(
        text(
            f"""
            SELECT p.id, p.plan_number, p.plan_name, p.source_id, p.provenance_id,
                   p.validation_status, p.validation_warnings,
                   {_geometry_sql('p.geom', detail)} AS geometry,
                   {SOURCE_COLUMNS}
            FROM plans p
            JOIN source_registry sr ON sr.source_id = p.source_id
            WHERE p.plan_number = :plan_number
            ORDER BY p.fetched_at DESC
            """
        ),
        {"plan_number": plan_number},
    ).mappings().all()
    items = [_plan_payload(row, include_geometry=detail != "none") for row in rows]
    return {"plan_number": plan_number, "status": "found" if items else "not_found", "items": items, "count": len(items)}


@router.get("/parcels")
def get_parcels(
    gush: str,
    helka: str,
    include_geometry: bool = False,
    geometry_detail: str = Query(default="simplified"),
    db: Session = Depends(get_gis_db),
) -> dict[str, Any]:
    gush = str(gush or "").strip()
    helka = str(helka or "").strip()
    if not gush or not helka:
        raise HTTPException(status_code=400, detail="parcel_query_requires_gush_and_helka")
    detail = _resolve_geometry_detail(include_geometry, geometry_detail)
    rows = db.execute(
        text(
            f"""
            SELECT p.id, p.source_id, p.provenance_id, p.source_object_id, p.gush, p.helka,
                   p.parcel_label, p.validation_status, p.validation_warnings,
                   {_geometry_sql('p.geom', detail)} AS geometry,
                   {SOURCE_COLUMNS}
            FROM parcels p
            JOIN source_registry sr ON sr.source_id = p.source_id
            WHERE p.gush = :gush AND p.helka = :helka
            ORDER BY p.fetched_at DESC
            """
        ),
        {"gush": gush, "helka": helka},
    ).mappings().all()
    items = [_parcel_payload(row, include_geometry=detail != "none") for row in rows]
    return {"query": {"gush": gush, "helka": helka}, "status": "found" if items else "not_found", "items": items, "count": len(items)}


@router.get("/search")
def search_gis(
    q: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_gis_db),
) -> dict[str, Any]:
    query = str(q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="search_query_required")

    items: list[dict[str, Any]] = []
    plan_number = _extract_plan_number(query)
    if plan_number:
        rows = _plan_search_rows(db, plan_number=plan_number, detail="centroid", limit=limit)
        items.extend(_search_item("plan", row, precision="exact_identifier") for row in rows)

    cadastral = extract_cadastral_id(query)
    if cadastral and len(items) < limit:
        rows = _parcel_search_rows(db, gush=cadastral[0], helka=cadastral[1], detail="bbox", limit=limit - len(items))
        items.extend(_search_item("parcel", row, precision="exact_identifier") for row in rows)

    if len(items) < limit:
        items.extend(_broad_search_items(db, query=query, limit=limit - len(items)))

    items = sorted(items, key=lambda item: (0 if item["precision"] == "exact_identifier" else 1, item["source_rank"], item["feature_type"]))[:limit]
    for item in items:
        item.pop("source_rank", None)
    return {"query": query, "items": items, "count": len(items)}


@router.get("/nearby")
def nearby_pois(
    lon: float,
    lat: float,
    radius_m: float = Query(default=500.0, gt=0, le=10000),
    categories: list[str] | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_gis_db),
) -> dict[str, Any]:
    category_values = _normalize_categories(categories)
    rows = _nearby_rows(db, lon=lon, lat=lat, radius_m=radius_m, categories=category_values, limit=limit)
    items = [_poi_payload(row) for row in rows]
    return {
        "point": {"lon": lon, "lat": lat},
        "radius_m": radius_m,
        "categories": category_values,
        "items": items,
        "count": len(items),
    }


@router.get("/point-report")
def point_report(lon: float, lat: float, db: Session = Depends(get_gis_db)) -> dict[str, Any]:
    municipality = municipality_for_point(db, lon=lon, lat=lat)
    if municipality is None:
        return {
            "point": {"lon": lon, "lat": lat},
            "municipality": {"status": "not_found", "message": "No ingested municipal boundary covers this point."},
            "layers": [],
        }

    source_row = db.execute(
        text(
            """
            SELECT source_id, name_he, name_en, provider_key, provenance_level, reuse_status,
                   display_status, source_is_official, reuse_is_verified, display_as_official,
                   attribution, license_name, license_url
            FROM source_registry
            WHERE source_id = :source_id
            """
        ),
        {"source_id": municipality["source_id"]},
    ).mappings().first()
    municipality_code = municipality["municipality_code"]
    parcel_rows = _features_covering_point(db, "parcels", lon=lon, lat=lat, detail="none", limit=1)
    plan_rows = _features_covering_point(db, "plans", lon=lon, lat=lat, detail="none", limit=10)
    neighborhood_rows = _features_covering_point(db, "neighborhoods", lon=lon, lat=lat, detail="none", limit=1)
    nearby_rows = _nearby_rows(db, lon=lon, lat=lat, radius_m=500.0, categories=None, limit=10)

    parcel_layer = _layer_result(
        db,
        municipality_code=municipality_code,
        layer_key="parcels",
        items=[_parcel_payload(row, include_geometry=False) for row in parcel_rows],
    )
    plans_layer = _layer_result(
        db,
        municipality_code=municipality_code,
        layer_key="plans",
        items=[_plan_payload(row, include_geometry=False) for row in plan_rows],
    )
    neighborhood_layer = _layer_result(
        db,
        municipality_code=municipality_code,
        layer_key="neighborhoods",
        items=[_neighborhood_payload(row) for row in neighborhood_rows],
    )
    nearby_layer = {
        "layer_key": "nearby_pois",
        "status": "found" if nearby_rows else "not_found",
        "items": [_poi_payload(row) for row in nearby_rows],
        "count": len(nearby_rows),
    }

    return {
        "point": {"lon": lon, "lat": lat},
        "municipality": {
            "status": "found",
            "municipality_code": municipality_code,
            "municipality_name_he": municipality["municipality_name_he"],
            "source_id": municipality["source_id"],
            "provenance_id": str(municipality["provenance_id"]),
            "source": source_fragment_from_row(source_row) if source_row else None,
        },
        "parcel": parcel_layer,
        "plans": plans_layer,
        "neighborhood": neighborhood_layer,
        "nearby_pois": nearby_layer,
        "layers": [parcel_layer, plans_layer, neighborhood_layer, nearby_layer],
    }


def _resolve_geometry_detail(include_geometry: bool, geometry_detail: str) -> str:
    detail = str(geometry_detail or "none").strip().lower()
    if detail not in GEOMETRY_DETAILS:
        raise HTTPException(status_code=422, detail="invalid_geometry_detail")
    return detail if include_geometry else "none"


def _extract_plan_number(query: str) -> str | None:
    match = re.search(r"\b\d{3,}-\d{3,}\b", query)
    return match.group(0) if match else None


def _plan_search_rows(db: Session, *, plan_number: str, detail: str, limit: int) -> list[Any]:
    return list(
        db.execute(
            text(
                f"""
                SELECT p.id, p.plan_number, p.plan_name, p.source_id, p.provenance_id,
                       p.validation_status, p.validation_warnings,
                       {_geometry_sql('p.geom', detail)} AS geometry,
                       {SOURCE_COLUMNS}
                FROM plans p
                JOIN source_registry sr ON sr.source_id = p.source_id
                WHERE p.plan_number = :plan_number {_provider_filter_sql('sr')}
                ORDER BY p.fetched_at DESC
                LIMIT :limit
                """
            ),
            {"plan_number": plan_number, "limit": limit},
        )
        .mappings()
        .all()
    )


def _parcel_search_rows(db: Session, *, gush: str, helka: str, detail: str, limit: int) -> list[Any]:
    return list(
        db.execute(
            text(
                f"""
                SELECT p.id, p.source_id, p.provenance_id, p.source_object_id, p.gush, p.helka,
                       p.parcel_label, p.validation_status, p.validation_warnings,
                       {_geometry_sql('p.geom', detail)} AS geometry,
                       {SOURCE_COLUMNS}
                FROM parcels p
                JOIN source_registry sr ON sr.source_id = p.source_id
                WHERE p.gush = :gush AND p.helka = :helka {_provider_filter_sql('sr')}
                ORDER BY p.fetched_at DESC
                LIMIT :limit
                """
            ),
            {"gush": gush, "helka": helka, "limit": limit},
        )
        .mappings()
        .all()
    )


def _broad_search_items(db: Session, *, query: str, limit: int) -> list[dict[str, Any]]:
    like = f"%{query.lower()}%"
    normalized_like = f"%{normalize_hebrew_text(query)}%"
    plan_rows = db.execute(
        text(
            f"""
            SELECT p.id, p.plan_number, p.plan_name, p.source_id, p.provenance_id,
                   p.validation_status, p.validation_warnings,
                   {_geometry_sql('p.geom', 'centroid')} AS geometry,
                   {SOURCE_COLUMNS}
            FROM plans p
            JOIN source_registry sr ON sr.source_id = p.source_id
            WHERE (
              lower(COALESCE(p.plan_number, '') || ' ' || COALESCE(p.plan_name, '')) LIKE :like
              OR lower(COALESCE(p.plan_number, '') || ' ' || COALESCE(p.plan_name, '')) LIKE :normalized_like
            ) {_provider_filter_sql('sr')}
            ORDER BY {_official_rank_sql('sr')}, p.fetched_at DESC
            LIMIT :limit
            """
        ),
        {"like": like, "normalized_like": normalized_like, "limit": limit},
    ).mappings().all()
    remaining = max(limit - len(plan_rows), 0)
    poi_rows = []
    if remaining:
        poi_rows = db.execute(
            text(
                f"""
                SELECT p.id, 'poi_points' AS feature_table, p.source_id, p.provenance_id,
                       p.source_object_id, p.poi_category, p.name_he, p.name_en, p.official_identifier,
                       p.validation_status, p.validation_warnings, ST_AsGeoJSON(p.geom) AS geometry,
                       {SOURCE_COLUMNS}
                FROM poi_points p
                JOIN source_registry sr ON sr.source_id = p.source_id
                WHERE (
                  lower(COALESCE(p.name_he, '') || ' ' || COALESCE(p.name_en, '') || ' ' || COALESCE(p.official_identifier, '')) LIKE :like
                  OR lower(COALESCE(p.name_he, '') || ' ' || COALESCE(p.name_en, '') || ' ' || COALESCE(p.official_identifier, '')) LIKE :normalized_like
                ) {_provider_filter_sql('sr')}
                ORDER BY {_official_rank_sql('sr')}, p.fetched_at DESC
                LIMIT :limit
                """
            ),
            {"like": like, "normalized_like": normalized_like, "limit": remaining},
        ).mappings().all()
    items = [_search_item("plan", row, precision="text_match") for row in plan_rows]
    items.extend(_search_item("poi", row, precision="text_match") for row in poi_rows)
    return sorted(items, key=lambda item: (item["source_rank"], item["feature_type"]))


def _features_covering_point(db: Session, table_name: str, *, lon: float, lat: float, detail: str, limit: int) -> list[Any]:
    if table_name == "parcels":
        select_columns = f"""
        p.id, p.source_id, p.provenance_id, p.source_object_id, p.gush, p.helka,
        p.parcel_label, p.validation_status, p.validation_warnings,
        {_geometry_sql('p.geom', detail)} AS geometry, {SOURCE_COLUMNS}
        """
    elif table_name == "plans":
        select_columns = f"""
        p.id, p.plan_number, p.plan_name, p.source_id, p.provenance_id,
        p.validation_status, p.validation_warnings,
        {_geometry_sql('p.geom', detail)} AS geometry, {SOURCE_COLUMNS}
        """
    elif table_name == "neighborhoods":
        select_columns = f"""
        p.id, p.source_id, p.provenance_id, p.municipality_code, p.name_he, p.name_en,
        p.validation_status, p.validation_warnings,
        {_geometry_sql('p.geom', detail)} AS geometry, {SOURCE_COLUMNS}
        """
    else:
        raise ValueError(f"unsupported point feature table: {table_name}")

    return list(
        db.execute(
            text(
                f"""
                SELECT {select_columns}
                FROM {table_name} p
                JOIN source_registry sr ON sr.source_id = p.source_id
                WHERE ST_Covers(p.geom, ST_SetSRID(ST_Point(:lon, :lat), 4326)) {_provider_filter_sql('sr')}
                ORDER BY p.fetched_at DESC
                LIMIT :limit
                """
            ),
            {"lon": lon, "lat": lat, "limit": limit},
        )
        .mappings()
        .all()
    )


def _nearby_rows(
    db: Session,
    *,
    lon: float,
    lat: float,
    radius_m: float,
    categories: list[str] | None,
    limit: int,
) -> list[Any]:
    category_placeholders = _category_placeholders(categories)
    category_params = {f"category_{index}": category for index, category in enumerate(categories or [])}
    poi_category_filter = f"AND p.poi_category IN ({category_placeholders})" if category_placeholders else ""
    context_category_filter = f"AND c.poi_category IN ({category_placeholders})" if category_placeholders else ""
    point_2039 = "ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)"
    rows = db.execute(
        text(
            f"""
            SELECT * FROM (
              SELECT 'poi_points' AS feature_table, p.id, p.source_id, p.provenance_id,
                     p.source_object_id, p.poi_category, p.name_he, p.name_en, p.official_identifier,
                     p.validation_status, p.validation_warnings,
                     ST_Distance(p.geom_2039, {point_2039}) AS distance_m,
                     {_official_rank_sql('sr')} AS official_rank,
                     {SOURCE_COLUMNS}
              FROM poi_points p
              JOIN source_registry sr ON sr.source_id = p.source_id
              WHERE ST_DWithin(p.geom_2039, {point_2039}, :radius_m)
                {poi_category_filter} {_provider_filter_sql('sr')}
              UNION ALL
              SELECT 'context_pois' AS feature_table, c.id, c.source_id, c.provenance_id,
                     c.source_object_id, c.poi_category, c.name AS name_he, NULL AS name_en, NULL AS official_identifier,
                     c.validation_status, c.validation_warnings,
                     ST_Distance(c.geom_2039, {point_2039}) AS distance_m,
                     {_official_rank_sql('sr')} AS official_rank,
                     {SOURCE_COLUMNS}
              FROM context_pois c
              JOIN source_registry sr ON sr.source_id = c.source_id
              WHERE ST_DWithin(c.geom_2039, {point_2039}, :radius_m)
                {context_category_filter} {_provider_filter_sql('sr')}
            ) nearby
            ORDER BY official_rank, distance_m
            LIMIT :limit
            """
        ),
        {"lon": lon, "lat": lat, "radius_m": radius_m, "limit": limit, **category_params},
    ).mappings().all()
    return list(rows)


def _layer_result(db: Session, *, municipality_code: str, layer_key: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    if items:
        return {"layer_key": layer_key, "status": "found", "items": items, "count": len(items)}
    coverage = _coverage_for_layer(db, municipality_code=municipality_code, layer_key=layer_key)
    status = str(coverage["status"]) if coverage else "not_found"
    if status in {"national_available", "municipal_open_available", "context_available"}:
        status = "not_found"
    return {
        "layer_key": layer_key,
        "status": status,
        "message": _status_message(status, layer_key),
        "source_id": coverage.get("source_id") if coverage else None,
        "source": source_fragment_from_row(coverage) if coverage and coverage.get("source_id") else None,
        "items": [],
        "count": 0,
    }


def _coverage_for_layer(db: Session, *, municipality_code: str, layer_key: str) -> Any | None:
    return db.execute(
        text(
            f"""
            SELECT lc.municipality_code, lc.municipality_name_he, lc.layer_key, lc.status,
                   lc.source_id, lc.feature_count, lc.last_successful_ingest_at, lc.updated_at,
                   {SOURCE_COLUMNS}
            FROM layer_coverage lc
            LEFT JOIN source_registry sr ON sr.source_id = lc.source_id
            WHERE lc.municipality_code = :municipality_code AND lc.layer_key = :layer_key
            """
        ),
        {"municipality_code": municipality_code, "layer_key": layer_key},
    ).mappings().first()


def _search_item(feature_type: str, row: Any, *, precision: str) -> dict[str, Any]:
    geometry = _geometry_from_row(row)
    item = {
        "feature_type": feature_type,
        "precision": precision,
        "can_persist": _can_persist(row),
        "id": str(row["id"]),
        "source_id": row["source_id"],
        "provenance_id": str(row["provenance_id"]),
        "source": source_fragment_from_row(row),
        "source_rank": _source_rank(row),
    }
    if feature_type == "plan":
        item.update({"title": row.get("plan_name") or row.get("plan_number"), "plan_number": row.get("plan_number")})
    elif feature_type == "parcel":
        item.update({"title": row.get("parcel_label"), "gush": row.get("gush"), "helka": row.get("helka")})
    else:
        item.update({"title": row.get("name_he") or row.get("name_en"), "poi_category": row.get("poi_category")})
    if geometry is not None:
        item["geometry"] = geometry
    return item


def _parcel_payload(row: Any, *, include_geometry: bool) -> dict[str, Any]:
    payload = {
        "id": str(row["id"]),
        "source_id": row["source_id"],
        "provenance_id": str(row["provenance_id"]),
        "source": source_fragment_from_row(row),
        "source_object_id": row.get("source_object_id"),
        "gush": row.get("gush"),
        "helka": row.get("helka"),
        "parcel_label": row.get("parcel_label"),
        "validation_status": str(row["validation_status"]),
        "validation_warnings": _normalize_json_list(row.get("validation_warnings")),
    }
    _add_geometry(payload, row, include_geometry=include_geometry)
    return payload


def _neighborhood_payload(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "source_id": row["source_id"],
        "provenance_id": str(row["provenance_id"]),
        "source": source_fragment_from_row(row),
        "municipality_code": row.get("municipality_code"),
        "name_he": row.get("name_he"),
        "name_en": row.get("name_en"),
        "validation_status": str(row["validation_status"]),
        "validation_warnings": _normalize_json_list(row.get("validation_warnings")),
    }


def _poi_payload(row: Any) -> dict[str, Any]:
    payload = {
        "id": str(row["id"]),
        "feature_table": row.get("feature_table", "poi_points"),
        "source_id": row["source_id"],
        "provenance_id": str(row["provenance_id"]),
        "source": source_fragment_from_row(row),
        "source_object_id": row.get("source_object_id"),
        "poi_category": row.get("poi_category"),
        "name_he": row.get("name_he"),
        "name_en": row.get("name_en"),
        "official_identifier": row.get("official_identifier"),
        "validation_status": str(row["validation_status"]),
        "validation_warnings": _normalize_json_list(row.get("validation_warnings")),
    }
    if row.get("distance_m") is not None:
        payload["distance_m"] = float(row["distance_m"])
    return payload


def _add_geometry(payload: dict[str, Any], row: Any, *, include_geometry: bool) -> None:
    if not include_geometry:
        return
    geometry = _geometry_from_row(row)
    if geometry is not None:
        payload["geometry"] = geometry


def _geometry_from_row(row: Any) -> dict[str, Any] | None:
    value = row.get("geometry")
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _normalize_categories(categories: list[str] | None) -> list[str] | None:
    values: list[str] = []
    for category in categories or []:
        values.extend(part.strip() for part in str(category).split(",") if part.strip())
    return values or None


def _category_placeholders(categories: list[str] | None) -> str:
    return ", ".join(f":category_{index}" for index, _ in enumerate(categories or []))


def _provider_filter_sql(alias: str) -> str:
    if os.getenv("ENABLE_GOOGLE_TEMPORARY_SEARCH", "false").lower() == "true":
        return ""
    return f"AND {alias}.provider_key <> 'google'"


def _official_rank_sql(alias: str) -> str:
    return f"""
    CASE
      WHEN {alias}.provenance_level IN ('official_national', 'official_municipal') THEN 0
      WHEN {alias}.provenance_level = 'municipal' THEN 1
      WHEN {alias}.provenance_level = 'context' THEN 2
      ELSE 3
    END
    """


def _source_rank(row: Any) -> int:
    provenance_level = str(row.get("provenance_level") or "")
    if provenance_level in {"official_national", "official_municipal"}:
        return 0
    if provenance_level == "municipal":
        return 1
    if provenance_level == "context":
        return 2
    return 3


def _can_persist(row: Any) -> bool:
    return str(row.get("provider_key") or "").lower() != "google" and str(row.get("display_status") or "") != "temporary_input_only"


def _status_message(status: str, layer_key: str) -> str:
    messages = {
        "not_found": f"No {layer_key} data was found for this municipality.",
        "not_ingested": f"{layer_key} data source is registered but has not been ingested.",
        "not_enabled": f"{layer_key} data source is known but not enabled.",
        "municipal_license_under_review": f"{layer_key} data exists but municipal license review is still open.",
    }
    return messages.get(status, f"{layer_key} data is unavailable: {status}.")


def _coverage_rows(db: Session) -> list[Any]:
    return list(
        db.execute(
            text(
                """
                SELECT lc.municipality_code, lc.municipality_name_he, lc.layer_key, lc.status,
                       lc.source_id, lc.feature_count, lc.last_successful_ingest_at, lc.updated_at,
                       sr.name_he, sr.name_en, sr.provider_key, sr.provenance_level, sr.reuse_status,
                       sr.display_status, sr.source_is_official, sr.reuse_is_verified,
                       sr.display_as_official, sr.attribution, sr.license_name, sr.license_url
                FROM layer_coverage lc
                LEFT JOIN source_registry sr ON sr.source_id = lc.source_id
                ORDER BY lc.municipality_code, lc.layer_key
                """
            )
        )
        .mappings()
        .all()
    )


def _plan_payload(row: Any, *, include_geometry: bool) -> dict[str, Any]:
    payload = {
        "id": str(row["id"]),
        "plan_number": row["plan_number"],
        "plan_name": row.get("plan_name"),
        "source_id": row["source_id"],
        "provenance_id": str(row["provenance_id"]),
        "source": source_fragment_from_row(row),
        "validation_status": str(row["validation_status"]),
        "validation_warnings": _normalize_json_list(row.get("validation_warnings")),
    }
    if include_geometry and row.get("geometry") is not None:
        payload["geometry"] = json.loads(row["geometry"])
    return payload


def _geometry_sql(column: str, detail: str) -> str:
    if detail == "centroid":
        return f"ST_AsGeoJSON(ST_Centroid({column}))"
    if detail == "bbox":
        return f"ST_AsGeoJSON(ST_Envelope({column}))"
    if detail == "simplified":
        return f"ST_AsGeoJSON(ST_SimplifyPreserveTopology({column}, 0.0001))"
    if detail == "full":
        return f"ST_AsGeoJSON({column})"
    return "NULL"


def _source_payload(row: Any) -> dict[str, Any]:
    return {
        "source_id": row["source_id"],
        "provider_key": row["provider_key"],
        "source_kind": row["source_kind"],
        "name_he": row["name_he"],
        "name_en": row.get("name_en"),
        "owner_name": row.get("owner_name"),
        "owner_url": row.get("owner_url"),
        "source_url": row.get("source_url"),
        "municipality_code": row.get("municipality_code"),
        "layer_keys": _normalize_list(row.get("layer_keys")),
        "provenance_level": str(row["provenance_level"]),
        "reuse_status": str(row["reuse_status"]),
        "geometry_status": str(row["geometry_status"]),
        "display_status": str(row["display_status"]),
        "source_is_official": bool(row["source_is_official"]),
        "reuse_is_verified": bool(row["reuse_is_verified"]),
        "display_as_official": bool(row["display_as_official"]),
        "legal_review_approved": bool(row["legal_review_approved"]),
        "enabled": bool(row["enabled"]),
        "license_name": row.get("license_name"),
        "license_url": row.get("license_url"),
        "attribution": row.get("attribution"),
        "notes": row.get("notes"),
        "metadata": _normalize_metadata(row.get("metadata")),
    }


def _coverage_payload(row: Any) -> dict[str, Any]:
    source = source_fragment_from_row(row) if row.get("source_id") else None
    return {
        "municipality_code": row["municipality_code"],
        "municipality_name_he": row.get("municipality_name_he"),
        "layer_key": row["layer_key"],
        "status": str(row["status"]),
        "source_id": row.get("source_id"),
        "source": source,
        "feature_count": int(row.get("feature_count") or 0),
        "last_successful_ingest_at": _string_or_none(row.get("last_successful_ingest_at")),
        "updated_at": _string_or_none(row.get("updated_at")),
    }


def _source_matches(
    item: dict[str, Any],
    enabled: bool | None,
    provider_key: str | None,
    provenance_level: str | None,
    reuse_status: str | None,
    display_status: str | None,
    municipality_code: str | None,
    layer_key: str | None,
) -> bool:
    return all(
        (
            enabled is None or item["enabled"] == enabled,
            provider_key is None or item["provider_key"] == provider_key,
            provenance_level is None or item["provenance_level"] == provenance_level,
            reuse_status is None or item["reuse_status"] == reuse_status,
            display_status is None or item["display_status"] == display_status,
            municipality_code is None or item.get("municipality_code") == municipality_code,
            layer_key is None or layer_key in item["layer_keys"],
        )
    )


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip("{}")
        if not stripped:
            return []
        return [item.strip().strip('"') for item in stripped.split(",")]
    return [str(value)]


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


def _normalize_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
