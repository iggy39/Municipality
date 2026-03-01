from __future__ import annotations

import hashlib
import re

from municipality.extraction import resolve_pages_for_span


HEADING_PREFIX_RE = re.compile(r"^(סעיף|פרק|נושא|החלטה|סיכום)")
ORDERED_PREFIX_RE = re.compile(r"^\d+[.)-]\s")
WHITESPACE_RE = re.compile(r"\s+")


def build_chunks(
    *,
    document_version_id: int,
    text: str,
    citation_map: list[dict[str, int]],
    source_kind: str,
    chunk_chars: int = 900,
    overlap_chars: int = 150,
) -> list[dict]:
    if not text.strip():
        return []

    section_spans = _detect_section_spans(text)
    chunks: list[dict] = []

    for section_start, section_end in section_spans:
        section_text = text[section_start:section_end]
        if not section_text.strip():
            continue

        if len(section_text) <= chunk_chars:
            left_trim = len(section_text) - len(section_text.lstrip())
            right_trim = len(section_text) - len(section_text.rstrip())
            start_offset = section_start + left_trim
            end_offset = section_end - right_trim
            segment = section_text.strip()
            if not segment:
                continue
            chunk = _make_chunk(
                document_version_id=document_version_id,
                source_kind=source_kind,
                start_offset=start_offset,
                end_offset=end_offset,
                text=segment,
                citation_map=citation_map,
            )
            chunks.append(chunk)
            continue

        rel_start = 0
        while rel_start < len(section_text):
            rel_end = min(rel_start + chunk_chars, len(section_text))
            segment_raw = section_text[rel_start:rel_end]
            left_trim = len(segment_raw) - len(segment_raw.lstrip())
            right_trim = len(segment_raw) - len(segment_raw.rstrip())
            segment = segment_raw.strip()
            if segment:
                abs_start = section_start + rel_start + left_trim
                abs_end = section_start + rel_end - right_trim
                chunk = _make_chunk(
                    document_version_id=document_version_id,
                    source_kind=source_kind,
                    start_offset=abs_start,
                    end_offset=abs_end,
                    text=segment,
                    citation_map=citation_map,
                )
                chunks.append(chunk)
            if rel_end == len(section_text):
                break
            rel_start = max(rel_end - overlap_chars, rel_start + 1)

    for idx, chunk in enumerate(chunks):
        chunk["chunk_index"] = idx

    return chunks


def build_trigrams(value: str) -> set[str]:
    norm = normalize_for_search(value)
    if len(norm) < 3:
        return {norm} if norm else set()
    return {norm[i : i + 3] for i in range(0, len(norm) - 2)}


def normalize_for_search(value: str) -> str:
    lowered = value.casefold()
    return WHITESPACE_RE.sub(" ", lowered).strip()


def _detect_section_spans(text: str) -> list[tuple[int, int]]:
    line_starts = [0]
    for idx, char in enumerate(text):
        if char == "\n" and idx + 1 < len(text):
            line_starts.append(idx + 1)

    heading_starts: list[int] = []
    for start in line_starts:
        line = text[start : text.find("\n", start) if "\n" in text[start:] else len(text)].strip()
        if _is_heading(line):
            heading_starts.append(start)

    if len(heading_starts) < 2:
        return [(0, len(text))]

    spans: list[tuple[int, int]] = []
    for idx, start in enumerate(heading_starts):
        end = heading_starts[idx + 1] if idx + 1 < len(heading_starts) else len(text)
        spans.append((start, end))
    return spans


def _is_heading(line: str) -> bool:
    compact = WHITESPACE_RE.sub(" ", line).strip()
    if not compact:
        return False
    if len(compact) > 80:
        return False
    if compact.endswith("."):
        return False
    if compact.endswith(":"):
        return True
    if HEADING_PREFIX_RE.match(compact):
        return True
    if ORDERED_PREFIX_RE.match(compact):
        return True
    return False


def _make_chunk(
    *,
    document_version_id: int,
    source_kind: str,
    start_offset: int,
    end_offset: int,
    text: str,
    citation_map: list[dict[str, int]],
) -> dict:
    text_norm = normalize_for_search(text)
    pages = resolve_pages_for_span(citation_map, start_offset, end_offset)
    start_page = pages[0] if pages else None
    end_page = pages[-1] if pages else None
    citation_label = None
    if start_page is not None and end_page is not None:
        citation_label = f"p.{start_page}" if start_page == end_page else f"pp.{start_page}-{end_page}"

    digest_input = f"{document_version_id}:{start_offset}:{end_offset}:{text_norm}"
    chunk_id = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:40]

    trigrams = build_trigrams(text_norm)
    return {
        "chunk_id": chunk_id,
        "source_kind": source_kind,
        "chunk_text": text,
        "chunk_text_norm": text_norm,
        "start_offset": start_offset,
        "end_offset": end_offset,
        "start_page": start_page,
        "end_page": end_page,
        "citation_label": citation_label,
        "trigrams": sorted(trigrams),
        "trigram_count": len(trigrams),
    }
