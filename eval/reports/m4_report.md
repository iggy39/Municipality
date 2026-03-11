# M4 QA Report - Ask UI + RAG Evaluation + Hallucination Guards

Date: 2026-03-10
Scope: M4-T06, M4-T07, M4-T08 (with existing semantic retrieval baseline)

## Exit Gate Snapshot

- PASS: Decision workflow UI now includes an Ask panel wired to `POST /ask` with Hebrew-first answer/refusal rendering.
- PASS: Ask panel renders answer text, limitations, refusal state, and citation links that click through to document page context.
- PASS: RAG eval harness scores citation correctness, answer correctness, and refusal correctness from live `POST /ask` response payloads.
- PASS: Eval set starts with bootstrap case `m4-t00-bootstrap-real-life-001` and keeps traceable source chunk IDs.
- PASS: Hallucination/prefix regressions expanded for uncited claims, mixed-source gaps, ambiguous queries, and missing prompt-prefix fail-fast behavior.

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

## Existing Semantic Baseline (M4-T11 Context)

- Semantic retrieval controls and API diagnostics remain available and unchanged.
- Semantic quality harness/report remains in:
  - `src/municipality/eval_semantic.py`
  - `eval/reports/m4_semantic_tree_report.md`

## Signoff Note

M4 UI and RAG quality gates are now measurable end-to-end on top of `POST /ask` outputs, with explicit hallucination and prompt-prefix regression coverage.
