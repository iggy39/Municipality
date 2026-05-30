from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

try:
    from .common import RAG_EVAL_ROOT, as_bool, ollama_chat_json, quote_match_status, read_jsonl, write_jsonl
except ImportError:  # pragma: no cover - direct script execution
    from common import RAG_EVAL_ROOT, as_bool, ollama_chat_json, quote_match_status, read_jsonl, write_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Hebrew municipal RAG eval rows before benchmarking")
    parser.add_argument("--eval-set", default=str(RAG_EVAL_ROOT / "data" / "eval_set.jsonl"))
    parser.add_argument("--chunks", default=str(RAG_EVAL_ROOT / "data" / "chunks.jsonl"))
    parser.add_argument("--output", default=str(RAG_EVAL_ROOT / "data" / "eval_set.validated.jsonl"))
    parser.add_argument("--report", default=str(RAG_EVAL_ROOT / "data" / "eval_set.validation_report.jsonl"))
    parser.add_argument("--llm-validate", action="store_true", help="Use a local judge to validate expected_answer_he semantics")
    parser.add_argument("--validator-model", default="qwen3.5:122b")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--keep-invalid", action="store_true", help="Write invalid rows too, marked with validation errors")
    args = parser.parse_args()

    eval_rows = read_jsonl(Path(args.eval_set))
    chunks_by_id = {str(row["chunk_id"]): row for row in read_jsonl(Path(args.chunks)) if row.get("chunk_id")}
    if not eval_rows:
        raise SystemExit("eval set is empty; run generate_eval_set.py first")
    if not chunks_by_id:
        raise SystemExit("chunks are empty; run extract_chunks.py first")

    validated_rows: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
    for row in eval_rows:
        validation = validate_one(
            row=row,
            chunks_by_id=chunks_by_id,
            seen_questions=seen_questions,
            llm_validate=args.llm_validate,
            validator_model=args.validator_model,
            ollama_base_url=args.ollama_base_url,
            timeout_seconds=args.timeout_seconds,
        )
        report_rows.append(validation)
        output_row = dict(row)
        output_row["quote_validation"] = validation["quote_validation"]
        output_row["expected_answer_validation"] = validation["expected_answer_validation"]
        output_row["label_source"] = _label_source(row=row, validation=validation)
        output_row["validation_errors"] = validation["errors"]
        if validation.get("semantic_validation") is not None:
            output_row["semantic_validation"] = validation["semantic_validation"]
        if validation["is_valid"] or args.keep_invalid:
            validated_rows.append(output_row)

    write_jsonl(Path(args.output), validated_rows)
    write_jsonl(Path(args.report), report_rows)
    invalid_count = len(report_rows) - sum(1 for row in report_rows if row["is_valid"])
    print(f"validated={len(validated_rows)} invalid={invalid_count} output={args.output} report={args.report}")
    return 0


def validate_one(
    *,
    row: dict[str, Any],
    chunks_by_id: dict[str, dict[str, Any]],
    seen_questions: set[str] | None = None,
    llm_validate: bool = False,
    validator_model: str = "qwen3.5:122b",
    ollama_base_url: str = "http://localhost:11434",
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    eval_id = str(row.get("id") or "")
    question = str(row.get("question_he") or "").strip()
    expected = str(row.get("expected_answer_he") or "").strip()
    required_quote = str(row.get("required_quote_he") or "").strip()
    source_chunk_ids = [str(value) for value in row.get("source_chunk_ids") or []]
    is_unanswerable = as_bool(row.get("is_unanswerable"))

    if not eval_id:
        errors.append("missing_id")
    if not question:
        errors.append("missing_question_he")
    if not expected:
        errors.append("missing_expected_answer_he")
    if not source_chunk_ids:
        errors.append("missing_source_chunk_ids")

    if seen_questions is not None and question:
        question_key = " ".join(question.split()).casefold()
        if question_key in seen_questions:
            errors.append("duplicate_question")
        seen_questions.add(question_key)

    source_chunks = [chunks_by_id.get(chunk_id) for chunk_id in source_chunk_ids]
    missing_chunk_ids = [chunk_id for chunk_id, chunk in zip(source_chunk_ids, source_chunks, strict=False) if chunk is None]
    if missing_chunk_ids:
        errors.append("missing_source_chunk")

    quote_validation = "empty"
    if is_unanswerable:
        if required_quote:
            errors.append("unanswerable_has_required_quote")
            quote_validation = _best_quote_match(required_quote=required_quote, source_chunks=source_chunks)
    else:
        if not required_quote:
            errors.append("answerable_missing_required_quote")
            quote_validation = "empty"
        else:
            quote_validation = _best_quote_match(required_quote=required_quote, source_chunks=source_chunks)
            if quote_validation == "failed":
                errors.append("required_quote_not_in_source_chunks")
            elif quote_validation == "normalized":
                warnings.append("required_quote_matches_after_whitespace_normalization")

    semantic_validation: dict[str, Any] | None = None
    expected_answer_validation = "unvalidated"
    if llm_validate and not errors:
        semantic_validation = _validate_expected_answer_with_llm(
            row=row,
            source_chunks=source_chunks,
            validator_model=validator_model,
            ollama_base_url=ollama_base_url,
            timeout_seconds=timeout_seconds,
        )
        if semantic_validation.get("validator_error"):
            warnings.append("llm_validation_failed")
        elif _semantic_validation_passed(semantic_validation):
            expected_answer_validation = "llm_validated"
        else:
            expected_answer_validation = "llm_rejected"
            errors.append("expected_answer_not_validated")

    return {
        "eval_id": eval_id,
        "is_valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "quote_validation": quote_validation,
        "expected_answer_validation": expected_answer_validation,
        "semantic_validation": semantic_validation,
    }


def _best_quote_match(*, required_quote: str, source_chunks: list[dict[str, Any] | None]) -> str:
    best_status = "failed"
    for chunk in source_chunks:
        if chunk is None:
            continue
        status = quote_match_status(quote=required_quote, text=str(chunk.get("text") or ""))
        if status == "exact":
            return status
        if status == "normalized":
            best_status = status
    return best_status


def _validate_expected_answer_with_llm(
    *,
    row: dict[str, Any],
    source_chunks: list[dict[str, Any] | None],
    validator_model: str,
    ollama_base_url: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    source_text = "\n\n".join(str(chunk.get("text") or "") for chunk in source_chunks if chunk is not None)[:12000]
    prompt = (
        "You are validating one Hebrew municipal RAG evaluation row before benchmarking. "
        "Do not answer the question; only decide whether the provided label is valid.\n\n"
        f"Source chunk text:\n{source_text}\n\n"
        f"Question:\n{row.get('question_he')}\n\n"
        f"Expected answer:\n{row.get('expected_answer_he')}\n\n"
        f"Required quote:\n{row.get('required_quote_he') or ''}\n\n"
        f"Is unanswerable row: {bool(as_bool(row.get('is_unanswerable')))}\n\n"
        "Return valid JSON only with keys: question_answerable_from_chunk (boolean), "
        "expected_answer_supported_by_quote (boolean), answer_type_correct (boolean), "
        "is_ambiguous (boolean), reason_he (short Hebrew explanation)."
    )
    try:
        payload = ollama_chat_json(
            model=validator_model,
            base_url=ollama_base_url,
            timeout_seconds=timeout_seconds,
            temperature=0.0,
            messages=[
                {"role": "system", "content": "Validate Hebrew municipal RAG eval labels. Return JSON only."},
                {"role": "user", "content": prompt},
            ],
        )
    except Exception as exc:  # noqa: BLE001
        return {"validator_error": f"{exc.__class__.__name__}:{exc}"}
    return payload if isinstance(payload, dict) else {"validator_error": "non_object_response"}


def _semantic_validation_passed(validation: dict[str, Any]) -> bool:
    return (
        as_bool(validation.get("question_answerable_from_chunk"))
        and as_bool(validation.get("expected_answer_supported_by_quote"))
        and as_bool(validation.get("answer_type_correct"))
        and not as_bool(validation.get("is_ambiguous"))
    )


def _label_source(*, row: dict[str, Any], validation: dict[str, Any]) -> str:
    current = str(row.get("label_source") or "auto_generated")
    if current == "human_verified":
        return current
    if validation["expected_answer_validation"] == "llm_validated":
        return "llm_validated"
    return current


if __name__ == "__main__":
    raise SystemExit(main())
