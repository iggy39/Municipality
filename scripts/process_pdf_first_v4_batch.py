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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from municipality.db import build_engine  # noqa: E402
from municipality.migrations import apply_all  # noqa: E402
from municipality.models import ArtifactSemanticLink, Document, DocumentVersion, RetrievalArtifact, SemanticAlias, SemanticNode  # noqa: E402
from municipality.pdf_first_v4_topic_tree import alias_lines, current_tree_from_db, tree_lines  # noqa: E402
from municipality.storage import RawStorage  # noqa: E402
from process_pdf_first_batch import _register_pdf, _step1_quality, _text_from_pages, _upsert_extracted_document  # noqa: E402

DEFAULT_INPUT_ROOT = PROJECT_ROOT / "rag_eval" / "data" / "raw_docs" / "ashdod_council_by_year"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "rag_eval" / "runs" / "ashdod_pdf_first_v4_batch"
DEFAULT_PIPELINE_SCRIPTS = PROJECT_ROOT / "rag_eval" / "runs" / "ashdod_2025_regular_3_short_pdf" / "pdf_first_pipeline" / "scripts"
DEFAULT_STORAGE_ROOT = PROJECT_ROOT / "storage" / "raw"
ATTACHMENT_MARKERS = {"נספח", "נספחים", "attachment", "attachments"}
PROTOCOL_MARKERS = {"פרוטוקול", "protocol"}


@dataclass(slots=True)
class Packet:
    path: Path
    attachments: list[Path]
    protocols: list[Path]


def main() -> int:
    parser = argparse.ArgumentParser(description="Process meeting folders through PDF-first v4 packet pipeline")
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--pipeline-scripts", type=Path, default=DEFAULT_PIPELINE_SCRIPTS)
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_STORAGE_ROOT)
    parser.add_argument("--city", default="ashdod")
    parser.add_argument("--years", default="2025")
    parser.add_argument("--packet", type=Path, help="Specific meeting packet folder to process")
    parser.add_argument("--limit-packets", type=int)
    parser.add_argument("--limit-pdfs", type=int, help="Stop after this many PDFs across selected packets")
    parser.add_argument("--protocol-mode", choices=["preferred", "all", "full-only", "short-only"], default="preferred", help="preferred selects one protocol per packet, using a short protocol when available")
    parser.add_argument("--pages", help="Optional page list/ranges passed to model-heavy steps")
    parser.add_argument("--vision-model", default="mistral-small3.1:latest")
    parser.add_argument("--dictalm-model", default="dicta-il/DictaLM-3.0-24B-Thinking:bf16")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--api-base-url", help="Optional running API base URL for /semantic/tree and /ask checks")
    parser.add_argument("--ask-question", default="מה אושר בפרוטוקול ומה הנושא המרכזי?")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    input_root = args.input_root.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    scripts_dir = args.pipeline_scripts.expanduser().resolve()
    _require_v4_scripts(scripts_dir)
    packets = _select_packets(input_root=input_root, years=_parse_years(str(args.years)), packet=args.packet, limit=args.limit_packets)
    if not packets:
        raise SystemExit(f"no meeting packets found under {input_root}")
    selected_pdf_count = sum(len(packet.attachments) + len(_filter_protocols(packet.protocols, mode=str(args.protocol_mode))) for packet in packets)
    print(json.dumps({"selected_packet_count": len(packets), "selected_pdf_count": selected_pdf_count, "dry_run": bool(args.dry_run)}, ensure_ascii=False), flush=True)
    if args.dry_run:
        for packet in packets:
            protocols = _filter_protocols(packet.protocols, mode=str(args.protocol_mode))
            print(json.dumps({"packet": str(packet.path), "attachments": [str(path) for path in packet.attachments], "protocols": [str(path) for path in protocols]}, ensure_ascii=False), flush=True)
        return 0

    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "v4_batch_status.jsonl"
    completed = set() if args.force else _completed_keys(manifest_path)
    engine = build_engine()
    apply_all(engine, PROJECT_ROOT / "migrations")
    storage = RawStorage(args.storage_root.expanduser().resolve())
    processed = 0
    try:
        for packet_index, packet in enumerate(packets, start=1):
            attachment_context_paths: list[Path] = []
            protocols = _filter_protocols(packet.protocols, mode=str(args.protocol_mode))
            ordered = [("attachment", path) for path in packet.attachments] + [("protocol", path) for path in protocols]
            for pdf_index, (packet_role, pdf_path) in enumerate(ordered, start=1):
                if args.limit_pdfs is not None and processed >= max(0, int(args.limit_pdfs)):
                    print(json.dumps({"status": "stopped_limit_pdfs", "processed_pdf_count": processed}, ensure_ascii=False), flush=True)
                    return 0
                key = _item_key(pdf_path)
                run_dir = output_root / _packet_run_name(packet.path, input_root=input_root) / _pdf_run_name(pdf_path)
                if key in completed:
                    if packet_role == "attachment":
                        retrieval_chunks_json = _retrieval_chunks_path(run_dir)
                        if retrieval_chunks_json.exists() and retrieval_chunks_json.stat().st_size > 0:
                            attachment_context_paths.append(retrieval_chunks_json)
                    print(json.dumps({"packet_index": packet_index, "pdf_index": pdf_index, "pdf": str(pdf_path), "status": "skipped_completed"}, ensure_ascii=False), flush=True)
                    continue
                processed += 1
                item_started = time.perf_counter()
                source_kind = f"pdf_first_v4_{packet_role}"
                try:
                    with Session(engine) as session:
                        docver_id = _register_pdf(session=session, storage=storage, city=str(args.city), input_root=input_root, pdf_path=pdf_path, source_kind=source_kind)
                        source_site_id = _source_site_id_for_docver(session=session, docver_id=docver_id)
                        existing_tree_json = _write_existing_tree_snapshot(session=session, run_dir=run_dir, source_site_id=source_site_id)
                        session.commit()
                    paths = _run_v4_pipeline(scripts_dir=scripts_dir, pdf_path=pdf_path, run_dir=run_dir, docver_id=docver_id, packet_role=packet_role, pages=args.pages, vision_model=args.vision_model, dictalm_model=args.dictalm_model, ollama_base_url=args.ollama_base_url, timeout_seconds=args.timeout_seconds, attachment_context_paths=attachment_context_paths if packet_role == "protocol" else [], existing_tree_json=existing_tree_json)
                    with Session(engine) as session:
                        extracted_id = _upsert_extracted_document(session=session, docver_id=docver_id, pages_json=paths["pages_json"])
                        session.commit()
                    import_result = _run_v4_import(docver_id=docver_id, source_kind=source_kind, retrieval_chunks_json=paths["retrieval_chunks_json"], model_name=str(args.dictalm_model))
                    if packet_role == "attachment":
                        attachment_context_paths.append(paths["retrieval_chunks_json"])
                    stats = _collect_v4_stats(paths=paths, import_result=import_result)
                    with Session(engine) as session:
                        report = _db_report(session=session, source_site_id=int(import_result.get("source_site_id") or 0) or None)
                    api_report = _api_report(api_base_url=args.api_base_url, question=args.ask_question) if args.api_base_url else {"api_checks": "skipped_no_api_base_url"}
                    status = {
                        "status": "completed",
                        "key": key,
                        "packet_index": packet_index,
                        "pdf_index": pdf_index,
                        "packet": str(packet.path),
                        "packet_role": packet_role,
                        "pdf": str(pdf_path),
                        "run_dir": str(run_dir),
                        "docver_id": docver_id,
                        "extracted_document_id": extracted_id,
                        "source_kind": source_kind,
                        "elapsed_seconds": round(time.perf_counter() - item_started, 3),
                        "stats": stats,
                        "db_report": report,
                        "api_report": api_report,
                    }
                    _append_jsonl(manifest_path, status)
                    print(json.dumps(_compact_status(status), ensure_ascii=False), flush=True)
                    print(json.dumps({"tree_lines": report.get("tree_lines", [])[:40], "alias_lines": report.get("alias_lines", [])[:40], "api_report": api_report}, ensure_ascii=False), flush=True)
                except Exception as exc:
                    failure = {"status": "failed", "key": key, "packet": str(packet.path), "packet_role": packet_role, "pdf": str(pdf_path), "run_dir": str(run_dir), "elapsed_seconds": round(time.perf_counter() - item_started, 3), "error": str(exc)}
                    _append_jsonl(manifest_path, failure)
                    print(json.dumps(failure, ensure_ascii=False), flush=True)
                    return 1
    finally:
        engine.dispose()
    return 0


def _run_v4_pipeline(*, scripts_dir: Path, pdf_path: Path, run_dir: Path, docver_id: int, packet_role: str, pages: str | None, vision_model: str, dictalm_model: str, ollama_base_url: str, timeout_seconds: float, attachment_context_paths: list[Path], existing_tree_json: Path | None) -> dict[str, Path]:
    outputs = run_dir / "pdf_first_pipeline" / "outputs"
    step1 = outputs / "step1_page_dissect"
    overlays = outputs / "step1_bbox_overlays"
    qa = outputs / "step1_visual_bbox_qa"
    step2 = outputs / "step2_visual_layout"
    step3 = outputs / "step3_semantic_interpretation"
    step31 = outputs / "step3_1_structure_normalization"
    step35 = outputs / "step3_5_entity_grounding"
    step4 = outputs / "step4_v4_global_topic_assignment"
    step45 = outputs / "step4_5_v4_topic_validation"
    step5 = outputs / "step5_v4_retrieval_chunks"
    for directory in (step1, overlays, qa, step2, step3, step31, step35, step4, step45, step5):
        directory.mkdir(parents=True, exist_ok=True)

    page_args = ["--pages", pages] if pages else []
    pages_json = step1 / "pages.json"
    _run_or_skip([sys.executable, str(scripts_dir / "step1_page_dissect.py"), str(pdf_path), "--output-dir", str(step1)], outputs=[pages_json])
    _run_or_skip([sys.executable, str(scripts_dir / "step1_make_bbox_overlays.py"), str(pdf_path), str(pages_json), "--output-dir", str(overlays)], outputs=[overlays / "overlay_manifest.json"])
    qa_report = qa / "bbox_quality_report.json"
    _run_or_skip([sys.executable, str(scripts_dir / "step1_visual_bbox_qa.py"), str(pages_json), str(overlays), "--output-dir", str(qa), "--model", vision_model, "--ollama-base-url", ollama_base_url, "--timeout-seconds", str(timeout_seconds), *page_args], outputs=[qa_report])
    visual_report = step2 / "visual_layout_report.json"
    _run_or_skip([sys.executable, str(scripts_dir / "step2_visual_layout_classify.py"), str(pages_json), str(overlays), str(qa_report), "--output-dir", str(step2), "--model", vision_model, "--ollama-base-url", ollama_base_url, "--timeout-seconds", str(timeout_seconds), *page_args], outputs=[visual_report])
    semantic_units = step3 / "semantic_units.json"
    _run_or_skip([sys.executable, str(scripts_dir / "step3_semantic_interpret.py"), str(visual_report), "--output-dir", str(step3), "--model", dictalm_model, "--ollama-base-url", ollama_base_url, "--timeout-seconds", str(timeout_seconds), *page_args], outputs=[semantic_units])
    structure_units = step31 / "structure_units.json"
    step31_validation = step31 / "validation_report.json"
    _run_or_skip([sys.executable, str(scripts_dir / "step3_1_structure_normalize.py"), str(semantic_units), "--output-dir", str(step31)], outputs=[structure_units, step31_validation], allowed_returncodes={0, 2})
    if not _acceptable_step31_validation(step31_validation):
        raise RuntimeError(f"step3_1_structure_normalization_blocked: {step31_validation}")
    entity_facts = step35 / "entity_facts.json"
    _run_or_skip([sys.executable, str(scripts_dir / "step3_5_ground_entities.py"), str(semantic_units), "--output-dir", str(step35)], outputs=[entity_facts])
    attachment_args = []
    for context_path in attachment_context_paths:
        attachment_args.extend(["--attachment-context-json", str(context_path)])
    existing_tree_args = ["--existing-tree-json", str(existing_tree_json)] if existing_tree_json else []
    topic_assignments = step4 / "topic_assignments.json"
    _run_or_skip([sys.executable, str(scripts_dir / "step4_v4_global_topic_assign.py"), "--structure-units-json", str(structure_units), "--entity-facts-json", str(entity_facts), "--output-dir", str(step4), "--input-pdf", str(pdf_path), "--packet-role", packet_role, "--model", dictalm_model, "--ollama-base-url", ollama_base_url, "--timeout-seconds", str(timeout_seconds), *existing_tree_args, *attachment_args], outputs=[topic_assignments])
    canonical_topics = step45 / "canonical_topics.json"
    _run_or_skip([sys.executable, str(scripts_dir / "step4_5_v4_validate_topics.py"), "--topic-assignments-json", str(topic_assignments), "--output-dir", str(step45)], outputs=[canonical_topics])
    _run_or_skip([sys.executable, str(scripts_dir / "step5_v4_build_retrieval_chunks.py"), "--structure-units-json", str(structure_units), "--canonical-topics-json", str(canonical_topics), "--entity-facts-json", str(entity_facts), "--output-dir", str(step5), "--input-pdf", str(pdf_path), "--document-version-id", str(docver_id)], outputs=[step5 / "retrieval_chunks.json", step5 / "validation_report.json"], allowed_returncodes={0, 2})
    return {"pages_json": pages_json, "qa_report": qa_report, "visual_report": visual_report, "semantic_units_json": semantic_units, "structure_units_json": structure_units, "topic_assignments_json": topic_assignments, "canonical_topics_json": canonical_topics, "entity_facts_json": entity_facts, "retrieval_chunks_json": step5 / "retrieval_chunks.json", "step5_validation_json": step5 / "validation_report.json"}


def _run_v4_import(*, docver_id: int, source_kind: str, retrieval_chunks_json: Path, model_name: str) -> dict[str, Any]:
    result = _run_cmd([sys.executable, str(PROJECT_ROOT / "scripts" / "import_pdf_first_v4_retrieval_chunks.py"), "--retrieval-chunks-json", str(retrieval_chunks_json), "--docver-id", str(docver_id), "--source-kind", source_kind, "--model-name", model_name, "--replace-existing", "--write"], capture=True)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else {}


def _source_site_id_for_docver(*, session: Session, docver_id: int) -> int:
    row = session.execute(select(Document.source_site_id).join(DocumentVersion, DocumentVersion.document_id == Document.id).where(DocumentVersion.id == docver_id)).scalar_one()
    return int(row)


def _write_existing_tree_snapshot(*, session: Session, run_dir: Path, source_site_id: int) -> Path:
    path = run_dir / "pdf_first_pipeline" / "outputs" / "step4_v4_global_topic_assignment" / "existing_tree_before_step4.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = current_tree_from_db(session, source_site_id=source_site_id, include_candidates=True, include_profiles=True)
    path.write_text(json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _retrieval_chunks_path(run_dir: Path) -> Path:
    return run_dir / "pdf_first_pipeline" / "outputs" / "step5_v4_retrieval_chunks" / "retrieval_chunks.json"


def _acceptable_step31_validation(path: Path) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("accept_for_next_step"):
        return True
    if int(payload.get("structure_unit_count") or 0) <= 0:
        return False
    blocked = payload.get("blocked_units") or []
    if not blocked:
        return False
    allowed = {"continuation_not_backward"}
    return all(set(item.get("issues") or []).issubset(allowed) for item in blocked if isinstance(item, dict))


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


def _run_or_skip(command: list[str], *, outputs: list[Path], capture: bool = False, allowed_returncodes: set[int] | None = None) -> subprocess.CompletedProcess[str] | None:
    if outputs and all(path.exists() and path.stat().st_size > 0 for path in outputs):
        print(json.dumps({"skip_existing": Path(command[1]).name if len(command) > 1 else command[0]}, ensure_ascii=False), flush=True)
        return None
    return _run_cmd(command, capture=capture, allowed_returncodes=allowed_returncodes)


def _collect_v4_stats(*, paths: dict[str, Path], import_result: dict[str, Any]) -> dict[str, Any]:
    pages = json.loads(paths["pages_json"].read_text(encoding="utf-8"))
    chunks = json.loads(paths["retrieval_chunks_json"].read_text(encoding="utf-8"))
    step5_validation = json.loads(paths["step5_validation_json"].read_text(encoding="utf-8"))
    page_count = int((pages.get("summary") or {}).get("page_count") or 0)
    pages_with_text = int((pages.get("summary") or {}).get("pages_with_text") or 0)
    chunk_rows = chunks.get("retrieval_chunks") or []
    return {"page_count": page_count, "pages_with_text": pages_with_text, "text_page_ratio": round(pages_with_text / page_count, 4) if page_count else 0.0, "chunk_count": int(chunks.get("chunk_count") or len(chunk_rows)), "active_topic_chunk_count": sum(1 for chunk in chunk_rows if chunk.get("topic_node_status") == "active"), "missing_topic_contract_count": int(step5_validation.get("missing_topic_contract_count") or 0), "missing_evidence_contract_count": int(step5_validation.get("missing_evidence_contract_count") or 0), "missing_decision_contract_count": int(step5_validation.get("missing_decision_contract_count") or 0), "blocked_issue_count": int(step5_validation.get("blocked_issue_count") or 0), "imported_artifact_count": int(import_result.get("written") or 0), "semantic_linked_count": int(import_result.get("semantic_linked") or 0)}


def _db_report(*, session: Session, source_site_id: int | None) -> dict[str, Any]:
    counts = {"semantic_node": session.execute(select(func.count()).select_from(SemanticNode)).scalar_one(), "semantic_alias": session.execute(select(func.count()).select_from(SemanticAlias)).scalar_one(), "artifact_semantic_link": session.execute(select(func.count()).select_from(ArtifactSemanticLink)).scalar_one(), "retrieval_artifact": session.execute(select(func.count()).select_from(RetrievalArtifact)).scalar_one()}
    tree = current_tree_from_db(session, source_site_id=source_site_id, include_candidates=True)
    return {"counts": {key: int(value or 0) for key, value in counts.items()}, "tree_lines": tree_lines(tree), "alias_lines": alias_lines(session, source_site_id=source_site_id)}


def _api_report(*, api_base_url: str | None, question: str) -> dict[str, Any]:
    if not api_base_url:
        return {"api_checks": "skipped_no_api_base_url"}
    base = api_base_url.rstrip("/")
    try:
        with httpx.Client(timeout=30.0) as client:
            tree_response = client.get(f"{base}/semantic/tree")
            ask_response = client.post(f"{base}/ask", json={"question": question})
        return {"semantic_tree_status": tree_response.status_code, "semantic_tree_sample": _compact_json(tree_response), "ask_status": ask_response.status_code, "ask_sample": _compact_json(ask_response)}
    except Exception as exc:  # noqa: BLE001
        return {"api_checks": "failed", "error": f"{exc.__class__.__name__}:{exc}"}


def _compact_json(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        return response.text[:500]
    if isinstance(payload, dict):
        return {key: payload.get(key) for key in list(payload)[:8]}
    return payload[:3] if isinstance(payload, list) else payload


def _select_packets(*, input_root: Path, years: list[int], packet: Path | None, limit: int | None) -> list[Packet]:
    if packet is not None:
        packet_path = packet.expanduser().resolve()
        return [_packet_from_path(packet_path)]
    packets = []
    for year in years:
        year_dir = input_root / str(year)
        if not year_dir.exists():
            continue
        for child in sorted(path for path in year_dir.iterdir() if path.is_dir()):
            packet_row = _packet_from_path(child)
            if packet_row.attachments or packet_row.protocols:
                packets.append(packet_row)
    return packets[: max(0, limit)] if limit is not None else packets


def _packet_from_path(path: Path) -> Packet:
    pdfs = sorted(item for item in path.rglob("*.pdf") if item.is_file())
    attachments = sorted([item for item in pdfs if _is_attachment(item)], key=_pdf_sort_key)
    protocols = sorted([item for item in pdfs if item not in set(attachments)], key=_pdf_sort_key)
    if not protocols:
        protocols = sorted([item for item in pdfs if any(marker.casefold() in item.name.casefold() for marker in PROTOCOL_MARKERS)], key=_pdf_sort_key)
        attachments = sorted([item for item in pdfs if item not in set(protocols)], key=_pdf_sort_key)
    return Packet(path=path, attachments=attachments, protocols=protocols)


def _is_attachment(path: Path) -> bool:
    folded = str(path).casefold()
    return any(marker.casefold() in folded for marker in ATTACHMENT_MARKERS)


def _filter_protocols(protocols: list[Path], *, mode: str) -> list[Path]:
    full = [path for path in protocols if "מלא" in path.name.casefold() or "full" in path.name.casefold()]
    short = [path for path in protocols if "מקוצר" in path.name.casefold() or "קצר" in path.name.casefold() or "short" in path.name.casefold()]
    if mode == "preferred":
        # Protocol packets can contain both transcript/full and concise versions; topic work should use the concise protocol when it exists.
        non_full = [path for path in protocols if path not in set(full)]
        return short[:1] or non_full[:1] or full[:1] or list(protocols[:1])
    if mode == "all":
        return list(protocols)
    if mode == "full-only":
        return full or [path for path in protocols if path not in set(short)] or list(protocols[:1])
    if mode == "short-only":
        return short or [path for path in protocols if path not in set(full)] or list(protocols[:1])
    return list(protocols)


def _pdf_sort_key(path: Path) -> tuple[int, str]:
    folded = path.name.casefold()
    if "מקוצר" in folded or "קצר" in folded or "short" in folded:
        return (0, folded)
    if "מלא" in folded or "full" in folded:
        return (2, folded)
    return (1, folded)


def _parse_years(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


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


def _packet_run_name(path: Path, *, input_root: Path) -> str:
    rel = _safe_rel(path, input_root=input_root).as_posix()
    slug = re.sub(r"[^0-9A-Za-z._-]+", "_", rel).strip("_")[:90]
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"{slug}_{digest}"


def _pdf_run_name(path: Path) -> str:
    slug = re.sub(r"[^0-9A-Za-z._-]+", "_", path.stem).strip("_")[:80]
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"{slug}_{digest}"


def _safe_rel(path: Path, *, input_root: Path) -> Path:
    try:
        return path.resolve().relative_to(input_root.resolve())
    except ValueError:
        return Path(path.name)


def _compact_status(status: dict[str, Any]) -> dict[str, Any]:
    stats = status["stats"]
    return {"status": "completed", "packet_index": status["packet_index"], "pdf_index": status["pdf_index"], "packet_role": status["packet_role"], "docver_id": status["docver_id"], "pages": stats["page_count"], "text_pages": stats["pages_with_text"], "chunks": stats["chunk_count"], "imported": stats["imported_artifact_count"], "semantic_linked": stats["semantic_linked_count"], "blocked": stats["blocked_issue_count"], "elapsed_seconds": status["elapsed_seconds"]}


def _require_v4_scripts(scripts_dir: Path) -> None:
    required = ["step1_page_dissect.py", "step1_make_bbox_overlays.py", "step1_visual_bbox_qa.py", "step2_visual_layout_classify.py", "step3_semantic_interpret.py", "step3_1_structure_normalize.py", "step3_5_ground_entities.py", "step4_v4_global_topic_assign.py", "step4_5_v4_validate_topics.py", "step5_v4_build_retrieval_chunks.py"]
    missing = [name for name in required if not (scripts_dir / name).exists()]
    if missing:
        raise SystemExit(f"missing v4 pipeline scripts in {scripts_dir}: {missing}")


if __name__ == "__main__":
    raise SystemExit(main())
