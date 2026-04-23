# RAG V2 Cutover

This repository now includes the first buildable slice of the hierarchy-aware Hebrew municipal RAG redesign.

## What Changed

- Added `RAG_ARCH_VERSION` with `v1`/`v2` in `src/municipality/rag_arch.py`.
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
- Added runtime backend selection in `src/municipality/rag_backend.py`.
- Added AI21 answer-provider support in `src/municipality/rag_llm.py`.

## Runtime Flags

- `RAG_ARCH_VERSION=v1|v2`
  - `v1`: existing chunk-first read path.
  - `v2`: retrieval prefers `retrieval_artifact` and falls back to legacy chunks until v2 artifacts exist.
- `RAG_V2_INDEX_BUILD_ENABLED=true|false`
  - Controls whether processing writes v2 sections/artifacts.
  - Default: enabled when `RAG_ARCH_VERSION=v2`.

## Current Cutover Shape

- Processing can keep writing legacy chunks while also writing v2 sections/artifacts.
- `/ask`, `/search`, and `/ask/debug/retrieval` now prefer v2 automatically once `retrieval_artifact` rows exist.
- V2 citations now carry `header_path` in addition to page references.
- Retrieval-set IDs are versioned through the retrieval version string, preventing cache collisions across architectures.

## Backfill Script

- `python scripts/backfill_rag_v2_structure_from_extracted.py`
- Recommended local embedding mode for backfill without external API keys:

```bash
RAG_EMBEDDING_PROVIDER=local_hash python scripts/backfill_rag_v2_structure_from_extracted.py
```

## Obsolete Logic Status

- Legacy summary-cache persistence remains a retired no-op.
- The retired `/topic/tree/cache` endpoint remains retired and points callers to `/semantic/tree`.
- Old chunk-first infrastructure still exists because decision extraction and some semantic helpers still depend on it during the phased cutover.

## Next Removal Targets

1. Replace chunk-based decision-context derivation with section/artifact-aware context derivation.
2. Move semantic chunk links to section/artifact links.
3. Remove unused topic-tree fallback heuristics from `src/municipality/rag_answering.py` and `src/municipality/api.py`.
4. Delete legacy chunk-first retrieval code after v2 evaluation/backfill signoff.
