# Milestone M6 - Timeline Track A (Decisions)

Goal: create and expose decision timeline events, add timeline UI, and support export for research workflows.

## Exit Gate

- [ ] Decision events are generated and persisted reliably.
- [ ] Timeline API supports filtering and pagination.
- [ ] Timeline UI displays Track A (decisions) correctly in Hebrew workflow.
- [ ] CSV/JSON export works for research usage.
- [ ] Ordering/date correctness is verified by tests.

## Ticket Board

| ID | Title | Priority | Depends On |
|---|---|---|---|
| M6-T01 | Event schema and decision-event generator | High | M5-T10 |
| M6-T02 | Event backfill for existing decisions | High | M6-T01 |
| M6-T03 | Timeline API (`GET /timeline`) | High | M6-T02 |
| M6-T04 | Timeline filtering and pagination | Medium | M6-T03 |
| M6-T05 | CSV/JSON export pipeline | Medium | M6-T03 |
| M6-T06 | Timeline UI Track A renderer | High | M6-T04 |
| M6-T07 | Date ordering and timezone regression tests | High | M6-T06 |
| M6-T08 | Performance and payload tuning | Medium | M6-T07 |
| M6-T09 | Research handoff docs and usage guide | Medium | M6-T05 |
| M6-T10 | M6 QA report and final signoff | High | M6-T09 |

---

## Tickets

### M6-T01 - Event schema and decision-event generator
Checklist:
- [ ] Implement/verify `event` schema for Track A decisions.
- [ ] Generate event per decision with meeting date as event date.
- [ ] Store references to meeting/decision/source citations.

Done when:
- [ ] Newly parsed decisions automatically produce timeline events.

### M6-T02 - Event backfill for existing decisions
Checklist:
- [ ] Backfill events for all existing decision records.
- [ ] Prevent duplicate event creation on reruns.
- [ ] Log backfill stats and conflicts.

Done when:
- [ ] Historical decisions in scope are represented in timeline.

### M6-T03 - Timeline API (`GET /timeline`)
Checklist:
- [ ] Implement timeline endpoint response schema.
- [ ] Include key metadata for display and drill-down.
- [ ] Include evidence links/citations where relevant.

Done when:
- [ ] API supports rendering Track A without additional joins in UI.

### M6-T04 - Timeline filtering and pagination
Checklist:
- [ ] Add filters (topic, date range, committee, scope where applicable).
- [ ] Add stable pagination and deterministic ordering.
- [ ] Add tests for filter combinations.

Done when:
- [ ] Timeline API remains predictable under filtering.

### M6-T05 - CSV/JSON export pipeline
Checklist:
- [ ] Add export endpoint or job for timeline data.
- [ ] Include citation and source fields in export.
- [ ] Verify UTF-8/Hebrew compatibility in exports.

Done when:
- [ ] Research team can download reusable timeline data.

### M6-T06 - Timeline UI Track A renderer
Checklist:
- [ ] Build timeline screen for decision events.
- [ ] Add event click-through to decision card.
- [ ] Display lag/recency indicators where available.

Done when:
- [ ] Timeline interaction is usable and linked to evidence pages.

### M6-T07 - Date ordering and timezone regression tests
Checklist:
- [ ] Add date parsing and ordering tests across edge cases.
- [ ] Add timezone normalization policy tests.
- [ ] Add duplicate-date event ordering fallback tests.

Done when:
- [ ] Chronological ordering errors are prevented by regression suite.

### M6-T08 - Performance and payload tuning
Checklist:
- [ ] Add performance checks for timeline queries.
- [ ] Optimize indexes and payload size.
- [ ] Confirm local deployment responsiveness.

Done when:
- [ ] Timeline responses meet accepted local performance target.

### M6-T09 - Research handoff docs and usage guide
Checklist:
- [ ] Document filter semantics and export fields.
- [ ] Document known limitations for Track A.
- [ ] Add quick-start usage examples for analysts.

Done when:
- [ ] Handoff docs allow independent usage by research users.

### M6-T10 - M6 QA report and final signoff
Checklist:
- [ ] Create `eval/reports/m6_report.md`.
- [ ] Include API/UI/export verification results.
- [ ] Include final open issues list.

Done when:
- [ ] Final milestone signoff is complete.
