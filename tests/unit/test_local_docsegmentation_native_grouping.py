from __future__ import annotations

from pathlib import Path

from municipality.docsegmentation_native import native_agenda_candidates as agenda
from municipality.docsegmentation_native import native_agenda_visual_review as visual_review
from municipality.docsegmentation_native import pdf_bbox_evidence as evidence
from municipality.docsegmentation_native.display_text_cleanup import cleanup_display_text
from scripts import validate_rtl_display_cleanup_large_visual_review as cleanup_review


def _raw_block(
    block_id: str,
    *,
    page: int,
    text: str,
    y0: float,
    y1: float | None = None,
    x0: float = 0.1,
    width: float = 0.7,
    max_font: float = 12.0,
    line_count: int = 1,
) -> dict:
    y1 = y1 if y1 is not None else y0 + 0.02
    return {
        "block_id": block_id,
        "page": page,
        "raw_native_text": text,
        "rtl_normalized_text": text,
        "bbox_pdf_points": [100.0, y0 * 1000.0, 100.0 + width * 500.0, y1 * 1000.0],
        "bbox_crop_image_path": f"/tmp/{block_id}.png",
        "bbox_text_path": f"/tmp/{block_id}.txt",
        "page_image_path": f"/tmp/page_{page:03d}.png",
        "visual_features": {
            "y0_ratio": y0,
            "y1_ratio": y1,
            "x0_ratio": x0,
            "x1_ratio": x0 + width,
            "width_ratio": width,
            "line_count": line_count,
            "char_count": len(text),
            "max_font_size": max_font,
            "median_font_size": max_font,
        },
        "semantic_scope": {"in_semantic_scope": True, "category": "semantic_content"},
        "quality_flags": [],
    }


def _items(raw_blocks: list[dict]) -> list[dict]:
    return [agenda._candidate_block_item(block) for block in raw_blocks]


def _visual_chars(text: str, *, x0: float, width: float = 8.0, rtl: bool = False) -> list[dict]:
    chars = []
    if rtl:
        x = x0 + width * len(text)
        for char in text:
            chars.append({"char": char, "bbox": [x - width, 10.0, x, 20.0]})
            x -= width
        return chars
    x = x0
    for char in text:
        chars.append({"char": char, "bbox": [x, 10.0, x + width, 20.0]})
        x += width
    return chars


def test_same_page_navigation_list_is_saved_separately_before_body_anchor() -> None:
    raw_blocks = [
        _raw_block("p003_b0019", page=3, text='22. Budget update - attachment', y0=0.54, width=0.25, line_count=2),
        _raw_block("p003_b0020", page=3, text='23. Donation declaration - attachment', y0=0.57, width=0.30, line_count=2),
        _raw_block("p003_b0021", page=3, text='24. Cancellation of April council meeting', y0=0.60, width=0.50, line_count=2),
        _raw_block("p003_b0022", page=3, text='25. Committee membership changes', y0=0.63, width=0.40, line_count=2),
        _raw_block("p003_b0023", page=3, text='1.1. Air pollution question and mayor response', y0=0.69, width=0.78, max_font=13.0, line_count=2),
        _raw_block("p003_b0024", page=3, text='The council member read the mayor response to the question.', y0=0.73, width=0.74, max_font=11.0),
        _raw_block("p003_b0025", page=3, text='This is continuous body discussion. It has enough text to be considered body evidence.', y0=0.77, width=0.76, max_font=11.0),
    ]
    blocks = agenda._annotate_grouping_context(_items(raw_blocks))
    anchor_index = agenda._first_body_anchor_index(blocks)
    navigation_runs = agenda._detect_navigation_list_runs(blocks, first_body_anchor_index=anchor_index)
    navigation_ids = {block["block_id"] for run in navigation_runs for block in run}
    body_blocks = agenda._promote_body_start_anchors([block for block in blocks if block["block_id"] not in navigation_ids])
    grouped = agenda._group_blocks_into_candidates(body_blocks, min_body_chars=80)
    navigation_candidates = agenda._navigation_candidates_from_runs(navigation_runs)

    assert anchor_index == 4
    assert len(navigation_candidates) == 1
    assert "22. Budget update" in navigation_candidates[0]["selected_text"]
    assert len(grouped) == 1
    assert grouped[0]["heading_text"].startswith("1.1. Air pollution")
    assert "22. Budget update" not in grouped[0]["selected_text"]


def test_internal_numbered_reference_stays_inside_existing_body_candidate() -> None:
    intro = "Opening body paragraph with enough continuous discussion text. " * 4
    internal = "6 this internal reference belongs to the current discussion, because it mentions the current agenda and continues the sentence, with punctuation."
    raw_blocks = [
        _raw_block("p006_b0029", page=6, text="254. Proposal for the agenda", y0=0.19, width=0.25, max_font=13.0),
        _raw_block("p006_b0030", page=6, text=intro, y0=0.24, width=0.76, max_font=12.0, line_count=3),
        _raw_block("p006_b0031", page=6, text=internal, y0=0.33, width=0.76, max_font=12.0),
        _raw_block("p006_b0032", page=6, text="The following discussion continues the same agenda item.", y0=0.37, width=0.70, max_font=12.0),
    ]
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(_items(raw_blocks)))
    grouped = agenda._group_blocks_into_candidates(blocks, min_body_chars=120)

    assert len(grouped) == 1
    assert internal in grouped[0]["selected_text"]
    internal_blocks = [block for block in grouped[0]["blocks"] if block["block_id"] == "p006_b0031"]
    assert internal_blocks[0]["role"] == "body"


def test_navigation_candidate_data_model_preserves_raw_text_and_evidence_paths() -> None:
    raw_blocks = [
        _raw_block("p001_b0001", page=1, text="1. First listed matter", y0=0.3),
        _raw_block("p001_b0002", page=1, text="2. Second listed matter", y0=0.34),
        _raw_block("p001_b0003", page=1, text="3. Third listed matter", y0=0.38),
    ]
    blocks = agenda._annotate_grouping_context(_items(raw_blocks))
    runs = agenda._detect_navigation_list_runs(blocks, first_body_anchor_index=None)
    navigation_candidates = agenda._navigation_candidates_from_runs(runs)

    assert len(navigation_candidates) == 1
    candidate = navigation_candidates[0]
    assert candidate["accepted_as_ground_truth"] is False
    assert candidate["status"] == "navigation_list_candidate"
    assert candidate["block_ids"] == ["p001_b0001", "p001_b0002", "p001_b0003"]
    assert "First listed matter" in candidate["raw_native_text"]
    assert candidate["suggested_generic_next_step"]


def test_decision_table_body_after_strong_heading_is_not_navigation_list_only() -> None:
    raw_blocks = [
        _raw_block("p010_b0007", page=10, text="13. Approval of a municipal use agreement for an existing building", y0=0.19, width=0.78, max_font=13.0, line_count=3),
        _raw_block("p010_b0008", page=10, text="Property details and plan references continue the same blue body heading", y0=0.24, width=0.76, max_font=13.0, line_count=2),
        _raw_block("p010_b0009", page=10, text="More heading details - attachment", y0=0.28, width=0.60, max_font=13.0),
        _raw_block("p010_b0010", page=10, text="Decisions Subject Content Recipients Effective date", y0=0.34, x0=0.18, width=0.70, max_font=12.0, line_count=3),
        _raw_block("p010_b0011", page=10, text="9. Approval of agreement", y0=0.38, x0=0.76, width=0.12, max_font=11.0, line_count=3),
        _raw_block("p010_b0012", page=10, text="Assets department", y0=0.38, x0=0.34, width=0.12, max_font=11.0, line_count=2),
        _raw_block("p010_b0013", page=10, text="11/03/2026", y0=0.39, x0=0.19, width=0.10, max_font=11.0),
        _raw_block("p010_b0014", page=10, text="Council members approve the agreement", y0=0.40, x0=0.50, width=0.25, max_font=11.0),
        _raw_block("p010_b0015", page=10, text="For: 21 members", y0=0.44, x0=0.63, width=0.12, max_font=11.0),
        _raw_block("p010_b0016", page=10, text="Abstain: 5 members", y0=0.46, x0=0.63, width=0.13, max_font=11.0),
        _raw_block("p010_b0017", page=10, text="Against: none", y0=0.48, x0=0.68, width=0.08, max_font=11.0),
        _raw_block("p010_b0018", page=10, text="Known parcel details and public-building use continue in the subject cell", y0=0.52, x0=0.77, width=0.12, max_font=11.0, line_count=4),
    ]
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(_items(raw_blocks)))
    grouped = agenda._group_blocks_into_candidates(blocks, min_body_chars=80)

    assert len(grouped) == 1
    assert grouped[0]["status"] == "plausible_candidate"
    assert grouped[0]["grouping_diagnostics"]["contamination"]["status"] == "ok"


def test_narrow_repeated_decision_table_heading_stays_inside_current_candidate() -> None:
    raw_blocks = [
        _raw_block("p009_b0026", page=9, text="Section 12: Approval of a use agreement for kindergarten classrooms", y0=0.52, width=0.74, max_font=13.0, line_count=3),
        _raw_block("p009_b0027", page=9, text="Introductory body details with enough explanation before the decision table. " * 3, y0=0.58, width=0.72, line_count=3),
        _raw_block("p009_b0031", page=9, text="Decisions Subject Content Recipients Effective date", y0=0.68, width=0.70, line_count=3),
        _raw_block("p009_b0032", page=9, text="8. * Approval of use agreement for two kindergarten classrooms", y0=0.75, x0=0.77, width=0.11, max_font=12.0, line_count=6),
        _raw_block("p009_b0035", page=9, text="Council members approve the agreement by majority vote", y0=0.76, x0=0.49, width=0.26, max_font=12.0, line_count=2),
    ]
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(_items(raw_blocks)))
    grouped = agenda._group_blocks_into_candidates(blocks, min_body_chars=80)

    assert len(grouped) == 1
    assert "8. * Approval" in grouped[0]["selected_text"]
    repeated_rows = [block for block in grouped[0]["blocks"] if block["block_id"] == "p009_b0032"]
    assert repeated_rows[0]["role"] == "body"
    assert "downgraded_internal_enumerated_body_item" in repeated_rows[0]["role_reasons"]


def test_table_only_candidate_with_five_line_global_heading_is_not_contamination() -> None:
    raw_blocks = [
        _raw_block("p008_b0003", page=8, text="Section10: Approval of addendum to use and development agreement for existing allocation", y0=0.11, width=0.78, max_font=13.0, line_count=5),
        _raw_block("p008_b0007", page=8, text="Decisions Subject Content Recipients Effective date", y0=0.24, width=0.70, line_count=3),
        _raw_block("p008_b0008", page=8, text="6. * Approval of addendum", y0=0.30, x0=0.78, width=0.10, max_font=11.0, line_count=3),
        _raw_block("p008_b0028", page=8, text="Assets department", y0=0.30, x0=0.33, width=0.12, max_font=11.0, line_count=2),
        _raw_block("p008_b0023", page=8, text="Council members approve the agreements", y0=0.31, x0=0.49, width=0.26, max_font=11.0),
        _raw_block("p008_b0025", page=8, text="For: 20 members", y0=0.36, x0=0.63, width=0.12, max_font=11.0, line_count=3),
        _raw_block("p008_b0026", page=8, text="Abstain: 6 members", y0=0.38, x0=0.63, width=0.12, max_font=11.0),
        _raw_block("p008_b0013", page=8, text="Registration number 580479004", y0=0.44, x0=0.78, width=0.10, max_font=11.0, line_count=3),
        _raw_block("p008_b0015", page=8, text="2022 partial parcel", y0=0.50, x0=0.79, width=0.09, max_font=11.0, line_count=2),
    ]
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(_items(raw_blocks)))
    grouped = agenda._group_blocks_into_candidates(blocks, min_body_chars=80)

    assert len(grouped) == 1
    assert grouped[0]["grouping_diagnostics"]["contamination"]["status"] == "ok"


def test_numeric_legal_clauses_stay_inside_body_but_next_large_section_splits() -> None:
    legal_clause = "1 according to sections 250 and 251 of the ordinance and section 11 of the licensing law, 1968"
    raw_blocks = [
        _raw_block("p018_b0010", page=18, text="259. Legal ordinance amendment", y0=0.35, width=0.75, max_font=13.0),
        _raw_block("p018_b0011", page=18, text="The council approved this ordinance after discussion and vote. " * 3, y0=0.40, width=0.78, max_font=12.0, line_count=3),
        _raw_block("p018_b0012", page=18, text=legal_clause, y0=0.55, width=0.76, max_font=12.0, line_count=2),
        _raw_block("p018_b0013", page=18, text="2 install this bylaw for temporary emergency operation, 2026", y0=0.58, width=0.45, max_font=12.0, line_count=2),
        _raw_block("p018_b0014", page=18, text="The legal body continues with more explanation and enough body text for the same item.", y0=0.62, width=0.72, max_font=12.0, line_count=2),
        _raw_block("p018_b0015", page=18, text="1 retroactive", y0=0.74, x0=0.79, width=0.08, max_font=12.0, line_count=2),
        _raw_block("p018_b0016", page=18, text="262. Appointments and changes in corporations", y0=0.78, width=0.62, max_font=13.0),
        _raw_block("p018_b0017", page=18, text="This is the next agenda body and should not be merged backward.", y0=0.82, width=0.70, max_font=12.0, line_count=2),
    ]
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(_items(raw_blocks)))
    grouped = agenda._group_blocks_into_candidates(blocks, min_body_chars=100)

    assert len(grouped) == 2
    assert legal_clause in grouped[0]["selected_text"]
    assert "1 retroactive" in grouped[0]["selected_text"]
    assert "262. Appointments" not in grouped[0]["selected_text"]
    assert grouped[1]["heading_text"].startswith("262. Appointments")


def test_nested_legal_clause_with_footnote_artifact_does_not_split_candidate() -> None:
    intro = "Introductory law explanation with enough continuous body text. " * 3
    nested = _raw_block(
        "p019_b0039",
        page=19,
        text=') נזק המלחמה אירע בתקופה בה חלה בתחומי העירייה ההכרזה על1(הפנייה להערת שוליים 3',
        y0=0.32,
        width=0.50,
        max_font=12.0,
    )
    nested["display_text"] = "3 (1) נזק המלחמה אירע בתקופה בה חלה בתחומי העירייה ההכרזה על הפנייה להערת שוליים"
    raw_blocks = [
        _raw_block("p018_b0001", page=18, text="259. Temporary law amendment", y0=0.20, width=0.76, max_font=13.0),
        _raw_block("p018_b0002", page=18, text=intro, y0=0.26, width=0.76, max_font=12.0, line_count=3),
        nested,
        _raw_block("p019_b0040", page=19, text="The nested clause continues the same legal amendment body.", y0=0.35, width=0.52, max_font=12.0),
        _raw_block("p020_b0001", page=20, text="260. Next agenda item", y0=0.20, width=0.76, max_font=13.0),
        _raw_block("p020_b0002", page=20, text="The next agenda item has its own body text. " * 3, y0=0.26, width=0.76, max_font=12.0, line_count=3),
    ]
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(_items(raw_blocks)))
    grouped = agenda._group_blocks_into_candidates(blocks, min_body_chars=120)

    assert len(grouped) == 2
    assert nested["block_id"] in grouped[0]["block_ids"]
    nested_block = next(block for block in grouped[0]["blocks"] if block["block_id"] == nested["block_id"])
    assert nested_block["role"] == "body"
    assert "הפנייה להערת שוליים" not in grouped[0]["selected_text"]
    assert "3 (1) נזק המלחמה" in grouped[0]["selected_text"]
    assert grouped[1]["heading_text"].startswith("260. Next agenda item")


def test_short_trailing_number_heading_with_heading_font_can_split() -> None:
    raw_blocks = [
        _raw_block("p035_b0010", page=35, text="262. Appointments and changes", y0=0.30, width=0.62, max_font=13.0),
        _raw_block("p035_b0011", page=35, text="Existing item body text with enough discussion and decision material. " * 3, y0=0.34, width=0.75, max_font=12.0, line_count=3),
        _raw_block("p035_b0012", page=35, text="Break in meetings263", y0=0.55, x0=0.69, width=0.17, max_font=13.0),
        _raw_block("p035_b0013", page=35, text="The chair introduces the next item and the body continues.", y0=0.60, width=0.35, max_font=12.0, line_count=2),
    ]
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(_items(raw_blocks)))
    grouped = agenda._group_blocks_into_candidates(blocks, min_body_chars=100)

    assert len(grouped) == 2
    assert grouped[1]["heading_text"].startswith("Break in meetings263")


def test_short_real_candidate_splits_before_next_strong_heading() -> None:
    raw_blocks = [
        _raw_block("p004_b0003", page=4, text="Section 3: Short question item", y0=0.12, width=0.78, max_font=13.0, line_count=3),
        _raw_block("p004_b0004", page=4, text="Short answer text that is visually complete for this item.", y0=0.20, width=0.72, max_font=12.0),
        _raw_block("p004_b0008", page=4, text="Section 4: Next strong proposal item", y0=0.28, width=0.78, max_font=13.0, line_count=3),
        _raw_block("p004_b0009", page=4, text="The next item has enough body text and should not absorb the previous short item. " * 2, y0=0.35, width=0.74, line_count=3),
    ]
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(_items(raw_blocks)))
    grouped = agenda._group_blocks_into_candidates(blocks, min_body_chars=120)

    assert len(grouped) == 2
    assert grouped[0]["heading_text"].startswith("Section 3")
    assert grouped[1]["heading_text"].startswith("Section 4")


def test_repeated_top_page_headers_do_not_split_long_candidate() -> None:
    raw_blocks = [
        _raw_block("p006_b0001", page=6, text="254. Proposal for the agenda", y0=0.19, width=0.55, max_font=13.0),
        _raw_block("p006_b0002", page=6, text="Opening discussion text with enough body evidence. " * 5, y0=0.25, width=0.76, max_font=12.0, line_count=4),
        _raw_block("p007_b0001", page=7, text="Council meeting protocol 34\nRegular meeting protocol\nDate 23.3.2026\n- 7 -", y0=0.08, y1=0.19, width=0.70, max_font=13.0, line_count=4),
        _raw_block("p007_b0002", page=7, text="Continuation of the same item discussion on the next page. " * 5, y0=0.25, width=0.76, max_font=12.0, line_count=4),
        _raw_block("p008_b0001", page=8, text="Council meeting protocol 34\nRegular meeting protocol\nDate 23.3.2026\n- 8 -", y0=0.08, y1=0.19, width=0.70, max_font=13.0, line_count=4),
        _raw_block("p008_b0002", page=8, text="More continuation of the same item, still not a new agenda topic. " * 5, y0=0.25, width=0.76, max_font=12.0, line_count=4),
        _raw_block("p009_b0001", page=9, text="Council meeting protocol 34\nRegular meeting protocol\nDate 23.3.2026\n- 9 -", y0=0.08, y1=0.19, width=0.70, max_font=13.0, line_count=4),
        _raw_block("p009_b0002", page=9, text="Final continuation of the same item before the vote. " * 5, y0=0.25, width=0.76, max_font=12.0, line_count=4),
    ]
    excluded: list[dict] = []
    blocks = agenda._drop_repeated_page_header_blocks(_items(raw_blocks), excluded)
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(blocks))
    grouped = agenda._group_blocks_into_candidates(blocks, min_body_chars=120)

    assert len(grouped) == 1
    assert grouped[0]["heading_text"].startswith("254. Proposal")
    assert "Council meeting protocol" not in grouped[0]["heading_text"]
    assert {item["block_id"] for item in excluded} == {"p007_b0001", "p008_b0001", "p009_b0001"}


def test_early_front_matter_index_is_saved_as_navigation_even_when_first_anchor_is_list_like() -> None:
    raw_blocks = [
        _raw_block("p002_b0010", page=2, text="1. Questions (page 3)", y0=0.25, width=0.18, max_font=13.0),
        _raw_block("p002_b0011", page=2, text="2. Proposals (page 6)", y0=0.30, width=0.22, max_font=13.0),
        _raw_block("p002_b0012", page=2, text="3. Mayor remarks (page 12)", y0=0.35, width=0.28, max_font=13.0),
        _raw_block("p003_b0010", page=3, text="253. Questions", y0=0.30, width=0.35, max_font=13.0),
        _raw_block("p003_b0011", page=3, text="The first real body item starts here with enough text. " * 4, y0=0.36, width=0.76, line_count=3),
    ]
    blocks = agenda._annotate_grouping_context(_items(raw_blocks))
    runs = agenda._dedupe_navigation_runs(
        agenda._detect_navigation_list_runs(blocks, first_body_anchor_index=agenda._first_body_anchor_index(blocks))
        + agenda._detect_dense_early_navigation_list_runs(blocks, first_body_anchor_index=agenda._first_body_anchor_index(blocks))
    )
    navigation_ids = {block["block_id"] for run in runs for block in run}
    body_blocks = agenda._promote_body_start_anchors([block for block in blocks if block["block_id"] not in navigation_ids])
    grouped = agenda._group_blocks_into_candidates(body_blocks, min_body_chars=80)

    assert navigation_ids == {"p002_b0010", "p002_b0011", "p002_b0012"}
    assert len(grouped) == 1
    assert grouped[0]["heading_text"].startswith("253. Questions")


def test_front_matter_index_row_uses_display_text_page_reference() -> None:
    final_index_row = _raw_block(
        "p002_b0039",
        page=2,
        text=")29 .Speeches from the floor (p .10",
        y0=0.49,
        width=0.24,
        max_font=13.0,
    )
    final_index_row["display_text"] = "10. Speeches from the floor (p. 29)"
    raw_blocks = [
        _raw_block("p002_b0030", page=2, text="1. Questions (p. 3)", y0=0.25, width=0.18, max_font=13.0),
        _raw_block("p002_b0031", page=2, text="2. Proposals (p. 4)", y0=0.28, width=0.20, max_font=13.0),
        _raw_block("p002_b0032", page=2, text="3. Mayor remarks (p. 16)", y0=0.31, width=0.24, max_font=13.0),
        final_index_row,
        _raw_block("p003_b0029", page=3, text="Chair opens the regular meeting.", y0=0.19, width=0.45, max_font=12.0),
        _raw_block("p003_b0031", page=3, text="265. Questions", y0=0.28, width=0.25, max_font=13.0),
        _raw_block("p003_b0032", page=3, text="The first body section starts here with enough evidence. " * 3, y0=0.34, width=0.76, line_count=3),
    ]
    blocks = agenda._annotate_grouping_context(_items(raw_blocks))
    scan_limit = agenda._front_matter_navigation_scan_limit_index(blocks, fallback_index=agenda._first_body_anchor_index(blocks))
    runs = agenda._dedupe_navigation_runs(
        agenda._detect_navigation_list_runs(blocks, first_body_anchor_index=scan_limit)
        + agenda._detect_dense_early_navigation_list_runs(blocks, first_body_anchor_index=scan_limit)
    )
    navigation_ids = {block["block_id"] for run in runs for block in run}
    body_blocks = agenda._promote_body_start_anchors([block for block in blocks if block["block_id"] not in navigation_ids])
    grouped = agenda._group_blocks_into_candidates(body_blocks, min_body_chars=80)

    assert "p002_b0039" in navigation_ids
    assert len(grouped) == 1
    assert "Speeches from the floor" not in grouped[0]["selected_text"]
    assert grouped[0]["heading_text"].startswith("265. Questions")


def test_dense_table_after_first_body_anchor_is_not_navigation() -> None:
    raw_blocks = [
        _raw_block("p002_b0010", page=2, text="1. Front matter item (page 4)", y0=0.25, width=0.22, max_font=13.0),
        _raw_block("p002_b0011", page=2, text="2. Front matter item (page 7)", y0=0.30, width=0.22, max_font=13.0),
        _raw_block("p002_b0012", page=2, text="3. Front matter item (page 9)", y0=0.35, width=0.22, max_font=13.0),
        _raw_block("p004_b0010", page=4, text="3. Main body agenda item", y0=0.30, width=0.55, max_font=13.0),
        _raw_block("p004_b0011", page=4, text="The body discussion starts here with enough continuous text. " * 4, y0=0.36, width=0.76, line_count=3),
        _raw_block("p005_b0010", page=5, text="1. Decision row inside the body table", y0=0.25, width=0.28, max_font=12.0, line_count=2),
        _raw_block("p005_b0011", page=5, text="2. Another decision row inside the same body table", y0=0.31, width=0.30, max_font=12.0, line_count=2),
        _raw_block("p005_b0012", page=5, text="3. More table material for the already started item", y0=0.37, width=0.32, max_font=12.0, line_count=2),
    ]
    blocks = agenda._annotate_grouping_context(_items(raw_blocks))
    first_anchor = agenda._first_body_anchor_index(blocks)
    runs = agenda._dedupe_navigation_runs(
        agenda._detect_navigation_list_runs(blocks, first_body_anchor_index=first_anchor)
        + agenda._detect_dense_early_navigation_list_runs(blocks, first_body_anchor_index=first_anchor)
    )
    navigation_ids = {block["block_id"] for run in runs for block in run}

    assert first_anchor == 3
    assert {"p002_b0010", "p002_b0011", "p002_b0012"}.issubset(navigation_ids)
    assert "p005_b0010" not in navigation_ids
    assert "p005_b0011" not in navigation_ids
    assert "p005_b0012" not in navigation_ids


def test_long_front_matter_rows_before_non_list_body_anchor_are_navigation() -> None:
    raw_blocks = [
        _raw_block("p001_b0010", page=1, text="1. Questions", y0=0.60, x0=0.80, width=0.12, max_font=13.0, line_count=2),
        _raw_block("p001_b0011", page=1, text="1.1. Long front matter question description with requester and date", y0=0.66, width=0.76, max_font=12.0, line_count=3),
        _raw_block("p001_b0012", page=1, text="1.2. Another long front matter question description with requester and date", y0=0.72, width=0.76, max_font=12.0, line_count=3),
        _raw_block("p002_b0010", page=2, text="3. Mayor remarks", y0=0.25, width=0.20, max_font=13.0, line_count=2),
        _raw_block("p002_b0011", page=2, text="Continuation of the same listed item without a leading number", y0=0.28, width=0.44, max_font=13.0, line_count=3),
        _raw_block("p002_b0012", page=2, text="4. Long listed approval item with parcel details and attachment reference", y0=0.34, width=0.76, max_font=12.0, line_count=3),
        _raw_block("p002_b0013", page=2, text="5. Another long listed approval item with parcel details and attachment reference", y0=0.40, width=0.76, max_font=12.0, line_count=3),
        _raw_block("p004_b0010", page=4, text="Main body topic with question details 1.2", y0=0.30, width=0.78, max_font=13.0, line_count=3),
        _raw_block("p004_b0011", page=4, text="The real protocol body starts here with enough continuous answer and decision text. " * 3, y0=0.37, width=0.76, line_count=3),
        _raw_block("p004_b0012", page=4, text="More body decision text follows the same real section before the table continues.", y0=0.46, width=0.74, line_count=2),
        _raw_block("p005_b0010", page=5, text="1. Decision row inside the body table", y0=0.25, width=0.28, max_font=12.0, line_count=2),
        _raw_block("p005_b0011", page=5, text="2. Another decision row inside the same body table", y0=0.31, width=0.30, max_font=12.0, line_count=2),
        _raw_block("p005_b0012", page=5, text="3. More table material for the already started item", y0=0.37, width=0.32, max_font=12.0, line_count=2),
    ]
    blocks = agenda._annotate_grouping_context(_items(raw_blocks))
    scan_limit = agenda._front_matter_navigation_scan_limit_index(blocks, fallback_index=agenda._first_body_anchor_index(blocks))
    runs = agenda._dedupe_navigation_runs(
        agenda._detect_navigation_list_runs(blocks, first_body_anchor_index=scan_limit)
        + agenda._detect_dense_early_navigation_list_runs(blocks, first_body_anchor_index=scan_limit)
    )
    navigation_ids = {block["block_id"] for run in runs for block in run}

    assert scan_limit == 7
    assert {"p001_b0010", "p001_b0011", "p001_b0012"}.issubset(navigation_ids)
    assert {"p002_b0010", "p002_b0011", "p002_b0012", "p002_b0013"}.issubset(navigation_ids)
    assert "p004_b0010" not in navigation_ids
    assert "p005_b0010" not in navigation_ids


def test_same_page_body_run_after_dense_navigation_is_not_navigation() -> None:
    raw_blocks = [
        _raw_block("p003_b0010", page=3, text="21. Listed front matter item", y0=0.50, width=0.30, max_font=12.0, line_count=2),
        _raw_block("p003_b0011", page=3, text="22. Another listed front matter item", y0=0.54, width=0.32, max_font=12.0, line_count=2),
        _raw_block("p003_b0012", page=3, text="23. Final listed front matter item", y0=0.58, width=0.31, max_font=12.0, line_count=2),
        _raw_block("p003_b0013", page=3, text="Main body section with question details 1.1", y0=0.63, width=0.78, max_font=13.0, line_count=3),
        _raw_block("p003_b0014", page=3, text="The answer starts here and belongs to the body section, not the navigation list.", y0=0.70, width=0.75, line_count=2),
        _raw_block("p003_b0015", page=3, text="More body answer text follows with enough substance to support the boundary.", y0=0.76, width=0.75, line_count=2),
    ]
    blocks = agenda._annotate_grouping_context(_items(raw_blocks))
    scan_limit = agenda._front_matter_navigation_scan_limit_index(blocks, fallback_index=agenda._first_body_anchor_index(blocks))
    runs = agenda._dedupe_navigation_runs(
        agenda._detect_navigation_list_runs(blocks, first_body_anchor_index=scan_limit)
        + agenda._detect_dense_early_navigation_list_runs(blocks, first_body_anchor_index=scan_limit)
    )
    navigation_ids = {block["block_id"] for run in runs for block in run}

    assert scan_limit == 3
    assert {"p003_b0010", "p003_b0011", "p003_b0012"}.issubset(navigation_ids)
    assert "p003_b0013" not in navigation_ids


def test_date_like_body_continuation_is_not_promoted_to_candidate_start() -> None:
    raw_blocks = [
        _raw_block("p003_b0010", page=3, text="253. Questions", y0=0.30, width=0.35, max_font=13.0),
        _raw_block("p003_b0011", page=3, text="The answer starts here with enough body text. " * 4, y0=0.36, width=0.76, line_count=3),
        _raw_block("p004_b0010", page=4, text="Following the question from stadium23.2.26, the requester repeats unanswered details.", y0=0.73, width=0.72, max_font=13.0, line_count=2),
        _raw_block("p004_b0011", page=4, text="The same question continues with supporting facts and examples. " * 4, y0=0.80, width=0.76, line_count=3),
    ]
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(_items(raw_blocks)))
    grouped = agenda._group_blocks_into_candidates(blocks, min_body_chars=80)

    assert len(grouped) == 1
    assert "23.2.26" in grouped[0]["selected_text"]


def test_visually_strong_question_continuation_is_not_promoted_to_new_candidate() -> None:
    continuation = "Were the facts presented to you? Which actions were taken after receiving the worrying information? until 2."
    raw_blocks = [
        _raw_block("p003_b0045", page=3, text="58. Environmental question", y0=0.30, width=0.45, max_font=13.0),
        _raw_block("p003_b0046", page=3, text="Opening question details with enough continuous evidence. " * 4, y0=0.36, width=0.76, line_count=3),
        _raw_block("p004_b0034", page=4, text=continuation, y0=0.40, width=0.76, max_font=14.7, line_count=2),
        _raw_block("p004_b0036", page=4, text="The response starts here and continues the same question item. " * 3, y0=0.49, width=0.72, line_count=3),
        _raw_block("p004_b0043", page=4, text="266. Next agenda section", y0=0.70, width=0.35, max_font=13.0),
        _raw_block("p004_b0044", page=4, text="The next item has its own body evidence. " * 3, y0=0.76, width=0.72, line_count=3),
    ]
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(_items(raw_blocks)))
    continuation_block = next(block for block in blocks if block["block_id"] == "p004_b0034")
    grouped = agenda._group_blocks_into_candidates(blocks, min_body_chars=100)

    assert continuation_block["role"] == "body"
    assert "promoted_generic_body_start_anchor" not in continuation_block["role_reasons"]
    assert len(grouped) == 2
    assert continuation in grouped[0]["selected_text"]
    assert grouped[1]["heading_text"].startswith("266. Next agenda")


def test_wide_date_like_closure_stays_body_before_next_strong_heading() -> None:
    closure = "27.4.2026 from protocol 11/26 and a related protocol dated 22.3.2026."
    raw_blocks = [
        _raw_block("p020_b0029", page=20, text="269. Committee allocation protocols", y0=0.20, width=0.48, max_font=13.0),
        _raw_block("p020_b0030", page=20, text="Discussion and voting details for the current item. " * 4, y0=0.26, width=0.74, line_count=3),
        _raw_block("p020_b0031", page=20, text=closure, y0=0.36, width=0.66, max_font=12.0),
        _raw_block("p020_b0032", page=20, text="Appendix B and appendix C", y0=0.39, width=0.20, max_font=12.0),
        _raw_block("p020_b0033", page=20, text="270. Finance committee protocol 28/26", y0=0.46, width=0.34, max_font=13.0),
        _raw_block("p020_b0034", page=20, text="The next item starts its own discussion and has enough body evidence. " * 3, y0=0.52, width=0.74, line_count=3),
    ]
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(_items(raw_blocks)))
    closure_block = next(block for block in blocks if block["block_id"] == "p020_b0031")
    grouped = agenda._group_blocks_into_candidates(blocks, min_body_chars=100)

    assert closure_block["role"] == "body"
    assert closure_block["role_reasons"] == ["wide_date_like_body_continuation_not_heading"]
    assert len(grouped) == 2
    assert closure in grouped[0]["selected_text"]
    assert grouped[1]["heading_text"].startswith("270. Finance committee")


def test_narrower_date_like_topic_heading_can_still_start_candidate() -> None:
    role, reasons = agenda._block_role(
        block={"page": 3, "visual_features": {"width_ratio": 0.52, "line_count": 1, "max_font_size": 12.0}},
        selected_text="30.4.2026 council member question number 58",
    )

    assert role == "candidate_start"
    assert reasons == ["numbering_like_heading_shape"]


def test_same_page_low_confidence_child_heading_stays_under_parent_section() -> None:
    child_heading = "30.4.2026 council member question number 58"
    raw_blocks = [
        _raw_block("p003_b0031", page=3, text="265. Questions", y0=0.28, width=0.25, max_font=13.0),
        _raw_block("p003_b0032", page=3, text="The chair introduces the question section and reads the response. " * 3, y0=0.34, width=0.74, line_count=3),
        _raw_block("p003_b0045", page=3, text=child_heading, y0=0.62, width=0.52, max_font=12.0),
        _raw_block("p003_b0046", page=3, text="The written question text continues under the same parent section. " * 3, y0=0.68, width=0.76, line_count=3),
        _raw_block("p004_b0043", page=4, text="266. Next agenda section", y0=0.30, width=0.35, max_font=13.0),
        _raw_block("p004_b0044", page=4, text="The next section has its own body text. " * 3, y0=0.36, width=0.74, line_count=3),
    ]
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(_items(raw_blocks)))
    grouped = agenda._group_blocks_into_candidates(blocks, min_body_chars=100)

    assert len(grouped) == 2
    assert child_heading in grouped[0]["selected_text"]
    assert grouped[0]["heading_text"].startswith("265. Questions")
    assert grouped[1]["heading_text"].startswith("266. Next agenda")


def test_low_strength_numbered_legal_continuation_is_body_not_heading() -> None:
    role, reasons = agenda._block_role(
        block={
            "page": 20,
            "visual_features": {
                "y0_ratio": 0.25,
                "width_ratio": 0.78,
                "line_count": 2,
                "max_font_size": 12.0,
            },
        },
        selected_text="1 food and hospitality businesses may not reopen after prolonged closure, because reputation and recovery costs are high,",
    )

    assert role == "body"
    assert reasons == ["numbered_body_continuation_not_heading"]


def test_compact_narrow_large_numeric_cell_is_body_not_heading() -> None:
    role, reasons = agenda._block_role(
        block={
            "page": 10,
            "visual_features": {
                "y0_ratio": 0.24,
                "width_ratio": 0.08,
                "line_count": 2,
                "max_font_size": 12.0,
            },
        },
        selected_text="472 , חלק",
    )

    assert role == "body"
    assert reasons == ["narrow_numeric_table_cell_not_heading"]


def test_compact_narrow_date_cell_is_body_not_heading() -> None:
    role, reasons = agenda._block_role(
        block={
            "page": 14,
            "visual_features": {
                "y0_ratio": 0.62,
                "width_ratio": 0.12,
                "line_count": 3,
                "max_font_size": 12.0,
            },
        },
        selected_text="25.02.26 and subcommittee protocol",
    )

    assert role == "body"
    assert reasons == ["narrow_numeric_table_cell_not_heading"]


def test_top_multiline_document_header_is_not_agenda_content() -> None:
    block = _raw_block(
        "p007_b0001",
        page=7,
        text="Council meeting protocol 34\nRegular meeting protocol\nDate 23.3.2026\n- 7 -",
        y0=0.08,
        y1=0.19,
        width=0.70,
        max_font=13.0,
        line_count=4,
    )

    assert agenda._is_agenda_content_scope(block) is False


def test_prominent_global_heading_overrides_top_header_scope_skip() -> None:
    block = _raw_block(
        "p007_b0002",
        page=7,
        text="Section 9: Approval of a municipal agreement\nwith continuation title lines",
        y0=0.20,
        y1=0.27,
        width=0.78,
        max_font=13.0,
        line_count=4,
    )
    block["semantic_scope"] = {"in_semantic_scope": False, "category": "header_footer_logo"}

    assert agenda._is_agenda_content_scope(block) is True


def test_top_section_heading_with_dates_overrides_header_scope_skip() -> None:
    block = _raw_block(
        "p014_b0004",
        page=14,
        text="Section19: committee protocol 2.26 from 17.02.26 and subcommittee protocol from 25.02.26",
        y0=0.146,
        y1=0.199,
        width=0.78,
        max_font=13.0,
        line_count=4,
    )
    block["semantic_scope"] = {"in_semantic_scope": False, "category": "header_footer_logo"}

    assert agenda._is_agenda_content_scope(block) is True


def test_top_section_heading_with_dates_is_promoted_to_body_start() -> None:
    raw_blocks = [
        _raw_block(
            "p014_b0004",
            page=14,
            text="Section19: committee protocol 2.26 from 17.02.26 and subcommittee protocol from 25.02.26",
            y0=0.146,
            y1=0.199,
            width=0.78,
            max_font=13.0,
            line_count=4,
        ),
        _raw_block("p014_b0006", page=14, text="15. Committee protocol", y0=0.277, x0=0.78, width=0.10, max_font=12.0, line_count=3),
        _raw_block("p014_b0013", page=14, text="Council members approve the protocol by majority vote", y0=0.279, x0=0.49, width=0.26, max_font=12.0),
    ]
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(_items(raw_blocks)))

    assert blocks[0]["role"] == "candidate_start"
    assert "promoted_generic_body_start_anchor" in blocks[0]["role_reasons"]


def test_extraction_marks_top_multiline_document_header_as_page_chrome() -> None:
    decision = evidence._semantic_scope_decision(
        {
            "block_id": "p007_b0001",
            "page": 7,
            "text": "Council meeting protocol 34\nRegular meeting protocol\nDate 23.3.2026\n- 7 -",
            "visual_features": {
                "y0_ratio": 0.08,
                "y1_ratio": 0.19,
                "height_ratio": 0.11,
                "width_ratio": 0.70,
                "line_count": 4,
                "char_count": 76,
                "max_font_size": 13.0,
            },
        }
    )

    assert decision["in_semantic_scope"] is False
    assert "top_multiline_document_title_or_header" in decision["reasons"]


def test_extraction_marks_bottom_median_small_footer_as_page_chrome() -> None:
    decision = evidence._semantic_scope_decision(
        {
            "block_id": "p015_b0001",
            "page": 15,
            "text": ":סוג דיון ישיבת מועצה\n:מתאריך\n11/03/2026\n:מספר דיון\n45179\n:הודפס בתאריך\n25/03/2026\nעמוד 15 מתוך 18",
            "visual_features": {
                "y0_ratio": 0.8982,
                "y1_ratio": 0.9307,
                "height_ratio": 0.0325,
                "width_ratio": 0.7599,
                "line_count": 8,
                "char_count": 91,
                "median_font_size": 9.96,
                "max_font_size": 12.0,
            },
        }
    )

    assert decision["in_semantic_scope"] is False
    assert "small_font_bottom_page_chrome_area" in decision["reasons"]


def test_extraction_keeps_top_section_heading_with_dates_in_semantic_scope() -> None:
    decision = evidence._semantic_scope_decision(
        {
            "block_id": "p014_b0004",
            "page": 14,
            "text": "Section19: committee protocol 2.26 from 17.02.26 and subcommittee protocol from 25.02.26",
            "visual_features": {
                "y0_ratio": 0.146,
                "y1_ratio": 0.199,
                "height_ratio": 0.053,
                "width_ratio": 0.78,
                "line_count": 4,
                "char_count": 92,
                "max_font_size": 13.0,
            },
        }
    )

    assert decision["in_semantic_scope"] is True
    assert decision["category"] == "semantic_content"


def test_grouping_excludes_bottom_page_chrome_footer_from_candidate_text() -> None:
    footer_text = ":סוג דיון ישיבת מועצה\n:מתאריך\n11/03/2026\n:מספר דיון\n45179\n:הודפס בתאריך\n25/03/2026\nעמוד 15 מתוך 18"
    footer = _raw_block(
        "p015_b0001",
        page=15,
        text=footer_text,
        y0=0.8982,
        y1=0.9307,
        width=0.7599,
        max_font=12.0,
        line_count=8,
    )
    footer["visual_features"]["median_font_size"] = 9.96
    pages = [
        {
            "page": 15,
            "page_processing_status": {"status": "native_text_available"},
            "native_page_diagnostics": {"native_text_absence_reason": "native_text_available"},
            "plain_text": "21. Main item\nBody text\n" + footer_text,
            "page_image_path": "/tmp/page_015.png",
            "page_text_path": "/tmp/page_015.txt",
            "blocks": [
                _raw_block("p015_b0002", page=15, text="21. Main agenda item", y0=0.20, width=0.55, max_font=13.0),
                _raw_block("p015_b0003", page=15, text="The council discussion continues with enough text to form a safe body candidate. " * 3, y0=0.26, width=0.76, line_count=3),
                footer,
            ],
        }
    ]

    selected, skipped_pages, excluded_context = agenda._selected_semantic_blocks(pages)
    blocks = agenda._promote_body_start_anchors(agenda._annotate_grouping_context(selected))
    grouped = agenda._group_blocks_into_candidates(blocks, min_body_chars=80)

    assert skipped_pages == []
    assert "p015_b0001" not in {block["block_id"] for block in selected}
    assert any(item["block_id"] == "p015_b0001" and item["reason"] == "outside_agenda_content_scope" for item in excluded_context)
    assert len(grouped) == 1
    assert "סוג דיון" not in grouped[0]["selected_text"]
    assert "סוג דיון" not in grouped[0]["raw_native_text"]


def test_candidate_block_item_preserves_raw_text_and_cleans_display_text() -> None:
    raw_text = ':מצ"ל – )(בראנץ 28.01.26 מיום\n)(ביטון- מצ"ל\n:סוג דיון ישיבת מועצה\n:מתאריך'
    block = _raw_block("p010_b0001", page=10, text=raw_text, y0=0.30, width=0.76, line_count=3)

    item = agenda._candidate_block_item(block)

    assert item["raw_native_text"] == raw_text
    assert item["rtl_normalized_text"] == raw_text
    assert ')(בראנץ' not in item["selected_text"]
    assert ')(ביטון' not in item["selected_text"]
    assert "(בראנץ) 28.01.26" in item["selected_text"]
    assert "(ביטון)- מצ\"ל" in item["selected_text"]
    assert 'מצ"ל: –' in item["selected_text"]
    assert "סוג דיון: ישיבת מועצה" in item["selected_text"]
    assert "מתאריך:" in item["selected_text"]
    assert item["text_cleanup"]["applied"] is True


def test_display_cleanup_removes_pdfua_footnote_reference_artifacts() -> None:
    cleaned = agenda._display_text_for_downstream(
        "3 (א) נזק מלחמה הפנייה להערת שוליים\n1 היינפהכדי לקדם תוצאה חיובית םיילוש תרעהל\nהערת שוליים1\n1 תשל\"ו - 1976 4. םיילוש"
    )

    assert "הפנייה להערת שוליים" not in cleaned
    assert "הערת שוליים" not in cleaned
    assert "היינפה" not in cleaned
    assert "םיילוש" not in cleaned
    assert "3 (א) נזק מלחמה" in cleaned
    assert "1 כדי לקדם תוצאה חיובית" in cleaned


def test_hebrew_rtl_display_cleanup_repairs_underlines_parentheses_and_quote_dash_spacing() -> None:
    bad_text = (
        "U גב'דיין - היו\"ר U:\n"
        "חברת המועצה שמואל מזרחי מבקש לרשום לפנינו ולפני מר טיומקין - שהחברים שיחפצו בכך\n"
        "יוזמנו לוועדת תנועה וחניה כאשר הנושא ידון בה.\n"
        "החלטה: ההצעה מועברת לועדת תנועה וחנייה.\n"
        "על סדר היום: U U\n"
        "3. מר ראובן לדיאנסקי -\"חינוך לאהבת בעלי חיים ואכיפת חוקים המגנים על זכויותיהם -\n"
        "כבסיס לחברת בני אדם מתוקנת ונאורה\".\n"
        ") מחיאות כפיים ("
    )

    cleaned = agenda._display_text_for_downstream(bad_text)

    assert "U גב" not in cleaned
    assert " U:" not in cleaned
    assert "U U" not in cleaned
    assert "גב' דיין - היו\"ר:" in cleaned
    assert "לדיאנסקי - \"חינוך" in cleaned
    assert "(מחיאות כפיים)" in cleaned
    assert ") מחיאות כפיים (" not in cleaned


def test_hebrew_rtl_display_cleanup_keeps_hebrew_quote_abbreviations_compact() -> None:
    raw_text = 'כ"א באדר תשפ"ד עו"ד כהן ומ"מ יו"ר הוועדה -"נושא חדש"'

    cleaned = agenda._display_text_for_downstream(raw_text)

    assert 'כ"א באדר תשפ"ד עו"ד כהן ומ"מ יו"ר הוועדה - "נושא חדש"' == cleaned
    assert 'כ "א' not in cleaned
    assert 'תשפ "ד' not in cleaned
    assert 'עו "ד' not in cleaned
    assert 'יו "ר' not in cleaned


def test_hebrew_rtl_display_cleanup_does_not_space_between_adjacent_acronyms() -> None:
    raw_text = 'המשנה ליועמ"ש, ע. אברהמי, סמנכ"ל תכנון, עוזר לרה"ע'

    cleaned = agenda._display_text_for_downstream(raw_text)

    assert cleaned == raw_text
    assert 'ליועמ "ש' not in cleaned
    assert 'סמנכ" ל' not in cleaned
    assert 'לרה "ע' not in cleaned


def test_hebrew_rtl_display_cleanup_does_not_treat_separate_acronyms_as_quote_pair() -> None:
    raw_text = 'במגרשים ריקים), התשמ"ז- 1987 לעו"ד גבריאל כנפו, סגן ומ"מ ראש העיר. (עמיר).'

    cleaned = agenda._display_text_for_downstream(raw_text)

    assert 'התשמ"ז-1987 לעו"ד גבריאל כנפו' in cleaned
    assert 'התשמ "ז' not in cleaned
    assert 'לעו" ד' not in cleaned
    assert 'ה - 100' not in agenda._display_text_for_downstream('אירועי ה- 100 לעיר')
    assert '1909 - 2009' == agenda._display_text_for_downstream('1909 - 2009')


def test_hebrew_rtl_display_cleanup_does_not_split_longer_geresh_words_or_names() -> None:
    raw_text = "חבר'ה, בתכל'ס איכות התחזוקה של האופניים. מר מאיר אברז'ל דיבר על אג'נדות וצ'אנסים"

    cleaned = agenda._display_text_for_downstream(raw_text)

    assert cleaned == raw_text
    assert "חבר' ה" not in cleaned
    assert "בתכל' ס" not in cleaned
    assert "אברז' ל" not in cleaned
    assert "אג' נדות" not in cleaned
    assert "צ' אנסים" not in cleaned


def test_large_cleanup_scanner_does_not_flag_normal_geresh_words_or_names() -> None:
    normal = "מר מאיר אברז'ל אמר שזה לא צ'אנסים אלא אג'נדות ובתכל'ס החלטה."
    joined_abbreviation = "U גב'דיין -\"נושא\""

    assert cleanup_review._case_reasons(before=normal, raw_native=normal, after=normal) == []
    assert "quote_dash_or_abbreviation_spacing_shape" in cleanup_review._case_reasons(
        before=joined_abbreviation,
        raw_native=joined_abbreviation,
        after=agenda._display_text_for_downstream(joined_abbreviation),
    )


def test_hebrew_rtl_display_cleanup_repairs_reversed_date_parentheses() -> None:
    raw_text = 'מתאריך י"ד אייר תשפ"א) 26/4/2021 (\nמתאריך ל\' סיוון תשפ"ג)19/6/2023('

    cleaned = agenda._display_text_for_downstream(raw_text)

    assert 'מתאריך י"ד אייר תשפ"א (26/4/2021)' in cleaned
    assert 'מתאריך ל\' סיוון תשפ"ג (19/6/2023)' in cleaned
    assert ') 26/4/2021 (' not in cleaned
    assert ')19/6/2023(' not in cleaned


def test_hebrew_rtl_display_cleanup_repairs_mixed_ltr_parenthetical_spacing() -> None:
    raw_text = "1. השתתפות חבר / י מועצה בישיבה באופן מקוון) VC - אפליקציית zoom (על רקע"

    cleaned = agenda._display_text_for_downstream(raw_text)

    assert cleaned == "1. השתתפות חבר / י מועצה בישיבה באופן מקוון (VC - אפליקציית zoom) על רקע"
    assert "מקוון(" not in cleaned
    assert ")על" not in cleaned
    assert agenda._display_text_for_downstream(cleaned) == cleaned


def test_hebrew_rtl_display_cleanup_repairs_visual_two_digit_rtl_enumerators() -> None:
    raw_text = ".01 מינויים ושינויים במועצות מנהלים\n.21 התקנת קווי ביוב"

    cleaned = agenda._display_text_for_downstream(raw_text)

    assert "10. מינויים ושינויים" in cleaned
    assert "12. התקנת קווי ביוב" in cleaned
    assert ".01" not in cleaned
    assert ".21" not in cleaned


def test_hebrew_rtl_display_cleanup_spaces_after_parenthetical_before_hebrew_word() -> None:
    raw_text = ") הצגת הדברים מלווה בשקפים (ערב טוב."

    cleaned = agenda._display_text_for_downstream(raw_text)

    assert cleaned == "(הצגת הדברים מלווה בשקפים) ערב טוב."
    assert ")ערב" not in cleaned


def test_hebrew_rtl_display_cleanup_spaces_colon_before_quote() -> None:
    cleaned = agenda._display_text_for_downstream('אתה אומר פה:"לאור ההרכב"')

    assert cleaned == 'אתה אומר פה: "לאור ההרכב"'
    assert 'פה:"' not in cleaned


def test_hebrew_rtl_display_cleanup_removes_comma_before_colon_artifact() -> None:
    cleaned = agenda._display_text_for_downstream(
        "מר גפן:\nרגע רגע,: אני רוצה לשאול את היועץ המשפטי."
    )

    assert cleaned == "מר גפן:\nרגע רגע: אני רוצה לשאול את היועץ המשפטי."
    assert ",:" not in cleaned


def test_hebrew_rtl_display_cleanup_repairs_standalone_date_line_leading_period() -> None:
    cleaned = agenda._display_text_for_downstream(
        "שהתקיימה בתאריך\n. 23.2.2009"
    )

    assert cleaned == "שהתקיימה בתאריך\n23.2.2009."
    assert "\n. 23.2.2009" not in cleaned


def test_hebrew_rtl_display_cleanup_repairs_zero_thousands_visual_order() -> None:
    cleaned = agenda._display_text_for_downstream("קריאה:\nגדל ב-000, 30.")

    assert cleaned == "קריאה:\nגדל ב-30,000."
    assert "000, 30" not in cleaned


def test_hebrew_rtl_display_cleanup_repairs_multi_group_zero_thousands_visual_order() -> None:
    cleaned = agenda._display_text_for_downstream(
        "על סך של 000, 220, 1 ₪\nסה\"כ ביניים 000, 488, 2"
    )

    assert cleaned == "על סך של 1,220,000 ₪\nסה\"כ ביניים 2,488,000"
    assert "220,000, 1" not in cleaned
    assert "488,000, 2" not in cleaned


def test_hebrew_rtl_display_cleanup_repairs_contextual_nonzero_grouped_number_visual_order() -> None:
    cleaned = agenda._display_text_for_downstream(
        "הסכום 193, 136 ₪\nכ- 500, 17 - 000, 18 תלמידים\n₪ 010, 2"
    )

    assert cleaned == "הסכום 136,193 ₪\nכ-17,500 - 18,000 תלמידים\n₪ 2,010"
    assert "193, 136" not in cleaned
    assert "500, 17" not in cleaned
    assert "010, 2" not in cleaned


def test_hebrew_rtl_display_cleanup_does_not_reverse_contextual_number_ending_zero_group() -> None:
    cleaned = agenda._display_text_for_downstream(
        "סה\"כ תוספת\n₪ 250, 000 ₪ 300, 000\n. ₪ 550, 000"
    )

    assert cleaned == "סה\"כ תוספת\n₪ 250,000 ₪ 300,000\n. ₪ 550,000"
    assert "000,250" not in cleaned
    assert "000,300" not in cleaned
    assert "000,550" not in cleaned


def test_hebrew_rtl_display_cleanup_does_not_reverse_normal_numbers_or_plain_lists() -> None:
    cleaned = agenda._display_text_for_downstream(
        "סכום רגיל 1,220,000 ₪\nסעיפים 250, 251\nשנים 2005, 2004, 2003"
    )

    assert "1,220,000 ₪" in cleaned
    assert "סעיפים 250, 251" in cleaned
    assert "שנים 2005, 2004, 2003" in cleaned
    assert "251,250" not in cleaned
    assert "2003,2004,2005" not in cleaned


def test_hebrew_rtl_display_cleanup_repairs_two_letter_trailing_geresh_acronym() -> None:
    cleaned = agenda._display_text_for_downstream("15,000 שח'? מס' 5 וגב' דיין")

    assert cleaned == "15,000 ש\"ח? מס' 5 וגב' דיין"
    assert "שח'?" not in cleaned
    assert "מס' 5" in cleaned
    assert "גב' דיין" in cleaned


def test_hebrew_rtl_display_cleanup_keeps_department_abbreviation_with_geresh() -> None:
    cleaned = agenda._display_text_for_downstream("גב' אדוה אלמוזינינו מנהלת מח' משכורות ו-15,000 שח'?")

    assert "מנהלת מח' משכורות" in cleaned
    assert "מנהלת מ\"ח" not in cleaned
    assert "15,000 ש\"ח?" in cleaned


def test_hebrew_rtl_display_cleanup_is_idempotent_for_multiple_parentheticals() -> None:
    reversed_text = "עמיעד 11) שוק הפשפשים (, למטרת הפעלת קיוסק / בית אוכל) עמ' 34 ("
    normal_text = "עמיעד 11(שוק הפשפשים), למטרת הפעלת קיוסק / בית אוכל(עמ' 34)"

    cleaned = agenda._display_text_for_downstream(reversed_text)

    assert cleaned == normal_text
    assert agenda._display_text_for_downstream(cleaned) == normal_text
    assert agenda._display_text_for_downstream(normal_text) == normal_text


def test_hebrew_rtl_display_cleanup_does_not_damage_nested_legal_parentheticals() -> None:
    partially_repaired = "שידרשו. תוצאות המשא ומתן ואישור ההתקשרות בחוזה) ללא מכרז, כאמור בתקנה 22(ח)"
    adjacent_parentheticals = (
        "מתאריך 13/4/2021.\n"
        "(נספח ח')\n"
        "349. חוק עזר לתל אביב - יפו(פטור מתשלום אגרות)(תיקון), התשפ\"א - 2021:"
    )

    assert agenda._display_text_for_downstream(partially_repaired) == partially_repaired
    assert agenda._display_text_for_downstream(adjacent_parentheticals) == adjacent_parentheticals


def test_hebrew_rtl_display_cleanup_repairs_empty_paren_before_hebrew_when_geometry_is_missing() -> None:
    cleaned = agenda._display_text_for_downstream("()מחיאות כפיים\n)מחיאות כפיים של הקהל(")

    assert "(מחיאות כפיים)" in cleaned
    assert "(מחיאות כפיים של הקהל)" in cleaned
    assert "()מחיאות" not in cleaned


def test_candidate_block_item_keeps_raw_native_text_while_cleaning_display_selected_text() -> None:
    raw_text = "U היו\"ר-גב' דייןU:\n)מחיאות כפיים("
    display_text = "U גב'דיין - היו\"ר U:\n3. מר ראובן לדיאנסקי -\"חינוך\".\n) מחיאות כפיים ("
    block = _raw_block("p031_b0021", page=31, text=raw_text, y0=0.66, width=0.66, line_count=3)
    block["display_text"] = display_text
    block["display_text_diagnostics"] = {"source": "rawdict_character_geometry_visual_order"}

    item = agenda._candidate_block_item(block)

    assert item["raw_native_text"] == raw_text
    assert item["display_selected_text_before_cleanup"] == display_text
    assert "U גב" not in item["display_selected_text"]
    assert "גב' דיין - היו\"ר:" in item["display_selected_text"]
    assert "לדיאנסקי - \"חינוך\"." in item["display_selected_text"]
    assert "(מחיאות כפיים)" in item["display_selected_text"]
    assert item["display_text_cleanup"]["applied"] is True


def test_visual_geometry_text_repairs_rtl_small_punctuation_near_numbers() -> None:
    chars = []
    # Raw/native order from the PDF can place the left visual tail first, while
    # character bboxes still show the correct visual order across the line.
    chars += _visual_chars(",", x0=140.0)
    chars += _visual_chars(" ", x0=136.0)
    chars += _visual_chars("פרוטוקול", x0=77.0, rtl=True)
    chars += _visual_chars("17.12.25", x0=144.0)
    chars += _visual_chars(" ", x0=202.0)
    chars += _visual_chars("מיום", x0=206.0, rtl=True)
    chars += _visual_chars(" ", x0=235.0)
    chars += _visual_chars("8.25", x0=239.0)
    chars += _visual_chars(" ", x0=267.0)
    chars += _visual_chars("'", x0=271.0)
    chars += _visual_chars(".", x0=449.0)
    chars += _visual_chars(" ", x0=445.0)
    chars += _visual_chars("פרוטוקול", x0=386.0, rtl=True)
    chars += _visual_chars(" ", x0=382.0)
    chars += _visual_chars("ועדת", x0=349.0, rtl=True)
    chars += _visual_chars(" ", x0=345.0)
    chars += _visual_chars("ביקורת", x0=299.0, rtl=True)
    chars += _visual_chars(" ", x0=295.0)
    chars += _visual_chars("מס", x0=275.0, rtl=True)
    chars += _visual_chars("18", x0=453.0)

    visual = evidence._visual_line_text(chars)

    assert visual == "18. פרוטוקול ועדת ביקורת מס' 8.25 מיום 17.12.25, פרוטוקול"


def test_visual_geometry_text_merges_split_ltr_numeric_island_in_rtl_line() -> None:
    chars = [
        {"char": "0", "bbox": [247.73, 107.6, 253.046, 119.6]},
        {"char": "1", "bbox": [253.01, 107.6, 258.326, 119.6]},
        {"char": "/", "bbox": [258.29, 107.6, 262.61, 119.6]},
        {"char": "2", "bbox": [262.61, 107.6, 267.926, 119.6]},
        {"char": "0", "bbox": [267.89, 107.6, 273.206, 119.6]},
        {"char": "2", "bbox": [273.17, 107.6, 278.486, 119.6]},
        {"char": " ", "bbox": [283.85, 107.6, 286.502, 119.6]},
        {"char": "'", "bbox": [286.502, 107.6, 289.826, 119.6]},
        {"char": "פ", "bbox": [342.026, 107.6, 348.002, 119.6]},
        {"char": "ר", "bbox": [336.386, 107.6, 342.026, 119.6]},
        {"char": "ו", "bbox": [333.026, 107.6, 336.35, 119.6]},
        {"char": "ט", "bbox": [325.718, 107.6, 333.026, 119.6]},
        {"char": "ו", "bbox": [322.37, 107.6, 325.694, 119.6]},
        {"char": "ק", "bbox": [315.398, 107.6, 322.37, 119.6]},
        {"char": "ו", "bbox": [312.074, 107.6, 315.398, 119.6]},
        {"char": "ל", "bbox": [306.758, 107.6, 312.074, 119.6]},
        {"char": " ", "bbox": [304.106, 107.6, 306.758, 119.6]},
        {"char": "מ", "bbox": [297.134, 107.6, 304.106, 119.6]},
        {"char": "ס", "bbox": [289.826, 107.6, 297.134, 119.6]},
        {"char": "3", "bbox": [278.45, 107.6, 283.766, 119.6]},
    ]

    visual = evidence._visual_line_text(chars)

    assert visual == "פרוטוקול מס' 01/2023"
    assert "3 01/202" not in visual


def test_visual_geometry_text_keeps_separate_rtl_list_marker_and_page_reference_numbers() -> None:
    chars = []
    # This mirrors a visually confirmed row: "1. שאילתות; (עמ' 7)".
    # The native char stream places the page number 7 immediately before the
    # list marker 1, but their bboxes are far apart and must not be merged.
    chars += _visual_chars("(", x0=378.0)
    chars += _visual_chars("'", x0=392.0)
    chars += _visual_chars("שאילתות", x0=420.0, rtl=True)
    chars += _visual_chars(";", x0=414.0)
    chars += _visual_chars(")", x0=408.0)
    chars += _visual_chars("עמ", x0=396.0, rtl=True)
    chars += _visual_chars("7", x0=384.0)
    chars += _visual_chars("1", x0=476.0)
    chars += _visual_chars(".", x0=472.0)

    visual = evidence._visual_line_text(chars)
    cleaned = agenda._display_text_for_downstream(visual)

    assert visual == "1. שאילתות;) עמ' 7 ("
    assert cleaned == "1. שאילתות; (עמ' 7)"
    assert "71" not in cleaned


def test_visual_geometry_text_uses_bboxes_not_source_order_for_hebrew_words() -> None:
    right = _visual_chars("נכחו", x0=470.0, rtl=True)
    right += _visual_chars("בישיבה:", x0=400.0, rtl=True)
    asher = _visual_chars("אשר", x0=320.0, rtl=True)
    ben_shoshan = _visual_chars("בן-שושן", x0=230.0, rtl=True)
    role = _visual_chars('סמנכ"ל', x0=125.0, rtl=True)
    role += _visual_chars("משאבי", x0=55.0, rtl=True)
    role += _visual_chars("אנוש", x0=0.0, rtl=True)

    # The source character stream is intentionally scrambled like difficult
    # native PDFs; bbox geometry still shows the readable visual order.
    scrambled_name = ben_shoshan[:2] + asher[:1] + ben_shoshan[2:] + asher[1:]

    visual = evidence._visual_line_text(right + scrambled_name + role)

    assert visual == 'נכחו בישיבה: אשר בן-שושן סמנכ"ל משאבי אנוש'
    assert "בןא" not in visual
    assert "שושןשר" not in visual


def test_visual_geometry_text_ignores_false_spaces_inside_hyphenated_hebrew_and_acronyms() -> None:
    chars = []
    chars += _visual_chars("אשר", x0=360.0, rtl=True)
    chars += _visual_chars("בן-", x0=310.0, rtl=True)
    chars += _visual_chars(" ", x0=302.0)
    chars += _visual_chars("שושן", x0=260.0, rtl=True)
    chars += _visual_chars("ע'", x0=190.0, rtl=True)
    chars += _visual_chars(" ", x0=182.0)
    chars += _visual_chars("ר", x0=170.0, rtl=True)
    chars += _visual_chars(" ", x0=162.0)
    chars += _visual_chars('ה"ע', x0=135.0, rtl=True)

    visual = evidence._visual_line_text(chars)

    assert visual == 'אשר בן-שושן ע\' רה"ע'
    assert "בן- שושן" not in visual
    assert 'ר ה"ע' not in visual


def test_visual_geometry_text_preserves_geresh_spacing_and_compact_acronyms() -> None:
    chars = []
    chars += _visual_chars("גב'", x0=420.0, rtl=True)
    chars += _visual_chars("סטלה", x0=360.0, rtl=True)
    chars += _visual_chars("דר'", x0=290.0, rtl=True)
    chars += _visual_chars("יחיאל", x0=220.0, rtl=True)
    chars += _visual_chars("כ\"א", x0=150.0, rtl=True)
    chars += _visual_chars("תשפ\"ד", x0=70.0, rtl=True)

    visual = evidence._visual_line_text(chars)

    assert visual == 'גב\' סטלה דר\' יחיאל כ"א תשפ"ד'
    assert "גב'סטלה" not in visual
    assert "דר'יחיאל" not in visual
    assert 'כ "א' not in visual
    assert 'תשפ "ד' not in visual


def test_visual_geometry_normalizes_tiny_encoded_digit_marker_as_geresh() -> None:
    chars = []
    chars += _visual_chars("6/26", x0=70.0, width=5.0)
    chars += _visual_chars(" ", x0=92.0, width=2.0)
    chars.append({"char": "1", "bbox": [98.0, 12.0, 100.4, 17.0]})
    chars += _visual_chars("החלטה", x0=130.0, width=6.0, rtl=True)
    chars += _visual_chars("מס", x0=105.0, width=6.0, rtl=True)
    chars += _visual_chars(":", x0=66.0, width=2.0)

    visual = evidence._visual_line_text(chars)

    assert visual == "החלטה מס׳ 6/26:"
    assert "6/261" not in visual


def test_stricter_large_cleanup_scanner_flags_dirty_and_changed_text() -> None:
    dirty = "עמוד1\nמתוך5\n'פרוטוקול מס3\n01/202\n:שם"
    reasons = cleanup_review._case_reasons(before="פרוטוקול מס' 3 01/202", raw_native=dirty, after="פרוטוקול מס' 3 01/202")

    assert "raw_native_dirty_glued_hebrew_digit_shape" in reasons
    assert "raw_native_dirty_leading_geresh_before_hebrew_shape" in reasons
    assert "raw_native_dirty_leading_colon_label_shape" in reasons
    assert "display_before_dirty_truncated_date_or_protocol_number_shape" in reasons
    assert cleanup_review._case_reasons(before="גדל ב-000, 30", raw_native="גדל ב-000, 30", after="גדל ב-30,000") == [
        "display_cleanup_changed_text_needs_visual_check"
    ]


def test_display_cleanup_repairs_leading_rtl_colon_idempotently() -> None:
    simple_label = cleanup_display_text(":: ח ברים")
    clean_speaker = cleanup_display_text(": אתי ברייטברט לפני שנפתח את הישיבה")
    noisy_speaker = cleanup_display_text(": א, ל תי ברייטברט לפני שנפתח את הישיבה")
    punctuation_prefix = cleanup_display_text("., מ כולם לעטות מסכות\n, ב מהלך הדיון")

    assert simple_label == "ח ברים:"
    assert cleanup_display_text(simple_label) == simple_label
    assert clean_speaker == "אתי ברייטברט: לפני שנפתח את הישיבה"
    assert cleanup_display_text(clean_speaker) == clean_speaker
    assert noisy_speaker == "א, ל תי ברייטברט לפני שנפתח את הישיבה"
    assert cleanup_display_text(noisy_speaker) == noisy_speaker
    assert punctuation_prefix == "מכולם לעטות מסכות\nבמהלך הדיון"
    assert cleanup_display_text(punctuation_prefix) == punctuation_prefix


def test_display_cleanup_keeps_table_range_lists_idempotent() -> None:
    text = (
        "גוש 6146 גוש 6146\n"
        "329 -,, 65,227,229,233,235,228,230-232, 234, 236\n"
        ",, 330\n"
        "גוש 7050 גוש 7048\n"
        "4, 16-18, 22, 24 22,115-117, 121 -\n"
        "2, 4-8, 13, 41, 72,108 - 1, 68-71,146-151, 154-155"
    )

    cleaned = cleanup_display_text(text)

    assert cleaned == text
    assert cleanup_display_text(cleaned) == cleaned
    assert "230-236,234,232" not in cleaned
    assert "115-121,117" not in cleaned
    assert "146-154,151-155" not in cleaned


def test_stricter_large_cleanup_scanner_ignores_valid_hebrew_maqaf_and_slash_numbers() -> None:
    assert "display_after_dirty_glued_hebrew_digit_shape" not in cleanup_review._case_reasons(
        before="לכיתות ב־ 4 בתי ספר",
        raw_native="לכיתות ב־4בתי ספר",
        after="לכיתות ב־4 בתי ספר",
    )
    assert "display_after_dirty_glued_hebrew_digit_shape" not in cleanup_review._case_reasons(
        before="כ־ 80 שוטרי מג״ב",
        raw_native="כ־80שוטרי מג״ב",
        after="כ־80 שוטרי מג״ב",
    )
    assert "display_after_dirty_truncated_date_or_protocol_number_shape" not in cleanup_review._case_reasons(
        before="סניף 13/152 לגני הילדים",
        raw_native="סניף13/152 לגני הילדים",
        after="סניף 13/152 לגני הילדים",
    )
    assert "display_after_dirty_truncated_date_or_protocol_number_shape" not in cleanup_review._case_reasons(
        before="בתכנית הר / 2/198.",
        raw_native="בתכנית הר / 2/198.",
        after="בתכנית הר / 2/198.",
    )
    assert "display_after_dirty_truncated_date_or_protocol_number_shape" in cleanup_review._case_reasons(
        before="פרוטוקול מס' 3 01/202",
        raw_native="פרוטוקול מס3 01/202",
        after="פרוטוקול מס' 3 01/202",
    )
    assert "display_after_dirty_truncated_date_or_protocol_number_shape" in cleanup_review._case_reasons(
        before="מתאריך כ\"ט אב תשע\"ג (18/180/83 /)",
        raw_native="מתאריך כ\"ט אב תשע\"ג (18/180/83 /)",
        after="מתאריך כ\"ט אב תשע\"ג (18/180/83 /)",
    )


def test_large_cleanup_scanner_classifies_text_layer_issues_as_ocr_scope() -> None:
    text_layer_reasons = cleanup_review._case_reasons(
        before="מתאריך כ\"ט אב תשע\"ג (18/180/83 /)",
        raw_native="מתאריך כ\"ט אב תשע\"ג (18/180/83 /)",
        after="מתאריך כ\"ט אב תשע\"ג (18/180/83 /)",
    )
    table_reasons = cleanup_review._case_reasons(
        before="גוש 6146\n329 -,, 65, 227, 229, 233, 235, 228, 230-232, 234, 236",
        raw_native="גוש 6146\n329-,,65,227,229,233,235,228,230-232,234,236",
        after="גוש 6146\n329 -,, 65,227,229,233,235,228,230-232, 234, 236",
    )

    assert cleanup_review._case_review_scope(reasons=text_layer_reasons, skip_text_layer_issues=True) == "skipped_for_ocr_pipeline"
    assert cleanup_review._case_review_scope(reasons=table_reasons, skip_text_layer_issues=True) == "non_ocr_cleanup_review"
    assert cleanup_review._non_ocr_problem_reasons(table_reasons) == []


def test_candidate_output_prefers_geometry_display_text_without_changing_grouping_text() -> None:
    block = _raw_block(
        "p015_b0025",
        page=15,
        text="8.25 '. פרוטוקול ועדת ביקורת מס18",
        y0=0.30,
        width=0.76,
        line_count=1,
    )
    block["display_text"] = "18. פרוטוקול ועדת ביקורת מס' 8.25"
    block["display_text_diagnostics"] = {"source": "rawdict_character_geometry_visual_order"}

    item = agenda._candidate_block_item(block)
    candidate = agenda._candidate_from_blocks(index=1, blocks=[item], min_body_chars=80)

    assert item["selected_text"] == "8.25 '. פרוטוקול ועדת ביקורת מס18"
    assert item["display_selected_text"] == "18. פרוטוקול ועדת ביקורת מס' 8.25"
    assert item["raw_native_text"] == "8.25 '. פרוטוקול ועדת ביקורת מס18"
    assert item["selected_text_source"] == "deterministic_display_text_from_rtl_normalized_text"
    assert item["display_selected_text_source"] == "deterministic_display_text_from_geometry_display_text"
    assert candidate["selected_text"] == "18. פרוטוקול ועדת ביקורת מס' 8.25"
    assert candidate["grouping_selected_text"] == "8.25 '. פרוטוקול ועדת ביקורת מס18"


def test_page_with_only_out_of_scope_native_artifact_is_ignored_without_ocr() -> None:
    artifact = _raw_block(
        "p040_b0001",
        page=40,
        text="Photo of protocol ending and signatures",
        y0=-1.0,
        y1=0.30,
        width=0.80,
        line_count=3,
    )
    artifact["semantic_scope"] = {"in_semantic_scope": False, "category": "header_footer_logo", "status": "skipped"}
    pages = [
        {
            "page": 40,
            "page_processing_status": {"status": "native_text_available"},
            "native_page_diagnostics": {"native_text_absence_reason": "native_text_available"},
            "plain_text": "Photo of protocol ending and signatures",
            "page_image_path": "/tmp/page_040.png",
            "page_text_path": "/tmp/page_040.txt",
            "blocks": [artifact],
        }
    ]

    selected, skipped_pages, excluded_context = agenda._selected_semantic_blocks(pages)

    assert selected == []
    assert len(skipped_pages) == 1
    assert skipped_pages[0]["status"] == "ignored_image_or_nonsemantic_page"
    assert skipped_pages[0]["skip_reason"] == "native_blocks_outside_semantic_scope"
    assert skipped_pages[0]["no_ocr_attempted"] is True
    assert skipped_pages[0]["downstream_blocking"] is False
    assert skipped_pages[0]["raw_native_text_preview"] == "Photo of protocol ending and signatures"
    assert excluded_context[0]["reason"] == "outside_agenda_content_scope"


def test_visual_review_treats_ignored_page_as_nonblocking() -> None:
    page = {
        "page": 40,
        "status": "ignored_image_or_nonsemantic_page",
        "status_reason": "No usable native semantic text.",
        "raw_native_text_preview": "Photo of protocol ending and signatures",
        "no_ocr_attempted": True,
        "downstream_blocking": False,
        "page_image_path": "/tmp/page_040.png",
    }

    reviewed = visual_review._skipped_page_review(page, manual_payload={})

    assert reviewed["final_status"] == "ignored_image_or_nonsemantic_page"
    assert reviewed["no_ocr_attempted"] is True
    assert reviewed["downstream_blocking"] is False
    assert visual_review._skipped_page_is_ignored(reviewed) is True


def test_visual_review_html_marks_missing_judgement_as_not_accepted() -> None:
    candidate = {
        "candidate_id": "candidate_0001",
        "status": "plausible_candidate",
        "status_reason": "test candidate",
        "heading_text": "1. Test topic",
        "pages": [1],
        "page_range": [1, 1],
        "selected_text": "1. Test topic\nDiscussion body.",
        "raw_native_text": "1. Test topic\nDiscussion body.",
        "group_crops": [{"page": 1, "group_crop_image_path": "/tmp/candidate_0001_page_001.png"}],
    }

    review = visual_review._candidate_review(candidate=candidate, manual_payload={}, original_pdf={}, report={})
    html = visual_review._candidate_html(review=review, output_dir=Path("/tmp"))

    assert "Unknown." not in html
    assert "No recorded visual judgement is available" not in html
    assert "No manual visual judgement is available" not in html
    assert 'class="candidate bad"' in html
    assert "Needs review. Not accepted for downstream use." in html
    assert "Final judgement: needs_review, not accepted." in html
