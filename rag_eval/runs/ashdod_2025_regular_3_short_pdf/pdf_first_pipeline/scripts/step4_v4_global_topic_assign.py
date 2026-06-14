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
    ROOT_BY_ID,
    TOPIC_ASSIGNMENT_BACKEND,
    TOPIC_TREE_VERSION,
    V4_ROOT_TOPICS,
    attachment_contexts_from_retrieval_chunks,
    child_topic_id,
    clean_topic_label,
    derive_child_candidate,
    global_topic_tree_payload,
    infer_root_topic_id,
    referenced_attachment_contexts,
    root_label_for_id,
    resolve_child_topic_assignment,
    validate_child_label,
)
from municipality.pdf_first_v4_topic_policy import (  # noqa: E402
    adjudicate_root_topic,
    clean_protocol_subject_text,
    topic_policy_matches,
    topic_policy_prompt_payload,
)
from municipality.pdf_first_v4_topic_classifier import build_topic_profile_index, find_topic_candidates  # noqa: E402

DEFAULT_MODEL = "dicta-il/DictaLM-3.0-24B-Thinking:bf16"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
EXCLUDED_STRUCTURAL_ROLES = {"noise", "table_header_only"}
CACHE_VERSION = "step4_v4_global_topic_assign_v28_preserve_explicit_headline_provenance"


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 4 v4: assign PDF-first topics against the global semantic tree")
    parser.add_argument("--structure-units-json", required=True, help="Step 3.1 structure_units.json")
    parser.add_argument("--entity-facts-json", help="Optional Step 3.5 entity_facts.json")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--existing-tree-json", help="Optional current tree payload from DB or previous packet")
    parser.add_argument("--attachment-context-json", action="append", default=[], help="Attachment Step 5 retrieval_chunks.json; repeatable")
    parser.add_argument("--input-pdf", help="Original PDF path for document-level title context")
    parser.add_argument("--packet-role", choices=["attachment", "protocol", "unknown"], default="unknown")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-base-url", default=DEFAULT_OLLAMA_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--max-units-per-call", type=int, default=4)
    parser.add_argument("--max-raw-chars", type=int, default=1200)
    parser.add_argument("--dicta-mode", choices=["auto", "disabled", "required"], default="auto", help="auto uses Dicta only for ambiguous items; disabled imports ambiguous items as candidate topics; required sends topic-bearing items to Dicta")
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

    units = [unit for unit in structure_payload.get("structure_units") or [] if str(unit.get("structural_role") or "") not in EXCLUDED_STRUCTURAL_ROLES]
    facts_by_unit = _facts_by_unit(entity_payload.get("entity_facts") or [])
    topic_contexts_by_unit = _topic_contexts_by_unit(units)
    document_context = _document_context(input_pdf=args.input_pdf, packet_role=str(args.packet_role), units=units)
    document_child_candidates = _document_child_candidates(units=units, document_context=document_context, topic_contexts_by_unit=topic_contexts_by_unit)
    tree_payload = global_topic_tree_payload(existing_tree=existing_tree, attachment_contexts=[])
    topic_profile_index = build_topic_profile_index(tree_payload)
    items = []
    for unit in units:
        unit_id = str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or "")
        items.append(
            _build_item(
                unit=unit,
                facts=facts_by_unit.get(unit_id, []),
                max_raw_chars=args.max_raw_chars,
                attachment_contexts=attachment_contexts,
                document_context=document_context,
                topic_context=topic_contexts_by_unit.get(unit_id, {}),
                document_child_candidates=document_child_candidates,
                topic_tree=tree_payload,
                topic_index=topic_profile_index,
            )
        )
    for item in items:
        item["dicta_mode"] = str(args.dicta_mode)
    assignments_by_id: dict[str, dict[str, Any]] = {}
    model_items: list[dict[str, Any]] = []
    deterministic_count = 0
    low_confidence_candidate_count = 0
    for item in items:
        if _preassign_without_model(item):
            assignment = _non_topic_assignment(item)
            assignments_by_id[item["structure_unit_id"]] = assignment
            _write_cached_assignment(checkpoint_dir=checkpoint_dir, item=item, model=str(args.model), assignment=assignment)
        elif str(args.dicta_mode) != "required" and _preassign_with_candidate_finder(item):
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
    assignments = _mark_inherited_duplicate_topic_rows(assignments)
    assignments = _normalize_protocol_non_topic_assignments(assignments)

    validation = _validate(assignments=assignments, items=items, item_count=len(items), model_errors=model_errors, deterministic_count=deterministic_count, low_confidence_candidate_count=low_confidence_candidate_count, dicta_mode=str(args.dicta_mode))
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


def _build_item(*, unit: dict[str, Any], facts: list[dict[str, Any]], max_raw_chars: int, attachment_contexts: list[dict[str, Any]], document_context: dict[str, Any], topic_context: dict[str, Any], document_child_candidates: list[dict[str, Any]], topic_tree: dict[str, Any], topic_index: dict[str, Any]) -> dict[str, Any]:
    unit_id = str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or "")
    raw_text = _compact(unit.get("raw_text"))
    summary = _compact(unit.get("summary_he"))
    packet_role = str(document_context.get("packet_role") or "")
    source_actions = [str(value) for value in unit.get("explicit_actions") or [] if str(value).strip()]
    topic_text = _compact(topic_context.get("topic_identification_text"))
    headline_source = _headline_source_from_unit(unit)
    repaired_topic_text = _repair_embedded_carrier_subject(topic_text) or ("" if topic_text else _repair_embedded_carrier_subject(raw_text))
    if repaired_topic_text:
        topic_text = repaired_topic_text
    topic_contract = _topic_contract_from_headline(topic_text, structural_role=str(unit.get("structural_role") or ""), packet_role=packet_role)
    provenance_reason = _protocol_topic_provenance_reject_reason(unit=unit, headline=topic_text, raw_text=raw_text, topic_context_source=str(topic_context.get("context_source") or ""), headline_source=headline_source, packet_role=packet_role)
    if provenance_reason:
        topic_contract = {**topic_contract, "is_topic_bearing": False, "topic_subject_he": None, "non_topic_reason": provenance_reason}
    non_topic_reason = _non_topic_protocol_reason(headline=topic_text or raw_text, raw_text=raw_text, structural_role=str(unit.get("structural_role") or ""), packet_role=packet_role)
    if non_topic_reason:
        topic_contract = {**topic_contract, "is_topic_bearing": False, "topic_subject_he": None, "non_topic_reason": non_topic_reason}
    row_type = _row_type_for_unit(unit=unit, topic_contract=topic_contract, packet_role=packet_role)
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
        "section_id": unit.get("section_id"),
        "section_number": unit.get("section_number"),
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
        "topic_context_source": topic_context.get("context_source"),
        "topic_headline_source": headline_source,
        "topic_provenance_reject_reason": provenance_reason,
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
    }


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
            "If a substantive subject has no matching child topic, choose the closest allowed root_topic_id and leave child_choice_id/child_label_he null.",
            "Use existing child topics from the supplied tree when they match evidence.",
            "Choose child_choice_id from item.candidate_child_topics when a candidate is evidence-backed and more specific than the root.",
            "Treat candidate_child_topics as a closed classifier label set; prefer evidence-backed existing_tree candidates over new document-specific phrasing.",
            "Use candidate profile summaries, aliases, positive examples, and negative examples to judge aboutness; examples are guidance, not proof for the current item.",
            "Select an existing_tree child only when the current item evidence is about that child topic, not merely mentioning words from an example.",
            "Set child_choice_id to null only when no candidate_child_topics row is supported by the evidence.",
            "Do not invent child labels. If no candidate is acceptable, set child_choice_id null and child_label_he null.",
            "Never use נושא כללי.",
            "Do not invent entities, dates, geometry, or municipality-specific schema rules.",
            "For protocol items, identify the topic from topic_identification_context, document title, agenda title, section heading, bounded parent agenda context, or explicit referenced attachment context.",
            "For protocol items, separate agenda carrier/type from semantic subject: carrier examples include question/proposal/approval/protocol form; classify the semantic subject, not the carrier.",
            "Return agenda_carrier_he, topic_subject_he, attribution_he, and is_topic_bearing for every item so the assignment can be audited.",
            "Do not include requester, submitter, signer, vote, date, or person attribution inside child_label_he or topic_subject_he.",
            "If the headline/context is only a container, fragment, vote/result, person name, date, or generic procedural heading, set is_topic_bearing false and root_topic_id null.",
            "For protocol items, raw_text is evidence only; do not use transcript/body details as the primary topic source.",
            "topic_supporting_quote_he must be copied from raw_text, explicit_actions, document_context, or attachment context.",
            "Metadata role does not force root-only when explicit_actions or document_context contain a concrete municipal subject.",
            "Treat protocol/agenda carriers such as שאילתה, הצעה לסדר, אישור, פרוטוקול ועדה, ועדה, ביקורת, החלטות, dates, requester names, and vote text as context, not as the semantic subject.",
            "Apply topic_policies as reusable taxonomy rules. If a policy applies, return its policy_id and root_topic_id.",
            "If item.topic_policy_matches is not empty, copy item.topic_policy_matches[0].policy_id into policy_id and copy item.topic_policy_matches[0].root_topic_id into root_topic_id unless the item text clearly contradicts that policy.",
            "If no topic_policy applies, classify by the clean semantic subject and explain why the selected root fits better than nearby roots.",
            "Prefer reusable municipal subdomains over one-off action wording; if the only child candidate is too narrow, choose root-only.",
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
        "topic_tree": topic_tree,
        "topic_policies": topic_policy_prompt_payload(),
        "items": items,
    }
    body = {
        "model": model,
        "stream": False,
        "think": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": "/no_think\nYou are a Hebrew municipal topic judge. Use the supplied global topic tree. Return JSON only."},
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
            }
        )
    return out


def _load_cached_assignment(*, checkpoint_dir: Path, item: dict[str, Any], model: str) -> dict[str, Any] | None:
    path = _assignment_checkpoint_path(checkpoint_dir=checkpoint_dir, item=item, model=model)
    if not path.exists() or path.stat().st_size <= 0:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if payload.get("cache_version") != CACHE_VERSION:
        return None
    assignment = payload.get("assignment")
    if not isinstance(assignment, dict):
        return None
    if str(assignment.get("structure_unit_id") or "") != str(item.get("structure_unit_id") or ""):
        return None
    return assignment


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
    }
    digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]
    safe_unit_id = re.sub(r"[^0-9A-Za-z._-]+", "_", str(item.get("structure_unit_id") or "unit"))[:70]
    return checkpoint_dir / f"{safe_unit_id}_{digest}.json"


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
    if _parsed_declares_non_topic(parsed) or _non_topic_protocol_reason(headline=topic_text, raw_text=text, structural_role=str(item.get("structural_role") or ""), packet_role=str((item.get("document_context") or {}).get("packet_role") or "")):
        return _non_topic_assignment(item, reason="model_or_shape_non_topic")
    parsed_subject = clean_protocol_subject_text(parsed.get("clean_subject_he") or parsed.get("topic_subject_he"))
    if _is_protocol_item(item) and parsed_subject:
        topic_text = parsed_subject
    raw_dicta_root_topic_id = str(parsed.get("root_topic_id") or "").strip()
    dicta_root_topic_id = raw_dicta_root_topic_id if raw_dicta_root_topic_id in ROOT_BY_ID else None
    if raw_dicta_root_topic_id and dicta_root_topic_id is None:
        dicta_root_topic_id = _align_unknown_dicta_root(item=item, parsed=parsed, subject=topic_text or text)
        if dicta_root_topic_id is None:
            return _unknown_root_candidate_assignment(item=item, parsed=parsed, raw_dicta_root_topic_id=raw_dicta_root_topic_id, subject=topic_text or text)
    fallback_root_topic_id = infer_root_topic_id(topic_text or text)
    if _is_protocol_item(item) and bool(item.get("is_topic_bearing")) and dicta_root_topic_id in {"root_agenda_queries", "root_order_proposals"}:
        recovered_root = _recover_root_only_topic(item=item, parsed=parsed, subject=topic_text or text, fallback_root_topic_id=fallback_root_topic_id)
        if recovered_root:
            dicta_root_topic_id = recovered_root
    root_adjudication = _adjudicate_assignment_root(item=item, parsed=parsed, subject=topic_text or text, dicta_root_topic_id=dicta_root_topic_id, fallback_root_topic_id=fallback_root_topic_id)
    if dicta_root_topic_id and dicta_root_topic_id not in {raw_dicta_root_topic_id, None}:
        root_adjudication = {**root_adjudication, "decision": f"{root_adjudication.get('decision') or 'root_adjudicated'}:procedural_root_recovered"}
    if raw_dicta_root_topic_id and raw_dicta_root_topic_id not in ROOT_BY_ID:
        root_adjudication = {**root_adjudication, "unknown_dicta_root_topic_id": raw_dicta_root_topic_id, "decision": f"{root_adjudication.get('decision') or 'root_adjudicated'}:unknown_dicta_root_aligned" if dicta_root_topic_id else f"{root_adjudication.get('decision') or 'root_adjudicated'}:unknown_dicta_root_unresolved"}
    root_topic_id = str(root_adjudication.get("root_topic_id") or "")
    if root_topic_id not in ROOT_BY_ID:
        root_topic_id = fallback_root_topic_id if fallback_root_topic_id in ROOT_BY_ID else "root_agenda_queries"
        root_adjudication = {**root_adjudication, "root_topic_id": root_topic_id, "decision": f"{root_adjudication.get('decision') or 'root_adjudication'}:invalid_root_fallback"}
    root_label = root_label_for_id(root_topic_id) or ""
    quote = _grounded_quote(item=item, parsed_quote=_compact(parsed.get("topic_supporting_quote_he")), fallback_text=text)
    candidate_by_id = {str(row.get("candidate_child_id") or ""): row for row in item.get("candidate_child_topics") or []}
    selected_candidate = candidate_by_id.get(str(parsed.get("child_choice_id") or ""))
    selected_candidate_matched_by_label = False
    raw_child = clean_topic_label(_strip_attribution_tail((selected_candidate or {}).get("label_he") or parsed.get("child_label_he")))
    if selected_candidate is None and raw_child:
        selected_candidate = _matching_candidate_by_label(item=item, root_topic_id=root_topic_id, label=raw_child)
        selected_candidate_matched_by_label = selected_candidate is not None
    if selected_candidate and str(selected_candidate.get("root_topic_id") or "") in ROOT_BY_ID:
        candidate_root_topic_id = str(selected_candidate.get("root_topic_id"))
        if root_adjudication.get("policy_id") and candidate_root_topic_id != root_topic_id:
            selected_candidate = None
            raw_child = None
        else:
            root_topic_id = candidate_root_topic_id
            root_label = root_label_for_id(root_topic_id) or root_label
            root_adjudication = {**root_adjudication, "root_topic_id": root_topic_id, "decision": f"{root_adjudication.get('decision') or 'root_adjudication'}:child_candidate_root"}
    elif _is_protocol_item(item):
        raw_child = None
    validation_evidence = _validation_evidence_text(item=item, quote=quote, selected_candidate=selected_candidate)
    validation = validate_child_label(raw_label=raw_child, root_label_he=root_label, evidence_text=validation_evidence, selected_existing=bool(selected_candidate), structural_role=str(item.get("structural_role") or "")) if raw_child else None
    if validation is None:
        selected_candidate = _best_high_confidence_candidate(item=item, root_topic_id=root_topic_id)
        raw_child = clean_topic_label((selected_candidate or {}).get("label_he"))
        validation_evidence = _validation_evidence_text(item=item, quote=quote, selected_candidate=selected_candidate)
        validation = validate_child_label(raw_label=raw_child, root_label_he=root_label, evidence_text=validation_evidence, selected_existing=bool(selected_candidate), structural_role=str(item.get("structural_role") or "")) if raw_child else None
    resolved = resolve_child_topic_assignment(root_topic_id=root_topic_id, root_label_he=root_label, child_label_he=validation.cleaned_label if validation and validation.status == "active" else raw_child, evidence_text=validation_evidence, structural_role=str(item.get("structural_role") or ""))
    root_topic_id = str(resolved["root_topic_id"])
    root_label = str(resolved["root_label_he"])
    if root_topic_id != str(root_adjudication.get("root_topic_id") or ""):
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
    if _assignment_requires_subject_review(item=item, root_topic_id=root_topic_id, child_label=child_label):
        return _review_assignment(item=item, root_topic_id=root_topic_id, root_label=root_label, reason="topic_subject_collapsed_to_procedural_root")
    verified_root = _verified_body_root_override(item=item, root_topic_id=root_topic_id, root_adjudication=root_adjudication, child_label=child_label)
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
    if _non_topic_protocol_reason(headline=subject, raw_text=str(item.get("raw_text") or ""), structural_role=str(item.get("structural_role") or ""), packet_role=str((item.get("document_context") or {}).get("packet_role") or "")):
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
    root_topic_id = infer_root_topic_id(alignment_text)
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


def _fallback_assignment(item: dict[str, Any], *, reason: str) -> dict[str, Any]:
    text = str(item.get("raw_text") or "")
    topic_text = _topic_basis_text(item)
    if _non_topic_protocol_reason(headline=topic_text, raw_text=text, structural_role=str(item.get("structural_role") or ""), packet_role=str((item.get("document_context") or {}).get("packet_role") or "")):
        return _non_topic_assignment(item, reason=f"fallback_{reason}_non_topic")
    fallback_root_topic_id = infer_root_topic_id(topic_text or text)
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
    resolved = resolve_child_topic_assignment(root_topic_id=root_topic_id, root_label_he=root_label, child_label_he=validation.cleaned_label if validation and validation.status == "active" else candidate, evidence_text=validation_evidence, structural_role=str(item.get("structural_role") or ""))
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


def _fallback_requires_review(*, item: dict[str, Any], root_topic_id: str, reason: str) -> bool:
    if not _is_protocol_item(item) or reason not in {"model_omitted_unit", "model_error", "missing_after_retry"}:
        return False
    if not bool(item.get("is_topic_bearing")):
        return False
    # Clear deterministic non-procedural roots can remain conservative root-only fallbacks.
    # Topic-bearing rows that collapse to procedural/root-only need human/model review.
    return root_topic_id == "root_agenda_queries" and not item.get("candidate_child_topics")


def _preassign_without_model(item: dict[str, Any]) -> bool:
    return _skip_model_for_row_type(row_type=str(item.get("row_type") or ""), packet_role=str((item.get("document_context") or {}).get("packet_role") or ""))


def _preassign_with_candidate_finder(item: dict[str, Any]) -> bool:
    decision = item.get("deterministic_topic_decision") if isinstance(item.get("deterministic_topic_decision"), dict) else {}
    return (
        str(decision.get("action") or "") == "choose_existing_topic"
        and not bool(decision.get("needs_dicta"))
        and str(decision.get("root_topic_id") or "") in ROOT_BY_ID
    )


def _assignment_from_candidate_finder(item: dict[str, Any]) -> dict[str, Any]:
    decision = item.get("deterministic_topic_decision") if isinstance(item.get("deterministic_topic_decision"), dict) else {}
    text = str(item.get("raw_text") or "")
    topic_text = _topic_basis_text(item)
    root_topic_id = str(decision.get("root_topic_id") or infer_root_topic_id(topic_text or text))
    if root_topic_id not in ROOT_BY_ID:
        root_topic_id = infer_root_topic_id(topic_text or text)
    root_label = root_label_for_id(root_topic_id) or ""
    candidate_by_id = {str(row.get("candidate_child_id") or ""): row for row in item.get("candidate_child_topics") or []}
    selected_candidate = candidate_by_id.get(str(decision.get("child_choice_id") or ""))
    raw_child = clean_topic_label((selected_candidate or {}).get("label_he") or decision.get("child_label_he"))
    if selected_candidate is None and raw_child:
        selected_candidate = _matching_candidate_by_label(item=item, root_topic_id=root_topic_id, label=raw_child)
    if selected_candidate and str(selected_candidate.get("root_topic_id") or "") in ROOT_BY_ID:
        root_topic_id = str(selected_candidate.get("root_topic_id"))
        root_label = root_label_for_id(root_topic_id) or root_label
    quote = _grounded_quote(item=item, parsed_quote=str((selected_candidate or {}).get("evidence_quote_he") or ""), fallback_text=text)
    validation_evidence = _validation_evidence_text(item=item, quote=quote, selected_candidate=selected_candidate)
    validation = validate_child_label(raw_label=raw_child, root_label_he=root_label, evidence_text=validation_evidence, selected_existing=bool(selected_candidate), structural_role=str(item.get("structural_role") or "")) if raw_child else None
    resolved = resolve_child_topic_assignment(root_topic_id=root_topic_id, root_label_he=root_label, child_label_he=validation.cleaned_label if validation and validation.status == "active" else raw_child, evidence_text=validation_evidence, structural_role=str(item.get("structural_role") or ""))
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


def _candidate_review_assignment(*, item: dict[str, Any], reason: str) -> dict[str, Any]:
    decision = item.get("deterministic_topic_decision") if isinstance(item.get("deterministic_topic_decision"), dict) else {}
    text = str(item.get("raw_text") or "")
    topic_text = _topic_basis_text(item)
    root_topic_id = str(decision.get("root_topic_id") or infer_root_topic_id(topic_text or text))
    if root_topic_id not in ROOT_BY_ID:
        root_topic_id = infer_root_topic_id(topic_text or text)
    evidence_root = _strong_body_evidence_root(item)
    if evidence_root:
        root_topic_id = evidence_root
    root_label = root_label_for_id(root_topic_id) or ""
    return _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=None, raw_child_label=None, status="candidate", reject_reason=f"non_blocking_topic_review:{reason}:{decision.get('reason') or 'no_decision'}", aliases=[], confidence=min(0.71, max(0.25, _confidence(decision.get("confidence")))), quote=text[:500], route=f"deterministic_v4_candidate_review:{reason}:{decision.get('reason') or 'no_decision'}", rationale_he="ambiguous topic imported as candidate so protocol ingestion can continue", root_adjudication=_adjudicate_assignment_root(item=item, parsed={}, subject=topic_text or text, dicta_root_topic_id=None, fallback_root_topic_id=root_topic_id))


def _skip_model_for_row_type(*, row_type: str, packet_role: str) -> bool:
    if packet_role != "protocol":
        return False
    return row_type in {"metadata", "container", "vote_or_result", "fragment", "attribution_fragment"}


def _non_topic_assignment(item: dict[str, Any], *, reason: str | None = None) -> dict[str, Any]:
    topic_text = _topic_basis_text(item)
    row_type = str(item.get("row_type") or "fragment")
    root_topic_id = infer_root_topic_id(topic_text) if topic_text and row_type == "vote_or_result" else "root_agenda_queries"
    root_label = root_label_for_id(root_topic_id) or root_label_for_id("root_agenda_queries") or ""
    selected_candidate = _best_high_confidence_candidate(item=item, root_topic_id=root_topic_id) if row_type == "vote_or_result" else None
    child_label = clean_topic_label((selected_candidate or {}).get("label_he")) if selected_candidate else None
    route = f"deterministic_v4_row_type:{row_type}"
    if reason:
        route = f"{route}:{reason}"
    if selected_candidate and child_label:
        route = f"{route}:inherited_candidate"
    return _assignment_payload(
        item=item,
        root_topic_id=root_topic_id,
        root_label=root_label,
        child_label=child_label,
        raw_child_label=child_label,
        status="active",
        reject_reason=None,
        aliases=list((selected_candidate or {}).get("aliases_he") or []),
        confidence=0.5 if row_type == "vote_or_result" else 0.35,
        quote=str(item.get("raw_text") or "")[:500],
        route=route,
        rationale_he="row type is not a standalone topic-bearing agenda subject",
        parsed_contract={"is_topic_bearing": False, "topic_subject_he": None, "clean_subject_he": None},
    )


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


def _strong_body_evidence_root(item: dict[str, Any]) -> str | None:
    text = _join_unique([item.get("topic_subject_he"), item.get("topic_headline_he"), item.get("raw_text")])
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


def _assignment_payload(*, item: dict[str, Any], root_topic_id: str, root_label: str, child_label: str | None, raw_child_label: str | None, status: str, reject_reason: str | None, aliases: list[str], confidence: float, quote: str, route: str, rationale_he: str, parsed_contract: dict[str, Any] | None = None, root_adjudication: dict[str, Any] | None = None) -> dict[str, Any]:
    child_id = child_topic_id(root_topic_id, child_label) if child_label else None
    parsed_contract = parsed_contract or {}
    root_adjudication = root_adjudication or {}
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
        "row_type": item.get("row_type"),
        "skip_model_assignment": bool(item.get("skip_model_assignment")),
        "packet_role": str((item.get("document_context") or {}).get("packet_role") or ""),
        "topic_identification_context": item.get("topic_identification_context"),
        "topic_headline_he": item.get("topic_headline_he"),
        "agenda_carrier_he": _contract_value(parsed_contract, item, "agenda_carrier_he"),
        "topic_subject_he": _contract_value(parsed_contract, item, "topic_subject_he"),
        "attribution_he": _contract_value(parsed_contract, item, "attribution_he"),
        "is_topic_bearing": bool(_contract_value(parsed_contract, item, "is_topic_bearing")),
        "agenda_item_title_he": item.get("agenda_item_title_he"),
        "parent_agenda_unit_id": item.get("parent_agenda_unit_id"),
        "topic_context_source": item.get("topic_context_source"),
        "topic_headline_source": item.get("topic_headline_source"),
        "topic_provenance_reject_reason": item.get("topic_provenance_reject_reason"),
        "topic_tree_version": TOPIC_TREE_VERSION,
        "topic_assignment_backend": TOPIC_ASSIGNMENT_BACKEND,
        "root_topic_id": root_topic_id,
        "root_label_he": root_label,
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
        "root_topic_candidates": item.get("root_topic_candidates") or [],
        "deterministic_topic_decision": item.get("deterministic_topic_decision") or {},
        "deterministic_classifier_method": item.get("deterministic_classifier_method"),
        "topic_review_status": "needs_review" if status == "candidate" and reject_reason else None,
        "model_clean_subject_he": parsed_contract.get("clean_subject_he"),
        "model_primary_action_he": parsed_contract.get("primary_action_he"),
        "model_service_domain_he": parsed_contract.get("service_domain_he"),
        "model_why_not_other_roots_he": parsed_contract.get("why_not_other_roots_he"),
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
            current["is_topic_bearing"] = False
            current["skip_model_assignment"] = True
            current["inherited_topic_from_unit_id"] = parent.get("structure_unit_id")
            current["topic_assignment_route"] = f"{current.get('topic_assignment_route') or 'unknown'}:inherited_duplicate_topic"
            current["topic_reject_reason"] = current.get("topic_reject_reason") or "inherited_duplicate_topic_subject"
        elif key:
            seen[key] = current
        if section_root_key and str(current.get("row_type") or "") == "topic_item":
            seen_section_root.setdefault(section_root_key, current)
        out.append(current)
    return out


def _normalize_protocol_non_topic_assignments(assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    procedural_non_topic_subjects: set[str] = set()
    for row in assignments:
        current = dict(row)
        if str(current.get("packet_role") or "") != "protocol":
            out.append(current)
            continue
        reason = _post_assignment_non_topic_reason(current)
        if reason:
            current = _convert_assignment_to_non_topic_fragment(current, reason=reason)
            key = _duplicate_subject_norm(current.get("topic_headline_he") or current.get("topic_identification_context") or "")
            if key:
                procedural_non_topic_subjects.add(key)
        out.append(current)
    final: list[dict[str, Any]] = []
    for row in out:
        current = dict(row)
        key = _duplicate_subject_norm(current.get("topic_headline_he") or current.get("topic_identification_context") or current.get("topic_subject_he") or "")
        if key and key in procedural_non_topic_subjects and str(current.get("topic_node_status") or "") == "candidate":
            current = _convert_assignment_to_non_topic_fragment(current, reason="duplicate_procedural_non_topic")
        final.append(current)
    return final


def _post_assignment_non_topic_reason(row: dict[str, Any]) -> str | None:
    if row.get("topic_provenance_reject_reason"):
        return str(row.get("topic_provenance_reject_reason"))
    text = _join_unique([row.get("topic_subject_he"), row.get("topic_headline_he"), row.get("topic_identification_context")])
    role = str(row.get("structural_role") or "")
    reason = _non_topic_protocol_reason(headline=text, raw_text=text, structural_role=role, packet_role=str(row.get("packet_role") or ""))
    if reason:
        return reason
    if str(row.get("topic_node_status") or "") == "candidate" and str(row.get("root_topic_id") or "") == "root_agenda_queries" and role in {"body", "continuation", "task_row"}:
        if not _has_explicit_local_topic_marker(text):
            return "evidence_only_body_fragment"
    return None


def _convert_assignment_to_non_topic_fragment(row: dict[str, Any], *, reason: str) -> dict[str, Any]:
    current = dict(row)
    current["row_type"] = "fragment" if str(current.get("row_type") or "") != "container" else "container"
    current["is_topic_bearing"] = False
    current["skip_model_assignment"] = True
    current["topic_subject_he"] = None
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
    quote_norm = _norm(quote)
    if not quote_norm:
        return False
    evidence_parts = [
        str(item.get("raw_text") or ""),
        str(item.get("topic_identification_context") or ""),
        "\n".join(str(action) for action in item.get("explicit_actions") or []),
        str(item.get("outline_title_he") or ""),
        "\n".join(str(context.get("topic_supporting_quote_he") or "") for context in item.get("referenced_attachment_contexts") or [] if isinstance(context, dict)),
    ]
    evidence_norm = _norm("\n".join(evidence_parts))
    if quote_norm in evidence_norm:
        return True
    quote_tokens = [token for token in _hebrew_tokens(quote_norm) if len(token) >= 3]
    if len(quote_tokens) < 3:
        return False
    hits = [token for token in quote_tokens if token in evidence_norm]
    return len(hits) >= min(len(quote_tokens), max(3, int(len(quote_tokens) * 0.75)))


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
    return {
        "is_topic_bearing": bool(parsed.get("is_topic_bearing")),
        "agenda_carrier_he": _compact(parsed.get("agenda_carrier_he")) or None,
        "topic_subject_he": subject or None,
        "clean_subject_he": subject or None,
        "primary_action_he": _compact(parsed.get("primary_action_he")) or None,
        "service_domain_he": _compact(parsed.get("service_domain_he")) or None,
        "policy_id": _compact(parsed.get("policy_id")) or None,
        "attribution_he": _compact(parsed.get("attribution_he")) or None,
        "why_not_other_roots_he": _compact(parsed.get("why_not_other_roots_he")) or None,
    }


def _topic_contract_from_headline(headline: str, *, structural_role: str, packet_role: str) -> dict[str, Any]:
    headline = _compact(headline)
    if packet_role != "protocol" or not headline:
        return {"is_topic_bearing": bool(headline), "agenda_carrier_he": None, "topic_subject_he": headline or None, "attribution_he": None}
    carrier, subject, attribution = _split_headline_contract(headline)
    subject = _strip_attribution_tail(subject)
    if not subject and structural_role in {"continuation", "vote_or_result", "task_row"}:
        subject = _strip_attribution_tail(_clean_heading(headline))
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
    if _non_topic_protocol_reason(headline=headline, raw_text=raw_text, structural_role=role, packet_role=packet_role):
        return "fragment"
    if _looks_like_attribution_fragment(raw_text):
        return "attribution_fragment"
    if bool(topic_contract.get("is_topic_bearing")):
        return "topic_item"
    return "fragment"


def _non_topic_protocol_reason(*, headline: str, raw_text: str, structural_role: str, packet_role: str) -> str | None:
    if packet_role != "protocol":
        return None
    text = _compact(headline or raw_text)
    raw = _compact(raw_text)
    if not text and not raw:
        return "empty_fragment"
    if _looks_like_container_heading(text):
        return "container_heading"
    if _looks_like_procedural_carrier_heading(text):
        return "procedural_carrier_heading"
    if _looks_like_no_topic_continuation(raw or text):
        return "no_topic_continuation"
    if _looks_like_procedural_dialogue_fragment(raw or text):
        return "procedural_dialogue_fragment"
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
    if topic_context_source in {"parent_agenda", "section_agenda", "previous_agenda"} and role in {"body", "continuation", "task_row"}:
        return "inherited_context_not_standalone_topic"
    if headline_source in {"explicit_header", "explicit_title", "explicit_section_title"}:
        return None
    if role == "body":
        return "body_without_headline_topic_provenance"
    if headline_source in {"raw_titleish", "raw_extracted"}:
        token_count = len(_hebrew_tokens(compact_raw))
        # Raw text can stand in for a heading only when the unit itself is short.
        # Long transcript windows often contain incidental heading-like phrases.
        if role in {"outline_item", "section_heading", "task_row", "continuation"} and token_count <= 28 and len(compact_raw) <= 360:
            return None
        return "transcript_window_without_bounded_headline"
    if headline_source == "none":
        return "missing_topic_headline_provenance"
    if role in {"outline_item", "section_heading", "task_row", "continuation"} and compact_headline:
        return None
    return "missing_topic_headline_provenance"


def _has_bounded_raw_topic_marker(raw_text: str) -> bool:
    compact = _compact(raw_text)
    if not _has_explicit_local_topic_marker(compact):
        return False
    if len(compact) <= 360:
        return True
    return False


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


def _looks_like_container_heading(text: str) -> bool:
    compact = _clean_heading(text)
    if not compact:
        return False
    if _looks_like_protocol_listing(compact):
        return True
    normalized = _norm(compact).strip(" :.-–")
    # These are agenda carriers/section labels, not municipal subjects by themselves.
    container_patterns = [
        r"^(?:סדר\s+)?ישיבת\s+המועצה$",
        r"^(?:ה)?נושאים\s+לדיון(?:\s*[:.\-–]?\s*(?:שאילתות|הצעות\s+לסדר|אישורים|פרוטוקולים))?$",
        r"^סדר\s+יום(?:\s+ושאילתות)?$",
        r"^שאילתות(?:\s+\d+(?:\.\d+)?)?$",
        r"^הצעות\s+לסדר(?:\s+\d+(?:\.\d+)?)?$",
        r"^אישורים$",
        r"^פרוטוקולים?$",
        r"^משימות$",
    ]
    return any(re.search(pattern, normalized) for pattern in container_patterns)


def _looks_like_procedural_carrier_heading(text: str) -> bool:
    compact = _clean_heading(text)
    if not compact:
        return False
    normalized = _norm(compact).strip(" :.-–")
    return bool(re.match(r"^פרוטוקול(?:י)?\s+ועדת\b", normalized))


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
    if re.search(r"\b(?:עו\"ד|מר|גב'|גברת|הרב|ד\"ר)\b", compact) and not any(term in compact for term in ["בנושא", "הסכם", "מינוי", "תמיכות", "הקצאה"]):
        return True
    return bool(re.fullmatch(r"[\d\s./()\-–:]+", compact))


def _looks_like_vote_fragment(text: str) -> bool:
    compact = _compact(text)
    if not compact:
        return False
    vote_terms = ["הצביעו נגד", "הצביעו בעד", "לא השתתפו בהצבעה", "נמנע", "נמנעו", "בעד:", "נגד:"]
    if not any(term in compact for term in vote_terms):
        return False
    substantive_terms = ["בנושא", "הסכם", "הקצאה", "מינוי", "תמיכות", "תקציב", "היטל", "שירותי שמירה"]
    return not any(term in compact for term in substantive_terms)


def _split_headline_contract(headline: str) -> tuple[str | None, str, str | None]:
    attribution = _extract_attribution(headline)
    text = _clean_heading(headline)
    text = re.sub(r"\bבנו\s+[\"'׳״]?שא[\"'׳״]?\b", "בנושא", text)
    if attribution:
        text = _strip_attribution_tail(text)
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


def _clean_topic_subject(value: str) -> str:
    return clean_protocol_subject_text(value)


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
    tokens = [token for token in _hebrew_tokens(text) if len(token) >= 3]
    if len(tokens) < 2:
        return False
    if len(text) <= 3 or re.fullmatch(r"[\d\W_]+", text):
        return False
    # Single-person/attribution fragments should not become topics.
    if len(tokens) <= 2 and not any(term in text for term in ["חינוך", "תמיכות", "רווחה", "שמירה", "הסכם", "מינוי", "עובדים", "הקצאה", "הקצאות"]):
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
    patterns = [
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
        r"(?:^|\s)(?:הנדון|נדון|בנושא|נושא)\s*[:\-–]?\s+(.{6,180}?)(?=[.;\n]|$)",
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
    terms = ("שאילתה בנושא", "הצעה לסדר", "נושא לדיון", "אישור", "מינוי", "הסכם", "פרוטוקול ועד", "ועדה")
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
    return {
        "pdf_file_stem": file_stem,
        "packet_role": packet_role or "unknown",
        "document_title_candidates": _title_candidates_from_filename(file_stem),
        "explicit_actions": all_actions[:12],
    }


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
    rows = _tree_child_candidates(topic_tree=topic_tree, text="\n".join([text, outline_title]), referenced_attachment_contexts=referenced_attachment_contexts)
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
            if str(child.get("status") or "active") == "rejected":
                continue
            label = _compact(child.get("child_label_he") or child.get("label_he"))
            profile = child.get("profile") if isinstance(child.get("profile"), dict) else {}
            score = _existing_child_match_score(label=label, text=compact, support_count=int(child.get("support_count") or child.get("support") or 0), profile=profile)
            if score < 0.46:
                continue
            candidate = _existing_tree_candidate_row(root_topic_id=root_topic_id, root_label=root_label, child=child, label=label, evidence_quote=compact[:500], confidence_hint=score)
            if candidate:
                rows.append(candidate)
    return rows


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
    return bracketed_items >= 2 or (bracketed_items >= 1 and dot_leaders >= 1) or agenda_mentions >= 2 or numbered_items >= 3


def _existing_tree_candidate_row(*, root_topic_id: str, root_label: str, child: dict[str, Any], label: str, evidence_quote: str, confidence_hint: float) -> dict[str, Any] | None:
    cleaned = clean_topic_label(label)
    if not cleaned or root_topic_id not in ROOT_BY_ID:
        return None
    validation = validate_child_label(raw_label=cleaned, root_label_he=root_label, evidence_text=evidence_quote, selected_existing=True)
    if validation.status != "active" or not validation.cleaned_label:
        return None
    return {
        "candidate_child_id": str(child.get("child_topic_id") or child.get("topic_id") or ""),
        "label_he": validation.cleaned_label,
        "root_topic_id": root_topic_id,
        "root_label_he": root_label,
        "evidence_quote_he": _compact(evidence_quote)[:500],
        "evidence_source": "existing_tree",
        "confidence_hint": max(0.0, min(1.0, float(confidence_hint))),
        "aliases_he": [label] if label and _norm(label) != _norm(validation.cleaned_label) else [],
        "profile": _compact_profile(child.get("profile")),
    }


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


def _existing_child_match_score(*, label: str, text: str, support_count: int, profile: dict[str, Any]) -> float:
    label_norm = _norm(label)
    text_norm = _norm(text)
    if not label_norm or not text_norm:
        return 0.0
    if label_norm in text_norm:
        return 0.96
    alias_scores = [_label_text_match_score(label=str(alias), text_norm=text_norm, support_count=support_count, exact_score=0.94) for alias in profile.get("aliases_he") or []]
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
    hits = {token for token in label_tokens if token in text_norm}
    return len(hits) >= min(len(label_tokens), max(2, len(label_tokens) - 1))


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


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


if __name__ == "__main__":
    raise SystemExit(main())
