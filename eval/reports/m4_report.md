# M4 QA Report - Ask UI + RAG Evaluation + Hallucination Guards

Date: 2026-03-11
Scope: M4-T06, M4-T07, M4-T08, M4-T09, M4-T10 (with existing semantic retrieval baseline)

## Exit Gate Snapshot

- PASS: Decision workflow UI now includes an Ask panel wired to `POST /ask` with Hebrew-first answer/refusal rendering.
- PASS: Ask panel renders answer text, limitations, refusal state, and citation links that click through to document page context.
- PASS: RAG eval harness scores citation correctness, answer correctness, and refusal correctness from live `POST /ask` response payloads.
- PASS: Eval set starts with bootstrap case `m4-t00-bootstrap-real-life-001` and keeps traceable source chunk IDs.
- PASS: Hallucination/prefix regressions expanded for uncited claims, mixed-source gaps, ambiguous queries, and missing prompt-prefix fail-fast behavior.
- PASS: Structured observability is now emitted per ask request, retrieval set, LLM call type, refusal reason, and sampled audit event.

## M4-T06 Ask Panel UI Integration

- UI route: `GET /ui/decision/{decision_id}`
- Integration contract:
  - Ask form submits to `POST /ask` using meeting/topic context (`muni`, `topic`) and mixed evidence requirements.
  - Answer path renders answer body + limitations + citation list.
  - Refusal path renders refusal message and missing source hints.
  - Citation entries link to source documents with `#page=` anchors for click-through context.
- Regression coverage: `tests/integration/test_m3_decisions_api.py`

## M4-T07 RAG Eval Harness + Scoring

- Harness module: `src/municipality/eval_rag.py`
- Gold set: `eval/gold/m4_rag_eval_set.json`
- Integration regression: `tests/integration/test_m4_rag_bootstrap.py`
- Scoring dimensions:
  - Citation correctness (`required_chunk_ids` present in `citations[].chunk_id`)
  - Answer correctness (`expected_grounded.answer_must_include` fragments)
  - Refusal correctness (reason code + missing source type + refusal message fragments)
- Thresholds (from eval set):
  - Citation correctness >= 1.00
  - Answer correctness >= 1.00
  - Refusal correctness >= 1.00

Published deterministic bootstrap-run metrics:

- Citation correctness: 1.00
- Answer correctness: 1.00
- Refusal correctness: 1.00
- Bootstrap traceability: PASS (`m4-t00-bootstrap-real-life-001` links back to fixed chunk IDs)

## M4-T08 Hallucination + Prefix Regression Expansion

- Unit regressions: `tests/unit/test_rag_answering.py`
  - Refuse when answer cites only one side of required mixed protocol/attachment evidence
  - Refuse ambiguous query when retrieval context is empty (`INSUFFICIENT_EVIDENCE`)
  - Refuse when verification cites chunk IDs not present in retrieved context (`INVALID_VERIFICATION_FORMAT`)
- Prefix policy regression: `tests/unit/test_rag_llm.py`
  - Missing required prompt prefix now fails fast before provider call.

## M4-T09 RAG Observability + Tracing

- Request-level traceability:
  - `ask_request_id` is generated per `/ask` request and returned in API output.
  - `retrieval_set_id` is generated per retrieval context set and returned under `retrieval` payload.
- Structured log events:
  - `rag.ask.request`, `rag.ask.response`, `rag.ask.audit_sample`
  - `rag.retrieval.start`, `rag.retrieval.result`
  - `rag.llm.call`, `rag.llm.result`
  - `rag.answering.start`, `rag.answering.answer`, `rag.answering.refusal`
- Required trace attributes now present in logs:
  - provider/model metadata
  - prompt-prefix category (`answer` / `verify` / `refuse`)
  - refusal reason code + missing source types
  - retrieval set IDs + retrieval score stats (`max/min/avg`)
- Audit sampling:
  - `RAG_AUDIT_SAMPLE_RATE` controls sampled quality logs (`0.0-1.0`, default `0.05`).

## M4-T10 QA Signoff Metadata

- Model lock:
  - Default RAG provider: `bytez`
  - Default RAG model: `google/gemini-2.5-pro`
  - Lock source: `src/municipality/fallback.py` + `src/municipality/rag_llm.py`
- Prefix-policy compliance:
  - Required call categories: `answer`, `verify`, `refuse`
  - Empty required prefix fails fast before provider call.
  - Regression coverage: `tests/unit/test_rag_llm.py`
- API contract traceability:
  - `/ask` now returns `ask_request_id` and `retrieval.retrieval_set_id`
  - Contract coverage: `tests/integration/test_m4_ask_api.py`

## Known Limitations and Mitigations

- Limitation: RAG gold set is bootstrap-sized and currently anchored by one primary real-life case.
  - Mitigation: Expand to a multi-question municipality set (30+ prompts) with mixed-source coverage and adversarial refusals.
- Limitation: Ask panel integration is currently decision-card first (`/ui/decision/{decision_id}`), not yet mirrored across all meeting/topic surfaces.
  - Mitigation: Reuse the same `/ask` contract in meeting/topic pages with shared UI component behavior.
- Limitation: Observability is log-based; sampled audits are not yet persisted in a dedicated analytics store.
  - Mitigation: Add sink integration (table or log pipeline) keyed by `ask_request_id` for long-term QA replay.
- Limitation: Ask UI sends `semantic_mode="off"` by default for conservative citation-first rollout.
  - Mitigation: Enable staged rollout for semantic boost mode after wider semantic retrieval benchmarking.

## M4 Signoff Decision

- Gate check: Citation correctness `1.00` (threshold `1.00`) - PASS
- Gate check: Answer correctness `1.00` (threshold `1.00`) - PASS
- Gate check: Refusal correctness `1.00` (threshold `1.00`) - PASS
- Gate check: Prompt-prefix enforcement + hallucination regressions - PASS
- Gate check: Structured observability and trace IDs in `/ask` path - PASS

Conclusion: M4 QA signoff is complete for tickets M4-T06 through M4-T10, and M5 implementation can begin.

## Existing Semantic Baseline (M4-T11 Context)

- Semantic retrieval controls and API diagnostics remain available and unchanged.
- Semantic quality harness/report remains in:
  - `src/municipality/eval_semantic.py`
  - `eval/reports/m4_semantic_tree_report.md`

## Signoff Note

M4 UI and RAG quality gates are now measurable end-to-end on top of `POST /ask` outputs, with explicit hallucination and prompt-prefix regression coverage.
