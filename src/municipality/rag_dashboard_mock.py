from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from municipality.resident_gis_registry import load_resident_gis_registry


TEL_AVIV_MUNICIPALITY_ID = "tel_aviv"
TEL_AVIV_MUNICIPALITY_CODE = "5000"
DEFAULT_RESIDENT_ADDRESS = "דיזנגוף 99"
DEFAULT_SCENARIO_KEY = "near_me_recent_decisions"
GIS_STORY_REPORT_PATH = Path(__file__).resolve().parents[2] / "eval" / "reports" / "v3_gis_stories_all_artifacts_20260624.json"
GIS_STORY_GOVMAP_EXECUTION_PATH = Path(__file__).resolve().parents[2] / "eval" / "reports" / "v3_gis_story_govmap_execution_20260624.json"


CATEGORY_LABELS = {
    "planning": "תכנון ובנייה",
    "transport": "תחבורה",
    "education": "חינוך",
    "welfare": "רווחה",
    "environment": "סביבה",
    "emergency": "חירום",
    "culture": "תרבות וספורט",
    "commerce": "מסחר ותעסוקה",
}


SCENARIOS: list[dict[str, Any]] = [
    {
        "key": "near_me_recent_decisions",
        "question": "מה הוחלט לאחרונה על אזור המגורים שלי, ומה מתוך זה באמת קרוב אליי?",
        "category": "planning",
        "topic": "החלטות קרובות לבית",
        "intent": "resident_nearby_recent_decisions",
        "brief": "מוקאפ תל אביבי שמסנן החלטות עירוניות לפי קרבה לבית ומציג רק שכבות שמסבירות השפעה יומיומית.",
        "address": DEFAULT_RESIDENT_ADDRESS,
        "layer_keys": ["resident_location", "neighborhoods_and_statistics", "public_buildings_assets", "roads_parking_public_works", "playgrounds_youth_space"],
        "aliases": ["neighborhoods_area", "bus_stops", "school", "clinics", "bombshelters"],
        "focuses": ["neighborhoods_area", "bus_stops", "school", "clinics", "bombshelters"],
    },
    {
        "key": "parcel_allocation_watch",
        "question": "האם יש הקצאות קרקע, הסכמי שימוש או פיתוח בגוש/חלקה ליד הבית שלי?",
        "category": "planning",
        "topic": "הקצאות קרקע ליד הבית",
        "intent": "parcel_allocation_context",
        "brief": "החלטות הקצאה והסכמי שימוש הופכות מובנות כשהן מוצמדות לחלקה, בעלות, ייעוד ושירות קרוב.",
        "address": "אבן גבירול 71",
        "layer_keys": ["parcels_cadaster", "public_buildings_assets", "religious_services", "planning_land_use"],
        "aliases": ["PARCEL_ALL", "SUB_GUSH_ALL", "retzefmigrashim", "mikve", "neighborhoods_area"],
        "focuses": ["PARCEL_ALL", "retzefmigrashim", "mikve", "SUB_GUSH_ALL", "neighborhoods_area"],
    },
    {
        "key": "school_future_near_child",
        "question": "מה קורה עם בתי הספר והגנים באזור של הילדים שלי, והאם יש שינוי שיכול להשפיע על הליכה/רישום/קהילה?",
        "category": "education",
        "topic": "עתיד מוסדות חינוך בשכונה",
        "intent": "education_facility_context",
        "brief": "מוקאפ שמחבר דיוני חינוך למוסדות, גנים, הליכה בטוחה ושכונת המגורים של הילד.",
        "address": "ארלוזורוב 120",
        "layer_keys": ["education_facilities", "neighborhoods_and_statistics", "playgrounds_youth_space", "demographic_equity"],
        "aliases": ["school", "kids_g", "neighborhoods_area", "bus_stops"],
        "focuses": ["school", "kids_g", "neighborhoods_area", "bus_stops", "school"],
    },
    {
        "key": "youth_parks_night_safety",
        "question": "איפה בעיר דיברו על מפגשי בני נוער בפארקים בלילה, ומה מצב הביטחון/תאורה/שירותים סביב הפארקים שלי?",
        "category": "emergency",
        "topic": "בטיחות בני נוער בפארקים",
        "intent": "youth_parks_safety_context",
        "brief": "הסיפור מציג איך דיוני בטיחות מקבלים הקשר דרך פארקים, דרכי גישה ונקודות חירום סמוכות.",
        "address": "שדרות בן גוריון 42",
        "layer_keys": ["playgrounds_youth_space", "emergency_security_services", "roads_parking_public_works", "neighborhoods_and_statistics"],
        "aliases": ["neighborhoods_area", "bombshelters", "bus_stops", "teva_ironi"],
        "focuses": ["neighborhoods_area", "bombshelters", "bus_stops", "neighborhoods_area", "bombshelters"],
    },
    {
        "key": "elderly_winter_vulnerability",
        "question": "בשכונה שלי יש הרבה קשישים? האם העירייה דיברה על מוות מקור/בדידות ומה השירותים הקרובים?",
        "category": "welfare",
        "topic": "פגיעות קשישים בחורף",
        "intent": "elderly_welfare_context",
        "brief": "דיוני רווחה מוצגים דרך שירותי בריאות קרובים, תחבורה ושכונות עם אוכלוסייה רגישה.",
        "address": "וייצמן 14",
        "layer_keys": ["welfare_health_services", "demographic_equity", "neighborhoods_and_statistics", "public_transport_access"],
        "aliases": ["clinics", "pharmacies", "neighborhoods_area", "bus_stops"],
        "focuses": ["clinics", "pharmacies", "neighborhoods_area", "bus_stops", "clinics"],
    },
    {
        "key": "flooding_damage_and_drainage",
        "question": "האם הרחוב שלי נמצא באזור שהוזכר בהצפות, ומה ידוע על ניקוז/צינורות/פיצוי?",
        "category": "environment",
        "topic": "הצפות וניקוז ברחוב",
        "intent": "flooding_drainage_context",
        "brief": "מוקאפ שמספר סיפור של תלונת הצפה, בדיקת תשתיות, עבודות רחוב והחלטת תחזוקה.",
        "address": "נחלת בנימין 88",
        "layer_keys": ["drainage_water_sewer", "roads_parking_public_works", "parcels_cadaster", "neighborhoods_and_statistics"],
        "aliases": ["neighborhoods_area", "PARCEL_ALL", "bus_stops", "water_flow"],
        "focuses": ["neighborhoods_area", "PARCEL_ALL", "bus_stops", "neighborhoods_area", "PARCEL_ALL"],
    },
    {
        "key": "commercial_center_safety",
        "question": "האם המרכז המסחרי ליד הבית שלי הוזכר כסיכון או כמועמד לשיפוץ, ומה ההקשר הבנייני/תכנוני שלו?",
        "category": "commerce",
        "topic": "בטיחות מרכז מסחרי",
        "intent": "commercial_center_safety_context",
        "brief": "מרכז מסחרי מוקאפ מחובר למבנה, חלקה, דרכי גישה ונקודות חירום סמוכות.",
        "address": "דיזנגוף סנטר תל אביב",
        "layer_keys": ["commerce_employment", "parcels_cadaster", "emergency_security_services", "public_buildings_assets"],
        "aliases": ["PARCEL_ALL", "GASSTATIONS", "bombshelters", "bus_stops", "neighborhoods_area"],
        "focuses": ["PARCEL_ALL", "bombshelters", "bus_stops", "GASSTATIONS", "neighborhoods_area"],
    },
    {
        "key": "antenna_agreement_near_home",
        "question": "האם אושרו אנטנות או מתקני שידור ליד הבית/בית הספר, ובאיזו חלקה או מבנה?",
        "category": "environment",
        "topic": "אנטנות ומתקני שידור",
        "intent": "cellular_agreement_context",
        "brief": "החלטת שימוש במתקן שידור מוצגת לצד אנטנות פעילות, בתי ספר, חלקות ומבני ציבור.",
        "address": "יהודה המכבי 45",
        "layer_keys": ["cellular_and_radiation_context", "education_facilities", "parcels_cadaster", "public_buildings_assets"],
        "aliases": ["cell_active", "school", "PARCEL_ALL", "neighborhoods_area"],
        "focuses": ["cell_active", "school", "PARCEL_ALL", "cell_active", "neighborhoods_area"],
    },
    {
        "key": "public_transport_budget_gap",
        "question": "האם יש החלטות על תחבורה ציבורית שמשפיעות על התחנות והמסלולים שאני משתמש בהם?",
        "category": "transport",
        "topic": "פערי תקציב בתחבורה ציבורית",
        "intent": "public_transport_budget_context",
        "brief": "תקציב תחבורה מקבל הקשר דרך תחנות, שכונות נגישות וצירי נסיעה שמשמשים את התושב.",
        "address": "רכבת מרכז תל אביב",
        "layer_keys": ["public_transport_access", "roads_parking_public_works", "demographic_equity", "neighborhoods_and_statistics"],
        "aliases": ["bus_stops", "neighborhoods_area", "neta_lines", "nta_metro_stations"],
        "focuses": ["bus_stops", "neighborhoods_area", "bus_stops", "neighborhoods_area", "bus_stops"],
    },
    {
        "key": "parking_and_roadwork_disruption",
        "question": "אילו עבודות, חניה או שינויי דרך צפויים סביבי, והאם הם עלו במועצה?",
        "category": "transport",
        "topic": "עבודות וחניה סביב הבית",
        "intent": "roadwork_parking_context",
        "brief": "עבודות וחניה מוצגות כמסלול הפרעה יומיומי: רחוב, שכונה, תחבורה וצמתים קרובים.",
        "address": "קינג ג'ורג' 33",
        "layer_keys": ["roads_parking_public_works", "neighborhoods_and_statistics", "public_transport_access", "resident_location"],
        "aliases": ["bus_stops", "neighborhoods_area", "PARCEL_ALL", "districts_new"],
        "focuses": ["bus_stops", "neighborhoods_area", "PARCEL_ALL", "bus_stops", "neighborhoods_area"],
    },
    {
        "key": "sanitation_dogs_waste",
        "question": "האם יש החלטות או תשובות על ניקיון, גללי כלבים או פינוי פסולת באזור שלי?",
        "category": "environment",
        "topic": "ניקיון ופסולת בשכונה",
        "intent": "sanitation_service_context",
        "brief": "שאילתות ניקיון נקשרות לפארקים, שכונות, ימי פינוי ומוקדי מפגע סביב התושב.",
        "address": "פלורנטין 12",
        "layer_keys": ["environment_sanitation", "playgrounds_youth_space", "neighborhoods_and_statistics", "roads_parking_public_works"],
        "aliases": ["aq_realtime", "neighborhoods_area", "teva_ironi", "bus_stops"],
        "focuses": ["neighborhoods_area", "aq_realtime", "bus_stops", "neighborhoods_area", "aq_realtime"],
    },
    {
        "key": "emergency_readiness_near_me",
        "question": "במקרה חירום, אילו מקלטים/תחנות חירום קרובים אליי ומה הוחלט על מוכנות העיר?",
        "category": "emergency",
        "topic": "מוכנות חירום ליד הבית",
        "intent": "emergency_readiness_context",
        "brief": "דיוני חירום מוצגים כמפת שירותי הצלה, מקלטים ונתיבי גישה סביב נקודת התושב.",
        "address": "קפלן 6",
        "layer_keys": ["emergency_security_services", "roads_parking_public_works", "public_buildings_assets", "neighborhoods_and_statistics"],
        "aliases": ["bombshelters", "GASSTATIONS", "regional_authorities", "neighborhoods_area"],
        "focuses": ["bombshelters", "regional_authorities", "GASSTATIONS", "bombshelters", "neighborhoods_area"],
    },
    {
        "key": "family_support_services",
        "question": "האם למשפחות במצב דומה לשלי יש מענים עירוניים קרובים, ומה הוחלט עליהם?",
        "category": "welfare",
        "topic": "שירותי תמיכה למשפחות",
        "intent": "family_support_context",
        "brief": "החלטה חברתית מקבלת שכבת שירותים, תחבורה ונגישות במקום להישאר הצהרה כללית.",
        "address": "דרך השלום 53",
        "layer_keys": ["welfare_health_services", "public_transport_access", "demographic_equity", "neighborhoods_and_statistics"],
        "aliases": ["clinics", "pharmacies", "bus_stops", "neighborhoods_area"],
        "focuses": ["clinics", "bus_stops", "pharmacies", "neighborhoods_area", "clinics"],
    },
    {
        "key": "sport_support_distribution",
        "question": "מי מקבל תמיכות ספורט בעיר, והאם יש קשר למתקנים או שכונות מסוימות?",
        "category": "culture",
        "topic": "חלוקת תמיכות ספורט",
        "intent": "sport_support_context",
        "brief": "תמיכות ספורט מוצגות מול מתקנים, שכונות, נגישות ונתוני שוויון שירותים.",
        "address": "שדרות רוקח 101",
        "layer_keys": ["culture_sport_leisure", "neighborhoods_and_statistics", "demographic_equity", "public_transport_access"],
        "aliases": ["neighborhoods_area", "bus_stops", "atractions", "fieldschool"],
        "focuses": ["neighborhoods_area", "bus_stops", "neighborhoods_area", "bus_stops", "neighborhoods_area"],
    },
    {
        "key": "culture_accessibility_issue",
        "question": "האם נדונו בעיות נגישות במוסדות התרבות/פנאי שאני משתמש בהם?",
        "category": "culture",
        "topic": "נגישות במוסדות תרבות",
        "intent": "culture_accessibility_context",
        "brief": "שאלת נגישות מקבלת מוסד, הגעה בתחבורה ונקודות חירום סמוכות.",
        "address": "שאול המלך 27",
        "layer_keys": ["culture_sport_leisure", "public_buildings_assets", "public_transport_access", "emergency_security_services"],
        "aliases": ["bus_stops", "regional_authorities", "bombshelters", "neighborhoods_area"],
        "focuses": ["bus_stops", "regional_authorities", "bombshelters", "neighborhoods_area", "bus_stops"],
    },
    {
        "key": "environmental_exposure_near_school",
        "question": "האם ליד בית הספר/הבית שלי יש מקורות זיהום או אנטנות שהוזכרו בדיוני העירייה?",
        "category": "environment",
        "topic": "חשיפה סביבתית ליד בית ספר",
        "intent": "environmental_exposure_context",
        "brief": "חשש תושב מוצג יחד עם בית ספר, אנטנות, ניטור סביבתי ושכונה.",
        "address": "בוגרשוב 71",
        "layer_keys": ["environment_sanitation", "cellular_and_radiation_context", "education_facilities", "neighborhoods_and_statistics"],
        "aliases": ["aq_realtime", "cell_active", "school", "neighborhoods_area"],
        "focuses": ["school", "cell_active", "aq_realtime", "neighborhoods_area", "school"],
    },
    {
        "key": "local_economy_employment_shift",
        "question": "האם העירייה דנה באובדן מקומות עבודה או מעבר מפעלים, ואילו אזורי תעסוקה/מסחר מושפעים?",
        "category": "commerce",
        "topic": "שינויי תעסוקה וכלכלה מקומית",
        "intent": "local_economy_context",
        "brief": "שאלה כלכלית מקבלת הקשר מרחבי של אזורי מסחר, תחבורה ונגישות עובדים.",
        "address": "יגאל אלון 94",
        "layer_keys": ["commerce_employment", "neighborhoods_and_statistics", "public_transport_access", "demographic_equity"],
        "aliases": ["bus_stops", "GASSTATIONS", "neighborhoods_area", "banks"],
        "focuses": ["bus_stops", "GASSTATIONS", "neighborhoods_area", "bus_stops", "GASSTATIONS"],
    },
    {
        "key": "planning_objection_window",
        "question": "יש תכנית ליד הבית שלי; מה נאמר עליה בפרוטוקולים ומתי/איך אפשר להתנגד?",
        "category": "planning",
        "topic": "חלון התנגדות לתכנית",
        "intent": "planning_objection_context",
        "brief": "סטטוס תכנון, השתתפות ציבורית, חלקות ושכונות מוצגים יחד עם פרוטוקולי התכנון.",
        "address": "שדרות רוטשילד 22",
        "layer_keys": ["planning_land_use", "parcels_cadaster", "resident_location", "neighborhoods_and_statistics"],
        "aliases": ["retzefmigrashim", "PARCEL_ALL", "SUB_GUSH_ALL", "neighborhoods_area"],
        "focuses": ["retzefmigrashim", "PARCEL_ALL", "neighborhoods_area", "SUB_GUSH_ALL", "retzefmigrashim"],
    },
    {
        "key": "neighborhood_budget_equity",
        "question": "האם השכונה שלי מקבלת פחות/יותר שירותים או תקציבים לפי הפרוטוקולים והשכבות?",
        "category": "welfare",
        "topic": "שוויון תקציבי בין שכונות",
        "intent": "neighborhood_budget_equity_context",
        "brief": "מוקאפ שבודק החלטות תקציביות מול פיזור שירותים ואוכלוסייה בשכונות.",
        "address": "העלייה 60",
        "layer_keys": ["demographic_equity", "education_facilities", "welfare_health_services", "neighborhoods_and_statistics"],
        "aliases": ["neighborhoods_area", "school", "clinics", "kids_g"],
        "focuses": ["neighborhoods_area", "school", "clinics", "kids_g", "neighborhoods_area"],
    },
    {
        "key": "street_naming_memorialization",
        "question": "האם יש הצעות לקריאת רחובות או הנצחה באזור שלי, ומה הסטטוס שלהן?",
        "category": "planning",
        "topic": "קריאת רחובות והנצחה",
        "intent": "street_naming_context",
        "brief": "הצעות הנצחה מקושרות לשכונה, רחוב, תכנית וסטטוס ועדה.",
        "address": "שינקין 36",
        "layer_keys": ["roads_parking_public_works", "neighborhoods_and_statistics", "resident_location", "planning_land_use"],
        "aliases": ["neighborhoods_area", "bus_stops", "retzefmigrashim", "PARCEL_ALL"],
        "focuses": ["neighborhoods_area", "bus_stops", "retzefmigrashim", "PARCEL_ALL", "neighborhoods_area"],
    },
    {
        "key": "municipal_asset_contracts",
        "question": "אילו הסכמים עם מתקנים ציבוריים או חברות עירוניות קיימים סביבי ומה אושר בהם?",
        "category": "planning",
        "topic": "הסכמים סביב נכסים עירוניים",
        "intent": "municipal_asset_contract_context",
        "brief": "הסכמים משפטיים מוצגים לצד נכסים ומבנים שהתושב יכול לזהות במפה.",
        "address": "מרמורק 10",
        "layer_keys": ["public_buildings_assets", "parcels_cadaster", "commerce_employment", "planning_land_use"],
        "aliases": ["PARCEL_ALL", "regional_authorities", "bus_stops", "retzefmigrashim"],
        "focuses": ["PARCEL_ALL", "regional_authorities", "bus_stops", "retzefmigrashim", "PARCEL_ALL"],
    },
    {
        "key": "religious_service_access",
        "question": "האם מתוכנן או אושר מקווה/שירות דת ליד השכונה שלי, ועל איזו קרקע?",
        "category": "planning",
        "topic": "נגישות לשירותי דת",
        "intent": "public_service_facility_context",
        "brief": "החלטת שירות דת מקבלת הקשר של מתקן, שכונה, חלקה ותכנית.",
        "address": "ספיר 7 תל אביב",
        "layer_keys": ["religious_services", "parcels_cadaster", "neighborhoods_and_statistics", "planning_land_use"],
        "aliases": ["mikve", "PARCEL_ALL", "neighborhoods_area", "retzefmigrashim"],
        "focuses": ["mikve", "PARCEL_ALL", "neighborhoods_area", "retzefmigrashim", "mikve"],
    },
    {
        "key": "citywide_risk_hotspots",
        "question": "איפה בעיר חוזרות שאילתות על סיכונים: הצפות, מבנים מסוכנים, אלימות, בטיחות או זיהום?",
        "category": "emergency",
        "topic": "מוקדי סיכון עירוניים",
        "intent": "citywide_risk_hotspots_context",
        "brief": "מפת סיכון מוקאפ מתוך פרוטוקולים: חירום, ניקוז, סביבה ושכונות עם ריבוי אזכורים.",
        "address": "כיכר רבין תל אביב",
        "layer_keys": ["emergency_security_services", "drainage_water_sewer", "environment_sanitation", "neighborhoods_and_statistics"],
        "aliases": ["bombshelters", "aq_realtime", "neighborhoods_area", "PARCEL_ALL"],
        "focuses": ["bombshelters", "aq_realtime", "neighborhoods_area", "PARCEL_ALL", "bombshelters"],
    },
    {
        "key": "service_gap_for_children",
        "question": "האם באזור שלי יש מספיק מענים לילדים ונוער ביחס למה שהמועצה דנה בו?",
        "category": "education",
        "topic": "פער שירותים לילדים ונוער",
        "intent": "children_service_gap_context",
        "brief": "הסיפור משווה מוסדות ילדים, פארקים, רווחה ודמוגרפיה סביב נקודת התושב.",
        "address": "לינקולן 5",
        "layer_keys": ["education_facilities", "playgrounds_youth_space", "demographic_equity", "welfare_health_services"],
        "aliases": ["school", "kids_g", "clinics", "neighborhoods_area"],
        "focuses": ["school", "kids_g", "clinics", "neighborhoods_area", "school"],
    },
]


def _address_with_city(scenario: dict[str, Any]) -> str:
    address = " ".join(str(scenario.get("address") or DEFAULT_RESIDENT_ADDRESS).split())
    return address if "תל אביב" in address else f"{address} תל אביב"


def _question_with_visible_address(scenario: dict[str, Any]) -> str:
    custom = NATURAL_QUESTIONS_BY_KEY.get(str(scenario.get("key")))
    if custom:
        return custom
    address = _address_with_city(scenario)
    return f"מה הוחלט לאחרונה בנושא {scenario.get('topic')} ליד {address}?"


NATURAL_QUESTIONS_BY_KEY = {
    "near_me_recent_decisions": "אילו החלטות עירייה התקבלו לאחרונה ליד דיזנגוף 99 תל אביב",
    "parcel_allocation_watch": "האם אושרו הקצאות קרקע או הסכמי שימוש ליד אבן גבירול 71 תל אביב?",
    "school_future_near_child": "אילו החלטות התקבלו על בתי ספר וגנים ליד ארלוזורוב 120 תל אביב?",
    "youth_parks_night_safety": "מה העירייה החליטה על בטיחות בני נוער בפארקים סביב שדרות בן גוריון 42 תל אביב?",
    "elderly_winter_vulnerability": "אילו החלטות רווחה נוגעות לקשישים ליד וייצמן 14 תל אביב?",
    "flooding_damage_and_drainage": "מה הוחלט על הצפות וניקוז ליד נחלת בנימין 88 תל אביב?",
    "commercial_center_safety": "האם דנו בשיפוץ או בטיחות המרכז המסחרי ליד דיזנגוף סנטר תל אביב?",
    "antenna_agreement_near_home": "האם אושרו אנטנות או מתקני שידור ליד יהודה המכבי 45 תל אביב?",
    "public_transport_budget_gap": "אילו החלטות תחבורה ציבורית משפיעות על תחנות ליד רכבת מרכז תל אביב?",
    "parking_and_roadwork_disruption": "אילו עבודות דרך או שינויי חניה צפויים ליד קינג ג'ורג' 33 תל אביב?",
    "sanitation_dogs_waste": "מה הוחלט על ניקיון ופינוי פסולת ליד פלורנטין 12 תל אביב?",
    "emergency_readiness_near_me": "אילו החלטות חירום ומקלטים נוגעות לאזור קפלן 6 תל אביב?",
    "family_support_services": "אילו שירותי תמיכה למשפחות אושרו ליד דרך השלום 53 תל אביב?",
    "sport_support_distribution": "אילו תמיכות ספורט או מתקנים עירוניים נוגעים לשדרות רוקח 101 תל אביב?",
    "culture_accessibility_issue": "מה הוחלט על נגישות מוסדות תרבות ליד שאול המלך 27 תל אביב?",
    "environmental_exposure_near_school": "האם דנו בזיהום או אנטנות ליד בוגרשוב 71 תל אביב?",
    "local_economy_employment_shift": "אילו החלטות על תעסוקה ומסחר נוגעות ליגאל אלון 94 תל אביב?",
    "planning_objection_window": "איזו תכנית ליד שדרות רוטשילד 22 תל אביב פתוחה להתנגדויות?",
    "neighborhood_budget_equity": "האם אזור העלייה 60 תל אביב מקבל שירותים ותקציבים כמו אזורים אחרים?",
    "street_naming_memorialization": "האם הוצעו שמות רחובות או הנצחה ליד שינקין 36 תל אביב?",
    "municipal_asset_contracts": "אילו הסכמים סביב נכסים עירוניים אושרו ליד מרמורק 10 תל אביב?",
    "religious_service_access": "האם אושר מקווה או שירות דת ליד ספיר 7 תל אביב?",
    "citywide_risk_hotspots": "אילו מוקדי סיכון עירוניים הוזכרו סביב כיכר רבין תל אביב?",
    "service_gap_for_children": "האם יש מספיק שירותים לילדים ונוער ליד לינקולן 5 תל אביב?",
}


for _scenario in SCENARIOS:
    _scenario["base_question"] = _scenario["question"]
    _scenario["display_address"] = _address_with_city(_scenario)
    _scenario["question"] = _question_with_visible_address(_scenario)


BROAD_SCENARIO_KEYS = {DEFAULT_SCENARIO_KEY}
BROAD_TOPIC_SCENARIO_KEYS = [
    "near_me_recent_decisions",
    "parcel_allocation_watch",
    "school_future_near_child",
    "public_transport_budget_gap",
    "elderly_winter_vulnerability",
    "flooding_damage_and_drainage",
    "emergency_readiness_near_me",
]
BROAD_DISABLED_TOPIC_SCENARIO_KEYS: list[str] = []


SCENARIO_BY_KEY = {scenario["key"]: scenario for scenario in SCENARIOS}
SCENARIO_BY_QUESTION = {scenario["question"]: scenario for scenario in SCENARIOS}
SCENARIO_KEY_BY_TOPIC_ID = {f"topic_{scenario['key']}": str(scenario["key"]) for scenario in SCENARIOS}
HOT_TOPIC_EXCLUDED_SCENARIO_KEYS = {"planning_objection_window"}
CATEGORY_SCENARIO_KEYS: dict[str, list[str]] = {}
for scenario in SCENARIOS:
    if str(scenario["key"]) not in HOT_TOPIC_EXCLUDED_SCENARIO_KEYS:
        CATEGORY_SCENARIO_KEYS.setdefault(str(scenario["category"]), []).append(str(scenario["key"]))
DEFAULT_SCENARIO = SCENARIO_BY_KEY[DEFAULT_SCENARIO_KEY]
CURRENT_QUESTION = DEFAULT_SCENARIO["question"]
POPULAR_QUESTIONS = [scenario["question"] for scenario in SCENARIOS]


TIMELINE_TEMPLATES = [
    ("2024-03-18", "דיון ראשון", "פתיחת דיון עירוני בנושא"),
    ("2024-04-22", "בדיקה מקצועית", "הצגת בדיקה מקצועית לוועדה"),
    ("2024-05-29", "אומדן ותקצוב", "דיון בתקציב, אחריות ולוחות זמנים"),
    ("2024-06-24", "החלטת מועצה", "קבלת החלטה או הנחיית המשך"),
    ("2024-07-16", "מעקב ביצוע", "עדכון ביצוע והשלמות"),
]


PROGRESS_TEMPLATES = [
    {"status": "yellow", "label_he": "נפתח דיון", "title_he": "דיון ראשון"},
    {"status": "yellow", "label_he": "בבדיקה", "title_he": "בדיקה מקצועית"},
    {"status": "red", "label_he": "חסם פתוח", "title_he": "נדרש תקציב או תיאום"},
    {"status": "green", "label_he": "אושרה פעולה", "title_he": "החלטת מועצה"},
    {"status": "green", "label_he": "במעקב", "title_he": "עדכון ביצוע"},
]


CATEGORY_PROTOCOL_TIMELINES = {
    "planning": [
        ("דיון בוועדת תכנון", "ועדת התכנון דנה ב{topic} ליד {address} וביקשה פירוט בעלויות, ייעוד קרקע והשפעה על שירותים ציבוריים."),
        ("בדיקת זכויות ותיאום", "אגף ההנדסה הציג בדיקת זכויות ותיאום עם אגפי נכסים, תנועה ושירותי ציבור עבור {topic}."),
        ("אומדן תקציבי", "גזברות העירייה הציגה אומדן עלויות ומקורות מימון, והוועדה דרשה לוח זמנים מפורט לפני אישור סופי."),
        ("החלטת מועצה", "המועצה אישרה לקדם את {topic} בתנאים, כולל פרסום לציבור ותיאום עם הגורמים המקצועיים."),
        ("מעקב ביצוע", "אגף הביצוע עדכן כי נפתחו פעולות תיאום ונקבע דיווח חוזר לוועדה על ההתקדמות ליד {address}."),
    ],
    "education": [
        ("דיון בוועדת חינוך", "מינהל החינוך הציג עומסי רישום ומיפוי מוסדות סביב {address} במסגרת הדיון על {topic}."),
        ("בדיקת קיבולת", "הוועדה בחנה כיתות, גנים, מעברים בטוחים וצרכי קהילה לפני שנת הלימודים הקרובה."),
        ("תקצוב והתאמות", "נבחן תקציב להתאמות במבנים, תגבור צוותים ושיפור גישה בטוחה למוסדות החינוך."),
        ("החלטת חינוך", "אושרה הנחיה להכין תכנית פעולה למוסדות הקרובים ולדווח על פתרונות רישום והסעות."),
        ("עדכון יישום", "מינהל החינוך עדכן על צעדי היערכות ועל נקודות שנותרו פתוחות מול אגפי תנועה ובטיחות."),
    ],
    "transport": [
        ("דיון תנועה ותחבורה", "אגף התנועה הציג השפעה צפויה של {topic} על רחובות ותחנות ליד {address}."),
        ("בדיקת חלופות", "נבחנו חלופות לשינוי תנועה, תחנות, חניה וזמני ביצוע כדי לצמצם פגיעה בשגרה המקומית."),
        ("אומדן הפרעה", "הוועדה דרשה אומדן עלויות, שילוט והסדרי ביניים לפני פתיחת עבודות או שינוי קווים."),
        ("אישור הסדר", "אושר לקדם הסדר תחבורתי בתנאי פרסום מוקדם לתושבים ותיאום עם מפעילי התחבורה."),
        ("מעקב הסדרים", "אגף התנועה דיווח על הכנות לביצוע ועל נקודות מעקב לאחר הפעלת ההסדר ליד {address}."),
    ],
    "welfare": [
        ("דיון רווחה ובריאות", "אגף הרווחה הציג צרכים ושירותים זמינים ליד {address} במסגרת {topic}."),
        ("בדיקת כיסוי שירותים", "נבדקו זמינות שירותים, הגעה בתחבורה, פניות קודמות וקבוצות אוכלוסייה שדורשות מענה."),
        ("תקצוב מענים", "הוועדה בחנה תקציב להפעלת שירותים, שיתופי פעולה ותגבור נקודות קבלה באזור."),
        ("החלטת שירות", "אושרה הנחיה להרחיב או לתאם מענים עירוניים ולמסור דיווח ביצוע לאגף הרווחה."),
        ("מעקב שירות", "האגף עדכן על פתיחת מענים, חסמים שנותרו ועל צורך בתיאום נוסף עם שירותי בריאות וקהילה."),
    ],
    "environment": [
        ("דיון סביבה ותשתיות", "הוועדה דנה ב{topic} ליד {address} וביקשה נתוני תשתית, מפגעים ופניות ציבור קודמות."),
        ("בדיקת שטח", "אגפי שפ\"ע, סביבה ותשתיות הציגו ממצאי שטח ופעולות תחזוקה נדרשות באזור."),
        ("אומדן טיפול", "נבחנו עלויות טיפול, אחריות קבלנים, אכיפה ולוחות זמנים להפחתת המפגע."),
        ("החלטת טיפול", "אושרה תכנית טיפול הכוללת פעולת תחזוקה, פיקוח ודיווח חוזר לוועדה."),
        ("מעקב מפגעים", "האגף עדכן על פעולות שבוצעו ועל נקודות שבהן נדרש המשך ניטור או תיאום תשתיות."),
    ],
    "emergency": [
        ("דיון מוכנות וביטחון", "אגף החירום הציג תמונת מצב של שירותי חירום ומקלטים סביב {address} במסגרת {topic}."),
        ("בדיקת כשירות", "נבדקו כשירות מתקנים, דרכי גישה, שילוט ופערים מול צרכי האוכלוסייה באזור."),
        ("תקציב מוכנות", "הוועדה בחנה תקציב לתחזוקה, סימון, תרגול והשלמת ציוד בנקודות הרלוונטיות."),
        ("החלטת חירום", "אושרה הנחיה להשלים פעולות מוכנות ולדווח על סטטוס ביצוע בישיבה הבאה."),
        ("מעקב מוכנות", "אגף החירום עדכן אילו פעולות הושלמו ומה נדרש להמשך טיפול מול אגפים נוספים."),
    ],
    "culture": [
        ("דיון תרבות ונגישות", "אגף התרבות הציג צרכים סביב {topic} ליד {address}, כולל הגעה, נגישות ובטיחות מבקרים."),
        ("בדיקת מתקנים", "נבדקו מצב מתקנים, זמני פעילות, דרכי גישה ונקודות שדורשות שיפור שירות לציבור."),
        ("אומדן שדרוג", "הוועדה בחנה תקציב לשדרוג, שילוט, התאמות נגישות ותיאום מול מפעילי המוסדות."),
        ("החלטת תרבות", "אושרה הכנת תכנית שיפור למוסדות הרלוונטיים ודיווח על סדר עדיפויות לביצוע."),
        ("מעקב שדרוג", "אגף התרבות עדכן על פעולות שכבר נקבעו ועל חסמים שנותרו מול תקציב ותפעול."),
    ],
    "commerce": [
        ("דיון מסחר ותעסוקה", "אגף הכלכלה הציג השפעות של {topic} ליד {address} על עסקים, עובדים ותנועה באזור."),
        ("בדיקת עסקים", "נבחנו פניות בעלי עסקים, נגישות, פריקה וטעינה, בטיחות וממשק עם עבודות עירוניות."),
        ("אומדן כלכלי", "הוועדה בחנה עלויות, פיצוי אפשרי, תיאום עבודות והפחתת פגיעה בפעילות מסחרית."),
        ("החלטת כלכלה", "אושרה הנחיה להכין תכנית תיאום לעסקים באזור ולפרסם לוחות זמנים ברורים."),
        ("מעקב עסקים", "אגף הכלכלה עדכן על פעולות תיאום, פניות שנענו ונושאים שממתינים להחלטת המשך."),
    ],
}


SCENARIO_PROTOCOL_DETAILS = {
    "near_me_recent_decisions": [
        ("איסוף החלטות סמוכות", "נבדקו עבודות רחוב, מבני ציבור ונקודות שירות ברדיוס הליכה סביב {address}."),
        ("סינון השפעה יומיומית", "האגפים הבחינו בין החלטות עירוניות כלליות לבין פעולות שמשנות גישה, רעש, שירות או בטיחות ליד {address}."),
        ("תיעדוף פעולות קרובות", "הוצגו עלויות לתחזוקה, שילוט, בטיחות הולכי רגל ותיאום עבודות סמוכות."),
        ("פרסום רשימת פעולות", "אושרה הכנת רשימת פעולות קרובות לבית עם אחריות אגף ולוח זמנים לכל פעולה."),
        ("דיווח מרוכז", "נקבע דיווח חוזר על החלטות שהתקדמו ועל פריטים שנותרו ללא מועד ביצוע."),
    ],
    "parcel_allocation_watch": [
        ("בירור זכויות חלקה", "הוצגו גוש, חלקה, בעלות ושימושים ציבוריים אפשריים ליד {address}."),
        ("תיאום נכסים והנדסה", "אגף נכסים ביקש השלמת נסח, תשריט וזיקת הנאה לפני קידום {topic}."),
        ("עלות שימוש ופיתוח", "הגזברות בחנה דמי שימוש, עלויות פיתוח וסיכוני תחזוקה למקרקעין."),
        ("אישור מותנה לפרסום", "אושר לקדם פרסום ותיאום ציבורי לפני חתימה על הסכם שימוש."),
        ("בדיקת מסמכים", "נדרש דיווח חוזר על השלמת המסמכים ועל התאמת הקרקע לשירות המתוכנן."),
    ],
    "school_future_near_child": [
        ("עומסי רישום", "מינהל החינוך הציג עומסי רישום וגני ילדים סמוכים ל-{address}."),
        ("מסלולי הליכה בטוחים", "נבדקו מעברי חציה, שערי מוסדות ותחנות הסעה סביב מוסדות החינוך."),
        ("התאמות לקראת שנה חדשה", "נבחן תקציב לפתיחת כיתות, הצללות, תחזוקה ושיפור נגישות."),
        ("הנחיית היערכות", "אושרה תכנית היערכות לרישום, הסעות ובטיחות בקרבת המוסדות."),
        ("עדכון רישום ובטיחות", "המינהל דיווח על מקומות פנויים, עבודות שנותרו ותיאום עם אגף התנועה."),
    ],
    "youth_parks_night_safety": [
        ("אירועי לילה בפארקים", "הוצגו דיווחי מוקד על התקהלות, רעש ותאורה בפארקים סביב {address}."),
        ("סיור ביטחון ותאורה", "נבדקו עמודי תאורה, מצלמות, דרכי גישה וזמינות סיור עירוני."),
        ("תקציב תאורה ונוכחות", "נבחן תקציב לשיפור תאורה, פעילות נוער והגברת נוכחות פיקוח."),
        ("החלטת הפעלה", "אושרה הפעלת סיורים ושעות פעילות קהילתית בפארקים שנבדקו."),
        ("מעקב פניות מוקד", "נדרש להשוות פניות מוקד לפני ואחרי הפעולות ולדווח על שינוי ברמת הסיכון."),
    ],
    "elderly_winter_vulnerability": [
        ("איתור אוכלוסייה רגישה", "אגף הרווחה הציג נתונים על קשישים בודדים ושירותי בריאות ליד {address}."),
        ("בדיקת ביקורי בית", "נבדקו רשימות קשר, ביקורי בית, מוקדי חימום והגעה בתחבורה ציבורית."),
        ("תקצוב מעני חורף", "נבחן תקציב להפעלת קו קשר, ציוד חימום וסיוע קהילתי."),
        ("הרחבת מענה חורף", "אושרה הרחבת קשר יזום ושיתוף פעולה עם מרפאות וארגוני סיוע."),
        ("דוח קשר וביקורים", "האגף עדכן על מספר ביקורים, פניות פתוחות וחסמים שנותרו לטיפול."),
    ],
    "flooding_damage_and_drainage": [
        ("מיפוי מוקדי הצפה", "הוצגו פניות הצפה, שיפועי רחוב וקולטנים באזור {address}."),
        ("בדיקת קווי ניקוז", "אגפי תשתיות ושפ\"ע הציגו ממצאי שטח וקווי ניקוז שדורשים ניקוי או תיקון."),
        ("אומדן עבודות ניקוז", "נבחנו עלויות פתיחת קולטנים, החלפת מקטעי צנרת והסדרי עבודה."),
        ("אישור טיפול תשתיתי", "אושרה תכנית תחזוקה ותיקון לפני עונת הגשמים."),
        ("בקרת אירועי גשם", "נקבע מעקב אחרי אירועי גשם ראשונים ודיווח על נקודות שבהן נשארה הצפה."),
    ],
    "commercial_center_safety": [
        ("בדיקת מבנה מסחרי", "הוצגו ליקויי בטיחות, כניסות הולכי רגל ונתיבי גישה למרכז ליד {address}."),
        ("סיור הנדסי", "מהנדס העיר ביקש בדיקת יציבות, שילוט, כיבוי אש ופריקה וטעינה."),
        ("אומדן שיקום וניהול", "נבחנו עלויות תיקון, אחריות בעלי נכסים והפחתת הפרעה לעסקים."),
        ("החלטת שיפוץ מותנה", "אושרה דרישת תיקון ולוח זמנים לביצוע מול בעלי הזכויות."),
        ("מעקב ליקויים", "נדרש דיווח על ליקויים שתוקנו ועל עסקים שעדיין מושפעים מהעבודות."),
    ],
    "antenna_agreement_near_home": [
        ("איתור מתקני שידור", "הוצגו אנטנות פעילות, מוסדות חינוך וחלקות ציבוריות סביב {address}."),
        ("בדיקת היתרים ומדידות", "נבדקו היתרי הצבה, מדידות קרינה ותיאום עם גורמי חינוך וסביבה."),
        ("אומדן פיקוח סביבתי", "נבחן תקציב למדידות חוזרות, פרסום נתונים ופיקוח על הסכמי שימוש."),
        ("החלטת פיקוח ופרסום", "אושרה דרישה לפרסום סטטוס מתקנים ומדידות באזור."),
        ("מעקב מדידות", "האגף עדכן אילו מדידות בוצעו ואילו מתקנים מחייבים בדיקה נוספת."),
    ],
    "public_transport_budget_gap": [
        ("פערי שירות בתחנות", "אגף התחבורה הציג תחנות, זמני המתנה וצירי נסיעה ליד {address}."),
        ("בדיקת קווים וחיבורים", "נבדקו קישוריות בין קווים, נגישות לתחנות והשלכות על שכונות סמוכות."),
        ("אומדן תגבור שירות", "נבחן תקציב לתגבור תחנות, שילוט, נגישות וסנכרון עם עבודות דרך."),
        ("אישור פנייה למפעילים", "אושר לפנות למפעילי תחבורה ולקדם הסדרים בתחנות שנמצאו חסרות."),
        ("מעקב זמני המתנה", "נקבע דיווח על שינוי זמני המתנה ופניות חוזרות לאחר ההסדר."),
    ],
    "parking_and_roadwork_disruption": [
        ("עבודות וחניה ברחוב", "הוצגו עבודות מתוכננות, מקומות חניה והסדרי גישה ליד {address}."),
        ("בדיקת הסדרי ביניים", "נבדקו מסלולי עקיפה, זמני ביצוע, נגישות לבתי עסק ולכניסות בניינים."),
        ("אומדן הפרעה ופיצוי", "נבחן תקציב לשילוט, פיקוח, חניות חלופיות וצמצום הפרעה."),
        ("אישור לוח זמנים", "אושר לוח זמנים לעבודות בתנאי הודעה מוקדמת והסדרי חניה זמניים."),
        ("מעקב תלונות וביצוע", "אגף התנועה עדכן על עומסים, תלונות ותיקונים בלוח הזמנים."),
    ],
    "sanitation_dogs_waste": [
        ("מפגעי ניקיון חוזרים", "הוצגו פניות ניקיון, גללי כלבים ופחי אשפה באזור {address}."),
        ("בדיקת מסלולי ניקיון", "נבדקו ימי פינוי, מיקום פחים, אכיפה ונקודות מפגע חוזרות."),
        ("תקצוב תגבור ואכיפה", "נבחן תקציב לתגבור ניקיון, שילוט ואכיפה בנקודות בעייתיות."),
        ("החלטת תגבור שירות", "אושרה תכנית תגבור ניקיון ואכיפה סביב המוקדים שהוצגו."),
        ("מעקב מפגעים", "נדרש דוח פניות חוזרות והשוואת מצב הרחוב לאחר התגבור."),
    ],
    "emergency_readiness_near_me": [
        ("מיפוי מקלטים ונקודות חירום", "אגף החירום הציג מקלטים, דרכי גישה ונקודות שירות סביב {address}."),
        ("בדיקת כשירות מתקנים", "נבדקו פתיחה, שילוט, תאורה, ציוד ונגישות במתקנים הקרובים."),
        ("תקציב תחזוקה וציוד", "נבחן תקציב להשלמת ציוד, תיקוני בטיחות ותרגול מקומי."),
        ("אישור פעולות מוכנות", "אושרה השלמת פעולות מוכנות במתקנים הקרובים ומתן עדיפות לנקודות חסרות."),
        ("מעקב כשירות", "האגף דיווח אילו מקלטים הושמשו ואילו חסמים נותרו לפני בדיקה נוספת."),
    ],
    "family_support_services": [
        ("צרכי משפחות באזור", "אגף הרווחה הציג שירותי תמיכה, מרפאות ותחבורה סביב {address}."),
        ("בדיקת זמינות מענים", "נבדקו זמני המתנה, שעות קבלה ונגישות למרכזי שירות למשפחות."),
        ("תקצוב הרחבת שירות", "נבחן תקציב להרחבת שעות פעילות, ליווי משפחות ושיתוף פעולה קהילתי."),
        ("החלטת הרחבה ותיאום", "אושרה הרחבת מענה משפחתי ותיאום מול שירותי בריאות וחינוך."),
        ("מעקב פניות פתוחות", "נדרש דוח על פניות שנענו, פניות ממתינות וחסמי נגישות."),
    ],
    "sport_support_distribution": [
        ("בחינת תמיכות ומתקנים", "אגף הספורט הציג חלוקת תמיכות ומתקנים עירוניים סביב {address}."),
        ("בדיקת נגישות ושוויון", "נבדקו שעות שימוש, נגישות בתחבורה ופיזור תמיכות בין שכונות."),
        ("אומדן חלוקה מעודכנת", "נבחן תקציב לחלוקה שמאזנת בין פעילות קיימת לבין פערי שירות."),
        ("אישור קריטריונים", "אושרו קריטריונים לפרסום תמיכות ושימוש במתקנים לפי צרכי האזור."),
        ("מעקב שימוש במתקנים", "נדרש דוח תפוסה, משתתפים ופערים שנותרו לאחר חלוקת התמיכות."),
    ],
    "culture_accessibility_issue": [
        ("נגישות מוסדות תרבות", "אגף התרבות הציג פערי נגישות, הגעה ובטיחות במוסדות ליד {address}."),
        ("סיור נגישות", "נבדקו כניסות, שילוט, תחנות קרובות, שירותים נגישים ונתיבי מילוט."),
        ("אומדן התאמות", "נבחן תקציב לרמפות, שילוט, תאורה והסדרי הגעה לאירועים."),
        ("החלטת התאמות", "אושרה הכנת תכנית התאמות למוסדות התרבות שנמצאו חסרים."),
        ("מעקב ביצוע נגישות", "אגף התרבות עדכן אילו התאמות נקבעו ומה ממתין לאישור תקציבי."),
    ],
    "environmental_exposure_near_school": [
        ("חשיפה סביב מוסד חינוך", "הוצגו מקורות זיהום, אנטנות ומוסדות חינוך סביב {address}."),
        ("בדיקת ניטור ומרחקים", "נבדקו מדידות סביבתיות, מרחקי מתקנים ונתוני פניות ציבור."),
        ("תקציב מדידות ופיקוח", "נבחן תקציב למדידות חוזרות, פרסום תוצאות ותיאום עם הנהלות מוסדות."),
        ("החלטת ניטור שקוף", "אושרה תכנית ניטור ופרסום ממצאים להורים ולציבור."),
        ("מעקב תוצאות", "האגף עדכן אילו ממצאים התקבלו ואילו בדיקות דורשות השלמה."),
    ],
    "local_economy_employment_shift": [
        ("שינויי תעסוקה באזור", "אגף הכלכלה הציג עסקים, מוקדי תעסוקה ותחבורה ליד {address}."),
        ("בדיקת השפעת מעבר עסקים", "נבדקו פניות מעסיקים, תנועת עובדים ופגיעה אפשרית במסחר המקומי."),
        ("אומדן סיוע לעסקים", "נבחן תקציב לליווי עסקים, פרסום אזורי ותיאום עבודות עירוניות."),
        ("החלטת תכנית כלכלית", "אושרה הכנת תכנית סיוע ותיאום לעסקים במוקדים שנפגעו."),
        ("מעקב עסקים ותעסוקה", "נדרש דוח על עסקים פעילים, עסקים שעזבו ופניות שנותרו פתוחות."),
    ],
    "planning_objection_window": [
        ("פתיחת חלון התנגדויות", "הוצגה תכנית סמוכה ל-{address} עם מועדי פרסום והגשת התנגדויות."),
        ("בדיקת פרסום לציבור", "נבדקו גבולות התכנית, חלקות מושפעות ודרכי יידוע לדיירים סמוכים."),
        ("אומדן טיפול בהתנגדויות", "נבחן לוח זמנים לשמיעת התנגדויות ולחוות דעת מקצועיות."),
        ("החלטת פרסום והמשך", "אושר לקדם פרסום והסבר לציבור לפני דיון המשך בתכנית."),
        ("מעקב התנגדויות", "נדרש דוח על התנגדויות שהוגשו ועל נושאים שמחייבים תיקון בתכנית."),
    ],
    "neighborhood_budget_equity": [
        ("השוואת שירותים שכונתית", "הוצגו שירותי חינוך, רווחה ובריאות סביב {address} מול אזורים אחרים."),
        ("בדיקת פערי כיסוי", "נבדקו זמינות שירותים, מרחקי הליכה ונתוני אוכלוסייה רלוונטיים."),
        ("אומדן סגירת פערים", "נבחן תקציב לתגבור שירותים באזורים שבהם נמצאו חסרים."),
        ("החלטת איזון תקציבי", "אושרה הכנת תכנית איזון בין שכונות לפי קריטריונים שקופים."),
        ("מעקב מדדי שירות", "נקבע דיווח על מדדי שירות ופערים שנותרו לאחר חלוקת התקציב."),
    ],
    "street_naming_memorialization": [
        ("הצעת שם רחוב", "הוועדה דנה בהצעת הנצחה או שם רחוב באזור {address}."),
        ("בדיקת התאמה ומיקום", "נבדקו היסטוריה עירונית, כפילות שמות, מיקום מוצע והשפעה על כתובות."),
        ("אומדן שילוט ועדכון מערכות", "נבחן תקציב לשילוט, עדכון כתובות ותיאום מול שירותים עירוניים."),
        ("החלטת ועדת שמות", "אושרה העברת ההצעה להמשך פרסום או לאישור מועצה."),
        ("מעקב פרסום ושילוט", "נדרש דיווח על פרסום לציבור, הערות שהתקבלו ומועד הצבת שילוט."),
    ],
    "municipal_asset_contracts": [
        ("בחינת נכס עירוני", "הוצגו נכסים, הסכמים קיימים ושימושים ציבוריים ליד {address}."),
        ("בדיקת חוזה ותפעול", "נבדקו תנאי חוזה, אחריות תחזוקה ונגישות השירות לציבור."),
        ("אומדן עלויות והכנסות", "נבחן איזון בין הכנסה עירונית, עלויות תחזוקה ותועלת ציבורית."),
        ("אישור הסכם מותנה", "אושר לקדם הסכם בכפוף לבדיקת יועץ משפטי ופרסום תנאים."),
        ("מעקב עמידה בתנאים", "נדרש דוח על חתימה, ביטוחים, תחזוקה ותנאים שטרם הושלמו."),
    ],
    "religious_service_access": [
        ("צרכי שירות דת", "הוצגו שירותי דת קיימים, חלקות ומוסדות סמוכים ל-{address}."),
        ("בדיקת קרקע ונגישות", "נבדקו בעלות הקרקע, דרכי גישה, תחבורה ושימושים ציבוריים שכנים."),
        ("אומדן הקמה ותפעול", "נבחן תקציב להקמה, תחזוקה, פיקוח ותיאום עם גופי שירות דת."),
        ("אישור המשך תכנון", "אושר לקדם בדיקת תכנון ושיתוף ציבור לפני החלטה סופית."),
        ("מעקב תכנון והיתרים", "נדרש דיווח על סטטוס היתרים, התנגדויות ותיאומים שנותרו."),
    ],
    "citywide_risk_hotspots": [
        ("ריכוז מוקדי סיכון", "אגף החירום הציג מוקדי הצפה, מבנים מסוכנים ובטיחות סביב {address}."),
        ("בדיקת דירוג סיכון", "נבדקו חומרת אירועים, זמינות תגובה וקרבה לאוכלוסיות רגישות."),
        ("תקצוב טיפול מדורג", "נבחן תקציב לטיפול לפי חומרה, דחיפות ואחריות אגפים."),
        ("אישור מפת עדיפות", "אושרה מפת עדיפות עירונית לטיפול במוקדי סיכון חוזרים."),
        ("מעקב מוקדים אדומים", "נדרש דוח על מוקדים שטופלו ועל מוקדים שנשארו ברמת סיכון גבוהה."),
    ],
    "service_gap_for_children": [
        ("בדיקת שירותי ילדים", "הוצגו בתי ספר, גנים, פארקים ושירותי רווחה סביב {address}."),
        ("השוואת זמינות מענים", "נבדקו מרחקי הליכה, שעות פעילות, עומסי רישום ופניות משפחות."),
        ("תקצוב השלמת פערים", "נבחן תקציב לפתיחת פעילות נוער, תגבור גנים ושיפור גישה לפארקים."),
        ("החלטת מענה לילדים", "אושרה תכנית השלמת שירותים לילדים ונוער במוקדים שנמצאו חסרים."),
        ("מעקב מדדי ילדים", "נדרש דיווח על רישום, פעילות נוער ופערים שלא נסגרו."),
    ],
}


def _event_id(key: str, date_value: str) -> str:
    return f"event_{key}_{date_value.replace('-', '_')}"


def _default_event_id(scenario: dict[str, Any]) -> str:
    return _event_id(str(scenario["key"]), TIMELINE_TEMPLATES[2][0])


def _scenario_for_question(question: Any) -> dict[str, Any]:
    return SCENARIO_BY_QUESTION.get(str(question or ""), DEFAULT_SCENARIO)


def _is_broad_scenario(scenario: dict[str, Any]) -> bool:
    return str(scenario.get("key")) in BROAD_SCENARIO_KEYS


def _scenario_for_topic_selection(top_scenario: dict[str, Any], selected_topic_node_id: str | None) -> dict[str, Any]:
    selected_key = SCENARIO_KEY_BY_TOPIC_ID.get(str(selected_topic_node_id or ""))
    if selected_key:
        return SCENARIO_BY_KEY.get(selected_key, top_scenario)
    return top_scenario


def _first_scenario_key_for_category(category_id: str | None) -> str | None:
    keys = CATEGORY_SCENARIO_KEYS.get(str(category_id or ""), [])
    return keys[0] if keys else None


def _category_for_topic_id(topic_id: str | None) -> str | None:
    key = SCENARIO_KEY_BY_TOPIC_ID.get(str(topic_id or ""))
    if not key:
        return None
    return str(SCENARIO_BY_KEY[key]["category"])


def _story_for_event(scenario: dict[str, Any], event_id: str | None) -> dict[str, Any]:
    key = str(scenario["key"])
    aliases = list(scenario["aliases"])
    layer_keys = list(scenario["layer_keys"])
    focuses = list(scenario["focuses"])
    selected_index = 2
    for index, (date_value, _, _) in enumerate(TIMELINE_TEMPLATES):
        if event_id == _event_id(key, date_value):
            selected_index = index
            break
    focus_layer = str((focuses or aliases or ["neighborhoods_area"])[0])
    story_aliases = [focus_layer, *aliases]
    if "neighborhoods_area" not in story_aliases:
        story_aliases.append("neighborhoods_area")
    return {
        "event_index": selected_index,
        "address": _address_with_city(scenario),
        "focus_layer": focus_layer,
        "govmap_layer_aliases": list(dict.fromkeys(alias for alias in story_aliases if alias)),
        "resident_layer_keys": layer_keys,
    }


def _geo_intent(scenario: dict[str, Any], selected_event_id: str | None) -> dict[str, Any]:
    story = _story_for_event(scenario, selected_event_id)
    layer_keys = story["resident_layer_keys"]
    aliases = story["govmap_layer_aliases"]
    return {
        "geo": {
            "intent": scenario["intent"],
            "confidence_label": "בינונית",
            "needs_gis": True,
            "matched_terms": [scenario["key"], scenario["topic"], "resident_mock"],
            "focus": {
                "focus_type": "place",
                "confidence_label": "בינונית",
                "place_query": story["address"],
                "address_query": story["address"],
                "matched_text": story["address"],
            },
            "resident_layer_keys": layer_keys,
            "govmap_layer_aliases": aliases,
            "focus_layer": story["focus_layer"],
            "map_context": _map_context(scenario, selected_event_id, layer_keys, aliases, story["focus_layer"]),
            "municipality_scope": {"effective_muni": TEL_AVIV_MUNICIPALITY_ID, "municipality_code": TEL_AVIV_MUNICIPALITY_CODE},
        }
    }


def _map_context(scenario: dict[str, Any], selected_event_id: str | None, layer_keys: list[str], aliases: list[str], focus_layer: str) -> dict[str, Any]:
    selected_date = _event_date_label(selected_event_id)
    progress = _progress_for_event(scenario, selected_event_id)
    return {
        "status": "mock_story",
        "municipality_code": TEL_AVIV_MUNICIPALITY_CODE,
        "municipality_id": TEL_AVIV_MUNICIPALITY_ID,
        "resident_context": {"address": _address_with_city(scenario), "label_he": "מיקום"},
        "layers": [
            {"layer_key": key, "status": "mock_linked", "count": max(1, 7 - index), "govmap_aliases": aliases[:3]}
            for index, key in enumerate(layer_keys[:6])
        ],
        "story": {
            "scenario_key": scenario["key"],
            "timeline_event_id": selected_event_id,
            "focus_layer": focus_layer,
            "question": scenario["question"],
            "map_stability": "stable_per_selected_topic",
        },
        "progress": progress,
        "caveats": [
            f"דוגמת הדגמה תל אביבית: הפרוטוקול מתוזמן ל-{selected_date}; שכבות GovMap נשארות הקשר עדכני וקבוע לנושא, וציר הזמן משנה סטטוס התקדמות בלבד.",
        ],
    }


def _event_date_label(event_id: str | None) -> str:
    text = str(event_id or "")
    for date_value, _, _ in TIMELINE_TEMPLATES:
        if date_value.replace("-", "_") in text:
            return _date_label(date_value)
    return _date_label(TIMELINE_TEMPLATES[2][0])


def _date_label(date_value: str) -> str:
    year, month, day = date_value.split("-")
    return f"{day}.{month}.{year}"


def _event_index(event_id: str | None) -> int:
    text = str(event_id or "")
    for index, (date_value, _, _) in enumerate(TIMELINE_TEMPLATES):
        if date_value.replace("-", "_") in text:
            return index
    return 2


def _progress_for_event(scenario: dict[str, Any], event_id: str | None) -> dict[str, Any]:
    index = _event_index(event_id)
    date_value = TIMELINE_TEMPLATES[index][0]
    progress = dict(PROGRESS_TEMPLATES[index])
    progress.update(
        {
            "protocol_date": date_value,
            "protocol_date_label": _date_label(date_value),
            "topic": scenario["topic"],
            "address": _address_with_city(scenario),
            "event_index": index,
        }
    )
    return progress


def _protocol_timeline_rows(scenario: dict[str, Any]) -> list[tuple[str, str, str]]:
    templates = CATEGORY_PROTOCOL_TIMELINES.get(str(scenario.get("category")), CATEGORY_PROTOCOL_TIMELINES["planning"])
    details = SCENARIO_PROTOCOL_DETAILS.get(str(scenario.get("key")), [])
    address = _address_with_city(scenario)
    topic = str(scenario.get("topic") or "הנושא")
    rows: list[tuple[str, str, str]] = []
    for index, (date_value, _, _) in enumerate(TIMELINE_TEMPLATES):
        title_template, summary_template = templates[index]
        detail_title, detail_summary = details[index] if index < len(details) else (topic, "")
        summary_parts = [detail_summary.format(topic=topic, address=address)] if detail_summary else []
        summary_parts.append(summary_template.format(topic=topic, address=address))
        rows.append(
            (
                date_value,
                f"{title_template.format(topic=topic, address=address)}: {detail_title.format(topic=topic, address=address)}",
                " ".join(part for part in summary_parts if part),
            )
        )
    return rows


def _display_brief(scenario: dict[str, Any]) -> str:
    return str(scenario.get("brief") or "").replace("מוקאפ", "תצוגה לדוגמה")


def _evidence_rows(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, (date_value, title, summary) in enumerate(_protocol_timeline_rows(scenario), start=1):
        evidence_id = f"evidence_{scenario['key']}_{index}"
        rows.append(
            {
                "id": evidence_id,
                "source_type": "protocol",
                "source_title": f"פרוטוקול תל אביב {title} - {scenario['topic']}",
                "source_url": f"https://example.local/tel-aviv/protocols/{scenario['key']}-{date_value}.pdf",
                "retrieval_artifact_id": f"artifact_{scenario['key']}_{index}",
                "artifact_kind": "decision_unit" if index in {3, 4} else "agenda_item",
                "retrieval_set_id": f"retrieval_set_{scenario['key']}_tel_aviv",
                "header_path": ["עיריית תל אביב-יפו", CATEGORY_LABELS.get(scenario["category"], "שירותים עירוניים"), scenario["topic"]],
                "page_span": {"start": index + 2, "end": index + 2},
                "start_offset": 100 * index,
                "end_offset": 100 * index + 78,
                "bbox": [90, 160 + index * 18, 520, 220 + index * 18],
                "text": summary,
                "confidence_label": "גבוהה" if index >= 3 else "בינונית",
                "extraction_warnings": ["דוגמת הדגמה; אינו פרוטוקול אמיתי."],
            }
        )
    return rows


def _resident_evidence_link(evidence_ref: str) -> dict[str, str]:
    return {"label_he": "מקור", "icon": "document-link", "evidence_ref": evidence_ref}


def _decisions(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    kind_codes = ["INQUIRY", "DISCUSSION", "BUDGET_REVIEW", "APPROVAL", "FOLLOW_UP"]
    outcome_codes = ["PENDING", "NOTED", "PENDING", "APPROVED_WITH_CONDITIONS", "NOTED"]
    for index, (date_value, title, summary) in enumerate(_protocol_timeline_rows(scenario), start=1):
        evidence_ref = f"evidence_{scenario['key']}_{index}"
        decision_id = f"decision_{scenario['key']}_{index}"
        kind_code = kind_codes[index - 1]
        outcome_code = outcome_codes[index - 1]
        decisions.append(
            {
                "id": decision_id,
                "title": f"{title}: {scenario['topic']}",
                "raw_decision_text": summary,
                "summary": summary,
                "primary_time": {
                    "start": date_value,
                    "end": None,
                    "precision": "day",
                    "kind": "mock_protocol_date",
                    "label_he": "תאריך פרוטוקול מוקאפ",
                    "confidence_label": "בינונית",
                    "evidence_refs": [evidence_ref],
                },
                "time_anchors": [
                    {
                        "kind": "mock_protocol_date",
                        "start": date_value,
                        "end": None,
                        "precision": "day",
                        "label_he": "תאריך פרוטוקול מוקאפ",
                        "confidence_label": "בינונית",
                        "evidence_refs": [evidence_ref],
                    }
                ],
                "decision_kind": {
                    "code": kind_code,
                    "label_he": _decision_kind_label(kind_code),
                    "vocabulary": "municipal-decision-kind:v1",
                    "raw_text": summary,
                    "confidence_label": "בינונית",
                },
                "outcome_status": {
                    "code": outcome_code,
                    "label_he": _outcome_status_label(outcome_code),
                    "vocabulary": "municipal-outcome-status:v1",
                    "raw_text": summary,
                    "confidence_label": "בינונית",
                },
                "legal_effect": {
                    "code": "BINDING" if kind_code == "APPROVAL" else "PROCEDURAL",
                    "label_he": "מחייב" if kind_code == "APPROVAL" else "תהליכי",
                    "vocabulary": "municipal-legal-effect:v1",
                    "confidence_label": "בינונית",
                },
                "topic_ids": [f"topic_{scenario['key']}"],
                "entity_ids": [f"entity_{scenario['key']}_focus"],
                "timeline_event_ids": [_event_id(str(scenario["key"]), date_value)],
                "evidence_refs": [evidence_ref],
                "resident_evidence_links": [_resident_evidence_link(evidence_ref)],
                "normalized_by": "mock_tel_aviv_resident_story",
                "curation_status": "mock",
                "limitations": ["דוגמת הדגמה; לא החלטה אמיתית."],
            }
        )
    return decisions


def _decision_kind_label(code: str) -> str:
    return {
        "INQUIRY": "שאילתה",
        "DISCUSSION": "דיון",
        "BUDGET_REVIEW": "בדיקת תקציב",
        "APPROVAL": "אישור",
        "FOLLOW_UP": "מעקב ביצוע",
    }.get(code, "פעולה")


def _outcome_status_label(code: str) -> str:
    return {
        "APPROVED_WITH_CONDITIONS": "אושר בתנאים",
        "PENDING": "בהמשך טיפול",
        "NOTED": "נרשם",
    }.get(code, "לא ידוע")


def _topic_node(*, topic_id: str, label: str, primary_category_id: str, mention_count: int, decision_count: int, parent_id: str | None = None, child_ids: list[str] | None = None, depth: int = 1) -> dict[str, Any]:
    root_id = f"topic_root_{primary_category_id}"
    return {
        "id": topic_id,
        "label": label,
        "origin": "mock_resident_catalog",
        "curation_status": "mock",
        "generated_label": label,
        "curated_label": label,
        "topic_type": {"code": "RESIDENT_QUESTION", "label_he": "שאלת תושב", "vocabulary": "municipal-topic-type:v1"},
        "category_ids": [primary_category_id],
        "primary_category_id": primary_category_id,
        "parent_id": parent_id,
        "child_ids": child_ids or [],
        "sibling_ids": [],
        "path_ids": list(dict.fromkeys(value for value in [root_id, parent_id, topic_id] if value)),
        "depth": depth,
        "sort_order": mention_count,
        "mention_count": mention_count,
        "decision_count": decision_count,
        "recent_activity_at": TIMELINE_TEMPLATES[-1][0],
        "time_rollup": {"first_seen": TIMELINE_TEMPLATES[0][0], "last_seen": TIMELINE_TEMPLATES[-1][0], "active_years": [2024]},
        "decision_rollups": {"decision_kind": {"INQUIRY": 1, "DISCUSSION": 1, "APPROVAL": 1}, "outcome_status": {"PENDING": 2, "NOTED": 2, "APPROVED_WITH_CONDITIONS": 1}, "legal_effect": {"PROCEDURAL": 4, "BINDING": 1}},
        "merged_from_topic_ids": [],
        "split_from_topic_id": None,
        "hidden_from_public": False,
        "confidence_label": "בינונית",
        "evidence_refs": [],
    }


def _related_topics(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"id": f"related_{scenario['key']}", "label": scenario["topic"], "relation_type": "topic", "relation_label_he": "נושא", "target_topic_id": f"topic_{scenario['key']}", "count": 5, "selected": True},
        {"id": f"related_{scenario['category']}", "label": CATEGORY_LABELS.get(scenario["category"], "שירותים עירוניים"), "relation_type": "category", "relation_label_he": "קטגוריה", "target_topic_id": f"topic_root_{scenario['category']}", "count": 12, "selected": False},
        {"id": "related_neighborhood_context", "label": "הקשר שכונתי", "relation_type": "geography", "relation_label_he": "גאוגרפיה", "target_topic_id": "topic_neighborhood_context", "count": 9, "selected": False},
        {"id": "related_gis_layers", "label": "שכבות GIS לתושב", "relation_type": "dataset", "relation_label_he": "שכבה", "target_topic_id": "topic_resident_gis_layers", "count": len(scenario["layer_keys"]), "selected": False},
    ]


def _timeline_events(scenario: dict[str, Any], selected_event_id: str) -> list[dict[str, Any]]:
    decisions = _decisions(scenario)
    by_date = {decision["primary_time"]["start"]: decision for decision in decisions}
    events: list[dict[str, Any]] = []
    for date_value, title, summary in _protocol_timeline_rows(scenario):
        event_id = _event_id(str(scenario["key"]), date_value)
        decision = by_date[date_value]
        events.append(
            {
                "id": event_id,
                "date": date_value,
                "date_label": _date_label(date_value),
                "title": title,
                "summary": summary,
                "selected": event_id == selected_event_id,
                "decision_ids": [decision["id"]],
                "evidence_refs": decision["evidence_refs"],
                "progress": _progress_for_event(scenario, event_id),
            }
        )
    return events


def _map_entities(scenario: dict[str, Any], selected_event_id: str) -> list[dict[str, Any]]:
    story = _story_for_event(scenario, selected_event_id)
    focus_label = story["focus_layer"]
    return [
        {
            "id": f"entity_{scenario['key']}_resident_area",
            "label": "אזור מגורים בתל אביב",
            "entity_type": {"code": "RESIDENT_AREA", "label_he": "אזור תושב", "vocabulary": "municipal-entity-type:v1"},
            "spatial_representation": "schematic",
            "schematic_shape": {"kind": "polygon", "coordinates": [[336, 330], [377, 238], [455, 250], [518, 287], [491, 344], [426, 341], [404, 392]]},
            "real_geometry": None,
            "geometry_provenance": None,
            "confidence_label": "בינונית",
            "uncertainty_reasons": ["מוקאפ סכמטי; המפה האמיתית נטענת דרך GovMap לפי שכבות הסיפור."],
            "active_from": TIMELINE_TEMPLATES[0][0],
            "active_to": None,
            "activity_score": 80,
            "is_recent_high_activity": True,
            "topic_ids": [f"topic_{scenario['key']}"],
            "decision_ids": [f"decision_{scenario['key']}_3"],
            "evidence_refs": [f"evidence_{scenario['key']}_3"],
            "selected": False,
        },
        {
            "id": f"entity_{scenario['key']}_focus",
            "label": f"מוקד מפה: {focus_label}",
            "entity_type": {"code": "GIS_STORY_FOCUS", "label_he": "מוקד GIS", "vocabulary": "municipal-entity-type:v1"},
            "spatial_representation": "schematic",
            "schematic_shape": {"kind": "point", "coordinates": [[468, 302]]},
            "real_geometry": None,
            "geometry_provenance": None,
            "confidence_label": "בינונית",
            "uncertainty_reasons": ["מוקד הסיפור קבוע לנושא; אירועי ציר הזמן משנים סטטוס פרוטוקולי ולא שכבות מפה."],
            "active_from": TIMELINE_TEMPLATES[2][0],
            "active_to": None,
            "activity_score": 92,
            "is_recent_high_activity": True,
            "topic_ids": [f"topic_{scenario['key']}"],
            "decision_ids": [f"decision_{scenario['key']}_{story['event_index'] + 1}"],
            "evidence_refs": [f"evidence_{scenario['key']}_{story['event_index'] + 1}"],
            "selected": True,
        },
    ]


def _discovery_panel(top_scenario: dict[str, Any], selected_topic_node_id: str) -> dict[str, Any]:
    category_counts = {"planning": 342, "transport": 128, "education": 95, "welfare": 76, "environment": 64, "emergency": 42, "culture": 31, "commerce": 27}
    all_categories = list(CATEGORY_LABELS)
    active_scenario = _scenario_for_topic_selection(top_scenario, selected_topic_node_id)
    selected_category = str(active_scenario["category"])
    enabled_category_ids = set(CATEGORY_SCENARIO_KEYS)
    hot_topic_keys = CATEGORY_SCENARIO_KEYS.get(selected_category, [str(active_scenario["key"])])

    def topic_row(key: str, index: int) -> dict[str, Any]:
        row_scenario = SCENARIO_BY_KEY[key]
        return {
            "id": f"topic_{key}",
            "label": row_scenario["topic"],
            "count": max(5, 24 - index * 2),
            "selected": selected_topic_node_id == f"topic_{key}",
            "disabled": False,
            "disabled_reason": None,
        }

    hot_topics = [topic_row(key, index) for index, key in enumerate(hot_topic_keys) if key in SCENARIO_BY_KEY]

    tree_children = [
        {"id": row["id"], "label": row["label"], "count": row["count"], "selected": row["selected"], "disabled": row["disabled"], "disabled_reason": row["disabled_reason"]}
        for row in hot_topics[:5]
    ]
    return {
        "categories": [
            {
                "id": key,
                "label": CATEGORY_LABELS[key],
                "count": category_counts[key],
                "selected": key == selected_category,
                "disabled": key not in enabled_category_ids,
                "disabled_reason": "אין נושאי הדגמה בקטגוריה" if key not in enabled_category_ids else None,
            }
            for key in all_categories
        ],
        "hot_topics": hot_topics,
        "focused_topic_tree_context": {
            "selected_topic_id": selected_topic_node_id,
            "root": {"id": f"topic_root_{selected_category}", "label": CATEGORY_LABELS.get(selected_category, "שירותים עירוניים")},
            "parent": {"id": f"topic_root_{selected_category}", "label": CATEGORY_LABELS.get(selected_category, "שירותים עירוניים")},
            "breadcrumbs": [
                {"id": f"topic_root_{selected_category}", "label": CATEGORY_LABELS.get(selected_category, "שירותים עירוניים")},
                {"id": selected_topic_node_id, "label": active_scenario["topic"]},
            ],
            "siblings": [],
            "children": tree_children,
            "nearby_topics": [],
            "collapsed_categories": [
                {"id": key, "label": CATEGORY_LABELS[key]}
                for key in all_categories
                if key != selected_category
            ][:2],
        },
    }


def _gis_story_review_panel(selected_story_id: str | None = None) -> dict[str, Any]:
    report = _load_gis_story_report()
    stories = _gis_story_rows(report)
    strong = _gis_story_lane_rows(stories, "strong_story")
    needs_review = _gis_story_lane_rows(stories, "needs_review")
    story_searches = [_gis_story_search_row(row) for row in [*strong, *needs_review]]
    return {
        "status": "loaded" if stories else "missing_story_report",
        "source_path": str(GIS_STORY_REPORT_PATH),
        "title_he": "סיפורי GIS לפרוטוקולים",
        "subtitle_he": "9 סיפורים חזקים מול 9 סיפורים שדורשים שיפוט אנושי לפני הצגה לתושב.",
        "summary": {
            "story_count": int(report.get("story_count") or len(stories)),
            "strong_story_count": len([row for row in stories if row.get("human_judgement", {}).get("judgement") == "strong_story"]),
            "needs_review_count": len([row for row in stories if row.get("human_judgement", {}).get("judgement") == "needs_review"]),
            "mock_seed_only_count": len([row for row in stories if row.get("human_judgement", {}).get("judgement") == "mock_seed_only"]),
            "traffic_light_counts": dict(report.get("traffic_light_counts") or {}),
        },
        "popular_story_searches": story_searches,
        "lanes": [
            {
                "id": "strong_story",
                "title_he": "מוכן לדמו",
                "count": len(strong),
                "description_he": "קשרים חזקים בין כמה אירועי פרוטוקול, שכבות GIS וסטטוס התקדמות.",
                "stories": [_gis_story_card(row, selected_story_id=selected_story_id) for row in strong],
            },
            {
                "id": "needs_review",
                "title_he": "דורש בדיקה",
                "count": len(needs_review),
                "description_he": "קבוצות אפשריות, אבל חסר תאריך אמיתי, מקור נוסף, או ודאות נושאית מספקת.",
                "stories": [_gis_story_card(row, selected_story_id=selected_story_id) for row in needs_review],
            },
        ],
    }


def _load_gis_story_report() -> dict[str, Any]:
    try:
        payload = json.loads(GIS_STORY_REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"stories": [], "story_count": 0, "traffic_light_counts": {}}
    return payload if isinstance(payload, dict) else {"stories": [], "story_count": 0, "traffic_light_counts": {}}


def _load_gis_story_govmap_execution_report() -> dict[str, Any]:
    try:
        payload = json.loads(GIS_STORY_GOVMAP_EXECUTION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"stories": []}
    return payload if isinstance(payload, dict) else {"stories": []}


def _gis_story_govmap_execution_by_id(story_id: str | None) -> dict[str, Any] | None:
    story_id = str(story_id or "")
    if not story_id:
        return None
    report = _load_gis_story_govmap_execution_report()
    return next((row for row in report.get("stories", []) if isinstance(row, dict) and str(row.get("story_id") or "") == story_id), None)


def _gis_story_rows(report: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = report if isinstance(report, dict) else _load_gis_story_report()
    return [row for row in payload.get("stories", []) if isinstance(row, dict)]


def _gis_story_lane_rows(stories: list[dict[str, Any]], judgement: str) -> list[dict[str, Any]]:
    return [row for row in stories if row.get("human_judgement", {}).get("judgement") == judgement][:9]


def _gis_story_search_row(story: dict[str, Any]) -> dict[str, Any]:
    title = str(story.get("title_he") or "סיפור GIS")
    judgement = str(story.get("human_judgement", {}).get("judgement") or "")
    query_prefix = "מה התקדם בסיפור" if judgement == "strong_story" else "הצג לבדיקה את הסיפור"
    query = f"{query_prefix}: {title}?"
    return {
        "query": query,
        "story_id": str(story.get("story_id") or ""),
        "title_he": title,
        "judgement": judgement,
        "traffic_light": str(story.get("traffic_light") or "yellow"),
    }


def _gis_story_by_id(story_id: str | None) -> dict[str, Any] | None:
    story_id = str(story_id or "")
    if not story_id:
        return None
    return next((row for row in _gis_story_rows() if str(row.get("story_id") or "") == story_id), None)


def _gis_story_by_query(query: str | None) -> dict[str, Any] | None:
    query = str(query or "")
    for row in _gis_story_rows():
        if _gis_story_search_row(row)["query"] == query:
            return row
    return None


def _popular_story_questions() -> list[str]:
    stories = _gis_story_rows()
    return [_gis_story_search_row(row)["query"] for row in [*_gis_story_lane_rows(stories, "strong_story"), *_gis_story_lane_rows(stories, "needs_review")]]


def _story_location_label(story: dict[str, Any]) -> str:
    labels = [str(value) for value in (story.get("location_labels") or []) if str(value or "").strip()]
    if labels:
        return labels[0]
    return str(story.get("municipality_slug") or "רשות לא מזוהה")


def _story_layer_rows(story: dict[str, Any]) -> list[dict[str, Any]]:
    summary = story.get("gis_summary") if isinstance(story.get("gis_summary"), dict) else {}
    rows = summary.get("top_layers") if isinstance(summary.get("top_layers"), list) else []
    return [row for row in rows if isinstance(row, dict)]


def _resident_layer_group_payload(layer_key: str) -> dict[str, Any] | None:
    try:
        group = load_resident_gis_registry().layer_by_key.get(layer_key)
    except Exception:
        return None
    return group.to_payload() if group else None


def _story_layer_govmap_aliases(layer_key: str) -> list[str]:
    group = _resident_layer_group_payload(layer_key)
    if not group:
        return []
    aliases = [str(value) for value in group.get("govmap_aliases", []) if str(value or "").strip()]
    aliases.extend(str(row.get("alias") or "") for row in group.get("govmap_layers", []) if isinstance(row, dict) and str(row.get("alias") or "").strip())
    return list(dict.fromkeys(aliases))


def _story_map_entities(story: dict[str, Any]) -> list[dict[str, Any]]:
    story_id = str(story.get("story_id") or "gis_story")
    title = str(story.get("title_he") or "סיפור GIS")
    location = _story_location_label(story)
    protocols = [str(value) for value in (story.get("source_protocols") or []) if str(value or "")]
    layers = _story_layer_rows(story)
    evidence_refs = protocols[:3]
    entities = [
        {
            "id": f"entity_{story_id}_municipality_context",
            "label": f"הקשר עירוני: {location}",
            "entity_type": {"code": "GIS_STORY_MUNICIPALITY", "label_he": "הקשר עירוני", "vocabulary": "municipal-entity-type:v1"},
            "spatial_representation": "schematic",
            "schematic_shape": {"kind": "polygon", "coordinates": [[336, 330], [377, 238], [455, 250], [518, 287], [491, 344], [426, 341], [404, 392]]},
            "real_geometry": None,
            "geometry_provenance": None,
            "confidence_label": "בינונית",
            "uncertainty_reasons": ["אין גיאומטריה מאומתת בדשבורד; זהו הקשר GIS סכמטי לפי סיפור הפרוטוקולים."],
            "active_from": None,
            "active_to": None,
            "activity_score": int(story.get("event_count") or 0),
            "is_recent_high_activity": bool(story.get("traffic_light") == "red"),
            "topic_ids": [story_id],
            "decision_ids": protocols,
            "evidence_refs": evidence_refs,
            "selected": False,
        },
        {
            "id": f"entity_{story_id}_focus",
            "label": f"מוקד סיפור: {title}",
            "entity_type": {"code": "GIS_STORY_FOCUS", "label_he": "מוקד סיפור GIS", "vocabulary": "municipal-entity-type:v1"},
            "spatial_representation": "schematic",
            "schematic_shape": {"kind": "point", "coordinates": [[468, 302]]},
            "real_geometry": None,
            "geometry_provenance": None,
            "confidence_label": "בינונית",
            "uncertainty_reasons": ["מוקד מפה סכמטי; השכבות מוצגות כהקשר ולא כהוכחת מיקום היסטורית."],
            "active_from": None,
            "active_to": None,
            "activity_score": int(story.get("source_protocol_count") or len(protocols) or 1),
            "is_recent_high_activity": True,
            "topic_ids": [story_id],
            "decision_ids": protocols,
            "evidence_refs": evidence_refs,
            "selected": True,
        },
    ]
    for index, layer in enumerate(layers[:3]):
        label = str(layer.get("display_name_he") or layer.get("layer_key") or "שכבת GIS")
        entities.append(
            {
                "id": f"entity_{story_id}_layer_{index + 1}",
                "label": f"שכבה קשורה: {label}",
                "entity_type": {"code": "GIS_STORY_LAYER", "label_he": "שכבת GIS", "vocabulary": "municipal-entity-type:v1"},
                "spatial_representation": "schematic",
                "schematic_shape": {"kind": "point", "coordinates": [[410 + (index * 42), 245 + (index * 35)]]},
                "real_geometry": None,
                "geometry_provenance": None,
                "confidence_label": "בינונית",
                "uncertainty_reasons": ["שכבה מקושרת מהסיפור; ללא גיאומטריה מאומתת בדשבורד."],
                "active_from": None,
                "active_to": None,
                "activity_score": int(layer.get("event_count") or 0),
                "is_recent_high_activity": False,
                "topic_ids": [story_id],
                "decision_ids": protocols,
                "evidence_refs": evidence_refs,
                "selected": False,
            }
        )
    return entities


def _story_map_context(story: dict[str, Any], progress: dict[str, Any], execution: dict[str, Any] | None = None) -> dict[str, Any]:
    story_id = str(story.get("story_id") or "")
    title = str(story.get("title_he") or "סיפור GIS")
    location = _story_location_label(story)
    summary = story.get("gis_summary") if isinstance(story.get("gis_summary"), dict) else {}
    layers = [
        {
            "layer_key": str(row.get("layer_key") or "story_layer"),
            "status": "story_linked_context",
            "count": int(row.get("event_count") or 0),
            "display_name_he": str(row.get("display_name_he") or row.get("layer_key") or "שכבת GIS"),
            "govmap_aliases": _story_layer_govmap_aliases(str(row.get("layer_key") or "")),
            "activation_source": "selected_story",
        }
        for row in _story_layer_rows(story)
    ]
    execution_queries = [row for row in (execution or {}).get("queries", []) if isinstance(row, dict)]
    execution_status_counts: dict[str, int] = {}
    for row in execution_queries:
        status = str(row.get("status") or "unknown")
        execution_status_counts[status] = execution_status_counts.get(status, 0) + 1
    execution_summary = {
        "status": "available" if execution else "missing_execution_artifact",
        "source_path": str(GIS_STORY_GOVMAP_EXECUTION_PATH),
        "query_count": len(execution_queries),
        "status_counts": execution_status_counts,
        "loaded_real_geometry_count": execution_status_counts.get("loaded_real_geometry", 0),
        "empty_query_count": execution_status_counts.get("query_ready_but_empty", 0),
        "not_executed_count": execution_status_counts.get("not_executed_live_disabled", 0),
        "fallback_geocode_count": len([row for row in execution_queries if str(row.get("geocode_match_quality") or "").startswith("alternate_") or str(row.get("geocode_match_quality") or "") == "external_open_data_candidate"]),
    }
    execution_caveat = (
        f"GovMap execution artifact: {execution_summary['loaded_real_geometry_count']} שכבות/שאילתות עם תוצאות, {execution_summary['empty_query_count']} ריקות, {execution_summary['not_executed_count']} לא הורצו, {execution_summary['fallback_geocode_count']} עם גיאוקוד fallback ולא התאמה מדויקת."
        if execution
        else "אין עדיין ארטיפקט הרצה אמיתי ל-GovMap עבור הסיפור הנבחר; המפה מציגה הקשר סכמטי בלבד."
    )
    return {
        "status": "generated_protocol_gis_story",
        "municipality_code": str(story.get("municipality_slug") or ""),
        "municipality_id": str(story.get("municipality_slug") or ""),
        "resident_context": {"address": location, "label_he": "רשות / מוקד סיפור"},
        "layers": layers,
        "story": {
            "source": "generated_protocol_gis_story",
            "selected_gis_story_id": story_id,
            "story_title_he": title,
            "traffic_light": story.get("traffic_light"),
            "progression": story.get("progression"),
            "question": _gis_story_search_row(story)["query"],
            "map_stability": "story_context_layers_without_verified_geometry",
        },
        "story_anchor": {
            "label_he": location,
            "marker_kind": "circle",
            "resolution_status": "city_level_fallback" if execution_summary.get("fallback_geocode_count") else "story_context",
            "exact_facility_resolved": False if execution_summary.get("fallback_geocode_count") else None,
        },
        "real_gis_execution": execution_summary,
        "progress": progress,
        "caveats": [
            execution_caveat,
            f"סיפור GIS מתוך פרוטוקולי V3 עבור {location}: השכבות מקושרות מהאירועים, אך המפה כאן סכמטית ואינה גיאומטריה מאומתת.",
            f"שאילתות GovMap מוכנות: {int(summary.get('ready_govmap_query_count') or 0)}; חסומות: {int(summary.get('blocked_govmap_query_count') or 0)}.",
        ],
    }


def _gis_story_card(story: dict[str, Any], *, selected_story_id: str | None = None) -> dict[str, Any]:
    timeline_events = [row for row in story.get("timeline_events", []) if isinstance(row, dict)]
    first_event = timeline_events[0] if timeline_events else {}
    latest_event = timeline_events[-1] if timeline_events else {}
    judge = story.get("human_judgement") if isinstance(story.get("human_judgement"), dict) else {}
    story_id = str(story.get("story_id") or "")
    return {
        "story_id": story_id,
        "story_query": _gis_story_search_row(story)["query"],
        "title_he": str(story.get("title_he") or "סיפור ללא כותרת"),
        "municipality_slug": str(story.get("municipality_slug") or ""),
        "traffic_light": str(story.get("traffic_light") or "yellow"),
        "progression": str(story.get("progression") or "unknown"),
        "event_count": int(story.get("event_count") or len(timeline_events)),
        "source_protocol_count": int(story.get("source_protocol_count") or 0),
        "subject_terms": [str(value) for value in (story.get("subject_terms") or [])[:4]],
        "first_event_label": str(first_event.get("date_label") or first_event.get("date") or ""),
        "latest_event_label": str(latest_event.get("date_label") or latest_event.get("date") or ""),
        "latest_event_title": str(latest_event.get("title") or ""),
        "mock_warning": bool(story.get("has_mock_completion")),
        "judge_confidence": str(judge.get("confidence") or ""),
        "judge_reason": str(judge.get("reason") or ""),
        "selected": bool(story_id and story_id == str(selected_story_id or "")),
    }


def _gis_story_timeline_events(story: dict[str, Any], selected_event_id: str | None = None) -> list[dict[str, Any]]:
    raw_events = [row for row in story.get("timeline_events", []) if isinstance(row, dict)]
    selected_ids = {str(selected_event_id or "")}
    visible = raw_events[-5:] if len(raw_events) > 5 else raw_events
    if not selected_ids or not next((row for row in visible if str(row.get("id") or "") in selected_ids), None):
        selected_ids = {str((visible[-1] if visible else {}).get("id") or "")}
    out: list[dict[str, Any]] = []
    for row in visible:
        event_id = str(row.get("id") or row.get("event_id") or "")
        mock_fields = [str(value) for value in (row.get("mock_fields") or [])]
        real_fields = ["source_text", "evidence_quotes", "action_type", "matter"]
        if not row.get("date_is_mock"):
            real_fields.insert(0, "date")
        inferred_fields = ["progress_status"]
        if row.get("gis_layer_keys"):
            inferred_fields.append("gis_layers")
        if int(row.get("ready_govmap_query_count") or 0) or int(row.get("blocked_govmap_query_count") or 0):
            inferred_fields.append("govmap_query_links")
        out.append(
            {
                "id": event_id,
                "date": str(row.get("date") or ""),
                "date_label": str(row.get("date_label") or row.get("date") or ""),
                "title": str(row.get("title") or row.get("event_status") or "אירוע"),
                "summary": str(row.get("summary") or row.get("matter_he") or ""),
                "selected": event_id in selected_ids,
                "decision_ids": [str(row.get("artifact_id") or event_id)],
                "evidence_refs": [str(row.get("artifact_id") or event_id)],
                "progress": dict(row.get("progress") or {"status": row.get("traffic_light") or "yellow"}),
                "date_is_mock": bool(row.get("date_is_mock")),
                "date_source": str(row.get("date_source") or ""),
                "real_fields": real_fields,
                "mock_fields": mock_fields,
                "inferred_fields": inferred_fields,
                "provenance_label_he": "תאריך מוקאפ" if row.get("date_is_mock") else "תאריך מהמקור",
                "provenance_detail_he": f"מקור תאריך: {row.get('date_source') or 'לא ידוע'}",
                "gis_layer_keys": [str(value) for value in (row.get("gis_layer_keys") or [])],
                "ready_govmap_query_count": int(row.get("ready_govmap_query_count") or 0),
                "blocked_govmap_query_count": int(row.get("blocked_govmap_query_count") or 0),
            }
        )
    return out


def build_mock_rag_dashboard_payload(question: str | None = None, selected_event_id: str | None = None, selected_topic_node_id: str | None = None, selected_gis_story_id: str | None = None) -> dict[str, Any]:
    """Return a Tel Aviv resident mock payload shaped like the future dashboard API."""

    current_question = str(question or CURRENT_QUESTION)
    selected_story = _gis_story_by_id(selected_gis_story_id)
    if selected_story is None:
        selected_story = _gis_story_by_query(current_question)
    selected_gis_story_id = str(selected_story.get("story_id") or "") if selected_story else None
    top_scenario = _scenario_for_question(current_question)
    selected_topic_node_id = selected_topic_node_id or f"topic_{top_scenario['key']}"
    scenario = _scenario_for_topic_selection(top_scenario, selected_topic_node_id)
    selected_topic_node_id = f"topic_{scenario['key']}" if selected_topic_node_id not in SCENARIO_KEY_BY_TOPIC_ID and scenario is not top_scenario else selected_topic_node_id
    if not selected_story:
        selected_event_id = selected_event_id if selected_event_id in {_event_id(str(scenario["key"]), date_value) for date_value, _, _ in TIMELINE_TEMPLATES} else _default_event_id(scenario)
    evidence = _evidence_rows(scenario)
    decisions = _decisions(scenario)
    entities = _map_entities(scenario, selected_event_id)
    related = _related_topics(scenario)
    geo_intent = _geo_intent(scenario, selected_event_id)
    story = geo_intent["geo"]["map_context"]["story"]
    story["top_level_question"] = top_scenario["question"]
    story["active_mini_question"] = scenario["question"]
    story["selected_topic_node_id"] = selected_topic_node_id
    progress = geo_intent["geo"]["map_context"].get("progress") or {}
    timeline_events = _timeline_events(scenario, selected_event_id)
    timeline_selected_event_id = selected_event_id
    selected_story_card = _gis_story_card(selected_story, selected_story_id=selected_gis_story_id) if selected_story else None
    selected_map_entity_id = f"entity_{scenario['key']}_focus"
    if selected_story:
        story_timeline = _gis_story_timeline_events(selected_story, selected_event_id)
        if story_timeline:
            timeline_events = story_timeline
            timeline_selected_event_id = next((row["id"] for row in story_timeline if row.get("selected")), story_timeline[-1]["id"])
            selected_timeline_event = next((row for row in story_timeline if row.get("id") == timeline_selected_event_id), story_timeline[-1])
            progress = selected_timeline_event.get("progress") or progress
            entities = _story_map_entities(selected_story)
            selected_map_entity_id = f"entity_{selected_gis_story_id}_focus"
            geo_intent["geo"]["map_context"] = _story_map_context(selected_story, progress, _gis_story_govmap_execution_by_id(selected_gis_story_id))
            geo_intent["geo"]["focus"] = {"focus_type": "municipality_or_story", "confidence_label": "בינונית", "place_query": _story_location_label(selected_story), "matched_text": _story_location_label(selected_story)}
            geo_intent["geo"]["resident_layer_keys"] = [row["layer_key"] for row in geo_intent["geo"]["map_context"].get("layers", [])]
            geo_intent["geo"]["govmap_layer_aliases"] = [alias for row in geo_intent["geo"]["map_context"].get("layers", []) for alias in row.get("govmap_aliases", []) if alias]
            geo_intent["geo"]["focus_layer"] = (geo_intent["geo"]["resident_layer_keys"] or [""])[0]
    topics = [
        _topic_node(topic_id=f"topic_root_{scenario['category']}", label=CATEGORY_LABELS.get(scenario["category"], "שירותים עירוניים"), primary_category_id=scenario["category"], mention_count=120, decision_count=18, child_ids=[f"topic_{scenario['key']}"], depth=0),
        _topic_node(topic_id=f"topic_{scenario['key']}", label=scenario["topic"], primary_category_id=scenario["category"], mention_count=24, decision_count=5, parent_id=f"topic_root_{scenario['category']}", depth=1),
    ]
    return {
        "ui_copy": {
            "document_title": "לוח מחוונים עירוני",
            "municipality_brand": "עירייה",
            "header": {"search_aria_label": "שאלת חיפוש", "search_submit_label": "חיפוש", "popular_searches_button": "חיפושים פופולריים", "filters_button": "מסננים", "admin_button": "מנהל", "admin_aria_label": "מנהל"},
            "answer_drawer": {"close_label": "סגירת תשובה", "title": "תשובה", "question_prefix": "שאלה:", "brief_title": "תקציר", "confidence_label": "ביטחון התשובה:", "decisions_title": "החלטות עיקריות", "related_topics_title": "נושאים קשורים", "limitations_title": "מגבלות", "source_link_label": "מקור"},
            "map": {"title": f"מפת סיפור GIS: {selected_story_card['title_he'] if selected_story_card else scenario['topic']}", "description": (f"מוקד: {_story_location_label(selected_story)}. סטטוס סיפור: {progress.get('label_he', '')}. המפה סכמטית והשכבות הן הקשר GIS." if selected_story else f"כתובת: {_address_with_city(scenario)}. סטטוס נבחר: {progress.get('label_he', '')}"), "provenance_label": "מפה סכמטית בלבד", "provenance_description": "שכבות GovMap נטענות כהקשר עדכני וקבוע לנושא; ציר הזמן הוא דוגמת פרוטוקולים ולא היסטוריית GIS רשמית.", "sea_label": "חוף הים", "legend_title": "מקרא", "control_labels": ["מרכז מפה", "התקרבות", "התרחקות", "שכבות מפה"], "area_labels": ["צפון העיר", "מרכז העיר", "מערב העיר", "מזרח העיר", "דרום העיר"], "marker_labels": ["תחנת תחבורה ציבורית", "פארק", "מבנה ציבור"]},
            "timeline": {"title": "ציר זמן", "previous_label": "אירוע קודם", "next_label": "אירוע הבא"},
            "start_discovery_panel": {"categories_title": "קטגוריות", "hot_topics_title": "נושאים בולטים", "topic_tree_title": "עץ נושאים", "show_more_label": "הצג עוד", "show_full_tree_label": "הצג כל העץ"},
            "filter_modal": {"title": "סינון תוצאות", "close_label": "סגירת מסננים", "sections": ["אזור", "טווח זמן", "קטגוריה", "סוגי מקורות", "ודאות"], "reset_label": "איפוס", "apply_label": "החל סינון", "options": {"area": ["כל העיר", "מרכז תל אביב"], "time_range": ["2024", "כל השנים"], "category": ["תכנון ובנייה", "תחבורה", "חינוך", "רווחה", "סביבה"], "source_types": ["פרוטוקולים ונספחים", "פרוטוקולים"], "confidence": ["גבוהה ובינונית", "כל הרמות"]}},
            "popular_searches": {"title": "חיפושים פופולריים", "choices": POPULAR_QUESTIONS, "story_title": "סיפורי פרוטוקולים", "story_choices": _popular_story_questions()},
        },
        "state": {
            "municipality_id": TEL_AVIV_MUNICIPALITY_ID,
            "current_question": current_question,
            "search_intent": "protocol_gis_story_lookup" if selected_story else scenario["intent"],
            "intent_resolution": geo_intent,
            "selected_time_range": {"start": TIMELINE_TEMPLATES[0][0], "end": TIMELINE_TEMPLATES[-1][0]},
            "selected_category_id": scenario["category"],
            "selected_topic_node_id": selected_topic_node_id,
            "selected_gis_story_id": selected_gis_story_id,
            "selected_map_entity_id": selected_map_entity_id,
            "selected_timeline_event_id": timeline_selected_event_id,
            "current_answer_id": f"answer_{scenario['key']}",
            "active_detail_drawer_mode": "answer",
            "confidence_filter": "medium_and_high",
            "source_type_filter": ["protocol", "attachment"],
            "filter_modal_open": False,
            "active_filter_count": 0,
            "popular_searches_open": False,
            "cached_answer_id": f"cached_answer_{scenario['key']}",
            "map_mode": "schematic",
            "generation_status": "mock_answer_ready",
            "login_state": "anonymous",
        },
        "start_discovery_panel": _discovery_panel(top_scenario, selected_topic_node_id),
        "gis_story_review": _gis_story_review_panel(selected_gis_story_id),
        "main_civic_workspace": {
            "map": {
                "spatial_representation": "schematic",
                "label": "מפה סכמטית",
                "real_geometry": None,
                "geometry_provenance": None,
                "provenance": {"status": "schematic_only", "label_he": "מפה סכמטית בלבד", "description_he": "אין גיאומטריית GIS מאומתת בדוגמת ההדגמה; GovMap הרשמי נטען כהקשר נפרד לפי שכבות הסיפור.", "source_evidence_refs": []},
                "entities": entities,
                "legend": [{"id": "selected_area", "label": "אזור נבחר"}, {"id": "neighborhood_boundary", "label": "שכונה / גבול עירוני"}, {"id": "story_focus", "label": "מוקד ציר זמן"}, {"id": "protocol_context", "label": "הקשר פרוטוקול"}],
                "real_gis_available": False,
            },
            "timeline": {"selected_event_id": timeline_selected_event_id, "events": timeline_events},
            "map_context": geo_intent["geo"]["map_context"],
        },
        "end_detail_drawer": {
            "mode": "answer",
            "answer_id": f"answer_{scenario['key']}",
            "title": "תשובה",
            "question": current_question if selected_story_card else scenario["question"],
            "brief": f"{selected_story_card['title_he']}: הסיפור שנבחר מתוך פרוטוקולי V3 מוצג כציר זמן ביקורתי עם רמזור התקדמות." if selected_story_card else f"{_display_brief(scenario)} ציר הזמן מציג חמישה שלבי פרוטוקול; המפה נשארת קבועה לנושא והאירועים משנים רק את סטטוס ההתקדמות.",
            "confidence_label": "בינונית",
            "decisions": decisions,
            "related_topics": related,
            "limitations": ["דוגמת הדגמה: שכבות GIS הן הקשר עדכני, לא הוכחה היסטורית למצב ביום הפרוטוקול."],
        },
        "contracts": {"topic_nodes": topics, "decisions": decisions, "evidence": evidence, "map_entities": entities, "related_topics": related},
        "evidence": evidence,
    }


def get_mock_rag_dashboard_evidence(evidence_id: str) -> dict[str, Any] | None:
    for scenario in SCENARIOS:
        for row in _evidence_rows(scenario):
            if row["id"] == evidence_id:
                return deepcopy(row)
    return None


def get_mock_rag_dashboard_payload() -> dict[str, Any]:
    return deepcopy(build_mock_rag_dashboard_payload())


def apply_mock_rag_dashboard_interaction(*, state: dict[str, Any] | None, interaction: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(interaction, dict):
        return None
    interaction_type = str(interaction.get("type") or "").strip()
    interaction_id = str(interaction.get("id") or "").strip()
    if not interaction_type:
        return None

    current_question = str((state or {}).get("current_question") or CURRENT_QUESTION)
    selected_gis_story_id = str((state or {}).get("selected_gis_story_id") or "")
    top_scenario = _scenario_for_question(current_question)
    selected_topic_node_id = str((state or {}).get("selected_topic_node_id") or f"topic_{top_scenario['key']}")
    scenario = _scenario_for_topic_selection(top_scenario, selected_topic_node_id)
    selected_event_id = str((state or {}).get("selected_timeline_event_id") or _default_event_id(scenario))
    if selected_event_id not in {_event_id(str(scenario["key"]), date_value) for date_value, _, _ in TIMELINE_TEMPLATES}:
        selected_event_id = _default_event_id(scenario)

    if interaction_type == "select_popular_search":
        selected_story = _gis_story_by_query(interaction_id)
        if selected_story is not None:
            current_question = interaction_id
            selected_gis_story_id = str(selected_story.get("story_id") or "")
            selected_event_id = ""
        elif interaction_id not in SCENARIO_BY_QUESTION:
            return None
        else:
            selected_gis_story_id = ""
            top_scenario = SCENARIO_BY_QUESTION[interaction_id]
            current_question = top_scenario["question"]
            scenario = top_scenario
            selected_topic_node_id = f"topic_{scenario['key']}"
            selected_event_id = _default_event_id(scenario)
    elif interaction_type == "select_gis_story":
        selected_story = _gis_story_by_id(interaction_id)
        if selected_story is None:
            return None
        selected_gis_story_id = str(selected_story.get("story_id") or "")
        current_question = _gis_story_search_row(selected_story)["query"]
        selected_event_id = ""
    elif interaction_type == "select_category":
        selected_gis_story_id = ""
        selected_key = _first_scenario_key_for_category(interaction_id)
        if not selected_key:
            return None
        selected_topic_node_id = f"topic_{selected_key}"
        scenario = SCENARIO_BY_KEY[selected_key]
        selected_event_id = _default_event_id(scenario)
    elif interaction_type in {"select_hot_topic", "select_topic_tree_node"}:
        selected_gis_story_id = ""
        validation_payload = build_mock_rag_dashboard_payload(question=top_scenario["question"], selected_event_id=selected_event_id, selected_topic_node_id=selected_topic_node_id)
        rows = validation_payload["start_discovery_panel"]["hot_topics"] if interaction_type == "select_hot_topic" else validation_payload["start_discovery_panel"]["focused_topic_tree_context"]["children"]
        enabled_ids = {row["id"] for row in rows if not row.get("disabled")}
        if interaction_id not in enabled_ids:
            return None
        selected_topic_node_id = interaction_id
        scenario = _scenario_for_topic_selection(top_scenario, selected_topic_node_id)
        selected_event_id = _default_event_id(scenario)
    elif interaction_type == "select_timeline_event":
        if selected_gis_story_id:
            selected_story = _gis_story_by_id(selected_gis_story_id)
            valid_event_ids = {str(row.get("id") or "") for row in (selected_story or {}).get("timeline_events", []) if isinstance(row, dict)}
        else:
            valid_event_ids = {_event_id(str(scenario["key"]), date_value) for date_value, _, _ in TIMELINE_TEMPLATES}
        if interaction_id not in valid_event_ids:
            return None
        selected_event_id = interaction_id
    elif interaction_type == "apply_filters":
        selected_gis_story_id = ""
        filters = interaction.get("filters") if isinstance(interaction.get("filters"), dict) else {}
        selected_key = _first_scenario_key_for_category(_category_id_from_filter(filters.get("category")))
        if selected_key:
            selected_topic_node_id = f"topic_{selected_key}"
            scenario = SCENARIO_BY_KEY[selected_key]
            selected_event_id = _default_event_id(scenario)

    payload = build_mock_rag_dashboard_payload(question=current_question, selected_event_id=selected_event_id, selected_topic_node_id=selected_topic_node_id, selected_gis_story_id=selected_gis_story_id)
    next_state = dict(payload["state"])
    if isinstance(state, dict):
        for key in ("confidence_filter", "source_type_filter", "filter_modal_open", "active_filter_count", "active_filter_summary", "popular_searches_open", "login_state"):
            if key in state:
                next_state[key] = state[key]

    category_ids = {row["id"] for row in payload["start_discovery_panel"]["categories"] if not row.get("disabled")}
    hot_topic_ids = {row["id"] for row in payload["start_discovery_panel"]["hot_topics"] if not row.get("disabled")}
    tree_ids = {row["id"] for row in payload["start_discovery_panel"]["focused_topic_tree_context"]["children"] if not row.get("disabled")}
    entity_ids = {row["id"] for row in payload["main_civic_workspace"]["map"]["entities"]}
    evidence_by_id = {row["id"]: row for row in payload["evidence"]}
    related_by_id = {row["id"]: row for row in payload["end_detail_drawer"]["related_topics"]}

    if interaction_type == "select_category":
        if interaction_id not in category_ids:
            return None
        next_state["active_detail_drawer_mode"] = "category"
    elif interaction_type == "select_hot_topic":
        if interaction_id not in hot_topic_ids:
            return None
        next_state["selected_topic_node_id"] = interaction_id
        next_state["active_detail_drawer_mode"] = "topic"
    elif interaction_type == "select_topic_tree_node":
        if interaction_id not in tree_ids:
            return None
        next_state["selected_topic_node_id"] = interaction_id
        payload["start_discovery_panel"]["focused_topic_tree_context"]["selected_topic_id"] = interaction_id
        next_state["active_detail_drawer_mode"] = "topicTree"
    elif interaction_type == "select_timeline_event":
        next_state["selected_timeline_event_id"] = selected_event_id
        next_state["active_detail_drawer_mode"] = "timelineEvent"
    elif interaction_type == "select_gis_story":
        next_state["selected_gis_story_id"] = selected_gis_story_id
        next_state["current_question"] = current_question
        next_state["active_detail_drawer_mode"] = "answer"
    elif interaction_type == "select_map_entity":
        if interaction_id not in entity_ids:
            return None
        next_state["selected_map_entity_id"] = interaction_id
        next_state["active_detail_drawer_mode"] = "mapEntity"
    elif interaction_type == "select_related_topic":
        if interaction_id not in related_by_id:
            return None
        next_state["selected_topic_node_id"] = related_by_id[interaction_id]["target_topic_id"]
        next_state["active_detail_drawer_mode"] = "topic"
    elif interaction_type == "open_evidence":
        if interaction_id not in evidence_by_id:
            return None
        next_state["active_detail_drawer_mode"] = "evidencePreview"
        next_state["selected_evidence_id"] = interaction_id
        payload["evidence_preview"] = evidence_by_id[interaction_id]
    elif interaction_type == "apply_filters":
        filters = interaction.get("filters") if isinstance(interaction.get("filters"), dict) else {}
        next_state["filter_modal_open"] = False
        next_state["active_filter_count"] = sum(1 for value in filters.values() if value not in (None, "", [], {}))
        next_state["active_filter_summary"] = filters
    elif interaction_type == "reset_filters":
        next_state["filter_modal_open"] = False
        next_state["active_filter_count"] = 0
        next_state["active_filter_summary"] = {}
    elif interaction_type == "select_popular_search":
        next_state["current_question"] = current_question
        next_state["selected_gis_story_id"] = selected_gis_story_id or None
        next_state["cached_answer_id"] = f"cached_answer_{scenario['key']}"
        next_state["active_detail_drawer_mode"] = "answer"
    else:
        return None

    payload["state"] = next_state
    _apply_selection_flags(payload)
    payload["interaction_result"] = {"type": interaction_type, "id": interaction_id, "status": "applied"}
    return payload


def _apply_selection_flags(payload: dict[str, Any]) -> None:
    state = payload["state"]
    for row in payload["start_discovery_panel"]["categories"]:
        row["selected"] = row["id"] == state.get("selected_category_id")
    for row in payload["start_discovery_panel"]["hot_topics"]:
        row["selected"] = row["id"] == state.get("selected_topic_node_id")
    for row in payload["start_discovery_panel"]["focused_topic_tree_context"]["children"]:
        row["selected"] = row["id"] == state.get("selected_topic_node_id")
    for row in payload["main_civic_workspace"]["timeline"]["events"]:
        row["selected"] = row["id"] == state.get("selected_timeline_event_id")
    payload["main_civic_workspace"]["timeline"]["selected_event_id"] = state.get("selected_timeline_event_id")
    for row in payload["main_civic_workspace"]["map"]["entities"]:
        row["selected"] = row["id"] == state.get("selected_map_entity_id")


def _category_id_from_filter(value: Any) -> str | None:
    compact = " ".join(str(value or "").split())
    labels = {
        "תכנון ובנייה": "planning",
        "תחבורה": "transport",
        "חינוך": "education",
        "רווחה": "welfare",
        "סביבה": "environment",
        "חירום": "emergency",
        "תרבות וספורט": "culture",
        "מסחר ותעסוקה": "commerce",
    }
    return labels.get(compact, compact if compact in set(labels.values()) else None)
