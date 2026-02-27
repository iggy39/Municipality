# Municipality Transparency + Urban Planning Intelligence
## Agent Implementation Plan (v2)

Status: Ready for implementation
Date: 2026-02-27
Pilot municipality: Ashdod
Primary source root:
`https://www.ashdod.muni.il/he-il/%d7%90%d7%aa%d7%a8-%d7%94%d7%a2%d7%99%d7%a8/%d7%a4%d7%a8%d7%95%d7%98%d7%95%d7%a7%d7%95%d7%9c%d7%99%d7%9d/%d7%a8%d7%a9%d7%99%d7%9e%d7%aa-%d7%a4%d7%a8%d7%95%d7%98%d7%95%d7%a7%d7%95%d7%9c%d7%99%d7%9d-%d7%9c%d7%a4%d7%99-%d7%a0%d7%95%d7%a9%d7%90/`

---

## 1) Locked Product Decisions

These decisions are final unless explicitly changed:

1. Initial ingest window for first execution: `2025-2026`.
2. UI language scope for MVP: Hebrew-only.
3. Deployment target for MVP: local environment only.
4. LLM layer: provider-agnostic abstraction, optimized for simple testing and eval.
5. Geo phase starts with no-key fallback (do not require GovMap/MAPI API keys for first implementation).
6. Crawler policy for MVP: do not block on robots/rate-limit constraints.
7. Attachments are in MVP scope and must be extracted/indexed.
8. Source reality note: currently protocols and attachments in this MVP source are PDF-only.

---

## 2) Product Goal and Constraints

### 2.1 Goal
Build a research-grade MVP that ingests municipality protocol documents and attachments, then serves:
- Decision cards with verifiable citations.
- Dynamic topic-tree navigation.
- Search over protocol and attachment text.
- RAG answers with strict citation grounding.
- Geo classification and conservative mapping (high-confidence only).
- Timeline track for decisions.

### 2.2 Hard constraints
- Never fabricate facts in generated outputs.
- Every major claim in RAG output must include citations.
- If retrieval evidence is weak, return refusal.
- If `geo_scope = NONE`, do not render map geometry.
- Preserve provenance for all derived artifacts.

---

## 3) Scope by Delivery Tier

### 3.1 MVP-0 (must ship first)
- Recursive Ashdod discovery and ingest for protocols and attachments.
- Fetch/version/hash storage pipeline.
- PDF text extraction with page-level citation anchors.
- Decision extraction (decisions-first parser).
- Keyword search (FTS + trigram).
- UI flow:
  - Topic tree
  - Meetings list
  - Meeting page
  - Decision card

### 3.2 MVP-1
- RAG endpoint and UI panel with:
  - citation-required answers,
  - refusal behavior for weak evidence.

### 3.3 MVP-2
- Geo Step 5 classification and mapping.
- Map view with confidence threshold.
- Timeline Track A (decision events).

### 3.4 Explicitly deferred
- Audio/video transcription.
- Multi-city adapter rollout.
- Predictive forecasting.
- Timeline Track B execution evidence.
- Human moderation workflow.

---

## 4) Source Discovery and Dynamic Taxonomy

### 4.1 Dynamic tree requirement
Topic navigation must be seeded from municipality taxonomy but stay dynamic in both:
- Tree width (any number of sibling nodes)
- Tree depth (no hardcoded level depth)

### 4.2 Discovery model
Treat site structure as a recursive graph of node types:
- topic folder
- year folder
- meeting folder
- generic subfolder
- asset leaf

Do not infer behavior from fixed level number. Infer from page content and links.

### 4.3 External identifiers
Use stable source-derived keys in this order:
1. `parentMediaID` from page URL/query.
2. Canonical normalized URL hash fallback.
3. Parent+title fallback only for conflict logging.

### 4.4 Canonical `taxonomy_node` output
Each discovered node must include:
- `node_external_id`
- `parent_external_id` (nullable)
- `title_he`
- `url`
- `node_type`
- `depth`
- `count_hint`
- `crawl_run_id`
- `discovered_at`

### 4.5 Canonical `asset_manifest` output
Each discovered asset must include:
- `asset_external_id` (canonical URL)
- `source_node_external_id`
- `asset_url`
- `asset_kind` (`protocol_short`, `protocol_full`, `attachment`, `audio`, `video`, `other`)
- `title_he`
- `mime_hint`
- `crawl_run_id`
- `discovered_at`

### 4.6 MVP format policy
- Current source is PDF-only for protocols and attachments.
- MVP extraction/indexing supports PDF.
- If a non-PDF asset appears:
  - keep it as metadata,
  - mark `UNSUPPORTED_MIME`,
  - skip text extraction until Phase-2 file-type plugins are added.

---

## 5) OCR and PDF Extraction Policy

### 5.1 Why OCR exists in architecture
OCR is only required for scanned/image-only pages with no text layer.

### 5.2 Locked MVP behavior
- Run normal PDF text extraction first.
- OCR is not mandatory in MVP-0.
- If extraction quality is poor, flag for optional OCR pass in later milestone.

### 5.3 Required extraction outputs
- Full extracted text.
- Page boundaries.
- Citation map linking text spans to page references.
- Extraction quality metadata (text-layer coverage, parser flags).

---

## 6) Recommended Tech Stack

### 6.1 Backend and data
- Python 3.11+
- FastAPI
- SQLAlchemy + Alembic
- PostgreSQL 15+
- PostGIS extension
- PostgreSQL FTS + `pg_trgm`

### 6.2 Pipeline and storage
- Python pipeline workers (same repo)
- Local filesystem object storage abstraction (S3-compatible interface)
- DB-backed run tracking for reproducibility

### 6.3 Frontend
- Next.js
- Hebrew-first RTL UI
- Map framework integrated in MVP-2

### 6.4 LLM abstraction
- Provider-agnostic client interface:
  - `generate_answer`
  - `summarize`
  - `extract_structured`
- Adapter plug-ins for providers.
- Default provider chosen by config, not hardcoded.
- Eval suite runs independent of specific provider implementation.

---

## 7) Data Model (Minimum Viable)

### 7.1 Core entities (MVP-0)
- `municipality`
- `source_site`
- `taxonomy_node`
- `meeting`
- `document`
- `document_version`
- `meeting_document_link`
- `decision`
- `vote`
- `text_chunk`
- `pipeline_run`
- `pipeline_run_step`

### 7.2 Geo entities (MVP-2)
- `geo_mention`
- `geo_link`
- `geo_summary`
- `geo_feature`
- `geo_layer`

### 7.3 Key constraints and indexes
- Unique `taxonomy_node(source_site_id, node_external_id)`
- Unique `document(canonical_url)`
- Unique `document_version(document_id, sha256)`
- Unique `meeting(source_site_id, meeting_external_id)`
- GIN index on `text_chunk.tsv`
- Trigram index on searchable text/title fields

---

## 8) Pipeline Modules

### Module A: Recursive discovery (`scrape_protocols`)
Input:
- root URL
- crawl config

Output:
- `taxonomy_node` upserts
- `asset_manifest` rows

Acceptance:
- Traverses dynamic width/depth correctly.
- Avoids loops with visited key-set.
- Re-run is idempotent.

### Module B: Fetch and version
Input:
- discovered assets

Output:
- raw files
- `document` and `document_version`
- hashes and fetch metadata

Acceptance:
- No duplicate versions for identical hash.
- Full error logs with failure reason codes.

### Module C: PDF text extraction
Input:
- `document_version` for PDFs

Output:
- extracted text
- page/citation map
- extraction quality metadata

Acceptance:
- >=90 percent usable extraction on sample batch.

### Module D: Meeting parser (decisions-first)
Input:
- extracted protocol text

Output:
- meeting metadata
- decisions list
- votes when present
- citation anchors per decision

Acceptance:
- Decision extraction precision/recall threshold reached on labeled sample.

### Module E: Attachment extraction and linking
Input:
- extracted attachment text

Output:
- chunked attachment text
- document links to meetings/decisions

Acceptance:
- Attachment chunks searchable and citable.

### Module F: Chunking and indexing
Input:
- extracted protocol and attachment text

Output:
- `text_chunk` rows
- FTS/trigram searchable corpus

Acceptance:
- Known phrases from decisions and attachments are retrievable.

### Module G: RAG with citations (MVP-1)
Input:
- user query

Output:
- answer with citations OR refusal

Acceptance:
- Citation correctness passes eval threshold.
- Refusal behavior passes insufficiency tests.

### Module H: Geo Step 5 (MVP-2)
Input:
- decisions and chunk mentions

Output:
- scope, mentions, links, confidence

Acceptance:
- High precision at confidence >=0.85.
- Very low false map rate on `NONE` scope.

### Module I: Timeline Track A (MVP-2)
Input:
- decision records

Output:
- event timeline data

Acceptance:
- Correct chronological rendering and export.

---

## 9) API Contracts

### 9.1 Public endpoints
- `GET /topics?muni=ashdod`
- `GET /meetings?muni=ashdod&topic_id=...&year=...`
- `GET /meeting/{meeting_id}`
- `GET /decision/{decision_id}`
- `GET /search?q=...&muni=ashdod`
- `POST /ask` (MVP-1)
- `GET /map/features?...` (MVP-2)
- `GET /timeline?...` (MVP-2)

### 9.2 Internal/admin endpoints
- `POST /crawl/run?muni=ashdod`
- `POST /process/run?doc_id=...`
- `GET /runs/{run_id}`
- `GET /health`

---

## 10) UI Contracts

### 10.1 MVP-0 screens
1. Topic tree (dynamic hierarchy)
2. Meetings list
3. Meeting page
4. Decision card

### 10.2 MVP-1 additions
5. Ask panel with citation-required answers and refusal messaging

### 10.3 MVP-2 additions
6. Map view with confidence filters and uncertain toggle
7. Timeline view (Track A)

---

## 11) Project Structure (Target)

```text
/apps
  /api
  /pipeline
  /web
/src
  /adapters
    /ashdod
  /domain
  /services
  /shared
/migrations
/eval
  /gold
  /reports
/storage
  /raw
  /parsed
  /derived
/tests
  /unit
  /integration
  /e2e
/docs
  AGENT_IMPLEMENTATION_PLAN.md
```

---

## 12) Implementation Order (Strict)

1. Schema, migrations, run-tracking foundation.
2. Recursive Ashdod discovery (`scrape_protocols`) and dynamic taxonomy ingest.
3. Fetch/version/hash pipeline with idempotent re-run behavior.
4. PDF extraction and citation map generation.
5. Decisions-first parser with validation on sampled meetings.
6. Attachment extraction/indexing and linkage.
7. API + MVP-0 UI core flow.
8. Search quality pass.
9. RAG integration (MVP-1).
10. Geo + map (MVP-2).
11. Timeline Track A (MVP-2).

---

## 13) Agent Work Breakdown

### Agent A: Data Foundation
- Own schema/migrations/indexes.
- Deliver migration test suite and DB bootstrap docs.

### Agent B: Adapter + Discovery
- Own recursive scraping and canonicalization.
- Deliver deterministic node/asset manifests.

### Agent C: Fetch + Extraction
- Own download/version/hash/storage and PDF extraction.
- Deliver extraction quality report.

### Agent D: Parsing + Indexing
- Own meeting parser, decisions extraction, chunking/search.
- Deliver parser benchmark report.

### Agent E: API + UI
- Own endpoint implementation and Hebrew UI screens.
- Deliver end-to-end core flow demo.

### Agent F: RAG + Geo
- Own citation-locked RAG, then geo scoring/map outputs.
- Deliver evaluation reports and failure analysis.

### Agent G: QA + Eval
- Own gold datasets, pass/fail gates, release checklist.

---

## 14) Evaluation Plan

### 14.1 Datasets
- Decision extraction labeled set.
- Geo labeled set.
- RAG question set with expected citations.

### 14.2 Metrics
- Decision extraction precision/recall.
- Search retrieval hit-rate on known phrases.
- RAG citation correctness.
- RAG refusal correctness.
- Geo precision at confidence threshold.
- False-map rate for `NONE` scope.

### 14.3 Exit thresholds
Define concrete numeric thresholds before each milestone starts; publish in `eval/reports`.

---

## 15) Observability and Reproducibility

- Every pipeline action belongs to a `pipeline_run_id`.
- Log URL, status, bytes, hash, and error reason.
- Track parser and extraction failures by document version.
- Store derivation metadata:
  - source hash,
  - code version,
  - model/provider version where relevant.

---

## 16) Risk Register

1. CMS page structure changes.
   - Mitigation: resilient parser with fallback extraction paths.
2. Extraction quality variance in PDFs.
   - Mitigation: quality flags and deferred OCR fallback pass.
3. Attachment volume growth.
   - Mitigation: queue control and batch processing.
4. Geo false positives.
   - Mitigation: conservative confidence thresholds and `NONE` guardrail.
5. RAG hallucination.
   - Mitigation: citations-only policy and refusal default.

---

## 17) Milestones and Deliverables

### M1: Discovery + versioned archive
- Recursive adapter operational.
- PDFs fetched and versioned.
- Crawl run endpoint available.

### M2: Extraction + search corpus
- Protocol and attachment text extracted.
- Search endpoint operational.

### M3: Decisions with citations
- Meeting decision lists and decision cards available.

### M4: RAG (MVP-1)
- Ask endpoint with citations/refusal.

### M5: Geo (MVP-2)
- Scope classification and high-confidence mapping.

### M6: Timeline (MVP-2)
- Decision timeline and export.

---

## 18) First Sprint (Execution Ready)

1. Build schema and migration baseline.
2. Implement recursive Ashdod scraper for topic/year/meeting/attachments.
3. Ingest 2025-2026 council scope.
4. Fetch and version first batch.
5. Extract text from all PDF protocols and attachments.
6. Validate parser quality on sample meetings.
7. Ship minimal UI path with citations.

---

## 19) Definition of Done

MVP-0 is complete when:
- Dynamic taxonomy navigation works.
- Meetings and decisions are visible with citations.
- Protocol and attachment text is searchable.
- Pipeline is idempotent and reproducible.

MVP-2 is complete when:
- RAG passes citation/refusal evaluation gates.
- Geo mapping obeys strict confidence rules.
- Timeline Track A is operational and exportable.
