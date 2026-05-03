from __future__ import annotations

from dataclasses import dataclass, field
import importlib
from typing import Any

import numpy as np
from PIL import Image


HEBREW_BLOCK_RE = "\u0590-\u05FF"


@dataclass(slots=True)
class ParsedLayoutPage:
    page: int
    text: str
    blocks: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ParsedLayoutDocument:
    full_text: str
    pages: list[ParsedLayoutPage]
    citation_map: list[dict[str, int]]
    backend_name: str


def parse_pdf_layout(pdf_bytes: bytes) -> ParsedLayoutDocument | None:
    fitz_module = _load_fitz()
    if fitz_module is None:
        return None

    try:
        document = fitz_module.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return None

    try:
        return _parse_with_fitz(document)
    except Exception:  # noqa: BLE001
        return None
    finally:
        document.close()


def parse_pdf_ocr(pdf_bytes: bytes) -> ParsedLayoutDocument | None:
    fitz_module = _load_fitz()
    easyocr_module = _load_easyocr()
    if fitz_module is None or easyocr_module is None:
        return None

    try:
        document = fitz_module.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return None

    try:
        reader = _easyocr_reader(easyocr_module)
        if reader is None:
            return None
        return _parse_with_easyocr(document, reader=reader)
    except Exception:  # noqa: BLE001
        return None
    finally:
        document.close()


def _parse_with_fitz(document: Any) -> ParsedLayoutDocument:
    pages: list[ParsedLayoutPage] = []
    full_parts: list[str] = []
    citation_map: list[dict[str, int]] = []
    cursor = 0

    for page_index in range(len(document)):
        page = document.load_page(page_index)
        page_payload = page.get_text("dict", sort=True)
        page_width = float(getattr(page.rect, "width", 0.0) or 0.0)
        page_lines = _extract_page_lines(page_payload, page_width=page_width, page_number=page_index + 1)
        page_text = "\n".join(line["text"] for line in page_lines if str(line.get("text") or "").strip())
        page_start = cursor
        page_end = page_start + len(page_text)

        blocks: list[dict[str, Any]] = []
        line_cursor = page_start
        for line in page_lines:
            line_text = str(line.get("text") or "").strip()
            if not line_text:
                continue
            start_offset = line_cursor
            end_offset = start_offset + len(line_text)
            line_cursor = end_offset + 1
            blocks.append(
                {
                    "kind": "line",
                    "block_index": int(line.get("block_index") or 0),
                    "line_index": int(line.get("line_index") or 0),
                    "start_offset": start_offset,
                    "end_offset": end_offset,
                    "text": line_text,
                    "bbox": list(line.get("bbox") or []),
                    "reading_direction": line.get("reading_direction"),
                    "font_size": line.get("font_size"),
                    "font_weight": line.get("font_weight"),
                    "alignment": line.get("alignment"),
                    "column_index": line.get("column_index"),
                    "is_table": bool(line.get("is_table") or False),
                    "role_guess": line.get("role_guess"),
                }
            )

        pages.append(
            ParsedLayoutPage(
                page=page_index + 1,
                text=page_text,
                blocks=blocks,
            )
        )
        if page_text:
            full_parts.append(page_text)
            citation_map.append({"start": page_start, "end": page_end, "page": page_index + 1})
        if page_index < len(document) - 1:
            full_parts.append("\n\n")
            cursor = page_end + 2
        else:
            cursor = page_end

    return ParsedLayoutDocument(
        full_text="".join(full_parts),
        pages=pages,
        citation_map=citation_map,
        backend_name="pymupdf_layout",
    )


def _parse_with_easyocr(document: Any, *, reader: Any) -> ParsedLayoutDocument:
    pages: list[ParsedLayoutPage] = []
    full_parts: list[str] = []
    citation_map: list[dict[str, int]] = []
    cursor = 0

    for page_index in range(len(document)):
        page = document.load_page(page_index)
        pix = page.get_pixmap(dpi=200)
        if pix is None:
            continue
        image = _pixmap_to_numpy(pix)
        if image is None:
            continue
        results = reader.readtext(image, detail=1, paragraph=False)
        blocks: list[dict[str, Any]] = []
        page_lines: list[str] = []
        line_cursor = cursor
        for line_index, row in enumerate(results):
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            bbox_raw, text_value, confidence = row[0], row[1], row[2]
            compact = " ".join(str(text_value or "").split()).strip()
            if not compact:
                continue
            start_offset = line_cursor
            end_offset = start_offset + len(compact)
            line_cursor = end_offset + 1
            bbox = _easyocr_bbox_to_list(bbox_raw)
            blocks.append(
                {
                    "kind": "line",
                    "block_index": line_index,
                    "line_index": line_index,
                    "start_offset": start_offset,
                    "end_offset": end_offset,
                    "text": compact,
                    "bbox": bbox,
                    "reading_direction": _reading_direction(compact),
                    "font_size": None,
                    "font_weight": None,
                    "alignment": _alignment_for_bbox(bbox, page_width=float(getattr(page.rect, 'width', 0.0) or 0.0), reading_direction=_reading_direction(compact)),
                    "column_index": _column_index_for_bbox(bbox, page_width=float(getattr(page.rect, 'width', 0.0) or 0.0)),
                    "is_table": False,
                    "role_guess": "ocr_line",
                    "ocr_confidence": float(confidence or 0.0),
                }
            )
            page_lines.append(compact)

        page_text = "\n".join(page_lines)
        page_start = cursor
        page_end = page_start + len(page_text)
        pages.append(ParsedLayoutPage(page=page_index + 1, text=page_text, blocks=blocks))
        if page_text:
            full_parts.append(page_text)
            citation_map.append({"start": page_start, "end": page_end, "page": page_index + 1})
        if page_index < len(document) - 1:
            full_parts.append("\n\n")
            cursor = page_end + 2
        else:
            cursor = page_end

    return ParsedLayoutDocument(
        full_text="".join(full_parts),
        pages=pages,
        citation_map=citation_map,
        backend_name="easyocr_scan_fallback",
    )


def _extract_page_lines(page_payload: dict[str, Any], *, page_width: float, page_number: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    blocks = page_payload.get("blocks") if isinstance(page_payload, dict) else []
    if not isinstance(blocks, list):
        return rows

    for block_index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        if int(block.get("type") or 0) != 0:
            continue
        lines = block.get("lines")
        if not isinstance(lines, list):
            continue
        for line_index, line in enumerate(lines):
            if not isinstance(line, dict):
                continue
            spans = line.get("spans")
            if not isinstance(spans, list):
                continue
            span_texts = [str(span.get("text") or "") for span in spans if isinstance(span, dict)]
            text = "".join(span_texts).strip()
            if not text:
                continue
            bbox = _bbox_to_list(line.get("bbox") or block.get("bbox"))
            reading_direction = _reading_direction(text)
            font_size = max((float(span.get("size") or 0.0) for span in spans if isinstance(span, dict)), default=0.0)
            font_flags = [int(span.get("flags") or 0) for span in spans if isinstance(span, dict)]
            font_weight = "bold" if any(_span_is_bold(flags) for flags in font_flags) else "regular"
            alignment = _alignment_for_bbox(bbox, page_width=page_width, reading_direction=reading_direction)
            rows.append(
                {
                    "page": page_number,
                    "block_index": block_index,
                    "line_index": line_index,
                    "text": text,
                    "bbox": bbox,
                    "reading_direction": reading_direction,
                    "font_size": round(font_size, 3) if font_size else None,
                    "font_weight": font_weight,
                    "alignment": alignment,
                    "column_index": _column_index_for_bbox(bbox, page_width=page_width),
                    "is_table": False,
                    "role_guess": _role_guess(text=text, font_size=font_size, alignment=alignment, font_weight=font_weight),
                }
            )

    rows.sort(key=lambda row: (row["bbox"][1] if row["bbox"] else 0.0, row["bbox"][0] if row["bbox"] else 0.0))
    return rows


def _bbox_to_list(value: Any) -> list[float]:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return [float(item) for item in value]
    return []


def _reading_direction(value: str) -> str | None:
    if not value:
        return None
    hebrew_chars = sum(1 for char in value if "\u0590" <= char <= "\u05FF")
    latin_chars = sum(1 for char in value if "a" <= char.casefold() <= "z")
    if hebrew_chars > max(0, latin_chars):
        return "rtl"
    if latin_chars > 0:
        return "ltr"
    return None


def _span_is_bold(flags: int) -> bool:
    return bool(flags & 16) or bool(flags & 2)


def _alignment_for_bbox(bbox: list[float], *, page_width: float, reading_direction: str | None) -> str | None:
    if len(bbox) != 4 or page_width <= 0:
        return None
    left = bbox[0]
    right = bbox[2]
    width = max(1.0, right - left)
    center = (left + right) / 2.0
    if abs(center - (page_width / 2.0)) <= max(page_width * 0.08, width * 0.35):
        return "center"
    if reading_direction == "rtl":
        return "right" if right >= page_width * 0.65 else "left"
    return "left" if left <= page_width * 0.35 else "right"


def _column_index_for_bbox(bbox: list[float], *, page_width: float) -> int | None:
    if len(bbox) != 4 or page_width <= 0:
        return None
    center = (bbox[0] + bbox[2]) / 2.0
    return 0 if center <= page_width / 2.0 else 1


def _role_guess(*, text: str, font_size: float, alignment: str | None, font_weight: str | None) -> str | None:
    compact = " ".join(text.split())
    if not compact:
        return None
    token_count = len(compact.split())
    if token_count <= 14 and (compact.endswith(":") or compact.startswith(("נושא", "סעיף", "החלט", "פרוטוקול", "ועדת", "ועדה"))):
        return "heading_candidate"
    if token_count <= 14 and (alignment == "center" or font_weight == "bold") and font_size >= 11.5:
        return "heading_candidate"
    return "body"


def _load_fitz() -> Any | None:
    try:
        return importlib.import_module("fitz")
    except Exception:  # noqa: BLE001
        return None


def _load_easyocr() -> Any | None:
    try:
        return importlib.import_module("easyocr")
    except Exception:  # noqa: BLE001
        return None


_EASYOCR_READER: Any | None = None


def _easyocr_reader(easyocr_module: Any) -> Any | None:
    global _EASYOCR_READER
    if _EASYOCR_READER is not None:
        return _EASYOCR_READER
    try:
        _EASYOCR_READER = easyocr_module.Reader(["he", "en"], gpu=False, verbose=False)
    except Exception:  # noqa: BLE001
        _EASYOCR_READER = None
    return _EASYOCR_READER


def _pixmap_to_numpy(pix: Any) -> np.ndarray | None:
    try:
        mode = "RGBA" if getattr(pix, "alpha", 0) else "RGB"
        image = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
        if mode == "RGBA":
            image = image.convert("RGB")
        return np.array(image)
    except Exception:  # noqa: BLE001
        return None


def _easyocr_bbox_to_list(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return []
    try:
        xs = [float(point[0]) for point in value]
        ys = [float(point[1]) for point in value]
    except Exception:  # noqa: BLE001
        return []
    return [min(xs), min(ys), max(xs), max(ys)]
