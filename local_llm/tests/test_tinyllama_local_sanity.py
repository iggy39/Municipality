from __future__ import annotations

import os

import pytest

pytest.importorskip("torch")
pytest.importorskip("transformers")

from local_llm.tinyllama_local import DEFAULT_MODEL_ID, LocalLlmError, TinyLlamaLocalEngine


MODEL_SOURCE = os.getenv("TINYLLAMA_MODEL_DIR") or DEFAULT_MODEL_ID
TEST_DEVICE = os.getenv("TINYLLAMA_TEST_DEVICE", "auto")


@pytest.fixture(scope="module")
def tinyllama_engine() -> TinyLlamaLocalEngine:
    try:
        return TinyLlamaLocalEngine(model_source=MODEL_SOURCE, device=TEST_DEVICE)
    except LocalLlmError as exc:
        pytest.skip(f"TinyLlama local model is unavailable: {exc}")


def test_local_load_fails_with_clear_error_when_model_missing(tmp_path) -> None:
    missing_model_dir = tmp_path / "missing_tinyllama_model"

    with pytest.raises(LocalLlmError, match="Could not load TinyLlama locally"):
        TinyLlamaLocalEngine(model_source=str(missing_model_dir), device="cpu")


def test_generate_returns_non_empty_text(tinyllama_engine: TinyLlamaLocalEngine) -> None:
    result = tinyllama_engine.generate(
        prompt="Write one short sentence about local city councils.",
        max_new_tokens=48,
        temperature=0.0,
        seed=7,
    )

    assert result.response_text
    assert result.tokens_out > 0


def test_greedy_generation_is_repeatable_for_same_seed(tinyllama_engine: TinyLlamaLocalEngine) -> None:
    prompt = "Give a concise explanation of municipal budgeting in one sentence."

    first = tinyllama_engine.generate(
        prompt=prompt,
        max_new_tokens=48,
        temperature=0.0,
        seed=123,
    )
    second = tinyllama_engine.generate(
        prompt=prompt,
        max_new_tokens=48,
        temperature=0.0,
        seed=123,
    )

    assert first.response_text == second.response_text


def test_metrics_are_reported(tinyllama_engine: TinyLlamaLocalEngine) -> None:
    result = tinyllama_engine.generate(
        prompt="List two goals of a city public works department.",
        max_new_tokens=48,
        temperature=0.0,
        seed=99,
    )

    assert result.latency_s > 0.0
    assert result.tokens_out > 0
    assert result.tokens_per_s > 0.0
