# GovMap Resident Layer Catalog Research Agent Prompt

Use this prompt with an AI/web-research agent to inspect the GovMap layers catalog and identify additional official GovMap layers that could improve resident-facing GIS/RAG answers beyond the layer groups already configured in this project.

```text
You are researching GovMap catalog layers for the Municipality resident GIS/RAG MVP.

Workspace context:
- Project path: /Users/igor/Desktop/projects/Municipality
- Current resident GIS registry: /Users/igor/Desktop/projects/Municipality/config/gis/resident_gis_registry.yaml
- GovMap dashboard payload code: /Users/igor/Desktop/projects/Municipality/src/municipality/govmap_client.py
- Goal: identify additional GovMap catalog layers that are beneficial for residential GIS/RAG and are not already represented in current resident layer groups or dashboard base layers.

Hard rules:
- Do not fabricate layers, aliases, fields, URLs, coverage, official status, or examples.
- Do not print, request, or expose GovMap tokens, API keys, cookies, or credentials.
- Do not rely on fixed municipality-specific protocol wording or document structure.
- Prefer generic, reusable national or broadly applicable GovMap layers over municipality-specific layers.
- Do not recommend a municipality-specific layer as a generic resident filter unless you clearly mark it as municipality_specific and name its municipality.
- Do not recommend stale or missing aliases. If an alias is not present in the current catalog, mark it unavailable.
- Do not recommend semantic keyword hacks. Every recommendation must be based on catalog metadata and, when possible, sample feature/coverage evidence.

Primary source to inspect:
- GovMap layers catalog: https://www.govmap.gov.il/api/layers-catalog/catalog

Current project coverage to compare against:
1. Read /Users/igor/Desktop/projects/Municipality/config/gis/resident_gis_registry.yaml.
2. Extract all current `layer_groups[*].govmap_aliases`.
3. Read /Users/igor/Desktop/projects/Municipality/src/municipality/govmap_client.py.
4. Extract existing `GOVMAP_DASHBOARD_LAYERS` aliases and default visible aliases.
5. Treat those aliases as already covered. Do not list them as new unless the catalog shows a clearly better alternative or replacement.

Known recent decisions to preserve:
- `public_institutions_survey` is the verified generic layer for public institutions/buildings.
- `layer_210692` and `layer_210697` are municipality-specific public-building layers and must not be recommended as generic public-building filters.
- `playgrounds_youth_space` currently has no verified generic GovMap map alias. Search for a generic official candidate, but do not force one.
- `res_parks_meforat` was not found in the current catalog and should be treated as stale unless you prove otherwise.
- Search datatypes such as `address`, `street`, and `settlement` are not map layers.

Resident GIS/RAG value areas to prioritize:
- Public buildings and services.
- Parks, playgrounds, youth spaces, sports, leisure, beaches, and open public spaces.
- Accessibility and mobility barriers.
- Parking, roadworks, bike lanes, public transport, traffic safety.
- Welfare, health, emergency, shelters, security, public safety.
- Environmental hazards: air, noise, radiation, waste, drainage, flooding, water/sewer.
- Planning, permits, parcels, zoning, land use, conservation, construction.
- Education, demographics, neighborhood statistics, community facilities.
- Any layer that helps answer: what is near my address, parcel, building, neighborhood, or plan, and what municipal/national decisions may affect me?

Research workflow:
1. Fetch and parse the GovMap catalog.
2. Build a normalized list of catalog entries with alias, Hebrew/English caption if available, category tree/path, geometry hints if available, fields if available, and metadata fields exposed by the catalog.
3. Compare catalog aliases against current registry/dashboard aliases and remove already-covered aliases.
4. Search broadly by category and metadata, not only by obvious Hebrew keywords. Inspect relevant catalog branches manually if needed.
5. For each promising alias, determine whether it is generic/national, municipality-specific, stale/unavailable, duplicate, or too vague.
6. When feasible without credentials leakage or aggressive scraping, probe a small number of representative locations to see whether the layer returns features outside one municipality. Use evidence like counts by location, not token values.
7. Prefer layers that improve RAG answer quality with clear resident questions and official/source confidence.

For every candidate layer, return:
- alias
- display_name_he or catalog caption
- catalog_category_path
- source_owner if visible
- scope: generic_national, broad_regional, municipality_specific, unclear, unavailable
- municipality_name if municipality_specific
- geometry_type if visible or inferred: point, line, polygon, raster, unknown
- fields_observed, or `fields_not_visible`
- already_covered: yes/no, with matching current alias if yes
- resident_value_score: 1-5
- source_confidence_score: 1-5
- likely registry group: existing layer_key or suggested new generic layer_key
- resident questions unlocked
- evidence URLs and catalog snippets checked
- sample coverage evidence, if tested: location name, approximate center only, feature count, no secrets
- risks/blockers
- recommendation: add_now, inspect_manually, municipality_specific_only, duplicate_skip, unavailable_skip

Output format:
1. Executive summary: 5-10 bullets focused on highest-value findings.
2. New generic layer candidates table, sorted by resident value and confidence.
3. Municipality-specific candidates table, only if useful for future city-specific profiles.
4. Duplicates or near-duplicates of current registry/dashboard layers.
5. Unavailable, stale, or misleading aliases discovered.
6. Proposed changes to /Users/igor/Desktop/projects/Municipality/config/gis/resident_gis_registry.yaml, as a concise patch-style summary, but do not edit files unless explicitly asked.
7. Evidence appendix with exact URLs and catalog paths checked.

Quality bar:
- Every factual claim must cite a catalog entry, URL, or observed response.
- If you cannot verify a layer is generic, mark it unclear or municipality_specific; do not promote it to a generic resident filter.
- If you cannot see fields or geometry, say so explicitly.
- Keep the final answer concise enough for an engineer to decide which aliases to add next.
```
