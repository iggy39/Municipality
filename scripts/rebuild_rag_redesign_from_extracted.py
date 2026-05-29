from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from municipality.db import build_engine
from municipality.migrations import apply_all
from municipality.models import Document, DocumentVersion, ExtractedDocument, RetrievalArtifact
from municipality.processing import SemanticEnrichmentPolicy, _source_kind_for_document
from municipality.decision_context import DecisionContextService
from municipality.semantic_service import SemanticService
from municipality.structured_indexing import StructuredIndexingService
from municipality.topic_data_maintenance import TopicDataMaintenanceStats, cleanup_low_quality_topic_data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild artifact-native RAG structures, semantic links, and decision context from extracted documents"
    )
    parser.add_argument("--doc-id", type=int, default=None, help="Only rebuild one document id")
    parser.add_argument("--docver-id", type=int, default=None, help="Only rebuild one document version id")
    parser.add_argument("--limit", type=int, default=None, help="Maximum extracted documents to process")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N extracted document rows after filtering")
    parser.add_argument(
        "--reindex-existing",
        action="store_true",
        help="Rebuild structure rows even if artifacts already exist for the document version",
    )
    parser.add_argument("--skip-structure", action="store_true", help="Do not rebuild document_section / retrieval_artifact")
    parser.add_argument("--skip-semantic", action="store_true", help="Do not rebuild semantic artifact links")
    parser.add_argument("--skip-context", action="store_true", help="Do not rebuild decision request context")
    parser.add_argument(
        "--allow-fresh-semantic-calls",
        action="store_true",
        help="Allow new semantic model calls when no cached semantic run exists for a document version",
    )
    parser.add_argument(
        "--cleanup-low-quality-topics",
        action="store_true",
        help="Sanitize stale low-quality topic annotations and deprecate low-quality semantic topic nodes for the selected scope",
    )
    parser.add_argument(
        "--cleanup-dry-run",
        action="store_true",
        help="Report low-quality topic cleanup counts for the selected scope without mutating data",
    )
    args = parser.parse_args()
    if args.cleanup_dry_run and not args.cleanup_low_quality_topics:
        parser.error("--cleanup-dry-run requires --cleanup-low-quality-topics")

    engine = build_engine()
    apply_all(engine, Path("migrations"))

    policy = SemanticEnrichmentPolicy.from_env()
    processed = 0
    skipped = 0
    failures = 0
    structure_runs = 0
    semantic_runs = 0
    context_runs = 0
    cleanup_stats = TopicDataMaintenanceStats()

    with Session(engine) as session:
        structure_service = StructuredIndexingService(session)
        semantic_service = SemanticService(session)
        context_service = DecisionContextService(session)

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
        if args.docver_id is not None:
            stmt = stmt.where(DocumentVersion.id == args.docver_id)

        rows = session.execute(stmt).all()
        if args.offset is not None and int(args.offset) > 0:
            rows = rows[int(args.offset) :]
        if args.limit is not None:
            rows = rows[: max(0, int(args.limit))]

        selected_document_ids = sorted({int(document.id) for _extracted, _document_version, document in rows})
        selected_document_version_ids = sorted({int(document_version.id) for _extracted, document_version, _document in rows})

        if args.cleanup_low_quality_topics:
            cleanup_stats = cleanup_low_quality_topic_data(
                session,
                document_ids=selected_document_ids,
                document_version_ids=selected_document_version_ids,
                dry_run=bool(args.cleanup_dry_run),
            )
            print(
                "cleanup "
                f"artifact_annotations_scanned={cleanup_stats.artifact_annotations_scanned} "
                f"artifact_annotations_updated={cleanup_stats.artifact_annotations_updated} "
                f"semantic_nodes_scanned={cleanup_stats.semantic_nodes_scanned} "
                f"semantic_nodes_deprecated={cleanup_stats.semantic_nodes_deprecated}"
            )
            if args.cleanup_dry_run:
                session.rollback()
                return 0
            session.commit()

        for extracted, document_version, document in rows:
            extracted_text = str(extracted.extracted_text or "").strip()
            if not extracted_text:
                skipped += 1
                continue

            source_kind = _source_kind_for_document(document.doc_kind)
            citation_map = _load_json_list(extracted.citation_map_json)
            pages = _load_json_list(extracted.pages_json)

            try:
                artifact_count = _artifact_count(session=session, document_version_id=document_version.id)
                if not args.skip_structure:
                    if args.reindex_existing or artifact_count <= 0:
                        structure_result = structure_service.replace_document_structure(
                            document_id=document.id,
                            document_version_id=document_version.id,
                            extracted_document_id=extracted.id,
                            document_title=document.title_he,
                            text=extracted_text,
                            citation_map=citation_map,
                            source_kind=source_kind,
                            pages=pages,
                        )
                        artifact_count = structure_result.artifact_count
                        structure_runs += 1

                if not args.skip_semantic:
                    should_run_semantic, _reason = policy.evaluate(
                        source_kind=source_kind,
                        quality_score=extracted.quality_score,
                        extracted_text=extracted_text,
                        chunk_count=artifact_count,
                    )
                    if should_run_semantic:
                        if args.allow_fresh_semantic_calls:
                            semantic_service.run_for_document(
                                source_site_id=document.source_site_id,
                                document_id=document.id,
                                document_version_id=document_version.id,
                                source_kind=source_kind,
                                extracted_text=extracted_text,
                                citation_map=citation_map,
                            )
                            semantic_runs += 1
                        else:
                            rebuilt = semantic_service.rebuild_from_recorded_run(
                                source_site_id=document.source_site_id,
                                document_id=document.id,
                                document_version_id=document_version.id,
                                source_kind=source_kind,
                                extracted_text=extracted_text,
                                citation_map=citation_map,
                            )
                            if rebuilt is not None:
                                semantic_runs += 1

                if not args.skip_context and source_kind == "protocol":
                    context_service.process_document(
                        source_document_id=document.id,
                        document_version_id=document_version.id,
                        source_kind=source_kind,
                    )
                    context_runs += 1

                session.commit()
                processed += 1
                print(
                    f"processed doc={document.id} docver={document_version.id} "
                    f"source={source_kind} artifacts={artifact_count}"
                )
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                failures += 1
                print(
                    f"failed doc={document.id} docver={document_version.id} "
                    f"error={exc.__class__.__name__}:{exc}"
                )

    print(
        "done "
        f"processed={processed} skipped={skipped} failures={failures} "
        f"structure_runs={structure_runs} semantic_runs={semantic_runs} context_runs={context_runs} "
        f"cleanup_artifact_annotations_updated={cleanup_stats.artifact_annotations_updated} "
        f"cleanup_semantic_nodes_deprecated={cleanup_stats.semantic_nodes_deprecated}"
    )
    return 0 if failures == 0 else 1


def _artifact_count(*, session: Session, document_version_id: int) -> int:
    return int(
        session.execute(
            select(func.count(RetrievalArtifact.id)).where(RetrievalArtifact.document_version_id == document_version_id)
        ).scalar_one()
        or 0
    )


def _load_json_list(value: str | None) -> list[dict] | list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


if __name__ == "__main__":
    raise SystemExit(main())
