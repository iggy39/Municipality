from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any

from municipality.docsegmentation_native.pdf_bbox_evidence import _abs, _with_path_validation


SCHEMA_VERSION = "municipality_native_agenda_visual_review_v1"
GOOD_CANDIDATE_STATUS = "good_research_candidate"
IGNORED_PAGE_STATUSES = {"ignored_image_or_nonsemantic_page", "ignored_no_native_text_page"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an HTML visual review report for one local native agenda candidate run.")
    parser.add_argument("agenda_candidate_report", help="Path to one agenda_candidate_report.json file")
    parser.add_argument("--manual-judgement-json", default="", help="Optional manual visual judgement JSON")
    parser.add_argument("--output-dir", default="", help="Output directory. Default: visual_review under the candidate run folder")
    args = parser.parse_args(argv)

    result = write_visual_agenda_review(
        agenda_report_path=Path(args.agenda_candidate_report).expanduser().resolve(),
        manual_judgement_path=Path(args.manual_judgement_json).expanduser().resolve() if str(args.manual_judgement_json).strip() else None,
        output_dir=Path(args.output_dir).expanduser().resolve() if str(args.output_dir).strip() else None,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "reason": result["status_reason"],
                "visual_review_html_path": result["visual_review_html_path"],
                "visual_review_summary_path": result["visual_review_summary_path"],
                "candidate_count": result["summary"]["candidate_count"],
                "navigation_candidate_count": result["summary"]["navigation_candidate_count"],
                "ignored_page_count": result["summary"].get("ignored_page_count", 0),
                "blocking_skipped_page_count": result["summary"].get("blocking_skipped_page_count", 0),
                "final_status_counts": result["summary"]["final_status_counts"],
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
                    "raw_evidence": result["quality_judgement"],
                    "suggested_generic_next_step": result["suggested_generic_next_step"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0 if result["status"] in {"ok", "partial", "needs_review"} else 2


def write_visual_agenda_review(
    *,
    agenda_report_path: Path,
    manual_judgement_path: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    agenda_report_path = agenda_report_path.expanduser().resolve()
    if not agenda_report_path.exists():
        raise FileNotFoundError(f"Agenda candidate report not found: {agenda_report_path}")
    report = json.loads(agenda_report_path.read_text(encoding="utf-8"))
    run_dir = agenda_report_path.parent
    output_dir = (output_dir or (run_dir / "visual_review")).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    original_pdf = _copy_original_pdf_for_review(report=report, output_dir=output_dir)
    manual_payload = _read_manual_judgements(manual_judgement_path)
    candidates = [candidate for candidate in report.get("candidates") or [] if isinstance(candidate, dict)]
    navigation_candidates = [candidate for candidate in report.get("agenda_navigation_candidates") or [] if isinstance(candidate, dict)]
    reviewed_candidates = [_candidate_review(candidate=candidate, manual_payload=manual_payload, original_pdf=original_pdf, report=report) for candidate in candidates]
    reviewed_navigation = [_navigation_review(candidate=candidate, manual_payload=manual_payload, original_pdf=original_pdf, report=report) for candidate in navigation_candidates]
    skipped_pages = [_skipped_page_review(page, manual_payload=manual_payload) for page in report.get("skipped_pages") or [] if isinstance(page, dict)]
    final_status_counts = _counts(review.get("final_status") for review in reviewed_candidates)
    failed_count = sum(1 for review in reviewed_candidates if str(review.get("final_status") or "").startswith("failed"))
    review_count = sum(1 for review in reviewed_candidates if str(review.get("final_status") or "").startswith("needs_review"))
    ignored_page_count = sum(1 for page in skipped_pages if _skipped_page_is_ignored(page))
    blocking_skipped_page_count = len(skipped_pages) - ignored_page_count
    if failed_count:
        status = "needs_review"
        status_reason = "The visual review found at least one failed candidate split."
    elif review_count or blocking_skipped_page_count:
        status = "partial"
        status_reason = "The report was created, but one or more candidates/pages were judged partial or need follow-up before downstream use."
    elif ignored_page_count:
        status = "ok"
        status_reason = "All body candidates have recorded good visual judgement; ignored pages are preserved as non-blocking visual evidence and OCR was not attempted."
    else:
        status = "ok"
        status_reason = "All body candidates have recorded good visual judgement."
    suggested_generic_next_step = (
        "Proceed to the native-text adapter/Step 4/5 path. Ignored pages remain visual evidence only unless a separate manual/OCR workflow is explicitly requested."
        if status == "ok"
        else "Resolve failed or partial body candidates and blocking skipped pages against rendered page evidence before adapter Step 4/5."
    )

    html_path = output_dir / "visual_agenda_review.html"
    summary_path = output_dir / "visual_agenda_review_summary.json"
    summary_payload = _with_path_validation(
        {
            "step": "municipality_native_agenda_visual_review",
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "status_reason": status_reason,
            "accepted_as_ground_truth": False,
            "research_use": "visual_candidate_review_only",
            "agenda_candidate_report_path": _abs(agenda_report_path),
            "manual_judgement_path": _abs(manual_judgement_path) if manual_judgement_path else "",
            "output_dir_path": _abs(output_dir),
            "visual_review_html_path": _abs(html_path),
            "visual_review_summary_path": _abs(summary_path),
            "input_pdf_path": report.get("input_pdf_path"),
            "original_document_pdf_path": original_pdf.get("original_document_pdf_path"),
            "summary": {
                "candidate_count": len(reviewed_candidates),
                "navigation_candidate_count": len(reviewed_navigation),
                "skipped_page_count": len(skipped_pages),
                "ignored_page_count": ignored_page_count,
                "blocking_skipped_page_count": blocking_skipped_page_count,
                "final_status_counts": final_status_counts,
            },
            "quality_judgement": {
                "status": status,
                "reason": status_reason,
                "failed_candidate_ids": [review.get("candidate_id") for review in reviewed_candidates if str(review.get("final_status") or "").startswith("failed")],
                "needs_review_candidate_ids": [review.get("candidate_id") for review in reviewed_candidates if str(review.get("final_status") or "").startswith("needs_review")],
                "navigation_candidate_ids": [review.get("navigation_candidate_id") for review in reviewed_navigation],
                "ignored_page_ids": [page.get("page") for page in skipped_pages if _skipped_page_is_ignored(page)],
                "blocking_skipped_page_ids": [page.get("page") for page in skipped_pages if not _skipped_page_is_ignored(page)],
            },
            "suggested_generic_next_step": suggested_generic_next_step,
            "candidate_reviews": reviewed_candidates,
            "navigation_candidate_reviews": reviewed_navigation,
            "skipped_page_reviews": skipped_pages,
        }
    )
    html_path.write_text(_html_report(report=report, reviews=reviewed_candidates, navigation_reviews=reviewed_navigation, skipped_pages=skipped_pages, output_dir=output_dir, original_pdf=original_pdf), encoding="utf-8")
    summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary_payload


def _candidate_review(*, candidate: dict[str, Any], manual_payload: dict[str, Any], original_pdf: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id") or "")
    manual = (manual_payload.get("candidate_judgements") or {}).get(candidate_id) if isinstance(manual_payload.get("candidate_judgements"), dict) else None
    if isinstance(manual, dict):
        final_status = str(manual.get("final_status") or "needs_review")
        belongs_together = manual.get("belongs_together")
        why = str(manual.get("why") or manual.get("reason") or "")
        visual = str(manual.get("visual_judgement") or "Recorded visual judgement is missing.")
        semantic = str(manual.get("semantic_judgement") or "Recorded semantic judgement is missing.")
    else:
        machine_status = str(candidate.get("status") or "")
        if machine_status.startswith("needs_review_possible_list_body_merge") or machine_status.startswith("needs_review_navigation_list_only"):
            final_status = "failed_or_needs_visual_review"
            belongs_together = False
            why = candidate.get("status_reason") or "Machine diagnostics found possible list/body contamination."
            visual = "Final judgement: failed for downstream acceptance until reviewed. The group may start in a navigation/list region; compare the first crop with the original rendered page."
            semantic = "Final judgement: failed for downstream acceptance. The candidate may mix list/index material with body discussion, so it is not one accepted semantic unit."
        else:
            final_status = "needs_review_visual_judgement_required"
            belongs_together = None
            why = "No independent visual judgement has been recorded for this candidate. It is not accepted for downstream use until the rendered crop and original page are judged."
            visual = "Final judgement: needs_review, not accepted. Rendered candidate crop images must be checked against original page images before downstream use."
            semantic = "Final judgement: needs_review, not accepted. Candidate generation is diagnostic only; no independent semantic judgement has accepted this as one coherent topic."
    return {
        "candidate_id": candidate_id,
        "final_status": final_status,
        "belongs_together": belongs_together,
        "why": why,
        "visual_judgement": visual,
        "semantic_judgement": semantic,
        "original_pdf_path": original_pdf.get("original_document_pdf_path") or report.get("input_pdf_path"),
        "source_input_document_path": original_pdf.get("source_input_document_path") or report.get("input_pdf_path"),
        "page_range": candidate.get("page_range") or [],
        "pages": candidate.get("pages") or [],
        "page_image_paths": _page_image_paths_for_candidate(candidate=candidate, report=report),
        "group_crops": candidate.get("group_crops") or [],
        "heading_text": candidate.get("heading_text") or "",
        "selected_text": candidate.get("selected_text") or "",
        "raw_native_text": candidate.get("raw_native_text") or "",
        "machine_status": candidate.get("status"),
        "machine_status_reason": candidate.get("status_reason"),
        "quality_flags": candidate.get("quality_flags") or [],
        "grouping_diagnostics": candidate.get("grouping_diagnostics") or {},
    }


def _navigation_review(*, candidate: dict[str, Any], manual_payload: dict[str, Any], original_pdf: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    navigation_id = str(candidate.get("navigation_candidate_id") or "")
    manual = (manual_payload.get("navigation_candidate_judgements") or {}).get(navigation_id) if isinstance(manual_payload.get("navigation_candidate_judgements"), dict) else None
    return {
        "navigation_candidate_id": navigation_id,
        "final_status": str((manual or {}).get("final_status") or "navigation_list_candidate_saved_for_review"),
        "visual_judgement": str((manual or {}).get("visual_judgement") or "This crop should show agenda/index/list material that was excluded from body candidates."),
        "semantic_judgement": str((manual or {}).get("semantic_judgement") or "Navigation candidates are preserved as research evidence and are not passed to Municipality Step 4/5."),
        "original_pdf_path": original_pdf.get("original_document_pdf_path") or report.get("input_pdf_path"),
        "page_range": candidate.get("page_range") or [],
        "pages": candidate.get("pages") or [],
        "page_image_paths": _page_image_paths_for_navigation(candidate=candidate, report=report),
        "navigation_crops": candidate.get("navigation_crops") or [],
        "selected_text": candidate.get("selected_text") or "",
        "raw_native_text": candidate.get("raw_native_text") or "",
        "list_run_features": candidate.get("list_run_features") or {},
        "status_reason": candidate.get("status_reason") or "",
    }


def _skipped_page_review(page: dict[str, Any], *, manual_payload: dict[str, Any]) -> dict[str, Any]:
    page_number = str(page.get("page") or "")
    manual = (manual_payload.get("skipped_page_judgements") or {}).get(page_number) if isinstance(manual_payload.get("skipped_page_judgements"), dict) else None
    default_status = str(page.get("status") or "skipped_page_needs_review")
    if default_status not in IGNORED_PAGE_STATUSES and page.get("downstream_blocking") is not False:
        default_status = "skipped_page_needs_review"
    return {
        "page": page.get("page"),
        "final_status": str((manual or {}).get("final_status") or default_status),
        "visual_judgement": str((manual or {}).get("visual_judgement") or _default_skipped_page_visual_judgement(page)),
        "semantic_judgement": str((manual or {}).get("semantic_judgement") or _default_skipped_page_semantic_judgement(page)),
        "status_reason": page.get("status_reason"),
        "skip_reason": page.get("skip_reason"),
        "raw_native_text_preview": page.get("raw_native_text_preview"),
        "no_ocr_attempted": page.get("no_ocr_attempted"),
        "downstream_blocking": page.get("downstream_blocking"),
        "page_image_path": page.get("page_image_path"),
    }


def _default_skipped_page_visual_judgement(page: dict[str, Any]) -> str:
    if _skipped_page_is_ignored(page):
        return "The rendered page is preserved as ignored visual evidence because it has no usable native semantic text. OCR was not attempted by policy."
    return "The rendered page image must be checked because no native body candidate was produced from it."


def _default_skipped_page_semantic_judgement(page: dict[str, Any]) -> str:
    if _skipped_page_is_ignored(page):
        return "Ignored for native-text Step 4/5; use a separate manual/OCR workflow only if explicitly requested."
    return "No downstream use until visual skip judgement is recorded."


def _skipped_page_is_ignored(page: dict[str, Any]) -> bool:
    status = str(page.get("final_status") or page.get("status") or "")
    return status in IGNORED_PAGE_STATUSES or page.get("downstream_blocking") is False


def _html_report(*, report: dict[str, Any], reviews: list[dict[str, Any]], navigation_reviews: list[dict[str, Any]], skipped_pages: list[dict[str, Any]], output_dir: Path, original_pdf: dict[str, Any]) -> str:
    title = "Local Native Agenda/Topic Visual Review"
    proof_html = _boundary_bug_visual_proof_html(reviews=reviews, output_dir=output_dir)
    parts = [
        "<!doctype html>",
        "<html lang=\"en\">",
        "<head>",
        "<meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        f"<title>{_h(title)}</title>",
        _css(),
        "</head>",
        "<body>",
        f"<h1>{_h(title)}</h1>",
        "<section class=\"summary\">",
        f"<p><strong>Original document PDF:</strong> {_pdf_link(original_pdf, output_dir=output_dir)}</p>",
        f"<p><strong>Candidate run:</strong> <code>{_h(report.get('output_run_dir_path'))}</code></p>",
        f"<p><strong>Machine status:</strong> {_badge(str(report.get('status') or 'unknown'))} {_h(report.get('status_reason'))}</p>",
        f"<p><strong>Review rule:</strong> One PDF/run only. Native text only. Body candidates and navigation candidates both require visual review before downstream use.</p>",
        _pdf_viewer(original_pdf, output_dir=output_dir),
        "</section>",
    ]
    if proof_html:
        parts.append(proof_html)
    parts.append("<h2>Body Candidate Groups</h2>")
    for review in reviews:
        parts.append(_candidate_html(review=review, output_dir=output_dir))
    parts.append("<h2>Agenda Navigation Candidates</h2>")
    if navigation_reviews:
        for review in navigation_reviews:
            parts.append(_navigation_html(review=review, output_dir=output_dir))
    else:
        parts.append("<p>No navigation/list candidates were saved.</p>")
    if skipped_pages:
        parts.append("<h2>Skipped Or Ignored Pages</h2>")
        for page in skipped_pages:
            parts.append(_skipped_page_html(page=page, output_dir=output_dir))
    parts.extend(["</body>", "</html>"])
    return "\n".join(parts) + "\n"


def _boundary_bug_visual_proof_html(*, reviews: list[dict[str, Any]], output_dir: Path) -> str:
    proof = _boundary_bug_visual_proof(reviews)
    if not proof:
        return ""
    signature = str(proof.get("signature") or "")
    run_reviews = [review for review in proof.get("run_reviews") or [] if isinstance(review, dict)]
    previous_review = proof.get("previous_review") if isinstance(proof.get("previous_review"), dict) else None
    failed_pair = proof.get("failed_pair") if isinstance(proof.get("failed_pair"), list) else []

    parts = [
        "<h2>Boundary Bug Visual Proof</h2>",
        "<section class=\"summary boundary-proof\">",
        "<p><strong>Finding:</strong> repeated page-header-like text is being treated as a new <code>candidate_start</code>. The visual crops below show that these candidates are continuations of one long agenda item, not new independent agenda topics.</p>",
        "<p><strong>How this proof was selected:</strong> the report found the same normalized heading signature in several consecutive candidate headings. Digits and punctuation-only page-number lines were ignored, so this check is layout/repetition based and does not depend on Hebrew wording or municipality-specific labels.</p>",
        f"<p><strong>Repeated heading signature:</strong> detected in {_h(proof.get('signature_count'))} candidate headings.</p>",
        f"<pre dir=\"rtl\">{_h(signature)}</pre>",
        "<p><strong>Visual sequence:</strong> the first crop is the candidate immediately before the repeated-header run. The following crops begin with the repeated header and continue the same discussion across pages.</p>",
        "<div class=\"image-pair proof-grid\">",
    ]
    if previous_review:
        parts.append(_proof_figure(review=previous_review, output_dir=output_dir, caption_prefix="Previous candidate before repeated-header split"))
    for review in run_reviews[:4]:
        parts.append(_proof_figure(review=review, output_dir=output_dir, caption_prefix="Repeated-header candidate treated as new start"))
    parts.append("</div>")

    if len(failed_pair) >= 2 and all(isinstance(item, dict) for item in failed_pair[:2]):
        parts.extend(
            [
                "<p><strong>Second proof point:</strong> a failed split later in the same run shows the same pattern: a repeated header appears at the boundary, while the crop content visually continues the surrounding item.</p>",
                "<div class=\"image-pair proof-grid\">",
                _proof_figure(review=failed_pair[0], output_dir=output_dir, caption_prefix="Failed split with repeated header"),
                _proof_figure(review=failed_pair[1], output_dir=output_dir, caption_prefix="Following continuation candidate"),
                "</div>",
            ]
        )
    parts.extend(
        [
            "<p><strong>What this proves:</strong> for this run, visual evidence supports the repeated-header boundary bug and explains many page-level fragments.</p>",
            "<p><strong>What this does not prove:</strong> another municipality/run, such as Ashdod, must be judged with its own rendered crops before claiming the exact same root cause there.</p>",
            "</section>",
        ]
    )
    return "\n".join(part for part in parts if part)


def _boundary_bug_visual_proof(reviews: list[dict[str, Any]]) -> dict[str, Any] | None:
    signatures = [_heading_signature(review) for review in reviews]
    counts = _counts(signature for signature in signatures if signature)
    repeated = {signature for signature, count in counts.items() if count >= 3}
    if not repeated:
        return None

    best_run: list[int] = []
    current_run: list[int] = []
    for index, signature in enumerate(signatures):
        if signature not in repeated:
            if len(current_run) > len(best_run):
                best_run = current_run
            current_run = []
            continue
        previous_index = current_run[-1] if current_run else None
        previous_number = _candidate_number(reviews[previous_index]) if previous_index is not None else None
        current_number = _candidate_number(reviews[index])
        if previous_index is not None and signatures[previous_index] == signature and previous_number is not None and current_number == previous_number + 1:
            current_run.append(index)
        else:
            if len(current_run) > len(best_run):
                best_run = current_run
            current_run = [index]
    if len(current_run) > len(best_run):
        best_run = current_run
    if len(best_run) < 2:
        return None

    signature = signatures[best_run[0]]
    previous_review = reviews[best_run[0] - 1] if best_run[0] > 0 else None
    failed_pair: list[dict[str, Any]] = []
    for index, review in enumerate(reviews[:-1]):
        if signatures[index] != signature:
            continue
        if not str(review.get("final_status") or "").startswith("failed"):
            continue
        if len([crop for crop in review.get("group_crops") or [] if isinstance(crop, dict)]) < 2:
            continue
        failed_pair = [review, reviews[index + 1]]
        break
    return {
        "signature": signature,
        "signature_count": counts.get(signature, 0),
        "previous_review": previous_review,
        "run_reviews": [reviews[index] for index in best_run],
        "failed_pair": failed_pair,
    }


def _heading_signature(review: dict[str, Any]) -> str:
    lines: list[str] = []
    for raw_line in str(review.get("heading_text") or "").splitlines():
        line = _normalized_heading_line(raw_line)
        if line:
            lines.append(line)
        if len(lines) >= 3:
            break
    if len(lines) < 2:
        return ""
    return "\n".join(lines)


def _normalized_heading_line(raw_line: str) -> str:
    line = " ".join(str(raw_line or "").split())
    if not line:
        return ""
    if not re.search(r"[A-Za-z\u0590-\u05FF]", line):
        return ""
    return re.sub(r"\d+", "#", line)


def _candidate_number(review: dict[str, Any]) -> int | None:
    match = re.search(r"(\d+)\s*$", str(review.get("candidate_id") or ""))
    return int(match.group(1)) if match else None


def _proof_figure(*, review: dict[str, Any], output_dir: Path, caption_prefix: str) -> str:
    crop = next((item for item in review.get("group_crops") or [] if isinstance(item, dict) and item.get("group_crop_image_path")), None)
    if not crop:
        return ""
    candidate_id = str(review.get("candidate_id") or "")
    page = crop.get("page") or ""
    status = str(review.get("final_status") or "")
    visual_judgement = str(review.get("visual_judgement") or "")
    return "\n".join(
        [
            "<figure>",
            f"<figcaption>{_h(caption_prefix)}: {_h(candidate_id)}, page {_h(page)}, {_h(status)}</figcaption>",
            _image_tag(crop.get("group_crop_image_path"), output_dir=output_dir, alt=f"{candidate_id} proof crop page {page}"),
            f"<p>{_h(visual_judgement)}</p>",
            "</figure>",
        ]
    )


def _candidate_html(*, review: dict[str, Any], output_dir: Path) -> str:
    status = str(review.get("final_status") or "unknown")
    page_images = review.get("page_image_paths") if isinstance(review.get("page_image_paths"), dict) else {}
    parts = [
        f"<article class=\"candidate {_status_class(status)}\">",
        f"<h3>{_h(review.get('candidate_id'))}: {_badge(status)} {_h(review.get('heading_text'))}</h3>",
        "<div class=\"judgement-grid\">",
        f"<div><h4>Visual Judgement</h4><p>{_h(review.get('visual_judgement'))}</p></div>",
        f"<div><h4>Semantic Judgement</h4><p>{_h(review.get('semantic_judgement'))}</p></div>",
        f"<div><h4>Final Belongs-Together Judgement</h4><p>{_h(_belongs_text(review.get('belongs_together')))} {_h(review.get('why'))}</p></div>",
        "</div>",
        f"<p><strong>Pages:</strong> {_h(review.get('pages'))}</p>",
        f"<p><strong>Machine status:</strong> {_h(review.get('machine_status'))} - {_h(review.get('machine_status_reason'))}</p>",
        f"<p><strong>Grouping diagnostics:</strong> <code>{_h(json.dumps(review.get('grouping_diagnostics') or {}, ensure_ascii=False))}</code></p>",
        "<h4>Original Pages And Group Crops</h4>",
    ]
    for crop in review.get("group_crops") or []:
        if not isinstance(crop, dict):
            continue
        page_number = str(crop.get("page") or "")
        original_page = page_images.get(page_number) or page_images.get(int(page_number)) if page_number else ""
        parts.extend(["<div class=\"image-pair\">", "<figure>", f"<figcaption>Original PDF page {_h(page_number)}</figcaption>", _image_tag(original_page, output_dir=output_dir, alt=f"Original page {page_number}"), "</figure>", "<figure>", f"<figcaption>Group crop from page {_h(page_number)}</figcaption>", _image_tag(crop.get("group_crop_image_path"), output_dir=output_dir, alt=f"Group crop page {page_number}"), "</figure>", "</div>"])
    parts.extend(["<details open>", "<summary>Extracted Text</summary>", f"<pre dir=\"rtl\">{_h(review.get('selected_text'))}</pre>", "</details>", "<details>", "<summary>Raw Native Text</summary>", f"<pre dir=\"rtl\">{_h(review.get('raw_native_text'))}</pre>", "</details>", "</article>"])
    return "\n".join(parts)


def _navigation_html(*, review: dict[str, Any], output_dir: Path) -> str:
    page_images = review.get("page_image_paths") if isinstance(review.get("page_image_paths"), dict) else {}
    parts = [
        "<article class=\"candidate partial\">",
        f"<h3>{_h(review.get('navigation_candidate_id'))}: {_badge(str(review.get('final_status') or 'navigation'))}</h3>",
        f"<p><strong>Reason:</strong> {_h(review.get('status_reason'))}</p>",
        f"<p><strong>Visual judgement:</strong> {_h(review.get('visual_judgement'))}</p>",
        f"<p><strong>Semantic judgement:</strong> {_h(review.get('semantic_judgement'))}</p>",
        f"<p><strong>List-run features:</strong> <code>{_h(json.dumps(review.get('list_run_features') or {}, ensure_ascii=False))}</code></p>",
        "<h4>Original Pages And Navigation Crops</h4>",
    ]
    for crop in review.get("navigation_crops") or []:
        if not isinstance(crop, dict):
            continue
        page_number = str(crop.get("page") or "")
        original_page = page_images.get(page_number) or page_images.get(int(page_number)) if page_number else ""
        parts.extend(["<div class=\"image-pair\">", "<figure>", f"<figcaption>Original PDF page {_h(page_number)}</figcaption>", _image_tag(original_page, output_dir=output_dir, alt=f"Original page {page_number}"), "</figure>", "<figure>", f"<figcaption>Navigation crop from page {_h(page_number)}</figcaption>", _image_tag(crop.get("navigation_crop_image_path"), output_dir=output_dir, alt=f"Navigation crop page {page_number}"), "</figure>", "</div>"])
    parts.extend(["<details open>", "<summary>Navigation Text</summary>", f"<pre dir=\"rtl\">{_h(review.get('selected_text'))}</pre>", "</details>", "</article>"])
    return "\n".join(parts)


def _skipped_page_html(*, page: dict[str, Any], output_dir: Path) -> str:
    return "\n".join(["<article class=\"candidate partial\">", f"<h3>Page {_h(page.get('page'))}: {_badge(str(page.get('final_status') or 'skipped'))}</h3>", f"<p><strong>Visual judgement:</strong> {_h(page.get('visual_judgement'))}</p>", f"<p><strong>Semantic judgement:</strong> {_h(page.get('semantic_judgement'))}</p>", "<figure>", f"<figcaption>Original rendered page {_h(page.get('page'))}</figcaption>", _image_tag(page.get("page_image_path"), output_dir=output_dir, alt=f"Skipped page {page.get('page')}"), "</figure>", "</article>"])


def _page_image_paths_for_candidate(*, candidate: dict[str, Any], report: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for block in candidate.get("blocks") or []:
        if isinstance(block, dict) and block.get("page") and block.get("page_image_path"):
            out[str(block.get("page"))] = str(block.get("page_image_path"))
    evidence_folder = Path(str(report.get("evidence_folder_path") or ""))
    for page in candidate.get("pages") or []:
        if str(page) not in out and evidence_folder:
            out[str(page)] = _abs(evidence_folder / "pages" / f"page_{int(page):03d}.png")
    return out


def _page_image_paths_for_navigation(*, candidate: dict[str, Any], report: dict[str, Any]) -> dict[str, str]:
    return _page_image_paths_for_candidate(candidate={"blocks": candidate.get("blocks") or [], "pages": candidate.get("pages") or []}, report=report)


def _read_manual_judgements(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"Manual judgement JSON not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _copy_original_pdf_for_review(*, report: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    source_path = Path(str(report.get("input_pdf_path") or "")).expanduser()
    if not source_path.is_absolute():
        source_path = source_path.resolve()
    target_path = output_dir / "original_document.pdf"
    if not source_path.exists():
        return {"source_input_document_path": _abs(source_path), "original_document_pdf_path": "", "copy_status": "failed_source_missing", "status_reason": "The source document path from the candidate report does not exist."}
    if not _has_pdf_header(source_path):
        return {"source_input_document_path": _abs(source_path), "original_document_pdf_path": "", "copy_status": "failed_not_pdf", "status_reason": "The source document does not start with a %PDF header."}
    shutil.copyfile(source_path, target_path)
    return {"source_input_document_path": _abs(source_path), "original_document_pdf_path": _abs(target_path), "copy_status": "copied_pdf", "status_reason": "The source document was copied for review."}


def _has_pdf_header(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            return file.read(5) == b"%PDF-"
    except OSError:
        return False


def _image_tag(path: Any, *, output_dir: Path, alt: str) -> str:
    if not path:
        return "<p class=\"missing\">Image path missing.</p>"
    return f"<img src=\"{_h(_rel(path, output_dir))}\" alt=\"{_h(alt)}\">"


def _pdf_link(original_pdf: dict[str, Any], *, output_dir: Path) -> str:
    pdf_path = original_pdf.get("original_document_pdf_path")
    if not pdf_path:
        return f"<span class=\"missing\">PDF copy missing: {_h(original_pdf.get('status_reason'))}</span>"
    return f"<a href=\"{_h(_rel(pdf_path, output_dir))}\">Open original_document.pdf</a> <code>{_h(pdf_path)}</code>"


def _pdf_viewer(original_pdf: dict[str, Any], *, output_dir: Path) -> str:
    pdf_path = original_pdf.get("original_document_pdf_path")
    if not pdf_path:
        return ""
    return "\n".join(["<details>", "<summary>Original PDF viewer</summary>", f"<iframe class=\"pdf-viewer\" src=\"{_h(_rel(pdf_path, output_dir))}\"></iframe>", "</details>"])


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


def _status_class(status: str) -> str:
    if status.startswith("good") or status == "ok":
        return "good"
    if status.startswith("failed") or status.startswith("needs_review"):
        return "bad"
    return "partial"


def _belongs_text(value: Any) -> str:
    if value is True:
        return "Success: yes."
    if value is False:
        return "Fail: no."
    return "Needs review. Not accepted for downstream use."


def _css() -> str:
    return """
<style>
:root { color-scheme: light; --bg: #f7f3ed; --card: #fffaf2; --ink: #211b16; --muted: #6c5c4d; --line: #dfd0bf; --good: #0f7b4f; --partial: #a06000; --bad: #b3261e; }
body { margin: 0; padding: 28px; background: var(--bg); color: var(--ink); font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
h1 { font-size: 34px; margin: 0 0 18px; } h2 { margin-top: 34px; border-bottom: 2px solid var(--line); padding-bottom: 6px; } h3 { margin-top: 0; font-size: 22px; }
code { background: #f0e4d5; padding: 2px 5px; border-radius: 5px; word-break: break-all; }
.summary, .candidate { background: var(--card); border: 1px solid var(--line); border-radius: 18px; padding: 18px; margin: 18px 0; box-shadow: 0 8px 20px rgba(60, 40, 20, 0.06); }
.candidate.good { border-left: 8px solid var(--good); } .candidate.partial { border-left: 8px solid var(--partial); } .candidate.bad { border-left: 8px solid var(--bad); }
.badge { display: inline-block; padding: 3px 9px; border-radius: 999px; color: white; font-size: 13px; font-weight: 700; } .badge.good { background: var(--good); } .badge.partial { background: var(--partial); } .badge.bad { background: var(--bad); }
.judgement-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin: 14px 0; } .judgement-grid > div { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 12px; }
.image-pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; margin: 14px 0 24px; } figure { margin: 0; } figcaption { color: var(--muted); font-weight: 700; margin-bottom: 6px; }
img { max-width: 100%; height: auto; border: 1px solid var(--line); border-radius: 10px; background: white; } iframe.pdf-viewer { width: 100%; height: 720px; border: 1px solid var(--line); border-radius: 12px; background: white; }
pre { white-space: pre-wrap; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px; overflow-x: auto; font-size: 15px; } details { margin-top: 12px; } summary { cursor: pointer; font-weight: 700; } .missing { color: var(--bad); font-weight: 700; }
</style>
"""


def _h(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
