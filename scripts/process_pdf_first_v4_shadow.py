#!/usr/bin/env python3
"""Run the PDF-first v4 pipeline without registering or importing documents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import process_pdf_first_v4_batch as batch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_ROOT = PROJECT_ROOT / "rag_eval" / "data" / "raw_docs" / "jerusalem_council_sample"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "rag_eval" / "runs" / "jerusalem_pdf_first_v4_shadow"
DEFAULT_PIPELINE_SCRIPTS = PROJECT_ROOT / "rag_eval" / "runs" / "ashdod_2025_regular_3_short_pdf" / "pdf_first_pipeline" / "scripts"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PDF-first v4 shadow pipeline without DB writes")
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--pdf", type=Path, help="Specific PDF to process instead of scanning input-root")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--pipeline-scripts", type=Path, default=DEFAULT_PIPELINE_SCRIPTS)
    parser.add_argument("--limit-pdfs", type=int)
    parser.add_argument("--pages", help="Optional page list/ranges passed to model-heavy steps")
    parser.add_argument("--vision-model", default="mistral-small3.1:latest")
    parser.add_argument("--dictalm-model", default="dicta-il/DictaLM-3.0-24B-Thinking:bf16")
    parser.add_argument("--dicta-mode", choices=["auto", "disabled", "required"], default="auto")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=120.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    input_root = args.input_root.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    scripts_dir = args.pipeline_scripts.expanduser().resolve()
    batch._require_v4_scripts(scripts_dir)

    if args.pdf:
        pdfs = [args.pdf.expanduser().resolve()]
    else:
        pdfs = sorted(path for path in input_root.rglob("*.pdf") if path.is_file())
    if args.limit_pdfs is not None:
        pdfs = pdfs[: max(0, int(args.limit_pdfs))]
    if not pdfs:
        raise SystemExit(f"no PDFs found under {input_root}")

    print(json.dumps({"selected_pdf_count": len(pdfs), "dry_run": bool(args.dry_run), "db_writes": False}, ensure_ascii=False), flush=True)
    if args.dry_run:
        for index, pdf in enumerate(pdfs, start=1):
            print(json.dumps({"pdf_index": index, "pdf": str(pdf)}, ensure_ascii=False), flush=True)
        return 0

    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "v4_shadow_status.jsonl"
    completed = set() if args.force else batch._completed_keys(manifest_path)
    batch._run_cmd = _heartbeat_run_cmd(args.heartbeat_seconds)  # type: ignore[method-assign]
    batch._run_or_skip = _shadow_run_or_skip  # type: ignore[method-assign]

    for pdf_index, pdf_path in enumerate(pdfs, start=1):
        key = batch._item_key(pdf_path)
        run_dir = output_root / batch._pdf_run_name(pdf_path)
        if key in completed:
            print(json.dumps({"pdf_index": pdf_index, "pdf": str(pdf_path), "status": "skipped_completed"}, ensure_ascii=False), flush=True)
            continue

        started = time.perf_counter()
        print(json.dumps({"pdf_index": pdf_index, "pdf": str(pdf_path), "status": "started"}, ensure_ascii=False), flush=True)
        try:
            paths = batch._run_v4_pipeline(
                scripts_dir=scripts_dir,
                pdf_path=pdf_path,
                run_dir=run_dir,
                docver_id=0,
                packet_role="protocol",
                pages=args.pages,
                vision_model=str(args.vision_model),
                dictalm_model=str(args.dictalm_model),
                dicta_mode=str(args.dicta_mode),
                ollama_base_url=str(args.ollama_base_url),
                timeout_seconds=float(args.timeout_seconds),
                attachment_context_paths=[],
                existing_tree_json=None,
            )
            stats = batch._collect_v4_stats(paths=paths, import_result={})
            status = {
                "status": "completed",
                "key": key,
                "pdf_index": pdf_index,
                "packet_role": "protocol",
                "pdf": str(pdf_path),
                "run_dir": str(run_dir),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "stats": stats,
                "outputs": {name: str(path) for name, path in paths.items()},
                "db_writes": False,
            }
            batch._append_jsonl(manifest_path, status)
            print(json.dumps(_compact_status(status), ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001
            failure = {
                "status": "failed",
                "key": key,
                "pdf_index": pdf_index,
                "pdf": str(pdf_path),
                "run_dir": str(run_dir),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "error": f"{exc.__class__.__name__}: {exc}",
                "db_writes": False,
            }
            batch._append_jsonl(manifest_path, failure)
            print(json.dumps(failure, ensure_ascii=False), flush=True)
            return 1
    return 0


def _heartbeat_run_cmd(heartbeat_seconds: float):
    def run_cmd(command: list[str], *, capture: bool = False, allowed_returncodes: set[int] | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(batch.SRC_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        name = Path(command[1]).name if len(command) > 1 else command[0]
        print(json.dumps({"run": name}, ensure_ascii=False), flush=True)
        if capture:
            result = subprocess.run(command, cwd=batch.PROJECT_ROOT, env=env, text=True, capture_output=True)
        else:
            process = subprocess.Popen(command, cwd=batch.PROJECT_ROOT, env=env, text=True)
            started = time.perf_counter()
            next_heartbeat = started + max(1.0, float(heartbeat_seconds))
            while process.poll() is None:
                now = time.perf_counter()
                if now >= next_heartbeat:
                    print(json.dumps({"heartbeat": name, "elapsed_seconds": round(now - started, 1)}, ensure_ascii=False), flush=True)
                    next_heartbeat = now + max(1.0, float(heartbeat_seconds))
                time.sleep(1.0)
            result = subprocess.CompletedProcess(command, process.returncode or 0)
        accepted = allowed_returncodes or {0}
        if result.returncode not in accepted:
            if capture and result.stdout:
                print(result.stdout.strip(), flush=True)
            if capture and result.stderr:
                print(result.stderr.strip(), file=sys.stderr, flush=True)
            raise RuntimeError(f"command failed rc={result.returncode}: {' '.join(command)}")
        return result

    return run_cmd


def _shadow_run_or_skip(command: list[str], *, outputs: list[Path], capture: bool = False, allowed_returncodes: set[int] | None = None) -> subprocess.CompletedProcess[str] | None:
    if outputs and all(path.exists() and path.stat().st_size > 0 for path in outputs):
        name = Path(command[1]).name if len(command) > 1 else command[0]
        print(json.dumps({"skip_existing": name}, ensure_ascii=False), flush=True)
        return None
    name = Path(command[1]).name if len(command) > 1 else command[0]
    if allowed_returncodes is None and name in {"step4_v4_global_topic_assign.py", "step4_5_v4_validate_topics.py"}:
        allowed_returncodes = {0, 2}
    return batch._run_cmd(command, capture=capture, allowed_returncodes=allowed_returncodes)


def _compact_status(status: dict) -> dict:
    stats = status["stats"]
    return {
        "status": "completed",
        "pdf_index": status["pdf_index"],
        "pages": stats["page_count"],
        "text_pages": stats["pages_with_text"],
        "chunks": stats["chunk_count"],
        "blocked": stats["blocked_issue_count"],
        "active_topic_chunks": stats["active_topic_chunk_count"],
        "elapsed_seconds": status["elapsed_seconds"],
        "db_writes": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
