from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
import municipality  # noqa: F401
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from municipality.api import AskRequest, _run_ask
from municipality.db import build_engine
from municipality.eval_rag import evaluate_rag_eval_set, load_rag_eval_set
from municipality.models import (
    Document,
    DocumentVersion,
    RagDecisionSummaryCache,
    SemanticCandidateReject,
    SemanticDocumentRun,
)
from municipality.processing import ProcessingService
from municipality.semantic_extractor import BytezSemanticClient, SemanticExtractor
from municipality.semantic_service import SemanticService


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "eval" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
RETRY_LOG_PATH = REPORTS_DIR / "semantic_retry_log.jsonl"
WARM_LOG_PATH = REPORTS_DIR / "topic_cache_warmup_log.jsonl"
SUMMARY_PATH = REPORTS_DIR / "semantic_retry_and_warm_summary.json"
RAG_EVAL_SET_PATH = PROJECT_ROOT / "eval" / "gold" / "m4_rag_eval_set.json"


RETRY_TIMEOUTS_SECONDS = [180.0, 300.0, 420.0]
WARMUP_QUERIES = [
    "אילו החלטות בטיחות בדרכים התקבלו?",
    "מה הוחלט בנושא חינוך ובתי ספר בעיר?",
    "אילו החלטות התקבלו בנושא רווחה ושירותים חברתיים?",
    "מה הוחלט בוועדת הקצאות מקרקעין?",
    "אילו החלטות התקבלו בנושא תנועה ותמרורים?",
    "מה הוחלט בנושא אלימות במשפחה ומניעה?",
    "אילו נושאים עלו בוועדת ביקורת ומה הוחלט?",
    "מה הוחלט בנושא קידום מעמד הילד?",
    "אילו החלטות תקציביות התקבלו בוועדות?",
    "אילו החלטות התקבלו בנושא פינויים ומבני ציבור?",
]


def _append_jsonl(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _failed_protocol_docs(session: Session) -> list[tuple[int, int, str]]:
    rows = session.execute(
        select(Document.id, DocumentVersion.id, Document.title_he)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .join(SemanticDocumentRun, SemanticDocumentRun.document_version_id == DocumentVersion.id)
        .where(Document.doc_kind.like("protocol%"))
        .where(SemanticDocumentRun.status == "failed")
        .order_by(Document.id.asc())
    ).all()
    return [(int(doc_id), int(doc_version_id), str(title or "")) for doc_id, doc_version_id, title in rows]


def _purge_semantic_run_cache_for_doc_version(session: Session, document_version_id: int) -> int:
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


def _retry_failed_semantics() -> dict:
    if RETRY_LOG_PATH.exists():
        RETRY_LOG_PATH.unlink()

    engine = build_engine()
    retry_started = time.perf_counter()
    protocol_results: list[dict] = []

    with Session(engine) as session:
        failed_docs = _failed_protocol_docs(session)

    print(
        json.dumps(
            {
                "phase": "retry_start",
                "failed_protocol_count": len(failed_docs),
                "timeouts_seconds": RETRY_TIMEOUTS_SECONDS,
            },
            ensure_ascii=True,
        ),
        flush=True,
    )

    for index, (doc_id, doc_version_id, title) in enumerate(failed_docs, start=1):
        final_status = "failed"
        final_error_code = None
        final_error_text = None
        final_run_id = None
        attempts: list[dict] = []

        for attempt_index, timeout_seconds in enumerate(RETRY_TIMEOUTS_SECONDS, start=1):
            with Session(engine) as session:
                purged_runs = _purge_semantic_run_cache_for_doc_version(session, doc_version_id)

                custom_semantic = SemanticService(
                    session,
                    extractor=SemanticExtractor(
                        session,
                        model_client=BytezSemanticClient(timeout_seconds=timeout_seconds),
                    ),
                )
                processor = ProcessingService(
                    session=session,
                    storage_root=Path("storage/raw"),
                    semantic_service=custom_semantic,
                )

                started = time.perf_counter()
                pipeline_run_id = processor.run(doc_id=doc_id, municipality_slug="ashdod")
                elapsed_seconds = round(time.perf_counter() - started, 3)

                latest = _latest_semantic_run(session, doc_version_id)
                status = latest.status if latest is not None else "missing"
                error_code = latest.error_code if latest is not None else "MISSING_RUN"
                error_text = latest.error_text if latest is not None else "semantic run row not found"
                run_id = int(latest.id) if latest is not None else None

                attempt_payload = {
                    "doc_id": doc_id,
                    "document_version_id": doc_version_id,
                    "title": title,
                    "attempt": attempt_index,
                    "timeout_seconds": timeout_seconds,
                    "purged_cached_runs": purged_runs,
                    "pipeline_run_id": int(pipeline_run_id),
                    "semantic_run_id": run_id,
                    "status": status,
                    "error_code": error_code,
                    "error_text": error_text,
                    "elapsed_seconds": elapsed_seconds,
                }
                attempts.append(attempt_payload)
                _append_jsonl(RETRY_LOG_PATH, attempt_payload)

            print(
                json.dumps(
                    {
                        "phase": "retry_progress",
                        "index": index,
                        "total": len(failed_docs),
                        **attempt_payload,
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )

            if status == "completed":
                final_status = "completed"
                final_run_id = run_id
                final_error_code = None
                final_error_text = None
                break

            final_status = status
            final_run_id = run_id
            final_error_code = error_code
            final_error_text = error_text

        protocol_final = {
            "doc_id": doc_id,
            "document_version_id": doc_version_id,
            "title": title,
            "status": final_status,
            "semantic_run_id": final_run_id,
            "error_code": final_error_code,
            "error_text": final_error_text,
            "attempts_used": len(attempts),
        }
        protocol_results.append(protocol_final)
        print(
            json.dumps(
                {
                    "phase": "retry_protocol_done",
                    "index": index,
                    "total": len(failed_docs),
                    **protocol_final,
                },
                ensure_ascii=True,
            ),
            flush=True,
        )

    elapsed_total = round(time.perf_counter() - retry_started, 3)
    completed_count = sum(1 for row in protocol_results if row["status"] == "completed")
    failed_count = len(protocol_results) - completed_count
    summary = {
        "failed_initial_count": len(failed_docs),
        "completed_after_retry": completed_count,
        "still_failed_after_retry": failed_count,
        "elapsed_seconds": elapsed_total,
        "results": protocol_results,
    }
    print(json.dumps({"phase": "retry_done", **summary}, ensure_ascii=True), flush=True)
    return summary


def _warm_topic_cache_and_eval() -> dict:
    if WARM_LOG_PATH.exists():
        WARM_LOG_PATH.unlink()

    engine = build_engine()
    warm_results: list[dict] = []
    warm_started = time.perf_counter()

    with Session(engine) as session:
        for index, question in enumerate(WARMUP_QUERIES, start=1):
            started = time.perf_counter()
            request = AskRequest(
                question=question,
                muni="ashdod",
                top_k=12,
                source_types=["protocol"],
                required_source_types=["protocol"],
                semantic_mode="off",
            )
            response = _run_ask(request=request, db=session)
            elapsed = round(time.perf_counter() - started, 3)
            row = {
                "index": index,
                "question": question,
                "status": response.get("status"),
                "citations": len(response.get("citations") or []),
                "answer_sections": len(response.get("answer_sections") or []),
                "reason_code": response.get("reason_code"),
                "elapsed_seconds": elapsed,
            }
            warm_results.append(row)
            _append_jsonl(WARM_LOG_PATH, row)
            print(json.dumps({"phase": "warm_progress", **row}, ensure_ascii=True), flush=True)

        cache_rows = session.execute(select(RagDecisionSummaryCache)).scalars().all()
        cache_protocols = sorted({str(row.protocol_title) for row in cache_rows if row.protocol_title})

        eval_set = load_rag_eval_set(RAG_EVAL_SET_PATH)

        def ask_fn(**kwargs):
            req = AskRequest(
                question=str(kwargs.get("question") or ""),
                top_k=int(kwargs.get("top_k") or 8),
                source_types=kwargs.get("source_types"),
                required_source_types=kwargs.get("required_source_types"),
                muni=kwargs.get("muni"),
                year=kwargs.get("year"),
                topic=kwargs.get("topic"),
                semantic_mode=str(kwargs.get("semantic_mode") or "off"),
            )
            return _run_ask(request=req, db=session)

        eval_summary, eval_case_results = evaluate_rag_eval_set(eval_set=eval_set, ask_fn=ask_fn)

    warm_elapsed = round(time.perf_counter() - warm_started, 3)
    warm_answers = sum(1 for row in warm_results if row.get("status") == "answer")
    warm_refusals = sum(1 for row in warm_results if row.get("status") == "refusal")
    out = {
        "warmup_elapsed_seconds": warm_elapsed,
        "warmup_total_queries": len(warm_results),
        "warmup_answers": warm_answers,
        "warmup_refusals": warm_refusals,
        "cache_rows": len(cache_rows),
        "cache_distinct_protocols": len(cache_protocols),
        "cache_protocol_titles_sample": cache_protocols[:20],
        "rag_eval_summary": {
            "total_cases": eval_summary.total_cases,
            "citation_correctness": eval_summary.citation_correctness,
            "answer_correctness": eval_summary.answer_correctness,
            "refusal_correctness": eval_summary.refusal_correctness,
            "evaluated_refusal_cases": eval_summary.evaluated_refusal_cases,
            "passed_refusal_cases": eval_summary.passed_refusal_cases,
            "bootstrap_case_id": eval_summary.bootstrap_case_id,
            "bootstrap_traceable": eval_summary.bootstrap_traceable,
            "thresholds_passed": eval_summary.thresholds_passed,
        },
        "rag_eval_case_results": [
            {
                "case_id": row.case_id,
                "answer_status": row.answer_status,
                "citation_ok": row.citation_ok,
                "answer_ok": row.answer_ok,
                "refusal_cases_total": row.refusal_cases_total,
                "refusal_cases_passed": row.refusal_cases_passed,
                "refusal_failures": row.refusal_failures,
            }
            for row in eval_case_results
        ],
    }
    print(json.dumps({"phase": "warm_done", **out}, ensure_ascii=True), flush=True)
    return out


def _final_coverage_snapshot() -> dict:
    engine = build_engine()
    with Session(engine) as session:
        protocol_docs = session.execute(
            select(Document.id).where(Document.doc_kind.like("protocol%"))
        ).scalars().all()
        protocol_doc_ids = [int(doc_id) for doc_id in protocol_docs]
        with_semantic = session.execute(
            select(Document.id)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .join(SemanticDocumentRun, SemanticDocumentRun.document_version_id == DocumentVersion.id)
            .where(Document.doc_kind.like("protocol%"))
            .distinct()
        ).scalars().all()
        failed_runs = session.execute(
            select(SemanticDocumentRun.id)
            .join(DocumentVersion, SemanticDocumentRun.document_version_id == DocumentVersion.id)
            .join(Document, DocumentVersion.document_id == Document.id)
            .where(Document.doc_kind.like("protocol%"))
            .where(SemanticDocumentRun.status == "failed")
        ).scalars().all()
        return {
            "protocol_docs_total": len(protocol_doc_ids),
            "protocol_docs_with_semantic_run": len(set(int(row) for row in with_semantic)),
            "failed_semantic_runs": len(failed_runs),
        }


def main() -> int:
    start = time.perf_counter()
    retry_summary = _retry_failed_semantics()
    warm_summary = _warm_topic_cache_and_eval()
    final_snapshot = _final_coverage_snapshot()

    payload = {
        "finished_at_epoch": time.time(),
        "elapsed_seconds": round(time.perf_counter() - start, 3),
        "retry_summary": retry_summary,
        "warm_summary": warm_summary,
        "final_snapshot": final_snapshot,
        "artifacts": {
            "retry_log_jsonl": str(RETRY_LOG_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "warm_log_jsonl": str(WARM_LOG_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        },
    }
    SUMMARY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"phase": "all_done", "summary_path": str(SUMMARY_PATH)}, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
