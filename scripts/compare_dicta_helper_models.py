from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


MODEL_17B = "dicta-il/DictaLM-3.0-1.7B-Thinking:latest"
MODEL_24B = "dicta-il/DictaLM-3.0-24B-Thinking:bf16"


def _model_thinking_enabled(model: str) -> bool:
    model_id = str(model or "").casefold()
    if "1.7b" in model_id or "1_7b" in model_id:
        return False
    return "thinking" in model_id and ("24b" in model_id or "122b" in model_id)


def _model_system_prompt(*, model: str, prompt: str) -> str:
    text = str(prompt or "")
    if _model_thinking_enabled(model):
        return re.sub(r"^\s*/no_think\s*\n?", "", text)
    if re.match(r"^\s*/no_think\b", text):
        return text
    return f"/no_think\n{text}"


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


def _task_specs() -> list[dict[str, Any]]:
    return [
        {
            "task_id": "schema_repair",
            "title": "Schema Repair",
            "instructions": [
                "For each case, inspect original_response as a possible contextual classification result.",
                "Return whether it can be used directly without a repair call, and whether a repair call is needed.",
                "Do not reclassify the municipal topic; judge only schema usability for the stated expected fields.",
            ],
            "schema": {
                "results": [
                    {
                        "case_id": "string",
                        "usable_without_repair": "boolean",
                        "repair_needed": "boolean",
                        "root_topic_id": "string|null",
                        "is_topic_bearing": "boolean|null",
                        "topic_subject_he": "string|null",
                    }
                ]
            },
            "cases": [
                {
                    "case_id": "schema_direct_unwrapped",
                    "source_text": "Direct single assignment JSON without assignments wrapper",
                    "original_response": {
                        "structure_unit_id": "u1",
                        "root_topic_id": "root_budget_finance",
                        "is_topic_bearing": True,
                        "topic_subject_he": "תקציב הגיל הרך",
                        "confidence": 0.95,
                    },
                    "expected": {
                        "usable_without_repair": True,
                        "repair_needed": False,
                        "root_topic_id": "root_budget_finance",
                        "is_topic_bearing": True,
                        "topic_subject_he": "תקציב הגיל הרך",
                    },
                },
                {
                    "case_id": "schema_missing_root",
                    "source_text": "Topic-bearing assignment missing root_topic_id",
                    "original_response": {
                        "assignments": [
                            {
                                "structure_unit_id": "u2",
                                "is_topic_bearing": True,
                                "topic_subject_he": "הסכם רשות לעמותה",
                                "confidence": 0.91,
                            }
                        ]
                    },
                    "expected": {
                        "usable_without_repair": False,
                        "repair_needed": True,
                        "root_topic_id": None,
                        "is_topic_bearing": True,
                        "topic_subject_he": "הסכם רשות לעמותה",
                    },
                },
                {
                    "case_id": "schema_non_topic_root_null",
                    "source_text": "Non-topic procedural row with null root",
                    "original_response": {
                        "assignments": [
                            {
                                "structure_unit_id": "u3",
                                "root_topic_id": None,
                                "is_topic_bearing": False,
                                "topic_subject_he": None,
                                "confidence": 0.88,
                            }
                        ]
                    },
                    "expected": {
                        "usable_without_repair": True,
                        "repair_needed": False,
                        "root_topic_id": None,
                        "is_topic_bearing": False,
                        "topic_subject_he": None,
                    },
                },
            ],
        },
        {
            "task_id": "indexability_judgment",
            "title": "Indexability Judgment",
            "instructions": [
                "For each Hebrew municipal protocol row, decide if it is a standalone topic or only a procedural/evidence fragment.",
                "Strip agenda carriers such as שאילתה or פרוטוקול ועדה when a bounded municipal subject remains.",
                "Return only supported facts; use null for unsupported clean_subject_he.",
            ],
            "schema": {
                "results": [
                    {
                        "case_id": "string",
                        "is_topic_bearing": "boolean",
                        "indexability_status": "standalone_topic|duplicate_reference|evidence_fragment|procedural_only|insufficient_context|unknown",
                        "clean_subject_he": "string|null",
                    }
                ]
            },
            "cases": [
                {
                    "case_id": "generic_committee_carrier",
                    "source_text": "פרוטוקול הוועדה המקצועית מספר",
                    "expected": {"is_topic_bearing": False, "indexability_status": "procedural_only", "clean_subject_he": None},
                },
                {
                    "case_id": "named_welfare_committee",
                    "source_text": "פרוטוקול מישיבת ועדת רווחה",
                    "expected": {"is_topic_bearing": True, "indexability_status": "standalone_topic", "clean_subject_he": "ועדת רווחה"},
                },
                {
                    "case_id": "removed_agreement",
                    "source_text": "הסכם עם החברה העירונית לתיירות הוסר מסדר היום",
                    "expected": {"is_topic_bearing": True, "indexability_status": "standalone_topic", "clean_subject_he": "הסכם עם חברה עירונית לתיירות"},
                },
                {
                    "case_id": "query_dangerous_buildings",
                    "source_text": "שאילתה בנושא בדיקת מבנים מסוכנים ברחבי העיר",
                    "expected": {"is_topic_bearing": True, "indexability_status": "standalone_topic", "clean_subject_he": "בדיקת מבנים מסוכנים ברחבי העיר"},
                },
                {
                    "case_id": "aggregate_support_table_fragment",
                    "source_text": "סה\"כ תמיכה שנתית",
                    "expected": {"is_topic_bearing": False, "indexability_status": "insufficient_context", "clean_subject_he": None},
                },
            ],
        },
        {
            "task_id": "subject_cleanup",
            "title": "Subject Cleanup",
            "instructions": [
                "For each row, return a short reusable Hebrew topic subject.",
                "Remove carriers, years, dates, plan numbers, speakers, and unsupported location metadata.",
                "Return null when the source is only procedural/dialogue/noisy and not a reusable municipal subject.",
            ],
            "schema": {"results": [{"case_id": "string", "clean_subject_he": "string|null"}]},
            "cases": [
                {
                    "case_id": "procurement_without_tender",
                    "source_text": "ניהול משא ומתן עם ספקים פוטנציאליים לצורך התקשרות ללא מכרז, לאור אי קבלת הצעות",
                    "expected": {"clean_subject_he": "ניהול משא ומתן להתקשרות ללא מכרז"},
                },
                {
                    "case_id": "urban_renewal_plan",
                    "source_text": "פרוייקט פינוי בינוי ברחוב הרב מימון, תכנית 0778050, רובע ב, אשדוד",
                    "expected": {"clean_subject_he": "פינוי בינוי הרב מימון"},
                },
                {
                    "case_id": "apartment_sale_publication",
                    "source_text": "פרסום אסור של מכירת דירות למגזר הציבור החרדי מחסידות בעלזא",
                    "expected": {"clean_subject_he": "פרסום מכירת דירות"},
                },
                {
                    "case_id": "protocol_typo_correction",
                    "source_text": "תיקון טעות קולמוס בפרוטוקול מועצה",
                    "expected": {"clean_subject_he": None},
                },
                {
                    "case_id": "weather_forecast_employment_cost",
                    "source_text": "עלות העסקת חב' חיזוי מזג האויר",
                    "expected": {"clean_subject_he": "עלות העסקת חברת חיזוי מזג האוויר"},
                },
            ],
        },
        {
            "task_id": "closed_root_selection",
            "title": "Closed Root Selection",
            "instructions": [
                "For each normalized event, select only one root_topic_id from allowed_roots, or null when the event is not topic-bearing.",
                "Classify by the municipal action and semantic subject, not by carrier wording.",
                "Organization names alone are insufficient unless an action or support/allocation/contract context is present.",
            ],
            "schema": {"results": [{"case_id": "string", "root_topic_id": "string|null", "is_topic_bearing": "boolean"}]},
            "allowed_roots": [
                {"root_topic_id": "root_agreements", "root_label_he": "הסכמים והתקשרויות"},
                {"root_topic_id": "root_planning_building", "root_label_he": "תכנון ובנייה"},
                {"root_topic_id": "root_supports", "root_label_he": "תמיכות"},
                {"root_topic_id": "root_infrastructure_environment", "root_label_he": "תשתיות וסביבה"},
                {"root_topic_id": "root_culture_sport", "root_label_he": "תרבות וספורט"},
            ],
            "cases": [
                {
                    "case_id": "root_procurement_without_tender",
                    "normalized_event": {"primary_action_he": "ניהול משא ומתן להתקשרות ללא מכרז", "clean_subject_he": "ניהול משא ומתן להתקשרות ללא מכרז", "indexability_status": "standalone_topic"},
                    "expected": {"root_topic_id": "root_agreements", "is_topic_bearing": True},
                },
                {
                    "case_id": "root_urban_renewal",
                    "normalized_event": {"primary_action_he": "פרויקט פינוי בינוי", "clean_subject_he": "פינוי בינוי הרב מימון", "service_domain_he": "תכנון ובנייה", "indexability_status": "standalone_topic"},
                    "expected": {"root_topic_id": "root_planning_building", "is_topic_bearing": True},
                },
                {
                    "case_id": "root_sport_support_criteria",
                    "normalized_event": {"primary_action_he": "עדכון תבחיני תמיכה", "clean_subject_he": "עדכון תבחיני ספורט", "service_domain_he": "ספורט", "indexability_status": "standalone_topic"},
                    "expected": {"root_topic_id": "root_supports", "is_topic_bearing": True},
                },
                {
                    "case_id": "root_electronic_signage",
                    "normalized_event": {"primary_action_he": "הצבת לוחות פרסום אלקטרוניים", "clean_subject_he": "לוחות פרסום אלקטרוניים", "service_domain_he": "מרחב ציבורי", "indexability_status": "standalone_topic"},
                    "expected": {"root_topic_id": "root_infrastructure_environment", "is_topic_bearing": True},
                },
                {
                    "case_id": "root_org_name_alone",
                    "normalized_event": {"primary_action_he": None, "clean_subject_he": "עמותת כדורעף AOV", "service_domain_he": "ספורט", "indexability_status": "insufficient_context"},
                    "expected": {"root_topic_id": None, "is_topic_bearing": False},
                },
            ],
        },
    ]


def _call_model(*, model: str, base_url: str, payload: dict[str, Any], timeout_seconds: float, num_predict: int) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    body = {
        "model": model,
        "stream": False,
        "think": _model_thinking_enabled(model),
        "format": "json",
        "messages": [
            {
                "role": "system",
                "content": _model_system_prompt(model=model, prompt="/no_think\nYou are a careful Hebrew municipal protocol helper. Return JSON only."),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "options": {"temperature": 0.0, "num_predict": num_predict},
        "keep_alive": "30m",
    }
    started = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=10.0, read=timeout_seconds, write=30.0, pool=10.0)) as client:
            response = client.post(f"{base_url}/api/chat", json=body)
            response.raise_for_status()
            raw = response.json()
    except Exception as exc:  # noqa: BLE001
        return None, {"status": "error", "error": f"{exc.__class__.__name__}:{exc}", "elapsed_seconds": round(time.monotonic() - started, 2), "body": body}
    parsed = _parse_json_content(str(((raw.get("message") or {}).get("content")) or ""))
    if not isinstance(parsed, dict):
        return None, {"status": "invalid_json", "raw": raw, "elapsed_seconds": round(time.monotonic() - started, 2), "body": body}
    return parsed, {"status": "ok", "raw": raw, "elapsed_seconds": round(time.monotonic() - started, 2), "body": body}


def _results_by_case(parsed: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(parsed, dict):
        return {}
    rows = parsed.get("results")
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and str(row.get("case_id") or ""):
            out[str(row["case_id"])] = row
    return out


def _normalized(value: Any) -> Any:
    if isinstance(value, str):
        text = " ".join(value.split()).strip()
        return text or None
    return value


def _evaluate_case(*, result: dict[str, Any] | None, expected: dict[str, Any]) -> tuple[bool, list[str]]:
    if result is None:
        return False, ["missing_result"]
    errors: list[str] = []
    for key, expected_value in expected.items():
        actual_value = result.get(key)
        if _normalized(actual_value) != _normalized(expected_value):
            errors.append(f"{key}: expected={expected_value!r} actual={actual_value!r}")
    return not errors, errors


def _run_comparison(args: argparse.Namespace) -> dict[str, Any]:
    models = [MODEL_24B, MODEL_17B]
    tasks = _task_specs()
    calls: list[dict[str, Any]] = []
    case_rows: list[dict[str, Any]] = []
    for task in tasks:
        payload = {
            "task": task["task_id"],
            "instructions": task["instructions"],
            "schema": task["schema"],
            "allowed_roots": task.get("allowed_roots"),
            "cases": [{key: value for key, value in case.items() if key != "expected"} for case in task["cases"]],
        }
        for model in models:
            label = "24B-thinking" if model == MODEL_24B else "1.7B-no-think"
            print(f"Running {task['title']} with {label}...")
            parsed, call = _call_model(model=model, base_url=args.ollama_base_url, payload=payload, timeout_seconds=args.timeout_seconds, num_predict=args.num_predict)
            call.update({"task_id": task["task_id"], "task_title": task["title"], "model": model, "model_label": label, "parsed": parsed})
            calls.append(call)
            by_case = _results_by_case(parsed)
            correct = 0
            for case in task["cases"]:
                result = by_case.get(case["case_id"])
                ok, errors = _evaluate_case(result=result, expected=case["expected"])
                correct += int(ok)
                case_rows.append(
                    {
                        "task_id": task["task_id"],
                        "task_title": task["title"],
                        "case_id": case["case_id"],
                        "source_text": case.get("source_text") or json.dumps(case.get("normalized_event") or case.get("original_response"), ensure_ascii=False),
                        "model": model,
                        "model_label": label,
                        "prediction": result,
                        "expected": case["expected"],
                        "ok": ok,
                        "errors": errors,
                    }
                )
            print(f"  {label}: {correct}/{len(task['cases'])} exact matches in {call.get('elapsed_seconds')}s")
    return {"created_at": datetime.now().isoformat(timespec="seconds"), "calls": calls, "case_rows": case_rows}


def _write_report(*, payload: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comparison_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = payload["case_rows"]
    by_model: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_model.setdefault(row["model_label"], []).append(row)
    lines = ["# Dicta Helper Model Comparison", "", f"Created: {payload['created_at']}", "", "## Summary", "", "| Model | Exact matches | Cases |", "|---|---:|---:|"]
    for label, model_rows in by_model.items():
        lines.append(f"| {label} | {sum(1 for row in model_rows if row['ok'])} | {len(model_rows)} |")
    lines.extend(["", "## Failures", "", "| Task | Full source/text | Model | Model prediction | Ground truth | Reason |", "|---|---|---|---|---|---|"])
    failures = [row for row in rows if not row["ok"]]
    for row in failures:
        prediction = json.dumps(row["prediction"], ensure_ascii=False, sort_keys=True) if row["prediction"] is not None else "null"
        expected = json.dumps(row["expected"], ensure_ascii=False, sort_keys=True)
        reason = "; ".join(row["errors"])
        lines.append(f"| {row['task_title']} | {row['source_text']} | {row['model_label']} | `{prediction}` | `{expected}` | {reason} |")
    if not failures:
        lines.append("| None | All cases matched | - | - | - | - |")
    lines.extend(["", "## Per-Case Results", "", "| Task | Case | 24B-thinking | 1.7B-no-think |", "|---|---|---|---|"])
    case_ids = []
    for row in rows:
        key = (row["task_title"], row["case_id"])
        if key not in case_ids:
            case_ids.append(key)
    for task_title, case_id in case_ids:
        matching = [row for row in rows if row["task_title"] == task_title and row["case_id"] == case_id]
        status = {row["model_label"]: "pass" if row["ok"] else "fail" for row in matching}
        lines.append(f"| {task_title} | {case_id} | {status.get('24B-thinking', 'missing')} | {status.get('1.7B-no-think', 'missing')} |")
    (output_dir / "comparison_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare local Dicta helper-task behavior between 24B thinking and 1.7B no-think.")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--num-predict", type=int, default=2200)
    parser.add_argument("--output-dir", type=Path, default=Path("rag_eval/runs/dicta_helper_model_comparison_20260621"))
    args = parser.parse_args()
    payload = _run_comparison(args)
    _write_report(payload=payload, output_dir=args.output_dir)
    print(f"Wrote {args.output_dir / 'comparison_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
