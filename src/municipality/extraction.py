from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from municipality.layout_parser import parse_pdf_layout, parse_pdf_ocr


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
    quality_summary: dict[str, float | int]
    error_code: str | None
    warning_text: str | None


class PdfTextExtractor:
    def __init__(self, pdftotext_path: str | None = None, timeout_seconds: float = 45.0):
        self.pdftotext_path = pdftotext_path or shutil.which("pdftotext")
        self.timeout_seconds = timeout_seconds

    def extract(self, pdf_bytes: bytes) -> PdfExtractionResult:
        layout_result = parse_pdf_layout(pdf_bytes)
        if layout_result is not None and layout_result.full_text.strip():
            pages = _pages_from_layout_result(layout_result)
            quality_score, quality_flags, quality_summary = score_extraction_quality(layout_result.full_text, pages)
            layout_extraction = PdfExtractionResult(
                ok=True,
                parser_name=layout_result.backend_name,
                parser_version=None,
                full_text=layout_result.full_text,
                pages=pages,
                citation_map=layout_result.citation_map,
                quality_score=quality_score,
                quality_flags=quality_flags,
                quality_summary=quality_summary,
                error_code=None,
                warning_text=None,
            )
            ocr_extraction = _ocr_fallback_extraction(pdf_bytes=pdf_bytes, current=layout_extraction)
            if ocr_extraction is not None:
                return ocr_extraction
            return layout_extraction

        if not self.pdftotext_path:
            return PdfExtractionResult(
                ok=False,
                parser_name="pdftotext",
                parser_version=None,
                full_text="",
                pages=[],
                citation_map=[],
                quality_score=None,
                quality_flags=["MISSING_PARSER"],
                quality_summary={},
                error_code="MISSING_PARSER",
                warning_text=None,
            )

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(pdf_bytes)

        try:
            proc = subprocess.run(
                [self.pdftotext_path, "-layout", "-enc", "UTF-8", str(tmp_path), "-"],
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            tmp_path.unlink(missing_ok=True)
            return PdfExtractionResult(
                ok=False,
                parser_name="pdftotext",
                parser_version=None,
                full_text="",
                pages=[],
                citation_map=[],
                quality_score=None,
                quality_flags=["TIMEOUT"],
                quality_summary={},
                error_code="TIMEOUT",
                warning_text=None,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        if proc.returncode != 0:
            warning = proc.stderr.decode("utf-8", errors="replace").strip() or None
            return PdfExtractionResult(
                ok=False,
                parser_name="pdftotext",
                parser_version=None,
                full_text="",
                pages=[],
                citation_map=[],
                quality_score=None,
                quality_flags=["PARSER_ERROR"],
                quality_summary={},
                error_code="PARSER_ERROR",
                warning_text=warning,
            )

        text = proc.stdout.decode("utf-8", errors="replace")
        warning = proc.stderr.decode("utf-8", errors="replace").strip() or None
        full_text, pages, citation_map = parse_extracted_text(text)
        quality_score, quality_flags, quality_summary = score_extraction_quality(full_text, pages)

        extraction = PdfExtractionResult(
            ok=True,
            parser_name="pdftotext",
            parser_version=None,
            full_text=full_text,
            pages=pages,
            citation_map=citation_map,
            quality_score=quality_score,
            quality_flags=quality_flags,
            quality_summary=quality_summary,
            error_code=None,
            warning_text=warning,
        )
        ocr_extraction = _ocr_fallback_extraction(pdf_bytes=pdf_bytes, current=extraction)
        if ocr_extraction is not None:
            return ocr_extraction
        return extraction


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


def _ocr_fallback_extraction(*, pdf_bytes: bytes, current: PdfExtractionResult) -> PdfExtractionResult | None:
    if not _should_attempt_ocr(current):
        return None
    ocr_result = parse_pdf_ocr(pdf_bytes)
    if ocr_result is None or not ocr_result.full_text.strip():
        return None
    pages = _pages_from_layout_result(ocr_result)
    quality_score, quality_flags, quality_summary = score_extraction_quality(ocr_result.full_text, pages)
    candidate = PdfExtractionResult(
        ok=True,
        parser_name=ocr_result.backend_name,
        parser_version=None,
        full_text=ocr_result.full_text,
        pages=pages,
        citation_map=ocr_result.citation_map,
        quality_score=quality_score,
        quality_flags=quality_flags,
        quality_summary=quality_summary,
        error_code=None,
        warning_text=current.warning_text,
    )
    if _is_better_ocr_candidate(candidate=candidate, current=current):
        return candidate
    return None


def _should_attempt_ocr(current: PdfExtractionResult) -> bool:
    if not current.ok:
        return True
    flags = set(current.quality_flags or [])
    if "OCR_CANDIDATE" in flags:
        return True
    if not current.full_text.strip():
        return True
    return False


def _is_better_ocr_candidate(*, candidate: PdfExtractionResult, current: PdfExtractionResult) -> bool:
    current_score = float(current.quality_score or 0.0)
    candidate_score = float(candidate.quality_score or 0.0)
    if candidate_score > (current_score + 0.08):
        return True
    if len(candidate.full_text.strip()) > max(400, len(current.full_text.strip()) * 2):
        return True
    return False


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
