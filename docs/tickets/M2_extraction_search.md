# Milestone M2 - Text Extraction + Search

Goal: extract text from protocol and attachment PDFs, build chunk corpus with citations, and ship keyword search over the corpus.

## Exit Gate

- [ ] Protocol and attachment PDFs are extracted to usable text.
- [ ] Page-aware citation map exists for extracted documents.
- [ ] Chunking pipeline persists citation-linked chunks.
- [ ] Search endpoint returns reliable hits for known phrases.
- [ ] Extraction quality and search quality reports are published.

## Ticket Board

| ID | Title | Priority | Depends On |
|---|---|---|---|
| M2-T01 | PDF extraction engine with page boundaries | High | M1-T10 |
| M2-T02 | Extraction quality scoring and flags | High | M2-T01 |
| M2-T03 | Attachment extraction path (same PDF pipeline) | High | M2-T01 |
| M2-T04 | Citation-map serializer schema | High | M2-T01 |
| M2-T05 | Chunking strategy and chunk persistence | High | M2-T04 |
| M2-T06 | Search indexes (FTS + trigram) | High | M2-T05 |
| M2-T07 | Search API and ranking policy | High | M2-T06 |
| M2-T08 | Source-type filters in search (`protocol`/`attachment`) | Medium | M2-T07 |
| M2-T09 | Search quality eval set and tests | High | M2-T08 |
| M2-T10 | M2 QA report and signoff | High | M2-T09 |

---

## Tickets

### M2-T01 - PDF extraction engine with page boundaries
Checklist:
- [ ] Implement robust PDF text extraction.
- [ ] Preserve page boundaries in extracted representation.
- [ ] Persist extraction metadata (parser used, errors, warnings).
- [ ] Store extraction output tied to `document_version`.

Done when:
- [ ] Sample protocol PDFs produce clean text with page segmentation.

### M2-T02 - Extraction quality scoring and flags
Checklist:
- [ ] Compute text-layer coverage score.
- [ ] Detect likely poor extraction (too short, too noisy, missing pages).
- [ ] Mark documents for optional OCR fallback in later phase.
- [ ] Emit extraction quality summary report.

Done when:
- [ ] Poor-quality docs are consistently flagged for review.

### M2-T03 - Attachment extraction path (same PDF pipeline)
Checklist:
- [ ] Run attachment PDFs through same extraction pipeline.
- [ ] Preserve parent meeting linkage.
- [ ] Verify extraction coverage for attachments separately from protocols.

Done when:
- [ ] Attachment extraction coverage is reported and acceptable.

### M2-T04 - Citation-map serializer schema
Checklist:
- [ ] Define citation map structure (offset -> page reference).
- [ ] Persist citation map in metadata JSON or normalized table.
- [ ] Add helper functions to resolve citation for any chunk span.

Done when:
- [ ] Every chunk can resolve to document and page references.

### M2-T05 - Chunking strategy and chunk persistence
Checklist:
- [ ] Chunk by headings when possible, fallback to fixed windows.
- [ ] Include overlap to preserve context continuity.
- [ ] Store source metadata (`docver_id`, page refs, source kind).
- [ ] Ensure chunk IDs are deterministic on rerun.

Done when:
- [ ] Chunk corpus persists without duplication on rerun.

### M2-T06 - Search indexes (FTS + trigram)
Checklist:
- [ ] Build GIN FTS index on chunk text vectors.
- [ ] Build trigram index for fuzzy term matching.
- [ ] Verify Hebrew query behavior and tokenization assumptions.

Done when:
- [ ] Index-backed queries return in acceptable latency on sample corpus.

### M2-T07 - Search API and ranking policy
Checklist:
- [ ] Implement `GET /search` endpoint.
- [ ] Add hybrid ranking signal (FTS rank + trigram score).
- [ ] Return citation-ready search snippets.
- [ ] Return source type and meeting linkage in each result.

Done when:
- [ ] Search API returns stable, citable results.

### M2-T08 - Source-type filters in search
Checklist:
- [ ] Add filter options for `protocol` vs `attachment`.
- [ ] Add optional year/topic filters aligned with taxonomy.
- [ ] Ensure filter combinations are test-covered.

Done when:
- [ ] Users can isolate attachment evidence when needed.

### M2-T09 - Search quality eval set and tests
Checklist:
- [x] Build query set from known protocol and attachment phrases.
- [x] Define hit criteria (`top_k` contains expected target).
- [x] Add automated regression tests.

Done when:
- [x] Search hit-rate meets agreed threshold.

### M2-T10 - M2 QA report and signoff
Checklist:
- [x] Create `eval/reports/m2_report.md`.
- [x] Publish extraction coverage metrics.
- [x] Publish search hit-rate and latency summaries.
- [x] List known issues and deferrals.

Done when:
- [x] M2 signoff completed and M3 can start.
