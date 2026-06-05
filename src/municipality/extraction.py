from __future__ import annotations

import re
from dataclasses import dataclass, field

from municipality.layout_parser import parse_pdf_layout, parse_pdf_ocr
from municipality.qwen_ocr import QwenVisionOcrClient


HEBREW_CHAR_RE = re.compile(r"[\u0590-\u05FF]")
BIDI_CONTROL_RE = re.compile(r"[\u200E\u200F\u202A-\u202E]")
WHITESPACE_RE = re.compile(r"[ \t]+")


@dataclass(slots=True)
class ExtractedPage:
    page: int
    text: str
    start_offset: int
    end_offset: int
    reading_direction: str | None = None
    layout_blocks: list[dict[str, object]] = field(default_factory=list)


@dataclass(slots=True)
class PdfExtractionResult:
    ok: bool
    parser_name: str
    parser_version: str | None
    full_text: str
    pages: list[ExtractedPage]
    citation_map: list[dict[str, int]]
    quality_score: float | None
    quality_flags: list[str]
    quality_summary: dict[str, object]
    error_code: str | None
    warning_text: str | None


class PdfTextExtractor:
    def __init__(self, qwen_ocr_client: QwenVisionOcrClient | None = None):
        self.qwen_ocr_client = qwen_ocr_client

    def extract(self, pdf_bytes: bytes) -> PdfExtractionResult:
        layout_result = parse_pdf_ocr(pdf_bytes, client=self.qwen_ocr_client)
        if layout_result is None:
            return PdfExtractionResult(
                ok=False,
                parser_name="qwen_vision_ocr",
                parser_version=None,
                full_text="",
                pages=[],
                citation_map=[],
                quality_score=None,
                quality_flags=["QWEN_OCR_FAILED"],
                quality_summary={},
                error_code="QWEN_OCR_FAILED",
                warning_text="Qwen OCR did not return a parsed document",
            )

        native_layout_result = parse_pdf_layout(pdf_bytes)
        pages = _pages_from_layout_result(layout_result)
        quality_score, quality_flags, quality_summary = score_extraction_quality(layout_result.full_text, pages)
        quality_summary = {**quality_summary, **dict(layout_result.metadata)}
        if native_layout_result is not None:
            quality_summary.update(
                {
                    "native_parser_name": native_layout_result.backend_name,
                    "native_text_chars": len(native_layout_result.full_text),
                    "native_page_count": len(native_layout_result.pages),
                    "native_citation_count": len(native_layout_result.citation_map),
                }
            )
        if layout_result.metadata.get("ocr_failed_pages"):
            quality_flags.append("OCR_PAGE_FAILURE")
        if layout_result.metadata.get("ocr_parse_warning_pages"):
            quality_flags.append("OCR_PARSE_WARNING")
        ok = bool(layout_result.full_text.strip())

        return PdfExtractionResult(
            ok=ok,
            parser_name=layout_result.backend_name,
            parser_version=None,
            full_text=layout_result.full_text,
            pages=pages,
            citation_map=layout_result.citation_map,
            quality_score=quality_score,
            quality_flags=sorted(set(quality_flags)),
            quality_summary=quality_summary,
            error_code=None if ok else "QWEN_OCR_EMPTY_TEXT",
            warning_text=None if ok else "Qwen OCR returned no text",
        )


def parse_extracted_text(raw_text: str) -> tuple[str, list[ExtractedPage], list[dict[str, int]]]:
    raw_pages = raw_text.split("\f")

    full_parts: list[str] = []
    pages: list[ExtractedPage] = []
    citation_map: list[dict[str, int]] = []
    cursor = 0

    for idx, raw_page in enumerate(raw_pages, start=1):
        cleaned = normalize_pdf_text(raw_page)
        start = cursor
        end = start + len(cleaned)
        pages.append(
            ExtractedPage(
                page=idx,
                text=cleaned,
                start_offset=start,
                end_offset=end,
                reading_direction=_reading_direction(cleaned),
                layout_blocks=_layout_blocks_for_page(cleaned, page_start_offset=start),
            )
        )
        if cleaned:
            full_parts.append(cleaned)
            citation_map.append({"start": start, "end": end, "page": idx})
        if idx < len(raw_pages):
            full_parts.append("\n\n")
            cursor = end + 2
        else:
            cursor = end

    full_text = "".join(full_parts)

    return full_text, pages, citation_map


def normalize_pdf_text(value: str) -> str:
    without_bidi = BIDI_CONTROL_RE.sub("", value)
    without_cr = without_bidi.replace("\r", "")
    normalized_lines = [WHITESPACE_RE.sub(" ", line).strip() for line in without_cr.split("\n")]
    return "\n".join(line for line in normalized_lines if line)


def _layout_blocks_for_page(value: str, *, page_start_offset: int) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    cursor = page_start_offset
    for line_index, raw_line in enumerate(value.split("\n")):
        line = str(raw_line or "")
        start_offset = cursor
        end_offset = start_offset + len(line)
        cursor = end_offset + 1
        compact = line.strip()
        if not compact:
            continue
        blocks.append(
            {
                "kind": "line",
                "line_index": line_index,
                "start_offset": start_offset,
                "end_offset": end_offset,
                "text": compact,
                "reading_direction": _reading_direction(compact),
            }
        )
    return blocks


def _pages_from_layout_result(layout_result) -> list[ExtractedPage]:
    pages: list[ExtractedPage] = []
    for page in layout_result.pages:
        page_text = str(page.text or "")
        page_start = None
        page_end = None
        if page.blocks:
            page_start = min(int(block.get("start_offset") or 0) for block in page.blocks)
            page_end = max(int(block.get("end_offset") or 0) for block in page.blocks)
        if page_start is None:
            page_start = 0
        if page_end is None:
            page_end = page_start + len(page_text)
        pages.append(
            ExtractedPage(
                page=page.page,
                text=page_text,
                start_offset=page_start,
                end_offset=page_end,
                reading_direction=_reading_direction(page_text),
                layout_blocks=[dict(block) for block in page.blocks],
            )
        )
    return pages


def _reading_direction(value: str) -> str | None:
    if not value:
        return None
    hebrew_chars = sum(1 for char in value if HEBREW_CHAR_RE.match(char) is not None)
    latin_chars = sum(1 for char in value if "a" <= char.casefold() <= "z")
    if hebrew_chars > max(0, latin_chars):
        return "rtl"
    if latin_chars > 0:
        return "ltr"
    return None


def score_extraction_quality(
    full_text: str, pages: list[ExtractedPage]
) -> tuple[float, list[str], dict[str, float | int]]:
    total_chars = len(full_text)
    total_pages = len(pages)
    non_empty_pages = sum(1 for page in pages if page.text.strip())
    replacement_chars = full_text.count("\ufffd")
    hebrew_chars = sum(1 for char in full_text if HEBREW_CHAR_RE.match(char) is not None)
    hebrew_ratio = (hebrew_chars / total_chars) if total_chars else 0.0

    flags: list[str] = []
    score = 1.0

    if total_chars == 0:
        flags.append("EMPTY_TEXT")
        score = 0.0
    if total_chars < 400 and total_chars > 0:
        flags.append("TOO_SHORT")
        score -= 0.35
    if replacement_chars > 0:
        flags.append("REPLACEMENT_CHARS")
        score -= 0.15
    if total_chars > 0 and hebrew_ratio < 0.2:
        flags.append("LOW_HEBREW_RATIO")
        score -= 0.25
    if total_pages > 0 and (non_empty_pages / total_pages) < 0.6:
        flags.append("MANY_EMPTY_PAGES")
        score -= 0.15

    score = max(0.0, min(1.0, score))
    if "TOO_SHORT" in flags or score < 0.55:
        flags.append("OCR_CANDIDATE")

    summary: dict[str, float | int] = {
        "total_chars": total_chars,
        "total_pages": total_pages,
        "non_empty_pages": non_empty_pages,
        "replacement_chars": replacement_chars,
        "hebrew_ratio": round(hebrew_ratio, 4),
    }
    return score, sorted(set(flags)), summary


def resolve_pages_for_span(citation_map: list[dict[str, int]], start: int, end: int) -> list[int]:
    pages: list[int] = []
    for item in citation_map:
        page_start = item["start"]
        page_end = item["end"]
        if page_end <= start:
            continue
        if page_start >= end:
            continue
        pages.append(item["page"])
    return sorted(set(pages))
