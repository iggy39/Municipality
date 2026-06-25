# Real GIS Data Ingestion Session Prompt

Use this prompt in a fresh session to fill the GIS database with real data.

```text
You are working in /Users/igor/Desktop/projects/Municipality.

Goal: ingest real GIS data needed for the GovMap-independent municipal GIS/RAG MVP. Do not fabricate data. Do not use mock data except for tests. Use only real official/open/context sources and keep source status honest.

Current state:
- Stages 1-5 are implemented.
- GIS schema, source registry, provenance, coverage, canonical tables, validation, APIs, OSM importer, and generic municipal importers exist.
- Main files:
  - /Users/igor/Desktop/projects/Municipality/src/municipality/gis_importers.py
  - /Users/igor/Desktop/projects/Municipality/src/municipality/gis_api.py
  - /Users/igor/Desktop/projects/Municipality/config/gis/source_registry.seed.yaml
  - /Users/igor/Desktop/projects/Municipality/docs/gis_real_data_ingestion.md
  - /Users/igor/Desktop/projects/Municipality/scripts/download_gis_poc_sources.py
  - /Users/igor/Desktop/projects/Municipality/scripts/import_xplan_plans.py
  - /Users/igor/Desktop/projects/Municipality/scripts/import_mapi_parcels.py
  - /Users/igor/Desktop/projects/Municipality/scripts/import_govmap_public_parcel.py
  - /Users/igor/Desktop/projects/Municipality/scripts/import_mot_bus_stops.py
  - /Users/igor/Desktop/projects/Municipality/scripts/import_gtfs_stops.py
  - /Users/igor/Desktop/projects/Municipality/scripts/import_moe_schools.py
  - /Users/igor/Desktop/projects/Municipality/scripts/import_municipal_boundaries.py
  - /Users/igor/Desktop/projects/Municipality/scripts/import_osm_context.py
  - /Users/igor/Desktop/projects/Municipality/scripts/import_municipal_source.py

Required real data to ingest:
- Official municipal boundaries.
- XPLAN plan polygons.
- MAPI parcels, or documented GovMap public fallback for selected parcels if MAPI is blocked.
- Ministry of Transport stops.
- Ministry of Education schools.
- OSM context buildings and POIs from Geofabrik.
- Municipal city data for Haifa, Beer Sheva, Jerusalem, Ashdod, Tel Aviv, and Yeruham where legally usable or marked under review.

Rules:
- Prefer official national or municipal sources.
- Never mark OSM, Google, Overture, community, or temporary sources as official.
- If a municipal source license is unclear, store it as municipal_license_under_review.
- Do not silently substitute context data for official data.
- Every imported feature must have source_id and provenance_id.
- Update coverage honestly: distinguish not_found, not_ingested, not_enabled, municipal_license_under_review, context_available, national_available, and municipal_open_available.
- If a source is blocked or unavailable, document the exact blocker and leave coverage honest.
- Do not hardcode city-specific importer logic if generic config can handle it.

Suggested workflow:
1. Inspect the current repo and confirm stages 1-5.
2. Start services: make up; make migrate; make seed-sources.
3. Download accessible POC sources: make download-gis-poc-sources.
4. Import real XPLAN sample or broader set using scripts/import_xplan_plans.py.
5. Import real MOT bus stops using scripts/import_mot_bus_stops.py.
6. Import real MOE schools using scripts/import_moe_schools.py.
7. Import OSM context using scripts/import_osm_context.py. Start with limits, then decide whether full import is acceptable.
8. Try official MAPI parcel ZIP. If blocked, use scripts/import_govmap_public_parcel.py only as a clearly caveated fallback.
9. Research and obtain an official municipal boundary GeoJSON/source. Import using scripts/import_municipal_boundaries.py.
10. For municipal sources, use scripts/import_municipal_source.py with config files/mappings. Do not invent source URLs.
11. Run verification: /v1/health, /v1/sources, /v1/coverage, /v1/plans/{sample}, /v1/parcels?gush=&helka=, /v1/point-report?lon=&lat=, and /v1/nearby?lon=&lat=.
12. Print concise evidence: input source, row count, sample feature, source_id, provenance_id, coverage status.
13. Update docs only if new real source details, commands, or blockers are discovered.

Deliverable:
- A concise report listing imported sources, row counts, coverage status by municipality/layer, blocked sources, and exact commands used.
- If code/config changes are needed, make minimal changes and run targeted tests.
```
