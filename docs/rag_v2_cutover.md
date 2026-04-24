# RAG V2 Cutover

This repository now runs the hierarchy-aware Hebrew municipal RAG redesign as the active architecture.

## What Changed

- Added structure-aware storage in `migrations/012_m12_rag_v2_structure.sql`:
  - `document_section`
  - `retrieval_artifact`
  - `retrieval_artifact_trigram`
  - `retrieval_artifact_embedding`
  - `artifact_fts`
- Added section/artifact builder in `src/municipality/rag_structure.py`.
- Added artifact indexing in `src/municipality/structured_indexing.py`.
- Added v2 lexical retrieval backend in `src/municipality/artifact_search.py`.
- Added v2 embedding backend in `src/municipality/artifact_embeddings.py`.
- Added artifact-native semantic links in `migrations/013_m13_rag_v2_semantic_links.sql`.
- Added legacy-schema removal and artifact-native decision-context migration in `migrations/014_m14_remove_legacy_chunk_rag.sql`.
- Added AI21 answer-provider support in `src/municipality/rag_llm.py`.
- Added AI21 semantic-extraction support in `src/municipality/semantic_extractor.py`.

## Runtime Flags

- `RAG_V2_INDEX_BUILD_ENABLED=true|false`
  - Controls whether processing writes structure/artifact rows.
  - Default: enabled.

## Current Shape

- Processing writes `document_section` and `retrieval_artifact` only for RAG retrieval.
- `/ask`, `/search`, and `/ask/debug/retrieval` read artifact-backed retrieval only.
- Semantic search/filtering is artifact-native through `artifact_semantic_link`.
- Decision-context provenance is stored as `source_artifact_ids_json`.
- V2 citations carry `header_path` in addition to page references.
- Retrieval-set IDs remain versioned through the retrieval version string.

## Backfill Script

- `python scripts/backfill_rag_v2_structure_from_extracted.py`
- Full artifact/semantic/context rebuild from extracted docs:
  - `python scripts/rebuild_rag_redesign_from_extracted.py`
- Recommended local embedding mode for backfill without external API keys:

```bash
RAG_EMBEDDING_PROVIDER=local_hash python scripts/backfill_rag_v2_structure_from_extracted.py
```

The full rebuild script replays the latest completed recorded semantic run for each document version by default and avoids fresh semantic model calls unless `--allow-fresh-semantic-calls` is provided.

## Obsolete Logic Status

- Legacy summary-cache persistence remains a retired no-op.
- The retired `/topic/tree/cache` endpoint remains retired and points callers to `/semantic/tree`.
- Legacy chunk-first tables and runtime search/index paths have been removed from the active design.

## Architecture Notes

- See `docs/rag_current_design.md` for the current end-to-end diagram and detailed explanation.
