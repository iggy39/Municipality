#!/usr/bin/env python3
"""
OCR Hebrew municipal PDF pages using Qwen Vision LLM.
Outputs JSON array with results for each page.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path
from typing import Any

import fitz
import httpx


# Configuration
DEFAULT_MODEL = "qwen3.5:122b"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_DPI = 300
DEFAULT_TIMEOUT = 600.0  # seconds per page (10 minutes)

THINK_BLOCK_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def build_qwen_ocr_prompt(page_number: int) -> str:
    return f"""/think
You are a meticulous OCR engine for Hebrew municipal PDF pages.
Use thinking mode internally to inspect the page, but the final answer must be JSON only.

OCR page {page_number} exactly as it appears.

Return only this JSON object:
{{
  "page_number": {page_number},
  "plain_text": "full transcription in natural reading order",
  "markdown_layout": "full transcription preserving meaningful line breaks, headings, lists, and tables",
  "tables_markdown": ["each detected table as markdown"],
  "detected_headings": ["visible headings only"],
  "uncertain_regions": [{{"text": "raw uncertain text or [לא קריא]", "reason": "why uncertain"}}],
  "quality_notes": "short OCR quality notes",
  "ocr_confidence": 0.0
}}

Rules:
- Preserve Hebrew text order. Do not reverse Hebrew words or lines.
- Preserve all visible text, including headers, footers, stamps, signatures, handwritten text, dates, and page numbers.
- Preserve tables using Markdown where possible.
- Do not summarize, classify, explain, or add commentary.
- Mark unreadable text explicitly as [לא קריא].
- If a field has no content, return an empty string, empty array, or null as appropriate.
- The final response must not include markdown fences, prose, or <thinking> blocks.
""".strip()


def clean_model_content(content: str) -> str:
    """Remove thinking blocks and code fences from LLM response."""
    cleaned = THINK_BLOCK_RE.sub("", content).strip()
    return CODE_FENCE_RE.sub("", cleaned).strip()


def parse_json_from_response(content: str) -> dict[str, Any] | None:
    """Extract JSON object from LLM response."""
    if not content:
        return None
    
    # Try parsing directly first
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    
    # Extract first JSON object from text
    start = content.find("{")
    if start < 0:
        return None
    
    depth = 0
    in_string = False
    escape = False
    
    for i in range(start, len(content)):
        char = content[i]
        
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
                json_str = content[start:i+1]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    return None
    
    return None


def render_page_to_png(pdf_path: Path, page_number: int, dpi: int) -> bytes:
    """Render PDF page to PNG bytes."""
    doc = fitz.open(pdf_path)
    page = doc[page_number - 1]  # 1-indexed to 0-indexed
    
    # Calculate zoom factor for desired DPI
    # Default PDF 72 DPI to target DPI
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    
    pix = page.get_pixmap(matrix=matrix)
    doc.close()
    
    return pix.tobytes("png")


def ocr_page_with_qwen(
    image_bytes: bytes,
    page_number: int,
    model: str,
    base_url: str
) -> dict[str, Any]:
    """Send page image to Qwen OCR endpoint."""
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    prompt = build_qwen_ocr_prompt(page_number)
    
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [image_b64],
            }
        ],
    }
    
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            response = client.post(f"{base_url}/api/chat", json=payload)
            response.raise_for_status()
            resp_json = response.json()
    except httpx.TimeoutException:
        return {
            "page_number": page_number,
            "error": "TIMEOUT",
            "error_detail": f"Request timed out after {DEFAULT_TIMEOUT}s"
        }
    except Exception as e:
        return {
            "page_number": page_number,
            "error": "REQUEST_FAILED",
            "error_detail": f"{type(e).__name__}: {e}"
        }
    
    # Extract content from Ollama response
    message = resp_json.get("message", {})
    content = message.get("content", "")
    
    if not content:
        return {
            "page_number": page_number,
            "error": "EMPTY_RESPONSE",
            "error_detail": "Ollama returned empty content"
        }
    
    # Clean and parse response
    cleaned = clean_model_content(content)
    parsed = parse_json_from_response(cleaned)
    
    if not parsed:
        return {
            "page_number": page_number,
            "error": "JSON_PARSE_FAILED",
            "error_detail": "Could not extract JSON from response",
            "raw_response_preview": content[:500]
        }
    
    # Ensure page_number is correct
    parsed["page_number"] = page_number
    
    return parsed


def process_pdf(
    pdf_path: Path,
    pages: list[int] | None,
    model: str,
    base_url: str,
    dpi: int
) -> list[dict[str, Any]]:
    """Process all pages in a PDF."""
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    doc.close()
    
    if pages is None:
        pages = list(range(1, total_pages + 1))
    
    results = []
    for page_num in pages:
        if page_num < 1 or page_num > total_pages:
            print(f"Warning: Page {page_num} out of range (1-{total_pages})", file=sys.stderr)
            continue
        
        print(f"Processing page {page_num}/{total_pages}...", file=sys.stderr, flush=True)
        
        try:
            image_bytes = render_page_to_png(pdf_path, page_num, dpi)
            result = ocr_page_with_qwen(image_bytes, page_num, model, base_url)
            results.append(result)
        except Exception as e:
            print(f"Error processing page {page_num}: {e}", file=sys.stderr)
            results.append({
                "page_number": page_num,
                "error": "UNEXPECTED_ERROR",
                "error_detail": f"{type(e).__name__}: {e}"
            })
    
    return results


def parse_page_range(value: str, max_pages: int) -> list[int]:
    """Parse page range like '1-3,5,7-9' into list of page numbers."""
    pages = []
    for part in value.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            pages.extend(range(int(start), int(end) + 1))
        else:
            pages.append(int(part))
    return [p for p in pages if 1 <= p <= max_pages]


def main():
    parser = argparse.ArgumentParser(
        description="OCR Hebrew municipal PDF pages using Qwen Vision LLM"
    )
    parser.add_argument(
        "pdf_path",
        help="Path to the PDF file"
    )
    parser.add_argument(
        "--pages", "-p",
        help="Pages to process (e.g., '1-3,5,7-9'), default: all"
    )
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        help=f"Ollama model name (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--base-url", "-b",
        default=DEFAULT_BASE_URL,
        help=f"Ollama base URL (default: {DEFAULT_BASE_URL})"
    )
    parser.add_argument(
        "--dpi", "-d",
        type=int,
        default=DEFAULT_DPI,
        help=f"Render DPI (default: {DEFAULT_DPI})"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output JSON file (default: stdout)"
    )
    
    args = parser.parse_args()
    
    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        print(f"Error: PDF not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)
    
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    doc.close()
    
    print(f"PDF has {total_pages} pages", file=sys.stderr)
    
    if args.pages:
        page_range = parse_page_range(args.pages, total_pages)
        if not page_range:
            print(f"Error: Invalid page range: {args.pages}", file=sys.stderr)
            sys.exit(1)
        print(f"Processing pages: {page_range}", file=sys.stderr)
    else:
        print(f"Processing all pages", file=sys.stderr)
    
    results = process_pdf(
        pdf_path=pdf_path,
        pages=page_range if hasattr(args, 'pages') and args.pages else None,
        model=args.model,
        base_url=args.base_url,
        dpi=args.dpi
    )
    
    output = json.dumps(results, ensure_ascii=False, indent=2)
    
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Results written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
