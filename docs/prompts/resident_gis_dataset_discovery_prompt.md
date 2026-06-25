# Resident GIS Dataset Discovery Agent Prompt

Use this prompt with an AI/web-research agent to discover datasets on one specific website that can improve resident GIS/RAG questions beyond the sources already known to this project.

```text
You are researching data sources for the Municipality resident GIS/RAG MVP.

Workspace context:
- Project path: /Users/igor/Desktop/projects/Municipality
- Goal: improve resident-facing GIS questions with real, source-backed datasets.
- Do not fabricate datasets, URLs, schemas, permissions, or coverage.
- Do not assume fixed municipality-specific document formats or terminology.

Target website to search:
- <WEBSITE_URL>

Target municipality or region, if relevant:
- <MUNICIPALITY_OR_REGION>

Current known/covered sources. Do not report these as new unless the target website has a better, more official, more detailed, or more current version:
- XPLAN planning polygons from Israel Planning Administration.
- MAPI parcels, or GovMap public parcel fallback when MAPI is blocked.
- Ministry of Interior official municipal boundaries.
- Ministry of Transport GTFS/stops.
- Ministry of Education school coordinates.
- OSM buildings/roads/POIs as context-only, never official.
- Existing municipal open-data layers already discovered in this repo, especially Tel Aviv layers currently marked license-under-review.

Resident GIS/RAG use cases to prioritize:
- What is happening around a parcel, address, building, neighborhood, or plan?
- What is the status of a planning case or plan number?
- What new residential/commercial/public projects are planned nearby?
- What objections, deadlines, public participation windows, or hearings apply?
- What roads, bike paths, transit routes, stations, schools, parks, public buildings, public shelters, parking, accessibility features, noise/environment risks, or construction disruptions are nearby?
- Which municipal decisions/protocols/documents mention a GIS feature?
- What changed recently in a place, and what is official vs context-only?

Search task:
1. Search only the target website and its official linked data portals/APIs/download pages.
2. Identify datasets, APIs, GIS services, ArcGIS/GeoServer/CKAN endpoints, CSV/GeoJSON/Shapefile downloads, PDF indexes, or metadata catalogs that could improve these resident GIS questions.
3. For every candidate, capture the exact source URL and, when available, API endpoint/layer ID/download URL.
4. Check whether the website exposes metadata: owner, update date, license/reuse terms, coordinate system, fields, geometry type, coverage, and row count.
5. Classify each source honestly:
   - official_municipal
   - official_national
   - municipal_open_data
   - public_records
   - context_only
   - license_under_review
   - unavailable_or_blocked
6. Do not mark anything as official unless the website clearly identifies an official authority/source.
7. If license or reuse terms are missing/unclear, mark `license_under_review` and explain the exact uncertainty.
8. Prefer generic reusable sources over one-off pages tailored to a single document.
9. Avoid duplicate sources already known unless there is a concrete improvement: richer fields, better update frequency, official status, legal clarity, or better geometry.

For each candidate dataset, return:
- dataset_name
- source_url
- endpoint_or_download_url
- source_owner
- source_status classification
- license/reuse status and evidence URL
- geometry_type: point, line, polygon, raster, tabular, PDF/index, unknown
- likely layer_key: plans, parcels, addresses, neighborhoods, buildings, roads, planned_roads, bike_paths, transport_stops, schools, public_buildings, shelters, parking, parks, environment, construction, protocols, permits, other
- expected resident question unlocked
- useful fields observed
- update frequency or latest update date
- coverage area
- access method: API, ArcGIS REST, GeoServer WFS/WMS, CKAN, CSV, GeoJSON, Shapefile, PDF, HTML table, other
- import complexity: low, medium, high
- risks/blockers
- recommended action: import_now, inspect_manually, legal_review_first, skip_duplicate, blocked

Output format:
1. Executive summary: 5-10 bullets.
2. Candidate dataset table, sorted by resident value and legal/source confidence.
3. Duplicates or near-duplicates of existing sources.
4. Blocked/unusable sources and exact reason.
5. Recommended next imports, with commands/config suggestions only if clear from the repo patterns.
6. Evidence appendix with exact URLs checked.

Quality bar:
- Every factual claim must include a URL.
- Do not invent field names. If fields are not visible, say `fields_not_visible`.
- Do not scrape aggressively or bypass access controls.
- If the site has robots, rate limits, or terms, respect them and report constraints.
- Keep the final answer concise but complete enough for an engineer to decide what to import next.
```
