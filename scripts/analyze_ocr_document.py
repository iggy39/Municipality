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


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import httpx  # noqa: E402

from municipality.qwen_ocr import (  # noqa: E402
    DEFAULT_QWEN_OCR_MODEL,
    DEFAULT_QWEN_OCR_OLLAMA_BASE_URL,
    DEFAULT_QWEN_OCR_TIMEOUT_SECONDS,
)


THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

PASS_SPECS: dict[str, dict[str, str]] = {
    "sections": {
        "suffix": ".sections.json",
        "instruction": """
Build a validated section outline for this Hebrew municipal protocol OCR.
Analyze ONLY the supplied page or page chunk. Be concise.
Return only JSON with this shape:
{
  "document_title_candidate": string|null,
  "sections": [
    {
      "section_id": "stable short id",
      "parent_section_id": string|null,
      "level": integer,
      "section_type": "document_title|metadata_block|participants|agenda_item|discussion|decision|vote|embedded_protocol|table_section|signature_block|appendix|other",
      "heading": string,
      "page_start": integer,
      "page_end": integer,
      "summary": string|null,
      "evidence": [{"page_number": integer, "quote": string}],
      "confidence": number
    }
  ]
}
Reject OCR noise as headings. Do not build the whole document hierarchy in this pass.
""".strip(),
    },
    "tables": {
        "suffix": ".tables.json",
        "instruction": """
Normalize all tables and table-like lists from this Hebrew municipal protocol OCR.
Analyze ONLY the supplied page or page chunk. Be concise.
Use both page text and OCR tables_markdown.
Return only JSON with this shape:
{
  "tables": [
    {
      "table_id": "stable short id",
      "page_start": integer,
      "page_end": integer,
      "caption": string|null,
      "table_type": "participants|absent|present|budget|decision_amounts|votes|signatures|other",
      "columns": [string],
      "rows": [[string]],
      "normalized_rows": [object],
      "evidence": [{"page_number": integer, "quote": string}],
      "confidence": number
    }
  ]
}
Keep raw cell text. Normalize names, roles, amounts, dates, and decision numbers when clear.
""".strip(),
    },
    "metadata_facts": {
        "suffix": ".metadata_facts.json",
        "instruction": """
Extract evidence-backed metadata facts from this Hebrew municipal protocol OCR.
Analyze ONLY the supplied page or page chunk. Be concise.
Return only JSON with this shape:
{
  "facts": [
    {
      "key": string,
      "value": string|number|boolean|null,
      "raw_value": string|null,
      "fact_type": "document|meeting|person|organization|decision|budget|legal|place|land|other",
      "page_number": integer|null,
      "evidence_quote": string,
      "confidence": number
    }
  ],
  "canonical": {
    "municipality": string|null,
    "document_kind": string|null,
    "protocol_number": string|null,
    "meeting_date": string|null,
    "meeting_time": string|null,
    "meeting_location": string|null,
    "committee_or_body": string|null
  }
}
Every fact must be grounded in a quote. Use null when evidence is missing.
""".strip(),
    },
    "topics_entities": {
        "suffix": ".topics_entities.json",
        "instruction": """
Extract a topic and entity graph from this Hebrew municipal protocol OCR.
Analyze ONLY the supplied page or page chunk. Be concise.
Return only JSON with this shape:
{
  "topics": [
    {
      "topic_id": "stable short id",
      "label_he": string,
      "parent_topic_id": string|null,
      "topic_type": "governance|budget|education|transport|welfare|planning_land|culture|sports|taxation|legal|operations|other",
      "page_numbers": [integer],
      "evidence": [{"page_number": integer, "quote": string}],
      "confidence": number
    }
  ],
  "entities": [
    {
      "entity_id": "stable short id",
      "label_he": string,
      "entity_type": "person|role|municipal_body|organization|place|project|budget_item|legal_reference|land_parcel|other",
      "aliases": [string],
      "page_numbers": [integer],
      "evidence": [{"page_number": integer, "quote": string}],
      "confidence": number
    }
  ],
  "links": [
    {"source_id": string, "target_id": string, "relation": string, "confidence": number}
  ]
}
Prefer specific useful labels over generic labels like protocol, committee, item, discussion.
""".strip(),
    },
    "quality_report": {
        "suffix": ".quality_report.json",
        "instruction": """
Assess OCR and extraction quality for this Hebrew municipal protocol OCR.
Be concise. Do not produce long explanations.
Return only JSON with this shape:
{
  "overall_quality": number,
  "page_quality": [
    {
      "page_number": integer,
      "quality": number,
      "issues": [string],
      "uncertain_regions": [object],
      "notes": string|null
    }
  ],
  "document_issues": [string],
  "recommended_followups": [string]
}
Focus on OCR errors, broken Hebrew order, missing table structure, suspicious hallucinations, and uncertain text. Limit each page notes field to 20 words.
Quality scores MUST be on a 0-100 scale. Use 95 for excellent, never 0.95. Use 85 for good with issues, never 0.85.
""".strip(),
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Qwen OCR JSONL into structured sidecars")
    parser.add_argument("ocr_jsonl", help="Path to <pdf>.ocr.jsonl")
    parser.add_argument("--output-dir", help="Directory for analysis sidecars. Default: OCR JSONL directory")
    parser.add_argument("--prefix", help="Output filename prefix. Default: OCR JSONL stem without .ocr")
    parser.add_argument("--passes", default="all", help="Comma-separated passes or all")
    parser.add_argument("--model", default=os.getenv("QWEN_ANALYSIS_MODEL") or os.getenv("QWEN_OCR_MODEL") or DEFAULT_QWEN_OCR_MODEL)
    parser.add_argument("--ollama-base-url", default=os.getenv("QWEN_ANALYSIS_OLLAMA_BASE_URL") or os.getenv("OLLAMA_BASE_URL") or DEFAULT_QWEN_OCR_OLLAMA_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=_env_float(os.getenv("QWEN_ANALYSIS_TIMEOUT_SECONDS"), DEFAULT_QWEN_OCR_TIMEOUT_SECONDS))
    parser.add_argument("--chunk-pages", type=int, default=1, help="Pages per model call (default: 1)")
    parser.add_argument("--ocr-pages", help="Only analyze these OCR pages, for example: 1,3-5")
    parser.add_argument("--retries", type=int, default=1, help="Retries per failed chunk (default: 1)")
    parser.add_argument("--num-predict", type=int, default=2048, help="Ollama max generated tokens per chunk (default: 2048)")
    parser.add_argument("--max-page-chars", type=int, default=1400, help="Max OCR chars per page sent to analysis model")
    parser.add_argument("--chunk-wall-timeout-seconds", type=float, default=120.0, help="Abort a chunk after this many seconds")
    parser.add_argument("--progress-interval-seconds", type=float, default=5.0, help="Print stream progress at this interval")
    parser.add_argument("--fallback-only", action="store_true", help="Do not call Qwen; produce deterministic sidecars from OCR JSONL")
    parser.add_argument("--force", action="store_true", help="Overwrite completed sidecars and partial files")
    args = parser.parse_args()

    ocr_jsonl_path = Path(args.ocr_jsonl).expanduser().resolve()
    if not ocr_jsonl_path.exists():
        print(f"OCR JSONL not found: {ocr_jsonl_path}", file=sys.stderr)
        return 1

    pages = _load_ocr_pages(ocr_jsonl_path)
    if args.ocr_pages:
        selected_page_numbers = set(_parse_page_range(args.ocr_pages, page_count=max(int(page.get("page_number") or 0) for page in pages)))
        pages = [page for page in pages if int(page.get("page_number") or 0) in selected_page_numbers]
    if not pages:
        print(f"No OCR page records found: {ocr_jsonl_path}", file=sys.stderr)
        return 1

    selected_passes = _selected_passes(args.passes)
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else ocr_jsonl_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix or _default_prefix(ocr_jsonl_path)
    base_url = str(args.ollama_base_url).rstrip("/")

    print(
        (
            f"Analyze {ocr_jsonl_path} pages={len(pages)} passes={selected_passes} "
            f"model={args.model} timeout={args.timeout_seconds}s chunk_pages={args.chunk_pages}"
        ),
        file=sys.stderr,
        flush=True,
    )
    print(f"Ollama URL: {base_url}", file=sys.stderr, flush=True)

    failures = 0
    for pass_name in selected_passes:
        spec = PASS_SPECS[pass_name]
        output_path = output_dir / f"{prefix}{spec['suffix']}"
        partial_path = output_dir / f"{prefix}{spec['suffix']}.partial.jsonl"
        if output_path.exists() and not args.force and _sidecar_completed(output_path):
            print(f"\n[{pass_name}] skip existing {output_path} (use --force to overwrite)", file=sys.stderr, flush=True)
            continue
        if args.force:
            partial_path.unlink(missing_ok=True)
        started = time.perf_counter()
        chunks = list(_page_chunks(pages, chunk_size=max(1, args.chunk_pages)))
        completed_chunks: list[dict[str, Any]] = []
        existing_completed = _latest_chunk_records(_load_completed_partials(partial_path))
        completed_keys = {
            str(item.get("chunk_key") or "")
            for item in existing_completed
            if item.get("status") in {"completed", "fallback"}
        }
        completed_chunks.extend(existing_completed)
        print(f"\n[{pass_name}] chunks={len(chunks)} partial={partial_path}", file=sys.stderr, flush=True)
        for chunk_index, chunk_pages in enumerate(chunks, start=1):
            chunk_key = _chunk_key(chunk_pages)
            if chunk_key in completed_keys:
                print(f"[{pass_name}] chunk {chunk_index}/{len(chunks)} pages={chunk_key} skip partial", file=sys.stderr, flush=True)
                continue
            chunk_started = time.perf_counter()
            request_payload = _build_request_payload(
                pass_name=pass_name,
                instruction=spec["instruction"],
                pages=chunk_pages,
                max_page_chars=max(500, args.max_page_chars),
            )
            request_chars = len(json.dumps(request_payload, ensure_ascii=False))
            if args.fallback_only:
                chunk_elapsed = time.perf_counter() - chunk_started
                chunk_record = {
                    "status": "fallback",
                    "pass_name": pass_name,
                    "chunk_key": chunk_key,
                    "page_numbers": [page.get("page_number") for page in chunk_pages],
                    "elapsed_seconds": round(chunk_elapsed, 3),
                    "error_text": "FALLBACK_ONLY",
                    "result": _fallback_chunk_result(pass_name=pass_name, pages=chunk_pages, error_text="FALLBACK_ONLY"),
                }
                completed_chunks.append(chunk_record)
                _append_jsonl(partial_path, chunk_record)
                print(f"[{pass_name}] chunk {chunk_index}/{len(chunks)} pages={chunk_key} wrote deterministic fallback", file=sys.stderr, flush=True)
                continue
            print(
                f"[{pass_name}] chunk {chunk_index}/{len(chunks)} pages={chunk_key} request_chars={request_chars} calling model",
                file=sys.stderr,
                flush=True,
            )
            raw_content = None
            error_text = None
            for attempt in range(1, max(1, args.retries) + 1):
                if attempt > 1:
                    print(f"[{pass_name}] chunk pages={chunk_key} retry {attempt}/{args.retries}", file=sys.stderr, flush=True)
                raw_content, error_text = _call_ollama_json_streaming(
                    base_url=base_url,
                    model=args.model,
                    payload=request_payload,
                    timeout_seconds=args.timeout_seconds,
                    wall_timeout_seconds=args.chunk_wall_timeout_seconds,
                    num_predict=args.num_predict,
                    progress_interval_seconds=args.progress_interval_seconds,
                    label=f"{pass_name}:{chunk_key}:attempt{attempt}",
                )
                if not error_text:
                    break
            chunk_elapsed = time.perf_counter() - chunk_started
            if error_text:
                fallback_result = _fallback_chunk_result(pass_name=pass_name, pages=chunk_pages, error_text=error_text)
                chunk_record = {
                    "status": "fallback",
                    "pass_name": pass_name,
                    "chunk_key": chunk_key,
                    "page_numbers": [page.get("page_number") for page in chunk_pages],
                    "elapsed_seconds": round(chunk_elapsed, 3),
                    "error_text": error_text,
                    "result": fallback_result,
                }
                completed_chunks.append(chunk_record)
                print(
                    f"[{pass_name}] chunk pages={chunk_key} used fallback in {chunk_elapsed:.1f}s: {error_text}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                parsed = _parse_json_object(_clean_model_content(raw_content or ""))
                if parsed is None:
                    fallback_result = _fallback_chunk_result(pass_name=pass_name, pages=chunk_pages, error_text="MODEL_INVALID_JSON")
                    chunk_record = {
                        "status": "fallback",
                        "pass_name": pass_name,
                        "chunk_key": chunk_key,
                        "page_numbers": [page.get("page_number") for page in chunk_pages],
                        "elapsed_seconds": round(chunk_elapsed, 3),
                        "error_text": "MODEL_INVALID_JSON",
                        "raw_response": raw_content,
                        "result": fallback_result,
                    }
                    completed_chunks.append(chunk_record)
                    print(f"[{pass_name}] chunk pages={chunk_key} used fallback after invalid JSON in {chunk_elapsed:.1f}s", file=sys.stderr, flush=True)
                else:
                    chunk_record = {
                        "status": "completed",
                        "pass_name": pass_name,
                        "chunk_key": chunk_key,
                        "page_numbers": [page.get("page_number") for page in chunk_pages],
                        "elapsed_seconds": round(chunk_elapsed, 3),
                        "result": parsed,
                    }
                    completed_chunks.append(chunk_record)
                    print(f"[{pass_name}] chunk pages={chunk_key} completed in {chunk_elapsed:.1f}s", file=sys.stderr, flush=True)
            _append_jsonl(partial_path, chunk_record)
        elapsed = time.perf_counter() - started
        all_records = _latest_chunk_records(_load_completed_partials(partial_path))
        pass_failed = any(item.get("status") not in {"completed", "fallback"} for item in all_records)
        fallback_count = sum(1 for item in all_records if item.get("status") == "fallback")
        result_payload = {
            "status": "failed" if pass_failed else "completed",
            "pass_name": pass_name,
            "model": args.model,
            "source_ocr_jsonl": str(ocr_jsonl_path),
            "elapsed_seconds": round(elapsed, 3),
            "chunk_pages": max(1, args.chunk_pages),
            "partial_jsonl": str(partial_path),
            "fallback_chunks": fallback_count,
            "chunks": all_records,
            "merged_result": _merge_chunk_results(pass_name=pass_name, chunks=all_records),
        }
        print(f"[{pass_name}] {'failed' if pass_failed else 'completed'} in {elapsed:.1f}s", file=sys.stderr, flush=True)
        output_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{pass_name}] wrote {output_path}", file=sys.stderr, flush=True)

    return 2 if failures else 0


def _load_ocr_pages(path: Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        compact = raw_line.strip()
        if not compact:
            continue
        try:
            parsed = json.loads(compact)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSONL at line {line_number}: {exc}") from exc
        if isinstance(parsed, dict):
            pages.append(parsed)
    pages.sort(key=lambda row: int(row.get("page_number") or 0))
    return pages


def _parse_page_range(value: str, *, page_count: int) -> list[int]:
    pages: list[int] = []
    for part in value.split(","):
        compact = part.strip()
        if not compact:
            continue
        if "-" in compact:
            start_raw, end_raw = compact.split("-", 1)
            start = int(start_raw)
            end = int(end_raw)
            pages.extend(range(start, end + 1))
        else:
            pages.append(int(compact))
    return sorted({page for page in pages if 1 <= page <= page_count})


def _selected_passes(value: str) -> list[str]:
    if not value or value.strip().casefold() == "all":
        return list(PASS_SPECS.keys())
    selected = []
    for raw in value.split(","):
        pass_name = raw.strip()
        if not pass_name:
            continue
        if pass_name not in PASS_SPECS:
            raise SystemExit(f"Unsupported pass: {pass_name}; expected one of {', '.join(PASS_SPECS)}")
        selected.append(pass_name)
    return selected or list(PASS_SPECS.keys())


def _default_prefix(path: Path) -> str:
    name = path.name
    if name.endswith(".ocr.jsonl"):
        return name[: -len(".ocr.jsonl")]
    return path.stem


def _page_chunks(pages: list[dict[str, Any]], *, chunk_size: int) -> list[list[dict[str, Any]]]:
    return [pages[index : index + chunk_size] for index in range(0, len(pages), chunk_size)]


def _chunk_key(pages: list[dict[str, Any]]) -> str:
    page_numbers = [int(page.get("page_number") or 0) for page in pages]
    if not page_numbers:
        return "empty"
    if len(page_numbers) == 1:
        return str(page_numbers[0])
    return f"{page_numbers[0]}-{page_numbers[-1]}"


def _load_completed_partials(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        compact = raw_line.strip()
        if not compact:
            continue
        try:
            parsed = json.loads(compact)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def _latest_chunk_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in records:
        key = str(record.get("chunk_key") or "")
        if not key:
            continue
        if key not in latest_by_key:
            order.append(key)
        latest_by_key[key] = record
    return [latest_by_key[key] for key in order if key in latest_by_key]


def _sidecar_completed(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "completed"


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")


def _merge_chunk_results(*, pass_name: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [chunk for chunk in chunks if chunk.get("status") in {"completed", "fallback"} and isinstance(chunk.get("result"), dict)]
    if pass_name == "sections":
        sections: list[Any] = []
        document_title = None
        for chunk in completed:
            result = chunk.get("result") or {}
            if document_title is None:
                document_title = result.get("document_title")
            if isinstance(result.get("sections"), list):
                sections.extend(result["sections"])
        return {"document_title": document_title, "sections": sections}
    if pass_name == "tables":
        tables: list[Any] = []
        for chunk in completed:
            result = chunk.get("result") or {}
            if isinstance(result.get("tables"), list):
                tables.extend(result["tables"])
        return {"tables": tables}
    if pass_name == "metadata_facts":
        facts: list[Any] = []
        canonical: dict[str, Any] = {}
        for chunk in completed:
            result = chunk.get("result") or {}
            if isinstance(result.get("facts"), list):
                facts.extend(result["facts"])
            if isinstance(result.get("canonical"), dict):
                for key, value in result["canonical"].items():
                    if value is not None and canonical.get(key) is None:
                        canonical[key] = value
        return {"facts": facts, "canonical": canonical}
    if pass_name == "topics_entities":
        topics: list[Any] = []
        entities: list[Any] = []
        links: list[Any] = []
        for chunk in completed:
            result = chunk.get("result") or {}
            if isinstance(result.get("topics"), list):
                topics.extend(result["topics"])
            if isinstance(result.get("entities"), list):
                entities.extend(result["entities"])
            if isinstance(result.get("links"), list):
                links.extend(result["links"])
        return {"topics": topics, "entities": entities, "links": links}
    if pass_name == "quality_report":
        page_quality: list[Any] = []
        document_issues: list[Any] = []
        recommended_followups: list[Any] = []
        qualities: list[float] = []
        for chunk in completed:
            result = chunk.get("result") or {}
            if isinstance(result.get("page_quality"), list):
                normalized_page_quality = _normalize_page_quality_items(result["page_quality"])
                page_quality.extend(normalized_page_quality)
                for item in normalized_page_quality:
                    if isinstance(item, dict) and isinstance(item.get("quality"), int | float):
                        qualities.append(float(item["quality"]))
            if isinstance(result.get("document_issues"), list):
                document_issues.extend(result["document_issues"])
            if isinstance(result.get("recommended_followups"), list):
                recommended_followups.extend(result["recommended_followups"])
            quality = result.get("overall_quality")
            if not result.get("page_quality") and isinstance(quality, int | float):
                qualities.append(_normalize_quality_score(float(quality)))
        return {
            "overall_quality": round(sum(qualities) / len(qualities), 4) if qualities else None,
            "page_quality": page_quality,
            "document_issues": _dedupe(document_issues),
            "recommended_followups": _dedupe(recommended_followups),
        }
    return {"chunks": [chunk.get("result") for chunk in completed]}


def _normalize_page_quality_items(items: list[Any]) -> list[Any]:
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        copy = dict(item)
        quality = copy.get("quality")
        if isinstance(quality, int | float):
            copy["quality"] = _normalize_quality_score(float(quality))
        normalized.append(copy)
    return normalized


def _normalize_quality_score(value: float) -> float:
    if 0 <= value <= 1:
        return round(value * 100, 4)
    return round(value, 4)


def _fallback_chunk_result(*, pass_name: str, pages: list[dict[str, Any]], error_text: str) -> dict[str, Any]:
    if pass_name == "sections":
        sections = []
        title_candidate = None
        for page in pages:
            page_number = int(page.get("page_number") or 0)
            headings = _fallback_headings(page)
            if title_candidate is None and headings:
                title_candidate = headings[0]
            for index, heading in enumerate(headings, start=1):
                sections.append(
                    {
                        "section_id": f"p{page_number}_h{index}",
                        "parent_section_id": None,
                        "level": _fallback_heading_level(heading),
                        "section_type": _fallback_section_type(heading),
                        "heading": heading,
                        "page_start": page_number,
                        "page_end": page_number,
                        "summary": None,
                        "evidence": [{"page_number": page_number, "quote": heading}],
                        "confidence": 0.45,
                        "fallback_reason": error_text,
                    }
                )
        return {"document_title_candidate": title_candidate, "sections": sections}
    if pass_name == "tables":
        tables = []
        for page in pages:
            page_number = int(page.get("page_number") or 0)
            for index, table in enumerate(page.get("tables_markdown") or [], start=1):
                rows = _markdown_table_rows(str(table))
                tables.append(
                    {
                        "table_id": f"p{page_number}_t{index}",
                        "page_start": page_number,
                        "page_end": page_number,
                        "caption": None,
                        "table_type": "other",
                        "columns": rows[0] if rows else [],
                        "rows": rows[1:] if len(rows) > 1 else rows,
                        "normalized_rows": [],
                        "evidence": [{"page_number": page_number, "quote": str(table)[:500]}],
                        "confidence": 0.4,
                        "fallback_reason": error_text,
                    }
                )
        return {"tables": tables}
    if pass_name == "metadata_facts":
        facts = []
        canonical: dict[str, Any] = {}
        for page in pages:
            page_number = int(page.get("page_number") or 0)
            text = str(page.get("text") or "")
            for key, value in _fallback_metadata_values(text).items():
                canonical.setdefault(key, value)
                facts.append(
                    {
                        "key": key,
                        "value": value,
                        "raw_value": value,
                        "fact_type": "document" if key in {"municipality", "protocol_number"} else "meeting",
                        "page_number": page_number,
                        "evidence_quote": _quote_around(text, str(value)),
                        "confidence": 0.5,
                        "fallback_reason": error_text,
                    }
                )
        return {"facts": facts, "canonical": canonical}
    if pass_name == "topics_entities":
        topics = []
        entities = []
        for page in pages:
            page_number = int(page.get("page_number") or 0)
            text = str(page.get("text") or "")
            for index, topic in enumerate(_fallback_topics(text), start=1):
                topics.append(
                    {
                        "topic_id": f"p{page_number}_topic{index}",
                        "label_he": topic,
                        "parent_topic_id": None,
                        "topic_type": "other",
                        "page_numbers": [page_number],
                        "evidence": [{"page_number": page_number, "quote": _quote_around(text, topic)}],
                        "confidence": 0.4,
                        "fallback_reason": error_text,
                    }
                )
            for index, entity in enumerate(_fallback_entities(text), start=1):
                entities.append(
                    {
                        "entity_id": f"p{page_number}_entity{index}",
                        "label_he": entity,
                        "entity_type": "person",
                        "aliases": [],
                        "page_numbers": [page_number],
                        "evidence": [{"page_number": page_number, "quote": _quote_around(text, entity)}],
                        "confidence": 0.35,
                        "fallback_reason": error_text,
                    }
                )
        return {"topics": topics, "entities": entities, "links": []}
    if pass_name == "quality_report":
        page_quality = []
        for page in pages:
            page_number = int(page.get("page_number") or 0)
            page_quality.append(
                {
                    "page_number": page_number,
                    "quality": page.get("ocr_confidence") or 0.5,
                    "issues": ["llm_quality_pass_fallback"],
                    "uncertain_regions": page.get("uncertain_regions") or [],
                    "notes": f"Fallback quality report because model did not complete: {error_text}",
                }
            )
        return {
            "overall_quality": None,
            "page_quality": page_quality,
            "document_issues": ["Some quality chunks used deterministic fallback"],
            "recommended_followups": ["Review fallback chunks manually or rerun with larger budget"],
        }
    return {"fallback_reason": error_text}


def _fallback_headings(page: dict[str, Any]) -> list[str]:
    headings = [str(item).strip() for item in page.get("detected_headings") or [] if str(item).strip()]
    text = str(page.get("text") or "")
    for raw_line in text.splitlines():
        line = raw_line.strip().strip("#* ")
        if not line or len(line) > 180:
            continue
        if raw_line.lstrip().startswith("#") or line.startswith(("החלטה", "החלטת", "פרוטוקול", "תנחומים", "ברכות")):
            headings.append(line)
        elif re.match(r"^\d+[.)]\s+", line):
            headings.append(line)
    return _dedupe(headings)[:20]


def _fallback_heading_level(heading: str) -> int:
    if heading.startswith("פרוטוקול"):
        return 1
    if re.match(r"^\d+[.)]\s+", heading):
        return 3
    if heading.startswith(("החלטה", "החלטת")):
        return 4
    return 2


def _fallback_section_type(heading: str) -> str:
    if heading.startswith(("החלטה", "החלטת")):
        return "decision"
    if re.match(r"^\d+[.)]\s+", heading):
        return "agenda_item"
    if "השתתפו" in heading or "נכחו" in heading or "נעדרו" in heading:
        return "participants"
    if "פרוטוקול" in heading:
        return "embedded_protocol"
    return "other"


def _markdown_table_rows(value: str) -> list[list[str]]:
    rows = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", cell or "") for cell in cells):
            continue
        rows.append(cells)
    return rows


def _fallback_metadata_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if "עיריית אשדוד" in text:
        values["municipality"] = "אשדוד"
    protocol_match = re.search(r"פרוטוקול\s+מס['׳]?\s*([\d/]+)", text)
    if protocol_match:
        values["protocol_number"] = protocol_match.group(1)
    date_match = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})", text)
    if date_match:
        day, month, year = date_match.groups()
        year_value = int(year)
        if year_value < 100:
            year_value += 2000
        values["meeting_date"] = f"{year_value:04d}-{int(month):02d}-{int(day):02d}"
    time_match = re.search(r"שעה\s+(\d{1,2}[.:]\d{2})", text)
    if time_match:
        values["meeting_time"] = time_match.group(1).replace(".", ":")
    if "מועצת העיר" in text:
        values["committee_or_body"] = "מועצת העיר"
    return values


def _fallback_topics(text: str) -> list[str]:
    candidates = []
    keywords = ["חוף", "תקציב", "תב\"ר", "גמלאות", "תמיכות", "ארנונה", "ועדת ערר", "תאגיד הספורט", "תרבות תורנית"]
    for keyword in keywords:
        if keyword in text:
            candidates.append(keyword)
    return candidates[:10]


def _fallback_entities(text: str) -> list[str]:
    names = re.findall(r"(?:מר|גב'|ד\"ר|הרב|עו\"ד)\s+[\u0590-\u05FF'\"׳״\-]+(?:\s+[\u0590-\u05FF'\"׳״\-]+){0,2}", text)
    return _dedupe([name.strip() for name in names])[:20]


def _quote_around(text: str, value: str, *, radius: int = 80) -> str:
    if not value:
        return text[: radius * 2].strip()
    index = text.find(value)
    if index < 0:
        return text[: radius * 2].strip()
    return text[max(0, index - radius) : index + len(value) + radius].strip()


def _dedupe(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    out: list[Any] = []
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _build_request_payload(
    *,
    pass_name: str,
    instruction: str,
    pages: list[dict[str, Any]],
    max_page_chars: int,
) -> dict[str, Any]:
    compact_pages = []
    for page in pages:
        compact_pages.append(_page_payload_for_pass(pass_name=pass_name, page=page, max_page_chars=max_page_chars))
    return {
        "pass_name": pass_name,
        "instruction": instruction,
        "rules": [
            "Use thinking internally but final answer must be JSON only.",
            "Keep internal thinking very brief. Do not deliberate at length.",
            "This is a page-local chunk pass, not a final document-level reconciliation pass.",
            "Use only the supplied OCR evidence.",
            "Every extracted item must include page-level evidence where requested.",
            "Do not invent missing fields; use null or empty arrays when evidence is missing.",
        ],
        "ocr_pages": compact_pages,
    }


def _page_payload_for_pass(*, pass_name: str, page: dict[str, Any], max_page_chars: int) -> dict[str, Any]:
    text = page.get("text") or page.get("markdown_layout") or page.get("plain_text") or ""
    text = _trim_text(str(text), limit=max_page_chars)
    common = {
        "page_number": page.get("page_number"),
        "text": text,
        "detected_headings": page.get("detected_headings") or [],
        "uncertain_regions": page.get("uncertain_regions") or [],
        "quality_notes": page.get("quality_notes"),
        "ocr_confidence": page.get("ocr_confidence"),
        "error_code": page.get("error_code"),
    }
    if pass_name == "tables":
        return {
            **common,
            "tables_markdown": page.get("tables_markdown") or [],
        }
    if pass_name == "quality_report":
        return common
    if pass_name == "sections":
        return common
    if pass_name == "metadata_facts":
        return common
    if pass_name == "topics_entities":
        return common
    return common


def _trim_text(value: str, *, limit: int) -> str:
    compact = value.strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit].rstrip()}\n...[TRUNCATED_FOR_ANALYSIS]"


def _call_ollama_json_streaming(
    *,
    base_url: str,
    model: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    wall_timeout_seconds: float,
    num_predict: int,
    progress_interval_seconds: float,
    label: str,
) -> tuple[str | None, str | None]:
    system_prompt = "/think\nYou analyze one small Hebrew municipal OCR chunk. Think briefly, then return strict concise JSON only."
    body = _ollama_body(model=model, payload=payload, stream=True, json_format=True, num_predict=num_predict)
    content, error = _stream_ollama_body(
        base_url=base_url,
        body=body,
        timeout_seconds=timeout_seconds,
        wall_timeout_seconds=wall_timeout_seconds,
        progress_interval_seconds=progress_interval_seconds,
        label=label,
    )
    if content or error != "MODEL_EMPTY_RESPONSE":
        return content, error

    print(f"[{label}] empty JSON-mode response; retrying without Ollama format=json", file=sys.stderr, flush=True)
    fallback_body = _ollama_body(model=model, payload=payload, stream=True, json_format=False, num_predict=num_predict)
    return _stream_ollama_body(
        base_url=base_url,
        body=fallback_body,
        timeout_seconds=timeout_seconds,
        wall_timeout_seconds=wall_timeout_seconds,
        progress_interval_seconds=progress_interval_seconds,
        label=f"{label}:fallback",
    )


def _ollama_body(*, model: str, payload: dict[str, Any], stream: bool, json_format: bool, num_predict: int) -> dict[str, Any]:
    system_prompt = "/think\nYou analyze Hebrew municipal OCR. Think briefly, then return strict concise JSON only."
    body: dict[str, Any] = {
        "model": model,
        "stream": stream,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "options": {"temperature": 0, "num_predict": max(128, int(num_predict))},
    }
    if json_format:
        body["format"] = "json"
    return body


def _stream_ollama_body(
    *,
    base_url: str,
    body: dict[str, Any],
    timeout_seconds: float,
    wall_timeout_seconds: float,
    progress_interval_seconds: float,
    label: str,
) -> tuple[str | None, str | None]:
    content_parts: list[str] = []
    thinking_chars = 0
    first_chunk_seen = False
    started = time.perf_counter()
    try:
        timeout = httpx.Timeout(timeout_seconds, connect=30.0, read=timeout_seconds, write=60.0, pool=30.0)
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", f"{base_url}/api/chat", json=body) as response:
                response.raise_for_status()
                last_progress = time.perf_counter()
                for line in response.iter_lines():
                    elapsed = time.perf_counter() - started
                    if elapsed > wall_timeout_seconds:
                        return None, f"CHUNK_WALL_TIMEOUT:{elapsed:.1f}s"
                    if not line:
                        now = time.perf_counter()
                        if now - last_progress >= progress_interval_seconds:
                            print(f"[{label}] waiting... elapsed={now - started:.1f}s", file=sys.stderr, flush=True)
                            last_progress = now
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not first_chunk_seen:
                        first_chunk_seen = True
                        print(f"[{label}] first response chunk after {time.perf_counter() - started:.1f}s", file=sys.stderr, flush=True)
                    message = event.get("message") if isinstance(event, dict) else None
                    chunk = message.get("content") if isinstance(message, dict) else None
                    thinking_chunk = message.get("thinking") if isinstance(message, dict) else None
                    if isinstance(thinking_chunk, str) and thinking_chunk:
                        thinking_chars += len(thinking_chunk)
                        now = time.perf_counter()
                        if thinking_chars % 1000 < len(thinking_chunk) or now - last_progress >= progress_interval_seconds:
                            print(
                                f"[{label}] thinking_chars={thinking_chars} content_chars={sum(len(part) for part in content_parts)} elapsed={now - started:.1f}s",
                                file=sys.stderr,
                                flush=True,
                            )
                            last_progress = now
                    if isinstance(chunk, str) and chunk:
                        content_parts.append(chunk)
                        total_chars = sum(len(part) for part in content_parts)
                        now = time.perf_counter()
                        if total_chars % 1000 < len(chunk) or now - last_progress >= progress_interval_seconds:
                            print(f"[{label}] received_chars={total_chars} elapsed={now - started:.1f}s", file=sys.stderr, flush=True)
                            last_progress = now
                    if event.get("done") is True:
                        break
    except Exception as exc:  # noqa: BLE001
        return None, f"{exc.__class__.__name__}:{exc}"

    content = "".join(content_parts).strip()
    if content:
        return content, None
    if thinking_chars:
        return None, f"MODEL_NO_FINAL_CONTENT:thinking_chars={thinking_chars}"
    return None, "MODEL_EMPTY_RESPONSE"


def _clean_model_content(value: str) -> str:
    without_think = THINK_BLOCK_RE.sub("", value).strip()
    return FENCE_RE.sub("", without_think).strip()


def _parse_json_object(value: str) -> dict[str, Any] | None:
    compact = value.strip()
    if not compact:
        return None
    try:
        parsed = json.loads(compact)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = compact.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(compact)):
        char = compact[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(compact[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _env_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return max(1.0, float(value))
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
