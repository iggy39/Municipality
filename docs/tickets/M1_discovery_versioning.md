# Milestone M1 - Discovery + Versioned Raw Archive

Goal: recursively discover Ashdod hierarchy (dynamic width/depth), ingest 2025-2026 council scope, and store versioned raw PDF assets with idempotent reruns.

## Exit Gate

- [x] Recursive discovery works for topic/year/meeting/subfolder patterns.
- [x] Deterministic external IDs are stable across reruns.
- [x] Protocol and attachment PDFs are fetched and hashed.
- [x] `document_version` dedupe by hash is enforced.
- [x] Crawl/process runs are traceable with statuses and errors.
- [x] One rerun proves no duplicate versions are created.

## Ticket Board

| ID | Title | Priority | Depends On |
|---|---|---|---|
| M1-T01 | Schema foundation for discovery/versioning | High | - |
| M1-T02 | URL normalization and external ID resolver | High | M1-T01 |
| M1-T03 | Recursive Ashdod discovery engine | High | M1-T02 |
| M1-T04 | Asset classifier and manifest writer | High | M1-T03 |
| M1-T05 | Fetcher with retry/error taxonomy | High | M1-T04 |
| M1-T06 | Versioning and hash dedupe persistence | High | M1-T05 |
| M1-T07 | Local storage URI policy and writer | Medium | M1-T05 |
| M1-T08 | Admin run endpoints and run status API | Medium | M1-T06 |
| M1-T09 | Integration test on 2025-2026 sample | High | M1-T08 |
| M1-T10 | M1 QA report and milestone signoff | High | M1-T09 |

---

## Tickets

### M1-T01 - Schema foundation for discovery/versioning
Checklist:
- [x] Add/verify tables for `source_site`, `taxonomy_node`, `document`, `document_version`, `pipeline_run`, `pipeline_run_step`.
- [x] Add uniqueness constraints for stable IDs and hash dedupe.
- [x] Add indexes for rerun performance (`node_external_id`, `canonical_url`, hash, run IDs).
- [x] Add migration files and migration tests.

Done when:
- [x] Fresh database migration succeeds.
- [x] Reapply migration path is clean.

### M1-T02 - URL normalization and external ID resolver
Checklist:
- [x] Implement canonical URL normalization (remove tracking params, resolve relatives).
- [x] Parse `parentMediaID` from URLs when present.
- [x] Implement fallback URL-hash external ID.
- [x] Add deterministic ID tests with fixture URLs.

Done when:
- [x] Same input URL variants produce one stable canonical ID.

### M1-T03 - Recursive Ashdod discovery engine
Checklist:
- [x] Implement depth-agnostic crawler with visited-set loop prevention.
- [x] Discover folder-like nodes and leaf assets from source pages.
- [x] Capture parent-child relationships and depth.
- [x] Limit scope to council meetings in 2025-2026 for first ingest run.

Done when:
- [x] Discovery output reconstructs expected hierarchy for sampled paths.

### M1-T04 - Asset classifier and manifest writer
Checklist:
- [x] Classify assets into `protocol_short`, `protocol_full`, `attachment`, `audio`, `video`, `other`.
- [x] Filter ingest candidates to PDF assets only.
- [x] Persist non-PDF rows as metadata with `UNSUPPORTED_MIME` for extraction skip.
- [x] Emit deterministic manifest rows linked to source nodes.

Done when:
- [x] Manifest contains all expected PDF assets from sampled meetings.

### M1-T05 - Fetcher with retry/error taxonomy
Checklist:
- [x] Implement fetch with timeout, retries, and exponential backoff.
- [x] Log status code, bytes, mime, and final failure reason.
- [x] Support resumable pipeline run behavior after partial failures.
- [x] Record fetch metadata in run-step logs.

Done when:
- [x] Failed URLs are visible in run report with actionable reason codes.

### M1-T06 - Versioning and hash dedupe persistence
Checklist:
- [x] Compute sha256 for every fetched PDF.
- [x] Insert `document` if canonical URL unseen.
- [x] Insert `document_version` only when hash is new for document.
- [x] Update `last_seen_at` for repeated assets.

Done when:
- [x] Rerun on same sample produces zero new versions.

### M1-T07 - Local storage URI policy and writer
Checklist:
- [x] Implement raw storage path format (`{muni}/{doc_kind}/{hash}.bin`).
- [x] Ensure atomic write and safe overwrite behavior.
- [x] Store storage URI in `document_version` metadata.

Done when:
- [x] Stored files are retrievable by URI and hash-verified.

### M1-T08 - Admin run endpoints and run status API
Checklist:
- [x] Add crawl run trigger endpoint.
- [x] Add process run trigger endpoint (can be no-op placeholder for M1).
- [x] Add run status endpoint with step-level statuses.
- [x] Add health endpoint.

Done when:
- [x] A full M1 run can be triggered and monitored through API.

### M1-T09 - Integration test on 2025-2026 sample
Checklist:
- [x] Execute end-to-end discovery + fetch + version for sampled meetings.
- [x] Validate protocol + attachments presence.
- [x] Validate zero duplicate versions on rerun.
- [x] Capture runtime, error counts, and item counts.

Done when:
- [x] Integration test is repeatable and documented.

### M1-T10 - M1 QA report and milestone signoff
Checklist:
- [x] Create report under `eval/reports/m1_report.md`.
- [x] Include pass/fail against exit gate.
- [x] Include known issues and deferred items.
- [x] Include rerun idempotency evidence.

Done when:
- [x] M1 is signed off and M2 can start.
