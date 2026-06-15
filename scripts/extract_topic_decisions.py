#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from municipality.db import build_engine  # noqa: E402
from municipality.migrations import apply_all  # noqa: E402
from municipality.topic_decisions import (  # noqa: E402
    DEFAULT_DICTA_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    MockTopicDecisionClient,
    OllamaTopicDecisionClient,
    TopicDecisionResearchConfig,
    artifacts_markdown,
    quality_report_markdown,
    run_topic_decision_research,
    topic_tree_markdown,
)


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "rag_eval" / "runs" / "topic_decision_research"


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract topic-level municipal decisions from accepted topic artifacts")
    parser.add_argument("--muni", default="ashdod", help="Municipality slug")
    parser.add_argument("--limit", type=int, help="Optional artifact limit for development runs")
    parser.add_argument("--write", action="store_true", help="Persist run, decisions, and quality rows; default is dry-run")
    parser.add_argument("--mock-dicta", action="store_true", help="Use a fast local mock client for verification instead of calling Ollama")
    parser.add_argument("--model", default=DEFAULT_DICTA_MODEL)
    parser.add_argument("--ollama-base-url", default=DEFAULT_OLLAMA_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--max-text-chars", type=int, default=3500)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    output_dir = args.output_root.expanduser().resolve() / _run_dir_name(municipality_slug=str(args.muni), mock=bool(args.mock_dicta), write=bool(args.write))
    config = TopicDecisionResearchConfig(
        municipality_slug=str(args.muni),
        model_name=str(args.model),
        ollama_base_url=str(args.ollama_base_url),
        timeout_seconds=float(args.timeout_seconds),
        max_text_chars=max(500, int(args.max_text_chars)),
        limit=args.limit,
        write=bool(args.write),
        output_dir=output_dir,
    )
    client = MockTopicDecisionClient() if args.mock_dicta else OllamaTopicDecisionClient()

    print(
        json.dumps(
            {
                "mode": "write" if args.write else "dry_run",
                "municipality": config.municipality_slug,
                "model": "mock" if args.mock_dicta else config.model_name,
                "estimated_execution_time": _estimated_execution_time(limit=args.limit, mock=bool(args.mock_dicta)),
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    engine = build_engine()
    apply_all(engine, PROJECT_ROOT / "migrations")
    with Session(engine) as session:
        result = run_topic_decision_research(session, config=config, client=client)
        if args.write:
            session.commit()
        else:
            session.rollback()

    print(topic_tree_markdown(result.topic_tree), flush=True)
    print(artifacts_markdown(result.artifacts), flush=True)
    print(quality_report_markdown(result.quality_rows), flush=True)
    print(
        json.dumps(
            {
                "status": "completed",
                "run_id": result.run_id,
                "selected_artifacts": len(result.artifacts),
                "extracted_decisions": len(result.extracted_decisions),
                "accepted_decisions": result.accepted_decision_count,
                "failed_rows": result.failed_count,
                "elapsed_seconds": result.elapsed_seconds,
                "output_paths": result.output_paths,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def _run_dir_name(*, municipality_slug: str, mock: bool, write: bool) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    mode = "mock" if mock else "dicta"
    write_mode = "write" if write else "dry_run"
    return f"{stamp}_{municipality_slug}_{mode}_{write_mode}"


def _estimated_execution_time(*, limit: int | None, mock: bool) -> str:
    selected = max(0, int(limit)) if limit is not None else 187
    if mock:
        return "under 10 seconds for the current Ashdod-sized set"
    low_minutes = max(1, selected // 6)
    high_minutes = max(low_minutes + 1, selected // 2)
    return f"about {low_minutes}-{high_minutes} minutes, depending on local Ollama/Dicta speed"


if __name__ == "__main__":
    raise SystemExit(main())
