#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


TABLE_HEADER_TERMS = (
    "נושא ההחלטה",
    "תוכן ההחלטה",
    "תוכן ה החלטה",
    "מכותבים",
    "תחילת תוקף",
    "נושא המשימה",
    "פעילות",
    "אחראי",
    "יעד",
    "subject",
    "decision content",
    "addressees",
    "effective date",
)
METADATA_TERMS = (
    "השתתפו",
    "נעדרו",
    "מיקום הישיבה",
    "נוהל ע\"י",
    "הוקלד",
    "הודפס",
    "תועד",
    "attendees",
    "absent",
    "location",
)
VOTE_RESULT_TERMS = (
    "הצביעו",
    "הצביעה",
    "בעד",
    "נגד",
    "נמנע",
    "נמנעו",
    "לא הצביעה",
    "לא השתתפו בהצבעה",
    "מאשרים פה אחד",
    "מאשרים ברוב קולות",
    "approved unanimously",
    "approved by majority",
    "for",
    "against",
    "abstain",
)
STRUCTURAL_SUBJECT_TERMS = (
    "סעיף",
    "שאילתה",
    "הצעה לסדר",
    "נושא לדיון",
    "פרוטוקול",
    "החלטות",
    "הסכם",
    "אישור",
    "מינוי",
    "item",
    "section",
    "agenda",
    "decision",
    "resolution",
)
TASK_TERMS = (
    "משימות",
    "נושא המשימה",
    "פעילות",
    "אחראי",
    "יעד",
    "יש לתאם",
    "לעדכן",
    "משימה",
    "task",
    "owner",
    "due",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 3.1: generic structural normalization of semantic units")
    parser.add_argument("semantic_units_json", help="Step 3 semantic_units.json")
    parser.add_argument("--output-dir", required=True, help="Directory for Step 3.1 outputs")
    args = parser.parse_args()

    input_path = Path(args.semantic_units_json).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = output_dir / "evidence_tables"
    evidence_dir.mkdir(exist_ok=True)

    semantic_payload = json.loads(input_path.read_text(encoding="utf-8"))
    semantic_units = semantic_payload.get("semantic_units") or []
    structure_units = _normalize_structure(semantic_units, semantic_payload=semantic_payload)
    validation = _validate_structure(structure_units)

    output = {
        "step": "step3_1_structure_normalization",
        "input_semantic_units_json": str(input_path),
        "semantic_unit_count": len(semantic_units),
        "structure_unit_count": len(structure_units),
        "structure_units": structure_units,
    }
    structure_path = output_dir / "structure_units.json"
    validation_path = output_dir / "validation_report.json"
    structure_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_evidence(evidence_dir=evidence_dir, structure_units=structure_units)

    print(
        json.dumps(
            {
                "structure_units": str(structure_path),
                "validation_report": str(validation_path),
                "semantic_unit_count": len(semantic_units),
                "structure_unit_count": len(structure_units),
                "split_unit_count": validation["split_unit_count"],
                "linked_continuation_count": validation["linked_continuation_count"],
                "ineligible_count": validation["topic_ineligible_count"],
                "accept_for_next_step": validation["accept_for_next_step"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if validation["accept_for_next_step"] else 2


def _normalize_structure(units: list[dict[str, Any]], *, semantic_payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    current_section_id: str | None = None
    current_anchor_id: str | None = None
    for ordinal, unit in enumerate(units, start=1):
        fragments = _split_semantic_unit(unit)
        for fragment_index, fragment in enumerate(fragments, start=1):
            role = _structural_role(fragment["text"], original_unit=unit, fragment_count=len(fragments))
            section_number = _section_number(fragment["text"])
            starts_section = role in {"section_heading", "outline_item", "body"} and bool(section_number or _starts_new_subject(fragment["text"]))
            structure_unit_id = f"s{ordinal:04d}_{fragment_index:02d}_{_short_hash(fragment['text'])}"
            if starts_section:
                current_section_id = _section_id(section_number=section_number, text=fragment["text"], fallback_id=structure_unit_id)
                current_anchor_id = structure_unit_id
            elif role in {"metadata", "table_header_only", "noise"}:
                pass
            elif current_section_id is None:
                current_section_id = _section_id(section_number=section_number, text=fragment["text"], fallback_id=structure_unit_id)
                current_anchor_id = structure_unit_id if role != "vote_or_result" else None

            continuation_of = None
            if role in {"continuation", "vote_or_result", "task_row"} and current_anchor_id:
                continuation_of = current_anchor_id
            if role in {"body", "section_heading", "outline_item"}:
                current_anchor_id = structure_unit_id

            topic_assignment_eligible = role not in {"metadata", "table_header_only", "noise"}
            out.append(
                {
                    "structure_unit_id": structure_unit_id,
                    "semantic_unit_id": structure_unit_id,
                    "source_semantic_unit_ids": [str(unit.get("semantic_unit_id") or "")],
                    "source_window_id": str(unit.get("source_window_id") or ""),
                    "page": int(unit.get("page") or 0),
                    "source_region_ids": unit.get("source_region_ids") or [],
                    "source_block_ids": unit.get("source_block_ids") or [],
                    "raw_text": fragment["text"],
                    "summary_he": _summary(fragment["text"]),
                    "explicit_actions": _string_list(unit.get("explicit_actions")),
                    "structural_role": role,
                    "section_id": current_section_id,
                    "section_number": section_number,
                    "continuation_of_unit_id": continuation_of,
                    "topic_assignment_eligible": topic_assignment_eligible,
                    "fragment_index": fragment_index,
                    "fragment_count": len(fragments),
                    "structure_evidence": fragment["evidence"],
                }
            )
    out.extend(_visual_bounded_headline_units(semantic_payload or {}, start_index=len(out) + 1))
    return out


def _split_semantic_unit(unit: dict[str, Any]) -> list[dict[str, Any]]:
    text = _clean_text(str(unit.get("raw_text") or unit.get("summary_he") or ""))
    if not text:
        return [{"text": "", "evidence": {"split_reason": "empty"}}]
    stripped_headers = _remove_table_header_runs(text)
    text = stripped_headers if stripped_headers else text
    starts = _structural_starts(text)
    if len(starts) <= 1:
        return [{"text": text, "evidence": {"split_reason": "single_fragment"}}]
    fragments = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        fragment = _clean_text(text[start:end])
        if len(fragment) < 12:
            continue
        fragments.append({"text": fragment, "evidence": {"split_reason": "multiple_structural_starts", "start_char": start, "end_char": end}})
    return fragments or [{"text": text, "evidence": {"split_reason": "split_fallback"}}]


def _structural_starts(text: str) -> list[int]:
    patterns = [
        r"(?<!\d)(סעיף\s*\d+\s*[:.-]?)",
        r"(?<!\d)(\d+(?:\.\d+)?\s*[.)]?\s*(?:שאילתה|הצעה\s+לסדר|נושא\s+לדיון|פרוטוקול|הסכם|אישור|מינוי))",
        r"(?<!\d)(\d+\s*[.)]\s*[\"'׳״]?[^\d]{8,})",
        r"\b(Item|Section|Agenda|Decision|Resolution)\s+\d+\b",
    ]
    starts = {0}
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            starts.add(match.start())
    return sorted(starts)


def _visual_bounded_headline_units(semantic_payload: dict[str, Any], *, start_index: int) -> list[dict[str, Any]]:
    visual_path = Path(str(semantic_payload.get("input_visual_layout_report") or ""))
    if not visual_path.exists():
        return []
    try:
        visual_payload = json.loads(visual_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page_result in visual_payload.get("results") or []:
        page = int(page_result.get("page") or 0)
        for visual_index, region in enumerate(page_result.get("visual_regions") or [], start=1):
            for candidate_index, candidate in enumerate(_visual_headline_candidates(region), start=1):
                norm = _norm(candidate["header_text"])
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                source_region_id = str(region.get("region_id") or "")
                structure_unit_id = f"vh{page:03d}_{visual_index:02d}_{candidate_index:02d}_{_short_hash(candidate['header_text'])}"
                section_number = _section_number(candidate["header_text"])
                section_id = _section_id(section_number=section_number, text=candidate["header_text"], fallback_id=structure_unit_id)
                out.append(
                    {
                        "structure_unit_id": structure_unit_id,
                        "semantic_unit_id": structure_unit_id,
                        "source_semantic_unit_ids": [],
                        "source_window_id": f"visual_page_{page}",
                        "page": page,
                        "source_region_ids": [source_region_id] if source_region_id else [],
                        "source_block_ids": [str(value) for value in region.get("block_ids") or [] if str(value).strip()],
                        "raw_text": candidate["raw_text"],
                        "header_text": candidate["header_text"],
                        "summary_he": _summary(candidate["raw_text"]),
                        "explicit_actions": [],
                        "structural_role": "outline_item",
                        "section_id": section_id,
                        "section_number": section_number,
                        "continuation_of_unit_id": None,
                        "topic_assignment_eligible": True,
                        "fragment_index": 1,
                        "fragment_count": 1,
                        "structure_evidence": {
                            "split_reason": "visual_bounded_headline_candidate",
                            "visual_role": region.get("visual_role"),
                            "visual_confidence": region.get("confidence"),
                            "candidate_reason": candidate["reason"],
                            "visual_order": start_index + len(out),
                        },
                    }
                )
    return out


def _visual_headline_candidate(region: dict[str, Any]) -> dict[str, str] | None:
    candidates = _visual_headline_candidates(region)
    return candidates[0] if candidates else None


def _visual_headline_candidates(region: dict[str, Any]) -> list[dict[str, str]]:
    role = str(region.get("visual_role") or "")
    text = _clean_text(str(region.get("text") or ""))
    if role in {"document_header", "document_footer", "logo_or_decoration", "unknown"}:
        return []
    if role not in {"section_heading", "structured_header", "structured_row", "body_text"}:
        return []
    if not _bounded_text_window(text):
        return []
    if _looks_like_page_chrome(text) or _is_metadata(text, original_unit={}) or _is_vote_or_result(text) or _is_table_header_only(text):
        return []
    out: list[dict[str, str]] = []
    for header_text, reason, raw_text in _extract_visual_headline_texts(text):
        if not _valid_visual_headline_text(header_text, visual_role=role, source_text=text):
            continue
        if role == "body_text" and reason not in {"attribution_dash_subject", "strong_marker_subject"}:
            continue
        out.append({"raw_text": raw_text[:700], "header_text": header_text, "reason": reason})
    return out


def _bounded_text_window(text: str) -> bool:
    compact = _clean_text(text)
    if len(compact) < 6 or len(compact) > 420:
        return False
    return len([token for token in re.split(r"\W+", compact) if token]) <= 70


def _extract_visual_headline_texts(text: str) -> list[tuple[str, str, str]]:
    compact = _clean_text(text)
    dash_subject = _subject_after_attribution_dash(compact)
    if dash_subject:
        return [(dash_subject, "attribution_dash_subject", compact)]
    marker_candidates = _explicit_marker_headline_segments(compact)
    if marker_candidates:
        return marker_candidates
    if _looks_like_short_title_row(compact):
        return [(_clean_visual_headline_text(compact), "short_visual_title", compact)]
    return []


def _explicit_marker_headline_segments(text: str) -> list[tuple[str, str, str]]:
    marker_re = re.compile(r"שאילת[אה]?\s*[:\-–]|שאילת[אה]?\s+של\b|הצעה\s+לסדר(?:\s+יום)?(?:\s+בנושא)?|נושא\s+לדיון")
    matches = list(marker_re.finditer(text))
    out: list[tuple[str, str, str]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = _clean_text(text[start:end])
        if not _marker_segment_has_topic(segment):
            continue
        candidate = _clean_visual_headline_text(segment)
        if candidate:
            out.append((candidate, "strong_marker_subject", segment))
    return out


def _marker_segment_has_topic(segment: str) -> bool:
    normalized = _norm(segment)
    if "שלנו" in normalized[:40]:
        return False
    if re.search(r"שאילת[אה]?\s+של\b", segment) and not any(term in normalized for term in ("בנושא", "הנדון")) and "–" not in segment and "-" not in segment:
        return False
    return len([token for token in re.split(r"\W+", normalized) if len(token) >= 2]) >= 3


def _subject_after_attribution_dash(text: str) -> str:
    parts = [part.strip() for part in re.split(r"\s+[\-–]\s+", text) if part.strip()]
    if len(parts) < 2:
        return ""
    for split_index in range(len(parts) - 1):
        attribution = " – ".join(parts[: split_index + 1])
        if not _looks_like_attribution_side(attribution):
            continue
        subject = _clean_visual_headline_text(" – ".join(parts[split_index + 1 :]))
        if _looks_like_speaker_role_fragment(subject):
            continue
        if _valid_titleish_tokens(subject):
            return subject
    return ""


def _looks_like_attribution_side(text: str) -> bool:
    normalized = _norm(text)
    cues = ["חבר מועצה", "חברת מועצה", "משנה לרהע", "סגן רהע", "סגנית רהע", "ראש העיר", "ראש העירייה", "מגיש", "מגישת"]
    return any(cue in normalized for cue in cues)


def _looks_like_speaker_role_fragment(text: str) -> bool:
    normalized = _norm(text).strip(" :.-–")
    if not normalized:
        return True
    role_prefixes = ("היור", "יור", "ראש העיר", "ראש העירייה", "מנכל", "חבר מועצה", "חברת מועצה", "סגן", "סגנית")
    return any(normalized.startswith(prefix) for prefix in role_prefixes)


def _clean_visual_headline_text(text: str) -> str:
    value = _clean_text(text)
    value = re.split(r"\s+:(?:מר|גב'|גברת|ד\"ר|עו\"ד|ראש|יו\"ר)\b", value, maxsplit=1)[0]
    value = re.sub(r"^[:.\s]+", "", value)
    value = re.sub(r"\s+\.?\d{1,3}\s*$", "", value)
    value = re.sub(r"\s+\(?עמ\.?\s*\d{1,3}\)?\s*$", "", value)
    value = value.strip(" '\"׳״()[]-–:.")
    return _clean_text(value)[:220]


def _valid_visual_headline_text(text: str, *, visual_role: str, source_text: str) -> bool:
    if not _valid_titleish_tokens(text):
        return False
    if _looks_like_page_chrome(text):
        return False
    normalized = _norm(text)
    if re.search(r":(?:מר|גב'|גברת|ד\"ר|עו\"ד|ראש|יו\"ר)\b", text):
        return False
    if "פרוטוקול ועדת" in normalized or "פרוטוקולי ועדת" in normalized:
        return False
    if re.search(r"\bהחלטה\s*:", source_text):
        return False
    speaker_turns = len(re.findall(r":(?:מר|גב'|גברת|ד\"ר|עו\"ד|ראש|יו\"ר)\b", source_text))
    if speaker_turns >= 2 and "–" not in source_text and "-" not in source_text:
        return False
    if visual_role in {"section_heading", "structured_header", "structured_row"}:
        return True
    return bool(re.search(r"[\-–]|שאילת[אה]?|הצעה\s+לסדר|נושא\s+לדיון", source_text))


def _valid_titleish_tokens(text: str) -> bool:
    compact = _clean_text(text)
    if len(compact) < 4 or len(compact) > 220:
        return False
    if re.fullmatch(r"[\d\W_]+", compact):
        return False
    tokens = [token for token in re.split(r"\W+", _norm(compact)) if len(token) >= 2]
    return len(tokens) >= 2


def _looks_like_short_title_row(text: str) -> bool:
    compact = _clean_text(text)
    if len(compact) > 160:
        return False
    if re.search(r":(?:מר|גב'|גברת|ד\"ר|עו\"ד|ראש|יו\"ר)\b", compact):
        return False
    if re.search(r"\b(?:סעיף|Item|Section|Agenda|Decision|Resolution)\b", compact, flags=re.IGNORECASE):
        return True
    normalized = _norm(compact)
    return any(_norm(term) in normalized for term in STRUCTURAL_SUBJECT_TERMS) and len([token for token in re.split(r"\W+", normalized) if token]) <= 18


def _looks_like_page_chrome(text: str) -> bool:
    compact = _clean_text(text)
    normalized = _norm(compact)
    if re.search(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b", compact) and any(term in normalized for term in ("פרוטוקול ישיבות", "ישיבה מן המניין", "מתאריך")):
        return True
    if "פרוטוקול ישיבות" in normalized and any(term in normalized for term in ("מנכ", "עיריית", "עירייה", "ישיבה")):
        return True
    if any(term in normalized for term in ("לשכת מנכל", "טלפון", "פקס")) and len(compact) <= 180:
        return True
    return False


def _structural_role(text: str, *, original_unit: dict[str, Any], fragment_count: int) -> str:
    normalized = _norm(text)
    if not normalized:
        return "noise"
    if _is_metadata(text, original_unit=original_unit):
        return "metadata"
    if _is_table_header_only(text):
        return "table_header_only"
    if _is_task_row(text):
        return "task_row"
    if _is_vote_or_result(text):
        return "vote_or_result"
    if _starts_new_subject(text):
        return "outline_item" if fragment_count > 1 or _looks_like_outline(text) else "section_heading"
    if len(normalized) < 120 and not _starts_new_subject(text):
        return "continuation"
    return "body"


def _is_metadata(text: str, *, original_unit: dict[str, Any]) -> bool:
    normalized = _norm(text)
    if _has_structural_subject(text):
        return False
    if any(_norm(term) in normalized for term in METADATA_TERMS):
        return True
    kind = str(original_unit.get("unit_kind") or "")
    return kind == "metadata" and not _has_structural_subject(text)


def _is_table_header_only(text: str) -> bool:
    normalized = _norm(text)
    hits = sum(1 for term in TABLE_HEADER_TERMS if _norm(term) in normalized)
    substantive_tokens = [token for token in re.split(r"\W+", normalized) if len(token) >= 3]
    return hits >= 2 and len(substantive_tokens) <= hits * 5 + 8


def _is_task_row(text: str) -> bool:
    normalized = _norm(text)
    return any(_norm(term) in normalized for term in TASK_TERMS) and not _starts_new_subject(text)


def _is_vote_or_result(text: str) -> bool:
    normalized = _norm(text)
    if not any(_norm(term) in normalized for term in VOTE_RESULT_TERMS):
        return False
    if _starts_new_subject(text):
        return False
    return True


def _starts_new_subject(text: str) -> bool:
    normalized = _norm(text)
    if re.search(r"(?:^|\s)סעיף\s*\d+", text):
        return True
    if re.search(r"(?:^|\s)\d+(?:\.\d+)?\s*[.)]?\s*(?:שאילתה|הצעה\s+לסדר|נושא\s+לדיון|פרוטוקול|הסכם|אישור|מינוי)", text):
        return True
    return any(_norm(term) in normalized for term in ("נושא לדיון", "הצעה לסדר", "שאילתה בנושא", "Item", "Section", "Decision", "Resolution"))


def _has_structural_subject(text: str) -> bool:
    normalized = _norm(text)
    return any(_norm(term) in normalized for term in STRUCTURAL_SUBJECT_TERMS)


def _looks_like_outline(text: str) -> bool:
    return len(_structural_starts(text)) > 1 or bool(re.search(r"(?:^|\s)\d+(?:\.\d+)?\s*[.)]", text))


def _section_number(text: str) -> str | None:
    for pattern in (r"סעיף\s*(\d+(?:\.\d+)?)", r"(?:^|\s)(\d+(?:\.\d+)?)\s*[.)]?\s*(?:שאילתה|הצעה\s+לסדר|נושא\s+לדיון|פרוטוקול|הסכם|אישור|מינוי)"):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _section_id(*, section_number: str | None, text: str, fallback_id: str) -> str:
    basis = section_number or _summary(text)[:80] or fallback_id
    return "section_" + _short_hash(basis)


def _remove_table_header_runs(text: str) -> str:
    pattern = r"(?:נושא\s+ההחלטה\s+תוכן\s+ה\s*החלטה\s+מכותבים\s+תחילת\s+תוקף|נושא\s+המשימה\s+פעילות\s+אחראי\s+יעד)"
    return _clean_text(re.sub(pattern, " ", text))


def _validate_structure(structure_units: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [str(unit.get("structure_unit_id") or "") for unit in structure_units]
    id_order = {unit_id: index for index, unit_id in enumerate(ids)}
    blocked = []
    for index, unit in enumerate(structure_units):
        issues = []
        role = str(unit.get("structural_role") or "")
        continuation = str(unit.get("continuation_of_unit_id") or "")
        if continuation and id_order.get(continuation, 10**9) >= index:
            issues.append("continuation_not_backward")
        if role in {"metadata", "table_header_only", "noise"} and unit.get("topic_assignment_eligible"):
            issues.append("ineligible_role_marked_topic_eligible")
        if not str(unit.get("raw_text") or "").strip():
            issues.append("empty_raw_text")
        if issues:
            blocked.append({"structure_unit_id": unit.get("structure_unit_id"), "issues": issues})
    return {
        "step": "step3_1_structure_validation",
        "structure_unit_count": len(structure_units),
        "split_unit_count": sum(1 for unit in structure_units if int(unit.get("fragment_count") or 1) > 1),
        "linked_continuation_count": sum(1 for unit in structure_units if unit.get("continuation_of_unit_id")),
        "topic_ineligible_count": sum(1 for unit in structure_units if not unit.get("topic_assignment_eligible")),
        "role_counts": _counts(str(unit.get("structural_role") or "") for unit in structure_units),
        "blocked_units": blocked,
        "accept_for_next_step": not blocked,
    }


def _write_evidence(*, evidence_dir: Path, structure_units: list[dict[str, Any]]) -> None:
    by_page: dict[int, list[dict[str, Any]]] = {}
    for unit in structure_units:
        by_page.setdefault(int(unit.get("page") or 0), []).append(unit)
    for page, page_units in sorted(by_page.items()):
        lines = [f"# Step 3.1 Structure Page {page}", "", "| Unit | Role | Section | Continuation | Eligible | Text |", "|---|---|---|---|---|---|"]
        for unit in page_units:
            lines.append(
                "| {unit} | {role} | {section} | {cont} | {eligible} | {text} |".format(
                    unit=_md(unit.get("structure_unit_id")),
                    role=_md(unit.get("structural_role")),
                    section=_md(unit.get("section_id")),
                    cont=_md(unit.get("continuation_of_unit_id")),
                    eligible=_md(unit.get("topic_assignment_eligible")),
                    text=_md(_summary(str(unit.get("raw_text") or ""))[:220]),
                )
            )
        (evidence_dir / f"page_{page:03d}_structure.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _summary(text: str) -> str:
    return _clean_text(text)[:700]


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _norm(text: str) -> str:
    return _clean_text(text).casefold()


def _short_hash(text: str) -> str:
    return hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()[:12]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _counts(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


if __name__ == "__main__":
    raise SystemExit(main())
