# Milestone M4 - RAG with Citations-Only Policy

Goal: add question answering over protocol+attachment corpus with strict citation grounding and refusal on weak evidence.

Scope lock for M4:
- Use the same API model as M3 for RAG answer generation and citation verification.
- Provider: Bytez.
- Model: `google/gemini-2.5-pro`.
- API key source: `BYTEZ_API_KEY` environment variable only (never hardcoded in repo).
- Start with a real-life case from persisted M2 outputs before synthetic-only eval cases.

Processing prompt prefixes (mandatory):
- Every API-model answer-synthesis request must start with:
  - `answer question from provided hebrew municipal evidence with citations only`
- Every API-model citation-verification request must start with:
  - `verify every claim against provided hebrew evidence and citations only`
- Every API-model refusal-decision request must start with:
  - `if evidence is insufficient, refuse in hebrew and explain missing evidence`
- Prefix strings must be literal and appear at the first instruction line.

Real-life bootstrap example (must be first):
- Build the first M4 evaluation case from existing M2 outputs in `municipality.db`.
- Protocol evidence is mandatory for bootstrap pass/fail; attachment evidence is supporting when same-topic context exists.
- Seed evidence references:
  - Protocol chunk `text_chunk.chunk_id=72cfd3d013348e5ba31c2f5f53a1c6d1f5ce76e1` (`source_kind=protocol`, citation `pp.2-3`).
  - Supporting attachment chunk `text_chunk.chunk_id=ab0ed12f9373d2130119a1e20faf951fe1da4490` (`source_kind=attachment`, citation `pp.2-3`).
- Required behavior: return a citation-grounded answer from protocol evidence; include supporting attachment citation when available and same-topic; refuse when protocol evidence is missing or cross-topic evidence is detected.

## Exit Gate

- [x] Provider-agnostic LLM adapter layer is in place.
- [x] Retrieval uses indexed chunks with citation metadata.
- [x] `/ask` returns answer + citations or refusal only.
- [x] Citation correctness and refusal correctness pass evaluation.
- [x] Ask UI panel is usable in Hebrew-first workflow.
- [x] Prompt-prefix policy is enforced for answer, verification, and refusal calls.
- [x] M4 eval starts with a real-life M2-derived mixed-source case.
- [x] Default RAG provider/model is Bytez + `google/gemini-2.5-pro`.
- [ ] Hybrid semantic topic+entity tree is persisted and integrated into retrieval with max one semantic API extraction call per document version.

## Ticket Board

| ID | Title | Priority | Depends On |
|---|---|---|---|
| M4-T00 | Real-life RAG bootstrap from M2 products | High | M3-T10 |
| M4-T01 | Provider-agnostic LLM interface | High | M4-T00 |
| M4-T02 | Retrieval orchestration for RAG | High | M4-T01 |
| M4-T03 | Answer composer with citation checks | High | M4-T02 |
| M4-T04 | Refusal policy and confidence gate | High | M4-T03 |
| M4-T05 | `/ask` API contract and handler | High | M4-T04 |
| M4-T06 | Ask panel UI integration | Medium | M4-T05 |
| M4-T07 | RAG eval set and scoring harness | High | M4-T06 |
| M4-T08 | Hallucination guard regression tests | High | M4-T07 |
| M4-T09 | RAG observability and tracing | Medium | M4-T05 |
| M4-T10 | M4 QA report and signoff | High | M4-T08 |
| M4-T11 | Hybrid semantic topic+entity tree (one API call per document) | High | M4-T02, M4-T03 |

---

## Tickets

### M4-T00 - Real-life RAG bootstrap from M2 products
Checklist:
- [x] Build first `/ask` scenario from persisted M2 outputs (no synthetic-only source text).
- [x] Use one mandatory protocol chunk and one same-topic supporting attachment chunk with citation labels.
- [x] Define expected grounded answer and paired expected refusal behavior.
- [x] Store stable source references (chunk IDs/document IDs) for reproducible eval.

Done when:
- [x] Bootstrap case passes citation checks and is item #1 in the M4 eval set.

### M4-T01 - Provider-agnostic LLM interface
Checklist:
- [x] Define minimal interface for generation tasks.
- [x] Add pluggable provider adapters behind one config surface.
- [x] Ensure local config can switch providers without code changes.
- [x] Add mock provider for deterministic tests.
- [x] Set default provider/model to Bytez + `google/gemini-2.5-pro`.
- [x] Expose prompt-prefix config by call type (answer/verify/refuse).

Done when:
- [x] RAG tests run against mock provider without network dependency.
- [x] Production default resolves to M3-locked provider/model without code changes.

### M4-T02 - Retrieval orchestration for RAG
Checklist:
- [x] Implement query normalization and retrieval from chunk index.
- [x] Retrieve across protocol and attachment sources.
- [x] Preserve chunk citation metadata through retrieval pipeline.
- [x] Add tunable top-k and source filters.
- [x] Confirm retrieval can reproduce M4-T00 mixed-source context.

Done when:
- [x] Retrieved context set includes citable chunks for known questions.
- [x] Bootstrap case retrieves both source kinds with citation-ready context.

### M4-T03 - Answer composer with citation checks
Checklist:
- [x] Build answer synthesis prompt/policy requiring citations.
- [x] Enforce answer-synthesis prefix on every generation call.
- [x] Add citation-verification pass with mandatory verification prefix.
- [x] Validate every major claim has citation support.
- [x] Return structured output: answer, citations, limitations.

Done when:
- [x] Uncited claim paths are rejected before response is returned.

### M4-T04 - Refusal policy and confidence gate
Checklist:
- [x] Define insufficient-evidence conditions.
- [x] Enforce refusal-decision prefix on refusal path.
- [x] Implement refusal response template in Hebrew.
- [x] Add logic to prevent speculative completion.

Done when:
- [x] Weak retrieval cases consistently return refusal.
- [x] Missing mixed-source evidence in bootstrap case returns refusal.

### M4-T05 - `/ask` API contract and handler
Checklist:
- [x] Implement `POST /ask` request/response schema.
- [x] Include citations with document/page references.
- [x] Include limitations field when evidence is partial.
- [x] Include source type per citation.
- [x] Include refusal reason code when response is refusal.

Done when:
- [x] API is stable and backward-compatible with planned UI.

### M4-T06 - Ask panel UI integration
Checklist:
- [x] Add Ask panel to meeting/topic context.
- [x] Render answer, citations, and refusal state clearly.
- [x] Enable click-through from citation to source document context.

Done when:
- [x] User can ask and inspect evidence without leaving workflow.

### M4-T07 - RAG eval set and scoring harness
Checklist:
- [x] Start eval set with the real-life M4-T00 mixed-source case.
- [x] Create question set with expected evidence references.
- [x] Score citation correctness and answer correctness.
- [x] Score refusal correctness for insufficient-evidence items.
- [x] Define pass/fail thresholds for citation and refusal metrics.

Done when:
- [x] Evaluation report shows pass/fail against thresholds.
- [x] Report includes bootstrap-case traceability to source chunk IDs.

### M4-T08 - Hallucination guard regression tests
Checklist:
- [x] Add tests for common hallucination failure modes.
- [x] Add tests for mixed protocol/attachment evidence.
- [x] Add tests for intentionally ambiguous queries.
- [x] Add regression test that missing required prompt prefix fails fast.

Done when:
- [x] Regression suite catches uncited/unsupported outputs and missing-prefix calls.

### M4-T09 - RAG observability and tracing
Checklist:
- [x] Log retrieval set IDs, scoring stats, and provider metadata.
- [x] Log refusal reasons in structured form.
- [x] Log model name and prompt-prefix category used per call.
- [x] Add sampling for answer quality auditing.

Done when:
- [x] Debugging a bad answer can be done from logs alone.

### M4-T10 - M4 QA report and signoff
Checklist:
- [x] Create `eval/reports/m4_report.md`.
- [x] Publish citation correctness and refusal correctness metrics.
- [x] List known limitations and planned mitigations.
- [x] Confirm model lock and prefix-policy compliance in report metadata.

Done when:
- [x] M4 signoff completed and M5 can start.

### M4-T11 - Hybrid semantic topic+entity tree (one API call per document)
Checklist:
- [x] Implement semantic schema + ORM for tree nodes, aliases, edges, mentions, and decision/chunk links.
- [x] Add one-call-per-document semantic extraction contract and caching key.
- [x] Enforce strict span validation and reject unsupported semantic candidates.
- [x] Implement canonicalization pipeline (normalization, alias merge, specificity/depth gates, stable-hash dedup).
- [x] Integrate semantic boosting/filtering in retrieval while preserving citation-first behavior.
- [x] Add regression tests + eval report for semantic precision, duplicate rate, depth/specificity quality, and retrieval lift.
- [x] Follow detailed implementation blueprint in `docs/tickets/M4_semantic_topic_entity_tree_plan.md`.

Status note:
- Phases 1-6 are completed in code with semantic retrieval integrated in search and `/ask` pathways.
- Evaluation and reporting are published with one-call compliance, evidence-backed node quality, duplicate-rate tracking, and retrieval-lift metrics.

Done when:
- [x] Semantic extraction performs at most one API call per `(document_version_id, prompt_hash, model)` tuple.
- [x] Accepted semantic nodes are 100% evidence-backed (validated text spans).
- [x] Semantic retrieval improves specific decision-level query quality over lexical baseline.
