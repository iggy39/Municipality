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

from municipality.qwen_ocr import DEFAULT_QWEN_OCR_MODEL, DEFAULT_QWEN_OCR_OLLAMA_BASE_URL  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair OCR text using OCR output plus quality suggestions")
    parser.add_argument("ocr_jsonl", help="Path to <pdf>.ocr.jsonl")
    parser.add_argument("--quality-report", help="Path to <pdf>.quality_report.json. Default: next to OCR JSONL")
    parser.add_argument("--output-dir", help="Output directory. Default: OCR JSONL directory")
    parser.add_argument("--prefix", help="Output prefix. Default: OCR JSONL stem without .ocr")
    parser.add_argument("--model", default=os.getenv("OCR_REPAIR_MODEL") or os.getenv("TEXT_REPAIR_MODEL") or DEFAULT_QWEN_OCR_MODEL)
    parser.add_argument("--ollama-base-url", default=os.getenv("OCR_REPAIR_OLLAMA_BASE_URL") or os.getenv("OLLAMA_BASE_URL") or DEFAULT_QWEN_OCR_OLLAMA_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=600.0, help="HTTP timeout per page")
    parser.add_argument("--num-predict", type=int, default=4096, help="Ollama max generated tokens per page")
    parser.add_argument("--pages", help="Only repair these pages, for example: 1,3-5")
    parser.add_argument("--fallback-only", action="store_true", help="Do not call a model; write original OCR text with suggestions attached")
    parser.add_argument("--deterministic-fallback", action="store_true", help="When the model fails, apply explicit typo suggestions conservatively")
    parser.add_argument("--force", action="store_true", help="Overwrite existing repaired output files")
    args = parser.parse_args()

    ocr_jsonl_path = Path(args.ocr_jsonl).expanduser().resolve()
    if not ocr_jsonl_path.exists():
        print(f"OCR JSONL not found: {ocr_jsonl_path}", file=sys.stderr)
        return 1

    prefix = args.prefix or _default_prefix(ocr_jsonl_path)
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else ocr_jsonl_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    quality_report_path = Path(args.quality_report).expanduser().resolve() if args.quality_report else output_dir / f"{prefix}.quality_report.json"
    if not quality_report_path.exists():
        fallback_quality = ocr_jsonl_path.parent / f"{prefix}.quality_report.json"
        quality_report_path = fallback_quality if fallback_quality.exists() else quality_report_path

    jsonl_output_path = output_dir / f"{prefix}.repaired.jsonl"
    text_output_path = output_dir / f"{prefix}.repaired.txt"
    if not args.force and (jsonl_output_path.exists() or text_output_path.exists()):
        print(f"Repaired outputs already exist: {jsonl_output_path}, {text_output_path} (use --force)", file=sys.stderr)
        return 1

    pages = _load_jsonl(ocr_jsonl_path)
    if args.pages:
        selected = set(_parse_page_range(args.pages, page_count=max(int(page.get("page_number") or 0) for page in pages)))
        pages = [page for page in pages if int(page.get("page_number") or 0) in selected]
    if not pages:
        print("No OCR pages selected", file=sys.stderr)
        return 1

    suggestions_by_page = _load_suggestions_by_page(quality_report_path) if quality_report_path.exists() else {}
    print(
        f"Repair OCR text pages={len(pages)} model={args.model} output_dir={output_dir} quality_report={quality_report_path if quality_report_path.exists() else 'NONE'}",
        file=sys.stderr,
        flush=True,
    )

    repaired_records: list[dict[str, Any]] = []
    with jsonl_output_path.open("w", encoding="utf-8") as jsonl_handle, text_output_path.open("w", encoding="utf-8") as text_handle:
        for index, page in enumerate(pages, start=1):
            page_number = int(page.get("page_number") or 0)
            suggestions = suggestions_by_page.get(page_number, [])
            print(f"[repair page {page_number}] {index}/{len(pages)} suggestions={len(suggestions)}", file=sys.stderr, flush=True)
            started = time.perf_counter()
            if args.fallback_only:
                if args.deterministic_fallback:
                    record = _deterministic_repair_record(page=page, suggestions=suggestions, reason="FALLBACK_ONLY")
                else:
                    record = _fallback_record(page=page, suggestions=suggestions, reason="FALLBACK_ONLY")
            else:
                record = _repair_page(
                    page=page,
                    suggestions=suggestions,
                    model=args.model,
                    base_url=str(args.ollama_base_url).rstrip("/"),
                    timeout_seconds=max(1.0, args.timeout_seconds),
                    num_predict=max(512, args.num_predict),
                    deterministic_fallback=args.deterministic_fallback,
                )
            record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            repaired_records.append(record)
            jsonl_handle.write(json.dumps(record, ensure_ascii=False))
            jsonl_handle.write("\n")
            text_handle.write(_format_text_page(record))
            text_handle.write("\n")
            print(
                (
                    f"[repair page {page_number}] status={record.get('status')} "
                    f"corrections={len(record.get('corrections') or [])} unresolved={len(record.get('unresolved_suggestions') or [])} "
                    f"elapsed={record['elapsed_seconds']:.1f}s"
                ),
                file=sys.stderr,
                flush=True,
            )

    summary_path = output_dir / f"{prefix}.repair_summary.json"
    summary = _build_summary(
        ocr_jsonl_path=ocr_jsonl_path,
        quality_report_path=quality_report_path if quality_report_path.exists() else None,
        jsonl_output_path=jsonl_output_path,
        text_output_path=text_output_path,
        model=args.model,
        records=repaired_records,
    )
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Repaired JSONL: {jsonl_output_path}")
    print(f"Repaired text: {text_output_path}")
    print(f"Repair summary: {summary_path}")
    print(f"Corrections applied: {summary['correction_count']} unresolved suggestions: {summary['unresolved_count']} fallback_pages: {summary['fallback_pages']}")
    return 0


def _default_prefix(path: Path) -> str:
    if path.name.endswith(".ocr.jsonl"):
        return path.name[: -len(".ocr.jsonl")]
    return path.stem


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
    pages: list[int] = []
    for part in value.split(","):
        compact = part.strip()
        if not compact:
            continue
        if "-" in compact:
            start_raw, end_raw = compact.split("-", 1)
            pages.extend(range(int(start_raw), int(end_raw) + 1))
        else:
            pages.append(int(compact))
    return sorted({page for page in pages if 1 <= page <= page_count})


def _load_suggestions_by_page(path: Path) -> dict[int, list[dict[str, Any]]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    merged = report.get("merged_result") if isinstance(report, dict) else None
    quality = merged if isinstance(merged, dict) else report
    suggestions: dict[int, list[dict[str, Any]]] = {}
    for page_quality in quality.get("page_quality") or []:
        if not isinstance(page_quality, dict):
            continue
        page_number = int(page_quality.get("page_number") or 0)
        page_suggestions = suggestions.setdefault(page_number, [])
        for region in page_quality.get("uncertain_regions") or []:
            if isinstance(region, dict):
                page_suggestions.append({"type": "uncertain_region", **region})
        for issue in page_quality.get("issues") or []:
            page_suggestions.append({"type": "issue", "text": str(issue), "reason": str(issue)})
    for followup in quality.get("recommended_followups") or []:
        for page_number in suggestions:
            if _mentions_any_suggestion(str(followup), suggestions[page_number]):
                suggestions[page_number].append({"type": "followup", "text": str(followup), "reason": str(followup)})
    return suggestions


def _mentions_any_suggestion(value: str, suggestions: list[dict[str, Any]]) -> bool:
    return any(str(item.get("text") or "") and str(item.get("text")) in value for item in suggestions)


def _repair_page(
    *,
    page: dict[str, Any],
    suggestions: list[dict[str, Any]],
    model: str,
    base_url: str,
    timeout_seconds: float,
    num_predict: int,
    deterministic_fallback: bool,
) -> dict[str, Any]:
    page_number = int(page.get("page_number") or 0)
    original_text = str(page.get("text") or page.get("plain_text") or "")
    payload = {
        "page_number": page_number,
        "task": "Produce a corrected final Hebrew OCR text from the raw OCR and quality suggestions.",
        "rules": [
            "Preserve the document meaning and order.",
            "Only change text when the suggestion is highly likely from context.",
            "Do not modernize spelling unless it fixes an OCR error.",
            "Do not invent missing content.",
            "Keep names unchanged when uncertain; list them in unresolved_suggestions.",
            "Return JSON only.",
        ],
        "expected_json_shape": {
            "page_number": page_number,
            "repaired_text": "string",
            "corrections": [{"original": "string", "replacement": "string", "reason": "string", "confidence": 0.0}],
            "unresolved_suggestions": [{"text": "string", "reason": "string"}],
            "warnings": ["string"],
        },
        "quality_suggestions": suggestions,
        "raw_ocr_text": original_text,
    }
    try:
        response = _call_ollama_json(base_url=base_url, model=model, payload=payload, timeout_seconds=timeout_seconds, num_predict=num_predict)
        repaired_text = str(response.get("repaired_text") or "").strip()
        if not repaired_text:
            return _fallback_record(page=page, suggestions=suggestions, reason="MODEL_EMPTY_REPAIRED_TEXT")
        return {
            "status": "completed",
            "page_number": page_number,
            "model": model,
            "original_text": original_text,
            "repaired_text": repaired_text,
            "corrections": response.get("corrections") if isinstance(response.get("corrections"), list) else [],
            "unresolved_suggestions": response.get("unresolved_suggestions") if isinstance(response.get("unresolved_suggestions"), list) else [],
            "warnings": response.get("warnings") if isinstance(response.get("warnings"), list) else [],
            "suggestions_input": suggestions,
        }
    except Exception as exc:  # noqa: BLE001
        if deterministic_fallback:
            return _deterministic_repair_record(page=page, suggestions=suggestions, reason=f"{exc.__class__.__name__}:{exc}")
        return _fallback_record(page=page, suggestions=suggestions, reason=f"{exc.__class__.__name__}:{exc}")


def _call_ollama_json(*, base_url: str, model: str, payload: dict[str, Any], timeout_seconds: float, num_predict: int) -> dict[str, Any]:
    content = _call_ollama_content(
        base_url=base_url,
        model=model,
        payload=payload,
        timeout_seconds=timeout_seconds,
        num_predict=num_predict,
        json_format=True,
    )
    if not content:
        content = _call_ollama_content(
            base_url=base_url,
            model=model,
            payload=payload,
            timeout_seconds=timeout_seconds,
            num_predict=num_predict,
            json_format=False,
        )
    if not content:
        raise ValueError("MODEL_EMPTY_RESPONSE")
    parsed = _parse_json_object(content)
    if parsed is None:
        raise ValueError("MODEL_INVALID_JSON")
    return parsed


def _call_ollama_content(
    *,
    base_url: str,
    model: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    num_predict: int,
    json_format: bool,
) -> str | None:
    body = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": "/no_think\nYou repair OCR text. Return strict JSON only. Do not include thinking."},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    if json_format:
        body["format"] = "json"
    timeout = httpx.Timeout(timeout_seconds, connect=30.0, read=timeout_seconds, write=60.0, pool=30.0)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(f"{base_url}/api/chat", json=body)
        response.raise_for_status()
    event = response.json()
    message = event.get("message") if isinstance(event, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    return content.strip() if isinstance(content, str) and content.strip() else None


def _parse_json_object(value: str) -> dict[str, Any] | None:
    compact = re.sub(r"<think>.*?</think>", "", value, flags=re.DOTALL | re.IGNORECASE).strip()
    compact = re.sub(r"^```(?:json)?\s*|\s*```$", "", compact, flags=re.IGNORECASE).strip()
    try:
        parsed = json.loads(compact)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = compact.find("{")
    end = compact.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(compact[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _fallback_record(*, page: dict[str, Any], suggestions: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    original_text = str(page.get("text") or page.get("plain_text") or "")
    return {
        "status": "fallback",
        "page_number": int(page.get("page_number") or 0),
        "model": None,
        "original_text": original_text,
        "repaired_text": original_text,
        "corrections": [],
        "unresolved_suggestions": suggestions,
        "warnings": [reason],
        "suggestions_input": suggestions,
    }


def _deterministic_repair_record(*, page: dict[str, Any], suggestions: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    original_text = str(page.get("text") or page.get("plain_text") or "")
    repaired_text = original_text
    corrections = []
    unresolved = []
    for suggestion in suggestions:
        original = str(suggestion.get("text") or "").strip()
        replacement = _explicit_replacement(suggestion)
        if not original or not replacement:
            unresolved.append(suggestion)
            continue
        if original not in repaired_text:
            unresolved.append({**suggestion, "unresolved_reason": "original_text_not_found"})
            continue
        repaired_text = repaired_text.replace(original, replacement)
        corrections.append(
            {
                "original": original,
                "replacement": replacement,
                "reason": suggestion.get("reason"),
                "confidence": 0.65,
                "method": "deterministic_explicit_typo_suggestion",
            }
        )
    return {
        "status": "deterministic_fallback",
        "page_number": int(page.get("page_number") or 0),
        "model": None,
        "original_text": original_text,
        "repaired_text": repaired_text,
        "corrections": corrections,
        "unresolved_suggestions": unresolved,
        "warnings": [reason, "model_failed; applied only explicit typo suggestions"],
        "suggestions_input": suggestions,
    }


def _explicit_replacement(suggestion: dict[str, Any]) -> str | None:
    original = str(suggestion.get("text") or "").strip()
    reason = str(suggestion.get("reason") or "")
    lower_reason = reason.casefold()
    if not original or "abbreviation" in lower_reason or "unclear" in lower_reason or "verify" in lower_reason:
        return None
    if " or " in lower_reason or " או " in reason:
        return None
    if "typo" not in lower_reason and "instead of" not in lower_reason and "correction" not in lower_reason:
        return None
    match = re.search(r"(?:likely|for|instead of|to)\s+['\"׳״]([^'\"׳״]+)['\"׳״]", reason, flags=re.IGNORECASE)
    if not match:
        return None
    replacement = match.group(1).strip()
    if not replacement or replacement == original:
        return None
    if " or " in replacement or "או" in replacement:
        return None
    if " " in original and " " not in replacement:
        return None
    if len(replacement) > max(40, len(original) * 4):
        return None
    return replacement


def _format_text_page(record: dict[str, Any]) -> str:
    page_number = record.get("page_number")
    text = str(record.get("repaired_text") or "")
    return f"\n\n===== PAGE {page_number} =====\n{text}\n"


def _build_summary(
    *,
    ocr_jsonl_path: Path,
    quality_report_path: Path | None,
    jsonl_output_path: Path,
    text_output_path: Path,
    model: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "source_ocr_jsonl": str(ocr_jsonl_path),
        "source_quality_report": str(quality_report_path) if quality_report_path else None,
        "repaired_jsonl": str(jsonl_output_path),
        "repaired_text": str(text_output_path),
        "model": model,
        "page_count": len(records),
        "completed_pages": sum(1 for record in records if record.get("status") == "completed"),
        "fallback_pages": sum(1 for record in records if record.get("status") == "fallback"),
        "deterministic_fallback_pages": sum(1 for record in records if record.get("status") == "deterministic_fallback"),
        "correction_count": sum(len(record.get("corrections") or []) for record in records),
        "unresolved_count": sum(len(record.get("unresolved_suggestions") or []) for record in records),
        "pages": [
            {
                "page_number": record.get("page_number"),
                "status": record.get("status"),
                "corrections": len(record.get("corrections") or []),
                "unresolved_suggestions": len(record.get("unresolved_suggestions") or []),
                "warnings": record.get("warnings") or [],
            }
            for record in records
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
