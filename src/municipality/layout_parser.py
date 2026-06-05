from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import os
from typing import Any

from municipality.qwen_ocr import (
    DEFAULT_QWEN_OCR_DPI,
    DEFAULT_QWEN_OCR_IMAGE_FORMAT,
    QwenOcrPageResult,
    QwenVisionOcrClient,
)


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
    metadata: dict[str, Any] = field(default_factory=dict)


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


def parse_pdf_ocr(pdf_bytes: bytes, *, client: QwenVisionOcrClient | None = None) -> ParsedLayoutDocument | None:
    fitz_module = _load_fitz()
    resolved_client = client or QwenVisionOcrClient()
    is_configured = getattr(resolved_client, "is_configured", lambda: True)
    if fitz_module is None or not is_configured():
        return None

    try:
        document = fitz_module.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return None

    try:
        return _parse_with_qwen_ocr(document, client=resolved_client)
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


def _parse_with_qwen_ocr(document: Any, *, client: QwenVisionOcrClient) -> ParsedLayoutDocument:
    pages: list[ParsedLayoutPage] = []
    full_parts: list[str] = []
    citation_map: list[dict[str, int]] = []
    cursor = 0
    dpi = _qwen_ocr_dpi()
    image_format = _qwen_ocr_image_format()
    failed_pages: list[int] = []
    parse_warning_pages: list[int] = []
    confidence_values: list[float] = []

    for page_index in range(len(document)):
        page = document.load_page(page_index)
        image_bytes, image_mime = _render_page_image(page, dpi=dpi, image_format=image_format)
        ocr_result = client.ocr_page(
            image_bytes=image_bytes,
            page_number=page_index + 1,
            image_mime=image_mime,
        )
        if ocr_result.error_code:
            failed_pages.append(page_index + 1)
        if ocr_result.parse_warning:
            parse_warning_pages.append(page_index + 1)
        if ocr_result.ocr_confidence is not None:
            confidence_values.append(float(ocr_result.ocr_confidence))
        page_text = _normalize_qwen_page_text(ocr_result.text)
        page_start = cursor
        page_end = page_start + len(page_text)
        blocks = _qwen_layout_blocks_for_page(
            result=ocr_result,
            page_text=page_text,
            page_start_offset=page_start,
        )
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
        backend_name="qwen_vision_ocr",
        metadata={
            "ocr_engine": "qwen_vision",
            "ocr_model": getattr(client, "model_name", None),
            "ocr_provider": getattr(client, "provider", None),
            "ocr_dpi": dpi,
            "ocr_image_format": image_format,
            "ocr_page_count": len(document),
            "ocr_failed_pages": failed_pages,
            "ocr_parse_warning_pages": parse_warning_pages,
            "ocr_confidence_avg": (
                round(sum(confidence_values) / len(confidence_values), 4)
                if confidence_values
                else None
            ),
        },
    )


def _render_page_image(page: Any, *, dpi: int, image_format: str) -> tuple[bytes, str]:
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    normalized_format = image_format.strip().casefold()
    if normalized_format in {"jpg", "jpeg"}:
        return pix.tobytes("jpg"), "image/jpeg"
    return pix.tobytes("png"), "image/png"


def _normalize_qwen_page_text(value: str) -> str:
    without_cr = str(value or "").replace("\r", "")
    lines = [line.rstrip() for line in without_cr.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _qwen_layout_blocks_for_page(
    *,
    result: QwenOcrPageResult,
    page_text: str,
    page_start_offset: int,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    heading_values = {
        " ".join(value.split()).strip()
        for value in result.detected_headings
        if str(value).strip()
    }
    cursor = page_start_offset
    for line_index, raw_line in enumerate(page_text.split("\n")):
        line_text = str(raw_line or "")
        start_offset = cursor
        end_offset = start_offset + len(line_text)
        cursor = end_offset + 1
        compact = " ".join(line_text.split()).strip()
        if not compact:
            continue
        role_guess = "heading_candidate" if compact in heading_values else _role_guess(
            text=compact,
            font_size=0.0,
            alignment=None,
            font_weight=None,
        )
        block = {
            "kind": "line",
            "block_index": line_index,
            "line_index": line_index,
            "start_offset": start_offset,
            "end_offset": end_offset,
            "text": compact,
            "bbox": [],
            "reading_direction": _reading_direction(compact),
            "font_size": None,
            "font_weight": None,
            "alignment": None,
            "column_index": None,
            "is_table": "|" in compact,
            "role_guess": role_guess or "qwen_ocr_line",
            "source": "qwen_vision_ocr",
            "ocr_confidence": result.ocr_confidence,
        }
        if not blocks:
            block.update(
                {
                    "qwen_quality_notes": result.quality_notes,
                    "qwen_parse_warning": result.parse_warning,
                    "qwen_tables_markdown": list(result.tables_markdown),
                    "qwen_uncertain_regions": list(result.uncertain_regions),
                }
            )
        blocks.append(block)
    return blocks


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
    if token_count <= 14 and (
        compact.endswith(":")
        or compact.startswith(("נושא", "סעיף", "החלט", "פרוטוקול", "ועדת", "ועדה"))
    ):
        return "heading_candidate"
    if token_count <= 14 and (alignment == "center" or font_weight == "bold") and font_size >= 11.5:
        return "heading_candidate"
    return "body"


def _qwen_ocr_dpi() -> int:
    raw_value = os.getenv("QWEN_OCR_DPI")
    if raw_value:
        try:
            return max(72, min(600, int(raw_value)))
        except ValueError:
            return DEFAULT_QWEN_OCR_DPI
    return DEFAULT_QWEN_OCR_DPI


def _qwen_ocr_image_format() -> str:
    value = (os.getenv("QWEN_OCR_IMAGE_FORMAT") or DEFAULT_QWEN_OCR_IMAGE_FORMAT).strip().casefold()
    if value in {"jpg", "jpeg"}:
        return "jpeg"
    return "png"


def _load_fitz() -> Any | None:
    try:
        return importlib.import_module("fitz")
    except Exception:  # noqa: BLE001
        return None
