from __future__ import annotations

import json
import os
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
    ):
        self.api_key = api_key if api_key is not None else os.getenv("BYTEZ_API_KEY")
        self.endpoint = endpoint or os.getenv("BYTEZ_API_URL", DEFAULT_BYTEZ_API_URL)
        self.timeout_seconds = timeout_seconds
        self._model_name = model_name

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

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=body,
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return SemanticModelResponse(
                payload=None,
                request_tokens=None,
                response_tokens=None,
                error_code="MODEL_REQUEST_FAILED",
                error_text=f"{exc.__class__.__name__}:{exc}",
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
                error_text="response JSON is not an object",
            )

        return SemanticModelResponse(
            payload=parsed,
            request_tokens=request_tokens,
            response_tokens=response_tokens,
            error_code=None,
            error_text=None,
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
        self.model_client = model_client or BytezSemanticClient()

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
    return message.get("content")


def _parse_json_content(content: Any) -> Any | None:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None
    return None


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
    raw_issues = value.get("issues") if isinstance(value.get("issues"), list) else []
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
