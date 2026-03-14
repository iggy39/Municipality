from __future__ import annotations

from pathlib import Path

from municipality.env_loader import _parse_env_line, load_runtime_env


def test_load_runtime_env_reads_env_files_without_overriding_existing_values(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    package_file = project_root / "src" / "municipality" / "__init__.py"
    package_file.parent.mkdir(parents=True, exist_ok=True)

    (project_root / ".env").write_text(
        "BYTEZ_API_KEY=from_env\nRAG_LLM_PROVIDER=bytez\nSHARED_KEY=base\n",
        encoding="utf-8",
    )
    (project_root / ".env.local").write_text(
        "SHARED_KEY=local\nRAG_LLM_MODEL=google/gemini-2.5-pro\n",
        encoding="utf-8",
    )

    env: dict[str, str] = {"SHARED_KEY": "already-set"}
    loaded = load_runtime_env(environ=env, package_file=package_file, force=True)

    assert env["BYTEZ_API_KEY"] == "from_env"
    assert env["RAG_LLM_PROVIDER"] == "bytez"
    assert env["RAG_LLM_MODEL"] == "google/gemini-2.5-pro"
    assert env["SHARED_KEY"] == "already-set"
    assert loaded["BYTEZ_API_KEY"] == "from_env"
    assert loaded["RAG_LLM_MODEL"] == "google/gemini-2.5-pro"
    assert "SHARED_KEY" not in loaded


def test_parse_env_line_supports_quotes_and_comments() -> None:
    assert _parse_env_line("BYTEZ_API_KEY=abc123") == ("BYTEZ_API_KEY", "abc123")
    assert _parse_env_line('RAG_PROMPT_PREFIX_ANSWER="hello\\nworld"') == (
        "RAG_PROMPT_PREFIX_ANSWER",
        "hello\nworld",
    )
    assert _parse_env_line("RAG_LLM_PROVIDER=bytez # comment") == ("RAG_LLM_PROVIDER", "bytez")
    assert _parse_env_line("# comment only") is None
