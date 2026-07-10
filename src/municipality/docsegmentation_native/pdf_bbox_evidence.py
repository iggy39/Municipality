from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import statistics
from pathlib import Path
from typing import Any

try:
    import fitz
except ImportError as exc:  # pragma: no cover - exercised only in broken environments.
    raise SystemExit("PyMuPDF is required. Install project dependencies first.") from exc


SCHEMA_VERSION = "municipality_docsegmentation_pdf_bbox_evidence_v1"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "rag_eval" / "runs" / "local_docsegmentation_native"
BODY_PUNCTUATION_RE = re.compile(r"[.!?;:]|[\u05C3]")
EARLY_SECTION_NUMBER_RE = re.compile(r"^\D{0,12}\d{1,4}(?!\d)\s*:")
HEBREW_CHAR_RE = re.compile(r"[\u0590-\u05FF]")
LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
NUMBER_CHAR_RE = re.compile(r"[0-9]")
RTL_ATTACH_TO_PREVIOUS = {"'", "׳", "\"", "״", ".", ",", ":", ";", "!", "?", "%"}
RTL_OPENING_PUNCTUATION = {"(", "[", "{"}
RTL_CLOSING_PUNCTUATION = {")": "(", "]": "[", "}": "{"}
LTR_ISLAND_PUNCTUATION = {".", "/", ":", "@", "-", "_", "+", "%", "#", "&"}
RTL_WORD_INTERNAL_PUNCTUATION = {"'", "׳", "\"", "״", "-", "־"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract native PDF block, page, and bbox evidence from exactly one PDF.")
    parser.add_argument("pdf", help="Absolute or relative path to one input PDF")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Root where the per-PDF evidence folder will be created")
    parser.add_argument("--dpi", type=int, default=180, help="Render DPI for page images and bbox crops")
    parser.add_argument("--crop-margin-points", type=float, default=1.0, help="Small visual margin around each bbox crop")
    args = parser.parse_args(argv)

    result = extract_pdf_bbox_evidence(
        pdf_path=Path(args.pdf).expanduser(),
        output_root=Path(args.output_root).expanduser().resolve(),
        dpi=max(72, int(args.dpi)),
        crop_margin_points=max(0.0, float(args.crop_margin_points)),
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "reason": result["status_reason"],
                "input_pdf_path": result["input_pdf_path"],
                "output_pdf_folder_path": result["output_pdf_folder_path"],
                "extraction_report_path": result["extraction_report_path"],
                "page_count": result["summary"]["page_count"],
                "text_block_count": result["summary"]["text_block_count"],
                "warning_count": result["summary"]["warning_count"],
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
                    "raw_evidence": result.get("warnings", [])[:10],
                    "suggested_generic_next_step": result.get("suggested_generic_next_step"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0 if result["status"] in {"ok", "partial"} else 2


def extract_pdf_bbox_evidence(
    *,
    pdf_path: Path,
    output_root: Path,
    dpi: int = 180,
    crop_margin_points: float = 1.0,
) -> dict[str, Any]:
    pdf_path = pdf_path.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    started_at = datetime.now(timezone.utc)
    pdf_sha256 = _sha256_file(pdf_path)
    pdf_folder = _unique_pdf_output_folder(output_root=output_root, pdf_path=pdf_path, pdf_sha256=pdf_sha256, started_at=started_at)
    pages_dir = pdf_folder / "pages"
    overlays_dir = pdf_folder / "overlays"
    bbox_crops_dir = pdf_folder / "bbox_crops"
    page_text_dir = pdf_folder / "page_text"
    for directory in (pages_dir, overlays_dir, bbox_crops_dir, page_text_dir):
        directory.mkdir(parents=True, exist_ok=False)

    pages: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as document:
        for page_index, page in enumerate(document, start=1):
            page_record, page_warnings = _extract_page_evidence(
                page=page,
                page_number=page_index,
                pdf_path=pdf_path,
                pages_dir=pages_dir,
                overlays_dir=overlays_dir,
                bbox_crops_dir=bbox_crops_dir,
                page_text_dir=page_text_dir,
                dpi=dpi,
                crop_margin_points=crop_margin_points,
            )
            pages.append(page_record)
            warnings.extend(page_warnings)

    summary = _summary(pages=pages, warnings=warnings)
    status = "ok" if not any(item.get("severity") == "error" for item in warnings) else "needs_review"
    status_reason = "Native PDF evidence was extracted; OCR fallback was not used." if status == "ok" else "Native PDF evidence has warnings or errors that need review."
    report_path = pdf_folder / "extraction_report.json"
    pages_json_path = pdf_folder / "pages.json"
    quality_path = pdf_folder / "quality_report.json"
    audit_path = pdf_folder / "audit.md"

    payload = _with_path_validation(
        {
            "step": "municipality_extract_pdf_bbox_evidence",
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "status_reason": status_reason,
            "accepted_as_ground_truth": False,
            "research_use": "native_pdf_evidence_only",
            "created_at_utc": started_at.isoformat(),
            "input_pdf_path": _abs(pdf_path),
            "resolved_input_pdf_path": _abs(pdf_path),
            "input_pdf_sha256": pdf_sha256,
            "input_pdf_size_bytes": pdf_path.stat().st_size,
            "output_pdf_folder_path": _abs(pdf_folder),
            "pages_dir_path": _abs(pages_dir),
            "overlays_dir_path": _abs(overlays_dir),
            "bbox_crops_dir_path": _abs(bbox_crops_dir),
            "page_text_dir_path": _abs(page_text_dir),
            "extraction_report_path": _abs(report_path),
            "pages_json_path": _abs(pages_json_path),
            "quality_report_path": _abs(quality_path),
            "audit_markdown_path": _abs(audit_path),
            "settings": {
                "dpi": dpi,
                "crop_margin_points": crop_margin_points,
                "processing_mode": "one_pdf_at_a_time",
                "source_text_policy": "native_text_only_no_ocr_no_model_repair",
            },
            "summary": summary,
            "warnings": warnings,
            "suggested_generic_next_step": "Use the rendered page images and bbox crop images as visual evidence before accepting any grouping result.",
            "pages": pages,
        }
    )
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    pages_json_path.write_text(json.dumps({"pages": pages, "extraction_report_path": _abs(report_path)}, ensure_ascii=False, indent=2), encoding="utf-8")
    quality_path.write_text(json.dumps({"status": status, "summary": summary, "warnings": warnings}, ensure_ascii=False, indent=2), encoding="utf-8")
    audit_path.write_text(_audit_markdown(payload), encoding="utf-8")
    return payload


def _extract_page_evidence(
    *,
    page: Any,
    page_number: int,
    pdf_path: Path,
    pages_dir: Path,
    overlays_dir: Path,
    bbox_crops_dir: Path,
    page_text_dir: Path,
    dpi: int,
    crop_margin_points: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    page_image_path = pages_dir / f"page_{page_number:03d}.png"
    overlay_image_path = overlays_dir / f"page_{page_number:03d}_overlay.png"
    page_crop_dir = bbox_crops_dir / f"page_{page_number:03d}"
    page_crop_dir.mkdir(parents=True, exist_ok=False)
    page_text_path = page_text_dir / f"page_{page_number:03d}.txt"

    _render_page(page=page, output_path=page_image_path, dpi=dpi)
    raw = page.get_text("dict", sort=False)
    rawdict = page.get_text("rawdict", sort=False)
    blocks, warnings = _extract_text_blocks(
        page=page,
        page_number=page_number,
        raw=raw if isinstance(raw, dict) else {},
        rawdict=rawdict if isinstance(rawdict, dict) else {},
        pdf_path=pdf_path,
        page_image_path=page_image_path,
        page_crop_dir=page_crop_dir,
        dpi=dpi,
        crop_margin_points=crop_margin_points,
    )
    page_text = "\n\n".join(str(block.get("text") or "") for block in blocks if str(block.get("text") or "").strip())
    page_processing_status = _page_processing_status(text_block_count=len(blocks), page_text=page_text)
    page_text_path.write_text(_page_text_file(page_number=page_number, blocks=blocks, page_text=page_text, page_processing_status=page_processing_status), encoding="utf-8")
    _draw_overlay(page=page, blocks=blocks)
    _render_page(page=page, output_path=overlay_image_path, dpi=dpi)
    page_record = {
        "page": page_number,
        "width_pdf_points": round(float(page.rect.width), 3),
        "height_pdf_points": round(float(page.rect.height), 3),
        "page_image_path": _abs(page_image_path),
        "overlay_image_path": _abs(overlay_image_path),
        "bbox_crop_page_dir_path": _abs(page_crop_dir),
        "page_text_path": _abs(page_text_path),
        "plain_text": page_text,
        "text_block_count": len(blocks),
        "char_count": len(page_text),
        "native_page_diagnostics": {"native_text_absence_reason": "native_text_available" if blocks else "no_text_blocks_detected"},
        "page_processing_status": page_processing_status,
        "blocks": blocks,
    }
    if not blocks:
        warnings.append(
            {
                "severity": "warning",
                "issue": "page_has_no_native_text_blocks",
                "page": page_number,
                "input_pdf_path": _abs(pdf_path),
                "page_image_path": _abs(page_image_path),
                "status_reason": page_processing_status.get("status_reason"),
            }
        )
    return page_record, warnings


def _page_processing_status(*, text_block_count: int, page_text: str) -> dict[str, Any]:
    if text_block_count > 0:
        return {
            "status": "native_text_available",
            "status_reason": "Native text blocks were extracted; OCR fallback is not used.",
            "ocr_policy": "disabled_in_production",
            "skip_reason": None,
        }
    return {
        "status": "needs_review_no_native_text_blocks",
        "status_reason": "No native text blocks were extracted. Visually inspect the rendered page before deciding whether OCR is needed.",
        "ocr_policy": "disabled_in_production",
        "skip_reason": "no_text_blocks_detected",
        "raw_evidence": page_text[:500],
    }


def _extract_text_blocks(
    *,
    page: Any,
    page_number: int,
    raw: dict[str, Any],
    rawdict: dict[str, Any],
    pdf_path: Path,
    page_image_path: Path,
    page_crop_dir: Path,
    dpi: int,
    crop_margin_points: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blocks: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    raw_blocks = raw.get("blocks") if isinstance(raw.get("blocks"), list) else []
    rawdict_blocks = [block for block in (rawdict.get("blocks") if isinstance(rawdict.get("blocks"), list) else []) if isinstance(block, dict) and int(block.get("type") or 0) == 0]
    rawdict_block_index = 0
    block_index = 0
    for raw_block in raw_blocks:
        if not isinstance(raw_block, dict) or int(raw_block.get("type") or 0) != 0:
            continue
        rawdict_block = _matching_rawdict_block(raw_block=raw_block, rawdict_blocks=rawdict_blocks, start_index=rawdict_block_index)
        rawdict_block_index += 1
        lines, span_sizes, colors = _extract_lines(raw_block=raw_block)
        block_text = "\n".join(line["text"] for line in lines if line.get("text")).strip()
        if not block_text:
            continue
        block_index += 1
        block_id = f"p{page_number:03d}_b{block_index:04d}"
        bbox = _bbox(raw_block.get("bbox"))
        crop_image_path = page_crop_dir / f"{block_id}.png"
        crop_text_path = page_crop_dir / f"{block_id}.txt"
        crop_rect = _bounded_crop_rect(bbox=bbox, page_rect=page.rect, margin_points=crop_margin_points)
        visual_features = _visual_features(raw_block=raw_block, page_rect=page.rect, lines=lines, span_sizes=span_sizes, colors=colors)
        display_text, display_diagnostics = _display_text_from_rawdict_block(rawdict_block=rawdict_block, fallback_text=block_text)
        semantic_scope = _semantic_scope_decision(
            {
                "block_id": block_id,
                "page": page_number,
                "bbox_pdf_points": bbox,
                "bbox_crop_image_path": _abs(crop_image_path),
                "text": block_text,
                "lines": lines,
                "visual_features": visual_features,
            }
        )
        quality_flags: list[str] = []
        if crop_rect is None:
            quality_flags.append("invalid_or_empty_bbox")
            warnings.append(_warning(issue="invalid_or_empty_bbox", page=page_number, block_id=block_id, pdf_path=pdf_path, page_image_path=page_image_path, crop_image_path=crop_image_path, severity="error"))
        else:
            try:
                _render_page(page=page, output_path=crop_image_path, dpi=dpi, clip=crop_rect)
            except Exception as exc:  # noqa: BLE001
                quality_flags.append("crop_render_failed")
                warnings.append(_warning(issue=f"crop_render_failed: {exc}", page=page_number, block_id=block_id, pdf_path=pdf_path, page_image_path=page_image_path, crop_image_path=crop_image_path, severity="error"))
        block = {
            "block_id": block_id,
            "page": page_number,
            "input_pdf_path": _abs(pdf_path),
            "page_image_path": _abs(page_image_path),
            "bbox_crop_image_path": _abs(crop_image_path),
            "bbox_text_path": _abs(crop_text_path),
            "bbox_pdf_points": bbox,
            "crop_bbox_pdf_points_with_margin": _rect_to_bbox(crop_rect) if crop_rect else [],
            "text": block_text,
            "raw_native_text": block_text,
            "rtl_normalized_text": _rtl_normalized_text_for_block(block_text),
            "display_text": display_text,
            "display_text_diagnostics": display_diagnostics,
            "char_count": len(block_text),
            "line_count": len(lines),
            "lines": lines,
            "visual_features": visual_features,
            "semantic_scope": semantic_scope,
            "native_text_quality": {"status": "not_model_corrected", "flags": []},
            "quality_flags": sorted(set(quality_flags)),
        }
        crop_text_path.write_text(_block_text_file(block), encoding="utf-8")
        blocks.append(block)
    return blocks, warnings


def _matching_rawdict_block(*, raw_block: dict[str, Any], rawdict_blocks: list[dict[str, Any]], start_index: int) -> dict[str, Any]:
    if 0 <= start_index < len(rawdict_blocks):
        candidate = rawdict_blocks[start_index]
        if _bbox_close(_bbox(raw_block.get("bbox")), _bbox(candidate.get("bbox"))):
            return candidate
    raw_bbox = _bbox(raw_block.get("bbox"))
    for candidate in rawdict_blocks:
        if _bbox_close(raw_bbox, _bbox(candidate.get("bbox"))):
            return candidate
    return {}


def _bbox_close(left: list[float], right: list[float], *, tolerance: float = 2.0) -> bool:
    return len(left) == 4 and len(right) == 4 and all(abs(float(left[index]) - float(right[index])) <= tolerance for index in range(4))


def _display_text_from_rawdict_block(*, rawdict_block: dict[str, Any], fallback_text: str) -> tuple[str, dict[str, Any]]:
    line_groups = _visual_line_groups(rawdict_block=rawdict_block)
    line_records = [_visual_line_text_record(group) for group in line_groups]
    display_lines = [str(record.get("text") or "") for record in line_records if str(record.get("text") or "").strip()]
    fallback = _rtl_normalized_text_for_block(fallback_text)
    if not display_lines:
        return fallback, {
            "applied": False,
            "source": "fallback_rtl_normalized_text",
            "reason": "rawdict_character_geometry_unavailable",
        }
    display_text = "\n".join(display_lines).strip()
    return display_text, {
        "applied": display_text != fallback,
        "source": "rawdict_character_geometry_visual_order_v2",
        "line_count": len(display_lines),
        "confidence": _display_text_confidence(line_records),
        "line_order_method": "bbox_y_then_geometry_x",
        "char_run_count": sum(int(record.get("token_count") or 0) for record in line_records),
        "fallback_used": False,
        "fallback_reason": None,
        "policy": "Reconstruct display/downstream text from character geometry. Raw native text is preserved unchanged.",
    }


def _visual_line_groups(*, rawdict_block: dict[str, Any]) -> list[list[dict[str, Any]]]:
    line_records: list[dict[str, Any]] = []
    for raw_line in rawdict_block.get("lines") or []:
        if not isinstance(raw_line, dict):
            continue
        chars: list[dict[str, Any]] = []
        has_visible_nonspace = False
        for span in raw_line.get("spans") or []:
            if not isinstance(span, dict):
                continue
            for raw_char in span.get("chars") or []:
                if not isinstance(raw_char, dict):
                    continue
                char = str(raw_char.get("c") or "")
                bbox = _bbox(raw_char.get("bbox"))
                if not char or len(bbox) != 4:
                    continue
                if not char.isspace():
                    has_visible_nonspace = True
                chars.append({"char": char, "bbox": bbox})
        if not chars or not has_visible_nonspace:
            continue
        bbox = _union_bbox([item["bbox"] for item in chars if not str(item.get("char") or "").isspace()])
        if len(bbox) != 4:
            continue
        line_records.append(
            {
                "chars": chars,
                "bbox": bbox,
                "center_y": (bbox[1] + bbox[3]) / 2.0,
                "height": max(1.0, bbox[3] - bbox[1]),
            }
        )

    groups: list[dict[str, Any]] = []
    for line in sorted(line_records, key=lambda item: (float(item["center_y"]), float(item["bbox"][0]))):
        matched: dict[str, Any] | None = None
        for group in groups:
            tolerance = max(2.5, min(float(group["height"]), float(line["height"])) * 0.35)
            if abs(float(group["center_y"]) - float(line["center_y"])) <= tolerance:
                matched = group
                break
        if matched is None:
            groups.append({"center_y": line["center_y"], "height": line["height"], "chars": list(line["chars"]), "bbox": list(line["bbox"])})
            continue
        matched["chars"].extend(line["chars"])
        matched["bbox"] = _union_bbox([matched["bbox"], line["bbox"]])
        matched["center_y"] = (float(matched["bbox"][1]) + float(matched["bbox"][3])) / 2.0
        matched["height"] = max(1.0, float(matched["bbox"][3]) - float(matched["bbox"][1]))
    return [group["chars"] for group in sorted(groups, key=lambda item: (float(item["bbox"][1]), float(item["bbox"][0])))]


def _visual_line_text(chars: list[dict[str, Any]]) -> str:
    return str(_visual_line_text_record(chars).get("text") or "")


def _visual_line_text_record(chars: list[dict[str, Any]]) -> dict[str, Any]:
    char_items = _visual_char_items(chars)
    if not char_items:
        return {"text": "", "token_count": 0, "confidence": "low", "direction": "unknown"}
    hebrew_count = sum(1 for item in char_items if item.get("kind") == "hebrew")
    latin_count = sum(1 for item in char_items if item.get("kind") == "latin")
    rtl_line = hebrew_count > 0 and hebrew_count >= latin_count
    tokens = _visual_tokens_from_chars(char_items, rtl_line=rtl_line)
    if not tokens:
        return {"text": "", "token_count": 0, "confidence": "low", "direction": "rtl" if rtl_line else "ltr"}
    tokens = sorted(tokens, key=lambda token: float(token.get("center_x") or 0.0), reverse=rtl_line)
    if rtl_line:
        tokens = _normalize_tiny_encoded_geresh_markers_in_rtl_line(tokens=tokens, char_items=char_items)
        tokens = _merge_split_ltr_numeric_tokens_in_rtl_line(tokens=tokens, char_items=char_items)
    return {
        "text": _clean_visual_spacing(_join_visual_tokens(tokens)),
        "token_count": len(tokens),
        "confidence": _line_geometry_confidence(char_items=char_items, tokens=tokens),
        "direction": "rtl" if rtl_line else "ltr",
    }


def _normalize_tiny_encoded_geresh_markers_in_rtl_line(*, tokens: list[dict[str, Any]], char_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(tokens) < 3:
        return tokens
    median_height = _median_char_height(char_items)
    median_width = _median_char_width(char_items)
    out = [dict(token) for token in tokens]
    for index, token in enumerate(out):
        if not _looks_like_tiny_encoded_geresh_marker(token=token, median_height=median_height):
            continue
        previous_token = out[index - 1] if index > 0 else None
        next_token = out[index + 1] if index + 1 < len(out) else None
        max_gap = max(1.5, median_width * 1.1)
        if not _tiny_digit_is_in_geresh_position(marker=token, previous_token=previous_token, next_token=next_token, max_gap=max_gap):
            continue
        token["text"] = "׳"
        token["kind"] = "punctuation"
        token["source"] = "tiny_encoded_digit_normalized_to_geresh_by_geometry"
    return out


def _looks_like_tiny_encoded_geresh_marker(*, token: dict[str, Any], median_height: float) -> bool:
    if str(token.get("text") or "") != "1":
        return False
    bbox = _bbox(token.get("bbox") or [])
    if len(bbox) != 4:
        return False
    height = max(0.1, float(bbox[3]) - float(bbox[1]))
    return height <= max(5.0, median_height * 0.78)


def _tiny_digit_is_in_geresh_position(*, marker: dict[str, Any], previous_token: dict[str, Any] | None, next_token: dict[str, Any] | None, max_gap: float) -> bool:
    if not previous_token or not next_token:
        return False
    previous_text = str(previous_token.get("text") or "")
    if previous_token.get("kind") != "hebrew" or not (1 <= len(previous_text) <= 4):
        return False
    if next_token.get("kind") not in {"ltr", "number", "punctuation"}:
        return False
    marker_bbox = _bbox(marker.get("bbox") or [])
    previous_bbox = _bbox(previous_token.get("bbox") or [])
    if len(marker_bbox) != 4 or len(previous_bbox) != 4:
        return False
    return max(0.0, float(previous_bbox[0]) - float(marker_bbox[2])) <= max_gap


def _visual_char_items(chars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_chars = [str(item.get("char") or "") for item in chars]
    out: list[dict[str, Any]] = []
    for index, item in enumerate(chars):
        char = str(item.get("char") or "")
        bbox = _bbox(item.get("bbox") or [])
        if not char or len(bbox) != 4:
            continue
        out.append(
            {
                "char": char,
                "bbox": bbox,
                "center_x": _bbox_center_x(bbox),
                "height": max(0.1, float(bbox[3]) - float(bbox[1])),
                "width": max(0.1, float(bbox[2]) - float(bbox[0])),
                "kind": "space" if char.isspace() else _visual_char_kind(raw_chars, index),
                "source_index": index,
            }
        )
    return out


def _visual_tokens_from_chars(chars: list[dict[str, Any]], *, rtl_line: bool) -> list[dict[str, Any]]:
    ltr_tokens, consumed_indexes = _ltr_island_tokens(chars)
    remaining = [item for item in chars if int(item.get("source_index") or 0) not in consumed_indexes]
    return ltr_tokens + _geometry_tokens_from_remaining_chars(remaining, rtl_line=rtl_line)


def _merge_split_ltr_numeric_tokens_in_rtl_line(*, tokens: list[dict[str, Any]], char_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(tokens) < 2:
        return tokens
    median_width = _median_char_width(char_items)
    max_gap = max(2.0, median_width * 0.9)
    merged: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        run = [tokens[index]]
        index += 1
        while index < len(tokens) and _should_merge_ltr_numeric_tokens(run[-1], tokens[index], max_gap=max_gap):
            run.append(tokens[index])
            index += 1
        if len(run) == 1:
            merged.append(run[0])
            continue
        ordered = sorted(run, key=lambda token: float((_bbox(token.get("bbox") or [0, 0, 0, 0]))[0]))
        text = "".join(str(token.get("text") or "") for token in ordered)
        merged.append(_new_visual_token(text=text, bbox=_union_bbox([token.get("bbox") or [] for token in ordered]), kind="ltr"))
    return merged


def _should_merge_ltr_numeric_tokens(right_token: dict[str, Any], left_token: dict[str, Any], *, max_gap: float) -> bool:
    if right_token.get("kind") != "ltr" or left_token.get("kind") != "ltr":
        return False
    right_text = str(right_token.get("text") or "")
    left_text = str(left_token.get("text") or "")
    if NUMBER_CHAR_RE.search(right_text + left_text) is None:
        return False
    right_bbox = _bbox(right_token.get("bbox") or [])
    left_bbox = _bbox(left_token.get("bbox") or [])
    if len(right_bbox) != 4 or len(left_bbox) != 4:
        return False
    gap = max(0.0, right_bbox[0] - left_bbox[2])
    if gap > max_gap:
        return False
    combined_ltr = "".join(str(token.get("text") or "") for token in sorted([right_token, left_token], key=lambda token: float((_bbox(token.get("bbox") or [0, 0, 0, 0]))[0])))
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:_+%#&-]*", combined_ltr))


def _ltr_island_tokens(chars: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[int]]:
    tokens: list[dict[str, Any]] = []
    consumed: set[int] = set()
    median_width = _median_char_width(chars)
    max_gap = max(2.5, median_width * 1.8)
    max_overlap = max(1.0, median_width * 0.35)
    index = 0
    while index < len(chars):
        item = chars[index]
        if item.get("kind") not in {"latin", "number"}:
            index += 1
            continue
        run = [item]
        cursor = index + 1
        while cursor < len(chars):
            candidate = chars[cursor]
            char = str(candidate.get("char") or "")
            kind = str(candidate.get("kind") or "")
            if not _visually_continues_ltr_island(run[-1], candidate, max_gap=max_gap, max_overlap=max_overlap):
                break
            if kind in {"latin", "number"} or (char in LTR_ISLAND_PUNCTUATION and _punctuation_continues_ltr_run(chars, cursor)):
                run.append(candidate)
                cursor += 1
                continue
            break
        token = _new_visual_token(text="".join(str(part.get("char") or "") for part in run), bbox=_union_bbox([part.get("bbox") or [] for part in run]), kind="ltr")
        token["source"] = "source_order_ltr_island"
        tokens.append(token)
        consumed.update(int(part.get("source_index") or 0) for part in run)
        index = cursor
    return tokens, consumed


def _visually_continues_ltr_island(previous: dict[str, Any], candidate: dict[str, Any], *, max_gap: float, max_overlap: float) -> bool:
    previous_bbox = _bbox(previous.get("bbox") or [])
    candidate_bbox = _bbox(candidate.get("bbox") or [])
    if len(previous_bbox) != 4 or len(candidate_bbox) != 4:
        return False
    gap = float(candidate_bbox[0]) - float(previous_bbox[2])
    return -max_overlap <= gap <= max_gap


def _punctuation_continues_ltr_run(chars: list[dict[str, Any]], index: int) -> bool:
    previous_kind = str(chars[index - 1].get("kind") or "") if index > 0 else ""
    next_kind = str(chars[index + 1].get("kind") or "") if index + 1 < len(chars) else ""
    return previous_kind in {"latin", "number"} and next_kind in {"latin", "number"}


def _geometry_tokens_from_remaining_chars(chars: list[dict[str, Any]], *, rtl_line: bool) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    sorted_chars = sorted(chars, key=lambda item: float(item.get("center_x") or 0.0), reverse=rtl_line)
    median_width = _median_char_width(sorted_chars)
    same_word_gap = max(1.5, median_width * 0.55)
    punctuation_gap = max(2.0, median_width * 0.8)
    previous_item: dict[str, Any] | None = None
    for item in sorted_chars:
        char = str(item.get("char") or "")
        kind = str(item.get("kind") or "")
        if kind == "space":
            current = _flush_visual_token(tokens=tokens, current=current)
            previous_item = None
            continue
        if kind == "punctuation":
            if current is not None and previous_item is not None and char in RTL_WORD_INTERNAL_PUNCTUATION and _visual_gap(previous_item, item, rtl_line=rtl_line) <= punctuation_gap:
                _append_item_to_visual_token(current, item)
                previous_item = item
                continue
            current = _flush_visual_token(tokens=tokens, current=current)
            tokens.append(_new_visual_token(text=char, bbox=item.get("bbox") or []))
            previous_item = item
            continue
        if current is not None and current.get("kind") == kind and previous_item is not None and _visual_gap(previous_item, item, rtl_line=rtl_line) <= same_word_gap:
            _append_item_to_visual_token(current, item)
            previous_item = item
            continue
        current = _flush_visual_token(tokens=tokens, current=current)
        current = _new_visual_token(text=char, bbox=item.get("bbox") or [], kind=kind)
        current["source"] = "geometry_ordered_chars"
        previous_item = item
    _flush_visual_token(tokens=tokens, current=current)
    return [token for token in tokens if str(token.get("text") or "").strip()]


def _append_item_to_visual_token(token: dict[str, Any], item: dict[str, Any]) -> None:
    token["text"] = str(token.get("text") or "") + str(item.get("char") or "")
    token["bbox"] = _union_bbox([token.get("bbox") or [], item.get("bbox") or []])
    token["center_x"] = _bbox_center_x(token.get("bbox") or [])


def _median_char_width(chars: list[dict[str, Any]]) -> float:
    widths = [float(item.get("width") or 0.0) for item in chars if item.get("kind") != "space" and float(item.get("width") or 0.0) > 0.0]
    return statistics.median(widths) if widths else 4.0


def _median_char_height(chars: list[dict[str, Any]]) -> float:
    heights = [float(item.get("height") or 0.0) for item in chars if item.get("kind") != "space" and float(item.get("height") or 0.0) > 0.0]
    return statistics.median(heights) if heights else 8.0


def _visual_gap(left_or_right: dict[str, Any], next_item: dict[str, Any], *, rtl_line: bool) -> float:
    first_bbox = _bbox(left_or_right.get("bbox") or [])
    second_bbox = _bbox(next_item.get("bbox") or [])
    if len(first_bbox) != 4 or len(second_bbox) != 4:
        return 999.0
    if rtl_line:
        return max(0.0, float(first_bbox[0]) - float(second_bbox[2]))
    return max(0.0, float(second_bbox[0]) - float(first_bbox[2]))


def _display_text_confidence(line_records: list[dict[str, Any]]) -> str:
    confidences = [str(record.get("confidence") or "low") for record in line_records if str(record.get("text") or "").strip()]
    if not confidences:
        return "low"
    if "low" in confidences:
        return "low"
    if "medium" in confidences:
        return "medium"
    return "high"


def _line_geometry_confidence(*, char_items: list[dict[str, Any]], tokens: list[dict[str, Any]]) -> str:
    if not char_items or not tokens:
        return "low"
    if len(tokens) == 1 and len(char_items) >= 80:
        return "medium"
    widths = [float(item.get("width") or 0.0) for item in char_items if item.get("kind") != "space" and float(item.get("width") or 0.0) > 0.0]
    if not widths:
        return "low"
    median_width = statistics.median(widths)
    if median_width <= 0.2:
        return "low"
    return "high"


def _visual_char_kind(chars: list[str], index: int) -> str:
    char = chars[index]
    if HEBREW_CHAR_RE.match(char):
        return "hebrew"
    if LATIN_CHAR_RE.match(char):
        return "latin"
    if NUMBER_CHAR_RE.match(char):
        return "number"
    if char in {".", "/"}:
        previous_is_digit = index > 0 and NUMBER_CHAR_RE.match(chars[index - 1]) is not None
        next_is_digit = index + 1 < len(chars) and NUMBER_CHAR_RE.match(chars[index + 1]) is not None
        if previous_is_digit and next_is_digit:
            return "number"
    return "punctuation"


def _new_visual_token(*, text: str, bbox: list[Any], kind: str = "punctuation") -> dict[str, Any]:
    clean_bbox = _bbox(bbox)
    return {"text": text, "bbox": clean_bbox, "center_x": _bbox_center_x(clean_bbox), "kind": kind}


def _flush_visual_token(*, tokens: list[dict[str, Any]], current: dict[str, Any] | None) -> dict[str, Any] | None:
    if current is not None and str(current.get("text") or "").strip():
        tokens.append(current)
    return None


def _join_visual_tokens(tokens: list[dict[str, Any]]) -> str:
    out = ""
    previous = ""
    for token in tokens:
        text = str(token.get("text") or "").strip()
        if not text:
            continue
        if not out:
            out = text
        elif _token_attaches_to_previous(text) or previous in RTL_OPENING_PUNCTUATION:
            out += text
        else:
            out += " " + text
        previous = text
    return out


def _token_attaches_to_previous(text: str) -> bool:
    return text in RTL_ATTACH_TO_PREVIOUS or text in RTL_CLOSING_PUNCTUATION


def _clean_visual_spacing(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = re.sub(r"\s+([.,:;!?%])", r"\1", text)
    text = re.sub(r"([(\"״])\s+", r"\1", text)
    text = re.sub(r"\s+([)\]}])", r"\1", text)
    text = re.sub(r"(?<=[\u0590-\u05FF])([-־])\s+(?=[\u0590-\u05FF])", r"\1", text)
    text = re.sub(r"(?<![\u0590-\u05FF])([\u0590-\u05FF])\s+([\u0590-\u05FF][\"״][\u0590-\u05FF])", r"\1\2", text)
    text = re.sub(r"([\u0590-\u05FF])\s+(['׳])(?=\s|\d|$)", r"\1\2", text)
    text = re.sub(r"(['׳])(?=\d)", r"\1 ", text)
    text = re.sub(r"\b(\d{1,4})\s+\.\s+", r"\1. ", text)
    return text.strip()


def _extract_lines(*, raw_block: dict[str, Any]) -> tuple[list[dict[str, Any]], list[float], list[Any]]:
    lines: list[dict[str, Any]] = []
    sizes: list[float] = []
    colors: list[Any] = []
    for raw_line in raw_block.get("lines") or []:
        if not isinstance(raw_line, dict):
            continue
        span_texts: list[str] = []
        for span in raw_line.get("spans") or []:
            if not isinstance(span, dict):
                continue
            text = str(span.get("text") or "")
            if text:
                span_texts.append(text)
            try:
                sizes.append(float(span.get("size")))
            except (TypeError, ValueError):
                pass
            if span.get("color") is not None:
                colors.append(span.get("color"))
        line_text = "".join(span_texts).strip()
        if line_text:
            lines.append({"text": line_text, "bbox": _bbox(raw_line.get("bbox"))})
    return lines, sizes, colors


def _visual_features(*, raw_block: dict[str, Any], page_rect: Any, lines: list[dict[str, Any]], span_sizes: list[float], colors: list[Any]) -> dict[str, Any]:
    bbox = _bbox(raw_block.get("bbox"))
    page_width = max(float(page_rect.width), 1.0)
    page_height = max(float(page_rect.height), 1.0)
    x0, y0, x1, y1 = bbox
    return {
        "x0_ratio": round(x0 / page_width, 4),
        "y0_ratio": round(y0 / page_height, 4),
        "x1_ratio": round(x1 / page_width, 4),
        "y1_ratio": round(y1 / page_height, 4),
        "width_ratio": round(max(0.0, x1 - x0) / page_width, 4),
        "height_ratio": round(max(0.0, y1 - y0) / page_height, 4),
        "line_count": len(lines),
        "char_count": sum(len(str(line.get("text") or "")) for line in lines),
        "min_font_size": round(min(span_sizes), 3) if span_sizes else None,
        "median_font_size": round(statistics.median(span_sizes), 3) if span_sizes else None,
        "max_font_size": round(max(span_sizes), 3) if span_sizes else None,
        "dominant_color": _mode(colors),
    }


def _semantic_scope_decision(block: dict[str, Any]) -> dict[str, Any]:
    features = block.get("visual_features") if isinstance(block.get("visual_features"), dict) else {}
    text = str(block.get("text") or "")
    single_line_text = " ".join(text.split())
    char_count = _int_value(features.get("char_count"), default=len(text))
    line_count = _int_value(features.get("line_count"), default=max(1, text.count("\n") + 1 if text else 0))
    y0_ratio = _float_value(features.get("y0_ratio"))
    y1_ratio = _float_value(features.get("y1_ratio"))
    height_ratio = _float_value(features.get("height_ratio"))
    width_ratio = _float_value(features.get("width_ratio"))
    max_font_size = _float_value(features.get("max_font_size"))
    median_font_size = _float_value(features.get("median_font_size"))
    top_body_heading_shape = bool(EARLY_SECTION_NUMBER_RE.search(single_line_text)) and width_ratio is not None and width_ratio >= 0.45 and line_count <= 6
    small_page_chrome_font = (max_font_size is not None and max_font_size <= 10.5) or (
        median_font_size is not None and median_font_size <= 10.5 and (max_font_size is None or max_font_size <= 12.5)
    )
    reasons: list[str] = []
    category = "semantic_content"
    if char_count <= 0:
        reasons.append("no_visible_text_block")
        category = "decorative_or_empty"
    if width_ratio is not None and width_ratio <= 0.025 and char_count <= 12:
        reasons.append("tiny_isolated_fragment")
        category = "decorative_fragment"
    if y1_ratio is not None and y1_ratio >= 0.94 and char_count <= 100:
        reasons.append("bottom_footer_or_page_number_area")
        category = "header_footer_logo"
    if y0_ratio is not None and y0_ratio <= 0.12 and char_count <= 80:
        reasons.append("top_logo_or_decorative_header_area")
        category = "header_footer_logo"
    if (
        y0_ratio is not None
        and y0_ratio >= 0.88
        and height_ratio is not None
        and height_ratio <= 0.06
        and small_page_chrome_font
        and line_count <= 12
        and char_count <= 180
    ):
        reasons.append("small_font_bottom_page_chrome_area")
        category = "header_footer_logo"
    if (
        y0_ratio is not None
        and y1_ratio is not None
        and height_ratio is not None
        and y0_ratio <= 0.11
        and y1_ratio <= 0.16
        and height_ratio <= 0.06
        and small_page_chrome_font
        and line_count <= 12
        and char_count <= 180
    ):
        reasons.append("small_font_top_page_chrome_area")
        category = "header_footer_logo"
    if (
        y0_ratio is not None
        and height_ratio is not None
        and max_font_size is not None
        and y0_ratio <= 0.22
        and height_ratio >= 0.04
        and line_count >= 3
        and max_font_size >= 12.0
        and char_count <= 260
        and not top_body_heading_shape
    ):
        reasons.append("top_multiline_document_title_or_header")
        category = "header_footer_logo"
    in_scope = not reasons
    return {
        "applied": True,
        "in_semantic_scope": in_scope,
        "status": "included" if in_scope else "skipped",
        "category": category,
        "status_reason": "Block layout is eligible for native-text grouping." if in_scope else "Block layout is outside semantic scope: " + ", ".join(reasons),
        "scope_rule": "Generic layout and text-density filter. It does not use municipality-specific wording or semantic keywords.",
        "evidence": {
            "block_id": str(block.get("block_id") or ""),
            "page": block.get("page"),
            "char_count": char_count,
            "line_count": line_count,
            "width_ratio": width_ratio,
            "height_ratio": height_ratio,
            "y0_ratio": y0_ratio,
            "y1_ratio": y1_ratio,
            "max_font_size": max_font_size,
            "median_font_size": median_font_size,
            "bbox_pdf_points": block.get("bbox_pdf_points") or [],
            "bbox_crop_image_path": block.get("bbox_crop_image_path"),
            "native_text_preview": text[:500],
        },
        "reasons": reasons,
        "suggested_generic_next_step": "Validate the crop/page image visually; if this rule is wrong, adjust generic layout thresholds and retest across document types.",
    }


def _rtl_normalized_text_for_block(text: str) -> str:
    # Keep deterministic and conservative: normalize whitespace only. Do not repair native text.
    return "\n".join(" ".join(line.split()) for line in str(text or "").splitlines()).strip()


def _block_in_semantic_scope(block: dict[str, Any]) -> bool:
    scope = block.get("semantic_scope") if isinstance(block.get("semantic_scope"), dict) else {}
    return scope.get("in_semantic_scope") is not False


def _with_path_validation(payload: dict[str, Any]) -> dict[str, Any]:
    return payload


def _abs(path: Path | str | None) -> str:
    if path is None:
        return ""
    return str(Path(path).expanduser().resolve())


def _bbox(value: Any) -> list[float]:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            x0, y0, x1, y1 = [float(item) for item in value]
            return [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]
        except (TypeError, ValueError):
            pass
    return [0.0, 0.0, 0.0, 0.0]


def _union_bbox(bboxes: list[list[Any]]) -> list[float]:
    valid: list[tuple[float, float, float, float]] = []
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


def _bbox_center_x(bbox: list[Any]) -> float:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return 0.0
    try:
        return (float(bbox[0]) + float(bbox[2])) / 2.0
    except (TypeError, ValueError):
        return 0.0


def _bounded_crop_rect(*, bbox: list[float], page_rect: Any, margin_points: float) -> Any | None:
    if len(bbox) != 4:
        return None
    rect = fitz.Rect(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    if rect.is_empty or rect.is_infinite:
        return None
    margin = max(0.0, float(margin_points))
    expanded = fitz.Rect(rect.x0 - margin, rect.y0 - margin, rect.x1 + margin, rect.y1 + margin)
    bounded = expanded & page_rect
    return None if bounded.is_empty else bounded


def _draw_overlay(*, page: Any, blocks: list[dict[str, Any]]) -> None:
    for block in blocks:
        bbox = block.get("bbox_pdf_points") if isinstance(block.get("bbox_pdf_points"), list) else []
        if len(bbox) != 4:
            continue
        rect = fitz.Rect(*[float(value) for value in bbox])
        page.draw_rect(rect, color=(1, 0, 0), width=0.7)


def _render_page(*, page: Any, output_path: Path, dpi: int, clip: Any | None = None) -> None:
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False, clip=clip)
    pixmap.save(output_path)


def _rect_to_bbox(rect: Any) -> list[float]:
    return [round(float(rect.x0), 3), round(float(rect.y0), 3), round(float(rect.x1), 3), round(float(rect.y1), 3)]


def _warning(*, issue: str, page: int, block_id: str, pdf_path: Path, page_image_path: Path, crop_image_path: Path, severity: str) -> dict[str, Any]:
    return {
        "severity": severity,
        "issue": issue,
        "page": page,
        "block_id": block_id,
        "input_pdf_path": _abs(pdf_path),
        "page_image_path": _abs(page_image_path),
        "crop_image_path": _abs(crop_image_path),
    }


def _block_text_file(block: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"block_id: {block.get('block_id')}",
            f"page: {block.get('page')}",
            f"bbox_pdf_points: {json.dumps(block.get('bbox_pdf_points') or [], ensure_ascii=False)}",
            f"semantic_scope: {json.dumps(block.get('semantic_scope') or {}, ensure_ascii=False)}",
            "",
            "raw_native_text:",
            str(block.get("raw_native_text") or ""),
            "",
            "rtl_normalized_text:",
            str(block.get("rtl_normalized_text") or ""),
            "",
            "display_text:",
            str(block.get("display_text") or ""),
        ]
    ).rstrip() + "\n"


def _page_text_file(*, page_number: int, blocks: list[dict[str, Any]], page_text: str, page_processing_status: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"page: {page_number}",
            f"page_processing_status: {json.dumps(page_processing_status, ensure_ascii=False)}",
            f"block_count: {len(blocks)}",
            "",
            "page_text:",
            page_text,
        ]
    ).rstrip() + "\n"


def _summary(*, pages: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    all_blocks = [block for page in pages for block in page.get("blocks") or [] if isinstance(block, dict)]
    semantic_blocks = [block for block in all_blocks if _block_in_semantic_scope(block)]
    return {
        "page_count": len(pages),
        "text_block_count": len(all_blocks),
        "semantic_text_block_count": len(semantic_blocks),
        "warning_count": len(warnings),
        "page_processing_status_counts": _counts((page.get("page_processing_status") or {}).get("status") for page in pages),
    }


def _audit_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return "\n".join(
        [
            "# Local Native PDF Evidence Audit",
            "",
            f"Status: {payload.get('status')}",
            f"Reason: {payload.get('status_reason')}",
            f"Input PDF: {payload.get('input_pdf_path')}",
            f"Evidence folder: {payload.get('output_pdf_folder_path')}",
            "",
            f"Pages: {summary.get('page_count')}",
            f"Text blocks: {summary.get('text_block_count')}",
            f"Semantic blocks: {summary.get('semantic_text_block_count')}",
            f"Warnings: {summary.get('warning_count')}",
        ]
    ).rstrip() + "\n"


def _unique_pdf_output_folder(*, output_root: Path, pdf_path: Path, pdf_sha256: str, started_at: datetime) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", pdf_path.stem)[:80].strip("._") or "pdf"
    base = output_root / f"{stem}_{pdf_sha256[:12]}_{started_at.strftime('%Y%m%dT%H%M%SZ')}"
    if not base.exists():
        return base
    suffix = 2
    while True:
        candidate = base.with_name(f"{base.name}_{suffix:02d}")
        if not candidate.exists():
            return candidate
        suffix += 1


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mode(values: list[Any]) -> Any:
    if not values:
        return None
    counts: dict[Any, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0]


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
