from __future__ import annotations

import os
import re
from pathlib import Path
from typing import MutableMapping


ENV_FILE_ORDER = (".env", ".env.local")
ENV_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")

_RUNTIME_ENV_LOADED = False


def load_runtime_env(
    *,
    environ: MutableMapping[str, str] | None = None,
    package_file: Path | None = None,
    force: bool = False,
) -> dict[str, str]:
    """Load .env files into process environment without overriding existing values."""

    global _RUNTIME_ENV_LOADED

    using_process_env = environ is None and package_file is None and not force
    if using_process_env and _RUNTIME_ENV_LOADED:
        return {}

    target_env = environ if environ is not None else os.environ
    root = _project_root(package_file=package_file)
    loaded: dict[str, str] = {}

    for env_file_name in ENV_FILE_ORDER:
        env_path = root / env_file_name
        if not env_path.exists() or not env_path.is_file():
            continue
        loaded.update(_load_env_file(env_path=env_path, environ=target_env))

    if using_process_env:
        _RUNTIME_ENV_LOADED = True
    return loaded


def _project_root(*, package_file: Path | None) -> Path:
    file_path = package_file or Path(__file__).resolve()
    if len(file_path.parents) >= 3:
        return file_path.parents[2]
    return file_path.parent


def _load_env_file(*, env_path: Path, environ: MutableMapping[str, str]) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(raw_line)
        if parsed is None:
            continue

        key, value = parsed
        existing = environ.get(key)
        if isinstance(existing, str) and existing.strip():
            continue

        environ[key] = value
        loaded[key] = value

    return loaded


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    match = ENV_ASSIGN_RE.match(line)
    if match is None:
        return None

    key = match.group(1)
    raw_value = match.group(2).strip()
    if not raw_value:
        return key, ""

    if raw_value.startswith('"') and raw_value.endswith('"') and len(raw_value) >= 2:
        value = raw_value[1:-1]
        value = value.replace("\\n", "\n").replace("\\t", "\t")
        return key, value

    if raw_value.startswith("'") and raw_value.endswith("'") and len(raw_value) >= 2:
        return key, raw_value[1:-1]

    if "#" in raw_value:
        raw_value = raw_value.split("#", 1)[0].rstrip()

    return key, raw_value
