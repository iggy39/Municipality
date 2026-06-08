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
    ROOT_BY_ID,
    TOPIC_ASSIGNMENT_BACKEND,
    TOPIC_TREE_VERSION,
    child_topic_id,
    missing_contract_counts,
    root_label_for_id,
    validate_child_label,
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
    for row in rows:
        row["topic_tree_version"] = TOPIC_TREE_VERSION
        row["topic_assignment_backend"] = TOPIC_ASSIGNMENT_BACKEND
        if str(row.get("root_topic_id") or "") not in ROOT_BY_ID:
            invalid.append({"structure_unit_id": row.get("structure_unit_id"), "reason": "invalid_root_topic_id"})
            continue
        root_label = str(row.get("root_label_he") or root_label_for_id(str(row.get("root_topic_id") or "")) or "")
        child_label = str(row.get("child_label_he") or "").strip()
        if child_label:
            evidence_text = "\n".join(str(value or "") for value in [row.get("topic_supporting_quote_he"), row.get("rationale_he"), row.get("raw_child_label_he")])
            validation = validate_child_label(raw_label=child_label, root_label_he=root_label, evidence_text=evidence_text, structural_role=str(row.get("structural_role") or ""))
            if validation.status == "active" and validation.cleaned_label:
                row["child_label_he"] = validation.cleaned_label
                row["child_topic_id"] = child_topic_id(str(row.get("root_topic_id") or ""), validation.cleaned_label)
            else:
                demoted_children.append({"structure_unit_id": row.get("structure_unit_id"), "raw_child_label_he": child_label, "reason": validation.reason if validation else "invalid_child_label"})
                row["raw_child_label_he"] = row.get("raw_child_label_he") or child_label
                row["rejected_child_label_he"] = child_label
                row["child_label_he"] = None
                row["child_topic_id"] = None
                row["topic_node_status"] = "active"
                row["topic_reject_reason"] = f"child_demoted:{validation.reason if validation else 'invalid_child_label'}"
                row["topic_assignment_route"] = f"{row.get('topic_assignment_route') or 'unknown'}:child_demoted"
        if row.get("child_label_he") and not row.get("child_topic_id"):
            invalid.append({"structure_unit_id": row.get("structure_unit_id"), "reason": "child_label_without_child_topic_id"})

    status_counts = Counter(str(row.get("topic_node_status") or "") for row in rows)
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
        "demoted_child_topic_count": len(demoted_children),
        "demoted_child_topics": demoted_children[:50],
        "invalid_assignment_count": len(invalid),
        "invalid_assignments": invalid,
        "contract_counts_before_step5": missing_contract_counts(rows),
        "accept_for_next_step": bool(rows) and not invalid,
    }
    output_path = output_dir / "canonical_topics.json"
    validation_path = output_dir / "validation_report.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"canonical_topics": str(output_path), "validation_report": str(validation_path), **validation}, ensure_ascii=False))
    return 0 if validation["accept_for_next_step"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
