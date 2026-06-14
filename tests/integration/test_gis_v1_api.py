from __future__ import annotations

import json
from collections.abc import Generator
from math import sqrt

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from municipality.gis_api import get_gis_db, router


def test_v1_health_reports_db_and_postgis_status() -> None:
    client = _client_with_seeded_sqlite()

    response = client.get("/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["api"]["status"] == "ok"
    assert payload["db"]["status"] == "ok"
    assert payload["postgis"]["status"] == "unavailable"


def test_v1_sources_support_filters_and_detail() -> None:
    client = _client_with_seeded_sqlite()

    response = client.get("/v1/sources", params={"layer_key": "plans", "enabled": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["source_id"] == "xplan_blue_lines"

    detail = client.get("/v1/sources/xplan_blue_lines")
    assert detail.status_code == 200
    assert detail.json()["display_as_official"] is True


def test_v1_coverage_exposes_explicit_missing_data_states() -> None:
    client = _client_with_seeded_sqlite()

    response = client.get("/v1/coverage")

    assert response.status_code == 200
    statuses = {item["status"] for item in response.json()["items"]}
    assert {"not_found", "not_ingested", "not_enabled"} <= statuses

    municipality = client.get("/v1/coverage/5000")
    assert municipality.status_code == 200
    assert municipality.json()["municipality_name_he"] == "תל אביב-יפו"


def test_v1_plan_and_parcel_endpoints_include_provenance_and_obey_geometry_defaults() -> None:
    client = _client_with_seeded_sqlite()

    plan = client.get("/v1/plans/603-1373075")

    assert plan.status_code == 200
    plan_item = plan.json()["items"][0]
    assert plan_item["source"]["source_id"] == "xplan_blue_lines"
    assert plan_item["provenance_id"] == "prov-plan-1"
    assert plan_item["plan_metadata"]["station_desc"] == "בהפקדה"
    assert plan_item["plan_metadata"]["pl_url"] == "https://example.test/plan/603-1373075"
    assert "geometry" not in plan_item

    full_plan = client.get("/v1/plans/603-1373075", params={"include_geometry": True, "geometry_detail": "full"})
    assert full_plan.status_code == 200
    assert full_plan.json()["items"][0]["geometry"]["type"] == "MultiPolygon"

    parcel = client.get("/v1/parcels", params={"gush": "123", "helka": "45"})
    assert parcel.status_code == 200
    parcel_item = parcel.json()["items"][0]
    assert parcel_item["source"]["source_id"] == "mapi_parcels"
    assert parcel_item["provenance_id"] == "prov-parcel-1"
    assert "geometry" not in parcel_item

    no_match = client.get("/v1/parcels", params={"gush": "999", "helka": "1"})
    assert no_match.status_code == 200
    assert no_match.json()["status"] == "not_found"
    assert no_match.json()["items"] == []


def test_v1_search_detects_plan_and_parcel_identifiers_first() -> None:
    client = _client_with_seeded_sqlite()

    plan = client.get("/v1/search", params={"q": "תכנית 603-1373075"})
    parcel = client.get("/v1/search", params={"q": "גוש 123 חלקה 45"})

    assert plan.status_code == 200
    assert plan.json()["items"][0]["feature_type"] == "plan"
    assert plan.json()["items"][0]["precision"] == "exact_identifier"
    assert plan.json()["items"][0]["source"]["source_id"] == "xplan_blue_lines"
    assert plan.json()["items"][0]["can_persist"] is True

    assert parcel.status_code == 200
    assert parcel.json()["items"][0]["feature_type"] == "parcel"
    assert parcel.json()["items"][0]["precision"] == "exact_identifier"
    assert parcel.json()["items"][0]["source"]["source_id"] == "mapi_parcels"


def test_v1_nearby_filters_categories_and_returns_provenance() -> None:
    client = _client_with_seeded_sqlite()

    response = client.get("/v1/nearby", params={"lon": 34.78, "lat": 32.08, "categories": "school"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["poi_category"] == "school"
    assert payload["items"][0]["source"]["source_id"] == "moe_school_coordinates"
    assert payload["items"][0]["provenance_id"] == "prov-school-1"


def test_v1_point_report_returns_layers_and_explicit_missing_states() -> None:
    client = _client_with_seeded_sqlite()

    response = client.get("/v1/point-report", params={"lon": 34.78, "lat": 32.08})

    assert response.status_code == 200
    payload = response.json()
    assert payload["municipality"]["municipality_code"] == "5000"
    assert payload["municipality"]["source"]["source_id"] == "moin_municipal_boundaries"
    assert payload["parcel"]["status"] == "found"
    assert payload["plans"]["status"] == "found"
    assert payload["neighborhood"]["status"] == "not_enabled"
    assert payload["nearby_pois"]["count"] >= 1
    for section_name in ("parcel", "plans", "nearby_pois"):
        for item in payload[section_name]["items"]:
            assert item["source"]
            assert item["provenance_id"]


def _client_with_seeded_sqlite() -> TestClient:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    event.listen(engine, "connect", _register_spatial_sqlite_functions)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE source_registry (
                  source_id TEXT PRIMARY KEY,
                  provider_key TEXT NOT NULL,
                  source_kind TEXT NOT NULL,
                  name_he TEXT NOT NULL,
                  name_en TEXT,
                  owner_name TEXT,
                  owner_url TEXT,
                  source_url TEXT,
                  municipality_code TEXT,
                  layer_keys TEXT NOT NULL,
                  provenance_level TEXT NOT NULL,
                  reuse_status TEXT NOT NULL,
                  geometry_status TEXT NOT NULL,
                  display_status TEXT NOT NULL,
                  source_is_official INTEGER NOT NULL,
                  reuse_is_verified INTEGER NOT NULL,
                  display_as_official INTEGER NOT NULL,
                  legal_review_approved INTEGER NOT NULL,
                  enabled INTEGER NOT NULL,
                  license_name TEXT,
                  license_url TEXT,
                  attribution TEXT,
                  notes TEXT,
                  metadata TEXT,
                  created_at TEXT,
                  updated_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE layer_coverage (
                  municipality_code TEXT NOT NULL,
                  municipality_name_he TEXT,
                  layer_key TEXT NOT NULL,
                  status TEXT NOT NULL,
                  source_id TEXT,
                  feature_count INTEGER NOT NULL DEFAULT 0,
                  last_successful_ingest_at TEXT,
                  updated_at TEXT,
                  PRIMARY KEY (municipality_code, layer_key)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE official_municipal_boundaries (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  provenance_id TEXT NOT NULL,
                  municipality_code TEXT NOT NULL,
                  municipality_name_he TEXT NOT NULL,
                  geom TEXT NOT NULL,
                  fetched_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE plans (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  provenance_id TEXT NOT NULL,
                  plan_number TEXT NOT NULL,
                  plan_name TEXT,
                  validation_status TEXT NOT NULL,
                  validation_warnings TEXT NOT NULL,
                  geom TEXT NOT NULL,
                  geom_2039 TEXT,
                  metadata TEXT NOT NULL,
                  fetched_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE parcels (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  provenance_id TEXT NOT NULL,
                  source_object_id TEXT,
                  gush TEXT,
                  helka TEXT,
                  parcel_label TEXT,
                  validation_status TEXT NOT NULL,
                  validation_warnings TEXT NOT NULL,
                  geom TEXT NOT NULL,
                  geom_2039 TEXT,
                  fetched_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE neighborhoods (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  provenance_id TEXT NOT NULL,
                  municipality_code TEXT,
                  name_he TEXT NOT NULL,
                  name_en TEXT,
                  validation_status TEXT NOT NULL,
                  validation_warnings TEXT NOT NULL,
                  geom TEXT NOT NULL,
                  geom_2039 TEXT,
                  fetched_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE poi_points (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  provenance_id TEXT NOT NULL,
                  source_object_id TEXT,
                  poi_category TEXT NOT NULL,
                  name_he TEXT,
                  name_en TEXT,
                  official_identifier TEXT,
                  validation_status TEXT NOT NULL,
                  validation_warnings TEXT NOT NULL,
                  geom TEXT NOT NULL,
                  geom_2039 TEXT,
                  fetched_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE context_pois (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  provenance_id TEXT NOT NULL,
                  source_object_id TEXT,
                  poi_category TEXT NOT NULL,
                  name TEXT,
                  validation_status TEXT NOT NULL,
                  validation_warnings TEXT NOT NULL,
                  geom_2039 TEXT,
                  fetched_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO source_registry VALUES
                ('moin_municipal_boundaries', 'moin', 'national_official', 'גבולות רשויות', 'Boundaries', NULL, NULL, NULL, NULL,
                 '{boundaries}', 'official_national', 'verified', 'official_geometry', 'official', 1, 1, 1, 0, 1,
                 NULL, NULL, NULL, NULL, '{}', NULL, NULL),
                ('xplan_blue_lines', 'xplan', 'national_official', 'תכניות', 'Plans', NULL, NULL, NULL, NULL,
                 '{plans}', 'official_national', 'verified', 'official_geometry', 'official', 1, 1, 1, 0, 1,
                 NULL, NULL, NULL, NULL, '{}', NULL, NULL),
                ('mapi_parcels', 'mapi', 'national_official', 'חלקות', 'Parcels', NULL, NULL, NULL, NULL,
                 '{parcels}', 'official_national', 'verified', 'official_geometry', 'official', 1, 1, 1, 0, 1,
                 NULL, NULL, NULL, NULL, '{}', NULL, NULL),
                ('moe_school_coordinates', 'moe', 'national_official', 'מוסדות חינוך', 'Schools', NULL, NULL, NULL, NULL,
                 '{poi_points}', 'official_national', 'verified', 'official_geometry', 'official', 1, 1, 1, 0, 1,
                 NULL, NULL, NULL, NULL, '{}', NULL, NULL),
                ('osm_context', 'osm', 'osm', 'OpenStreetMap', 'OpenStreetMap', NULL, NULL, NULL, NULL,
                 '{context_pois}', 'context', 'verified', 'context_geometry', 'context_only', 0, 1, 0, 0, 1,
                 NULL, NULL, 'OpenStreetMap contributors', NULL, '{}', NULL, NULL),
                ('tel_aviv_open_data_discovered', 'tel_aviv', 'municipal_open_data', 'תל אביב', 'Tel Aviv', NULL, NULL, NULL, '5000',
                 '{address_points,neighborhoods}', 'official_municipal', 'municipal_license_under_review', 'unknown', 'unavailable', 1, 0, 0, 0, 0,
                 NULL, NULL, NULL, NULL, '{}', NULL, NULL)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO layer_coverage VALUES
                ('4000', 'חיפה', 'plans', 'not_ingested', 'xplan_blue_lines', 0, NULL, NULL),
                ('5000', 'תל אביב-יפו', 'address_points', 'not_enabled', 'tel_aviv_open_data_discovered', 0, NULL, NULL),
                ('5000', 'תל אביב-יפו', 'parcels', 'national_available', 'mapi_parcels', 1, NULL, NULL),
                ('5000', 'תל אביב-יפו', 'plans', 'national_available', 'xplan_blue_lines', 1, NULL, NULL),
                ('5000', 'תל אביב-יפו', 'neighborhoods', 'not_enabled', 'tel_aviv_open_data_discovered', 0, NULL, NULL),
                ('8310', 'ירוחם', 'neighborhoods', 'not_found', NULL, 0, NULL, NULL)
                """
            )
        )
        boundary = _polygon_json(34.70, 32.00, 34.90, 32.20)
        connection.execute(
            text(
                """
                INSERT INTO official_municipal_boundaries VALUES
                ('boundary-1', 'moin_municipal_boundaries', 'prov-boundary-1', '5000', 'תל אביב-יפו', :boundary, '2026-06-09')
                """
            ),
            {"boundary": boundary},
        )
        connection.execute(
            text(
                """
                INSERT INTO plans VALUES
                ('plan-1', 'xplan_blue_lines', 'prov-plan-1', '603-1373075', 'תכנית בדיקה', 'valid', '[]', :geom, :geom, :metadata, '2026-06-09')
                """
            ),
            {
                "geom": _polygon_json(34.75, 32.05, 34.83, 32.12),
                "metadata": json.dumps(
                    {
                        "plan": {
                            "station_desc": "בהפקדה",
                            "internet_short_status": "ניתן להגיש התנגדויות",
                            "pl_url": "https://example.test/plan/603-1373075",
                        },
                        "properties": {"station_desc": "בהפקדה"},
                    },
                    ensure_ascii=False,
                ),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO parcels VALUES
                ('parcel-1', 'mapi_parcels', 'prov-parcel-1', 'parcel-source-1', '123', '45', '123 / 45', 'valid', '[]', :geom, :geom, '2026-06-09')
                """
            ),
            {"geom": _polygon_json(34.76, 32.06, 34.80, 32.10)},
        )
        school_point = _point_json(34.781, 32.081)
        stop_point = _point_json(34.782, 32.082)
        context_point = _point_json(34.783, 32.083)
        connection.execute(
            text(
                """
                INSERT INTO poi_points VALUES
                ('school-1', 'moe_school_coordinates', 'prov-school-1', 'school-1', 'school', 'בית ספר בדיקה', 'Test School', '1001', 'valid', '[]', :school, :school, '2026-06-09'),
                ('stop-1', 'moe_school_coordinates', 'prov-stop-1', 'stop-1', 'transport_stop', 'תחנת בדיקה', 'Test Stop', '2001', 'valid', '[]', :stop, :stop, '2026-06-09')
                """
            ),
            {"school": school_point, "stop": stop_point},
        )
        connection.execute(
            text(
                """
                INSERT INTO context_pois VALUES
                ('context-1', 'osm_context', 'prov-context-1', 'osm-1', 'cafe', 'בית קפה הקשר', 'valid', '[]', :context, '2026-06-09')
                """
            ),
            {"context": context_point},
        )

    app = FastAPI()
    app.include_router(router)

    def override_db() -> Generator[Session, None, None]:
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_gis_db] = override_db
    return TestClient(app)


def _register_spatial_sqlite_functions(connection: object, _record: object) -> None:
    connection.create_function("ST_AsGeoJSON", 1, lambda geom: geom)
    connection.create_function("ST_SetSRID", 2, lambda geom, _srid: geom)
    connection.create_function("ST_Transform", 2, lambda geom, _srid: geom)
    connection.create_function("ST_Point", 2, _point_json)
    connection.create_function("ST_Centroid", 1, _centroid_json)
    connection.create_function("ST_Envelope", 1, _envelope_json)
    connection.create_function("ST_SimplifyPreserveTopology", 2, lambda geom, _tolerance: geom)
    connection.create_function("ST_Covers", 2, _covers)
    connection.create_function("ST_Distance", 2, _distance_m)
    connection.create_function("ST_DWithin", 3, lambda geom, point, radius: 1 if _distance_m(geom, point) <= float(radius) else 0)


def _polygon_json(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> str:
    return json.dumps(
        {
            "type": "MultiPolygon",
            "coordinates": [[[[min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat]]]],
        }
    )


def _point_json(lon: float, lat: float) -> str:
    return json.dumps({"type": "Point", "coordinates": [float(lon), float(lat)]})


def _centroid_json(geom: object) -> str:
    bbox = _bbox(geom)
    if bbox is None:
        return str(geom)
    min_lon, min_lat, max_lon, max_lat = bbox
    return _point_json((min_lon + max_lon) / 2, (min_lat + max_lat) / 2)


def _envelope_json(geom: object) -> str:
    bbox = _bbox(geom)
    if bbox is None:
        return str(geom)
    return _polygon_json(*bbox)


def _covers(geom: object, point: object) -> int:
    bbox = _bbox(geom)
    point_coordinates = _point_coordinates(point)
    if bbox is None or point_coordinates is None:
        return 0
    min_lon, min_lat, max_lon, max_lat = bbox
    lon, lat = point_coordinates
    return 1 if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat else 0


def _distance_m(geom: object, point: object) -> float:
    left = _point_coordinates(geom)
    right = _point_coordinates(point)
    if left is None or right is None:
        return 1_000_000.0
    return sqrt(((left[0] - right[0]) * 111_000) ** 2 + ((left[1] - right[1]) * 111_000) ** 2)


def _point_coordinates(value: object) -> tuple[float, float] | None:
    geometry = _load_geometry(value)
    if not geometry:
        return None
    if geometry.get("type") == "Point":
        coordinates = geometry.get("coordinates") or []
        return float(coordinates[0]), float(coordinates[1])
    bbox = _bbox(value)
    if bbox is None:
        return None
    min_lon, min_lat, max_lon, max_lat = bbox
    return (min_lon + max_lon) / 2, (min_lat + max_lat) / 2


def _bbox(value: object) -> tuple[float, float, float, float] | None:
    geometry = _load_geometry(value)
    if not geometry:
        return None
    points = list(_iter_points(geometry.get("coordinates")))
    if not points:
        return None
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    return min(lons), min(lats), max(lons), max(lats)


def _iter_points(coordinates: object) -> Generator[tuple[float, float], None, None]:
    if isinstance(coordinates, list) and len(coordinates) >= 2 and all(isinstance(value, (int, float)) for value in coordinates[:2]):
        yield float(coordinates[0]), float(coordinates[1])
        return
    if isinstance(coordinates, list):
        for item in coordinates:
            yield from _iter_points(item)


def _load_geometry(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
