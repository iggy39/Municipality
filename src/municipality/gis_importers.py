from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.orm import Session

from municipality.gis_geometry import geometry_hash, normalize_polygon_geometry, validate_geojson_geometry
from municipality.gis_raw_storage import ImmutableRawStorage

JsonFetcher = Callable[[str, dict[str, Any]], dict[str, Any]]
JsonPoster = Callable[[str, dict[str, Any]], Any]

GOVMAP_PARCEL_AUTOCOMPLETE_URL = "https://www.govmap.gov.il/api/search-service/parcel/autocomplete"
GOVMAP_GET_SHAPE_URL = "https://www.govmap.gov.il/api/search-service/getShape"


@dataclass(frozen=True)
class ImportSummary:
    inserted_or_updated: int
    rejected: int = 0
    warnings: list[str] | None = None


def normalize_municipality_code(value: Any) -> str:
    text_value = str(value or "").strip()
    digits = "".join(ch for ch in text_value if ch.isdigit())
    return digits.zfill(4) if digits else text_value


def import_municipal_boundaries_geojson(
    session: Session,
    *,
    source_id: str,
    features: Iterable[Mapping[str, Any]],
    code_field: str,
    name_he_field: str,
    raw_storage_uri: str | None = None,
) -> ImportSummary:
    count = 0
    rejected = 0
    warnings: list[str] = []
    grouped_features, grouping_warnings = _group_boundary_features(features, code_field=code_field, name_he_field=name_he_field)
    warnings.extend(grouping_warnings)
    for boundary in grouped_features:
        properties = boundary["properties"]
        municipality_code = str(boundary["municipality_code"])
        municipality_name_he = str(boundary["municipality_name_he"])
        geometry = boundary["geometry"]
        result = validate_geojson_geometry(geometry, expected_type="MultiPolygon")
        if result.status == "invalid" or not municipality_code or not municipality_name_he:
            rejected += 1
            warnings.extend(result.warnings or ["missing_boundary_identifier"])
            continue
        geom_hash = geometry_hash(geometry)
        provenance_id = _get_or_create_provenance(
            session,
            canonical_table="official_municipal_boundaries",
            source_id=source_id,
            source_key=municipality_code,
            raw_storage_uri=raw_storage_uri,
            geom_hash=geom_hash,
            metadata={"properties": properties, "source_feature_count": boundary["source_feature_count"]},
        )
        session.execute(
            text(
                """
                INSERT INTO official_municipal_boundaries (
                  source_id, provenance_id, municipality_code, municipality_name_he,
                  municipality_name_en, normalized_geometry_hash, geom, geom_2039,
                  validation_status, validation_warnings, metadata
                ) VALUES (
                  :source_id, :provenance_id, :municipality_code, :municipality_name_he,
                  :municipality_name_en, :normalized_geometry_hash,
                  ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry))::geometry(MultiPolygon, 4326),
                  ST_Transform(ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry)), 2039)::geometry(MultiPolygon, 2039),
                  :validation_status, CAST(:validation_warnings AS jsonb), CAST(:metadata AS jsonb)
                )
                ON CONFLICT (source_id, municipality_code) DO UPDATE SET
                  provenance_id = EXCLUDED.provenance_id,
                  municipality_name_he = EXCLUDED.municipality_name_he,
                  municipality_name_en = EXCLUDED.municipality_name_en,
                  normalized_geometry_hash = EXCLUDED.normalized_geometry_hash,
                  geom = EXCLUDED.geom,
                  geom_2039 = EXCLUDED.geom_2039,
                  validation_status = EXCLUDED.validation_status,
                  validation_warnings = EXCLUDED.validation_warnings,
                  metadata = EXCLUDED.metadata,
                  fetched_at = now(),
                  updated_at = now()
                """
            ),
            {
                "source_id": source_id,
                "provenance_id": provenance_id,
                "municipality_code": municipality_code,
                "municipality_name_he": municipality_name_he,
                "municipality_name_en": properties.get("name_en") or properties.get("NAME_EN") or properties.get("Muni_Eng"),
                "normalized_geometry_hash": geom_hash,
                "geometry": json.dumps(geometry),
                "validation_status": result.status,
                "validation_warnings": json.dumps(result.warnings, ensure_ascii=False),
                "metadata": json.dumps({"properties": properties, "source_feature_count": boundary["source_feature_count"]}, ensure_ascii=False),
            },
        )
        _upsert_coverage(session, municipality_code, municipality_name_he, "boundaries", "national_available", source_id, 1)
        count += 1
    return ImportSummary(count, rejected, _dedupe(warnings))


def import_municipal_boundaries_geojson_postgis(
    session: Session,
    *,
    source_id: str,
    features: Iterable[Mapping[str, Any]],
    code_field: str,
    name_he_field: str,
    raw_storage_uri: str | None = None,
) -> ImportSummary:
    session.execute(
        text(
            """
            CREATE TEMP TABLE IF NOT EXISTS tmp_municipal_boundaries_import (
              municipality_code text NOT NULL,
              municipality_name_he text NOT NULL,
              municipality_name_en text,
              geometry_json text NOT NULL,
              properties jsonb NOT NULL
            ) ON COMMIT DROP
            """
        )
    )
    session.execute(text("TRUNCATE tmp_municipal_boundaries_import"))

    staged = 0
    rejected = 0
    warnings: list[str] = []
    for feature in features:
        properties = _properties(feature)
        municipality_code = normalize_municipality_code(properties.get(code_field))
        municipality_name_he = str(properties.get(name_he_field) or "").strip()
        geometry = normalize_polygon_geometry(_geometry(feature))
        if not municipality_code or not municipality_name_he or _is_empty_geometry(geometry):
            rejected += 1
            warnings.append("missing_boundary_identifier_or_geometry")
            continue
        session.execute(
            text(
                """
                INSERT INTO tmp_municipal_boundaries_import (
                  municipality_code, municipality_name_he, municipality_name_en,
                  geometry_json, properties
                ) VALUES (
                  :municipality_code, :municipality_name_he, :municipality_name_en,
                  :geometry_json, CAST(:properties AS jsonb)
                )
                """
            ),
            {
                "municipality_code": municipality_code,
                "municipality_name_he": municipality_name_he,
                "municipality_name_en": properties.get("name_en") or properties.get("NAME_EN") or properties.get("Muni_Eng"),
                "geometry_json": json.dumps(geometry),
                "properties": json.dumps(properties, ensure_ascii=False, default=str),
            },
        )
        staged += 1

    rows = session.execute(
        text(
            """
            WITH source_geometries AS (
              SELECT municipality_code, municipality_name_he, municipality_name_en, properties,
                     ST_SetSRID(ST_GeomFromGeoJSON(geometry_json), 4326) AS geom
              FROM tmp_municipal_boundaries_import
            ), grouped AS (
              SELECT municipality_code,
                     min(municipality_name_he) AS municipality_name_he,
                     min(municipality_name_en) AS municipality_name_en,
                     jsonb_agg(properties) AS source_properties,
                     count(*) AS source_feature_count,
                     ST_Multi(ST_CollectionExtract(ST_UnaryUnion(ST_Collect(ST_MakeValid(geom))), 3))::geometry(MultiPolygon, 4326) AS geom
              FROM source_geometries
              GROUP BY municipality_code
            )
            SELECT municipality_code, municipality_name_he, municipality_name_en,
                   source_properties, source_feature_count,
                   ST_AsGeoJSON(geom)::text AS geometry,
                   md5(ST_AsEWKB(geom)) AS geometry_hash,
                   ST_IsValid(geom) AS is_valid
            FROM grouped
            WHERE NOT ST_IsEmpty(geom)
            ORDER BY municipality_code
            """
        )
    ).mappings().all()

    count = 0
    for row in rows:
        metadata = {
            "source_properties": row["source_properties"],
            "source_feature_count": row["source_feature_count"],
            "postgis_grouped_import": True,
        }
        provenance_id = _get_or_create_provenance(
            session,
            canonical_table="official_municipal_boundaries",
            source_id=source_id,
            source_key=str(row["municipality_code"]),
            raw_storage_uri=raw_storage_uri,
            geom_hash=str(row["geometry_hash"]),
            metadata=metadata,
        )
        validation_warnings = [] if bool(row["is_valid"]) else ["postgis_geometry_invalid_after_makevalid"]
        validation_status = "valid" if bool(row["is_valid"]) else "warning"
        session.execute(
            text(
                """
                INSERT INTO official_municipal_boundaries (
                  source_id, provenance_id, municipality_code, municipality_name_he,
                  municipality_name_en, normalized_geometry_hash, geom, geom_2039,
                  validation_status, validation_warnings, metadata
                ) VALUES (
                  :source_id, :provenance_id, :municipality_code, :municipality_name_he,
                  :municipality_name_en, :normalized_geometry_hash,
                  ST_Multi(ST_CollectionExtract(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326), 3))::geometry(MultiPolygon, 4326),
                  ST_Transform(ST_Multi(ST_CollectionExtract(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326), 3)), 2039)::geometry(MultiPolygon, 2039),
                  :validation_status, CAST(:validation_warnings AS jsonb), CAST(:metadata AS jsonb)
                )
                ON CONFLICT (source_id, municipality_code) DO UPDATE SET
                  provenance_id = EXCLUDED.provenance_id,
                  municipality_name_he = EXCLUDED.municipality_name_he,
                  municipality_name_en = EXCLUDED.municipality_name_en,
                  normalized_geometry_hash = EXCLUDED.normalized_geometry_hash,
                  geom = EXCLUDED.geom,
                  geom_2039 = EXCLUDED.geom_2039,
                  validation_status = EXCLUDED.validation_status,
                  validation_warnings = EXCLUDED.validation_warnings,
                  metadata = EXCLUDED.metadata,
                  fetched_at = now(),
                  updated_at = now()
                """
            ),
            {
                "source_id": source_id,
                "provenance_id": provenance_id,
                "municipality_code": row["municipality_code"],
                "municipality_name_he": row["municipality_name_he"],
                "municipality_name_en": row["municipality_name_en"],
                "normalized_geometry_hash": row["geometry_hash"],
                "geometry": row["geometry"],
                "validation_status": validation_status,
                "validation_warnings": json.dumps(validation_warnings, ensure_ascii=False),
                "metadata": json.dumps(metadata, ensure_ascii=False, default=str),
            },
        )
        _upsert_coverage(session, row["municipality_code"], row["municipality_name_he"], "boundaries", "national_available", source_id, 1)
        count += 1
    return ImportSummary(count, rejected, _dedupe(warnings + ([] if staged else ["no_boundary_features_staged"])))


def _group_boundary_features(
    features: Iterable[Mapping[str, Any]],
    *,
    code_field: str,
    name_he_field: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    grouped: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for feature in features:
        properties = _properties(feature)
        municipality_code = normalize_municipality_code(properties.get(code_field))
        municipality_name_he = str(properties.get(name_he_field) or "").strip()
        if not municipality_code or not municipality_name_he:
            warnings.append("missing_boundary_identifier")
            continue
        geometry = normalize_polygon_geometry(_geometry(feature))
        result = validate_geojson_geometry(geometry, expected_type="MultiPolygon")
        if result.status == "invalid":
            warnings.extend(result.warnings or ["invalid_boundary_geometry"])
            continue
        if municipality_code not in grouped:
            grouped[municipality_code] = {
                "municipality_code": municipality_code,
                "municipality_name_he": municipality_name_he,
                "properties": properties,
                "geometry": {"type": "MultiPolygon", "coordinates": []},
                "source_feature_count": 0,
            }
        grouped_boundary = grouped[municipality_code]
        grouped_boundary["geometry"]["coordinates"].extend(geometry.get("coordinates") or [])
        grouped_boundary["source_feature_count"] += 1
        if grouped_boundary["municipality_name_he"] != municipality_name_he:
            warnings.append("boundary_name_mismatch_for_code")
    return list(grouped.values()), _dedupe(warnings)


def _is_empty_geometry(geometry: Mapping[str, Any]) -> bool:
    coordinates = geometry.get("coordinates")
    if coordinates is None or coordinates == []:
        return True
    if isinstance(coordinates, list):
        return all(_is_empty_geometry({"coordinates": item}) for item in coordinates)
    return False


def municipality_for_point(session: Session, *, lon: float, lat: float) -> dict[str, Any] | None:
    row = session.execute(
        text(
            """
            SELECT municipality_code, municipality_name_he, source_id, provenance_id
            FROM official_municipal_boundaries
            WHERE ST_Covers(geom, ST_SetSRID(ST_Point(:lon, :lat), 4326))
            ORDER BY fetched_at DESC
            LIMIT 1
            """
        ),
        {"lon": lon, "lat": lat},
    ).mappings().first()
    return dict(row) if row else None


def fetch_arcgis_feature_pages(
    *,
    service_url: str,
    fetch_json: JsonFetcher,
    page_size: int = 1000,
    where: str = "1=1",
    query_params: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {
            "f": "geojson",
            "where": where,
            "outFields": "*",
            "returnGeometry": "true",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        }
        if query_params:
            params.update({key: value for key, value in query_params.items() if value is not None})
        page = fetch_json(
            service_url.rstrip("/") + "/query",
            params,
        )
        pages.append(page)
        features = page.get("features") or []
        if len(features) < page_size or not page.get("exceededTransferLimit"):
            break
        offset += page_size
    return pages


def import_xplan_arcgis_plans(
    session: Session,
    *,
    source_id: str,
    service_url: str,
    fetch_json: JsonFetcher,
    raw_storage: ImmutableRawStorage,
    plan_number_field: str = "plan_number",
    plan_name_field: str = "plan_name",
    page_size: int = 1000,
    where: str = "1=1",
) -> ImportSummary:
    pages = fetch_arcgis_feature_pages(service_url=service_url, fetch_json=fetch_json, page_size=page_size, where=where)
    count = 0
    rejected = 0
    warnings: list[str] = []
    for page_index, page in enumerate(pages):
        raw_uri = raw_storage.write_json(source_id=source_id, name=f"xplan_page_{page_index}.geojson", payload=page)
        for feature in page.get("features") or []:
            properties = _properties(feature)
            plan_number = str(properties.get(plan_number_field) or "").strip()
            geometry = normalize_polygon_geometry(_geometry(feature))
            result = validate_geojson_geometry(geometry, expected_type="MultiPolygon")
            if result.status == "invalid" or not plan_number:
                rejected += 1
                warnings.extend(result.warnings or ["missing_plan_number"])
                continue
            geom_hash = geometry_hash(geometry)
            source_key = f"{source_id}:{plan_number}:{geom_hash}"
            provenance_id = _get_or_create_provenance(
                session,
                canonical_table="plans",
                source_id=source_id,
                source_key=source_key,
                raw_storage_uri=raw_uri,
                geom_hash=geom_hash,
                metadata={"properties": properties},
            )
            session.execute(
                text(
                    """
                    INSERT INTO plans (
                      source_id, provenance_id, plan_number, plan_name, normalized_geometry_hash,
                      geom, geom_2039, validation_status, validation_warnings, metadata
                    ) VALUES (
                      :source_id, :provenance_id, :plan_number, :plan_name, :normalized_geometry_hash,
                      ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry))::geometry(MultiPolygon, 4326),
                      ST_Transform(ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry)), 2039)::geometry(MultiPolygon, 2039),
                      :validation_status, CAST(:validation_warnings AS jsonb), CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT (source_id, plan_number, normalized_geometry_hash) DO UPDATE SET
                      provenance_id = EXCLUDED.provenance_id,
                      plan_name = EXCLUDED.plan_name,
                      geom = EXCLUDED.geom,
                      geom_2039 = EXCLUDED.geom_2039,
                      validation_status = EXCLUDED.validation_status,
                      validation_warnings = EXCLUDED.validation_warnings,
                      metadata = EXCLUDED.metadata,
                      fetched_at = now(),
                      updated_at = now()
                    """
                ),
                _polygon_params(source_id, provenance_id, plan_number, properties.get(plan_name_field), geom_hash, geometry, result, properties),
            )
            count += 1
    _refresh_national_polygon_coverage(session, source_id=source_id, layer_key="plans", table_name="plans")
    return ImportSummary(count, rejected, _dedupe(warnings))


def inspect_mapi_zip(zip_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(zip_path) as archive:
        shp_names = [name for name in archive.namelist() if name.lower().endswith(".shp")]
    if not shp_names:
        raise ValueError("MAPI ZIP does not contain a .shp file")
    return {"shapefiles": shp_names}


def resolve_parcel_fields(fields: Iterable[str]) -> dict[str, str | None]:
    field_list = list(fields)
    return {
        "source_object_id": _first_field(field_list, ["objectid", "object_id", "fid", "id", "mslink"]),
        "gush": _first_field(field_list, ["gush", "gush_num", "gushno", "gush_numbe", "גוש"]),
        "helka": _first_field(field_list, ["helka", "parcel", "parcel_num", "helka_num", "חלקה"]),
    }


def import_mapi_parcel_zip(
    session: Session,
    *,
    source_id: str,
    zip_path: Path,
    raw_storage: ImmutableRawStorage,
    input_srid: int = 2039,
    gush_filter: str | None = None,
    helka_filter: str | None = None,
    boundary_bbox_filter: tuple[float, float, float, float] | None = None,
    limit: int | None = None,
    encoding: str = "cp1255",
) -> ImportSummary:
    import shapefile

    raw_uri = raw_storage.write_bytes(source_id=source_id, name=zip_path.name, content=zip_path.read_bytes())
    with tempfile.TemporaryDirectory() as tmp_dir:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(tmp_dir)
        shp_path = next(Path(tmp_dir).rglob("*.shp"), None)
        if shp_path is None:
            raise ValueError("MAPI ZIP does not contain a .shp file")
        reader = shapefile.Reader(str(shp_path), encoding=encoding)
        summary = _import_mapi_parcel_reader(
            session,
            source_id=source_id,
            reader=reader,
            raw_uri=raw_uri,
            input_srid=input_srid,
            gush_filter=gush_filter,
            helka_filter=helka_filter,
            boundary_bbox_filter=boundary_bbox_filter,
            limit=limit,
        )
    _refresh_national_polygon_coverage(session, source_id=source_id, layer_key="parcels", table_name="parcels")
    return summary


def import_mapi_parcel_directory(
    session: Session,
    *,
    source_id: str,
    directory_path: Path,
    raw_storage: ImmutableRawStorage,
    input_srid: int = 2039,
    gush_filter: str | None = None,
    helka_filter: str | None = None,
    boundary_bbox_filter: tuple[float, float, float, float] | None = None,
    limit: int | None = None,
    encoding: str = "cp1255",
) -> ImportSummary:
    import shapefile

    shp_path = next(directory_path.glob("*.shp"), None)
    if shp_path is None:
        raise ValueError("MAPI directory does not contain a .shp file")
    raw_uri = raw_storage.write_json(
        source_id=source_id,
        name=f"{directory_path.name}_manifest.json",
        payload={"directory_path": str(directory_path), "files": sorted(path.name for path in directory_path.iterdir())},
    )
    reader = shapefile.Reader(str(shp_path), encoding=encoding)
    summary = _import_mapi_parcel_reader(
        session,
        source_id=source_id,
        reader=reader,
        raw_uri=raw_uri,
        input_srid=input_srid,
        gush_filter=gush_filter,
        helka_filter=helka_filter,
        boundary_bbox_filter=boundary_bbox_filter,
        limit=limit,
    )
    _refresh_national_polygon_coverage(session, source_id=source_id, layer_key="parcels", table_name="parcels")
    return summary


def _import_mapi_parcel_reader(
    session: Session,
    *,
    source_id: str,
    reader: Any,
    raw_uri: str,
    input_srid: int,
    gush_filter: str | None,
    helka_filter: str | None,
    boundary_bbox_filter: tuple[float, float, float, float] | None,
    limit: int | None,
) -> ImportSummary:
    fields = [field[0] for field in reader.fields[1:]]
    mapping = resolve_parcel_fields(fields)
    target_gush = _normalize_parcel_number(gush_filter) if gush_filter else None
    target_helka = _normalize_parcel_number(helka_filter) if helka_filter else None
    count = 0
    rejected = 0
    warnings: list[str] = []
    for shape_record in reader.iterShapeRecords():
        if limit is not None and count >= limit:
            break
        properties = dict(zip(fields, shape_record.record))
        gush = _normalize_parcel_number(_field_value(properties, mapping["gush"]))
        helka = _normalize_parcel_number(_field_value(properties, mapping["helka"]))
        if target_gush is not None and gush != target_gush:
            continue
        if target_helka is not None and helka != target_helka:
            continue
        if boundary_bbox_filter is not None and not _shape_bbox_intersects(shape_record.shape.bbox, boundary_bbox_filter):
            continue
        source_object_id = _field_value(properties, mapping["source_object_id"])
        source_geometry = normalize_polygon_geometry(shape_record.shape.__geo_interface__)
        geometry = transform_geojson_geometry(source_geometry, source_srid=input_srid, target_srid=4326)
        result = validate_geojson_geometry(geometry, expected_type="MultiPolygon")
        if result.status == "invalid" or (not source_object_id and (not gush or not helka)):
            rejected += 1
            warnings.extend(result.warnings or ["missing_parcel_identifier"])
            continue
        geom_hash = geometry_hash(geometry)
        source_key = f"{source_id}:{source_object_id or f'{gush}:{helka}:{geom_hash}'}"
        provenance_id = _get_or_create_provenance(
            session,
            canonical_table="parcels",
            source_id=source_id,
            source_key=source_key,
            raw_storage_uri=raw_uri,
            geom_hash=geom_hash,
            metadata={"properties": properties},
        )
        session.execute(
            text(
                """
                INSERT INTO parcels (
                  source_id, provenance_id, source_object_id, gush, helka, parcel_label,
                  normalized_geometry_hash, geom, geom_2039, validation_status,
                  validation_warnings, metadata
                ) VALUES (
                  :source_id, :provenance_id, :source_object_id, :gush, :helka, :parcel_label,
                  :normalized_geometry_hash,
                  ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry))::geometry(MultiPolygon, 4326),
                  ST_Transform(ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry)), 2039)::geometry(MultiPolygon, 2039),
                  :validation_status, CAST(:validation_warnings AS jsonb), CAST(:metadata AS jsonb)
                )
                ON CONFLICT (source_id, gush, helka, normalized_geometry_hash) DO UPDATE SET
                  provenance_id = EXCLUDED.provenance_id,
                  source_object_id = COALESCE(EXCLUDED.source_object_id, parcels.source_object_id),
                  parcel_label = EXCLUDED.parcel_label,
                  geom = EXCLUDED.geom,
                  geom_2039 = EXCLUDED.geom_2039,
                  validation_status = EXCLUDED.validation_status,
                  validation_warnings = EXCLUDED.validation_warnings,
                  metadata = EXCLUDED.metadata,
                  fetched_at = now(),
                  updated_at = now()
                """
            ),
            {
                "source_id": source_id,
                "provenance_id": provenance_id,
                "source_object_id": source_object_id,
                "gush": gush,
                "helka": helka,
                "parcel_label": " / ".join(part for part in [gush, helka] if part),
                "normalized_geometry_hash": geom_hash,
                "geometry": json.dumps(geometry),
                "validation_status": result.status,
                "validation_warnings": json.dumps(result.warnings, ensure_ascii=False),
                "metadata": json.dumps({"properties": properties}, ensure_ascii=False, default=str),
            },
        )
        count += 1
    return ImportSummary(count, rejected, _dedupe(warnings))


def fetch_govmap_public_parcel(*, gush: str, helka: str, post_json: JsonPoster) -> dict[str, Any] | None:
    search_payload = {
        "searchText": f"block {gush} parcel {helka}",
        "language": "he",
        "maxResults": 10,
        "isAccurate": False,
        "subType": "parcel",
    }
    search_response = post_json(GOVMAP_PARCEL_AUTOCOMPLETE_URL, search_payload)
    result = _select_govmap_parcel_result(search_response, gush=gush, helka=helka)
    if result is None:
        return None
    source_object_id = str(result["id"]).split("|")[-1]
    shape_payload = {"idType": "parcel", "id": source_object_id}
    wkt = post_json(GOVMAP_GET_SHAPE_URL, shape_payload)
    if not isinstance(wkt, str) or not wkt.strip():
        return None
    return {
        "gush": _normalize_parcel_number(gush),
        "helka": _normalize_parcel_number(helka),
        "source_object_id": source_object_id,
        "wkt": wkt,
        "search_payload": search_payload,
        "search_result": result,
        "shape_payload": shape_payload,
    }


def import_govmap_public_parcel(
    session: Session,
    *,
    source_id: str,
    gush: str,
    helka: str,
    post_json: JsonPoster,
    raw_storage: ImmutableRawStorage,
    input_srid: int = 3857,
) -> ImportSummary:
    parcel = fetch_govmap_public_parcel(gush=gush, helka=helka, post_json=post_json)
    if parcel is None:
        return ImportSummary(0, 1, ["parcel_not_found"])
    raw_uri = raw_storage.write_json(source_id=source_id, name=f"govmap_parcel_{gush}_{helka}.json", payload=parcel)
    source_geometry = _geojson_polygon_from_wkt(parcel["wkt"])
    geometry = transform_geojson_geometry(source_geometry, source_srid=input_srid, target_srid=4326)
    result = validate_geojson_geometry(geometry, expected_type="MultiPolygon")
    if result.status == "invalid":
        return ImportSummary(0, 1, result.warnings or ["invalid_parcel_geometry"])
    geom_hash = geometry_hash(geometry)
    metadata = {
        "govmap_public_fallback": True,
        "search_payload": parcel["search_payload"],
        "search_result": parcel["search_result"],
        "shape_payload": parcel["shape_payload"],
    }
    provenance_id = _get_or_create_provenance(
        session,
        canonical_table="parcels",
        source_id=source_id,
        source_key=f"{source_id}:{parcel['source_object_id']}",
        source_record_id=str(parcel["source_object_id"]),
        raw_storage_uri=raw_uri,
        geom_hash=geom_hash,
        metadata=metadata,
    )
    session.execute(
        text(
            """
            INSERT INTO parcels (
              source_id, provenance_id, source_object_id, gush, helka, parcel_label,
              normalized_geometry_hash, geom, geom_2039, validation_status,
              validation_warnings, metadata
            ) VALUES (
              :source_id, :provenance_id, :source_object_id, :gush, :helka, :parcel_label,
              :normalized_geometry_hash,
              ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry))::geometry(MultiPolygon, 4326),
              ST_Transform(ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry)), 2039)::geometry(MultiPolygon, 2039),
              :validation_status, CAST(:validation_warnings AS jsonb), CAST(:metadata AS jsonb)
            )
            ON CONFLICT (source_id, gush, helka, normalized_geometry_hash) DO UPDATE SET
              provenance_id = EXCLUDED.provenance_id,
              source_object_id = EXCLUDED.source_object_id,
              parcel_label = EXCLUDED.parcel_label,
              geom = EXCLUDED.geom,
              geom_2039 = EXCLUDED.geom_2039,
              validation_status = EXCLUDED.validation_status,
              validation_warnings = EXCLUDED.validation_warnings,
              metadata = EXCLUDED.metadata,
              fetched_at = now(),
              updated_at = now()
            """
        ),
        {
            "source_id": source_id,
            "provenance_id": provenance_id,
            "source_object_id": str(parcel["source_object_id"]),
            "gush": parcel["gush"],
            "helka": parcel["helka"],
            "parcel_label": f"{parcel['gush']} / {parcel['helka']}",
            "normalized_geometry_hash": geom_hash,
            "geometry": json.dumps(geometry),
            "validation_status": result.status,
            "validation_warnings": json.dumps(result.warnings, ensure_ascii=False),
            "metadata": json.dumps(metadata, ensure_ascii=False, default=str),
        },
    )
    _refresh_national_polygon_coverage(session, source_id=source_id, layer_key="parcels", table_name="parcels")
    return ImportSummary(1, 0, _dedupe(result.warnings))


def import_gtfs_stops(session: Session, *, source_id: str, zip_bytes: bytes, raw_storage: ImmutableRawStorage) -> ImportSummary:
    raw_uri = raw_storage.write_bytes(source_id=source_id, name="gtfs.zip", content=zip_bytes)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        with archive.open("stops.txt") as stops_file:
            rows = list(csv.DictReader(io.TextIOWrapper(stops_file, encoding="utf-8-sig")))
    count = 0
    rejected = 0
    warnings: list[str] = []
    for row in rows:
        stop_id = str(row.get("stop_id") or "").strip()
        lon, lat = _float_or_none(row.get("stop_lon")), _float_or_none(row.get("stop_lat"))
        if not stop_id or lon is None or lat is None:
            rejected += 1
            warnings.append("missing_stop_id_or_coordinates")
            continue
        geometry = {"type": "Point", "coordinates": [lon, lat]}
        result = validate_geojson_geometry(geometry, expected_type="Point")
        if result.status == "invalid":
            rejected += 1
            warnings.extend(result.warnings)
            continue
        provenance_id = _get_or_create_provenance(
            session,
            canonical_table="poi_points",
            source_id=source_id,
            source_key=f"{source_id}:{stop_id}",
            source_record_id=stop_id,
            raw_storage_uri=raw_uri,
            geom_hash=geometry_hash(geometry),
            metadata={"row": row},
        )
        _upsert_poi_point(
            session,
            source_id=source_id,
            provenance_id=provenance_id,
            source_object_id=stop_id,
            poi_category="transport_stop",
            name_he=row.get("stop_name"),
            name_en=row.get("stop_name_en"),
            official_identifier=stop_id,
            lon=lon,
            lat=lat,
            validation_status=result.status,
            validation_warnings=result.warnings,
            metadata={"row": row},
        )
        count += 1
    return ImportSummary(count, rejected, _dedupe(warnings))


def import_osm_context_zip(
    session: Session,
    *,
    source_id: str,
    zip_bytes: bytes,
    raw_storage: ImmutableRawStorage,
    source_name: str = "geofabrik_osm_context.zip",
    buildings_limit: int | None = None,
    pois_limit: int | None = None,
    context_geometry_limit: int | None = None,
    center_lon: float | None = None,
    center_lat: float | None = None,
    radius_m: float | None = None,
) -> ImportSummary:
    _assert_context_only_source(session, source_id)
    raw_uri = raw_storage.write_bytes(source_id=source_id, name=source_name, content=zip_bytes)
    spatial_bbox = _spatial_bbox(center_lon=center_lon, center_lat=center_lat, radius_m=radius_m)
    count = 0
    rejected = 0
    warnings: list[str] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            archive.extractall(tmp_dir)
        root = Path(tmp_dir)
        buildings_path = _first_named_shapefile(root, ["gis_osm_buildings_a_free_1.shp", "buildings_a.shp"])
        pois_path = _first_named_shapefile(root, ["gis_osm_pois_free_1.shp", "pois.shp"])
        geometry_specs = [
            ("context_roads", ["gis_osm_roads_free_1.shp", "roads.shp"], ("LineString", "MultiLineString")),
            ("context_railways", ["gis_osm_railways_free_1.shp", "railways.shp"], ("LineString", "MultiLineString")),
            ("context_waterways", ["gis_osm_waterways_free_1.shp", "waterways.shp"], ("LineString", "MultiLineString")),
            ("context_water", ["gis_osm_water_a_free_1.shp", "water_a.shp"], ("Polygon", "MultiPolygon")),
            ("context_landuse", ["gis_osm_landuse_a_free_1.shp", "landuse_a.shp"], ("Polygon", "MultiPolygon")),
            ("context_landuse", ["gis_osm_natural_a_free_1.shp", "natural_a.shp"], ("Polygon", "MultiPolygon")),
        ]
        geometry_paths = [(layer_key, path, expected_types) for layer_key, names, expected_types in geometry_specs for path in [_first_named_shapefile(root, names)] if path is not None]
        if buildings_path is None and pois_path is None and not geometry_paths:
            raise ValueError("OSM context ZIP does not contain supported Geofabrik context shapefiles")
        if buildings_path is not None:
            buildings = _import_osm_buildings_shp(
                session,
                source_id=source_id,
                shp_path=buildings_path,
                raw_uri=raw_uri,
                limit=buildings_limit,
                spatial_bbox=spatial_bbox,
            )
            count += buildings.inserted_or_updated
            rejected += buildings.rejected
            warnings.extend(buildings.warnings or [])
        if pois_path is not None:
            pois = _import_osm_pois_shp(
                session,
                source_id=source_id,
                shp_path=pois_path,
                raw_uri=raw_uri,
                limit=pois_limit,
                spatial_bbox=spatial_bbox,
            )
            count += pois.inserted_or_updated
            rejected += pois.rejected
            warnings.extend(pois.warnings or [])
        for layer_key, path, expected_types in geometry_paths:
            context = _import_osm_context_geometries_shp(
                session,
                source_id=source_id,
                shp_path=path,
                raw_uri=raw_uri,
                context_layer=layer_key,
                expected_types=expected_types,
                limit=context_geometry_limit,
                spatial_bbox=spatial_bbox,
            )
            count += context.inserted_or_updated
            rejected += context.rejected
            warnings.extend(context.warnings or [])
    if _has_municipal_boundaries(session):
        _refresh_osm_context_coverage(session, source_id=source_id)
    else:
        warnings.append("municipal_boundaries_not_ingested_coverage_not_refreshed")
    return ImportSummary(count, rejected, _dedupe(warnings))


def import_municipal_geojson(
    session: Session,
    *,
    source_id: str,
    geojson_payload: Mapping[str, Any],
    config: Mapping[str, Any],
    raw_storage: ImmutableRawStorage,
    source_name: str = "municipal.geojson",
) -> ImportSummary:
    raw_uri = raw_storage.write_json(source_id=source_id, name=source_name, payload=geojson_payload)
    features = geojson_payload.get("features") if isinstance(geojson_payload, Mapping) else None
    if not isinstance(features, list):
        raise ValueError("municipal GeoJSON payload must be a FeatureCollection")
    return _import_municipal_features(session, source_id=source_id, features=features, config=config, raw_uri=raw_uri)


def import_municipal_csv(
    session: Session,
    *,
    source_id: str,
    csv_text: str,
    config: Mapping[str, Any],
    raw_storage: ImmutableRawStorage,
    source_name: str = "municipal.csv",
) -> ImportSummary:
    raw_uri = raw_storage.write_bytes(source_id=source_id, name=source_name, content=csv_text.encode("utf-8"))
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    features = [_csv_row_feature(row, config=config) for row in rows]
    return _import_municipal_features(session, source_id=source_id, features=features, config=config, raw_uri=raw_uri)


def import_municipal_shp_zip(
    session: Session,
    *,
    source_id: str,
    zip_bytes: bytes,
    config: Mapping[str, Any],
    raw_storage: ImmutableRawStorage,
    source_name: str = "municipal_shapefile.zip",
    input_srid: int = 4326,
) -> ImportSummary:
    import shapefile

    raw_uri = raw_storage.write_bytes(source_id=source_id, name=source_name, content=zip_bytes)
    with tempfile.TemporaryDirectory() as tmp_dir:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            archive.extractall(tmp_dir)
        shp_path = _municipal_shapefile_path(Path(tmp_dir), config)
        if shp_path is None:
            raise ValueError("municipal SHP ZIP does not contain the configured .shp file")
        reader = shapefile.Reader(str(shp_path))
        fields = [field[0] for field in reader.fields[1:]]
        features: list[dict[str, Any]] = []
        for shape_record in reader.iterShapeRecords():
            geometry = dict(shape_record.shape.__geo_interface__)
            if input_srid != 4326:
                geometry = transform_geojson_geometry(geometry, source_srid=input_srid, target_srid=4326)
            features.append({"type": "Feature", "properties": dict(zip(fields, shape_record.record)), "geometry": geometry})
    return _import_municipal_features(session, source_id=source_id, features=features, config=config, raw_uri=raw_uri)


def import_municipal_arcgis(
    session: Session,
    *,
    source_id: str,
    service_url: str,
    fetch_json: JsonFetcher,
    config: Mapping[str, Any],
    raw_storage: ImmutableRawStorage,
    page_size: int = 1000,
    where: str = "1=1",
    query_params: Mapping[str, Any] | None = None,
) -> ImportSummary:
    pages = fetch_arcgis_feature_pages(service_url=service_url, fetch_json=fetch_json, page_size=page_size, where=where, query_params=query_params)
    total = ImportSummary(0, 0, [])
    for page_index, page in enumerate(pages):
        raw_uri = raw_storage.write_json(source_id=source_id, name=f"municipal_arcgis_page_{page_index}.geojson", payload=page)
        summary = _import_municipal_features(session, source_id=source_id, features=page.get("features") or [], config=config, raw_uri=raw_uri)
        total = ImportSummary(
            total.inserted_or_updated + summary.inserted_or_updated,
            total.rejected + summary.rejected,
            _dedupe([*(total.warnings or []), *(summary.warnings or [])]),
        )
    return total


def _import_municipal_features(
    session: Session,
    *,
    source_id: str,
    features: Iterable[Mapping[str, Any]],
    config: Mapping[str, Any],
    raw_uri: str,
) -> ImportSummary:
    layer_key = _municipal_layer_key(config)
    count = 0
    rejected = 0
    warnings: list[str] = []
    for index, feature in enumerate(features):
        properties = _properties(feature)
        geometry = _geometry(feature)
        try:
            inserted = _import_municipal_feature(
                session,
                source_id=source_id,
                layer_key=layer_key,
                feature=feature,
                properties=properties,
                geometry=geometry,
                config=config,
                raw_uri=raw_uri,
                index=index,
            )
        except ValueError as exc:
            rejected += 1
            warnings.append(str(exc))
            continue
        if inserted:
            count += 1
    _refresh_municipal_coverage(session, source_id=source_id, layer_key=layer_key, config=config)
    return ImportSummary(count, rejected, _dedupe(warnings))


def _import_municipal_feature(
    session: Session,
    *,
    source_id: str,
    layer_key: str,
    feature: Mapping[str, Any],
    properties: Mapping[str, Any],
    geometry: dict[str, Any],
    config: Mapping[str, Any],
    raw_uri: str,
    index: int,
) -> bool:
    if layer_key in {"neighborhoods", "buildings"}:
        normalized_geometry = normalize_polygon_geometry(geometry)
        result = validate_geojson_geometry(normalized_geometry, expected_type="MultiPolygon")
    elif layer_key in {"address_points", "poi_points"}:
        normalized_geometry = geometry
        result = validate_geojson_geometry(normalized_geometry, expected_type="Point")
    elif layer_key == "context_geometries":
        normalized_geometry = _normalise_context_geometry(geometry)
        expected_types = tuple(config.get("expected_geometry_types") or ["MultiPolygon", "MultiLineString", "Polygon", "LineString"])
        result = validate_geojson_geometry(normalized_geometry, expected_type=expected_types)
    else:
        raise ValueError(f"unsupported_municipal_layer:{layer_key}")
    if result.status == "invalid":
        raise ValueError((result.warnings or ["invalid_municipal_geometry"])[0])

    geom_hash = geometry_hash(normalized_geometry)
    source_object_id = _municipal_source_object_id(properties, normalized_geometry, config=config, index=index)
    municipality_code = _municipal_feature_municipality_code(properties, config)
    municipality_name_he = str(config.get("municipality_name_he") or "").strip() or None
    source_key = f"{source_id}:{layer_key}:{source_object_id}"
    provenance_id = _get_or_create_provenance(
        session,
        canonical_table=layer_key,
        source_id=source_id,
        source_key=source_key,
        source_record_id=source_object_id,
        raw_storage_uri=raw_uri,
        geom_hash=geom_hash,
        metadata={"properties": properties, "municipal_import_config": dict(config)},
    )
    metadata = {"properties": dict(properties), "municipality_name_he": municipality_name_he}
    if layer_key == "neighborhoods":
        _upsert_municipal_neighborhood(session, source_id, provenance_id, source_object_id, municipality_code, geom_hash, normalized_geometry, result, properties, metadata, config)
    elif layer_key == "address_points":
        _upsert_municipal_address_point(session, source_id, provenance_id, source_object_id, municipality_code, normalized_geometry, result, properties, metadata, config)
    elif layer_key == "poi_points":
        _upsert_municipal_poi_point(session, source_id, provenance_id, source_object_id, municipality_code, normalized_geometry, result, properties, metadata, config)
    elif layer_key == "buildings":
        _upsert_municipal_building(session, source_id, provenance_id, source_object_id, municipality_code, geom_hash, normalized_geometry, result, metadata)
    elif layer_key == "context_geometries":
        _upsert_municipal_context_geometry(session, source_id, provenance_id, source_object_id, normalized_geometry, result, properties, metadata, config)
    return True


def _import_osm_buildings_shp(
    session: Session,
    *,
    source_id: str,
    shp_path: Path,
    raw_uri: str,
    limit: int | None,
    spatial_bbox: tuple[float, float, float, float] | None = None,
) -> ImportSummary:
    import shapefile

    reader = shapefile.Reader(str(shp_path))
    fields = [field[0] for field in reader.fields[1:]]
    count = 0
    rejected = 0
    warnings: list[str] = []
    for shape_record in reader.iterShapeRecords():
        if limit is not None and count >= limit:
            break
        if spatial_bbox is not None and not _shape_intersects_bbox(shape_record.shape, spatial_bbox):
            continue
        properties = dict(zip(fields, shape_record.record))
        source_object_id = _osm_source_object_id(properties, fallback=count)
        geometry = normalize_polygon_geometry(shape_record.shape.__geo_interface__)
        result = validate_geojson_geometry(geometry, expected_type="MultiPolygon")
        if result.status == "invalid" or not source_object_id:
            rejected += 1
            warnings.extend(result.warnings or ["missing_osm_building_identifier"])
            continue
        geom_hash = geometry_hash(geometry)
        provenance_id = _get_or_create_provenance(
            session,
            canonical_table="buildings",
            source_id=source_id,
            source_key=f"{source_id}:buildings:{source_object_id}",
            source_record_id=source_object_id,
            raw_storage_uri=raw_uri,
            geom_hash=geom_hash,
            metadata={"properties": properties},
        )
        session.execute(
            text(
                """
                INSERT INTO buildings (
                  source_id, provenance_id, source_object_id, normalized_geometry_hash,
                  geom, geom_2039, validation_status, validation_warnings, metadata
                ) VALUES (
                  :source_id, :provenance_id, :source_object_id, :normalized_geometry_hash,
                  ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry))::geometry(MultiPolygon, 4326),
                  ST_Transform(ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry)), 2039)::geometry(MultiPolygon, 2039),
                  :validation_status, CAST(:validation_warnings AS jsonb), CAST(:metadata AS jsonb)
                )
                ON CONFLICT (source_id, source_object_id) WHERE source_object_id IS NOT NULL DO UPDATE SET
                  provenance_id = EXCLUDED.provenance_id,
                  normalized_geometry_hash = EXCLUDED.normalized_geometry_hash,
                  geom = EXCLUDED.geom,
                  geom_2039 = EXCLUDED.geom_2039,
                  validation_status = EXCLUDED.validation_status,
                  validation_warnings = EXCLUDED.validation_warnings,
                  metadata = EXCLUDED.metadata,
                  fetched_at = now(),
                  updated_at = now()
                """
            ),
            {
                "source_id": source_id,
                "provenance_id": provenance_id,
                "source_object_id": source_object_id,
                "normalized_geometry_hash": geom_hash,
                "geometry": json.dumps(geometry),
                "validation_status": result.status,
                "validation_warnings": json.dumps(result.warnings, ensure_ascii=False),
                "metadata": json.dumps({"properties": properties}, ensure_ascii=False, default=str),
            },
        )
        count += 1
    return ImportSummary(count, rejected, _dedupe(warnings))


def _import_osm_pois_shp(
    session: Session,
    *,
    source_id: str,
    shp_path: Path,
    raw_uri: str,
    limit: int | None,
    spatial_bbox: tuple[float, float, float, float] | None = None,
) -> ImportSummary:
    import shapefile

    reader = shapefile.Reader(str(shp_path))
    fields = [field[0] for field in reader.fields[1:]]
    count = 0
    rejected = 0
    warnings: list[str] = []
    for shape_record in reader.iterShapeRecords():
        if limit is not None and count >= limit:
            break
        if spatial_bbox is not None and not _shape_intersects_bbox(shape_record.shape, spatial_bbox):
            continue
        properties = dict(zip(fields, shape_record.record))
        source_object_id = _osm_source_object_id(properties, fallback=count)
        geometry = dict(shape_record.shape.__geo_interface__)
        result = validate_geojson_geometry(geometry, expected_type="Point")
        if result.status == "invalid" or not source_object_id:
            rejected += 1
            warnings.extend(result.warnings or ["missing_osm_poi_identifier"])
            continue
        lon, lat = geometry["coordinates"][:2]
        provenance_id = _get_or_create_provenance(
            session,
            canonical_table="context_pois",
            source_id=source_id,
            source_key=f"{source_id}:context_pois:{source_object_id}",
            source_record_id=source_object_id,
            raw_storage_uri=raw_uri,
            geom_hash=geometry_hash(geometry),
            metadata={"properties": properties},
        )
        session.execute(
            text(
                """
                INSERT INTO context_pois (
                  source_id, provenance_id, source_object_id, poi_category, name,
                  municipality_code, geom, geom_2039, validation_status, validation_warnings, metadata
                ) VALUES (
                  :source_id, :provenance_id, :source_object_id, :poi_category, :name,
                  NULL, ST_SetSRID(ST_Point(:lon, :lat), 4326)::geometry(Point, 4326),
                  ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)::geometry(Point, 2039),
                  :validation_status, CAST(:validation_warnings AS jsonb), CAST(:metadata AS jsonb)
                )
                ON CONFLICT (source_id, source_object_id) WHERE source_object_id IS NOT NULL DO UPDATE SET
                  provenance_id = EXCLUDED.provenance_id,
                  poi_category = EXCLUDED.poi_category,
                  name = EXCLUDED.name,
                  geom = EXCLUDED.geom,
                  geom_2039 = EXCLUDED.geom_2039,
                  validation_status = EXCLUDED.validation_status,
                  validation_warnings = EXCLUDED.validation_warnings,
                  metadata = EXCLUDED.metadata,
                  fetched_at = now(),
                  updated_at = now()
                """
            ),
            {
                "source_id": source_id,
                "provenance_id": provenance_id,
                "source_object_id": source_object_id,
                "poi_category": _osm_poi_category(properties),
                "name": _first_present(properties, ["name", "name_en", "name_he"]),
                "lon": float(lon),
                "lat": float(lat),
                "validation_status": result.status,
                "validation_warnings": json.dumps(result.warnings, ensure_ascii=False),
                "metadata": json.dumps({"properties": properties}, ensure_ascii=False, default=str),
            },
        )
        count += 1
    return ImportSummary(count, rejected, _dedupe(warnings))


def _import_osm_context_geometries_shp(
    session: Session,
    *,
    source_id: str,
    shp_path: Path,
    raw_uri: str,
    context_layer: str,
    expected_types: tuple[str, ...],
    limit: int | None,
    spatial_bbox: tuple[float, float, float, float] | None,
) -> ImportSummary:
    import shapefile

    reader = shapefile.Reader(str(shp_path))
    fields = [field[0] for field in reader.fields[1:]]
    count = 0
    rejected = 0
    warnings: list[str] = []
    for shape_record in reader.iterShapeRecords():
        if limit is not None and count >= limit:
            break
        if spatial_bbox is not None and not _shape_intersects_bbox(shape_record.shape, spatial_bbox):
            continue
        properties = dict(zip(fields, shape_record.record))
        source_object_id = _osm_source_object_id(properties, fallback=count)
        geometry = _normalise_context_geometry(dict(shape_record.shape.__geo_interface__))
        result = validate_geojson_geometry(geometry, expected_type=expected_types)
        if result.status == "invalid" or not source_object_id:
            rejected += 1
            warnings.extend(result.warnings or ["missing_osm_context_geometry_identifier"])
            continue
        geom_hash = geometry_hash(geometry)
        source_key = f"{source_id}:{context_layer}:{source_object_id}"
        provenance_id = _get_or_create_provenance(
            session,
            canonical_table="context_geometries",
            source_id=source_id,
            source_key=source_key,
            source_record_id=source_object_id,
            raw_storage_uri=raw_uri,
            geom_hash=geom_hash,
            metadata={"properties": properties, "context_layer": context_layer},
        )
        session.execute(
            text(
                """
                INSERT INTO context_geometries (
                  source_id, provenance_id, source_object_id, context_layer, geometry_type,
                  category, name, normalized_geometry_hash, geom, geom_2039,
                  validation_status, validation_warnings, metadata
                ) VALUES (
                  :source_id, :provenance_id, :source_object_id, :context_layer, :geometry_type,
                  :category, :name, :normalized_geometry_hash,
                  ST_Force2D(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry))::geometry(Geometry, 4326),
                  ST_Transform(ST_Force2D(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry)), 2039)::geometry(Geometry, 2039),
                  :validation_status, CAST(:validation_warnings AS jsonb), CAST(:metadata AS jsonb)
                )
                ON CONFLICT (source_id, context_layer, source_object_id) WHERE source_object_id IS NOT NULL DO UPDATE SET
                  provenance_id = EXCLUDED.provenance_id,
                  geometry_type = EXCLUDED.geometry_type,
                  category = EXCLUDED.category,
                  name = EXCLUDED.name,
                  normalized_geometry_hash = EXCLUDED.normalized_geometry_hash,
                  geom = EXCLUDED.geom,
                  geom_2039 = EXCLUDED.geom_2039,
                  validation_status = EXCLUDED.validation_status,
                  validation_warnings = EXCLUDED.validation_warnings,
                  metadata = EXCLUDED.metadata,
                  fetched_at = now(),
                  updated_at = now()
                """
            ),
            {
                "source_id": source_id,
                "provenance_id": provenance_id,
                "source_object_id": source_object_id,
                "context_layer": context_layer,
                "geometry_type": str(geometry.get("type") or "Geometry"),
                "category": _osm_poi_category(properties),
                "name": _first_present(properties, ["name", "name_en", "name_he"]),
                "normalized_geometry_hash": geom_hash,
                "geometry": json.dumps(geometry),
                "validation_status": result.status,
                "validation_warnings": json.dumps(result.warnings, ensure_ascii=False),
                "metadata": json.dumps({"properties": properties, "context_layer": context_layer}, ensure_ascii=False, default=str),
            },
        )
        count += 1
    return ImportSummary(count, rejected, _dedupe(warnings))


def import_mot_bus_stops_csv(session: Session, *, source_id: str, csv_text: str, raw_storage: ImmutableRawStorage) -> ImportSummary:
    raw_uri = raw_storage.write_bytes(source_id=source_id, name="mot_bus_stops.csv", content=csv_text.encode("utf-8"))
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    count = 0
    rejected = 0
    warnings: list[str] = []
    for row in rows:
        stop = _mot_bus_stop_values(row)
        if stop is None:
            rejected += 1
            warnings.append("missing_stop_id_or_coordinates")
            continue
        stop_id, lon, lat = stop
        geometry = {"type": "Point", "coordinates": [lon, lat]}
        result = validate_geojson_geometry(geometry, expected_type="Point")
        if result.status == "invalid":
            rejected += 1
            warnings.extend(result.warnings)
            continue
        provenance_id = _get_or_create_provenance(
            session,
            canonical_table="poi_points",
            source_id=source_id,
            source_key=f"{source_id}:{stop_id}",
            source_record_id=stop_id,
            raw_storage_uri=raw_uri,
            geom_hash=geometry_hash(geometry),
            metadata={"row": row},
        )
        _upsert_poi_point(
            session,
            source_id=source_id,
            provenance_id=provenance_id,
            source_object_id=stop_id,
            poi_category="transport_stop",
            name_he=_first_present(row, ["cityname", "city_name"]),
            name_en=None,
            official_identifier=stop_id,
            lon=lon,
            lat=lat,
            validation_status=result.status,
            validation_warnings=result.warnings,
            metadata={"row": row},
        )
        count += 1
    _refresh_national_point_coverage(session, source_id=source_id, layer_key="poi_points", table_name="poi_points")
    return ImportSummary(count, rejected, _dedupe(warnings))


def _mot_bus_stop_values(row: Mapping[str, Any]) -> tuple[str, float, float] | None:
    stop_id = _first_present(row, ["stationid", "station_id", "stop_id"])
    lon = _float_or_none(_first_present(row, ["long", "lon", "longitude", "stop_lon"]))
    lat = _float_or_none(_first_present(row, ["lat", "latitude", "stop_lat"]))
    if not stop_id or lon is None or lat is None:
        return None
    return stop_id, lon, lat


def _select_govmap_parcel_result(search_response: Any, *, gush: str, helka: str) -> dict[str, Any] | None:
    if not isinstance(search_response, Mapping):
        return None
    target_gush = _normalize_parcel_number(gush)
    target_helka = _normalize_parcel_number(helka)
    for result in search_response.get("results") or []:
        if not isinstance(result, Mapping):
            continue
        result_id = str(result.get("id") or "")
        if not result_id.startswith("parcel|"):
            continue
        text_value = str(result.get("text") or "")
        if _govmap_text_matches_parcel(text_value, gush=target_gush, helka=target_helka):
            return dict(result)
    return None


def _govmap_text_matches_parcel(text_value: str, *, gush: str, helka: str) -> bool:
    parts = text_value.replace(",", " ").split()
    for index, part in enumerate(parts[:-1]):
        if part == "גוש" and _normalize_parcel_number(parts[index + 1]) == gush:
            break
    else:
        return False
    for index, part in enumerate(parts[:-1]):
        if part == "חלקה" and _normalize_parcel_number(parts[index + 1]) == helka:
            return True
    return False


def _normalize_parcel_number(value: Any) -> str:
    text_value = str(value or "").strip()
    return str(int(text_value)) if text_value.isdigit() else text_value


def _geojson_polygon_from_wkt(wkt: str) -> dict[str, Any]:
    text_value = wkt.strip().strip('"')
    upper = text_value.upper()
    if upper.startswith("MULTIPOLYGON"):
        body = _strip_wkt_type(text_value, "MULTIPOLYGON")
        polygons = []
        for polygon_text in _split_wkt_groups(_strip_outer_parens(body)):
            rings = []
            for ring_text in _split_wkt_groups(_strip_outer_parens(polygon_text)):
                rings.append(_parse_wkt_ring(_strip_outer_parens(ring_text)))
            polygons.append(rings)
        return {"type": "MultiPolygon", "coordinates": polygons}
    if upper.startswith("POLYGON"):
        body = _strip_wkt_type(text_value, "POLYGON")
        rings = [_parse_wkt_ring(_strip_outer_parens(ring_text)) for ring_text in _split_wkt_groups(_strip_outer_parens(body))]
        return {"type": "MultiPolygon", "coordinates": [rings]}
    raise ValueError("Only POLYGON and MULTIPOLYGON WKT are supported")


def _strip_wkt_type(wkt: str, type_name: str) -> str:
    return wkt[len(type_name) :].strip()


def _strip_outer_parens(text_value: str) -> str:
    stripped = text_value.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        return stripped[1:-1].strip()
    return stripped


def _split_wkt_groups(text_value: str) -> list[str]:
    groups: list[str] = []
    level = 0
    start = 0
    for index, char in enumerate(text_value):
        if char == "(":
            level += 1
        elif char == ")":
            level -= 1
        elif char == "," and level == 0:
            groups.append(text_value[start:index].strip())
            start = index + 1
    groups.append(text_value[start:].strip())
    return [group for group in groups if group]


def _parse_wkt_ring(text_value: str) -> list[list[float]]:
    coordinates: list[list[float]] = []
    for coordinate_text in text_value.split(","):
        parts = coordinate_text.strip().split()
        if len(parts) < 2:
            continue
        coordinates.append([float(parts[0]), float(parts[1])])
    if len(coordinates) < 4:
        raise ValueError("WKT polygon ring must contain at least four coordinates")
    return coordinates


def transform_geojson_geometry(geometry: dict[str, Any], *, source_srid: int, target_srid: int) -> dict[str, Any]:
    if source_srid == target_srid:
        return geometry
    from pyproj import Transformer

    transformer = Transformer.from_crs(f"EPSG:{source_srid}", f"EPSG:{target_srid}", always_xy=True)
    return {**geometry, "coordinates": _transform_coordinates(geometry.get("coordinates"), transformer)}


def discover_ckan_csv_resource(package_payload: Mapping[str, Any]) -> dict[str, Any]:
    result = package_payload.get("result", package_payload)
    resources = result.get("resources") or []
    for resource in resources:
        if str(resource.get("format") or "").lower() == "csv" and resource.get("url"):
            return dict(resource)
    raise ValueError("No CSV resource found in CKAN package")


def import_school_coordinates_csv(session: Session, *, source_id: str, csv_text: str, raw_storage: ImmutableRawStorage) -> ImportSummary:
    raw_uri = raw_storage.write_bytes(source_id=source_id, name="schools.csv", content=csv_text.encode("utf-8"))
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    count = 0
    rejected = 0
    warnings: list[str] = []
    for row in rows:
        official_id = _first_present(row, ["institution_id", "semel_mosad", "school_id", "סמל מוסד"])
        lon_lat = _school_lon_lat(row)
        if not official_id or lon_lat is None:
            rejected += 1
            warnings.append("missing_school_id_or_coordinates")
            continue
        lon, lat = lon_lat
        geometry = {"type": "Point", "coordinates": [lon, lat]}
        result = validate_geojson_geometry(geometry, expected_type="Point")
        if result.status == "invalid":
            rejected += 1
            warnings.extend(result.warnings)
            continue
        provenance_id = _get_or_create_provenance(
            session,
            canonical_table="poi_points",
            source_id=source_id,
            source_key=f"{source_id}:{official_id}",
            source_record_id=official_id,
            raw_storage_uri=raw_uri,
            geom_hash=geometry_hash(geometry),
            metadata={"row": row},
        )
        _upsert_poi_point(
            session,
            source_id=source_id,
            provenance_id=provenance_id,
            source_object_id=official_id,
            poi_category="school",
            name_he=_first_present(row, ["name_he", "institution_name", "shem_mosad", "שם מוסד"]),
            name_en=_first_present(row, ["name_en", "institution_name_en"]),
            official_identifier=official_id,
            lon=lon,
            lat=lat,
            validation_status=result.status,
            validation_warnings=result.warnings,
            metadata={"row": row},
        )
        count += 1
    _refresh_national_point_coverage(session, source_id=source_id, layer_key="poi_points", table_name="poi_points")
    return ImportSummary(count, rejected, _dedupe(warnings))


def _school_lon_lat(row: Mapping[str, Any]) -> tuple[float, float] | None:
    lon = _float_or_none(_first_present(row, ["lon", "longitude", "x_wgs84", "lng", "utm_x"]))
    lat = _float_or_none(_first_present(row, ["lat", "latitude", "y_wgs84", "utm_y"]))
    if lon is not None and lat is not None:
        return lon, lat
    x = _float_or_none(_first_present(row, ["x", "itm_x", "x_itm", "coord_x"]))
    y = _float_or_none(_first_present(row, ["y", "itm_y", "y_itm", "coord_y"]))
    if x is None or y is None:
        return None
    from pyproj import Transformer

    transformer = Transformer.from_crs("EPSG:2039", "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(x, y)
    return float(lon), float(lat)


def _get_or_create_provenance(
    session: Session,
    *,
    canonical_table: str,
    source_id: str,
    source_key: str,
    raw_storage_uri: str | None,
    geom_hash: str,
    metadata: dict[str, Any],
    source_record_id: str | None = None,
) -> str:
    existing = session.execute(
        text(
            """
            SELECT id FROM feature_provenance
            WHERE canonical_table = :canonical_table AND source_id = :source_id AND source_key = :source_key
            LIMIT 1
            """
        ),
        {"canonical_table": canonical_table, "source_id": source_id, "source_key": source_key},
    ).scalar_one_or_none()
    if existing:
        return str(existing)
    return str(
        session.execute(
            text(
                """
                INSERT INTO feature_provenance (
                  canonical_table, source_id, source_key, source_record_id,
                  raw_storage_uri, geometry_hash, metadata
                ) VALUES (
                  :canonical_table, :source_id, :source_key, :source_record_id,
                  :raw_storage_uri, :geometry_hash, CAST(:metadata AS jsonb)
                )
                RETURNING id
                """
            ),
            {
                "canonical_table": canonical_table,
                "source_id": source_id,
                "source_key": source_key,
                "source_record_id": source_record_id,
                "raw_storage_uri": raw_storage_uri,
                "geometry_hash": geom_hash,
                "metadata": json.dumps(metadata, ensure_ascii=False, default=str),
            },
        ).scalar_one()
    )


def _upsert_poi_point(
    session: Session,
    *,
    source_id: str,
    provenance_id: str,
    source_object_id: str,
    poi_category: str,
    name_he: str | None,
    name_en: str | None,
    official_identifier: str,
    lon: float,
    lat: float,
    validation_status: str,
    validation_warnings: list[str],
    metadata: dict[str, Any],
) -> None:
    session.execute(
        text(
            """
            INSERT INTO poi_points (
              source_id, provenance_id, source_object_id, poi_category, name_he, name_en,
              official_identifier, geom, geom_2039, validation_status,
              validation_warnings, metadata
            ) VALUES (
              :source_id, :provenance_id, :source_object_id, :poi_category, :name_he, :name_en,
              :official_identifier,
              ST_SetSRID(ST_Point(:lon, :lat), 4326)::geometry(Point, 4326),
              ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)::geometry(Point, 2039),
              :validation_status, CAST(:validation_warnings AS jsonb), CAST(:metadata AS jsonb)
            )
            ON CONFLICT (source_id, official_identifier) WHERE official_identifier IS NOT NULL DO UPDATE SET
              provenance_id = EXCLUDED.provenance_id,
              source_object_id = EXCLUDED.source_object_id,
              poi_category = EXCLUDED.poi_category,
              name_he = EXCLUDED.name_he,
              name_en = EXCLUDED.name_en,
              geom = EXCLUDED.geom,
              geom_2039 = EXCLUDED.geom_2039,
              validation_status = EXCLUDED.validation_status,
              validation_warnings = EXCLUDED.validation_warnings,
              metadata = EXCLUDED.metadata,
              fetched_at = now(),
              updated_at = now()
            """
        ),
        {
            "source_id": source_id,
            "provenance_id": provenance_id,
            "source_object_id": source_object_id,
            "poi_category": poi_category,
            "name_he": name_he,
            "name_en": name_en,
            "official_identifier": official_identifier,
            "lon": lon,
            "lat": lat,
            "validation_status": validation_status,
            "validation_warnings": json.dumps(validation_warnings, ensure_ascii=False),
            "metadata": json.dumps(metadata, ensure_ascii=False, default=str),
        },
    )


def _upsert_coverage(
    session: Session,
    municipality_code: str,
    municipality_name_he: str,
    layer_key: str,
    status: str,
    source_id: str,
    feature_count: int,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO layer_coverage (
              municipality_code, municipality_name_he, layer_key, status, source_id,
              feature_count, last_successful_ingest_at, updated_at
            ) VALUES (
              :municipality_code, :municipality_name_he, :layer_key, :status, :source_id,
              :feature_count, now(), now()
            )
            ON CONFLICT (municipality_code, layer_key) DO UPDATE SET
              municipality_name_he = EXCLUDED.municipality_name_he,
              status = EXCLUDED.status,
              source_id = EXCLUDED.source_id,
              feature_count = EXCLUDED.feature_count,
              last_successful_ingest_at = now(),
              updated_at = now()
            """
        ),
        {
            "municipality_code": municipality_code,
            "municipality_name_he": municipality_name_he,
            "layer_key": layer_key,
            "status": status,
            "source_id": source_id,
            "feature_count": feature_count,
        },
    )


def _refresh_national_polygon_coverage(session: Session, *, source_id: str, layer_key: str, table_name: str) -> None:
    if not _has_municipal_boundaries(session):
        return
    _refresh_national_coverage_for_table(
        session,
        source_id=source_id,
        layer_key=layer_key,
        table_name=table_name,
        point_sql="ST_PointOnSurface(feature.geom)",
    )


def _refresh_national_point_coverage(session: Session, *, source_id: str, layer_key: str, table_name: str) -> None:
    if not _has_municipal_boundaries(session):
        return
    _refresh_national_coverage_for_table(
        session,
        source_id=source_id,
        layer_key=layer_key,
        table_name=table_name,
        point_sql="feature.geom",
    )


def _refresh_national_coverage_for_table(
    session: Session,
    *,
    source_id: str,
    layer_key: str,
    table_name: str,
    point_sql: str,
) -> None:
    if table_name not in {"plans", "parcels", "poi_points"}:
        raise ValueError(f"unsupported_national_coverage_table:{table_name}")
    session.execute(
        text(
            f"""
            INSERT INTO layer_coverage (
              municipality_code, municipality_name_he, layer_key, status, source_id,
              feature_count, last_successful_ingest_at, updated_at
            )
            SELECT boundary.municipality_code, boundary.municipality_name_he, :layer_key,
                   'national_available', :source_id, count(feature.id), now(), now()
            FROM official_municipal_boundaries boundary
            JOIN {table_name} feature
              ON feature.source_id = :source_id
             AND ST_Covers(boundary.geom, {point_sql})
            GROUP BY boundary.municipality_code, boundary.municipality_name_he
            ON CONFLICT (municipality_code, layer_key) DO UPDATE SET
              municipality_name_he = EXCLUDED.municipality_name_he,
              status = EXCLUDED.status,
              source_id = EXCLUDED.source_id,
              feature_count = EXCLUDED.feature_count,
              last_successful_ingest_at = now(),
              updated_at = now()
            """
        ),
        {"source_id": source_id, "layer_key": layer_key},
    )


def _assert_context_only_source(session: Session, source_id: str) -> None:
    row = session.execute(
        text(
            """
            SELECT provider_key, provenance_level, display_status, display_as_official
            FROM source_registry
            WHERE source_id = :source_id
            """
        ),
        {"source_id": source_id},
    ).mappings().first()
    if row is None:
        raise ValueError(f"source not found: {source_id}")
    if str(row["provider_key"]).lower() != "osm":
        raise ValueError("OSM context import requires provider_key=osm")
    if str(row["provenance_level"]) != "context" or str(row["display_status"]) != "context_only":
        raise ValueError("OSM context import requires provenance_level=context and display_status=context_only")
    if bool(row["display_as_official"]):
        raise ValueError("OSM context import cannot use a source displayed as official")


def _first_named_shapefile(root: Path, names: list[str]) -> Path | None:
    by_name = {path.name.lower(): path for path in root.rglob("*.shp")}
    for name in names:
        path = by_name.get(name.lower())
        if path is not None:
            return path
    return None


def _spatial_bbox(*, center_lon: float | None, center_lat: float | None, radius_m: float | None) -> tuple[float, float, float, float] | None:
    if center_lon is None or center_lat is None or radius_m is None:
        return None
    radius = max(float(radius_m), 0.0)
    lat_delta = radius / 111_320.0
    lon_scale = max(math.cos(math.radians(float(center_lat))), 0.2)
    lon_delta = radius / (111_320.0 * lon_scale)
    return (float(center_lon) - lon_delta, float(center_lat) - lat_delta, float(center_lon) + lon_delta, float(center_lat) + lat_delta)


def _shape_intersects_bbox(shape: Any, bbox: tuple[float, float, float, float]) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    shape_bbox = getattr(shape, "bbox", None)
    if isinstance(shape_bbox, (list, tuple)) and len(shape_bbox) >= 4:
        s_min_lon, s_min_lat, s_max_lon, s_max_lat = map(float, shape_bbox[:4])
        return not (s_max_lon < min_lon or s_min_lon > max_lon or s_max_lat < min_lat or s_min_lat > max_lat)
    for point in getattr(shape, "points", []) or []:
        if len(point) >= 2 and min_lon <= float(point[0]) <= max_lon and min_lat <= float(point[1]) <= max_lat:
            return True
    return False


def _normalise_context_geometry(geometry: dict[str, Any]) -> dict[str, Any]:
    geometry_type = geometry.get("type")
    if geometry_type == "Polygon":
        return normalize_polygon_geometry(geometry)
    if geometry_type == "LineString":
        return {"type": "MultiLineString", "coordinates": [geometry.get("coordinates", [])]}
    return geometry


def _osm_source_object_id(properties: Mapping[str, Any], *, fallback: int) -> str:
    value = _first_present(properties, ["osm_id", "osm_way_id", "id"])
    return value or f"row-{fallback}"


def _osm_poi_category(properties: Mapping[str, Any]) -> str:
    # Geofabrik exposes the OSM feature class as fclass; keep the raw class for generic context filtering.
    return _first_present(properties, ["fclass", "class", "type", "amenity"]) or "osm_poi"


def _refresh_osm_context_coverage(session: Session, *, source_id: str) -> None:
    _refresh_context_coverage_for_table(
        session,
        source_id=source_id,
        layer_key="buildings",
        table_name="buildings",
        point_sql="ST_PointOnSurface(feature.geom)",
    )
    _refresh_context_coverage_for_table(
        session,
        source_id=source_id,
        layer_key="context_pois",
        table_name="context_pois",
        point_sql="feature.geom",
    )


def _has_municipal_boundaries(session: Session) -> bool:
    count = session.execute(text("SELECT count(*) FROM official_municipal_boundaries")).scalar_one()
    return int(count or 0) > 0


def _refresh_context_coverage_for_table(
    session: Session,
    *,
    source_id: str,
    layer_key: str,
    table_name: str,
    point_sql: str,
) -> None:
    session.execute(
        text(
            f"""
            INSERT INTO layer_coverage (
              municipality_code, municipality_name_he, layer_key, status, source_id,
              feature_count, last_successful_ingest_at, updated_at
            )
            SELECT boundary.municipality_code, boundary.municipality_name_he, :layer_key,
                   'context_available', :source_id, count(feature.id), now(), now()
            FROM official_municipal_boundaries boundary
            JOIN {table_name} feature
              ON feature.source_id = :source_id
             AND ST_Covers(boundary.geom, {point_sql})
            GROUP BY boundary.municipality_code, boundary.municipality_name_he
            ON CONFLICT (municipality_code, layer_key) DO UPDATE SET
              municipality_name_he = EXCLUDED.municipality_name_he,
              status = EXCLUDED.status,
              source_id = EXCLUDED.source_id,
              feature_count = EXCLUDED.feature_count,
              last_successful_ingest_at = now(),
              updated_at = now()
            """
        ),
        {"source_id": source_id, "layer_key": layer_key},
    )


def _csv_row_feature(row: Mapping[str, Any], *, config: Mapping[str, Any]) -> dict[str, Any]:
    geometry_field = _mapping_spec(config, "geometry_geojson")
    if geometry_field:
        value = _mapped_value(row, config, "geometry_geojson")
        geometry = json.loads(value) if value else {}
    else:
        lon = _float_or_none(_mapped_value(row, config, "lon"))
        lat = _float_or_none(_mapped_value(row, config, "lat"))
        if lon is None or lat is None:
            geometry = {}
        else:
            geometry = {"type": "Point", "coordinates": [lon, lat]}
            input_srid = int(config.get("input_srid", 4326))
            if input_srid != 4326:
                geometry = transform_geojson_geometry(geometry, source_srid=input_srid, target_srid=4326)
    return {"type": "Feature", "properties": dict(row), "geometry": geometry}


def _municipal_shapefile_path(root: Path, config: Mapping[str, Any]) -> Path | None:
    configured = str(config.get("shapefile_name") or "").strip().lower()
    shapefiles = list(root.rglob("*.shp"))
    if configured:
        return next((path for path in shapefiles if path.name.lower() == configured), None)
    return shapefiles[0] if shapefiles else None


def _municipal_layer_key(config: Mapping[str, Any]) -> str:
    layer_key = str(config.get("layer_key") or "").strip()
    if layer_key not in {"neighborhoods", "address_points", "poi_points", "buildings", "context_geometries"}:
        raise ValueError(f"unsupported_municipal_layer:{layer_key}")
    return layer_key


def _municipal_feature_municipality_code(properties: Mapping[str, Any], config: Mapping[str, Any]) -> str | None:
    configured = str(config.get("municipality_code") or "").strip()
    if configured:
        return normalize_municipality_code(configured)
    value = _mapped_value(properties, config, "municipality_code")
    return normalize_municipality_code(value) if value else None


def _municipal_source_object_id(properties: Mapping[str, Any], geometry: dict[str, Any], *, config: Mapping[str, Any], index: int) -> str:
    value = _mapped_value(properties, config, "source_object_id") or _mapped_value(properties, config, "raw_feature_id")
    prefix = str(config.get("source_object_prefix") or "").strip()
    if value:
        text_value = str(value).strip()
        return f"{prefix}:{text_value}" if prefix and not text_value.startswith(f"{prefix}:") else text_value
    # Persist a deterministic derived key so reruns of sources without IDs remain idempotent.
    digest = hashlib.sha256(json.dumps({"properties": dict(properties), "geometry": geometry}, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return f"derived-{digest[:24]}-{index}"


def _upsert_municipal_neighborhood(
    session: Session,
    source_id: str,
    provenance_id: str,
    source_object_id: str,
    municipality_code: str | None,
    geom_hash: str,
    geometry: dict[str, Any],
    result: Any,
    properties: Mapping[str, Any],
    metadata: dict[str, Any],
    config: Mapping[str, Any],
) -> None:
    name_he = _mapped_value(properties, config, "name_he") or source_object_id
    session.execute(
        text(
            """
            INSERT INTO neighborhoods (
              source_id, provenance_id, municipality_code, name_he, name_en,
              normalized_geometry_hash, geom, geom_2039, validation_status,
              validation_warnings, metadata
            ) VALUES (
              :source_id, :provenance_id, :municipality_code, :name_he, :name_en,
              :normalized_geometry_hash,
              ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry))::geometry(MultiPolygon, 4326),
              ST_Transform(ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry)), 2039)::geometry(MultiPolygon, 2039),
              :validation_status, CAST(:validation_warnings AS jsonb), CAST(:metadata AS jsonb)
            )
            ON CONFLICT (source_id, municipality_code, name_he, normalized_geometry_hash) DO UPDATE SET
              provenance_id = EXCLUDED.provenance_id,
              name_en = EXCLUDED.name_en,
              geom = EXCLUDED.geom,
              geom_2039 = EXCLUDED.geom_2039,
              validation_status = EXCLUDED.validation_status,
              validation_warnings = EXCLUDED.validation_warnings,
              metadata = EXCLUDED.metadata,
              fetched_at = now(),
              updated_at = now()
            """
        ),
        _municipal_polygon_params(source_id, provenance_id, municipality_code, geom_hash, geometry, result, metadata)
        | {"name_he": name_he, "name_en": _mapped_value(properties, config, "name_en")},
    )


def _upsert_municipal_building(
    session: Session,
    source_id: str,
    provenance_id: str,
    source_object_id: str,
    municipality_code: str | None,
    geom_hash: str,
    geometry: dict[str, Any],
    result: Any,
    metadata: dict[str, Any],
) -> None:
    session.execute(
        text(
            """
            INSERT INTO buildings (
              source_id, provenance_id, source_object_id, municipality_code,
              normalized_geometry_hash, geom, geom_2039, validation_status,
              validation_warnings, metadata
            ) VALUES (
              :source_id, :provenance_id, :source_object_id, :municipality_code,
              :normalized_geometry_hash,
              ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry))::geometry(MultiPolygon, 4326),
              ST_Transform(ST_Multi(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry)), 2039)::geometry(MultiPolygon, 2039),
              :validation_status, CAST(:validation_warnings AS jsonb), CAST(:metadata AS jsonb)
            )
            ON CONFLICT (source_id, source_object_id) WHERE source_object_id IS NOT NULL DO UPDATE SET
              provenance_id = EXCLUDED.provenance_id,
              municipality_code = EXCLUDED.municipality_code,
              normalized_geometry_hash = EXCLUDED.normalized_geometry_hash,
              geom = EXCLUDED.geom,
              geom_2039 = EXCLUDED.geom_2039,
              validation_status = EXCLUDED.validation_status,
              validation_warnings = EXCLUDED.validation_warnings,
              metadata = EXCLUDED.metadata,
              fetched_at = now(),
              updated_at = now()
            """
        ),
        _municipal_polygon_params(source_id, provenance_id, municipality_code, geom_hash, geometry, result, metadata)
        | {"source_object_id": source_object_id},
    )


def _upsert_municipal_address_point(
    session: Session,
    source_id: str,
    provenance_id: str,
    source_object_id: str,
    municipality_code: str | None,
    geometry: dict[str, Any],
    result: Any,
    properties: Mapping[str, Any],
    metadata: dict[str, Any],
    config: Mapping[str, Any],
) -> None:
    lon, lat = _point_lon_lat(geometry)
    session.execute(
        text(
            """
            INSERT INTO address_points (
              source_id, provenance_id, source_object_id, municipality_code, street_name_he,
              house_number, full_address_he, geom, geom_2039, validation_status,
              validation_warnings, metadata
            ) VALUES (
              :source_id, :provenance_id, :source_object_id, :municipality_code, :street_name_he,
              :house_number, :full_address_he,
              ST_SetSRID(ST_Point(:lon, :lat), 4326)::geometry(Point, 4326),
              ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)::geometry(Point, 2039),
              :validation_status, CAST(:validation_warnings AS jsonb), CAST(:metadata AS jsonb)
            )
            ON CONFLICT (source_id, source_object_id) WHERE source_object_id IS NOT NULL DO UPDATE SET
              provenance_id = EXCLUDED.provenance_id,
              municipality_code = EXCLUDED.municipality_code,
              street_name_he = EXCLUDED.street_name_he,
              house_number = EXCLUDED.house_number,
              full_address_he = EXCLUDED.full_address_he,
              geom = EXCLUDED.geom,
              geom_2039 = EXCLUDED.geom_2039,
              validation_status = EXCLUDED.validation_status,
              validation_warnings = EXCLUDED.validation_warnings,
              metadata = EXCLUDED.metadata,
              fetched_at = now(),
              updated_at = now()
            """
        ),
        _municipal_point_params(source_id, provenance_id, source_object_id, municipality_code, lon, lat, result, metadata)
        | {
            "street_name_he": _mapped_value(properties, config, "street_name_he"),
            "house_number": _mapped_value(properties, config, "house_number"),
            "full_address_he": _mapped_value(properties, config, "full_address_he"),
        },
    )


def _upsert_municipal_poi_point(
    session: Session,
    source_id: str,
    provenance_id: str,
    source_object_id: str,
    municipality_code: str | None,
    geometry: dict[str, Any],
    result: Any,
    properties: Mapping[str, Any],
    metadata: dict[str, Any],
    config: Mapping[str, Any],
) -> None:
    lon, lat = _point_lon_lat(geometry)
    official_identifier = _mapped_value(properties, config, "official_identifier") or source_object_id
    prefix = str(config.get("source_object_prefix") or "").strip()
    if official_identifier and prefix and not str(official_identifier).startswith(f"{prefix}:"):
        official_identifier = f"{prefix}:{official_identifier}"
    session.execute(
        text(
            """
            INSERT INTO poi_points (
              source_id, provenance_id, source_object_id, poi_category, name_he, name_en,
              official_identifier, municipality_code, geom, geom_2039, validation_status,
              validation_warnings, metadata
            ) VALUES (
              :source_id, :provenance_id, :source_object_id, :poi_category, :name_he, :name_en,
              :official_identifier, :municipality_code,
              ST_SetSRID(ST_Point(:lon, :lat), 4326)::geometry(Point, 4326),
              ST_Transform(ST_SetSRID(ST_Point(:lon, :lat), 4326), 2039)::geometry(Point, 2039),
              :validation_status, CAST(:validation_warnings AS jsonb), CAST(:metadata AS jsonb)
            )
            ON CONFLICT (source_id, official_identifier) WHERE official_identifier IS NOT NULL DO UPDATE SET
              provenance_id = EXCLUDED.provenance_id,
              source_object_id = EXCLUDED.source_object_id,
              poi_category = EXCLUDED.poi_category,
              name_he = EXCLUDED.name_he,
              name_en = EXCLUDED.name_en,
              municipality_code = EXCLUDED.municipality_code,
              geom = EXCLUDED.geom,
              geom_2039 = EXCLUDED.geom_2039,
              validation_status = EXCLUDED.validation_status,
              validation_warnings = EXCLUDED.validation_warnings,
              metadata = EXCLUDED.metadata,
              fetched_at = now(),
              updated_at = now()
            """
        ),
        _municipal_point_params(source_id, provenance_id, source_object_id, municipality_code, lon, lat, result, metadata)
        | {
            "poi_category": _mapped_value(properties, config, "poi_category") or str(config.get("default_poi_category") or "municipal_poi"),
            "name_he": _mapped_value(properties, config, "name_he"),
            "name_en": _mapped_value(properties, config, "name_en"),
            "official_identifier": official_identifier,
        },
    )


def _upsert_municipal_context_geometry(
    session: Session,
    source_id: str,
    provenance_id: str,
    source_object_id: str,
    geometry: dict[str, Any],
    result: Any,
    properties: Mapping[str, Any],
    metadata: dict[str, Any],
    config: Mapping[str, Any],
) -> None:
    context_layer = str(config.get("context_layer") or "municipal_context").strip()
    session.execute(
        text(
            """
            INSERT INTO context_geometries (
              source_id, provenance_id, source_object_id, context_layer, geometry_type,
              category, name, normalized_geometry_hash, geom, geom_2039,
              validation_status, validation_warnings, metadata
            ) VALUES (
              :source_id, :provenance_id, :source_object_id, :context_layer, :geometry_type,
              :category, :name, :normalized_geometry_hash,
              ST_Force2D(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry))::geometry(Geometry, 4326),
              ST_Transform(ST_Force2D(ST_MakeValid(ST_GeomFromGeoJSON(:geometry)::geometry)), 2039)::geometry(Geometry, 2039),
              :validation_status, CAST(:validation_warnings AS jsonb), CAST(:metadata AS jsonb)
            )
            ON CONFLICT (source_id, context_layer, normalized_geometry_hash) DO UPDATE SET
              provenance_id = EXCLUDED.provenance_id,
              source_object_id = COALESCE(EXCLUDED.source_object_id, context_geometries.source_object_id),
              geometry_type = EXCLUDED.geometry_type,
              category = EXCLUDED.category,
              name = EXCLUDED.name,
              normalized_geometry_hash = EXCLUDED.normalized_geometry_hash,
              geom = EXCLUDED.geom,
              geom_2039 = EXCLUDED.geom_2039,
              validation_status = EXCLUDED.validation_status,
              validation_warnings = EXCLUDED.validation_warnings,
              metadata = EXCLUDED.metadata,
              fetched_at = now(),
              updated_at = now()
            """
        ),
        {
            "source_id": source_id,
            "provenance_id": provenance_id,
            "source_object_id": source_object_id,
            "context_layer": context_layer,
            "geometry_type": str(geometry.get("type") or "Geometry"),
            "category": _mapped_value(properties, config, "category") or str(config.get("default_category") or context_layer),
            "name": _mapped_value(properties, config, "name_he") or _mapped_value(properties, config, "name_en"),
            "normalized_geometry_hash": geometry_hash(geometry),
            "geometry": json.dumps(geometry),
            "validation_status": result.status,
            "validation_warnings": json.dumps(result.warnings, ensure_ascii=False),
            "metadata": json.dumps(metadata | {"context_layer": context_layer}, ensure_ascii=False, default=str),
        },
    )


def _municipal_polygon_params(
    source_id: str,
    provenance_id: str,
    municipality_code: str | None,
    geom_hash: str,
    geometry: dict[str, Any],
    result: Any,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "provenance_id": provenance_id,
        "municipality_code": municipality_code,
        "normalized_geometry_hash": geom_hash,
        "geometry": json.dumps(geometry),
        "validation_status": result.status,
        "validation_warnings": json.dumps(result.warnings, ensure_ascii=False),
        "metadata": json.dumps(metadata, ensure_ascii=False, default=str),
    }


def _municipal_point_params(
    source_id: str,
    provenance_id: str,
    source_object_id: str,
    municipality_code: str | None,
    lon: float,
    lat: float,
    result: Any,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "provenance_id": provenance_id,
        "source_object_id": source_object_id,
        "municipality_code": municipality_code,
        "lon": lon,
        "lat": lat,
        "validation_status": result.status,
        "validation_warnings": json.dumps(result.warnings, ensure_ascii=False),
        "metadata": json.dumps(metadata, ensure_ascii=False, default=str),
    }


def _point_lon_lat(geometry: dict[str, Any]) -> tuple[float, float]:
    coordinates = geometry.get("coordinates") or []
    return float(coordinates[0]), float(coordinates[1])


def _refresh_municipal_coverage(session: Session, *, source_id: str, layer_key: str, config: Mapping[str, Any]) -> None:
    municipality_code = normalize_municipality_code(config.get("municipality_code")) if config.get("municipality_code") else None
    if not municipality_code:
        return
    municipality_name_he = str(config.get("municipality_name_he") or "").strip() or None
    status = _municipal_coverage_status(session, source_id)
    count = _municipal_layer_feature_count(session, source_id=source_id, layer_key=layer_key, municipality_code=municipality_code)
    _upsert_coverage(session, municipality_code, municipality_name_he or municipality_code, layer_key, status, source_id, count)


def _municipal_coverage_status(session: Session, source_id: str) -> str:
    row = session.execute(
        text("SELECT enabled, reuse_status, display_status FROM source_registry WHERE source_id = :source_id"),
        {"source_id": source_id},
    ).mappings().first()
    if row is None:
        raise ValueError(f"source not found: {source_id}")
    if not bool(row["enabled"]):
        return "not_enabled"
    if str(row["reuse_status"]) == "municipal_license_under_review" or str(row["display_status"]) == "municipal_license_under_review":
        return "municipal_license_under_review"
    return "municipal_open_available"


def _municipal_layer_feature_count(session: Session, *, source_id: str, layer_key: str, municipality_code: str) -> int:
    if layer_key == "context_geometries":
        return int(
            session.execute(
                text("SELECT count(*) FROM context_geometries WHERE source_id = :source_id"),
                {"source_id": source_id},
            ).scalar_one()
            or 0
        )
    if layer_key not in {"neighborhoods", "address_points", "poi_points", "buildings"}:
        return 0
    return int(
        session.execute(
            text(f"SELECT count(*) FROM {layer_key} WHERE source_id = :source_id AND municipality_code = :municipality_code"),
            {"source_id": source_id, "municipality_code": municipality_code},
        ).scalar_one()
        or 0
    )


def _mapped_value(properties: Mapping[str, Any], config: Mapping[str, Any], key: str) -> str | None:
    spec = _mapping_spec(config, key)
    candidates = spec if isinstance(spec, list) else [spec] if isinstance(spec, str) else [key]
    for candidate in candidates:
        if not candidate:
            continue
        value = _case_insensitive_value(properties, str(candidate))
        if value not in (None, ""):
            return str(value).strip()
    return None


def _mapping_spec(config: Mapping[str, Any], key: str) -> Any:
    mappings = config.get("field_mappings") or config.get("fields") or {}
    return mappings.get(key) if isinstance(mappings, Mapping) else None


def _case_insensitive_value(properties: Mapping[str, Any], key: str) -> Any:
    if key in properties:
        return properties[key]
    normalized = {str(field).lower(): field for field in properties.keys()}
    actual = normalized.get(key.lower())
    return properties.get(actual) if actual is not None else None


def _properties(feature: Mapping[str, Any]) -> dict[str, Any]:
    value = feature.get("properties") or feature.get("attributes") or {}
    return dict(value) if isinstance(value, Mapping) else {}


def _geometry(feature: Mapping[str, Any]) -> dict[str, Any]:
    geometry = feature.get("geometry")
    return dict(geometry) if isinstance(geometry, Mapping) else {}


def _polygon_params(
    source_id: str,
    provenance_id: str,
    plan_number: str,
    plan_name: Any,
    geom_hash: str,
    geometry: dict[str, Any],
    result: Any,
    properties: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "provenance_id": provenance_id,
        "plan_number": plan_number,
        "plan_name": str(plan_name).strip() if plan_name is not None else None,
        "normalized_geometry_hash": geom_hash,
        "geometry": json.dumps(geometry),
        "validation_status": result.status,
        "validation_warnings": json.dumps(result.warnings, ensure_ascii=False),
        "metadata": json.dumps({"properties": properties}, ensure_ascii=False, default=str),
    }


def _first_field(fields: list[str], candidates: list[str]) -> str | None:
    normalized = {field.lower(): field for field in fields}
    for candidate in candidates:
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]
    return None


def _field_value(properties: Mapping[str, Any], field_name: str | None) -> str | None:
    if not field_name:
        return None
    value = properties.get(field_name)
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _shape_bbox_intersects(shape_bbox: Any, target_bbox: tuple[float, float, float, float]) -> bool:
    if not isinstance(shape_bbox, (list, tuple)) or len(shape_bbox) < 4:
        return False
    min_x, min_y, max_x, max_y = (float(value) for value in shape_bbox[:4])
    target_min_x, target_min_y, target_max_x, target_max_y = target_bbox
    return not (max_x < target_min_x or min_x > target_max_x or max_y < target_min_y or min_y > target_max_y)


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_present(row: Mapping[str, Any], fields: list[str]) -> str | None:
    normalized = {key.lower(): key for key in row.keys()}
    for field in fields:
        key = normalized.get(field.lower())
        if key is not None and row.get(key) not in (None, ""):
            return str(row[key]).strip()
    return None


def _transform_coordinates(coordinates: Any, transformer: Any) -> Any:
    if isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2 and all(isinstance(v, (int, float)) for v in coordinates[:2]):
        lon, lat = transformer.transform(float(coordinates[0]), float(coordinates[1]))
        rest = list(coordinates[2:]) if len(coordinates) > 2 else []
        return [float(lon), float(lat), *rest]
    if isinstance(coordinates, list):
        return [_transform_coordinates(item, transformer) for item in coordinates]
    if isinstance(coordinates, tuple):
        return [_transform_coordinates(item, transformer) for item in coordinates]
    return coordinates


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
