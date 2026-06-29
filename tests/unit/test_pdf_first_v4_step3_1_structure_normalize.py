from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_step31_module():
    path = Path(__file__).resolve().parents[2] / "rag_eval/runs/ashdod_2025_regular_3_short_pdf/pdf_first_pipeline/scripts/step3_1_structure_normalize.py"
    spec = importlib.util.spec_from_file_location("step3_1_structure_normalize", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


step31 = _load_step31_module()


def test_visual_attribution_dash_row_extracts_bounded_headline() -> None:
    candidate = step31._visual_headline_candidate(
        {
            "visual_role": "body_text",
            "region_id": "r1",
            "block_ids": ["b1"],
            "text": "חברת מועצה פלונית – טיפול במפגעי רעש באזור מגורים .2",
            "confidence": 0.82,
        }
    )

    assert candidate is not None
    assert candidate["header_text"] == "טיפול במפגעי רעש באזור מגורים"


def test_visual_speaker_label_dash_is_not_headline() -> None:
    candidate = step31._visual_headline_candidate(
        {
            "visual_role": "structured_header",
            "region_id": "r1",
            "block_ids": ["b1"],
            "text": ":מר פלוני – היו\"ר .אני מעביר את רשות הדיבור לחבר המועצה הבא",
            "confidence": 0.86,
        }
    )

    assert candidate is None


def test_visual_region_with_multiple_explicit_markers_splits_candidates() -> None:
    candidates = step31._visual_headline_candidates(
        {
            "visual_role": "structured_row",
            "region_id": "r1",
            "block_ids": ["b1"],
            "text": "שאילתה של חברה בנושא טיפול במפגעי רעש. שאילתה של חבר בנושא שיפוץ חוף עירוני.",
            "confidence": 0.82,
        }
    )

    assert [candidate["header_text"] for candidate in candidates] == [
        "שאילתה של חברה בנושא טיפול במפגעי רעש",
        "שאילתה של חבר בנושא שיפוץ חוף עירוני",
    ]


def test_visual_repeated_page_chrome_is_not_headline() -> None:
    candidate = step31._visual_headline_candidate(
        {
            "visual_role": "section_heading",
            "region_id": "r1",
            "block_ids": ["b1"],
            "text": "פרוטוקול ישיבות המועצה 35 ישיבה מן המניין מתאריך 11.5.2026",
            "confidence": 0.9,
        }
    )

    assert candidate is None


def test_normalize_structure_adds_visual_headline_units_from_generic_report(tmp_path: Path) -> None:
    visual_report = {
        "results": [
            {
                "page": 4,
                "visual_regions": [
                    {
                        "region_id": "p4_r7",
                        "visual_role": "structured_row",
                        "block_ids": ["p4_b7"],
                        "text": "חבר מועצה אלמוני – שיפור בטיחות הולכי רגל ליד בתי ספר .1",
                        "confidence": 0.84,
                    }
                ],
            }
        ]
    }
    visual_path = tmp_path / "visual_layout_report.json"
    visual_path.write_text(json.dumps(visual_report, ensure_ascii=False), encoding="utf-8")

    structure_units = step31._normalize_structure(
        [
            {
                "semantic_unit_id": "u1",
                "source_window_id": "w1",
                "page": 4,
                "source_region_ids": ["p4_r8"],
                "source_block_ids": ["p4_b8"],
                "raw_text": "נאום ארוך של חברי מועצה שאינו כותרת עצמאית",
                "explicit_actions": [],
            }
        ],
        semantic_payload={"input_visual_layout_report": str(visual_path)},
    )

    visual_units = [unit for unit in structure_units if unit["structure_evidence"]["split_reason"] == "visual_bounded_headline_candidate"]
    assert len(visual_units) == 1
    assert visual_units[0]["header_text"] == "שיפור בטיחות הולכי רגל ליד בתי ספר"
    assert visual_units[0]["source_region_ids"] == ["p4_r7"]
    assert visual_units[0]["structural_role"] == "outline_item"


def test_structural_starts_does_not_split_conjoined_numeric_reference() -> None:
    text = (
        "בתחילת דברי ההסבר לתקציב מוזכרת המלחמה, אירועי המלחמה - "
        "זה חוזר כמה פעמים, סעיף 2 ו-3. , אין שום סעיף בתקציב הזה"
    )

    assert step31._structural_starts(text) == [0]
    assert step31._structural_starts("סעיף 741/370 - להלן סעיף 23.") == [0]


def test_structural_starts_does_not_split_date_tail_attachment_reference() -> None:
    text = "שאילתה של עו\"ד גלבר בנושא הנחת מבנה יביל ברחוב האדמור מבעלז – ( 25.10.22 ) – מצ\"ל"

    assert step31._structural_starts(text) == [0]


def test_structural_starts_keeps_section_label_with_numbered_title() -> None:
    text = "סעיף 24 : 23. הסעות לחינוך המיוחד ולמסגרות הרווחה - קבלת החלטת מועצה שלא בישיבת מועצה"

    assert step31._structural_starts(text) == [0]


def test_visual_continuity_keeps_numbered_approval_clause_with_decision(tmp_path: Path) -> None:
    visual_report = {
        "results": [
            {
                "page": 13,
                "block_roles": [
                    {
                        "block_id": "p13_b10",
                        "text": ".להצביע .לסיים את ההצבעה בבקשה בעד17 אין נגד .אין נמנעים",
                        "final_visual_role": "structured_row",
                        "region_id": "p13_r6",
                        "bbox": [200.0, 560.0, 540.0, 590.0],
                        "visual_features": {"median_font_size": 12.0},
                    },
                    {
                        "block_id": "p13_b14",
                        "text": "החלטה: שינוי מטרת חכירה מתחם \"סיפולוקס\" יגאל אלון – מ א ו ש ר",
                        "final_visual_role": "structured_row",
                        "region_id": "p13_r8",
                        "bbox": [220.0, 603.0, 540.0, 633.0],
                        "visual_features": {"median_font_size": 12.0},
                    },
                    {
                        "block_id": "p13_b15",
                        "text": "בגוש107 וחלקה111 קול), לאשר החכרת המגרש המאוחד המהווה את חלק מחלקה17( המועצה החליטה פה אחד",
                        "final_visual_role": "structured_row",
                        "region_id": "p13_r8",
                        "bbox": [39.0, 639.0, 540.0, 668.0],
                        "visual_features": {"median_font_size": 12.0},
                    },
                    {
                        "block_id": "p13_b17",
                        "text": "יום מיום45 בתוספת מע\"מ ובתוספת ריבית והצמדה החל מתום₪ 324,033,580 תמורת דמי חכירה כוללים של",
                        "final_visual_role": "structured_row",
                        "region_id": "p13_r8",
                        "bbox": [56.0, 710.0, 540.0, 722.0],
                        "visual_features": {"median_font_size": 12.0},
                    },
                    {
                        "block_id": "p13_b18",
                        "text": ". בהצעת ההחלטה4 -1 אישור מועצת העירייה וכן לחתום על חוזה להקמת מבנה הציבור, וכמפורט בסעיפים",
                        "final_visual_role": "structured_row",
                        "region_id": "p13_r8",
                        "bbox": [66.0, 727.0, 540.0, 740.0],
                        "visual_features": {"median_font_size": 12.0},
                    },
                ],
            }
        ]
    }
    visual_path = tmp_path / "visual_layout_report.json"
    visual_path.write_text(json.dumps(visual_report, ensure_ascii=False), encoding="utf-8")
    raw_text = (
        ".להצביע .לסיים את ההצבעה בבקשה בעד17 אין נגד .אין נמנעים "
        "החלטה: שינוי מטרת חכירה מתחם \"סיפולוקס\" יגאל אלון – מ א ו ש ר "
        "בגוש107 וחלקה111 קול), לאשר החכרת המגרש המאוחד המהווה את חלק מחלקה17( המועצה החליטה פה אחד "
        "יום מיום45 בתוספת מע\"מ ובתוספת ריבית והצמדה החל מתום₪ 324,033,580 תמורת דמי חכירה כוללים של "
        ". בהצעת ההחלטה4 -1 אישור מועצת העירייה וכן לחתום על חוזה להקמת מבנה הציבור, וכמפורט בסעיפים"
    )

    structure_units = step31._normalize_structure(
        [
            {
                "semantic_unit_id": "p13_w15_u1",
                "source_window_id": "p13_w15",
                "page": 13,
                "source_region_ids": ["p13_r6", "p13_r8"],
                "source_block_ids": ["p13_b10", "p13_b14", "p13_b15", "p13_b17", "p13_b18"],
                "raw_text": raw_text,
                "explicit_actions": [],
            }
        ],
        semantic_payload={"input_visual_layout_report": str(visual_path)},
    )

    assert len(structure_units) == 2
    decision_unit = structure_units[1]
    assert decision_unit["raw_text"].startswith("החלטה: שינוי מטרת חכירה")
    assert decision_unit["structural_role"] == "outline_item"
    assert decision_unit["topic_assignment_eligible"] is True
    assert "1 אישור מועצת העירייה" in decision_unit["raw_text"]
    assert all(not unit["raw_text"].startswith("1 אישור מועצת העירייה") for unit in structure_units)


def test_explicit_decision_subject_starts_new_subject_but_generic_disposition_does_not() -> None:
    assert step31._starts_new_subject("החלטה: שינוי מטרת חכירה מתחם סיפולוקס יגאל אלון – מאושר") is True
    assert step31._starts_new_subject("החלטה: ההצעה לסדר עוברת לדיון בוועדת הביקורת") is False
    assert step31._starts_new_subject("החלטה: מאושר") is False


def test_explicit_decision_with_protocol_date_is_not_page_metadata() -> None:
    text = "החלטה: פרוטוקול ועדת נכסים מס .27.10.2025 מתאריך12/25 ' קול), לאשר את פרוטוקול ועדת נכסים מס"

    assert step31._looks_like_page_chrome(text) is True
    assert step31._structural_role(text, original_unit={}, fragment_count=2) == "outline_item"


def test_committee_protocol_heading_is_topic_eligible_not_page_metadata() -> None:
    text = "פרוטוקול ועדת נכסים מס211 :מר שפירא – היו\"ר . 12/25 'פרוטוקול ועדת נכסים מס .לפתוח להצבעה"

    assert step31._starts_new_subject(text) is True
    assert step31._structural_role(text, original_unit={}, fragment_count=2) == "outline_item"


def test_committee_protocol_heading_after_decision_starts_new_fragment() -> None:
    structure_units = step31._normalize_structure(
        [
            {
                "semantic_unit_id": "u1",
                "source_window_id": "w1",
                "page": 14,
                "source_region_ids": ["p14_r1"],
                "source_block_ids": ["p14_b1"],
                "raw_text": (
                    "החלטה: פרוטוקול ועדת נכסים מס .27.10.2025 מתאריך12/25 ' "
                    "המועצה החליטה לאשר את פרוטוקול ועדת נכסים מס )(נספח ה "
                    ":22/25 '. פרוטוקול ועדת כספים מס212 :מר שפירא – היו\"ר "
                    ":אנחנו עוברים לסעיף הבא"
                ),
                "explicit_actions": [],
            }
        ],
        semantic_payload={},
    )

    assert len(structure_units) == 2
    assert structure_units[0]["raw_text"].startswith("החלטה: פרוטוקול ועדת נכסים")
    assert structure_units[0]["raw_text"].count("פרוטוקול ועדת") == 2
    assert structure_units[1]["raw_text"].startswith("פרוטוקול ועדת כספים")


def test_trailing_number_agenda_heading_starts_new_fragment_after_generic_decision() -> None:
    structure_units = step31._normalize_structure(
        [
            {
                "semantic_unit_id": "u1",
                "source_window_id": "w1",
                "page": 6,
                "source_region_ids": ["p6_r1"],
                "source_block_ids": ["p6_b1"],
                "raw_text": (
                    "החלטה: ההצעה לסדר עוברת לדיון בוועדת הביקורת "
                    ":. דברי ראש העירייה206 :מר שפירא – היו\"ר "
                    ".אדוני ראש העיר, דברי ראש העיר"
                ),
                "explicit_actions": [],
            }
        ],
        semantic_payload={},
    )

    assert len(structure_units) == 2
    assert structure_units[0]["raw_text"].startswith("החלטה: ההצעה לסדר עוברת לדיון בוועדת הביקורת")
    assert "דברי ראש העירייה" not in structure_units[0]["raw_text"]
    assert structure_units[1]["raw_text"].startswith(". דברי ראש העירייה206")


def test_true_numbered_agenda_items_still_split() -> None:
    structure_units = step31._normalize_structure(
        [
            {
                "semantic_unit_id": "u1",
                "source_window_id": "w1",
                "page": 4,
                "source_region_ids": ["p4_r1"],
                "source_block_ids": ["p4_b1"],
                "raw_text": "1 אישור נסיעה מקצועית לחו\"ל. 2 אישור פרוטוקול ועדת כספים.",
                "explicit_actions": [],
            }
        ],
        semantic_payload={},
    )

    assert [unit["raw_text"] for unit in structure_units] == [
        "1 אישור נסיעה מקצועית לחו\"ל.",
        "2 אישור פרוטוקול ועדת כספים.",
    ]


def test_first_short_protocol_header_does_not_self_link_as_continuation() -> None:
    structure_units = step31._normalize_structure(
        [
            {
                "semantic_unit_id": "p1_w1_u1",
                "source_window_id": "p1_w1",
                "page": 1,
                "source_region_ids": ["p1_r1", "p1_r2"],
                "source_block_ids": ["p1_b1", "p1_b2"],
                "raw_text": "עיריית באר שבע פרוטוקול אישור מועצה מן המניין מס' 50 מיום ראשון, ח' סיון תשפ\"ו 24.5.26",
                "explicit_actions": [],
            }
        ],
        semantic_payload={},
    )

    assert len(structure_units) == 1
    assert structure_units[0]["structural_role"] == "metadata"
    assert structure_units[0]["continuation_of_unit_id"] is None
    assert structure_units[0]["topic_assignment_eligible"] is False
    assert step31._validate_structure(structure_units)["accept_for_next_step"] is True


def test_page_chrome_with_substantive_body_is_not_metadata_or_vote_only() -> None:
    text = (
        "פרוטוקול ישיבות המועצה העשרים ושתיים 29 'פרוטוקול ישיבה מן המניין מס "
        ") 10.11.2025( מתאריך י\"ט בחשון תשפ\"ו - 6- "
        "צעדים כאלה וצעדים אחרים יוכלו להציל חיים של עובדים ושל עוברי אורח. "
        "אני חושב שטוב ונכון שגם אנחנו, כמועצת העיר, נקבל על כך את ההחלטה ביחד. "
        "יש מישהו שנמנע או מתנגד? לא. אז אנחנו פה אחד מעבירים את זה לוועדת ביקורת."
    )

    assert step31._looks_like_page_chrome(text) is True
    assert step31._is_metadata(text, original_unit={}) is False
    assert step31._is_vote_or_result(text) is False
    assert step31._structural_role(text, original_unit={}, fragment_count=1) == "body"


def test_page_chrome_with_vote_only_remainder_stays_metadata() -> None:
    text = (
        "פרוטוקול ישיבות המועצה העשרים ושתיים 29 'פרוטוקול ישיבה מן המניין מס "
        ") 10.11.2025( מתאריך י\"ט בחשון תשפ\"ו - 15- "
        "חברי מועצה19-בעד חברי מועצה3- נגד חבר מועצה1 -נמנעים מ א ו ש ר"
    )

    assert step31._is_metadata(text, original_unit={}) is True
