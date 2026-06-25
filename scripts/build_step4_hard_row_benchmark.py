#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from municipality.pdf_first_v4_topic_benchmark import (  # noqa: E402
    build_hard_row_benchmark,
    load_topic_assignment_snapshot,
    score_hard_row_benchmark,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or score a DB-free Step 4 hard-row benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--topic-assignments-json", action="append", required=True, help="Step 4 topic_assignments.json; repeatable")
    build_parser.add_argument("--existing-fixture-json", help="Optional existing benchmark fixture to preserve manual expected labels")
    build_parser.add_argument("--output-json", required=True)
    build_parser.add_argument("--limit", type=int)

    score_parser = subparsers.add_parser("score")
    score_parser.add_argument("--topic-assignments-json", action="append", required=True, help="Step 4 topic_assignments.json; repeatable")
    score_parser.add_argument("--fixture-json", required=True)
    score_parser.add_argument("--output-json", required=True)

    args = parser.parse_args()
    snapshots = [load_topic_assignment_snapshot(path) for path in args.topic_assignments_json]
    output_path = Path(args.output_json).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.command == "build":
        existing_cases = []
        if args.existing_fixture_json:
            existing_payload = json.loads(Path(args.existing_fixture_json).expanduser().resolve().read_text(encoding="utf-8"))
            existing_cases = [row for row in existing_payload.get("cases") or [] if isinstance(row, dict)]
        payload = build_hard_row_benchmark(snapshots=snapshots, existing_cases=existing_cases, limit=args.limit)
    else:
        benchmark = json.loads(Path(args.fixture_json).expanduser().resolve().read_text(encoding="utf-8"))
        payload = score_hard_row_benchmark(benchmark=benchmark, snapshots=snapshots)

    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output_json": str(output_path), "case_count": payload.get("case_count"), "judged_count": payload.get("judged_count")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
