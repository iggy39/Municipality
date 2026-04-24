from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.db import build_engine
from municipality.migrations import apply_all
from municipality.models import Document, DocumentVersion, ExtractedDocument, RetrievalArtifact
from municipality.processing import _source_kind_for_document
from municipality.structured_indexing import StructuredIndexingService


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill hierarchy-aware RAG v2 structure from extracted documents")
    parser.add_argument("--doc-id", type=int, default=None, help="Only backfill one document id")
    parser.add_argument("--limit", type=int, default=None, help="Maximum extracted documents to process")
    parser.add_argument(
        "--reindex-existing",
        action="store_true",
        help="Rebuild rows even if retrieval artifacts already exist for the document version",
    )
    args = parser.parse_args()

    engine = build_engine()
    apply_all(engine, Path("migrations"))

    processed = 0
    skipped = 0
    total_sections = 0
    total_artifacts = 0
    total_embeddings = 0

    with Session(engine) as session:
        service = StructuredIndexingService(session)
        stmt = (
            select(ExtractedDocument, DocumentVersion, Document)
            .join(DocumentVersion, DocumentVersion.id == ExtractedDocument.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(ExtractedDocument.status == "completed")
            .where(ExtractedDocument.extracted_text.is_not(None))
            .order_by(ExtractedDocument.document_version_id.asc())
        )
        if args.doc_id is not None:
            stmt = stmt.where(Document.id == args.doc_id)
        rows = session.execute(stmt).all()
        if args.limit is not None:
            rows = rows[: max(0, int(args.limit))]

        for extracted, document_version, document in rows:
            if not args.reindex_existing:
                existing = session.execute(
                    select(RetrievalArtifact.id).where(RetrievalArtifact.document_version_id == document_version.id).limit(1)
                ).first()
                if existing is not None:
                    skipped += 1
                    continue

            extracted_text = str(extracted.extracted_text or "").strip()
            if not extracted_text:
                skipped += 1
                continue
            try:
                citation_map = json.loads(extracted.citation_map_json or "[]")
            except json.JSONDecodeError:
                citation_map = []
            if not isinstance(citation_map, list):
                citation_map = []
            try:
                pages = json.loads(extracted.pages_json or "[]")
            except json.JSONDecodeError:
                pages = []
            if not isinstance(pages, list):
                pages = []

            result = service.replace_document_structure(
                document_id=document.id,
                document_version_id=document_version.id,
                extracted_document_id=extracted.id,
                document_title=document.title_he,
                text=extracted_text,
                citation_map=citation_map,
                source_kind=_source_kind_for_document(document.doc_kind),
                pages=pages,
            )
            processed += 1
            total_sections += result.section_count
            total_artifacts += result.artifact_count
            total_embeddings += result.embedding_created
            session.commit()
            print(
                f"processed doc={document.id} docver={document_version.id} "
                f"sections={result.section_count} artifacts={result.artifact_count} embeds={result.embedding_created}"
            )

    print(
        f"done processed={processed} skipped={skipped} "
        f"sections={total_sections} artifacts={total_artifacts} embeds={total_embeddings}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
