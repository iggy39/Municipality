from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from municipality.chunking import build_trigrams, normalize_for_search
from municipality.db import build_engine
from municipality.migrations import apply_all
from municipality.models import (
    ArtifactSemanticLink,
    ArtifactTopicAnnotation,
    Document,
    DocumentVersion,
    ExtractedDocument,
    RetrievalArtifact,
    RetrievalArtifactEmbedding,
)


DEFAULT_SOURCE_KIND = "pdf_first_protocol"
DEFAULT_CLASSIFIER_ROUTE = "dictalm_pdf_first_step4_5"


def main() -> int:
    parser = argparse.ArgumentParser(description="Import PDF-first Step 5 retrieval chunks into searchable RAG artifacts")
    parser.add_argument("--retrieval-chunks-json", required=True, help="Step 5 retrieval_chunks.json")
    parser.add_argument("--docver-id", type=int, required=True, help="Target document_version.id")
    parser.add_argument("--source-kind", default=DEFAULT_SOURCE_KIND, help="Isolated source kind for imported chunks")
    parser.add_argument("--replace-existing", action="store_true", help="Replace existing artifacts for this docver/source-kind")
    parser.add_argument("--write", action="store_true", help="Actually mutate the database; default is dry-run")
    args = parser.parse_args()

    chunks_path = Path(args.retrieval_chunks_json).expanduser().resolve()
    payload = json.loads(chunks_path.read_text(encoding="utf-8"))
    chunks = payload.get("retrieval_chunks") or []
    source_kind = str(args.source_kind or DEFAULT_SOURCE_KIND).strip() or DEFAULT_SOURCE_KIND

    engine = build_engine()
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        document_version, document, extracted = _load_target(session=session, docver_id=int(args.docver_id))
        existing_ids = _existing_artifact_ids(session=session, document_version_id=document_version.id, source_kind=source_kind)
        artifacts = _build_artifacts(
            chunks=chunks,
            document_version_id=document_version.id,
            source_kind=source_kind,
            document_title=document.title_he,
        )

        result = {
            "mode": "write" if args.write else "dry_run",
            "retrieval_chunks_json": str(chunks_path),
            "doc_id": document.id,
            "docver_id": document_version.id,
            "extracted_document_id": extracted.id,
            "source_kind": source_kind,
            "input_chunk_count": len(chunks),
            "artifact_count": len(artifacts),
            "existing_source_kind_artifact_count": len(existing_ids),
            "replace_existing": bool(args.replace_existing),
            "sample_artifact_ids": [artifact["artifact_id"] for artifact in artifacts[:5]],
        }

        if not args.write:
            print(json.dumps({**result, "would_write": len(artifacts), "accept_for_write": len(artifacts) > 0}, ensure_ascii=False))
            session.rollback()
            return 0

        if existing_ids and not args.replace_existing:
            print(json.dumps({**result, "error": "existing_artifacts_require_replace_existing"}, ensure_ascii=False))
            session.rollback()
            return 2

        if existing_ids:
            _delete_artifacts(session=session, artifact_ids=existing_ids)

        for ordinal, artifact in enumerate(artifacts):
            row = RetrievalArtifact(
                artifact_id=artifact["artifact_id"],
                document_id=document.id,
                document_version_id=document_version.id,
                extracted_document_id=extracted.id,
                section_id=artifact.get("section_id"),
                source_kind=source_kind,
                artifact_kind=artifact["artifact_kind"],
                ordinal=ordinal,
                title_he=artifact.get("title_he"),
                committee_name=None,
                meeting_date=artifact.get("meeting_date"),
                header_path_json=artifact["header_path_json"],
                body_text=artifact["body_text"],
                retrieval_text=artifact["retrieval_text"],
                retrieval_text_norm=artifact["retrieval_text_norm"],
                start_offset=artifact["start_offset"],
                end_offset=artifact["end_offset"],
                start_page=artifact.get("start_page"),
                end_page=artifact.get("end_page"),
                citation_label=artifact.get("citation_label"),
                trigram_count=artifact["trigram_count"],
                metadata_json=artifact.get("metadata_json"),
            )
            session.add(row)
            session.execute(
                text("INSERT INTO artifact_fts (artifact_id, retrieval_text) VALUES (:artifact_id, :retrieval_text)"),
                {"artifact_id": artifact["artifact_id"], "retrieval_text": artifact["retrieval_text"]},
            )
            trigram_rows = [{"artifact_id": artifact["artifact_id"], "trigram": trigram} for trigram in artifact["trigrams"]]
            if trigram_rows:
                session.execute(
                    text("INSERT INTO retrieval_artifact_trigram (artifact_id, trigram) VALUES (:artifact_id, :trigram)"),
                    trigram_rows,
                )
            if artifact.get("primary_topic_he"):
                session.add(
                    ArtifactTopicAnnotation(
                        artifact_id=artifact["artifact_id"],
                        structural_topic_he=artifact.get("structural_topic_he"),
                        structural_topic_norm=normalize_for_search(str(artifact.get("structural_topic_he") or "")) or None,
                        primary_topic_he=artifact.get("primary_topic_he"),
                        primary_topic_norm=normalize_for_search(str(artifact.get("primary_topic_he") or "")) or None,
                        secondary_topics_json=json.dumps([], ensure_ascii=False),
                        section_summary=artifact.get("section_summary"),
                        classifier_confidence=artifact.get("classifier_confidence"),
                        classifier_route=DEFAULT_CLASSIFIER_ROUTE,
                        provider_name="ollama",
                        model_name="dicta-il/DictaLM-3.0-24B-Thinking:bf16",
                    )
                )

        session.commit()
        print(json.dumps({**result, "written": len(artifacts), "deleted_existing": len(existing_ids)}, ensure_ascii=False))
        return 0


def _load_target(*, session: Session, docver_id: int) -> tuple[DocumentVersion, Document, ExtractedDocument]:
    row = session.execute(
        select(DocumentVersion, Document, ExtractedDocument)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(ExtractedDocument, ExtractedDocument.document_version_id == DocumentVersion.id)
        .where(DocumentVersion.id == docver_id)
    ).first()
    if row is None:
        raise SystemExit(f"document_version not found or has no extracted document: {docver_id}")
    document_version, document, extracted = row
    return document_version, document, extracted


def _existing_artifact_ids(*, session: Session, document_version_id: int, source_kind: str) -> list[str]:
    return list(
        session.execute(
            select(RetrievalArtifact.artifact_id)
            .where(RetrievalArtifact.document_version_id == document_version_id)
            .where(RetrievalArtifact.source_kind == source_kind)
        )
        .scalars()
        .all()
    )


def _delete_artifacts(*, session: Session, artifact_ids: list[str]) -> None:
    if not artifact_ids:
        return
    session.query(ArtifactSemanticLink).filter(ArtifactSemanticLink.artifact_id.in_(artifact_ids)).delete(synchronize_session=False)
    session.query(ArtifactTopicAnnotation).filter(ArtifactTopicAnnotation.artifact_id.in_(artifact_ids)).delete(synchronize_session=False)
    session.query(RetrievalArtifactEmbedding).filter(RetrievalArtifactEmbedding.artifact_id.in_(artifact_ids)).delete(synchronize_session=False)
    session.query(RetrievalArtifact).filter(RetrievalArtifact.artifact_id.in_(artifact_ids)).delete(synchronize_session=False)
    session.execute(text("DELETE FROM artifact_fts WHERE artifact_id = :artifact_id"), [{"artifact_id": artifact_id} for artifact_id in artifact_ids])
    session.execute(
        text("DELETE FROM retrieval_artifact_trigram WHERE artifact_id = :artifact_id"),
        [{"artifact_id": artifact_id} for artifact_id in artifact_ids],
    )


def _build_artifacts(*, chunks: list[dict[str, Any]], document_version_id: int, source_kind: str, document_title: str) -> list[dict[str, Any]]:
    artifacts = []
    for ordinal, chunk in enumerate(chunks):
        retrieval_text = str(chunk.get("chunk_text") or chunk.get("raw_text") or "").strip()
        if not retrieval_text:
            continue
        norm = normalize_for_search(retrieval_text)
        trigrams = sorted(build_trigrams(norm))
        page = _positive_int_or_none(chunk.get("page"))
        topic_label = str(chunk.get("canonical_topic_label_he") or chunk.get("raw_topic_label_he") or "").strip() or None
        role = str(chunk.get("structural_role") or "").strip() or None
        synthetic_offset = ordinal * 100000
        artifact_id = f"pf{document_version_id}_{chunk.get('chunk_id')}"[:64]
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "section_id": _short_text(chunk.get("section_id"), 64),
                "source_kind": source_kind,
                "artifact_kind": "pdf_first_retrieval_chunk",
                "title_he": topic_label or role or document_title,
                "meeting_date": _first_date(chunk.get("entity_facts") or []),
                "header_path_json": json.dumps([value for value in [topic_label, role] if value], ensure_ascii=False),
                "body_text": str(chunk.get("raw_text") or ""),
                "retrieval_text": retrieval_text,
                "retrieval_text_norm": norm,
                "start_offset": synthetic_offset,
                "end_offset": synthetic_offset + len(str(chunk.get("raw_text") or retrieval_text)),
                "start_page": page,
                "end_page": page,
                "citation_label": f"p.{page}" if page is not None else None,
                "trigrams": trigrams,
                "trigram_count": len(trigrams),
                "metadata_json": json.dumps(_metadata_for_chunk(chunk), ensure_ascii=False),
                "primary_topic_he": topic_label,
                "structural_topic_he": role,
                "section_summary": str(chunk.get("summary_he") or "")[:1000] or None,
                "classifier_confidence": _float_or_none(chunk.get("assignment_confidence")),
            }
        )
    return artifacts


def _metadata_for_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "pipeline_step": "step5_retrieval_chunks",
        "structure_unit_id": chunk.get("structure_unit_id"),
        "semantic_unit_id": chunk.get("semantic_unit_id"),
        "source_semantic_unit_ids": chunk.get("source_semantic_unit_ids") or [],
        "source_window_id": chunk.get("source_window_id"),
        "source_region_ids": chunk.get("source_region_ids") or [],
        "source_block_ids": chunk.get("source_block_ids") or [],
        "canonicalization_route": chunk.get("canonicalization_route"),
        "assignment_route": chunk.get("assignment_route"),
        "retrieval_weight": chunk.get("retrieval_weight"),
    }


def _first_date(entity_facts: list[dict[str, Any]]) -> str | None:
    for fact in entity_facts:
        if str(fact.get("entity_kind") or "") == "date" and fact.get("normalized_display"):
            return str(fact.get("normalized_display"))[:32]
    return None


def _positive_int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _short_text(value: Any, limit: int) -> str | None:
    text_value = str(value or "").strip()
    return text_value[:limit] or None


if __name__ == "__main__":
    raise SystemExit(main())
