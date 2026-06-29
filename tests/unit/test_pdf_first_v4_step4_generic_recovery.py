from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_step4_module():
    path = Path(__file__).resolve().parents[2] / "rag_eval/runs/ashdod_2025_regular_3_short_pdf/pdf_first_pipeline/scripts/step4_v4_global_topic_assign.py"
    spec = importlib.util.spec_from_file_location("step4_v4_global_topic_assign", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


step4 = _load_step4_module()


def test_candidate_child_prompt_includes_deterministic_metadata_schema() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    candidate = step4._existing_child_candidate_by_label(
        topic_tree=tree,
        root_topic_id="root_supports",
        label="תמיכה בספורט",
        evidence_quote="מענקי ספורט לקבוצת כדורסל",
        confidence_hint=0.94,
    )

    prompt_choice = step4._candidate_child_choices_for_prompt([candidate])[0]

    assert prompt_choice["label_he"] == "תמיכה בספורט"
    assert prompt_choice["metadata_schema"]["labeling_method"] == "deterministic_curated_topic_tree"
    assert "raw_text" in prompt_choice["metadata_schema"]["field_names"]
    assert "beneficiary_he" in prompt_choice["metadata_schema"]["field_names"]


def test_pure_agenda_section_heading_is_container() -> None:
    assert step4._looks_like_container_heading("סעיף1 :\u202b :הנושאים לדיון\u202b שאילתות") is True
    assert step4._looks_like_container_heading("הנושאים לדיון :שאילתות") is True


def test_numeric_prefixed_deferred_reply_is_non_topic() -> None:
    assert step4._looks_like_no_topic_continuation("22 ) – .תועבר אליך בהמשך") is True


def test_noisy_query_carrier_extracts_semantic_subject() -> None:
    contract = step4._topic_contract_from_headline(
        'שאילתא של ד"ר לחמני בנו "שא "העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל',
        structural_role="continuation",
        packet_role="protocol",
    )

    assert contract["is_topic_bearing"] is True
    assert "העמדת לוח מודעות" in contract["topic_subject_he"]


def test_orphaned_quoted_title_extracts_local_semantic_subject() -> None:
    item = {
        "unit_raw_text": "22 ) - .מצ\"ל 11 \".\"פרס חינוך עירוני שנתי לחינוך פורץ דרך באשדוד- דיון עפ\"י בקשת עו\"ד טובול- ( 29.5.",
        "raw_text": "22 ) - .מצ\"ל 11 \".\"פרס חינוך עירוני שנתי לחינוך פורץ דרך באשדוד- דיון עפ\"י בקשת עו\"ד טובול- ( 29.5.",
        "topic_headline_he": "זרימת מי ביוב בחוף יא",
        "topic_identification_context": "זרימת מי ביוב בחוף יא",
    }

    subject = step4._best_explicit_topic_subject(item)
    proposal = step4._topic_proposal_from_text(
        row={"root_topic_id": "root_infrastructure_environment"},
        item={**item, "root_topic_candidates": [], "document_context": {"packet_role": "protocol"}},
        text=subject or "",
        evidence_text=item["unit_raw_text"],
        source="weak_carrier_span",
        base_score=86,
    )

    assert subject == "פרס חינוך עירוני שנתי לחינוך פורץ דרך באשדוד"
    assert proposal is not None
    assert proposal["root_topic_id"] == "root_education"
    assert proposal["subject_he"] == "פרס חינוך"


def test_orphaned_quoted_title_ignores_ocr_apostrophe_word_split() -> None:
    item = {
        "unit_raw_text": "22.פרוטוקול מישיבת ועדת בטיחות וגהות עירו 'נית מס1/22 מיום27.10.22 – )(דבדה– .מצ\"ל",
        "raw_text": "22.פרוטוקול מישיבת ועדת בטיחות וגהות עירו 'נית מס1/22 מיום27.10.22 – )(דבדה– .מצ\"ל",
        "topic_headline_he": "פרוטוקול מישיבת ועדת בטיחות וגהות עירו נית מס 22 מיום.10.22",
        "topic_identification_context": "פרוטוקול מישיבת ועדת בטיחות וגהות עירו נית מס 22 מיום.10.22",
    }

    assert step4._best_explicit_topic_subject(item) is None
    assert step4._local_committee_protocol_subject(item) == "ועדת בטיחות וגהות עירו נית מס 22 מיום"


def test_pure_mayor_update_heading_is_internal_non_topic() -> None:
    row = {
        "packet_role": "protocol",
        "row_type": "topic_item",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "root_topic_id": "root_mayor_updates",
        "topic_subject_he": "עדכוני ראש העיר",
        "topic_headline_he": "עדכוני ראש העיר",
        "topic_identification_context": "עדכוני ראש העיר",
    }

    assert step4._looks_like_container_heading("14 ..עדכוני ראש העיר") is True
    assert step4._post_assignment_non_topic_reason(row) == "internal_update_heading"


def test_noisy_pure_mayor_update_heading_is_internal_non_topic() -> None:
    row = {
        "packet_role": "protocol",
        "row_type": "container",
        "structural_role": "outline_item",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "root_topic_id": "root_hr_labor",
        "topic_subject_he": "עדכוני ראש העיר סעיף : עדכוני ראש העיר",
        "topic_headline_he": "סעיף2 : ..עדכוני ראש העיר14",
        "topic_identification_context": "סעיף2 : ..עדכוני ראש העיר14",
    }

    assert step4._looks_like_container_heading("סעיף2 : ..עדכוני ראש העיר14") is True
    assert step4._post_assignment_non_topic_reason(row) == "internal_update_heading"


def test_pure_mayor_remarks_heading_is_internal_non_topic() -> None:
    row = {
        "packet_role": "protocol",
        "row_type": "topic_item",
        "structural_role": "outline_item",
        "topic_node_status": "candidate",
        "is_topic_bearing": True,
        "root_topic_id": "root_hr_labor",
        "topic_subject_he": "דברי ראש העיר סעיף : דברי ראש העיר",
        "topic_headline_he": "דברי ראש העיר",
        "topic_identification_context": "דברי ראש העיר",
    }

    assert step4._looks_like_container_heading("דברי ראש העיר") is True
    assert step4._post_assignment_non_topic_reason(row) == "container_heading"
    normalized = step4._normalize_protocol_non_topic_assignments([row])[0]
    assert normalized["is_topic_bearing"] is False
    assert normalized["root_topic_id"] == "root_agenda_queries"


def test_substantive_fragment_gets_model_review() -> None:
    item = {
        "row_type": "fragment",
        "document_context": {"packet_role": "protocol"},
        "topic_identification_context": "סעיף20 : הצעת הגזברות לעדכון בצו הארנונה לשנת2024 הוספת תת סיווג חדש",
        "topic_headline_he": "",
        "raw_text": "סעיף20 : הצעת הגזברות לעדכון בצו הארנונה לשנת2024 הוספת תת סיווג חדש",
    }

    assert step4._fragment_should_get_model_review(item=item, row_type="fragment", packet_role="protocol") is True


def test_numeric_evidence_fragment_is_not_active_topic() -> None:
    row = {
        "topic_subject_he": "עזרה משטרתית",
        "topic_identification_context": "100 .ומבקשים את עזרת המשטרה, שזה מספר משמעותי מאוד",
    }

    assert step4._looks_like_numeric_or_partial_evidence_fragment(
        row=row,
        subject="עזרה משטרתית",
        text="עזרה משטרתית 100 .ומבקשים את עזרת המשטרה",
    ) is True


def test_numbered_continuation_with_bounded_topic_signal_is_not_numeric_fragment() -> None:
    row = {
        "topic_subject_he": "שיפוץ מרכז מסחרי רובע ו׳ ועדכון תבחינים",
        "topic_identification_context": "29 .שיפוץ מרכז מסחרי רובע ו' ,עדכון תבחינים שנתקבלו במועצה ממאי2018 - רוטנברג",
        "structural_role": "continuation",
    }

    assert step4._looks_like_numeric_or_partial_evidence_fragment(
        row=row,
        subject="שיפוץ מרכז מסחרי רובע ו׳ ועדכון תבחינים",
        text="שיפוץ מרכז מסחרי רובע ו׳ ועדכון תבחינים",
    ) is False


def test_numbered_query_continuation_with_supported_subject_is_not_numeric_fragment() -> None:
    row = {
        "topic_subject_he": "כיכר רמון בעיר ודרך מנחם בגין",
        "topic_supporting_quote_he": "22 )- .יועבר בהמשך 5 \".שאילתה של ד\"ר לחמני בנושא \"כיכר רמון בעיר ודרך מנחם בגין",
        "structural_role": "continuation",
    }

    assert step4._looks_like_numeric_or_partial_evidence_fragment(
        row=row,
        subject="כיכר רמון בעיר ודרך מנחם בגין",
        text="כיכר רמון בעיר ודרך מנחם בגין",
    ) is False


def _structured_fallback_item(text: str, *, topic_subject: str | None = None, hints: list[dict] | None = None) -> dict:
    return {
        "structure_unit_id": "s0001_01_aaaaaaaaaaaa",
        "semantic_unit_id": "s0001_01_aaaaaaaaaaaa",
        "source_window_id": "w1",
        "source_region_ids": [],
        "source_block_ids": [],
        "source_page": 1,
        "structural_role": "body",
        "row_type": "topic_item",
        "skip_model_assignment": False,
        "section_id": "section-a",
        "section_number": None,
        "topic_identification_context": text,
        "topic_headline_he": text,
        "topic_subject_he": topic_subject,
        "agenda_carrier_he": None,
        "attribution_he": None,
        "is_topic_bearing": bool(topic_subject),
        "agenda_item_title_he": None,
        "parent_agenda_unit_id": None,
        "topic_context_source": None,
        "topic_headline_source": "test",
        "topic_provenance_reject_reason": None,
        "topic_anchor_quote_he": None,
        "protocol_subject_he": None,
        "topic_carrier_mode": "headline_topics",
        "unit_raw_text": text,
        "raw_text": text,
        "document_context": {"packet_role": "protocol", "municipality_he": "אשדוד"},
        "topic_subject_v3_hints": hints or [],
        "deterministic_topic_decision": {},
        "root_topic_candidates": [],
    }


def test_topic_unit_grouping_merges_action_anchor_and_continuation_detail() -> None:
    units = [
        {
            "structure_unit_id": "u_anchor",
            "semantic_unit_id": "u_anchor",
            "structural_role": "continuation",
            "section_id": "s1",
            "continuation_of_unit_id": "split_parent",
            "raw_text": "5. * אישור הסכמי רשות ופיתוח להקצאת גג מבנה גן ילדים קיים במקרקעין הידועים כגוש2458 חלקי חלקה45",
        },
        {
            "structure_unit_id": "u_detail",
            "semantic_unit_id": "u_detail",
            "structural_role": "continuation",
            "section_id": "s1",
            "continuation_of_unit_id": "split_parent",
            "raw_text": "13 ., רובע ו' עבור עמותת בית החסידים נרקיס אשדוד ע.ר לצורך הקמה והפעלת בית כנסת ומקווה",
        },
    ]

    grouped = step4._apply_topic_unit_grouping(units=units, packet_role="protocol")

    assert grouped[0]["topic_group_role"] == "anchor"
    assert grouped[0]["merged_detail_unit_ids"] == ["u_detail"]
    assert "בית כנסת ומקווה" in grouped[0]["raw_text"]
    assert grouped[1]["topic_group_role"] == "merged_detail"
    assert grouped[1]["merged_into_unit_id"] == "u_anchor"


def test_topic_unit_grouping_prefers_nearest_action_anchor_after_agenda_heading() -> None:
    units = [
        {
            "structure_unit_id": "u_agenda_heading",
            "semantic_unit_id": "u_agenda_heading",
            "structural_role": "continuation",
            "section_id": "s1",
            "continuation_of_unit_id": "split_parent",
            "raw_text": "26 )) (שם דובר :הנושאים לדיון",
        },
        {
            "structure_unit_id": "u_non_topic_heading",
            "semantic_unit_id": "u_non_topic_heading",
            "structural_role": "continuation",
            "section_id": "s1",
            "continuation_of_unit_id": "split_parent",
            "raw_text": "3. דברי ראש העיר",
        },
        {
            "structure_unit_id": "u_first_action",
            "semantic_unit_id": "u_first_action",
            "structural_role": "continuation",
            "section_id": "s1",
            "continuation_of_unit_id": "split_parent",
            "raw_text": "4. * אישור הסכמי רשות ופיתוח להקצאת גג מבנה קיים במקרקעין הידועים כגוש2815 חלקי חלקה24",
        },
        {
            "structure_unit_id": "u_first_detail",
            "semantic_unit_id": "u_first_detail",
            "structural_role": "continuation",
            "section_id": "s1",
            "continuation_of_unit_id": "split_parent",
            "raw_text": "8 ., רובע טז' עבור עמותת בית חב\"ד ע.ר לצורך הקמה והפעלת בית כנסת",
        },
        {
            "structure_unit_id": "u_second_action",
            "semantic_unit_id": "u_second_action",
            "structural_role": "continuation",
            "section_id": "s1",
            "continuation_of_unit_id": "split_parent",
            "raw_text": "5. * אישור הסכמי רשות ופיתוח להקצאת גג מבנה גן ילדים קיים במקרקעין הידועים כגוש2458 חלקי חלקה45",
        },
        {
            "structure_unit_id": "u_second_detail",
            "semantic_unit_id": "u_second_detail",
            "structural_role": "continuation",
            "section_id": "s1",
            "continuation_of_unit_id": "split_parent",
            "raw_text": "13 ., רובע ו' עבור עמותת בית החסידים ע.ר לצורך הקמה והפעלת בית כנסת ומקווה",
        },
    ]

    grouped = step4._apply_topic_unit_grouping(units=units, packet_role="protocol")
    by_id = {unit["structure_unit_id"]: unit for unit in grouped}

    assert by_id["u_agenda_heading"].get("topic_group_role") is None
    assert by_id["u_non_topic_heading"].get("topic_group_role") is None
    assert by_id["u_first_action"]["topic_group_role"] == "anchor"
    assert by_id["u_first_action"]["merged_detail_unit_ids"] == ["u_first_detail"]
    assert by_id["u_first_detail"]["merged_into_unit_id"] == "u_first_action"
    assert by_id["u_second_action"]["topic_group_role"] == "anchor"
    assert by_id["u_second_action"]["merged_detail_unit_ids"] == ["u_second_detail"]
    assert by_id["u_second_detail"]["merged_into_unit_id"] == "u_second_action"


def test_grouped_anchor_uses_original_anchor_text_for_topic_shape() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u_anchor",
            "semantic_unit_id": "u_anchor",
            "structural_role": "continuation",
            "raw_text": "7. * אישור נספח להסכם הרשאה לשימוש בנכסים עירוניים עבור \"תיירות עירונית\" לתוספת נכסים במקרקעין הידועים כגוש2791 חלקי חלקה9 לנכסים המפורטים כדלקמן 1 .הקמה והפעלת מתחם פעילות 2 .הקמה והפעלת מגרשי פדל בשטח של כ- 1400 מ\"ר",
            "topic_group_role": "anchor",
            "topic_group_anchor_raw_text": "7. * אישור נספח להסכם הרשאה לשימוש בנכסים עירוניים עבור \"תיירות עירונית\" לתוספת נכסים במקרקעין הידועים כגוש2791 חלקי חלקה9 לנכסים המפורטים כדלקמן",
            "merged_detail_unit_ids": ["u_detail"],
            "merged_detail_texts": ["1 .הקמה והפעלת מתחם פעילות", "2 .הקמה והפעלת מגרשי פדל בשטח של כ- 1400 מ\"ר"],
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "לתוספת נכסים במקרקעין הידועים כגוש חלקי חלקה", "context_source": "explicit_topic_marker_subject"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )

    assert item["row_type"] == "topic_item"
    assert item["is_topic_bearing"] is True
    assert item["topic_headline_he"].startswith("אישור נספח להסכם הרשאה")
    assert item["topic_identification_context"].startswith("אישור נספח להסכם הרשאה")


def test_square_meter_ocr_quote_does_not_replace_agreement_subject() -> None:
    raw_text = "7. * אישור הסכמי רשות ופיתוח להקצאת מקרקעין בשטח של כ- 500 \":מ\"ר בין עיריית אשדוד לעמותת לב לדעת \" ע.ר במקרקעין הידועים כגוש2815 חלקי חלקה22"
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "body",
            "raw_text": raw_text,
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )

    assert step4._best_explicit_topic_subject({"unit_raw_text": raw_text, "topic_headline_he": raw_text, "topic_identification_context": raw_text}) is None
    assert item["topic_headline_he"].startswith("אישור הסכמי רשות ופיתוח")
    assert item["topic_subject_he"].startswith("אישור הסכמי רשות ופיתוח")
    assert item["deterministic_topic_decision"]["action"] == "choose_existing_topic"


def test_response_to_order_proposal_vote_is_non_topic_fragment() -> None:
    text = "תגובת עו\"ד פלוני להצעה אין קשר בין האירוע לבין הנושא שהועלה. ההצעה יורדת מסדר היום ברוב קולות בעד- 20 נגד- 5 נמנע- אין"

    assert step4._non_topic_protocol_reason(headline=text, raw_text=text, structural_role="body", packet_role="protocol") == "order_proposal_response_vote_fragment"


def test_mayor_update_content_without_action_is_non_topic() -> None:
    text = "סעיף11 :. דברי ראש העיר • העיר חוגגת שנות מצוינות והוצג הלוגו הרשמי שנבחר בקול קורא ציבורי"

    assert step4._non_topic_protocol_reason(headline="הלוגו הרשמי", raw_text=text, structural_role="section_heading", packet_role="protocol") == "mayor_update_content"


def test_weak_span_does_not_override_strong_policy_assignment() -> None:
    row = {
        "root_topic_id": "root_agreements",
        "topic_subject_he": "הסכם שימוש במקרקעין",
        "topic_headline_he": "אישור נספח להסכם הרשאה לשימוש בנכסים עירוניים",
        "topic_identification_context": "אישור נספח להסכם הרשאה לשימוש בנכסים עירוניים",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "topic_assignment_route": "deterministic_v4_candidate_finder:strong_policy_match:existing_tree",
    }
    proposal = {
        "source": "weak_carrier_span",
        "root_topic_id": "root_planning_building",
        "subject_he": "הקמה והפעלת מגרשי פדל",
        "score": 86.0,
    }

    assert step4._topic_proposal_should_replace(row=row, proposal=proposal) is False


def test_compound_child_does_not_move_strong_action_policy_to_object_root() -> None:
    row = {
        "root_topic_id": "root_agreements",
        "topic_subject_he": "הסכם שימוש במקרקעין",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "topic_assignment_route": "deterministic_v4_candidate_finder:strong_policy_match:existing_tree",
    }
    item = {
        "unit_raw_text": "אישור נספח להסכם הרשאה לשימוש בנכסים עירוניים",
        "topic_identification_context": "אישור נספח להסכם הרשאה לשימוש בנכסים עירוניים",
        "topic_headline_he": "אישור נספח להסכם הרשאה לשימוש בנכסים עירוניים",
        "root_topic_candidates": [
            {
                "root_topic_id": "root_commerce_assets",
                "child_label_he": "נכסים עירוניים",
                "score": 0.94,
                "matched_terms": ["נכסים", "עירוניים"],
            }
        ],
    }

    assert step4._compound_child_subject_arbitration_assignment(row=row, item=item) is None


def test_topic_unit_grouping_merges_numbered_details_under_list_anchor() -> None:
    units = [
        {
            "structure_unit_id": "u_anchor",
            "semantic_unit_id": "u_anchor",
            "structural_role": "body",
            "section_id": "s1",
            "raw_text": "7. * אישור נספח להסכם הרשאה לשימוש בנכסים עירוניים לתוספת נכסים במקרקעין לנכסים המפורטים כדלקמן",
        },
        {
            "structure_unit_id": "u_detail",
            "semantic_unit_id": "u_detail",
            "structural_role": "task_row",
            "section_id": "s1",
            "continuation_of_unit_id": "u_anchor",
            "raw_text": "1 .הקמה והפעלת מתחם פעילות ימי הולדת בשטח של כ- 1000 מ\"ר",
        },
    ]

    grouped = step4._apply_topic_unit_grouping(units=units, packet_role="protocol")

    assert grouped[1]["topic_group_role"] == "merged_detail"
    assert grouped[1]["merged_into_unit_id"] == "u_anchor"
    assert "מתחם פעילות" in grouped[0]["raw_text"]


def test_topic_unit_grouping_does_not_merge_next_explicit_topic() -> None:
    units = [
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "body",
            "section_id": "s1",
            "continuation_of_unit_id": "split_parent",
            "raw_text": "5. * אישור הסכם שימוש במבנה עירוני",
        },
        {
            "structure_unit_id": "u2",
            "semantic_unit_id": "u2",
            "structural_role": "body",
            "section_id": "s1",
            "continuation_of_unit_id": "split_parent",
            "raw_text": "6. * אישור הסכם רשות שימוש במבני שתי כיתות גני ילדים",
        },
    ]

    grouped = step4._apply_topic_unit_grouping(units=units, packet_role="protocol")

    assert grouped[0]["topic_group_role"] == "anchor"
    assert grouped[1]["topic_group_role"] == "anchor"
    assert grouped[0].get("merged_detail_unit_ids") in (None, [])


def test_merged_topic_detail_item_cannot_be_resurrected_by_arbitration() -> None:
    item = {
        "structure_unit_id": "u_detail",
        "semantic_unit_id": "u_detail",
        "row_type": "merged_topic_detail",
        "topic_group_role": "merged_detail",
        "merged_into_unit_id": "u_anchor",
        "document_context": {"packet_role": "protocol"},
        "unit_raw_text": "רובע ו' עבור עמותה לצורך הקמה והפעלת בית כנסת",
        "raw_text": "רובע ו' עבור עמותה לצורך הקמה והפעלת בית כנסת",
        "root_topic_candidates": [{"root_topic_id": "root_religious_services", "score": 0.98, "matched_terms": ["בית כנסת"]}],
        "topic_policy_matches": [],
    }
    row = step4._non_topic_assignment(item)

    updated = step4._apply_topic_arbitration(assignments=[row], items=[item], enable_govmap_geo=False)[0]

    assert updated["is_topic_bearing"] is False
    assert updated["row_type"] == "merged_topic_detail"
    assert updated["root_topic_id"] == "root_agenda_queries"


def test_geo_fallback_routes_place_only_query_to_geo_candidate() -> None:
    text = 'שאילתה של ד"ר לחמני בנושא "כיכר רמון בעיר ודרך מנחם בגין"'
    item = _structured_fallback_item(text)
    row = {"structure_unit_id": item["structure_unit_id"], "root_topic_id": "root_agenda_queries", "topic_subject_he": None, "topic_node_status": "active", "is_topic_bearing": False, "topic_assignment_route": "deterministic_v4_row_type:fragment"}

    assignment = step4._structured_fallback_assignment(row=row, item=item, enable_govmap_geo=False)

    assert assignment is not None
    assert assignment["root_topic_id"] == "root_geo"
    assert assignment["child_label_he"] == "כיכרות וצמתים"
    assert assignment["topic_subject_he"] == "כיכר רמון בעיר ודרך מנחם בגין"
    assert assignment["topic_node_status"] == "candidate"
    assert assignment["geo_resolution"]["source"] == "local_geo_pattern"


def test_root_geo_child_is_repaired_from_subject_pattern() -> None:
    row = {
        "packet_role": "protocol",
        "root_topic_id": "root_geo",
        "child_label_he": "כתובות ורחובות",
        "raw_child_label_he": "כתובות ורחובות",
        "topic_subject_he": "כיכר רמון בעיר ודרך מנחם בגין",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "topic_assignment_route": "dictalm_v4_global_tree:child_choice:existing_tree",
    }

    repaired = step4._repair_root_geo_child_from_subject(row)

    assert repaired["child_label_he"] == "כיכרות וצמתים"
    assert repaired["geo_resolution"]["source"] == "local_geo_pattern"
    assert repaired["topic_assignment_route"].endswith(":geo_child_repaired")


def test_geo_fallback_does_not_steal_action_topic() -> None:
    assert step4._resolve_geo_fallback(
        subject="הקמת חניון מוניציפלי חכם בסמוך לתחנת רכבת",
        municipality="אשדוד",
        enable_govmap_geo=False,
    ) is None


def test_geo_fallback_prefers_google_maps_when_high_confidence(monkeypatch) -> None:
    def fake_google_maps_geo_resolution(*, subject: str, municipality: str | None) -> dict:
        return {
            "source": "google_maps_places",
            "confidence": "high",
            "status": "matched",
            "query": f"{subject} {municipality} ישראל",
            "child_label_he": "כיכרות וצמתים",
            "matched_name_he": "כיכר רמון",
            "matched_type": "point_of_interest",
            "place_id": "places/test",
            "lat_lng": {"lat": 31.8, "lng": 34.65},
        }

    def fail_govmap_geo_resolution(*, subject: str, municipality: str | None) -> dict:
        raise AssertionError("GovMap should not be called after a high-confidence Google match")

    monkeypatch.setattr(step4, "_google_maps_geo_resolution", fake_google_maps_geo_resolution)
    monkeypatch.setattr(step4, "_govmap_geo_resolution", fail_govmap_geo_resolution)

    geo = step4._resolve_geo_fallback(
        subject="כיכר רמון בעיר ודרך מנחם בגין",
        municipality="אשדוד",
        enable_govmap_geo=True,
    )

    assert geo is not None
    assert geo["source"] == "google_maps_places"
    assert geo["confidence"] == "high"
    assert geo["child_label_he"] == "כיכרות וצמתים"
    assert geo["local_geo_resolution"]["source"] == "local_geo_pattern"
    assert step4._geo_resolution_is_backend_confirmed(geo) is True


def test_geo_fallback_prefers_govmap_when_enabled(monkeypatch) -> None:
    def fake_govmap_geo_resolution(*, subject: str, municipality: str | None) -> dict:
        return {
            "source": "govmap_search",
            "confidence": "high",
            "query": f"{municipality} {subject}",
            "child_label_he": "כיכרות וצמתים",
            "matched_name_he": subject,
            "matched_type": "street",
            "centroid": {"x": 1, "y": 2},
        }

    monkeypatch.setattr(step4, "_google_maps_geo_resolution", lambda *, subject, municipality: {"source": "google_maps_places", "confidence": "none", "status": "no_match"})
    monkeypatch.setattr(step4, "_govmap_geo_resolution", fake_govmap_geo_resolution)

    geo = step4._resolve_geo_fallback(
        subject="כיכר רמון בעיר ודרך מנחם בגין",
        municipality="אשדוד",
        enable_govmap_geo=True,
    )

    assert geo is not None
    assert geo["source"] == "govmap_search"
    assert geo["confidence"] == "high"
    assert geo["child_label_he"] == "כיכרות וצמתים"
    assert geo["local_geo_resolution"]["source"] == "local_geo_pattern"
    assert geo["backend_resolution_attempts"][0]["source"] == "google_maps_places"


def test_geo_fallback_records_google_error_then_uses_local_pattern(monkeypatch) -> None:
    monkeypatch.setattr(
        step4,
        "_google_maps_geo_resolution",
        lambda *, subject, municipality: {"source": "google_maps_places", "confidence": "none", "status": "error", "query": "test query", "error": "missing_google_maps_api_key"},
    )
    monkeypatch.setattr(step4, "_govmap_geo_resolution", lambda *, subject, municipality: None)

    geo = step4._resolve_geo_fallback(
        subject="כיכר רמון בעיר ודרך מנחם בגין",
        municipality="אשדוד",
        enable_govmap_geo=True,
    )

    assert geo is not None
    assert geo["source"] == "local_geo_pattern"
    assert geo["child_label_he"] == "כיכרות וצמתים"
    assert geo["backend_resolution_attempts"][0]["source"] == "google_maps_places"
    assert geo["backend_resolution_attempts"][0]["error"] == "missing_google_maps_api_key"
    assert geo["backend_resolution_attempts"][1]["source"] == "govmap_search"


def test_google_maps_geo_resolution_extracts_place_diagnostics(monkeypatch) -> None:
    from municipality import google_maps_client

    calls: list[dict] = []

    class FakeGoogleMapsClient:
        def __init__(self, **kwargs) -> None:
            pass

        def search_text(self, query: str, *, max_results: int = 5) -> dict:
            calls.append({"query": query, "max_results": max_results})
            return {
                "places": [
                    {
                        "id": "places/ramon-square",
                        "displayName": {"text": "כיכר רמון"},
                        "formattedAddress": "דרך מנחם בגין, אשדוד, ישראל",
                        "location": {"latitude": 31.8, "longitude": 34.65},
                        "types": ["point_of_interest", "establishment"],
                        "googleMapsUri": "https://maps.google.com/?cid=test",
                    }
                ]
            }

    monkeypatch.setattr(google_maps_client, "GoogleMapsClient", FakeGoogleMapsClient)

    geo = step4._google_maps_geo_resolution(subject="כיכר רמון בעיר ודרך מנחם בגין", municipality="אשדוד")

    assert geo is not None
    assert geo["source"] == "google_maps_places"
    assert geo["confidence"] == "high"
    assert geo["status"] == "matched"
    assert geo["place_id"] == "places/ramon-square"
    assert geo["display_name"] == "כיכר רמון"
    assert geo["formatted_address"] == "דרך מנחם בגין, אשדוד, ישראל"
    assert geo["child_label_he"] == "כיכרות וצמתים"
    assert geo["municipality_match"] is True
    assert "כיכר" in geo["token_overlap"]
    assert geo["query"] == "כיכר רמון אשדוד ישראל"
    assert calls[0]["query"] == "כיכר רמון אשדוד ישראל"


def test_google_maps_geo_resolution_rejects_named_square_when_only_road_matches(monkeypatch) -> None:
    from municipality import google_maps_client

    class FakeGoogleMapsClient:
        def __init__(self, **kwargs) -> None:
            pass

        def search_text(self, query: str, *, max_results: int = 5) -> dict:
            return {
                "places": [
                    {
                        "id": "places/menachem-begin-road",
                        "displayName": {"text": "דרך מנחם בגין"},
                        "formattedAddress": "דרך מנחם בגין, אשדוד",
                        "location": {"latitude": 31.78, "longitude": 34.64},
                        "types": ["route"],
                    }
                ]
            }

    monkeypatch.setattr(google_maps_client, "GoogleMapsClient", FakeGoogleMapsClient)

    geo = step4._google_maps_geo_resolution(subject="כיכר רמון בעיר ודרך מנחם בגין", municipality="אשדוד")

    assert geo is not None
    assert geo["source"] == "google_maps_places"
    assert geo["status"] == "no_match"
    assert geo["confidence"] == "none"


def test_explicit_query_subject_strips_speaker_tail() -> None:
    item = {
        "unit_raw_text": "סעיף 7 : 5. שאילתה של ד\"ר לחמני בנושא \"כיכר רמון בעיר ודרך מנחם \"בגין גב' דינה בר אולפן הקריאה את תשובת ראש העיר לשאלה שלד\"ר לחמני בנושא \"כיכר רמו ן בעיר ודרך מנחם \"בגין ד”ר לחמני?: מה זה מינורי",
        "topic_headline_he": "כיכר רמו ן בעיר ודרך מנחם \"בגין ד”ר לחמני?: מה זה מינורי",
        "topic_identification_context": "כיכר רמו ן בעיר ודרך מנחם \"בגין ד”ר לחמני?: מה זה מינורי",
    }

    subject = step4._best_explicit_topic_subject(item)

    assert subject == "כיכר רמון בעיר ודרך מנחם בגין"


def test_contextual_event_block_links_transition_heading_to_next_decision() -> None:
    items = [
        {
            "structure_unit_id": "s0017_05_1392ca93cdcd",
            "source_page": 15,
            "source_window_id": "p15_w17",
            "section_id": "section_transition",
            "structural_role": "outline_item",
            "row_type": "topic_item",
            "topic_identification_context": "מינויים ושינויים בתאגידים ואיגודים",
            "topic_headline_he": "מינויים ושינויים בתאגידים ואיגודים",
            "topic_subject_he": "מינויים ושינויים בתאגידים ואיגודים",
            "unit_raw_text": "אנחנו עוברים לסעיף הבא: מינויים ושינויים בתאגידים ואיגודים",
        },
        {
            "structure_unit_id": "s0018_02_aba86ef0b9aa",
            "source_page": 16,
            "source_window_id": "p16_w18",
            "section_id": "section_decision",
            "structural_role": "outline_item",
            "row_type": "topic_item",
            "topic_identification_context": "מינויים ושינויים בתאגידים ואיגודים",
            "topic_headline_he": "החלטה: מינויים ושינויים בתאגידים ואיגודים",
            "topic_subject_he": "מינויים ושינויים בתאגידים ואיגודים",
            "unit_raw_text": "החלטה: מינויים ושינויים בתאגידים ואיגודים המועצה החליטה למנות את נציגי העירייה",
        },
    ]

    context = step4._contextual_event_context_for_item(items=items, index=0, subject_counts=step4.Counter())

    assert context["event_block"]["row_ids"] == ["s0017_05_1392ca93cdcd", "s0018_02_aba86ef0b9aa"]
    assert "החליטה למנות" in context["event_block"]["event_block_text"]


def test_call_json_model_retries_truncated_json(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    class FakeClient:
        calls = 0

        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, json: dict) -> FakeResponse:
            FakeClient.calls += 1
            if FakeClient.calls == 1:
                return FakeResponse({"message": {"content": "{\"ok\": true"}, "done": False})
            return FakeResponse({"message": {"content": "{\"ok\": true}"}, "done": True})

    monkeypatch.setattr(step4.httpx, "Client", FakeClient)

    result = step4._call_json_model(
        step="test_json_retry",
        item_ids=["u1"],
        request_payload={"schema": {"ok": "boolean"}},
        system_prompt="Return JSON only.",
        model="dicta-il/DictaLM-3.0-24B-Thinking:bf16",
        base_url="http://localhost:11434",
        timeout_seconds=1.0,
        model_call_dir=None,
        num_predict=16,
    )

    assert FakeClient.calls == 2
    assert result["ok"] is True


def test_people_role_fallback_routes_role_only_subject() -> None:
    item = _structured_fallback_item('שאילתה בנושא "ראש העיר"', topic_subject="ראש העיר")
    row = {"structure_unit_id": item["structure_unit_id"], "root_topic_id": "root_agenda_queries", "topic_subject_he": "ראש העיר", "topic_node_status": "candidate", "is_topic_bearing": True, "topic_assignment_route": "deterministic_v4_candidate_review:weak_candidate"}

    assignment = step4._structured_fallback_assignment(row=row, item=item, enable_govmap_geo=False)

    assert assignment is not None
    assert assignment["root_topic_id"] == "root_people_roles"
    assert assignment["child_label_he"] == "נבחרי ציבור ובעלי תפקידים"
    assert assignment["topic_node_status"] == "candidate"


def test_v3_fallback_maps_single_entailed_hint_through_topic_tree() -> None:
    hint = {
        "source": "topic_subject_v3",
        "source_structure_unit_id": "s0001_01_aaaaaaaaaaaa",
        "matter_he": "מועצה דתית",
        "action_type_he": "מינוי",
        "source_quote_he": "שאילתא בנושא מינוי מועצה דתית",
        "quality_status": "accepted",
        "row_role": "action_anchor",
        "event_role": "primary",
        "entailment_status": "entailed",
    }
    item = _structured_fallback_item("שאילתא בנושא מינוי מועצה דתית", hints=[hint])
    row = {"structure_unit_id": item["structure_unit_id"], "root_topic_id": "root_agenda_queries", "topic_subject_he": None, "topic_node_status": "active", "is_topic_bearing": False, "topic_assignment_route": "deterministic_v4_row_type:fragment"}

    assignment = step4._structured_fallback_assignment(row=row, item=item, enable_govmap_geo=False)

    assert assignment is not None
    assert assignment["root_topic_id"] == "root_religious_services"
    assert assignment["topic_subject_he"] == "מועצה דתית"
    assert assignment["v3_fallback_selection"]["status"] == "selected"


def test_v3_confirmed_location_action_is_active_without_external_geo_verification() -> None:
    hint = {
        "source": "topic_subject_v3",
        "source_structure_unit_id": "s0001_01_aaaaaaaaaaaa",
        "matter_he": "כיכר רמון בעיר ודרך מנחם בגין",
        "action_type_he": "מענה לשאילתה",
        "source_quote_he": "תשובת ראש העיר לשאילתה בנושא כיכר רמון בעיר ודרך מנחם בגין",
        "quality_status": "accepted",
        "row_role": "action_anchor",
        "event_role": "primary",
        "entailment_status": "entailed",
    }
    item = _structured_fallback_item(
        'שאילתה של ד"ר לחמני בנושא "כיכר רמון בעיר ודרך מנחם בגין"',
        topic_subject="כיכר רמון בעיר ודרך מנחם בגין",
        hints=[hint],
    )
    row = {
        "structure_unit_id": item["structure_unit_id"],
        "root_topic_id": "root_geo",
        "topic_subject_he": "כיכר רמון בעיר ודרך מנחם בגין",
        "topic_node_status": "candidate",
        "is_topic_bearing": True,
        "topic_assignment_route": "deterministic_v4_topic_arbitration:explicit_action_span",
    }
    proposal = {
        "source": "explicit_action_span",
        "subject_he": "כיכר רמון בעיר ודרך מנחם בגין",
        "root_topic_id": "root_geo",
        "child_label_he": "כיכרות וצמתים",
        "geo_resolution": {"source": "local_geo_pattern", "confidence": "medium", "child_label_he": "כיכרות וצמתים"},
        "score": 88,
        "confidence": 0.82,
        "quote": item["unit_raw_text"],
    }

    assignment = step4._assignment_from_topic_proposal(row=row, item=item, proposal=proposal)
    assignment = step4._normalize_protocol_non_topic_assignments([assignment])[0]

    assert assignment["root_topic_id"] == "root_geo"
    assert assignment["topic_node_status"] == "active"
    assert assignment["topic_reject_reason"] is None
    assert assignment["topic_review_status"] is None


def test_v3_response_to_location_query_keeps_location_root_not_mayor_updates() -> None:
    hint = {
        "source": "topic_subject_v3",
        "source_structure_unit_id": "s0001_01_aaaaaaaaaaaa",
        "matter_he": "כיכר רמון בעיר ודרך מנחם בגין",
        "matter_display_he": "כיכר רמון ודרך מנחם בגין",
        "action_type_he": "מענה לשאילתה",
        "source_quote_he": "תשובת ראש העיר לשאילתה בנושא כיכר רמון בעיר ודרך מנחם בגין",
        "quality_status": "accepted",
        "row_role": "action_anchor",
        "event_role": "primary",
        "entailment_status": "entailed",
    }
    item = _structured_fallback_item(
        'תשובת ראש העיר לשאילתה בנושא "כיכר רמון בעיר ודרך מנחם בגין"',
        topic_subject="כיכר רמון בעיר ודרך מנחם בגין",
        hints=[hint],
    )

    selection = step4._select_v3_fallback_hint(item=item)
    proposals = step4._v3_topic_proposals(item=item)

    assert selection["best"]["root_topic_id"] == "root_geo"
    assert proposals[0]["root_topic_id"] == "root_geo"


def test_religious_council_plural_appointment_stays_religious_services() -> None:
    assert step4.infer_root_topic_id("מינויים למועצות דתיות") == "root_religious_services"
    assert step4._existing_child_label_for_subject(root_topic_id="root_religious_services", subject="מינויים למועצות דתיות") == "מועצה דתית"


def test_v3_selected_candidate_beats_competing_multi_item_hint() -> None:
    hint = {
        "source": "topic_subject_v3",
        "source_structure_unit_id": "s0001_01_aaaaaaaaaaaa",
        "matter_he": "מינוי מועצה דתית",
        "action_type_he": "שאילתה",
        "source_quote_he": "שאילתא בנושא מינוי מועצה דתית",
        "selected_candidate_id": "religious_council",
        "quality_status": "accepted",
        "entailment_status": "entailed",
        "event_candidates": [
            {
                "candidate_id": "religious_council",
                "matter_he": "מינוי מועצה דתית",
                "action_type_he": "שאילתה",
                "matter_quote_he": "שאילתא בנושא מינוי מועצה דתית",
            },
            {
                "candidate_id": "sports_budget",
                "matter_he": "תקציב מכבי אשדוד",
                "action_type_he": "שאילתה",
                "matter_quote_he": "שאילתה בנושא תקציב מכבי אשדוד",
            },
        ],
    }
    item = _structured_fallback_item(
        "שאילתא בנושא מינוי מועצה דתית. שאילתה בנושא תקציב מכבי אשדוד",
        hints=[hint],
    )

    selection = step4._select_v3_fallback_hint(item=item)

    assert selection["status"] == "selected"
    assert selection["best"]["topic_subject_he"] == "מינוי מועצה דתית"
    assert selection["best"]["root_topic_id"] == "root_religious_services"


def test_non_geo_child_evidence_with_place_term_is_active() -> None:
    item = _structured_fallback_item('שאילתה בנושא "זרימת מי ביוב בחוף יא"', topic_subject="זרימת מי ביוב בחוף יא")
    row = {
        "structure_unit_id": item["structure_unit_id"],
        "root_topic_id": "root_infrastructure_environment",
        "topic_subject_he": "זרימת מי ביוב בחוף",
        "topic_node_status": "candidate",
        "is_topic_bearing": True,
        "topic_assignment_route": "deterministic_v4_topic_arbitration:explicit_action_span",
    }
    proposal = {
        "source": "explicit_action_span",
        "subject_he": "זרימת מי ביוב בחוף",
        "root_topic_id": "root_infrastructure_environment",
        "child_label_he": "ניהול מים וביוב",
        "score": 88,
        "confidence": 0.82,
        "quote": item["unit_raw_text"],
    }

    assignment = step4._assignment_from_topic_proposal(row=row, item=item, proposal=proposal)

    assert assignment["root_topic_id"] == "root_infrastructure_environment"
    assert assignment["child_label_he"] == "ניהול מים וביוב"
    assert assignment["topic_node_status"] == "active"
    assert assignment["topic_reject_reason"] is None


def test_order_proposal_discussion_subject_is_trimmed() -> None:
    contract = step4._topic_contract_from_headline(
        "הצעה לסדר שעסקה בנושא המיגון. וביקשנו לקיים דיון נוסף בוועדת החירום",
        structural_role="outline_item",
        packet_role="protocol",
    )

    assert contract["is_topic_bearing"] is True
    assert contract["topic_subject_he"] == "המיגון"


def test_multi_item_agenda_tail_is_protocol_listing() -> None:
    text = "4 מינוי נציג העירייה לוועדה הציבורית לפי תמא.10 )30 ' (עמ2024 דוח פניות ותלונות הציבור.11 )31 ' (עמ2024 דוח תאגידים לשנת.12 )34 'נאומים מהמקום (עמ.13"

    assert step4._looks_like_protocol_listing(text) is True
    assert step4._non_topic_protocol_reason(
        headline="מינוי נציג העירייה לוועדה הציבורית לפי תמא",
        raw_text=text,
        structural_role="outline_item",
        packet_role="protocol",
    ) == "protocol_listing"


def test_numbered_legal_tax_clause_is_not_protocol_listing() -> None:
    text = "4 )קבע מנהל הארנונה כי בשל נזק מלחמה נהרס נכס. 1( לא ישולם בגין נכס כאמור היטל. 2( נזק מלחמה כהגדרתו בחוק מס רכוש. 197 ', עמ8 ' דיני מדינת ישראל"

    assert step4._looks_like_protocol_listing(text) is False


def test_contract_approval_procedure_without_appendix_is_non_topic() -> None:
    assert step4._looks_like_contract_approval_procedure_fragment(
        "יובאו לאישור ועדת התקשרויות עליונה ואישור ההתקשרות בחוזה ללא מכרז"
    ) is True


def test_personal_topic_reference_is_non_topic() -> None:
    reason = step4._non_topic_protocol_reason(
        headline="שלי היום היא בנושא תיקון התנהלות בתאגידים העירוניים",
        raw_text="ההצעה שלי היום היא בנושא תיקון התנהלות בתאגידים העירוניים ואני רוצה להסביר",
        structural_role="body",
        packet_role="protocol",
    )

    assert reason == "personal_topic_reference"


def test_order_proposal_speaker_reply_is_non_topic() -> None:
    reason = step4._non_topic_protocol_reason(
        headline="יו\"ר הישיבה-מר זמיר את צודקת הוא באמת בהמשך של זה",
        raw_text="הצעה לסדר : יו\"ר הישיבה-מר זמיר .את צודקת. הוא באמת בהמשך של זה",
        structural_role="outline_item",
        packet_role="protocol",
    )

    assert reason == "speaker_reference_heading"


def test_appointment_heading_prefers_minnui_phrase() -> None:
    heading = step4._extract_heading_from_text(
        "עוברים לנושא הבא, נושא מס . נמל התעופה בן גוריון2/ מינוי נציג העירייה לוועדה הציבורית לפי תמ\"א בנמל התעופה בן גוריון"
    )

    assert heading == "מינוי נציג העירייה לוועדה הציבורית לפי תמ\"א בנמל התעופה בן גוריון"


def test_subject_cleaning_strips_years_to_metadata() -> None:
    assert step4.clean_protocol_subject_text("תקציב 2026") == "תקציב"
    assert step4.clean_protocol_subject_text("הצעת התקציב הרגיל והבלתי רגיל לשנת 2026") == "הצעת התקציב הרגיל והבלתי רגיל"
    assert step4.clean_protocol_subject_text("מכרז פומבי מס 89/2025 להשכרת מבנה") == "מכרז פומבי להשכרת מבנה"
    assert step4.clean_protocol_subject_text("צעדים לתיקון בעקבות דו\"ח .מבקר המדינה") == "צעדים לתיקון בעקבות דו\"ח מבקר המדינה"
    assert step4.clean_protocol_subject_text("תיקון מעלית המשכן לאומנויות הבמה המושבתת למעלה משנה וחצי") == "תיקון מעלית המשכן לאומנויות הבמה"
    assert step4.clean_protocol_subject_text("טיפול במערכת מיזוג במבנה ציבור שאינה פעילה מזה חודשים") == "טיפול במערכת מיזוג במבנה ציבור"
    assert step4.clean_protocol_subject_text("מזרקה בפארק המושבתת למעלה מחודש") == "מזרקה בפארק המושבתת למעלה מחודש"
    assert step4.clean_topic_label("תקציב 2026") == "תקציב"


def test_canonical_topic_label_shortens_reusable_subjects() -> None:
    cases = [
        (
            "הרכבת גוף בוחר לבחירת רב ראשי אורתודוכסי",
            "בחירת רב ראשי",
        ),
        (
            "עתיד מינויים ושינויים בוועדת מכרזים",
            "מינויים ושינויים בוועדת מכרזים",
        ),
        (
            "צעדים לתיקון ההתנהלות בתאגידים עירוניים בעקבות דו\"ח מבקר המדינה",
            "תיקון התנהלות בתאגידים עירוניים בעקבות דו\"ח מבקר המדינה",
        ),
        (
            "להלן–יפו (סלילת רחובות), תשע\"ב-בחוק עזר לתל–אביב .1 1 תיקון סעיף – 1 חוק העזר העיקרי), בסעיף",
            "תיקון חוק עזר סלילת רחובות",
        ),
        (
            "ההסתייגות היא בנושא תכנית שכונה כעיר, קיצוץ מתקציב התכנית לצמצום פערים",
            "קיצוץ תקציב תכנית שכונה כעיר",
        ),
        (
            "הצעה לסדר שעסקה בנושא המיגון",
            "מיגון",
        ),
        (
            "פרטי יציאת ראש העיר לחו\"ל מטעם הקונגרס היהודי",
            "נסיעה בתפקיד לחו\"ל",
        ),
        (
            "ה צפות חוזרות ונשנות ברחבי העיר- טיפול בתשתיות ופיצוי התושבים בעקבות נזקי ההצפות",
            "הצפות וטיפול בתשתיות",
        ),
        (
            "תיקון מעלית המשכן לאומנויות הבמה המושבתת למעלה משנה וחצי",
            "תיקון מעלית המשכן לאומנויות הבמה",
        ),
        (
            "הוספת תקנות עיר ירוקה להתקנת פאנלים סולאריים על גגות מבנים",
            "התקנת פאנלים סולאריים",
        ),
        (
            "פרסום אסור של מכירת דירות למגזר הציבור החרדי מחסידות בעלזא",
            "פרסום מכירת דירות",
        ),
        (
            "החלטה מספר2 – מקדמות לשנת2022 הועדה המקצועית ממליצה על תשלום מקדמה למוסד הפועל כעמותה",
            "מקדמות תמיכה לעמותות",
        ),
        (
            "פרוטוקול מישיבת ועדת תרומות מיום19.12.21 - עמותת אתנה",
            "ועדת תרומות",
        ),
        (
            "פרוטוקול מישיבת ועדת קליטה מיום29.11.21",
            "ועדת קליטה",
        ),
        (
            "פרוטוקול מישיבת הועדה למאבק בנגע הסמים המסוכנים מס1/21 מיום",
            "ועדה למאבק בנגע הסמים המסוכנים",
        ),
        (
            "מינוי מ 'נכ\"ל חב \"יובלים",
            "מינוי מנכ\"ל חברה עירונית",
        ),
        (
            "התשע\"ה א \"פטור לנכס שאינו ראוי לשימוש בשל נזק מלחמה א",
            "פטור לנכס בשל נזק מלחמה",
        ),
        (
            "להשכרת מבנה למטרת ניהול והפעלת/2025 ' מכרז פומבי מס- . אישור התקשרות עם זוכה",
            "התקשרות להשכרת מבנה",
        ),
        (
            "הסכם רשות בין עיריית אשדוד לבין עמותת מרכז לחינוך תורני לב שמחה דחסידי גור אשדוד - הסדרת שימוש לצורך הפעלת גני ילדים בגוש ח\"ח",
            "הסכם רשות למוסדות חינוך",
        ),
        (
            "דיון חוזר לאישור הסכם בין העירייה לבין עמותת משכנות שמעון בגוש חלקה ברחוב ברקת",
            "הסכם עם עמותה",
        ),
        (
            "עדכון שכר ל- 65% משכר מנכ\"ל לעוזר בכיר לראש הרשות שמואל דוד",
            "עדכון שכר עוזר בכיר",
        ),
        (
            "טבלת ניקוד ענפי הספורט בעיר אשדוד עפ\"י דירוג עדיפות הענף ורמת הישגיותו בוגרים נוער ילדים דירוג עדיפות 1 2 3 4 5 6",
            "ניקוד תמיכות ספורט",
        ),
        (
            "חילופי גברי בוועדות ובדירקטוריונים– .)(דינה ,השתתפו בזום",
            "חילופי גברי בוועדות ודירקטוריונים",
        ),
        (
            "המחלקה הווטרינרית– הסכמים עם מרפאות חוץ ( בטיפול בחתולים",
            "הסכמים עם מרפאות וטרינריות",
        ),
        (
            "טיפול העירייה בדרי רחוב שבתחומה",
            "טיפול בדרי רחוב",
        ),
        (
            "אבטחת מידע פנים והגנת פרטיות מפני מתקפות סייבר",
            "אבטחת מידע והגנת פרטיות",
        ),
        (
            "כנפי רוח\" הקצאה לפעילות רב תכליתית – מבקש ההגדרה לכך",
            "הקצאה לפעילות רב תכליתית",
        ),
        (
            "קריאת רחוב על שמו של זאב רווח ז\"ל",
            "קריאת רחוב על שמו של זאב רווח ז\"ל",
        ),
        (
            "לנוהל תמיכות לעמותות אשר הגישו בקשה למקדמה בהתאם לנהלים",
            "נוהל תמיכות לעמותות",
        ),
        (
            "מינוי מנכ\"ל חב' יובלים",
            "מינוי מנכ\"ל חברה עירונית",
        ),
        (
            "פרוטוקול מישיבת ו עדת תמיכות מקצועית מס 22 מיום.7.22",
            "ועדת תמיכות מקצועית",
        ),
        (
            "אישור נסיעה למשלחת ראש העיר לברצלונה",
            "נסיעת משלחת עירונית",
        ),
        (
            "הנחת מבנה יביל ברחוב האדמור מבעל\"ז",
            "הנחת מבנה יביל",
        ),
        (
            "אי בניית מבני ציבור מתנ\"סים בעשור האחרון",
            "בניית מבני ציבור ומתנ\"סים",
        ),
        (
            "זרימת מי ביוב בחוף יא",
            "זרימת מי ביוב בחוף",
        ),
        (
            "בקשה לשדרוג תבחינים - שיפוץ חזיתות אינג’ :כצנלסון ביקשנו לשדרג את התבחינים",
            "תבחינים לשיפוץ חזיתות",
        ),
        (
            "הנדון המלצה ל מענקי הישגי ות– ספורט א להלן רשימת ההישגים והמענקים לאישורכם",
            "מענקי הישגיות ספורט",
        ),
        (
            "ציוד מגן אישי שמתוקצב פר עובד, פר מקצוע",
            "ציוד מגן אישי לעובדים",
        ),
        (
            "סיוע להבטחת ביטחון תזונתי על רקע משבר הקורונה",
            "סיוע לביטחון תזונתי",
        ),
        (
            "חממת שטראוס מהתקציב העירוני",
            "מימון חממה מהתקציב העירוני",
        ),
        (
            "מה ההטבות שהתקבלו בעקבות הירידה במדד החברתי-כלכלי",
            "הטבות בעקבות ירידה במדד חברתי-כלכלי",
        ),
        (
            "אישור הרשאות עיריית אשדוד",
            "הרשאות עירייה",
        ),
        (
            "חידוש כהונה לעו\"ד אלי נכט בדירקטוריון יובלים",
            "חידוש כהונה בדירקטוריון",
        ),
        (
            "המאגר שממנו נשלחים מסרונים לתושבי העיר בנושאים שונים",
            "מאגר מסרונים לתושבים",
        ),
        (
            "מסר וידאו שנשלח לתושבים",
            "מסרי וידאו לתושבים",
        ),
        (
            "לוחות פרסום אלקטרוניים בעיר",
            "לוחות פרסום אלקטרוניים",
        ),
        (
            "מוכנות עיריית אשדוד לרעידת אדמה והתקנת מערכת התראה",
            "מוכנות לרעידת אדמה ומערכת התראה",
        ),
        (
            "זיהום ים משפכים בחופי המצודה ומנחל לכיש",
            "זיהום ים משפכים",
        ),
        (
            "פרוטוקול מישיבת ועדת הועדה למיגור תופעת האלימות מס 21 מיום",
            "ועדה למיגור תופעת האלימות",
        ),
        (
            "אופן הטיפול בגורים יונקים של חתולי רחוב",
            "טיפול בגורי חתולי רחוב",
        ),
        (
            "המח' הווטרינרית- שעות פעילות ותקציב המחלקה",
            "שעות פעילות ותקציב מחלקה וטרינרית",
        ),
        (
            "מדעניות העתיד של העיר אשדוד",
            "תוכנית מדעניות העתיד",
        ),
        (
            "סיוע ממשלת ישראל במיגון העיר אשדוד",
            "סיוע במיגון העיר",
        ),
        (
            "הסכם עם החברה העירונית לתיירות אשדוד",
            "הסכם עם חברה עירונית לתיירות",
        ),
        (
            "בקשה לאישור מועצה להתקשרות בפטור ממכרז- ביטוח אחריות נושאי משרה",
            "התקשרות לביטוח אחריות נושאי משרה",
        ),
        (
            "הקמת פסל ע\"ש יאשה אליאשוילי",
            "הקמת פסל ציבורי",
        ),
        (
            "שימוע למהנדס העיר ד\"ר לחמני המהנדס כרגע עדיין נמצא בתפקיד",
            "שימוע למהנדס העיר",
        ),
        (
            "אלרגיות מסכנות חיים ומזרקי אפיפן במרחב הציבורי",
            "אלרגיות ואפיפן במרחב הציבורי",
        ),
        (
            "אלרגיות מסכנות דיון עפ\"י",
            "אלרגיות ואפיפן במרחב הציבורי",
        ),
        (
            "תרומת זורב גגולשוילי",
            "הקמת פסל ציבורי",
        ),
        (
            "פרוטוקול למחיקת חובות מס7.25 מיום25.12",
            "מחיקת חובות",
        ),
        (
            "שינוי קריטריונים בביטוח לאומי למתן טיפולים פרא רפואיים",
            "טיפולים פרא רפואיים",
        ),
        (
            "עדכון תקנוני הפרסים העירוניים ואישור תקנון פרס התיאטרון",
            "פרסים עירוניים",
        ),
        (
            "מינוי מ\"מ ראש העיר כדירקטור ויו\"ר דירקטוריון החברה העירונית לאנרגיה",
            "מינוי בדירקטוריון",
        ),
        (
            "אישור אינג' שמעון כצנלסון לשמש כנציג עיריית אשדוד בדיוני הוועדה המחוזית לתכנון ובנייה",
            "נציג בוועדה מחוזית לתכנון ובנייה",
        ),
        (
            "הסמכת עו )ד מירב ביטון לרשמת הנכסים של עיריית אשדוד (בן עדי",
            "הסמכת רשם נכסים",
        ),
        (
            "תחנת שאיבת ביוב ומאגר חירום לביוב ברובע יג",
            "תחנת שאיבת ביוב ומאגר חירום",
        ),
        (
            "זיהום אויר חמור ותלונות חוזרות על ריחות קשים ברחבי העיר",
            "זיהום אוויר וריחות",
        ),
        (
            "השבתת המזרקה המוזיקלית בפארק אשדוד ים",
            "השבתת מזרקה בפארק",
        ),
        (
            "ההשקעה הנמוכה בחינוך באשדוד- נתוני מחקר, תלונות הורים והצורך בתיקון מידי",
            "השקעה בחינוך",
        ),
        (
            "אופן זימון הורים לוועדות אפיון וזכאות",
            "ועדות אפיון וזכאות",
        ),
        (
            "ייעוד המקרקעין מבנים ומוסדות ציבור, למטרת הפעלת כיתת גן ילדים אחת",
            "ייעוד מקרקעין לכיתת גן",
        ),
        (
            "טיפול בבדידות קשישים עריריים, ניטור צריכת מים, לחצני מצוקה וחיישני תנועה",
            "טיפול בקשישים עריריים",
        ),
        (
            "מתן במה לאמנות מקומית ואמנים מקומיים בחגיגות ה לאשדוד",
            "אמנות מקומית",
        ),
        (
            "חלופה למאו ת בני נוער במקרה של סגירת קבוצת עירוני אשדוד",
            "חלופה לבני נוער בעקבות סגירת קבוצת ספורט",
        ),
        (
            "תכנית מס-1445311 איחוד וחלוקה ותוספת זכויות בנייה למבנה בית הדר",
            "תוכנית איחוד וחלוקה וזכויות בנייה",
        ),
        (
            "הסמכת החברה לתיירות אשדוד למימוש התוכנית לפתרון חניית קרוואנים",
            "תוכנית לפתרון חניית קרוואנים",
        ),
        (
            "כשל מתמשך בטיפול באגם המרינה ובחינת העברת האחריות",
            "טיפול באגם המרינה",
        ),
        (
            "לפעילות מלאה בחופי אשדוד",
            "פעילות בחופים",
        ),
        (
            "בע\"מ (מאור פוקס )הצטרפות ועשייה במקרקעין",
            "פעולה במקרקעין",
        ),
    ]

    for raw, expected in cases:
        evidence = "חיים ומזרקי אפיפן במרחב הציבורי" if raw.startswith("אלרגיות מסכנות דיון") else "תרומה להקמת פסל ע\"ש יאשה" if raw.startswith("תרומת") else None
        canonical, _reason = step4.canonicalize_topic_label(raw, evidence_text=evidence)
        assert canonical == expected


def test_residual_transcript_fragments_are_non_topics() -> None:
    cases = [
        ".כל הדברים האלה, נראה את המשמעויות, ואני מקווה שנקבל החלטות מושכלות",
        ",זה אחד האישורים שהיא צריכה לקבל. בגלל שאנחנו רוצים להקדים את זה ולעשות את זה",
        ") חברי האופוזיציה שלא נכחו בדיון את מינוי",
        ") חברי האופוזיציה שלא נכחו בדיון את פרוטוקול הוועדה למאבק בנגע הסמים",
        "הצעה לסדר הבאה. אין הצעה, אנחנו רק פועלים כמו שאנחנו פועלים. אנחנו נעשה אותו גם בלי קשר",
        "עו\"ד גבי כנפו. זה מחייב אישור מועצת עיר",
        "גב' דינה בר-אולפן: יצורף לפרוטוקול, אם תהיה תשובה במשך הישיבה תעביר אותה התשובה הועברה במייל",
    ]

    for text in cases:
        assert step4._non_topic_protocol_reason(
            headline=text,
            raw_text=text,
            structural_role="body",
            packet_role="protocol",
        ) in {
            "deliberation_continuation_fragment",
            "approval_timing_fragment",
            "attendance_context_fragment",
            "no_proposal_or_agenda_action_fragment",
            "approval_requirement_fragment",
            "reply_attachment_procedure_fragment",
        }


def test_unsupported_model_subject_in_protocol_listing_is_non_topic() -> None:
    item = {
        "structure_unit_id": "u_listing",
        "semantic_unit_id": "u_listing",
        "document_context": {"packet_role": "protocol"},
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "34.פרוטוקול מישיבת ועדת מחיקת חובות מס6/21 מיום2.12.21 – מצ\"ל 35. פרוטוקול מישיבת ועדת הנחות במיסים ומוסד מתנדב מס5/21 מיום3.1.22 – מצ\"ל 36. פרוטוקול מישיבת הועדה למאבק בנגע הסמים המסוכנים מס1/21 מיום13.10.21 – מצ\"ל",
        "topic_identification_context": "פרוטוקול מישיבת ועדת מחיקת חובות, ועדת הנחות במיסים והועדה למאבק בנגע הסמים",
        "topic_headline_he": "פרוטוקולים של ועדות",
        "topic_subject_he": "פרוטוקולים של ועדות",
    }
    parsed = {
        "root_topic_id": "root_hr_labor",
        "is_topic_bearing": True,
        "topic_subject_he": "עדכון שכר ל- 65% משכר מנכ\"ל לעוזר בכיר",
        "clean_subject_he": "עדכון שכר ל- 65% משכר מנכ\"ל לעוזר בכיר",
        "topic_supporting_quote_he": "עדכון שכר ל- 65% משכר מנכ\"ל לעוזר בכיר",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["is_topic_bearing"] is False
    assert assignment["topic_subject_he"] is None


def test_dicta_authoritative_keeps_valid_dicta_root_over_policy() -> None:
    item = {
        "structure_unit_id": "u_auth_policy",
        "semantic_unit_id": "u_auth_policy",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_authoritative",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "מינוי מנכ\"ל חב' יובלים",
        "topic_identification_context": "מינוי מנכ\"ל חב' יובלים",
        "topic_headline_he": "מינוי מנכ\"ל חב' יובלים",
        "topic_subject_he": "מינוי מנכ\"ל חב' יובלים",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_administration"},
        "root_topic_candidates": [{"root_topic_id": "root_administration", "score": 0.9}],
        "candidate_child_topics": [],
    }
    parsed = {
        "root_topic_id": "root_culture_sport",
        "is_topic_bearing": True,
        "topic_subject_he": "מינוי מנכ\"ל חב' יובלים",
        "clean_subject_he": "מינוי מנכ\"ל חב' יובלים",
        "topic_supporting_quote_he": "מינוי מנכ\"ל חב' יובלים",
        "confidence": 0.91,
        "rationale_he": "בדיקת מצב סמכותי: יש להשתמש בשורש שהמודל בחר.",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_culture_sport"
    assert assignment["topic_node_status"] == "active"
    assert assignment["root_adjudication_decision"] == "dicta_authoritative_root"
    assert assignment["dicta_raw_root_topic_id"] == "root_culture_sport"
    assert assignment["dicta_raw_subject_he"] == "מינוי מנכ\"ל חב' יובלים"


def test_dicta_authoritative_does_not_canonical_reparent_valid_root() -> None:
    item = {
        "structure_unit_id": "u_auth_canonical",
        "semantic_unit_id": "u_auth_canonical",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_authoritative",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "שאילתא בנושא מעקב זכויות אויר",
        "topic_identification_context": "מעקב זכויות אויר",
        "topic_headline_he": "מעקב זכויות אויר",
        "topic_subject_he": "מעקב זכויות אויר",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_planning_building"},
        "root_topic_candidates": [{"root_topic_id": "root_planning_building", "score": 0.9}],
        "candidate_child_topics": [],
    }
    parsed = {
        "root_topic_id": "root_agenda_queries",
        "is_topic_bearing": True,
        "topic_subject_he": "מעקב זכויות אויר",
        "clean_subject_he": "מעקב זכויות אויר",
        "topic_supporting_quote_he": "שאילתא בנושא מעקב זכויות אויר",
        "confidence": 0.88,
        "rationale_he": "בדיקת מצב סמכותי: שורש המודל נשמר גם אם ניקוי התווית מזהה תחום אחר.",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_agenda_queries"
    assert assignment["topic_subject_he"] == "זכויות אוויר"
    assert "canonical_root_reparent" not in assignment["topic_assignment_route"]
    assert assignment["topic_node_status"] == "active"


def test_contextual_payload_includes_child_choices_without_full_policy_dump() -> None:
    item = {
        "structure_unit_id": "u_contextual_payload",
        "semantic_unit_id": "u_contextual_payload",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "שאילתא בנושא פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_identification_context": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_headline_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_subject_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "root_topic_candidates": [
            {"root_topic_id": "root_mayor_updates", "root_label_he": "עדכוני ראש העיר", "score": 0.9},
            {"root_topic_id": "root_travel_approvals", "root_label_he": "אישורי נסיעות", "score": 0.84},
        ],
        "candidate_child_topics": [
            {
                "candidate_child_id": "child_travel",
                "label_he": "נסיעה בתפקיד לחו\"ל",
                "root_topic_id": "root_travel_approvals",
                "root_label_he": "אישורי נסיעות",
                "evidence_quote_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
                "evidence_source": "heading",
                "confidence_hint": 0.68,
                "aliases_he": ["פרטי יציאה לחו\"ל"],
            }
        ],
        "topic_policy_matches": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_payload",
        "is_standalone_topic": True,
        "agenda_status_he": None,
        "agenda_carrier_he": "שאילתא",
        "primary_action_he": "נסיעה בתפקיד לחו\"ל",
        "service_domain_he": None,
        "clean_subject_he": "נסיעה בתפקיד לחו\"ל",
        "event_summary_he": "בירור פרטי יציאה של סגן ראש העיר לחו\"ל",
        "supporting_quote_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "confidence": 0.8,
    }

    payload = step4._contextual_classification_payload(item=item, normalized_event=normalized_event, topic_tree=step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[]))

    assert "topic_policies" not in payload
    assert payload["candidate_child_choices"][0]["root_topic_id"] == "root_travel_approvals"
    travel_root = next(root for root in payload["allowed_root_topics"] if root["root_topic_id"] == "root_travel_approvals")
    assert any(example["child_label_he"] == "נסיעות עבודה לחו\"ל" for example in travel_root["child_examples"])


def test_thinking_model_keeps_thinking_prompt_and_small_model_no_think() -> None:
    assert step4._model_thinking_enabled("dicta-il/DictaLM-3.0-24B-Thinking:bf16") is True
    assert step4._model_thinking_enabled("dicta-il/DictaLM-3.0-1.7B-Thinking:latest") is False
    assert step4._model_system_prompt(model="dicta-il/DictaLM-3.0-24B-Thinking:bf16", prompt="/no_think\nReturn JSON only.") == "Return JSON only."
    assert step4._model_system_prompt(model="dicta-il/DictaLM-3.0-1.7B-Thinking:latest", prompt="Return JSON only.") == "/no_think\nReturn JSON only."


def test_contextual_accepts_unwrapped_single_assignment_json() -> None:
    item = {
        "structure_unit_id": "u_contextual_unwrapped",
        "semantic_unit_id": "u_contextual_unwrapped",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "דיון בנושא תקציב הגיל הרך",
        "topic_identification_context": "תקציב הגיל הרך",
        "topic_headline_he": "תקציב הגיל הרך",
        "topic_subject_he": "תקציב הגיל הרך",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_budget_finance"},
        "root_topic_candidates": [{"root_topic_id": "root_budget_finance", "score": 0.9}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_unwrapped",
        "standalone_event": True,
        "agenda_disposition": "discussed",
        "event_status": "discussed",
        "indexability_status": "standalone_topic",
        "is_standalone_topic": True,
        "agenda_status_he": None,
        "agenda_carrier_he": None,
        "municipal_action_he": "דיון תקציבי",
        "semantic_subject_he": "תקציב הגיל הרך",
        "primary_action_he": "דיון תקציבי",
        "service_domain_he": "חינוך",
        "classification_basis": "primary_action",
        "clean_subject_he": "תקציב הגיל הרך",
        "event_summary_he": "דיון תקציבי בנושא הגיל הרך.",
        "supporting_quote_he": "תקציב הגיל הרך",
        "confidence": 0.95,
    }
    response = {
        "structure_unit_id": "u_contextual_unwrapped",
        "agenda_disposition": "discussed",
        "event_status": "discussed",
        "indexability_status": "standalone_topic",
        "root_topic_id": "root_budget_finance",
        "is_topic_bearing": True,
        "topic_subject_he": "תקציב הגיל הרך",
        "clean_subject_he": "תקציב הגיל הרך",
        "classification_basis": "primary_action",
        "topic_supporting_quote_he": "תקציב הגיל הרך",
        "confidence": 0.95,
        "rationale_he": "הפעולה היא תקציבית.",
        "why_not_other_roots_he": "בדיקה",
        "competing_roots": [],
    }

    assert step4._missing_contextual_classification_fields(response) == []
    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert parsed["contextual_schema_valid"] is True
    assert assignment["root_topic_id"] == "root_budget_finance"
    assert assignment["topic_node_status"] == "active"


def test_contextual_schema_defaults_use_normalized_subject_before_validation() -> None:
    item = {
        "structure_unit_id": "u_contextual_defaults",
        "semantic_unit_id": "u_contextual_defaults",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "דיון בנושא הקמת מתנ\"ס ברובע ט\"ו",
        "topic_identification_context": "הקמת מתנ\"ס ברובע ט\"ו",
        "topic_headline_he": "הקמת מתנ\"ס ברובע ט\"ו",
        "topic_subject_he": "הקמת מתנ\"ס ברובע ט\"ו",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_planning_building"},
        "root_topic_candidates": [{"root_topic_id": "root_planning_building", "score": 0.9}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_defaults",
        "agenda_disposition": "discussed",
        "event_status": "discussed",
        "indexability_status": "standalone_topic",
        "clean_subject_he": "הקמת מתנ\"ס ברובע ט\"ו",
        "semantic_subject_he": "הקמת מתנ\"ס ברובע ט\"ו",
        "primary_action_he": "הקמת מתנ\"ס",
        "classification_basis": "primary_action",
        "supporting_quote_he": "דיון בנושא הקמת מתנ\"ס ברובע ט\"ו",
    }
    response = {
        "assignments": [
            {
                "structure_unit_id": "u_contextual_defaults",
                "root_topic_id": "root_planning_building",
                "is_topic_bearing": True,
                "confidence": 0.9,
            }
        ]
    }

    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert parsed["contextual_schema_valid"] is True
    assert assignment["topic_subject_he"] == "הקמת מתנ\"ס ברובע ט\"ו"
    assert assignment["topic_node_status"] == "active"


def test_quote_support_is_tolerant_to_ocr_quote_damage() -> None:
    item = {
        "raw_text": "העמדת לוח מודעות אלקטרוני \"בצומת הרחובות שדרות בגין ושדרות הרצל",
        "topic_identification_context": "שאילתא בנושא העמדת לוח מודעות אלקטרוני \"בצומת הרחובות שדרות בגין ושדרות הרצל",
        "explicit_actions": [],
        "referenced_attachment_contexts": [],
    }

    assert step4._quote_supported_by_item(
        item=item,
        quote="העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
    ) is True


def test_active_conversational_subject_is_demoted_to_non_topic() -> None:
    row = {
        "structure_unit_id": "u_dialogue_subject",
        "semantic_unit_id": "u_dialogue_subject",
        "packet_role": "protocol",
        "row_type": "topic_item",
        "structural_role": "body",
        "is_topic_bearing": True,
        "topic_node_status": "active",
        "root_topic_id": "root_administration",
        "root_label_he": "מנהל עירוני ומינויים",
        "topic_subject_he": "נאמר שגם ככה זה קיים בישיבות",
        "topic_headline_he": "נאמר שגם ככה זה קיים בישיבות",
        "topic_identification_context": "נאמר שגם ככה זה קיים בישיבות",
        "topic_assignment_route": "dictalm_v4_global_tree:dicta_contextual_root:root_only",
        "dicta_contextual_indexability_status": "standalone_topic",
        "dicta_contextual_event_status": "discussed",
    }

    normalized = step4._normalize_protocol_non_topic_assignments([row])[0]

    assert normalized["is_topic_bearing"] is False
    assert normalized["topic_subject_he"] is None
    assert "post_non_topic:active_conversational_subject" in normalized["topic_assignment_route"]


def test_dialogue_exchange_subject_with_commitment_word_is_demoted() -> None:
    row = {
        "structure_unit_id": "u_dialogue_commitment",
        "semantic_unit_id": "u_dialogue_commitment",
        "packet_role": "protocol",
        "row_type": "topic_item",
        "structural_role": "continuation",
        "is_topic_bearing": True,
        "topic_node_status": "active",
        "root_topic_id": "root_budget_finance",
        "root_label_he": "תקציב וכספים",
        "topic_subject_he": "נכתב כתב ההתחייבות, אחרי ההסכמה אתו",
        "topic_headline_he": "נכתב כתב ההתחייבות, אחרי ההסכמה אתו ד\"ר כהן?: למה הוא תרם את הקומה מרצונו הטוב עו\"ד לוי: כן",
        "topic_identification_context": "נכתב כתב ההתחייבות, אחרי ההסכמה אתו ד\"ר כהן?: למה הוא תרם את הקומה מרצונו הטוב עו\"ד לוי: כן",
        "topic_assignment_route": "dictalm_v4_global_tree:dicta_contextual_root:root_only",
        "dicta_contextual_indexability_status": "standalone_topic",
        "dicta_contextual_event_status": "discussed",
    }

    normalized = step4._normalize_protocol_non_topic_assignments([row])[0]

    assert normalized["is_topic_bearing"] is False
    assert "post_non_topic:active_conversational_subject" in normalized["topic_assignment_route"]


def test_model_error_fallback_is_review_candidate_without_strong_evidence() -> None:
    item = {
        "structure_unit_id": "u_model_error_fallback_weak",
        "semantic_unit_id": "u_model_error_fallback_weak",
        "document_context": {"packet_role": "protocol"},
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "דיון בנושא תקציב הגיל הרך",
        "topic_identification_context": "תקציב הגיל הרך",
        "topic_headline_he": "תקציב הגיל הרך",
        "topic_subject_he": "תקציב הגיל הרך",
        "root_topic_candidates": [{"root_topic_id": "root_budget_finance", "score": 0.72}],
        "candidate_child_topics": [],
    }

    assignment = step4._fallback_assignment(item, reason="model_error")

    assert assignment["topic_node_status"] == "candidate"
    assert assignment["topic_reject_reason"] == "non_blocking_topic_review:model_error"


def test_candidate_review_prefers_supported_non_procedural_root() -> None:
    item = {
        "structure_unit_id": "u_notice_board",
        "semantic_unit_id": "u_notice_board",
        "document_context": {"packet_role": "protocol"},
        "packet_role": "protocol",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "שאילתא בנושא העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
        "topic_identification_context": "העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
        "topic_headline_he": "העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
        "topic_subject_he": "העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
        "root_topic_candidates": [
            {"root_topic_id": "root_transport_safety", "score": 0.8428, "matched_terms": ["צומת", "רחובות", "שדרות"]},
            {"root_topic_id": "root_agenda_queries", "score": 0.54, "matched_terms": ["לוח", "מודעות"]},
        ],
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_agenda_queries", "confidence": 0.54},
        "candidate_child_topics": [],
    }

    assignment = step4._candidate_review_assignment(item=item, reason="dicta_conservative_review")

    assert assignment["root_topic_id"] != "root_agenda_queries"
    assert assignment["topic_node_status"] == "candidate"


def test_prompt_schema_converts_to_ollama_json_schema() -> None:
    schema = step4._json_schema_from_prompt_schema(
        {
            "status": "accepted|needs_review|rejected",
            "confidence": 0.0,
            "items": [{"name": "string", "enabled": "boolean|null"}],
        }
    )

    assert schema["type"] == "object"
    assert schema["properties"]["status"]["enum"] == ["accepted", "needs_review", "rejected"]
    assert schema["properties"]["items"]["items"]["properties"]["enabled"]["type"] == ["boolean", "null"]


def test_ceremonial_notice_heading_is_non_topic() -> None:
    reason = step4._non_topic_protocol_reason(
        headline="סעיף2 : ברכות,הוקרות והודעות",
        raw_text="סעיף2 : ברכות,הוקרות והודעות",
        structural_role="outline_item",
        packet_role="protocol",
    )

    assert reason == "ceremonial_notice_heading"


def test_protocol_cover_metadata_is_non_topic() -> None:
    reason = step4._non_topic_protocol_reason(
        headline="פרוטוקול הו ו עדה המקצועית עיריית אשדוד מספר15 / 2022 מתאריך22.11.2022",
        raw_text="פרוטוקול הו ו עדה המקצועית עיריית אשדוד מספר15 / 2022 מתאריך22.11.2022",
        structural_role="outline_item",
        packet_role="protocol",
    )

    assert reason == "protocol_cover_metadata"


def test_reply_attachment_tail_is_inheritable_evidence() -> None:
    item = {
        "unit_raw_text": "ג ב' מרק .: אני אשמח שההורים ידעו .השאלה והתשובה מצורפות לפרוטוקול זה כנספח",
        "structural_role": "outline_item",
    }
    row = {"topic_subject_he": None, "topic_node_status": "candidate", "is_topic_bearing": False}

    assert step4._evidence_only_arbitration_reason(row=row, item=item) == "reply_attachment_dependent_detail"


def test_v3_facility_subject_overrides_reply_tail_without_using_procedural_root() -> None:
    raw = "המתנ\"ס. .שתושבי רובע ט\"ו מחכים לו כל כך הרבה שנים .השאלה והתשובה מצורפות לפרוטוקול זה כנספח"
    hint = {
        "source": "topic_subject_v3",
        "source_structure_unit_id": "vh006_07_01_966a6a09f777",
        "matter_he": "המתנ\"ס שתושבי רובע ט\"ו מחכים לו כל כך הרבה שנים",
        "matter_display_he": "מתנ\"ס לרובע ט\"ו",
        "action_type_he": "מענה לשאילתה",
        "source_quote_he": "השאלה והתשובה מצורפות לפרוטוקול זה כנספח",
        "quality_status": "accepted",
        "row_role": "action_anchor",
        "event_role": "primary",
    }
    item = _structured_fallback_item(raw, hints=[hint])
    item["structure_unit_id"] = "vh006_07_01_966a6a09f777"
    row = {
        "structure_unit_id": "vh006_07_01_966a6a09f777",
        "root_topic_id": "root_geo",
        "topic_subject_he": None,
        "raw_topic_subject_he": "המתנ\"ס שתושבי רובע ט\"ו מחכים לו כל כך הרבה שנים",
        "topic_node_status": "active",
        "is_topic_bearing": False,
        "topic_assignment_route": "dictalm_v4_global_tree:root_only",
    }

    assignment = step4._apply_topic_arbitration(assignments=[row], items=[item], enable_govmap_geo=False)[0]

    assert assignment["root_topic_id"] == "root_planning_building"
    assert assignment["topic_subject_he"] == "מתנ\"ס רובע ט\"ו"
    assert assignment["topic_node_status"] == "active"
    assert assignment["is_topic_bearing"] is True


def test_v3_support_criteria_tail_is_active_topic_not_review_candidate() -> None:
    raw = ".מבקשת לבחון את התבחינים מחדש, לאור מצב הקבוצות והקריטריונים .השאלה והתשובה מצורפות לפרוטוקול זה כנספח"
    hint = {
        "source": "topic_subject_v3",
        "source_structure_unit_id": "s0001_01_aaaaaaaaaaaa",
        "matter_he": "תבחינים לתמיכות",
        "action_type_he": "שאילתה",
        "source_quote_he": "מבקשת לבחון את התבחינים מחדש",
        "quality_status": "accepted",
        "row_role": "action_anchor",
        "event_role": "primary",
        "entailment_status": "entailed",
    }
    item = _structured_fallback_item(raw, hints=[hint])
    row = {
        "structure_unit_id": item["structure_unit_id"],
        "root_topic_id": "root_agenda_queries",
        "topic_subject_he": None,
        "topic_node_status": "candidate",
        "is_topic_bearing": False,
        "topic_assignment_route": "deterministic_v4_row_type:fragment",
    }

    assignment = step4._apply_topic_arbitration(assignments=[row], items=[item], enable_govmap_geo=False)[0]
    normalized = step4._normalize_protocol_non_topic_assignments([assignment])[0]

    assert normalized["root_topic_id"] == "root_supports"
    assert normalized["child_label_he"] == "תבחינים"
    assert normalized["topic_node_status"] == "active"
    assert normalized["topic_review_status"] is None


def test_v3_proposal_does_not_override_numeric_evidence_fragment() -> None:
    proposal = {"source": "v3_event_subject", "score": 94, "root_topic_id": "root_supports", "subject_he": "עמותת יד לבנים"}

    assert step4._topic_proposal_overrides_evidence_only(proposal, reason="numeric_or_partial_evidence") is False


def test_nearby_contained_visual_split_can_inherit_context() -> None:
    current = {"unit_raw_text": "המתנ\"ס. שתושבי רובע ט\"ו מחכים לו כל כך הרבה שנים", "source_window_id": "visual_page_6"}
    candidate = {"unit_raw_text": "שאילתה בנושא אי בניית מבני ציבור ומתנ\"סים. המתנ\"ס. שתושבי רובע ט\"ו מחכים לו כל כך הרבה שנים", "source_window_id": "p6_w16"}

    assert step4._nearby_item_can_provide_context(current=current, candidate=candidate) is True


def test_inherited_context_ignores_self_parent_id() -> None:
    previous = {
        "structure_unit_id": "parent",
        "unit_raw_text": "דיון בנושא החזר תשלומי הורים. אני אשמח שההורים ידעו",
        "source_window_id": "p11_w23",
    }
    current = {
        "structure_unit_id": "child",
        "unit_raw_text": "אני אשמח שההורים ידעו",
        "source_window_id": "visual_page_11",
        "parent_agenda_unit_id": "child",
    }
    assignments = {
        "parent": {"structure_unit_id": "parent", "root_topic_id": "root_education", "topic_subject_he": "החזר תשלומי הורים", "topic_node_status": "active"},
        "child": {"structure_unit_id": "child", "root_topic_id": "root_education", "topic_subject_he": "אני אשמח שההורים ידעו", "topic_node_status": "candidate"},
    }

    context = step4._select_inherited_topic_context(
        row=assignments["child"],
        item=current,
        items=[previous, current],
        assignment_by_id=assignments,
        emitted_by_id={},
        order_by_id={"parent": 0, "child": 1},
    )

    assert context["source_unit_id"] == "parent"
    assert context["subject_he"] == "החזר תשלומי הורים"


def test_body_row_with_stale_section_does_not_inherit_explicit_actions() -> None:
    previous = {
        "structure_unit_id": "parent",
        "unit_raw_text": "החלטה: מינוי נציגי עירייה לתאגידים ואיגודים",
        "source_window_id": "p17_w18",
        "section_id": "section-a",
    }
    current = {
        "structure_unit_id": "child",
        "unit_raw_text": ". נאומים מהמקום215 :מר שפירא – היו\"ר .אנחנו עוברים לנאומים מהמקום",
        "source_window_id": "p17_w19",
        "section_id": "section-a",
        "structural_role": "body",
        "explicit_actions": ["מינוי נציגי עירייה לתאגידים ואיגודים", "דיון בנאומים מהמקום"],
    }
    assignments = {
        "parent": {"structure_unit_id": "parent", "root_topic_id": "root_administration", "topic_subject_he": "מינוי נציגי עירייה לתאגידים ואיגודים", "topic_node_status": "active"},
        "child": {"structure_unit_id": "child", "root_topic_id": "root_administration", "topic_subject_he": None, "topic_node_status": "candidate"},
    }

    context = step4._select_inherited_topic_context(
        row=assignments["child"],
        item=current,
        items=[previous, current],
        assignment_by_id=assignments,
        emitted_by_id={},
        order_by_id={"parent": 0, "child": 1},
    )

    assert context is None


def test_speaker_dialogue_with_parent_agenda_stays_non_topic() -> None:
    previous_item = {
        "structure_unit_id": "parent",
        "semantic_unit_id": "parent",
        "structural_role": "outline_item",
        "unit_raw_text": "החלטה: מינוי נציגי עירייה לתאגידים ואיגודים",
        "document_context": {"packet_role": "protocol"},
    }
    previous_row = {
        "structure_unit_id": "parent",
        "packet_role": "protocol",
        "row_type": "topic_item",
        "root_topic_id": "root_administration",
        "topic_subject_he": "מינוי נציגי עירייה לתאגידים ואיגודים",
        "topic_node_status": "active",
        "is_topic_bearing": True,
    }
    item = {
        "structure_unit_id": "child",
        "semantic_unit_id": "child",
        "structural_role": "body",
        "row_type": "fragment",
        "unit_raw_text": ". נאומים מהמקום215 :מר שפירא – היו\"ר .אנחנו עוברים לנאומים מהמקום",
        "raw_text": ". נאומים מהמקום215 :מר שפירא – היו\"ר .אנחנו עוברים לנאומים מהמקום",
        "parent_agenda_unit_id": "parent",
        "non_topic_reason": "speaker_dialogue_fragment",
        "document_context": {"packet_role": "protocol"},
    }
    row = step4._non_topic_assignment(item, reason="speaker_dialogue_fragment")

    updated = step4._apply_topic_arbitration(assignments=[previous_row, row], items=[previous_item, item], enable_govmap_geo=False)[1]

    assert updated["is_topic_bearing"] is False
    assert updated["row_type"] == "fragment"
    assert updated["topic_arbitration"] == {"decision": "evidence_only", "reason": "speaker_dialogue_fragment"}


def test_allocation_repair_keeps_public_use_subject() -> None:
    raw = "ביטול החלטת ועדת הקצאות עבור הקמה של בית כנסת -ומרכז רוחני של עמותת הדור הרביעי רובע יג"
    item = {
        "topic_headline_he": raw,
        "topic_identification_context": raw,
        "raw_text": raw,
    }

    repaired, reason = step4._repair_rejected_topic_subject(
        raw_subject=raw,
        item=item,
        root_label="הקצאות ושימושים",
        quote=raw,
    )

    assert repaired == "ביטול החלטת ועדת הקצאות עבור בית כנסת ומרכז רוחני"
    assert reason is not None


def test_model_error_fallback_can_be_active_with_strong_deterministic_policy() -> None:
    item = {
        "structure_unit_id": "u_model_error_fallback_strong",
        "semantic_unit_id": "u_model_error_fallback_strong",
        "document_context": {"packet_role": "protocol"},
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "דיון בנושא תקציב הגיל הרך",
        "topic_identification_context": "תקציב הגיל הרך",
        "topic_headline_he": "תקציב הגיל הרך",
        "topic_subject_he": "תקציב הגיל הרך",
        "deterministic_topic_decision": {
            "action": "choose_existing_topic",
            "needs_dicta": False,
            "root_topic_id": "root_budget_finance",
            "policy_id": "budget_line_or_reserve",
            "confidence": 0.95,
        },
        "root_topic_candidates": [{"root_topic_id": "root_budget_finance", "score": 0.95, "policy_id": "budget_line_or_reserve"}],
        "candidate_child_topics": [],
    }

    assignment = step4._fallback_assignment(item, reason="model_error")

    assert assignment["topic_node_status"] == "active"
    assert assignment["topic_reject_reason"] is None
    assert "candidate_review:model_error" not in assignment["topic_assignment_route"]


def test_model_error_fallback_can_be_active_with_strong_child_decision_without_policy() -> None:
    item = {
        "structure_unit_id": "u_model_error_child_decision",
        "semantic_unit_id": "u_model_error_child_decision",
        "document_context": {"packet_role": "protocol"},
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "דו\"ח חובה על נסיעות בתפקיד לחו\"ל מטעם הרשות",
        "topic_identification_context": "דו\"ח חובה על נסיעות בתפקיד לחו\"ל מטעם הרשות",
        "topic_headline_he": "דו\"ח חובה על נסיעות בתפקיד לחו\"ל מטעם הרשות",
        "topic_subject_he": "נסיעה בתפקיד לחו\"ל",
        "deterministic_topic_decision": {
            "action": "choose_existing_topic",
            "needs_dicta": False,
            "root_topic_id": "root_travel_approvals",
            "child_choice_id": "child_travel",
            "confidence": 0.9,
            "matched_terms": ["נסיעה בתפקיד לחו\"ל"],
        },
        "root_topic_candidates": [{"root_topic_id": "root_travel_approvals", "score": 0.9}],
        "candidate_child_topics": [],
    }

    assignment = step4._fallback_assignment(item, reason="contextual_model_error")

    assert assignment["topic_node_status"] == "active"
    assert assignment["topic_reject_reason"] is None
    assert "candidate_review:contextual_model_error" not in assignment["topic_assignment_route"]


def test_contextual_incomplete_schema_high_confidence_root_can_be_active() -> None:
    item = {
        "structure_unit_id": "u_contextual_incomplete_schema_strong",
        "semantic_unit_id": "u_contextual_incomplete_schema_strong",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "body",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "ההסתייגות היא בנושא תכנית שכונה כעיר, קיצוץ מתקציב התכנית לצמצום פערים",
        "topic_identification_context": "ההסתייגות היא בנושא תכנית שכונה כעיר, קיצוץ מתקציב התכנית לצמצום פערים",
        "topic_headline_he": "הסתייגות בנושא תכנית שכונה כעיר",
        "topic_subject_he": "תכנית שכונה כעיר",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_planning_building", "confidence": 0.84},
        "root_topic_candidates": [{"root_topic_id": "root_planning_building", "score": 0.84}],
        "candidate_child_topics": [],
    }
    parsed = {
        "contextual_mode": "dicta_contextual",
        "contextual_schema_valid": False,
        "root_topic_id": "root_budget_finance",
        "is_topic_bearing": True,
        "topic_subject_he": "תכנית שכונה כעיר",
        "clean_subject_he": "תכנית שכונה כעיר",
        "primary_action_he": "קיצוץ תקציב",
        "topic_supporting_quote_he": "ההסתייגות היא בנושא תכנית שכונה כעיר",
        "confidence": 0.9,
        "rationale_he": "הנושא הוא הסתייגות תקציבית.",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_budget_finance"
    assert assignment["topic_subject_he"] == "קיצוץ תקציב תכנית שכונה כעיר"
    assert assignment["topic_node_status"] == "active"
    assert assignment["topic_reject_reason"] is None
    assert "dicta_contextual_root_recovered:dicta_contextual_incomplete_schema_root" in assignment["topic_assignment_route"]


def test_contextual_missing_confidence_can_be_active_with_strong_child_evidence() -> None:
    item = {
        "structure_unit_id": "u_contextual_missing_confidence_child",
        "semantic_unit_id": "u_contextual_missing_confidence_child",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "body",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "חילופי גברי בוועדות ובדירקטוריונים",
        "topic_identification_context": "חילופי גברי בוועדות ובדירקטוריונים",
        "topic_headline_he": "חילופי גברי בוועדות ובדירקטוריונים",
        "topic_subject_he": "חילופי גברי בוועדות ובדירקטוריונים",
        "deterministic_topic_decision": {
            "action": "needs_judge",
            "needs_dicta": True,
            "reason": "ambiguous_candidates",
            "root_topic_id": "root_administration",
            "child_choice_id": "child_replacement",
            "confidence": 0.94,
            "matched_terms": ["חילופי", "גברי"],
        },
        "root_topic_candidates": [{"root_topic_id": "root_administration", "score": 0.94}],
        "candidate_child_topics": [
            {
                "candidate_child_id": "child_replacement",
                "root_topic_id": "root_administration",
                "root_label_he": "מנהל עירוני ומינויים",
                "label_he": "חילופי גברי",
                "evidence_source": "existing_tree",
                "evidence_quote_he": "חילופי גברי בוועדות ובדירקטוריונים",
                "confidence_hint": 0.94,
                "aliases_he": [],
            }
        ],
    }
    parsed = {
        "contextual_mode": "dicta_contextual",
        "contextual_schema_valid": True,
        "root_topic_id": "root_administration",
        "is_topic_bearing": True,
        "topic_subject_he": "חילופי גברי בוועדות ובדירקטוריונים",
        "clean_subject_he": "חילופי גברי בוועדות ובדירקטוריונים",
        "topic_supporting_quote_he": "חילופי גברי בוועדות ובדירקטוריונים",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_administration"
    assert assignment["child_label_he"] == "חילופי גברי"
    assert assignment["topic_node_status"] == "active"
    assert assignment["topic_reject_reason"] is None
    assert "dicta_contextual_root_recovered:dicta_contextual_missing_confidence_root" in assignment["topic_assignment_route"]


def test_contextual_missing_confidence_is_not_authoritative() -> None:
    item = {
        "structure_unit_id": "u_contextual_untrusted",
        "semantic_unit_id": "u_contextual_untrusted",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "שאילתא בנושא פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_identification_context": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_headline_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_subject_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_mayor_updates"},
        "root_topic_candidates": [{"root_topic_id": "root_mayor_updates", "score": 0.9}],
        "candidate_child_topics": [],
    }
    parsed = {
        "contextual_mode": "dicta_contextual",
        "contextual_schema_valid": False,
        "root_topic_id": "root_mayor_updates",
        "is_topic_bearing": True,
        "topic_subject_he": "נסיעה בתפקיד לחו\"ל",
        "clean_subject_he": "נסיעה בתפקיד לחו\"ל",
        "topic_supporting_quote_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_travel_approvals"
    assert assignment["topic_node_status"] == "candidate"
    assert assignment["dicta_confidence"] is None


def test_contextual_high_confidence_schema_valid_root_is_authoritative() -> None:
    item = {
        "structure_unit_id": "u_contextual_trusted",
        "semantic_unit_id": "u_contextual_trusted",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "שאילתא בנושא פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_identification_context": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_headline_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "topic_subject_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_mayor_updates"},
        "root_topic_candidates": [{"root_topic_id": "root_mayor_updates", "score": 0.9}],
        "candidate_child_topics": [],
    }
    parsed = {
        "contextual_mode": "dicta_contextual",
        "contextual_schema_valid": True,
        "root_topic_id": "root_travel_approvals",
        "is_topic_bearing": True,
        "topic_subject_he": "נסיעה בתפקיד לחו\"ל",
        "clean_subject_he": "נסיעה בתפקיד לחו\"ל",
        "topic_supporting_quote_he": "פרטי יציאה של סגן ראש העיר לחו\"ל",
        "confidence": 0.86,
        "rationale_he": "הפעולה המרכזית היא נסיעה בתפקיד לחו\"ל ולכן שורש אישורי נסיעות מתאים יותר מעדכוני ראש העיר.",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_travel_approvals"
    assert assignment["topic_node_status"] == "active"
    assert assignment["root_adjudication_decision"] == "dicta_contextual_root"


def test_contextual_non_indexable_conflict_becomes_review_candidate() -> None:
    item = {
        "structure_unit_id": "u_contextual_status_conflict",
        "semantic_unit_id": "u_contextual_status_conflict",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "סעיף בנושא הסכם רשות שנדון בהקשר סטטוס בלבד",
        "topic_identification_context": "הסכם רשות לעמותה",
        "topic_headline_he": "הסכם רשות לעמותה",
        "topic_subject_he": "הסכם רשות לעמותה",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_agreements"},
        "root_topic_candidates": [{"root_topic_id": "root_agreements", "score": 0.9}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_status_conflict",
        "standalone_event": False,
        "agenda_disposition": "deferred",
        "event_status": "deferred",
        "indexability_status": "duplicate_reference",
        "is_standalone_topic": False,
        "agenda_status_he": "סטטוס בלבד",
        "agenda_carrier_he": None,
        "municipal_action_he": None,
        "semantic_subject_he": "הסכם רשות לעמותה",
        "primary_action_he": None,
        "service_domain_he": None,
        "clean_subject_he": "הסכם רשות לעמותה",
        "duplicate_of_unit_id": "u_original_agreement",
        "event_summary_he": "שורת סטטוס לגבי הסכם רשות לעמותה",
        "supporting_quote_he": "הסכם רשות לעמותה",
        "confidence": 0.9,
    }
    response = {
        "assignments": [
            {
                "structure_unit_id": "u_contextual_status_conflict",
                "agenda_disposition": "deferred",
                "event_status": "deferred",
                "indexability_status": "duplicate_reference",
                "root_topic_id": "root_agreements",
                "is_topic_bearing": True,
                "topic_subject_he": "הסכם רשות לעמותה",
                "clean_subject_he": "הסכם רשות לעמותה",
                "classification_basis": "primary_action",
                "duplicate_of_unit_id": "u_original_agreement",
                "topic_supporting_quote_he": "הסכם רשות לעמותה",
                "confidence": 0.91,
                "rationale_he": "בדיקה סינתטית: המודל סימן גם סטטוס לא יבוא וגם נושא פעיל.",
                "why_not_other_roots_he": "בדיקה",
                "competing_roots": [],
            }
        ]
    }

    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["is_topic_bearing"] is True
    assert assignment["topic_node_status"] == "candidate"
    assert assignment["dicta_contextual_event_status"] == "deferred"
    assert assignment["dicta_contextual_indexability_status"] == "duplicate_reference"
    assert assignment["dicta_contextual_consistency_warning"] == "topic_bearing_with_non_indexable_status:duplicate_reference"


def test_contextual_optional_audit_fields_do_not_block_authoritative_root() -> None:
    item = {
        "structure_unit_id": "u_contextual_optional_missing",
        "semantic_unit_id": "u_contextual_optional_missing",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "דיון בנושא תקציב הגיל הרך",
        "topic_identification_context": "תקציב הגיל הרך",
        "topic_headline_he": "תקציב הגיל הרך",
        "topic_subject_he": "תקציב הגיל הרך",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_budget_finance"},
        "root_topic_candidates": [{"root_topic_id": "root_budget_finance", "score": 0.9}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_optional_missing",
        "standalone_event": True,
        "agenda_disposition": "approved",
        "event_status": "approved",
        "indexability_status": "standalone_topic",
        "is_standalone_topic": True,
        "agenda_status_he": None,
        "agenda_carrier_he": None,
        "municipal_action_he": "אישור תקציב",
        "semantic_subject_he": "תקציב הגיל הרך",
        "primary_action_he": "אישור תקציב",
        "service_domain_he": "חינוך",
        "classification_basis": "primary_action",
        "clean_subject_he": "תקציב הגיל הרך",
        "event_summary_he": "דיון תקציבי בנושא הגיל הרך.",
        "supporting_quote_he": "תקציב הגיל הרך",
        "confidence": 0.95,
    }
    response = {
        "assignments": [
            {
                "structure_unit_id": "u_contextual_optional_missing",
                "root_topic_id": "root_budget_finance",
                "is_topic_bearing": True,
                "topic_subject_he": "תקציב הגיל הרך",
                "confidence": 0.95,
            }
        ]
    }

    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert parsed["contextual_schema_valid"] is True
    assert "rationale_he" in assignment["dicta_contextual_missing_optional_fields"]
    assert "competing_roots" in assignment["dicta_contextual_missing_optional_fields"]
    assert assignment["dicta_contextual_rationale_fallback"] is True
    assert assignment["topic_node_status"] == "active"
    assert assignment["root_topic_id"] == "root_budget_finance"


def test_contextual_deferred_standalone_topic_remains_importable() -> None:
    item = {
        "structure_unit_id": "u_contextual_deferred_budget",
        "semantic_unit_id": "u_contextual_deferred_budget",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "הסתייגות בעניין קיצוץ בתקציב המשפחתונים לנשים עובדות נדחתה",
        "topic_identification_context": "תקציב המשפחתונים לנשים עובדות",
        "topic_headline_he": "תקציב המשפחתונים לנשים עובדות",
        "topic_subject_he": "תקציב המשפחתונים לנשים עובדות",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_budget_finance"},
        "root_topic_candidates": [{"root_topic_id": "root_budget_finance", "score": 0.98}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_deferred_budget",
        "standalone_event": False,
        "agenda_disposition": "deferred",
        "event_status": "deferred",
        "indexability_status": "standalone_topic",
        "is_standalone_topic": False,
        "agenda_status_he": "נדחתה",
        "agenda_carrier_he": None,
        "municipal_action_he": None,
        "semantic_subject_he": "תקציב המשפחתונים לנשים עובדות",
        "primary_action_he": "קיצוץ תקציבי",
        "service_domain_he": None,
        "classification_basis": "primary_action",
        "clean_subject_he": "תקציב המשפחתונים לנשים עובדות",
        "event_summary_he": "הסתייגות תקציבית שנדחתה אך יש לה נושא תקציבי עצמאי.",
        "supporting_quote_he": "קיצוץ בתקציב המשפחתונים לנשים עובדות",
        "confidence": 0.96,
    }
    response = {
        "assignments": [
            {
                "structure_unit_id": "u_contextual_deferred_budget",
                "agenda_disposition": "deferred",
                "event_status": "deferred",
                "indexability_status": "standalone_topic",
                "root_topic_id": "root_budget_finance",
                "is_topic_bearing": False,
                "topic_subject_he": "תקציב המשפחתונים לנשים עובדות",
                "clean_subject_he": "תקציב המשפחתונים לנשים עובדות",
                "primary_action_he": "קיצוץ תקציבי",
                "service_domain_he": None,
                "classification_basis": "primary_action",
                "topic_supporting_quote_he": "קיצוץ בתקציב המשפחתונים לנשים עובדות",
                "confidence": 0.96,
                "rationale_he": "הדיספוזיציה היא דחייה, אך הנושא התקציבי עצמו עצמאי ובר סיווג.",
                "why_not_other_roots_he": "הבסיס הוא פעולה תקציבית ולא שירות ישיר.",
                "competing_roots": [],
            }
        ]
    }

    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["is_topic_bearing"] is True
    assert assignment["root_topic_id"] == "root_budget_finance"
    assert assignment["topic_node_status"] == "active"
    assert assignment["dicta_contextual_event_status"] == "deferred"
    assert assignment["dicta_contextual_indexability_status"] == "standalone_topic"
    assert assignment["dicta_contextual_consistency_warning"] is None


def test_contextual_unsupported_duplicate_reference_reverts_to_standalone_topic() -> None:
    item = {
        "structure_unit_id": "u_contextual_nonindexable_budget",
        "semantic_unit_id": "u_contextual_nonindexable_budget",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "הסתייגות בעניין קיצוץ בתקציב המשפחתונים לנשים עובדות",
        "topic_identification_context": "תקציב המשפחתונים לנשים עובדות",
        "topic_headline_he": "תקציב המשפחתונים לנשים עובדות",
        "topic_subject_he": "תקציב המשפחתונים לנשים עובדות",
        "topic_policy_matches": [{"policy_id": "budget_line_or_reserve", "root_topic_id": "root_budget_finance"}],
        "deterministic_topic_decision": {"needs_dicta": False, "reason": "strong_policy_match", "root_topic_id": "root_budget_finance"},
        "root_topic_candidates": [{"root_topic_id": "root_budget_finance", "score": 0.98, "policy_id": "budget_line_or_reserve"}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_nonindexable_budget",
        "standalone_event": False,
        "agenda_disposition": "discussed",
        "event_status": "discussed",
        "indexability_status": "duplicate_reference",
        "is_standalone_topic": False,
        "agenda_status_he": None,
        "agenda_carrier_he": None,
        "municipal_action_he": None,
        "semantic_subject_he": "תקציב המשפחתונים לנשים עובדות",
        "primary_action_he": None,
        "service_domain_he": None,
        "classification_basis": "service_domain",
        "clean_subject_he": "תקציב המשפחתונים לנשים עובדות",
        "event_summary_he": "הסתייגות תקציבית עם נושא עצמאי.",
        "supporting_quote_he": "בתקציב המשפחתונים לנשים עובדות",
        "confidence": 0.96,
    }
    response = {
        "assignments": [
            {
                "structure_unit_id": "u_contextual_nonindexable_budget",
                "agenda_disposition": "discussed",
                "event_status": "discussed",
                "indexability_status": "duplicate_reference",
                "root_topic_id": "root_budget_finance",
                "is_topic_bearing": False,
                "topic_subject_he": "תקציב וכספים",
                "clean_subject_he": "תקציב וכספים",
                "primary_action_he": None,
                "service_domain_he": None,
                "classification_basis": "unclear",
                "policy_id": "budget_line_or_reserve",
                "topic_supporting_quote_he": "בתקציב המשפחתונים לנשים עובדות",
                "confidence": 0.98,
                "rationale_he": "המודל בחר שורש תקציבי אבל סימן בטעות שהשורה אינה אינדקסבילית.",
                "why_not_other_roots_he": "פעולה תקציבית.",
                "competing_roots": [],
            }
        ]
    }

    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["is_topic_bearing"] is True
    assert assignment["root_topic_id"] == "root_budget_finance"
    assert assignment["topic_subject_he"] == "תקציב המשפחתונים לנשים עובדות"
    assert assignment["topic_node_status"] == "active"
    assert assignment["dicta_contextual_indexability_status"] == "standalone_topic"
    assert assignment["dicta_contextual_original_indexability_status"] == "duplicate_reference"
    assert assignment["dicta_contextual_indexability_override_reason"] == "unsupported_duplicate_reference"
    assert assignment["dicta_contextual_consistency_warning"] is None


def test_contextual_action_domain_conflict_adds_competing_root_and_clear_rationale() -> None:
    item = {
        "structure_unit_id": "u_contextual_action_domain_conflict",
        "semantic_unit_id": "u_contextual_action_domain_conflict",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "קיצוץ משמעותי בתקציב גני ילדים",
        "topic_identification_context": "גני ילדים",
        "topic_headline_he": "גני ילדים",
        "topic_subject_he": "גני ילדים",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_education"},
        "root_topic_candidates": [{"root_topic_id": "root_education", "score": 0.9}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_action_domain_conflict",
        "standalone_event": True,
        "agenda_disposition": "discussed",
        "event_status": "discussed",
        "indexability_status": "standalone_topic",
        "is_standalone_topic": True,
        "agenda_status_he": None,
        "agenda_carrier_he": None,
        "municipal_action_he": "קיצוץ תקציבי",
        "semantic_subject_he": "גני ילדים",
        "primary_action_he": "קיצוץ תקציבי",
        "service_domain_he": "חינוך",
        "classification_basis": "primary_action",
        "clean_subject_he": "גני ילדים",
        "event_summary_he": "קיצוץ תקציבי בתחום גני הילדים.",
        "supporting_quote_he": "קיצוץ משמעותי בתקציב גני ילדים",
        "confidence": 0.97,
    }
    response = {
        "assignments": [
            {
                "structure_unit_id": "u_contextual_action_domain_conflict",
                "agenda_disposition": "discussed",
                "event_status": "discussed",
                "indexability_status": "standalone_topic",
                "root_topic_id": "root_education",
                "is_topic_bearing": True,
                "topic_subject_he": "גני ילדים",
                "clean_subject_he": "גני ילדים",
                "primary_action_he": "קיצוץ תקציבי",
                "service_domain_he": "חינוך",
                "classification_basis": "primary_action",
                "topic_supporting_quote_he": "קיצוץ משמעותי בתקציב גני ילדים",
                "confidence": 0.97,
                "rationale_he": "המודל בחר חינוך לפי תחום השירות.",
                "why_not_other_roots_he": "בדיקה",
                "competing_roots": [],
            }
        ]
    }

    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_budget_finance"
    assert any(row["root_topic_id"] == "root_education" for row in assignment["dicta_contextual_competing_roots"])
    assert "שורש המודל המקורי היה חינוך" in assignment["rationale_he"]
    assert "שכבת האימות בחרה בשורש תקציב וכספים" in assignment["rationale_he"]


def test_contextual_exit_publication_is_not_travel_action() -> None:
    assert step4._contextual_action_root_from_text("יציאה לפרסום לקבלת המלצות למועמדים") is None
    assert step4._contextual_action_root_from_text("פרטי יציאת ראש העיר לחו\"ל מטעם העירייה") == "root_travel_approvals"


def test_contextual_committee_domain_beats_generic_governance_carrier() -> None:
    assert step4._contextual_action_root_from_text("פרוטוקול מישיבת ועדת רווחה") == "root_welfare_social"
    assert step4._contextual_action_root_from_text("פרוטוקול מישיבת הועדה למאבק בנגע הסמים המסוכנים") == "root_security_enforcement"
    assert step4._contextual_action_root_from_text("עדכון תבחיני ספורט") == "root_supports"
    assert step4._contextual_action_root_from_text("מינויים ושינויים בוועדת מכרזים") == "root_administration"


def test_generic_committee_protocol_carrier_is_demoted_but_domain_committee_survives() -> None:
    generic = "פרוטוקול הוועדה המקצועית מספר"
    domain = "פרוטוקול מישיבת ועדת רווחה"

    assert step4._looks_like_procedural_carrier_heading(generic) is True
    assert step4._non_topic_protocol_reason(headline=generic, raw_text=generic, structural_role="outline_item", packet_role="protocol") == "procedural_carrier_heading"
    assert step4._looks_like_procedural_carrier_heading(domain) is False
    assert step4._non_topic_protocol_reason(headline=domain, raw_text=domain, structural_role="outline_item", packet_role="protocol") is None


def test_committee_protocol_date_heading_without_action_is_non_topic() -> None:
    examples = [
        "פרוטוקול ועדת פינויים.26 מיום",
        "ועדת פינויים.26 מיום",
        "פרוטוקול ועדת רווחה מס.26 מיום",
    ]

    for text in examples:
        assert step4._non_topic_protocol_reason(
            headline=text,
            raw_text=text,
            structural_role="outline_item",
            packet_role="protocol",
        ) == "committee_protocol_date_heading"


def test_committee_protocol_approval_stays_topic() -> None:
    text = "אישור פרוטוקול ועדת פינויים מס.26 מיום"

    assert step4._non_topic_protocol_reason(
        headline=text,
        raw_text=text,
        structural_role="outline_item",
        packet_role="protocol",
    ) is None


def test_contextual_removed_agenda_topic_becomes_non_blocking_candidate() -> None:
    item = {
        "structure_unit_id": "u_contextual_removed_agenda",
        "semantic_unit_id": "u_contextual_removed_agenda",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "סעיפי הסכמי רשות ופיתוח הוסרו מסדר היום",
        "topic_identification_context": "סעיפי הסכמי רשות ופיתוח",
        "topic_headline_he": "סעיפי הסכמי רשות ופיתוח",
        "topic_subject_he": "סעיפי הסכמי רשות ופיתוח",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_agreements"},
        "root_topic_candidates": [{"root_topic_id": "root_agreements", "score": 0.9}],
        "candidate_child_topics": [],
    }
    parsed = {
        "contextual_mode": "dicta_contextual",
        "contextual_schema_valid": True,
        "contextual_agenda_disposition": "removed",
        "contextual_event_status": "removed",
        "contextual_indexability_status": "standalone_topic",
        "root_topic_id": "root_agreements",
        "is_topic_bearing": True,
        "topic_subject_he": "סעיפי הסכמי רשות ופיתוח",
        "clean_subject_he": "סעיפי הסכמי רשות ופיתוח",
        "classification_basis": "primary_action",
        "topic_supporting_quote_he": "סעיפי הסכמי רשות ופיתוח הוסרו מסדר היום",
        "confidence": 0.93,
        "rationale_he": "הנושא עצמו הוא הסכמי רשות ופיתוח, אך הוא סומן כמוסר מסדר היום.",
        "why_not_other_roots_he": "הפעולה היא הסכמית.",
        "competing_roots": [],
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_agreements"
    assert assignment["topic_node_status"] == "candidate"
    assert assignment["topic_reject_reason"] == "non_blocking_topic_review:dicta_contextual_removed_agenda_topic"
    assert "contextual_agenda_review" in assignment["topic_assignment_route"]


def test_contextual_approved_non_standalone_event_can_be_active() -> None:
    item = {
        "structure_unit_id": "u_contextual_approved_fragment",
        "semantic_unit_id": "u_contextual_approved_fragment",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "פרוטוקול מישיבת ועדת שמות אושר",
        "topic_identification_context": "פרוטוקול מישיבת ועדת שמות",
        "topic_headline_he": "פרוטוקול מישיבת ועדת שמות",
        "topic_subject_he": "ועדת שמות",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "ambiguous_candidates", "root_topic_id": "root_administration"},
        "root_topic_candidates": [{"root_topic_id": "root_administration", "score": 0.9}],
        "candidate_child_topics": [],
    }
    normalized_event = {
        "structure_unit_id": "u_contextual_approved_fragment",
        "standalone_event": False,
        "agenda_disposition": "approved",
        "event_status": "approved",
        "indexability_status": "standalone_topic",
        "is_standalone_topic": False,
        "agenda_status_he": "אושר",
        "agenda_carrier_he": "פרוטוקול ועדה",
        "municipal_action_he": None,
        "semantic_subject_he": "ועדת שמות",
        "primary_action_he": None,
        "service_domain_he": None,
        "classification_basis": "governance_status",
        "clean_subject_he": "ועדת שמות",
        "event_summary_he": "אישור פרוטוקול ועדת שמות",
        "supporting_quote_he": "פרוטוקול מישיבת ועדת שמות אושר",
        "confidence": 0.9,
    }
    response = {
        "assignments": [
            {
                "structure_unit_id": "u_contextual_approved_fragment",
                "agenda_disposition": "approved",
                "event_status": "approved",
                "indexability_status": "standalone_topic",
                "root_topic_id": "root_administration",
                "is_topic_bearing": True,
                "topic_subject_he": "ועדת שמות",
                "clean_subject_he": "ועדת שמות",
                "classification_basis": "governance_status",
                "topic_supporting_quote_he": "פרוטוקול מישיבת ועדת שמות אושר",
                "confidence": 0.91,
                "rationale_he": "אישור פרוטוקול ועדת שמות הוא פעולה מועצתית בתחום מנהל עירוני.",
                "why_not_other_roots_he": "אין שורש מתאים יותר מהתחום המנהלי.",
                "competing_roots": [],
            }
        ]
    }

    parsed = step4._contextual_assignment_from_response(response=response, item=item, normalized_event=normalized_event)
    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["is_topic_bearing"] is True
    assert assignment["topic_node_status"] == "active"
    assert assignment["dicta_contextual_event_status"] == "approved"
    assert assignment["dicta_contextual_consistency_warning"] is None


def test_contextual_low_confidence_valid_root_is_preserved_as_candidate() -> None:
    item = {
        "structure_unit_id": "u_contextual_low_conf_root",
        "semantic_unit_id": "u_contextual_low_conf_root",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "פרוטוקול מישיבת ועדת שמות מס 4/21 מיום 26.12.21",
        "topic_identification_context": "פרוטוקול מישיבת ועדת שמות",
        "topic_headline_he": "פרוטוקול מישיבת ועדת שמות",
        "topic_subject_he": "פרוטוקול מישיבת ועדת שמות",
        "deterministic_topic_decision": {"needs_dicta": True, "reason": "weak_candidate", "root_topic_id": "root_administration"},
        "root_topic_candidates": [{"root_topic_id": "root_administration", "score": 0.45}],
        "candidate_child_topics": [],
    }
    parsed = {
        "contextual_mode": "dicta_contextual",
        "contextual_schema_valid": True,
        "contextual_event_status": "approved",
        "contextual_indexability_status": "standalone_topic",
        "root_topic_id": "root_administration",
        "is_topic_bearing": True,
        "topic_subject_he": "ועדת שמות",
        "clean_subject_he": "ועדת שמות",
        "classification_basis": "governance_status",
        "topic_supporting_quote_he": "פרוטוקול מישיבת ועדת שמות",
        "confidence": 0.5,
        "rationale_he": "הנושא הוא פרוטוקול של ועדת שמות בתחום מנהל עירוני.",
        "why_not_other_roots_he": "אין בסיס לתמיכות או לשורש אחר.",
        "competing_roots": [{"root_topic_id": "root_supports", "confidence": 0.42, "reason_he": "שורש מתחרה חלש בלבד."}],
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_administration"
    assert assignment["topic_node_status"] == "candidate"
    assert assignment["topic_reject_reason"] == "non_blocking_topic_review:dicta_contextual_low_confidence_root"
    assert assignment["dicta_contextual_competing_roots"][0]["root_topic_id"] == "root_supports"


def test_contextual_rejected_generic_committee_label_becomes_non_topic() -> None:
    item = {
        "structure_unit_id": "u_contextual_bad_label_valid_root",
        "semantic_unit_id": "u_contextual_bad_label_valid_root",
        "document_context": {"packet_role": "protocol"},
        "dicta_mode": "dicta_contextual",
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "raw_text": "פרוטוקול מישיבת ועדה מקצועית מס 9/21 מיום",
        "topic_identification_context": "פרוטוקול מישיבת ועדה מקצועית מס 9/21 מיום",
        "topic_headline_he": "פרוטוקול מישיבת ועדה מקצועית מס 9/21 מיום",
        "topic_subject_he": "פרוטוקול מישיבת ועדה מקצועית מס 9/21 מיום",
        "deterministic_topic_decision": {"needs_dicta": False, "reason": "strong_policy_match", "root_topic_id": "root_supports"},
        "root_topic_candidates": [{"root_topic_id": "root_supports", "score": 0.98}],
        "candidate_child_topics": [],
    }
    parsed = {
        "contextual_mode": "dicta_contextual",
        "contextual_schema_valid": True,
        "contextual_event_status": "approved",
        "contextual_indexability_status": "standalone_topic",
        "root_topic_id": "root_supports",
        "is_topic_bearing": True,
        "topic_subject_he": "פרוטוקול מישיבת ועדה מקצועית מס 9 21 מיום",
        "clean_subject_he": "פרוטוקול מישיבת ועדה מקצועית מס 9 21 מיום",
        "classification_basis": "service_domain",
        "topic_supporting_quote_he": "פרוטוקול מישיבת ועדה מקצועית מס 9/21 מיום",
        "confidence": 0.98,
        "rationale_he": "הוועדה עוסקת בתמיכות ולכן השורש תקף גם אם הכותרת דורשת ניקוי ידני.",
        "why_not_other_roots_he": "אין שורש מתאים יותר.",
        "competing_roots": [],
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)
    normalized = step4._normalize_protocol_non_topic_assignments([assignment])[0]

    assert normalized["is_topic_bearing"] is False
    assert normalized["row_type"] == "fragment"
    assert normalized["root_topic_id"] == "root_agenda_queries"
    assert normalized["topic_node_status"] == "active"
    assert normalized["topic_subject_he"] is None
    assert normalized["topic_reject_reason"] is None
    assert "post_non_topic:procedural_carrier_heading" in normalized["topic_assignment_route"]


def test_rejected_procedural_subject_can_be_demoted_after_assignment() -> None:
    row = {
        "topic_reject_reason": "non_blocking_topic_review:topic_subject_rejected:procedural_or_dialogue_label",
        "topic_subject_he": None,
        "topic_headline_he": "תיקון טעות קולמוס בפרוטוקול מועצה",
        "topic_identification_context": "תיקון טעות קולמוס בפרוטוקול מועצה",
        "topic_supporting_quote_he": "תיקון טעות קולמוס בפרוטוקול מועצה",
        "structural_role": "outline_item",
        "packet_role": "protocol",
        "topic_node_status": "candidate",
        "root_topic_id": "root_administration",
    }

    assert step4._post_assignment_non_topic_reason(row) == "rejected_procedural_subject"


def test_rejected_supported_agreement_subject_is_not_demoted_to_fragment() -> None:
    row = {
        "topic_reject_reason": "non_blocking_topic_review:topic_subject_rejected:unresolved_noisy_label",
        "topic_subject_he": None,
        "raw_topic_subject_he": "דיון חוזר לאישור הסכם בין העירייה לבין עמותת משכנות שמעון בגוש חלקה",
        "topic_headline_he": "דיון חוזר לאישור הסכם בין העירייה לבין עמותת משכנות שמעון בגוש חלקה",
        "topic_identification_context": "דיון חוזר לאישור הסכם בין העירייה לבין עמותת משכנות שמעון בגוש חלקה",
        "topic_supporting_quote_he": "לאשר הסכם בין העירייה לבין עמותת משכנות שמעון",
        "structural_role": "body",
        "packet_role": "protocol",
        "topic_node_status": "candidate",
        "root_topic_id": "root_agreements",
    }

    assert step4._post_assignment_non_topic_reason(row) is None


def test_rejected_budget_tbr_subject_gets_compact_repair_candidate() -> None:
    repaired, reason = step4._repair_rejected_topic_subject(
        raw_subject="ביטול תב\"ר פארק בין מגדלי היוקרה החדשים בכיכר המדינה",
        item={
            "topic_subject_he": "ביטול תב\"ר פארק בין מגדלי היוקרה החדשים בכיכר המדינה",
            "topic_headline_he": "ביטול תב\"ר פארק בין מגדלי היוקרה החדשים בכיכר המדינה",
            "topic_identification_context": "ביטול תב\"ר פארק בין מגדלי היוקרה החדשים בכיכר המדינה",
            "raw_text": "ביטול תב\"ר פארק בין מגדלי היוקרה החדשים בכיכר המדינה",
        },
        root_label="תקציב וכספים",
        quote="ביטול תב\"ר פארק בין מגדלי היוקרה החדשים בכיכר המדינה",
    )

    assert repaired == "ביטול תב\"ר פארק בכיכר המדינה"
    assert reason == "subject_repaired:alternate_source"


def test_rejected_procurement_subject_uses_action_span_repair() -> None:
    raw = "הסבר כללי. ניהול משא ומתן עם ספקים פוטנציאליים לצורך התקשרות ללא מכרז, לאור אי קבלת הצעות"
    repaired, reason = step4._repair_rejected_topic_subject(
        raw_subject=raw,
        item={
            "topic_subject_he": raw,
            "topic_headline_he": raw,
            "topic_identification_context": raw,
            "raw_text": raw,
        },
        root_label="הסכמים והתקשרויות",
        quote=raw,
    )

    assert repaired == "ניהול משא ומתן להתקשרות ללא מכרז"
    assert reason == "subject_repaired:semantic_canonicalized"


def test_rejected_agreement_subject_uses_generic_party_type_repair() -> None:
    raw = "דיון חוזר לאישור הסכם בין העירייה לבין עמותת משכנות שמעון וברית מרים בגוש חלקה ברחוב ברקת מחליטים פה אחד"
    repaired, reason = step4._repair_rejected_topic_subject(
        raw_subject=raw,
        item={
            "topic_subject_he": raw,
            "topic_headline_he": raw,
            "topic_identification_context": raw,
            "raw_text": raw,
        },
        root_label="הסכמים והתקשרויות",
        quote=raw,
    )

    assert repaired == "הסכם עם עמותה"
    assert reason in {"subject_repaired:semantic_canonicalized", "subject_repaired:alternate_source"}


def test_rejected_public_building_subject_uses_generic_facility_repair() -> None:
    raw = "המתנ\"ס שתושבי רובע ט\"ו מחכים לו כל כך הרבה שנים"
    repaired, reason = step4._repair_rejected_topic_subject(
        raw_subject=raw,
        item={
            "topic_subject_he": raw,
            "topic_headline_he": raw,
            "topic_identification_context": raw,
            "raw_text": raw,
        },
        root_label="תכנון ובנייה",
        quote=raw,
    )

    assert repaired == "מתנ\"ס רובע ט\"ו"
    assert reason == "subject_repaired:alternate_source"


def test_rejected_planning_subject_uses_reusable_renewal_label() -> None:
    raw = "פרוייקט פינוי בינוי ברחוב הרב מימון, תכנית 0778050, רובע ב, אשדוד"
    repaired, reason = step4._repair_rejected_topic_subject(
        raw_subject=raw,
        item={
            "topic_subject_he": raw,
            "topic_headline_he": raw,
            "topic_identification_context": raw,
            "raw_text": raw,
        },
        root_label="תכנון ובנייה",
        quote=raw,
    )

    assert repaired == "פינוי בינוי הרב מימון"
    assert reason == "subject_repaired:semantic_canonicalized"


def test_query_carrier_extracts_semantic_subject_from_dangerous_building_query() -> None:
    contract = step4._topic_contract_from_headline(
        "שאילתה בנושא בדיקת מבנים מסוכנים ברחבי העיר",
        structural_role="outline_item",
        packet_role="protocol",
    )

    assert contract["is_topic_bearing"] is True
    assert contract["topic_subject_he"] == "בדיקת מבנים מסוכנים ברחבי העיר"


def test_compound_child_arbitration_preserves_action_scoped_subject(monkeypatch) -> None:
    monkeypatch.setattr(
        step4,
        "_best_candidate_for_subject",
        lambda **_: {"root_topic_id": "root_planning_building", "child_label_he": "מבנים מסוכנים", "score": 0.95},
    )

    assignment = step4._compound_child_subject_arbitration_assignment(
        row={
            "is_topic_bearing": True,
            "topic_subject_he": "בדיקת מבנים מסוכנים",
            "root_topic_id": "root_welfare_social",
        },
        item={
            "structure_unit_id": "u1",
            "semantic_unit_id": "sem-u1",
            "unit_raw_text": "שאילתה בנושא בדיקת מבנים מסוכנים ברחבי העיר",
            "raw_text": "שאילתה בנושא בדיקת מבנים מסוכנים ברחבי העיר",
            "topic_identification_context": "בדיקת מבנים מסוכנים ברחבי העיר",
            "topic_headline_he": "בדיקת מבנים מסוכנים ברחבי העיר",
            "row_type": "topic_item",
        },
    )

    assert assignment is not None
    assert assignment["root_topic_id"] == "root_planning_building"
    assert assignment["child_label_he"] == "מבנים מסוכנים"
    assert assignment["topic_subject_he"] == "בדיקת מבנים מסוכנים"


def test_compound_child_arbitration_does_not_replace_clear_non_geo_subject_with_geo(monkeypatch) -> None:
    monkeypatch.setattr(
        step4,
        "_best_candidate_for_subject",
        lambda **_: {"root_topic_id": "root_geo", "child_label_he": "שכונות ואזורים", "score": 0.94},
    )

    assignment = step4._compound_child_subject_arbitration_assignment(
        row={
            "is_topic_bearing": True,
            "topic_subject_he": "הסכם עם עמותה",
            "root_topic_id": "root_agreements",
        },
        item={
            "structure_unit_id": "u1",
            "semantic_unit_id": "sem-u1",
            "unit_raw_text": "דיון חוזר לאישור הסכם בין עיריית אשדוד לבין עמותת משכנות שמעון בגוש 2002 חלקה 158, רובע יז",
            "raw_text": "דיון חוזר לאישור הסכם בין עיריית אשדוד לבין עמותת משכנות שמעון בגוש 2002 חלקה 158, רובע יז",
            "topic_identification_context": "אישור הסכם בין העירייה לבין עמותה ברובע יז",
            "topic_headline_he": "אישור הסכם בין העירייה לבין עמותה ברובע יז",
            "row_type": "topic_item",
        },
    )

    assert assignment is None


def test_v3_hint_keeps_entailed_matter_when_only_outcome_is_not_entailed() -> None:
    hint = step4._topic_subject_v3_hint_from_row(
        {
            "quality_status": "accepted",
            "row_role": "action_anchor",
            "event_role": "primary",
            "artifact_id": "json_900000_110_s0043_01_a10162c4edfe",
            "model_prediction": {
                "action_type_he": "דיון",
                "matter_he": "אישור הסכם בין העירייה לבין עמותה",
                "matter_display_he": "אישור הסכם עם עמותה",
                "v3_evidence_entailment": {
                    "entailment_status": "not_entailed",
                    "field_assessments": {
                        "action_type_he": {"status": "entailed", "source_quote_he": "דיון חוזר לאישור הסכם"},
                        "matter_he": {"status": "entailed", "source_quote_he": "אישור הסכם בין העירייה לבין עמותה"},
                        "outcome": {"status": "not_entailed"},
                    },
                },
            },
        }
    )

    assert hint is not None
    assert hint["source_structure_unit_id"] == "s0043_01_a10162c4edfe"
    assert hint["matter_he"] == "אישור הסכם בין העירייה לבין עמותה"


def test_multifacet_ambiguous_subject_stays_candidate() -> None:
    row = {
        "packet_role": "protocol",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "root_topic_id": "root_culture_sport",
        "child_label_he": "הנחות לתושבים",
        "topic_subject_he": "תחזוקת מתקנים, עלויות שימוש והנחות לתושבים",
        "deterministic_topic_decision": {"reason": "ambiguous_candidates"},
        "root_topic_candidates": [
            {"root_topic_id": "root_culture_sport", "score": 0.82},
            {"root_topic_id": "root_budget_finance", "score": 0.81},
        ],
    }

    assert step4._post_assignment_candidate_review_reason(row) == "ambiguous_multifacet_topic"


def test_root_only_geo_supported_subject_stays_candidate_without_external_verification() -> None:
    row = {
        "packet_role": "protocol",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "root_topic_id": "root_mayor_updates",
        "child_label_he": None,
        "topic_subject_he": "כיכר מרכזית ודרך ראשית",
        "root_topic_candidates": [
            {"root_topic_id": "root_geo", "child_label_he": "כתובות ורחובות", "score": 0.91},
        ],
    }

    assert step4._post_assignment_candidate_review_reason(row) == "unverified_geo_location"


def test_v3_confirmed_geo_subject_stays_active_without_external_verification() -> None:
    row = {
        "structure_unit_id": "s0003_07_6a62f1711b20",
        "packet_role": "protocol",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "root_topic_id": "root_geo",
        "child_label_he": "כיכרות וצמתים",
        "topic_subject_he": "כיכר רמון בעיר ודרך מנחם בגין",
        "geo_resolution": {
            "source": "local_geo_pattern",
            "confidence": "medium",
            "child_label_he": "כיכרות וצמתים",
        },
    }
    item = {
        "structure_unit_id": "s0003_07_6a62f1711b20",
        "unit_raw_text": "שאילתה של ד\"ר לחמני בנושא כיכר רמון בעיר ודרך מנחם בגין",
        "topic_subject_v3_hints": [
            {
                "source": "topic_subject_v3",
                "source_structure_unit_id": "s0003_07_6a62f1711b20",
                "matter_he": "כיכר רמון בעיר ודרך מנחם בגין",
                "action_type_he": "שאילתה",
                "quality_status": "accepted",
                "entailment_status": "entailed",
            }
        ],
    }

    assert step4._post_assignment_candidate_review_reason(row, item=item) is None


def test_geo_supported_subject_with_clear_non_geo_root_stays_active() -> None:
    row = {
        "packet_role": "protocol",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "root_topic_id": "root_security_enforcement",
        "child_label_he": None,
        "topic_subject_he": "אלימות ופשע ברחובות העיר",
        "root_topic_candidates": [
            {"root_topic_id": "root_geo", "child_label_he": "כתובות ורחובות", "score": 0.91},
        ],
    }

    assert step4._post_assignment_candidate_review_reason(row) is None


def test_lease_agreement_subject_is_reusable_active_topic() -> None:
    row = {
        "packet_role": "protocol",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "root_topic_id": "root_agreements",
        "child_label_he": "הסכמי שימוש במתקנים ציבוריים",
        "topic_subject_he": "הסכם שכירות למתקן שידור",
    }

    assert step4._post_assignment_candidate_review_reason(row) is None


def test_support_criteria_subject_matches_existing_child_alias() -> None:
    assert step4._existing_child_label_for_subject(root_topic_id="root_supports", subject="תבחינים לתמיכות") == "תבחינים"


def test_electronic_notice_board_subject_reparents_from_location_to_infrastructure() -> None:
    item = {
        "structure_unit_id": "u1",
        "semantic_unit_id": "sem-u1",
        "source_window_id": "w1",
        "source_region_ids": [],
        "source_block_ids": [],
        "source_page": 1,
        "structural_role": "outline_item",
        "section_id": "s1",
        "section_number": "1",
        "skip_model_assignment": False,
        "row_type": "topic_item",
        "raw_text": "שאילתא בנושא העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
        "topic_headline_he": "העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
        "document_context": {"packet_role": "protocol"},
    }

    assignment = step4._assignment_payload(
        item=item,
        root_topic_id="root_transport_safety",
        root_label=step4.root_label_for_id("root_transport_safety") or "",
        child_label=None,
        raw_child_label=None,
        status="active",
        reject_reason=None,
        aliases=[],
        confidence=0.8,
        quote=item["raw_text"],
        route="deterministic_v4_topic_arbitration:v3_event_subject",
        rationale_he="test",
        parsed_contract={"is_topic_bearing": True, "topic_subject_he": "העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל"},
    )

    assert assignment["root_topic_id"] == "root_infrastructure_environment"
    assert assignment["child_label_he"] == "מערכות מידע ותקשורת ציבורית"
    assert assignment["topic_subject_he"] == "לוח מודעות אלקטרוני"


def test_dependent_numeric_continuation_is_non_topic_fragment() -> None:
    row = {
        "packet_role": "protocol",
        "row_type": "topic_item",
        "structural_role": "continuation",
        "root_topic_id": "root_security_enforcement",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "topic_subject_he": "סיוע משטרתי",
        "topic_identification_context": "ומבקשים את עזרת המשטרה, שזה מספר משמעותי מאוד התחנה מטפלת ב8,",
        "topic_supporting_quote_he": "ומבקשים את עזרת המשטרה, שזה מספר משמעותי מאוד התחנה מטפלת ב8,",
        "topic_assignment_route": "dictalm_v4_global_tree:dicta_contextual_root:root_only:root_only",
    }

    assert step4._post_assignment_non_topic_reason(row) == "active_numeric_or_partial_evidence_fragment"


def test_amount_only_continuation_with_beneficiary_is_non_topic_fragment() -> None:
    row = {
        "packet_role": "protocol",
        "row_type": "topic_item",
        "structural_role": "continuation",
        "root_topic_id": "root_supports",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "topic_subject_he": "יד לבנים",
        "topic_identification_context": "₪ סכום נוסף זה יעמיד את התמיכה השנתית ליד לבנים על סך 700,000 ₪",
        "topic_supporting_quote_he": "800 ₪ סכום נוסף זה יעמיד את התמיכה השנתית ליד לבנים על סך 700,000 ₪",
        "topic_assignment_route": "dictalm_v4_global_tree:dicta_contextual_root:root_only:root_only",
    }

    assert step4._post_assignment_non_topic_reason(row) == "active_numeric_or_partial_evidence_fragment"


def test_amount_clause_quote_with_beneficiary_is_non_topic_fragment() -> None:
    row = {
        "packet_role": "protocol",
        "row_type": "topic_item",
        "structural_role": "continuation",
        "root_topic_id": "root_supports",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "topic_subject_he": "עמותת יד לבנים",
        "topic_identification_context": "₪ (סכום נוסף זה יעמיד את התמיכה השנתית ליד לבנים על סך,000 ) ₪",
        "topic_headline_he": "₪ (סכום נוסף זה יעמיד את התמיכה השנתית ליד לבנים על סך,000 ) ₪",
        "topic_supporting_quote_he": "\"סכום נוסף זה יעמיד את התמיכה השנתית ליד לבנים על סך,000 ₪\"",
        "topic_assignment_route": "dictalm_v4_global_tree:dicta_contextual_root:root_only:root_only",
    }

    assert step4._post_assignment_non_topic_reason(row) == "active_numeric_or_partial_evidence_fragment"


def test_continuation_list_heading_is_non_topic_even_with_policy_match() -> None:
    row = {
        "packet_role": "protocol",
        "row_type": "topic_item",
        "structural_role": "continuation",
        "root_topic_id": "root_culture_sport",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "topic_subject_he": "מענקי הישגיות ספורט",
        "topic_identification_context": "המלצה למענקי הישגיות ספורט א להלן רשימת ההישגים והמענקים לאישורכם",
        "topic_policy_matches": [{"root_topic_id": "root_culture_sport"}],
        "topic_assignment_route": "deterministic_v4_candidate_finder:strong_policy_match:root_only",
    }

    assert step4._post_assignment_non_topic_reason(row) == "attachment_or_list_heading"


def test_canonical_topic_label_rejects_dialogue_only() -> None:
    canonical, reason = step4.canonicalize_topic_label("יו\"ר הישיבה .זה מועצת העיר, נכון")

    assert canonical is None
    assert reason == "procedural_or_dialogue_label"


def test_procedural_meeting_order_heading_is_non_topic() -> None:
    assert step4._looks_like_container_heading("ישיבת המועצה סדר") is True
    assert step4._non_topic_protocol_reason(headline="ישיבת המועצה סדר", raw_text="ישיבת המועצה סדר", structural_role="outline_item", packet_role="protocol") == "container_heading"


def test_personal_announcement_handoff_is_non_topic() -> None:
    text = "דברי ראש העיר במקום דברי ראש העיר- עו\"ד גבי כנפו יו\"ר המועצה נותן למר לאוניד גלמן את זכות הדיבור למסירת הודעה אישית"

    assert step4._non_topic_protocol_reason(headline=text, raw_text=text, structural_role="outline_item", packet_role="protocol") == "personal_announcement_handoff"


def test_person_attribution_only_row_is_not_topic_item() -> None:
    unit = {
        "structure_unit_id": "u1",
        "structural_role": "body",
        "raw_text": "גב' מזל אסולין- רכזת וועדה על סדר היום",
    }
    contract = step4._topic_contract_from_headline(unit["raw_text"], structural_role="body", packet_role="protocol")

    assert step4._row_type_for_unit(unit=unit, topic_contract=contract, packet_role="protocol") == "attribution_fragment"


def test_active_person_attribution_subject_is_demoted_after_assignment() -> None:
    row = {
        "packet_role": "protocol",
        "row_type": "topic_item",
        "structural_role": "body",
        "root_topic_id": "root_people_roles",
        "topic_node_status": "active",
        "is_topic_bearing": True,
        "topic_subject_he": "גב' מזל אסולין",
        "topic_identification_context": "גב' מזל אסולין- רכזת וועדה על סדר היום",
        "topic_assignment_route": "dictalm_v4_global_tree",
    }

    assert step4._post_assignment_non_topic_reason(row) == "active_person_attribution_subject"


def test_vote_attendance_text_is_vote_fragment() -> None:
    text = "בביתן הצפוני, נמל יפו תל אביב נוכחות בהצבעה חברי המועצה"

    assert step4._looks_like_vote_fragment(text) is True


def test_canonical_topic_label_rejects_clause_fragment() -> None:
    canonical, reason = step4.canonicalize_topic_label("כוח אדם, שנוגעת לכוח אדם")

    assert canonical is None
    assert reason == "procedural_or_dialogue_label"


def test_canonical_topic_label_rejects_generic_committee_carrier() -> None:
    canonical, reason = step4.canonicalize_topic_label("פרוטוקול ועדה מקצועית")

    assert canonical is None
    assert reason == "procedural_or_dialogue_label"


def test_canonical_topic_label_rejects_protocol_decision_fragment() -> None:
    canonical, reason = step4.canonicalize_topic_label("שהתקבלו בפרוטוקולים של הועדות כוחם יפה והם בתוקף")

    assert canonical is None
    assert reason == "procedural_or_dialogue_label"


def test_canonical_topic_label_rejects_decision_carriers() -> None:
    for raw in ["החלטה", "ה חלטה", "חמור זה התייחסות הוועדה ל", "מחדש לפני ועדת התמיכות"]:
        canonical, reason = step4.canonicalize_topic_label(raw)

        assert canonical is None
        assert reason == "procedural_or_dialogue_label"


def test_canonical_topic_label_rejects_legal_and_table_carriers() -> None:
    for raw in ["להלן", "בסעיף", "תיקון סעיף", "קוד תאור החלטה תוקף סטאטוס", "שאילתה של עורכת דין גלבר בנושא", "מי בעד הצעת ההחלטה"]:
        canonical, reason = step4.canonicalize_topic_label(raw)

        assert canonical is None
        assert reason in {"procedural_or_dialogue_label", "unresolved_noisy_label"}


def test_canonical_topic_label_does_not_use_unrelated_evidence_subject() -> None:
    canonical, _reason = step4.canonicalize_topic_label(
        "מינוי גב' איילה גיני לתפקיד מנהלת אגף פרט, בקרה ונוכחות במנהל משאבי אנוש",
        evidence_text="הסכם רשות בין עיריית אשדוד לבין עמותת מרכז לחינוך תורני",
    )

    assert canonical != "הסכם רשות למוסדות חינוך"


def test_subject_scoped_protocol_mode_detected_from_title_text() -> None:
    units = [
        {
            "structure_unit_id": "u1",
            "raw_text": "פרוטוקול ישיבה לא מן המניין מס 31 תקציב - 2- :סדר הישיבה 2026 הצעת התקציב הרגיל והבלתי רגיל לשנת ************************",
        },
        *[
            {
                "structure_unit_id": f"u{i}",
                "raw_text": ":גב' כהן . דברי הסבר : היו\"ר-מר לוי . תגובה",
            }
            for i in range(2, 8)
        ],
    ]
    context = step4._document_context(input_pdf=None, packet_role="protocol", units=units)

    assert context["protocol_subject_he"] == "הצעת התקציב הרגיל והבלתי רגיל"
    assert context["topic_carrier_mode"] == "subject_scoped_transcript_topics"
    assert "2026" in context["temporal_metadata"]


def test_normal_protocol_without_subject_stays_headline_mode() -> None:
    units = [
        {
            "structure_unit_id": "u1",
            "raw_text": "פרוטוקול ישיבה מן המניין מס 35 :סדר הישיבה שאילתות הצעות לסדר אישורים",
        },
        {
            "structure_unit_id": "u2",
            "raw_text": "שאילתה: יובל צלנר, חבר מועצה",
        },
    ]
    context = step4._document_context(input_pdf=None, packet_role="protocol", units=units)

    assert context["protocol_subject_he"] is None
    assert context["topic_carrier_mode"] == "headline_topics"


def test_protocol_subject_rejects_transcript_fragment() -> None:
    units = [
        {
            "structure_unit_id": "u1",
            "raw_text": "ישיבה לא מן המניין מס 28 ועכשיו אני אחזור לדוח שיש בפניכם דוחות ביקורת וגם ב- כמו הדוחות הקודמים בכל שנה, דנים ב2024 לשנת53 הדוח הזה, דוח מספר בדיקה",
        }
    ]
    context = step4._document_context(input_pdf=None, packet_role="protocol", units=units)

    assert context["protocol_subject_he"] is None
    assert context["topic_carrier_mode"] == "headline_topics"


def test_subject_scoped_anchor_extracts_hidden_topic_without_year() -> None:
    unit = {
        "structure_unit_id": "u1",
        "structural_role": "outline_item",
        "raw_text": "סעיף81213/786/6 סעיף תקציבי :גב' קשת . שח' בתקציב המשפחתונים לנשים עובדות200,000 ההסתייגות היא בעניין קיצוץ של : היו\"ר-מר שפירא .11 להלן",
    }
    context = {
        "packet_role": "protocol",
        "protocol_subject_he": "הצעת התקציב הרגיל והבלתי רגיל",
        "topic_carrier_mode": "subject_scoped_transcript_topics",
    }

    anchor = step4._subject_scoped_transcript_topic_anchor(unit=unit, document_context=context)

    assert anchor is not None
    assert anchor["topic_subject_he"] == "תקציב המשפחתונים לנשים עובדות"
    assert "2026" not in anchor["topic_subject_he"]


def test_subject_scoped_anchor_rejects_body_budget_clause() -> None:
    unit = {
        "structure_unit_id": "u1",
        "structural_role": "body",
        "raw_text": ":מר גילצר . תודה רבה על ההזדמנות להציג את הדוחות הכספיים. "
        + " ".join(["העירייה פעלה באחריות כלכלית"] * 16)
        + " התנהלה בתקציב המשכי והשלכות המלחמה ונבעה מהליך התכנסות.",
    }
    context = {
        "packet_role": "protocol",
        "protocol_subject_he": "דוחות כספיים מבוקרים",
        "topic_carrier_mode": "subject_scoped_transcript_topics",
    }

    assert step4._subject_scoped_transcript_topic_anchor(unit=unit, document_context=context) is None


def test_procedural_dialogue_fragment_is_non_topic() -> None:
    reason = step4._non_topic_protocol_reason(
        headline="שניות מיקרופון אני אתן לך אישור לדבר",
        raw_text="שניות מיקרופון : עו\"ד כנפו אני אתן לך אישור, תדברי. אני לא נותן לך אפשרות כזאת",
        structural_role="body",
        packet_role="protocol",
    )

    assert reason == "procedural_dialogue_fragment"


def test_procedural_dialogue_detection_is_role_independent() -> None:
    reason = step4._non_topic_protocol_reason(
        headline="שניות מיקרופון אני רוצה לדבר גם אני רוצה לדבר",
        raw_text="שניות מיקרופון אני רוצה לדבר גם אני רוצה לדבר אני לא נותן לך אפשרות",
        structural_role="section_heading",
        packet_role="protocol",
    )

    assert reason == "procedural_dialogue_fragment"


def test_previous_decision_continuation_without_subject_is_non_topic() -> None:
    assert step4._looks_like_no_topic_continuation("הוועדה עומדת מאחורי החלטתה מהדיון הקודם ולמען הסדר הטוב") is True


def test_short_domain_subject_is_not_no_topic_continuation() -> None:
    assert step4._looks_like_no_topic_continuation("הקלטת עובדים") is False
    assert step4._non_topic_protocol_reason(headline="הקלטת עובדים", raw_text='שאילתה בנושא "הקלטת עובדים"', structural_role="continuation", packet_role="protocol") is None


def test_body_transcript_without_explicit_marker_has_no_topic_provenance() -> None:
    unit = {
        "structural_role": "body",
        "raw_text": "מר כהן אני מבקש לומר שהמשטרה הגיעה בבוקר והיה דיון ארוך על הבניין הישן ועל שריפה אפשרית",
    }

    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="המשטרה הגיעה בבוקר והיה דיון ארוך",
        raw_text=unit["raw_text"],
        topic_context_source="unit_heading",
        headline_source="raw_extracted",
        packet_role="protocol",
    ) == "body_without_headline_topic_provenance"


def test_long_outline_transcript_window_is_not_standalone_topic() -> None:
    raw_text = " ".join(["פרוטוקול ישיבה מן המניין", "מר לוי אמר שהצעה לסדר נמשכה זמן רב", "ומכאן המשיך דיון ארוך מאוד על נושאים שונים"] * 8)
    unit = {"structural_role": "outline_item", "raw_text": raw_text}

    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="פרוטוקול ישיבה מן המניין מר לוי אמר שהצעה לסדר נמשכה זמן רב",
        raw_text=raw_text,
        topic_context_source="unit_heading",
        headline_source="raw_extracted",
        packet_role="protocol",
    ) == "transcript_window_without_bounded_headline"


def test_explicit_local_topic_marker_allows_body_topic_extraction() -> None:
    raw_text = 'שאילתה בנושא "הקלטת עובדים" נשאלה על ידי חבר מועצה'
    unit = {"structural_role": "continuation", "raw_text": raw_text}

    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="הקלטת עובדים",
        raw_text=raw_text,
        topic_context_source="unit_heading",
        headline_source="raw_extracted",
        packet_role="protocol",
    ) is None


def test_incidental_body_marker_in_long_transcript_does_not_create_topic() -> None:
    raw_text = " ".join(["פרוטוקול ישיבה מן המניין", "רשות המים התריעה בנושא הזיהום אבל הדובר המשיך בדיון ארוך"] * 12)
    unit = {"structural_role": "outline_item", "raw_text": raw_text}

    assert step4._has_bounded_raw_topic_marker(raw_text) is False
    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="פרוטוקול ישיבה מן המניין",
        raw_text=raw_text,
        topic_context_source="unit_heading",
        headline_source="raw_extracted",
        packet_role="protocol",
    ) == "transcript_window_without_bounded_headline"


def test_incidental_strong_marker_in_long_transcript_does_not_create_topic() -> None:
    raw_text = " ".join(["פרוטוקול ישיבה מן המניין", "מר לוי אמר שהשאילתה בנושא העצים כבר נדונה", "ומכאן המשיך דיון ארוך"] * 10)
    unit = {"structural_role": "outline_item", "raw_text": raw_text}

    assert step4._has_bounded_raw_topic_marker(raw_text) is False
    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="שאילתה בנושא העצים כבר נדונה",
        raw_text=raw_text,
        topic_context_source="unit_heading",
        headline_source="raw_extracted",
        packet_role="protocol",
    ) == "transcript_window_without_bounded_headline"


def test_short_continuation_without_local_marker_is_not_standalone_topic() -> None:
    raw_text = "2026 יפו מתן פטור מאגרות בעקבות המלחמה הוראת שעה הסעיף הבא חוק העזר לתל אביב"
    unit = {"structural_role": "continuation", "raw_text": raw_text}

    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="יפו מתן פטור מאגרות בעקבות המלחמה",
        raw_text=raw_text,
        topic_context_source="unit_heading",
        headline_source="raw_extracted",
        packet_role="protocol",
    ) == "transcript_window_without_bounded_headline"


def test_section47_email_approval_carrier_is_not_topic_bearing() -> None:
    text = "התקבל אישור מועצה במייל ב- 24.5.26 לפי סעיף47 לתקנון בדבר ישיבות מועצה"

    assert step4._topic_contract_from_headline(text, structural_role="continuation", packet_role="protocol")["is_topic_bearing"] is False
    assert step4._non_topic_protocol_reason(headline=text, raw_text=text, structural_role="continuation", packet_role="protocol") == "procedural_packet_carrier"


def test_section47_email_carrier_does_not_keep_weak_council_root_candidate() -> None:
    text = "התקבל אישור מועצה במייל ב- 24.5.26"
    item = {
        "structure_unit_id": "u1",
        "semantic_unit_id": "u1",
        "source_page": 1,
        "structural_role": "outline_item",
        "row_type": "fragment",
        "is_topic_bearing": False,
        "unit_raw_text": text,
        "raw_text": text,
        "topic_identification_context": text,
        "topic_headline_he": "",
        "topic_subject_he": None,
        "non_topic_reason": "procedural_packet_carrier",
        "document_context": {"packet_role": "protocol"},
        "root_topic_candidates": [
            {
                "root_topic_id": "root_religious_services",
                "root_label_he": "דת ושירותי דת",
                "child_choice_id": "child_religious_council",
                "child_label_he": "מועצה דתית",
                "score": 0.48,
                "matched_terms": [],
            }
        ],
    }
    previous_item = {
        "structure_unit_id": "parent",
        "structural_role": "outline_item",
        "unit_raw_text": "תקצוב אירוע חגיגות אליפות",
        "document_context": {"packet_role": "protocol"},
    }
    previous_row = {
        "structure_unit_id": "parent",
        "packet_role": "protocol",
        "row_type": "topic_item",
        "root_topic_id": "root_budget_finance",
        "topic_subject_he": "תקצוב אירוע חגיגות אליפות",
        "topic_node_status": "active",
        "is_topic_bearing": True,
    }
    row = step4._non_topic_assignment(item, reason="procedural_packet_carrier")

    updated = step4._apply_topic_arbitration(assignments=[previous_row, row], items=[previous_item, item], enable_govmap_geo=False)[1]

    assert updated["root_topic_id"] == "root_agenda_queries"
    assert updated["topic_node_status"] == "active"
    assert updated["is_topic_bearing"] is False
    assert updated["topic_arbitration"] == {"decision": "evidence_only", "reason": "procedural_packet_carrier"}


def test_short_protocol_cover_headers_do_not_keep_weak_council_root_candidate() -> None:
    for text in (
        "עיריית באר שבע פרוטוקול אישור מועצה מן המניין",
        "פרוטוקול אישור מועצה מן המניין מס' 50",
    ):
        item = {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "source_page": 1,
            "structural_role": "outline_item",
            "row_type": "fragment",
            "is_topic_bearing": False,
            "unit_raw_text": text,
            "raw_text": text,
            "topic_identification_context": text,
            "topic_headline_he": "",
            "topic_subject_he": None,
            "non_topic_reason": "protocol_cover_metadata",
            "document_context": {"packet_role": "protocol"},
            "root_topic_candidates": [
                {
                    "root_topic_id": "root_religious_services",
                    "root_label_he": "דת ושירותי דת",
                    "child_choice_id": "child_religious_council",
                    "child_label_he": "מועצה דתית",
                    "score": 0.48,
                    "matched_terms": [],
                }
            ],
        }
        previous_item = {
            "structure_unit_id": "parent",
            "structural_role": "outline_item",
            "unit_raw_text": "תקצוב אירוע חגיגות אליפות",
            "document_context": {"packet_role": "protocol"},
        }
        previous_row = {
            "structure_unit_id": "parent",
            "packet_role": "protocol",
            "row_type": "topic_item",
            "root_topic_id": "root_budget_finance",
            "topic_subject_he": "תקצוב אירוע חגיגות אליפות",
            "topic_node_status": "active",
            "is_topic_bearing": True,
        }
        row = step4._non_topic_assignment(item, reason="protocol_cover_metadata")

        updated = step4._apply_topic_arbitration(assignments=[previous_row, row], items=[previous_item, item], enable_govmap_geo=False)[1]

        assert step4._non_topic_protocol_reason(headline=text, raw_text=text, structural_role="outline_item", packet_role="protocol") == "protocol_cover_metadata"
        assert step4._topic_contract_from_headline(text, structural_role="outline_item", packet_role="protocol")["is_topic_bearing"] is False
        assert updated["root_topic_id"] == "root_agenda_queries"
        assert updated["is_topic_bearing"] is False
        assert updated["topic_arbitration"] == {"decision": "evidence_only", "reason": "protocol_cover_metadata"}


def test_section47_budget_packet_extracts_budget_subject() -> None:
    raw_text = (
        "סעיף47 לתקנון בדבר ישיבות המועצה לאחר אישור היועץ המשפטי .2026 "
        "תקצוב אירוע חגיגות אליפות לשנת 2026 – עדכון תקציב לשנת 2026 "
        "לעירייה לאשר אישור המועצה לעדכון נדרש עפ\"י דין"
    )
    headline = step4._extract_heading_from_text(raw_text)
    unit = {"structural_role": "outline_item", "raw_text": raw_text}

    assert "תקצוב אירוע" in headline
    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline=headline,
        raw_text=raw_text,
        topic_context_source="unit_heading",
        headline_source="raw_extracted",
        packet_role="protocol",
    ) is None

    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={"structure_unit_id": "u1", "semantic_unit_id": "u1", **unit},
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": headline, "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )

    assert item["row_type"] == "topic_item"
    assert item["deterministic_topic_decision"]["root_topic_id"] == "root_budget_finance"
    assert item["deterministic_topic_decision"]["action"] == "choose_existing_topic"


def test_explicit_decision_heading_extracts_bounded_subject_from_long_row() -> None:
    raw_text = (
        "החלטה: שינוי מטרת חכירה מתחם \"סיפולוקס\" יגאל אלון – מ א ו ש ר "
        "בגוש107 וחלקה111 קול), לאשר החכרת המגרש המאוחד המהווה את חלק מחלקה17( "
        "המועצה החליטה פה אחד לחברת מגדל סיפולוקס בע\"מ להקמת מבנה בן45 קומות "
        "לשימושי מסחר, תעסוקה, מגורים ומבנים ומוסדות ציבור וכן שימור ותוספת בניה "
        "למבנה סיפולוקס לתקופה שמתחילה במועד חתימת חוזה החכירה ועד ליום עתידי "
        "בתוספת מע\"מ ובתוספת ריבית והצמדה החל מתום יום התשלום. "
        "בהצעת ההחלטה4 -1 אישור מועצת העירייה וכן לחתום על חוזה להקמת מבנה הציבור"
    )
    headline = step4._extract_heading_from_text(raw_text)
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])

    item = step4._build_item(
        unit={"structure_unit_id": "u1", "semantic_unit_id": "u1", "structural_role": "outline_item", "raw_text": raw_text},
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "", "context_source": "", "parent_agenda_unit_id": None},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )

    assert len(raw_text) > 360
    assert headline == "שינוי מטרת חכירה מתחם \"סיפולוקס\" יגאל אלון"
    assert item["topic_headline_he"] == headline
    assert item["row_type"] == "topic_item"
    assert item["topic_provenance_reject_reason"] is None
    assert item["is_topic_bearing"] is True


def test_structure_ineligible_rows_cannot_be_resurrected_as_topics() -> None:
    items = [
        {
            "structure_unit_id": "m1",
            "document_context": {"packet_role": "protocol"},
            "structural_role": "metadata",
            "row_type": "metadata",
            "topic_assignment_eligible": False,
        },
        {
            "structure_unit_id": "v1",
            "document_context": {"packet_role": "protocol"},
            "structural_role": "vote_or_result",
            "row_type": "vote_or_result",
            "topic_assignment_eligible": True,
        },
        {
            "structure_unit_id": "c1",
            "document_context": {"packet_role": "protocol"},
            "structural_role": "continuation",
            "row_type": "container",
            "topic_assignment_eligible": True,
        },
    ]
    assignments = [
        {"structure_unit_id": "m1", "row_type": "metadata", "is_topic_bearing": True, "topic_subject_he": "נושא שגוי", "topic_assignment_route": "test"},
        {"structure_unit_id": "v1", "row_type": "vote_or_result", "is_topic_bearing": True, "topic_subject_he": "נושא שגוי", "topic_assignment_route": "test"},
        {"structure_unit_id": "c1", "row_type": "inherited_topic_context", "is_topic_bearing": True, "topic_subject_he": "נושא שגוי", "topic_assignment_route": "test"},
    ]

    updated = step4._enforce_structure_ineligible_rows(assignments=assignments, items=items)

    assert [row["is_topic_bearing"] for row in updated] == [False, False, False]
    assert [row["row_type"] for row in updated] == ["metadata", "vote_or_result", "container"]
    assert updated[0]["topic_subject_he"] is None
    assert updated[1]["topic_subject_he"] is None
    assert updated[2]["topic_subject_he"] is None
    assert "structure_marked_topic_ineligible" in updated[0]["topic_assignment_route"]
    assert "structural_role_vote_or_result" in updated[1]["topic_assignment_route"]
    assert "row_type_container" in updated[2]["topic_assignment_route"]


def test_bounded_split_lease_continuation_is_topic_item() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "continuation",
            "raw_text": "4 .. עסקאות חכירה מול רמ\"י",
            "structure_evidence": {"split_reason": "multiple_structural_starts"},
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "עסקאות חכירה מול רמ\"י", "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )

    assert item["row_type"] == "topic_item"
    assert item["skip_model_assignment"] is False
    assert item["deterministic_topic_decision"]["root_topic_id"] == "root_allocations"


def test_punctuated_hendon_continuation_heading_is_topic_item() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "continuation",
            "raw_text": ":הנדון 'מענק עליה לשלב ב",
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "", "context_source": "", "parent_agenda_unit_id": None},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )

    assert item["row_type"] == "topic_item"
    assert item["topic_subject_he"] == "מענק עליה לשלב ב"
    assert item["deterministic_topic_decision"]["root_topic_id"] == "root_culture_sport"


def test_contextual_prefilter_only_allows_strong_policy_matches() -> None:
    item = {
        "row_type": "topic_item",
        "raw_text": "תקציב הגיל הרך",
        "topic_subject_he": "תקציב הגיל הרך",
        "document_context": {"packet_role": "protocol"},
        "deterministic_topic_decision": {
            "action": "choose_existing_topic",
            "needs_dicta": False,
            "reason": "strong_policy_match",
            "root_topic_id": "root_budget_finance",
            "confidence": 0.98,
        },
    }

    assert step4._preassign_contextual_with_candidate_finder(item) is True

    assert step4._preassign_contextual_with_candidate_finder(
        {**item, "deterministic_topic_decision": {**item["deterministic_topic_decision"], "reason": "strong_root_match"}}
    ) is False
    assert step4._preassign_contextual_with_candidate_finder(
        {**item, "deterministic_topic_decision": {"action": "needs_judge", "reason": "ambiguous_candidates", "root_topic_id": "root_budget_finance", "confidence": 0.84}}
    ) is False
    assert step4._preassign_contextual_with_candidate_finder(
        {**item, "deterministic_topic_decision": {**item["deterministic_topic_decision"], "confidence": 0.91}}
    ) is False


def test_contextual_model_omission_uses_strong_child_tree_evidence() -> None:
    row = step4._fallback_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "topic_item",
            "structural_role": "continuation",
            "raw_text": "4 . קריאת רחוב על שמו של זאב רווח ז\"ל",
            "topic_headline_he": "קריאת רחוב על שמו של זאב רווח ז\"ל",
            "topic_subject_he": "קריאת רחוב על שמו של זאב רווח ז\"ל",
            "is_topic_bearing": True,
            "document_context": {"packet_role": "protocol"},
            "deterministic_topic_decision": {
                "action": "needs_judge",
                "needs_dicta": True,
                "reason": "ambiguous_candidates",
                "root_topic_id": "root_administration",
                "confidence": 0.9,
            },
            "root_topic_candidates": [{"root_topic_id": "root_administration", "score": 0.9}],
            "candidate_child_topics": [
                {
                    "candidate_child_id": "u1_child_1",
                    "root_topic_id": "root_administration",
                    "root_label_he": "מנהל עירוני ומינויים",
                    "label_he": "שמות והנצחה",
                    "evidence_source": "existing_tree",
                    "evidence_quote_he": "קריאת רחוב על שמו של זאב רווח ז\"ל",
                    "confidence_hint": 0.94,
                    "aliases_he": ["קריאת רחוב"],
                }
            ],
        },
        reason="contextual_model_omitted_unit",
    )

    assert row["topic_node_status"] == "active"
    assert row["topic_subject_he"] == "קריאת רחוב על שמו של זאב רווח ז\"ל"
    assert row["child_label_he"] == "שמות והנצחה"
    assert row["topic_reject_reason"] is None


def test_db_free_tree_exposes_curated_child_aliases() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    admin_root = next(row for row in tree["root_topics"] if row["root_topic_id"] == "root_administration")
    naming_child = next(child for child in admin_root["children"] if child["child_label_he"] == "שמות והנצחה")

    assert "קריאת רחוב" in naming_child["aliases_he"]


def test_curated_child_alias_supports_contextual_root_and_child() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "outline_item",
            "raw_text": "4 . קריאת רחוב על שמו של זאב רווח ז\"ל",
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "קריאת רחוב על שמו של זאב רווח ז\"ל", "context_source": "unit_heading"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )
    item["dicta_mode"] = "dicta_contextual"
    parsed = {
        "structure_unit_id": "u1",
        "root_topic_id": "root_administration",
        "is_topic_bearing": True,
        "topic_subject_he": "קריאת רחוב על שמו של זאב רווח ז\"ל",
        "clean_subject_he": "קריאת רחוב על שמו של זאב רווח ז\"ל",
        "confidence": 0.9,
        "rationale_he": "נושא שמות והנצחה במרחב העירוני.",
        "topic_supporting_quote_he": "קריאת רחוב על שמו של זאב רווח ז\"ל",
        "indexability_status": "standalone_topic",
        "event_status": "discussed",
        "agenda_disposition": "discussed",
        "classification_basis": "service_domain",
        "competing_roots": [],
        "contextual_schema_valid": True,
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert any(child["label_he"] == "שמות והנצחה" for child in item["candidate_child_topics"])
    assert assignment["root_topic_id"] == "root_administration"
    assert assignment["child_label_he"] == "שמות והנצחה"


def test_lease_right_cancellation_routes_to_allocations_despite_agreement_word() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "outline_item",
            "raw_text": "8 . אישור הסכם לביטול זכות חכירה . 9 '. החלפת שטחי ציבור בתוכנית מס605-1535301 ברח' הורקנוס",
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "אישור הסכם לביטול זכות חכירה . 9 '. החלפת שטחי ציבור בתוכנית מס605-1535301 ברח' הורקנוס", "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )
    row = step4._assignment_from_candidate_finder(item)

    assert row["root_topic_id"] == "root_allocations"
    assert row["topic_subject_he"] == "ביטול זכות חכירה"
    assert row["topic_node_status"] == "active"
    assert "candidate_review" not in row["topic_assignment_route"]


def test_senior_officer_appointment_trims_tender_tail() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "outline_item",
            "raw_text": "סעיף47 לתקנון בדבר ישיבות המועצה לאחר אישור היועץ המשפטי לעירייה לאשר,מינוי שכר ואצילת סמכויות למהנדס העיר מר פלוני שנבחר במכרז ביום13.5.",
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "מינוי שכר ואצילת סמכויות למהנדס העיר מר פלוני שנבחר במכרז ביום", "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )
    row = step4._assignment_from_candidate_finder(item)

    assert row["root_topic_id"] == "root_administration"
    assert row["topic_subject_he"] == "מינוי שכר ואצילת סמכויות למהנדס העיר"
    assert "topic_subject_rejected" not in row["topic_assignment_route"]


def test_general_committee_protocol_approval_preserves_action_and_routes_to_administration() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    raw_text = "10 . אישור החלטות בפרוטוקולים של ועדות העירייה :"
    headline = step4._extract_heading_from_text(raw_text)
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "outline_item",
            "raw_text": raw_text,
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": headline, "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )
    row = step4._assignment_from_candidate_finder(item)

    assert headline == "אישור החלטות בפרוטוקולים של ועדות העירייה"
    assert row["root_topic_id"] == "root_administration"
    assert row["topic_node_status"] == "active"


def test_debt_writeoff_protocol_label_keeps_subject_and_existing_child() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "outline_item",
            "raw_text": "12 . 'פרוטוקול למחיקת חובות מס7.25 מיום25.12.",
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "פרוטוקול למחיקת חובות מס7.25 מיום25.12", "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )
    row = step4._assignment_from_candidate_finder(item)

    assert row["topic_subject_he"] == "מחיקת חובות"
    assert row["root_topic_id"] == "root_budget_finance"
    assert row["child_label_he"] == "גביית חובות"
    assert row["topic_node_status"] == "active"


def test_paramedical_benefits_question_routes_to_welfare_subject() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    raw_text = (
        "סעיף6 : שינוי קריטריונים בביטוח לאומי למתן - שאילתא בנושא 1.5 מצ\"ל "
        "טיפולים פרא רפואיים, בקשתו של חבר מועצה"
    )
    item = step4._build_item(
        unit={"structure_unit_id": "u1", "semantic_unit_id": "u1", "structural_role": "outline_item", "raw_text": raw_text},
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "שינוי קריטריונים בביטוח לאומי למתן - שאילתא בנושא", "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )
    row = step4._assignment_from_candidate_finder(item)

    assert row["topic_subject_he"] == "טיפולים פרא רפואיים"
    assert row["root_topic_id"] == "root_welfare_social"
    assert row["topic_node_status"] == "active"


def test_municipal_prizes_subject_matches_existing_culture_child() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "outline_item",
            "raw_text": ")22 'עדכון תקנוני הפרסים העירוניים ואישור תקנון פרס התיאטרון (עמ.8",
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "עדכון תקנוני הפרסים העירוניים ואישור תקנון פרס התיאטרון", "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )
    row = step4._assignment_from_candidate_finder(item)

    assert row["topic_subject_he"] == "פרסים עירוניים"
    assert row["root_topic_id"] == "root_culture_sport"
    assert row["child_label_he"] == "פרסים עירוניים"
    assert row["topic_node_status"] == "active"


def test_urban_renewal_heading_routes_to_planning() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "continuation",
            "raw_text": "6 .. בקשה לקידום מתחם התחדשות עירונית גן סיאטל בותמ\"ל",
            "structure_evidence": {"split_reason": "multiple_structural_starts"},
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "בקשה לקידום מתחם התחדשות עירונית גן סיאטל בותמ\"ל", "context_source": "unit_heading", "parent_agenda_unit_id": "u1"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )

    assert item["row_type"] == "topic_item"
    assert item["deterministic_topic_decision"]["root_topic_id"] == "root_planning_building"


def test_legal_boilerplate_fragment_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="לפקודת העיריות המועצה החליטה בתוקף סמכותה לפי סעיף",
        raw_text="לפקודת העיריות המועצה החליטה בתוקף סמכותה לפי סעיף",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "legal_boilerplate_fragment"


def test_dependent_legal_clause_fragment_is_not_topic() -> None:
    assert step4._post_assignment_non_topic_reason(
        {
            "packet_role": "protocol",
            "topic_node_status": "active",
            "is_topic_bearing": True,
            "root_topic_id": "root_planning_building",
            "topic_subject_he": "מתאימה בהכנת תוכנית ואישורה או בדרך חוקית אחרת",
            "topic_headline_he": "מתאימה בהכנת תוכנית ואישורה או בדרך חוקית אחרת",
            "topic_identification_context": "מתאימה בהכנת תוכנית ואישורה או בדרך חוקית אחרת",
        }
    ) == "active_dependent_legal_clause_fragment"


def test_legal_section_reference_fragment_is_not_extracted_as_heading() -> None:
    raw_text = "סעיף198 א בוצעו. יש חוות דעת של הוועדה המקצועית שמצביעה על תועלת כלכלית בבנייה"

    assert step4._extract_heading_from_text(raw_text) == ""
    assert step4._non_topic_protocol_reason(
        headline=raw_text,
        raw_text=raw_text,
        structural_role="outline_item",
        packet_role="protocol",
    ) == "legal_section_reference_fragment"


def test_transcript_thank_you_fragment_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="בדקתי ושלחתי לה תודה על טיפול מסור וסבלני אז תודה לך",
        raw_text="בדקתי, הראשון שבהם היה בפברואר. שלחתי לה. תודה על טיפול מסור וסבלני אז תודה לך",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "transcript_speech_fragment"


def test_short_speech_fragment_without_subject_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="היום, אני אגיד לך על",
        raw_text="היום, אני אגיד לך על",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "transcript_speech_fragment"


def test_visual_instruction_fragment_without_subject_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="רועי ואורנה ?עוד מישהו מחוץ מרועי ומאורנה .אני מזכיר לכם, את הסעיף התקציבי אתם צריכים להגיד כל פעם",
        raw_text="רועי ואורנה ?עוד מישהו מחוץ מרועי ומאורנה .אני מזכיר לכם, את הסעיף התקציבי אתם צריכים להגיד כל פעם",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "transcript_speech_fragment"


def test_speaker_dialogue_fragment_without_subject_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="סעיף תקציבי :גב' קשת . ההסתייגות היא בעניין קיצוץ : היו\"ר-מר שפירא .11 להלן",
        raw_text="סעיף תקציבי :גב' קשת . ההסתייגות היא בעניין קיצוץ : היו\"ר-מר שפירא .11 להלן",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "speaker_dialogue_fragment"


def test_signature_end_page_fragment_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline='תצלום סיום הפרוטוקול וחתימות יו"ר הישיבה ומנכ"ל העירייה',
        raw_text='תצלום סיום הפרוטוקול וחתימות יו"ר הישיבה ומנכ"ל העירייה',
        structural_role="continuation",
        packet_role="protocol",
    ) == "signature_or_end_page_fragment"


def test_vote_dialogue_table_and_incomplete_query_fragments_are_non_topic() -> None:
    cases = [
        (
            "הצעה לסדר ?שלך, אנחנו רוצים להצביע. מי בעד הצעת ההחלטה",
            "vote_or_discussion_procedure_fragment",
        ),
        (
            "הצעה לסדר, ענת ענתה. אפשר לקיים דיון בהזדמנות אחרת,",
            "vote_or_discussion_procedure_fragment",
        ),
        (
            "החלטות קוד תאור החלטה תוקף סטאטו ס",
            "table_header_or_schema_fragment",
        ),
        (
            "שאילתה של עור כת דין גלבר בנושא-",
            "incomplete_query_topic_marker",
        ),
    ]

    for text, expected in cases:
        assert step4._non_topic_protocol_reason(headline=text, raw_text=text, structural_role="outline_item", packet_role="protocol") == expected


def test_model_non_topic_topic_item_is_normalized_to_fragment() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "topic_item",
            "topic_headline_he": "תצלום סיום הפרוטוקול וחתימות",
            "topic_subject_he": "תצלום סיום הפרוטוקול וחתימות",
            "document_context": {"packet_role": "protocol"},
            "raw_text": "תצלום סיום הפרוטוקול וחתימות",
        },
        reason="model_or_shape_non_topic",
    )

    assert row["row_type"] == "fragment"
    assert row["is_topic_bearing"] is False
    assert row["topic_subject_he"] is None


def test_non_topic_vote_row_does_not_inherit_child_candidate() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "vote_or_result",
            "topic_identification_context": "מינוי נציג ציבור בוועדת הבטיחות בדרכים",
            "topic_headline_he": "מינוי נציג ציבור בוועדת הבטיחות בדרכים",
            "document_context": {"packet_role": "protocol"},
            "raw_text": "מינוי נציג ציבור בוועדת הבטיחות בדרכים. מי בעד?",
            "candidate_child_topics": [
                {
                    "candidate_child_id": "c1",
                    "label_he": "בטיחות בדרכים",
                    "root_topic_id": "root_transport_safety",
                    "root_label_he": "תחבורה ובטיחות",
                    "confidence_hint": 0.96,
                    "evidence_source": "existing_tree",
                }
            ],
        }
    )

    assert row["is_topic_bearing"] is False
    assert row["root_topic_id"] == "root_transport_safety"
    assert row["child_label_he"] is None
    assert "inherited_candidate" not in row["topic_assignment_route"]


def test_fragment_body_with_strong_parking_evidence_gets_transport_root_only() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "body",
            "topic_identification_context": "יש למנהלת אגף החנייה סמכות לחלוקת תווי חנייה",
            "topic_headline_he": "",
            "document_context": {"packet_role": "protocol"},
            "raw_text": "יש למנהלת אגף החנייה סמכות, אבל זה לא בהתאם לנוהל; חלוקה של תווי חנייה בניגוד לנהלים עירוניים",
        },
        reason="body_without_headline_topic_provenance",
    )

    assert row["is_topic_bearing"] is False
    assert row["root_topic_id"] == "root_transport_safety"
    assert row["child_label_he"] is None
    assert row["topic_assignment_confidence"] == 0.55
    assert row["topic_assignment_route"].endswith("strong_body_evidence_root")


def test_split_header_fragment_ignores_copied_action_evidence_for_root_override() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "continuation",
            "topic_identification_context": "ישיבת מועצה מספר 75",
            "topic_headline_he": "",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": "ישיבת מועצה מספר 75 מתאריך 31.8.2023",
            "raw_text": "ישיבת מועצה מספר 75\nמאפשר חלוקה של תווי חנייה בניגוד לנהלים עירוניים",
        },
        reason="missing_topic_headline_provenance",
    )

    assert row["root_topic_id"] == "root_agenda_queries"
    assert row["topic_assignment_confidence"] == 0.35
    assert not row["topic_assignment_route"].endswith("strong_body_evidence_root")


def test_short_protocol_header_listing_does_not_get_topic_root_context() -> None:
    raw_text = (
        "פרוטוקול ישיבות המועצה העשרים ושתיים פרוטוקול ישיבה לא מן המניין "
        "מתאריך ט' בטבת תשפ\"ו תקציב 2026 - סדר הישיבה הצעת התקציב הרגיל והבלתי רגיל לשנת 2026"
    )
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "body",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": raw_text,
            "raw_text": raw_text,
        },
        reason="body_without_headline_topic_provenance",
    )

    assert row["root_topic_id"] == "root_agenda_queries"
    assert "strong_body_evidence_root" not in row["topic_assignment_route"]


def test_non_topic_tender_report_row_gets_procurement_root_context() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "continuation",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": "דוח ועדה למסירת עבודות הפטורות ממכרז מישיבה מס 42",
            "raw_text": "דוח ועדה למסירת עבודות הפטורות ממכרז מישיבה מס 42",
        },
        reason="transcript_window_without_bounded_headline",
    )

    assert row["root_topic_id"] == "root_agreements"
    assert row["child_label_he"] is None
    assert "strong_body_evidence_root" in row["topic_assignment_route"]


def test_long_transcript_fragment_does_not_get_procurement_root_context() -> None:
    transcript = " ".join(
        [
            "פרוטוקול ישיבות המועצה פרוטוקול ישיבה מן המניין מתאריך יט בחשון",
            "מר כהן ראש העירייה שאל על בקשה לאישור ניהול משא ומתן עם ספקים פוטנציאליים לצורך התקשרות ללא מכרז",
            "גב' לוי השיבה שהדיון נמשך והדוברים עברו לנושאים נוספים ללא כותרת עצמאית",
        ]
        * 9
    )
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "body",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": transcript,
            "raw_text": transcript,
        },
        reason="body_without_headline_topic_provenance",
    )

    assert row["root_topic_id"] == "root_agenda_queries"
    assert row["topic_assignment_confidence"] == 0.35
    assert "strong_body_evidence_root" not in row["topic_assignment_route"]


def test_protocol_agenda_listing_does_not_get_land_allocation_root_context() -> None:
    listing = (
        "פרוטוקול ישיבות המועצה העשרים ושתיים פרוטוקול ישיבה מן המניין "
        "מתאריך ו' באדר תשפ\"ו סדר הישיבה שאילתות עמ 3 הצעות לסדר היום עמ 10 "
        "פרוטוקול ועדת נכסים עמ 16 פרוטוקול ועדת הקצאת מקרקעין מס 9/26 עמ 17 "
        "פרוטוקול ועדת כספים עמ 23 פרוטוקול ועדת תמיכות עמ 25 מינויים ושינויים בתאגידים"
    )
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "body",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": listing,
            "raw_text": listing,
        },
        reason="body_without_headline_topic_provenance",
    )

    assert row["root_topic_id"] == "root_agenda_queries"
    assert row["topic_assignment_confidence"] == 0.35
    assert "strong_body_evidence_root" not in row["topic_assignment_route"]


def test_non_topic_environmental_report_row_gets_environment_root_context() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "continuation",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": "דוח ועדת איכות הסביבה מישיבה מס 8",
            "raw_text": "דוח ועדת איכות הסביבה מישיבה מס 8",
        },
        reason="transcript_window_without_bounded_headline",
    )

    assert row["root_topic_id"] == "root_infrastructure_environment"
    assert row["child_label_he"] is None


def test_non_topic_environmental_bylaw_row_gets_environment_root_context() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "body",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": "הצעת חוק עזר למניעת רעש והארכת הוראת השעה לפינוי אשפה בהתאם לאישור השרה להגנת הסביבה",
            "raw_text": "הצעת חוק עזר למניעת רעש והארכת הוראת השעה לפינוי אשפה בהתאם לאישור השרה להגנת הסביבה",
        },
        reason="body_without_headline_topic_provenance",
    )

    assert row["root_topic_id"] == "root_infrastructure_environment"
    assert row["child_label_he"] is None


def test_container_with_strong_environmental_bylaw_evidence_gets_environment_root_context() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "container",
            "structural_role": "body",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": "חוק העזר מובא לידיעה בהתאם לאישור השרה להגנת הסביבה בנושא פינוי אשפה",
            "raw_text": "חוק העזר מובא לידיעה בהתאם לאישור השרה להגנת הסביבה בנושא פינוי אשפה",
        },
        reason="container_heading",
    )

    assert row["root_topic_id"] == "root_infrastructure_environment"
    assert row["row_type"] == "container"
    assert row["child_label_he"] is None


def test_non_topic_committee_appointment_continuation_gets_admin_root_context() -> None:
    row = step4._non_topic_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "fragment",
            "structural_role": "body",
            "document_context": {"packet_role": "protocol"},
            "unit_raw_text": "להאריך את מינויה של אדריכלית פלונית כממלאת מקום בוועדה עד סוף השנה",
            "raw_text": "להאריך את מינויה של אדריכלית פלונית כממלאת מקום בוועדה עד סוף השנה",
        },
        reason="body_without_headline_topic_provenance",
    )

    assert row["root_topic_id"] == "root_administration"
    assert row["child_label_he"] is None


def test_unsupported_weak_candidate_does_not_choose_arbitrary_root_when_dicta_disabled() -> None:
    row = step4._candidate_review_assignment(
        item={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "topic_item",
            "structural_role": "outline_item",
            "is_topic_bearing": True,
            "topic_identification_context": "דוחות המובאים לאישור המועצה",
            "topic_headline_he": "דוחות המובאים לאישור המועצה",
            "document_context": {"packet_role": "protocol"},
            "raw_text": "דוחות המובאים לאישור המועצה",
            "deterministic_topic_decision": {
                "needs_dicta": True,
                "root_topic_id": "root_religious_services",
                "confidence": 0.45,
                "reason": "weak_candidate",
            },
            "root_topic_candidates": [
                {"root_topic_id": "root_religious_services", "root_label_he": "דת ושירותי דת", "score": 0.45, "matched_terms": []}
            ],
        },
        reason="dicta_disabled",
    )

    assert row["root_topic_id"] == "root_agenda_queries"
    assert row["row_type"] == "fragment"
    assert "unsupported_weak_candidate" in row["topic_assignment_route"]


def test_mayor_office_employment_subject_prefers_hr_over_mayor_updates() -> None:
    tree = step4.global_topic_tree_payload(existing_tree=None, attachment_contexts=[])
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "structural_role": "outline_item",
            "raw_text": "3.2 אישור מועצת העירייה להעסקת עובד במשרת אמון בלשכת ראש העיר",
        },
        facts=[],
        max_raw_chars=1000,
        attachment_contexts=[],
        document_context={"packet_role": "protocol", "topic_carrier_mode": "headline_topics"},
        topic_context={"topic_identification_text": "3.2 אישור מועצת העירייה להעסקת עובד במשרת אמון בלשכת ראש העיר", "context_source": "unit_heading"},
        document_child_candidates=[],
        topic_tree=tree,
        topic_index=step4.build_topic_profile_index(tree),
    )

    decision = item["deterministic_topic_decision"]
    assert decision["action"] == "choose_existing_topic"
    assert decision["root_topic_id"] == "root_hr_labor"
    assert decision["policy_id"] == "hr_employment_conditions"


def test_model_omission_uses_candidate_review_instead_of_active_guess() -> None:
    row = step4._fallback_assignment(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "topic_item",
            "structural_role": "body",
            "is_topic_bearing": True,
            "topic_subject_he": "מקור לכיסוי גירעון תקציבי",
            "topic_identification_context": "מקור לכיסוי גירעון תקציבי",
            "topic_headline_he": "מקור לכיסוי גירעון תקציבי",
            "document_context": {"packet_role": "protocol"},
            "raw_text": "דיון בנושא מקור לכיסוי גירעון תקציבי בפרויקט עירוני",
            "deterministic_topic_decision": {
                "needs_dicta": True,
                "root_topic_id": "root_budget_finance",
                "reason": "ambiguous_candidates",
            },
            "root_topic_candidates": [
                {"root_topic_id": "root_budget_finance", "root_label_he": "תקציב וכספים", "score": 0.8}
            ],
        },
        reason="model_omitted_unit",
    )

    assert row["root_topic_id"] == "root_budget_finance"
    assert row["topic_node_status"] == "candidate"
    assert row["child_label_he"] is None
    assert row["topic_reject_reason"] == "non_blocking_topic_review:model_omitted_unit:ambiguous_candidates"


def test_policy_match_inside_dialogue_fragment_is_non_topic() -> None:
    row = step4._assignment_from_candidate_finder(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "topic_item",
            "structural_role": "outline_item",
            "is_topic_bearing": True,
            "topic_subject_he": "לגבי תקציב, אם צריך, אולי לא צריך",
            "topic_identification_context": "לגבי תקציב, אם צריך, אולי לא צריך",
            "topic_headline_he": "לגבי תקציב, אם צריך, אולי לא צריך",
            "document_context": {"packet_role": "protocol"},
            "raw_text": "הצעה לסדר, איך אנחנו יכולים לקבל החלטות לגבי תקציב, אם צריך, אולי לא צריך",
            "deterministic_topic_decision": {
                "action": "choose_existing_topic",
                "needs_dicta": False,
                "confidence": 0.98,
                "reason": "strong_policy_match",
                "root_topic_id": "root_budget_finance",
                "root_label_he": "תקציב וכספים",
                "policy_id": "budget_line_or_reserve",
            },
            "root_topic_candidates": [],
            "candidate_child_topics": [],
        }
    )

    assert row["is_topic_bearing"] is False
    assert row["topic_assignment_route"].endswith("policy_match_inside_dialogue_fragment")


def test_contextual_valid_root_survives_noisy_post_cleanup() -> None:
    row = {
        "structure_unit_id": "u_contextual_noisy_heading",
        "semantic_unit_id": "u_contextual_noisy_heading",
        "packet_role": "protocol",
        "row_type": "topic_item",
        "structural_role": "outline_item",
        "is_topic_bearing": True,
        "topic_node_status": "active",
        "topic_subject_he": "מינוי מנכ\"ל חב' יובלים",
        "topic_headline_he": "מינוי מנכ\"ל חב' יובלים",
        "topic_identification_context": "מינוי מנכ\"ל חב' יובלים",
        "root_topic_id": "root_administration",
        "root_label_he": "מנהל עירוני ומינויים",
        "dicta_contextual_indexability_status": "standalone_topic",
        "dicta_contextual_event_status": "approved",
        "topic_assignment_route": "dictalm_v4_global_tree:dicta_contextual_root:child_choice:heading:canonical_topic_label",
    }

    normalized = step4._normalize_protocol_non_topic_assignments([row])[0]

    assert normalized["is_topic_bearing"] is True
    assert normalized["row_type"] == "topic_item"
    assert "post_non_topic" not in normalized["topic_assignment_route"]


def test_order_proposal_dialogue_only_is_non_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="הצעה לסדר, קיבלת תשובה, זה לא עובד ככה",
        raw_text="הצעה לסדר, קיבלת תשובה, זה לא עובד ככה",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "order_proposal_intro_only"


def test_policy_root_attaches_existing_child_candidate() -> None:
    row = step4._assignment_from_candidate_finder(
        {
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "topic_item",
            "structural_role": "outline_item",
            "is_topic_bearing": True,
            "topic_subject_he": "גני ילדים",
            "topic_identification_context": "גני ילדים",
            "topic_headline_he": "גני ילדים",
            "document_context": {"packet_role": "protocol"},
            "raw_text": "גני ילדים",
            "deterministic_topic_decision": {
                "action": "choose_existing_topic",
                "needs_dicta": False,
                "confidence": 0.98,
                "reason": "strong_policy_match",
                "root_topic_id": "root_education",
                "root_label_he": "חינוך",
                "policy_id": "parent_payments_education",
            },
            "root_topic_candidates": [],
            "candidate_child_topics": [
                {
                    "candidate_child_id": "child-education",
                    "label_he": "מוסדות חינוך",
                    "root_topic_id": "root_education",
                    "root_label_he": "חינוך",
                    "evidence_quote_he": "גני ילדים",
                    "evidence_source": "existing_tree",
                    "confidence_hint": 0.94,
                    "aliases_he": ["גני ילדים"],
                }
            ],
        }
    )

    assert row["root_topic_id"] == "root_education"
    assert row["child_label_he"] == "מוסדות חינוך"


def test_semantic_child_facets_return_existing_closed_list_children() -> None:
    topic_tree = {
        "root_topics": [
            {
                "root_topic_id": "root_budget_finance",
                "root_label_he": "תקציב וכספים",
                "children": [
                    {"child_topic_id": "c1", "child_label_he": "מימון פרויקטים עירוניים", "status": "active"},
                    {"child_topic_id": "c2", "child_label_he": "הנחות ופטורים", "status": "active"},
                    {"child_topic_id": "c3", "child_label_he": "תקצוב שירותים עירוניים", "status": "active"},
                ],
            },
            {
                "root_topic_id": "root_agreements",
                "root_label_he": "הסכמים והתקשרויות",
                "children": [{"child_topic_id": "c4", "child_label_he": "מכרזים והתקשרויות", "status": "active"}],
            },
            {
                "root_topic_id": "root_security_enforcement",
                "root_label_he": "ביטחון ואכיפה",
                "children": [{"child_topic_id": "c5", "child_label_he": "מוכנות לחירום", "status": "active"}],
            },
        ]
    }

    tbr = step4._candidate_child_topics(unit_id="u1", text="קרצוף כבישים, תב\"ר", evidence_text="קרצוף כבישים, תב\"ר", outline_title="", explicit_actions=[], document_context={"packet_role": "protocol"}, document_child_candidates=[], referenced_attachment_contexts=[], topic_tree=topic_tree, structural_role="outline_item")
    exemption = step4._candidate_child_topics(unit_id="u2", text="פטור לנכס שאינו ראוי לשימוש ולא ישולם היטל", evidence_text="פטור לנכס שאינו ראוי לשימוש ולא ישולם היטל", outline_title="", explicit_actions=[], document_context={"packet_role": "protocol"}, document_child_candidates=[], referenced_attachment_contexts=[], topic_tree=topic_tree, structural_role="outline_item")
    tender = step4._candidate_child_topics(unit_id="u3", text="אישור התקשרות עם זוכה במכרז פומבי", evidence_text="אישור התקשרות עם זוכה במכרז פומבי", outline_title="", explicit_actions=[], document_context={"packet_role": "protocol"}, document_child_candidates=[], referenced_attachment_contexts=[], topic_tree=topic_tree, structural_role="outline_item")
    emergency = step4._candidate_child_topics(unit_id="u4", text="דיון בנושא מיגון וחירום", evidence_text="דיון בנושא מיגון וחירום", outline_title="", explicit_actions=[], document_context={"packet_role": "protocol"}, document_child_candidates=[], referenced_attachment_contexts=[], topic_tree=topic_tree, structural_role="outline_item")

    assert any(row["label_he"] == "מימון פרויקטים עירוניים" for row in tbr)
    assert any(row["label_he"] == "הנחות ופטורים" for row in exemption)
    assert any(row["label_he"] == "מכרזים והתקשרויות" for row in tender)
    assert any(row["label_he"] == "מוכנות לחירום" for row in emergency)


def test_rejected_procedural_topic_subject_clears_child_label() -> None:
    raw = "שהתקבלו בפרוטוקולים של הועדות כוחם יפה והם בתוקף"
    row = step4._assignment_payload(
        item={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "row_type": "topic_item",
            "structural_role": "outline_item",
            "document_context": {"packet_role": "protocol"},
            "topic_identification_context": raw,
            "topic_headline_he": raw,
            "raw_text": raw,
            "source_region_ids": [],
            "source_block_ids": [],
        },
        root_topic_id="root_agreements",
        root_label="הסכמים והתקשרויות",
        child_label="מכרזים והתקשרויות",
        raw_child_label="מכרזים והתקשרויות",
        status="active",
        reject_reason=None,
        aliases=[],
        confidence=0.9,
        quote=raw,
        route="test",
        rationale_he="test",
        parsed_contract={"is_topic_bearing": True, "topic_subject_he": raw},
    )

    assert row["is_topic_bearing"] is False
    assert row["child_label_he"] is None


def test_dicta_prompt_tree_is_compact_and_child_context_is_per_item() -> None:
    payload = step4._topic_tree_prompt_payload(
        {
            "topic_tree_version": "test",
            "root_topics": [
                {
                    "root_topic_id": "root_budget_finance",
                    "root_label_he": "תקציב וכספים",
                    "keywords": ["תקציב"],
                    "profile": {"positive_examples": [{"quote_he": "long example"}]},
                    "children": [
                        {
                            "child_topic_id": "c1",
                            "child_label_he": "תקצוב שירותים עירוניים",
                            "profile": {"positive_examples": [{"quote_he": "child example"}]},
                        }
                    ],
                }
            ],
        }
    )

    assert payload["root_topics"][0]["child_count"] == 1
    assert "children" not in payload["root_topics"][0]
    assert "profile" not in payload["root_topics"][0]


def test_model_prompt_adds_compact_child_choices() -> None:
    items = step4._items_for_model_prompt(
        [
            {
                "structure_unit_id": "u1",
                "candidate_child_topics": [
                    {
                        "candidate_child_id": "child-1",
                        "root_topic_id": "root_budget_finance",
                        "root_label_he": "תקציב וכספים",
                        "label_he": "תקצוב שירותים עירוניים",
                        "evidence_quote_he": "תקציב הגיל הרך",
                        "evidence_source": "existing_tree",
                        "confidence_hint": 0.9,
                        "profile": {"summary_he": "long child profile"},
                    }
                ],
            }
        ]
    )

    assert items[0]["candidate_child_choices"][0]["candidate_child_id"] == "child-1"
    assert items[0]["candidate_child_choices"][0]["label_he"] == "תקצוב שירותים עירוניים"
    assert "profile" not in items[0]["candidate_child_choices"][0]


def test_cleaned_continuation_without_source_is_not_topic() -> None:
    unit = {"structural_role": "task_row", "raw_text": "מר כהן ממשיך לדבר על בית הספר והעירייה"}

    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="בית הספר הוא מיקרוקוסמוס של החברה בישראל",
        raw_text=unit["raw_text"],
        topic_context_source="unit_heading",
        headline_source="none",
        packet_role="protocol",
    ) == "missing_topic_headline_provenance"


def test_short_split_agenda_title_with_bounded_raw_signal_keeps_provenance() -> None:
    raw_text = "22 ) - .מצ\"ל 11 \".\"פרס חינוך עירוני שנתי לחינוך פורץ דרך באשדוד- דיון עפ\"י בקשת עו\"ד טובול- ( 29.5."
    unit = {
        "structural_role": "continuation",
        "raw_text": raw_text,
        "structure_evidence": {"split_reason": "multiple_structural_starts"},
    }

    assert step4._protocol_topic_provenance_reject_reason(
        unit=unit,
        headline="",
        raw_text=raw_text,
        topic_context_source="",
        headline_source="none",
        packet_role="protocol",
    ) is None


def test_committee_protocol_carrier_is_not_standalone_topic() -> None:
    assert step4._looks_like_procedural_carrier_heading("פרוטוקול הוועדה המקצועית מספר") is True
    assert step4._non_topic_protocol_reason(headline="פרוטוקול הוועדה המקצועית מספר", raw_text="פרוטוקול הוועדה המקצועית מספר", structural_role="outline_item", packet_role="protocol") == "procedural_carrier_heading"
    assert step4._looks_like_procedural_carrier_heading("פרוטוקול ועדת תמיכות") is False


def test_query_attribution_without_subject_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="שאילתה: יובל צלנר, חבר מועצה",
        raw_text="שאילתה: יובל צלנר, חבר מועצה",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "query_attribution_only"


def test_query_intro_without_subject_is_not_topic() -> None:
    text = "שאילתה: אורנה ברביבאי, חברת מועצה : היו\"ר-מר שפירא . של חברת המועצה אורנה ברביבאי אדוני ראש העיר, שאילתה מס"
    assert step4._non_topic_protocol_reason(
        headline=text,
        raw_text=text,
        structural_role="outline_item",
        packet_role="protocol",
    ) == "query_attribution_only"


def test_query_intro_speaker_prompt_without_subject_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="אדוני ראש העיר, שאילתה ראשונה בבקשה",
        raw_text="אדוני ראש העיר, שאילתה ראשונה בבקשה",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "query_intro_only"


def test_query_carrier_person_dash_subject_prefers_real_subject() -> None:
    contract = step4._topic_contract_from_headline(
        "שאילתה: יובל צלנר, חבר המועצה – בעיית המפונים שבתיהם נפגעו",
        structural_role="outline_item",
        packet_role="protocol",
    )

    assert contract["is_topic_bearing"] is True
    assert contract["agenda_carrier_he"] == "שאילתה"
    assert contract["topic_subject_he"] == "בעיית המפונים שבתיהם נפגעו"
    assert step4.infer_root_topic_id(contract["topic_subject_he"]) == "root_welfare_social"


def test_query_subject_after_benosheh_prefers_evacuated_residents() -> None:
    contract = step4._topic_contract_from_headline(
        "שאילתה של יובל צלנר בנושא פינוי תושבים מבתים שנפגעו",
        structural_role="outline_item",
        packet_role="protocol",
    )

    assert contract["is_topic_bearing"] is True
    assert contract["topic_subject_he"] == "פינוי תושבים מבתים שנפגעו"
    assert step4.infer_root_topic_id(contract["topic_subject_he"]) == "root_welfare_social"


def test_order_proposal_person_dash_subject_prefers_real_subject() -> None:
    contract = step4._topic_contract_from_headline(
        "הצעה לסדר: חברת מועצה – טיפול בדרי רחוב",
        structural_role="outline_item",
        packet_role="protocol",
    )

    assert contract["is_topic_bearing"] is True
    assert contract["agenda_carrier_he"] == "הצעה לסדר"
    assert contract["topic_subject_he"] == "טיפול בדרי רחוב"
    assert step4.infer_root_topic_id(contract["topic_subject_he"]) == "root_welfare_social"


def test_order_proposal_intro_without_subject_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="הצעה לסדר היום, אני אגיד לך על",
        raw_text="הצעה לסדר היום, אני אגיד לך על",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "order_proposal_intro_only"


def test_explicit_visual_header_is_not_overwritten_by_raw_region_repair() -> None:
    item = step4._build_item(
        unit={
            "structure_unit_id": "u1",
            "semantic_unit_id": "u1",
            "page": 1,
            "header_text": "בניינים מסוכנים בעיר",
            "raw_text": "חבר מועצה פלוני – בניינים מסוכנים בעיר .1 :מר היו\"ר .הצעה לסדר יום",
            "structural_role": "outline_item",
        },
        facts=[],
        max_raw_chars=700,
        attachment_contexts=[],
        document_context={"packet_role": "protocol"},
        topic_context={"topic_identification_text": "בניינים מסוכנים בעיר", "context_source": "unit_heading"},
        document_child_candidates=[],
        topic_tree=step4.global_topic_tree_payload(),
        topic_index={},
    )

    assert item["topic_headline_he"] == "בניינים מסוכנים בעיר"
    assert item["topic_identification_context"] == "בניינים מסוכנים בעיר"


def test_signature_page_with_inherited_context_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="אישור מועצת העירייה וכן לחתום על חוזה להקמת מבנה הציבור",
        raw_text="הישיבה נעולה הישיבה ננעלה בשעה 18:39 תצלום חתימות יו\"ר הישיבה ומנכ\"ל העירייה",
        structural_role="body",
        packet_role="protocol",
    ) == "signature_or_end_page_fragment"


def test_appendix_contract_approval_procedure_is_not_topic() -> None:
    assert step4._non_topic_protocol_reason(
        headline="ח) הנ\"ל) יובאו לאישור ועדת התקשרויות עליונה ההתקשרות בחוזה ללא מכרז כאמור בתקנה נספח ג",
        raw_text="ח) הנ\"ל) יובאו לאישור ועדת התקשרויות עליונה ההתקשרות בחוזה ללא מכרז כאמור בתקנה נספח ג",
        structural_role="outline_item",
        packet_role="protocol",
    ) == "contract_approval_procedure_fragment"


def test_approval_to_sign_contract_cleans_to_actual_subject() -> None:
    assert step4.clean_protocol_subject_text("אישור מועצת העירייה וכן לחתום על חוזה להקמת מבנה הציבור") == "הקמת מבנה הציבור"


def test_appendix_request_prefix_cleans_to_actual_subject() -> None:
    assert step4.clean_protocol_subject_text("נספח ב . בקשה לאישור ניהול משא ומתן עם ספקים פוטנציאליים לצורך התקשרות ללא מכרז, לאור אי") == "ניהול משא ומתן עם ספקים פוטנציאליים לצורך התקשרות ללא מכרז"


def test_weak_body_agenda_candidate_becomes_evidence_only_fragment() -> None:
    row = {
        "packet_role": "protocol",
        "structure_unit_id": "u1",
        "semantic_unit_id": "u1",
        "structural_role": "body",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "topic_node_status": "candidate",
        "root_topic_id": "root_agenda_queries",
        "root_label_he": "סדר יום ושאילתות",
        "topic_subject_he": "השכירות, נושא מאוד חשוב, מעבר לדירות למגורים",
        "topic_headline_he": "השכירות, נושא מאוד חשוב, מעבר לדירות למגורים",
        "topic_assignment_route": "deterministic_v4_candidate_review:topic_subject_collapsed_to_procedural_root",
    }

    normalized = step4._normalize_protocol_non_topic_assignments([row])[0]

    assert normalized["row_type"] == "fragment"
    assert normalized["is_topic_bearing"] is False
    assert normalized["topic_node_status"] == "active"
    assert normalized["topic_subject_he"] is None


def test_unknown_sports_root_aligns_to_allowed_culture_sport_root() -> None:
    item = {
        "is_topic_bearing": True,
        "document_context": {"packet_role": "protocol"},
        "topic_headline_he": "הפועל אשדוד כדוריד מחזיקת גביע המדינה ואלופת המדינה",
    }
    parsed = {
        "root_topic_id": "root_sports_culture",
        "topic_supporting_quote_he": "הפועל אשדוד כדוריד מחזיקת גביע המדינה",
        "rationale_he": "נושא זה עוסק בקבוצת ספורט מקומית ובהישגיה",
    }

    assert step4._align_unknown_dicta_root(item=item, parsed=parsed, subject=item["topic_headline_he"]) == "root_culture_sport"


def test_procedural_dicta_root_recovers_local_economy_root() -> None:
    item = {
        "is_topic_bearing": True,
        "document_context": {"packet_role": "protocol"},
        "raw_text": "שאילתה בנושא העתקת חלק ניכר ממפעל אלתא באשדוד לבאר שבע ומעבר עובדים",
        "root_topic_candidates": [],
    }

    recovered = step4._recover_root_only_topic(
        item=item,
        parsed={"root_topic_id": "root_agenda_queries"},
        subject="העתקת חלק ניכר ממפעל אלתא באשדוד לבאר שבע ומעבר עובדים",
        fallback_root_topic_id="root_agenda_queries",
    )

    assert recovered == "root_local_economy"


def test_procedural_dicta_root_recovers_notice_board_policy_root_from_subject_span() -> None:
    item = {
        "is_topic_bearing": True,
        "document_context": {"packet_role": "protocol"},
        "raw_text": "שאילתא בנושא העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
        "root_topic_candidates": [],
    }

    recovered = step4._recover_root_only_topic(
        item=item,
        parsed={"root_topic_id": "root_agenda_queries"},
        subject="העמדת לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל",
        fallback_root_topic_id="root_transport_safety",
    )

    assert recovered == "root_infrastructure_environment"


def test_body_root_evidence_prefers_transport_over_weak_infrastructure() -> None:
    item = {
        "document_context": {"packet_role": "protocol"},
        "structural_role": "task_row",
        "topic_subject_he": "עדכון ראש העיר",
        "topic_headline_he": "עדכון ראש העיר",
        "raw_text": "המשטרה העבירה תחקיר, ונפגש עם יו\"ר הרלב\"ד ומשרד התחבורה כדי למנוע תאונות דרכים בכביש ובצמתים",
    }

    assert step4._strong_body_evidence_root(item) == "root_transport_safety"


def test_single_hebrew_keyword_alignment_requires_whole_token() -> None:
    assert step4._alignment_phrase_score("רב", step4._norm("יו\"ר הרלב\"ד הארצי")) == 0.0
    assert step4._alignment_phrase_score("מים", step4._norm("מתחת לשמים הפתוחים")) == 0.0
    assert step4._alignment_phrase_score("משטרה", step4._norm("בדיקות של המשטרה")) == 0.9


def test_tax_discount_committee_aligns_to_finance_root() -> None:
    assert step4.infer_root_topic_id("ועדת הנחות במיסים ומוסד מתנדב") == "root_budget_finance"


def test_canonical_subjects_align_to_non_procedural_roots() -> None:
    assert step4.infer_root_topic_id("מחזור והסברה סביבתית") == "root_infrastructure_environment"
    assert step4.infer_root_topic_id("אבטחת מידע והגנת פרטיות") == "root_security_enforcement"
    assert step4.infer_root_topic_id("טיפול בדרי רחוב") == "root_welfare_social"
    assert step4.infer_root_topic_id("הנחת מבנה יביל") == "root_planning_building"
    assert step4.infer_root_topic_id("בניית מבני ציבור ומתנ\"סים") == "root_planning_building"
    assert step4.infer_root_topic_id("מתנ\"ס רובע ט\"ו") == "root_planning_building"
    assert step4.infer_root_topic_id("כיתות אזרחים ותיקים") == "root_education"
    assert step4.infer_root_topic_id("פרס חינוך") == "root_education"
    assert step4.infer_root_topic_id("מענקי ספורט") == "root_supports"
    assert step4.infer_root_topic_id("שיפוץ חוף") == "root_planning_building"
    assert step4.infer_root_topic_id("לוח מודעות אלקטרוני בצומת הרחובות שדרות בגין ושדרות הרצל") == "root_infrastructure_environment"
    assert step4.infer_root_topic_id("ציוד מגן אישי לעובדים") == "root_hr_labor"
    assert step4.infer_root_topic_id("החזר תשלומי הורים") == "root_education"
    assert step4.infer_root_topic_id("הטבות בעקבות ירידה במדד חברתי-כלכלי") == "root_budget_finance"
    assert step4.infer_root_topic_id("סיוע לביטחון תזונתי") == "root_welfare_social"
    assert step4.infer_root_topic_id("מאגר מסרונים לתושבים") == "root_administration"
    assert step4.infer_root_topic_id("מסרי וידאו לתושבים") == "root_administration"
    assert step4.infer_root_topic_id("לוחות פרסום אלקטרוניים") == "root_infrastructure_environment"
    assert step4.infer_root_topic_id("מוכנות לרעידת אדמה ומערכת התראה") == "root_security_enforcement"
    assert step4.infer_root_topic_id("ועדה למיגור תופעת האלימות") == "root_security_enforcement"
    assert step4.infer_root_topic_id("טיפול בגורי חתולי רחוב") == "root_welfare_social"
    assert step4.infer_root_topic_id("תוכנית מדעניות העתיד") == "root_education"
    assert step4.infer_root_topic_id("קביעת שיעור היטל השבחה בפרויקטים לפינוי בינוי") == "root_budget_finance"
    assert step4.infer_root_topic_id("שימוע למהנדס העיר") == "root_administration"
    assert step4.infer_root_topic_id("אלרגיות ואפיפן במרחב הציבורי") == "root_security_enforcement"
    assert step4.infer_root_topic_id("הקמת פסל ציבורי") == "root_culture_sport"


def test_contextual_service_domain_corrects_beneficiary_root_for_school_classes() -> None:
    override = step4._contextual_validation_root_override(
        item={"topic_headline_he": "ייסוד והקמת כיתות אזרחים וותיקים בביה\"ס התיכוניים"},
        parsed={
            "is_topic_bearing": True,
            "root_topic_id": "root_welfare_social",
            "topic_subject_he": "אזרחים ותיקים",
            "clean_subject_he": "אזרחים ותיקים",
            "primary_action_he": "הקמת כיתות אזרחים ותיקים",
            "service_domain_he": "חינוך",
        },
        normalized_event={
            "primary_action_he": "הקמת כיתות אזרחים ותיקים",
            "clean_subject_he": "כיתות אזרחים ותיקים",
            "service_domain_he": "חינוך",
            "event_summary_he": "דיון בהקמת כיתות אזרחים ותיקים בבתי ספר תיכוניים",
        },
    )

    assert override == "root_education"


def test_child_only_judge_applies_same_root_existing_child() -> None:
    item = {
        "structure_unit_id": "u1",
        "semantic_unit_id": "u1",
        "document_context": {"packet_role": "protocol"},
        "topic_identification_context": "פטור לנכס שאינו ראוי לשימוש בשל נזק מלחמה",
        "topic_headline_he": "פטור לנכס שאינו ראוי לשימוש בשל נזק מלחמה",
        "topic_subject_he": "פטור לנכס בשל נזק מלחמה",
        "raw_text": "פטור לנכס שאינו ראוי לשימוש בשל נזק מלחמה",
        "explicit_actions": [],
    }
    assignment = {
        "structure_unit_id": "u1",
        "root_topic_id": "root_budget_finance",
        "root_label_he": "תקציב וכספים",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "topic_node_status": "active",
        "topic_supporting_quote_he": "פטור לנכס שאינו ראוי לשימוש בשל נזק מלחמה",
        "topic_assignment_route": "dictalm_v4_global_tree:root_only",
    }
    candidate = {
        "candidate_child_id": "c1",
        "root_topic_id": "root_budget_finance",
        "root_label_he": "תקציב וכספים",
        "label_he": "הנחות ופטורים",
        "evidence_source": "existing_tree",
        "evidence_quote_he": "פטור לנכס שאינו ראוי לשימוש בשל נזק מלחמה",
        "confidence_hint": 0.9,
        "aliases_he": [],
    }

    updated, error = step4._assignment_with_child_only_decision(
        item=item,
        assignment=assignment,
        decision={"structure_unit_id": "u1", "child_choice_id": "c1", "confidence": 0.88, "rationale_he": "הפריט עוסק בפטור"},
        candidate_child_choices=[candidate],
    )

    assert error is None
    assert updated["root_topic_id"] == "root_budget_finance"
    assert updated["child_label_he"] == "הנחות ופטורים"
    assert ":child_only_dicta:existing_tree" in updated["topic_assignment_route"]


def test_topic_subject_v3_hint_requires_accepted_entailed_primary_anchor() -> None:
    row = {
        "artifact_id": "json_900000_49_s0009_01_5f79c9055981",
        "quality_status": "accepted",
        "row_role": "action_anchor",
        "event_role": "primary",
        "source_ordinal": 49,
        "raw_text_he": "שאילתה בנושא מוסדות חינוך לציבור הדתי לאומי",
        "event_payload": {
            "matter_he": "מוסדות חינוך לציבור הדתי לאומי",
            "action_type_he": "מענה לשאילתה",
            "event_phase": "response_given",
            "v3_evidence_entailment": {
                "entailment_status": "entailed",
                "field_assessments": {
                    "matter_he": {"status": "entailed", "source_quote_he": "שאילתה בנושא מוסדות חינוך לציבור הדתי לאומי"},
                    "action_type_he": {"status": "entailed", "source_quote_he": "הוקראה תשובת ראש העיר"},
                },
            },
        },
    }

    hint = step4._topic_subject_v3_hint_from_row(row)

    assert hint is not None
    assert hint["matter_he"] == "מוסדות חינוך לציבור הדתי לאומי"
    assert step4._topic_subject_v3_hint_from_row({**row, "quality_status": "needs_review"}) is None
    keys = step4._topic_subject_v3_hint_keys(row=row)
    assert keys[:2] == ["unit:s0009_01_5f79c9055981", "ordinal:49"]
    assert keys[2].startswith("raw:")


def test_topic_subject_v3_hint_accepts_nested_model_prediction_without_entailment() -> None:
    row = {
        "artifact_id": "json_900000_222_vh006_07_01_966a6a09f777",
        "quality_status": "accepted",
        "row_role": "action_anchor",
        "event_role": "primary",
        "source_ordinal": 222,
        "raw_text_he": "המתנ\"ס. שתושבי רובע ט\"ו מחכים לו כל כך הרבה שנים",
        "subject_metadata": {
            "matter_he": "המתנ\"ס שתושבי רובע ט\"ו מחכים לו כל כך הרבה שנים",
            "matter_display_he": "מתנ\"ס לרובע ט\"ו",
        },
        "event_metadata": {"action_type_he": "מענה לשאילתה"},
        "model_prediction": {
            "matter_he": "המתנ\"ס שתושבי רובע ט\"ו מחכים לו כל כך הרבה שנים",
            "matter_display_he": "מתנ\"ס לרובע ט\"ו",
            "action_type_he": "מענה לשאילתה",
        },
    }

    hint = step4._topic_subject_v3_hint_from_row(row)

    assert hint is not None
    assert hint["source_structure_unit_id"] == "vh006_07_01_966a6a09f777"
    assert hint["matter_he"] == "המתנ\"ס שתושבי רובע ט\"ו מחכים לו כל כך הרבה שנים"
    assert hint["matter_display_he"] == "מתנ\"ס לרובע ט\"ו"


def test_topic_subject_v3_hint_accepts_top_level_primary_target_with_supporting_source_rows() -> None:
    row = {
        "artifact_id": "json_900000_8_s0003_07_6a62f1711b20",
        "validation_status": "accepted",
        "event_payload": {
            "is_event": True,
            "target_row_role": "action_anchor",
            "matter_he": "כיכר רמון בעיר ודרך מנחם בגין",
            "action_type_he": "שאילתה",
            "v3_evidence_entailment": {
                "entailment_status": "entailed",
                "field_assessments": {
                    "matter_he": {"status": "entailed", "source_quote_he": "כיכר רמון בעיר ודרך מנחם בגין"},
                    "action_type_he": {"status": "entailed", "source_quote_he": "שאילתה של ד\"ר לחמני בנושא כיכר רמון"},
                },
            },
        },
        "event_source_rows": [
            {"row_role": "dependent_detail", "event_role": "supporting"},
        ],
    }

    hint = step4._topic_subject_v3_hint_from_row(row)

    assert hint is not None
    assert hint["source_structure_unit_id"] == "s0003_07_6a62f1711b20"
    assert hint["event_role"] == "primary"


def test_topic_subject_v3_hint_matches_unit_id_without_root_overwrite() -> None:
    hint = {
        "source": "topic_subject_v3",
        "matter_he": "מוסדות חינוך לציבור הדתי לאומי",
        "action_type_he": "מענה לשאילתה",
        "event_phase": "response_given",
        "source_quote_he": "שאילתה בנושא מוסדות חינוך לציבור הדתי לאומי",
    }
    index = {"unit:s0009_01_5f79c9055981": [hint]}

    hints = step4._topic_subject_v3_hints_for_unit(
        unit={},
        unit_id="s0009_01_5f79c9055981",
        raw_text="שאילתה בנושא מוסדות חינוך לציבור הדתי לאומי",
        hint_index=index,
    )

    assert hints == [hint]
    assert "root_topic_id" not in hints[0]


def test_child_only_judge_rejects_cross_root_choice() -> None:
    item = {
        "structure_unit_id": "u1",
        "document_context": {"packet_role": "protocol"},
        "topic_identification_context": "עתיד מינויים ושינויים בוועדת מכרזים",
        "topic_headline_he": "עתיד מינויים ושינויים בוועדת מכרזים",
        "raw_text": "עתיד מינויים ושינויים בוועדת מכרזים",
        "explicit_actions": [],
    }
    assignment = {
        "structure_unit_id": "u1",
        "root_topic_id": "root_administration",
        "root_label_he": "מנהל עירוני ומינויים",
        "row_type": "topic_item",
        "is_topic_bearing": True,
        "topic_node_status": "active",
        "topic_assignment_route": "dictalm_v4_global_tree:root_only",
    }
    candidate = {
        "candidate_child_id": "c_wrong_root",
        "root_topic_id": "root_agreements",
        "root_label_he": "הסכמים והתקשרויות",
        "label_he": "מכרזים והתקשרויות",
        "evidence_source": "existing_tree",
        "evidence_quote_he": "ועדת מכרזים",
    }

    updated, error = step4._assignment_with_child_only_decision(
        item=item,
        assignment=assignment,
        decision={"structure_unit_id": "u1", "child_choice_id": "c_wrong_root", "confidence": 0.9},
        candidate_child_choices=[candidate],
    )

    assert updated["root_topic_id"] == "root_administration"
    assert updated.get("child_label_he") is None
    assert error["error_code"] == "CHILD_MODEL_ROOT_MISMATCH"


def test_child_only_candidate_choices_are_fixed_to_assignment_root() -> None:
    item = {
        "candidate_child_topics": [
            {
                "candidate_child_id": "c_wrong_root",
                "root_topic_id": "root_agreements",
                "root_label_he": "הסכמים והתקשרויות",
                "label_he": "מכרזים והתקשרויות",
                "evidence_source": "existing_tree",
            }
        ]
    }
    assignment = {"root_topic_id": "root_administration", "root_label_he": "מנהל עירוני ומינויים"}

    assert step4._fixed_root_child_candidate_rows(item=item, assignment=assignment) == []


def test_committee_appointments_do_not_become_procurement_child() -> None:
    item = {
        "structure_unit_id": "u1",
        "semantic_unit_id": "u1",
        "document_context": {"packet_role": "protocol"},
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "skip_model_assignment": False,
        "topic_identification_context": "עתיד מינויים ושינויים בוועדת מכרזים",
        "topic_headline_he": "עתיד מינויים ושינויים בוועדת מכרזים",
        "topic_subject_he": "עתיד מינויים ושינויים בוועדת מכרזים",
        "raw_text": "עתיד מינויים ושינויים בוועדת מכרזים",
        "explicit_actions": [],
        "candidate_child_topics": [
            {
                "candidate_child_id": "c_tenders",
                "root_topic_id": "root_agreements",
                "root_label_he": "הסכמים והתקשרויות",
                "label_he": "מכרזים והתקשרויות",
                "evidence_source": "existing_tree",
                "evidence_quote_he": "עתיד מינויים ושינויים בוועדת מכרזים",
                "confidence_hint": 0.86,
            }
        ],
        "root_topic_candidates": [
            {"root_topic_id": "root_agreements", "score": 0.84},
            {"root_topic_id": "root_administration", "score": 0.84},
        ],
    }
    parsed = {
        "root_topic_id": "root_agreements",
        "is_topic_bearing": True,
        "topic_subject_he": "עתיד מינויים ושינויים בוועדת מכרזים",
        "clean_subject_he": "עתיד מינויים ושינויים בוועדת מכרזים",
        "topic_supporting_quote_he": "עתיד מינויים ושינויים בוועדת מכרזים",
        "confidence": 0.82,
        "rationale_he": "הנושא עוסק בוועדת מכרזים",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_administration"
    assert assignment["child_label_he"] is None
    assert "committee_governance_override" in assignment["root_adjudication_decision"]


def test_valid_dicta_root_is_not_overridden_by_canonical_root_guess() -> None:
    item = {
        "structure_unit_id": "u1",
        "semantic_unit_id": "u1",
        "document_context": {"packet_role": "protocol"},
        "structural_role": "outline_item",
        "row_type": "topic_item",
        "skip_model_assignment": False,
        "topic_identification_context": "חזרת מיזם חופי לפעילות מלאה בחופי העיר",
        "topic_headline_he": "חזרת מיזם חופי לפעילות מלאה בחופי העיר",
        "topic_subject_he": "חזרת מיזם חופי לפעילות מלאה בחופי העיר",
        "raw_text": "חזרת מיזם חופי לפעילות מלאה בחופי העיר",
        "explicit_actions": [],
        "candidate_child_topics": [],
        "root_topic_candidates": [],
        "deterministic_topic_decision": {"action": "low_confidence", "needs_dicta": True, "reason": "no_candidate"},
    }
    parsed = {
        "root_topic_id": "root_culture_sport",
        "is_topic_bearing": True,
        "topic_subject_he": "חזרת מיזם חופי לפעילות מלאה בחופי העיר",
        "clean_subject_he": "חזרת מיזם חופי לפעילות מלאה בחופי העיר",
        "topic_supporting_quote_he": "חזרת מיזם חופי לפעילות מלאה בחופי העיר",
        "confidence": 0.82,
        "rationale_he": "מיזם פנאי בחופים",
    }

    assignment = step4._assignment_from_parsed(item=item, parsed=parsed)

    assert assignment["root_topic_id"] == "root_culture_sport"
    assert assignment["topic_subject_he"] == "פעילות בחופים"


def test_child_only_judge_skips_non_topic_rows(monkeypatch) -> None:
    def fail_call(**_kwargs):
        raise AssertionError("child-only judge should not be called for non-topic rows")

    monkeypatch.setattr(step4, "_call_child_only_dictalm", fail_call)
    assignments, errors, count = step4._apply_child_only_dicta_judgements(
        assignments=[
            {
                "structure_unit_id": "u1",
                "root_topic_id": "root_budget_finance",
                "root_label_he": "תקציב וכספים",
                "row_type": "fragment",
                "is_topic_bearing": False,
                "topic_node_status": "active",
            }
        ],
        items=[
            {
                "structure_unit_id": "u1",
                "candidate_child_topics": [
                    {
                        "candidate_child_id": "c1",
                        "root_topic_id": "root_budget_finance",
                        "label_he": "הנחות ופטורים",
                        "evidence_source": "existing_tree",
                    }
                ],
            }
        ],
        model="unused",
        base_url="http://localhost:1",
        timeout_seconds=1.0,
        model_call_dir=Path("/tmp"),
    )

    assert count == 0
    assert errors == []
    assert assignments[0].get("child_label_he") is None


def test_child_only_judge_runs_for_active_root_only_same_root_candidate(monkeypatch) -> None:
    def fake_call(**kwargs):
        assert kwargs["assignment"]["root_topic_id"] == "root_budget_finance"
        assert kwargs["candidate_child_choices"][0]["candidate_child_id"] == "c_budget"
        return {"decisions": [{"structure_unit_id": "u1", "fixed_root_topic_id": "root_budget_finance", "child_choice_id": "c_budget", "confidence": 0.91, "rationale_he": "תקצוב שירות עירוני"}]}

    monkeypatch.setattr(step4, "_call_child_only_dictalm", fake_call)
    assignments, errors, count = step4._apply_child_only_dicta_judgements(
        assignments=[
            {
                "structure_unit_id": "u1",
                "root_topic_id": "root_budget_finance",
                "root_label_he": "תקציב וכספים",
                "row_type": "topic_item",
                "is_topic_bearing": True,
                "topic_node_status": "active",
                "topic_supporting_quote_he": "תקציב שירותי גיל הרך",
                "topic_assignment_route": "dictalm_v4_global_tree:root_only",
            }
        ],
        items=[
            {
                "structure_unit_id": "u1",
                "semantic_unit_id": "u1",
                "document_context": {"packet_role": "protocol"},
                "structural_role": "outline_item",
                "topic_identification_context": "תקציב שירותי גיל הרך",
                "topic_headline_he": "תקציב שירותי גיל הרך",
                "topic_subject_he": "תקציב שירותי גיל הרך",
                "raw_text": "תקציב שירותי גיל הרך",
                "explicit_actions": [],
                "candidate_child_topics": [
                    {
                        "candidate_child_id": "c_budget",
                        "root_topic_id": "root_budget_finance",
                        "root_label_he": "תקציב וכספים",
                        "label_he": "תקצוב שירותים עירוניים",
                        "evidence_source": "existing_tree",
                        "evidence_quote_he": "תקציב שירותי גיל הרך",
                        "confidence_hint": 0.86,
                    },
                    {
                        "candidate_child_id": "c_wrong_root",
                        "root_topic_id": "root_agreements",
                        "root_label_he": "הסכמים והתקשרויות",
                        "label_he": "מכרזים והתקשרויות",
                        "evidence_source": "existing_tree",
                        "evidence_quote_he": "תקציב שירותי גיל הרך",
                        "confidence_hint": 0.9,
                    },
                ],
            }
        ],
        model="unused",
        base_url="http://localhost:1",
        timeout_seconds=1.0,
        model_call_dir=Path("/tmp"),
    )

    assert count == 1
    assert errors == []
    assert assignments[0]["root_topic_id"] == "root_budget_finance"
    assert assignments[0]["child_label_he"] == "תקצוב שירותים עירוניים"
    assert ":child_only_dicta:existing_tree" in assignments[0]["topic_assignment_route"]
