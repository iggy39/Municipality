#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[5]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from municipality.pdf_first_v4_topic_tree import (  # noqa: E402
    BACKEND_VERSION,
    TOPIC_ASSIGNMENT_BACKEND,
    TOPIC_TREE_VERSION,
    decision_contract_fields,
    evidence_payload,
    evidence_reference,
    missing_contract_counts,
    noisy_label_reason,
    topic_node_contract,
)

EXCLUDED_STRUCTURAL_ROLES = {"noise", "table_header_only"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 5 v4: build UI-contract-compatible retrieval chunks")
    parser.add_argument("--structure-units-json", required=True)
    parser.add_argument("--canonical-topics-json", required=True)
    parser.add_argument("--entity-facts-json", help="Optional Step 3.5 entity_facts.json")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--input-pdf", help="Original PDF path for evidence source title/url metadata")
    parser.add_argument("--retrieval-set-id", help="Stable retrieval set id; defaults to hash of inputs")
    parser.add_argument("--document-version-id", type=int, help="Optional document_version.id for evidence contracts")
    args = parser.parse_args()

    structure_path = Path(args.structure_units_json).expanduser().resolve()
    topics_path = Path(args.canonical_topics_json).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = output_dir / "evidence_tables"
    evidence_dir.mkdir(exist_ok=True)

    structure_payload = json.loads(structure_path.read_text(encoding="utf-8"))
    topic_payload = json.loads(topics_path.read_text(encoding="utf-8"))
    entity_payload = json.loads(Path(args.entity_facts_json).expanduser().resolve().read_text(encoding="utf-8")) if args.entity_facts_json else {}
    retrieval_set_id = args.retrieval_set_id or "rset_" + _short_hash(f"{structure_path}|{topics_path}")
    source_title = Path(str(args.input_pdf or structure_payload.get("input_pdf") or "")).name or None

    assignments_by_id = {str(row.get("structure_unit_id") or row.get("semantic_unit_id") or ""): row for row in topic_payload.get("canonical_assignments") or []}
    entities_by_unit = _entities_by_unit(entity_payload.get("entity_facts") or [])
    chunks = []
    skipped = []
    for ordinal, unit in enumerate(structure_payload.get("structure_units") or []):
        unit_id = str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or "")
        raw_text = _compact(unit.get("raw_text"))
        role = str(unit.get("structural_role") or "")
        if not raw_text or role in EXCLUDED_STRUCTURAL_ROLES:
            skipped.append({"structure_unit_id": unit_id, "structural_role": role, "reason": "empty_or_excluded_role"})
            continue
        assignment = assignments_by_id.get(unit_id, {})
        chunks.append(_build_chunk(unit=unit, assignment=assignment, entity_facts=entities_by_unit.get(unit_id, []), ordinal=ordinal, retrieval_set_id=retrieval_set_id, source_title=source_title, source_url=str(args.input_pdf or structure_payload.get("input_pdf") or "") or None, document_version_id=args.document_version_id))

    validation = _validate(chunks=chunks, skipped=skipped, structure_units=structure_payload.get("structure_units") or [], assignments_by_id=assignments_by_id)
    output = {
        "step": "step5_v4_retrieval_chunk_generation",
        "topic_tree_version": TOPIC_TREE_VERSION,
        "topic_assignment_backend": TOPIC_ASSIGNMENT_BACKEND,
        "backend_version": BACKEND_VERSION,
        "input_pdf": str(args.input_pdf or structure_payload.get("input_pdf") or "") or None,
        "document_version_id": args.document_version_id,
        "retrieval_set_id": retrieval_set_id,
        "inputs": {"structure_units_json": str(structure_path), "canonical_topics_json": str(topics_path), "entity_facts_json": str(Path(args.entity_facts_json).expanduser().resolve()) if args.entity_facts_json else None},
        "chunk_count": len(chunks),
        "skipped_count": len(skipped),
        "retrieval_chunks": chunks,
        "skipped_structure_units": skipped,
    }
    output_path = output_dir / "retrieval_chunks.json"
    validation_path = output_dir / "validation_report.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_evidence(evidence_dir=evidence_dir, chunks=chunks)
    print(json.dumps({"retrieval_chunks": str(output_path), "validation_report": str(validation_path), **validation}, ensure_ascii=False))
    return 0 if validation["accept_for_next_step"] else 2


def _build_chunk(*, unit: dict[str, Any], assignment: dict[str, Any], entity_facts: list[dict[str, Any]], ordinal: int, retrieval_set_id: str, source_title: str | None, source_url: str | None, document_version_id: int | None) -> dict[str, Any]:
    unit_id = str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or "")
    raw_text = _compact(unit.get("raw_text"))
    summary = _compact(unit.get("summary_he"))
    page = _positive_int(unit.get("page") or assignment.get("source_page"))
    confidence = _float_or_none(assignment.get("topic_assignment_confidence")) or 0.45
    root_topic_id = str(assignment.get("root_topic_id") or "")
    root_label = str(assignment.get("root_label_he") or "")
    child_id = str(assignment.get("child_topic_id") or "") or None
    child_label = str(assignment.get("child_label_he") or "") or None
    status = str(assignment.get("topic_node_status") or "active")
    local_id = "rc_" + _short_hash("|".join([unit_id, str(page or ""), raw_text[:160]]))
    artifact_kind = "pdf_first_v4_retrieval_chunk"
    quote = str(assignment.get("topic_supporting_quote_he") or raw_text[:500])[:500]
    evidence = evidence_payload(artifact_id=local_id, document_version_id=document_version_id, source_kind="pdf_first_v4", page=page, quote_he=quote, role="topic_assignment", source_region_ids=unit.get("source_region_ids") or assignment.get("source_region_ids") or [], source_block_ids=unit.get("source_block_ids") or assignment.get("source_block_ids") or [], start_offset=None, end_offset=None, confidence=confidence)
    evidence_ref = evidence_reference(source_type="pdf", source_title=source_title, source_url=source_url, retrieval_artifact_id=local_id, artifact_kind=artifact_kind, retrieval_set_id=retrieval_set_id, header_path=[value for value in [root_label, child_label, str(unit.get("structural_role") or "")] if value], page_span={"start_page": page, "end_page": page}, offsets={"start_offset": None, "end_offset": None}, confidence=confidence, extraction_warnings=[])
    topic_node = topic_node_contract(root_topic_id=root_topic_id, root_label_he=root_label, child_topic_id_value=child_id, child_label_he=child_label, status=status, confidence=confidence, evidence_refs=[evidence_ref], decision_count=1 if _looks_decision_like(raw_text) else 0)
    entity_mentions = _dedupe_entities(entity_facts)
    entity_ids = [str(item.get("entity_fact_id") or "") for item in entity_mentions if str(item.get("entity_fact_id") or "")]
    decision_fields = decision_contract_fields(text=raw_text, topic_root_id=root_topic_id, topic_child_id=child_id, evidence_refs=[evidence_ref], evidence_span_ids=[evidence["evidence_span_id"]], confidence=confidence, entity_ids=entity_ids)
    if decision_fields.get("decision_candidate_id"):
        decision_fields["decision_citation_chunk_ids"] = [local_id]
        decision_fields["decision_citation_artifact_ids"] = [local_id]
    chunk_text = _chunk_text(root_label=root_label, child_label=child_label, structural_role=str(unit.get("structural_role") or ""), summary=summary, raw_text=raw_text, entity_mentions=entity_mentions)
    return {
        "retrieval_artifact_id": local_id,
        "chunk_id": local_id,
        "artifact_kind": artifact_kind,
        "retrieval_set_id": retrieval_set_id,
        "chunk_kind": "structure_unit",
        "structure_unit_id": unit_id,
        "semantic_unit_id": str(unit.get("semantic_unit_id") or unit_id),
        "source_semantic_unit_ids": [str(value) for value in unit.get("source_semantic_unit_ids") or [] if str(value).strip()],
        "source_window_id": unit.get("source_window_id"),
        "source_page": page,
        "page": page,
        "source_region_ids": unit.get("source_region_ids") or [],
        "source_block_ids": unit.get("source_block_ids") or [],
        "structural_role": unit.get("structural_role"),
        "section_id": unit.get("section_id"),
        "section_number": unit.get("section_number"),
        "topic_tree_version": TOPIC_TREE_VERSION,
        "topic_assignment_backend": TOPIC_ASSIGNMENT_BACKEND,
        "root_topic_id": root_topic_id,
        "root_label_he": root_label,
        "child_topic_id": child_id,
        "child_label_he": child_label,
        "topic_node_status": status,
        "topic_reject_reason": assignment.get("topic_reject_reason"),
        "topic_assignment_route": assignment.get("topic_assignment_route"),
        "topic_assignment_confidence": confidence,
        "topic_evidence_span_ids": [evidence["evidence_span_id"]],
        "topic_supporting_quote_he": quote,
        "topic_aliases_he": assignment.get("topic_aliases_he") or [],
        "topic_node": topic_node,
        "topic_ids": [value for value in [root_topic_id, child_id] if value],
        "primary_category_id": root_topic_id,
        "category_ids": [root_topic_id],
        "evidence_contract": evidence,
        "evidence_refs": [evidence_ref],
        "entity_facts": entity_mentions,
        "map_entities": [],
        "spatial_representation": "none",
        **decision_fields,
        "raw_text": raw_text,
        "summary_he": summary,
        "chunk_text": chunk_text,
        "search_text": re.sub(r"\s+", " ", chunk_text).strip(),
        "retrieval_weight": _retrieval_weight(str(unit.get("structural_role") or ""), child_label=child_label),
    }


def _chunk_text(*, root_label: str, child_label: str | None, structural_role: str, summary: str, raw_text: str, entity_mentions: list[dict[str, Any]]) -> str:
    lines = [f"root_label_he: {root_label}"]
    if child_label:
        lines.append(f"child_label_he: {child_label}")
    lines.append(f"structural_role: {structural_role or 'unknown'}")
    if summary and summary != raw_text:
        lines.append(f"summary_he: {summary}")
    if entity_mentions:
        lines.append("entity_facts: " + "; ".join(f"{item.get('entity_kind')}={item.get('canonical_raw_span')}" for item in entity_mentions[:16]))
    lines.append(f"raw_pdf_text: {raw_text}")
    return "\n".join(lines)


def _entities_by_unit(facts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        for key in [fact.get("structure_unit_id"), fact.get("semantic_unit_id")]:
            if str(key or ""):
                out[str(key)].append(fact)
    return out


def _dedupe_entities(entity_facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for fact in entity_facts:
        key = (str(fact.get("entity_kind") or ""), str(fact.get("canonical_raw_span") or ""), str(fact.get("semantic_unit_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append({"entity_fact_id": fact.get("entity_fact_id"), "entity_kind": fact.get("entity_kind"), "canonical_raw_span": fact.get("canonical_raw_span"), "normalized_display": fact.get("normalized_display"), "source_semantic_unit_id": fact.get("semantic_unit_id"), "repair_route": fact.get("repair_route"), "confidence": fact.get("confidence")})
    return out


def _validate(*, chunks: list[dict[str, Any]], skipped: list[dict[str, Any]], structure_units: list[dict[str, Any]], assignments_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    duplicate_ids = sorted([item for item, count in Counter(str(chunk.get("retrieval_artifact_id") or "") for chunk in chunks).items() if count > 1])
    missing_assignments = [str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or "") for unit in structure_units if str(unit.get("structural_role") or "") not in EXCLUDED_STRUCTURAL_ROLES and str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or "") not in assignments_by_id]
    contract_counts = missing_contract_counts(chunks)
    blocked_issue_count = len(duplicate_ids) + len(missing_assignments) + sum(int(value) for value in contract_counts.values())
    return {
        "chunk_count": len(chunks),
        "skipped_count": len(skipped),
        "duplicate_retrieval_artifact_ids": duplicate_ids,
        "missing_assignment_count": len(missing_assignments),
        **contract_counts,
        "missing_decision_contract_examples": _missing_decision_contract_examples(chunks)[:20],
        "topic_quality_warnings": _topic_quality_warnings(chunks)[:50],
        "blocked_issue_count": blocked_issue_count,
        "accept_for_next_step": bool(chunks) and blocked_issue_count == 0,
    }


def _missing_decision_contract_examples(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    required = [
        "decision_candidate_id",
        "decision_type",
        "decision_outcome_type",
        "decision_summary_he",
        "decision_text_he",
        "decision_evidence_span_ids",
        "decision_supporting_quote_he",
        "decision_citation_chunk_ids",
        "decision_confidence",
        "decision_topic_root_id",
    ]
    out = []
    for chunk in chunks:
        raw_text = chunk.get("raw_text") or chunk.get("chunk_text") or ""
        is_decision_like = bool(chunk.get("decision_candidate_id")) or _looks_decision_like(raw_text)
        if not is_decision_like or chunk.get("decision_reject_reason"):
            continue
        missing = [key for key in required if _empty_contract_value(chunk.get(key))]
        has_child_topic = not _empty_contract_value(chunk.get("child_topic_id")) or not _empty_contract_value(chunk.get("child_label_he"))
        if has_child_topic and _empty_contract_value(chunk.get("decision_topic_child_id")):
            missing.append("decision_topic_child_id")
        if missing:
            out.append(
                {
                    "retrieval_artifact_id": chunk.get("retrieval_artifact_id"),
                    "structure_unit_id": chunk.get("structure_unit_id"),
                    "source_page": chunk.get("source_page"),
                    "structural_role": chunk.get("structural_role"),
                    "root_label_he": chunk.get("root_label_he"),
                    "child_label_he": chunk.get("child_label_he"),
                    "missing_fields": missing,
                    "text_preview": _compact(raw_text)[:360],
                }
            )
    return out


def _topic_quality_warnings(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for chunk in chunks:
        child_label = str(chunk.get("child_label_he") or "").strip()
        if not child_label or str(chunk.get("topic_node_status") or "") == "rejected":
            continue
        reason = noisy_label_reason(child_label)
        if reason:
            out.append(
                {
                    "retrieval_artifact_id": chunk.get("retrieval_artifact_id"),
                    "structure_unit_id": chunk.get("structure_unit_id"),
                    "source_page": chunk.get("source_page"),
                    "root_label_he": chunk.get("root_label_he"),
                    "child_label_he": child_label,
                    "reason": reason,
                    "route": chunk.get("topic_assignment_route"),
                    "text_preview": _compact(chunk.get("raw_text") or "")[:360],
                }
            )
    return out


def _empty_contract_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return len(value) == 0
    return False


def _write_evidence(*, evidence_dir: Path, chunks: list[dict[str, Any]]) -> None:
    rows = [{"retrieval_artifact_id": chunk.get("retrieval_artifact_id"), "evidence_contract": chunk.get("evidence_contract"), "evidence_refs": chunk.get("evidence_refs") or []} for chunk in chunks]
    (evidence_dir / "evidence_refs.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _looks_decision_like(value: str) -> bool:
    normalized = " ".join(str(value or "").split())
    return any(term in normalized for term in ["מחליטים", "מאשרים", "הוחלט", "הצביעו", "פה אחד", "ברוב קולות"])


def _retrieval_weight(role: str, *, child_label: str | None) -> float:
    if role == "metadata":
        return 0.55
    if role in {"vote_or_result", "task_row"}:
        return 0.95
    return 1.0 if child_label else 0.8


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
