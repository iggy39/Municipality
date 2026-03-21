from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
import municipality  # noqa: F401
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from municipality.db import build_engine
from municipality.models import DocumentVersion, SemanticCandidateReject, SemanticDocumentRun
from municipality.processing import ProcessingService
from municipality.semantic_extractor import BytezSemanticClient, SemanticExtractor
from municipality.semantic_service import SemanticService


def _document_version_id(session: Session, doc_id: int) -> int | None:
    return session.execute(
        select(DocumentVersion.id)
        .where(DocumentVersion.document_id == doc_id)
        .order_by(DocumentVersion.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _purge_runs(session: Session, document_version_id: int) -> int:
    run_ids = list(
        session.execute(
            select(SemanticDocumentRun.id).where(SemanticDocumentRun.document_version_id == document_version_id)
        ).scalars().all()
    )
    if run_ids:
        session.execute(
            delete(SemanticCandidateReject).where(SemanticCandidateReject.semantic_document_run_id.in_(run_ids))
        )
        session.execute(delete(SemanticDocumentRun).where(SemanticDocumentRun.document_version_id == document_version_id))
    session.commit()
    return len(run_ids)


def _latest_run(session: Session, document_version_id: int) -> SemanticDocumentRun | None:
    return session.execute(
        select(SemanticDocumentRun)
        .where(SemanticDocumentRun.document_version_id == document_version_id)
        .order_by(SemanticDocumentRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def main() -> int:
    parser = argparse.ArgumentParser(description="Retry semantic enrichment for one protocol doc")
    parser.add_argument("doc_id", type=int)
    parser.add_argument("--timeouts", default="180,300,480,720")
    args = parser.parse_args()

    timeout_values = [float(item.strip()) for item in args.timeouts.split(",") if item.strip()]
    engine = build_engine()

    with Session(engine) as session:
        document_version_id = _document_version_id(session, args.doc_id)
    if document_version_id is None:
        print(json.dumps({"doc_id": args.doc_id, "status": "missing_document_version"}, ensure_ascii=True))
        return 1

    final_payload: dict[str, object] = {
        "doc_id": args.doc_id,
        "document_version_id": int(document_version_id),
        "status": "failed",
    }

    for attempt, timeout_seconds in enumerate(timeout_values, start=1):
        with Session(engine) as session:
            purged = _purge_runs(session, document_version_id)

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

            started = time.perf_counter()
            pipeline_run_id = processor.run(doc_id=args.doc_id, municipality_slug="ashdod")
            elapsed_seconds = round(time.perf_counter() - started, 3)

            latest = _latest_run(session, document_version_id)
            status = latest.status if latest is not None else "missing"
            error_code = latest.error_code if latest is not None else "MISSING_RUN"
            error_text = latest.error_text if latest is not None else "semantic run row not found"
            run_id = int(latest.id) if latest is not None else None

            payload = {
                "doc_id": args.doc_id,
                "document_version_id": int(document_version_id),
                "attempt": attempt,
                "timeout_seconds": timeout_seconds,
                "purged_runs": purged,
                "pipeline_run_id": int(pipeline_run_id),
                "semantic_run_id": run_id,
                "status": status,
                "error_code": error_code,
                "error_text": error_text,
                "elapsed_seconds": elapsed_seconds,
            }
            print(json.dumps(payload, ensure_ascii=True), flush=True)

            final_payload = payload
            if status == "completed":
                break

    print(json.dumps({"final": final_payload}, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
