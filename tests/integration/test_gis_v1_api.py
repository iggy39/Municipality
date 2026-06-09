from __future__ import annotations

from collections.abc import Generator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
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


def _client_with_seeded_sqlite() -> TestClient:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
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
                INSERT INTO source_registry VALUES
                ('xplan_blue_lines', 'xplan', 'national_official', 'תכניות', 'Plans', NULL, NULL, NULL, NULL,
                 '{plans}', 'official_national', 'verified', 'official_geometry', 'official', 1, 1, 1, 0, 1,
                 NULL, NULL, NULL, NULL, '{}', NULL, NULL),
                ('tel_aviv_open_data_discovered', 'tel_aviv', 'municipal_open_data', 'תל אביב', 'Tel Aviv', NULL, NULL, NULL, '5000',
                 '{address_points}', 'official_municipal', 'municipal_license_under_review', 'unknown', 'unavailable', 1, 0, 0, 0, 0,
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
                ('8310', 'ירוחם', 'neighborhoods', 'not_found', NULL, 0, NULL, NULL)
                """
            )
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
