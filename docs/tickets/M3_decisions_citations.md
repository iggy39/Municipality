# Milestone M3 - Decision Extraction + Citation-First UX

Goal: parse decisions from protocols, attach verifiable citations, and expose decision-centric API/UI.

Scope lock for M3:
- Council meetings first (`ישיבות מועצה` / city council protocols).
- Deterministic parser is primary.
- Fallback model is allowed only for low-confidence cases.

Fallback model policy (mandatory):
- Provider: Bytez
- Model: `dicta-il/DictaLM-3.0-24B-Thinking`
- API key source: `BYTEZ_API_KEY` environment variable only (never hardcoded in repo)
- Every fallback invocation must be recorded in decision metadata.
- Fallback metadata is public and must be included in API responses.

Quality gates (mandatory):
- Decision extraction precision >= 0.95
- Decision extraction recall >= 0.95
- Vote extraction precision >= 0.95 (where vote evidence exists)
- Vote extraction recall >= 0.95 (where vote evidence exists)
- Citation coverage for public decisions = 100%

## Exit Gate

- [ ] Council meeting metadata extraction is reliable for sampled corpus.
- [ ] Decisions are parsed and persisted with citation anchors.
- [ ] Votes are parsed when present, and uncertain votes are flagged.
- [ ] Fallback model usage is traceable in metadata and exposed in API.
- [ ] Meeting and decision APIs return citable evidence and fallback metadata.
- [ ] Decision card UI displays source-grounded evidence in Hebrew-first RTL UX.
- [ ] Decision extraction quality report is published with precision/recall and coverage.

## Ticket Board

| ID | Title | Priority | Depends On |
|---|---|---|---|
| M3-T00 | M3 schema + ORM bootstrap | High | M2-T10 |
| M3-T01 | Meeting metadata parser | High | M3-T00 |
| M3-T02 | Decision section locator | High | M3-T01 |
| M3-T03 | Decision row parser and normalizer | High | M3-T02 |
| M3-T04 | Vote parser and linkage | High | M3-T03 |
| M3-T05 | Decision-to-citation anchoring | High | M3-T03 |
| M3-T06 | Fallback adjudication + metadata audit | High | M3-T04, M3-T05 |
| M3-T07 | Meeting/decision document linkage model | High | M3-T05 |
| M3-T08 | Meeting and decision API handlers | High | M3-T06, M3-T07 |
| M3-T09 | Decision-card UI with evidence panel (RTL) | High | M3-T08 |
| M3-T10 | M3 eval harness + QA report + signoff | High | M3-T09 |

---

## Tickets

### M3-T00 - M3 schema + ORM bootstrap
Checklist:
- [ ] Add migration `migrations/003_m3_decisions_citations.sql`.
- [ ] Add tables: `meeting`, `decision`, `vote`, `decision_citation`, `meeting_document_link`, `decision_document_link`.
- [ ] Add required indexes/uniques for meeting lookup and decision retrieval.
- [ ] Add metadata fields (`metadata_json`) needed for fallback provenance and audit.
- [ ] Add ORM model classes in `src/municipality/models.py`.

Done when:
- [ ] Migration applies on clean DB and re-apply is idempotent.
- [ ] ORM models match migration and pass migration/integration tests.

### M3-T01 - Meeting metadata parser
Checklist:
- [ ] Extract meeting date, committee name, meeting code (best effort) from taxonomy + protocol header.
- [ ] Normalize extracted fields into canonical schema.
- [ ] Handle missing fields with null-safe defaults and parse confidence metadata.
- [ ] Keep council-first parsing rules for meeting title formats (`רגילה`, `מיוחדת`, etc.).

Done when:
- [ ] Sample set shows consistent council meeting metadata extraction behavior.

### M3-T02 - Decision section locator
Checklist:
- [ ] Detect likely decision sections/tables in protocol text.
- [ ] Support multiple council layout patterns across years.
- [ ] Preserve source span references for downstream citation anchors.
- [ ] Detect table headers like `נושא ההחלטה`, `תוכן ההחלטה`, `תחילת תוקף` robustly.

Done when:
- [ ] Decision sections are detected on representative council protocol sample.

### M3-T03 - Decision row parser and normalizer
Checklist:
- [ ] Parse each decision row into structured `decision_text`.
- [ ] Normalize numbering and duplicate markers.
- [ ] Attach fallback synthetic agenda item when agenda structure is missing.
- [ ] Assign parser confidence per decision row.
- [ ] Deduplicate coherent duplicates by normalized decision signature.

Done when:
- [ ] Parsed decisions are coherent, deduplicated, and confidence-scored.

### M3-T04 - Vote parser and linkage
Checklist:
- [ ] Detect vote patterns (counts, unanimous phrasing, mixed forms).
- [ ] Persist vote records linked to decision IDs.
- [ ] Flag uncertain vote extraction rather than guessing.
- [ ] Support examples such as `בעד/נגד/נמנע` and `פה אחד`.

Done when:
- [ ] Vote extraction is present where source contains vote evidence.

### M3-T05 - Decision-to-citation anchoring
Checklist:
- [ ] Map each decision to source page/offset references.
- [ ] Persist anchor metadata required for UI deep-linking.
- [ ] Ensure every public decision has at least one citation.
- [ ] Block uncited decisions from public output.

Done when:
- [ ] Decision without citations is blocked from public API output.

### M3-T06 - Fallback adjudication + metadata audit
Checklist:
- [ ] Add Bytez fallback client wrapper using `BYTEZ_API_KEY` env var.
- [ ] Use `dicta-il/DictaLM-3.0-24B-Thinking` only for low-confidence rows.
- [ ] Define strict structured-output contract (JSON) for fallback extraction.
- [ ] Validate fallback output against source spans before accepting.
- [ ] Persist fallback metadata fields in `decision.metadata_json`:
  - [ ] `fallback_used`
  - [ ] `fallback_reason`
  - [ ] `fallback_provider`
  - [ ] `fallback_model`
  - [ ] `fallback_invoked_at`
  - [ ] `fallback_validation_status`
  - [ ] `fallback_validation_reasons`
- [ ] Keep deterministic parser output when fallback validation fails.

Done when:
- [ ] Fallback path is covered by tests and metadata is always populated correctly.

### M3-T07 - Meeting/decision document linkage model
Checklist:
- [ ] Link protocol and attachment documents to meeting.
- [ ] Link attachments relevant to decisions when inferable.
- [ ] Keep linkage provenance fields for auditability (`direct`, `descendant`, `heuristic`).

Done when:
- [ ] Decision card can enumerate related source documents with provenance.

### M3-T08 - Meeting and decision API handlers
Checklist:
- [ ] Implement `GET /meeting/{meeting_id}` with decisions/docs/summary placeholders.
- [ ] Implement `GET /decision/{decision_id}` with citations, linked docs, votes.
- [ ] Ensure API response includes source-type markers.
- [ ] Expose fallback metadata publicly in decision API payload.
- [ ] Ensure public APIs do not return uncited decisions.

Done when:
- [ ] API responses are sufficient for citation-first UI rendering and metadata transparency.

### M3-T09 - Decision-card UI with evidence panel (RTL)
Checklist:
- [ ] Render decision text and vote.
- [ ] Render citation links to source page anchors.
- [ ] Render attachment links in evidence panel.
- [ ] Keep Hebrew-first RTL UX and clear evidence labels.
- [ ] Show fallback usage badge/label when `fallback_used=true`.

Done when:
- [ ] User can inspect decision and verify evidence quickly in a minimal RTL page.

### M3-T10 - M3 eval harness + QA report + signoff
Checklist:
- [ ] Build labeled set of council decision samples.
- [ ] Compute decision precision/recall metrics.
- [ ] Compute vote precision/recall metrics where vote evidence exists.
- [ ] Compute citation coverage and citation correctness checks.
- [ ] Add regression checks for parser/fallback changes.
- [ ] Create `eval/reports/m3_report.md`.
- [ ] Include extraction metrics, citation coverage, and fallback usage statistics.
- [ ] Include known parser edge cases and backlog notes.
- [ ] Include step-by-step real-life examples from sampled council protocols.

Done when:
- [ ] Decision precision >= 0.95 and recall >= 0.95.
- [ ] Vote precision >= 0.95 and recall >= 0.95 (where evidence exists).
- [ ] Citation coverage for public decisions is 100%.
- [ ] M3 signoff completed and M4 can start.

---

## Public API metadata contract (M3 mandatory)

For each decision response, include a public metadata object:
- `fallback_used: boolean`
- `fallback_reason: string | null`
- `fallback_provider: string | null`
- `fallback_model: string | null`
- `fallback_invoked_at: string | null`
- `fallback_validation_status: "accepted" | "rejected" | null`
- `fallback_validation_reasons: string[]`
