from __future__ import annotations

import argparse
import time
from typing import Any

import httpx

try:
    from .common import RAG_EVAL_ROOT, append_jsonl, read_jsonl, sanitize_model_name
except ImportError:  # pragma: no cover - direct script execution
    from common import RAG_EVAL_ROOT, append_jsonl, read_jsonl, sanitize_model_name


DEFAULT_MODELS = "qwen3.5:122b,hrbrmstr/jamba:latest,dicta-il/DictaLM-3.0-24B-Thinking:bf16"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run each Hebrew LLM through the local RAG /ask endpoint")
    parser.add_argument("--models", default=DEFAULT_MODELS, help="Comma-separated Ollama model names")
    parser.add_argument("--eval-set", default=str(RAG_EVAL_ROOT / "data" / "eval_set.jsonl"))
    parser.add_argument("--runs-dir", default=str(RAG_EVAL_ROOT / "runs"))
    parser.add_argument("--ask-url", default="http://127.0.0.1:8000/ask")
    parser.add_argument("--retrieve-url", default="http://127.0.0.1:8000/ask/debug/retrieval")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--source-types", default="protocol", help="Comma-separated source types, or empty for all")
    parser.add_argument("--retrieval-strategy", default="auto")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--resume", action="store_true", help="Skip eval rows already present in the model output file")
    args = parser.parse_args()

    eval_rows = read_jsonl(_path(args.eval_set))
    if not eval_rows:
        raise SystemExit("eval set is empty; run generate_eval_set.py first")

    models = [part.strip() for part in args.models.split(",") if part.strip()]
    if not models:
        raise SystemExit("no models provided")

    source_types = [part.strip() for part in args.source_types.split(",") if part.strip()] or None
    runs_dir = _path(args.runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=args.timeout_seconds) as client:
        for model_name in models:
            output_path = runs_dir / f"{sanitize_model_name(model_name)}.jsonl"
            completed_eval_ids = _completed_eval_ids(output_path) if args.resume else set()
            if output_path.exists() and not args.resume:
                output_path.unlink()
            for index, eval_row in enumerate(eval_rows, start=1):
                eval_id = str(eval_row.get("id") or "")
                if eval_id and eval_id in completed_eval_ids:
                    print(f"model={model_name} {index}/{len(eval_rows)} eval_id={eval_id} status=skipped_resume")
                    continue
                run_row = _run_one(
                    client=client,
                    eval_row=eval_row,
                    model_name=model_name,
                    ask_url=args.ask_url,
                    retrieve_url=args.retrieve_url,
                    top_k=args.top_k,
                    source_types=source_types,
                    retrieval_strategy=args.retrieval_strategy,
                )
                append_jsonl(output_path, run_row)
                print(f"model={model_name} {index}/{len(eval_rows)} eval_id={eval_row.get('id')} status={run_row.get('status')}")

    return 0


def _run_one(
    *,
    client: httpx.Client,
    eval_row: dict[str, Any],
    model_name: str,
    ask_url: str,
    retrieve_url: str,
    top_k: int,
    source_types: list[str] | None,
    retrieval_strategy: str,
) -> dict[str, Any]:
    question = str(eval_row.get("question_he") or "")
    retrieval_payload = {
        "question": question,
        "top_k": max(5, top_k),
        "source_types": source_types,
        "retrieval_strategy": retrieval_strategy,
        "debug_mode": True,
    }
    ask_payload = {
        "question": question,
        "model_name": model_name,
        "top_k": top_k,
        "source_types": source_types,
        "retrieval_strategy": retrieval_strategy,
        "debug_mode": True,
        "disable_answer_cache": True,
    }

    retrieval_response: dict[str, Any] = {}
    retrieval_error = None
    try:
        retrieval_http = client.post(retrieve_url, json=retrieval_payload)
        retrieval_http.raise_for_status()
        payload = retrieval_http.json()
        retrieval_response = payload if isinstance(payload, dict) else {}
    except Exception as exc:  # noqa: BLE001
        retrieval_error = f"{exc.__class__.__name__}:{exc}"

    started = time.perf_counter()
    ask_response: dict[str, Any] = {}
    ask_error = None
    try:
        ask_http = client.post(ask_url, json=ask_payload)
        ask_http.raise_for_status()
        payload = ask_http.json()
        ask_response = payload if isinstance(payload, dict) else {}
    except Exception as exc:  # noqa: BLE001
        ask_error = f"{exc.__class__.__name__}:{exc}"
    latency_seconds = round(time.perf_counter() - started, 4)

    retrieved_chunks = retrieval_response.get("results") if isinstance(retrieval_response.get("results"), list) else []
    citations = ask_response.get("citations") if isinstance(ask_response.get("citations"), list) else []
    return {
        "eval_id": eval_row.get("id"),
        "model_name": model_name,
        "question_he": question,
        "status": ask_response.get("status") or ("error" if ask_error else "unknown"),
        "answer": ask_response.get("answer"),
        "extended_answer": ask_response.get("extended_answer"),
        "retrieved_chunk_ids": [str(row.get("chunk_id")) for row in retrieved_chunks if isinstance(row, dict) and row.get("chunk_id")],
        "retrieved_chunks": retrieved_chunks,
        "citations": citations,
        "cited_chunk_ids": [str(row.get("chunk_id")) for row in citations if isinstance(row, dict) and row.get("chunk_id")],
        "latency_seconds": latency_seconds,
        "retrieval_error": retrieval_error,
        "ask_error": ask_error,
        "raw_retrieval_response": retrieval_response,
        "raw_ask_response": ask_response,
    }


def _path(value: str):
    from pathlib import Path

    return Path(value)


def _completed_eval_ids(output_path) -> set[str]:
    return {str(row.get("eval_id")) for row in read_jsonl(output_path) if row.get("eval_id")}


if __name__ == "__main__":
    raise SystemExit(main())
