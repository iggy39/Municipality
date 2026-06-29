from __future__ import annotations

from municipality.codex_agent_harness import (
    build_commit_safety_report,
    build_preflight_checklist,
    format_quality_report,
    sort_examples_hardest_first,
)


def test_preflight_adds_ui_and_database_verification_from_paths() -> None:
    checks = build_preflight_checklist(
        task_text="Fix dashboard layout",
        changed_paths=["src/municipality/rag_dashboard_ui.py", "migrations/025_example.sql"],
    )

    check_ids = {row["id"] for row in checks}

    assert "generic_solution" in check_ids
    assert "playwright_verification" in check_ids
    assert "database_server_restart" in check_ids
    assert "commit_safety" in check_ids


def test_sort_examples_hardest_first_uses_metadata_not_input_order() -> None:
    sorted_examples = sort_examples_hardest_first(
        [
            {"id": "easy", "raw_text": "simple", "difficulty": "easy"},
            {"id": "hard", "raw_text": "hard", "difficulty": "hard", "edge_cases": ["ocr", "ambiguous"]},
            {"id": "medium", "raw_text": "medium", "difficulty": "medium", "confidence": 0.6},
        ]
    )

    assert [example.example_id for example in sorted_examples] == ["hard", "medium", "easy"]


def test_quality_report_includes_raw_text_and_filters_successful_rows() -> None:
    report = format_quality_report(
        [
            {"id": "ok", "raw_text": "raw success text", "prediction": "A", "expected": "A", "succeeded": True, "confidence": 0.95},
            {"id": "bad", "raw_text": "full raw failing text", "prediction": "A", "expected": "B", "succeeded": False, "reason": "wrong label"},
        ]
    )

    assert "full raw failing text" in report
    assert "wrong label" in report
    assert "raw success text" not in report


def test_commit_safety_blocks_runtime_outputs_and_possible_secrets() -> None:
    report = build_commit_safety_report(
        [
            "/Users/igor/Desktop/projects/Municipality/src/municipality/api.py",
            "/Users/igor/Desktop/projects/Municipality/.uvicorn-rag-dashboard-8001.log",
            "/Users/igor/Desktop/projects/Municipality/municipality.db.before-test",
            "/Users/igor/Desktop/projects/Municipality/.env",
        ]
    )

    safe_paths = {row["path"] for row in report["safe_to_commit"]}
    blocked_categories = {row["category"] for row in report["needs_review_or_exclusion"]}

    assert "/Users/igor/Desktop/projects/Municipality/src/municipality/api.py" in safe_paths
    assert {"runtime_artifact", "database_backup", "possible_secret"}.issubset(blocked_categories)
