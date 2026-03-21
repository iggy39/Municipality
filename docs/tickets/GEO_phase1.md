# Geo PoC Tickets - Phase 1 (Map Visualization in `/ui/ask`)

Goal: ship map visualization for geo-tagged ask results using the current FastAPI + SQLite architecture.

Scope lock:
- Map UI is only in `GET /ui/ask` (`src/municipality/api.py`).
- Keep existing `POST /ask` request contract unchanged.
- Use manual/semi-manual geo tagging for demo data.
- No separate GIS server, no PostGIS, no overlays, no map-first search.

## Exit Gate

- [ ] `POST /ask` always returns a stable top-level `geo` payload.
- [ ] `/ui/ask` renders point/polygon/multipolygon features when geometry exists.
- [ ] Result-to-map highlight and geo-type filtering work for the current response.
- [ ] Manual geo import is validated and idempotent.
- [ ] Phase 1 tests pass with no ask regressions.

## Ticket Board

| ID | Title | Priority | Estimate | Depends On |
|---|---|---|---|---|
| GEO-P1-01 | Chunk geo schema + ORM | High | 1.5d | - |
| GEO-P1-02 | Manual geo dataset importer | High | 1.5d | GEO-P1-01 |
| GEO-P1-03 | Ask geo service payload | High | 1.0d | GEO-P1-01 |
| GEO-P1-04 | Extend `/ask` response contract with `geo` | High | 0.75d | GEO-P1-03 |
| GEO-P1-05 | Add OpenLayers map panel to `/ui/ask` | High | 2.0d | GEO-P1-04 |
| GEO-P1-06 | Result-map sync + geo type filters | High | 1.5d | GEO-P1-05 |
| GEO-P1-07 | Phase 1 integration and contract tests | High | 1.25d | GEO-P1-06 |

---

## Tickets

### GEO-P1-01 - Chunk geo schema + ORM
Checklist:
- [ ] Create migration `migrations/006_m6_geo_map.sql`.
- [ ] Add table `chunk_geo_feature` with columns:
  - `id`, `chunk_id`, `geo_type`, `display_label`, `geometry_geojson`,
  - `centroid_lat`, `centroid_lon`, `geo_confidence`, `geo_source`,
  - `locality_code`, `street_code`, `gush`, `helka`, `plan_id`,
  - `metadata_json`, `created_at`, `updated_at`.
- [ ] Add FK from `chunk_id` to `text_chunk.chunk_id`.
- [ ] Add unique constraint `(chunk_id, geo_type, display_label)`.
- [ ] Add indexes for `chunk_id`, `geo_type`, `plan_id`, `(gush, helka)`.
- [ ] Add ORM model in `src/municipality/models.py`.
- [ ] Update migration test in `tests/unit/test_migrations.py`.

Done when:
- [ ] Migration applies and reapplies cleanly via `apply_all`.
- [ ] ORM model matches SQL schema.
- [ ] Migration test asserts `chunk_geo_feature` exists.

### GEO-P1-02 - Manual geo dataset importer
Checklist:
- [ ] Add sample dataset file `scripts/data/geo/manual_demo_tags.json`.
- [ ] Add importer `scripts/import_manual_geo_tags.py`.
- [ ] Support input records keyed by `chunk_id`.
- [ ] Optional convenience: support `decision_id` and resolve to chunks.
- [ ] Validate geometry type is one of `Point`, `Polygon`, `MultiPolygon`.
- [ ] Compute centroid if geometry exists and centroid missing.
- [ ] Upsert idempotently by `(chunk_id, geo_type, display_label)`.
- [ ] Produce clear failure messages for invalid records.

Done when:
- [ ] Importing the same file twice creates no duplicates.
- [ ] Invalid geometry fails with actionable error output.

### GEO-P1-03 - Ask geo service payload
Checklist:
- [ ] Add `src/municipality/geo_service.py`.
- [ ] Implement API-independent method to build ask geo payload from chunk ids.
- [ ] Include payload fields:
  - `has_geo`, `available_types`, `default_center`, `default_zoom`, `features`.
- [ ] For each feature include:
  - `feature_id`, `chunk_id`, `geo_type`, `display_label`, `geometry`,
  - `center`, `geo_confidence`, `geo_source`, `plan_id`, `gush`, `helka`,
  - `locality_code`, `street_code`.
- [ ] Return stable empty payload when no geometry exists.

Done when:
- [ ] Service can be called from ask flow without altering retrieval logic.
- [ ] Empty and non-empty payload shapes are deterministic.

### GEO-P1-04 - Extend `/ask` response contract with `geo`
Checklist:
- [ ] Update `_run_ask` in `src/municipality/api.py` to attach top-level `geo`.
- [ ] Keep existing request/response fields intact.
- [ ] Ensure `geo` exists for `answer` and `refusal` statuses.
- [ ] Maintain backward compatibility for non-geo clients.

Done when:
- [ ] `POST /ask` always includes `geo`.
- [ ] No existing ask integration test regresses.

### GEO-P1-05 - Add OpenLayers map panel to `/ui/ask`
Checklist:
- [ ] Update inline HTML in `src/municipality/api.py` route `GET /ui/ask`.
- [ ] Add map panel elements with IDs:
  - `ask-playground-map-panel`,
  - `ask-playground-map`,
  - `ask-playground-geo-filters`,
  - `ask-playground-geo-empty`.
- [ ] Load OpenLayers from CDN (no npm toolchain).
- [ ] Render point/polygon/multipolygon geometries from ask `geo.features`.
- [ ] Hide map panel when `geo.has_geo` is false.
- [ ] Keep existing tested page IDs and debug panels unchanged.

Done when:
- [ ] `/ui/ask` still passes current UI expectations.
- [ ] Map appears only for geo-enabled ask responses.

### GEO-P1-06 - Result-map sync + geo type filters
Checklist:
- [ ] Add client-side filter controls for `address_point`, `parcel`, `plan`, `area`.
- [ ] Tag rendered answer sections/citations with `data-chunk-id`.
- [ ] Clicking a result highlights and zooms to related map feature(s).
- [ ] Clicking a map feature focuses corresponding result block if present.
- [ ] Apply filters client-side without additional API requests.

Done when:
- [ ] Text/map linking works bidirectionally on one ask response.
- [ ] Filtering updates visible features correctly.

### GEO-P1-07 - Phase 1 integration and contract tests
Checklist:
- [ ] Extend `tests/integration/test_m4_ask_api.py` for `geo` contract.
- [ ] Add seeded geo case asserting `geo.has_geo == True`.
- [ ] Add no-geo case asserting stable empty payload.
- [ ] Add UI assertions for new map element IDs in `/ui/ask` page body.
- [ ] Optionally add unit tests for `geo_service` payload shape.

Done when:
- [ ] Ask API + UI tests cover geo success and geo-empty behavior.
- [ ] Existing ask tests remain green.

---

## Execution Order (Strict)

1. GEO-P1-01
2. GEO-P1-02
3. GEO-P1-03
4. GEO-P1-04
5. GEO-P1-05
6. GEO-P1-06
7. GEO-P1-07

## Verification Commands

- `pytest tests/unit/test_migrations.py`
- `pytest tests/integration/test_m4_ask_api.py`

## Out of Scope (Phase 1)

- Hebrew normalization from official datasets (Phase 2).
- WMS/planning overlays.
- `/ui/decision/{decision_id}` map integration.
- Geocoder autocomplete and map-first geo search.
