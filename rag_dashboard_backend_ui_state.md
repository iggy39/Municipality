# Municipal RAG Dashboard Backend And UI State

This document describes the current Hebrew RTL municipal RAG dashboard implementation, its backend mock data contract, UI state transitions, and verification coverage.

## Routes

Resident dashboard:

```text
GET /ask
GET /ui/ask
```

Legacy debug playground:

```text
GET /debug
GET /ui/debug
```

Dashboard data API:

```text
GET /api/ui/rag-dashboard/mock
POST /api/ui/rag-dashboard/query
GET /api/ui/rag-dashboard/evidence/{evidence_id}
POST /api/ui/rag-dashboard/interaction
```

Existing answer API remains unchanged:

```text
POST /ask
```

## Backend Data Provider

Backend-owned mock dashboard data lives in:

```text
src/municipality/rag_dashboard_mock.py
```

The provider returns contract-shaped data intended to match the design document and future API boundaries. The current data is static mock data, not persisted DB data and not real municipal/GIS data.

Main provider functions:

```python
get_mock_rag_dashboard_payload()
get_mock_rag_dashboard_evidence(evidence_id)
apply_mock_rag_dashboard_interaction(state, interaction)
```

Strict dashboard contracts live in:

```text
src/municipality/rag_dashboard_contracts.py
```

The real-query adapter lives in:

```text
src/municipality/rag_dashboard_adapter.py
```

Both mock and query payloads are validated with `RagDashboardPayload` before returning to the UI.

## Dashboard Payload Shape

`GET /api/ui/rag-dashboard/mock` returns one complete dashboard view model:

```json
{
  "ui_copy": {},
  "state": {},
  "start_discovery_panel": {},
  "main_civic_workspace": {},
  "end_detail_drawer": {},
  "contracts": {},
  "evidence": []
}
```

Important sections:

```text
ui_copy
```

Resident-visible labels and copy for the header, answer drawer, map, timeline, discovery panel, filters modal, and popular searches.

```text
state
```

Current dashboard state, including selected category, topic, map entity, timeline event, drawer mode, filter state, and question.

```text
start_discovery_panel
```

Categories, hot topics, and focused topic tree data with counts and selected flags.

```text
main_civic_workspace
```

Schematic map entities, legend, timeline events, selected timeline event, and real GIS availability flag.

```text
end_detail_drawer
```

Answer drawer mode, brief, decisions, related topics, limitations, and resident-facing source links.

```text
contracts
```

Design-document-shaped contract objects for topic nodes, decisions, evidence, map entities, and related topics.

## Evidence Contract

`GET /api/ui/rag-dashboard/evidence/{evidence_id}` returns artifact-native evidence:

```json
{
  "id": "evidence_rova_tet_vav_protocol_1",
  "source_type": "protocol",
  "source_title": "פרוטוקול מועצה 23.06.2024",
  "retrieval_artifact_id": "artifact_protocol_decision_unit_478",
  "artifact_kind": "decision_unit",
  "retrieval_set_id": "retrieval_set_rova_tet_vav_2024",
  "header_path": ["מועצת העיר", "תכנון ובנייה", "תכנית רובע טו"],
  "page_span": { "start": 7, "end": 7 },
  "text": "...",
  "confidence_label": "גבוהה"
}
```

Resident UI source links use the visible label `מקור`, not `ראיה`.

## Interaction Endpoint

`POST /api/ui/rag-dashboard/interaction` applies deterministic mock state transitions.

Request:

```json
{
  "state": {},
  "interaction": {
    "type": "select_category",
    "id": "transport"
  }
}
```

Response:

```text
Full dashboard payload with updated state and selected flags.
```

Supported interaction types:

```text
select_category
select_hot_topic
select_topic_tree_node
select_timeline_event
select_map_entity
select_related_topic
open_evidence
apply_filters
reset_filters
select_popular_search
```

Unknown interaction types or unknown IDs return:

```text
404 rag_dashboard_interaction_not_found
```

## Query Endpoint

`POST /api/ui/rag-dashboard/query` runs the existing `/ask` answer path and adapts the answer payload into the dashboard view model.

Request:

```json
{
  "question": "מה הוחלט לגבי תכנית רובע טו?",
  "muni": "ashdod",
  "top_k": 8,
  "semantic_mode": "off",
  "filters": {},
  "debug_mode": false
}
```

Response:

```text
Full `RagDashboardPayload` with answer drawer, evidence links, and schematic map provenance.
```

If the answer backend fails, the endpoint returns a valid dashboard payload with:

```text
state.generation_status = "error"
state.error.code = backend error code
end_detail_drawer.mode = "errorState"
evidence = []
```

The query adapter currently reuses the dashboard shell and discovery scaffolding, then replaces the answer drawer, decisions, and evidence with data adapted from the real `/ask` payload.

The adapter performs first-pass normalization from answer/citation text into governed public codelists:

```text
decision_kind: APPROVAL, REJECTION, DEFERRAL, REQUEST_FOR_INFO, PUBLICATION, BUDGET_ALLOCATION, DISCUSSION, UNKNOWN
outcome_status: APPROVED, APPROVED_WITH_CONDITIONS, REJECTED, PENDING, NOTED, NO_FORMAL_OUTCOME, UNKNOWN
legal_effect: BINDING, PROCEDURAL, INFORMATIONAL, UNKNOWN
```

Uncertain mappings remain broad or `UNKNOWN`; the adapter does not invent unsupported codelist values.

Dashboard filters are passed to `/api/ui/rag-dashboard/query` and mapped conservatively:

```text
time_range -> AskRequest.year when the value is a four-digit year
category -> AskRequest.semantic_label when no explicit semantic_label was supplied
source_types -> dashboard state source_type_filter
confidence -> dashboard state confidence_filter
area -> dashboard state only; it does not use schematic map geometry as real GIS
```

## UI State Model

The browser UI fetches `/api/ui/rag-dashboard/mock` on load and renders from the returned payload.

The dashboard root exposes state for verification:

```html
<div
  id="rag-dashboard"
  data-dashboard-endpoint="/api/ui/rag-dashboard/mock"
  data-source-status="loaded"
  data-source-url="/api/ui/rag-dashboard/mock"
  data-selected-category-id="planning"
  data-selected-topic-node-id="topic_rova_tet_vav_plan"
  data-selected-timeline-event-id="event_2024_06_23"
  data-selected-map-entity-id="entity_rova_tet_vav_selected_area"
  data-detail-drawer-mode="answer"
>
```

Debug globals:

```js
window.__municipalDashboardData
window.__municipalDashboardState
window.__lastDashboardInteraction
window.applyDashboardInteraction
```

## UI Interaction Behavior

Category click:

```text
Updates selected_category_id and selected category row.
Does not rerun the answer.
```

Hot topic click:

```text
Updates selected_topic_node_id and selected hot topic row.
Does not rerun the answer.
```

Topic tree click:

```text
Updates selected_topic_node_id and focused topic tree selected row.
```

Timeline card click:

```text
Updates selected_timeline_event_id and selected timeline card.
```

Map entity click:

```text
Updates selected_map_entity_id for the schematic entity.
Does not claim real GIS geometry.
```

Related topic chip click:

```text
Updates topic state without showing relation labels in the chip UI.
```

Source link click:

```text
Fetches /api/ui/rag-dashboard/evidence/{evidence_id} and opens an evidence preview dialog.
```

Filters:

```text
Modal opens only after clicking מסננים.
Apply/reset update filter state without adding persistent scope chips.
```

Popular search:

```text
Updates the search question and cached answer state.
```

## Evidence Preview

Evidence preview is a compact dialog in the resident UI. It shows:

```text
source title
artifact kind
page span
confidence
header path
snippet text
source document link when available
```

The dialog preserves the dashboard state and returns focus to the source link when closed.

Real artifact evidence source URLs include a page fragment when a page is known, for example:

```text
/document-versions/{document_version_id}/source.pdf#page=7
```

## Map State

The current map is schematic:

```json
{
  "spatial_representation": "schematic",
  "real_geometry": null,
  "geometry_provenance": null,
  "real_gis_available": false
}
```

Map-level provenance is explicit in both backend data and UI:

```json
{
  "status": "schematic_only",
  "label_he": "מפה סכמטית בלבד",
  "description_he": "אין גיאומטריית GIS מאומתת; המיקומים והצורות מוצגים להמחשה בלבד על בסיס הראיות.",
  "source_evidence_refs": []
}
```

Do not treat the schematic coordinates as real GIS. Real map work requires a sourced geometry provider with provenance, CRS, source URL, accuracy, and confidence.

## Tests

Dashboard tests are in:

```text
tests/integration/test_rag_dashboard_api.py
```

Coverage includes:

```text
mock payload shape
resident-visible data coverage
endpoint-backed /ui/ask wiring
interaction state transitions
unknown interaction handling
HTTP route exposure
strict contract validation
real query adapter behavior
safe query error state
artifact-backed evidence lookup
first-pass codelist normalization
filter-to-query/state mapping
page-anchored evidence source URLs
UI wiring for filter badge, query filters, and evidence source link
```

Focused command:

```bash
./.venv/bin/python -m pytest tests/integration/test_rag_dashboard_api.py
```

Current expected result:

```text
11 passed, 1 skipped
```

The skipped test is an optional Chromium executable test. The local `/opt/homebrew/bin/chromium` shim is broken in this environment, but Playwright CLI verification has been run separately.

## Browser Verification

The interaction smoke check used Playwright to verify:

```text
initial endpoint load reaches data-source-status=loaded
category click updates selected category state
hot topic click updates selected topic state
source link opens evidence preview
```

Latest screenshot artifact:

```text
/var/folders/d9/sq9k5qn94jl1tsbb9sfsf8gc0000gn/T/opencode/municipality-dashboard-stateful.png
```

## Current Limitations

- Data is backend-owned mock data, not live DB-derived municipal data.
- `/api/ui/rag-dashboard/query` adapts real `/ask` answer/evidence data, but discovery panels are still scaffolded from the dashboard shell.
- The UI is endpoint-backed, but still rendered as inline FastAPI HTML rather than a dedicated frontend bundle.
- The schematic map is not a real GIS map.
- Interaction transitions are deterministic mock transitions except source links, which fetch evidence directly.
- Search submit now calls `POST /api/ui/rag-dashboard/query` and renders the dashboard payload.
