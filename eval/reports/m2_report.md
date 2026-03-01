# M2 QA Report - Extraction + Search

Date: 2026-02-28
Scope: M2 tickets M2-T01 through M2-T10

## Exit Gate Status

- PASS: Protocol and attachment PDF extraction path implemented via `pdftotext` with page segmentation.
- PASS: Citation map is persisted per extracted document and page-aware chunk spans resolve to page refs.
- PASS: Chunking pipeline persists deterministic chunk IDs and rerun-safe replacement behavior.
- PASS: Search endpoint (`GET /search`) returns ranked hits with snippet + citation metadata.
- PASS: Extraction quality and search quality reports are published with regression harness results.

## Extraction Coverage Metrics

Dataset: deterministic M2 eval fixture corpus used by `tests/integration/test_m2_search_eval.py`.

- Protocol coverage: 1/1 extracted (100%).
- Attachment coverage: 1/1 extracted (100%).
- Combined PDF coverage: 2/2 extracted (100%).
- Quality flags observed: low-severity `MANY_EMPTY_PAGES` on sparse layouts; no parser hard-fail in eval fixture.

## Search Quality Summary

Query set source: `eval/gold/m2_search_eval_set.json` (known phrases from protocol + attachment text).
Hit criterion: expected target appears in `top_k` for each query.

- Queries evaluated: 4
- Hits in `top_k`: 4/4 (hit-rate 100%)
- Average latency: 4.78 ms/query
- p95 latency: 6.998 ms/query
- Max latency: 6.998 ms/query

## Evidence

- Extraction implementation: `src/municipality/extraction.py`
- Chunking implementation: `src/municipality/chunking.py`
- Process pipeline implementation: `src/municipality/processing.py`
- Search service + ranking: `src/municipality/search.py`
- Search eval harness utilities: `src/municipality/eval_search.py`
- API endpoints: `src/municipality/api.py`
- Schema migration: `migrations/002_m2_extraction_search.sql`
- Integration test: `tests/integration/test_m2_processing_search.py`
- Search eval regression test: `tests/integration/test_m2_search_eval.py`
- Eval query set: `eval/gold/m2_search_eval_set.json`
- Unit extraction test: `tests/unit/test_extraction.py`

## Known Defects and Deferred Items

- Deferred: OCR fallback execution remains intentionally deferred; low-quality extraction is flagged (`OCR_CANDIDATE`).
- Deferred: Topic/year filters currently rely on document metadata heuristics and not a normalized meeting/topic model.
- Note: SQLite implementation uses FTS5 + explicit trigram table; PostgreSQL GIN/pg_trgm indexes remain a deployment-time adaptation.

## Rerun Idempotency Note

Process reruns replace chunks by `document_version_id`, then reinsert deterministic `chunk_id` values and refresh FTS/trigram rows, preventing duplication while preserving stable references.

## Signoff

M2 is signed off for MVP progression. M3 can start with decision parsing and citation-first APIs/UI on top of the verified extraction + search substrate.
