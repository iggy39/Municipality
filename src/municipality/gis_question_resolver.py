from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from municipality.gis_normalization import extract_cadastral_id, normalize_hebrew_text


PLAN_NUMBER_RE = re.compile(r"(?<!\d)(\d{3,}-\d{3,})(?!\d)")
NEAR_PLACE_RE = re.compile(r"(?:ליד|סביב|באזור|באיזור|בשכונת|בשכונה|ברובע|בקרבת)\s+([^?.,;]+)")
STREET_PLACE_RE = re.compile(r"(?:רחוב|רח'|רח׳|שדרות|שדרה|דרך)\s+([^?.,;:()]{2,40})")
RELIGIOUS_SERVICE_TERMS = ("מקווה", "מקוא", "מקוואות", "מקואות", "שירותי דת", "מועצה דתית")


@dataclass(frozen=True)
class GisFocus:
    focus_type: str
    confidence_label: str
    plan_number: str | None = None
    gush: str | None = None
    helka: str | None = None
    place_query: str | None = None
    address_query: str | None = None
    unresolved_reason: str | None = None
    matched_text: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if value is not None}


@dataclass(frozen=True)
class GeoIntentResolution:
    intent: str
    confidence_label: str
    needs_gis: bool
    matched_terms: list[str] = field(default_factory=list)
    focus: GisFocus | None = None
    resident_layer_keys: tuple[str, ...] = ()
    govmap_layer_aliases: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "intent": self.intent,
            "confidence_label": self.confidence_label,
            "needs_gis": self.needs_gis,
            "matched_terms": list(self.matched_terms),
        }
        if self.focus is not None:
            payload["focus"] = self.focus.to_payload()
        if self.resident_layer_keys:
            payload["resident_layer_keys"] = list(self.resident_layer_keys)
        if self.govmap_layer_aliases:
            payload["govmap_layer_aliases"] = list(self.govmap_layer_aliases)
        return payload


def resolve_geo_intent(question: str) -> GeoIntentResolution:
    """Resolve GIS-relevant resident intent without relying on municipality-specific rules."""

    normalized = normalize_hebrew_text(question)
    focus = resolve_gis_focus(question)
    matched_terms: list[str] = []

    if focus is not None:
        matched_terms.append(focus.focus_type)

    if _has_any(normalized, RELIGIOUS_SERVICE_TERMS):
        return GeoIntentResolution(
            intent="public_service_facility_context",
            confidence_label="בינונית" if focus is None else focus.confidence_label,
            needs_gis=True,
            matched_terms=_dedupe([*matched_terms, "שירותי דת", "מקווה"]),
            focus=focus or GisFocus(focus_type="place", confidence_label="בינונית", place_query="מקווה טהרה", matched_text="מקווה"),
            resident_layer_keys=("religious_services", "neighborhoods_and_statistics"),
            govmap_layer_aliases=("mikve", "neighborhoods_area"),
        )

    if focus and focus.focus_type == "parcel":
        return GeoIntentResolution(
            intent="parcel_context",
            confidence_label="גבוהה",
            needs_gis=True,
            matched_terms=_dedupe([*matched_terms, "גוש", "חלקה"]),
            focus=focus,
        )

    if focus and focus.focus_type == "plan" and _has_any(normalized, ("סטטוס", "מעמד", "שלב", "איפה", "מה קורה")):
        return GeoIntentResolution(
            intent="plan_status",
            confidence_label="גבוהה",
            needs_gis=True,
            matched_terms=_dedupe([*matched_terms, "תכנית", "סטטוס"]),
            focus=focus,
        )

    if _has_any(normalized, ("התנגדות", "התנגדויות", "להגיש התנגדות", "הפקדה", "פרסום")) and _has_any(normalized, ("תכנית", "תוכנית", "אזור", "איזור")):
        return GeoIntentResolution(
            intent="planning_public_participation",
            confidence_label="בינונית" if focus is None else "גבוהה",
            needs_gis=True,
            matched_terms=_dedupe([*matched_terms, "התנגדות", "הפקדה"]),
            focus=focus,
        )

    if _has_any(normalized, ("פרויקט", "פרויקטים", "מגורים", "בנייה", "בניה", "פיתוח", "התחדשות")) and _has_any(normalized, ("ליד", "סביב", "באזור", "באיזור", "שכונה", "רובע", "עיר")):
        return GeoIntentResolution(
            intent="development_near_place",
            confidence_label="בינונית" if focus is None else "גבוהה",
            needs_gis=True,
            matched_terms=_dedupe([*matched_terms, "פרויקט", "מגורים"]),
            focus=focus,
        )

    if _has_any(normalized, ("תחבורה", "תחנות", "אוטובוס", "רכבת", "בתי ספר", "בית ספר", "גנים", "גן ילדים")) and _has_any(normalized, ("ליד", "סביב", "באזור", "באיזור", "מקום", "בית")):
        return GeoIntentResolution(
            intent="services_near_place",
            confidence_label="בינונית" if focus is None else "גבוהה",
            needs_gis=True,
            matched_terms=_dedupe([*matched_terms, "תחבורה", "חינוך"]),
            focus=focus,
        )

    if _has_any(normalized, ("מה קרה", "מה הוחלט", "מה קורה", "פעיל", "פעילות")) and _has_any(normalized, ("ליד", "סביב", "באזור", "באיזור", "שכונה", "רובע", "עיר")):
        return GeoIntentResolution(
            intent="activity_near_place",
            confidence_label="בינונית" if focus is None else "גבוהה",
            needs_gis=True,
            matched_terms=_dedupe([*matched_terms, "פעילות", "מקום"]),
            focus=focus,
        )

    if _has_any(normalized, ("איפה בעיר", "בכל העיר", "נושאים פעילים", "מוקדי פעילות", "הכי הרבה")):
        return GeoIntentResolution(
            intent="city_activity_overview",
            confidence_label="בינונית",
            needs_gis=True,
            matched_terms=_dedupe([*matched_terms, "עיר", "פעילות"]),
            focus=focus,
        )

    if focus and focus.focus_type == "plan":
        return GeoIntentResolution(
            intent="plan_lookup",
            confidence_label="גבוהה",
            needs_gis=True,
            matched_terms=_dedupe([*matched_terms, "תכנית"]),
            focus=focus,
        )

    return GeoIntentResolution(intent="unknown", confidence_label="נמוכה", needs_gis=False, matched_terms=[], focus=focus)


def resolve_gis_focus(question: str) -> GisFocus | None:
    plan_match = PLAN_NUMBER_RE.search(str(question or ""))
    if plan_match:
        return GisFocus(
            focus_type="plan",
            confidence_label="גבוהה",
            plan_number=plan_match.group(1),
            matched_text=plan_match.group(1),
        )

    cadastral = extract_cadastral_id(question)
    if cadastral:
        gush, helka = cadastral
        return GisFocus(
            focus_type="parcel",
            confidence_label="גבוהה",
            gush=gush,
            helka=helka,
            matched_text=f"{gush}/{helka}",
        )

    street = _extract_street_query(question)
    if street:
        return GisFocus(
            focus_type="place",
            confidence_label="בינונית",
            place_query=street,
            address_query=street,
            matched_text=street,
        )

    place = _extract_place_query(question)
    if place:
        normalized_place = normalize_hebrew_text(place)
        unresolved = "resident_relative_place" if _has_any(normalized_place, ("שלי", "הבית", "המקום")) else None
        return GisFocus(
            focus_type="place" if unresolved is None else "unresolved_place",
            confidence_label="בינונית" if unresolved is None else "נמוכה",
            place_query=place,
            unresolved_reason=unresolved,
            matched_text=place,
        )

    return None


def _extract_place_query(question: str) -> str | None:
    match = NEAR_PLACE_RE.search(str(question or ""))
    if not match:
        return None
    value = " ".join(match.group(1).split()).strip(" ?.,;:")
    return value or None


def _extract_street_query(question: str) -> str | None:
    match = STREET_PLACE_RE.search(str(question or ""))
    if not match:
        return None
    prefix = match.group(0).split()[0]
    value = match.group(1)
    value = re.split(r"\s+(?:ברובע|בשכונה|באשדוד|בתל|בירושלים|בחיפה|בבאר|ליד|סביב)\b", value, maxsplit=1)[0]
    value = " ".join(value.split()).strip(" ?.,;:")
    return f"{prefix} {value}" if value else None


def _has_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(term in value for term in terms)


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out
