from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import shutil
from typing import Any, Callable
import urllib.error
import urllib.request

try:
    import fitz
except ImportError as exc:  # pragma: no cover - exercised only in broken environments.
    raise SystemExit("PyMuPDF is required. Install project dependencies first.") from exc

from municipality.docsegmentation_native.display_text_cleanup import cleanup_display_text, cleanup_display_text_record
from municipality.docsegmentation_native.pdf_bbox_evidence import _abs, _with_path_validation


SCHEMA_VERSION = "municipality_internal_topic_split_v1"
DEFAULT_OUTPUT_SUBDIR = "internal_topic_splits"
DEFAULT_DICTA_MODEL = "dictaLM"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

LETTER_RE = re.compile(r"[A-Za-z\u0590-\u05FF]")
LEADING_OUTLINE_RE = re.compile(r"^\s*[(:.\-]*([0-9]{1,4}(?:[./][0-9]{1,4}){0,3})\s*[.)'\"״׳:]*\s+\S")
BODY_PUNCTUATION_RE = re.compile(r"[.!?;:]|[\u05C3]")
SPEAKER_TURN_RE = re.compile(r"^\s*(?:מר|גב\s*(?:'|׳)?|ד\s*[\"״']?\s*ר|עו\s*[\"״']?\s*ד|ראש העיר(?:ייה)?|מנכ\s*[\"״']?\s*ל|תשובת ראש העירייה)[^:：]{0,70}[:：]")
VOTE_OR_RESULT_RE = re.compile(r"(?:\bvote\b|\bfor\b|\bagainst\b|\babstain|הצבעה|הצביע|ההצעה עברה|בעד|נגד|נמנע|נמנעים|הוחלט|החלטה)", re.IGNORECASE)
DECISION_TABLE_RE = re.compile(r"(?:נושא ההחלטה|תוכן ההחלטה|מכותבים לפעולה|תחילת תוקף|משימה\s+פעילות|אחראי\s+תאריך\s+יעד)")
DATE_LIKE_RE = re.compile(r"(?<!\d)\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?(?!\d)")
SEMANTIC_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
FOOTER_ARTIFACT_RE = re.compile(r"(?:https?://|www\.|[A-Za-z]:\s*\\|\\\s*Users\s*\\|\.doc\b|טלפון|פקס)", re.IGNORECASE)
UNSAFE_INTERNAL_SPLIT_PARENT_STATUSES = {"needs_review_possible_list_body_merge", "needs_review_navigation_list_only"}

SemanticClient = Callable[..., dict[str, Any]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Split broad DocSegmentation agenda candidates into internal topic candidates using visual markers first and optional Dicta semantic boundaries."
    )
    parser.add_argument("--agenda-candidate-report-json", required=True)
    parser.add_argument("--manual-visual-judgement-json", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--run-name", default="")
    parser.add_argument("--parent-candidate-id", action="append", default=[], help="Optional parent candidate id; may be repeated.")
    parser.add_argument("--max-parents", type=int, default=0)
    parser.add_argument("--min-child-body-chars", type=int, default=120)
    parser.add_argument("--broad-page-threshold", type=int, default=3)
    parser.add_argument("--broad-char-threshold", type=int, default=3500)
    parser.add_argument("--render-dpi", type=int, default=180)
    parser.add_argument("--crop-margin-points", type=float, default=8.0)
    parser.add_argument("--run-dicta", action="store_true", help="Call local Dicta through Ollama for parents without visual-marker splits.")
    parser.add_argument("--dicta-model", default=DEFAULT_DICTA_MODEL)
    parser.add_argument("--ollama-base-url", default=DEFAULT_OLLAMA_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=480.0)
    args = parser.parse_args(argv)

    result = run_internal_topic_split(
        agenda_report_path=Path(args.agenda_candidate_report_json).expanduser().resolve(),
        manual_judgement_path=Path(args.manual_visual_judgement_json).expanduser().resolve() if str(args.manual_visual_judgement_json).strip() else None,
        output_dir=Path(args.output_dir).expanduser().resolve() if str(args.output_dir).strip() else None,
        run_name=str(args.run_name),
        parent_candidate_ids=[str(item) for item in args.parent_candidate_id or []],
        max_parents=max(0, int(args.max_parents)),
        min_child_body_chars=max(1, int(args.min_child_body_chars)),
        broad_page_threshold=max(1, int(args.broad_page_threshold)),
        broad_char_threshold=max(1, int(args.broad_char_threshold)),
        render_dpi=max(72, int(args.render_dpi)),
        crop_margin_points=max(0.0, float(args.crop_margin_points)),
        run_dicta=bool(args.run_dicta),
        dicta_model=str(args.dicta_model),
        ollama_base_url=str(args.ollama_base_url).rstrip("/"),
        timeout_seconds=max(1.0, float(args.timeout_seconds)),
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "reason": result["status_reason"],
                "internal_topic_split_json_path": result["internal_topic_split_json_path"],
                "html_report_path": result["html_report_path"],
                "parent_count": result["summary"]["parent_count"],
                "parent_with_child_split_count": result["summary"]["parent_with_child_split_count"],
                "child_topic_count": result["summary"]["child_topic_count"],
                "visual_boundary_count": result["summary"]["visual_boundary_count"],
                "semantic_only_boundary_count": result["summary"]["semantic_only_boundary_count"],
                "needs_review_parent_count": result["summary"]["needs_review_parent_count"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if result["status"] != "ok":
        print(json.dumps(result["quality_judgement"], ensure_ascii=False), flush=True)
    return 0 if result["status"] in {"ok", "needs_review", "partial"} else 2


def run_internal_topic_split(
    *,
    agenda_report_path: Path,
    manual_judgement_path: Path | None = None,
    output_dir: Path | None = None,
    run_name: str = "",
    parent_candidate_ids: list[str] | None = None,
    max_parents: int = 0,
    min_child_body_chars: int = 120,
    broad_page_threshold: int = 3,
    broad_char_threshold: int = 3500,
    render_dpi: int = 180,
    crop_margin_points: float = 8.0,
    run_dicta: bool = False,
    dicta_model: str = DEFAULT_DICTA_MODEL,
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout_seconds: float = 480.0,
    semantic_client: SemanticClient | None = None,
) -> dict[str, Any]:
    agenda_report_path = agenda_report_path.expanduser().resolve()
    if not agenda_report_path.exists():
        raise FileNotFoundError(f"Agenda candidate report not found: {agenda_report_path}")
    report = _load_json(agenda_report_path)
    manual_path = _resolve_manual_path(agenda_report_path=agenda_report_path, explicit=manual_judgement_path)
    manual_payload = _load_json(manual_path) if manual_path and manual_path.exists() else {}
    output_dir = _output_dir(agenda_report_path=agenda_report_path, output_dir=output_dir, run_name=run_name)
    child_crops_dir = output_dir / "child_crops"
    raw_semantic_dir = output_dir / "raw_semantic_model_responses"
    child_crops_dir.mkdir(parents=True, exist_ok=True)
    if run_dicta or semantic_client is not None:
        raw_semantic_dir.mkdir(parents=True, exist_ok=True)
    split_path = output_dir / "internal_topic_split_report.json"
    html_path = output_dir / "internal_topic_split_review.html"

    original_pdf = _copy_original_pdf_for_report(report=report, output_dir=output_dir)
    input_pdf_path = Path(str(report.get("input_pdf_path") or "")).expanduser()
    candidates = [candidate for candidate in report.get("candidates") or [] if isinstance(candidate, dict)]
    selected, excluded = _select_parent_candidates(
        candidates=candidates,
        manual_payload=manual_payload,
        parent_candidate_ids=parent_candidate_ids or [],
        max_parents=max_parents,
        broad_page_threshold=broad_page_threshold,
        broad_char_threshold=broad_char_threshold,
    )

    parent_results = []
    for candidate in selected:
        parent_results.append(
            _split_parent_candidate(
                candidate=candidate,
                input_pdf_path=input_pdf_path,
                child_crops_dir=child_crops_dir,
                raw_semantic_dir=raw_semantic_dir,
                min_child_body_chars=min_child_body_chars,
                render_dpi=render_dpi,
                crop_margin_points=crop_margin_points,
                run_dicta=run_dicta,
                dicta_model=dicta_model,
                ollama_base_url=ollama_base_url.rstrip("/"),
                timeout_seconds=timeout_seconds,
                semantic_client=semantic_client,
            )
        )

    child_topics = [child for parent in parent_results for child in parent.get("child_topics") or []]
    boundary_candidates = [boundary for parent in parent_results for boundary in parent.get("boundary_candidates") or []]
    parents_with_split = [parent for parent in parent_results if len(parent.get("child_topics") or []) > 1]
    semantic_errors = [review for parent in parent_results for review in parent.get("semantic_model_reviews") or [] if str(review.get("status") or "") in {"model_error", "model_output_parse_failed"}]
    status = "needs_review" if parent_results else "failed"
    if semantic_errors:
        status = "partial"
    status_reason = (
        "Internal topic split predictions were produced. They are not ground truth; every child boundary needs visual and semantic review."
        if parent_results and not semantic_errors
        else "Internal topic split predictions are partial because one or more semantic model calls failed or did not parse."
        if parent_results
        else "No parent candidates were selected for internal topic splitting."
    )

    payload = _with_path_validation(
        {
            "step": "municipality_internal_topic_split",
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "status_reason": status_reason,
            "accepted_as_ground_truth": False,
            "research_use": "visual_marker_and_dicta_semantic_internal_topic_split_review",
            "agenda_candidate_report_path": _abs(agenda_report_path),
            "manual_judgement_path": _abs(manual_path) if manual_path else "",
            "input_pdf_path": report.get("input_pdf_path") or "",
            "original_document_pdf_path": original_pdf.get("original_document_pdf_path") or "",
            "output_dir_path": _abs(output_dir),
            "internal_topic_split_json_path": _abs(split_path),
            "html_report_path": _abs(html_path),
            "child_crops_dir_path": _abs(child_crops_dir),
            "raw_semantic_model_responses_dir_path": _abs(raw_semantic_dir) if raw_semantic_dir.exists() else "",
            "split_policy": {
                "visual_markers_first": True,
                "semantic_model": dicta_model,
                "semantic_model_enabled": bool(run_dicta or semantic_client is not None),
                "semantic_only_boundaries_allowed": True,
                "semantic_only_boundaries_need_visual_review": True,
                "models_are_not_ground_truth": True,
                "parent_groups_are_fixed_containers": True,
                "downstream_policy": "A parent with child_topics is a container. Use reviewed child topics downstream, not the broad parent as one final semantic unit.",
            },
            "summary": {
                "source_candidate_count": len(candidates),
                "parent_count": len(parent_results),
                "excluded_parent_count": len(excluded),
                "parent_with_child_split_count": len(parents_with_split),
                "child_topic_count": len(child_topics),
                "boundary_candidate_count": len(boundary_candidates),
                "visual_boundary_count": sum(1 for boundary in boundary_candidates if boundary.get("boundary_source") == "visual_marker"),
                "semantic_only_boundary_count": sum(1 for boundary in boundary_candidates if boundary.get("boundary_source") == "semantic_only"),
                "needs_review_parent_count": sum(1 for parent in parent_results if parent.get("status") != "parent_preserved_no_internal_split"),
                "child_status_counts": dict(Counter(str(child.get("status") or "") for child in child_topics)),
                "semantic_model_error_count": len(semantic_errors),
            },
            "excluded_parent_candidates": excluded,
            "parent_topic_splits": parent_results,
            "quality_judgement": {
                "status": status,
                "reason": status_reason,
                "human_judgement_required": True,
                "raw_evidence": [
                    {
                        "parent_candidate_id": parent.get("parent_candidate_id"),
                        "status": parent.get("status"),
                        "heading_text": parent.get("heading_text"),
                        "child_topic_count": len(parent.get("child_topics") or []),
                        "boundary_previews": [boundary.get("candidate_text") for boundary in (parent.get("boundary_candidates") or [])[:6]],
                    }
                    for parent in parent_results[:12]
                ],
                "suggested_generic_next_step": "Open the HTML report and compare child crops with the original PDF. Accept child topics only when visual evidence or semantic-only evidence is independently judged correct.",
            },
        }
    )
    split_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_html_report(payload=payload, output_dir=output_dir, original_pdf=original_pdf), encoding="utf-8")
    return payload


def _select_parent_candidates(
    *,
    candidates: list[dict[str, Any]],
    manual_payload: dict[str, Any],
    parent_candidate_ids: list[str],
    max_parents: int,
    broad_page_threshold: int,
    broad_char_threshold: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    requested = {str(item) for item in parent_candidate_ids if str(item).strip()}
    manual = manual_payload.get("candidate_judgements") if isinstance(manual_payload.get("candidate_judgements"), dict) else {}
    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        review = manual.get(candidate_id) if isinstance(manual.get(candidate_id), dict) else {}
        accepted_parent = review.get("final_status") == "good_research_candidate" and review.get("belongs_together") is True
        broad = _looks_like_broad_parent_candidate(candidate, broad_page_threshold=broad_page_threshold, broad_char_threshold=broad_char_threshold)
        explicit = candidate_id in requested
        if requested and not explicit:
            excluded.append(
                {
                    "candidate_id": candidate_id,
                    "heading_text": candidate.get("heading_text") or "",
                    "page_range": candidate.get("page_range") or [],
                    "accepted_parent": accepted_parent,
                    "broad_parent_shape": broad,
                    "exclude_reason": "not_in_requested_parent_candidate_ids",
                }
            )
            continue
        if explicit or (accepted_parent and broad):
            selected.append(candidate)
            if max_parents > 0 and len(selected) >= max_parents:
                break
            continue
        excluded.append(
            {
                "candidate_id": candidate_id,
                "heading_text": candidate.get("heading_text") or "",
                "page_range": candidate.get("page_range") or [],
                "accepted_parent": accepted_parent,
                "broad_parent_shape": broad,
                "exclude_reason": "not_requested_and_not_broad_accepted_parent",
            }
        )
    return selected, excluded


def _looks_like_broad_parent_candidate(candidate: dict[str, Any], *, broad_page_threshold: int, broad_char_threshold: int) -> bool:
    pages = candidate.get("pages") if isinstance(candidate.get("pages"), list) else []
    page_count = len({int(page) for page in pages if isinstance(page, int) or str(page).isdigit()})
    char_count = len(str(candidate.get("selected_text") or candidate.get("raw_native_text") or ""))
    return page_count >= broad_page_threshold or char_count >= broad_char_threshold


def _split_parent_candidate(
    *,
    candidate: dict[str, Any],
    input_pdf_path: Path,
    child_crops_dir: Path,
    raw_semantic_dir: Path,
    min_child_body_chars: int,
    render_dpi: int,
    crop_margin_points: float,
    run_dicta: bool,
    dicta_model: str,
    ollama_base_url: str,
    timeout_seconds: float,
    semantic_client: SemanticClient | None,
) -> dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id") or "")
    blocks = [block for block in candidate.get("blocks") or [] if isinstance(block, dict)]
    spans = [_span_from_block(block=block, span_index=index) for index, block in enumerate(blocks, start=1)]
    blocked_reason = _internal_split_blocking_parent_reason(candidate)
    visual_boundaries = [] if blocked_reason else _visual_marker_boundaries(parent_candidate_id=candidate_id, spans=spans, min_child_body_chars=min_child_body_chars)
    if len(visual_boundaries) < 2:
        visual_boundaries = []
    semantic_reviews: list[dict[str, Any]] = []
    semantic_boundaries: list[dict[str, Any]] = []
    if not blocked_reason and not visual_boundaries and (run_dicta or semantic_client is not None) and len(spans) >= 2:
        semantic_review = _semantic_only_boundaries(
            candidate=candidate,
            spans=spans,
            raw_semantic_dir=raw_semantic_dir,
            model=dicta_model,
            ollama_base_url=ollama_base_url,
            timeout_seconds=timeout_seconds,
            semantic_client=semantic_client,
        )
        semantic_reviews.append(semantic_review)
        semantic_boundaries = semantic_review.get("boundary_candidates") if isinstance(semantic_review.get("boundary_candidates"), list) else []
        if len(semantic_boundaries) < 2:
            semantic_boundaries = []
    boundaries = visual_boundaries or semantic_boundaries
    parent_context_spans = _leading_parent_context_spans(spans=spans, boundaries=boundaries)
    child_groups = _child_groups_from_boundaries(spans=spans, boundaries=boundaries)
    parent_dir = child_crops_dir / candidate_id
    parent_dir.mkdir(parents=True, exist_ok=True)
    child_topics = []
    for child_index, group in enumerate(child_groups, start=1):
        child = _child_topic(parent_candidate_id=candidate_id, child_index=child_index, spans=group, total_children=len(child_groups), boundaries=boundaries)
        child["child_crops"] = _render_child_crops(child=child, spans=group, input_pdf_path=input_pdf_path, output_dir=parent_dir, render_dpi=render_dpi, crop_margin_points=crop_margin_points)
        child_topics.append(child)
    if len(child_topics) <= 1:
        status = "parent_preserved_no_internal_split"
        reason = blocked_reason or "No visual marker split was found, and no semantic-only split was produced."
    else:
        status = "needs_visual_semantic_review"
        reason = "Internal child topics were predicted; parent must be treated as a container until child boundaries are reviewed."
    return {
        "parent_candidate_id": candidate_id,
        "status": status,
        "status_reason": reason,
        "accepted_as_ground_truth": False,
        "heading_text": candidate.get("heading_text") or "",
        "page_range": candidate.get("page_range") or [],
        "pages": candidate.get("pages") or [],
        "selected_char_count": len(str(candidate.get("selected_text") or "")),
        "parent_group_crops": candidate.get("group_crops") or [],
        "parent_context_spans": [_public_span(span) for span in parent_context_spans],
        "parent_context_text": "\n\n".join(str(span.get("text") or "").strip() for span in parent_context_spans if str(span.get("text") or "").strip()),
        "boundary_candidates": boundaries,
        "semantic_model_reviews": semantic_reviews,
        "child_topics": child_topics,
        "parent_downstream_policy": "container_only_when_child_topics_exist" if len(child_topics) > 1 else "can_remain_single_topic_if_reviewed",
        "review_judgement": {
            "status": "needs_review" if len(child_topics) > 1 else "no_internal_split_found",
            "reason": "Judge each child crop visually and semantically. Dicta/visual rules are diagnostic, not ground truth.",
        },
    }


def _internal_split_blocking_parent_reason(candidate: dict[str, Any]) -> str:
    candidate_status = str(candidate.get("status") or "")
    diagnostics = candidate.get("grouping_diagnostics") if isinstance(candidate.get("grouping_diagnostics"), dict) else {}
    contamination = diagnostics.get("contamination") if isinstance(diagnostics.get("contamination"), dict) else {}
    contamination_status = str(contamination.get("status") or "")
    statuses = {candidate_status, contamination_status}
    unsafe = sorted(status for status in statuses if status in UNSAFE_INTERNAL_SPLIT_PARENT_STATUSES)
    if not unsafe:
        return ""
    reason = str(contamination.get("reason") or candidate.get("status_reason") or "")
    suffix = f" Raw upstream reason: {reason}" if reason else ""
    return f"Parent candidate was flagged upstream as unsafe for internal splitting ({', '.join(unsafe)}).{suffix}"


def _span_from_block(*, block: dict[str, Any], span_index: int) -> dict[str, Any]:
    display_text = str(block.get("display_selected_text") or block.get("display_text") or "").strip()
    selected_text = str(block.get("selected_text") or "").strip()
    raw_text = str(block.get("raw_native_text") or "").strip()
    text_before_cleanup = display_text or selected_text or raw_text
    text = cleanup_display_text(text_before_cleanup)
    return {
        "span_id": f"span_{span_index:04d}",
        "span_index": span_index,
        "block_id": block.get("block_id"),
        "page": block.get("page"),
        "text": text,
        "text_before_display_cleanup": text_before_cleanup,
        "display_text_cleanup": cleanup_display_text_record(before=text_before_cleanup, after=text),
        "grouping_text": selected_text or raw_text,
        "raw_native_text": raw_text,
        "source_role": block.get("role") or "",
        "source_role_reasons": block.get("role_reasons") or [],
        "visual_features": block.get("visual_features") or {},
        "bbox_pdf_points": block.get("bbox_pdf_points") or [],
        "bbox_crop_image_path": block.get("bbox_crop_image_path") or "",
        "page_image_path": block.get("page_image_path") or "",
        "raw_block": block,
    }


def _visual_marker_boundaries(*, parent_candidate_id: str, spans: list[dict[str, Any]], min_child_body_chars: int) -> list[dict[str, Any]]:
    marker_spans: list[tuple[int, dict[str, Any], list[str]]] = []
    for index, span in enumerate(spans):
        if index == 0:
            continue
        marker_status, marker_reasons = _visual_marker_status(span)
        if marker_status != "supported":
            continue
        marker_spans.append((index, span, marker_reasons))
    if _looks_like_dense_non_discussion_marker_run(spans=spans, marker_indexes=[index for index, _, _ in marker_spans], min_child_body_chars=min_child_body_chars):
        return []

    boundaries: list[dict[str, Any]] = []
    for position, (index, span, marker_reasons) in enumerate(marker_spans):
        next_index = marker_spans[position + 1][0] if position + 1 < len(marker_spans) else len(spans)
        local_support = _local_child_body_support(spans=spans, start=index, end=next_index)
        semantic_status, semantic_reason = _visual_marker_semantic_status(span=span, local_support=local_support, min_child_body_chars=min_child_body_chars)
        accepted = semantic_status == "supported"
        boundaries.append(
            {
                "boundary_id": f"{parent_candidate_id}_boundary_{len(boundaries) + 1:04d}",
                "parent_candidate_id": parent_candidate_id,
                "boundary_source": "visual_marker",
                "boundary_index": len(boundaries) + 1,
                "span_index": span.get("span_index"),
                "span_id": span.get("span_id"),
                "candidate_block_id": span.get("block_id"),
                "candidate_page": span.get("page"),
                "candidate_text": span.get("text") or "",
                "accepted_as_topic_boundary": accepted,
                "accepted_as_ground_truth": False,
                "status": "accepted_needs_review" if accepted else "rejected_or_needs_review",
                "status_reason": "Visual marker and semantic following text support an internal topic boundary; still requires human review." if accepted else semantic_reason,
                "visual_evidence": {
                    "status": marker_status,
                    "reasons": marker_reasons,
                    "bbox_crop_image_path": span.get("bbox_crop_image_path") or "",
                },
                "semantic_evidence": {
                    "status": semantic_status,
                    "reason": semantic_reason,
                    "following_substantive_chars": local_support["substantive_chars"],
                    "local_support": local_support,
                    "before_context_text": _context_text(spans=spans, start=max(0, index - 5), end=index, keep="end"),
                    "after_context_text": _context_text(spans=spans, start=index, end=min(len(spans), index + 8), keep="start"),
                },
            }
        )
    return [boundary for boundary in boundaries if boundary.get("accepted_as_topic_boundary") is True]


def _visual_marker_status(span: dict[str, Any]) -> tuple[str, list[str]]:
    text = _single_line(span.get("text"))
    if not text or not LETTER_RE.search(text):
        return "not_supported", ["no_text_or_letters"]
    if _looks_like_speaker_or_label(text):
        return "not_supported", ["speaker_turn_or_label"]
    if _looks_like_vote_or_table_text(text):
        return "not_supported", ["vote_result_or_table_text"]
    features = span.get("visual_features") if isinstance(span.get("visual_features"), dict) else {}
    line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
    width_ratio = _float_value(features.get("width_ratio"))
    max_font = _float_value(features.get("max_font_size"))
    source_role = str(span.get("source_role") or "")
    reasons: list[str] = []
    if LEADING_OUTLINE_RE.search(text):
        reasons.append("display_text_leading_outline_number")
    if source_role in {"candidate_start", "label_or_speaker"}:
        reasons.append("source_structural_role")
    if max_font is not None and max_font >= 12.5:
        reasons.append("large_or_bold_like_font")
    if line_count <= 3 and len(text) <= 240:
        reasons.append("heading_sized_block")
    if width_ratio is None or width_ratio >= 0.20:
        reasons.append("wide_enough_for_heading")
    if {"display_text_leading_outline_number", "source_structural_role"}.issubset(set(reasons)) and "heading_sized_block" in reasons:
        return "supported", reasons
    if "display_text_leading_outline_number" in reasons and "large_or_bold_like_font" in reasons and "heading_sized_block" in reasons:
        return "supported", reasons
    if "display_text_leading_outline_number" in reasons and "heading_sized_block" in reasons and "wide_enough_for_heading" in reasons and not _looks_like_numbered_body_sentence(text):
        reasons.append("plain_outline_heading_shape_without_source_role")
        return "supported", reasons
    return "not_supported", reasons or ["insufficient_visual_marker_evidence"]


def _visual_marker_semantic_status(*, span: dict[str, Any], local_support: dict[str, Any], min_child_body_chars: int) -> tuple[str, str]:
    text = _single_line(span.get("text"))
    if _looks_like_speaker_or_label(text):
        return "rejected", "Candidate marker is a speaker turn or short label."
    if _looks_like_vote_or_table_text(text):
        return "rejected", "Candidate marker is a vote/result/table row, not a topic."
    if DATE_LIKE_RE.search(text) and len(text) <= 120 and not LEADING_OUTLINE_RE.search(text):
        return "rejected", "Candidate marker is date/reference-like continuation."
    substantive_chars = _int_value(local_support.get("substantive_chars"), default=0)
    short_body_chars = _int_value(local_support.get("short_body_chars"), default=0)
    has_speaker_turn = bool(local_support.get("has_speaker_turn"))
    title_chars = _topic_title_detail_chars(text)
    if substantive_chars >= min_child_body_chars:
        return "supported", "The marker has topic-title shape and enough local substantive text before the next marker."
    if has_speaker_turn and title_chars >= 45 and substantive_chars + short_body_chars >= 10:
        return "supported", "The marker has a detailed title and local protocol discussion evidence before the next marker."
    return "rejected", "Not enough local substantive text follows this marker before the next visual marker."


def _local_child_body_support(*, spans: list[dict[str, Any]], start: int, end: int) -> dict[str, Any]:
    substantive_chars = 0
    short_body_chars = 0
    body_span_count = 0
    short_body_span_count = 0
    speaker_turn_count = 0
    list_like_span_count = 0
    vote_or_table_span_count = 0
    for span in spans[start + 1 : end]:
        text = _single_line(span.get("text"))
        if not text:
            continue
        if _looks_like_footer_or_path_artifact(text):
            list_like_span_count += 1
            continue
        if _looks_like_vote_or_table_text(text):
            vote_or_table_span_count += 1
            continue
        if SPEAKER_TURN_RE.search(text):
            speaker_turn_count += 1
            speaker_body = _speaker_body_text(text)
            if _looks_like_body_paragraph_text(speaker_body):
                substantive_chars += len(speaker_body)
                body_span_count += 1
            elif len(speaker_body) >= 10:
                short_body_chars += len(speaker_body)
                short_body_span_count += 1
            continue
        if _looks_like_speaker_or_label(text) or _looks_like_short_numbered_list_entry(text) or _looks_like_reference_like_continuation(text):
            list_like_span_count += 1
            continue
        if _looks_like_body_paragraph_text(text):
            substantive_chars += len(text)
            body_span_count += 1
            continue
        if len(text) >= 10:
            short_body_chars += len(text)
            short_body_span_count += 1
    return {
        "substantive_chars": substantive_chars,
        "short_body_chars": short_body_chars,
        "body_span_count": body_span_count,
        "short_body_span_count": short_body_span_count,
        "speaker_turn_count": speaker_turn_count,
        "has_speaker_turn": speaker_turn_count > 0,
        "list_like_span_count": list_like_span_count,
        "vote_or_table_span_count": vote_or_table_span_count,
    }


def _looks_like_dense_non_discussion_marker_run(*, spans: list[dict[str, Any]], marker_indexes: list[int], min_child_body_chars: int) -> bool:
    if len(marker_indexes) < 2:
        return False
    has_protocol_speaker = any(SPEAKER_TURN_RE.search(_single_line(span.get("text"))) for span in spans)
    if not has_protocol_speaker:
        # Internal visual splitting is only accepted for protocol-like discussion
        # evidence. Numbered runs without speaker turns are usually rosters,
        # agenda indexes, or table/list pages and stay as one review unit.
        return True
    unsupported_ranges = 0
    short_marker_count = 0
    for position, start in enumerate(marker_indexes):
        end = marker_indexes[position + 1] if position + 1 < len(marker_indexes) else len(spans)
        support = _local_child_body_support(spans=spans, start=start, end=end)
        if _int_value(support.get("substantive_chars"), default=0) < min_child_body_chars:
            unsupported_ranges += 1
        if _topic_title_detail_chars(_single_line(spans[start].get("text"))) < 45:
            short_marker_count += 1
    return unsupported_ranges == len(marker_indexes) and short_marker_count / max(1, len(marker_indexes)) >= 0.70


def _topic_title_detail_chars(text: str) -> int:
    stripped = _single_line(text)
    match = LEADING_OUTLINE_RE.search(stripped)
    remainder = stripped[match.end() :].strip() if match else stripped
    return len(re.sub(r"\s+", "", remainder))


def _speaker_body_text(text: str) -> str:
    parts = re.split(r"[:：]", _single_line(text), maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _looks_like_body_paragraph_text(text: str) -> bool:
    stripped = _single_line(text)
    if not stripped or _looks_like_vote_or_table_text(stripped) or _looks_like_footer_or_path_artifact(stripped):
        return False
    if LEADING_OUTLINE_RE.search(stripped):
        return False
    if _looks_like_reference_like_continuation(stripped):
        return False
    return len(stripped) >= 45 and (len(stripped) >= 80 or BODY_PUNCTUATION_RE.search(stripped) is not None)


def _looks_like_reference_like_continuation(text: str) -> bool:
    stripped = _single_line(text)
    if not stripped:
        return True
    return DATE_LIKE_RE.search(stripped) is not None and len(stripped) <= 140


def _looks_like_footer_or_path_artifact(text: str) -> bool:
    return FOOTER_ARTIFACT_RE.search(_single_line(text)) is not None


def _looks_like_short_numbered_list_entry(text: str) -> bool:
    stripped = _single_line(text)
    match = LEADING_OUTLINE_RE.search(stripped)
    if not match:
        return False
    remainder = stripped[match.end() :].strip()
    words = [part for part in re.split(r"\s+", remainder) if part]
    return len(remainder) <= 120 and len(words) <= 16 and BODY_PUNCTUATION_RE.search(remainder) is None


def _semantic_only_boundaries(
    *,
    candidate: dict[str, Any],
    spans: list[dict[str, Any]],
    raw_semantic_dir: Path,
    model: str,
    ollama_base_url: str,
    timeout_seconds: float,
    semantic_client: SemanticClient | None,
) -> dict[str, Any]:
    parent_id = str(candidate.get("candidate_id") or "")
    request_payload = {
        "parent_candidate_id": parent_id,
        "heading_text": candidate.get("heading_text") or "",
        "instruction": "Find semantic topic boundaries inside this parent even when there are no visual markers.",
        "spans": [
            {
                "span_id": span.get("span_id"),
                "span_index": span.get("span_index"),
                "page": span.get("page"),
                "text": _truncate(str(span.get("text") or ""), 900),
            }
            for span in spans
            if str(span.get("text") or "").strip()
        ],
    }
    prompt = _semantic_boundary_prompt(request_payload)
    safe = _safe_name(parent_id, default="parent")
    prompt_path = raw_semantic_dir / f"{safe}.prompt.txt"
    response_path = raw_semantic_dir / f"{safe}.response.json"
    prompt_path.write_text(prompt, encoding="utf-8")
    try:
        if semantic_client is None:
            raw_response = _call_ollama_text_json(prompt=prompt, model=model, ollama_base_url=ollama_base_url, timeout_seconds=timeout_seconds)
        else:
            raw_response = semantic_client(prompt=prompt, request_payload=request_payload, model=model)
        response_path.write_text(json.dumps(raw_response, ensure_ascii=False, indent=2), encoding="utf-8")
        parsed = _parse_model_json(_model_text(raw_response))
    except Exception as exc:  # pragma: no cover - network/model failures depend on environment.
        return {
            "status": "model_error",
            "reason": f"Semantic boundary model failed: {exc.__class__.__name__}: {exc}",
            "prompt_path": _abs(prompt_path),
            "raw_model_response_path": _abs(response_path) if response_path.exists() else "",
            "boundary_candidates": [],
        }
    if not isinstance(parsed, dict):
        return {
            "status": "model_output_parse_failed",
            "reason": "Semantic boundary model output did not contain a JSON object.",
            "prompt_path": _abs(prompt_path),
            "raw_model_response_path": _abs(response_path),
            "boundary_candidates": [],
        }
    boundaries = _semantic_boundaries_from_model(parent_candidate_id=parent_id, spans=spans, parsed=parsed)
    return {
        "status": "needs_review",
        "reason": "Semantic-only boundaries are diagnostic and require independent visual/semantic review.",
        "prompt_path": _abs(prompt_path),
        "raw_model_response_path": _abs(response_path),
        "parsed_json": parsed,
        "boundary_candidates": boundaries,
    }


def _semantic_boundary_prompt(request_payload: dict[str, Any]) -> str:
    return """
You are reviewing Hebrew municipality protocol text.
Task: find internal topic boundaries inside one broad parent agenda group.

Rules:
- Use semantic meaning, not municipality-specific wording.
- A new topic starts where the discussion changes to a different matter/request/decision, even if there is no visual heading.
- Keep chronological order. Do not create overlapping topics.
- Do not treat speaker changes alone as topic changes.
- Return JSON only.

Output schema:
{
  "topic_groups": [
    {
      "topic_title": "short Hebrew title",
      "start_span_id": "span_0001",
      "end_span_id": "span_0003",
      "reason": "why this is one topic",
      "evidence_quotes": ["exact short quote from spans"],
      "confidence": 0.0
    }
  ]
}

Input JSON:
""".strip() + "\n" + json.dumps(request_payload, ensure_ascii=False, indent=2)


def _semantic_boundaries_from_model(*, parent_candidate_id: str, spans: list[dict[str, Any]], parsed: dict[str, Any]) -> list[dict[str, Any]]:
    span_by_id = {str(span.get("span_id") or ""): span for span in spans}
    index_by_id = {str(span.get("span_id") or ""): index for index, span in enumerate(spans)}
    topics_value = parsed.get("topic_groups")
    topics = topics_value if isinstance(topics_value, list) else []
    normalized: list[dict[str, Any]] = []
    used_start_indexes: set[int] = set()
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        start_id = str(topic.get("start_span_id") or "")
        if start_id not in index_by_id:
            continue
        start_index = index_by_id[start_id]
        if start_index in used_start_indexes:
            continue
        used_start_indexes.add(start_index)
        normalized.append({**topic, "start_index": start_index, "start_span": span_by_id[start_id]})
    normalized.sort(key=lambda item: int(item.get("start_index") or 0))
    boundaries: list[dict[str, Any]] = []
    topics_to_boundary = normalized[1:] if normalized and int(normalized[0].get("start_index") or 0) == 0 else normalized
    for topic in topics_to_boundary:
        span = topic["start_span"]
        boundaries.append(
            {
                "boundary_id": f"{parent_candidate_id}_semantic_boundary_{len(boundaries) + 1:04d}",
                "parent_candidate_id": parent_candidate_id,
                "boundary_source": "semantic_only",
                "boundary_index": len(boundaries) + 1,
                "span_index": span.get("span_index"),
                "span_id": span.get("span_id"),
                "candidate_block_id": span.get("block_id"),
                "candidate_page": span.get("page"),
                "candidate_text": span.get("text") or "",
                "accepted_as_topic_boundary": True,
                "accepted_as_ground_truth": False,
                "status": "semantic_only_needs_review",
                "status_reason": "Dicta proposed this semantic topic boundary without a visual marker; independent review is required.",
                "visual_evidence": {"status": "semantic_only_no_visual_marker", "reasons": []},
                "semantic_evidence": {
                    "status": "model_supported_needs_review",
                    "reason": str(topic.get("reason") or "Semantic model grouped adjacent spans into different topics."),
                    "topic_title": str(topic.get("topic_title") or ""),
                    "evidence_quotes": topic.get("evidence_quotes") if isinstance(topic.get("evidence_quotes"), list) else [],
                    "confidence": topic.get("confidence"),
                },
            }
        )
    return boundaries


def _child_groups_from_boundaries(*, spans: list[dict[str, Any]], boundaries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not spans:
        return []
    parent_context = _leading_parent_context_spans(spans=spans, boundaries=boundaries)
    first_start = len(parent_context) if parent_context else 0
    start_indexes = [first_start]
    start_indexes.extend(index for index in _accepted_boundary_start_indexes(boundaries=boundaries, span_count=len(spans)) if index >= first_start)
    start_indexes = sorted(set(index for index in start_indexes if 0 <= index < len(spans)))
    groups: list[list[dict[str, Any]]] = []
    for position, start in enumerate(start_indexes):
        end = start_indexes[position + 1] if position + 1 < len(start_indexes) else len(spans)
        group = spans[start:end]
        if group:
            groups.append(group)
    return groups or [spans]


def _accepted_boundary_start_indexes(*, boundaries: list[dict[str, Any]], span_count: int) -> list[int]:
    starts: list[int] = []
    for boundary in boundaries:
        if boundary.get("accepted_as_topic_boundary") is not True:
            continue
        start = _int_value(boundary.get("span_index"), default=0) - 1
        if 0 <= start < span_count:
            starts.append(start)
    return sorted(set(starts))


def _leading_parent_context_spans(*, spans: list[dict[str, Any]], boundaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    starts = _accepted_boundary_start_indexes(boundaries=boundaries, span_count=len(spans))
    if not starts or starts[0] <= 0:
        return []
    context = spans[: starts[0]]
    return context if _looks_like_parent_context_spans(context) else []


def _looks_like_parent_context_spans(spans: list[dict[str, Any]]) -> bool:
    if not spans or len(spans) > 3:
        return False
    compact = _single_line("\n".join(str(span.get("text") or "") for span in spans))
    if not compact or len(compact) > 500:
        return False
    for span in spans:
        text = _single_line(span.get("text"))
        if not text:
            continue
        features = span.get("visual_features") if isinstance(span.get("visual_features"), dict) else {}
        source_role = str(span.get("source_role") or "")
        line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
        short_heading_shape = len(text) <= 240 and line_count <= 3
        if LEADING_OUTLINE_RE.search(text) and short_heading_shape:
            continue
        if _looks_like_speaker_or_label(text) and short_heading_shape:
            continue
        if source_role in {"candidate_start", "label_or_speaker"} and short_heading_shape:
            continue
        if text.endswith((':', '：')) and len(text) <= 160:
            continue
        return False
    return True


def _child_topic(*, parent_candidate_id: str, child_index: int, spans: list[dict[str, Any]], total_children: int, boundaries: list[dict[str, Any]]) -> dict[str, Any]:
    anchor = spans[0] if spans else {}
    pages = sorted({_int_value(span.get("page"), default=0) for span in spans if _int_value(span.get("page"), default=0) > 0})
    selected_text = "\n\n".join(str(span.get("text") or "").strip() for span in spans if str(span.get("text") or "").strip())
    selected_text_before_cleanup = "\n\n".join(
        str(span.get("text_before_display_cleanup") or span.get("text") or "").strip()
        for span in spans
        if str(span.get("text_before_display_cleanup") or span.get("text") or "").strip()
    )
    raw_text = "\n\n".join(str(span.get("raw_native_text") or "").strip() for span in spans if str(span.get("raw_native_text") or "").strip())
    child_id = f"{parent_candidate_id}_topic_{child_index:04d}"
    boundary = next((item for item in boundaries if _int_value(item.get("span_index"), default=-1) == _int_value(anchor.get("span_index"), default=-2)), {})
    status = "needs_visual_semantic_review" if total_children > 1 else "parent_only_no_internal_split"
    return {
        "child_topic_id": child_id,
        "parent_candidate_id": parent_candidate_id,
        "child_index": child_index,
        "status": status,
        "status_reason": "Predicted internal topic; requires visual and semantic review." if total_children > 1 else "No internal split was produced.",
        "accepted_as_ground_truth": False,
        "split_source": boundary.get("boundary_source") or "parent_start",
        "boundary_id": boundary.get("boundary_id") or "",
        "anchor_span_id": anchor.get("span_id"),
        "anchor_block_id": anchor.get("block_id"),
        "anchor_text": _single_line(anchor.get("text")),
        "pages": pages,
        "page_range": [min(pages), max(pages)] if pages else [],
        "selected_text": selected_text,
        "selected_text_before_display_cleanup": selected_text_before_cleanup,
        "text_cleanup": cleanup_display_text_record(before=selected_text_before_cleanup, after=selected_text),
        "raw_native_text": raw_text,
        "selected_char_count": len(selected_text),
        "source_block_ids": [str(span.get("block_id") or "") for span in spans],
        "spans": [_public_span(span) for span in spans],
        "visual_judgement": {"status": "needs_review", "reason": "Open child crops and original pages."},
        "semantic_judgement": {"status": "needs_review", "reason": "Check that this child text is one coherent topic and does not swallow the next topic."},
        "child_crops": [],
    }


def _public_span(span: dict[str, Any]) -> dict[str, Any]:
    return {
        "span_id": span.get("span_id"),
        "span_index": span.get("span_index"),
        "block_id": span.get("block_id"),
        "page": span.get("page"),
        "text": span.get("text") or "",
        "text_before_display_cleanup": span.get("text_before_display_cleanup") or "",
        "raw_native_text": span.get("raw_native_text") or "",
        "display_text_cleanup": span.get("display_text_cleanup") or {},
        "bbox_crop_image_path": span.get("bbox_crop_image_path") or "",
        "page_image_path": span.get("page_image_path") or "",
    }


def _render_child_crops(*, child: dict[str, Any], spans: list[dict[str, Any]], input_pdf_path: Path, output_dir: Path, render_dpi: int, crop_margin_points: float) -> list[dict[str, Any]]:
    if not input_pdf_path.exists() or not spans:
        return []
    crops: list[dict[str, Any]] = []
    with fitz.open(input_pdf_path) as document:
        by_page: dict[int, list[dict[str, Any]]] = {}
        for span in spans:
            page = _int_value(span.get("page"), default=0)
            if page > 0:
                by_page.setdefault(page, []).append(span)
        for page_number in sorted(by_page):
            if page_number < 1 or page_number > len(document):
                continue
            bbox = _union_bbox([span.get("bbox_pdf_points") or [] for span in by_page[page_number]])
            if not bbox:
                continue
            page = document[page_number - 1]
            clip = _expanded_rect(bbox=bbox, page_rect=page.rect, margin_points=crop_margin_points)
            crop_path = output_dir / f"{child['child_topic_id']}_page_{page_number:03d}_child_crop.png"
            _render_page(page=page, output_path=crop_path, dpi=render_dpi, clip=clip)
            crops.append(
                {
                    "page": page_number,
                    "child_crop_image_path": _abs(crop_path),
                    "crop_bbox_pdf_points": _rect_to_bbox(clip),
                    "source_block_ids": [str(span.get("block_id") or "") for span in by_page[page_number]],
                    "visual_evidence_type": "rendered_original_pdf_child_topic_crop",
                }
            )
    return crops


def _following_substantive_chars(*, spans: list[dict[str, Any]], start: int) -> int:
    total = 0
    for span in spans[start + 1 :]:
        text = _single_line(span.get("text"))
        if not text or _looks_like_speaker_or_label(text):
            continue
        total += len(text)
    return total


def _context_text(*, spans: list[dict[str, Any]], start: int, end: int, keep: str) -> str:
    lines = [f"[{span.get('span_id')} p{span.get('page')}] {_single_line(span.get('text'))}" for span in spans[start:end] if _single_line(span.get("text"))]
    text = "\n".join(lines)
    if len(text) <= 1400:
        return text
    return ("…" + text[-1400:]) if keep == "end" else (text[:1400] + "…")


def _looks_like_speaker_or_label(text: str) -> bool:
    stripped = _single_line(text)
    if SPEAKER_TURN_RE.search(stripped):
        return True
    return stripped.endswith(":") and len(stripped) <= 80 and LEADING_OUTLINE_RE.search(stripped) is None


def _looks_like_vote_or_table_text(text: str) -> bool:
    stripped = _single_line(text)
    return VOTE_OR_RESULT_RE.search(stripped) is not None or DECISION_TABLE_RE.search(stripped) is not None


def _looks_like_numbered_body_sentence(text: str) -> bool:
    stripped = _single_line(text)
    match = LEADING_OUTLINE_RE.search(stripped)
    if not match:
        return False
    remainder = stripped[match.end() :].strip()
    if not remainder:
        return True
    # The fallback is intentionally strict: source-role and large-font markers
    # are handled above; this branch only accepts clean heading-shaped lines.
    if re.search(r"[.!?;]", remainder):
        return True
    words = [part for part in re.split(r"\s+", remainder) if part]
    return len(remainder) > 180 or len(words) > 22


def _html_report(*, payload: dict[str, Any], output_dir: Path, original_pdf: dict[str, Any]) -> str:
    parent_cards = "\n".join(_parent_card(parent, output_dir=output_dir) for parent in payload.get("parent_topic_splits") or [])
    original_pdf_path = str(original_pdf.get("original_document_pdf_path") or "")
    pdf_link = f'<p><a href="{_h(_rel(Path(original_pdf_path), output_dir))}">Open original_document.pdf</a></p>' if original_pdf_path else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Internal Topic Split Review</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f6f7fb; color: #172033; }}
    header {{ background: #172033; color: #fff; padding: 26px 36px; }}
    main {{ max-width: 1340px; margin: 0 auto; padding: 26px 36px 56px; }}
    a {{ color: #1d5f8f; word-break: break-word; }}
    .card {{ background: #fff; border: 1px solid #d8dfeb; border-radius: 16px; padding: 18px; margin: 18px 0; box-shadow: 0 10px 24px rgba(23,32,51,.08); }}
    .parent {{ border-right: 8px solid #6b46c1; }}
    .child {{ border-right: 8px solid #2b6cb0; }}
    .boundary {{ border-right: 8px solid #c05621; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; }}
    .metric {{ background: #fff; border: 1px solid #d8dfeb; border-radius: 14px; padding: 14px; }}
    .metric strong {{ display: block; font-size: 26px; color: #173b66; }}
    pre {{ direction: rtl; text-align: right; white-space: pre-wrap; background: #111827; color: #f8fafc; padding: 12px; border-radius: 10px; overflow: auto; }}
    img {{ max-width: 100%; border: 1px solid #d8dfeb; border-radius: 10px; background: #fff; }}
    .path {{ direction: ltr; text-align: left; font-size: 12px; color: #526070; }}
  </style>
</head>
<body>
  <header>
    <h1>Internal Topic Split Review</h1>
    <p>Visual-marker splits are diagnostic. Dicta semantic-only splits are diagnostic. Human visual and semantic judgement is required.</p>
  </header>
  <main>
    <section class="grid">
      <div class="metric"><strong>{_h((payload.get('summary') or {}).get('parent_count'))}</strong> parents reviewed</div>
      <div class="metric"><strong>{_h((payload.get('summary') or {}).get('child_topic_count'))}</strong> child topics</div>
      <div class="metric"><strong>{_h((payload.get('summary') or {}).get('semantic_only_boundary_count'))}</strong> semantic-only boundaries</div>
    </section>
    <section class="card">
      {pdf_link}
      <p>JSON report: <span class="path">{_h(payload.get('internal_topic_split_json_path'))}</span></p>
      <p>Status: <strong>{_h(payload.get('status'))}</strong> - {_h(payload.get('status_reason'))}</p>
    </section>
    {parent_cards}
  </main>
</body>
</html>
"""


def _parent_card(parent: dict[str, Any], *, output_dir: Path) -> str:
    parent_crops = "\n".join(
        _image_figure(crop.get("group_crop_image_path"), f"Parent crop page {crop.get('page')}", output_dir=output_dir)
        for crop in parent.get("parent_group_crops") or []
    )
    boundaries = "\n".join(_boundary_card(boundary, output_dir=output_dir) for boundary in parent.get("boundary_candidates") or [])
    children = "\n".join(_child_card(child, output_dir=output_dir) for child in parent.get("child_topics") or [])
    return f"""
    <section class="card parent">
      <h2>{_h(parent.get('parent_candidate_id'))}: {_h(parent.get('heading_text'))}</h2>
      <p>Status: <strong>{_h(parent.get('status'))}</strong> - {_h(parent.get('status_reason'))}</p>
      <p>Downstream policy: <code>{_h(parent.get('parent_downstream_policy'))}</code></p>
      <h3>Parent Context</h3>
      <pre>{_h(parent.get('parent_context_text'))}</pre>
      <h3>Parent Crops</h3>
      <div class="grid">{parent_crops}</div>
      <h3>Boundary Candidates</h3>
      {boundaries or '<p>No internal boundary candidates.</p>'}
      <h3>Child Topic Predictions</h3>
      {children or '<p>No child topics.</p>'}
    </section>
    """


def _boundary_card(boundary: dict[str, Any], *, output_dir: Path) -> str:
    semantic = boundary.get("semantic_evidence") if isinstance(boundary.get("semantic_evidence"), dict) else {}
    visual = boundary.get("visual_evidence") if isinstance(boundary.get("visual_evidence"), dict) else {}
    context_text = (semantic.get("before_context_text") or "") + "\n--- SPLIT HERE ---\n" + (semantic.get("after_context_text") or "")
    boundary_crop = _image_figure(visual.get("bbox_crop_image_path"), "Boundary bbox crop", output_dir=output_dir)
    return f"""
    <section class="card boundary">
      <h4>{_h(boundary.get('boundary_id'))}: {_h(boundary.get('candidate_text'))}</h4>
      <p>Source: <code>{_h(boundary.get('boundary_source'))}</code>; status: <code>{_h(boundary.get('status'))}</code></p>
      <div class="grid">{boundary_crop}</div>
      <p>Semantic reason: {_h(semantic.get('reason'))}</p>
      <pre>{_h(context_text)}</pre>
    </section>
    """


def _child_card(child: dict[str, Any], *, output_dir: Path) -> str:
    crops = "\n".join(_image_figure(crop.get("child_crop_image_path"), f"Child crop page {crop.get('page')}", output_dir=output_dir) for crop in child.get("child_crops") or [])
    return f"""
    <section class="card child">
      <h4>{_h(child.get('child_topic_id'))}: {_h(child.get('anchor_text'))}</h4>
      <p>Status: <code>{_h(child.get('status'))}</code>; split source: <code>{_h(child.get('split_source'))}</code>; pages: {_h(child.get('page_range'))}</p>
      <p>Display cleanup: <code>{_h((child.get('text_cleanup') or {}).get('applied'))}</code>. Raw native text is preserved below.</p>
      <h5>Cleaned Display Text</h5>
      <pre>{_h(child.get('selected_text'))}</pre>
      <h5>Before Display Cleanup</h5>
      <pre>{_h(child.get('selected_text_before_display_cleanup'))}</pre>
      <h5>Raw Native Text</h5>
      <pre>{_h(child.get('raw_native_text'))}</pre>
      <div class="grid">{crops}</div>
    </section>
    """


def _image_figure(path_value: Any, caption: str, *, output_dir: Path) -> str:
    path = str(path_value or "")
    if not path:
        return ""
    src = _rel(Path(path), output_dir)
    return f"<figure><img src=\"{_h(src)}\" alt=\"{_h(caption)}\"><figcaption>{_h(caption)}<br><span class=\"path\">{_h(path)}</span></figcaption></figure>"


def _resolve_manual_path(*, agenda_report_path: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit.expanduser().resolve()
    candidate = agenda_report_path.parent / "manual_visual_review" / "manual_visual_judgements.json"
    if candidate.exists():
        return candidate.resolve()
    legacy = agenda_report_path.parent / "manual_visual_judgements.json"
    return legacy.resolve() if legacy.exists() else None


def _output_dir(*, agenda_report_path: Path, output_dir: Path | None, run_name: str) -> Path:
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir
    root = agenda_report_path.parent / DEFAULT_OUTPUT_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    name = _safe_name(run_name, default=datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ"))
    candidate = root / name
    if not candidate.exists():
        candidate.mkdir(parents=True)
        return candidate
    suffix = 2
    while True:
        next_candidate = root / f"{name}_{suffix:02d}"
        if not next_candidate.exists():
            next_candidate.mkdir(parents=True)
            return next_candidate
        suffix += 1


def _copy_original_pdf_for_report(*, report: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    source_text = str(report.get("input_pdf_path") or "")
    if not source_text:
        return {"status": "missing_input_pdf_path"}
    source = Path(source_text).expanduser()
    if not source.exists():
        return {"status": "missing_source_pdf", "source_input_document_path": source_text}
    target = output_dir / "original_document.pdf"
    if source.resolve() != target.resolve() and (not target.exists() or target.stat().st_size != source.stat().st_size):
        shutil.copy2(source, target)
    return {"status": "copied", "source_input_document_path": _abs(source), "original_document_pdf_path": _abs(target)}


def _call_ollama_text_json(*, prompt: str, model: str, ollama_base_url: str, timeout_seconds: float) -> dict[str, Any]:
    url = ollama_base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 1600},
    }
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - local Ollama URL.
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:  # pragma: no cover - depends on local model server.
        raise RuntimeError(f"Ollama request failed for {url}: {exc}") from exc


def _parse_model_json(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        match = SEMANTIC_JSON_RE.search(stripped)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _model_text(response: dict[str, Any]) -> str:
    message = response.get("message") if isinstance(response.get("message"), dict) else {}
    return str(message.get("content") or response.get("response") or "")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _union_bbox(bboxes: list[list[Any]]) -> list[float]:
    valid = []
    for bbox in bboxes:
        if isinstance(bbox, list) and len(bbox) == 4:
            try:
                x0, y0, x1, y1 = [float(value) for value in bbox]
            except (TypeError, ValueError):
                continue
            if x1 > x0 and y1 > y0:
                valid.append((x0, y0, x1, y1))
    if not valid:
        return []
    return [round(min(item[0] for item in valid), 3), round(min(item[1] for item in valid), 3), round(max(item[2] for item in valid), 3), round(max(item[3] for item in valid), 3)]


def _expanded_rect(*, bbox: list[float], page_rect: Any, margin_points: float) -> Any:
    rect = fitz.Rect(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    margin = max(0.0, float(margin_points))
    return (fitz.Rect(rect.x0 - margin, rect.y0 - margin, rect.x1 + margin, rect.y1 + margin)) & page_rect


def _render_page(*, page: Any, output_path: Path, dpi: int, clip: Any | None = None) -> None:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False, clip=clip)
    pixmap.save(output_path)


def _rect_to_bbox(rect: Any) -> list[float]:
    return [round(float(rect.x0), 3), round(float(rect.y0), 3), round(float(rect.x1), 3), round(float(rect.y1), 3)]


def _int_value(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_value(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _truncate(value: str, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _safe_name(value: str, *, default: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())[:120].strip("._")
    return safe or default


def _rel(path: Path, output_dir: Path) -> str:
    try:
        return str(path.expanduser().resolve().relative_to(output_dir.expanduser().resolve()))
    except ValueError:
        return path.expanduser().resolve().as_uri()


def _h(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


if __name__ == "__main__":
    raise SystemExit(main())
