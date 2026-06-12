from __future__ import annotations

from pathlib import Path


MIGRATION_SQL = Path("alembic/versions/0001_gis_milestone_1.py").read_text(encoding="utf-8")


def test_gis_migration_enables_required_extensions() -> None:
    for extension in ("postgis", "pg_trgm", "unaccent", "btree_gist", "pgcrypto"):
        assert f"CREATE EXTENSION IF NOT EXISTS {extension}" in MIGRATION_SQL


def test_gis_migration_enforces_source_statuses_and_official_display_policy() -> None:
    assert "CREATE TYPE provenance_level_enum" in MIGRATION_SQL
    assert "CREATE TYPE reuse_status_enum" in MIGRATION_SQL
    assert "CREATE TYPE display_status_enum" in MIGRATION_SQL
    assert "CREATE TYPE geometry_status_enum" in MIGRATION_SQL
    assert "chk_source_display_official_policy" in MIGRATION_SQL
    assert "lower(provider_key) NOT IN ('osm', 'overture', 'google')" in MIGRATION_SQL


def test_gis_migration_creates_provenance_and_canonical_tables() -> None:
    assert "CREATE TABLE IF NOT EXISTS feature_provenance" in MIGRATION_SQL
    assert "provenance_id uuid NOT NULL REFERENCES feature_provenance(id)" in MIGRATION_SQL
    assert "USING gist(geom)" in MIGRATION_SQL
    for table_name in (
        "parcels",
        "plans",
        "official_municipal_boundaries",
        "address_points",
        "neighborhoods",
        "poi_points",
        "buildings",
        "context_pois",
    ):
        assert f'"{table_name}"' in MIGRATION_SQL


def test_gis_context_geometry_migration_supports_map_context_layers() -> None:
    migration_sql = Path("alembic/versions/0002_context_geometries.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS context_geometries" in migration_sql
    assert "context_layer text NOT NULL" in migration_sql
    assert "geom geometry(Geometry, 4326) NOT NULL" in migration_sql
    assert "USING gist(geom_2039)" in migration_sql


def test_gis_migration_creates_coverage_model() -> None:
    assert "CREATE TABLE IF NOT EXISTS layer_coverage" in MIGRATION_SQL
    assert "'not_found'" in MIGRATION_SQL
    assert "'not_enabled'" in MIGRATION_SQL
    assert "'not_ingested'" in MIGRATION_SQL
