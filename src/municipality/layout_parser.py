from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import importlib
import os
import re
import unicodedata
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
        page_payload = page.get_text("dict", sort=False)
        page_words = page.get_text("words", sort=False)
        page_width = float(getattr(page.rect, "width", 0.0) or 0.0)
        page_lines = _extract_page_lines(page_payload, page_width=page_width, page_number=page_index + 1, page_words=page_words)
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
                    **(
                        {
                            "raw_extractor_text": line.get("raw_extractor_text"),
                            "text_reconstructed_from_words": True,
                            "reconstruction_source": line.get("reconstruction_source"),
                        }
                        if line.get("raw_extractor_text")
                        else {}
                    ),
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


def _extract_page_lines(page_payload: dict[str, Any], *, page_width: float, page_number: int, page_words: list[Any] | None = None) -> list[dict[str, Any]]:
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
            raw_text = "".join(span_texts).strip()
            if not raw_text:
                continue
            bbox = _bbox_to_list(line.get("bbox") or block.get("bbox"))
            text = reconstruct_pdf_line_text(raw_text=raw_text, bbox=bbox, page_words=page_words or [])
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
                    **(
                        {
                            "raw_extractor_text": raw_text,
                            "reconstruction_source": "pymupdf_words_directional_runs",
                        }
                        if text != raw_text
                        else {}
                    ),
                }
            )

    rows.sort(key=lambda row: (row["bbox"][1] if row["bbox"] else 0.0, row["bbox"][0] if row["bbox"] else 0.0))
    return rows


def reconstruct_pdf_line_text(
    *, raw_text: str, bbox: list[float], page_words: list[Any] | None = None
) -> str:
    """Reconstruct a single extracted PDF line without applying page-wide ordering."""
    reconstructed = _reconstruct_rtl_numeric_line_text(raw_text=raw_text, bbox=bbox, page_words=page_words or [])
    if reconstructed != raw_text or not _has_noisy_hebrew_quote_shape(raw_text):
        return reconstructed
    return _cleanup_hebrew_quote_noise(" ".join(str(raw_text or "").split()), raw_text=raw_text)


def _reconstruct_rtl_numeric_line_text(*, raw_text: str, bbox: list[float], page_words: list[Any]) -> str:
    if not _needs_rtl_numeric_reconstruction(raw_text) or len(bbox) != 4 or not page_words:
        return raw_text
    x0, y0, x1, y1 = bbox
    tolerance = 2.5
    selected_words: list[Any] = []
    for word in page_words:
        if not isinstance(word, (list, tuple)) or len(word) < 5:
            continue
        word_x0, word_y0, word_x1, word_y1, word_text = word[:5]
        if not str(word_text or "").strip():
            continue
        center_x = (float(word_x0) + float(word_x1)) / 2.0
        center_y = (float(word_y0) + float(word_y1)) / 2.0
        if x0 - tolerance <= center_x <= x1 + tolerance and y0 - tolerance <= center_y <= y1 + tolerance:
            selected_words.append(word)
    selected_words = _select_best_positioned_word_line(raw_text=raw_text, words=selected_words)
    if len(selected_words) < 2:
        return raw_text
    if not _selected_words_cover_raw_line(raw_text=raw_text, words=selected_words):
        return raw_text
    reconstructed = _cleanup_reconstructed_rtl_numeric_text(
        _logical_text_from_positioned_words(selected_words),
        raw_text=raw_text,
    )
    if not reconstructed or not _reconstruction_preserves_audit_tokens(raw_text=raw_text, reconstructed=reconstructed):
        return raw_text
    return reconstructed


def _reconstruction_preserves_audit_tokens(*, raw_text: str, reconstructed: str) -> bool:
    # Reordering may change token order, but it should not invent or drop numeric evidence.
    raw_digits = re.findall(r"\d", raw_text)
    reconstructed_digits = re.findall(r"\d", reconstructed)
    if sorted(raw_digits) != sorted(reconstructed_digits):
        return False
    raw_numeric_tokens = Counter(re.findall(r"\d+", raw_text))
    reconstructed_numeric_tokens = Counter(re.findall(r"\d+", reconstructed))
    if raw_numeric_tokens != reconstructed_numeric_tokens:
        return False
    raw_brackets = sum(raw_text.count(char) for char in "()[]{}")
    reconstructed_brackets = sum(reconstructed.count(char) for char in "()[]{}")
    return raw_brackets == reconstructed_brackets


def _selected_words_cover_raw_line(*, raw_text: str, words: list[Any]) -> bool:
    raw_score = len(_normalized_counter_text(raw_text))
    if raw_score < 20:
        return True
    word_score = _positioned_word_group_score(words)
    if word_score >= raw_score * 0.55:
        return True
    return _positioned_word_group_similarity(raw_text=raw_text, words=words) >= 0.55


def _select_best_positioned_word_line(*, raw_text: str, words: list[Any]) -> list[Any]:
    groups: dict[tuple[Any, Any], list[Any]] = {}
    for word in words:
        if isinstance(word, (list, tuple)) and len(word) >= 8:
            groups.setdefault((word[5], word[6]), []).append(word)
    if len(groups) <= 1:
        return words
    scored_groups = [(_positioned_word_group_similarity(raw_text=raw_text, words=group), _positioned_word_group_score(group), group) for group in groups.values()]
    scored_groups.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_similarity, _, best_group = scored_groups[0]
    second_similarity = scored_groups[1][0] if len(scored_groups) > 1 else 0.0
    if best_similarity >= 0.68 and best_similarity - second_similarity >= 0.12:
        return best_group
    best_score = _positioned_word_group_score(best_group)
    total_score = sum(_positioned_word_group_score(group) for group in groups.values())
    raw_score = len("".join(str(raw_text or "").split()))
    if best_score >= max(total_score * 0.65, raw_score * 0.45, 2):
        return best_group
    return words


def _positioned_word_group_score(words: list[Any]) -> int:
    return sum(len("".join(_positioned_word_text(word).split())) for word in words)


def _positioned_word_group_similarity(*, raw_text: str, words: list[Any]) -> float:
    raw = _normalized_counter_text(raw_text)
    candidate = _normalized_counter_text("".join(_positioned_word_text(word) for word in sorted(words, key=lambda item: float(item[0]))))
    if not raw or not candidate:
        return 0.0
    raw_counter = Counter(raw)
    candidate_counter = Counter(candidate)
    overlap = sum((raw_counter & candidate_counter).values())
    return (2.0 * overlap) / (len(raw) + len(candidate))


def _normalized_counter_text(value: str) -> str:
    return "".join(char for char in str(value or "") if not char.isspace())


def _logical_text_from_positioned_words(words: list[Any]) -> str:
    visual_words = sorted(words, key=lambda word: (float(word[0]), float(word[1])))
    if not visual_words:
        return ""
    base_direction = _positioned_words_base_direction(visual_words)
    visual_words = _merge_touching_mixed_identifier_words(visual_words=visual_words, base_direction=base_direction)
    directions = [_token_direction(_positioned_word_text(word)) for word in visual_words]
    directions = _resolve_positioned_word_neutrals(visual_words=visual_words, directions=directions, base_direction=base_direction)
    minor_direction = "L" if base_direction == "R" else "R"
    runs: list[tuple[str, list[Any]]] = []
    for word, direction in zip(visual_words, directions):
        if direction == minor_direction and runs and runs[-1][0] == minor_direction:
            runs[-1][1].append(word)
        elif direction == base_direction and runs and runs[-1][0] == base_direction:
            runs[-1][1].append(word)
        else:
            runs.append((direction, [word]))
    if base_direction == "R":
        runs.reverse()
    ordered_words: list[Any] = []
    for direction, run_words in runs:
        if direction == "L":
            ordered_words.extend(sorted(run_words, key=lambda word: float(word[0])))
        else:
            ordered_words.extend(sorted(run_words, key=lambda word: float(word[2]), reverse=True))
    return " ".join(_positioned_word_text(word) for word in ordered_words if _positioned_word_text(word))


def _merge_touching_mixed_identifier_words(*, visual_words: list[Any], base_direction: str) -> list[Any]:
    merged: list[Any] = []
    index = 0
    while index < len(visual_words):
        current = visual_words[index]
        if index + 1 >= len(visual_words):
            merged.append(current)
            break
        next_word = visual_words[index + 1]
        combined_text = _touching_mixed_identifier_text(current=current, next_word=next_word, base_direction=base_direction)
        if not combined_text:
            merged.append(current)
            index += 1
            continue
        merged.append(
            (
                min(float(current[0]), float(next_word[0])),
                min(float(current[1]), float(next_word[1])),
                max(float(current[2]), float(next_word[2])),
                max(float(current[3]), float(next_word[3])),
                combined_text,
            )
        )
        index += 2
    return merged


def _touching_mixed_identifier_text(*, current: Any, next_word: Any, base_direction: str) -> str | None:
    if not isinstance(current, (list, tuple)) or not isinstance(next_word, (list, tuple)) or len(current) < 5 or len(next_word) < 5:
        return None
    gap = float(next_word[0]) - float(current[2])
    if gap > 0.75:
        return None
    current_text = _positioned_word_text(current)
    next_text = _positioned_word_text(next_word)
    if not _compact_identifier_pair(current_text, next_text):
        return None
    return f"{next_text}{current_text}" if base_direction == "R" else f"{current_text}{next_text}"


def _compact_identifier_pair(left_text: str, right_text: str) -> bool:
    return (_single_letter(left_text) and right_text.isdigit()) or (left_text.isdigit() and _single_letter(right_text))


def _single_letter(value: str) -> bool:
    return len(value) == 1 and any(unicodedata.category(char).startswith("L") for char in value)


def _positioned_words_base_direction(words: list[Any]) -> str:
    rtl = 0
    ltr = 0
    for word in words:
        for char in _positioned_word_text(word):
            bidi_class = unicodedata.bidirectional(char)
            if bidi_class in {"R", "AL"}:
                rtl += 1
            elif bidi_class == "L":
                ltr += 1
    return "R" if rtl and rtl >= ltr else "L"


def _token_direction(text: str) -> str:
    classes = {unicodedata.bidirectional(char) for char in str(text or "") if not char.isspace()}
    if classes & {"R", "AL"}:
        return "R"
    if "L" in classes or classes & {"EN", "AN"}:
        return "L"
    return "N"


def _resolve_positioned_word_neutrals(*, visual_words: list[Any], directions: list[str], base_direction: str) -> list[str]:
    resolved = directions.copy()
    for index, direction in enumerate(directions):
        if direction != "N":
            continue
        left_index = _nearest_non_neutral_index(directions=directions, start=index, step=-1)
        right_index = _nearest_non_neutral_index(directions=directions, start=index, step=1)
        left_direction = directions[left_index] if left_index is not None else None
        right_direction = directions[right_index] if right_index is not None else None
        if left_direction is not None and left_direction == right_direction:
            resolved[index] = left_direction
            continue
        if _positioned_word_text(visual_words[index]) in {"%", "‰", "₪", "$", "€", "£"}:
            numeric_neighbor = any(
                neighbor_index is not None
                and directions[neighbor_index] == "L"
                and any(char.isdigit() for char in _positioned_word_text(visual_words[neighbor_index]))
                for neighbor_index in (left_index, right_index)
            )
            if numeric_neighbor:
                resolved[index] = "L"
                continue
        resolved[index] = base_direction
    return resolved


def _nearest_non_neutral_index(*, directions: list[str], start: int, step: int) -> int | None:
    index = start + step
    while 0 <= index < len(directions):
        if directions[index] != "N":
            return index
        index += step
    return None


def _positioned_word_text(word: Any) -> str:
    return str(word[4]).strip() if isinstance(word, (list, tuple)) and len(word) >= 5 else ""


def _needs_rtl_numeric_reconstruction(value: str) -> bool:
    text = str(value or "")
    if not any("\u0590" <= char <= "\u05FF" for char in text):
        return False
    return any(char.isdigit() for char in text)


def _cleanup_reconstructed_rtl_numeric_text(value: str, *, raw_text: str | None = None) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    text = _cleanup_hebrew_quote_noise(text, raw_text=raw_text)
    text = re.sub(r"([\u0590-\u05FF])\s+'", r"\1'", text)
    text = re.sub(r"\s+([.,:;?!%)\]])", r"\1", text)
    text = re.sub(r"([([{])\s+", r"\1", text)
    text = re.sub(r"\b([\u0590-\u05FF]{1,3})\s+(\d+(?:\.\d+)?)%-", r"\1-\2%", text)
    text = re.sub(r"\b([\u0590-\u05FF])\s+(\d[\d./]*(?:-\d[\d./]*)+)\b", r"\1-\2", text)
    text = re.sub(r"\b([\u0590-\u05FF])\s+(\d[\d./]*)-(?!\d)", r"\1-\2", text)
    text = re.sub(r"\)(\d{1,2}[./]\d{1,2}[./]\d{2,4})\(", r"(\1)", text)
    text = re.sub(r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})\(\)", r"(\1)", text)
    text = re.sub(r"([\u0590-\u05FF])\((\d)", r"\1 (\2", text)
    text = re.sub(r"([\u0590-\u05FF])\.(\d+)(?=\s|$)", r"\1 \2.", text)
    text = re.sub(r"(\d+)\s+([\u0590-\u05FF])\.(\d+)-(?=\s|$)", r"\1 \2-\3.", text)
    text = re.sub(r"([\u0590-\u05FF])\s+-(\d[\d./]*/\d[\d./]*)\s+([\u0590-\u05FF])", r"\1 \2 - \3", text)
    text = re.sub(r"(\d+):,\s+([\u0590-\u05FF][\u0590-\u05FF\"'׳״]*)", r"\1, \2:", text)
    text = re.sub(r"(\d[\d./]*)\s+([\u0590-\u05FF])\s+([\u0590-\u05FF]{2,})\b", r"\1 \2\3", text)
    text = re.sub(r"([\u0590-\u05FF][\u0590-\u05FF\"'׳״]*)\s+(\d{1,2})\s+(\d{4})-,", r"\1-\3, \2", text)
    text = re.sub(r"([\u0590-\u05FF][\u0590-\u05FF\"'׳״]*)\s+(\d{1,2})\s+(\d{4})([-–])", r"\1\4\3 \2", text)
    text = re.sub(r"([\u0590-\u05FF][\u0590-\u05FF\"'׳״]*)\s+([-–])\s+(\d{1,2})\s+(\d{4})", r"\1 \2 \4 \3", text)
    text = re.sub(r"([\u0590-\u05FF][\u0590-\u05FF\"'׳״]*)([;\"'׳״]+)(\d{1,2})\s+(\d{4})-", r"\1-\4 \2\3", text)
    text = re.sub(r"\b([\u0590-\u05FF]{1,2})\s+(\d[\d./]*)-\s+([\u0590-\u05FF])", r"\1-\2 \3", text)
    text = re.sub(r"\b([\u0590-\u05FF]{3,})\s+(\d[\d./]*)-\s+([\u0590-\u05FF])", r"\1 \2 \3", text)
    text = re.sub(r"(\d[\d./]*)-,\.,", r"\1,", text)
    text = re.sub(r"^:,\s+([\u0590-\u05FF][\u0590-\u05FF\"'׳״]*)\s+(\d{2,3}-\d{5,8})$", r"\1: \2", text)
    text = re.sub(r"^([\u0590-\u05FF][\u0590-\u05FF\"'׳״]*):,\s+(\d{2,3}-\d{5,8})$", r"\1: \2", text)
    text = re.sub(r"^:,\s+([\u0590-\u05FF][\u0590-\u05FF\"'׳״]*)\s+(/?\d[\d./-]*)$", r"\1: \2", text)
    text = re.sub(r"^:,\s+(.+?)\s+(\d{1,3})$", r"\2: \1", text)
    text = re.sub(r"([\u0590-\u05FF][\u0590-\u05FF\"'׳״]*)\s+(\d{1,2}/\d{1,2}/\d{2,4}):,", r"\1: \2", text)
    text = re.sub(r"(\d{4})\s+(\d{1,2}):,", r"\1, \2:", text)
    text = re.sub(r"([\u0590-\u05FF][\u0590-\u05FF\"'׳״]*):,\s*(/?\d[\d./-]*)", r"\1: \2", text)
    text = re.sub(r"(\d{1,2}:\d{2});,", r"\1;", text)
    text = re.sub(r"([\u0590-\u05FF]):(\d[\d./]*)$", r"\1 \2:", text)
    text = re.sub(r"^(\d{1,3})\s+(\d{4})\)\.\s+(.+?)\s*\(([^()]*)$", r"\1. \3 (\4) \2", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _cleanup_hebrew_quote_noise(text: str, *, raw_text: str | None = None) -> str:
    text = _repair_source_quoted_hebrew_word_splits(text=text, raw_text=raw_text)
    text = re.sub(r"\b([\u0590-\u05FF]{1,4})\s*([\"”״])\s*([\u0590-\u05FF]{1,2})\b", r"\1\2\3", text)
    text = re.sub(r"\b([\u0590-\u05FF]{3,4})([\"”״])([\u0590-\u05FF]{2,})\b", r"\1\3", text)
    text = re.sub(r"(^|\s)([\"”״])\s+([\u0590-\u05FF])", r"\1\2\3", text)
    text = re.sub(r"([\u0590-\u05FF])\s+([\"”״])(?=\s*(?:[-–.,:;?!)]|$))", r"\1\2", text)
    text = re.sub(r"([\"”״])'([\"”״])", r"\2", text)
    text = re.sub(r"\b([\u0590-\u05FF]{5,})([\"”״])([\u0590-\u05FF]{2,})", r"\1 \2\3", text)
    text = re.sub(r"([\"”״])'(?=\s*(?:[-–.,:;?!)]|$))", r"\1", text)
    text = re.sub(r"([\u0590-\u05FF])\s+([\"”״])(?=\s*(?:[-–.,:;?!)]|$))", r"\1\2", text)
    return text


def _has_noisy_hebrew_quote_shape(value: str) -> bool:
    text = str(value or "")
    if not any("\u0590" <= char <= "\u05FF" for char in text):
        return False
    return bool(
        re.search(r"[\"”״]'[\"”״]", text)
        or re.search(r"\b[\u0590-\u05FF]{3,}[\"”״][\u0590-\u05FF]{2,}\b", text)
        or re.search(r"\b[\u0590-\u05FF]{2,}\s+[\"”״]'\s*[\u0590-\u05FF]\b", text)
    )


def _repair_source_quoted_hebrew_word_splits(*, text: str, raw_text: str | None) -> str:
    if not raw_text:
        return text
    repaired = text
    raw = str(raw_text or "")
    for match in re.finditer(r"\b([\u0590-\u05FF]{3,})([\"”״])([\u0590-\u05FF]{2,})\b", raw):
        left, _, right = match.groups()
        repaired = re.sub(rf"\b{re.escape(left)}\s+{re.escape(right)}\b", f"{left}{right}", repaired)
        repaired = re.sub(rf"\b{re.escape(left)}[\"”״]{re.escape(right)}\b", f"{left}{right}", repaired)
    for match in re.finditer(r"\b([\u0590-\u05FF]{2,})\s+[\"”״]'\s*([\u0590-\u05FF])\b", raw):
        left, right = match.groups()
        repaired = re.sub(rf"\b{re.escape(left)}\s+{re.escape(right)}\b", f"{left}{right}", repaired)
    return repaired


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
