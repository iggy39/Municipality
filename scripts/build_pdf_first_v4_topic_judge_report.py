#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a judge report for PDF-first v4 topic classifier shadow runs")
    parser.add_argument("--baseline-step4", required=True)
    parser.add_argument("--candidate-step4", required=True)
    parser.add_argument("--baseline-step45", required=True)
    parser.add_argument("--candidate-step45", required=True)
    parser.add_argument("--candidate-step45-validation")
    parser.add_argument("--candidate-step5-validation")
    parser.add_argument("--candidate-step4-log")
    parser.add_argument("--candidate-cache-log")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    baseline_step4 = _load_json(Path(args.baseline_step4))
    candidate_step4 = _load_json(Path(args.candidate_step4))
    baseline_step45 = _load_json(Path(args.baseline_step45))
    candidate_step45 = _load_json(Path(args.candidate_step45))
    candidate_step45_validation = _load_json(Path(args.candidate_step45_validation)) if args.candidate_step45_validation else {}
    candidate_step5_validation = _load_json(Path(args.candidate_step5_validation)) if args.candidate_step5_validation else {}

    baseline_rows = baseline_step45.get("canonical_assignments") or baseline_step4.get("topic_assignments") or []
    candidate_rows = candidate_step45.get("canonical_assignments") or candidate_step4.get("topic_assignments") or []
    candidate_items = {str(item.get("structure_unit_id") or ""): item for item in candidate_step4.get("items") or [] if isinstance(item, dict)}
    baseline_by_id = {str(row.get("structure_unit_id") or ""): row for row in baseline_rows if isinstance(row, dict)}
    candidate_by_id = {str(row.get("structure_unit_id") or ""): row for row in candidate_rows if isinstance(row, dict)}

    changes = _changed_assignments(baseline_by_id=baseline_by_id, candidate_by_id=candidate_by_id, items=candidate_items)
    root_counts = {"baseline": _distribution(baseline_rows, "root_label_he"), "candidate": _distribution(candidate_rows, "root_label_he")}
    child_counts = {"baseline": _child_distribution(baseline_rows), "candidate": _child_distribution(candidate_rows)}
    dominance_flags = _dominance_flags(root_counts["baseline"], root_counts["candidate"])
    judge_queue = _judge_queue(changes=changes, dominance_flags=dominance_flags)
    candidate_step4_log = _parse_step_log(Path(args.candidate_step4_log)) if args.candidate_step4_log else {}
    candidate_cache_log = _parse_step_log(Path(args.candidate_cache_log)) if args.candidate_cache_log else {}
    report = {
        "report_type": "pdf_first_v4_topic_judge_report",
        "inputs": {
            "baseline_step4": str(Path(args.baseline_step4).expanduser().resolve()),
            "candidate_step4": str(Path(args.candidate_step4).expanduser().resolve()),
            "baseline_step45": str(Path(args.baseline_step45).expanduser().resolve()),
            "candidate_step45": str(Path(args.candidate_step45).expanduser().resolve()),
        },
        "timing": {
            "baseline_step4_elapsed_seconds": baseline_step4.get("elapsed_seconds"),
            "candidate_step4_output_elapsed_seconds": candidate_step4.get("elapsed_seconds"),
            "candidate_step4_observed_non_cached_seconds": candidate_step4_log.get("elapsed_sum_non_cached"),
            "candidate_step4_log": candidate_step4_log,
            "candidate_cache_log": candidate_cache_log,
        },
        "validation": {
            "candidate_step4": _load_sibling_validation(Path(args.candidate_step4)),
            "candidate_step45": candidate_step45_validation,
            "candidate_step5": candidate_step5_validation,
        },
        "distributions": {
            "roots": root_counts,
            "children": child_counts,
            "routes": {
                "baseline": _route_sources(baseline_step4.get("topic_assignments") or []),
                "candidate": _route_sources(candidate_step4.get("topic_assignments") or []),
            },
            "root_only_count": {
                "baseline": sum(1 for row in baseline_rows if not row.get("child_label_he")),
                "candidate": sum(1 for row in candidate_rows if not row.get("child_label_he")),
            },
        },
        "risk_flags": {
            "root_dominance": dominance_flags,
            "changed_assignment_count": len(changes),
            "high_risk_change_count": sum(1 for row in changes if row.get("risk_score", 0) >= 3),
        },
        "changed_assignments": changes,
        "judge_queue": judge_queue,
        "judgment_schema": {
            "allowed_labels": [
                "correct",
                "wrong_root",
                "wrong_child",
                "should_be_root_only",
                "too_specific",
                "too_generic",
                "mention_not_about",
                "procedural_not_semantic",
                "needs_new_topic",
                "needs_alias",
                "should_merge",
                "should_split",
            ],
            "required_fields": ["structure_unit_id", "judgment", "evidence_quote_he", "correction"],
        },
    }
    report_json = output_dir / "topic_judge_report.json"
    report_md = output_dir / "topic_judge_report.md"
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report_md.write_text(_markdown_report(report), encoding="utf-8")
    print(json.dumps({"judge_report_json": str(report_json), "judge_report_md": str(report_md), "changed_assignment_count": len(changes), "judge_queue_count": len(judge_queue), "root_dominance_flags": len(dominance_flags)}, ensure_ascii=False))
    return 0


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))


def _load_sibling_validation(path: Path) -> dict[str, Any]:
    validation_path = path.expanduser().resolve().parent / "validation_report.json"
    return _load_json(validation_path) if validation_path.exists() else {}


def _distribution(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(Counter(str(row.get(key) or "<none>") for row in rows if isinstance(row, dict)))


def _child_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(f"{row.get('root_label_he') or '<none>'} -> {row.get('child_label_he') or '<root-only>'}" for row in rows if isinstance(row, dict)))


def _route_sources(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        route = str(row.get("topic_assignment_route") or "")
        if "existing_tree" in route:
            counts["existing_tree"] += 1
        elif "referenced_attachment" in route:
            counts["referenced_attachment"] += 1
        elif "deterministic_v4_fallback" in route:
            counts["deterministic_fallback"] += 1
        elif "child_choice" in route or "matched_child_label" in route:
            counts["other_child_choice"] += 1
        else:
            counts["root_or_other"] += 1
    return dict(counts)


def _changed_assignments(*, baseline_by_id: dict[str, dict[str, Any]], candidate_by_id: dict[str, dict[str, Any]], items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for unit_id in sorted(set(baseline_by_id) & set(candidate_by_id)):
        old = baseline_by_id[unit_id]
        new = candidate_by_id[unit_id]
        old_pair = (old.get("root_label_he"), old.get("child_label_he"))
        new_pair = (new.get("root_label_he"), new.get("child_label_he"))
        if old_pair == new_pair:
            continue
        item = items.get(unit_id, {})
        flags = _change_flags(old=old, new=new, item=item)
        changes.append(
            {
                "structure_unit_id": unit_id,
                "source_page": new.get("source_page") or item.get("source_page"),
                "old_root_label_he": old.get("root_label_he"),
                "old_child_label_he": old.get("child_label_he"),
                "new_root_label_he": new.get("root_label_he"),
                "new_child_label_he": new.get("child_label_he"),
                "new_route": new.get("topic_assignment_route"),
                "risk_flags": flags,
                "risk_score": len(flags),
                "raw_text_preview": str(item.get("raw_text") or new.get("topic_supporting_quote_he") or "")[:500],
                "candidate_children": _compact_candidates(item.get("candidate_child_topics") or []),
            }
        )
    changes.sort(key=lambda row: (int(row.get("risk_score") or 0), str(row.get("structure_unit_id") or "")), reverse=True)
    return changes


def _change_flags(*, old: dict[str, Any], new: dict[str, Any], item: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if old.get("root_label_he") != new.get("root_label_he"):
        flags.append("root_changed")
    if old.get("child_label_he") != new.get("child_label_he"):
        flags.append("child_changed")
    if not old.get("child_label_he") and new.get("child_label_he"):
        flags.append("root_only_to_child")
    if old.get("child_label_he") and not new.get("child_label_he"):
        flags.append("child_to_root_only")
    route = str(new.get("topic_assignment_route") or "")
    if "existing_tree" in route:
        flags.append("existing_tree_selected")
    if str(item.get("structural_role") or "") in {"metadata", "outline_item", "section_heading"} and new.get("child_label_he"):
        flags.append("procedural_role_with_child")
    return flags


def _compact_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for candidate in candidates[:8]:
        if not isinstance(candidate, dict):
            continue
        out.append(
            {
                "label_he": candidate.get("label_he"),
                "root_label_he": candidate.get("root_label_he"),
                "source": candidate.get("evidence_source"),
                "confidence_hint": candidate.get("confidence_hint"),
            }
        )
    return out


def _dominance_flags(baseline: dict[str, int], candidate: dict[str, int]) -> list[dict[str, Any]]:
    total = max(1, sum(candidate.values()))
    flags = []
    for root, count in candidate.items():
        baseline_count = int(baseline.get(root, 0))
        ratio = count / total
        growth = count / max(1, baseline_count)
        if ratio >= 0.45 or (count >= 20 and growth >= 2.0):
            flags.append({"root_label_he": root, "baseline_count": baseline_count, "candidate_count": count, "candidate_ratio": round(ratio, 4), "growth": round(growth, 3)})
    return flags


def _judge_queue(*, changes: list[dict[str, Any]], dominance_flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dominant_roots = {str(row.get("root_label_he")) for row in dominance_flags}
    queue = []
    for change in changes:
        flags = list(change.get("risk_flags") or [])
        if change.get("new_root_label_he") in dominant_roots:
            flags.append("dominant_root_shift")
        if len(flags) < 2:
            continue
        row = dict(change)
        row["risk_flags"] = flags
        row["judge_instruction"] = "Decide whether the new classification is the primary aboutness of the local evidence; if not, provide root/child correction or root-only."
        queue.append(row)
    queue.sort(key=lambda row: (len(row.get("risk_flags") or []), str(row.get("structure_unit_id") or "")), reverse=True)
    return queue[:80]


def _parse_step_log(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.exists():
        return {}
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "batch" in payload:
            events.append(payload)
    elapsed = [float(event.get("elapsed_seconds") or 0.0) for event in events if event.get("status") != "cached"]
    return {
        "event_count": len(events),
        "status_counts": dict(Counter(str(event.get("status") or "") for event in events)),
        "elapsed_sum_non_cached": round(sum(elapsed), 3),
        "p50_non_cached": round(statistics.median(elapsed), 3) if elapsed else 0.0,
        "p90_non_cached": round(sorted(elapsed)[max(0, int(len(elapsed) * 0.9) - 1)], 3) if elapsed else 0.0,
        "max_non_cached": round(max(elapsed), 3) if elapsed else 0.0,
    }


def _markdown_report(report: dict[str, Any]) -> str:
    lines = ["# PDF-first v4 Topic Judge Report", ""]
    lines.append("## Timing")
    timing = report.get("timing") or {}
    lines.append(f"- Baseline Step 4 elapsed: `{timing.get('baseline_step4_elapsed_seconds')}` seconds")
    lines.append(f"- Candidate Step 4 output elapsed: `{timing.get('candidate_step4_output_elapsed_seconds')}` seconds")
    lines.append(f"- Candidate Step 4 observed non-cached elapsed: `{timing.get('candidate_step4_observed_non_cached_seconds')}` seconds")
    lines.append(f"- Candidate log: `{(timing.get('candidate_step4_log') or {}).get('status_counts')}`")
    lines.append(f"- Cache log: `{(timing.get('candidate_cache_log') or {}).get('status_counts')}`")
    lines.append("")
    lines.append("## Risk Flags")
    risk = report.get("risk_flags") or {}
    lines.append(f"- Changed assignments: `{risk.get('changed_assignment_count')}`")
    lines.append(f"- High-risk changes: `{risk.get('high_risk_change_count')}`")
    for flag in risk.get("root_dominance") or []:
        lines.append(f"- Dominant root shift: `{flag.get('root_label_he')}` baseline `{flag.get('baseline_count')}`, candidate `{flag.get('candidate_count')}`")
    lines.append("")
    lines.append("## Judge Queue")
    lines.append("| Unit | Page | Old | New | Flags | Evidence Preview |")
    lines.append("|---|---:|---|---|---|---|")
    for row in (report.get("judge_queue") or [])[:30]:
        old_label = f"{row.get('old_root_label_he')} -> {row.get('old_child_label_he') or '<root-only>'}"
        new_label = f"{row.get('new_root_label_he')} -> {row.get('new_child_label_he') or '<root-only>'}"
        lines.append(f"| `{_md(row.get('structure_unit_id'))}` | {_md(row.get('source_page'))} | {_md(old_label)} | {_md(new_label)} | {_md(', '.join(row.get('risk_flags') or []))} | {_md(row.get('raw_text_preview'))[:260]} |")
    lines.append("")
    return "\n".join(lines)


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


if __name__ == "__main__":
    raise SystemExit(main())
