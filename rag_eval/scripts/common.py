from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
RAG_EVAL_ROOT = PROJECT_ROOT / "rag_eval"
WHITESPACE_RE = re.compile(r"\s+")

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            compact = line.strip()
            if not compact:
                continue
            try:
                payload = json.loads(compact)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"expected JSON object at {path}:{line_number}")
            rows.append(payload)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def normalize_text_for_match(value: str) -> str:
    return WHITESPACE_RE.sub(" ", value).strip()


def quote_match_status(*, quote: str, text: str) -> str:
    if not quote.strip():
        return "empty"
    if quote in text:
        return "exact"
    normalized_quote = normalize_text_for_match(quote)
    normalized_text = normalize_text_for_match(text)
    if normalized_quote and normalized_quote in normalized_text:
        return "normalized"
    return "failed"


def quote_matches(*, quote: str, text: str) -> bool:
    return quote_match_status(quote=quote, text=text) in {"exact", "normalized"}


def parse_json_payload(value: str | None) -> Any:
    if value is None:
        return None
    compact = value.strip()
    if not compact:
        return None
    candidates = [compact]
    stripped = _strip_markdown_fence(compact)
    if stripped != compact:
        candidates.append(stripped)
    for candidate in candidates:
        parsed = _loads_json(candidate)
        if parsed is not None:
            return parsed
        sliced = _extract_json_slice(candidate)
        if sliced:
            parsed = _loads_json(sliced)
            if parsed is not None:
                return parsed
    return None


def ollama_chat_json(
    *,
    model: str,
    messages: list[dict[str, str]],
    base_url: str = "http://localhost:11434",
    timeout_seconds: float = 180.0,
    temperature: float = 0.0,
) -> Any:
    content = ollama_chat_text(
        model=model,
        messages=messages,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        temperature=temperature,
        json_mode=True,
    )
    parsed = parse_json_payload(content)
    if parsed is None:
        raise ValueError(f"Ollama model returned non-JSON content: {content[:240]}")
    return parsed


def ollama_chat_text(
    *,
    model: str,
    messages: list[dict[str, str]],
    base_url: str = "http://localhost:11434",
    timeout_seconds: float = 180.0,
    temperature: float = 0.0,
    json_mode: bool = False,
) -> str:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if json_mode:
        body["format"] = "json"
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(f"{base_url.rstrip('/')}/api/chat", json=body)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Ollama returned a non-object response")
    message = payload.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"].strip()
    if isinstance(payload.get("response"), str):
        return payload["response"].strip()
    raise ValueError("Ollama response did not include message.content")


def sanitize_model_name(value: str) -> str:
    compact = value.strip()
    if not compact:
        return "model"
    return re.sub(r"[^A-Za-z0-9._-]+", "__", compact).strip("_") or "model"


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, min_value: float = 0.0, max_value: float = 1.0) -> float:
    return max(min_value, min(max_value, value))


def _strip_markdown_fence(value: str) -> str:
    stripped = value.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_slice(value: str) -> str | None:
    object_start = value.find("{")
    object_end = value.rfind("}")
    array_start = value.find("[")
    array_end = value.rfind("]")
    object_slice = value[object_start : object_end + 1].strip() if object_start >= 0 and object_end > object_start else ""
    array_slice = value[array_start : array_end + 1].strip() if array_start >= 0 and array_end > array_start else ""
    if object_slice and array_slice:
        return object_slice if object_start < array_start else array_slice
    return object_slice or array_slice or None


def _loads_json(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None
