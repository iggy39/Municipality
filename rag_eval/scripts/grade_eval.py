from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

try:
    from .common import RAG_EVAL_ROOT, as_bool, as_float, clamp, ollama_chat_json, quote_match_status, read_jsonl, write_jsonl
except ImportError:  # pragma: no cover - direct script execution
    from common import RAG_EVAL_ROOT, as_bool, as_float, clamp, ollama_chat_json, quote_match_status, read_jsonl, write_jsonl


ABSTENTION_MARKERS = (
    "לא נמצא מידע מספיק",
    "אין מספיק מידע",
    "אין מספיק ראיות",
    "לא ניתן להשיב",
    "לא ניתן לענות",
    "לא אותר מידע",
    "לא נמצא במסמכים",
    "המסמכים שסופקו אינם כוללים",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Grade local Hebrew municipal RAG eval runs")
    parser.add_argument("--eval-set", default=str(RAG_EVAL_ROOT / "data" / "eval_set.jsonl"))
    parser.add_argument("--chunks", default=str(RAG_EVAL_ROOT / "data" / "chunks.jsonl"))
    parser.add_argument("--runs-dir", default=str(RAG_EVAL_ROOT / "runs"))
    parser.add_argument("--output", default=str(RAG_EVAL_ROOT / "results" / "graded.jsonl"))
    parser.add_argument("--judge-model", default="qwen3.5:122b")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--skip-llm-judge", action="store_true")
    args = parser.parse_args()

    eval_by_id = {str(row["id"]): row for row in read_jsonl(Path(args.eval_set)) if row.get("id")}
    chunks_by_id = {str(row["chunk_id"]): row for row in read_jsonl(Path(args.chunks)) if row.get("chunk_id")}
    if not eval_by_id:
        raise SystemExit("eval set is empty; run generate_eval_set.py first")

    graded_rows: list[dict[str, Any]] = []
    for run_path in sorted(Path(args.runs_dir).glob("*.jsonl")):
        for run_row in read_jsonl(run_path):
            eval_row = eval_by_id.get(str(run_row.get("eval_id") or ""))
            if eval_row is None:
                continue
            graded_rows.append(
                grade_one(
                    eval_row=eval_row,
                    run_row=run_row,
                    chunks_by_id=chunks_by_id,
                    judge_model=args.judge_model,
                    ollama_base_url=args.ollama_base_url,
                    timeout_seconds=args.timeout_seconds,
                    skip_llm_judge=args.skip_llm_judge,
                )
            )

    write_jsonl(Path(args.output), graded_rows)
    print(f"graded={len(graded_rows)} output={args.output}")
    return 0


def grade_one(
    *,
    eval_row: dict[str, Any],
    run_row: dict[str, Any],
    chunks_by_id: dict[str, dict[str, Any]],
    judge_model: str,
    ollama_base_url: str,
    timeout_seconds: float,
    skip_llm_judge: bool = False,
) -> dict[str, Any]:
    expected_chunk_ids = [str(value) for value in eval_row.get("source_chunk_ids") or []]
    retrieved_chunk_ids = [str(value) for value in run_row.get("retrieved_chunk_ids") or []]
    cited_chunk_ids = [str(value) for value in run_row.get("cited_chunk_ids") or []]
    required_quote = str(eval_row.get("required_quote_he") or "")
    is_unanswerable = as_bool(eval_row.get("is_unanswerable"))
    answer_text = _answer_text(run_row)

    retrieval_hit_at_5 = any(chunk_id in retrieved_chunk_ids[:5] for chunk_id in expected_chunk_ids)
    citation_match = _citation_contains_required_quote(
        required_quote=required_quote,
        cited_chunk_ids=cited_chunk_ids,
        chunks_by_id=chunks_by_id,
    )
    citation_correctness = citation_match in {"exact", "normalized", "empty"}
    abstention = _is_abstention(answer_text=answer_text, status=str(run_row.get("status") or ""))

    judge = _deterministic_judge_fallback(answer_text=answer_text, is_unanswerable=is_unanswerable, abstention=abstention)
    if not skip_llm_judge:
        judge = _judge_answer(
            eval_row=eval_row,
            run_row=run_row,
            chunks_by_id=chunks_by_id,
            judge_model=judge_model,
            ollama_base_url=ollama_base_url,
            timeout_seconds=timeout_seconds,
            fallback=judge,
        )

    judge_score = clamp(as_float(judge.get("score"), 0.0) / 5.0)
    answer_correctness = _score01(judge.get("answer_correctness"), judge_score)
    groundedness = _score01(judge.get("groundedness"), judge_score)
    answer_completeness = _score01(judge.get("answer_completeness"), judge_score)
    no_hallucination = not as_bool(judge.get("is_hallucination"))

    if is_unanswerable:
        final_score = (0.70 * float(abstention)) + (0.30 * float(no_hallucination))
    else:
        final_score = (
            0.30 * answer_correctness
            + 0.25 * groundedness
            + 0.20 * float(citation_correctness)
            + 0.15 * float(retrieval_hit_at_5)
            + 0.10 * float(no_hallucination)
        )

    return {
        "eval_id": eval_row.get("id"),
        "model_name": run_row.get("model_name"),
        "question_he": eval_row.get("question_he"),
        "task_type": eval_row.get("task_type") or eval_row.get("answer_type"),
        "expected_answer_he": eval_row.get("expected_answer_he"),
        "model_answer": answer_text,
        "source_doc_id": eval_row.get("source_doc_id"),
        "source_chunk_ids": expected_chunk_ids,
        "retrieved_chunk_ids": retrieved_chunk_ids,
        "cited_chunk_ids": cited_chunk_ids,
        "required_quote_he": required_quote,
        "answer_type": eval_row.get("answer_type"),
        "difficulty": eval_row.get("difficulty"),
        "is_unanswerable": is_unanswerable,
        "status": run_row.get("status"),
        "metrics": {
            "retrieval_hit_at_5": bool(retrieval_hit_at_5),
            "answer_correctness": round(answer_correctness, 4),
            "citation_correctness": bool(citation_correctness),
            "citation_quote_match": citation_match,
            "groundedness": round(groundedness, 4),
            "no_hallucination": bool(no_hallucination),
            "abstention_when_unanswerable": bool(abstention) if is_unanswerable else None,
            "answer_completeness": round(answer_completeness, 4),
            "latency_seconds": as_float(run_row.get("latency_seconds"), 0.0),
            "final_score": round(clamp(final_score), 4),
        },
        "judge": judge,
        "errors": {
            "retrieval_error": run_row.get("retrieval_error"),
            "ask_error": run_row.get("ask_error"),
        },
    }


def _judge_answer(
    *,
    eval_row: dict[str, Any],
    run_row: dict[str, Any],
    chunks_by_id: dict[str, dict[str, Any]],
    judge_model: str,
    ollama_base_url: str,
    timeout_seconds: float,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    retrieved_context = _retrieved_context(run_row=run_row, chunks_by_id=chunks_by_id)
    prompt = (
        "You are grading a Hebrew municipal RAG answer.\n"
        "Grade legal/municipal factuality strictly. Punish unsupported legal claims, wrong citations, invented addresses, "
        "invented plan IDs, invented dates, and confident answers when context is insufficient.\n\n"
        f"Question:\n{eval_row.get('question_he')}\n\n"
        f"Task type:\n{eval_row.get('task_type') or eval_row.get('answer_type') or 'qa'}\n\n"
        f"Expected answer:\n{eval_row.get('expected_answer_he')}\n\n"
        f"Required supporting quote:\n{eval_row.get('required_quote_he') or ''}\n\n"
        f"Model answer:\n{_answer_text(run_row)}\n\n"
        f"Retrieved context:\n{retrieved_context}\n\n"
        "Return valid JSON only with keys: score (0-5), answer_correctness (0-5), groundedness (0-5), "
        "answer_completeness (0-5), is_hallucination (boolean), citation_valid (boolean), reason_he (short Hebrew explanation)."
    )
    try:
        payload = ollama_chat_json(
            model=judge_model,
            base_url=ollama_base_url,
            timeout_seconds=timeout_seconds,
            temperature=0.0,
            messages=[
                {"role": "system", "content": "Grade Hebrew municipal RAG answers. Return JSON only."},
                {"role": "user", "content": prompt},
            ],
        )
    except Exception as exc:  # noqa: BLE001
        fallback = dict(fallback)
        fallback["judge_error"] = f"{exc.__class__.__name__}:{exc}"
        return fallback
    if not isinstance(payload, dict):
        return fallback
    merged = dict(fallback)
    merged.update(payload)
    return merged


def _deterministic_judge_fallback(*, answer_text: str, is_unanswerable: bool, abstention: bool) -> dict[str, Any]:
    if not answer_text.strip():
        return {
            "score": 0,
            "answer_correctness": 0,
            "groundedness": 0,
            "answer_completeness": 0,
            "is_hallucination": not is_unanswerable,
            "citation_valid": False,
            "reason_he": "לא התקבלה תשובה לבדיקה.",
        }
    if is_unanswerable and abstention:
        return {
            "score": 5,
            "answer_correctness": 5,
            "groundedness": 5,
            "answer_completeness": 5,
            "is_hallucination": False,
            "citation_valid": True,
            "reason_he": "התשובה נמנעת כראוי בשאלה שאינה ניתנת למענה.",
        }
    return {
        "score": 2,
        "answer_correctness": 2,
        "groundedness": 2,
        "answer_completeness": 2,
        "is_hallucination": False,
        "citation_valid": False,
        "reason_he": "ציון ברירת מחדל עד להשלמת שיפוט LLM מקומי.",
    }


def _citation_contains_required_quote(
    *,
    required_quote: str,
    cited_chunk_ids: list[str],
    chunks_by_id: dict[str, dict[str, Any]],
) -> str:
    if not required_quote:
        return "empty"
    best_status = "failed"
    for chunk_id in cited_chunk_ids:
        chunk = chunks_by_id.get(chunk_id)
        if not chunk:
            continue
        status = quote_match_status(quote=required_quote, text=str(chunk.get("text") or ""))
        if status == "exact":
            return status
        if status == "normalized":
            best_status = status
    return best_status


def _is_abstention(*, answer_text: str, status: str) -> bool:
    if status.strip().casefold() == "refusal":
        return True
    return any(marker in answer_text for marker in ABSTENTION_MARKERS)


def _answer_text(run_row: dict[str, Any]) -> str:
    for key in ("answer", "extended_answer"):
        value = run_row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raw = run_row.get("raw_ask_response")
    if isinstance(raw, dict):
        refusal = raw.get("refusal")
        if isinstance(refusal, dict) and isinstance(refusal.get("message_he"), str):
            return refusal["message_he"].strip()
    return ""


def _retrieved_context(*, run_row: dict[str, Any], chunks_by_id: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for chunk_id in [str(value) for value in run_row.get("retrieved_chunk_ids") or []][:8]:
        chunk = chunks_by_id.get(chunk_id)
        text = str(chunk.get("text") or "") if chunk else ""
        if text:
            parts.append(f"chunk_id={chunk_id}\n{text[:1600]}")
    return "\n\n".join(parts)[:12000]


def _score01(value: Any, default: float) -> float:
    score = as_float(value, default)
    if score > 1.0:
        score = score / 5.0
    return clamp(score)


if __name__ == "__main__":
    raise SystemExit(main())
