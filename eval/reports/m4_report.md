# M4 QA Report - RAG + Semantic Retrieval Foundation

Date: 2026-03-10
Scope: M4 semantic quality gate artifacts (M4-T11 completion support)

## Exit Gate Snapshot

- PASS: One-call semantic extraction guard remains enforced by run-call unique key and cache reuse.
- PASS: Accepted active semantic nodes are evidence-backed through persisted mention spans.
- PASS: Retrieval supports `semantic_mode=boost|filter|off` for semantic usage and lexical-only baseline checks.
- PASS: Semantic eval harness is published and wired to integration tests.

## Semantic Eval Harness

- Harness: `src/municipality/eval_semantic.py`
- Gold set: `eval/gold/m4_semantic_eval_set.json`
- Regression test: `tests/integration/test_m4_semantic_eval.py`

Published metrics from deterministic integration fixture:

- Semantic hit rate: 1.00
- Lexical-only hit rate: 1.00
- Retrieval lift vs lexical baseline: 0.00
- One-call compliance rate: 1.00
- Evidence-backed active node rate: 1.00
- Duplicate active-node rate: 0.00
- Negative reject hit rate: 1.00

## Retrieval Debugging Contract

- `GET /search` adds semantic diagnostics (`semantic_match_count`, `semantic_boost`, optional semantic node debug payloads).
- `GET /semantic/node/{id}` exposes aliases, linked chunks/decisions, and mention evidence.
- `GET /semantic/runs/{document_version_id}` exposes run-level compliance and reject histogram details.

## Known Limitations

- Current retrieval lift metric is measured on seeded integration fixture scenarios; municipality-scale production benchmark is still pending.
- `semantic_mode=off` is provided for controlled lexical baseline evaluation and tuning, not for end-user defaults.
- Hierarchy descendant expansion for semantic filters is not implemented yet.

## Signoff Note

M4 semantic quality gates are now measurable and regression-tested. Remaining M4 signoff should combine this semantic gate with final citation/refusal QA artifacts for complete milestone closure.
