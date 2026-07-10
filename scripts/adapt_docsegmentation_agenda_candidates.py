#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "docsegmentation_agenda_adapter_v1"
GOOD_CANDIDATE_STATUSES = {"good_research_candidate"}
GOOD_SKIPPED_PAGE_STATUSES = {"skipped_image_only_page_accepted", "ignored_image_or_nonsemantic_page", "ignored_no_native_text_page"}
SUPPORTED_EXTERNAL_LINK_STATUSES = {"visually_supported_research_link", "good_research_link"}
EXTERNAL_LINK_NEEDS_REVIEW_STATUS = "external_document_link_needs_visual_review"
DATE_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b")
HEADING_NUMBER_RE = re.compile(r"^\s*(\d{1,4}(?:[./]\d{1,4})*)")
STRONG_EXTERNAL_REFERENCE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"מצורפ",
        r"מצ[\"״']ב",
        r"מצ[\"״']ל",
        r"כנספח",
        r"attached",
        r"enclosed",
        r"annexed",
        r"as\s+an?\s+(?:appendix|annex|attachment|exhibit)",
        r"see\s+(?:the\s+)?(?:appendix|annex|attachment|exhibit)",
    ]
]
WEAK_EXTERNAL_REFERENCE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"נספח",
        r"appendix",
        r"annex",
        r"annexe",
        r"attachment",
        r"addendum",
        r"exhibit",
        r"enclosure",
    ]
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Adapt DocSegmentation native agenda candidates into Municipality PDF-first Step 4/5 inputs."
    )
    parser.add_argument("--agenda-candidate-report-json", required=True, help="DocSegmentation agenda_candidate_report.json")
    parser.add_argument("--manual-visual-judgement-json", default="", help="Optional DocSegmentation manual_visual_judgements.json")
    parser.add_argument("--internal-topic-split-json", default="", help="Optional internal_topic_split_report.json. Parents with child topics are treated as container-only, not final Step 4/5 units.")
    parser.add_argument("--external-link-json", default="", help="Optional reviewed external/appendix evidence link manifest for this one candidate run")
    parser.add_argument("--output-dir", required=True, help="Directory for adapter outputs")
    parser.add_argument("--protocol-report-dir", default="", help="Directory for the per-protocol HTML report. Defaults to --output-dir.")
    parser.add_argument("--pipeline-root-dir", default="", help="Optional root containing adapter/step3_5/step4/step4_5/step5 outputs for the HTML summary.")
    parser.add_argument("--packet-role", choices=["protocol", "attachment", "unknown"], default="protocol")
    parser.add_argument("--max-preview-chars", type=int, default=1200)
    args = parser.parse_args(argv)

    agenda_path = Path(args.agenda_candidate_report_json).expanduser().resolve()
    manual_path = Path(args.manual_visual_judgement_json).expanduser().resolve() if str(args.manual_visual_judgement_json).strip() else _default_manual_path(agenda_path)
    internal_topic_split_path = Path(args.internal_topic_split_json).expanduser().resolve() if str(args.internal_topic_split_json).strip() else None
    internal_topic_split_report = _load_json(internal_topic_split_path) if internal_topic_split_path and internal_topic_split_path.exists() else None
    external_link_manifest_path = Path(args.external_link_json).expanduser().resolve() if str(args.external_link_json).strip() else None
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    protocol_report_dir = Path(args.protocol_report_dir).expanduser().resolve() if str(args.protocol_report_dir).strip() else output_dir
    protocol_report_dir.mkdir(parents=True, exist_ok=True)
    pipeline_root = Path(args.pipeline_root_dir).expanduser().resolve() if str(args.pipeline_root_dir).strip() else _default_pipeline_root(output_dir=output_dir, protocol_report_dir=protocol_report_dir)

    agenda_report = json.loads(agenda_path.read_text(encoding="utf-8"))
    manual_payload = _load_json(manual_path) if manual_path and manual_path.exists() else {}
    result = adapt_agenda_candidates(
        agenda_report=agenda_report,
        agenda_report_path=agenda_path,
        manual_payload=manual_payload,
        manual_path=manual_path if manual_path and manual_path.exists() else None,
        internal_topic_split_report=internal_topic_split_report,
        internal_topic_split_path=internal_topic_split_path,
        external_link_manifest_path=external_link_manifest_path,
        output_dir=output_dir,
        packet_role=str(args.packet_role),
        max_preview_chars=max(200, int(args.max_preview_chars)),
    )

    semantic_path = output_dir / "semantic_units.json"
    grouped_path = output_dir / "grouped_structure_units.json"
    validation_path = output_dir / "validation_report.json"
    reviewed_attachment_context_path = output_dir / "reviewed_external_attachment_contexts.json"
    protocol_html_path = protocol_report_dir / "protocol_hybrid_review.html"
    legacy_html_path = output_dir / "docsegmentation_adapter_report.html"
    audit_path = output_dir / "adapter_audit.md"

    semantic_path.write_text(json.dumps(result["semantic_payload"], ensure_ascii=False, indent=2), encoding="utf-8")
    grouped_path.write_text(json.dumps(result["grouped_payload"], ensure_ascii=False, indent=2), encoding="utf-8")
    validation_path.write_text(json.dumps(result["validation"], ensure_ascii=False, indent=2), encoding="utf-8")
    reviewed_attachment_context_path.write_text(json.dumps(result["reviewed_external_attachment_contexts"], ensure_ascii=False, indent=2), encoding="utf-8")
    html_text = _html_report(
        result=result,
        output_dir=protocol_report_dir,
        max_preview_chars=max(200, int(args.max_preview_chars)),
        downstream=_load_downstream_summary(pipeline_root=pipeline_root),
    )
    protocol_html_path.write_text(html_text, encoding="utf-8")
    if legacy_html_path != protocol_html_path:
        legacy_html_path.write_text(html_text, encoding="utf-8")
    audit_path.write_text(_audit_markdown(result=result), encoding="utf-8")

    print(
        json.dumps(
            {
                "semantic_units": str(semantic_path),
                "grouped_structure_units": str(grouped_path),
                "validation_report": str(validation_path),
                "reviewed_external_attachment_contexts": str(reviewed_attachment_context_path),
                "protocol_html_report": str(protocol_html_path),
                "html_report": str(legacy_html_path),
                "audit_report": str(audit_path),
                "status": result["validation"]["status"],
                "reason": result["validation"]["reason"],
                "candidate_count": result["validation"]["candidate_count"],
                "accepted_candidate_count": result["validation"]["accepted_candidate_count"],
                "needs_review_candidate_count": result["validation"]["needs_review_candidate_count"],
                "failed_candidate_count": result["validation"]["failed_candidate_count"],
                "reviewed_external_attachment_context_count": len(result.get("reviewed_external_attachment_contexts") or []),
                "accept_for_next_step": result["validation"]["accept_for_next_step"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not result["validation"]["accept_for_next_step"]:
        print(
            json.dumps(
                {
                    "status": result["validation"]["status"],
                    "reason": result["validation"]["reason"],
                    "raw_evidence": [
                        *(result["validation"].get("blocking_issues", []) or []),
                        *(result["validation"].get("review_issues", []) or []),
                    ][:12],
                    "suggested_generic_next_step": result["validation"].get("suggested_generic_next_step"),
                    "protocol_html_report": str(protocol_html_path),
                    "html_report": str(legacy_html_path),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0 if result["validation"]["accept_for_next_step"] else 2


def adapt_agenda_candidates(
    *,
    agenda_report: dict[str, Any],
    agenda_report_path: Path,
    manual_payload: dict[str, Any],
    manual_path: Path | None,
    output_dir: Path,
    internal_topic_split_report: dict[str, Any] | None = None,
    internal_topic_split_path: Path | None = None,
    external_link_manifest_path: Path | None = None,
    packet_role: str = "protocol",
    max_preview_chars: int = 1200,
) -> dict[str, Any]:
    candidates = [candidate for candidate in agenda_report.get("candidates") or [] if isinstance(candidate, dict)]
    navigation_candidates = [candidate for candidate in agenda_report.get("agenda_navigation_candidates") or [] if isinstance(candidate, dict)]
    internal_splits_by_candidate = _internal_splits_by_candidate(internal_topic_split_report or {})
    external_links_by_candidate, missing_external_docs_by_candidate, external_link_issues, external_link_manifest_summary = _load_external_link_manifest(external_link_manifest_path)
    candidate_reviews = [_candidate_review(candidate=candidate, manual_payload=manual_payload, agenda_report=agenda_report) for candidate in candidates]
    review_by_id = {str(review.get("candidate_id") or ""): review for review in candidate_reviews}

    semantic_units: list[dict[str, Any]] = []
    structure_units: list[dict[str, Any]] = []
    topic_groups: list[dict[str, Any]] = []
    conversion_issues: list[dict[str, Any]] = []
    candidate_semantic_preflights: list[dict[str, Any]] = []
    reviewed_external_attachment_contexts: list[dict[str, Any]] = []

    for ordinal, candidate in enumerate(candidates, start=1):
        candidate_id = str(candidate.get("candidate_id") or "")
        review = review_by_id.get(candidate_id, {})
        unit_id = _unit_id(candidate=candidate, ordinal=ordinal)
        raw_native_text = str(candidate.get("raw_native_text") or "")
        readable_text = str(candidate.get("selected_text") or candidate.get("rtl_normalized_text") or "")
        raw_text = readable_text if readable_text.strip() else raw_native_text
        candidate_preflight = _candidate_external_preflight(
            candidate=candidate,
            external_links=external_links_by_candidate.get(candidate_id, []),
            missing_external_documents=missing_external_docs_by_candidate.get(candidate_id, []),
        )
        candidate_semantic_preflights.append(candidate_preflight)
        if not raw_native_text.strip():
            conversion_issues.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "status": "failed",
                    "reason": "missing_raw_native_text",
                    "raw_evidence": "DocSegmentation candidate has no raw_native_text to preserve as evidence.",
                }
            )
            raw_native_text = raw_text
        if not raw_text.strip():
            continue

        pages = _positive_int_list(candidate.get("pages") or [])
        page = min(pages) if pages else None
        block_ids = _string_list(candidate.get("block_ids"))
        source_region_ids = _candidate_source_region_ids(candidate)
        heading_text = _compact(candidate.get("heading_text"))
        heading_raw_text = _candidate_heading_raw_text(candidate)
        page_range = _page_range(candidate, pages=pages)
        section_number = _heading_number(heading_text)
        summary = heading_text or _compact(readable_text or raw_text)[:240]
        date_source_text = "\n".join(_dedupe_strings([raw_text, raw_native_text]))
        external_attachment_contexts = _attachment_contexts_from_external_links(
            unit_id=unit_id,
            candidate=candidate,
            links=candidate_preflight.get("external_document_links") or [],
        )
        reviewed_external_attachment_contexts.extend(external_attachment_contexts)

        base_source = {
            "source_system": "DocSegmentation",
            "source_step": agenda_report.get("step"),
            "source_schema_version": agenda_report.get("schema_version"),
            "source_agenda_candidate_report_json": str(agenda_report_path),
            "source_candidate_id": candidate.get("candidate_id"),
            "source_candidate_status": candidate.get("status"),
            "source_candidate_status_reason": candidate.get("status_reason"),
            "source_candidate_metadata_path": candidate.get("candidate_metadata_path"),
            "source_candidate_text_path": candidate.get("candidate_text_path"),
            "source_group_crops": candidate.get("group_crops") or [],
            "source_candidate_raw_native_text": raw_native_text,
            "source_candidate_readable_text": readable_text,
            "source_text_policy": "raw_native_text preserved for audit; deterministic display text is used for downstream Step 4/5 fields when available.",
            "manual_visual_judgement": review,
            "candidate_semantic_preflight": candidate_preflight,
            "external_document_references": candidate_preflight.get("external_document_references") or [],
            "external_document_links": candidate_preflight.get("external_document_links") or [],
            "internal_topic_split": internal_splits_by_candidate.get(candidate_id, {}),
        }

        semantic_units.append(
            {
                "semantic_unit_id": unit_id,
                "source_window_id": f"docseg_{candidate.get('candidate_id') or ordinal}",
                "page": page,
                "source_region_ids": source_region_ids,
                "source_block_ids": block_ids,
                "raw_text": raw_text,
                "raw_native_text": raw_native_text,
                "readable_text": readable_text,
                "summary_he": summary,
                "explicit_actions": [],
                "explicit_dates": _dedupe_strings(DATE_RE.findall(date_source_text)),
                "explicit_people_or_orgs": [],
                "sanitized_model_spans": [],
                "source_semantic_unit_ids": [unit_id],
                "candidate_semantic_preflight": candidate_preflight,
                "external_document_references": candidate_preflight.get("external_document_references") or [],
                "external_document_links": candidate_preflight.get("external_document_links") or [],
                "reviewed_external_attachment_contexts": external_attachment_contexts,
                "docsegmentation": base_source,
            }
        )

        structure_unit = {
            "structure_unit_id": unit_id,
            "semantic_unit_id": unit_id,
            "source_semantic_unit_ids": [unit_id],
            "source_window_id": f"docseg_{candidate.get('candidate_id') or ordinal}",
            "page": page,
            "source_region_ids": source_region_ids,
            "source_block_ids": block_ids,
            "raw_text": raw_text,
            "raw_native_text": raw_native_text,
            "readable_text": readable_text,
            "summary_he": summary,
            "header_text": heading_text,
            "title_he": heading_text,
            "explicit_actions": [],
            "structural_role": "outline_item" if heading_text else "body",
            "section_id": unit_id,
            "section_number": section_number,
            "continuation_of_unit_id": None,
            "topic_assignment_eligible": True,
            "fragment_index": 1,
            "fragment_count": 1,
            "topic_group_id": unit_id,
            "topic_group_role": "anchor",
            "topic_group_text_raw": raw_text,
            "topic_group_text_for_grouping": raw_text,
            "topic_group_text_readable": readable_text,
            "topic_group_raw_native_text": raw_native_text,
            "topic_group_anchor_raw_text": heading_raw_text,
            "topic_group_anchor_readable_text": heading_text,
            "topic_group_source_unit_ids": [unit_id],
            "merged_detail_unit_ids": [],
            "merged_detail_texts": [],
            "topic_group_pages": pages,
            "topic_group_page_range": page_range,
            "topic_group_source_block_ids": block_ids,
            "topic_group_source_region_ids": source_region_ids,
            "docsegmentation_candidate_id": candidate.get("candidate_id"),
            "docsegmentation_selected_text": readable_text,
            "docsegmentation_raw_native_text": raw_native_text,
            "docsegmentation_rtl_normalized_text": candidate.get("rtl_normalized_text") or "",
            "docsegmentation_quality_flags": candidate.get("quality_flags") or [],
            "docsegmentation_group_crops": candidate.get("group_crops") or [],
            "candidate_semantic_preflight": candidate_preflight,
            "internal_topic_split": internal_splits_by_candidate.get(candidate_id, {}),
            "external_document_references": candidate_preflight.get("external_document_references") or [],
            "external_document_links": candidate_preflight.get("external_document_links") or [],
            "reviewed_external_attachment_contexts": external_attachment_contexts,
            "structure_evidence": {
                "adapter": SCHEMA_VERSION,
                "split_reason": "docsegmentation_native_agenda_candidate",
                **base_source,
            },
        }
        structure_units.append(structure_unit)
        topic_groups.append(
            {
                "topic_group_id": unit_id,
                "anchor_unit_id": unit_id,
                "source_candidate_id": candidate.get("candidate_id"),
                "status": review.get("final_status") or "needs_review_missing_manual_judgement",
                "pages": pages,
                "page_range": page_range,
                "source_block_ids": block_ids,
                "source_region_ids": source_region_ids,
                "heading_text": heading_text,
                "raw_text_preview": raw_text[:max_preview_chars],
                "raw_native_text_preview": raw_native_text[:max_preview_chars],
                "selected_text_preview": readable_text[:max_preview_chars],
                "group_crops": candidate.get("group_crops") or [],
                "candidate_semantic_preflight": candidate_preflight,
                "internal_topic_split": internal_splits_by_candidate.get(candidate_id, {}),
                "reviewed_external_attachment_contexts": external_attachment_contexts,
            }
        )

    validation = _validation(
        agenda_report=agenda_report,
        manual_payload=manual_payload,
        candidates=candidates,
        candidate_reviews=candidate_reviews,
        conversion_issues=conversion_issues,
        external_link_issues=external_link_issues,
        candidate_semantic_preflights=candidate_semantic_preflights,
        semantic_units=semantic_units,
        structure_units=structure_units,
        manual_path=manual_path,
        internal_topic_split_report=internal_topic_split_report,
        internal_topic_split_path=internal_topic_split_path,
    )
    semantic_payload = {
        "step": "adapt_docsegmentation_agenda_candidates_semantic_units",
        "schema_version": SCHEMA_VERSION,
        "input_agenda_candidate_report_json": str(agenda_report_path),
        "input_manual_visual_judgement_json": str(manual_path) if manual_path else None,
        "input_internal_topic_split_json": str(internal_topic_split_path) if internal_topic_split_path else None,
        "input_external_link_manifest_json": str(external_link_manifest_path) if external_link_manifest_path else None,
        "input_pdf": agenda_report.get("input_pdf_path"),
        "source_evidence_folder_path": agenda_report.get("evidence_folder_path"),
        "external_link_manifest_summary": external_link_manifest_summary,
        "reviewed_external_attachment_context_count": len(reviewed_external_attachment_contexts),
        "selected_pages": sorted({page for unit in structure_units for page in _positive_int_list(unit.get("topic_group_pages") or [])}),
        "window_count": len(semantic_units),
        "unit_count": len(semantic_units),
        "windows": _semantic_windows_from_units(semantic_units),
        "semantic_units": semantic_units,
        "validation": validation,
    }
    grouped_payload = {
        "step": "adapt_docsegmentation_agenda_candidates",
        "schema_version": SCHEMA_VERSION,
        "input_agenda_candidate_report_json": str(agenda_report_path),
        "input_manual_visual_judgement_json": str(manual_path) if manual_path else None,
        "input_internal_topic_split_json": str(internal_topic_split_path) if internal_topic_split_path else None,
        "input_external_link_manifest_json": str(external_link_manifest_path) if external_link_manifest_path else None,
        "input_pdf": agenda_report.get("input_pdf_path"),
        "source_evidence_folder_path": agenda_report.get("evidence_folder_path"),
        "source_agenda_candidate_run_path": agenda_report.get("output_run_dir_path"),
        "accepted_as_ground_truth": False,
        "research_use": "docsegmentation_native_agenda_candidates_as_prefilter_for_municipality_steps",
        "packet_role": packet_role,
        "unit_count": len(structure_units),
        "topic_group_count": len(topic_groups),
        "topic_groups": topic_groups,
        "agenda_navigation_candidates": navigation_candidates,
        "candidate_semantic_preflights": candidate_semantic_preflights,
        "external_link_manifest_summary": external_link_manifest_summary,
        "reviewed_external_attachment_contexts": reviewed_external_attachment_contexts,
        "structure_units": structure_units,
        "units": structure_units,
        "validation": validation,
    }
    return {
        "agenda_report": agenda_report,
        "agenda_report_path": str(agenda_report_path),
        "manual_payload": manual_payload,
        "manual_path": str(manual_path) if manual_path else None,
        "internal_topic_split_path": str(internal_topic_split_path) if internal_topic_split_path else None,
        "internal_topic_split_report": internal_topic_split_report or {},
        "external_link_manifest_path": str(external_link_manifest_path) if external_link_manifest_path else None,
        "external_link_manifest_summary": external_link_manifest_summary,
        "output_dir": str(output_dir),
        "candidate_reviews": candidate_reviews,
        "candidate_semantic_preflights": candidate_semantic_preflights,
        "navigation_candidates": navigation_candidates,
        "reviewed_external_attachment_contexts": reviewed_external_attachment_contexts,
        "semantic_payload": semantic_payload,
        "grouped_payload": grouped_payload,
        "validation": validation,
    }


def _internal_splits_by_candidate(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    parents = report.get("parent_topic_splits") if isinstance(report.get("parent_topic_splits"), list) else []
    for parent in parents:
        if not isinstance(parent, dict):
            continue
        parent_id = str(parent.get("parent_candidate_id") or "").strip()
        if not parent_id:
            continue
        child_topics = [child for child in parent.get("child_topics") or [] if isinstance(child, dict)]
        out[parent_id] = {
            "parent_candidate_id": parent_id,
            "status": parent.get("status") or "",
            "status_reason": parent.get("status_reason") or "",
            "parent_downstream_policy": parent.get("parent_downstream_policy") or "",
            "child_topic_count": len(child_topics),
            "boundary_count": len([item for item in parent.get("boundary_candidates") or [] if isinstance(item, dict)]),
            "internal_topic_split_status": report.get("status") or "",
            "internal_topic_split_report_path": report.get("internal_topic_split_json_path") or report.get("html_report_path") or "",
            "html_report_path": report.get("html_report_path") or "",
            "child_topics": [
                {
                    "child_topic_id": child.get("child_topic_id") or "",
                    "status": child.get("status") or "",
                    "split_source": child.get("split_source") or "",
                    "anchor_text": child.get("anchor_text") or "",
                    "pages": child.get("pages") or [],
                    "page_range": child.get("page_range") or [],
                    "selected_char_count": child.get("selected_char_count"),
                    "child_crops": child.get("child_crops") or [],
                }
                for child in child_topics
            ],
        }
    return out


def _validation(
    *,
    agenda_report: dict[str, Any],
    manual_payload: dict[str, Any],
    candidates: list[dict[str, Any]],
    candidate_reviews: list[dict[str, Any]],
    conversion_issues: list[dict[str, Any]],
    external_link_issues: list[dict[str, Any]],
    candidate_semantic_preflights: list[dict[str, Any]],
    semantic_units: list[dict[str, Any]],
    structure_units: list[dict[str, Any]],
    manual_path: Path | None,
    internal_topic_split_report: dict[str, Any] | None = None,
    internal_topic_split_path: Path | None = None,
) -> dict[str, Any]:
    blocking_issues: list[dict[str, Any]] = []
    review_issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    blocking_issues.extend(conversion_issues)
    blocking_issues.extend(issue for issue in external_link_issues if str(issue.get("status") or "") == "failed")
    review_issues.extend(issue for issue in external_link_issues if str(issue.get("status") or "") == "needs_review")

    if not candidates:
        blocking_issues.append(
            {
                "status": "failed",
                "reason": "no_candidates",
                "raw_evidence": "DocSegmentation agenda report contains no candidates.",
            }
        )
    if manual_path is None:
        review_issues.append(
            {
                "status": "needs_review",
                "reason": "missing_manual_visual_judgement_json",
                "raw_evidence": "No manual visual judgement JSON was supplied or found next to the agenda candidate report.",
            }
        )
    if internal_topic_split_path is not None and internal_topic_split_report is None:
        blocking_issues.append(
            {
                "status": "failed",
                "reason": "internal_topic_split_report_not_found",
                "path": str(internal_topic_split_path),
                "raw_evidence": "The adapter was given --internal-topic-split-json, but that file does not exist or was not loaded.",
                "suggested_generic_next_step": "Regenerate the internal topic split report or rerun the adapter without this optional input.",
            }
        )
    internal_splits_by_candidate = _internal_splits_by_candidate(internal_topic_split_report or {})

    for preflight in candidate_semantic_preflights:
        candidate_id = str(preflight.get("candidate_id") or "")
        references = [item for item in preflight.get("external_document_references") or [] if isinstance(item, dict)]
        links = [item for item in preflight.get("external_document_links") or [] if isinstance(item, dict)]
        if preflight.get("needs_external_document_link") is True:
            missing_documents = [item for item in preflight.get("missing_external_documents") or [] if isinstance(item, dict)]
            if missing_documents:
                warnings.append(
                    {
                        "candidate_id": candidate_id,
                        "status": "warning",
                        "reason": "external_document_reference_marked_missing_nonblocking",
                        "raw_evidence": {
                            "external_document_references": references,
                            "external_document_link_statuses": [str(link.get("status") or "") for link in links],
                            "missing_external_documents": missing_documents,
                        },
                        "step4_5_policy": "The main protocol candidate may continue to Step 4/5 with missing external documents marked as absent; the missing external documents must not be used as supporting context.",
                        "suggested_generic_next_step": "If the missing PDFs are later supplied, render/extract them one file at a time, visually verify them, add reviewed external-link entries, and rerun the adapter.",
                    }
                )
            else:
                review_issues.append(
                    {
                        "candidate_id": candidate_id,
                        "status": "needs_review",
                        "reason": "external_document_reference_needs_reviewed_link",
                        "raw_evidence": {
                            "external_document_references": references,
                            "external_document_link_statuses": [str(link.get("status") or "") for link in links],
                        },
                        "suggested_generic_next_step": "Create a reviewed external-link entry, or mark searched-but-missing external documents in the manifest, then rerun the adapter.",
                    }
                )
        elif links and not any(_external_link_satisfies_reference(link) for link in links):
            warnings.append(
                {
                    "candidate_id": candidate_id,
                    "status": "warning",
                    "reason": "external_document_link_not_used_without_supported_visual_judgement",
                    "raw_evidence": [str(link.get("status") or "") for link in links],
                    "suggested_generic_next_step": "If this external document should affect topic assignment, visually review the evidence and update the manifest link status.",
                }
            )

    skipped_pages = _skipped_pages_by_page(agenda_report)
    manual_skipped = _manual_skipped_page_judgements(manual_payload)
    for review in candidate_reviews:
        candidate_id = str(review.get("candidate_id") or "")
        final_status = str(review.get("final_status") or "")
        candidate = review.get("source_candidate") if isinstance(review.get("source_candidate"), dict) else {}
        internal_split = internal_splits_by_candidate.get(candidate_id, {})
        if int(internal_split.get("child_topic_count") or 0) > 1:
            review_issues.append(
                {
                    "candidate_id": candidate_id,
                    "status": "needs_review",
                    "reason": "candidate_has_internal_child_topics_container_only",
                    "raw_evidence": {
                        "parent_downstream_policy": internal_split.get("parent_downstream_policy"),
                        "child_topic_count": internal_split.get("child_topic_count"),
                        "child_topic_anchors": [child.get("anchor_text") for child in internal_split.get("child_topics") or []],
                        "internal_topic_split_report_path": str(internal_topic_split_path) if internal_topic_split_path else "",
                    },
                    "suggested_generic_next_step": "Visually and semantically review the child-topic crops, then adapt reviewed child topics downstream instead of this broad parent as one final Step 4/5 unit.",
                }
            )
        missing_pages = _missing_middle_pages(candidate=candidate, skipped_pages=skipped_pages)
        for page in missing_pages:
            blocking_issues.append(
                {
                    "candidate_id": candidate_id,
                    "status": "failed",
                    "reason": "candidate_omits_middle_native_page",
                    "page": page,
                    "raw_evidence": f"Candidate pages are {candidate.get('pages')}; page_range is {candidate.get('page_range')}; page {page} is not recorded as an image-only skipped page.",
                    "suggested_generic_next_step": "Fix the generic continuation/boundary grouping rule and rerun this one PDF before Step 4.",
                }
            )
        if final_status in GOOD_CANDIDATE_STATUSES and review.get("belongs_together") is not False:
            continue
        if final_status.startswith("failed") or review.get("belongs_together") is False:
            blocking_issues.append(
                {
                    "candidate_id": candidate_id,
                    "status": "failed",
                    "reason": "manual_visual_judgement_failed_candidate",
                    "raw_evidence": review.get("visual_judgement") or review.get("why") or final_status,
                    "suggested_generic_next_step": "Fix the generic boundary grouping rule, rerun this one PDF, and visually review the new candidate crops.",
                }
            )
        else:
            review_issues.append(
                {
                    "candidate_id": candidate_id,
                    "status": "needs_review",
                    "reason": "candidate_not_manually_accepted",
                    "raw_evidence": review.get("visual_judgement") or review.get("why") or final_status or "missing manual candidate judgement",
                    "suggested_generic_next_step": "Open the HTML report and rendered candidate crop images, then record a manual visual judgement before Step 4.",
                }
            )

    for skipped_page, skipped in sorted(skipped_pages.items()):
        manual = manual_skipped.get(skipped_page, {})
        final_status = str(manual.get("final_status") or "")
        source_status = str(skipped.get("status") or "")
        if final_status in GOOD_SKIPPED_PAGE_STATUSES or source_status in GOOD_SKIPPED_PAGE_STATUSES or skipped.get("downstream_blocking") is False:
            reason = "ignored_nonsemantic_or_image_page_by_policy" if source_status.startswith("ignored_") or final_status.startswith("ignored_") or skipped.get("downstream_blocking") is False else "manual_accepted_skipped_image_only_page"
            warnings.append(
                {
                    "status": "warning",
                    "reason": reason,
                    "page": skipped_page,
                    "raw_evidence": manual.get("visual_judgement") or skipped.get("status_reason") or skipped.get("skip_reason"),
                    "no_ocr_attempted": skipped.get("no_ocr_attempted"),
                }
            )
        else:
            review_issues.append(
                {
                    "status": "needs_review",
                    "reason": "skipped_page_needs_manual_visual_judgement",
                    "page": skipped_page,
                    "raw_evidence": skipped,
                    "suggested_generic_next_step": "Visually check the rendered page image and record whether the native-only skip is acceptable.",
                }
            )

    for unit in structure_units:
        pages = _positive_int_list(unit.get("topic_group_pages") or [])
        raw_len = len(str(unit.get("topic_group_text_raw") or ""))
        if len(pages) >= 6 or raw_len >= 12000:
            warnings.append(
                {
                    "structure_unit_id": unit.get("structure_unit_id"),
                    "status": "warning",
                    "reason": "broad_candidate_may_need_internal_subject_split",
                    "pages": pages,
                    "raw_char_count": raw_len,
                    "suggested_generic_next_step": "Use the group as a safe global agenda boundary, then split internal subjects later if retrieval quality needs it.",
                }
            )

    duplicate_ids = sorted([item for item, count in Counter(str(unit.get("structure_unit_id") or "") for unit in structure_units).items() if item and count > 1])
    if duplicate_ids:
        blocking_issues.append(
            {
                "status": "failed",
                "reason": "duplicate_structure_unit_ids",
                "raw_evidence": duplicate_ids,
            }
        )

    accepted_candidate_count = sum(1 for review in candidate_reviews if str(review.get("final_status") or "") in GOOD_CANDIDATE_STATUSES and review.get("belongs_together") is not False)
    failed_candidate_ids = sorted({str(issue.get("candidate_id")) for issue in blocking_issues if issue.get("candidate_id")})
    needs_review_candidate_ids = sorted({str(issue.get("candidate_id")) for issue in review_issues if issue.get("candidate_id")})
    if blocking_issues:
        status = "failed"
        reason = "One or more DocSegmentation candidate groups are not safe for Municipality Step 4/5."
    elif review_issues:
        status = "needs_review"
        reason = "Visual/manual evidence is incomplete, so the adapter output is not accepted for Step 4/5 yet."
    else:
        status = "accepted"
        reason = "All DocSegmentation candidate groups have supporting manual visual judgement and required provenance."

    return {
        "step": "adapt_docsegmentation_agenda_candidates_validation",
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "candidate_count": len(candidates),
        "semantic_unit_count": len(semantic_units),
        "structure_unit_count": len(structure_units),
        "accepted_candidate_count": accepted_candidate_count,
        "needs_review_candidate_count": len(needs_review_candidate_ids),
        "failed_candidate_count": len(failed_candidate_ids),
        "failed_candidate_ids": failed_candidate_ids,
        "needs_review_candidate_ids": needs_review_candidate_ids,
        "blocking_issues": blocking_issues,
        "review_issues": review_issues,
        "warnings": warnings,
        "accept_for_next_step": not blocking_issues and not review_issues,
        "suggested_generic_next_step": "Open the HTML report, visually judge rendered candidate crops against the original page images, then rerun the adapter with manual judgements before Step 4." if blocking_issues or review_issues else "Proceed to Municipality Step 3.5, Step 4, Step 4.5, and Step 5.",
    }


def _load_external_link_manifest(manifest_path: Path | None) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], list[dict[str, Any]], dict[str, Any]]:
    if not manifest_path:
        return {}, {}, [], {"provided": False, "link_count": 0, "supported_link_count": 0, "missing_external_document_count": 0, "issue_count": 0}

    manifest_path = manifest_path.expanduser().resolve()
    summary: dict[str, Any] = {"provided": True, "manifest_path": str(manifest_path), "link_count": 0, "supported_link_count": 0, "missing_external_document_count": 0, "issue_count": 0}
    issues: list[dict[str, Any]] = []
    if not manifest_path.exists():
        issues.append(
            {
                "status": "failed",
                "reason": "external_link_manifest_not_found",
                "path": str(manifest_path),
                "raw_evidence": "The adapter was given --external-link-json, but that file does not exist.",
                "suggested_generic_next_step": "Create the reviewed external-link manifest for this one candidate run, or rerun without --external-link-json.",
            }
        )
        summary["issue_count"] = len(issues)
        return {}, {}, issues, summary

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        issues.append(
            {
                "status": "failed",
                "reason": "external_link_manifest_json_invalid",
                "path": str(manifest_path),
                "raw_evidence": f"JSON parse error: {exc}",
                "suggested_generic_next_step": "Fix the manifest JSON syntax and rerun the adapter.",
            }
        )
        summary["issue_count"] = len(issues)
        return {}, {}, issues, summary

    raw_links = manifest.get("links") if isinstance(manifest, dict) else manifest
    missing_external_docs_by_candidate = _missing_external_documents_by_candidate(manifest if isinstance(manifest, dict) else {})
    if not isinstance(raw_links, list):
        issues.append(
            {
                "status": "failed",
                "reason": "external_link_manifest_missing_links",
                "path": str(manifest_path),
                "raw_evidence": "External link manifest must be a list or contain a top-level 'links' list.",
                "suggested_generic_next_step": "Use links[] with candidate_id, document_role, external_evidence_report_path, and visual_judgement.status.",
            }
        )
        summary["issue_count"] = len(issues)
        return {}, missing_external_docs_by_candidate, issues, summary

    links_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for link_index, raw in enumerate(raw_links, start=1):
        if not isinstance(raw, dict):
            issues.append(
                {
                    "status": "failed",
                    "reason": "external_link_item_not_object",
                    "link_index": link_index,
                    "raw_evidence": raw,
                    "suggested_generic_next_step": "Each external-link manifest entry must be an object.",
                }
            )
            continue
        candidate_id = str(raw.get("candidate_id") or "").strip()
        if not candidate_id:
            issues.append(
                {
                    "status": "failed",
                    "reason": "external_link_item_missing_candidate_id",
                    "link_index": link_index,
                    "raw_evidence": raw,
                    "suggested_generic_next_step": "Add the DocSegmentation candidate_id to this manifest link.",
                }
            )
            continue
        item, item_issues = _external_link_item(raw, manifest_path=manifest_path, link_index=link_index)
        issues.extend(item_issues)
        if item:
            links_by_candidate.setdefault(candidate_id, []).append(item)

    links = [link for values in links_by_candidate.values() for link in values]
    summary.update(
        {
            "link_count": len(links),
            "candidate_count": len(links_by_candidate),
            "supported_link_count": sum(1 for link in links if _external_link_satisfies_reference(link)),
            "missing_external_document_count": sum(len(items) for items in missing_external_docs_by_candidate.values()),
            "missing_external_document_candidate_count": len(missing_external_docs_by_candidate),
            "issue_count": len(issues),
        }
    )
    return links_by_candidate, missing_external_docs_by_candidate, issues, summary


def _missing_external_documents_by_candidate(manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    raw_docs = manifest.get("missing_external_documents") if isinstance(manifest.get("missing_external_documents"), list) else []
    for index, raw_doc in enumerate(raw_docs, start=1):
        if not isinstance(raw_doc, dict):
            continue
        candidate_id = str(raw_doc.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        status = str(raw_doc.get("status") or "missing_external_document")
        if status != "missing_external_document":
            continue
        doc = {
            "candidate_id": candidate_id,
            "missing_document_index": index,
            "missing_document_id": raw_doc.get("missing_document_id") or f"missing_external_document_{index:04d}",
            "status": status,
            "document_title": raw_doc.get("document_title") or "",
            "document_type": raw_doc.get("document_type") or "",
            "referenced_date": raw_doc.get("referenced_date") or "",
            "reason": raw_doc.get("reason") or "External document was searched for or expected but not available as reviewed rendered evidence.",
            "search_evidence": raw_doc.get("search_evidence") or [],
            "accept_for_step4_5": raw_doc.get("accept_for_step4_5"),
            "nonblocking_for_step4_5": True,
            "policy": "Missing external document is explicitly marked and is nonblocking for Step 4/5; no missing-document text is supplied as supporting context.",
        }
        out.setdefault(candidate_id, []).append(doc)
    return out


def _external_link_item(raw: dict[str, Any], *, manifest_path: Path, link_index: int) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    candidate_id = str(raw.get("candidate_id") or "").strip()
    report_value = raw.get("external_evidence_report_path") or raw.get("external_extraction_report_path")
    if not report_value:
        return None, [
            {
                "candidate_id": candidate_id,
                "status": "failed",
                "reason": "external_link_item_missing_external_evidence_report_path",
                "link_index": link_index,
                "raw_evidence": raw,
                "suggested_generic_next_step": "Point the manifest link to an external evidence report that contains rendered page image paths.",
            }
        ]
    report_path = _resolve_path_text(report_value, base_dir=manifest_path.parent)
    report = Path(report_path)
    if not report.exists():
        return None, [
            {
                "candidate_id": candidate_id,
                "status": "failed",
                "reason": "external_evidence_report_not_found",
                "link_index": link_index,
                "external_evidence_report_path": report_path,
                "raw_evidence": "The manifest link points to an external evidence report path that does not exist.",
                "suggested_generic_next_step": "Run external-document evidence extraction first, or correct the manifest path.",
            }
        ]
    try:
        extraction = json.loads(report.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [
            {
                "candidate_id": candidate_id,
                "status": "failed",
                "reason": "external_evidence_report_json_invalid",
                "link_index": link_index,
                "external_evidence_report_path": report_path,
                "raw_evidence": f"JSON parse error: {exc}",
                "suggested_generic_next_step": "Fix or regenerate the external evidence report JSON.",
            }
        ]

    pages = [page for page in extraction.get("pages") or [] if isinstance(page, dict)]
    page_image_paths = _resolved_path_list([page.get("page_image_path") for page in pages], base_dir=report.parent)
    existing_page_image_paths = [path for path in page_image_paths if Path(path).exists()]
    page_text_paths = _resolved_path_list([page.get("page_text_path") for page in pages], base_dir=report.parent)
    visual_judgement = raw.get("visual_judgement") if isinstance(raw.get("visual_judgement"), dict) else {}
    judgement_status = str(visual_judgement.get("status") or "needs_visual_review")
    judgement_reason = str(
        visual_judgement.get("reason")
        or "External evidence was linked, but the link still needs visual review before it can resolve the reference."
    )
    has_supported_status = judgement_status in SUPPORTED_EXTERNAL_LINK_STATUSES
    link_status = judgement_status if has_supported_status and existing_page_image_paths else EXTERNAL_LINK_NEEDS_REVIEW_STATUS
    visual_evidence_status = "rendered_external_page_images_available" if existing_page_image_paths else "missing_rendered_external_page_images"
    if has_supported_status and not existing_page_image_paths:
        judgement_reason = f"{judgement_reason} Missing rendered external page image evidence, so this link cannot resolve the reference yet."

    return (
        {
            "candidate_id": candidate_id,
            "status": link_status,
            "accepted_as_ground_truth": False,
            "document_role": str(raw.get("document_role") or "external_document"),
            "external_evidence_report_path": report_path,
            "external_input_pdf_path": _resolve_path_text(extraction.get("resolved_input_pdf_path") or extraction.get("input_pdf_path"), base_dir=report.parent),
            "external_evidence_folder_path": _resolve_path_text(extraction.get("output_pdf_folder_path"), base_dir=report.parent),
            "external_page_count": (extraction.get("summary") or {}).get("page_count") or len(pages),
            "external_text_block_count": (extraction.get("summary") or {}).get("text_block_count"),
            "external_page_image_paths": page_image_paths,
            "external_existing_page_image_paths": existing_page_image_paths,
            "external_page_text_paths": page_text_paths,
            "external_text_preview": _preview("\n".join(str(page.get("plain_text") or "") for page in pages), limit=900),
            "root_topic_id": raw.get("root_topic_id") or "",
            "root_label_he": raw.get("root_label_he") or "",
            "child_topic_id": raw.get("child_topic_id") or "",
            "child_label_he": raw.get("child_label_he") or "",
            "topic_supporting_quote_he": raw.get("topic_supporting_quote_he") or "",
            "visual_evidence_status": visual_evidence_status,
            "visual_judgement": {
                "status": judgement_status,
                "reason": judgement_reason,
            },
        },
        issues,
    )


def _candidate_external_preflight(*, candidate: dict[str, Any], external_links: list[dict[str, Any]], missing_external_documents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    text = "\n".join(
        _dedupe_strings(
            [
                candidate.get("heading_text"),
                candidate.get("selected_text"),
                candidate.get("rtl_normalized_text"),
                candidate.get("raw_native_text"),
            ]
        )
    )
    references = _external_document_references(text)
    strong_references = [item for item in references if item.get("strength") == "strong"]
    supported_external_links = [link for link in external_links if _external_link_satisfies_reference(link)]
    needs_external_link = bool(strong_references) and not supported_external_links
    missing_external_documents = [item for item in missing_external_documents or [] if isinstance(item, dict)]
    references = _references_with_external_link_status(references, supported_external_links=supported_external_links)
    return {
        "candidate_id": candidate.get("candidate_id"),
        "page_range": candidate.get("page_range") or [],
        "heading_text": candidate.get("heading_text") or "",
        "external_document_references": references,
        "external_document_links": external_links,
        "missing_external_documents": missing_external_documents,
        "needs_external_document_link": needs_external_link,
        "semantic_preflight_status": _candidate_external_preflight_status(
            strong_references=strong_references,
            external_links=external_links,
            supported_external_links=supported_external_links,
            needs_external_link=needs_external_link,
            missing_external_documents=missing_external_documents,
        ),
        "semantic_preflight_reason": _candidate_external_preflight_reason(
            strong_references=strong_references,
            external_links=external_links,
            supported_external_links=supported_external_links,
            needs_external_link=needs_external_link,
            missing_external_documents=missing_external_documents,
        ),
        "accepted_as_ground_truth": False,
        "policy": "External-document text clues are diagnostic only. Reviewed external evidence may add context; explicitly marked missing external documents are nonblocking and are not used as supporting context.",
    }


def _candidate_external_preflight_status(
    *,
    strong_references: list[dict[str, Any]],
    external_links: list[dict[str, Any]],
    supported_external_links: list[dict[str, Any]],
    needs_external_link: bool,
    missing_external_documents: list[dict[str, Any]],
) -> str:
    if strong_references and needs_external_link and missing_external_documents:
        return "prepared_with_missing_external_documents_marked"
    if strong_references and needs_external_link and external_links:
        return EXTERNAL_LINK_NEEDS_REVIEW_STATUS
    if strong_references and needs_external_link:
        return "external_document_link_needed"
    if strong_references and supported_external_links:
        return "prepared_with_external_document_link"
    if external_links and not supported_external_links:
        return EXTERNAL_LINK_NEEDS_REVIEW_STATUS
    return "prepared_no_external_document_link_needed"


def _candidate_external_preflight_reason(
    *,
    strong_references: list[dict[str, Any]],
    external_links: list[dict[str, Any]],
    supported_external_links: list[dict[str, Any]],
    needs_external_link: bool,
    missing_external_documents: list[dict[str, Any]],
) -> str:
    if strong_references and needs_external_link and missing_external_documents:
        return "The candidate references external documents, but those documents are explicitly marked missing after search/review. The main protocol candidate can proceed without external-document context."
    if strong_references and needs_external_link and external_links:
        return "External evidence was provided, but it does not yet have a supported visual-review status and existing rendered page image evidence."
    if strong_references and needs_external_link:
        return "The candidate text has diagnostic attached/appendix-like clues, but no reviewed external-document link is available yet."
    if strong_references and supported_external_links:
        return "The candidate has a visually reviewed external-document evidence link for research use."
    if external_links and not supported_external_links:
        return "External-document links were provided as metadata, but none are accepted for downstream context yet."
    return "No strong external-document clue requiring a reviewed link was detected."


def _external_link_satisfies_reference(link: dict[str, Any]) -> bool:
    return str(link.get("status") or "") in SUPPORTED_EXTERNAL_LINK_STATUSES and bool(link.get("external_existing_page_image_paths"))


def _references_with_external_link_status(
    references: list[dict[str, Any]],
    *,
    supported_external_links: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not supported_external_links:
        return references
    out: list[dict[str, Any]] = []
    for reference in references:
        item = dict(reference)
        if item.get("strength") == "strong" and item.get("status") == "unresolved_external_document_reference":
            item["status"] = "resolved_by_external_document_link"
            item["resolved_by_external_document_link_count"] = len(supported_external_links)
            item["reason"] = "Diagnostic clue was resolved for research use by a visually reviewed external document link."
        out.append(item)
    return out


def _external_document_references(text: str) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for match in _iter_reference_matches(text, STRONG_EXTERNAL_REFERENCE_PATTERNS, strength="strong"):
        references.append(match)
    for match in _iter_reference_matches(text, WEAK_EXTERNAL_REFERENCE_PATTERNS, strength="weak"):
        if not any(_overlaps(match, existing) for existing in references):
            references.append(match)
    references.sort(key=lambda item: (0 if item.get("strength") == "strong" else 1, int(item.get("start_char") or 0)))
    return references[:8]


def _iter_reference_matches(text: str, patterns: list[re.Pattern[str]], *, strength: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            out.append(
                {
                    "strength": strength,
                    "status": "unresolved_external_document_reference" if strength == "strong" else "document_reference_mention",
                    "matched_text": match.group(0),
                    "start_char": match.start(),
                    "end_char": match.end(),
                    "snippet": _snippet(text, start=match.start(), end=match.end()),
                    "reason": "Diagnostic multilingual clue only; requires reviewed external document evidence before acceptance.",
                }
            )
    return out


def _overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return int(a.get("start_char") or 0) < int(b.get("end_char") or 0) and int(b.get("start_char") or 0) < int(a.get("end_char") or 0)


def _snippet(text: str, *, start: int, end: int, radius: int = 90) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return _compact(text[left:right])


def _attachment_contexts_from_external_links(*, unit_id: str, candidate: dict[str, Any], links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for link in links:
        if not isinstance(link, dict) or not _external_link_satisfies_reference(link):
            continue
        pdf_path = str(link.get("external_input_pdf_path") or "")
        report_path = str(link.get("external_evidence_report_path") or "")
        quote = _compact(link.get("topic_supporting_quote_he")) or _compact(link.get("external_text_preview")) or _compact(candidate.get("heading_text"))
        contexts.append(
            {
                "source": "reviewed_external_document_link",
                "source_candidate_id": candidate.get("candidate_id"),
                "structure_unit_id": unit_id,
                "semantic_unit_id": unit_id,
                "document_role": link.get("document_role") or "external_document",
                "accepted_as_ground_truth": False,
                "pdf_path": pdf_path,
                "file_stem": Path(pdf_path or report_path).stem,
                "external_evidence_report_path": report_path,
                "external_page_image_paths": link.get("external_existing_page_image_paths") or [],
                "external_page_text_paths": link.get("external_page_text_paths") or [],
                "root_topic_id": link.get("root_topic_id") or "",
                "root_label_he": link.get("root_label_he") or "",
                "child_topic_id": link.get("child_topic_id") or "",
                "child_label_he": link.get("child_label_he") or "",
                "topic_supporting_quote_he": quote[:900],
                "visual_judgement": link.get("visual_judgement") or {},
                "review_policy": "Use only because this link has supported visual judgement and rendered external page image evidence.",
            }
        )
    return contexts


def _resolve_path_text(value: Any, *, base_dir: Path) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())


def _resolved_path_list(values: list[Any], *, base_dir: Path) -> list[str]:
    return _dedupe_strings([_resolve_path_text(value, base_dir=base_dir) for value in values if str(value or "").strip()])


def _preview(value: Any, *, limit: int = 900) -> str:
    text = _compact(value)
    return text[: max(0, int(limit))]


def _candidate_review(*, candidate: dict[str, Any], manual_payload: dict[str, Any], agenda_report: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id") or "")
    manual = (manual_payload.get("candidate_judgements") or {}).get(candidate_id) if isinstance(manual_payload.get("candidate_judgements"), dict) else None
    if isinstance(manual, dict):
        final_status = str(manual.get("final_status") or "needs_review")
        belongs_together = manual.get("belongs_together")
        why = str(manual.get("why") or manual.get("reason") or "")
        visual = str(manual.get("visual_judgement") or "Manual visual judgement did not include a visual_judgement field.")
        semantic = str(manual.get("semantic_judgement") or "Manual visual judgement did not include a semantic_judgement field.")
        source = "manual_visual_judgement_json"
    else:
        final_status = "needs_review_missing_manual_judgement"
        belongs_together = None
        why = "No independent manual visual judgement has been recorded for this candidate. It is not accepted for downstream use until the rendered crop and original page are judged."
        visual = "Final judgement: needs_review, not accepted. Rendered candidate crop images must be checked against the original page images before downstream use."
        semantic_reason = str(((candidate.get("semantic_judgement") if isinstance(candidate.get("semantic_judgement"), dict) else {}) or {}).get("reason") or "")
        semantic = "Final judgement: needs_review, not accepted. Candidate generation is diagnostic only; no independent semantic judgement has accepted this as one coherent topic."
        if semantic_reason:
            semantic = f"{semantic} Machine semantic note: {semantic_reason}"
        source = "adapter_default_needs_review"
    return {
        "candidate_id": candidate_id,
        "final_status": final_status,
        "belongs_together": belongs_together,
        "why": why,
        "visual_judgement": visual,
        "semantic_judgement": semantic,
        "judgement_source": source,
        "pages": _positive_int_list(candidate.get("pages") or []),
        "page_range": candidate.get("page_range") or [],
        "group_crops": candidate.get("group_crops") or [],
        "page_image_paths": _page_image_paths_for_candidate(candidate=candidate, agenda_report=agenda_report),
        "heading_text": candidate.get("heading_text") or "",
        "candidate_metadata_path": candidate.get("candidate_metadata_path") or "",
        "source_candidate": candidate,
    }


def _manual_skipped_page_judgements(manual_payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    skipped = manual_payload.get("skipped_page_judgements") if isinstance(manual_payload.get("skipped_page_judgements"), dict) else {}
    for raw_page, page_review in skipped.items():
        if not isinstance(page_review, dict):
            continue
        page = _positive_int(raw_page)
        if page is not None:
            out[page] = page_review
    return out


def _skipped_pages_by_page(agenda_report: dict[str, Any]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for row in agenda_report.get("skipped_pages") or []:
        if not isinstance(row, dict):
            continue
        page = _positive_int(row.get("page"))
        if page is not None:
            out[page] = row
    return out


def _missing_middle_pages(*, candidate: dict[str, Any], skipped_pages: dict[int, dict[str, Any]]) -> list[int]:
    pages = _positive_int_list(candidate.get("pages") or [])
    if len(pages) < 2:
        return []
    expected = set(range(min(pages), max(pages) + 1))
    present = set(pages)
    missing = sorted(expected - present)
    return [page for page in missing if page not in skipped_pages]


def _semantic_windows_from_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    windows = []
    for unit in units:
        windows.append(
            {
                "window_id": unit.get("source_window_id"),
                "page": unit.get("page"),
                "source_region_ids": unit.get("source_region_ids") or [],
                "source_block_ids": unit.get("source_block_ids") or [],
                "raw_text": unit.get("raw_text") or "",
            }
        )
    return windows


def _candidate_source_region_ids(candidate: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for crop in candidate.get("group_crops") or []:
        if not isinstance(crop, dict):
            continue
        crop_path = str(crop.get("group_crop_image_path") or "").strip()
        if crop_path:
            values.append(f"docseg_crop:{crop_path}")
    return _dedupe_strings(values)


def _candidate_heading_raw_text(candidate: dict[str, Any]) -> str:
    heading_ids = set(_string_list(candidate.get("heading_block_ids")))
    if heading_ids:
        for block in candidate.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            if str(block.get("block_id") or "") in heading_ids:
                return _compact(block.get("raw_native_text"))
    blocks = [block for block in candidate.get("blocks") or [] if isinstance(block, dict)]
    if blocks:
        return _compact(blocks[0].get("raw_native_text"))
    return _compact(candidate.get("heading_text"))


def _page_image_paths_for_candidate(*, candidate: dict[str, Any], agenda_report: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for block in candidate.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        value = str(block.get("page_image_path") or "").strip()
        if value:
            paths.append(value)
    evidence_folder = Path(str(agenda_report.get("evidence_folder_path") or "")) if agenda_report.get("evidence_folder_path") else None
    if evidence_folder:
        for page in _positive_int_list(candidate.get("pages") or []):
            paths.append(str(evidence_folder / "pages" / f"page_{page:03d}.png"))
    return _dedupe_strings(paths)


def _page_range(candidate: dict[str, Any], *, pages: list[int]) -> list[int]:
    values = _positive_int_list(candidate.get("page_range") or [])
    if len(values) >= 2:
        return [min(values), max(values)]
    if pages:
        return [min(pages), max(pages)]
    return []


def _unit_id(*, candidate: dict[str, Any], ordinal: int) -> str:
    candidate_id = re.sub(r"[^0-9A-Za-z_\-]+", "_", str(candidate.get("candidate_id") or f"candidate_{ordinal:04d}"))
    basis = "|".join([candidate_id, str(candidate.get("page_range") or ""), str(candidate.get("block_ids") or "")])
    return f"ds_{candidate_id}_{_short_hash(basis)}"


def _heading_number(heading_text: str) -> str | None:
    match = HEADING_NUMBER_RE.search(str(heading_text or ""))
    return match.group(1) if match else None


def _default_manual_path(agenda_path: Path) -> Path | None:
    candidate = agenda_path.parent / "manual_visual_judgements.json"
    return candidate if candidate.exists() else None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_int_list(values: Any) -> list[int]:
    out: list[int] = []
    for value in values if isinstance(values, list) else []:
        parsed = _positive_int(value)
        if parsed is not None:
            out.append(parsed)
    return sorted(set(out))


def _string_list(values: Any) -> list[str]:
    return _dedupe_strings([str(value) for value in values if str(value).strip()]) if isinstance(values, list) else []


def _dedupe_strings(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def _default_pipeline_root(*, output_dir: Path, protocol_report_dir: Path) -> Path:
    if output_dir.name == "adapter":
        return output_dir.parent
    if (protocol_report_dir / "adapter").exists():
        return protocol_report_dir
    return output_dir


def _load_downstream_summary(*, pipeline_root: Path) -> dict[str, Any]:
    paths = {
        "entity_validation": pipeline_root / "step3_5_entity_grounding" / "validation_report.json",
        "step4_validation": pipeline_root / "step4_v4_global_topic_assignment" / "validation_report.json",
        "step4_assignments": pipeline_root / "step4_v4_global_topic_assignment" / "topic_assignments.json",
        "step45_validation": pipeline_root / "step4_5_v4_topic_validation" / "validation_report.json",
        "canonical_topics": pipeline_root / "step4_5_v4_topic_validation" / "canonical_topics.json",
        "step5_validation": pipeline_root / "step5_v4_retrieval_chunks" / "validation_report.json",
        "retrieval_chunks": pipeline_root / "step5_v4_retrieval_chunks" / "retrieval_chunks.json",
    }
    loaded = {key: _load_json_if_exists(path) for key, path in paths.items()}
    assignments = loaded.get("canonical_topics", {}).get("canonical_assignments") or loaded.get("step4_assignments", {}).get("topic_assignments") or []
    chunks = loaded.get("retrieval_chunks", {}).get("retrieval_chunks") or []
    return {
        "pipeline_root": str(pipeline_root),
        "paths": {key: str(path) for key, path in paths.items() if path.exists()},
        "validations": {key: loaded.get(key) or {} for key in ("entity_validation", "step4_validation", "step45_validation", "step5_validation")},
        "assignments_by_unit": {str(row.get("structure_unit_id") or row.get("semantic_unit_id") or ""): row for row in assignments if isinstance(row, dict)},
        "chunks_by_unit": {str(row.get("structure_unit_id") or row.get("semantic_unit_id") or ""): row for row in chunks if isinstance(row, dict)},
    }


def _load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _html_report(*, result: dict[str, Any], output_dir: Path, max_preview_chars: int, downstream: dict[str, Any] | None = None) -> str:
    validation = result["validation"]
    reviews = result["candidate_reviews"]
    navigation_candidates = result.get("navigation_candidates") if isinstance(result.get("navigation_candidates"), list) else []
    grouped_units = result["grouped_payload"].get("structure_units") or []
    unit_by_candidate = {str(unit.get("docsegmentation_candidate_id") or ""): unit for unit in grouped_units}
    downstream = downstream or {}
    title = "Municipality Hybrid Protocol Review"
    original_pdf = _original_pdf_path(result)
    parts = [
        "<!doctype html>",
        "<html lang=\"en\">",
        "<head>",
        "<meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        f"<title>{_h(title)}</title>",
        _docsegmentation_css(),
        "</head>",
        "<body>",
        f"<h1>{_h(title)}</h1>",
        "<section class=\"summary\">",
        f"<p><strong>Adapter status:</strong> {_badge(str(validation.get('status') or 'unknown'))} {_h(validation.get('reason'))}</p>",
        f"<p><strong>Accepted for Municipality Step 4/5:</strong> {_h(validation.get('accept_for_next_step'))}</p>",
        f"<p><strong>Original document PDF:</strong> {_pdf_link(original_pdf, output_dir=output_dir)}</p>",
        f"<p><strong>Agenda report:</strong> <code>{_h(result.get('agenda_report_path'))}</code></p>",
        f"<p><strong>Manual judgement:</strong> <code>{_h(result.get('manual_path') or '')}</code></p>",
        f"<p><strong>Report rule:</strong> One protocol/PDF per HTML file. Native text only for DocSegmentation groups. Every accepted group must have independent visual evidence.</p>",
        _pdf_viewer(original_pdf, output_dir=output_dir),
        "</section>",
        _downstream_summary_html(downstream=downstream),
        _issue_html(title="Blocking Issues", issues=validation.get("blocking_issues") or []),
        _issue_html(title="Needs Review", issues=validation.get("review_issues") or []),
        _issue_html(title="Warnings", issues=validation.get("warnings") or []),
        "<h2>Candidate Groups</h2>",
    ]
    for review in reviews:
        candidate_id = str(review.get("candidate_id") or "")
        unit = unit_by_candidate.get(candidate_id, {})
        parts.append(
            _candidate_html(
                review=review,
                unit=unit,
                downstream=downstream,
                output_dir=output_dir,
                max_preview_chars=max_preview_chars,
            )
        )
    parts.append("<h2>Agenda Navigation Candidates</h2>")
    if navigation_candidates:
        parts.append(
            "<section class=\"summary\"><p><strong>Important:</strong> These list/index/navigation candidates are preserved as evidence, but they are not converted into Municipality Step 4/5 units.</p></section>"
        )
        for navigation_candidate in navigation_candidates:
            if isinstance(navigation_candidate, dict):
                parts.append(_navigation_candidate_html(candidate=navigation_candidate, output_dir=output_dir, max_preview_chars=max_preview_chars))
    else:
        parts.append("<p>No agenda navigation candidates were supplied in the agenda report.</p>")
    parts.extend(["</body>", "</html>"])
    return "\n".join(part for part in parts if part) + "\n"


def _status_class(status: str) -> str:
    if status in GOOD_CANDIDATE_STATUSES or status in {"ok", "accepted", "true"}:
        return "good"
    if status.startswith("failed") or status.startswith("needs_review") or status == "failed":
        return "bad"
    return "partial"


def _candidate_html(*, review: dict[str, Any], unit: dict[str, Any], downstream: dict[str, Any], output_dir: Path, max_preview_chars: int) -> str:
    status = str(review.get("final_status") or "needs_review")
    crops = [crop for crop in review.get("group_crops") or [] if isinstance(crop, dict)]
    unit_id = str(unit.get("structure_unit_id") or unit.get("semantic_unit_id") or "")
    assignment = (downstream.get("assignments_by_unit") or {}).get(unit_id) or {}
    chunk = (downstream.get("chunks_by_unit") or {}).get(unit_id) or {}
    parts = [
        f"<article class=\"candidate {_status_class(status)}\">",
        f"<h3>{_h(review.get('candidate_id'))}: {_badge(status)} {_h(review.get('heading_text'))}</h3>",
        "<div class=\"judgement-grid\">",
        f"<div><h4>Visual Judgement</h4><p>{_h(review.get('visual_judgement'))}</p></div>",
        f"<div><h4>Semantic Judgement</h4><p>{_h(review.get('semantic_judgement'))}</p></div>",
        f"<div><h4>Final Belongs-Together Judgement</h4><p>{_h(_belongs_text(review.get('belongs_together')))} {_h(review.get('why'))}</p></div>",
        "</div>",
        f"<p><strong>Pages:</strong> {_h(review.get('pages'))}</p>",
        f"<p><strong>Structure unit:</strong> <code>{_h(unit_id)}</code></p>",
        f"<p><strong>Machine status:</strong> {_h((unit.get('structure_evidence') or {}).get('source_candidate_status'))} - {_h((unit.get('structure_evidence') or {}).get('source_candidate_status_reason'))}</p>",
        f"<p><strong>Quality flags:</strong> {_h(unit.get('docsegmentation_quality_flags'))}</p>",
        _external_preflight_html(preflight=unit.get("candidate_semantic_preflight") or {}, output_dir=output_dir),
        _assignment_html(assignment=assignment, chunk=chunk),
        "<h4>Original Pages And Group Crops</h4>",
    ]
    for crop in crops:
        page_number = str(crop.get("page") or "")
        original_page = _original_page_for_crop(review=review, crop=crop)
        parts.append("<div class=\"image-pair\">")
        parts.append("<figure>")
        parts.append(f"<figcaption>Original PDF page {_h(page_number)}</figcaption>")
        parts.append(_image_tag(original_page, output_dir=output_dir, alt=f"Original page {page_number}"))
        parts.append("</figure>")
        parts.append("<figure>")
        parts.append(f"<figcaption>Group crop from page {_h(page_number)}</figcaption>")
        parts.append(_image_tag(crop.get("group_crop_image_path"), output_dir=output_dir, alt=f"Group crop page {page_number}"))
        parts.append("</figure>")
        parts.append("</div>")
    parts.extend(
        [
            "<details open>",
            "<summary>Cleaned Downstream Text</summary>",
            f"<pre dir=\"rtl\">{_h(str(unit.get('docsegmentation_selected_text') or '')[:max_preview_chars])}</pre>",
            "</details>",
            "<details>",
            "<summary>Raw Native Text</summary>",
            f"<pre dir=\"rtl\">{_h(str(unit.get('docsegmentation_raw_native_text') or unit.get('topic_group_raw_native_text') or '')[:max_preview_chars])}</pre>",
            "</details>",
            "</article>",
        ]
    )
    return "\n".join(parts)


def _navigation_candidate_html(*, candidate: dict[str, Any], output_dir: Path, max_preview_chars: int) -> str:
    status = str(candidate.get("status") or "navigation_list_candidate")
    crops = [crop for crop in candidate.get("navigation_crops") or [] if isinstance(crop, dict)]
    page_images = _page_image_paths_for_navigation_candidate(candidate)
    parts = [
        f"<article class=\"candidate partial\">",
        f"<h3>{_h(candidate.get('navigation_candidate_id'))}: {_badge(status)}</h3>",
        f"<p><strong>Reason:</strong> {_h(candidate.get('status_reason'))}</p>",
        f"<p><strong>Pages:</strong> {_h(candidate.get('pages'))}</p>",
        f"<p><strong>List-run features:</strong> <code>{_h(json.dumps(candidate.get('list_run_features') or {}, ensure_ascii=False))}</code></p>",
        f"<p><strong>Downstream policy:</strong> Saved as visual/navigation evidence only. Not converted into semantic or structure units.</p>",
        "<h4>Original Pages And Navigation Crops</h4>",
    ]
    for crop in crops:
        page_number = str(crop.get("page") or "")
        original_page = page_images.get(page_number) or page_images.get(int(page_number)) if page_number else ""
        parts.append("<div class=\"image-pair\">")
        parts.append("<figure>")
        parts.append(f"<figcaption>Original PDF page {_h(page_number)}</figcaption>")
        parts.append(_image_tag(original_page, output_dir=output_dir, alt=f"Original page {page_number}"))
        parts.append("</figure>")
        parts.append("<figure>")
        parts.append(f"<figcaption>Navigation crop from page {_h(page_number)}</figcaption>")
        parts.append(_image_tag(crop.get("navigation_crop_image_path"), output_dir=output_dir, alt=f"Navigation crop page {page_number}"))
        parts.append("</figure>")
        parts.append("</div>")
    parts.extend(
        [
            "<details open>",
            "<summary>Navigation Text</summary>",
            f"<pre dir=\"rtl\">{_h(str(candidate.get('selected_text') or '')[:max_preview_chars])}</pre>",
            "</details>",
            "<details>",
            "<summary>Raw Native Text</summary>",
            f"<pre dir=\"rtl\">{_h(str(candidate.get('raw_native_text') or '')[:max_preview_chars])}</pre>",
            "</details>",
            "</article>",
        ]
    )
    return "\n".join(parts)


def _page_image_paths_for_navigation_candidate(candidate: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for block in candidate.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        page = str(block.get("page") or "")
        image_path = str(block.get("page_image_path") or "")
        if page and image_path:
            out[page] = image_path
    return out


def _assignment_html(*, assignment: dict[str, Any], chunk: dict[str, Any]) -> str:
    if not assignment and not chunk:
        return ""
    return "\n".join(
        [
            "<div class=\"judgement-grid\">",
            "<div><h4>Municipality Topic Assignment</h4>"
            f"<p>{_h(assignment.get('topic_node_status') or 'not_run')} - {_h(assignment.get('root_topic_id'))} / {_h(assignment.get('root_label_he'))} / {_h(assignment.get('child_label_he'))}</p>"
            f"<p>{_h(assignment.get('topic_reject_reason'))}</p></div>",
            "<div><h4>Retrieval Chunk</h4>"
            f"<p>{_h(chunk.get('retrieval_artifact_id') or 'not_built')}</p>"
            f"<p>page={_h(chunk.get('page'))}, confidence={_h(chunk.get('topic_assignment_confidence'))}</p></div>",
            "</div>",
        ]
    )


def _external_preflight_html(*, preflight: dict[str, Any], output_dir: Path) -> str:
    references = [item for item in preflight.get("external_document_references") or [] if isinstance(item, dict)]
    links = [item for item in preflight.get("external_document_links") or [] if isinstance(item, dict)]
    if not references and not links:
        return ""
    parts = ["<details open>", "<summary>External Document Reference Check</summary>"]
    parts.append(
        "<p>"
        f"<strong>Status:</strong> {_badge(str(preflight.get('semantic_preflight_status') or 'unknown'))} "
        f"{_h(preflight.get('semantic_preflight_reason'))}"
        "</p>"
    )
    if references:
        parts.append("<h4>Diagnostic Text Clues</h4>")
        for reference in references:
            parts.append(
                "<pre>"
                + _h(json.dumps(reference, ensure_ascii=False, indent=2))
                + "</pre>"
            )
    if links:
        parts.append("<h4>Reviewed External Links</h4>")
        for link in links:
            parts.append("<article class=\"candidate partial\">")
            parts.append(f"<p><strong>Link status:</strong> {_badge(str(link.get('status') or 'unknown'))}</p>")
            parts.append(f"<p><strong>Evidence report:</strong> <code>{_h(link.get('external_evidence_report_path'))}</code></p>")
            parts.append(f"<p><strong>Visual judgement:</strong> {_h((link.get('visual_judgement') or {}).get('reason'))}</p>")
            for image_path in (link.get("external_existing_page_image_paths") or [])[:2]:
                parts.append(_image_tag(image_path, output_dir=output_dir, alt="External document rendered page evidence"))
            parts.append("</article>")
    parts.append("</details>")
    return "\n".join(parts)


def _downstream_summary_html(*, downstream: dict[str, Any]) -> str:
    validations = downstream.get("validations") if isinstance(downstream.get("validations"), dict) else {}
    if not validations:
        return ""
    cards = []
    labels = [
        ("entity_validation", "Step 3.5 Entity Grounding"),
        ("step4_validation", "Step 4 Topic Assignment"),
        ("step45_validation", "Step 4.5 Topic Validation"),
        ("step5_validation", "Step 5 Retrieval Chunks"),
    ]
    for key, label in labels:
        payload = validations.get(key) or {}
        if not payload:
            continue
        status = "ok" if payload.get("accept_for_next_step") else "needs_review"
        cards.append(
            "<div>"
            f"<h4>{_h(label)} {_badge(status)}</h4>"
            f"<pre>{_h(json.dumps(_compact_validation_summary(payload), ensure_ascii=False, indent=2))}</pre>"
            "</div>"
        )
    if not cards:
        return ""
    return "<section class=\"summary\"><h2>Municipality Downstream Summary</h2><div class=\"judgement-grid\">" + "".join(cards) + "</div></section>"


def _compact_validation_summary(payload: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "accept_for_next_step",
        "candidate_count",
        "accepted_entity_count",
        "unanchored_accepted_count",
        "assignment_count",
        "status_counts",
        "model_error_count",
        "non_blocking_review_count",
        "invalid_assignment_count",
        "critical_tree_audit_warning_count",
        "chunk_count",
        "skipped_count",
        "blocked_issue_count",
        "missing_assignment_count",
    ]
    return {key: payload.get(key) for key in keys if key in payload}


def _issue_html(*, title: str, issues: list[dict[str, Any]]) -> str:
    if not issues:
        return ""
    rows = [f"<h2>{_h(title)}</h2>"]
    for issue in issues:
        rows.append("<article class=\"candidate partial\">")
        rows.append(f"<pre>{_h(json.dumps(issue, ensure_ascii=False, indent=2))}</pre>")
        rows.append("</article>")
    return "\n".join(rows)


def _original_page_for_crop(*, review: dict[str, Any], crop: dict[str, Any]) -> str:
    page = _positive_int(crop.get("page"))
    paths = review.get("page_image_paths") or []
    if isinstance(paths, dict):
        return str(paths.get(str(page)) or paths.get(page) or "")
    for path in paths if isinstance(paths, list) else []:
        text = str(path or "")
        if page is not None and f"page_{page:03d}" in text:
            return text
    return str(paths[0]) if isinstance(paths, list) and paths else ""


def _original_pdf_path(result: dict[str, Any]) -> str:
    agenda_path = Path(str(result.get("agenda_report_path") or "")) if result.get("agenda_report_path") else None
    if agenda_path:
        review_pdf = agenda_path.parent / "visual_review" / "original_document.pdf"
        if review_pdf.exists():
            return str(review_pdf)
    agenda_report = result.get("agenda_report") if isinstance(result.get("agenda_report"), dict) else {}
    return str(agenda_report.get("input_pdf_path") or "")


def _image_tag(path: Any, *, output_dir: Path, alt: str) -> str:
    if not path:
        return "<p class=\"missing\">Image path missing.</p>"
    return f"<img src=\"{_h(_rel(path, output_dir))}\" alt=\"{_h(alt)}\">"


def _pdf_link(pdf_path: str, *, output_dir: Path) -> str:
    if not pdf_path:
        return "<span class=\"missing\">PDF path missing.</span>"
    return f"<a href=\"{_h(_rel(pdf_path, output_dir))}\">Open original document</a> <code>{_h(pdf_path)}</code>"


def _pdf_viewer(pdf_path: str, *, output_dir: Path) -> str:
    if not pdf_path:
        return ""
    return "\n".join(
        [
            "<details>",
            "<summary>Original PDF viewer</summary>",
            f"<iframe class=\"pdf-viewer\" src=\"{_h(_rel(pdf_path, output_dir))}\"></iframe>",
            "</details>",
        ]
    )


def _rel(path: Any, output_dir: Path) -> str:
    text = str(path or "")
    if not text:
        return ""
    try:
        return os.path.relpath(text, start=str(output_dir)).replace(os.sep, "/")
    except ValueError:
        return Path(text).as_uri() if Path(text).is_absolute() else text


def _badge(status: str) -> str:
    return f"<span class=\"badge {_status_class(status)}\">{_h(status)}</span>"


def _belongs_text(value: Any) -> str:
    if value is True:
        return "Success: yes."
    if value is False:
        return "Fail: no."
    return "Needs review. Not accepted for downstream use."


def _docsegmentation_css() -> str:
    return """
<style>
:root { color-scheme: light; --bg: #f7f3ed; --card: #fffaf2; --ink: #211b16; --muted: #6c5c4d; --line: #dfd0bf; --good: #0f7b4f; --partial: #a06000; --bad: #b3261e; }
body { margin: 0; padding: 28px; background: var(--bg); color: var(--ink); font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
h1 { font-size: 34px; margin: 0 0 18px; }
h2 { margin-top: 34px; border-bottom: 2px solid var(--line); padding-bottom: 6px; }
h3 { margin-top: 0; font-size: 22px; }
code { background: #f0e4d5; padding: 2px 5px; border-radius: 5px; word-break: break-all; }
.summary, .candidate { background: var(--card); border: 1px solid var(--line); border-radius: 18px; padding: 18px; margin: 18px 0; box-shadow: 0 8px 20px rgba(60, 40, 20, 0.06); }
.candidate.good { border-left: 8px solid var(--good); }
.candidate.partial { border-left: 8px solid var(--partial); }
.candidate.bad { border-left: 8px solid var(--bad); }
.badge { display: inline-block; padding: 3px 9px; border-radius: 999px; color: white; font-size: 13px; font-weight: 700; }
.badge.good { background: var(--good); }
.badge.partial { background: var(--partial); }
.badge.bad { background: var(--bad); }
.judgement-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin: 14px 0; }
.judgement-grid > div { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 12px; }
.judgement-grid h4 { margin: 0 0 6px; }
.image-pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; margin: 14px 0 24px; }
figure { margin: 0; }
figcaption { color: var(--muted); font-weight: 700; margin-bottom: 6px; }
img { max-width: 100%; height: auto; border: 1px solid var(--line); border-radius: 10px; background: white; }
iframe.pdf-viewer { width: 100%; height: 720px; border: 1px solid var(--line); border-radius: 12px; background: white; }
pre { white-space: pre-wrap; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px; overflow-x: auto; font-size: 15px; }
details { margin-top: 12px; }
summary { cursor: pointer; font-weight: 700; }
.missing { color: var(--bad); font-weight: 700; }
</style>
"""


def _h(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _audit_markdown(*, result: dict[str, Any]) -> str:
    validation = result["validation"]
    lines = [
        "# DocSegmentation Adapter Audit",
        "",
        f"Status: {validation.get('status')}",
        f"Reason: {validation.get('reason')}",
        f"Accept for next step: {validation.get('accept_for_next_step')}",
        f"Agenda report: {result.get('agenda_report_path')}",
        f"Manual judgement: {result.get('manual_path') or ''}",
        f"External link manifest: {result.get('external_link_manifest_path') or ''}",
        f"Reviewed external attachment contexts: {len(result.get('reviewed_external_attachment_contexts') or [])}",
        "",
        "## Candidate Reviews",
    ]
    for review in result.get("candidate_reviews") or []:
        lines.extend(
            [
                "",
                f"### {review.get('candidate_id')}",
                f"Status: {review.get('final_status')}",
                f"Pages: {review.get('pages')}",
                f"Heading: {review.get('heading_text')}",
                f"Visual judgement: {review.get('visual_judgement')}",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
