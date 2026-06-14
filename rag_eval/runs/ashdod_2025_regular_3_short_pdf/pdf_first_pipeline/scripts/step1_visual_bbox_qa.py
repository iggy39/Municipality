#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx


DEFAULT_MODEL = "mistral-small3.1:latest"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


def main() -> int:
    parser = argparse.ArgumentParser(description="Use local Mistral vision to validate PDF text block bbox overlays")
    parser.add_argument("pages_json", help="Step 1 pages.json path")
    parser.add_argument("overlay_dir", help="Directory containing page_###_overlay.png")
    parser.add_argument("--output-dir", required=True, help="Directory for QA outputs")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-base-url", default=DEFAULT_OLLAMA_BASE_URL)
    parser.add_argument("--pages", help="Page list/ranges, e.g. 3,5-7. Default: all pages")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--num-ctx", type=int, default=8192, help="Ollama context window for dense overlay pages")
    args = parser.parse_args()

    pages_json_path = Path(args.pages_json).expanduser().resolve()
    overlay_dir = Path(args.overlay_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    per_page_dir = output_dir / "pages"
    per_page_dir.mkdir(exist_ok=True)

    payload = json.loads(pages_json_path.read_text(encoding="utf-8"))
    pages = payload.get("pages") or []
    max_page = max(int(page.get("page") or 0) for page in pages) if pages else 0
    selected_pages = set(_parse_pages(args.pages, max_page=max_page)) if args.pages else set(range(1, max_page + 1))

    results = []
    for page_payload in pages:
        page_number = int(page_payload.get("page") or 0)
        if page_number not in selected_pages:
            continue
        overlay_path = overlay_dir / f"page_{page_number:03d}_overlay.png"
        if not overlay_path.exists():
            raise SystemExit(f"Overlay missing for page {page_number}: {overlay_path}")
        started = time.perf_counter()
        result = _qa_page(
            page_payload=page_payload,
            overlay_path=overlay_path,
            model=args.model,
            base_url=str(args.ollama_base_url).rstrip("/"),
            timeout_seconds=max(1.0, args.timeout_seconds),
            num_ctx=max(4096, int(args.num_ctx)),
        )
        result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        page_out = per_page_dir / f"page_{page_number:03d}_bbox_qa.json"
        page_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(result)
        print(
            json.dumps(
                {
                    "page": page_number,
                    "bbox_quality": result.get("bbox_quality"),
                    "accept_for_next_step": result.get("accept_for_next_step"),
                    "issue_count": len(result.get("problems") or []),
                    "elapsed_seconds": result["elapsed_seconds"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    report = {
        "step": "step1_visual_bbox_qa",
        "model": args.model,
        "input_pages_json": str(pages_json_path),
        "input_overlay_dir": str(overlay_dir),
        "selected_pages": sorted(selected_pages),
        "result_count": len(results),
        "accepted_pages": [item.get("page") for item in results if item.get("accept_for_next_step") is True],
        "blocked_pages": [item.get("page") for item in results if item.get("accept_for_next_step") is not True],
        "results": results,
    }
    report_path = output_dir / "bbox_quality_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"bbox_quality_report": str(report_path), "result_count": len(results)}, ensure_ascii=False))
    return 0


def _qa_page(*, page_payload: dict[str, Any], overlay_path: Path, model: str, base_url: str, timeout_seconds: float, num_ctx: int) -> dict[str, Any]:
    page_number = int(page_payload.get("page") or 0)
    image_b64 = base64.b64encode(overlay_path.read_bytes()).decode("ascii")
    block_summary = _block_summary(page_payload)
    prompt = f"""
Validate bbox overlay quality for page {page_number}.

The image has colored rectangles and labels such as p3_b12. Decide whether rectangles align with the visible text.
Judge the rectangle-to-text coverage, not the placement of the inserted block-id labels. Table grid lines, split table cells, and footer/system lines can be partial but acceptable when the visible text is covered and no text is missing.

Do NOT transcribe. Do NOT classify sections, decisions, topics, or tables. Return only compact QA JSON.

Block IDs and boxes:
{json.dumps(block_summary, ensure_ascii=False)}

Quality definitions:
- good: text boxes mostly cover the visible text they label; no major missing visible text.
- partial: usable, but some boxes are merged, split, hard to inspect, or table areas are imperfect.
- bad: major visible text is missing or boxes do not align with visible text.

Set accept_for_next_step=true only when quality is good, or partial with only low-severity problems.
Keep summary under 12 words. If there are no problems, use empty arrays.
""".strip()
    response_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "page": {"type": "integer"},
            "bbox_quality": {"type": "string", "enum": ["good", "partial", "bad"]},
            "accept_for_next_step": {"type": "boolean"},
            "confidence": {"type": "number"},
            "summary": {"type": "string"},
            "problems": {"type": "array", "items": {"type": "string"}},
            "missing_visible_text_regions": {"type": "array", "items": {"type": "string"}},
            "recommended_next_action": {
                "type": "string",
                "enum": ["accept", "use_visual_ocr", "split_or_merge_blocks", "manual_review"],
            },
        },
        "required": [
            "page",
            "bbox_quality",
            "accept_for_next_step",
            "confidence",
            "summary",
            "problems",
            "missing_visible_text_regions",
            "recommended_next_action",
        ],
    }
    body = {
        "model": model,
        "stream": False,
        "format": response_schema,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [image_b64],
            }
        ],
        "options": {"temperature": 0.0, "num_predict": 512, "num_ctx": num_ctx},
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=10.0, read=timeout_seconds, write=30.0, pool=10.0)) as client:
            response = client.post(f"{base_url}/api/chat", json=body)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return {
            "page": page_number,
            "bbox_quality": "bad",
            "accept_for_next_step": False,
            "confidence": 0.0,
            "summary": f"visual model call failed: {exc.__class__.__name__}:{exc}",
            "problems": [{"block_id": None, "problem": "model_call_failed", "severity": "high", "explanation": str(exc)}],
            "missing_visible_text_regions": [],
            "recommended_next_action": "manual_review",
            "raw_payload": None,
        }
    content = str(((payload.get("message") or {}).get("content")) or "").strip()
    parsed = _parse_json(content)
    if parsed is None:
        return {
            "page": page_number,
            "bbox_quality": "bad",
            "accept_for_next_step": False,
            "confidence": 0.0,
            "summary": "visual model did not return valid JSON",
            "problems": [{"block_id": None, "problem": "invalid_json", "severity": "high", "explanation": content[:1000]}],
            "missing_visible_text_regions": [],
            "recommended_next_action": "manual_review",
            "raw_model_text": content,
            "raw_payload": payload,
        }
    return _normalize_result(parsed, page_number=page_number, page_payload=page_payload, raw_payload=payload)


def _block_summary(page_payload: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for block in page_payload.get("blocks") or []:
        text = " ".join(str(block.get("text") or "").split())
        out.append(
            {
                "block_id": block.get("block_id"),
                "bbox": _compact_bbox(block.get("bbox")),
            }
        )
    return out


def _compact_bbox(value: Any) -> list[int] | Any:
    if not isinstance(value, list) or len(value) != 4:
        return value
    out: list[int] = []
    for part in value:
        try:
            out.append(int(round(float(part))))
        except (TypeError, ValueError):
            return value
    return out


def _parse_pages(value: str, *, max_page: int) -> list[int]:
    pages: set[int] = set()
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            pages.update(range(int(left), int(right) + 1))
        else:
            pages.add(int(part))
    return sorted(page for page in pages if 1 <= page <= max_page)


def _parse_json(value: str) -> dict[str, Any] | None:
    candidates = [value]
    match = re.search(r"\{.*\}", value, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _normalize_result(parsed: dict[str, Any], *, page_number: int, page_payload: dict[str, Any], raw_payload: dict[str, Any]) -> dict[str, Any]:
    quality = str(parsed.get("bbox_quality") or "partial").strip().lower()
    if quality not in {"good", "partial", "bad"}:
        quality = "partial"
    action = str(parsed.get("recommended_next_action") or "manual_review").strip()
    if action not in {"accept", "use_visual_ocr", "split_or_merge_blocks", "manual_review"}:
        action = "manual_review"
    accept = parsed.get("accept_for_next_step")
    if not isinstance(accept, bool):
        accept = quality == "good" and action == "accept"
    problems = parsed.get("problems") if isinstance(parsed.get("problems"), list) else []
    missing = parsed.get("missing_visible_text_regions") if isinstance(parsed.get("missing_visible_text_regions"), list) else []
    acceptance_override = None
    if not accept and quality == "partial" and not missing and _only_footer_problems(problems=problems, page_payload=page_payload):
        accept = True
        action = "accept"
        acceptance_override = "footer_only_low_risk"
    confidence = parsed.get("confidence")
    try:
        confidence_value = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence_value = 0.0
    return {
        "page": page_number,
        "bbox_quality": quality,
        "accept_for_next_step": accept,
        "confidence": confidence_value,
        "summary": str(parsed.get("summary") or "").strip(),
        "problems": problems,
        "missing_visible_text_regions": missing,
        "recommended_next_action": action,
        "acceptance_override": acceptance_override,
        "raw_payload": raw_payload,
    }


def _only_footer_problems(*, problems: list[Any], page_payload: dict[str, Any]) -> bool:
    if not problems:
        return False
    block_by_id = {str(block.get("block_id") or ""): block for block in page_payload.get("blocks") or []}
    for problem in problems:
        text = str(problem)
        match = re.search(r"p\d+_b\d+", text)
        if not match:
            return False
        block = block_by_id.get(match.group(0))
        if not block:
            return False
        features = block.get("visual_features") or {}
        if float(features.get("y0_ratio") or 0.0) < 0.88:
            return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
