from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from municipality.chunking import normalize_for_search


TITLE_RE = re.compile(r"^(פרוטוקול|ישיבה|ישיבת)")
COMMITTEE_RE = re.compile(r"^(ועדה|ועדת)")
TOPIC_RE = re.compile(r"^(נושא|פרק)")
SECTION_RE = re.compile(r"^(סעיף|סעיפים)")
DECISION_RE = re.compile(r"^(החלטה|החלטות|סיכום(?:\s+והחלטה)?)")
ORDERED_RE = re.compile(r"^(\d+(?:[.)-]|(?:\.\d+)+))\s+")


@dataclass(slots=True)
class BlockHeadingClassification:
    text: str
    start_offset: int
    end_offset: int
    header_level: int
    node_type: str
    confidence: float
    metadata: dict[str, Any]


def classify_heading_block(*, block: dict[str, Any], block_index: int, page_block_count: int) -> BlockHeadingClassification | None:
    text = " ".join(str(block.get("text") or "").split())
    if not text or len(text) > 180 or text.endswith("."):
        return None

    font_size = _as_float(block.get("font_size"))
    font_weight = str(block.get("font_weight") or "").strip().casefold()
    alignment = str(block.get("alignment") or "").strip().casefold()
    role_guess = str(block.get("role_guess") or "").strip().casefold()
    token_count = len(text.split())
    heading_score = 0.0

    if role_guess == "heading_candidate":
        heading_score += 0.22
    if alignment == "center":
        heading_score += 0.18
    if font_weight == "bold":
        heading_score += 0.18
    if font_size is not None and font_size >= 11.5:
        heading_score += min(0.22, (font_size - 11.5) * 0.04)
    if token_count <= 10:
        heading_score += 0.12
    if text.endswith(":"):
        heading_score += 0.08
    if block_index <= 1:
        heading_score += 0.08
    if page_block_count <= 12:
        heading_score += 0.02

    classification = _pattern_classification(text)
    if classification is not None:
        level, node_type, base_confidence = classification
        return BlockHeadingClassification(
            text=text,
            start_offset=int(block.get("start_offset") or 0),
            end_offset=int(block.get("end_offset") or 0),
            header_level=level,
            node_type=node_type,
            confidence=min(0.99, base_confidence + heading_score),
            metadata={
                "font_size": font_size,
                "font_weight": font_weight or None,
                "alignment": alignment or None,
                "role_guess": role_guess or None,
            },
        )

    if heading_score < 0.38:
        return None
    if token_count > 16:
        return None

    inferred_level = 3 if alignment == "center" else 4
    return BlockHeadingClassification(
        text=text,
        start_offset=int(block.get("start_offset") or 0),
        end_offset=int(block.get("end_offset") or 0),
        header_level=inferred_level,
        node_type="layout_heading",
        confidence=min(0.82, 0.36 + heading_score),
        metadata={
            "font_size": font_size,
            "font_weight": font_weight or None,
            "alignment": alignment or None,
            "role_guess": role_guess or None,
        },
    )


def infer_section_summary(*, header_text: str, body_text: str) -> str | None:
    opening = _opening_sentences(body_text, max_sentences=2, max_chars=260)
    if not opening:
        return None
    header_norm = normalize_for_search(header_text)
    opening_norm = normalize_for_search(opening)
    if header_norm and header_norm in opening_norm:
        return opening
    return f"{header_text}: {opening}" if header_text else opening


def _pattern_classification(text: str) -> tuple[int, str, float] | None:
    if TITLE_RE.match(text):
        return 1, "document_title", 0.74
    if COMMITTEE_RE.match(text):
        return 2, "committee", 0.72
    if TOPIC_RE.match(text):
        return 3, "topic", 0.76
    if SECTION_RE.match(text):
        return 4, "section", 0.74
    if DECISION_RE.match(text):
        return 5, "decision", 0.82
    if ORDERED_RE.match(text) and len(text.split()) <= 14:
        return 4, "ordered_section", 0.64
    if text.endswith(":") and len(text.split()) <= 12:
        return 4, "heading_colon", 0.62
    return None


def _opening_sentences(value: str, *, max_sentences: int, max_chars: int) -> str | None:
    compact = " ".join(str(value or "").split()).strip()
    if not compact:
        return None
    sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", compact) if segment.strip()]
    if not sentences:
        return compact[:max_chars].rstrip()
    summary = " ".join(sentences[:max_sentences]).strip()
    if len(summary) > max_chars:
        summary = f"{summary[: max_chars - 3].rstrip()}..."
    return summary or None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
