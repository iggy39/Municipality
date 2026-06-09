# GovMap-Independent Municipal GIS/RAG Platform: Design Amendment V2

This document records amendments to the GovMap-independent Israeli municipal GIS/RAG platform design. It should be applied to the original design before implementation tickets are executed.

## 1. Provenance Linkage and Feature Identity

Every canonical feature row must be directly linkable to provenance.

Canonical geometry-bearing tables must include:

```sql
provenance_id uuid REFERENCES feature_provenance(canonical_feature_id)
```

For high-volume batch imports, a canonical row may reference one provenance row representing a source batch, but APIs must still be able to return source metadata for every feature through `source_id`.

Ingestion must use stable source keys when available:

- plans: `source_id + plan_number + normalized_geometry_hash`
- parcels: `source_id + source_object_id` or `source_id + gush + helka + normalized_geometry_hash`
- GTFS stops: `source_id + stop_id`
- schools: `source_id + official_identifier`
- municipal features: `source_id + raw_feature_id` where available

All canonical feature tables must have either a unique source key or a documented deduplication rule.

## 2. Required Database Extensions

Database initialization must enable:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

`pgcrypto` is required for `gen_random_uuid()`.

## 3. Source Status Constraints

The database must enforce valid values for:

- `provenance_level`
- `reuse_status`
- `geometry_status`
- `display_status`

Use PostgreSQL enums or CHECK constraints.

`display_as_official=true` is allowed only when:

- source is official national or official municipal
- reuse status is `verified` or explicitly approved by product/legal review
- source is not OSM, Overture, Google, user-selected, or community-contributed

Separate these concepts:

- `source_is_official`: owner is an official body
- `reuse_is_verified`: legal reuse is verified
- `display_as_official`: product may display it as official/open

This prevents confusing official ownership with verified reuse rights.

## 4. Coverage Model

Coverage must be materialized or computed from canonical data and source registry.

Create a `layer_coverage` table or materialized view:

```sql
CREATE TABLE layer_coverage (
  municipality_code text NOT NULL,
  municipality_name_he text,
  layer_key text NOT NULL,
  status text NOT NULL,
  source_id text REFERENCES source_registry(source_id),
  feature_count integer NOT NULL DEFAULT 0,
  last_successful_ingest_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (municipality_code, layer_key)
);
```

Coverage statuses:

```text
national_available
municipal_open_available
municipal_license_under_review
context_available
not_found
not_enabled
not_ingested
```

The coverage API must not infer availability from source registration alone. It must distinguish source discovered, source enabled, source ingested, and municipality has matching rows.

## 5. Context Table Scope

If the API or UI references these context layers, define their tables before implementation:

- `context_roads`
- `context_address_points`
- `context_neighborhoods`

Alternatively, remove them from the POC scope until the context-data milestone.

## 6. Validation Status Storage

Canonical geometry-bearing tables should preserve validation outcomes.

Add these fields:

```sql
validation_status text NOT NULL DEFAULT 'valid',
validation_warnings jsonb NOT NULL DEFAULT '[]'::jsonb
```

Features crossing the generous Israel bbox should be stored with a warning, not silently deleted.

Invalid geometries that are repaired with `ST_MakeValid` should record that repair in `validation_warnings`.

## 7. Geometry Response Policy

Large geometries must not be returned by default.

Endpoints returning parcels, plans, or polygon layers should support:

```text
include_geometry=false|true
geometry_detail=none|centroid|bbox|simplified|full
```

Default behavior:

- search results return centroid or bbox only
- detail endpoints may return simplified geometry
- full geometry is returned only when explicitly requested
- map rendering should prefer vector tiles where available

## 8. Milestone 1 Additions

Milestone 1 must also deliver:

- backend Dockerfile
- frontend Dockerfile or documented Vite local fallback
- `.env.example` plus compose-safe defaults
- Alembic path that matches Makefile commands
- DB enums or CHECK constraints
- coverage table or initial materialized view

## 9. Milestone 2 Dependency Change

Milestone 2 must include Ministry of Interior municipal boundary ingestion because point-report and coverage depend on municipality identification.

## 10. Acceptance Test Additions

Every public API feature response must include a `source` object.

Tests must assert:

- no returned feature lacks provenance
- no OSM/Overture/Google source can be displayed as official
- coverage distinguishes `not_found`, `not_ingested`, and `not_enabled`
- point-report can identify municipality after boundary ingestion
- large geometry endpoints obey `include_geometry` defaults

## 11. Design Risks Resolved by This Amendment

This amendment addresses these implementation risks:

- canonical rows were not directly linkable to provenance rows
- official ownership and legal reuse were conflated
- coverage had no backing data model
- point-report depended on municipal boundaries before boundaries were scheduled
- Docker and Alembic setup was underspecified for Milestone 1
- ingestion idempotency lacked stable keys and constraints
- large API geometries could violate performance targets
- context layers were referenced without all corresponding tables
