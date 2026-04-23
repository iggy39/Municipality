from __future__ import annotations

import pytest

from municipality.fallback import BYTEZ_MODEL
from municipality.rag_llm import (
    AI21RagProvider,
    RAG_ANSWER_PREFIX_DEFAULT,
    RAG_CALL_ANSWER,
    RAG_CALL_REFUSE,
    RAG_CALL_VERIFY,
    RAG_REFUSE_PREFIX_DEFAULT,
    RAG_VERIFY_PREFIX_DEFAULT,
    BytezRagProvider,
    MockRagProvider,
    RagLlmConfig,
    build_rag_llm_client,
)


def test_rag_llm_default_config_matches_m4_policy_lock() -> None:
    config = RagLlmConfig.from_env({})

    assert config.provider == "bytez"
    assert config.model == BYTEZ_MODEL
    assert config.prompt_prefixes.for_call_type(RAG_CALL_ANSWER) == RAG_ANSWER_PREFIX_DEFAULT
    assert config.prompt_prefixes.for_call_type(RAG_CALL_VERIFY) == RAG_VERIFY_PREFIX_DEFAULT
    assert config.prompt_prefixes.for_call_type(RAG_CALL_REFUSE) == RAG_REFUSE_PREFIX_DEFAULT


def test_rag_llm_config_can_switch_provider_without_code_change() -> None:
    config = RagLlmConfig.from_env(
        {
            "RAG_LLM_PROVIDER": "mock",
            "RAG_LLM_MODEL": "mock-rag-v9",
        }
    )
    client = build_rag_llm_client(config=config)

    assert isinstance(client.provider, MockRagProvider)
    assert client.provider.model_name == "mock-rag-v9"


def test_rag_llm_can_build_ai21_provider_from_env() -> None:
    config = RagLlmConfig.from_env(
        {
            "RAG_LLM_PROVIDER": "ai21",
            "RAG_LLM_MODEL": "jamba-mini",
        }
    )
    client = build_rag_llm_client(config=config)

    assert isinstance(client.provider, AI21RagProvider)
    assert client.provider.model_name == "jamba-mini"


def test_rag_llm_prompt_prefixes_are_first_line_for_all_call_types() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            RAG_CALL_ANSWER: "ok-answer",
            RAG_CALL_VERIFY: "ok-verify",
            RAG_CALL_REFUSE: "ok-refuse",
        }
    )
    config = RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"})
    client = build_rag_llm_client(config=config, provider=provider)

    for call_type, expected_prefix in {
        RAG_CALL_ANSWER: RAG_ANSWER_PREFIX_DEFAULT,
        RAG_CALL_VERIFY: RAG_VERIFY_PREFIX_DEFAULT,
        RAG_CALL_REFUSE: RAG_REFUSE_PREFIX_DEFAULT,
    }.items():
        result = client.generate(
            call_type=call_type,
            instruction="respond in hebrew",
            payload={"question": "שאלה", "evidence": []},
        )

        assert result.error_code is None
        assert result.text is not None
        sent = provider.requests[-1]
        first_line = sent["messages"][0]["content"].splitlines()[0]
        assert first_line == expected_prefix


def test_bytez_provider_returns_not_configured_without_api_key() -> None:
    provider = BytezRagProvider(api_key="")
    result = provider.generate(
        call_type=RAG_CALL_ANSWER,
        messages=[{"role": "system", "content": "x"}, {"role": "user", "content": "y"}],
    )

    assert result.error_code == "MODEL_NOT_CONFIGURED"
    assert result.text is None


def test_ai21_provider_returns_not_configured_without_api_key() -> None:
    provider = AI21RagProvider(api_key="", model_name="jamba-mini")
    result = provider.generate(
        call_type=RAG_CALL_ANSWER,
        messages=[{"role": "system", "content": "x"}, {"role": "user", "content": "y"}],
    )

    assert result.error_code == "MODEL_NOT_CONFIGURED"
    assert result.text is None


def test_rag_llm_missing_required_prompt_prefix_fails_fast() -> None:
    provider = MockRagProvider()
    config = RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"})
    config.prompt_prefixes.answer = "   "
    client = build_rag_llm_client(config=config, provider=provider)

    with pytest.raises(ValueError, match="missing required prompt prefix"):
        client.generate(
            call_type=RAG_CALL_ANSWER,
            instruction="respond in hebrew",
            payload={"question": "שאלה", "evidence": []},
        )

    assert provider.requests == []
