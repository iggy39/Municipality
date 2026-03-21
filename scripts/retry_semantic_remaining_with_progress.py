from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
import municipality  # noqa: F401
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from municipality.db import build_engine
from municipality.models import Document, DocumentVersion, SemanticCandidateReject, SemanticDocumentRun
from municipality.processing import ProcessingService
from municipality.semantic_extractor import BytezSemanticClient, SemanticExtractor
from municipality.semantic_service import SemanticService


RETRY_TIMEOUTS_SECONDS = [180.0, 300.0, 480.0, 720.0]


def _docs_needing_completed_semantic(session: Session) -> list[tuple[int, int, str]]:
    rows = session.execute(
        select(Document.id, DocumentVersion.id, Document.title_he)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(Document.doc_kind.like("protocol%"))
        .where(
            ~select(SemanticDocumentRun.id)
            .where(SemanticDocumentRun.document_version_id == DocumentVersion.id)
            .where(SemanticDocumentRun.status == "completed")
            .exists()
        )
        .order_by(Document.id.asc())
    ).all()
    return [(int(doc_id), int(doc_version_id), str(title or "")) for doc_id, doc_version_id, title in rows]


def _purge_doc_semantic_runs(session: Session, document_version_id: int) -> int:
    run_ids = list(
        session.execute(
            select(SemanticDocumentRun.id).where(SemanticDocumentRun.document_version_id == document_version_id)
        ).scalars().all()
    )
    if run_ids:
        session.execute(
            delete(SemanticCandidateReject).where(SemanticCandidateReject.semantic_document_run_id.in_(run_ids))
        )
        session.execute(
            delete(SemanticDocumentRun).where(SemanticDocumentRun.document_version_id == document_version_id)
        )
    session.commit()
    return len(run_ids)


def _latest_semantic_run(session: Session, document_version_id: int) -> SemanticDocumentRun | None:
    return session.execute(
        select(SemanticDocumentRun)
        .where(SemanticDocumentRun.document_version_id == document_version_id)
        .order_by(SemanticDocumentRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def main() -> int:
    engine = build_engine()
    started = time.perf_counter()

    with Session(engine) as session:
        targets = _docs_needing_completed_semantic(session)

    print(
        json.dumps(
            {
                "phase": "start",
                "targets": len(targets),
                "doc_ids": [row[0] for row in targets],
                "timeouts": RETRY_TIMEOUTS_SECONDS,
            },
            ensure_ascii=True,
        ),
        flush=True,
    )

    succeeded = 0
    failed = 0

    for idx, (doc_id, doc_version_id, title) in enumerate(targets, start=1):
        final_status = "failed"
        final_error = None
        final_run_id = None
        attempts_used = 0

        for attempt_idx, timeout_seconds in enumerate(RETRY_TIMEOUTS_SECONDS, start=1):
            attempts_used = attempt_idx
            with Session(engine) as session:
                purged_runs = _purge_doc_semantic_runs(session, doc_version_id)

                semantic_service = SemanticService(
                    session,
                    extractor=SemanticExtractor(
                        session,
                        model_client=BytezSemanticClient(timeout_seconds=timeout_seconds),
                    ),
                )
                processor = ProcessingService(
                    session=session,
                    storage_root=Path("storage/raw"),
                    semantic_service=semantic_service,
                )

                attempt_started = time.perf_counter()
                pipeline_run_id = processor.run(doc_id=doc_id, municipality_slug="ashdod")
                elapsed = round(time.perf_counter() - attempt_started, 3)

                latest = _latest_semantic_run(session, doc_version_id)
                status = latest.status if latest is not None else "missing"
                error_code = latest.error_code if latest is not None else "MISSING_RUN"
                error_text = latest.error_text if latest is not None else "semantic run row missing"
                run_id = int(latest.id) if latest is not None else None

            print(
                json.dumps(
                    {
                        "phase": "attempt",
                        "index": idx,
                        "total": len(targets),
                        "doc_id": doc_id,
                        "document_version_id": doc_version_id,
                        "attempt": attempt_idx,
                        "timeout_seconds": timeout_seconds,
                        "purged_runs": purged_runs,
                        "pipeline_run_id": int(pipeline_run_id),
                        "semantic_run_id": run_id,
                        "status": status,
                        "error_code": error_code,
                        "error_text": error_text,
                        "elapsed_seconds": elapsed,
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )

            if status == "completed":
                final_status = "completed"
                final_error = None
                final_run_id = run_id
                break

            final_status = status
            final_error = error_code
            final_run_id = run_id

        if final_status == "completed":
            succeeded += 1
        else:
            failed += 1

        print(
            json.dumps(
                {
                    "phase": "protocol_result",
                    "index": idx,
                    "total": len(targets),
                    "doc_id": doc_id,
                    "title": title,
                    "final_status": final_status,
                    "final_error_code": final_error,
                    "final_run_id": final_run_id,
                    "attempts_used": attempts_used,
                },
                ensure_ascii=True,
            ),
            flush=True,
        )

    with Session(engine) as session:
        remaining = _docs_needing_completed_semantic(session)

    print(
        json.dumps(
            {
                "phase": "done",
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "target_count": len(targets),
                "succeeded": succeeded,
                "failed": failed,
                "remaining_without_completed": len(remaining),
                "remaining_doc_ids": [row[0] for row in remaining],
            },
            ensure_ascii=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
