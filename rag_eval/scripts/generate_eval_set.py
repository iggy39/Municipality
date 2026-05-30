from __future__ import annotations

import argparse
import random
import re
from typing import Any

try:
    from .common import RAG_EVAL_ROOT, ollama_chat_json, quote_match_status, read_jsonl, write_jsonl
except ImportError:  # pragma: no cover - direct script execution
    from common import RAG_EVAL_ROOT, ollama_chat_json, quote_match_status, read_jsonl, write_jsonl


ANSWER_TYPES = {
    "decision",
    "date",
    "address",
    "parcel_gush_helka",
    "plan_id",
    "condition",
    "budget_or_amount",
    "person_or_department",
    "summary",
    "unanswerable",
    "contradiction",
}
ANSWERABLE_TYPES = ANSWER_TYPES - {"unanswerable", "contradiction"}
DIFFICULTIES = {"easy", "medium", "hard"}
PRIORITY_TYPES = {"decision", "condition", "plan_id", "address", "unanswerable"}
HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
WHITESPACE_RE = re.compile(r"\s+")
MUNICIPAL_KEYWORDS = (
    "הוחלט",
    "מחליטה",
    "מחליטים",
    "מאשרת",
    "מאשרים",
    "אושר",
    "לאשר",
    "בתנאי",
    "בכפוף",
    "תנאי",
    "תכנית",
    "תוכנית",
    "תב\"ע",
    "גוש",
    "חלקה",
    "מגרש",
    "רחוב",
    "כתובת",
    "תקציב",
    "₪",
    "ש\"ח",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Hebrew municipal RAG eval set from chunk JSONL")
    parser.add_argument("--chunks", default=str(RAG_EVAL_ROOT / "data" / "chunks.jsonl"))
    parser.add_argument("--output", default=str(RAG_EVAL_ROOT / "data" / "eval_set.jsonl"))
    parser.add_argument("--generator-model", default="qwen3.5:122b")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--target-count", type=int, default=300)
    parser.add_argument("--final-count", type=int, default=200)
    parser.add_argument("--max-chunks", type=int, default=120)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--unanswerable-ratio", type=float, default=0.25)
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    args = parser.parse_args()

    chunks = [row for row in read_jsonl(RAG_EVAL_ROOT / "data" / "chunks.jsonl" if args.chunks is None else _path(args.chunks)) if _is_usable_chunk(row)]
    if not chunks:
        raise SystemExit("no usable chunks found; run extract_chunks.py first")

    selected_chunks = _select_chunks(chunks=chunks, max_chunks=args.max_chunks, seed=args.seed)
    generated: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
    target_unanswerable = max(0, int(args.target_count * max(0.0, min(0.8, args.unanswerable_ratio))))

    for chunk in selected_chunks:
        if len(generated) >= args.target_count:
            break
        generated.extend(
            _generate_answerable_rows(
                chunk=chunk,
                model=args.generator_model,
                base_url=args.ollama_base_url,
                timeout_seconds=args.timeout_seconds,
                seen_questions=seen_questions,
            )
        )
        if _count_type(generated, "unanswerable") < target_unanswerable and len(generated) < args.target_count:
            row = _generate_unanswerable_row(
                chunk=chunk,
                model=args.generator_model,
                base_url=args.ollama_base_url,
                timeout_seconds=args.timeout_seconds,
                seen_questions=seen_questions,
            )
            if row is not None:
                generated.append(row)

    final_rows = _finalize_rows(generated, final_count=args.final_count)
    write_jsonl(_path(args.output), final_rows)
    print(f"generated={len(generated)} kept={len(final_rows)} output={args.output}")
    return 0


def _generate_answerable_rows(
    *,
    chunk: dict[str, Any],
    model: str,
    base_url: str,
    timeout_seconds: float,
    seen_questions: set[str],
) -> list[dict[str, Any]]:
    text = str(chunk["text"])
    prompt = (
        "You are creating a Hebrew municipal RAG evaluation set.\n\n"
        "Given this municipal document chunk, create 3 evaluation questions.\n\n"
        "Rules:\n"
        "- Questions must be answerable ONLY from the given chunk.\n"
        "- Include the exact supporting Hebrew quote copied from the chunk.\n"
        "- Prefer municipal/legal/planning questions: decisions, conditions, addresses, plan IDs, dates, parcel references, budgets.\n"
        "- Avoid vague questions.\n"
        "- Preserve Hebrew legal/municipal terminology.\n"
        "- Output valid JSON only as an object with key items.\n\n"
        f"Chunk:\n{_trim(text, 3600)}\n\n"
        "Return JSON shape:\n"
        "{\"items\":[{\"question_he\":\"...\",\"expected_answer_he\":\"...\","
        "\"required_quote_he\":\"...\",\"answer_type\":\"decision|date|address|parcel_gush_helka|plan_id|condition|budget_or_amount|person_or_department|summary\","
        "\"difficulty\":\"easy|medium|hard\"}]}"
    )
    try:
        payload = ollama_chat_json(
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            temperature=0.0,
            messages=[
                {"role": "system", "content": "Create private Hebrew municipal RAG eval data. Return JSON only."},
                {"role": "user", "content": prompt},
            ],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"generation_failed chunk_id={chunk.get('chunk_id')} error={exc.__class__.__name__}:{exc}")
        return []

    rows: list[dict[str, Any]] = []
    for item in _items_from_payload(payload):
        row = _answerable_eval_row(chunk=chunk, item=item, seen_questions=seen_questions)
        if row is not None:
            rows.append(row)
    return rows


def _generate_unanswerable_row(
    *,
    chunk: dict[str, Any],
    model: str,
    base_url: str,
    timeout_seconds: float,
    seen_questions: set[str],
) -> dict[str, Any] | None:
    prompt = (
        "Create one Hebrew question that sounds plausible for this municipal document, "
        "but cannot be answered from the provided chunk.\n"
        "The question must not ask for information explicitly present in the chunk.\n"
        "The correct expected answer is exactly: לא נמצא מידע מספיק במסמכים שסופקו.\n"
        "Output valid JSON only as {\"question_he\":\"...\"}.\n\n"
        f"Chunk:\n{_trim(str(chunk['text']), 2800)}"
    )
    try:
        payload = ollama_chat_json(
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            temperature=0.0,
            messages=[
                {"role": "system", "content": "Create private Hebrew municipal RAG unanswerable eval data. Return JSON only."},
                {"role": "user", "content": prompt},
            ],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"unanswerable_generation_failed chunk_id={chunk.get('chunk_id')} error={exc.__class__.__name__}:{exc}")
        return None
    item = payload if isinstance(payload, dict) else {}
    question = str(item.get("question_he") or "").strip()
    if not question or _normalized(question) in seen_questions:
        return None
    seen_questions.add(_normalized(question))
    return {
        "id": "",
        "task_type": "unanswerable",
        "question_he": question,
        "expected_answer_he": "לא נמצא מידע מספיק במסמכים שסופקו.",
        "source_doc_id": str(chunk["doc_id"]),
        "source_chunk_ids": [str(chunk["chunk_id"])],
        "required_quote_he": "",
        "answer_type": "unanswerable",
        "difficulty": "medium",
        "is_unanswerable": True,
        "label_source": "auto_generated",
        "quote_validation": "empty",
        "expected_answer_validation": "unvalidated",
    }


def _answerable_eval_row(
    *,
    chunk: dict[str, Any],
    item: dict[str, Any],
    seen_questions: set[str],
) -> dict[str, Any] | None:
    question = str(item.get("question_he") or "").strip()
    answer = str(item.get("expected_answer_he") or "").strip()
    quote = str(item.get("required_quote_he") or "").strip()
    answer_type = str(item.get("answer_type") or "").strip()
    difficulty = str(item.get("difficulty") or "medium").strip()
    chunk_text = str(chunk["text"])
    if not question or not answer or not quote:
        return None
    if _normalized(question) in seen_questions:
        return None
    if answer_type not in ANSWERABLE_TYPES:
        return None
    if difficulty not in DIFFICULTIES:
        difficulty = "medium"
    quote_validation = quote_match_status(quote=quote, text=chunk_text)
    if quote_validation == "failed":
        return None
    seen_questions.add(_normalized(question))
    return {
        "id": "",
        "task_type": "qa",
        "question_he": question,
        "expected_answer_he": answer,
        "source_doc_id": str(chunk["doc_id"]),
        "source_chunk_ids": [str(chunk["chunk_id"])],
        "required_quote_he": quote,
        "answer_type": answer_type,
        "difficulty": difficulty,
        "is_unanswerable": False,
        "label_source": "auto_generated",
        "quote_validation": quote_validation,
        "expected_answer_validation": "unvalidated",
    }


def _finalize_rows(rows: list[dict[str, Any]], *, final_count: int) -> list[dict[str, Any]]:
    sorted_rows = sorted(rows, key=_row_rank)
    kept: list[dict[str, Any]] = []
    unanswerable_cap = max(1, int(final_count * 0.3)) if final_count else 0
    unanswerable_count = 0
    for row in sorted_rows:
        if len(kept) >= final_count:
            break
        if row["answer_type"] == "unanswerable":
            if unanswerable_count >= unanswerable_cap:
                continue
            unanswerable_count += 1
        kept.append(dict(row))
    for index, row in enumerate(kept, start=1):
        row["id"] = f"eval_{index:04d}"
    return kept


def _select_chunks(*, chunks: list[dict[str, Any]], max_chunks: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    shuffled = list(chunks)
    rng.shuffle(shuffled)
    shuffled.sort(key=lambda row: _chunk_score(str(row.get("text") or "")), reverse=True)
    return shuffled[: max(1, max_chunks)]


def _chunk_score(text: str) -> int:
    return sum(2 if keyword in {"הוחלט", "מחליטים", "מאשרת", "לאשר", "בתנאי", "בכפוף"} else 1 for keyword in MUNICIPAL_KEYWORDS if keyword in text)


def _is_usable_chunk(row: dict[str, Any]) -> bool:
    text = str(row.get("text") or "").strip()
    return len(text) >= 180 and bool(HEBREW_RE.search(text)) and bool(str(row.get("chunk_id") or "").strip())


def _items_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return [payload]
    return []


def _row_rank(row: dict[str, Any]) -> tuple[int, int, str]:
    answer_type = str(row.get("answer_type") or "")
    return (0 if answer_type in PRIORITY_TYPES else 1, 0 if answer_type != "summary" else 1, str(row.get("question_he") or ""))


def _count_type(rows: list[dict[str, Any]], answer_type: str) -> int:
    return sum(1 for row in rows if row.get("answer_type") == answer_type)


def _normalized(value: str) -> str:
    return WHITESPACE_RE.sub(" ", value).strip().casefold()


def _trim(value: str, limit: int) -> str:
    compact = value.strip()
    return compact if len(compact) <= limit else compact[:limit].rsplit(" ", 1)[0]


def _path(value: str):
    from pathlib import Path

    return Path(value)


if __name__ == "__main__":
    raise SystemExit(main())
