#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from municipality.db import build_engine  # noqa: E402
from municipality.migrations import apply_all  # noqa: E402
from municipality.topic_subjects import (  # noqa: E402
    DEFAULT_DICTA_SMALL_MODEL,
    OllamaTopicSubjectV3Client,
    PROVENANCE_V3,
    TopicSubjectResearchConfig,
    TopicSubjectV3ResearchResult,
    build_topic_subject_v3_event_contexts,
    load_accepted_topic_artifacts,
    process_topic_subject_v3_context,
    topic_subject_v3_all_rows_report_markdown,
    topic_subject_v3_event_to_dict,
    topic_subject_v3_quality_report_markdown,
    topic_subject_v3_research_records,
    topic_subject_v3_row_quality_to_dict,
)
from municipality.topic_decisions import DEFAULT_DICTA_MODEL, DEFAULT_OLLAMA_BASE_URL, TopicDecisionArtifact  # noqa: E402


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "rag_eval" / "runs" / "topic_subject_v3_model_benchmarks"
DEFAULT_SAMPLES = ["ashdod:2", "ashdod:5", "ashdod:28", "tel_aviv:10", "tel_aviv:14", "tel_aviv:22"]


@dataclass(slots=True)
class BenchmarkItem:
    municipality_slug: str
    sample_label: str
    artifact: Any
    offset: int | None = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark V3 quality with primary and optional helper-stage Dicta models")
    parser.add_argument("--sample", action="append", help="Sample as municipality_slug:offset. Can be repeated. Defaults to a 6-row Ashdod/Tel Aviv set.")
    parser.add_argument("--docver", action="append", help="Processed document version as municipality_slug:document_version_id. Can be repeated.")
    parser.add_argument("--docver-row", action="append", help="Exact processed row as municipality_slug:document_version_id:source_ordinal. Can be repeated.")
    parser.add_argument("--topic-assignments-json", action="append", type=Path, help="Step 4 topic_assignments.json to benchmark directly without DB import. Can be repeated.")
    parser.add_argument("--json-row", action="append", type=int, help="Exact source ordinal from --topic-assignments-json. Can be repeated.")
    parser.add_argument("--json-max-samples", type=int, default=6, help="Maximum topic-bearing rows to sample from JSON inputs when --json-row is not used.")
    parser.add_argument("--json-muni", default="json", help="Municipality slug label for JSON-only benchmark inputs.")
    parser.add_argument("--max-per-docver", type=int, default=12, help="Evenly sample at most this many accepted artifacts per --docver. Use 0 for all rows.")
    parser.add_argument("--list-only", action="store_true", help="Print selected artifacts without invoking the model.")
    parser.add_argument("--model", default=DEFAULT_DICTA_MODEL)
    parser.add_argument("--small-model", default=DEFAULT_DICTA_SMALL_MODEL, help="Model for helper/repair stages. Defaults to the primary model until mixed routing is explicitly requested.")
    parser.add_argument("--ollama-base-url", default=DEFAULT_OLLAMA_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-text-chars", type=int, default=3500)
    parser.add_argument("--disable-schema-no-think-helpers", action="store_true", help="Disable the default V3 helper-stage schema/no-think profile for A/B debugging.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    json_paths = [path.expanduser().resolve() for path in args.topic_assignments_json or []]
    sample_specs = args.sample or ([] if args.docver or args.docver_row or json_paths else DEFAULT_SAMPLES)
    sample_offsets = [_parse_sample(value) for value in sample_specs]
    docver_specs = [_parse_docver(value) for value in args.docver or []]
    docver_row_specs = [_parse_docver_row(value) for value in args.docver_row or []]

    events: list[Any] = []
    quality_rows: list[Any] = []
    client = OllamaTopicSubjectV3Client()

    if json_paths:
        benchmark_items = _json_benchmark_items(paths=json_paths, municipality_slug=str(args.json_muni), json_rows=args.json_row or [], max_samples=int(args.json_max_samples or 0))
        selection_summary = _selection_summary(benchmark_items)
        output_dir = args.output_root.expanduser().resolve() / _run_dir_name(sample_labels=[item.sample_label for item in benchmark_items], model=str(args.model), small_model=str(args.small_model))
        print(
            json.dumps(
                {
                    "mode": "dry_run_benchmark",
                    "pipeline_version": "v3",
                    "input_mode": "topic_assignments_json",
                    "model": str(args.model),
                    "small_model": str(args.small_model),
                    "schema_no_think_helpers": not bool(args.disable_schema_no_think_helpers),
                    "topic_assignments_json": [str(path) for path in json_paths],
                    "json_rows": args.json_row or [],
                    "selected_artifacts": len(benchmark_items),
                    "estimated_execution_time": "about 1-3 hours for 6 rows; larger sample sets scale roughly linearly with 24B Thinking latency",
                    "output_dir": str(output_dir),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        print(json.dumps({"selection_summary": selection_summary}, ensure_ascii=False), flush=True)
        if args.list_only:
            print(json.dumps({"status": "listed", "selected_artifacts": len(benchmark_items)}, ensure_ascii=False), flush=True)
            return 0
        output_dir.mkdir(parents=True, exist_ok=True)
        _process_items(
            benchmark_items=benchmark_items,
            events=events,
            quality_rows=quality_rows,
            client=client,
            args=args,
        )
    else:
        engine = build_engine()
        apply_all(engine, PROJECT_ROOT / "migrations")
        with Session(engine) as session:
            benchmark_items = _benchmark_items(
                session=session,
                sample_offsets=sample_offsets,
                docver_specs=docver_specs,
                docver_row_specs=docver_row_specs,
                max_per_docver=int(args.max_per_docver or 0),
            )
            selection_summary = _selection_summary(benchmark_items)
            output_dir = args.output_root.expanduser().resolve() / _run_dir_name(sample_labels=[item.sample_label for item in benchmark_items], model=str(args.model), small_model=str(args.small_model))
            print(
                json.dumps(
                    {
                        "mode": "dry_run_benchmark",
                        "pipeline_version": "v3",
                        "input_mode": "db",
                        "model": str(args.model),
                        "small_model": str(args.small_model),
                        "schema_no_think_helpers": not bool(args.disable_schema_no_think_helpers),
                        "samples": sample_specs,
                        "docvers": args.docver or [],
                        "docver_rows": args.docver_row or [],
                        "selected_artifacts": len(benchmark_items),
                        "estimated_execution_time": "about 1-3 hours for 6 rows; larger sample sets scale roughly linearly with 24B Thinking latency",
                        "output_dir": str(output_dir),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            print(json.dumps({"selection_summary": selection_summary}, ensure_ascii=False), flush=True)
            if args.list_only:
                print(json.dumps({"status": "listed", "selected_artifacts": len(benchmark_items)}, ensure_ascii=False), flush=True)
                session.rollback()
                return 0
            output_dir.mkdir(parents=True, exist_ok=True)
            _process_items(
                benchmark_items=benchmark_items,
                events=events,
                quality_rows=quality_rows,
                client=client,
                args=args,
            )
            session.rollback()

    _write_outputs(
        output_dir=output_dir,
        events=events,
        quality_rows=quality_rows,
        samples=[item.sample_label for item in benchmark_items],
        selection_summary=selection_summary,
        model=str(args.model),
        small_model=str(args.small_model),
    )
    print(topic_subject_v3_quality_report_markdown(quality_rows), flush=True)
    print(topic_subject_v3_all_rows_report_markdown(quality_rows), flush=True)
    print(
        json.dumps(
            {
                "status": "completed",
                "pipeline_version": "v3",
                "selected_artifacts": len(quality_rows),
                "events": len(events),
                "failed_rows": sum(1 for row in quality_rows if row.quality_status in {"model_error", "failed"}),
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def _process_items(*, benchmark_items: list[BenchmarkItem], events: list[Any], quality_rows: list[Any], client: OllamaTopicSubjectV3Client, args: argparse.Namespace) -> None:
    for item in benchmark_items:
        context = build_topic_subject_v3_event_contexts(artifacts=[item.artifact], max_context_rows=5)[0]
        config = TopicSubjectResearchConfig(
            municipality_slug=item.municipality_slug,
            model_name=str(args.model),
            small_model_name=str(args.small_model),
            ollama_base_url=str(args.ollama_base_url),
            timeout_seconds=float(args.timeout_seconds),
            max_text_chars=max(500, int(args.max_text_chars)),
            offset=item.offset or 0,
            limit=1,
            write=False,
            output_dir=None,
            pipeline_version="v3",
            use_schema_no_think_helpers=not bool(args.disable_schema_no_think_helpers),
        )
        started = time.perf_counter()
        event, quality = process_topic_subject_v3_context(context=context, client=client, config=config)
        elapsed = round(time.perf_counter() - started, 3)
        quality.metadata["benchmark_elapsed_seconds"] = elapsed
        quality.metadata["benchmark_municipality_slug"] = item.municipality_slug
        quality.metadata["benchmark_sample_label"] = item.sample_label
        quality.metadata["benchmark_sample_offset"] = item.offset
        quality.metadata["benchmark_document_version_id"] = item.artifact.source_document_version_id
        quality.metadata["benchmark_source_kind"] = item.artifact.source_kind
        quality.metadata["benchmark_source_ordinal"] = item.artifact.source_ordinal
        if event is not None:
            events.append(event)
        quality_rows.append(quality)
        print(
            json.dumps(
                {
                    "sample": item.sample_label,
                    "docver": item.artifact.source_document_version_id,
                    "source_kind": item.artifact.source_kind,
                    "source_ordinal": item.artifact.source_ordinal,
                    "elapsed_seconds": elapsed,
                    "quality_status": quality.quality_status,
                    "event": event is not None,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


def _parse_sample(value: str) -> tuple[str, int]:
    if ":" not in value:
        raise ValueError(f"sample must be municipality_slug:offset, got: {value}")
    municipality_slug, offset_text = value.split(":", 1)
    return municipality_slug.strip(), int(offset_text)


def _parse_docver(value: str) -> tuple[str, int]:
    if ":" not in value:
        raise ValueError(f"docver must be municipality_slug:document_version_id, got: {value}")
    municipality_slug, docver_text = value.split(":", 1)
    return municipality_slug.strip(), int(docver_text)


def _parse_docver_row(value: str) -> tuple[str, int, int]:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"docver-row must be municipality_slug:document_version_id:source_ordinal, got: {value}")
    municipality_slug, docver_text, ordinal_text = parts
    return municipality_slug.strip(), int(docver_text), int(ordinal_text)


def _json_benchmark_items(*, paths: list[Path], municipality_slug: str, json_rows: list[int], max_samples: int) -> list[BenchmarkItem]:
    selected: list[BenchmarkItem] = []
    row_filter = set(json_rows)
    for path_index, path in enumerate(paths):
        artifacts = _topic_assignment_artifacts(path=path, source_document_version_id=900000 + path_index)
        if row_filter:
            artifacts = [artifact for artifact in artifacts if artifact.source_ordinal in row_filter]
        else:
            topic_bearing = [artifact for artifact in artifacts if bool(artifact.metadata.get("artifact_metadata", {}).get("topic_assignment", {}).get("is_topic_bearing"))]
            artifacts = topic_bearing or artifacts
            if max_samples > 0:
                artifacts = _sample_docver_artifacts(artifacts, max_per_docver=max_samples)
        if not artifacts:
            raise ValueError(f"no JSON benchmark artifacts selected from {path}")
        slug = municipality_slug if municipality_slug != "json" else _infer_municipality_slug_from_path(path)
        for artifact in artifacts:
            selected.append(BenchmarkItem(municipality_slug=slug, sample_label=f"{slug}:json{path_index}:ord{artifact.source_ordinal}", artifact=artifact, offset=None))
    return selected


def _topic_assignment_artifacts(*, path: Path, source_document_version_id: int) -> list[TopicDecisionArtifact]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("step") != "step4_v4_global_topic_assignment":
        raise ValueError(f"expected Step 4 topic_assignments.json, got step={payload.get('step')!r}: {path}")
    structure_path_value = payload.get("input_structure_units_json")
    if not structure_path_value:
        raise ValueError(f"topic assignments missing input_structure_units_json: {path}")
    structure_path = Path(str(structure_path_value)).expanduser().resolve()
    structure_payload = json.loads(structure_path.read_text(encoding="utf-8"))
    units = {str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or ""): unit for unit in structure_payload.get("structure_units") or [] if isinstance(unit, dict)}
    items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    if not items:
        raise ValueError(f"topic assignments contain no items: {path}")

    artifacts: list[TopicDecisionArtifact] = []
    for ordinal, item in enumerate(items):
        unit_id = str(item.get("structure_unit_id") or item.get("semantic_unit_id") or "")
        unit = units.get(unit_id, {})
        raw_text = str(unit.get("raw_text") or item.get("unit_raw_text") or item.get("raw_text") or "").strip()
        if not raw_text:
            continue
        topic = _topic_assignment_labels(item)
        page = _int_or_none(unit.get("page") or item.get("source_page"))
        artifact_id = f"json_{source_document_version_id}_{ordinal}_{unit_id}"[:64]
        metadata = {
            "topic_assignments_json": str(path),
            "structure_units_json": str(structure_path),
            "structure_metadata": {
                "structure_unit_id": unit_id,
                "semantic_unit_id": unit.get("semantic_unit_id") or item.get("semantic_unit_id"),
                "source_semantic_unit_ids": unit.get("source_semantic_unit_ids") or [],
                "source_window_id": unit.get("source_window_id") or item.get("source_window_id"),
                "source_region_ids": unit.get("source_region_ids") or item.get("source_region_ids") or [],
                "source_block_ids": unit.get("source_block_ids") or item.get("source_block_ids") or [],
                "structural_role": unit.get("structural_role") or item.get("structural_role"),
                "section_id": unit.get("section_id") or item.get("section_id"),
                "section_number": unit.get("section_number") or item.get("section_number"),
            },
            "topic_assignment": topic,
            "summary_he": unit.get("summary_he") or item.get("topic_subject_he"),
            "topic_headline_he": item.get("topic_headline_he"),
            "topic_subject_he": item.get("topic_subject_he"),
            "topic_context_source": item.get("topic_context_source"),
            "topic_headline_source": item.get("topic_headline_source"),
            "protocol_subject_he": item.get("protocol_subject_he") or payload.get("document_context", {}).get("protocol_subject_he"),
            "step4_item": item,
        }
        artifacts.append(
            TopicDecisionArtifact(
                artifact_id=artifact_id,
                semantic_node_id=0,
                topic_label_he=topic.get("child_label_he") or topic.get("root_label_he") or str(unit.get("structural_role") or item.get("structural_role") or "מקור עירוני"),
                root_topic_id=topic.get("root_topic_id"),
                root_label_he=topic.get("root_label_he"),
                child_topic_id=topic.get("child_topic_id"),
                child_label_he=topic.get("child_label_he"),
                source_kind="topic_assignments_json_v4",
                source_document_id=0,
                source_document_version_id=source_document_version_id,
                source_ordinal=ordinal,
                source_title=str(payload.get("document_context", {}).get("protocol_subject_he") or path.parent.name),
                source_url=str(path),
                artifact_kind="topic_assignment_item",
                start_page=page,
                end_page=page,
                header_path=[value for value in [topic.get("root_label_he"), topic.get("child_label_he"), unit.get("structural_role") or item.get("structural_role")] if value],
                real_text=raw_text,
                retrieval_text=str(item.get("topic_identification_context") or raw_text),
                topic_confidence=float(topic.get("confidence") or 0.0),
                existing_decision_candidate_id=None,
                metadata={"artifact_metadata": metadata, "link_metadata": topic},
            )
        )
    return artifacts


def _topic_assignment_labels(item: dict[str, Any]) -> dict[str, Any]:
    root_topic_id = _first_text(item.get("root_topic_id"), item.get("primary_root_topic_id"), item.get("assigned_root_topic_id"))
    root_label = _first_text(item.get("root_label_he"), item.get("primary_root_label_he"), item.get("assigned_root_label_he"))
    child_topic_id = _first_text(item.get("child_topic_id"), item.get("primary_child_topic_id"), item.get("assigned_child_topic_id"))
    child_label = _first_text(item.get("child_label_he"), item.get("primary_child_label_he"), item.get("assigned_child_label_he"), item.get("topic_subject_he"))
    confidence = _float_or_none(item.get("topic_assignment_confidence") or item.get("confidence"))
    candidates = item.get("root_topic_candidates") if isinstance(item.get("root_topic_candidates"), list) else []
    if (not root_topic_id or not root_label) and candidates:
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        root_topic_id = root_topic_id or _first_text(first.get("root_topic_id"))
        root_label = root_label or _first_text(first.get("root_label_he"))
        child_topic_id = child_topic_id or _first_text(first.get("child_choice_id"), first.get("child_topic_id"))
        child_label = child_label or _first_text(first.get("child_label_he"))
        confidence = confidence if confidence is not None else _float_or_none(first.get("score"))
    return {
        "root_topic_id": root_topic_id or None,
        "root_label_he": root_label or None,
        "child_topic_id": child_topic_id or None,
        "child_label_he": child_label or None,
        "confidence": confidence if confidence is not None else 0.0,
        "route": item.get("topic_assignment_route") or item.get("deterministic_classifier_method"),
        "status": item.get("topic_node_status") or ("active" if item.get("is_topic_bearing") else "non_topic"),
        "is_topic_bearing": bool(item.get("is_topic_bearing")),
        "row_type": item.get("row_type"),
        "topic_supporting_quote_he": item.get("topic_supporting_quote_he") or item.get("topic_anchor_quote_he"),
    }


def _first_text(*values: Any) -> str:
    for value in values:
        text_value = str(value or "").strip()
        if text_value:
            return text_value
    return ""


def _infer_municipality_slug_from_path(path: Path) -> str:
    text = str(path)
    if "tel_aviv" in text:
        return "tel_aviv"
    if "ashdod" in text:
        return "ashdod"
    return "json"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _benchmark_items(
    *,
    session: Session,
    sample_offsets: list[tuple[str, int]],
    docver_specs: list[tuple[str, int]],
    docver_row_specs: list[tuple[str, int, int]],
    max_per_docver: int,
) -> list[BenchmarkItem]:
    artifacts_by_slug: dict[str, list[Any]] = {}
    items: list[BenchmarkItem] = []
    seen_artifact_ids: set[str] = set()

    def slug_artifacts(municipality_slug: str) -> list[Any]:
        if municipality_slug not in artifacts_by_slug:
            artifacts_by_slug[municipality_slug] = load_accepted_topic_artifacts(session, municipality_slug=municipality_slug)
        return artifacts_by_slug[municipality_slug]

    for municipality_slug, offset in sample_offsets:
        artifacts = slug_artifacts(municipality_slug)
        if offset >= len(artifacts):
            raise ValueError(f"sample offset out of range for {municipality_slug}: {offset}")
        artifact = artifacts[offset]
        if artifact.artifact_id in seen_artifact_ids:
            continue
        seen_artifact_ids.add(artifact.artifact_id)
        items.append(BenchmarkItem(municipality_slug=municipality_slug, sample_label=f"{municipality_slug}:{offset}", artifact=artifact, offset=offset))

    for municipality_slug, document_version_id in docver_specs:
        doc_artifacts = [artifact for artifact in slug_artifacts(municipality_slug) if artifact.source_document_version_id == document_version_id]
        if not doc_artifacts:
            raise ValueError(f"no accepted artifacts found for {municipality_slug}:{document_version_id}")
        for artifact in _sample_docver_artifacts(doc_artifacts, max_per_docver=max_per_docver):
            if artifact.artifact_id in seen_artifact_ids:
                continue
            seen_artifact_ids.add(artifact.artifact_id)
            items.append(
                BenchmarkItem(
                    municipality_slug=municipality_slug,
                    sample_label=f"{municipality_slug}:docver{document_version_id}:ord{artifact.source_ordinal}",
                    artifact=artifact,
                    offset=None,
                )
            )

    for municipality_slug, document_version_id, source_ordinal in docver_row_specs:
        matches = [
            artifact
            for artifact in slug_artifacts(municipality_slug)
            if artifact.source_document_version_id == document_version_id and artifact.source_ordinal == source_ordinal
        ]
        if not matches:
            raise ValueError(f"no accepted artifact found for {municipality_slug}:{document_version_id}:{source_ordinal}")
        for artifact in sorted(matches, key=lambda item: item.artifact_id):
            if artifact.artifact_id in seen_artifact_ids:
                continue
            seen_artifact_ids.add(artifact.artifact_id)
            items.append(
                BenchmarkItem(
                    municipality_slug=municipality_slug,
                    sample_label=f"{municipality_slug}:docver{document_version_id}:ord{source_ordinal}",
                    artifact=artifact,
                    offset=None,
                )
            )

    if not items:
        raise ValueError("no benchmark samples selected")
    return items


def _sample_docver_artifacts(artifacts: list[Any], *, max_per_docver: int) -> list[Any]:
    ordered = sorted(artifacts, key=lambda item: (item.source_ordinal, item.artifact_id))
    if max_per_docver <= 0 or len(ordered) <= max_per_docver:
        return ordered
    if max_per_docver == 1:
        return [ordered[len(ordered) // 2]]
    indexes = sorted({round(index * (len(ordered) - 1) / (max_per_docver - 1)) for index in range(max_per_docver)})
    return [ordered[index] for index in indexes]


def _selection_summary(items: list[BenchmarkItem]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, str], list[int]] = {}
    for item in items:
        key = (item.municipality_slug, item.artifact.source_document_version_id, item.artifact.source_kind)
        grouped.setdefault(key, []).append(item.artifact.source_ordinal)
    return [
        {
            "municipality_slug": municipality_slug,
            "document_version_id": document_version_id,
            "source_kind": source_kind,
            "selected_rows": len(ordinals),
            "source_ordinals": sorted(ordinals),
        }
        for (municipality_slug, document_version_id, source_kind), ordinals in sorted(grouped.items())
    ]


def _run_dir_name(*, sample_labels: list[str], model: str, small_model: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    sample_label = "_".join(_safe_path_label(label) for label in sample_labels[:4])
    if len(sample_labels) > 4:
        sample_label += f"_plus{len(sample_labels) - 4}"
    model_policy = "single_model" if model == small_model else "mixed_models"
    return f"{stamp}_{sample_label}_v3_quality_{model_policy}"


def _safe_path_label(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")[:48] or "sample"


def _write_outputs(
    *,
    output_dir: Path,
    events: list[Any],
    quality_rows: list[Any],
    samples: list[str],
    selection_summary: list[dict[str, Any]],
    model: str,
    small_model: str,
) -> None:
    artifacts = [row.artifact for row in quality_rows]
    research_result = TopicSubjectV3ResearchResult(run_id=None, topic_tree={}, artifacts=artifacts, events=events, row_quality_rows=quality_rows, elapsed_seconds=0.0)
    (output_dir / "v3_events.json").write_text(json.dumps([topic_subject_v3_event_to_dict(event) for event in events], ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "v3_quality_report.json").write_text(json.dumps([topic_subject_v3_row_quality_to_dict(row) for row in quality_rows], ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "v3_research_events_subjects.json").write_text(json.dumps(topic_subject_v3_research_records(research_result), ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "v3_quality_report.md").write_text(topic_subject_v3_quality_report_markdown(quality_rows), encoding="utf-8")
    (output_dir / "v3_all_rows_report.md").write_text(topic_subject_v3_all_rows_report_markdown(quality_rows), encoding="utf-8")
    (output_dir / "v3_summary.json").write_text(
        json.dumps(
            {
                "provenance": PROVENANCE_V3,
                "benchmark": "topic_subject_v3_quality_single_model" if model == small_model else "topic_subject_v3_quality_mixed_models",
                "samples": samples,
                "selection_summary": selection_summary,
                "model": model,
                "small_model": small_model,
                "selected_artifacts": len(quality_rows),
                "events": len(events),
                "failed_rows": sum(1 for row in quality_rows if row.quality_status in {"model_error", "failed"}),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
