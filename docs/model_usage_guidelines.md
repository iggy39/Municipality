# Model Usage Guidelines

These rules protect extraction quality while allowing faster local models for bounded support work.

## Core Rule

Choose the model by semantic risk, not only by prompt length.

Use the heavy model when the task decides what the document means. Use the small model only when the task is narrow, source-bounded, schema-bounded, and followed by heavier validation when it can affect final output.

## Default Models

| Model | Use | Thinking |
|---|---|---|
| `dicta-il/DictaLM-3.0-24B-Thinking:bf16` | Hebrew semantic extraction, interpretation, and judging | Required |
| `dicta-il/DictaLM-3.0-1.7B-Thinking:latest` | Short bounded support stages | Enabled unless there is a concrete reason not to |
| `mistral-small3.1:latest` | Vision/layout tasks | Task dependent |
| `qwen3.5:122b` | General non-Hebrew or broad planning/reasoning tasks | Task dependent |

## Never Disable 24B Thinking

For `dicta-il/DictaLM-3.0-24B-Thinking:bf16`, do not send `think: false` and do not add `/no_think` to the system prompt. Disabling thinking lowers accuracy for Hebrew municipal interpretation.

Use `format: json` and strict JSON instructions instead of disabling thinking.

## Topic Subject V3 Routing

| Stage | Model | Reason |
|---|---|---|
| `topic_subject_legacy_extraction` | 24B Thinking | Full semantic extraction |
| `topic_subject_v3_contextual_event_normalization` | 24B Thinking | Decides event identity and row roles |
| `topic_subject_v3_action_subject_extraction` | 24B Thinking | Decides action, matter, and outcome |
| `topic_subject_v3_event_judge` | 24B Thinking | Final semantic quality gate |
| `topic_subject_json_repair` | 1.7B Thinking | Format repair only, no new facts |
| `topic_subject_v3_json_repair` | 1.7B Thinking | Format repair only, no new facts |
| `topic_subject_v3_quote_repair` | 1.7B Thinking | Exact quote copying from supplied rows |
| `topic_subject_v3_evidence_entailment` | 1.7B Thinking | Short source-bounded entailment check; final 24B judge still runs afterward |

Unknown new stages must default to the heavy model until they are explicitly classified.

## Safe Uses For The Small Dicta Model

- Exact quote repair when the model may only copy spans from supplied source rows.
- JSON repair when the model may only convert malformed output into valid JSON without adding facts.
- Short evidence-entailment checks when all evidence is supplied in the prompt and a heavier judge follows before acceptance.
- Fast smoke checks where results are not persisted as final predictions.

## Unsafe Uses For The Small Dicta Model

- Choosing action type, matter, or outcome from raw municipal text.
- Final judging or accepting/rejecting extracted events.
- Multi-row event normalization, duplicate suppression, or row role decisions.
- Ambiguous rows, low-confidence cases, OCR-heavy rows, or cross-municipality generalization decisions.
- Any stage whose output is persisted without a later heavy-model or deterministic validation step.

## Verification Rule

After changing model routing, run focused unit tests and at least one real dry-run sample from multiple municipalities. Inspect the quality report and the event JSON, especially the `stage_model_name`, `stage_think`, evidence-entailment metadata, and judge result.

If a small-model stage introduces semantic drift, promote that stage back to 24B before optimizing speed further.
