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
    attachment_contexts_from_retrieval_chunks,
    child_topic_id,
    clean_topic_label,
    derive_child_candidate,
    global_topic_tree_payload,
    infer_root_topic_id,
    referenced_attachment_contexts,
    root_label_for_id,
    validate_child_label,
)

DEFAULT_MODEL = "dicta-il/DictaLM-3.0-24B-Thinking:bf16"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
EXCLUDED_STRUCTURAL_ROLES = {"noise", "table_header_only"}


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
    args = parser.parse_args()

    structure_path = Path(args.structure_units_json).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    structure_payload = json.loads(structure_path.read_text(encoding="utf-8"))
    entity_payload = _load_json(Path(args.entity_facts_json).expanduser().resolve()) if args.entity_facts_json else {}
    existing_tree = _load_json(Path(args.existing_tree_json).expanduser().resolve()) if args.existing_tree_json else None
    attachment_contexts = _load_attachment_contexts(args.attachment_context_json)

    units = [unit for unit in structure_payload.get("structure_units") or [] if str(unit.get("structural_role") or "") not in EXCLUDED_STRUCTURAL_ROLES]
    facts_by_unit = _facts_by_unit(entity_payload.get("entity_facts") or [])
    document_context = _document_context(input_pdf=args.input_pdf, packet_role=str(args.packet_role), units=units)
    document_child_candidates = _document_child_candidates(units=units, document_context=document_context)
    tree_payload = global_topic_tree_payload(existing_tree=existing_tree, attachment_contexts=[])
    items = [_build_item(unit=unit, facts=facts_by_unit.get(str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or ""), []), max_raw_chars=args.max_raw_chars, attachment_contexts=attachment_contexts, document_context=document_context, document_child_candidates=document_child_candidates) for unit in units]

    assignments: list[dict[str, Any]] = []
    model_errors: list[dict[str, Any]] = []
    started_all = time.perf_counter()
    for batch in _chunked(items, max(1, int(args.max_units_per_call))):
        started = time.perf_counter()
        response = _call_dictalm(items=batch, topic_tree=tree_payload, model=str(args.model), base_url=str(args.ollama_base_url).rstrip("/"), timeout_seconds=max(1.0, float(args.timeout_seconds)))
        elapsed = round(time.perf_counter() - started, 3)
        if response.get("error_code"):
            model_errors.append({"structure_unit_ids": [item["structure_unit_id"] for item in batch], **response})
            assignments.extend([_fallback_assignment(item, reason="model_error") for item in batch])
            print(json.dumps({"batch": [item["structure_unit_id"] for item in batch], "status": "fallback", "elapsed_seconds": elapsed}, ensure_ascii=False), flush=True)
            continue
        assignments.extend(_assignments_from_response(response=response, items_by_id={item["structure_unit_id"]: item for item in batch}))
        print(json.dumps({"batch": [item["structure_unit_id"] for item in batch], "status": "ok", "elapsed_seconds": elapsed}, ensure_ascii=False), flush=True)

    validation = _validate(assignments=assignments, item_count=len(items), model_errors=model_errors)
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
        "items": items,
        "topic_assignments": assignments,
        "elapsed_seconds": round(time.perf_counter() - started_all, 3),
    }
    assignments_path = output_dir / "topic_assignments.json"
    validation_path = output_dir / "validation_report.json"
    assignments_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"topic_assignments": str(assignments_path), "validation_report": str(validation_path), **validation}, ensure_ascii=False))
    return 0 if validation["accept_for_next_step"] else 2


def _build_item(*, unit: dict[str, Any], facts: list[dict[str, Any]], max_raw_chars: int, attachment_contexts: list[dict[str, Any]], document_context: dict[str, Any], document_child_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    unit_id = str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or "")
    raw_text = _compact(unit.get("raw_text"))
    summary = _compact(unit.get("summary_he"))
    explicit_actions = [str(value) for value in unit.get("explicit_actions") or [] if str(value).strip()]
    text = "\n".join(value for value in [raw_text, summary, "\n".join(explicit_actions)] if value)
    referenced_contexts = referenced_attachment_contexts(text=text, attachment_contexts=attachment_contexts)
    candidate_child_topics = _candidate_child_topics(
        unit_id=unit_id,
        text=text,
        outline_title=str(unit.get("header_text") or unit.get("title_he") or unit.get("section_title_he") or ""),
        explicit_actions=explicit_actions,
        document_context=document_context,
        document_child_candidates=document_child_candidates,
        referenced_attachment_contexts=referenced_contexts,
    )
    return {
        "structure_unit_id": unit_id,
        "semantic_unit_id": str(unit.get("semantic_unit_id") or unit_id),
        "source_window_id": unit.get("source_window_id"),
        "source_region_ids": unit.get("source_region_ids") or [],
        "source_block_ids": unit.get("source_block_ids") or [],
        "source_page": _positive_int(unit.get("page")),
        "structural_role": str(unit.get("structural_role") or ""),
        "section_id": unit.get("section_id"),
        "section_number": unit.get("section_number"),
        "outline_title_he": unit.get("header_text") or unit.get("title_he") or unit.get("section_title_he"),
        "raw_text": text[: max(250, int(max_raw_chars))],
        "explicit_actions": explicit_actions[:8],
        "accepted_entity_spans": [str(fact.get("canonical_raw_span") or "") for fact in facts[:16] if str(fact.get("canonical_raw_span") or "").strip()],
        "document_context": document_context,
        "candidate_child_topics": candidate_child_topics,
        "referenced_attachment_contexts": referenced_contexts,
    }


def _call_dictalm(*, items: list[dict[str, Any]], topic_tree: dict[str, Any], model: str, base_url: str, timeout_seconds: float) -> dict[str, Any]:
    request_payload = {
        "task": "pdf_first_v4_global_topic_assignment",
        "requirements": [
            "Return strict JSON only with key assignments.",
            "Return exactly one assignment per structure_unit_id.",
            "Choose root_topic_id only from topic_tree.root_topics.root_topic_id.",
            "Use existing child topics from the supplied tree when they match evidence.",
            "Choose child_choice_id from item.candidate_child_topics when a candidate is evidence-backed and more specific than the root.",
            "Set child_choice_id to null only when no candidate_child_topics row is supported by the evidence.",
            "Do not invent child labels. If no candidate is acceptable, set child_choice_id null and child_label_he null.",
            "Never use נושא כללי.",
            "Do not invent entities, dates, geometry, or municipality-specific schema rules.",
            "topic_supporting_quote_he must be copied from raw_text, explicit_actions, document_context, or attachment context.",
            "Metadata role does not force root-only when explicit_actions or document_context contain a concrete municipal subject.",
            "Prefer a concrete subject such as a seminar, training, agreement, allocation, committee, question, proposal, project, or named municipal activity over root-only.",
        ],
        "schema": {
            "assignments": [
                {
                    "structure_unit_id": "string",
                    "root_topic_id": "string",
                    "child_choice_id": "string|null",
                    "child_label_he": "string|null",
                    "topic_supporting_quote_he": "string",
                    "confidence": 0.0,
                    "rationale_he": "string",
                }
            ]
        },
        "topic_tree": topic_tree,
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
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=10.0, read=timeout_seconds, write=30.0, pool=10.0)) as client:
            response = client.post(f"{base_url}/api/chat", json=body)
            response.raise_for_status()
            raw_payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return {"error_code": "MODEL_REQUEST_FAILED", "error_text": f"{exc.__class__.__name__}:{exc}", "raw_payload": None}
    parsed = _parse_json_content(str(((raw_payload.get("message") or {}).get("content")) or ""))
    if not isinstance(parsed, dict):
        return {"error_code": "MODEL_INVALID_JSON", "error_text": str(raw_payload)[:500], "raw_payload": raw_payload}
    parsed["raw_payload"] = raw_payload
    return parsed


def _assignments_from_response(*, response: dict[str, Any], items_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    raw_rows = response.get("assignments") if isinstance(response.get("assignments"), list) else []
    parsed_by_id = {str(row.get("structure_unit_id") or ""): row for row in raw_rows if isinstance(row, dict) and str(row.get("structure_unit_id") or "")}
    return [_assignment_from_parsed(item=item, parsed=parsed_by_id.get(unit_id)) for unit_id, item in items_by_id.items()]


def _assignment_from_parsed(*, item: dict[str, Any], parsed: dict[str, Any] | None) -> dict[str, Any]:
    if not parsed:
        return _fallback_assignment(item, reason="model_omitted_unit")
    text = str(item.get("raw_text") or "")
    root_topic_id = str(parsed.get("root_topic_id") or "")
    if root_topic_id not in ROOT_BY_ID:
        root_topic_id = infer_root_topic_id(text)
    root_label = root_label_for_id(root_topic_id) or ""
    quote = _compact(parsed.get("topic_supporting_quote_he")) or text[:500]
    candidate_by_id = {str(row.get("candidate_child_id") or ""): row for row in item.get("candidate_child_topics") or []}
    selected_candidate = candidate_by_id.get(str(parsed.get("child_choice_id") or ""))
    raw_child = clean_topic_label((selected_candidate or {}).get("label_he") or parsed.get("child_label_he"))
    validation = validate_child_label(raw_label=raw_child, root_label_he=root_label, evidence_text="\n".join([text, quote, str((selected_candidate or {}).get("evidence_quote_he") or "")]), selected_existing=bool(selected_candidate), structural_role=str(item.get("structural_role") or "")) if raw_child else None
    if validation is None:
        selected_candidate = _best_high_confidence_candidate(item=item, root_topic_id=root_topic_id)
        raw_child = clean_topic_label((selected_candidate or {}).get("label_he"))
        validation = validate_child_label(raw_label=raw_child, root_label_he=root_label, evidence_text="\n".join([text, quote, str((selected_candidate or {}).get("evidence_quote_he") or "")]), selected_existing=bool(selected_candidate), structural_role=str(item.get("structural_role") or "")) if raw_child else None
    child_label = validation.cleaned_label if validation and validation.status == "active" else None
    status = validation.status if validation else "active"
    route = f"dictalm_v4_global_tree:{validation.route if validation else 'root_only'}"
    if selected_candidate and child_label:
        route = f"dictalm_v4_global_tree:child_choice:{selected_candidate.get('evidence_source') or validation.route}"
    if validation and validation.status == "rejected":
        route = f"dictalm_v4_global_tree:rejected:{validation.reason}"
    return _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=child_label, raw_child_label=raw_child, status=status, reject_reason=validation.reason if validation else None, aliases=validation.aliases_he if validation else [], confidence=_confidence(parsed.get("confidence")), quote=quote, route=route, rationale_he=_compact(parsed.get("rationale_he"))[:180])


def _fallback_assignment(item: dict[str, Any], *, reason: str) -> dict[str, Any]:
    text = str(item.get("raw_text") or "")
    root_topic_id = infer_root_topic_id(text)
    root_label = root_label_for_id(root_topic_id) or ""
    best_candidate = _best_high_confidence_candidate(item=item, root_topic_id=root_topic_id)
    candidate = str((best_candidate or {}).get("label_he") or "") or None
    candidate_route = str((best_candidate or {}).get("evidence_source") or "") or "root_only"
    if not candidate:
        candidate, candidate_route = derive_child_candidate(text=text, outline_title_he=str(item.get("outline_title_he") or ""))
    validation = validate_child_label(raw_label=candidate, root_label_he=root_label, evidence_text=text, structural_role=str(item.get("structural_role") or "")) if candidate else None
    child_label = validation.cleaned_label if validation and validation.status == "active" else None
    status = validation.status if validation else "active"
    return _assignment_payload(item=item, root_topic_id=root_topic_id, root_label=root_label, child_label=child_label, raw_child_label=candidate, status=status, reject_reason=validation.reason if validation else None, aliases=validation.aliases_he if validation else [], confidence=0.55 if child_label else 0.45, quote=text[:500], route=f"deterministic_v4_fallback:{reason}:{candidate_route}", rationale_he=reason)


def _assignment_payload(*, item: dict[str, Any], root_topic_id: str, root_label: str, child_label: str | None, raw_child_label: str | None, status: str, reject_reason: str | None, aliases: list[str], confidence: float, quote: str, route: str, rationale_he: str) -> dict[str, Any]:
    child_id = child_topic_id(root_topic_id, child_label) if child_label else None
    return {
        "structure_unit_id": item["structure_unit_id"],
        "semantic_unit_id": item["semantic_unit_id"],
        "source_window_id": item.get("source_window_id"),
        "source_region_ids": item.get("source_region_ids") or [],
        "source_block_ids": item.get("source_block_ids") or [],
        "source_page": item.get("source_page"),
        "structural_role": item.get("structural_role"),
        "topic_tree_version": TOPIC_TREE_VERSION,
        "topic_assignment_backend": TOPIC_ASSIGNMENT_BACKEND,
        "root_topic_id": root_topic_id,
        "root_label_he": root_label,
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


def _validate(*, assignments: list[dict[str, Any]], item_count: int, model_errors: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(row.get("topic_node_status") or "") for row in assignments)
    missing_roots = [row.get("structure_unit_id") for row in assignments if str(row.get("root_topic_id") or "") not in ROOT_BY_ID]
    return {
        "unit_count": item_count,
        "assignment_count": len(assignments),
        "status_counts": dict(status_counts),
        "missing_or_invalid_root_count": len(missing_roots),
        "model_error_count": len(model_errors),
        "accept_for_next_step": item_count > 0 and len(assignments) == item_count and not missing_roots,
    }


def _document_context(*, input_pdf: str | None, packet_role: str, units: list[dict[str, Any]]) -> dict[str, Any]:
    path = Path(input_pdf).expanduser() if input_pdf else None
    file_stem = path.stem if path else ""
    all_actions: list[str] = []
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


def _document_child_candidates(*, units: list[dict[str, Any]], document_context: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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
    outline_title: str,
    explicit_actions: list[str],
    document_context: dict[str, Any],
    document_child_candidates: list[dict[str, Any]],
    referenced_attachment_contexts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = list(document_child_candidates)
    for label, source, quote in _raw_candidate_labels(text="\n".join([text, outline_title]), explicit_actions=explicit_actions, document_context=document_context):
        candidate = _candidate_row(label=label, evidence_source=source, evidence_quote=quote, support_text="\n".join([text, outline_title]), confidence_hint=0.86 if source in {"heading", "explicit_action"} else 0.72)
        if candidate:
            rows.append(candidate)
    for context in referenced_attachment_contexts:
        label = _compact(context.get("child_label_he"))
        quote = _compact(context.get("topic_supporting_quote_he"))
        candidate = _candidate_row(label=label, evidence_source="referenced_attachment", evidence_quote=quote or label, support_text="\n".join([text, quote, label]), confidence_hint=0.72)
        if candidate:
            rows.append(candidate)
    out = _dedupe_candidates(rows)[:12]
    for index, row in enumerate(out, start=1):
        row["candidate_child_id"] = f"{unit_id}_child_{index}_{_short_hash(row['root_topic_id'], row['label_he'])[:8]}"
    return out


def _raw_candidate_labels(*, text: str, explicit_actions: list[str], document_context: dict[str, Any]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    compact = _compact(text)
    patterns = [
        (r"(?:^|\s)(?:הנדון|נדון)\s*[:\-–]?\s+([^.;\n]{4,150})", "heading"),
        (r"(?:^|\s)(?:בנושא|נושא)\s*[:\-–]?\s+([^.;\n]{4,150})", "heading"),
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
    cleaned = clean_topic_label(label)
    if not cleaned:
        return None
    root_topic_id = infer_root_topic_id("\n".join([cleaned, evidence_quote, support_text]))
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
    }


def _best_high_confidence_candidate(*, item: dict[str, Any], root_topic_id: str) -> dict[str, Any] | None:
    candidates = [row for row in item.get("candidate_child_topics") or [] if str(row.get("root_topic_id") or "") == root_topic_id]
    if not candidates:
        return None
    candidates.sort(key=lambda row: (float(row.get("confidence_hint") or 0.0), len(str(row.get("label_he") or ""))), reverse=True)
    best = candidates[0]
    return best if float(best.get("confidence_hint") or 0.0) >= 0.72 else None


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
    return " ".join(str(value or "").split())


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
