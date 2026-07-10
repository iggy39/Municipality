#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import re
from pathlib import Path
import traceback
from typing import Any

try:
    import fitz
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyMuPDF is required. Install project dependencies first.") from exc

from municipality.docsegmentation_native.display_text_cleanup import cleanup_display_text
from municipality.docsegmentation_native.pdf_bbox_evidence import extract_pdf_bbox_evidence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_ROOT = PROJECT_ROOT / "rag_eval" / "results" / "rtl_display_cleanup_large_visual_review"
STORAGE_RAW_ROOT = PROJECT_ROOT / "storage" / "raw"
TEL_AVIV_ROOT = PROJECT_ROOT / "storage" / "raw" / "tree" / "tel_aviv" / "city_council_protocols"
ASHDOD_ROOT = PROJECT_ROOT / "storage" / "raw" / "ashdod" / "protocol_full"

HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
FAKE_UNDERLINE_RE = re.compile(r"(^|[\s\"'([{.])U\s*[\u0590-\u05FF]|[\u0590-\u05FF0-9\"'׳״]\s*U\s*[:：]?")
REVERSED_PAREN_RE = re.compile(r"\)\s*[^()\n]{0,120}[\u0590-\u05FF][^()\n]{0,120}\s*\(|\(\)\s*[^()\n]{0,120}[\u0590-\u05FF]")
QUOTE_DASH_RE = re.compile(r"[-–—]\s*[\"״][^\n\"״]{0,160}[\u0590-\u05FF]|(?<![\u0590-\u05FF])(?:גב|דר|מר|מס|רח|עמ)['׳](?=[\u0590-\u05FF0-9])|(?<![\u0590-\u05FF])[\u0590-\u05FF]{1,4}['׳](?=\d)")
GLUED_HEBREW_DIGIT_RE = re.compile(r"[\u05D0-\u05EA]\d|\d[\u05D0-\u05EA]")
TRUNCATED_DATE_FRAGMENT_RE = re.compile(r"(?<!\d)\d{1,2}/\d{3}(?!\d)")
LEADING_GERESH_HEBREW_RE = re.compile(r"(^|\n)\s*['׳](?=[\u0590-\u05FF])")
LEADING_COLON_HEBREW_LABEL_RE = re.compile(r"(^|\n)\s*:[\u0590-\u05FF]{1,20}(?=\s|$)")
WIDE_SPACED_HEBREW_WORD_RE = re.compile(r"(?:[\u0590-\u05FF]\s{2,}){3,}[\u0590-\u05FF]")

DISPLAY_TRUNCATED_DATE_REASON = "display_after_dirty_truncated_date_or_protocol_number_shape"
RESIDUAL_CLEANUP_REASON = "residual_suspicious_after_cleanup"
SKIPPED_TEXT_LAYER_SCOPE = "skipped_for_ocr_pipeline"
NON_OCR_PROBLEM_SCOPE = "non_ocr_cleanup_problem"
NON_OCR_REVIEW_SCOPE = "non_ocr_cleanup_review"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Large per-document visual review pack for Hebrew/RTL display cleanup.")
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--run-name", default="large_visual_review")
    parser.add_argument("--resume-run-dir", type=Path, default=None)
    parser.add_argument("--dpi", type=int, default=160)
    parser.add_argument("--crop-margin-points", type=float, default=1.0)
    parser.add_argument("--max-documents", type=int, default=0, help="Optional safety limit. Default 0 means all inputs.")
    parser.add_argument("--input-progress-jsonl", type=Path, default=None, help="Optional previous progress.jsonl used to select rerun inputs.")
    parser.add_argument("--native-text-only-from-progress", action="store_true", help="With --input-progress-jsonl, rerun only documents that previously had native text blocks.")
    parser.add_argument("--skip-text-layer-issues", action="store_true", help="Mark cleaned-output slash/date/citation text-layer issues as skipped for a separate OCR/text-layer pipeline.")
    parser.add_argument("--prepare-only", action="store_true", help="Create the run folder and manifest, then stop before extraction.")
    args = parser.parse_args(argv)

    if args.resume_run_dir:
        run_dir = args.resume_run_dir.expanduser().resolve()
        manifest_path = run_dir / "input_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        inputs = [item for item in manifest.get("inputs") or [] if isinstance(item, dict)]
    else:
        run_dir = _new_run_dir(results_root=args.results_root.expanduser().resolve(), run_name=args.run_name)
        inputs, selection_policy = _selected_inputs(
            max_documents=max(0, int(args.max_documents)),
            input_progress_jsonl=args.input_progress_jsonl,
            native_text_only_from_progress=bool(args.native_text_only_from_progress),
        )
        manifest = {
            "schema_version": "municipality_rtl_display_cleanup_large_visual_review_manifest_v1",
            "status": "prepared",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "run_dir_path": _abs(run_dir),
            "input_count": len(inputs),
            "selection_policy": selection_policy,
            "source_progress_jsonl_path": _abs(args.input_progress_jsonl) if args.input_progress_jsonl else "",
            "native_text_only_from_progress": bool(args.native_text_only_from_progress),
            "skip_text_layer_issues": bool(args.skip_text_layer_issues),
            "inputs": inputs,
        }
        (run_dir / "input_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    evidence_root = run_dir / "evidence"
    doc_reports_dir = run_dir / "document_reports"
    evidence_root.mkdir(parents=True, exist_ok=True)
    doc_reports_dir.mkdir(parents=True, exist_ok=True)
    progress_path = run_dir / "progress.jsonl"
    index_json_path = run_dir / "large_visual_review_index.json"
    index_html_path = run_dir / "large_visual_review_index.html"

    print(
        json.dumps(
            {
                "status": "prepared",
                "run_dir_path": _abs(run_dir),
                "input_manifest_path": _abs(run_dir / "input_manifest.json"),
                "input_count": len(inputs),
                "prepare_only": bool(args.prepare_only),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if args.prepare_only:
        _write_index(manifest=manifest, records=_load_progress(progress_path), index_json_path=index_json_path, index_html_path=index_html_path, progress_path=progress_path)
        return 0

    records = _load_progress(progress_path)
    done_paths = {str(record.get("input_pdf_path") or "") for record in records}
    for ordinal, item in enumerate(inputs, start=1):
        input_path = str(item.get("input_pdf_path") or "")
        if input_path in done_paths:
            continue
        print(json.dumps({"status": "processing_started", "ordinal": ordinal, "input_pdf_path": input_path, "municipality": item.get("municipality")}, ensure_ascii=False), flush=True)
        record = _process_one(
            item=item,
            ordinal=ordinal,
            evidence_root=evidence_root,
            doc_reports_dir=doc_reports_dir,
            dpi=max(72, int(args.dpi)),
            crop_margin_points=max(0.0, float(args.crop_margin_points)),
            skip_text_layer_issues=bool(args.skip_text_layer_issues),
        )
        records.append(record)
        with progress_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        _write_index(manifest=manifest, records=records, index_json_path=index_json_path, index_html_path=index_html_path, progress_path=progress_path)
        print(_progress_line(record), flush=True)

    payload = _write_index(manifest=manifest, records=records, index_json_path=index_json_path, index_html_path=index_html_path, progress_path=progress_path)
    if args.skip_text_layer_issues:
        _write_non_ocr_scope_report(manifest=manifest, records=records, run_dir=run_dir)
    print(json.dumps({"status": payload["status"], "reason": payload["status_reason"], "index_html_path": _abs(index_html_path), "index_json_path": _abs(index_json_path), "document_count": len(records)}, ensure_ascii=False), flush=True)
    return 0 if payload["status"] in {"accepted", "needs_review", "partial"} else 2


def _all_inputs(*, max_documents: int) -> list[dict[str, Any]]:
    inputs = [_input_record_from_raw_path(path) for path in sorted(STORAGE_RAW_ROOT.rglob("*")) if path.is_file() and path.suffix.lower() in {".pdf", ".bin"}]
    return inputs[:max_documents] if max_documents else inputs


def _selected_inputs(*, max_documents: int, input_progress_jsonl: Path | None, native_text_only_from_progress: bool) -> tuple[list[dict[str, Any]], str]:
    if input_progress_jsonl is None:
        return (
            _all_inputs(max_documents=max_documents),
            "All .pdf and .bin files found recursively under /Users/igor/Desktop/projects/Municipality/storage/raw. Every changed or suspicious cleanup instance is included with no cap.",
        )
    inputs = _inputs_from_progress(progress_path=input_progress_jsonl.expanduser().resolve(), native_text_only=native_text_only_from_progress)
    if max_documents:
        inputs = inputs[:max_documents]
    scope = "documents that previously had native text blocks" if native_text_only_from_progress else "non-failed documents from the previous progress file"
    return (
        inputs,
        f"Inputs selected from {_abs(input_progress_jsonl)}: {scope}. OCR/no-native-text documents are excluded only when native_text_only_from_progress is true.",
    )


def _inputs_from_progress(*, progress_path: Path, native_text_only: bool) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    for record in _load_progress(progress_path):
        input_path = Path(str(record.get("input_pdf_path") or "")).expanduser()
        if not str(input_path):
            continue
        if record.get("status") == "failed":
            continue
        counts = record.get("scan_counts") if isinstance(record.get("scan_counts"), dict) else {}
        if native_text_only and int(counts.get("text_block_count") or record.get("text_block_count") or 0) <= 0:
            continue
        if not input_path.exists():
            continue
        inputs.append(
            _input_record(
                path=input_path,
                municipality=str(record.get("municipality") or _municipality_from_raw_path(input_path)),
                input_kind=str(record.get("input_kind") or _input_kind_from_raw_path(input_path)),
                display_name=str(record.get("display_name") or input_path.name),
            )
        )
    return inputs


def _input_record_from_raw_path(path: Path) -> dict[str, Any]:
    parts = path.relative_to(STORAGE_RAW_ROOT).parts if path.is_relative_to(STORAGE_RAW_ROOT) else path.parts
    return _input_record(path=path, municipality=parts[0] if parts else "unknown", input_kind=_input_kind_from_raw_path(path))


def _municipality_from_raw_path(path: Path) -> str:
    parts = path.relative_to(STORAGE_RAW_ROOT).parts if path.is_absolute() and path.is_relative_to(STORAGE_RAW_ROOT) else path.parts
    return parts[0] if parts else "unknown"


def _input_kind_from_raw_path(path: Path) -> str:
    parts = path.relative_to(STORAGE_RAW_ROOT).parts if path.is_absolute() and path.is_relative_to(STORAGE_RAW_ROOT) else path.parts
    if path.suffix.lower() == ".pdf":
        input_kind = "tree_pdf" if "tree" in parts else "raw_pdf"
    else:
        input_kind = "raw_bin_pdf_candidate"
    if "attachment" in parts:
        input_kind = "raw_attachment_bin_pdf" if path.suffix.lower() == ".bin" else "attachment_pdf"
    if "protocol_full" in parts:
        input_kind = "raw_protocol_bin_pdf" if path.suffix.lower() == ".bin" else "raw_protocol_pdf"
    return input_kind


def _input_record(*, path: Path, municipality: str, input_kind: str, display_name: str | None = None) -> dict[str, Any]:
    return {"municipality": municipality, "input_kind": input_kind, "input_pdf_path": _abs(path), "display_name": display_name or path.name}


def _process_one(*, item: dict[str, Any], ordinal: int, evidence_root: Path, doc_reports_dir: Path, dpi: int, crop_margin_points: float, skip_text_layer_issues: bool) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    input_path = Path(str(item.get("input_pdf_path") or "")).expanduser().resolve()
    slug = _doc_slug(ordinal=ordinal, path=input_path)
    doc_json_path = doc_reports_dir / f"{slug}.json"
    doc_html_path = doc_reports_dir / f"{slug}.html"
    record: dict[str, Any] = {
        "ordinal": ordinal,
        "municipality": item.get("municipality") or "",
        "input_kind": item.get("input_kind") or "",
        "display_name": item.get("display_name") or input_path.name,
        "input_pdf_path": _abs(input_path),
        "started_at_utc": started.isoformat(),
        "document_report_json_path": _abs(doc_json_path),
        "document_report_html_path": _abs(doc_html_path),
        "accepted_as_ground_truth": False,
    }
    try:
        probe = _probe_pdf(input_path)
        record["input_pdf_probe"] = probe
        if probe.get("status") != "ok":
            record.update({"status": "failed", "status_reason": "Input could not be opened as PDF.", "raw_evidence": probe, "suggested_generic_next_step": "Remove this input from native-PDF validation or verify it is a real PDF."})
            _write_document_report(record=record, doc_json_path=doc_json_path, doc_html_path=doc_html_path)
            return record
        extraction = extract_pdf_bbox_evidence(pdf_path=input_path, output_root=evidence_root, dpi=dpi, crop_margin_points=crop_margin_points)
        cases, counts = _scan_extraction(extraction=extraction, input_record=record, skip_text_layer_issues=skip_text_layer_issues)
        status, reason = _document_status_from_counts(counts=counts, has_cases=bool(cases))
        record.update(
            {
                "status": status,
                "status_reason": reason,
                "extraction_status": extraction.get("status"),
                "extraction_reason": extraction.get("status_reason"),
                "extraction_report_path": extraction.get("extraction_report_path"),
                "evidence_folder_path": extraction.get("output_pdf_folder_path"),
                "page_count": (extraction.get("summary") or {}).get("page_count"),
                "text_block_count": (extraction.get("summary") or {}).get("text_block_count"),
                "scan_counts": counts,
                "flagged_case_count": counts.get("flagged_case_count", 0),
                "flagged_case_cards_in_html": len(cases),
                "flagged_cases": cases,
                "text_layer_issue_policy": "skipped_for_separate_ocr_pipeline" if skip_text_layer_issues else "included_as_visual_review_case",
                "cleaned_display_text_preview": _cleaned_page_text_preview(extraction),
                "raw_input_text_preview": _page_text_preview(extraction),
                "suggested_generic_next_step": "Open this document HTML and visually compare each crop/page image with the cleaned text. Mark accepted only when the image supports it.",
            }
        )
    except Exception as exc:  # noqa: BLE001
        record.update({"status": "failed", "status_reason": f"Validation failed: {exc.__class__.__name__}: {exc}", "raw_evidence": traceback.format_exc(limit=20), "suggested_generic_next_step": "Inspect this document's extraction output and rerun this one file."})
    finally:
        record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        record["duration_seconds"] = round((datetime.now(timezone.utc) - started).total_seconds(), 3)
        _write_document_report(record=record, doc_json_path=doc_json_path, doc_html_path=doc_html_path)
    return record


def _scan_extraction(*, extraction: dict[str, Any], input_record: dict[str, Any], skip_text_layer_issues: bool = False) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts = {
        "text_block_count": 0,
        "changed_display_block_count": 0,
        "changed_review_case_count": 0,
        "dirty_text_quality_flag_count": 0,
        "flagged_case_count": 0,
        "non_ocr_problem_case_count": 0,
        "non_ocr_review_case_count": 0,
        "skipped_text_layer_or_ocr_case_count": 0,
        "residual_suspicious_after_cleanup_count": 0,
    }
    cases: list[dict[str, Any]] = []
    for page in extraction.get("pages") or []:
        if not isinstance(page, dict):
            continue
        for block in page.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            counts["text_block_count"] += 1
            raw_native = str(block.get("raw_native_text") or block.get("text") or "")
            display_before = str(block.get("display_text") or block.get("rtl_normalized_text") or raw_native)
            cleaned = cleanup_display_text(display_before)
            raw_cleaned = cleanup_display_text(raw_native)
            changed = cleaned != display_before
            raw_changed = raw_cleaned != raw_native
            if changed:
                counts["changed_display_block_count"] += 1
            reasons = _case_reasons(before=display_before, raw_native=raw_native, after=cleaned)
            if not reasons:
                continue
            counts["flagged_case_count"] += 1
            if changed:
                counts["changed_review_case_count"] += 1
            if any("dirty" in reason or "shape" in reason for reason in reasons):
                counts["dirty_text_quality_flag_count"] += 1
            if RESIDUAL_CLEANUP_REASON in reasons:
                counts["residual_suspicious_after_cleanup_count"] += 1
            review_scope = _case_review_scope(reasons=reasons, skip_text_layer_issues=skip_text_layer_issues)
            problem_reasons = _non_ocr_problem_reasons(reasons)
            if review_scope == SKIPPED_TEXT_LAYER_SCOPE:
                counts["skipped_text_layer_or_ocr_case_count"] += 1
            else:
                counts["non_ocr_review_case_count"] += 1
            if problem_reasons:
                counts["non_ocr_problem_case_count"] += 1
            cases.append(
                {
                    "case_id": f"doc_{int(input_record.get('ordinal') or 0):04d}_{str(block.get('block_id') or 'block')}",
                    "municipality": input_record.get("municipality"),
                    "input_pdf_path": input_record.get("input_pdf_path"),
                    "page": block.get("page"),
                    "block_id": block.get("block_id"),
                    "reasons": reasons,
                    "review_scope": review_scope,
                    "non_ocr_problem_reasons": problem_reasons,
                    "raw_native_text": raw_native,
                    "display_text_before_cleanup": display_before,
                    "display_text_after_cleanup": cleaned,
                    "raw_native_text_after_display_cleanup_for_diagnostic_only": raw_cleaned,
                    "display_cleanup_applied": changed,
                    "raw_cleanup_would_change_for_diagnostic_only": raw_changed,
                    "raw_native_text_preserved_in_pipeline": True,
                    "page_image_path": block.get("page_image_path") or page.get("page_image_path") or "",
                    "bbox_crop_image_path": block.get("bbox_crop_image_path") or "",
                    "bbox_pdf_points": block.get("bbox_pdf_points") or [],
                    "assistant_visual_judgement": _case_judgement_stub(review_scope),
                }
            )
    return cases, counts


def _document_status_from_counts(*, counts: dict[str, int], has_cases: bool) -> tuple[str, str]:
    if int(counts.get("text_block_count") or 0) <= 0:
        return "needs_visual_spot_check", "No native text blocks were extracted in this rerun. This document belongs to OCR/vision review."
    if int(counts.get("non_ocr_problem_case_count") or 0) > 0:
        return "needs_visual_judgement", "Non-OCR cleanup problem cases were found and need visual judgement against crops/pages."
    if int(counts.get("skipped_text_layer_or_ocr_case_count") or 0) > 0:
        return "text_layer_issues_skipped_for_ocr_pipeline", "Only OCR/text-layer cases remain in this document; they were skipped for the separate OCR pipeline."
    if has_cases:
        return "needs_visual_spot_check", "Cleanup changed or suspicious cases were detected, but no remaining non-OCR cleanup failures were found. Spot-check before ground-truth acceptance."
    return "needs_visual_spot_check", "No review cases were detected by the current scanner, but this document has not been independently accepted as ground truth."


def _case_review_scope(reasons: list[str], *, skip_text_layer_issues: bool) -> str:
    if skip_text_layer_issues and _is_text_layer_or_ocr_issue(reasons):
        return SKIPPED_TEXT_LAYER_SCOPE
    if _non_ocr_problem_reasons(reasons):
        return NON_OCR_PROBLEM_SCOPE
    return NON_OCR_REVIEW_SCOPE


def _is_text_layer_or_ocr_issue(reasons: list[str]) -> bool:
    return DISPLAY_TRUNCATED_DATE_REASON in set(reasons)


def _non_ocr_problem_reasons(reasons: list[str]) -> list[str]:
    return sorted(
        reason
        for reason in set(reasons)
        if reason == RESIDUAL_CLEANUP_REASON or (reason.startswith("display_after_dirty_") and reason != DISPLAY_TRUNCATED_DATE_REASON)
    )


def _case_judgement_stub(review_scope: str) -> dict[str, str]:
    if review_scope == SKIPPED_TEXT_LAYER_SCOPE:
        return {
            "status": SKIPPED_TEXT_LAYER_SCOPE,
            "reason": "The cleaned display text still has a slash/date/citation shape that visual samples show is caused by a bad native text layer. OCR is handled by a separate pipeline.",
            "suggested_generic_next_step": "Send this bbox/page evidence to the OCR/text-layer pipeline; do not accept this cleanup output as ground truth.",
        }
    return {
        "status": "pending_visual_judgement",
        "reason": "This case has been detected and included in the HTML report, but has not yet been visually accepted against the rendered crop.",
        "suggested_generic_next_step": "Open/read the bbox crop and compare it with display_text_after_cleanup.",
    }


def _case_reasons(*, before: str, raw_native: str, after: str) -> list[str]:
    if HEBREW_RE.search(before + raw_native) is None:
        return []
    reasons: list[str] = []
    reasons.extend(_text_quality_reasons(text=raw_native, prefix="raw_native"))
    reasons.extend(_text_quality_reasons(text=before, prefix="display_before"))
    reasons.extend(_text_quality_reasons(text=after, prefix="display_after"))
    if FAKE_UNDERLINE_RE.search(before) or FAKE_UNDERLINE_RE.search(raw_native):
        reasons.append("fake_underline_marker_shape")
    if REVERSED_PAREN_RE.search(before) or REVERSED_PAREN_RE.search(raw_native):
        reasons.append("reversed_or_empty_hebrew_parenthesis_shape")
    if QUOTE_DASH_RE.search(before):
        reasons.append("quote_dash_or_abbreviation_spacing_shape")
    if before != after:
        reasons.append("display_cleanup_changed_text_needs_visual_check")
    if cleanup_display_text(after) != after:
        reasons.append(RESIDUAL_CLEANUP_REASON)
    return sorted(set(reasons))


def _text_quality_reasons(*, text: str, prefix: str) -> list[str]:
    value = str(text or "")
    if not value or HEBREW_RE.search(value) is None:
        return []
    reasons: list[str] = []
    if GLUED_HEBREW_DIGIT_RE.search(value):
        reasons.append(f"{prefix}_dirty_glued_hebrew_digit_shape")
    if _has_high_confidence_truncated_date_fragment(value):
        reasons.append(f"{prefix}_dirty_truncated_date_or_protocol_number_shape")
    if LEADING_GERESH_HEBREW_RE.search(value):
        reasons.append(f"{prefix}_dirty_leading_geresh_before_hebrew_shape")
    if LEADING_COLON_HEBREW_LABEL_RE.search(value):
        reasons.append(f"{prefix}_dirty_leading_colon_label_shape")
    if WIDE_SPACED_HEBREW_WORD_RE.search(value):
        reasons.append(f"{prefix}_dirty_wide_spaced_hebrew_word_shape")
    return reasons


def _has_high_confidence_truncated_date_fragment(value: str) -> bool:
    for match in TRUNCATED_DATE_FRAGMENT_RE.finditer(value):
        if _slash_fragment_has_spaced_reference_context(value=value, match=match):
            continue
        fragment = match.group(0)
        second_group = fragment.split("/", 1)[1]
        if second_group.startswith(("19", "20")):
            return True
        if _slash_fragment_is_inside_longer_compact_slash_run(value=value, match=match):
            return True
    return False


def _slash_fragment_is_inside_longer_compact_slash_run(*, value: str, match: re.Match[str]) -> bool:
    before = value[max(0, match.start() - 3) : match.start()]
    after = value[match.end() : match.end() + 3]
    return re.search(r"\d/$", before) is not None or re.match(r"/\d", after) is not None


def _slash_fragment_has_spaced_reference_context(*, value: str, match: re.Match[str]) -> bool:
    before = value[max(0, match.start() - 4) : match.start()]
    after = value[match.end() : match.end() + 4]
    return re.search(r"/\s+$", before) is not None or re.match(r"\s+/", after) is not None


def _write_document_report(*, record: dict[str, Any], doc_json_path: Path, doc_html_path: Path) -> None:
    doc_json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    doc_html_path.write_text(_document_html(record), encoding="utf-8")


def _write_index(*, manifest: dict[str, Any], records: list[dict[str, Any]], index_json_path: Path, index_html_path: Path, progress_path: Path) -> dict[str, Any]:
    failed = sum(1 for record in records if record.get("status") == "failed")
    pending = sum(1 for record in records if record.get("status") in {"needs_visual_judgement", "needs_visual_spot_check", "text_layer_issues_skipped_for_ocr_pipeline"})
    status = "failed" if failed else "needs_review" if pending else "accepted"
    reason = "One or more documents failed." if failed else "One or more documents still require visual judgement or spot-checking." if pending else "No pending review states remain in processed documents."
    payload = {
        "schema_version": "municipality_rtl_display_cleanup_large_visual_review_index_v1",
        "status": status,
        "status_reason": reason,
        "accepted_as_ground_truth": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest": manifest,
        "progress_jsonl_path": _abs(progress_path),
        "index_json_path": _abs(index_json_path),
        "index_html_path": _abs(index_html_path),
        "summary": {
            "manifest_input_count": int(manifest.get("input_count") or 0),
            "processed_document_count": len(records),
            "failed_document_count": failed,
            "pending_visual_judgement_document_count": pending,
            "visual_spot_check_document_count": sum(1 for record in records if record.get("status") == "needs_visual_spot_check"),
            "total_flagged_case_count": sum(int((record.get("scan_counts") or {}).get("flagged_case_count") or 0) for record in records),
            "total_changed_display_block_count": sum(int((record.get("scan_counts") or {}).get("changed_display_block_count") or 0) for record in records),
            "total_changed_review_case_count": sum(int((record.get("scan_counts") or {}).get("changed_review_case_count") or 0) for record in records),
            "total_dirty_text_quality_flag_count": sum(int((record.get("scan_counts") or {}).get("dirty_text_quality_flag_count") or 0) for record in records),
            "total_non_ocr_problem_case_count": sum(int((record.get("scan_counts") or {}).get("non_ocr_problem_case_count") or 0) for record in records),
            "total_non_ocr_review_case_count": sum(int((record.get("scan_counts") or {}).get("non_ocr_review_case_count") or 0) for record in records),
            "total_skipped_text_layer_or_ocr_case_count": sum(int((record.get("scan_counts") or {}).get("skipped_text_layer_or_ocr_case_count") or 0) for record in records),
            "total_residual_suspicious_after_cleanup_count": sum(int((record.get("scan_counts") or {}).get("residual_suspicious_after_cleanup_count") or 0) for record in records),
        },
        "records": records,
    }
    index_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    index_html_path.write_text(_index_html(payload), encoding="utf-8")
    return payload


def _write_non_ocr_scope_report(*, manifest: dict[str, Any], records: list[dict[str, Any]], run_dir: Path) -> dict[str, Any]:
    report_json_path = run_dir / "native_text_non_ocr_scope_report.json"
    report_html_path = run_dir / "native_text_non_ocr_scope_report.html"
    all_cases = [case for record in records for case in (record.get("flagged_cases") or []) if isinstance(case, dict)]
    skipped_cases = [case for case in all_cases if case.get("review_scope") == SKIPPED_TEXT_LAYER_SCOPE]
    non_ocr_problem_cases = [case for case in all_cases if case.get("review_scope") == NON_OCR_PROBLEM_SCOPE]
    changed_review_cases = [case for case in all_cases if case.get("review_scope") == NON_OCR_REVIEW_SCOPE]
    summary = {
        "schema_version": "municipality_native_text_non_ocr_scope_report_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_dir_path": _abs(run_dir),
        "report_json_path": _abs(report_json_path),
        "report_html_path": _abs(report_html_path),
        "manifest_input_count": int(manifest.get("input_count") or 0),
        "processed_document_count": len(records),
        "failed_document_count": sum(1 for record in records if record.get("status") == "failed"),
        "no_native_text_document_count": sum(1 for record in records if int((record.get("scan_counts") or {}).get("text_block_count") or 0) <= 0),
        "changed_display_block_count": sum(int((record.get("scan_counts") or {}).get("changed_display_block_count") or 0) for record in records),
        "non_ocr_cleanup_problem_case_count": len(non_ocr_problem_cases),
        "skipped_text_layer_or_ocr_case_count": len(skipped_cases),
        "changed_non_ocr_review_case_count": len(changed_review_cases),
        "status": "needs_review",
        "status_reason": "Native-text-only rerun complete. OCR/text-layer cases are skipped by policy; non-OCR cleanup problems must be zero before this cleanup scope is considered clean.",
        "accepted_as_ground_truth": False,
    }
    payload = {
        "summary": summary,
        "non_ocr_cleanup_problem_cases": non_ocr_problem_cases,
        "skipped_text_layer_or_ocr_cases": skipped_cases,
        "changed_non_ocr_review_cases_sample": changed_review_cases[:100],
    }
    report_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_html_path.write_text(_non_ocr_scope_html(summary=summary, non_ocr_problem_cases=non_ocr_problem_cases, skipped_cases=skipped_cases, changed_review_cases=changed_review_cases), encoding="utf-8")
    return payload


def _non_ocr_scope_html(*, summary: dict[str, Any], non_ocr_problem_cases: list[dict[str, Any]], skipped_cases: list[dict[str, Any]], changed_review_cases: list[dict[str, Any]]) -> str:
    skipped_rows = _slash_fragment_rows(skipped_cases)
    return f"""<!doctype html>
<html lang="en">
<head>{_html_head('Native Text Non-OCR Scope Report')}</head>
<body>
  <header><h1>Native Text Non-OCR Scope Report</h1><p>OCR/text-layer cases are skipped for the separate OCR pipeline.</p></header>
  <main>
    <section class="card">
      <h2>Status: {_h(summary.get('status'))}</h2>
      <p>{_h(summary.get('status_reason'))}</p>
      <p class="path">Run: {_h(summary.get('run_dir_path'))}</p>
      <p class="path">JSON: <a href="{_h(_uri(summary.get('report_json_path')))}">{_h(summary.get('report_json_path'))}</a></p>
      <div class="metrics">
        <div class="metric"><strong>{_h(summary.get('processed_document_count'))}/{_h(summary.get('manifest_input_count'))}</strong> native-text docs processed</div>
        <div class="metric"><strong>{_h(summary.get('non_ocr_cleanup_problem_case_count'))}</strong> non-OCR cleanup problems</div>
        <div class="metric"><strong>{_h(summary.get('skipped_text_layer_or_ocr_case_count'))}</strong> skipped OCR/text-layer cases</div>
        <div class="metric"><strong>{_h(summary.get('changed_display_block_count'))}</strong> changed display blocks</div>
        <div class="metric"><strong>{_h(summary.get('no_native_text_document_count'))}</strong> no-native-text docs in rerun</div>
        <div class="metric"><strong>{_h(summary.get('failed_document_count'))}</strong> failed docs</div>
      </div>
    </section>
    <section class="card">
      <h2>Skipped OCR/Text-Layer Cases</h2>
      <p>These are not cleanup failures in this scope. Visual samples show the rendered page/crop can be correct while the native text layer is wrong.</p>
      <table><thead><tr><th>Slash Fragment</th><th>Count</th></tr></thead><tbody>{skipped_rows}</tbody></table>
      <details><summary>Show first 50 skipped examples with evidence</summary>{''.join(_case_card(case) for case in skipped_cases[:50])}</details>
    </section>
    <section class="card">
      <h2>Non-OCR Cleanup Problems</h2>
      <p>Expected count is zero. If any cards appear here, inspect their crops before accepting.</p>
      {''.join(_case_card(case) for case in non_ocr_problem_cases) or '<p><strong>No non-OCR cleanup problems detected.</strong></p>'}
    </section>
    <section class="card">
      <h2>Changed Non-OCR Review Sample</h2>
      <p>Changed-only cases are review evidence, not counted as remaining cleanup failures. First 50 are shown for spot-checking.</p>
      <details><summary>Show first 50 changed-only examples</summary>{''.join(_case_card(case) for case in changed_review_cases[:50])}</details>
    </section>
  </main>
</body>
</html>
"""


def _slash_fragment_rows(cases: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for case in cases:
        text = str(case.get("display_text_after_cleanup") or "")
        for match in TRUNCATED_DATE_FRAGMENT_RE.finditer(text):
            counts[match.group(0)] = counts.get(match.group(0), 0) + 1
    return "".join(f"<tr><td>{_h(fragment)}</td><td>{_h(count)}</td></tr>" for fragment, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:50])


def _document_html(record: dict[str, Any]) -> str:
    cases = [case for case in record.get("flagged_cases") or [] if isinstance(case, dict)]
    cards = "\n".join(_case_card(case) for case in cases)
    counts = record.get("scan_counts") if isinstance(record.get("scan_counts"), dict) else {}
    return f"""<!doctype html>
<html lang="en">
<head>{_html_head('Document Visual Review')}</head>
<body>
  <header><h1>Document Visual Review</h1><p>{_h(record.get('display_name'))}</p></header>
  <main>
    <section class="card">
      <h2>Status: {_h(record.get('status'))}</h2>
      <p>{_h(record.get('status_reason'))}</p>
      <p class="path">Input: {_h(record.get('input_pdf_path'))}</p>
      <p class="path">Extraction: {_h(record.get('extraction_report_path'))}</p>
      <div class="metrics">
        <div class="metric"><strong>{_h(record.get('page_count'))}</strong> pages</div>
        <div class="metric"><strong>{_h(counts.get('text_block_count'))}</strong> text blocks</div>
        <div class="metric"><strong>{_h(counts.get('flagged_case_count'))}</strong> flagged instances</div>
        <div class="metric"><strong>{_h(counts.get('changed_display_block_count'))}</strong> changed display blocks</div>
        <div class="metric"><strong>{_h(counts.get('changed_review_case_count'))}</strong> changed review cases</div>
        <div class="metric"><strong>{_h(counts.get('dirty_text_quality_flag_count'))}</strong> dirty-text flags</div>
        <div class="metric"><strong>{_h(counts.get('non_ocr_problem_case_count'))}</strong> non-OCR problems</div>
        <div class="metric"><strong>{_h(counts.get('skipped_text_layer_or_ocr_case_count'))}</strong> skipped text-layer/OCR</div>
        <div class="metric"><strong>{_h(counts.get('residual_suspicious_after_cleanup_count'))}</strong> residual suspicious</div>
      </div>
    </section>
    <section class="card"><h2>Pipeline Output Preview After Cleanup</h2><p>This is the text users should inspect first.</p><pre>{_h(record.get('cleaned_display_text_preview'))}</pre></section>
    <section class="card"><h2>Raw Native Text Evidence</h2><p>This is preserved provenance. It may be dirty and is not accepted as the cleaned pipeline result.</p><details><summary>Show raw native preview</summary><pre>{_h(record.get('raw_input_text_preview'))}</pre></details></section>
    <section><h2>Review Instances</h2>{cards or '<div class="card needs"><p>No review cards were detected by the current scanner. This document still requires visual spot-check before ground-truth acceptance.</p></div>'}</section>
  </main>
</body>
</html>
"""


def _index_html(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = "\n".join(_record_row(record) for record in payload.get("records") or [] if isinstance(record, dict))
    return f"""<!doctype html>
<html lang="en">
<head>{_html_head('Large RTL Visual Review Index')}</head>
<body>
  <header><h1>Large RTL Visual Review Index</h1><p>Per-document HTML reports for every flagged cleanup/suspicious instance.</p></header>
  <main>
    <section class="metrics">
      <div class="metric"><strong>{_h(summary.get('processed_document_count'))}/{_h(summary.get('manifest_input_count'))}</strong> processed</div>
      <div class="metric"><strong>{_h(summary.get('total_flagged_case_count'))}</strong> flagged instances</div>
      <div class="metric"><strong>{_h(summary.get('pending_visual_judgement_document_count'))}</strong> docs pending visual judgement</div>
      <div class="metric"><strong>{_h(summary.get('visual_spot_check_document_count'))}</strong> docs needing spot-check</div>
      <div class="metric"><strong>{_h(summary.get('failed_document_count'))}</strong> failed docs</div>
      <div class="metric"><strong>{_h(summary.get('total_dirty_text_quality_flag_count'))}</strong> dirty-text flags</div>
      <div class="metric"><strong>{_h(summary.get('total_non_ocr_problem_case_count'))}</strong> non-OCR problems</div>
      <div class="metric"><strong>{_h(summary.get('total_skipped_text_layer_or_ocr_case_count'))}</strong> skipped text-layer/OCR</div>
      <div class="metric"><strong>{_h(summary.get('total_residual_suspicious_after_cleanup_count'))}</strong> residual suspicious</div>
    </section>
    <section class="card">
      <h2>Status: {_h(payload.get('status'))}</h2>
      <p>{_h(payload.get('status_reason'))}</p>
      <p class="path">Index JSON: {_h(payload.get('index_json_path'))}</p>
      <p class="path">Progress JSONL: {_h(payload.get('progress_jsonl_path'))}</p>
    </section>
    <section class="card">
      <h2>Documents</h2>
      <table><thead><tr><th>#</th><th>Status</th><th>Municipality</th><th>Document</th><th>Flagged</th><th>Changed</th><th>HTML</th></tr></thead><tbody>{rows}</tbody></table>
    </section>
  </main>
</body>
</html>
"""


def _case_card(case: dict[str, Any]) -> str:
    judgement = case.get("assistant_visual_judgement") if isinstance(case.get("assistant_visual_judgement"), dict) else {}
    card_class = "skipped" if case.get("review_scope") == SKIPPED_TEXT_LAYER_SCOPE else "needs"
    return f"""
    <article class="card {card_class}">
      <h3>{_h(case.get('case_id'))}: page {_h(case.get('page'))}, block {_h(case.get('block_id'))}</h3>
      <p><strong>Review scope:</strong> <code>{_h(case.get('review_scope'))}</code></p>
      <p><strong>Reasons:</strong> {_h(', '.join(case.get('reasons') or []))}</p>
      <p><strong>Non-OCR problem reasons:</strong> {_h(', '.join(case.get('non_ocr_problem_reasons') or []))}</p>
      <p><strong>Assistant visual judgement:</strong> <code>{_h(judgement.get('status'))}</code> - {_h(judgement.get('reason'))}</p>
      <div class="grid">
        {_image(case.get('page_image_path'), 'Rendered page image')}
        {_image(case.get('bbox_crop_image_path'), 'Rendered bbox crop image')}
      </div>
      <details open><summary>Display Text Before Cleanup</summary><pre>{_h(case.get('display_text_before_cleanup'))}</pre></details>
      <details open><summary>Display Text After Cleanup</summary><pre>{_h(case.get('display_text_after_cleanup'))}</pre></details>
      <details><summary>Raw Native Text Preserved</summary><pre>{_h(case.get('raw_native_text'))}</pre></details>
    </article>
    """


def _record_row(record: dict[str, Any]) -> str:
    counts = record.get("scan_counts") if isinstance(record.get("scan_counts"), dict) else {}
    return "".join(
        [
            "<tr>",
            f"<td>{_h(record.get('ordinal'))}</td>",
            f"<td><code>{_h(record.get('status'))}</code><br>{_h(record.get('status_reason'))}</td>",
            f"<td>{_h(record.get('municipality'))}</td>",
            f"<td class=\"path\">{_h(record.get('input_pdf_path'))}</td>",
            f"<td>{_h(counts.get('flagged_case_count'))}</td>",
            f"<td>{_h(counts.get('changed_display_block_count'))}</td>",
            f"<td class=\"path\"><a href=\"{_h(_uri(record.get('document_report_html_path')))}\">{_h(record.get('document_report_html_path'))}</a></td>",
            "</tr>",
        ]
    )


def _html_head(title: str) -> str:
    return f"""
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_h(title)}</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f7fb; color: #172033; }}
    header {{ background: #172033; color: white; padding: 28px 36px; }}
    main {{ max-width: 1500px; margin: 0 auto; padding: 26px 36px 64px; }}
    .card {{ background: white; border: 1px solid #d8dfeb; border-radius: 16px; padding: 18px; margin: 18px 0; box-shadow: 0 10px 24px rgba(23,32,51,.08); }}
    .needs {{ border-right: 8px solid #b45309; }}
    .skipped {{ border-right: 8px solid #2563eb; }}
    .accepted {{ border-right: 8px solid #15803d; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 14px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
    .metric {{ background: white; border: 1px solid #d8dfeb; border-radius: 14px; padding: 14px; }}
    .metric strong {{ display: block; font-size: 24px; color: #173b66; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ border-bottom: 1px solid #d8dfeb; padding: 8px; text-align: left; vertical-align: top; }}
    pre {{ direction: rtl; text-align: right; white-space: pre-wrap; background: #111827; color: #f8fafc; padding: 12px; border-radius: 10px; overflow: auto; max-height: 420px; }}
    img {{ max-width: 100%; border: 1px solid #d8dfeb; border-radius: 10px; background: white; }}
    code {{ background: #eef2ff; padding: 2px 5px; border-radius: 4px; }}
    .path {{ direction: ltr; text-align: left; font-size: 12px; color: #526070; word-break: break-all; }}
  </style>
"""


def _image(path_value: Any, caption: str) -> str:
    path = str(path_value or "")
    if not path:
        return "<p>Missing image path.</p>"
    return f"<figure><img src=\"{_h(_uri(path))}\" alt=\"{_h(caption)}\"><figcaption>{_h(caption)}<br><span class=\"path\">{_h(path)}</span></figcaption></figure>"


def _probe_pdf(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "path": _abs(path)}
    try:
        with fitz.open(path) as document:
            return {"status": "ok", "page_count": document.page_count, "is_pdf": document.is_pdf, "metadata_title": (document.metadata or {}).get("title") or ""}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "path": _abs(path), "reason": f"{exc.__class__.__name__}: {exc}"}


def _page_text_preview(extraction: dict[str, Any]) -> str:
    for page in extraction.get("pages") or []:
        if isinstance(page, dict) and str(page.get("plain_text") or "").strip():
            return str(page.get("plain_text") or "").strip()[:1200]
    return ""


def _cleaned_page_text_preview(extraction: dict[str, Any]) -> str:
    for page in extraction.get("pages") or []:
        if not isinstance(page, dict):
            continue
        lines: list[str] = []
        for block in page.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            raw_native = str(block.get("raw_native_text") or block.get("text") or "")
            display_before = str(block.get("display_text") or block.get("rtl_normalized_text") or raw_native)
            cleaned = cleanup_display_text(display_before)
            if cleaned.strip():
                lines.append(cleaned.strip())
        preview = "\n".join(lines).strip()
        if preview:
            return preview[:1600]
    return ""


def _progress_line(record: dict[str, Any]) -> str:
    counts = record.get("scan_counts") if isinstance(record.get("scan_counts"), dict) else {}
    return json.dumps(
        {
            "status": record.get("status"),
            "reason": record.get("status_reason"),
            "document_report_html_path": record.get("document_report_html_path"),
            "input_pdf_path": record.get("input_pdf_path"),
            "page_count": record.get("page_count"),
            "flagged_case_count": counts.get("flagged_case_count", 0),
            "changed_display_block_count": counts.get("changed_display_block_count", 0),
            "changed_review_case_count": counts.get("changed_review_case_count", 0),
            "dirty_text_quality_flag_count": counts.get("dirty_text_quality_flag_count", 0),
            "non_ocr_problem_case_count": counts.get("non_ocr_problem_case_count", 0),
            "skipped_text_layer_or_ocr_case_count": counts.get("skipped_text_layer_or_ocr_case_count", 0),
            "residual_suspicious_after_cleanup_count": counts.get("residual_suspicious_after_cleanup_count", 0),
            "suggested_generic_next_step": record.get("suggested_generic_next_step"),
        },
        ensure_ascii=False,
    )


def _load_progress(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _new_run_dir(*, results_root: Path, run_name: str) -> Path:
    results_root.mkdir(parents=True, exist_ok=True)
    base = _safe_name(run_name, default=datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ"))
    candidate = results_root / base
    if not candidate.exists():
        candidate.mkdir(parents=True)
        return candidate
    suffix = 2
    while True:
        next_candidate = results_root / f"{base}_{suffix:02d}"
        if not next_candidate.exists():
            next_candidate.mkdir(parents=True)
            return next_candidate
        suffix += 1


def _doc_slug(*, ordinal: int, path: Path) -> str:
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:10]
    return f"doc_{ordinal:04d}_{_safe_name(path.stem, default='document')}_{digest}"


def _safe_name(value: str, *, default: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())[:90].strip("._")
    return safe or default


def _uri(value: Any) -> str:
    try:
        return Path(str(value or "")).expanduser().resolve().as_uri()
    except ValueError:
        return str(value or "")


def _h(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _abs(path: Path | str | None) -> str:
    if path is None:
        return ""
    return str(Path(path).expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main())
