# Milestone M3 - Decision Extraction + Citation-First UX

Goal: parse decisions from protocols, attach verifiable citations, and expose decision-centric API/UI.

## Exit Gate

- [ ] Meeting metadata extraction is reliable for sampled corpus.
- [ ] Decisions are parsed and persisted with citation anchors.
- [ ] Votes are parsed when present.
- [ ] Meeting and decision APIs return citable evidence.
- [ ] Decision card UI displays source-grounded evidence.
- [ ] Decision extraction quality report is published.

## Ticket Board

| ID | Title | Priority | Depends On |
|---|---|---|---|
| M3-T01 | Meeting metadata parser | High | M2-T10 |
| M3-T02 | Decision section locator | High | M3-T01 |
| M3-T03 | Decision row parser and normalizer | High | M3-T02 |
| M3-T04 | Vote parser and linkage | Medium | M3-T03 |
| M3-T05 | Decision-to-citation anchoring | High | M3-T03 |
| M3-T06 | Meeting/decision document linkage model | High | M3-T05 |
| M3-T07 | Meeting and decision API handlers | High | M3-T06 |
| M3-T08 | Decision-card UI with evidence panel | High | M3-T07 |
| M3-T09 | Decision extraction eval harness | High | M3-T08 |
| M3-T10 | M3 QA report and signoff | High | M3-T09 |

---

## Tickets

### M3-T01 - Meeting metadata parser
Checklist:
- [ ] Extract meeting date, committee name, meeting code (best effort).
- [ ] Normalize extracted fields into canonical schema.
- [ ] Handle missing fields gracefully with null-safe defaults.

Done when:
- [ ] Sample set shows consistent metadata extraction behavior.

### M3-T02 - Decision section locator
Checklist:
- [ ] Detect likely decision sections/tables in protocol text.
- [ ] Support multiple layout patterns across years.
- [ ] Preserve source span references for downstream citation anchors.

Done when:
- [ ] Decision sections are detected on representative protocol sample.

### M3-T03 - Decision row parser and normalizer
Checklist:
- [ ] Parse each decision row into structured `decision_text`.
- [ ] Normalize numbering and duplicate markers.
- [ ] Attach fallback synthetic agenda item when agenda structure missing.

Done when:
- [ ] Parsed decisions are coherent and deduplicated.

### M3-T04 - Vote parser and linkage
Checklist:
- [ ] Detect vote patterns (counts, unanimous phrasing, etc.).
- [ ] Persist vote records linked to decision IDs.
- [ ] Flag uncertain vote extraction rather than guessing.

Done when:
- [ ] Vote extraction is present where source contains vote evidence.

### M3-T05 - Decision-to-citation anchoring
Checklist:
- [ ] Map each decision to source page/offset references.
- [ ] Persist anchor metadata required for UI deep-linking.
- [ ] Ensure every public decision has at least one citation.

Done when:
- [ ] Decision without citations is blocked from public output.

### M3-T06 - Meeting/decision document linkage model
Checklist:
- [ ] Link protocol and attachment documents to meeting.
- [ ] Link attachments relevant to decisions when inferable.
- [ ] Keep linkage provenance fields for auditability.

Done when:
- [ ] Decision card can enumerate related source documents.

### M3-T07 - Meeting and decision API handlers
Checklist:
- [ ] Implement `GET /meeting/{meeting_id}` with decisions/docs/summary placeholders.
- [ ] Implement `GET /decision/{decision_id}` with citations and linked docs.
- [ ] Ensure API response includes source-type markers.

Done when:
- [ ] API responses are sufficient for citation-first UI rendering.

### M3-T08 - Decision-card UI with evidence panel
Checklist:
- [ ] Render decision text and vote.
- [ ] Render citation links to source page anchors.
- [ ] Render attachment links in evidence panel.
- [ ] Keep Hebrew-first UX and clear evidence labels.

Done when:
- [ ] User can inspect decision and verify evidence quickly.

### M3-T09 - Decision extraction eval harness
Checklist:
- [ ] Build labeled set of decision samples.
- [ ] Compute precision/recall metrics.
- [ ] Add regression checks for parser changes.

Done when:
- [ ] Metrics pass defined threshold.

### M3-T10 - M3 QA report and signoff
Checklist:
- [ ] Create `eval/reports/m3_report.md`.
- [ ] Include extraction metrics and citation coverage.
- [ ] Include known parser edge cases and backlog notes.

Done when:
- [ ] M3 signoff completed and M4 can start.
