from __future__ import annotations

from municipality.rag_retrieval import RagRetrievalService
from municipality.search import SearchHit


class StubSearchService:
    def __init__(self, responses_by_source: dict[str, list[SearchHit]]):
        self.responses_by_source = responses_by_source
        self.calls: list[dict] = []

    def search(self, **kwargs) -> list[SearchHit]:
        self.calls.append(kwargs)
        source = kwargs.get("source_type") or "all"
        return list(self.responses_by_source.get(source, []))


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


def _hit(*, chunk_id: str, source_type: str, score: float, citation: str) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        score=score,
        snippet="snippet",
        citation=citation,
        source_type=source_type,
        document_id=1,
        document_url="https://example.local/doc.pdf",
        document_title="כותרת",
        municipality_slug="ashdod",
        meeting_external_id="meeting:1",
        start_page=1,
        end_page=1,
    )
