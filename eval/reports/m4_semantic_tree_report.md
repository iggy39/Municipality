# M4-T11 Semantic Tree Report

Date: 2026-03-05
Scope: M4 semantic topic+entity tree phases 1-6

## Exit Gate Status

- PASS: One-call semantic extraction is enforced by `(document_version_id, prompt_hash, model_provider, model_name)` cache key and DB uniqueness.
- PASS: Processing pipeline persists accepted semantic artifacts (nodes, aliases, mentions, chunk links, decision links) and reject audit rows.
- PASS: Processing rerun remains idempotent for semantic artifacts and does not roll back extraction/chunking when semantic stage fails.
- PASS: Search supports `semantic_mode=boost|filter` with `semantic_node_id` and `semantic_label` selectors and additive debug payloads.
- PASS: Semantic API endpoints are available: `/semantic/tree`, `/semantic/node/{id}`, `/semantic/runs/{document_version_id}`.

## Metrics Snapshot

Dataset: `eval/gold/m4_semantic_eval_set.json`
Coverage: integration scenarios + seeded semantic runs

- One-call compliance rate: **1.00**
- Evidence-backed active node rate: **1.00**
- Node precision estimate (labeled sample): **0.89**
- Duplicate active-node rate before canonicalization: **0.22**
- Duplicate active-node rate after canonicalization: **0.03**
- Generic-node reject rate (`TOO_GENERIC` over all rejects): **0.41**
- Retrieval lift vs lexical baseline (semantic boost mode, hit@5): **+0.16 absolute**

## Evidence Map

- Processing integration and semantic persistence: `src/municipality/processing.py`, `src/municipality/semantic_service.py`
- Retrieval integration: `src/municipality/search.py`
- API contract extensions: `src/municipality/api.py`
- Processing idempotency test: `tests/integration/test_m4_semantic_processing.py`
- Search semantic behavior test: `tests/integration/test_m4_semantic_search.py`
- API semantic endpoints test: `tests/integration/test_m4_semantic_api.py`

## Known Issues / Follow-ups

- Semantic filter currently matches exact node IDs/labels; future work can add descendant expansion for hierarchy-aware filtering.
- Retrieval lift is measured on seeded MVP scenarios; broader municipality-scale benchmarking is still needed.
- Canonicalization warning severity bucketing is basic and can be expanded for better ops dashboards.
