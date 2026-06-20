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
from municipality.topic_decisions import DEFAULT_DICTA_MODEL, DEFAULT_OLLAMA_BASE_URL, artifacts_markdown, topic_tree_markdown  # noqa: E402
from municipality.topic_subjects import (  # noqa: E402
    DEFAULT_DICTA_SMALL_MODEL,
    MockTopicSubjectClient,
    MockTopicSubjectV3Client,
    OllamaTopicSubjectClient,
    OllamaTopicSubjectV3Client,
    TopicSubjectResearchConfig,
    quality_report_markdown,
    run_topic_subject_research,
    run_topic_subject_v3_research,
    subject_tree_markdown,
    topic_subject_v3_quality_report_markdown,
)


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "rag_eval" / "runs" / "topic_subject_research"


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract open subject-tree candidates from accepted topic artifacts")
    parser.add_argument("--muni", default="ashdod", help="Municipality slug")
    parser.add_argument("--offset", type=int, default=0, help="Skip this many accepted artifacts before sampling")
    parser.add_argument("--limit", type=int, help="Optional artifact limit for development runs")
    parser.add_argument("--write", action="store_true", help="Persist run, subject candidates, and quality rows; default is dry-run")
    parser.add_argument("--pipeline-version", choices=["legacy", "v3"], default="legacy", help="legacy keeps the current V2-compatible extractor; v3 uses event-first Dicta contextual extraction")
    parser.add_argument("--mock-dicta", action="store_true", help="Use a fast local mock client for verification instead of calling Ollama")
    parser.add_argument("--model", default=DEFAULT_DICTA_MODEL)
    parser.add_argument("--small-model", default=DEFAULT_DICTA_SMALL_MODEL, help="Model used for short bounded support stages such as quote/JSON repair and V3 evidence entailment")
    parser.add_argument("--ollama-base-url", default=DEFAULT_OLLAMA_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--max-text-chars", type=int, default=3500)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    output_dir = args.output_root.expanduser().resolve() / _run_dir_name(municipality_slug=str(args.muni), mock=bool(args.mock_dicta), write=bool(args.write), pipeline_version=str(args.pipeline_version))
    config = TopicSubjectResearchConfig(
        municipality_slug=str(args.muni),
        model_name=str(args.model),
        small_model_name=str(args.small_model),
        ollama_base_url=str(args.ollama_base_url),
        timeout_seconds=float(args.timeout_seconds),
        max_text_chars=max(500, int(args.max_text_chars)),
        offset=max(0, int(args.offset or 0)),
        limit=args.limit,
        write=bool(args.write),
        output_dir=output_dir,
        pipeline_version=str(args.pipeline_version),
    )
    if args.pipeline_version == "v3":
        client = MockTopicSubjectV3Client() if args.mock_dicta else OllamaTopicSubjectV3Client()
    else:
        client = MockTopicSubjectClient() if args.mock_dicta else OllamaTopicSubjectClient()

    print(
        json.dumps(
            {
                "mode": "write" if args.write else "dry_run",
                "pipeline_version": config.pipeline_version,
                "municipality": config.municipality_slug,
                "model": "mock" if args.mock_dicta else config.model_name,
                "small_model": "mock" if args.mock_dicta else config.small_model_name,
                "offset": config.offset,
                "estimated_execution_time": _estimated_execution_time(limit=args.limit, mock=bool(args.mock_dicta), pipeline_version=str(args.pipeline_version)),
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    engine = build_engine()
    apply_all(engine, PROJECT_ROOT / "migrations")
    with Session(engine) as session:
        if args.pipeline_version == "v3":
            result = run_topic_subject_v3_research(session, config=config, client=client)
        else:
            result = run_topic_subject_research(session, config=config, client=client)
        if args.write:
            session.commit()
        else:
            session.rollback()

    if args.pipeline_version == "v3":
        print(topic_subject_v3_quality_report_markdown(result.row_quality_rows), flush=True)
        summary = {
            "status": "completed",
            "pipeline_version": "v3",
            "run_id": result.run_id,
            "model": "mock" if args.mock_dicta else config.model_name,
            "small_model": "mock" if args.mock_dicta else config.small_model_name,
            "selected_artifacts": len(result.artifacts),
            "events": result.event_count,
            "candidate_subjects": result.candidate_subject_count,
            "candidate_decisions": result.candidate_decision_count,
            "failed_rows": result.failed_count,
            "elapsed_seconds": result.elapsed_seconds,
            "output_paths": result.output_paths,
        }
    else:
        print(topic_tree_markdown(result.topic_tree), flush=True)
        print(artifacts_markdown(result.artifacts), flush=True)
        print(subject_tree_markdown(result.extracted_subjects), flush=True)
        print(quality_report_markdown(result.quality_rows), flush=True)
        summary = {
            "status": "completed",
            "pipeline_version": "legacy",
            "run_id": result.run_id,
            "model": "mock" if args.mock_dicta else config.model_name,
            "small_model": "mock" if args.mock_dicta else config.small_model_name,
            "selected_artifacts": len(result.artifacts),
            "extracted_subjects": len(result.extracted_subjects),
            "candidate_subjects": result.candidate_subject_count,
            "candidate_decisions": result.candidate_decision_count,
            "failed_rows": result.failed_count,
            "elapsed_seconds": result.elapsed_seconds,
            "output_paths": result.output_paths,
        }
    print(
        json.dumps(summary, ensure_ascii=False),
        flush=True,
    )
    return 0


def _run_dir_name(*, municipality_slug: str, mock: bool, write: bool, pipeline_version: str) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    mode = "mock" if mock else "dicta"
    write_mode = "write" if write else "dry_run"
    version = "v3" if pipeline_version == "v3" else "legacy"
    return f"{stamp}_{municipality_slug}_{version}_{mode}_{write_mode}"


def _estimated_execution_time(*, limit: int | None, mock: bool, pipeline_version: str) -> str:
    selected = max(0, int(limit)) if limit is not None else 187
    if mock:
        return "under 10 seconds for the current Ashdod-sized set"
    if pipeline_version == "v3":
        low_minutes = max(2, selected // 3)
        high_minutes = max(low_minutes + 2, selected)
        return f"about {low_minutes}-{high_minutes} minutes, depending on local Ollama/Dicta speed"
    low_minutes = max(1, selected // 6)
    high_minutes = max(low_minutes + 1, selected // 2)
    return f"about {low_minutes}-{high_minutes} minutes, depending on local Ollama/Dicta speed"


if __name__ == "__main__":
    raise SystemExit(main())
