from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

try:
    import torch
except ImportError:
    torch = None

try:
    from transformers import AutoModelForCausalLM, AutoTokenizer
except ImportError:
    AutoModelForCausalLM = None
    AutoTokenizer = None


DEFAULT_MODEL_ID = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
DEFAULT_SYSTEM_PROMPT = "You are a concise and factual assistant."


class LocalLlmError(RuntimeError):
    """Raised when local model loading or generation fails."""


@dataclass(slots=True)
class LocalGenerationResult:
    model_source: str
    device: str
    prompt: str
    response_text: str
    latency_s: float
    tokens_out: int
    tokens_per_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_source": self.model_source,
            "device": self.device,
            "prompt": self.prompt,
            "response_text": self.response_text,
            "latency_s": round(self.latency_s, 6),
            "tokens_out": self.tokens_out,
            "tokens_per_s": round(self.tokens_per_s, 6),
        }


def runtime_dependencies_available() -> bool:
    return bool(torch is not None and AutoTokenizer is not None and AutoModelForCausalLM is not None)


def resolve_model_source(model_dir: str | None) -> str:
    compact = (model_dir or "").strip()
    if not compact:
        return DEFAULT_MODEL_ID
    if compact.startswith("~"):
        return str(Path(compact).expanduser())
    if compact.startswith(".") or compact.startswith("/") or "\\" in compact:
        return str(Path(compact).expanduser())
    if len(compact) >= 2 and compact[1] == ":":
        return str(Path(compact).expanduser())
    return compact


class TinyLlamaLocalEngine:
    def __init__(self, *, model_source: str | None = None, device: str = "auto"):
        _ensure_runtime_dependencies()
        self.model_source = resolve_model_source(model_source)
        self.device = _resolve_device(device)

        dtype = torch.float16 if self.device == "cuda" else torch.float32

        try:
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_source, local_files_only=True)
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_source,
                local_files_only=True,
                dtype=dtype,
            )
        except Exception as exc:
            message = (
                f"Could not load TinyLlama locally from '{self.model_source}'. "
                "Download model files first or set --model-dir to a local copy."
            )
            raise LocalLlmError(message) from exc

        self.model.to(self.device)
        self.model.eval()

        if self.tokenizer.pad_token_id is None and self.tokenizer.eos_token is not None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        pad_token_id = self.tokenizer.pad_token_id
        if pad_token_id is None:
            pad_token_id = self.tokenizer.eos_token_id
        if pad_token_id is None:
            raise LocalLlmError("Tokenizer is missing both pad_token_id and eos_token_id.")
        self._pad_token_id = int(pad_token_id)

    def generate(
        self,
        *,
        prompt: str,
        max_new_tokens: int = 96,
        temperature: float = 0.0,
        top_p: float = 1.0,
        seed: int = 42,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> LocalGenerationResult:
        compact_prompt = prompt.strip()
        if not compact_prompt:
            raise LocalLlmError("Prompt cannot be empty.")
        if max_new_tokens < 1:
            raise LocalLlmError("max_new_tokens must be >= 1.")
        if temperature < 0.0:
            raise LocalLlmError("temperature must be >= 0.0.")
        if not 0.0 < top_p <= 1.0:
            raise LocalLlmError("top_p must be in the range (0.0, 1.0].")

        _set_seed(seed)

        model_inputs = self._build_model_inputs(prompt=compact_prompt, system_prompt=system_prompt)
        if hasattr(model_inputs, "get") and "input_ids" in model_inputs:
            input_ids = model_inputs["input_ids"].to(self.device)
            attention_mask = model_inputs.get("attention_mask")
            if attention_mask is None:
                attention_mask = torch.ones_like(input_ids)
            else:
                attention_mask = attention_mask.to(self.device)
        elif torch.is_tensor(model_inputs):
            input_ids = model_inputs.to(self.device)
            attention_mask = torch.ones_like(input_ids)
        else:
            raise LocalLlmError("Tokenizer did not return usable input tensors.")

        generation_kwargs: dict[str, Any] = {
            "max_new_tokens": max_new_tokens,
            "pad_token_id": self._pad_token_id,
            "attention_mask": attention_mask,
            "do_sample": temperature > 0.0,
        }
        if temperature > 0.0:
            generation_kwargs["temperature"] = temperature
            generation_kwargs["top_p"] = top_p

        started = time.perf_counter()
        try:
            with torch.inference_mode():
                output_ids = self.model.generate(input_ids=input_ids, **generation_kwargs)
        except RuntimeError as exc:
            compact_error = str(exc).lower()
            if "out of memory" in compact_error:
                raise LocalLlmError(
                    "Generation failed due to out-of-memory. "
                    "Try fewer max_new_tokens or run with --device cpu."
                ) from exc
            raise LocalLlmError(f"Generation failed: {exc}") from exc

        latency_s = time.perf_counter() - started
        generated_tokens = output_ids[0][input_ids.shape[-1] :]
        tokens_out = int(generated_tokens.numel())
        response_text = self.tokenizer.decode(generated_tokens, skip_special_tokens=True).strip()
        tokens_per_s = float(tokens_out) / latency_s if latency_s > 0.0 else 0.0

        return LocalGenerationResult(
            model_source=self.model_source,
            device=self.device,
            prompt=compact_prompt,
            response_text=response_text,
            latency_s=latency_s,
            tokens_out=tokens_out,
            tokens_per_s=tokens_per_s,
        )

    def _build_model_inputs(self, *, prompt: str, system_prompt: str):
        messages = [
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": prompt},
        ]
        if hasattr(self.tokenizer, "apply_chat_template"):
            try:
                return self.tokenizer.apply_chat_template(
                    messages,
                    add_generation_prompt=True,
                    return_tensors="pt",
                )
            except Exception:
                pass

        fallback_prompt = f"System: {system_prompt.strip()}\nUser: {prompt}\nAssistant:"
        tokenized = self.tokenizer(fallback_prompt, return_tensors="pt")
        return tokenized["input_ids"]


def run_local_generation(
    *,
    prompt: str,
    model_dir: str | None = None,
    max_new_tokens: int = 96,
    temperature: float = 0.0,
    top_p: float = 1.0,
    device: str = "auto",
    seed: int = 42,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> LocalGenerationResult:
    engine = TinyLlamaLocalEngine(model_source=model_dir, device=device)
    return engine.generate(
        prompt=prompt,
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        top_p=top_p,
        seed=seed,
        system_prompt=system_prompt,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run TinyLlama locally via Hugging Face files.")
    parser.add_argument("--prompt", required=True, help="User prompt sent to TinyLlama.")
    parser.add_argument("--model-dir", default=None, help="Local model directory. If omitted, use cached model id.")
    parser.add_argument("--max-new-tokens", type=int, default=96, help="Maximum number of generated tokens.")
    parser.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature.")
    parser.add_argument("--top-p", type=float, default=1.0, help="Nucleus sampling top-p value.")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto", help="Inference device.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility.")
    parser.add_argument("--json", action="store_true", help="Print JSON output with text and metrics.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        result = run_local_generation(
            prompt=args.prompt,
            model_dir=args.model_dir,
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
            top_p=args.top_p,
            device=args.device,
            seed=args.seed,
        )
    except LocalLlmError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        return 0

    print(result.response_text)
    print(
        (
            f"[metrics] latency_s={result.latency_s:.3f} "
            f"tokens_out={result.tokens_out} tokens_per_s={result.tokens_per_s:.3f}"
        ),
        file=sys.stderr,
    )
    return 0


def _ensure_runtime_dependencies() -> None:
    if runtime_dependencies_available():
        return
    raise LocalLlmError("Missing dependencies. Install 'torch' and 'transformers' to run TinyLlama locally.")


def _resolve_device(device: str) -> str:
    normalized = (device or "auto").strip().casefold()
    if normalized not in {"auto", "cpu", "cuda"}:
        raise LocalLlmError(f"Unsupported device value: {device}")

    if normalized == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if normalized == "cuda" and not torch.cuda.is_available():
        raise LocalLlmError("CUDA device requested but torch.cuda.is_available() is False.")
    return normalized


def _set_seed(seed: int) -> None:
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


if __name__ == "__main__":
    raise SystemExit(main())
