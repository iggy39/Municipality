# Current RAG Design

```text
                               HEBREW MUNICIPAL RAG (CURRENT)

 [PDF / protocol / attachment]
            |
            v
   PdfTextExtractor
   - PyMuPDF layout parse when available
   - pdftotext -layout fallback
   - bidi cleanup
   - per-page reading_direction
   - per-page layout_blocks with bbox/font/alignment
   `src/municipality/extraction.py`
            |
            v
   ExtractedDocument
   - extracted_text
   - pages_json
   - citation_map_json
            |
            +------------------------------+
            |                              |
            v                              v
   DecisionExtractionService        StructuredIndexingService
   - meeting / decision rows        - build_structured_document()
   - citations                      - document_section rows
   `src/municipality/decisions.py`  - retrieval_artifact rows
                                     - retrieval_artifact_embedding rows
                                     - artifact_topic_annotation rows
                                     `src/municipality/structured_indexing.py`
                                                |
                                                v
                                     ArtifactSearchService
                                     - FTS + trigram lexical search
                                     - topic annotation boost
                                     - semantic boost / filter via
                                       artifact_semantic_link
                                     `src/municipality/artifact_search.py`
                                                |
                                                v
                                     QueryRewriteService
                                     - query rewrite bundle
                                     - artifact kind routing
                                     - strategy selection
                                     `src/municipality/query_rewrite.py`
                                                |
                                                v
                                     RagRetrievalService
                                     - staged artifact-kind retrieval
                                     - source coverage backfill
                                     - embedding rerank
                                     - retrieval_set_id
                                     `src/municipality/rag_retrieval.py`
                                                |
                                                v
                                     /ask pipeline
                                     - protocol subject anchors
                                     - protocol semantic topic labels
                                     - decision request context by artifact id
                                     - answer cache
                                     - grounded answer compose / verify
                                     - optional AI21 rewrite
                                     `src/municipality/api.py`
                                     `src/municipality/rag_answering.py`


                    SEMANTIC ENRICHMENT LANE

   ExtractedDocument text
            |
            v
   semantic_prompt evidence packet
   - headings
   - evidence lines
   - artifact_evidence with header_path/topic priors
   - entity hints
   `src/municipality/semantic_prompt.py`
            |
            v
   Bytez / AI21 semantic extractor
   `src/municipality/semantic_extractor.py`
            |
            v
   SemanticService persistence
   - semantic_node
   - semantic_alias
   - semantic_mention
   - semantic_edge
   - decision_semantic_link
   - artifact_semantic_link
   `src/municipality/semantic_service.py`


                    DECISION CONTEXT LANE

   decision + decision_citation + retrieval_artifact windows
            |
            v
   DecisionContextService
   - request_subject_he
   - subject_topic_he
   - address / gush / helka / migrash
   - source_artifact_ids_json
   `src/municipality/decision_context.py`
```

## How It Works

1. Extraction
- `PdfTextExtractor` first tries a layout-aware PyMuPDF parse and stores richer line blocks with bounding boxes, font size, font weight, alignment, and role guesses.
- If that path is unavailable, it falls back to `pdftotext -layout` and still stores normalized page payloads in `pages_json`.

2. Structure build
- `StructuredIndexingService` feeds extracted text and layout pages into `build_structured_document()`.
- `rag_structure` classifies heading candidates from layout/text signals, preserves hierarchy in `document_section`, and emits multiple artifact kinds: `document_profile`, `header_anchor`, `header_plus_opening`, `section_summary`, `section_unit`, `decision_unit`, and `context_window`.
- Every artifact carries page spans, offsets, `artifact_kind`, and `header_path_json`.
- `ArtifactTopicClassifier` persists structural/refined topic annotations in `artifact_topic_annotation`.

3. Retrieval
- `QueryRewriteService` rewrites Hebrew questions into retrieval bundles with lexical terms, semantic terms, header terms, artifact-kind priorities, and a retrieval strategy.
- `ArtifactSearchService` searches `artifact_fts` and `retrieval_artifact_trigram`, then boosts by semantic matches and persisted topic annotations.
- `RagRetrievalService` executes staged retrieval by artifact kind, optionally expands into context windows, reranks with embeddings, and emits the final context set.

4. Semantic enrichment
- The semantic extractor consumes compact evidence packets built from extracted text plus artifact evidence carrying `header_path`, `artifact_kind`, and stored topic priors.
- Accepted nodes and mentions are persisted once, then linked directly to retrieval artifacts through overlap-based artifact linking.
- This makes semantic retrieval artifact-native rather than flat-chunk-native.

5. Decision context
- Decision citations anchor into nearby retrieval artifacts, not chunk windows.
- The resulting `decision_request_context` row stores artifact provenance in `source_artifact_ids_json`.
- That context is later used to sharpen topic assignment and answer rewriting for municipal allocation / agreement decisions.

6. Answering
- `/ask` loads retrieval contexts, topic anchors, semantic labels, persisted artifact topics, and artifact-based decision context.
- `RagAnsweringService` now prefers persisted artifact topics during section topic assignment, then performs grounded answer drafting, deterministic validation, citation assembly, and optional AI21 rewrite polishing.
- Citations returned to clients include page spans and `header_path` so answers remain traceable back into section hierarchy.

## Main Tables

- `extracted_document`: raw extracted text and page/layout payloads.
- `document_section`: preserved document hierarchy.
- `retrieval_artifact`: retrieval units used by search and answering.
- `artifact_topic_annotation`: persisted structural + refined topic labels and section summaries per artifact.
- `retrieval_artifact_embedding`: vector embeddings for rerank and cache similarity.
- `semantic_node` / `semantic_alias` / `semantic_mention` / `semantic_edge`: semantic graph.
- `artifact_semantic_link`: semantic-to-artifact grounding.
- `decision_request_context`: artifact-grounded municipal request context.
- `rag_answer_cache`: retrieval-set-scoped answer cache.
