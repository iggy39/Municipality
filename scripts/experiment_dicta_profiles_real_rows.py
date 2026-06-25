#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_DIR = (
    PROJECT_ROOT
    / "rag_eval"
    / "runs"
    / "ashdod_pdf_first_v4_shadow"
    / "early_steps_regression_after_visualqa_fix_v1_20260614"
    / "2022_-2-22-_-02-02-22_-_-2-22-_-02-02-22-pdfua__dc91d1b3ac.pdf_a24e8a49fa_5467b6"
    / "step4_v4_global_topic_assignment_dicta_contextual_v53_solar_child_label_sample_20260622"
    / "dicta_call_pairs"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "rag_eval" / "runs" / "dicta_profile_real_row_experiments"
DEFAULT_MODEL = "dicta-il/DictaLM-3.0-24B-Thinking:bf16"
DEFAULT_SMALL_MODEL = "dicta-il/DictaLM-3.0-1.7B-Thinking:latest"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_STAGE_ORDER = (
    "step4_v4_contextual_event_normalization",
    "step4_v4_contextual_root_classification",
    "step4_v4_contextual_event_normalization_repair",
    "step4_v4_contextual_root_classification_repair",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay saved Step4 Dicta prompts on real rows and compare request profiles."
    )
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--fixture", action="append", type=Path, default=[], help="Explicit call-pair JSON file; repeatable.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--small-model", default=DEFAULT_SMALL_MODEL)
    parser.add_argument("--max-fixtures", type=int, default=6)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--live", action="store_true", help="Actually call Ollama. Default is dry-run only.")
    parser.add_argument("--fail-on-problem", action="store_true", help="Exit 2 when deterministic quality checks find errors.")
    parser.add_argument("--profile", action="append", choices=["current_rerun", "guideline_no_think", "guideline_schema_no_think", "repair_small_no_think"], help="Profile to run. Repeatable. Defaults to all profiles that fit each fixture.")
    args = parser.parse_args()

    started = time.perf_counter()
    fixture_paths = _select_fixtures(
        fixture_dir=args.fixture_dir.expanduser().resolve(),
        explicit_paths=[path.expanduser().resolve() for path in args.fixture],
        max_fixtures=max(1, int(args.max_fixtures)),
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_root.expanduser().resolve() / (stamp + ("_live" if args.live else "_dry_run"))
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    problem_rows = []
    for fixture_path in fixture_paths:
        fixture = _load_json(fixture_path)
        fixture_rows, fixture_problem_rows = _process_fixture(
            fixture=fixture,
            fixture_path=fixture_path,
            output_dir=output_dir,
            base_url=str(args.base_url).rstrip("/"),
            model=str(args.model),
            small_model=str(args.small_model),
            timeout_seconds=max(1.0, float(args.timeout_seconds)),
            live=bool(args.live),
            selected_profiles=args.profile or [],
        )
        rows.extend(fixture_rows)
        problem_rows.extend(fixture_problem_rows)

    summary = {
        "status": "completed",
        "mode": "live" if args.live else "dry_run",
        "fixture_count": len(fixture_paths),
        "result_count": len(rows),
        "problem_count": len(problem_rows),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "output_dir": str(output_dir),
        "model": str(args.model),
        "small_model": str(args.small_model),
        "base_url": str(args.base_url).rstrip("/"),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "quality_report.json").write_text(json.dumps(problem_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "report.md").write_text(_markdown_report(summary=summary, rows=rows, problem_rows=problem_rows), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False), flush=True)
    print(_console_table(rows=rows, problem_rows=problem_rows), flush=True)
    if args.fail_on_problem and any(row.get("severity") == "error" for row in problem_rows):
        return 2
    return 0


def _select_fixtures(*, fixture_dir: Path, explicit_paths: list[Path], max_fixtures: int) -> list[Path]:
    if explicit_paths:
        return [path for path in explicit_paths if path.exists()]
    if not fixture_dir.exists():
        raise SystemExit("fixture directory not found: " + str(fixture_dir))

    selected = []
    for stage in DEFAULT_STAGE_ORDER:
        matches = _fixtures_for_exact_stage(fixture_dir=fixture_dir, stage=stage)
        if not matches:
            continue
        preferred = _preferred_fixture(matches)
        if preferred not in selected:
            selected.append(preferred)
        if len(selected) >= max_fixtures:
            return selected[:max_fixtures]

    if len(selected) < max_fixtures:
        for path in sorted(fixture_dir.glob("*.json")):
            if path not in selected:
                selected.append(path)
            if len(selected) >= max_fixtures:
                break
    return selected[:max_fixtures]


def _fixtures_for_exact_stage(*, fixture_dir: Path, stage: str) -> list[Path]:
    matches = []
    for path in sorted(fixture_dir.glob(stage + "*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("step") == stage:
            matches.append(path)
    return matches


def _preferred_fixture(paths: list[Path]) -> Path:
    preferred_tokens = (
        "vh009_02_01_011700a82792",
        "s0003_03_12785d8896a5",
        "s0005_10_7f36a450e52a",
        "vh001_10_01_55646974715f",
    )
    for token in preferred_tokens:
        for path in paths:
            if token in path.name:
                return path
    return paths[0]


def _process_fixture(
    *,
    fixture: dict[str, Any],
    fixture_path: Path,
    output_dir: Path,
    base_url: str,
    model: str,
    small_model: str,
    timeout_seconds: float,
    live: bool,
    selected_profiles: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = []
    problem_rows = []
    fixture_id = str(fixture.get("call_id") or fixture_path.stem)
    step = str(fixture.get("step") or "unknown")
    item_ids = [str(value) for value in fixture.get("item_ids") or []]
    source_text = _source_text(fixture)
    saved_parsed = ((fixture.get("dicta") or {}).get("parsed_response"))
    saved_raw = ((fixture.get("dicta") or {}).get("raw_payload")) or {}
    schema = _schema_for_fixture(fixture)
    saved_quality = _assess_response(
        step=step,
        parsed=saved_parsed,
        raw_payload=saved_raw,
        request_payload=fixture.get("request_payload") or {},
        source_text=source_text,
        schema=schema,
        baseline=None,
    )
    saved_row = {
        "fixture_id": fixture_id,
        "fixture_path": str(fixture_path),
        "step": step,
        "item_ids": item_ids,
        "profile": "saved_current",
        "live": False,
        "model": fixture.get("model"),
        "think": ((fixture.get("request_body") or {}).get("think")),
        "format": _format_label((fixture.get("request_body") or {}).get("format")),
        "temperature": (((fixture.get("request_body") or {}).get("options") or {}).get("temperature")),
        "num_predict": (((fixture.get("request_body") or {}).get("options") or {}).get("num_predict")),
        "done_reason": saved_raw.get("done_reason"),
        "elapsed_seconds": _duration_seconds(saved_raw),
        "prompt_eval_count": saved_raw.get("prompt_eval_count"),
        "eval_count": saved_raw.get("eval_count"),
        "quality": saved_quality,
        "summary": _prediction_summary(step=step, parsed=saved_parsed),
    }
    rows.append(saved_row)
    problem_rows.extend(_problem_rows_from_quality(row=saved_row, source_text=source_text, baseline_summary=None))

    profiles = _profiles_for_fixture(
        fixture=fixture,
        selected_profiles=selected_profiles,
        model=model,
        small_model=small_model,
    )
    for profile_name, body in profiles:
        profile_dir = output_dir / "responses" / fixture_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        request_path = profile_dir / (profile_name + "_request.json")
        request_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        if live:
            live_result = _call_ollama(base_url=base_url, body=body, timeout_seconds=timeout_seconds)
            parsed = _parse_json_content(_message_content(live_result.get("raw_payload") or {}))
            raw_payload = live_result.get("raw_payload") or {}
            response_path = profile_dir / (profile_name + "_response.json")
            response_path.write_text(json.dumps(live_result, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            parsed = None
            raw_payload = {}
            live_result = {"status": "planned", "raw_payload": None, "error_code": None, "elapsed_seconds": 0.0}

        quality = _assess_response(
            step=step,
            parsed=parsed,
            raw_payload=raw_payload,
            request_payload=fixture.get("request_payload") or {},
            source_text=source_text,
            schema=schema,
            baseline=saved_parsed,
        ) if live else {"status": "planned", "checks": [], "warnings": [], "errors": []}
        row = {
            "fixture_id": fixture_id,
            "fixture_path": str(fixture_path),
            "step": step,
            "item_ids": item_ids,
            "profile": profile_name,
            "live": live,
            "model": body.get("model"),
            "think": body.get("think"),
            "format": _format_label(body.get("format")),
            "temperature": ((body.get("options") or {}).get("temperature")),
            "num_predict": ((body.get("options") or {}).get("num_predict")),
            "done_reason": raw_payload.get("done_reason"),
            "elapsed_seconds": live_result.get("elapsed_seconds"),
            "prompt_eval_count": raw_payload.get("prompt_eval_count"),
            "eval_count": raw_payload.get("eval_count"),
            "request_path": str(request_path),
            "quality": quality,
            "summary": _prediction_summary(step=step, parsed=parsed),
            "error_code": live_result.get("error_code"),
            "error_text": live_result.get("error_text"),
        }
        rows.append(row)
        problem_rows.extend(_problem_rows_from_quality(row=row, source_text=source_text, baseline_summary=_prediction_summary(step=step, parsed=saved_parsed)))
    return rows, problem_rows


def _profiles_for_fixture(*, fixture: dict[str, Any], selected_profiles: list[str], model: str, small_model: str) -> list[tuple[str, dict[str, Any]]]:
    request_body = fixture.get("request_body") or {}
    step = str(fixture.get("step") or "")
    requested = set(selected_profiles)
    profiles = []
    for name in ("current_rerun", "guideline_no_think", "guideline_schema_no_think", "repair_small_no_think"):
        if requested and name not in requested:
            continue
        if name == "repair_small_no_think" and "repair" not in step:
            continue
        body = copy.deepcopy(request_body)
        if not body:
            continue
        if name == "current_rerun":
            profiles.append((name, body))
            continue
        body["model"] = small_model if name == "repair_small_no_think" else model
        body["think"] = False
        body["messages"] = _force_no_think_messages(body.get("messages") or [])
        body["format"] = "json"
        body.setdefault("options", {})["temperature"] = 0.0
        if name in {"guideline_schema_no_think", "repair_small_no_think"}:
            schema = _json_schema_from_prompt_schema((fixture.get("request_payload") or {}).get("schema"))
            if schema:
                body["format"] = schema
        profiles.append((name, body))
    return profiles


def _force_no_think_messages(messages: list[Any]) -> list[dict[str, Any]]:
    updated = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        item = copy.deepcopy(message)
        if index == 0 and item.get("role") == "system":
            content = str(item.get("content") or "")
            content = re.sub(r"^\s*/no_think\s*\n?", "", content)
            item["content"] = "/no_think\n" + content
        updated.append(item)
    return updated


def _call_ollama(*, base_url: str, body: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    started = time.perf_counter()
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - local Ollama URL is user supplied.
            raw = json.loads(response.read().decode("utf-8"))
        return {"status": "ok", "raw_payload": raw, "elapsed_seconds": round(time.perf_counter() - started, 3)}
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")[:1000]
        except Exception:  # noqa: BLE001
            detail = str(exc)
        return {"status": "error", "error_code": "HTTP_ERROR", "error_text": detail, "raw_payload": None, "elapsed_seconds": round(time.perf_counter() - started, 3)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error_code": "REQUEST_FAILED", "error_text": f"{exc.__class__.__name__}:{exc}", "raw_payload": None, "elapsed_seconds": round(time.perf_counter() - started, 3)}


def _assess_response(
    *,
    step: str,
    parsed: Any,
    raw_payload: dict[str, Any],
    request_payload: dict[str, Any],
    source_text: str,
    schema: Any,
    baseline: Any,
) -> dict[str, Any]:
    checks = []
    warnings = []
    errors = []
    if not isinstance(parsed, dict):
        errors.append("response_not_parsed_as_json_object")
        return {"status": "error", "checks": checks, "warnings": warnings, "errors": errors}
    checks.append("json_object")
    missing = _missing_schema_fields(parsed, schema)
    if missing:
        errors.append("missing_schema_fields:" + ",".join(missing[:12]))
    else:
        checks.append("schema_required_fields_present")

    invalid_enums = _invalid_enum_fields(parsed, schema)
    if invalid_enums:
        errors.append("invalid_enum_values:" + ",".join(invalid_enums[:12]))
    elif schema:
        checks.append("enum_values_valid")

    allowed_roots = set(str(row.get("root_topic_id")) for row in request_payload.get("allowed_root_topics") or [] if isinstance(row, dict))
    invalid_roots = _invalid_roots(parsed=parsed, allowed_roots=allowed_roots)
    if invalid_roots:
        errors.append("invalid_root_topic_id:" + ",".join(invalid_roots[:12]))
    elif allowed_roots:
        checks.append("root_topic_ids_allowed")

    unsupported_quotes = _unsupported_quotes(parsed=parsed, source_text=source_text)
    if unsupported_quotes:
        warnings.append("quote_not_found_in_source:" + " | ".join(unsupported_quotes[:3]))
    else:
        checks.append("quotes_supported_or_not_present")

    if raw_payload.get("done_reason") and raw_payload.get("done_reason") != "stop":
        warnings.append("done_reason:" + str(raw_payload.get("done_reason")))

    changed = _critical_changes(step=step, baseline=baseline, parsed=parsed)
    if changed:
        warnings.append("changed_from_saved_current:" + ",".join(changed[:12]))

    status = "error" if errors else "warning" if warnings else "ok"
    return {"status": status, "checks": checks, "warnings": warnings, "errors": errors}


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
        if isinstance(expected, str) and "|" in expected and value is not None:
            allowed = [part for part in expected.split("|") if part not in {"null", "string", "boolean"}]
            if allowed and str(value) not in allowed:
                invalid.append(path)
        elif isinstance(expected, list) and expected and isinstance(value, list) and value:
            invalid.extend(_invalid_enum_fields(value[0], expected[0], path + "[]"))
        elif isinstance(expected, dict) and isinstance(value, dict):
            invalid.extend(_invalid_enum_fields(value, expected, path))
    return invalid


def _invalid_roots(*, parsed: Any, allowed_roots: set[str]) -> list[str]:
    if not allowed_roots:
        return []
    roots = []
    for row in _assignment_rows(parsed):
        root = row.get("root_topic_id")
        if root and str(root) not in allowed_roots:
            roots.append(str(root))
    return roots


def _unsupported_quotes(*, parsed: Any, source_text: str) -> list[str]:
    text_norm = _compact(source_text)
    if not text_norm:
        return []
    unsupported = []
    for key, value in _walk_strings(parsed):
        if "quote" not in key and "evidence" not in key:
            continue
        quote = _compact(value)
        if len(quote) < 8:
            continue
        if quote not in text_norm:
            unsupported.append(value[:180])
    return unsupported


def _critical_changes(*, step: str, baseline: Any, parsed: Any) -> list[str]:
    if not isinstance(baseline, dict) or not isinstance(parsed, dict):
        return []
    keys = ["root_topic_id", "is_topic_bearing", "indexability_status", "event_status", "agenda_disposition", "standalone_event"]
    changes = []
    base_rows = _assignment_rows(baseline) or [baseline]
    new_rows = _assignment_rows(parsed) or [parsed]
    for index, base_row in enumerate(base_rows):
        new_row = new_rows[index] if index < len(new_rows) else {}
        for key in keys:
            if base_row.get(key) != new_row.get(key):
                changes.append(f"{key}:{base_row.get(key)}->{new_row.get(key)}")
    return changes


def _problem_rows_from_quality(*, row: dict[str, Any], source_text: str, baseline_summary: Optional[str]) -> list[dict[str, Any]]:
    quality = row.get("quality") or {}
    if quality.get("status") in {"ok", "planned"}:
        return []
    reasons = []
    reasons.extend(quality.get("errors") or [])
    reasons.extend(quality.get("warnings") or [])
    return [
        {
            "severity": "error" if quality.get("errors") else "warning",
            "fixture_id": row.get("fixture_id"),
            "step": row.get("step"),
            "profile": row.get("profile"),
            "full_source_text": source_text,
            "model_prediction": row.get("summary"),
            "ground_truth_based_on_agent_judgement": baseline_summary or "Deterministic check only: valid JSON, required schema fields, allowed root ids, and quotes must be supported by source text.",
            "reason_for_failure_or_uncertainty": "; ".join(reasons),
        }
    ]


def _prediction_summary(*, step: str, parsed: Any) -> str:
    if not isinstance(parsed, dict):
        return "no parsed JSON"
    if "assignments" in parsed:
        parts = []
        for row in parsed.get("assignments") or []:
            if isinstance(row, dict):
                parts.append(
                    "unit={unit} root={root} topic={topic} bearing={bearing} status={status}".format(
                        unit=row.get("structure_unit_id"),
                        root=row.get("root_topic_id"),
                        topic=row.get("topic_subject_he") or row.get("clean_subject_he"),
                        bearing=row.get("is_topic_bearing"),
                        status=row.get("indexability_status") or row.get("event_status"),
                    )
                )
        return " | ".join(parts)[:1000]
    return "unit={unit} subject={subject} action={action} indexability={indexability} event_status={status}".format(
        unit=parsed.get("structure_unit_id"),
        subject=parsed.get("semantic_subject_he") or parsed.get("clean_subject_he") or parsed.get("event_summary_he"),
        action=parsed.get("municipal_action_he") or parsed.get("primary_action_he"),
        indexability=parsed.get("indexability_status"),
        status=parsed.get("event_status") or parsed.get("agenda_disposition"),
    )[:1000]


def _schema_for_fixture(fixture: dict[str, Any]) -> Any:
    return (fixture.get("request_payload") or {}).get("schema")


def _json_schema_from_prompt_schema(schema: Any) -> Optional[dict[str, Any]]:
    if not isinstance(schema, dict):
        return None
    return {
        "type": "object",
        "properties": _json_schema_properties(schema),
        "required": list(schema.keys()),
        "additionalProperties": True,
    }


def _json_schema_properties(schema: dict[str, Any]) -> dict[str, Any]:
    props = {}
    for key, value in schema.items():
        props[str(key)] = _json_schema_value(value)
    return props


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
        if enum_values:
            schema = {"type": ["string", "null"] if nullable else "string", "enum": enum_values + ([None] if nullable else [])}
            return schema
        if "boolean" in parts:
            return {"type": ["boolean", "null"] if nullable else "boolean"}
        if nullable:
            return {"type": ["string", "null"]}
    return {"type": "string"}


def _source_text(fixture: dict[str, Any]) -> str:
    payload = fixture.get("request_payload") or {}
    texts = []
    context = payload.get("context") or {}
    for section in ("target", "nearby_rows", "same_window_rows", "same_section_rows"):
        value = context.get(section)
        if isinstance(value, dict):
            texts.extend(_row_text_values(value))
        elif isinstance(value, list):
            for row in value:
                if isinstance(row, dict):
                    texts.extend(_row_text_values(row))
    for item in payload.get("items") or []:
        if isinstance(item, dict):
            texts.extend(_row_text_values(item))
    normalized_event = payload.get("normalized_event") or {}
    if isinstance(normalized_event, dict):
        texts.extend(_row_text_values(normalized_event))
    return " ".join(text for text in texts if text)


def _row_text_values(row: dict[str, Any]) -> list[str]:
    keys = (
        "raw_text_sample",
        "raw_text",
        "topic_identification_context",
        "topic_headline_he",
        "topic_subject_he",
        "agenda_item_title_he",
        "event_summary_he",
        "supporting_quote_he",
        "topic_supporting_quote_he",
        "evidence_quote_he",
    )
    return [str(row.get(key) or "") for key in keys if str(row.get(key) or "").strip()]


def _assignment_rows(parsed: Any) -> list[dict[str, Any]]:
    if isinstance(parsed, dict) and isinstance(parsed.get("assignments"), list):
        return [row for row in parsed.get("assignments") or [] if isinstance(row, dict)]
    return []


def _walk_strings(value: Any, key: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(key, value)]
    if isinstance(value, dict):
        result = []
        for child_key, child_value in value.items():
            result.extend(_walk_strings(child_value, str(child_key)))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_walk_strings(item, key))
        return result
    return []


def _message_content(raw_payload: dict[str, Any]) -> str:
    return str(((raw_payload.get("message") or {}).get("content")) or "")


def _parse_json_content(content: str) -> Any:
    text = str(content or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None


def _duration_seconds(raw_payload: dict[str, Any]) -> Optional[float]:
    value = raw_payload.get("total_duration")
    if isinstance(value, (int, float)):
        return round(float(value) / 1_000_000_000.0, 3)
    return None


def _format_label(value: Any) -> str:
    if isinstance(value, dict):
        return "json_schema"
    return str(value)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _markdown_report(*, summary: dict[str, Any], rows: list[dict[str, Any]], problem_rows: list[dict[str, Any]]) -> str:
    lines = ["# Dicta Profile Real-Row Experiment", "", "## Summary", ""]
    for key in ("mode", "fixture_count", "result_count", "problem_count", "elapsed_seconds", "model", "small_model"):
        lines.append(f"- {key}: `{summary.get(key)}`")
    lines.extend(["", "## Results", "", "| fixture | step | profile | model | think | format | seconds | status | prediction |", "|---|---|---|---|---:|---|---:|---|---|"])
    for row in rows:
        quality = row.get("quality") or {}
        lines.append(
            "| {fixture} | {step} | {profile} | {model} | {think} | {format} | {seconds} | {status} | {prediction} |".format(
                fixture=_md(row.get("fixture_id")),
                step=_md(row.get("step")),
                profile=_md(row.get("profile")),
                model=_md(row.get("model")),
                think=row.get("think"),
                format=_md(row.get("format")),
                seconds=row.get("elapsed_seconds"),
                status=_md(quality.get("status")),
                prediction=_md(row.get("summary")),
            )
        )
    lines.extend(["", "## Problem Or Uncertain Rows", "", "| severity | step | profile | full source/text | model prediction | ground truth | reason |", "|---|---|---|---|---|---|---|"])
    if not problem_rows:
        lines.append("| none | | | | | | |")
    for row in problem_rows:
        lines.append(
            "| {severity} | {step} | {profile} | {source} | {prediction} | {truth} | {reason} |".format(
                severity=_md(row.get("severity")),
                step=_md(row.get("step")),
                profile=_md(row.get("profile")),
                source=_md(_short(row.get("full_source_text"), 500)),
                prediction=_md(row.get("model_prediction")),
                truth=_md(row.get("ground_truth_based_on_agent_judgement")),
                reason=_md(row.get("reason_for_failure_or_uncertainty")),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _console_table(*, rows: list[dict[str, Any]], problem_rows: list[dict[str, Any]]) -> str:
    lines = ["profile summary:"]
    for row in rows:
        quality = row.get("quality") or {}
        lines.append(
            "- {profile}: {step} status={status} think={think} format={format} seconds={seconds}".format(
                profile=row.get("profile"),
                step=row.get("step"),
                status=quality.get("status"),
                think=row.get("think"),
                format=row.get("format"),
                seconds=row.get("elapsed_seconds"),
            )
        )
    lines.append("problem_or_uncertain_rows=" + str(len(problem_rows)))
    return "\n".join(lines)


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _short(value: Any, limit: int) -> str:
    text = _compact(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
