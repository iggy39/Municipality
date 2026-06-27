#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import process_pdf_first_v4_batch as batch  # noqa: E402


DEFAULT_SELECTION = PROJECT_ROOT / "rag_eval" / "runs" / "targeted_hard_raw_pdf_shadow_20260627_selection.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "rag_eval" / "runs" / "targeted_hard_raw_pdf_shadow_20260627"
SHADOW_SCRIPT = SCRIPTS_ROOT / "process_pdf_first_v4_shadow.py"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PDF-first v4 shadow pipeline over a selected hard-case PDF list")
    parser.add_argument("--selection-json", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--parallel-pdfs", type=int, default=2)
    parser.add_argument("--max-pdfs", type=int, default=0)
    parser.add_argument("--dicta-mode", choices=["auto", "disabled", "required"], default="auto")
    parser.add_argument("--topic-subject-v3-mode", choices=["disabled", "problem_rows", "all"], default="problem_rows")
    parser.add_argument("--topic-subject-v3-max-rows", type=int, default=24)
    parser.add_argument("--topic-subject-v3-disable-judge", action="store_true")
    parser.add_argument("--vision-model", default="mistral-small3.1:latest")
    parser.add_argument("--dictalm-model", default="dicta-il/DictaLM-3.0-24B-Thinking:bf16")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=120.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    selection_path = args.selection_json.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    entries = _load_selection(selection_path)
    if args.max_pdfs:
        entries = entries[: max(0, int(args.max_pdfs))]

    print(
        json.dumps(
            {
                "status": "start",
                "selection_json": str(selection_path),
                "output_root": str(output_root),
                "selected_pdf_count": len(entries),
                "parallel_pdfs": max(1, int(args.parallel_pdfs)),
                "dicta_mode": args.dicta_mode,
                "topic_subject_v3_mode": args.topic_subject_v3_mode,
                "topic_subject_v3_max_rows": int(args.topic_subject_v3_max_rows or 0),
                "topic_subject_v3_disable_judge": bool(args.topic_subject_v3_disable_judge),
                "dry_run": bool(args.dry_run),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    if args.dry_run:
        for index, entry in enumerate(entries, 1):
            print(json.dumps({"index": index, **_compact_entry(entry), "final_output_exists": _final_output_exists(output_root, entry)}, ensure_ascii=False), flush=True)
        return 0

    output_root.mkdir(parents=True, exist_ok=True)
    log_root = output_root / "runner_logs"
    log_root.mkdir(parents=True, exist_ok=True)
    queue = list(enumerate(entries, 1))
    running: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    parallel = max(1, int(args.parallel_pdfs))
    last_heartbeat = started

    while queue or running:
        while queue and len(running) < parallel:
            index, entry = queue.pop(0)
            if _final_output_exists(output_root, entry) and not args.force:
                row = {"status": "skipped_existing", "index": index, **_compact_entry(entry)}
                completed.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                continue
            job = _start_job(index=index, total=len(entries), entry=entry, args=args, output_root=output_root, log_root=log_root)
            running.append(job)
            print(json.dumps({"status": "protocol_started", "index": index, "of": len(entries), **_compact_entry(entry), "parallel_running": len(running)}, ensure_ascii=False), flush=True)

        now = time.perf_counter()
        if now - last_heartbeat >= max(30.0, float(args.heartbeat_seconds)):
            print(
                json.dumps(
                    {
                        "status": "heartbeat",
                        "completed": len(completed),
                        "failed": len(failures),
                        "queued": len(queue),
                        "running": [
                            {"index": job["index"], "city": job["entry"].get("city"), "elapsed_seconds": round(now - float(job["started"]), 1)}
                            for job in running
                        ],
                        "elapsed_seconds": round(now - started, 1),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            last_heartbeat = now

        for job in list(running):
            proc = job["proc"]
            if proc.poll() is None:
                continue
            running.remove(job)
            _close_job_files(job)
            elapsed = round(time.perf_counter() - float(job["started"]), 3)
            row = {"index": job["index"], **_compact_entry(job["entry"]), "elapsed_seconds": elapsed, "stdout_log": str(job["stdout_path"]), "stderr_log": str(job["stderr_path"])}
            if proc.returncode == 0:
                completed.append({"status": "completed", **row})
                print(json.dumps({"status": "protocol_done", **row}, ensure_ascii=False), flush=True)
            else:
                failure = {"status": "failed", "returncode": proc.returncode, **row, "stderr_tail": _tail_file(job["stderr_path"]), "stdout_tail": _tail_file(job["stdout_path"])}
                failures.append(failure)
                print(json.dumps(failure, ensure_ascii=False), flush=True)

        if running:
            time.sleep(2.0)

    summary = _build_summary(output_root=output_root, entries=entries, completed=completed, failures=failures, elapsed=time.perf_counter() - started)
    summary_path = output_root / "targeted_shadow_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "summary", "summary_path": str(summary_path), "completed": len(completed), "failed": len(failures), "elapsed_seconds": summary["elapsed_seconds"]}, ensure_ascii=False), flush=True)
    return 1 if failures else 0


def _load_selection(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("selected_pdfs") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError(f"selection file must contain selected_pdfs list: {path}")
    out: list[dict[str, Any]] = []
    for row in entries:
        if not isinstance(row, dict):
            continue
        pdf = Path(str(row.get("pdf") or "")).expanduser().resolve()
        if not pdf.exists():
            raise FileNotFoundError(str(pdf))
        out.append({**row, "pdf": str(pdf)})
    return out


def _start_job(*, index: int, total: int, entry: dict[str, Any], args: argparse.Namespace, output_root: Path, log_root: Path) -> dict[str, Any]:
    pdf = Path(str(entry["pdf"]))
    run_name = batch._pdf_run_name(pdf)
    log_prefix = f"{index:03d}_{_safe_name(str(entry.get('city') or 'city'))}_{run_name[:80]}"
    stdout_path = log_root / f"{log_prefix}.stdout.log"
    stderr_path = log_root / f"{log_prefix}.stderr.log"
    stdout_file = stdout_path.open("w", encoding="utf-8")
    stderr_file = stderr_path.open("w", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    cmd = [
        sys.executable,
        str(SHADOW_SCRIPT),
        "--pdf",
        str(pdf),
        "--output-root",
        str(output_root),
        "--dicta-mode",
        str(args.dicta_mode),
        "--topic-subject-v3-mode",
        str(args.topic_subject_v3_mode),
        "--topic-subject-v3-max-rows",
        str(args.topic_subject_v3_max_rows),
        "--vision-model",
        str(args.vision_model),
        "--dictalm-model",
        str(args.dictalm_model),
        "--ollama-base-url",
        str(args.ollama_base_url),
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--heartbeat-seconds",
        str(args.heartbeat_seconds),
    ]
    if args.topic_subject_v3_disable_judge:
        cmd.append("--topic-subject-v3-disable-judge")
    if args.force:
        cmd.append("--force")
    proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=env, text=True, stdout=stdout_file, stderr=stderr_file)
    return {"index": index, "total": total, "entry": entry, "proc": proc, "started": time.perf_counter(), "stdout_file": stdout_file, "stderr_file": stderr_file, "stdout_path": stdout_path, "stderr_path": stderr_path}


def _build_summary(*, output_root: Path, entries: list[dict[str, Any]], completed: list[dict[str, Any]], failures: list[dict[str, Any]], elapsed: float) -> dict[str, Any]:
    reports = []
    for index, entry in enumerate(entries, 1):
        reports.append({"index": index, **_compact_entry(entry), **_collect_pipeline_stats(output_root=output_root, entry=entry)})
    return {"summary": reports, "completed": completed, "failures": failures, "aggregate": _aggregate(reports=reports, failures=failures), "elapsed_seconds": round(elapsed, 3)}


def _collect_pipeline_stats(*, output_root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    outputs = _outputs_dir(output_root, entry)
    step1 = _load_json(outputs / "step1_page_dissect" / "pages.json")
    step4_validation = _load_json(outputs / "step4_v4_global_topic_assignment" / "validation_report.json")
    step45_validation = _load_json(outputs / "step4_5_v4_topic_validation" / "validation_report.json")
    step5_validation = _load_json(outputs / "step5_v4_retrieval_chunks" / "validation_report.json")
    return {
        "run_dir": str(outputs.parent.parent),
        "pages": len(step1.get("pages") or []),
        "step4_units": int(step4_validation.get("unit_count") or 0),
        "step4_assignments": int(step4_validation.get("assignment_count") or 0),
        "step4_candidates": int(step4_validation.get("non_blocking_review_count") or 0),
        "step4_model_errors": int(step4_validation.get("model_error_count") or 0),
        "step4_accept": bool(step4_validation.get("accept_for_next_step")),
        "step45_assignments": int(step45_validation.get("assignment_count") or 0),
        "step45_candidates": int(step45_validation.get("non_blocking_review_count") or 0),
        "step45_invalid": int(step45_validation.get("invalid_assignment_count") or 0),
        "step45_accept": bool(step45_validation.get("accept_for_next_step")),
        "step45_tree_warnings": int(step45_validation.get("tree_audit_warning_count") or 0),
        "step45_status_counts": step45_validation.get("status_counts") or {},
        "step5_accept": bool(step5_validation.get("accept_for_import")),
        "step5_blocked": int(step5_validation.get("blocked_issue_count") or 0),
    }


def _aggregate(*, reports: list[dict[str, Any]], failures: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for report in reports:
        city = str(report.get("city") or "unknown")
        row = out.setdefault(city, {"pdfs": 0, "pages": 0, "assignments": 0, "candidates": 0, "model_errors": 0, "invalid": 0, "tree_warnings": 0, "step5_blocked": 0, "failures": 0})
        row["pdfs"] += 1
        row["pages"] += int(report.get("pages") or 0)
        row["assignments"] += int(report.get("step45_assignments") or report.get("step4_assignments") or 0)
        row["candidates"] += int(report.get("step45_candidates") or report.get("step4_candidates") or 0)
        row["model_errors"] += int(report.get("step4_model_errors") or 0)
        row["invalid"] += int(report.get("step45_invalid") or 0)
        row["tree_warnings"] += int(report.get("step45_tree_warnings") or 0)
        row["step5_blocked"] += int(report.get("step5_blocked") or 0)
    for failure in failures:
        city = str(failure.get("city") or "unknown")
        row = out.setdefault(city, {"pdfs": 0, "pages": 0, "assignments": 0, "candidates": 0, "model_errors": 0, "invalid": 0, "tree_warnings": 0, "step5_blocked": 0, "failures": 0})
        row["failures"] += 1
    return out


def _outputs_dir(output_root: Path, entry: dict[str, Any]) -> Path:
    return output_root / batch._pdf_run_name(Path(str(entry["pdf"]))) / "pdf_first_pipeline" / "outputs"


def _final_output_exists(output_root: Path, entry: dict[str, Any]) -> bool:
    path = _outputs_dir(output_root, entry) / "step5_v4_retrieval_chunks" / "retrieval_chunks.json"
    return path.exists() and path.stat().st_size > 0


def _compact_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {"city": entry.get("city"), "label": entry.get("label"), "pdf": entry.get("pdf"), "difficulty_reasons": entry.get("difficulty_reasons") or []}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _close_job_files(job: dict[str, Any]) -> None:
    for key in ("stdout_file", "stderr_file"):
        handle = job.get(key)
        if handle is not None:
            handle.close()


def _tail_file(path: Path, limit: int = 1600) -> str:
    try:
        return path.read_text(encoding="utf-8")[-limit:]
    except OSError:
        return ""


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)[:40] or "item"


if __name__ == "__main__":
    raise SystemExit(main())
