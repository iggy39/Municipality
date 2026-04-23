from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from municipality.artifact_embeddings import ArtifactEmbeddingService
from municipality.artifact_search import ArtifactSearchService
from municipality.embeddings import ChunkEmbeddingService, EmbeddingConfig, EmbeddingModelClient
from municipality.models import RetrievalArtifact
from municipality.rag_arch import RagArchitectureConfig
from municipality.search import SearchService


def build_search_backend(*, session: Session, arch_config: RagArchitectureConfig | None = None):
    resolved_arch = arch_config or RagArchitectureConfig.from_env()
    if resolved_arch.uses_v2 and _has_v2_artifacts(session):
        return ArtifactSearchService(session)
    return SearchService(session)


def build_embedding_backend(
    *,
    session: Session,
    arch_config: RagArchitectureConfig | None = None,
    model_client: EmbeddingModelClient | None = None,
    config: EmbeddingConfig | None = None,
): 
    resolved_arch = arch_config or RagArchitectureConfig.from_env()
    service_cls = ArtifactEmbeddingService if resolved_arch.uses_v2 and _has_v2_artifacts(session) else ChunkEmbeddingService
    return service_cls(session, model_client=model_client, config=config)


def _has_v2_artifacts(session: Session) -> bool:
    try:
        count = session.execute(select(func.count(RetrievalArtifact.id))).scalar_one()
    except Exception:  # noqa: BLE001
        session.rollback()
        return False
    return bool(int(count or 0) > 0)
