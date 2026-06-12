"""Generic context geometry layers.

Revision ID: 0002_context_geometries
Revises: 0001_gis_milestone_1
Create Date: 2026-06-12
"""

from __future__ import annotations

from alembic import op

revision = "0002_context_geometries"
down_revision = "0001_gis_milestone_1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS context_geometries (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          source_id text NOT NULL REFERENCES source_registry(source_id),
          provenance_id uuid NOT NULL REFERENCES feature_provenance(id),
          source_object_id text,
          context_layer text NOT NULL,
          geometry_type text NOT NULL,
          category text,
          name text,
          normalized_geometry_hash text NOT NULL,
          geom geometry(Geometry, 4326) NOT NULL,
          geom_2039 geometry(Geometry, 2039) NOT NULL,
          fetched_at timestamptz NOT NULL DEFAULT now(),
          validation_status validation_status_enum NOT NULL DEFAULT 'valid',
          validation_warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
          metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_context_geometries_source_layer_object ON context_geometries(source_id, context_layer, source_object_id) WHERE source_object_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_context_geometries_source_layer_geom ON context_geometries(source_id, context_layer, normalized_geometry_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_context_geometries_source_id ON context_geometries(source_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_context_geometries_provenance_id ON context_geometries(provenance_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_context_geometries_context_layer ON context_geometries(context_layer)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_context_geometries_geom ON context_geometries USING gist(geom)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_context_geometries_geom_2039 ON context_geometries USING gist(geom_2039)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS context_geometries CASCADE")
