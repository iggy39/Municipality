"""GIS milestone 1 schema.

Revision ID: 0001_gis_milestone_1
Revises:
Create Date: 2026-06-09
"""

from __future__ import annotations

from alembic import op

revision = "0001_gis_milestone_1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute(
        """
        DO $$ BEGIN
          CREATE TYPE provenance_level_enum AS ENUM (
            'official_national',
            'official_municipal',
            'municipal',
            'context',
            'community',
            'commercial',
            'user_selected',
            'unknown'
          );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          CREATE TYPE reuse_status_enum AS ENUM (
            'verified',
            'legal_approved',
            'municipal_license_under_review',
            'restricted',
            'unknown',
            'not_reusable'
          );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          CREATE TYPE geometry_status_enum AS ENUM (
            'official_geometry',
            'derived_geometry',
            'context_geometry',
            'schematic_only',
            'none',
            'unknown'
          );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          CREATE TYPE display_status_enum AS ENUM (
            'official',
            'official_with_caveat',
            'municipal_open',
            'municipal_license_under_review',
            'context_only',
            'temporary_input_only',
            'unavailable'
          );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          CREATE TYPE validation_status_enum AS ENUM (
            'valid',
            'repaired',
            'warning',
            'invalid',
            'not_checked'
          );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          CREATE TYPE coverage_status_enum AS ENUM (
            'national_available',
            'municipal_open_available',
            'municipal_license_under_review',
            'context_available',
            'not_found',
            'not_enabled',
            'not_ingested'
          );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS source_registry (
          source_id text PRIMARY KEY,
          provider_key text NOT NULL,
          source_kind text NOT NULL,
          name_he text NOT NULL,
          name_en text,
          owner_name text,
          owner_url text,
          source_url text,
          municipality_code text,
          layer_keys text[] NOT NULL DEFAULT ARRAY[]::text[],
          provenance_level provenance_level_enum NOT NULL,
          reuse_status reuse_status_enum NOT NULL,
          geometry_status geometry_status_enum NOT NULL DEFAULT 'unknown',
          display_status display_status_enum NOT NULL,
          source_is_official boolean NOT NULL DEFAULT false,
          reuse_is_verified boolean NOT NULL DEFAULT false,
          display_as_official boolean NOT NULL DEFAULT false,
          legal_review_approved boolean NOT NULL DEFAULT false,
          enabled boolean NOT NULL DEFAULT true,
          license_name text,
          license_url text,
          attribution text,
          notes text,
          metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT chk_source_display_official_policy CHECK (
            display_as_official = false OR (
              provenance_level IN ('official_national', 'official_municipal')
              AND (reuse_status IN ('verified', 'legal_approved') OR legal_review_approved = true)
              AND lower(source_kind) NOT IN ('osm', 'overture', 'google', 'user_selected', 'community')
              AND lower(provider_key) NOT IN ('osm', 'overture', 'google')
            )
          ),
          CONSTRAINT chk_source_official_flag_policy CHECK (
            source_is_official = false OR provenance_level IN ('official_national', 'official_municipal')
          ),
          CONSTRAINT chk_source_verified_flag_policy CHECK (
            reuse_is_verified = false OR reuse_status IN ('verified', 'legal_approved')
          )
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_source_registry_provider_key ON source_registry(provider_key)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_source_registry_enabled ON source_registry(enabled)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_source_registry_municipality_code ON source_registry(municipality_code)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_source_registry_layer_keys ON source_registry USING gin(layer_keys)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_provenance (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          canonical_table text NOT NULL,
          canonical_feature_id text,
          source_id text NOT NULL REFERENCES source_registry(source_id),
          source_key text NOT NULL,
          source_record_id text,
          batch_id text,
          fetched_at timestamptz NOT NULL DEFAULT now(),
          raw_storage_uri text,
          geometry_hash text,
          metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_feature_provenance_source_id ON feature_provenance(source_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_feature_provenance_source_key ON feature_provenance(source_id, source_key)")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_feature_provenance_feature
        ON feature_provenance(canonical_table, canonical_feature_id)
        WHERE canonical_feature_id IS NOT NULL;
        """
    )

    _create_polygon_table(
        "parcels",
        extra_columns="""
          source_object_id text,
          gush text,
          helka text,
          parcel_label text,
          normalized_geometry_hash text NOT NULL,
          geom_2039 geometry(MultiPolygon, 2039),
        """,
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_parcels_source_object ON parcels(source_id, source_object_id) WHERE source_object_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_parcels_gush_helka_geom ON parcels(source_id, gush, helka, normalized_geometry_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_parcels_gush_helka ON parcels(gush, helka)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_parcels_geom_2039 ON parcels USING gist(geom_2039)")

    _create_polygon_table(
        "plans",
        extra_columns="""
          plan_number text NOT NULL,
          plan_name text,
          normalized_geometry_hash text NOT NULL,
          geom_2039 geometry(MultiPolygon, 2039),
        """,
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_plans_source_number_geom ON plans(source_id, plan_number, normalized_geometry_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_plans_plan_number ON plans(plan_number)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_plans_geom_2039 ON plans USING gist(geom_2039)")

    _create_polygon_table(
        "official_municipal_boundaries",
        extra_columns="""
          municipality_code text NOT NULL,
          municipality_name_he text NOT NULL,
          municipality_name_en text,
          normalized_geometry_hash text NOT NULL,
          geom_2039 geometry(MultiPolygon, 2039),
        """,
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_boundaries_source_municipality ON official_municipal_boundaries(source_id, municipality_code)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_boundaries_municipality_code ON official_municipal_boundaries(municipality_code)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_boundaries_geom_2039 ON official_municipal_boundaries USING gist(geom_2039)")

    _create_point_table(
        "address_points",
        extra_columns="""
          source_object_id text,
          municipality_code text,
          street_name_he text,
          house_number text,
          full_address_he text,
          geom_2039 geometry(Point, 2039),
        """,
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_address_points_source_object ON address_points(source_id, source_object_id) WHERE source_object_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_address_points_municipality_code ON address_points(municipality_code)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_address_points_geom_2039 ON address_points USING gist(geom_2039)")

    _create_polygon_table(
        "neighborhoods",
        extra_columns="""
          municipality_code text,
          name_he text NOT NULL,
          name_en text,
          normalized_geometry_hash text NOT NULL,
          geom_2039 geometry(MultiPolygon, 2039),
        """,
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_neighborhoods_source_name_geom ON neighborhoods(source_id, municipality_code, name_he, normalized_geometry_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_neighborhoods_municipality_code ON neighborhoods(municipality_code)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_neighborhoods_geom_2039 ON neighborhoods USING gist(geom_2039)")

    _create_point_table(
        "poi_points",
        extra_columns="""
          source_object_id text,
          poi_category text NOT NULL,
          name_he text,
          name_en text,
          official_identifier text,
          municipality_code text,
          geom_2039 geometry(Point, 2039),
        """,
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_poi_points_source_object ON poi_points(source_id, source_object_id) WHERE source_object_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_poi_points_official_identifier ON poi_points(source_id, official_identifier) WHERE official_identifier IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_poi_points_category ON poi_points(poi_category)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_poi_points_geom_2039 ON poi_points USING gist(geom_2039)")

    _create_polygon_table(
        "buildings",
        extra_columns="""
          source_object_id text,
          municipality_code text,
          normalized_geometry_hash text NOT NULL,
          geom_2039 geometry(MultiPolygon, 2039),
        """,
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_buildings_source_object ON buildings(source_id, source_object_id) WHERE source_object_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_buildings_source_geom ON buildings(source_id, normalized_geometry_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_buildings_geom_2039 ON buildings USING gist(geom_2039)")

    _create_point_table(
        "context_pois",
        extra_columns="""
          source_object_id text,
          poi_category text NOT NULL,
          name text,
          municipality_code text,
          geom_2039 geometry(Point, 2039),
        """,
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_context_pois_source_object ON context_pois(source_id, source_object_id) WHERE source_object_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_context_pois_category ON context_pois(poi_category)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_context_pois_geom_2039 ON context_pois USING gist(geom_2039)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS layer_coverage (
          municipality_code text NOT NULL,
          municipality_name_he text,
          layer_key text NOT NULL,
          status coverage_status_enum NOT NULL,
          source_id text REFERENCES source_registry(source_id),
          feature_count integer NOT NULL DEFAULT 0,
          last_successful_ingest_at timestamptz,
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (municipality_code, layer_key)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_layer_coverage_source_id ON layer_coverage(source_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_layer_coverage_status ON layer_coverage(status)")


def downgrade() -> None:
    for table_name in (
        "layer_coverage",
        "context_pois",
        "buildings",
        "poi_points",
        "neighborhoods",
        "address_points",
        "official_municipal_boundaries",
        "plans",
        "parcels",
        "feature_provenance",
        "source_registry",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")

    for enum_name in (
        "coverage_status_enum",
        "validation_status_enum",
        "display_status_enum",
        "geometry_status_enum",
        "reuse_status_enum",
        "provenance_level_enum",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name} CASCADE")


def _common_feature_columns() -> str:
    return """
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      source_id text NOT NULL REFERENCES source_registry(source_id),
      provenance_id uuid NOT NULL REFERENCES feature_provenance(id),
      fetched_at timestamptz NOT NULL DEFAULT now(),
      validation_status validation_status_enum NOT NULL DEFAULT 'valid',
      validation_warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
      metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    """


def _create_polygon_table(table_name: str, *, extra_columns: str) -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
          {extra_columns}
          geom geometry(MultiPolygon, 4326) NOT NULL,
          {_common_feature_columns()}
        );
        """
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_source_id ON {table_name}(source_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_provenance_id ON {table_name}(provenance_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_geom ON {table_name} USING gist(geom)")


def _create_point_table(table_name: str, *, extra_columns: str) -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
          {extra_columns}
          geom geometry(Point, 4326) NOT NULL,
          {_common_feature_columns()}
        );
        """
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_source_id ON {table_name}(source_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_provenance_id ON {table_name}(provenance_id)")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_geom ON {table_name} USING gist(geom)")
