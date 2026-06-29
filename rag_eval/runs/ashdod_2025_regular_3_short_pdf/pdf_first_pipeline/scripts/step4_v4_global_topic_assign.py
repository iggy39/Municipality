#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from municipality.pdf_first_v4_topic_tree import (  # noqa: E402
    BACKEND_VERSION,
    CURATED_V4_CHILD_TOPICS,
    ROOT_BY_ID,
    TOPIC_ASSIGNMENT_BACKEND,
    TOPIC_TREE_VERSION,
    V4_ROOT_TOPICS,
    attachment_contexts_from_retrieval_chunks,
    child_topic_id,
    clean_topic_label,
    compact_topic_metadata_schema,
    derive_child_candidate,
    global_topic_tree_payload,
    infer_root_topic_id,
    referenced_attachment_contexts,
    root_label_for_id,
    resolve_child_topic_assignment,
    validate_child_label,
    secondary_topic_roots,
)
from municipality.pdf_first_v4_topic_policy import (  # noqa: E402
    adjudicate_root_topic,
    clean_protocol_subject_text,
    topic_policy_matches,
    topic_policy_prompt_payload,
)
from municipality.pdf_first_v4_topic_classifier import build_topic_profile_index, find_topic_candidates  # noqa: E402
from municipality.topic_label_quality import canonicalize_topic_label, is_low_quality_topic_label  # noqa: E402

DEFAULT_MODEL = "dicta-il/DictaLM-3.0-24B-Thinking:bf16"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
EXCLUDED_STRUCTURAL_ROLES = {"noise", "table_header_only"}
CACHE_VERSION = "step4_v4_global_topic_assign_v63_google_geo_verification"
DICTA_FULL_MODEL_MODES = {"required", "dicta_authoritative", "dicta_contextual"}
ACTION_DOMINANT_ROOT_IDS = {"root_agreements", "root_budget_finance", "root_travel_approvals", "root_hr_labor", "root_allocations", "root_supports", "root_administration"}
CONTEXTUAL_NON_INDEXABLE_STATUSES = {"duplicate_reference", "evidence_fragment", "procedural_only", "insufficient_context"}
NON_TOPIC_CARRIER_REASONS = {"procedural_packet_carrier", "protocol_cover_metadata"}
NON_INHERITABLE_EVIDENCE_REASONS = NON_TOPIC_CARRIER_REASONS | {"speaker_dialogue_fragment", "transcript_speech_fragment", "procedural_dialogue_fragment"}


def _model_thinking_enabled(model: str) -> bool:
    model_id = str(model or "").casefold()
    if "1.7b" in model_id or "1_7b" in model_id:
        return False
    return "thinking" in model_id and ("24b" in model_id or "122b" in model_id)


def _model_system_prompt(*, model: str, prompt: str) -> str:
    return _model_system_prompt_for_think(prompt=prompt, think=_model_thinking_enabled(model))


def _model_system_prompt_for_think(*, prompt: str, think: bool) -> str:
    text = str(prompt or "")
    if think:
        return re.sub(r"^\s*/no_think\s*\n?", "", text)
    if re.match(r"^\s*/no_think\b", text):
        return text
    return f"/no_think\n{text}"


def _json_schema_from_prompt_schema(schema: Any) -> dict[str, Any] | None:
    if not isinstance(schema, dict):
        return None
    return {"type": "object", "properties": _json_schema_properties(schema), "required": list(schema.keys()), "additionalProperties": True}


def _json_schema_properties(schema: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_schema_value(value) for key, value in schema.items()}


def _json_schema_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"type": "object", "properties": _json_schema_properties(value), "required": list(value.keys()), "additionalProperties": True}
    if isinstance(value, list):
        item_schema = _json_schema_value(value[0]) if value else {"type": "string"}
        return {"type": "array", "items": item_schema}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)):
        return {"type": "number"}
    if isinstance(value, str):
        parts = value.split("|")
        nullable = "null" in parts
        enum_values = [part for part in parts if part not in {"string", "boolean", "null"}]
        if enum_values and _is_enum_schema_text(value):
            return {"type": ["string", "null"] if nullable else "string", "enum": enum_values + ([None] if nullable else [])}
        if "boolean" in parts:
            return {"type": ["boolean", "null"] if nullable else "boolean"}
        if nullable:
            return {"type": ["string", "null"]}
    return {"type": "string"}


def _is_enum_schema_text(value: str) -> bool:
    parts = [part for part in str(value or "").split("|") if part]
    if len(parts) < 2:
        return False
    symbolic = re.compile(r"^[0-9A-Za-z_\-]+$")
    return all(part in {"null", "string", "boolean"} or bool(symbolic.match(part)) for part in parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 4 v4: assign PDF-first topics against the global semantic tree")
    parser.add_argument("--structure-units-json", required=True, help="Step 3.1 structure_units.json")
    parser.add_argument("--entity-facts-json", help="Optional Step 3.5 entity_facts.json")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--existing-tree-json", help="Optional current tree payload from DB or previous packet")
    parser.add_argument("--attachment-context-json", action="append", default=[], help="Attachment Step 5 retrieval_chunks.json; repeatable")
    parser.add_argument("--topic-subject-v3-json", action="append", default=[], help="Optional Topic Subject V3 artifact JSON; accepted entailed rows are used as hints only")
    parser.add_argument("--input-pdf", help="Original PDF path for document-level title context")
    parser.add_argument("--packet-role", choices=["attachment", "protocol", "unknown"], default="unknown")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-base-url", default=DEFAULT_OLLAMA_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--max-units-per-call", type=int, default=4)
    parser.add_argument("--max-raw-chars", type=int, default=1200)
    parser.add_argument("--disable-govmap-geo-fallback", action="store_true", help="Disable live GovMap search for ambiguous location-only topic fallback")
    parser.add_argument("--dicta-mode", choices=["auto", "disabled", "required", "dicta_authoritative", "dicta_contextual"], default="auto", help="auto uses Dicta only for ambiguous items; disabled imports ambiguous items as candidate topics; required sends topic-bearing items to Dicta; dicta_authoritative makes valid Dicta roots authoritative; dicta_contextual first normalizes event context and then classifies with compact root/child context")
    args = parser.parse_args()

    structure_path = Path(args.structure_units_json).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(exist_ok=True)
    model_call_dir = output_dir / "dicta_call_pairs"
    model_call_dir.mkdir(exist_ok=True)

    structure_payload = json.loads(structure_path.read_text(encoding="utf-8"))
    entity_payload = _load_json(Path(args.entity_facts_json).expanduser().resolve()) if args.entity_facts_json else {}
    existing_tree = _load_json(Path(args.existing_tree_json).expanduser().resolve()) if args.existing_tree_json else None
    attachment_contexts = _load_attachment_contexts(args.attachment_context_json)
    topic_subject_v3_hints = _load_topic_subject_v3_hints(args.topic_subject_v3_json)

    units = [unit for unit in structure_payload.get("structure_units") or [] if str(unit.get("structural_role") or "") not in EXCLUDED_STRUCTURAL_ROLES]
    units = _apply_topic_unit_grouping(units=units, packet_role=str(args.packet_role))
    facts_by_unit = _facts_by_unit(entity_payload.get("entity_facts") or [])
    document_context = _document_context(input_pdf=args.input_pdf, packet_role=str(args.packet_role), units=units)
    topic_contexts_by_unit = _topic_contexts_by_unit(units)
    document_child_candidates = _document_child_candidates(units=units, document_context=document_context, topic_contexts_by_unit=topic_contexts_by_unit)
    tree_payload = global_topic_tree_payload(existing_tree=existing_tree, attachment_contexts=[])
    topic_profile_index = build_topic_profile_index(tree_payload)
    items = []
    for unit in units:
        unit_id = str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or "")
        unit_facts = list(facts_by_unit.get(unit_id, []))
        for merged_unit_id in unit.get("merged_detail_unit_ids") or []:
            unit_facts.extend(facts_by_unit.get(str(merged_unit_id), []))
        items.append(
            _build_item(
                unit=unit,
                facts=unit_facts,
                max_raw_chars=args.max_raw_chars,
                attachment_contexts=attachment_contexts,
                document_context=document_context,
                topic_context=topic_contexts_by_unit.get(unit_id, {}),
                document_child_candidates=document_child_candidates,
                topic_tree=tree_payload,
                topic_index=topic_profile_index,
                topic_subject_v3_hints=topic_subject_v3_hints,
            )
        )
    for item in items:
        item["dicta_mode"] = str(args.dicta_mode)
    if str(args.dicta_mode) == "dicta_contextual":
        _attach_contextual_event_context(items)
    assignments_by_id: dict[str, dict[str, Any]] = {}
    model_items: list[dict[str, Any]] = []
    deterministic_count = 0
    low_confidence_candidate_count = 0
    for item in items:
        if _preassign_without_model(item):
            assignment = _non_topic_assignment(item)
            assignments_by_id[item["structure_unit_id"]] = assignment
            _write_cached_assignment(checkpoint_dir=checkpoint_dir, item=item, model=str(args.model), assignment=assignment)
        elif str(args.dicta_mode) == "dicta_contextual" and _preassign_contextual_with_candidate_finder(item):
            assignment = _assignment_from_candidate_finder(item)
            assignments_by_id[item["structure_unit_id"]] = assignment
            deterministic_count += 1
            _write_cached_assignment(checkpoint_dir=checkpoint_dir, item=item, model=str(args.model), assignment=assignment)
        elif str(args.dicta_mode) not in DICTA_FULL_MODEL_MODES and _preassign_with_candidate_finder(item):
            assignment = _assignment_from_candidate_finder(item)
            assignments_by_id[item["structure_unit_id"]] = assignment
            deterministic_count += 1
            _write_cached_assignment(checkpoint_dir=checkpoint_dir, item=item, model=str(args.model), assignment=assignment)
        elif str(args.dicta_mode) == "disabled":
            assignment = _candidate_review_assignment(item=item, reason="dicta_disabled")
            assignments_by_id[item["structure_unit_id"]] = assignment
            low_confidence_candidate_count += 1
            _write_cached_assignment(checkpoint_dir=checkpoint_dir, item=item, model=str(args.model), assignment=assignment)
        else:
            model_items.append(item)
    model_errors: list[dict[str, Any]] = []
    cache_hit_count = 0
    started_all = time.perf_counter()
    for batch in _adaptive_batches(model_items, max(1, int(args.max_units_per_call))):
        pending: list[dict[str, Any]] = []
        for item in batch:
            cached = _load_cached_assignment(checkpoint_dir=checkpoint_dir, item=item, model=str(args.model))
            if cached is None:
                pending.append(item)
                continue
            assignments_by_id[item["structure_unit_id"]] = cached
            cache_hit_count += 1
        if not pending:
            print(json.dumps({"batch": [item["structure_unit_id"] for item in batch], "status": "cached", "elapsed_seconds": 0.0}, ensure_ascii=False), flush=True)
            continue
        batch_assignments, batch_errors = _assign_items_with_retry(items=pending, topic_tree=tree_payload, model=str(args.model), base_url=str(args.ollama_base_url).rstrip("/"), timeout_seconds=max(1.0, float(args.timeout_seconds)), checkpoint_dir=checkpoint_dir, model_call_dir=model_call_dir)
        model_errors.extend(batch_errors)
        assignments_by_id.update({str(row.get("structure_unit_id") or ""): row for row in batch_assignments if str(row.get("structure_unit_id") or "")})

    assignments = [assignments_by_id.get(item["structure_unit_id"]) or _fallback_assignment(item, reason="missing_after_retry") for item in items]
    child_only_judged_count = 0
    if str(args.dicta_mode) == "dicta_contextual":
        assignments, child_errors, child_only_judged_count = _apply_child_only_dicta_judgements(assignments=assignments, items=items, model=str(args.model), base_url=str(args.ollama_base_url).rstrip("/"), timeout_seconds=max(1.0, float(args.timeout_seconds)), model_call_dir=model_call_dir)
        model_errors.extend(child_errors)
    assignments = _apply_structured_fallbacks(assignments=assignments, items=items, enable_govmap_geo=not bool(args.disable_govmap_geo_fallback))
    assignments = _apply_topic_arbitration(assignments=assignments, items=items, enable_govmap_geo=not bool(args.disable_govmap_geo_fallback))
    assignments = _mark_inherited_duplicate_topic_rows(assignments)
    assignments = _normalize_protocol_non_topic_assignments(assignments, items=items)
    assignments = _enforce_structure_ineligible_rows(assignments=assignments, items=items)

    validation = _validate(assignments=assignments, items=items, item_count=len(items), model_errors=model_errors, deterministic_count=deterministic_count, low_confidence_candidate_count=low_confidence_candidate_count, dicta_mode=str(args.dicta_mode))
    validation["child_only_judged_count"] = child_only_judged_count
    output = {
        "step": "step4_v4_global_topic_assignment",
        "topic_tree_version": TOPIC_TREE_VERSION,
        "topic_assignment_backend": TOPIC_ASSIGNMENT_BACKEND,
        "backend_version": BACKEND_VERSION,
        "model": args.model,
        "input_structure_units_json": str(structure_path),
        "input_entity_facts_json": str(Path(args.entity_facts_json).expanduser().resolve()) if args.entity_facts_json else None,
        "topic_tree": tree_payload,
        "document_context": document_context,
        "attachment_context_count": len(attachment_contexts),
        "cache_hit_count": cache_hit_count,
        "checkpoint_dir": str(checkpoint_dir),
        "items": items,
        "topic_assignments": assignments,
        "elapsed_seconds": round(time.perf_counter() - started_all, 3),
    }
    assignments_path = output_dir / "topic_assignments.json"
    validation_path = output_dir / "validation_report.json"
    quality_path = output_dir / "classifier_quality_report.json"
    quality_md_path = output_dir / "classifier_quality_report.md"
    mirror_queue_path = output_dir / "assistant_mirror_queue.json"
    assignments_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    quality_report = validation.get("classifier_quality_report") or {}
    quality_path.write_text(json.dumps(quality_report, ensure_ascii=False, indent=2), encoding="utf-8")
    quality_md_path.write_text(_classifier_quality_markdown(quality_report), encoding="utf-8")
    _write_assistant_mirror_queue(model_call_dir=model_call_dir, output_path=mirror_queue_path)
    print(json.dumps({"topic_assignments": str(assignments_path), "validation_report": str(validation_path), "classifier_quality_report_json": str(quality_path), "classifier_quality_report_md": str(quality_md_path), **validation}, ensure_ascii=False))
    return 0 if validation["accept_for_next_step"] else 2


def _build_item(*, unit: dict[str, Any], facts: list[dict[str, Any]], max_raw_chars: int, attachment_contexts: list[dict[str, Any]], document_context: dict[str, Any], topic_context: dict[str, Any], document_child_candidates: list[dict[str, Any]], topic_tree: dict[str, Any], topic_index: dict[str, Any], topic_subject_v3_hints: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    unit_id = str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or "")
    raw_text = _compact(unit.get("raw_text"))
    summary = _compact(unit.get("summary_he"))
    packet_role = str(document_context.get("packet_role") or "")
    if str(unit.get("topic_group_role") or "") == "merged_detail":
        return _build_merged_detail_item(unit=unit, unit_id=unit_id, raw_text=raw_text, max_raw_chars=max_raw_chars, document_context=document_context, topic_context=topic_context, topic_subject_v3_hints=topic_subject_v3_hints or {})
    anchor_raw_text = _compact(unit.get("topic_group_anchor_raw_text"))
    headline_unit = {**unit, "raw_text": anchor_raw_text} if anchor_raw_text else unit
    topic_shape_raw_text = anchor_raw_text or raw_text
    source_actions = [str(value) for value in unit.get("explicit_actions") or [] if str(value).strip()]
    topic_text = "" if anchor_raw_text else _compact(topic_context.get("topic_identification_text"))
    unit_headline, headline_source = _headline_with_source_from_unit(headline_unit)
    subject_scoped_anchor = _subject_scoped_transcript_topic_anchor(unit=headline_unit, document_context=document_context)
    topic_context_source = str(topic_context.get("context_source") or "")
    if subject_scoped_anchor:
        topic_text = str(subject_scoped_anchor.get("topic_subject_he") or "")
        topic_context_source = str(subject_scoped_anchor.get("context_source") or "subject_scoped_speaker_anchor")
        headline_source = str(subject_scoped_anchor.get("headline_source") or "subject_scoped_speaker_opening")
    elif packet_role == "protocol" and str(document_context.get("topic_carrier_mode") or "") == "subject_scoped_transcript_topics":
        topic_text = ""
        topic_context_source = "subject_scoped_no_anchor"
    elif not topic_text and unit_headline:
        topic_text = unit_headline
        topic_context_source = "unit_heading"
    repaired_topic_text = _repair_embedded_carrier_subject(topic_text) or ("" if topic_text else _repair_embedded_carrier_subject(raw_text))
    if repaired_topic_text:
        topic_text = repaired_topic_text
    if packet_role == "protocol":
        explicit_topic_subject = _best_explicit_topic_subject(
            {
                "unit_raw_text": topic_shape_raw_text,
                "topic_headline_he": topic_text,
                "topic_identification_context": topic_text,
            }
        )
        if explicit_topic_subject and (not topic_text or (not anchor_raw_text and _subject_noise_score(explicit_topic_subject) < _subject_noise_score(topic_text))):
            topic_text = explicit_topic_subject
            topic_context_source = "explicit_topic_marker_subject"
            headline_source = "explicit_topic_marker"
    topic_contract = _topic_contract_from_headline(topic_text, structural_role=str(unit.get("structural_role") or ""), packet_role=packet_role)
    provenance_reason = None if subject_scoped_anchor else _protocol_topic_provenance_reject_reason(unit=headline_unit, headline=topic_text, raw_text=topic_shape_raw_text, topic_context_source=topic_context_source, headline_source=headline_source, packet_role=packet_role)
    if provenance_reason:
        topic_contract = {**topic_contract, "is_topic_bearing": False, "topic_subject_he": None, "non_topic_reason": provenance_reason}
    non_topic_shape_text = topic_text if subject_scoped_anchor else topic_shape_raw_text
    non_topic_reason = _non_topic_protocol_reason(headline=topic_text or topic_shape_raw_text, raw_text=non_topic_shape_text, structural_role=str(unit.get("structural_role") or ""), packet_role=packet_role)
    if non_topic_reason and _non_topic_reason_should_override_topic_contract(reason=non_topic_reason, headline=topic_text or raw_text, topic_contract=topic_contract):
        topic_contract = {**topic_contract, "is_topic_bearing": False, "topic_subject_he": None, "non_topic_reason": non_topic_reason}
    row_type = "topic_item" if subject_scoped_anchor and bool(topic_contract.get("is_topic_bearing")) else _row_type_for_unit(unit=headline_unit, topic_contract=topic_contract, packet_role=packet_role)
    if row_type in {"metadata", "container", "vote_or_result", "fragment", "attribution_fragment"}:
        topic_contract = {**topic_contract, "is_topic_bearing": False, "topic_subject_he": None}
    topic_subject = str(topic_contract.get("topic_subject_he") or "")
    explicit_actions = _topic_actions_for_unit(actions=source_actions, topic_text=topic_subject or topic_text, raw_text=raw_text, packet_role=packet_role)
    evidence_text = _join_unique([raw_text, summary, "\n".join(source_actions if packet_role != "protocol" else explicit_actions)])
    classification_text = topic_subject if packet_role == "protocol" and topic_subject else (topic_text if packet_role == "protocol" and topic_text else evidence_text)
    policy_matches = topic_policy_matches(classification_text, limit=3)
    reference_text = _join_unique([classification_text, raw_text]) if packet_role == "protocol" else evidence_text
    referenced_contexts = referenced_attachment_contexts(text=reference_text, attachment_contexts=attachment_contexts)
    candidate_child_topics = _candidate_child_topics(
        unit_id=unit_id,
        text=classification_text,
        evidence_text=evidence_text,
        outline_title=str(unit.get("header_text") or unit.get("title_he") or unit.get("section_title_he") or ""),
        explicit_actions=explicit_actions,
        document_context=document_context,
        document_child_candidates=document_child_candidates,
        referenced_attachment_contexts=referenced_contexts,
        topic_tree=topic_tree,
        structural_role=str(unit.get("structural_role") or ""),
    )
    candidate_finder = find_topic_candidates(
        text=classification_text,
        evidence_text="" if packet_role == "protocol" else evidence_text,
        topic_tree=topic_tree,
        topic_index=topic_index,
        policy_matches=policy_matches,
        child_candidates=candidate_child_topics,
        is_topic_bearing=bool(topic_contract.get("is_topic_bearing")),
    )
    return {
        "structure_unit_id": unit_id,
        "semantic_unit_id": str(unit.get("semantic_unit_id") or unit_id),
        "source_window_id": unit.get("source_window_id"),
        "source_region_ids": unit.get("source_region_ids") or [],
        "source_block_ids": unit.get("source_block_ids") or [],
        "source_page": _positive_int(unit.get("page")),
        "structural_role": str(unit.get("structural_role") or ""),
        "row_type": row_type,
        "skip_model_assignment": _skip_model_for_row_type(row_type=row_type, packet_role=packet_role),
        "topic_assignment_eligible": unit.get("topic_assignment_eligible"),
        "section_id": unit.get("section_id"),
        "section_number": unit.get("section_number"),
        "continuation_of_unit_id": unit.get("continuation_of_unit_id"),
        "outline_title_he": unit.get("header_text") or unit.get("title_he") or unit.get("section_title_he"),
        "topic_identification_context": classification_text[:600],
        "topic_headline_he": topic_text[:600],
        "agenda_carrier_he": topic_contract.get("agenda_carrier_he"),
        "topic_subject_he": topic_contract.get("topic_subject_he"),
        "attribution_he": topic_contract.get("attribution_he"),
        "is_topic_bearing": bool(topic_contract.get("is_topic_bearing")),
        "non_topic_reason": topic_contract.get("non_topic_reason"),
        "agenda_item_title_he": topic_context.get("agenda_item_title_he"),
        "parent_agenda_unit_id": topic_context.get("parent_agenda_unit_id"),
        "topic_context_source": topic_context_source or None,
        "topic_headline_source": headline_source,
        "topic_provenance_reject_reason": provenance_reason,
        "topic_anchor_quote_he": subject_scoped_anchor.get("topic_supporting_quote_he") if subject_scoped_anchor else None,
        "protocol_subject_he": document_context.get("protocol_subject_he"),
        "topic_carrier_mode": document_context.get("topic_carrier_mode"),
        "unit_raw_text": raw_text,
        "raw_text": evidence_text[: max(250, int(max_raw_chars))],
        "explicit_actions": explicit_actions[:8],
        "accepted_entity_spans": [str(fact.get("canonical_raw_span") or "") for fact in facts[:16] if str(fact.get("canonical_raw_span") or "").strip()],
        "document_context": document_context,
        "candidate_child_topics": candidate_child_topics,
        "root_topic_candidates": candidate_finder.get("candidates") or [],
        "deterministic_topic_decision": candidate_finder.get("decision") or {},
        "deterministic_classifier_method": candidate_finder.get("method"),
        "topic_policy_matches": policy_matches,
        "referenced_attachment_contexts": referenced_contexts,
        "topic_subject_v3_hints": _topic_subject_v3_hints_for_unit(unit=unit, unit_id=unit_id, raw_text=raw_text, hint_index=topic_subject_v3_hints or {}),
        "topic_group_id": unit.get("topic_group_id"),
        "topic_group_role": unit.get("topic_group_role"),
        "topic_group_source_unit_ids": unit.get("topic_group_source_unit_ids") or [],
        "merged_detail_unit_ids": unit.get("merged_detail_unit_ids") or [],
        "merged_detail_texts": unit.get("merged_detail_texts") or [],
    }


def _apply_topic_unit_grouping(*, units: list[dict[str, Any]], packet_role: str) -> list[dict[str, Any]]:
    if packet_role != "protocol":
        return [dict(unit) for unit in units]
    grouped = [dict(unit) for unit in units]
    by_id = {str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or ""): unit for unit in grouped}
    current_anchor_id = ""
    for unit in grouped:
        unit_id = str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or "")
        if not unit_id or str(unit.get("structural_role") or "") in {"metadata", "noise", "table_header_only", "vote_or_result"}:
            continue
        current_anchor = by_id.get(current_anchor_id, {}) if current_anchor_id else {}
        if current_anchor and _should_merge_unit_into_topic_group(unit=unit, anchor=current_anchor):
            _mark_topic_group_detail(unit=unit, anchor=current_anchor)
            continue
        if _unit_starts_topic_group_anchor(unit=unit, current_anchor=current_anchor):
            current_anchor_id = unit_id
            unit.setdefault("topic_group_id", unit_id)
            unit["topic_group_role"] = "anchor"
            unit.setdefault("topic_group_source_unit_ids", [unit_id])
            continue
        if _unit_resets_topic_group_context(unit):
            current_anchor_id = ""
            continue
        current_anchor = by_id.get(current_anchor_id, {}) if current_anchor_id else {}
        if current_anchor and _should_merge_unit_into_topic_group(unit=unit, anchor=current_anchor):
            _mark_topic_group_detail(unit=unit, anchor=current_anchor)
            continue
    for unit in grouped:
        if str(unit.get("topic_group_role") or "") != "anchor":
            continue
        detail_texts = [str(text) for text in unit.get("merged_detail_texts") or [] if str(text).strip()]
        if not detail_texts:
            continue
        original_raw = _compact(unit.get("raw_text"))
        unit["topic_group_anchor_raw_text"] = original_raw
        unit["raw_text"] = _join_unique([original_raw, *detail_texts])
        unit["summary_he"] = _join_unique([unit.get("summary_he"), *detail_texts])
    return grouped


def _build_merged_detail_item(*, unit: dict[str, Any], unit_id: str, raw_text: str, max_raw_chars: int, document_context: dict[str, Any], topic_context: dict[str, Any], topic_subject_v3_hints: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "structure_unit_id": unit_id,
        "semantic_unit_id": str(unit.get("semantic_unit_id") or unit_id),
        "source_window_id": unit.get("source_window_id"),
        "source_region_ids": unit.get("source_region_ids") or [],
        "source_block_ids": unit.get("source_block_ids") or [],
        "source_page": _positive_int(unit.get("page")),
        "structural_role": str(unit.get("structural_role") or ""),
        "row_type": "merged_topic_detail",
        "skip_model_assignment": True,
        "topic_assignment_eligible": False,
        "section_id": unit.get("section_id"),
        "section_number": unit.get("section_number"),
        "continuation_of_unit_id": unit.get("continuation_of_unit_id"),
        "outline_title_he": unit.get("header_text") or unit.get("title_he") or unit.get("section_title_he"),
        "topic_identification_context": raw_text[:600],
        "topic_headline_he": None,
        "agenda_carrier_he": None,
        "topic_subject_he": None,
        "attribution_he": None,
        "is_topic_bearing": False,
        "non_topic_reason": "merged_topic_detail",
        "agenda_item_title_he": topic_context.get("agenda_item_title_he"),
        "parent_agenda_unit_id": topic_context.get("parent_agenda_unit_id"),
        "topic_context_source": "merged_topic_detail",
        "topic_headline_source": "merged_topic_detail",
        "topic_provenance_reject_reason": "merged_topic_detail",
        "topic_anchor_quote_he": None,
        "protocol_subject_he": document_context.get("protocol_subject_he"),
        "topic_carrier_mode": document_context.get("topic_carrier_mode"),
        "unit_raw_text": raw_text,
        "raw_text": raw_text[: max(250, int(max_raw_chars))],
        "explicit_actions": [],
        "accepted_entity_spans": [],
        "document_context": document_context,
        "candidate_child_topics": [],
        "root_topic_candidates": [],
        "deterministic_topic_decision": {"action": "non_topic", "reason": "merged_topic_detail", "needs_dicta": False},
        "deterministic_classifier_method": "topic_unit_grouping_v1",
        "topic_policy_matches": [],
        "referenced_attachment_contexts": [],
        "topic_subject_v3_hints": _topic_subject_v3_hints_for_unit(unit=unit, unit_id=unit_id, raw_text=raw_text, hint_index=topic_subject_v3_hints),
        "topic_group_id": unit.get("topic_group_id"),
        "topic_group_role": "merged_detail",
        "merged_into_unit_id": unit.get("merged_into_unit_id"),
        "topic_group_source_unit_ids": unit.get("topic_group_source_unit_ids") or [],
        "merged_detail_unit_ids": [],
        "merged_detail_texts": [],
    }


def _mark_topic_group_detail(*, unit: dict[str, Any], anchor: dict[str, Any]) -> None:
    anchor_id = str(anchor.get("structure_unit_id") or anchor.get("semantic_unit_id") or "")
    unit_id = str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or "")
    raw_text = _compact(unit.get("raw_text"))
    unit["topic_group_id"] = anchor_id
    unit["topic_group_role"] = "merged_detail"
    unit["merged_into_unit_id"] = anchor_id
    unit["topic_assignment_eligible"] = False
    source_ids = _dedupe_strings([*(anchor.get("topic_group_source_unit_ids") or [anchor_id]), unit_id])
    anchor["topic_group_source_unit_ids"] = source_ids
    anchor["merged_detail_unit_ids"] = _dedupe_strings([*(anchor.get("merged_detail_unit_ids") or []), unit_id])
    if raw_text:
        anchor["merged_detail_texts"] = _dedupe_strings([*(anchor.get("merged_detail_texts") or []), raw_text])
    unit["topic_group_source_unit_ids"] = source_ids


def _unit_starts_topic_group_anchor(*, unit: dict[str, Any], current_anchor: dict[str, Any]) -> bool:
    raw = _compact(unit.get("raw_text"))
    if not raw:
        return False
    role = str(unit.get("structural_role") or "")
    if _is_numbered_detail_under_list_anchor(raw=raw, anchor=current_anchor):
        return False
    if _starts_numbered_independent_topic(raw):
        return True
    if role in {"body", "outline_item", "section_heading", "task_row"} and _unit_has_topic_group_anchor_signal(unit):
        return True
    return role == "continuation" and _unit_has_topic_group_anchor_signal(unit) and not current_anchor


def _should_merge_unit_into_topic_group(*, unit: dict[str, Any], anchor: dict[str, Any]) -> bool:
    anchor_id = str(anchor.get("structure_unit_id") or anchor.get("semantic_unit_id") or "")
    unit_id = str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or "")
    if not anchor_id or not unit_id or unit_id == anchor_id:
        return False
    raw = _compact(unit.get("raw_text"))
    if not raw:
        return False
    non_topic_reason = _non_topic_protocol_reason(headline=raw, raw_text=raw, structural_role=str(unit.get("structural_role") or ""), packet_role="protocol")
    if non_topic_reason in {"container_heading", "committee_protocol_date_heading", "procedural_carrier_heading", "protocol_listing", "protocol_cover_metadata", "procedural_packet_carrier"}:
        return False
    if _starts_numbered_independent_topic(raw) and not _is_numbered_detail_under_list_anchor(raw=raw, anchor=anchor):
        return False
    continuation_parent = str(unit.get("continuation_of_unit_id") or "")
    anchor_parent = str(anchor.get("continuation_of_unit_id") or "")
    if continuation_parent == anchor_id:
        return True
    if continuation_parent and anchor_parent and continuation_parent == anchor_parent and _looks_like_dependent_topic_detail(raw):
        return True
    if str(unit.get("section_id") or "") and str(unit.get("section_id") or "") == str(anchor.get("section_id") or "") and str(unit.get("structural_role") or "") in {"continuation", "task_row"}:
        return _looks_like_dependent_topic_detail(raw) or _is_numbered_detail_under_list_anchor(raw=raw, anchor=anchor)
    return False


def _unit_has_topic_group_anchor_signal(unit: dict[str, Any]) -> bool:
    raw = _compact(unit.get("raw_text"))
    heading = _headline_from_unit(unit) or raw
    if _non_topic_protocol_reason(headline=heading, raw_text=raw, structural_role=str(unit.get("structural_role") or ""), packet_role="protocol"):
        return False
    if _best_explicit_topic_subject({"unit_raw_text": raw, "topic_headline_he": heading, "topic_identification_context": heading}):
        return True
    contract = _topic_contract_from_headline(heading, structural_role=str(unit.get("structural_role") or ""), packet_role="protocol")
    return bool(contract.get("is_topic_bearing"))


def _starts_numbered_independent_topic(raw: str) -> bool:
    compact = _compact(raw)
    match = re.match(r"^\s*\d+(?:\.\d+)?\s*[.)'׳]?\s*\*?\s*(.+)$", compact)
    candidate = _strip_leading_topic_group_punctuation(match.group(1) if match else compact)
    normalized = _norm(candidate)
    if not candidate:
        return False
    if _non_topic_protocol_reason(headline=candidate, raw_text=candidate, structural_role="outline_item", packet_role="protocol"):
        return False
    action_starts = (
        "אישור",
        "אישורי",
        "דיון",
        "שאילתה",
        "שאילתא",
        "הצעה לסדר",
        "נושא לדיון",
        "מינוי",
        "מינויים",
        "עדכון",
        "תקצוב",
        "ביטול",
        "פרוטוקול",
    )
    if normalized.startswith(action_starts):
        return True
    if any(term in normalized for term in ("בקשתו של", "בקשתה של", "בקשתם של", "בקשת חבר", "בקשת חברת")):
        return True
    if _numbered_candidate_looks_like_dependent_detail(candidate):
        return False
    return len(_hebrew_tokens(normalized)) >= 3 and not _numbered_candidate_looks_like_dependent_detail(candidate)


def _strip_leading_topic_group_punctuation(value: str) -> str:
    return _compact(str(value or "").strip(" \'\"׳״.,:;()[]-–"))


def _unit_resets_topic_group_context(unit: dict[str, Any]) -> bool:
    raw = _compact(unit.get("raw_text"))
    if not raw:
        return False
    if _starts_numbered_independent_topic(raw):
        return True
    reason = _non_topic_protocol_reason(headline=raw, raw_text=raw, structural_role=str(unit.get("structural_role") or ""), packet_role="protocol")
    return reason in {"container_heading", "committee_protocol_date_heading", "procedural_carrier_heading", "protocol_listing", "protocol_cover_metadata"}


def _is_numbered_detail_under_list_anchor(*, raw: str, anchor: dict[str, Any]) -> bool:
    if not re.match(r"^\s*\d+(?:\.\d+)?\s*[.)'׳]?", _compact(raw)):
        return False
    anchor_text = _norm(_compact(anchor.get("raw_text")))
    if not any(term in anchor_text for term in ("כדלקמן", "להלן", "המפורטים", "המפורטות", "תוספת נכסים")):
        return False
    normalized = _norm(_strip_leading_topic_group_punctuation(re.sub(r"^\s*\d+(?:\.\d+)?\s*[.)'׳]?\s*", "", raw)))
    return not normalized.startswith(("אישור", "דיון", "שאילתה", "שאילתא", "הצעה לסדר", "פרוטוקול"))


def _looks_like_dependent_topic_detail(raw: str) -> bool:
    normalized = _norm(raw)
    if not normalized:
        return False
    numbered = re.match(r"^\s*\d+(?:\.\d+)?\s*[.)'׳]?\s*(.+)$", raw)
    if numbered and _starts_numbered_independent_topic(raw):
        return False
    if _has_explicit_local_topic_marker(normalized) or normalized.startswith(("אישור", "דיון", "שאילתה", "שאילתא", "הצעה לסדר", "פרוטוקול")):
        return False
    detail_terms = (
        "גוש",
        "חלקה",
        "מגרש",
        "רובע",
        "רח'",
        "רחוב",
        "ע ר",
        "ע.ר",
        "לצורך",
        "למטרת",
        "עבור",
        "מ\"ר",
        "מצ\"ל",
        "בעד",
        "נגד",
        "נמנע",
        "שח",
        "₪",
    )
    if any(term in normalized for term in detail_terms):
        return True
    return bool(re.match(r"^\s*(?:[,.;:–-]|\d+(?:\.\d+)?\s*[.)'׳]?)", raw)) and len(_hebrew_tokens(normalized)) <= 18


def _numbered_candidate_looks_like_dependent_detail(candidate: str) -> bool:
    normalized = _norm(_strip_leading_topic_group_punctuation(candidate))
    if not normalized:
        return False
    detail_terms = ("גוש", "חלקה", "מגרש", "רובע", "רח'", "רחוב", "ע ר", "ע.ר", "לצורך", "למטרת", "עבור", "מ\"ר", "מצ\"ל")
    return any(term in normalized for term in detail_terms)


def _call_dictalm(*, items: list[dict[str, Any]], topic_tree: dict[str, Any], model: str, base_url: str, timeout_seconds: float, model_call_dir: Path | None = None, call_context: dict[str, Any] | None = None) -> dict[str, Any]:
    allowed_root_topics = _allowed_root_topics(topic_tree)
    request_payload = {
        "task": "pdf_first_v4_global_topic_assignment",
        "requirements": [
            "Return strict JSON only with key assignments.",
            "Return exactly one assignment per structure_unit_id.",
            "Choose root_topic_id only by copying an exact root_topic_id from allowed_root_topics.",
            "Do not invent, translate, rename, paraphrase, or compose root_topic_id values.",
            "If your preferred semantic category is named differently than the allowed labels, choose the closest allowed root by meaning and copy its exact root_topic_id.",
            "If no allowed root reasonably covers the subject, set root_topic_id null, needs_taxonomy_review true, and proposed_new_root_label_he to the missing category label.",
            "You are not creating a taxonomy. You are assigning each item to the closest existing root in allowed_root_topics.",
            "Use item.root_topic_candidates as the deterministic classifier shortlist; prefer the highest scored candidate unless the current evidence clearly contradicts it.",
            "If item.deterministic_topic_decision.action is needs_judge, decide between the shortlisted candidates; do not invent a new root.",
            "When item.root_topic_candidates is empty, still choose from allowed_root_topics if one reasonably covers the subject.",
            "For topic-bearing protocol rows, do not choose procedural carrier roots such as root_agenda_queries or root_order_proposals merely because the text contains שאילתה or הצעה לסדר.",
            "Choose root_agenda_queries/root_order_proposals only when the row is about the council procedure itself and no substantive municipal subject is present.",
            "If the row says שאילתה/הצעה לסדר בנושא X, classify X, not the carrier.",
            "If the item is only a section heading, procedural dialogue, speaking-time dispute, vote-order discussion, short continuation fragment, or has no municipal subject, set is_topic_bearing false and root_topic_id null.",
            "Do not use root_agenda_queries as a substitute for non-topic. Use root_agenda_queries only for real agenda/query procedure topics.",
            "This root-assignment call does not select child topics. Always set child_choice_id null and child_label_he null here; child selection is judged later after the root is fixed.",
            "Never use נושא כללי.",
            "Do not invent entities, dates, geometry, or municipality-specific schema rules.",
            "For protocol items, identify the topic from topic_identification_context, document title, agenda title, section heading, bounded parent agenda context, or explicit referenced attachment context.",
            "For protocol items, separate agenda carrier/type from semantic subject: carrier examples include question/proposal/approval/protocol form; classify the semantic subject, not the carrier.",
            "Return agenda_carrier_he, topic_subject_he, attribution_he, and is_topic_bearing for every item so the assignment can be audited.",
            "Do not include requester, submitter, signer, vote, date, or person attribution inside child_label_he or topic_subject_he.",
            "Do not include years or dates in topic_subject_he or child_label_he; treat them only as metadata/context.",
            "Use topic-node metadata_schema only as deterministic destinations for details such as raw_text, place, time, people, organizations, and amounts; do not invent labels from metadata fields.",
            "If the headline/context is only a container, fragment, vote/result, person name, date, or generic procedural heading, set is_topic_bearing false and root_topic_id null.",
            "For protocol items, raw_text is evidence only; do not use transcript/body details as the primary topic source.",
            "If topic_carrier_mode is subject_scoped_transcript_topics, use protocol_subject_he only as context and classify the bounded speaker-opening item as the topic.",
            "topic_supporting_quote_he must be copied from raw_text, explicit_actions, document_context, or attachment context.",
            "Metadata role does not force root-only when explicit_actions or document_context contain a concrete municipal subject.",
            "Treat protocol/agenda carriers such as שאילתה, הצעה לסדר, אישור, פרוטוקול ועדה, ועדה, ביקורת, החלטות, dates, requester names, and vote text as context, not as the semantic subject.",
            "Apply topic_policies as reusable taxonomy rules. If a policy applies, return its policy_id and root_topic_id.",
            "If item.topic_policy_matches is not empty, copy item.topic_policy_matches[0].policy_id into policy_id and copy item.topic_policy_matches[0].root_topic_id into root_topic_id unless the item text clearly contradicts that policy.",
            "If no topic_policy applies, classify by the clean semantic subject and explain why the selected root fits better than nearby roots.",
            "Use item.topic_subject_v3_hints only as accepted/entailed evidence hints for action, subject, and phase. They are not taxonomy decisions and must not overwrite allowed roots.",
            "Prefer reusable municipal root domains over one-off action wording.",
        ],
        "schema": {
            "assignments": [
                {
                    "structure_unit_id": "string",
                    "root_topic_id": "string|null",
                    "is_topic_bearing": "boolean",
                    "agenda_carrier_he": "string|null",
                    "topic_subject_he": "string|null",
                    "clean_subject_he": "string|null",
                    "primary_action_he": "string|null",
                    "service_domain_he": "string|null",
                    "policy_id": "string|null",
                    "needs_taxonomy_review": "boolean",
                    "proposed_new_root_label_he": "string|null",
                    "attribution_he": "string|null",
                    "child_choice_id": "string|null",
                    "child_label_he": "string|null",
                    "topic_supporting_quote_he": "string",
                    "confidence": 0.0,
                    "rationale_he": "string",
                    "why_not_other_roots_he": "string|null",
                }
            ]
        },
        "allowed_root_topics": allowed_root_topics,
        "allowed_root_topic_ids": [row["root_topic_id"] for row in allowed_root_topics],
        "topic_tree": _topic_tree_prompt_payload(topic_tree),
        "topic_policies": topic_policy_prompt_payload(),
        "items": _items_for_model_prompt(items, include_child_choices=False),
    }
    body = {
        "model": model,
        "stream": False,
        "think": _model_thinking_enabled(model),
        "format": "json",
        "messages": [
            {"role": "system", "content": _model_system_prompt(model=model, prompt="/no_think\nYou are a Hebrew municipal root-topic judge. Use allowed roots only. Return JSON only.")},
            {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False)},
        ],
        "options": {"temperature": 0.0, "num_predict": 2048},
        "keep_alive": "30m",
    }
    call_id = _model_call_id(step="step4_v4_global_topic_assignment", ids=[str(item.get("structure_unit_id") or "") for item in items], body=body, call_context=call_context or {})
    call_record = _dicta_call_record(call_id=call_id, step="step4_v4_global_topic_assignment", model=model, base_url=base_url, request_payload=request_payload, request_body=body, item_ids=[str(item.get("structure_unit_id") or "") for item in items], call_context=call_context or {})
    _write_dicta_call_record(model_call_dir=model_call_dir, record=call_record)
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=10.0, read=timeout_seconds, write=30.0, pool=10.0)) as client:
            response = client.post(f"{base_url}/api/chat", json=body)
            response.raise_for_status()
            raw_payload = response.json()
    except Exception as exc:  # noqa: BLE001
        call_record["dicta"] = {"status": "error", "error_code": "MODEL_REQUEST_FAILED", "error_text": f"{exc.__class__.__name__}:{exc}", "raw_payload": None}
        _write_dicta_call_record(model_call_dir=model_call_dir, record=call_record)
        return {"error_code": "MODEL_REQUEST_FAILED", "error_text": f"{exc.__class__.__name__}:{exc}", "raw_payload": None}
    parsed = _parse_json_content(str(((raw_payload.get("message") or {}).get("content")) or ""))
    if not isinstance(parsed, dict):
        call_record["dicta"] = {"status": "error", "error_code": "MODEL_INVALID_JSON", "error_text": str(raw_payload)[:500], "raw_payload": raw_payload, "parsed_response": None}
        _write_dicta_call_record(model_call_dir=model_call_dir, record=call_record)
        return {"error_code": "MODEL_INVALID_JSON", "error_text": str(raw_payload)[:500], "raw_payload": raw_payload}
    call_record["dicta"] = {"status": "ok", "raw_payload": raw_payload, "parsed_response": parsed}
    _write_dicta_call_record(model_call_dir=model_call_dir, record=call_record)
    parsed["raw_payload"] = raw_payload
    return parsed


def _assign_items_with_retry(
    *,
    items: list[dict[str, Any]],
    topic_tree: dict[str, Any],
    model: str,
    base_url: str,
    timeout_seconds: float,
    checkpoint_dir: Path,
    model_call_dir: Path,
    split_depth: int = 0,
    omission_retry_depth: int = 0,
    invalid_root_retry_depth: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not items:
        return [], []
    if all(str(item.get("dicta_mode") or "") == "dicta_contextual" for item in items):
        return _assign_items_contextual(items=items, topic_tree=topic_tree, model=model, base_url=base_url, timeout_seconds=timeout_seconds, checkpoint_dir=checkpoint_dir, model_call_dir=model_call_dir, call_context={"split_depth": split_depth, "omission_retry_depth": omission_retry_depth, "invalid_root_retry_depth": invalid_root_retry_depth})
    started = time.perf_counter()
    response = _call_dictalm(items=items, topic_tree=topic_tree, model=model, base_url=base_url, timeout_seconds=timeout_seconds, model_call_dir=model_call_dir, call_context={"split_depth": split_depth, "omission_retry_depth": omission_retry_depth, "invalid_root_retry_depth": invalid_root_retry_depth})
    elapsed = round(time.perf_counter() - started, 3)
    if not response.get("error_code"):
        items_by_id = {item["structure_unit_id"]: item for item in items}
        invalid_root_items = _items_with_invalid_model_roots(response=response, items_by_id=items_by_id)
        invalid_root_assignments: list[dict[str, Any]] = []
        invalid_root_errors: list[dict[str, Any]] = []
        if invalid_root_items and invalid_root_retry_depth < 1:
            invalid_ids = [item["structure_unit_id"] for item in invalid_root_items]
            valid_items_by_id = {unit_id: item for unit_id, item in items_by_id.items() if unit_id not in set(invalid_ids)}
            assignments, omitted_items = _assignments_from_response(response=response, items_by_id=valid_items_by_id)
            print(json.dumps({"batch": invalid_ids, "status": "retry_invalid_root", "elapsed_seconds": elapsed, "split_depth": split_depth, "invalid_root_retry_depth": invalid_root_retry_depth}, ensure_ascii=False), flush=True)
            invalid_root_assignments, invalid_root_errors = _assign_items_with_retry(items=invalid_root_items, topic_tree=topic_tree, model=model, base_url=base_url, timeout_seconds=timeout_seconds, checkpoint_dir=checkpoint_dir, model_call_dir=model_call_dir, split_depth=split_depth + 1, omission_retry_depth=omission_retry_depth, invalid_root_retry_depth=invalid_root_retry_depth + 1)
        else:
            assignments, omitted_items = _assignments_from_response(response=response, items_by_id=items_by_id)
            if invalid_root_items:
                invalid_root_errors = [{"structure_unit_ids": [item["structure_unit_id"]], "error_code": "MODEL_INVALID_ROOT_TOPIC_ID", "error_text": "model returned a root_topic_id outside allowed_root_topics after retry"} for item in invalid_root_items]
        omitted_assignments: list[dict[str, Any]] = []
        omitted_errors: list[dict[str, Any]] = []
        if omitted_items and omission_retry_depth < 2:
            print(json.dumps({"batch": [item["structure_unit_id"] for item in omitted_items], "status": "retry_omitted_units", "elapsed_seconds": elapsed, "split_depth": split_depth, "omission_retry_depth": omission_retry_depth}, ensure_ascii=False), flush=True)
            omitted_assignments, omitted_errors = _assign_items_with_retry(items=omitted_items, topic_tree=topic_tree, model=model, base_url=base_url, timeout_seconds=timeout_seconds, checkpoint_dir=checkpoint_dir, model_call_dir=model_call_dir, split_depth=split_depth + 1, omission_retry_depth=omission_retry_depth + 1, invalid_root_retry_depth=invalid_root_retry_depth)
        elif omitted_items:
            omitted_assignments = [_fallback_assignment(item, reason="model_omitted_unit") for item in omitted_items]
            omitted_errors = [{"structure_unit_ids": [item["structure_unit_id"]], "error_code": "MODEL_OMITTED_UNIT", "error_text": "model returned valid JSON but omitted the unit after retries"} for item in omitted_items]
            print(json.dumps({"batch": [item["structure_unit_id"] for item in omitted_items], "status": "omitted_after_retry", "elapsed_seconds": elapsed, "split_depth": split_depth, "omission_retry_depth": omission_retry_depth}, ensure_ascii=False), flush=True)
        assignments = assignments + omitted_assignments + invalid_root_assignments
        for assignment in assignments:
            item = next((candidate for candidate in items if candidate["structure_unit_id"] == assignment.get("structure_unit_id")), None)
            if item is not None:
                _write_cached_assignment(checkpoint_dir=checkpoint_dir, item=item, model=model, assignment=assignment)
        print(json.dumps({"batch": [item["structure_unit_id"] for item in items], "status": "ok", "elapsed_seconds": elapsed, "split_depth": split_depth, "omitted_count": len(omitted_items)}, ensure_ascii=False), flush=True)
        return assignments, omitted_errors + invalid_root_errors
    if len(items) == 1:
        item = items[0]
        assignment = _fallback_assignment(item, reason="model_error")
        error = {"structure_unit_ids": [item["structure_unit_id"]], **response}
        print(json.dumps({"batch": [item["structure_unit_id"]], "status": "fallback", "elapsed_seconds": elapsed, "split_depth": split_depth, "error_code": response.get("error_code")}, ensure_ascii=False), flush=True)
        return [assignment], [error]
    print(json.dumps({"batch": [item["structure_unit_id"] for item in items], "status": "split_retry", "elapsed_seconds": elapsed, "split_depth": split_depth, "error_code": response.get("error_code")}, ensure_ascii=False), flush=True)
    midpoint = max(1, len(items) // 2)
    left_assignments, left_errors = _assign_items_with_retry(items=items[:midpoint], topic_tree=topic_tree, model=model, base_url=base_url, timeout_seconds=timeout_seconds, checkpoint_dir=checkpoint_dir, model_call_dir=model_call_dir, split_depth=split_depth + 1, omission_retry_depth=omission_retry_depth, invalid_root_retry_depth=invalid_root_retry_depth)
    right_assignments, right_errors = _assign_items_with_retry(items=items[midpoint:], topic_tree=topic_tree, model=model, base_url=base_url, timeout_seconds=timeout_seconds, checkpoint_dir=checkpoint_dir, model_call_dir=model_call_dir, split_depth=split_depth + 1, omission_retry_depth=omission_retry_depth, invalid_root_retry_depth=invalid_root_retry_depth)
    return left_assignments + right_assignments, left_errors + right_errors


def _items_with_invalid_model_roots(*, response: dict[str, Any], items_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = response.get("assignments") if isinstance(response.get("assignments"), list) else []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        unit_id = str(row.get("structure_unit_id") or "")
        root_topic_id = str(row.get("root_topic_id") or "").strip()
        if not unit_id or not root_topic_id:
            continue
        if root_topic_id not in ROOT_BY_ID and unit_id in items_by_id:
            out.append(items_by_id[unit_id])
    return out


def _allowed_root_topics(topic_tree: dict[str, Any]) -> list[dict[str, Any]]:
    rows = topic_tree.get("root_topics") or topic_tree.get("roots") or []
    out = []
    for row in rows:
        root_topic_id = str(row.get("root_topic_id") or "")
        if root_topic_id not in ROOT_BY_ID:
            continue
        out.append(
            {
                "root_topic_id": root_topic_id,
                "root_label_he": row.get("root_label_he") or root_label_for_id(root_topic_id),
                "keywords": [str(value) for value in (row.get("keywords") or [])[:16] if str(value).strip()],
                "aliases": [str(value) for value in (((row.get("profile") if isinstance(row.get("profile"), dict) else {}) or {}).get("aliases_he") or [])[:8] if str(value).strip()],
                "metadata_field_names": (compact_topic_metadata_schema(row.get("metadata_schema") or {}).get("field_names") or [])[:12],
            }
        )
    return out


def _topic_tree_prompt_payload(topic_tree: dict[str, Any]) -> dict[str, Any]:
    """Keep global prompt context compact; rich child context is per item.

    Sending every child profile/example globally makes Dicta drift from the
    required assignments schema. The model only needs root inventory globally;
    candidate children for the current row remain in item.candidate_child_topics.
    """

    roots = []
    for row in topic_tree.get("root_topics") or topic_tree.get("roots") or []:
        root_topic_id = str(row.get("root_topic_id") or "")
        if root_topic_id not in ROOT_BY_ID:
            continue
        roots.append(
            {
                "root_topic_id": root_topic_id,
                "root_label_he": row.get("root_label_he") or root_label_for_id(root_topic_id),
                "keywords": [str(value) for value in (row.get("keywords") or [])[:12] if str(value).strip()],
                "child_count": len(row.get("children") or []),
                "metadata_field_names": (compact_topic_metadata_schema(row.get("metadata_schema") or {}).get("field_names") or [])[:12],
            }
        )
    return {"topic_tree_version": topic_tree.get("topic_tree_version") or TOPIC_TREE_VERSION, "root_topics": roots}


def _items_for_model_prompt(items: list[dict[str, Any]], *, include_child_choices: bool = True) -> list[dict[str, Any]]:
    prompt_items: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        if include_child_choices:
            row["candidate_child_choices"] = _candidate_child_choices_for_prompt(item.get("candidate_child_topics") or [])
        else:
            row.pop("candidate_child_choices", None)
            row.pop("candidate_child_topics", None)
        prompt_items.append(row)
    return prompt_items


def _candidate_child_choices_for_prompt(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for candidate in candidates[:8]:
        out.append(
            {
                "candidate_child_id": candidate.get("candidate_child_id"),
                "root_topic_id": candidate.get("root_topic_id"),
                "root_label_he": candidate.get("root_label_he"),
                "label_he": candidate.get("label_he"),
                "aliases_he": [str(value) for value in (candidate.get("aliases_he") or [])[:5] if str(value).strip()],
                "evidence_quote_he": _compact(candidate.get("evidence_quote_he"))[:220],
                "evidence_source": candidate.get("evidence_source"),
                "match_reason": candidate.get("match_reason"),
                "confidence_hint": candidate.get("confidence_hint"),
                "profile_summary_he": _compact(((candidate.get("profile") if isinstance(candidate.get("profile"), dict) else {}) or {}).get("summary_he"))[:180],
                "metadata_schema": _candidate_metadata_schema_for_prompt(candidate),
            }
        )
    return out


def _candidate_metadata_schema_for_prompt(candidate: dict[str, Any]) -> dict[str, Any]:
    profile = candidate.get("profile") if isinstance(candidate.get("profile"), dict) else {}
    schema = candidate.get("metadata_schema") if isinstance(candidate.get("metadata_schema"), dict) else profile.get("metadata_schema")
    return compact_topic_metadata_schema(schema, max_fields=14)


def _attach_contextual_event_context(items: list[dict[str, Any]]) -> None:
    subject_counts: Counter[str] = Counter()
    for item in items:
        key = _duplicate_subject_norm(item.get("topic_subject_he") or item.get("topic_headline_he") or item.get("topic_identification_context") or "")
        if key:
            subject_counts[key] += 1
    for index, item in enumerate(items):
        item["contextual_event_context"] = _contextual_event_context_for_item(items=items, index=index, subject_counts=subject_counts)


def _contextual_event_context_for_item(*, items: list[dict[str, Any]], index: int, subject_counts: Counter[str]) -> dict[str, Any]:
    item = items[index]
    unit_id = str(item.get("structure_unit_id") or "")
    event_block_items = _contextual_event_block_items(items=items, index=index)
    same_window = [
        candidate
        for candidate in items
        if candidate is not item and item.get("source_window_id") and candidate.get("source_window_id") == item.get("source_window_id")
    ][:8]
    same_section = [
        candidate
        for candidate in items
        if candidate is not item and item.get("section_id") and candidate.get("section_id") == item.get("section_id")
    ][:8]
    before = items[max(0, index - 2) : index]
    after = items[index + 1 : min(len(items), index + 3)]
    subject_key = _duplicate_subject_norm(item.get("topic_subject_he") or item.get("topic_headline_he") or item.get("topic_identification_context") or "")
    return {
        "target": _contextual_row_for_prompt(item),
        "event_block": _contextual_event_block_for_prompt(event_block_items),
        "nearby_rows": [_contextual_row_for_prompt(row) for row in [*before, *after]],
        "same_window_rows": [_contextual_row_for_prompt(row) for row in same_window],
        "same_section_rows": [_contextual_row_for_prompt(row) for row in same_section[:5]],
        "duplicate_subject_count": int(subject_counts.get(subject_key, 0)) if subject_key else 0,
        "candidate_child_choices": _candidate_child_choices_for_prompt(item.get("candidate_child_topics") or []),
        "root_topic_candidates": item.get("root_topic_candidates") or [],
        "deterministic_topic_decision": item.get("deterministic_topic_decision") or {},
        "topic_policy_matches": item.get("topic_policy_matches") or [],
        "topic_subject_v3_hints": item.get("topic_subject_v3_hints") or [],
        "topic_group_id": item.get("topic_group_id"),
        "topic_group_role": item.get("topic_group_role"),
        "merged_into_unit_id": item.get("merged_into_unit_id"),
        "topic_group_source_unit_ids": item.get("topic_group_source_unit_ids") or [],
        "merged_detail_unit_ids": item.get("merged_detail_unit_ids") or [],
        "instructions": [
            "Use the target row as the item being classified.",
            "Use event_block as the bounded paragraph/event context when the target is a split heading, transition row, continuation, page-header remainder, vote/result bridge, or explicit decision fragment.",
            "Use nearby/same-window rows only to recover bounded agenda context, status, duplicate relationships, and missing subject words.",
            "Do not create a new topic from neighbor text when the target row is only a vote/result, procedural fragment, or duplicate heading.",
            "Use topic_subject_v3_hints only as accepted/entailed action and subject evidence hints; never treat them as taxonomy/root decisions.",
        ],
        "structure_unit_id": unit_id,
    }


def _contextual_event_block_items(*, items: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    target = items[index]
    selected: list[tuple[int, dict[str, Any]]] = [(index, target)]
    for candidate_index in range(max(0, index - 4), min(len(items), index + 5)):
        if candidate_index == index:
            continue
        candidate = items[candidate_index]
        if _contextual_event_block_can_include(target=target, candidate=candidate, distance=candidate_index - index):
            selected.append((candidate_index, candidate))
    selected.sort(key=lambda pair: pair[0])
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _candidate_index, row in selected:
        unit_id = str(row.get("structure_unit_id") or "")
        if unit_id in seen:
            continue
        seen.add(unit_id)
        deduped.append(row)
    return deduped[:7]


def _contextual_event_block_can_include(*, target: dict[str, Any], candidate: dict[str, Any], distance: int) -> bool:
    if str(candidate.get("row_type") or "") == "metadata" and not _contextual_rows_have_subject_overlap(target, candidate):
        return False
    target_id = str(target.get("structure_unit_id") or "")
    candidate_id = str(candidate.get("structure_unit_id") or "")
    if str(target.get("continuation_of_unit_id") or "") == candidate_id or str(candidate.get("continuation_of_unit_id") or "") == target_id:
        return True
    same_section = bool(target.get("section_id") and target.get("section_id") == candidate.get("section_id"))
    subject_overlap = _contextual_rows_have_subject_overlap(target, candidate)
    if target.get("source_window_id") and target.get("source_window_id") == candidate.get("source_window_id"):
        return same_section or subject_overlap
    if same_section and abs(distance) <= 3:
        return True
    if abs(distance) <= 2 and subject_overlap:
        return True
    return False


def _contextual_rows_have_subject_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_text = _norm(_join_unique([left.get("topic_subject_he"), left.get("topic_headline_he"), left.get("topic_identification_context")]))
    right_text = _norm(_join_unique([right.get("topic_subject_he"), right.get("topic_headline_he"), right.get("topic_identification_context")]))
    left_tokens = {token for token in _hebrew_tokens(left_text) if len(token) >= 3}
    right_tokens = {token for token in _hebrew_tokens(right_text) if len(token) >= 3}
    if not left_tokens or not right_tokens:
        return False
    overlap = left_tokens & right_tokens
    return len(overlap) >= 2 or len(overlap) >= min(len(left_tokens), len(right_tokens))


def _contextual_event_block_for_prompt(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "row_ids": [row.get("structure_unit_id") for row in rows],
        "rows": [_contextual_row_for_prompt(row) for row in rows],
        "event_block_text": _join_unique([row.get("unit_raw_text") or row.get("raw_text") or row.get("topic_identification_context") for row in rows])[:2200],
    }


def _contextual_row_for_prompt(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "structure_unit_id": item.get("structure_unit_id"),
        "source_page": item.get("source_page"),
        "source_window_id": item.get("source_window_id"),
        "structural_role": item.get("structural_role"),
        "row_type": item.get("row_type"),
        "topic_identification_context": _compact(item.get("topic_identification_context"))[:420],
        "topic_headline_he": _compact(item.get("topic_headline_he"))[:300],
        "topic_subject_he": _compact(item.get("topic_subject_he"))[:300] or None,
        "agenda_item_title_he": _compact(item.get("agenda_item_title_he"))[:300] or None,
        "protocol_subject_he": _compact(item.get("protocol_subject_he"))[:220] or None,
        "topic_context_source": item.get("topic_context_source"),
        "topic_headline_source": item.get("topic_headline_source"),
        "topic_anchor_quote_he": _compact(item.get("topic_anchor_quote_he"))[:360] or None,
        "non_topic_reason": item.get("non_topic_reason"),
        "topic_provenance_reject_reason": item.get("topic_provenance_reject_reason"),
        "raw_text_sample": _compact(item.get("raw_text") or item.get("unit_raw_text"))[:700],
    }


def _apply_child_only_dicta_judgements(
    *,
    assignments: list[dict[str, Any]],
    items: list[dict[str, Any]],
    model: str,
    base_url: str,
    timeout_seconds: float,
    model_call_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    items_by_id = {str(item.get("structure_unit_id") or ""): item for item in items}
    out: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    judged_count = 0
    for assignment in assignments:
        current = dict(assignment)
        item = items_by_id.get(str(current.get("structure_unit_id") or ""))
        choices = _fixed_root_child_candidate_rows(item=item, assignment=current) if item else []
        if not item or not _child_only_judge_eligible(current) or not choices:
            out.append(current)
            continue
        judged_count += 1
        response = _call_child_only_dictalm(
            item=item,
            assignment=current,
            candidate_child_choices=choices,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            model_call_dir=model_call_dir,
        )
        if response.get("error_code"):
            errors.append({"structure_unit_ids": [current.get("structure_unit_id")], **response})
            out.append(current)
            continue
        decision = _child_only_decision_from_response(response=response, structure_unit_id=str(current.get("structure_unit_id") or ""))
        updated, error = _assignment_with_child_only_decision(item=item, assignment=current, decision=decision, candidate_child_choices=choices)
        if error:
            errors.append(error)
        out.append(updated)
    return out, errors, judged_count


def _child_only_judge_eligible(assignment: dict[str, Any]) -> bool:
    if str(assignment.get("root_topic_id") or "") not in ROOT_BY_ID:
        return False
    if not bool(assignment.get("is_topic_bearing")):
        return False
    if str(assignment.get("row_type") or "") != "topic_item":
        return False
    if bool(assignment.get("skip_model_assignment")):
        return False
    if str(assignment.get("topic_node_status") or "active") != "active":
        return False
    if str(assignment.get("topic_review_status") or "") == "needs_review":
        return False
    # Only model-classified rows need a model child judge. Deterministic rows
    # attach high-confidence closed-list children without another model call.
    if "dictalm_v4_global_tree" not in str(assignment.get("topic_assignment_route") or ""):
        return False
    return not bool(assignment.get("child_label_he") or assignment.get("child_topic_id"))


def _fixed_root_child_candidate_rows(*, item: dict[str, Any] | None, assignment: dict[str, Any]) -> list[dict[str, Any]]:
    if item is None:
        return []
    root_topic_id = str(assignment.get("root_topic_id") or "")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for candidate in item.get("candidate_child_topics") or []:
        candidate_id = str(candidate.get("candidate_child_id") or "")
        if not candidate_id or candidate_id in seen:
            continue
        if str(candidate.get("root_topic_id") or "") != root_topic_id:
            continue
        if str(candidate.get("evidence_source") or "") != "existing_tree":
            continue
        if not clean_topic_label(candidate.get("label_he")):
            continue
        seen.add(candidate_id)
        out.append(candidate)
    out.sort(key=lambda row: float(row.get("confidence_hint") or 0.0), reverse=True)
    return out[:6]


def _call_child_only_dictalm(
    *,
    item: dict[str, Any],
    assignment: dict[str, Any],
    candidate_child_choices: list[dict[str, Any]],
    model: str,
    base_url: str,
    timeout_seconds: float,
    model_call_dir: Path | None = None,
) -> dict[str, Any]:
    unit_id = str(assignment.get("structure_unit_id") or item.get("structure_unit_id") or "")
    root_topic_id = str(assignment.get("root_topic_id") or "")
    root_label = str(assignment.get("root_label_he") or root_label_for_id(root_topic_id) or "")
    request_payload = {
        "task": "pdf_first_v4_child_only_topic_selection",
        "requirements": [
            "Return strict JSON only with key decisions.",
            "Return exactly one decision for the provided structure_unit_id.",
            "The root is fixed. Do not change root_topic_id or classify another root.",
            "Choose child_choice_id only by copying an exact candidate_child_id from candidate_child_choices, or return null.",
            "Do not invent, translate, rename, paraphrase, or compose child labels.",
            "Each candidate may include metadata_schema fields such as raw_text, place, time, people, organizations, and amounts. These fields explain where details belong; they are not child labels.",
            "Choose a child only when the current item evidence is about that reusable child topic, not merely mentioning one token from an example.",
            "If no candidate is clearly supported by the evidence, set child_choice_id null.",
            "If the row is a fragment, vote/result, dialogue-only text, or otherwise not a topic, set child_choice_id null.",
            "Never use נושא כללי.",
        ],
        "schema": {
            "decisions": [
                {
                    "structure_unit_id": "string",
                    "fixed_root_topic_id": "string",
                    "child_choice_id": "string|null",
                    "confidence": 0.0,
                    "rationale_he": "string",
                }
            ]
        },
        "structure_unit_id": unit_id,
        "fixed_root_topic_id": root_topic_id,
        "fixed_root_label_he": root_label,
        "item": _child_only_item_prompt(item=item, assignment=assignment),
        "candidate_child_choices": _candidate_child_choices_for_prompt(candidate_child_choices),
    }
    body = {
        "model": model,
        "stream": False,
        "think": _model_thinking_enabled(model),
        "format": "json",
        "messages": [
            {"role": "system", "content": _model_system_prompt(model=model, prompt="/no_think\nYou are a Hebrew municipal child-topic judge. The root is fixed. Return JSON only.")},
            {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False)},
        ],
        "options": {"temperature": 0.0, "num_predict": 768},
        "keep_alive": "30m",
    }
    call_context = {"fixed_root_topic_id": root_topic_id, "child_choice_count": len(candidate_child_choices)}
    call_id = _model_call_id(step="step4_v4_child_only_topic_selection", ids=[unit_id], body=body, call_context=call_context)
    call_record = _dicta_call_record(call_id=call_id, step="step4_v4_child_only_topic_selection", model=model, base_url=base_url, request_payload=request_payload, request_body=body, item_ids=[unit_id], call_context=call_context)
    _write_dicta_call_record(model_call_dir=model_call_dir, record=call_record)
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=10.0, read=timeout_seconds, write=30.0, pool=10.0)) as client:
            response = client.post(f"{base_url}/api/chat", json=body)
            response.raise_for_status()
            raw_payload = response.json()
    except Exception as exc:  # noqa: BLE001
        call_record["dicta"] = {"status": "error", "error_code": "CHILD_MODEL_REQUEST_FAILED", "error_text": f"{exc.__class__.__name__}:{exc}", "raw_payload": None}
        _write_dicta_call_record(model_call_dir=model_call_dir, record=call_record)
        return {"error_code": "CHILD_MODEL_REQUEST_FAILED", "error_text": f"{exc.__class__.__name__}:{exc}", "raw_payload": None}
    parsed = _parse_json_content(str(((raw_payload.get("message") or {}).get("content")) or ""))
    if not isinstance(parsed, dict):
        call_record["dicta"] = {"status": "error", "error_code": "CHILD_MODEL_INVALID_JSON", "error_text": str(raw_payload)[:500], "raw_payload": raw_payload, "parsed_response": None}
        _write_dicta_call_record(model_call_dir=model_call_dir, record=call_record)
        return {"error_code": "CHILD_MODEL_INVALID_JSON", "error_text": str(raw_payload)[:500], "raw_payload": raw_payload}
    call_record["dicta"] = {"status": "ok", "raw_payload": raw_payload, "parsed_response": parsed}
    _write_dicta_call_record(model_call_dir=model_call_dir, record=call_record)
    parsed["raw_payload"] = raw_payload
    return parsed


def _child_only_item_prompt(*, item: dict[str, Any], assignment: dict[str, Any]) -> dict[str, Any]:
    return {
        "structure_unit_id": item.get("structure_unit_id"),
        "row_type": assignment.get("row_type") or item.get("row_type"),
        "structural_role": item.get("structural_role"),
        "topic_identification_context": item.get("topic_identification_context"),
        "topic_headline_he": item.get("topic_headline_he"),
        "topic_subject_he": assignment.get("topic_subject_he") or item.get("topic_subject_he"),
        "raw_text": item.get("raw_text"),
        "explicit_actions": item.get("explicit_actions") or [],
        "topic_supporting_quote_he": assignment.get("topic_supporting_quote_he"),
    }


def _child_only_decision_from_response(*, response: dict[str, Any], structure_unit_id: str) -> dict[str, Any] | None:
    decisions = response.get("decisions") if isinstance(response.get("decisions"), list) else []
    for decision in decisions:
        if isinstance(decision, dict) and str(decision.get("structure_unit_id") or "") == structure_unit_id:
            return decision
    if isinstance(response.get("decision"), dict):
        return response["decision"]
    if "child_choice_id" in response:
        return response
    return None


def _assignment_with_child_only_decision(
    *,
    item: dict[str, Any],
    assignment: dict[str, Any],
    decision: dict[str, Any] | None,
    candidate_child_choices: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    current = dict(assignment)
    unit_id = str(current.get("structure_unit_id") or "")
    if not isinstance(decision, dict):
        return current, {"structure_unit_ids": [unit_id], "error_code": "CHILD_MODEL_OMITTED_UNIT", "error_text": "child-only model returned valid JSON but omitted the unit"}
    choice_id = str(decision.get("child_choice_id") or "").strip()
    if not choice_id:
        current["child_only_dicta_decision"] = {"child_choice_id": None, "rationale_he": _compact(decision.get("rationale_he"))[:180]}
        return current, None
    candidate_by_id = {str(row.get("candidate_child_id") or ""): row for row in candidate_child_choices}
    selected_candidate = candidate_by_id.get(choice_id)
    if selected_candidate is None:
        return current, {"structure_unit_ids": [unit_id], "error_code": "CHILD_MODEL_INVALID_CHOICE", "error_text": "child-only model returned a child_choice_id outside candidate_child_choices"}
    root_topic_id = str(current.get("root_topic_id") or "")
    if str(selected_candidate.get("root_topic_id") or "") != root_topic_id:
        return current, {"structure_unit_ids": [unit_id], "error_code": "CHILD_MODEL_ROOT_MISMATCH", "error_text": "child-only model returned a child outside the fixed root"}
    root_label = str(current.get("root_label_he") or root_label_for_id(root_topic_id) or "")
    raw_child = clean_topic_label(selected_candidate.get("label_he"))
    quote = str(current.get("topic_supporting_quote_he") or selected_candidate.get("evidence_quote_he") or item.get("raw_text") or "")
    validation_evidence = _validation_evidence_text(item=item, quote=quote, selected_candidate=selected_candidate)
    validation = validate_child_label(raw_label=raw_child, root_label_he=root_label, evidence_text=validation_evidence, selected_existing=True, structural_role=str(item.get("structural_role") or "")) if raw_child else None
    resolved = resolve_child_topic_assignment(root_topic_id=root_topic_id, root_label_he=root_label, child_label_he=validation.cleaned_label if validation and validation.status == "active" else raw_child, evidence_text=validation_evidence, structural_role=str(item.get("structural_role") or ""), selected_existing=True)
    if str(resolved.get("root_topic_id") or "") != root_topic_id:
        return current, {"structure_unit_ids": [unit_id], "error_code": "CHILD_MODEL_RESOLVED_ROOT_MISMATCH", "error_text": "child-only child resolution would change the fixed root"}
    child_label = resolved.get("child_label_he")
    if not child_label or str(resolved.get("status") or "") != "active":
        return current, {"structure_unit_ids": [unit_id], "error_code": "CHILD_MODEL_REJECTED_LABEL", "error_text": str(resolved.get("reason") or (validation.reason if validation else "child label rejected"))}
    current["child_topic_id"] = child_topic_id(root_topic_id, child_label)
    current["child_label_he"] = child_label
    current["raw_child_label_he"] = raw_child
    current["topic_node_status"] = "active"
    current["topic_reject_reason"] = None
    current["topic_aliases_he"] = _dedupe_strings([*(current.get("topic_aliases_he") or []), *(resolved.get("aliases_he") or []), *(selected_candidate.get("aliases_he") or [])])
    current["topic_assignment_route"] = f"{current.get('topic_assignment_route') or 'unknown'}:child_only_dicta:{selected_candidate.get('evidence_source') or 'existing_tree'}"
    current["child_only_dicta_decision"] = {
        "child_choice_id": choice_id,
        "confidence": _confidence(decision.get("confidence")),
        "rationale_he": _compact(decision.get("rationale_he"))[:180],
    }
    return current, None


def _load_cached_assignment(*, checkpoint_dir: Path, item: dict[str, Any], model: str) -> dict[str, Any] | None:
    path = _assignment_checkpoint_path(checkpoint_dir=checkpoint_dir, item=item, model=model)
    assignment = _read_cached_assignment(path=path, item=item, model=model)
    if assignment is not None:
        return assignment
    if item.get("topic_subject_v3_hints"):
        return _load_same_unit_cached_assignment(checkpoint_dir=checkpoint_dir, expected_path=path, item=item, model=model)
    return None


def _read_cached_assignment(*, path: Path, item: dict[str, Any], model: str) -> dict[str, Any] | None:
    if not path.exists() or path.stat().st_size <= 0:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if payload.get("cache_version") != CACHE_VERSION:
        return None
    if str(payload.get("model") or "") != str(model or ""):
        return None
    assignment = payload.get("assignment")
    if not isinstance(assignment, dict):
        return None
    if str(assignment.get("structure_unit_id") or "") != str(item.get("structure_unit_id") or ""):
        return None
    return assignment


def _load_same_unit_cached_assignment(*, checkpoint_dir: Path, expected_path: Path, item: dict[str, Any], model: str) -> dict[str, Any] | None:
    safe_unit_id = _safe_checkpoint_unit_id(item.get("structure_unit_id"))
    candidates = [path for path in checkpoint_dir.glob(f"{safe_unit_id}_*.json") if path != expected_path]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        assignment = _read_cached_assignment(path=path, item=item, model=model)
        if assignment is not None:
            return assignment
    return None


def _write_cached_assignment(*, checkpoint_dir: Path, item: dict[str, Any], model: str, assignment: dict[str, Any]) -> None:
    path = _assignment_checkpoint_path(checkpoint_dir=checkpoint_dir, item=item, model=model)
    payload = {"cache_version": CACHE_VERSION, "model": model, "structure_unit_id": item["structure_unit_id"], "assignment": assignment}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _assignment_checkpoint_path(*, checkpoint_dir: Path, item: dict[str, Any], model: str) -> Path:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "cache_version": CACHE_VERSION,
        "model": model,
        "structure_unit_id": item.get("structure_unit_id"),
        "raw_text": item.get("raw_text"),
        "topic_identification_context": item.get("topic_identification_context"),
        "structural_role": item.get("structural_role"),
        "row_type": item.get("row_type"),
        "skip_model_assignment": item.get("skip_model_assignment"),
        "dicta_mode": item.get("dicta_mode"),
        "candidate_child_topics": item.get("candidate_child_topics") or [],
        "root_topic_candidates": item.get("root_topic_candidates") or [],
        "deterministic_topic_decision": item.get("deterministic_topic_decision") or {},
        "topic_policy_matches": item.get("topic_policy_matches") or [],
        "referenced_attachment_contexts": item.get("referenced_attachment_contexts") or [],
        "contextual_event_context": item.get("contextual_event_context") or {},
        "topic_subject_v3_hints": item.get("topic_subject_v3_hints") or [],
    }
    digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]
    safe_unit_id = _safe_checkpoint_unit_id(item.get("structure_unit_id"))
    return checkpoint_dir / f"{safe_unit_id}_{digest}.json"


def _safe_checkpoint_unit_id(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", str(value or "unit"))[:70]


def _model_call_id(*, step: str, ids: list[str], body: dict[str, Any], call_context: dict[str, Any]) -> str:
    digest = hashlib.sha1(json.dumps({"step": step, "ids": ids, "body": body, "call_context": call_context}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]
    label = "_".join(re.sub(r"[^0-9A-Za-z._-]+", "_", value)[:24] for value in ids[:3] if value) or "batch"
    return f"{step}_{label}_{digest}"


def _dicta_call_record(*, call_id: str, step: str, model: str, base_url: str, request_payload: dict[str, Any], request_body: dict[str, Any], item_ids: list[str], call_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "call_id": call_id,
        "step": step,
        "item_ids": item_ids,
        "call_context": call_context,
        "model": model,
        "base_url": base_url,
        "request_payload": request_payload,
        "request_body": request_body,
        "dicta": {"status": "pending"},
        "assistant_mirror": {
            "status": "pending_manual_review",
            "instruction": "Use the exact request_body.messages prompt and return the same JSON schema for comparison with Dicta.",
            "response": None,
        },
    }


def _write_dicta_call_record(*, model_call_dir: Path | None, record: dict[str, Any]) -> None:
    if model_call_dir is None:
        return
    model_call_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^0-9A-Za-z._-]+", "_", str(record.get("call_id") or "dicta_call"))[:180]
    path = model_call_dir / f"{safe_id}.json"
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _write_assistant_mirror_queue(*, model_call_dir: Path, output_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    for path in sorted(model_call_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        calls.append(
            {
                "call_id": record.get("call_id"),
                "prompt_response_file": str(path),
                "step": record.get("step"),
                "item_ids": record.get("item_ids") or [],
                "dicta_status": (record.get("dicta") or {}).get("status"),
                "assistant_mirror_status": (record.get("assistant_mirror") or {}).get("status"),
            }
        )
    payload = {
        "purpose": "Manual mirror-review queue. Each prompt_response_file contains the exact Dicta prompt, Dicta result, and an empty assistant_mirror.response slot.",
        "call_count": len(calls),
        "calls": calls,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _call_json_model(
    *,
    step: str,
    item_ids: list[str],
    request_payload: dict[str, Any],
    system_prompt: str,
    model: str,
    base_url: str,
    timeout_seconds: float,
    model_call_dir: Path | None,
    call_context: dict[str, Any] | None = None,
    num_predict: int = 1536,
    force_no_think: bool = False,
    use_json_schema: bool = False,
) -> dict[str, Any]:
    attempts = [
        {
            "label": "initial",
            "num_predict": num_predict,
            "force_no_think": force_no_think,
            "use_json_schema": use_json_schema,
        },
        {
            "label": "json_schema_retry",
            "num_predict": max(num_predict * 2, 2400),
            "force_no_think": True,
            "use_json_schema": True,
        },
    ]
    last_error: dict[str, Any] | None = None
    for attempt_index, attempt in enumerate(attempts):
        think = False if bool(attempt["force_no_think"]) else _model_thinking_enabled(model)
        attempt_system_prompt = _model_system_prompt_for_think(prompt=system_prompt, think=think)
        response_format: Any = "json"
        if bool(attempt["use_json_schema"]):
            response_format = _json_schema_from_prompt_schema(request_payload.get("schema")) or "json"
        body = {
            "model": model,
            "stream": False,
            "think": think,
            "format": response_format,
            "messages": [
                {"role": "system", "content": attempt_system_prompt},
                {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False)},
            ],
            "options": {"temperature": 0.0, "num_predict": int(attempt["num_predict"])},
            "keep_alive": "30m",
        }
        attempt_context = {**(call_context or {}), "json_attempt": attempt_index, "json_attempt_label": attempt["label"]}
        call_id = _model_call_id(step=step, ids=item_ids, body=body, call_context=attempt_context)
        call_record = _dicta_call_record(call_id=call_id, step=step, model=model, base_url=base_url, request_payload=request_payload, request_body=body, item_ids=item_ids, call_context=attempt_context)
        _write_dicta_call_record(model_call_dir=model_call_dir, record=call_record)
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=10.0, read=timeout_seconds, write=30.0, pool=10.0)) as client:
                response = client.post(f"{base_url}/api/chat", json=body)
                response.raise_for_status()
                raw_payload = response.json()
        except Exception as exc:  # noqa: BLE001
            call_record["dicta"] = {"status": "error", "error_code": "MODEL_REQUEST_FAILED", "error_text": f"{exc.__class__.__name__}:{exc}", "raw_payload": None}
            _write_dicta_call_record(model_call_dir=model_call_dir, record=call_record)
            return {"error_code": "MODEL_REQUEST_FAILED", "error_text": f"{exc.__class__.__name__}:{exc}", "raw_payload": None}
        parsed = _parse_json_content(str(((raw_payload.get("message") or {}).get("content")) or ""))
        incomplete = _raw_model_payload_incomplete(raw_payload)
        if isinstance(parsed, dict) and not incomplete:
            call_record["dicta"] = {"status": "ok", "raw_payload": raw_payload, "parsed_response": parsed}
            _write_dicta_call_record(model_call_dir=model_call_dir, record=call_record)
            parsed["raw_payload"] = raw_payload
            return parsed
        error_code = "MODEL_INCOMPLETE_JSON" if incomplete else "MODEL_INVALID_JSON"
        call_record["dicta"] = {"status": "error", "error_code": error_code, "error_text": str(raw_payload)[:500], "raw_payload": raw_payload, "parsed_response": parsed if isinstance(parsed, dict) else None}
        _write_dicta_call_record(model_call_dir=model_call_dir, record=call_record)
        last_error = {"error_code": error_code, "error_text": str(raw_payload)[:500], "raw_payload": raw_payload}
    return last_error or {"error_code": "MODEL_INVALID_JSON", "error_text": "model returned invalid JSON", "raw_payload": None}


def _raw_model_payload_incomplete(raw_payload: Any) -> bool:
    if not isinstance(raw_payload, dict):
        return False
    if raw_payload.get("done") is False:
        return True
    done_reason = str(raw_payload.get("done_reason") or raw_payload.get("stop_reason") or "").casefold()
    return done_reason in {"length", "max_tokens", "num_predict"}


def _assign_items_contextual(
    *,
    items: list[dict[str, Any]],
    topic_tree: dict[str, Any],
    model: str,
    base_url: str,
    timeout_seconds: float,
    checkpoint_dir: Path,
    model_call_dir: Path,
    call_context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assignments: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for item in items:
        started = time.perf_counter()
        assignment, error = _assign_contextual_item(item=item, topic_tree=topic_tree, model=model, base_url=base_url, timeout_seconds=timeout_seconds, model_call_dir=model_call_dir, call_context=call_context or {})
        assignments.append(assignment)
        if error:
            errors.append(error)
        _write_cached_assignment(checkpoint_dir=checkpoint_dir, item=item, model=model, assignment=assignment)
        print(
            json.dumps(
                {
                    "batch": [item["structure_unit_id"]],
                    "status": "contextual_ok" if not error else "contextual_fallback",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "error_code": error.get("error_code") if error else None,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return assignments, errors


def _assign_contextual_item(
    *,
    item: dict[str, Any],
    topic_tree: dict[str, Any],
    model: str,
    base_url: str,
    timeout_seconds: float,
    model_call_dir: Path,
    call_context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    unit_id = str(item.get("structure_unit_id") or "")
    normalized, normalization_error = _normalize_contextual_event(item=item, model=model, base_url=base_url, timeout_seconds=timeout_seconds, model_call_dir=model_call_dir, call_context=call_context)
    response = _classify_contextual_event(item=item, normalized_event=normalized, topic_tree=topic_tree, model=model, base_url=base_url, timeout_seconds=timeout_seconds, model_call_dir=model_call_dir, call_context=call_context)
    if response.get("error_code"):
        assignment = _fallback_assignment(item, reason="contextual_model_error")
        return assignment, {"structure_unit_ids": [unit_id], **response}
    parsed = _contextual_assignment_from_response(response=response, item=item, normalized_event=normalized)
    if not parsed:
        assignment = _fallback_assignment(item, reason="contextual_model_omitted_unit")
        return assignment, {"structure_unit_ids": [unit_id], "error_code": "CONTEXTUAL_MODEL_OMITTED_UNIT", "error_text": "contextual classifier returned JSON but omitted the target unit"}
    assignment = _assignment_from_parsed(item=item, parsed=parsed)
    if normalization_error:
        assignment["topic_assignment_route"] = f"{assignment.get('topic_assignment_route') or 'unknown'}:normalization_fallback"
    return assignment, normalization_error


def _normalize_contextual_event(*, item: dict[str, Any], model: str, base_url: str, timeout_seconds: float, model_call_dir: Path, call_context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    unit_id = str(item.get("structure_unit_id") or "")
    payload = {
        "task": "pdf_first_v4_contextual_event_normalization",
        "requirements": [
            "Return strict JSON only.",
            "Normalize the target municipal event before taxonomy classification; do not choose root_topic_id here.",
            "Use nearby rows only to recover bounded context, agenda disposition, duplicate/sub-fragment relationships, and missing subject words.",
            "Separate agenda carrier/procedure from semantic subject.",
            "Classify agenda_disposition as one of: approved, discussed, deferred, removed, unknown. This is the council/protocol disposition, not whether the row should be indexed as a topic.",
            "Classify indexability_status as one of: standalone_topic, duplicate_reference, evidence_fragment, procedural_only, insufficient_context, unknown. Only indexability_status decides whether the row is topic-indexable.",
            "Use duplicate_reference only when you can name the duplicated/source row or quote the duplicate-reference evidence; otherwise use standalone_topic or unknown.",
            "A deferred or removed agenda disposition can still be a standalone topic when the row has a bounded municipal subject/action.",
            "Set standalone_event true only when the target row itself represents a standalone council/municipal event rather than only a reference, duplicate, fragment, or procedural/status-only row.",
            "Committee protocol approvals can be standalone municipal events; decide by event structure and evidence, not by fixed wording.",
            "Do not invent municipality-specific rules or facts not supported by the supplied context.",
        ],
        "schema": {
            "structure_unit_id": "string",
            "standalone_event": "boolean",
            "agenda_disposition": "approved|discussed|deferred|removed|unknown",
            "event_status": "approved|discussed|deferred|removed|unknown",
            "indexability_status": "standalone_topic|duplicate_reference|evidence_fragment|procedural_only|insufficient_context|unknown",
            "is_standalone_topic": "boolean",
            "agenda_status_he": "string|null",
            "agenda_carrier_he": "string|null",
            "municipal_action_he": "string|null",
            "semantic_subject_he": "string|null",
            "primary_action_he": "string|null",
            "service_domain_he": "string|null",
            "classification_basis": "primary_action|service_domain|governance_status|unclear",
            "duplicate_of_unit_id": "string|null",
            "duplicate_evidence_quote_he": "string|null",
            "clean_subject_he": "string|null",
            "event_summary_he": "string|null",
            "supporting_quote_he": "string|null",
            "context_notes_he": "string|null",
            "confidence": 0.0,
        },
        "context": item.get("contextual_event_context") or {"target": _contextual_row_for_prompt(item)},
    }
    response = _call_json_model(step="step4_v4_contextual_event_normalization", item_ids=[unit_id], request_payload=payload, system_prompt="/no_think\nYou normalize Hebrew municipal agenda/transcript context into a clean event. Return JSON only.", model=model, base_url=base_url, timeout_seconds=timeout_seconds, model_call_dir=model_call_dir, call_context={**call_context, "contextual_stage": "normalization"}, num_predict=1200)
    if response.get("error_code"):
        return _fallback_contextual_event(item=item, reason=str(response.get("error_code") or "model_error")), {"structure_unit_ids": [unit_id], **response}
    normalized = _repair_contextual_normalization_if_needed(item=item, parsed=response, model=model, base_url=base_url, timeout_seconds=timeout_seconds, model_call_dir=model_call_dir, call_context=call_context)
    normalized = _ensure_contextual_event_defaults(item=item, parsed=normalized)
    return normalized, None


def _repair_contextual_normalization_if_needed(*, item: dict[str, Any], parsed: dict[str, Any], model: str, base_url: str, timeout_seconds: float, model_call_dir: Path, call_context: dict[str, Any]) -> dict[str, Any]:
    missing = _missing_contextual_normalization_fields(parsed)
    if not missing:
        return parsed
    unit_id = str(item.get("structure_unit_id") or "")
    payload = {
        "task": "repair_contextual_event_normalization_json",
        "missing_fields": missing,
        "requirements": ["Return the same JSON object with all missing fields filled.", "Do not change supported facts; use null where unsupported."],
        "schema": {
            "structure_unit_id": "string",
            "standalone_event": "boolean",
            "agenda_disposition": "approved|discussed|deferred|removed|unknown",
            "event_status": "approved|discussed|deferred|removed|unknown",
            "indexability_status": "standalone_topic|duplicate_reference|evidence_fragment|procedural_only|insufficient_context|unknown",
            "is_standalone_topic": "boolean",
            "agenda_status_he": "string|null",
            "agenda_carrier_he": "string|null",
            "municipal_action_he": "string|null",
            "semantic_subject_he": "string|null",
            "primary_action_he": "string|null",
            "service_domain_he": "string|null",
            "classification_basis": "primary_action|service_domain|governance_status|unclear",
            "duplicate_of_unit_id": "string|null",
            "duplicate_evidence_quote_he": "string|null",
            "clean_subject_he": "string|null",
            "event_summary_he": "string|null",
            "supporting_quote_he": "string|null",
            "context_notes_he": "string|null",
            "confidence": 0.0,
        },
        "original_response": {key: value for key, value in parsed.items() if key != "raw_payload"},
        "context": item.get("contextual_event_context") or {"target": _contextual_row_for_prompt(item)},
    }
    repaired = _call_json_model(step="step4_v4_contextual_event_normalization_repair", item_ids=[unit_id], request_payload=payload, system_prompt="/no_think\nRepair missing fields in Hebrew municipal event-normalization JSON. Return JSON only.", model=model, base_url=base_url, timeout_seconds=timeout_seconds, model_call_dir=model_call_dir, call_context={**call_context, "contextual_stage": "normalization_repair", "missing_fields": missing, "helper_profile": "schema_no_think_24b_helpers"}, num_predict=900, force_no_think=True, use_json_schema=True)
    if repaired.get("error_code"):
        return parsed
    return repaired


def _classify_contextual_event(*, item: dict[str, Any], normalized_event: dict[str, Any], topic_tree: dict[str, Any], model: str, base_url: str, timeout_seconds: float, model_call_dir: Path, call_context: dict[str, Any]) -> dict[str, Any]:
    unit_id = str(item.get("structure_unit_id") or "")
    payload = _contextual_classification_payload(item=item, normalized_event=normalized_event, topic_tree=topic_tree)
    response = _call_json_model(step="step4_v4_contextual_root_classification", item_ids=[unit_id], request_payload=payload, system_prompt="/no_think\nYou classify a normalized Hebrew municipal event into one allowed root topic. Return JSON only.", model=model, base_url=base_url, timeout_seconds=timeout_seconds, model_call_dir=model_call_dir, call_context={**call_context, "contextual_stage": "root_classification"}, num_predict=1400)
    if response.get("error_code"):
        return response
    missing = _missing_contextual_classification_fields(response)
    if not missing:
        return response
    repair_payload = {
        "task": "repair_contextual_root_classification_json",
        "missing_fields": missing,
        "requirements": ["Return the same JSON object with all missing fields filled.", "Confidence must be explicit; do not omit it.", "agenda_disposition is approved, discussed, deferred, removed, or unknown.", "indexability_status is standalone_topic, duplicate_reference, evidence_fragment, procedural_only, insufficient_context, or unknown.", "Use null root_topic_id only when is_topic_bearing is false or no allowed root fits."],
        "schema": payload.get("schema"),
        "original_response": {key: value for key, value in response.items() if key != "raw_payload"},
        "classification_payload": payload,
    }
    repaired = _call_json_model(step="step4_v4_contextual_root_classification_repair", item_ids=[unit_id], request_payload=repair_payload, system_prompt="/no_think\nRepair missing fields in Hebrew municipal root-classification JSON. Return JSON only.", model=model, base_url=base_url, timeout_seconds=timeout_seconds, model_call_dir=model_call_dir, call_context={**call_context, "contextual_stage": "root_classification_repair", "missing_fields": missing, "helper_profile": "schema_no_think_24b_helpers"}, num_predict=1000, force_no_think=True, use_json_schema=True)
    return repaired if not repaired.get("error_code") else response


def _contextual_classification_payload(*, item: dict[str, Any], normalized_event: dict[str, Any], topic_tree: dict[str, Any]) -> dict[str, Any]:
    event_text = _contextual_event_text(item=item, normalized_event=normalized_event)
    return {
        "task": "pdf_first_v4_contextual_root_classification",
        "requirements": [
            "Return strict JSON only with key assignments.",
            "Choose root_topic_id only by copying an exact id from allowed_root_topics.",
            "Use the normalized_event as the primary source; use source_context only to verify evidence.",
            "Root-topic candidates are hints, not instructions. Override them when normalized_event or child/sibling examples contradict them.",
            "Use candidate_child_choices as manually curated child-taxonomy evidence for root disambiguation: when an existing child label or alias directly supports the semantic subject, prefer that child's root unless stronger evidence contradicts it.",
            "Use topic-node metadata_schema only as deterministic destinations for row details such as raw_text, place, time, people, organizations, and amounts; do not put those details in topic_subject_he or child_label_he.",
            "This call fixes root/topic-bearing status only. Do not invent or finalize child topics here; child_choice_id and child_label_he should stay null unless a later fixed-root child-only task asks for them.",
            "Classify the semantic municipal subject, not the agenda carrier such as שאילתה, הצעה לסדר, אישור, פרוטוקול ועדה, or vote text.",
            "Use normalized_event.agenda_disposition/event_status only as the council disposition. Deferred or removed items can still be indexed when indexability_status is standalone_topic.",
            "Use topic_subject_v3_hints only as accepted/entailed evidence hints for action, subject, and phase. They are not taxonomy decisions and must not overwrite allowed roots.",
            "Use normalized_event.indexability_status to decide is_topic_bearing: standalone_topic means true; duplicate_reference, evidence_fragment, procedural_only, or insufficient_context mean false unless the target row also identifies a separate standalone municipal action.",
            "Use duplicate_reference only with explicit duplicate evidence: copy duplicate_of_unit_id when known, or provide duplicate_evidence_quote_he. Without that evidence, use standalone_topic or unknown.",
            "Committee protocol approvals are importable council actions when the target row represents approval/discussion of that committee protocol; classify by the committee domain. They are not importable only when normalized_event.indexability_status identifies them as duplicate_reference, evidence_fragment, procedural_only, or insufficient_context.",
            "When primary_action_he is a formal municipal action such as funding/budget change, travel approval/report, agreement/permission, salary/employment, allocation, appointment, or committee protocol, prefer the root representing that action unless the event is clearly about direct service delivery.",
            "Use service_domain_he only as secondary context when the primary action root is more specific.",
            "If normalized_event.standalone_event is false, do not make that alone a non-topic decision; use indexability_status and the presence of a bounded municipal subject/action.",
            "Always return topic_subject_he, clean_subject_he, confidence, rationale_he, why_not_other_roots_he, topic_supporting_quote_he, classification_basis, and competing_roots.",
            "Never use נושא כללי.",
        ],
        "schema": {
            "assignments": [
                {
                    "structure_unit_id": "string",
                    "agenda_disposition": "approved|discussed|deferred|removed|unknown",
                    "event_status": "approved|discussed|deferred|removed|unknown",
                    "indexability_status": "standalone_topic|duplicate_reference|evidence_fragment|procedural_only|insufficient_context|unknown",
                    "root_topic_id": "string|null",
                    "is_topic_bearing": "boolean",
                    "agenda_carrier_he": "string|null",
                    "topic_subject_he": "string|null",
                    "clean_subject_he": "string|null",
                    "primary_action_he": "string|null",
                    "service_domain_he": "string|null",
                    "classification_basis": "primary_action|service_domain|governance_status|unclear",
                    "duplicate_of_unit_id": "string|null",
                    "duplicate_evidence_quote_he": "string|null",
                    "policy_id": "string|null",
                    "needs_taxonomy_review": "boolean",
                    "proposed_new_root_label_he": "string|null",
                    "attribution_he": "string|null",
                    "child_choice_id": "string|null",
                    "child_label_he": "string|null",
                    "topic_supporting_quote_he": "string",
                    "confidence": 0.0,
                    "rationale_he": "string",
                    "why_not_other_roots_he": "string|null",
                    "competing_roots": [
                        {
                            "root_topic_id": "string",
                            "confidence": 0.0,
                            "reason_he": "string"
                        }
                    ],
                }
            ]
        },
        "allowed_root_topics": _contextual_allowed_root_topics(topic_tree=topic_tree, item=item, normalized_event=normalized_event),
        "allowed_root_topic_ids": [row["root_topic_id"] for row in _allowed_root_topics(topic_tree)],
        "matched_topic_policies": _contextual_policy_matches(item=item, normalized_event=normalized_event),
        "normalized_event": normalized_event,
        "source_context": item.get("contextual_event_context") or {"target": _contextual_row_for_prompt(item)},
        "root_topic_candidates": item.get("root_topic_candidates") or [],
        "candidate_child_choices": _candidate_child_choices_for_prompt(item.get("candidate_child_topics") or []),
        "topic_subject_v3_hints": item.get("topic_subject_v3_hints") or [],
        "event_text_for_search": event_text[:900],
    }


def _contextual_assignment_from_response(*, response: dict[str, Any], item: dict[str, Any], normalized_event: dict[str, Any]) -> dict[str, Any] | None:
    rows = _contextual_classification_rows(response)
    unit_id = str(item.get("structure_unit_id") or "")
    parsed = next((row for row in rows if isinstance(row, dict) and str(row.get("structure_unit_id") or "") == unit_id), None)
    if parsed is None:
        return None
    parsed = dict(parsed)
    original_missing_optional_fields = _missing_contextual_classification_audit_fields(parsed)
    parsed = _ensure_contextual_classification_defaults(item=item, parsed=parsed, normalized_event=normalized_event)
    parsed["dicta_mode"] = "dicta_contextual"
    parsed["contextual_mode"] = "dicta_contextual"
    parsed["contextual_schema_valid"] = not _missing_contextual_classification_fields({"assignments": [parsed]})
    parsed["contextual_missing_optional_fields"] = original_missing_optional_fields
    agenda_disposition = _contextual_agenda_disposition(parsed.get("agenda_disposition") or parsed.get("event_status") or normalized_event.get("agenda_disposition") or normalized_event.get("event_status"))
    indexability_status = _contextual_indexability_status(parsed.get("indexability_status") or normalized_event.get("indexability_status"), legacy_event_status=parsed.get("event_status") or normalized_event.get("event_status"))
    if indexability_status == "duplicate_reference" and not _contextual_duplicate_reference_supported(item=item, parsed=parsed, normalized_event=normalized_event):
        parsed["contextual_original_indexability_status"] = "duplicate_reference"
        parsed["contextual_indexability_override_reason"] = "unsupported_duplicate_reference"
        indexability_status = "standalone_topic" if _contextual_has_bounded_topic_subject(item=item, parsed=parsed, normalized_event=normalized_event) else "unknown"
    parsed["contextual_event_status"] = agenda_disposition
    parsed["contextual_agenda_disposition"] = agenda_disposition
    parsed["contextual_indexability_status"] = indexability_status
    parsed["contextual_classification_basis"] = _contextual_classification_basis(parsed.get("classification_basis") or normalized_event.get("classification_basis"))
    parsed["contextual_competing_roots"] = _augment_contextual_competing_roots(item=item, parsed=parsed, normalized_event=normalized_event, roots=_contextual_competing_roots(parsed.get("competing_roots")))
    parsed["contextual_standalone_event"] = _contextual_standalone_event(normalized_event)
    parsed["contextual_event_summary_he"] = _compact(normalized_event.get("event_summary_he")) or None
    parsed["contextual_agenda_status_he"] = _compact(normalized_event.get("agenda_status_he")) or None
    parsed["contextual_normalization_confidence"] = _optional_confidence(normalized_event.get("confidence"))
    if indexability_status == "standalone_topic" and str(parsed.get("root_topic_id") or "") in ROOT_BY_ID and _compact(parsed.get("topic_subject_he") or parsed.get("clean_subject_he") or normalized_event.get("clean_subject_he")):
        parsed["is_topic_bearing"] = True
    if not _compact(parsed.get("topic_subject_he") or parsed.get("clean_subject_he")):
        parsed["topic_subject_he"] = normalized_event.get("clean_subject_he")
        parsed["clean_subject_he"] = normalized_event.get("clean_subject_he")
    root_label = root_label_for_id(str(parsed.get("root_topic_id") or "")) or ""
    if root_label and _norm(str(parsed.get("topic_subject_he") or "")) == _norm(root_label) and _compact(normalized_event.get("clean_subject_he")):
        parsed["topic_subject_he"] = normalized_event.get("clean_subject_he")
        parsed["clean_subject_he"] = normalized_event.get("clean_subject_he")
    parsed.setdefault("agenda_carrier_he", normalized_event.get("agenda_carrier_he"))
    parsed.setdefault("primary_action_he", normalized_event.get("primary_action_he"))
    parsed.setdefault("service_domain_he", normalized_event.get("service_domain_he"))
    if not _compact(parsed.get("topic_supporting_quote_he")):
        parsed["topic_supporting_quote_he"] = normalized_event.get("supporting_quote_he") or _contextual_quote_fallback(item)
    if not _compact(parsed.get("rationale_he")) and str(parsed.get("root_topic_id") or "") in ROOT_BY_ID and parsed.get("is_topic_bearing") is True:
        parsed["rationale_he"] = _contextual_fallback_rationale(item=item, parsed=parsed, normalized_event=normalized_event)
        parsed["contextual_rationale_fallback"] = True
    override_root = _contextual_validation_root_override(item=item, parsed=parsed, normalized_event=normalized_event)
    if override_root and override_root != str(parsed.get("root_topic_id") or ""):
        original_root = str(parsed.get("root_topic_id") or "")
        parsed["contextual_validation_original_root_topic_id"] = original_root
        parsed["contextual_validation_override_root_topic_id"] = override_root
        parsed["contextual_competing_roots"] = [row for row in (parsed.get("contextual_competing_roots") or []) if str(row.get("root_topic_id") or "") != override_root]
        if original_root in ROOT_BY_ID:
            parsed["contextual_competing_roots"] = _append_contextual_competing_root(parsed.get("contextual_competing_roots") or [], root_topic_id=original_root, reason_he="שורש מתחרה הוא שורש המודל המקורי לפני אימות הפעולה המרכזית.")
        parsed["root_topic_id"] = override_root
        parsed["rationale_he"] = _contextual_override_rationale(parsed=parsed, normalized_event=normalized_event, override_root=override_root, original_root=original_root)
    if indexability_status in CONTEXTUAL_NON_INDEXABLE_STATUSES and parsed.get("is_topic_bearing") is not True and _contextual_non_indexable_has_topic_evidence(item=item, parsed=parsed, normalized_event=normalized_event):
        parsed["is_topic_bearing"] = True
    consistency_reason = _contextual_event_consistency_reason(parsed=parsed, normalized_event=normalized_event)
    if consistency_reason:
        parsed["contextual_consistency_warning"] = consistency_reason
    if indexability_status in CONTEXTUAL_NON_INDEXABLE_STATUSES and parsed.get("is_topic_bearing") is not True:
        parsed["is_topic_bearing"] = False
        parsed["root_topic_id"] = None
    return parsed


def _contextual_event_status(value: Any) -> str:
    status = str(value or "unknown").strip().lower()
    allowed = {"approved", "discussed", "deferred", "removed", "duplicate_reference", "fragment_only", "procedural_only", "vote_or_result_only", "no_municipal_action", "unknown"}
    return status if status in allowed else "unknown"


def _contextual_agenda_disposition(value: Any) -> str:
    status = _contextual_event_status(value)
    return status if status in {"approved", "discussed", "deferred", "removed"} else "unknown"


def _contextual_indexability_status(value: Any, *, legacy_event_status: Any = None) -> str:
    status = str(value or "").strip().lower()
    allowed = {"standalone_topic", "duplicate_reference", "evidence_fragment", "procedural_only", "insufficient_context", "unknown"}
    if status in allowed:
        return status
    legacy = _contextual_event_status(legacy_event_status)
    legacy_map = {
        "duplicate_reference": "duplicate_reference",
        "fragment_only": "evidence_fragment",
        "vote_or_result_only": "procedural_only",
        "procedural_only": "procedural_only",
        "no_municipal_action": "insufficient_context",
    }
    return legacy_map.get(legacy, "unknown")


def _contextual_classification_basis(value: Any) -> str:
    basis = str(value or "unclear").strip().lower()
    return basis if basis in {"primary_action", "service_domain", "governance_status", "unclear"} else "unclear"


def _contextual_competing_roots(value: Any) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        root_topic_id = str(row.get("root_topic_id") or "").strip()
        if root_topic_id not in ROOT_BY_ID or root_topic_id in seen:
            continue
        seen.add(root_topic_id)
        out.append(
            {
                "root_topic_id": root_topic_id,
                "root_label_he": root_label_for_id(root_topic_id),
                "confidence": _optional_confidence(row.get("confidence")),
                "reason_he": _compact(row.get("reason_he") or row.get("rationale_he") or row.get("reason"))[:240] or None,
            }
        )
    return out[:4]


def _contextual_standalone_event(normalized_event: dict[str, Any]) -> bool | None:
    if "standalone_event" in normalized_event:
        return bool(normalized_event.get("standalone_event"))
    if "is_standalone_topic" in normalized_event:
        return bool(normalized_event.get("is_standalone_topic"))
    return None


def _contextual_event_consistency_reason(*, parsed: dict[str, Any], normalized_event: dict[str, Any]) -> str | None:
    status = _contextual_indexability_status(parsed.get("contextual_indexability_status") or parsed.get("indexability_status") or normalized_event.get("indexability_status"), legacy_event_status=parsed.get("event_status") or normalized_event.get("event_status"))
    model_topic = parsed.get("is_topic_bearing") is True
    if model_topic and status in CONTEXTUAL_NON_INDEXABLE_STATUSES and not _contextual_has_separate_action(normalized_event):
        return f"topic_bearing_with_non_indexable_status:{status}"
    return None


def _contextual_has_separate_action(normalized_event: dict[str, Any]) -> bool:
    action = _compact(normalized_event.get("municipal_action_he") or normalized_event.get("primary_action_he"))
    subject = _compact(normalized_event.get("semantic_subject_he") or normalized_event.get("clean_subject_he"))
    return bool(action and subject)


def _contextual_non_indexable_has_topic_evidence(*, item: dict[str, Any], parsed: dict[str, Any], normalized_event: dict[str, Any]) -> bool:
    root_topic_id = str(parsed.get("root_topic_id") or "")
    if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
        return False
    subject = _compact(normalized_event.get("clean_subject_he") or normalized_event.get("semantic_subject_he") or parsed.get("clean_subject_he") or parsed.get("topic_subject_he") or item.get("topic_subject_he"))
    if not subject:
        return False
    root_label = root_label_for_id(root_topic_id) or ""
    if root_label and _norm(subject) == _norm(root_label):
        return False
    if _compact(normalized_event.get("municipal_action_he") or normalized_event.get("primary_action_he") or parsed.get("primary_action_he")):
        return True
    policy_id = _compact(parsed.get("policy_id"))
    if policy_id and any(str(match.get("root_topic_id") or "") == root_topic_id and str(match.get("policy_id") or "") == policy_id for match in item.get("topic_policy_matches") or []):
        return True
    for candidate in item.get("root_topic_candidates") or []:
        if str(candidate.get("root_topic_id") or "") == root_topic_id and float(candidate.get("score") or 0.0) >= 0.72:
            return True
    return _optional_confidence(parsed.get("confidence")) is not None and float(parsed.get("confidence") or 0.0) >= 0.9 and bool(item.get("is_topic_bearing"))


def _contextual_duplicate_reference_supported(*, item: dict[str, Any], parsed: dict[str, Any], normalized_event: dict[str, Any]) -> bool:
    if _compact(parsed.get("duplicate_of_unit_id") or normalized_event.get("duplicate_of_unit_id")):
        return True
    quote = _compact(parsed.get("duplicate_evidence_quote_he") or normalized_event.get("duplicate_evidence_quote_he"))
    if quote and _quote_supported_by_context(item=item, quote=quote):
        return True
    return False


def _quote_supported_by_context(*, item: dict[str, Any], quote: str) -> bool:
    normalized_quote = _quote_match_norm(quote)
    if not normalized_quote:
        return False
    context = item.get("contextual_event_context") if isinstance(item.get("contextual_event_context"), dict) else {}
    text = _quote_match_norm(_join_unique([item.get("raw_text"), item.get("topic_identification_context"), item.get("topic_headline_he"), json.dumps(context, ensure_ascii=False)[:4000]]))
    return bool(text and normalized_quote in text)


def _contextual_has_bounded_topic_subject(*, item: dict[str, Any], parsed: dict[str, Any], normalized_event: dict[str, Any]) -> bool:
    subject = _compact(normalized_event.get("clean_subject_he") or normalized_event.get("semantic_subject_he") or parsed.get("clean_subject_he") or parsed.get("topic_subject_he") or item.get("topic_subject_he"))
    if not subject:
        return False
    root_label = root_label_for_id(str(parsed.get("root_topic_id") or "")) or ""
    return not (root_label and _norm(subject) == _norm(root_label))


def _augment_contextual_competing_roots(*, item: dict[str, Any], parsed: dict[str, Any], normalized_event: dict[str, Any], roots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = list(roots)
    selected_root = str(parsed.get("root_topic_id") or "")
    action_text = _join_unique([parsed.get("primary_action_he"), normalized_event.get("primary_action_he"), parsed.get("topic_subject_he"), parsed.get("clean_subject_he"), normalized_event.get("clean_subject_he")])
    action_root = _contextual_action_root_from_text(action_text)
    domain_text = _join_unique([parsed.get("service_domain_he"), normalized_event.get("service_domain_he")])
    domain_root = infer_root_topic_id(domain_text) if domain_text else None
    for root_topic_id, reason in [
        (action_root, "שורש מתחרה נגזר מהפעולה העירונית המרכזית."),
        (domain_root, "שורש מתחרה נגזר מתחום השירות או המדיניות."),
    ]:
        if root_topic_id and root_topic_id in ROOT_BY_ID and root_topic_id != selected_root:
            out = _append_contextual_competing_root(out, root_topic_id=root_topic_id, reason_he=reason)
    for candidate in item.get("root_topic_candidates") or []:
        root_topic_id = str(candidate.get("root_topic_id") or "")
        if root_topic_id in ROOT_BY_ID and root_topic_id != selected_root and float(candidate.get("score") or 0.0) >= 0.72:
            out = _append_contextual_competing_root(out, root_topic_id=root_topic_id, reason_he="שורש מתחרה הגיע ממועמד סיווג דטרמיניסטי חזק.", confidence=float(candidate.get("score") or 0.0))
    return out[:4]


def _append_contextual_competing_root(roots: list[dict[str, Any]], *, root_topic_id: str, reason_he: str, confidence: float | None = None) -> list[dict[str, Any]]:
    if any(str(row.get("root_topic_id") or "") == root_topic_id for row in roots):
        return roots
    return [
        *roots,
        {
            "root_topic_id": root_topic_id,
            "root_label_he": root_label_for_id(root_topic_id),
            "confidence": confidence,
            "reason_he": reason_he,
        },
    ]


def _contextual_override_rationale(*, parsed: dict[str, Any], normalized_event: dict[str, Any], override_root: str, original_root: str) -> str:
    action = _compact(parsed.get("primary_action_he") or normalized_event.get("primary_action_he") or normalized_event.get("municipal_action_he"))
    subject = _compact(parsed.get("topic_subject_he") or parsed.get("clean_subject_he") or normalized_event.get("clean_subject_he"))
    parts = [
        f"שכבת האימות בחרה בשורש {root_label_for_id(override_root) or override_root} לפי הפעולה העירונית המרכזית.",
    ]
    if original_root and original_root != override_root:
        parts.append(f"שורש המודל המקורי היה {root_label_for_id(original_root) or original_root}.")
    if action:
        parts.append(f"הפעולה המרכזית: {action}.")
    if subject:
        parts.append(f"הנושא: {subject}.")
    return _compact(" ".join(parts))[:500]


def _contextual_fallback_rationale(*, item: dict[str, Any], parsed: dict[str, Any], normalized_event: dict[str, Any]) -> str:
    root_topic_id = str(parsed.get("root_topic_id") or "")
    subject = _compact(parsed.get("topic_subject_he") or parsed.get("clean_subject_he") or normalized_event.get("clean_subject_he") or item.get("topic_subject_he"))
    action = _compact(parsed.get("primary_action_he") or normalized_event.get("primary_action_he") or normalized_event.get("municipal_action_he"))
    basis = _contextual_classification_basis(parsed.get("classification_basis") or normalized_event.get("classification_basis"))
    parts = [f"שורש {root_label_for_id(root_topic_id) or root_topic_id} נשמר לפי סיווג הקשרי תקין וביטחון מפורש."]
    if subject:
        parts.append(f"הנושא: {subject}.")
    if action:
        parts.append(f"הפעולה המרכזית: {action}.")
    if basis != "unclear":
        parts.append(f"בסיס הסיווג: {basis}.")
    return _compact(" ".join(parts))[:500]


def _contextual_validation_root_override(*, item: dict[str, Any], parsed: dict[str, Any], normalized_event: dict[str, Any]) -> str | None:
    if parsed.get("is_topic_bearing") is not True:
        return None
    model_root = str(parsed.get("root_topic_id") or "")
    if model_root not in ROOT_BY_ID:
        return None
    text = _join_unique(
        [
            normalized_event.get("primary_action_he"),
            normalized_event.get("service_domain_he"),
            normalized_event.get("clean_subject_he"),
            normalized_event.get("event_summary_he"),
            parsed.get("service_domain_he"),
            parsed.get("clean_subject_he"),
            parsed.get("topic_subject_he"),
            item.get("topic_headline_he"),
            item.get("topic_anchor_quote_he"),
        ]
    )
    committee_domain_root = _contextual_committee_domain_root_from_text(text)
    if committee_domain_root and committee_domain_root != model_root:
        return committee_domain_root
    action_root = _contextual_action_root_from_text(text)
    if action_root:
        return action_root if action_root != model_root else None
    domain_root = _contextual_service_domain_root_override(parsed=parsed, normalized_event=normalized_event, evidence_text=text)
    if domain_root and domain_root != model_root:
        return domain_root
    return None


def _contextual_service_domain_root_override(*, parsed: dict[str, Any], normalized_event: dict[str, Any], evidence_text: str) -> str | None:
    domain_text = _join_unique([parsed.get("service_domain_he"), normalized_event.get("service_domain_he")])
    if not domain_text:
        return None
    domain_root = infer_root_topic_id(domain_text, allow_procedural_default=False)
    if domain_root not in ROOT_BY_ID or domain_root in {"root_agenda_queries", "root_order_proposals"}:
        return None
    normalized = _norm(_join_unique([domain_text, evidence_text]))
    if not normalized:
        return None
    if domain_root == "root_education" and any(term in normalized for term in ("חינוך", "בית ספר", "בתי ספר", "בתי הספר", "ביה ס", "ביה\"ס", "כיתות", "תלמידים")):
        return domain_root
    for row in _allowed_root_alignment_scores(normalized):
        if str(row.get("root_topic_id") or "") == domain_root and float(row.get("score") or 0.0) >= 0.82 and row.get("matched_terms"):
            return domain_root
    return None


def _contextual_action_root_from_text(text: str) -> str | None:
    text = _compact(text)
    if not text:
        return None
    committee_domain_root = _contextual_committee_domain_root_from_text(text)
    if committee_domain_root:
        return committee_domain_root
    for match in topic_policy_matches(text, limit=3):
        root_topic_id = str(match.get("root_topic_id") or "")
        if root_topic_id in ACTION_DOMINANT_ROOT_IDS:
            return root_topic_id
    for root_topic_id in ("root_budget_finance", "root_agreements", "root_travel_approvals", "root_hr_labor", "root_allocations", "root_supports", "root_administration"):
        if _contextual_action_signal_supported(root_topic_id=root_topic_id, text=text):
            return root_topic_id
    canonical_subject, _ = canonicalize_topic_label(text, root_label_he="", evidence_text=text)
    inferred = infer_root_topic_id(canonical_subject or text)
    if inferred in ACTION_DOMINANT_ROOT_IDS and _contextual_action_signal_supported(root_topic_id=inferred, text=text):
        return inferred
    if _contextual_travel_action_signal(text):
        return "root_travel_approvals"
    return None


def _contextual_action_signal_supported(*, root_topic_id: str, text: str) -> bool:
    normalized = _norm(text)
    if root_topic_id == "root_travel_approvals":
        return _contextual_travel_action_signal(text)
    signal_terms = {
        "root_agreements": ("הסכם", "הסכמים", "רשות שימוש", "הרשאה", "התקשרות"),
        "root_budget_finance": ("תקציב", "קיצוץ", "גירעון", "גרעון", "תב ר", "תבר", "מימון", "כספים"),
        "root_hr_labor": ("שכר", "עובד", "עובדים", "העסקה", "תקן", "משרת אמון"),
        "root_allocations": ("הקצאה", "הקצאות", "מקרקעין", "חלקה", "גוש", "מגרש"),
        "root_supports": ("תמיכה", "תמיכות", "מלגה", "מלגות", "תבחין", "תבחינים", "תבחיני"),
        "root_administration": ("מינוי", "מינויים", "ועדה", "ועדת", "דירקטוריון", "מורשי חתימה", "חילופי גברי"),
    }
    return any(term in normalized for term in signal_terms.get(root_topic_id, ()))


def _contextual_travel_action_signal(text: str) -> bool:
    normalized = _norm(text)
    abroad = any(term in normalized for term in ("לחו ל", "לחו\"ל", "בחו ל", "בחו\"ל", "חו\"ל", "ארה ב", "ארה\"ב", "ארהב")) or "חול" in set(_hebrew_tokens(normalized))
    explicit_travel = any(term in normalized for term in ("נסיעה", "נסיעות", "נסיעת", "דוח נסיעה", "הוצאות נסיעה", "משלחת"))
    exit_abroad = abroad and any(term in normalized for term in ("יציאה", "יציאת", "פרטי יציאה"))
    return explicit_travel or exit_abroad


def _contextual_committee_domain_root_from_text(text: str) -> str | None:
    normalized = _norm(text)
    if not normalized:
        return None
    committee_terms = ("ועדה", "וועדה", "ועדת", "וועדת", "ועדות", "דירקטוריון", "דירקטוריונים")
    if not any(term in normalized for term in committee_terms):
        return None
    governance_terms = ("מינוי", "מינויים", "ממנה", "שינוי", "שינויים", "הרכב", "כהונה", "חבר", "חברים", "נציג", "נציגים", "חילופי גברי")
    if any(term in normalized for term in governance_terms):
        return None
    for match in topic_policy_matches(text, limit=5):
        root_topic_id = str(match.get("root_topic_id") or "")
        if root_topic_id in ROOT_BY_ID and root_topic_id not in {"root_administration", "root_agenda_queries", "root_order_proposals"}:
            return root_topic_id
    inferred = infer_root_topic_id(text)
    if inferred in ROOT_BY_ID and inferred not in {"root_administration", "root_agenda_queries", "root_order_proposals"}:
        return inferred
    return None


def _missing_contextual_normalization_fields(parsed: dict[str, Any]) -> list[str]:
    required = ["structure_unit_id", "standalone_event", "agenda_disposition", "event_status", "indexability_status", "is_standalone_topic", "agenda_status_he", "agenda_carrier_he", "municipal_action_he", "semantic_subject_he", "primary_action_he", "service_domain_he", "classification_basis", "clean_subject_he", "event_summary_he", "supporting_quote_he", "confidence"]
    return [field for field in required if field not in parsed]


def _missing_contextual_classification_fields(parsed: dict[str, Any]) -> list[str]:
    rows = _contextual_classification_rows(parsed)
    if not rows or not isinstance(rows[0], dict):
        return ["assignments"]
    row = rows[0]
    required = ["structure_unit_id", "root_topic_id", "is_topic_bearing"]
    missing = [field for field in required if field not in row]
    if row.get("is_topic_bearing") is True and not str(row.get("root_topic_id") or "").strip():
        missing.append("root_topic_id_for_topic_bearing_row")
    if row.get("is_topic_bearing") is True and not _compact(row.get("topic_subject_he") or row.get("clean_subject_he")):
        missing.append("subject_for_topic_bearing_row")
    return missing


def _contextual_classification_rows(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    rows = parsed.get("assignments") if isinstance(parsed.get("assignments"), list) else None
    if rows is not None:
        return [row for row in rows if isinstance(row, dict)]
    if isinstance(parsed.get("assignments"), dict):
        return [parsed["assignments"]]
    if {"structure_unit_id", "root_topic_id", "is_topic_bearing"}.issubset(parsed.keys()):
        return [parsed]
    return []


def _missing_contextual_classification_audit_fields(row: dict[str, Any]) -> list[str]:
    required = ["agenda_disposition", "event_status", "indexability_status", "classification_basis", "confidence", "rationale_he", "why_not_other_roots_he", "topic_supporting_quote_he", "competing_roots"]
    return [field for field in required if field not in row]


def _ensure_contextual_classification_defaults(*, item: dict[str, Any], parsed: dict[str, Any], normalized_event: dict[str, Any]) -> dict[str, Any]:
    current = {key: value for key, value in parsed.items() if key != "raw_payload"}
    current.setdefault("structure_unit_id", item.get("structure_unit_id"))
    current["agenda_disposition"] = _contextual_agenda_disposition(current.get("agenda_disposition") or normalized_event.get("agenda_disposition") or normalized_event.get("event_status"))
    current["event_status"] = _contextual_agenda_disposition(current.get("event_status") or current.get("agenda_disposition") or normalized_event.get("event_status"))
    current["indexability_status"] = _contextual_indexability_status(current.get("indexability_status") or normalized_event.get("indexability_status"), legacy_event_status=current.get("event_status") or normalized_event.get("event_status"))
    current["classification_basis"] = _contextual_classification_basis(current.get("classification_basis") or normalized_event.get("classification_basis"))
    current.setdefault("agenda_carrier_he", normalized_event.get("agenda_carrier_he"))
    current.setdefault("primary_action_he", normalized_event.get("primary_action_he") or normalized_event.get("municipal_action_he"))
    current.setdefault("service_domain_he", normalized_event.get("service_domain_he"))
    if "is_topic_bearing" not in current and current["indexability_status"] in {"standalone_topic", "unknown"} and str(current.get("root_topic_id") or "") in ROOT_BY_ID:
        current["is_topic_bearing"] = True
    subject = _compact(current.get("clean_subject_he") or current.get("topic_subject_he") or normalized_event.get("clean_subject_he") or normalized_event.get("semantic_subject_he") or item.get("topic_subject_he"))
    if subject:
        current.setdefault("topic_subject_he", subject)
        current.setdefault("clean_subject_he", subject)
    if not _compact(current.get("topic_supporting_quote_he")):
        current["topic_supporting_quote_he"] = normalized_event.get("supporting_quote_he") or _contextual_quote_fallback(item)
    current.setdefault("competing_roots", [])
    current.setdefault("why_not_other_roots_he", None)
    if not _compact(current.get("rationale_he")) and str(current.get("root_topic_id") or "") in ROOT_BY_ID and current.get("is_topic_bearing") is True:
        current["rationale_he"] = _contextual_fallback_rationale(item=item, parsed=current, normalized_event=normalized_event)
        current["contextual_rationale_fallback"] = True
    return current


def _ensure_contextual_event_defaults(*, item: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    current = {key: value for key, value in parsed.items() if key != "raw_payload"}
    fallback = _fallback_contextual_event(item=item, reason="missing_fields")
    for key, value in fallback.items():
        current.setdefault(key, value)
    if not _compact(current.get("clean_subject_he")) and bool(item.get("is_topic_bearing")):
        current["clean_subject_he"] = _compact(item.get("topic_subject_he") or item.get("topic_headline_he") or item.get("topic_identification_context"))[:180] or None
    current["agenda_disposition"] = _contextual_agenda_disposition(current.get("agenda_disposition") or current.get("event_status"))
    current["event_status"] = current["agenda_disposition"]
    current["indexability_status"] = _contextual_indexability_status(current.get("indexability_status"), legacy_event_status=current.get("event_status"))
    if current["indexability_status"] == "unknown" and bool(item.get("is_topic_bearing")):
        current["indexability_status"] = "standalone_topic"
    current["classification_basis"] = _contextual_classification_basis(current.get("classification_basis"))
    current.setdefault("standalone_event", current.get("is_standalone_topic"))
    current.setdefault("is_standalone_topic", current.get("standalone_event"))
    current.setdefault("municipal_action_he", current.get("primary_action_he"))
    current.setdefault("semantic_subject_he", current.get("clean_subject_he"))
    if not _compact(current.get("event_summary_he")):
        current["event_summary_he"] = _compact(current.get("clean_subject_he") or item.get("topic_identification_context") or item.get("raw_text"))[:260] or None
    if not _compact(current.get("supporting_quote_he")):
        current["supporting_quote_he"] = _contextual_quote_fallback(item)
    return current


def _fallback_contextual_event(*, item: dict[str, Any], reason: str) -> dict[str, Any]:
    subject = _compact(item.get("topic_subject_he") or item.get("topic_headline_he") or item.get("topic_identification_context"))[:220]
    text = _join_unique([subject, item.get("topic_anchor_quote_he"), item.get("raw_text")])
    return {
        "structure_unit_id": item.get("structure_unit_id"),
        "standalone_event": bool(item.get("is_topic_bearing")) and str(item.get("row_type") or "") == "topic_item",
        "agenda_disposition": "unknown",
        "event_status": "unknown",
        "indexability_status": "standalone_topic" if bool(item.get("is_topic_bearing")) and str(item.get("row_type") or "") == "topic_item" else "insufficient_context",
        "is_standalone_topic": bool(item.get("is_topic_bearing")) and str(item.get("row_type") or "") == "topic_item",
        "agenda_status_he": None,
        "agenda_carrier_he": item.get("agenda_carrier_he"),
        "municipal_action_he": None,
        "semantic_subject_he": subject or None,
        "primary_action_he": None,
        "service_domain_he": None,
        "classification_basis": "unclear",
        "clean_subject_he": subject or None,
        "event_summary_he": subject or _compact(item.get("raw_text"))[:220] or None,
        "supporting_quote_he": _contextual_quote_fallback(item),
        "context_notes_he": f"fallback_contextual_event:{reason}",
        "confidence": 0.25 if not text else 0.45,
    }


def _contextual_quote_fallback(item: dict[str, Any]) -> str:
    return _compact(item.get("topic_anchor_quote_he") or item.get("raw_text") or item.get("unit_raw_text") or item.get("topic_identification_context"))[:500]


def _contextual_event_text(*, item: dict[str, Any], normalized_event: dict[str, Any]) -> str:
    contextual_context = item.get("contextual_event_context") if isinstance(item.get("contextual_event_context"), dict) else {}
    event_block = contextual_context.get("event_block") if isinstance(contextual_context.get("event_block"), dict) else {}
    return _join_unique(
        [
            normalized_event.get("clean_subject_he"),
            normalized_event.get("semantic_subject_he"),
            normalized_event.get("event_summary_he"),
            normalized_event.get("municipal_action_he"),
            normalized_event.get("primary_action_he"),
            normalized_event.get("service_domain_he"),
            normalized_event.get("supporting_quote_he"),
            item.get("topic_headline_he"),
            item.get("topic_anchor_quote_he"),
            str(event_block.get("event_block_text") or "")[:900],
        ]
    )


def _contextual_policy_matches(*, item: dict[str, Any], normalized_event: dict[str, Any]) -> list[dict[str, Any]]:
    text = _contextual_event_text(item=item, normalized_event=normalized_event)
    matches = list(item.get("topic_policy_matches") or [])
    matches.extend(topic_policy_matches(text, limit=5))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in matches:
        policy_id = str(match.get("policy_id") or "")
        root_topic_id = str(match.get("root_topic_id") or "")
        key = f"{policy_id}:{root_topic_id}"
        if not policy_id or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "policy_id": policy_id,
                "root_topic_id": root_topic_id,
                "root_label_he": root_label_for_id(root_topic_id),
                "description_he": match.get("description_he"),
                "matched_terms": match.get("matched_terms") or [],
            }
        )
    return out[:6]


def _contextual_allowed_root_topics(*, topic_tree: dict[str, Any], item: dict[str, Any], normalized_event: dict[str, Any]) -> list[dict[str, Any]]:
    relevant_root_ids = _contextual_relevant_root_ids(item=item, normalized_event=normalized_event)
    rows = topic_tree.get("root_topics") or topic_tree.get("roots") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        root_topic_id = str(row.get("root_topic_id") or "")
        if root_topic_id not in ROOT_BY_ID:
            continue
        payload = {
            "root_topic_id": root_topic_id,
            "root_label_he": row.get("root_label_he") or root_label_for_id(root_topic_id),
            "keywords": [str(value) for value in (row.get("keywords") or [])[:8] if str(value).strip()],
        }
        if root_topic_id in relevant_root_ids:
            payload["child_examples"] = _contextual_child_examples(root_topic_id=root_topic_id, topic_tree=topic_tree, evidence_text=_contextual_event_text(item=item, normalized_event=normalized_event))
        out.append(payload)
    return out


def _contextual_relevant_root_ids(*, item: dict[str, Any], normalized_event: dict[str, Any]) -> set[str]:
    text = _contextual_event_text(item=item, normalized_event=normalized_event)
    relevant: set[str] = set()
    for candidate in item.get("root_topic_candidates") or []:
        root_topic_id = str(candidate.get("root_topic_id") or "")
        if root_topic_id in ROOT_BY_ID:
            relevant.add(root_topic_id)
    for child in item.get("candidate_child_topics") or []:
        root_topic_id = str(child.get("root_topic_id") or "")
        if root_topic_id in ROOT_BY_ID:
            relevant.add(root_topic_id)
    inferred = infer_root_topic_id(text)
    if inferred in ROOT_BY_ID:
        relevant.add(inferred)
    for match in _contextual_policy_matches(item=item, normalized_event=normalized_event):
        root_topic_id = str(match.get("root_topic_id") or "")
        if root_topic_id in ROOT_BY_ID:
            relevant.add(root_topic_id)
    for score in _allowed_root_alignment_scores(text)[:4]:
        root_topic_id = str(score.get("root_topic_id") or "")
        if root_topic_id in ROOT_BY_ID:
            relevant.add(root_topic_id)
    return relevant


def _contextual_child_examples(*, root_topic_id: str, topic_tree: dict[str, Any], evidence_text: str) -> list[dict[str, Any]]:
    normalized = _norm(evidence_text)
    examples: list[dict[str, Any]] = []
    roots = topic_tree.get("root_topics") or topic_tree.get("roots") or []
    children: list[dict[str, Any]] = []
    for row in roots:
        if str(row.get("root_topic_id") or "") == root_topic_id:
            children.extend([child for child in row.get("children") or [] if isinstance(child, dict)])
            break
    if not children:
        children = [row for row in CURATED_V4_CHILD_TOPICS if str(row.get("root_topic_id") or "") == root_topic_id]
    scored: list[tuple[int, dict[str, Any]]] = []
    for child in children:
        labels = [str(child.get("child_label_he") or child.get("label_he") or ""), *[str(alias) for alias in child.get("aliases_he") or []]]
        score = sum(1 for label in labels if label and _norm(label) and _norm(label) in normalized)
        scored.append((score, child))
    scored.sort(key=lambda row: row[0], reverse=True)
    for _, child in scored[:5]:
        label = str(child.get("child_label_he") or child.get("label_he") or "")
        if not label:
            continue
        examples.append({"child_label_he": label, "aliases_he": [str(value) for value in (child.get("aliases_he") or [])[:4] if str(value).strip()]})
    return examples


def _assignments_from_response(*, response: dict[str, Any], items_by_id: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_rows = response.get("assignments") if isinstance(response.get("assignments"), list) else []
    parsed_by_id = {str(row.get("structure_unit_id") or ""): row for row in raw_rows if isinstance(row, dict) and str(row.get("structure_unit_id") or "")}
    assignments = [_assignment_from_parsed(item=item, parsed=parsed_by_id[unit_id]) for unit_id, item in items_by_id.items() if unit_id in parsed_by_id]
    omitted_items = [item for unit_id, item in items_by_id.items() if unit_id not in parsed_by_id]
    return assignments, omitted_items


def _assignment_from_parsed(*, item: dict[str, Any], parsed: dict[str, Any] | None) -> dict[str, Any]:
    if not parsed:
        return _fallback_assignment(item, reason="model_omitted_unit")
    text = str(item.get("raw_text") or "")
    topic_text = _topic_basis_text(item)
    shape_text = _shape_guard_raw_text(item=item, topic_text=topic_text, fallback_text=text)
    if _parsed_declares_non_topic(parsed) or _non_topic_protocol_reason(headline=topic_text, raw_text=shape_text, structural_role=str(item.get("structural_role") or ""), packet_role=str((item.get("document_context") or {}).get("packet_role") or "")):
        return _non_topic_assignment(item, reason="model_or_shape_non_topic")
    parsed_subject = clean_protocol_subject_text(parsed.get("clean_subject_he") or parsed.get("topic_subject_he"))
    if parsed_subject and _parsed_subject_unsupported_in_protocol_listing(item=item, parsed_subject=parsed_subject, parsed=parsed):
        return _non_topic_assignment(item, reason="unsupported_model_subject_in_protocol_listing")
    if _is_protocol_item(item) and parsed_subject:
        topic_text = parsed_subject
    dicta_authoritative = str(item.get("dicta_mode") or "") == "dicta_authoritative"
    dicta_contextual = str(item.get("dicta_mode") or "") == "dicta_contextual"
    dicta_contextual_trusted = dicta_contextual and _contextual_dicta_root_trusted(item=item, parsed=parsed)
    dicta_root_is_final = dicta_authoritative or dicta_contextual_trusted
    raw_dicta_root_topic_id = str(parsed.get("root_topic_id") or "").strip()
    dicta_root_topic_id = raw_dicta_root_topic_id if raw_dicta_root_topic_id in ROOT_BY_ID else None
    if raw_dicta_root_topic_id and dicta_root_topic_id is None:
        dicta_root_topic_id = _align_unknown_dicta_root(item=item, parsed=parsed, subject=topic_text or text)
        if dicta_root_topic_id is None and not (dicta_authoritative or dicta_contextual):
            return _unknown_root_candidate_assignment(item=item, parsed=parsed, raw_dicta_root_topic_id=raw_dicta_root_topic_id, subject=topic_text or text)
    fallback_root_topic_id = infer_root_topic_id(topic_text or text, allow_procedural_default=not (_is_protocol_item(item) and bool(item.get("is_topic_bearing"))))
    if not dicta_root_is_final and _is_protocol_item(item) and bool(item.get("is_topic_bearing")) and dicta_root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
        recovered_root = _recover_root_only_topic(item=item, parsed=parsed, subject=topic_text or text, fallback_root_topic_id=fallback_root_topic_id)
        if recovered_root:
            dicta_root_topic_id = recovered_root
    contextual_root_candidate_reason = _contextual_valid_root_candidate_reason(parsed=parsed, subject=topic_text or text) if dicta_contextual and not dicta_contextual_trusted else None
    dicta_root_for_adjudication = dicta_root_topic_id if not (dicta_contextual and not dicta_contextual_trusted and not contextual_root_candidate_reason) else None
    if dicta_root_is_final:
        root_adjudication = _dicta_authoritative_root_adjudication(item=item, parsed=parsed, subject=topic_text or text, dicta_root_topic_id=dicta_root_topic_id, raw_dicta_root_topic_id=raw_dicta_root_topic_id, fallback_root_topic_id=fallback_root_topic_id)
        if dicta_contextual_trusted:
            root_adjudication = {**root_adjudication, "decision": str(root_adjudication.get("decision") or "dicta_authoritative_root").replace("dicta_authoritative_root", "dicta_contextual_root")}
    else:
        root_adjudication = _adjudicate_assignment_root(item=item, parsed=parsed, subject=topic_text or text, dicta_root_topic_id=dicta_root_for_adjudication, fallback_root_topic_id=fallback_root_topic_id)
    if dicta_root_topic_id and dicta_root_topic_id not in {raw_dicta_root_topic_id, None}:
        root_adjudication = {**root_adjudication, "decision": f"{root_adjudication.get('decision') or 'root_adjudicated'}:procedural_root_recovered"}
    if raw_dicta_root_topic_id and raw_dicta_root_topic_id not in ROOT_BY_ID:
        root_adjudication = {**root_adjudication, "unknown_dicta_root_topic_id": raw_dicta_root_topic_id, "decision": f"{root_adjudication.get('decision') or 'root_adjudicated'}:unknown_dicta_root_aligned" if dicta_root_topic_id else f"{root_adjudication.get('decision') or 'root_adjudicated'}:unknown_dicta_root_unresolved"}
    root_topic_id = str(root_adjudication.get("root_topic_id") or "")
    if root_topic_id not in ROOT_BY_ID:
        root_topic_id = fallback_root_topic_id if fallback_root_topic_id in ROOT_BY_ID else "root_agenda_queries"
        root_adjudication = {**root_adjudication, "root_topic_id": root_topic_id, "decision": f"{root_adjudication.get('decision') or 'root_adjudication'}:invalid_root_fallback"}
    root_label = root_label_for_id(root_topic_id) or ""
    contextual_warning = _compact(parsed.get("contextual_consistency_warning")) if dicta_contextual else ""
    if contextual_warning and bool(parsed.get("is_topic_bearing")):
        parsed_contract = _parsed_topic_contract(parsed)
        quote = _grounded_quote(item=item, parsed_quote=_compact(parsed.get("topic_supporting_quote_he")), fallback_text=text)
        return _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=None, raw_child_label=None, status="candidate", reject_reason=f"non_blocking_topic_review:{contextual_warning}", aliases=[], confidence=_confidence(parsed.get("confidence")), quote=quote, route=f"dictalm_v4_global_tree:contextual_consistency_review:{contextual_warning}", rationale_he=_compact(parsed.get("rationale_he"))[:180] or contextual_warning, parsed_contract=parsed_contract, root_adjudication=root_adjudication)
    agenda_review_reason = _contextual_agenda_review_reason(parsed) if dicta_contextual else None
    if agenda_review_reason and bool(parsed.get("is_topic_bearing")):
        parsed_contract = _parsed_topic_contract(parsed)
        quote = _grounded_quote(item=item, parsed_quote=_compact(parsed.get("topic_supporting_quote_he")), fallback_text=text)
        return _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=None, raw_child_label=None, status="candidate", reject_reason=f"non_blocking_topic_review:{agenda_review_reason}", aliases=[], confidence=min(0.71, max(0.25, _confidence(parsed.get("confidence")))), quote=quote, route=f"dictalm_v4_global_tree:contextual_agenda_review:{agenda_review_reason}", rationale_he=_compact(parsed.get("rationale_he"))[:180] or agenda_review_reason, parsed_contract=parsed_contract, root_adjudication=root_adjudication)
    if contextual_root_candidate_reason and dicta_root_topic_id in ROOT_BY_ID:
        root_topic_id = dicta_root_topic_id
        root_label = root_label_for_id(root_topic_id) or root_label
        activate_contextual_candidate = _contextual_root_candidate_has_strong_evidence(
            item=item,
            parsed=parsed,
            root_topic_id=root_topic_id,
            reason=contextual_root_candidate_reason,
        )
        root_adjudication = {
            **root_adjudication,
            "root_topic_id": root_topic_id,
            "dicta_root_topic_id": root_topic_id,
            "dicta_root_label_he": root_label,
            "adjudicated_root_label_he": root_label,
            "decision": f"dicta_contextual_root_{'recovered' if activate_contextual_candidate else 'candidate'}:{contextual_root_candidate_reason}",
        }
        if not activate_contextual_candidate:
            parsed_contract = _parsed_topic_contract(parsed)
            quote = _grounded_quote(item=item, parsed_quote=_compact(parsed.get("topic_supporting_quote_he")), fallback_text=text)
            return _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=None, raw_child_label=None, status="candidate", reject_reason=f"non_blocking_topic_review:{contextual_root_candidate_reason}", aliases=[], confidence=min(0.71, max(0.25, _confidence(parsed.get("confidence")))), quote=quote, route=f"dictalm_v4_global_tree:contextual_root_candidate:{contextual_root_candidate_reason}", rationale_he=_compact(parsed.get("rationale_he"))[:180] or contextual_root_candidate_reason, parsed_contract=parsed_contract, root_adjudication=root_adjudication)
    conservative_review = None if dicta_root_is_final else _dicta_assignment_conservative_review_reason(item=item, root_adjudication=root_adjudication)
    if conservative_review:
        return _review_assignment(item=item, root_topic_id=root_topic_id, root_label=root_label, reason=conservative_review)
    quote = _grounded_quote(item=item, parsed_quote=_compact(parsed.get("topic_supporting_quote_he")), fallback_text=text)
    # Root Dicta is intentionally not trusted for child selection. Child labels
    # are attached only by deterministic same-root candidates or the post-root
    # child-only judge, so a child can never change the root chosen above.
    selected_candidate = None
    selected_candidate_matched_by_label = False
    raw_child = None
    validation_evidence = _validation_evidence_text(item=item, quote=quote, selected_candidate=selected_candidate)
    validation = validate_child_label(raw_label=raw_child, root_label_he=root_label, evidence_text=validation_evidence, selected_existing=bool(selected_candidate), structural_role=str(item.get("structural_role") or "")) if raw_child else None
    if validation is None:
        selected_candidate = _best_high_confidence_candidate(item=item, root_topic_id=root_topic_id)
        raw_child = clean_topic_label((selected_candidate or {}).get("label_he"))
        validation_evidence = _validation_evidence_text(item=item, quote=quote, selected_candidate=selected_candidate)
        validation = validate_child_label(raw_label=raw_child, root_label_he=root_label, evidence_text=validation_evidence, selected_existing=bool(selected_candidate), structural_role=str(item.get("structural_role") or "")) if raw_child else None
    resolved = resolve_child_topic_assignment(root_topic_id=root_topic_id, root_label_he=root_label, child_label_he=validation.cleaned_label if validation and validation.status == "active" else raw_child, evidence_text=validation_evidence, structural_role=str(item.get("structural_role") or ""), selected_existing=bool(selected_candidate))
    authoritative_root_topic_id = str(root_adjudication.get("root_topic_id") or "")
    if dicta_root_is_final and str(resolved["root_topic_id"]) != authoritative_root_topic_id:
        root_topic_id = authoritative_root_topic_id
        root_label = root_label_for_id(root_topic_id) or root_label
        child_label = None
        raw_child = None
        status = "active"
        route = f"dictalm_v4_global_tree:{root_adjudication.get('decision') or 'dicta_authoritative_root'}:root_only:child_root_change_ignored"
        aliases = []
        parsed_contract = _parsed_topic_contract(parsed)
        return _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=child_label, raw_child_label=raw_child, status=status, reject_reason=None, aliases=aliases, confidence=_confidence(parsed.get("confidence")), quote=quote, route=route, rationale_he=_compact(parsed.get("rationale_he"))[:180], parsed_contract=parsed_contract, root_adjudication=root_adjudication)
    root_topic_id = str(resolved["root_topic_id"])
    root_label = str(resolved["root_label_he"])
    if root_topic_id != authoritative_root_topic_id:
        root_adjudication = {
            **root_adjudication,
            "resolved_root_topic_id": root_topic_id,
            "resolved_root_label_he": root_label,
            "decision": f"{root_adjudication.get('decision') or 'root_adjudication'}:{resolved.get('route_suffix') or 'resolved_root'}",
        }
    child_label = resolved.get("child_label_he")
    status = str(resolved.get("status") or "active")
    route = f"dictalm_v4_global_tree:{root_adjudication.get('decision') or 'root_adjudicated'}:{validation.route if validation else 'root_only'}"
    if selected_candidate and child_label:
        route_prefix = "matched_child_label" if selected_candidate_matched_by_label else "child_choice"
        route = f"dictalm_v4_global_tree:{root_adjudication.get('decision') or 'root_adjudicated'}:{route_prefix}:{selected_candidate.get('evidence_source') or validation.route}"
    if resolved.get("route_suffix") == "semantic_reparent":
        route = f"{route}:semantic_reparent"
    elif resolved.get("route_suffix") in {"procedural_root_child_demoted", "root_only"} or str(resolved.get("route_suffix") or "").startswith("child_demoted"):
        route = f"{route}:{resolved.get('route_suffix')}"
    if validation and validation.status == "rejected":
        route = f"dictalm_v4_global_tree:rejected:{validation.reason}"
    aliases = _dedupe_strings([*(resolved.get("aliases_he") or []), *(((selected_candidate or {}).get("aliases_he") or []) if selected_candidate else [])])
    if not dicta_root_is_final and _assignment_requires_subject_review(item=item, root_topic_id=root_topic_id, child_label=child_label):
        return _review_assignment(item=item, root_topic_id=root_topic_id, root_label=root_label, reason="topic_subject_collapsed_to_procedural_root")
    verified_root = None if dicta_root_is_final else _verified_body_root_override(item=item, root_topic_id=root_topic_id, root_adjudication=root_adjudication, child_label=child_label)
    if verified_root and verified_root != root_topic_id:
        root_topic_id = verified_root
        root_label = root_label_for_id(root_topic_id) or root_label
        child_label = None
        raw_child = None
        route = f"{route}:body_root_evidence_corrected"
        root_adjudication = {
            **root_adjudication,
            "root_topic_id": root_topic_id,
            "resolved_root_topic_id": root_topic_id,
            "resolved_root_label_he": root_label,
            "decision": f"{root_adjudication.get('decision') or 'root_adjudication'}:body_root_evidence_corrected",
        }
    parsed_contract = _parsed_topic_contract(parsed)
    if _parsed_subject_should_preserve_item_subject(parsed_contract=parsed_contract, item=item, root_label=root_label):
        parsed_contract["topic_subject_he"] = item.get("topic_subject_he")
    return _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=child_label, raw_child_label=raw_child, status=status, reject_reason=resolved.get("reason") or (validation.reason if validation else None), aliases=aliases, confidence=_confidence(parsed.get("confidence")), quote=quote, route=route, rationale_he=_compact(parsed.get("rationale_he"))[:180], parsed_contract=parsed_contract, root_adjudication=root_adjudication)


def _adjudicate_assignment_root(*, item: dict[str, Any], parsed: dict[str, Any], subject: str, dicta_root_topic_id: str | None, fallback_root_topic_id: str | None) -> dict[str, Any]:
    adjudication = adjudicate_root_topic(subject=subject, dicta_root_topic_id=dicta_root_topic_id, fallback_root_topic_id=fallback_root_topic_id)
    policy_matches = item.get("topic_policy_matches") or topic_policy_matches(subject, limit=3)
    governance_root = _committee_governance_root_override(subject=subject, item=item, adjudication=adjudication, dicta_root_topic_id=dicta_root_topic_id, fallback_root_topic_id=fallback_root_topic_id)
    if governance_root:
        adjudication = {
            **adjudication,
            "root_topic_id": governance_root,
            "decision": f"{adjudication.get('decision') or 'root_adjudicated'}:committee_governance_override",
        }
    first_item_policy = policy_matches[0] if policy_matches else None
    if first_item_policy and not adjudication.get("policy_id"):
        policy_root = str(first_item_policy.get("root_topic_id") or "")
        adjudicated_root = str(adjudication.get("root_topic_id") or "")
        if policy_root and policy_root == adjudicated_root:
            adjudication = {
                **adjudication,
                "policy_id": first_item_policy.get("policy_id"),
                "policy_description_he": first_item_policy.get("description_he"),
                "matched_terms": first_item_policy.get("matched_terms") or [],
                "decision": f"{adjudication.get('decision') or 'root_adjudicated'}:item_policy_preserved",
            }
    return {
        **adjudication,
        "dicta_policy_id": _compact(parsed.get("policy_id")) or None,
        "dicta_root_label_he": root_label_for_id(dicta_root_topic_id),
        "fallback_root_label_he": root_label_for_id(fallback_root_topic_id),
        "adjudicated_root_label_he": root_label_for_id(str(adjudication.get("root_topic_id") or "")),
        "policy_matches": policy_matches,
    }


def _dicta_authoritative_root_adjudication(*, item: dict[str, Any], parsed: dict[str, Any], subject: str, dicta_root_topic_id: str | None, raw_dicta_root_topic_id: str | None, fallback_root_topic_id: str | None) -> dict[str, Any]:
    policy_matches = item.get("topic_policy_matches") or topic_policy_matches(subject, limit=3)
    raw_root = str(raw_dicta_root_topic_id or "").strip() or None
    if dicta_root_topic_id in ROOT_BY_ID:
        decision = "dicta_authoritative_root"
        if raw_root and raw_root != dicta_root_topic_id:
            decision = f"{decision}:unknown_root_aligned"
        return {
            "root_topic_id": dicta_root_topic_id,
            "policy_id": None,
            "policy_description_he": None,
            "matched_terms": [],
            "decision": decision,
            "dicta_root_topic_id": dicta_root_topic_id,
            "dicta_root_label_he": root_label_for_id(dicta_root_topic_id),
            "fallback_root_topic_id": fallback_root_topic_id,
            "fallback_root_label_he": root_label_for_id(fallback_root_topic_id),
            "adjudicated_root_label_he": root_label_for_id(dicta_root_topic_id),
            "dicta_policy_id": _compact(parsed.get("policy_id")) or None,
            "unknown_dicta_root_topic_id": raw_root if raw_root and raw_root not in ROOT_BY_ID else None,
            "policy_matches": policy_matches,
        }
    adjudication = adjudicate_root_topic(subject=subject, dicta_root_topic_id=None, fallback_root_topic_id=fallback_root_topic_id)
    root_topic_id = str(adjudication.get("root_topic_id") or "")
    if root_topic_id not in ROOT_BY_ID:
        root_topic_id = fallback_root_topic_id if fallback_root_topic_id in ROOT_BY_ID else "root_agenda_queries"
    return {
        **adjudication,
        "root_topic_id": root_topic_id,
        "decision": f"{adjudication.get('decision') or 'fallback_root'}:dicta_authoritative_missing_valid_root",
        "dicta_root_topic_id": None,
        "dicta_root_label_he": None,
        "fallback_root_topic_id": fallback_root_topic_id,
        "fallback_root_label_he": root_label_for_id(fallback_root_topic_id),
        "adjudicated_root_label_he": root_label_for_id(root_topic_id),
        "dicta_policy_id": _compact(parsed.get("policy_id")) or None,
        "unknown_dicta_root_topic_id": raw_root if raw_root and raw_root not in ROOT_BY_ID else None,
        "policy_matches": policy_matches,
    }


def _dicta_assignment_conservative_review_reason(*, item: dict[str, Any], root_adjudication: dict[str, Any]) -> str | None:
    if not _is_protocol_item(item) or not bool(item.get("is_topic_bearing")):
        return None
    if root_adjudication.get("policy_id"):
        return None
    if "dicta_contextual_root_recovered" in str(root_adjudication.get("decision") or ""):
        return None
    if "committee_governance_override" in str(root_adjudication.get("decision") or ""):
        return None
    decision = item.get("deterministic_topic_decision") if isinstance(item.get("deterministic_topic_decision"), dict) else {}
    if not bool(decision.get("needs_dicta")):
        return None
    reason = str(decision.get("reason") or "ambiguous_candidate")
    if reason not in {"ambiguous_candidates", "weak_candidate"}:
        return None
    return f"dicta_conservative_review:{reason}"


def _contextual_dicta_root_trusted(*, item: dict[str, Any], parsed: dict[str, Any]) -> bool:
    root_topic_id = str(parsed.get("root_topic_id") or "")
    if root_topic_id not in ROOT_BY_ID:
        return False
    if bool(parsed.get("is_topic_bearing")) and root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
        return False
    if parsed.get("contextual_schema_valid") is not True:
        return False
    if _compact(parsed.get("contextual_consistency_warning")):
        return False
    confidence = _optional_confidence(parsed.get("confidence"))
    if confidence is None or confidence < 0.72:
        return False
    if not _compact(parsed.get("rationale_he")):
        return False
    if bool(parsed.get("is_topic_bearing")) and not _compact(parsed.get("clean_subject_he") or parsed.get("topic_subject_he")):
        return False
    return True


def _contextual_valid_root_candidate_reason(*, parsed: dict[str, Any], subject: str) -> str | None:
    root_topic_id = str(parsed.get("root_topic_id") or "")
    if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
        return None
    if parsed.get("is_topic_bearing") is not True:
        return None
    if _compact(parsed.get("contextual_consistency_warning")):
        return None
    action_root = _contextual_action_root_from_text(_join_unique([parsed.get("primary_action_he"), parsed.get("clean_subject_he"), parsed.get("topic_subject_he"), subject]))
    if action_root and action_root != root_topic_id:
        return None
    if parsed.get("contextual_schema_valid") is not True:
        return "dicta_contextual_incomplete_schema_root"
    confidence = _optional_confidence(parsed.get("confidence"))
    if confidence is None:
        return "dicta_contextual_missing_confidence_root"
    if confidence < 0.72:
        return "dicta_contextual_low_confidence_root"
    if not _compact(parsed.get("rationale_he")):
        return "dicta_contextual_missing_rationale_root"
    if not _compact(parsed.get("clean_subject_he") or parsed.get("topic_subject_he")):
        return "dicta_contextual_missing_subject_root"
    return None


def _contextual_root_candidate_has_strong_evidence(*, item: dict[str, Any], parsed: dict[str, Any], root_topic_id: str, reason: str) -> bool:
    if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
        return False
    if reason not in {"dicta_contextual_incomplete_schema_root", "dicta_contextual_missing_confidence_root"}:
        return False
    if parsed.get("is_topic_bearing") is not True:
        return False
    if _compact(parsed.get("contextual_consistency_warning")):
        return False
    subject = clean_protocol_subject_text(parsed.get("clean_subject_he") or parsed.get("topic_subject_he") or item.get("topic_subject_he"))
    if not subject:
        return False
    canonical_subject, canonical_reason = canonicalize_topic_label(subject, root_label_he=root_label_for_id(root_topic_id) or "", evidence_text=_join_unique([subject, item.get("raw_text"), parsed.get("topic_supporting_quote_he")]))
    if not canonical_subject or canonical_reason in {"unresolved_noisy_label", "procedural_or_dialogue_label"}:
        return False
    confidence = _optional_confidence(parsed.get("confidence"))
    if reason == "dicta_contextual_incomplete_schema_root" and confidence is not None and confidence >= 0.8:
        return True
    return _deterministic_decision_supports_root(item=item, root_topic_id=root_topic_id, min_confidence=0.9)


def _contextual_agenda_review_reason(parsed: dict[str, Any]) -> str | None:
    disposition = _contextual_agenda_disposition(parsed.get("contextual_agenda_disposition") or parsed.get("agenda_disposition") or parsed.get("contextual_event_status") or parsed.get("event_status"))
    indexability = _contextual_indexability_status(parsed.get("contextual_indexability_status") or parsed.get("indexability_status"), legacy_event_status=parsed.get("contextual_event_status") or parsed.get("event_status"))
    if disposition == "removed" and indexability in {"standalone_topic", "unknown"}:
        return "dicta_contextual_removed_agenda_topic"
    return None


def _committee_governance_root_override(*, subject: str, item: dict[str, Any], adjudication: dict[str, Any], dicta_root_topic_id: str | None, fallback_root_topic_id: str | None) -> str | None:
    current_root = str(adjudication.get("root_topic_id") or "")
    if current_root not in {"root_agreements", "root_administration"}:
        return None
    if current_root == "root_agreements" and fallback_root_topic_id != "root_administration" and not _root_candidate_has_admin_tie(item):
        return None
    text = _norm(_join_unique([subject, item.get("topic_headline_he"), item.get("topic_identification_context")]))
    if not text:
        return None
    committee_terms = ("ועדה", "וועדה", "ועדת", "וועדת", "ועדות", "דירקטוריון", "דירקטוריונים", "מועצה", "מליאה")
    governance_terms = ("מינוי", "מינויים", "ממנה", "שינוי", "שינויים", "הרכב", "כהונה", "חבר", "חברים", "נציג", "נציגים")
    if not any(term in text for term in committee_terms) or not any(term in text for term in governance_terms):
        return None
    procurement_action_terms = (
        "מכרז פומבי",
        "ביטול מכרז",
        "אישור מכרז",
        "פרסום מכרז",
        "תוצאות מכרז",
        "זוכה",
        "זוכים",
        "התקשרות עם",
        "אישור התקשרות",
        "אישור התקשרויות",
        "הסכם",
        "חוזה",
        "פטור ממכרז",
        "ספק",
        "ספקים",
    )
    if any(term in text for term in procurement_action_terms):
        return None
    return "root_administration"


def _root_candidate_has_admin_tie(item: dict[str, Any]) -> bool:
    scores: dict[str, float] = {}
    for candidate in item.get("root_topic_candidates") or []:
        root_topic_id = str(candidate.get("root_topic_id") or "")
        if root_topic_id not in {"root_agreements", "root_administration"}:
            continue
        scores[root_topic_id] = max(scores.get(root_topic_id, 0.0), float(candidate.get("score") or 0.0))
    admin_score = scores.get("root_administration", 0.0)
    agreements_score = scores.get("root_agreements", 0.0)
    return admin_score >= 0.72 and agreements_score >= 0.72 and abs(admin_score - agreements_score) <= 0.12


def _parsed_declares_non_topic(parsed: dict[str, Any]) -> bool:
    if "is_topic_bearing" not in parsed:
        return False
    if bool(parsed.get("is_topic_bearing")):
        return False
    root_topic_id = str(parsed.get("root_topic_id") or "").strip()
    return not root_topic_id or root_topic_id in {"root_agenda_queries", "root_order_proposals"}


def _recover_root_only_topic(*, item: dict[str, Any], parsed: dict[str, Any], subject: str, fallback_root_topic_id: str | None) -> str | None:
    """Recover substantive root-only topics when Dicta falls back to a carrier root.

    The recovery is intentionally root-only: it never creates/accepts a child topic,
    and it avoids procedural roots. This covers one-off real subjects while keeping
    child taxonomy maintenance separate from root classification.
    """
    if _non_topic_protocol_reason(headline=subject, raw_text=_shape_guard_raw_text(item=item, topic_text=subject, fallback_text=str(item.get("raw_text") or "")), structural_role=str(item.get("structural_role") or ""), packet_role=str((item.get("document_context") or {}).get("packet_role") or "")):
        return None
    for match in topic_policy_matches(subject, limit=3):
        root_topic_id = str(match.get("root_topic_id") or "")
        if root_topic_id in ROOT_BY_ID and root_topic_id not in {"root_agenda_queries", "root_order_proposals"}:
            return root_topic_id
    if fallback_root_topic_id in ROOT_BY_ID and fallback_root_topic_id not in {"root_agenda_queries", "root_order_proposals"}:
        return fallback_root_topic_id
    candidates = [row for row in item.get("root_topic_candidates") or [] if str(row.get("root_topic_id") or "") in ROOT_BY_ID and str(row.get("root_topic_id") or "") not in {"root_agenda_queries", "root_order_proposals"}]
    candidates.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
    if not candidates:
        return None
    best = candidates[0]
    score = float(best.get("score") or 0.0)
    matched_terms = [str(term) for term in best.get("matched_terms") or [] if str(term).strip()]
    if score >= 0.58 or (score >= 0.44 and matched_terms):
        return str(best.get("root_topic_id") or "")
    return None


def _align_unknown_dicta_root(*, item: dict[str, Any], parsed: dict[str, Any], subject: str) -> str | None:
    """Map an invented Dicta root to the closest allowed root using generic evidence.

    This is not a hardcoded root-pair repair. It reuses the allowed root keyword
    inference over Dicta's own rationale/quote plus bounded item context, and only
    accepts a concrete allowed root. Procedural carrier roots are not accepted for
    topic-bearing rows here because the caller already treats those as review cases.
    """
    alignment_text = _join_unique(
        [
            parsed.get("root_topic_id"),
            parsed.get("selected_allowed_root_label_he"),
            parsed.get("proposed_new_root_label_he"),
            _positive_alignment_text(parsed.get("rationale_he")),
            parsed.get("topic_supporting_quote_he"),
            parsed.get("topic_subject_he"),
            subject,
            item.get("topic_headline_he"),
        ]
    )
    if bool(parsed.get("needs_taxonomy_review")):
        return None
    for match in topic_policy_matches(alignment_text, limit=3):
        root_topic_id = str(match.get("root_topic_id") or "")
        if root_topic_id in ROOT_BY_ID and root_topic_id not in {"root_agenda_queries", "root_order_proposals"}:
            return root_topic_id
    scored = _allowed_root_alignment_scores(alignment_text)
    if _is_protocol_item(item) and bool(item.get("is_topic_bearing")):
        scored = [row for row in scored if str(row.get("root_topic_id") or "") not in {"root_agenda_queries", "root_order_proposals"}]
    if not scored:
        return None
    best = scored[0]
    second = scored[1] if len(scored) > 1 else {"score": 0.0}
    best_root = str(best.get("root_topic_id") or "")
    best_score = float(best.get("score") or 0.0)
    margin = best_score - float(second.get("score") or 0.0)
    # Only align invented roots when one allowed root is a clear semantic fit.
    # Otherwise preserve the unknown root as a candidate taxonomy gap.
    if best_score >= 0.82 and margin >= 0.14:
        return best_root
    return None


def _positive_alignment_text(value: Any) -> str:
    """Keep affirmative rationale sentences; negative comparisons can name false rival roots."""
    text = _compact(value)
    if not text:
        return ""
    negative_markers = ("אין ", "אינו", "אינה", "אינם", "לא ", "ללא ", "אחרים", "other", "not ", "without")
    parts = re.split(r"(?<=[.!?])\s+|[\n;]+", text)
    kept = [part for part in parts if part.strip() and not any(marker in part.casefold() for marker in negative_markers)]
    return _compact(" ".join(kept))


def _allowed_root_alignment_scores(text: str) -> list[dict[str, Any]]:
    normalized = _norm(text)
    if not normalized:
        return []
    out: list[dict[str, Any]] = []
    for root in V4_ROOT_TOPICS:
        root_topic_id = str(root.get("root_topic_id") or "")
        if root_topic_id not in ROOT_BY_ID:
            continue
        label = str(root.get("root_label_he") or root_label_for_id(root_topic_id) or "")
        scores = [_alignment_phrase_score(label, normalized) * 0.92]
        hits: list[str] = []
        profile = root.get("profile") if isinstance(root.get("profile"), dict) else {}
        phrases = [*(root.get("keywords") or []), *(profile.get("aliases_he") or []), *(profile.get("aliases_en") or []), profile.get("summary_he")]
        for keyword in phrases:
            score = _alignment_phrase_score(str(keyword or ""), normalized)
            if score > 0.0:
                scores.append(score)
                hits.append(str(keyword))
        best_score = max(scores or [0.0])
        if best_score <= 0.0:
            continue
        out.append({"root_topic_id": root_topic_id, "root_label_he": label, "score": round(min(1.0, best_score), 4), "matched_terms": _dedupe_strings(hits)[:8]})
    out.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
    return out


def _alignment_phrase_score(phrase: str, normalized_text: str) -> float:
    phrase_norm = _norm(phrase)
    if not phrase_norm or not normalized_text:
        return 0.0
    phrase_tokens = _hebrew_tokens(phrase_norm)
    if len(phrase_tokens) == 1 and phrase_norm == phrase_tokens[0]:
        # Single Hebrew keywords are too short for substring matching:
        # e.g. "רב" inside "הרלב\"ד" or "מים" inside an unrelated word.
        return 0.9 if _single_hebrew_keyword_in_text(phrase_tokens[0], normalized_text) else 0.0
    if phrase_norm in normalized_text:
        return 0.9 if len(_hebrew_tokens(phrase_norm)) <= 1 else 0.96
    if not phrase_tokens:
        return 0.0
    text_tokens = set(_hebrew_tokens(normalized_text))
    hits = [token for token in phrase_tokens if token in text_tokens]
    if len(hits) >= min(len(phrase_tokens), max(2, len(phrase_tokens) - 1)):
        return 0.72
    return 0.0


def _single_hebrew_keyword_in_text(keyword: str, normalized_text: str) -> bool:
    prefixes = {"ו", "ה", "ב", "כ", "ל", "מ"}
    for token in _hebrew_tokens(normalized_text):
        stripped = token
        for _ in range(3):
            if stripped == keyword:
                return True
            if len(stripped) <= len(keyword) or stripped[:1] not in prefixes:
                break
            stripped = stripped[1:]
    return False


def _unknown_root_candidate_assignment(*, item: dict[str, Any], parsed: dict[str, Any], raw_dicta_root_topic_id: str, subject: str) -> dict[str, Any]:
    alignment_text = _join_unique([subject, parsed.get("topic_subject_he"), parsed.get("topic_supporting_quote_he"), parsed.get("rationale_he")])
    root_topic_id = infer_root_topic_id(alignment_text, allow_procedural_default=False)
    if root_topic_id not in ROOT_BY_ID:
        root_topic_id = "root_agenda_queries"
    root_label = root_label_for_id(root_topic_id) or root_label_for_id("root_agenda_queries") or ""
    root_adjudication = {
        "root_topic_id": root_topic_id,
        "dicta_root_topic_id": None,
        "fallback_root_topic_id": root_topic_id,
        "fallback_root_label_he": root_label,
        "unknown_dicta_root_topic_id": raw_dicta_root_topic_id,
        "decision": "unknown_dicta_root_candidate_root",
    }
    proposed = _compact(parsed.get("proposed_new_root_label_he")) or _compact(parsed.get("root_topic_id"))
    return _assignment_payload(
        item=item,
        root_topic_id=root_topic_id,
        root_label=root_label,
        child_label=None,
        raw_child_label=None,
        status="candidate",
        reject_reason=f"candidate_root:unknown_dicta_root:{raw_dicta_root_topic_id}",
        aliases=[proposed] if proposed else [],
        confidence=min(0.6, _confidence(parsed.get("confidence"))),
        quote=_grounded_quote(item=item, parsed_quote=_compact(parsed.get("topic_supporting_quote_he")), fallback_text=str(item.get("raw_text") or "")),
        route="dictalm_v4_candidate_root:unknown_dicta_root",
        rationale_he=_compact(parsed.get("rationale_he"))[:180] or "Dicta proposed a root outside the allowed tree; kept as taxonomy candidate.",
        parsed_contract=_parsed_topic_contract(parsed),
        root_adjudication=root_adjudication,
    )


def _parsed_subject_should_preserve_item_subject(*, parsed_contract: dict[str, Any], item: dict[str, Any], root_label: str) -> bool:
    if not _is_protocol_item(item) or not item.get("topic_subject_he"):
        return False
    if str(item.get("topic_context_source") or "") == "subject_scoped_speaker_anchor":
        return True
    parsed_subject = _compact(parsed_contract.get("topic_subject_he"))
    if not parsed_subject:
        return True
    if _norm(parsed_subject) == _norm(root_label):
        return True
    if parsed_subject in {"שמירה והיטלים", "הצעות לסדר", "סדר יום ושאילתות"}:
        return True
    if any(term in parsed_subject for term in ["מצ\"ל", "החלטות", "חברי המועצה מאשרים", "הצביעו"]):
        return True
    return False


def _parsed_subject_unsupported_in_protocol_listing(*, item: dict[str, Any], parsed_subject: str, parsed: dict[str, Any]) -> bool:
    if not _is_protocol_item(item):
        return False
    raw_text = _compact(item.get("raw_text"))
    if not raw_text or not (_looks_like_protocol_listing(raw_text) or _looks_like_multi_committee_protocol_listing(raw_text)):
        return False
    # Multi-item protocol listings can inherit nearby context. Require the
    # model subject to be supported by the row's own text, not by inherited text.
    return not _tokens_supported(parsed_subject, raw_text)


def _looks_like_multi_committee_protocol_listing(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    protocol_mentions = len(re.findall(r"פרוטוקול\s+מישיבת\s+(?:ה)?ועדת", normalized))
    attachment_markers = len(re.findall(r"מצ\s*ל", normalized))
    return protocol_mentions >= 2 and (attachment_markers >= 1 or len(_hebrew_tokens(normalized)) <= 45)


def _fallback_assignment(item: dict[str, Any], *, reason: str) -> dict[str, Any]:
    text = str(item.get("raw_text") or "")
    topic_text = _topic_basis_text(item)
    if _non_topic_protocol_reason(headline=topic_text, raw_text=_shape_guard_raw_text(item=item, topic_text=topic_text, fallback_text=text), structural_role=str(item.get("structural_role") or ""), packet_role=str((item.get("document_context") or {}).get("packet_role") or "")):
        return _non_topic_assignment(item, reason=f"fallback_{reason}_non_topic")
    candidate_review = _candidate_review_for_omitted_model(item=item, reason=reason)
    if candidate_review:
        return candidate_review
    fallback_root_topic_id = infer_root_topic_id(topic_text or text, allow_procedural_default=not (_is_protocol_item(item) and bool(item.get("is_topic_bearing"))))
    decision = item.get("deterministic_topic_decision") if isinstance(item.get("deterministic_topic_decision"), dict) else {}
    decision_root = str(decision.get("root_topic_id") or "")
    if fallback_root_topic_id not in ROOT_BY_ID and decision_root in ROOT_BY_ID and not bool(decision.get("needs_dicta")) and (_optional_confidence(decision.get("confidence")) or 0.0) >= 0.86:
        fallback_root_topic_id = decision_root
    if _is_protocol_item(item) and bool(item.get("is_topic_bearing")) and fallback_root_topic_id == "root_agenda_queries":
        recovered_root = _recover_root_only_topic(item=item, parsed={}, subject=topic_text or text, fallback_root_topic_id=fallback_root_topic_id)
        if recovered_root:
            fallback_root_topic_id = recovered_root
    evidence_root = _strong_body_evidence_root(item)
    if evidence_root:
        fallback_root_topic_id = evidence_root
    root_adjudication = _adjudicate_assignment_root(item=item, parsed={}, subject=topic_text or text, dicta_root_topic_id=None, fallback_root_topic_id=fallback_root_topic_id)
    root_topic_id = str(root_adjudication.get("root_topic_id") or fallback_root_topic_id)
    if root_topic_id not in ROOT_BY_ID:
        root_topic_id = fallback_root_topic_id if fallback_root_topic_id in ROOT_BY_ID else "root_agenda_queries"
    root_label = root_label_for_id(root_topic_id) or ""
    if _fallback_requires_review(item=item, root_topic_id=root_topic_id, reason=reason):
        return _review_assignment(item=item, root_topic_id=root_topic_id, root_label=root_label, reason=reason)
    best_candidate = _best_high_confidence_candidate(item=item, root_topic_id=root_topic_id)
    candidate = str((best_candidate or {}).get("label_he") or "") or None
    candidate_route = str((best_candidate or {}).get("evidence_source") or "") or "root_only"
    packet_role = str((item.get("document_context") or {}).get("packet_role") or "")
    if not candidate and packet_role != "protocol":
        candidate, candidate_route = derive_child_candidate(text=text, outline_title_he=str(item.get("outline_title_he") or ""))
    validation_evidence = _validation_evidence_text(item=item, quote=text[:500], selected_candidate=best_candidate)
    validation = validate_child_label(raw_label=candidate, root_label_he=root_label, evidence_text=validation_evidence, structural_role=str(item.get("structural_role") or "")) if candidate else None
    resolved = resolve_child_topic_assignment(root_topic_id=root_topic_id, root_label_he=root_label, child_label_he=validation.cleaned_label if validation and validation.status == "active" else candidate, evidence_text=validation_evidence, structural_role=str(item.get("structural_role") or ""), selected_existing=bool(best_candidate))
    root_topic_id = str(resolved["root_topic_id"])
    root_label = str(resolved["root_label_he"])
    child_label = resolved.get("child_label_he")
    status = str(resolved.get("status") or "active")
    route = f"deterministic_v4_fallback:{reason}:{candidate_route}"
    if resolved.get("route_suffix") == "semantic_reparent":
        route = f"{route}:semantic_reparent"
    elif resolved.get("route_suffix") in {"procedural_root_child_demoted", "root_only"} or str(resolved.get("route_suffix") or "").startswith("child_demoted"):
        route = f"{route}:{resolved.get('route_suffix')}"
    aliases = _dedupe_strings([*(resolved.get("aliases_he") or []), *(((best_candidate or {}).get("aliases_he") or []) if best_candidate else [])])
    return _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=child_label, raw_child_label=candidate, status=status, reject_reason=resolved.get("reason") or (validation.reason if validation else None), aliases=aliases, confidence=0.55 if child_label else 0.45, quote=text[:500], route=route, rationale_he=reason, root_adjudication=root_adjudication)


def _review_assignment(*, item: dict[str, Any], root_topic_id: str, root_label: str, reason: str) -> dict[str, Any]:
    return _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=None, raw_child_label=None, status="candidate", reject_reason=f"non_blocking_topic_review:{reason}", aliases=[], confidence=0.25, quote=str(item.get("raw_text") or "")[:500], route=f"deterministic_v4_candidate_review:{reason}", rationale_he="topic assignment is imported as a candidate instead of blocking the protocol")


def _candidate_review_for_omitted_model(*, item: dict[str, Any], reason: str) -> dict[str, Any] | None:
    if reason not in {"model_omitted_unit", "model_error", "missing_after_retry", "contextual_model_omitted_unit", "contextual_model_error"} or not _is_protocol_item(item) or not bool(item.get("is_topic_bearing")):
        return None
    decision = item.get("deterministic_topic_decision") if isinstance(item.get("deterministic_topic_decision"), dict) else {}
    if not bool(decision.get("needs_dicta")):
        return None
    root_topic_id = str(decision.get("root_topic_id") or "")
    if root_topic_id not in ROOT_BY_ID:
        candidates = item.get("root_topic_candidates") if isinstance(item.get("root_topic_candidates"), list) else []
        first_candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
        root_topic_id = str(first_candidate.get("root_topic_id") or "")
    if root_topic_id not in ROOT_BY_ID:
        return None
    child_evidence_assignment = _assignment_from_omitted_model_child_evidence(item=item, root_topic_id=root_topic_id, reason=reason, decision=decision)
    if child_evidence_assignment:
        return child_evidence_assignment
    root_label = root_label_for_id(root_topic_id) or ""
    review_reason = f"{reason}:{decision.get('reason') or 'ambiguous_candidate'}"
    return _review_assignment(item=item, root_topic_id=root_topic_id, root_label=root_label, reason=review_reason)


def _assignment_from_omitted_model_child_evidence(*, item: dict[str, Any], root_topic_id: str, reason: str, decision: dict[str, Any]) -> dict[str, Any] | None:
    selected_candidate = _best_high_confidence_candidate(item=item, root_topic_id=root_topic_id)
    if not selected_candidate or str(selected_candidate.get("evidence_source") or "") != "existing_tree":
        return None
    if float(selected_candidate.get("confidence_hint") or 0.0) < 0.9:
        return None
    root_label = root_label_for_id(root_topic_id) or ""
    raw_child = clean_topic_label(selected_candidate.get("label_he"))
    if not raw_child:
        return None
    text = str(item.get("raw_text") or "")
    quote = _grounded_quote(item=item, parsed_quote=str(selected_candidate.get("evidence_quote_he") or ""), fallback_text=text)
    validation_evidence = _validation_evidence_text(item=item, quote=quote, selected_candidate=selected_candidate)
    validation = validate_child_label(raw_label=raw_child, root_label_he=root_label, evidence_text=validation_evidence, selected_existing=True, structural_role=str(item.get("structural_role") or ""))
    resolved = resolve_child_topic_assignment(root_topic_id=root_topic_id, root_label_he=root_label, child_label_he=validation.cleaned_label if validation and validation.status == "active" else raw_child, evidence_text=validation_evidence, structural_role=str(item.get("structural_role") or ""), selected_existing=True)
    if str(resolved.get("root_topic_id") or "") != root_topic_id or str(resolved.get("status") or "") != "active" or not resolved.get("child_label_he"):
        return None
    root_adjudication = _adjudicate_assignment_root(item=item, parsed={}, subject=_topic_basis_text(item) or text, dicta_root_topic_id=None, fallback_root_topic_id=root_topic_id)
    route = f"deterministic_v4_child_tree_model_omission:{reason}:{decision.get('reason') or 'ambiguous_candidate'}:{selected_candidate.get('evidence_source') or 'existing_tree'}"
    aliases = _dedupe_strings([*(resolved.get("aliases_he") or []), *(selected_candidate.get("aliases_he") or [])])
    return _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=resolved.get("child_label_he"), raw_child_label=raw_child, status="active", reject_reason=None, aliases=aliases, confidence=max(0.74, min(0.9, float(selected_candidate.get("confidence_hint") or 0.0))), quote=quote, route=route, rationale_he="closed-list child topic evidence resolved an omitted model row", root_adjudication=root_adjudication)


def _fallback_requires_review(*, item: dict[str, Any], root_topic_id: str, reason: str) -> bool:
    if not _is_protocol_item(item) or reason not in {"model_omitted_unit", "model_error", "missing_after_retry", "contextual_model_omitted_unit", "contextual_model_error"}:
        return False
    if not bool(item.get("is_topic_bearing")):
        return False
    return not _fallback_has_strong_import_evidence(item=item, root_topic_id=root_topic_id)


def _fallback_has_strong_import_evidence(*, item: dict[str, Any], root_topic_id: str) -> bool:
    if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
        return False
    decision = item.get("deterministic_topic_decision") if isinstance(item.get("deterministic_topic_decision"), dict) else {}
    policy_id = _compact(decision.get("policy_id"))
    confidence = _optional_confidence(decision.get("confidence")) or 0.0
    if str(decision.get("action") or "") == "choose_existing_topic" and bool(policy_id) and confidence >= 0.92:
        return True
    if _deterministic_decision_supports_root(item=item, root_topic_id=root_topic_id, min_confidence=0.9):
        return True
    best_candidate = _best_high_confidence_candidate(item=item, root_topic_id=root_topic_id)
    if best_candidate and float(best_candidate.get("confidence_hint") or 0.0) >= 0.9:
        return True
    return False


def _deterministic_decision_supports_root(*, item: dict[str, Any], root_topic_id: str, min_confidence: float) -> bool:
    decision = item.get("deterministic_topic_decision") if isinstance(item.get("deterministic_topic_decision"), dict) else {}
    if str(decision.get("root_topic_id") or "") != root_topic_id:
        return False
    confidence = _optional_confidence(decision.get("confidence")) or 0.0
    if confidence < min_confidence:
        return False
    if str(decision.get("action") or "") == "choose_existing_topic" and not bool(decision.get("needs_dicta")):
        return True
    matched_terms = [str(term) for term in decision.get("matched_terms") or [] if str(term).strip()]
    if str(decision.get("child_choice_id") or "") and len(matched_terms) >= 1:
        return True
    if len(matched_terms) >= 2:
        return True
    return False


def _preassign_without_model(item: dict[str, Any]) -> bool:
    row_type = str(item.get("row_type") or "")
    packet_role = str((item.get("document_context") or {}).get("packet_role") or "")
    if not _skip_model_for_row_type(row_type=row_type, packet_role=packet_role):
        return False
    return not _fragment_should_get_model_review(item=item, row_type=row_type, packet_role=packet_role)


def _fragment_should_get_model_review(*, item: dict[str, Any], row_type: str, packet_role: str) -> bool:
    if packet_role != "protocol" or row_type not in {"fragment", "attribution_fragment"}:
        return False
    text = _compact(_join_unique([item.get("topic_identification_context"), item.get("topic_headline_he"), item.get("raw_text")]))
    if not text or len(text) > 420 or len(_hebrew_tokens(text)) > 70:
        return False
    if _looks_like_protocol_listing(text) or _looks_like_speaker_dialogue_fragment(text):
        return False
    if not (_has_explicit_local_topic_marker(text) or _has_bounded_substantive_topic_signal(text)):
        return False
    subject = _clean_topic_subject(text)
    if not subject or len(_hebrew_tokens(subject)) < 2:
        return False
    return infer_root_topic_id(subject, allow_procedural_default=False) in ROOT_BY_ID


def _preassign_with_candidate_finder(item: dict[str, Any]) -> bool:
    decision = item.get("deterministic_topic_decision") if isinstance(item.get("deterministic_topic_decision"), dict) else {}
    return (
        str(decision.get("action") or "") == "choose_existing_topic"
        and not bool(decision.get("needs_dicta"))
        and str(decision.get("root_topic_id") or "") in ROOT_BY_ID
    )


def _preassign_contextual_with_candidate_finder(item: dict[str, Any]) -> bool:
    """Skip expensive contextual calls only for strong reusable policy matches."""
    if not _preassign_with_candidate_finder(item):
        return False
    if not _is_protocol_item(item):
        return False
    decision = item.get("deterministic_topic_decision") if isinstance(item.get("deterministic_topic_decision"), dict) else {}
    root_topic_id = str(decision.get("root_topic_id") or "")
    if root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
        return False
    if str(decision.get("reason") or "") != "strong_policy_match":
        return False
    confidence = _optional_confidence(decision.get("confidence"))
    if confidence is None or confidence < 0.92:
        return False
    return _policy_autoselect_block_reason(item=item, decision=decision) is None


def _assignment_from_candidate_finder(item: dict[str, Any]) -> dict[str, Any]:
    decision = item.get("deterministic_topic_decision") if isinstance(item.get("deterministic_topic_decision"), dict) else {}
    text = str(item.get("raw_text") or "")
    topic_text = _topic_basis_text(item)
    root_topic_id = str(decision.get("root_topic_id") or infer_root_topic_id(topic_text or text) or "")
    if root_topic_id not in ROOT_BY_ID:
        root_topic_id = infer_root_topic_id(topic_text or text) or "root_agenda_queries"
    root_label = root_label_for_id(root_topic_id) or ""
    policy_block_reason = _policy_autoselect_block_reason(item=item, decision=decision)
    if policy_block_reason:
        return _non_topic_assignment(item, reason=policy_block_reason)
    candidate_by_id = {str(row.get("candidate_child_id") or ""): row for row in item.get("candidate_child_topics") or []}
    selected_candidate = candidate_by_id.get(str(decision.get("child_choice_id") or ""))
    raw_child = clean_topic_label((selected_candidate or {}).get("label_he") or decision.get("child_label_he"))
    if selected_candidate is None and raw_child:
        selected_candidate = _matching_candidate_by_label(item=item, root_topic_id=root_topic_id, label=raw_child)
    if selected_candidate is None and not raw_child:
        selected_candidate = _best_high_confidence_candidate(item=item, root_topic_id=root_topic_id)
        raw_child = clean_topic_label((selected_candidate or {}).get("label_he"))
    if selected_candidate and str(selected_candidate.get("root_topic_id") or "") in ROOT_BY_ID:
        root_topic_id = str(selected_candidate.get("root_topic_id"))
        root_label = root_label_for_id(root_topic_id) or root_label
    quote = _grounded_quote(item=item, parsed_quote=str((selected_candidate or {}).get("evidence_quote_he") or ""), fallback_text=text)
    validation_evidence = _validation_evidence_text(item=item, quote=quote, selected_candidate=selected_candidate)
    validation = validate_child_label(raw_label=raw_child, root_label_he=root_label, evidence_text=validation_evidence, selected_existing=bool(selected_candidate), structural_role=str(item.get("structural_role") or "")) if raw_child else None
    resolved = resolve_child_topic_assignment(root_topic_id=root_topic_id, root_label_he=root_label, child_label_he=validation.cleaned_label if validation and validation.status == "active" else raw_child, evidence_text=validation_evidence, structural_role=str(item.get("structural_role") or ""), selected_existing=bool(selected_candidate))
    root_topic_id = str(resolved["root_topic_id"])
    root_label = str(resolved["root_label_he"])
    child_label = resolved.get("child_label_he")
    status = str(resolved.get("status") or "active")
    reject_reason = resolved.get("reason") or (validation.reason if validation else None)
    if child_label and selected_candidate and str(selected_candidate.get("evidence_source") or "") not in {"existing_tree", "referenced_attachment"}:
        raw_child = child_label
        child_label = None
        status = "active"
        reject_reason = None
    root_adjudication = _adjudicate_assignment_root(item=item, parsed={"policy_id": decision.get("policy_id")}, subject=topic_text or text, dicta_root_topic_id=None, fallback_root_topic_id=root_topic_id)
    if root_topic_id != str(root_adjudication.get("root_topic_id") or ""):
        root_adjudication = {**root_adjudication, "resolved_root_topic_id": root_topic_id, "resolved_root_label_he": root_label, "decision": f"{root_adjudication.get('decision') or 'root_adjudication'}:candidate_finder_resolved_root"}
    route = f"deterministic_v4_candidate_finder:{decision.get('reason') or 'strong_match'}"
    if selected_candidate and child_label:
        route = f"{route}:{selected_candidate.get('evidence_source') or 'child_candidate'}"
    elif raw_child and selected_candidate and str(selected_candidate.get("evidence_source") or "") not in {"existing_tree", "referenced_attachment"}:
        route = f"{route}:{selected_candidate.get('evidence_source') or 'child_candidate'}:new_child_demoted_to_root"
    if resolved.get("route_suffix"):
        route = f"{route}:{resolved.get('route_suffix')}"
    aliases = _dedupe_strings([*(resolved.get("aliases_he") or []), *(((selected_candidate or {}).get("aliases_he") or []) if selected_candidate else [])])
    return _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=child_label, raw_child_label=raw_child, status=status, reject_reason=reject_reason, aliases=aliases, confidence=_confidence(decision.get("confidence")), quote=quote, route=route, rationale_he="deterministic topic-tree candidate finder selected a high-confidence existing topic", root_adjudication=root_adjudication)


def _policy_autoselect_block_reason(*, item: dict[str, Any], decision: dict[str, Any]) -> str | None:
    if not _is_protocol_item(item) or str(decision.get("reason") or "") != "strong_policy_match":
        return None
    text = _topic_basis_text(item)
    normalized = _norm(text)
    if not normalized:
        return "policy_match_without_topic_subject"
    if _looks_like_policy_dialogue_fragment(normalized):
        return "policy_match_inside_dialogue_fragment"
    return None


def _looks_like_policy_dialogue_fragment(normalized: str) -> bool:
    dialogue_or_uncertainty = [
        "אם צריך",
        "אולי לא צריך",
        "איך אנחנו",
        "קיבלת תשובה",
        "זה לא עובד",
        "אני שואל",
        "כדי לדעת",
    ]
    if not any(cue in normalized for cue in dialogue_or_uncertainty):
        return False
    substantive_actions = ["אישור", "הסכם", "מינוי", "הקצאה", "תיקון", "חוק עזר", "תכנית", "תוכנית", "תב ר", "תבר", "פטור", "הנחה"]
    return not any(action in normalized for action in substantive_actions)


def _looks_like_active_conversational_subject(subject: str) -> bool:
    normalized = _norm(subject)
    if not normalized:
        return True
    substantive_actions = ["אישור", "הסכם", "מינוי", "הקצאה", "תיקון", "חוק עזר", "תכנית", "תוכנית", "תב ר", "תבר", "פטור", "הנחה", "תקציב", "מכרז", "ארנונה", "רשות שימוש"]
    if any(action in normalized for action in substantive_actions):
        return False
    tokens = _hebrew_tokens(normalized)
    dialogue_cues = [
        "נאמר ש",
        "גם ככה",
        "אני אשמח",
        "אני שואל",
        "קיבלת תשובה",
        "אם צריך",
        "אולי לא צריך",
        "כדי לדעת",
        "זה קיים",
        "זה לא עובד",
    ]
    if any(cue in normalized for cue in dialogue_cues) and len(tokens) <= 12:
        return True
    if tokens and tokens[0] in {"אני", "אנחנו", "נאמר", "אמרתי", "אמרנו", "צריך"} and len(tokens) <= 10:
        return True
    return False


def _candidate_review_assignment(*, item: dict[str, Any], reason: str) -> dict[str, Any]:
    decision = item.get("deterministic_topic_decision") if isinstance(item.get("deterministic_topic_decision"), dict) else {}
    if _unsupported_weak_candidate_decision(item=item, decision=decision):
        return _non_topic_assignment(item, reason="unsupported_weak_candidate")
    text = str(item.get("raw_text") or "")
    topic_text = _topic_basis_text(item)
    root_topic_id = _candidate_review_root_topic_id(item=item, decision=decision, topic_text=topic_text or text)
    if root_topic_id not in ROOT_BY_ID:
        root_topic_id = infer_root_topic_id(topic_text or text, allow_procedural_default=False) or "root_agenda_queries"
    evidence_root = _strong_body_evidence_root(item)
    if evidence_root:
        root_topic_id = evidence_root
    root_label = root_label_for_id(root_topic_id) or ""
    return _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=None, raw_child_label=None, status="candidate", reject_reason=f"non_blocking_topic_review:{reason}:{decision.get('reason') or 'no_decision'}", aliases=[], confidence=min(0.71, max(0.25, _confidence(decision.get("confidence")))), quote=text[:500], route=f"deterministic_v4_candidate_review:{reason}:{decision.get('reason') or 'no_decision'}", rationale_he="ambiguous topic imported as candidate so protocol ingestion can continue", root_adjudication=_adjudicate_assignment_root(item=item, parsed={}, subject=topic_text or text, dicta_root_topic_id=None, fallback_root_topic_id=root_topic_id))


def _candidate_review_root_topic_id(*, item: dict[str, Any], decision: dict[str, Any], topic_text: str) -> str:
    decision_root = str(decision.get("root_topic_id") or "")
    policy_matches = topic_policy_matches(topic_text, limit=1)
    policy_root = str((policy_matches[0] if policy_matches else {}).get("root_topic_id") or "")
    if policy_root in ROOT_BY_ID and policy_root not in {"root_agenda_queries", "root_order_proposals"}:
        return policy_root
    if decision_root in ROOT_BY_ID and decision_root not in {"root_agenda_queries", "root_order_proposals"} and _candidate_root_supported(item=item, root_topic_id=decision_root):
        return decision_root
    supported = _best_supported_non_procedural_candidate_root(item=item)
    if supported:
        return supported
    if decision_root in ROOT_BY_ID:
        return decision_root
    return infer_root_topic_id(topic_text, allow_procedural_default=False) or "root_agenda_queries"


def _best_supported_non_procedural_candidate_root(*, item: dict[str, Any]) -> str | None:
    candidates = []
    for candidate in item.get("root_topic_candidates") or []:
        root_topic_id = str(candidate.get("root_topic_id") or "")
        if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
            continue
        matched_terms = [str(term) for term in candidate.get("matched_terms") or [] if str(term).strip()]
        if not matched_terms:
            continue
        score = float(candidate.get("score") or 0.0)
        if score < 0.72:
            continue
        candidates.append((score, root_topic_id))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _candidate_root_supported(*, item: dict[str, Any], root_topic_id: str) -> bool:
    for candidate in item.get("root_topic_candidates") or []:
        if str(candidate.get("root_topic_id") or "") != root_topic_id:
            continue
        if float(candidate.get("score") or 0.0) >= 0.72 and any(str(term).strip() for term in candidate.get("matched_terms") or []):
            return True
    return False


def _unsupported_weak_candidate_decision(*, item: dict[str, Any], decision: dict[str, Any]) -> bool:
    if str(decision.get("reason") or "") != "weak_candidate":
        return False
    if float(decision.get("confidence") or 0.0) >= 0.58:
        return False
    root_topic_id = str(decision.get("root_topic_id") or "")
    candidates = [row for row in item.get("root_topic_candidates") or [] if isinstance(row, dict)]
    selected = next((row for row in candidates if str(row.get("root_topic_id") or "") == root_topic_id), candidates[0] if candidates else {})
    if str(selected.get("source") or "") in {"topic_policy", "existing_tree", "referenced_attachment"}:
        return False
    if selected.get("child_choice_id"):
        return False
    return not [str(term) for term in selected.get("matched_terms") or [] if str(term).strip()]


def _skip_model_for_row_type(*, row_type: str, packet_role: str) -> bool:
    if packet_role != "protocol":
        return False
    return row_type in {"metadata", "container", "vote_or_result", "fragment", "attribution_fragment", "merged_topic_detail"}


def _non_topic_assignment(item: dict[str, Any], *, reason: str | None = None) -> dict[str, Any]:
    topic_text = _topic_basis_text(item)
    row_type = str(item.get("row_type") or "fragment")
    if reason is None and row_type == "merged_topic_detail":
        reason = "merged_topic_detail"
    root_topic_id = infer_root_topic_id(topic_text) if topic_text and row_type == "vote_or_result" else "root_agenda_queries"
    evidence_root = _strong_body_evidence_root(item, own_text_only=True) if _can_apply_non_topic_evidence_root(item=item, row_type=row_type, reason=reason) else None
    if evidence_root:
        root_topic_id = evidence_root
    root_label = root_label_for_id(root_topic_id) or root_label_for_id("root_agenda_queries") or ""
    selected_candidate = None
    child_label = None
    route = f"deterministic_v4_row_type:{row_type}"
    if reason:
        route = f"{route}:{reason}"
    if evidence_root:
        route = f"{route}:strong_body_evidence_root"
    return _assignment_payload(
        item=item,
        root_topic_id=root_topic_id,
        root_label=root_label,
        child_label=child_label,
        raw_child_label=child_label,
        status="active",
        reject_reason=None,
        aliases=list((selected_candidate or {}).get("aliases_he") or []),
        confidence=0.55 if evidence_root else (0.5 if row_type == "vote_or_result" else 0.35),
        quote=str(item.get("raw_text") or "")[:500],
        route=route,
        rationale_he="row type is not a standalone topic-bearing agenda subject; strong body evidence supplies root context" if evidence_root else "row type is not a standalone topic-bearing agenda subject",
        parsed_contract={"is_topic_bearing": False, "topic_subject_he": None, "clean_subject_he": None},
    )


def _can_apply_non_topic_evidence_root(*, item: dict[str, Any], row_type: str, reason: str | None) -> bool:
    if not _is_protocol_item(item) or row_type not in {"container", "fragment", "attribution_fragment"}:
        return False
    if reason in NON_TOPIC_CARRIER_REASONS or str(item.get("non_topic_reason") or "") in NON_TOPIC_CARRIER_REASONS:
        return False
    return not _looks_like_long_protocol_transcript_fragment(item=item, reason=reason)


def _looks_like_long_protocol_transcript_fragment(*, item: dict[str, Any], reason: str | None) -> bool:
    text = _compact(item.get("unit_raw_text") or item.get("raw_text") or "")
    if not text:
        return False
    if _looks_like_protocol_listing(text):
        return True
    normalized = _norm(text)
    has_protocol_header = "פרוטוקול" in normalized and ("מתאריך" in normalized or "ישיבה מן המניין" in normalized or "ישיבה לא מן המניין" in normalized or "ישיבות המועצה" in normalized)
    if has_protocol_header:
        return True
    if len(text) < 700 and len(_hebrew_tokens(text)) < 120:
        return False
    speaker_markers = len(_speaker_marker_re().findall(text))
    if speaker_markers >= 2:
        return True
    return reason in {"transcript_window_without_bounded_headline", "inherited_context_not_standalone_topic"} and speaker_markers >= 1


def _assignment_requires_subject_review(*, item: dict[str, Any], root_topic_id: str, child_label: str | None) -> bool:
    return _is_protocol_item(item) and bool(item.get("is_topic_bearing")) and root_topic_id == "root_agenda_queries" and not child_label


def _verified_body_root_override(*, item: dict[str, Any], root_topic_id: str, root_adjudication: dict[str, Any], child_label: str | None) -> str | None:
    if not _is_protocol_item(item) or child_label:
        return None
    if str(item.get("structural_role") or "") not in {"body", "continuation", "task_row"}:
        return None
    if root_adjudication.get("policy_id"):
        return None
    evidence_root = _strong_body_evidence_root(item)
    if not evidence_root or evidence_root == root_topic_id:
        return None
    return evidence_root


def _strong_body_evidence_root(item: dict[str, Any], *, own_text_only: bool = False) -> str | None:
    own_text = item.get("unit_raw_text") or item.get("raw_text")
    text = str(own_text or "") if own_text_only else _join_unique([item.get("topic_subject_he"), item.get("topic_headline_he"), own_text])
    if not text:
        return None
    for match in topic_policy_matches(text, limit=3):
        root_topic_id = str(match.get("root_topic_id") or "")
        if root_topic_id in ROOT_BY_ID and root_topic_id not in {"root_agenda_queries", "root_order_proposals"}:
            return root_topic_id
    scored = [row for row in _allowed_root_alignment_scores(text) if str(row.get("root_topic_id") or "") not in {"root_agenda_queries", "root_order_proposals"}]
    if not scored:
        return None
    best = scored[0]
    second = scored[1] if len(scored) > 1 else {"score": 0.0}
    best_score = float(best.get("score") or 0.0)
    margin = best_score - float(second.get("score") or 0.0)
    matched_terms = [str(term) for term in best.get("matched_terms") or [] if str(term).strip()]
    if best_score >= 0.82 and margin >= 0.12 and len(matched_terms) >= 2:
        return str(best.get("root_topic_id") or "")
    second_terms = [str(term) for term in second.get("matched_terms") or [] if str(term).strip()]
    if best_score >= 0.82 and margin >= 0.0 and len(matched_terms) >= 2 and len(second_terms) <= 1:
        return str(best.get("root_topic_id") or "")
    return None


def _apply_structured_fallbacks(*, assignments: list[dict[str, Any]], items: list[dict[str, Any]], enable_govmap_geo: bool) -> list[dict[str, Any]]:
    item_by_id = {str(item.get("structure_unit_id") or ""): item for item in items}
    out: list[dict[str, Any]] = []
    for row in assignments:
        current = dict(row)
        item = item_by_id.get(str(current.get("structure_unit_id") or ""))
        replacement = _structured_fallback_assignment(row=current, item=item, enable_govmap_geo=enable_govmap_geo) if item else None
        out.append(replacement or current)
    return out


def _structured_fallback_assignment(*, row: dict[str, Any], item: dict[str, Any], enable_govmap_geo: bool) -> dict[str, Any] | None:
    if not _should_try_structured_fallback(row=row, item=item):
        return None
    subject = _structured_fallback_subject(row=row, item=item)
    if subject:
        municipality = str((item.get("document_context") or {}).get("municipality_he") or "") or None
        geo = _resolve_geo_fallback(subject=subject, municipality=municipality, enable_govmap_geo=enable_govmap_geo)
        if geo:
            status = "active" if _geo_resolution_confirms_topic(geo=geo, item=item) else "candidate"
            assignment = _assignment_payload(
                item=item,
                root_topic_id="root_geo",
                root_label=root_label_for_id("root_geo") or "מיקומים וגיאוגרפיה",
                child_label=str(geo.get("child_label_he") or "כתובות ורחובות"),
                raw_child_label=str(geo.get("child_label_he") or "כתובות ורחובות"),
                status=status,
                reject_reason=None if status == "active" else "non_blocking_topic_review:geo_fallback_unverified_location",
                aliases=[],
                confidence=0.76 if status == "active" else 0.58,
                quote=str(item.get("raw_text") or item.get("unit_raw_text") or "")[:500],
                route=f"deterministic_v4_structured_fallback:geo:{geo.get('source') or 'unknown'}",
                rationale_he="location-only subject routed through generic geo fallback",
                parsed_contract={"is_topic_bearing": True, "topic_subject_he": subject, "clean_subject_he": subject},
            )
            assignment["geo_resolution"] = geo
            if _has_confirming_v3_action_hint(item=item):
                assignment["v3_confirmed_subject"] = True
            return assignment
        people = _resolve_people_role_fallback(subject)
        if people:
            assignment = _assignment_payload(
                item=item,
                root_topic_id="root_people_roles",
                root_label=root_label_for_id("root_people_roles") or "אנשים ותפקידים",
                child_label="נבחרי ציבור ובעלי תפקידים",
                raw_child_label="נבחרי ציבור ובעלי תפקידים",
                status="candidate",
                reject_reason="non_blocking_topic_review:people_role_fallback_unverified_alias",
                aliases=list(people.get("aliases_he") or []),
                confidence=0.52,
                quote=str(item.get("raw_text") or item.get("unit_raw_text") or "")[:500],
                route=f"deterministic_v4_structured_fallback:people_role:{people.get('source') or 'unknown'}",
                rationale_he="person or role-only subject routed through generic people/roles fallback",
                parsed_contract={"is_topic_bearing": True, "topic_subject_he": subject, "clean_subject_he": subject},
            )
            assignment["people_role_resolution"] = people
            return assignment
    return _v3_structured_fallback_assignment(row=row, item=item)


def _should_try_structured_fallback(*, row: dict[str, Any], item: dict[str, Any]) -> bool:
    if not _is_protocol_item(item):
        return False
    if str(row.get("topic_reject_reason") or "") == "inherited_duplicate_topic_subject":
        return False
    route = str(row.get("topic_assignment_route") or "")
    root_topic_id = str(row.get("root_topic_id") or "")
    has_trusted_active_topic = (
        str(row.get("topic_node_status") or "") == "active"
        and row.get("is_topic_bearing") is True
        and root_topic_id in ROOT_BY_ID
        and root_topic_id not in {"root_agenda_queries", "root_order_proposals"}
        and bool(_compact(row.get("topic_subject_he")))
        and not any(marker in route for marker in ("weak_candidate", "ambiguous", "model_error", "model_omitted", "topic_subject_rejected"))
    )
    return not has_trusted_active_topic


def _structured_fallback_subject(*, row: dict[str, Any], item: dict[str, Any]) -> str | None:
    for value in [row.get("topic_subject_he"), item.get("topic_subject_he")]:
        subject = _clean_structured_fallback_subject(value)
        if subject and not is_low_quality_topic_label(subject):
            return subject
    text = _join_unique([item.get("topic_headline_he"), item.get("topic_identification_context"), item.get("unit_raw_text")])
    if not _has_explicit_local_topic_marker(text):
        return None
    contract = _topic_contract_from_headline(text, structural_role=str(item.get("structural_role") or ""), packet_role="protocol")
    subject = _clean_structured_fallback_subject(contract.get("topic_subject_he"))
    if subject and not is_low_quality_topic_label(subject):
        return subject
    quoted = _quoted_subject_after_topic_marker(text)
    subject = _clean_structured_fallback_subject(quoted)
    return subject if subject and not is_low_quality_topic_label(subject) else None


def _quoted_subject_after_topic_marker(text: str) -> str | None:
    compact = _compact(text)
    match = re.search(r"\bבנושא\b\s*[\"'׳״]?\s*(.{3,180}?)(?=[\"'׳״]|\s+גב['׳]?|\s+מר\b|\s+ד[\"”]?ר\b|\s+עו[\"”]?ד\b|\s+השאלה|\s+מצ\"?ל|$)", compact)
    return match.group(1) if match else None


def _clean_structured_fallback_subject(value: Any) -> str | None:
    text = _compact(value)
    if not text:
        return None
    text = re.sub(r"(?<=[\u0590-\u05FF])\s*\d{1,2}\s*[\"'׳״]+\s*(?=[\u0590-\u05FF])", " ", text)
    text = text.replace('"', " ").replace("׳", " ").replace("״", " ")
    text = clean_protocol_subject_text(text)
    text = re.sub(r"\s+", " ", text).strip(" '()[]-–:.,")
    return text or None


def _resolve_geo_fallback(*, subject: str, municipality: str | None, enable_govmap_geo: bool) -> dict[str, Any] | None:
    if _subject_has_action_dominant_terms(subject):
        return None
    local = _local_geo_resolution(subject)
    backend_attempts: list[dict[str, Any]] = []
    if enable_govmap_geo:
        google = _google_maps_geo_resolution(subject=subject, municipality=municipality)
        if google and str(google.get("confidence") or "") in {"high", "medium"}:
            if local:
                google["local_geo_resolution"] = local
            return google
        if google:
            backend_attempts.append(google)
        govmap = _govmap_geo_resolution(subject=subject, municipality=municipality)
        if govmap:
            if backend_attempts:
                govmap["backend_resolution_attempts"] = backend_attempts
            if local:
                govmap["local_geo_resolution"] = local
            return govmap
        backend_attempts.append({"source": "govmap_search", "confidence": "none", "status": "no_match", "query": _compact(" ".join(part for part in [municipality, subject] if part))})
    if local and backend_attempts:
        return {**local, "backend_resolution_attempts": backend_attempts}
    return local


def _google_maps_geo_resolution(*, subject: str, municipality: str | None) -> dict[str, Any] | None:
    queries = _google_maps_geo_queries(subject=subject, municipality=municipality)
    if not queries:
        return None
    places: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        from municipality.google_maps_client import GoogleMapsClient  # noqa: PLC0415

        client = GoogleMapsClient(timeout_seconds=6.0)
        for query in queries:
            payload = client.search_text(query, max_results=5)
            if payload.get("error"):
                errors.append(str(payload.get("error")))
                continue
            for place in payload.get("places") or []:
                if isinstance(place, dict):
                    places.append({**place, "_google_maps_query": query, "_google_maps_result_count": len(payload.get("places") or [])})
    except Exception as exc:  # noqa: BLE001 - Google Maps is optional external verification.
        return {"source": "google_maps_places", "confidence": "none", "status": "error", "query": queries[0], "queries": queries, "error": f"{exc.__class__.__name__}:{exc}"}
    if errors and not places:
        return {"source": "google_maps_places", "confidence": "none", "status": "error", "query": queries[0], "queries": queries, "error": "; ".join(errors[:3])}
    result = _best_google_maps_place(places=places, subject=subject, municipality=municipality)
    if not result:
        return {"source": "google_maps_places", "confidence": "none", "status": "no_match", "query": queries[0], "queries": queries, "result_count": len(places)}
    place = result["place"]
    location = place.get("location") if isinstance(place.get("location"), dict) else {}
    confidence = "high" if result["score"] >= 24 and location and result.get("required_place_match") is not False else "medium"
    return {
        "source": "google_maps_places",
        "confidence": confidence,
        "status": "matched",
        "query": str(place.get("_google_maps_query") or queries[0]),
        "queries": queries,
        "result_count": int(place.get("_google_maps_result_count") or len(places)),
        "place_id": place.get("id"),
        "display_name": _google_maps_place_display_name(place),
        "formatted_address": _compact(place.get("formattedAddress")),
        "matched_name_he": _google_maps_place_display_name(place) or _compact(place.get("formattedAddress")) or subject,
        "matched_type": ",".join(str(value) for value in place.get("types") or [] if str(value).strip())[:180] or "google_maps_place",
        "child_label_he": _geo_child_label_for_google_maps_place(place=place, subject=subject),
        "lat_lng": {"lat": location.get("latitude"), "lng": location.get("longitude")} if location else None,
        "google_maps_uri": place.get("googleMapsUri"),
        "municipality_match": result.get("municipality_match"),
        "token_overlap": result.get("token_overlap") or [],
        "distinctive_place_match": result.get("distinctive_place_match"),
        "place_type_match": result.get("place_type_match"),
    }


def _google_maps_geo_queries(*, subject: str, municipality: str | None) -> list[str]:
    clean_subject = _compact(subject)
    clean_subject = re.sub(r"\bבעיר\b", " ", clean_subject)
    clean_subject = re.sub(r"(^|\s)ו(דרך|רחוב|שדרות|שדרה)(?=\s|$)", r"\1\2", clean_subject)
    clean_subject = re.sub(r"\bסעיף\b.*$", "", clean_subject).strip(" '()[]-–:.,")
    requirements = _google_maps_place_marker_requirements(subject)
    queries: list[str] = []
    marker = str(requirements.get("marker") or "")
    distinctive_tokens = [str(token) for token in requirements.get("distinctive_tokens") or [] if str(token).strip()]
    if marker and distinctive_tokens:
        queries.append(_compact(" ".join(part for part in [marker, " ".join(distinctive_tokens), municipality, "ישראל"] if part)))
    queries.append(_compact(" ".join(part for part in [clean_subject, municipality, "ישראל"] if part)))
    return _dedupe_strings([query for query in queries if query])


def _best_google_maps_place(*, places: list[dict[str, Any]], subject: str, municipality: str | None) -> dict[str, Any] | None:
    subject_tokens = {token for token in _hebrew_tokens(_norm(subject)) if len(token) >= 3}
    requirements = _google_maps_place_marker_requirements(subject)
    required_tokens = set(requirements.get("distinctive_tokens") or [])
    required_marker = str(requirements.get("marker") or "")
    scored: list[dict[str, Any]] = []
    for place in places:
        label = _norm(_join_unique([_google_maps_place_display_name(place), place.get("formattedAddress")]))
        if not label:
            continue
        label_tokens = {token for token in _hebrew_tokens(label) if len(token) >= 3}
        overlap_tokens = sorted(subject_tokens & label_tokens)
        if subject_tokens and len(overlap_tokens) < min(2, len(subject_tokens)):
            continue
        municipality_match = _google_maps_municipality_matches(label=label, municipality=municipality)
        location = place.get("location") if isinstance(place.get("location"), dict) else {}
        types = {str(value).lower() for value in place.get("types") or []}
        place_type_match = (required_marker == "כיכר" and ("כיכר" in label or "town_square" in types)) or (required_marker == "צומת" and ("צומת" in label or "intersection" in types))
        distinctive_place_match = bool(required_tokens & label_tokens)
        if required_tokens and not distinctive_place_match and not place_type_match:
            continue
        type_bonus = 4 if types & {"intersection", "town_square", "route", "street_address", "premise", "point_of_interest", "establishment"} else 0
        score = len(overlap_tokens) * 10 + (6 if municipality_match is True else 0) + (2 if location else 0) + type_bonus
        scored.append({"score": score, "place": place, "municipality_match": municipality_match, "token_overlap": overlap_tokens, "required_place_match": (distinctive_place_match or place_type_match) if required_tokens else None, "distinctive_place_match": distinctive_place_match if required_tokens else None, "place_type_match": place_type_match if required_marker else None})
    if not scored:
        return None
    scored.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
    return scored[0]


def _google_maps_place_marker_requirements(subject: str) -> dict[str, Any]:
    normalized = _norm(subject)
    match = re.search(r"(?:^|\s)(כיכר|צומת)\s+(.{2,120})", normalized)
    if not match:
        return {}
    marker = match.group(1)
    tail = match.group(2)
    tail = re.split(r"\s+(?:בעיר|ביישוב|דרך|ודרך|רחוב|ורחוב|רחובות|הרחובות|שדרות|ושדרות|שדרה|ושדרה|פינת|ופינת|ליד|מול|סמוך|באזור)\b", tail, maxsplit=1)[0]
    generic_tokens = {"כיכר", "צומת", "רחוב", "רחובות", "הרחובות", "דרך", "שדרות", "שדרה", "בעיר", "ביישוב"}
    tokens = [token for token in _hebrew_tokens(tail) if token not in generic_tokens and len(token) >= 3]
    return {"marker": marker, "distinctive_tokens": tokens[:3]}


def _google_maps_place_display_name(place: dict[str, Any]) -> str:
    display = place.get("displayName") if isinstance(place.get("displayName"), dict) else {}
    return _compact(display.get("text") or place.get("name") or "")


def _google_maps_municipality_matches(*, label: str, municipality: str | None) -> bool | None:
    municipality_norm = _norm(municipality)
    if not municipality_norm:
        return None
    return municipality_norm in label


def _geo_child_label_for_google_maps_place(*, place: dict[str, Any], subject: str) -> str:
    label = _norm(_join_unique([subject, _google_maps_place_display_name(place), place.get("formattedAddress")]))
    types = {str(value).lower() for value in place.get("types") or []}
    if "כיכר" in label or "צומת" in label or types & {"intersection", "town_square"}:
        return "כיכרות וצמתים"
    if types & {"route", "street_address", "premise", "subpremise"}:
        return "כתובות ורחובות"
    if types & {"neighborhood", "locality", "sublocality", "administrative_area_level_3"}:
        return "שכונות ואזורים"
    return "אתרים ומבני ציבור"


def _subject_has_action_dominant_terms(subject: str) -> bool:
    normalized = _norm(subject)
    action_terms = (
        "אישור", "לאשר", "הסכם", "התקשרות", "מכרז", "מינוי", "הקמת", "הקמה", "שיפוץ", "שדרוג", "ביטול", "דיון",
        "סקירה", "מענק", "פטור", "הקצאה", "הסדרת", "הפעלת", "תבחינים", "תוכנית", "תכנית", "תקציב", "שאילתה",
    )
    return any(term in normalized for term in action_terms)


def _local_geo_resolution(subject: str) -> dict[str, Any] | None:
    normalized = _norm(subject)
    if not normalized:
        return None
    if any(term in normalized for term in ("גוש", "חלקה", "מגרש")):
        return {"source": "local_geo_pattern", "confidence": "medium", "child_label_he": "גושים וחלקות", "matched_name_he": subject, "matched_type": "parcel_reference"}
    if "כיכר" in normalized or "צומת" in normalized:
        return {"source": "local_geo_pattern", "confidence": "medium", "child_label_he": "כיכרות וצמתים", "matched_name_he": subject, "matched_type": "square_or_intersection"}
    road_terms = [term for term in ("רחוב", "רח", "דרך", "שדרות", "שדרה") if re.search(rf"(?:^|\s){re.escape(term)}(?:\s|$)", normalized)]
    if len(road_terms) >= 2:
        return {"source": "local_geo_pattern", "confidence": "medium", "child_label_he": "כיכרות וצמתים", "matched_name_he": subject, "matched_type": "street_intersection"}
    if road_terms:
        return {"source": "local_geo_pattern", "confidence": "medium", "child_label_he": "כתובות ורחובות", "matched_name_he": subject, "matched_type": "street_or_address"}
    if any(term in normalized for term in ("שכונה", "רובע", "אזור")):
        return {"source": "local_geo_pattern", "confidence": "medium", "child_label_he": "שכונות ואזורים", "matched_name_he": subject, "matched_type": "neighborhood_or_area"}
    if any(term in normalized for term in ("פארק", "גן", "חוף", "מתנס", "מתנ ס", "מרכז קהילתי", "בית ספר")):
        return {"source": "local_geo_pattern", "confidence": "medium", "child_label_he": "אתרים ומבני ציבור", "matched_name_he": subject, "matched_type": "public_site"}
    return None


def _govmap_geo_resolution(*, subject: str, municipality: str | None) -> dict[str, Any] | None:
    query = _compact(" ".join(part for part in [municipality, subject] if part))
    if not query:
        return None
    try:
        from municipality.govmap_client import GovMapClient  # noqa: PLC0415

        payload = GovMapClient(timeout_seconds=3.0).search(query, max_results=5)
    except Exception:  # noqa: BLE001 - GovMap is an optional external resolver.
        return None
    result = _best_govmap_geo_result(results=payload.get("results") if isinstance(payload, dict) else [], subject=subject)
    if not result:
        return None
    return {
        "source": "govmap_search",
        "confidence": "high" if result.get("centroid") else "medium",
        "query": query,
        "child_label_he": _geo_child_label_for_govmap_result(result, subject=subject),
        "matched_name_he": _compact(result.get("originalText") or result.get("text") or subject),
        "matched_type": str(result.get("type") or result.get("layerName") or "govmap_result"),
        "centroid": result.get("centroid"),
    }


def _best_govmap_geo_result(*, results: Any, subject: str) -> dict[str, Any] | None:
    rows = [row for row in (results or []) if isinstance(row, dict)]
    if not rows:
        return None
    subject_tokens = set(_hebrew_tokens(_norm(subject)))
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        label = _norm(_join_unique([row.get("originalText"), row.get("text"), row.get("name")]))
        if not label:
            continue
        row_tokens = set(_hebrew_tokens(label))
        overlap = len(subject_tokens & row_tokens)
        if overlap == 0 and subject_tokens:
            continue
        type_bonus = 2 if str(row.get("type") or "").lower() in {"address", "street", "settlement"} else 0
        centroid_bonus = 2 if row.get("centroid") else 0
        scored.append((overlap * 10 + type_bonus + centroid_bonus, row))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1]


def _geo_child_label_for_govmap_result(result: dict[str, Any], *, subject: str = "") -> str:
    result_type = str(result.get("type") or result.get("layerName") or "").lower()
    label = _norm(_join_unique([subject, result.get("originalText"), result.get("text"), result.get("name")]))
    if "כיכר" in label or "צומת" in label:
        return "כיכרות וצמתים"
    if result_type in {"address", "street"}:
        return "כתובות ורחובות"
    if result_type in {"settlement", "neighborhood", "neighborhoods_area"}:
        return "שכונות ואזורים"
    return "אתרים ומבני ציבור"


def _resolve_people_role_fallback(subject: str) -> dict[str, Any] | None:
    if _subject_has_action_dominant_terms(subject):
        return None
    normalized = _norm(subject)
    role_terms = ("ראש העיר", "ראש העירייה", "חבר מועצה", "חברי מועצה", "חברת מועצה", "מנכל", "מנכ ל", "מהנדס העיר", "גזבר", "יועץ משפטי")
    title_pattern = r"(?:^|\s)(?:מר|גב['׳]?|גברת|ד[\"”]?ר|עו[\"”]?ד|הרב)\s+[\u0590-\u05FF]{2,}"
    if any(term in normalized for term in role_terms) or re.search(title_pattern, subject):
        return {"source": "generic_people_role_pattern", "matched_name_he": subject, "aliases_he": [subject]}
    return None


def _geo_resolution_confirms_topic(*, geo: dict[str, Any], item: dict[str, Any]) -> bool:
    if _geo_resolution_is_backend_confirmed(geo):
        return True
    return str(geo.get("source") or "") == "local_geo_pattern" and _has_confirming_v3_action_hint(item=item)


def _geo_resolution_is_backend_confirmed(geo: dict[str, Any]) -> bool:
    return str(geo.get("source") or "") in {"govmap_search", "google_maps_places"} and str(geo.get("confidence") or "") == "high"


def _v3_structured_fallback_assignment(*, row: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    selection = _select_v3_fallback_hint(item=item)
    if not selection:
        return None
    if selection.get("status") == "ambiguous":
        best = selection.get("best") if isinstance(selection.get("best"), dict) else {}
        root_topic_id = str(best.get("root_topic_id") or "root_agenda_queries")
        root_label = root_label_for_id(root_topic_id) or root_label_for_id("root_agenda_queries") or ""
        assignment = _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=None, raw_child_label=None, status="candidate", reject_reason="non_blocking_topic_review:v3_multiple_topic_candidates", aliases=[], confidence=0.35, quote=str(item.get("raw_text") or item.get("unit_raw_text") or "")[:500], route="deterministic_v4_structured_fallback:v3_multiple_topic_candidates", rationale_he="multiple accepted V3 event/subject hints remained ambiguous")
        assignment["v3_fallback_selection"] = selection
        return assignment
    hint = selection.get("best") if isinstance(selection.get("best"), dict) else {}
    root_topic_id = str(hint.get("root_topic_id") or "")
    subject = _clean_structured_fallback_subject(hint.get("topic_subject_he") or hint.get("matter_he"))
    if root_topic_id not in ROOT_BY_ID or not subject:
        return None
    root_label = root_label_for_id(root_topic_id) or ""
    child_label = _existing_child_label_for_subject(root_topic_id=root_topic_id, subject=subject)
    assignment = _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=child_label, raw_child_label=child_label, status="active", reject_reason=None, aliases=[], confidence=0.64, quote=str(hint.get("source_quote_he") or item.get("raw_text") or "")[:500], route="deterministic_v4_structured_fallback:v3_event_subject", rationale_he="accepted entailed V3 event/subject hint mapped back through the Step 4 topic tree", parsed_contract={"is_topic_bearing": True, "topic_subject_he": subject, "clean_subject_he": subject})
    assignment["v3_fallback_selection"] = selection
    return assignment


def _select_v3_fallback_hint(*, item: dict[str, Any]) -> dict[str, Any] | None:
    hints = [hint for hint in item.get("topic_subject_v3_hints") or [] if isinstance(hint, dict)]
    scored: list[dict[str, Any]] = []
    for hint in hints:
        for basis_row in _v3_hint_basis_rows(hint):
            matter = _clean_structured_fallback_subject(basis_row.get("matter_he"))
            matter_display = _clean_structured_fallback_subject(basis_row.get("matter_display_he"))
            action = _clean_structured_fallback_subject(basis_row.get("action_type_he"))
            if not matter and not action:
                continue
            basis = _join_unique([action, matter, basis_row.get("source_quote_he")])
            root_basis = _join_unique([matter_display, matter]) or basis
            root_topic_id = infer_root_topic_id(root_basis, allow_procedural_default=False)
            matter_geo = _resolve_geo_fallback(subject=matter or matter_display or "", municipality=None, enable_govmap_geo=False)
            if matter_geo and root_topic_id not in ROOT_BY_ID:
                root_topic_id = "root_geo"
            if root_topic_id in {None, "root_agenda_queries", "root_order_proposals"}:
                root_topic_id = infer_root_topic_id(basis, allow_procedural_default=False)
            action_root = _contextual_action_root_from_text(_join_unique([basis_row.get("source_quote_he"), matter, action]))
            if action_root in ACTION_DOMINANT_ROOT_IDS and action_root != "root_administration":
                root_topic_id = action_root
            if root_topic_id == "root_geo" and not matter_geo:
                continue
            if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals", "root_mayor_updates", "root_people_roles"}:
                continue
            quote = _compact(basis_row.get("source_quote_he"))
            raw = _join_unique([item.get("unit_raw_text"), item.get("raw_text"), item.get("topic_identification_context"), item.get("topic_headline_he")])
            quote_overlap = bool(quote and (_tokens_supported(quote, raw) or _tokens_supported(raw, quote)))
            exact_unit = str(hint.get("source_structure_unit_id") or "") == str(item.get("structure_unit_id") or "")
            score = 0
            score += 80 if exact_unit else 0
            score += 40 if quote_overlap else 0
            score += 20 if matter and action else 0
            score += 10 if not _subject_has_action_dominant_terms(matter or "") else 0
            # V3 has already judged the selected event candidate against surrounding evidence.
            # Give that selection enough weight to avoid re-marking normal multi-item rows as ambiguous.
            score += 25 if basis_row.get("selected_candidate") else 0
            scored.append({**hint, **basis_row, "root_topic_id": root_topic_id, "topic_subject_he": matter or matter_display or action, "selection_score": score, "quote_overlap": quote_overlap, "exact_unit_match": exact_unit})
    if not scored:
        return None
    scored.sort(key=lambda row: int(row.get("selection_score") or 0), reverse=True)
    top = scored[0]
    competing = [row for row in scored[1:] if int(top.get("selection_score") or 0) - int(row.get("selection_score") or 0) <= 10 and _norm(row.get("topic_subject_he")) != _norm(top.get("topic_subject_he"))]
    if competing:
        return {"status": "ambiguous", "best": top, "competing": competing[:3]}
    return {"status": "selected", "best": top, "competing": scored[1:3]}


def _v3_hint_basis_rows(hint: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "matter_he": hint.get("matter_he"),
            "matter_display_he": hint.get("matter_display_he"),
            "action_type_he": hint.get("action_type_he"),
            "source_quote_he": hint.get("source_quote_he"),
            "candidate_id": hint.get("selected_candidate_id"),
            "selected_candidate": True,
        }
    ]
    selected_id = str(hint.get("selected_candidate_id") or "")
    for candidate in hint.get("event_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        rows.append(
            {
                "matter_he": candidate.get("matter_he"),
                "matter_display_he": candidate.get("matter_display_he"),
                "action_type_he": candidate.get("action_type_he"),
                "source_quote_he": _join_unique([candidate.get("matter_quote_he"), candidate.get("action_quote_he"), candidate.get("phase_quote_he"), candidate.get("decision_quote_he")]),
                "candidate_id": candidate_id or None,
                "selected_candidate": bool(selected_id and candidate_id == selected_id),
                "candidate_confidence": candidate.get("confidence"),
            }
        )
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = _norm(_join_unique([row.get("matter_he"), row.get("action_type_he"), row.get("source_quote_he")]))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _apply_topic_arbitration(*, assignments: list[dict[str, Any]], items: list[dict[str, Any]], enable_govmap_geo: bool) -> list[dict[str, Any]]:
    item_by_id = {str(item.get("structure_unit_id") or ""): item for item in items}
    assignment_by_id = {str(row.get("structure_unit_id") or ""): row for row in assignments}
    ordered_unit_ids = [str(item.get("structure_unit_id") or "") for item in items]
    order_by_id = {unit_id: index for index, unit_id in enumerate(ordered_unit_ids) if unit_id}
    out: list[dict[str, Any]] = []
    emitted_by_id: dict[str, dict[str, Any]] = {}
    for row in assignments:
        current = dict(row)
        item = item_by_id.get(str(current.get("structure_unit_id") or ""))
        if not item or not _is_protocol_item(item):
            out.append(current)
            if str(current.get("structure_unit_id") or ""):
                emitted_by_id[str(current.get("structure_unit_id") or "")] = current
            continue
        if str(item.get("topic_group_role") or "") == "merged_detail":
            out.append(current)
            emitted_by_id[str(current.get("structure_unit_id") or "")] = current
            continue
        evidence_reason = _evidence_only_arbitration_reason(row=current, item=item)
        proposal = _best_topic_arbitration_proposal(row=current, item=item, enable_govmap_geo=enable_govmap_geo)
        if evidence_reason and not _topic_proposal_overrides_evidence_only(proposal, reason=evidence_reason):
            context = None if evidence_reason in NON_INHERITABLE_EVIDENCE_REASONS else _select_inherited_topic_context(row=current, item=item, items=items, assignment_by_id=assignment_by_id, emitted_by_id=emitted_by_id, order_by_id=order_by_id)
            if context:
                updated = _assignment_from_inherited_topic_context(row=current, item=item, context=context, reason=evidence_reason)
            else:
                updated = _convert_assignment_to_non_topic_fragment(current, reason=f"topic_arbitration:{evidence_reason}")
                updated["topic_arbitration"] = {"decision": "evidence_only", "reason": evidence_reason}
            out.append(updated)
            emitted_by_id[str(updated.get("structure_unit_id") or "")] = updated
            continue
        if proposal and _topic_proposal_should_replace(row=current, proposal=proposal):
            updated = _assignment_from_topic_proposal(row=current, item=item, proposal=proposal)
            out.append(updated)
            emitted_by_id[str(updated.get("structure_unit_id") or "")] = updated
            continue
        compound = _compound_child_subject_arbitration_assignment(row=current, item=item)
        final_row = compound or current
        out.append(final_row)
        emitted_by_id[str(final_row.get("structure_unit_id") or "")] = final_row
    return out


def _best_topic_arbitration_proposal(*, row: dict[str, Any], item: dict[str, Any], enable_govmap_geo: bool) -> dict[str, Any] | None:
    proposals = _topic_arbitration_proposals(row=row, item=item, enable_govmap_geo=enable_govmap_geo)
    if not proposals:
        return None
    proposals.sort(key=_topic_proposal_sort_key, reverse=True)
    return proposals[0]


def _topic_proposal_sort_key(proposal: dict[str, Any]) -> tuple[float, int, float]:
    root_topic_id = str(proposal.get("root_topic_id") or "")
    # A location mention inside an action row is often secondary evidence; keep it only when it clearly wins.
    non_geo_tie_break = 0 if root_topic_id == "root_geo" else 1
    return (float(proposal.get("score") or 0.0), non_geo_tie_break, float(proposal.get("confidence") or 0.0))


def _topic_arbitration_proposals(*, row: dict[str, Any], item: dict[str, Any], enable_govmap_geo: bool) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    proposals.extend(_v3_topic_proposals(item=item))
    proposals.extend(_explicit_action_topic_proposals(row=row, item=item))
    proposals.extend(_candidate_span_topic_proposals(row=row, item=item))
    proposals.extend(_existing_assignment_topic_proposals(row=row, item=item))
    proposals.extend(_headline_topic_proposals(row=row, item=item))
    proposals.extend(_local_marker_topic_proposals(row=row, item=item))
    return _dedupe_topic_proposals(proposals=proposals, item=item, enable_govmap_geo=enable_govmap_geo)


def _v3_topic_proposals(*, item: dict[str, Any]) -> list[dict[str, Any]]:
    selection = _select_v3_fallback_hint(item=item)
    if not selection or selection.get("status") != "selected":
        return []
    hint = selection.get("best") if isinstance(selection.get("best"), dict) else {}
    subject = _clean_structured_fallback_subject(hint.get("topic_subject_he") or hint.get("matter_he") or hint.get("action_type_he"))
    root_topic_id = str(hint.get("root_topic_id") or "")
    if not subject or root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
        return []
    score = 94 if bool(hint.get("exact_unit_match")) else 82
    return [
        {
            "source": "v3_event_subject",
            "subject_he": subject,
            "root_topic_id": root_topic_id,
            "child_label_he": _existing_child_label_for_subject(root_topic_id=root_topic_id, subject=subject),
            "score": score,
            "confidence": 0.82 if score >= 90 else 0.68,
            "quote": _compact(hint.get("source_quote_he")) or _compact(item.get("unit_raw_text") or item.get("raw_text")),
            "v3_selection": selection,
        }
    ]


def _explicit_action_topic_proposals(*, row: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    evidence_text = _join_unique([item.get("unit_raw_text"), item.get("topic_headline_he"), item.get("topic_identification_context")])
    for action in item.get("explicit_actions") or []:
        proposal = _topic_proposal_from_text(
            row=row,
            item=item,
            text=str(action),
            evidence_text=_join_unique([action, evidence_text]),
            source="explicit_action_span",
            base_score=88,
        )
        if proposal:
            proposals.append(proposal)
    return proposals


def _candidate_span_topic_proposals(*, row: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    evidence_text = _join_unique([item.get("topic_subject_he"), item.get("topic_headline_he"), item.get("topic_identification_context"), item.get("unit_raw_text"), item.get("raw_text")])
    candidates = [candidate for candidate in item.get("root_topic_candidates") or [] if isinstance(candidate, dict)]
    for candidate in candidates[:8]:
        root_topic_id = str(candidate.get("root_topic_id") or "")
        if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals", "root_people_roles"}:
            continue
        score = float(candidate.get("score") or 0.0)
        matched_terms = [str(term) for term in candidate.get("matched_terms") or [] if str(term).strip()]
        child_label = clean_topic_label(candidate.get("child_label_he"))
        if score < 0.72 and not matched_terms and not child_label:
            continue
        span_text = _best_supported_candidate_span(text=evidence_text, candidate=candidate)
        if not span_text:
            continue
        proposal = _topic_proposal_from_text(
            row=row,
            item=item,
            text=span_text,
            evidence_text=evidence_text,
            source="classifier_candidate_span",
            base_score=min(90.0, 58.0 + score * 30.0),
            root_topic_id=root_topic_id,
            child_label=child_label,
        )
        if proposal:
            proposal["candidate"] = candidate
            proposals.append(proposal)
    for match in item.get("topic_policy_matches") or []:
        if not isinstance(match, dict):
            continue
        root_topic_id = str(match.get("root_topic_id") or "")
        if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
            continue
        span_text = _best_supported_candidate_span(text=evidence_text, candidate=match)
        if not span_text:
            continue
        proposal = _topic_proposal_from_text(
            row=row,
            item=item,
            text=span_text,
            evidence_text=evidence_text,
            source="topic_policy_span",
            base_score=86,
            root_topic_id=root_topic_id,
        )
        if proposal:
            proposal["policy_match"] = match
            proposals.append(proposal)
    return proposals


def _existing_assignment_topic_proposals(*, row: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    subject = _clean_structured_fallback_subject(row.get("topic_subject_he") or row.get("raw_topic_subject_he"))
    root_topic_id = str(row.get("root_topic_id") or "")
    if not subject or root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
        return []
    if root_topic_id == "root_geo" and not _resolve_geo_fallback(subject=subject, municipality=None, enable_govmap_geo=False):
        return []
    if is_low_quality_topic_label(subject):
        return []
    return [
        {
            "source": "existing_assignment_subject",
            "subject_he": subject,
            "root_topic_id": root_topic_id,
            "child_label_he": row.get("child_label_he") or _existing_child_label_for_subject(root_topic_id=root_topic_id, subject=subject),
            "score": 80 if str(row.get("topic_node_status") or "") == "active" else 68,
            "confidence": _confidence(row.get("topic_assignment_confidence")) or 0.7,
            "quote": _compact(row.get("topic_supporting_quote_he") or item.get("unit_raw_text") or item.get("raw_text")),
        }
    ]


def _headline_topic_proposals(*, row: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    evidence_text = _join_unique([item.get("topic_subject_he"), item.get("topic_headline_he"), item.get("topic_identification_context"), item.get("unit_raw_text")])
    raw_text = _compact(item.get("unit_raw_text"))
    rows: list[tuple[str, Any, int]] = [
        ("item_topic_subject", item.get("topic_subject_he"), 82),
        ("topic_headline", item.get("topic_headline_he"), 76),
        ("topic_identification_context", item.get("topic_identification_context"), 72),
    ]
    if raw_text and len(raw_text) <= 120 and len(_hebrew_tokens(raw_text)) <= 10:
        rows.insert(0, ("raw_short_heading", raw_text, 84))
    for source, text, score in rows:
        proposal = _topic_proposal_from_text(row=row, item=item, text=str(text or ""), evidence_text=evidence_text, source=source, base_score=score)
        if proposal:
            proposals.append(proposal)
    return proposals


def _local_marker_topic_proposals(*, row: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    # These are weak fallback proposals. They recover OCR carrier spans, but do not decide the topic alone.
    proposals: list[dict[str, Any]] = []
    evidence_text = _join_unique([item.get("unit_raw_text"), item.get("topic_headline_he"), item.get("topic_identification_context")])
    for subject in [_local_hendon_subject(item), _best_explicit_topic_subject(item), _local_committee_protocol_subject(item)]:
        base_score = 86 if subject else 66
        proposal = _topic_proposal_from_text(row=row, item=item, text=str(subject or ""), evidence_text=evidence_text, source="weak_carrier_span", base_score=base_score)
        if proposal:
            proposals.append(proposal)
    return proposals


def _topic_proposal_from_text(*, row: dict[str, Any], item: dict[str, Any], text: str, evidence_text: str, source: str, base_score: float, root_topic_id: str | None = None, child_label: str | None = None) -> dict[str, Any] | None:
    subject = _clean_structured_fallback_subject(text)
    if not subject:
        return None
    preliminary_root = _proposal_root_topic_id(row=row, item=item, subject=subject, evidence_text=subject, root_topic_id=root_topic_id)
    preliminary_root_label = root_label_for_id(preliminary_root) or ""
    canonical_subject, canonical_reason = canonicalize_topic_label(subject, root_label_he=preliminary_root_label, evidence_text=evidence_text)
    if canonical_subject:
        subject = canonical_subject
    elif is_low_quality_topic_label(subject):
        return None
    root_topic_id = _proposal_root_topic_id(row=row, item=item, subject=subject, evidence_text=evidence_text, root_topic_id=root_topic_id)
    if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals", "root_people_roles", "root_mayor_updates"}:
        return None
    root_label = root_label_for_id(root_topic_id) or ""
    if not _tokens_supported(subject, evidence_text) and not _tokens_supported(subject, text):
        return None
    if source == "classifier_candidate_span" and _looks_like_carrier_contaminated_subject(subject):
        base_score = max(60.0, float(base_score) - 14.0)
    if source == "explicit_action_span" and item.get("unit_raw_text") and not _tokens_supported(subject, str(item.get("unit_raw_text") or "")):
        base_score = max(66.0, float(base_score) - 8.0)
    child = child_label or _arbitrated_child_label(row=row, item=item, root_topic_id=root_topic_id, subject=subject)
    return {
        "source": source,
        "subject_he": subject,
        "root_topic_id": root_topic_id,
        "child_label_he": child,
        "score": float(base_score),
        "confidence": min(0.9, max(0.55, float(base_score) / 100.0)),
        "quote": _compact(text or evidence_text),
        "canonical_reason": canonical_reason,
    }


def _looks_like_carrier_contaminated_subject(subject: str) -> bool:
    normalized = _norm(subject)
    if not normalized:
        return False
    carrier_terms = {"סעיף", "שאילתה", "שאילתא", "הצעה", "פרוטוקול", "מצ ל"}
    if any(term in normalized for term in carrier_terms):
        return True
    return _subject_noise_score(subject) >= 5


def _proposal_root_topic_id(*, row: dict[str, Any], item: dict[str, Any], subject: str, evidence_text: str, root_topic_id: str | None) -> str:
    excluded_roots = {"root_agenda_queries", "root_order_proposals", "root_people_roles", "root_mayor_updates"}
    policy = topic_policy_matches(subject, limit=1)
    if policy:
        policy_root = str(policy[0].get("root_topic_id") or "")
        if policy_root in ROOT_BY_ID and policy_root not in excluded_roots:
            return policy_root
    inferred = infer_root_topic_id(subject, allow_procedural_default=False)
    if inferred in ROOT_BY_ID and inferred not in excluded_roots:
        return inferred
    if root_topic_id in ROOT_BY_ID and root_topic_id not in excluded_roots:
        if root_topic_id != "root_geo" or _resolve_geo_fallback(subject=subject, municipality=None, enable_govmap_geo=False):
            return str(root_topic_id)
    candidate = _best_candidate_for_subject(item=item, subject=subject)
    candidate_root = str((candidate or {}).get("root_topic_id") or "")
    if candidate_root in ROOT_BY_ID and candidate_root not in excluded_roots:
        if candidate_root != "root_geo" or _resolve_geo_fallback(subject=subject, municipality=None, enable_govmap_geo=False):
            return candidate_root
    inferred_from_evidence = infer_root_topic_id(_join_unique([subject, evidence_text]), allow_procedural_default=False)
    if inferred_from_evidence in ROOT_BY_ID and inferred_from_evidence not in excluded_roots:
        return inferred_from_evidence
    row_root = str(row.get("root_topic_id") or "")
    if row_root in ROOT_BY_ID and row_root not in excluded_roots:
        return row_root
    return inferred or inferred_from_evidence or row_root


def _best_supported_candidate_span(*, text: str, candidate: dict[str, Any]) -> str | None:
    text = _compact(text)
    if not text:
        return None
    terms = _candidate_terms(candidate)
    spans = _candidate_bounded_spans(text)
    if not terms:
        return spans[0] if spans else text[:220]
    scored: list[tuple[int, int, str]] = []
    for span in spans:
        span_norm = _norm(span)
        hits = sum(1 for term in terms if _norm(term) and _norm(term) in span_norm)
        if hits <= 0:
            continue
        scored.append((hits, -abs(len(_hebrew_tokens(span)) - 6), span))
    if scored:
        scored.sort(reverse=True)
        return scored[0][2]
    text_norm = _norm(text)
    if any(_norm(term) and _norm(term) in text_norm for term in terms):
        return text[:220]
    return None


def _candidate_terms(candidate: dict[str, Any]) -> list[str]:
    terms = [str(term) for term in candidate.get("matched_terms") or [] if str(term).strip()]
    for key in ("child_label_he", "label_he", "description_he"):
        value = str(candidate.get(key) or "").strip()
        if value:
            terms.append(value)
    return _dedupe_strings(terms)


def _candidate_bounded_spans(text: str) -> list[str]:
    raw = _compact(text)
    if not raw:
        return []
    quoted_spans = [match.group(1).strip(" '()[]-–:.,") for match in re.finditer(r"[\"׳״']([^\"׳״']{4,180})[\"׳״']", raw) if match.group(1).strip()]
    spans = [part.strip(" '()[]-–:.,") for part in re.split(r"\s+(?:\d+(?:\.\d+)?\s*[.)]\s*)|[;\n]|\s+[–-]\s+", raw) if part.strip()]
    spans = [*quoted_spans, *spans]
    if not spans:
        spans = [raw]
    out: list[str] = []
    for span in spans:
        if len(_hebrew_tokens(span)) > 18:
            pieces = [piece.strip(" '()[]-–:.,") for piece in re.split(r"[.]\s+|\s{2,}", span) if piece.strip()]
            out.extend(piece for piece in pieces if len(_hebrew_tokens(piece)) >= 2)
        elif len(_hebrew_tokens(span)) >= 2:
            out.append(span)
    return _dedupe_strings(out)[:16]


def _dedupe_topic_proposals(*, proposals: list[dict[str, Any]], item: dict[str, Any], enable_govmap_geo: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    municipality = str((item.get("document_context") or {}).get("municipality_he") or "") or None
    for proposal in proposals:
        subject = _clean_structured_fallback_subject(proposal.get("subject_he"))
        root_topic_id = str(proposal.get("root_topic_id") or "")
        if not subject or root_topic_id not in ROOT_BY_ID:
            continue
        if root_topic_id == "root_geo":
            geo = _resolve_geo_fallback(subject=subject, municipality=municipality, enable_govmap_geo=enable_govmap_geo)
            if not geo:
                continue
            proposal = {**proposal, "geo_resolution": geo, "child_label_he": str(geo.get("child_label_he") or proposal.get("child_label_he") or "כתובות ורחובות")}
        key = (root_topic_id, _norm(subject), str(proposal.get("child_label_he") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append({**proposal, "subject_he": subject, "root_topic_id": root_topic_id})
    return out


def _topic_proposal_should_replace(*, row: dict[str, Any], proposal: dict[str, Any]) -> bool:
    score = float(proposal.get("score") or 0.0)
    if score < 66:
        return False
    root_topic_id = str(row.get("root_topic_id") or "")
    proposed_root = str(proposal.get("root_topic_id") or "")
    current_subject = _compact(row.get("topic_subject_he"))
    route = str(row.get("topic_assignment_route") or "")
    if str(row.get("topic_node_status") or "") == "candidate" or str(row.get("topic_review_status") or "") == "needs_review":
        return True
    if row.get("is_topic_bearing") is not True or not current_subject:
        return True
    if root_topic_id in {"root_agenda_queries", "root_order_proposals", "root_people_roles", "root_mayor_updates"} and proposed_root not in {root_topic_id, "root_agenda_queries", "root_order_proposals"}:
        return True
    if "topic_subject_rejected" in route or "ambiguous" in route or "weak_candidate" in route:
        return True
    if is_low_quality_topic_label(current_subject):
        return True
    proposal_source = str(proposal.get("source") or "")
    if "strong_policy_match" in route and proposal_source in {"classifier_candidate_span", "weak_carrier_span"} and proposed_root != root_topic_id:
        return False
    if proposal_source in {"classifier_candidate_span", "raw_short_heading", "item_topic_subject", "topic_headline", "weak_carrier_span"}:
        headline = _norm(_join_unique([row.get("topic_headline_he"), row.get("topic_identification_context"), row.get("topic_supporting_quote_he")]))
        proposed_subject = _norm(proposal.get("subject_he"))
        if proposed_subject and proposed_subject in headline and _norm(current_subject) not in headline:
            return True
    if score >= 86 and proposed_root != root_topic_id and proposed_root not in {"root_geo", "root_people_roles"}:
        return True
    return False


def _topic_proposal_overrides_evidence_only(proposal: dict[str, Any] | None, *, reason: str | None = None) -> bool:
    if not proposal:
        return False
    if reason in {"numeric_or_partial_evidence", "amount_or_percentage_clause", "candidate_evidence_fragment"}:
        return False
    source = str(proposal.get("source") or "")
    if reason in NON_TOPIC_CARRIER_REASONS:
        return source in {"v3_event_subject", "explicit_action_span", "topic_policy_span"} and float(proposal.get("score") or 0.0) >= 76
    return source in {"v3_event_subject", "explicit_action_span", "topic_policy_span", "raw_short_heading", "item_topic_subject", "topic_headline", "weak_carrier_span"} and float(proposal.get("score") or 0.0) >= 76


def _assignment_from_topic_proposal(*, row: dict[str, Any], item: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    root_topic_id = str(proposal.get("root_topic_id") or "")
    root_label = root_label_for_id(root_topic_id) or ""
    subject = _clean_structured_fallback_subject(proposal.get("subject_he")) or ""
    source = str(proposal.get("source") or "proposal")
    if root_topic_id == "root_planning_building":
        compacted_subject = _generic_public_building_subject_compaction(_join_unique([subject, item.get("unit_raw_text"), item.get("raw_text"), item.get("topic_headline_he")]))
        if compacted_subject:
            subject = compacted_subject
    if root_topic_id == "root_allocations":
        compacted_subject = _generic_allocation_subject_compaction(_join_unique([subject, item.get("unit_raw_text"), item.get("raw_text"), item.get("topic_headline_he")]))
        if compacted_subject:
            subject = compacted_subject
    if source == "classifier_candidate_span" and _looks_like_carrier_contaminated_subject(subject):
        repaired_subject = _clean_structured_fallback_subject(_best_explicit_topic_subject(item))
        repaired_root = _proposal_root_topic_id(row=row, item=item, subject=repaired_subject or "", evidence_text=_join_unique([repaired_subject, item.get("unit_raw_text"), item.get("topic_headline_he")]), root_topic_id=root_topic_id) if repaired_subject else ""
        if repaired_subject and repaired_root == root_topic_id:
            subject = repaired_subject
            source = "classifier_candidate_span:clean_carrier_subject"
    child_label = clean_topic_label(proposal.get("child_label_he")) if proposal.get("child_label_he") else _existing_child_label_for_subject(root_topic_id=root_topic_id, subject=subject)
    status = _arbitrated_topic_status(row=row, item=item, subject=subject, child_label=child_label, root_topic_id=root_topic_id)
    if proposal.get("geo_resolution") and not _geo_resolution_is_backend_confirmed(proposal.get("geo_resolution") or {}) and not _has_confirming_v3_action_hint(item=item):
        status = "candidate"
    reject_reason = "non_blocking_topic_review:topic_arbitration:proposal_ambiguous" if status == "candidate" else None
    assignment = _assignment_payload(
        item=item,
        root_topic_id=root_topic_id,
        root_label=root_label,
        child_label=child_label,
        raw_child_label=child_label,
        status=status,
        reject_reason=reject_reason,
        aliases=[],
        confidence=float(proposal.get("confidence") or 0.7),
        quote=str(proposal.get("quote") or item.get("unit_raw_text") or item.get("raw_text") or "")[:500],
        route=f"deterministic_v4_topic_arbitration:{source}",
        rationale_he="topic proposal arbitration selected the best grounded subject/root evidence",
        parsed_contract={"is_topic_bearing": True, "topic_subject_he": subject, "clean_subject_he": subject},
    )
    assignment["topic_arbitration"] = {
        "decision": "topic_proposal",
        "source": source,
        "subject_he": subject,
        "root_topic_id": root_topic_id,
        "score": proposal.get("score"),
    }
    if proposal.get("geo_resolution"):
        assignment["geo_resolution"] = proposal.get("geo_resolution")
    if proposal.get("v3_selection"):
        assignment["v3_fallback_selection"] = proposal.get("v3_selection")
    if _has_confirming_v3_action_hint(item=item):
        assignment["v3_confirmed_subject"] = True
    return assignment


def _select_inherited_topic_context(*, row: dict[str, Any], item: dict[str, Any], items: list[dict[str, Any]], assignment_by_id: dict[str, dict[str, Any]], emitted_by_id: dict[str, dict[str, Any]], order_by_id: dict[str, int]) -> dict[str, Any] | None:
    unit_id = str(item.get("structure_unit_id") or row.get("structure_unit_id") or "")
    candidates: list[dict[str, Any]] = []
    for parent_id in _inherited_context_parent_ids(item):
        if parent_id == unit_id:
            continue
        context = _context_from_assignment_or_item(unit_id=parent_id, assignment_by_id=assignment_by_id, emitted_by_id=emitted_by_id, items=items, source="continuation_parent")
        if context:
            candidates.append(context)
    current_index = order_by_id.get(unit_id, -1)
    if current_index >= 0:
        for offset in range(1, 5):
            previous_index = current_index - offset
            if previous_index < 0:
                break
            previous = items[previous_index]
            if not _nearby_item_can_provide_context(current=item, candidate=previous):
                continue
            context = _context_from_assignment_or_item(unit_id=str(previous.get("structure_unit_id") or ""), assignment_by_id=assignment_by_id, emitted_by_id=emitted_by_id, items=items, source="nearby_previous_topic")
            if context:
                candidates.append(context)
        current_text = _norm(item.get("unit_raw_text") or item.get("raw_text"))
        current_page = item.get("source_page")
        if current_text and len(current_text) >= 20 and current_page is not None:
            for previous in reversed(items[:current_index]):
                if previous.get("source_page") != current_page:
                    continue
                previous_text = _norm(previous.get("unit_raw_text") or previous.get("raw_text"))
                if current_text not in previous_text:
                    continue
                context = _context_from_assignment_or_item(unit_id=str(previous.get("structure_unit_id") or ""), assignment_by_id=assignment_by_id, emitted_by_id=emitted_by_id, items=items, source="same_page_containing_row")
                if context:
                    candidates.append(context)
                    break
    explicit_context = _context_from_explicit_actions(item=item)
    if explicit_context and _item_allows_own_explicit_action_inheritance(item):
        candidates.append(explicit_context)
    if not candidates:
        return None
    candidates.sort(key=lambda context: float(context.get("score") or 0.0), reverse=True)
    return candidates[0]


def _inherited_context_parent_ids(item: dict[str, Any]) -> list[str]:
    return _dedupe_strings([str(item.get("continuation_of_unit_id") or ""), str(item.get("parent_agenda_unit_id") or "")])


def _item_allows_own_explicit_action_inheritance(item: dict[str, Any]) -> bool:
    if _inherited_context_parent_ids(item):
        return True
    return str(item.get("structural_role") or "") in {"continuation", "vote_or_result", "task_row"}


def _context_from_assignment_or_item(*, unit_id: str, assignment_by_id: dict[str, dict[str, Any]], emitted_by_id: dict[str, dict[str, Any]], items: list[dict[str, Any]], source: str) -> dict[str, Any] | None:
    if not unit_id:
        return None
    assignment = emitted_by_id.get(unit_id) or assignment_by_id.get(unit_id) or {}
    item = next((candidate for candidate in items if str(candidate.get("structure_unit_id") or "") == unit_id), None)
    candidates = [_context_from_assignment(assignment=assignment, source=source)]
    if item:
        candidates.extend([_context_from_explicit_actions(item=item), _context_from_item_subject(item=item, source=source)])
    valid = [candidate for candidate in candidates if candidate]
    if not valid:
        return None
    valid.sort(key=lambda context: float(context.get("score") or 0.0), reverse=True)
    return valid[0]


def _context_from_assignment(*, assignment: dict[str, Any], source: str) -> dict[str, Any] | None:
    root_topic_id = str(assignment.get("root_topic_id") or "")
    subject = _clean_structured_fallback_subject(assignment.get("topic_subject_he") or assignment.get("raw_topic_subject_he") or assignment.get("child_label_he"))
    if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals", "root_people_roles", "root_geo"}:
        return None
    if _assignment_has_mismatched_geo_child(assignment):
        return None
    if not subject or is_low_quality_topic_label(subject):
        return None
    return {
        "source": source,
        "source_unit_id": assignment.get("structure_unit_id"),
        "root_topic_id": root_topic_id,
        "subject_he": subject,
        "child_label_he": assignment.get("child_label_he"),
        "score": 86 if str(assignment.get("topic_node_status") or "") == "active" else 72,
        "quote": assignment.get("topic_supporting_quote_he"),
    }


def _assignment_has_mismatched_geo_child(assignment: dict[str, Any]) -> bool:
    root_topic_id = str(assignment.get("root_topic_id") or "")
    child = _norm(assignment.get("child_label_he"))
    if not child or root_topic_id == "root_geo":
        return False
    return child in {_norm("כתובות ורחובות"), _norm("כיכרות וצמתים"), _norm("שכונות ואזורים"), _norm("אתרים ומבני ציבור")}


def _context_from_explicit_actions(*, item: dict[str, Any]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for action in item.get("explicit_actions") or []:
        text = _compact(action)
        if not text:
            continue
        root_topic_id = infer_root_topic_id(text, allow_procedural_default=False)
        policy = topic_policy_matches(text, limit=1)
        if policy:
            root_topic_id = str(policy[0].get("root_topic_id") or root_topic_id or "")
        if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals", "root_people_roles", "root_geo"}:
            continue
        root_label = root_label_for_id(root_topic_id) or ""
        subject, _reason = canonicalize_topic_label(text, root_label_he=root_label, evidence_text=_join_unique([text, item.get("unit_raw_text")]))
        subject = _clean_structured_fallback_subject(subject or text)
        if not subject or is_low_quality_topic_label(subject):
            continue
        context = {
            "source": "explicit_action_context",
            "source_unit_id": item.get("structure_unit_id"),
            "root_topic_id": root_topic_id,
            "subject_he": subject,
            "child_label_he": _existing_child_label_for_subject(root_topic_id=root_topic_id, subject=subject),
            "score": 92,
            "quote": text,
        }
        if best is None or float(context["score"]) > float(best.get("score") or 0.0):
            best = context
    return best


def _context_from_item_subject(*, item: dict[str, Any], source: str) -> dict[str, Any] | None:
    subject = _clean_structured_fallback_subject(item.get("topic_subject_he") or item.get("topic_headline_he") or item.get("topic_identification_context"))
    if not subject or is_low_quality_topic_label(subject):
        return None
    root_topic_id = infer_root_topic_id(subject, allow_procedural_default=False)
    if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals", "root_people_roles", "root_geo"}:
        return None
    return {
        "source": source,
        "source_unit_id": item.get("structure_unit_id"),
        "root_topic_id": root_topic_id,
        "subject_he": subject,
        "child_label_he": _existing_child_label_for_subject(root_topic_id=root_topic_id, subject=subject),
        "score": 66,
        "quote": item.get("unit_raw_text"),
    }


def _nearby_item_can_provide_context(*, current: dict[str, Any], candidate: dict[str, Any]) -> bool:
    current_text = _norm(current.get("unit_raw_text") or current.get("raw_text"))
    candidate_text = _norm(candidate.get("unit_raw_text") or candidate.get("raw_text"))
    if current_text and candidate_text and len(current_text) >= 20:
        # Visual/OCR rows often split a short detail row out of a longer transcript row.
        # In that case the longer row is the safest available topic context.
        return current_text in candidate_text
    if not _item_allows_own_explicit_action_inheritance(current):
        return False
    if current.get("source_window_id") and current.get("source_window_id") == candidate.get("source_window_id"):
        return True
    if current.get("section_id") and current.get("section_id") == candidate.get("section_id"):
        return True
    return False


def _assignment_from_inherited_topic_context(*, row: dict[str, Any], item: dict[str, Any], context: dict[str, Any], reason: str) -> dict[str, Any]:
    root_topic_id = str(context.get("root_topic_id") or "")
    subject = _clean_structured_fallback_subject(context.get("subject_he")) or ""
    child_label = clean_topic_label(context.get("child_label_he")) if context.get("child_label_he") else _existing_child_label_for_subject(root_topic_id=root_topic_id, subject=subject)
    assignment = _assignment_payload(
        item=item,
        root_topic_id=root_topic_id,
        root_label=root_label_for_id(root_topic_id) or "",
        child_label=child_label,
        raw_child_label=child_label,
        status="active",
        reject_reason=None,
        aliases=[],
        confidence=0.62,
        quote=str(item.get("unit_raw_text") or item.get("raw_text") or context.get("quote") or "")[:500],
        route=f"deterministic_v4_topic_arbitration:inherited_context:{context.get('source') or 'context'}",
        rationale_he="dependent row inherited the closest grounded topic context instead of becoming topicless",
        parsed_contract={"is_topic_bearing": True, "topic_subject_he": subject, "clean_subject_he": subject},
    )
    assignment["row_type"] = "inherited_topic_context"
    assignment["topic_context_role"] = "dependent_detail"
    assignment["inherited_topic_from_unit_id"] = context.get("source_unit_id")
    assignment["topic_arbitration"] = {"decision": "inherited_context", "reason": reason, "source": context.get("source"), "subject_he": subject, "root_topic_id": root_topic_id}
    return assignment


def _local_topic_arbitration_assignment(*, row: dict[str, Any], item: dict[str, Any], enable_govmap_geo: bool) -> dict[str, Any] | None:
    local_subject = _local_hendon_subject(item) or _best_explicit_topic_subject(item) or _local_committee_protocol_subject(item)
    if not local_subject:
        return None
    subject = _clean_structured_fallback_subject(local_subject)
    if not subject or is_low_quality_topic_label(subject):
        return None
    geo = _resolve_geo_fallback(subject=subject, municipality=str((item.get("document_context") or {}).get("municipality_he") or "") or None, enable_govmap_geo=enable_govmap_geo)
    if geo:
        assignment = _assignment_payload(
            item=item,
            root_topic_id="root_geo",
            root_label=root_label_for_id("root_geo") or "מיקומים וגיאוגרפיה",
            child_label=str(geo.get("child_label_he") or "כתובות ורחובות"),
            raw_child_label=str(geo.get("child_label_he") or "כתובות ורחובות"),
            status="active" if _geo_resolution_is_backend_confirmed(geo) else "candidate",
            reject_reason=None if _geo_resolution_is_backend_confirmed(geo) else "non_blocking_topic_review:geo_fallback_unverified_location",
            aliases=[],
            confidence=0.76 if _geo_resolution_is_backend_confirmed(geo) else 0.58,
            quote=str(item.get("unit_raw_text") or item.get("raw_text") or "")[:500],
            route=f"deterministic_v4_topic_arbitration:local_subject:geo:{geo.get('source') or 'unknown'}",
            rationale_he="local topic evidence selected over weaker topic-pipeline proposal",
            parsed_contract={"is_topic_bearing": True, "topic_subject_he": subject, "clean_subject_he": subject},
        )
        assignment["geo_resolution"] = geo
        assignment["topic_arbitration"] = {"decision": "local_geo_subject", "subject_he": subject, "source": "local_topic_marker"}
        return assignment
    root_topic_id = _arbitrated_root_topic_id(row=row, item=item, subject=subject)
    if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
        return None
    root_label = root_label_for_id(root_topic_id) or ""
    child_label = _arbitrated_child_label(row=row, item=item, root_topic_id=root_topic_id, subject=subject)
    status = _arbitrated_topic_status(row=row, item=item, subject=subject, child_label=child_label)
    reject_reason = "non_blocking_topic_review:topic_arbitration:local_subject_ambiguous" if status == "candidate" else None
    assignment = _assignment_payload(
        item=item,
        root_topic_id=root_topic_id,
        root_label=root_label,
        child_label=child_label,
        raw_child_label=child_label,
        status=status,
        reject_reason=reject_reason,
        aliases=[],
        confidence=0.86 if status == "active" else 0.62,
        quote=str(item.get("unit_raw_text") or item.get("raw_text") or "")[:500],
        route="deterministic_v4_topic_arbitration:local_subject",
        rationale_he="local agenda/title evidence selected over inherited or noisy context",
        parsed_contract={"is_topic_bearing": True, "topic_subject_he": subject, "clean_subject_he": subject},
    )
    assignment["topic_arbitration"] = {"decision": "local_subject", "subject_he": subject, "root_topic_id": root_topic_id, "child_label_he": child_label}
    return assignment


def _best_explicit_topic_subject(item: dict[str, Any]) -> str | None:
    text = _preclean_topic_marker_text(_join_unique([item.get("unit_raw_text"), item.get("topic_headline_he"), item.get("topic_identification_context")]))
    candidates: list[str] = []
    if _has_explicit_local_topic_marker(text):
        for match in re.finditer(r"\bבנושא\b\s*[\"'׳״]?\s*(.{3,240})", text):
            tail = match.group(1)
            tail = re.split(r"\s+(?:גב[׳']?|גברת|מר|ד[\"”]?ר|עו[\"”]?ד)\b|\s+השאלה\b|\s+התשובה\b|\s+מצורפ|\s+מצ[\"׳״']?ל\b|[.;]\s", tail, maxsplit=1)[0]
            candidate = _clean_structured_fallback_subject(tail)
            if _looks_like_contract_party_fragment(candidate):
                continue
            if candidate and len(_hebrew_tokens(candidate)) >= 2:
                candidates.append(candidate)
    candidates.extend(_local_quoted_title_subjects(text))
    if not candidates:
        return None
    candidates = _dedupe_strings(candidates)
    candidates.sort(key=lambda value: (_subject_noise_score(value), abs(len(_hebrew_tokens(value)) - 5), len(value)))
    return candidates[0]


def _local_quoted_title_subjects(text: str) -> list[str]:
    compact = _compact(text)
    if not compact:
        return []
    out: list[str] = []
    patterns = [
        r"[\"׳״']([^\"׳״']{4,220})[\"׳״']",
        r"[\"׳״']\s*[.\-–]*\s*([\u0590-\u05FF][^\"׳״']{4,220})$",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, compact):
            candidate = match.group(1)
            if _looks_like_ocr_apostrophe_word_split(compact, quote_index=match.start(), candidate=candidate):
                continue
            candidate = re.split(r"\s*[-–]\s*(?:דיון|בקשת|לבקשת|עפ|עפ\"י|מצ[\"׳״']?ל)\b|\s+דיון\b|\s+בקשת\b|\s+לבקשת\b|\s*\(\s*\d", candidate, maxsplit=1)[0]
            candidate = _strip_attribution_tail(_clean_topic_subject(candidate))
            candidate = _clean_structured_fallback_subject(candidate)
            if not candidate or len(_hebrew_tokens(candidate)) < 2:
                continue
            if _looks_like_contract_party_fragment(candidate):
                continue
            if _looks_like_attribution_fragment(candidate) or not _is_substantive_topic_subject(candidate):
                continue
            out.append(candidate)
    return _dedupe_strings(out)


def _looks_like_contract_party_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    # OCR around מ"ר can create quoted fragments like "ר בין עיריית... לעמותת...".
    # Those are contract parties/details, not the municipal subject itself.
    return bool(re.match(r"^(?:ר\s+|מ\s*ר\s+)?בין\s+.+\s+ל(?:עמותת|חברת|חברה|תאגיד|עיריית|מועצת|משרד)\b", normalized))


def _looks_like_ocr_apostrophe_word_split(text: str, *, quote_index: int, candidate: str) -> bool:
    before = str(text[:quote_index] or "").rstrip()
    if not before or not candidate:
        return False
    previous = re.search(r"([\u0590-\u05FF]{2,})$", before)
    first = re.match(r"\s*([\u0590-\u05FF]+)", str(candidate))
    if not previous or not first:
        return False
    preceding_context = before[-40:]
    if re.search(r"(?:בנושא|הנדון|נושא|שאילת[אה]|שאילתה|הצעה\s+לסדר)\s*$", preceding_context):
        return False
    # OCR sometimes splits one Hebrew word with an apostrophe-like mark, e.g. עירו 'נית.
    return len(previous.group(1)) <= 6 and len(first.group(1)) <= 6


def _preclean_topic_marker_text(value: Any) -> str:
    text = _compact(value)
    text = re.sub(r"\bבנו\s+[\"'׳״]?שא[\"'׳״]?\b", "בנושא", text)
    # OCR often inserts a footnote number and a quote in the middle of the quoted subject.
    text = re.sub(r"(?<=[\u0590-\u05FF])\s*\d{1,2}\s*[\"'׳״]+\s*(?=[\u0590-\u05FF])", " ", text)
    text = text.replace("”", '"').replace("“", '"')
    return _compact(text)


def _subject_noise_score(value: str) -> int:
    text = _compact(value)
    score = 0
    score += 8 if re.search(r"\b(?:גב[׳']?|גברת|מר|ד[\"”]?ר|עו[\"”]?ד)\b", text) else 0
    score += 5 if any(term in text for term in ("השאלה", "התשובה", "מצורפ", "פרוטוקול")) else 0
    score += 3 if len(_hebrew_tokens(text)) > 12 else 0
    score += text.count('"') + text.count("׳") + text.count("״")
    return score


def _local_hendon_subject(item: dict[str, Any]) -> str | None:
    raw = _preclean_topic_marker_text(item.get("unit_raw_text") or item.get("raw_text"))
    match = re.search(r"^\s*:?\s*(?:הנדון|נידון|נדון)\s*[:'\"׳״\-–]?\s*(.{3,160})", raw)
    if not match:
        return None
    subject = match.group(1)
    if "להלן רשימת" in subject or "רשימת ההישגים" in subject:
        return None
    subject = re.split(r"[.;]\s|\s+להלן\b|\s+מצ[\"׳״']?ל\b", subject, maxsplit=1)[0]
    return _clean_structured_fallback_subject(subject)


def _local_committee_protocol_subject(item: dict[str, Any]) -> str | None:
    raw = _preclean_topic_marker_text(item.get("unit_raw_text") or item.get("raw_text"))
    match = re.search(r"(?:פרוטוקול\s+)?מישיבת\s+(ועדת\s+.{3,100}?)(?=\s+מס\b|\s+מיום\b|\s+מתאריך\b|\s+ד[\"”]?ר\b|\s+גב[׳']?\b|[.;–-]|$)", raw)
    if not match:
        return None
    return _clean_structured_fallback_subject(match.group(1))


def _arbitrated_root_topic_id(*, row: dict[str, Any], item: dict[str, Any], subject: str) -> str:
    candidate = _best_candidate_for_subject(item=item, subject=subject)
    if candidate and str(candidate.get("root_topic_id") or "") in ROOT_BY_ID:
        return str(candidate.get("root_topic_id"))
    inferred = infer_root_topic_id(subject, allow_procedural_default=False)
    if inferred in ROOT_BY_ID and inferred not in {"root_agenda_queries", "root_order_proposals"}:
        return inferred
    row_root = str(row.get("root_topic_id") or "")
    if row_root in ROOT_BY_ID and row_root not in {"root_agenda_queries", "root_order_proposals", "root_mayor_updates"}:
        return row_root
    return inferred or row_root


def _arbitrated_child_label(*, row: dict[str, Any], item: dict[str, Any], root_topic_id: str, subject: str) -> str | None:
    candidate = _best_candidate_for_subject(item=item, subject=subject, root_topic_id=root_topic_id)
    child = clean_topic_label((candidate or {}).get("child_label_he")) if candidate else None
    if child:
        return child
    return _existing_child_label_for_subject(root_topic_id=root_topic_id, subject=subject)


def _best_candidate_for_subject(*, item: dict[str, Any], subject: str, root_topic_id: str | None = None) -> dict[str, Any] | None:
    subject_norm = _norm(subject)
    best: tuple[float, dict[str, Any]] | None = None
    for candidate in item.get("root_topic_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        candidate_root = str(candidate.get("root_topic_id") or "")
        if candidate_root in {"root_agenda_queries", "root_order_proposals"}:
            continue
        if candidate_root == "root_people_roles" and not _resolve_people_role_fallback(subject):
            continue
        if root_topic_id and str(candidate.get("root_topic_id") or "") != root_topic_id:
            continue
        label = _norm(candidate.get("child_label_he") or "")
        terms = _norm(" ".join(str(term) for term in candidate.get("matched_terms") or []))
        overlap = len(set(_hebrew_tokens(subject_norm)) & set(_hebrew_tokens(" ".join([label, terms]))))
        score = float(candidate.get("score") or 0.0) + overlap / 10.0
        if label and (label in subject_norm or subject_norm in label):
            score += 0.4
        if overlap <= 0 and not (label and (label in subject_norm or subject_norm in label)):
            continue
        if best is None or score > best[0]:
            best = (score, candidate)
    return best[1] if best else None


def _arbitrated_topic_status(*, row: dict[str, Any], item: dict[str, Any], subject: str, child_label: str | None, root_topic_id: str | None = None) -> str:
    selected_root_topic_id = str(root_topic_id or row.get("root_topic_id") or "")
    if _has_confirming_v3_action_hint(item=item):
        return "active"
    if selected_root_topic_id in ROOT_BY_ID and selected_root_topic_id not in {"root_geo", "root_people_roles", "root_agenda_queries", "root_order_proposals"} and child_label:
        return "active"
    if _resolve_geo_fallback(subject=subject, municipality=None, enable_govmap_geo=False):
        return "candidate"
    if _looks_like_multifacet_subject(subject):
        return "candidate"
    if child_label or infer_root_topic_id(subject, allow_procedural_default=False) in ROOT_BY_ID:
        return "active"
    return str(row.get("topic_node_status") or "candidate") if str(row.get("topic_node_status") or "") == "active" else "candidate"


def _has_confirming_v3_action_hint(*, item: dict[str, Any]) -> bool:
    raw = _join_unique([item.get("unit_raw_text"), item.get("raw_text"), item.get("topic_identification_context"), item.get("topic_headline_he")])
    unit_id = str(item.get("structure_unit_id") or "")
    for hint in item.get("topic_subject_v3_hints") or []:
        if not isinstance(hint, dict):
            continue
        matter = _clean_structured_fallback_subject(hint.get("matter_he"))
        action = _clean_structured_fallback_subject(hint.get("action_type_he"))
        if not matter or not action:
            continue
        if str(hint.get("source_structure_unit_id") or "") == unit_id:
            return True
        quote = _compact(hint.get("source_quote_he"))
        if quote and (_tokens_supported(quote, raw) or _tokens_supported(raw, quote)):
            return True
    return False


def _looks_like_multifacet_subject(subject: str) -> bool:
    normalized = _norm(subject)
    if not normalized:
        return False
    tokens = _hebrew_tokens(normalized)
    separators = subject.count(",") + subject.count(";") + subject.count("/")
    if separators >= 1 and len(tokens) >= 5:
        return True
    conjunctions = len(re.findall(r"(?:^|\s)ו[\u0590-\u05FF]{2,}", normalized))
    return conjunctions >= 2 and len(tokens) >= 5


def _compound_child_subject_arbitration_assignment(*, row: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("is_topic_bearing") is not True:
        return None
    subject = _compact(row.get("topic_subject_he"))
    if not subject or len(_hebrew_tokens(subject)) > 3:
        return None
    evidence_text = _join_unique([item.get("unit_raw_text"), item.get("topic_identification_context"), item.get("topic_headline_he")])
    candidate = _best_candidate_for_subject(item=item, subject=_join_unique([subject, evidence_text]))
    child = clean_topic_label((candidate or {}).get("child_label_he")) if candidate else None
    root_topic_id = str((candidate or {}).get("root_topic_id") or row.get("root_topic_id") or "")
    if not child or root_topic_id not in ROOT_BY_ID:
        return None
    current_root_topic_id = str(row.get("root_topic_id") or "")
    route = str(row.get("topic_assignment_route") or "")
    if "strong_policy_match" in route and current_root_topic_id in ACTION_DOMINANT_ROOT_IDS and root_topic_id != current_root_topic_id and root_topic_id not in ACTION_DOMINANT_ROOT_IDS:
        return None
    if (
        root_topic_id == "root_geo"
        and current_root_topic_id in ROOT_BY_ID
        and current_root_topic_id not in {"root_geo", "root_people_roles", "root_agenda_queries", "root_order_proposals"}
        and _tokens_supported(subject, evidence_text)
    ):
        return None
    if len(_hebrew_tokens(child)) < 2 or _norm(subject) == _norm(child):
        return None
    subject_for_assignment = subject if _should_preserve_action_scoped_subject(subject=subject, child=child) else child
    assignment = _assignment_payload(
        item=item,
        root_topic_id=root_topic_id,
        root_label=root_label_for_id(root_topic_id) or "",
        child_label=child,
        raw_child_label=child,
        status="active",
        reject_reason=None,
        aliases=_dedupe_strings([subject, child]),
        confidence=max(0.72, _confidence((candidate or {}).get("score"))),
        quote=str(item.get("unit_raw_text") or item.get("raw_text") or "")[:500],
        route="deterministic_v4_topic_arbitration:compound_child_subject",
        rationale_he="existing child topic preserved a compound municipal subject that the subject cleaner over-compressed",
        parsed_contract={"is_topic_bearing": True, "topic_subject_he": subject_for_assignment, "clean_subject_he": subject_for_assignment},
    )
    assignment["topic_arbitration"] = {"decision": "compound_child_subject", "previous_subject_he": subject, "subject_he": subject_for_assignment, "child_label_he": child}
    return assignment


def _should_preserve_action_scoped_subject(*, subject: str, child: str) -> bool:
    subject_norm = _norm(subject)
    child_norm = _norm(child)
    if not subject_norm or not child_norm or child_norm not in subject_norm or subject_norm == child_norm:
        return False
    action_terms = ("בדיקת", "בדיקה", "טיפול", "תיקון", "הקמת", "הקמה", "שדרוג", "הוספת", "מינוי", "אישור", "הסדרת")
    return any(subject_norm.startswith(f"{term} ") or f" {term} " in subject_norm for term in action_terms)


def _evidence_only_arbitration_reason(*, row: dict[str, Any], item: dict[str, Any]) -> str | None:
    raw = _compact(item.get("unit_raw_text") or item.get("raw_text") or row.get("topic_supporting_quote_he") or row.get("topic_identification_context"))
    if not raw:
        return None
    normalized = _norm(raw)
    role = str(item.get("structural_role") or row.get("structural_role") or "")
    non_topic_reason = str(item.get("non_topic_reason") or "")
    if role == "continuation" and ("להלן רשימת" in normalized or "רשימת ההישגים" in normalized):
        return "attachment_or_list_heading"
    if non_topic_reason in NON_INHERITABLE_EVIDENCE_REASONS:
        return non_topic_reason
    if _has_explicit_local_topic_marker(raw) or _local_hendon_subject(item) or _local_committee_protocol_subject(item):
        return None
    if _looks_like_procedural_packet_carrier_only(raw):
        return "procedural_packet_carrier"
    if _looks_like_numeric_or_partial_evidence_fragment(row={**row, "topic_identification_context": raw}, subject=str(row.get("topic_subject_he") or ""), text=raw):
        return "numeric_or_partial_evidence"
    if re.search(r"\bולפיו\b.*(?:%|אחוז|עלות|סכום)", normalized) and not _has_bounded_substantive_topic_signal(raw):
        return "amount_or_percentage_clause"
    if "?" in raw and re.search(r"(?:ד[\"”]?ר|עו[\"”]?ד|אינג[׳']?|גב[׳']?)", raw) and not any(term in normalized for term in ("בנושא", "שאילתה", "הנדון")):
        return "speaker_qa_dependent_detail"
    if _looks_like_reply_attachment_tail(raw) and not _has_bounded_substantive_topic_signal(raw):
        return "reply_attachment_dependent_detail"
    if str(row.get("topic_node_status") or "") == "candidate" and row.get("is_topic_bearing") is False and re.match(r"^(?:\d|כ\s*\d|[%₪])", raw):
        return "candidate_evidence_fragment"
    return None


def _assignment_payload(*, item: dict[str, Any], root_topic_id: str, root_label: str, child_label: str | None, raw_child_label: str | None, status: str, reject_reason: str | None, aliases: list[str], confidence: float, quote: str, route: str, rationale_he: str, parsed_contract: dict[str, Any] | None = None, root_adjudication: dict[str, Any] | None = None) -> dict[str, Any]:
    parsed_contract = parsed_contract or {}
    root_adjudication = root_adjudication or {}
    is_topic_bearing = bool(_contract_value(parsed_contract, item, "is_topic_bearing"))
    row_type = item.get("row_type")
    if not is_topic_bearing and row_type == "topic_item":
        row_type = "fragment"
    raw_topic_subject = _contract_value(parsed_contract, item, "topic_subject_he") if is_topic_bearing else None
    topic_subject = raw_topic_subject
    canonical_reason = None
    if is_topic_bearing and raw_topic_subject:
        canonical_subject, canonical_reason = canonicalize_topic_label(
            raw_topic_subject,
            root_label_he=root_label,
            evidence_text=_join_unique([raw_topic_subject, item.get("topic_headline_he"), item.get("raw_text"), quote]),
        )
        if canonical_subject:
            topic_subject = canonical_subject
            if _norm(canonical_subject) != _norm(raw_topic_subject):
                aliases = _dedupe_strings([*aliases, str(raw_topic_subject)])
                route = f"{route}:canonical_topic_label"
                canonical_root_topic_id = infer_root_topic_id(canonical_subject, allow_procedural_default=False)
                full_evidence_root_topic_id = infer_root_topic_id(
                    _join_unique([raw_topic_subject, item.get("topic_headline_he"), item.get("raw_text"), quote]),
                    allow_procedural_default=False,
                )
                if (
                    canonical_root_topic_id in ROOT_BY_ID
                    and canonical_root_topic_id not in {"root_agenda_queries", "root_order_proposals"}
                    and canonical_root_topic_id != root_topic_id
                    and full_evidence_root_topic_id != root_topic_id
                    and root_topic_id not in {"root_geo", "root_people_roles"}
                    and "topic_arbitration:local_subject" not in route
                    and not root_adjudication.get("policy_id")
                    and not _has_valid_dicta_root_without_policy(root_adjudication)
                ):
                    root_topic_id = canonical_root_topic_id
                    root_label = root_label_for_id(root_topic_id) or root_label
                    root_adjudication = {
                        **root_adjudication,
                        "root_topic_id": root_topic_id,
                        "adjudicated_root_label_he": root_label,
                        "resolved_root_topic_id": root_topic_id,
                        "resolved_root_label_he": root_label,
                        "decision": f"{root_adjudication.get('decision') or 'unknown'}:canonical_root_reparent",
                    }
                    route = f"{route}:canonical_root_reparent"
        else:
            repaired_subject, repair_reason = _repair_rejected_topic_subject(raw_subject=raw_topic_subject, item=item, root_label=root_label, quote=quote)
            if repaired_subject:
                topic_subject = repaired_subject
                aliases = _dedupe_strings([*aliases, str(raw_topic_subject)])
                canonical_reason = repair_reason
                route = f"{route}:topic_subject_repaired"
            else:
                topic_subject = None
                child_label = None
                raw_child_label = None
                subject_reject_reason = f"topic_subject_rejected:{canonical_reason or 'no_canonical_label'}"
                if "contextual" in route and root_topic_id in ROOT_BY_ID and root_topic_id not in {"root_agenda_queries", "root_order_proposals"}:
                    status = "candidate"
                    reject_reason = reject_reason or f"non_blocking_topic_review:{subject_reject_reason}"
                else:
                    is_topic_bearing = False
                    row_type = "fragment"
                    reject_reason = reject_reason or subject_reject_reason
                route = f"{route}:topic_subject_rejected"
    if is_topic_bearing and topic_subject and not child_label:
        matched_child = _existing_child_label_for_subject(root_topic_id=root_topic_id, subject=topic_subject)
        if matched_child:
            child_label = matched_child
            raw_child_label = raw_child_label or topic_subject
            route = f"{route}:subject_matched_existing_child"
    child_id = child_topic_id(root_topic_id, child_label) if child_label else None
    secondary_roots = secondary_topic_roots(
        root_topic_id=root_topic_id,
        child_label_he=child_label or topic_subject,
        evidence_text=_join_unique([topic_subject, child_label, raw_child_label, quote, item.get("topic_identification_context"), item.get("topic_headline_he"), item.get("raw_text")]),
    ) if is_topic_bearing else []
    return {
        "structure_unit_id": item["structure_unit_id"],
        "semantic_unit_id": item["semantic_unit_id"],
        "source_window_id": item.get("source_window_id"),
        "source_region_ids": item.get("source_region_ids") or [],
        "source_block_ids": item.get("source_block_ids") or [],
        "source_page": item.get("source_page"),
        "structural_role": item.get("structural_role"),
        "section_id": item.get("section_id"),
        "section_number": item.get("section_number"),
        "continuation_of_unit_id": item.get("continuation_of_unit_id"),
        "row_type": row_type,
        "skip_model_assignment": bool(item.get("skip_model_assignment")),
        "packet_role": str((item.get("document_context") or {}).get("packet_role") or ""),
        "topic_identification_context": item.get("topic_identification_context"),
        "topic_headline_he": item.get("topic_headline_he"),
        "agenda_carrier_he": _contract_value(parsed_contract, item, "agenda_carrier_he"),
        "topic_subject_he": topic_subject,
        "raw_topic_subject_he": raw_topic_subject,
        "topic_subject_canonicalization_reason": canonical_reason,
        "attribution_he": _contract_value(parsed_contract, item, "attribution_he"),
        "is_topic_bearing": is_topic_bearing,
        "agenda_item_title_he": item.get("agenda_item_title_he"),
        "parent_agenda_unit_id": item.get("parent_agenda_unit_id"),
        "topic_context_source": item.get("topic_context_source"),
        "topic_headline_source": item.get("topic_headline_source"),
        "topic_provenance_reject_reason": item.get("topic_provenance_reject_reason"),
        "topic_anchor_quote_he": item.get("topic_anchor_quote_he"),
        "protocol_subject_he": item.get("protocol_subject_he"),
        "topic_carrier_mode": item.get("topic_carrier_mode"),
        "topic_group_id": item.get("topic_group_id"),
        "topic_group_role": item.get("topic_group_role"),
        "merged_into_unit_id": item.get("merged_into_unit_id"),
        "topic_group_source_unit_ids": item.get("topic_group_source_unit_ids") or [],
        "merged_detail_unit_ids": item.get("merged_detail_unit_ids") or [],
        "merged_detail_texts": item.get("merged_detail_texts") or [],
        "topic_tree_version": TOPIC_TREE_VERSION,
        "topic_assignment_backend": TOPIC_ASSIGNMENT_BACKEND,
        "root_topic_id": root_topic_id,
        "root_label_he": root_label,
        "secondary_topic_roots": secondary_roots,
        "secondary_topic_ids": [str(row.get("root_topic_id") or "") for row in secondary_roots if str(row.get("root_topic_id") or "")],
        "secondary_topics": [str(row.get("root_label_he") or "") for row in secondary_roots if str(row.get("root_label_he") or "")],
        "dicta_root_topic_id": root_adjudication.get("dicta_root_topic_id"),
        "dicta_root_label_he": root_adjudication.get("dicta_root_label_he"),
        "fallback_root_topic_id": root_adjudication.get("fallback_root_topic_id"),
        "fallback_root_label_he": root_adjudication.get("fallback_root_label_he"),
        "adjudicated_root_topic_id": root_adjudication.get("root_topic_id") or root_topic_id,
        "adjudicated_root_label_he": root_adjudication.get("adjudicated_root_label_he") or root_label,
        "resolved_root_topic_id": root_adjudication.get("resolved_root_topic_id") or root_topic_id,
        "resolved_root_label_he": root_adjudication.get("resolved_root_label_he") or root_label,
        "root_adjudication_decision": root_adjudication.get("decision"),
        "topic_policy_id": root_adjudication.get("policy_id"),
        "topic_policy_description_he": root_adjudication.get("policy_description_he"),
        "topic_policy_matched_terms": root_adjudication.get("matched_terms") or [],
        "topic_policy_matches": item.get("topic_policy_matches") or root_adjudication.get("policy_matches") or [],
        "dicta_policy_id": root_adjudication.get("dicta_policy_id"),
        "unknown_dicta_root_topic_id": root_adjudication.get("unknown_dicta_root_topic_id"),
        "dicta_raw_is_topic_bearing": parsed_contract.get("dicta_raw_is_topic_bearing"),
        "dicta_raw_subject_he": parsed_contract.get("dicta_raw_subject_he"),
        "dicta_clean_subject_he": parsed_contract.get("dicta_clean_subject_he"),
        "dicta_raw_root_topic_id": parsed_contract.get("dicta_raw_root_topic_id"),
        "dicta_raw_root_label_he": parsed_contract.get("dicta_raw_root_label_he"),
        "dicta_confidence": parsed_contract.get("dicta_confidence"),
        "dicta_rationale_he": parsed_contract.get("dicta_rationale_he"),
        "dicta_contextual_schema_valid": parsed_contract.get("dicta_contextual_schema_valid"),
        "dicta_contextual_event_status": parsed_contract.get("dicta_contextual_event_status"),
        "dicta_contextual_agenda_disposition": parsed_contract.get("dicta_contextual_agenda_disposition"),
        "dicta_contextual_indexability_status": parsed_contract.get("dicta_contextual_indexability_status"),
        "dicta_contextual_original_indexability_status": parsed_contract.get("dicta_contextual_original_indexability_status"),
        "dicta_contextual_indexability_override_reason": parsed_contract.get("dicta_contextual_indexability_override_reason"),
        "dicta_contextual_classification_basis": parsed_contract.get("dicta_contextual_classification_basis"),
        "dicta_contextual_missing_optional_fields": parsed_contract.get("dicta_contextual_missing_optional_fields") or [],
        "dicta_contextual_rationale_fallback": parsed_contract.get("dicta_contextual_rationale_fallback"),
        "dicta_contextual_competing_roots": parsed_contract.get("dicta_contextual_competing_roots") or [],
        "dicta_contextual_standalone_event": parsed_contract.get("dicta_contextual_standalone_event"),
        "dicta_contextual_consistency_warning": parsed_contract.get("dicta_contextual_consistency_warning"),
        "dicta_contextual_event_summary_he": parsed_contract.get("dicta_contextual_event_summary_he"),
        "dicta_contextual_agenda_status_he": parsed_contract.get("dicta_contextual_agenda_status_he"),
        "dicta_contextual_normalization_confidence": parsed_contract.get("dicta_contextual_normalization_confidence"),
        "dicta_contextual_original_root_topic_id": parsed_contract.get("dicta_contextual_original_root_topic_id"),
        "dicta_contextual_override_root_topic_id": parsed_contract.get("dicta_contextual_override_root_topic_id"),
        "root_topic_candidates": item.get("root_topic_candidates") or [],
        "deterministic_topic_decision": item.get("deterministic_topic_decision") or {},
        "deterministic_classifier_method": item.get("deterministic_classifier_method"),
        "topic_review_status": "needs_review" if status == "candidate" and reject_reason else None,
        "model_clean_subject_he": parsed_contract.get("clean_subject_he"),
        "model_primary_action_he": parsed_contract.get("primary_action_he"),
        "model_service_domain_he": parsed_contract.get("service_domain_he"),
        "model_classification_basis": parsed_contract.get("classification_basis"),
        "model_why_not_other_roots_he": parsed_contract.get("why_not_other_roots_he"),
        "model_duplicate_of_unit_id": parsed_contract.get("duplicate_of_unit_id"),
        "model_duplicate_evidence_quote_he": parsed_contract.get("duplicate_evidence_quote_he"),
        "child_topic_id": child_id,
        "child_label_he": child_label,
        "raw_child_label_he": raw_child_label,
        "topic_node_status": status,
        "topic_reject_reason": reject_reason,
        "topic_aliases_he": aliases,
        "topic_assignment_route": route,
        "topic_assignment_confidence": max(0.0, min(1.0, float(confidence))),
        "topic_supporting_quote_he": quote[:500],
        "topic_evidence_span_ids": [],
        "referenced_attachment_contexts": item.get("referenced_attachment_contexts") or [],
        "rationale_he": rationale_he,
    }


def _has_valid_dicta_root_without_policy(root_adjudication: dict[str, Any]) -> bool:
    dicta_root_topic_id = str(root_adjudication.get("dicta_root_topic_id") or "")
    if dicta_root_topic_id not in ROOT_BY_ID:
        return False
    if root_adjudication.get("policy_id"):
        return False
    decision = str(root_adjudication.get("decision") or "")
    return "dicta_root" in decision or "dicta_authoritative_root" in decision or "dicta_contextual_root" in decision


def _repair_rejected_topic_subject(*, raw_subject: Any, item: dict[str, Any], root_label: str, quote: str) -> tuple[str | None, str | None]:
    evidence = _join_unique([raw_subject, item.get("topic_headline_he"), item.get("topic_identification_context"), item.get("raw_text"), quote])
    candidates = _dedupe_strings(
        [
            item.get("topic_subject_he"),
            item.get("topic_headline_he"),
            item.get("topic_identification_context"),
            _generic_subject_prefix(raw_subject),
            _generic_subject_prefix(quote),
            _generic_allocation_subject_compaction(raw_subject),
            _generic_allocation_subject_compaction(item.get("topic_headline_he")),
            _generic_allocation_subject_compaction(quote),
            *_generic_action_subject_candidates(raw_subject),
            *_generic_action_subject_candidates(item.get("topic_headline_he")),
            *_generic_action_subject_candidates(quote),
            _generic_agreement_subject_compaction(raw_subject, root_label=root_label),
            _generic_agreement_subject_compaction(item.get("topic_headline_he"), root_label=root_label),
            _generic_agreement_subject_compaction(quote, root_label=root_label),
            _generic_budget_subject_compaction(raw_subject, root_label=root_label),
            _generic_budget_subject_compaction(item.get("topic_headline_he"), root_label=root_label),
            _generic_budget_subject_compaction(quote, root_label=root_label),
            _generic_public_building_subject_compaction(raw_subject),
            _generic_public_building_subject_compaction(item.get("topic_headline_he")),
            _generic_public_building_subject_compaction(quote),
        ]
    )
    for candidate in candidates:
        cleaned, reason = canonicalize_topic_label(candidate, root_label_he=root_label, evidence_text=evidence)
        if not cleaned:
            continue
        if _norm(cleaned) == _norm(root_label):
            continue
        if not _tokens_supported(cleaned, evidence) and not _tokens_supported(str(candidate), evidence):
            continue
        return cleaned, f"subject_repaired:{reason or 'alternate_source'}"
    return None, None


def _generic_agreement_subject_compaction(value: Any, *, root_label: str) -> str:
    text = _compact(value)
    normalized = _norm(_join_unique([root_label, text]))
    if not text or not any(term in normalized for term in ("הסכם", "התקשרות", "חוזה", "רשות שימוש")):
        return ""
    qualified = _qualified_agreement_phrase(text)
    if qualified:
        return qualified
    if any(term in normalized for term in ("עמותה", "עמותת", "עמותות", "מלכ ר", "חל צ")):
        if any(term in normalized for term in ("גני ילדים", "גן ילדים", "בית ספר", "כיתות", "חינוך", "תורני")):
            return "הסכם רשות למוסדות חינוך"
        return "הסכם עם עמותה"
    if "חברה עירונית" in normalized:
        return "הסכם עם חברה עירונית"
    if "רשות שימוש" in normalized:
        return "הסכם רשות שימוש"
    if "שכירות" in normalized:
        if any(term in normalized for term in ("מתקן שידור", "מתקני שידור", "אנטנה", "אנטנות")):
            return "הסכם שכירות למתקן שידור"
        if "מתקן" in normalized:
            return "הסכם שכירות למתקן"
    if any(term in normalized for term in ("גוש", "חלקה", "מקרקעין", "נכס", "מבנה")):
        return "הסכם שימוש במקרקעין"
    if "ללא מכרז" in normalized or "פטור ממכרז" in normalized:
        return "התקשרות ללא מכרז"
    return "אישור הסכם" if "אישור" in normalized or "לאשר" in normalized else "הסכם"


def _qualified_agreement_phrase(value: Any) -> str:
    text = _compact(value)
    match = re.search(r"\b(?:אישור\s+)?(הסכם|התקשרות|חוזה)\s+([\w\u0590-\u05FF'\"׳״-]+(?:\s+[\w\u0590-\u05FF'\"׳״-]+){0,3})", text)
    if not match:
        return ""
    phrase = _compact(match.group(0))
    words = phrase.split()
    if len(words) > 2:
        kept = words[:2]
        for word in words[2:]:
            normalized_word = _norm(word)
            if normalized_word in {"עד", "עכשיו", "לאחר", "לפני"} or (normalized_word.startswith("ו") and len(normalized_word) > 1):
                break
            kept.append(word)
        phrase = _compact(" ".join(kept))
    normalized = _norm(phrase)
    if any(term in normalized for term in ("בין", "עם", "ללא", "פטור", "עיריית", "העירייה")):
        return ""
    if len(_hebrew_tokens(phrase)) < 2 or len(_hebrew_tokens(phrase)) > 5:
        return ""
    return phrase.strip(" '()[]-–:.,")


def _generic_public_building_subject_compaction(value: Any) -> str:
    text = _compact(value)
    normalized = _norm(text)
    if not normalized or not any(term in normalized for term in ("מתנ ס", "מתנס", "המתנ", "מרכז קהילתי")):
        return ""
    if not any(term in normalized for term in ("הקמה", "הקמת", "בניה", "בנייה", "בניית", "מחכים", "ממתינים")):
        return ""
    districts = re.findall(r"רובע\s+[\u0590-\u05FF\"'׳״A-Za-z0-9]+", text)
    district = max(districts, key=len) if districts else ""
    prefix = "הקמת מתנ\"ס" if any(term in normalized for term in ("הקמה", "הקמת", "בניה", "בנייה", "בניית")) else "מתנ\"ס"
    if district:
        return f"{prefix} {district}"
    return prefix


def _generic_allocation_subject_compaction(value: Any) -> str:
    text = _compact(value)
    normalized = _norm(text)
    if not normalized or not any(term in normalized for term in ("הקצאה", "הקצאת", "הקצאות", "ועדת הקצאות")):
        return ""
    if not any(term in normalized for term in ("בית כנסת", "מרכז רוחני", "מוסד ציבור", "מבנה ציבור", "מקרקעין", "קרקע")):
        return "הקצאה"
    uses: list[str] = []
    if "בית כנסת" in normalized:
        uses.append("בית כנסת")
    if "מרכז רוחני" in normalized:
        uses.append("מרכז רוחני")
    if not uses and "מוסד ציבור" in normalized:
        uses.append("מוסד ציבור")
    if not uses and "מבנה ציבור" in normalized:
        uses.append("מבנה ציבור")
    if uses:
        if "ביטול" in normalized and "ועדת הקצאות" in normalized:
            return "ביטול החלטת ועדת הקצאות עבור " + " ו".join(uses)
        prefix = "הקצאות ל" if "הקצאות" in normalized and "הקצאה" not in normalized else "הקצאה ל"
        return prefix + " ו".join(uses)
    return "הקצאת מקרקעין"


def _generic_budget_subject_compaction(value: Any, *, root_label: str) -> str:
    text = _generic_subject_prefix(value) or _compact(value)
    normalized = _norm(_join_unique([root_label, text]))
    if not text or not any(term in normalized for term in ("תקציב", "כספים", "תב ר", "תבר")):
        return ""
    text = re.sub(r"\s+בין\s+[\u0590-\u05FF\s]{2,80}?(?=\s+ב[\u0590-\u05FF]{2,}\b)", " ", text, count=1)
    return _compact(text)[:220]


def _generic_subject_prefix(value: Any) -> str:
    text = _compact(value)
    if not text:
        return ""
    parts = re.split(r"\s+[–-]\s+|[.;:]|\n", text, maxsplit=1)
    prefix = _compact(parts[0] if parts else text)
    if len(_hebrew_tokens(prefix)) >= 2:
        return prefix[:220]
    return ""


def _generic_action_subject_candidates(value: Any) -> list[str]:
    text = _compact(value)
    if not text:
        return []
    action_terms = (
        "בקשה לאישור",
        "אישור",
        "לאשר",
        "ניהול",
        "פרסום",
        "שיווק",
        "מכירת",
        "מכירה",
        "ביטול",
        "עדכון",
        "הסכם",
        "התקשרות",
        "מכרז",
        "הקצאה",
        "הקצאת",
        "הסדרת",
        "פרויקט",
        "פרוייקט",
        "תכנית",
        "תוכנית",
        "בדיקת",
        "עלות",
        "פטור",
        "מקדמה",
        "תמיכה",
        "שימוע",
        "מינוי",
    )
    action_re = "|".join(re.escape(term) for term in action_terms)
    candidates: list[str] = []
    for match in re.finditer(rf"(?:^|[.;:\n]\s*)(({action_re})\b[^.;:\n]{{4,180}})", text):
        candidate = _compact(match.group(1))
        if len(_hebrew_tokens(candidate)) >= 2:
            candidates.append(candidate[:220])
    return _dedupe_strings(candidates)


def _existing_child_label_for_subject(*, root_topic_id: str, subject: str) -> str | None:
    subject_norm = _norm(subject)
    if not root_topic_id or not subject_norm:
        return None
    for row in CURATED_V4_CHILD_TOPICS:
        if str(row.get("root_topic_id") or "") != root_topic_id:
            continue
        label = str(row.get("child_label_he") or "").strip()
        aliases = [str(value).strip() for value in row.get("aliases_he") or [] if str(value).strip()]
        for candidate in [label, *aliases]:
            if subject_norm == _norm(candidate):
                return label
    return None


def _mark_inherited_duplicate_topic_rows(assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    seen_section_root: dict[tuple[str, str], dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for row in assignments:
        current = dict(row)
        key = _duplicate_topic_key(current)
        section_root_key = _section_root_key(current)
        parent = seen.get(key) if key else None
        if parent is None and str(current.get("structural_role") or "") == "task_row" and section_root_key:
            parent = seen_section_root.get(section_root_key)
        if parent is not None:
            current["row_type"] = "inherited_topic_context"
            current["is_topic_bearing"] = True
            current["skip_model_assignment"] = True
            current["inherited_topic_from_unit_id"] = parent.get("structure_unit_id")
            current["topic_context_role"] = "duplicate_or_dependent_detail"
            current["topic_assignment_route"] = f"{current.get('topic_assignment_route') or 'unknown'}:inherited_duplicate_topic"
            current["topic_reject_reason"] = current.get("topic_reject_reason") or "inherited_duplicate_topic_subject"
        elif key:
            seen[key] = current
        if section_root_key and str(current.get("row_type") or "") == "topic_item":
            seen_section_root.setdefault(section_root_key, current)
        out.append(current)
    return out


def _normalize_protocol_non_topic_assignments(assignments: list[dict[str, Any]], *, items: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    items_by_id = {str(item.get("structure_unit_id") or ""): item for item in items or []}
    out: list[dict[str, Any]] = []
    procedural_non_topic_subjects: set[str] = set()
    for row in assignments:
        current = dict(row)
        item = items_by_id.get(str(current.get("structure_unit_id") or ""), {})
        if str(current.get("packet_role") or "") != "protocol":
            out.append(current)
            continue
        current = _repair_root_geo_child_from_subject(current)
        reason = _post_assignment_non_topic_reason(current)
        if reason:
            current = _convert_assignment_to_non_topic_fragment(current, reason=reason)
            key = _duplicate_subject_norm(current.get("topic_headline_he") or current.get("topic_identification_context") or "")
            if key:
                procedural_non_topic_subjects.add(key)
        else:
            review_reason = _post_assignment_candidate_review_reason(current, item=item)
            if review_reason:
                current = _convert_assignment_to_candidate_review(current, reason=review_reason)
        out.append(current)
    final: list[dict[str, Any]] = []
    for row in out:
        current = dict(row)
        key = _duplicate_subject_norm(current.get("topic_headline_he") or current.get("topic_identification_context") or current.get("topic_subject_he") or "")
        if key and key in procedural_non_topic_subjects and str(current.get("topic_node_status") or "") == "candidate":
            current = _convert_assignment_to_non_topic_fragment(current, reason="duplicate_procedural_non_topic")
        final.append(current)
    return final


def _enforce_structure_ineligible_rows(*, assignments: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items_by_id = {str(item.get("structure_unit_id") or ""): item for item in items}
    out: list[dict[str, Any]] = []
    for row in assignments:
        item = items_by_id.get(str(row.get("structure_unit_id") or ""), {})
        reason = _structure_ineligible_non_topic_reason(item=item, row=row)
        if reason and bool(row.get("is_topic_bearing")):
            out.append(_convert_assignment_to_structural_non_topic(row=row, item=item, reason=reason))
            continue
        out.append(row)
    return out


def _structure_ineligible_non_topic_reason(*, item: dict[str, Any], row: dict[str, Any]) -> str | None:
    if not _is_protocol_item(item):
        return None
    role = str(item.get("structural_role") or row.get("structural_role") or "")
    item_row_type = str(item.get("row_type") or "")
    row_type = str(row.get("row_type") or item_row_type)
    if item.get("topic_assignment_eligible") is False:
        return "structure_marked_topic_ineligible"
    if role in {"metadata", "noise", "table_header_only", "vote_or_result"}:
        return f"structural_role_{role}"
    structural_row_type = row_type if row_type in {"metadata", "vote_or_result", "container"} else item_row_type
    if structural_row_type in {"metadata", "vote_or_result", "container"}:
        return f"row_type_{structural_row_type}"
    return None


def _convert_assignment_to_structural_non_topic(*, row: dict[str, Any], item: dict[str, Any], reason: str) -> dict[str, Any]:
    current = _convert_assignment_to_non_topic_fragment(row, reason=reason)
    preserved_type = str(item.get("row_type") or row.get("row_type") or "fragment")
    if preserved_type in {"metadata", "vote_or_result", "container", "fragment", "attribution_fragment", "merged_topic_detail"}:
        current["row_type"] = preserved_type
    current["topic_assignment_eligible"] = item.get("topic_assignment_eligible")
    current["topic_provenance_reject_reason"] = row.get("topic_provenance_reject_reason") or reason
    current["rationale_he"] = "row preserved as non-topic because Step 3.1 marked it as metadata, vote/result, or topic-ineligible"
    return current


def _repair_root_geo_child_from_subject(row: dict[str, Any]) -> dict[str, Any]:
    if str(row.get("root_topic_id") or "") != "root_geo" or row.get("is_topic_bearing") is not True:
        return row
    subject = _compact(row.get("topic_subject_he"))
    if not subject:
        return row
    geo = _resolve_geo_fallback(subject=subject, municipality=None, enable_govmap_geo=False)
    child_label = _compact((geo or {}).get("child_label_he"))
    if not child_label or child_label == _compact(row.get("child_label_he")):
        return row
    current = dict(row)
    current["child_label_he"] = child_label
    current["raw_child_label_he"] = child_label
    current["child_topic_id"] = child_topic_id("root_geo", child_label)
    current["geo_resolution"] = geo
    route = str(current.get("topic_assignment_route") or "")
    if "geo_child_repaired" not in route:
        current["topic_assignment_route"] = f"{route or 'unknown'}:geo_child_repaired"
    return current


def _post_assignment_non_topic_reason(row: dict[str, Any]) -> str | None:
    if str(row.get("row_type") or "") == "inherited_topic_context" or row.get("topic_context_role"):
        return None
    if row.get("topic_provenance_reject_reason"):
        return str(row.get("topic_provenance_reject_reason"))
    rejected_subject_reason = _rejected_subject_non_topic_reason(row)
    if rejected_subject_reason:
        return rejected_subject_reason
    active_subject_reason = _active_subject_non_topic_reason(row)
    if active_subject_reason:
        return active_subject_reason
    if _contextual_topic_assignment_has_valid_root(row):
        return None
    quote = _compact(row.get("topic_supporting_quote_he"))
    subject = _compact(row.get("topic_subject_he"))
    if subject and quote and _looks_like_multi_committee_protocol_listing(quote) and not _tokens_supported(subject, quote):
        return "unsupported_subject_in_multi_protocol_listing"
    text = _join_unique([row.get("topic_subject_he"), row.get("topic_headline_he"), row.get("topic_identification_context")])
    role = str(row.get("structural_role") or "")
    if _looks_like_policy_dialogue_fragment(_norm(text)):
        return "policy_match_inside_dialogue_fragment"
    reason = _non_topic_protocol_reason(headline=text, raw_text=text, structural_role=role, packet_role=str(row.get("packet_role") or ""))
    if reason:
        return reason
    if str(row.get("topic_node_status") or "") == "candidate" and str(row.get("root_topic_id") or "") == "root_agenda_queries" and role in {"body", "continuation", "task_row"}:
        if not _has_explicit_local_topic_marker(text):
            return "evidence_only_body_fragment"
    return None


def _post_assignment_candidate_review_reason(row: dict[str, Any], *, item: dict[str, Any] | None = None) -> str | None:
    if str(row.get("packet_role") or "") != "protocol":
        return None
    if str(row.get("topic_node_status") or "") != "active" or row.get("is_topic_bearing") is not True:
        return None
    if _row_has_selected_v3_subject(row):
        return None
    if _active_root_only_subject_has_unverified_geo_support(row):
        if _has_confirming_v3_subject_hint_for_row(row=row, item=item or {}):
            return None
        return "unverified_geo_location"
    if _active_multifacet_subject_is_ambiguous(row):
        return "ambiguous_multifacet_topic"
    if str(row.get("root_topic_id") or "") != "root_geo":
        return None
    geo = row.get("geo_resolution") if isinstance(row.get("geo_resolution"), dict) else {}
    if _geo_resolution_is_backend_confirmed(geo):
        return None
    if _has_confirming_v3_subject_hint_for_row(row=row, item=item or {}):
        return None
    return "unverified_geo_location"


def _row_has_selected_v3_subject(row: dict[str, Any]) -> bool:
    if row.get("v3_confirmed_subject") is True:
        return True
    selection = row.get("v3_fallback_selection") if isinstance(row.get("v3_fallback_selection"), dict) else {}
    if str(selection.get("status") or "") == "selected":
        return True
    return "v3_event_subject" in str(row.get("topic_assignment_route") or "")


def _has_confirming_v3_subject_hint_for_row(*, row: dict[str, Any], item: dict[str, Any]) -> bool:
    subject = _clean_structured_fallback_subject(row.get("topic_subject_he"))
    if not subject:
        return False
    unit_id = str(row.get("structure_unit_id") or item.get("structure_unit_id") or "")
    raw = _join_unique([
        row.get("topic_supporting_quote_he"),
        row.get("topic_headline_he"),
        row.get("topic_identification_context"),
        item.get("unit_raw_text"),
        item.get("raw_text"),
    ])
    for hint in item.get("topic_subject_v3_hints") or []:
        if not isinstance(hint, dict):
            continue
        matter = _clean_structured_fallback_subject(hint.get("matter_he") or hint.get("matter_display_he"))
        action = _clean_structured_fallback_subject(hint.get("action_type_he"))
        if not matter or not action:
            continue
        if not (_tokens_supported(matter, subject) or _tokens_supported(subject, matter)):
            continue
        if unit_id and str(hint.get("source_structure_unit_id") or "") == unit_id:
            return True
        quote = _compact(hint.get("source_quote_he"))
        if quote and (_tokens_supported(quote, raw) or _tokens_supported(raw, quote)):
            return True
    return False


def _active_root_only_subject_has_unverified_geo_support(row: dict[str, Any]) -> bool:
    if str(row.get("root_topic_id") or "") == "root_geo" or _compact(row.get("child_label_he")):
        return False
    geo = row.get("geo_resolution") if isinstance(row.get("geo_resolution"), dict) else {}
    if _geo_resolution_is_backend_confirmed(geo):
        return False
    subject = _compact(row.get("topic_subject_he"))
    if not subject:
        return False
    inferred_root_topic_id = infer_root_topic_id(subject, allow_procedural_default=False)
    if inferred_root_topic_id in ROOT_BY_ID and inferred_root_topic_id != "root_geo":
        return False
    if _resolve_geo_fallback(subject=subject, municipality=None, enable_govmap_geo=False):
        return True
    for candidate in row.get("root_topic_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("root_topic_id") or "") == "root_geo" and _confidence(candidate.get("score")) >= 0.75:
            return True
    return False


def _active_multifacet_subject_is_ambiguous(row: dict[str, Any]) -> bool:
    subject = _compact(row.get("topic_subject_he"))
    if not subject or not _looks_like_multifacet_subject(subject):
        return False
    decision = row.get("deterministic_topic_decision") if isinstance(row.get("deterministic_topic_decision"), dict) else {}
    if str(decision.get("reason") or "") != "ambiguous_candidates":
        return False
    roots: set[str] = {str(row.get("root_topic_id") or "")}
    for candidate in row.get("root_topic_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        root_topic_id = str(candidate.get("root_topic_id") or "")
        if root_topic_id in ROOT_BY_ID and _confidence(candidate.get("score")) >= 0.75:
            roots.add(root_topic_id)
    roots.discard("")
    return len(roots) >= 2


def _specific_agreement_subject_needs_review(row: dict[str, Any]) -> bool:
    if str(row.get("root_topic_id") or "") != "root_agreements":
        return False
    subject = _compact(row.get("topic_subject_he"))
    child = _compact(row.get("child_label_he"))
    subject_norm = _norm(subject)
    child_norm = _norm(child)
    if not subject_norm or "שכירות" not in subject_norm:
        return False
    if child_norm and (child_norm == subject_norm or child_norm in subject_norm):
        return False
    return len(_hebrew_tokens(subject)) >= 3


def _rejected_subject_non_topic_reason(row: dict[str, Any]) -> str | None:
    reason = str(row.get("topic_reject_reason") or "")
    if "topic_subject_rejected" not in reason:
        return None
    if _compact(row.get("topic_subject_he")):
        return None
    text = _join_unique([row.get("topic_headline_he"), row.get("topic_identification_context"), row.get("topic_supporting_quote_he")])
    role = str(row.get("structural_role") or "")
    packet_role = str(row.get("packet_role") or "")
    if "procedural_or_dialogue_label" in reason:
        return "rejected_procedural_subject"
    if "unresolved_noisy_label" in reason and _rejected_subject_has_supported_action(row):
        return None
    if "unresolved_noisy_label" in reason and _non_topic_protocol_reason(headline=text, raw_text=text, structural_role=role, packet_role=packet_role):
        return "rejected_noisy_non_topic_subject"
    return None


def _rejected_subject_has_supported_action(row: dict[str, Any]) -> bool:
    root_topic_id = str(row.get("root_topic_id") or "")
    text = _norm(_join_unique([row.get("raw_topic_subject_he"), row.get("topic_headline_he"), row.get("topic_identification_context"), row.get("topic_supporting_quote_he")]))
    if not text:
        return False
    if root_topic_id == "root_agreements" and any(term in text for term in ("הסכם", "התקשרות", "חוזה", "רשות שימוש")):
        return True
    return False


def _active_subject_non_topic_reason(row: dict[str, Any]) -> str | None:
    if str(row.get("topic_node_status") or "") != "active" or row.get("is_topic_bearing") is not True:
        return None
    if "deterministic_v4_topic_arbitration:local_subject" in str(row.get("topic_assignment_route") or ""):
        return None
    subject = _compact(row.get("topic_subject_he"))
    if not subject:
        return None
    if _looks_like_internal_update_heading(subject):
        return "internal_update_heading"
    root_topic_id = str(row.get("root_topic_id") or "")
    if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
        return None
    text = _join_unique([subject, row.get("topic_headline_he"), row.get("topic_identification_context")])
    normalized = _norm(text)
    if str(row.get("structural_role") or "") == "continuation" and ("להלן רשימת" in normalized or "רשימת ההישגים" in normalized):
        return "attachment_or_list_heading"
    if str(row.get("topic_assignment_route") or "").startswith("deterministic_v4_topic_arbitration") and _tokens_supported(subject, text):
        return None
    if row.get("topic_policy_matches") and _tokens_supported(subject, text):
        return None
    if _looks_like_numeric_or_partial_evidence_fragment(row=row, subject=subject, text=text):
        return "active_numeric_or_partial_evidence_fragment"
    if _looks_like_dependent_legal_clause_fragment(text):
        return "active_dependent_legal_clause_fragment"
    if _looks_like_attribution_fragment(subject) and not _has_explicit_local_topic_marker(text):
        return "active_person_attribution_subject"
    if _looks_like_dialogue_exchange_subject(subject=subject, text=text):
        return "active_conversational_subject"
    if _has_explicit_local_topic_marker(normalized) or _has_bounded_substantive_topic_signal(subject):
        return None
    if _looks_like_active_conversational_subject(subject):
        return "active_conversational_subject"
    return None


def _looks_like_numeric_or_partial_evidence_fragment(*, row: dict[str, Any], subject: str, text: str) -> bool:
    if _has_explicit_local_topic_marker(text):
        return False
    subject_tokens = _hebrew_tokens(subject)
    if len(subject_tokens) > 8:
        return False
    raw_candidates = _dedupe_strings(
        [
            _compact(row.get("topic_supporting_quote_he")),
            _compact(row.get("topic_identification_context")),
            _compact(row.get("topic_headline_he")),
            _compact(row.get("raw_text")),
            _compact(row.get("unit_raw_text")),
        ]
    )
    if not raw_candidates:
        return False
    role = str(row.get("structural_role") or "")
    for raw in raw_candidates:
        normalized = _norm(raw)
        start = _compact(raw).strip(" '\"׳״()[]-–:.")
        start_norm = _norm(start)
        if _has_explicit_local_topic_marker(raw) and _tokens_supported(subject, raw):
            return False
        numeric_or_amount_start = bool(re.match(r"^(?:סעיף\s*)?\d+(?:[.,]\d+)?|^כ\s*\d|^[%₪]", start))
        amount_terms = ("סכום", "שח", "₪", "אחוז", "עלות", "אירועים", "מתקשרים", "מבקשים", "תקציב", "חניות")
        if numeric_or_amount_start and any(term in normalized for term in amount_terms):
            return True
        if role == "continuation" and start_norm.startswith("סכום") and any(term in normalized for term in ("₪", "שח", "תמיכה", "עלות")):
            return True
        if _has_bounded_substantive_topic_signal(raw) and _tokens_supported(subject, raw):
            return False
        if role == "continuation" and re.search(r"\d", raw) and re.match(r"^(?:ו|ש|זה|שזה|כ\s*\d|\d)", start_norm) and len(_hebrew_tokens(raw)) <= 14:
            return True
        if "?" in raw and len(subject_tokens) <= 5 and not any(term in normalized for term in ("בנושא", "שאילתה", "הסכם", "אישור", "מינוי")):
            return True
    if any(_has_bounded_substantive_topic_signal(raw) and _tokens_supported(subject, raw) for raw in raw_candidates):
        return False
    return False


def _looks_like_dialogue_exchange_subject(*, subject: str, text: str) -> bool:
    subject_norm = _norm(subject)
    text_norm = _norm(text)
    if not subject_norm or not text_norm:
        return False
    speaker_or_question = any(term in text_norm for term in ("ד ר", "עו ד", "מר ", "גב ", "למה", "שאל", "השיב", "כן ")) or "?" in str(text)
    if speaker_or_question and any(term in subject_norm for term in ("נכתב", "אחרי ההסכמה", "אתו", "איתו", "מרצונו", "למה הוא")):
        return True
    speaker_turns = len(re.findall(r"(?:ד[\"”]?\s*ר|עו[\"”]?\s*ד|מר|גב[׳']?)\s+[\u0590-\u05FF]{2,}", str(text)))
    return speaker_turns >= 2 and len(_hebrew_tokens(subject_norm)) <= 12 and not _has_explicit_local_topic_marker(subject_norm)


def _contextual_topic_assignment_has_valid_root(row: dict[str, Any]) -> bool:
    if str(row.get("topic_node_status") or "") not in {"active", "candidate"}:
        return False
    root_topic_id = str(row.get("root_topic_id") or "")
    if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
        return False
    route = str(row.get("topic_assignment_route") or "")
    if "dicta_contextual" not in route and "contextual_" not in route:
        return False
    indexability = _contextual_indexability_status(row.get("dicta_contextual_indexability_status"), legacy_event_status=row.get("dicta_contextual_event_status"))
    return indexability in {"standalone_topic", "unknown"}


def _convert_assignment_to_non_topic_fragment(row: dict[str, Any], *, reason: str) -> dict[str, Any]:
    current = dict(row)
    current["row_type"] = str(current.get("row_type") or "") if str(current.get("row_type") or "") in {"container", "merged_topic_detail"} else "fragment"
    current["is_topic_bearing"] = False
    current["skip_model_assignment"] = True
    current["topic_subject_he"] = None
    current["root_topic_id"] = "root_agenda_queries"
    current["root_label_he"] = root_label_for_id("root_agenda_queries") or ""
    current["resolved_root_topic_id"] = "root_agenda_queries"
    current["resolved_root_label_he"] = root_label_for_id("root_agenda_queries") or ""
    current["child_label_he"] = None
    current["raw_child_label_he"] = None
    current["child_topic_id"] = None
    current["topic_node_status"] = "active"
    current["topic_reject_reason"] = None
    current["topic_review_status"] = None
    route = str(current.get("topic_assignment_route") or "deterministic_v4_row_type")
    current["topic_assignment_route"] = f"{route}:post_non_topic:{reason}"
    current["rationale_he"] = "row normalized as non-topic/evidence-only protocol fragment"
    return current


def _convert_assignment_to_candidate_review(row: dict[str, Any], *, reason: str) -> dict[str, Any]:
    current = dict(row)
    current["topic_node_status"] = "candidate"
    current["topic_review_status"] = "needs_review"
    current["topic_reject_reason"] = f"non_blocking_topic_review:{reason}"
    route = str(current.get("topic_assignment_route") or "deterministic_v4_topic_review")
    current["topic_assignment_route"] = f"{route}:post_candidate_review:{reason}"
    current["rationale_he"] = "topic kept as candidate pending external confirmation"
    return current


def _section_root_key(row: dict[str, Any]) -> tuple[str, str] | None:
    scope = str(row.get("section_id") or row.get("parent_agenda_unit_id") or "")
    root_topic_id = str(row.get("root_topic_id") or "")
    if not scope or not root_topic_id or str(row.get("packet_role") or "") != "protocol":
        return None
    return (scope, root_topic_id)


def _duplicate_topic_key(row: dict[str, Any]) -> tuple[str, str, str] | None:
    if str((row.get("packet_role") or "")) != "protocol":
        return None
    if str(row.get("row_type") or "") != "topic_item":
        return None
    if str(row.get("topic_node_status") or "") != "active":
        return None
    structural_role = str(row.get("structural_role") or "")
    if structural_role not in {"body", "continuation", "outline_item", "section_heading", "task_row"}:
        return None
    scope = str(row.get("section_id") or row.get("parent_agenda_unit_id") or "")
    if str(row.get("topic_context_source") or "") == "subject_scoped_speaker_anchor":
        scope = f"subject_scoped:{_norm(str(row.get('protocol_subject_he') or ''))}"
    if not scope:
        return None
    subject_norm = _duplicate_subject_norm(row.get("topic_subject_he") or row.get("topic_identification_context") or "")
    if not subject_norm or len(_hebrew_tokens(subject_norm)) < 2:
        return None
    root_topic_id = str(row.get("root_topic_id") or "")
    if not root_topic_id:
        return None
    return (scope, root_topic_id, subject_norm)


def _duplicate_subject_norm(value: Any) -> str:
    subject = clean_protocol_subject_text(value)
    subject = re.sub(r"^(?:התמודדות|טיפול|מניעת|קידום|בדיקת)\s+עם\s+(?:תופעת\s+)?", "", subject)
    subject = re.sub(r"^תופעת\s+", "", subject)
    tokens = _norm(subject).split()
    if tokens and tokens[0].startswith("ה") and len(tokens[0]) >= 4:
        tokens[0] = tokens[0][1:]
    return " ".join(tokens)


def _contract_value(parsed_contract: dict[str, Any], item: dict[str, Any], key: str) -> Any:
    if key == "is_topic_bearing":
        if key in parsed_contract:
            return bool(parsed_contract.get(key))
        return bool(item.get(key))
    value = parsed_contract.get(key)
    if value is None or value == "":
        return item.get(key)
    return value


def _grounded_quote(*, item: dict[str, Any], parsed_quote: str, fallback_text: str) -> str:
    quote = _compact(parsed_quote)
    if quote and _quote_supported_by_item(item=item, quote=quote):
        return quote[:500]
    return str(fallback_text or "")[:500]


def _quote_supported_by_item(*, item: dict[str, Any], quote: str) -> bool:
    quote_norm = _quote_match_norm(quote)
    if not quote_norm:
        return False
    evidence_parts = [
        str(item.get("raw_text") or ""),
        str(item.get("topic_identification_context") or ""),
        "\n".join(str(action) for action in item.get("explicit_actions") or []),
        str(item.get("outline_title_he") or ""),
        "\n".join(str(context.get("topic_supporting_quote_he") or "") for context in item.get("referenced_attachment_contexts") or [] if isinstance(context, dict)),
    ]
    evidence_norm = _quote_match_norm("\n".join(evidence_parts))
    if quote_norm in evidence_norm:
        return True
    quote_tokens = [token for token in _hebrew_tokens(quote_norm) if len(token) >= 3]
    if len(quote_tokens) < 3:
        return False
    hits = [token for token in quote_tokens if token in evidence_norm]
    return len(hits) >= min(len(quote_tokens), max(3, int(len(quote_tokens) * 0.75)))


def _quote_match_norm(value: Any) -> str:
    text = _norm(str(value or ""))
    text = re.sub(r"[\u0591-\u05C7]", "", text)
    text = text.translate(str.maketrans({"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u05f4": '"', "\u05f3": "'", "\u2018": "'", "\u2019": "'", "\u201a": "'"}))
    text = re.sub(r"[^\u0590-\u05FF\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _validate(*, assignments: list[dict[str, Any]], items: list[dict[str, Any]], item_count: int, model_errors: list[dict[str, Any]], deterministic_count: int, low_confidence_candidate_count: int, dicta_mode: str) -> dict[str, Any]:
    status_counts = Counter(str(row.get("topic_node_status") or "") for row in assignments)
    row_type_counts = Counter(str(row.get("row_type") or "") for row in assignments)
    route_counts = Counter(_route_bucket(str(row.get("topic_assignment_route") or "")) for row in assignments)
    classifier_actions = Counter(str(((item.get("deterministic_topic_decision") or {}) if isinstance(item.get("deterministic_topic_decision"), dict) else {}).get("action") or "") for item in items)
    classifier_needs_dicta = sum(1 for item in items if bool(((item.get("deterministic_topic_decision") or {}) if isinstance(item.get("deterministic_topic_decision"), dict) else {}).get("needs_dicta")))
    missing_roots = [row.get("structure_unit_id") for row in assignments if str(row.get("root_topic_id") or "") not in ROOT_BY_ID]
    needs_review = [row for row in assignments if str(row.get("topic_node_status") or "") == "needs_review"]
    non_blocking_review = [row for row in assignments if str(row.get("topic_review_status") or "") == "needs_review" or str(row.get("topic_node_status") or "") == "candidate"]
    return {
        "unit_count": item_count,
        "assignment_count": len(assignments),
        "status_counts": dict(status_counts),
        "row_type_counts": dict(row_type_counts),
        "route_counts": dict(route_counts),
        "classifier_quality_report": {
            "dicta_mode": dicta_mode,
            "candidate_finder_action_counts": dict(classifier_actions),
            "candidate_finder_needs_dicta_count": classifier_needs_dicta,
            "deterministic_preassigned_count": deterministic_count,
            "low_confidence_candidate_count": low_confidence_candidate_count,
            "dicta_or_cached_assignment_count": int(route_counts.get("dicta", 0)),
            "dicta_calls_avoided_estimate": deterministic_count,
            "non_blocking_review_count": len(non_blocking_review),
        },
        "missing_or_invalid_root_count": len(missing_roots),
        "needs_review_count": len(needs_review),
        "needs_review_units": [row.get("structure_unit_id") for row in needs_review[:50]],
        "non_blocking_review_count": len(non_blocking_review),
        "non_blocking_review_units": [row.get("structure_unit_id") for row in non_blocking_review[:50]],
        "model_error_count": len(model_errors),
        "accept_for_next_step": item_count > 0 and len(assignments) == item_count and not missing_roots,
    }


def _route_bucket(route: str) -> str:
    if route.startswith("deterministic_v4_candidate_finder"):
        return "candidate_finder"
    if route.startswith("deterministic_v4_candidate_review"):
        return "non_blocking_review"
    if route.startswith("dictalm_v4_global_tree"):
        return "dicta"
    if route.startswith("deterministic_v4_row_type"):
        return "row_type"
    if route.startswith("deterministic_v4_fallback"):
        return "fallback"
    return "other"


def _classifier_quality_markdown(report: dict[str, Any]) -> str:
    lines = ["# PDF-first v4 Topic Classifier Quality Report", ""]
    lines.append(f"- Dicta mode: `{report.get('dicta_mode')}`")
    lines.append(f"- Candidate finder needs Dicta: `{report.get('candidate_finder_needs_dicta_count')}`")
    lines.append(f"- Deterministic preassigned: `{report.get('deterministic_preassigned_count')}`")
    lines.append(f"- Low-confidence candidates: `{report.get('low_confidence_candidate_count')}`")
    lines.append(f"- Dicta/cached assignments: `{report.get('dicta_or_cached_assignment_count')}`")
    lines.append(f"- Estimated Dicta calls avoided: `{report.get('dicta_calls_avoided_estimate')}`")
    lines.append(f"- Non-blocking review rows: `{report.get('non_blocking_review_count')}`")
    lines.append("")
    lines.append("## Candidate Finder Actions")
    for key, value in sorted((report.get("candidate_finder_action_counts") or {}).items()):
        lines.append(f"- `{key or '<none>'}`: `{value}`")
    lines.append("")
    return "\n".join(lines)


def _adaptive_batches(items: list[dict[str, Any]], default_size: int) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    pending: list[dict[str, Any]] = []
    for item in items:
        if bool(item.get("is_topic_bearing")):
            if pending:
                batches.append(pending)
                pending = []
            # Topic-bearing protocol rows are small but important; single-item calls reduce omissions.
            batches.append([item])
            continue
        pending.append(item)
        if len(pending) >= default_size:
            batches.append(pending)
            pending = []
    if pending:
        batches.append(pending)
    return batches


def _parsed_topic_contract(parsed: dict[str, Any]) -> dict[str, Any]:
    subject = _clean_topic_subject(_compact(parsed.get("clean_subject_he") or parsed.get("topic_subject_he")))
    raw_root_topic_id = str(parsed.get("root_topic_id") or "").strip() or None
    return {
        "is_topic_bearing": bool(parsed.get("is_topic_bearing")),
        "agenda_carrier_he": _compact(parsed.get("agenda_carrier_he")) or None,
        "topic_subject_he": subject or None,
        "clean_subject_he": subject or None,
        "primary_action_he": _compact(parsed.get("primary_action_he")) or None,
        "service_domain_he": _compact(parsed.get("service_domain_he")) or None,
        "classification_basis": _contextual_classification_basis(parsed.get("classification_basis") or parsed.get("contextual_classification_basis")) if ("classification_basis" in parsed or "contextual_classification_basis" in parsed) else None,
        "duplicate_of_unit_id": _compact(parsed.get("duplicate_of_unit_id")) or None,
        "duplicate_evidence_quote_he": _compact(parsed.get("duplicate_evidence_quote_he"))[:500] or None,
        "policy_id": _compact(parsed.get("policy_id")) or None,
        "attribution_he": _compact(parsed.get("attribution_he")) or None,
        "why_not_other_roots_he": _compact(parsed.get("why_not_other_roots_he")) or None,
        "dicta_raw_is_topic_bearing": parsed.get("is_topic_bearing") if "is_topic_bearing" in parsed else None,
        "dicta_raw_subject_he": _compact(parsed.get("topic_subject_he")) or None,
        "dicta_clean_subject_he": subject or None,
        "dicta_raw_root_topic_id": raw_root_topic_id,
        "dicta_raw_root_label_he": root_label_for_id(raw_root_topic_id) if raw_root_topic_id in ROOT_BY_ID else None,
        "dicta_confidence": _optional_confidence(parsed.get("confidence")),
        "dicta_rationale_he": _compact(parsed.get("rationale_he"))[:500] or None,
        "dicta_contextual_schema_valid": parsed.get("contextual_schema_valid") if "contextual_schema_valid" in parsed else None,
        "dicta_contextual_event_status": _contextual_event_status(parsed.get("contextual_event_status")) if "contextual_event_status" in parsed else None,
        "dicta_contextual_agenda_disposition": _contextual_agenda_disposition(parsed.get("contextual_agenda_disposition") or parsed.get("contextual_event_status")) if ("contextual_agenda_disposition" in parsed or "contextual_event_status" in parsed) else None,
        "dicta_contextual_indexability_status": _contextual_indexability_status(parsed.get("contextual_indexability_status") or parsed.get("indexability_status"), legacy_event_status=parsed.get("event_status")) if ("contextual_indexability_status" in parsed or "indexability_status" in parsed or "event_status" in parsed) else None,
        "dicta_contextual_original_indexability_status": _contextual_indexability_status(parsed.get("contextual_original_indexability_status")) if "contextual_original_indexability_status" in parsed else None,
        "dicta_contextual_indexability_override_reason": _compact(parsed.get("contextual_indexability_override_reason")) or None,
        "dicta_contextual_classification_basis": _contextual_classification_basis(parsed.get("contextual_classification_basis") or parsed.get("classification_basis")) if ("contextual_classification_basis" in parsed or "classification_basis" in parsed) else None,
        "dicta_contextual_missing_optional_fields": [str(value) for value in (parsed.get("contextual_missing_optional_fields") or []) if str(value).strip()],
        "dicta_contextual_rationale_fallback": bool(parsed.get("contextual_rationale_fallback")) if "contextual_rationale_fallback" in parsed else None,
        "dicta_contextual_competing_roots": _contextual_competing_roots(parsed.get("contextual_competing_roots") or parsed.get("competing_roots")),
        "dicta_contextual_standalone_event": parsed.get("contextual_standalone_event") if "contextual_standalone_event" in parsed else None,
        "dicta_contextual_consistency_warning": _compact(parsed.get("contextual_consistency_warning")) or None,
        "dicta_contextual_event_summary_he": _compact(parsed.get("contextual_event_summary_he"))[:500] or None,
        "dicta_contextual_agenda_status_he": _compact(parsed.get("contextual_agenda_status_he"))[:220] or None,
        "dicta_contextual_normalization_confidence": _optional_confidence(parsed.get("contextual_normalization_confidence")),
        "dicta_contextual_original_root_topic_id": _compact(parsed.get("contextual_validation_original_root_topic_id")) or None,
        "dicta_contextual_override_root_topic_id": _compact(parsed.get("contextual_validation_override_root_topic_id")) or None,
    }


def _topic_contract_from_headline(headline: str, *, structural_role: str, packet_role: str) -> dict[str, Any]:
    headline = _compact(headline)
    if packet_role != "protocol" or not headline:
        return {"is_topic_bearing": bool(headline), "agenda_carrier_he": None, "topic_subject_he": headline or None, "attribution_he": None}
    carrier, subject, attribution = _split_headline_contract(headline)
    subject = _strip_attribution_tail(subject)
    if _looks_like_procedural_packet_carrier_only(subject):
        subject = ""
    if _looks_like_protocol_cover_metadata(subject) or _looks_like_protocol_cover_metadata(headline):
        subject = ""
    if _looks_like_protocol_meeting_carrier_only(subject):
        subject = ""
    if not subject and structural_role in {"continuation", "vote_or_result", "task_row"}:
        subject = _strip_attribution_tail(_clean_heading(headline))
    if _looks_like_procedural_packet_carrier_only(subject):
        subject = ""
    if _looks_like_protocol_cover_metadata(subject) or _looks_like_protocol_cover_metadata(headline):
        subject = ""
    if _looks_like_protocol_meeting_carrier_only(subject):
        subject = ""
    is_bearing = _is_substantive_topic_subject(subject)
    return {
        "is_topic_bearing": is_bearing,
        "agenda_carrier_he": carrier or None,
        "topic_subject_he": subject if is_bearing else None,
        "attribution_he": attribution or None,
    }


def _row_type_for_unit(*, unit: dict[str, Any], topic_contract: dict[str, Any], packet_role: str) -> str:
    role = str(unit.get("structural_role") or "")
    raw_text = _compact(unit.get("raw_text"))
    headline = _headline_from_unit(unit) or raw_text
    if packet_role != "protocol":
        return "topic_item" if bool(topic_contract.get("is_topic_bearing")) else "fragment"
    if role == "metadata" or _looks_like_protocol_metadata(raw_text):
        return "metadata"
    if role == "vote_or_result" or _looks_like_vote_fragment(raw_text):
        return "vote_or_result"
    if _looks_like_container_heading(headline):
        return "container"
    non_topic_reason = _non_topic_protocol_reason(headline=headline, raw_text=raw_text, structural_role=role, packet_role=packet_role)
    if non_topic_reason and _non_topic_reason_should_override_topic_contract(reason=non_topic_reason, headline=headline, topic_contract=topic_contract):
        return "fragment"
    if _looks_like_attribution_fragment(raw_text):
        return "attribution_fragment"
    if bool(topic_contract.get("is_topic_bearing")):
        return "topic_item"
    return "fragment"


def _non_topic_reason_should_override_topic_contract(*, reason: str, headline: str, topic_contract: dict[str, Any]) -> bool:
    if not bool(topic_contract.get("is_topic_bearing")):
        return True
    if not _has_bounded_substantive_topic_signal(headline):
        return True
    procedural_tail_reasons = {
        "vote_or_discussion_procedure_fragment",
        "contract_approval_procedure_fragment",
        "approval_requirement_fragment",
        "approval_timing_fragment",
    }
    return reason not in procedural_tail_reasons


def _non_topic_protocol_reason(*, headline: str, raw_text: str, structural_role: str, packet_role: str) -> str | None:
    if packet_role != "protocol":
        return None
    text = _compact(headline or raw_text)
    raw = _compact(raw_text)
    if not text and not raw:
        return "empty_fragment"
    if _looks_like_container_heading(text):
        return "container_heading"
    if _looks_like_mayor_update_content(raw or text):
        return "mayor_update_content"
    if _looks_like_ceremonial_notice_heading(text):
        return "ceremonial_notice_heading"
    if _looks_like_protocol_meeting_carrier_only(text):
        return "protocol_cover_metadata"
    if _looks_like_protocol_cover_metadata(text):
        return "protocol_cover_metadata"
    if raw and raw != text and _looks_like_protocol_listing(raw):
        if not (_has_bounded_substantive_topic_signal(text) and _can_ignore_listing_for_bounded_subject(raw_text=raw, role=structural_role)):
            return "protocol_listing"
    if _looks_like_procedural_packet_carrier_only(text):
        return "procedural_packet_carrier"
    if _looks_like_procedural_carrier_heading(text):
        return "procedural_carrier_heading"
    if _looks_like_committee_protocol_date_heading(text):
        return "committee_protocol_date_heading"
    if _looks_like_query_attribution_only(text):
        return "query_attribution_only"
    if _looks_like_incomplete_query_topic_marker(text):
        return "incomplete_query_topic_marker"
    if _looks_like_query_intro_only(text):
        return "query_intro_only"
    if _looks_like_order_proposal_intro_only(text):
        return "order_proposal_intro_only"
    if raw and raw != text and _looks_like_order_proposal_intro_only(raw):
        return "order_proposal_intro_only"
    if _looks_like_personal_topic_reference(raw or text):
        return "personal_topic_reference"
    if _looks_like_personal_announcement_handoff(raw or text):
        return "personal_announcement_handoff"
    if _looks_like_speaker_reference_heading(text):
        return "speaker_reference_heading"
    if _looks_like_speaker_dialogue_fragment(raw or text):
        return "speaker_dialogue_fragment"
    if _looks_like_legal_section_reference_fragment(raw or text):
        return "legal_section_reference_fragment"
    if _looks_like_dependent_legal_clause_fragment(text):
        return "dependent_legal_clause_fragment"
    if _looks_like_legal_boilerplate_fragment(text):
        return "legal_boilerplate_fragment"
    if _looks_like_table_header_or_schema_fragment(text):
        return "table_header_or_schema_fragment"
    if _looks_like_signature_or_end_page_fragment(text):
        return "signature_or_end_page_fragment"
    if raw and raw != text and _looks_like_signature_or_end_page_fragment(raw):
        return "signature_or_end_page_fragment"
    if _looks_like_contract_approval_procedure_fragment(text) or (raw and raw != text and _looks_like_contract_approval_procedure_fragment(raw)):
        return "contract_approval_procedure_fragment"
    if _looks_like_deliberation_continuation_fragment(raw or text):
        return "deliberation_continuation_fragment"
    if _looks_like_approval_timing_fragment(raw or text):
        return "approval_timing_fragment"
    if _looks_like_attendance_context_fragment(raw or text):
        return "attendance_context_fragment"
    if _looks_like_no_proposal_or_agenda_action_fragment(raw or text):
        return "no_proposal_or_agenda_action_fragment"
    if _looks_like_approval_requirement_fragment(raw or text):
        return "approval_requirement_fragment"
    if _looks_like_protocol_formatting_discussion_fragment(raw or text):
        return "protocol_formatting_discussion_fragment"
    if _looks_like_reply_attachment_procedure_fragment(raw or text):
        return "reply_attachment_procedure_fragment"
    if _looks_like_order_proposal_response_vote_fragment(raw or text):
        return "order_proposal_response_vote_fragment"
    if _looks_like_no_topic_continuation(raw or text):
        return "no_topic_continuation"
    if _looks_like_vote_or_discussion_procedure_fragment(raw or text) and not _substantive_headline_overrides_raw_fragment_guard(headline=text, raw_text=raw):
        return "vote_or_discussion_procedure_fragment"
    if _looks_like_procedural_dialogue_fragment(raw or text):
        return "procedural_dialogue_fragment"
    if _looks_like_transcript_speech_fragment(raw or text):
        return "transcript_speech_fragment"
    return None


def _protocol_topic_provenance_reject_reason(*, unit: dict[str, Any], headline: str, raw_text: str, topic_context_source: str, headline_source: str, packet_role: str) -> str | None:
    if packet_role != "protocol":
        return None
    role = str(unit.get("structural_role") or "")
    if role in {"metadata", "noise", "table_header_only", "vote_or_result"}:
        return None
    compact_headline = _compact(headline)
    compact_raw = _compact(raw_text)
    if not compact_headline and not compact_raw:
        return "missing_topic_headline_provenance"
    if _has_explicit_local_topic_marker(compact_headline):
        if headline_source in {"explicit_header", "explicit_title", "explicit_section_title"} or len(compact_raw) <= 360:
            return None
    if _has_bounded_raw_topic_marker(compact_raw):
        return None
    if _has_bounded_substantive_topic_signal(compact_raw) and _bounded_topic_signal_allowed(unit=unit, raw_text=compact_raw, headline=compact_raw, role=role):
        return None
    if _has_bounded_substantive_topic_signal(compact_headline) and _bounded_topic_signal_allowed(unit=unit, raw_text=compact_raw, headline=compact_headline, role=role):
        return None
    if topic_context_source in {"parent_agenda", "section_agenda", "previous_agenda"} and role in {"body", "continuation", "task_row"}:
        return "inherited_context_not_standalone_topic"
    if headline_source in {"explicit_header", "explicit_title", "explicit_section_title"}:
        return None
    if headline_source == "raw_extracted" and _headline_matches_explicit_decision_subject(raw_text=compact_raw, headline=compact_headline):
        return None
    if role == "body":
        return "body_without_headline_topic_provenance"
    if headline_source in {"raw_titleish", "raw_extracted"}:
        token_count = len(_hebrew_tokens(compact_raw))
        # Raw text can stand in for a heading only when the unit itself is short.
        # Long transcript windows often contain incidental heading-like phrases.
        if role in {"outline_item", "section_heading", "task_row"} and token_count <= 28 and len(compact_raw) <= 360:
            return None
        return "transcript_window_without_bounded_headline"
    if headline_source == "none":
        return "missing_topic_headline_provenance"
    if role in {"outline_item", "section_heading", "task_row", "continuation"} and compact_headline:
        return None
    return "missing_topic_headline_provenance"


def _substantive_headline_overrides_raw_fragment_guard(*, headline: str, raw_text: str) -> bool:
    compact_headline = _compact(headline)
    compact_raw = _compact(raw_text)
    return bool(
        compact_headline
        and compact_raw
        and compact_headline != compact_raw
        and _is_substantive_topic_subject(compact_headline)
        and not _looks_like_vote_or_discussion_procedure_fragment(compact_headline)
    )


def _headline_matches_explicit_decision_subject(*, raw_text: str, headline: str) -> bool:
    decision_heading = _extract_explicit_decision_heading(raw_text)
    return bool(decision_heading and _duplicate_subject_norm(decision_heading) == _duplicate_subject_norm(headline))


def _has_bounded_raw_topic_marker(raw_text: str) -> bool:
    compact = _compact(raw_text)
    if not _has_explicit_local_topic_marker(compact):
        return False
    if len(compact) <= 360:
        return True
    return False


def _has_bounded_substantive_topic_signal(text: str) -> bool:
    compact = _compact(text)
    if not compact or len(compact) > 260 or len(_hebrew_tokens(compact)) > 28:
        return False
    for match in topic_policy_matches(compact, limit=3):
        root_topic_id = str(match.get("root_topic_id") or "")
        if root_topic_id in ROOT_BY_ID and root_topic_id not in {"root_agenda_queries", "root_order_proposals"}:
            return True
    for row in _allowed_root_alignment_scores(compact):
        root_topic_id = str(row.get("root_topic_id") or "")
        matched_terms = [str(term) for term in row.get("matched_terms") or [] if str(term).strip()]
        if root_topic_id in ROOT_BY_ID and root_topic_id not in {"root_agenda_queries", "root_order_proposals"} and float(row.get("score") or 0.0) >= 0.82 and matched_terms:
            return True
    return False


def _bounded_topic_signal_allowed(*, unit: dict[str, Any], raw_text: str, headline: str, role: str) -> bool:
    raw = _compact(raw_text)
    head = _compact(headline)
    if not raw or not head or _looks_like_protocol_listing(raw):
        return False
    if role in {"outline_item", "section_heading", "task_row"}:
        return len(raw) <= 360 and len(_hebrew_tokens(raw)) <= 55
    if role == "continuation":
        evidence = unit.get("structure_evidence") if isinstance(unit.get("structure_evidence"), dict) else {}
        return str(evidence.get("split_reason") or "") == "multiple_structural_starts" and len(raw) <= 360 and len(_hebrew_tokens(raw)) <= 55
    return False


def _bounded_topic_signal_allowed_without_unit(*, raw_text: str, role: str) -> bool:
    raw = _compact(raw_text)
    if not raw:
        return False
    if role in {"outline_item", "section_heading", "task_row"}:
        return len(raw) <= 360 and len(_hebrew_tokens(raw)) <= 55
    return False


def _can_ignore_listing_for_bounded_subject(*, raw_text: str, role: str) -> bool:
    raw = _compact(raw_text)
    if not _bounded_topic_signal_allowed_without_unit(raw_text=raw, role=role):
        return False
    bracketed_items = len({match.group(1) for match in re.finditer(r"\[\s*(\d+(?:\.\d+)?)\s*\]", raw)})
    dot_leaders = len(re.findall(r"\.{6,}", raw))
    numbered_items = len(re.findall(r"(?:^|\s)\d+(?:\.\d+)?\s*[.)]", raw))
    page_ref_mentions = len(re.findall(r"\(?\s*עמ", raw))
    return bracketed_items == 0 and dot_leaders == 0 and page_ref_mentions == 0 and numbered_items < 3


def _looks_like_procedural_packet_carrier_only(text: str) -> bool:
    compact = _compact(text)
    normalized = _norm(compact)
    if not normalized:
        return False
    if _has_bounded_substantive_topic_signal(compact):
        return False
    packet_cues = [
        "אישור מועצה במייל",
        "התקבל אישור מועצה",
        "תקנון בדבר ישיבות",
        "סעיף47",
        "סעיף 47",
        "פנייה לחברי המועצה",
        "אישור היועץ המשפטי",
        "נוסח הפנייה",
    ]
    return any(cue in normalized for cue in packet_cues)


def _looks_like_protocol_metadata(text: str) -> bool:
    compact = _compact(text)
    if not compact:
        return False
    metadata_terms = [
        "ישיבת מועצה מאושר",
        "ישיבת מועצה רגילה",
        "מספר דיון",
        "נושא הדיון",
        "קדנציה",
        "מיקום הישיבה",
        "תאריך אישור",
        "הוקלד ע\"י",
        "הודפס ע\"י",
        "השתתפו",
        "נעדרו",
        "נוכחים",
        "נוהל ע\"י",
        "תועד ע\"י",
    ]
    normalized = re.sub(r"\s*:\s*", ":", compact)
    normalized = re.sub(r"נ:ושא", "נושא", normalized)
    hits = sum(1 for term in metadata_terms if term in normalized)
    return hits >= 2 or (hits >= 1 and len(_hebrew_tokens(compact)) <= 12)


def _looks_like_protocol_cover_metadata(text: str) -> bool:
    compact = _compact(text)
    normalized = _norm(compact)
    if not normalized or "פרוטוקול" not in normalized:
        return False
    if any(term in normalized for term in ("חתימה", "חתימות", "סיום הפרוטוקול", "סיום הישיבה")):
        return False
    if _has_bounded_substantive_topic_signal(compact):
        return False
    has_municipality = "עיריית" in normalized or "עיריה" in normalized or "עירייה" in normalized
    has_serial = re.search(r"\b(?:מס|מספר)(?:\s*[/\d])?", normalized) is not None
    has_date = "מתאריך" in normalized or "מיום" in normalized or re.search(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b", compact)
    has_meeting_cue = any(term in normalized for term in ("מועצה", "ישיבה", "ישיבות", "מן המניין", "שלא מן המניין"))
    if has_municipality and (has_serial or has_meeting_cue) and (bool(has_date) or len(_hebrew_tokens(normalized)) <= 14):
        return True
    return has_serial and has_meeting_cue and len(_hebrew_tokens(normalized)) <= 14


def _looks_like_protocol_meeting_carrier_only(text: str) -> bool:
    compact = _clean_heading(text)
    normalized = _norm(compact).strip(" :.-–")
    if not normalized:
        return False
    if not any(term in normalized for term in ("מועצה", "ישיבה", "ישיבות", "מן המניין", "שלא מן המניין")):
        return False
    generic_tokens = {"פרוטוקול", "אישור", "אישורי", "מועצה", "המועצה", "ישיבה", "ישיבת", "ישיבות", "מן", "המניין", "שלא", "מס", "מספר"}
    tokens = [token for token in _hebrew_tokens(normalized) if token]
    return bool(tokens) and len(tokens) <= 8 and set(tokens).issubset(generic_tokens)


def _looks_like_ceremonial_notice_heading(text: str) -> bool:
    compact = _clean_heading(text)
    normalized = _norm(compact)
    if not normalized:
        return False
    normalized = re.sub(r"^סעיף\s*\d+\s*:?\s*", "", normalized).strip(" :.-–")
    if not normalized or _has_bounded_substantive_topic_signal(normalized):
        return False
    ceremonial_terms = {"ברכות", "הוקרות", "הודעות", "ברכה", "הוקרה", "הודעה"}
    tokens = [token[1:] if token.startswith("ו") and token[1:] in ceremonial_terms else token for token in _hebrew_tokens(normalized) if token]
    if not tokens:
        return False
    return set(tokens).issubset(ceremonial_terms) and any(token in ceremonial_terms for token in tokens)


def _looks_like_container_heading(text: str) -> bool:
    compact = _clean_heading(text)
    if not compact:
        return False
    if _looks_like_protocol_listing(compact):
        return True
    normalized = _norm(compact).strip(" :.-–")
    if _looks_like_internal_update_heading(normalized):
        return True
    discussion_heading = re.search(r"(?:^|:)\s*(?:ה)?נושאים\s+לדיון(?:\s*[:.\-–]?\s*(?:שאילתות|הצעות\s+לסדר|אישורים|פרוטוקולים))?$", normalized)
    if discussion_heading:
        prefix = normalized[: discussion_heading.start()].strip(" :.-–")
        if not prefix or (len(_hebrew_tokens(prefix)) <= 4 and not _has_bounded_substantive_topic_signal(prefix)):
            return True
    # These are agenda carriers/section labels, not municipal subjects by themselves.
    container_patterns = [
        r"^(?:סדר\s+)?ישיבת\s+המועצה$",
        r"^ישיבת\s+המועצה\s+סדר$",
        r"^סעיף\s*\d+\s*:?[\s\-–]*(?:סדר\s+)?ישיבת\s+המועצה$",
        r"^(?:ה)?נושאים\s+לדיון(?:\s*[:.\-–]?\s*(?:שאילתות|הצעות\s+לסדר|אישורים|פרוטוקולים))?$",
        r"^סדר\s+יום(?:\s+ושאילתות)?$",
        r"^שאילתות(?:\s+\d+(?:\.\d+)?)?$",
        r"^הצעות\s+לסדר(?:\s+\d+(?:\.\d+)?)?$",
        r"^אישורים$",
        r"^פרוטוקולים?$",
        r"^משימות$",
    ]
    return any(re.search(pattern, normalized) for pattern in container_patterns)


def _looks_like_internal_update_heading(text: str) -> bool:
    normalized = _norm(text).strip(" :.-–")
    if not normalized:
        return False
    normalized = re.sub(r"^סעיף\s*\d*\s*:?", " ", normalized).strip(" :.-–")
    normalized = re.sub(r"\b\d+\b", " ", normalized)
    normalized = re.sub(r"\bסעיף\b", " ", normalized)
    normalized = _compact(normalized).strip(" :.-–")
    if not any(phrase in normalized for phrase in ("עדכוני ראש העיר", "עדכון ראש העיר", "דברי ראש העיר")):
        return False
    allowed_tokens = {"עדכוני", "עדכון", "דברי", "ראש", "העיר", "העיריה", "העירייה"}
    tokens = _hebrew_tokens(normalized)
    return bool(tokens) and set(tokens).issubset(allowed_tokens)


def _looks_like_mayor_update_content(text: str) -> bool:
    normalized = _norm(text).strip(" :.-–")
    if not normalized:
        return False
    if not any(phrase in normalized for phrase in ("דברי ראש העיר", "עדכוני ראש העיר", "עדכון ראש העיר")):
        return False
    if any(term in normalized for term in ("זכות הדיבור", "הודעה אישית", "מסירת הודעה")):
        return False
    action_terms = ("אישור", "אישורי", "החלט", "מינוי", "מינויים", "תקצוב", "תקציב", "שאילתה", "שאילתא", "הצעה לסדר", "דיון")
    if any(term in normalized for term in action_terms):
        return False
    return len(_hebrew_tokens(normalized)) > 8


def _looks_like_committee_protocol_date_heading(text: str) -> bool:
    compact = _clean_heading(text)
    if not compact:
        return False
    normalized = _norm(compact).strip(" :.-–")
    if any(term in normalized for term in ("אישור", "אישר", "אושרה", "אושר", "החלט", "מינוי", "מינויים", "תקצוב", "עדכון", "דיון")):
        return False
    if not re.match(r"^(?:פרוטוקול\s+(?:מישיבת\s+)?)?(?:ה)?(?:ו?ועדה|ו?ועדת)\b", normalized):
        return False
    has_date_or_number = bool(re.search(r"\b(?:מס|מספר|מיום|מתאריך)\b", normalized) or re.search(r"\d", normalized))
    if not has_date_or_number:
        return False
    remainder = re.sub(r"^פרוטוקול\s+(?:מישיבת\s+)?", "", normalized).strip()
    remainder = re.sub(r"^(?:הוועדה|הועדה|וועדה|ועדה|הוועדת|הועדת|וועדת|ועדת)\s+", "", remainder).strip()
    remainder = re.sub(r"\b(?:מס|מספר)\s*[/\d.\-]*\b.*$", "", remainder).strip()
    remainder = re.sub(r"\b(?:מיום|מתאריך)\b.*$", "", remainder).strip()
    return 1 <= len(_hebrew_tokens(remainder)) <= 5


def _looks_like_procedural_carrier_heading(text: str) -> bool:
    compact = _clean_heading(text)
    if not compact:
        return False
    if _contextual_committee_domain_root_from_text(compact):
        return False
    normalized = _norm(compact).strip(" :.-–")
    if not re.match(r"^פרוטוקול(?:י)?\s+(?:מישיבת\s+)?(?:ה)?(?:ו?ועדה|ו?ועדת)\b", normalized):
        return False
    return not _has_specific_committee_protocol_subject(normalized)


def _has_specific_committee_protocol_subject(text: str) -> bool:
    normalized = _norm(text)
    normalized = re.sub(r"^פרוטוקול(?:י)?\s+(?:מישיבת\s+)?", "", normalized).strip()
    normalized = re.sub(r"^(?:הוועדה|הועדה|וועדה|ועדה|הוועדת|הועדת|וועדת|ועדת)\s+", "", normalized).strip()
    normalized = re.sub(r"\b(?:מס|מספר)\s*[/\d.\-]*\b.*$", "", normalized).strip()
    normalized = re.sub(r"\bמיום\b.*$", "", normalized).strip()
    generic_tokens = {"ועדה", "וועדה", "ועדת", "וועדת", "הועדה", "הוועדה", "מקצועית", "המקצועית", "מקצועי", "מקצועיים", "פרוטוקול", "ישיבה", "ישיבת", "מישיבת", "מס", "מספר"}
    tokens = [token for token in _hebrew_tokens(normalized) if token not in generic_tokens]
    return bool(tokens)


def _looks_like_query_attribution_only(text: str) -> bool:
    compact = _compact(text)
    normalized = _norm(compact)
    if not normalized.startswith("שאילת"):
        return False
    if _has_explicit_local_topic_marker(normalized) or "–" in compact or " - " in compact:
        return False
    role_cues = ["חבר מועצה", "חברת מועצה", "מועצה", "ראש העיר", "סגן", "סגנית", "עוד", "דר", "מר", "גברת"]
    tokens = _hebrew_tokens(normalized)
    has_role_cue = any(cue in normalized for cue in role_cues)
    if len(tokens) <= 8 and has_role_cue:
        return True
    intro_cues = ["אדוני ראש העיר", "שאילתה מס", "שאילתה מספר", "שאילתא מס", "של חבר המועצה", "של חברת המועצה"]
    return has_role_cue and any(cue in normalized for cue in intro_cues)


def _looks_like_query_intro_only(text: str) -> bool:
    normalized = _norm(_compact(text))
    if not normalized or _has_explicit_local_topic_marker(normalized):
        return False
    if "שאילתה" not in normalized and "שאילתא" not in normalized:
        return False
    intro_cues = ["אדוני ראש העיר", "בבקשה", "שאילתה ראשונה", "שאילתה שנייה", "שאילתה שלישית", "שאילתא ראשונה"]
    return any(cue in normalized for cue in intro_cues) and len(_hebrew_tokens(normalized)) <= 8


def _looks_like_incomplete_query_topic_marker(text: str) -> bool:
    normalized = _norm(_compact(text)).strip(" -–:.,")
    if not normalized.startswith(("שאילתה של", "שאילתא של")):
        return False
    if not re.search(r"\bבנושא\b", normalized):
        return False
    tail = normalized.split("בנושא", 1)[1].strip(" -–:.,")
    return not tail or len(_hebrew_tokens(tail)) <= 1


def _looks_like_order_proposal_intro_only(text: str) -> bool:
    compact = _compact(text)
    normalized = _norm(compact)
    if not normalized.startswith("הצעה לסדר"):
        return False
    if _has_explicit_local_topic_marker(normalized) or "–" in compact or " - " in compact:
        return False
    if _speaker_marker_re().search(compact):
        return True
    tokens = _hebrew_tokens(normalized)
    speech_cues = ["אני", "אנחנו", "אגיד", "אומר", "מבקש", "את צודקת", "בהמשך של זה", "הוא באמת", "קיבלת תשובה", "זה לא עובד"]
    return len(tokens) <= 9 and (normalized in {"הצעה לסדר", "הצעה לסדר היום"} or any(cue in normalized for cue in speech_cues))


def _looks_like_speaker_reference_heading(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    starts_with_speaker = re.match(r"^(?:יו\"?ר|יור|מר|גב'?|גברת)\b", normalized) is not None
    dialogue_cues = ["את צודקת", "בהמשך של זה", "הוא באמת", "תודה", "בבקשה", "רגע"]
    return starts_with_speaker and any(cue in normalized for cue in dialogue_cues)


def _looks_like_personal_announcement_handoff(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    if _has_bounded_substantive_topic_signal(normalized):
        return False
    handoff_cues = ("זכות הדיבור", "רשות הדיבור", "נותן למר", "נותנת למר", "נותן לגב", "נותנת לגב")
    personal_cues = ("הודעה אישית", "למסירת הודעה", "מסירת הודעה אישית")
    return any(cue in normalized for cue in handoff_cues) and any(cue in normalized for cue in personal_cues)


def _looks_like_speaker_dialogue_fragment(text: str) -> bool:
    compact = _compact(text)
    normalized = _norm(compact)
    if not normalized:
        return False
    if re.search(r"\b(?:בנושא|הנדון|נושא\s+נוסף|הנושא\s+הבא)\b", normalized):
        return False
    speaker_markers = re.findall(r":\s*(?:מר|גב'?|גברת|היו[\"'׳״]?ר)(?=\s|[-–]|$)|היו[\"'׳״]?ר\s*[-–]?\s*(?:מר|גב'?)", compact)
    if not speaker_markers:
        return False
    punctuation_breaks = len(re.findall(r"[.?!]|\s[-–]\s", compact))
    return len(speaker_markers) >= 2 or punctuation_breaks >= 2 or len(_hebrew_tokens(normalized)) >= 22


def _looks_like_legal_boilerplate_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    cues = ["לפקודת העיריות", "בתוקף סמכות", "המועצה החליטה", "להתקין חוק עזר", "חוק עזר זה"]
    return sum(1 for cue in cues if cue in normalized) >= 2


def _looks_like_dependent_legal_clause_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized or _has_explicit_local_topic_marker(normalized):
        return False
    tokens = _hebrew_tokens(normalized)
    if len(tokens) > 12:
        return False
    dependent_start = re.match(r"^(?:מתאימ[הים]?|בהכנת|ואישור[הו]?|בדרך\s+חוקית|או\s+בדרך\s+חוקית)\b", normalized) is not None
    cues = ("בהכנת תוכנית", "בהכנת תכנית", "תוכנית ואישורה", "תכנית ואישורה", "ואישורה", "בדרך חוקית אחרת")
    return dependent_start and sum(1 for cue in cues if cue in normalized) >= 2


def _looks_like_legal_section_reference_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized or _has_explicit_local_topic_marker(normalized):
        return False
    if re.match(r"^סעיף\s*\d{1,4}\s*[א-ת]\b", normalized):
        return True
    return False


def _looks_like_table_header_or_schema_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    header_cues = ["קוד", "תאור", "תיאור", "החלטה", "תוקף", "סטאטוס", "סטטוס"]
    if sum(1 for cue in header_cues if cue in normalized) >= 4:
        return True
    if re.fullmatch(r"(?:קוד|תאור|תיאור|החלטה|תוקף|סטאטוס|סטטוס|מס|עמוד)(?:\s+(?:קוד|תאור|תיאור|החלטה|תוקף|סטאטוס|סטטוס|מס|עמוד))*", normalized):
        return True
    return False


def _looks_like_signature_or_end_page_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    cues = ["תצלום סיום", "סיום הפרוטוקול", "חתימות", "יור הישיבה", "יו\"ר הישיבה", "מנכל", "מנכ\"ל", "הישיבה נעולה", "הישיבה ננעלה", "ננעלה בשעה"]
    return sum(1 for cue in cues if cue in normalized) >= 2


def _looks_like_contract_approval_procedure_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    procedure_cues = ["יובאו לאישור ועדת התקשרויות", "ההתקשרות בחוזה", "ללא מכרז", "ועדת התקשרויות עליונה"]
    cue_count = sum(1 for cue in procedure_cues if cue in normalized)
    return cue_count >= 3 or ("נספח" in normalized and cue_count >= 2)


def _looks_like_deliberation_continuation_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized or _has_explicit_local_topic_marker(normalized):
        return False
    cues = ("נראה את המשמעויות", "נקבל החלטות מושכלות", "נקבל החלטה מושכלת", "נקבל החלטות")
    substantive_markers = ("תקציב", "הסכם", "מינוי", "הקצאה", "תכנית", "תוכנית", "מכרז", "ארנונה", "חינוך", "רווחה", "תחבורה")
    return any(cue in normalized for cue in cues) and not any(term in normalized for term in substantive_markers)


def _looks_like_approval_timing_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized or _has_explicit_local_topic_marker(normalized):
        return False
    approval_cues = ("אחד האישורים", "האישורים שהיא צריכה לקבל", "האישור שהיא צריכה לקבל", "אישור שהיא צריכה לקבל")
    timing_cues = ("להקדים", "לעשות את זה", "צריך לקבל", "צריכה לקבל")
    return any(cue in normalized for cue in approval_cues) and any(cue in normalized for cue in timing_cues) and not _has_bounded_substantive_topic_signal(normalized)


def _looks_like_attendance_context_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    if _has_explicit_local_topic_marker(normalized):
        return False
    if "חברי האופוזיציה שלא נכחו בדיון" not in normalized and "חברי מועצה שלא נכחו בדיון" not in normalized:
        return False
    dangling_tail = re.search(r"\bאת\s+(?:מינוי|פרוטוקול(?:\s+הוועדה|\s+ועדת)?)?$", normalized) is not None
    protocol_attendance_tail = re.search(r"\bאת\s+פרוטוקול\s+(?:הוועדה|ועדת)\b", normalized) is not None
    return dangling_tail or protocol_attendance_tail or len(_hebrew_tokens(normalized)) <= 10


def _looks_like_no_proposal_or_agenda_action_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    if _has_bounded_substantive_topic_signal(normalized):
        return False
    no_proposal_cues = ("אין הצעה", "אין הצעה לסדר")
    process_cues = ("אנחנו פועלים", "אנחנו נעשה", "נעשה אותו", "בלי קשר")
    return any(cue in normalized for cue in no_proposal_cues) and any(cue in normalized for cue in process_cues)


def _looks_like_approval_requirement_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    if not any(cue in normalized for cue in ("מחייב אישור מועצת עיר", "מחייב אישור מועצה", "מחייב את אישור המועצה")):
        return False
    return not _has_bounded_substantive_topic_signal(normalized)


def _looks_like_protocol_formatting_discussion_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    discussion_cues = ("מברך על הפרוטוקול", "נייר מכתבים", "קל ללמוד אותו", "יתוקן")
    protocol_cues = ("פרוטוקול", "ועדה", "וועדה")
    return sum(1 for cue in discussion_cues if cue in normalized) >= 2 and any(cue in normalized for cue in protocol_cues)


def _looks_like_reply_attachment_procedure_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    attachment_cues = ("יצורף לפרוטוקול", "יצורף לפרוטוקול", "תצורף לפרוטוקול", "תשובה במשך הישיבה", "התשובה הועברה במייל", "תעביר אותה")
    if sum(1 for cue in attachment_cues if cue in normalized) < 2:
        return False
    return not _has_bounded_substantive_topic_signal(normalized)


def _looks_like_reply_attachment_tail(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    if not any(cue in normalized for cue in ("השאלה והתשובה מצורפות", "השאלה והתשובה מצורפת", "תשובה מצורפת לפרוטוקול", "תשובה מצורפות לפרוטוקול")):
        return False
    tail_cues = ("אני אשמח", "מחכים לו", "מצורפות לפרוטוקול", "מצורפת לפרוטוקול", "כנספח")
    return sum(1 for cue in tail_cues if cue in normalized) >= 1


def _looks_like_order_proposal_response_vote_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    response_to_proposal = bool(re.search(r"(?:^|\s)תגוב(?:ה|ת)\s+.{0,100}?ל?הצעה(?:\s|$)", normalized))
    if not response_to_proposal:
        return False
    vote_or_disposition = "ההצעה יורדת מסדר היום" in normalized or "הצעה יורדת מסדר היום" in normalized
    vote_or_disposition = vote_or_disposition or ("בעד" in normalized and "נגד" in normalized and ("נמנע" in normalized or "נמנעים" in normalized))
    return vote_or_disposition


def _looks_like_personal_topic_reference(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    personal_cues = [
        r"\b(?:שלי|שלנו)\b.{0,40}\b(?:בנושא|בעניין|לגבי)\b",
        r"\bאני\b.{0,50}\b(?:בנושא|בעניין|לגבי)\b",
        r"\bההצעה\s+שלי\b.{0,50}\b(?:בנושא|בעניין|לגבי)\b",
    ]
    return any(re.search(pattern, normalized) for pattern in personal_cues)


def _looks_like_no_topic_continuation(text: str) -> bool:
    compact = _compact(text).strip(" .:-–")
    cleaned = clean_protocol_subject_text(compact)
    if not compact and not cleaned:
        return True
    normalized = _norm(cleaned or compact)
    if re.fullmatch(r"(?:תועבר|תועברו|יועבר|יועברו)\s+אלי(?:ך|כם)?\s+בהמשך", normalized):
        return True
    if re.search(r"מלל\s+בתמליל\s+המלא", normalized):
        return True
    if re.search(r"הדיון\s+הקודם|למען\s+הסדר\s+הטוב|הוועדה\s+עומדת\s+מאחורי\s+החלטתה", normalized) and not any(term in normalized for term in ["בנושא", "הנדון"]):
        return True
    tokens = [token for token in _hebrew_tokens(normalized) if len(token) >= 3]
    if len(tokens) <= 2 and _has_short_topic_signal(normalized):
        return False
    return len(tokens) <= 2 and not any(term in normalized for term in ["בנושא", "הנדון", "הסכם", "מינוי", "תקציב", "ארנונה", "תמיכות", "הקצאה"])


def _has_short_topic_signal(text: str) -> bool:
    """Do not erase compact but meaningful agenda subjects.

    Short headings often contain only one domain keyword, e.g. employee, tax,
    road, or contract terms. Those are still valid topic subjects when the
    allowed root tree has a clear non-procedural match.
    """
    for match in topic_policy_matches(text, limit=3):
        root_topic_id = str(match.get("root_topic_id") or "")
        if root_topic_id in ROOT_BY_ID and root_topic_id not in {"root_agenda_queries", "root_order_proposals"}:
            return True
    for row in _allowed_root_alignment_scores(text):
        root_topic_id = str(row.get("root_topic_id") or "")
        if root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
            continue
        if root_topic_id in ROOT_BY_ID and float(row.get("score") or 0.0) >= 0.82 and row.get("matched_terms"):
            return True
    return False


def _looks_like_procedural_dialogue_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    procedural_hits = sum(1 for term in ["מיקרופון", "לדבר", "רשות דיבור", "זמן דיבור", "סדר היום", "סדר יום", "הצבעה מיוחדת", "אני אתן לך", "אני לא נותן", "עוברים לאמצע"] if term in normalized)
    if procedural_hits < 2:
        return False
    substantive_markers = ["בנושא", "הנדון", "הסכם", "ארנונה", "מפעל", "תאונות", "כביש", "תחבורה", "תמיכות", "הקצאה", "מינוי", "חינוך", "דירות", "שכירות"]
    return not any(term in normalized for term in substantive_markers)


def _looks_like_vote_or_discussion_procedure_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    cues = ["מי בעד", "מי נגד", "רוצים להצביע", "הצעת ההחלטה", "אפשר לקיים דיון", "דיון בהזדמנות אחרת"]
    if not any(cue in normalized for cue in cues):
        return False
    substantive_markers = ["בנושא", "הנדון", "הסכם", "ארנונה", "מפעל", "תאונות", "כביש", "תחבורה", "תמיכות", "הקצאה", "מינוי", "חינוך", "דירות", "שכירות", "מיגון"]
    return not any(term in normalized for term in substantive_markers)


def _looks_like_transcript_speech_fragment(text: str) -> bool:
    normalized = _norm(text)
    if not normalized or _has_explicit_local_topic_marker(normalized):
        return False
    speech_cues = ["אני רוצה", "אני מבקש", "אני אגיד", "אני אומר", "אני מזכיר", "אנחנו מתחילים", "אתם צריכים", "תסתכלו", "רגע", "אתה לא", "את יכולה", "עוד מישהו", "תודה", "לשאלתך", "שלחתי", "בדקתי", "חסרת לנו"]
    if not any(cue in normalized for cue in speech_cues):
        return False
    sentence_breaks = len(re.findall(r"[.;:]|\s[-–]\s", text))
    if sentence_breaks >= 1 or len(_hebrew_tokens(normalized)) >= 12:
        return True
    return bool(re.search(r"\bאני\b.{0,24}\b(?:אגיד|אומר|מבקש)\b", normalized))


def _has_explicit_local_topic_marker(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    markers = [
        r"\bבנושא\b",
        r"\bהנדון\b",
        r"\bנושא\s+נוסף\b",
        r"\bהנושא\s+הבא\b",
        r"\bלעניין\b",
        r"\bבעניין\b",
        r"\bאישור\s+[^.]{3,120}",
        r"\bהסכם\s+[^.]{3,120}",
    ]
    return any(re.search(pattern, normalized) for pattern in markers)


def _looks_like_attribution_fragment(text: str) -> bool:
    compact = _compact(text)
    if not compact:
        return False
    tokens = _hebrew_tokens(compact)
    if len(tokens) > 8:
        return False
    if re.search(r"(?:^|\s)(?:עו\"ד|מר|גב['׳]?|גברת|הרב|ד\"ר)(?=\s|[-–]|$)", compact) and not any(term in compact for term in ["בנושא", "הסכם", "מינוי", "תמיכות", "הקצאה"]):
        return True
    return bool(re.fullmatch(r"[\d\s./()\-–:]+", compact))


def _looks_like_vote_fragment(text: str) -> bool:
    compact = _compact(text)
    if not compact:
        return False
    vote_terms = ["הצביעו נגד", "הצביעו בעד", "לא השתתפו בהצבעה", "נוכחות בהצבעה", "משתתפים בהצבעה", "השתתפו בהצבעה", "נמנע", "נמנעו", "בעד:", "נגד:", "מי בעד", "מי נגד"]
    if not any(term in compact for term in vote_terms):
        return False
    substantive_terms = ["בנושא", "הסכם", "הקצאה", "מינוי", "תמיכות", "תקציב", "היטל", "שירותי שמירה"]
    return not any(term in compact for term in substantive_terms)


def _split_headline_contract(headline: str) -> tuple[str | None, str, str | None]:
    attribution = _extract_attribution(headline)
    raw_text = re.sub(r"\bבנו\s+[\"'׳״]?שא[\"'׳״]?\b", "בנושא", _compact(headline))
    raw_attributed_subject = _subject_after_carrier_attribution_separator(raw_text)
    if raw_attributed_subject:
        carrier, subject = raw_attributed_subject
        return carrier, subject, attribution
    text = _clean_heading(headline)
    text = re.sub(r"\bבנו\s+[\"'׳״]?שא[\"'׳״]?\b", "בנושא", text)
    if attribution:
        text = _strip_attribution_tail(text)
    attributed_subject = _subject_after_attribution_separator(text)
    if attributed_subject:
        return None, attributed_subject, attribution
    patterns = [
        (r"^(שאילת[אה]|שאילתה)\s+של\b.{0,120}?\s*בנושא\s+(.+)$", "שאילתה"),
        (r"^(שאילת[אה]|שאילתה)\s+של\b.{0,120}?[\"'׳״]\s*(.{4,220})$", "שאילתה"),
        (r"^(שאילת[אה]|שאילתה)\s+בנושא\s+(.+)$", "שאילתה"),
        (r"^(הצעה\s+לסדר)(?:\s+\d+(?:\.\d+)?)?\s*[-–:]?\s+(.+)$", "הצעה לסדר"),
        (r"^(נושא\s+לדיון)\s+(.+)$", "נושא לדיון"),
        (r"^(פרוטוקול\s+ועדה)\s+לקידום\s+מעמד\s+(.+)$", "פרוטוקול ועדה"),
        (r"^(פרוטוקול\s+ועדת)\s+(.+)$", "פרוטוקול ועדה"),
        (r"^(פרוטוקול\s+ועדה[^:,.]*)\s+(.+)$", "פרוטוקול ועדה"),
    ]
    for pattern, carrier_label in patterns:
        match = re.search(pattern, text)
        if match:
            subject = _clean_topic_subject(match.group(2))
            if pattern.startswith("^(פרוטוקול\\s+ועדה)\\s+לקידום") and subject:
                subject = _clean_topic_subject(f"קידום מעמד {subject}")
            elif carrier_label == "פרוטוקול ועדה" and "ועדת" in match.group(1) and subject and not subject.startswith("ועדת"):
                subject = _clean_topic_subject(f"ועדת {subject}")
            return carrier_label, subject, attribution
    # Numbered agenda rows often encode the real subject after the number.
    numbered = re.search(r"^(?:\d+(?:\.\d+)?\s*[.)]?\s*)+(.+)$", text)
    if numbered:
        return None, _clean_topic_subject(numbered.group(1)), attribution
    return None, _clean_topic_subject(text), attribution


def _subject_after_carrier_attribution_separator(text: str) -> tuple[str, str] | None:
    compact = _compact(text)
    if not compact:
        return None
    carrier_pattern = r"(?P<carrier>שאילת[אה]|שאילתה|הצעה\s+לסדר(?:\s+היום)?|נושא\s+לדיון)"
    match = re.search(rf"^{carrier_pattern}\s*[:\-–]?\s*(?P<left>.{{0,180}}?)\s+[-–]\s+(?P<right>.{{4,220}})$", compact)
    if not match:
        return None
    left = _compact(match.group("left"))
    right = _clean_topic_subject(match.group("right"))
    if not right or not _is_substantive_topic_subject(right):
        return None
    if left and not _looks_like_person_attribution_segment(left):
        return None
    return _carrier_label(match.group("carrier")), right


def _subject_after_attribution_separator(text: str) -> str | None:
    compact = _compact(text)
    if not compact or not re.search(r"\s[-–]\s", compact):
        return None
    left, right = re.split(r"\s[-–]\s", compact, maxsplit=1)
    left = _compact(left)
    right = _clean_topic_subject(right)
    if not right or not _is_substantive_topic_subject(right):
        return None
    if not _looks_like_person_attribution_segment(left):
        return None
    return right


def _carrier_label(value: str) -> str:
    normalized = _norm(value)
    if "הצעה לסדר" in normalized:
        return "הצעה לסדר"
    if "נושא לדיון" in normalized:
        return "נושא לדיון"
    return "שאילתה"


def _looks_like_person_attribution_segment(value: str) -> bool:
    text = _compact(value).strip(" :,-–")
    if not text:
        return True
    role_terms = (
        "חבר מועצה",
        "חבר המועצה",
        "חברת מועצה",
        "חברת המועצה",
        "חברי מועצה",
        "חברי המועצה",
        "ראש העיר",
        "סגן ראש העיר",
        "סגנית ראש העיר",
        "יו\"ר",
        "יור",
        "מר ",
        "גב'",
        "גברת",
        "עו\"ד",
        "ד\"ר",
        "פרופ",
    )
    if any(term in text for term in role_terms):
        return True
    tokens = [token for token in _hebrew_tokens(text) if len(token) >= 2]
    substantive_terms = ("תקציב", "הסכם", "מכרז", "מינוי", "חינוך", "רווחה", "בנייה", "בניה", "תכנון", "תחבורה", "מפונים")
    return len(tokens) <= 3 and not any(term in text for term in substantive_terms)


def _clean_topic_subject(value: str) -> str:
    raw_text = _compact(value)
    discussion_subject_pattern = r"^שעסק(?:ה|ו)?\s+בנושא\s+(.+?)(?=[.;,]|\s+(?:ו?ביקשנו|ו?אמרנו|ו?אני|ו?הוא|ו?היא|ו?דיון|ו?יהיה)\b|$)"
    raw_match = re.search(discussion_subject_pattern, raw_text)
    if raw_match:
        return clean_protocol_subject_text(raw_match.group(1))
    text = clean_protocol_subject_text(value)
    match = re.search(discussion_subject_pattern, text)
    if match:
        return clean_protocol_subject_text(match.group(1))
    return text


def _best_repeated_subject_before_or_after_decision(text: str) -> str:
    compact = _compact(text)
    if "החלטות" not in compact:
        return compact
    before, after = compact.split("החלטות", 1)
    before = _compact(before).strip(" '\"׳״()[]-–:.,")
    after = _compact(after).strip(" '\"׳״()[]-–:.,")
    if after and len(_hebrew_tokens(after)) >= 3 and not _looks_like_vote_fragment(after):
        return after
    return before or compact


def _strip_attribution_tail(value: Any) -> str:
    text = _compact(value)
    if not text:
        return ""
    patterns = [
        r",?\s+בקשת(?:ו|ה|ם|ן)?\s+של\b.*$",
        r",?\s+בקשה\s+של\b.*$",
        r"\s*\)?\s*בקשת(?:ו|ה|ם|ן)?\s+של\b.*$",
        r"\s*\)?\s*בקשה\s+של\b.*$",
        r",?\s+לבקשת(?:ו|ה|ם|ן)?\s+של\b.*$",
        r",?\s+בבקשה\b.*$",
        r",?\s+מאת\b.*$",
        r",?\s+על\s+ידי\b.*$",
        r"\([^)]{0,80}\)\s*$",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text)
    return _compact(text).strip(" '\"׳״()[]-–:.,")


def _extract_attribution(value: str) -> str | None:
    match = re.search(r"(?:בקשת(?:ו|ה|ם|ן)?\s+של|לבקשת(?:ו|ה|ם|ן)?\s+של|מאת|על\s+ידי)\s+(.+)$", _compact(value))
    return _compact(match.group(0))[:180] if match else None


def _is_substantive_topic_subject(subject: str) -> bool:
    text = _compact(subject)
    if not text:
        return False
    if _looks_like_procedural_packet_carrier_only(text):
        return False
    tokens = [token for token in _hebrew_tokens(text) if len(token) >= 3]
    if len(tokens) < 2:
        return _has_short_topic_signal(text)
    if len(text) <= 3 or re.fullmatch(r"[\d\W_]+", text):
        return False
    # Single-person/attribution fragments should not become topics.
    if len(tokens) <= 2 and not any(term in text for term in ["חינוך", "תמיכות", "רווחה", "שמירה", "הסכם", "מינוי", "עובדים", "הקצאה", "הקצאות"]) and not _has_short_topic_signal(text):
        return False
    return True


def _topic_contexts_by_unit(units: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    current_context: dict[str, Any] | None = None
    section_contexts: dict[str, dict[str, Any]] = {}
    for index, unit in enumerate(units):
        unit_id = str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or "")
        role = str(unit.get("structural_role") or "")
        section_id = str(unit.get("section_id") or "")
        parent_id = str(unit.get("continuation_of_unit_id") or "")
        heading = _headline_from_unit(unit)
        replacement_heading = _cleaner_continuation_heading(units=units, index=index, parent_unit_id=unit_id, parent_heading=heading)
        if replacement_heading:
            heading = replacement_heading
        context: dict[str, Any] = {}
        if role == "vote_or_result" and parent_id and parent_id in contexts:
            context = {**contexts[parent_id], "context_source": "parent_agenda"}
        elif role == "vote_or_result" and section_id and section_id in section_contexts:
            context = {**section_contexts[section_id], "context_source": "section_agenda"}
        elif role == "vote_or_result" and current_context:
            context = {**current_context, "context_source": "previous_agenda"}
        elif heading and role not in {"metadata", "noise", "table_header_only"} and not _looks_like_protocol_metadata(_compact(unit.get("raw_text"))):
            candidate_context = _topic_context_payload(title=heading, source="unit_heading", parent_unit_id=unit_id)
            contract = _topic_contract_from_headline(heading, structural_role=role, packet_role="protocol")
            if bool(contract.get("is_topic_bearing")) and not _looks_like_protocol_listing(heading):
                context = candidate_context
                current_context = context
                if section_id:
                    section_contexts[section_id] = context
        elif parent_id and parent_id in contexts:
            context = {**contexts[parent_id], "context_source": "parent_agenda"}
        elif section_id and section_id in section_contexts and role in {"body", "continuation", "vote_or_result", "task_row"}:
            context = {**section_contexts[section_id], "context_source": "section_agenda"}
        elif current_context and role in {"continuation", "vote_or_result", "task_row"}:
            context = {**current_context, "context_source": "previous_agenda"}
        contexts[unit_id] = context
    return contexts


def _cleaner_continuation_heading(*, units: list[dict[str, Any]], index: int, parent_unit_id: str, parent_heading: str) -> str:
    parent = units[index]
    parent_raw = _compact(parent.get("raw_text"))
    if not parent_unit_id or not _looks_like_malformed_protocol_subject(parent_heading or parent_raw):
        return ""
    parent_score = _subject_quality_score(parent_heading)
    section_id = str(parent.get("section_id") or "")
    for next_unit in units[index + 1 : min(len(units), index + 4)]:
        if str(next_unit.get("continuation_of_unit_id") or "") != parent_unit_id and str(next_unit.get("section_id") or "") != section_id:
            continue
        if str(next_unit.get("structural_role") or "") in {"vote_or_result", "metadata", "noise", "table_header_only"}:
            continue
        candidate = _headline_from_unit(next_unit)
        candidate_score = _subject_quality_score(candidate)
        if candidate and not _looks_like_malformed_protocol_subject(candidate) and candidate_score >= 4:
            return candidate
        if candidate and candidate_score >= max(4, parent_score + 2):
            return candidate
    return ""


def _looks_like_malformed_protocol_subject(text: str) -> bool:
    compact = _compact(text)
    if not compact:
        return False
    carrier_count = sum(1 for term in ["פרוטוקול", "ועדת", "ביקורת", "דו\"ח", "החלטות", "מס", "מתאריך"] if term in compact)
    return carrier_count >= 3 or (carrier_count >= 2 and re.search(r"\d", compact) is not None) or bool(re.search(r"\b\d+(?:\.\d+)?\b.*\b(?:פרוטוקול|ועדת|ביקורת)\b", compact))


def _subject_quality_score(text: str) -> int:
    compact = _compact(text)
    if not compact:
        return 0
    score = len([token for token in _hebrew_tokens(compact) if len(token) >= 3])
    score -= 2 * sum(1 for term in ["פרוטוקול", "החלטות", "מתאריך", "מס", "מאשרים", "הצביעו"] if term in compact)
    if re.search(r"\b(?:בנושא|התמודדות|הקמת|תיקון|עבודה|הקצאות|תמיכות|רווחה|שמירה|אלימות)\b", compact):
        score += 3
    return score


def _topic_context_payload(*, title: str, source: str, parent_unit_id: str) -> dict[str, Any]:
    title = _compact(title)
    return {
        "agenda_item_title_he": title,
        "topic_identification_text": title,
        "parent_agenda_unit_id": parent_unit_id,
        "context_source": source,
    }


def _headline_from_unit(unit: dict[str, Any]) -> str:
    headline, _source = _headline_with_source_from_unit(unit)
    return headline


def _headline_source_from_unit(unit: dict[str, Any]) -> str:
    _headline, source = _headline_with_source_from_unit(unit)
    return source


def _headline_with_source_from_unit(unit: dict[str, Any]) -> tuple[str, str]:
    for key, source in (("header_text", "explicit_header"), ("title_he", "explicit_title"), ("section_title_he", "explicit_section_title")):
        value = _clean_heading(_compact(unit.get(key)))
        if value:
            return value, source
    raw_text = _compact(unit.get("raw_text"))
    extracted = _extract_heading_from_text(raw_text)
    if extracted:
        return extracted, "raw_extracted"
    role = str(unit.get("structural_role") or "")
    if role in {"outline_item", "section_heading"} and len(raw_text) <= 260 and _looks_titleish(raw_text):
        return _clean_heading(raw_text), "raw_titleish"
    return "", "none"


def _extract_heading_from_text(text: str) -> str:
    compact = _compact(text)
    if not compact:
        return ""
    if _looks_like_legal_section_reference_fragment(compact):
        return ""
    decision_heading = _extract_explicit_decision_heading(compact)
    if decision_heading:
        return decision_heading
    patterns = [
        r"((?:תקצוב|עדכון\s+תקציב|אישור\s+תקציבי|מינוי|אצילת\s+סמכויות|העברת\s+סמכויות|עסקאות\s+חכירה|ויתור\s+על\s+[^.;:\n]{0,90}?חכירה|בקשה\s+לקידום\s+מתחם\s+[^.;:\n]{0,140}?|התחדשות\s+עירונית\s+[^.;:\n]{0,140}?)[^.;:\n]{6,220}?)(?=\s+(?:לעירייה\s+לאשר|נדרש|מצ\"?ב|בהתאם|כמקובל)|[.;:\n]|$)",
        r"(שאילתה\s+בנושא\s+.{6,220}?)(?=,\s*בקשת|\s+בקשת|\s+הוקראה|\s+השאלה|\s+התשובה|\s+מצורפ|\s+מצ\"?ל|\s*[-–]\s*מצ|[.;]|$)",
        r"(הצעה\s+לסדר(?:\s+בנושא)?\s+.{6,220}?)(?=,\s*בקשת|\s+בקשת|\s+הועלתה|\s+מצ\"?ל|[.;]|$)",
        r"(נושא\s+לדיון\s+.{6,220}?)(?=,\s*בקשת|\s+בקשת|\s+מצ\"?ל|[.;]|$)",
        r"(פרוטוקול\s+ועדה\s+לקידום\s+מעמד\s+['׳״]?הילד)(?=\s+מס|\s+מתאריך|[.;]|$)",
        r"(פרוטוקול\s+ועדת\s+['׳״]?רווחה)(?=\s+מס|\s+מתאריך|[.;]|$)",
        r"(פרוטוקול\s+ועדה\s+לקידום\s+מעמד\s+הילד)(?=\s+מס|\s+מתאריך|[.;]|$)",
        r"(פרוטוקול\s+ועדת\s+רווחה)(?=\s+מס|\s+מתאריך|[.;]|$)",
        r"(פרוטוקול\s+ועדת\s+הקצאות\s+מקצועית)(?=\s+מס|\s+מתאריך|[.;]|$)",
        r"(פרוטוקול\s+ועדת\s+משנה\s+להקצאות\s+קרקע)(?=\s+מס|\s+מתאריך|[.;]|$)",
        r"(פרוטוקול\s+ועדה\s+משנה\s+לתמיכות)(?=\s+מס|\s+מתאריך|[.;]|$)",
        r"((?:דו\"?ח\s+)?ביקורת\s+.{6,180}?)(?=\s+מס\b|\s+מתאריך|[.;]|$)",
        r"(מינוי\s+[^.;:\n]{6,180}?)(?=[.;:\n]|$)",
        r"(?:^|[\s:])(?:הנדון|נדון|בנושא|נושא)\s*[:'\"׳״\-–]?\s*(.{6,180}?)(?=[.;\n]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, compact)
        if match:
            value = _clean_heading(match.group(1))
            if value:
                return value
    numbered = re.search(r"^(?:סעיף\s*)?\d+(?:\.\d+)?\s*[.)]?\s*:?\s*[\"'׳״()\s\-–]*(.{6,220})", compact)
    if numbered:
        raw_value = numbered.group(1)
        repaired = _repair_embedded_carrier_subject(raw_value)
        if repaired:
            return repaired
        value = _clean_heading(raw_value)
        nested = _extract_heading_from_text(value) if value != compact else ""
        if nested:
            return nested
        if value:
            return value
    if len(compact) <= 240 and _looks_titleish(compact):
        return _clean_heading(compact)
    return ""


def _extract_explicit_decision_heading(text: str) -> str:
    match = re.match(r"^\s*החלטה\s*[:：]\s*(.{6,260})", _compact(text))
    if not match:
        return ""
    body = match.group(1)
    body = re.split(r"\s*[-–]\s*(?:מ\s*)?א\s*ו\s*ש\s*ר\b|\s+מאושר\b|\s+אושר\b|[.;\n]", body, maxsplit=1)[0]
    heading = _clean_heading(body)
    normalized = _norm(heading)
    if not normalized or re.match(r"^(?:מ\s*)?א\s*ו\s*ש\s*ר\b|^מאושר\b|^אושר\b|^לא\s+אושר\b", normalized):
        return ""
    if normalized.startswith(("ההצעה", "הבקשה", "הסעיף", "הנושא")):
        return ""
    return heading if len(_hebrew_tokens(heading)) >= 2 else ""


def _clean_heading(value: str) -> str:
    text = _compact(value)
    if not text:
        return ""
    text = re.sub(r"^(?:סעיף\s*)?\d+(?:\.\d+)?\s*[.)]?\s*:?\s*", "", text)
    text = re.sub(r"^(?:\)?\([^)]{0,30}\)\s*)?(?:מתאריך\s+)?\d+(?:\.\d+)?\s*['׳״.\-–,\s]*", "", text)
    text = re.sub(r"^מתאריך\s+\d+(?:\.\d+)?\s*['׳״.\-–,\s]*", "", text)
    text = re.sub(r"^(?:מצ\"?ל\s*[-–]?\s*)+", "", text)
    text = text.strip(" '\"׳״()[]-–:.")
    text = re.sub(r"\s*[-–]?\s*מצ\"?ל.*$", "", text)
    text = re.sub(r"\s+מצורפ(?:ת|ות|ים)?\b.*$", "", text)
    text = re.sub(r",?\s+בקשת(?:ו|ה|ם|ן)?\b.*$", "", text)
    text = re.sub(r"\s+הוקראה\b.*$", "", text)
    text = re.sub(r"\s+השאלה\b.*$", "", text)
    text = re.sub(r"\s+התשובה\b.*$", "", text)
    text = re.sub(r"\(?\b\d{1,2}\.\d{1,2}\.\d{2,4}\b\)?", " ", text)
    text = re.sub(r"['׳״]+(?=[\u0590-\u05FF])", "", text)
    text = re.sub(r"\s+156\d+\b.*$", "", text)
    text = clean_protocol_subject_text(text)
    text = text.strip(" '\"׳״()[]-–:.")
    return _compact(text)[:220]


def _repair_embedded_carrier_subject(value: Any) -> str:
    text = _compact(value)
    if not text or "הצעה לסדר" not in text:
        return ""
    match = re.search(r"הצעה\s+לסדר", text)
    if not match:
        return ""
    before, after = text[: match.start()], text[match.end() :]
    before = clean_protocol_subject_text(before)
    after = clean_protocol_subject_text(after)
    after = re.sub(r"^\d+(?:\.\d+)?\s*", "", after).strip(" -–:.,")
    if not before or not after:
        return before or after
    parts = [part.strip(" -–:.,") for part in re.split(r"\s+[-–]\s+", before) if part.strip(" -–:.,")]
    if len(parts) >= 2:
        before = " - ".join([parts[-1], *parts[:-1]])
    return clean_protocol_subject_text(f"{before} {after}")


def _looks_titleish(text: str) -> bool:
    compact = _compact(text)
    if not compact:
        return False
    if re.search(r"^(?:סעיף\s*)?\d+(?:\.\d+)?\s*[.)]", compact):
        return True
    terms = ("שאילתה בנושא", "הצעה לסדר", "נושא לדיון", "אישור", "מינוי", "הסכם", "פרוטוקול ועד", "ועדה", "תקצוב", "עדכון תקציב", "עסקאות", "חכירה", "התחדשות עירונית", "ותמ\"ל", "ותמל")
    return any(term in compact for term in terms)


def _topic_actions_for_unit(*, actions: list[str], topic_text: str, raw_text: str, packet_role: str) -> list[str]:
    if packet_role != "protocol":
        return _dedupe_strings(actions)
    # For protocol rows, actions extracted from the answer/body are evidence only.
    # They must not become topic-identification support merely because they appear in raw_text.
    support_text = topic_text
    supported = []
    for action in actions:
        compact = _compact(action)
        if not compact:
            continue
        if _tokens_supported(compact, support_text) or _tokens_supported(topic_text, compact):
            contract = _topic_contract_from_headline(compact, structural_role="", packet_role="protocol")
            supported.append(str(contract.get("topic_subject_he") or _strip_attribution_tail(compact)))
    return _dedupe_strings(supported)[:4]


def _topic_basis_text(item: dict[str, Any]) -> str:
    topic_text = _compact(item.get("topic_identification_context"))
    if _is_protocol_item(item) and topic_text:
        return topic_text
    return str(item.get("raw_text") or "")


def _shape_guard_raw_text(*, item: dict[str, Any], topic_text: str, fallback_text: str) -> str:
    if _is_protocol_item(item) and str(item.get("topic_context_source") or "") == "subject_scoped_speaker_anchor":
        return topic_text
    return fallback_text


def _subject_scoped_transcript_topic_anchor(*, unit: dict[str, Any], document_context: dict[str, Any]) -> dict[str, Any] | None:
    if str(document_context.get("topic_carrier_mode") or "") != "subject_scoped_transcript_topics":
        return None
    role = str(unit.get("structural_role") or "")
    if role in {"metadata", "noise", "table_header_only", "vote_or_result"}:
        return None
    raw_text = _compact(unit.get("raw_text"))
    if not raw_text or _looks_like_vote_fragment(raw_text) or _looks_like_signature_or_end_page_fragment(raw_text):
        return None
    candidates = _subject_scoped_anchor_candidates(raw_text)
    if not candidates:
        return None
    protocol_subject = str(document_context.get("protocol_subject_he") or "")
    valid: list[dict[str, Any]] = []
    for candidate in candidates:
        subject = _clean_subject_scoped_anchor_label(str(candidate.get("topic_subject_he") or ""))
        if not subject or _norm(subject) == _norm(protocol_subject):
            continue
        if _non_topic_protocol_reason(headline=subject, raw_text=subject, structural_role=role, packet_role="protocol"):
            continue
        if not _is_substantive_topic_subject(subject):
            continue
        root_topic_id = infer_root_topic_id(subject)
        if root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
            subject_root = infer_root_topic_id(_join_unique([subject, protocol_subject]))
            if subject_root in ROOT_BY_ID and subject_root not in {"root_agenda_queries", "root_order_proposals"}:
                root_topic_id = subject_root
        if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
            continue
        valid.append({**candidate, "topic_subject_he": subject, "root_topic_id": root_topic_id})
    if not valid:
        return None
    valid.sort(key=lambda row: (float(row.get("score") or 0.0), len(str(row.get("topic_subject_he") or ""))), reverse=True)
    best = valid[0]
    return {
        "topic_subject_he": best["topic_subject_he"],
        "context_source": "subject_scoped_speaker_anchor",
        "headline_source": str(best.get("source") or "subject_scoped_speaker_opening"),
        "topic_supporting_quote_he": _compact(best.get("quote_he"))[:500],
    }


def _subject_scoped_anchor_candidates(text: str) -> list[dict[str, Any]]:
    compact = _strip_protocol_chrome(text)
    if not compact or not _speaker_marker_re().search(compact):
        return []
    opening_segments = _speaker_opening_segments(compact)
    if not opening_segments:
        return []
    rows: list[dict[str, Any]] = []
    patterns = [
        (r"(?:הסתייגות|ההסתייגות)\s+(?:היא\s+)?(?:בנושא|בעניין|לגבי)\s+(.{4,180}?)(?=\s*:\s*(?:מר|גב'?|גברת|היו[\"'׳״]?ר)|[.;?]|$)", "speaker_subject_objection", 0.92),
        (r"(?:הסתייגות|ההסתייגות)\s+בנוגע\s+.{0,80}?\bבנושא\s+([^.;:?]{4,120})", "speaker_subject_objection", 0.9),
        (r"(בתקציב\s+[^.;:?]{4,120}?)(?=\d|\s+הסתייגות|\s+ההסתייגות|[.;:]|$)", "speaker_subject_budget_phrase", 0.86),
        (r"לתב[\"']?ר\s+בנושא\s+([^.;:?]{4,120})", "speaker_subject_capital_budget", 0.86),
        (r"סעיף\s+[\"'׳״]([^\"'׳״]{3,90})[\"'׳״]", "speaker_quoted_section_label", 0.85),
        (r"(ביטול\s+תב[\"']?ר[^.;:?]{4,120})", "speaker_action_subject", 0.84),
        (r"(שיפור\s+תאורה[^.;:?]{0,100})", "speaker_action_subject", 0.82),
        (r"([\u0590-\u05FF][\u0590-\u05FF\s\"'׳״()]{4,90}?)[-–]\s*\d{2,}(?:/\d+)*\s*סעיף", "speaker_section_label", 0.84),
        (r"סעיף(?:\s+תקציבי)?\s*[\d/]+\s*[-–]?\s*([^.;:?]{4,120})", "speaker_section_label", 0.74),
    ]
    for segment in opening_segments:
        for pattern, source, score in patterns:
            for match in re.finditer(pattern, segment):
                raw_label = match.group(1)
                label = _clean_subject_scoped_anchor_label(_refine_subject_scoped_anchor_label(raw_label))
                if label:
                    rows.append({"topic_subject_he": label, "source": source, "quote_he": match.group(0), "score": score})
    return rows


def _refine_subject_scoped_anchor_label(value: str) -> str:
    text = _compact(value)
    if not text:
        return ""
    text = re.sub(r"^(?:מר|גב'?|גברת)?\s*[\u0590-\u05FF]{2,20}\s+(?=(?:הסתייגות|ההסתייגות|בנוגע|היא\s+בנושא|מליון|ועומד))", "", text)
    normalized = _norm(text)
    phrase_rules = [
        ("מצבת כוח האדם", "מצבת כוח האדם"),
        ("מסגרות ותכניות טיפוליות", "מסגרות ותכניות טיפוליות"),
        ("מסגרות ותוכניות טיפוליות", "מסגרות ותכניות טיפוליות"),
        ("תשלומי רשות נוספים", "תשלומי רשות נוספים"),
        ("התחנה המרכזית", "התחנה המרכזית"),
        ("החלפה ושיקום מדרכות", "החלפה ושיקום מדרכות"),
        ("שיפור תאורה", "שיפור תאורה"),
    ]
    for needle, label in phrase_rules:
        if needle in normalized:
            return label
    match = re.search(r"(תכנית\s+[^,.;:]{4,100}(?:,\s*[^,.;:]{4,80}){0,3})", text)
    if match:
        return match.group(1)
    match = re.search(r"(עתודת\s+[^,.;:]{4,80})(?=,|\s+סעיף|$)", text)
    if match:
        return match.group(1)
    match = re.search(r"(פארק\s+[^,.;:]{4,120})", text)
    if match and "ביטול" in normalized:
        return f"ביטול תב\"ר {match.group(1)}"
    match = re.search(r"(?:שיפור|לשיפור)\s+(?:ה)?מרחב\s+הציבורי\s+([^,.;:]{0,80})", text)
    if match:
        return _compact(f"שיפור המרחב הציבורי {match.group(1)}")
    match = re.search(r"בתקציב\s+([^.;:,]{4,100})", text)
    if match:
        return f"תקציב {match.group(1)}"
    match = re.search(r"עתודה.{0,80}?סעיף\s+[\"'׳״]([^\"'׳״]{3,80})[\"'׳״]", text)
    if match:
        return f"עתודה {match.group(1)}"
    match = re.search(r"(?:לעבודות|עבודות)\s+([^.;:,]{4,120})", text)
    if match:
        return f"עבודות {match.group(1)}"
    return text


def _clean_subject_scoped_anchor_label(value: str) -> str:
    text = clean_protocol_subject_text(value)
    text = re.sub(r"\s+השנה\s+לעומת\s+שנה\s+שעברה\b.*$", "", text)
    text = re.sub(r",?\s*(?:קיצוץ|תוספת|גידול|הוספה)\b.*$", "", text)
    text = re.sub(r"\bסעיף\s+שכלל\b.*$", "", text)
    text = re.sub(r"\b(?:הופיע|הופיעו)\s+בתקציבים\s+קודמים\b.*$", "", text)
    text = re.sub(r"\b(?:הסתייגות|ההסתייגות|בעניין|לגבי|הוספה|משמעותית|קיצוץ|של|בסך|סך)\b", " ", text)
    text = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:מליון|מיליון|שח|ש\"ח|₪|%)\b", " ", text)
    text = re.sub(r"\b(?:מר|גב'?|גברת|היו[\"'׳״]?ר)\b.*$", "", text)
    text = re.sub(r"(?<=[\u0590-\u05FF])[\"'׳״](?=,|$)", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ' \"׳״()[]-–:.,")
    if _looks_like_subject_scoped_dialogue_only(text):
        return ""
    if _norm(text).startswith("תקציב העירייה") or "ספר התקציב נסגר" in _norm(text):
        return ""
    if text.startswith("תקציב") and not _specific_budget_phrase(text):
        return ""
    return text[:180]


def _specific_budget_phrase(text: str) -> bool:
    normalized = _norm(text)
    tokens = [token for token in _hebrew_tokens(normalized) if len(token) >= 3]
    if not tokens or tokens[0] != "תקציב":
        return True
    stopwords = {"תקציב", "הקודם", "העירייה", "הרגיל", "רגיל", "בהיקף", "השנה", "לעומת", "ואין", "אם", "גידול", "בסה"}
    meaningful = [token for token in tokens[1:] if token not in stopwords]
    return len(meaningful) >= 2


def _looks_like_subject_scoped_dialogue_only(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return True
    dialogue_cues = ["אני", "אתה", "את", "אנחנו", "רגע", "תודה", "תסתכלו", "תגיד", "עוד מישהו", "נמוך", "להלן סעיף", "סעיף הוראות", "לא יכול", "יכולה", "ראש העירייה", "הוא יהיה", "מעכשיו", "נדע"]
    if any(cue in normalized for cue in dialogue_cues):
        return True
    return len([token for token in _hebrew_tokens(normalized) if len(token) >= 3]) < 2


def _speaker_opening_segments(text: str) -> list[str]:
    compact = _strip_protocol_chrome(text)
    segments: list[str] = []
    for match in _speaker_marker_re().finditer(compact):
        after = compact[match.end() : match.end() + 220]
        after = re.sub(r"^\s*[.:;!?\-–]+\s*", "", after)
        after = re.sub(r"^\s*[\u0590-\u05FF][\u0590-\u05FF'׳״\-–]{1,24}\s*[.:;!?\-–]+\s*", "", after)
        after = re.split(r"\s*:\s*(?:מר|גב'?|גברת|היו[\"'׳״]?ר)|[.?!]", after, maxsplit=1)[0]
        after = _compact(after).strip(" ' \"׳״()[]-–:.,")
        if after:
            segments.append(after)
    return segments


def _strip_protocol_chrome(text: str) -> str:
    compact = _compact(text)
    compact = re.sub(r"פרוטוקול\s+ישיבות?\s+המועצה[^:.;]{0,160}?[-–]\s*\d+\s*[-–]", " ", compact)
    compact = re.sub(r"^\s*סעיף\s*\d{1,6}(?:/\d+){0,3}\s*", "סעיף ", compact)
    return _compact(compact)


def _speaker_marker_re() -> re.Pattern[str]:
    return re.compile(r":\s*(?:מר|גב'?|גברת|היו[\"'׳״]?ר)(?=\s|[-–]|$)|היו[\"'׳״]?ר\s*[-–]?\s*(?:מר|גב'?)")


def _validation_evidence_text(*, item: dict[str, Any], quote: str, selected_candidate: dict[str, Any] | None) -> str:
    candidate_quote = str((selected_candidate or {}).get("evidence_quote_he") or "")
    if _is_protocol_item(item):
        # Validate child/root semantics against the agenda/headline context; the body quote is retained only as supporting evidence.
        return _join_unique([_topic_basis_text(item), "\n".join(str(action) for action in item.get("explicit_actions") or []), candidate_quote])
    return _join_unique([str(item.get("raw_text") or ""), "\n".join(str(action) for action in item.get("explicit_actions") or []), candidate_quote, quote])


def _is_protocol_item(item: dict[str, Any]) -> bool:
    return str((item.get("document_context") or {}).get("packet_role") or "") == "protocol"


def _document_context(*, input_pdf: str | None, packet_role: str, units: list[dict[str, Any]]) -> dict[str, Any]:
    path = Path(input_pdf).expanduser() if input_pdf else None
    file_stem = path.stem if path else ""
    all_actions: list[str] = []
    if packet_role != "protocol":
        for unit in units:
            for action in unit.get("explicit_actions") or []:
                text = _compact(action)
                if text and text not in all_actions:
                    all_actions.append(text)
    subject = _protocol_subject_from_title_or_first_pages(file_stem=file_stem, units=units) if packet_role == "protocol" else {}
    protocol_subject = str(subject.get("protocol_subject_he") or "")
    topic_carrier_mode = "subject_scoped_transcript_topics" if _uses_subject_scoped_transcript_topics(protocol_subject=protocol_subject, units=units) else "headline_topics"
    return {
        "pdf_file_stem": file_stem,
        "packet_role": packet_role or "unknown",
        "municipality_he": _municipality_from_units(units) if packet_role == "protocol" else None,
        "document_title_candidates": _title_candidates_from_filename(file_stem),
        "protocol_subject_he": protocol_subject or None,
        "protocol_subject_source": subject.get("source"),
        "protocol_subject_quote_he": subject.get("quote_he"),
        "temporal_metadata": subject.get("temporal_metadata") or [],
        "topic_carrier_mode": topic_carrier_mode,
        "explicit_actions": all_actions[:12],
    }


def _municipality_from_units(units: list[dict[str, Any]]) -> str | None:
    text = _compact(" ".join(str(unit.get("raw_text") or "") for unit in units[: min(len(units), 12)]))
    if not text:
        return None
    patterns = (
        r"עיריית\s+([\u0590-\u05FF][\u0590-\u05FF\s\-]{1,28})",
        r"מועצה\s+מקומית\s+([\u0590-\u05FF][\u0590-\u05FF\s\-]{1,28})",
        r"מועצה\s+אזורית\s+([\u0590-\u05FF][\u0590-\u05FF\s\-]{1,28})",
    )
    stop_words = {"לבין", "מחלקה", "מנהל", "בגוש", "רח", "רובע", "הנדסה", "גזבר"}
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        candidate = _compact(match.group(1)).strip(" '()[]-–:.,")
        words = []
        for word in candidate.split():
            if word in stop_words:
                break
            words.append(word)
            if len(words) >= 3:
                break
        value = _compact(" ".join(words))
        if value:
            return value
    return None


def _protocol_subject_from_title_or_first_pages(*, file_stem: str, units: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for title in _title_candidates_from_filename(file_stem):
        candidates.extend(_protocol_subject_candidates_from_text(text=title, source="filename"))
    for unit in units[: min(len(units), 6)]:
        raw_text = _compact(unit.get("raw_text"))
        if raw_text:
            candidates.extend(_protocol_subject_candidates_from_text(text=raw_text, source="early_document_text"))
        for key in ("header_text", "title_he", "section_title_he"):
            value = _compact(unit.get(key))
            if value:
                candidates.extend(_protocol_subject_candidates_from_text(text=value, source=f"early_{key}"))
    candidates = [row for row in candidates if _is_substantive_topic_subject(str(row.get("protocol_subject_he") or ""))]
    candidates = [row for row in candidates if str(row.get("root_topic_id") or "") not in {"root_agenda_queries", "root_order_proposals"}]
    if not candidates:
        return {}
    candidates.sort(key=lambda row: (float(row.get("score") or 0.0), len(str(row.get("protocol_subject_he") or ""))), reverse=True)
    return candidates[0]


def _protocol_subject_candidates_from_text(*, text: str, source: str) -> list[dict[str, Any]]:
    compact = _compact(text)
    if not compact:
        return []
    rows: list[dict[str, Any]] = []
    patterns = [
        r"סדר\s+הישיבה\s+(.{4,180}?)(?=\*{3,}|\s+:\s*(?:היו[\"'׳״]?ר|מר|גב'?)|$)",
        r"(?:^|\s)[-–]\s*([^.;:\n]{4,120}?)(?=\.pdf|$)",
        r"(?:ישיבה\s+לא\s+מן\s+המניין|ישיבה\s+מיוחדת).{0,140}?\b(תקציב\s*\d{4}|דו[\"']?ח\s+מבקר[^.;:\n]{0,80}|ביקורת[^.;:\n]{0,80})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, compact):
            raw = _compact(match.group(1))
            candidate = _clean_protocol_subject_candidate(raw)
            row = _protocol_subject_candidate_row(candidate=candidate, quote=match.group(0), source=source)
            if row:
                rows.append(row)
    if re.search(r"\bתקציב\s*\d{4}\b", compact) and any(term in compact for term in ["לא מן המניין", "סדר הישיבה", "פרוטוקול"]):
        row = _protocol_subject_candidate_row(candidate="תקציב", quote=compact[:240], source=source)
        if row:
            rows.append(row)
    return rows


def _clean_protocol_subject_candidate(value: str) -> str:
    text = _compact(value)
    text = re.sub(r"\*+", " ", text)
    text = clean_protocol_subject_text(text)
    text = re.sub(r"\s+(?:לשנת|בשנת|שנת)\s*$", "", text)
    return _compact(text).strip(" ' \"׳״()[]-–:.,")


def _protocol_subject_candidate_row(*, candidate: str, quote: str, source: str) -> dict[str, Any] | None:
    subject = _clean_protocol_subject_candidate(candidate)
    if not subject or _looks_like_protocol_listing(subject) or _looks_like_malformed_protocol_subject_candidate(subject):
        return None
    root_topic_id = infer_root_topic_id(subject)
    if root_topic_id not in ROOT_BY_ID or root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
        return None
    score = _subject_quality_score(subject) + (4 if source.startswith("filename") else 0) + (3 if "סדר הישיבה" in quote else 0)
    return {
        "protocol_subject_he": subject,
        "root_topic_id": root_topic_id,
        "source": source,
        "quote_he": _compact(quote)[:300],
        "score": score,
        "temporal_metadata": _temporal_metadata_from_text(quote),
    }


def _uses_subject_scoped_transcript_topics(*, protocol_subject: str, units: list[dict[str, Any]]) -> bool:
    if not protocol_subject:
        return False
    early_text = _compact(" ".join(str(unit.get("raw_text") or "") for unit in units[: min(len(units), 6)]))
    if not early_text:
        return False
    single_subject_signal = "לא מן המניין" in early_text or "ישיבה מיוחדת" in early_text or "סדר הישיבה" in early_text
    if not single_subject_signal:
        return False
    speaker_markers = sum(len(_speaker_marker_re().findall(_compact(unit.get("raw_text")))) for unit in units)
    return speaker_markers >= 8


def _looks_like_malformed_protocol_subject_candidate(subject: str) -> bool:
    text = _compact(subject)
    if not text:
        return True
    if _looks_like_malformed_protocol_subject(text):
        return True
    normalized = _norm(text)
    speech_or_deictic_cues = [
        "אני",
        "אנחנו",
        "אנו",
        "אתם",
        "בפניכם",
        "כאן",
        "כמו",
        "דנים",
        "נדון",
        "דנו",
        "הזה",
        "הזאת",
        "שלנו",
    ]
    if any(cue in normalized for cue in speech_or_deictic_cues):
        return True
    connector_cues = ["וגם", "אבל", "ואז", "ועכשיו", "מכל מקום"]
    if any(cue in normalized for cue in connector_cues):
        return True
    if re.search(r"\b[בלמכש]?[-–]\s*,", text):
        return True
    if text.count(",") >= 2:
        return True
    return False


def _temporal_metadata_from_text(text: str) -> list[str]:
    values = re.findall(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b|\b\d{4}\b", _compact(text))
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out[:8]


def _document_child_candidates(*, units: list[dict[str, Any]], document_context: dict[str, Any], topic_contexts_by_unit: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if str(document_context.get("packet_role") or "") == "protocol":
        # Protocol-wide candidates must come from bounded headings, not the transcript/body text.
        combined_text = "\n".join(
            _compact(context.get("topic_identification_text"))
            for context in topic_contexts_by_unit.values()
            if _compact(context.get("topic_identification_text")) and not _looks_like_protocol_listing(str(context.get("topic_identification_text") or ""))
        )
    else:
        combined_text = "\n".join(
            _compact(value)
            for unit in units
            for value in [unit.get("raw_text"), unit.get("summary_he"), "\n".join(str(action) for action in unit.get("explicit_actions") or [])]
            if _compact(value)
        )
    for label, source, quote in _raw_candidate_labels(text=combined_text, explicit_actions=document_context.get("explicit_actions") or [], document_context=document_context):
        rows.append(_candidate_row(label=label, evidence_source=source, evidence_quote=quote, support_text=combined_text, confidence_hint=0.82 if source in {"heading", "explicit_action"} else 0.68))
    return _dedupe_candidates([row for row in rows if row is not None])[:12]


def _candidate_child_topics(
    *,
    unit_id: str,
    text: str,
    evidence_text: str,
    outline_title: str,
    explicit_actions: list[str],
    document_context: dict[str, Any],
    document_child_candidates: list[dict[str, Any]],
    referenced_attachment_contexts: list[dict[str, Any]],
    topic_tree: dict[str, Any],
    structural_role: str,
) -> list[dict[str, Any]]:
    packet_role = str(document_context.get("packet_role") or "")
    if packet_role == "protocol" and structural_role == "metadata":
        return []
    local_support_text = "\n".join([text, outline_title, "\n".join(explicit_actions)]) if packet_role == "protocol" else "\n".join([evidence_text, outline_title, "\n".join(explicit_actions)])
    if packet_role == "protocol" and _looks_like_protocol_listing(local_support_text):
        return []
    child_match_text = "\n".join([text, outline_title])
    rows = _tree_child_candidates(topic_tree=topic_tree, text=child_match_text, referenced_attachment_contexts=referenced_attachment_contexts)
    rows.extend(_semantic_child_facet_candidates(topic_tree=topic_tree, text=child_match_text))
    rows.extend(_locally_supported_document_candidates(candidates=document_child_candidates, local_text=local_support_text, document_context=document_context))
    for label, source, quote in _raw_candidate_labels(text="\n".join([text, outline_title]), explicit_actions=explicit_actions, document_context=document_context):
        if packet_role == "protocol" and source not in {"heading", "explicit_action", "subject_heading", "question_subject", "filename_supported"}:
            continue
        candidate = _candidate_row(label=label, evidence_source=source, evidence_quote=quote, support_text="\n".join([text, outline_title]), confidence_hint=0.86 if source in {"heading", "explicit_action"} else 0.72)
        if candidate:
            rows.append(candidate)
    for context in referenced_attachment_contexts:
        label = _compact(context.get("child_label_he"))
        quote = _compact(context.get("topic_supporting_quote_he"))
        if packet_role == "protocol" and not _candidate_supported_by_local_text(candidate={"label_he": label, "aliases_he": []}, local_text=local_support_text):
            continue
        candidate = _referenced_attachment_candidate_row(context=context, label=label, evidence_quote=quote or label, confidence_hint=0.72)
        if candidate:
            rows.append(candidate)
    out = _dedupe_candidates(rows)[:12]
    for index, row in enumerate(out, start=1):
        row["candidate_child_id"] = f"{unit_id}_child_{index}_{_short_hash(row['root_topic_id'], row['label_he'])[:8]}"
    return out


def _tree_child_candidates(*, topic_tree: dict[str, Any], text: str, referenced_attachment_contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = _compact(text)
    if not compact:
        return []
    rows: list[dict[str, Any]] = []
    for root in topic_tree.get("root_topics") or topic_tree.get("roots") or []:
        root_topic_id = str(root.get("root_topic_id") or "")
        root_label = str(root.get("root_label_he") or "")
        for child in root.get("children") or []:
            if not _importable_tree_child(child=child, root_topic_id=root_topic_id):
                continue
            label = _compact(child.get("child_label_he") or child.get("label_he"))
            profile = child.get("profile") if isinstance(child.get("profile"), dict) else {}
            aliases = [str(value) for value in [*(child.get("aliases_he") or []), *(profile.get("aliases_he") or [])] if str(value).strip()]
            score = _existing_child_match_score(label=label, text=compact, support_count=int(child.get("support_count") or child.get("support") or 0), aliases_he=aliases)
            if score < 0.46:
                continue
            candidate = _existing_tree_candidate_row(root_topic_id=root_topic_id, root_label=root_label, child=child, label=label, evidence_quote=compact[:500], confidence_hint=score)
            if candidate:
                rows.append(candidate)
    return rows


def _semantic_child_facet_candidates(*, topic_tree: dict[str, Any], text: str) -> list[dict[str, Any]]:
    """Return existing child nodes matched by reusable action/object facets.

    These are closed-list candidates: the helper never invents labels, it only
    selects child nodes already present in the tree when the agenda text carries
    a generic municipal action signal such as tender, exemption, TBR, or emergency
    readiness.
    """

    compact = _compact(text)
    normalized = _norm(compact)
    if not normalized:
        return []
    rows: list[dict[str, Any]] = []
    for root_id, labels, required, blocked, confidence in _semantic_child_facet_rules():
        if any(term in normalized for term in blocked):
            continue
        if not all(any(term in normalized for term in group) for group in required):
            continue
        for label in labels:
            candidate = _existing_child_candidate_by_label(topic_tree=topic_tree, root_topic_id=root_id, label=label, evidence_quote=compact[:500], confidence_hint=confidence)
            if candidate:
                candidate["evidence_source"] = "existing_tree"
                candidate["match_reason"] = "semantic_child_facet"
                rows.append(candidate)
                break
    return rows


def _semantic_child_facet_rules() -> tuple[tuple[str, tuple[str, ...], tuple[tuple[str, ...], ...], tuple[str, ...], float], ...]:
    return (
        ("root_budget_finance", ("הנחות ופטורים",), (("פטור", "פטורים", "הנחה", "הנחות", "לא ישולם", "לא תשולם", "אי גבייה", "אי-גבייה"),), (), 0.88),
        ("root_budget_finance", ("אגרות והיטלים",), (("אגרה", "אגרות", "היטל", "היטלים", "תעריף", "תעריפים"),), ("פטור", "הנחה", "לא ישולם", "לא תשולם"), 0.82),
        ("root_budget_finance", ("מימון פרויקטים עירוניים",), (("תב ר", "תבר", "תב\"ר", "קרן", "פרויקט", "פרויקטים"),), (), 0.84),
        ("root_budget_finance", ("תקצוב שירותים עירוניים",), (("תקציב", "עתודה", "עתודת", "סעיף תקציבי", "העברות תקציב"),), ("תבר", "תב ר", "תב\"ר", "פטור", "הנחה", "היטל", "אגרה"), 0.8),
        ("root_agreements", ("מכרזים והתקשרויות",), (("מכרז", "מכרזים", "התקשרות", "התקשרויות", "זוכה", "פטור ממכרז"),), (), 0.86),
        ("root_security_enforcement", ("מוכנות לחירום",), (("מיגון", "חירום", "מקלט", "פיקוד העורף", "מלח", "מל ח", "מל\"ח"),), (), 0.84),
        ("root_education", ("מוסדות חינוך",), (("גן", "גני ילדים", "בית ספר", "בתי ספר", "מוסד חינוך", "מוסדות חינוך"),), (), 0.86),
    )


def _existing_child_candidate_by_label(*, topic_tree: dict[str, Any], root_topic_id: str, label: str, evidence_quote: str, confidence_hint: float) -> dict[str, Any] | None:
    label_norm = _norm(label)
    for root in topic_tree.get("root_topics") or topic_tree.get("roots") or []:
        if str(root.get("root_topic_id") or "") != root_topic_id:
            continue
        root_label = str(root.get("root_label_he") or root_label_for_id(root_topic_id) or "")
        for child in root.get("children") or []:
            if not _importable_tree_child(child=child, root_topic_id=root_topic_id):
                continue
            child_label = _compact(child.get("child_label_he") or child.get("label_he"))
            if _norm(child_label) != label_norm:
                continue
            return _existing_tree_candidate_row(root_topic_id=root_topic_id, root_label=root_label, child=child, label=child_label, evidence_quote=evidence_quote, confidence_hint=confidence_hint)
    return None


def _locally_supported_document_candidates(*, candidates: list[dict[str, Any]], local_text: str, document_context: dict[str, Any]) -> list[dict[str, Any]]:
    if str(document_context.get("packet_role") or "") != "protocol":
        return list(candidates)
    return [candidate for candidate in candidates if _candidate_supported_by_local_text(candidate=candidate, local_text=local_text)]


def _candidate_supported_by_local_text(*, candidate: dict[str, Any], local_text: str) -> bool:
    compact = _compact(local_text)
    if not compact:
        return False
    labels = [str(candidate.get("label_he") or ""), *(str(alias) for alias in candidate.get("aliases_he") or [])]
    return any(label and _tokens_supported(label, compact) for label in labels)


def _looks_like_protocol_listing(text: str) -> bool:
    compact = _compact(text)
    if not compact:
        return False
    bracketed_items = len({match.group(1) for match in re.finditer(r"\[\s*(\d+(?:\.\d+)?)\s*\]", compact)})
    dot_leaders = len(re.findall(r"\.{6,}", compact))
    agenda_mentions = len({_norm(match.group(0)) for match in re.finditer(r"(?:שאילתה\s+בנושא\s+[^,;.]{4,120}|הצעה\s+לסדר\s+[^,;.]{4,120}|נושא\s+לדיון\s+[^,;.]{4,120}|פרוטוקול\s+ועדת?\s+[^,;.]{4,120}|אישור\s+[^,;.]{4,120})", compact)})
    numbered_items = len(re.findall(r"(?:^|\s)\d+(?:\.\d+)?\s*[.)]", compact))
    page_ref_mentions = len(re.findall(r"\(?\s*עמ", compact))
    dotted_suffix_items = len(re.findall(r"\.\d{1,2}(?=\s|[)'\"׳״]|$)", compact))
    if numbered_items >= 3 and not any([bracketed_items, dot_leaders, agenda_mentions]) and _looks_like_numbered_legal_or_rule_clause(compact):
        return False
    return bracketed_items >= 2 or (bracketed_items >= 1 and dot_leaders >= 1) or agenda_mentions >= 2 or numbered_items >= 3 or ("סדר הישיבה" in compact and page_ref_mentions >= 3) or (page_ref_mentions >= 2 and dotted_suffix_items >= 2)


def _looks_like_numbered_legal_or_rule_clause(text: str) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    legal_cues = ["סעיף", "חוק", "פקודה", "תקנות", "דיני", "נוסח חדש", "כהגדרתו", "קבע מנהל"]
    rule_cues = ["לא ישולם", "לא תשולם", "פטור", "היטל", "ארנונה", "אגרה", "תשלום", "נכס", "יחול", "זכאי"]
    return sum(1 for cue in legal_cues if cue in normalized) >= 1 and sum(1 for cue in rule_cues if cue in normalized) >= 2


def _existing_tree_candidate_row(*, root_topic_id: str, root_label: str, child: dict[str, Any], label: str, evidence_quote: str, confidence_hint: float) -> dict[str, Any] | None:
    tree_label = _compact(label)
    cleaned = clean_topic_label(tree_label)
    if not tree_label or not cleaned or root_topic_id not in ROOT_BY_ID:
        return None
    validation = validate_child_label(raw_label=cleaned, root_label_he=root_label, evidence_text=evidence_quote, selected_existing=True)
    if validation.status != "active" or not validation.cleaned_label:
        return None
    profile = child.get("profile") if isinstance(child.get("profile"), dict) else {}
    aliases = _dedupe_strings([tree_label, validation.cleaned_label, *(child.get("aliases_he") or []), *(profile.get("aliases_he") or [])])
    metadata_schema = child.get("metadata_schema") if isinstance(child.get("metadata_schema"), dict) else profile.get("metadata_schema")
    return {
        "candidate_child_id": str(child.get("child_topic_id") or child.get("topic_id") or ""),
        "label_he": tree_label,
        "root_topic_id": root_topic_id,
        "root_label_he": root_label,
        "evidence_quote_he": _compact(evidence_quote)[:500],
        "evidence_source": "existing_tree",
        "confidence_hint": max(0.0, min(1.0, float(confidence_hint))),
        "aliases_he": [alias for alias in aliases if _norm(alias) != _norm(tree_label)],
        "profile": _compact_profile(profile),
        "metadata_schema": compact_topic_metadata_schema(metadata_schema, max_fields=14),
    }


def _importable_tree_child(*, child: dict[str, Any], root_topic_id: str) -> bool:
    if str(child.get("status") or "active") != "active":
        return False
    child_root_id = str(child.get("root_topic_id") or root_topic_id)
    if child_root_id in ROOT_BY_ID and child_root_id != root_topic_id:
        return False
    label = str(child.get("child_label_he") or child.get("label_he") or "")
    return bool(clean_topic_label(label)) and not is_low_quality_topic_label(label)


def _referenced_attachment_candidate_row(*, context: dict[str, Any], label: str, evidence_quote: str, confidence_hint: float) -> dict[str, Any] | None:
    cleaned = clean_topic_label(label)
    root_topic_id = str(context.get("root_topic_id") or "")
    if not cleaned or root_topic_id not in ROOT_BY_ID:
        return None
    root_label = root_label_for_id(root_topic_id) or str(context.get("root_label_he") or "")
    validation = validate_child_label(raw_label=cleaned, root_label_he=root_label, evidence_text=evidence_quote, selected_existing=True)
    if validation.status != "active" or not validation.cleaned_label:
        return None
    return {
        "candidate_child_id": str(context.get("child_topic_id") or ""),
        "label_he": validation.cleaned_label,
        "root_topic_id": root_topic_id,
        "root_label_he": root_label,
        "evidence_quote_he": _compact(evidence_quote)[:500],
        "evidence_source": "referenced_attachment",
        "confidence_hint": max(0.0, min(1.0, float(confidence_hint) + min(0.12, float(context.get("attachment_match_score") or 0.0) * 0.15))),
        "aliases_he": [label] if label and _norm(label) != _norm(validation.cleaned_label) else [],
    }


def _existing_child_match_score(*, label: str, text: str, support_count: int, aliases_he: list[str]) -> float:
    label_norm = _norm(label)
    text_norm = _norm(text)
    if not label_norm or not text_norm:
        return 0.0
    if label_norm in text_norm:
        return 0.96
    alias_scores = [_label_text_match_score(label=str(alias), text_norm=text_norm, support_count=support_count, exact_score=0.94) for alias in aliases_he]
    label_score = _label_text_match_score(label=label_norm, text_norm=text_norm, support_count=support_count, exact_score=0.96)
    return max([label_score, *alias_scores, 0.0])


def _label_text_match_score(*, label: str, text_norm: str, support_count: int, exact_score: float) -> float:
    label_norm = _norm(label)
    if not label_norm:
        return 0.0
    if label_norm in text_norm:
        return exact_score
    tokens = _hebrew_tokens(label_norm)
    if not tokens:
        return 0.0
    hits = [token for token in tokens if token in text_norm]
    ratio = len(hits) / max(1, len(tokens))
    if len(hits) >= min(len(tokens), max(2, len(tokens) - 1)):
        return min(0.9, 0.58 + ratio * 0.24 + min(0.08, support_count / 100.0))
    if len(hits) >= 2 and support_count >= 3:
        return min(0.72, 0.42 + ratio * 0.2 + min(0.08, support_count / 100.0))
    return 0.0


def _compact_profile(profile: Any) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    return {
        "summary_he": str(profile.get("summary_he") or "")[:240],
        "aliases_he": [str(alias) for alias in (profile.get("aliases_he") or [])[:8] if str(alias).strip()],
        "positive_examples": [
            {
                "quote_he": str(example.get("quote_he") or "")[:260],
                "source_kind": example.get("source_kind"),
            }
            for example in (profile.get("positive_examples") or [])[:3]
            if isinstance(example, dict) and str(example.get("quote_he") or "").strip()
        ],
        "negative_examples": [example for example in (profile.get("negative_examples") or [])[:3] if isinstance(example, dict)],
        "metadata_schema": compact_topic_metadata_schema(profile.get("metadata_schema"), max_fields=14),
        "labeling_method": str(profile.get("labeling_method") or "deterministic_curated_topic_tree"),
    }


def _raw_candidate_labels(*, text: str, explicit_actions: list[str], document_context: dict[str, Any]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    compact = re.sub(r"\bבנו\s+[\"'׳״]?שא[\"'׳״]?\b", "בנושא", _compact(text))
    patterns = [
        (r"(?:^|\s)(?:הנדון|נדון)\s*[:\-–]?\s+([^.;\n]{4,150})", "heading"),
        (r"(?:^|\s)(?:בנושא|נושא)\s*[:\-–]?\s+([^.;\n]{4,150})", "heading"),
        (r"(?:שאילת[אה]|שאילתה)\s+של\b.{0,120}?\s*בנושא\s+([^.;\n]{4,150})", "question_subject"),
        (r"(?:שאילת[אה]|שאילתה)\s+של\b.{0,120}?[\"'׳״]\s*([^.;\n]{4,150})", "question_subject"),
    ]
    for pattern, source in patterns:
        for match in re.finditer(pattern, compact):
            out.append((match.group(1), source, match.group(0)))
    for action in explicit_actions:
        label, route = derive_child_candidate(text=action)
        if label:
            out.append((label, "explicit_action", action))
        else:
            action_match = re.search(r"((?:סמינר|השתלמות|כנס|קורס|הכשרה)\s+[^.;\n]{6,150}?)(?=\s+בין\s+התאריכים|\s+בתאריכים|\s+מיום|\s+בתאריך|[.;\n]|$)", _compact(action))
            if action_match:
                out.append((action_match.group(1), "explicit_action", action))
    label, route = derive_child_candidate(text=compact, outline_title_he="")
    if label:
        out.append((label, route, compact[:500]))
    for title in document_context.get("document_title_candidates") or []:
        cleaned = clean_topic_label(title)
        if cleaned and _tokens_supported(cleaned, compact):
            out.append((cleaned, "filename_supported", title))
    return out


def _candidate_row(*, label: str | None, evidence_source: str, evidence_quote: str, support_text: str, confidence_hint: float) -> dict[str, Any] | None:
    raw_label = _strip_attribution_tail(label)
    cleaned = clean_topic_label(raw_label)
    if not cleaned:
        return None
    root_basis = _clean_topic_subject(cleaned) or _clean_topic_subject(evidence_quote) or cleaned
    root_topic_id = infer_root_topic_id(root_basis)
    root_label = root_label_for_id(root_topic_id) or ""
    validation = validate_child_label(raw_label=cleaned, root_label_he=root_label, evidence_text="\n".join([support_text, evidence_quote]), selected_existing=True)
    if validation.status != "active" or not validation.cleaned_label:
        return None
    adjusted_confidence = max(0.0, min(1.0, float(confidence_hint)))
    normalized_blob = _norm("\n".join([validation.cleaned_label, evidence_quote]))
    if any(term in normalized_blob for term in ["עלויות", "הוצאות", "הוצאה", "נסיעה"]):
        adjusted_confidence -= 0.18
    if any(term in normalized_blob for term in ["אשרו", "אישור", "מאשרים", "מאושר"]) and not any(term in normalized_blob for term in ["עלויות", "הוצאות"]):
        adjusted_confidence += 0.06
    if any(term in normalized_blob for term in ["סמינר", "השתלמות", "כנס", "קורס", "הכשרה"]):
        adjusted_confidence += 0.04
    return {
        "candidate_child_id": "",
        "label_he": validation.cleaned_label,
        "root_topic_id": root_topic_id,
        "root_label_he": root_label,
        "evidence_quote_he": _compact(evidence_quote)[:500],
        "evidence_source": evidence_source,
        "confidence_hint": max(0.0, min(1.0, adjusted_confidence)),
        "aliases_he": [raw_label] if raw_label and _norm(raw_label) != _norm(validation.cleaned_label) else [],
    }


def _best_high_confidence_candidate(*, item: dict[str, Any], root_topic_id: str) -> dict[str, Any] | None:
    candidates = [row for row in item.get("candidate_child_topics") or [] if str(row.get("root_topic_id") or "") == root_topic_id]
    if not candidates:
        return None
    candidates.sort(key=lambda row: (float(row.get("confidence_hint") or 0.0), len(str(row.get("label_he") or ""))), reverse=True)
    best = candidates[0]
    return best if float(best.get("confidence_hint") or 0.0) >= 0.72 else None


def _matching_candidate_by_label(*, item: dict[str, Any], root_topic_id: str, label: str) -> dict[str, Any] | None:
    label_norm = _norm(label)
    for candidate in item.get("candidate_child_topics") or []:
        if str(candidate.get("root_topic_id") or "") != root_topic_id:
            continue
        if _norm(str(candidate.get("label_he") or "")) == label_norm:
            return candidate
    return None


def _dedupe_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("root_topic_id") or ""), _norm(str(row.get("label_he") or "")))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        out.append(row)
    out.sort(key=lambda row: (float(row.get("confidence_hint") or 0.0), len(str(row.get("label_he") or ""))), reverse=True)
    return out


def _title_candidates_from_filename(file_stem: str) -> list[str]:
    if not file_stem:
        return []
    text = re.sub(r"__[^\s_]+$", "", file_stem)
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"\bpdfua\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{4}\b", " ", text)
    text = _compact(text)
    return [text] if text else []


def _tokens_supported(label: str, text: str) -> bool:
    label_tokens = {token for token in re.findall(r"[\u0590-\u05FF]{3,}", label)}
    text_norm = _norm(text)
    if not label_tokens or not text_norm:
        return False
    hits = {token for token in label_tokens if _token_supported_in_text(token=token, text_norm=text_norm)}
    return len(hits) >= min(len(label_tokens), max(2, len(label_tokens) - 1))


def _token_supported_in_text(*, token: str, text_norm: str) -> bool:
    if token in text_norm:
        return True
    # Hebrew construct forms commonly alternate final ה/ת in short labels, e.g.
    # עמותה in the label and עמותת in the source text.
    if token.endswith("ה") and f"{token[:-1]}ת" in text_norm:
        return True
    return False


def _hebrew_tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[\u0590-\u05FF]{2,}", value) if len(token) >= 2]


def _dedupe_strings(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = _norm(text)
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _join_unique(values: list[Any]) -> str:
    return "\n".join(_dedupe_strings([_compact(value) for value in values if _compact(value)]))


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _short_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()[:16]


def _load_attachment_contexts(paths: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for value in paths or []:
        path = Path(value).expanduser().resolve()
        payload = _load_json(path)
        if isinstance(payload, list):
            out.extend([row for row in payload if isinstance(row, dict)])
        else:
            out.extend(attachment_contexts_from_retrieval_chunks(path))
    return out[:300]


def _load_topic_subject_v3_hints(paths: list[str]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for value in paths or []:
        path = Path(value).expanduser().resolve()
        payload = _load_json(path)
        rows = payload if isinstance(payload, list) else payload.get("events") if isinstance(payload, dict) else []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            hint = _topic_subject_v3_hint_from_row(row)
            if not hint:
                continue
            for key in _topic_subject_v3_hint_keys(row=row):
                index.setdefault(key, []).append(hint)
    return {key: values[:3] for key, values in index.items()}


def _topic_subject_v3_hints_for_unit(*, unit: dict[str, Any], unit_id: str, raw_text: str, hint_index: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    keys = [f"unit:{unit_id}"]
    for value in [unit.get("source_ordinal"), unit.get("ordinal")]:
        if value not in (None, ""):
            keys.append(f"ordinal:{value}")
    raw_norm = _norm(raw_text)
    if raw_norm:
        keys.append(f"raw:{hashlib.sha1(raw_norm[:500].encode('utf-8')).hexdigest()[:16]}")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in keys:
        for hint in hint_index.get(key) or []:
            hint_key = _norm(_join_unique([hint.get("matter_he"), hint.get("action_type_he"), hint.get("source_quote_he")]))
            if not hint_key or hint_key in seen:
                continue
            seen.add(hint_key)
            out.append(hint)
    return out[:3]


def _topic_subject_v3_hint_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    quality = str(row.get("quality_status") or row.get("validation_status") or row.get("judge_status") or "")
    event = row.get("event") if isinstance(row.get("event"), dict) else {}
    payload = row.get("event_payload") if isinstance(row.get("event_payload"), dict) else event.get("event_payload") if isinstance(event.get("event_payload"), dict) else row.get("model_prediction") if isinstance(row.get("model_prediction"), dict) else {}
    source_rows = row.get("event_source_rows") if isinstance(row.get("event_source_rows"), list) else event.get("event_source_rows") if isinstance(event.get("event_source_rows"), list) else []
    first_source_row = source_rows[0] if source_rows and isinstance(source_rows[0], dict) else {}
    row_role = str(row.get("row_role") or payload.get("target_row_role") or first_source_row.get("row_role") or "")
    event_role = str(row.get("event_role") or payload.get("target_event_role") or "")
    if not event_role and bool(payload.get("is_event")) and row_role == "action_anchor":
        event_role = "primary"
    if not event_role:
        event_role = str(first_source_row.get("event_role") or "")
    if quality != "accepted" or row_role != "action_anchor" or event_role != "primary":
        return None
    entailment = payload.get("v3_evidence_entailment") if isinstance(payload.get("v3_evidence_entailment"), dict) else {}
    has_entailment = bool(entailment)
    assessments = entailment.get("field_assessments") if isinstance(entailment.get("field_assessments"), dict) else {}
    matter_assessment = assessments.get("matter_he") if isinstance(assessments.get("matter_he"), dict) else {}
    action_assessment = assessments.get("action_type_he") if isinstance(assessments.get("action_type_he"), dict) else {}
    subject_metadata = row.get("subject_metadata") if isinstance(row.get("subject_metadata"), dict) else {}
    event_metadata = row.get("event_metadata") if isinstance(row.get("event_metadata"), dict) else {}
    matter = _compact(payload.get("matter_he") or subject_metadata.get("matter_he"))
    matter_display = _compact(payload.get("matter_display_he") or subject_metadata.get("matter_display_he"))
    action = _compact(payload.get("action_type_he") or event_metadata.get("action_type_he"))
    if has_entailment and not assessments and str(entailment.get("entailment_status") or "") != "entailed":
        return None
    if has_entailment and assessments:
        if matter and str(matter_assessment.get("status") or "") != "entailed":
            return None
        if action and str(action_assessment.get("status") or "") != "entailed":
            return None
    quote = _compact(matter_assessment.get("source_quote_he") or action_assessment.get("source_quote_he") or row.get("raw_text_he") or row.get("anchor_source_text_he"))
    if not matter and not action:
        return None
    event_candidates = _topic_subject_v3_event_candidates(payload)
    artifact_id = str(row.get("artifact_id") or event.get("artifact_id") or "")
    source_unit_id = str(row.get("source_structure_unit_id") or first_source_row.get("source_structure_unit_id") or first_source_row.get("structure_unit_id") or "")
    if not source_unit_id:
        match = re.search(r"((?:s\d{4}_\d{2}|vh\d{3}_\d{2}_\d{2})_[0-9a-f]{12})", artifact_id)
        source_unit_id = match.group(1) if match else ""
    return {
        "source": "topic_subject_v3",
        "artifact_id": artifact_id or None,
        "source_structure_unit_id": source_unit_id or None,
        "matter_he": matter or None,
        "matter_display_he": matter_display or None,
        "action_type_he": action or None,
        "event_phase": _compact(payload.get("event_phase")) or None,
        "source_quote_he": quote[:300] or None,
        "selected_candidate_id": _compact(payload.get("selected_candidate_id")) or None,
        "event_candidates": event_candidates,
        "quality_status": quality,
        "row_role": row_role,
        "event_role": event_role,
        "entailment_status": "entailed",
    }


def _topic_subject_v3_event_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = payload.get("event_candidates") if isinstance(payload.get("event_candidates"), list) else []
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("is_event") is False:
            continue
        matter = _compact(candidate.get("matter_he"))
        matter_display = _compact(candidate.get("matter_display_he"))
        action = _compact(candidate.get("action_type_he"))
        if not matter and not action:
            continue
        out.append(
            {
                "candidate_id": _compact(candidate.get("candidate_id")) or None,
                "matter_he": matter or None,
                "matter_display_he": matter_display or None,
                "action_type_he": action or None,
                "action_quote_he": _compact(candidate.get("action_quote_he"))[:300] or None,
                "matter_quote_he": _compact(candidate.get("matter_quote_he"))[:300] or None,
                "phase_quote_he": _compact(candidate.get("phase_quote_he"))[:300] or None,
                "decision_quote_he": _compact(candidate.get("decision_quote_he"))[:300] or None,
                "is_current_action": bool(candidate.get("is_current_action")),
                "is_title_only": bool(candidate.get("is_title_only")),
                "confidence": _optional_confidence(candidate.get("confidence")),
            }
        )
    return out[:5]


def _topic_subject_v3_hint_keys(*, row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    event = row.get("event") if isinstance(row.get("event"), dict) else {}
    artifact_id = str(row.get("artifact_id") or event.get("artifact_id") or "")
    match = re.search(r"((?:s\d{4}_\d{2}|vh\d{3}_\d{2}_\d{2})_[0-9a-f]{12})", artifact_id)
    if match:
        keys.append(f"unit:{match.group(1)}")
    general = row.get("general_text_metadata") if isinstance(row.get("general_text_metadata"), dict) else {}
    source_ordinal = row.get("source_ordinal") or general.get("source_ordinal")
    if source_ordinal not in (None, ""):
        keys.append(f"ordinal:{source_ordinal}")
    raw_text = _compact(row.get("raw_text_he") or row.get("anchor_source_text_he") or general.get("raw_text_he"))
    if raw_text:
        keys.append(f"raw:{hashlib.sha1(_norm(raw_text)[:500].encode('utf-8')).hexdigest()[:16]}")
    return _dedupe_strings(keys)


def _facts_by_unit(facts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        unit_id = str(fact.get("structure_unit_id") or fact.get("semantic_unit_id") or "")
        if unit_id:
            out.setdefault(unit_id, []).append(fact)
    return out


def _parse_json_content(value: str) -> Any:
    text = value.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _compact(value: Any) -> str:
    return " ".join(re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", str(value or "")).split())


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.55


def _optional_confidence(value: Any) -> float | None:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


if __name__ == "__main__":
    raise SystemExit(main())
