from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import select

from municipality.embeddings import ChunkEmbeddingIndexResult, ChunkEmbeddingService
from municipality.models import RetrievalArtifactEmbedding


class ArtifactEmbeddingService(ChunkEmbeddingService):
    def load_vectors(self, chunk_ids: Sequence[str]) -> dict[str, list[float]]:
        normalized_ids = [str(chunk_id or "").strip() for chunk_id in chunk_ids if str(chunk_id or "").strip()]
        if not normalized_ids:
            return {}
        rows = self.session.execute(
            select(RetrievalArtifactEmbedding).where(
                RetrievalArtifactEmbedding.artifact_id.in_(normalized_ids),
                RetrievalArtifactEmbedding.model_provider == self.model_client.provider_name,
                RetrievalArtifactEmbedding.model_name == self.model_client.model_name,
                RetrievalArtifactEmbedding.dimensions == self.model_client.dimensions,
            )
        ).scalars().all()
        out: dict[str, list[float]] = {}
        for row in rows:
            try:
                parsed = json.loads(row.embedding_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, list) or not parsed:
                continue
            out[str(row.artifact_id)] = [float(value) for value in parsed]
        return out

    def delete_chunk_embeddings(self, *, chunk_ids: Sequence[str]) -> None:
        normalized_ids = [str(chunk_id or "").strip() for chunk_id in chunk_ids if str(chunk_id or "").strip()]
        if not normalized_ids:
            return
        self.session.query(RetrievalArtifactEmbedding).filter(
            RetrievalArtifactEmbedding.artifact_id.in_(normalized_ids)
        ).delete(synchronize_session=False)

    def _ensure_chunk_embeddings(self, rows: Sequence[tuple[str, str]]) -> ChunkEmbeddingIndexResult:
        normalized_rows: list[tuple[str, str]] = []
        seen_ids: set[str] = set()
        for chunk_id, chunk_text in rows:
            identifier = str(chunk_id or "").strip()
            payload = str(chunk_text or "").strip()
            if not identifier or not payload or identifier in seen_ids:
                continue
            seen_ids.add(identifier)
            normalized_rows.append((identifier, payload))
        if not normalized_rows:
            return ChunkEmbeddingIndexResult(enabled=self.is_enabled(), total=0)
        if not self.is_enabled():
            return ChunkEmbeddingIndexResult(enabled=False, total=len(normalized_rows))

        existing_rows = self.session.execute(
            select(RetrievalArtifactEmbedding.artifact_id).where(
                RetrievalArtifactEmbedding.artifact_id.in_([artifact_id for artifact_id, _ in normalized_rows]),
                RetrievalArtifactEmbedding.model_provider == self.model_client.provider_name,
                RetrievalArtifactEmbedding.model_name == self.model_client.model_name,
                RetrievalArtifactEmbedding.dimensions == self.model_client.dimensions,
            )
        ).all()
        existing_ids = {str(artifact_id) for artifact_id, in existing_rows}
        missing_rows = [(artifact_id, text) for artifact_id, text in normalized_rows if artifact_id not in existing_ids]
        if not missing_rows:
            return ChunkEmbeddingIndexResult(enabled=True, created=0, cached=len(existing_ids), total=len(normalized_rows))

        try:
            vectors = self.model_client.embed_texts([text for _artifact_id, text in missing_rows])
        except Exception as exc:  # noqa: BLE001
            return ChunkEmbeddingIndexResult(
                enabled=True,
                created=0,
                cached=len(existing_ids),
                total=len(normalized_rows),
                error_text=f"{exc.__class__.__name__}:{exc}",
            )
        if len(vectors) != len(missing_rows):
            return ChunkEmbeddingIndexResult(
                enabled=True,
                created=0,
                cached=len(existing_ids),
                total=len(normalized_rows),
                error_text="embedding vector count mismatch",
            )

        now = datetime.utcnow()
        for (artifact_id, _text), vector in zip(missing_rows, vectors, strict=True):
            self.session.add(
                RetrievalArtifactEmbedding(
                    artifact_id=artifact_id,
                    model_provider=self.model_client.provider_name,
                    model_name=self.model_client.model_name,
                    dimensions=len(vector),
                    embedding_json=json.dumps(vector, separators=(",", ":")),
                    created_at=now,
                    updated_at=now,
                )
            )
        self.session.flush()
        return ChunkEmbeddingIndexResult(
            enabled=True,
            created=len(missing_rows),
            cached=len(existing_ids),
            total=len(normalized_rows),
        )
