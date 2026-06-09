from __future__ import annotations

import csv
import io
import json
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
    for feature in features:
        properties = _properties(feature)
        municipality_code = normalize_municipality_code(properties.get(code_field))
        municipality_name_he = str(properties.get(name_he_field) or "").strip()
        geometry = normalize_polygon_geometry(_geometry(feature))
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
            metadata={"properties": properties},
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
                "municipality_name_en": properties.get("name_en") or properties.get("NAME_EN"),
                "normalized_geometry_hash": geom_hash,
                "geometry": json.dumps(geometry),
                "validation_status": result.status,
                "validation_warnings": json.dumps(result.warnings, ensure_ascii=False),
                "metadata": json.dumps({"properties": properties}, ensure_ascii=False),
            },
        )
        _upsert_coverage(session, municipality_code, municipality_name_he, "boundaries", "national_available", source_id, 1)
        count += 1
    return ImportSummary(count, rejected, _dedupe(warnings))


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
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = fetch_json(
            service_url.rstrip("/") + "/query",
            {
                "f": "geojson",
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "true",
                "resultOffset": offset,
                "resultRecordCount": page_size,
            },
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
) -> ImportSummary:
    pages = fetch_arcgis_feature_pages(service_url=service_url, fetch_json=fetch_json, page_size=page_size)
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
) -> ImportSummary:
    import shapefile

    raw_uri = raw_storage.write_bytes(source_id=source_id, name=zip_path.name, content=zip_path.read_bytes())
    with tempfile.TemporaryDirectory() as tmp_dir:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(tmp_dir)
        shp_path = next(Path(tmp_dir).rglob("*.shp"), None)
        if shp_path is None:
            raise ValueError("MAPI ZIP does not contain a .shp file")
        reader = shapefile.Reader(str(shp_path))
        fields = [field[0] for field in reader.fields[1:]]
        mapping = resolve_parcel_fields(fields)
        count = 0
        rejected = 0
        warnings: list[str] = []
        for shape_record in reader.iterShapeRecords():
            properties = dict(zip(fields, shape_record.record))
            source_geometry = normalize_polygon_geometry(shape_record.shape.__geo_interface__)
            geometry = transform_geojson_geometry(source_geometry, source_srid=input_srid, target_srid=4326)
            result = validate_geojson_geometry(geometry, expected_type="MultiPolygon")
            gush = _field_value(properties, mapping["gush"])
            helka = _field_value(properties, mapping["helka"])
            source_object_id = _field_value(properties, mapping["source_object_id"])
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
    return ImportSummary(count, rejected, _dedupe(warnings))


def _school_lon_lat(row: Mapping[str, Any]) -> tuple[float, float] | None:
    lon = _float_or_none(_first_present(row, ["lon", "longitude", "x_wgs84", "lng"]))
    lat = _float_or_none(_first_present(row, ["lat", "latitude", "y_wgs84"]))
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
