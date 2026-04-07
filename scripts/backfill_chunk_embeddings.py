from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import select

from municipality.db import build_engine, build_session_factory
from municipality.embeddings import ChunkEmbeddingService
from municipality.migrations import apply_all
from municipality.models import TextChunk


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill OpenAI chunk embeddings for existing text chunks.")
    parser.add_argument("--source-kind", choices=["protocol", "attachment", "other"], default=None)
    parser.add_argument("--document-id", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    engine = build_engine()
    apply_all(engine, Path("migrations"))
    SessionLocal = build_session_factory(engine)

    scanned = 0
    created = 0
    cached = 0
    failed_batches = 0

    with SessionLocal() as session:
        embedding_service = ChunkEmbeddingService(session)
        if not embedding_service.is_enabled():
            raise SystemExit("chunk embedding service is not enabled or OPENAI_API_KEY is missing")

        batch_size = max(1, int(args.batch_size))
        limit = max(0, int(args.limit)) if args.limit is not None else None
        offset = 0

        while True:
            stmt = select(TextChunk).order_by(TextChunk.id.asc()).offset(offset).limit(batch_size)
            if args.source_kind:
                stmt = stmt.where(TextChunk.source_kind == args.source_kind)
            if args.document_id is not None:
                stmt = stmt.where(TextChunk.document_id == int(args.document_id))

            rows = session.execute(stmt).scalars().all()
            if not rows:
                break

            if limit is not None and scanned >= limit:
                break

            chunk_payloads = []
            for row in rows:
                if limit is not None and scanned >= limit:
                    break
                chunk_payloads.append({"chunk_id": row.chunk_id, "chunk_text": row.chunk_text})
                scanned += 1

            if not chunk_payloads:
                break

            result = embedding_service.index_chunks(chunks=chunk_payloads)
            if result.error_text:
                failed_batches += 1
                session.rollback()
                print(
                    f"batch_failed offset={offset} size={len(chunk_payloads)} error={result.error_text}"
                )
                if _is_unrecoverable_embedding_error(result.error_text):
                    print("stopping early because the embedding provider returned an unrecoverable account/auth error")
                    break
            else:
                created += result.created
                cached += result.cached
                session.commit()
                print(
                    f"batch_ok offset={offset} scanned={scanned} created={result.created} cached={result.cached} total={result.total}"
                )

            offset += len(rows)

    print(
        f"done scanned={scanned} created={created} cached={cached} failed_batches={failed_batches}"
    )

def _is_unrecoverable_embedding_error(error_text: str | None) -> bool:
    normalized = str(error_text or "").casefold()
    return any(
        marker in normalized
        for marker in (
            "billing_not_active",
            "insufficient_quota",
            "invalid_api_key",
            "authenticationerror",
            "permissiondeniederror",
        )
    )


if __name__ == "__main__":
    main()
