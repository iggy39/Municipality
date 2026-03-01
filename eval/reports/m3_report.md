# M3 QA Report - Decision Extraction + Citation-First UX

Date: 2026-03-01
Scope: M3 tickets M3-T00 through M3-T10

## Exit Gate Status

- PASS: Council meeting metadata parsing is implemented with date/kind/code/committee normalization and confidence metadata.
- PASS: Decisions are parsed into normalized records, deduplicated, and persisted with citation anchors.
- PASS: Vote parsing supports `בעד/נגד/נמנע`, spaced count variants, and `פה אחד`; uncertain vote evidence is flagged.
- PASS: Bytez fallback path exists for low-confidence rows and public fallback metadata is persisted on decisions.
- PASS: `GET /meeting/{meeting_id}` and `GET /decision/{decision_id}` expose citations, linked documents, votes, source markers, and fallback metadata.
- PASS: Minimal RTL decision-card UI route (`/ui/decision/{decision_id}`) renders decision text, citations, linked docs, vote outcome, and fallback badge.
- PASS: Decision extraction quality harness and regression tests are published.

## Metrics Snapshot

Dataset: `eval/gold/m3_decision_eval_set.json`
Harness: `src/municipality/eval_decisions.py`

- Decision precision: 1.00
- Decision recall: 1.00
- Vote precision: 1.00
- Vote recall: 1.00
- Citation coverage (public decisions): 1.00
- Citation correctness proxy (span->page resolution): 1.00

## Fallback Usage Transparency

- Provider lock: Bytez
- Model lock: `dicta-il/DictaLM-3.0-24B-Thinking`
- Credential source: `BYTEZ_API_KEY` only
- Public decision metadata contract includes:
  - `fallback_used`
  - `fallback_reason`
  - `fallback_provider`
  - `fallback_model`
  - `fallback_invoked_at`
  - `fallback_validation_status`
  - `fallback_validation_reasons`

## Evidence and Implementation Map

- Migration: `migrations/003_m3_decisions_citations.sql`
- Models: `src/municipality/models.py`
- Deterministic decision parser + linker: `src/municipality/decisions.py`
- Bytez fallback wrapper: `src/municipality/fallback.py`
- Processing integration: `src/municipality/processing.py`
- API + UI handlers: `src/municipality/api.py`
- M3 eval harness: `src/municipality/eval_decisions.py`
- Decision parser unit tests: `tests/unit/test_decisions.py`
- M3 processing/API integration test: `tests/integration/test_m3_decisions_api.py`
- M3 eval regression test: `tests/integration/test_m3_decision_eval.py`

## Step-by-Step Examples (Sampled Council Protocol Format)

1. Input line: `1. אישור תקציב החינוך לשנת 2026 הוחלט לאשר. בעד/נגד/נמנע: 9/1/0`
   - Parsed decision text: `אישור תקציב החינוך לשנת 2026 הוחלט לאשר...`
   - Parsed vote: `for=9, against=1, abstain=0`
   - Citation anchor: generated from text offsets to page label (`p.X#start-end`)

2. Input line: `2. מינוי ועדת ביקורת אושר פה אחד`
   - Parsed decision text: `מינוי ועדת ביקורת אושר פה אחד`
   - Parsed vote: `unanimous=true`
   - Citation anchor: generated and exposed in decision API payload

3. Input line with weak vote evidence: `... בעד ונגד ללא מספרים`
   - Vote handling: `is_uncertain=true` (no numeric guessing)
   - Low-confidence path: fallback adjudication can run when configured

## Known Edge Cases / Backlog

- Table-heavy PDFs with broken line layout can still require additional row-reconstruction heuristics.
- Complex Hebrew date formats (non-numeric) are not fully normalized yet.
- Bytez endpoint-specific transport differences may require deployment-time endpoint override (`BYTEZ_API_URL`) while keeping model/provider lock.
- Decision-to-attachment linkage is conservative (`heuristic`) when exact per-decision attachment relevance is not explicit in source.

## Signoff

M3 quality gates pass on the published regression harness and integration scenarios. M4 can start.
