# Resident GIS/RAG Question Plan Tickets

These tickets implement the revised plan for resident questions that combine municipal document RAG with real GIS objects. Execute tickets in order unless a ticket explicitly says it is parallel-safe.

## Ticket 1: Add GIS Question Intent and Focus Resolution

Status: [x]

Priority: High

Goal: Detect when a resident question needs GIS context and identify the most likely map focus before running or adapting the answer.

Why this matters: The current `/ask` UI can show real GIS layers, but question submission still behaves mostly like text RAG. A resident question such as `מה קורה סביב גוש 7103 חלקה 43?` must first resolve a parcel focus, while `מה הסטטוס של תכנית 101-0057273?` must resolve a plan focus.

Implementation notes:

- Create a generic `GeoIntentResolver` for natural Hebrew questions.
- Create a `GisFocusResolver` for plan numbers, cadastral IDs, place names, neighborhoods, and clicked map points.
- Keep this deterministic at first; do not call an LLM for the resolver.
- Do not hardcode one municipality, one plan, or one source-specific phrase beyond generic aliases.
- Use normalized Hebrew so `רובע ט״ו`, `רובע טו`, and similar variants behave consistently.

Example questions:

- `מה קורה סביב גוש 7103 חלקה 43?` -> `parcel_context`, focus `parcel`, `gush=7103`, `helka=43`.
- `מה הסטטוס של תכנית 101-0057273?` -> `plan_status`, focus `plan`, `plan_number=101-0057273`.
- `אילו פרויקטי מגורים חדשים מתוכננים ליד השכונה שלי?` -> `development_near_place`, focus may be unresolved until address/place is supplied.
- `אילו תחנות תחבורה ובתי ספר ליד המקום?` -> `services_near_place`, focus may be point/place.
- `מתי אפשר להגיש התנגדות לתכנית באזור שלי?` -> `planning_public_participation`, focus may be plan/place.

Done when:

- Resolver returns a typed intent, confidence label, matched terms, and optional focus object.
- Unit tests cover plan, parcel, development, transport/schools, public participation, and unknown questions.
- `/api/ui/rag-dashboard/query` includes the resolver output in `state.intent_resolution` without breaking existing dashboard responses.

## Ticket 2: Build a Map Context API Object

Status: [x]

Priority: High

Goal: Convert a resolved GIS focus into a reusable map context for the answer drawer and map.

Why this matters: The UI currently fetches real GIS separately. The product needs one backend-owned object describing what should be highlighted and why.

Implementation notes:

- Add a backend `MapContextBuilder` that accepts the focus from Ticket 1.
- Use existing data first: MAPI parcels, MOI boundaries, MOT stops, MOE schools, OSM context, Tel Aviv municipal layers under review, and currently ingested XPLAN rows.
- Return layer statuses explicitly: `found`, `not_found`, `not_ingested`, `not_enabled`, `context_only`, `municipal_license_under_review`.
- Include source fragments and provenance for every returned feature.

Example output for `גוש 7103 חלקה 43`:

- selected parcel from `mapi_parcels`.
- municipality from `moin_municipal_boundaries`.
- covering plans from `xplan_blue_lines` if available.
- neighborhood from municipal layer if loaded, marked `municipal_license_under_review`.
- nearby schools from `moe_school_coordinates`.
- nearby transport stops from `mot_gtfs_stops`.
- roads/buildings/parks from `osm_context` as `context_only`.

Done when:

- Backend returns a stable `map_context` object for plan, parcel, and point focus.
- Missing layers are shown as missing; OSM is never substituted as official data.
- Concise verification prints focus, municipality, layer counts, source IDs, and caveats.

## Ticket 3: Fuse GIS Context Into Dashboard Query Responses

Status: [x]

Priority: High

Goal: Make `/api/ui/rag-dashboard/query` return one fused response that updates answer drawer, timeline, map highlights, and source caveats together.

Why this matters: The browser should not independently guess how to join RAG evidence and GIS objects.

Implementation notes:

- Extend the dashboard payload with optional GIS context while keeping the existing resident UI stable.
- Keep the current question visible in the search field.
- Open the end detail drawer in `answer` mode after search.
- Populate map-layer summary counts from backend map context.
- Preserve same-row `מקור` links for document evidence.

Done when:

- Querying `מה קורה סביב גוש 7103 חלקה 43?` updates answer state and selected map focus in one response.
- Querying a non-GIS question still works as before.
- Verification logs `geo_intent`, `focus_type`, `selected_map_entity_id`, layer counts, and evidence count.

## Ticket 4: Fix Municipality Consistency Across RAG and GIS

Status: [x]

Priority: High

Goal: Stop mixing Ashdod RAG answers with Tel Aviv GIS context except in explicitly labeled demo mode.

Why this matters: Current UI submits `muni: "ashdod"` while the real GIS demo centers on Tel Aviv. This is acceptable only for temporary demo data, not resident-facing behavior.

Implementation notes:

- Add a selected municipality state used by both RAG and GIS.
- If GIS focus resolves a municipality, use it to scope RAG where possible.
- Until Tel Aviv protocols are processed, show a clear demo caveat when RAG and GIS municipalities differ.

Done when:

- Search payload does not hardcode Ashdod when the selected GIS municipality is Tel Aviv.
- Mismatch state is explicit and visible in debug/verification output.

## Ticket 5: Ingest Municipality-Scoped XPLAN First

Status: [x]

Priority: High

Goal: Expand XPLAN beyond the current 4 plans, starting with the target municipality before national ingestion.

Why this matters: Planning questions depend on XPLAN. OSM cannot answer official planning status, objection dates, plan boundaries, or residential unit deltas.

Implementation notes:

- Use ArcGIS REST pagination from `https://ags.iplan.gov.il/arcgisiplan/rest/services/PlanningPublic/Xplan/MapServer/1`.
- The public layer currently reports about 36,146 national features.
- Start with municipality-scoped ingestion by spatial intersection with the official municipal boundary or by service fields such as `plan_county_name`, `jurstiction_area_name`, or `plan_area_name`.
- Preserve all raw metadata, including `station_desc`, `internet_short_status`, `pl_url`, dates, land-use string, objectives, and quantity deltas.

Implementation progress:

- Added `--municipality-code` support to the existing XPLAN import script.
- The importer now uses an ingested official municipal boundary as an ArcGIS spatial prefilter and a local intersection filter.
- The importer preserves raw XPLAN properties and exposes compact plan metadata for status, URL, dates, land use, objectives, and quantity deltas.
- `/v1/plans/{plan_number}` now returns `plan_metadata` plus raw `metadata`.
- Live import completed against the local compose PostGIS database using:
  `.venv/bin/python scripts/import_xplan_plans.py https://ags.iplan.gov.il/arcgisiplan/rest/services/PlanningPublic/Xplan/MapServer/1 --municipality-code 5000 --plan-number-field pl_number --plan-name-field pl_name --where "plan_area_name LIKE '%תל אביב%'"`
- Import result: `inserted_or_updated=482 rejected=0`.
- Verification result: `total_xplan_plans=484`, `intersecting_tel_aviv=480`, `coverage_feature_count=480`, `coverage_status=national_available`.
- API evidence: `/v1/plans/507-0073395` returns `station_desc=אישור`, `internet_short_status=התכנית אושרה`, and a Mavat `pl_url` in `plan_metadata`.

Example resident questions unlocked:

- `מה הסטטוס של תכנית 101-0057273?`
- `אילו פרויקטי מגורים חדשים מתוכננים באזור?`
- `מתי אפשר להגיש התנגדות לתכנית באזור שלי?`

Done when:

- Target municipality has non-trivial XPLAN coverage.
- `/v1/plans/{plan_number}` exposes status/date/metadata fields needed for resident answers.
- Coverage row for `plans` reflects ingested feature count.

## Ticket 6: Link Protocol Evidence To GIS Features

Status: [ ] implementation ready; live links blocked until Tel Aviv protocol decisions are processed

Priority: High after Tel Aviv protocols are processed

Goal: Connect municipal protocol artifacts and decisions to plans, neighborhoods, parcels, and places.

Why this matters: A GIS map is useful only when decisions and evidence can explain what happened there.

Implementation notes:

- Match plan mentions by `pl_number` first.
- Match neighborhoods and named places using governed aliases and confidence labels.
- Store artifact-to-feature links with evidence, confidence, and warnings.
- Do not invent real geometry from text mentions.

Implementation progress:

- Added `decision_gis_feature_link` table migration for SQLite app databases.
- Added deterministic plan-number linker from public decisions and source retrieval artifacts to XPLAN `plans`.
- Added `/api/admin/gis/decision-links/rebuild` to rebuild exact plan-number links.
- `/decision/{decision_id}` now returns `decision.linked_gis_features`.
- `/v1/plans/{plan_number}` now returns `related_decisions` when links exist.
- Focused tests verify both directions: decision -> GIS plan and plan -> related decisions/evidence ref.
- Live run is currently blocked by data split: local SQLite document DB has decision/artifact tables but `0` decisions and no `plans`; local PostGIS DB has `484` plans but no decision/artifact tables.

Done when:

- A decision row can list linked GIS features.
- A selected GIS feature can list related decisions and evidence references.
- Low-confidence links are visible as uncertain to residents and detailed to admins.

## Ticket 7: Add Planned Roads and Transport Changes Only From Reliable Sources

Status: [ ]

Priority: Medium

Goal: Support questions about planned roads, not only current roads.

Why this matters: OSM mostly shows existing roads as context. Planned roads require official planning sources or extracted plan attachments.

Implementation notes:

- Use XPLAN metadata and plan attachments where they reliably describe planned roads or transport corridors.
- Keep OSM roads as `context_only` and label them existing/background unless proven otherwise.
- If planned-road geometry is absent, answer with limitations rather than drawing fake lines.

Done when:

- Planned-road answers identify whether geometry is official, extracted with evidence, or unavailable.
- No OSM road is labeled as a planned official road.

## Ticket 8: Rank And De-Duplicate Similar Layers

Status: [ ]

Priority: Medium

Goal: Decide which similar layers to show when national, municipal, and OSM layers overlap.

Why this matters: Tel Aviv municipal layers can overlap with national schools and OSM POIs, but they also add local detail.

Layer rules:

- Prefer national official layers for official claims: MAPI parcels, MOI boundaries, MOT stops, MOE schools, XPLAN plans.
- Use municipal layers as local enrichment while `municipal_license_under_review` remains unresolved.
- Use OSM only as context for roads, buildings, landuse, and POIs.
- Never display municipal-under-review or OSM data as approved official data.

Similar layer examples:

- `municipal_school` overlaps with `moe_school_coordinates`; MOE wins for official school claims.
- `municipal_parks` overlaps with OSM landuse/parks; municipal layer may add managed park names but must show license caveat.
- `municipal_bike_paths` overlaps partly with OSM roads; municipal layer is more bike-specific.
- `community_center`, `culture`, and `parking` overlap partly with OSM POIs; municipal layer can add local completeness.
- `address_points` has no good current national/OSM replacement for precise resident address search.

Done when:

- Map context includes `preferred_source_rank` and caveats for similar layers.
- UI can hide redundant context layers without losing source transparency.

## Ticket 9: Resident Use-Case Verification Matrix

Status: [ ]

Priority: Medium

Goal: Track which resident GIS questions are sufficiently supported by current data and which require more ingestion.

Current support matrix:

- Sufficient now: parcel context, point report, nearby schools, nearby transport, municipality identification.
- Partial now: neighborhood context, place-based activity, city hot spots.
- Missing until XPLAN/protocol links: plan status at scale, new residential projects, objection deadlines, planned roads.

Done when:

- A verification script prints one concise row per use case: question, intent, focus, GIS layer counts, RAG evidence count, and missing dependencies.
- The report is saved under `eval/reports/`.
