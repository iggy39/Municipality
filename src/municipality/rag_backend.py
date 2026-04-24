from __future__ import annotations

from sqlalchemy.orm import Session

from municipality.artifact_embeddings import ArtifactEmbeddingService
from municipality.artifact_search import ArtifactSearchService
from municipality.embeddings import EmbeddingConfig, EmbeddingModelClient
from municipality.rag_arch import RagArchitectureConfig


def build_search_backend(*, session: Session, arch_config: RagArchitectureConfig | None = None):
    _ = arch_config or RagArchitectureConfig.from_env()
    return ArtifactSearchService(session)


def build_embedding_backend(
    *,
    session: Session,
    arch_config: RagArchitectureConfig | None = None,
    model_client: EmbeddingModelClient | None = None,
    config: EmbeddingConfig | None = None,
): 
    _ = arch_config or RagArchitectureConfig.from_env()
    return ArtifactEmbeddingService(session, model_client=model_client, config=config)
