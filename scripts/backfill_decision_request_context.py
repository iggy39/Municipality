from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from municipality.db import build_engine, build_session_factory
from municipality.decision_context import DecisionContextService
from municipality.migrations import apply_all
from municipality.models import Document, DocumentVersion


def main() -> None:
    engine = build_engine()
    apply_all(engine, Path("migrations"))
    SessionLocal = build_session_factory(engine)

    processed = 0
    linked = 0
    with SessionLocal() as session:
        rows = session.execute(
            select(Document.id, DocumentVersion.id)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(Document.doc_kind.like("protocol%"))
            .order_by(Document.id.asc(), DocumentVersion.id.asc())
        ).all()

        service = DecisionContextService(session)
        for document_id, document_version_id in rows:
            result = service.process_document(
                source_document_id=int(document_id),
                document_version_id=int(document_version_id),
                source_kind="protocol",
            )
            processed += 1
            linked += int(result.get("contexts", 0))

        session.commit()

    print(f"processed={processed} linked={linked}")


if __name__ == "__main__":
    main()
