from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.fallback import BYTEZ_MODEL, BYTEZ_PROVIDER, DEFAULT_BYTEZ_API_URL
from municipality.models import SemanticDocumentRun
from municipality.semantic_contract import (
    SemanticExtractionOutput,
    SemanticValidationIssue,
    SemanticValidationReport,
    parse_semantic_model_output,
)


SEMANTIC_PROVIDER_BYTEZ = "bytez"
SEMANTIC_PROVIDER_AI21 = "ai21"
DEFAULT_AI21_API_URL = "https://api.ai21.com/studio/v1/chat/completions"


@dataclass(slots=True)
class SemanticModelResponse:
    payload: dict[str, Any] | None
    request_tokens: int | None
    response_tokens: int | None
    error_code: str | None
    error_text: str | None


class SemanticModelClient(Protocol):
    @property
    def provider_name(self) -> str:
        raise NotImplementedError

    @property
    def model_name(self) -> str:
        raise NotImplementedError

    def is_configured(self) -> bool:
        raise NotImplementedError

    def extract_semantic(self, *, request_payload: dict[str, Any]) -> SemanticModelResponse:
        raise NotImplementedError


class BytezSemanticClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        timeout_seconds: float = 60.0,
        model_name: str = BYTEZ_MODEL,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.75,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("BYTEZ_API_KEY")
        self.endpoint = endpoint or os.getenv("BYTEZ_API_URL", DEFAULT_BYTEZ_API_URL)
        self.timeout_seconds = timeout_seconds
        self._model_name = model_name
        self.max_attempts = max(1, int(max_attempts))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))

    @property
    def provider_name(self) -> str:
        return BYTEZ_PROVIDER

    @property
    def model_name(self) -> str:
        return self._model_name

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def extract_semantic(self, *, request_payload: dict[str, Any]) -> SemanticModelResponse:
        if not self.api_key:
            return SemanticModelResponse(
                payload=None,
                request_tokens=None,
                response_tokens=None,
                error_code="MODEL_NOT_CONFIGURED",
                error_text="BYTEZ_API_KEY is not configured",
            )

        body = {
            "model": self.model_name,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": request_payload.get("system_instruction", "")},
                {
                    "role": "user",
                    "content": json.dumps(request_payload, ensure_ascii=False),
                },
            ],
        }

        payload: dict[str, Any] | None = None
        last_exception: Exception | None = None
        for attempt_index in range(1, self.max_attempts + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        self.endpoint,
                        headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                        json=body,
                    )
                    response.raise_for_status()
                    parsed_payload = response.json()
                if isinstance(parsed_payload, dict):
                    payload = parsed_payload
                    last_exception = None
                    break
                last_exception = ValueError("response JSON root is not an object")
            except Exception as exc:  # noqa: BLE001
                last_exception = exc

            if attempt_index < self.max_attempts and self.retry_backoff_seconds > 0.0:
                time.sleep(self.retry_backoff_seconds * attempt_index)

        if payload is None:
            error_suffix = f"; attempts={self.max_attempts}" if self.max_attempts > 1 else ""
            if last_exception is None:
                error_text = f"model response payload missing{error_suffix}"
            else:
                error_text = f"{last_exception.__class__.__name__}:{last_exception}{error_suffix}"
            return SemanticModelResponse(
                payload=None,
                request_tokens=None,
                response_tokens=None,
                error_code="MODEL_REQUEST_FAILED",
                error_text=error_text,
            )

        usage = payload.get("usage") if isinstance(payload, dict) else {}
        request_tokens = _as_int(usage.get("prompt_tokens")) if isinstance(usage, dict) else None
        response_tokens = _as_int(usage.get("completion_tokens")) if isinstance(usage, dict) else None

        content = _extract_response_content(payload)
        if content is None:
            return SemanticModelResponse(
                payload=None,
                request_tokens=request_tokens,
                response_tokens=response_tokens,
                error_code="MODEL_EMPTY_RESPONSE",
                error_text="missing model response content",
            )

        parsed = _parse_json_content(content)
        if not isinstance(parsed, dict):
            return SemanticModelResponse(
                payload=None,
                request_tokens=request_tokens,
                response_tokens=response_tokens,
                error_code="MODEL_INVALID_JSON",
                error_text=_invalid_json_error_text(content),
            )

        return SemanticModelResponse(
            payload=parsed,
            request_tokens=request_tokens,
            response_tokens=response_tokens,
            error_code=None,
            error_text=None,
        )


class AI21SemanticClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        timeout_seconds: float = 60.0,
        model_name: str = "jamba-mini",
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.75,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("AI21_API_KEY")
        self.endpoint = endpoint or os.getenv("AI21_API_URL", DEFAULT_AI21_API_URL)
        self.timeout_seconds = timeout_seconds
        self._model_name = model_name
        self.max_attempts = max(1, int(max_attempts))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))

    @property
    def provider_name(self) -> str:
        return SEMANTIC_PROVIDER_AI21

    @property
    def model_name(self) -> str:
        return self._model_name

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def extract_semantic(self, *, request_payload: dict[str, Any]) -> SemanticModelResponse:
        if not self.api_key:
            return SemanticModelResponse(
                payload=None,
                request_tokens=None,
                response_tokens=None,
                error_code="MODEL_NOT_CONFIGURED",
                error_text="AI21_API_KEY is not configured",
            )

        body = {
            "model": self.model_name,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": request_payload.get("system_instruction", "")},
                {
                    "role": "user",
                    "content": json.dumps(request_payload, ensure_ascii=False),
                },
            ],
        }

        payload: dict[str, Any] | None = None
        last_exception: Exception | None = None
        for attempt_index in range(1, self.max_attempts + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        self.endpoint,
                        headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                        json=body,
                    )
                    response.raise_for_status()
                    parsed_payload = response.json()
                if isinstance(parsed_payload, dict):
                    payload = parsed_payload
                    last_exception = None
                    break
                last_exception = ValueError("response JSON root is not an object")
            except Exception as exc:  # noqa: BLE001
                last_exception = exc

            if attempt_index < self.max_attempts and self.retry_backoff_seconds > 0.0:
                time.sleep(self.retry_backoff_seconds * attempt_index)

        if payload is None:
            error_suffix = f"; attempts={self.max_attempts}" if self.max_attempts > 1 else ""
            if last_exception is None:
                error_text = f"model response payload missing{error_suffix}"
            else:
                error_text = f"{last_exception.__class__.__name__}:{last_exception}{error_suffix}"
            return SemanticModelResponse(
                payload=None,
                request_tokens=None,
                response_tokens=None,
                error_code="MODEL_REQUEST_FAILED",
                error_text=error_text,
            )

        usage = payload.get("usage") if isinstance(payload, dict) else {}
        request_tokens = _as_int(usage.get("prompt_tokens")) if isinstance(usage, dict) else None
        response_tokens = _as_int(usage.get("completion_tokens")) if isinstance(usage, dict) else None

        content = _extract_response_content(payload)
        if content is None:
            return SemanticModelResponse(
                payload=None,
                request_tokens=request_tokens,
                response_tokens=response_tokens,
                error_code="MODEL_EMPTY_RESPONSE",
                error_text="missing model response content",
            )

        parsed = _parse_json_content(content)
        if not isinstance(parsed, dict):
            return SemanticModelResponse(
                payload=None,
                request_tokens=request_tokens,
                response_tokens=response_tokens,
                error_code="MODEL_INVALID_JSON",
                error_text=_invalid_json_error_text(content),
            )

        return SemanticModelResponse(
            payload=parsed,
            request_tokens=request_tokens,
            response_tokens=response_tokens,
            error_code=None,
            error_text=None,
        )


def build_semantic_model_client() -> SemanticModelClient:
    provider = (os.getenv("SEMANTIC_MODEL_PROVIDER") or SEMANTIC_PROVIDER_BYTEZ).strip().casefold()
    default_model_name = "jamba-mini" if provider == SEMANTIC_PROVIDER_AI21 else BYTEZ_MODEL
    model_name = (os.getenv("SEMANTIC_MODEL") or default_model_name).strip() or default_model_name
    timeout_seconds = _env_float(os.getenv("SEMANTIC_MODEL_TIMEOUT_SECONDS"), default=60.0)
    max_attempts = _env_int(os.getenv("SEMANTIC_MODEL_MAX_ATTEMPTS"), default=3)
    retry_backoff_seconds = _env_float(os.getenv("SEMANTIC_MODEL_RETRY_BACKOFF_SECONDS"), default=0.75)

    if provider == SEMANTIC_PROVIDER_AI21:
        ai21_model = model_name if model_name else "jamba-mini"
        return AI21SemanticClient(
            model_name=ai21_model,
            endpoint=os.getenv("AI21_API_URL", DEFAULT_AI21_API_URL),
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )

    return BytezSemanticClient(
        model_name=model_name,
        endpoint=os.getenv("BYTEZ_API_URL", DEFAULT_BYTEZ_API_URL),
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )


@dataclass(slots=True)
class SemanticExtractionRunResult:
    run: SemanticDocumentRun
    output: SemanticExtractionOutput | None
    validation_report: SemanticValidationReport | None
    from_cache: bool


class SemanticExtractor:
    def __init__(self, session: Session, *, model_client: SemanticModelClient | None = None):
        self.session = session
        self.model_client = model_client or build_semantic_model_client()

    def extract_once(
        self,
        *,
        document_version_id: int,
        prompt_hash: str,
        request_payload: dict[str, Any],
    ) -> SemanticExtractionRunResult:
        existing = self.session.execute(
            select(SemanticDocumentRun).where(
                SemanticDocumentRun.document_version_id == document_version_id,
                SemanticDocumentRun.prompt_hash == prompt_hash,
                SemanticDocumentRun.model_provider == self.model_client.provider_name,
                SemanticDocumentRun.model_name == self.model_client.model_name,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return self._result_from_cached_run(existing)

        run = SemanticDocumentRun(
            document_version_id=document_version_id,
            prompt_hash=prompt_hash,
            model_provider=self.model_client.provider_name,
            model_name=self.model_client.model_name,
            status="running",
            api_call_count=0,
            started_at=datetime.utcnow(),
        )
        self.session.add(run)
        self.session.flush()

        if not self.model_client.is_configured():
            run.status = "failed"
            run.error_code = "MODEL_NOT_CONFIGURED"
            run.error_text = "semantic model client is not configured"
            run.finished_at = datetime.utcnow()
            self.session.flush()
            return SemanticExtractionRunResult(run=run, output=None, validation_report=None, from_cache=False)

        model_response = self.model_client.extract_semantic(request_payload=request_payload)
        run.api_call_count = 1
        run.request_tokens = model_response.request_tokens
        run.response_tokens = model_response.response_tokens

        if model_response.error_code or model_response.payload is None:
            run.status = "failed"
            run.error_code = model_response.error_code or "MODEL_REQUEST_FAILED"
            run.error_text = model_response.error_text
            run.finished_at = datetime.utcnow()
            self.session.flush()
            return SemanticExtractionRunResult(run=run, output=None, validation_report=None, from_cache=False)

        output, validation_report = parse_semantic_model_output(model_response.payload)
        run.extraction_payload_json = json.dumps(model_response.payload, ensure_ascii=False)
        run.validation_report_json = json.dumps(_validation_report_to_dict(validation_report), ensure_ascii=False)
        run.status = "completed" if validation_report.is_valid else "failed"
        if not validation_report.is_valid:
            run.error_code = "INVALID_SCHEMA"
            run.error_text = "semantic extraction response failed schema validation"
        run.finished_at = datetime.utcnow()
        self.session.flush()
        return SemanticExtractionRunResult(
            run=run,
            output=output,
            validation_report=validation_report,
            from_cache=False,
        )

    def _result_from_cached_run(self, run: SemanticDocumentRun) -> SemanticExtractionRunResult:
        payload = _loads_json(run.extraction_payload_json)
        report_payload = _loads_json(run.validation_report_json)

        output: SemanticExtractionOutput | None = None
        validation_report: SemanticValidationReport | None = None

        if isinstance(payload, dict):
            output, parsed_report = parse_semantic_model_output(payload)
            validation_report = parsed_report
        elif isinstance(report_payload, dict):
            validation_report = _validation_report_from_dict(report_payload)

        return SemanticExtractionRunResult(run=run, output=output, validation_report=validation_report, from_cache=True)


def _extract_response_content(payload: dict[str, Any]) -> Any | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text_value = item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                text_parts.append(text_value.strip())
        if text_parts:
            return "\n".join(text_parts)
        return None
    return content


def _parse_json_content(content: Any) -> Any | None:
    if isinstance(content, dict):
        coerced = _coerce_semantic_payload(content)
        return coerced if coerced is not None else content
    if isinstance(content, list):
        if len(content) == 1 and isinstance(content[0], dict):
            return content[0]
        return None
    if isinstance(content, str):
        compact = content.strip()
        if not compact:
            return None

        parsed = _try_json_loads(compact)
        if parsed is not None:
            coerced = _coerce_semantic_payload(parsed)
            if coerced is not None:
                return coerced
            return parsed

        stripped_fence = _strip_markdown_fence(compact)
        if stripped_fence != compact:
            parsed = _try_json_loads(stripped_fence)
            if parsed is not None:
                coerced = _coerce_semantic_payload(parsed)
                if coerced is not None:
                    return coerced
                return parsed

        extracted_payload = _extract_best_semantic_payload(compact)
        if extracted_payload is not None:
            return extracted_payload
    return None


def _try_json_loads(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None

    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
        return parsed[0]
    return None


def _strip_markdown_fence(value: str) -> str:
    compact = value.strip()
    if not compact.startswith("```"):
        return compact

    lines = compact.splitlines()
    if len(lines) < 3:
        return compact
    if lines[-1].strip() != "```":
        return compact

    return "\n".join(lines[1:-1]).strip()


def _extract_first_json_object(value: str) -> str | None:
    start = value.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(value)):
            char = value[idx]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return value[start : idx + 1]
        start = value.find("{", start + 1)
    return None


def _extract_best_semantic_payload(value: str) -> dict[str, Any] | None:
    start = value.find("{")
    fallback: dict[str, Any] | None = None
    while start >= 0:
        candidate_text = _extract_balanced_object(value=value, start_index=start)
        if candidate_text is None:
            start = value.find("{", start + 1)
            continue

        parsed = _try_json_loads(candidate_text)
        if parsed is None:
            start = value.find("{", start + 1)
            continue

        coerced = _coerce_semantic_payload(parsed)
        if coerced is not None and _looks_like_semantic_root(coerced):
            return coerced
        if fallback is None:
            fallback = parsed
        start = value.find("{", start + 1)

    if fallback is None:
        return None
    coerced_fallback = _coerce_semantic_payload(fallback)
    return coerced_fallback if coerced_fallback is not None else fallback


def _extract_balanced_object(*, value: str, start_index: int) -> str | None:
    if start_index < 0 or start_index >= len(value) or value[start_index] != "{":
        return None

    depth = 0
    in_string = False
    escaped = False
    for idx in range(start_index, len(value)):
        char = value[idx]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return value[start_index : idx + 1]
    return None


def _coerce_semantic_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    if _looks_like_semantic_root(payload):
        normalized = dict(payload)
        if not isinstance(normalized.get("evidence_spans"), list):
            normalized["evidence_spans"] = []
        if not isinstance(normalized.get("nodes"), list):
            normalized["nodes"] = []
        return normalized

    for key in ("output", "data", "result", "semantic", "response"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            coerced_nested = _coerce_semantic_payload(nested)
            if coerced_nested is not None:
                return coerced_nested

    if _looks_like_evidence_span(payload):
        return {"evidence_spans": [payload], "nodes": []}
    if _looks_like_node(payload):
        return {"evidence_spans": [], "nodes": [payload]}
    return None


def _looks_like_semantic_root(payload: dict[str, Any]) -> bool:
    return "evidence_spans" in payload or "nodes" in payload


def _looks_like_evidence_span(payload: dict[str, Any]) -> bool:
    required_keys = {"span_id", "category", "start_offset", "end_offset", "text"}
    return required_keys.issubset(payload.keys())


def _looks_like_node(payload: dict[str, Any]) -> bool:
    required_keys = {"candidate_id", "label_he", "node_kind", "semantic_type"}
    return required_keys.issubset(payload.keys())


def _invalid_json_error_text(content: Any) -> str:
    if content is None:
        return "response JSON is not an object; content is empty"
    content_type = type(content).__name__
    if isinstance(content, str):
        snippet = content.strip().replace("\n", " ")[:240]
        return f"response JSON is not an object; content_type={content_type}; snippet={snippet}"
    return f"response JSON is not an object; content_type={content_type}"


def _loads_json(value: str | None) -> Any | None:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _validation_report_to_dict(report: SemanticValidationReport) -> dict[str, Any]:
    return {
        "is_valid": report.is_valid,
        "issues": [
            {
                "code": issue.code,
                "path": issue.path,
                "message": issue.message,
            }
            for issue in report.issues
        ],
    }


def _validation_report_from_dict(value: dict[str, Any]) -> SemanticValidationReport:
    issues_value = value.get("issues")
    raw_issues: list[Any] = issues_value if isinstance(issues_value, list) else []
    issues: list[SemanticValidationIssue] = []
    for raw in raw_issues:
        if not isinstance(raw, dict):
            continue
        code = raw.get("code")
        path = raw.get("path")
        message = raw.get("message")
        if not isinstance(code, str) or not isinstance(path, str) or not isinstance(message, str):
            continue
        issues.append(SemanticValidationIssue(code=code, path=path, message=message))
    return SemanticValidationReport(is_valid=bool(value.get("is_valid")), issues=issues)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        compact = value.strip()
        if compact.isdigit():
            return int(compact)
    return None


def _env_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        return max(1, int(value))
    except ValueError:
        return default


def _env_float(value: str | None, *, default: float) -> float:
    if value is None:
        return default
    try:
        return max(1.0e-6, float(value))
    except ValueError:
        return default
