from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import time
from pathlib import Path


def _compute_missing_doc_ids() -> list[int]:
    import sys

    sys.path.insert(0, "src")
    import municipality  # noqa: F401
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from municipality.db import build_engine
    from municipality.models import Document, DocumentVersion, ExtractedDocument, SemanticDocumentRun

    engine = build_engine()
    with Session(engine) as session:
        protocol_doc_ids = session.execute(
            select(Document.id)
            .where(Document.doc_kind.like("protocol%"))
            .order_by(Document.id.asc())
        ).scalars().all()

        missing: list[int] = []
        for doc_id in protocol_doc_ids:
            has_extracted = (
                session.execute(
                    select(ExtractedDocument.id)
                    .join(DocumentVersion, ExtractedDocument.document_version_id == DocumentVersion.id)
                    .where(DocumentVersion.document_id == doc_id)
                    .limit(1)
                ).scalar_one_or_none()
                is not None
            )
            has_semantic = (
                session.execute(
                    select(SemanticDocumentRun.id)
                    .join(DocumentVersion, SemanticDocumentRun.document_version_id == DocumentVersion.id)
                    .where(DocumentVersion.document_id == doc_id)
                    .limit(1)
                ).scalar_one_or_none()
                is not None
            )
            if not has_extracted or not has_semantic:
                missing.append(int(doc_id))
        return missing


def _worker(doc_id: int, queue: mp.Queue) -> None:
    import sys

    sys.path.insert(0, "src")
    import municipality  # noqa: F401
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from municipality.db import build_engine
    from municipality.models import PipelineRunStep
    from municipality.processing import ProcessingService

    started = time.perf_counter()
    try:
        engine = build_engine()
        with Session(engine) as session:
            service = ProcessingService(session=session, storage_root=Path("storage/raw"))
            run_id = service.run(doc_id=doc_id, municipality_slug="ashdod")
            steps = session.execute(
                select(PipelineRunStep.step_name, PipelineRunStep.status)
                .where(PipelineRunStep.run_id == run_id)
                .order_by(PipelineRunStep.id.asc())
            ).all()
        payload = {
            "doc_id": doc_id,
            "run_id": run_id,
            "seconds": round(time.perf_counter() - started, 3),
            "steps": [{"name": name, "status": status} for name, status in steps],
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        payload = {
            "doc_id": doc_id,
            "run_id": None,
            "seconds": round(time.perf_counter() - started, 3),
            "steps": [],
            "error": f"{exc.__class__.__name__}:{exc}",
        }
    queue.put(payload)


def _process_one(doc_id: int, timeout_seconds: int) -> dict:
    ctx = mp.get_context("spawn")
    queue: mp.Queue = ctx.Queue()
    process = ctx.Process(target=_worker, args=(doc_id, queue))
    process.start()
    process.join(timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join(5)
        return {
            "doc_id": doc_id,
            "run_id": None,
            "seconds": timeout_seconds,
            "steps": [],
            "error": "TIMEOUT",
        }

    if not queue.empty():
        return queue.get()

    return {
        "doc_id": doc_id,
        "run_id": None,
        "seconds": 0.0,
        "steps": [],
        "error": "NO_RESULT",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Process remaining protocol docs with per-doc timeout")
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument("--max-passes", type=int, default=3)
    args = parser.parse_args()

    overall_started = time.perf_counter()
    missing = _compute_missing_doc_ids()
    print(json.dumps({"phase": "start", "remaining": len(missing), "missing_doc_ids": missing}, ensure_ascii=True), flush=True)

    pass_index = 1
    while missing and pass_index <= args.max_passes:
        print(json.dumps({"phase": "pass_start", "pass": pass_index, "count": len(missing)}, ensure_ascii=True), flush=True)
        for idx, doc_id in enumerate(missing, start=1):
            row = _process_one(doc_id=doc_id, timeout_seconds=args.timeout_seconds)
            print(
                json.dumps(
                    {
                        "phase": "progress",
                        "pass": pass_index,
                        "index": idx,
                        "total": len(missing),
                        **row,
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )
        missing = _compute_missing_doc_ids()
        print(
            json.dumps(
                {
                    "phase": "pass_done",
                    "pass": pass_index,
                    "remaining": len(missing),
                    "missing_doc_ids": missing,
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
        pass_index += 1

    print(
        json.dumps(
            {
                "phase": "done",
                "elapsed_seconds": round(time.perf_counter() - overall_started, 3),
                "remaining": len(missing),
                "missing_doc_ids": missing,
            },
            ensure_ascii=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
