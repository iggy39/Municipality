# M1 QA Report - Discovery + Versioned Raw Archive

Date: 2026-02-27
Scope: M1 tickets M1-T01 through M1-T10

## Exit Gate Status

- PASS: Recursive discovery handles variable width/depth and parent-child depth capture.
- PASS: Deterministic external IDs are stable (`parentMediaID` preferred, URL-hash fallback).
- PASS: Protocol and attachment PDFs are fetched, hashed, and persisted as document versions.
- PASS: `document_version` hash dedupe is enforced with unique constraint.
- PASS: Crawl/process runs are tracked with step statuses and error reasons.
- PASS: Rerun idempotency verified; rerun creates zero new versions for unchanged files.

## Evidence

- URL normalization + deterministic ID tests: `tests/unit/test_discovery.py`
- Migration apply/reapply test: `tests/unit/test_migrations.py`
- End-to-end rerun idempotency test (2025-2026 scoped sample): `tests/integration/test_m1_pipeline.py`
- SQL migration artifact: `migrations/001_m1_foundation.sql`

## Known Defects and Deferred Items

- Deferred: `POST /process/run` remains an M1 no-op placeholder by design.
- Deferred: No robots/rate-limit governance in MVP, per locked product decisions.
- Deferred: Real Ashdod adapter selectors may need tuning against live CMS markup changes.

## Rerun Idempotency Note

Running the integration scenario twice against the same dataset keeps `document` stable and does not insert duplicate `document_version` rows for unchanged hashes. Existing documents only update `last_seen_at`.
