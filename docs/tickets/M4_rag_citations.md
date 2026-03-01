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
- Use a mixed-source question that requires both protocol and attachment evidence.
- Seed evidence references:
  - Protocol chunk `text_chunk.chunk_id=72cfd3d013348e5ba31c2f5f53a1c6d1f5ce76e1` (`source_kind=protocol`, citation `pp.2-3`).
  - Attachment chunk `text_chunk.chunk_id=ee1a42618d5aa132b3f8821151d3620af5ea4fb0` (`source_kind=attachment`, citation `p.1`).
- Required behavior: return answer with both citations, or refusal if one evidence side is missing.

## Exit Gate

- [ ] Provider-agnostic LLM adapter layer is in place.
- [ ] Retrieval uses indexed chunks with citation metadata.
- [ ] `/ask` returns answer + citations or refusal only.
- [ ] Citation correctness and refusal correctness pass evaluation.
- [ ] Ask UI panel is usable in Hebrew-first workflow.
- [ ] Prompt-prefix policy is enforced for answer, verification, and refusal calls.
- [ ] M4 eval starts with a real-life M2-derived mixed-source case.
- [ ] Default RAG provider/model is Bytez + `google/gemini-2.5-pro`.

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

---

## Tickets

### M4-T00 - Real-life RAG bootstrap from M2 products
Checklist:
- [ ] Build first `/ask` scenario from persisted M2 outputs (no synthetic-only source text).
- [ ] Use one protocol chunk and one attachment chunk with citation labels.
- [ ] Define expected grounded answer and paired expected refusal behavior.
- [ ] Store stable source references (chunk IDs/document IDs) for reproducible eval.

Done when:
- [ ] Bootstrap case passes citation checks and is item #1 in the M4 eval set.

### M4-T01 - Provider-agnostic LLM interface
Checklist:
- [ ] Define minimal interface for generation tasks.
- [ ] Add pluggable provider adapters behind one config surface.
- [ ] Ensure local config can switch providers without code changes.
- [ ] Add mock provider for deterministic tests.
- [ ] Set default provider/model to Bytez + `google/gemini-2.5-pro`.
- [ ] Expose prompt-prefix config by call type (answer/verify/refuse).

Done when:
- [ ] RAG tests run against mock provider without network dependency.
- [ ] Production default resolves to M3-locked provider/model without code changes.

### M4-T02 - Retrieval orchestration for RAG
Checklist:
- [ ] Implement query normalization and retrieval from chunk index.
- [ ] Retrieve across protocol and attachment sources.
- [ ] Preserve chunk citation metadata through retrieval pipeline.
- [ ] Add tunable top-k and source filters.
- [ ] Confirm retrieval can reproduce M4-T00 mixed-source context.

Done when:
- [ ] Retrieved context set includes citable chunks for known questions.
- [ ] Bootstrap case retrieves both source kinds with citation-ready context.

### M4-T03 - Answer composer with citation checks
Checklist:
- [ ] Build answer synthesis prompt/policy requiring citations.
- [ ] Enforce answer-synthesis prefix on every generation call.
- [ ] Add citation-verification pass with mandatory verification prefix.
- [ ] Validate every major claim has citation support.
- [ ] Return structured output: answer, citations, limitations.

Done when:
- [ ] Uncited claim paths are rejected before response is returned.

### M4-T04 - Refusal policy and confidence gate
Checklist:
- [ ] Define insufficient-evidence conditions.
- [ ] Enforce refusal-decision prefix on refusal path.
- [ ] Implement refusal response template in Hebrew.
- [ ] Add logic to prevent speculative completion.

Done when:
- [ ] Weak retrieval cases consistently return refusal.
- [ ] Missing mixed-source evidence in bootstrap case returns refusal.

### M4-T05 - `/ask` API contract and handler
Checklist:
- [ ] Implement `POST /ask` request/response schema.
- [ ] Include citations with document/page references.
- [ ] Include limitations field when evidence is partial.
- [ ] Include source type per citation.
- [ ] Include refusal reason code when response is refusal.

Done when:
- [ ] API is stable and backward-compatible with planned UI.

### M4-T06 - Ask panel UI integration
Checklist:
- [ ] Add Ask panel to meeting/topic context.
- [ ] Render answer, citations, and refusal state clearly.
- [ ] Enable click-through from citation to source document context.

Done when:
- [ ] User can ask and inspect evidence without leaving workflow.

### M4-T07 - RAG eval set and scoring harness
Checklist:
- [ ] Start eval set with the real-life M4-T00 mixed-source case.
- [ ] Create question set with expected evidence references.
- [ ] Score citation correctness and answer correctness.
- [ ] Score refusal correctness for insufficient-evidence items.
- [ ] Define pass/fail thresholds for citation and refusal metrics.

Done when:
- [ ] Evaluation report shows pass/fail against thresholds.
- [ ] Report includes bootstrap-case traceability to source chunk IDs.

### M4-T08 - Hallucination guard regression tests
Checklist:
- [ ] Add tests for common hallucination failure modes.
- [ ] Add tests for mixed protocol/attachment evidence.
- [ ] Add tests for intentionally ambiguous queries.
- [ ] Add regression test that missing required prompt prefix fails fast.

Done when:
- [ ] Regression suite catches uncited/unsupported outputs and missing-prefix calls.

### M4-T09 - RAG observability and tracing
Checklist:
- [ ] Log retrieval set IDs, scoring stats, and provider metadata.
- [ ] Log refusal reasons in structured form.
- [ ] Log model name and prompt-prefix category used per call.
- [ ] Add sampling for answer quality auditing.

Done when:
- [ ] Debugging a bad answer can be done from logs alone.

### M4-T10 - M4 QA report and signoff
Checklist:
- [ ] Create `eval/reports/m4_report.md`.
- [ ] Publish citation correctness and refusal correctness metrics.
- [ ] List known limitations and planned mitigations.
- [ ] Confirm model lock and prefix-policy compliance in report metadata.

Done when:
- [ ] M4 signoff completed and M5 can start.
