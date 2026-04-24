from __future__ import annotations

from municipality.embeddings import ChunkEmbeddingIndexResult, ChunkEmbeddingLookupResult, ChunkEmbeddingService


class ArtifactEmbeddingService(ChunkEmbeddingService):
    pass


__all__ = [
    "ArtifactEmbeddingService",
    "ChunkEmbeddingIndexResult",
    "ChunkEmbeddingLookupResult",
]
