# Milestone M4 - RAG with Citations-Only Policy

Goal: add question answering over protocol+attachment corpus with strict citation grounding and refusal on weak evidence.

## Exit Gate

- [ ] Provider-agnostic LLM adapter layer is in place.
- [ ] Retrieval uses indexed chunks with citation metadata.
- [ ] `/ask` returns answer + citations or refusal only.
- [ ] Citation correctness and refusal correctness pass evaluation.
- [ ] Ask UI panel is usable in Hebrew-first workflow.

## Ticket Board

| ID | Title | Priority | Depends On |
|---|---|---|---|
| M4-T01 | Provider-agnostic LLM interface | High | M3-T10 |
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

### M4-T01 - Provider-agnostic LLM interface
Checklist:
- [ ] Define minimal interface for generation tasks.
- [ ] Add pluggable provider adapters behind one config surface.
- [ ] Ensure local config can switch providers without code changes.
- [ ] Add mock provider for deterministic tests.

Done when:
- [ ] RAG tests run against mock provider without network dependency.

### M4-T02 - Retrieval orchestration for RAG
Checklist:
- [ ] Implement query normalization and retrieval from chunk index.
- [ ] Retrieve across protocol and attachment sources.
- [ ] Preserve chunk citation metadata through retrieval pipeline.
- [ ] Add tunable top-k and source filters.

Done when:
- [ ] Retrieved context set includes citable chunks for known questions.

### M4-T03 - Answer composer with citation checks
Checklist:
- [ ] Build answer synthesis prompt/policy requiring citations.
- [ ] Validate every major claim has citation support.
- [ ] Return structured output: answer, citations, limitations.

Done when:
- [ ] Uncited claim paths are rejected before response is returned.

### M4-T04 - Refusal policy and confidence gate
Checklist:
- [ ] Define insufficient-evidence conditions.
- [ ] Implement refusal response template in Hebrew.
- [ ] Add logic to prevent speculative completion.

Done when:
- [ ] Weak retrieval cases consistently return refusal.

### M4-T05 - `/ask` API contract and handler
Checklist:
- [ ] Implement `POST /ask` request/response schema.
- [ ] Include citations with document/page references.
- [ ] Include limitations field when evidence is partial.
- [ ] Include source type per citation.

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
- [ ] Create question set with expected evidence references.
- [ ] Score citation correctness and answer correctness.
- [ ] Score refusal correctness for insufficient-evidence items.

Done when:
- [ ] Evaluation report shows pass/fail against thresholds.

### M4-T08 - Hallucination guard regression tests
Checklist:
- [ ] Add tests for common hallucination failure modes.
- [ ] Add tests for mixed protocol/attachment evidence.
- [ ] Add tests for intentionally ambiguous queries.

Done when:
- [ ] Regression suite catches uncited/unsupported outputs.

### M4-T09 - RAG observability and tracing
Checklist:
- [ ] Log retrieval set IDs, scoring stats, and provider metadata.
- [ ] Log refusal reasons in structured form.
- [ ] Add sampling for answer quality auditing.

Done when:
- [ ] Debugging a bad answer can be done from logs alone.

### M4-T10 - M4 QA report and signoff
Checklist:
- [ ] Create `eval/reports/m4_report.md`.
- [ ] Publish citation correctness and refusal correctness metrics.
- [ ] List known limitations and planned mitigations.

Done when:
- [ ] M4 signoff completed and M5 can start.
