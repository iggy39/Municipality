#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE_SUMMARY = PROJECT_ROOT / "rag_eval/runs/cross_city_step4_after_label_fixes_20260618_summary.json"
STEP4_SCRIPT = PROJECT_ROOT / "rag_eval/runs/ashdod_2025_regular_3_short_pdf/pdf_first_pipeline/scripts/step4_v4_global_topic_assign.py"
STEP45_SCRIPT = PROJECT_ROOT / "rag_eval/runs/ashdod_2025_regular_3_short_pdf/pdf_first_pipeline/scripts/step4_5_v4_validate_topics.py"

BASE_BY_CITY = {
    "Tel Aviv": PROJECT_ROOT / "rag_eval/runs/tel_aviv_pdf_first_v4_shadow",
    "Ashdod": PROJECT_ROOT / "rag_eval/runs/ashdod_pdf_first_v4_shadow",
    "Ashdod canonical": PROJECT_ROOT / "rag_eval/runs/ashdod_2025_regular_3_short_pdf",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run resumable DB-free cross-city Step 4/4.5 regression")
    parser.add_argument("--baseline-summary", default=str(DEFAULT_BASELINE_SUMMARY))
    parser.add_argument("--output-suffix", required=True, help="Suffix used in Step 4/4.5 output directory names")
    parser.add_argument("--dicta-mode", choices=["auto", "disabled", "required", "dicta_authoritative", "dicta_contextual"], default="auto")
    parser.add_argument("--model", default="dicta-il/DictaLM-3.0-24B-Thinking:bf16")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--timeout-seconds", type=float, default=300.0, help="Per Dicta request timeout passed to Step 4")
    parser.add_argument("--step4-timeout-seconds", type=float, default=1200.0, help="Wall timeout per protocol Step 4 process")
    parser.add_argument("--max-seconds", type=float, default=0.0, help="Stop gracefully after this many seconds; 0 means no limit")
    parser.add_argument("--max-protocols", type=int, default=0, help="Run at most this many pending protocols; 0 means all")
    parser.add_argument("--start-index", type=int, default=1, help="1-based input index to start from")
    parser.add_argument("--indexes", default="", help="Optional comma-separated 1-based indexes or ranges, e.g. 11,40 or 19-25")
    parser.add_argument("--parallel-protocols", type=int, default=1, help="Run this many full protocols concurrently")
    parser.add_argument("--force", action="store_true", help="Rerun even when Step 4.5 validation exists")
    parser.add_argument("--summary-only", action="store_true", help="Only aggregate existing outputs; do not start pending protocols")
    args = parser.parse_args()

    started = time.monotonic()
    baseline_path = Path(args.baseline_summary).expanduser().resolve()
    rows = json.loads(baseline_path.read_text(encoding="utf-8")).get("summary") or []
    step4_output_name = f"step4_v4_global_topic_assignment_{args.output_suffix}"
    step45_output_name = f"step4_5_v4_topic_validation_{args.output_suffix}"
    summary_path = PROJECT_ROOT / "rag_eval/runs" / f"cross_city_{args.output_suffix}_summary.json"

    reports: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    skipped = 0
    completed_this_run = 0
    selected_indexes = _parse_indexes(str(args.indexes or ""))

    print(
        json.dumps(
            {
                "status": "start",
                "input_count": len(rows),
                "dicta_mode": args.dicta_mode,
                "output_suffix": args.output_suffix,
                "resume": not args.force,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    pending_entries: list[dict[str, Any]] = []
    selected_pending = 0
    for index, row in enumerate(rows, 1):
        if index < max(1, int(args.start_index)):
            continue
        if selected_indexes and index not in selected_indexes:
            continue
        if args.max_protocols and selected_pending >= args.max_protocols:
            break

        city = str(row.get("city") or "")
        label = str(row.get("label") or "")
        base = BASE_BY_CITY.get(city)
        if base is None:
            failures.append({"city": city, "label": label, "error": "unknown_city"})
            continue
        structure_path = base / label
        if not structure_path.exists():
            failures.append({"city": city, "label": label, "error": f"missing_structure:{structure_path}"})
            continue

        output_base = _output_base_for_structure(structure_path)
        step4_dir = output_base / step4_output_name
        step45_dir = output_base / step45_output_name
        validation_path = step45_dir / "validation_report.json"
        if validation_path.exists() and not args.force:
            skipped += 1
            report = _report_from_existing(city=city, label=label, baseline=row, step4_dir=step4_dir, step45_dir=step45_dir)
            reports.append(report)
            print(json.dumps({"status": "skipped_existing", "index": index, "of": len(rows), "city": city, "candidates": report.get("candidates"), "invalid": report.get("invalid")}, ensure_ascii=False), flush=True)
            continue
        if args.summary_only:
            continue

        entity_path = _resolve_entity_facts(structure_path)
        pending_entries.append(
            {
                "index": index,
                "of": len(rows),
                "city": city,
                "label": label,
                "baseline": row,
                "structure_path": structure_path,
                "entity_path": entity_path,
                "step4_dir": step4_dir,
                "step45_dir": step45_dir,
            }
        )
        selected_pending += 1

    run_result = _run_pending_entries(entries=pending_entries, args=args, started=started)
    reports.extend(run_result["reports"])
    failures.extend(run_result["failures"])
    completed_this_run += int(run_result["completed_this_run"])

    payload = {
        "summary": reports,
        "aggregate": _aggregate(reports, failures),
        "failures": failures,
        "skipped_existing": skipped,
        "completed_this_run": completed_this_run,
        "step4_output_name": step4_output_name,
        "step45_output_name": step45_output_name,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "summary",
                "summary_path": str(summary_path),
                "run_count": len(reports),
                "failure_count": len(failures),
                "skipped_existing": skipped,
                "completed_this_run": completed_this_run,
                "aggregate": payload["aggregate"],
                "elapsed_seconds": payload["elapsed_seconds"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 1 if failures else 0


def _output_base_for_structure(structure_path: Path) -> Path:
    parts = structure_path.parts
    if "outputs" in parts:
        return Path(*parts[: parts.index("outputs") + 1])
    if structure_path.parent.name.startswith("step3_1_structure_normalization"):
        return structure_path.parent.parent
    return structure_path.parent


def _resolve_entity_facts(structure_path: Path) -> Path | None:
    parts = structure_path.parts
    candidates: list[Path] = []
    if "outputs" in parts:
        candidates.append(Path(*parts[: parts.index("outputs") + 1]) / "step3_5_entity_grounding/entity_facts.json")
    if structure_path.parent.name.startswith("step3_1_structure_normalization"):
        candidates.append(structure_path.parent.parent / "step3_5_entity_grounding/entity_facts.json")
    candidates.append(structure_path.parent / "entity_facts.json")
    return next((path for path in candidates if path.exists()), None)


def _run_pending_entries(*, entries: list[dict[str, Any]], args: argparse.Namespace, started: float) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    completed_this_run = 0
    queue = list(entries)
    running: list[dict[str, Any]] = []
    parallel = max(1, int(args.parallel_protocols))
    timeout_seconds = max(1.0, float(args.step4_timeout_seconds))
    last_heartbeat = started

    while queue or running:
        now = time.monotonic()
        max_seconds_reached = bool(args.max_seconds and now - started >= float(args.max_seconds))
        if max_seconds_reached and queue:
            print(json.dumps({"status": "max_seconds_reached", "queued_remaining": len(queue), "running": len(running), "completed_this_run": completed_this_run}, ensure_ascii=False), flush=True)
            queue.clear()

        while queue and len(running) < parallel:
            entry = queue.pop(0)
            cmd4 = _step4_command(entry=entry, args=args)
            entry["step4_dir"].mkdir(parents=True, exist_ok=True)
            stdout_path = entry["step4_dir"] / "step4_stdout.log"
            stderr_path = entry["step4_dir"] / "step4_stderr.log"
            stdout_file = stdout_path.open("w", encoding="utf-8")
            stderr_file = stderr_path.open("w", encoding="utf-8")
            proc = subprocess.Popen(cmd4, cwd=str(PROJECT_ROOT), text=True, stdout=stdout_file, stderr=stderr_file)
            running.append({"entry": entry, "proc": proc, "started": time.monotonic(), "cmd": cmd4, "stdout_file": stdout_file, "stderr_file": stderr_file, "stdout_path": stdout_path, "stderr_path": stderr_path})
            print(json.dumps({"status": "protocol_started", "index": entry["index"], "of": entry["of"], "city": entry["city"], "label": entry["label"], "parallel_running": len(running), "timeout_seconds": timeout_seconds}, ensure_ascii=False), flush=True)

        now = time.monotonic()
        if now - last_heartbeat >= 120:
            print(
                json.dumps(
                    {
                        "status": "heartbeat",
                        "completed_this_run": completed_this_run,
                        "queued": len(queue),
                        "running": [
                            {
                                "index": job["entry"]["index"],
                                "city": job["entry"]["city"],
                                "elapsed_seconds": round(now - float(job["started"]), 1),
                            }
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
            entry = job["entry"]
            proc = job["proc"]
            elapsed = time.monotonic() - float(job["started"])
            if proc.poll() is None and elapsed < timeout_seconds:
                continue
            running.remove(job)
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)
                _close_job_files(job)
                failure = _failure_for_entry(entry, f"step4_timeout after {timeout_seconds}s stdout={_tail_file(job['stdout_path'])} stderr={_tail_file(job['stderr_path'])}")
                failures.append(failure)
                print(json.dumps({"status": "protocol_failed", "index": entry["index"], "of": entry["of"], **failure}, ensure_ascii=False), flush=True)
                continue

            proc.wait()
            _close_job_files(job)
            if proc.returncode != 0:
                failure = _failure_for_entry(entry, f"step4_failed rc={proc.returncode} stdout={_tail_file(job['stdout_path'])} stderr={_tail_file(job['stderr_path'])}")
                failures.append(failure)
                print(json.dumps({"status": "protocol_failed", "index": entry["index"], "of": entry["of"], **failure}, ensure_ascii=False), flush=True)
                continue

            try:
                _run_step45(entry=entry)
                report = _report_from_existing(city=entry["city"], label=entry["label"], baseline=entry["baseline"], step4_dir=entry["step4_dir"], step45_dir=entry["step45_dir"])
                reports.append(report)
                completed_this_run += 1
                print(
                    json.dumps(
                        {
                            "status": "protocol_done",
                            "index": entry["index"],
                            "of": entry["of"],
                            "city": entry["city"],
                            "label": entry["label"],
                            "assignments": report.get("assignments"),
                            "candidates": report.get("candidates"),
                            "invalid": report.get("invalid"),
                            "model_errors": report.get("model_errors"),
                            "accept": report.get("accept"),
                            "elapsed_seconds": round(elapsed, 1),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                failure = _failure_for_entry(entry, str(exc))
                failures.append(failure)
                print(json.dumps({"status": "protocol_failed", "index": entry["index"], "of": entry["of"], **failure}, ensure_ascii=False), flush=True)

        if running:
            time.sleep(2.0)

    return {"reports": reports, "failures": failures, "completed_this_run": completed_this_run}


def _step4_command(*, entry: dict[str, Any], args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        str(STEP4_SCRIPT),
        "--structure-units-json",
        str(entry["structure_path"]),
        "--output-dir",
        str(entry["step4_dir"]),
        "--packet-role",
        "protocol",
        "--dicta-mode",
        str(args.dicta_mode),
        "--model",
        str(args.model),
        "--ollama-base-url",
        str(args.ollama_base_url),
        "--timeout-seconds",
        str(args.timeout_seconds),
    ]
    if entry.get("entity_path") is not None:
        cmd.extend(["--entity-facts-json", str(entry["entity_path"])])
    return cmd


def _run_step45(*, entry: dict[str, Any]) -> None:
    topic_assignments = Path(entry["step4_dir"]) / "topic_assignments.json"
    result45 = subprocess.run(
        [sys.executable, str(STEP45_SCRIPT), "--topic-assignments-json", str(topic_assignments), "--output-dir", str(entry["step45_dir"])],
        cwd=str(PROJECT_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
    )
    if result45.returncode != 0:
        raise RuntimeError(f"step45_failed rc={result45.returncode} stdout={_tail(result45.stdout)} stderr={_tail(result45.stderr)}")


def _failure_for_entry(entry: dict[str, Any], error: str) -> dict[str, Any]:
    return {"city": entry["city"], "label": entry["label"], "structure": str(entry["structure_path"]), "error": error}


def _tail(value: str, limit: int = 1600) -> str:
    return str(value or "")[-limit:]


def _tail_file(path: Path, limit: int = 1600) -> str:
    try:
        return path.read_text(encoding="utf-8")[-limit:]
    except OSError:
        return ""


def _close_job_files(job: dict[str, Any]) -> None:
    for key in ("stdout_file", "stderr_file"):
        handle = job.get(key)
        if handle is not None:
            handle.close()


def _parse_indexes(value: str) -> set[int]:
    indexes: set[int] = set()
    for part in str(value or "").split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            try:
                start = int(left.strip())
                end = int(right.strip())
            except ValueError:
                continue
            indexes.update(range(min(start, end), max(start, end) + 1))
            continue
        try:
            indexes.add(int(token))
        except ValueError:
            continue
    return {index for index in indexes if index > 0}


def _report_from_existing(*, city: str, label: str, baseline: dict[str, Any], step4_dir: Path, step45_dir: Path) -> dict[str, Any]:
    step4_validation = _load_json(step4_dir / "validation_report.json")
    validation = _load_json(step45_dir / "validation_report.json")
    canonical = _load_json(step45_dir / "canonical_topics.json")
    assignments = canonical.get("canonical_assignments") or []
    active_topics = [row for row in assignments if row.get("is_topic_bearing") and str(row.get("topic_node_status") or "") == "active"]
    children = [row for row in assignments if row.get("child_label_he")]
    return {
        "city": city,
        "label": label,
        "units": baseline.get("units"),
        "assignments": validation.get("assignment_count", 0),
        "topics": len(active_topics),
        "candidates": int(validation.get("non_blocking_review_count") or 0),
        "children": len(children),
        "model_errors": int(step4_validation.get("model_error_count") or 0),
        "invalid": int(validation.get("invalid_assignment_count") or 0),
        "accept": bool(validation.get("accept_for_next_step")),
        "status_counts": validation.get("status_counts") or {},
        "step4_output": str(step4_dir),
        "step45_output": str(step45_dir),
    }


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _aggregate(reports: list[dict[str, Any]], failures: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    aggregate: dict[str, dict[str, int]] = {}
    for report in reports:
        city = str(report.get("city") or "unknown")
        row = aggregate.setdefault(city, {"outputs": 0, "units": 0, "assignments": 0, "topics": 0, "candidates": 0, "children": 0, "model_errors": 0, "invalid": 0, "failures": 0})
        row["outputs"] += 1
        for key in ["units", "assignments", "topics", "candidates", "children", "model_errors", "invalid"]:
            row[key] += int(report.get(key) or 0)
    for failure in failures:
        city = str(failure.get("city") or "unknown")
        row = aggregate.setdefault(city, {"outputs": 0, "units": 0, "assignments": 0, "topics": 0, "candidates": 0, "children": 0, "model_errors": 0, "invalid": 0, "failures": 0})
        row["failures"] += 1
    return aggregate


if __name__ == "__main__":
    raise SystemExit(main())
