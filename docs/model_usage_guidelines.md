# Model Usage Guidelines

These rules protect extraction quality. Topic Subject V3 currently uses the primary Dicta 24B Thinking model for every model-backed stage.

## Core Rule

Choose the model by semantic risk, not only by prompt length.

Use the heavy model when the task decides what the document means. Do not route Topic Subject V3 stages to smaller Dicta models unless a fresh benchmark proves they do not introduce semantic drift or invalid enum values.

## Default Models

| Model | Use | Thinking |
|---|---|---|
| `dicta-il/DictaLM-3.0-24B-Thinking:bf16` | Hebrew semantic extraction, interpretation, and judging | Required |
| `dicta-il/DictaLM-3.0-1.7B-Thinking:latest` | Not used for Topic Subject V3 now | Paused after benchmark drift |
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
| `topic_subject_json_repair` | 24B Thinking | Format repair remains on the quality model for now |
| `topic_subject_v3_json_repair` | 24B Thinking | Format repair remains on the quality model for now |
| `topic_subject_v3_quote_repair` | 24B Thinking | Exact quote copying still affected final quality |
| `topic_subject_v3_evidence_entailment` | 24B Thinking | Evidence status controls final event/outcome acceptance |

Unknown new stages must default to the heavy model until they are explicitly classified.

## Small Dicta Model Status

The 1.7B Dicta model is disabled for Topic Subject V3 until further notice. The last benchmark showed enum drift such as `entitled` instead of `entailed` and differences on outcome entailment.

## Unsafe Uses For The Small Dicta Model

- Choosing action type, matter, or outcome from raw municipal text.
- Final judging or accepting/rejecting extracted events.
- Multi-row event normalization, duplicate suppression, or row role decisions.
- Ambiguous rows, low-confidence cases, OCR-heavy rows, or cross-municipality generalization decisions.
- Any stage whose output is persisted without a later heavy-model or deterministic validation step.

## Verification Rule

After changing model routing, run focused unit tests and at least one real dry-run sample from multiple municipalities. Inspect the quality report and the event JSON, especially the `stage_model_name`, `stage_think`, evidence-entailment metadata, and judge result.

If a future small-model experiment introduces semantic drift, keep the stage on 24B before optimizing speed further.

## Benchmarking V3 Quality

Use `scripts/benchmark_topic_subject_v3_models.py` to run 24B-only V3 quality benchmarks across representative municipality rows.

Each benchmark run must write these reports in the same output directory:

- `v3_quality_report.md` for problematic or low-confidence rows.
- `v3_all_rows_report.md` for every row, including accepted events and non-events.

Do not reintroduce 1.7B unless a separate experiment includes enum validation, output repair, and broad cross-municipality evidence that quality is unchanged.
