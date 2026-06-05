from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

import httpx

from municipality.fallback import BYTEZ_MODEL, BYTEZ_PROVIDER, DEFAULT_BYTEZ_API_URL
from municipality.rag_observability import log_rag_event


RAG_CALL_ANSWER = "answer"
RAG_CALL_VERIFY = "verify"
RAG_CALL_REFUSE = "refuse"
RAG_CALL_REWRITE = "rewrite"
RAG_CALL_CLASSIFY = "classify"

RAG_PROVIDER_BYTEZ = "bytez"
RAG_PROVIDER_AI21 = "ai21"
RAG_PROVIDER_OLLAMA = "ollama"
RAG_PROVIDER_MOCK = "mock"
DEFAULT_AI21_API_URL = "https://api.ai21.com/studio/v1/chat/completions"
DEFAULT_AI21_MODEL = "jamba-mini"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "dicta-il/DictaLM-3.0-24B-Thinking:bf16"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 300.0
DEFAULT_OLLAMA_NUM_PREDICT = 1536

RAG_ANSWER_PREFIX_DEFAULT = "answer question from provided hebrew municipal evidence with citations only"
RAG_VERIFY_PREFIX_DEFAULT = "verify every claim against provided hebrew evidence and citations only"
RAG_REFUSE_PREFIX_DEFAULT = "if evidence is insufficient, refuse in hebrew and explain missing evidence"
RAG_REWRITE_PREFIX_DEFAULT = "rewrite hebrew municipal queries into grounded retrieval intents and terms only"
RAG_CLASSIFY_PREFIX_DEFAULT = "classify hebrew municipal sections into grounded topics using visible structure and text only"


@dataclass(slots=True)
class RagPromptPrefixConfig:
    answer: str = RAG_ANSWER_PREFIX_DEFAULT
    verify: str = RAG_VERIFY_PREFIX_DEFAULT
    refuse: str = RAG_REFUSE_PREFIX_DEFAULT
    rewrite: str = RAG_REWRITE_PREFIX_DEFAULT
    classify: str = RAG_CLASSIFY_PREFIX_DEFAULT

    def for_call_type(self, call_type: str) -> str:
        normalized = _normalize_call_type(call_type)
        mapping = self.as_dict()
        if normalized not in mapping:
            raise ValueError(f"unsupported rag call type: {call_type}")
        prefix = mapping[normalized].strip()
        if not prefix:
            raise ValueError(f"missing required prompt prefix for call type: {normalized}")
        return prefix

    def as_dict(self) -> dict[str, str]:
        return {
            RAG_CALL_ANSWER: self.answer,
            RAG_CALL_VERIFY: self.verify,
            RAG_CALL_REFUSE: self.refuse,
            RAG_CALL_REWRITE: self.rewrite,
            RAG_CALL_CLASSIFY: self.classify,
        }


@dataclass(slots=True)
class RagLlmConfig:
    provider: str = RAG_PROVIDER_OLLAMA
    model: str = DEFAULT_OLLAMA_MODEL
    bytez_endpoint: str = DEFAULT_BYTEZ_API_URL
    ai21_endpoint: str = DEFAULT_AI21_API_URL
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS
    ollama_num_predict: int = DEFAULT_OLLAMA_NUM_PREDICT
    prompt_prefixes: RagPromptPrefixConfig = field(default_factory=RagPromptPrefixConfig)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RagLlmConfig:
        source = env if env is not None else os.environ
        requested_provider = (source.get("RAG_LLM_PROVIDER") or RAG_PROVIDER_OLLAMA).strip().casefold()
        provider = _normalize_provider(requested_provider)
        if provider == RAG_PROVIDER_AI21:
            default_model = DEFAULT_AI21_MODEL
        elif provider == RAG_PROVIDER_OLLAMA:
            default_model = DEFAULT_OLLAMA_MODEL
        else:
            default_model = BYTEZ_MODEL
        model_env = source.get("RAG_LLM_MODEL") if requested_provider == provider else None
        model = (model_env or default_model).strip() or default_model
        endpoint = (source.get("BYTEZ_API_URL") or DEFAULT_BYTEZ_API_URL).strip() or DEFAULT_BYTEZ_API_URL
        ai21_endpoint = (source.get("AI21_API_URL") or DEFAULT_AI21_API_URL).strip() or DEFAULT_AI21_API_URL
        ollama_base_url = (source.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL).strip() or DEFAULT_OLLAMA_BASE_URL
        timeout_raw = source.get("RAG_LLM_TIMEOUT_SECONDS")

        timeout_seconds = DEFAULT_OLLAMA_TIMEOUT_SECONDS if provider == RAG_PROVIDER_OLLAMA else 60.0
        if timeout_raw:
            try:
                timeout_seconds = max(1.0, float(timeout_raw))
            except ValueError:
                timeout_seconds = DEFAULT_OLLAMA_TIMEOUT_SECONDS if provider == RAG_PROVIDER_OLLAMA else 60.0
        num_predict_raw = source.get("RAG_LLM_OLLAMA_NUM_PREDICT") or source.get("OLLAMA_NUM_PREDICT")
        ollama_num_predict = DEFAULT_OLLAMA_NUM_PREDICT
        if num_predict_raw:
            try:
                ollama_num_predict = max(128, int(num_predict_raw))
            except ValueError:
                ollama_num_predict = DEFAULT_OLLAMA_NUM_PREDICT

        prefixes = RagPromptPrefixConfig(
            answer=(source.get("RAG_PROMPT_PREFIX_ANSWER") or RAG_ANSWER_PREFIX_DEFAULT).strip()
            or RAG_ANSWER_PREFIX_DEFAULT,
            verify=(source.get("RAG_PROMPT_PREFIX_VERIFY") or RAG_VERIFY_PREFIX_DEFAULT).strip()
            or RAG_VERIFY_PREFIX_DEFAULT,
            refuse=(source.get("RAG_PROMPT_PREFIX_REFUSE") or RAG_REFUSE_PREFIX_DEFAULT).strip()
            or RAG_REFUSE_PREFIX_DEFAULT,
            rewrite=(source.get("RAG_PROMPT_PREFIX_REWRITE") or RAG_REWRITE_PREFIX_DEFAULT).strip()
            or RAG_REWRITE_PREFIX_DEFAULT,
            classify=(source.get("RAG_PROMPT_PREFIX_CLASSIFY") or RAG_CLASSIFY_PREFIX_DEFAULT).strip()
            or RAG_CLASSIFY_PREFIX_DEFAULT,
        )

        return cls(
            provider=provider,
            model=model,
            bytez_endpoint=endpoint,
            ai21_endpoint=ai21_endpoint,
            ollama_base_url=ollama_base_url,
            timeout_seconds=timeout_seconds,
            ollama_num_predict=ollama_num_predict,
            prompt_prefixes=prefixes,
        )


@dataclass(slots=True)
class RagLlmResult:
    provider: str
    model: str
    text: str | None
    request_tokens: int | None
    response_tokens: int | None
    error_code: str | None
    error_text: str | None
    raw_payload: dict[str, Any] | None = None


class RagLlmProvider(Protocol):
    @property
    def provider_name(self) -> str:
        raise NotImplementedError

    @property
    def model_name(self) -> str:
        raise NotImplementedError

    def is_configured(self) -> bool:
        raise NotImplementedError

    def generate(
        self,
        *,
        call_type: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
    ) -> RagLlmResult:
        raise NotImplementedError


class BytezRagProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        model_name: str = BYTEZ_MODEL,
        timeout_seconds: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("BYTEZ_API_KEY")
        self.endpoint = endpoint or os.getenv("BYTEZ_API_URL", DEFAULT_BYTEZ_API_URL)
        self._model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @property
    def provider_name(self) -> str:
        return BYTEZ_PROVIDER

    @property
    def model_name(self) -> str:
        return self._model_name

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def generate(
        self,
        *,
        call_type: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
    ) -> RagLlmResult:
        _normalize_call_type(call_type)
        if not self.api_key:
            return RagLlmResult(
                provider=self.provider_name,
                model=self.model_name,
                text=None,
                request_tokens=None,
                response_tokens=None,
                error_code="MODEL_NOT_CONFIGURED",
                error_text="BYTEZ_API_KEY is not configured",
            )

        body = {
            "model": self.model_name,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }

        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = client.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=body,
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return RagLlmResult(
                provider=self.provider_name,
                model=self.model_name,
                text=None,
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
            return RagLlmResult(
                provider=self.provider_name,
                model=self.model_name,
                text=None,
                request_tokens=request_tokens,
                response_tokens=response_tokens,
                error_code="MODEL_EMPTY_RESPONSE",
                error_text="missing model response content",
                raw_payload=payload if isinstance(payload, dict) else None,
            )

        return RagLlmResult(
            provider=self.provider_name,
            model=self.model_name,
            text=content,
            request_tokens=request_tokens,
            response_tokens=response_tokens,
            error_code=None,
            error_text=None,
            raw_payload=payload if isinstance(payload, dict) else None,
        )


class AI21RagProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        model_name: str,
        timeout_seconds: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("AI21_API_KEY")
        self.endpoint = endpoint or os.getenv("AI21_API_URL", DEFAULT_AI21_API_URL)
        self._model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @property
    def provider_name(self) -> str:
        return RAG_PROVIDER_AI21

    @property
    def model_name(self) -> str:
        return self._model_name

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def generate(
        self,
        *,
        call_type: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
    ) -> RagLlmResult:
        _normalize_call_type(call_type)
        if not self.api_key:
            return RagLlmResult(
                provider=self.provider_name,
                model=self.model_name,
                text=None,
                request_tokens=None,
                response_tokens=None,
                error_code="MODEL_NOT_CONFIGURED",
                error_text="AI21_API_KEY is not configured",
            )

        body = {
            "model": self.model_name,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = client.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=body,
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return RagLlmResult(
                provider=self.provider_name,
                model=self.model_name,
                text=None,
                request_tokens=None,
                response_tokens=None,
                error_code="MODEL_REQUEST_FAILED",
                error_text=f"{exc.__class__.__name__}:{exc}",
            )

        usage = payload.get("usage") if isinstance(payload, dict) else {}
        request_tokens = _as_int(usage.get("prompt_tokens")) if isinstance(usage, dict) else None
        response_tokens = _as_int(usage.get("completion_tokens")) if isinstance(usage, dict) else None
        content = _extract_response_content(payload if isinstance(payload, dict) else {})
        if content is None:
            return RagLlmResult(
                provider=self.provider_name,
                model=self.model_name,
                text=None,
                request_tokens=request_tokens,
                response_tokens=response_tokens,
                error_code="MODEL_EMPTY_RESPONSE",
                error_text="missing model response content",
                raw_payload=payload if isinstance(payload, dict) else None,
            )
        return RagLlmResult(
            provider=self.provider_name,
            model=self.model_name,
            text=content,
            request_tokens=request_tokens,
            response_tokens=response_tokens,
            error_code=None,
            error_text=None,
            raw_payload=payload if isinstance(payload, dict) else None,
        )


class OllamaRagProvider:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        model_name: str = DEFAULT_OLLAMA_MODEL,
        timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS,
        num_predict: int = DEFAULT_OLLAMA_NUM_PREDICT,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL).rstrip("/")
        self._model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.num_predict = num_predict
        self.transport = transport

    @property
    def provider_name(self) -> str:
        return RAG_PROVIDER_OLLAMA

    @property
    def model_name(self) -> str:
        return self._model_name

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model_name)

    def generate(
        self,
        *,
        call_type: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
    ) -> RagLlmResult:
        _normalize_call_type(call_type)
        if not self.is_configured():
            return RagLlmResult(
                provider=self.provider_name,
                model=self.model_name,
                text=None,
                request_tokens=None,
                response_tokens=None,
                error_code="MODEL_NOT_CONFIGURED",
                error_text="OLLAMA_BASE_URL or model name is missing",
            )

        sent_messages = _with_no_think(messages)
        body = {
            "model": self.model_name,
            "messages": sent_messages,
            "stream": False,
            "think": False,
            "format": "json",
            "keep_alive": "10m",
            "options": {
                "temperature": temperature,
                "num_predict": self.num_predict,
            },
        }

        try:
            timeout = httpx.Timeout(
                timeout=max(1.0, self.timeout_seconds),
                connect=10.0,
                read=max(1.0, self.timeout_seconds),
                write=30.0,
                pool=10.0,
            )
            with httpx.Client(timeout=timeout, transport=self.transport) as client:
                response = client.post(f"{self.base_url}/api/chat", json=body)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return RagLlmResult(
                provider=self.provider_name,
                model=self.model_name,
                text=None,
                request_tokens=None,
                response_tokens=None,
                error_code="MODEL_REQUEST_FAILED",
                error_text=f"{exc.__class__.__name__}:{exc}",
            )

        content = _extract_ollama_response_content(payload if isinstance(payload, dict) else {})
        if content is None:
            return RagLlmResult(
                provider=self.provider_name,
                model=self.model_name,
                text=None,
                request_tokens=_as_int(payload.get("prompt_eval_count")) if isinstance(payload, dict) else None,
                response_tokens=_as_int(payload.get("eval_count")) if isinstance(payload, dict) else None,
                error_code="MODEL_EMPTY_RESPONSE",
                error_text="missing Ollama message content",
                raw_payload=payload if isinstance(payload, dict) else None,
            )

        return RagLlmResult(
            provider=self.provider_name,
            model=self.model_name,
            text=content,
            request_tokens=_as_int(payload.get("prompt_eval_count")) if isinstance(payload, dict) else None,
            response_tokens=_as_int(payload.get("eval_count")) if isinstance(payload, dict) else None,
            error_code=None,
            error_text=None,
            raw_payload=payload if isinstance(payload, dict) else None,
        )


class MockRagProvider:
    def __init__(
        self,
        *,
        provider_name: str = "MockProvider",
        model_name: str = "mock-rag-v1",
        configured: bool = True,
        responses_by_call_type: Mapping[str, str] | None = None,
    ):
        self._provider_name = provider_name
        self._model_name = model_name
        self._configured = configured
        self.requests: list[dict[str, Any]] = []

        defaults = {
            RAG_CALL_ANSWER: "mock-answer",
            RAG_CALL_VERIFY: "mock-verify",
            RAG_CALL_REFUSE: "mock-refuse",
        }
        if responses_by_call_type:
            for key, value in responses_by_call_type.items():
                normalized_key = _normalize_call_type(key)
                defaults[normalized_key] = value
        self._responses = defaults

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def is_configured(self) -> bool:
        return self._configured

    def generate(
        self,
        *,
        call_type: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
    ) -> RagLlmResult:
        normalized_call_type = _normalize_call_type(call_type)
        self.requests.append(
            {
                "call_type": normalized_call_type,
                "messages": messages,
                "temperature": temperature,
            }
        )

        if not self._configured:
            return RagLlmResult(
                provider=self.provider_name,
                model=self.model_name,
                text=None,
                request_tokens=None,
                response_tokens=None,
                error_code="MODEL_NOT_CONFIGURED",
                error_text="mock provider marked as unconfigured",
            )

        return RagLlmResult(
            provider=self.provider_name,
            model=self.model_name,
            text=self._responses.get(normalized_call_type),
            request_tokens=0,
            response_tokens=0,
            error_code=None,
            error_text=None,
        )


class RagLlmClient:
    def __init__(self, *, provider: RagLlmProvider, prompt_prefixes: RagPromptPrefixConfig | None = None):
        self.provider = provider
        self.prompt_prefixes = prompt_prefixes or RagPromptPrefixConfig()

    def generate(
        self,
        *,
        call_type: str,
        instruction: str,
        payload: str | dict[str, Any] | list[Any],
        temperature: float = 0.0,
        ask_request_id: str | None = None,
    ) -> RagLlmResult:
        normalized_call_type = _normalize_call_type(call_type)
        prefix = self.prompt_prefixes.for_call_type(normalized_call_type)
        compact_instruction = instruction.strip()
        system_content = prefix if not compact_instruction else f"{prefix}\n{compact_instruction}"

        if isinstance(payload, str):
            user_content = payload
        else:
            user_content = json.dumps(payload, ensure_ascii=False)

        log_rag_event(
            "rag.llm.call",
            ask_request_id=ask_request_id,
            call_type=normalized_call_type,
            prompt_prefix_category=normalized_call_type,
            provider=self.provider.provider_name,
            model=self.provider.model_name,
            payload_chars=len(user_content),
            temperature=temperature,
        )

        started = time.perf_counter()
        result = self.provider.generate(
            call_type=normalized_call_type,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            temperature=temperature,
        )
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        log_rag_event(
            "rag.llm.result",
            ask_request_id=ask_request_id,
            call_type=normalized_call_type,
            prompt_prefix_category=normalized_call_type,
            provider=result.provider,
            model=result.model,
            latency_ms=latency_ms,
            request_tokens=result.request_tokens,
            response_tokens=result.response_tokens,
            error_code=result.error_code,
        )
        return result


def build_rag_llm_client(
    *,
    config: RagLlmConfig | None = None,
    provider: RagLlmProvider | None = None,
) -> RagLlmClient:
    resolved_config = config or RagLlmConfig.from_env()
    resolved_provider = provider or build_rag_provider(config=resolved_config)
    return RagLlmClient(provider=resolved_provider, prompt_prefixes=resolved_config.prompt_prefixes)


def build_rag_provider(*, config: RagLlmConfig | None = None) -> RagLlmProvider:
    resolved_config = config or RagLlmConfig.from_env()
    provider_key = _normalize_provider(resolved_config.provider)

    if provider_key == RAG_PROVIDER_BYTEZ:
        return BytezRagProvider(
            model_name=resolved_config.model,
            endpoint=resolved_config.bytez_endpoint,
            timeout_seconds=resolved_config.timeout_seconds,
        )
    if provider_key == RAG_PROVIDER_AI21:
        return AI21RagProvider(
            model_name=resolved_config.model,
            endpoint=resolved_config.ai21_endpoint,
            timeout_seconds=resolved_config.timeout_seconds,
        )
    if provider_key == RAG_PROVIDER_OLLAMA:
        return OllamaRagProvider(
            model_name=resolved_config.model,
            base_url=resolved_config.ollama_base_url,
            timeout_seconds=resolved_config.timeout_seconds,
            num_predict=resolved_config.ollama_num_predict,
        )
    if provider_key == RAG_PROVIDER_MOCK:
        return MockRagProvider(model_name=resolved_config.model)
    raise ValueError(f"unsupported rag llm provider: {resolved_config.provider}")


def _normalize_call_type(call_type: str) -> str:
    normalized = (call_type or "").strip().casefold()
    if normalized not in {RAG_CALL_ANSWER, RAG_CALL_VERIFY, RAG_CALL_REFUSE, RAG_CALL_REWRITE, RAG_CALL_CLASSIFY}:
        raise ValueError(f"unsupported rag call type: {call_type}")
    return normalized


def _normalize_provider(provider: str | None) -> str:
    normalized = (provider or RAG_PROVIDER_OLLAMA).strip().casefold()
    if normalized == RAG_PROVIDER_MOCK:
        return normalized
    if normalized == RAG_PROVIDER_OLLAMA:
        return normalized
    return RAG_PROVIDER_OLLAMA


def _with_no_think(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    if not messages:
        return messages
    first = dict(messages[0])
    content = str(first.get("content") or "")
    if not content.lstrip().startswith("/no_think"):
        first["content"] = f"/no_think\n{content}".strip()
    return [first, *messages[1:]]


def _extract_response_content(payload: dict[str, Any]) -> str | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None

    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return None

    content = message.get("content")
    if isinstance(content, str):
        compact = content.strip()
        return compact or None

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text_value = item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                parts.append(text_value.strip())
        if parts:
            return "\n".join(parts)

    return None


def _extract_ollama_response_content(payload: dict[str, Any]) -> str | None:
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return _clean_model_text(content)
    response = payload.get("response")
    if isinstance(response, str) and response.strip():
        return _clean_model_text(response)
    return None


def _clean_model_text(value: str) -> str | None:
    text = value.strip()
    text = text.split("</think>")[-1].strip() if "</think>" in text else text
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    text = re.sub(r"^```(?:json|text|markdown)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    return text or None


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
