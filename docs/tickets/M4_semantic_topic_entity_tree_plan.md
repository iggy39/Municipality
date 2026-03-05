# M4-T11 Implementation Plan
## Hybrid Semantic Topic+Entity Tree (One API Call Per Document)

Status: In progress (Phase 1-2 completed in code, Phase 3 aligned to multicategory evidence spans)
Date: 2026-03-05
Owner milestone: M4
Related board ticket: `M4-T11` in `docs/tickets/M4_rag_citations.md`

Implementation progress snapshot:

- Phase 1: completed (migration `004` + ORM parity + migration idempotency test update).
- Phase 2: completed (single-call extractor, cache guard, strict contract, multicategory evidence-span prompt contract).
- Phase 3: in progress (canonicalization core implemented and aligned to Phase 2 evidence-span categories with Option-2 gating; full persistence audit wiring remains in later phases).
- Phase 4-6: planned.

---

## 1) Why this plan exists

Current taxonomy in the project is source-navigation taxonomy (`taxonomy_node`), not semantic meaning extracted from decisions/chunks. This limits precision for user queries and weakens retrieval quality for specific decision questions.

This plan adds a hybrid semantic layer:

- Hierarchical policy topics for navigation and grouping.
- Linked entities for precision (organizations, programs, roads, neighborhoods, budget items, etc.).

The design is locked to minimize model cost and drift:

- Max **one semantic API extraction call per document version**.
- All accepted nodes must be backed by source evidence spans.
- Canonicalization is deterministic and idempotent.

---

## 2) Scope and non-scope

### In scope

- Semantic schema + ORM.
- Processing-stage semantic extraction and persistence.
- Canonicalization and dedup governance.
- Search/retrieval integration (semantic boost + optional filter).
- API contract additions for semantic navigation and debugability.
- Evaluation/reporting for semantic quality and retrieval lift.

### Out of scope (for M4-T11)

- Manual curation UI/editor workflow.
- Cross-municipality shared ontology synchronization.
- Full ontology reasoning engine (OWL reasoner).
- Multi-call map-reduce extraction per long document (explicitly disallowed here).

---

## 3) Locked constraints and design principles

1. **One semantic model call per document**
   - One call for each unique tuple `(document_version_id, prompt_hash, provider, model)`.
   - Re-runs with same tuple must read cached result.
2. **Evidence-first acceptance**
   - Model outputs are candidates only.
   - Candidates become persisted nodes/links only after span and policy validation.
3. **Deterministic canonicalization**
   - Same inputs produce same canonical IDs and links.
   - Idempotent reruns: no duplicate semantic nodes/aliases/links.
4. **Precision-first public behavior**
   - Prefer missing a low-confidence semantic tag over introducing noisy/generic tags.
5. **Backward compatibility**
   - Existing `/search`, `/meeting/{id}`, `/decision/{id}` contracts remain valid.
   - Semantic fields are additive.

---

## 4) End-to-end data flow (single-call architecture)

1. Processing loads extracted text + existing decision/chunk spans.
2. Deterministic pre-pass builds compact semantic evidence packet:
   - headings,
   - compact evidence lines (coverage-first, page-aware),
   - soft regex priors for category hints (`decision|plan_program|discussion|policy|budget_finance|implementation|procurement_legal|public_feedback`),
   - high-value noun phrases / entities,
   - citation-aware offsets and page references.
3. System sends one strict JSON extraction request to the API model with two tasks in the same call:
   - detect and classify evidence spans by category,
   - extract semantic nodes/entities linked to detected span ids.
4. System validates response schema, evidence-span references, and span integrity.
5. Canonicalizer normalizes, merges aliases, applies specificity/depth gates, and computes stable hashes.
6. Persist accepted nodes, aliases, mentions, and links.
7. Persist reject reasons for audit and quality tuning.
8. Retrieval uses lexical + semantic signals.

If one-call extraction fails or is invalid:

- No second semantic API call.
- Deterministic-only semantic candidates may be persisted if they pass strict thresholds.
- Run metadata records fallback mode and reason.

---

## 5) File-by-file delta plan

This section is the implementation blueprint by file, in execution order.

### 5.1 New migration

#### `migrations/004_m4_semantic_tree.sql` (new)

Create semantic persistence and audit tables.

1) `semantic_document_run`
- Purpose: one-call guarantee, caching, and audit.
- Columns:
  - `id` PK
  - `document_version_id` FK -> `document_version.id`
  - `prompt_hash` `VARCHAR(64)`
  - `model_provider` `VARCHAR(64)`
  - `model_name` `VARCHAR(128)`
  - `status` `VARCHAR(32)` (`running|completed|failed|cached|deterministic_only`)
  - `api_call_count` `INTEGER NOT NULL DEFAULT 0`
  - `request_tokens` `INTEGER NULL`
  - `response_tokens` `INTEGER NULL`
  - `error_code` `VARCHAR(64) NULL`
  - `error_text` `TEXT NULL`
  - `extraction_payload_json` `TEXT NULL`
  - `validation_report_json` `TEXT NULL`
  - `canonicalization_report_json` `TEXT NULL`
  - `started_at`, `finished_at`, `created_at`
- Constraints/indexes:
  - `UNIQUE(document_version_id, prompt_hash, model_provider, model_name)`
  - index on `status`

2) `semantic_node`
- Purpose: canonical topic/entity nodes.
- Columns:
  - `id` PK
  - `source_site_id` FK -> `source_site.id`
  - `node_key_hash` `VARCHAR(40)`
  - `node_kind` `VARCHAR(16)` (`topic|entity`)
  - `semantic_type` `VARCHAR(64)` (typed class, ex: `committee`, `program`, `budget_item`)
  - `pref_label_he` `TEXT`
  - `pref_label_norm` `VARCHAR(255)`
  - `parent_node_id` FK -> `semantic_node.id` nullable
  - `depth` `INTEGER`
  - `specificity_score` `REAL`
  - `confidence` `REAL`
  - `support_count` `INTEGER NOT NULL DEFAULT 0`
  - `status` `VARCHAR(16)` (`active|candidate|deprecated|rejected`)
  - `first_seen_document_version_id` FK nullable
  - `last_seen_document_version_id` FK nullable
  - `metadata_json` `TEXT NULL`
  - `created_at`, `updated_at`
- Constraints/indexes:
  - `UNIQUE(source_site_id, node_key_hash)`
  - `UNIQUE(source_site_id, parent_node_id, node_kind, semantic_type, pref_label_norm)`
  - indexes on `parent_node_id`, `node_kind`, `semantic_type`, `depth`, `status`, `pref_label_norm`

3) `semantic_alias`
- Purpose: alternate labels and merged variants.
- Columns:
  - `id` PK
  - `semantic_node_id` FK -> `semantic_node.id`
  - `alias_hash` `VARCHAR(40)`
  - `alias_label_he` `TEXT`
  - `alias_label_norm` `VARCHAR(255)`
  - `alias_kind` `VARCHAR(24)` (`alt|abbr|surface|legacy`)
  - `confidence` `REAL`
  - `first_seen_document_version_id` FK nullable
  - `last_seen_document_version_id` FK nullable
  - `metadata_json` `TEXT NULL`
  - `created_at`
- Constraints/indexes:
  - `UNIQUE(semantic_node_id, alias_hash)`
  - indexes on `alias_label_norm`, `alias_kind`

4) `semantic_edge`
- Purpose: non-parent semantic relations.
- Columns:
  - `id` PK
  - `source_node_id` FK -> `semantic_node.id`
  - `target_node_id` FK -> `semantic_node.id`
  - `relation_type` `VARCHAR(24)` (`related|same_as|replaced_by|supports`)
  - `confidence` `REAL`
  - `provenance` `VARCHAR(24)` (`model|canonicalizer|manual`)
  - `metadata_json` `TEXT NULL`
  - `created_at`
- Constraints/indexes:
  - `UNIQUE(source_node_id, target_node_id, relation_type)`
  - indexes on `source_node_id`, `target_node_id`, `relation_type`

5) `semantic_mention`
- Purpose: evidence spans anchoring semantic nodes to source text.
- Columns:
  - `id` PK
  - `semantic_node_id` FK -> `semantic_node.id`
  - `document_id` FK -> `document.id`
  - `document_version_id` FK -> `document_version.id`
  - `source_kind` `VARCHAR(32)` (`protocol|attachment|other`)
  - `start_offset` `INTEGER`
  - `end_offset` `INTEGER`
  - `start_page` `INTEGER NULL`
  - `end_page` `INTEGER NULL`
  - `mention_text` `TEXT`
  - `mention_text_norm` `TEXT`
  - `mention_confidence` `REAL`
  - `evidence_hash` `VARCHAR(40)`
  - `metadata_json` `TEXT NULL`
  - `created_at`
- Constraints/indexes:
  - `UNIQUE(document_version_id, semantic_node_id, start_offset, end_offset)`
  - indexes on `document_id`, `semantic_node_id`, `evidence_hash`

6) `decision_semantic_link`
- Purpose: map decisions to semantic nodes.
- Columns:
  - `id` PK
  - `decision_id` FK -> `decision.id`
  - `semantic_node_id` FK -> `semantic_node.id`
  - `relation_role` `VARCHAR(32)` (`subject|object|location|budget_scope|program_scope`)
  - `confidence` `REAL`
  - `source_mention_id` FK -> `semantic_mention.id` nullable
  - `metadata_json` `TEXT NULL`
  - `created_at`
- Constraints/indexes:
  - `UNIQUE(decision_id, semantic_node_id, relation_role)`
  - indexes on `decision_id`, `semantic_node_id`, `relation_role`

7) `chunk_semantic_link`
- Purpose: map chunks to semantic nodes for retrieval.
- Columns:
  - `id` PK
  - `chunk_id` FK -> `text_chunk.chunk_id`
  - `semantic_node_id` FK -> `semantic_node.id`
  - `confidence` `REAL`
  - `source_mention_id` FK -> `semantic_mention.id` nullable
  - `metadata_json` `TEXT NULL`
  - `created_at`
- Constraints/indexes:
  - `UNIQUE(chunk_id, semantic_node_id)`
  - indexes on `chunk_id`, `semantic_node_id`

8) `semantic_candidate_reject`
- Purpose: explain why candidates were rejected.
- Columns:
  - `id` PK
  - `semantic_document_run_id` FK -> `semantic_document_run.id`
  - `candidate_label_he` `TEXT`
  - `candidate_label_norm` `VARCHAR(255)`
  - `node_kind` `VARCHAR(16)`
  - `semantic_type` `VARCHAR(64)`
  - `reason_code` `VARCHAR(64)`
  - `model_confidence` `REAL NULL`
  - `metadata_json` `TEXT NULL`
  - `created_at`
- Constraints/indexes:
  - index on `semantic_document_run_id`
  - index on `reason_code`

---

### 5.2 ORM and domain model deltas

#### `src/municipality/models.py` (update)

Add SQLAlchemy models mirroring all new migration tables:

- `SemanticDocumentRun`
- `SemanticNode`
- `SemanticAlias`
- `SemanticEdge`
- `SemanticMention`
- `DecisionSemanticLink`
- `ChunkSemanticLink`
- `SemanticCandidateReject`

Model constraints and indexes must match migration names exactly to keep migration/model parity and pass migration tests.

---

### 5.3 Semantic extraction and canonicalization modules

#### `src/municipality/semantic_contract.py` (new)

Defines:

- Prompt prefix constant:
  - `extract semantic topic and entity tree from hebrew municipal document with evidence offsets only`
- JSON schema contract for model response.
- Dataclasses for evidence spans, candidate nodes, aliases, mentions, links, and validation reports.
- Enumerations for node kinds, semantic relation roles, reject reason codes, and evidence categories.
- Validation for node-to-evidence-span references (`evidence_span_ids`).

#### `src/municipality/semantic_prompt.py` (new)

Builds one-call request payload:

- Input packet composition from deterministic lightweight pre-pass.
- Coverage-first evidence-line compaction with soft regex category priors (confidence hints only, never hard filters).
- Strict same-call instructions to classify evidence spans and map semantic nodes to span ids.
- Explicit refusal behavior in extraction output for unknown/uncertain candidates.

#### `src/municipality/semantic_extractor.py` (new)

Responsibilities:

- One-call extraction orchestration.
- Cache lookup in `semantic_document_run` before API call.
- Model invocation and strict JSON parsing.
- Zero additional retries that trigger extra model calls for same tuple.
- Capture token/call metrics in `semantic_document_run`.

#### `src/municipality/semantic_canonicalization.py` (new)

Responsibilities:

- Text normalization.
- Alias merge logic.
- Specificity/depth gating.
- Option-2 category support gating aligned to Phase 2:
  - decision evidence support threshold,
  - contextual non-decision evidence support threshold,
  - stricter no-reference activation gate.
- Stable-hash ID derivation.
- Dedup and hierarchy integrity checks.

#### `src/municipality/semantic_service.py` (new)

Responsibilities:

- End-to-end semantic pipeline per document version.
- Validation against extracted text and citation map.
- Structured run report for observability (`run_id`, cache/call status, evidence span count, candidate/accept/reject counters).
- Persistence of accepted nodes/mentions/links and rejected candidates is deferred to Phase 4 processing integration.

---

### 5.4 Processing integration deltas

#### `src/municipality/processing.py` (update)

Changes:

- Inject `SemanticService` dependency.
- After decision extraction and chunk persistence, call semantic pipeline once per processed document version.
- Add pipeline step status detail, for example:
  - `semantic_nodes=34; aliases=19; mentions=102; api_calls=1; rejects=12`
- On semantic failure:
  - mark semantic sub-step failed,
  - do not rollback extraction/chunking success,
  - preserve run idempotency.

---

### 5.5 Search and retrieval deltas

#### `src/municipality/search.py` (update)

Changes:

- Extend `SearchHit` with semantic metadata:
  - `semantic_match_count`
  - `semantic_node_ids`
  - `semantic_boost`
- Add optional search params in service method:
  - `semantic_node_id: int | None`
  - `semantic_label: str | None`
  - `semantic_mode: str = "boost"` (`boost|filter`)
- Add SQL joins to `chunk_semantic_link` and `semantic_node`.
- Ranking update (initial proposal):
  - `final_score = 0.65 * lexical_score + 0.25 * semantic_overlap_score + 0.10 * specificity_prior`
- Default behavior remains lexical-first with semantic boost.
- `semantic_mode=filter` applies strict filter only when requested.

---

### 5.6 API contract deltas

#### `src/municipality/api.py` (update)

1) Update `GET /search` query params (additive)

- Add optional:
  - `semantic_node_id`
  - `semantic_label`
  - `semantic_mode`
  - `include_semantic_debug`

2) Add semantic tree endpoints

- `GET /semantic/tree?muni=ashdod&root_id=&depth=&kind=`
  - returns hierarchical topic/entity nodes with counts.
- `GET /semantic/node/{node_id}`
  - returns node details, aliases, parents/children, linked decisions/chunks.

3) Add semantic run observability endpoint

- `GET /semantic/runs/{document_version_id}`
  - returns one-call compliance and validation/canonicalization report.

Backward compatibility:

- Existing endpoints remain unchanged in required fields.
- New fields are additive and nullable where needed.

---

### 5.7 Migration application and startup

#### `src/municipality/migrations.py` (no logic changes expected)

- Existing `apply_all()` already applies ordered SQL migrations.
- Ensure new migration name sorts after `003_*` and is idempotent.

---

### 5.8 Evaluation and reporting files

#### `eval/gold/m4_semantic_eval_set.json` (new)

Must include:

- Specific decision-level Hebrew queries.
- Expected semantic nodes (or aliases).
- Expected evidence chunk ids and source kinds.
- Negative tests for generic-topic failure.

#### `eval/reports/m4_semantic_tree_report.md` (new)

Must report:

- One-call compliance rate.
- Node precision estimate on labeled sample.
- Duplicate rate before/after canonicalization.
- Generic-node reject rate.
- Retrieval lift vs lexical baseline.

---

### 5.9 Test plan files

#### Unit tests (new)

- `tests/unit/test_semantic_normalization.py`
- `tests/unit/test_semantic_alias_merge.py`
- `tests/unit/test_semantic_specificity.py`
- `tests/unit/test_semantic_hashing.py`
- `tests/unit/test_semantic_span_validation.py`
- `tests/unit/test_semantic_prompt.py`
- `tests/unit/test_semantic_contract.py`

#### Integration tests (new)

- `tests/integration/test_m4_semantic_processing.py`
- `tests/integration/test_m4_semantic_search.py`
- `tests/integration/test_m4_semantic_api.py`

#### Existing tests updated

- `tests/unit/test_migrations.py` (ensure migration `004` applies idempotently)

---

## 6) Detailed canonicalization rules (critical)

This section is mandatory implementation behavior, not optional guidance.

### 6.1 Rule group A: normalization

For every candidate node label and mention text:

1. Unicode normalize with `NFKC`.
2. Remove bidi controls and zero-width chars.
3. Normalize quotes/apostrophes to a single ASCII apostrophe.
4. Normalize dashes (`-`, `--`, em-dash) to single hyphen.
5. Collapse multiple whitespace to single space.
6. Trim punctuation-only prefix/suffix tokens.
7. Lowercase/casefold where relevant for matching key only.
8. Preserve original label for display (`pref_label_he`), use normalized label for matching (`pref_label_norm`).
9. Expand safe abbreviations using controlled map (example categories: municipality abbreviations, committee abbreviations, street abbreviations).
10. Do not apply aggressive stemming in canonical key derivation (too risky for Hebrew proper nouns).

Reject if normalized label is empty, numeric-only without semantic type support, or contains only stopwords.

### 6.2 Rule group B: generic/noisy label rejection

A candidate is rejected as `TOO_GENERIC` if one or more holds:

- Label in denylist (`general`, `updates`, `misc`, equivalents in Hebrew corpus).
- Informative token count < 2 for topic nodes (unless whitelisted top-level concept).
- Stopword ratio > configured threshold.
- Label duplicates parent concept meaning with no discriminative token gain.

Reject reason must be written into `semantic_candidate_reject`.

### 6.3 Rule group C: specificity/depth gating

Compute `specificity_score` in `[0,1]` as weighted combination:

- lexical distinctiveness (IDF-weighted token uniqueness),
- mention support count,
- evidence span density,
- parent-child information gain.

Depth rules:

- Root depth starts at `0`.
- Child depth = parent depth + 1.
- Max depth hard cap (initially 6).
- Candidates beyond depth cap are either:
  - collapsed to nearest valid ancestor, or
  - persisted as `related` edge if meaning is valid but hierarchy is uncertain.

Promotion rules:

- `candidate -> active` only if
  - `specificity_score >= threshold_active`,
  - evidence spans >= minimum,
  - confidence >= minimum,
  - no hierarchy integrity violation.

### 6.4 Rule group D: alias merge policy

Matching operates in blocks to reduce false merges and runtime:

- block key = `(source_site_id, node_kind, semantic_type, parent_node_id)`.

Within block, compute pairwise similarity using:

- exact normalized match,
- token-set Jaccard,
- trigram similarity,
- abbreviation expansion equivalence,
- numeric token consistency.

Decision bands:

- `>= 0.94`: auto-merge.
- `0.85 - 0.9399`: keep separate, mark `MERGE_REVIEW_NEEDED`.
- `< 0.85`: no merge.

Hard merge blockers (override scores):

- conflicting numeric identifiers (for example different committee number/year code).
- incompatible semantic type.
- parent mismatch when both parents have high confidence and are not connected.

After merge:

- preserve loser label as `semantic_alias` (`alias_kind=legacy`).
- create `semantic_edge` with `relation_type=replaced_by` from old to new if old existed as active.
- never hard-delete active node that has links; deprecate instead.

### 6.5 Rule group E: stable hash dedup

Node canonical key format (`v1`):

`v1|source_site_id|node_kind|semantic_type|parent_node_key_hash_or_ROOT|pref_label_norm`

Hash:

- `node_key_hash = sha1(canonical_key).hexdigest()[:40]`

Alias hash format:

`v1|semantic_node_id|alias_label_norm`

Mention evidence hash format:

`v1|document_version_id|semantic_node_id|start_offset|end_offset|mention_text_norm`

Collision handling:

- if same hash but different canonical key appears, persist collision event and append deterministic suffix key for disambiguation.
- collision event is high severity metric.

### 6.6 Rule group F: hierarchy integrity

Before persisting parent-child:

- disallow self-parent.
- disallow cycles via ancestor traversal.
- disallow child with same normalized label as parent unless semantic type differs and is explicitly allowed.
- disallow topic/entity parent rules that violate configured ontology policy.

When parent confidence is low:

- store relation as `semantic_edge(related)` and keep node unparented until confidence rises.

### 6.7 Rule group G: evidence validation

For every model candidate mention:

- `0 <= start_offset < end_offset <= len(extracted_text)`
- `extracted_text[start_offset:end_offset]` must contain the claimed mention with strict or normalized match.
- page resolution must succeed from citation map (or be explicitly marked unresolved).

If any mention fails validation:

- mention rejected,
- node acceptance downgraded unless alternative valid evidence remains.

No public semantic link without at least one valid evidence mention.

### 6.8 Rule group H: confidence and public eligibility

Node public eligibility requires all:

- status `active`
- `confidence >= public_threshold`
- evidence count >= minimum
- no unresolved high-severity canonicalization warnings

Low-confidence nodes can remain internal for retrieval boost with reduced weight, but not as explicit user-facing semantic filters by default.

---

## 7) One-call-per-document enforcement details

### 7.1 Enforcement keys

One call allowed per unique tuple:

- `document_version_id`
- `prompt_hash`
- `model_provider`
- `model_name`

Enforced by unique DB constraint and service-level guard.

### 7.2 Processing behavior

Pseudo-flow:

```text
if semantic_document_run exists and status in (completed, cached):
    reuse cached output
else:
    create running record
    build evidence packet (deterministic)
    call model once
    api_call_count += 1
    validate + canonicalize + persist
    finalize run status
```

### 7.3 Long-document handling without extra calls

If extracted text is too large:

- deterministic pre-pass compacts text into evidence packet budget.
- packet includes top-ranked spans + metadata, not full raw text.
- no second model call for overflow.
- under-coverage is recorded in validation report.

---

## 8) Risk register and mitigations

1. Over-merge of near-similar entities
- Impact: incorrect grouping and retrieval contamination.
- Mitigation: hard blockers + confidence bands + no auto-merge in review band.
- Monitor: merge rollback candidates, contradictory links.

2. Under-merge duplicate variants
- Impact: bloated tree and weak recall.
- Mitigation: alias blocking + periodic consolidation job.
- Monitor: duplicate rate by normalized label per parent.

3. Generic topic inflation
- Impact: low-value tree and noisy filters.
- Mitigation: denylist + specificity thresholds + parent info-gain rule.
- Monitor: reject rate `TOO_GENERIC`; active generic-node ratio.

4. Hierarchy cycles or parent drift
- Impact: broken tree traversal.
- Mitigation: cycle checks + deterministic parent scoring + related-edge fallback.
- Monitor: cycle detection metric must stay zero.

5. Hallucinated model nodes
- Impact: false semantic facts.
- Mitigation: strict span validation; reject unsupported candidates.
- Monitor: unsupported-candidate reject rate.

6. Offset mismatch from extraction noise
- Impact: false rejects or wrong citations.
- Mitigation: normalized substring fallback + page-boundary validation.
- Monitor: span validation failure categories.

7. One-call policy reduces coverage on very long docs
- Impact: missing fine-grained entities.
- Mitigation: better deterministic pre-pass ranking and packet budgeting.
- Monitor: under-coverage marker rate vs retrieval lift.

8. API model outage/latency
- Impact: delayed processing.
- Mitigation: deterministic-only fallback mode and run status transparency.
- Monitor: semantic run failure rate and timeout rate.

9. Retrieval over-bias toward semantic tags
- Impact: missing lexical exact matches.
- Mitigation: lexical-first blend and guarded semantic filter mode.
- Monitor: regression on lexical benchmark queries.

10. Migration risk on live db
- Impact: deploy failure or lock contention.
- Mitigation: additive migration, indexed carefully, idempotent DDL.
- Monitor: migration time and lock logs.

11. Hebrew normalization mistakes
- Impact: incorrect merges/splits.
- Mitigation: conservative normalization, explicit abbreviation map, test corpus.
- Monitor: manual sample audits per run.

12. Semantic type drift over time
- Impact: unstable node typing.
- Mitigation: typed policy registry and versioned canonicalizer.
- Monitor: type-change frequency by node.

---

## 9) API delta contract details

### 9.1 `GET /search` additions (backward compatible)

Request additions:

- `semantic_node_id: int | null`
- `semantic_label: str | null`
- `semantic_mode: "boost" | "filter"` (default `boost`)
- `include_semantic_debug: bool` (default false)

Response additions per result:

- `semantic_match_count: int`
- `semantic_nodes: [{id, label, kind, type, confidence}]` (when debug enabled)
- `semantic_boost: float`

### 9.2 New endpoint `GET /semantic/tree`

Purpose: browse semantic hierarchy.

Response item shape:

- `id`, `label`, `kind`, `semantic_type`, `depth`, `parent_id`, `specificity_score`, `confidence`, `child_count`.

### 9.3 New endpoint `GET /semantic/node/{node_id}`

Purpose: inspect node details and evidence links.

Response includes:

- node core fields,
- aliases,
- parent and children,
- linked decisions,
- linked chunks,
- recent mentions/citations.

### 9.4 New endpoint `GET /semantic/runs/{document_version_id}`

Purpose: compliance and debugging.

Response includes:

- run status,
- `api_call_count`,
- prompt/model identity,
- validation report summary,
- reject reason histogram.

---

## 10) Execution phases

Phase 1: schema + ORM (completed)

- Add migration `004`.
- Add model classes.
- Pass migration idempotency tests.

Phase 2: extraction contracts + one-call service (completed)

- Add semantic contract/prompt/extractor modules.
- Add run caching and one-call guard.
- Add multicategory evidence-span extraction contract in the same model call.

Phase 3: canonicalization core (in progress)

- Implement normalization, merge, specificity, hash dedup.
- Align gating with Phase 2 evidence categories (decision + contextual support, stricter no-ref activation).
- Add reject audit pipeline persistence in integration phase.

Phase 4: processing integration (planned)

- Call semantic stage from `ProcessingService`.
- Persist links and mentions.

Phase 5: retrieval + APIs (planned)

- Add semantic boost/filter in search.
- Add semantic endpoints.

Phase 6: evaluation + report (planned)

- Create gold set and run metrics.
- Publish report and open known-issues list.

---

## 11) Quality gates for signoff

1. One-call compliance
- 100% of successful semantic runs have `api_call_count <= 1`.

2. Evidence integrity
- 100% of active nodes have at least one validated mention span.

3. Canonicalization quality
- duplicate active nodes under same parent/type below threshold.
- cycle count equals zero.

4. Retrieval impact
- specific decision-level query success improves over lexical baseline.

5. Stability
- rerun idempotency: no duplicate semantic artifacts for unchanged inputs.

---

## 12) Open implementation notes

- This plan intentionally keeps semantic extraction provider/model pluggable and aligned with M4 provider abstraction direction.
- If prompt/model or canonicalizer version changes, only then can a new semantic run be created for same document version.
- No destructive merge/delete operations should be applied to public nodes without preserving provenance and redirect edges.
