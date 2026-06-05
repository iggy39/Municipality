#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx


DICTALM_MODEL = "dicta-il/DictaLM-3.0-24B-Thinking:bf16"
OLLAMA_BASE_URL = "http://localhost:11434"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build hybrid OCR text from Qwen OCR and native PDF text")
    parser.add_argument("--native-ocr-jsonl", required=True, help="Native-first OCR JSONL")
    parser.add_argument("--qwen-repaired-jsonl", required=True, help="Existing Qwen correction JSONL containing Qwen OCR original/repaired text")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--prefix", required=True, help="Output file prefix")
    parser.add_argument("--dictalm-model", default=os.getenv("DICTALM_MODEL") or DICTALM_MODEL)
    parser.add_argument("--ollama-base-url", default=os.getenv("OLLAMA_BASE_URL") or OLLAMA_BASE_URL)
    parser.add_argument("--dictalm-pages", default="", help="Comma/range pages to normalize from native text, e.g. 1,4-5")
    parser.add_argument("--chunk-chars", type=int, default=700, help="Approximate max chars per DictaLM normalization chunk")
    parser.add_argument("--timeout-seconds", type=float, default=300.0, help="HTTP timeout per DictaLM chunk")
    parser.add_argument("--num-predict", type=int, default=2048, help="Ollama max generated tokens per chunk")
    parser.add_argument("--skip-dictalm", action="store_true", help="Deprecated; DictaLM is required for requested normalization pages")
    parser.add_argument("--force", action="store_true", help="Overwrite outputs")
    args = parser.parse_args()

    native_path = Path(args.native_ocr_jsonl).expanduser().resolve()
    qwen_path = Path(args.qwen_repaired_jsonl).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    output_jsonl = output_dir / f"{args.prefix}.hybrid.ocr.jsonl"
    output_text = output_dir / f"{args.prefix}.hybrid.ocr.txt"
    report_path = output_dir / f"{args.prefix}.hybrid_source_report.json"
    for path in (output_jsonl, output_text, report_path):
        if path.exists() and not args.force:
            print(f"Output exists: {path} (use --force)", file=sys.stderr)
            return 1

    native_rows = _load_jsonl(native_path)
    qwen_rows = _load_jsonl(qwen_path)
    qwen_by_page = {int(row.get("page_number") or 0): row for row in qwen_rows}
    max_page = max(int(row.get("page_number") or 0) for row in native_rows)
    dictalm_pages = set(_parse_page_range(args.dictalm_pages, page_count=max_page)) if args.dictalm_pages else set()

    print(
        f"Build hybrid OCR pages={len(native_rows)} dictalm_pages={sorted(dictalm_pages)} model={args.dictalm_model}",
        file=sys.stderr,
        flush=True,
    )

    hybrid_rows: list[dict[str, Any]] = []
    source_report = {
        "native_ocr_jsonl": str(native_path),
        "qwen_repaired_jsonl": str(qwen_path),
        "output_jsonl": str(output_jsonl),
        "output_text": str(output_text),
        "dictalm_model": args.dictalm_model,
        "pages": [],
    }
    for native_row in native_rows:
        page_number = int(native_row.get("page_number") or 0)
        qwen_row = qwen_by_page.get(page_number) or {}
        qwen_text = str(qwen_row.get("repaired_text") or qwen_row.get("original_text") or "").strip()
        native_text = str(native_row.get("text") or "").strip()
        qwen_usable = _qwen_text_usable(qwen_text)
        if qwen_usable and page_number not in dictalm_pages:
            selected_text = qwen_text
            source_mode = "qwen_existing_text"
            normalization = {"status": "not_needed", "chunks": []}
        else:
            source_mode = "native_dictalm_normalized" if page_number in dictalm_pages else "native_text"
            selected_text, normalization = _normalize_native_text(
                text=native_text,
                page_number=page_number,
                model=args.dictalm_model,
                base_url=str(args.ollama_base_url).rstrip("/"),
                chunk_chars=max(300, args.chunk_chars),
                timeout_seconds=max(1.0, args.timeout_seconds),
                num_predict=max(256, args.num_predict),
                skip_dictalm=args.skip_dictalm or page_number not in dictalm_pages,
            )
        hybrid_row = dict(native_row)
        hybrid_row["text"] = selected_text
        hybrid_row["plain_text"] = selected_text
        hybrid_row["markdown_layout"] = selected_text
        hybrid_row["tables_markdown"] = qwen_row.get("tables_markdown") or native_row.get("tables_markdown") or []
        hybrid_row["detected_headings"] = _headings_from_text(selected_text)
        hybrid_row["uncertain_regions"] = native_row.get("uncertain_regions") or []
        hybrid_row["quality_notes"] = f"Hybrid OCR selected {source_mode}."
        hybrid_row["ocr_confidence"] = 0.97 if selected_text else None
        hybrid_row["parse_warning"] = None
        hybrid_row["error_code"] = None if selected_text else "HYBRID_EMPTY_TEXT"
        hybrid_row["error_text"] = None if selected_text else "No selected OCR text"
        hybrid_row["parsed_payload"] = {
            "source_mode": source_mode,
            "qwen_existing_chars": len(qwen_text),
            "native_chars": len(native_text),
            "dictalm_normalization": normalization,
        }
        hybrid_rows.append(hybrid_row)
        source_report["pages"].append(
            {
                "page_number": page_number,
                "selected_source": source_mode,
                "selected_chars": len(selected_text),
                "qwen_existing_chars": len(qwen_text),
                "qwen_usable": qwen_usable,
                "native_chars": len(native_text),
                "normalization_status": normalization.get("status"),
                "normalization_chunks": len(normalization.get("chunks") or []),
            }
        )
        print(
            f"[page {page_number}] selected={source_mode} chars={len(selected_text)} qwen_chars={len(qwen_text)} native_chars={len(native_text)} norm={normalization.get('status')}",
            file=sys.stderr,
            flush=True,
        )

    output_jsonl.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in hybrid_rows) + "\n", encoding="utf-8")
    output_text.write_text("\n".join(f"\n===== PAGE {row['page_number']} =====\n{row.get('text') or ''}" for row in hybrid_rows), encoding="utf-8")
    report_path.write_text(json.dumps(source_report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Hybrid OCR JSONL: {output_jsonl}")
    print(f"Hybrid OCR text: {output_text}")
    print(f"Hybrid source report: {report_path}")
    return 0


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
        if isinstance(payload, dict):
            rows.append(payload)
    rows.sort(key=lambda row: int(row.get("page_number") or 0))
    return rows


def _parse_page_range(value: str, *, page_count: int) -> list[int]:
    pages = []
    for part in value.split(","):
        item = part.strip()
        if not item:
            continue
        if "-" in item:
            start, end = item.split("-", 1)
            pages.extend(range(int(start), int(end) + 1))
        else:
            pages.append(int(item))
    return sorted({page for page in pages if 1 <= page <= page_count})


def _qwen_text_usable(text: str) -> bool:
    if len(text) < 500:
        return False
    if text.lstrip().startswith("{") and "plain_text" in text[:200]:
        return False
    return True


def _normalize_native_text(
    *,
    text: str,
    page_number: int,
    model: str,
    base_url: str,
    chunk_chars: int,
    timeout_seconds: float,
    num_predict: int,
    skip_dictalm: bool,
) -> tuple[str, dict[str, Any]]:
    chunks = _text_chunks(text, chunk_chars=chunk_chars)
    normalized_chunks = []
    chunk_reports = []
    for index, chunk in enumerate(chunks, start=1):
        if skip_dictalm:
            raise RuntimeError("DICTALM_REQUIRED: --skip-dictalm is not supported for normalization pages")
        else:
            started = time.perf_counter()
            try:
                normalized = _call_dictalm_normalize(
                    text=chunk,
                    page_number=page_number,
                    chunk_index=index,
                    chunk_count=len(chunks),
                    model=model,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                    num_predict=num_predict,
                )
                status = "completed" if normalized else "empty"
                if not normalized:
                    raise RuntimeError("MODEL_EMPTY_NORMALIZED_TEXT")
                else:
                    error = None
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"DICTALM_NORMALIZATION_FAILED page={page_number} chunk={index}/{len(chunks)} "
                    f"error={exc.__class__.__name__}:{exc}"
                ) from exc
            chunk_reports.append(
                {
                    "chunk_index": index,
                    "status": status,
                    "chars": len(chunk),
                    "elapsed_seconds": round(time.perf_counter() - started, 3) if not skip_dictalm else 0,
                    "error": error,
                }
            )
        normalized_chunks.append(normalized.strip())
    completed = sum(1 for item in chunk_reports if item.get("status") == "completed")
    return "\n\n".join(part for part in normalized_chunks if part), {
        "status": "completed" if completed == len(chunk_reports) else "failed",
        "completed_chunks": completed,
        "chunks": chunk_reports,
    }


def _text_chunks(text: str, *, chunk_chars: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks = []
    current = []
    current_chars = 0
    for paragraph in paragraphs:
        if current and current_chars + len(paragraph) > chunk_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_chars = 0
        if len(paragraph) > chunk_chars:
            lines = paragraph.splitlines()
            for line in lines:
                if current and current_chars + len(line) > chunk_chars:
                    chunks.append("\n".join(current))
                    current = []
                    current_chars = 0
                current.append(line)
                current_chars += len(line) + 1
            continue
        current.append(paragraph)
        current_chars += len(paragraph) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text]


def _call_dictalm_normalize(
    *,
    text: str,
    page_number: int,
    chunk_index: int,
    chunk_count: int,
    model: str,
    base_url: str,
    timeout_seconds: float,
    num_predict: int,
) -> str:
    prompt = f"""/no_think
Normalize Hebrew PDF-extracted text into natural reading order.

Page: {page_number}
Chunk: {chunk_index}/{chunk_count}

The input may have Hebrew words in reverse order within each line because it came from embedded PDF text.

Rules:
- Preserve every fact, name, number, date, title, and speaker label.
- Restore natural Hebrew reading order.
- Fix only order/spacing/punctuation artifacts caused by PDF extraction.
- Do not summarize.
- Do not add or remove content.
- Keep tables as readable aligned text if present.
- Return normalized text only. No JSON. No markdown fence. No explanation.

TEXT:
{text}
""".strip()
    body = {
        "model": model,
        "stream": True,
        "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    timeout = httpx.Timeout(timeout_seconds, connect=30.0, read=timeout_seconds, write=60.0, pool=30.0)
    with httpx.Client(timeout=timeout) as client:
        with client.stream("POST", f"{base_url}/api/chat", json=body) as response:
            response.raise_for_status()
            content_parts: list[str] = []
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                payload = json.loads(raw_line)
                if not isinstance(payload, dict):
                    continue
                message = payload.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, str) and content:
                    content_parts.append(content)
    return _clean_model_text("".join(content_parts))


def _clean_model_text(value: str) -> str:
    text = value.strip()
    text = text.split("</think>")[-1].strip() if "</think>" in text else text
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    return re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()


def _headings_from_text(text: str) -> list[str]:
    headings = []
    for line in text.splitlines():
        value = line.strip().strip("#* ")
        if not value or len(value) > 180:
            continue
        if value.startswith(("סעיף", "החלט", "פרוטוקול", "ישיבת מועצה")) or value.endswith(":"):
            headings.append(value)
    return list(dict.fromkeys(headings))[:30]


if __name__ == "__main__":
    raise SystemExit(main())
