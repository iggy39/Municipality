from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx


QWEN_OCR_PROVIDER_OLLAMA = "ollama"
QWEN_OCR_PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
DEFAULT_QWEN_OCR_MODEL = "qwen3.5:122b"
DEFAULT_QWEN_OCR_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_QWEN_OCR_OPENAI_BASE_URL = "http://localhost:1234/v1"
DEFAULT_QWEN_OCR_TIMEOUT_SECONDS = 600.0
DEFAULT_QWEN_OCR_DPI = 300
DEFAULT_QWEN_OCR_IMAGE_FORMAT = "png"
DEFAULT_QWEN_OCR_PROMPT_MODE = "json-thinking"

THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


@dataclass(slots=True)
class QwenOcrPageResult:
    page_number: int
    text: str
    plain_text: str | None = None
    markdown_layout: str | None = None
    tables_markdown: list[str] = field(default_factory=list)
    detected_headings: list[str] = field(default_factory=list)
    uncertain_regions: list[dict[str, Any]] = field(default_factory=list)
    quality_notes: str | None = None
    ocr_confidence: float | None = None
    raw_response: str | None = None
    parsed_payload: dict[str, Any] | None = None
    parse_warning: str | None = None
    error_code: str | None = None
    error_text: str | None = None


class QwenVisionOcrClient:
    def __init__(
        self,
        *,
        provider: str | None = None,
        model_name: str | None = None,
        ollama_base_url: str | None = None,
        openai_base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.provider = _normalize_provider(provider or os.getenv("QWEN_OCR_PROVIDER"))
        self.model_name = (model_name or os.getenv("QWEN_OCR_MODEL") or DEFAULT_QWEN_OCR_MODEL).strip()
        self.ollama_base_url = (
            ollama_base_url
            or os.getenv("QWEN_OCR_OLLAMA_BASE_URL")
            or os.getenv("OLLAMA_BASE_URL")
            or DEFAULT_QWEN_OCR_OLLAMA_BASE_URL
        ).rstrip("/")
        self.openai_base_url = (
            openai_base_url or os.getenv("QWEN_OCR_OPENAI_BASE_URL") or DEFAULT_QWEN_OCR_OPENAI_BASE_URL
        ).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("QWEN_OCR_API_KEY")
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else _env_float(
            os.getenv("QWEN_OCR_TIMEOUT_SECONDS"),
            default=DEFAULT_QWEN_OCR_TIMEOUT_SECONDS,
        )
        self.transport = transport

    def is_configured(self) -> bool:
        if not self.model_name:
            return False
        if self.provider == QWEN_OCR_PROVIDER_OPENAI_COMPATIBLE:
            return bool(self.openai_base_url)
        return bool(self.ollama_base_url)

    def ocr_page(
        self,
        *,
        image_bytes: bytes,
        page_number: int,
        image_mime: str,
    ) -> QwenOcrPageResult:
        if not self.is_configured():
            return QwenOcrPageResult(
                page_number=page_number,
                text="",
                error_code="QWEN_OCR_NOT_CONFIGURED",
                error_text="QWEN_OCR_MODEL and OCR endpoint must be configured",
            )
        if not image_bytes:
            return QwenOcrPageResult(
                page_number=page_number,
                text="",
                error_code="EMPTY_IMAGE",
                error_text="rendered page image is empty",
            )

        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        prompt = build_qwen_ocr_prompt(page_number=page_number)
        if self.provider == QWEN_OCR_PROVIDER_OPENAI_COMPATIBLE:
            return self._ocr_page_openai_compatible(
                image_b64=image_b64,
                image_mime=image_mime,
                page_number=page_number,
                prompt=prompt,
            )
        return self._ocr_page_ollama(image_b64=image_b64, page_number=page_number, prompt=prompt)

    def _ocr_page_ollama(self, *, image_b64: str, page_number: int, prompt: str) -> QwenOcrPageResult:
        body = {
            "model": self.model_name,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }
            ],
            "options": {"temperature": 0},
        }
        if _qwen_ocr_prompt_mode() != "markdown-no-think":
            body["format"] = "json"
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = client.post(f"{self.ollama_base_url}/api/chat", json=body)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001
            return QwenOcrPageResult(
                page_number=page_number,
                text="",
                error_code="QWEN_OCR_REQUEST_FAILED",
                error_text=f"{exc.__class__.__name__}:{exc}",
            )

        content = _extract_ollama_content(payload if isinstance(payload, dict) else {})
        return parse_qwen_ocr_response(content, page_number=page_number)

    def _ocr_page_openai_compatible(
        self,
        *,
        image_b64: str,
        image_mime: str,
        page_number: int,
        prompt: str,
    ) -> QwenOcrPageResult:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model_name,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{image_mime};base64,{image_b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
        if _qwen_ocr_prompt_mode() != "markdown-no-think":
            body["response_format"] = {"type": "json_object"}
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = client.post(
                    f"{self.openai_base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001
            return QwenOcrPageResult(
                page_number=page_number,
                text="",
                error_code="QWEN_OCR_REQUEST_FAILED",
                error_text=f"{exc.__class__.__name__}:{exc}",
            )

        content = _extract_openai_content(payload if isinstance(payload, dict) else {})
        return parse_qwen_ocr_response(content, page_number=page_number)


def build_qwen_ocr_prompt(*, page_number: int) -> str:
    prompt_mode = _qwen_ocr_prompt_mode()
    if prompt_mode == "markdown-no-think":
        return f"""/no_think
You are a meticulous OCR engine for Hebrew municipal PDF pages.

Extract all visible text from page {page_number} exactly as it appears.

Return clean Markdown only.

Rules:
- Preserve the natural reading order. Do not jump between columns.
- Preserve Hebrew text order. Do not reverse Hebrew words or lines.
- Preserve all visible text, including headers, footers, stamps, signatures, handwritten text, dates, and page numbers.
- Use Markdown headings/lists where visible.
- Use HTML tables for tables when table structure is visible.
- For non-text visual objects, write placeholders such as [Figure: description] or [Chart: description].
- Mark unreadable text explicitly as [לא קריא].
- Do not summarize, classify, explain, or add commentary.
- Do not return JSON, markdown fences, prose before/after the transcription, or <think> blocks.
""".strip()

    thinking_directive = "/no_think" if prompt_mode == "json-no-think" else "/think"
    thinking_rule = "Do not use thinking mode; return the final JSON directly." if prompt_mode == "json-no-think" else "Use thinking mode internally to inspect the page, but the final answer must be JSON only."
    return f"""{thinking_directive}
You are a meticulous OCR engine for Hebrew municipal PDF pages.
{thinking_rule}

OCR page {page_number} exactly as it appears.

Return only this JSON object:
{{
  "page_number": {page_number},
  "plain_text": "full transcription in natural reading order",
  "markdown_layout": "full transcription preserving meaningful line breaks, headings, lists, and tables",
  "tables_markdown": ["each detected table as markdown"],
  "detected_headings": ["visible headings only"],
  "uncertain_regions": [{{"text": "raw uncertain text or [לא קריא]", "reason": "why uncertain"}}],
  "quality_notes": "short OCR quality notes",
  "ocr_confidence": 0.0
}}

Rules:
- Extract all visible text. Preserve the natural reading order. Do not jump between columns.
- Preserve Hebrew text order. Do not reverse Hebrew words or lines.
- Preserve all visible text, including headers, footers, stamps, signatures, handwritten text, dates, and page numbers.
- Preserve tables using Markdown where possible. If a table is complex, preserve it using HTML in markdown_layout.
- If there are charts or figures, use descriptive placeholders like [Chart: description] or [Figure: description].
- Do not summarize, classify, explain, or add commentary.
- Mark unreadable text explicitly as [לא קריא].
- If a field has no content, return an empty string, empty array, or null as appropriate.
- The final response must not include markdown fences, prose, or <think> blocks.
""".strip()


def _qwen_ocr_prompt_mode() -> str:
    value = (os.getenv("QWEN_OCR_PROMPT_MODE") or DEFAULT_QWEN_OCR_PROMPT_MODE).strip().casefold()
    if value in {"json-no-think", "markdown-no-think"}:
        return value
    return DEFAULT_QWEN_OCR_PROMPT_MODE


def parse_qwen_ocr_response(content: str | None, *, page_number: int) -> QwenOcrPageResult:
    if not content or not str(content).strip():
        return QwenOcrPageResult(
            page_number=page_number,
            text="",
            error_code="QWEN_OCR_EMPTY_RESPONSE",
            error_text="missing OCR response content",
        )

    cleaned = _clean_model_content(str(content))
    payload = _parse_json_object(cleaned)
    if not isinstance(payload, dict):
        fallback_text = cleaned.strip()
        return QwenOcrPageResult(
            page_number=page_number,
            text=fallback_text,
            raw_response=content,
            parse_warning="INVALID_JSON_FALLBACK_TO_TEXT",
        )

    plain_text = _as_optional_str(payload.get("plain_text"))
    markdown_layout = _as_optional_str(payload.get("markdown_layout"))
    tables_markdown = _as_str_list(payload.get("tables_markdown") or payload.get("tables"))
    detected_headings = _as_str_list(payload.get("detected_headings") or payload.get("headings"))
    uncertain_regions = _as_dict_list(payload.get("uncertain_regions"))
    quality_notes = _as_optional_str(payload.get("quality_notes"))
    ocr_confidence = _as_float(payload.get("ocr_confidence"))
    page_text = _best_page_text(
        plain_text=plain_text,
        markdown_layout=markdown_layout,
        tables_markdown=tables_markdown,
    )

    return QwenOcrPageResult(
        page_number=page_number,
        text=page_text,
        plain_text=plain_text,
        markdown_layout=markdown_layout,
        tables_markdown=tables_markdown,
        detected_headings=detected_headings,
        uncertain_regions=uncertain_regions,
        quality_notes=quality_notes,
        ocr_confidence=ocr_confidence,
        raw_response=content,
        parsed_payload=payload,
    )


def _best_page_text(
    *,
    plain_text: str | None,
    markdown_layout: str | None,
    tables_markdown: list[str],
) -> str:
    primary = (markdown_layout or plain_text or "").strip()
    if not tables_markdown:
        return primary
    missing_tables = [
        table
        for table in tables_markdown
        if table.strip() and table.strip() not in primary
    ]
    if not missing_tables:
        return primary
    table_block = "\n\n".join(missing_tables).strip()
    if not primary:
        return table_block
    return f"{primary}\n\nטבלאות מזוהות:\n{table_block}".strip()


def _clean_model_content(value: str) -> str:
    without_think = THINK_BLOCK_RE.sub("", value).strip()
    return FENCE_RE.sub("", without_think).strip()


def _parse_json_object(value: str) -> dict[str, Any] | None:
    compact = value.strip()
    if not compact:
        return None
    try:
        parsed = json.loads(compact)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    object_text = _extract_first_json_object(compact)
    if object_text is None:
        return None
    try:
        parsed = json.loads(object_text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_first_json_object(value: str) -> str | None:
    start = value.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(value)):
        char = value[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return value[start : index + 1]
    return None


def _extract_ollama_content(payload: dict[str, Any]) -> str | None:
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    response = payload.get("response")
    if isinstance(response, str) and response.strip():
        return response.strip()
    return None


def _extract_openai_content(payload: dict[str, Any]) -> str | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text_value = item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                parts.append(text_value.strip())
        if parts:
            return "\n".join(parts)
    return None


def _normalize_provider(value: str | None) -> str:
    normalized = (value or QWEN_OCR_PROVIDER_OLLAMA).strip().casefold().replace("-", "_")
    if normalized in {"openai", "openai_compatible", "lmstudio", "lm_studio"}:
        return QWEN_OCR_PROVIDER_OPENAI_COMPATIBLE
    return QWEN_OCR_PROVIDER_OLLAMA


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    compact = str(value).strip()
    return compact or None


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _env_float(value: str | None, *, default: float) -> float:
    if value is None:
        return default
    try:
        return max(1.0, float(value))
    except ValueError:
        return default
