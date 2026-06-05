#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import fitz  # noqa: E402

from municipality.qwen_ocr import (  # noqa: E402
    DEFAULT_QWEN_OCR_DPI,
    DEFAULT_QWEN_OCR_IMAGE_FORMAT,
    DEFAULT_QWEN_OCR_TIMEOUT_SECONDS,
    QwenOcrPageResult,
    QwenVisionOcrClient,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR a PDF with local Qwen vision OCR")
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument("--output", "-o", help="Text output path. Default: <pdf>.ocr.txt")
    parser.add_argument("--json-output", help="JSONL output path. Default: <pdf>.ocr.jsonl")
    parser.add_argument("--output-dir", help="Directory for default sidecars. Default: PDF directory")
    parser.add_argument("--json", action="store_true", help="Print JSONL to terminal instead of text; files still include both")
    parser.add_argument("--no-terminal-output", action="store_true", help="Do not print OCR page text/JSON to terminal; still write files")
    parser.add_argument(
        "--ocr-mode",
        choices=("vision", "native", "native-first"),
        default=os.getenv("MUNICIPALITY_OCR_MODE") or "native-first",
        help="OCR strategy: vision=Qwen only, native=PDF text only, native-first=PDF text with Qwen fallback",
    )
    parser.add_argument(
        "--native-min-chars",
        type=int,
        default=int(os.getenv("MUNICIPALITY_NATIVE_MIN_CHARS") or "500"),
        help="Minimum native PDF text chars before skipping Qwen in native-first mode",
    )
    parser.add_argument("--dpi", type=int, help="Render DPI for Qwen vision OCR. Overrides QWEN_OCR_DPI")
    parser.add_argument("--image-format", choices=("png", "jpeg"), help="Rendered image format for Qwen vision OCR")
    parser.add_argument("--pages", "-p", help="Pages to OCR, for example: 1,3-5. Default: all pages")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_QWEN_OCR_TIMEOUT_SECONDS,
        help=f"Per-page model timeout in seconds (default: {DEFAULT_QWEN_OCR_TIMEOUT_SECONDS:g})",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 1
    if not pdf_path.is_file():
        print(f"Path is not a file: {pdf_path}", file=sys.stderr)
        return 1

    document = fitz.open(pdf_path)
    try:
        page_numbers = _parse_pages(args.pages, page_count=len(document)) if args.pages else list(range(1, len(document) + 1))
        if not page_numbers:
            print("No valid pages selected", file=sys.stderr)
            return 1

        text_output_path, json_output_path = _resolve_output_paths(
            pdf_path=pdf_path,
            output_dir=args.output_dir,
            text_output=args.output,
            json_output=args.json_output,
        )
        text_output_handle = text_output_path.open("w", encoding="utf-8")
        json_output_handle = json_output_path.open("w", encoding="utf-8")
        try:
            client = QwenVisionOcrClient(timeout_seconds=args.timeout_seconds)
            dpi = _ocr_dpi(args.dpi)
            image_format = args.image_format or _ocr_image_format()
            print(
                (
                    f"OCR {pdf_path} pages={page_numbers} dpi={dpi} format={image_format} "
                    f"mode={args.ocr_mode} native_min_chars={args.native_min_chars} "
                    f"provider={client.provider} model={client.model_name} timeout={client.timeout_seconds}s"
                ),
                file=sys.stderr,
                flush=True,
            )
            print(f"Ollama URL: {client.ollama_base_url}", file=sys.stderr, flush=True)
            print(f"OpenAI-compatible URL: {client.openai_base_url}", file=sys.stderr, flush=True)
            print(f"Text output file: {text_output_path}", file=sys.stderr, flush=True)
            print(f"JSONL output file: {json_output_path}", file=sys.stderr, flush=True)
            failed = 0
            for page_number in page_numbers:
                page_started = time.perf_counter()
                print(f"\n[page {page_number}] start", file=sys.stderr, flush=True)
                page = document.load_page(page_number - 1)
                result = _ocr_page_by_mode(
                    page=page,
                    page_number=page_number,
                    mode=args.ocr_mode,
                    native_min_chars=max(0, args.native_min_chars),
                    client=client,
                    dpi=dpi,
                    image_format=image_format,
                )
                if result.error_code:
                    failed += 1
                text_output = _format_text_page(result)
                json_output = _format_json_page(result)
                terminal_output = json_output if args.json else text_output
                if not args.no_terminal_output:
                    print(f"[page {page_number}] writing terminal output", file=sys.stderr, flush=True)
                    print(terminal_output, flush=True)
                print(f"[page {page_number}] writing text output", file=sys.stderr, flush=True)
                text_output_handle.write(text_output)
                text_output_handle.write("\n")
                text_output_handle.flush()
                print(f"[page {page_number}] writing JSONL output", file=sys.stderr, flush=True)
                json_output_handle.write(json_output)
                json_output_handle.write("\n")
                json_output_handle.flush()
                page_seconds = time.perf_counter() - page_started
                print(f"[page {page_number}] done in {page_seconds:.1f}s", file=sys.stderr, flush=True)
            print(f"Wrote OCR text to {text_output_path}", file=sys.stderr)
            print(f"Wrote OCR JSONL to {json_output_path}", file=sys.stderr)
            return 2 if failed else 0
        finally:
            text_output_handle.close()
            json_output_handle.close()
    finally:
        document.close()


def _format_text_page(result) -> str:
    lines = [f"\n===== PAGE {result.page_number} ====="]
    if result.error_code:
        lines.append(f"OCR_ERROR: {result.error_code}")
        if result.error_text:
            lines.append(result.error_text)
    else:
        lines.append(result.text)
    return "\n".join(lines).rstrip()


def _format_json_page(result) -> str:
    payload = {
        "page_number": result.page_number,
        "text": result.text,
        "plain_text": result.plain_text,
        "markdown_layout": result.markdown_layout,
        "tables_markdown": result.tables_markdown,
        "detected_headings": result.detected_headings,
        "uncertain_regions": result.uncertain_regions,
        "quality_notes": result.quality_notes,
        "ocr_confidence": result.ocr_confidence,
        "parse_warning": result.parse_warning,
        "error_code": result.error_code,
        "error_text": result.error_text,
        "parsed_payload": result.parsed_payload,
    }
    return json.dumps(payload, ensure_ascii=False)


def _resolve_output_paths(
    *,
    pdf_path: Path,
    output_dir: str | None,
    text_output: str | None,
    json_output: str | None,
) -> tuple[Path, Path]:
    base_dir = Path(output_dir).expanduser().resolve() if output_dir else pdf_path.parent
    text_path = Path(text_output).expanduser().resolve() if text_output else base_dir / f"{pdf_path.stem}.ocr.txt"
    json_path = Path(json_output).expanduser().resolve() if json_output else base_dir / f"{pdf_path.stem}.ocr.jsonl"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    return text_path, json_path


def _render_page(page, *, dpi: int, image_format: str) -> tuple[bytes, str]:
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    if image_format in {"jpg", "jpeg"}:
        return pix.tobytes("jpg"), "image/jpeg"
    return pix.tobytes("png"), "image/png"


def _ocr_page_by_mode(
    *,
    page,
    page_number: int,
    mode: str,
    native_min_chars: int,
    client: QwenVisionOcrClient,
    dpi: int,
    image_format: str,
) -> QwenOcrPageResult:
    if mode in {"native", "native-first"}:
        native_result = _native_text_result(page=page, page_number=page_number, native_min_chars=native_min_chars)
        print(
            (
                f"[page {page_number}] native text chars={len(native_result.text)} "
                f"blocks={(native_result.parsed_payload or {}).get('native_block_count')} "
                f"usable={(native_result.parsed_payload or {}).get('native_text_usable')}"
            ),
            file=sys.stderr,
            flush=True,
        )
        if mode == "native" or (native_result.parsed_payload or {}).get("native_text_usable") is True:
            return native_result

    print(f"[page {page_number}] rendering at {dpi} DPI as {image_format}", file=sys.stderr, flush=True)
    image_bytes, image_mime = _render_page(page, dpi=dpi, image_format=image_format)
    print(f"[page {page_number}] rendered image_mime={image_mime} bytes={len(image_bytes)}", file=sys.stderr, flush=True)
    print(f"[page {page_number}] calling model provider={client.provider} model={client.model_name}", file=sys.stderr, flush=True)
    call_started = time.perf_counter()
    result = client.ocr_page(image_bytes=image_bytes, page_number=page_number, image_mime=image_mime)
    call_seconds = time.perf_counter() - call_started
    if result.parsed_payload is None:
        result.parsed_payload = {}
    result.parsed_payload.setdefault("source_mode", "qwen_vision")
    print(
        (
            f"[page {page_number}] model returned in {call_seconds:.1f}s "
            f"error={result.error_code or 'NONE'} parse_warning={result.parse_warning or 'NONE'} "
            f"chars={len(result.text)} confidence={result.ocr_confidence}"
        ),
        file=sys.stderr,
        flush=True,
    )
    return result


def _native_text_result(*, page, page_number: int, native_min_chars: int) -> QwenOcrPageResult:
    try:
        text = page.get_text("text", sort=True) or ""
        blocks = page.get_text("blocks", sort=True) or []
    except TypeError:
        text = page.get_text("text") or ""
        blocks = page.get_text("blocks") or []
    text = _clean_native_text(text)
    nonspace_chars = len(re.sub(r"\s+", "", text))
    usable = len(text) >= native_min_chars and nonspace_chars >= max(100, native_min_chars // 3)
    confidence = 0.99 if usable else (0.4 if text else None)
    quality_notes = "Native PDF text extracted; Qwen vision OCR skipped." if usable else "Native PDF text below threshold; Qwen vision OCR needed."
    return QwenOcrPageResult(
        page_number=page_number,
        text=text,
        plain_text=text,
        markdown_layout=text,
        tables_markdown=[],
        detected_headings=_native_headings(text),
        uncertain_regions=[] if usable else [{"text": "[native text below threshold]", "reason": quality_notes}],
        quality_notes=quality_notes,
        ocr_confidence=confidence,
        parsed_payload={
            "source_mode": "native_pdf_text",
            "native_text_usable": usable,
            "native_chars": len(text),
            "native_nonspace_chars": nonspace_chars,
            "native_block_count": len(blocks),
            "native_min_chars": native_min_chars,
        },
        error_code=None if text else "NATIVE_TEXT_EMPTY",
        error_text=None if text else "No native PDF text extracted from page",
    )


def _clean_native_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    cleaned = []
    blank_seen = False
    for line in lines:
        if not line:
            if not blank_seen:
                cleaned.append("")
            blank_seen = True
            continue
        cleaned.append(line)
        blank_seen = False
    return "\n".join(cleaned).strip()


def _native_headings(text: str) -> list[str]:
    headings = []
    for line in text.splitlines():
        compact = line.strip()
        if not compact or len(compact) > 180:
            continue
        if compact.startswith(("סעיף", "החלט", "פרוטוקול")) or compact.endswith(":"):
            headings.append(compact)
    return list(dict.fromkeys(headings))[:30]


def _ocr_dpi(cli_dpi: int | None = None) -> int:
    if cli_dpi is not None:
        return max(72, min(600, int(cli_dpi)))
    raw_value = os.getenv("QWEN_OCR_DPI")
    if raw_value:
        try:
            return max(72, min(600, int(raw_value)))
        except ValueError:
            return DEFAULT_QWEN_OCR_DPI
    return DEFAULT_QWEN_OCR_DPI


def _ocr_image_format() -> str:
    value = (os.getenv("QWEN_OCR_IMAGE_FORMAT") or DEFAULT_QWEN_OCR_IMAGE_FORMAT).strip().casefold()
    if value in {"jpg", "jpeg"}:
        return "jpeg"
    return "png"


def _parse_pages(value: str, *, page_count: int) -> list[int]:
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


if __name__ == "__main__":
    raise SystemExit(main())
