from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from municipality.chunking import build_chunks
from municipality.embeddings import (
    ChunkEmbeddingLookupResult,
    ChunkEmbeddingService,
    EmbeddingConfig,
    EmbeddingReranker,
)
from municipality.migrations import apply_all
from municipality.models import Document, DocumentVersion, ExtractedDocument, SourceSite
from municipality.search import SearchHit, SearchService


class StubEmbeddingClient:
    def __init__(self, vectors_by_text: dict[str, list[float]], *, model_name: str = "stub-embed") -> None:
        self.vectors_by_text = vectors_by_text
        self.calls: list[list[str]] = []
        self._model_name = model_name

    @property
    def provider_name(self) -> str:
        return "StubOpenAI"

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        first_vector = next(iter(self.vectors_by_text.values()))
        return len(first_vector)

    def is_configured(self) -> bool:
        return True

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [list(self.vectors_by_text[text]) for text in texts]


class StubRerankEmbeddingService:
    def __init__(self) -> None:
        self.config = EmbeddingConfig(
            enabled=True,
            api_key="test-key",
            model_name="stub-embed",
            dimensions=2,
            rerank_weight=0.65,
            rerank_candidate_multiplier=2,
            rerank_min_candidates=2,
            rerank_max_candidates=2,
        )

    def is_enabled(self) -> bool:
        return True

    def initial_candidate_limit(self, *, top_k: int) -> int:
        return max(2, top_k)

    def embed_query(self, query: str) -> list[float] | None:
        if query == "תקציב":
            return [0.0, 1.0]
        return None

    def ensure_embeddings_for_hits(self, *, hits: list[SearchHit]) -> ChunkEmbeddingLookupResult:
        vectors = {
            "chunk-a": [1.0, 0.0],
            "chunk-b": [0.0, 1.0],
        }
        return ChunkEmbeddingLookupResult(vectors_by_chunk_id=vectors, created=1, cached=1)


def test_chunk_embedding_service_caches_vectors_per_chunk(tmp_path: Path) -> None:
    db_path = tmp_path / "embeddings.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    chunk_text = "נושא תקציב חינוך עירוני " * 30

    with Session(engine) as session:
        chunks = _seed_chunks(session, text=chunk_text)
        client = StubEmbeddingClient({chunks[0]["chunk_text"]: [0.1, 0.9]})
        service = ChunkEmbeddingService(
            session,
            model_client=client,
            config=EmbeddingConfig(enabled=True, api_key="test-key", model_name="stub-embed", dimensions=2),
        )

        first = service.index_chunks(chunks=chunks)
        session.commit()
        second = service.index_chunks(chunks=chunks)

        assert first.enabled is True
        assert first.created == 1
        assert first.cached == 0
        assert second.created == 0
        assert second.cached == 1
        assert len(client.calls) == 1
        assert service.load_vectors([chunks[0]["chunk_id"]])[chunks[0]["chunk_id"]] == [0.1, 0.9]


def test_embedding_reranker_reorders_hits_using_similarity() -> None:
    reranker = EmbeddingReranker(StubRerankEmbeddingService())
    hits = [
        _hit(chunk_id="chunk-a", score=0.9, chunk_text="alpha"),
        _hit(chunk_id="chunk-b", score=0.5, chunk_text="beta"),
    ]

    reranked, stats = reranker.rerank_hits(query="תקציב", hits=hits, top_k=2)

    assert [hit.chunk_id for hit in reranked[:2]] == ["chunk-b", "chunk-a"]
    assert stats["query_embedding_used"] is True
    assert stats["available_chunk_embeddings"] == 2
    assert stats["created_chunk_embeddings"] == 1


def _seed_chunks(session: Session, *, text: str) -> list[dict]:
    site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
    session.add(site)
    session.flush()

    document = Document(
        source_site_id=site.id,
        document_external_id="doc-embed-1",
        canonical_url="https://example.local/protocol-1.pdf",
        title_he="פרוטוקול ועדת תקציב",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    session.add(document)
    session.flush()

    version = DocumentVersion(
        document_id=document.id,
        sha256="1" * 64,
        byte_size=100,
        storage_uri="tree/ashdod/protocol-1.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add(version)
    session.flush()

    extracted = ExtractedDocument(
        document_version_id=version.id,
        parser_name="stub",
        parser_version="1",
        status="completed",
        extracted_text=text,
        page_count=1,
        pages_json="[]",
        citation_map_json="[]",
        quality_score=1.0,
        quality_flags_json="[]",
        quality_summary_json="{}",
        error_code=None,
        warning_text=None,
    )
    session.add(extracted)
    session.flush()

    chunks = build_chunks(
        document_version_id=version.id,
        text=text,
        citation_map=[{"start": 0, "end": len(text), "page": 1}],
        source_kind="protocol",
    )
    SearchService(session).replace_document_chunks(
        document_id=document.id,
        document_version_id=version.id,
        extracted_document_id=extracted.id,
        source_kind="protocol",
        chunks=chunks,
    )
    session.flush()
    return chunks


def _hit(*, chunk_id: str, score: float, chunk_text: str) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        score=score,
        snippet=chunk_text,
        citation="p.1",
        source_type="protocol",
        document_id=1,
        document_url="https://example.local/doc.pdf",
        document_title="פרוטוקול",
        municipality_slug="ashdod",
        meeting_external_id="meeting:1",
        start_offset=None,
        end_offset=None,
        start_page=1,
        end_page=1,
        chunk_text=chunk_text,
    )
