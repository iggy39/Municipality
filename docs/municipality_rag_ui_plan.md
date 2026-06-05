# Municipality RAG UI Plan

## Purpose

Design a research-grade but public-friendly POC UI for exploring municipality protocols, decisions, attachments, topics, and mapped entities.

The UI should be intuitive for residents, remain flexible during the POC stage, and avoid spreading the experience across many screens. The core idea is one main explorer where users can ask questions, browse hot topics, inspect a topic tree, filter mapped entities by time, and open original evidence.

## Product Goals

- Let a resident understand what the municipality decided about a topic, place, project, parcel, plan, or entity.
- Make discovery possible without knowing exact protocol wording.
- Keep the map and timeline central, because many municipal decisions are place-based and evolve over time.
- Make all generated answers evidence-first, with links to original protocols and highlighted source spans.
- Support anonymous use for public exploration.
- Support logged-in use for saved work, export, administration, and debugging.
- Keep the POC easy to change later by using clear UI regions, simple data contracts, and state-driven interactions.

## Non-Goals For The First POC

- Do not create many routes or separate screens for map, topics, timeline, evidence, and ask.
- Do not make login required for basic exploration.
- Do not hide source evidence behind generated summaries.
- Do not assume fixed protocol wording, structure, municipality schema, or language pattern.
- Do not design around one hardcoded municipality or one hardcoded document layout.

## Main Information Architecture

Use one main route: `Explorer`.

The app can later expose additional routes for admin or shared links, but the resident-facing POC should feel like one workspace.

Primary layout:

```text
+--------------------------------------------------------------------------------+
| Top Bar                                                                        |
| [Municipality] [Ask/search municipal protocols...] [filters] [Login/Profile]   |
+----------------------+--------------------------------------+------------------+
| Left Panel           | Main Map Canvas                       | Right Drawer     |
| Categories           |                                      | Context/Answer   |
| Specific hot topics  |                                      | Topic tree       |
| Topic-tree context   |                                      | Entity details   |
|                      |                                      | Evidence links   |
+----------------------+--------------------------------------+------------------+
| Bottom Horizontal Time Bar                                                     |
| [time range slider] [event density] [play/progression] [clear time filter]      |
+--------------------------------------------------------------------------------+
```

## Screen Regions

### Top Bar

The top bar is always visible.

Contents:

- Municipality selector or current municipality label.
- Ask/search input.
- Lightweight filters if needed: source type, confidence, only decisions, only protocols, only attachments.
- Login/profile button.

Behavior:

- Typing a question opens or updates the right drawer with an answer.
- Asking a question should not replace the map.
- If a time range is active, the ask result is scoped to that range unless the user clears it.
- If a map entity or topic is selected, the question inherits that context unless the user removes it.

### Left Panel: Categories And Hot Topics

The left panel is a compact discovery panel. It combines broad categories and specific hot topics from the topic tree.

Categories are stable, human-readable groupings, for example:

- Planning
- Housing
- Transport
- Education
- Environment
- Public Space
- Budget
- Welfare
- Business Licensing

Specific hot topics are not generic tags. They are concrete topic-tree nodes ranked by activity.

Examples:

- Planning: `תכנית רובע טו`, `התחדשות עירונית רחוב הרצל`, `שינוי ייעוד מגרש 204`
- Transport: `נתיבי אוטובוס בשד׳ בני ברית`, `חניות ליד בתי ספר`, `מעברי חציה באזור גנים`
- Housing: `פרויקט מגורים פארק לכיש`, `תוספת יחידות דיור`, `דיור להשכרה`

Hot topic ranking should use:

- Mention count in protocols, decisions, attachments, and evidence spans.
- Recent activity in the selected time range.
- Number of linked decisions.
- Number of linked mapped entities.
- Evidence confidence.
- Optional public-interest boost from user interactions, only if appropriate later.

Left panel behavior:

- Categories are always visible.
- Expanding a category shows specific hot topics from the topic tree.
- Clicking a category highlights that category, filters the map, updates hot topics, and opens category context in the right drawer.
- Clicking a hot topic highlights both the hot topic and its compatible category.
- If a hot topic belongs to multiple categories, show the primary category highlighted and secondary categories as small related badges.
- The selected category and selected hot topic remain visible even after the user interacts with the map or topic tree.
- When the bottom time bar changes, the hot topic list re-ranks for that selected time range.

Example state:

```text
Planning [active]
  תכנית רובע טו              [selected] [23 mentions] [recent]
  הקצאת מגרש 204             [12]
  התחדשות עירונית הרצל       [9]

Transport
  נתיבי אוטובוס
  חניות ליד בתי ספר

Education
  גני ילדים חדשים
  בטיחות ליד בתי ספר
```

### Main Map Canvas

The map is the main visual surface.

It shows entities active in the selected time range and matching the selected category/topic/question context.

Supported entity types for the first useful demo:

- Address or site points.
- Streets or corridors.
- Neighborhoods or named areas.
- Parcels, when available.
- Plans or planning polygons, when available.
- Project entities that may combine point, parcel, plan, and document evidence.

Map visual rules:

- High-confidence entities are solid.
- Medium-confidence entities are lighter or dashed.
- Low-confidence entities are hidden by default, but can be shown by logged-in/admin users.
- Entity color is based on category.
- Selected entity is visually prominent.
- Related entities are subtly highlighted.
- Entities outside the selected time range are hidden or ghosted only if the user asks to compare history.

Map interactions:

- Click entity: open entity context in right drawer and auto-ask a default question scoped to the entity and selected time range.
- Hover/tap entity: show lightweight label, confidence, active date range, and top linked topic.
- Multi-entity cluster click: zoom or open a small list before selecting one entity.
- Map lasso or bounding-box selection can be added later, but is not required for the first POC.

Default entity auto-question:

```text
What decisions mention this entity in the selected time range?
```

For Hebrew UI, the displayed default can be:

```text
מה הוחלט או נדון לגבי המקום הזה בתקופה שנבחרה?
```

### Bottom Horizontal Time Bar

The timeline is a map and topic filter, not merely an answer timeline.

It is always visible on desktop and sticky at the bottom on mobile.

Main behavior:

- User selects a date range by dragging handles.
- The map updates to show entities active in that range.
- Hot topics re-rank based on that range.
- The right drawer keeps the current selection but marks content as filtered by the selected time.
- Clearing the time range returns to all available dates.

Visual elements:

- Horizontal date scale by year/month.
- Event density histogram above the bar.
- Selected range highlight.
- Optional playback button for project progression.
- Markers for major events when an entity is selected.

Entity progression example:

```text
2021: first mention
2022: committee discussion
2023: approval
2024: attachment/publication
2025: amendment
```

When a residential project is selected:

- The map shows the project geometry and related entities over time.
- The time bar shows project events.
- The right drawer shows the selected event list and related decisions.
- Hot topics update to show what was active during that project phase.

### Right Context Drawer

The right drawer is the main detail surface. It prevents extra routes and keeps the map visible.

It should support multiple modes, but visually feel like one consistent panel.

Right drawer structure:

```text
+------------------------------------------+
| Header                                   |
| Selected thing + type                    |
| Breadcrumb/context path                  |
+------------------------------------------+
| Context Visual                           |
| Topic tree / entity mini-timeline /      |
| evidence preview / answer scope          |
+------------------------------------------+
| Main Content                             |
| Answer, decisions, source links, actions |
+------------------------------------------+
```

Right drawer modes:

1. Answer Mode
2. Category Mode
3. Topic Mode
4. Topic Tree Mode
5. Map Entity Mode
6. Timeline/Event Mode
7. Evidence Mode
8. Logged-In/Admin Mode

The drawer should use tabs or section headers only inside the drawer, not new app routes.

## Right Drawer Detailed Usage

### 1. Answer Mode

Triggered by:

- User types a question.
- User clicks a hot topic and the system auto-asks.
- User clicks a map entity and the system auto-asks.
- User clicks a topic-tree node and the system auto-asks or prepares a scoped question.

Looks like:

```text
[Answer]
Question: מה הוחלט לגבי תכנית רובע טו?
Scope: Planning > תכנית רובע טו | 2021-2025 | Ashdod

Short Answer
...

Key Decisions
- Decision title/date/status [open evidence]
- Decision title/date/status [open evidence]

Evidence
[Protocol p.3] [Attachment p.12] [Decision card]

Related on map
[3 parcels] [1 plan polygon] [2 addresses]

Limitations
...
```

Important behavior:

- The answer must always show source links.
- If evidence is insufficient, show a refusal with missing evidence explanation.
- The user should be able to click each claim or citation and open the evidence drawer/modal.
- Related topics should be visible so the user can pivot.

### 2. Category Mode

Triggered by clicking a category in the left panel.

Looks like:

```text
[Category]
Planning

Active in selected time range: 2022-2025
Linked entities on map: 48
Linked decisions: 116

Top Specific Topics
- תכנית רובע טו
- הקצאת מגרש 204
- התחדשות עירונית הרצל

Topic Tree Preview
Planning
  Housing
  Plans
  Permits
  Public Space

Actions
[Ask about this category]
[Show only high-confidence entities]
[Export if logged in]
```

Behavior:

- Highlights the category in the left panel.
- Shows top child topics ranked by the current time range.
- Filters or highlights matching map entities.
- Lets the user click a child topic from the tree or list.

### 3. Topic Mode

Triggered by clicking a specific hot topic or a topic-tree node.

Looks like:

```text
[Topic]
Planning > Housing > תכנית רובע טו

Activity
23 mentions | 7 decisions | 4 mapped entities | recent activity: 2025

Topic Context
Parent: Housing
Siblings: פרויקט פארק לכיש, תוספת יחידות דיור
Children: שלב א, שלב ב, נספחי תנועה

Auto Answer
מה הוחלט או נדון בנושא הזה בתקופה שנבחרה?
...

Evidence and Decisions
...
```

Behavior:

- Highlights the compatible category in the left panel.
- Highlights the matching hot topic if it appears in the current hot topic list.
- Filters or highlights related map entities.
- Shows the topic tree context.
- Auto-asks a default question in POC mode.

### 4. Topic Tree Mode

The topic tree is both a visualization and an input control.

POC version:

- Render a compact tree inside the right drawer.
- Center the selected node.
- Show parent, children, and siblings.
- Show mention and recency badges.
- Allow every visible node to be clicked.

Later version:

- A large topic graph overlay can take screen space, either replacing the map temporarily or opening as a wide modal.
- The selected node remains connected to the left panel and map filters.

POC visual example:

```text
Planning
  Housing
    Project X        [selected] [23] [recent]
    Project Y        [9]
    Rental Housing   [5]
  Permits
  Transport Impact
```

Clicking a tree node updates:

- Selected topic.
- Selected category.
- Left-panel highlighted category.
- Left-panel selected hot topic when present.
- Map entity filter/highlight.
- Right drawer topic details.
- Optional auto-asked answer.
- Hot topic ranking if the node changes the active context.

This creates two-way data flow:

```text
Left category/topic click -> right topic tree updates -> map filters
Right topic-tree node click -> left category/topic highlights -> map filters
Map entity click -> right entity card -> related topics highlight
Timeline change -> map + hot topics + drawer context update
```

### 5. Map Entity Mode

Triggered by clicking a map entity.

Looks like:

```text
[Map Entity]
Project / Parcel / Plan / Address: Project X

Active range: 2021-2025
Confidence: High
Geometry type: plan polygon + parcels

Related Topics
- Planning > Housing > Project X
- Transport > Traffic impact

Progression
2021 first mention
2022 discussion
2023 approval
2024 amendment

Auto Answer
מה הוחלט או נדון לגבי המקום הזה בתקופה שנבחרה?
...

Evidence
[Protocol p.4] [Attachment p.19] [Map source]
```

Behavior:

- Selected entity is highlighted on the map.
- Related entities are highlighted lightly.
- Related topics are highlighted in the left panel if visible.
- The topic tree shows related topic context.
- The bottom time bar shows entity progression markers.

### 6. Timeline/Event Mode

Triggered by clicking a marker or event on the horizontal time bar.

Looks like:

```text
[Timeline Event]
2023-06-14 | Approval discussion

Entity: Project X
Topic: Planning > Housing > Project X
Protocol: Council meeting ...

Decision Summary
...

Evidence
[Open original protocol p.7]
```

Behavior:

- Selecting an event focuses related map geometry.
- It may narrow the drawer content to that event.
- It does not change the global time range unless the user explicitly drags the time range.

### 7. Evidence Mode

Evidence can be shown inside the right drawer for short cases or as a second-level drawer/modal for dense documents.

Evidence view should include:

- Source title.
- Source type: protocol, attachment, decision, map source.
- Page number or page range.
- Highlighted extracted text span.
- Original PDF/page view with bbox highlight when available.
- Link to open the original document.
- Evidence confidence and extraction warnings.

POC layout:

```text
[Evidence]
Protocol: Council meeting 2024-03-18
Page: 7

[PDF/page image with bbox highlight]

Highlighted text
"..."

Used by claims
- Claim 1
- Claim 2
```

Fallback behavior:

- If bbox exists, show PDF/page bbox and extracted text highlight.
- If bbox is missing, show page-level link and text-offset highlight.
- If source document cannot be displayed inline, show metadata and open-original link.

### 8. Logged-In/Admin Mode

Anonymous users can browse, ask, inspect map/timeline, and open evidence.

Logged-in users can additionally:

- Save questions and views.
- Export answer/evidence bundles.
- Export timeline/map data.
- See debug traces and confidence details.
- Review low-confidence entities.
- Access ingestion/admin controls if authorized.

This should not change the public core UX.

## Core Interaction Flows

### Flow A: Resident Asks A Question

1. User types a question in the top bar.
2. System keeps current time/topic/map scope if active.
3. Right drawer opens in Answer Mode.
4. Map highlights related entities.
5. Left panel highlights related categories/topics if known.
6. Evidence links are shown under the answer.
7. Clicking evidence opens Evidence Mode.

### Flow B: Resident Clicks A Category

1. User clicks `Planning`.
2. Left panel highlights `Planning`.
3. Hot topics under `Planning` expand and re-rank by selected time range.
4. Map highlights planning-related entities.
5. Right drawer opens Category Mode with topic tree preview.
6. User can click a child topic in either the left panel or right tree.

### Flow C: Resident Clicks A Hot Topic

1. User clicks a specific hot topic.
2. Left panel highlights the hot topic and its compatible category.
3. Right drawer opens Topic Mode.
4. Topic tree centers around the selected topic.
5. Map highlights related entities.
6. System auto-asks a default question about the selected topic and time range.
7. Evidence and related decisions are shown.

### Flow D: Resident Clicks A Topic Tree Node

1. User clicks a node in the right drawer topic tree.
2. The selected node becomes active.
3. Left panel updates category and hot-topic highlights.
4. Map updates to related entities.
5. Hot topics may re-rank or preserve the selected node at top.
6. Drawer updates to Topic Mode for the clicked node.
7. System may auto-ask a default question in POC mode.

### Flow E: Resident Uses The Timeline

1. User drags the bottom time bar to `2022-2024`.
2. Map updates to entities active in that range.
3. Hot topics re-rank for that range.
4. Current drawer content remains but displays `Filtered by 2022-2024`.
5. If a selected entity has progression events, the bar shows them.
6. User can click an event to open Timeline/Event Mode.

### Flow F: Resident Clicks A Map Entity

1. User clicks a parcel, plan, project, street, address, or area.
2. Map highlights the selected entity.
3. Right drawer opens Map Entity Mode.
4. Related categories/topics highlight in the left panel.
5. Topic tree shows related topic context.
6. System auto-asks what decisions mention the entity in the selected time range.
7. User can inspect evidence or progression events.

### Flow G: Resident Opens Evidence

1. User clicks a citation, claim evidence, decision source, or protocol link.
2. Evidence Mode opens as a drawer section or second-level modal.
3. Original protocol page is shown with bbox highlight when available.
4. Extracted text highlight is shown alongside the original.
5. User can return to answer/topic/entity without losing state.

## State Model

The UI should be state-driven. Most interactions update shared explorer state.

Suggested state fields:

```text
municipality
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
login_state
```

Derived state:

```text
visible_hot_topics
visible_map_entities
topic_tree_focus
related_decisions
related_evidence
entity_progression_events
answer_scope
```

## Two-Way Data Flow Rules

The UI must support selecting topics from both the left panel and the topic tree.

Rules:

- Left category click sets `selected_category_id` and clears or preserves `selected_topic_node_id` depending on whether the topic belongs to that category.
- Left hot-topic click sets both `selected_topic_node_id` and compatible `selected_category_id`.
- Right topic-tree node click sets `selected_topic_node_id` and derives compatible category.
- Map entity click sets `selected_map_entity_id` and derives related topic/category highlights.
- Timeline range change updates visible map entities and hot topic ranking but should not erase selected topic/entity.
- Ask question uses current selected topic/entity/time range as scope unless removed by the user.

## Topic Tree Visualization Requirements

POC requirements:

- Render in the right drawer.
- Show selected node, parent, siblings, and children.
- Use count and recency badges.
- Make every node clickable.
- Keep selected category and hot topic synchronized with the left panel.
- Preserve map and time filters while navigating tree nodes.

Later visualization:

- Expandable radial/tree graph that can take over the map area or open as a large overlay.
- Node size by activity.
- Node color by category.
- Edge thickness by relationship strength or co-mention count.
- Time-aware animation showing topic activity over the selected range.

## Map And Timeline Requirements

Map entities should be time-aware.

Each map entity should have:

- Entity id.
- Display label.
- Geometry.
- Geometry type.
- Entity type: address, street, parcel, plan, area, project, facility, other.
- Confidence score.
- Active date range.
- Event list.
- Linked topics.
- Linked decisions.
- Evidence references.

Timeline events should include:

- Event id.
- Date or date range.
- Event type: mention, discussion, decision, approval, rejection, publication, amendment, unknown.
- Linked entity ids.
- Linked topic ids.
- Linked evidence references.
- Display summary.

The timeline does three things:

- Filters visible entities on the map.
- Re-ranks hot topics.
- Shows progression markers for selected entity/topic.

## Evidence Requirements

Every answer, decision, topic, and map entity should be traceable to evidence.

Evidence reference fields should support:

- Source id.
- Source type.
- Source title.
- Original URL or file reference.
- Page number or page range.
- Text offsets.
- Bbox coordinates when available.
- Extracted text snippet.
- Confidence.
- Extraction warnings.
- Linked claim or decision id.

Evidence display hierarchy:

1. Claim-level evidence.
2. Decision-level evidence.
3. Topic/entity evidence.
4. Original source document.

## Responsive Design

### Desktop

- Left panel: persistent, collapsible.
- Map: largest region.
- Right drawer: collapsible, resizable if easy.
- Time bar: persistent at bottom.
- Evidence: second-level drawer/modal.

### Tablet

- Left panel can become overlay.
- Right drawer can cover 40-60% width.
- Time bar remains sticky.
- Map remains primary.

### Phone

Use a single-screen shell with bottom tabs:

- Map
- Topics
- Answer
- Evidence

Phone behavior:

- Timeline remains sticky at bottom above system navigation.
- Topic tree opens as bottom sheet.
- Evidence opens as full-screen modal or bottom sheet.
- Ask/search remains at top.
- Selecting a topic/entity switches to Answer or opens a bottom sheet, depending on interaction complexity.

## Visual Design Direction

The UI should feel like a civic intelligence tool, not a generic chatbot.

Recommended visual language:

- Map-first, calm, data-rich.
- Strong evidence affordances.
- Clear distinction between generated answer and source material.
- Category colors used consistently across map, hot topics, and topic tree.
- Confidence indicators should be subtle but visible.
- Avoid overloading residents with debug language unless logged in.

Suggested color semantics:

- Category color: map layer and topic accents.
- Selected item: strong outline or filled highlight.
- Related item: pale tint.
- High confidence: solid.
- Medium confidence: dashed or lighter.
- Low confidence: hidden by default or warning style for admin.

## Data Contracts, Backend-Agnostic

The UI should not depend on one backend implementation. It needs stable product-level data contracts.

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
  "confidence": 0.91
}
```

### Map Entity

```json
{
  "id": "entity_456",
  "label": "Project X",
  "entity_type": "project",
  "geometry": { "type": "Polygon", "coordinates": [] },
  "confidence": 0.93,
  "active_from": "2021-01-01",
  "active_to": "2025-01-01",
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
  "event_type": "approval",
  "summary": "Project approval discussed",
  "entity_ids": ["entity_456"],
  "topic_ids": ["topic_123"],
  "evidence_refs": ["evidence_1"]
}
```

### Evidence Reference

```json
{
  "id": "evidence_1",
  "source_type": "protocol",
  "source_title": "Council protocol 2023-06-14",
  "source_url": "...",
  "page": 7,
  "start_offset": 1023,
  "end_offset": 1188,
  "bbox": [120, 220, 510, 270],
  "text": "...",
  "confidence": 0.95
}
```

## POC Build Phases

### Phase 1: Static Shell And Interaction Model

- Build one Explorer layout.
- Implement left category/hot topic panel with mock or precomputed data.
- Implement map placeholder with selectable entities.
- Implement bottom time bar with date range state.
- Implement right drawer modes.
- Implement topic tree preview and clickable nodes.
- Verify two-way highlight behavior.

Exit criteria:

- User can click category, hot topic, topic-tree node, map entity, and time range.
- UI state updates consistently.
- No extra routes needed for the main workflow.

### Phase 2: Real Data Integration

- Connect topics, categories, entities, timeline events, answers, and evidence to backend contracts.
- Make hot topics time-aware.
- Make map entities filter by selected time range.
- Add entity progression markers to time bar.
- Add evidence links to original protocols and attachments.

Exit criteria:

- A resident can select a project/place/topic and see related map entities, hot topics, answer, decisions, and evidence.

### Phase 3: Evidence Viewer

- Implement text highlight.
- Implement original PDF/page viewer.
- Implement bbox highlight when available.
- Show fallback state when bbox is unavailable.
- Preserve return state to answer/topic/entity.

Exit criteria:

- Every answer claim and decision can be traced to a source view.

### Phase 4: Polishing And Logged-In Capabilities

- Add saved views/questions.
- Add export.
- Add admin/debug panels.
- Add better topic graph visualization.
- Add confidence review controls.

Exit criteria:

- Anonymous UX remains simple.
- Logged-in UX adds power features without changing the core flow.

## Verification Plan

Each stage should be verified with representative input/output evidence, not tests alone.

Manual verification scenarios:

1. Click a category and confirm compatible hot topics, map entities, and right drawer update.
2. Click a hot topic and confirm its category is highlighted.
3. Click a topic-tree node and confirm left panel, map, and drawer update.
4. Drag the timeline and confirm map entities and hot topics re-rank.
5. Click a map entity and confirm entity card, related topic highlights, progression events, and auto-answer.
6. Ask a free-text question and confirm answer/evidence appear without leaving the map.
7. Open a citation and confirm text highlight plus PDF bbox when available.
8. Test on mobile width and confirm the same workflow is available through tabs/bottom sheets.

Evidence to print during verification:

- Selected category id.
- Selected topic node id.
- Selected map entity id.
- Selected time range.
- Visible hot topic ids and scores.
- Visible map entity count.
- Evidence reference id and source page.

## Open Product Decisions

- Exact category taxonomy for the first municipality.
- Whether hot topic ranking should prioritize recent activity over total mentions.
- Whether clicking a topic-tree node always auto-asks, or only updates context and offers an ask button.
- How much low-confidence map evidence anonymous users should see.
- Whether the large topic visualization should replace the map temporarily or open as a modal overlay.

## Recommended Defaults

- Resident/public is the primary POC user.
- Anonymous users can explore, ask, and inspect evidence.
- Logged-in users get save/export/admin/debug capabilities.
- Clicking a hot topic auto-asks a default question.
- Clicking a map entity auto-asks a default entity-scoped question.
- Clicking a topic-tree node updates context and auto-asks in POC mode, with a later setting to disable auto-ask if needed.
- Evidence MVP should show text highlight plus PDF bbox when available.
- Map MVP should support points, areas, parcels, and plans when data exists, with strict confidence display rules.
