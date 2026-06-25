# Prompt For Topic Pipeline Agent: Coordinate With V3 Topic-Subject Extraction

You are working on the slow PDF-first topic pipeline in `/Users/igor/Desktop/projects/Municipality`. Another agent is working on Topic Subject V3 event/action extraction in `/Users/igor/Desktop/projects/Municipality/src/municipality/topic_subjects.py`.

Your goal is to improve the upstream topic/document pipeline so V3 can reuse structure and normalized text instead of repeating work. Prefer generic, reusable solutions. Do not hardcode municipality-specific wording, protocol layouts, or semantic keywords unless explicitly approved.

## Current V3 Behavior

V3 does not read PDFs directly. It consumes already-imported `RetrievalArtifact` rows from the DB through `load_accepted_topic_artifacts(...)` in `/Users/igor/Desktop/projects/Municipality/src/municipality/topic_decisions.py`.

Current input text path:

- `load_accepted_topic_artifacts(...)` joins `RetrievalArtifact`, `ArtifactSemanticLink`, and `SemanticNode`.
- It sets `TopicDecisionArtifact.real_text = compact_text(artifact.body_text or artifact.retrieval_text or "")`.
- V3 primarily works from `artifact.body_text`, falling back to `artifact.retrieval_text`.
- PDF extraction, OCR/layout, semantic interpretation, topic assignment, topic validation, and retrieval chunk creation happen earlier in the PDF-first pipeline.

Relevant upstream pipeline files:

- `/Users/igor/Desktop/projects/Municipality/scripts/process_pdf_first_v4_batch.py`
- `/Users/igor/Desktop/projects/Municipality/rag_eval/runs/ashdod_2025_regular_3_short_pdf/pdf_first_pipeline/scripts/step3_1_structure_normalize.py`
- `/Users/igor/Desktop/projects/Municipality/rag_eval/runs/ashdod_2025_regular_3_short_pdf/pdf_first_pipeline/scripts/step4_v4_global_topic_assign.py`
- `/Users/igor/Desktop/projects/Municipality/rag_eval/runs/ashdod_2025_regular_3_short_pdf/pdf_first_pipeline/scripts/step4_5_v4_validate_topics.py`
- `/Users/igor/Desktop/projects/Municipality/rag_eval/runs/ashdod_2025_regular_3_short_pdf/pdf_first_pipeline/scripts/step5_v4_build_retrieval_chunks.py`
- `/Users/igor/Desktop/projects/Municipality/scripts/import_pdf_first_v4_retrieval_chunks.py`

## Recent V3 Changes You Should Know

V3 now uses only `dicta-il/DictaLM-3.0-24B-Thinking:bf16`. The 1.7B model was removed from active V3 paths because benchmarks showed enum drift and outcome-status differences.

V3 now adds generic `text_spans` to each source row, derived from punctuation/layout boundaries and overlapping windows. This was added because long mixed rows can start with background text and contain the real action sentence late in the row.

V3 now separates:

- `Decision Outcome`: narrow formal result such as approval/rejection/referral/removal/deferment.
- `Event Phase`: procedural lifecycle/status such as `open_request`, `inquiry_submitted`, `report_presented`, `activity_described`, or `decision_made`.

V3 report output now has clearer columns: action, matter, decision outcome, event phase, evidence quote, model judge, status.

## Current Problems In V3 That Upstream Can Help Solve

1. V3 repeats structure reasoning that upstream already does.

V3 currently re-identifies structural rows, event-bearing rows, action-bearing spans, background spans, and non-events from text. If upstream gives stable structure metadata, V3 can use that as hints and spend fewer model tokens.

2. V3 sometimes receives raw row text that mixes background, action, and outcome in one chunk.

This causes more expensive model reasoning and sometimes overly long predictions. Upstream can help by preserving smaller action-bearing spans and block references.

3. V3 currently relies mostly on `body_text`, not the richer processed metadata in retrieval chunks.

Useful upstream fields already exist in chunks but are not yet first-class V3 inputs:

- `structural_role`
- `summary_he`
- `source_semantic_unit_ids`
- `source_region_ids`
- `source_block_ids`
- `section_id`
- `evidence_refs`
- `evidence_contract`
- `topic_supporting_quote_he`
- entity facts from Step 3.5

4. V3 uses evidence quotes for audit, but report labels can become long if upstream/action spans are too broad.

Please help by emitting small, stable, exact spans where possible: one span for action, one for decision outcome, and separate spans for background/supporting details.

## What Would Help V3 Most

## Required Step 5 + Import Contract

These fields are not optional for new Step 5 retrieval chunk outputs intended for V3 consumption:

- `retrieval_chunks.json` MUST emit `corrected_text_he`, `spans`, richer `topic_assignment`, and explicit `structure_metadata`.
- `raw_text` MUST remain unchanged for quote grounding.
- `summary_he` MUST be treated as summary only, never evidence.
- `/Users/igor/Desktop/projects/Municipality/scripts/import_pdf_first_v4_retrieval_chunks.py` MUST preserve `RetrievalArtifact.body_text = raw_text`.
- The DB import MUST store cleaned text, spans, provenance, structure metadata, and topic assignment in `RetrievalArtifact.metadata_json`.
- Allowed `span_role` values are only `structural`, `background`, `action_candidate`, `outcome_candidate`, `supporting_context`, and `not_relevant`.
- Upstream MUST NOT emit final V3 action/outcome labels such as `action_type_he`, `action_subtype_he`, `other_action_type_he`, `decision_outcome`, or final `event_phase`. It may emit only generic span hints.

Use richer `topic_assignment` to mean root/child topic ids, labels, confidence, route, status, `is_topic_bearing`, `row_type`, and supporting quote when available.

Note that `/Users/igor/Desktop/projects/Municipality/scripts/import_pdf_first_v4_retrieval_chunks.py` already preserves many of these fields in `_metadata_for_chunk(...)`, but this prompt makes the contract non-optional.

Please consider adding or improving upstream output fields in `retrieval_chunks.json` and DB import metadata:

1. Stable Structure Unit Metadata

For each retrieval chunk, preserve:

- `structure_unit_id`
- `semantic_unit_id`
- `source_semantic_unit_ids`
- `source_block_ids`
- `source_region_ids`
- `structural_role`
- `section_id`
- `section_number`
- `page`
- char offsets if available

These should be stored in `RetrievalArtifact.metadata_json` by `/Users/igor/Desktop/projects/Municipality/scripts/import_pdf_first_v4_retrieval_chunks.py` so V3 can load them without reading intermediate JSON files.

2. Action-Bearing Span Hints

Add generic span hints to retrieval chunks when structure normalization can identify them without doing V3’s semantic extraction:

- `span_id`
- `span_role`: `structural`, `background`, `action_candidate`, `outcome_candidate`, `supporting_context`, `not_relevant`
- `raw_text`
- `corrected_text`
- `char_start`, `char_end` if available
- source page/block/region ids

Do not decide the final action label here. Just expose good evidence boundaries.

3. Clean But Auditable Text

V3 needs both:

- exact/auditable raw text for quote grounding
- cleaned text for semantic interpretation

Please keep both, instead of replacing one with the other. Recommended fields:

- `raw_text`: exact or near-exact source unit text
- `corrected_text_he`: OCR/spacing-normalized text without adding facts
- `summary_he`: short human/model summary, explicitly marked as summary, not source evidence

4. Better Chunk Granularity

Avoid retrieval chunks that combine too much unrelated text. In particular, try to split or annotate rows where:

- background text precedes a late action sentence
- a protocol opening/closing is mixed with a decision
- participant/header/footer material is attached to event content
- one paragraph contains multiple procedural phases

If splitting is risky, preserve the full chunk but add span hints so V3 can anchor to the correct span.

5. Structural Role Consistency

Use stable, generic structural roles. Examples that help V3:

- `meeting_header`
- `protocol_header`
- `document_date`
- `signature_footer`
- `agenda_heading`
- `event_title`
- `background_context`
- `action_candidate`
- `decision_result`
- `vote_metadata`
- `attachment_reference`
- `insufficient_context`

Avoid document-specific labels. If uncertain, use a broad stable label plus confidence/metadata.

## How You Can Save Speed

1. Make V3 able to skip obvious structural rows using upstream `structural_role` instead of asking the model.

2. Provide selected action/outcome candidate spans so V3 prompts can include fewer full rows.

3. Store all needed structure metadata in DB `RetrievalArtifact.metadata_json` so V3 does not need to reload large step outputs.

4. Add document-version filters to benchmark/extraction flows if you touch orchestration. Current V3 benchmark samples by municipality offset, which is awkward for testing specific protocols.

5. Keep imported artifacts stable by preserving `retrieval_artifact_id`, `structure_unit_id`, and `semantic_unit_id`. Stable IDs allow caching and comparing V3 reruns.

## How You Can Save Quality

1. Ensure `body_text` is not overly raw if a better source unit text exists. Right now V3 prefers `body_text` over `retrieval_text`.

2. Preserve exact evidence quotes separately from summaries. V3 should not use summaries as quote evidence.

3. Mark rows that are purely structural before topic assignment when possible. This prevents non-events from entering V3 as topic-bearing actions.

4. Preserve neighboring row/block relationships. V3 currently uses nearby rows by ordinal, but upstream block/section relationships would be better.

5. Separate protocol lifecycle text from municipal action text when possible. Examples: meeting opened, meeting closed, participants, signatures, protocol metadata.

## Recent Benchmark Findings From V3

On the latest 18-row benchmark over existing processed Ashdod/Tel Aviv rows:

- Rows: 18
- Events: 10
- Non-events: 8
- Failed rows: 0
- `event_role` leaks: 0

Problem types still visible:

- Some accepted event labels are too verbose when evidence quotes are mixed into the display.
- Rows describing operational activity/drills currently use `action_type_he=אחר` because the controlled ontology lacks a reusable action for exercise/preparation/activity.
- V3 had to add its own span splitting to handle long mixed rows. Upstream span hints would make this faster and more reliable.

## Suggested Joint Contract

Please aim for a retrieval artifact contract like this:

```json
{
  "artifact_id": "stable id",
  "raw_text": "exact source unit text",
  "corrected_text_he": "safe OCR/spacing-normalized text",
  "summary_he": "summary, not evidence",
  "structural_role": "stable generic role",
  "structure_unit_id": "stable id",
  "semantic_unit_id": "stable id",
  "source_block_ids": ["..."],
  "source_region_ids": ["..."],
  "spans": [
    {
      "span_id": "stable span id",
      "span_role": "structural|background|action_candidate|outcome_candidate|supporting_context|not_relevant",
      "raw_text": "exact span",
      "corrected_text_he": "safe normalized span",
      "char_start": 0,
      "char_end": 120,
      "source_block_ids": ["..."],
      "source_region_ids": ["..."]
    }
  ],
  "evidence_refs": [],
  "topic_assignment": {
    "root_topic_id": "...",
    "child_topic_id": "...",
    "confidence": 0.0,
    "route": "..."
  }
}
```

This contract lets V3 focus on event/action/matter/outcome extraction rather than rediscovering document structure.

## Required Step 5 + Import Contract

Step 5 `retrieval_chunks.json` MUST emit:

- `corrected_text_he`
- `spans`
- richer `topic_assignment`
- explicit `structure_metadata`

`raw_text` MUST remain unchanged for quote grounding.

`summary_he` MUST be treated as summary only, never evidence.

DB import MUST preserve `RetrievalArtifact.body_text = raw_text`.

DB import MUST store cleaned text, spans, provenance, structure metadata, and topic assignment in `RetrievalArtifact.metadata_json`.

Allowed `span_role` values are only:

- `structural`
- `background`
- `action_candidate`
- `outcome_candidate`
- `supporting_context`
- `not_relevant`

Upstream MUST NOT emit final V3 action/outcome labels such as `action_type_he`, `action_subtype_he`, `other_action_type_he`, `decision_outcome`, or final `event_phase`. It may emit only generic span hints.

`topic_assignment` MUST include the richer topic metadata V3 needs:

- root topic id and label
- child topic id and label
- confidence
- route
- status
- `is_topic_bearing`
- `row_type`
- supporting quote

Note: `/Users/igor/Desktop/projects/Municipality/scripts/import_pdf_first_v4_retrieval_chunks.py` already preserves many of these fields in `_metadata_for_chunk(...)`, but this prompt makes the contract non-optional.

## Please Avoid

- Do not hardcode specific municipalities or protocol formats.
- Do not add topic/action labels from one example only.
- Do not replace raw evidence text with summaries.
- Do not collapse multiple distinct procedural phases into one opaque chunk when avoidable.
- Do not remove source block/page/region provenance.

## Coordination Request

If you make upstream changes, please provide:

- a short description of new/changed fields in `retrieval_chunks.json`
- how those fields are stored in DB `RetrievalArtifact.metadata_json`
- representative examples from multiple protocols/municipalities
- any changed assumptions about `body_text` vs `retrieval_text`
- expected impact on V3 speed and prompt size

The V3 agent can then update `/Users/igor/Desktop/projects/Municipality/src/municipality/topic_subjects.py` to consume those fields directly.
