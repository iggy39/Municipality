from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from municipality.embeddings import ChunkEmbeddingService, EmbeddingConfig
from municipality.migrations import apply_all
from municipality.rag_answer_cache import RagAnswerCacheService


class StubEmbeddingClient:
    def __init__(self, vectors_by_text: dict[str, list[float]]) -> None:
        self.vectors_by_text = vectors_by_text
        self.calls: list[list[str]] = []

    @property
    def provider_name(self) -> str:
        return "StubOpenAI"

    @property
    def model_name(self) -> str:
        return "stub-embed"

    @property
    def dimensions(self) -> int:
        return 2

    def is_configured(self) -> bool:
        return True

    def embed_texts(self, texts):
        self.calls.append(list(texts))
        return [list(self.vectors_by_text[text]) for text in texts]


def test_answer_cache_reuses_semantically_identical_query_for_same_retrieval_set(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'answer-cache.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        client = StubEmbeddingClient(
            {
                "תקציב חינוך": [0.0, 1.0],
                "מה הוחלט על תקציב חינוך": [0.0, 1.0],
            }
        )
        embedding_service = ChunkEmbeddingService(
            session,
            model_client=client,
            config=EmbeddingConfig(enabled=True, api_key="test-key", model_name="stub-embed", dimensions=2),
        )
        cache = RagAnswerCacheService(session, embedding_service=embedding_service)

        cache.store(
            query="תקציב חינוך",
            query_hash="q1",
            retrieval_set_id="retrieval-1",
            answer_provider="DeterministicExtractive",
            answer_model="decision_embedding_match_v1",
            answer_payload={"status": "answer", "answer": "אושר תקציב חינוך", "citations": [], "answer_sections": [], "extended_answer_sections": [], "claim_assessments": [], "limitations": [], "scoring": {}},
        )

        hit = cache.lookup(query="מה הוחלט על תקציב חינוך", retrieval_set_id="retrieval-1")

        assert hit is not None
        assert hit.payload["answer"] == "אושר תקציב חינוך"
        assert hit.similarity == 1.0


def test_answer_cache_skips_degraded_deterministic_fallback_payloads(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'answer-cache-skip.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        client = StubEmbeddingClient(
            {
                "תקציב חינוך": [0.0, 1.0],
                "מה הוחלט על תקציב חינוך": [0.0, 1.0],
            }
        )
        embedding_service = ChunkEmbeddingService(
            session,
            model_client=client,
            config=EmbeddingConfig(enabled=True, api_key="test-key", model_name="stub-embed", dimensions=2),
        )
        cache = RagAnswerCacheService(session, embedding_service=embedding_service)

        cache.store(
            query="תקציב חינוך",
            query_hash="q1",
            retrieval_set_id="retrieval-1",
            answer_provider="DeterministicExtractive",
            answer_model="decision_embedding_match_v1",
            answer_payload={
                "status": "answer",
                "answer": "אושר תקציב חינוך",
                "citations": [],
                "answer_sections": [],
                "extended_answer_sections": [],
                "claim_assessments": [],
                "limitations": [],
                "scoring": {"answer_generation_route": "extractive_decision_match"},
            },
        )
        cache.store(
            query="תקציב חינוך",
            query_hash="q2",
            retrieval_set_id="retrieval-1",
            answer_provider="DeterministicExtractive",
            answer_model="retrieval_fallback_v1",
            answer_payload={
                "status": "answer",
                "answer": "תשובה לא יציבה",
                "citations": [],
                "answer_sections": [],
                "extended_answer_sections": [],
                "claim_assessments": [],
                "limitations": [],
                "scoring": {
                    "answer_generation_route": "deterministic_retrieval_fallback",
                    "degraded_upstream_failure": True,
                },
            },
        )

        hit = cache.lookup(query="מה הוחלט על תקציב חינוך", retrieval_set_id="retrieval-1")

        assert hit is not None
        assert hit.payload["answer"] == "אושר תקציב חינוך"
