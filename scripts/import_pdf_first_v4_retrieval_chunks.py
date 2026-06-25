from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from municipality.chunking import build_trigrams, normalize_for_search  # noqa: E402
from municipality.db import build_engine  # noqa: E402
from municipality.migrations import apply_all  # noqa: E402
from municipality.models import (  # noqa: E402
    ArtifactSemanticLink,
    ArtifactTopicAnnotation,
    Document,
    DocumentVersion,
    ExtractedDocument,
    RetrievalArtifact,
    RetrievalArtifactEmbedding,
    SemanticDocumentRun,
    SemanticNode,
)
from municipality.pdf_first_v4_topic_tree import (  # noqa: E402
    BACKEND_VERSION,
    TOPIC_ASSIGNMENT_BACKEND,
    TOPIC_TREE_VERSION,
    V4_PROVENANCE,
    persist_v4_semantic_links,
    seed_root_nodes,
)

DEFAULT_SOURCE_KIND = "pdf_first_v4"
DEFAULT_MODEL = "dicta-il/DictaLM-3.0-24B-Thinking:bf16"


def main() -> int:
    parser = argparse.ArgumentParser(description="Import PDF-first v4 retrieval chunks and semantic links")
    parser.add_argument("--retrieval-chunks-json", required=True, help="Step 5 v4 retrieval_chunks.json")
    parser.add_argument("--docver-id", type=int, required=True, help="Target document_version.id")
    parser.add_argument("--source-kind", default=DEFAULT_SOURCE_KIND)
    parser.add_argument("--model-name", default=DEFAULT_MODEL)
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--write", action="store_true", help="Actually mutate the database; default is dry-run")
    args = parser.parse_args()

    chunks_path = Path(args.retrieval_chunks_json).expanduser().resolve()
    payload = json.loads(chunks_path.read_text(encoding="utf-8"))
    chunks = [chunk for chunk in payload.get("retrieval_chunks") or [] if isinstance(chunk, dict)]
    source_kind = str(args.source_kind or DEFAULT_SOURCE_KIND).strip() or DEFAULT_SOURCE_KIND

    engine = build_engine()
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        document_version, document, extracted = _load_target(session=session, docver_id=int(args.docver_id))
        existing_ids = _existing_artifact_ids(session=session, document_version_id=document_version.id, source_kind=source_kind)
        artifacts = _build_artifacts(chunks=chunks, document_version_id=document_version.id, source_kind=source_kind, document_title=document.title_he)

        result = {
            "mode": "write" if args.write else "dry_run",
            "retrieval_chunks_json": str(chunks_path),
            "doc_id": document.id,
            "docver_id": document_version.id,
            "source_site_id": document.source_site_id,
            "extracted_document_id": extracted.id,
            "source_kind": source_kind,
            "topic_tree_version": TOPIC_TREE_VERSION,
            "topic_assignment_backend": TOPIC_ASSIGNMENT_BACKEND,
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

        seed_root_nodes(session, source_site_id=int(document.source_site_id), document_version_id=document_version.id)
        semantic_run = _upsert_semantic_run(session=session, document_version_id=document_version.id, chunks_path=chunks_path, model_name=str(args.model_name), payload=payload)

        written = 0
        semantic_linked = 0
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
            session.flush()
            session.execute(text("INSERT INTO artifact_fts (artifact_id, retrieval_text) VALUES (:artifact_id, :retrieval_text)"), {"artifact_id": artifact["artifact_id"], "retrieval_text": artifact["retrieval_text"]})
            trigram_rows = [{"artifact_id": artifact["artifact_id"], "trigram": trigram} for trigram in artifact["trigrams"]]
            if trigram_rows:
                session.execute(text("INSERT INTO retrieval_artifact_trigram (artifact_id, trigram) VALUES (:artifact_id, :trigram)"), trigram_rows)
            if artifact.get("primary_topic_he"):
                session.add(
                    ArtifactTopicAnnotation(
                        artifact_id=artifact["artifact_id"],
                        structural_topic_he=artifact.get("structural_topic_he"),
                        structural_topic_norm=normalize_for_search(str(artifact.get("structural_topic_he") or "")) or None,
                        primary_topic_he=artifact.get("primary_topic_he"),
                        primary_topic_norm=normalize_for_search(str(artifact.get("primary_topic_he") or "")) or None,
                        secondary_topics_json=json.dumps([artifact.get("child_label_he")] if artifact.get("child_label_he") else [], ensure_ascii=False),
                        section_summary=artifact.get("section_summary"),
                        classifier_confidence=artifact.get("classifier_confidence"),
                        classifier_route=TOPIC_ASSIGNMENT_BACKEND,
                        provider_name="ollama",
                        model_name=str(args.model_name),
                    )
                )
            link_result = persist_v4_semantic_links(session, source_site_id=int(document.source_site_id), artifact=row, chunk=artifact["chunk"], semantic_run=semantic_run)
            if link_result is not None:
                semantic_linked += 1
            written += 1

        _refresh_semantic_support_counts(session=session, source_site_id=int(document.source_site_id))
        semantic_run.finished_at = datetime.utcnow()
        semantic_run.status = "succeeded"
        semantic_run.api_call_count = int(payload.get("api_call_count") or 0)
        semantic_run.validation_report_json = json.dumps({"written_artifact_count": written, "semantic_linked_count": semantic_linked}, ensure_ascii=False)
        session.commit()
        print(json.dumps({**result, "written": written, "deleted_existing": len(existing_ids), "semantic_linked": semantic_linked}, ensure_ascii=False))
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
    return list(session.execute(select(RetrievalArtifact.artifact_id).where(RetrievalArtifact.document_version_id == document_version_id).where(RetrievalArtifact.source_kind == source_kind)).scalars().all())


def _delete_artifacts(*, session: Session, artifact_ids: list[str]) -> None:
    if not artifact_ids:
        return
    session.query(ArtifactSemanticLink).filter(ArtifactSemanticLink.artifact_id.in_(artifact_ids)).delete(synchronize_session=False)
    session.query(ArtifactTopicAnnotation).filter(ArtifactTopicAnnotation.artifact_id.in_(artifact_ids)).delete(synchronize_session=False)
    session.query(RetrievalArtifactEmbedding).filter(RetrievalArtifactEmbedding.artifact_id.in_(artifact_ids)).delete(synchronize_session=False)
    session.query(RetrievalArtifact).filter(RetrievalArtifact.artifact_id.in_(artifact_ids)).delete(synchronize_session=False)
    session.execute(text("DELETE FROM artifact_fts WHERE artifact_id = :artifact_id"), [{"artifact_id": artifact_id} for artifact_id in artifact_ids])
    session.execute(text("DELETE FROM retrieval_artifact_trigram WHERE artifact_id = :artifact_id"), [{"artifact_id": artifact_id} for artifact_id in artifact_ids])


def _refresh_semantic_support_counts(*, session: Session, source_site_id: int) -> None:
    nodes = session.execute(select(SemanticNode).where(SemanticNode.source_site_id == source_site_id, SemanticNode.node_kind == "topic")).scalars().all()
    nodes_by_id = {int(node.id): node for node in nodes}
    root_counts: dict[str, int] = {}
    child_counts: dict[int, int] = {}
    links = session.execute(select(ArtifactSemanticLink).where(ArtifactSemanticLink.semantic_node_id.in_(nodes_by_id))).scalars().all()
    for link in links:
        node = nodes_by_id.get(int(link.semantic_node_id))
        if node is None:
            continue
        metadata = _loads_dict(link.metadata_json)
        root_topic_id = str(metadata.get("root_topic_id") or _loads_dict(node.metadata_json).get("root_topic_id") or "")
        if root_topic_id:
            root_counts[root_topic_id] = root_counts.get(root_topic_id, 0) + 1
        if node.parent_node_id is not None:
            child_counts[int(node.id)] = child_counts.get(int(node.id), 0) + 1
    for node in nodes:
        metadata = _loads_dict(node.metadata_json)
        root_topic_id = str(metadata.get("root_topic_id") or "")
        if node.parent_node_id is None:
            node.support_count = int(root_counts.get(root_topic_id, 0))
            node.status = "active"
        else:
            support_count = int(child_counts.get(int(node.id), 0))
            node.support_count = support_count
            if support_count <= 0:
                node.status = "rejected"


def _loads_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _upsert_semantic_run(*, session: Session, document_version_id: int, chunks_path: Path, model_name: str, payload: dict[str, Any]) -> SemanticDocumentRun:
    prompt_hash = hashlib.sha256(f"{TOPIC_TREE_VERSION}|{chunks_path}|{payload.get('retrieval_set_id') or ''}".encode("utf-8")).hexdigest()
    row = session.execute(select(SemanticDocumentRun).where(SemanticDocumentRun.document_version_id == document_version_id, SemanticDocumentRun.prompt_hash == prompt_hash, SemanticDocumentRun.model_provider == "ollama", SemanticDocumentRun.model_name == model_name)).scalar_one_or_none()
    if row is None:
        row = SemanticDocumentRun(document_version_id=document_version_id, prompt_hash=prompt_hash, model_provider="ollama", model_name=model_name, status="running", api_call_count=0, extraction_payload_json=json.dumps(_compact_payload(payload), ensure_ascii=False), canonicalization_report_json=json.dumps({"provenance": V4_PROVENANCE, "backend_version": BACKEND_VERSION}, ensure_ascii=False))
        session.add(row)
        session.flush()
    else:
        row.status = "running"
        row.error_code = None
        row.error_text = None
        row.extraction_payload_json = json.dumps(_compact_payload(payload), ensure_ascii=False)
    return row


def _build_artifacts(*, chunks: list[dict[str, Any]], document_version_id: int, source_kind: str, document_title: str) -> list[dict[str, Any]]:
    artifacts = []
    for ordinal, original_chunk in enumerate(chunks):
        retrieval_text = str(original_chunk.get("chunk_text") or original_chunk.get("raw_text") or "").strip()
        if not retrieval_text:
            continue
        local_id = str(original_chunk.get("retrieval_artifact_id") or original_chunk.get("chunk_id") or f"rc_{ordinal}")
        artifact_id = f"pfv4_{document_version_id}_{local_id}"[:64]
        chunk = _rewrite_chunk_artifact_id(original_chunk, artifact_id=artifact_id)
        norm = normalize_for_search(retrieval_text)
        trigrams = sorted(build_trigrams(norm))
        page = _positive_int_or_none(chunk.get("source_page") or chunk.get("page"))
        title = str(chunk.get("child_label_he") or chunk.get("root_label_he") or chunk.get("structural_role") or document_title).strip()
        header_path = _header_path(chunk)
        synthetic_offset = ordinal * 100000
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "section_id": _short_text(chunk.get("section_id"), 64),
                "source_kind": source_kind,
                "artifact_kind": str(chunk.get("artifact_kind") or "pdf_first_v4_retrieval_chunk"),
                "title_he": title,
                "child_label_he": chunk.get("child_label_he"),
                "meeting_date": _first_date(chunk.get("entity_facts") or []),
                "header_path_json": json.dumps(header_path, ensure_ascii=False),
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
                "primary_topic_he": str(chunk.get("root_label_he") or "").strip() or None,
                "structural_topic_he": str(chunk.get("structural_role") or "").strip() or None,
                "section_summary": str(chunk.get("summary_he") or "")[:1000] or None,
                "classifier_confidence": _float_or_none(chunk.get("topic_assignment_confidence")),
                "chunk": chunk,
            }
        )
    return artifacts


def _rewrite_chunk_artifact_id(chunk: dict[str, Any], *, artifact_id: str) -> dict[str, Any]:
    out = json.loads(json.dumps(chunk, ensure_ascii=False))
    out["retrieval_artifact_id"] = artifact_id
    out["chunk_id"] = artifact_id
    if isinstance(out.get("evidence_contract"), dict):
        out["evidence_contract"]["artifact_id"] = artifact_id
    for ref in out.get("evidence_refs") or []:
        if isinstance(ref, dict):
            ref["retrieval_artifact_id"] = artifact_id
    if isinstance(out.get("topic_node"), dict):
        for ref in out["topic_node"].get("evidence_refs") or []:
            if isinstance(ref, dict):
                ref["retrieval_artifact_id"] = artifact_id
    return out


def _metadata_for_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "pipeline_step": "step5_v4_retrieval_chunks",
        "provenance": V4_PROVENANCE,
        "backend_version": BACKEND_VERSION,
        "topic_tree_version": TOPIC_TREE_VERSION,
        "topic_assignment_backend": TOPIC_ASSIGNMENT_BACKEND,
        "retrieval_artifact_id": chunk.get("retrieval_artifact_id"),
        "retrieval_set_id": chunk.get("retrieval_set_id"),
        "structure_unit_id": chunk.get("structure_unit_id"),
        "semantic_unit_id": chunk.get("semantic_unit_id"),
        "source_semantic_unit_ids": chunk.get("source_semantic_unit_ids") or [],
        "source_window_id": chunk.get("source_window_id"),
        "source_region_ids": chunk.get("source_region_ids") or [],
        "source_block_ids": chunk.get("source_block_ids") or [],
        "structural_role": chunk.get("structural_role"),
        "section_id": chunk.get("section_id"),
        "section_number": chunk.get("section_number"),
        "source_page": chunk.get("source_page") or chunk.get("page"),
        "page": chunk.get("page") or chunk.get("source_page"),
        "raw_text": chunk.get("raw_text"),
        "corrected_text_he": chunk.get("corrected_text_he"),
        "summary_he": chunk.get("summary_he"),
        "body_text_source": "chunk.raw_text",
        "retrieval_text_source": "chunk.chunk_text",
        "structure_metadata": chunk.get("structure_metadata") or {},
        "spans": chunk.get("spans") or [],
        "topic_assignment": chunk.get("topic_assignment") or {},
        "topic_node": chunk.get("topic_node"),
        "topic_ids": chunk.get("topic_ids") or [],
        "category_ids": chunk.get("category_ids") or [],
        "primary_category_id": chunk.get("primary_category_id"),
        "evidence_contract": chunk.get("evidence_contract"),
        "evidence_refs": chunk.get("evidence_refs") or [],
        "decision_candidate_id": chunk.get("decision_candidate_id"),
        "decision_kind": chunk.get("decision_kind"),
        "outcome_status": chunk.get("outcome_status"),
        "legal_effect": chunk.get("legal_effect"),
        "primary_time": chunk.get("primary_time"),
        "time_anchors": chunk.get("time_anchors") or [],
        "map_entities": chunk.get("map_entities") or [],
        "spatial_representation": chunk.get("spatial_representation"),
        "topic_assignment_route": chunk.get("topic_assignment_route"),
        "retrieval_weight": chunk.get("retrieval_weight"),
    }


def _header_path(chunk: dict[str, Any]) -> list[str]:
    refs = chunk.get("evidence_refs") or []
    if refs and isinstance(refs[0], dict) and isinstance(refs[0].get("header_path"), list):
        return [str(value) for value in refs[0]["header_path"] if str(value).strip()]
    return [str(value) for value in [chunk.get("root_label_he"), chunk.get("child_label_he"), chunk.get("structural_role")] if str(value or "").strip()]


def _compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {"step": payload.get("step"), "topic_tree_version": payload.get("topic_tree_version"), "topic_assignment_backend": payload.get("topic_assignment_backend"), "retrieval_set_id": payload.get("retrieval_set_id"), "chunk_count": payload.get("chunk_count"), "skipped_count": payload.get("skipped_count")}


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
    return text_value[:limit] if text_value else None


if __name__ == "__main__":
    raise SystemExit(main())
