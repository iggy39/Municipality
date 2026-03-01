from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx


BYTEZ_PROVIDER = "Bytez"
BYTEZ_MODEL = "google/gemini-2.5-pro"
DEFAULT_BYTEZ_API_URL = "https://api.bytez.com/v1/chat/completions"
DECISION_PROMPT_PREFIX = "find decisions in next hebrew text"


class BytezFallbackClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ):
        self.api_key = api_key or os.getenv("BYTEZ_API_KEY")
        self.endpoint = endpoint or os.getenv("BYTEZ_API_URL", DEFAULT_BYTEZ_API_URL)
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.transport = transport

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def extract_decisions(self, *, source_text: str, text_offset: int = 0) -> list[dict[str, Any]] | None:
        if not self.api_key:
            return None

        prefixed_text = f"{DECISION_PROMPT_PREFIX}\n{source_text.strip()}"

        request_payload = {
            "model": BYTEZ_MODEL,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{DECISION_PROMPT_PREFIX}. "
                        "Extract ALL operative municipal decisions from Hebrew protocol text. "
                        "Do not return only the final summary. Include separate items for each explicit approval or resolution sentence, "
                        "including phrases like מאשרים and הוחלט. "
                        "If one paragraph contains multiple approvals, include them as separate decisions. "
                        "Ignore narrative background updates approved by departments (for example phrases like אושרה ע\"י אגף). "
                        "Return strict JSON with top-level key decisions (array). "
                        "Each item must contain: decision_text, decision_number, agenda_item, "
                        "confidence, start_offset, end_offset, vote. "
                        "The vote key must be either null or an object with keys for_count, "
                        "against_count, abstain_count, unanimous."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "instruction_prefix": DECISION_PROMPT_PREFIX,
                            "prefixed_text": prefixed_text,
                            "source_text": source_text,
                            "rules": [
                                "Use only evidence that appears in source_text",
                                "start_offset and end_offset are offsets within source_text",
                                "decision_text must be an exact substring copied from source_text",
                                "Extract all distinct approvals and resolutions, not just one",
                                "Skip background administrative approvals by departments",
                                "If a value is unknown, return null",
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }

        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                    response = client.post(
                        self.endpoint,
                        headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                        json=request_payload,
                    )
                    response.raise_for_status()
                    payload = response.json()
            except Exception:
                if attempt >= self.max_retries:
                    return None
                time.sleep(0.4 * (attempt + 1))
                continue

            content = _extract_message_content(payload)
            if content is None:
                if attempt >= self.max_retries:
                    return None
                time.sleep(0.3 * (attempt + 1))
                continue

            parsed = _parse_model_content(content)
            if parsed is None:
                if attempt >= self.max_retries:
                    return None
                time.sleep(0.3 * (attempt + 1))
                continue

            items = _extract_decision_items(parsed)
            if items is None:
                if attempt >= self.max_retries:
                    return None
                time.sleep(0.3 * (attempt + 1))
                continue

            normalized_out: list[dict[str, Any]] = []
            for item in items:
                try:
                    normalized = _normalize_decision_payload(item, text_offset=text_offset)
                except Exception:
                    continue
                if normalized is not None:
                    normalized_out.append(normalized)

            if normalized_out:
                return normalized_out
            if attempt >= self.max_retries:
                return None
            time.sleep(0.3 * (attempt + 1))

        return None

    def extract_decision(self, *, row_text: str, source_window: str) -> dict[str, Any] | None:
        decisions = self.extract_decisions(source_text=source_window, text_offset=0)
        if not decisions:
            return None
        return decisions[0]


def _extract_message_content(payload: dict[str, Any]) -> Any | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    return message.get("content")


def _parse_model_content(content: Any) -> Any | None:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None
    if isinstance(content, (dict, list)):
        return content
    return None


def _extract_decision_items(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        decisions = payload.get("decisions")
        if isinstance(decisions, list):
            rows = decisions
        elif "decision_text" in payload:
            rows = [payload]
        else:
            return None
    else:
        return None

    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
    return out


def _normalize_decision_payload(payload: dict[str, Any], *, text_offset: int) -> dict[str, Any] | None:
    decision_text = payload.get("decision_text")
    if not isinstance(decision_text, str) or not decision_text.strip():
        return None

    vote_value = payload.get("vote")
    vote_out: dict[str, Any] | None
    if isinstance(vote_value, dict):
        vote_out = {
            "for_count": _as_optional_int(vote_value.get("for_count")),
            "against_count": _as_optional_int(vote_value.get("against_count")),
            "abstain_count": _as_optional_int(vote_value.get("abstain_count")),
            "unanimous": _as_optional_bool(vote_value.get("unanimous")),
        }
    else:
        vote_out = None

    start_offset = _as_optional_int(payload.get("start_offset"))
    if start_offset is None:
        start_offset = _as_optional_int(payload.get("span_start"))
    end_offset = _as_optional_int(payload.get("end_offset"))
    if end_offset is None:
        end_offset = _as_optional_int(payload.get("span_end"))
    if start_offset is not None:
        start_offset += text_offset
    if end_offset is not None:
        end_offset += text_offset

    return {
        "decision_text": decision_text.strip(),
        "decision_number": _as_optional_str(payload.get("decision_number")),
        "agenda_item": _as_optional_str(payload.get("agenda_item")),
        "confidence": _as_optional_float(payload.get("confidence")),
        "start_offset": start_offset,
        "end_offset": end_offset,
        "vote": vote_out,
    }


def _as_optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        compact = value.strip()
        return compact or None
    return None


def _as_optional_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        compact = value.strip()
        if compact.isdigit():
            return int(compact)
    return None


def _as_optional_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        compact = value.strip()
        try:
            return float(compact)
        except ValueError:
            return None
    return None


def _as_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        compact = value.strip().casefold()
        if compact in {"true", "1", "yes"}:
            return True
        if compact in {"false", "0", "no"}:
            return False
    return None
