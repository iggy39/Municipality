from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts/adapt_docsegmentation_agenda_candidates.py"
    spec = importlib.util.spec_from_file_location("adapt_docsegmentation_agenda_candidates", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapter = _load_module()


def _candidate(
    *,
    pages: list[int] | None = None,
    page_range: list[int] | None = None,
    heading_text: str = "253. שאילתות:",
    selected_text: str | None = None,
    raw_native_text: str | None = None,
) -> dict:
    pages = pages or [3, 4]
    selected = selected_text if selected_text is not None else "253. שאילתות:\n\nשאלה בנושא חסות מסחרית לאצטדיון בלומפילד 16.3.2026"
    raw = raw_native_text if raw_native_text is not None else ":. שאילתות253\n\nחסות מסחרית לאצטדיון בלומפילד 16.3.2026"
    return {
        "candidate_id": "candidate_0001",
        "status": "plausible_candidate",
        "status_reason": "test candidate",
        "page_range": page_range or [min(pages), max(pages)],
        "pages": pages,
        "block_ids": [f"p{page:03d}_b0001" for page in pages],
        "heading_block_ids": [f"p{pages[0]:03d}_b0001"],
        "heading_text": heading_text,
        "selected_text": selected,
        "raw_native_text": raw,
        "rtl_normalized_text": selected,
        "blocks": [
            {
                "block_id": f"p{pages[0]:03d}_b0001",
                "page": pages[0],
                "raw_native_text": heading_text,
                "page_image_path": f"/tmp/page_{pages[0]:03d}.png",
            }
        ],
        "group_crops": [
            {
                "page": pages[0],
                "group_crop_image_path": f"/tmp/candidate_0001_page_{pages[0]:03d}.png",
                "source_block_ids": [f"p{pages[0]:03d}_b0001"],
            }
        ],
    }


def _report(candidate: dict, *, skipped_pages: list[dict] | None = None) -> dict:
    return {
        "step": "native_agenda_topic_candidate_grouping",
        "schema_version": "native_agenda_candidates_v1",
        "input_pdf_path": "/tmp/source.pdf",
        "evidence_folder_path": "/tmp/evidence",
        "output_run_dir_path": "/tmp/evidence/agenda_topic_candidates/test",
        "skipped_pages": skipped_pages or [],
        "candidates": [candidate],
    }


def _manual(*, candidate_status: str = "good_research_candidate", skipped_pages: dict | None = None) -> dict:
    return {
        "schema_version": "manual_visual_agenda_judgement_v1",
        "status": "global_agenda_boundaries_accepted_for_research_review",
        "candidate_judgements": {
            "candidate_0001": {
                "final_status": candidate_status,
                "belongs_together": candidate_status == "good_research_candidate",
                "why": "The crop visually belongs together.",
                "visual_judgement": "The rendered page crop starts at the heading and continues the same item.",
                "semantic_judgement": "One global agenda item.",
            }
        },
        "skipped_page_judgements": skipped_pages or {},
    }


def _write_external_evidence_report(tmp_path: Path) -> Path:
    external_dir = tmp_path / "external_evidence"
    external_dir.mkdir()
    external_pdf_path = tmp_path / "parking_appendix.pdf"
    external_pdf_path.write_bytes(b"%PDF-appendix\n")
    external_page_image_path = external_dir / "page_001.png"
    external_page_text_path = external_dir / "page_001.txt"
    external_page_image_path.write_bytes(b"png")
    external_page_text_path.write_text("Parking appendix question", encoding="utf-8")
    external_report_path = external_dir / "extraction_report.json"
    external_report_path.write_text(
        json.dumps(
            {
                "status": "ok",
                "resolved_input_pdf_path": str(external_pdf_path.resolve()),
                "output_pdf_folder_path": str(external_dir.resolve()),
                "summary": {"page_count": 1, "text_block_count": 3},
                "pages": [
                    {
                        "page": 1,
                        "page_image_path": str(external_page_image_path.resolve()),
                        "page_text_path": str(external_page_text_path.resolve()),
                        "plain_text": "Subject: Parking appendix question. The attached letter asks about paid parking.",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return external_report_path


def test_adapter_uses_readable_text_for_step45_and_preserves_raw_native_provenance(tmp_path: Path) -> None:
    candidate = _candidate()
    result = adapter.adapt_agenda_candidates(
        agenda_report=_report(candidate),
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload=_manual(),
        manual_path=tmp_path / "manual_visual_judgements.json",
        output_dir=tmp_path,
    )

    assert result["validation"]["accept_for_next_step"] is True
    unit = result["grouped_payload"]["structure_units"][0]
    semantic_unit = result["semantic_payload"]["semantic_units"][0]
    assert unit["raw_text"] == candidate["selected_text"]
    assert unit["topic_group_text_raw"] == candidate["selected_text"]
    assert unit["raw_native_text"] == candidate["raw_native_text"]
    assert unit["topic_group_raw_native_text"] == candidate["raw_native_text"]
    assert unit["docsegmentation_selected_text"] == candidate["selected_text"]
    assert unit["docsegmentation_raw_native_text"] == candidate["raw_native_text"]
    assert unit["structure_evidence"]["source_candidate_raw_native_text"] == candidate["raw_native_text"]
    assert semantic_unit["raw_text"] == candidate["selected_text"]
    assert semantic_unit["raw_native_text"] == candidate["raw_native_text"]
    assert unit["topic_group_role"] == "anchor"
    assert unit["topic_group_pages"] == [3, 4]
    assert unit["topic_group_source_block_ids"] == ["p003_b0001", "p004_b0001"]
    assert unit["structure_unit_id"] == semantic_unit["semantic_unit_id"]
    assert semantic_unit["explicit_dates"] == ["16.3.2026"]


def test_adapter_blocks_without_manual_visual_judgement(tmp_path: Path) -> None:
    result = adapter.adapt_agenda_candidates(
        agenda_report=_report(_candidate()),
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload={},
        manual_path=None,
        output_dir=tmp_path,
    )

    assert result["validation"]["status"] == "needs_review"
    assert result["validation"]["accept_for_next_step"] is False
    assert result["validation"]["review_issues"][0]["reason"] == "missing_manual_visual_judgement_json"


def test_adapter_html_marks_missing_manual_judgement_as_not_accepted(tmp_path: Path) -> None:
    result = adapter.adapt_agenda_candidates(
        agenda_report=_report(_candidate()),
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload={},
        manual_path=None,
        output_dir=tmp_path,
    )

    html = adapter._html_report(result=result, output_dir=tmp_path, max_preview_chars=500, downstream={})

    assert "Unknown." not in html
    assert "No recorded visual judgement is available" not in html
    assert "No manual visual judgement is available" not in html
    assert "Needs review. Not accepted for downstream use." in html
    assert "Final judgement: needs_review, not accepted." in html


def test_adapter_blocks_container_parent_when_internal_child_topics_exist(tmp_path: Path) -> None:
    internal_split_path = tmp_path / "internal_topic_split_report.json"
    internal_report = {
        "status": "needs_review",
        "internal_topic_split_json_path": str(internal_split_path),
        "html_report_path": str(tmp_path / "internal_topic_split_review.html"),
        "parent_topic_splits": [
            {
                "parent_candidate_id": "candidate_0001",
                "status": "needs_visual_semantic_review",
                "status_reason": "Parent has child topics.",
                "parent_downstream_policy": "container_only_when_child_topics_exist",
                "boundary_candidates": [{"boundary_id": "b1"}, {"boundary_id": "b2"}],
                "child_topics": [
                    {"child_topic_id": "candidate_0001_topic_0001", "status": "needs_visual_semantic_review", "split_source": "visual_marker", "anchor_text": "1. first child"},
                    {"child_topic_id": "candidate_0001_topic_0002", "status": "needs_visual_semantic_review", "split_source": "visual_marker", "anchor_text": "2. second child"},
                ],
            }
        ],
    }

    result = adapter.adapt_agenda_candidates(
        agenda_report=_report(_candidate()),
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload=_manual(),
        manual_path=tmp_path / "manual_visual_judgements.json",
        output_dir=tmp_path,
        internal_topic_split_report=internal_report,
        internal_topic_split_path=internal_split_path,
    )

    assert result["validation"]["status"] == "needs_review"
    assert result["validation"]["accept_for_next_step"] is False
    issue = result["validation"]["review_issues"][0]
    assert issue["reason"] == "candidate_has_internal_child_topics_container_only"
    assert issue["raw_evidence"]["child_topic_anchors"] == ["1. first child", "2. second child"]
    unit = result["grouped_payload"]["structure_units"][0]
    assert unit["internal_topic_split"]["child_topic_count"] == 2


def test_adapter_blocks_candidate_that_omits_middle_native_page(tmp_path: Path) -> None:
    result = adapter.adapt_agenda_candidates(
        agenda_report=_report(_candidate(pages=[3, 5], page_range=[3, 5])),
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload=_manual(),
        manual_path=tmp_path / "manual_visual_judgements.json",
        output_dir=tmp_path,
    )

    assert result["validation"]["status"] == "failed"
    assert result["validation"]["accept_for_next_step"] is False
    assert result["validation"]["blocking_issues"][0]["reason"] == "candidate_omits_middle_native_page"
    assert result["validation"]["blocking_issues"][0]["page"] == 4


def test_adapter_allows_missing_middle_page_when_image_only_skip_is_manually_accepted(tmp_path: Path) -> None:
    skipped_page = {
        "page": 4,
        "status": "skipped_image_only_page",
        "skip_reason": "image_only_page",
        "status_reason": "Page has no native text.",
    }
    result = adapter.adapt_agenda_candidates(
        agenda_report=_report(_candidate(pages=[3, 5], page_range=[3, 5]), skipped_pages=[skipped_page]),
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload=_manual(
            skipped_pages={
                "4": {
                    "final_status": "skipped_image_only_page_accepted",
                    "visual_judgement": "The rendered page is image-only.",
                }
            }
        ),
        manual_path=tmp_path / "manual_visual_judgements.json",
        output_dir=tmp_path,
    )

    assert result["validation"]["accept_for_next_step"] is True
    assert result["validation"]["warnings"][0]["reason"] == "manual_accepted_skipped_image_only_page"


def test_adapter_allows_ignored_nonsemantic_page_without_ocr(tmp_path: Path) -> None:
    skipped_page = {
        "page": 40,
        "status": "ignored_image_or_nonsemantic_page",
        "skip_reason": "native_blocks_outside_semantic_scope",
        "status_reason": "No usable native semantic text; OCR was not attempted.",
        "no_ocr_attempted": True,
        "downstream_blocking": False,
    }
    result = adapter.adapt_agenda_candidates(
        agenda_report=_report(_candidate(pages=[36, 37, 38, 39], page_range=[36, 39]), skipped_pages=[skipped_page]),
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload=_manual(),
        manual_path=tmp_path / "manual_visual_judgements.json",
        output_dir=tmp_path,
    )

    assert result["validation"]["accept_for_next_step"] is True
    assert result["validation"]["warnings"][0]["reason"] == "ignored_nonsemantic_or_image_page_by_policy"
    assert result["validation"]["warnings"][0]["no_ocr_attempted"] is True


def test_adapter_blocks_strong_external_reference_without_reviewed_link(tmp_path: Path) -> None:
    text = "The question and answer are attached to this protocol as an appendix."
    result = adapter.adapt_agenda_candidates(
        agenda_report=_report(_candidate(heading_text="Parking appendix question", selected_text=text, raw_native_text=text)),
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload=_manual(),
        manual_path=tmp_path / "manual_visual_judgements.json",
        output_dir=tmp_path,
    )

    assert result["validation"]["status"] == "needs_review"
    assert result["validation"]["accept_for_next_step"] is False
    assert result["validation"]["review_issues"][0]["reason"] == "external_document_reference_needs_reviewed_link"
    unit = result["grouped_payload"]["structure_units"][0]
    preflight = unit["candidate_semantic_preflight"]
    assert preflight["needs_external_document_link"] is True
    assert preflight["external_document_references"][0]["strength"] == "strong"


def test_adapter_resolves_external_reference_with_reviewed_manifest_link(tmp_path: Path) -> None:
    text = "The question and answer are attached to this protocol as an appendix."
    external_report_path = _write_external_evidence_report(tmp_path)
    manifest_path = tmp_path / "external_links.json"
    manifest_path.write_text(
        json.dumps(
            {
                "links": [
                    {
                        "candidate_id": "candidate_0001",
                        "document_role": "appendix",
                        "external_evidence_report_path": str(external_report_path.resolve()),
                        "child_label_he": "Parking appendix question",
                        "topic_supporting_quote_he": "Subject: Parking appendix question.",
                        "visual_judgement": {
                            "status": "visually_supported_research_link",
                            "reason": "Rendered external page title matches the candidate heading and appendix reference.",
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = adapter.adapt_agenda_candidates(
        agenda_report=_report(_candidate(heading_text="Parking appendix question", selected_text=text, raw_native_text=text)),
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload=_manual(),
        manual_path=tmp_path / "manual_visual_judgements.json",
        output_dir=tmp_path,
        external_link_manifest_path=manifest_path,
    )

    assert result["validation"]["accept_for_next_step"] is True
    unit = result["grouped_payload"]["structure_units"][0]
    preflight = unit["candidate_semantic_preflight"]
    assert preflight["needs_external_document_link"] is False
    assert preflight["semantic_preflight_status"] == "prepared_with_external_document_link"
    assert preflight["external_document_references"][0]["status"] == "resolved_by_external_document_link"
    assert preflight["external_document_links"][0]["status"] == "visually_supported_research_link"
    context = result["reviewed_external_attachment_contexts"][0]
    assert context["source_candidate_id"] == "candidate_0001"
    assert context["child_label_he"] == "Parking appendix question"
    assert context["external_page_image_paths"]


def test_adapter_allows_marked_missing_external_document_as_nonblocking_warning(tmp_path: Path) -> None:
    text = "The question and answer are attached to this protocol as an appendix."
    manifest_path = tmp_path / "external_links_with_missing.json"
    manifest_path.write_text(
        json.dumps(
            {
                "links": [],
                "missing_external_documents": [
                    {
                        "candidate_id": "candidate_0001",
                        "missing_document_id": "candidate_0001_missing_appendix",
                        "status": "missing_external_document",
                        "document_title": "Missing appendix",
                        "document_type": "appendix",
                        "reason": "The appendix was referenced but not found after local search.",
                        "search_evidence": ["No matching local attachment was available."],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = adapter.adapt_agenda_candidates(
        agenda_report=_report(_candidate(heading_text="Parking appendix question", selected_text=text, raw_native_text=text)),
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload=_manual(),
        manual_path=tmp_path / "manual_visual_judgements.json",
        output_dir=tmp_path,
        external_link_manifest_path=manifest_path,
    )

    assert result["validation"]["accept_for_next_step"] is True
    warning_reasons = [warning["reason"] for warning in result["validation"]["warnings"]]
    assert "external_document_reference_marked_missing_nonblocking" in warning_reasons
    unit = result["grouped_payload"]["structure_units"][0]
    preflight = unit["candidate_semantic_preflight"]
    assert preflight["needs_external_document_link"] is True
    assert preflight["semantic_preflight_status"] == "prepared_with_missing_external_documents_marked"
    assert preflight["missing_external_documents"][0]["nonblocking_for_step4_5"] is True
    assert result["reviewed_external_attachment_contexts"] == []


def test_adapter_keeps_unsupported_external_link_in_needs_review(tmp_path: Path) -> None:
    text = "The question and answer are attached to this protocol as an appendix."
    external_report_path = _write_external_evidence_report(tmp_path)
    manifest_path = tmp_path / "external_links.json"
    manifest_path.write_text(
        json.dumps(
            {
                "links": [
                    {
                        "candidate_id": "candidate_0001",
                        "document_role": "appendix",
                        "external_evidence_report_path": str(external_report_path.resolve()),
                        "visual_judgement": {"status": "needs_visual_review", "reason": "Not reviewed yet."},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = adapter.adapt_agenda_candidates(
        agenda_report=_report(_candidate(heading_text="Parking appendix question", selected_text=text, raw_native_text=text)),
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload=_manual(),
        manual_path=tmp_path / "manual_visual_judgements.json",
        output_dir=tmp_path,
        external_link_manifest_path=manifest_path,
    )

    assert result["validation"]["status"] == "needs_review"
    assert result["validation"]["review_issues"][0]["reason"] == "external_document_reference_needs_reviewed_link"
    link = result["grouped_payload"]["structure_units"][0]["external_document_links"][0]
    assert link["status"] == "external_document_link_needs_visual_review"
    assert result["reviewed_external_attachment_contexts"] == []


def test_adapter_fails_missing_external_evidence_report_path(tmp_path: Path) -> None:
    manifest_path = tmp_path / "external_links.json"
    manifest_path.write_text(
        json.dumps(
            {
                "links": [
                    {
                        "candidate_id": "candidate_0001",
                        "document_role": "appendix",
                        "external_evidence_report_path": str((tmp_path / "missing_report.json").resolve()),
                        "visual_judgement": {"status": "visually_supported_research_link"},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = adapter.adapt_agenda_candidates(
        agenda_report=_report(_candidate()),
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload=_manual(),
        manual_path=tmp_path / "manual_visual_judgements.json",
        output_dir=tmp_path,
        external_link_manifest_path=manifest_path,
    )

    assert result["validation"]["status"] == "failed"
    assert result["validation"]["blocking_issues"][0]["reason"] == "external_evidence_report_not_found"


def test_adapter_does_not_block_weak_external_reference_mention(tmp_path: Path) -> None:
    text = "Approval of addendum to existing development agreement."
    result = adapter.adapt_agenda_candidates(
        agenda_report=_report(_candidate(heading_text="Development agreement addendum", selected_text=text, raw_native_text=text)),
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload=_manual(),
        manual_path=tmp_path / "manual_visual_judgements.json",
        output_dir=tmp_path,
    )

    assert result["validation"]["accept_for_next_step"] is True
    references = result["grouped_payload"]["structure_units"][0]["external_document_references"]
    assert references[0]["strength"] == "weak"
    assert references[0]["status"] == "document_reference_mention"


def test_html_report_uses_docsegmentation_visual_review_style(tmp_path: Path) -> None:
    result = adapter.adapt_agenda_candidates(
        agenda_report=_report(_candidate()),
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload=_manual(),
        manual_path=tmp_path / "manual_visual_judgements.json",
        output_dir=tmp_path,
    )

    html = adapter._html_report(result=result, output_dir=tmp_path, max_preview_chars=500, downstream={})

    assert '<section class="summary">' in html
    assert 'class="candidate good"' in html
    assert 'class="badge good"' in html
    assert 'class="judgement-grid"' in html
    assert 'class="image-pair"' in html
    assert "Original Pages And Group Crops" in html
    assert "Raw Native Text" in html


def test_html_report_displays_navigation_candidates_without_creating_units(tmp_path: Path) -> None:
    report = _report(_candidate())
    report["agenda_navigation_candidates"] = [
        {
            "navigation_candidate_id": "navigation_0001",
            "status": "navigation_list_candidate",
            "status_reason": "Dense agenda/index rows were saved separately.",
            "pages": [1],
            "page_range": [1, 1],
            "block_ids": ["p001_b0001", "p001_b0002"],
            "selected_text": "1. First listed item\n2. Second listed item",
            "raw_native_text": "1. First listed item\n2. Second listed item",
            "list_run_features": {"list_like_block_count": 2},
            "blocks": [
                {
                    "block_id": "p001_b0001",
                    "page": 1,
                    "page_image_path": "/tmp/page_001.png",
                }
            ],
            "navigation_crops": [
                {
                    "page": 1,
                    "navigation_crop_image_path": "/tmp/navigation_0001_page_001.png",
                }
            ],
        }
    ]
    result = adapter.adapt_agenda_candidates(
        agenda_report=report,
        agenda_report_path=tmp_path / "agenda_candidate_report.json",
        manual_payload=_manual(),
        manual_path=tmp_path / "manual_visual_judgements.json",
        output_dir=tmp_path,
    )

    html = adapter._html_report(result=result, output_dir=tmp_path, max_preview_chars=500, downstream={})

    assert len(result["grouped_payload"]["structure_units"]) == 1
    assert result["grouped_payload"]["agenda_navigation_candidates"][0]["navigation_candidate_id"] == "navigation_0001"
    assert "Agenda Navigation Candidates" in html
    assert "navigation_0001" in html
    assert "Not converted into semantic or structure units" in html


def test_main_writes_protocol_level_html_and_legacy_copy(tmp_path: Path) -> None:
    agenda_path = tmp_path / "agenda_candidate_report.json"
    manual_path = tmp_path / "manual_visual_judgements.json"
    output_dir = tmp_path / "adapter"
    protocol_report_dir = tmp_path / "protocol_report"
    agenda_path.write_text(json.dumps(_report(_candidate()), ensure_ascii=False), encoding="utf-8")
    manual_path.write_text(json.dumps(_manual(), ensure_ascii=False), encoding="utf-8")

    exit_code = adapter.main(
        [
            "--agenda-candidate-report-json",
            str(agenda_path),
            "--manual-visual-judgement-json",
            str(manual_path),
            "--output-dir",
            str(output_dir),
            "--protocol-report-dir",
            str(protocol_report_dir),
        ]
    )

    assert exit_code == 0
    assert (protocol_report_dir / "protocol_hybrid_review.html").exists()
    assert (output_dir / "docsegmentation_adapter_report.html").exists()
    assert (output_dir / "reviewed_external_attachment_contexts.json").exists()
    assert '<section class="summary">' in (protocol_report_dir / "protocol_hybrid_review.html").read_text(encoding="utf-8")
