from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from municipality.api import (
    _cache_topic_name,
    _canonicalize_topic_child,
    _infer_topic_path_from_summary,
    _object_root_from_topic_parts,
    _split_topic_path,
)
from municipality.chunking import normalize_for_search
from municipality.db import build_engine, build_session_factory
from municipality.models import RagDecisionSummaryCache


def _is_generic_topic(value: str) -> bool:
    normalized = normalize_for_search(value)
    return normalized in {
        "",
        normalize_for_search("ללא תיוג סמנטי"),
        normalize_for_search("נושא כללי"),
        normalize_for_search("החלטה כללית"),
        normalize_for_search("כללית"),
    }


def _normalize_topic_path(*, protocol_title: str, topic_name: str, summary_he: str) -> str:
    current_topic = str(topic_name or "").strip()
    if _is_generic_topic(current_topic):
        inferred = _infer_topic_path_from_summary(summary_he=str(summary_he or ""), protocol_title=str(protocol_title or ""))
        if inferred:
            current_topic = inferred

    root_topic, child_topic = _split_topic_path(current_topic)
    root_topic = _cache_topic_name(root_topic)
    child_topic = _cache_topic_name(child_topic)

    if _is_generic_topic(child_topic):
        child_topic = root_topic

    object_root = _object_root_from_topic_parts(root_topic=root_topic, child_topic=child_topic)
    if object_root:
        root_topic = object_root

    child_topic = _canonicalize_topic_child(root_topic=root_topic, child_topic=child_topic)

    title_norm = normalize_for_search(protocol_title)
    if (
        normalize_for_search(root_topic) == normalize_for_search("הסכמים")
        and normalize_for_search(child_topic) == normalize_for_search("הסכם רשות לעירייה")
        and "הקצא" in title_norm
    ):
        child_topic = "הסכם רשות לעמותה"

    if normalize_for_search(child_topic) == normalize_for_search(root_topic):
        return root_topic
    return f"{root_topic} > {child_topic}"


def main() -> None:
    engine = build_engine()
    SessionLocal = build_session_factory(engine)

    updated = 0
    scanned = 0
    now = datetime.utcnow()

    with SessionLocal() as db:
        rows = db.execute(select(RagDecisionSummaryCache).order_by(RagDecisionSummaryCache.id.asc())).scalars().all()
        for row in rows:
            scanned += 1
            old_topic = str(row.topic_name or "").strip()
            new_topic = _normalize_topic_path(
                protocol_title=str(row.protocol_title or ""),
                topic_name=old_topic,
                summary_he=str(row.summary_he or ""),
            )
            if normalize_for_search(new_topic) == normalize_for_search(old_topic):
                continue

            row.topic_name = new_topic
            row.updated_at = now
            updated += 1

        db.commit()

    print(f"scanned={scanned} updated={updated}")


if __name__ == "__main__":
    main()
