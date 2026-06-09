# GovMap-Independent Municipal GIS/RAG Platform: Implementation Tickets

These tickets implement the amended design in `docs/govmap_independent_municipal_gis_design_v2.md`.

## Ticket 1: Normalize Repo Skeleton

Title: Create runnable repo skeleton with Docker, Alembic, and Makefile alignment

Priority: High

Scope: Milestone 1

Acceptance criteria:

- `make up`, `make migrate`, and `make test` work from a clean checkout.
- Backend and frontend Dockerfiles exist or compose uses images that do not require missing build files.
- Alembic config path matches Makefile commands.
- `.env.example` exists and compose does not fail because `.env` is absent.

## Ticket 2: Add Database Extensions and Source Enums

Title: Add PostGIS init SQL and source status constraints

Priority: High

Scope: Milestone 1

Acceptance criteria:

- DB init enables `postgis`, `pg_trgm`, `unaccent`, `btree_gist`, and `pgcrypto`.
- `provenance_level`, `reuse_status`, and `display_status` are enforced by enum or CHECK constraints.
- Tests reject invalid source status values.

## Ticket 3: Implement Source Registry Migration

Title: Create source registry schema and seed loader

Priority: High

Scope: Milestone 1

Acceptance criteria:

- `source_registry` table matches design amendments.
- YAML seed loader upserts sources idempotently.
- Duplicate `source_id` in YAML fails validation.
- `display_as_official=true` is rejected for OSM, Overture, and Google sources.

## Ticket 4: Implement Feature Provenance Linkage

Title: Link canonical feature rows to provenance records

Priority: High

Scope: Milestone 1

Acceptance criteria:

- `feature_provenance` table exists.
- Canonical feature tables include `source_id` and `provenance_id` or a documented batch provenance link.
- API source fragments can be built for every returned feature.
- Tests prove no feature response is returned without provenance.

## Ticket 5: Create Canonical Tables

Title: Create canonical GIS tables with indexes and validation fields

Priority: High

Scope: Milestone 1

Acceptance criteria:

- Migrations create parcels, plans, boundaries, address points, neighborhoods, POIs, buildings, and context POIs.
- Every geometry table has GiST indexes.
- Every geometry table has `source_id`, `fetched_at`, `validation_status`, and `validation_warnings`.
- Unique source keys or deduplication indexes exist for idempotent ingestion.

## Ticket 6: Implement Health and Sources APIs

Title: Add health and source registry endpoints

Priority: High

Scope: Milestone 1

Acceptance criteria:

- `GET /v1/health` returns API, DB, and PostGIS status.
- `GET /v1/sources` supports documented filters.
- `GET /v1/sources/{source_id}` returns source metadata.
- API tests pass with seeded source registry.

## Ticket 7: Implement Coverage Model

Title: Add layer coverage table and API

Priority: High

Scope: Milestone 1

Acceptance criteria:

- `layer_coverage` table or materialized view exists.
- `GET /v1/coverage` and `/v1/coverage/{municipality_code}` return structured statuses.
- Statuses distinguish `not_found`, `not_ingested`, and `not_enabled`.
- Coverage rows exist for Haifa, Beer Sheva, Jerusalem, Ashdod, Tel Aviv, and one small authority.

## Ticket 8: Implement Geometry Validation Service

Title: Validate and flag imported geometries

Priority: High

Scope: Milestone 2

Acceptance criteria:

- Validator checks null, empty, type mismatch, validity, bbox, polygon area, invalid point coordinates, and `0,0`.
- Fixable geometries use `ST_MakeValid` and record warnings.
- Bbox-crossing features are flagged, not deleted.
- Unit tests cover valid, invalid, empty, out-of-bbox, and repairable geometries.

## Ticket 9: Ingest Municipal Boundaries

Title: Implement national municipal boundary importer

Priority: High

Scope: Milestone 2

Acceptance criteria:

- Importer loads official municipal boundaries into `official_municipal_boundaries`.
- Municipality code and Hebrew name are normalized.
- Point-report can identify municipality using `ST_Covers`.
- Coverage API uses boundaries as municipality backbone.

## Ticket 10: Ingest XPLAN Plans

Title: Implement ArcGIS REST importer for XPLAN blue lines

Priority: High

Scope: Milestone 2

Acceptance criteria:

- Importer reads ArcGIS metadata and pages through features.
- Raw pages and manifest are stored under immutable raw storage.
- Plans are upserted idempotently by source key and geometry hash.
- `/v1/plans/603-1373075` returns a structured response with provenance.

## Ticket 11: Ingest MAPI Parcels

Title: Implement MAPI parcel ZIP importer

Priority: High

Scope: Milestone 2

Acceptance criteria:

- Importer downloads ZIP, stores manifest, discovers `.shp`, and inspects schema.
- Field mapping handles known gush/helka variants without assuming one schema.
- Parcels are loaded with `geom` and `geom_2039`.
- Table has more than zero rows after successful ingest.

## Ticket 12: Ingest MOT GTFS Stops

Title: Implement GTFS static stops importer

Priority: Medium

Scope: Milestone 2

Acceptance criteria:

- Importer downloads GTFS ZIP and reads `stops.txt`.
- Valid stops are inserted as `poi_points` with `poi_category=transport_stop`.
- Invalid coordinates and missing IDs are rejected with warnings.
- Rerunning importer does not duplicate stops.

## Ticket 13: Ingest Ministry of Education Coordinates

Title: Implement school coordinate importer

Priority: Medium

Scope: Milestone 2

Acceptance criteria:

- Importer discovers CKAN resource.
- WGS84 coordinates are preferred; ITM coordinates are transformed when needed.
- Schools are inserted as `poi_points` with `poi_category=school`.
- Official institution identifier is preserved.

## Ticket 14: Implement Geometry Response Policy

Title: Avoid returning large geometries by default

Priority: Medium

Scope: Milestone 3

Acceptance criteria:

- Polygon endpoints support `include_geometry` and `geometry_detail`.
- Search responses default to centroid or bbox.
- Full geometry is only returned when explicitly requested.
- Tests verify defaults.

## Ticket 15: Implement Plan and Parcel APIs

Title: Add plan and parcel lookup endpoints

Priority: High

Scope: Milestone 3

Acceptance criteria:

- `GET /v1/plans/{plan_number}` returns matches with source fragments.
- `GET /v1/parcels?gush=&helka=` returns structured matches.
- No-match responses are structured and not null blobs.
- Endpoints meet basic indexed-query performance on ingested data.

## Ticket 16: Implement Point Report API

Title: Add point-report endpoint with explicit missing-data states

Priority: High

Scope: Milestone 3

Acceptance criteria:

- Endpoint returns municipality, parcel, plans, neighborhood status, and nearby POIs.
- Unavailable layers return message and fallback list.
- Context data is never silently substituted for official data.
- Every feature includes source/provenance.

## Ticket 17: Implement Hebrew Normalization

Title: Add Hebrew normalization and search helpers

Priority: Medium

Scope: Milestone 3

Acceptance criteria:

- `normalize_hebrew_text`, `normalize_gershayim`, `normalize_cadastral_id`, and `normalize_address` exist.
- Tests cover `רובע ט״ו`, `רובע טו`, and `רובע ט''ו`.
- Tests verify abbreviations like `שד׳` are handled only by explicit synonyms.
- Official identifiers are not over-normalized.

## Ticket 18: Implement Search API

Title: Add multi-source search endpoint

Priority: High

Scope: Milestone 3

Acceptance criteria:

- Search detects plan numbers and parcel patterns first.
- Official and municipal results are ordered before context results.
- Search results include precision, source, and `can_persist`.
- Google provider is disabled by default.

## Ticket 19: Implement Nearby API

Title: Add nearby POI endpoint

Priority: Medium

Scope: Milestone 3

Acceptance criteria:

- Endpoint supports radius and categories.
- Official/municipal results are returned before context results.
- Distance is computed in meters using geography index or `geom_2039`.
- Tests cover category filtering and provenance.

## Ticket 20: Implement OSM Context Importer

Title: Import OSM context buildings and POIs

Priority: Medium

Scope: Milestone 4

Acceptance criteria:

- Importer downloads Geofabrik extract into immutable raw storage.
- Context buildings and POIs are inserted into context tables.
- All context features have `display_status=context_only`.
- No context feature is labeled official.

## Ticket 21: Define or Defer Missing Context Tables

Title: Resolve context roads, addresses, and neighborhoods scope

Priority: Medium

Scope: Milestone 4

Acceptance criteria:

- Either create `context_roads`, `context_address_points`, and `context_neighborhoods`, or remove them from POC API/UI references.
- Tests match the chosen scope.

## Ticket 22: Implement Generic Municipal Importers

Title: Add municipal GeoJSON, SHP, CSV, and ArcGIS importers

Priority: High

Scope: Milestone 5

Acceptance criteria:

- Importers use source config mappings rather than city-specific API code.
- Municipal data is inserted into canonical municipal tables.
- Unclear-license sources are stored with `municipal_license_under_review`.
- Reruns are idempotent.

## Ticket 23: Add City Source Configs

Title: Add POC city source registry/config entries

Priority: High

Scope: Milestone 5

Acceptance criteria:

- Haifa, Beer Sheva, Jerusalem, Ashdod, Tel Aviv, and one small authority are configured.
- Ashdod quarter candidate is not displayed as verified official.
- Tel Aviv sources are discoverable but disabled until legal review.
- Coverage matrix reflects city-specific source status.

## Ticket 24: Build Map UI Shell

Title: Create MapLibre Hebrew UI shell

Priority: Medium

Scope: Milestone 6

Acceptance criteria:

- Frontend loads on desktop and mobile.
- Map renders with base style and layer toggles.
- RTL/Hebrew labels render correctly.
- API client handles source/provenance fragments.

## Ticket 25: Build Source Badges and Legend

Title: Display provenance and layer status in UI

Priority: High

Scope: Milestone 6

Acceptance criteria:

- Every selected feature displays `SourceBadge`.
- Legend includes all visual statuses from the design.
- Official, caveat, municipal, context, temporary, and unavailable states are visually distinct.
- Missing-source messages are visible to users.

## Ticket 26: Build Point Report Panel

Title: Add map click point-report workflow

Priority: High

Scope: Milestone 6

Acceptance criteria:

- Clicking map calls `/v1/point-report`.
- Panel shows municipality, parcel, plans, neighborhood/quarter, and nearby POIs.
- Unavailable layers are shown explicitly.
- All returned features show source badges.

## Ticket 27: Build Coverage Matrix UI

Title: Add coverage matrix page

Priority: Medium

Scope: Milestone 6

Acceptance criteria:

- Page displays municipalities and layer status columns.
- Statuses match backend coverage API.
- Source details are accessible from coverage cells.
- City rows include the six POC municipalities/authorities.

## Ticket 28: Optional Google Temporary Search

Title: Add temporary Google search provider behind feature flag

Priority: Low

Scope: Milestone 7

Acceptance criteria:

- Disabled by default with `ENABLE_GOOGLE_TEMPORARY_SEARCH=false`.
- No ingestion adapter uses Google.
- Results return `display_status=temporary_input_only` and `can_persist=false`.
- Tests prove no canonical tables are written during temporary search.

## Ticket 29: Add End-to-End Demo Verification

Title: Add concise demo verification scripts

Priority: Medium

Scope: Cross-milestone

Acceptance criteria:

- Script verifies health, seeded sources, coverage, plan lookup, point report, GTFS stops, and schools.
- Output prints concise input/output evidence.
- Demo script includes missing-data honesty scenario.

## Ticket 30: Performance and Index Review

Title: Validate spatial query performance

Priority: Medium

Scope: After Milestone 3

Acceptance criteria:

- Run `EXPLAIN ANALYZE` for plan lookup, parcel lookup, point-report, and nearby.
- Add geography expression indexes or use `geom_2039` for nearby distance queries.
- Endpoints meet POC performance targets on representative ingested data.
