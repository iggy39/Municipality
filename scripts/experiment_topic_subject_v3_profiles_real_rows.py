#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "rag_eval" / "runs" / "topic_subject_v3_profile_real_row_experiments"
DEFAULT_MODEL = "dicta-il/DictaLM-3.0-24B-Thinking:bf16"
DEFAULT_SMALL_MODEL = "dicta-il/DictaLM-3.0-1.7B-Thinking:latest"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_ROWS = ["ashdod:1:2", "tel_aviv:20:10"]
HELPER_STAGES = {
    "topic_subject_json_repair",
    "topic_subject_v3_json_repair",
    "topic_subject_v3_quote_repair",
    "topic_subject_v3_evidence_entailment",
    "topic_subject_v3_formal_decision_evidence_repair",
}
OPTIONAL_RECOVERY_STAGES = {
    "topic_subject_v3_non_event_reconsideration",
    "topic_subject_v3_json_repair",
    "topic_subject_v3_formal_decision_evidence_repair",
}
PROFILE_CHOICES = [
    "baseline_current",
    "mixed_helpers_1_7b",
    "schema_no_think_24b_helpers",
    "schema_no_think_24b_all",
]


EXPECTED_BY_ROW = {
    "ashdod:1:2": {
        "is_event": True,
        "action_types": ["בקשה"],
        "matter_keywords": ["סמינר", "חדשנות", "חינוך"],
        "outcome_is_decision": False,
        "outcome_type": "none",
        "judgement": "Open request for approval/payment of seminar and travel costs; no formal council decision is present in the row.",
    },
    "tel_aviv:20:10": {
        "is_event": True,
        "action_types": ["אישור"],
        "matter_keywords": ["בחירת", "יו\"ר", "מועצת"],
        "outcome_is_decision": True,
        "outcome_type": "approved",
        "judgement": "Formal decision approving/electing the council chair; decision outcome is directly stated.",
    },
    "ashdod:2:2": {
        "is_event": True,
        "action_types": ["בקשה"],
        "matter_keywords": ["האצלת", "סמכויות", "לינור"],
        "outcome_is_decision": False,
        "outcome_type": "none",
        "judgement": "Open request asking the council to approve delegation of signature authority to Linor Cohen; no final decision is present.",
    },
    "ashdod:4:0": {
        "is_event": True,
        "action_types": ["הצעה לסדר יום"],
        "matter_keywords": ["הצפות", "תשתיות"],
        "outcome_is_decision": False,
        "outcome_type": "none",
        "judgement": "Agenda proposal about recurring floods, infrastructure treatment, and compensation; no council decision is present in the row.",
    },
    "ashdod:8:23": {
        "is_event": True,
        "action_types": ["אישור"],
        "matter_keywords": ["ועדה", "מקצועית"],
        "outcome_is_decision": True,
        "outcome_type": "approved",
        "judgement": "Subcommittee approves professional committee decision 9 decision 1, with two members opposing; this is a formal approval outcome.",
    },
    "ashdod:10:2": {
        "is_event": True,
        "action_types": ["המלצה"],
        "matter_keywords": ["אבטחת", "מידע"],
        "outcome_is_decision": False,
        "outcome_type": "none",
        "judgement": "Audit committee recommends council decisions on information security and staffing; the row states recommendations, not a final council approval.",
    },
    "ashdod:12:0": {
        "is_event": True,
        "action_types": ["מענה לשאילתה"],
        "matter_keywords": ["גללי", "כלבים"],
        "outcome_is_decision": False,
        "outcome_type": "none",
        "judgement": "Municipal answer to an inquiry about dog feces cleanup, enforcement, and public-awareness activity; no formal decision outcome.",
    },
    "ashdod:13:2": {
        "is_event": True,
        "action_types": ["שאילתה"],
        "matter_keywords": ["גרעון"],
        "outcome_is_decision": False,
        "outcome_type": "none",
        "judgement": "Inquiry title asking about the source for covering a deficit; this is an inquiry, not a decision.",
    },
    "tel_aviv:21:4": {
        "is_event": True,
        "action_types": ["אישור"],
        "matter_keywords": ["סגן", "ראש", "עירייה"],
        "outcome_is_decision": True,
        "outcome_type": "approved",
        "judgement": "Formal council decision approved by majority: remove one deputy mayor and choose another deputy mayor with delegated powers.",
    },
    "tel_aviv:22:12": {
        "is_event": False,
        "outcome_is_decision": False,
        "outcome_type": "none",
        "judgement": "Procedural closing text thanking participants and closing meetings; no standalone municipal subject/action decision is present.",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real-row Topic Subject V3 profile experiments.")
    parser.add_argument("--row", action="append", default=[], help="Exact row as municipality_slug:document_version_id:source_ordinal.")
    parser.add_argument("--topic-assignments-json", action="append", type=Path, help="Step 4 topic_assignments.json to run directly without DB import. Can be repeated.")
    parser.add_argument("--json-row", action="append", type=int, help="Exact source ordinal from --topic-assignments-json. Can be repeated.")
    parser.add_argument("--json-max-samples", type=int, default=6, help="Maximum topic-bearing rows to sample from JSON inputs when --json-row is not used.")
    parser.add_argument("--json-muni", default="json", help="Municipality slug label for JSON-only inputs.")
    parser.add_argument("--profile", action="append", choices=PROFILE_CHOICES, default=[], help="Profile to run. Repeatable.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--small-model", default=DEFAULT_SMALL_MODEL)
    parser.add_argument("--ollama-base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-text-chars", type=int, default=3500)
    parser.add_argument("--list-only", action="store_true", help="List selected rows; no model calls.")
    parser.add_argument("--dry-run", action="store_true", help="Write planned first-stage requests; no model calls.")
    parser.add_argument("--live", action="store_true", help="Actually call Ollama and run the V3 pipeline.")
    parser.add_argument("--fail-on-problem", action="store_true", help="Exit 2 when semantic or schema problems are found.")
    parser.add_argument("--resume-stage-dir", type=Path, help="Reuse matching saved stage-call JSON files from an interrupted run.")
    args = parser.parse_args()

    if args.live and args.dry_run:
        raise SystemExit("choose either --live or --dry-run, not both")
    if not args.live and not args.dry_run and not args.list_only:
        args.dry_run = True

    module = _import_topic_subject_modules()
    json_paths = [path.expanduser().resolve() for path in args.topic_assignments_json or []]
    row_specs = [_parse_row_spec(value) for value in (args.row or ([] if json_paths else DEFAULT_ROWS))]
    profiles = args.profile or ["baseline_current", "mixed_helpers_1_7b", "schema_no_think_24b_helpers"]
    output_dir = _output_dir(root=args.output_root.expanduser().resolve(), live=bool(args.live))
    output_dir.mkdir(parents=True, exist_ok=True)
    stage_dir = output_dir / "stage_calls"
    stage_dir.mkdir(exist_ok=True)

    started_all = time.perf_counter()
    if json_paths:
        selected_rows = _load_json_rows(
            module=module,
            paths=json_paths,
            municipality_slug=str(args.json_muni),
            json_rows=args.json_row or [],
            max_samples=int(args.json_max_samples or 0),
        )
    else:
        selected_rows = _load_rows(module=module, row_specs=row_specs)
    selection = [_selection_row(row) for row in selected_rows]
    if args.list_only:
        _write_summary(output_dir=output_dir, summary={"status": "listed", "selected_rows": selection, "profiles": profiles})
        print(json.dumps({"status": "listed", "selected_rows": selection, "output_dir": str(output_dir)}, ensure_ascii=False), flush=True)
        return 0

    results = []
    problem_rows = []
    if args.dry_run:
        for row in selected_rows:
            for profile in profiles:
                result = _write_planned_normalization_request(
                    module=module,
                    row=row,
                    profile=profile,
                    args=args,
                    stage_dir=stage_dir,
                )
                results.append(result)
        summary = _summary_payload(
            mode="dry_run",
            output_dir=output_dir,
            selected_rows=selection,
            profiles=profiles,
            results=results,
            problem_rows=problem_rows,
            elapsed_seconds=time.perf_counter() - started_all,
            args=args,
        )
        _write_outputs(output_dir=output_dir, summary=summary, results=results, problem_rows=problem_rows)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0

    for row in selected_rows:
        for profile in profiles:
            result, problems = _run_profile_on_row(
                module=module,
                row=row,
                profile=profile,
                args=args,
                stage_dir=stage_dir,
            )
            results.append(result)
            problem_rows.extend(problems)

    summary = _summary_payload(
        mode="live",
        output_dir=output_dir,
        selected_rows=selection,
        profiles=profiles,
        results=results,
        problem_rows=problem_rows,
        elapsed_seconds=time.perf_counter() - started_all,
        args=args,
    )
    _write_outputs(output_dir=output_dir, summary=summary, results=results, problem_rows=problem_rows)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    print(_console_summary(results=results, problem_rows=problem_rows), flush=True)
    if args.fail_on_problem and problem_rows:
        return 2
    return 0


def _import_topic_subject_modules() -> Any:
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))
    try:
        import municipality.topic_subjects as topic_subjects_module  # noqa: PLC0415
        from municipality.db import build_engine  # noqa: PLC0415
        from municipality.migrations import apply_all  # noqa: PLC0415
        from sqlalchemy.orm import Session  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "Could not import project dependencies. Use the project Python environment first. "
            f"Import error: {exc.__class__.__name__}: {exc}"
        ) from exc
    topic_subjects_module._experiment_build_engine = build_engine
    topic_subjects_module._experiment_apply_all = apply_all
    topic_subjects_module._experiment_session_cls = Session
    return topic_subjects_module


def _parse_row_spec(value: str) -> dict[str, Any]:
    parts = str(value).split(":")
    if len(parts) != 3:
        raise SystemExit("row must be municipality_slug:document_version_id:source_ordinal, got: " + str(value))
    return {"key": str(value), "municipality_slug": parts[0], "document_version_id": int(parts[1]), "source_ordinal": int(parts[2])}


def _load_rows(*, module: Any, row_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    engine = module._experiment_build_engine()
    module._experiment_apply_all(engine, PROJECT_ROOT / "migrations")
    with module._experiment_session_cls(engine) as session:
        for spec in row_specs:
            artifacts = module.load_accepted_topic_artifacts(session, municipality_slug=spec["municipality_slug"])
            matches = [
                artifact
                for artifact in artifacts
                if int(artifact.source_document_version_id) == int(spec["document_version_id"])
                and int(artifact.source_ordinal) == int(spec["source_ordinal"])
            ]
            if not matches:
                raise SystemExit("No accepted V3 artifact found for row " + spec["key"])
            artifact = sorted(matches, key=lambda item: item.artifact_id)[0]
            context = module.build_topic_subject_v3_event_contexts(artifacts=[artifact], context_artifacts=artifacts, max_context_rows=5)[0]
            rows.append({"spec": spec, "artifact": artifact, "context": context})
        session.rollback()
    return rows


def _load_json_rows(*, module: Any, paths: list[Path], municipality_slug: str, json_rows: list[int], max_samples: int) -> list[dict[str, Any]]:
    try:
        from benchmark_topic_subject_v3_models import (  # noqa: PLC0415
            _infer_municipality_slug_from_path,
            _sample_docver_artifacts,
            _topic_assignment_artifacts,
        )
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Could not import JSON benchmark helpers: {exc.__class__.__name__}: {exc}") from exc

    rows = []
    row_filter = set(json_rows)
    for path_index, path in enumerate(paths):
        all_artifacts = _topic_assignment_artifacts(path=path, source_document_version_id=900000 + path_index)
        artifacts = list(all_artifacts)
        if row_filter:
            artifacts = [artifact for artifact in artifacts if artifact.source_ordinal in row_filter]
        else:
            topic_bearing = [
                artifact
                for artifact in artifacts
                if bool(artifact.metadata.get("artifact_metadata", {}).get("topic_assignment", {}).get("is_topic_bearing"))
            ]
            artifacts = topic_bearing or artifacts
            if max_samples > 0:
                artifacts = _sample_docver_artifacts(artifacts, max_per_docver=max_samples)
        if not artifacts:
            raise SystemExit(f"No JSON profile rows selected from {path}")
        slug = municipality_slug if municipality_slug != "json" else _infer_municipality_slug_from_path(path)
        contexts_by_artifact_id = {
            context.target_artifact.artifact_id: context
            for context in module.build_topic_subject_v3_event_contexts(
                artifacts=artifacts,
                context_artifacts=all_artifacts,
                max_context_rows=5,
            )
        }
        for artifact in artifacts:
            context = contexts_by_artifact_id[artifact.artifact_id]
            spec = {
                "key": f"{slug}:json{path_index}:ord{artifact.source_ordinal}",
                "municipality_slug": slug,
                "document_version_id": int(artifact.source_document_version_id),
                "source_ordinal": int(artifact.source_ordinal),
                "source_kind": artifact.source_kind,
                "topic_assignments_json": str(path),
            }
            rows.append({"spec": spec, "artifact": artifact, "context": context})
    return rows


def _selection_row(row: dict[str, Any]) -> dict[str, Any]:
    artifact = row["artifact"]
    return {
        "row": row["spec"]["key"],
        "artifact_id": artifact.artifact_id,
        "municipality_slug": row["spec"]["municipality_slug"],
        "document_version_id": artifact.source_document_version_id,
        "source_ordinal": artifact.source_ordinal,
        "topic_label_he": artifact.topic_label_he,
        "text": _short(artifact.real_text, 500),
    }


def _run_profile_on_row(*, module: Any, row: dict[str, Any], profile: str, args: argparse.Namespace, stage_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spec = row["spec"]
    artifact = row["artifact"]
    context = row["context"]
    config = _config_for_profile(module=module, row=row, profile=profile, args=args)
    client_cls = _profiled_client_class(module)
    resume_stage_dir = args.resume_stage_dir.expanduser().resolve() if args.resume_stage_dir else None
    client = client_cls(profile=profile, stage_dir=stage_dir, resume_stage_dir=resume_stage_dir, primary_model=str(args.model), small_model=str(args.small_model))
    started = time.perf_counter()
    event, quality = module.process_topic_subject_v3_context(context=context, client=client, config=config)
    elapsed = round(time.perf_counter() - started, 3)
    event_dict = module.topic_subject_v3_event_to_dict(event) if event is not None else None
    quality_dict = module.topic_subject_v3_row_quality_to_dict(quality)
    prediction = _prediction_from_event(event_dict=event_dict, quality_dict=quality_dict)
    expected = EXPECTED_BY_ROW.get(spec["key"], {})
    semantic_problems = _semantic_problems(expected=expected, prediction=prediction)
    stage_problems = _stage_problems(stage_calls=client.stage_calls, prediction=prediction)
    result = {
        "row": spec["key"],
        "profile": profile,
        "artifact_id": artifact.artifact_id,
        "full_source_text": artifact.real_text,
        "elapsed_seconds": elapsed,
        "event": event_dict,
        "quality": quality_dict,
        "prediction": prediction,
        "expected": expected,
        "stage_calls": client.stage_calls,
        "stage_count": len(client.stage_calls),
        "problem_count": len(semantic_problems) + len(stage_problems),
    }
    problems = [
        _problem_row(row=row, profile=profile, prediction=prediction, expected=expected, reason=reason, severity="error")
        for reason in semantic_problems
    ]
    problems.extend(
        _problem_row(row=row, profile=profile, prediction=prediction, expected=expected, reason=reason, severity="warning")
        for reason in stage_problems
    )
    return result, problems


def _write_planned_normalization_request(*, module: Any, row: dict[str, Any], profile: str, args: argparse.Namespace, stage_dir: Path) -> dict[str, Any]:
    config = _config_for_profile(module=module, row=row, profile=profile, args=args)
    request_payload = module.topic_subject_v3_normalization_payload(context=row["context"], max_text_chars=int(args.max_text_chars))
    body = _request_body_for_stage(
        module=module,
        profile=profile,
        stage="topic_subject_v3_contextual_event_normalization",
        request_payload=request_payload,
        system_prompt="You normalize Hebrew municipal protocol context into one target event. Return strict JSON only.",
        config=config,
        primary_model=str(args.model),
        small_model=str(args.small_model),
        num_predict=2200,
    )
    call_id = _call_id(row=row, profile=profile, stage="topic_subject_v3_contextual_event_normalization", body=body)
    path = stage_dir / (call_id + ".json")
    record = {
        "call_id": call_id,
        "row": row["spec"]["key"],
        "profile": profile,
        "stage": "topic_subject_v3_contextual_event_normalization",
        "status": "planned",
        "request_payload": request_payload,
        "request_body": body,
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "row": row["spec"]["key"],
        "profile": profile,
        "artifact_id": row["artifact"].artifact_id,
        "full_source_text": row["artifact"].real_text,
        "planned_stage_call": str(path),
        "stage": "topic_subject_v3_contextual_event_normalization",
        "model": body.get("model"),
        "think": body.get("think"),
        "format": _format_label(body.get("format")),
    }


def _config_for_profile(*, module: Any, row: dict[str, Any], profile: str, args: argparse.Namespace) -> Any:
    small_model = str(args.small_model) if profile == "mixed_helpers_1_7b" else str(args.model)
    return module.TopicSubjectResearchConfig(
        municipality_slug=row["spec"]["municipality_slug"],
        model_name=str(args.model),
        small_model_name=small_model,
        ollama_base_url=str(args.ollama_base_url),
        timeout_seconds=float(args.timeout_seconds),
        max_text_chars=max(500, int(args.max_text_chars)),
        offset=0,
        limit=1,
        write=False,
        output_dir=None,
        pipeline_version="v3",
        # Experiments must be profile-controlled. Production defaults may change,
        # so each experiment profile applies its model/thinking/schema policy below.
        use_schema_no_think_helpers=False,
    )


def _profiled_client_class(module: Any) -> Any:
    class ProfiledV3Client(module.OllamaTopicSubjectV3Client):
        def __init__(self, *, profile: str, stage_dir: Path, resume_stage_dir: Optional[Path], primary_model: str, small_model: str) -> None:
            super().__init__()
            self.profile = profile
            self.stage_dir = stage_dir
            self.resume_stage_dir = resume_stage_dir
            self.primary_model = primary_model
            self.small_model = small_model
            self.stage_calls = []

        def _call_stage_json(self, *, stage: str, request_payload: dict[str, Any], system_prompt: str, config: Any, num_predict: int) -> dict[str, Any]:
            body = _request_body_for_stage(
                module=module,
                profile=self.profile,
                stage=stage,
                request_payload=request_payload,
                system_prompt=system_prompt,
                config=config,
                primary_model=self.primary_model,
                small_model=self.small_model,
                num_predict=num_predict,
            )
            return self._execute_stage(stage=stage, request_payload=request_payload, body=body, config=config)

        def _repair_stage_json(self, *, raw_content: str, stage: str, config: Any) -> Optional[dict[str, Any]]:
            if not str(raw_content or "").strip():
                return None
            repair_stage = "topic_subject_v3_json_repair"
            request_payload = {
                "task": "repair_invalid_topic_subject_v3_json_without_changing_meaning",
                "stage": stage,
                "instructions": [
                    "Convert the supplied text into valid JSON.",
                    "Do not add or infer new municipal facts.",
                    "Use null, false, empty strings, or empty arrays when the original response omitted a value.",
                ],
                "invalid_json_text": str(raw_content)[:6000],
            }
            body = _request_body_for_stage(
                module=module,
                profile=self.profile,
                stage=repair_stage,
                request_payload=request_payload,
                system_prompt="Return valid JSON only. Do not add facts. Do not use markdown.",
                config=config,
                primary_model=self.primary_model,
                small_model=self.small_model,
                num_predict=2200,
            )
            repaired = self._execute_stage(stage=repair_stage, request_payload=request_payload, body=body, config=config)
            if repaired.get("error_code"):
                return None
            return repaired

        def _execute_stage(self, *, stage: str, request_payload: dict[str, Any], body: dict[str, Any], config: Any) -> dict[str, Any]:
            started = time.perf_counter()
            call_id = _stage_call_id(stage=stage, body=body, profile=self.profile)
            cached = self._cached_stage_record(call_id=call_id)
            if cached is not None:
                cached_payload = self._payload_from_cached_stage(stage=stage, record=cached, config=config)
                if cached_payload is not None:
                    return cached_payload
            record = {
                "call_id": call_id,
                "profile": self.profile,
                "stage": stage,
                "model": body.get("model"),
                "think": body.get("think"),
                "format": _format_label(body.get("format")),
                "request_payload": request_payload,
                "request_body": body,
            }
            attempts = 1 if stage == "topic_subject_v3_contextual_event_normalization" else 2
            record["request_attempts"] = attempts
            raw_payload, error = self._post_chat_with_timeout_retry(body=body, config=config, attempts=attempts)
            elapsed = round(time.perf_counter() - started, 3)
            parsed = None
            if error is not None:
                record["response"] = {"status": "error", **error, "elapsed_seconds": elapsed}
                self._record_stage(record)
                return {**error, "stage": stage, "stage_model_name": body.get("model"), "stage_think": body.get("think")}
            content = str(((raw_payload.get("message") or {}).get("content")) or "")
            parsed = module.parse_json_object(content)
            if not isinstance(parsed, dict) and raw_payload.get("done_reason") == "length":
                retry_body = dict(body)
                retry_options = dict(retry_body.get("options") or {})
                retry_options["num_predict"] = max(int(retry_options.get("num_predict") or 0) * 2, int(retry_options.get("num_predict") or 0) + 1200, 2200)
                retry_body["options"] = retry_options
                record["length_retry_attempted"] = True
                retry_payload, retry_error = self._post_chat_with_timeout_retry(body=retry_body, config=config, attempts=1)
                if retry_error is None:
                    retry_content = str(((retry_payload.get("message") or {}).get("content")) or "")
                    retry_parsed = module.parse_json_object(retry_content)
                    raw_payload = {**retry_payload, "retried_after_done_reason_length": True, "initial_done_reason": "length"}
                    content = retry_content or content
                    parsed = retry_parsed
            elapsed = round(time.perf_counter() - started, 3)
            quality = _stage_quality(parsed=parsed, request_payload=request_payload, raw_payload=raw_payload)
            record["response"] = {"status": "ok" if isinstance(parsed, dict) else "invalid_json", "elapsed_seconds": elapsed, "raw_payload": raw_payload, "parsed_response": parsed, "quality": quality}
            self._record_stage(record)
            if not isinstance(parsed, dict):
                repaired = self._repair_stage_json(raw_content=content, stage=stage, config=config)
                if isinstance(repaired, dict):
                    repaired["raw_payload"] = raw_payload
                    repaired["json_repair_applied"] = True
                    repaired["stage"] = stage
                    repaired["stage_model_name"] = body.get("model")
                    repaired["stage_think"] = body.get("think")
                    return repaired
                return {"error_code": "MODEL_INVALID_JSON", "error_text": content[:500], "raw_payload": raw_payload, "stage": stage, "stage_model_name": body.get("model"), "stage_think": body.get("think")}
            parsed["raw_payload"] = raw_payload
            parsed["stage"] = stage
            parsed["stage_model_name"] = body.get("model")
            parsed["stage_think"] = body.get("think")
            return parsed

        def _cached_stage_record(self, *, call_id: str) -> Optional[dict[str, Any]]:
            if self.resume_stage_dir is None:
                return None
            safe = re.sub(r"[^0-9A-Za-z._-]+", "_", str(call_id or "stage_call"))[:180]
            path = self.resume_stage_dir / (safe + ".json")
            if not path.exists():
                return None
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            record["resumed_from_path"] = str(path)
            return record

        def _payload_from_cached_stage(self, *, stage: str, record: dict[str, Any], config: Any) -> Optional[dict[str, Any]]:
            record = self._record_with_current_quality(record)
            response = record.get("response") if isinstance(record.get("response"), dict) else {}
            status = response.get("status")
            self._record_stage(record, cached=True)
            if status == "error":
                error_code = response.get("error_code")
                error_text = response.get("error_text")
                if error_code:
                    return {"error_code": error_code, "error_text": error_text, "stage": stage, "stage_model_name": record.get("model"), "stage_think": record.get("think")}
                return None
            parsed = response.get("parsed_response")
            raw_payload = response.get("raw_payload")
            if not isinstance(parsed, dict):
                content = str(((raw_payload or {}).get("message") or {}).get("content") or "") if isinstance(raw_payload, dict) else ""
                repaired = self._repair_stage_json(raw_content=content, stage=stage, config=config)
                if isinstance(repaired, dict):
                    repaired["raw_payload"] = raw_payload
                    repaired["json_repair_applied"] = True
                    repaired["stage"] = stage
                    repaired["stage_model_name"] = record.get("model")
                    repaired["stage_think"] = record.get("think")
                    return repaired
                return {"error_code": "MODEL_INVALID_JSON", "error_text": content[:500], "raw_payload": raw_payload, "stage": stage, "stage_model_name": record.get("model"), "stage_think": record.get("think")}
            parsed = dict(parsed)
            parsed["raw_payload"] = raw_payload
            parsed["stage"] = stage
            parsed["stage_model_name"] = record.get("model")
            parsed["stage_think"] = record.get("think")
            return parsed

        def _record_with_current_quality(self, record: dict[str, Any]) -> dict[str, Any]:
            response = record.get("response") if isinstance(record.get("response"), dict) else {}
            if response.get("status") not in {"ok", "invalid_json"}:
                return record
            updated = dict(record)
            updated_response = dict(response)
            parsed = updated_response.get("parsed_response")
            raw_payload = updated_response.get("raw_payload") if isinstance(updated_response.get("raw_payload"), dict) else {}
            request_payload = updated.get("request_payload") if isinstance(updated.get("request_payload"), dict) else {}
            updated_response["quality"] = _stage_quality(parsed=parsed, request_payload=request_payload, raw_payload=raw_payload)
            updated_response["status"] = "ok" if isinstance(parsed, dict) else "invalid_json"
            updated["response"] = updated_response
            return updated

        def _record_stage(self, record: dict[str, Any], cached: bool = False) -> None:
            safe = re.sub(r"[^0-9A-Za-z._-]+", "_", str(record.get("call_id") or "stage_call"))[:180]
            path = self.stage_dir / (safe + ".json")
            output_record = dict(record)
            if cached:
                output_record["resumed_from_cache"] = True
            path.write_text(json.dumps(output_record, ensure_ascii=False, indent=2), encoding="utf-8")
            response = record.get("response") or {}
            self.stage_calls.append(
                {
                    "call_id": record.get("call_id"),
                    "path": str(path),
                    "stage": record.get("stage"),
                    "profile": record.get("profile"),
                    "model": record.get("model"),
                    "think": record.get("think"),
                    "format": record.get("format"),
                    "status": response.get("status"),
                    "elapsed_seconds": response.get("elapsed_seconds"),
                    "resumed_from_cache": cached,
                    "done_reason": ((response.get("raw_payload") or {}).get("done_reason") if isinstance(response.get("raw_payload"), dict) else None),
                    "quality": response.get("quality") or {},
                    "error_code": response.get("error_code"),
                    "error_text": response.get("error_text"),
                }
            )

    return ProfiledV3Client


def _request_body_for_stage(*, module: Any, profile: str, stage: str, request_payload: dict[str, Any], system_prompt: str, config: Any, primary_model: str, small_model: str, num_predict: int) -> dict[str, Any]:
    model_name = module.topic_subject_model_for_stage(stage=stage, config=config)
    think = module.topic_subject_thinking_enabled_for_model(model_name)
    use_schema = False
    if profile == "schema_no_think_24b_helpers" and stage in HELPER_STAGES:
        model_name = primary_model
        think = False
        use_schema = True
    elif profile == "schema_no_think_24b_all":
        model_name = primary_model
        think = False
        use_schema = True
    elif profile == "mixed_helpers_1_7b" and stage in HELPER_STAGES:
        model_name = small_model
        think = False
    fmt: Any = "json"
    if use_schema:
        schema_builder = getattr(module, "topic_subject_json_schema_from_prompt_schema", None)
        schema = schema_builder(request_payload.get("schema")) if callable(schema_builder) else _json_schema_from_prompt_schema(request_payload.get("schema"))
        if schema is not None:
            fmt = schema
    return {
        "model": model_name,
        "stream": False,
        "think": think,
        "format": fmt,
        "messages": [
            {"role": "system", "content": module.topic_subject_system_prompt(system_prompt, think=think)},
            {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False)},
        ],
        "options": {"temperature": 0.0, "num_predict": int(num_predict)},
        "keep_alive": "30m",
    }


def _stage_quality(*, parsed: Any, request_payload: dict[str, Any], raw_payload: dict[str, Any]) -> dict[str, Any]:
    errors = []
    warnings = []
    checks = []
    schema = request_payload.get("schema") if isinstance(request_payload, dict) else None
    if not isinstance(parsed, dict):
        errors.append("response_not_parsed_as_json_object")
        return {"status": "error", "checks": checks, "warnings": warnings, "errors": errors}
    checks.append("json_object")
    missing = _missing_schema_fields(parsed, schema)
    if missing:
        # The prompt schema is an output guide, not a strict contract. Some V3 stages
        # intentionally normalize/fill omitted nullable or trace fields after parsing.
        warnings.append("missing_schema_fields:" + ",".join(missing[:12]))
    elif schema:
        checks.append("schema_required_fields_present")
    invalid = _invalid_enum_fields(parsed, schema)
    if invalid:
        errors.append("invalid_enum_values:" + ",".join(invalid[:12]))
    elif schema:
        checks.append("enum_values_valid")
    placeholders = _placeholder_like_fields(parsed=parsed, schema=schema)
    if placeholders:
        errors.append("placeholder_like_values:" + ",".join(placeholders[:12]))
    unsupported_quotes = _unsupported_quote_fields(parsed=parsed, request_payload=request_payload)
    if unsupported_quotes:
        warnings.append("quote_not_found_in_payload:" + ",".join(unsupported_quotes[:12]))
    else:
        checks.append("quote_fields_supported_or_absent")
    if raw_payload.get("done_reason") and raw_payload.get("done_reason") != "stop":
        warnings.append("done_reason:" + str(raw_payload.get("done_reason")))
    return {"status": "error" if errors else "warning" if warnings else "ok", "checks": checks, "warnings": warnings, "errors": errors}


def _prediction_from_event(*, event_dict: Optional[dict[str, Any]], quality_dict: dict[str, Any]) -> dict[str, Any]:
    payload = (event_dict or {}).get("event_payload") or {}
    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    return {
        "is_event": bool(event_dict),
        "action_type_he": payload.get("action_type_he") or quality_dict.get("action_type_by_dicta"),
        "matter_he": payload.get("matter_he") or quality_dict.get("matter_by_dicta"),
        "outcome_is_decision": bool(payload.get("outcome_is_decision")),
        "outcome_type": outcome.get("outcome_type") or "none",
        "validation_status": (event_dict or {}).get("validation_status"),
        "quality_status": quality_dict.get("quality_status"),
        "failure_reasons": (event_dict or {}).get("failure_reasons") or [quality_dict.get("reason_for_failure")],
    }


def _semantic_problems(*, expected: dict[str, Any], prediction: dict[str, Any]) -> list[str]:
    if not expected:
        return []
    problems = []
    if bool(prediction.get("is_event")) != bool(expected.get("is_event")):
        problems.append(f"is_event mismatch expected={expected.get('is_event')} predicted={prediction.get('is_event')}")
    if expected.get("action_types") and prediction.get("action_type_he") not in expected.get("action_types"):
        problems.append(f"action_type mismatch expected_one_of={expected.get('action_types')} predicted={prediction.get('action_type_he')}")
    matter = str(prediction.get("matter_he") or "")
    for keyword in expected.get("matter_keywords") or []:
        if str(keyword) not in matter:
            problems.append(f"matter missing keyword {keyword!r}: {matter}")
    if bool(prediction.get("outcome_is_decision")) != bool(expected.get("outcome_is_decision")):
        problems.append(f"outcome_is_decision mismatch expected={expected.get('outcome_is_decision')} predicted={prediction.get('outcome_is_decision')}")
    if expected.get("outcome_type") and str(prediction.get("outcome_type") or "none") != str(expected.get("outcome_type")):
        problems.append(f"outcome_type mismatch expected={expected.get('outcome_type')} predicted={prediction.get('outcome_type')}")
    if prediction.get("quality_status") in {"model_error", "failed"}:
        problems.append("pipeline quality_status=" + str(prediction.get("quality_status")))
    return problems


def _stage_problems(*, stage_calls: list[dict[str, Any]], prediction: Optional[dict[str, Any]] = None) -> list[str]:
    problems = []
    final_quality = str((prediction or {}).get("quality_status") or "")
    final_usable = final_quality not in {"model_error", "failed"}
    for call in stage_calls:
        optional_recovery_failed_after_usable_prediction = final_usable and call.get("stage") in OPTIONAL_RECOVERY_STAGES
        quality = call.get("quality") or {}
        if call.get("error_code"):
            if optional_recovery_failed_after_usable_prediction:
                continue
            problems.append(f"{call.get('stage')} request_error:{call.get('error_code')}")
        for error in quality.get("errors") or []:
            if optional_recovery_failed_after_usable_prediction:
                continue
            problems.append(f"{call.get('stage')} {error}")
        if call.get("done_reason") and call.get("done_reason") != "stop":
            if optional_recovery_failed_after_usable_prediction:
                continue
            problems.append(f"{call.get('stage')} done_reason={call.get('done_reason')}")
    return problems


def _problem_row(*, row: dict[str, Any], profile: str, prediction: dict[str, Any], expected: dict[str, Any], reason: str, severity: str) -> dict[str, Any]:
    return {
        "severity": severity,
        "row": row["spec"]["key"],
        "profile": profile,
        "full_source_text": row["artifact"].real_text,
        "model_prediction": _prediction_text(prediction),
        "ground_truth_based_on_agent_judgement": expected.get("judgement") or "No manual expectation registered; deterministic stage checks only.",
        "reason_for_failure_or_uncertainty": reason,
    }


def _missing_schema_fields(parsed: Any, schema: Any, prefix: str = "") -> list[str]:
    if not isinstance(parsed, dict) or not isinstance(schema, dict):
        return []
    missing = []
    for key, expected in schema.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if key not in parsed:
            missing.append(path)
            continue
        value = parsed.get(key)
        if isinstance(expected, list) and expected:
            if not isinstance(value, list):
                missing.append(path)
            elif value and isinstance(expected[0], dict) and isinstance(value[0], dict):
                missing.extend(_missing_schema_fields(value[0], expected[0], path + "[]"))
        elif isinstance(expected, dict) and isinstance(value, dict):
            missing.extend(_missing_schema_fields(value, expected, path))
    return missing


def _invalid_enum_fields(parsed: Any, schema: Any, prefix: str = "") -> list[str]:
    if not isinstance(parsed, dict) or not isinstance(schema, dict):
        return []
    invalid = []
    for key, expected in schema.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if key not in parsed:
            continue
        value = parsed.get(key)
        if isinstance(expected, str) and _is_enum_schema_text(expected) and value is not None:
            allowed = [part for part in expected.split("|") if part not in {"null", "string", "boolean"}]
            if allowed and not _enum_value_valid_or_recoverable(path=path, value=value, allowed=allowed):
                invalid.append(path)
        elif isinstance(expected, list) and expected and isinstance(value, list) and value:
            invalid.extend(_invalid_enum_fields(value[0], expected[0], path + "[]"))
        elif isinstance(expected, dict) and isinstance(value, dict):
            invalid.extend(_invalid_enum_fields(value, expected, path))
    return invalid


def _enum_value_valid_or_recoverable(*, path: str, value: Any, allowed: list[str]) -> bool:
    if value in allowed:
        return True
    text = str(value or "").strip()
    if text in allowed:
        return True
    if isinstance(value, dict) and path == "entailment_status":
        return _field_status_dict_recoverable(value)
    if isinstance(value, dict) and path == "prediction_comparison":
        return True
    alias = _enum_alias_for_path(path=path, value=text)
    if alias in allowed:
        return True
    if "|" in text and _path_accepts_composite_enum(path):
        parts = [part.strip() for part in text.split("|") if part.strip()]
        normalized_parts = [_enum_alias_for_path(path=path, value=part) for part in parts if part != "null"]
        if normalized_parts and all(part in allowed for part in normalized_parts):
            return True
    return False


def _path_accepts_composite_enum(path: str) -> bool:
    return path in {
        "target_row_role",
        "event_status",
        "prediction_comparison",
        "row_quality.quality_status",
        "entailment_status",
        "evidence_roles.subject_hint_relation",
    } or path.endswith("span_roles[].span_role") or path.endswith("row_roles[].row_role")


def _enum_alias_for_path(*, path: str, value: str) -> str:
    aliases_by_path = {
        "entailment_status": {
            "partial": "partially_entailed",
            "partially": "partially_entailed",
            "partial_entailed": "partially_entailed",
            "partly_entailed": "partially_entailed",
            "not entailed": "not_entailed",
            "not-entailed": "not_entailed",
        },
        "event_status": {
            "non_event": "not_event",
            "none": "not_event",
            "no_event": "not_event",
            "not an event": "not_event",
            "pending": "open_request",
            "in_discussion": "discussed",
        },
        "prediction_comparison": {
            "partial": "partially_different",
            "partially different": "partially_different",
            "partly_different": "partially_different",
            "model_invalidated": "model_invalid",
            "invalidated": "model_invalid",
            "not_same": "different",
            "invalid": "model_invalid",
            "uncertain": "judge_uncertain",
            "unknown": "judge_uncertain",
        },
        "event_identity_status": {
            "new_event_best_guess": "new_event",
            "best_guess_new_event": "new_event",
            "non_event": "unknown",
            "not_event": "unknown",
            "none": "unknown",
        },
        "outcome_he": {
            "approved": "approved",
            "rejected": "rejected",
            "referred": "referred",
            "removed": "removed",
            "deferred": "deferred",
            "reported": "reported",
            "unknown": "unknown",
            "none": "none",
        },
        "outcome_evidence_classification": {
            "approved": "actual_result",
            "rejected": "actual_result",
            "referred": "actual_result",
            "removed": "actual_result",
            "deferred": "actual_result",
            "reported": "actual_result",
            "decision_result": "actual_result",
            "formal_decision": "actual_result",
            "actual_outcome": "actual_result",
            "request_for_outcome": "proposal_or_intent",
            "proposal": "proposal_or_intent",
            "intent": "proposal_or_intent",
            "modal": "proposal_or_intent",
            "ambiguous": "ambiguous_agreement",
            "unknown": "not_outcome",
            "none": "not_outcome",
        },
        "outcome.outcome_evidence_classification": {
            "approved": "actual_result",
            "rejected": "actual_result",
            "referred": "actual_result",
            "removed": "actual_result",
            "deferred": "actual_result",
            "reported": "actual_result",
            "decision_result": "actual_result",
            "formal_decision": "actual_result",
            "actual_outcome": "actual_result",
            "request_for_outcome": "proposal_or_intent",
            "proposal": "proposal_or_intent",
            "intent": "proposal_or_intent",
            "modal": "proposal_or_intent",
            "ambiguous": "ambiguous_agreement",
            "unknown": "not_outcome",
            "none": "not_outcome",
        },
        "field_assessments.outcome.outcome_evidence_classification": {
            "approved": "actual_result",
            "rejected": "actual_result",
            "referred": "actual_result",
            "removed": "actual_result",
            "deferred": "actual_result",
            "reported": "actual_result",
            "decision_result": "actual_result",
            "formal_decision": "actual_result",
            "actual_outcome": "actual_result",
            "request_for_outcome": "proposal_or_intent",
            "proposal": "proposal_or_intent",
            "intent": "proposal_or_intent",
            "modal": "proposal_or_intent",
            "ambiguous": "ambiguous_agreement",
            "unknown": "not_outcome",
            "none": "not_outcome",
        },
        "row_quality.quality_status": {
            "good": "accepted",
            "ok": "accepted",
            "pass": "accepted",
            "passed": "accepted",
            "valid": "accepted",
            "high": "accepted",
            "high_confidence": "accepted",
            "clear": "accepted",
            "review": "needs_review",
            "warning": "needs_review",
            "medium": "needs_review",
            "low": "needs_review",
            "error": "failed",
            "invalid": "failed",
            "rejected": "failed",
        },
    }
    row_role_aliases = {
        "body": "insufficient_context",
        "current_row": "insufficient_context",
        "target": "insufficient_context",
        "target_row": "insufficient_context",
        "non_event": "insufficient_context",
        "not_event": "insufficient_context",
        "outline": "structural_metadata",
        "outline_item": "structural_metadata",
        "agenda_item": "structural_metadata",
        "heading": "structural_metadata",
        "header": "structural_metadata",
        "title": "event_title",
    }
    span_role_aliases = {
        "subject": "supporting_context",
        "matter": "supporting_context",
        "matter_candidate": "supporting_context",
        "subject_matter": "supporting_context",
        "date": "structural",
        "actual_result": "outcome_candidate",
        "decision_result": "outcome_candidate",
    }
    if path == "target_row_role" or path.endswith("row_roles[].row_role"):
        return row_role_aliases.get(value, value)
    if path.endswith("span_roles[].span_role"):
        return span_role_aliases.get(value, value)
    if path == "outcome_he":
        if any(token in value for token in ("אושרה", "אושר", "אישרה", "אישר", "לאשר", "התקבלה")):
            return "approved"
        if any(token in value for token in ("נדחה", "נדחתה", "לא אושר", "לא אושרה")):
            return "rejected"
        if any(token in value for token in ("יורד", "יורדת", "ירד", "ירדה", "הוסרה")):
            return "removed"
        if any(token in value for token in ("הועברה", "הועבר", "מועברת", "עוברת")):
            return "referred"
    return aliases_by_path.get(path, {}).get(value, value)


def _field_status_dict_recoverable(value: dict[str, Any]) -> bool:
    allowed_field_statuses = {"entailed", "partially_entailed", "not_entailed", "uncertain", "not_applicable"}
    for field_name in ("action_type_he", "matter_he", "outcome"):
        field_payload = value.get(field_name)
        if not isinstance(field_payload, dict):
            continue
        status = str(field_payload.get("status") or "").strip()
        if not status:
            continue
        status = _enum_alias_for_path(path="entailment_status", value=status)
        if status not in allowed_field_statuses:
            return False
    return True


def _placeholder_like_fields(*, parsed: Any, schema: Any, prefix: str = "") -> list[str]:
    if isinstance(parsed, dict):
        fields = []
        schema_dict = schema if isinstance(schema, dict) else {}
        for key, value in parsed.items():
            expected = schema_dict.get(key) if isinstance(schema_dict, dict) else None
            path = f"{prefix}.{key}" if prefix else str(key)
            fields.extend(_placeholder_like_fields(parsed=value, schema=expected, prefix=path))
        return fields
    if isinstance(parsed, list):
        expected = schema[0] if isinstance(schema, list) and schema else None
        fields = []
        for index, value in enumerate(parsed[:5]):
            fields.extend(_placeholder_like_fields(parsed=value, schema=expected, prefix=f"{prefix}[{index}]"))
        return fields
    if isinstance(parsed, str) and _is_placeholder_like_value(parsed, schema):
        return [prefix or "$"]
    return []


def _is_placeholder_like_value(value: str, schema_hint: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    hint = str(schema_hint or "").strip().lower()
    if hint and text == hint and not _is_enum_schema_text(hint):
        return True
    placeholder_fragments = (
        "short exact quote",
        "supplied raw_text",
        "preferably under",
        "short sentence",
        "short overall rationale",
        "full corrected event payload",
        "using the extraction schema",
    )
    return any(fragment in text for fragment in placeholder_fragments)


def _unsupported_quote_fields(*, parsed: Any, request_payload: dict[str, Any]) -> list[str]:
    source_text = _quote_search_text(request_payload)
    if not source_text:
        return []
    source_norm = _quote_search_norm(source_text)
    unsupported = []
    for path, value in _quote_field_values(parsed):
        quote_norm = _quote_search_norm(value)
        if len(quote_norm) >= 12 and quote_norm not in source_norm:
            unsupported.append(path)
    return unsupported


def _quote_field_values(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        fields = []
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            fields.extend(_quote_field_values(item, path))
        return fields
    if isinstance(value, list):
        fields = []
        for index, item in enumerate(value[:10]):
            fields.extend(_quote_field_values(item, f"{prefix}[{index}]"))
        return fields
    if isinstance(value, str) and prefix.endswith("quote_he") and value.strip():
        return [(prefix, value)]
    return []


def _quote_search_text(value: Any) -> str:
    parts: list[str] = []
    source_keys = {
        "raw_text",
        "raw_text_he",
        "full_source_text",
        "full_source_text_he",
        "raw_text_before_cleaning_he",
        "corrected_text_he",
        "text",
    }

    def walk(item: Any, key: str = "") -> None:
        if isinstance(item, dict):
            for child_key, child_value in item.items():
                walk(child_value, str(child_key))
        elif isinstance(item, list):
            for child_value in item:
                walk(child_value, key)
        elif isinstance(item, str) and key in source_keys:
            parts.append(item)

    walk(value)
    return "\n".join(parts)


def _quote_search_norm(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").lower(), flags=re.UNICODE)


def _json_schema_from_prompt_schema(schema: Any) -> Optional[dict[str, Any]]:
    if not isinstance(schema, dict):
        return None
    return {"type": "object", "properties": _json_schema_properties(schema), "required": list(schema.keys()), "additionalProperties": True}


def _json_schema_properties(schema: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_schema_value(value) for key, value in schema.items()}


def _json_schema_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"type": "object", "properties": _json_schema_properties(value), "required": list(value.keys()), "additionalProperties": True}
    if isinstance(value, list):
        item_schema = _json_schema_value(value[0]) if value else {"type": "string"}
        return {"type": "array", "items": item_schema}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)):
        return {"type": "number"}
    if isinstance(value, str):
        parts = value.split("|")
        nullable = "null" in parts
        enum_values = [part for part in parts if part not in {"string", "boolean", "null"}]
        if enum_values and _is_enum_schema_text(value):
            return {"type": ["string", "null"] if nullable else "string", "enum": enum_values + ([None] if nullable else [])}
        if "boolean" in parts:
            return {"type": ["boolean", "null"] if nullable else "boolean"}
        if nullable:
            return {"type": ["string", "null"]}
    return {"type": "string"}


def _is_enum_schema_text(value: str) -> bool:
    parts = [part for part in str(value or "").split("|") if part]
    enum_parts = [part for part in parts if part not in {"null", "string", "boolean"}]
    if len(enum_parts) < 2:
        return False
    # Prompt schemas also use descriptive strings like "exact quote from raw_text|null".
    # Treat only compact symbolic values as enums, not natural-language descriptions.
    symbolic = re.compile(r"^[0-9A-Za-z_\-]+$")
    return all(part in {"null", "string", "boolean"} or bool(symbolic.match(part)) for part in parts)


def _summary_payload(*, mode: str, output_dir: Path, selected_rows: list[dict[str, Any]], profiles: list[str], results: list[dict[str, Any]], problem_rows: list[dict[str, Any]], elapsed_seconds: float, args: argparse.Namespace) -> dict[str, Any]:
    return {
        "status": "completed",
        "mode": mode,
        "selected_row_count": len(selected_rows),
        "profiles": profiles,
        "result_count": len(results),
        "problem_count": len(problem_rows),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "output_dir": str(output_dir),
        "model": str(args.model),
        "small_model": str(args.small_model),
        "selected_rows": selected_rows,
    }


def _write_outputs(*, output_dir: Path, summary: dict[str, Any], results: list[dict[str, Any]], problem_rows: list[dict[str, Any]]) -> None:
    _write_summary(output_dir=output_dir, summary=summary)
    (output_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "quality_report.json").write_text(json.dumps(problem_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "research_events_subjects.json").write_text(json.dumps(_research_events_subjects(results), ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "report.md").write_text(_markdown_report(summary=summary, results=results, problem_rows=problem_rows), encoding="utf-8")


def _write_summary(*, output_dir: Path, summary: dict[str, Any]) -> None:
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def _research_events_subjects(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for result in results:
        event = result.get("event") if isinstance(result.get("event"), dict) else None
        quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
        event_payload = (event or {}).get("event_payload") if isinstance((event or {}).get("event_payload"), dict) else {}
        prediction = result.get("prediction") if isinstance(result.get("prediction"), dict) else {}
        model_prediction = quality.get("model_prediction") if isinstance(quality.get("model_prediction"), dict) else {}
        records.append(
            {
                "record_type": "topic_subject_v3_profile_research_row",
                "row": result.get("row"),
                "profile": result.get("profile"),
                "artifact_id": result.get("artifact_id"),
                "raw_text_he": result.get("full_source_text"),
                "source_provenance": (event or quality).get("source_provenance"),
                "source_document_version_id": (event or quality).get("source_document_version_id"),
                "source_ordinal": (event or quality).get("source_ordinal"),
                "page_span": quality.get("page_span") or (event or {}).get("page_span"),
                "quality_status": prediction.get("quality_status") or quality.get("quality_status"),
                "general_text_metadata": (event or quality).get("general_text_metadata"),
                "event_metadata": (event or quality).get("event_metadata"),
                "subject_metadata": (event or quality).get("subject_metadata"),
                "event_prediction": {
                    "is_event": prediction.get("is_event"),
                    "action_type_he": prediction.get("action_type_he") or event_payload.get("action_type_he") or model_prediction.get("action_type_he"),
                    "matter_he": prediction.get("matter_he") or event_payload.get("matter_he") or model_prediction.get("matter_he"),
                    "outcome_is_decision": prediction.get("outcome_is_decision"),
                    "outcome_type": prediction.get("outcome_type"),
                    "validation_status": prediction.get("validation_status"),
                },
                "event": event,
                "quality": quality,
            }
        )
    return records


def _markdown_report(*, summary: dict[str, Any], results: list[dict[str, Any]], problem_rows: list[dict[str, Any]]) -> str:
    lines = ["# Topic Subject V3 Profile Experiment", "", "## Summary", ""]
    for key in ("mode", "selected_row_count", "profiles", "result_count", "problem_count", "elapsed_seconds", "model", "small_model"):
        lines.append(f"- {key}: `{summary.get(key)}`")
    lines.extend(["", "## Results", "", "| row | profile | seconds | stage calls | prediction | problems |", "|---|---|---:|---:|---|---:|"])
    for result in results:
        lines.append(
            "| {row} | {profile} | {seconds} | {stages} | {prediction} | {problems} |".format(
                row=_md(result.get("row")),
                profile=_md(result.get("profile")),
                seconds=result.get("elapsed_seconds", ""),
                stages=result.get("stage_count", ""),
                prediction=_md(_prediction_text(result.get("prediction") or {})),
                problems=result.get("problem_count", ""),
            )
        )
    lines.extend(["", "## Problem Or Uncertain Rows", "", "| severity | row | profile | full source/text | model prediction | ground truth | reason |", "|---|---|---|---|---|---|---|"])
    if not problem_rows:
        lines.append("| none | | | | | | |")
    for row in problem_rows:
        lines.append(
            "| {severity} | {row} | {profile} | {source} | {prediction} | {truth} | {reason} |".format(
                severity=_md(row.get("severity")),
                row=_md(row.get("row")),
                profile=_md(row.get("profile")),
                source=_md(_short(row.get("full_source_text"), 500)),
                prediction=_md(row.get("model_prediction")),
                truth=_md(row.get("ground_truth_based_on_agent_judgement")),
                reason=_md(row.get("reason_for_failure_or_uncertainty")),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _console_summary(*, results: list[dict[str, Any]], problem_rows: list[dict[str, Any]]) -> str:
    lines = ["profile summary:"]
    for result in results:
        lines.append(
            "- {row} {profile}: seconds={seconds} stages={stages} problems={problems} prediction={prediction}".format(
                row=result.get("row"),
                profile=result.get("profile"),
                seconds=result.get("elapsed_seconds"),
                stages=result.get("stage_count"),
                problems=result.get("problem_count"),
                prediction=_prediction_text(result.get("prediction") or {}),
            )
        )
    lines.append("problem_or_uncertain_rows=" + str(len(problem_rows)))
    return "\n".join(lines)


def _prediction_text(prediction: dict[str, Any]) -> str:
    return "is_event={is_event} action={action} matter={matter} decision={decision} outcome={outcome} quality={quality}".format(
        is_event=prediction.get("is_event"),
        action=prediction.get("action_type_he"),
        matter=prediction.get("matter_he"),
        decision=prediction.get("outcome_is_decision"),
        outcome=prediction.get("outcome_type"),
        quality=prediction.get("quality_status"),
    )


def _output_dir(*, root: Path, live: bool) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root / (stamp + ("_live" if live else "_dry_run"))


def _call_id(*, row: dict[str, Any], profile: str, stage: str, body: dict[str, Any]) -> str:
    return _safe_label(f"{row['spec']['key']}_{profile}_{stage}_{_digest(body)}")


def _stage_call_id(*, stage: str, body: dict[str, Any], profile: str) -> str:
    return _safe_label(f"{profile}_{stage}_{_digest(body)}")


def _digest(value: Any) -> str:
    return hashlib.sha1(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _safe_label(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", str(value))[:180]


def _format_label(value: Any) -> str:
    return "json_schema" if isinstance(value, dict) else str(value)


def _short(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
