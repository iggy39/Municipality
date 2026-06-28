from __future__ import annotations

from municipality.pdf_first_v4_topic_classifier import build_topic_profile_index, find_topic_candidates
from municipality.pdf_first_v4_topic_policy import clean_protocol_subject_text, topic_policy_matches
from municipality.chunking import normalize_for_search
from municipality.pdf_first_v4_topic_tree import CURATED_V4_CHILD_TOPICS, ROOT_BY_ID, clean_topic_label, compact_topic_metadata_schema, global_topic_tree_payload, infer_root_topic_id, resolve_child_topic_assignment, secondary_topic_roots, semantic_root_for_child_label, topic_metadata_schema
from municipality.topic_label_quality import canonicalize_topic_label


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


def test_topic_tree_nodes_expose_deterministic_metadata_schema_with_raw_text() -> None:
    tree = global_topic_tree_payload()
    education_root = next(root for root in tree["root_topics"] if root["root_topic_id"] == "root_education")
    education_fields = {field["name"] for field in education_root["metadata_schema"]["fields"]}
    education_child = next(child for child in education_root["children"] if child["child_label_he"] == "מוסדות חינוך")
    child_fields = {field["name"] for field in education_child["metadata_schema"]["fields"]}

    assert education_root["labeling_method"] == "deterministic_curated_topic_tree"
    assert {"raw_text", "place_he", "time_he"} <= education_fields
    assert {"raw_text", "education_institution_he", "program_or_award_he"} <= child_fields


def test_compact_metadata_schema_keeps_raw_text_for_prompts() -> None:
    schema = topic_metadata_schema(root_topic_id="root_supports", label_he="תמיכה בספורט", node_level="child")
    compact = compact_topic_metadata_schema(schema)

    assert compact["labeling_method"] == "deterministic_curated_topic_tree"
    assert "raw_text" in compact["field_names"]
    assert "beneficiary_he" in compact["field_names"]


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


def test_street_violence_query_reuses_existing_community_violence_child() -> None:
    assignment = resolve_child_topic_assignment(
        root_topic_id="root_security_enforcement",
        root_label_he="ביטחון ואכיפה",
        child_label_he="אלימות ופשע ברחובות העיר",
        evidence_text="שאילתה בנושא אלימות ופשע ברחובות העיר",
        selected_existing=True,
    )

    assert assignment["child_label_he"] == "מאבק באלימות קהילתית"


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


def test_policy_routes_religious_council_to_religious_services() -> None:
    matches = topic_policy_matches("שאילתא בנושא מינוי מועצה דתית")

    assert matches[0]["root_topic_id"] == "root_religious_services"
    assert infer_root_topic_id("מינוי מועצה דתית") == "root_religious_services"


def test_generic_root_keywords_cover_finance_and_notice_board_actions() -> None:
    assert infer_root_topic_id("הנפקת כרטיס אשראי טעון", allow_procedural_default=False) == "root_budget_finance"
    assert infer_root_topic_id("ביצוע פעולות מול בנק הדואר", allow_procedural_default=False) == "root_budget_finance"
    assert infer_root_topic_id("פתיחת חשבון בבנק מזרחי", allow_procedural_default=False) == "root_budget_finance"
    assert infer_root_topic_id("העמדת לוח מודעות אלקטרוני", allow_procedural_default=False) == "root_administration"


def test_canonicalizes_substantive_fragment_subjects() -> None:
    examples = {
        "הצעת הגזברות לעדכון בצו הארנונה לשנת2024 הוספת תת סיווג חדש": "הוספת תת סיווג בצו הארנונה",
        "הקמת חניון מוניציפלי חכם בסמוך לתחנת רכבת ופארק הייטק": "הקמת חניון מוניציפלי חכם",
        "שיפוץ מרכז מסחרי רובע ו' ,עדכון תבחינים שנתקבלו במועצה": "שיפוץ מרכז מסחרי",
        "סקירה שנתית של מפקד תחנת אשדוד סנ\"צ אילן שושן": "סקירה שנתית של מפקד תחנת אשדוד",
        "אישור מתן פטור לבעלי עסקים מאגרת שילוט בשל גל תחלואה": "פטור מאגרת שילוט לבעלי עסקים",
        "פעילות הקאנטרי,עלויות מנוי \"והנחות לזכאים": "פעילות הקאנטרי",
        "פרוטוקול מישיבת ועדת בטיחות וגהות עירו נית": "ועדת בטיחות וגהות עירונית",
        "ייסוד והקמת כיתות אזרחים וותיקים בביה\"ס התיכוניים": "כיתות אזרחים ותיקים",
        "פרס חינוך עירוני שנתי לחינוך פורץ דרך באשדוד": "פרס חינוך",
        "שאילתה בנושא שיפוץ חוף הקשתות": "שיפוץ חוף",
        "הוספת תת הצעת הגזברות לעדכון בצו הארנונה לשנת סיווג חדש": "הוספת תת סיווג בצו הארנונה",
        "יחידות דיור שיהיו ברובע במסגרת הסכם הגג ועד עכשיו הצטרפו זוגות": "הסכם הגג",
    }

    for raw, expected in examples.items():
        canonical, reason = canonicalize_topic_label(raw)
        assert canonical == expected
        assert reason in {"semantic_canonicalized", None}


def test_canonicalizer_moves_sport_grant_details_to_evidence_context() -> None:
    canonical, reason = canonicalize_topic_label(
        "מענק עליה לשלב ב",
        root_label_he="תמיכות",
        evidence_text="קבוצת כדורסל בנות אשדוד עברו לשלב ב באליפות אירופה ועל פי התבחין יש מענק",
    )

    assert canonical == "מענקי ספורט"
    assert reason == "semantic_canonicalized"


def test_policy_routes_semantic_essence_subjects_to_expected_roots() -> None:
    assert topic_policy_matches("פרס חינוך עירוני שנתי לחינוך פורץ דרך", limit=1)[0]["root_topic_id"] == "root_education"
    assert topic_policy_matches("קבוצת כדורסל בנות אשדוד מענק עליה לשלב ב", limit=1)[0]["root_topic_id"] == "root_supports"
    assert topic_policy_matches("שיפוץ חוף הקשתות", limit=1)[0]["root_topic_id"] == "root_planning_building"
    assert topic_policy_matches("אלימות ופשע ברחובות העיר", limit=1)[0]["root_topic_id"] == "root_security_enforcement"
    assert topic_policy_matches("יחידות דיור במסגרת הסכם הגג", limit=1)[0]["root_topic_id"] == "root_planning_building"
    assert infer_root_topic_id("פעילות הקאנטרי", allow_procedural_default=False) == "root_culture_sport"
    assert infer_root_topic_id("אלימות ופשע ברחובות העיר", allow_procedural_default=False) == "root_security_enforcement"
    assert infer_root_topic_id("יחידות דיור במסגרת הסכם הגג", allow_procedural_default=False) == "root_planning_building"


def test_support_root_canonicalizes_criteria_synonyms_to_support_criteria() -> None:
    canonical, reason = canonicalize_topic_label(
        "תבחינים|קריטריונים",
        root_label_he="תמיכות",
        evidence_text="מבקשת לבחון את התבחינים מחדש, לאור מצב הקבוצות והקריטריונים",
    )

    assert canonical == "תבחינים לתמיכות"
    assert reason == "semantic_canonicalized"


def test_section_only_label_is_cleaned_consistently() -> None:
    canonical, reason = canonicalize_topic_label("סעיף5")

    assert canonical is None
    assert reason == "procedural_or_dialogue_label"
    assert clean_topic_label("סעיף5") is None
    assert clean_protocol_subject_text("סעיף5") == ""


def test_canonicalizer_repairs_ocr_digit_quote_tail_from_evidence() -> None:
    canonical, reason = canonicalize_topic_label(
        "כיכר רמון בעיר ודרך מנחם",
        evidence_text='שאילתה בנושא "כיכר רמון בעיר ודרך מנחם5 "בגין',
    )

    assert canonical == "כיכר רמון בעיר ודרך מנחם בגין"
    assert reason is None


def test_infer_root_can_disable_procedural_default() -> None:
    text = "בלורת שמית אבגדית"

    assert infer_root_topic_id(text, allow_procedural_default=False) is None
    assert infer_root_topic_id(text) == "root_agenda_queries"


def test_profile_index_ignores_candidate_and_low_quality_children() -> None:
    tree = {
        "root_topics": [
            {
                "root_topic_id": "root_transport_safety",
                "root_label_he": "תחבורה ובטיחות בדרכים",
                "keywords": [],
                "children": [
                    {"child_topic_id": "candidate-child", "child_label_he": "כיכר רמון", "status": "candidate"},
                    {"child_topic_id": "bad-child", "child_label_he": "נושא כללי", "status": "active"},
                ],
            }
        ]
    }

    index = build_topic_profile_index(tree)

    assert all(entry.get("child_choice_id") not in {"candidate-child", "bad-child"} for entry in index["entries"])


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


def test_policy_routes_budget_update_over_sports_domain() -> None:
    matches = topic_policy_matches("תקצוב אירוע חגיגות אליפות לשנת 2026 - עדכון תקציב לשנת 2026")

    assert matches[0]["root_topic_id"] == "root_budget_finance"
    assert matches[0]["policy_id"] == "municipal_budget_update_action"


def test_policy_routes_budget_objection_over_service_domain() -> None:
    matches = topic_policy_matches("ההסתייגות היא קיצוץ מתקציב תכנית שכונה כעיר, שכונות התקווה")

    assert matches[0]["root_topic_id"] == "root_budget_finance"
    assert matches[0]["policy_id"] == "budget_objection_or_cut_action"


def test_policy_routes_street_paving_bylaw_to_transport() -> None:
    matches = topic_policy_matches("תיקון סעיף בחוק עזר לתל אביב יפו סלילת רחובות")

    assert matches[0]["root_topic_id"] == "root_transport_safety"
    assert matches[0]["policy_id"] == "street_paving_bylaw_transport"


def test_policy_routes_municipal_protection_to_security() -> None:
    matches = topic_policy_matches("הצעה לסדר בנושא המיגון וקיום דיון בוועדת החירום")

    assert matches[0]["root_topic_id"] == "root_security_enforcement"
    assert matches[0]["policy_id"] == "municipal_protection_emergency_security"


def test_policy_routes_committee_membership_changes_to_administration() -> None:
    matches = topic_policy_matches("מינויים ושינויים בוועדת מכרזים")

    assert matches[0]["root_topic_id"] == "root_administration"
    assert matches[0]["policy_id"] == "committee_membership_change_administration"


def test_policy_routes_municipal_corporation_audit_to_administration() -> None:
    matches = topic_policy_matches("צעדים לתיקון ההתנהלות בתאגידים עירוניים בעקבות דו\"ח מבקר המדינה")

    assert matches[0]["root_topic_id"] == "root_administration"
    assert matches[0]["policy_id"] == "municipal_corporation_audit_governance"


def test_policy_routes_air_rights_to_planning() -> None:
    matches = topic_policy_matches("שאילתא בנושא מעקב זכויות אויר")

    assert matches[0]["root_topic_id"] == "root_planning_building"
    assert matches[0]["policy_id"] == "air_rights_planning"


def test_policy_routes_housing_marketing_to_planning() -> None:
    matches = topic_policy_matches("פרסום אסור של מכירת דירות למגזר מסוים")

    assert matches[0]["root_topic_id"] == "root_planning_building"
    assert matches[0]["policy_id"] == "housing_sale_marketing_planning"


def test_policy_routes_support_advances_and_donations_to_supports() -> None:
    cases = [
        "מקדמות לשנת 2022 ותשלום מקדמה למוסד הפועל כעמותה",
        "פרוטוקול מישיבת ועדת תרומות - עמותת אתנה",
    ]

    for text in cases:
        matches = topic_policy_matches(text)
        assert matches[0]["root_topic_id"] == "root_supports"
        assert matches[0]["policy_id"] == "support_advance_or_donation_committee"


def test_policy_routes_absorption_committee_to_welfare() -> None:
    matches = topic_policy_matches("פרוטוקול מישיבת ועדת קליטה")

    assert matches[0]["root_topic_id"] == "root_welfare_social"
    assert matches[0]["policy_id"] == "absorption_committee_welfare"


def test_policy_routes_drug_abuse_committee_to_security() -> None:
    matches = topic_policy_matches("פרוטוקול מישיבת הועדה למאבק בנגע הסמים המסוכנים")

    assert matches[0]["root_topic_id"] == "root_security_enforcement"
    assert matches[0]["policy_id"] == "drug_abuse_committee_security"


def test_policy_routes_debt_writeoff_to_finance() -> None:
    matches = topic_policy_matches("פרוטוקול למחיקת חובות מס7.25 מיום25.12")

    assert matches[0]["root_topic_id"] == "root_budget_finance"
    assert matches[0]["policy_id"] == "municipal_debt_writeoff_finance"


def test_policy_routes_land_lease_rights_to_allocations() -> None:
    matches = topic_policy_matches("עסקאות חכירה מול רמ\"י")

    assert matches[0]["root_topic_id"] == "root_allocations"
    assert matches[0]["policy_id"] == "land_lease_or_rights_allocation"


def test_policy_does_not_treat_ice_skating_as_land_parcel() -> None:
    matches = topic_policy_matches("שאילתה בנושא מתחם ההחלקה על הקרח בקניון בלו אייס ארנה")

    assert all(match["policy_id"] != "land_lease_or_rights_allocation" for match in matches)


def test_policy_routes_lease_right_cancellation_to_allocations() -> None:
    matches = topic_policy_matches("אישור הסכם לביטול זכות חכירה")

    assert matches[0]["root_topic_id"] == "root_allocations"
    assert matches[0]["policy_id"] == "lease_right_cancellation_allocation"


def test_policy_routes_urban_renewal_to_planning() -> None:
    matches = topic_policy_matches("בקשה לקידום מתחם התחדשות עירונית גן סיאטל בותמ\"ל")

    assert matches[0]["root_topic_id"] == "root_planning_building"
    assert matches[0]["policy_id"] == "urban_renewal_planning"


def test_policy_routes_general_committee_protocol_approval_to_administration() -> None:
    matches = topic_policy_matches("אישור החלטות בפרוטוקולים של ועדות העירייה")

    assert matches[0]["root_topic_id"] == "root_administration"
    assert matches[0]["policy_id"] == "municipal_committee_protocol_decision_approval"


def test_policy_routes_paramedical_benefits_to_welfare() -> None:
    matches = topic_policy_matches("שינוי קריטריונים בביטוח לאומי למתן טיפולים פרא רפואיים")

    assert matches[0]["root_topic_id"] == "root_welfare_social"
    assert matches[0]["policy_id"] == "paramedical_treatment_benefits_welfare"


def test_policy_routes_municipal_prizes_to_culture() -> None:
    matches = topic_policy_matches("עדכון תקנוני הפרסים העירוניים ואישור תקנון פרס התיאטרון")

    assert matches[0]["root_topic_id"] == "root_culture_sport"
    assert matches[0]["policy_id"] == "municipal_culture_prizes"


def test_policy_routes_director_appointment_to_administration() -> None:
    matches = topic_policy_matches("מינוי מ\"מ ראש העיר כדירקטור ויו\"ר דירקטוריון החברה העירונית")

    assert matches[0]["root_topic_id"] == "root_administration"
    assert matches[0]["policy_id"] == "director_board_appointment_administration"


def test_policy_routes_domain_representation_to_planning_not_administration() -> None:
    matches = topic_policy_matches("אישור אינג' שמעון כצנלסון לשמש כנציג עיריית אשדוד בדיוני הוועדה המחוזית לתכנון ובנייה")

    assert matches[0]["root_topic_id"] == "root_planning_building"
    assert matches[0]["policy_id"] == "planning_committee_domain_representation"


def test_policy_routes_committee_member_appointment_to_administration() -> None:
    matches = topic_policy_matches("מינוי חבר לוועדה המחוזית לתכנון ולבניה מחוז ת\"א")

    assert matches[0]["root_topic_id"] == "root_administration"
    assert matches[0]["policy_id"] == "committee_appointment_or_membership_administration"


def test_policy_routes_asset_registrar_to_assets() -> None:
    matches = topic_policy_matches("הסמכת עו\"ד מירב ביטון לרשמת הנכסים של עיריית אשדוד")

    assert matches[0]["root_topic_id"] == "root_commerce_assets"
    assert matches[0]["policy_id"] == "asset_registrar_authorization_assets"


def test_policy_routes_electric_mobility_enforcement_to_security() -> None:
    matches = topic_policy_matches("אכיפה והסדרה של כלים חשמליים ממונעים בעיר")

    assert matches[0]["root_topic_id"] == "root_security_enforcement"
    assert matches[0]["policy_id"] == "electric_mobility_enforcement_security"


def test_policy_routes_solar_panels_to_environment() -> None:
    matches = topic_policy_matches("הוספת תקנות עיר ירוקה להתקנת פאנלים סולאריים על גגות מבנים")

    assert matches[0]["root_topic_id"] == "root_infrastructure_environment"
    assert matches[0]["policy_id"] == "solar_renewable_energy_environment"


def test_policy_routes_disagreement_domains_without_dicta() -> None:
    cases = [
        ("תחנת שאיבת ביוב ומאגר חירום לביוב ברובע יג", "root_infrastructure_environment", "sewage_water_utility_infrastructure"),
        ("זיהום אויר חמור ותלונות חוזרות על ריחות קשים ברחבי העיר", "root_infrastructure_environment", "air_pollution_odors_environment"),
        ("השבתת המזרקה המוזיקלית בפארק אשדוד ים", "root_infrastructure_environment", "park_fountain_infrastructure"),
        ("ההשקעה הנמוכה בחינוך באשדוד", "root_education", "education_quality_or_committee_access"),
        ("אופן זימון הורים לוועדות אפיון וזכאות", "root_education", "education_quality_or_committee_access"),
        ("ייעוד המקרקעין מבנים ומוסדות ציבור, למטרת הפעלת כיתת גן ילדים אחת", "root_education", "kindergarten_land_use_education"),
        ("טיפול בבדידות קשישים עריריים, ניטור צריכת מים, לחצני מצוקה וחיישני תנועה", "root_welfare_social", "elderly_loneliness_monitoring_welfare"),
        ("מתן במה לאמנות מקומית ואמנים מקומיים בחגיגות העיר", "root_culture_sport", "local_art_culture_subject"),
        ("חלופה למאות בני נוער במקרה של סגירת קבוצת עירוני אשדוד", "root_culture_sport", "youth_sports_club_alternative_culture"),
        ("תכנית מס-1445311 איחוד וחלוקה ותוספת זכויות בנייה למבנה בית הדר", "root_planning_building", "statutory_plan_rights_planning"),
        ("הסמכת החברה לתיירות למימוש התוכנית לפתרון חניית קרוואנים", "root_planning_building", "activity_complex_or_caravan_parking_plan_planning"),
        ("כשל מתמשך בטיפול באגם המרינה ובחינת העברת האחריות", "root_planning_building", "waterfront_public_site_operation_planning"),
        ("לפעילות מלאה בחופי העיר", "root_planning_building", "waterfront_public_site_operation_planning"),
        ("הצטרפות ועשייה במקרקעין", "root_allocations", "land_action_real_estate_allocation"),
        ("מינוי ואי פרסום מכרז לתפקיד דובר עיריית אשדוד", "root_administration", "municipal_role_appointment_administration"),
        ("מינויים ושינויים בתאגידים ואיגודים", "root_administration", "committee_appointment_or_membership_administration"),
    ]

    for text, root_topic_id, policy_id in cases:
        matches = topic_policy_matches(text)
        assert matches[0]["root_topic_id"] == root_topic_id
        assert matches[0]["policy_id"] == policy_id


def test_branded_beach_initiative_does_not_force_planning_policy() -> None:
    matches = topic_policy_matches("חזרת מיזם חופי לפעילות מלאה בחופי העיר")

    assert all(match["policy_id"] != "waterfront_public_site_operation_planning" for match in matches)


def test_semantic_root_routes_scholarship_child_to_supports() -> None:
    assert semantic_root_for_child_label("מלגת מפעל הפיס", evidence_text="סטודנטים תושבי העיר") == "root_supports"


def test_budget_child_root_is_stable_with_security_context() -> None:
    assert semantic_root_for_child_label("הנחות ופטורים", evidence_text="נזק מלחמה") == "root_budget_finance"


def test_location_only_square_subject_stays_geo_when_no_transport_action() -> None:
    assert semantic_root_for_child_label("כיכרות וצמתים", evidence_text="כיכר רמון בעיר ודרך מנחם בגין", fallback="root_geo") == "root_geo"


def test_public_safety_streets_context_does_not_route_to_geo_or_transport() -> None:
    text = "שאילתה בנושא המשך התגברות תופעת האלימות והפשע ברחובות העיר"

    assert semantic_root_for_child_label("מאבק באלימות קהילתית", evidence_text=text, fallback="root_geo") == "root_security_enforcement"
    resolved = resolve_child_topic_assignment(root_topic_id="root_geo", root_label_he="מיקומים וגיאוגרפיה", child_label_he="כתובות ורחובות", evidence_text=text, selected_existing=True)

    assert resolved["root_topic_id"] == "root_security_enforcement"
    assert resolved["child_label_he"] is None
    assert resolved["reason"] == "geo_child_in_public_safety_context"


def test_mixed_welfare_education_subject_emits_secondary_education_root() -> None:
    roots = secondary_topic_roots(root_topic_id="root_welfare_social", child_label_he="הסעות למסגרות חינוך ורווחה", evidence_text="הסעות לחינוך המיוחד ולמסגרות הרווחה")

    assert roots == [
        {
            "root_topic_id": "root_education",
            "root_label_he": "חינוך",
            "reason": "semantic_root_context",
            "confidence": 0.68,
        }
    ]


def test_location_only_subject_does_not_emit_transport_secondary_root() -> None:
    roots = secondary_topic_roots(root_topic_id="root_geo", child_label_he="כיכרות וצמתים", evidence_text="כיכר רמון בעיר ודרך מנחם בגין")

    assert roots == []


def test_curated_child_topic_seeds_are_unique_and_valid() -> None:
    keys = []
    for row in CURATED_V4_CHILD_TOPICS:
        assert row["root_topic_id"] in ROOT_BY_ID
        assert normalize_for_search(row["child_label_he"])
        keys.append((row["root_topic_id"], normalize_for_search(row["child_label_he"])))

    assert len(CURATED_V4_CHILD_TOPICS) == 93
    assert len(keys) == len(set(keys))


def test_curated_solar_child_routes_to_environment() -> None:
    assert semantic_root_for_child_label("התקנת פאנלים סולאריים", evidence_text="התקנת פאנלים סולאריים על גגות מבנים") == "root_infrastructure_environment"


def test_curated_culture_prizes_child_exists() -> None:
    assert any(
        row["root_topic_id"] == "root_culture_sport"
        and row["child_label_he"] == "פרסים עירוניים"
        and "פרס התיאטרון" in row.get("aliases_he", [])
        for row in CURATED_V4_CHILD_TOPICS
    )
