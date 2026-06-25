from __future__ import annotations

import html
import hmac
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Generator
from urllib.parse import unquote, urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import httpx
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, inspect, or_, select

from municipality.chunking import normalize_for_search
from municipality.db import build_engine, build_session_factory
from municipality.embeddings import ChunkEmbeddingService, EmbeddingReranker
from municipality.fetcher import AssetFetcher
from municipality.gis_api import router as gis_router
from municipality.gis_map_context import build_map_context
from municipality.gis_evidence_links import decision_gis_feature_links, link_decisions_to_gis_plans
from municipality.gis_question_resolver import resolve_geo_intent
from municipality.govmap_client import DEFAULT_GOVMAP_API_KEY, DEFAULT_GOVMAP_ORIGIN
from municipality.migrations import apply_all
from municipality.models import (
    ArtifactSemanticLink,
    Decision,
    DecisionCitation,
    DecisionDocumentLink,
    DecisionRequestContext,
    Document,
    DocumentVersion,
    Meeting,
    MeetingDocumentLink,
    PipelineRun,
    PipelineRunStep,
    RetrievalArtifact,
    SemanticAlias,
    SemanticCandidateReject,
    SemanticDocumentRun,
    SemanticMention,
    SemanticNode,
    DecisionSemanticLink,
    SourceSite,
    TopicSubject,
    TopicSubjectQualityReport,
    TopicSubjectRun,
    Vote,
)
from municipality.pipeline import PipelineService
from municipality.processing import ProcessingService
from municipality.rag_arch import RagArchitectureConfig
from municipality.rag_answering import RagAnswerResult, RagAnsweringService, RagCitation, rag_answering_thresholds_snapshot
from municipality.rag_backend import build_embedding_backend, build_search_backend
from municipality.rag_llm import RagLlmClient, RagLlmConfig, build_rag_llm_client
from municipality.rag_observability import (
    audit_sample_rate,
    hash_text,
    log_rag_event,
    new_ask_request_id,
    should_sample_audit,
)
from municipality.rag_retrieval import RagContextChunk, RagRetrievalResult, RagRetrievalService
from municipality.rag_dashboard_adapter import (
    build_dashboard_error_payload,
    build_dashboard_payload_from_ask_result,
    get_dashboard_artifact_evidence,
    validate_dashboard_payload,
)
from municipality.rag_dashboard_contracts import RagDashboardInteractionRequest, RagDashboardQueryRequest
from municipality.rag_dashboard_gis import build_dashboard_gis_buildings_payload, build_dashboard_gis_context_pois_payload, build_dashboard_gis_map_payload, build_dashboard_gis_parcels_payload
from municipality.rag_dashboard_mock import (
    apply_mock_rag_dashboard_interaction,
    get_mock_rag_dashboard_evidence,
    get_mock_rag_dashboard_payload,
)
from municipality.rag_dashboard_ui import render_rag_dashboard_page
from municipality.search import search_thresholds_snapshot
from municipality.semantic_canonicalization import SemanticCanonicalizer
from municipality.subject_browser_ui import render_subject_browser_page


def _default_html_fetcher(url: str) -> str:
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


engine = build_engine()
SessionLocal = build_session_factory(engine)
app = FastAPI(title="Municipality API")
app.include_router(gis_router)


REVERSE_PROXY_SECRET_HEADER = "X-Govmap-Proxy-Secret"


def _public_path_prefix() -> str:
    return os.environ.get("APP_PUBLIC_PATH_PREFIX", "").strip().rstrip("/")


def _strip_public_prefix(path: str) -> str:
    prefix = _public_path_prefix()
    if not prefix:
        return path
    if path == prefix:
        return "/"
    if path.startswith(f"{prefix}/"):
        return path.removeprefix(prefix)
    return path


def _reverse_proxy_secret() -> str:
    return os.environ.get("APP_REVERSE_PROXY_SECRET", "")


def _proxy_secret_matches(request: Request) -> bool:
    secret = _reverse_proxy_secret()
    if not secret:
        return True
    return hmac.compare_digest(request.headers.get(REVERSE_PROXY_SECRET_HEADER, ""), secret)


def _effective_request_origin(request: Request) -> str:
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    host = forwarded_host or request.headers.get("host")
    if forwarded_proto and host:
        proto = forwarded_proto.split(",")[0].strip()
        request_host = host.split(",")[0].strip()
        return f"{proto}://{request_host}".rstrip("/")
    return ""


def _govmap_allowed_iframe_origins() -> tuple[str, ...]:
    configured = os.environ.get("GOVMAP_ALLOWED_IFRAME_ORIGINS", "")
    if configured.strip():
        return tuple(part.strip().rstrip("/") for part in configured.split(",") if part.strip())
    origin = (os.environ.get("GOVMAP_ORIGIN") or DEFAULT_GOVMAP_ORIGIN).rstrip("/")
    parsed = urlparse(origin)
    dev_origin = f"{parsed.scheme}://dev.{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    return tuple(dict.fromkeys(part for part in (origin, dev_origin) if part))


@app.middleware("http")
async def strip_public_path_prefix_middleware(request: Request, call_next):
    prefix = _public_path_prefix()
    if prefix and request.scope.get("path", "").startswith(prefix):
        stripped_path = _strip_public_prefix(str(request.scope.get("path") or ""))
        if stripped_path != "/health" and not _proxy_secret_matches(request):
            return JSONResponse(
                {"error": "forbidden", "message": "Reverse proxy header is required."},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        request.scope["root_path"] = prefix
        request.scope["path"] = stripped_path
    return await call_next(request)

TOPIC_SEMANTIC_CANONICALIZER = SemanticCanonicalizer()
PDF_FIRST_ASK_SOURCE_TYPES = ["pdf_first_protocol", "pdf_first_attachment", "pdf_first_v4_protocol", "pdf_first_v4_attachment"]
PDF_FIRST_PROTOCOL_SOURCE_TYPES = {"pdf_first_protocol", "pdf_first_v4_protocol"}
PDF_FIRST_ATTACHMENT_SOURCE_TYPES = {"pdf_first_attachment", "pdf_first_v4_attachment"}
PDF_FIRST_RETRIEVAL_ARTIFACT_KINDS = {"pdf_first_retrieval_chunk", "pdf_first_v4_retrieval_chunk"}
MUNICIPALITY_CODE_TO_SLUG = {
    "0070": "ashdod",
    "3000": "jerusalem",
    "4000": "haifa",
    "5000": "tel_aviv",
    "9000": "beer_sheva",
    "0831": "yeruham",
}
TOPIC_SUBJECT_DISPLAY_RULES = {
    ("שאילתה", ""): ("council_inquiry", "שאילתות מועצה", None),
    ("מענה לשאילתה", ""): ("council_inquiry_response", "מענים לשאילתות", None),
    ("בקשה", "בקשת מידע"): ("request_information", "שאילתות מועצה", None),
    ("בקשה", "בקשת אישור"): ("request_approval", "בקשות אישור", None),
    ("בקשה", "בקשת דיון"): ("request_discussion", "בקשות דיון", None),
    ("בקשה", "בקשה להאצלת סמכויות"): ("request_approval", "בקשות אישור", "האצלת סמכויות"),
    ("הצעה לסדר יום", ""): ("agenda_proposal", "הצעות לסדר יום", None),
    ("אישור החלטה", ""): ("decision_approval", "אישורי החלטות", None),
    ("אישור החלטה", "אישור החלטת ועדה"): ("decision_approval", "אישורי החלטות", "החלטת ועדה"),
    ("אישור פרוטוקול", ""): ("protocol_approval", "אישורי פרוטוקולים", None),
    ("המלצה", ""): ("committee_recommendation", "המלצות ועדה", None),
    ("דיווח", ""): ("report_review", "דיווחים וסקירות", None),
    ("דיווח", "סקירה"): ("report_review", "דיווחים וסקירות", "סקירה"),
    ("הנחיה", "הנחיה לפעול"): ("follow_up_instruction", "הנחיות / משימות המשך", "פעולה"),
    ("הנחיה", "הנחיה לתיאום מפגש"): ("follow_up_instruction", "הנחיות / משימות המשך", "תיאום מפגש"),
}
TOPIC_SUBJECT_DISPLAY_ORDER = {
    "council_inquiry": 8,
    "council_inquiry_response": 9,
    "request_information": 10,
    "request_approval": 20,
    "request_discussion": 30,
    "agenda_proposal": 40,
    "decision_approval": 50,
    "protocol_approval": 60,
    "committee_recommendation": 70,
    "report_review": 80,
    "follow_up_instruction": 90,
}
TOPIC_SUBJECT_ENGLISH_LABELS = {
    ("שאילתה", ""): "Council inquiry",
    ("מענה לשאילתה", ""): "Response to council inquiry",
    ("בקשה", "בקשת מידע"): "Council inquiry",
    ("בקשה", "בקשת אישור"): "Request for approval",
    ("בקשה", "בקשת דיון"): "Request for discussion",
    ("בקשה", "בקשה להאצלת סמכויות"): "Request to delegate signing authority",
    ("הצעה לסדר יום", ""): "Agenda proposal",
    ("אישור החלטה", ""): "Decision approval",
    ("אישור החלטה", "אישור החלטת ועדה"): "Committee decision approval",
    ("אישור פרוטוקול", ""): "Protocol approval",
    ("המלצה", ""): "Committee recommendation",
    ("דיווח", ""): "Report",
    ("דיווח", "סקירה"): "Review report",
    ("הנחיה", "הנחיה לפעול"): "Follow-up instruction",
    ("הנחיה", "הנחיה לתיאום מפגש"): "Instruction to coordinate a meeting",
}


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _default_fallback_metadata() -> dict:
    return {
        "fallback_used": False,
        "fallback_reason": None,
        "fallback_provider": None,
        "fallback_model": None,
        "fallback_invoked_at": None,
        "fallback_validation_status": None,
        "fallback_validation_reasons": [],
    }


def _fallback_metadata_from_json(metadata_json: str | None) -> dict:
    metadata = _default_fallback_metadata()
    if not metadata_json:
        return metadata
    try:
        payload = json.loads(metadata_json)
    except json.JSONDecodeError:
        return metadata
    if not isinstance(payload, dict):
        return metadata
    merged = dict(payload)
    for key, value in metadata.items():
        merged.setdefault(key, value)
    metadata = merged
    if not isinstance(metadata["fallback_validation_reasons"], list):
        metadata["fallback_validation_reasons"] = []
    if "api_model_reasons" in metadata and not isinstance(metadata["api_model_reasons"], list):
        metadata["api_model_reasons"] = []
    return metadata


def _loads_json(value: str | None) -> dict | list | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, (dict, list)):
        return payload
    return None


def _semantic_node_payload(
    node: SemanticNode,
    *,
    child_count: int = 0,
    child_ids: list[int] | None = None,
    sibling_ids: list[int] | None = None,
    path_ids: list[int] | None = None,
    sort_order: int | None = None,
) -> dict:
    metadata = _loads_json(node.metadata_json)
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    return {
        "id": node.id,
        "label": node.pref_label_he,
        "label_he": node.pref_label_he,
        "pref_label_he": node.pref_label_he,
        "label_norm": node.pref_label_norm,
        "kind": node.node_kind,
        "semantic_type": node.semantic_type,
        "depth": node.depth,
        "parent_id": node.parent_node_id,
        "child_ids": child_ids or [],
        "sibling_ids": sibling_ids or [],
        "path_ids": path_ids or [node.id],
        "sort_order": sort_order if sort_order is not None else 0,
        "generated_label": node.pref_label_he,
        "curated_label": metadata_dict.get("curated_label"),
        "origin": metadata_dict.get("provenance") or metadata_dict.get("origin"),
        "curation_status": metadata_dict.get("curation_status") or ("approved" if node.status == "active" else node.status),
        "hidden_from_public": bool(metadata_dict.get("hidden_from_public") or node.status != "active"),
        "category_ids": [metadata_dict.get("root_topic_id")] if metadata_dict.get("root_topic_id") else [],
        "primary_category_id": metadata_dict.get("root_topic_id"),
        "specificity_score": node.specificity_score,
        "confidence": node.confidence,
        "support_count": node.support_count,
        "mention_count": node.support_count,
        "decision_count": 0,
        "recent_activity_at": None,
        "time_rollup": {},
        "decision_rollups": {},
        "evidence_refs": [],
        "status": node.status,
        "child_count": child_count,
    }


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    model_name: str | None = None
    muni: str | None = None
    top_k: int = Field(default=8, ge=1, le=50)
    year: int | None = None
    topic: str | None = None
    semantic_node_id: int | None = None
    semantic_label: str | None = None
    semantic_mode: str = "off"
    retrieval_strategy: str = "auto"
    document_id: int | None = Field(default=None, ge=1)
    document_version_id: int | None = Field(default=None, ge=1)
    pipeline_run_id: str | None = None
    debug_mode: bool = False


def _ask_effective_scope(request: AskRequest, *, db=None) -> dict[str, Any]:
    """Force /ask onto the accepted PDF-first pipeline artifacts only."""
    allowed_docvers = _pdf_first_allowed_document_version_ids()
    requested_docver = int(request.document_version_id) if request.document_version_id else None
    requested_doc_id = int(request.document_id) if request.document_id else None
    document_version_ids = list(allowed_docvers) if allowed_docvers is not None else None
    scope_reason = "env_allowed_document_versions" if allowed_docvers is not None else "all_pdf_first_document_versions"
    date_scope = _empty_pdf_first_date_scope_debug(question=request.question)

    if requested_docver is not None:
        if allowed_docvers is None or requested_docver in allowed_docvers:
            document_version_ids = [requested_docver]
            scope_reason = "request_document_version_id"
        else:
            document_version_ids = []
            scope_reason = "request_document_version_id_outside_allowed_scope"
    elif db is not None:
        date_scope = _resolve_pdf_first_date_scope(
            db=db,
            request=request,
            allowed_document_version_ids=allowed_docvers,
        )
        if date_scope["applied"]:
            document_version_ids = list(date_scope["document_version_ids"])
            scope_reason = "question_date_match"
        elif date_scope.get("extracted_dates") and date_scope.get("reason") == "no_matching_pdf_first_document":
            document_version_ids = []
            scope_reason = "question_date_no_match"

    return {
        "source_types": list(PDF_FIRST_ASK_SOURCE_TYPES),
        "required_source_types": list(PDF_FIRST_ASK_SOURCE_TYPES),
        "document_ids": [requested_doc_id] if requested_doc_id is not None else None,
        "document_version_ids": document_version_ids,
        "forced_pdf_first_scope": True,
        "scope_reason": scope_reason,
        "date_scope": date_scope,
    }


NUMERIC_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})(?!\d)")


def _empty_pdf_first_date_scope_debug(*, question: str) -> dict[str, Any]:
    extracted = _extract_numeric_date_specs(question)
    return {
        "applied": False,
        "reason": "no_numeric_date" if not extracted else "not_resolved",
        "extracted_dates": [date["iso"] for date in extracted],
        "document_version_ids": [],
        "candidates": [],
    }


def _extract_numeric_date_specs(value: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in NUMERIC_DATE_RE.finditer(value or ""):
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))
        if year < 100:
            year += 2000
        if not (1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2100):
            continue
        iso = f"{year:04d}-{month:02d}-{day:02d}"
        if iso in seen:
            continue
        seen.add(iso)
        yy = year % 100
        variants = set()
        for sep in (".", "/", "-"):
            variants.update(
                {
                    f"{day}{sep}{month}{sep}{year}",
                    f"{day:02d}{sep}{month:02d}{sep}{year}",
                    f"{day}{sep}{month}{sep}{yy:02d}",
                    f"{day:02d}{sep}{month:02d}{sep}{yy:02d}",
                }
            )
        out.append({"iso": iso, "day": day, "month": month, "year": year, "variants": sorted(variants, key=len, reverse=True)})
    return out


def _resolve_pdf_first_date_scope(
    *,
    db,
    request: AskRequest,
    allowed_document_version_ids: list[int] | None,
) -> dict[str, Any]:
    extracted = _extract_numeric_date_specs(request.question)
    if not extracted:
        return _empty_pdf_first_date_scope_debug(question=request.question)

    stmt = (
        select(
            RetrievalArtifact.document_version_id,
            RetrievalArtifact.document_id,
            RetrievalArtifact.source_kind,
            Document.title_he,
            Document.canonical_url,
            DocumentVersion.storage_uri,
            func.group_concat(func.distinct(RetrievalArtifact.meeting_date)),
        )
        .select_from(RetrievalArtifact)
        .join(Document, RetrievalArtifact.document_id == Document.id)
        .join(DocumentVersion, RetrievalArtifact.document_version_id == DocumentVersion.id)
        .join(SourceSite, Document.source_site_id == SourceSite.id)
        .where(RetrievalArtifact.source_kind.in_(PDF_FIRST_ASK_SOURCE_TYPES))
        .group_by(
            RetrievalArtifact.document_version_id,
            RetrievalArtifact.document_id,
            RetrievalArtifact.source_kind,
            Document.title_he,
            Document.canonical_url,
            DocumentVersion.storage_uri,
        )
    )
    if request.muni:
        stmt = stmt.where(SourceSite.municipality_slug == request.muni)
    if request.document_id:
        stmt = stmt.where(RetrievalArtifact.document_id == int(request.document_id))
    if allowed_document_version_ids is not None:
        stmt = stmt.where(RetrievalArtifact.document_version_id.in_(allowed_document_version_ids))

    candidates = []
    matched_docvers: dict[int, dict[str, Any]] = {}
    for row in db.execute(stmt).all():
        document_version_id, document_id, source_kind, title, canonical_url, storage_uri, meeting_dates = row
        searchable_document_text = " ".join(str(part or "") for part in (title, canonical_url, storage_uri))
        searchable_meeting_dates = str(meeting_dates or "")
        reasons: list[str] = []
        matched_iso = None
        for date_spec in extracted:
            variants = list(date_spec["variants"])
            if any(variant in searchable_document_text for variant in variants):
                reasons.append("document_metadata_date_match")
                matched_iso = date_spec["iso"]
            if _date_text_matches_iso(searchable_meeting_dates, date_spec["iso"]):
                reasons.append("artifact_meeting_date_match")
                matched_iso = date_spec["iso"]
        if not reasons:
            continue
        docver_id = int(document_version_id)
        candidate = matched_docvers.setdefault(
            docver_id,
            {
                "document_version_id": docver_id,
                "document_id": int(document_id),
                "source_types": [],
                "matched_date": matched_iso,
                "reasons": [],
                "title": str(title or ""),
            },
        )
        candidate["source_types"].append(str(source_kind))
        for reason in reasons:
            if reason not in candidate["reasons"]:
                candidate["reasons"].append(reason)

    candidates = sorted(matched_docvers.values(), key=lambda item: item["document_version_id"])
    metadata_candidates = [
        candidate for candidate in candidates if "document_metadata_date_match" in set(candidate.get("reasons") or [])
    ]
    if metadata_candidates:
        candidates = metadata_candidates
    if not candidates:
        return {
            "applied": False,
            "reason": "no_matching_pdf_first_document",
            "extracted_dates": [date["iso"] for date in extracted],
            "document_version_ids": [],
            "candidates": [],
        }

    matched_ids = [int(candidate["document_version_id"]) for candidate in candidates]
    return {
        "applied": True,
        "reason": "matched_pdf_first_document_date",
        "extracted_dates": [date["iso"] for date in extracted],
        "document_version_ids": matched_ids,
        "candidates": candidates,
    }


def _date_text_matches_iso(value: str, iso: str) -> bool:
    return any(date_spec["iso"] == iso for date_spec in _extract_numeric_date_specs(value))


def _ask_retrieval_query(*, request: AskRequest, effective_scope: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    date_scope = effective_scope.get("date_scope") if isinstance(effective_scope.get("date_scope"), dict) else {}
    if not bool(date_scope.get("applied")) or not _is_meeting_overview_question(request.question):
        return request.question, {"expanded": False, "reason": None, "query": request.question}
    expanded_query = f"{request.question} נושאים לדיון החלטות סעיפים"
    return expanded_query, {"expanded": True, "reason": "date_scoped_meeting_overview", "query": expanded_query}


def _is_meeting_overview_question(question: str) -> bool:
    normalized = normalize_for_search(question)
    if not normalized:
        return False
    has_overview_marker = any(marker in normalized for marker in ("נדון", "נדונו", "נידון", "נושאים", "סעיפים", "החלטות"))
    has_meeting_marker = any(marker in normalized for marker in ("ישיבה", "ישיבת", "מועצה", "פרוטוקול"))
    return has_overview_marker and has_meeting_marker


def _enrich_pdf_first_reference_contexts(
    *,
    db,
    contexts: list[RagContextChunk],
    municipality_slug: str | None,
    allowed_document_version_ids: list[int] | None,
) -> tuple[list[RagContextChunk], dict[str, Any]]:
    if not contexts:
        return contexts, {"applied": False, "enrichment_map": {}, "candidate_count": 0, "linked_count": 0}

    context_ids = {context.chunk_id for context in contexts}
    enriched_contexts = list(contexts)
    enrichment_map: dict[str, list[str]] = {}
    candidate_count = 0
    for context in contexts:
        if context.source_kind not in PDF_FIRST_PROTOCOL_SOURCE_TYPES:
            continue
        specs = _pdf_first_reference_specs_for_context(context)
        if not specs:
            continue
        for spec in specs[:3]:
            matches = _find_pdf_first_reference_matches(
                db=db,
                spec=spec,
                protocol_context=context,
                municipality_slug=municipality_slug or context.municipality_slug,
                allowed_document_version_ids=allowed_document_version_ids,
            )
            candidate_count += len(matches)
            if not matches:
                continue
            best = matches[0]
            if best.chunk_id in context_ids:
                continue
            context_ids.add(best.chunk_id)
            enriched_contexts.append(best)
            enrichment_map.setdefault(context.chunk_id, []).append(best.chunk_id)
            if len(enrichment_map.get(context.chunk_id, [])) >= 5:
                break

    return enriched_contexts, {
        "applied": bool(enrichment_map),
        "enrichment_map": enrichment_map,
        "candidate_count": candidate_count,
        "linked_count": sum(len(values) for values in enrichment_map.values()),
    }


def _pdf_first_reference_specs_for_context(context: RagContextChunk) -> list[dict[str, Any]]:
    text = _api_pdf_first_raw_text(context.chunk_text or context.snippet)
    if not text:
        return []
    compact = " ".join(text.split())
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    protocol_pattern = re.compile(
        r"פרוטוקול\s+מישיבת\s+(.{3,120}?)(?=\s+מס\s*\d|\s+מס\d|\s+מיום\s*\d|[.;\n]|$)"
    )
    for match in protocol_pattern.finditer(compact):
        start = match.start()
        tail = compact[start : start + 180]
        label = _api_clean_pdf_first_reference_label(match.group(1))
        if not label:
            continue
        number_match = re.search(r"מס\s*([0-9]+(?:/[0-9]+)?)", tail)
        date_match = re.search(r"מיום\s*([0-9]{1,2}[./-][0-9]{1,2}[./-][0-9]{2,4})", tail)
        key = f"protocol:{_api_topic_equivalence_key(label)}:{number_match.group(1) if number_match else ''}:{date_match.group(1) if date_match else ''}"
        if key in seen:
            continue
        seen.add(key)
        specs.append(
            {
                "kind": "committee_protocol",
                "label": label,
                "number": number_match.group(1) if number_match else None,
                "date": date_match.group(1) if date_match else None,
                "requires_disambiguator": True,
            }
        )

    agreement_pattern = re.compile(
        r"((?:הסכם|אישור\s+הסכם|דיון\s+חוזר\s+לאישור\s+הסכם)[^.;\n]{8,160}?)(?=\s+מצ\"?ל|\s+\*|[.;\n]|$)"
    )
    for match in agreement_pattern.finditer(compact):
        label = _api_clean_pdf_first_reference_label(match.group(1))
        if not label:
            continue
        key = f"agreement:{_api_topic_equivalence_key(label)}"
        if key in seen:
            continue
        seen.add(key)
        specs.append({"kind": "agreement", "label": label, "number": None, "date": None, "requires_disambiguator": False})
    question_pattern = re.compile(
        r"שאיל(?:תא|תה)\s+[^.;\n]{0,120}?בנושא\s+[\"'׳״”]*([^\"'׳״”();.\n]{3,120})"
    )
    for match in question_pattern.finditer(compact):
        start = match.start()
        tail = compact[start : start + 180]
        label = _api_clean_pdf_first_reference_label(match.group(1))
        if not label:
            continue
        date_match = re.search(r"[(:\-–]\s*([0-9]{1,2}[./-][0-9]{1,2}[./-][0-9]{2,4})\s*[):\-–]", tail)
        requester_match = re.search(r"שאיל(?:תא|תה)\s+של\s+([^\"'׳״”]{2,60}?)(?=\s+בנושא)", tail)
        requester = _api_clean_pdf_first_reference_label(requester_match.group(1)) if requester_match else None
        key = f"question:{_api_topic_equivalence_key(label)}:{_api_topic_equivalence_key(requester)}:{date_match.group(1) if date_match else ''}"
        if key in seen:
            continue
        seen.add(key)
        specs.append(
            {
                "kind": "question",
                "label": label,
                "number": None,
                "date": date_match.group(1) if date_match else None,
                "requester": requester,
                "requires_disambiguator": False,
            }
        )
    return specs


def _find_pdf_first_reference_matches(
    *,
    db,
    spec: dict[str, Any],
    protocol_context: RagContextChunk,
    municipality_slug: str | None,
    allowed_document_version_ids: list[int] | None,
) -> list[RagContextChunk]:
    label = str(spec.get("label") or "").strip()
    label_tokens = _api_topic_tokens(label)
    if len(label_tokens) < 2:
        return []
    strongest_tokens = sorted(label_tokens, key=len, reverse=True)[:3]
    stmt = (
        select(RetrievalArtifact, Document, DocumentVersion, SourceSite)
        .join(Document, RetrievalArtifact.document_id == Document.id)
        .join(DocumentVersion, RetrievalArtifact.document_version_id == DocumentVersion.id)
        .join(SourceSite, Document.source_site_id == SourceSite.id)
        .where(RetrievalArtifact.source_kind.in_(PDF_FIRST_ASK_SOURCE_TYPES))
        .where(RetrievalArtifact.artifact_kind.in_(PDF_FIRST_RETRIEVAL_ARTIFACT_KINDS))
        .where(RetrievalArtifact.artifact_id != protocol_context.chunk_id)
        .where(
            or_(
                RetrievalArtifact.document_version_id == int(protocol_context.document_version_id or 0),
                RetrievalArtifact.source_kind.in_(PDF_FIRST_ATTACHMENT_SOURCE_TYPES),
            )
        )
    )
    for token in strongest_tokens[:2]:
        stmt = stmt.where(RetrievalArtifact.retrieval_text.like(f"%{token}%"))
    if municipality_slug:
        stmt = stmt.where(SourceSite.municipality_slug == municipality_slug)
    if allowed_document_version_ids is not None:
        stmt = stmt.where(RetrievalArtifact.document_version_id.in_(allowed_document_version_ids))
    rows = db.execute(stmt.limit(50)).all()

    scored: list[tuple[float, RagContextChunk]] = []
    for artifact, document, version, site in rows:
        candidate_text = " ".join(
            str(part or "")
            for part in (artifact.title_he, artifact.body_text, artifact.retrieval_text, document.title_he, document.canonical_url)
        )
        score = _pdf_first_reference_match_score(
            spec=spec,
            candidate_text=candidate_text,
            source_kind=str(artifact.source_kind),
            same_document_version=artifact.document_version_id == protocol_context.document_version_id,
        )
        if score < 2.5:
            continue
        scored.append((score, _api_retrieval_artifact_to_context(artifact, document, version, site, score=score)))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [context for _, context in scored[:3]]


def _pdf_first_reference_match_score(
    *,
    spec: dict[str, Any],
    candidate_text: str,
    source_kind: str,
    same_document_version: bool,
) -> float:
    label = str(spec.get("label") or "")
    label_key = _api_topic_equivalence_key(label)
    candidate_key = _api_topic_equivalence_key(candidate_text)
    label_tokens = set(_api_topic_tokens(label_key))
    if not label_tokens or not candidate_key:
        return 0.0
    overlap = {token for token in label_tokens if token in candidate_key}
    required_overlap = min(3, max(2, len(label_tokens) // 2))
    if label_key not in candidate_key and len(overlap) < required_overlap:
        return 0.0

    score = float(len(overlap))
    number = str(spec.get("number") or "").strip()
    date = str(spec.get("date") or "").strip()
    has_disambiguator = False
    if number:
        normalized_number = normalize_for_search(number)
        if normalized_number and normalized_number in candidate_key:
            score += 1.5
            has_disambiguator = True
    if date:
        date_specs = _extract_numeric_date_specs(date)
        if any(_date_text_matches_iso(candidate_text, date_spec["iso"]) for date_spec in date_specs):
            score += 1.5
            has_disambiguator = True
    requester = str(spec.get("requester") or "").strip()
    if requester:
        requester_key = _api_topic_equivalence_key(requester)
        if requester_key and requester_key in candidate_key:
            score += 1.0
            has_disambiguator = True
    if bool(spec.get("requires_disambiguator")) and not has_disambiguator:
        return 0.0
    if str(spec.get("kind") or "") == "agreement" and label_key in candidate_key:
        score += 0.75
    if str(spec.get("kind") or "") == "question":
        if label_key in candidate_key:
            score += 1.0
        if any(term in candidate_key for term in ("תשובת ראש העיר", "השאלה והתשובה", "מקריאה את תשובת", "לשאילתא", "לשאילתה")):
            score += 1.0
    if not _pdf_first_reference_candidate_has_detail_evidence(kind=str(spec.get("kind") or ""), candidate_text=candidate_text):
        return 0.0
    if source_kind in PDF_FIRST_ATTACHMENT_SOURCE_TYPES:
        score += 0.35
    elif same_document_version:
        score += 0.2
    normalized_candidate = normalize_for_search(candidate_text)
    if "summary_he" in candidate_text:
        score += 1.0
    if len(normalized_candidate) > 450:
        score += 0.75
    if "מצ ל" in normalized_candidate and len(normalized_candidate) < 280:
        score -= 0.75
    return score


def _pdf_first_reference_candidate_has_detail_evidence(*, kind: str, candidate_text: str) -> bool:
    normalized = normalize_for_search(candidate_text)
    if not normalized:
        return False
    detail_markers = (
        "מחליטים",
        "הצביעו",
        "ברוב קולות",
        "פה אחד",
        "מקריאה את תשובת",
        "תשובת ראש העיר",
        "השאלה והתשובה",
        "מדובר באישור",
        "עיקרי ההסכם",
        "מבקשת שימוש",
        "האם ",
    )
    if any(marker in normalized for marker in detail_markers):
        return True
    if kind == "agreement" and len(normalized) >= 450 and "הסכם" in normalized:
        return True
    reference_marker_count = normalized.count("מצ ל") + normalized.count("פרוטוקול מישיבת")
    if reference_marker_count >= 2:
        return False
    if "מצ ל" in normalized and len(normalized) < 350:
        return False
    return len(normalized) >= 500


def _api_retrieval_artifact_to_context(
    artifact: RetrievalArtifact,
    document: Document,
    version: DocumentVersion,
    site: SourceSite,
    *,
    score: float,
) -> RagContextChunk:
    try:
        section_path = json.loads(artifact.header_path_json or "[]")
    except json.JSONDecodeError:
        section_path = []
    if not isinstance(section_path, list):
        section_path = []
    return RagContextChunk(
        chunk_id=str(artifact.artifact_id),
        score=float(score),
        snippet=str(artifact.body_text or artifact.retrieval_text or ""),
        citation=artifact.citation_label,
        source_kind=str(artifact.source_kind),
        document_id=int(artifact.document_id),
        document_version_id=int(artifact.document_version_id),
        document_title=str(document.title_he or ""),
        document_url=f"/document-versions/{int(version.id)}/source.pdf",
        municipality_slug=str(site.municipality_slug or ""),
        start_offset=artifact.start_offset,
        end_offset=artifact.end_offset,
        start_page=artifact.start_page,
        end_page=artifact.end_page,
        chunk_index=artifact.ordinal,
        chunk_text=str(artifact.retrieval_text or artifact.body_text or ""),
        primary_topic=str(artifact.title_he or "") or None,
        secondary_topics=[],
        section_path=[str(item) for item in section_path if str(item).strip()],
        artifact_kind=artifact.artifact_kind,
    )


def _api_pdf_first_raw_text(value: str) -> str:
    text = str(value or "")
    marker = "raw_pdf_text:"
    if marker not in text:
        return " ".join(text.split()).strip()
    return " ".join(text.split(marker, 1)[1].split()).strip()


def _api_clean_pdf_first_reference_label(value: str | None) -> str | None:
    label = " ".join(str(value or "").replace("‫", " ").replace("‬", " ").split()).strip()
    if not label:
        return None
    label = re.sub(r"^סעיף\s*\d+\s*[:.)-]*\s*", "", label).strip()
    label = re.sub(r"^[\d\s'.:()\-–]+", "", label).strip()
    label = re.sub(r"^פרוטוקול\s+מישיבת\s+", "", label).strip()
    label = re.sub(r"^מישיבת\s+", "", label).strip()
    label = re.sub(r"^פרוטוקול\s+", "", label).strip()
    label = re.sub(r"^אישור\s+(?=הסכם\b)", "", label).strip()
    label = re.split(r"\s+מס\s*\d|\s+מס\d|\s+מיום\s*\d|\s+מחליטים\b|\s+מאשרים\b", label, maxsplit=1)[0].strip()
    label = re.split(r"\s+[–-]\s+", label, maxsplit=1)[0].strip()
    label = label.strip(" .,:;()[]{}\"'׳״-–")
    label = re.sub(r"\s+", " ", label).strip()
    label = re.sub(r"\s+\d+$", "", label).strip()
    label = re.sub(r"\s+[\u0590-\u05FF]$", "", label).strip()
    return label if len(_api_topic_tokens(label)) >= 2 else None


def _api_topic_equivalence_key(value: str | None) -> str:
    label = _api_clean_pdf_first_reference_label(value) or str(value or "")
    label = label.replace("'", " ").replace("׳", " ").replace("\"", " ").replace("״", " ")
    return normalize_for_search(re.sub(r"[^\u0590-\u05FF0-9/.-]+", " ", label))


def _api_topic_tokens(value: str | None) -> list[str]:
    return [token for token in re.findall(r"[\u0590-\u05FF]{2,}", str(value or "")) if len(token) >= 2]


def _pdf_first_allowed_document_version_ids() -> list[int] | None:
    return _env_int_list("RAG_ASK_DOCUMENT_VERSION_IDS") or None


def _env_int_list(name: str) -> list[int]:
    raw = os.getenv(name, "")
    values = []
    seen = set()
    for part in raw.split(","):
        try:
            value = int(part.strip())
        except ValueError:
            continue
        if value <= 0 or value in seen:
            continue
        values.append(value)
        seen.add(value)
    return values


def _active_embedding_cache_summary(*, db) -> dict[str, Any]:
    embedding_service = build_embedding_backend(session=db)
    model_client = embedding_service.model_client
    model_provider = model_client.provider_name
    model_name = model_client.model_name
    dimensions = model_client.dimensions

    from municipality.models import RetrievalArtifact, RetrievalArtifactEmbedding

    total_chunks = int(db.execute(select(func.count(RetrievalArtifact.id))).scalar_one() or 0)
    embedded_chunks = int(
        db.execute(
            select(func.count(func.distinct(RetrievalArtifactEmbedding.artifact_id))).where(
                RetrievalArtifactEmbedding.model_provider == model_provider,
                RetrievalArtifactEmbedding.model_name == model_name,
                RetrievalArtifactEmbedding.dimensions == dimensions,
            )
        ).scalar_one()
        or 0
    )

    by_source_rows = db.execute(
        select(
            RetrievalArtifact.source_kind,
            func.count(func.distinct(RetrievalArtifact.artifact_id)),
            func.count(func.distinct(RetrievalArtifactEmbedding.artifact_id)),
        )
        .select_from(RetrievalArtifact)
        .outerjoin(
            RetrievalArtifactEmbedding,
            and_(
                RetrievalArtifactEmbedding.artifact_id == RetrievalArtifact.artifact_id,
                RetrievalArtifactEmbedding.model_provider == model_provider,
                RetrievalArtifactEmbedding.model_name == model_name,
                RetrievalArtifactEmbedding.dimensions == dimensions,
            ),
        )
        .group_by(RetrievalArtifact.source_kind)
        .order_by(RetrievalArtifact.source_kind.asc())
    ).all()
    count_label = "total_artifacts"
    embedded_label = "embedded_artifacts"
    missing_label = "missing_artifacts"

    by_source_kind = []
    for source_kind, source_total, source_embedded in by_source_rows:
        total_value = int(source_total or 0)
        embedded_value = int(source_embedded or 0)
        missing_value = max(0, total_value - embedded_value)
        row_payload = {
            "source_type": str(source_kind or "unknown"),
            count_label: total_value,
            embedded_label: embedded_value,
            missing_label: missing_value,
            "coverage_rate": round((embedded_value / total_value), 4) if total_value else 0.0,
        }
        by_source_kind.append(row_payload)

    missing_chunks = max(0, total_chunks - embedded_chunks)
    payload = {
        "enabled": embedding_service.is_enabled(),
        "architecture_version": RagArchitectureConfig.from_env().version,
        "index_kind": "artifact",
        "provider": model_provider,
        "model": model_name,
        "dimensions": dimensions,
        count_label: total_chunks,
        embedded_label: embedded_chunks,
        missing_label: missing_chunks,
        "coverage_rate": round((embedded_chunks / total_chunks), 4) if total_chunks else 0.0,
        "by_source_type": by_source_kind,
    }
    payload.setdefault("total_chunks", 0)
    payload.setdefault("embedded_chunks", 0)
    payload.setdefault("missing_chunks", 0)
    return payload


def _rerank_debug_summary(*, retrieval_result: RagRetrievalResult) -> dict[str, Any]:
    retrieval_trace = dict(retrieval_result.debug_info)
    lexical_top_k_chunk_ids = [
        str(chunk_id)
        for chunk_id in retrieval_trace.get("lexical_top_k_chunk_ids", [])
        if str(chunk_id).strip()
    ]
    reranked_top_k_chunk_ids = [
        str(chunk_id)
        for chunk_id in retrieval_trace.get("reranked_top_k_chunk_ids", [])
        if str(chunk_id).strip()
    ]
    embedding_rerank = retrieval_trace.get("embedding_rerank")
    if not isinstance(embedding_rerank, dict):
        embedding_rerank = {"enabled": False}

    lexical_top_k_set = set(lexical_top_k_chunk_ids)
    overlap_count = sum(1 for chunk_id in reranked_top_k_chunk_ids if chunk_id in lexical_top_k_set)
    changed_count = sum(
        1
        for index, chunk_id in enumerate(reranked_top_k_chunk_ids)
        if index >= len(lexical_top_k_chunk_ids) or lexical_top_k_chunk_ids[index] != chunk_id
    )
    top_k_count = max(len(reranked_top_k_chunk_ids), 1)
    candidate_count = int(embedding_rerank.get("candidate_count") or 0)
    available_chunk_embeddings = int(embedding_rerank.get("available_chunk_embeddings") or 0)

    return {
        "candidate_limit": int(retrieval_trace.get("candidate_limit") or 0),
        "broad_decision_query": bool(retrieval_trace.get("broad_decision_query") is True),
        "decision_match_count": int(retrieval_trace.get("decision_match_count") or 0),
        "top_decision_matches": list(retrieval_trace.get("top_decision_matches") or []),
        "lexical_top_k_chunk_ids": lexical_top_k_chunk_ids,
        "reranked_top_k_chunk_ids": reranked_top_k_chunk_ids,
        "overlap_count": overlap_count,
        "changed_count": changed_count,
        "lexical_top_k_hit_rate": round((overlap_count / top_k_count), 4),
        "candidate_embedding_hit_rate": round((available_chunk_embeddings / max(candidate_count, 1)), 4)
        if candidate_count
        else 0.0,
        "embedding_rerank": embedding_rerank,
    }


def _collapse_duplicate_answering_contexts(
    *,
    embedding_service: ChunkEmbeddingService,
    question: str,
    contexts: list[RagContextChunk],
) -> tuple[list[RagContextChunk], dict[str, Any]]:
    stats = {"enabled": embedding_service.is_enabled(), "before_count": len(contexts), "after_count": len(contexts), "dropped_chunk_ids": []}
    if len(contexts) <= 2 or not embedding_service.is_enabled():
        return contexts, stats
    query_vector = embedding_service.embed_query(question, query_kind="answer_context_dedupe")
    if not query_vector:
        return contexts, stats
    lookup = embedding_service.ensure_embeddings_for_hits(hits=contexts)
    vectors_by_chunk = lookup.vectors_by_chunk_id
    if not vectors_by_chunk:
        return contexts, stats
    threshold = 0.965
    ranked_contexts = sorted(
        contexts,
        key=lambda item: (
            _cosine_similarity_api(query_vector, vectors_by_chunk.get(str(item.chunk_id))),
            float(item.score or 0.0),
        ),
        reverse=True,
    )
    kept: list[RagContextChunk] = []
    dropped: list[str] = []
    for context in ranked_contexts:
        vector = vectors_by_chunk.get(str(context.chunk_id))
        if vector is None:
            kept.append(context)
            continue
        is_duplicate = False
        for existing in kept:
            if context.source_kind != existing.source_kind or context.document_id != existing.document_id:
                continue
            existing_vector = vectors_by_chunk.get(str(existing.chunk_id))
            if existing_vector is None:
                continue
            if _cosine_similarity_api(vector, existing_vector) >= threshold:
                is_duplicate = True
                break
        if is_duplicate:
            dropped.append(str(context.chunk_id))
            continue
        kept.append(context)
    kept_ids = {str(context.chunk_id) for context in kept}
    ordered_kept = [context for context in contexts if str(context.chunk_id) in kept_ids]
    stats["after_count"] = len(ordered_kept)
    stats["dropped_chunk_ids"] = dropped
    return ordered_kept, stats


def _cosine_similarity_api(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _llm_thresholds_payload(llm_client: RagLlmClient) -> dict:
    provider = llm_client.provider
    timeout_seconds = getattr(provider, "timeout_seconds", None)
    config_snapshot = RagLlmConfig.from_env()
    return {
        "call_order": ["answer", "verify", "refuse"],
        "temperature_default": 0.0,
        "provider": provider.provider_name,
        "model": provider.model_name,
        "call_timeout_seconds": timeout_seconds,
        "default_timeout_seconds": config_snapshot.timeout_seconds,
        "prompt_prefixes": llm_client.prompt_prefixes.as_dict(),
        "missing_prefix_policy": "fail_fast",
    }


def _pdf_first_llm_debug_payload(llm_client: RagLlmClient) -> dict:
    provider = llm_client.provider
    timeout_seconds = getattr(provider, "timeout_seconds", None)
    num_predict = getattr(provider, "num_predict", None)
    return {
        "provider": provider.provider_name,
        "model": provider.model_name,
        "call_type": "answer",
        "temperature": 0.0,
        "timeout_seconds": timeout_seconds,
        "ollama_num_predict": num_predict,
        "local_model": provider.provider_name == "ollama",
    }


def _ask_thresholds_payload(*, request: AskRequest, llm_client: RagLlmClient, effective_scope: dict[str, Any] | None = None) -> dict:
    effective_scope = effective_scope or _ask_effective_scope(request)
    if effective_scope.get("forced_pdf_first_scope"):
        return {
            "debug_payload_kind": "pdf_first_debug",
            "obsolete_legacy_thresholds_hidden": True,
            "scope": {
                "source_types": effective_scope["source_types"],
                "required_source_types": effective_scope["required_source_types"],
                "document_ids": effective_scope["document_ids"],
                "document_version_ids": effective_scope["document_version_ids"],
                "forced_pdf_first_scope": True,
                "scope_reason": effective_scope.get("scope_reason"),
                "date_scope": effective_scope.get("date_scope"),
            },
            "retrieval": {
                "top_k_min": 1,
                "top_k_max": 50,
                "top_k_effective": max(1, request.top_k),
                "source_kinds": list(PDF_FIRST_ASK_SOURCE_TYPES),
                "artifact_kinds": sorted(PDF_FIRST_RETRIEVAL_ARTIFACT_KINDS),
            },
            "answering": {
                "route": "pdf_first_dictalm",
                "model_role": "DictaLM judges outcomes and writes cited claims from PDF-first evidence.",
                "deterministic_role": [
                    "force PDF-first evidence scope",
                    "rank and package evidence chunks",
                    "validate cited chunk ids and cited-claim coverage",
                    "retry malformed JSON with compact evidence",
                    "repair missing claims only when DictaLM already returned answer_status=answer and visible vote/result text exists",
                ],
                "legacy_rules_disabled": [
                    "decision marker scoring",
                    "old decision-unit reconstruction",
                    "claim score thresholds",
                    "fallback heuristic weights",
                    "protocol/attachment coverage rules",
                ],
            },
            "llm": _pdf_first_llm_debug_payload(llm_client),
        }
    return {
        "request_validation": {
            "top_k_min": 1,
            "top_k_max": 50,
            "top_k_effective": max(1, request.top_k),
        },
        "retrieval": search_thresholds_snapshot(),
        "answering": rag_answering_thresholds_snapshot(),
        "llm": _llm_thresholds_payload(llm_client),
    }


def _citation_document_url(citation: RagCitation) -> str:
    allowed_docvers = _pdf_first_allowed_document_version_ids()
    if citation.source_kind in PDF_FIRST_ASK_SOURCE_TYPES and citation.document_version_id and (
        allowed_docvers is None or citation.document_version_id in allowed_docvers
    ):
        return f"/document-versions/{citation.document_version_id}/source.pdf"
    return citation.document_url


def _normalized_answering_trace(scoring: dict | None) -> dict:
    trace = dict(scoring or {})
    trace.setdefault("semantic_scoring_source", "not_reached")
    trace.setdefault("verify_route", "not_reached")
    trace.setdefault("fallback_verify_attempted", False)
    trace.setdefault("fallback_provider", "none")
    trace.setdefault("similarity_external_api_called", False)
    trace.setdefault("answer_external_api_called", False)
    trace.setdefault("external_call_count", 0)
    timing_payload = trace.get("timing_ms")
    if not isinstance(timing_payload, dict):
        trace["timing_ms"] = {}
    return trace


def _provider_warning_payload(answer_result: RagAnswerResult) -> dict[str, Any] | None:
    scoring = dict(answer_result.scoring or {})
    if not bool(scoring.get("degraded_upstream_failure")):
        return None
    upstream_provider = str(scoring.get("upstream_answer_provider") or "").strip() or None
    upstream_model = str(scoring.get("upstream_answer_model") or "").strip() or None
    error_code = str(scoring.get("upstream_answer_error_code") or scoring.get("answer_call_error_code") or "").strip() or None
    mock_mode = answer_result.status == "answer"
    message_he = (
        "ספק התשובה אינו זמין כעת. מוצגת טיוטת תשובה מבוססת שליפה בלבד."
        if mock_mode
        else "ספק התשובה אינו זמין כעת, ולא ניתן היה להרכיב גם טיוטת תשובה אמינה מהראיות שנשלפו."
    )
    return {
        "message_he": message_he,
        "provider": upstream_provider,
        "model": upstream_model,
        "error_code": error_code,
        "mock_mode": mock_mode,
    }


def _answer_mode(answer_result: RagAnswerResult) -> str:
    if answer_result.status != "answer":
        return "refusal"
    return "mockup" if bool(dict(answer_result.scoring or {}).get("degraded_upstream_failure")) else "grounded"


def _as_int_in_range(value: Any, *, min_value: int, max_value: int) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < min_value or parsed > max_value:
        return None
    return parsed


def _as_optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cache_topic_name(topic_name: str) -> str:
    segments = _topic_path_segments(topic_name)
    if not segments:
        return "ללא תיוג סמנטי"

    root_topic = segments[0]
    if len(segments) == 1:
        return root_topic or "ללא תיוג סמנטי"

    out = [root_topic or "ללא תיוג סמנטי"]
    for segment in segments[1:]:
        normalized_segment = segment.strip()
        if normalized_segment in {"הזמנה תקציבית", "הצעת מחיר", "אישור תקציבי", "אישור הצעה", "החלטה כללית"}:
            continue
        segment_tokens = [token for token in normalized_segment.split() if token]
        if len(segment_tokens) < 2:
            continue
        out.append(" ".join(segment_tokens[:8]))
    return _join_topic_path(out)


TOPIC_LABEL_STOP_TOKENS = {
    "פרוטוקול",
    "ועדה",
    "ועדת",
    "הועדה",
    "הוועדה",
    "מס",
    "מספר",
    "ישיבה",
    "דיון",
    "דיונים",
    "החלטה",
    "החלטות",
    "אישור",
    "מאשר",
    "מאשרים",
    "עיר",
    "בעיר",
    "עירייה",
    "העירייה",
}
TOPIC_TOKEN_RE = re.compile(r"[\u0590-\u05FF]{2,}|[A-Za-z]{2,}")


def _topic_display_tokens(value: str, *, max_tokens: int | None = None) -> list[str]:
    compact = " ".join(str(value or "").split())
    if not compact:
        return []

    tokens: list[str] = []
    for token in TOPIC_TOKEN_RE.findall(compact):
        token_norm = normalize_for_search(token)
        if not token_norm or token_norm in TOPIC_LABEL_STOP_TOKENS:
            continue
        tokens.append(token)
        if max_tokens is not None and len(tokens) >= max_tokens:
            break
    return tokens


def _sanitize_topic_label(value: str, *, min_tokens: int = 2, max_tokens: int = 4) -> str:
    tokens = _topic_display_tokens(value, max_tokens=max_tokens)
    if len(tokens) < min_tokens:
        return ""
    return " ".join(tokens).strip()


def _topic_path_segments(topic_name: str | None) -> list[str]:
    parts = [part.strip() for part in str(topic_name or "").split(">") if part and part.strip()]
    if not parts:
        return []

    out: list[str] = []
    for index, part in enumerate(parts):
        min_tokens = 1 if index == 0 else 2
        cleaned = _sanitize_topic_label(part, min_tokens=min_tokens, max_tokens=8)
        if cleaned:
            out.append(cleaned)
    return out


def _join_topic_path(segments: list[str]) -> str:
    cleaned = [segment.strip() for segment in segments if str(segment).strip()]
    return " > ".join(cleaned)


def _split_topic_path(topic_name: str | None) -> tuple[str, str]:
    parts = _topic_path_segments(topic_name)
    if len(parts) >= 2:
        return parts[0], " > ".join(parts[1:])
    if parts:
        return parts[0], "החלטה כללית"
    return "ללא תיוג סמנטי", "החלטה כללית"


TOPIC_VARIANT_DETAIL_TOKENS = {
    "קיימים",
    "מוארים",
    "חדש",
    "חדשים",
    "חדשה",
    "זמני",
    "זמנית",
    "ראשון",
    "שני",
    "נוסף",
    "נוספת",
    "נוספים",
    "נוספות",
}
TOPIC_MERGE_TOKEN_NORMALIZATION = {
    "בבקשות": "בקשות",
    "להקצאת": "להקצאה",
    "בהקצאת": "הקצאה",
}
PLACEHOLDER_TOPIC_LABEL = "ללא תיוג סמנטי"


def _topic_granularity_heuristic(topic: str) -> int:
    tokens = _topic_merge_tokens(topic)
    if not tokens:
        return 5
    if len(tokens) == 1:
        return 3
    if len(tokens) == 2:
        if tokens[1] in TOPIC_VARIANT_DETAIL_TOKENS:
            return 7
        return 5
    if len(tokens) == 3:
        return 7
    return 8


def _merge_head_phrase(topic: str, *, granularity_level: int) -> str:
    topic_norm = normalize_for_search(_sanitize_topic_label(topic, min_tokens=1, max_tokens=6))
    if topic_norm == normalize_for_search(PLACEHOLDER_TOPIC_LABEL):
        return PLACEHOLDER_TOPIC_LABEL

    tokens = _topic_merge_tokens(topic)
    if not tokens:
        return PLACEHOLDER_TOPIC_LABEL

    if tokens[0] in {"ללא", "בקשה", "בקשות"} and len(tokens) >= 2:
        if tokens[0] == "ללא" and tokens[1] == "תיוג":
            return PLACEHOLDER_TOPIC_LABEL
        return " ".join(tokens[:2])

    if len(tokens) >= 2 and tokens[0] == "בקשה" and tokens[1] in {"להקצאה", "להקצאת"}:
        return "בקשה להקצאה"

    if len(tokens) >= 2 and tokens[1] in TOPIC_VARIANT_DETAIL_TOKENS and granularity_level >= 6:
        return tokens[0]
    if len(tokens) >= 2:
        return " ".join(tokens[:2])
    return tokens[0]


def _topic_lexical_overlap(topic_a: str, topic_b: str) -> int:
    tokens_a = set(_topic_merge_tokens(topic_a))
    tokens_b = set(_topic_merge_tokens(topic_b))
    if not tokens_a or not tokens_b:
        return 0
    return len(tokens_a.intersection(tokens_b))


def _topic_merge_tokens(topic: str) -> list[str]:
    tokens = [token for token in _sanitize_topic_label(topic, min_tokens=1, max_tokens=6).split() if token]
    if not tokens:
        return []

    out: list[str] = []
    for token in tokens:
        out.append(TOPIC_MERGE_TOKEN_NORMALIZATION.get(token, token))
    return [token for token in out if token]


def _object_root_from_topic_parts(*, root_topic: str, child_topic: str) -> str | None:
    combined_tokens = set(_topic_merge_tokens(f"{root_topic} {child_topic}"))
    if not combined_tokens:
        return None

    if any(token in combined_tokens for token in {"הסכם", "הסכמים", "חוזה", "חוזים", "רשות"}):
        return "הסכמים"
    if any(token in combined_tokens for token in {"תמרור", "תמרורים"}):
        return "תמרורים"
    if any(token in combined_tokens for token in {"הקצאה", "הקצאות", "להקצאה", "בקשה", "בקשות"}):
        return "הקצאות"
    if any(token in combined_tokens for token in {"ניקיון"}):
        return "ניקיון"
    if any(token in combined_tokens for token in {"אבטחה", "אבטחת"}):
        return "אבטחה"
    return None


def _canonicalize_topic_child(*, root_topic: str, child_topic: str) -> str:
    child_norm = normalize_for_search(child_topic)
    root_norm = normalize_for_search(root_topic)
    if not child_norm:
        return child_topic

    if root_norm == normalize_for_search("הקצאות"):
        if "עמות" in child_norm and "הקצא" in child_norm:
            return "הקצאה לעמותה"
        if "קרקע" in child_norm and "מבנ" in child_norm:
            return "הקצאת קרקעות ומבנים"
        if child_norm in {
            normalize_for_search("בקשה להקצאה"),
            normalize_for_search("החלטת הקצאות"),
            normalize_for_search("פרסום בעיתונות"),
            normalize_for_search("החלטה כללית"),
        }:
            return "אישור הקצאה"

    if root_norm == normalize_for_search("הסכמים"):
        if "הסכם" in child_norm and "רשות" in child_norm:
            if "עמות" in child_norm:
                return "הסכם רשות לעמותה"
            if "עירייה" in child_norm:
                return "הסכם רשות לעירייה"
            return "הסכם רשות"

    return child_topic


TOPIC_REQUEST_SUBJECT_PREFERRED_MARKERS = (
    "בקשה למתן ",
    "בקשה להסדרת ",
    "בקשה להקצאת ",
    "בקשה להקצאה ",
    "בקשה להחלפת ",
    "בקשה לביטול ",
    "בקשה לקבלת ",
    "בקשת ",
    "העמותה מבקשת ",
    "העמותה מבקשה ",
    "מבקשת ",
    "מבקשה ",
    "מבוקש ",
    "מבוקשת ",
)
TOPIC_REQUEST_SUBJECT_STOP_MARKERS = (
    ".",
    "מורשי חתימה",
    "חברי הנהלה",
    "בעלי זכות חתימה",
    "הבקשה פורסמה",
    "הוצבה הודעה",
    "בהתאם להחלטת",
    "בהמשך לבקשת",
    "לקראת שנת הלימודים",
    "לאור סיום",
)
TOPIC_REQUEST_SUBJECT_LEADING_NORMALIZATION = {
    "למתן ": "מתן ",
    "להסדרת ": "הסדרת ",
    "להסדיר ": "הסדרת ",
    "להקצאת ": "הקצאת ",
    "להקצאה ": "הקצאה ",
    "להחלפת ": "החלפת ",
    "לביטול ": "ביטול ",
    "לקבלת ": "קבלת ",
    "לרשות שימוש": "רשות שימוש",
}
TOPIC_LEAF_ENTITY_LEAD_TOKENS = {
    "העמותה",
    "לעמותה",
    "עמותה",
    "עמותת",
    "העירייה",
    "העיריה",
}
TOPIC_LEAF_RELATIONAL_LEAD_TOKENS = {
    "מבקשת",
    "מבוקש",
    "מבוקשת",
    "ממליצה",
    "מדובר",
    "מאשרת",
    "מאשר",
    "לגבי",
    "בנוגע",
    "עבור",
    "עבור",
}
TOPIC_LEAF_TRUNCATED_TAIL_TOKENS = {
    "דו",
}
TOPIC_LEAF_MAX_PROTOCOL_LINKS = 8
TOPIC_LEAF_MAX_SUPPORT_LINKS = 8
TOPIC_LEAF_SEMANTIC_CANDIDATE_LIMIT = 14
TOPIC_LEAF_EMBED_MIN_SIMILARITY = 0.18
TOPIC_LEAF_SCORE_MARGIN = 0.035
TOPIC_LEAF_NEIGHBOR_LOOKBACK = 4
TOPIC_LEAF_NEIGHBOR_LOOKAHEAD = 1
TOPIC_LOCAL_SUBJECT_MAX_CANDIDATES = 10
TOPIC_SEMANTIC_MERGE_THRESHOLD = 0.82
TOPIC_SEMANTIC_EMBED_MERGE_THRESHOLD = 0.86
TOPIC_LOCAL_ALIGNMENT_MIN = 0.45
TOPIC_NON_SUBJECT_PROTOTYPES = (
    "גורם מנהלי",
    "בעל תפקיד",
    "אגף עירוני",
    "מחלקה עירונית",
    "יחידה ארגונית",
    "מינהל עירוני",
    "גורם מקצועי",
    "עמותה",
    "ספק חיצוני",
)
FIXED_TAXONOMY_ROOTS = {
    normalize_for_search("הקצאות"),
    normalize_for_search("הסכמים"),
    normalize_for_search("תמרורים"),
    normalize_for_search("ניקיון"),
    normalize_for_search("אבטחה"),
    normalize_for_search("עירוניות"),
    normalize_for_search("החלטות עירוניות"),
}
PROTOCOL_TITLE_SUBJECT_RE = re.compile(r"\bבנושא\s+(.+)$")
TOPIC_PROCEDURAL_PREFIX_PATTERNS = (
    re.compile(r"^החלטה(?:\s+מספר|\s+מס['\"]?)?[\s:\-–]*\d+[\d/.-]*\s*"),
    re.compile(r"^נושא\s+החלטה\s*"),
    re.compile(r"^סעיף\s*:?\s*\d+[\d/.-]*\s*"),
    re.compile(r"^מכרז\s*\d+[\d/.-]*\s*[-:]?\s*"),
    re.compile(r"^מס['\"]?\s*\d+[\d/.-]*\s*"),
)
TOPIC_GENERIC_SUBJECT_PREFIXES = (
    "הועדה",
    "הוועדה",
    "ועדת",
    "הוחלט",
    "מאשרים",
    "מאשרת",
    "מאושר",
    "אושרה",
    "לפיכך",
    "לאור",
    "בהתאם",
)
TOPIC_GENERIC_SUBJECT_VALUES = {
    normalize_for_search("מבקש"),
    normalize_for_search("תאריך"),
    normalize_for_search("עמוד"),
    normalize_for_search("נושא המשימה"),
}
TOPIC_PROCEDURAL_SUBJECT_TOKENS = {
    "תקופה",
    "שנים",
    "שנה",
    "אופציה",
    "הארכת",
    "הסכם",
    "הסכמי",
    "התקשרות",
    "הרחבה",
    "הרחבת",
    "פריט",
    "פריטים",
    "מכרז",
    "תשלום",
    "אישור",
    "אישרה",
    "מאשרים",
    "מאשרת",
    "תושבים",
    "עמותה",
    "עירייה",
    "עיריה",
    "התנגדות",
    "התנגדויות",
    "ערעור",
    "דחייה",
    "דחיית",
    "פרסום",
    "עיתונות",
}
TOPIC_OBJECT_FIELD_POSITIVE_TOKENS = {
    "שימוש",
    "שימושים",
    "ייעוד",
    "יעוד",
    "סוג",
    "סיווג",
    "מטרה",
    "מטרת",
}
TOPIC_OBJECT_FIELD_SECONDARY_TOKENS = {
    "תאור",
    "תיאור",
    "מהות",
    "נושא",
}
TOPIC_OBJECT_FIELD_NEGATIVE_TOKENS = {
    "גוש",
    "חלקה",
    "מגרש",
    "כתובת",
    "שטח",
    "החלטה",
    "החלטת",
    "החלטות",
    "סעיף",
    "נוכחים",
    "נעדרו",
    "חברי הנהלה",
    "מורשי חתימה",
}
TOPIC_OBJECT_VALUE_STOP_MARKERS = (
    ".",
    ",",
    "הבקשה",
    "חברי הנהלה",
    "מורשי חתימה",
    "בעלי זכות חתימה",
    "פרטי מקרקעין",
    "נחתמו",
    "בהתאם",
    "הוצבה",
    "פורסמה",
    "לא התקבלו",
)
TOPIC_OBJECT_DETAIL_TOKENS = {
    "דו",
    "תכליתי",
    "לגברים",
    "לנשים",
    "לבנות",
    "לבנים",
    "בקומת",
    "במתחם",
    "בתחום",
    "בשטח",
    "טהרה",
    "רחצה",
}
TOPIC_REQUEST_GENERIC_LEAD_TOKENS = {
    "בקשה",
    "בקשת",
    "מבקשת",
    "מבקשה",
}
TOPIC_CANDIDATE_SOURCE_BONUS = {
    "current": 0.02,
    "structured": 0.18,
    "local_subject": 0.2,
    "semantic_local": 0.14,
    "agenda_item": 0.12,
    "decision_text": 0.1,
    "semantic_node": -0.08,
    "subject_topic": 0.02,
    "request_subject": -0.04,
}


def _clean_topic_candidate_phrase(value: str, *, max_tokens: int = 8) -> str:
    compact = _strip_topic_entity_detail(value)
    compact = re.sub(r"[\r\n]+", " ", compact)
    compact = re.sub(r"\s+", " ", compact).strip(" ,;:-")
    if not compact:
        return ""

    for pattern in TOPIC_PROCEDURAL_PREFIX_PATTERNS:
        compact = pattern.sub("", compact).strip(" ,;:-")

    compact = re.sub(r"^\d+[\d/.-]*", "", compact).strip(" ,;:-")
    compact = re.sub(r"^(?:ועדת|ועדה|הועדה|הוועדה)\s+", "", compact).strip(" ,;:-")
    compact = re.sub(r"^(?:בנושא|הנדון)\s*:?\s*", "", compact).strip(" ,;:-")

    candidate = _sanitize_topic_label(compact, min_tokens=2, max_tokens=max_tokens)
    if not candidate:
        return ""
    return candidate


def _looks_generic_topic_candidate(value: str) -> bool:
    candidate = _clean_topic_candidate_phrase(value)
    if not candidate:
        return True
    raw_value = str(value or "")
    if "@" in raw_value or "http" in raw_value.casefold():
        return True

    normalized = normalize_for_search(candidate)
    if normalized in TOPIC_GENERIC_SUBJECT_VALUES:
        return True

    tokens = _topic_text_tokens(candidate)
    if len(tokens) < 2:
        return True
    if tokens[0] in TOPIC_GENERIC_SUBJECT_PREFIXES:
        return True
    return False


def _extract_inline_topic_candidates(text: str) -> list[str]:
    if not text:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for raw_line in str(text).splitlines()[:3]:
        line = " ".join(raw_line.split())
        if not line:
            continue
        if "@" in line or "http" in line.casefold():
            continue
        if ":" in line:
            continue
        if ":" not in line and not re.search(r"^(?:החלטה|סעיף|מכרז|בנושא)", line) and line.endswith("."):
            continue
        candidate = _clean_topic_candidate_phrase(line)
        if not candidate or _looks_generic_topic_candidate(candidate):
            continue
        key = normalize_for_search(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
        if len(out) >= 4:
            break
    return out


def _structured_topic_fields_from_chunk_text(text: str) -> list[tuple[str, str]]:
    if not text:
        return []

    lines = [" ".join(str(raw_line or "").split()) for raw_line in str(text).splitlines()]
    out: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or ":" not in line:
            continue

        left, right = line.split(":", 1)
        label = _normalize_headline_text(left)
        value = _normalize_headline_text(right)
        if not value:
            while index < len(lines):
                fallback_value = _normalize_headline_text(lines[index])
                index += 1
                if fallback_value:
                    value = fallback_value
                    break
        if label and value:
            out.append((label, value))
    return out


def _clean_topic_object_value(value: str) -> str:
    compact = _strip_topic_entity_detail(value)
    compact = re.sub(r"[\r\n]+", " ", compact)
    compact = re.sub(r"\s+", " ", compact).strip(" ,;:-")
    for pattern in TOPIC_PROCEDURAL_PREFIX_PATTERNS:
        compact = pattern.sub("", compact).strip(" ,;:-")
    if not compact:
        return ""
    for marker in TOPIC_OBJECT_VALUE_STOP_MARKERS:
        marker_norm = normalize_for_search(marker)
        compact_norm = normalize_for_search(compact)
        split_index = compact_norm.find(marker_norm)
        if split_index > 0:
            compact = compact[:split_index].rstrip(" ,;:-")
            break

    candidate_tokens = [token for token in _sanitize_topic_label(compact, min_tokens=1, max_tokens=6).split() if token]
    while candidate_tokens and candidate_tokens[-1] in TOPIC_OBJECT_DETAIL_TOKENS:
        candidate_tokens.pop()
    if not candidate_tokens:
        return ""
    candidate = " ".join(candidate_tokens)
    candidate_norm = normalize_for_search(candidate)
    if candidate_norm in TOPIC_GENERIC_SUBJECT_VALUES:
        return ""
    if normalize_for_search(candidate).startswith(normalize_for_search("החלטת")):
        return ""
    return candidate


def _topic_object_field_score(*, label: str, value: str, distance: int) -> float:
    label_norm = normalize_for_search(label)
    value_norm = normalize_for_search(value)
    if not label_norm or not value_norm:
        return -1.0
    object_candidate = _clean_topic_object_value(value)
    if not object_candidate and _looks_generic_topic_candidate(value):
        return -1.0
    if any(token in label_norm for token in TOPIC_OBJECT_FIELD_NEGATIVE_TOKENS):
        return -1.0
    if any(token in label_norm for token in {"בעלי ענין", "פקס", "email", "e-mail", "טל"}):
        return -1.0
    if any(token in label_norm for token in {"דיון", "דיונים", "מהלך"}):
        return -1.0
    if any(token in label_norm for token in {"נוכחים", "נעדרו", "פרוטוקול", "תאריך", "עמוד"}):
        return -1.0
    if any(token in value_norm for token in {"גוש", "חלקה", "מגרש", "כתובת"}):
        return -1.0

    score = 1.4
    label_tokens = _topic_text_tokens(label)
    value_tokens = _topic_text_tokens(value)
    if "מהות" in label_norm:
        score -= 0.45
    if 1 <= len(label_tokens) <= 4:
        score += 0.2
    if 2 <= len(value_tokens) <= 5:
        score += 0.4
    if any(re.search(r"[A-Za-z]", token) for token in value.split()):
        score += 0.2

    if not value_tokens:
        return -1.0
    if any(char.isdigit() for char in value_norm):
        score -= 0.35
    score -= max(0, distance) * 0.2
    return score


def _protocol_subject_root_from_title(protocol_title: str) -> str | None:
    compact = " ".join(str(protocol_title or "").split())
    if not compact:
        return None
    match = PROTOCOL_TITLE_SUBJECT_RE.search(compact)
    if match is None:
        return None
    return _sanitize_topic_label(match.group(1), min_tokens=3, max_tokens=8) or None


def _is_substantive_protocol_root(value: str) -> bool:
    candidate = _sanitize_topic_label(value, min_tokens=2, max_tokens=8)
    if not candidate:
        return False
    candidate_tokens = _topic_text_tokens(candidate)
    if candidate_tokens and candidate_tokens[0] in TOPIC_LEAF_ENTITY_LEAD_TOKENS:
        return False
    if candidate_tokens and candidate_tokens[0] in TOPIC_LEAF_RELATIONAL_LEAD_TOKENS:
        return False
    if candidate_tokens and candidate_tokens[0] in TOPIC_REQUEST_GENERIC_LEAD_TOKENS:
        return False
    if candidate_tokens and candidate_tokens[0] in {
        "הקמה",
        "הפעלת",
        "לתקופה",
        "הארכת",
        "הרחבה",
        "הרחבת",
        "מכרז",
        "חשכ",
        "תוספת",
        "הסדרת",
        "ביטול",
        "החלפת",
        "יצירת",
        "גישה",
        "בקשה",
        "בקשת",
        "מבקשת",
        "מתן",
        "רשות",
    }:
        return False
    if _preferred_root_from_action(candidate) is not None:
        return False
    return normalize_for_search(candidate) not in FIXED_TAXONOMY_ROOTS and len(_topic_text_tokens(candidate)) >= 3


def _topic_action_from_texts(*values: str) -> str | None:
    combined = normalize_for_search(" ".join(str(value or "") for value in values if str(value or "").strip()))
    if not combined:
        return None
    if "ביטול" in combined and "הקצא" in combined:
        return "ביטול הקצאה"
    if "תוספת" in combined and "שימוש" in combined:
        return "תוספת שימוש"
    if "הסדרת" in combined and "שימוש" in combined:
        return "הסדרת שימוש"
    if "רשות" in combined and "שימוש" in combined:
        return "רשות שימוש"
    if "החלפ" in combined and "הקצא" in combined:
        return "החלפת הקצאה"
    if "הקצא" in combined and "קרקע" in combined:
        return "הקצאת קרקע"
    if "הקצא" in combined and "מבנ" in combined:
        return "הקצאת מבנה"
    return None


def _topic_action_preposition(action: str) -> str:
    if normalize_for_search(action) in {
        normalize_for_search("הסדרת שימוש"),
        normalize_for_search("רשות שימוש"),
    }:
        return "ב"
    return "ל"


def _compose_topic_from_action_and_object(*, action: str, object_value: str) -> str | None:
    object_candidate = _clean_topic_object_value(object_value)
    if not object_candidate:
        return None

    action_tokens = set(_topic_text_tokens(action))
    def is_action_related(token: str) -> bool:
        token_norm = normalize_for_search(token)
        token_stripped = re.sub(r"^[בלכמשוה]", "", token_norm)
        return token_norm in action_tokens or token_stripped in action_tokens

    object_tokens_list = _topic_display_tokens(object_candidate, max_tokens=8)
    filtered_object_tokens = [
        token
        for token in object_tokens_list
        if not is_action_related(token)
        and normalize_for_search(token) not in TOPIC_REQUEST_GENERIC_LEAD_TOKENS
        and normalize_for_search(token) not in TOPIC_PROCEDURAL_SUBJECT_TOKENS
    ]
    if filtered_object_tokens:
        object_candidate = " ".join(filtered_object_tokens)

    object_tokens = set(_topic_text_tokens(object_candidate))
    if object_tokens and object_tokens.issubset(action_tokens):
        return _clean_topic_candidate_phrase(action)

    return f"{action} {_topic_action_preposition(action)}{object_candidate}".strip()


def _preferred_root_from_action(action: str | None) -> str | None:
    normalized = normalize_for_search(action or "")
    if normalized in {
        normalize_for_search("הקצאת קרקע"),
        normalize_for_search("הקצאת מבנה"),
        normalize_for_search("ביטול הקצאה"),
        normalize_for_search("החלפת הקצאה"),
        normalize_for_search("תוספת שימוש"),
        normalize_for_search("הסדרת שימוש"),
        normalize_for_search("רשות שימוש"),
    }:
        return "הקצאות"
    if normalized == normalize_for_search("הסכם רשות"):
        return "הסכמים"
    return None


def _strip_topic_entity_detail(value: str) -> str:
    compact = str(value or "")
    compact = re.sub(r"\([^)]*\)", " ", compact)
    compact = re.sub(r"[\"״][^\"״]+[\"״]", " ", compact)
    compact = re.sub(r"\s*[–—-]\s*(?=[\u0590-\u05FFA-Za-z]{2,})", " - ", compact)
    for separator in (" - ", " – ", " — "):
        if separator in compact:
            left, _right = compact.split(separator, 1)
            if len(_topic_display_tokens(left, max_tokens=6)) >= 2:
                compact = left
                break
    return " ".join(compact.split()).strip()


def _compact_request_subject_for_topic(value: str) -> str | None:
    compact = _strip_topic_entity_detail(value)
    compact = " ".join(str(compact or "").split()).strip(" ,;:-")
    if not compact:
        return None

    start_index = 0
    for marker in TOPIC_REQUEST_SUBJECT_PREFERRED_MARKERS:
        marker_index = compact.find(marker)
        if marker_index >= 0:
            start_index = marker_index
            break
    compact = compact[start_index:].strip(" ,;:-")

    for stop_marker in TOPIC_REQUEST_SUBJECT_STOP_MARKERS:
        marker_index = compact.find(stop_marker)
        if marker_index > 0:
            compact = compact[:marker_index].strip(" ,;:-")
            break

    compact = re.sub(r"^בקשת\s+[^\s]+\s+ל", "ל", compact)
    compact = re.sub(r"^בקשת\s+העמותה\s+ל", "ל", compact)
    compact = re.sub(r"^בקשה\s+ל", "ל", compact)
    compact = re.sub(r"^העמותה\s+מבקש(?:ה|ת)\s+", "", compact)
    compact = re.sub(r"^מבקשת\s+", "", compact)
    compact = re.sub(r"^מבקשה\s+", "", compact)
    compact = re.sub(r"^מבוקש(?:ת)?\s+", "", compact)
    compact = re.sub(r"^לעמותה\s+קיים\s+הסכם[^.]*", "", compact).strip(" ,;:-")

    for prefix, replacement in TOPIC_REQUEST_SUBJECT_LEADING_NORMALIZATION.items():
        if compact.startswith(prefix):
            compact = f"{replacement}{compact[len(prefix):]}"
            break

    compact = _sanitize_topic_label(compact, min_tokens=2, max_tokens=6)
    return compact or None


def _topic_text_tokens(value: str, *, max_tokens: int | None = None) -> list[str]:
    tokens = [normalize_for_search(token) for token in _topic_display_tokens(value, max_tokens=max_tokens)]
    if max_tokens is not None:
        return tokens[:max_tokens]
    return tokens


def _topic_text_overlap_ratio(topic: str, text: str) -> float:
    topic_tokens = set(_topic_merge_tokens(topic))
    text_tokens = set(_topic_text_tokens(text))
    if not topic_tokens or not text_tokens:
        return 0.0
    return len(topic_tokens.intersection(text_tokens)) / max(len(topic_tokens), 1)


def _topic_label_vector(
    label: str,
    *,
    embedding_service: ChunkEmbeddingService | None,
    candidate_vector_cache: dict[str, list[float]],
) -> list[float] | None:
    cleaned = _clean_topic_candidate_phrase(label)
    if not cleaned:
        return None
    vector = candidate_vector_cache.get(cleaned)
    if vector is not None:
        return vector
    if embedding_service is None or not embedding_service.is_enabled():
        return None
    try:
        vectors = embedding_service.model_client.embed_texts([cleaned])
    except Exception:  # noqa: BLE001
        return None
    if len(vectors) != 1:
        return None
    vector = [float(value) for value in vectors[0]]
    candidate_vector_cache[cleaned] = vector
    return vector


def _topic_concept_similarity(
    left: str,
    right: str,
    *,
    embedding_service: ChunkEmbeddingService | None,
    candidate_vector_cache: dict[str, list[float]],
) -> float:
    left_clean = _clean_topic_candidate_phrase(left)
    right_clean = _clean_topic_candidate_phrase(right)
    if not left_clean or not right_clean:
        return 0.0

    lexical_score = TOPIC_SEMANTIC_CANONICALIZER.similarity_score(
        TOPIC_SEMANTIC_CANONICALIZER.normalize_text(left_clean, expand_abbreviations=False),
        TOPIC_SEMANTIC_CANONICALIZER.normalize_text(right_clean, expand_abbreviations=False),
    )
    left_vector = _topic_label_vector(left_clean, embedding_service=embedding_service, candidate_vector_cache=candidate_vector_cache)
    right_vector = _topic_label_vector(right_clean, embedding_service=embedding_service, candidate_vector_cache=candidate_vector_cache)
    embedding_score = _cosine_similarity_api(left_vector, right_vector) if left_vector is not None and right_vector is not None else 0.0
    return max(float(lexical_score), float(embedding_score))


def _topic_label_alignment_score(
    label: str,
    *,
    reference_labels: list[str],
    embedding_service: ChunkEmbeddingService | None,
    candidate_vector_cache: dict[str, list[float]],
) -> float:
    cleaned = _clean_topic_candidate_phrase(label)
    if not cleaned:
        return 0.0
    scores = [
        _topic_concept_similarity(
            cleaned,
            reference,
            embedding_service=embedding_service,
            candidate_vector_cache=candidate_vector_cache,
        )
        for reference in reference_labels
        if _clean_topic_candidate_phrase(reference)
    ]
    if not scores:
        return 0.0
    return max(scores)


def _topic_non_subject_similarity(
    label: str,
    *,
    embedding_service: ChunkEmbeddingService | None,
    candidate_vector_cache: dict[str, list[float]],
) -> float:
    cleaned = _clean_topic_candidate_phrase(label)
    if not cleaned:
        return 0.0

    lexical_tokens = set(_topic_text_tokens(cleaned))
    lexical_penalty = 0.0
    if lexical_tokens.intersection({"מנהל", "מינהל", "אגף", "מחלקה", "יחידה", "ועדה", "חברה", "ספק"}):
        lexical_penalty = 0.7

    embedding_penalty = 0.0
    for prototype in TOPIC_NON_SUBJECT_PROTOTYPES:
        embedding_penalty = max(
            embedding_penalty,
            _topic_concept_similarity(
                cleaned,
                prototype,
                embedding_service=embedding_service,
                candidate_vector_cache=candidate_vector_cache,
            ),
        )
    return max(lexical_penalty, embedding_penalty)


def _topic_is_action_equivalent(label: str) -> bool:
    cleaned = _clean_topic_candidate_phrase(label)
    action = _topic_action_from_texts(cleaned)
    if not cleaned or not action:
        return False
    if normalize_for_search(cleaned) == normalize_for_search(action):
        return True
    if (
        len(_topic_text_tokens(cleaned)) <= len(_topic_text_tokens(action)) + 1
        and _topic_text_overlap_ratio(action, cleaned) >= 0.5
    ):
        return True
    similarity = TOPIC_SEMANTIC_CANONICALIZER.similarity_score(
        TOPIC_SEMANTIC_CANONICALIZER.normalize_text(cleaned, expand_abbreviations=False),
        TOPIC_SEMANTIC_CANONICALIZER.normalize_text(action, expand_abbreviations=False),
    )
    return similarity >= 0.8 and len(_topic_text_tokens(cleaned)) <= len(_topic_text_tokens(action)) + 1


def _topic_canonical_preference_score(label: str, *, support_count: int = 0) -> float:
    cleaned = _clean_topic_candidate_phrase(label)
    if not cleaned:
        return -1.0
    tokens = _topic_text_tokens(cleaned)
    if not tokens:
        return -1.0
    action = _topic_action_from_texts(cleaned)
    score = _topic_leaf_quality_adjustment(cleaned)
    score += min(0.2, max(0, support_count) * 0.03)
    score -= max(0, len(tokens) - 3) * 0.03
    if _is_procedural_topic_phrase(cleaned):
        score -= 0.2
    if set(tokens).intersection({"מנהל", "מינהל", "אגף", "מחלקה", "יחידה", "ועדה", "חברה", "ספק"}):
        score -= 0.18
    if action and normalize_for_search(cleaned) == normalize_for_search(action):
        score += 0.08
    if tokens and tokens[0] in TOPIC_LEAF_RELATIONAL_LEAD_TOKENS.union(TOPIC_LEAF_ENTITY_LEAD_TOKENS):
        score -= 0.08
    return score


def _prefer_topic_label(
    left: str,
    right: str,
    *,
    left_support_count: int = 0,
    right_support_count: int = 0,
) -> str:
    left_clean = _clean_topic_candidate_phrase(left)
    right_clean = _clean_topic_candidate_phrase(right)
    if not left_clean:
        return right_clean
    if not right_clean:
        return left_clean

    left_tokens = set(_topic_text_tokens(left_clean))
    right_tokens = set(_topic_text_tokens(right_clean))
    left_score = _topic_canonical_preference_score(left_clean, support_count=left_support_count)
    right_score = _topic_canonical_preference_score(right_clean, support_count=right_support_count)

    if left_tokens and right_tokens:
        if left_tokens.issubset(right_tokens) and left_score >= right_score - 0.08:
            return left_clean
        if right_tokens.issubset(left_tokens) and right_score >= left_score - 0.08:
            return right_clean

    if right_score > left_score + 0.03:
        return right_clean
    if left_score > right_score + 0.03:
        return left_clean
    return left_clean if len(left_clean) <= len(right_clean) else right_clean


def _topic_leaf_quality_adjustment(topic: str) -> float:
    tokens = _topic_merge_tokens(topic)
    if not tokens:
        return -1.0

    score = 0.0
    detail_token_count = len(set(tokens).intersection(TOPIC_OBJECT_DETAIL_TOKENS))
    procedural_token_count = len(set(tokens).intersection(TOPIC_PROCEDURAL_SUBJECT_TOKENS))
    administrative_token_count = len(set(tokens).intersection({"מנהל", "מינהל", "אגף", "מחלקה", "יחידה", "ועדה", "חברה", "ספק"}))
    if detail_token_count > 0:
        score -= min(0.24, detail_token_count * 0.12)
    if procedural_token_count > 0:
        score -= min(0.28, procedural_token_count * 0.08)
    if administrative_token_count > 0:
        score -= min(0.32, administrative_token_count * 0.16)
    if tokens[0] in TOPIC_REQUEST_GENERIC_LEAD_TOKENS:
        score -= 0.12
    if tokens[0] in TOPIC_LEAF_ENTITY_LEAD_TOKENS:
        score -= 0.2
    if tokens[0] in TOPIC_LEAF_RELATIONAL_LEAD_TOKENS:
        score -= 0.14
    if tokens[-1] in TOPIC_LEAF_TRUNCATED_TAIL_TOKENS:
        score -= 0.22
    if tokens[0] in {"הסדרת", "שימוש", "רשות", "הקצאה", "הקצאת", "תוספת", "החלפת", "ביטול", "הסכם"}:
        score += 0.06
    if len(tokens) >= 3:
        score += 0.03
    if len(tokens) >= 4:
        score += 0.02
    return score


def _topic_leaf_is_low_quality(topic: str) -> bool:
    return _topic_leaf_quality_adjustment(topic) <= -0.1


def _protocol_title_topic_root(protocol_title: str) -> str | None:
    return _object_root_from_topic_parts(root_topic=str(protocol_title or ""), child_topic="")


def _topic_leaf_root_for_row(
    *,
    topic_name: str,
    protocol_title: str,
    summary_he: str,
    request_subject_he: str | None = None,
    subject_topic_he: str | None = None,
) -> str:
    cached_topic = _cache_topic_name(topic_name)
    root_topic, child_topic = _split_topic_path(cached_topic)
    root_topic = _sanitize_topic_label(root_topic, min_tokens=1, max_tokens=6)
    child_topic = _sanitize_topic_label(child_topic, min_tokens=2, max_tokens=6)
    request_subject = _compact_request_subject_for_topic(request_subject_he or "")
    subject_topic = _sanitize_topic_label(subject_topic_he or "", min_tokens=2, max_tokens=6)
    inferred_topic = _infer_topic_path_from_summary(summary_he=summary_he, protocol_title=protocol_title)
    inferred_root, _ = _split_topic_path(inferred_topic)
    inferred_root = _sanitize_topic_label(inferred_root, min_tokens=1, max_tokens=6)
    protocol_subject_root = _protocol_subject_root_from_title(protocol_title)
    preferred_action_root = _preferred_root_from_action(
        _topic_action_from_texts(topic_name, summary_he, request_subject or "", subject_topic or "")
    )
    return (
        protocol_subject_root
        or (root_topic if _is_substantive_protocol_root(root_topic) else None)
        or (inferred_root if _is_substantive_protocol_root(inferred_root) else None)
        or preferred_action_root
        or _object_root_from_topic_parts(root_topic="", child_topic=" ".join(bit for bit in [subject_topic, request_subject] if bit))
        or _protocol_title_topic_root(protocol_title)
        or _object_root_from_topic_parts(root_topic=root_topic, child_topic=child_topic)
        or (inferred_root if _is_substantive_protocol_root(inferred_root) else None)
        or (root_topic if _is_substantive_protocol_root(root_topic) else None)
        or "החלטות עירוניות"
    )


def _topic_leaf_candidate_score(
    *,
    candidate: str,
    evidence_text: str,
    embedding_similarity: float | None,
    support_count: int,
    source_bonus: float,
    local_alignment: float,
    non_subject_penalty: float,
) -> float:
    lexical_overlap = _topic_text_overlap_ratio(candidate, evidence_text)
    embedding_score = embedding_similarity if embedding_similarity is not None else 0.0
    support_bonus = min(0.08, max(0, support_count) * 0.02)
    quality_score = _topic_leaf_quality_adjustment(candidate)
    return (0.52 * embedding_score) + (0.22 * lexical_overlap) + (0.18 * local_alignment) + support_bonus + quality_score + source_bonus - (0.22 * non_subject_penalty)


def _best_topic_leaf_label(
    *,
    root_topic: str,
    current_child: str,
    summary_he: str,
    chunk_text: str,
    request_subject_he: str | None,
    subject_topic_he: str | None,
    agenda_item_he: str | None,
    decision_text_he: str | None,
    structured_candidates: list[str],
    semantic_candidates: list[dict[str, Any]],
    chunk_vector: list[float] | None,
    embedding_service: ChunkEmbeddingService | None,
    candidate_vector_cache: dict[str, list[float]],
) -> str | None:
    evidence_text = " ".join(
        bit
        for bit in [summary_he, chunk_text, agenda_item_he or "", decision_text_he or "", request_subject_he or "", subject_topic_he or ""]
        if str(bit).strip()
    )
    candidates_by_key: dict[str, dict[str, Any]] = {}
    reference_labels = [
        label
        for label in [*structured_candidates, agenda_item_he or "", decision_text_he or "", subject_topic_he or "", request_subject_he or ""]
        if _clean_topic_candidate_phrase(label)
    ]
    reference_labels.extend(
        [str(row.get("label") or "") for row in semantic_candidates if _clean_topic_candidate_phrase(str(row.get("label") or ""))]
    )

    def add_candidate(label: str, *, source: str, support_count: int = 0) -> None:
        candidate = _sanitize_topic_label(_strip_topic_entity_detail(label), min_tokens=2, max_tokens=6)
        if not candidate:
            return
        candidate_root = _object_root_from_topic_parts(root_topic="", child_topic=candidate)
        if candidate_root and candidate_root != root_topic:
            return
        key = normalize_for_search(candidate)
        if not key:
            return
        bucket = candidates_by_key.setdefault(
            key,
            {"label": candidate, "support_count": 0, "sources": set(), "source_bonus": 0.0},
        )
        bucket["label"] = candidate
        bucket["support_count"] = max(int(bucket["support_count"]), int(support_count))
        bucket["source_bonus"] = max(
            float(bucket.get("source_bonus") or 0.0),
            float(TOPIC_CANDIDATE_SOURCE_BONUS.get(source, 0.0)),
        )
        sources = bucket.get("sources")
        if isinstance(sources, set):
            sources.add(source)

    add_candidate(current_child, source="current")
    add_candidate(agenda_item_he or "", source="agenda_item")
    add_candidate(decision_text_he or "", source="decision_text")
    for structured_candidate in structured_candidates:
        add_candidate(structured_candidate, source="structured")
    add_candidate(_compact_request_subject_for_topic(request_subject_he or "") or "", source="request_subject")
    add_candidate(subject_topic_he or "", source="subject_topic")

    for row in semantic_candidates[:TOPIC_LEAF_SEMANTIC_CANDIDATE_LIMIT]:
        add_candidate(str(row.get("label") or ""), source="semantic_node", support_count=int(row.get("support_count") or 0))

    if not candidates_by_key:
        return None

    texts_to_embed = [
        payload["label"]
        for payload in candidates_by_key.values()
        if payload["label"] not in candidate_vector_cache
    ]
    if embedding_service is not None and embedding_service.is_enabled() and texts_to_embed:
        try:
            vectors = embedding_service.model_client.embed_texts(texts_to_embed)
        except Exception:  # noqa: BLE001
            vectors = []
        if len(vectors) == len(texts_to_embed):
            for text, vector in zip(texts_to_embed, vectors, strict=True):
                candidate_vector_cache[text] = [float(value) for value in vector]

    ranked: list[tuple[float, str]] = []
    score_by_key: dict[str, float] = {}
    current_key = normalize_for_search(_sanitize_topic_label(current_child, min_tokens=2, max_tokens=6))
    current_score: float | None = None
    for key, payload in candidates_by_key.items():
        label = str(payload["label"])
        vector = candidate_vector_cache.get(label)
        payload_sources = payload.get("sources")
        has_request_subject_source = isinstance(payload_sources, set) and "request_subject" in payload_sources
        embedding_similarity = _cosine_similarity_api(chunk_vector, vector) if chunk_vector is not None and vector is not None else None
        score = _topic_leaf_candidate_score(
            candidate=label,
            evidence_text=evidence_text,
            embedding_similarity=embedding_similarity,
            support_count=int(payload.get("support_count") or 0),
            source_bonus=float(payload.get("source_bonus") or 0.0),
            local_alignment=_topic_label_alignment_score(
                label,
                reference_labels=reference_labels,
                embedding_service=embedding_service,
                candidate_vector_cache=candidate_vector_cache,
            ),
            non_subject_penalty=_topic_non_subject_similarity(
                label,
                embedding_service=embedding_service,
                candidate_vector_cache=candidate_vector_cache,
            ),
        )
        if key == current_key:
            current_score = score
        if embedding_similarity is not None and embedding_similarity < TOPIC_LEAF_EMBED_MIN_SIMILARITY and not has_request_subject_source:
            continue
        score_by_key[key] = score
        ranked.append((score, label))

    if not ranked:
        return _sanitize_topic_label(current_child, min_tokens=2, max_tokens=6) or None

    ranked.sort(key=lambda row: row[0], reverse=True)
    best_score, best_label = ranked[0]
    if _topic_action_from_texts(best_label) is None:
        best_core_tokens = _topic_core_object_tokens(best_label)
        richer_action_candidates: list[tuple[float, str]] = []
        for candidate_score, candidate_label in ranked[1:]:
            candidate_action = _topic_action_from_texts(candidate_label)
            candidate_core_tokens = _topic_core_object_tokens(candidate_label)
            if (
                candidate_action
                and not _is_procedural_topic_phrase(candidate_label)
                and not _topic_leaf_is_low_quality(candidate_label)
                and best_core_tokens
                and candidate_core_tokens
                and best_core_tokens.intersection(candidate_core_tokens)
            ):
                richer_action_candidates.append((candidate_score, candidate_label))
        if richer_action_candidates:
            richer_action_candidates.sort(key=lambda row: row[0], reverse=True)
            action_score, action_label = richer_action_candidates[0]
            if (best_score - action_score) <= 0.22:
                best_label = action_label
                best_score = action_score
    best_action = _topic_action_from_texts(best_label)
    if best_action and normalize_for_search(best_label) == normalize_for_search(best_action):
        richer_structured = []
        best_alignment = _topic_label_alignment_score(
            best_label,
            reference_labels=reference_labels,
            embedding_service=embedding_service,
            candidate_vector_cache=candidate_vector_cache,
        )
        for structured_candidate in structured_candidates:
            candidate_label = _clean_topic_candidate_phrase(structured_candidate)
            candidate_score = score_by_key.get(normalize_for_search(candidate_label))
            candidate_action = _topic_action_from_texts(candidate_label)
            if (
                candidate_label
                and candidate_score is not None
                and candidate_action
                and normalize_for_search(candidate_action) == normalize_for_search(best_action)
                and normalize_for_search(candidate_label) != normalize_for_search(best_label)
                and not _topic_leaf_is_low_quality(candidate_label)
            ):
                richer_structured.append(
                    (
                        _topic_label_alignment_score(
                            candidate_label,
                            reference_labels=reference_labels,
                            embedding_service=embedding_service,
                            candidate_vector_cache=candidate_vector_cache,
                        ),
                        candidate_score,
                        candidate_label,
                    )
                )
        if richer_structured:
            richer_structured.sort(key=lambda row: (row[0], row[1]), reverse=True)
            richer_alignment, richer_score, richer_label = richer_structured[0]
            if richer_alignment >= max(best_alignment, TOPIC_LOCAL_ALIGNMENT_MIN):
                best_label = richer_label
                best_score = richer_score
                best_action = _topic_action_from_texts(best_label)

    best_tokens = set(_topic_text_tokens(best_label))
    if best_action and best_tokens:
        broader_candidates: list[tuple[float, str]] = []
        for candidate_score, candidate_label in ranked[1:]:
            candidate_action = _topic_action_from_texts(candidate_label)
            candidate_tokens = set(_topic_text_tokens(candidate_label))
            if (
                candidate_action
                and normalize_for_search(candidate_action) == normalize_for_search(best_action)
                and candidate_tokens
                and candidate_tokens.issubset(best_tokens)
                and normalize_for_search(candidate_label) != normalize_for_search(best_label)
                and not _topic_leaf_is_low_quality(candidate_label)
            ):
                extra_tokens = best_tokens - candidate_tokens
                if extra_tokens and extra_tokens.issubset(TOPIC_OBJECT_DETAIL_TOKENS.union(TOPIC_PROCEDURAL_SUBJECT_TOKENS)):
                    broader_candidates.append((candidate_score, candidate_label))
        if broader_candidates:
            broader_candidates.sort(key=lambda row: row[0], reverse=True)
            broader_score, broader_label = broader_candidates[0]
            if (best_score - broader_score) <= 0.25:
                best_label = broader_label
                best_score = broader_score
                best_action = _topic_action_from_texts(best_label)
    if structured_candidates:
        preferred_structured = ""
        preferred_score: float | None = None
        for structured_candidate in structured_candidates:
            candidate_label = _clean_topic_candidate_phrase(structured_candidate)
            candidate_score = score_by_key.get(normalize_for_search(candidate_label))
            if not candidate_label or candidate_score is None:
                continue
            if preferred_score is None or candidate_score > preferred_score:
                preferred_structured = candidate_label
                preferred_score = candidate_score
        if preferred_structured and preferred_score is not None:
            best_action = _topic_action_from_texts(best_label)
            preferred_action = _topic_action_from_texts(preferred_structured)
            best_tokens = set(_topic_text_tokens(best_label))
            preferred_tokens = set(_topic_text_tokens(preferred_structured))
            best_detail_count = len(set(_topic_text_tokens(best_label)).intersection(TOPIC_OBJECT_DETAIL_TOKENS))
            preferred_detail_count = len(set(_topic_text_tokens(preferred_structured)).intersection(TOPIC_OBJECT_DETAIL_TOKENS))
            if (
                (
                    (
                        best_action
                        and preferred_action
                        and normalize_for_search(best_action) == normalize_for_search(preferred_action)
                    )
                    or (preferred_tokens and preferred_tokens.issubset(best_tokens) and len(best_tokens - preferred_tokens) <= 2)
                )
                and (
                    best_detail_count > preferred_detail_count
                    or (preferred_tokens and preferred_tokens.issubset(best_tokens) and len(best_tokens - preferred_tokens) <= 2)
                )
                and (best_score - preferred_score) <= 0.3
            ):
                best_label = preferred_structured
                best_score = preferred_score
    current_label = _sanitize_topic_label(current_child, min_tokens=2, max_tokens=6) or ""
    current_action = _topic_action_from_texts(current_label)
    current_is_action_only = bool(
        current_label
        and current_action
        and normalize_for_search(current_label) == normalize_for_search(current_action)
    )
    if current_label and current_score is not None and normalize_for_search(best_label) != normalize_for_search(current_label):
        if (
            not current_is_action_only
            and not _topic_leaf_is_low_quality(current_label)
            and (best_score - current_score) < TOPIC_LEAF_SCORE_MARGIN
        ):
            return current_label
    return best_label


def _merge_root_payload_into_target(*, target: dict[str, Any], source: dict[str, Any]) -> None:
    target["count"] += int(source["count"])
    target["protocols"].update(source["protocols"])

    source_last_seen = source.get("last_seen_at")
    target_last_seen = target.get("last_seen_at")
    if source_last_seen and (not target_last_seen or str(source_last_seen) > str(target_last_seen)):
        target["last_seen_at"] = source_last_seen

    for topic, source_variant in source["variants"].items():
        target_variant = target["variants"].setdefault(
            topic,
            {
                "count": 0,
                "last_seen_at": None,
                "protocols": set(),
                "avg_granularity": 0.0,
                "samples": 0,
            },
        )
        previous_samples = int(target_variant["samples"])
        source_samples = int(source_variant["samples"])
        total_samples = previous_samples + source_samples
        if total_samples > 0:
            target_variant["avg_granularity"] = (
                (float(target_variant["avg_granularity"]) * previous_samples)
                + (float(source_variant["avg_granularity"]) * source_samples)
            ) / total_samples
        target_variant["samples"] = total_samples
        target_variant["count"] += int(source_variant["count"])
        target_variant["protocols"].update(source_variant["protocols"])

        source_variant_last_seen = source_variant.get("last_seen_at")
        target_variant_last_seen = target_variant.get("last_seen_at")
        if source_variant_last_seen and (
            not target_variant_last_seen or str(source_variant_last_seen) > str(target_variant_last_seen)
        ):
            target_variant["last_seen_at"] = source_variant_last_seen


def _topic_same_action_family(left: str, right: str) -> bool:
    left_action = _topic_action_from_texts(left)
    right_action = _topic_action_from_texts(right)
    return bool(
        left_action
        and right_action
        and normalize_for_search(left_action) == normalize_for_search(right_action)
    )


def _topic_is_action_only(label: str) -> bool:
    action = _topic_action_from_texts(label)
    return bool(action and normalize_for_search(label) == normalize_for_search(action))


def _topic_core_object_tokens(label: str) -> set[str]:
    cleaned = _clean_topic_candidate_phrase(label)
    tokens = set(_topic_text_tokens(cleaned))
    action_tokens = set(_topic_text_tokens(_topic_action_from_texts(cleaned) or ""))
    return {
        token
        for token in tokens
        if token not in action_tokens
        and token not in TOPIC_PROCEDURAL_SUBJECT_TOKENS
        and token not in TOPIC_REQUEST_GENERIC_LEAD_TOKENS
        and token not in TOPIC_LEAF_ENTITY_LEAD_TOKENS
        and token not in TOPIC_LEAF_RELATIONAL_LEAD_TOKENS
    }


def _topic_labels_should_merge(
    left: str,
    right: str,
    *,
    embedding_service: ChunkEmbeddingService | None,
    candidate_vector_cache: dict[str, list[float]],
) -> bool:
    left_clean = _clean_topic_candidate_phrase(left)
    right_clean = _clean_topic_candidate_phrase(right)
    if not left_clean or not right_clean:
        return False
    if normalize_for_search(left_clean) == normalize_for_search(right_clean):
        return True

    similarity = _topic_concept_similarity(
        left_clean,
        right_clean,
        embedding_service=embedding_service,
        candidate_vector_cache=candidate_vector_cache,
    )
    if similarity >= TOPIC_SEMANTIC_EMBED_MERGE_THRESHOLD:
        return True
    if similarity >= TOPIC_SEMANTIC_MERGE_THRESHOLD:
        return True

    left_tokens = set(_topic_text_tokens(left_clean))
    right_tokens = set(_topic_text_tokens(right_clean))
    if not left_tokens or not right_tokens:
        return False

    same_action_family = _topic_same_action_family(left_clean, right_clean)
    if same_action_family:
        if _topic_is_action_equivalent(left_clean) and _topic_is_action_equivalent(right_clean):
            return True
        if left_tokens.issubset(right_tokens) or right_tokens.issubset(left_tokens):
            return True
        if (_topic_is_action_only(left_clean) or _topic_is_action_only(right_clean)) and (
            _topic_leaf_is_low_quality(left_clean) or _topic_leaf_is_low_quality(right_clean)
        ):
            return similarity >= 0.55

    overlap = len(left_tokens.intersection(right_tokens)) / max(1, min(len(left_tokens), len(right_tokens)))
    left_core_tokens = _topic_core_object_tokens(left_clean)
    right_core_tokens = _topic_core_object_tokens(right_clean)
    if left_core_tokens and right_core_tokens and left_core_tokens.intersection(right_core_tokens) and (
        left_core_tokens.issubset(right_core_tokens) or right_core_tokens.issubset(left_core_tokens)
    ) and (
        _is_procedural_topic_phrase(left_clean)
        or _is_procedural_topic_phrase(right_clean)
        or _topic_leaf_is_low_quality(left_clean)
        or _topic_leaf_is_low_quality(right_clean)
    ):
        return True
    if overlap >= 0.5 and similarity >= 0.6 and (
        _is_procedural_topic_phrase(left_clean)
        or _is_procedural_topic_phrase(right_clean)
        or _topic_leaf_is_low_quality(left_clean)
        or _topic_leaf_is_low_quality(right_clean)
    ):
        return True
    return False


def _merge_tree_node_payload(*, target: dict[str, Any], source: dict[str, Any], add_counts: bool) -> None:
    if add_counts:
        previous_samples = int(target.get("samples") or 0)
        source_samples = int(source.get("samples") or 0)
        total_samples = previous_samples + source_samples
        target["count"] += int(source.get("count") or 0)
        if total_samples > 0:
            target["avg_granularity"] = (
                (float(target.get("avg_granularity") or 0.0) * previous_samples)
                + (float(source.get("avg_granularity") or 0.0) * source_samples)
            ) / total_samples
        target["samples"] = total_samples

    target["protocols"].update(source.get("protocols", set()))
    source_last_seen = source.get("last_seen_at")
    if source_last_seen and (not target.get("last_seen_at") or str(source_last_seen) > str(target.get("last_seen_at"))):
        target["last_seen_at"] = source_last_seen

    for doc_id, payload in source.get("protocol_links", {}).items():
        target.setdefault("protocol_links", {}).setdefault(doc_id, payload)
    for doc_id, payload in source.get("support_documents", {}).items():
        target.setdefault("support_documents", {}).setdefault(doc_id, payload)

    target_children = target.setdefault("children", {})
    for child_topic, child_node in source.get("children", {}).items():
        existing_child = target_children.get(child_topic)
        if existing_child is None:
            target_children[child_topic] = child_node
            continue
        _merge_tree_node_payload(target=existing_child, source=child_node, add_counts=True)


def _normalize_topic_tree_node(
    node: dict[str, Any],
    *,
    parent_topic: str | None,
    embedding_service: ChunkEmbeddingService | None,
    candidate_vector_cache: dict[str, list[float]],
) -> dict[str, Any]:
    raw_children = list(node.get("children", {}).values())
    normalized_children = [
        _normalize_topic_tree_node(
            child,
            parent_topic=str(node.get("topic") or "") if str(node.get("topic") or "") != "__root__" else None,
            embedding_service=embedding_service,
            candidate_vector_cache=candidate_vector_cache,
        )
        for child in raw_children
    ]

    merged_children: list[dict[str, Any]] = []
    for child in sorted(normalized_children, key=lambda item: int(item.get("count") or 0), reverse=True):
        merged = False
        for existing in merged_children:
            if _topic_labels_should_merge(
                str(existing.get("topic") or ""),
                str(child.get("topic") or ""),
                embedding_service=embedding_service,
                candidate_vector_cache=candidate_vector_cache,
            ):
                preferred = _prefer_topic_label(
                    str(existing.get("topic") or ""),
                    str(child.get("topic") or ""),
                    left_support_count=int(existing.get("count") or 0),
                    right_support_count=int(child.get("count") or 0),
                )
                existing["topic"] = preferred
                _merge_tree_node_payload(target=existing, source=child, add_counts=True)
                merged = True
                break
        if not merged:
            merged_children.append(child)

    collapsed_children: list[dict[str, Any]] = []
    for child in merged_children:
        parent_label = str(node.get("topic") or "")
        if parent_topic is not None and parent_label and _topic_labels_should_merge(
            parent_label,
            str(child.get("topic") or ""),
            embedding_service=embedding_service,
            candidate_vector_cache=candidate_vector_cache,
        ):
            node["topic"] = _prefer_topic_label(
                parent_label,
                str(child.get("topic") or ""),
                left_support_count=int(node.get("count") or 0),
                right_support_count=int(child.get("count") or 0),
            )
            _merge_tree_node_payload(target=node, source=child, add_counts=False)
            continue
        collapsed_children.append(child)

    node["children"] = {str(child.get("topic") or ""): child for child in collapsed_children if str(child.get("topic") or "")}
    return node


def _coalesce_sparse_single_token_roots(global_roots: dict[str, dict[str, Any]]) -> None:
    root_topics = list(global_roots.keys())
    for topic in root_topics:
        source_payload = global_roots.get(topic)
        if source_payload is None:
            continue

        source_tokens = _topic_merge_tokens(topic)
        if len(source_tokens) != 1:
            continue
        if int(source_payload["count"]) > 2:
            continue

        token = source_tokens[0]
        best_target_topic: str | None = None
        best_target_score = -1.0
        for candidate_topic, candidate_payload in global_roots.items():
            if candidate_topic == topic:
                continue
            candidate_tokens = _topic_merge_tokens(candidate_topic)
            if len(candidate_tokens) < 2:
                continue
            if token not in set(candidate_tokens):
                continue

            protocol_overlap = len(source_payload["protocols"].intersection(candidate_payload["protocols"]))
            score = float(candidate_payload["count"]) + (0.6 * protocol_overlap)
            if score > best_target_score:
                best_target_score = score
                best_target_topic = candidate_topic

        if not best_target_topic:
            continue
        target_payload = global_roots.get(best_target_topic)
        if target_payload is None:
            continue
        _merge_root_payload_into_target(target=target_payload, source=source_payload)
        global_roots.pop(topic, None)


def _infer_topic_path_from_summary(*, summary_he: str, protocol_title: str) -> str | None:
    summary_norm = normalize_for_search(summary_he)
    title_norm = normalize_for_search(protocol_title)
    combined = f"{summary_norm} {title_norm}".strip()
    if not combined:
        return None

    if "הסכם" in combined or "חוזה" in combined or "רשות" in combined:
        child = "הסכם רשות"
        if any(token in combined for token in {"לעמותה", "עמותה", "עמותת", "עמותות"}):
            child = "הסכם רשות לעמותה"
        elif any(token in combined for token in {"לעירייה", "עירייה", "העירייה"}):
            child = "הסכם רשות לעירייה"
        return f"הסכמים > {child}"

    if "הקצאה" in combined or "הקצאות" in combined or "עמותה" in combined:
        return "הקצאות > הקצאה לעמותה"

    if "תמרור" in combined or "תמרורים" in combined:
        if "מוארים" in combined:
            return "תמרורים > תמרורים מוארים"
        if "קיימים" in combined:
            return "תמרורים > תמרורים קיימים"
        return "תמרורים > תמרורים עירוניים"

    if "ניקיון" in combined:
        return "ניקיון > ניקיון מוסדות"

    if "אבטחה" in combined:
        return "אבטחה > אבטחת מוסדות"

    return "החלטות עירוניות > החלטה ענפית"


PROTOCOL_HEADLINE_RE = re.compile(r"(?:^|\n)\s*([^:\n]{6,120})\s*:")
IGNORED_PROTOCOL_HEADLINES = {
    "מהלך הדיון",
    "החלטות",
    "נוכחים",
    "נעדרו",
    "סיכום והחלטות",
    "השתתפו",
    "נושא הוועדה",
}
HEADLINE_VALUE_FROM_RIGHT_LABELS = {
    "נושא הוועדה",
    "נושא",
    "נושא הדיון",
    "מהות הבקשה",
}
IGNORED_HEADLINE_PREFIXES = (
    "להלן",
    "זומנו",
    "נוכחים",
    "נעדרו",
    "השתתפו",
    "החלטות",
    "סיכום",
    "מהלך",
)
TOPIC_CUE_TOKENS = {
    "פינויים",
    "פינוי",
    "מתחם",
    "רובע",
    "בטיחות",
    "תמרורים",
    "תמרור",
    "חציה",
    "התמכרות",
    "סמים",
    "סם",
    "תרופות",
    "קופות",
    "בריאות",
    "הכשרה",
    "הכשרות",
    "חינוך",
    "תחבורה",
    "תנועה",
    "עמותת",
    "עמותות",
    "הסכם",
    "הסכמים",
    "חוזה",
    "חוזים",
    "הקצאה",
    "הקצאות",
    "בקשה",
    "בקשות",
    "רשות",
}

def _normalize_headline_text(value: str) -> str:
    cleaned = " ".join(str(value or "").split())
    cleaned = cleaned.replace('"', "").replace("׳", "'").replace("״", "")
    cleaned = cleaned.strip("-–:;,.()[]{} ")
    return cleaned


def _is_ignored_headline(value: str) -> bool:
    normalized = _normalize_headline_text(value)
    if not normalized:
        return True
    if normalized in IGNORED_PROTOCOL_HEADLINES:
        return True
    lowered = normalized.casefold()
    if lowered.startswith("לגבי"):
        return True
    if lowered.startswith(IGNORED_HEADLINE_PREFIXES):
        return True
    return False


def _headline_has_topic_cue(value: str) -> bool:
    normalized = normalize_for_search(value)
    if not normalized:
        return False

    tokens = [token for token in normalized.split(" ") if token]
    if len(tokens) < 2:
        return False
    return any(token in TOPIC_CUE_TOKENS for token in tokens)


def _headline_candidates_from_chunk_text(text: str) -> list[str]:
    if not text:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for raw_line in str(text).splitlines():
        line = " ".join(raw_line.split())
        if not line or ":" not in line:
            continue

        left, right = line.split(":", 1)
        left_norm = _normalize_headline_text(left)
        right_norm = _normalize_headline_text(right)

        candidate = left_norm
        if _normalize_headline_text(left_norm) in HEADLINE_VALUE_FROM_RIGHT_LABELS and right_norm:
            candidate = right_norm

        candidate = _normalize_headline_text(candidate)
        if not candidate:
            continue
        if _is_ignored_headline(candidate):
            continue
        if len(candidate) > 80:
            continue
        if not _headline_has_topic_cue(candidate):
            continue

        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)

    if not out:
        for match in PROTOCOL_HEADLINE_RE.finditer(text):
            candidate = _normalize_headline_text(match.group(1))
            if not candidate or _is_ignored_headline(candidate):
                continue
            if not _headline_has_topic_cue(candidate):
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate)
    return out


def _decision_request_context_payload(*, db, decision_id: int) -> dict[str, Any] | None:
    if not inspect(db.get_bind()).has_table("decision_request_context"):
        return None

    row = db.execute(
        select(DecisionRequestContext).where(DecisionRequestContext.decision_id == decision_id)
    ).scalar_one_or_none()
    if row is None:
        return None

    source_artifact_ids = _loads_json(row.source_artifact_ids_json)
    metadata = _loads_json(row.metadata_json)
    normalized_artifact_ids = [str(item).strip() for item in source_artifact_ids] if isinstance(source_artifact_ids, list) else []
    if not normalized_artifact_ids and isinstance(metadata, dict):
        raw_artifact_ids = metadata.get("source_artifact_ids")
        if isinstance(raw_artifact_ids, list):
            normalized_artifact_ids = [str(item).strip() for item in raw_artifact_ids if str(item).strip()]
    return {
        "request_subject_he": row.request_subject_he,
        "subject_topic_he": row.subject_topic_he,
        "address_he": row.address_he,
        "gush": row.gush,
        "helka": row.helka,
        "migrash": row.migrash,
        "source_artifact_ids": normalized_artifact_ids,
        "confidence": row.confidence,
    }


def _load_semantic_topic_candidate_bank(*, db) -> list[dict[str, Any]]:
    rows = db.execute(
        select(SemanticNode.pref_label_he, SemanticNode.support_count)
        .where(SemanticNode.node_kind == "topic")
        .order_by(SemanticNode.support_count.desc(), SemanticNode.id.asc())
    ).all()

    deduped: dict[str, dict[str, Any]] = {}
    for label_he, support_count in rows:
        label = _sanitize_topic_label(str(label_he or ""), min_tokens=2, max_tokens=6)
        if not label:
            continue
        key = normalize_for_search(label)
        if not key:
            continue
        existing = deduped.get(key)
        if existing is None or int(support_count or 0) > int(existing.get("support_count") or 0):
            deduped[key] = {
                "label": label,
                "support_count": int(support_count or 0),
            }
    return list(deduped.values())


def _is_procedural_topic_phrase(value: str) -> bool:
    tokens = _topic_text_tokens(value)
    if not tokens:
        return False
    overlap = len(set(tokens).intersection(TOPIC_PROCEDURAL_SUBJECT_TOKENS))
    return overlap >= 2 or (overlap >= 1 and len(tokens) <= 4)


def _allocation_asset_kind(*, action: str | None, metadata_fields: list[dict[str, Any]]) -> str | None:
    for field in metadata_fields:
        object_value = normalize_for_search(str(field.get("object") or ""))
        if object_value == normalize_for_search("מבנה"):
            return "מבנה"
        if object_value == normalize_for_search("קרקע"):
            return "קרקע"

    action_norm = normalize_for_search(action or "")
    if "קרקע" in action_norm:
        return "קרקע"
    if "מבנה" in action_norm:
        return "מבנה"
    return None


def _allocation_category_segment(*, asset_kind: str | None, leaf_topic: str) -> str | None:
    if asset_kind == "מבנה":
        return "הקצאות מבנים"
    if asset_kind == "קרקע" and normalize_for_search(leaf_topic) != normalize_for_search("הקצאת קרקע"):
        return "הקצאות קרקע"
    return None


def _structured_topic_candidates_for_row(
    *,
    root_topic: str,
    current_child: str,
    summary_he: str,
    request_subject_he: str | None,
    agenda_item_he: str | None,
    decision_text_he: str | None,
    metadata_fields: list[dict[str, Any]],
    local_semantic_candidates: list[dict[str, Any]],
) -> list[str]:
    action = _topic_action_from_texts(
        current_child,
        summary_he,
        request_subject_he or "",
        agenda_item_he or "",
        decision_text_he or "",
    )
    local_context_text = " ".join(
        bit
        for bit in [current_child, summary_he, request_subject_he or "", agenda_item_he or "", decision_text_he or ""]
        if str(bit).strip()
    )
    out: list[str] = []
    seen: set[str] = set()

    def add_candidate(candidate: str | None) -> None:
        cleaned = _clean_topic_candidate_phrase(candidate or "")
        if not cleaned or _looks_generic_topic_candidate(cleaned):
            return
        key = normalize_for_search(cleaned)
        if not key or key in seen:
            return
        seen.add(key)
        out.append(cleaned)

    scored_local_candidates = [
        field
        for field in [*metadata_fields, *local_semantic_candidates]
        if (
            str(field.get("subject") or "").strip()
            and float(field.get("score") or 0.0) >= 0.95
            and not (
                _topic_leaf_is_low_quality(str(field.get("subject") or ""))
                and _topic_leaf_is_low_quality(str(field.get("object") or field.get("subject") or ""))
            )
        )
    ]
    scored_local_candidates.sort(
        key=lambda field: (
            float(field.get("score") or 0.0)
            + _topic_text_overlap_ratio(str(field.get("subject") or field.get("object") or ""), local_context_text)
            + _topic_text_overlap_ratio(str(field.get("object") or field.get("subject") or ""), local_context_text)
        ),
        reverse=True,
    )
    subject_candidates = [str(field.get("subject") or "") for field in scored_local_candidates[:TOPIC_LOCAL_SUBJECT_MAX_CANDIDATES]]

    if normalize_for_search(root_topic) == normalize_for_search("הקצאות"):
        asset_kind = _allocation_asset_kind(action=action, metadata_fields=metadata_fields)
        for field in scored_local_candidates:
            object_value = str(field.get("object") or field.get("subject") or field.get("value") or "")
            if action:
                add_candidate(_compose_topic_from_action_and_object(action=action, object_value=object_value))
            elif asset_kind == "מבנה":
                add_candidate(_compose_topic_from_action_and_object(action="הקצאת מבנה", object_value=object_value))
            elif asset_kind == "קרקע":
                add_candidate(_compose_topic_from_action_and_object(action="הקצאת קרקע", object_value=object_value))

        if action and asset_kind == "מבנה" and (
            _is_procedural_topic_phrase(current_child)
            or _is_procedural_topic_phrase(summary_he)
            or normalize_for_search(action)
            in {
                normalize_for_search("רשות שימוש"),
                normalize_for_search("הסדרת שימוש"),
                normalize_for_search("הסכם רשות"),
            }
        ):
            for subject_candidate in subject_candidates:
                add_candidate(_compose_topic_from_action_and_object(action="הקצאת מבנה", object_value=subject_candidate))
        elif action and asset_kind == "קרקע":
            for subject_candidate in subject_candidates:
                add_candidate(_compose_topic_from_action_and_object(action="הקצאת קרקע", object_value=subject_candidate))
        if not out:
            for subject_candidate in subject_candidates:
                add_candidate(subject_candidate)
        return out

    for subject_candidate in subject_candidates:
        add_candidate(subject_candidate)

    if action:
        for field in scored_local_candidates:
            object_value = str(field.get("object") or field.get("subject") or field.get("value") or "")
            add_candidate(_compose_topic_from_action_and_object(action=action, object_value=object_value))

    if not out and action:
        add_candidate(action)
    return out


def _topic_path_for_row(
    *,
    root_topic: str,
    leaf_topic: str,
    current_child: str,
    summary_he: str,
    request_subject_he: str | None,
    subject_topic_he: str | None,
    agenda_item_he: str | None,
    decision_text_he: str | None,
    metadata_fields: list[dict[str, Any]],
) -> list[str]:
    canonical_leaf = _canonicalize_topic_child(root_topic=root_topic, child_topic=leaf_topic)
    segments = [root_topic]

    if normalize_for_search(root_topic) == normalize_for_search("הקצאות"):
        action = _topic_action_from_texts(
            canonical_leaf,
            current_child,
            summary_he,
            request_subject_he or "",
            subject_topic_he or "",
            agenda_item_he or "",
            decision_text_he or "",
        )
        category_segment = _allocation_category_segment(
            asset_kind=_allocation_asset_kind(action=action, metadata_fields=metadata_fields),
            leaf_topic=canonical_leaf,
        )
        if category_segment:
            segments.append(category_segment)

    if normalize_for_search(canonical_leaf) != normalize_for_search(segments[-1]):
        segments.append(canonical_leaf)
    return segments


def _canonicalize_topic_cache_row(
    *,
    topic_name: str,
    protocol_title: str,
    summary_he: str,
    chunk_id: str,
    chunk_meta_by_id: dict[str, dict[str, Any]],
    neighbor_fields_by_chunk: dict[str, list[dict[str, Any]]],
    request_context_by_chunk: dict[str, dict[str, Any]],
    local_semantic_candidates_by_chunk: dict[str, list[dict[str, Any]]],
    embedding_service: ChunkEmbeddingService | None,
    chunk_vectors_by_id: dict[str, list[float]],
    candidate_vector_cache: dict[str, list[float]],
) -> str:
    current_topic = str(topic_name or "").strip()
    if normalize_for_search(current_topic) in {
        "",
        normalize_for_search(PLACEHOLDER_TOPIC_LABEL),
        normalize_for_search("נושא כללי"),
    }:
        inferred = _infer_topic_path_from_summary(summary_he=str(summary_he or ""), protocol_title=str(protocol_title or ""))
        if inferred:
            current_topic = inferred

    current_root, current_child = _split_topic_path(current_topic)
    current_child = _sanitize_topic_label(current_child, min_tokens=2, max_tokens=6)
    if not current_child:
        current_child = _sanitize_topic_label(current_root, min_tokens=2, max_tokens=6)

    chunk_key = str(chunk_id or "").strip()
    chunk_meta = chunk_meta_by_id.get(chunk_key, {})
    request_context = request_context_by_chunk.get(chunk_key, {})
    metadata_fields = neighbor_fields_by_chunk.get(chunk_key, [])
    local_semantic_candidates = local_semantic_candidates_by_chunk.get(chunk_key, [])
    request_subject_he = str(request_context.get("request_subject_he") or "").strip() or None
    subject_topic_he = str(request_context.get("subject_topic_he") or "").strip() or None
    agenda_item_he = str(request_context.get("agenda_item") or "").strip() or None
    decision_text_he = str(request_context.get("decision_text") or "").strip() or None
    root_topic = _topic_leaf_root_for_row(
        topic_name=current_topic,
        protocol_title=str(protocol_title or ""),
        summary_he=str(summary_he or ""),
        request_subject_he=request_subject_he,
        subject_topic_he=subject_topic_he,
    )
    agenda_root_candidate = _clean_topic_candidate_phrase(agenda_item_he or "")
    if agenda_root_candidate and _is_substantive_protocol_root(agenda_root_candidate):
        root_topic_norm = normalize_for_search(root_topic)
        agenda_root_norm = normalize_for_search(agenda_root_candidate)
        if root_topic_norm.startswith(agenda_root_norm) or _topic_leaf_is_low_quality(root_topic):
            root_topic = agenda_root_candidate
    allocation_root_signal = normalize_for_search(
        " ".join(bit for bit in [current_topic, request_subject_he or "", subject_topic_he or ""] if str(bit).strip())
    )
    if (
        normalize_for_search(root_topic) == normalize_for_search("הסכמים")
        and any(token in allocation_root_signal for token in {"הקצא", "שימוש"})
        and _allocation_asset_kind(action=_topic_action_from_texts(current_topic, request_subject_he or "", subject_topic_he or ""), metadata_fields=metadata_fields)
    ):
        root_topic = "הקצאות"
    structured_candidates = _structured_topic_candidates_for_row(
        root_topic=root_topic,
        current_child=current_child,
        summary_he=str(summary_he or ""),
        request_subject_he=request_subject_he,
        agenda_item_he=agenda_item_he,
        decision_text_he=decision_text_he,
        metadata_fields=metadata_fields,
        local_semantic_candidates=local_semantic_candidates,
    )
    if structured_candidates:
        current_child_norm = normalize_for_search(current_child)
        current_action = _topic_action_from_texts(current_child)
        if current_action and current_child_norm == normalize_for_search(current_action):
            current_child = structured_candidates[0]
        elif any(token in current_child_norm for token in TOPIC_OBJECT_DETAIL_TOKENS):
            current_child = structured_candidates[0]

    semantic_candidates = [
        {
            "label": str(candidate.get("subject") or ""),
            "support_count": int(max(1, round(float(candidate.get("score") or 0.0) * 10))),
        }
        for candidate in local_semantic_candidates
        if str(candidate.get("subject") or "").strip()
    ]
    best_child = _best_topic_leaf_label(
        root_topic=root_topic,
        current_child=current_child,
        summary_he=str(summary_he or ""),
        chunk_text=str(chunk_meta.get("chunk_text") or ""),
        request_subject_he=request_subject_he,
        subject_topic_he=subject_topic_he,
        agenda_item_he=agenda_item_he,
        decision_text_he=decision_text_he,
        structured_candidates=structured_candidates,
        semantic_candidates=semantic_candidates,
        chunk_vector=chunk_vectors_by_id.get(chunk_key),
        embedding_service=embedding_service,
        candidate_vector_cache=candidate_vector_cache,
    )
    if not best_child:
        return root_topic
    if normalize_for_search(root_topic) != normalize_for_search("הקצאות") and (
        _is_procedural_topic_phrase(best_child) or _topic_leaf_is_low_quality(best_child)
    ):
        return root_topic

    path_segments = _topic_path_for_row(
        root_topic=root_topic,
        leaf_topic=best_child,
        current_child=current_child,
        summary_he=str(summary_he or ""),
        request_subject_he=request_subject_he,
        subject_topic_he=subject_topic_he,
        agenda_item_he=agenda_item_he,
        decision_text_he=decision_text_he,
        metadata_fields=metadata_fields,
    )
    if not path_segments:
        return root_topic
    if len(path_segments) == 1:
        return path_segments[0]
    return _join_topic_path(path_segments)


def _run_ask(
    *,
    request: AskRequest,
    db,
    llm_client: RagLlmClient | None = None,
) -> dict:
    request_started = time.perf_counter()
    ask_request_id = new_ask_request_id()
    question_hash = hash_text(request.question)
    arch_config = RagArchitectureConfig.from_env()
    effective_scope = _ask_effective_scope(request, db=db)
    retrieval_query, retrieval_query_debug = _ask_retrieval_query(request=request, effective_scope=effective_scope)
    log_rag_event(
        "rag.ask.request",
        ask_request_id=ask_request_id,
        question_hash=question_hash,
        top_k=request.top_k,
        municipality_slug=request.muni,
        source_types=effective_scope["source_types"],
        required_source_types=effective_scope["required_source_types"],
        year=request.year,
        topic=request.topic,
        semantic_node_id=request.semantic_node_id,
        semantic_label=request.semantic_label,
        semantic_mode=request.semantic_mode,
        retrieval_strategy=request.retrieval_strategy,
        document_ids=effective_scope["document_ids"],
        document_version_ids=effective_scope["document_version_ids"],
        scope_reason=effective_scope["scope_reason"],
    )

    embedding_service = build_embedding_backend(session=db, arch_config=arch_config)
    retrieval_service = RagRetrievalService(
        search_service=build_search_backend(session=db, arch_config=arch_config),
        reranker=EmbeddingReranker(embedding_service),
    )
    retrieval_started = time.perf_counter()
    retrieval_result = retrieval_service.retrieve(
        query=retrieval_query,
        top_k=request.top_k,
        municipality_slug=request.muni,
        source_kinds=effective_scope["source_types"],
        year=request.year,
        topic=request.topic,
        semantic_node_id=request.semantic_node_id,
        semantic_label=request.semantic_label,
        semantic_mode=request.semantic_mode,
        retrieval_strategy=request.retrieval_strategy,
        document_ids=effective_scope["document_ids"],
        document_version_ids=effective_scope["document_version_ids"],
        ask_request_id=ask_request_id,
    )
    retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000.0, 3)

    answering_contexts, reference_enrichment_debug = _enrich_pdf_first_reference_contexts(
        db=db,
        contexts=list(retrieval_result.contexts),
        municipality_slug=request.muni,
        allowed_document_version_ids=_pdf_first_allowed_document_version_ids(),
    )
    answering_contexts = _hydrate_context_semantic_topic_labels(db=db, contexts=answering_contexts)
    answering_contexts, context_dedupe_stats = _collapse_duplicate_answering_contexts(
        embedding_service=embedding_service,
        question=request.question,
        contexts=answering_contexts,
    )
    retrieval_for_answering = RagRetrievalResult(
        query=retrieval_result.query,
        normalized_query=retrieval_result.normalized_query,
        top_k=retrieval_result.top_k,
        retrieval_set_id=retrieval_result.retrieval_set_id,
        requested_source_kinds=list(retrieval_result.requested_source_kinds),
        contexts=answering_contexts,
        debug_info={
            **dict(retrieval_result.debug_info),
            "answer_context_dedupe": context_dedupe_stats,
            "pdf_first_reference_enrichments": reference_enrichment_debug["enrichment_map"],
            "pdf_first_reference_enrichment": reference_enrichment_debug,
            "ask_scope": {
                "scope_reason": effective_scope.get("scope_reason"),
                "date_scope": effective_scope.get("date_scope"),
                "retrieval_query": retrieval_query_debug,
            },
        },
    )
    if set(retrieval_result.requested_source_kinds) != set(PDF_FIRST_ASK_SOURCE_TYPES):
        raise HTTPException(status_code=500, detail="ask_pdf_first_scope_missing")

    resolved_llm_client = llm_client or _build_ask_llm_client(request=request)
    answering_service = RagAnsweringService(llm_client=resolved_llm_client)
    answering_started = time.perf_counter()
    answer_result = answering_service.compose_pdf_first(
        question=request.question,
        retrieval=retrieval_for_answering,
        ask_request_id=ask_request_id,
    )
    answering_ms = round((time.perf_counter() - answering_started) * 1000.0, 3)
    total_ms = round((time.perf_counter() - request_started) * 1000.0, 3)

    limitations = list(answer_result.limitations)
    if answer_result.status == "answer" and len(retrieval_result.source_kinds) <= 1:
        limitations.append("הראיות חלקיות ומבוססות על סוג מקור אחד בלבד.")
    provider_warning = _provider_warning_payload(answer_result)
    if provider_warning is not None and provider_warning["message_he"] not in limitations:
        limitations.insert(0, str(provider_warning["message_he"]))

    citations_payload = [
        {
            "chunk_id": citation.chunk_id,
            "source_type": citation.source_kind,
            "citation": citation.citation_label,
            "start_page": citation.start_page,
            "end_page": citation.end_page,
            "header_path": list(citation.section_path),
            "document": {
                "id": citation.document_id,
                "version_id": citation.document_version_id,
                "title": citation.document_title,
                "url": _citation_document_url(citation),
                "original_url": citation.document_url,
            },
            "score": citation.score,
        }
        for citation in answer_result.citations
    ]

    refusal_payload = None
    if answer_result.status == "refusal":
        refusal_payload = {
            "reason_code": answer_result.refusal_reason_code,
            "message_he": answer_result.refusal_message_he,
            "missing_source_types": answer_result.missing_source_kinds,
        }

    response_payload = {
        "ask_request_id": ask_request_id,
        "status": answer_result.status,
        "answer_mode": _answer_mode(answer_result),
        "question": request.question,
        "answer": answer_result.answer,
        "extended_answer": answer_result.extended_answer,
        "answer_sections": list(answer_result.answer_sections),
        "extended_answer_sections": list(answer_result.extended_answer_sections),
        "citations": citations_payload,
        "claim_assessments": list(answer_result.claim_assessments),
        "limitations": limitations,
        "provider_warning": provider_warning,
        "refusal": refusal_payload,
        "scoring": dict(answer_result.scoring),
        "retrieval": {
            "retrieval_set_id": retrieval_result.retrieval_set_id,
            "count": len(retrieval_result.contexts),
            "answer_context_count": len(answering_contexts),
            "source_types": sorted(retrieval_result.source_kinds),
            "answer_source_types": sorted({context.source_kind for context in answering_contexts}),
            "requested_source_types": retrieval_result.requested_source_kinds,
            "top_k": retrieval_result.top_k,
            "semantic_mode": request.semantic_mode,
            "semantic_node_id": request.semantic_node_id,
            "semantic_label": request.semantic_label,
            "document_id": request.document_id,
            "document_version_id": request.document_version_id,
            "effective_document_ids": effective_scope["document_ids"],
            "effective_document_version_ids": effective_scope["document_version_ids"],
            "forced_pdf_first_scope": effective_scope["forced_pdf_first_scope"],
            "scope_reason": effective_scope["scope_reason"],
            "date_scope": effective_scope["date_scope"],
            "retrieval_query": retrieval_query_debug,
            "pdf_first_reference_enrichment": reference_enrichment_debug,
            "pipeline_run_id": request.pipeline_run_id,
        },
        "model": {
            "provider": answer_result.provider,
            "name": answer_result.model,
        },
    }
    if request.debug_mode:
        trace_payload = _normalized_answering_trace(answer_result.scoring)
        retrieval_trace = dict(retrieval_result.debug_info)
        timing_breakdown = {
            "request_total": total_ms,
            "retrieval": retrieval_ms,
            "answering": answering_ms,
            "answering_stages": trace_payload.get("timing_ms") if isinstance(trace_payload.get("timing_ms"), dict) else {},
        }
        response_payload["debug"] = {
            "enabled": True,
            "thresholds": _ask_thresholds_payload(
                request=request,
                llm_client=resolved_llm_client,
                effective_scope=effective_scope,
            ),
            "answering_trace": trace_payload,
            "retrieval_trace": retrieval_trace,
            "timing_ms": timing_breakdown,
            "pipeline": {
                "stage_order": [
                    "retrieve",
                    "dictalm_pdf_first_answer",
                    "validate_cited_chunks",
                    "retry_or_repair_if_needed",
                    "refuse_if_uncited",
                ],
                "effective_required_source_types": effective_scope["required_source_types"],
                "effective_document_version_ids": effective_scope["document_version_ids"],
            },
        }

    log_rag_event(
        "rag.ask.response",
        ask_request_id=ask_request_id,
        question_hash=question_hash,
        status=answer_result.status,
        retrieval_set_id=retrieval_result.retrieval_set_id,
        retrieval_count=len(retrieval_result.contexts),
        retrieval_source_types=sorted(retrieval_result.source_kinds),
        citation_count=len(citations_payload),
        refusal_reason_code=answer_result.refusal_reason_code,
        provider=answer_result.provider,
        model=answer_result.model,
    )

    sample_rate = audit_sample_rate()
    if should_sample_audit(rate=sample_rate):
        log_rag_event(
            "rag.ask.audit_sample",
            ask_request_id=ask_request_id,
            question_hash=question_hash,
            sample_rate=sample_rate,
            status=answer_result.status,
            retrieval_set_id=retrieval_result.retrieval_set_id,
            citation_chunk_ids=[citation["chunk_id"] for citation in citations_payload],
            refusal_reason_code=answer_result.refusal_reason_code,
            missing_source_types=answer_result.missing_source_kinds,
            answer_preview=(answer_result.answer or "")[:280],
        )

    return response_payload


def _build_ask_llm_client(*, request: AskRequest) -> RagLlmClient:
    model_name = (request.model_name or "").strip()
    if not model_name:
        return build_rag_llm_client()

    source = dict(os.environ)
    source["RAG_LLM_PROVIDER"] = "ollama"
    source["RAG_LLM_MODEL"] = model_name
    return build_rag_llm_client(config=RagLlmConfig.from_env(source))


def _decision_payload(decision_id: int, db) -> dict | None:
    decision = db.execute(
        select(Decision).where(Decision.id == decision_id, Decision.is_public.is_(True))
    ).scalar_one_or_none()
    if decision is None:
        return None

    meeting = db.execute(select(Meeting).where(Meeting.id == decision.meeting_id)).scalar_one_or_none()
    municipality_slug = None
    if meeting is not None:
        site = db.execute(select(SourceSite).where(SourceSite.id == meeting.source_site_id)).scalar_one_or_none()
        municipality_slug = site.municipality_slug if site else None

    citations = db.execute(
        select(DecisionCitation, Document)
        .join(Document, Document.id == DecisionCitation.document_id)
        .where(DecisionCitation.decision_id == decision.id)
        .order_by(DecisionCitation.page_number.asc(), DecisionCitation.start_offset.asc())
    ).all()

    vote = db.execute(select(Vote).where(Vote.decision_id == decision.id)).scalar_one_or_none()
    linked_docs = db.execute(
        select(DecisionDocumentLink, Document)
        .join(Document, Document.id == DecisionDocumentLink.document_id)
        .where(DecisionDocumentLink.decision_id == decision.id)
        .order_by(Document.id.asc())
    ).all()

    return {
        "decision": {
            "id": decision.id,
            "meeting_id": decision.meeting_id,
            "meeting_external_id": meeting.meeting_external_id if meeting else None,
            "municipality": municipality_slug,
            "decision_number": decision.decision_number,
            "agenda_item": decision.agenda_item,
            "decision_text": decision.decision_text,
            "parser_confidence": decision.parser_confidence,
            "source_type": "protocol",
            "metadata": _fallback_metadata_from_json(decision.metadata_json),
            "request_context": _decision_request_context_payload(db=db, decision_id=decision.id),
            "linked_gis_features": decision_gis_feature_links(db, decision_id=decision.id),
        },
        "votes": [
            {
                "for_count": vote.for_count,
                "against_count": vote.against_count,
                "abstain_count": vote.abstain_count,
                "unanimous": vote.unanimous,
                "is_uncertain": vote.is_uncertain,
                "confidence": vote.confidence,
                "raw_text": vote.raw_text,
            }
            for vote in [vote]
            if vote is not None
        ],
        "citations": [
            {
                "id": citation.id,
                "document": {
                    "id": document.id,
                    "title": document.title_he,
                    "url": document.canonical_url,
                    "source_type": citation.source_type,
                },
                "page": citation.page_number,
                "start_offset": citation.start_offset,
                "end_offset": citation.end_offset,
                "anchor_label": citation.anchor_label,
                "anchor_text": citation.anchor_text,
            }
            for citation, document in citations
        ],
        "linked_documents": [
            {
                "id": document.id,
                "title": document.title_he,
                "url": document.canonical_url,
                "doc_kind": document.doc_kind,
                "source_type": link.source_type,
                "provenance": link.provenance,
            }
            for link, document in linked_docs
        ],
    }


@app.on_event("startup")
def startup() -> None:
    if engine.dialect.name == "sqlite":
        apply_all(engine, Path("migrations"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/govmap/iframe-config")
def govmap_iframe_config(request: Request) -> JSONResponse:
    origin = _effective_request_origin(request)
    allowed_origins = _govmap_allowed_iframe_origins()
    if origin not in allowed_origins:
        return JSONResponse(
            {
                "error": "iframe_origin_not_allowed",
                "message": "Native GovMap iframe API requires an approved HTTPS origin.",
                "origin": origin,
                "allowedIframeOrigins": allowed_origins,
            },
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        {
            "token": os.environ.get("GOVMAP_API_KEY") or DEFAULT_GOVMAP_API_KEY,
            "scriptUrl": "https://www.govmap.gov.il/govmap/api/govmap.api.js",
            "allowedIframeOrigins": allowed_origins,
            "origin": origin,
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/ui/rag-dashboard/mock")
def rag_dashboard_mock() -> dict[str, Any]:
    return validate_dashboard_payload(get_mock_rag_dashboard_payload())


@app.get("/api/ui/rag-dashboard/evidence/{evidence_id}")
def rag_dashboard_evidence(evidence_id: str, db=Depends(get_db)) -> dict[str, Any]:
    evidence = get_mock_rag_dashboard_evidence(evidence_id)
    if evidence is None:
        evidence = get_dashboard_artifact_evidence(db=db, evidence_id=evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="rag_dashboard_evidence_not_found")
    return evidence


@app.get("/api/ui/rag-dashboard/gis-map")
def rag_dashboard_gis_map(gush: str = "7103", helka: str = "43", radius_m: float = 3000.0, example: str = "tel_aviv_parcel", profile: str = "initial", provider: str | None = None, municipality: str | None = None, address: str | None = None, center_x: float | None = None, center_y: float | None = None, focus_layer: str | None = None, timeline_event_id: str | None = None, db=Depends(get_db)) -> dict[str, Any]:
    return build_dashboard_gis_map_payload(db, gush=gush, helka=helka, radius_m=radius_m, example=example, profile=profile, provider=provider, municipality=municipality, address=address, center_x=center_x, center_y=center_y, focus_layer=focus_layer, timeline_event_id=timeline_event_id)


@app.get("/api/ui/rag-dashboard/gis-buildings")
def rag_dashboard_gis_buildings(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    municipality_code: str = "5000",
    db=Depends(get_db),
) -> dict[str, Any]:
    return build_dashboard_gis_buildings_payload(db, municipality_code=municipality_code, min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat)


@app.get("/api/ui/rag-dashboard/gis-parcels")
def rag_dashboard_gis_parcels(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    municipality_code: str = "5000",
    db=Depends(get_db),
) -> dict[str, Any]:
    return build_dashboard_gis_parcels_payload(db, municipality_code=municipality_code, min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat)


@app.get("/api/ui/rag-dashboard/gis-context-pois")
def rag_dashboard_gis_context_pois(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    municipality_code: str = "5000",
    db=Depends(get_db),
) -> dict[str, Any]:
    return build_dashboard_gis_context_pois_payload(db, municipality_code=municipality_code, min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat)


@app.post("/api/admin/gis/decision-links/rebuild")
def rebuild_decision_gis_links(limit: int | None = None, db=Depends(get_db)) -> dict[str, Any]:
    summary = link_decisions_to_gis_plans(db, limit=limit)
    db.commit()
    return {
        "status": "ok",
        "scanned_decisions": summary.scanned_decisions,
        "matched_decisions": summary.matched_decisions,
        "inserted_or_updated": summary.inserted_or_updated,
    }


@app.post("/api/ui/rag-dashboard/query")
def rag_dashboard_query(request: RagDashboardQueryRequest, db=Depends(get_db)) -> dict[str, Any]:
    dashboard_filters = request.filters if isinstance(request.filters, dict) else {}
    filter_year = _dashboard_filter_year(dashboard_filters)
    filter_semantic_label = request.semantic_label or _dashboard_filter_semantic_label(dashboard_filters)
    geo_intent = resolve_geo_intent(request.question)
    geo_intent_payload = geo_intent.to_payload()
    map_context = _dashboard_map_context(db=db, geo_intent_payload=geo_intent_payload) if geo_intent.needs_gis else None
    if map_context is not None:
        geo_intent_payload["map_context"] = map_context
    municipality_scope = _dashboard_municipality_scope(requested_muni=request.muni, map_context=map_context)
    if municipality_scope:
        geo_intent_payload["municipality_scope"] = municipality_scope
    effective_muni = str(municipality_scope.get("effective_muni") or request.muni or "").strip() or None
    try:
        ask_payload = _run_ask(
            request=AskRequest(
                question=request.question,
                muni=effective_muni,
                top_k=request.top_k,
                year=filter_year,
                semantic_node_id=request.semantic_node_id,
                semantic_label=filter_semantic_label,
                semantic_mode=request.semantic_mode,
                debug_mode=request.debug_mode,
            ),
            db=db,
        )
    except HTTPException as exc:
        return build_dashboard_error_payload(
            question=request.question,
            municipality_id=effective_muni or request.muni,
            error_code=str(exc.detail or "dashboard_query_failed"),
            message_he="שירות התשובות לא הצליח להפיק תשובה כרגע.",
            geo_intent_resolution=geo_intent_payload,
        )
    except Exception as exc:  # noqa: BLE001 - dashboard query should render a safe error state.
        return build_dashboard_error_payload(
            question=request.question,
            municipality_id=effective_muni or request.muni,
            error_code=exc.__class__.__name__,
            message_he="שירות התשובות לא זמין כרגע.",
            geo_intent_resolution=geo_intent_payload,
        )
    return build_dashboard_payload_from_ask_result(
        question=request.question,
        municipality_id=effective_muni or request.muni,
        ask_payload=ask_payload,
        filters=dashboard_filters,
        geo_intent_resolution=geo_intent_payload,
    )


def _dashboard_filter_year(filters: dict[str, Any]) -> int | None:
    raw = str(filters.get("time_range") or "").strip()
    return int(raw) if raw.isdigit() and len(raw) == 4 else None


def _dashboard_map_context(*, db, geo_intent_payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return build_map_context(db, geo_intent_payload)
    except Exception as exc:  # noqa: BLE001 - dashboard search must continue if GIS context fails.
        if hasattr(db, "rollback"):
            db.rollback()
        return {
            "status": "unavailable",
            "focus": geo_intent_payload.get("focus"),
            "layers": [],
            "caveats": [f"GIS context unavailable: {exc.__class__.__name__}"],
        }


def _dashboard_municipality_scope(*, requested_muni: str | None, map_context: dict[str, Any] | None) -> dict[str, Any]:
    requested = str(requested_muni or "").strip() or None
    gis_code = str((map_context or {}).get("municipality_code") or "").strip() or None
    gis_slug = MUNICIPALITY_CODE_TO_SLUG.get(gis_code or "")
    effective = gis_slug or requested
    scope = {
        "requested_muni": requested,
        "gis_municipality_code": gis_code,
        "gis_municipality_slug": gis_slug,
        "effective_muni": effective,
        "mismatch": bool(requested and gis_slug and requested != gis_slug),
    }
    if scope["mismatch"]:
        scope["caveat_he"] = "השאלה מוקדה לפי מיקום ה-GIS, ולכן חיפוש המסמכים הותאם לרשות שעלתה מהמפה במקום לרשות שנשלחה מהדמו."
    elif gis_slug and not requested:
        scope["caveat_he"] = "חיפוש המסמכים הותאם לרשות המקומית שזוהתה מתוך שכבת גבולות רשמית."
    return scope if any(value is not None and value is not False for value in scope.values()) else {}


def _dashboard_filter_semantic_label(filters: dict[str, Any]) -> str | None:
    raw = str(filters.get("category") or "").strip()
    labels = {
        "planning": "תכנון ובנייה",
        "transport": "תחבורה",
        "education": "חינוך",
        "welfare": "רווחה",
        "environment": "סביבה",
    }
    if raw in labels:
        return labels[raw]
    return raw if raw and raw not in {"כל הקטגוריות", "הכול"} else None


def _latest_topic_subject_run(*, db, municipality_slug: str, run_id: int | None = None) -> TopicSubjectRun | None:
    if run_id is not None:
        return db.execute(select(TopicSubjectRun).where(TopicSubjectRun.id == run_id)).scalar_one_or_none()

    completed_write = db.execute(
        select(TopicSubjectRun)
        .where(TopicSubjectRun.municipality_slug == municipality_slug)
        .where(TopicSubjectRun.status == "completed")
        .where(TopicSubjectRun.write_mode.is_(True))
        .order_by(TopicSubjectRun.started_at.desc(), TopicSubjectRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if completed_write is not None:
        return completed_write

    return db.execute(
        select(TopicSubjectRun)
        .where(TopicSubjectRun.municipality_slug == municipality_slug)
        .where(TopicSubjectRun.status == "completed")
        .order_by(TopicSubjectRun.started_at.desc(), TopicSubjectRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _topic_subject_run_payload(run: TopicSubjectRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "municipality": run.municipality_slug,
        "model_provider": run.model_provider,
        "model_name": run.model_name,
        "status": run.status,
        "write_mode": bool(run.write_mode),
        "source_artifact_count": run.source_artifact_count,
        "extraction_count": run.extraction_count,
        "candidate_subject_count": run.candidate_subject_count,
        "candidate_decision_count": run.candidate_decision_count,
        "failed_count": run.failed_count,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def _topic_subject_stored_label(*, root: str | None, child: str | None) -> str:
    root_label = str(root or "").strip()
    child_label = str(child or "").strip()
    return f"{root_label} / {child_label}" if child_label else root_label


def _topic_subject_display_payload(*, root: str | None, child: str | None) -> dict[str, Any]:
    root_label = str(root or "").strip()
    child_label = str(child or "").strip()
    stored_label = _topic_subject_stored_label(root=root_label, child=child_label)
    rule = TOPIC_SUBJECT_DISPLAY_RULES.get((root_label, child_label))
    if rule is None:
        fallback_key = "stored_" + re.sub(r"[^0-9A-Za-z_]+", "_", stored_label).strip("_")
        rule = (fallback_key or "stored_unknown", stored_label or "לא מסווג", child_label or None)
    group_key, group_label, subgroup_label = rule
    return {
        "group_key": group_key,
        "group_label_he": group_label,
        "subgroup_label_he": subgroup_label,
        "stored_label_he": stored_label,
        "stored_root_label_he": root_label,
        "stored_child_label_he": child_label,
    }


def _topic_subject_quality_flags(subject: TopicSubject) -> list[str]:
    flags: list[str] = []
    if subject.topic_relevance == "topic_suspect":
        flags.append("topic_suspect")
    if subject.subject_status == "candidate_decision_rejected":
        flags.append("decision_rejected_subject_kept")
    if subject.row_role and subject.row_role != "action_anchor":
        flags.append(f"row_role:{subject.row_role}")
    if subject.anchor_status and subject.anchor_status != "validated_anchor":
        flags.append(f"anchor_status:{subject.anchor_status}")
    return flags


def _topic_subject_rows(*, db, run_id: int, include_linked_details: bool) -> list[TopicSubject]:
    stmt = select(TopicSubject).where(TopicSubject.run_id == run_id)
    if not include_linked_details:
        stmt = stmt.where(TopicSubject.row_role == "action_anchor").where(TopicSubject.anchor_status == "validated_anchor")
    return list(
        db.execute(
            stmt.order_by(
                TopicSubject.source_document_version_id.asc(),
                TopicSubject.source_ordinal.asc(),
                TopicSubject.subject_index.asc(),
                TopicSubject.id.asc(),
            )
        ).scalars()
    )


def _topic_subject_quality_key(run_id: int, artifact_id: str, semantic_node_id: int) -> tuple[int, str, int]:
    return int(run_id), str(artifact_id), int(semantic_node_id)


def _topic_subject_prediction_en_from_parts(*, root: str | None, child: str | None, object_text: str | None) -> str:
    root_label = str(root or "").strip()
    child_label = str(child or "").strip()
    action_label = TOPIC_SUBJECT_ENGLISH_LABELS.get((root_label, child_label))
    if action_label is None:
        stored_label = _topic_subject_stored_label(root=root_label, child=child_label)
        action_label = f"Subject action: {stored_label}" if stored_label else "Subject action"
    clean_object = str(object_text or "").strip()
    if not clean_object:
        return action_label
    preposition = "about" if action_label in {"Council inquiry", "Response to council inquiry", "Agenda proposal", "Report", "Review report", "Committee recommendation"} else "for"
    return f"{action_label} {preposition} {clean_object}"


def _topic_subject_action_root(subject: TopicSubject) -> str | None:
    return subject.action_root_label_he or subject.subject_root_label_he


def _topic_subject_action_child(subject: TopicSubject) -> str | None:
    return subject.action_child_label_he if subject.action_child_label_he is not None else subject.subject_child_label_he


def _topic_subject_matter(subject: TopicSubject) -> str | None:
    return subject.subject_matter_he or subject.subject_object_he


def _quality_action_root(quality: TopicSubjectQualityReport | None) -> str | None:
    if quality is None:
        return None
    return quality.action_root_by_dicta or quality.subject_root_by_dicta


def _quality_action_child(quality: TopicSubjectQualityReport | None) -> str | None:
    if quality is None:
        return None
    return quality.action_child_by_dicta if quality.action_child_by_dicta is not None else quality.subject_child_by_dicta


def _quality_subject_matter(quality: TopicSubjectQualityReport | None) -> str | None:
    if quality is None:
        return None
    return quality.subject_matter_by_dicta or quality.subject_object_by_dicta


def _quality_action_details(quality: TopicSubjectQualityReport | None) -> str | None:
    if quality is None:
        return None
    return quality.action_details_by_dicta or quality.subject_details_by_dicta


def _topic_subject_agent_prediction_en(subject: TopicSubject, quality: TopicSubjectQualityReport | None = None) -> str:
    return _topic_subject_prediction_en_from_parts(
        root=_topic_subject_action_root(subject),
        child=_topic_subject_action_child(subject),
        object_text=_topic_subject_matter(subject) or _quality_subject_matter(quality),
    )


def _topic_subject_audit_payload(subject: TopicSubject, quality: TopicSubjectQualityReport | None) -> dict[str, Any]:
    return {
        "agent_subject_prediction_en": _topic_subject_agent_prediction_en(subject, quality),
        "full_source_text_he": quality.real_text if quality is not None else None,
        "ground_truth_subject_he": quality.ground_truth if quality is not None else None,
        "ground_truth_note_he": quality.ground_truth if quality is not None else None,
        "judge_prediction": {
            "root_label_he": _topic_subject_action_root(subject),
            "child_label_he": _topic_subject_action_child(subject),
            "object_he": _topic_subject_matter(subject) or _quality_subject_matter(quality),
            "note_he": quality.ground_truth if quality is not None else None,
            "judgment": quality.my_judgment if quality is not None else None,
        },
        "my_judgment": quality.my_judgment if quality is not None else None,
        "reason_for_failure": quality.reason_for_failure if quality is not None else None,
        "model_prediction": {
            "root_label_he": _quality_action_root(quality),
            "child_label_he": _quality_action_child(quality),
            "object_he": _quality_subject_matter(quality),
            "details_he": _quality_action_details(quality),
            "decision_he": quality.decision_by_dicta if quality is not None else None,
        },
    }


def _topic_subject_linked_detail_payload(quality: TopicSubjectQualityReport) -> dict[str, Any]:
    return {
        "artifact_id": quality.artifact_id,
        "event_id": quality.event_id,
        "linked_event_id": quality.linked_event_id,
        "topic_label_he": quality.topic_label_he,
        "row_role": quality.row_role,
        "anchor_status": quality.anchor_status,
        "status": quality.status,
        "agent_subject_prediction_en": _topic_subject_prediction_en_from_parts(
            root=_quality_action_root(quality),
            child=_quality_action_child(quality),
            object_text=_quality_subject_matter(quality),
        ),
        "full_source_text_he": quality.real_text,
        "ground_truth_subject_he": quality.ground_truth,
        "ground_truth_note_he": quality.ground_truth,
        "judge_prediction": {
            "root_label_he": _quality_action_root(quality),
            "child_label_he": _quality_action_child(quality),
            "object_he": _quality_subject_matter(quality),
            "note_he": quality.ground_truth,
            "judgment": quality.my_judgment,
        },
        "my_judgment": quality.my_judgment,
        "model_prediction": {
            "root_label_he": _quality_action_root(quality),
            "child_label_he": _quality_action_child(quality),
            "object_he": _quality_subject_matter(quality),
            "details_he": _quality_action_details(quality),
            "decision_he": quality.decision_by_dicta,
        },
    }


def _topic_subject_item_payload(
    subject: TopicSubject,
    document: Document | None = None,
    document_version: DocumentVersion | None = None,
    quality: TopicSubjectQualityReport | None = None,
    linked_details: list[TopicSubjectQualityReport] | None = None,
) -> dict[str, Any]:
    display = _topic_subject_display_payload(root=_topic_subject_action_root(subject), child=_topic_subject_action_child(subject))
    source_path = _resolve_document_version_source_pdf_path(
        document_version_id=int(subject.source_document_version_id or 0),
        storage_uri=str(document_version.storage_uri or "") if document_version is not None else "",
        document_url=str(document.canonical_url or "") if document is not None else "",
    )
    return {
        "id": subject.id,
        "artifact_id": subject.artifact_id,
        "event_id": subject.event_id,
        "display": display,
        "stored": {
            "root_label_he": _topic_subject_action_root(subject),
            "child_label_he": _topic_subject_action_child(subject),
            "label_he": display["stored_label_he"],
        },
        "topic": {
            "label_he": subject.topic_label_he,
            "root_topic_id": subject.root_topic_id,
            "child_topic_id": subject.child_topic_id,
            "relevance": subject.topic_relevance,
        },
        "subject": {
            "object_he": _topic_subject_matter(subject),
            "summary_he": subject.subject_summary_he,
            "details_he": subject.action_details_he or subject.subject_details_he,
            "what_text_is_about_he": subject.what_text_is_about_he,
            "status": subject.subject_status,
            "confidence": subject.confidence,
        },
        "decision": {
            "is_decision": bool(subject.is_decision),
            "label_he": subject.decision_label_he,
            "summary_he": subject.decision_summary_he,
            "source_quote_he": subject.decision_source_quote_he,
        },
        "source": {
            "kind": subject.source_kind,
            "document_id": subject.source_document_id,
            "document_version_id": subject.source_document_version_id,
            "document_title": document.title_he if document is not None else subject.source_title,
            "document_url": document.canonical_url if document is not None else None,
            "document_file_path": str(source_path.resolve()) if source_path is not None else None,
            "document_full_path": str(source_path.resolve()) if source_path is not None else None,
            "title": subject.source_title,
            "ordinal": subject.source_ordinal,
            "page_start": subject.source_page_start,
            "page_end": subject.source_page_end,
        },
        "quality": {
            "validation_status": subject.validation_status,
            "row_role": subject.row_role,
            "anchor_status": subject.anchor_status,
            "flags": _topic_subject_quality_flags(subject),
        },
        "audit": _topic_subject_audit_payload(subject, quality),
        "linked_details": [
            _topic_subject_linked_detail_payload(detail)
            for detail in (linked_details or [])
        ],
    }


@app.get("/api/topic-subjects/groups")
def topic_subject_groups(
    muni: str = "ashdod",
    run_id: int | None = None,
    include_linked_details: bool = False,
    db=Depends(get_db),
) -> dict[str, Any]:
    run = _latest_topic_subject_run(db=db, municipality_slug=muni, run_id=run_id)
    if run is None or run.municipality_slug != muni:
        raise HTTPException(status_code=404, detail="topic_subject_run_not_found")

    rows = _topic_subject_rows(db=db, run_id=run.id, include_linked_details=include_linked_details)
    groups: dict[str, dict[str, Any]] = {}
    for subject in rows:
        display = _topic_subject_display_payload(root=_topic_subject_action_root(subject), child=_topic_subject_action_child(subject))
        group = groups.setdefault(
            display["group_key"],
            {
                "group_key": display["group_key"],
                "label_he": display["group_label_he"],
                "count": 0,
                "decision_count": 0,
                "topic_suspect_count": 0,
                "stored_labels": {},
                "subgroups": {},
                "sample_subjects": [],
            },
        )
        group["count"] += 1
        group["decision_count"] += 1 if subject.is_decision else 0
        group["topic_suspect_count"] += 1 if subject.topic_relevance == "topic_suspect" else 0
        stored_labels = group["stored_labels"]
        stored_labels[display["stored_label_he"]] = int(stored_labels.get(display["stored_label_he"], 0)) + 1
        if display["subgroup_label_he"]:
            subgroups = group["subgroups"]
            subgroups[display["subgroup_label_he"]] = int(subgroups.get(display["subgroup_label_he"], 0)) + 1
        if len(group["sample_subjects"]) < 3:
            group["sample_subjects"].append(
                {
                    "id": subject.id,
                    "artifact_id": subject.artifact_id,
                    "topic_label_he": subject.topic_label_he,
                    "object_he": _topic_subject_matter(subject),
                    "is_decision": bool(subject.is_decision),
                    "quality_flags": _topic_subject_quality_flags(subject),
                }
            )

    group_payloads = sorted(
        groups.values(),
        key=lambda group: (TOPIC_SUBJECT_DISPLAY_ORDER.get(str(group["group_key"]), 10_000), -int(group["count"]), str(group["label_he"])),
    )
    return {
        "run": _topic_subject_run_payload(run),
        "filters": {
            "municipality": muni,
            "include_linked_details": include_linked_details,
        },
        "count": sum(int(group["count"]) for group in group_payloads),
        "group_count": len(group_payloads),
        "groups": group_payloads,
    }


@app.get("/api/topic-subjects")
def topic_subject_items(
    muni: str = "ashdod",
    display_group: str | None = None,
    run_id: int | None = None,
    include_linked_details: bool = False,
    limit: int = 50,
    offset: int = 0,
    db=Depends(get_db),
) -> dict[str, Any]:
    run = _latest_topic_subject_run(db=db, municipality_slug=muni, run_id=run_id)
    if run is None or run.municipality_slug != muni:
        raise HTTPException(status_code=404, detail="topic_subject_run_not_found")

    rows = _topic_subject_rows(db=db, run_id=run.id, include_linked_details=include_linked_details)
    requested_group = str(display_group or "").strip()
    if requested_group:
        rows = [
            subject
            for subject in rows
            if requested_group
            in {
                _topic_subject_display_payload(root=_topic_subject_action_root(subject), child=_topic_subject_action_child(subject))["group_key"],
                _topic_subject_display_payload(root=_topic_subject_action_root(subject), child=_topic_subject_action_child(subject))["group_label_he"],
            }
        ]

    total_count = len(rows)
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 200))
    page_rows = rows[offset : offset + limit]
    documents_by_id = {
        document.id: document
        for document in db.execute(
            select(Document).where(Document.id.in_([subject.source_document_id for subject in page_rows]))
        ).scalars()
    } if page_rows else {}
    versions_by_id = {
        version.id: version
        for version in db.execute(
            select(DocumentVersion).where(DocumentVersion.id.in_([subject.source_document_version_id for subject in page_rows]))
        ).scalars()
    } if page_rows else {}
    quality_rows = list(
        db.execute(
            select(TopicSubjectQualityReport)
            .where(TopicSubjectQualityReport.run_id == run.id)
            .where(TopicSubjectQualityReport.artifact_id.in_([subject.artifact_id for subject in page_rows]))
        ).scalars()
    ) if page_rows else []
    quality_by_key = {
        _topic_subject_quality_key(row.run_id, row.artifact_id, row.semantic_node_id): row
        for row in quality_rows
    }
    event_ids = [str(subject.event_id) for subject in page_rows if str(subject.event_id or "").strip()]
    linked_detail_rows = list(
        db.execute(
            select(TopicSubjectQualityReport)
            .where(TopicSubjectQualityReport.run_id == run.id)
            .where(TopicSubjectQualityReport.linked_event_id.in_(event_ids))
            .where(TopicSubjectQualityReport.status == "linked_detail")
            .order_by(TopicSubjectQualityReport.id.asc())
        ).scalars()
    ) if event_ids else []
    linked_details_by_event: dict[str, list[TopicSubjectQualityReport]] = {}
    for detail in linked_detail_rows:
        linked_details_by_event.setdefault(str(detail.linked_event_id or ""), []).append(detail)
    return {
        "run": _topic_subject_run_payload(run),
        "filters": {
            "municipality": muni,
            "display_group": requested_group or None,
            "include_linked_details": include_linked_details,
            "limit": limit,
            "offset": offset,
        },
        "count": len(page_rows),
        "total_count": total_count,
        "items": [
            _topic_subject_item_payload(
                subject,
                document=documents_by_id.get(subject.source_document_id),
                document_version=versions_by_id.get(subject.source_document_version_id),
                quality=quality_by_key.get(_topic_subject_quality_key(subject.run_id, subject.artifact_id, subject.semantic_node_id)),
                linked_details=linked_details_by_event.get(str(subject.event_id or ""), []),
            )
            for subject in page_rows
        ],
    }


@app.post("/api/ui/rag-dashboard/interaction")
def rag_dashboard_interaction(payload: RagDashboardInteractionRequest) -> dict[str, Any]:
    next_payload = apply_mock_rag_dashboard_interaction(
        state=payload.state,
        interaction=payload.interaction.model_dump(exclude_none=True),
    )
    if next_payload is None:
        raise HTTPException(status_code=404, detail="rag_dashboard_interaction_not_found")
    return validate_dashboard_payload(next_payload)


@app.post("/crawl/run")
def crawl_run(muni: str, root_url: str, db=Depends(get_db)) -> dict[str, int]:
    service = PipelineService(
        session=db,
        html_fetcher=_default_html_fetcher,
        fetcher=AssetFetcher(),
        storage_root=Path("storage/raw"),
    )
    run_id = service.run_crawl(muni, root_url)
    return {"run_id": run_id}


@app.post("/process/run")
def process_run(doc_id: int | None = None, muni: str | None = None, db=Depends(get_db)) -> dict[str, int]:
    service = ProcessingService(
        session=db,
        storage_root=Path("storage/raw"),
    )
    run_id = service.run(doc_id=doc_id, municipality_slug=muni)
    return {"run_id": run_id}


@app.get("/search")
def search(
    q: str,
    muni: str | None = None,
    source_type: str | None = None,
    year: int | None = None,
    topic: str | None = None,
    semantic_node_id: int | None = None,
    semantic_label: str | None = None,
    semantic_mode: str = "boost",
    retrieval_strategy: str = "auto",
    include_semantic_debug: bool = False,
    limit: int = 20,
    db=Depends(get_db),
) -> dict:
    service = build_search_backend(session=db)
    retrieval_service = RagRetrievalService(
        search_service=service,
        reranker=EmbeddingReranker(build_embedding_backend(session=db)),
    )
    retrieval_result = retrieval_service.retrieve(
        query=q,
        top_k=max(1, min(limit, 50)),
        municipality_slug=muni,
        source_kinds=[source_type] if source_type else None,
        year=year,
        topic=topic,
        semantic_node_id=semantic_node_id,
        semantic_label=semantic_label,
        semantic_mode=semantic_mode,
        retrieval_strategy=retrieval_strategy,
    )
    hits = retrieval_result.contexts
    results = [
        {
            "chunk_id": hit.chunk_id,
            "score": hit.score,
            "snippet": hit.snippet,
            "citation": hit.citation,
            "source_type": hit.source_kind,
            "artifact_kind": hit.artifact_kind,
            "header_path": list(hit.section_path),
            "document": {
                "id": hit.document_id,
                "title": hit.document_title,
                "url": hit.document_url,
            },
            "municipality": hit.municipality_slug,
            "meeting_external_id": hit.meeting_external_id,
            "start_page": hit.start_page,
            "end_page": hit.end_page,
            "semantic_match_count": hit.semantic_match_count,
            "semantic_boost": hit.semantic_boost if hasattr(hit, "semantic_boost") else 0.0,
            "primary_topic": getattr(hit, "primary_topic", None),
            "secondary_topics": list(getattr(hit, "secondary_topics", []) or []),
        }
        for hit in hits
    ]

    if include_semantic_debug:
        for idx, hit in enumerate(hits):
            results[idx]["semantic_nodes"] = [
                {
                    "id": node.id,
                    "label": node.label,
                    "kind": node.kind,
                    "type": node.semantic_type,
                    "confidence": node.confidence,
                }
                for node in hit.semantic_nodes
            ]
            results[idx]["semantic_node_ids"] = list(hit.semantic_node_ids)

    return {
        "query": q,
        "retrieval_strategy": retrieval_strategy,
        "count": len(hits),
        "results": results,
    }


@app.post("/ask")
def ask(request: AskRequest, db=Depends(get_db)) -> dict:
    return _run_ask(request=request, db=db)


def _hydrate_context_semantic_topic_labels(*, db, contexts: list[RagContextChunk]) -> list[RagContextChunk]:
    chunk_ids = [context.chunk_id for context in contexts if str(context.chunk_id or "").strip()]
    if not chunk_ids:
        return contexts

    rows = db.execute(
        select(ArtifactSemanticLink.artifact_id, SemanticNode.id, SemanticNode.pref_label_he)
        .join(SemanticNode, SemanticNode.id == ArtifactSemanticLink.semantic_node_id)
        .where(ArtifactSemanticLink.artifact_id.in_(chunk_ids))
        .where(SemanticNode.node_kind == "topic")
        .where(SemanticNode.status == "active")
        .order_by(SemanticNode.depth.desc(), SemanticNode.specificity_score.desc(), SemanticNode.pref_label_norm.asc())
    ).all()
    labels_by_chunk: dict[str, list[str]] = {}
    node_ids_by_chunk: dict[str, list[int]] = {}
    for artifact_id, node_id, label in rows:
        chunk_id = str(artifact_id)
        compact_label = str(label or "").strip()
        if not compact_label:
            continue
        labels = labels_by_chunk.setdefault(chunk_id, [])
        if compact_label not in labels:
            labels.append(compact_label)
        node_ids = node_ids_by_chunk.setdefault(chunk_id, [])
        if int(node_id) not in node_ids:
            node_ids.append(int(node_id))

    if not labels_by_chunk:
        return contexts

    hydrated: list[RagContextChunk] = []
    for context in contexts:
        labels = labels_by_chunk.get(context.chunk_id)
        if not labels:
            hydrated.append(context)
            continue
        merged_labels = list(context.semantic_topic_labels)
        for label in labels:
            if label not in merged_labels:
                merged_labels.append(label)
        context.semantic_topic_labels = merged_labels
        context.semantic_node_ids = list(dict.fromkeys([*context.semantic_node_ids, *node_ids_by_chunk.get(context.chunk_id, [])]))
        hydrated.append(context)
    return hydrated


@app.post("/ask/debug/retrieval")
def ask_debug_retrieval(request: AskRequest, db=Depends(get_db)) -> dict:
    started = time.perf_counter()
    arch_config = RagArchitectureConfig.from_env()
    effective_scope = _ask_effective_scope(request, db=db)
    retrieval_query, retrieval_query_debug = _ask_retrieval_query(request=request, effective_scope=effective_scope)
    retrieval_service = RagRetrievalService(
        search_service=build_search_backend(session=db, arch_config=arch_config),
        reranker=EmbeddingReranker(build_embedding_backend(session=db, arch_config=arch_config)),
    )
    retrieval_result = retrieval_service.retrieve(
        query=retrieval_query,
        top_k=request.top_k,
        municipality_slug=request.muni,
        source_kinds=effective_scope["source_types"],
        year=request.year,
        topic=request.topic,
        semantic_node_id=request.semantic_node_id,
        semantic_label=request.semantic_label,
        semantic_mode=request.semantic_mode,
        retrieval_strategy=request.retrieval_strategy,
        document_ids=effective_scope["document_ids"],
        document_version_ids=effective_scope["document_version_ids"],
    )
    retrieval_ms = round((time.perf_counter() - started) * 1000.0, 3)
    rerank_summary = _rerank_debug_summary(retrieval_result=retrieval_result)
    return {
        "query": request.question,
        "retrieval": {
            "retrieval_set_id": retrieval_result.retrieval_set_id,
            "count": len(retrieval_result.contexts),
            "source_types": sorted(retrieval_result.source_kinds),
            "requested_source_types": retrieval_result.requested_source_kinds,
            "top_k": retrieval_result.top_k,
            "semantic_mode": request.semantic_mode,
            "semantic_node_id": request.semantic_node_id,
            "semantic_label": request.semantic_label,
            "retrieval_strategy": request.retrieval_strategy,
            "document_id": request.document_id,
            "document_version_id": request.document_version_id,
            "effective_document_ids": effective_scope["document_ids"],
            "effective_document_version_ids": effective_scope["document_version_ids"],
            "forced_pdf_first_scope": effective_scope["forced_pdf_first_scope"],
            "scope_reason": effective_scope["scope_reason"],
            "date_scope": effective_scope["date_scope"],
            "retrieval_query": retrieval_query_debug,
            "pipeline_run_id": request.pipeline_run_id,
        },
        "embedding_cache": _active_embedding_cache_summary(db=db),
        "rerank": rerank_summary,
        "timing_ms": {
            "retrieval": retrieval_ms,
        },
        "query_rewrite": dict(retrieval_result.debug_info.get("query_rewrite") or {}),
        "results": [
            {
                "chunk_id": context.chunk_id,
                "score": context.score,
                "citation": context.citation,
                "source_type": context.source_kind,
                "document": {
                    "id": context.document_id,
                    "version_id": context.document_version_id,
                    "title": context.document_title,
                    "url": _citation_document_url(
                        RagCitation(
                            chunk_id=context.chunk_id,
                            source_kind=context.source_kind,
                            citation_label=context.citation,
                            document_id=context.document_id,
                            document_title=context.document_title,
                            document_url=context.document_url,
                            start_page=context.start_page,
                            end_page=context.end_page,
                            score=context.score,
                            section_path=list(context.section_path),
                            document_version_id=context.document_version_id,
                        )
                    ),
                    "original_url": context.document_url,
                },
                "semantic_topic_labels": list(context.semantic_topic_labels),
                "primary_topic": context.primary_topic,
                "secondary_topics": list(context.secondary_topics),
            }
            for context in retrieval_result.contexts
        ],
    }


@app.get("/document-versions/{document_version_id}/source.pdf")
def document_version_source_pdf(document_version_id: int, db=Depends(get_db)) -> FileResponse:
    allowed_docvers = _pdf_first_allowed_document_version_ids()
    if allowed_docvers is not None and document_version_id not in allowed_docvers:
        raise HTTPException(status_code=404, detail="pdf_first_document_version_not_found")
    row = db.execute(
        select(DocumentVersion, Document)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(DocumentVersion.id == document_version_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="document_version_not_found")
    version, document = row
    resolved = _resolve_document_version_source_pdf_path(
        document_version_id=document_version_id,
        storage_uri=str(version.storage_uri or ""),
        document_url=str(document.canonical_url or ""),
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="document_version_file_missing")
    resolved = resolved.resolve()
    workspace_root = Path.cwd().resolve()
    if not str(resolved).startswith(str(workspace_root) + os.sep):
        raise HTTPException(status_code=403, detail="document_version_storage_forbidden")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="document_version_file_missing")
    return FileResponse(
        resolved,
        media_type="application/pdf",
        filename=resolved.name,
        content_disposition_type="inline",
    )


def _resolve_document_version_source_pdf_path(*, document_version_id: int, storage_uri: str, document_url: str = "") -> Path | None:
    storage_uri = str(storage_uri or "").strip()
    candidates: list[Path] = []
    candidates.extend(_local_pdf_first_batch_candidates(document_url))
    if storage_uri:
        storage_path = Path(storage_uri)
        candidates.append(storage_path if storage_path.is_absolute() else Path.cwd() / storage_path)
        if not storage_path.is_absolute():
            candidates.append(Path.cwd() / "storage" / "raw" / storage_path)
    batch_pdf = _pdf_first_batch_pdf_path_for_docver(document_version_id)
    if batch_pdf is not None:
        candidates.append(batch_pdf)

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def _local_pdf_first_batch_candidates(document_url: str) -> list[Path]:
    parsed = urlparse(str(document_url or "").strip())
    if parsed.scheme != "local" or parsed.netloc != "pdf_first_batch":
        return []
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return []
    city = parts[0]
    rel = Path(*parts[1:])
    project_root = Path(__file__).resolve().parents[2]
    return [
        project_root / "rag_eval" / "data" / "raw_docs" / f"{city}_council_by_year" / rel,
        project_root / "rag_eval" / "data" / "raw_docs" / f"{city}_council_protocols" / rel,
        project_root / "storage" / "raw" / "tree" / city / "city_council_protocols" / rel,
    ]


def _pdf_first_batch_pdf_path_for_docver(document_version_id: int) -> Path | None:
    status_path = Path("rag_eval/runs/ashdod_pdf_first_batch/batch_status.jsonl")
    if not status_path.exists():
        return None
    try:
        lines = status_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("status") != "completed":
            continue
        try:
            row_docver_id = int(payload.get("docver_id") or 0)
        except (TypeError, ValueError):
            continue
        if row_docver_id != int(document_version_id):
            continue
        pdf_path = str(payload.get("pdf") or "").strip()
        if not pdf_path:
            return None
        candidate = Path(pdf_path)
        return candidate if candidate.is_absolute() else Path.cwd() / candidate
    return None


PIPELINE_RUNS_ROOT = Path("rag_eval/runs").resolve()


@app.get("/pipeline-runs/{run_id}/artifacts")
def pipeline_run_artifacts(run_id: str) -> dict:
    manifest = _load_pipeline_artifact_manifest(run_id)
    artifacts = []
    for artifact in manifest.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        key = str(artifact.get("key") or "").strip()
        if not key:
            continue
        artifacts.append(
            {
                "key": key,
                "label": str(artifact.get("label") or key),
                "kind": str(artifact.get("kind") or "file"),
                "url": f"/pipeline-runs/{run_id}/artifacts/{key}",
                "bytes": artifact.get("bytes"),
                "path": artifact.get("path"),
            }
        )
    return {
        "run_id": run_id,
        "title": manifest.get("title"),
        "document_id": manifest.get("document_id"),
        "document_version_id": manifest.get("document_version_id"),
        "artifacts": artifacts,
    }


@app.get("/pipeline-runs/{run_id}/artifacts/{artifact_key}")
def pipeline_run_artifact_file(run_id: str, artifact_key: str) -> FileResponse:
    manifest = _load_pipeline_artifact_manifest(run_id)
    for artifact in manifest.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        if str(artifact.get("key") or "").strip() != artifact_key:
            continue
        path = _resolve_pipeline_artifact_path(run_id=run_id, artifact=artifact)
        media_type = str(artifact.get("content_type") or "application/octet-stream")
        filename = path.name
        return FileResponse(path, media_type=media_type, filename=filename)
    raise HTTPException(status_code=404, detail="pipeline_artifact_not_found")


def _load_pipeline_artifact_manifest(run_id: str) -> dict[str, Any]:
    normalized_run_id = _validate_pipeline_run_id(run_id)
    manifest_path = PIPELINE_RUNS_ROOT / normalized_run_id / "artifact_manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="pipeline_run_manifest_not_found")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="pipeline_run_manifest_invalid_json") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="pipeline_run_manifest_invalid")
    return payload


def _validate_pipeline_run_id(run_id: str) -> str:
    normalized = str(run_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", normalized):
        raise HTTPException(status_code=400, detail="pipeline_run_id_invalid")
    return normalized


def _resolve_pipeline_artifact_path(*, run_id: str, artifact: dict[str, Any]) -> Path:
    normalized_run_id = _validate_pipeline_run_id(run_id)
    run_root = (PIPELINE_RUNS_ROOT / normalized_run_id).resolve()
    raw_path = str(artifact.get("path") or "").strip()
    if not raw_path:
        raise HTTPException(status_code=404, detail="pipeline_artifact_path_missing")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = run_root / candidate
    resolved = candidate.resolve()
    if not str(resolved).startswith(str(run_root) + os.sep):
        raise HTTPException(status_code=403, detail="pipeline_artifact_path_forbidden")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="pipeline_artifact_file_missing")
    return resolved


@app.get("/ask", response_class=HTMLResponse)
@app.get("/ui/ask", response_class=HTMLResponse)
def ask_playground_page() -> HTMLResponse:
    return HTMLResponse(
        render_rag_dashboard_page(initial_gis_map_payload=None),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/ui/subjects", response_class=HTMLResponse)
def subject_browser_page() -> HTMLResponse:
    return HTMLResponse(
        render_subject_browser_page(),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/debug", response_class=HTMLResponse)
@app.get("/ui/debug", response_class=HTMLResponse)
def debug_playground_page() -> HTMLResponse:
    html_page = """
<!doctype html>
<html lang="en" dir="ltr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Ask Playground</title>
  <style>
    :root {
      --bg-a: #f3f7fb;
      --bg-b: #fcfaf4;
      --ink: #15313c;
      --muted: #536b75;
      --panel: #ffffff;
      --line: #d6e3ec;
      --accent: #0b7380;
      --warn: #a4511a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Inter", "Segoe UI", "Helvetica Neue", sans-serif;
      color: var(--ink);
      background: radial-gradient(1000px 420px at 100% -10%, #dceff7 0%, transparent 70%),
                  radial-gradient(820px 340px at -8% 110%, #f9ecd3 0%, transparent 70%),
                  linear-gradient(135deg, var(--bg-a), var(--bg-b));
      min-height: 100vh;
      padding: 22px;
    }
    .card {
      max-width: 980px;
      margin: 0 auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 24px;
      box-shadow: 0 16px 34px rgba(21, 49, 60, 0.11);
    }
    h1 { margin: 0 0 8px; font-size: 1.62rem; }
    p { margin: 0; line-height: 1.7; }
    .muted { color: var(--muted); font-size: 0.92rem; }
    .ask-form { margin-top: 14px; display: grid; gap: 12px; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 10px;
    }
    .field { display: grid; gap: 6px; }
    .field label, .group legend { font-weight: 600; font-size: 0.92rem; }
    textarea,
    input,
    select {
      width: 100%;
      border: 1px solid #bfd3de;
      border-radius: 10px;
      padding: 8px 10px;
      font-family: inherit;
      color: inherit;
      background: #fff;
    }
    textarea {
      min-height: 108px;
      line-height: 1.65;
      resize: vertical;
      direction: rtl;
      text-align: right;
    }
    .he-input { direction: rtl; text-align: right; }
    .group {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px;
      margin: 0;
    }
    .checks { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 8px; }
    .checks label { display: flex; align-items: center; gap: 6px; font-size: 0.92rem; }
    .checks input { width: auto; }
    .actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    button {
      border: 1px solid #0e6a76;
      background: var(--accent);
      color: #fff;
      border-radius: 999px;
      padding: 8px 18px;
      font-family: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    button:hover { background: #095f6a; }
    .output {
      margin-top: 14px;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: #fff;
    }
    .output.refusal { border-color: #ebd6c5; background: #fff8f2; }
    .output.warning { border-color: #f1d39d; background: #fff8e7; }
    .answer-text, .refusal-text { direction: rtl; text-align: right; white-space: pre-line; }
    .answer-sections { margin-top: 10px; display: grid; gap: 10px; }
    .topic-tree-body { display: grid; gap: 10px; }
    .protocol-group { border: 1px solid #d7e5ee; border-radius: 11px; padding: 10px; background: #fdfefe; }
    .protocol-group > h4 { margin: 0 0 8px; font-size: 0.96rem; color: #23424f; }
    .topic-root, .topic-child { border: 1px solid #e0eaf0; border-radius: 9px; background: #fff; }
    .topic-root + .topic-root { margin-top: 8px; }
    .topic-root > summary, .topic-child > summary {
      cursor: pointer;
      list-style: none;
      padding: 8px 10px;
      font-weight: 600;
      color: #2f4d58;
      border-bottom: 1px solid #ecf2f7;
    }
    .topic-root > summary::-webkit-details-marker, .topic-child > summary::-webkit-details-marker { display: none; }
    .topic-root > summary::before, .topic-child > summary::before { content: "▾ "; color: #668390; }
    .topic-root:not([open]) > summary::before, .topic-child:not([open]) > summary::before { content: "▸ "; }
    .topic-root-content { padding: 8px; display: grid; gap: 8px; }
    .topic-child-content { padding: 8px 10px; display: grid; gap: 8px; }
    .answer-section { border: 1px solid #dde7ee; border-radius: 10px; padding: 10px; background: #fbfdff; }
    .answer-section h4 { margin: 0 0 4px; font-size: 0.98rem; }
    .answer-section .topic { margin: 0 0 6px; color: #4c5963; font-size: 0.88rem; }
    .answer-section .text { margin: 0; direction: rtl; text-align: right; }
    .answer-section .extended { margin: 8px 0 0; direction: rtl; text-align: right; color: #30404a; border-top: 1px dashed #d6e4ec; padding-top: 8px; }
    .decision-sources { margin: 0; padding-inline-start: 18px; }
    .decision-sources li { margin: 4px 0; }
    ul { margin: 8px 0 0; padding-inline-start: 20px; }
    li { margin: 6px 0; line-height: 1.5; }
    a { color: #00696f; text-decoration: none; border-bottom: 1px dotted #8fbac2; }
    a:hover { border-bottom-style: solid; }
    pre {
      margin: 8px 0 0;
      border-radius: 10px;
      border: 1px solid #d9e6ee;
      background: #f9fbfd;
      padding: 10px;
      overflow-x: auto;
      white-space: pre-wrap;
      font-size: 0.84rem;
      line-height: 1.45;
      direction: ltr;
      text-align: left;
      max-height: 260px;
    }
    .hidden { display: none; }
  </style>
</head>
<body>
  <article class="card">
    <h1>Ask Playground</h1>
    <p class="muted">General UI for <code>POST /ask</code>. Fill optional filters and inspect PDF-first retrieval, answering, citation validation, and LLM debug data.</p>
    <form id="ask-playground-form" class="ask-form">
      <div class="field">
        <label for="ask-question">Question (Hebrew recommended)</label>
        <textarea id="ask-question" name="question" required placeholder="מה הוחלט בעיר?">מה הוחלט בעיר?</textarea>
      </div>
      <section class="grid">
        <div class="field hidden">
          <label for="ask-muni">muni</label>
          <input id="ask-muni" name="muni" type="text" value="ashdod" />
        </div>
        <div class="field hidden">
          <label for="ask-topic">topic</label>
          <input id="ask-topic" class="he-input" name="topic" type="text" placeholder="optional" />
        </div>
        <div class="field hidden">
          <label for="ask-year">year</label>
          <input id="ask-year" name="year" type="number" min="2000" max="2100" placeholder="optional" />
        </div>
        <div class="field">
          <label for="ask-top-k">top_k</label>
          <input id="ask-top-k" name="top_k" type="number" min="1" max="50" value="8" />
          <span class="muted">How many chunks retrieval returns before answering (higher can be slower and costlier).</span>
        </div>
      </section>
      <div class="actions">
        <label class="muted"><input id="ask-debug-mode" type="checkbox" checked /> debug mode (show PDF-first debug)</label>
        <label class="muted hidden"><input id="ask-show-extended" type="checkbox" /> show extended answer</label>
        <button type="submit">Send Ask Request</button>
        <span id="ask-playground-status" class="muted" aria-live="polite"></span>
      </div>
    </form>

    <section id="ask-playground-answer" class="output hidden">
      <h3>Answer</h3>
      <p id="ask-playground-answer-text" class="answer-text"></p>
      <div id="ask-playground-answer-sections" class="answer-sections"></div>
      <details id="ask-playground-extended-panel" class="hidden">
        <summary>Extended answer (aggregate fallback)</summary>
        <p id="ask-playground-extended-text" class="answer-text"></p>
      </details>
      <ul id="ask-playground-limitations"></ul>
    </section>

    <section id="ask-playground-provider-warning" class="output warning hidden">
      <h3>Provider Warning</h3>
      <p id="ask-playground-provider-warning-text" class="answer-text"></p>
    </section>

    <section id="ask-playground-refusal" class="output refusal hidden">
      <h3>Refusal</h3>
      <p id="ask-playground-refusal-text" class="refusal-text"></p>
    </section>

    <section id="ask-playground-citations" class="output hidden">
      <h3>Citations</h3>
      <ul id="ask-playground-citations-list"></ul>
    </section>

    <section id="ask-playground-thresholds" class="output hidden">
      <h3>PDF-first debug</h3>
      <pre id="ask-playground-thresholds-json"></pre>
    </section>

    <section id="ask-playground-trace" class="output hidden">
      <h3>PDF-first validation</h3>
      <ul id="ask-playground-trace-list"></ul>
    </section>

    <section id="ask-playground-artifacts" class="output hidden">
      <h3>Pipeline Artifacts</h3>
      <p id="ask-playground-artifacts-title" class="muted"></p>
      <ul id="ask-playground-artifacts-list"></ul>
    </section>

    <section class="output">
      <h3>Debug</h3>
      <ul id="ask-playground-meta"></ul>
      <details>
        <summary>Raw JSON</summary>
        <pre id="ask-playground-json"></pre>
      </details>
    </section>

  </article>
  <script>
    (() => {
      const form = document.getElementById("ask-playground-form");
      if (!form) {
        return;
      }

      const questionInput = document.getElementById("ask-question");
      const topicInput = document.getElementById("ask-topic");
      const yearInput = document.getElementById("ask-year");
      const topKInput = document.getElementById("ask-top-k");
      const debugModeInput = document.getElementById("ask-debug-mode");
      const showExtendedInput = document.getElementById("ask-show-extended");

      const statusNode = document.getElementById("ask-playground-status");
      const answerPanel = document.getElementById("ask-playground-answer");
      const answerText = document.getElementById("ask-playground-answer-text");
      const answerSectionsNode = document.getElementById("ask-playground-answer-sections");
      const extendedPanel = document.getElementById("ask-playground-extended-panel");
      const extendedText = document.getElementById("ask-playground-extended-text");
      const limitationsList = document.getElementById("ask-playground-limitations");
      const providerWarningPanel = document.getElementById("ask-playground-provider-warning");
      const providerWarningText = document.getElementById("ask-playground-provider-warning-text");
      const refusalPanel = document.getElementById("ask-playground-refusal");
      const refusalText = document.getElementById("ask-playground-refusal-text");
      const citationsPanel = document.getElementById("ask-playground-citations");
      const citationsList = document.getElementById("ask-playground-citations-list");
      const thresholdsPanel = document.getElementById("ask-playground-thresholds");
      const thresholdsJson = document.getElementById("ask-playground-thresholds-json");
      const tracePanel = document.getElementById("ask-playground-trace");
      const traceList = document.getElementById("ask-playground-trace-list");
      const artifactsPanel = document.getElementById("ask-playground-artifacts");
      const artifactsTitle = document.getElementById("ask-playground-artifacts-title");
      const artifactsList = document.getElementById("ask-playground-artifacts-list");
      const metaList = document.getElementById("ask-playground-meta");
      const rawJson = document.getElementById("ask-playground-json");
      let lastExtendedAnswer = null;
      let lastAnswerSections = [];
      let lastExtendedSections = [];
      let lastAnswerText = "";
      let lastCitations = [];

      const hide = (node) => {
        if (node) {
          node.classList.add("hidden");
        }
      };
      const show = (node) => {
        if (node) {
          node.classList.remove("hidden");
        }
      };
      const resetList = (node) => {
        if (node) {
          node.innerHTML = "";
        }
      };
      const appendItem = (node, text) => {
        if (!node || !text) {
          return;
        }
        const item = document.createElement("li");
        item.textContent = text;
        node.appendChild(item);
      };

      const RELATIONAL_TOKENS = new Set([
        "לגבי", "בנוגע", "באשר", "בנושא", "עבור", "בעקבות", "בתחום", "בתחומי", "לצורך", "עם", "מול", "בין",
        "ללא", "תוך", "כדי", "לשם", "של", "על", "אל", "את", "מן", "מ", "בהמשך"
      ]);
      const NON_INFORMATIONAL_TOKENS = new Set([
        "אחראי", "אחראית", "אחריות", "מזכירת", "הועדה", "ועדה", "יו", "ר", "מנכ", "ל", "מח", "מחלקת",
        "תנועה", "תחבורה", "גב", "ד", "ר", "עו", "ד", "שוטף", "בהתאם", "יוכנו", "להלן"
      ]);

      const sanitizeInformationalText = (value) => {
        let cleaned = normalizeText(value);
        if (!cleaned) {
          return "";
        }

        cleaned = cleaned.replace(/_{3,}.*/g, "").trim();
        cleaned = cleaned.replace(/\\s[-–]\\s(?:מח|מטה|משרד|ועדה|יו|מנכ|מנהל|מנהלת).*/g, "").trim();
        cleaned = cleaned.replace(/\\b(?:מזכירת|יו"ר|מנכ"ל|מנהלת|מח'|מחלקת)\\b.*$/g, "").trim();
        return cleaned;
      };

      const normalizeText = (value) => String(value || "").replace(/\\s+/g, " ").trim().toLowerCase();
      const tokenizeText = (value) => {
        const normalized = normalizeText(value);
        if (!normalized) {
          return [];
        }
        return normalized.match(/[\u0590-\u05FF]{2,}|[a-z0-9]{2,}/g) || [];
      };
      const tokenizeMeaningfulText = (value) => {
        return tokenizeText(value).filter(
          (token) => token.length >= 3 && !RELATIONAL_TOKENS.has(token) && !NON_INFORMATIONAL_TOKENS.has(token)
        );
      };
      const isAlmostEqualText = (left, right) => {
        const leftNorm = normalizeText(left);
        const rightNorm = normalizeText(right);
        if (!leftNorm || !rightNorm) {
          return false;
        }
        if (leftNorm === rightNorm) {
          return true;
        }

        const leftTokens = new Set(tokenizeText(leftNorm));
        const rightTokens = new Set(tokenizeText(rightNorm));
        if (leftTokens.size === 0 || rightTokens.size === 0) {
          return false;
        }

        let overlapCount = 0;
        for (const token of leftTokens) {
          if (rightTokens.has(token)) {
            overlapCount += 1;
          }
        }

        const overlap = overlapCount / Math.max(leftTokens.size, rightTokens.size, 1);
        const lenRatio = Math.min(leftNorm.length, rightNorm.length) / Math.max(leftNorm.length, rightNorm.length, 1);
        return (overlap >= 0.92 && lenRatio >= 0.82) || overlap >= 0.97;
      };
      const hasMeaningfulExtraInfo = (baseText, extendedText) => {
        if (!extendedText || !String(extendedText).trim()) {
          return false;
        }
        const baseSanitized = sanitizeInformationalText(baseText);
        const extendedSanitized = sanitizeInformationalText(extendedText);
        if (!extendedSanitized) {
          return false;
        }
        if (isAlmostEqualText(baseSanitized, extendedSanitized)) {
          return false;
        }

        const baseMeaningful = new Set(tokenizeMeaningfulText(baseSanitized));
        const extMeaningful = new Set(tokenizeMeaningfulText(extendedSanitized));
        const novelTokens = [];
        for (const token of extMeaningful) {
          if (!baseMeaningful.has(token)) {
            novelTokens.push(token);
          }
        }

        if (novelTokens.length < 4) {
          return false;
        }

        const baseNorm = normalizeText(baseSanitized);
        const extNorm = normalizeText(extendedSanitized);
        if ((extNorm.length - baseNorm.length) < 40) {
          return false;
        }
        const growthRatio = extNorm.length / Math.max(baseNorm.length, 1);
        const noveltyRatio = novelTokens.length / Math.max(extMeaningful.size, 1);
        if (growthRatio < 1.3 && noveltyRatio < 0.35) {
          return false;
        }
        if (growthRatio < 1.35 && !(noveltyRatio >= 0.45 && novelTokens.length >= 6)) {
          return false;
        }
        return true;
      };
      const splitTopicPath = (section) => {
        const topicRoot = typeof section?.topic_root === "string" ? section.topic_root.trim() : "";
        const topicChild = typeof section?.topic_child === "string" ? section.topic_child.trim() : "";
        if (topicRoot || topicChild) {
          return {
            root: topicRoot || topicChild || "ללא תיוג סמנטי",
            child: topicChild,
          };
        }

        const rawTopic = String((section && typeof section === "object" ? section.topic_name : "") || "").trim();
        const parts = rawTopic.split(">").map((part) => part.trim()).filter(Boolean);
        if (parts.length >= 2) {
          return {
            root: parts[0],
            child: parts.slice(1).join(" > "),
          };
        }

        return {
          root: parts[0] || "ללא תיוג סמנטי",
          child: "",
        };
      };
      const citationByChunkId = () => {
        const byChunk = new Map();
        const rows = Array.isArray(lastCitations) ? lastCitations : [];
        for (const row of rows) {
          if (!row || typeof row !== "object") {
            continue;
          }
          const chunkId = typeof row.chunk_id === "string" ? row.chunk_id : "";
          if (!chunkId || byChunk.has(chunkId)) {
            continue;
          }
          byChunk.set(chunkId, row);
        }
        return byChunk;
      };

      const renderAnswerSections = () => {
        if (!answerSectionsNode) {
          return;
        }
        answerSectionsNode.innerHTML = "";

        const sections = Array.isArray(lastAnswerSections) ? lastAnswerSections : [];
        const extendedSections = Array.isArray(lastExtendedSections) ? lastExtendedSections : [];
        const shouldShowExtended = Boolean(showExtendedInput && showExtendedInput.checked);
        const citationMap = citationByChunkId();

        const protocolOrder = [];
        const protocolBuckets = new Map();

        for (let idx = 0; idx < sections.length; idx += 1) {
          const section = sections[idx];
          if (!section || typeof section !== "object") {
            continue;
          }

          const protocolTitle = typeof section.protocol_title === "string" ? section.protocol_title : "פרוטוקול";
          const conciseText = typeof section.text === "string" ? section.text : "";
          if (!conciseText.trim()) {
            continue;
          }

          const topicPath = splitTopicPath(section);
          const extSection = extendedSections[idx];
          const extText = extSection && typeof extSection === "object" && typeof extSection.text === "string"
            ? extSection.text
            : "";
          const chunkIds = Array.isArray(section.chunk_ids) ? section.chunk_ids : [];

          if (!protocolBuckets.has(protocolTitle)) {
            protocolBuckets.set(protocolTitle, []);
            protocolOrder.push(protocolTitle);
          }
          protocolBuckets.get(protocolTitle).push({
            root: topicPath.root,
            child: topicPath.child,
            text: conciseText,
            extText,
            chunkIds,
          });
        }

        for (const protocolTitle of protocolOrder) {
          const protocolNode = document.createElement("section");
          protocolNode.className = "protocol-group";

          const protocolHeader = document.createElement("h4");
          protocolHeader.textContent = protocolTitle;
          protocolNode.appendChild(protocolHeader);

          const rows = protocolBuckets.get(protocolTitle) || [];
          const rootOrder = [];
          const rootBuckets = new Map();
          for (const row of rows) {
            if (!rootBuckets.has(row.root)) {
              rootBuckets.set(row.root, []);
              rootOrder.push(row.root);
            }
            rootBuckets.get(row.root).push(row);
          }

          for (const rootLabel of rootOrder) {
            const rootDetails = document.createElement("details");
            rootDetails.className = "topic-root";
            rootDetails.open = true;

            const rootSummary = document.createElement("summary");
            rootSummary.textContent = rootLabel;
            rootDetails.appendChild(rootSummary);

            const rootContent = document.createElement("div");
            rootContent.className = "topic-root-content";

            const rowsForRoot = rootBuckets.get(rootLabel) || [];
            for (const row of rowsForRoot) {
              const childContent = document.createElement("div");
              childContent.className = row.child ? "topic-child-content" : "topic-root-leaf";

              const textNode = document.createElement("p");
              textNode.className = "text";
              textNode.textContent = row.text;
              childContent.appendChild(textNode);

              if (shouldShowExtended && hasMeaningfulExtraInfo(row.text, row.extText)) {
                const extNode = document.createElement("p");
                extNode.className = "extended";
                extNode.textContent = `הרחבה: ${row.extText}`;
                childContent.appendChild(extNode);
              }

              const sourceRows = [];
              for (const chunkId of row.chunkIds) {
                if (typeof chunkId !== "string") {
                  continue;
                }
                const citation = citationMap.get(chunkId);
                if (citation) {
                  sourceRows.push(citation);
                }
              }
              if (sourceRows.length > 0) {
                const sourceTitle = document.createElement("p");
                sourceTitle.className = "topic";
                sourceTitle.textContent = "מקורות:";
                childContent.appendChild(sourceTitle);

                const sourceList = document.createElement("ul");
                sourceList.className = "decision-sources";
                for (const citation of sourceRows) {
                  const doc = citation.document && typeof citation.document === "object" ? citation.document : {};
                  const page = Number.isFinite(Number(citation.start_page)) ? Number(citation.start_page) : null;
                  const hrefBase = doc.url || "#";
                  const sourceItem = document.createElement("li");
                  const sourceLink = document.createElement("a");
                  sourceLink.href = page ? `${hrefBase}#page=${page}` : hrefBase;
                  sourceLink.target = "_blank";
                  sourceLink.rel = "noopener";
                  sourceLink.textContent = doc.title || "document";
                  sourceItem.appendChild(sourceLink);

                  const sourceMeta = document.createElement("span");
                  sourceMeta.className = "muted";
                  const citationLabel = citation.citation || (page ? `עמוד ${page}` : citation.chunk_id || "source");
                  sourceMeta.textContent = ` [${citation.source_type || "source"} | ${citationLabel}]`;
                  sourceItem.appendChild(sourceMeta);

                  sourceList.appendChild(sourceItem);
                }
                childContent.appendChild(sourceList);
              }

              if (row.child) {
                const childDetails = document.createElement("details");
                childDetails.className = "topic-child";
                childDetails.open = true;

                const childSummary = document.createElement("summary");
                childSummary.textContent = row.child;
                childDetails.appendChild(childSummary);
                childDetails.appendChild(childContent);
                rootContent.appendChild(childDetails);
              } else {
                rootContent.appendChild(childContent);
              }
            }

            rootDetails.appendChild(rootContent);
            protocolNode.appendChild(rootDetails);
          }

          answerSectionsNode.appendChild(protocolNode);
        }
      };

      const renderExtendedAnswer = () => {
        const shouldShow = Boolean(showExtendedInput && showExtendedInput.checked);
        const hasExtended = typeof lastExtendedAnswer === "string" && lastExtendedAnswer.trim().length > 0;
        const hasSections = Array.isArray(lastAnswerSections) && lastAnswerSections.length > 0;
        if (!extendedPanel || !extendedText) {
          return;
        }

        if (hasSections) {
          extendedText.textContent = "";
          hide(extendedPanel);
          return;
        }

        if (shouldShow && hasExtended && hasMeaningfulExtraInfo(lastAnswerText, lastExtendedAnswer)) {
          extendedText.textContent = lastExtendedAnswer;
          show(extendedPanel);
          return;
        }
        extendedText.textContent = "";
        hide(extendedPanel);
      };

      if (showExtendedInput) {
        showExtendedInput.addEventListener("change", () => {
          renderAnswerSections();
          renderExtendedAnswer();
        });
      }
      const cleanText = (value) => {
        const compact = String(value || "").trim();
        return compact || null;
      };
      const applyQueryParams = () => {
        const params = new URLSearchParams(window.location.search || "");
        const assign = (node, key) => {
          const value = params.get(key);
          if (node && value !== null) {
            node.value = value;
          }
        };
        assign(questionInput, "question");
        assign(yearInput, "year");
        assign(topicInput, "topic");
      };
      applyQueryParams();

      const loadPipelineArtifacts = async (runId) => {
        resetList(artifactsList);
        if (artifactsTitle) {
          artifactsTitle.textContent = "";
        }
        if (!runId) {
          hide(artifactsPanel);
          return;
        }
        try {
          const response = await fetch(`/pipeline-runs/${encodeURIComponent(runId)}/artifacts`);
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          const payload = await response.json();
          if (artifactsTitle) {
            artifactsTitle.textContent = `${payload.title || runId} · document_id=${payload.document_id || "-"} · document_version_id=${payload.document_version_id || "-"}`;
          }
          for (const artifact of payload.artifacts || []) {
            const item = document.createElement("li");
            const link = document.createElement("a");
            link.href = artifact.url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = artifact.label || artifact.key;
            item.appendChild(link);
            if (artifact.kind || artifact.bytes) {
              const meta = document.createElement("span");
              meta.className = "muted";
              meta.textContent = ` (${artifact.kind || "file"}${artifact.bytes ? `, ${artifact.bytes} bytes` : ""})`;
              item.appendChild(meta);
            }
            artifactsList.appendChild(item);
          }
          show(artifactsPanel);
        } catch (error) {
          appendItem(artifactsList, `Could not load artifacts for ${runId}: ${error.message || error}`);
          show(artifactsPanel);
        }
      };

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const question = cleanText(questionInput.value);
        if (!question) {
          statusNode.textContent = "Please enter a question before submitting.";
          return;
        }

        const topKRaw = parseInt(topKInput.value || "8", 10);
        const topK = Number.isFinite(topKRaw) ? Math.max(1, Math.min(50, topKRaw)) : 8;
        const payload = {
          question: question,
          top_k: topK,
          muni: "ashdod",
          debug_mode: Boolean(debugModeInput && debugModeInput.checked),
        };

        statusNode.textContent = "Submitting ask request...";
        hide(answerPanel);
        hide(providerWarningPanel);
        hide(refusalPanel);
        hide(citationsPanel);
        hide(thresholdsPanel);
        hide(tracePanel);
        hide(artifactsPanel);
        hide(extendedPanel);
        resetList(limitationsList);
        resetList(citationsList);
        resetList(traceList);
        resetList(artifactsList);
        resetList(metaList);
        if (answerSectionsNode) {
          answerSectionsNode.innerHTML = "";
        }
        rawJson.textContent = "";
        thresholdsJson.textContent = "";
        if (extendedText) {
          extendedText.textContent = "";
        }
        if (providerWarningText) {
          providerWarningText.textContent = "";
        }
        lastExtendedAnswer = null;
        lastAnswerSections = [];
        lastExtendedSections = [];
        lastAnswerText = "";
        lastCitations = [];

        try {
          const response = await fetch("/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          if (!response.ok) {
            const errorBody = await response.text().catch(() => "");
            const errorSuffix = errorBody ? `: ${errorBody.slice(0, 220)}` : "";
            throw new Error(`HTTP ${response.status}${errorSuffix}`);
          }

          const data = await response.json();
          rawJson.textContent = JSON.stringify(data, null, 2);
          const providerWarning = data.provider_warning && typeof data.provider_warning === "object" ? data.provider_warning : null;

          const retrieval = data.retrieval && typeof data.retrieval === "object" ? data.retrieval : {};
          const model = data.model && typeof data.model === "object" ? data.model : {};
          appendItem(metaList, `ask_request_id: ${data.ask_request_id || "-"}`);
          appendItem(metaList, `status: ${data.status || "-"}`);
          appendItem(metaList, `retrieval_set_id: ${retrieval.retrieval_set_id || "-"}`);
          appendItem(metaList, `retrieval_count: ${retrieval.count || 0}`);
          appendItem(metaList, `retrieved_source_types: ${(retrieval.source_types || []).join(", ") || "-"}`);
          appendItem(metaList, `effective_document_version_ids: ${(retrieval.effective_document_version_ids || []).join(", ") || "-"}`);
          appendItem(metaList, `forced_pdf_first_scope: ${retrieval.forced_pdf_first_scope === true ? "yes" : "no"}`);
          appendItem(metaList, `model: ${model.provider || "-"} / ${model.name || "-"}`);
          const scoring = data.scoring && typeof data.scoring === "object" ? data.scoring : {};
          appendItem(metaList, `answer_generation_route: ${scoring.answer_generation_route || "-"}`);
          hide(artifactsPanel);

          const debugPayload = data.debug && typeof data.debug === "object" ? data.debug : null;
          const debugTiming = debugPayload && debugPayload.timing_ms && typeof debugPayload.timing_ms === "object"
            ? debugPayload.timing_ms
            : null;
          if (debugTiming) {
            appendItem(metaList, `timing_ms.request_total: ${debugTiming.request_total ?? "-"}`);
            appendItem(metaList, `timing_ms.retrieval: ${debugTiming.retrieval ?? "-"}`);
            appendItem(metaList, `timing_ms.answering: ${debugTiming.answering ?? "-"}`);
          }

          const retrievalTrace = debugPayload && debugPayload.retrieval_trace && typeof debugPayload.retrieval_trace === "object"
            ? debugPayload.retrieval_trace
            : null;
          const embeddingRerank = retrievalTrace && retrievalTrace.embedding_rerank && typeof retrievalTrace.embedding_rerank === "object"
            ? retrievalTrace.embedding_rerank
            : null;
          if (embeddingRerank) {
            appendItem(metaList, `embedding_rerank.enabled: ${embeddingRerank.enabled === true ? "yes" : "no"}`);
            appendItem(metaList, `embedding_rerank.query_embedding_used: ${embeddingRerank.query_embedding_used === true ? "yes" : "no"}`);
            appendItem(metaList, `embedding_rerank.candidate_count: ${embeddingRerank.candidate_count ?? "-"}`);
            appendItem(metaList, `embedding_rerank.available_chunk_embeddings: ${embeddingRerank.available_chunk_embeddings ?? "-"}`);
            appendItem(metaList, `embedding_rerank.created_chunk_embeddings: ${embeddingRerank.created_chunk_embeddings ?? "-"}`);
          }

          const thresholds = data.debug && data.debug.thresholds ? data.debug.thresholds : null;
          if (thresholds) {
            thresholdsJson.textContent = JSON.stringify(thresholds, null, 2);
            show(thresholdsPanel);
          }

          const trace = debugPayload && debugPayload.answering_trace && typeof debugPayload.answering_trace === "object"
            ? debugPayload.answering_trace
            : null;
          if (trace) {
            appendItem(traceList, `answer_generation_route: ${trace.answer_generation_route || "-"}`);
            appendItem(traceList, `pdf_first_retry_attempted: ${trace.pdf_first_retry_attempted === true ? "yes" : "no"}`);
            appendItem(traceList, `pdf_first_retry_succeeded: ${trace.pdf_first_retry_succeeded === true ? "yes" : "no"}`);
            appendItem(traceList, `pdf_first_retry_reason: ${trace.pdf_first_retry_reason || "-"}`);
            appendItem(traceList, `pdf_first_claim_repair_used: ${trace.pdf_first_claim_repair_used === true ? "yes" : "no"}`);
            const pdfFirstValidation = trace.pdf_first_validation && typeof trace.pdf_first_validation === "object"
              ? trace.pdf_first_validation
              : null;
            if (pdfFirstValidation) {
              appendItem(traceList, `pdf_first_validation.accepted: ${pdfFirstValidation.accepted === true ? "yes" : "no"}`);
              appendItem(traceList, `pdf_first_validation.accepted_claim_count: ${pdfFirstValidation.accepted_claim_count ?? "-"}`);
              appendItem(traceList, `pdf_first_validation.used_chunk_ids: ${(pdfFirstValidation.used_chunk_ids || []).join(", ") || "-"}`);
            }
            const stageTiming = trace.timing_ms && typeof trace.timing_ms === "object" ? trace.timing_ms : null;
            if (stageTiming) {
              appendItem(
                traceList,
                `timing_ms: answer_call=${stageTiming.answer_call ?? "-"}, retry_call=${stageTiming.retry_call ?? "-"}, compose_total=${stageTiming.compose_total ?? "-"}`,
              );
            }
            show(tracePanel);
          } else {
            hide(tracePanel);
          }

          if (providerWarning && providerWarningText) {
            const warningBits = [providerWarning.message_he || "Provider unavailable."];
            if (providerWarning.provider || providerWarning.model) {
              warningBits.push(`(${providerWarning.provider || "-"} / ${providerWarning.model || "-"})`);
            }
            providerWarningText.textContent = warningBits.join(" ");
            show(providerWarningPanel);
            appendItem(metaList, `provider_warning.error_code: ${providerWarning.error_code || "-"}`);
            appendItem(metaList, `answer_mode: ${data.answer_mode || "-"}`);
          } else {
            hide(providerWarningPanel);
          }

          if (data.status === "answer") {
            const sections = Array.isArray(data.answer_sections) ? data.answer_sections : [];
            const extendedSections = Array.isArray(data.extended_answer_sections) ? data.extended_answer_sections : [];
            const citations = Array.isArray(data.citations) ? data.citations : [];
            lastAnswerSections = sections;
            lastExtendedSections = extendedSections;
            lastCitations = citations;

            if (sections.length > 0) {
              answerText.textContent = "";
              hide(answerText);
            } else {
              answerText.textContent = data.answer || "";
              show(answerText);
            }
            lastAnswerText = typeof data.answer === "string" ? data.answer : "";
            lastExtendedAnswer = typeof data.extended_answer === "string" ? data.extended_answer : null;
            renderAnswerSections();
            renderExtendedAnswer();
            const limitations = Array.isArray(data.limitations) ? data.limitations : [];
            if (limitations.length === 0) {
              appendItem(limitationsList, "No limitations were returned.");
            } else {
              for (const limitation of limitations) {
                appendItem(limitationsList, limitation);
              }
            }

            if (citations.length === 0) {
              appendItem(citationsList, "No citations were returned.");
            } else {
              for (const citation of citations) {
                const doc = citation.document && typeof citation.document === "object" ? citation.document : {};
                const page = Number.isFinite(Number(citation.start_page)) ? Number(citation.start_page) : null;
                const hrefBase = doc.url || "#";
                const anchor = document.createElement("a");
                anchor.href = page ? `${hrefBase}#page=${page}` : hrefBase;
                anchor.target = "_blank";
                anchor.rel = "noopener";
                anchor.textContent = citation.citation || citation.chunk_id || "source";

                const meta = document.createElement("span");
                meta.className = "muted";
                meta.textContent = ` [${citation.source_type || "source"}] ${doc.title || "document"}`;

                const item = document.createElement("li");
                item.appendChild(anchor);
                item.appendChild(meta);
                citationsList.appendChild(item);
              }
            }

            show(answerPanel);
            show(citationsPanel);
            hide(refusalPanel);
            statusNode.textContent = providerWarning && providerWarning.mock_mode === true
              ? "Provider unavailable; showing best-effort mockup answer with citations."
              : "Received grounded answer with citations.";
            return;
          }

          const refusalPayload = data.refusal && typeof data.refusal === "object" ? data.refusal : {};
          const missingTypes = Array.isArray(refusalPayload.missing_source_types)
            ? refusalPayload.missing_source_types.filter((value) => typeof value === "string" && value)
            : [];
          const refusalMessage = refusalPayload.message_he || "אין מספיק ראיות כדי להשיב.";
          const reasonCode = refusalPayload.reason_code || "-";
          const missingHint = missingTypes.length ? ` Missing sources: ${missingTypes.join(", ")}.` : "";
          refusalText.textContent = `${refusalMessage}${missingHint}`;
          appendItem(metaList, `reason_code: ${reasonCode}`);

          hide(answerPanel);
          hide(citationsPanel);
          show(refusalPanel);
          statusNode.textContent = providerWarning
            ? "Provider unavailable; no reliable mockup answer could be built."
            : "Model refused due to evidence policy.";
        } catch (_err) {
          console.error("ask_playground_request_failed", _err);
          const errorMessage = _err instanceof Error ? _err.message : String(_err || "unknown_error");
          hide(answerPanel);
          hide(citationsPanel);
          hide(refusalPanel);
          hide(providerWarningPanel);
          hide(thresholdsPanel);
          hide(tracePanel);
          hide(extendedPanel);
          statusNode.textContent = `Request failed: ${errorMessage}`;
        }
      });
    })();
  </script>
</body>
</html>
"""
    return HTMLResponse(
        html_page,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/semantic/tree")
def semantic_tree(
    request: Request,
    muni: str | None = None,
    root_id: int | None = None,
    depth: int | None = None,
    kind: str | None = None,
    status: str | None = "active",
    format: str | None = None,
    db=Depends(get_db),
) -> Any:
    stmt = select(SemanticNode)
    if muni:
        stmt = stmt.join(SourceSite, SourceSite.id == SemanticNode.source_site_id).where(
            SourceSite.municipality_slug == muni
        )
    if kind:
        stmt = stmt.where(SemanticNode.node_kind == kind)
    normalized_status = str(status or "").strip().casefold()
    if normalized_status and normalized_status != "all":
        if normalized_status not in {"active", "candidate", "deprecated", "rejected"}:
            raise HTTPException(status_code=400, detail="semantic_status_invalid")
        stmt = stmt.where(SemanticNode.status == normalized_status)

    nodes = db.execute(
        stmt.order_by(SemanticNode.depth.asc(), SemanticNode.pref_label_norm.asc(), SemanticNode.id.asc())
    ).scalars().all()
    nodes_by_id = {node.id: node for node in nodes}
    if root_id is not None and root_id not in nodes_by_id:
        raise HTTPException(status_code=404, detail="semantic_root_not_found")

    children_by_parent: dict[int, list[int]] = {}
    for node in nodes:
        if node.parent_node_id is None:
            continue
        children_by_parent.setdefault(node.parent_node_id, []).append(node.id)
    for child_ids in children_by_parent.values():
        child_ids.sort(key=lambda node_id: (nodes_by_id[node_id].pref_label_norm, node_id))

    included_ids: set[int]
    if root_id is not None:
        max_relative_depth = max(0, depth) if depth is not None else None
        included_ids = {root_id}
        frontier: list[tuple[int, int]] = [(root_id, 0)]
        while frontier:
            parent_id, relative_depth = frontier.pop(0)
            if max_relative_depth is not None and relative_depth >= max_relative_depth:
                continue
            for child_id in children_by_parent.get(parent_id, []):
                if child_id in included_ids:
                    continue
                included_ids.add(child_id)
                frontier.append((child_id, relative_depth + 1))
    else:
        included_ids = {node.id for node in nodes}
        if depth is not None:
            max_depth = max(0, depth)
            included_ids = {node.id for node in nodes if node.depth <= max_depth}

    parent_by_id = {node.id: node.parent_node_id for node in nodes}

    def path_for(node_id: int) -> list[int]:
        path = [node_id]
        parent_id = parent_by_id.get(node_id)
        while parent_id is not None and parent_id in nodes_by_id:
            path.append(parent_id)
            parent_id = parent_by_id.get(parent_id)
        return list(reversed(path))

    def payload_for(node: SemanticNode) -> dict:
        child_ids = [child_id for child_id in children_by_parent.get(node.id, []) if child_id in included_ids]
        siblings = [
            sibling_id
            for sibling_id in children_by_parent.get(node.parent_node_id, [])
            if sibling_id in included_ids and sibling_id != node.id
        ] if node.parent_node_id is not None else []
        sort_order = 0
        sibling_order = children_by_parent.get(node.parent_node_id, []) if node.parent_node_id is not None else [root.id for root in nodes if root.parent_node_id is None]
        if node.id in sibling_order:
            sort_order = sibling_order.index(node.id)
        return _semantic_node_payload(
            node,
            child_count=len(child_ids),
            child_ids=child_ids,
            sibling_ids=siblings,
            path_ids=[node_id for node_id in path_for(node.id) if node_id in included_ids],
            sort_order=sort_order,
        )

    def nested_node(node: SemanticNode) -> dict:
        payload = payload_for(node)
        payload["children"] = [nested_node(nodes_by_id[child_id]) for child_id in payload["child_ids"]]
        return payload

    items = []
    for node in nodes:
        if node.id not in included_ids:
            continue
        items.append(payload_for(node))

    roots = [
        nested_node(node)
        for node in nodes
        if node.id in included_ids and node.parent_node_id is None
    ]

    payload = {
        "count": len(items),
        "items": items,
        "root_count": len(roots),
        "roots": roots,
    }
    if _wants_semantic_tree_html(request=request, format=format):
        return HTMLResponse(_semantic_tree_html(payload, title="עץ נושאים סמנטי", subtitle="/semantic/tree"))
    return payload


def _wants_semantic_tree_html(*, request: Request | None, format: str | None) -> bool:
    if str(format or "").strip().casefold() == "json":
        return False
    if request is None:
        return False
    accept = str(request.headers.get("accept") or "").casefold()
    return "text/html" in accept and "application/json" not in accept


def _semantic_tree_html(payload: dict[str, Any], *, title: str, subtitle: str) -> str:
    roots = payload.get("roots") if isinstance(payload.get("roots"), list) else []
    rows = "\n".join(_semantic_tree_node_html(root) for root in roots)
    if not rows:
        rows = '<p class="empty">אין נושאים להצגה.</p>'
    return f"""
<!doctype html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f4ee; color: #17201b; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 32px 20px 48px; }}
    header {{ display: flex; justify-content: space-between; gap: 16px; align-items: end; margin-bottom: 24px; }}
    h1 {{ margin: 0; font-size: clamp(28px, 4vw, 44px); letter-spacing: -0.03em; }}
    .muted {{ color: #667064; font-size: 14px; }}
    .actions a {{ color: #145c40; font-weight: 700; text-decoration: none; border-bottom: 1px solid currentColor; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 22px; }}
    .metric {{ background: #fff; border: 1px solid #ded8cb; border-radius: 18px; padding: 16px; box-shadow: 0 12px 28px rgba(46, 37, 24, 0.06); }}
    .metric strong {{ display: block; font-size: 26px; }}
    .tree {{ display: grid; gap: 12px; }}
    .node {{ background: #fffdf9; border: 1px solid #ded8cb; border-radius: 18px; padding: 14px 16px; box-shadow: 0 10px 24px rgba(46, 37, 24, 0.05); }}
    .node.child {{ margin-top: 10px; margin-right: 24px; background: #fbfaf6; }}
    .label {{ font-size: 20px; font-weight: 800; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; color: #5c665d; font-size: 13px; }}
    .pill {{ border: 1px solid #d2cabb; border-radius: 999px; padding: 3px 8px; background: #f6f1e7; }}
    .empty {{ background: #fff; border-radius: 16px; padding: 18px; }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>{html.escape(title)}</h1>
        <div class="muted">{html.escape(subtitle)}</div>
      </div>
      <div class="actions"><a href="?format=json">JSON</a></div>
    </header>
    <section class="summary">
      <div class="metric"><span class="muted">שורשים</span><strong>{int(payload.get('root_count') or 0)}</strong></div>
      <div class="metric"><span class="muted">פריטים</span><strong>{int(payload.get('count') or 0)}</strong></div>
    </section>
    <section class="tree">{rows}</section>
  </main>
</body>
</html>
"""


def _semantic_tree_node_html(node: dict[str, Any], *, child: bool = False) -> str:
    label = str(node.get("label_he") or node.get("label") or "")
    status = str(node.get("status") or "")
    support = int(node.get("support_count") or 0)
    node_id = str(node.get("id") or "")
    semantic_type = str(node.get("semantic_type") or "")
    children = node.get("children") if isinstance(node.get("children"), list) else []
    child_rows = "\n".join(_semantic_tree_node_html(item, child=True) for item in children)
    css_class = "node child" if child else "node"
    return f"""
      <article class="{css_class}">
        <div class="label">{html.escape(label)}</div>
        <div class="meta">
          <span class="pill">id: {html.escape(node_id)}</span>
          <span class="pill">status: {html.escape(status)}</span>
          <span class="pill">support: {support}</span>
          <span class="pill">type: {html.escape(semantic_type)}</span>
        </div>
        {child_rows}
      </article>
    """


@app.get("/semantic/node/{node_id}")
def semantic_node_detail(node_id: int, db=Depends(get_db)) -> dict:
    node = db.execute(select(SemanticNode).where(SemanticNode.id == node_id)).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="semantic_node_not_found")

    parent = None
    if node.parent_node_id is not None:
        parent = db.execute(select(SemanticNode).where(SemanticNode.id == node.parent_node_id)).scalar_one_or_none()

    aliases = db.execute(
        select(SemanticAlias)
        .where(SemanticAlias.semantic_node_id == node.id)
        .order_by(SemanticAlias.alias_kind.asc(), SemanticAlias.alias_label_norm.asc())
    ).scalars().all()
    children = db.execute(
        select(SemanticNode)
        .where(SemanticNode.parent_node_id == node.id)
        .order_by(SemanticNode.pref_label_norm.asc(), SemanticNode.id.asc())
    ).scalars().all()

    decision_rows = db.execute(
        select(DecisionSemanticLink, Decision, Document, Meeting)
        .join(Decision, Decision.id == DecisionSemanticLink.decision_id)
        .join(Document, Document.id == Decision.source_document_id)
        .join(Meeting, Meeting.id == Decision.meeting_id)
        .where(DecisionSemanticLink.semantic_node_id == node.id)
        .order_by(Decision.id.asc())
    ).all()

    artifact_rows = db.execute(
        select(ArtifactSemanticLink, RetrievalArtifact, Document)
        .join(RetrievalArtifact, RetrievalArtifact.artifact_id == ArtifactSemanticLink.artifact_id)
        .join(Document, Document.id == RetrievalArtifact.document_id)
        .where(ArtifactSemanticLink.semantic_node_id == node.id)
        .order_by(RetrievalArtifact.document_id.asc(), RetrievalArtifact.ordinal.asc())
    ).all()

    mention_rows = db.execute(
        select(SemanticMention, Document)
        .join(Document, Document.id == SemanticMention.document_id)
        .where(SemanticMention.semantic_node_id == node.id)
        .order_by(SemanticMention.id.desc())
        .limit(30)
    ).all()

    return {
        "node": _semantic_node_payload(node, child_count=len(children)),
        "parent": _semantic_node_payload(parent, child_count=0) if parent else None,
        "children": [_semantic_node_payload(child, child_count=0) for child in children],
        "aliases": [
            {
                "id": alias.id,
                "label": alias.alias_label_he,
                "label_norm": alias.alias_label_norm,
                "kind": alias.alias_kind,
                "confidence": alias.confidence,
            }
            for alias in aliases
        ],
        "linked_decisions": [
            {
                "decision_id": decision.id,
                "relation_role": link.relation_role,
                "confidence": link.confidence,
                "source_mention_id": link.source_mention_id,
                "decision_text": decision.decision_text,
                "is_public": bool(decision.is_public),
                "meeting_id": decision.meeting_id,
                "meeting_external_id": meeting.meeting_external_id,
                "document": {
                    "id": document.id,
                    "title": document.title_he,
                    "url": document.canonical_url,
                },
            }
            for link, decision, document, meeting in decision_rows
        ],
        "linked_artifacts": [
            {
                "artifact_id": artifact.artifact_id,
                "confidence": link.confidence,
                "source_mention_id": link.source_mention_id,
                "source_type": artifact.source_kind,
                "artifact_kind": artifact.artifact_kind,
                "header_path": _loads_json(artifact.header_path_json) or [],
                "citation": artifact.citation_label,
                "start_page": artifact.start_page,
                "end_page": artifact.end_page,
                "document": {
                    "id": document.id,
                    "title": document.title_he,
                    "url": document.canonical_url,
                },
                "snippet": str(artifact.body_text or artifact.retrieval_text or "")[:240],
            }
            for link, artifact, document in artifact_rows
        ],
        "mentions": [
            {
                "id": mention.id,
                "document_id": mention.document_id,
                "document_version_id": mention.document_version_id,
                "start_offset": mention.start_offset,
                "end_offset": mention.end_offset,
                "start_page": mention.start_page,
                "end_page": mention.end_page,
                "mention_text": mention.mention_text,
                "mention_confidence": mention.mention_confidence,
                "evidence_hash": mention.evidence_hash,
                "citation": (
                    f"p.{mention.start_page}"
                    if mention.start_page is not None and mention.start_page == mention.end_page
                    else (
                        f"pp.{mention.start_page}-{mention.end_page}"
                        if mention.start_page is not None and mention.end_page is not None
                        else None
                    )
                ),
                "document": {
                    "id": document.id,
                    "title": document.title_he,
                    "url": document.canonical_url,
                },
            }
            for mention, document in mention_rows
        ],
    }


@app.get("/topic/tree/cache")
def topic_tree_cache(*, request: Request, limit: int = 5000, format: str | None = None, db=Depends(get_db)) -> Any:
    payload = semantic_tree(request=request, depth=None, kind="topic", status="active", format="json", db=db)
    if limit and int(limit) > 0:
        payload["items"] = payload.get("items", [])[: int(limit)]
        payload["count"] = len(payload["items"])
    payload = {
        "status": "retired",
        "replacement_endpoint": "/semantic/tree",
        "adapter": "semantic_tree",
        **payload,
    }
    if _wants_semantic_tree_html(request=request, format=format):
        return HTMLResponse(_semantic_tree_html(payload, title="עץ נושאים", subtitle="/topic/tree/cache · adapter to /semantic/tree"))
    return payload


@app.get("/semantic/runs/{document_version_id}")
def semantic_runs(document_version_id: int, db=Depends(get_db)) -> dict:
    runs = db.execute(
        select(SemanticDocumentRun)
        .where(SemanticDocumentRun.document_version_id == document_version_id)
        .order_by(SemanticDocumentRun.started_at.desc(), SemanticDocumentRun.id.desc())
    ).scalars().all()
    if not runs:
        return {
            "document_version_id": document_version_id,
            "count": 0,
            "runs": [],
        }

    run_ids = [run.id for run in runs]
    reject_rows = db.execute(
        select(SemanticCandidateReject.semantic_document_run_id, SemanticCandidateReject.reason_code).where(
            SemanticCandidateReject.semantic_document_run_id.in_(run_ids)
        )
    ).all()
    reject_histogram_by_run: dict[int, dict[str, int]] = {}
    for run_id, reason_code in reject_rows:
        bucket = reject_histogram_by_run.setdefault(run_id, {})
        bucket[reason_code] = bucket.get(reason_code, 0) + 1

    runs_payload = []
    for run in runs:
        validation_payload = _loads_json(run.validation_report_json)
        validation_dict = validation_payload if isinstance(validation_payload, dict) else {}
        issues_payload_raw = validation_dict.get("issues")
        issues_payload: list = issues_payload_raw if isinstance(issues_payload_raw, list) else []

        canonical_payload = _loads_json(run.canonicalization_report_json)
        canonical_dict = canonical_payload if isinstance(canonical_payload, dict) else {}
        accepted_nodes_raw = canonical_dict.get("accepted_nodes")
        rejected_nodes_raw = canonical_dict.get("rejected_nodes")
        warnings_raw = canonical_dict.get("warnings")
        accepted_nodes: list = accepted_nodes_raw if isinstance(accepted_nodes_raw, list) else []
        rejected_nodes: list = rejected_nodes_raw if isinstance(rejected_nodes_raw, list) else []
        warnings: list = warnings_raw if isinstance(warnings_raw, list) else []

        runs_payload.append(
            {
                "run_id": run.id,
                "status": run.status,
                "api_call_count": run.api_call_count,
                "prompt_hash": run.prompt_hash,
                "model_provider": run.model_provider,
                "model_name": run.model_name,
                "request_tokens": run.request_tokens,
                "response_tokens": run.response_tokens,
                "error_code": run.error_code,
                "error_text": run.error_text,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "validation": {
                    "is_valid": bool(validation_dict.get("is_valid")),
                    "issue_count": len(issues_payload),
                    "issues": issues_payload,
                },
                "canonicalization": {
                    "accepted_nodes": len(accepted_nodes),
                    "rejected_nodes": len(rejected_nodes),
                    "warning_count": len(warnings),
                    "warnings": warnings,
                },
                "reject_reason_histogram": reject_histogram_by_run.get(run.id, {}),
            }
        )

    return {
        "document_version_id": document_version_id,
        "count": len(runs_payload),
        "runs": runs_payload,
    }


@app.get("/meeting/{meeting_id}")
def meeting_detail(meeting_id: int, db=Depends(get_db)) -> dict:
    meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=404, detail="meeting_not_found")

    site = db.execute(select(SourceSite).where(SourceSite.id == meeting.source_site_id)).scalar_one_or_none()
    decisions = db.execute(
        select(Decision)
        .where(Decision.meeting_id == meeting.id, Decision.is_public.is_(True))
        .order_by(Decision.id.asc())
    ).scalars().all()
    links = db.execute(
        select(MeetingDocumentLink, Document)
        .join(Document, Document.id == MeetingDocumentLink.document_id)
        .where(MeetingDocumentLink.meeting_id == meeting.id)
        .order_by(MeetingDocumentLink.is_primary.desc(), Document.id.asc())
    ).all()

    decisions_payload = []
    for decision in decisions:
        vote = db.execute(select(Vote).where(Vote.decision_id == decision.id)).scalar_one_or_none()
        citation_count = db.execute(
            select(DecisionCitation.id).where(DecisionCitation.decision_id == decision.id)
        ).scalars().all()
        decisions_payload.append(
            {
                "id": decision.id,
                "decision_number": decision.decision_number,
                "agenda_item": decision.agenda_item,
                "decision_text": decision.decision_text,
                "parser_confidence": decision.parser_confidence,
                "citation_count": len(citation_count),
                "source_type": "protocol",
                "vote": {
                    "for_count": vote.for_count,
                    "against_count": vote.against_count,
                    "abstain_count": vote.abstain_count,
                    "unanimous": vote.unanimous,
                    "is_uncertain": vote.is_uncertain,
                }
                if vote
                else None,
                "metadata": _fallback_metadata_from_json(decision.metadata_json),
                "request_context": _decision_request_context_payload(db=db, decision_id=decision.id),
            }
        )

    return {
        "meeting": {
            "id": meeting.id,
            "municipality": site.municipality_slug if site else None,
            "meeting_external_id": meeting.meeting_external_id,
            "title_he": meeting.title_he,
            "committee_name": meeting.committee_name,
            "meeting_kind": meeting.meeting_kind,
            "meeting_code": meeting.meeting_code,
            "meeting_date": meeting.meeting_date,
            "parse_confidence": meeting.parse_confidence,
            "summary": None,
        },
        "documents": [
            {
                "id": document.id,
                "title": document.title_he,
                "url": document.canonical_url,
                "doc_kind": document.doc_kind,
                "source_type": link.source_type,
                "provenance": link.provenance,
                "is_primary": bool(link.is_primary),
            }
            for link, document in links
        ],
        "decisions": decisions_payload,
    }


@app.get("/decision/{decision_id}")
def decision_detail(decision_id: int, db=Depends(get_db)) -> dict:
    payload = _decision_payload(decision_id, db)
    if payload is None:
        raise HTTPException(status_code=404, detail="decision_not_found")
    return payload


@app.get("/ui/decision/{decision_id}", response_class=HTMLResponse)
def decision_card_page(decision_id: int, db=Depends(get_db)) -> HTMLResponse:
    payload = _decision_payload(decision_id, db)
    if payload is None:
        raise HTTPException(status_code=404, detail="decision_not_found")

    decision = payload["decision"]
    metadata = decision["metadata"]
    votes = payload["votes"]
    citations = payload["citations"]
    linked_documents = payload["linked_documents"]

    fallback_badge = ""
    if metadata.get("fallback_used"):
        fallback_badge = "<span class='badge fallback'>Fallback Bytez</span>"

    vote_html = "<div class='muted'>לא זוהו נתוני הצבעה.</div>"
    if votes:
        vote = votes[0]
        if vote.get("unanimous"):
            vote_html = "<div class='vote'>אושר פה אחד</div>"
        elif vote.get("is_uncertain"):
            vote_html = f"<div class='vote uncertain'>זוהתה הצבעה לא ודאית: {html.escape(vote.get('raw_text') or '')}</div>"
        else:
            vote_html = (
                "<div class='vote'>"
                f"בעד: {vote.get('for_count')} | נגד: {vote.get('against_count')} | נמנע: {vote.get('abstain_count')}"
                "</div>"
            )

    citation_parts: list[str] = []
    for item in citations:
        anchor_label = item.get("anchor_label") or f"עמוד {item['page']}"
        citation_parts.append(
            "<li>"
            f"<a href='{html.escape(item['document']['url'])}#page={item['page']}' target='_blank' rel='noopener'>"
            f"{html.escape(anchor_label)}"
            "</a>"
            f" <span class='muted'>{html.escape(item['document']['title'])}</span>"
            "</li>"
        )
    citation_items = "".join(citation_parts)

    document_items = "".join(
        (
            "<li>"
            f"<a href='{html.escape(item['url'])}' target='_blank' rel='noopener'>{html.escape(item['title'])}</a>"
            f" <span class='muted'>[{html.escape(item['source_type'])} | {html.escape(item['provenance'])}]</span>"
            "</li>"
        )
        for item in linked_documents
    )
    default_muni_json = json.dumps(decision.get("municipality") or "", ensure_ascii=False)
    default_topic_json = json.dumps(decision.get("agenda_item") or "", ensure_ascii=False)

    html_page = f"""
<!doctype html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>כרטיס החלטה {decision['id']}</title>
  <style>
    :root {{
      --bg-a: #f3f8f6;
      --bg-b: #fdfaf2;
      --ink: #15343b;
      --muted: #53707a;
      --panel: rgba(255,255,255,0.82);
      --line: #d7e6df;
      --accent: #0f7b80;
      --warn: #b65b1d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Heebo", "Rubik", sans-serif;
      color: var(--ink);
      background: radial-gradient(1200px 500px at 100% -10%, #d8ece7 0%, transparent 70%),
                  radial-gradient(1000px 400px at -10% 110%, #f9ead0 0%, transparent 70%),
                  linear-gradient(130deg, var(--bg-a), var(--bg-b));
      min-height: 100vh;
      padding: 22px;
    }}
    .card {{
      max-width: 900px;
      margin: 0 auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 24px;
      box-shadow: 0 14px 36px rgba(21, 52, 59, 0.11);
      backdrop-filter: blur(2px);
    }}
    h1 {{ margin: 0 0 10px; font-size: 1.7rem; line-height: 1.35; }}
    h2 {{ margin: 18px 0 8px; font-size: 1.05rem; }}
    p {{ margin: 0; line-height: 1.8; }}
    ul {{ margin: 8px 0 0; padding-inline-start: 20px; }}
    li {{ margin: 6px 0; line-height: 1.5; }}
    .top {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }}
    .badge {{
      display: inline-block;
      border-radius: 999px;
      font-size: 0.82rem;
      padding: 5px 10px;
      border: 1px solid var(--line);
      background: #e9f2ee;
    }}
    .badge.fallback {{ border-color: #f1c8a2; background: #fff2e5; color: #8a430f; }}
    .muted {{ color: var(--muted); font-size: 0.9rem; }}
    .vote {{ margin-top: 8px; font-weight: 600; color: var(--accent); }}
    .vote.uncertain {{ color: var(--warn); }}
    .grid {{ display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); margin-top: 14px; }}
    .panel {{ border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; background: #fff; }}
    .ask-panel {{ margin-top: 14px; }}
    .ask-form {{ display: grid; gap: 8px; margin-top: 8px; }}
    .ask-form label {{ font-weight: 600; font-size: 0.92rem; }}
    .ask-form textarea {{
      width: 100%;
      min-height: 104px;
      border: 1px solid #bfd3cb;
      border-radius: 10px;
      padding: 10px;
      font-family: inherit;
      line-height: 1.6;
      resize: vertical;
    }}
    .ask-controls {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
    .ask-controls input {{
      width: 84px;
      border: 1px solid #bfd3cb;
      border-radius: 8px;
      padding: 6px 8px;
      font-family: inherit;
    }}
    .ask-controls button {{
      border: 1px solid #0e6d72;
      background: #0f7b80;
      color: #fff;
      border-radius: 999px;
      padding: 8px 16px;
      font-family: inherit;
      font-weight: 600;
      cursor: pointer;
    }}
    .ask-controls button:hover {{ background: #0c666b; }}
    .ask-output {{ margin-top: 10px; border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; }}
    .ask-output.refusal {{ border-color: #e6d1be; background: #fff8f2; }}
    .ask-output.warning {{ border-color: #f1d39d; background: #fff8e7; }}
    .ask-output h3 {{ margin: 0 0 8px; font-size: 0.98rem; }}
    #ask-answer-text {{ direction: rtl; text-align: right; white-space: pre-line; }}
    .answer-sections {{ margin-top: 10px; display: grid; gap: 10px; }}
    .protocol-group {{ border: 1px solid #d7e5ee; border-radius: 11px; padding: 10px; background: #fdfefe; }}
    .protocol-group > h4 {{ margin: 0 0 8px; font-size: 0.96rem; color: #23424f; }}
    .topic-root, .topic-child {{ border: 1px solid #e0eaf0; border-radius: 9px; background: #fff; }}
    .topic-root + .topic-root {{ margin-top: 8px; }}
    .topic-root > summary, .topic-child > summary {{
      cursor: pointer;
      list-style: none;
      padding: 8px 10px;
      font-weight: 600;
      color: #2f4d58;
      border-bottom: 1px solid #ecf2f7;
    }}
    .topic-root > summary::-webkit-details-marker, .topic-child > summary::-webkit-details-marker {{ display: none; }}
    .topic-root > summary::before, .topic-child > summary::before {{ content: "▾ "; color: #668390; }}
    .topic-root:not([open]) > summary::before, .topic-child:not([open]) > summary::before {{ content: "▸ "; }}
    .topic-root-content {{ padding: 8px; display: grid; gap: 8px; }}
    .topic-child-content {{ padding: 8px 10px; display: grid; gap: 8px; }}
    .answer-section {{ border: 1px solid #dde7ee; border-radius: 10px; padding: 10px; background: #fbfdff; }}
    .answer-section h4 {{ margin: 0 0 4px; font-size: 0.98rem; }}
    .answer-section .topic {{ margin: 0 0 6px; color: #4c5963; font-size: 0.88rem; }}
    .answer-section .text {{ margin: 0; direction: rtl; text-align: right; }}
    .decision-sources {{ margin: 0; padding-inline-start: 18px; }}
    .decision-sources li {{ margin: 4px 0; }}
    pre {{
      margin: 0;
      border: 1px solid #d5e4ea;
      border-radius: 10px;
      padding: 10px;
      background: #f8fbfd;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 0.82rem;
      line-height: 1.45;
      direction: ltr;
      text-align: left;
      max-height: 260px;
      overflow: auto;
    }}
    .hidden {{ display: none; }}
    a {{ color: #00696f; text-decoration: none; border-bottom: 1px dotted #86b7bb; }}
    a:hover {{ border-bottom-style: solid; }}
    @media (max-width: 640px) {{
      body {{ padding: 14px; }}
      .card {{ padding: 16px; border-radius: 14px; }}
    }}
  </style>
</head>
<body>
  <article class="card">
    <div class="top">
      <span class="badge">החלטה #{decision['id']}</span>
      <span class="badge">פגישת מקור: {html.escape(str(decision.get('meeting_external_id') or '-'))}</span>
      {fallback_badge}
    </div>
    <h1>{html.escape(decision.get('decision_text') or '')}</h1>
    <p class="muted">סעיף: {html.escape(decision.get('agenda_item') or 'סעיף לא מזוהה')}</p>
    <h2>תוצאת הצבעה</h2>
    {vote_html}
    <section class="grid">
      <section class="panel">
        <h2>ציטוטים מאומתים</h2>
        <ul>{citation_items or '<li class="muted">לא נמצאו ציטוטים.</li>'}</ul>
      </section>
      <section class="panel">
        <h2>מסמכים קשורים</h2>
        <ul>{document_items or '<li class="muted">לא נמצאו מסמכים קשורים.</li>'}</ul>
      </section>
    </section>
    <section class="panel ask-panel" id="ask-panel">
      <h2>שאלו על הישיבה (ציטוטים בלבד)</h2>
      <p class="muted">השאלה נשלחת אל <code>POST /ask</code> עם הקשר הישיבה והסעיף הנוכחי.</p>
      <form id="ask-form" class="ask-form">
        <label for="ask-question">שאלה</label>
        <textarea id="ask-question" name="question" required placeholder="לדוגמה: אילו צעדים אושרו ומה הראיות לכך?"></textarea>
        <div class="ask-controls">
          <label for="ask-top-k" class="muted">top_k</label>
          <input id="ask-top-k" name="top_k" type="number" min="1" max="50" value="8" />
          <label class="muted"><input id="ask-debug-mode" type="checkbox" checked /> מצב debug (ספי חישוב)</label>
          <button type="submit">שאל</button>
        </div>
      </form>
      <p id="ask-status" class="muted" aria-live="polite"></p>
      <section id="ask-answer-panel" class="ask-output hidden">
        <h3>תשובה</h3>
        <p id="ask-answer-text"></p>
        <div id="ask-answer-sections" class="answer-sections"></div>
        <ul id="ask-limitations"></ul>
      </section>
      <section id="ask-provider-warning-panel" class="ask-output warning hidden">
        <h3>אזהרת ספק</h3>
        <p id="ask-provider-warning-text"></p>
      </section>
      <section id="ask-refusal-panel" class="ask-output refusal hidden">
        <h3>מצב סירוב</h3>
        <p id="ask-refusal-text"></p>
      </section>
      <section id="ask-citations-panel" class="ask-output hidden">
        <h3>ציטוטים תומכים</h3>
        <ul id="ask-citations-list"></ul>
      </section>
      <section id="ask-thresholds-panel" class="ask-output hidden">
        <h3>PDF-first debug</h3>
        <pre id="ask-thresholds-json"></pre>
      </section>
    </section>
    <p class="muted" style="margin-top: 12px;">סטטוס אימות fallback: {html.escape(str(metadata.get('fallback_validation_status')))}</p>
  </article>
  <script>
    (() => {{
      const form = document.getElementById("ask-form");
      if (!form) {{
        return;
      }}

      const questionInput = document.getElementById("ask-question");
      const topKInput = document.getElementById("ask-top-k");
      const debugModeInput = document.getElementById("ask-debug-mode");
      const statusNode = document.getElementById("ask-status");
      const answerPanel = document.getElementById("ask-answer-panel");
      const answerText = document.getElementById("ask-answer-text");
      const answerSectionsNode = document.getElementById("ask-answer-sections");
      const limitationsList = document.getElementById("ask-limitations");
      const providerWarningPanel = document.getElementById("ask-provider-warning-panel");
      const providerWarningText = document.getElementById("ask-provider-warning-text");
      const refusalPanel = document.getElementById("ask-refusal-panel");
      const refusalText = document.getElementById("ask-refusal-text");
      const citationsPanel = document.getElementById("ask-citations-panel");
      const citationsList = document.getElementById("ask-citations-list");
      const thresholdsPanel = document.getElementById("ask-thresholds-panel");
      const thresholdsJson = document.getElementById("ask-thresholds-json");

      const defaultMuni = {default_muni_json};
      const defaultTopic = {default_topic_json};
      const hide = (node) => {{
        if (node) {{
          node.classList.add("hidden");
        }}
      }};
      const show = (node) => {{
        if (node) {{
          node.classList.remove("hidden");
        }}
      }};

      const clearList = (listNode) => {{
        if (listNode) {{
          listNode.innerHTML = "";
        }}
      }};

      const appendListItem = (listNode, textValue) => {{
        if (!listNode || !textValue) {{
          return;
        }}
        const item = document.createElement("li");
        item.textContent = textValue;
        listNode.appendChild(item);
      }};

      const splitTopicPath = (topicValue, fallbackText) => {{
        const rawTopic = String(topicValue || "").trim();
        const parts = rawTopic.split(">").map((part) => part.trim()).filter(Boolean);
        if (parts.length >= 2) {{
          return {{ root: parts[0], child: parts.slice(1).join(" > ") }};
        }}
        const fallbackWords = String(fallbackText || "").trim().split(/\\s+/).filter(Boolean);
        return {{
          root: parts[0] || "ללא תיוג סמנטי",
          child: fallbackWords.slice(0, 4).join(" ") || "החלטה",
        }};
      }};

      form.addEventListener("submit", async (event) => {{
        event.preventDefault();
        const question = (questionInput.value || "").trim();
        if (!question) {{
          statusNode.textContent = "יש להזין שאלה לפני השליחה.";
          return;
        }}

        const topKRaw = parseInt(topKInput.value || "8", 10);
        const topK = Number.isFinite(topKRaw) ? Math.max(1, Math.min(50, topKRaw)) : 8;
        const payload = {{
          question: question,
          top_k: topK,
          semantic_mode: "off",
          debug_mode: Boolean(debugModeInput && debugModeInput.checked),
        }};
        if (defaultMuni) {{
          payload.muni = defaultMuni;
        }}
        if (defaultTopic) {{
          payload.topic = defaultTopic;
        }}

        statusNode.textContent = "שולח שאלה...";
        hide(answerPanel);
        hide(providerWarningPanel);
        hide(refusalPanel);
        hide(citationsPanel);
        hide(thresholdsPanel);
        clearList(limitationsList);
        clearList(citationsList);
        if (answerSectionsNode) {{
          answerSectionsNode.innerHTML = "";
        }}
        if (thresholdsJson) {{
          thresholdsJson.textContent = "";
        }}
        if (providerWarningText) {{
          providerWarningText.textContent = "";
        }}

        try {{
          const response = await fetch("/ask", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify(payload),
          }});
          if (!response.ok) {{
            const errorBody = await response.text().catch(() => "");
            const errorSuffix = errorBody ? `: ${{errorBody.slice(0, 220)}}` : "";
            throw new Error(`HTTP ${{response.status}}${{errorSuffix}}`);
          }}

          const data = await response.json();
          const providerWarning = data.provider_warning && typeof data.provider_warning === "object" ? data.provider_warning : null;
          const debugPayload = data.debug && typeof data.debug === "object" ? data.debug : null;
          if (debugPayload && debugPayload.thresholds && thresholdsJson) {{
            thresholdsJson.textContent = JSON.stringify(debugPayload.thresholds, null, 2);
            show(thresholdsPanel);
          }} else {{
            hide(thresholdsPanel);
          }}
          if (providerWarning && providerWarningText) {{
            const warningBits = [providerWarning.message_he || "ספק התשובה אינו זמין."];
            if (providerWarning.provider || providerWarning.model) {{
              warningBits.push(`(${{providerWarning.provider || "-"}} / ${{providerWarning.model || "-"}})`);
            }}
            providerWarningText.textContent = warningBits.join(" ");
            show(providerWarningPanel);
          }} else {{
            hide(providerWarningPanel);
          }}
          if (data.status === "answer") {{
            const sections = Array.isArray(data.answer_sections) ? data.answer_sections : [];
            const citationRows = Array.isArray(data.citations) ? data.citations : [];
            if (sections.length > 0) {{
              answerText.textContent = "";
              hide(answerText);
            }} else {{
              answerText.textContent = data.answer || "";
              show(answerText);
            }}

            if (answerSectionsNode) {{
              answerSectionsNode.innerHTML = "";
              const citationByChunk = new Map();
              for (const citation of citationRows) {{
                if (!citation || typeof citation !== "object") {{
                  continue;
                }}
                const chunkId = typeof citation.chunk_id === "string" ? citation.chunk_id : "";
                if (!chunkId || citationByChunk.has(chunkId)) {{
                  continue;
                }}
                citationByChunk.set(chunkId, citation);
              }}

              const protocolOrder = [];
              const protocolBuckets = new Map();
              for (const section of sections) {{
                if (!section || typeof section !== "object") {{
                  continue;
                }}
                const protocolTitle = typeof section.protocol_title === "string" ? section.protocol_title : "פרוטוקול";
                const topic = typeof section.topic_name === "string" ? section.topic_name : "נושא כללי";
                const text = typeof section.text === "string" ? section.text : "";
                if (!text.trim()) {{
                  continue;
                }}
                const path = splitTopicPath(topic, text);
                const chunkIds = Array.isArray(section.chunk_ids) ? section.chunk_ids : [];

                if (!protocolBuckets.has(protocolTitle)) {{
                  protocolBuckets.set(protocolTitle, []);
                  protocolOrder.push(protocolTitle);
                }}
                protocolBuckets.get(protocolTitle).push({{
                  root: path.root,
                  child: path.child,
                  text,
                  chunkIds,
                }});
              }}

              for (const protocolTitle of protocolOrder) {{
                const protocolNode = document.createElement("section");
                protocolNode.className = "protocol-group";

                const protocolHeader = document.createElement("h4");
                protocolHeader.textContent = protocolTitle;
                protocolNode.appendChild(protocolHeader);

                const rows = protocolBuckets.get(protocolTitle) || [];
                const rootOrder = [];
                const rootBuckets = new Map();
                for (const row of rows) {{
                  if (!rootBuckets.has(row.root)) {{
                    rootBuckets.set(row.root, []);
                    rootOrder.push(row.root);
                  }}
                  rootBuckets.get(row.root).push(row);
                }}

                for (const rootLabel of rootOrder) {{
                  const rootDetails = document.createElement("details");
                  rootDetails.className = "topic-root";
                  rootDetails.open = true;

                  const rootSummary = document.createElement("summary");
                  rootSummary.textContent = rootLabel;
                  rootDetails.appendChild(rootSummary);

                  const rootContent = document.createElement("div");
                  rootContent.className = "topic-root-content";
                  for (const row of rootBuckets.get(rootLabel) || []) {{
                    const childDetails = document.createElement("details");
                    childDetails.className = "topic-child";
                    childDetails.open = true;

                    const childSummary = document.createElement("summary");
                    childSummary.textContent = row.child;
                    childDetails.appendChild(childSummary);

                    const childContent = document.createElement("div");
                    childContent.className = "topic-child-content";

                    const textNode = document.createElement("p");
                    textNode.className = "text";
                    textNode.textContent = row.text;
                    childContent.appendChild(textNode);

                    const sourceRows = [];
                    for (const chunkId of row.chunkIds) {{
                      if (typeof chunkId !== "string") {{
                        continue;
                      }}
                      const citation = citationByChunk.get(chunkId);
                      if (citation) {{
                        sourceRows.push(citation);
                      }}
                    }}
                    if (sourceRows.length > 0) {{
                      const sourceTitle = document.createElement("p");
                      sourceTitle.className = "topic";
                      sourceTitle.textContent = "מקורות:";
                      childContent.appendChild(sourceTitle);

                      const sourceList = document.createElement("ul");
                      sourceList.className = "decision-sources";
                      for (const citation of sourceRows) {{
                        const documentPayload = citation.document && typeof citation.document === "object" ? citation.document : {{}};
                        const page = Number.isFinite(Number(citation.start_page)) ? Number(citation.start_page) : null;
                        const hrefBase = documentPayload.url || "#";
                        const sourceItem = document.createElement("li");
                        const sourceLink = document.createElement("a");
                        sourceLink.href = page ? `${{hrefBase}}#page=${{page}}` : hrefBase;
                        sourceLink.target = "_blank";
                        sourceLink.rel = "noopener";
                        sourceLink.textContent = documentPayload.title || "מסמך";
                        sourceItem.appendChild(sourceLink);

                        const sourceMeta = document.createElement("span");
                        sourceMeta.className = "muted";
                        const sourceType = citation.source_type || "source";
                        const citationLabel = citation.citation || (page ? `עמוד ${{page}}` : "מקור");
                        sourceMeta.textContent = ` [${{sourceType}} | ${{citationLabel}}]`;
                        sourceItem.appendChild(sourceMeta);
                        sourceList.appendChild(sourceItem);
                      }}
                      childContent.appendChild(sourceList);
                    }}

                    childDetails.appendChild(childContent);
                    rootContent.appendChild(childDetails);
                  }}
                  rootDetails.appendChild(rootContent);
                  protocolNode.appendChild(rootDetails);
                }}

                answerSectionsNode.appendChild(protocolNode);
              }}
            }}
            const limitations = Array.isArray(data.limitations) ? data.limitations : [];
            if (limitations.length === 0) {{
              appendListItem(limitationsList, "לא צוינו מגבלות נוספות.");
            }} else {{
              for (const limitation of limitations) {{
                appendListItem(limitationsList, limitation);
              }}
            }}

            if (citationRows.length === 0) {{
              appendListItem(citationsList, "לא הוחזרו ציטוטים.");
            }} else {{
              for (const citation of citationRows) {{
                const documentPayload = citation.document && typeof citation.document === "object" ? citation.document : {{}};
                const page = Number.isFinite(Number(citation.start_page)) ? Number(citation.start_page) : null;
                const hrefBase = documentPayload.url || "#";
                const link = document.createElement("a");
                link.href = page ? `${{hrefBase}}#page=${{page}}` : hrefBase;
                link.target = "_blank";
                link.rel = "noopener";
                link.textContent = documentPayload.title || "מסמך";

                const meta = document.createElement("span");
                meta.className = "muted";
                const sourceType = citation.source_type || "source";
                const citationLabel = citation.citation || (page ? `עמוד ${{page}}` : "מקור");
                meta.textContent = ` [${{sourceType}} | ${{citationLabel}}]`;

                const item = document.createElement("li");
                item.appendChild(link);
                item.appendChild(meta);
                citationsList.appendChild(item);
              }}
            }}

            show(answerPanel);
            show(citationsPanel);
            hide(refusalPanel);
            statusNode.textContent = providerWarning && providerWarning.mock_mode === true
              ? "ספק התשובה אינו זמין; מוצגת טיוטת תשובה מבוססת שליפה בלבד."
              : "התקבלה תשובה מבוססת ציטוטים.";
            return;
          }}

          const refusalPayload = data.refusal && typeof data.refusal === "object" ? data.refusal : {{}};
          const refusalMessage = refusalPayload.message_he || "אין מספיק ראיות כדי להשיב.";
          const missingTypes = Array.isArray(refusalPayload.missing_source_types)
            ? refusalPayload.missing_source_types.filter((value) => typeof value === "string" && value)
            : [];
          const missingHint = missingTypes.length ? ` חסרים: ${{missingTypes.join(", ")}}.` : "";
          refusalText.textContent = `${{refusalMessage}}${{missingHint}}`;

          show(refusalPanel);
          hide(answerPanel);
          hide(citationsPanel);
          statusNode.textContent = providerWarning
            ? "ספק התשובה אינו זמין, ולא ניתן היה להרכיב גם טיוטת תשובה אמינה."
            : "המערכת סירבה להשיב בגלל חוסר ראיות מספק.";
        }} catch (_err) {{
          console.error("ask_inline_panel_request_failed", _err);
          const errorMessage = _err instanceof Error ? _err.message : String(_err || "unknown_error");
          hide(answerPanel);
          hide(providerWarningPanel);
          hide(refusalPanel);
          hide(citationsPanel);
          hide(thresholdsPanel);
          statusNode.textContent = `שליחת השאלה נכשלה: ${{errorMessage}}`;
        }}
      }});
    }})();
  </script>
</body>
</html>
"""
    return HTMLResponse(html_page)


@app.get("/runs/{run_id}")
def run_status(run_id: int, db=Depends(get_db)) -> dict:
    run = db.execute(select(PipelineRun).where(PipelineRun.id == run_id)).scalar_one_or_none()
    if not run:
        return {"error": "not_found", "run_id": run_id}
    steps = db.execute(select(PipelineRunStep).where(PipelineRunStep.run_id == run_id)).scalars().all()
    return {
        "run_id": run.id,
        "status": run.status,
        "run_type": run.run_type,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "steps": [
            {"id": step.id, "step": step.step_name, "status": step.status, "item_ref": step.item_ref, "detail": step.detail}
            for step in steps
        ],
    }
