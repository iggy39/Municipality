from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from municipality.chunking import build_trigrams, normalize_for_search
from municipality.extraction import resolve_pages_for_span


TITLE_PREFIX_RE = re.compile(r"^(פרוטוקול|ישיבה|ישיבת)")
COMMITTEE_RE = re.compile(r"ועדת\s+([\u0590-\u05FF\s\-]{2,60})")
DATE_RE = re.compile(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})")
ORDERED_RE = re.compile(r"^(\d+(?:[.)-]|(?:\.\d+)+))\s+")
WHITESPACE_RE = re.compile(r"\s+")

HEADING_RULES: tuple[tuple[re.Pattern[str], int, str, float], ...] = (
    (re.compile(r"^(פרוטוקול|ישיבה|ישיבת)"), 1, "document_title", 0.96),
    (re.compile(r"^(ועדה|ועדת)"), 2, "committee", 0.88),
    (re.compile(r"^פרק"), 2, "chapter", 0.86),
    (re.compile(r"^נושא"), 3, "topic", 0.9),
    (re.compile(r"^סעיף"), 4, "section", 0.9),
    (re.compile(r"^(החלטה|החלטות|סיכום(?:\s+והחלטה)?)"), 5, "decision", 0.92),
)

DECISION_MARKERS = (
    "הוחלט",
    "החלטה",
    "החלטות",
    "אושר",
    "אושרה",
    "אושרו",
    "מאשר",
    "מאשרים",
)


@dataclass(slots=True)
class StructuredSection:
    section_id: str
    parent_section_id: str | None
    source_kind: str
    node_type: str
    header_text: str
    header_text_norm: str
    header_level: int
    section_path: list[str]
    start_offset: int
    end_offset: int
    ordinal: int
    confidence: float
    body_lines: list[str] = field(default_factory=list)
    start_page: int | None = None
    end_page: int | None = None

    @property
    def body_text(self) -> str:
        return "\n".join(line for line in self.body_lines if str(line).strip()).strip()


@dataclass(slots=True)
class StructuredDocumentBuildResult:
    sections: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    metadata: dict[str, Any]


def build_structured_document(
    *,
    document_version_id: int,
    document_title: str,
    text: str,
    citation_map: list[dict[str, int]],
    source_kind: str,
) -> StructuredDocumentBuildResult:
    title = str(document_title or "").strip() or _first_non_empty_line(text) or "מסמך"
    sections = _build_sections(
        document_version_id=document_version_id,
        title=title,
        text=text,
        citation_map=citation_map,
        source_kind=source_kind,
    )
    metadata = _extract_document_metadata(title=title, text=text)
    artifacts = _build_artifacts(
        document_version_id=document_version_id,
        source_kind=source_kind,
        title=title,
        metadata=metadata,
        sections=sections,
        citation_map=citation_map,
    )
    return StructuredDocumentBuildResult(
        sections=[_section_row(section=section) for section in sections],
        artifacts=artifacts,
        metadata=metadata,
    )


def _build_sections(
    *,
    document_version_id: int,
    title: str,
    text: str,
    citation_map: list[dict[str, int]],
    source_kind: str,
) -> list[StructuredSection]:
    root = StructuredSection(
        section_id=_section_id(document_version_id=document_version_id, ordinal=0, start_offset=0, header_text=title),
        parent_section_id=None,
        source_kind=source_kind,
        node_type="document_root",
        header_text=title,
        header_text_norm=normalize_for_search(title),
        header_level=0,
        section_path=[title],
        start_offset=0,
        end_offset=len(text),
        ordinal=0,
        confidence=1.0,
    )
    sections: list[StructuredSection] = [root]
    stack: list[StructuredSection] = [root]
    ordinal = 1
    lines = _iter_lines_with_offsets(text)

    for index, line in enumerate(lines):
        compact = WHITESPACE_RE.sub(" ", line.text.strip()).strip()
        if not compact:
            continue
        classification = _classify_heading(compact=compact, line_index=index)
        if classification is None and _is_implicit_heading(compact=compact, line_index=index, lines=lines):
            inferred_level = min(max(stack[-1].header_level + 1, 3), 4)
            classification = (inferred_level, "implicit_subtopic", 0.61)
        if classification is None:
            stack[-1].body_lines.append(compact)
            continue

        level, node_type, confidence = classification
        while stack and stack[-1].header_level >= level:
            stack[-1].end_offset = line.start_offset
            stack.pop()
        parent = stack[-1] if stack else root
        path = [*parent.section_path, compact]
        section = StructuredSection(
            section_id=_section_id(
                document_version_id=document_version_id,
                ordinal=ordinal,
                start_offset=line.start_offset,
                header_text=compact,
            ),
            parent_section_id=parent.section_id,
            source_kind=source_kind,
            node_type=node_type,
            header_text=compact,
            header_text_norm=normalize_for_search(compact),
            header_level=level,
            section_path=path,
            start_offset=line.start_offset,
            end_offset=len(text),
            ordinal=ordinal,
            confidence=confidence,
        )
        sections.append(section)
        stack.append(section)
        ordinal += 1

    for section in sections:
        pages = resolve_pages_for_span(citation_map, section.start_offset, section.end_offset)
        section.start_page = pages[0] if pages else None
        section.end_page = pages[-1] if pages else None
    return sections


def _build_artifacts(
    *,
    document_version_id: int,
    source_kind: str,
    title: str,
    metadata: dict[str, Any],
    sections: list[StructuredSection],
    citation_map: list[dict[str, int]],
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    doc_pages = resolve_pages_for_span(citation_map, 0, max((section.end_offset for section in sections), default=0))
    doc_start_page = doc_pages[0] if doc_pages else None
    doc_end_page = doc_pages[-1] if doc_pages else None
    artifacts.append(
        _artifact_row(
            artifact_id=_artifact_id(document_version_id=document_version_id, ordinal=0, kind="document_profile", section_id=None),
            source_kind=source_kind,
            artifact_kind="document_profile",
            ordinal=0,
            title_he=title,
            committee_name=metadata.get("committee_name"),
            meeting_date=metadata.get("meeting_date"),
            header_path=[title],
            body_text="\n".join(section.header_text for section in sections[1:12]),
            retrieval_text=_build_retrieval_text(
                title=title,
                committee_name=metadata.get("committee_name"),
                meeting_date=metadata.get("meeting_date"),
                header_path=[title],
                body_text="\n".join(section.header_text for section in sections[1:12]),
            ),
            start_offset=0,
            end_offset=max((section.end_offset for section in sections), default=0),
            start_page=doc_start_page,
            end_page=doc_end_page,
            citation_label=_citation_label(doc_start_page, doc_end_page),
            metadata={"top_heading_count": max(len(sections) - 1, 0)},
            section_id=None,
        )
    )

    artifact_ordinal = 1
    for section in sections[1:]:
        header_path = list(section.section_path)
        artifacts.append(
            _artifact_row(
                artifact_id=_artifact_id(
                    document_version_id=document_version_id,
                    ordinal=artifact_ordinal,
                    kind="header_anchor",
                    section_id=section.section_id,
                ),
                source_kind=source_kind,
                artifact_kind="header_anchor",
                ordinal=artifact_ordinal,
                title_he=title,
                committee_name=metadata.get("committee_name"),
                meeting_date=metadata.get("meeting_date"),
                header_path=header_path,
                body_text=section.header_text,
                retrieval_text=_build_retrieval_text(
                    title=title,
                    committee_name=metadata.get("committee_name"),
                    meeting_date=metadata.get("meeting_date"),
                    header_path=header_path,
                    body_text=section.header_text,
                ),
                start_offset=section.start_offset,
                end_offset=section.end_offset,
                start_page=section.start_page,
                end_page=section.end_page,
                citation_label=_citation_label(section.start_page, section.end_page),
                metadata={"node_type": section.node_type, "header_level": section.header_level},
                section_id=section.section_id,
            )
        )
        artifact_ordinal += 1

        body_text = section.body_text
        if not body_text:
            continue
        artifact_kind = "decision_unit" if _looks_like_decision(section=section, body_text=body_text) else "section_unit"
        artifacts.append(
            _artifact_row(
                artifact_id=_artifact_id(
                    document_version_id=document_version_id,
                    ordinal=artifact_ordinal,
                    kind=artifact_kind,
                    section_id=section.section_id,
                ),
                source_kind=source_kind,
                artifact_kind=artifact_kind,
                ordinal=artifact_ordinal,
                title_he=title,
                committee_name=metadata.get("committee_name"),
                meeting_date=metadata.get("meeting_date"),
                header_path=header_path,
                body_text=body_text,
                retrieval_text=_build_retrieval_text(
                    title=title,
                    committee_name=metadata.get("committee_name"),
                    meeting_date=metadata.get("meeting_date"),
                    header_path=header_path,
                    body_text=body_text,
                ),
                start_offset=section.start_offset,
                end_offset=section.end_offset,
                start_page=section.start_page,
                end_page=section.end_page,
                citation_label=_citation_label(section.start_page, section.end_page),
                metadata={"node_type": section.node_type, "header_level": section.header_level},
                section_id=section.section_id,
            )
        )
        artifact_ordinal += 1
    return artifacts


def _classify_heading(*, compact: str, line_index: int) -> tuple[int, str, float] | None:
    if len(compact) > 140 or compact.endswith("."):
        return None
    for pattern, level, node_type, confidence in HEADING_RULES:
        if pattern.match(compact):
            return level, node_type, confidence
    if ORDERED_RE.match(compact) and len(compact.split()) <= 14:
        return 4, "ordered_section", 0.72
    if compact.endswith(":") and len(compact.split()) <= 12:
        return 4, "heading_colon", 0.68
    if line_index == 0 and len(compact.split()) <= 18:
        return 1, "document_title", 0.7
    return None


def _is_implicit_heading(*, compact: str, line_index: int, lines: list[_Line]) -> bool:
    if len(compact) > 120 or compact.endswith(".") or compact.endswith(":"):
        return False
    token_count = len(compact.split())
    if token_count < 2 or token_count > 10:
        return False
    if line_index <= 0 or line_index >= len(lines) - 1:
        return False
    if not re.search(r"[\u0590-\u05FF]", compact):
        return False
    next_compact = WHITESPACE_RE.sub(" ", lines[line_index + 1].text.strip()).strip()
    if not next_compact:
        return False
    next_heading = _classify_heading(compact=next_compact, line_index=line_index + 1)
    return next_heading is not None


def _looks_like_decision(*, section: StructuredSection, body_text: str) -> bool:
    if section.node_type == "decision":
        return True
    normalized = normalize_for_search(body_text)
    return any(marker in normalized for marker in DECISION_MARKERS)


def _build_retrieval_text(
    *,
    title: str,
    committee_name: str | None,
    meeting_date: str | None,
    header_path: list[str],
    body_text: str,
) -> str:
    parts = [f"כותרת מסמך: {title}"]
    if committee_name:
        parts.append(f"ועדה: {committee_name}")
    if meeting_date:
        parts.append(f"תאריך: {meeting_date}")
    if header_path:
        parts.append(f"מסלול כותרות: {' > '.join(header_path)}")
    parts.append("טקסט:")
    parts.append(body_text)
    return "\n".join(part for part in parts if str(part).strip()).strip()


def _artifact_row(
    *,
    artifact_id: str,
    source_kind: str,
    artifact_kind: str,
    ordinal: int,
    title_he: str | None,
    committee_name: str | None,
    meeting_date: str | None,
    header_path: list[str],
    body_text: str,
    retrieval_text: str,
    start_offset: int,
    end_offset: int,
    start_page: int | None,
    end_page: int | None,
    citation_label: str | None,
    metadata: dict[str, Any],
    section_id: str | None,
) -> dict[str, Any]:
    trigrams = build_trigrams(retrieval_text)
    return {
        "artifact_id": artifact_id,
        "source_kind": source_kind,
        "artifact_kind": artifact_kind,
        "ordinal": ordinal,
        "title_he": title_he,
        "committee_name": committee_name,
        "meeting_date": meeting_date,
        "header_path_json": json.dumps(header_path, ensure_ascii=False),
        "body_text": body_text,
        "retrieval_text": retrieval_text,
        "retrieval_text_norm": normalize_for_search(retrieval_text),
        "start_offset": start_offset,
        "end_offset": end_offset,
        "start_page": start_page,
        "end_page": end_page,
        "citation_label": citation_label,
        "trigrams": sorted(trigrams),
        "trigram_count": len(trigrams),
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
        "section_id": section_id,
    }


def _section_row(section: StructuredSection) -> dict[str, Any]:
    return {
        "section_id": section.section_id,
        "parent_section_id": section.parent_section_id,
        "source_kind": section.source_kind,
        "node_type": section.node_type,
        "header_text": section.header_text,
        "header_text_norm": section.header_text_norm,
        "header_level": section.header_level,
        "section_path_json": json.dumps(section.section_path, ensure_ascii=False),
        "body_text": section.body_text,
        "start_offset": section.start_offset,
        "end_offset": section.end_offset,
        "start_page": section.start_page,
        "end_page": section.end_page,
        "ordinal": section.ordinal,
        "confidence": section.confidence,
        "metadata_json": json.dumps({"line_count": len(section.body_lines)}, ensure_ascii=False),
    }


@dataclass(slots=True)
class _Line:
    text: str
    start_offset: int
    end_offset: int


def _iter_lines_with_offsets(text: str) -> list[_Line]:
    lines: list[_Line] = []
    cursor = 0
    for raw in text.split("\n"):
        start_offset = cursor
        end_offset = start_offset + len(raw)
        lines.append(_Line(text=raw, start_offset=start_offset, end_offset=end_offset))
        cursor = end_offset + 1
    return lines


def _extract_document_metadata(*, title: str, text: str) -> dict[str, Any]:
    basis = " ".join(part for part in [title, _first_non_empty_line(text)] if part).strip()
    committee_name = None
    committee_match = COMMITTEE_RE.search(basis)
    if committee_match:
        committee_name = f"ועדת {committee_match.group(1).strip()}"
    meeting_date = None
    date_match = DATE_RE.search(basis)
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year = int(date_match.group(3))
        if year < 100:
            year += 2000
        if 1 <= day <= 31 and 1 <= month <= 12:
            meeting_date = f"{year:04d}-{month:02d}-{day:02d}"
    return {
        "committee_name": committee_name,
        "meeting_date": meeting_date,
    }


def _first_non_empty_line(text: str) -> str:
    for raw in text.split("\n"):
        compact = WHITESPACE_RE.sub(" ", raw).strip()
        if compact:
            return compact
    return ""


def _section_id(*, document_version_id: int, ordinal: int, start_offset: int, header_text: str) -> str:
    payload = f"sec:{document_version_id}:{ordinal}:{start_offset}:{normalize_for_search(header_text)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def _artifact_id(*, document_version_id: int, ordinal: int, kind: str, section_id: str | None) -> str:
    payload = f"art:{document_version_id}:{ordinal}:{kind}:{section_id or 'document'}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def _citation_label(start_page: int | None, end_page: int | None) -> str | None:
    if start_page is None or end_page is None:
        return None
    if start_page == end_page:
        return f"p.{start_page}"
    return f"pp.{start_page}-{end_page}"
