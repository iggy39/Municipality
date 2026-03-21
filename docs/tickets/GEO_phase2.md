# Geo PoC Tickets - Phase 2 (Hebrew Normalization + Geo Enrichment)

Goal: add deterministic Hebrew geo normalization and geo mention enrichment while keeping map rendering in `/ui/ask` only.

Prerequisite:
- Complete all Phase 1 tickets in `docs/tickets/GEO_phase1.md`.

Scope lock:
- Keep geometry source in `chunk_geo_feature` (Phase 1 path).
- Use official locality/street/synonym datasets for normalization.
- Add deterministic extraction in processing pipeline (`geo_enrichment`).
- Do not add overlays, map-first search, or external GIS services.

## Exit Gate

- [ ] Reference tables for locality/street/alias/mention exist and are idempotent.
- [ ] Official dataset imports are deterministic and rerunnable.
- [ ] Hebrew normalization handles common variants and abbreviations.
- [ ] Processing writes `geo_mention` evidence in a deterministic step.
- [ ] Ask geo labels are enriched from high-confidence normalized mentions.
- [ ] Phase 2 unit/integration tests pass with no regressions.

## Ticket Board

| ID | Title | Priority | Estimate | Depends On |
|---|---|---|---|---|
| GEO-P2-01 | Reference dataset schema (`locality/street/alias/mention`) | High | 1.0d | GEO-P1-07 |
| GEO-P2-02 | Official dataset import scripts | High | 1.5d | GEO-P2-01 |
| GEO-P2-03 | Hebrew geo normalization service | High | 1.5d | GEO-P2-02 |
| GEO-P2-04 | Deterministic geo mention extraction | High | 1.5d | GEO-P2-03 |
| GEO-P2-05 | Processing pipeline `geo_enrichment` step | High | 2.0d | GEO-P2-04 |
| GEO-P2-06 | Ask geo label enrichment from normalized mentions | Medium | 1.0d | GEO-P2-05 |
| GEO-P2-07 | Phase 2 tests + rerun/idempotency checks | High | 1.5d | GEO-P2-06 |

---

## Tickets

### GEO-P2-01 - Reference dataset schema (`locality/street/alias/mention`)
Checklist:
- [ ] Create migration `migrations/007_m7_geo_reference.sql`.
- [ ] Add tables:
  - `geo_locality`,
  - `geo_street`,
  - `geo_street_alias`,
  - `geo_mention`.
- [ ] Add ORM models in `src/municipality/models.py`.
- [ ] Add indexes/unique constraints for code-based idempotent imports.
- [ ] Extend `tests/unit/test_migrations.py` for new tables.

Done when:
- [ ] Migration is idempotent and all four tables are available.

### GEO-P2-02 - Official dataset import scripts
Checklist:
- [ ] Add scripts:
  - `scripts/import_geo_localities.py`,
  - `scripts/import_geo_streets.py`,
  - `scripts/import_geo_street_aliases.py`.
- [ ] Add deterministic upsert semantics using official codes.
- [ ] Normalize basic whitespace/bidi artifacts during import.
- [ ] Add minimal import summary output: inserted/updated/skipped/errors.

Done when:
- [ ] Re-running imports does not duplicate records.
- [ ] Reference tables are queryable for normalization lookup.

### GEO-P2-03 - Hebrew geo normalization service
Checklist:
- [ ] Add `src/municipality/geo_normalization.py`.
- [ ] Normalize:
  - bidi/control characters,
  - punctuation/spacing,
  - abbreviations (`רח'`, `רח.`, `רח` -> `רחוב`).
- [ ] Resolve locality/street aliases against reference tables.
- [ ] Emit confidence score and canonical codes.
- [ ] Define confidence policy:
  - high `>=0.85`, medium `0.60..0.84`, low `<0.60`.

Done when:
- [ ] Variant Hebrew street/locality strings map to canonical forms consistently.

### GEO-P2-04 - Deterministic geo mention extraction
Checklist:
- [ ] Add `src/municipality/geo_extraction.py`.
- [ ] Implement rule extraction priority:
  1. `plan_id`,
  2. `gush`,
  3. `helka`,
  4. locality,
  5. street.
- [ ] Attach offsets/pages using extracted text + citation map.
- [ ] Associate mention to best overlapping chunk when possible.
- [ ] Persist extraction method and confidence in mention records.

Done when:
- [ ] Mention extraction is deterministic and testable without LLM dependency.

### GEO-P2-05 - Processing pipeline `geo_enrichment` step
Checklist:
- [ ] Update `src/municipality/processing.py` to add `PipelineRunStep` named `geo_enrichment`.
- [ ] Run geo enrichment after chunk indexing and before semantic enrichment.
- [ ] Persist `geo_mention` rows for document version.
- [ ] Ensure reruns replace/update mention rows idempotently.
- [ ] Keep extraction/chunk indexing successful even if geo step fails.

Done when:
- [ ] Processing run logs `geo_enrichment` with clear status/detail.
- [ ] Geo step is idempotent across reruns.

### GEO-P2-06 - Ask geo label enrichment from normalized mentions
Checklist:
- [ ] Extend `src/municipality/geo_service.py` to merge high-confidence normalized labels.
- [ ] Keep geometry source from `chunk_geo_feature` unchanged.
- [ ] Optionally add `normalization` object on each feature payload.
- [ ] Do not create map features from mention-only rows without geometry.
- [ ] Mark medium confidence labels as approximate where surfaced.

Done when:
- [ ] UI labels are cleaner/consistent for Hebrew variants.
- [ ] No geometry is auto-invented from low-confidence evidence.

### GEO-P2-07 - Phase 2 tests + rerun/idempotency checks
Checklist:
- [ ] Add `tests/unit/test_geo_normalization.py`.
- [ ] Add `tests/unit/test_geo_extraction.py`.
- [ ] Add/extend processing integration test for `geo_enrichment` step.
- [ ] Add/extend ask integration test for normalized geo labels.
- [ ] Verify idempotency and no-regression behavior.

Done when:
- [ ] Phase 2 unit/integration coverage is in place and green.
- [ ] Rerun behavior is deterministic.

---

## Execution Order (Strict)

1. GEO-P2-01
2. GEO-P2-02
3. GEO-P2-03
4. GEO-P2-04
5. GEO-P2-05
6. GEO-P2-06
7. GEO-P2-07

## Verification Commands

- `pytest tests/unit/test_migrations.py`
- `pytest tests/integration/test_m4_semantic_processing.py`
- `pytest tests/integration/test_m4_ask_api.py`
- `pytest tests/unit/test_geo_normalization.py tests/unit/test_geo_extraction.py`

## Out of Scope (Phase 2)

- Planning overlays/WMS (Phase 3).
- Structured geo search by plan/gush/helka (Phase 4).
- `/ui/decision/{decision_id}` map integration.
- Geocoder autocomplete and radius/polygon query UX.
