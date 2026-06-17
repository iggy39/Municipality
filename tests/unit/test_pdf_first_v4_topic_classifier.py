from __future__ import annotations

from municipality.pdf_first_v4_topic_classifier import build_topic_profile_index, find_topic_candidates
from municipality.pdf_first_v4_topic_policy import topic_policy_matches
from municipality.chunking import normalize_for_search
from municipality.pdf_first_v4_topic_tree import CURATED_V4_CHILD_TOPICS, ROOT_BY_ID, global_topic_tree_payload, semantic_root_for_child_label


def test_candidate_finder_accepts_policy_match_without_dicta() -> None:
    text = "הודעה בדבר הפעלת שירותי שמירה וגביית היטל שמירה"
    result = find_topic_candidates(
        text=text,
        topic_tree=global_topic_tree_payload(),
        policy_matches=topic_policy_matches(text),
        is_topic_bearing=True,
    )

    decision = result["decision"]
    assert decision["action"] == "choose_existing_topic"
    assert decision["needs_dicta"] is False
    assert decision["root_topic_id"] == "root_guard_services"


def test_candidate_finder_accepts_strong_existing_child() -> None:
    result = find_topic_candidates(
        text="אישור בנושא שמירה והיטל שמירה",
        topic_tree=global_topic_tree_payload(),
        child_candidates=[
            {
                "candidate_child_id": "child-1",
                "label_he": "שמירה והיטל שמירה",
                "root_topic_id": "root_guard_services",
                "root_label_he": "שמירה והיטלים",
                "confidence_hint": 0.9,
                "evidence_source": "existing_tree",
            }
        ],
        is_topic_bearing=True,
    )

    decision = result["decision"]
    assert decision["action"] == "choose_existing_topic"
    assert decision["child_choice_id"] == "child-1"
    assert decision["needs_dicta"] is False


def test_candidate_finder_sends_weak_match_to_dicta() -> None:
    result = find_topic_candidates(
        text="דיון בנושא עירוני חדש שאינו דומה לשורשים הידועים",
        topic_tree=global_topic_tree_payload(),
        is_topic_bearing=True,
    )

    decision = result["decision"]
    assert decision["action"] in {"low_confidence", "needs_judge"}
    assert decision["needs_dicta"] is True


def test_indexed_candidate_finder_downranks_procedural_carrier() -> None:
    tree = global_topic_tree_payload()
    index = build_topic_profile_index(tree)

    result = find_topic_candidates(
        text='שאילתה בנושא כמות הדירות שאוכלסו וקיבלו טופס במסגרת הסכם הגג',
        topic_tree=tree,
        topic_index=index,
        is_topic_bearing=True,
    )

    candidates = result["candidates"]
    assert candidates[0]["root_topic_id"] == "root_planning_building"
    assert any(row["root_topic_id"] == "root_agenda_queries" and row.get("carrier_penalty") for row in candidates)


def test_indexed_candidate_finder_retrieves_auditorium_as_culture() -> None:
    tree = global_topic_tree_payload()
    index = build_topic_profile_index(tree)

    result = find_topic_candidates(
        text='שאילתה בנושא פתיחה מחדש של אודיטוריום על שם רמי נעים ז"ל',
        topic_tree=tree,
        topic_index=index,
        is_topic_bearing=True,
    )

    assert result["candidates"][0]["root_topic_id"] == "root_culture_sport"


def test_policy_routes_municipal_tax_to_finance() -> None:
    matches = topic_policy_matches("ביטול תוספת הארנונה לשנת 2022 לתושבי העיר")

    assert matches[0]["root_topic_id"] == "root_budget_finance"


def test_policy_routes_local_factory_relocation_to_economy() -> None:
    matches = topic_policy_matches("העתקת חלק ניכר ממפעל אלתא באשדוד לבאר שבע ומעבר עובדים")

    assert matches[0]["root_topic_id"] == "root_local_economy"


def test_policy_routes_road_safety_and_speed_bumps_to_transport() -> None:
    matches = topic_policy_matches("הצבת באמפרים והורדת מהירות כדי למנוע תאונות דרכים")

    assert matches[0]["root_topic_id"] == "root_transport_safety"


def test_short_hebrew_keyword_does_not_match_inside_longer_word() -> None:
    tree = global_topic_tree_payload()
    index = build_topic_profile_index(tree)

    result = find_topic_candidates(
        text="עתודת חטיבת התפעול",
        topic_tree=tree,
        topic_index=index,
        is_topic_bearing=True,
    )

    assert all(row["root_topic_id"] != "root_religious_services" for row in result["candidates"])


def test_short_hebrew_keyword_allows_attached_prefix() -> None:
    tree = global_topic_tree_payload()
    index = build_topic_profile_index(tree)

    result = find_topic_candidates(
        text="דיון בנושא הדת והשירותים הדתיים בעיר",
        topic_tree=tree,
        topic_index=index,
        is_topic_bearing=True,
    )

    assert any(row["root_topic_id"] == "root_religious_services" for row in result["candidates"])


def test_policy_routes_business_bylaw_to_local_economy() -> None:
    matches = topic_policy_matches("תיקון התוספת לחוק עזר עירוני בנושא רוכלות ודוכנים")

    assert matches[0]["root_topic_id"] == "root_local_economy"


def test_policy_routes_fiscal_exemption_to_finance() -> None:
    matches = topic_policy_matches("פטור לנכס שאינו ראוי לשימוש ולא ישולם היטל בגינו")

    assert matches[0]["root_topic_id"] == "root_budget_finance"


def test_policy_routes_numbered_fiscal_clause_to_finance() -> None:
    matches = topic_policy_matches("קבע מנהל הארנונה כי נכס שנהרס לא ראוי לשימוש ולא ישולם בגין נכס כאמור היטל")

    assert matches[0]["root_topic_id"] == "root_budget_finance"
    assert matches[0]["policy_id"] == "fiscal_exemption_or_relief_finance"


def test_policy_routes_public_space_trees_to_environment() -> None:
    matches = topic_policy_matches("הפסקת שתילת עצי פיקוס חדשים במרחב הציבורי בעיר")

    assert matches[0]["root_topic_id"] == "root_infrastructure_environment"
    assert matches[0]["policy_id"] == "urban_greening_public_space_environment"


def test_policy_matches_hebrew_construct_form_to_budget_reserve() -> None:
    matches = topic_policy_matches("עתודת חטיבת התפעול")

    assert matches[0]["root_topic_id"] == "root_budget_finance"
    assert matches[0]["policy_id"] == "budget_line_or_reserve"


def test_policy_routes_scholarship_grants_to_supports() -> None:
    matches = topic_policy_matches("חברי הוועדה אישרו את ההצעה למתן מלגת מפעל הפיס לסטודנטים תושבי העיר")

    assert matches[0]["root_topic_id"] == "root_supports"
    assert matches[0]["policy_id"] == "scholarship_grants_supports"


def test_policy_routes_emergency_drill_to_security() -> None:
    matches = topic_policy_matches("ועדת מל\"ח דנה בתרגיל פיקוד העורף ובטיפול באתרי הרס")

    assert matches[0]["root_topic_id"] == "root_security_enforcement"
    assert matches[0]["policy_id"] == "emergency_committee_drill_security"


def test_policy_does_not_route_general_war_context_to_security() -> None:
    assert not topic_policy_matches("דברי ראש העיר על שגרת חירום בעקבות מלחמת חרבות ברזל")


def test_policy_routes_sport_support_criteria_to_supports() -> None:
    matches = topic_policy_matches("שינוי תבחין תוספת לעקרונות שיטת ניקוד תקציב של רשות הספורט")

    assert matches[0]["root_topic_id"] == "root_supports"
    assert matches[0]["policy_id"] == "sport_support_criteria"


def test_policy_routes_public_transport_deficit_to_transport() -> None:
    matches = topic_policy_matches("מקור לכיסוי גירעון בפרויקט התחבורה הציבורית וכיכר רמון מול משרד התחבורה")

    assert matches[0]["root_topic_id"] == "root_transport_safety"
    assert matches[0]["policy_id"] == "public_transport_project_funding"


def test_semantic_root_routes_scholarship_child_to_supports() -> None:
    assert semantic_root_for_child_label("מלגת מפעל הפיס", evidence_text="סטודנטים תושבי העיר") == "root_supports"


def test_budget_child_root_is_stable_with_security_context() -> None:
    assert semantic_root_for_child_label("הנחות ופטורים", evidence_text="נזק מלחמה") == "root_budget_finance"


def test_curated_child_topic_seeds_are_unique_and_valid() -> None:
    keys = []
    for row in CURATED_V4_CHILD_TOPICS:
        assert row["root_topic_id"] in ROOT_BY_ID
        assert normalize_for_search(row["child_label_he"])
        keys.append((row["root_topic_id"], normalize_for_search(row["child_label_he"])))

    assert len(CURATED_V4_CHILD_TOPICS) == 85
    assert len(keys) == len(set(keys))
