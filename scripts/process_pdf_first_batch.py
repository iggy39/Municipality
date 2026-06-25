#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from municipality.db import build_engine
from municipality.migrations import apply_all
from municipality.models import Document, DocumentVersion, ExtractedDocument, SourceSite
from municipality.storage import RawStorage


DEFAULT_INPUT_ROOT = PROJECT_ROOT / "rag_eval" / "data" / "raw_docs" / "ashdod_council_by_year"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "rag_eval" / "runs" / "ashdod_pdf_first_batch"
DEFAULT_PIPELINE_SCRIPTS = PROJECT_ROOT / "rag_eval" / "runs" / "ashdod_2025_regular_3_short_pdf" / "pdf_first_pipeline" / "scripts"
DEFAULT_STORAGE_ROOT = PROJECT_ROOT / "storage" / "raw"
ATTACHMENT_MARKERS = {"נספח", "נספחים", "attachment", "attachments"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Process year folders through the PDF-first RAG pipeline")
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--pipeline-scripts", type=Path, default=DEFAULT_PIPELINE_SCRIPTS)
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_STORAGE_ROOT)
    parser.add_argument("--city", default="ashdod")
    parser.add_argument("--years", default="2022,2023,2024,2025")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--pages", help="Optional page list/ranges passed to model-heavy steps")
    parser.add_argument("--vision-model", default="mistral-small3.1:latest")
    parser.add_argument("--dictalm-model", default="dicta-il/DictaLM-3.0-24B-Thinking:bf16")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--min-text-page-ratio", type=float, default=0.5)
    parser.add_argument("--min-chunk-count", type=int, default=1)
    parser.add_argument("--min-topic-labeled-ratio", type=float, default=0.25)
    parser.add_argument("--dry-run", action="store_true", help="List selected PDFs without processing")
    parser.add_argument("--force", action="store_true", help="Re-run even if manifest has completed item")
    args = parser.parse_args()

    input_root = args.input_root.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    scripts_dir = args.pipeline_scripts.expanduser().resolve()
    years = _parse_years(args.years)
    pdfs = _select_pdfs(input_root=input_root, years=years, limit=args.limit)
    if not pdfs:
        raise SystemExit(f"no PDFs found under {input_root} for years={years}")
    print(json.dumps({"selected_pdf_count": len(pdfs), "years": years, "dry_run": bool(args.dry_run)}, ensure_ascii=False), flush=True)
    if args.dry_run:
        for pdf in pdfs[:20]:
            print(json.dumps({"pdf": str(pdf), "source_kind": _source_kind_for_path(pdf)}, ensure_ascii=False), flush=True)
        return 0

    _require_scripts(scripts_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "batch_status.jsonl"
    completed = set() if args.force else _completed_keys(manifest_path)

    engine = build_engine()
    apply_all(engine, PROJECT_ROOT / "migrations")
    storage = RawStorage(args.storage_root.expanduser().resolve())
    try:
        for index, pdf_path in enumerate(pdfs, start=1):
            item_started = time.perf_counter()
            source_kind = _source_kind_for_path(pdf_path)
            key = _item_key(pdf_path)
            if key in completed:
                print(json.dumps({"index": index, "pdf": str(pdf_path), "status": "skipped_completed"}, ensure_ascii=False), flush=True)
                continue
            run_dir = output_root / _run_name(pdf_path, input_root=input_root)
            try:
                with Session(engine) as session:
                    docver_id = _register_pdf(
                        session=session,
                        storage=storage,
                        city=str(args.city),
                        input_root=input_root,
                        pdf_path=pdf_path,
                        source_kind=source_kind,
                    )
                    session.commit()

                paths = _run_pipeline(
                    scripts_dir=scripts_dir,
                    pdf_path=pdf_path,
                    run_dir=run_dir,
                    pages=args.pages,
                    vision_model=args.vision_model,
                    dictalm_model=args.dictalm_model,
                    ollama_base_url=args.ollama_base_url,
                    timeout_seconds=args.timeout_seconds,
                )
                with Session(engine) as session:
                    extracted_id = _upsert_extracted_document(session=session, docver_id=docver_id, pages_json=paths["pages_json"])
                    session.commit()

                import_result = _run_import(docver_id=docver_id, source_kind=source_kind, retrieval_chunks_json=paths["retrieval_chunks_json"])
                stats = _collect_stats(paths=paths, import_result=import_result)
                status = {
                    "status": "completed",
                    "key": key,
                    "index": index,
                    "pdf": str(pdf_path),
                    "run_dir": str(run_dir),
                    "docver_id": docver_id,
                    "extracted_document_id": extracted_id,
                    "source_kind": source_kind,
                    "elapsed_seconds": round(time.perf_counter() - item_started, 3),
                    "stats": stats,
                }
                _append_jsonl(manifest_path, status)
                print(json.dumps(_compact_success(status), ensure_ascii=False), flush=True)
                verdict = _quality_verdict(
                    stats=stats,
                    min_text_page_ratio=float(args.min_text_page_ratio),
                    min_chunk_count=int(args.min_chunk_count),
                    min_topic_labeled_ratio=float(args.min_topic_labeled_ratio),
                )
                if not verdict["accept"]:
                    stop = {**status, "status": "stopped_poor_stats", "quality_verdict": verdict}
                    _append_jsonl(manifest_path, stop)
                    print(json.dumps({"stop_reason": "poor_document_stats", **verdict}, ensure_ascii=False), flush=True)
                    return 2
            except Exception as exc:
                failure = {
                    "status": "failed",
                    "key": key,
                    "index": index,
                    "pdf": str(pdf_path),
                    "source_kind": source_kind,
                    "run_dir": str(run_dir),
                    "elapsed_seconds": round(time.perf_counter() - item_started, 3),
                    "error": str(exc),
                }
                diagnostics = _failure_diagnostics(run_dir)
                if diagnostics:
                    failure["diagnostics"] = diagnostics
                _append_jsonl(manifest_path, failure)
                print(json.dumps(failure, ensure_ascii=False), flush=True)
                return 1
    finally:
        engine.dispose()
    return 0


def _run_pipeline(
    *,
    scripts_dir: Path,
    pdf_path: Path,
    run_dir: Path,
    pages: str | None,
    vision_model: str,
    dictalm_model: str,
    ollama_base_url: str,
    timeout_seconds: float,
) -> dict[str, Path]:
    outputs = run_dir / "pdf_first_pipeline" / "outputs"
    step1 = outputs / "step1_page_dissect"
    overlays = outputs / "step1_bbox_overlays"
    qa = outputs / "step1_visual_bbox_qa"
    step2 = outputs / "step2_visual_layout"
    step3 = outputs / "step3_semantic_interpretation"
    step31 = outputs / "step3_1_structure_normalization"
    step35 = outputs / "step3_5_entity_grounding"
    step4 = outputs / "step4_controlled_topic_assignment"
    step45 = outputs / "step4_5_topic_canonicalization"
    step5 = outputs / "step5_retrieval_chunks"
    for directory in (step1, overlays, qa, step2, step3, step31, step35, step4, step45, step5):
        directory.mkdir(parents=True, exist_ok=True)

    page_args = ["--pages", pages] if pages else []
    _run_cmd([sys.executable, str(scripts_dir / "step1_page_dissect.py"), str(pdf_path), "--output-dir", str(step1)])
    pages_json = step1 / "pages.json"
    _run_cmd([sys.executable, str(scripts_dir / "step1_make_bbox_overlays.py"), str(pdf_path), str(pages_json), "--output-dir", str(overlays)])
    _run_cmd(
        [
            sys.executable,
            str(scripts_dir / "step1_visual_bbox_qa.py"),
            str(pages_json),
            str(overlays),
            "--output-dir",
            str(qa),
            "--model",
            vision_model,
            "--ollama-base-url",
            ollama_base_url,
            "--timeout-seconds",
            str(timeout_seconds),
            *page_args,
        ]
    )
    qa_report = qa / "bbox_quality_report.json"
    _run_cmd(
        [
            sys.executable,
            str(scripts_dir / "step2_visual_layout_classify.py"),
            str(pages_json),
            str(overlays),
            str(qa_report),
            "--output-dir",
            str(step2),
            "--model",
            vision_model,
            "--ollama-base-url",
            ollama_base_url,
            "--timeout-seconds",
            str(timeout_seconds),
            *page_args,
        ]
    )
    visual_report = step2 / "visual_layout_report.json"
    _run_cmd(
        [
            sys.executable,
            str(scripts_dir / "step3_semantic_interpret.py"),
            str(visual_report),
            "--output-dir",
            str(step3),
            "--model",
            dictalm_model,
            "--ollama-base-url",
            ollama_base_url,
            "--timeout-seconds",
            str(timeout_seconds),
            *page_args,
        ]
    )
    semantic_units = step3 / "semantic_units.json"
    _run_cmd([sys.executable, str(scripts_dir / "step3_1_structure_normalize.py"), str(semantic_units), "--output-dir", str(step31)])
    structure_units = step31 / "structure_units.json"
    _run_cmd([sys.executable, str(scripts_dir / "step3_5_ground_entities.py"), str(semantic_units), "--output-dir", str(step35)])
    entity_facts = step35 / "entity_facts.json"
    _run_cmd(
        [
            sys.executable,
            str(scripts_dir / "step4_controlled_topic_assign.py"),
            str(semantic_units),
            str(entity_facts),
            "--output-dir",
            str(step4),
            "--structure-units-json",
            str(structure_units),
            "--model",
            dictalm_model,
            "--ollama-base-url",
            ollama_base_url,
            "--timeout-seconds",
            str(timeout_seconds),
        ]
    )
    topic_assignments = step4 / "topic_assignments.json"
    _run_cmd([sys.executable, str(scripts_dir / "step4_5_canonicalize_topics.py"), str(topic_assignments), "--output-dir", str(step45)])
    canonical_topics = step45 / "canonical_topics.json"
    _run_cmd(
        [
            sys.executable,
            str(scripts_dir / "step5_build_retrieval_chunks.py"),
            "--structure-units-json",
            str(structure_units),
            "--topic-assignments-json",
            str(topic_assignments),
            "--canonical-topics-json",
            str(canonical_topics),
            "--entity-facts-json",
            str(entity_facts),
            "--output-dir",
            str(step5),
        ],
        allowed_returncodes={0, 2},
    )
    return {
        "pages_json": pages_json,
        "qa_report": qa_report,
        "visual_report": visual_report,
        "semantic_units_json": semantic_units,
        "structure_units_json": structure_units,
        "topic_assignments_json": topic_assignments,
        "canonical_topics_json": canonical_topics,
        "entity_facts_json": entity_facts,
        "retrieval_chunks_json": step5 / "retrieval_chunks.json",
        "step5_validation_json": step5 / "validation_report.json",
    }


def _register_pdf(*, session: Session, storage: RawStorage, city: str, input_root: Path, pdf_path: Path, source_kind: str) -> int:
    site = _ensure_source_site(session=session, city=city)
    payload = pdf_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    rel = _safe_rel(pdf_path, input_root=input_root)
    canonical_url = f"local://pdf_first_batch/{city}/{rel.as_posix()}"
    document = session.execute(select(Document).where(Document.canonical_url == canonical_url)).scalar_one_or_none()
    doc_kind = "attachment" if source_kind.endswith("_attachment") else "protocol_full"
    if document is None:
        document = Document(
            source_site_id=site.id,
            document_external_id=f"pdf_first_batch:{digest[:24]}",
            canonical_url=canonical_url,
            title_he=pdf_path.stem,
            doc_kind=doc_kind,
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        session.add(document)
        session.flush()
    else:
        document.last_seen_at = datetime.utcnow()
        document.doc_kind = doc_kind
    version = session.execute(select(DocumentVersion).where(DocumentVersion.document_id == document.id, DocumentVersion.sha256 == digest)).scalar_one_or_none()
    if version is None:
        storage_uri = storage.write(city, doc_kind, digest, payload)
        version = DocumentVersion(
            document_id=document.id,
            sha256=digest,
            byte_size=len(payload),
            storage_uri=storage_uri,
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
        session.add(version)
        session.flush()
    return int(version.id)


def _upsert_extracted_document(*, session: Session, docver_id: int, pages_json: Path) -> int:
    payload = json.loads(pages_json.read_text(encoding="utf-8"))
    pages = payload.get("pages") or []
    full_text, page_rows, citation_map = _text_from_pages(pages)
    quality = _step1_quality(payload)
    row = session.execute(select(ExtractedDocument).where(ExtractedDocument.document_version_id == docver_id)).scalar_one_or_none()
    if row is None:
        row = ExtractedDocument(document_version_id=docver_id, parser_name="pdf_first_step1", parser_version="batch_v1", status="completed")
        session.add(row)
    row.extracted_text = full_text
    row.page_count = len(pages)
    row.pages_json = json.dumps(page_rows, ensure_ascii=False)
    row.citation_map_json = json.dumps(citation_map, ensure_ascii=False)
    row.quality_score = quality["text_page_ratio"]
    row.quality_flags_json = json.dumps(quality["flags"], ensure_ascii=False)
    row.quality_summary_json = json.dumps(quality, ensure_ascii=False)
    row.error_code = None if full_text.strip() else "PDF_FIRST_EMPTY_TEXT"
    row.warning_text = None
    row.updated_at = datetime.utcnow()
    session.flush()
    return int(row.id)


def _run_import(*, docver_id: int, source_kind: str, retrieval_chunks_json: Path) -> dict[str, Any]:
    result = _run_cmd(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "import_pdf_first_retrieval_chunks.py"),
            "--retrieval-chunks-json",
            str(retrieval_chunks_json),
            "--docver-id",
            str(docver_id),
            "--source-kind",
            source_kind,
            "--replace-existing",
            "--write",
        ],
        capture=True,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else {}


def _run_cmd(command: list[str], *, capture: bool = False, allowed_returncodes: set[int] | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    print(json.dumps({"run": Path(command[1]).name if len(command) > 1 else command[0]}, ensure_ascii=False), flush=True)
    result = subprocess.run(command, cwd=PROJECT_ROOT, env=env, text=True, capture_output=capture)
    accepted = allowed_returncodes or {0}
    if result.returncode not in accepted:
        if capture and result.stdout:
            print(result.stdout.strip(), flush=True)
        if capture and result.stderr:
            print(result.stderr.strip(), file=sys.stderr, flush=True)
        raise RuntimeError(f"command failed rc={result.returncode}: {' '.join(command)}")
    return result


def _collect_stats(*, paths: dict[str, Path], import_result: dict[str, Any]) -> dict[str, Any]:
    pages = json.loads(paths["pages_json"].read_text(encoding="utf-8"))
    chunks = json.loads(paths["retrieval_chunks_json"].read_text(encoding="utf-8"))
    step5_validation = json.loads(paths["step5_validation_json"].read_text(encoding="utf-8"))
    page_count = int((pages.get("summary") or {}).get("page_count") or 0)
    pages_with_text = int((pages.get("summary") or {}).get("pages_with_text") or 0)
    chunk_count = int(chunks.get("chunk_count") or len(chunks.get("retrieval_chunks") or []))
    topic_labeled = int(step5_validation.get("topic_labeled_chunk_count") or 0)
    return {
        "page_count": page_count,
        "pages_with_text": pages_with_text,
        "text_page_ratio": round(pages_with_text / page_count, 4) if page_count else 0.0,
        "chunk_count": chunk_count,
        "topic_labeled_chunk_count": topic_labeled,
        "topic_labeled_ratio": round(topic_labeled / chunk_count, 4) if chunk_count else 0.0,
        "blocked_issue_count": int(step5_validation.get("blocked_issue_count") or 0),
        "imported_artifact_count": int(import_result.get("written") or 0),
    }


def _quality_verdict(*, stats: dict[str, Any], min_text_page_ratio: float, min_chunk_count: int, min_topic_labeled_ratio: float) -> dict[str, Any]:
    reasons = []
    if float(stats.get("text_page_ratio") or 0.0) < min_text_page_ratio:
        reasons.append("too_few_pages_with_native_text")
    if int(stats.get("chunk_count") or 0) < min_chunk_count:
        reasons.append("too_few_retrieval_chunks")
    if int(stats.get("imported_artifact_count") or 0) < min_chunk_count:
        reasons.append("too_few_imported_artifacts")
    if float(stats.get("topic_labeled_ratio") or 0.0) < min_topic_labeled_ratio:
        reasons.append("too_few_topic_labeled_chunks")
    if int(stats.get("blocked_issue_count") or 0) > 0:
        reasons.append("step5_blocked_issues")
    suggestions = {
        "too_few_pages_with_native_text": "Run OCR/hybrid OCR for this PDF or lower --min-text-page-ratio only if native extraction is expected to be sparse.",
        "too_few_retrieval_chunks": "Inspect Step 3/3.1 outputs; widen --pages or fix visual/semantic segmentation.",
        "too_few_imported_artifacts": "Inspect import_pdf_first_retrieval_chunks output and Step 5 retrieval_chunks.json.",
        "too_few_topic_labeled_chunks": "Inspect Step 4/4.5 topic assignment; lower --min-topic-labeled-ratio only for attachment-heavy documents.",
        "step5_blocked_issues": "Open Step 5 validation_report.json and fix blocked validation issues before continuing.",
    }
    return {"accept": not reasons, "reasons": reasons, "suggested_fixes": [suggestions[reason] for reason in reasons]}


def _failure_diagnostics(run_dir: Path) -> dict[str, Any]:
    outputs = run_dir / "pdf_first_pipeline" / "outputs"
    visual_report = outputs / "step2_visual_layout" / "visual_layout_report.json"
    if visual_report.exists():
        payload = json.loads(visual_report.read_text(encoding="utf-8"))
        blocked_pages = [int(page) for page in payload.get("blocked_pages") or []]
        if blocked_pages:
            return {
                "stage": "step2_visual_layout",
                "blocked_pages": blocked_pages,
                "suggested_fixes": [
                    "Re-run the blocked pages manually after inspecting their Step 2 evidence tables and overlays.",
                    "If Mistral returns needs_recheck without contradictions, relax Step 2 acceptance for contradiction-free pages.",
                    "If local layout_quality_issues block pages, split oversized body regions or tune structured/header heuristics before continuing.",
                ],
            }
    step3_validation = outputs / "step3_semantic_interpretation" / "validation_report.json"
    if step3_validation.exists():
        payload = json.loads(step3_validation.read_text(encoding="utf-8"))
        if not payload.get("accept_for_next_step"):
            return {
                "stage": "step3_semantic_interpretation",
                "window_count": payload.get("window_count"),
                "unit_count": payload.get("unit_count"),
                "model_error_count": payload.get("model_error_count"),
                "coverage_ok": payload.get("coverage_ok"),
                "suggested_fixes": [
                    "Increase Step 3 Ollama num_predict if responses end with done_reason=length.",
                    "Reduce --max-window-chars or process fewer pages per run if JSON remains malformed.",
                ],
            }
    return {}


def _select_pdfs(*, input_root: Path, years: list[int], limit: int | None) -> list[Path]:
    files: list[Path] = []
    for year in years:
        year_dir = input_root / str(year)
        if year_dir.exists():
            files.extend(sorted(path for path in year_dir.rglob("*.pdf") if path.is_file()))
    return files[: max(0, limit)] if limit is not None else files


def _source_kind_for_path(path: Path) -> str:
    folded = str(path).casefold()
    return "pdf_first_attachment" if any(marker.casefold() in folded for marker in ATTACHMENT_MARKERS) else "pdf_first_protocol"


def _ensure_source_site(*, session: Session, city: str) -> SourceSite:
    root_url = f"local://pdf_first_batch/{city}"
    site = session.execute(select(SourceSite).where(SourceSite.municipality_slug == city, SourceSite.root_url == root_url)).scalar_one_or_none()
    if site is not None:
        return site
    site = SourceSite(municipality_slug=city, name=city, root_url=root_url)
    session.add(site)
    session.flush()
    return site


def _text_from_pages(pages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    parts = []
    page_rows = []
    citation_map = []
    offset = 0
    for page in pages:
        number = int(page.get("page") or len(page_rows) + 1)
        text = str(page.get("plain_text") or "")
        prefix = f"\n===== PAGE {number} =====\n" if parts else f"===== PAGE {number} =====\n"
        start = offset + len(prefix)
        part = prefix + text
        end = start + len(text)
        parts.append(part)
        page_rows.append({"page": number, "text": text, "start_offset": start, "end_offset": end})
        citation_map.append({"page": number, "start_offset": start, "end_offset": end, "citation": f"p.{number}"})
        offset += len(part)
    return "".join(parts), page_rows, citation_map


def _step1_quality(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") or {}
    page_count = int(summary.get("page_count") or 0)
    pages_with_text = int(summary.get("pages_with_text") or 0)
    ratio = round(pages_with_text / page_count, 4) if page_count else 0.0
    flags = [] if ratio > 0 else ["no_native_pdf_text"]
    return {"page_count": page_count, "pages_with_text": pages_with_text, "text_page_ratio": ratio, "flags": flags}


def _parse_years(value: str) -> list[int]:
    years = []
    for part in value.split(","):
        item = part.strip()
        if item:
            years.append(int(item))
    return years


def _safe_rel(path: Path, *, input_root: Path) -> Path:
    try:
        return path.resolve().relative_to(input_root.resolve())
    except ValueError:
        return Path(path.name)


def _run_name(path: Path, *, input_root: Path) -> str:
    rel = _safe_rel(path, input_root=input_root).as_posix()
    slug = re.sub(r"[^0-9A-Za-z._-]+", "_", rel).strip("_")[:120]
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"{slug}_{digest}"


def _item_key(path: Path) -> str:
    stat = path.stat()
    return hashlib.sha256(f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}".encode("utf-8")).hexdigest()


def _completed_keys(manifest_path: Path) -> set[str]:
    if not manifest_path.exists():
        return set()
    out = set()
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("status") == "completed" and payload.get("key"):
            out.add(str(payload["key"]))
    return out


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _compact_success(status: dict[str, Any]) -> dict[str, Any]:
    stats = status["stats"]
    return {
        "status": "completed",
        "index": status["index"],
        "docver_id": status["docver_id"],
        "source_kind": status["source_kind"],
        "pages": stats["page_count"],
        "text_pages": stats["pages_with_text"],
        "chunks": stats["chunk_count"],
        "topic_labeled": stats["topic_labeled_chunk_count"],
        "imported": stats["imported_artifact_count"],
        "elapsed_seconds": status["elapsed_seconds"],
    }


def _require_scripts(scripts_dir: Path) -> None:
    required = [
        "step1_page_dissect.py",
        "step1_make_bbox_overlays.py",
        "step1_visual_bbox_qa.py",
        "step2_visual_layout_classify.py",
        "step3_semantic_interpret.py",
        "step3_1_structure_normalize.py",
        "step3_5_ground_entities.py",
        "step4_controlled_topic_assign.py",
        "step4_5_canonicalize_topics.py",
        "step5_build_retrieval_chunks.py",
    ]
    missing = [name for name in required if not (scripts_dir / name).exists()]
    if missing:
        raise SystemExit(f"missing pipeline scripts in {scripts_dir}: {missing}")


if __name__ == "__main__":
    raise SystemExit(main())
