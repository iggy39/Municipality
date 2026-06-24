from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from municipality.gis_normalization import extract_cadastral_id, normalize_hebrew_text
from municipality.resident_gis_registry import ResidentGisLayerGroup, ResidentGisRegistry, load_resident_gis_registry

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_GUSH_ONLY_RE = re.compile(r"גוש\s*(?P<gush>\d+)")
_PARCEL_ONLY_RE = re.compile(r"חלקה\s*(?P<parcel>\d+)")
_PLAN_NUMBER_RE = re.compile(r"(?<!\d)(\d{3,}-\d{3,})(?!\d)")
_NEIGHBORHOOD_RE = re.compile(r"(?<![\u0590-\u05FF])(?:ברובע|רובע|בשכונת|שכונת|בשכונה|שכונה)\s+([^\n.,;:()]{1,40})")
_QUOTED_PLACE_RE = re.compile(r"[\"׳'`״](?P<value>[^\"׳'`״]{2,80})[\"׳'`״]")
_PLACE_TYPE_RE = re.compile(
    r"(?<![\u0590-\u05FF])(?P<value>(?:בית\s+ספר|בית\s+כנסת|בית|כיכר|מקווה|פארק|גן|גינה|מרכז|מבנה|מוסד|מתקן|תחנה|חוף|רחוב|שדרה|דרך|מגרש)\s+[^\n.,;:()]{2,45})"
)
_CONCRETE_PLACE_TYPES = (
    "בית ספר",
    "בית כנסת",
    "בית",
    "כיכר",
    "מקווה",
    "פארק",
    "גן",
    "גינה",
    "מרכז",
    "מבנה",
    "מוסד",
    "מתקן",
    "תחנה",
    "חוף",
    "רחוב",
    "שדרה",
    "דרך",
    "מגרש",
)
_ABSTRACT_PLACE_SUFFIXES = ("ציבור", "עיר", "כללי", "שונים", "רבים", "מספר", "קיים", "חדשים")

_FACILITY_LAYER_HINTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("בית ספר", "גן ילדים", "גני ילדים"), ("education_facilities", "public_buildings_assets", "public_transport_access", "playgrounds_youth_space")),
    (("בית כנסת", "מקווה", "דת"), ("religious_services", "public_buildings_assets", "planning_land_use")),
    (("כיכר", "רחוב", "דרך", "שדרה"), ("roads_parking_public_works", "public_transport_access", "neighborhoods_and_statistics")),
    (("פארק", "גינה", "גן", "מגרש"), ("playgrounds_youth_space", "environment_sanitation", "emergency_security_services")),
    (("בית", "מבנה", "מוסד", "מרכז", "אולם", "מתקן"), ("public_buildings_assets", "culture_sport_leisure", "planning_land_use")),
)
_CITYWIDE_LAYER_HINTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("הצפה", "הצפות", "ניקוז", "ביוב", "קולטנים", "תשתיות מים"), ("drainage_water_sewer", "roads_parking_public_works", "neighborhoods_and_statistics", "environment_sanitation")),
    (("סיכון", "סיכונים", "מפגע", "מפגעים", "בטיחות", "חירום", "מבנים מסוכנים", "אלימות", "זיהום"), ("emergency_security_services", "environment_sanitation", "neighborhoods_and_statistics")),
    (("אנטנה", "אנטנות", "קרינה", "מתקני שידור", "זיהום", "איכות אוויר"), ("cellular_and_radiation_context", "environment_sanitation", "education_facilities", "neighborhoods_and_statistics")),
    (("כלבים", "גללי", "צואה", "ניקיון", "נקיון", "פסולת", "אשפה"), ("environment_sanitation", "playgrounds_youth_space", "neighborhoods_and_statistics")),
    (("נוער", "צעירים", "לילה", "בלילות"), ("playgrounds_youth_space", "emergency_security_services", "roads_parking_public_works")),
    (("תחבורה", "אוטובוס", "רכבת", "כיכר", "כביש", "דרך"), ("public_transport_access", "roads_parking_public_works", "neighborhoods_and_statistics")),
)
_DEFAULT_RESIDENT_ARCHETYPES_PATH = Path(__file__).resolve().parents[2] / "config" / "ui" / "rag_dashboard_mock_scenarios.json"
_GOVMAP_BASE_URL = "https://www.govmap.gov.il"
_GOVMAP_SEARCH_PATH = "/api/search-service/api-search"
_GOVMAP_SPATIAL_PATH = "/api/spatial-analysis/layer-features-by-location"
_GOVMAP_SEARCH_DATATYPES = ("address", "street", "settlement")
_ARCHETYPE_STOPWORDS = {
    "האם",
    "אילו",
    "איזה",
    "איזו",
    "מה",
    "ומי",
    "איפה",
    "מתי",
    "איך",
    "שלי",
    "ליד",
    "סביב",
    "באזור",
    "באיזור",
    "הבית",
    "העירייה",
    "העיריה",
    "עירייה",
    "עיריה",
    "העיר",
    "בעיר",
    "המועצה",
    "הפרוטוקולים",
    "והאם",
    "ומה",
    "שיכול",
    "שלהם",
    "שלהן",
    "קיימים",
    "קיים",
    "החליטה",
    "החלטות",
    "הוחלט",
    "התקבלו",
    "לאחרונה",
    "תל",
    "אביב",
    "שדרות",
    "רחוב",
    "ברחוב",
    "שינויי",
    "נוגע",
    "נוגעות",
    "ידוע",
}
_MIN_V3_EVENT_LAYER_LINKS = 4
_MUNICIPALITY_LABELS_HE = {
    "ashdod": "אשדוד",
    "tel_aviv": "תל אביב-יפו",
    "beer_sheva": "באר שבע",
    "jerusalem": "ירושלים",
    "mock_unknown_municipality": "רשות לא מזוהה (מוקאפ)",
}
_MOCK_MUNICIPAL_CIVIC_ANCHORS = {
    # Coordinates are approximate mock anchors for artifact-only research output.
    # They are never presented as official geocoding and always carry mock provenance.
    "ashdod": {"label_he": "בניין העירייה / מוקד אזרחי אשדוד", "lon": 34.6553, "lat": 31.8044},
    "tel_aviv": {"label_he": "בניין העירייה / כיכר רבין תל אביב-יפו", "lon": 34.7818, "lat": 32.0812},
}
_KNOWN_MOCK_PLACE_COORDS: dict[tuple[str, str], dict[str, Any]] = {
    ("tel_aviv", "כיכר המדינה"): {"lon": 34.789, "lat": 32.086, "precision": "neighborhood_landmark_mock"},
    ("tel_aviv", "נווה שאנן"): {"lon": 34.779, "lat": 32.059, "precision": "neighborhood_mock"},
    ("ashdod", "המשכן לאומנויות הבמה"): {"lon": 34.646, "lat": 31.797, "precision": "facility_mock"},
}
_INFERRED_PLACE_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (r"המשכן לאומנויות הבמה", "המשכן לאומנויות הבמה", "named_civic_facility"),
    (r"בלו אייס(?:\s+ארנה)?", "בלו אייס ארנה", "named_leisure_facility"),
    (r"כיכר המדינה", "כיכר המדינה", "named_place"),
    (r"נווה שאנן", "נווה שאנן", "neighborhood_or_area"),
    (r"שכונות\s+התקווה,\s*עזרא\s+והארגזים", "שכונות התקווה, עזרא והארגזים", "neighborhood_or_area"),
    (r"דרום העיר", "דרום העיר", "city_region"),
    (r"מזרח העיר", "מזרח העיר", "city_region"),
    (r"צפון(?:\s+העיר)?", "צפון העיר", "city_region"),
    (r"מרכז(?:\s+העיר)?", "מרכז העיר", "city_region"),
)
_SUBJECT_LAYER_HINTS: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (("מעלית", "נגישות", "המשכן", "אומנויות", "תרבות"), ("public_buildings_assets", "culture_sport_leisure", "public_transport_access", "emergency_security_services"), "מוסד ציבורי/תרבותי ונגישות"),
    (("קשישים", "היפוטרמיה", "רווחה", "בדידות", "שירותים חברתיים"), ("welfare_health_services", "demographic_equity", "neighborhoods_and_statistics", "public_transport_access"), "אוכלוסייה רגישה ושירותי רווחה"),
    (("גני ילדים", "גן ילדים", "בתי ספר", "מוסדות חינוך", "חינוך", "גיל הרך", "משפחתונים", "סייעות"), ("education_facilities", "demographic_equity", "neighborhoods_and_statistics", "public_transport_access", "playgrounds_youth_space"), "חינוך ושירותי ילדים"),
    (("מקווה", "דת", "דתי", "מועצה דתית"), ("religious_services", "parcels_cadaster", "planning_land_use", "neighborhoods_and_statistics"), "שירותי דת וקרקע ציבורית"),
    (("קניון", "מסחרי", "עסקים", "תעסוקה", "מפעלים", "כלכלה"), ("commerce_employment", "public_buildings_assets", "parcels_cadaster", "public_transport_access"), "מסחר/תעסוקה ומבנים"),
    (("פארק", "גינה", "נוער", "ספורט", "החלקה", "קרח", "פנאי"), ("culture_sport_leisure", "playgrounds_youth_space", "public_buildings_assets", "public_transport_access"), "ספורט, פנאי ומרחב ציבורי"),
    (("כבישים", "מדרכות", "קרצוף", "תחבורה", "תחנות", "חניה", "תאורה", "רחובות"), ("roads_parking_public_works", "public_transport_access", "neighborhoods_and_statistics", "environment_sanitation"), "תנועה, דרך ותשתית רחוב"),
    (("תכנית", "תב\"ר", "תבר", "מתחם", "בנייה", "פיתוח", "שכונה"), ("planning_land_use", "parcels_cadaster", "neighborhoods_and_statistics", "public_buildings_assets"), "תכנון, תקציב פיתוח ומקרקעין"),
    (("תקציב", "קיצוץ", "הגדלה", "תמיכה", "שוויון", "פער"), ("demographic_equity", "neighborhoods_and_statistics", "public_buildings_assets", "municipal_boundaries"), "תקציב ושוויון שירותים"),
    (("סגן ראש עיר", "ראש עיר", "מועצה", "בחירת", "כהונתו", "מינוי"), ("municipal_boundaries", "public_buildings_assets", "neighborhoods_and_statistics", "resident_location"), "אירוע ממשל עירוני בעל השפעה כלל-עירונית"),
)
_V3_FALLBACK_LAYER_KEYS = ("municipal_boundaries", "neighborhoods_and_statistics", "public_buildings_assets", "resident_location")


@dataclass(frozen=True)
class ProtocolGisCandidate:
    artifact_id: str
    municipality_slug: str
    source_text: str
    extracted_signal: str
    linking_mode: str
    signal_type: str
    layer_key: str
    layer_display_name_he: str
    govmap_aliases: tuple[str, ...]
    govmap_layer_aliases: tuple[str, ...]
    confidence_label: str
    candidate_query: dict[str, Any]
    reason_he: str
    uncertainty_he: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "municipality_slug": self.municipality_slug,
            "source_text": self.source_text,
            "extracted_signal": self.extracted_signal,
            "linking_mode": self.linking_mode,
            "signal_type": self.signal_type,
            "layer_key": self.layer_key,
            "layer_display_name_he": self.layer_display_name_he,
            "govmap_aliases": list(self.govmap_aliases),
            "govmap_layer_aliases": list(self.govmap_layer_aliases),
            "real_gis_query": _real_gis_query_payload(self),
            "confidence_label": self.confidence_label,
            "candidate_query": self.candidate_query,
            "reason_he": self.reason_he,
            "uncertainty_he": self.uncertainty_he,
        }


@dataclass(frozen=True)
class ProtocolV3GisArchetypeMatch:
    key: str
    question: str
    topic: str
    intent: str
    matched_layer_keys: tuple[str, ...]
    matched_terms: tuple[str, ...]
    confidence_label: str
    reason_he: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "question": self.question,
            "topic": self.topic,
            "intent": self.intent,
            "matched_layer_keys": list(self.matched_layer_keys),
            "matched_terms": list(self.matched_terms),
            "confidence_label": self.confidence_label,
            "reason_he": self.reason_he,
        }


@dataclass(frozen=True)
class ProtocolV3LocationHint:
    label_he: str
    scope: str
    municipality_slug: str
    query: dict[str, Any]
    confidence_label: str
    provenance: str
    is_mock: bool
    reason_he: str
    caveat_he: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "label_he": self.label_he,
            "scope": self.scope,
            "municipality_slug": self.municipality_slug,
            "query": self.query,
            "confidence_label": self.confidence_label,
            "provenance": self.provenance,
            "is_mock": self.is_mock,
            "reason_he": self.reason_he,
            "caveat_he": self.caveat_he,
        }


@dataclass(frozen=True)
class ProtocolV3GisLink:
    event_id: str
    artifact_id: str
    municipality_slug: str
    profile: str
    source_row: str
    validation_status: str
    event_status: str
    action_type_he: str
    matter_he: str
    outcome_he: str
    source_provenance: dict[str, Any]
    primary_time: dict[str, Any] | None
    time_mentions: tuple[dict[str, Any], ...]
    raw_date_mentions: tuple[dict[str, Any], ...]
    evidence_quotes: tuple[str, ...]
    source_text: str
    location_hints: tuple[ProtocolV3LocationHint, ...]
    candidates: tuple[ProtocolGisCandidate, ...]
    supported_archetypes: tuple[ProtocolV3GisArchetypeMatch, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "artifact_id": self.artifact_id,
            "municipality_slug": self.municipality_slug,
            "profile": self.profile,
            "source_row": self.source_row,
            "validation_status": self.validation_status,
            "event_status": self.event_status,
            "action_type_he": self.action_type_he,
            "matter_he": self.matter_he,
            "outcome_he": self.outcome_he,
            "source_provenance": self.source_provenance,
            "primary_time": self.primary_time,
            "time_mentions": list(self.time_mentions),
            "raw_date_mentions": list(self.raw_date_mentions),
            "evidence_quotes": list(self.evidence_quotes),
            "source_text": self.source_text,
            "location_hints": [hint.to_payload() for hint in self.location_hints],
            "candidates": [candidate.to_payload() for candidate in self.candidates],
            "supported_archetypes": [match.to_payload() for match in self.supported_archetypes],
        }


def link_protocol_row_to_gis(row: Mapping[str, Any], *, registry: ResidentGisRegistry | None = None) -> list[ProtocolGisCandidate]:
    registry = registry or load_resident_gis_registry()
    source_text = _row_source_text(row)
    if not source_text:
        return []
    artifact_id = str(row.get("artifact_id") or "")
    municipality_slug = str(row.get("municipality_slug") or "")
    candidates: list[ProtocolGisCandidate] = []
    for signal in _extract_signals(row, source_text):
        for layer in _rank_layers_for_signal(signal, registry):
            candidates.append(_candidate_from_signal(signal, layer, artifact_id=artifact_id, municipality_slug=municipality_slug, source_text=source_text))
    return _dedupe_candidates(candidates)


def protocol_gis_link_report(session: "Session", *, artifact_ids: Sequence[str] | None = None, limit: int = 50) -> list[ProtocolGisCandidate]:
    registry = load_resident_gis_registry()
    rows = _topic_subject_artifact_rows(session, artifact_ids=artifact_ids, limit=limit)
    candidates: list[ProtocolGisCandidate] = []
    for row in rows:
        candidates.extend(link_protocol_row_to_gis(row, registry=registry))
    return _dedupe_candidates(candidates)


def protocol_gis_link_report_markdown(candidates: Sequence[ProtocolGisCandidate]) -> str:
    lines = [
        "| Artifact ID | Source Text | Signal | Linking Mode | GIS Group | Confidence | Reason / Uncertainty |",
        "|---|---|---|---|---|---|---|",
    ]
    for candidate in candidates:
        reason = " ".join(part for part in (candidate.reason_he, candidate.uncertainty_he) if part)
        lines.append(
            "| "
            + " | ".join(
                _md_cell(value)
                for value in (
                    candidate.artifact_id,
                    _shorten(candidate.source_text, 180),
                    candidate.extracted_signal,
                    candidate.linking_mode,
                    candidate.layer_display_name_he,
                    candidate.confidence_label,
                    reason,
                )
            )
            + " |"
        )
    return "\n".join(lines)


def load_resident_rag_archetypes(path: str | Path | None = None) -> list[dict[str, Any]]:
    fixture_path = Path(path) if path is not None else _DEFAULT_RESIDENT_ARCHETYPES_PATH
    if not fixture_path.exists():
        return []
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios") if isinstance(payload, dict) else None
    return [dict(row) for row in scenarios or [] if isinstance(row, Mapping)]


def link_topic_subject_v3_events_to_gis(
    payload: Sequence[Mapping[str, Any]],
    *,
    registry: ResidentGisRegistry | None = None,
    archetypes: Sequence[Mapping[str, Any]] | None = None,
) -> list[ProtocolV3GisLink]:
    registry = registry or load_resident_gis_registry()
    archetypes = list(archetypes) if archetypes is not None else load_resident_rag_archetypes()
    links: list[ProtocolV3GisLink] = []
    best_events: dict[tuple[str, str], tuple[int, Mapping[str, Any], Mapping[str, Any]]] = {}
    for wrapper, event in _iter_v3_events(payload):
        event_id = str(event.get("event_id") or "")
        artifact_id = str(wrapper.get("artifact_id") or event.get("artifact_id") or event.get("target_artifact_id") or "")
        source_row = str(wrapper.get("row") or "")
        dedupe_key = (source_row or event_id, artifact_id)
        rank = _v3_record_rank(wrapper=wrapper, event=event)
        if dedupe_key not in best_events or rank > best_events[dedupe_key][0]:
            best_events[dedupe_key] = (rank, wrapper, event)
    for _, wrapper, event in best_events.values():
        row = _v3_event_to_protocol_row(event=event, wrapper=wrapper)
        event_id = str(event.get("event_id") or "")
        artifact_id = str(row.get("artifact_id") or wrapper.get("artifact_id") or event.get("artifact_id") or "")
        source_text = _row_source_text(row)
        location_hints = _v3_location_hints(row=row, source_text=source_text)
        candidates = _enriched_v3_gis_candidates(row=row, source_text=source_text, location_hints=location_hints, registry=registry)
        matches = _direct_archetype_matches(source_text=source_text, candidates=candidates, archetypes=archetypes)
        links.append(
            ProtocolV3GisLink(
                event_id=event_id,
                artifact_id=artifact_id,
                municipality_slug=str(row.get("municipality_slug") or ""),
                profile=str(wrapper.get("profile") or ""),
                source_row=str(wrapper.get("row") or ""),
                validation_status=str(event.get("validation_status") or wrapper.get("validation_status") or ""),
                event_status=_v3_event_status(event),
                action_type_he=_v3_action_type(event),
                matter_he=_v3_matter(event),
                outcome_he=_v3_outcome(event),
                source_provenance=_v3_source_provenance(event=event, wrapper=wrapper),
                primary_time=_v3_primary_time(event=event, wrapper=wrapper),
                time_mentions=tuple(_v3_time_mentions(event=event, wrapper=wrapper)),
                raw_date_mentions=tuple(_v3_raw_date_mentions(event=event, wrapper=wrapper)),
                evidence_quotes=tuple(_v3_evidence_quotes(event)),
                source_text=_shorten(source_text, 600),
                location_hints=tuple(location_hints),
                candidates=tuple(candidates),
                supported_archetypes=tuple(matches),
            )
        )
    return links


def _v3_record_rank(*, wrapper: Mapping[str, Any], event: Mapping[str, Any]) -> int:
    prediction = wrapper.get("prediction") if isinstance(wrapper.get("prediction"), Mapping) else {}
    quality = wrapper.get("quality") if isinstance(wrapper.get("quality"), Mapping) else {}
    statuses = [
        str(event.get("validation_status") or ""),
        str(prediction.get("validation_status") or ""),
        str(quality.get("quality_status") or ""),
    ]
    rank = 0
    if "accepted" in statuses:
        rank += 100
    elif "needs_review" in statuses:
        rank += 50
    elif "failed" in statuses:
        rank -= 100
    if int(wrapper.get("problem_count") or 0) == 0:
        rank += 10
    if str(wrapper.get("profile") or ""):
        rank += 1
    return rank


def _v3_location_hints(*, row: Mapping[str, Any], source_text: str) -> list[ProtocolV3LocationHint]:
    municipality_slug = str(row.get("municipality_slug") or "")
    hints: list[ProtocolV3LocationHint] = []
    municipality_label = _municipality_label_he(municipality_slug)
    if municipality_slug:
        municipality_is_mock = municipality_slug == "mock_unknown_municipality"
        hints.append(
            ProtocolV3LocationHint(
                label_he=municipality_label,
                scope="municipality",
                municipality_slug=municipality_slug,
                query={"municipality_slug": municipality_slug, "scope": "whole_municipality", "is_mock": municipality_is_mock},
                confidence_label="low" if municipality_is_mock else "medium",
                provenance="mock_completion" if municipality_is_mock else "source_metadata",
                is_mock=municipality_is_mock,
                reason_he="לא נמצא מזהה רשות באירוע, ולכן נוסף מוקאפ רשות כדי לא להשאיר אירוע ללא הקשר מפה." if municipality_is_mock else "הרשות נלקחה ממזהה השורה או מנתיב המקור ולכן ניתן לקשר את האירוע לתחום השיפוט העירוני.",
                caveat_he="זה ערך מוקאפ בלבד; חובה לזהות רשות אמיתית לפני שימוש תושבי." if municipality_is_mock else "תחום שיפוט אינו מיקום נקודתי של האירוע ואינו מוכיח השפעה על רחוב מסוים.",
            )
        )

    for label, scope in _source_place_labels(source_text):
        query: dict[str, Any] = {"place_query": label, "scope": scope, "municipality_slug": municipality_slug, "is_mock": True, "geocoding_status": "mock_needs_verification"}
        known = _KNOWN_MOCK_PLACE_COORDS.get((municipality_slug, label))
        if known:
            query.update({"lon": known["lon"], "lat": known["lat"], "precision": known["precision"]})
        hints.append(
            ProtocolV3LocationHint(
                label_he=label,
                scope=scope,
                municipality_slug=municipality_slug,
                query=query,
                confidence_label="medium" if known else "low",
                provenance="source_text_mock_geocoding",
                is_mock=True,
                reason_he="שם מקום/אזור הופיע בטקסט המקור, אך לא בוצעה גיאוקוד רשמי בהרצה זו.",
                caveat_he="המיקום הוא ניחוש מחקרי מתוך טקסט; חובה לאמת מול גיאוקוד/GovMap לפני שימוש תושבי.",
            )
        )

    civic_anchor = _MOCK_MUNICIPAL_CIVIC_ANCHORS.get(municipality_slug)
    if civic_anchor:
        hints.append(
            ProtocolV3LocationHint(
                label_he=str(civic_anchor["label_he"]),
                scope="mock_civic_anchor",
                municipality_slug=municipality_slug,
                query={
                    "place_query": civic_anchor["label_he"],
                    "municipality_slug": municipality_slug,
                    "lon": civic_anchor["lon"],
                    "lat": civic_anchor["lat"],
                    "scope": "municipal_civic_anchor",
                    "is_mock": True,
                    "precision": "approximate_mock_civic_anchor",
                },
                confidence_label="low",
                provenance="mock_completion",
                is_mock=True,
                reason_he="כאשר אין נקודה מפורשת, אירוע מועצה/עירייה מקבל עוגן מחקרי במוקד העירוני כדי לאפשר חיבור מפה ראשוני.",
                caveat_he="זה אינו מיקום מוכח של האירוע או של ההשפעה; זה עוגן דמו בלבד להמשך אימות.",
            )
        )
    return _dedupe_location_hints(hints)


def _enriched_v3_gis_candidates(
    *,
    row: Mapping[str, Any],
    source_text: str,
    location_hints: Sequence[ProtocolV3LocationHint],
    registry: ResidentGisRegistry,
) -> list[ProtocolGisCandidate]:
    candidates = list(link_protocol_row_to_gis(row, registry=registry))
    artifact_id = str(row.get("artifact_id") or "")
    municipality_slug = str(row.get("municipality_slug") or "")
    for layer_key, reason in _subject_layer_keys(source_text):
        if layer_key in registry.layer_by_key:
            candidates.append(
                _candidate_for_layer_key(
                    registry=registry,
                    layer_key=layer_key,
                    artifact_id=artifact_id,
                    municipality_slug=municipality_slug,
                    source_text=source_text,
                    linking_mode="subject_event_layer_inference",
                    signal_type="municipal_action",
                    extracted_signal=reason,
                    confidence_label="medium" if any(not hint.is_mock and hint.scope == "municipality" for hint in location_hints) else "low",
                    candidate_query=_candidate_query_from_hints(location_hints, layer_key=layer_key, is_mock=True),
                    reason_he=f"שכבה זו קשורה לנושא האירוע: {reason}.",
                    uncertainty_he="הקישור נובע מנושא האירוע ולא מהתאמה רשמית ל-feature ספציפי; יש לאמת גיאומטריה ומקור לפני הצגה תושבית.",
                )
            )

    for layer_key in _V3_FALLBACK_LAYER_KEYS:
        if len({candidate.layer_key for candidate in candidates}) >= _MIN_V3_EVENT_LAYER_LINKS:
            break
        if layer_key in registry.layer_by_key:
            candidates.append(
                _candidate_for_layer_key(
                    registry=registry,
                    layer_key=layer_key,
                    artifact_id=artifact_id,
                    municipality_slug=municipality_slug,
                    source_text=source_text,
                    linking_mode="mock_municipality_context_completion",
                    signal_type="municipal_action",
                    extracted_signal="municipality_context",
                    confidence_label="low",
                    candidate_query=_candidate_query_from_hints(location_hints, layer_key=layer_key, is_mock=True),
                    reason_he="שכבת הקשר כלל-עירונית נוספה כדי שכל אירוע V3 יהיה ניתן להצבה ראשונית במפה.",
                    uncertainty_he="זה קישור דמו/מחקרי ברמת הקשר, לא הוכחה למיקום נקודתי או השפעה ישירה.",
                )
            )
    return _dedupe_candidates(candidates)


def _candidate_for_layer_key(
    *,
    registry: ResidentGisRegistry,
    layer_key: str,
    artifact_id: str,
    municipality_slug: str,
    source_text: str,
    linking_mode: str,
    signal_type: str,
    extracted_signal: str,
    confidence_label: str,
    candidate_query: dict[str, Any],
    reason_he: str,
    uncertainty_he: str,
) -> ProtocolGisCandidate:
    layer = registry.layer_by_key[layer_key]
    return ProtocolGisCandidate(
        artifact_id=artifact_id,
        municipality_slug=municipality_slug,
        source_text=_shorten(source_text, 320),
        extracted_signal=extracted_signal,
        linking_mode=linking_mode,
        signal_type=signal_type,
        layer_key=layer.layer_key,
        layer_display_name_he=layer.display_name_he,
        govmap_aliases=tuple(layer.govmap_aliases),
        govmap_layer_aliases=tuple(govmap_layer.alias for govmap_layer in layer.govmap_layers),
        confidence_label=confidence_label,
        candidate_query=candidate_query,
        reason_he=reason_he,
        uncertainty_he=uncertainty_he,
    )


def _candidate_query_from_hints(location_hints: Sequence[ProtocolV3LocationHint], *, layer_key: str, is_mock: bool) -> dict[str, Any]:
    primary = _primary_location_hint(location_hints)
    query = dict(primary.query) if primary else {}
    query.update({"layer_key": layer_key, "is_mock": bool(is_mock or (primary.is_mock if primary else False)), "provenance": primary.provenance if primary else "mock_completion"})
    return query


def _real_gis_query_payload(candidate: ProtocolGisCandidate) -> dict[str, Any]:
    query = dict(candidate.candidate_query or {})
    municipality_slug = candidate.municipality_slug
    municipality_label = _municipality_label_he(municipality_slug)
    map_layer_aliases = _govmap_map_layer_aliases(candidate)
    search_datatypes = _govmap_search_datatypes(candidate)
    location_text = _candidate_location_search_text(query=query, municipality_label=municipality_label)
    if (
        not location_text
        and map_layer_aliases
        and municipality_slug
        and municipality_slug != "mock_unknown_municipality"
        and not any(key in query for key in ("gush", "parcel", "plan_number"))
    ):
        location_text = municipality_label
    alternate_search_texts = _govmap_alternate_search_texts(query=query, municipality_slug=municipality_slug, primary_search_text=location_text)
    blocked_reasons: list[str] = []
    if municipality_slug == "mock_unknown_municipality" or not municipality_slug:
        blocked_reasons.append("missing_real_municipality")
    if query.get("is_mock") and not location_text and not any(key in query for key in ("gush", "parcel", "plan_number")):
        blocked_reasons.append("mock_location_without_search_text")
    if not map_layer_aliases and not search_datatypes:
        blocked_reasons.append("no_govmap_layer_or_search_alias")
    if location_text and map_layer_aliases:
        query_kind = "geocode_then_spatial_layer_query"
    elif any(key in query for key in ("gush", "parcel", "plan_number")) and map_layer_aliases:
        query_kind = "govmap_layer_attribute_lookup"
    elif location_text and search_datatypes:
        query_kind = "govmap_search_only"
    elif map_layer_aliases and municipality_slug and municipality_slug != "mock_unknown_municipality":
        query_kind = "municipality_context_layer_query"
    else:
        query_kind = "not_ready"

    executable = not blocked_reasons and query_kind != "not_ready"
    return {
        "provider": "govmap",
        "status": "ready" if executable else "blocked",
        "executable": executable,
        "query_kind": query_kind,
        "blocked_reasons": blocked_reasons,
        "municipality_slug": municipality_slug,
        "municipality_label_he": municipality_label,
        "search_text": location_text,
        "alternate_search_texts": list(alternate_search_texts),
        "search_datatypes": list(search_datatypes),
        "map_layer_aliases": list(map_layer_aliases),
        "endpoints": _govmap_query_endpoints(query_kind=query_kind, executable=executable),
        "payload_templates": _govmap_payload_templates(
            query=query,
            query_kind=query_kind,
            search_text=location_text,
            alternate_search_texts=alternate_search_texts,
            search_datatypes=search_datatypes,
            map_layer_aliases=map_layer_aliases,
        ),
        "execution_note_he": _real_gis_execution_note_he(executable=executable, query=query, candidate=candidate),
    }


def _govmap_map_layer_aliases(candidate: ProtocolGisCandidate) -> tuple[str, ...]:
    aliases = [*candidate.govmap_layer_aliases, *candidate.govmap_aliases]
    return tuple(alias for alias in _dedupe_text(aliases) if alias not in _GOVMAP_SEARCH_DATATYPES)


def _govmap_search_datatypes(candidate: ProtocolGisCandidate) -> tuple[str, ...]:
    aliases = [*candidate.govmap_layer_aliases, *candidate.govmap_aliases]
    datatypes = [alias for alias in aliases if alias in _GOVMAP_SEARCH_DATATYPES]
    if candidate.candidate_query.get("place_query") or candidate.candidate_query.get("area_query"):
        datatypes.extend(_GOVMAP_SEARCH_DATATYPES)
    return tuple(_dedupe_text(datatypes))


def _candidate_location_search_text(*, query: Mapping[str, Any], municipality_label: str) -> str:
    place = str(query.get("place_query") or query.get("area_query") or "").strip()
    if place:
        return " ".join(_dedupe_text((place, municipality_label)))
    if str(query.get("scope") or "") == "whole_municipality" and municipality_label and "מוקאפ" not in municipality_label:
        return municipality_label
    return ""


def _govmap_alternate_search_texts(*, query: Mapping[str, Any], municipality_slug: str, primary_search_text: str) -> tuple[str, ...]:
    if not municipality_slug or municipality_slug == "mock_unknown_municipality":
        return ()
    municipality_alias = municipality_slug.replace("_", " ")
    place = str(query.get("place_query") or query.get("area_query") or "").strip()
    alternates = [municipality_alias]
    if place:
        alternates.insert(0, f"{place} {municipality_alias}")
    return tuple(value for value in _dedupe_text(alternates) if value != primary_search_text)


def _govmap_query_endpoints(*, query_kind: str, executable: bool) -> list[dict[str, str]]:
    if not executable:
        return []
    endpoints: list[dict[str, str]] = []
    if query_kind in {"geocode_then_spatial_layer_query", "govmap_search_only", "municipality_context_layer_query"}:
        endpoints.append({"method": "POST", "url": f"{_GOVMAP_BASE_URL}{_GOVMAP_SEARCH_PATH}"})
    if query_kind in {"geocode_then_spatial_layer_query", "municipality_context_layer_query"}:
        endpoints.append({"method": "POST", "url": f"{_GOVMAP_BASE_URL}{_GOVMAP_SPATIAL_PATH}"})
    if query_kind == "govmap_layer_attribute_lookup":
        endpoints.append({"method": "POST", "url": f"{_GOVMAP_BASE_URL}{_GOVMAP_SPATIAL_PATH}", "requires": "feature center from cadaster/plan search or layer filter"})
    return endpoints


def _govmap_payload_templates(
    *,
    query: Mapping[str, Any],
    query_kind: str,
    search_text: str,
    alternate_search_texts: Sequence[str],
    search_datatypes: Sequence[str],
    map_layer_aliases: Sequence[str],
) -> dict[str, Any]:
    templates: dict[str, Any] = {}
    if search_text:
        templates["search"] = {
            "searchText": search_text,
            "language": "he",
            "maxResults": 5,
            "isAccurate": True,
            "layers": list(search_datatypes or _GOVMAP_SEARCH_DATATYPES),
            "apiKey": "${GOVMAP_API_KEY}",
        }
        if alternate_search_texts:
            templates["search_fallbacks"] = [
                {
                    "searchText": value,
                    "language": "he",
                    "maxResults": 5,
                    "isAccurate": True,
                    "layers": list(search_datatypes or _GOVMAP_SEARCH_DATATYPES),
                    "apiKey": "${GOVMAP_API_KEY}",
                }
                for value in alternate_search_texts
            ]
    if map_layer_aliases and query_kind in {"geocode_then_spatial_layer_query", "municipality_context_layer_query"}:
        templates["features_by_location_after_search"] = {
            "apiToken": "${GOVMAP_API_KEY}",
            "data": {
                "geometry": "POINT(${itm_x} ${itm_y})",
                "radius": 3000,
                "layers": [{"name": alias, "fields": ["objectid", "name", "shem", "SHEM", "address"]} for alias in map_layer_aliases],
            },
        }
    if map_layer_aliases and any(key in query for key in ("gush", "parcel", "plan_number")):
        templates["attribute_lookup"] = {
            "layers": list(map_layer_aliases),
            "filters": {key: query[key] for key in ("gush", "parcel", "plan_number") if key in query},
            "note": "Use GovMap/catalog attribute search or an ingested local canonical table, then fetch geometry by returned feature id.",
        }
    return templates


def _real_gis_execution_note_he(*, executable: bool, query: Mapping[str, Any], candidate: ProtocolGisCandidate) -> str:
    if not executable:
        return "לא ניתן להריץ מול GovMap עדיין: חסר מזהה רשות אמיתי, שם מקום, או alias שכבה רשמי."
    if query.get("is_mock"):
        return "אפשר להריץ חיפוש GovMap, אך מקור המיקום/השכבה עדיין מסומן כהסקה או מוקאפ ולכן התוצאה חייבת אימות ידני."
    if candidate.linking_mode == "explicit_cadaster_reference":
        return "יש בסיס להרצה מול שכבות קדסטר/תכנון רשמיות לפי גוש/חלקה/מספר תכנית."
    return "יש מספיק מידע להרצת חיפוש GovMap או גיאוקוד ולאחריו שאילתת שכבות סביב התוצאה."


def _primary_location_hint(location_hints: Sequence[ProtocolV3LocationHint]) -> ProtocolV3LocationHint | None:
    for scope in ("named_place", "named_civic_facility", "named_leisure_facility", "neighborhood_or_area", "city_region", "mock_civic_anchor", "municipality"):
        for hint in location_hints:
            if hint.scope == scope:
                return hint
    return location_hints[0] if location_hints else None


def _subject_layer_keys(source_text: str) -> list[tuple[str, str]]:
    normalized = normalize_hebrew_text(source_text)
    tokens = set(normalized.split())
    out: list[tuple[str, str]] = []
    for terms, layer_keys, reason in _SUBJECT_LAYER_HINTS:
        if any((term in normalized if " " in term else term in tokens) for term in terms):
            out.extend((layer_key, reason) for layer_key in layer_keys)
    return [(layer_key, reason) for layer_key, reason in _dedupe_layer_reasons(out)]


def _source_place_labels(source_text: str) -> list[tuple[str, str]]:
    labels: list[tuple[str, str]] = []
    for pattern, label, scope in _INFERRED_PLACE_PATTERNS:
        if re.search(pattern, source_text):
            labels.append((label, scope))
    for place in _places_from_text(source_text):
        cleaned = _clean_place(place)
        if cleaned and _looks_like_concrete_place(cleaned):
            labels.append((cleaned, "named_place"))
    return _dedupe_label_scopes(labels)[:5]


def _municipality_label_he(municipality_slug: str) -> str:
    return _MUNICIPALITY_LABELS_HE.get(municipality_slug, municipality_slug or "unknown_municipality")


def _municipality_slug_from_text(source_text: str) -> str:
    normalized = normalize_hebrew_text(source_text)
    if "אשדוד" in normalized:
        return "ashdod"
    if "תל אביב" in normalized or "תל-אביב" in normalized or "יפו" in normalized:
        return "tel_aviv"
    return ""


def _dedupe_location_hints(hints: Iterable[ProtocolV3LocationHint]) -> list[ProtocolV3LocationHint]:
    seen: set[tuple[str, str]] = set()
    out: list[ProtocolV3LocationHint] = []
    for hint in hints:
        key = (hint.scope, hint.label_he)
        if key in seen:
            continue
        seen.add(key)
        out.append(hint)
    return out


def _dedupe_layer_reasons(values: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for layer_key, reason in values:
        if layer_key in seen:
            continue
        seen.add(layer_key)
        out.append((layer_key, reason))
    return out


def _dedupe_label_scopes(values: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for label, scope in values:
        key = (label, scope)
        if key in seen:
            continue
        seen.add(key)
        out.append((label, scope))
    return out


def protocol_v3_gis_link_report_markdown(links: Sequence[ProtocolV3GisLink]) -> str:
    lines = [
        "| Event ID | Artifact ID | Matter | Event Status | GIS Groups | Direct Resident Archetypes | Evidence Quotes |",
        "|---|---|---|---|---|---|---|",
    ]
    for link in links:
        groups = ", ".join(_dedupe_text(candidate.layer_display_name_he for candidate in link.candidates)) or "none"
        archetypes = ", ".join(match.key for match in link.supported_archetypes) or "none"
        quotes = " / ".join(link.evidence_quotes[:2])
        lines.append(
            "| "
            + " | ".join(
                _md_cell(value)
                for value in (
                    link.event_id,
                    link.artifact_id,
                    _shorten(link.matter_he, 140),
                    link.event_status,
                    _shorten(groups, 180),
                    _shorten(archetypes, 180),
                    _shorten(quotes, 180),
                )
            )
            + " |"
        )
    return "\n".join(lines)


def _iter_v3_events(payload: Sequence[Mapping[str, Any]]) -> Iterable[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        nested = item.get("event")
        if isinstance(nested, Mapping):
            yield item, nested
        elif item.get("event_id") or item.get("artifact_id"):
            yield {}, item


def _v3_event_to_protocol_row(*, event: Mapping[str, Any], wrapper: Mapping[str, Any]) -> dict[str, Any]:
    event_payload = event.get("event_payload") if isinstance(event.get("event_payload"), Mapping) else {}
    normalized = event.get("normalized_event") if isinstance(event.get("normalized_event"), Mapping) else {}
    source_provenance = _v3_source_provenance(event=event, wrapper=wrapper)
    artifact_id = str(wrapper.get("artifact_id") or event.get("artifact_id") or event_payload.get("target_artifact_id") or normalized.get("target_artifact_id") or "")
    source_text = "\n".join(
        _dedupe_text(
            str(part or "")
            for part in (
                event_payload.get("matter_he"),
                normalized.get("matter_candidate_he"),
                event_payload.get("action_details_he"),
                normalized.get("municipal_action_description_he"),
                event_payload.get("action_quote_he"),
                normalized.get("supporting_quote_he"),
                event.get("anchor_source_text_he"),
                event.get("full_source_text_he"),
                wrapper.get("full_source_text"),
            )
        )
    )
    return {
        "artifact_id": artifact_id,
        "municipality_slug": _v3_municipality_slug(wrapper=wrapper, source_provenance=source_provenance) or _municipality_slug_from_text(source_text) or "mock_unknown_municipality",
        "subject_object_he": _v3_subject_object(event_payload=event_payload, normalized=normalized),
        "subject_details_he": _v3_matter(event),
        "subject_matter_he": _v3_matter(event),
        "action_details_he": str(event_payload.get("action_details_he") or normalized.get("municipal_action_description_he") or ""),
        "decision_source_quote_he": "\n".join(_v3_evidence_quotes(event)),
        "title_he": str(source_provenance.get("source_title") or ""),
        "body_text": source_text,
        "retrieval_text": source_text,
    }


def _v3_municipality_slug(*, wrapper: Mapping[str, Any], source_provenance: Mapping[str, Any]) -> str:
    wrapper_slug = str(wrapper.get("municipality_slug") or "")
    if wrapper_slug:
        return wrapper_slug
    row = str(wrapper.get("row") or "")
    if ":" in row:
        return row.split(":", 1)[0]
    source_url = str(source_provenance.get("source_url") or "")
    match = re.search(r"pdf_first_batch/([^/]+)/", source_url)
    return match.group(1) if match else ""


def _v3_subject_object(*, event_payload: Mapping[str, Any], normalized: Mapping[str, Any]) -> str:
    return str(
        event_payload.get("subject_summary_he")
        or event_payload.get("action_focus_quote_he")
        or normalized.get("action_focus_quote_he")
        or normalized.get("matter_candidate_he")
        or event_payload.get("matter_he")
        or ""
    )


def _v3_event_status(event: Mapping[str, Any]) -> str:
    event_payload = event.get("event_payload") if isinstance(event.get("event_payload"), Mapping) else {}
    normalized = event.get("normalized_event") if isinstance(event.get("normalized_event"), Mapping) else {}
    return str(normalized.get("event_status") or event_payload.get("event_phase") or event_payload.get("target_row_role") or "unknown")


def _v3_action_type(event: Mapping[str, Any]) -> str:
    event_payload = event.get("event_payload") if isinstance(event.get("event_payload"), Mapping) else {}
    normalized = event.get("normalized_event") if isinstance(event.get("normalized_event"), Mapping) else {}
    return str(event_payload.get("action_type_he") or normalized.get("procedural_carrier_he") or "")


def _v3_matter(event: Mapping[str, Any]) -> str:
    event_payload = event.get("event_payload") if isinstance(event.get("event_payload"), Mapping) else {}
    normalized = event.get("normalized_event") if isinstance(event.get("normalized_event"), Mapping) else {}
    return str(event_payload.get("matter_he") or normalized.get("matter_candidate_he") or normalized.get("normalized_event_summary_he") or "")


def _v3_outcome(event: Mapping[str, Any]) -> str:
    event_payload = event.get("event_payload") if isinstance(event.get("event_payload"), Mapping) else {}
    normalized = event.get("normalized_event") if isinstance(event.get("normalized_event"), Mapping) else {}
    outcome = event_payload.get("outcome") if event_payload.get("outcome") is not None else normalized.get("outcome_he")
    return str(outcome or "")


def _v3_source_provenance(*, event: Mapping[str, Any], wrapper: Mapping[str, Any]) -> dict[str, Any]:
    quality = wrapper.get("quality") if isinstance(wrapper.get("quality"), Mapping) else {}
    for payload in (event, wrapper, quality):
        source_provenance = payload.get("source_provenance") if isinstance(payload.get("source_provenance"), Mapping) else None
        if source_provenance:
            return dict(source_provenance)
    return {}


def _v3_primary_time(*, event: Mapping[str, Any], wrapper: Mapping[str, Any]) -> dict[str, Any] | None:
    for source in _v3_time_sources(event=event, wrapper=wrapper):
        primary = source.get("primary_time") if isinstance(source.get("primary_time"), Mapping) else None
        if primary and primary.get("start"):
            return dict(primary)
    return None


def _v3_time_mentions(*, event: Mapping[str, Any], wrapper: Mapping[str, Any]) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    for source in _v3_time_sources(event=event, wrapper=wrapper):
        raw_mentions = source.get("time_mentions") if isinstance(source.get("time_mentions"), list) else []
        if not raw_mentions and isinstance(source.get("date_mentions"), list):
            raw_mentions = source.get("date_mentions") or []
        for mention in raw_mentions:
            if isinstance(mention, Mapping):
                mentions.append(dict(mention))
    return mentions[:40]


def _v3_raw_date_mentions(*, event: Mapping[str, Any], wrapper: Mapping[str, Any]) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    for source in _v3_time_sources(event=event, wrapper=wrapper):
        raw_mentions = source.get("raw_date_mentions") if isinstance(source.get("raw_date_mentions"), list) else []
        if not raw_mentions and isinstance(source.get("date_mentions"), list):
            raw_mentions = source.get("date_mentions") or []
        for mention in raw_mentions:
            if isinstance(mention, Mapping):
                mentions.append(dict(mention))
    return mentions[:40]


def _v3_time_sources(*, event: Mapping[str, Any], wrapper: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sources: list[Mapping[str, Any]] = []
    quality = wrapper.get("quality") if isinstance(wrapper.get("quality"), Mapping) else {}
    for payload in (event, wrapper, quality):
        if isinstance(payload, Mapping):
            sources.append(payload)
            for key in ("general_text_metadata", "event_metadata", "subject_metadata"):
                nested = payload.get(key)
                if isinstance(nested, Mapping):
                    sources.append(nested)
    return sources


def _v3_evidence_quotes(event: Mapping[str, Any]) -> list[str]:
    event_payload = event.get("event_payload") if isinstance(event.get("event_payload"), Mapping) else {}
    normalized = event.get("normalized_event") if isinstance(event.get("normalized_event"), Mapping) else {}
    quotes: list[str] = []
    for value in (
        event_payload.get("action_quote_he"),
        event_payload.get("action_focus_quote_he"),
        normalized.get("supporting_quote_he"),
        normalized.get("action_focus_quote_he"),
    ):
        if str(value or "").strip():
            quotes.append(str(value).strip())
    entailment = event_payload.get("v3_evidence_entailment") if isinstance(event_payload.get("v3_evidence_entailment"), Mapping) else {}
    field_assessments = entailment.get("field_assessments") if isinstance(entailment.get("field_assessments"), Mapping) else entailment
    if isinstance(field_assessments, Mapping):
        for assessment in field_assessments.values():
            if isinstance(assessment, Mapping) and str(assessment.get("source_quote_he") or "").strip():
                quotes.append(str(assessment["source_quote_he"]).strip())
    return _dedupe_text(quotes)[:6]


def _direct_archetype_matches(
    *,
    source_text: str,
    candidates: Sequence[ProtocolGisCandidate],
    archetypes: Sequence[Mapping[str, Any]],
) -> list[ProtocolV3GisArchetypeMatch]:
    candidate_layer_keys = {candidate.layer_key for candidate in candidates}
    if not candidate_layer_keys:
        return []
    normalized_source = normalize_hebrew_text(source_text)
    source_tokens = set(normalized_source.split())
    matches: list[tuple[int, ProtocolV3GisArchetypeMatch]] = []
    for archetype in archetypes:
        layer_keys = tuple(str(value) for value in archetype.get("layer_keys") or () if str(value).strip())
        matched_layers = tuple(key for key in layer_keys if key in candidate_layer_keys)
        if not matched_layers:
            continue
        terms = _archetype_terms(archetype)
        matched_terms = tuple(term for term in terms if term in source_tokens)
        first_layer_direct = bool(layer_keys and layer_keys[0] in candidate_layer_keys)
        if not matched_terms or (len(matched_layers) < 2 and not first_layer_direct):
            continue
        if len(matched_terms) < 2 and not (len(matched_layers) >= 3 and first_layer_direct):
            continue
        confidence = "high" if len(matched_layers) >= 2 and len(matched_terms) >= 2 else "medium"
        score = len(matched_layers) * 10 + len(matched_terms) + (5 if first_layer_direct else 0)
        matches.append(
            (
                score,
                ProtocolV3GisArchetypeMatch(
                    key=str(archetype.get("key") or ""),
                    question=str(archetype.get("question") or archetype.get("base_question") or ""),
                    topic=str(archetype.get("topic") or ""),
                    intent=str(archetype.get("intent") or ""),
                    matched_layer_keys=matched_layers,
                    matched_terms=matched_terms[:8],
                    confidence_label=confidence,
                    reason_he="האירוע כולל גם שכבות GIS רלוונטיות וגם מונחי נושא שמופיעים בשאלת התושב; לכן זה ארכיטיפ נתמך ישירות ולא התאמה רחבה בלבד.",
                ),
            )
        )
    matches.sort(key=lambda item: (-item[0], item[1].key))
    return [match for _, match in matches[:8]]


def _archetype_terms(archetype: Mapping[str, Any]) -> tuple[str, ...]:
    question_text = str(archetype.get("base_question") or archetype.get("question") or "")
    text = " ".join(str(value or "") for value in (archetype.get("topic"), question_text))
    normalized = normalize_hebrew_text(text.replace("/", " "))
    tokens = [token.strip(" ?.,;:()[]{}-–\"'׳״") for token in normalized.split()]
    return tuple(
        _dedupe_text(
            token
            for token in tokens
            if len(token) >= 4 and token not in _ARCHETYPE_STOPWORDS and not token.isdigit()
        )
    )


def _extract_signals(row: Mapping[str, Any], source_text: str) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    cadaster_signal = _cadaster_signal(source_text)
    if cadaster_signal:
        signals.append(cadaster_signal)

    for place in _named_place_signals(row, source_text):
        signals.append(place)

    neighborhood = _neighborhood_signal(source_text)
    if neighborhood:
        signals.append(neighborhood)

    if not signals:
        citywide = _citywide_signal(source_text)
        if citywide:
            signals.append(citywide)
    return _dedupe_signals(signals)


def _cadaster_signal(source_text: str) -> dict[str, Any] | None:
    cadastral = extract_cadastral_id(source_text)
    if cadastral:
        gush, parcel = cadastral
        return {
            "linking_mode": "explicit_cadaster_reference",
            "signal_type": "cadastral_reference",
            "extracted_signal": f"גוש {gush} חלקה {parcel}",
            "candidate_query": {"gush": gush, "parcel": parcel},
            "confidence_label": "high",
            "preferred_layer_keys": ("parcels_cadaster", "planning_land_use"),
            "reason_he": "הטקסט כולל גוש וחלקה מפורשים ולכן עדיף להתחיל בהתאמה קדסטרית ולא בנושא כללי.",
            "uncertainty_he": "נדרש אימות מול שכבת קדסטר רשמית לפני הצגת גבול או בעלות.",
        }
    gush_match = _GUSH_ONLY_RE.search(source_text)
    if gush_match:
        query: dict[str, Any] = {"gush": gush_match.group("gush")}
        parcel_match = _PARCEL_ONLY_RE.search(source_text)
        if parcel_match:
            query["parcel"] = parcel_match.group("parcel")
        return {
            "linking_mode": "explicit_cadaster_reference",
            "signal_type": "cadastral_reference",
            "extracted_signal": " ".join(f"{key} {value}" for key, value in query.items()),
            "candidate_query": query,
            "confidence_label": "high" if "parcel" in query else "medium",
            "preferred_layer_keys": ("parcels_cadaster", "planning_land_use"),
            "reason_he": "הטקסט כולל מזהה קדסטרי מפורש ולכן יש בסיס לחיפוש גוש/חלקה.",
            "uncertainty_he": "אם חסרה חלקה, ההתאמה היא לגוש או הקשר תכנוני בלבד.",
        }
    plan_numbers = _extract_plan_numbers(source_text)
    if plan_numbers:
        return {
            "linking_mode": "explicit_cadaster_reference",
            "signal_type": "cadastral_reference",
            "extracted_signal": plan_numbers[0],
            "candidate_query": {"plan_number": plan_numbers[0]},
            "confidence_label": "medium",
            "preferred_layer_keys": ("planning_land_use", "parcels_cadaster"),
            "reason_he": "הטקסט כולל מספר תכנית מפורש ולכן מתאים לחיפוש שכבות תכנון לפני קישור נושאי.",
            "uncertainty_he": "מספר תכנית אינו מזהה תמיד גבול מגרש או החלטה סופית.",
        }
    return None


def _named_place_signals(row: Mapping[str, Any], source_text: str) -> list[dict[str, Any]]:
    places = _places_from_text(str(row.get("subject_object_he") or ""))
    if not places:
        places = []
        for value in (row.get("subject_details_he"), row.get("subject_matter_he"), row.get("decision_source_quote_he")):
            places.extend(_places_from_text(str(value or "")))
    if not places:
        places = [match.group("value") for match in _PLACE_TYPE_RE.finditer(source_text)]
    signals: list[dict[str, Any]] = []
    for place in _dedupe_text(_clean_place(value) for value in places):
        if not _looks_like_concrete_place(place):
            continue
        signals.append(
            {
                "linking_mode": "named_facility_geocoding",
                "signal_type": "named_place",
                "extracted_signal": place,
                "candidate_query": {"place_query": place},
                "confidence_label": "medium",
                "preferred_layer_keys": _facility_layer_keys(place),
                "reason_he": "נמצא שם מקום/מתקן מפורש במקור, ולכן ניתן לנסות גיאוקוד או התאמת POI לפני הסקת קשר נושאי.",
                "uncertainty_he": "השם עשוי להיות מוסד מקומי שאינו קיים בשכבת GovMap כללית; נדרש אימות תוצאה.",
            }
        )
    return signals[:3]


def _neighborhood_signal(source_text: str) -> dict[str, Any] | None:
    match = _NEIGHBORHOOD_RE.search(source_text)
    if not match:
        return None
    value = _clean_place(match.group(1))
    first_token = (normalize_hebrew_text(value).split() or [""])[0]
    if not value or first_token in {"שעות", "כל", "מונחים", "לתת", "אחר", "יש"}:
        return None
    raw_first_token = (value.split() or [""])[0]
    if "'" in raw_first_token or "׳" in raw_first_token or "\"" in raw_first_token or "״" in raw_first_token or len(raw_first_token) <= 3:
        value = raw_first_token
    return {
        "linking_mode": "neighborhood_or_statistical_area",
        "signal_type": "resident_address",
        "extracted_signal": value,
        "candidate_query": {"area_query": value},
        "confidence_label": "medium",
        "preferred_layer_keys": ("neighborhoods_and_statistics",),
        "reason_he": "הטקסט מציין רובע/שכונה/אזור ולכן ניתן לקשר להקשר מרחבי סטטיסטי או שכונתי.",
        "uncertainty_he": "שם אזור בפרוטוקול אינו תמיד זהה לשם הרשמי בשכבת GIS.",
    }


def _citywide_signal(source_text: str) -> dict[str, Any] | None:
    normalized = normalize_hebrew_text(source_text)
    layer_keys = _citywide_layer_keys(normalized)
    if not layer_keys:
        return None
    return {
        "linking_mode": "citywide_context",
        "signal_type": "municipal_action",
        "extracted_signal": "citywide_context",
        "candidate_query": {},
        "confidence_label": "low",
        "preferred_layer_keys": layer_keys,
        "reason_he": "לא נמצא מקום קונקרטי, אבל הטקסט מתאר שירות/מפגע/פעולה עירונית שיכולים לקבל הקשר שכבות כללי.",
        "uncertainty_he": "אין להסיק מיקום או feature ספציפי ללא כתובת, רובע, חלקה או שם מקום.",
    }


def _rank_layers_for_signal(signal: Mapping[str, Any], registry: ResidentGisRegistry) -> list[ResidentGisLayerGroup]:
    preferred = tuple(str(value) for value in signal.get("preferred_layer_keys") or ())
    by_key = registry.layer_by_key
    ranked = [by_key[key] for key in preferred if key in by_key]
    if ranked:
        return ranked[:4]
    signal_type = str(signal.get("signal_type") or "")
    if signal_type:
        ranked.extend(layer for layer in registry.layer_groups if signal_type in layer.protocol_signal_types and layer.layer_key not in {item.layer_key for item in ranked})
    return ranked[:4]


def _candidate_from_signal(signal: Mapping[str, Any], layer: ResidentGisLayerGroup, *, artifact_id: str, municipality_slug: str, source_text: str) -> ProtocolGisCandidate:
    return ProtocolGisCandidate(
        artifact_id=artifact_id,
        municipality_slug=municipality_slug,
        source_text=_shorten(source_text, 320),
        extracted_signal=str(signal.get("extracted_signal") or ""),
        linking_mode=str(signal.get("linking_mode") or ""),
        signal_type=str(signal.get("signal_type") or ""),
        layer_key=layer.layer_key,
        layer_display_name_he=layer.display_name_he,
        govmap_aliases=tuple(layer.govmap_aliases),
        govmap_layer_aliases=tuple(govmap_layer.alias for govmap_layer in layer.govmap_layers),
        confidence_label=str(signal.get("confidence_label") or "low"),
        candidate_query=dict(signal.get("candidate_query") or {}),
        reason_he=str(signal.get("reason_he") or ""),
        uncertainty_he=str(signal.get("uncertainty_he") or ""),
    )


def _topic_subject_artifact_rows(session: "Session", *, artifact_ids: Sequence[str] | None, limit: int) -> list[Mapping[str, Any]]:
    from sqlalchemy import text

    params: dict[str, Any] = {"limit": int(limit)}
    artifact_filter = ""
    if artifact_ids:
        placeholders = ", ".join(f":artifact_{index}" for index, _ in enumerate(artifact_ids))
        artifact_filter = f"AND ts.artifact_id IN ({placeholders})"
        params.update({f"artifact_{index}": artifact_id for index, artifact_id in enumerate(artifact_ids)})
    return list(
        session.execute(
            text(
                f"""
                SELECT ts.artifact_id, ts.municipality_slug, ts.topic_label_he,
                       ts.subject_root_label_he, ts.subject_child_label_he, ts.subject_object_he,
                       ts.subject_details_he, ts.subject_matter_he, ts.action_details_he,
                       ts.decision_source_quote_he, ts.confidence, ts.validation_status,
                       ra.title_he, ra.body_text, ra.retrieval_text, ra.citation_label
                FROM topic_subject ts
                LEFT JOIN retrieval_artifact ra ON ra.artifact_id = ts.artifact_id
                WHERE COALESCE(ts.validation_status, '') != 'failed'
                {artifact_filter}
                ORDER BY ts.confidence DESC, ts.artifact_id ASC, ts.subject_index ASC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()
    )


def _row_source_text(row: Mapping[str, Any]) -> str:
    parts = [
        row.get("subject_object_he"),
        row.get("subject_details_he"),
        row.get("subject_matter_he"),
        row.get("action_details_he"),
        row.get("decision_source_quote_he"),
        row.get("title_he"),
        row.get("body_text"),
        row.get("retrieval_text"),
    ]
    return "\n".join(_dedupe_text(str(part).strip() for part in parts if str(part or "").strip()))


def _facility_layer_keys(place: str) -> tuple[str, ...]:
    normalized = normalize_hebrew_text(place)
    for terms, layer_keys in _FACILITY_LAYER_HINTS:
        if any(term in normalized for term in terms):
            return layer_keys
    return ("public_buildings_assets", "neighborhoods_and_statistics")


def _citywide_layer_keys(normalized_text: str) -> tuple[str, ...]:
    out: list[str] = []
    tokens = set(normalized_text.split())
    for terms, layer_keys in _CITYWIDE_LAYER_HINTS:
        if any((term in normalized_text if " " in term else term in tokens) for term in terms):
            out.extend(layer_keys)
    return tuple(_dedupe_text(out))


def _looks_like_concrete_place(place: str) -> bool:
    normalized = normalize_hebrew_text(place)
    if not normalized:
        return False
    if "סגן" in normalized:
        return False
    if not _has_concrete_place_type(normalized):
        return False
    tokens = normalized.split()
    if len(tokens) < 2:
        return False
    return tokens[-1] not in _ABSTRACT_PLACE_SUFFIXES


def _has_concrete_place_type(value: str) -> bool:
    normalized = normalize_hebrew_text(value)
    return any(place_type in normalized for place_type in _CONCRETE_PLACE_TYPES)


def _places_from_text(value: str) -> list[str]:
    if not value:
        return []
    places = [match.group("value") for match in _QUOTED_PLACE_RE.finditer(value) if _has_concrete_place_type(match.group("value"))]
    places.extend(match.group("value") for match in _PLACE_TYPE_RE.finditer(value))
    return places


def _clean_place(value: str) -> str:
    text_value = " ".join(str(value or "").replace("\n", " ").split())
    return text_value.strip(" ,.;:()[]{}-–")


def _dedupe_signals(signals: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for signal in signals:
        key = (str(signal.get("linking_mode") or ""), str(signal.get("extracted_signal") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(signal))
    return out


def _dedupe_candidates(candidates: Iterable[ProtocolGisCandidate]) -> list[ProtocolGisCandidate]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[ProtocolGisCandidate] = []
    for candidate in candidates:
        key = (candidate.artifact_id, candidate.linking_mode, candidate.extracted_signal, candidate.layer_key)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _dedupe_text(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text_value = str(value or "").strip()
        if not text_value or text_value in seen:
            continue
        seen.add(text_value)
        out.append(text_value)
    return out


def _extract_plan_numbers(value: str) -> list[str]:
    return _dedupe_text(match.group(1).strip() for match in _PLAN_NUMBER_RE.finditer(str(value or "")))


def _shorten(value: str, limit: int) -> str:
    text_value = " ".join(str(value or "").split())
    return text_value if len(text_value) <= limit else f"{text_value[: limit - 1]}…"


def _md_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def main(argv: Sequence[str] | None = None) -> int:
    from sqlalchemy.orm import Session

    from municipality.db import build_engine

    parser = argparse.ArgumentParser(description="Generate a report-only protocol-to-GIS link candidate table.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--artifact-id", action="append", dest="artifact_ids")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args(argv)
    engine = build_engine(args.database_url)
    with Session(engine) as session:
        candidates = protocol_gis_link_report(session, artifact_ids=args.artifact_ids, limit=args.limit)
    if args.format == "json":
        print(json.dumps([candidate.to_payload() for candidate in candidates], ensure_ascii=False, indent=2))
    else:
        print(protocol_gis_link_report_markdown(candidates))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
