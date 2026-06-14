from __future__ import annotations

from municipality.pdf_first_v4_topic_classifier import build_topic_profile_index, find_topic_candidates
from municipality.pdf_first_v4_topic_policy import topic_policy_matches
from municipality.pdf_first_v4_topic_tree import global_topic_tree_payload


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
