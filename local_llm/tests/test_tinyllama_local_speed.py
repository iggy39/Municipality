from __future__ import annotations

import os
import statistics

import pytest

if os.getenv("RUN_TINYLLAMA_SPEED") != "1":
    pytest.skip("Set RUN_TINYLLAMA_SPEED=1 to run TinyLlama speed tests.", allow_module_level=True)

pytest.importorskip("torch")
pytest.importorskip("transformers")

from local_llm.tinyllama_local import DEFAULT_MODEL_ID, LocalLlmError, TinyLlamaLocalEngine


MODEL_SOURCE = os.getenv("TINYLLAMA_MODEL_DIR") or DEFAULT_MODEL_ID
TEST_DEVICE = os.getenv("TINYLLAMA_TEST_DEVICE", "auto")
MIN_TOKENS_PER_SEC = float(os.getenv("TINYLLAMA_MIN_TOKENS_PER_SEC", "1.0"))
MAX_MEDIAN_LATENCY_S = float(os.getenv("TINYLLAMA_MAX_MEDIAN_LATENCY_S", "45.0"))


@pytest.fixture(scope="module")
def tinyllama_engine() -> TinyLlamaLocalEngine:
    try:
        return TinyLlamaLocalEngine(model_source=MODEL_SOURCE, device=TEST_DEVICE)
    except LocalLlmError as exc:
        pytest.skip(f"TinyLlama local model is unavailable: {exc}")


def test_tinyllama_speed_budget(tinyllama_engine: TinyLlamaLocalEngine) -> None:
    prompt = "Summarize a city council decision process in two short sentences."

    tinyllama_engine.generate(
        prompt=prompt,
        max_new_tokens=64,
        temperature=0.0,
        seed=11,
    )

    measured_runs = []
    for run_index in range(3):
        measured_runs.append(
            tinyllama_engine.generate(
                prompt=prompt,
                max_new_tokens=64,
                temperature=0.0,
                seed=200 + run_index,
            )
        )

    median_latency = statistics.median(result.latency_s for result in measured_runs)
    median_tokens_per_s = statistics.median(result.tokens_per_s for result in measured_runs)

    assert median_tokens_per_s >= MIN_TOKENS_PER_SEC, (
        "TinyLlama median throughput is below the configured threshold: "
        f"{median_tokens_per_s:.3f} < {MIN_TOKENS_PER_SEC:.3f}"
    )
    assert median_latency <= MAX_MEDIAN_LATENCY_S, (
        "TinyLlama median latency exceeds the configured threshold: "
        f"{median_latency:.3f} > {MAX_MEDIAN_LATENCY_S:.3f}"
    )
