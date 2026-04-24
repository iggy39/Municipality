from __future__ import annotations

from municipality.rag_retrieval import RagRetrievalService
from municipality.search import SearchHit, SemanticDebugNode


class StubSearchService:
    def __init__(self, responses_by_source: dict[str, list[SearchHit]]):
        self.responses_by_source = responses_by_source
        self.calls: list[dict] = []

    def search(self, **kwargs) -> list[SearchHit]:
        self.calls.append(kwargs)
        source = kwargs.get("source_type") or "all"
        return list(self.responses_by_source.get(source, []))


class StubReranker:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def initial_candidate_limit(self, *, top_k: int) -> int:
        return max(9, top_k)

    def rerank_hits(self, *, query: str, hits: list[SearchHit], top_k: int) -> tuple[list[SearchHit], dict]:
        self.calls.append({"query": query, "top_k": top_k, "chunk_ids": [hit.chunk_id for hit in hits]})
        if len(hits) >= 2:
            hits[0].score = 0.2
            hits[1].score = 0.99
        return list(reversed(hits)), {"enabled": True, "candidate_count": len(hits)}


def test_rag_retrieval_backfills_missing_source_kinds_and_keeps_citations() -> None:
    protocol_hit = _hit(chunk_id="p-1", source_type="protocol", score=0.91, citation="pp.2-3")
    attachment_hit = _hit(chunk_id="a-1", source_type="attachment", score=0.88, citation="p.1")
    search = StubSearchService(
        {
            "all": [protocol_hit],
            "attachment": [attachment_hit],
        }
    )
    retrieval = RagRetrievalService(search_service=search)

    result = retrieval.retrieve(
        query="בטיחות והסכם",
        source_kinds=["protocol", "attachment"],
        top_k=5,
        semantic_mode="off",
    )

    assert {item.source_kind for item in result.contexts} == {"protocol", "attachment"}
    assert {item.citation for item in result.contexts} == {"pp.2-3", "p.1"}
    assert any(call.get("source_type") == "attachment" for call in search.calls)
    assert isinstance(result.retrieval_set_id, str)
    assert result.retrieval_set_id


def test_rag_retrieval_source_filter_and_top_k_are_tunable() -> None:
    search = StubSearchService(
        {
            "all": [
                _hit(chunk_id="p-1", source_type="protocol", score=0.95, citation="p.1"),
                _hit(chunk_id="p-2", source_type="protocol", score=0.89, citation="p.2"),
                _hit(chunk_id="a-1", source_type="attachment", score=0.93, citation="p.1"),
            ]
        }
    )
    retrieval = RagRetrievalService(search_service=search)

    result = retrieval.retrieve(
        query="תקציב",
        source_kinds=["protocol"],
        top_k=1,
        semantic_mode="off",
    )

    assert len(result.contexts) == 1
    assert result.contexts[0].source_kind == "protocol"
    assert result.contexts[0].chunk_id == "p-1"
    assert isinstance(result.retrieval_set_id, str)
    assert result.retrieval_set_id


def test_rag_retrieval_forwards_semantic_selector_params_to_search() -> None:
    search = StubSearchService(
        {
            "all": [
                _hit(chunk_id="p-1", source_type="protocol", score=0.95, citation="p.1"),
            ]
        }
    )
    retrieval = RagRetrievalService(search_service=search)

    result = retrieval.retrieve(
        query="תחבורה",
        source_kinds=["protocol"],
        semantic_mode="filter",
        semantic_node_id=77,
        semantic_label="תחבורה עירונית",
        top_k=3,
    )

    assert result.contexts
    assert search.calls
    first_call = search.calls[0]
    assert first_call["semantic_mode"] == "filter"
    assert first_call["semantic_node_id"] == 77
    assert first_call["semantic_label"] == "תחבורה עירונית"
    assert "artifact_kinds" in first_call
    assert result.debug_info["query_rewrite"]["retrieval_strategy"] in {"segments", "headers", "neighbors", "full_doc"}


def test_rag_retrieval_keeps_direct_artifact_ranking_for_broad_decision_queries() -> None:
    search = StubSearchService(
        {
            "all": [
                _hit(chunk_id="p-10-generic", source_type="protocol", score=0.93, citation="p.1", document_id=10),
                _hit(
                    chunk_id="p-11-decision",
                    source_type="protocol",
                    score=0.9,
                    citation="p.2",
                    document_id=11,
                    chunk_text="הוחלט לאשר תכנית בטיחות.",
                ),
                _hit(chunk_id="p-12-generic", source_type="protocol", score=0.88, citation="p.3", document_id=12),
            ],
            "protocol": [
                _hit(
                    chunk_id="p-10-decision",
                    source_type="protocol",
                    score=0.72,
                    citation="p.9",
                    document_id=10,
                    chunk_text="הוחלט לקדם הכשרות ייעודיות.",
                ),
                _hit(
                    chunk_id="p-12-decision",
                    source_type="protocol",
                    score=0.71,
                    citation="p.8",
                    document_id=12,
                    chunk_text="מאשרים הוצאת הזמנה תקציבית.",
                ),
            ],
        }
    )
    retrieval = RagRetrievalService(search_service=search)

    result = retrieval.retrieve(
        query="מה הוחלט בעיר?",
        source_kinds=["protocol"],
        top_k=3,
        semantic_mode="off",
    )

    assert len(result.contexts) == 3
    assert {context.document_id for context in result.contexts} == {10, 11, 12}
    assert {context.chunk_id for context in result.contexts} == {
        "p-10-generic",
        "p-11-decision",
        "p-12-generic",
    }
    assert all(call.get("source_type") in {None, "all"} for call in search.calls)


def test_rag_retrieval_exposes_topic_semantic_labels_in_context() -> None:
    hit = _hit(chunk_id="p-1", source_type="protocol", score=0.95, citation="p.1")
    hit.semantic_nodes = [
        SemanticDebugNode(
            id=3,
            label="בטיחות בדרכים",
            kind="topic",
            semantic_type="road_safety",
            confidence=0.91,
        ),
        SemanticDebugNode(
            id=4,
            label="עזרא שור",
            kind="entity",
            semantic_type="person",
            confidence=0.84,
        ),
    ]

    search = StubSearchService({"all": [hit]})
    retrieval = RagRetrievalService(search_service=search)

    result = retrieval.retrieve(query="בטיחות", source_kinds=["protocol"], top_k=3, semantic_mode="off")

    assert len(result.contexts) == 1
    assert result.contexts[0].semantic_topic_labels == ["בטיחות בדרכים"]


def test_rag_retrieval_preserves_primary_topics_from_hits() -> None:
    hit = _hit(chunk_id="p-1", source_type="protocol", score=0.95, citation="p.1")
    hit.primary_topic = "תחבורה ובטיחות > הרחבת חניה ליד בית הספר"
    hit.secondary_topics = ["הרחבת חניה ליד בית הספר"]

    search = StubSearchService({"all": [hit]})
    retrieval = RagRetrievalService(search_service=search)

    result = retrieval.retrieve(query="חניה ליד בית הספר", source_kinds=["protocol"], top_k=3, semantic_mode="off")

    assert result.contexts[0].primary_topic == "תחבורה ובטיחות > הרחבת חניה ליד בית הספר"
    assert result.contexts[0].secondary_topics == ["הרחבת חניה ליד בית הספר"]


def test_rag_retrieval_applies_optional_reranker_before_limiting_results() -> None:
    search = StubSearchService(
        {
            "all": [
                _hit(chunk_id="p-1", source_type="protocol", score=0.95, citation="p.1"),
                _hit(chunk_id="p-2", source_type="protocol", score=0.8, citation="p.2"),
            ]
        }
    )
    reranker = StubReranker()
    retrieval = RagRetrievalService(search_service=search, reranker=reranker)

    result = retrieval.retrieve(query="תקציב", source_kinds=["protocol"], top_k=1, semantic_mode="off")

    assert result.contexts[0].chunk_id == "p-2"
    assert result.debug_info["embedding_rerank"]["enabled"] is True
    assert result.debug_info["embedding_rerank"]["candidate_count"] == 2
    assert reranker.calls
    assert search.calls[0]["limit"] == 9


def _hit(
    *,
    chunk_id: str,
    source_type: str,
    score: float,
    citation: str,
    document_id: int = 1,
    document_title: str = "כותרת",
    chunk_text: str = "snippet",
) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        score=score,
        snippet="snippet",
        citation=citation,
        source_type=source_type,
        document_id=document_id,
        document_url="https://example.local/doc.pdf",
        document_title=document_title,
        municipality_slug="ashdod",
        meeting_external_id="meeting:1",
        start_offset=None,
        end_offset=None,
        start_page=1,
        end_page=1,
        chunk_text=chunk_text,
    )
