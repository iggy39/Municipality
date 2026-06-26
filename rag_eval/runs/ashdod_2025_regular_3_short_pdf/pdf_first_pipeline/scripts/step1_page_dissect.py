#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import fitz


def _add_project_src_to_path() -> None:
    for parent in Path(__file__).resolve().parents:
        src_dir = parent / "src"
        if (src_dir / "municipality" / "layout_parser.py").exists():
            sys.path.insert(0, str(src_dir))
            return


_add_project_src_to_path()

from municipality.layout_parser import reconstruct_pdf_line_text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 1: dissect PDF pages into images and text blocks")
    parser.add_argument("pdf", help="Input PDF path")
    parser.add_argument("--output-dir", required=True, help="Output directory for step 1 artifacts")
    parser.add_argument("--dpi", type=int, default=160, help="Rendered page image DPI")
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "page_images"
    text_dir = output_dir / "page_text"
    images_dir.mkdir(exist_ok=True)
    text_dir.mkdir(exist_ok=True)

    pages: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as doc:
        for page_index, page in enumerate(doc, start=1):
            image_path = images_dir / f"page_{page_index:03d}.png"
            _render_page(page=page, output_path=image_path, dpi=max(72, args.dpi))

            raw = page.get_text("dict", sort=False)
            page_words = page.get_text("words", sort=False)
            page_record = _page_record(
                page=page,
                page_number=page_index,
                raw=raw,
                page_words=page_words,
                image_path=image_path.relative_to(output_dir).as_posix(),
            )
            (text_dir / f"page_{page_index:03d}.txt").write_text(page_record["plain_text"], encoding="utf-8")
            pages.append(page_record)

    summary = _summary(pdf_path=pdf_path, pages=pages)
    payload = {
        "step": "step1_page_dissect",
        "input_pdf": str(pdf_path),
        "output_dir": str(output_dir),
        "acceptance_required": [
            "Each visible page has a rendered image.",
            "Each page has text blocks with bbox coordinates.",
            "Headers, footers, body, headings, and tables are not decided yet; only raw evidence is captured.",
            "No model-generated reasoning text is introduced.",
        ],
        "summary": summary,
        "pages": pages,
    }
    pages_json = output_dir / "pages.json"
    pages_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pages_json": str(pages_json), "page_count": len(pages), **summary}, ensure_ascii=False))
    return 0


def _render_page(*, page: fitz.Page, output_path: Path, dpi: int) -> None:
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    pix.save(output_path)


def _page_record(
    *,
    page: fitz.Page,
    page_number: int,
    raw: dict[str, Any],
    image_path: str,
    page_words: list[Any] | None = None,
) -> dict[str, Any]:
    page_rect = page.rect
    blocks: list[dict[str, Any]] = []
    spans_font_sizes: list[float] = []
    block_index = 0
    for raw_block in raw.get("blocks") or []:
        if int(raw_block.get("type", 0)) != 0:
            continue
        lines = []
        block_text_lines = []
        for raw_line in raw_block.get("lines") or []:
            spans = []
            line_text_parts = []
            for raw_span in raw_line.get("spans") or []:
                text = str(raw_span.get("text") or "")
                if not text.strip():
                    continue
                size = float(raw_span.get("size") or 0.0)
                spans_font_sizes.append(size)
                span = {
                    "text": text,
                    "bbox": _bbox(raw_span.get("bbox")),
                    "font": raw_span.get("font"),
                    "size": round(size, 3),
                    "color": raw_span.get("color"),
                    "flags": raw_span.get("flags"),
                }
                spans.append(span)
                line_text_parts.append(text)
            raw_line_text = "".join(line_text_parts).strip()
            if not raw_line_text:
                continue
            line_bbox = _bbox(raw_line.get("bbox"))
            line_text = reconstruct_pdf_line_text(raw_text=raw_line_text, bbox=line_bbox, page_words=page_words or [])
            line_payload = {
                "line_id": f"p{page_number}_b{block_index + 1}_l{len(lines) + 1}",
                "text": line_text,
                "bbox": line_bbox,
                "spans": spans,
            }
            if line_text != raw_line_text:
                line_payload.update(
                    {
                        "raw_extractor_text": raw_line_text,
                        "text_reconstructed_from_words": True,
                        "reconstruction_source": "pymupdf_words_directional_runs",
                    }
                )
            lines.append(line_payload)
            block_text_lines.append(line_text)
        block_text = "\n".join(block_text_lines).strip()
        if not block_text:
            continue
        block_index += 1
        blocks.append(
            {
                "block_id": f"p{page_number}_b{block_index}",
                "bbox": _bbox(raw_block.get("bbox")),
                "text": block_text,
                "lines": lines,
                "visual_features": _visual_features(raw_block=raw_block, page_rect=page_rect, lines=lines),
                "role_candidate": "unknown",
            }
        )

    font_stats = _font_stats(spans_font_sizes)
    plain_text = "\n\n".join(block["text"] for block in blocks)
    return {
        "page": page_number,
        "width": round(float(page_rect.width), 3),
        "height": round(float(page_rect.height), 3),
        "image_path": image_path,
        "plain_text": plain_text,
        "block_count": len(blocks),
        "font_stats": font_stats,
        "blocks": blocks,
    }


def _bbox(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    return [round(float(item), 3) for item in value]


def _visual_features(*, raw_block: dict[str, Any], page_rect: fitz.Rect, lines: list[dict[str, Any]]) -> dict[str, Any]:
    x0, y0, x1, y1 = _bbox(raw_block.get("bbox"))
    page_width = max(float(page_rect.width), 1.0)
    page_height = max(float(page_rect.height), 1.0)
    sizes = [float(span.get("size") or 0.0) for line in lines for span in line.get("spans") or []]
    colors = [span.get("color") for line in lines for span in line.get("spans") or [] if span.get("color") is not None]
    text = "\n".join(str(line.get("text") or "") for line in lines)
    return {
        "x0_ratio": round(x0 / page_width, 4),
        "y0_ratio": round(y0 / page_height, 4),
        "x1_ratio": round(x1 / page_width, 4),
        "y1_ratio": round(y1 / page_height, 4),
        "width_ratio": round(max(0.0, x1 - x0) / page_width, 4),
        "height_ratio": round(max(0.0, y1 - y0) / page_height, 4),
        "line_count": len(lines),
        "char_count": len(text),
        "max_font_size": round(max(sizes), 3) if sizes else None,
        "median_font_size": round(statistics.median(sizes), 3) if sizes else None,
        "dominant_color": _mode(colors),
    }


def _font_stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "max": None}
    return {"min": round(min(values), 3), "median": round(statistics.median(values), 3), "max": round(max(values), 3)}


def _mode(values: list[Any]) -> Any:
    if not values:
        return None
    counts: dict[Any, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))[0][0]


def _summary(*, pdf_path: Path, pages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "pdf_name": pdf_path.name,
        "page_count": len(pages),
        "total_blocks": sum(int(page.get("block_count") or 0) for page in pages),
        "pages_with_text": sum(1 for page in pages if str(page.get("plain_text") or "").strip()),
        "page_block_counts": {str(page["page"]): page.get("block_count") for page in pages},
    }


if __name__ == "__main__":
    raise SystemExit(main())
