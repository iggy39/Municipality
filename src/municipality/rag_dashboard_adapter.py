from __future__ import annotations

import base64
import json
from copy import deepcopy
from typing import Any

from sqlalchemy import select

from municipality.models import Document, DocumentVersion, RetrievalArtifact, SourceSite
from municipality.rag_dashboard_contracts import RagDashboardPayload
from municipality.rag_dashboard_mock import get_mock_rag_dashboard_payload
from municipality.source_type_taxonomy import source_type_codes_from_filter_value, source_type_display_fields, source_type_metadata


SCHEMATIC_MAP_PROVENANCE = {
    "status": "schematic_only",
    "label_he": "מפה סכמטית בלבד",
    "description_he": "אין גיאומטריית GIS מאומתת; המיקומים והצורות מוצגים להמחשה בלבד על בסיס הראיות.",
    "source_evidence_refs": [],
}


def validate_dashboard_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return RagDashboardPayload.model_validate(payload).model_dump(mode="json")


def build_dashboard_payload_from_ask_result(
    *,
    question: str,
    ask_payload: dict[str, Any],
    municipality_id: str | None = None,
    filters: dict[str, Any] | None = None,
    geo_intent_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = get_mock_rag_dashboard_payload()
    status = str(ask_payload.get("status") or "unknown")
    citations = ask_payload.get("citations") if isinstance(ask_payload.get("citations"), list) else []
    evidence = [_citation_to_evidence(row, idx, ask_payload=ask_payload) for idx, row in enumerate(citations, start=1) if isinstance(row, dict)]
    evidence = [row for row in evidence if row is not None]
    decisions = [_citation_to_decision(row, idx, evidence[idx - 1], ask_payload=ask_payload) for idx, row in enumerate(citations, start=1) if idx <= len(evidence)]

    answer_text = str(ask_payload.get("answer") or ask_payload.get("extended_answer") or "").strip()
    refusal = ask_payload.get("refusal") if isinstance(ask_payload.get("refusal"), dict) else None
    limitations = [str(item) for item in ask_payload.get("limitations") or [] if str(item).strip()]
    if not limitations:
        limitations = ["יש לפתוח את המקורות לפני הסקת מסקנות." if status == "answer" else "לא נמצאה תשתית ראייתית מספקת להצגת תשובה."]
    active_filters = _normalize_filters(filters)
    selected_category_id = _category_id_from_filter(active_filters.get("category")) or payload["state"].get("selected_category_id")
    map_context = _map_context_from_geo(geo_intent_resolution)
    map_context_entity = _map_context_entity(map_context)
    map_context_entity_id = map_context_entity["id"] if map_context_entity else None
    if active_filters.get("area") and active_filters["area"] != "כל העיר":
        limitations.append("סינון אזורי נשען על אזכורי מקום מהמקורות ולא על גיאומטריית GIS מאומתת.")
    limitations.extend(_map_context_limitations(map_context))
    limitations.extend(_municipality_scope_limitations(geo_intent_resolution))

    payload["state"] = {
        **payload["state"],
        "current_question": question,
        "municipality_id": municipality_id or ask_payload.get("municipality_id") or payload["state"].get("municipality_id"),
        "search_intent": _dashboard_search_intent(geo_intent_resolution, payload["state"].get("search_intent")),
        "intent_resolution": _dashboard_intent_resolution(geo_intent_resolution, payload["state"].get("intent_resolution")),
        "current_answer_id": str(ask_payload.get("ask_request_id") or "dashboard_query_answer"),
        "active_detail_drawer_mode": "answer" if status == "answer" else "emptyState",
        "generation_status": "answer_ready" if status == "answer" else "empty_answer",
        "selected_category_id": selected_category_id,
        "selected_map_entity_id": map_context_entity_id or payload["state"].get("selected_map_entity_id"),
        "selected_time_range": _selected_time_range_from_filters(active_filters) or payload["state"].get("selected_time_range"),
        "source_type_filter": _source_filter_from_filters(active_filters),
        "confidence_filter": _confidence_filter_from_filters(active_filters),
        "active_filter_count": len(active_filters),
        "active_filter_summary": active_filters,
        "error": None,
    }
    payload["main_civic_workspace"]["map"] = _schematic_map(payload["main_civic_workspace"]["map"])
    if map_context_entity:
        payload["main_civic_workspace"]["map"]["entities"] = [
            {**entity, "selected": False}
            for entity in payload["main_civic_workspace"]["map"].get("entities", [])
        ]
        payload["main_civic_workspace"]["map"]["entities"].insert(0, map_context_entity)
    if map_context is not None:
        payload["main_civic_workspace"]["map_context"] = map_context
    _apply_selected_category(payload, selected_category_id)
    payload["end_detail_drawer"] = {
        **payload["end_detail_drawer"],
        "mode": "answer" if status == "answer" else "emptyState",
        "answer_id": str(ask_payload.get("ask_request_id") or "dashboard_query_answer"),
        "question": question,
        "brief": answer_text or (str(refusal.get("message_he") or "לא נמצאה תשובה מספקת במסמכים.") if refusal else "לא נמצאה תשובה מספקת במסמכים."),
        "confidence_label": _confidence_from_ask(status=status, evidence_count=len(evidence), ask_payload=ask_payload),
        "decisions": decisions,
        "limitations": limitations,
    }
    payload["evidence"] = evidence
    payload["contracts"]["decisions"] = decisions
    payload["contracts"]["evidence"] = evidence
    payload["contracts"]["map_entities"] = payload["main_civic_workspace"]["map"]["entities"]
    return validate_dashboard_payload(payload)


def _dashboard_search_intent(geo_intent_resolution: dict[str, Any] | None, fallback: Any) -> str | None:
    intent = str((geo_intent_resolution or {}).get("intent") or "").strip()
    if intent and intent != "unknown":
        return intent
    return str(fallback) if fallback is not None else None


def _dashboard_intent_resolution(geo_intent_resolution: dict[str, Any] | None, fallback: Any) -> dict[str, Any]:
    if isinstance(geo_intent_resolution, dict) and geo_intent_resolution:
        return {"geo": geo_intent_resolution}
    return fallback if isinstance(fallback, dict) else {}


def _map_context_from_geo(geo_intent_resolution: dict[str, Any] | None) -> dict[str, Any] | None:
    map_context = (geo_intent_resolution or {}).get("map_context")
    return map_context if isinstance(map_context, dict) else None


def _map_context_entity(map_context: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(map_context, dict):
        return None
    focus = map_context.get("focus") if isinstance(map_context.get("focus"), dict) else {}
    focus_type = str(focus.get("focus_type") or "gis_focus")
    label = _map_context_focus_label(map_context)
    if not label:
        return None
    return {
        "id": f"entity_gis_focus_{focus_type}",
        "label": label,
        "entity_type": {
            "code": _map_context_entity_type_code(focus_type),
            "label_he": "מוקד GIS",
            "vocabulary": "municipal-entity-type:v1",
        },
        "spatial_representation": "schematic",
        "schematic_shape": {"kind": "point", "coordinates": []},
        "real_geometry": None,
        "geometry_provenance": None,
        "confidence_label": str(focus.get("confidence_label") or "בינונית"),
        "uncertainty_reasons": ["מיקום אמיתי נשמר ב-map_context; המפה הסכמטית מסמנת רק את מוקד השאלה."],
        "activity_score": 100,
        "is_recent_high_activity": False,
        "topic_ids": [],
        "decision_ids": [],
        "evidence_refs": [],
        "selected": True,
    }


def _map_context_focus_label(map_context: dict[str, Any]) -> str | None:
    focus = map_context.get("focus") if isinstance(map_context.get("focus"), dict) else {}
    focus_type = str(focus.get("focus_type") or "")
    if focus_type == "parcel":
        return f"גוש {focus.get('gush')} חלקה {focus.get('helka')}"
    if focus_type == "plan":
        return f"תכנית {focus.get('plan_number')}"
    if focus_type in {"place", "unresolved_place"}:
        return str(focus.get("place_query") or "מוקד מקום").strip()
    if focus_type == "point":
        return "נקודה במפה"
    return None


def _map_context_entity_type_code(focus_type: str) -> str:
    return {
        "parcel": "PARCEL",
        "plan": "PLANNING_PROJECT",
        "place": "PLACE",
        "unresolved_place": "PLACE",
        "point": "POINT",
    }.get(focus_type, "GIS_FOCUS")


def _map_context_limitations(map_context: dict[str, Any] | None) -> list[str]:
    if not isinstance(map_context, dict):
        return []
    caveats = [str(item).strip() for item in map_context.get("caveats") or [] if str(item).strip()]
    if not caveats and map_context.get("status") in {"focus_not_found", "focus_unresolved", "focus_needs_lookup"}:
        caveats.append("מוקד GIS לא נפתר במלואו ולכן שכבות המפה מוגבלות.")
    return caveats


def _municipality_scope_limitations(geo_intent_resolution: dict[str, Any] | None) -> list[str]:
    scope = (geo_intent_resolution or {}).get("municipality_scope")
    if not isinstance(scope, dict):
        return []
    caveat = str(scope.get("caveat_he") or "").strip()
    return [caveat] if caveat else []


def build_dashboard_error_payload(*, question: str, error_code: str, message_he: str, municipality_id: str | None = None, geo_intent_resolution: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = get_mock_rag_dashboard_payload()
    map_context = _map_context_from_geo(geo_intent_resolution)
    map_context_entity = _map_context_entity(map_context)
    payload["state"] = {
        **payload["state"],
        "current_question": question,
        "municipality_id": municipality_id or payload["state"].get("municipality_id"),
        "search_intent": _dashboard_search_intent(geo_intent_resolution, payload["state"].get("search_intent")),
        "intent_resolution": _dashboard_intent_resolution(geo_intent_resolution, payload["state"].get("intent_resolution")),
        "active_detail_drawer_mode": "errorState",
        "generation_status": "error",
        "selected_map_entity_id": map_context_entity["id"] if map_context_entity else payload["state"].get("selected_map_entity_id"),
        "error": {"code": error_code, "message_he": message_he},
    }
    payload["main_civic_workspace"]["map"] = _schematic_map(payload["main_civic_workspace"]["map"])
    if map_context_entity:
        payload["main_civic_workspace"]["map"]["entities"] = [
            {**entity, "selected": False}
            for entity in payload["main_civic_workspace"]["map"].get("entities", [])
        ]
        payload["main_civic_workspace"]["map"]["entities"].insert(0, map_context_entity)
    if map_context is not None:
        payload["main_civic_workspace"]["map_context"] = map_context
    payload["end_detail_drawer"] = {
        **payload["end_detail_drawer"],
        "mode": "errorState",
        "question": question,
        "brief": message_he,
        "confidence_label": "לא זמין",
        "decisions": [],
        "limitations": ["הבקשה לא הצליחה. נסו שוב או בדקו את שירות התשובות."],
    }
    payload["evidence"] = []
    payload["contracts"]["decisions"] = []
    payload["contracts"]["evidence"] = []
    payload["contracts"]["map_entities"] = payload["main_civic_workspace"]["map"]["entities"]
    return validate_dashboard_payload(payload)


def get_dashboard_artifact_evidence(*, db, evidence_id: str) -> dict[str, Any] | None:
    artifact_id = decode_artifact_evidence_id(evidence_id)
    if artifact_id is None:
        return None
    row = db.execute(
        select(RetrievalArtifact, Document, DocumentVersion, SourceSite)
        .join(Document, Document.id == RetrievalArtifact.document_id)
        .join(DocumentVersion, DocumentVersion.id == RetrievalArtifact.document_version_id)
        .join(SourceSite, SourceSite.id == Document.source_site_id)
        .where(RetrievalArtifact.artifact_id == artifact_id)
    ).first()
    if row is None:
        return None
    artifact, document, version, _site = row
    return validate_dashboard_payload(
        {
            **get_mock_rag_dashboard_payload(),
            "evidence": [_artifact_to_evidence(artifact, document, version, retrieval_set_id=None)],
            "contracts": {
                **get_mock_rag_dashboard_payload()["contracts"],
                "evidence": [_artifact_to_evidence(artifact, document, version, retrieval_set_id=None)],
            },
        }
    )["evidence"][0]


def encode_artifact_evidence_id(artifact_id: str) -> str:
    encoded = base64.urlsafe_b64encode(str(artifact_id).encode("utf-8")).decode("ascii").rstrip("=")
    return f"artifact_{encoded}"


def decode_artifact_evidence_id(evidence_id: str) -> str | None:
    if not evidence_id.startswith("artifact_"):
        return None
    raw = evidence_id.removeprefix("artifact_")
    padded = raw + "=" * (-len(raw) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None


def _schematic_map(map_payload: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(map_payload)
    out["spatial_representation"] = "schematic"
    out["real_geometry"] = None
    out["geometry_provenance"] = None
    out["real_gis_available"] = False
    out["provenance"] = dict(SCHEMATIC_MAP_PROVENANCE)
    for entity in out.get("entities") or []:
        entity["spatial_representation"] = "schematic"
        entity["real_geometry"] = None
        entity["geometry_provenance"] = None
        reasons = entity.setdefault("uncertainty_reasons", [])
        if "אין גיאומטריית GIS מאומתת" not in reasons:
            reasons.append("אין גיאומטריית GIS מאומתת")
    return out


def _citation_to_evidence(row: dict[str, Any], idx: int, *, ask_payload: dict[str, Any]) -> dict[str, Any] | None:
    artifact_id = str(row.get("chunk_id") or "").strip()
    if not artifact_id:
        return None
    document = row.get("document") if isinstance(row.get("document"), dict) else {}
    header_path = [str(item) for item in row.get("header_path") or [] if str(item).strip()]
    source_type = str(row.get("source_type") or "unknown")
    return {
        "id": encode_artifact_evidence_id(artifact_id),
        "source_type": source_type,
        **source_type_display_fields(source_type),
        "source_title": str(document.get("title") or row.get("citation") or "מקור עירוני"),
        "source_url": _source_url_with_page(_citation_document_url(document), row.get("start_page")),
        "retrieval_artifact_id": artifact_id,
        "artifact_kind": str(row.get("artifact_kind") or "retrieval_chunk"),
        "retrieval_set_id": str((ask_payload.get("retrieval") or {}).get("retrieval_set_id") or ""),
        "header_path": header_path,
        "page_span": {"start": row.get("start_page"), "end": row.get("end_page")},
        "start_offset": None,
        "end_offset": None,
        "bbox": None,
        "text": _citation_text(row=row, idx=idx, ask_payload=ask_payload),
        "confidence_label": "גבוהה" if float(row.get("score") or 0.0) >= 0.6 else "בינונית",
        "extraction_warnings": [],
    }


def _artifact_to_evidence(artifact: RetrievalArtifact, document: Document, version: DocumentVersion, *, retrieval_set_id: str | None) -> dict[str, Any]:
    header_path = _load_header_path(artifact.header_path_json)
    source_type = str(artifact.source_kind or "unknown")
    return {
        "id": encode_artifact_evidence_id(str(artifact.artifact_id)),
        "source_type": source_type,
        **source_type_display_fields(source_type),
        "source_title": str(document.title_he or artifact.title_he or "מקור עירוני"),
        "source_url": _source_url_with_page(f"/document-versions/{int(version.id)}/source.pdf", artifact.start_page),
        "retrieval_artifact_id": str(artifact.artifact_id),
        "artifact_kind": str(artifact.artifact_kind or "retrieval_chunk"),
        "retrieval_set_id": retrieval_set_id,
        "header_path": header_path,
        "page_span": {"start": artifact.start_page, "end": artifact.end_page},
        "start_offset": artifact.start_offset,
        "end_offset": artifact.end_offset,
        "bbox": None,
        "text": str(artifact.body_text or artifact.retrieval_text or ""),
        "confidence_label": "בינונית",
        "extraction_warnings": [],
    }


def _citation_to_decision(row: dict[str, Any], idx: int, evidence: dict[str, Any], *, ask_payload: dict[str, Any]) -> dict[str, Any]:
    answer_sections = ask_payload.get("answer_sections") if isinstance(ask_payload.get("answer_sections"), list) else []
    section = answer_sections[idx - 1] if idx <= len(answer_sections) and isinstance(answer_sections[idx - 1], dict) else {}
    summary = str(section.get("summary") or section.get("text") or ask_payload.get("answer") or evidence["text"] or "מקור תומך").strip()
    title = str(section.get("topic_child") or section.get("topic_root") or evidence.get("source_title") or f"מקור {idx}").strip()
    evidence_ref = str(evidence["id"])
    text_for_classification = " ".join(str(part or "") for part in (title, summary, evidence.get("text")))
    decision_kind = _classify_decision_kind(text_for_classification)
    outcome_status = _classify_outcome_status(text_for_classification)
    legal_effect = _classify_legal_effect(text_for_classification, decision_kind=decision_kind)
    return {
        "id": f"decision_from_evidence_{idx}",
        "title": title[:90],
        "raw_decision_text": summary,
        "summary": summary[:240],
        "primary_time": {
            "start": None,
            "end": None,
            "precision": "unknown",
            "kind": "unknown",
            "label_he": "תאריך לא מזוהה",
            "confidence_label": evidence.get("confidence_label") or "בינונית",
            "evidence_refs": [evidence_ref],
        },
        "time_anchors": [],
        "decision_kind": {**decision_kind, "raw_text": summary},
        "outcome_status": {**outcome_status, "raw_text": summary},
        "legal_effect": legal_effect,
        "topic_ids": [],
        "entity_ids": [],
        "timeline_event_ids": [],
        "evidence_refs": [evidence_ref],
        "resident_evidence_links": [{"label_he": source_type_metadata(evidence.get("source_type")).label_he, "icon": "document-link", "evidence_ref": evidence_ref}],
        "normalized_by": "ask_result_adapter",
        "curation_status": "unreviewed",
        "limitations": [],
    }


def _citation_text(*, row: dict[str, Any], idx: int, ask_payload: dict[str, Any]) -> str:
    claim_assessments = ask_payload.get("claim_assessments") if isinstance(ask_payload.get("claim_assessments"), list) else []
    for claim in claim_assessments:
        if not isinstance(claim, dict):
            continue
        chunk_ids = claim.get("citation_chunk_ids") or claim.get("supporting_chunk_ids") or []
        if row.get("chunk_id") in chunk_ids:
            return str(claim.get("quoted_evidence") or claim.get("text") or claim.get("text_he") or "").strip() or str(ask_payload.get("answer") or "")
    return str(row.get("citation") or ask_payload.get("answer") or f"מקור תומך {idx}")


def _confidence_from_ask(*, status: str, evidence_count: int, ask_payload: dict[str, Any]) -> str:
    if status != "answer":
        return "נמוכה"
    scoring = ask_payload.get("scoring") if isinstance(ask_payload.get("scoring"), dict) else {}
    confidence = scoring.get("confidence")
    if isinstance(confidence, (int, float)):
        if confidence >= 0.8:
            return "גבוהה"
        if confidence >= 0.45:
            return "בינונית"
        return "נמוכה"
    return "גבוהה" if evidence_count >= 2 else "בינונית"


def _classify_decision_kind(text: str) -> dict[str, str]:
    normalized = _normalize_hebrew_text(text)
    specs = [
        ("REQUEST_FOR_INFO", "בקשת מידע", ("נדרש", "נדרשו", "השלמת מסמכים", "בקשת מידע", "להשלים")),
        ("PUBLICATION", "פרסום", ("פרסום", "לפרסם", "הודעה כדין", "שמיעת התנגדויות")),
        ("BUDGET_ALLOCATION", "הקצאת תקציב", ("תקציב", "הקצאה", "הקצאת")),
        ("APPROVAL", "אישור", ("אושר", "אושרה", "אישר", "מאשרים", "הוחלט לאשר", "לאשר")),
        ("REJECTION", "דחייה", ("נדחה", "נדחתה", "דוחים", "לא לאשר")),
        ("DEFERRAL", "דחייה למועד אחר", ("נדחה ל", "יידחה", "יובא לדיון נוסף")),
        ("DISCUSSION", "דיון", ("דיון", "נדון", "נדונה", "הוצג")),
    ]
    for code, label, aliases in specs:
        if any(alias in normalized for alias in aliases):
            return {"code": code, "label_he": label, "vocabulary": "municipal-decision-kind:v1", "confidence_label": "בינונית"}
    return {"code": "UNKNOWN", "label_he": "לא ידוע", "vocabulary": "municipal-decision-kind:v1", "confidence_label": "נמוכה"}


def _classify_outcome_status(text: str) -> dict[str, str]:
    normalized = _normalize_hebrew_text(text)
    if any(alias in normalized for alias in ("בתנאים", "בכפוף", "תנאי")) and any(alias in normalized for alias in ("אושר", "לאשר", "מאשרים")):
        return {"code": "APPROVED_WITH_CONDITIONS", "label_he": "אושר בתנאים", "vocabulary": "municipal-outcome-status:v1", "confidence_label": "בינונית"}
    if any(alias in normalized for alias in ("אושר", "אושרה", "לאשר", "מאשרים", "הוחלט לאשר")):
        return {"code": "APPROVED", "label_he": "אושר", "vocabulary": "municipal-outcome-status:v1", "confidence_label": "בינונית"}
    if any(alias in normalized for alias in ("נדחה", "נדחתה", "לא לאשר")):
        return {"code": "REJECTED", "label_he": "נדחה", "vocabulary": "municipal-outcome-status:v1", "confidence_label": "בינונית"}
    if any(alias in normalized for alias in ("בהמשך טיפול", "נדרש", "להשלים", "ממתין")):
        return {"code": "PENDING", "label_he": "בהמשך טיפול", "vocabulary": "municipal-outcome-status:v1", "confidence_label": "בינונית"}
    if any(alias in normalized for alias in ("נרשם", "הוצג", "דווח")):
        return {"code": "NOTED", "label_he": "נרשם", "vocabulary": "municipal-outcome-status:v1", "confidence_label": "בינונית"}
    if any(alias in normalized for alias in ("דיון", "נדון", "נדונה")):
        return {"code": "NO_FORMAL_OUTCOME", "label_he": "ללא החלטה פורמלית", "vocabulary": "municipal-outcome-status:v1", "confidence_label": "נמוכה"}
    return {"code": "UNKNOWN", "label_he": "לא ידוע", "vocabulary": "municipal-outcome-status:v1", "confidence_label": "נמוכה"}


def _classify_legal_effect(text: str, *, decision_kind: dict[str, str]) -> dict[str, str]:
    normalized = _normalize_hebrew_text(text)
    kind_code = decision_kind.get("code")
    if kind_code in {"APPROVAL", "REJECTION", "BUDGET_ALLOCATION"}:
        return {"code": "BINDING", "label_he": "מחייב", "vocabulary": "municipal-legal-effect:v1", "confidence_label": "בינונית"}
    if kind_code in {"PUBLICATION", "REQUEST_FOR_INFO", "DEFERRAL"}:
        return {"code": "PROCEDURAL", "label_he": "תהליכי", "vocabulary": "municipal-legal-effect:v1", "confidence_label": "בינונית"}
    if any(alias in normalized for alias in ("דיווח", "הוצג", "עדכון", "דיון")):
        return {"code": "INFORMATIONAL", "label_he": "מידעי", "vocabulary": "municipal-legal-effect:v1", "confidence_label": "בינונית"}
    return {"code": "UNKNOWN", "label_he": "לא ידוע", "vocabulary": "municipal-legal-effect:v1", "confidence_label": "נמוכה"}


def _normalize_hebrew_text(text: str) -> str:
    return " ".join(str(text or "").replace("\u200f", " ").replace("\u200e", " ").split())


def _source_url_with_page(source_url: str | None, start_page: Any) -> str | None:
    if not source_url:
        return None
    try:
        page = int(start_page or 0)
    except (TypeError, ValueError):
        return source_url
    if page <= 0 or "#page=" in source_url:
        return source_url
    return f"{source_url}#page={page}"


def _citation_document_url(document: dict[str, Any]) -> str | None:
    url = document.get("url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    version_id = document.get("version_id")
    try:
        normalized_version_id = int(version_id or 0)
    except (TypeError, ValueError):
        normalized_version_id = 0
    if normalized_version_id > 0:
        return f"/document-versions/{normalized_version_id}/source.pdf"
    for key in ("source_url", "original_url"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _category_id_from_filter(value: Any) -> str | None:
    compact = " ".join(str(value or "").split())
    if not compact:
        return None
    labels = {
        "תכנון ובנייה": "planning",
        "תחבורה": "transport",
        "חינוך": "education",
        "רווחה": "welfare",
        "סביבה": "environment",
    }
    return labels.get(compact, compact if compact in set(labels.values()) else None)


def _apply_selected_category(payload: dict[str, Any], selected_category_id: str | None) -> None:
    if not selected_category_id:
        return
    for row in payload.get("start_discovery_panel", {}).get("categories", []):
        row["selected"] = row.get("id") == selected_category_id


def _normalize_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(filters, dict):
        return {}
    out: dict[str, Any] = {}
    defaults = {
        "area": "כל העיר",
        "time_range": "כל השנים",
        "category": "",
        "source_types": "",
        "confidence": "כל הרמות",
    }
    for key, value in filters.items():
        if value in (None, "", [], {}):
            continue
        if key == "source_types" and isinstance(value, list):
            selected = list(dict.fromkeys(" ".join(str(item or "").split()) for item in value))
            selected = [item for item in selected if item]
            if selected:
                out[str(key)] = selected
            continue
        compact = " ".join(str(value).split())
        if compact and compact != defaults.get(key):
            out[str(key)] = compact
    return out


def _selected_time_range_from_filters(filters: dict[str, Any]) -> dict[str, str] | None:
    value = str(filters.get("time_range") or "")
    if value.isdigit() and len(value) == 4:
        return {"start": f"{value}-01-01", "end": f"{value}-12-31"}
    return None


def _source_filter_from_filters(filters: dict[str, Any]) -> list[str]:
    raw = filters.get("source_types")
    values = raw if isinstance(raw, list) else [raw]
    selected: list[str] = []
    for item in values:
        selected.extend(source_type_codes_from_filter_value(str(item or "")))
    if selected:
        return list(dict.fromkeys(selected))
    return []


def _confidence_filter_from_filters(filters: dict[str, Any]) -> str | None:
    value = str(filters.get("confidence") or "")
    if "גבוהה ובינונית" in value:
        return "medium_and_high"
    return None


def _load_header_path(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if str(item).strip()]
