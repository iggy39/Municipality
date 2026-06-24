# Topic Subject V3 Schema/No-Think Helper Guidelines

This note is for agents working on the V3 topic-subject event pipeline.

## Default Profile

Use `schema_no_think_24b_helpers` as the default V3 helper-stage profile.

This means:
- Heavy semantic stages stay on the primary 24B Thinking model with normal thinking enabled.
- Helper and repair stages use the same primary model, but with `think=false`, `/no_think`, and JSON Schema response format when a prompt schema exists.

## Heavy Stages

Keep these stages on the primary model with normal thinking:
- `topic_subject_legacy_extraction`
- `topic_subject_v3_contextual_event_normalization`
- `topic_subject_v3_action_subject_extraction`
- `topic_subject_v3_event_judge`

Do not move these to no-think or a small model without a fresh matched regression.

## Helper Stages

Use schema/no-think for these stages:
- `topic_subject_json_repair`
- `topic_subject_v3_json_repair`
- `topic_subject_v3_quote_repair`
- `topic_subject_v3_evidence_entailment`
- `topic_subject_v3_formal_decision_evidence_repair`

These stages are mostly structure validation, quote repair, evidence checking, or conservative repair. They do not need long semantic reasoning by default.

## Evidence So Far

Matched profile runs showed:
- Same final predictions as baseline on the latest 18-row mixed direct Step 4 JSON batch.
- Faster helper-stage execution in practice.
- Cleaner accepted status on at least one formal-decision case after generic calibration repairs.

Important run references:
- `/Users/igor/Desktop/projects/Municipality/rag_eval/runs/topic_subject_v3_profile_real_row_experiments/20260624T135909Z_live`
- `/Users/igor/Desktop/projects/Municipality/rag_eval/runs/topic_subject_v3_profile_real_row_experiments/20260624T032621Z_live`

## Rules

- Do not enable `schema_no_think_24b_all` by default.
- Do not route semantic stages to `1.7B` or other small helper models without new A/B evidence.
- If a helper-stage warning appears, inspect whether it affects final normalized output before proposing a fix.
- Treat judge disagreement as a review signal, not automatic ground truth.
- Keep Step 4 subject/headline hints as matter/span hints only; they must not prove event existence, action, phase, or decision outcome.

## Required Verification For Future Changes

For any change to this profile or helper stages:
- Run focused unit tests: `/Users/igor/Desktop/projects/Municipality/.venv/bin/python -m pytest /Users/igor/Desktop/projects/Municipality/tests/unit/test_topic_subjects.py`
- Run a matched profile comparison against `baseline_current` on representative rows.
- Include at least one formal decision, one open request, one explicit objection, one inquiry title, one inquiry response, and one structural/attachment non-event.
- Report both semantic disagreements and warning-only differences.
