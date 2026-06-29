from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


DIFFICULTY_SCORES = {
    "hard": 30,
    "difficult": 30,
    "medium": 20,
    "normal": 20,
    "easy": 10,
}

RUNTIME_ARTIFACT_NAMES = {".DS_Store", ".last-run.json"}
RUNTIME_ARTIFACT_SUFFIXES = {".log", ".pid", ".pyc", ".tmp"}
VISUAL_ARTIFACT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
DOCUMENT_ARTIFACT_SUFFIXES = {".pdf", ".numbers"}


@dataclass(frozen=True)
class HarnessExample:
    """A single example that a Codex agent should validate explicitly."""

    example_id: str
    raw_text: str
    difficulty: str = "medium"
    municipality: str = ""
    document_type: str = ""
    edge_cases: tuple[str, ...] = ()
    model_prediction: str = ""
    ground_truth: str = ""
    reason: str = ""
    confidence: float | None = None
    succeeded: bool | None = None


def build_preflight_checklist(
    *,
    task_text: str,
    changed_paths: Iterable[str] = (),
    ui_work: bool | None = None,
    database_change: bool | None = None,
    reruns_predictions: bool | None = None,
    document_pipeline: bool | None = None,
) -> list[dict[str, str]]:
    """Build a rule checklist that agents can show before implementation.

    The checklist is intentionally generic. It looks at task shape and file types,
    not municipal wording or document-specific semantics.
    """

    paths = [str(path) for path in changed_paths]
    task = task_text.lower()
    inferred_ui = _bool_or_infer(ui_work, _looks_like_ui_work(task=task, paths=paths))
    inferred_database = _bool_or_infer(database_change, _looks_like_database_change(task=task, paths=paths))
    inferred_prediction = _bool_or_infer(reruns_predictions, any(word in task for word in ("prediction", "predict", "model", "rerun")))
    inferred_pipeline = _bool_or_infer(document_pipeline, any(word in task for word in ("pipeline", "document", "documents", "extraction")))

    checks = [
        _check("generic_solution", "Challenge the first non-generic idea and choose the broadest reusable approach."),
        _check("no_fixed_semantic_keywords", "Do not rely on semantic-specific keywords or fixed wording without explicit user permission."),
        _check("representative_testing", "Choose the broadest available examples across municipalities, document types, and edge cases."),
        _check("sequential_examples", "Sort validation examples hardest-to-easiest and run each only after the previous one succeeds."),
        _check("raw_text_evidence", "When showing cases or failures, include full raw source text with predictions and judgement."),
    ]
    if inferred_pipeline:
        checks.append(_check("pipeline_quality_report", "After processing examples, show a concise table of problematic or low-confidence predictions."))
    if inferred_prediction:
        checks.append(_check("prediction_summary", "After rerunning predictions, summarize the main remaining problems and generic fixes."))
    if inferred_ui:
        checks.append(_check("playwright_verification", "Restart the affected server, verify the port, and check the desired UI result with global Playwright."))
    if inferred_database:
        checks.append(_check("database_server_restart", "After database changes, restart affected local servers and verify the correct port."))
    checks.append(_check("commit_safety", "Before committing, exclude logs, database backups, local screenshots, and possible secrets."))
    return checks


def sort_examples_hardest_first(examples: Iterable[Mapping[str, Any] | HarnessExample]) -> list[HarnessExample]:
    """Return examples in the order Codex should validate them."""

    normalized = [_to_example(example) for example in examples]
    return sorted(normalized, key=lambda example: (-_difficulty_score(example), example.example_id))


def format_quality_report(rows: Iterable[Mapping[str, Any] | HarnessExample], *, max_rows: int = 20) -> str:
    """Format problematic or low-confidence rows as a raw-text Markdown table."""

    examples = [_to_example(row) for row in rows]
    problematic = [example for example in examples if _is_problematic(example)]
    visible = problematic[: max(0, int(max_rows))]
    header = "| Full source/text | Model prediction | Ground truth | Reason for failure or uncertainty |"
    separator = "|---|---|---|---|"
    if not visible:
        return "\n".join([header, separator, "| No problematic or low-confidence predictions found. |  |  |  |"])
    lines = [header, separator]
    for example in visible:
        lines.append(
            "| {source} | {prediction} | {truth} | {reason} |".format(
                source=_table_cell(example.raw_text),
                prediction=_table_cell(example.model_prediction),
                truth=_table_cell(example.ground_truth),
                reason=_table_cell(example.reason or _default_reason(example)),
            )
        )
    return "\n".join(lines)


def classify_commit_path(path: str | Path) -> dict[str, Any]:
    """Classify a changed path before a Codex-triggered commit."""

    text = str(path)
    name = Path(text).name
    suffix = Path(text).suffix.lower()
    parts = set(Path(text).parts)
    lowered = text.lower()

    if name in RUNTIME_ARTIFACT_NAMES or suffix in RUNTIME_ARTIFACT_SUFFIXES or "/.uvicorn-" in lowered:
        return _path_classification(text, "runtime_artifact", False, "local runtime output")
    if ".db.before" in lowered or name.endswith(".db") or name.endswith(".sqlite"):
        return _path_classification(text, "database_backup", False, "database or database backup")
    if "test-results" in parts and suffix in VISUAL_ARTIFACT_SUFFIXES.union({".json"}):
        return _path_classification(text, "local_test_artifact", False, "local Playwright/test artifact")
    if suffix in DOCUMENT_ARTIFACT_SUFFIXES and "docs" in parts:
        return _path_classification(text, "review_document_artifact", False, "binary or office document should be explicitly reviewed before commit")
    if _looks_like_secret_file(text):
        return _path_classification(text, "possible_secret", False, "possible secret-bearing file")
    return _path_classification(text, "commit_candidate", True, "normal source, test, config, or documentation path")


def build_commit_safety_report(paths: Iterable[str | Path]) -> dict[str, Any]:
    classifications = [classify_commit_path(path) for path in paths]
    return {
        "safe_to_commit": [row for row in classifications if row["safe_to_commit"]],
        "needs_review_or_exclusion": [row for row in classifications if not row["safe_to_commit"]],
    }


def preflight_markdown(checks: Iterable[Mapping[str, str]]) -> str:
    lines = ["| Check | Required action |", "|---|---|"]
    for check in checks:
        lines.append(f"| {_table_cell(check.get('id', ''))} | {_table_cell(check.get('required_action', ''))} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex agent rule harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="Build a task preflight checklist")
    preflight.add_argument("--task", required=True)
    preflight.add_argument("--changed-path", action="append", default=[])
    preflight.add_argument("--json", action="store_true")

    safety = subparsers.add_parser("commit-safety", help="Classify paths before committing")
    safety.add_argument("paths", nargs="+")

    report = subparsers.add_parser("quality-report", help="Print a problematic-row quality report")
    report.add_argument("--rows-json", required=True, help="JSON list of row objects")

    args = parser.parse_args(argv)
    if args.command == "preflight":
        checks = build_preflight_checklist(task_text=args.task, changed_paths=args.changed_path)
        print(json.dumps(checks, ensure_ascii=False, indent=2) if args.json else preflight_markdown(checks))
        return 0
    if args.command == "commit-safety":
        print(json.dumps(build_commit_safety_report(args.paths), ensure_ascii=False, indent=2))
        return 0
    rows = json.loads(Path(args.rows_json).expanduser().resolve().read_text(encoding="utf-8"))
    print(format_quality_report(rows))
    return 0


def _check(check_id: str, required_action: str) -> dict[str, str]:
    return {"id": check_id, "required_action": required_action}


def _bool_or_infer(value: bool | None, inferred: bool) -> bool:
    return inferred if value is None else bool(value)


def _looks_like_ui_work(*, task: str, paths: list[str]) -> bool:
    ui_suffixes = {".html", ".css", ".tsx", ".jsx"}
    if any(Path(path).suffix.lower() in ui_suffixes for path in paths):
        return True
    return "ui" in task or "dashboard" in task or "playwright" in task


def _looks_like_database_change(*, task: str, paths: list[str]) -> bool:
    if any(Path(path).suffix.lower() == ".sql" or "migrations" in Path(path).parts for path in paths):
        return True
    return "database" in task or "db" in task or "migration" in task


def _to_example(value: Mapping[str, Any] | HarnessExample) -> HarnessExample:
    if isinstance(value, HarnessExample):
        return value
    edge_cases = value.get("edge_cases") or value.get("hard_reasons") or []
    if isinstance(edge_cases, str):
        edge_cases = [edge_cases]
    return HarnessExample(
        example_id=str(value.get("example_id") or value.get("case_id") or value.get("id") or ""),
        raw_text=str(value.get("raw_text") or value.get("source_text") or value.get("full_source_text") or ""),
        difficulty=str(value.get("difficulty") or "medium"),
        municipality=str(value.get("municipality") or value.get("city") or ""),
        document_type=str(value.get("document_type") or value.get("source_type") or ""),
        edge_cases=tuple(str(item) for item in edge_cases),
        model_prediction=str(value.get("model_prediction") or value.get("prediction") or ""),
        ground_truth=str(value.get("ground_truth") or value.get("expected") or ""),
        reason=str(value.get("reason") or value.get("failure_reason") or value.get("judge_notes") or ""),
        confidence=_float_or_none(value.get("confidence")),
        succeeded=_bool_or_none(value.get("succeeded", value.get("success"))),
    )


def _difficulty_score(example: HarnessExample) -> int:
    score = DIFFICULTY_SCORES.get(example.difficulty.strip().lower(), 20)
    score += min(20, len(example.edge_cases) * 5)
    if example.confidence is not None and example.confidence < 0.7:
        score += 10
    if example.succeeded is False:
        score += 8
    if example.municipality:
        score += 1
    if example.document_type:
        score += 1
    return score


def _is_problematic(example: HarnessExample) -> bool:
    if example.succeeded is False:
        return True
    if example.confidence is not None and example.confidence < 0.75:
        return True
    if example.reason:
        return True
    return False


def _default_reason(example: HarnessExample) -> str:
    if example.confidence is not None and example.confidence < 0.75:
        return f"low confidence: {example.confidence:g}"
    if example.succeeded is False:
        return "failed validation"
    return "uncertain"


def _table_cell(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text.replace("|", "\\|")


def _path_classification(path: str, category: str, safe_to_commit: bool, reason: str) -> dict[str, Any]:
    return {"path": path, "category": category, "safe_to_commit": safe_to_commit, "reason": reason}


def _looks_like_secret_file(path: str) -> bool:
    name = Path(path).name.lower()
    if name in {".env", "local.env"}:
        return True
    if name.endswith(".env") and not name.endswith(".example"):
        return True
    return bool(re.search(r"(secret|token|credential|private[_-]?key)", path, flags=re.IGNORECASE))


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "pass", "passed"}:
        return True
    if text in {"false", "0", "no", "fail", "failed"}:
        return False
    return None


if __name__ == "__main__":
    raise SystemExit(main())
