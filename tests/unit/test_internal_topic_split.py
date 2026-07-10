from __future__ import annotations

import json
from pathlib import Path

from municipality.docsegmentation_native import internal_topic_split as splitter


def _block(
    block_id: str,
    *,
    page: int,
    text: str,
    role: str = "body",
    y0: float = 100.0,
    y1: float | None = None,
    max_font: float = 12.0,
    width_ratio: float = 0.60,
    line_count: int = 1,
) -> dict:
    y1 = y1 if y1 is not None else y0 + 18.0
    return {
        "block_id": block_id,
        "page": page,
        "display_text": text,
        "selected_text": text,
        "raw_native_text": text,
        "role": role,
        "role_reasons": ["unit_test"],
        "visual_features": {
            "line_count": line_count,
            "width_ratio": width_ratio,
            "max_font_size": max_font,
            "median_font_size": max_font,
        },
        "bbox_pdf_points": [80.0, y0, 520.0, y1],
        "bbox_crop_image_path": f"/tmp/{block_id}.png",
        "page_image_path": f"/tmp/page_{page:03d}.png",
    }


def _body_text(label: str) -> str:
    return f"{label} includes enough discussion text to support a child topic boundary without relying on a municipality-specific keyword. " * 2


def test_visual_marker_split_uses_child_topics_not_parent_heading(tmp_path: Path) -> None:
    candidate = {
        "candidate_id": "candidate_0002",
        "heading_text": "266. הצעות לסדר היום:",
        "page_range": [4, 16],
        "pages": [4, 5, 6, 7],
        "selected_text": "broad parent",
        "blocks": [
            _block("p004_b0001", page=4, text="266. הצעות לסדר היום:", role="candidate_start", max_font=13.0),
            _block("p004_b0002", page=4, text="1. איתמר אבנרי, חבר מועצה - בניינים מסוכנים בעיר", role="label_or_speaker", max_font=13.0),
            _block("p004_b0003", page=4, text=f"מר אבנרי: {_body_text('Dangerous buildings')}", y0=130.0),
            _block("p006_b0001", page=6, text="2. דודו לניאדו, חבר מועצה - מי שומר על ילדי הגן הפרטיים", role="label_or_speaker", y0=100.0, max_font=13.0),
            _block("p006_b0002", page=6, text=f"מר לניאדו: {_body_text('Private kindergarten supervision')}", y0=130.0),
            _block("p009_b0001", page=9, text="3. שחר לוי, חבר מועצה - הפסקת שתילת עצי פיקוס חדשים", role="body", y0=100.0, max_font=12.0),
            _block("p009_b0002", page=9, text=f"מר לוי: {_body_text('Ficus tree planting')}", y0=130.0),
            _block("p012_b0001", page=12, text="4. גל שרעבי, משנה לרה\"ע - הערכות העירייה לפעילות קיץ", role="label_or_speaker", y0=100.0, max_font=13.0),
            _block("p012_b0002", page=12, text=f"מר שרעבי: {_body_text('Summer activity preparation')}", y0=130.0),
        ],
    }

    result = splitter._split_parent_candidate(
        candidate=candidate,
        input_pdf_path=tmp_path / "missing.pdf",
        child_crops_dir=tmp_path / "child_crops",
        raw_semantic_dir=tmp_path / "semantic",
        min_child_body_chars=80,
        render_dpi=72,
        crop_margin_points=0.0,
        run_dicta=False,
        dicta_model="dictaLM",
        ollama_base_url="http://localhost:11434",
        timeout_seconds=1.0,
        semantic_client=None,
    )

    assert result["status"] == "needs_visual_semantic_review"
    assert result["parent_downstream_policy"] == "container_only_when_child_topics_exist"
    assert result["parent_context_text"] == "266. הצעות לסדר היום:"
    assert [child["anchor_text"].split(".", 1)[0] for child in result["child_topics"]] == ["1", "2", "3", "4"]
    assert all(child["split_source"] == "visual_marker" for child in result["child_topics"])
    assert "266. הצעות" not in result["child_topics"][0]["selected_text"]


def test_upstream_list_body_merge_parent_is_not_split(tmp_path: Path) -> None:
    candidate = {
        "candidate_id": "candidate_0015",
        "status": "needs_review_possible_list_body_merge",
        "status_reason": "Candidate starts with multiple dense list/index-like rows and later contains body-like text.",
        "grouping_diagnostics": {
            "contamination": {
                "status": "needs_review_possible_list_body_merge",
                "reason": "Candidate starts with multiple dense list/index-like rows and later contains body-like text.",
            }
        },
        "heading_text": "1. דיין רחל אין\n2. חולדאי מדואל\n3. אגמי יואב",
        "page_range": [30, 32],
        "pages": [30, 31, 32],
        "selected_text": "vote list accidentally merged with body discussion",
        "blocks": [
            _block("p030_b0010", page=30, text="1. דיין רחל אין", role="candidate_start"),
            _block("p030_b0011", page=30, text="2. חולדאי מדואל", role="candidate_start", y0=125.0),
            _block("p030_b0012", page=30, text="3. אגמי יואב", role="candidate_start", y0=150.0),
            _block("p031_b0001", page=31, text="בעד - 22 חברי מועצה, נגד - 5 חברי מועצה", role="body", y0=175.0),
            _block("p032_b0001", page=32, text="3. מר ראובן לדיאנסקי - חינוך לאהבת בעלי חיים", role="label_or_speaker", y0=100.0),
            _block("p032_b0002", page=32, text=_body_text("Animal welfare proposal body"), role="body", y0=130.0),
        ],
    }

    result = splitter._split_parent_candidate(
        candidate=candidate,
        input_pdf_path=tmp_path / "missing.pdf",
        child_crops_dir=tmp_path / "child_crops",
        raw_semantic_dir=tmp_path / "semantic",
        min_child_body_chars=80,
        render_dpi=72,
        crop_margin_points=0.0,
        run_dicta=False,
        dicta_model="dictaLM",
        ollama_base_url="http://localhost:11434",
        timeout_seconds=1.0,
        semantic_client=None,
    )

    assert result["status"] == "parent_preserved_no_internal_split"
    assert result["parent_downstream_policy"] == "can_remain_single_topic_if_reviewed"
    assert result["boundary_candidates"] == []
    assert len(result["child_topics"]) == 1
    assert "flagged upstream" in result["status_reason"]


def test_dense_numbered_roster_without_discussion_is_not_split(tmp_path: Path) -> None:
    candidate = {
        "candidate_id": "candidate_0025",
        "status": "plausible_candidate",
        "grouping_diagnostics": {"contamination": {"status": "ok"}},
        "heading_text": "(149 ז)\n1. אופירה יוחנן וולק - יו\"ר",
        "page_range": [84, 84],
        "pages": [84],
        "selected_text": "committee roster",
        "blocks": [
            _block("p084_b0001", page=84, text="(149 ז)\n1. אופירה יוחנן וולק - יו\"ר", role="candidate_start", max_font=12.0),
            _block("p084_b0002", page=84, text="2. סיגל ויצמן אהרוני - סגן יו\"ר", role="candidate_start", y0=125.0),
            _block("p084_b0003", page=84, text="3. ציפי ברנד פרנק - מ\"מ סגן יו\"ר", role="candidate_start", y0=150.0),
            _block("p084_b0004", page=84, text="4. מוריה שלומות", role="candidate_start", y0=175.0),
            _block("p084_b0005", page=84, text="5. ראובן לדיאנסקי", role="candidate_start", y0=200.0),
            _block("p084_b0006", page=84, text="6. מנהל מינהל החינוך", role="body", y0=225.0),
            _block("p084_b0007", page=84, text="7. מנהל מינהל לשירותים חברתיים", role="body", y0=250.0),
            _block("p084_b0008", page=84, text="8. מנהל בית ספר", role="body", y0=275.0),
            _block("p084_b0009", page=84, text="9. נציג ארגון המורים העל יסודיים", role="body", y0=300.0),
        ],
    }

    result = splitter._split_parent_candidate(
        candidate=candidate,
        input_pdf_path=tmp_path / "missing.pdf",
        child_crops_dir=tmp_path / "child_crops",
        raw_semantic_dir=tmp_path / "semantic",
        min_child_body_chars=80,
        render_dpi=72,
        crop_margin_points=0.0,
        run_dicta=False,
        dicta_model="dictaLM",
        ollama_base_url="http://localhost:11434",
        timeout_seconds=1.0,
        semantic_client=None,
    )

    assert result["status"] == "parent_preserved_no_internal_split"
    assert result["boundary_candidates"] == []
    assert len(result["child_topics"]) == 1


def test_vote_roster_does_not_borrow_later_topic_body(tmp_path: Path) -> None:
    candidate = {
        "candidate_id": "candidate_0099",
        "status": "plausible_candidate",
        "grouping_diagnostics": {"contamination": {"status": "ok"}},
        "heading_text": "1. דיין רחל אין\n2. חולדאי מדואל\n3. אגמי יואב",
        "page_range": [30, 32],
        "pages": [30, 31, 32],
        "selected_text": "vote roster followed by a real later topic",
        "blocks": [
            _block("p030_b0010", page=30, text="1. דיין רחל אין", role="candidate_start", max_font=12.0),
            _block("p030_b0011", page=30, text="2. חולדאי מדואל", role="candidate_start", y0=125.0),
            _block("p030_b0012", page=30, text="3. אגמי יואב", role="candidate_start", y0=150.0),
            _block("p030_b0013", page=30, text="4. שמוליק יעל", role="candidate_start", y0=175.0),
            _block("p031_b0001", page=31, text="בעד - 22 חברי מועצה, נגד - 5 חברי מועצה, נמנעים - אין", role="body", y0=100.0),
            _block("p031_b0002", page=31, text="גב' דיין - היו\"ר: ההצעה מועברת לוועדת תנועה וחניה.", role="body", y0=130.0),
            _block("p031_b0003", page=31, text="3. מר ראובן לדיאנסקי - חינוך לאהבת בעלי חיים", role="label_or_speaker", y0=160.0),
            _block("p032_b0001", page=32, text=_body_text("Animal welfare proposal body"), role="body", y0=100.0),
        ],
    }

    result = splitter._split_parent_candidate(
        candidate=candidate,
        input_pdf_path=tmp_path / "missing.pdf",
        child_crops_dir=tmp_path / "child_crops",
        raw_semantic_dir=tmp_path / "semantic",
        min_child_body_chars=80,
        render_dpi=72,
        crop_margin_points=0.0,
        run_dicta=False,
        dicta_model="dictaLM",
        ollama_base_url="http://localhost:11434",
        timeout_seconds=1.0,
        semantic_client=None,
    )

    assert result["status"] == "parent_preserved_no_internal_split"
    assert result["boundary_candidates"] == []
    assert len(result["child_topics"]) == 1


def test_child_topic_selected_text_is_display_cleaned_without_changing_raw_native_text() -> None:
    bad_text = (
        "U גב'דיין - היו\"ר U:\n"
        "חברת המועצה שמואל מזרחי מבקש לרשום לפנינו ולפני מר טיומקין - שהחברים שיחפצו בכך\n"
        "יוזמנו לוועדת תנועה וחניה כאשר הנושא ידון בה.\n"
        "החלטה: ההצעה מועברת לועדת תנועה וחנייה.\n"
        "3. מר ראובן לדיאנסקי -\"חינוך לאהבת בעלי חיים ואכיפת חוקים המגנים על זכויותיהם -\n"
        "כבסיס לחברת בני אדם מתוקנת ונאורה\".\n"
        ") מחיאות כפיים ("
    )
    block = _block("p031_b0021", page=31, text=bad_text, role="body", line_count=7)

    span = splitter._span_from_block(block=block, span_index=24)
    child = splitter._child_topic(
        parent_candidate_id="candidate_0015",
        child_index=1,
        spans=[span],
        total_children=1,
        boundaries=[],
    )

    assert child["raw_native_text"] == bad_text
    assert child["selected_text_before_display_cleanup"] == bad_text
    assert "U גב" not in child["selected_text"]
    assert "גב' דיין - היו\"ר:" in child["selected_text"]
    assert "לדיאנסקי - \"חינוך" in child["selected_text"]
    assert "(מחיאות כפיים)" in child["selected_text"]
    assert child["text_cleanup"]["source_text_preserved_in_raw_native_text"] is True


def test_semantic_only_split_can_start_after_parent_context(tmp_path: Path) -> None:
    raw_semantic_dir = tmp_path / "semantic"
    raw_semantic_dir.mkdir()
    candidate = {
        "candidate_id": "candidate_0100",
        "heading_text": "General discussion:",
        "page_range": [1, 2],
        "pages": [1, 2],
        "selected_text": "broad semantic parent",
        "blocks": [
            _block("p001_b0001", page=1, text="General discussion:", role="candidate_start", max_font=13.0),
            _block("p001_b0002", page=1, text="The members discuss a parks maintenance request in the north side of town."),
            _block("p001_b0003", page=1, text="The same parks request continues with operational details and timing."),
            _block("p002_b0001", page=2, text="The discussion then changes to school transportation safety for a different neighborhood."),
            _block("p002_b0002", page=2, text="The school transportation matter continues with requested next actions."),
        ],
    }

    def semantic_client(**_: object) -> dict:
        return {
            "message": {
                "content": json.dumps(
                    {
                        "topic_groups": [
                            {
                                "topic_title": "parks maintenance",
                                "start_span_id": "span_0002",
                                "end_span_id": "span_0003",
                                "reason": "The first two body spans discuss parks maintenance.",
                                "evidence_quotes": ["parks maintenance request"],
                                "confidence": 0.84,
                            },
                            {
                                "topic_title": "school transportation",
                                "start_span_id": "span_0004",
                                "end_span_id": "span_0005",
                                "reason": "The later body spans discuss school transportation safety.",
                                "evidence_quotes": ["school transportation safety"],
                                "confidence": 0.82,
                            },
                        ]
                    }
                )
            }
        }

    result = splitter._split_parent_candidate(
        candidate=candidate,
        input_pdf_path=tmp_path / "missing.pdf",
        child_crops_dir=tmp_path / "child_crops",
        raw_semantic_dir=raw_semantic_dir,
        min_child_body_chars=80,
        render_dpi=72,
        crop_margin_points=0.0,
        run_dicta=False,
        dicta_model="dictaLM",
        ollama_base_url="http://localhost:11434",
        timeout_seconds=1.0,
        semantic_client=semantic_client,
    )

    assert result["status"] == "needs_visual_semantic_review"
    assert result["parent_context_text"] == "General discussion:"
    assert [child["split_source"] for child in result["child_topics"]] == ["semantic_only", "semantic_only"]
    assert [child["anchor_block_id"] for child in result["child_topics"]] == ["p001_b0002", "p002_b0001"]
    assert "parks maintenance" in result["child_topics"][0]["selected_text"]
    assert "school transportation" in result["child_topics"][1]["selected_text"]
    assert not result["child_topics"][0]["selected_text"].startswith("General discussion")


def test_requested_parent_ids_restrict_selected_parents() -> None:
    candidates = [
        {"candidate_id": "candidate_0001", "heading_text": "1. broad", "pages": [1, 2, 3, 4], "selected_text": "x" * 4000},
        {"candidate_id": "candidate_0002", "heading_text": "2. target", "pages": [5, 6, 7, 8], "selected_text": "y" * 4000},
    ]
    manual = {
        "candidate_judgements": {
            "candidate_0001": {"final_status": "good_research_candidate", "belongs_together": True},
            "candidate_0002": {"final_status": "good_research_candidate", "belongs_together": True},
        }
    }

    selected, excluded = splitter._select_parent_candidates(
        candidates=candidates,
        manual_payload=manual,
        parent_candidate_ids=["candidate_0002"],
        max_parents=0,
        broad_page_threshold=3,
        broad_char_threshold=3500,
    )

    assert [candidate["candidate_id"] for candidate in selected] == ["candidate_0002"]
    assert excluded[0]["candidate_id"] == "candidate_0001"
    assert excluded[0]["exclude_reason"] == "not_in_requested_parent_candidate_ids"
