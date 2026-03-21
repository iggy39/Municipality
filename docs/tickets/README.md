# Milestone Ticket Boards

This directory breaks `docs/AGENT_IMPLEMENTATION_PLAN.md` into concrete, executable ticket boards per milestone.

## How to use

1. Complete milestones in order: `M1 -> M2 -> M3 -> M4 -> M5 -> M6`.
2. Within each milestone, execute tickets top-to-bottom unless the ticket explicitly says parallel-safe.
3. Do not start the next milestone until all exit-gate checkboxes are done.
4. Every ticket must produce artifacts (code, tests, docs, or reports) listed in its "Done when" section.

## Ticket status convention

- `[ ]` not started
- `[-]` in progress
- `[x]` complete
- `[!]` blocked

## Deliverable map

- `M1_discovery_versioning.md`
- `M2_extraction_search.md`
- `M3_decisions_citations.md`
- `M4_rag_citations.md`
- `M5_geo_step5.md`
- `M6_timeline_trackA.md`
- `GEO_phase1.md`
- `GEO_phase2.md`

## Global guardrails for all milestones

- Preserve provenance for all outputs.
- Never invent source facts in generated outputs.
- Keep all IDs deterministic and stable across reruns.
- Ensure reruns are idempotent.
- Keep local deployment compatibility.
- Keep Hebrew-first behavior in user-facing components.
- Keep source assumptions explicit:
  - Current pilot source is PDF-only for protocols and attachments.
  - If non-PDF appears, store metadata with `UNSUPPORTED_MIME` and skip extraction.

## Mandatory QA artifacts per milestone

Each milestone must end with:

- A short pass/fail report under `eval/reports/`.
- A list of known defects and scope decisions.
- A rerun note proving idempotency for that milestone scope.
