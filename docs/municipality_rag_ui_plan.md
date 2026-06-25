# Municipality RAG UI Plan

## Purpose

Design a Hebrew-first, resident-friendly POC UI for exploring municipality protocols, decisions, attachments, topics, mapped entities, timelines, and original evidence.

The POC should feel like a civic intelligence map, not a chatbot. Residents should be able to select a topic, place, project, parcel, plan, or entity and quickly understand what was discussed or decided, with visible evidence and uncertainty.

## Locked Product Decisions

- Primary audience: residents.
- First municipality dataset: Ashdod-like mock data, but the architecture must stay municipality-agnostic.
- Resident-facing UI language: Hebrew only.
- Layout direction: RTL-native.
- Internal/admin/debug labels: English is acceptable.
- Phase 1 data: mock data.
- Phase 1 map: map-like schematic, not real GIS yet.
- Phase 1 ask/search: canned mock answers after a fixed demo-friendly delay.
- Phase 1 generation delay: fixed `1.2s` delay.
- Phase 1 demo warning: always visible as a subtle banner.
- Topic/entity/category selection: no blocking answer generation.
- Cached summaries: shown immediately when available.
- Missing cached summaries: show a placeholder immediately, then show a canned/generated mock summary after `1.2s`.
- Answer generation: explicit user action through ask/search or an ask button, not automatic on every selection.
- Default map with no selected topic: recent high-activity entities.
- Map with selected topic/category: only relevant entities.
- Low-confidence entities in simple mode: only recent/high-activity ones, visible with warnings.
- Low-confidence entities in detailed mode: all available low-confidence entities may be visible.
- Confidence display for residents: labels such as `גבוהה`, `בינונית`, `נמוכה`, not numeric scores.
- Login/admin/debug: behind a small `Admin` entry point and disabled in Phase 1.

## Product Goals

- Let a resident understand what the municipality discussed or decided about a topic, place, project, parcel, plan, street, neighborhood, or entity.
- Make discovery possible without knowing exact protocol wording.
- Keep the map and timeline central, because municipal decisions are often place-based and evolve over time.
- Keep selection interactions instant and non-blocking.
- Show cached evidence-backed summaries whenever possible.
- Make every generated or cached answer evidence-first, with source links and highlighted source spans.
- Make uncertainty visible without overwhelming residents.
- Keep the POC flexible through simple UI regions, generic contracts, and state-driven interactions.

## Non-Goals For Phase 1

- Do not build full GIS integration yet.
- Do not build many resident-facing routes.
- Do not require login for public exploration.
- Do not expose debug traces to residents.
- Do not hide evidence behind summaries.
- Do not hardcode Ashdod-specific rules into components or data contracts.
- Do not assume fixed protocol wording, document structure, municipality schema, attachment structure, or language pattern.

## Recommended Frontend Stack

Use a stack optimized for fast implementation and later migration from mock data to real APIs:

- `Vite + React + TypeScript` for the Phase 1 shell.
- `Zustand` for explorer UI state.
- CSS Modules or Tailwind with RTL support for fast styling.
- `TanStack Query` later for backend/cache integration.
- `MapLibre GL` later when replacing the schematic map with real GIS layers.

## Main Information Architecture

Use one resident-facing route: `Explorer`.

The app can later add routes for admin, shared links, or ingestion tools, but the public POC should feel like one workspace.

RTL layout:

```text
+--------------------------------------------------------------------------------+
| Top Bar                                                                        |
| [עירייה] [חיפוש או שאלה בפרוטוקולים...] [תגיות הקשר/סינון] [Admin]            |
+--------------------------------------------------------------------------------+
| Demo Banner                                                                    |
| נתוני הדגמה בלבד - אינם משקפים בהכרח החלטות אמיתיות                           |
+----------------------+--------------------------------------+------------------+
| Left Drawer          | Map-Like Schematic                    | Right Panel      |
| תשובה / תקציר        | Ashdod-style spatial view             | קטגוריות         |
| החלטות / מקורות      | topic-filtered entities               | נושאים חמים      |
| ראיות / מקור מקורי   | simple/detailed layer mode            | עץ נושאים        |
+----------------------+--------------------------------------+------------------+
| Bottom Timeline                                                               |
| [טווח זמן] [צפיפות אירועים] [אירועים קשורים] [ניקוי סינון]                    |
+--------------------------------------------------------------------------------+
```

## Core UX Principle

Selection should be instant. Generation should be deliberate or backgrounded.

```text
User selects topic/entity/category
-> UI state updates immediately
-> schematic map filters/highlights immediately
-> left drawer opens with cached summary if available
-> if cache is missing, placeholder appears immediately
-> after 1.2s, a canned mock summary appears
-> evidence and decisions remain visible whenever available
-> user can ask a custom question explicitly
```

## Screen Regions

### Top Bar

The top bar is always visible.

Contents:

- Municipality selector or current municipality label, for example `אשדוד`.
- Ask/search input, for example `חיפוש או שאלה בפרוטוקולים...`.
- Scope chips.
- Small `Admin` entry point, disabled in Phase 1.

Behavior:

- Asking a question does not replace the map.
- Ask/search returns a canned mock answer after `1.2s` in Phase 1.
- If time, category, topic, entity, confidence, or source filters are active, the question uses them as scope.
- Active scope must be visible and removable before or after asking.

Example scope chips:

```text
אשדוד | 2022-2024 | תכנון ובנייה | תכנית רובע טו | ודאות בינונית ומעלה
```

### Demo Banner

The demo banner is always visible in Phase 1 because the mock data is real-looking.

Copy:

```text
נתוני הדגמה בלבד - אינם משקפים בהכרח החלטות אמיתיות
```

The banner should be subtle, persistent, and removable only after real evidence-backed data is connected.

### Right Panel: Categories, Hot Topics, Topic Tree

The right panel is the main discovery panel in RTL.

It combines broad categories, concrete hot topics, and a compact topic tree.

Example categories:

- תכנון ובנייה
- מגורים
- תחבורה
- חינוך
- סביבה
- מרחב ציבורי
- תקציב
- רווחה
- רישוי עסקים

Hot topics should be concrete topic-tree nodes, not generic tags.

Real-looking Ashdod-style mock examples:

- `תכנית רובע טו`
- `התחדשות עירונית ברחוב הרצל`
- `שינוי ייעוד מגרש 204`
- `נתיבי אוטובוס בשדרות בני ברית`
- `חניות ליד בתי ספר`
- `מעברי חציה באזור גנים`
- `פרויקט מגורים פארק לכיש`
- `מרינה אשדוד`
- `אזור התעשייה הצפוני`
- `קריית התרבות`

Hot topic ranking should use:

- Recent activity in the selected time range.
- Mention count in protocols, decisions, attachments, and evidence spans.
- Number of linked decisions.
- Number of linked mapped entities.
- Evidence quality.
- Public-interest/user-interaction boost only later, if appropriate.

Every ranked hot topic should have explainable metadata, such as:

```text
23 אזכורים | 7 החלטות | פעילות אחרונה: 2025 | ודאות גבוהה
```

Right panel behavior:

- Categories are always visible.
- Expanding a category shows hot topics from the topic tree.
- Clicking a category sets `selected_category_id`, filters the map, opens category context, and shows cached category summary if available.
- Clicking a hot topic sets `selected_topic_node_id`, derives compatible category, filters the map, and shows cached topic summary or placeholder.
- Clicking a topic-tree node sets `selected_topic_node_id`, derives category, filters the map, and shows topic context.
- Selection never auto-runs a fresh answer request.
- Selected category/topic remains visible after map, timeline, or drawer interactions.
- Timeline changes re-rank hot topics without clearing the selected topic/entity.

### Map-Like Schematic

Phase 1 uses a schematic map that looks geographically meaningful but is not real GIS.

It should include Ashdod-like labels and visual geography:

- Coastline or sea edge.
- Simplified urban shape.
- Neighborhood/area labels such as `רובע טו`, `הסיטי`, `מרינה`, `פארק לכיש`, `אזור התעשייה`, `קריית התרבות`.
- Selectable pins, areas, corridors, and project shapes.
- Category colors.
- Low-confidence warning styles.

Default map behavior:

- No selected topic/category: show recent high-activity entities.
- Topic/category selected: show only related entities.
- Entity selected: show selected entity prominently and related entities subtly.
- Timeline range selected: show entities active in that range.
- Low-confidence simple mode: show only recent/high-activity low-confidence entities with warnings.
- Detailed mode: show all available mock layers and all low-confidence entities.

Supported mock entity types:

- Address or site points.
- Streets or corridors.
- Neighborhoods or named areas.
- Parcels.
- Plans or planning polygons.
- Project entities that combine point, area, plan, and document evidence.

Map visual rules:

- High confidence: solid marker/shape and label `ודאות גבוהה`.
- Medium confidence: lighter marker/shape and label `ודאות בינונית`.
- Low confidence: dashed/faded marker/shape and warning `זיהוי לא ודאי`.
- Selected entity: strong outline or filled highlight.
- Related entity: pale tint.
- Category color: consistent across map, hot topics, and topic tree.

Uncertainty warning copy:

```text
הזיהוי אינו ודאי, מומלץ לבדוק את המקור.
```

Map modes:

```text
תצוגה פשוטה:
- ישויות פעילות/רלוונטיות
- ישויות פעילות במיוחד לאחרונה
- ישויות נבחרות וקשורות
- מעט ישויות לא ודאיות עם אזהרה

תצוגה מפורטת:
- חלקות
- תכניות ופוליגונים
- שכונות ואזורים
- רחובות ומסדרונות
- מבני ציבור
- כל הישויות הלא ודאיות
- שכבות צפיפות או החלטות, אם זמינות
```

Map interactions:

- Click entity: open entity context in the left drawer, update topic/category highlights, and show cached entity summary or placeholder.
- Hover/tap entity: show label, confidence label, active date range, and top linked topic.
- Cluster click: zoom within schematic or open a small list before selecting one entity.
- Lasso/bounding-box selection is not required for Phase 1.

### Bottom Timeline

The timeline is a map and topic filter, not just an answer timeline.

Main behavior:

- User selects a date range.
- Map updates to entities active in that range.
- Hot topics re-rank for that range.
- Left drawer keeps the current selection but marks the content as filtered.
- Clearing the time range returns to all available dates.
- Timeline changes do not clear selected topic/entity.

Visual elements:

- Horizontal date scale by year/month.
- Event density histogram.
- Selected range highlight.
- Major event markers for selected entity/topic.
- Clear time filter action.

Timeline events should support uncertain dates through `date_precision`:

- `exact`
- `month`
- `year`
- `range`
- `unknown`

### Left Drawer: Context, Answer, Evidence

The left drawer is the main detail surface in RTL.

It should support multiple modes but feel like one consistent panel.

Drawer content order:

```text
כותרת הקשר
תגיות היקף/סינון
תקציר שמור או Placeholder
החלטות מרכזיות
ראיות וציטוטים
ישויות קשורות במפה
אזהרות אי-ודאות
פעולת שאלה
```

Drawer modes:

1. Answer Mode
2. Category Mode
3. Topic Mode
4. Topic Tree Mode
5. Map Entity Mode
6. Timeline/Event Mode
7. Evidence Mode
8. Disabled Admin/Login Mode

The drawer should use tabs or section headers inside the drawer, not separate resident-facing routes.

## Detailed Drawer Modes

### Answer Mode

Triggered by explicit ask/search or explicit ask button.

Phase 1 behavior:

- Show loading state for `1.2s`.
- Return canned mock answer.
- Show source links and mock evidence references.
- Preserve map and selected scope.

Example:

```text
[תשובה]
שאלה: מה הוחלט לגבי תכנית רובע טו?
היקף: אשדוד | תכנון ובנייה > תכנית רובע טו | 2021-2025

תשובה קצרה
...

החלטות מרכזיות
- החלטה / תאריך / סטטוס [פתח מקור]

ראיות
[פרוטוקול עמ׳ 3] [נספח עמ׳ 12] [כרטיס החלטה]

מגבלות
...
```

Rules:

- Every claim must have at least one visible evidence reference.
- If evidence is insufficient, show partial/refusal state and explain missing evidence.
- Clicking a citation opens Evidence Mode.
- Related topics/entities should be visible for pivoting.

### Category Mode

Triggered by clicking a category.

Behavior:

- Highlight category in the right panel.
- Filter schematic map to matching entities.
- Show cached category summary or placeholder.
- Show top child topics ranked by current time range.
- Offer explicit ask action, for example `שאל על התחום הזה`.

### Topic Mode

Triggered by clicking a hot topic or topic-tree node.

Behavior:

- Highlight compatible category and selected topic.
- Filter schematic map to related entities.
- Center topic tree context around selected topic.
- Show cached topic summary or placeholder.
- Show key decisions and evidence immediately when available.
- Offer explicit ask action, for example `שאל על הנושא הזה`.

### Topic Tree Mode

The topic tree is both visualization and input control.

Phase 1 requirements:

- Render compact tree in the right panel and/or left drawer context block.
- Show selected node, parent, siblings, and children.
- Show count and recency badges.
- Every visible node is clickable.
- Keep category/topic highlights synchronized.
- Preserve map and time filters while navigating tree nodes.

### Map Entity Mode

Triggered by clicking a schematic map entity.

Behavior:

- Highlight selected entity on the map.
- Highlight related entities lightly.
- Show related topics and decisions.
- Show cached entity summary or placeholder.
- Show confidence label and uncertainty reason.
- Show progression events on the timeline.
- Offer explicit ask action, for example `מה הוחלט כאן?`.

Example uncertainty reasons:

- `מיקום לא ודאי`
- `קישור לנושא לא ודאי`
- `חילוץ טקסט לא ודאי`
- `תאריך לא ודאי`

### Timeline/Event Mode

Triggered by clicking a timeline marker.

Behavior:

- Focus related entity/topic.
- Show event summary, linked decision, and evidence.
- Do not change global time range unless the user drags the range.

### Evidence Mode

Evidence can be shown inside the drawer or as a second-level drawer/modal.

Because bbox data is available, Phase 1 should model the evidence UX even with mock data.

Evidence view should include:

- Source title.
- Source type: protocol, attachment, decision, map source.
- Page number or range.
- Highlighted extracted Hebrew text span.
- Original PDF/page preview with bbox highlight when available.
- Link to open original document.
- Confidence label.
- Extraction warnings.
- Claims or decisions that use this evidence.

Fallback behavior:

- If bbox exists, show page image/PDF preview with bbox and extracted text highlight.
- If bbox is missing, show page-level link and text-offset highlight.
- If source cannot display inline, show metadata and open-original action.

### Disabled Admin/Login Mode

Phase 1 should include a small `Admin` entry point, but admin/login/debug/export features are disabled.

Resident-facing behavior:

- Keep public flow simple.
- Do not show debug internals by default.
- If opened, disabled admin area can show `לא פעיל בהדגמה`.

Later logged-in users may get:

- Saved views/questions.
- Export answer/evidence bundles.
- Export timeline/map data.
- Debug traces and confidence details.
- Low-confidence review tools.
- Ingestion/admin controls when authorized.

## Core Interaction Flows

### Flow A: Resident Opens Explorer

1. App loads Hebrew RTL explorer.
2. Demo warning is visible.
3. Schematic map shows recent high-activity entities.
4. Right panel shows categories and hot topics.
5. Left drawer shows introductory context or recent activity summary.

### Flow B: Resident Clicks A Category

1. User clicks `תכנון ובנייה`.
2. Right panel highlights the category.
3. Hot topics under the category expand and re-rank by selected time range.
4. Map filters to planning-related entities.
5. Left drawer opens Category Mode.
6. Cached category summary appears or placeholder runs for `1.2s`.

### Flow C: Resident Clicks A Hot Topic

1. User clicks `תכנית רובע טו`.
2. Right panel highlights the topic and compatible category.
3. Map filters to related entities.
4. Left drawer opens Topic Mode.
5. Cached topic summary appears or placeholder runs for `1.2s`.
6. Evidence and decisions appear when available.
7. User may click `שאל על הנושא הזה`.

### Flow D: Resident Clicks A Topic Tree Node

1. User clicks a node in the topic tree.
2. Selected node becomes active.
3. Category/hot-topic highlights update.
4. Map filters to related entities.
5. Drawer updates to Topic Mode.
6. No fresh answer is generated unless the user explicitly asks.

### Flow E: Resident Uses Timeline

1. User drags time range to `2022-2024`.
2. Map updates to entities active in that range.
3. Hot topics re-rank for that range.
4. Drawer keeps current selection and displays filtered scope.
5. Entity/topic event markers appear when relevant.
6. User can click an event to open Timeline/Event Mode.

### Flow F: Resident Clicks Map Entity

1. User clicks a parcel, project, street, address, area, or plan shape.
2. Map highlights selected entity.
3. Drawer opens Map Entity Mode.
4. Related categories/topics highlight.
5. Cached entity summary appears or placeholder runs for `1.2s`.
6. User may click `מה הוחלט כאן?`.

### Flow G: Resident Asks A Question

1. User types a Hebrew question in the top bar.
2. Active scope chips remain visible.
3. User submits the question.
4. Drawer opens Answer Mode with `1.2s` loading state.
5. Canned mock answer appears with evidence links.
6. Map highlights related entities.
7. Clicking evidence opens Evidence Mode.

### Flow H: Resident Opens Evidence

1. User clicks a citation, source, decision, or evidence link.
2. Evidence Mode opens.
3. Original page preview and bbox highlight appear when available.
4. Extracted Hebrew text appears alongside the source.
5. User can return to answer/topic/entity without losing state.

## State Model

The UI should be state-driven. Most interactions update shared explorer state.

Suggested state fields:

```text
municipality_id
selected_time_range
selected_category_id
selected_topic_node_id
selected_map_entity_id
selected_timeline_event_id
current_question
current_answer_id
active_drawer_mode
evidence_selection
confidence_filter
source_type_filter
map_mode
generation_status
login_state
```

Derived state:

```text
visible_hot_topics
visible_map_entities
visible_map_layers
topic_tree_focus
related_decisions
related_evidence
entity_progression_events
answer_scope
scope_chips
active_uncertainty_warnings
```

## Selection And Generation Rules

- Category click sets `selected_category_id` and clears or preserves `selected_topic_node_id` depending on whether the topic belongs to that category.
- Hot-topic click sets `selected_topic_node_id` and compatible `selected_category_id`.
- Topic-tree node click sets `selected_topic_node_id` and derives compatible category.
- Map entity click sets `selected_map_entity_id` and derives related topic/category highlights.
- Timeline range change updates visible map entities and hot topic ranking but does not erase selected topic/entity.
- Ask/search uses current selected topic/entity/time range as scope unless removed by the user.
- Selection does not auto-run a fresh answer.
- Missing cached summary starts a background/demo generation after selection.
- If the user switches selection before generation finishes, ignore the previous result.

## Cache Strategy

Use a fastest-perceived `stale-while-revalidate` pattern.

Phase 1 mock behavior:

- Cached summary exists: show immediately.
- Cached summary missing: show placeholder for `1.2s`, then show canned summary.
- Ask/search: show loading for `1.2s`, then show canned answer.

Later real behavior:

- Generate summaries offline after ingestion when possible.
- Store summaries per category, topic, entity, and major time range.
- Show cached summaries immediately, even if slightly stale.
- Show `עודכן לאחרונה` freshness label.
- Refresh affected summaries in background after document/entity changes.
- Generate lazily if no cached summary exists.
- Ignore stale generation results if the user moved to another item.

Placeholder copy:

```text
מכינים תקציר מבוסס מקורות...
בינתיים מוצגות החלטות ומקורות זמינים.
```

## Data Contracts

The UI should not depend on one backend implementation. Contracts should be generic and municipality-agnostic.

### Municipality

```json
{
  "id": "ashdod",
  "display_name": "אשדוד",
  "locale": "he-IL",
  "direction": "rtl"
}
```

### Category

```json
{
  "id": "planning",
  "label_he": "תכנון ובנייה",
  "color": "#3B82F6",
  "topic_ids": ["topic_123"]
}
```

### Topic Node

```json
{
  "id": "topic_123",
  "label": "תכנית רובע טו",
  "category_ids": ["planning", "housing"],
  "primary_category_id": "planning",
  "parent_id": "topic_parent",
  "child_ids": [],
  "mention_count": 23,
  "decision_count": 7,
  "recent_activity_at": "2025-02-12",
  "confidence_label": "גבוהה"
}
```

### Map Entity

```json
{
  "id": "entity_456",
  "label": "פרויקט מגורים פארק לכיש",
  "entity_type": "project",
  "geometry_kind": "schematic_polygon",
  "geometry": { "type": "Polygon", "coordinates": [] },
  "confidence_label": "בינונית",
  "uncertainty_reasons": ["מיקום לא ודאי"],
  "active_from": "2021-01-01",
  "active_to": "2025-01-01",
  "activity_score": 87,
  "is_recent_high_activity": true,
  "topic_ids": ["topic_123"],
  "decision_ids": ["decision_1"],
  "evidence_refs": ["evidence_1"]
}
```

### Timeline Event

```json
{
  "id": "event_789",
  "date": "2023-06-14",
  "date_precision": "exact",
  "event_type": "approval",
  "summary": "דיון באישור הפרויקט",
  "entity_ids": ["entity_456"],
  "topic_ids": ["topic_123"],
  "evidence_refs": ["evidence_1"]
}
```

### Decision

```json
{
  "id": "decision_1",
  "title": "דיון בתכנית רובע טו",
  "date": "2023-06-14",
  "status": "discussed",
  "summary": "סיכום החלטה קצר לתצוגה ציבורית",
  "topic_ids": ["topic_123"],
  "entity_ids": ["entity_456"],
  "evidence_refs": ["evidence_1"]
}
```

### Evidence Reference

```json
{
  "id": "evidence_1",
  "source_type": "protocol",
  "source_title": "פרוטוקול מועצה 14.06.2023",
  "source_url": "...",
  "page": 7,
  "start_offset": 1023,
  "end_offset": 1188,
  "bbox": [120, 220, 510, 270],
  "text": "...",
  "confidence_label": "גבוהה",
  "extraction_warnings": []
}
```

### Cached Summary

```json
{
  "id": "summary_1",
  "target_type": "topic",
  "target_id": "topic_123",
  "scope": {
    "municipality_id": "ashdod",
    "time_range": ["2021-01-01", "2025-12-31"]
  },
  "status": "ready",
  "summary": "תקציר שמור מבוסס מקורות...",
  "key_decision_ids": ["decision_1"],
  "evidence_refs": ["evidence_1"],
  "updated_at": "2025-02-13T10:00:00Z"
}
```

### Answer

```json
{
  "id": "answer_1",
  "question": "מה הוחלט לגבי תכנית רובע טו?",
  "scope": {
    "municipality_id": "ashdod",
    "time_range": ["2021-01-01", "2025-12-31"],
    "topic_ids": ["topic_123"],
    "entity_ids": ["entity_456"],
    "source_types": ["protocol", "attachment"]
  },
  "status": "answered",
  "short_answer": "תשובה קצרה מבוססת ראיות...",
  "claims": [
    {
      "id": "claim_1",
      "text": "טענה מבוססת מקור...",
      "confidence_label": "גבוהה",
      "evidence_refs": ["evidence_1"]
    }
  ],
  "limitations": ["חלק מהנספחים אינם זמינים בהדגמה"]
}
```

## Responsive Design

Phase 1 should be responsive but not mobile-first.

Desktop:

- Right panel persistent and collapsible.
- Map-like schematic is the largest region.
- Left drawer persistent/collapsible.
- Timeline persistent at bottom.
- Evidence can open inside drawer or as modal.

Tablet:

- Right panel can become overlay.
- Left drawer can cover 40-60% width.
- Timeline remains sticky.
- Map remains primary.

Phone:

- Must not break.
- Map remains first screen.
- Drawer can become full-screen sheet.
- Topic panel can become a bottom sheet or overlay.
- Timeline can be compact.
- Full mobile optimization is not required for Phase 1.

## Accessibility And Hebrew Requirements

- Use global `dir="rtl"` for resident UI.
- Hebrew labels should be natural and concise.
- Use logical CSS properties where practical.
- Ensure keyboard navigation for topic lists, map entities, timeline handles, drawer tabs, and evidence links.
- Do not rely on color alone for category/confidence; use labels and shapes.
- Use color-blind-safe category colors.
- Provide clear focus states.
- Provide screen-reader labels for map entities and evidence links.

## POC Build Phases

### Phase 1: Mock Explorer Shell

- Build one `Explorer` route.
- Implement Hebrew RTL shell.
- Add persistent demo-data banner.
- Implement right panel categories, hot topics, and compact topic tree with mock data.
- Implement map-like schematic with selectable Ashdod-style entities.
- Implement simple/detailed map mode.
- Implement low-confidence warnings.
- Implement bottom timeline state and filters.
- Implement left drawer modes.
- Implement cached summary and missing-cache placeholder flows.
- Implement fixed `1.2s` fake generation delay.
- Implement ask/search with canned mock answers.
- Add disabled `Admin` entry point.

Exit criteria:

- User can click category, hot topic, topic-tree node, map entity, and timeline range.
- UI state updates consistently.
- Map filters/highlights immediately.
- Cached/placeholder/ask flows work with `1.2s` delay.
- Evidence links open mock Evidence Mode.
- No extra resident-facing routes are needed.

### Phase 2: Real Data Integration

- Connect topics, categories, entities, timeline events, answers, summaries, decisions, and evidence to backend contracts.
- Make cached summaries real and evidence-backed.
- Make hot topics time-aware.
- Replace schematic entities gradually with real map layers where available.
- Add original protocol and attachment links.

Exit criteria:

- A resident can select a real project/place/topic and see related map entities, decisions, summaries, and evidence.

### Phase 3: Evidence Viewer

- Implement text highlight.
- Implement original PDF/page viewer.
- Implement bbox highlight.
- Show fallback when bbox is unavailable.
- Preserve return state to answer/topic/entity.

Exit criteria:

- Every answer claim and decision can be traced to source view.

### Phase 4: Real GIS And Logged-In Capabilities

- Integrate MapLibre or equivalent real map canvas.
- Add external map layers through generic layer contracts.
- Add saved views/questions.
- Add export.
- Add admin/debug panels.
- Add confidence review controls.

Exit criteria:

- Anonymous UX remains simple.
- Logged-in/admin UX adds power features without changing the core public flow.

## Verification Plan

Each stage should be verified with representative input/output evidence, not tests alone.

Phase 1 manual scenarios:

1. Load Explorer and confirm Hebrew RTL layout, demo banner, recent high-activity default map, and disabled Admin entry.
2. Click a category and confirm right-panel highlight, map filtering, left drawer summary/placeholder, and scope chips.
3. Click a hot topic and confirm category highlight, topic highlight, map filtering, topic context, and cached/placeholder flow.
4. Click a topic-tree node and confirm right panel, map, drawer, and scope state update without auto-answer.
5. Drag the timeline and confirm map entities and hot topics re-rank while current selection remains.
6. Click a map entity and confirm entity drawer, related topic highlights, confidence label, uncertainty warning, and progression events.
7. Ask a free-text Hebrew question and confirm `1.2s` loading, canned answer, evidence links, and map highlights.
8. Open a citation and confirm Evidence Mode with mock page, bbox highlight, extracted text, source title, and return state.
9. Switch to detailed map mode and confirm additional layers and low-confidence entities appear.
10. Test responsive widths and confirm layout does not break.

Concise evidence to print/log during verification:

```text
selected_category_id
selected_topic_node_id
selected_map_entity_id
selected_time_range
visible_hot_topic_ids
visible_map_entity_count
map_mode
generation_status
active_scope_chips
evidence_reference_id
evidence_source_page
```

## Open Later Decisions

- Exact real category taxonomy after ingestion starts.
- Real map source priority and licensing constraints.
- Whether a large topic graph should replace the map temporarily or open as a modal.
- How often cached summaries should be regenerated after real ingestion batches.
- Whether public users should eventually see all low-confidence entities or only filtered subsets.
