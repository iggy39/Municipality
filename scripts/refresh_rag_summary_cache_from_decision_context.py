from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json

from sqlalchemy import select

from municipality.api import _cache_topic_name, _canonicalize_topic_child, _object_root_from_topic_parts, _split_topic_path
from municipality.chunking import normalize_for_search
from municipality.db import build_engine, build_session_factory
from municipality.migrations import apply_all
from municipality.models import DecisionRequestContext, RagDecisionSummaryCache
from municipality.rag_answering import _decision_context_request_subject, _decision_context_subject_topic, _rewrite_summary_from_decision_context


def _context_payload(row: DecisionRequestContext) -> dict[str, object]:
    return {
        "request_subject_he": row.request_subject_he,
        "subject_topic_he": row.subject_topic_he,
        "address_he": row.address_he,
        "gush": row.gush,
        "helka": row.helka,
        "migrash": row.migrash,
        "source_chunk_ids": [],
        "confidence": row.confidence,
    }


def _topic_from_decision_context(*, protocol_title: str, existing_topic: str, payload: dict[str, object]) -> str | None:
    context_topic = _decision_context_subject_topic(payload)
    if not context_topic:
        return None

    existing_root, _existing_child = _split_topic_path(existing_topic)
    root_topic = _object_root_from_topic_parts(root_topic=existing_root, child_topic=context_topic)
    if not root_topic:
        root_topic = _object_root_from_topic_parts(root_topic=protocol_title, child_topic=context_topic)
    if not root_topic:
        root_topic = existing_root or "החלטות עירוניות"

    root_topic = _cache_topic_name(root_topic)
    child_topic = _canonicalize_topic_child(root_topic=root_topic, child_topic=context_topic)
    child_topic = _cache_topic_name(child_topic)
    if normalize_for_search(child_topic) == normalize_for_search(root_topic):
        return root_topic
    return f"{root_topic} > {child_topic}"


def main() -> None:
    engine = build_engine()
    apply_all(engine, Path("migrations"))
    SessionLocal = build_session_factory(engine)

    scanned = 0
    updated = 0
    rewritten_summaries = 0
    now = datetime.utcnow()

    with SessionLocal() as db:
        context_rows = db.execute(select(DecisionRequestContext)).scalars().all()
        payload_by_chunk_id: dict[str, dict[str, object]] = {}
        for row in context_rows:
            payload = _context_payload(row)
            source_chunk_ids = []
            try:
                parsed = json.loads(row.source_chunk_ids_json or "[]")
                if isinstance(parsed, list):
                    source_chunk_ids = [str(item) for item in parsed if str(item)]
            except Exception:
                source_chunk_ids = []
            payload["source_chunk_ids"] = source_chunk_ids
            for chunk_id in source_chunk_ids:
                existing = payload_by_chunk_id.get(chunk_id)
                if existing is None or float(payload.get("confidence") or 0.0) > float(existing.get("confidence") or 0.0):
                    payload_by_chunk_id[chunk_id] = payload

        rows = db.execute(select(RagDecisionSummaryCache).order_by(RagDecisionSummaryCache.id.asc())).scalars().all()
        planned: dict[int, tuple[str, str]] = {}
        kept_by_final_key: dict[tuple[str, str, str], int] = {}
        delete_ids: set[int] = set()
        for row in rows:
            scanned += 1
            candidate_summary = str(row.summary_he or "")
            candidate_topic = str(row.topic_name or "")

            payload = payload_by_chunk_id.get(str(row.chunk_id))
            if payload is not None:
                new_topic = _topic_from_decision_context(
                    protocol_title=str(row.protocol_title or ""),
                    existing_topic=str(row.topic_name or ""),
                    payload=payload,
                )
                if new_topic:
                    candidate_topic = new_topic

                rewritten_summary, _ = _rewrite_summary_from_decision_context(
                    summary_text=str(row.summary_he or ""),
                    topic_name=candidate_topic,
                    decision_request_context=payload,
                )
                if rewritten_summary:
                    candidate_summary = rewritten_summary
                    if normalize_for_search(rewritten_summary) != normalize_for_search(str(row.summary_he or "")):
                        rewritten_summaries += 1

            final_key = (str(row.question_hash), str(row.chunk_id), candidate_summary)
            existing_keeper = kept_by_final_key.get(final_key)
            if existing_keeper is None:
                kept_by_final_key[final_key] = int(row.id)
                planned[int(row.id)] = (candidate_topic, candidate_summary)
                continue
            delete_ids.add(int(row.id))

        for row in rows:
            row_id = int(row.id)
            if row_id in delete_ids:
                db.delete(row)
                updated += 1
        db.flush()

        for row in rows:
            row_id = int(row.id)
            if row_id in delete_ids:
                continue
            target = planned.get(row_id)
            if target is None:
                continue
            candidate_topic, candidate_summary = target
            row.topic_name = candidate_topic
            row.summary_he = candidate_summary
            row.updated_at = now
            updated += 1

        db.commit()

    print(f"scanned={scanned} updated={updated} rewritten_summaries={rewritten_summaries}")


if __name__ == "__main__":
    main()
