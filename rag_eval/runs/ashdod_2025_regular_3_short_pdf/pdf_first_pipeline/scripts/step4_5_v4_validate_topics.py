#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from municipality.pdf_first_v4_topic_tree import (  # noqa: E402
    PROCEDURAL_ROOT_ONLY_IDS,
    ROOT_BY_ID,
    TOPIC_ASSIGNMENT_BACKEND,
    TOPIC_TREE_VERSION,
    child_topic_id,
    missing_contract_counts,
    noisy_label_reason,
    procedural_child_label_reason,
    resolve_child_topic_assignment,
    root_label_for_id,
    semantic_root_for_child_label,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 4.5 v4: deterministic validation of v4 topic assignments")
    parser.add_argument("--topic-assignments-json", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    input_path = Path(args.topic_assignments_json).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    rows = [dict(row) for row in payload.get("topic_assignments") or [] if isinstance(row, dict)]
    invalid = []
    demoted_children = []
    non_blocking_reviews = []
    for row in rows:
        row["topic_tree_version"] = TOPIC_TREE_VERSION
        row["topic_assignment_backend"] = TOPIC_ASSIGNMENT_BACKEND
        if str(row.get("root_topic_id") or "") not in ROOT_BY_ID:
            invalid.append({"structure_unit_id": row.get("structure_unit_id"), "reason": "invalid_root_topic_id"})
            continue
        if str(row.get("topic_node_status") or "") == "needs_review":
            row["topic_node_status"] = "candidate"
            row["topic_review_status"] = "needs_review"
            row["topic_reject_reason"] = row.get("topic_reject_reason") or "non_blocking_topic_review"
            non_blocking_reviews.append({"structure_unit_id": row.get("structure_unit_id"), "reason": row.get("topic_reject_reason")})
        root_label = str(row.get("root_label_he") or root_label_for_id(str(row.get("root_topic_id") or "")) or "")
        child_label = str(row.get("child_label_he") or "").strip()
        if child_label:
            evidence_text = _topic_validation_text(row)
            resolved = resolve_child_topic_assignment(root_topic_id=str(row.get("root_topic_id") or ""), root_label_he=root_label, child_label_he=child_label, evidence_text=evidence_text, structural_role=str(row.get("structural_role") or ""))
            old_root = str(row.get("root_topic_id") or "")
            if resolved.get("child_label_he"):
                row["root_topic_id"] = resolved["root_topic_id"]
                row["root_label_he"] = resolved["root_label_he"]
                row["child_label_he"] = resolved["child_label_he"]
                row["child_topic_id"] = child_topic_id(str(resolved["root_topic_id"]), str(resolved["child_label_he"]))
                row["topic_aliases_he"] = _dedupe_strings([*(row.get("topic_aliases_he") or []), *(resolved.get("aliases_he") or [])])
                if resolved.get("root_topic_id") != old_root:
                    row["topic_assignment_route"] = f"{row.get('topic_assignment_route') or 'unknown'}:semantic_reparent"
            else:
                demoted_children.append({"structure_unit_id": row.get("structure_unit_id"), "raw_child_label_he": child_label, "reason": resolved.get("reason") or "invalid_child_label"})
                row["raw_child_label_he"] = row.get("raw_child_label_he") or child_label
                row["rejected_child_label_he"] = child_label
                row["child_label_he"] = None
                row["child_topic_id"] = None
                row["topic_node_status"] = "active"
                row["topic_reject_reason"] = f"child_demoted:{resolved.get('reason') or 'invalid_child_label'}"
                row["topic_assignment_route"] = f"{row.get('topic_assignment_route') or 'unknown'}:child_demoted"
        if row.get("child_label_he") and not row.get("child_topic_id"):
            invalid.append({"structure_unit_id": row.get("structure_unit_id"), "reason": "child_label_without_child_topic_id"})

    audit_warnings = _audit_tree_assignments(rows)
    critical_audit_warnings = [row for row in audit_warnings if row.get("severity") == "critical"]
    status_counts = Counter(str(row.get("topic_node_status") or "") for row in rows)
    non_blocking_reviews.extend(
        {"structure_unit_id": row.get("structure_unit_id"), "reason": row.get("topic_reject_reason") or "candidate_topic"}
        for row in rows
        if str(row.get("topic_node_status") or "") == "candidate" and row.get("structure_unit_id") not in {item.get("structure_unit_id") for item in non_blocking_reviews}
    )
    output = {
        "step": "step4_5_v4_validate_topics",
        "topic_tree_version": TOPIC_TREE_VERSION,
        "topic_assignment_backend": TOPIC_ASSIGNMENT_BACKEND,
        "input_topic_assignments_json": str(input_path),
        "canonical_assignments": rows,
    }
    validation = {
        "assignment_count": len(rows),
        "status_counts": dict(status_counts),
        "non_blocking_review_count": len(non_blocking_reviews),
        "non_blocking_reviews": non_blocking_reviews[:50],
        "demoted_child_topic_count": len(demoted_children),
        "demoted_child_topics": demoted_children[:50],
        "tree_audit_warning_count": len(audit_warnings),
        "tree_audit_warnings": audit_warnings[:100],
        "critical_tree_audit_warning_count": len(critical_audit_warnings),
        "invalid_assignment_count": len(invalid),
        "invalid_assignments": invalid,
        "contract_counts_before_step5": missing_contract_counts(rows),
        "accept_for_next_step": bool(rows) and not invalid and not critical_audit_warnings,
    }
    output_path = output_dir / "canonical_topics.json"
    validation_path = output_dir / "validation_report.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"canonical_topics": str(output_path), "validation_report": str(validation_path), **validation}, ensure_ascii=False))
    return 0 if validation["accept_for_next_step"] else 2


def _audit_tree_assignments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    active_children: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        child_label = str(row.get("child_label_he") or "").strip()
        if not child_label or str(row.get("topic_node_status") or "") == "rejected":
            continue
        root_id = str(row.get("root_topic_id") or "")
        evidence_text = _topic_validation_text(row)
        if root_id in PROCEDURAL_ROOT_ONLY_IDS:
            warnings.append(_warning(row, severity="critical", reason="procedural_root_has_child"))
        procedural_reason = procedural_child_label_reason(child_label, evidence_text=evidence_text)
        if procedural_reason:
            warnings.append(_warning(row, severity="critical", reason=procedural_reason))
        noisy_reason = noisy_label_reason(child_label)
        if noisy_reason:
            warnings.append(_warning(row, severity="critical", reason=noisy_reason))
        semantic_root = semantic_root_for_child_label(child_label, evidence_text=evidence_text, fallback=root_id)
        if semantic_root != root_id and semantic_root not in PROCEDURAL_ROOT_ONLY_IDS:
            warnings.append(_warning(row, severity="warning", reason="semantic_root_mismatch", expected_root_topic_id=semantic_root, expected_root_label_he=root_label_for_id(semantic_root)))
        key = (child_label.casefold(), root_id)
        active_children.setdefault(key, []).append(row)
    labels_to_roots: dict[str, set[str]] = {}
    for label_norm, root_id in active_children:
        labels_to_roots.setdefault(label_norm, set()).add(root_id)
    for label_norm, root_ids in labels_to_roots.items():
        if len(root_ids) <= 1:
            continue
        sample = next(rows_for_key[0] for key, rows_for_key in active_children.items() if key[0] == label_norm)
        warnings.append(_warning(sample, severity="warning", reason="duplicate_child_across_roots", duplicate_root_topic_ids=sorted(root_ids)))
    return warnings


def _warning(row: dict[str, Any], *, severity: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "severity": severity,
        "reason": reason,
        "structure_unit_id": row.get("structure_unit_id"),
        "source_page": row.get("source_page"),
        "root_topic_id": row.get("root_topic_id"),
        "root_label_he": row.get("root_label_he"),
        "child_label_he": row.get("child_label_he"),
        "route": row.get("topic_assignment_route"),
        **extra,
    }


def _topic_validation_text(row: dict[str, Any]) -> str:
    if str(row.get("packet_role") or "") == "protocol" and str(row.get("topic_identification_context") or "").strip():
        # Protocol topic semantics are validated from the bounded agenda/headline context.
        # Body text remains evidence but must not drive root reparenting or audit warnings.
        return "\n".join(str(value or "") for value in [row.get("topic_identification_context"), row.get("raw_child_label_he"), row.get("child_label_he")])
    return "\n".join(str(value or "") for value in [row.get("topic_supporting_quote_he"), row.get("rationale_he"), row.get("raw_child_label_he")])


def _dedupe_strings(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
