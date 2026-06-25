# Municipality RAG UI Plan v6

## Purpose

Build a Hebrew-first, RTL-native, search-first extended POC for exploring municipality protocols, decisions, attachments, topics, mapped entities, timelines, and original evidence.

The POC should support three resident intents:

```text
1. What was decided?
2. What happened near this place?
3. What is active in the city?
```

The first interaction is search. The map, timeline, topic tree, and inline source links remain visible as civic context instead of replacing the answer experience. Sources are accessed from decision rows through compact links, not through a permanent evidence-card block in the answer drawer.

The POC must be contract-first: mock data should use contracts shaped like the future API responses so the product can transition to real backend data without redesign.

## Locked Product Decisions

- Primary audience: residents.
- Resident-facing UI language: Hebrew.
- Resident-facing layout: RTL-native.
- First interaction: search or question.
- The submitted/current question must remain visible inside the main search field after search.
- The top bar must prioritize a long question field so the product reads as question-first.
- Do not show a permanent horizontal scope-chip row in the header.
- Use one compact Hebrew filters button named `מסננים` near the question field. In the desktop header, place it physically to the left of `חיפושים פופולריים`.
- The filters modal/sheet is an interaction state only: it opens after the user clicks `מסננים`, and it must not be visible in the default answered-state mockup.
- Use a Hebrew cached-question reuse button named `חיפושים פופולריים` next to the question field; the `מסננים` button sits immediately to its physical left on desktop.
- Active filters live in state and are visible inside the filters modal/sheet after it is opened; a small count badge on `מסננים` is allowed, but not a row of chips.
- Search result destination: open directly in the large end-side detail drawer on desktop.
- No central answer card over the map.
- Map remains visible after search.
- Timeline remains visible after search.
- Topic tree and discovery remain visible as refinement/context.
- Discovery/navigation belongs on the RTL start edge, which is the right side.
- Answer/detail belongs on the RTL end edge, which is the left side. Resident-facing source links are placed inline beside decision rows and open a compact evidence/source preview overlay when requested.
- Phase 1 data may be mock, but contracts must resemble future backend API responses.
- Phase 1 map is schematic, not real GIS.
- Do not mirror geography in RTL.
- Mirror interface chrome, controls, chevrons, navigation order, and panels.
- Use logical region names and CSS logical properties, not hardcoded left/right concepts.
- Low-confidence items may be visible, but resident uncertainty and admin/debug uncertainty must be separate.
- Public codelists are governed, approved, and versioned. They are not free text.
- Answer Mode must not reserve space for a standalone `ראיות ומקורות` section. Resident-facing evidence access appears as small same-row source links labeled `מקור`, primarily beside decision rows.
- Public presentation mockups may omit the POC/demo banner to evaluate the production visual hierarchy; working POC environments may still show a disclaimer.

## Logical Layout

Use logical names throughout design and code.

```text
RTL desktop

+--------------------------------------------------------------------------------+
| Search-first top bar                                                           |
| [עירייה] [שדה שאלה ארוך: מה הוחלט לגבי תכנית רובע טו?] [חיפושים פופולריים] [מסננים] [Admin] |
+--------------------------------------------------------------------------------+
| Demo banner                                                                    |
+--------------------------+-------------------------------+---------------------+
| End Detail Drawer       | Main Civic Workspace           | Start Discovery     |
| תשובה / תקציר / החלטות   | schematic map + highlights     | קטגוריות / נושאים   |
| קישורי מקור / מגבלות     | timeline-linked entities       | עץ נושאים           |
+--------------------------+-------------------------------+---------------------+
| Timeline                                                                        |
+--------------------------------------------------------------------------------+
```

Implementation mapping:

```text
startDiscoveryPanel -> right side in RTL
mainCivicWorkspace -> center
endDetailDrawer -> left side in RTL
```

Do not name product components `leftDrawer` or `rightPanel`. Use physical left/right only inside low-level layout code when unavoidable.

## RTL And Geography Rules

```text
Mirror interface chrome.
Do not mirror geography.
Use logical layout names: start/end, not right/left.
```

Use CSS logical properties where possible:

```css
padding-inline-start
padding-inline-end
margin-inline-start
margin-inline-end
inset-inline-start
inset-inline-end
border-inline-start
border-inline-end
text-align: start
```

The schematic city representation should keep stable spatial meaning. Coastline, north/south/east/west relationships, and city-area relationships must not flip because the UI is RTL.

## Opening Experience

The opening state should emphasize search first:

```text
מה תרצו לבדוק בעירייה?

[ חיפוש או שאלה בפרוטוקולים, תכניות, החלטות ומקורות... ]

דוגמאות:
[מה הוחלט לגבי תכנית רובע טו?]
[מה קרה ליד פארק לכיש?]
[אילו נושאים פעילים בתחום התחבורה?]
```

The map, timeline, and discovery panel still load immediately. They provide context and refinement, not competition with search.

Answered/search state top bar:

```text
[ עירייה ] [ מה הוחלט לגבי תכנית רובע טו?                                      🔍 ] [ חיפושים פופולריים ] [ מסננים ] [ מנהל ]
```

Top-bar rules:

- The question field must be the widest control in the header.
- Do not place active scope chips next to or under the search bar.
- The `מסננים` button opens a modal/sheet with area, time, category, source type, and uncertainty filters.
- `חיפושים פופולריים` opens cached/reusable questions and answers.

## Top Bar, Popular Searches, And Filters

The header must make the product feel question-first, not filter-first. The current question field is the dominant element; filters are available but collapsed by default.

Required desktop header order, described physically from right to left:

```text
municipality branding -> long current-question search field -> חיפושים פופולריים -> מסננים -> admin/account
```

This means `מסננים` is physically to the left of `חיפושים פופולריים` on desktop.

Filter rules:

- The button label is `מסננים`.
- The filter icon must sit inside the button with consistent RTL padding; it must not collide with the text or badge.
- The search field remains longer than all adjacent controls combined where possible.
- Active filters are not displayed as a persistent row of scope chips.
- A compact badge such as `1` or `3` inside the `מסננים` button is allowed when filters are active.
- The filters modal/sheet is closed in the standard answered-state screen. Do not show it unless the screen is specifically documenting the clicked-filter state.
- Clicking `מסננים` opens a modal on desktop and a bottom/full-screen sheet on mobile.
- The modal title is `סינון תוצאות`.
- Modal sections: `אזור`, `טווח זמן`, `קטגוריה`, `סוגי מקורות`, `ודאות`.
- Modal actions: `איפוס` and `החל סינון`.
- Search uses the active filter state when the user submits or reruns the question.

Popular-searches rules:

- The button label is `חיפושים פופולריים`.
- It opens reusable/cached questions, for example `מה הוחלט לגבי תכנית רובע טו?`.
- Selecting a popular question fills the question field and can reuse a cached answer when the filter state matches.

## Search Intent Layer

Phase 1 does not require real semantic search. It should use deterministic intent resolution for demo questions.

```ts
type SearchIntent =
  | "decision_about_topic"
  | "activity_near_place"
  | "city_activity_overview"
  | "source_lookup"
  | "unknown";
```

Intent behavior:

```text
decision_about_topic
-> select topic
-> highlight related map entities
-> open End Detail Drawer in Answer Mode
-> show decisions, claims, limitations, and inline evidence links

activity_near_place
-> select map entity/place
-> show timeline events
-> show related topics and decisions
-> open End Detail Drawer in Answer or Entity Mode

city_activity_overview
-> select category/time scope
-> rank hot topics
-> show active entities/density
-> open End Detail Drawer in Overview or Answer Mode

source_lookup
-> show source results or an evidence preview, without adding a permanent evidence-card block to Answer Mode

unknown
-> show partial/no-result state with suggested examples
```

Search submit rule:

```text
Search submit
-> resolve SearchIntent
-> update active filter state and filter count when inferred
-> update selected topic/entity/category/time if inferred
-> highlight map and timeline context
-> open End Detail Drawer in Answer Mode
-> show loading/status state
-> show answer with claims and per-claim/per-decision evidence links
```

## Start Discovery Panel

The start discovery panel is the main navigation/refinement surface.

It contains:

- Categories.
- Hot topics.
- Focused topic tree.
- Time-aware topic counts.

Global filters are not shown as a scope-chip row inside the discovery panel or header. They live behind the `מסננים` button and modal/sheet.

Behavior:

- Clicking a category updates `selected_category_id` and filters the map/context.
- Clicking a hot topic updates `selected_topic_node_id`, active filter state if relevant, map highlights, and drawer context.
- Clicking a topic-tree node updates the focused tree context and related map/timeline/evidence links.
- Discovery interactions do not auto-run a fresh answer.
- Search uses the active filter state from the `מסננים` modal unless the user resets filters.

## Main Civic Workspace

The main civic workspace contains the schematic map and civic context.

Phase 1 map is schematic:

- It may look geographically meaningful.
- It must be labeled as schematic/demo.
- It must not claim authoritative GIS accuracy.
- It must not encode fake geometry as real GeoJSON.

Map behavior:

- Default: recent high-activity entities.
- Search/topic selected: related entities highlighted.
- Place/entity intent: selected entity highlighted prominently.
- Time range selected: active entities update.
- Low-confidence simple mode: show only relevant uncertain items with warnings.
- Detailed mode: show all relevant uncertain items with stronger warnings.

## Timeline

The timeline is a first-class context surface.

It supports:

- Primary event sorting.
- Time range filtering.
- Entity/topic activity periods.
- Decision/action dates.
- Uncertain dates.

The timeline must support multiple time anchors per decision/action because municipal documents can contain meeting date, decision date, publication date, effective date, deadline, implementation period, and budget year.

## End Detail Drawer

The end detail drawer is the main answer/detail surface. Evidence must be reachable through compact resident-facing source links, but Answer Mode must not allocate a separate evidence/source-card section. The visible link label in the answer drawer is `מקור`, not `ראיה`.

Modes:

```text
answer
overview
category
topic
topicTree
mapEntity
timelineEvent
evidencePreview
disabledAdmin
```

Search submit opens this drawer directly in `answer` mode on desktop.

On mobile, the end detail drawer becomes a full-screen sheet.

Answer Mode must show:

- Question.
- Current question and compact access to filters through the top-bar `מסננים` button; do not duplicate a chip row inside the drawer.
- Short answer or refusal/partial state. The brief/summary section does not need its own visible source link when it only summarizes the decision rows below.
- Key decisions/actions, each with a compact same-row source icon/link labeled `מקור`; the link must not create a second line under each decision.
- A compact `נושאים קשורים` section directly beneath the decision/source rows.
- Timeline context.
- Limitations.

Rules:

- Every decision row and every standalone factual claim outside the brief/summary must have at least one visible source reference as a compact icon/text link labeled `מקור`.
- Decision source links must be same-row, low-height controls; do not show a separate evidence subrow beneath each decision.
- Do not render a standalone `ראיות ומקורות` section, evidence-card list, or source-card stack inside Answer Mode.
- The related-topics heading must be exactly `נושאים קשורים`, not `עוד נושאים קשורים`.
- Show at least five related topics. Choose them because they are related by geography, topic, or category, but do not display relation-type labels in the chips/rows.
- Related-topic examples for the `רובע טו` answer: `רובע טו`, `תכנון ובנייה`, `תוכניות מפורטות`, `תחבורה ציבורית`, `שטחים פתוחים`, `השפעה סביבתית`.
- If evidence is insufficient, show a partial/refusal state and explain missing evidence next to the affected decision or standalone claim.
- Clicking a `מקור` link opens `evidencePreview` as a modal/sheet with source title, artifact kind, header path, page span, and snippet, without losing search/map/topic state.

## Related Topics UI Contract

Related topics are not a generic “more” list. They are selected because the answer has nearby geography, topic, or category connections. In the compact Answer Mode chips, show only the topic labels; do not show relation-type labels such as `גאוגרפיה`, `נושא`, or `קטגוריה`.

```json
{
  "id": "related_topic_1",
  "label": "רובע טו",
  "relation_type": "geography",
  "relation_label_he": "גאוגרפיה",
  "target_topic_id": "topic_rova_tet_vav"
}
```

Rules:

- Use the heading `נושאים קשורים`.
- Place the section directly below the compact decision/source rows in Answer Mode.
- Show at least five related topics when the selected answer has enough related geography, topic, or category links.
- Use compact chips or rows. The section must not be taller than the decisions section.
- Do not use the heading `עוד נושאים קשורים`.
- Store `relation_type` internally for ranking, filtering, and optional tooltips/previews, but do not print relation-type labels in the compact chips.

## Topic Contracts

Topic data has two layers:

```text
TopicNode: canonical topic/entity tree record.
FocusedTopicTreeContext: UI view around the selected node.
```

### TopicNode

```json
{
  "id": "topic_123",
  "label": "תכנית רובע טו",
  "origin": "hybrid",
  "curation_status": "edited",

  "generated_label": "רובע טו תכנית בנייה",
  "curated_label": "תכנית רובע טו",

  "topic_type": {
    "code": "PLANNING_PROJECT",
    "label_he": "פרויקט תכנון",
    "vocabulary": "municipal-topic-type:v1"
  },

  "category_ids": ["planning", "housing"],
  "primary_category_id": "planning",

  "parent_id": "topic_parent",
  "child_ids": ["topic_child_1", "topic_child_2"],
  "sibling_ids": ["topic_sibling_1", "topic_sibling_2"],
  "path_ids": ["topic_root", "topic_parent", "topic_123"],
  "depth": 2,
  "sort_order": 40,

  "mention_count": 23,
  "decision_count": 7,
  "recent_activity_at": "2025-02-12",

  "time_rollup": {
    "first_seen": "2021-01-01",
    "last_seen": "2025-02-12",
    "active_years": [2021, 2022, 2023, 2024, 2025]
  },

  "decision_rollups": {
    "decision_kind": {
      "DISCUSSION": 4,
      "APPROVAL": 2,
      "BUDGET_ALLOCATION": 1
    },
    "outcome_status": {
      "APPROVED": 2,
      "NO_FORMAL_OUTCOME": 3,
      "PENDING": 2
    },
    "legal_effect": {
      "BINDING": 1,
      "INFORMATIONAL": 5,
      "UNKNOWN": 1
    }
  },

  "merged_from_topic_ids": ["topic_old_1"],
  "split_from_topic_id": null,
  "hidden_from_public": false,

  "confidence_label": "בינונית",
  "evidence_refs": ["evidence_1"]
}
```

### FocusedTopicTreeContext

```json
{
  "selected_topic_id": "topic_123",
  "root": { "id": "topic_root", "label": "תכנון ובנייה" },
  "parent": { "id": "topic_parent", "label": "רובע טו" },
  "breadcrumbs": [
    { "id": "topic_root", "label": "תכנון ובנייה" },
    { "id": "topic_parent", "label": "רובע טו" },
    { "id": "topic_123", "label": "תכנית רובע טו" }
  ],
  "siblings": [],
  "children": [],
  "nearby_topics": []
}
```

## Decision Contract

Use the resident-facing name `Decision`, but model it internally as a municipal action/event.

```json
{
  "id": "decision_1",
  "title": "דיון בתכנית רובע טו",
  "raw_decision_text": "דיון באישור התכנית...",
  "summary": "סיכום קצר לתצוגה ציבורית",

  "primary_time": {
    "start": "2023-06-14",
    "end": null,
    "precision": "day",
    "kind": "meeting_date",
    "label_he": "תאריך הדיון",
    "confidence_label": "גבוהה",
    "evidence_refs": ["evidence_1"]
  },

  "time_anchors": [
    {
      "kind": "meeting_date",
      "start": "2023-06-14",
      "end": null,
      "precision": "day",
      "label_he": "תאריך הדיון",
      "confidence_label": "גבוהה",
      "evidence_refs": ["evidence_1"]
    },
    {
      "kind": "effective_date",
      "start": null,
      "end": null,
      "precision": "unknown",
      "label_he": "תחילת תוקף",
      "confidence_label": "נמוכה",
      "evidence_refs": []
    }
  ],

  "decision_kind": {
    "code": "DISCUSSION",
    "label_he": "דיון",
    "vocabulary": "municipal-decision-kind:v1",
    "raw_text": "דיון באישור התכנית",
    "confidence_label": "גבוהה"
  },

  "outcome_status": {
    "code": "NO_FORMAL_OUTCOME",
    "label_he": "ללא החלטה פורמלית",
    "vocabulary": "municipal-outcome-status:v1",
    "raw_text": "הנושא נדון ולא נרשמה הצבעה",
    "confidence_label": "בינונית"
  },

  "legal_effect": {
    "code": "INFORMATIONAL",
    "label_he": "מידעי",
    "vocabulary": "municipal-legal-effect:v1",
    "confidence_label": "בינונית"
  },

  "topic_ids": ["topic_123"],
  "entity_ids": ["entity_456"],
  "timeline_event_ids": ["event_789"],
  "evidence_refs": ["evidence_1"],
  "resident_evidence_links": [
    {
      "label_he": "מקור",
      "icon": "document-link",
      "evidence_ref": "evidence_1"
    }
  ],

  "normalized_by": "mock",
  "curation_status": "unreviewed",
  "limitations": ["לא נמצא נוסח החלטה פורמלי במקור"]
}
```

Initial `decision_kind` codelist:

```text
DISCUSSION
APPROVAL
REJECTION
DEFERRAL
REFERRAL
RECOMMENDATION
BUDGET_ALLOCATION
STATUS_UPDATE
REQUEST_FOR_INFO
PUBLICATION
OBJECTION_HEARING
PROCEDURAL_ACTION
UNKNOWN
```

Initial `outcome_status` codelist:

```text
APPROVED
APPROVED_WITH_CONDITIONS
REJECTED
DEFERRED
REFERRED
NOTED
PENDING
NO_FORMAL_OUTCOME
PARTIAL
UNKNOWN
```

Initial `legal_effect` codelist:

```text
BINDING
ADVISORY
INFORMATIONAL
PROCEDURAL
UNKNOWN
```

Initial `time_anchor.kind` codelist:

```text
meeting_date
decision_date
publication_date
effective_date
deadline
implementation_start
implementation_end
review_date
budget_year
unknown
```

## Open Codelist Governance

Open codelists are controlled, versioned, and extensible. They are not endless free-text values.

Public UI only exposes approved active codes.

```json
{
  "code": "APPROVAL",
  "label_he": "אישור",
  "description_he": "אישור פורמלי של פעולה, תכנית, תקציב או בקשה",
  "vocabulary": "municipal-decision-kind:v1",
  "status": "active",
  "aliases_he": ["אושר", "מאשרים", "הוחלט לאשר"],
  "sort_order": 20
}
```

Governance flow:

```text
source text
-> extractor maps to approved code if possible
-> uncertain mappings use UNKNOWN or broader approved code
-> repeated unmapped patterns become candidate codes
-> admin/reviewer approves, merges, renames, or rejects
-> approved values enter next vocabulary version
```

Resident queries remain natural Hebrew. Users do not need to know codes.

Examples:

```text
מה אושר בתחום התחבורה?
אילו נושאים נדחו?
איפה יש דיונים בלי החלטה פורמלית?
מה מחייב ומה רק עדכון?
```

The system maps aliases and source wording to approved codelist values internally.

Topic tree and codelists are complementary:

```text
Topic tree answers: What is this about?
Codelists answer: What happened, what was the outcome, what is the effect, and when?
```

## Evidence Contract

Evidence must align with RAG v2 artifact-native retrieval.

```json
{
  "id": "evidence_1",
  "source_type": "protocol",
  "source_title": "פרוטוקול מועצה 14.06.2023",
  "source_url": "...",

  "retrieval_artifact_id": "artifact_123",
  "artifact_kind": "decision_unit",
  "retrieval_set_id": "retrieval_set_456",
  "header_path": ["מועצת העיר", "תכנון ובנייה", "תכנית רובע טו"],

  "page_span": { "start": 7, "end": 7 },
  "start_offset": 1023,
  "end_offset": 1188,
  "bbox": [120, 220, 510, 270],

  "text": "...",
  "confidence_label": "גבוהה",
  "extraction_warnings": []
}
```

Resident-facing presentation:

- In Answer Mode, evidence appears as compact resident-facing `מקור` links beside decision rows and standalone claims, not as source cards.
- The visible link label should use source language, for example `מקור` or `2 מקורות`; do not use `ראיה` as the link label in the answer drawer.
- Clicking the link opens `evidencePreview` with the artifact-native metadata below.

Do not expose legacy `chunk_id` terminology in the resident-facing contract if the active retrieval architecture is artifact-native. If current backend code still uses `chunk_id` internally, adapt it at the API boundary.

## Map Entity Contract

Schematic UI geometry and real GIS geometry are separate.

```json
{
  "id": "entity_456",
  "label": "פרויקט מגורים פארק לכיש",

  "entity_type": {
    "code": "PROJECT",
    "label_he": "פרויקט",
    "vocabulary": "municipal-entity-type:v1"
  },

  "spatial_representation": "schematic",
  "schematic_shape": {
    "kind": "polygon",
    "coordinates": []
  },
  "real_geometry": null,

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

Real geometry must include provenance, accuracy, CRS/coordinate assumptions, source, and confidence. Do not invent real geometry from low-confidence text evidence.

## Low Confidence UX

Resident simple mode:

```text
Show relevant uncertain items only.
Do not show raw scores.
Use warning labels and evidence links.
```

Resident detailed mode:

```text
Show all relevant low-confidence items.
Use stronger warnings and evidence links.
```

Admin/debug mode:

```text
Show raw confidence, reason codes, model/source provenance, merge/split tools, and review controls.
```

Resident copy:

```text
זיהוי לא ודאי — מוצג כדי לאפשר בדיקת המקור.
מומלץ לפתוח את המקור לפני הסקת מסקנות.
```

## Accessibility Requirements

- Use global `dir="rtl"` for resident UI.
- Use `dir="auto"` for unknown-direction runtime text where needed.
- Use visible labels or accessible labels for search inputs.
- Expose dynamic loading, result count, no-result, partial-answer, and refusal states as status messages.
- Do not rely on color alone for category, confidence, or warning state.
- Provide keyboard access for search, the `מסננים` button/modal, popular-searches button, topic tree, map entities, timeline controls, drawer tabs, and evidence links.
- Preserve focus and return state when opening and closing `evidencePreview`.

## State Model

Suggested state fields:

```text
municipality_id
current_question
search_intent
intent_resolution
selected_time_range
selected_category_id
selected_topic_node_id
selected_map_entity_id
selected_timeline_event_id
current_answer_id
active_detail_drawer_mode
confidence_filter
source_type_filter
filter_modal_open
active_filter_count
popular_searches_open
cached_answer_id
map_mode
generation_status
login_state
```

Derived state:

```text
active_filter_summary
inline_evidence_links
related_topic_links
focused_topic_tree_context
visible_hot_topics
visible_map_entities
visible_map_layers
related_decisions
related_evidence
entity_progression_events
answer_scope
active_uncertainty_warnings
```

## Phase Scope

### P0: Search-First Extended Mock POC

- Search-first opening.
- Deterministic `SearchIntent` resolution.
- Start Discovery Panel.
- Main Civic Workspace with schematic map.
- End Detail Drawer.
- Timeline.
- Compact `מסננים` button; filters modal exists as a clicked state and is closed in the standard answered-state screen.
- `חיפושים פופולריים` button for cached/reusable questions.
- Mock answers with claims, decisions, compact same-row inline evidence links, and at least five related topics when relevant.
- Mock topic tree using future-shaped contracts.
- Mock decision codelists and time anchors.
- Mock low-confidence resident modes.

### P1: Product-Transition Skeleton

- Data provider abstraction: mock now, API later.
- RAG v2-compatible evidence contract.
- Topic curation fields.
- Open codelist registry.
- Low-confidence resident/admin model separation.
- Accessible status/no-result/refusal states.

### P2: Later Product Work

- Real PDF/page/bbox evidence viewer.
- Real GIS.
- Admin curation UI.
- Backend codelist extraction/classification.
- Real topic curation workflow.
- Saved views, export, and logged-in capabilities.

## Verification Plan

Manual scenarios:

```text
1. Open Explorer and confirm search-first Hebrew RTL layout with a long question field.
2. Submit "מה הוחלט לגבי תכנית רובע טו?" and confirm the question remains visible in the search field and End Detail Drawer Answer Mode opens.
3. Confirm selected topic, map highlight, timeline events, claims, and same-row inline `מקור` links beside every decision.
4. Submit "מה קרה ליד פארק לכיש?" and confirm entity/timeline context.
5. Submit "אילו נושאים פעילים בתחום התחבורה?" and confirm category, hot topics, map entities, and rollups.
6. Confirm `נושאים קשורים` appears below the decision/source rows with at least five items and no visible relation-type labels, then click a decision `מקור` link and confirm the evidence preview shows artifact/page/header-path/source metadata.
7. Toggle detailed uncertainty mode and confirm low-confidence warnings.
8. Resize to mobile and confirm drawer becomes full-screen sheet.
9. Confirm geography is not mirrored in RTL.
10. Confirm loading, no-result, refusal, and partial-answer states are accessible status messages.
```

Concise evidence to print/log during verification:

```text
search_intent
selected_category_id
selected_topic_node_id
selected_map_entity_id
selected_time_range
active_detail_drawer_mode
visible_hot_topic_ids
visible_map_entity_count
generation_status
active_filter_summary
inline_source_link_id
evidence_reference_id
retrieval_artifact_id
header_path
page_span
```


## v4 Change Notes

- Replaced persistent top-bar scope chips with a compact `מסננים` button and `סינון תוצאות` modal/sheet.
- Expanded the top search field and required it to display the current question after search.
- Added `חיפושים פופולריים` for reusable/cached questions.
- Removed the standalone `ראיות ומקורות` block from Answer Mode.
- Required compact source links beside every decision row and standalone factual claim.
- Added `resident_evidence_links`, `filter_modal_open`, `active_filter_count`, `popular_searches_open`, and `cached_answer_id` state/model fields.


## v5 Change Notes

- The standard answered-state mockup must not show the filters modal; only the closed `מסננים` button is visible.
- The generated image removes the visible POC/demo banner while keeping the plan as a POC specification.
- Header height is increased and the removed banner space is reused to emphasize the long current-question search field.
- Desktop header order is clarified: branding, long question field, `חיפושים פופולריים`, `מסננים`, admin/account, with `מסננים` physically to the left of `חיפושים פופולריים`.
- Decision source links are compact same-row controls, not separate rows and not a standalone evidence/source section.
- `נושאים קשורים` replaces any `עוד נושאים קשורים` language and must show at least five topics with internal geography, topic, or category relationships, without visible relation labels in the compact chips.

## v6 Change Notes

- Restored the cleaner visual baseline: no filter modal in the standard image, no visible POC banner in the image, and the original schematic map style is preserved.
- The header remains taller and question-first, with a long current-question field.
- `מסננים` stays physically to the left of `חיפושים פופולריים`, with icon/badge padding treated as a button-layout requirement.
- Decision-row links are now resident-facing source links labeled `מקור`; the answer drawer should not use `ראיה` as the link label.
- The brief/summary section does not need its own visible source link when it is only summarizing the linked decision rows.
- `נושאים קשורים` shows at least five related topics chosen by geography, topic, or category, but the compact chips must not display relation-type labels.
