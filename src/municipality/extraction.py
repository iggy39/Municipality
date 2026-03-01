from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


HEBREW_CHAR_RE = re.compile(r"[\u0590-\u05FF]")
BIDI_CONTROL_RE = re.compile(r"[\u200E\u200F\u202A-\u202E]")
WHITESPACE_RE = re.compile(r"[ \t]+")


@dataclass(slots=True)
class ExtractedPage:
    page: int
    text: str
    start_offset: int
    end_offset: int


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

        return PdfExtractionResult(
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
        pages.append(ExtractedPage(page=idx, text=cleaned, start_offset=start, end_offset=end))
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
