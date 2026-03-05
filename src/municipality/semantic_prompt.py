from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from municipality.semantic_contract import SEMANTIC_EXTRACTION_PROMPT_PREFIX, SEMANTIC_EXTRACTION_RESPONSE_SCHEMA


HEADING_MARKERS = (
    "סיכום",
    "החלטות",
    "סעיף",
    "נושא",
    "פרק",
    "ועדה",
    "ועדת",
)

CATEGORY_HINT_TERMS: dict[str, tuple[str, ...]] = {
    "decision": ("הוחלט", "החלטה", "מאושר", "אושר", "מאשרים", "מחליטים", "בעד", "נגד", "נמנע", "הצבעה"),
    "plan_program": (
        "תכנית",
        "תוכנית",
        "תכנית אב",
        "תוכנית אב",
        'תב"ע',
        "מתאר",
        "מתווה",
        "תכנון",
        "פרויקט",
    ),
    "discussion": ("דיון", "נדון", "הוצג", "הוצגה", "נשמעו", "התייחסות", "הערות", "עמדה"),
    "policy": ("מדיניות", "עקרונות", "קווים מנחים", "נוהל", "קריטריונים", "אסטרטגיה"),
    "budget_finance": ("תקציב", "הקצאה", "מימון", "עלות", "אומדן", 'תב"ר', "סעיף תקציבי", "מקור תקציבי"),
    "implementation": ("ביצוע", "יישום", "לוח זמנים", "אבני דרך", "התקדמות", "סטטוס", "מעקב", "השלמה"),
    "procurement_legal": (
        "מכרז",
        "התקשרות",
        "הסכם",
        "פטור ממכרז",
        "יועץ משפטי",
        "חוות דעת",
        "ועדת מכרזים",
    ),
    "public_feedback": ("שיתוף ציבור", "פניות ציבור", "התנגדויות", "שימוע", "תושבים"),
}

CATEGORY_HINT_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    category: tuple(re.compile(re.escape(term)) for term in terms)
    for category, terms in CATEGORY_HINT_TERMS.items()
}

ENTITY_HINT_RE = re.compile(r"([\u0590-\u05FF]{2,}(?:\s+[\u0590-\u05FF]{2,}){1,3})")
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class PacketSpan:
    text: str
    start_offset: int
    end_offset: int
    page: int | None = None


@dataclass(slots=True)
class HintPacketSpan(PacketSpan):
    category_hints: list[str] = field(default_factory=list)
    hint_terms: list[str] = field(default_factory=list)
    regex_boost: float = 0.0


@dataclass(slots=True)
class SemanticEvidencePacket:
    extracted_text_length: int
    headings: list[PacketSpan]
    evidence_lines: list[HintPacketSpan]
    entity_hints: list[str]
    citation_map: list[dict[str, int]]


@dataclass(slots=True)
class _LineCandidate:
    index: int
    text: str
    start_offset: int
    end_offset: int
    page: int | None
    category_hints: list[str]
    hint_terms: list[str]


def build_semantic_evidence_packet(
    *,
    extracted_text: str,
    citation_map: list[dict[str, int]],
    max_chars: int = 12000,
    max_entity_hints: int = 80,
) -> SemanticEvidencePacket:
    heading_candidates: list[PacketSpan] = []
    hint_candidates: list[_LineCandidate] = []
    coverage_candidates_by_page: dict[int, _LineCandidate] = {}

    for index, (line_text, start, end) in enumerate(_iter_lines_with_offsets(extracted_text)):
        compact = line_text.strip()
        if not compact:
            continue

        page = _resolve_primary_page(citation_map, start, end)

        if _looks_like_heading(compact):
            heading_candidates.append(PacketSpan(text=compact, start_offset=start, end_offset=end, page=page))

        category_hints, hint_terms = _category_hints_for_line(compact)
        if category_hints:
            hint_candidates.append(
                _LineCandidate(
                    index=index,
                    text=compact,
                    start_offset=start,
                    end_offset=end,
                    page=page,
                    category_hints=category_hints,
                    hint_terms=hint_terms,
                )
            )

        if page is not None and page not in coverage_candidates_by_page:
            coverage_candidates_by_page[page] = _LineCandidate(
                index=index,
                text=compact,
                start_offset=start,
                end_offset=end,
                page=page,
                category_hints=[],
                hint_terms=[],
            )

    heading_budget = max(500, int(max_chars * 0.2))
    headings = _select_headings(heading_candidates, max_items=80, max_chars=heading_budget)
    used_heading_chars = sum(len(span.text) for span in headings)
    evidence_budget = max(0, max_chars - used_heading_chars)
    evidence_lines = _select_evidence_lines(
        hint_candidates,
        list(coverage_candidates_by_page.values()),
        max_items=180,
        max_chars=evidence_budget,
    )

    hints = _extract_entity_hints(extracted_text, max_items=max_entity_hints)

    return SemanticEvidencePacket(
        extracted_text_length=len(extracted_text),
        headings=headings,
        evidence_lines=evidence_lines,
        entity_hints=hints,
        citation_map=citation_map,
    )


def build_semantic_model_request(
    *,
    document_version_id: int,
    source_kind: str,
    evidence_packet: SemanticEvidencePacket,
) -> dict[str, Any]:
    category_hint_lexicon = {category: list(terms) for category, terms in CATEGORY_HINT_TERMS.items()}
    categories = list(CATEGORY_HINT_TERMS.keys())
    category_csv = ", ".join(categories)

    system_instruction = (
        f"{SEMANTIC_EXTRACTION_PROMPT_PREFIX}\n"
        "You are extracting grounded semantic structure from Hebrew municipal evidence.\n"
        "Perform BOTH tasks in this same response:\n"
        "Task A: detect evidence spans and classify each span into exactly one category.\n"
        "Task B: extract semantic topic/entity nodes and link each node to one or more span_ids from Task A.\n"
        f"Allowed categories: {category_csv}.\n"
        "Treat regex category hints as soft priors that may increase confidence; do not use them as hard filters.\n"
        "Return strict JSON only. If uncertain, prefer rejects/refusal over speculative output."
    )

    return {
        "instruction_prefix": SEMANTIC_EXTRACTION_PROMPT_PREFIX,
        "system_instruction": system_instruction,
        "document": {
            "document_version_id": document_version_id,
            "source_kind": source_kind,
            "extracted_text_length": evidence_packet.extracted_text_length,
        },
        "input_packet": {
            "headings": [asdict(span) for span in evidence_packet.headings],
            "evidence_lines": [asdict(span) for span in evidence_packet.evidence_lines],
            "entity_hints": evidence_packet.entity_hints,
            "citation_map": evidence_packet.citation_map,
            "category_hint_lexicon": category_hint_lexicon,
        },
        "selection_strategy": {
            "evidence_line_selection": "lightweight_compaction_with_soft_category_priors",
            "category_hint_policy": "soft_boost_only",
            "regex_boost_cap": 0.12,
        },
        "response_schema": SEMANTIC_EXTRACTION_RESPONSE_SCHEMA,
        "rules": [
            "Use only evidence in input_packet fields",
            "All mention offsets must satisfy 0 <= start_offset < end_offset <= extracted_text_length",
            "All evidence_spans must include span_id, category, start_offset, end_offset, text",
            "Every accepted node must have at least one mention",
            "Every accepted node should include evidence_span_ids from evidence_spans",
            "Do not rely only on explicit decision wording; infer category from context",
            "When uncertain, emit reject records or refusal text instead of speculative nodes",
        ],
    }


def prompt_hash_for_payload(payload: dict[str, Any]) -> str:
    canonical_json = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _iter_lines_with_offsets(text_value: str) -> list[tuple[str, int, int]]:
    lines: list[tuple[str, int, int]] = []
    cursor = 0
    for raw_line in text_value.split("\n"):
        start = cursor
        end = start + len(raw_line)
        lines.append((raw_line, start, end))
        cursor = end + 1
    return lines


def _looks_like_heading(value: str) -> bool:
    compact = WHITESPACE_RE.sub(" ", value).strip()
    if not compact:
        return False
    if len(compact) > 110:
        return False
    if compact.endswith(":"):
        return True
    return any(marker in compact for marker in HEADING_MARKERS)


def _category_hints_for_line(value: str) -> tuple[list[str], list[str]]:
    categories: list[str] = []
    terms: list[str] = []
    for category, patterns in CATEGORY_HINT_PATTERNS.items():
        category_terms: list[str] = []
        for idx, pattern in enumerate(patterns):
            if pattern.search(value):
                category_terms.append(CATEGORY_HINT_TERMS[category][idx])
        if category_terms:
            categories.append(category)
            terms.extend(category_terms)
    return categories, _dedupe_preserve_order(terms)


def _select_headings(candidates: list[PacketSpan], *, max_items: int, max_chars: int) -> list[PacketSpan]:
    selected: list[PacketSpan] = []
    used_chars = 0
    for span in sorted(candidates, key=lambda item: item.start_offset):
        if len(selected) >= max_items:
            break
        span_chars = len(span.text)
        if used_chars + span_chars > max_chars:
            continue
        selected.append(span)
        used_chars += span_chars
    return selected


def _select_evidence_lines(
    hint_candidates: list[_LineCandidate],
    coverage_candidates: list[_LineCandidate],
    *,
    max_items: int,
    max_chars: int,
) -> list[HintPacketSpan]:
    if max_chars <= 0:
        return []

    selected_by_index: dict[int, _LineCandidate] = {}

    ranked_hints = sorted(hint_candidates, key=lambda item: (-len(item.hint_terms), item.start_offset))
    for candidate in ranked_hints:
        if len(selected_by_index) >= max_items:
            break
        selected_by_index.setdefault(candidate.index, candidate)

    for candidate in sorted(coverage_candidates, key=lambda item: item.start_offset):
        if len(selected_by_index) >= max_items:
            break
        selected_by_index.setdefault(candidate.index, candidate)

    if not selected_by_index:
        for candidate in sorted(coverage_candidates, key=lambda item: item.start_offset)[: min(40, max_items)]:
            selected_by_index[candidate.index] = candidate

    selected: list[HintPacketSpan] = []
    used_chars = 0
    for candidate in sorted(selected_by_index.values(), key=lambda item: item.start_offset):
        line_chars = len(candidate.text)
        if used_chars + line_chars > max_chars:
            continue
        used_chars += line_chars
        hint_terms = _dedupe_preserve_order(candidate.hint_terms)
        selected.append(
            HintPacketSpan(
                text=candidate.text,
                start_offset=candidate.start_offset,
                end_offset=candidate.end_offset,
                page=candidate.page,
                category_hints=_dedupe_preserve_order(candidate.category_hints),
                hint_terms=hint_terms,
                regex_boost=_regex_boost(len(hint_terms)),
            )
        )
        if len(selected) >= max_items:
            break

    return selected


def _regex_boost(hit_count: int) -> float:
    if hit_count <= 0:
        return 0.0
    return round(min(0.12, hit_count * 0.03), 3)


def _resolve_primary_page(citation_map: list[dict[str, int]], start: int, end: int) -> int | None:
    pages: list[int] = []
    for item in citation_map:
        page_start = item["start"]
        page_end = item["end"]
        if page_end <= start:
            continue
        if page_start >= end:
            continue
        pages.append(item["page"])
    return min(pages) if pages else None


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _extract_entity_hints(text_value: str, *, max_items: int) -> list[str]:
    scores: dict[str, int] = {}
    for match in ENTITY_HINT_RE.finditer(text_value):
        phrase = WHITESPACE_RE.sub(" ", match.group(1)).strip()
        if not phrase:
            continue
        if len(phrase) < 5 or len(phrase) > 70:
            continue
        if phrase.isdigit():
            continue
        scores[phrase] = scores.get(phrase, 0) + 1

    ordered = sorted(scores.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
    return [item[0] for item in ordered[:max_items]]
