from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any

try:
    import fitz
except ImportError as exc:  # pragma: no cover - exercised only in broken environments.
    raise SystemExit("PyMuPDF is required. Install project dependencies first.") from exc

from municipality.docsegmentation_native.display_text_cleanup import cleanup_display_text, cleanup_display_text_record
from municipality.docsegmentation_native.pdf_bbox_evidence import _abs, _block_in_semantic_scope, _with_path_validation


SCHEMA_VERSION = "municipality_native_agenda_candidates_v1"
DEFAULT_OUTPUT_SUBDIR = "agenda_topic_candidates"
HEADING_NUMBER_RE = re.compile(
    r"(^\s*[\(:.\-]*\d{1,4}(?:[./]\d{1,4}){0,2}\s*[.)'\"״׳:]*\s+\S)"
    r"|(\S\s+\d{1,4}\s*[.)'\"״׳:]*\s*$)"
)
LEADING_HEADING_NUMBER_RE = re.compile(r"^\s*[\(:.\-]*(\d{1,4})(?:[./]\d{1,4}){0,2}\s*[.)'\"״׳:]*\s+\S")
TRAILING_HEADING_NUMBER_RE = re.compile(r"(\d{1,4})\s*[.)'\"״׳:]*\s*$")
INDEX_ENTRY_RE = re.compile(r"(?:\([^)]*\d{1,4}[^)]*\)|\([^)]*\)\s*\d{1,4})\s*$")
DATE_LIKE_RE = re.compile(r"(?<!\d)\d{1,2}[./]\d{1,2}[./]\d{2,4}(?!\d)")
NUMBER_TOKEN_RE = re.compile(r"\d{1,6}")
COMPACT_DECIMAL_RE = re.compile(r"\d{1,3}[./]\d{1,3}")
EARLY_SECTION_NUMBER_RE = re.compile(r"^\D{0,12}\d{1,4}(?!\d)\s*:")
EMBEDDED_HEADING_NUMBER_RE = re.compile(
    r"^\D{0,12}\d{1,4}(?!\d)\s*:\s*\d{1,4}(?:[./]\d{1,4}){1,2}(?!\d)"
    r"|^\D{0,12}\d{1,4}(?!\d)\s+\d{1,4}\s*[.)'\"״׳:]\s+\S"
)
BODY_PUNCTUATION_RE = re.compile(r"[.!?;:]|[\u05C3]")
LETTER_RE = re.compile(r"[A-Za-z\u0590-\u05FF]")
PARENTHETICAL_ENUMERATOR_RE = re.compile(r"(?:\(\s*(?:\d{1,3}|[A-Za-z\u0590-\u05FF]{1,3})\s*\)|\)\s*(?:\d{1,3}|[A-Za-z\u0590-\u05FF]{1,3})\s*\()")
BODY_ANCHOR_THRESHOLD = 5.0
LIST_ENTRY_THRESHOLD = 3.5
MAX_FRONT_MATTER_NAVIGATION_PAGES = 6
IGNORED_PAGE_STATUSES = {"ignored_image_or_nonsemantic_page", "ignored_no_native_text_page"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Group local native PDF text blocks into agenda/body candidates for one evidence folder.")
    parser.add_argument("evidence_folder", help="One per-PDF folder created by local pdf_bbox_evidence")
    parser.add_argument("--output-subdir", default=DEFAULT_OUTPUT_SUBDIR)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--render-dpi", type=int, default=180)
    parser.add_argument("--crop-margin-points", type=float, default=8.0)
    parser.add_argument("--min-body-chars", type=int, default=120)
    args = parser.parse_args(argv)

    result = run_native_agenda_candidates(
        evidence_folder=Path(args.evidence_folder).expanduser().resolve(),
        output_subdir=str(args.output_subdir),
        run_name=str(args.run_name),
        render_dpi=max(72, int(args.render_dpi)),
        crop_margin_points=max(0.0, float(args.crop_margin_points)),
        min_body_chars=max(1, int(args.min_body_chars)),
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "reason": result["status_reason"],
                "evidence_folder_path": result["evidence_folder_path"],
                "output_run_dir_path": result["output_run_dir_path"],
                "agenda_candidate_report_path": result["agenda_candidate_report_path"],
                "candidate_count": result["summary"]["candidate_count"],
                "navigation_candidate_count": result["summary"]["navigation_candidate_count"],
                "skipped_page_count": result["summary"]["skipped_page_count"],
                "ignored_page_count": result["summary"].get("ignored_page_count", 0),
                "blocking_skipped_page_count": result["summary"].get("blocking_skipped_page_count", 0),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if result["status"] != "ok":
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "reason": result["status_reason"],
                    "raw_evidence": result["quality_judgement"].get("raw_evidence"),
                    "suggested_generic_next_step": result["quality_judgement"].get("suggested_generic_next_step"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0 if result["status"] in {"ok", "partial", "needs_review"} else 2


def run_native_agenda_candidates(
    *,
    evidence_folder: Path,
    output_subdir: str = DEFAULT_OUTPUT_SUBDIR,
    run_name: str = "",
    render_dpi: int = 180,
    crop_margin_points: float = 8.0,
    min_body_chars: int = 120,
) -> dict[str, Any]:
    evidence_folder = evidence_folder.expanduser().resolve()
    extraction_report_path = evidence_folder / "extraction_report.json"
    if not extraction_report_path.exists():
        raise FileNotFoundError(f"Missing extraction report: {extraction_report_path}")
    extraction = json.loads(extraction_report_path.read_text(encoding="utf-8"))
    input_pdf_path = Path(str(extraction.get("resolved_input_pdf_path") or extraction.get("input_pdf_path") or "")).expanduser().resolve()
    if not input_pdf_path.exists():
        raise FileNotFoundError(f"Input PDF not found from extraction report: {input_pdf_path}")

    pages = [page for page in extraction.get("pages") or [] if isinstance(page, dict)]
    output_run_dir = _new_output_run_dir(root=evidence_folder / _safe_name(output_subdir, default=DEFAULT_OUTPUT_SUBDIR), run_name=run_name)
    candidates_dir = output_run_dir / "candidates"
    navigation_dir = output_run_dir / "agenda_navigation_candidates"
    candidates_dir.mkdir(parents=True, exist_ok=False)
    navigation_dir.mkdir(parents=True, exist_ok=False)
    report_path = output_run_dir / "agenda_candidate_report.json"
    audit_path = output_run_dir / "agenda_candidate_audit.md"
    candidates_jsonl_path = output_run_dir / "candidates.jsonl"
    navigation_report_path = navigation_dir / "navigation_candidate_report.json"
    navigation_jsonl_path = navigation_dir / "navigation_candidates.jsonl"

    selected_blocks, skipped_pages, excluded_context = _selected_semantic_blocks(pages)
    annotated_blocks = _annotate_grouping_context(selected_blocks)
    first_body_anchor_index = _first_body_anchor_index(annotated_blocks)
    navigation_scan_limit_index = _front_matter_navigation_scan_limit_index(annotated_blocks, fallback_index=first_body_anchor_index)
    navigation_runs = _detect_navigation_list_runs(annotated_blocks, first_body_anchor_index=navigation_scan_limit_index)
    navigation_runs = _dedupe_navigation_runs(
        navigation_runs + _detect_dense_early_navigation_list_runs(annotated_blocks, first_body_anchor_index=navigation_scan_limit_index)
    )
    navigation_block_ids = {str(block.get("block_id") or "") for run in navigation_runs for block in run}
    body_blocks: list[dict[str, Any]] = []
    for block in annotated_blocks:
        if str(block.get("block_id") or "") in navigation_block_ids:
            excluded_context.append(_excluded_context_record(block=block, reason="agenda_navigation_list_candidate_saved_separately"))
            continue
        body_blocks.append(block)
    body_blocks = _promote_body_start_anchors(body_blocks)
    grouped = _group_blocks_into_candidates(body_blocks, min_body_chars=min_body_chars)
    navigation_candidates = _navigation_candidates_from_runs(navigation_runs)

    _render_candidate_group_crops(candidates=grouped, input_pdf_path=input_pdf_path, candidates_dir=candidates_dir, render_dpi=render_dpi, crop_margin_points=crop_margin_points)
    _render_navigation_group_crops(navigation_candidates=navigation_candidates, input_pdf_path=input_pdf_path, navigation_dir=navigation_dir, render_dpi=render_dpi, crop_margin_points=crop_margin_points)
    _write_candidate_files(grouped, candidates_dir=candidates_dir)
    _write_navigation_files(navigation_candidates, navigation_dir=navigation_dir)

    group_crop_count = sum(len(candidate.get("group_crops") or []) for candidate in grouped)
    navigation_crop_count = sum(len(candidate.get("navigation_crops") or []) for candidate in navigation_candidates)
    needs_review_count = sum(1 for candidate in grouped if str(candidate.get("status") or "").startswith("needs_review"))
    ignored_page_count = sum(1 for page in skipped_pages if _skipped_page_is_ignored(page))
    blocking_skipped_page_count = len(skipped_pages) - ignored_page_count
    if not grouped:
        status = "needs_review"
        status_reason = "No body agenda/topic candidates were found from semantic native text blocks."
    elif needs_review_count:
        status = "partial"
        status_reason = "Some body agenda candidates are short, weak, or possibly contaminated and need visual review."
    elif blocking_skipped_page_count:
        status = "partial"
        status_reason = "Body agenda candidates were generated, but one or more skipped pages still need visual review."
    elif ignored_page_count:
        status = "ok"
        status_reason = "Body agenda candidates were generated; pages without usable native semantic text were recorded as ignored evidence and OCR was not attempted."
    else:
        status = "ok"
        status_reason = "Body agenda candidates and separate navigation/list candidates were generated from native text and visual crops were saved."

    payload = _with_path_validation(
        {
            "step": "municipality_native_agenda_topic_candidate_grouping",
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "status_reason": status_reason,
            "accepted_as_ground_truth": False,
            "research_use": "candidate_generation_only_requires_visual_review",
            "evidence_folder_path": _abs(evidence_folder),
            "input_pdf_path": _abs(input_pdf_path),
            "extraction_report_path": _abs(extraction_report_path),
            "output_run_dir_path": _abs(output_run_dir),
            "candidates_dir_path": _abs(candidates_dir),
            "agenda_navigation_candidates_dir_path": _abs(navigation_dir),
            "agenda_candidate_report_path": _abs(report_path),
            "agenda_candidate_audit_path": _abs(audit_path),
            "candidates_jsonl_path": _abs(candidates_jsonl_path),
            "navigation_candidate_report_path": _abs(navigation_report_path),
            "navigation_candidates_jsonl_path": _abs(navigation_jsonl_path),
            "settings": {
                "processing_mode": "one_evidence_folder_at_a_time",
                "source_text_policy": "native_text_only_no_ocr_no_model_repair",
                "grouping_policy": "Generic body-anchor grouping with separate agenda_navigation_candidates for dense list/index material. No municipality-specific labels are used.",
                "render_dpi": render_dpi,
                "crop_margin_points": crop_margin_points,
                "min_body_chars": min_body_chars,
            },
            "summary": {
                "page_count": len(pages),
                "native_semantic_block_count": len(selected_blocks),
                "excluded_context_block_count": len(excluded_context),
                "candidate_count": len(grouped),
                "navigation_candidate_count": len(navigation_candidates),
                "needs_review_candidate_count": needs_review_count,
                "group_crop_count": group_crop_count,
                "navigation_crop_count": navigation_crop_count,
                "skipped_page_count": len(skipped_pages),
                "ignored_page_count": ignored_page_count,
                "blocking_skipped_page_count": blocking_skipped_page_count,
                "skipped_page_status_counts": _counts(item.get("status") for item in skipped_pages),
            },
            "skipped_pages": skipped_pages,
            "excluded_context_blocks": excluded_context[:500],
            "agenda_navigation_candidates": navigation_candidates,
            "quality_judgement": _quality_judgement(status=status, status_reason=status_reason, candidates=grouped, navigation_candidates=navigation_candidates, skipped_pages=skipped_pages),
            "candidates": grouped,
        }
    )
    navigation_payload = _with_path_validation(
        {
            "step": "municipality_native_agenda_navigation_candidates",
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if navigation_candidates else "no_navigation_candidates",
            "status_reason": "Dense agenda/index/list material was saved separately for visual review." if navigation_candidates else "No dense agenda/index/list material was detected before body anchors.",
            "accepted_as_ground_truth": False,
            "agenda_candidate_report_path": _abs(report_path),
            "navigation_candidate_report_path": _abs(navigation_report_path),
            "navigation_candidates_jsonl_path": _abs(navigation_jsonl_path),
            "navigation_candidates": navigation_candidates,
        }
    )
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    audit_path.write_text(_audit_markdown(payload), encoding="utf-8")
    candidates_jsonl_path.write_text("".join(json.dumps(candidate, ensure_ascii=False) + "\n" for candidate in grouped), encoding="utf-8")
    navigation_report_path.write_text(json.dumps(navigation_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    navigation_jsonl_path.write_text("".join(json.dumps(candidate, ensure_ascii=False) + "\n" for candidate in navigation_candidates), encoding="utf-8")
    return payload


def _selected_semantic_blocks(pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    skipped_pages: list[dict[str, Any]] = []
    excluded_context: list[dict[str, Any]] = []
    for page in pages:
        page_number = _int_value(page.get("page"), default=0)
        page_status = page.get("page_processing_status") if isinstance(page.get("page_processing_status"), dict) else {}
        status = str(page_status.get("status") or "")
        if status != "native_text_available":
            skipped_pages.append(
                {
                    "page": page_number,
                    "status": "ignored_no_native_text_page",
                    "skip_reason": page_status.get("skip_reason"),
                    "status_reason": "No usable native text blocks were available for this page. OCR is intentionally not attempted; the rendered page is kept as ignored visual evidence.",
                    "source_page_processing_status": status or "unknown",
                    "native_text_absence_reason": (page.get("native_page_diagnostics") or {}).get("native_text_absence_reason"),
                    "no_ocr_attempted": True,
                    "downstream_blocking": False,
                    "page_image_path": page.get("page_image_path"),
                    "page_text_path": page.get("page_text_path"),
                }
            )
            continue
        text_block_count = 0
        semantic_scope_block_count = 0
        for block in page.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            text_block_count += 1
            if not _is_agenda_content_scope(block):
                excluded_context.append(_excluded_context_record(block=block, reason="outside_agenda_content_scope"))
                continue
            semantic_scope_block_count += 1
            item = _candidate_block_item(block)
            if item["role"] == "context_before_first_topic":
                excluded_context.append(_excluded_context_record(block=item, reason="context_before_first_topic"))
                continue
            selected.append(item)
        if text_block_count and semantic_scope_block_count == 0:
            skipped_pages.append(
                {
                    "page": page_number,
                    "status": "ignored_image_or_nonsemantic_page",
                    "skip_reason": "native_blocks_outside_semantic_scope",
                    "status_reason": "Native text blocks were extracted, but none passed the generic semantic-content scope filter. OCR is intentionally not attempted; the rendered page is kept as ignored visual evidence.",
                    "native_text_absence_reason": (page.get("native_page_diagnostics") or {}).get("native_text_absence_reason"),
                    "text_block_count": text_block_count,
                    "semantic_scope_block_count": semantic_scope_block_count,
                    "raw_native_text_preview": str(page.get("plain_text") or "").strip()[:500],
                    "no_ocr_attempted": True,
                    "downstream_blocking": False,
                    "page_image_path": page.get("page_image_path"),
                    "page_text_path": page.get("page_text_path"),
                    "suggested_generic_next_step": "Keep the rendered page as evidence and ignore it for native-text Step 4/5 unless a separate manual/OCR workflow is explicitly requested.",
                }
            )
    selected.sort(key=lambda item: (_int_value(item.get("page"), default=0), _bbox_y0(item), _bbox_x0_for_reading(item)))
    selected = _drop_repeated_page_header_blocks(selected, excluded_context)
    return selected, skipped_pages, excluded_context


def _skipped_page_is_ignored(page: dict[str, Any]) -> bool:
    status = str(page.get("status") or "")
    return status in IGNORED_PAGE_STATUSES or page.get("downstream_blocking") is False


def _is_agenda_content_scope(block: dict[str, Any]) -> bool:
    if _looks_like_visually_strong_global_heading_block(block):
        return True
    if _looks_like_page_chrome_block(block):
        return False
    if not _block_in_semantic_scope(block):
        return False
    scope = block.get("semantic_scope") if isinstance(block.get("semantic_scope"), dict) else {}
    return str(scope.get("category") or "semantic_content") == "semantic_content"


def _drop_repeated_page_header_blocks(blocks: list[dict[str, Any]], excluded_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signature_pages: dict[str, set[int]] = defaultdict(set)
    block_signatures: dict[str, str] = {}
    for block in blocks:
        signature = _repeated_page_header_signature(block)
        if not signature:
            continue
        block_id = str(block.get("block_id") or id(block))
        block_signatures[block_id] = signature
        page = _int_value(block.get("page"), default=0)
        if page > 0:
            signature_pages[signature].add(page)
    repeated_signatures = {signature for signature, pages in signature_pages.items() if len(pages) >= 3}
    if not repeated_signatures:
        return blocks

    kept: list[dict[str, Any]] = []
    for block in blocks:
        block_id = str(block.get("block_id") or id(block))
        signature = block_signatures.get(block_id)
        if signature in repeated_signatures and not _looks_like_visually_strong_global_heading_block(block):
            excluded_context.append(_excluded_context_record(block=block, reason="repeated_top_page_header_chrome"))
            continue
        kept.append(block)
    return kept


def _repeated_page_header_signature(block: dict[str, Any]) -> str:
    if _looks_like_visually_strong_global_heading_block(block):
        return ""
    text = _single_line(str(block.get("selected_text") or block.get("rtl_normalized_text") or block.get("raw_native_text") or block.get("text") or ""))
    if len(text) < 20 or not LETTER_RE.search(text):
        return ""
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    y0_ratio = _float_value(features.get("y0_ratio"))
    y1_ratio = _float_value(features.get("y1_ratio"))
    height_ratio = _float_value(features.get("height_ratio"))
    if height_ratio is None and y0_ratio is not None and y1_ratio is not None:
        height_ratio = max(0.0, y1_ratio - y0_ratio)
    width_ratio = _float_value(features.get("width_ratio"))
    line_count = _int_value(features.get("line_count"), default=max(1, str(block.get("selected_text") or "").count("\n") + 1))
    char_count = _int_value(features.get("char_count"), default=len(text))
    if y0_ratio is None or y0_ratio > 0.24:
        return ""
    if y1_ratio is not None and y1_ratio > 0.30:
        return ""
    if line_count < 2:
        return ""
    if char_count > 320:
        return ""
    if height_ratio is not None and height_ratio > 0.16:
        return ""
    if width_ratio is not None and width_ratio < 0.20:
        return ""
    normalized = re.sub(r"\d+", "#", text)
    normalized = re.sub(r"\s+", " ", normalized).strip(" -–—:.,;()[]{}'\"״׳")
    return normalized if len(normalized) >= 20 else ""


def _looks_like_page_chrome_block(block: dict[str, Any]) -> bool:
    if _looks_like_visually_strong_global_heading_block(block):
        return False
    text = _single_line(str(block.get("rtl_normalized_text") or block.get("raw_native_text") or block.get("selected_text") or block.get("text") or ""))
    if not text:
        return False
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    y0_ratio = _float_value(features.get("y0_ratio"))
    y1_ratio = _float_value(features.get("y1_ratio"))
    height_ratio = _float_value(features.get("height_ratio"))
    if height_ratio is None and y0_ratio is not None and y1_ratio is not None:
        height_ratio = max(0.0, y1_ratio - y0_ratio)
    width_ratio = _float_value(features.get("width_ratio"))
    max_font_size = _float_value(features.get("max_font_size"))
    median_font_size = _float_value(features.get("median_font_size"))
    line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
    char_count = _int_value(features.get("char_count"), default=len(text))
    small_font = (max_font_size is not None and max_font_size <= 10.5) or (
        median_font_size is not None and median_font_size <= 10.5 and (max_font_size is None or max_font_size <= 12.5)
    )
    if small_font and char_count <= 180 and line_count <= 12:
        if height_ratio is not None and height_ratio > 0.06:
            return False
        if y0_ratio is not None and y1_ratio is not None and y0_ratio <= 0.11 and y1_ratio <= 0.16:
            return True
        if y0_ratio is not None and y1_ratio is not None and y0_ratio >= 0.88 and y1_ratio >= 0.90:
            return True
    if (
        y0_ratio is not None
        and y0_ratio <= 0.22
        and height_ratio is not None
        and height_ratio >= 0.04
        and line_count >= 3
        and max_font_size is not None
        and max_font_size >= 12.0
        and char_count <= 260
    ):
        return True
    return _looks_like_short_narrow_top_decimal_header(text=text, y0_ratio=y0_ratio, width_ratio=width_ratio, height_ratio=height_ratio, line_count=line_count, char_count=char_count)


def _looks_like_short_narrow_top_decimal_header(*, text: str, y0_ratio: float | None, width_ratio: float | None, height_ratio: float | None, line_count: int, char_count: int) -> bool:
    return (
        y0_ratio is not None
        and y0_ratio <= 0.22
        and width_ratio is not None
        and width_ratio <= 0.20
        and (height_ratio is None or height_ratio <= 0.025)
        and line_count <= 2
        and char_count <= 40
        and LETTER_RE.search(text) is not None
        and COMPACT_DECIMAL_RE.search(text) is not None
    )


def _candidate_block_item(block: dict[str, Any]) -> dict[str, Any]:
    raw_text = str(block.get("raw_native_text") or block.get("text") or "")
    display_text = str(block.get("display_text") or "")
    rtl_text = str(block.get("rtl_normalized_text") or "")
    source_text = rtl_text.strip() or raw_text.strip()
    display_source_text = display_text.strip() or source_text
    selected_text = _display_text_for_downstream(source_text)
    display_selected_text = _display_text_for_downstream(display_source_text)
    role, role_reasons = _block_role(block=block, selected_text=selected_text)
    selected_text_source = "deterministic_display_text_from_rtl_normalized_text" if rtl_text.strip() else "deterministic_display_text_from_raw_native_text"
    downstream_text_source = "deterministic_display_text_from_geometry_display_text" if display_text.strip() else selected_text_source
    return {
        "block_id": block.get("block_id"),
        "page": block.get("page"),
        "role": role,
        "role_reasons": role_reasons,
        "selected_text_source": selected_text_source,
        "selected_text": selected_text,
        "selected_text_before_display_cleanup": source_text,
        "display_selected_text_source": downstream_text_source,
        "display_selected_text": display_selected_text,
        "display_selected_text_before_cleanup": display_source_text,
        "raw_native_text": raw_text,
        "rtl_normalized_text": rtl_text,
        "display_text": display_text,
        "display_text_diagnostics": block.get("display_text_diagnostics") or {},
        "text_cleanup": _text_cleanup_record(before=source_text, after=selected_text),
        "display_text_cleanup": _text_cleanup_record(before=display_source_text, after=display_selected_text),
        "bbox_pdf_points": block.get("bbox_pdf_points") or [],
        "bbox_crop_image_path": block.get("bbox_crop_image_path"),
        "bbox_text_path": block.get("bbox_text_path"),
        "page_image_path": block.get("page_image_path"),
        "visual_features": block.get("visual_features") or {},
        "semantic_scope": block.get("semantic_scope") or {},
        "native_text_quality": block.get("native_text_quality") or {},
        "quality_flags": sorted(set(block.get("quality_flags") or [])),
        "char_count": len(selected_text),
        "body_char_count": _body_char_count(role=role, selected_text=selected_text),
    }


def _block_role(*, block: dict[str, Any], selected_text: str) -> tuple[str, list[str]]:
    text = _single_line(selected_text)
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    page_number = _int_value(block.get("page"), default=0)
    y0_ratio = _float_value(features.get("y0_ratio"))
    width_ratio = _float_value(features.get("width_ratio"))
    line_count = _int_value(features.get("line_count"), default=max(1, selected_text.count("\n") + 1))
    body_punctuation_count = len(BODY_PUNCTUATION_RE.findall(text))
    if len(text) < 4 or not LETTER_RE.search(text):
        return "context_before_first_topic", ["too_short_or_no_letters"]
    if width_ratio is not None and width_ratio <= 0.03 and len(text) <= 40:
        return "context_before_first_topic", ["tiny_narrow_number_or_footnote_fragment"]
    if page_number == 1 and y0_ratio is not None and y0_ratio < 0.20:
        return "context_before_first_topic", ["top_of_first_page_document_context"]
    if _looks_like_nested_numbered_body_clause(block=block, text=text, line_count=line_count):
        return "body", ["nested_numbered_body_clause_not_heading"]
    if _looks_like_short_trailing_number_heading(block=block, text=text, line_count=line_count):
        return "candidate_start", ["visually_supported_short_trailing_number_heading"]
    if _looks_like_narrow_numeric_table_cell(block=block, text=text):
        return "body", ["narrow_numeric_table_cell_not_heading"]
    if _looks_like_wide_date_like_body_continuation(block=block, text=text, line_count=line_count, body_punctuation_count=body_punctuation_count):
        return "body", ["wide_date_like_body_continuation_not_heading"]
    if _looks_like_numbered_body_continuation(block=block, text=text, body_punctuation_count=body_punctuation_count):
        return "body", ["numbered_body_continuation_not_heading"]
    if _looks_like_structural_heading(text=text, line_count=line_count, body_punctuation_count=body_punctuation_count):
        if DATE_LIKE_RE.search(text) and len(text) > 80:
            return "body", ["date_like_long_body_text_not_heading"]
        return "candidate_start", ["numbering_like_heading_shape"]
    if _looks_like_short_label(text=text, line_count=line_count):
        return "label_or_speaker", ["short_label_or_speaker_like_block"]
    return "body", ["body_continuation"]


def _annotate_grouping_context(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        features = _grouping_features(block=block, previous=blocks[index - 1] if index > 0 else None, following=blocks[index + 1 : index + 8])
        item = {**block, "grouping_features": features}
        out.append(item)
    return out


def _grouping_features(*, block: dict[str, Any], previous: dict[str, Any] | None, following: list[dict[str, Any]]) -> dict[str, Any]:
    list_score, list_reasons = _list_entry_score(block)
    body_score, body_reasons = _body_paragraph_score(block)
    anchor_score, anchor_reasons = _body_start_anchor_score(block=block, following=following)
    table_score, table_reasons = _table_cell_score(block)
    y_gap = None
    if previous and _int_value(previous.get("page"), default=0) == _int_value(block.get("page"), default=-1):
        y_gap = round(max(0.0, _bbox_y0(block) - _bbox_y1(previous)), 4)
    return {
        "list_entry_score": round(list_score, 3),
        "list_entry_like": list_score >= LIST_ENTRY_THRESHOLD,
        "list_entry_reasons": list_reasons,
        "body_paragraph_score": round(body_score, 3),
        "body_paragraph_like": body_score >= 2.0,
        "body_paragraph_reasons": body_reasons,
        "body_anchor_score": round(anchor_score, 3),
        "body_start_anchor": anchor_score >= BODY_ANCHOR_THRESHOLD,
        "body_anchor_reasons": anchor_reasons,
        "table_cell_score": round(table_score, 3),
        "table_cell_like": table_score >= 2.0,
        "table_cell_reasons": table_reasons,
        "same_page_y_gap_from_previous": y_gap,
    }


def _list_entry_score(block: dict[str, Any]) -> tuple[float, list[str]]:
    text = _single_line(str(block.get("selected_text") or ""))
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
    width_ratio = _float_value(features.get("width_ratio"))
    punctuation_count = len(BODY_PUNCTUATION_RE.findall(text))
    score = 0.0
    reasons: list[str] = []
    if _leading_heading_number(text) is not None or HEADING_NUMBER_RE.search(text):
        score += 2.0
        reasons.append("numbering_like_row")
    if len(text) <= 140:
        score += 1.0
        reasons.append("short_or_medium_row")
    elif len(text) <= 220:
        score += 0.5
        reasons.append("bounded_row_length")
    if line_count <= 4:
        score += 1.0
        reasons.append("few_visual_lines")
    elif line_count <= 6 and len(text) <= 140:
        score += 0.5
        reasons.append("fragmented_but_bounded_lines")
    if punctuation_count <= 2:
        score += 0.8
        reasons.append("low_body_punctuation")
    if width_ratio is not None and 0.04 <= width_ratio <= 0.85:
        score += 0.4
        reasons.append("row_width_in_list_range")
    if DATE_LIKE_RE.search(text) and len(text) <= 140:
        score += 0.3
        reasons.append("compact_date_or_reference")
    return score, reasons


def _body_paragraph_score(block: dict[str, Any]) -> tuple[float, list[str]]:
    text = _single_line(str(block.get("selected_text") or ""))
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    width_ratio = _float_value(features.get("width_ratio"))
    line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
    punctuation_count = len(BODY_PUNCTUATION_RE.findall(text))
    score = 0.0
    reasons: list[str] = []
    if len(text) >= 80:
        score += 1.0
        reasons.append("long_continuous_text")
    if punctuation_count >= 2:
        score += 1.0
        reasons.append("body_punctuation_present")
    if width_ratio is not None and width_ratio >= 0.35:
        score += 0.8
        reasons.append("wide_body_geometry")
    if line_count >= 2 and len(text) >= 45:
        score += 0.6
        reasons.append("multi_line_body_text")
    if block.get("role") in {"body", "label_or_speaker"} and len(text) >= 20:
        score += 0.5
        reasons.append("non_heading_content_role")
    return score, reasons


def _body_start_anchor_score(*, block: dict[str, Any], following: list[dict[str, Any]]) -> tuple[float, list[str]]:
    text = _single_line(str(block.get("selected_text") or ""))
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    width_ratio = _float_value(features.get("width_ratio"))
    max_font_size = _float_value(features.get("max_font_size"))
    line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
    score = 0.0
    reasons: list[str] = []
    if block.get("role") == "candidate_start":
        score += 2.0
        reasons.append("candidate_start_role")
    if max_font_size is not None and max_font_size >= 12.5 and width_ratio is not None and width_ratio >= 0.20 and line_count <= 4:
        score += 3.0
        reasons.append("visually_strong_heading")
    if _leading_heading_number(text) is not None and len(text) <= 180:
        score += 0.6
        reasons.append("bounded_leading_number_heading")
    follow_chars = 0
    follow_body_blocks = 0
    for next_block in following:
        if _int_value(next_block.get("page"), default=0) < _int_value(block.get("page"), default=0):
            continue
        if next_block.get("role") == "candidate_start":
            # A body anchor should be supported by its own immediate body, not
            # by body evidence after later packed list/index rows.
            break
        next_text = str(next_block.get("selected_text") or "").strip()
        body_score, _ = _body_paragraph_score(next_block)
        if body_score >= 1.6 or next_block.get("role") in {"body", "label_or_speaker"}:
            follow_chars += len(next_text)
            follow_body_blocks += 1
    if follow_chars >= 80:
        score += 1.6
        reasons.append("following_body_text")
    if follow_body_blocks >= 2:
        score += 0.8
        reasons.append("multiple_following_body_blocks")
    list_score, _ = _list_entry_score(block)
    if list_score >= LIST_ENTRY_THRESHOLD and follow_body_blocks == 0:
        score -= 1.0
        reasons.append("list_like_without_following_body")
    return score, reasons


def _table_cell_score(block: dict[str, Any]) -> tuple[float, list[str]]:
    text = _single_line(str(block.get("selected_text") or ""))
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    width_ratio = _float_value(features.get("width_ratio"))
    line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
    score = 0.0
    reasons: list[str] = []
    if width_ratio is not None and width_ratio <= 0.18:
        score += 1.0
        reasons.append("narrow_column_cell")
    if line_count <= 4 and len(text) <= 90:
        score += 0.7
        reasons.append("compact_cell_text")
    if DATE_LIKE_RE.search(text) or _trailing_heading_number(text) is not None:
        score += 0.5
        reasons.append("date_or_numeric_cell")
    return score, reasons


def _first_body_anchor_index(blocks: list[dict[str, Any]]) -> int | None:
    for index, block in enumerate(blocks):
        features = block.get("grouping_features") if isinstance(block.get("grouping_features"), dict) else {}
        anchor_score = _float_value(features.get("body_anchor_score"))
        anchor_reasons = features.get("body_anchor_reasons") if isinstance(features.get("body_anchor_reasons"), list) else []
        is_list_like = bool(features.get("list_entry_like"))
        has_following_body = "following_body_text" in anchor_reasons or "multiple_following_body_blocks" in anchor_reasons
        if _looks_like_front_matter_index_navigation_block(block):
            continue
        if anchor_score is not None and anchor_score >= BODY_ANCHOR_THRESHOLD and (not is_list_like or has_following_body):
            return index
    return None


def _front_matter_navigation_scan_limit_index(blocks: list[dict[str, Any]], *, fallback_index: int | None) -> int | None:
    for index, block in enumerate(blocks):
        if _starts_supported_body_run(blocks=blocks, index=index):
            return index
    return fallback_index


def _starts_supported_body_run(*, blocks: list[dict[str, Any]], index: int) -> bool:
    block = blocks[index]
    features = block.get("grouping_features") if isinstance(block.get("grouping_features"), dict) else {}
    if _looks_like_front_matter_index_navigation_block(block):
        return False
    anchor_score = _float_value(features.get("body_anchor_score"))
    anchor_reasons = features.get("body_anchor_reasons") if isinstance(features.get("body_anchor_reasons"), list) else []
    if anchor_score is None or anchor_score < BODY_ANCHOR_THRESHOLD:
        return False
    if features.get("list_entry_like"):
        return False
    if "following_body_text" not in anchor_reasons and "multiple_following_body_blocks" not in anchor_reasons:
        return False
    body_like_after = 0
    for next_block in blocks[index + 1 : index + 8]:
        next_features = next_block.get("grouping_features") if isinstance(next_block.get("grouping_features"), dict) else {}
        if next_features.get("list_entry_like") or next_block.get("role") == "candidate_start":
            return body_like_after >= 2
        body_score = _float_value(next_features.get("body_paragraph_score")) or 0.0
        next_text = str(next_block.get("selected_text") or "").strip()
        if body_score >= 1.6 or (next_block.get("role") in {"body", "label_or_speaker"} and len(next_text) >= 12):
            body_like_after += 1
        if body_like_after >= 2:
            return True
    return False


def _detect_navigation_list_runs(blocks: list[dict[str, Any]], *, first_body_anchor_index: int | None) -> list[list[dict[str, Any]]]:
    if first_body_anchor_index is None:
        prefix = blocks
    else:
        prefix = blocks[:first_body_anchor_index]
    list_like_count = sum(1 for block in prefix if ((block.get("grouping_features") or {}).get("list_entry_like")))
    if list_like_count < 3:
        return []
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_page: int | None = None
    for index, block in enumerate(prefix):
        page = _int_value(block.get("page"), default=0)
        features = block.get("grouping_features") if isinstance(block.get("grouping_features"), dict) else {}
        include = not _starts_supported_body_run(blocks=blocks, index=index) and (bool(features.get("list_entry_like")) or (current and _looks_like_navigation_context_continuation(block)))
        if not include:
            if len([item for item in current if (item.get("grouping_features") or {}).get("list_entry_like")]) >= 2:
                runs.append(current)
            current = []
            current_page = None
            continue
        if current and current_page is not None and page != current_page:
            if len([item for item in current if (item.get("grouping_features") or {}).get("list_entry_like")]) >= 2:
                runs.append(current)
            current = []
        current.append(block)
        current_page = page
    if len([item for item in current if (item.get("grouping_features") or {}).get("list_entry_like")]) >= 2:
        runs.append(current)
    return runs


def _detect_dense_early_navigation_list_runs(blocks: list[dict[str, Any]], *, first_body_anchor_index: int | None = None) -> list[list[dict[str, Any]]]:
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_page: int | None = None
    scan_blocks = blocks if first_body_anchor_index is None else blocks[:first_body_anchor_index]
    for index, block in enumerate(scan_blocks):
        page = _int_value(block.get("page"), default=0)
        include = page > 0 and page <= MAX_FRONT_MATTER_NAVIGATION_PAGES and not _starts_supported_body_run(blocks=blocks, index=index) and (_is_navigation_list_like_block(block) or (current and _looks_like_navigation_context_continuation(block)))
        if not include:
            _append_navigation_run_if_dense(runs, current)
            current = []
            current_page = None
            continue
        if current and current_page is not None and page != current_page:
            _append_navigation_run_if_dense(runs, current)
            current = []
        current.append(block)
        current_page = page
    _append_navigation_run_if_dense(runs, current)
    return runs


def _append_navigation_run_if_dense(runs: list[list[dict[str, Any]]], current: list[dict[str, Any]]) -> None:
    list_like_count = sum(1 for block in current if _is_navigation_list_like_block(block))
    if list_like_count >= 3:
        runs.append(list(current))


def _dedupe_navigation_runs(runs: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    seen: set[str] = set()
    for run in runs:
        filtered: list[dict[str, Any]] = []
        for block in run:
            block_id = str(block.get("block_id") or "")
            if not block_id or block_id in seen:
                continue
            filtered.append(block)
        if sum(1 for block in filtered if _is_navigation_list_like_block(block)) >= 2:
            out.append(filtered)
            seen.update(str(block.get("block_id") or "") for block in filtered)
    return out


def _is_navigation_list_like_block(block: dict[str, Any]) -> bool:
    features = block.get("grouping_features") if isinstance(block.get("grouping_features"), dict) else {}
    if bool(features.get("list_entry_like")) or _looks_like_front_matter_index_navigation_block(block):
        return True
    text = _single_line(str(block.get("selected_text") or ""))
    visual = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    line_count = _int_value(visual.get("line_count"), default=max(1, text.count("\n") + 1))
    page = _int_value(block.get("page"), default=0)
    list_score = _float_value(features.get("list_entry_score")) or 0.0
    return page > 0 and page <= MAX_FRONT_MATTER_NAVIGATION_PAGES and block.get("role") == "candidate_start" and list_score >= 3.0 and len(text) <= 220 and line_count <= 6


def _looks_like_front_matter_index_navigation_block(block: dict[str, Any]) -> bool:
    text = _single_line(str(block.get("selected_text") or ""))
    display_text = _single_line(str(block.get("display_selected_text") or block.get("display_text") or ""))
    searchable_text = " ".join(part for part in (text, display_text) if part)
    if not searchable_text or len(searchable_text) > 520:
        return False
    if not INDEX_ENTRY_RE.search(searchable_text):
        return False
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    line_count = _int_value(features.get("line_count"), default=max(1, str(block.get("selected_text") or block.get("display_selected_text") or "").count("\n") + 1))
    return line_count <= 6


def _looks_like_navigation_context_continuation(block: dict[str, Any]) -> bool:
    text = _single_line(str(block.get("selected_text") or ""))
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
    punctuation_count = len(BODY_PUNCTUATION_RE.findall(text))
    return len(text) <= 160 and line_count <= 6 and punctuation_count <= 3


def _promote_body_start_anchors(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in blocks:
        features = block.get("grouping_features") if isinstance(block.get("grouping_features"), dict) else {}
        promotable_anchor = bool(features.get("body_start_anchor")) or _looks_like_visually_strong_global_heading_block(block)
        if block.get("role") != "candidate_start" and promotable_anchor and not _looks_like_page_chrome_block(block) and not _looks_like_promoted_body_continuation(block):
            promoted = {**block}
            promoted["role"] = "candidate_start"
            promoted["role_reasons"] = [*(promoted.get("role_reasons") or []), "promoted_generic_body_start_anchor"]
            promoted["body_char_count"] = 0
            out.append(promoted)
        else:
            out.append(block)
    return out


def _group_blocks_into_candidates(blocks: list[dict[str, Any]], *, min_body_chars: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    ignored_before_first = 0
    for block in blocks:
        is_start = block.get("role") == "candidate_start"
        if is_start and current and _candidate_body_chars(current) >= min_body_chars and _looks_like_internal_body_enumeration(block):
            block = {**block}
            block["role"] = "body"
            block["role_reasons"] = [*(block.get("role_reasons") or []), "downgraded_internal_enumerated_body_item"]
            block["body_char_count"] = len(str(block.get("selected_text") or "").strip())
            is_start = False
        if is_start:
            if current and _should_split_before_start(current=current, next_start=block, min_body_chars=min_body_chars):
                candidates.append(_candidate_from_blocks(index=len(candidates) + 1, blocks=current, min_body_chars=min_body_chars))
                current = [block]
            elif current:
                current.append(block)
            else:
                current = [block]
            continue
        if not current:
            ignored_before_first += 1
            continue
        current.append(block)
    if current:
        candidates.append(_candidate_from_blocks(index=len(candidates) + 1, blocks=current, min_body_chars=min_body_chars))
    if ignored_before_first and candidates:
        candidates[0].setdefault("grouping_notes", []).append(f"Ignored {ignored_before_first} semantic blocks before the first body start anchor.")
    return candidates


def _should_split_before_start(*, current: list[dict[str, Any]], next_start: dict[str, Any], min_body_chars: int) -> bool:
    body_chars = _candidate_body_chars(current)
    if body_chars >= min_body_chars and _looks_like_same_page_child_heading_under_parent_section(current=current, next_start=next_start):
        return False
    if body_chars >= min_body_chars:
        return True
    if body_chars < 40:
        return False
    if not any(block.get("role") == "candidate_start" for block in current):
        return False
    return _is_visually_strong_body_heading(next_start) or _looks_like_visually_strong_global_heading_block(next_start)


def _looks_like_same_page_child_heading_under_parent_section(*, current: list[dict[str, Any]], next_start: dict[str, Any]) -> bool:
    if not current or not _current_starts_with_short_parent_section_heading(current):
        return False
    current_pages = [_int_value(block.get("page"), default=0) for block in current if _int_value(block.get("page"), default=0) > 0]
    next_page = _int_value(next_start.get("page"), default=0)
    if not current_pages or next_page != max(current_pages):
        return False
    return _looks_like_low_confidence_child_heading(next_start)


def _current_starts_with_short_parent_section_heading(current: list[dict[str, Any]]) -> bool:
    heading_blocks = [block for block in current if block.get("role") == "candidate_start"]
    if not heading_blocks:
        return False
    first_heading = heading_blocks[0]
    text = _single_line(str(first_heading.get("selected_text") or ""))
    features = first_heading.get("visual_features") if isinstance(first_heading.get("visual_features"), dict) else {}
    width_ratio = _float_value(features.get("width_ratio"))
    line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
    max_font_size = _float_value(features.get("max_font_size"))
    return (
        len(text) <= 90
        and line_count <= 2
        and width_ratio is not None
        and width_ratio <= 0.35
        and max_font_size is not None
        and max_font_size >= 12.5
    )


def _looks_like_low_confidence_child_heading(block: dict[str, Any]) -> bool:
    if block.get("role") != "candidate_start":
        return False
    features = block.get("grouping_features") if isinstance(block.get("grouping_features"), dict) else {}
    anchor_reasons = features.get("body_anchor_reasons") if isinstance(features.get("body_anchor_reasons"), list) else []
    if "visually_strong_heading" in anchor_reasons:
        return False
    anchor_score = _float_value(features.get("body_anchor_score")) or 0.0
    visual = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    max_font_size = _float_value(visual.get("max_font_size"))
    width_ratio = _float_value(visual.get("width_ratio"))
    return anchor_score <= BODY_ANCHOR_THRESHOLD and (max_font_size is None or max_font_size <= 12.2) and (width_ratio is None or width_ratio >= 0.30)


def _navigation_candidates_from_runs(runs: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, blocks in enumerate(runs, start=1):
        pages = [int(block.get("page") or 0) for block in blocks if int(block.get("page") or 0) > 0]
        selected_text = _joined_block_text(blocks, preferred_key="display_selected_text")
        selected_text_before_cleanup = _joined_block_text(blocks, preferred_key="display_selected_text_before_cleanup")
        grouping_selected_text = _joined_block_text(blocks, preferred_key="selected_text")
        raw_native_text = "\n\n".join(str(block.get("raw_native_text") or "").strip() for block in blocks if str(block.get("raw_native_text") or "").strip())
        list_like_count = sum(1 for block in blocks if ((block.get("grouping_features") or {}).get("list_entry_like")))
        candidate = {
            "navigation_candidate_id": f"navigation_{index:04d}",
            "status": "navigation_list_candidate",
            "status_reason": "Dense numbered/list-like material was detected before the first body-start anchor and saved separately instead of merging into body candidates.",
            "accepted_as_ground_truth": False,
            "research_use": "agenda_index_or_table_of_contents_navigation_evidence_only",
            "page_range": [min(pages), max(pages)] if pages else [],
            "pages": sorted(set(pages)),
            "block_ids": [str(block.get("block_id") or "") for block in blocks],
            "selected_text": selected_text,
            "selected_text_before_display_cleanup": selected_text_before_cleanup,
            "selected_text_source": "block_display_selected_text_joined_for_downstream_readability_grouping_uses_block_selected_text",
            "text_cleanup": cleanup_display_text_record(before=selected_text_before_cleanup, after=selected_text),
            "grouping_selected_text": grouping_selected_text,
            "raw_native_text": raw_native_text,
            "rtl_normalized_text": "\n\n".join(str(block.get("rtl_normalized_text") or "").strip() for block in blocks if str(block.get("rtl_normalized_text") or "").strip()),
            "list_run_features": {
                "block_count": len(blocks),
                "list_like_block_count": list_like_count,
                "list_like_ratio": round(list_like_count / max(1, len(blocks)), 3),
                "rule": "Generic pre-body dense list-run detection using numbering density, geometry, and body-anchor position.",
            },
            "blocks": blocks,
            "navigation_crops": [],
            "navigation_candidate_dir_path": "",
            "navigation_candidate_text_path": "",
            "navigation_candidate_metadata_path": "",
            "suggested_generic_next_step": "Open the navigation crop beside the original rendered page. If it is actually body discussion, adjust generic body-anchor/list-run thresholds and rerun this one PDF.",
        }
        candidates.append(candidate)
    return candidates


def _candidate_from_blocks(*, index: int, blocks: list[dict[str, Any]], min_body_chars: int) -> dict[str, Any]:
    candidate_id = f"candidate_{index:04d}"
    pages = [int(block.get("page") or 0) for block in blocks if int(block.get("page") or 0) > 0]
    heading_blocks = [block for block in blocks if block.get("role") == "candidate_start"]
    body_chars = _candidate_body_chars(blocks)
    quality_flags = sorted({flag for block in blocks for flag in block.get("quality_flags") or []})
    contamination = _candidate_contamination_status(blocks)
    status, status_reason = _candidate_status(heading_count=len(heading_blocks), body_chars=body_chars, min_body_chars=min_body_chars, quality_flags=quality_flags, contamination=contamination)
    selected_text = _joined_block_text(blocks, preferred_key="display_selected_text")
    selected_text_before_cleanup = _joined_block_text(blocks, preferred_key="display_selected_text_before_cleanup")
    grouping_selected_text = _joined_block_text(blocks, preferred_key="selected_text")
    raw_native_text = "\n\n".join(str(block.get("raw_native_text") or "").strip() for block in blocks if str(block.get("raw_native_text") or "").strip())
    rtl_normalized_text = "\n\n".join(str(block.get("rtl_normalized_text") or "").strip() for block in blocks if str(block.get("rtl_normalized_text") or "").strip())
    return {
        "candidate_id": candidate_id,
        "status": status,
        "status_reason": status_reason,
        "accepted_as_ground_truth": False,
        "semantic_judgement": {
            "status": "plausible_agenda_topic_candidate" if status == "plausible_candidate" else "needs_review",
            "reason": _semantic_reason(heading_count=len(heading_blocks), body_chars=body_chars, min_body_chars=min_body_chars, contamination=contamination),
            "rule": "Generic body-anchor grouping from native headings plus following semantic body blocks; no municipality-specific wording.",
        },
        "visual_judgement": {
            "status": "visual_evidence_saved_pending_human_judgement",
            "reason": "Rendered group crops are saved from the original PDF page image. They must be visually checked before treating the candidate as correct.",
        },
        "page_range": [min(pages), max(pages)] if pages else [],
        "pages": sorted(set(pages)),
        "block_ids": [str(block.get("block_id") or "") for block in blocks],
        "heading_block_ids": [str(block.get("block_id") or "") for block in heading_blocks],
        "heading_text": _joined_block_text(heading_blocks[:3], preferred_key="display_selected_text", separator="\n"),
        "selected_text": selected_text,
        "selected_text_before_display_cleanup": selected_text_before_cleanup,
        "selected_text_source": "block_display_selected_text_joined_for_downstream_readability_grouping_uses_block_selected_text",
        "text_cleanup": cleanup_display_text_record(before=selected_text_before_cleanup, after=selected_text),
        "grouping_selected_text": grouping_selected_text,
        "raw_native_text": raw_native_text,
        "rtl_normalized_text": rtl_normalized_text,
        "body_char_count": body_chars,
        "selected_char_count": len(selected_text),
        "quality_flags": quality_flags,
        "grouping_diagnostics": {"contamination": contamination, "body_anchor_threshold": BODY_ANCHOR_THRESHOLD, "list_entry_threshold": LIST_ENTRY_THRESHOLD},
        "blocks": blocks,
        "group_crops": [],
        "candidate_dir_path": "",
        "candidate_text_path": "",
        "candidate_metadata_path": "",
    }


def _joined_block_text(blocks: list[dict[str, Any]], *, preferred_key: str, separator: str = "\n\n") -> str:
    parts: list[str] = []
    for block in blocks:
        text = str(block.get(preferred_key) or "").strip()
        if not text and preferred_key != "selected_text":
            text = str(block.get("selected_text") or "").strip()
        if text:
            parts.append(text)
    return separator.join(parts)


def _candidate_contamination_status(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    if _has_visually_supported_table_body(blocks):
        return {
            "status": "ok",
            "reason": "A visually strong body heading is followed by compact grid/table-like body cells.",
        }
    first_blocks = blocks[:6]
    list_like_prefix = [block for block in first_blocks if ((block.get("grouping_features") or {}).get("list_entry_like"))]
    body_like_later = [block for block in blocks[3:] if ((block.get("grouping_features") or {}).get("body_paragraph_like"))]
    heading_count = sum(1 for block in first_blocks if block.get("role") == "candidate_start")
    if len(list_like_prefix) >= 3 and body_like_later:
        return {
            "status": "needs_review_possible_list_body_merge",
            "reason": "Candidate starts with multiple dense list/index-like rows and later contains body-like text.",
            "raw_evidence": [str(block.get("selected_text") or "")[:240] for block in list_like_prefix[:4]],
            "suggested_generic_next_step": "Save the prefix as agenda_navigation_candidates and start the body candidate at the first visually supported body anchor.",
        }
    if heading_count >= 3 and not body_like_later:
        return {
            "status": "needs_review_navigation_list_only",
            "reason": "Candidate has many heading/list rows and no clear following body paragraph evidence.",
            "raw_evidence": [str(block.get("selected_text") or "")[:240] for block in first_blocks[:4]],
            "suggested_generic_next_step": "Treat this as navigation/list evidence unless visual judgement shows body discussion.",
        }
    return {"status": "ok", "reason": "No generic list/body contamination pattern was detected."}


def _has_visually_supported_table_body(blocks: list[dict[str, Any]]) -> bool:
    if not blocks or not (_is_visually_strong_body_heading(blocks[0]) or _looks_like_visually_strong_global_heading_block(blocks[0])):
        return False
    compact_cells = 0
    x_buckets: set[int] = set()
    y_buckets: set[int] = set()
    has_wide_table_band = False
    for block in blocks[1:]:
        text = _single_line(str(block.get("selected_text") or ""))
        features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
        width_ratio = _float_value(features.get("width_ratio"))
        line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
        x0_ratio = _float_value(features.get("x0_ratio"))
        y0_ratio = _float_value(features.get("y0_ratio"))
        if width_ratio is not None and width_ratio >= 0.45 and line_count <= 5 and len(text) <= 160:
            has_wide_table_band = True
        if width_ratio is None or width_ratio > 0.32 or line_count > 4 or len(text) > 110:
            continue
        compact_cells += 1
        if x0_ratio is not None:
            x_buckets.add(int(x0_ratio * 20))
        if y0_ratio is not None:
            y_buckets.add(int(y0_ratio * 30))
    # Decision tables often contain many short cells.  Require both a wide
    # table/header band and repeated compact cells so a plain agenda list does
    # not get mistaken for body content.
    return compact_cells >= 6 and has_wide_table_band and (not x_buckets or len(x_buckets) >= 2) and (not y_buckets or len(y_buckets) >= 2)


def _looks_like_visually_strong_global_heading_block(block: dict[str, Any]) -> bool:
    text = _single_line(str(block.get("rtl_normalized_text") or block.get("raw_native_text") or block.get("selected_text") or block.get("text") or ""))
    if not text or not LETTER_RE.search(text):
        return False
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    page_number = _int_value(block.get("page"), default=0)
    width_ratio = _float_value(features.get("width_ratio"))
    line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
    max_font_size = _float_value(features.get("max_font_size"))
    y0_ratio = _float_value(features.get("y0_ratio"))
    if y0_ratio is not None and y0_ratio > 0.88:
        return False
    if max_font_size is None or max_font_size < 12.5:
        return False
    has_early_section_number = EARLY_SECTION_NUMBER_RE.search(text) is not None
    if y0_ratio is not None and y0_ratio <= 0.22 and line_count >= 3 and DATE_LIKE_RE.search(text) and not has_early_section_number:
        return False
    leading_number = _leading_heading_number(text)
    if leading_number is not None:
        if len(text) > 180:
            return False
        if width_ratio is not None and width_ratio < 0.20:
            return False
        return line_count <= 6
    trailing_number = _trailing_heading_number(text)
    if trailing_number is not None and trailing_number <= 999:
        if len(text) > 180 or line_count > 4:
            return False
        if DATE_LIKE_RE.search(text) and not has_early_section_number:
            return False
        return width_ratio is None or width_ratio >= 0.25
    if len(text) > 220 or line_count > 5:
        return False
    if width_ratio is not None and width_ratio < 0.55:
        if page_number <= 2 or width_ratio < 0.25:
            return False
    if DATE_LIKE_RE.search(text[:64]) and not has_early_section_number:
        return False
    return has_early_section_number or EMBEDDED_HEADING_NUMBER_RE.search(text) is not None


def _is_visually_strong_body_heading(block: dict[str, Any]) -> bool:
    grouping = block.get("grouping_features") if isinstance(block.get("grouping_features"), dict) else {}
    reasons = grouping.get("body_anchor_reasons") if isinstance(grouping.get("body_anchor_reasons"), list) else []
    visual = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    width_ratio = _float_value(visual.get("width_ratio"))
    max_font_size = _float_value(visual.get("max_font_size"))
    return "visually_strong_heading" in reasons and width_ratio is not None and width_ratio >= 0.45 and max_font_size is not None and max_font_size >= 12.5


def _candidate_status(*, heading_count: int, body_chars: int, min_body_chars: int, quality_flags: list[str], contamination: dict[str, Any]) -> tuple[str, str]:
    contamination_status = str(contamination.get("status") or "")
    if contamination_status.startswith("needs_review"):
        return contamination_status, str(contamination.get("reason") or "Candidate may mix navigation/list material with body text.")
    if heading_count <= 0:
        return "needs_review_no_heading", "The group has no structural heading/body-start anchor block."
    if body_chars < max(40, min_body_chars // 3):
        return "needs_review_too_short", "The group has a heading but too little following body text."
    if body_chars < min_body_chars:
        return "needs_review_short_candidate", "The group has a heading but limited body text."
    if quality_flags:
        return "needs_review_text_quality_flags", "The group has native text quality flags; visual crop review is required."
    return "plausible_candidate", "The group has a structural body-start anchor and enough following semantic body text."


def _semantic_reason(*, heading_count: int, body_chars: int, min_body_chars: int, contamination: dict[str, Any]) -> str:
    if str(contamination.get("status") or "") != "ok":
        return str(contamination.get("reason") or "Candidate needs review for possible list/body contamination.")
    if heading_count <= 0:
        return "No generic structural body-start anchor was found."
    if body_chars < min_body_chars:
        return f"A generic body-start anchor was found, but only {body_chars} body characters followed it."
    return f"A generic body-start anchor was found and {body_chars} body characters followed it."


def _render_candidate_group_crops(*, candidates: list[dict[str, Any]], input_pdf_path: Path, candidates_dir: Path, render_dpi: int, crop_margin_points: float) -> None:
    if not candidates:
        return
    with fitz.open(input_pdf_path) as document:
        for candidate in candidates:
            candidate_dir = candidates_dir / str(candidate["candidate_id"])
            candidate_dir.mkdir(parents=True, exist_ok=False)
            candidate["candidate_dir_path"] = _abs(candidate_dir)
            page_blocks: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for block in candidate.get("blocks") or []:
                page_number = _int_value(block.get("page"), default=0)
                if page_number > 0:
                    page_blocks[page_number].append(block)
            crops: list[dict[str, Any]] = []
            for page_number in sorted(page_blocks):
                if page_number < 1 or page_number > len(document):
                    continue
                page = document[page_number - 1]
                bbox = _union_bbox([block.get("bbox_pdf_points") or [] for block in page_blocks[page_number]])
                if not bbox:
                    continue
                clip = _expanded_rect(bbox=bbox, page_rect=page.rect, margin_points=crop_margin_points)
                crop_path = candidate_dir / f"{candidate['candidate_id']}_page_{page_number:03d}_group_crop.png"
                _render_page(page=page, output_path=crop_path, dpi=render_dpi, clip=clip)
                crops.append({"page": page_number, "group_crop_image_path": _abs(crop_path), "crop_bbox_pdf_points": _rect_to_bbox(clip), "source_block_ids": [str(block.get("block_id") or "") for block in page_blocks[page_number]], "visual_evidence_type": "rendered_original_pdf_group_crop"})
            candidate["group_crops"] = crops


def _render_navigation_group_crops(*, navigation_candidates: list[dict[str, Any]], input_pdf_path: Path, navigation_dir: Path, render_dpi: int, crop_margin_points: float) -> None:
    if not navigation_candidates:
        return
    with fitz.open(input_pdf_path) as document:
        for candidate in navigation_candidates:
            candidate_dir = navigation_dir / str(candidate["navigation_candidate_id"])
            candidate_dir.mkdir(parents=True, exist_ok=False)
            candidate["navigation_candidate_dir_path"] = _abs(candidate_dir)
            page_blocks: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for block in candidate.get("blocks") or []:
                page_number = _int_value(block.get("page"), default=0)
                if page_number > 0:
                    page_blocks[page_number].append(block)
            crops: list[dict[str, Any]] = []
            for page_number in sorted(page_blocks):
                if page_number < 1 or page_number > len(document):
                    continue
                page = document[page_number - 1]
                bbox = _union_bbox([block.get("bbox_pdf_points") or [] for block in page_blocks[page_number]])
                if not bbox:
                    continue
                clip = _expanded_rect(bbox=bbox, page_rect=page.rect, margin_points=crop_margin_points)
                crop_path = candidate_dir / f"{candidate['navigation_candidate_id']}_page_{page_number:03d}_navigation_crop.png"
                _render_page(page=page, output_path=crop_path, dpi=render_dpi, clip=clip)
                crops.append({"page": page_number, "navigation_crop_image_path": _abs(crop_path), "crop_bbox_pdf_points": _rect_to_bbox(clip), "source_block_ids": [str(block.get("block_id") or "") for block in page_blocks[page_number]], "visual_evidence_type": "rendered_original_pdf_navigation_crop"})
            candidate["navigation_crops"] = crops


def _write_candidate_files(candidates: list[dict[str, Any]], *, candidates_dir: Path) -> None:
    for candidate in candidates:
        candidate_dir_text = str(candidate.get("candidate_dir_path") or "")
        if not candidate_dir_text:
            continue
        candidate_dir = Path(candidate_dir_text)
        text_path = candidate_dir / f"{candidate['candidate_id']}_text.txt"
        metadata_path = candidate_dir / f"{candidate['candidate_id']}_metadata.json"
        candidate["candidate_text_path"] = _abs(text_path)
        candidate["candidate_metadata_path"] = _abs(metadata_path)
        text_path.write_text(_candidate_text_file(candidate), encoding="utf-8")
        metadata_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_navigation_files(navigation_candidates: list[dict[str, Any]], *, navigation_dir: Path) -> None:
    for candidate in navigation_candidates:
        candidate_dir_text = str(candidate.get("navigation_candidate_dir_path") or "")
        if not candidate_dir_text:
            continue
        candidate_dir = Path(candidate_dir_text)
        text_path = candidate_dir / f"{candidate['navigation_candidate_id']}_text.txt"
        metadata_path = candidate_dir / f"{candidate['navigation_candidate_id']}_metadata.json"
        candidate["navigation_candidate_text_path"] = _abs(text_path)
        candidate["navigation_candidate_metadata_path"] = _abs(metadata_path)
        text_path.write_text(_navigation_text_file(candidate), encoding="utf-8")
        metadata_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")


def _candidate_text_file(candidate: dict[str, Any]) -> str:
    lines = [f"candidate_id: {candidate.get('candidate_id')}", f"status: {candidate.get('status')}", f"status_reason: {candidate.get('status_reason')}", f"page_range: {json.dumps(candidate.get('page_range') or [], ensure_ascii=False)}", "group_crops:"]
    for crop in candidate.get("group_crops") or []:
        lines.append(f"- page {crop.get('page')}: {crop.get('group_crop_image_path')}")
    lines.extend(["", "heading_text:", str(candidate.get("heading_text") or ""), "", "selected_text:", str(candidate.get("selected_text") or ""), "", "raw_native_text:", str(candidate.get("raw_native_text") or "")])
    return "\n".join(lines).rstrip() + "\n"


def _navigation_text_file(candidate: dict[str, Any]) -> str:
    lines = [f"navigation_candidate_id: {candidate.get('navigation_candidate_id')}", f"status: {candidate.get('status')}", f"status_reason: {candidate.get('status_reason')}", f"page_range: {json.dumps(candidate.get('page_range') or [], ensure_ascii=False)}", "navigation_crops:"]
    for crop in candidate.get("navigation_crops") or []:
        lines.append(f"- page {crop.get('page')}: {crop.get('navigation_crop_image_path')}")
    lines.extend(["", "selected_text:", str(candidate.get("selected_text") or ""), "", "raw_native_text:", str(candidate.get("raw_native_text") or "")])
    return "\n".join(lines).rstrip() + "\n"


def _quality_judgement(*, status: str, status_reason: str, candidates: list[dict[str, Any]], navigation_candidates: list[dict[str, Any]], skipped_pages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": status,
        "reason": status_reason,
        "human_judgement_required": True,
        "raw_evidence": {
            "candidate_count": len(candidates),
            "navigation_candidate_count": len(navigation_candidates),
            "candidate_previews": [
                {"candidate_id": candidate.get("candidate_id"), "status": candidate.get("status"), "page_range": candidate.get("page_range"), "heading_text": candidate.get("heading_text"), "selected_text_preview": _preview(candidate.get("selected_text")), "group_crop_image_paths": [crop.get("group_crop_image_path") for crop in candidate.get("group_crops") or []]}
                for candidate in candidates[:10]
            ],
            "navigation_previews": [
                {"navigation_candidate_id": candidate.get("navigation_candidate_id"), "status": candidate.get("status"), "page_range": candidate.get("page_range"), "selected_text_preview": _preview(candidate.get("selected_text")), "navigation_crop_image_paths": [crop.get("navigation_crop_image_path") for crop in candidate.get("navigation_crops") or []]}
                for candidate in navigation_candidates[:10]
            ],
            "skipped_pages": skipped_pages,
        },
        "suggested_generic_next_step": "Open the HTML report and compare body candidate crops plus navigation crops against original rendered pages before adapter Step 4/5.",
    }


def _audit_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    out = ["# Local Native Agenda/Topic Candidate Audit", "", f"Status: {payload.get('status')}", f"Reason: {payload.get('status_reason')}", f"Input PDF: {payload.get('input_pdf_path')}", f"Evidence folder: {payload.get('evidence_folder_path')}", f"Output run: {payload.get('output_run_dir_path')}", "", "## Summary", "", f"- Pages: {summary.get('page_count', 0)}", f"- Native semantic blocks: {summary.get('native_semantic_block_count', 0)}", f"- Body candidates: {summary.get('candidate_count', 0)}", f"- Navigation candidates: {summary.get('navigation_candidate_count', 0)}", f"- Needs-review body candidates: {summary.get('needs_review_candidate_count', 0)}", "", "## Body Candidates", ""]
    for candidate in payload.get("candidates") or []:
        out.extend([f"### {candidate.get('candidate_id')}", "", f"Status: {candidate.get('status')}", f"Reason: {candidate.get('status_reason')}", f"Pages: {json.dumps(candidate.get('pages') or [], ensure_ascii=False)}", f"Heading: {candidate.get('heading_text')}", ""])
    if payload.get("agenda_navigation_candidates"):
        out.extend(["## Agenda Navigation Candidates", ""])
        for candidate in payload.get("agenda_navigation_candidates") or []:
            out.extend([f"### {candidate.get('navigation_candidate_id')}", "", f"Status: {candidate.get('status')}", f"Reason: {candidate.get('status_reason')}", f"Pages: {json.dumps(candidate.get('pages') or [], ensure_ascii=False)}", ""])
    return "\n".join(out).rstrip() + "\n"


def _looks_like_structural_heading(*, text: str, line_count: int, body_punctuation_count: int) -> bool:
    if line_count > 6 or len(text) > 240:
        return False
    if body_punctuation_count > 3 and len(text) > 100:
        return False
    if not HEADING_NUMBER_RE.search(text):
        return False
    leading_number = _leading_heading_number(text)
    trailing_number = _trailing_heading_number(text)
    if leading_number is not None and 1900 <= leading_number <= 2099 and len(text) > 40:
        return False
    if leading_number is None and trailing_number is not None and len(text) > 80:
        return False
    return True


def _looks_like_short_label(*, text: str, line_count: int) -> bool:
    if line_count > 2 or len(text) > 80:
        return False
    stripped = text.strip()
    return stripped.endswith(":") or " - " in stripped or "–" in stripped or "—" in stripped


def _looks_like_narrow_numeric_table_cell(*, block: dict[str, Any], text: str) -> bool:
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    width_ratio = _float_value(features.get("width_ratio"))
    line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
    leading_number = _leading_heading_number(text)
    trailing_number = _trailing_heading_number(text)
    narrow = width_ratio is not None and width_ratio <= 0.18
    compact = len(text) <= 90 and line_count <= 4
    compact_date_cell = DATE_LIKE_RE.search(text) is not None
    leading_numeric_cell = leading_number is not None and leading_number >= 100
    large_numeric_fragment = (leading_number is not None and leading_number >= 1000) or (trailing_number is not None and trailing_number >= 1000)
    trailing_numeric_cell = leading_number is None and trailing_number is not None
    return compact and narrow and (compact_date_cell or leading_numeric_cell or large_numeric_fragment or trailing_numeric_cell)


def _looks_like_numbered_body_continuation(*, block: dict[str, Any], text: str, body_punctuation_count: int) -> bool:
    leading_number = _leading_heading_number(text)
    if leading_number is None or leading_number > 20:
        return False
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    width_ratio = _float_value(features.get("width_ratio"))
    max_font_size = _float_value(features.get("max_font_size"))
    line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
    punctuation_density = body_punctuation_count + text.count(",") + text.count("،") + text.count(";")
    return (
        len(text) >= 70
        and line_count <= 4
        and (width_ratio is None or width_ratio >= 0.45)
        and (max_font_size is None or max_font_size <= 12.2)
        and punctuation_density >= 1
    )


def _looks_like_wide_date_like_body_continuation(*, block: dict[str, Any], text: str, line_count: int, body_punctuation_count: int) -> bool:
    if DATE_LIKE_RE.search(text) is None or _leading_heading_number(text) is None:
        return False
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    width_ratio = _float_value(features.get("width_ratio"))
    max_font_size = _float_value(features.get("max_font_size"))
    return (
        len(text) <= 180
        and line_count <= 2
        and body_punctuation_count >= 1
        and width_ratio is not None
        and width_ratio >= 0.60
        and (max_font_size is None or max_font_size <= 12.2)
    )


def _looks_like_promoted_body_continuation(block: dict[str, Any]) -> bool:
    text = _single_line(str(block.get("selected_text") or ""))
    if not text:
        return False
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    width_ratio = _float_value(features.get("width_ratio"))
    line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
    body_punctuation_count = len(BODY_PUNCTUATION_RE.findall(text))
    if (
        block.get("role") == "body"
        and len(text) >= 70
        and line_count <= 4
        and (width_ratio is None or width_ratio >= 0.45)
        and ("?" in text or body_punctuation_count >= 2)
        and not _looks_like_structural_heading(text=text, line_count=line_count, body_punctuation_count=body_punctuation_count)
        and not _has_explicit_global_heading_shape(text)
    ):
        return True
    if _looks_like_visually_strong_global_heading_block(block):
        return False
    if DATE_LIKE_RE.search(text) and len(text) >= 70 and body_punctuation_count >= 1:
        return True
    return False


def _has_explicit_global_heading_shape(text: str) -> bool:
    return EARLY_SECTION_NUMBER_RE.search(text) is not None or EMBEDDED_HEADING_NUMBER_RE.search(text) is not None


def _looks_like_short_trailing_number_heading(*, block: dict[str, Any], text: str, line_count: int) -> bool:
    if _int_value(block.get("page"), default=0) == 1:
        return False
    trailing_number = _trailing_heading_number(text)
    if trailing_number is None or not LETTER_RE.search(text):
        return False
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    width_ratio = _float_value(features.get("width_ratio"))
    max_font_size = _float_value(features.get("max_font_size"))
    return len(text) <= 90 and line_count <= 2 and width_ratio is not None and width_ratio >= 0.10 and max_font_size is not None and max_font_size >= 12.5


def _looks_like_internal_body_enumeration(block: dict[str, Any]) -> bool:
    text = _single_line(str(block.get("selected_text") or ""))
    leading_number = _leading_heading_number(text)
    if leading_number is None or leading_number > 20:
        return False
    trailing_number = _trailing_heading_number(text)
    if trailing_number is not None and trailing_number > 20 and not 1900 <= trailing_number <= 2099:
        return False
    if _is_visually_strong_body_heading(block):
        return False
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1))
    width_ratio = _float_value(features.get("width_ratio"))
    max_font_size = _float_value(features.get("max_font_size"))
    if width_ratio is not None and width_ratio <= 0.18 and line_count <= 7 and len(text) <= 180 and (max_font_size is None or max_font_size <= 12.2):
        return True
    if width_ratio is not None and width_ratio <= 0.16 and line_count <= 2 and len(text) <= 30:
        return True
    numbers = [_int_value(match.group(0), default=-1) for match in NUMBER_TOKEN_RE.finditer(text)]
    later_numbers = [number for number in numbers[1:] if number >= 0]
    has_numeric_reference_density = len(later_numbers) >= 2 or any(number >= 1000 for number in later_numbers) or any(1900 <= number <= 2099 for number in later_numbers)
    punctuation_count = len(BODY_PUNCTUATION_RE.findall(text)) + text.count(",")
    if len(text) > 60 and ("?" in text or punctuation_count >= 2 or DATE_LIKE_RE.search(text) is not None):
        return True
    return line_count <= 5 and len(text) >= 14 and has_numeric_reference_density


def _looks_like_nested_numbered_body_clause(*, block: dict[str, Any], text: str, line_count: int) -> bool:
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    width_ratio = _float_value(features.get("width_ratio"))
    max_font_size = _float_value(features.get("max_font_size"))
    if max_font_size is not None and max_font_size >= 12.5:
        return False
    if line_count > 4 or len(text) > 180:
        return False
    leading_number = _leading_heading_number(text)
    trailing_number = _trailing_heading_number(text)
    has_small_number = (leading_number is not None and leading_number <= 20) or (trailing_number is not None and trailing_number <= 20)
    if not has_small_number:
        return False
    combined = " ".join(
        str(value or "")
        for value in (
            text,
            block.get("display_text"),
            block.get("rtl_normalized_text"),
            block.get("raw_native_text"),
            block.get("text"),
        )
    )
    if not PARENTHETICAL_ENUMERATOR_RE.search(combined):
        return False
    # Nested legal clauses often look like numbered headings in native text,
    # but visually they are normal-size paragraph lines inside the current item.
    return width_ratio is None or width_ratio >= 0.16


def _body_char_count(*, role: str, selected_text: str) -> int:
    if role in {"candidate_start", "label_or_speaker", "context_before_first_topic"}:
        return 0
    return len(selected_text.strip())


def _candidate_body_chars(blocks: list[dict[str, Any]]) -> int:
    return sum(_int_value(block.get("body_char_count"), default=0) for block in blocks)


def _leading_heading_number(text: str) -> int | None:
    match = LEADING_HEADING_NUMBER_RE.search(text)
    if not match:
        return None
    return _int_value(match.group(1), default=-1)


def _trailing_heading_number(text: str) -> int | None:
    match = TRAILING_HEADING_NUMBER_RE.search(text)
    if not match:
        return None
    return _int_value(match.group(1), default=-1)


def _excluded_context_record(*, block: dict[str, Any], reason: str) -> dict[str, Any]:
    return {"block_id": block.get("block_id"), "page": block.get("page"), "reason": reason, "semantic_scope": block.get("semantic_scope") or {}, "selected_text_preview": _preview(block.get("selected_text") or block.get("rtl_normalized_text") or block.get("raw_native_text") or block.get("text")), "bbox_crop_image_path": block.get("bbox_crop_image_path"), "bbox_text_path": block.get("bbox_text_path"), "page_image_path": block.get("page_image_path"), "grouping_features": block.get("grouping_features") or {}}


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
    expanded = fitz.Rect(rect.x0 - margin, rect.y0 - margin, rect.x1 + margin, rect.y1 + margin)
    return expanded & page_rect


def _render_page(*, page: Any, output_path: Path, dpi: int, clip: Any | None = None) -> None:
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False, clip=clip)
    pixmap.save(output_path)


def _rect_to_bbox(rect: Any) -> list[float]:
    return [round(float(rect.x0), 3), round(float(rect.y0), 3), round(float(rect.x1), 3), round(float(rect.y1), 3)]


def _bbox_y0(item: dict[str, Any]) -> float:
    bbox = item.get("bbox_pdf_points") if isinstance(item.get("bbox_pdf_points"), list) else []
    return _float_value(bbox[1]) or 0.0 if len(bbox) == 4 else 0.0


def _bbox_y1(item: dict[str, Any]) -> float:
    bbox = item.get("bbox_pdf_points") if isinstance(item.get("bbox_pdf_points"), list) else []
    return _float_value(bbox[3]) or 0.0 if len(bbox) == 4 else 0.0


def _bbox_x0_for_reading(item: dict[str, Any]) -> float:
    bbox = item.get("bbox_pdf_points") if isinstance(item.get("bbox_pdf_points"), list) else []
    return -(_float_value(bbox[0]) or 0.0) if len(bbox) == 4 else 0.0


def _new_output_run_dir(*, root: Path, run_name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    candidate = root / (_safe_name(run_name.strip(), default="run") if run_name.strip() else datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ"))
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        next_candidate = candidate.with_name(f"{candidate.name}_{suffix:02d}")
        if not next_candidate.exists():
            return next_candidate
        suffix += 1


def _safe_name(value: str, *, default: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())[:120].strip("._")
    return safe or default


def _display_text_for_downstream(text: str) -> str:
    """Clean deterministic bidi punctuation artifacts for display/downstream use only."""
    return cleanup_display_text(text)


def _text_cleanup_record(*, before: str, after: str) -> dict[str, Any]:
    return cleanup_display_text_record(before=before, after=after)


def _single_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _preview(value: Any, *, limit: int = 500) -> str:
    text = _single_line(str(value or ""))
    return text[:limit] + ("..." if len(text) > limit else "")


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
