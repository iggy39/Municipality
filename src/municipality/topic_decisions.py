from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.chunking import normalize_for_search
from municipality.models import (
    ArtifactSemanticLink,
    Document,
    RetrievalArtifact,
    SemanticNode,
    SourceSite,
    TopicDecision,
    TopicDecisionQualityReport,
    TopicDecisionRun,
)
from municipality.pdf_first_v4_topic_tree import current_tree_from_db


DEFAULT_DICTA_MODEL = "dicta-il/DictaLM-3.0-24B-Thinking:bf16"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
PROVENANCE = "topic_decision_research_v1"

DECISION_KIND_LABELS = {
    "DISCUSSION": "דיון",
    "APPROVAL": "אישור",
    "REJECTION": "דחייה",
    "DEFERRAL": "דחייה למועד אחר",
    "REFERRAL": "העברה לטיפול",
    "RECOMMENDATION": "המלצה",
    "BUDGET_ALLOCATION": "הקצאת תקציב",
    "STATUS_UPDATE": "עדכון סטטוס",
    "REQUEST_FOR_INFO": "בקשת מידע",
    "PUBLICATION": "פרסום",
    "OBJECTION_HEARING": "שמיעת התנגדויות",
    "PROCEDURAL_ACTION": "פעולה תהליכית",
    "UNKNOWN": "לא ידוע",
}

OUTCOME_STATUS_LABELS = {
    "APPROVED": "אושר",
    "APPROVED_WITH_CONDITIONS": "אושר בתנאים",
    "REJECTED": "נדחה",
    "DEFERRED": "נדחה למועד אחר",
    "REFERRED": "הועבר לטיפול",
    "NOTED": "נרשם",
    "PENDING": "בהמשך טיפול",
    "NO_FORMAL_OUTCOME": "ללא החלטה פורמלית",
    "PARTIAL": "חלקי",
    "UNKNOWN": "לא ידוע",
}

LEGAL_EFFECT_LABELS = {
    "BINDING": "מחייב",
    "ADVISORY": "מייעץ",
    "INFORMATIONAL": "מידעי",
    "PROCEDURAL": "תהליכי",
    "UNKNOWN": "לא ידוע",
}

TIME_ANCHOR_KIND_LABELS = {
    "meeting_date": "תאריך הדיון",
    "decision_date": "תאריך החלטה",
    "publication_date": "תאריך פרסום",
    "effective_date": "תחילת תוקף",
    "deadline": "מועד אחרון",
    "implementation_start": "תחילת ביצוע",
    "implementation_end": "סיום ביצוע",
    "review_date": "מועד בדיקה",
    "budget_year": "שנת תקציב",
    "unknown": "תאריך לא ידוע",
}

DECISION_PRESENCE_LABELS = {
    "NO_DECISION": "אין החלטה בטקסט",
    "REQUEST_ONLY": "בקשה או הצעה בלבד",
    "DECISION_PRESENT": "החלטה או פעולה קיימת בטקסט",
    "DECISION_REFERENCE_ONLY": "אזכור החלטה ממקור אחר בלבד",
    "INSUFFICIENT_CONTEXT": "נדרש הקשר סמוך",
}

DECISION_EVENT_TYPE_LABELS = {
    "PROPOSAL": "הצעה או בקשה שהועלתה",
    "ACCEPTANCE": "קבלה או אישור",
    "REJECTION": "דחייה או אי אישור",
    "DISCUSSION": "דיון ללא תוצאה פורמלית",
    "IMMEDIATE_APPROVAL": "אישור מיידי באותה ישיבה",
    "COMMITTEE_CREATION": "הקמת ועדה או צוות",
    "REFERRAL": "העברה לוועדה, גורם מקצועי או טיפול",
    "POSTPONEMENT": "דחייה למועד אחר",
    "REQUEST_FOR_INFO": "בקשה להשלמת מידע",
    "BUDGET_ALLOCATION": "הקצאת תקציב",
    "STATUS_UPDATE": "עדכון סטטוס בלבד",
    "PROCEDURAL_ACTION": "פעולה מנהלית או פרוצדורלית",
    "UNKNOWN": "לא ידוע",
}

APPROVAL_VERB_CUES = (
    "מאשרים",
    "מאשר",
    "מאשרת",
    "אשרו",
    "אישרו",
    "אשרה",
    "אישרה",
    "אושר",
    "אושרה",
    "הוחלט לאשר",
    "ההצעה התקבלה",
    "התקבלה",
    "התקבל",
    "פה אחד",
    "ברוב קולות",
)

DECISION_ACTION_CUES = (
    *APPROVAL_VERB_CUES,
    "הוחלט",
    "מחליטים",
    "נדחתה",
    "נדחה",
    "לא אושר",
    "לא אושרה",
    "הועברה",
    "הועבר",
    "עוברת לדיון",
    "יועבר",
    "יקבע מפגש",
    "יש לתאם",
    "הוועדה ממליצה",
    "הועדה ממליצה",
    "תוקם ועדה",
    "מקימים ועדה",
    "יוקם צוות",
    "יוקצה",
    "הקצאת",
    "מסמיכים",
    "נרשם",
)

REQUEST_ONLY_CUES = (
    "אבקש",
    "אודה לאישור",
    "אודה לאשר",
    "מבקש לאשר",
    "מבקשת לאשר",
    "אנו מבקשים לאשר",
    "הנני מבקש",
    "הצעה לסדר",
    "שאילתה",
    "שאילתא",
    "לאור האמור אבקש",
)


@dataclass(slots=True)
class TopicDecisionResearchConfig:
    municipality_slug: str = "ashdod"
    model_name: str = DEFAULT_DICTA_MODEL
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    timeout_seconds: float = 180.0
    max_text_chars: int = 3500
    limit: int | None = None
    write: bool = False
    output_dir: Path | None = None


@dataclass(slots=True)
class TopicDecisionArtifact:
    artifact_id: str
    semantic_node_id: int
    topic_label_he: str
    root_topic_id: str | None
    root_label_he: str | None
    child_topic_id: str | None
    child_label_he: str | None
    source_kind: str
    source_document_id: int
    source_document_version_id: int
    source_ordinal: int
    source_title: str
    source_url: str | None
    artifact_kind: str
    start_page: int | None
    end_page: int | None
    header_path: list[str]
    real_text: str
    retrieval_text: str
    topic_confidence: float
    existing_decision_candidate_id: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
    decision_context_text: str = ""
    neighbor_contexts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ExtractedTopicDecision:
    artifact: TopicDecisionArtifact
    decision_index: int
    decision_payload: dict[str, Any]
    validation_status: str
    failure_reasons: list[str]
    judge_payload: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    resident_evidence_links: list[dict[str, str]]


@dataclass(slots=True)
class TopicDecisionQualityRow:
    artifact_id: str
    semantic_node_id: int
    topic: str
    real_text: str
    decision_by_dicta: str
    decision_ground_truth: str
    reason_for_failure: str
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TopicDecisionResearchResult:
    run_id: int | None
    topic_tree: dict[str, Any]
    artifacts: list[TopicDecisionArtifact]
    extracted_decisions: list[ExtractedTopicDecision]
    quality_rows: list[TopicDecisionQualityRow]
    elapsed_seconds: float
    output_paths: dict[str, str] = field(default_factory=dict)

    @property
    def accepted_decision_count(self) -> int:
        return sum(1 for item in self.extracted_decisions if item.validation_status == "accepted")

    @property
    def failed_count(self) -> int:
        return sum(1 for row in self.quality_rows if row.status in {"failed", "model_error", "partial"})


class TopicDecisionClient(Protocol):
    def extract(self, *, artifact: TopicDecisionArtifact, config: TopicDecisionResearchConfig) -> dict[str, Any]:
        raise NotImplementedError


class OllamaTopicDecisionClient:
    def extract(self, *, artifact: TopicDecisionArtifact, config: TopicDecisionResearchConfig) -> dict[str, Any]:
        request_payload = topic_decision_prompt_payload(artifact=artifact, max_text_chars=config.max_text_chars)
        body = {
            "model": config.model_name,
            "stream": False,
            "think": False,
            "format": "json",
            "messages": [
                {
                    "role": "system",
                    "content": "/no_think\nYou are a Hebrew municipal decision extraction judge. Return strict JSON only.",
                },
                {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False)},
            ],
            "options": {"temperature": 0.0, "num_predict": 2048},
            "keep_alive": "30m",
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(config.timeout_seconds, connect=10.0, read=config.timeout_seconds, write=30.0, pool=10.0)) as client:
                response = client.post(f"{config.ollama_base_url.rstrip('/')}/api/chat", json=body)
                response.raise_for_status()
                raw_payload = response.json()
        except Exception as exc:  # noqa: BLE001
            return {"error_code": "MODEL_REQUEST_FAILED", "error_text": f"{exc.__class__.__name__}:{exc}"}

        content = str(((raw_payload.get("message") or {}).get("content")) or "")
        parsed = parse_json_object(content)
        if not isinstance(parsed, dict):
            return {"error_code": "MODEL_INVALID_JSON", "error_text": content[:500], "raw_payload": raw_payload}
        parsed["raw_payload"] = raw_payload
        return parsed


class MockTopicDecisionClient:
    """Fast local client for verification; production runs should use OllamaTopicDecisionClient."""

    def extract(self, *, artifact: TopicDecisionArtifact, config: TopicDecisionResearchConfig) -> dict[str, Any]:
        text = compact_text(artifact.real_text)
        normalized = normalize_for_search(text)
        has_action = text_has_any(normalized, DECISION_ACTION_CUES)
        if has_action:
            quote = text[: min(len(text), 260)]
            is_approval = text_has_any(normalized, APPROVAL_VERB_CUES)
            return {
                "decisions": [
                    {
                        "title_he": artifact.topic_label_he,
                        "decision_text_he": quote,
                        "summary_he": quote[:180],
                        "decision_presence": {"code": "DECISION_PRESENT", "label_he": DECISION_PRESENCE_LABELS["DECISION_PRESENT"], "confidence_label": "בינונית"},
                        "decision_event_type": {"code": "ACCEPTANCE" if is_approval else "PROCEDURAL_ACTION", "label_he": DECISION_EVENT_TYPE_LABELS["ACCEPTANCE"] if is_approval else DECISION_EVENT_TYPE_LABELS["PROCEDURAL_ACTION"], "confidence_label": "בינונית"},
                        "decision_kind": {"code": "APPROVAL" if is_approval else "PROCEDURAL_ACTION", "label_he": "אישור" if is_approval else "פעולה תהליכית", "confidence_label": "בינונית"},
                        "outcome_status": {"code": "APPROVED" if is_approval else "UNKNOWN", "label_he": "אושר" if is_approval else "לא ידוע", "confidence_label": "בינונית"},
                        "legal_effect": {"code": "BINDING" if is_approval else "UNKNOWN", "label_he": "מחייב" if is_approval else "לא ידוע", "confidence_label": "בינונית"},
                        "primary_time": {"kind": "unknown", "start": None, "end": None, "precision": "unknown", "label_he": "תאריך לא ידוע", "confidence_label": "נמוכה"},
                        "time_anchors": [],
                        "source_quote_he": quote,
                        "confidence": 0.62,
                        "new_decision_label_candidate_he": None,
                        "limitations": ["פלט בדיקה מקומי; לא הופק על ידי Dicta"],
                    }
                ],
                "no_decision_reason_he": None,
                "rationale_he": "mock",
            }
        return {"decisions": [], "no_decision_reason_he": "לא נמצאה החלטה מפורשת בטקסט", "rationale_he": "mock"}


def topic_decision_prompt_payload(*, artifact: TopicDecisionArtifact, max_text_chars: int) -> dict[str, Any]:
    return {
        "task": "extract_topic_decision_from_accepted_topic_artifact",
        "requirements": [
            "The topic was already accepted by the topic pipeline. Do not reclassify the topic and do not modify the topic tree.",
            "Use raw_evidence_text only for source_quote_he. The quote must be exact text from raw_evidence_text, not from neighbor_contexts.",
            "Use decision_context_text and neighbor_contexts only to understand adjacent-row context and the object/outcome relationship.",
            "Return zero decisions when the primary artifact is only a request, agenda item, background, question, attachment, proposal, discussion fragment, or source note without a grounded municipal action/outcome.",
            "A decision/action may be formal or procedural, but it must be directly supported by source_quote_he copied from raw_evidence_text.",
            "Do not infer final approval from a request for approval unless the source text says the council/body approved or decided.",
            "Classify decision_presence first. If it is not DECISION_PRESENT, return no decisions and explain no_decision_reason_he.",
            "Use decision_event_type as a closed event label. If a useful new label is needed, put it only in new_decision_label_candidate_he.",
            "Use only approved codelist codes supplied below. If uncertain, use UNKNOWN or NO_FORMAL_OUTCOME and explain in limitations.",
            "Return strict JSON only with key decisions, no markdown.",
        ],
        "response_schema": {
            "decisions": [
                {
                    "title_he": "string",
                    "decision_text_he": "string",
                    "summary_he": "string",
                    "decision_presence": {"code": "approved code", "label_he": "string", "raw_text": "string|null", "confidence_label": "גבוהה|בינונית|נמוכה"},
                    "decision_event_type": {"code": "approved code", "label_he": "string", "raw_text": "string|null", "confidence_label": "גבוהה|בינונית|נמוכה"},
                    "decision_kind": {"code": "approved code", "label_he": "string", "raw_text": "string|null", "confidence_label": "גבוהה|בינונית|נמוכה"},
                    "outcome_status": {"code": "approved code", "label_he": "string", "raw_text": "string|null", "confidence_label": "גבוהה|בינונית|נמוכה"},
                    "legal_effect": {"code": "approved code", "label_he": "string", "confidence_label": "גבוהה|בינונית|נמוכה"},
                    "primary_time": {"kind": "approved time kind", "start": "YYYY-MM-DD|null", "end": "YYYY-MM-DD|null", "precision": "day|month|year|unknown", "label_he": "string", "confidence_label": "גבוהה|בינונית|נמוכה"},
                    "time_anchors": [],
                    "source_quote_he": "exact quote copied from raw_evidence_text",
                    "confidence": "number 0..1",
                    "new_decision_label_candidate_he": "string|null",
                    "limitations": ["string"],
                }
            ],
            "no_decision_reason_he": "string|null",
            "rationale_he": "string",
        },
        "approved_codelists": {
            "decision_presence": sorted(DECISION_PRESENCE_LABELS),
            "decision_event_type": sorted(DECISION_EVENT_TYPE_LABELS),
            "decision_kind": sorted(DECISION_KIND_LABELS),
            "outcome_status": sorted(OUTCOME_STATUS_LABELS),
            "legal_effect": sorted(LEGAL_EFFECT_LABELS),
            "time_anchor_kind": sorted(TIME_ANCHOR_KIND_LABELS),
        },
        "accepted_topic": {
            "semantic_node_id": artifact.semantic_node_id,
            "topic_label_he": artifact.topic_label_he,
            "root_topic_id": artifact.root_topic_id,
            "root_label_he": artifact.root_label_he,
            "child_topic_id": artifact.child_topic_id,
            "child_label_he": artifact.child_label_he,
        },
        "artifact": {
            "artifact_id": artifact.artifact_id,
            "source_kind": artifact.source_kind,
            "artifact_kind": artifact.artifact_kind,
            "source_title": artifact.source_title,
            "page_span": {"start": artifact.start_page, "end": artifact.end_page},
            "header_path": artifact.header_path,
        },
        "raw_evidence_text": artifact.real_text[: max(500, int(max_text_chars))],
        "decision_context_text": (artifact.decision_context_text or clean_decision_context_text(artifact.real_text))[: max(500, int(max_text_chars))],
        "neighbor_contexts": [
            {
                "relation": context.get("relation"),
                "artifact_id": context.get("artifact_id"),
                "page_span": context.get("page_span"),
                "header_path": context.get("header_path"),
                "decision_context_text": str(context.get("decision_context_text") or "")[:1000],
            }
            for context in artifact.neighbor_contexts
        ],
    }


def load_topic_tree_for_municipality(session: Session, *, municipality_slug: str) -> dict[str, Any]:
    site_id = session.execute(select(SourceSite.id).where(SourceSite.municipality_slug == municipality_slug).order_by(SourceSite.id.asc())).scalar_one_or_none()
    if site_id is None:
        return {"topic_tree_version": None, "roots": []}
    return current_tree_from_db(session, source_site_id=int(site_id), include_candidates=False, include_profiles=False)


def load_accepted_topic_artifacts(session: Session, *, municipality_slug: str, limit: int | None = None) -> list[TopicDecisionArtifact]:
    stmt = (
        select(RetrievalArtifact, ArtifactSemanticLink, SemanticNode, Document, SourceSite)
        .join(ArtifactSemanticLink, ArtifactSemanticLink.artifact_id == RetrievalArtifact.artifact_id)
        .join(SemanticNode, SemanticNode.id == ArtifactSemanticLink.semantic_node_id)
        .join(Document, Document.id == RetrievalArtifact.document_id)
        .join(SourceSite, SourceSite.id == Document.source_site_id)
        .where(SourceSite.municipality_slug == municipality_slug)
        .where(SemanticNode.node_kind == "topic")
        .where(SemanticNode.status == "active")
        .order_by(RetrievalArtifact.document_version_id.asc(), RetrievalArtifact.ordinal.asc(), RetrievalArtifact.artifact_id.asc())
    )
    if limit is not None:
        stmt = stmt.limit(max(0, int(limit)))
    rows = session.execute(stmt).all()
    parent_ids = sorted({int(node.parent_node_id) for _artifact, _link, node, _document, _site in rows if node.parent_node_id is not None})
    parents = {}
    if parent_ids:
        parents = {int(row.id): row for row in session.execute(select(SemanticNode).where(SemanticNode.id.in_(parent_ids))).scalars().all()}

    artifacts: list[TopicDecisionArtifact] = []
    for artifact, link, node, document, _site in rows:
        link_metadata = loads_dict(link.metadata_json)
        node_metadata = loads_dict(node.metadata_json)
        parent = parents.get(int(node.parent_node_id)) if node.parent_node_id is not None else None
        parent_metadata = loads_dict(parent.metadata_json) if parent is not None else {}
        root_topic_id = string_or_none(link_metadata.get("root_topic_id") or node_metadata.get("root_topic_id") or parent_metadata.get("root_topic_id"))
        child_topic_id = string_or_none(link_metadata.get("child_topic_id") or node_metadata.get("child_topic_id"))
        real_text = compact_text(artifact.body_text or artifact.retrieval_text or "")
        if not real_text:
            continue
        artifacts.append(
            TopicDecisionArtifact(
                artifact_id=str(artifact.artifact_id),
                semantic_node_id=int(node.id),
                topic_label_he=str(node.pref_label_he),
                root_topic_id=root_topic_id,
                root_label_he=str(parent.pref_label_he) if parent is not None else str(node.pref_label_he),
                child_topic_id=child_topic_id if parent is not None else None,
                child_label_he=str(node.pref_label_he) if parent is not None else None,
                source_kind=str(artifact.source_kind),
                source_document_id=int(artifact.document_id),
                source_document_version_id=int(artifact.document_version_id),
                source_ordinal=int(artifact.ordinal),
                source_title=str(document.title_he or artifact.title_he or "מקור עירוני"),
                source_url=str(document.canonical_url or "") or None,
                artifact_kind=str(artifact.artifact_kind),
                start_page=artifact.start_page,
                end_page=artifact.end_page,
                header_path=loads_json_list(artifact.header_path_json),
                real_text=real_text,
                retrieval_text=str(artifact.retrieval_text or ""),
                topic_confidence=float(link.confidence or node.confidence or 0.0),
                existing_decision_candidate_id=string_or_none(loads_dict(artifact.metadata_json).get("decision_candidate_id")),
                metadata={"link_metadata": link_metadata, "artifact_metadata": loads_dict(artifact.metadata_json)},
            )
        )
    attach_decision_contexts(session=session, artifacts=artifacts)
    return artifacts


def attach_decision_contexts(*, session: Session, artifacts: list[TopicDecisionArtifact]) -> None:
    if not artifacts:
        return
    docver_ids = sorted({artifact.source_document_version_id for artifact in artifacts})
    rows = (
        session.execute(
            select(RetrievalArtifact)
            .where(RetrievalArtifact.document_version_id.in_(docver_ids))
            .order_by(RetrievalArtifact.document_version_id.asc(), RetrievalArtifact.ordinal.asc(), RetrievalArtifact.artifact_id.asc())
        )
        .scalars()
        .all()
    )
    rows_by_docver: dict[int, list[RetrievalArtifact]] = defaultdict(list)
    for row in rows:
        rows_by_docver[int(row.document_version_id)].append(row)
    positions = {str(row.artifact_id): idx for doc_rows in rows_by_docver.values() for idx, row in enumerate(doc_rows)}

    topic_groups: dict[tuple[int, int], list[TopicDecisionArtifact]] = defaultdict(list)
    for artifact in artifacts:
        topic_groups[(artifact.source_document_version_id, artifact.semantic_node_id)].append(artifact)
    for group in topic_groups.values():
        group.sort(key=lambda item: (item.source_ordinal, item.artifact_id))

    for artifact in artifacts:
        contexts: list[dict[str, Any]] = []
        doc_rows = rows_by_docver.get(artifact.source_document_version_id, [])
        position = positions.get(artifact.artifact_id)
        if position is not None:
            if position > 0:
                contexts.append(neighbor_context_from_row(doc_rows[position - 1], relation="previous_protocol_row"))
            if position + 1 < len(doc_rows):
                contexts.append(neighbor_context_from_row(doc_rows[position + 1], relation="next_protocol_row"))

        topic_neighbors = topic_groups.get((artifact.source_document_version_id, artifact.semantic_node_id), [])
        topic_position = next((idx for idx, item in enumerate(topic_neighbors) if item.artifact_id == artifact.artifact_id), None)
        if topic_position is not None:
            if topic_position > 0:
                contexts.append(neighbor_context_from_artifact(topic_neighbors[topic_position - 1], relation="previous_same_topic_row"))
            if topic_position + 1 < len(topic_neighbors):
                contexts.append(neighbor_context_from_artifact(topic_neighbors[topic_position + 1], relation="next_same_topic_row"))

        artifact.neighbor_contexts = dedupe_neighbor_contexts(contexts)
        artifact.decision_context_text = build_decision_context_text(artifact=artifact)


def neighbor_context_from_row(row: RetrievalArtifact, *, relation: str) -> dict[str, Any]:
    raw_text = compact_text(row.body_text or row.retrieval_text or "")
    return {
        "relation": relation,
        "artifact_id": str(row.artifact_id),
        "document_version_id": int(row.document_version_id),
        "ordinal": int(row.ordinal),
        "page_span": {"start": row.start_page, "end": row.end_page},
        "header_path": loads_json_list(row.header_path_json),
        "raw_text": raw_text[:1200],
        "decision_context_text": clean_decision_context_text(row.body_text or row.retrieval_text or "")[:1200],
    }


def neighbor_context_from_artifact(artifact: TopicDecisionArtifact, *, relation: str) -> dict[str, Any]:
    return {
        "relation": relation,
        "artifact_id": artifact.artifact_id,
        "document_version_id": artifact.source_document_version_id,
        "ordinal": artifact.source_ordinal,
        "page_span": {"start": artifact.start_page, "end": artifact.end_page},
        "header_path": artifact.header_path,
        "raw_text": artifact.real_text[:1200],
        "decision_context_text": (artifact.decision_context_text or clean_decision_context_text(artifact.real_text))[:1200],
    }


def dedupe_neighbor_contexts(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for context in contexts:
        key = (str(context.get("relation") or ""), str(context.get("artifact_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(context)
    return out


def build_decision_context_text(*, artifact: TopicDecisionArtifact) -> str:
    parts = ["primary_artifact: " + clean_decision_context_text(artifact.real_text)]
    for context in artifact.neighbor_contexts:
        text = compact_text(context.get("decision_context_text"))
        if text:
            parts.append(f"{context.get('relation')}: {text}")
    return compact_text(" ".join(parts))[:6000]


def clean_decision_context_text(value: Any) -> str:
    text = str(value or "").replace("\u200f", " ").replace("\u200e", " ")
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines() or [text]:
        line = " ".join(raw_line.split()).strip()
        if not line:
            continue
        if re.fullmatch(r"[-–—•\s]*\d+[-–—•\s]*", line):
            continue
        if re.search(r"\bעמוד\s+\d+\b", line):
            continue
        if re.search(r"\b(חתימה|בברכה)\b", line) and len(line) <= 80:
            continue
        line = re.sub(r"^[א-ת\"' .\-]{2,40}:\s*", "", line)
        if line:
            cleaned_lines.append(line)
    return compact_text(" ".join(cleaned_lines))


def run_topic_decision_research(
    session: Session,
    *,
    config: TopicDecisionResearchConfig,
    client: TopicDecisionClient | None = None,
) -> TopicDecisionResearchResult:
    started = time.perf_counter()
    client = client or OllamaTopicDecisionClient()
    topic_tree = load_topic_tree_for_municipality(session, municipality_slug=config.municipality_slug)
    artifacts = load_accepted_topic_artifacts(session, municipality_slug=config.municipality_slug, limit=config.limit)
    run_row: TopicDecisionRun | None = None
    if config.write:
        run_row = TopicDecisionRun(
            municipality_slug=config.municipality_slug,
            model_provider="ollama",
            model_name=config.model_name,
            status="running",
            write_mode=True,
            source_artifact_count=len(artifacts),
            metadata_json=json.dumps({"provenance": PROVENANCE, "limit": config.limit}, ensure_ascii=False),
        )
        session.add(run_row)
        session.flush()

    extracted: list[ExtractedTopicDecision] = []
    quality_rows: list[TopicDecisionQualityRow] = []
    for artifact in artifacts:
        model_payload = client.extract(artifact=artifact, config=config)
        artifact_decisions, quality = process_topic_decision_payload(artifact=artifact, model_payload=model_payload)
        extracted.extend(artifact_decisions)
        quality_rows.append(quality)
        if run_row is not None:
            persist_topic_decision_artifact_result(session=session, run=run_row, artifact_decisions=artifact_decisions, quality_row=quality)

    if run_row is not None:
        run_row.status = "completed"
        run_row.extraction_count = len(extracted)
        run_row.accepted_decision_count = sum(1 for item in extracted if item.validation_status == "accepted")
        run_row.failed_count = sum(1 for row in quality_rows if row.status in {"failed", "partial", "model_error"})
        run_row.finished_at = datetime.utcnow()
        session.flush()

    result = TopicDecisionResearchResult(
        run_id=int(run_row.id) if run_row is not None else None,
        topic_tree=topic_tree,
        artifacts=artifacts,
        extracted_decisions=extracted,
        quality_rows=quality_rows,
        elapsed_seconds=round(time.perf_counter() - started, 3),
    )
    if config.output_dir is not None:
        result.output_paths.update(write_topic_decision_outputs(output_dir=config.output_dir, result=result))
    return result


def process_topic_decision_payload(*, artifact: TopicDecisionArtifact, model_payload: dict[str, Any]) -> tuple[list[ExtractedTopicDecision], TopicDecisionQualityRow]:
    if model_payload.get("error_code"):
        quality = TopicDecisionQualityRow(
            artifact_id=artifact.artifact_id,
            semantic_node_id=artifact.semantic_node_id,
            topic=artifact.topic_label_he,
            real_text=artifact.real_text,
            decision_by_dicta="",
            decision_ground_truth="לא ניתן לשפוט כי קריאת Dicta נכשלה.",
            reason_for_failure=str(model_payload.get("error_text") or model_payload.get("error_code") or "model_error"),
            status="model_error",
            metadata={"model_payload": compact_payload(model_payload)},
        )
        return [], quality

    decisions_raw = model_payload.get("decisions") if isinstance(model_payload.get("decisions"), list) else []
    extracted: list[ExtractedTopicDecision] = []
    for idx, decision_raw in enumerate(decisions_raw):
        if not isinstance(decision_raw, dict):
            continue
        decision_payload, failures = normalize_decision_payload(decision_raw)
        source_quote = str(decision_payload.get("source_quote_he") or "").strip()
        if not source_quote:
            failures.append("missing_source_quote")
        elif not quote_supported_by_text(quote=source_quote, text=artifact.real_text):
            failures.append("source_quote_not_grounded_in_artifact_text")
        apply_semantic_decision_validation(artifact=artifact, decision_payload=decision_payload, failures=failures)
        failures = unique_strings(failures)
        evidence_refs = [evidence_ref_for_decision(artifact=artifact, source_quote=source_quote)] if source_quote else []
        resident_links = [{"label_he": "מקור", "icon": "document-link", "evidence_ref": evidence_refs[0]["id"]}] if evidence_refs else []
        validation_status = "accepted" if not failures else "failed"
        judge_payload = judge_decision(artifact=artifact, decision_payload=decision_payload, failures=failures)
        extracted.append(
            ExtractedTopicDecision(
                artifact=artifact,
                decision_index=idx,
                decision_payload=decision_payload,
                validation_status=validation_status,
                failure_reasons=failures,
                judge_payload=judge_payload,
                evidence_refs=evidence_refs,
                resident_evidence_links=resident_links,
            )
        )

    quality = quality_row_for_artifact(artifact=artifact, extracted=extracted, model_payload=model_payload)
    return extracted, quality


def normalize_decision_payload(row: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    decision_presence = normalize_code_label(row.get("decision_presence"), labels=DECISION_PRESENCE_LABELS, vocabulary="municipal-decision-presence:v1", failure_prefix="invalid_decision_presence", failures=failures, default_code="NO_DECISION")
    decision_event_type = normalize_code_label(row.get("decision_event_type"), labels=DECISION_EVENT_TYPE_LABELS, vocabulary="municipal-decision-event-type:v1", failure_prefix="invalid_decision_event_type", failures=failures)
    decision_kind = normalize_code_label(row.get("decision_kind"), labels=DECISION_KIND_LABELS, vocabulary="municipal-decision-kind:v1", failure_prefix="invalid_decision_kind", failures=failures)
    outcome_status = normalize_code_label(row.get("outcome_status"), labels=OUTCOME_STATUS_LABELS, vocabulary="municipal-outcome-status:v1", failure_prefix="invalid_outcome_status", failures=failures)
    legal_effect = normalize_code_label(row.get("legal_effect"), labels=LEGAL_EFFECT_LABELS, vocabulary="municipal-legal-effect:v1", failure_prefix="invalid_legal_effect", failures=failures)
    primary_time = normalize_time_anchor(row.get("primary_time"), failures=failures)
    time_anchors = []
    for item in row.get("time_anchors") or []:
        if isinstance(item, dict):
            time_anchors.append(normalize_time_anchor(item, failures=failures))
    if primary_time not in time_anchors:
        time_anchors.insert(0, primary_time)
    confidence = clamp_float(row.get("confidence"), default=0.0)
    decision_payload = {
        "title_he": compact_text(row.get("title_he"))[:180] or "החלטה ללא כותרת",
        "decision_text_he": compact_text(row.get("decision_text_he"))[:2000],
        "summary_he": compact_text(row.get("summary_he"))[:500],
        "decision_presence": decision_presence,
        "decision_event_type": decision_event_type,
        "decision_kind": decision_kind,
        "outcome_status": outcome_status,
        "legal_effect": legal_effect,
        "primary_time": primary_time,
        "time_anchors": time_anchors,
        "source_quote_he": compact_text(row.get("source_quote_he"))[:1000],
        "confidence": confidence,
        "new_decision_label_candidate_he": string_or_none(row.get("new_decision_label_candidate_he")),
        "limitations": [compact_text(item) for item in row.get("limitations") or [] if compact_text(item)],
        "raw_model_payload": row,
    }
    if not decision_payload["decision_text_he"] and not decision_payload["summary_he"]:
        failures.append("missing_decision_text")
    return decision_payload, unique_strings(failures)


def normalize_code_label(value: Any, *, labels: dict[str, str], vocabulary: str, failure_prefix: str, failures: list[str], default_code: str = "UNKNOWN") -> dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    fallback_code = default_code if default_code in labels else ("UNKNOWN" if "UNKNOWN" in labels else next(iter(labels)))
    raw_code = str(row.get("code") or fallback_code).strip().upper()
    if raw_code not in labels:
        failures.append(f"{failure_prefix}:{raw_code or 'empty'}")
        raw_code = fallback_code
    return {
        "code": raw_code,
        "label_he": compact_text(row.get("label_he")) or labels[raw_code],
        "vocabulary": vocabulary,
        "raw_text": compact_text(row.get("raw_text")) or None,
        "confidence_label": compact_text(row.get("confidence_label")) or "נמוכה",
    }


def normalize_time_anchor(value: Any, *, failures: list[str]) -> dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    kind = str(row.get("kind") or "unknown").strip()
    if kind not in TIME_ANCHOR_KIND_LABELS:
        failures.append(f"invalid_time_anchor_kind:{kind or 'empty'}")
        kind = "unknown"
    return {
        "kind": kind,
        "start": string_or_none(row.get("start")),
        "end": string_or_none(row.get("end")),
        "precision": string_or_none(row.get("precision")) or "unknown",
        "label_he": compact_text(row.get("label_he")) or TIME_ANCHOR_KIND_LABELS[kind],
        "confidence_label": compact_text(row.get("confidence_label")) or "נמוכה",
        "evidence_refs": [],
    }


def judge_decision(*, artifact: TopicDecisionArtifact, decision_payload: dict[str, Any], failures: list[str]) -> dict[str, Any]:
    if failures:
        return {
            "judgement": "failed",
            "decision_ground_truth": "לא ניתן לאמת החלטה מהטקסט המצורף.",
            "reason_for_failure": "; ".join(failures),
        }
    quote = compact_text(decision_payload.get("source_quote_he"))[:220]
    return {
        "judgement": "accepted",
        "decision_ground_truth": f"נראה שההחלטה נתמכת בציטוט מהטקסט המצורף: {quote}",
        "reason_for_failure": "",
    }


def apply_semantic_decision_validation(*, artifact: TopicDecisionArtifact, decision_payload: dict[str, Any], failures: list[str]) -> None:
    presence_code = str((decision_payload.get("decision_presence") or {}).get("code") or "NO_DECISION")
    event_code = str((decision_payload.get("decision_event_type") or {}).get("code") or "UNKNOWN")
    kind_code = str((decision_payload.get("decision_kind") or {}).get("code") or "UNKNOWN")
    outcome_code = str((decision_payload.get("outcome_status") or {}).get("code") or "UNKNOWN")
    quote = compact_text(decision_payload.get("source_quote_he"))
    quote_norm = normalize_for_search(quote)

    if presence_code != "DECISION_PRESENT":
        failures.append(f"decision_presence_not_decision:{presence_code}")

    if event_code == "PROPOSAL":
        failures.append("proposal_event_without_decision_outcome")
    if event_code == "DISCUSSION" and outcome_code == "NO_FORMAL_OUTCOME":
        failures.append("discussion_without_decision_outcome")

    if text_has_any(quote_norm, REQUEST_ONLY_CUES) and not text_has_any(quote_norm, DECISION_ACTION_CUES):
        failures.append("request_or_proposal_without_decision_outcome")

    if kind_code == "APPROVAL" or outcome_code in {"APPROVED", "APPROVED_WITH_CONDITIONS"} or event_code in {"ACCEPTANCE", "IMMEDIATE_APPROVAL"}:
        if not text_has_any(quote_norm, APPROVAL_VERB_CUES):
            failures.append("approval_without_approval_language")

    if presence_code == "DECISION_PRESENT" and quote and not text_has_any(quote_norm, DECISION_ACTION_CUES):
        failures.append("decision_action_not_grounded_in_source_quote")

    if artifact.decision_context_text and quote and not quote_supported_by_text(quote=quote, text=artifact.real_text):
        failures.append("source_quote_from_context_instead_of_raw_evidence")


def quality_row_for_artifact(*, artifact: TopicDecisionArtifact, extracted: list[ExtractedTopicDecision], model_payload: dict[str, Any]) -> TopicDecisionQualityRow:
    if not extracted:
        reason = compact_text(model_payload.get("no_decision_reason_he")) or "Dicta לא החזירה החלטה"
        return TopicDecisionQualityRow(
            artifact_id=artifact.artifact_id,
            semantic_node_id=artifact.semantic_node_id,
            topic=artifact.topic_label_he,
            real_text=artifact.real_text,
            decision_by_dicta="",
            decision_ground_truth="לא זיהיתי החלטה מפורשת בטקסט המצורף.",
            reason_for_failure="",
            status="no_decision",
            metadata={"no_decision_reason_he": reason, "model_payload": compact_payload(model_payload)},
        )
    decision_by_dicta = " | ".join(decision_summary(item.decision_payload) for item in extracted)
    failed = [item for item in extracted if item.validation_status != "accepted"]
    accepted = [item for item in extracted if item.validation_status == "accepted"]
    if accepted and not failed:
        status = "accepted"
        ground_truth = " | ".join(str(item.judge_payload.get("decision_ground_truth") or "") for item in accepted)
        reason = ""
    elif accepted and failed:
        status = "partial"
        ground_truth = "חלק מההחלטות נתמכות בטקסט וחלק דורשות בדיקה."
        reason = "; ".join(unique_strings(reason for item in failed for reason in item.failure_reasons))
    else:
        status = "failed"
        ground_truth = "לא ניתן לאמת החלטה מהטקסט המצורף."
        reason = "; ".join(unique_strings(reason for item in failed for reason in item.failure_reasons))
    return TopicDecisionQualityRow(
        artifact_id=artifact.artifact_id,
        semantic_node_id=artifact.semantic_node_id,
        topic=artifact.topic_label_he,
        real_text=artifact.real_text,
        decision_by_dicta=decision_by_dicta,
        decision_ground_truth=ground_truth,
        reason_for_failure=reason,
        status=status,
        metadata={"model_payload": compact_payload(model_payload)},
    )


def persist_topic_decision_artifact_result(
    *,
    session: Session,
    run: TopicDecisionRun,
    artifact_decisions: list[ExtractedTopicDecision],
    quality_row: TopicDecisionQualityRow,
) -> None:
    for item in artifact_decisions:
        if item.validation_status != "accepted":
            continue
        decision = item.decision_payload
        artifact = item.artifact
        primary_time = decision.get("primary_time") if isinstance(decision.get("primary_time"), dict) else {}
        row = TopicDecision(
            run_id=int(run.id),
            municipality_slug=str(run.municipality_slug),
            artifact_id=artifact.artifact_id,
            semantic_node_id=artifact.semantic_node_id,
            decision_index=item.decision_index,
            root_topic_id=artifact.root_topic_id,
            child_topic_id=artifact.child_topic_id,
            topic_label_he=artifact.topic_label_he,
            source_kind=artifact.source_kind,
            source_document_id=artifact.source_document_id,
            source_document_version_id=artifact.source_document_version_id,
            source_page_start=artifact.start_page,
            source_page_end=artifact.end_page,
            source_title=artifact.source_title,
            decision_title_he=decision.get("title_he"),
            decision_text_he=decision.get("decision_text_he"),
            decision_summary_he=decision.get("summary_he"),
            decision_kind_code=str((decision.get("decision_kind") or {}).get("code") or "UNKNOWN"),
            decision_kind_label_he=str((decision.get("decision_kind") or {}).get("label_he") or DECISION_KIND_LABELS["UNKNOWN"]),
            outcome_status_code=str((decision.get("outcome_status") or {}).get("code") or "UNKNOWN"),
            outcome_status_label_he=str((decision.get("outcome_status") or {}).get("label_he") or OUTCOME_STATUS_LABELS["UNKNOWN"]),
            legal_effect_code=str((decision.get("legal_effect") or {}).get("code") or "UNKNOWN"),
            legal_effect_label_he=str((decision.get("legal_effect") or {}).get("label_he") or LEGAL_EFFECT_LABELS["UNKNOWN"]),
            primary_time_kind=primary_time.get("kind"),
            primary_time_start=primary_time.get("start"),
            primary_time_end=primary_time.get("end"),
            primary_time_precision=primary_time.get("precision"),
            primary_time_label_he=primary_time.get("label_he"),
            confidence=float(decision.get("confidence") or 0.0),
            validation_status=item.validation_status,
            failure_reason="; ".join(item.failure_reasons),
            source_quote_he=decision.get("source_quote_he"),
            time_anchors_json=json.dumps(decision.get("time_anchors") or [], ensure_ascii=False),
            evidence_refs_json=json.dumps(item.evidence_refs, ensure_ascii=False),
            resident_evidence_links_json=json.dumps(item.resident_evidence_links, ensure_ascii=False),
            limitations_json=json.dumps(decision.get("limitations") or [], ensure_ascii=False),
            dicta_payload_json=json.dumps(decision.get("raw_model_payload") or {}, ensure_ascii=False),
            judge_payload_json=json.dumps(item.judge_payload, ensure_ascii=False),
        )
        session.add(row)

    session.add(
        TopicDecisionQualityReport(
            run_id=int(run.id),
            artifact_id=quality_row.artifact_id,
            semantic_node_id=quality_row.semantic_node_id,
            topic_label_he=quality_row.topic,
            real_text=quality_row.real_text,
            decision_by_dicta=quality_row.decision_by_dicta,
            decision_ground_truth=quality_row.decision_ground_truth,
            reason_for_failure=quality_row.reason_for_failure,
            status=quality_row.status,
            metadata_json=json.dumps(quality_row.metadata, ensure_ascii=False),
        )
    )
    session.flush()


def evidence_ref_for_decision(*, artifact: TopicDecisionArtifact, source_quote: str) -> dict[str, Any]:
    evidence_id = "topic_decision_evidence_" + hashlib.sha1(f"{artifact.artifact_id}|{source_quote}".encode("utf-8")).hexdigest()[:16]
    return {
        "id": evidence_id,
        "source_type": artifact.source_kind,
        "source_title": artifact.source_title,
        "source_url": artifact.source_url,
        "retrieval_artifact_id": artifact.artifact_id,
        "artifact_kind": artifact.artifact_kind,
        "retrieval_set_id": artifact.metadata.get("artifact_metadata", {}).get("retrieval_set_id"),
        "header_path": artifact.header_path,
        "page_span": {"start": artifact.start_page, "end": artifact.end_page},
        "start_offset": None,
        "end_offset": None,
        "bbox": None,
        "text": source_quote,
        "confidence_label": "בינונית",
        "extraction_warnings": [],
    }


def write_topic_decision_outputs(*, output_dir: Path, result: TopicDecisionResearchResult) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    tree_path = output_dir / "topic_tree.md"
    artifacts_path = output_dir / "ashdod_artifacts.md"
    quality_path = output_dir / "quality_report.md"
    quality_json_path = output_dir / "quality_report.json"
    tree_path.write_text(topic_tree_markdown(result.topic_tree), encoding="utf-8")
    artifacts_path.write_text(artifacts_markdown(result.artifacts), encoding="utf-8")
    quality_path.write_text(quality_report_markdown(result.quality_rows), encoding="utf-8")
    quality_json_path.write_text(json.dumps([quality_row_to_dict(row) for row in result.quality_rows], ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "topic_tree_md": str(tree_path),
        "artifacts_md": str(artifacts_path),
        "quality_report_md": str(quality_path),
        "quality_report_json": str(quality_json_path),
    }


def topic_tree_markdown(tree: dict[str, Any]) -> str:
    lines = ["## Topic Tree", "", "| Topic | Support | Status |", "|---|---:|---|"]
    for root in tree.get("roots") or []:
        root_label = str(root.get("root_label_he") or "")
        lines.append(f"| {escape_table(root_label)} | {int(root.get('support_count') or 0)} | {escape_table(root.get('status'))} |")
        for child in root.get("children") or []:
            child_label = "  " + str(child.get("child_label_he") or "")
            lines.append(f"| {escape_table(child_label)} | {int(child.get('support_count') or 0)} | {escape_table(child.get('status'))} |")
    return "\n".join(lines) + "\n"


def artifacts_markdown(artifacts: list[TopicDecisionArtifact]) -> str:
    lines = ["## Ashdod Accepted Artifacts", "", "| Topic | Artifact | Page | Real Text |", "|---|---|---:|---|"]
    for artifact in artifacts:
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_table(artifact.topic_label_he),
                    escape_table(artifact.artifact_id),
                    escape_table(artifact.start_page if artifact.start_page is not None else ""),
                    escape_table(shorten(artifact.real_text, 180)),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def quality_report_markdown(rows: list[TopicDecisionQualityRow]) -> str:
    lines = ["## Topic Decision Quality Report", "", "| Topic | Real Text | Decision By Dicta | My Judgment | Decision Ground Truth | Reason For Failure |", "|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_table(row.topic),
                    escape_table(shorten(row.real_text, 220)),
                    escape_table(shorten(row.decision_by_dicta, 220)),
                    escape_table(row.status),
                    escape_table(shorten(row.decision_ground_truth, 220)),
                    escape_table(shorten(row.reason_for_failure, 220)),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def quality_row_to_dict(row: TopicDecisionQualityRow) -> dict[str, Any]:
    return {
        "artifact_id": row.artifact_id,
        "semantic_node_id": row.semantic_node_id,
        "topic": row.topic,
        "real_text": row.real_text,
        "decision_by_dicta": row.decision_by_dicta,
        "decision_ground_truth": row.decision_ground_truth,
        "reason_for_failure": row.reason_for_failure,
        "status": row.status,
        "metadata": row.metadata,
    }


def decision_summary(decision: dict[str, Any]) -> str:
    parts = [
        compact_text(decision.get("summary_he") or decision.get("decision_text_he")),
        str((decision.get("decision_presence") or {}).get("code") or "NO_DECISION"),
        str((decision.get("decision_event_type") or {}).get("code") or "UNKNOWN"),
        str((decision.get("decision_kind") or {}).get("code") or "UNKNOWN"),
        str((decision.get("outcome_status") or {}).get("code") or "UNKNOWN"),
    ]
    return " / ".join(part for part in parts if part)


def parse_json_object(value: str) -> dict[str, Any] | None:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def quote_supported_by_text(*, quote: str, text: str) -> bool:
    quote_norm = normalize_for_search(quote)
    text_norm = normalize_for_search(text)
    if not quote_norm:
        return False
    if quote_norm in text_norm:
        return True
    tokens = [token for token in quote_norm.split() if len(token) >= 3]
    if len(tokens) < 3:
        return False
    hits = sum(1 for token in tokens if token in text_norm)
    return hits >= min(len(tokens), max(3, int(len(tokens) * 0.75)))


def text_has_any(text_or_norm: str, cues: tuple[str, ...]) -> bool:
    text_norm = normalize_for_search(text_or_norm)
    return any(normalize_for_search(cue) in text_norm for cue in cues)


def compact_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u200f", " ").replace("\u200e", " ").split()).strip()


def loads_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def loads_json_list(value: Any) -> list[str]:
    try:
        loaded = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded if str(item).strip()]


def string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def clamp_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def unique_strings(values: list[str] | Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload.get(key) for key in ("error_code", "error_text", "no_decision_reason_he", "rationale_he", "decisions") if key in payload}


def escape_table(value: Any) -> str:
    text = compact_text(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def shorten(value: Any, length: int) -> str:
    text = compact_text(value)
    if len(text) <= length:
        return text
    return text[: max(0, length - 3)].rstrip() + "..."


def evidence_ref_to_public_id(artifact_id: str) -> str:
    encoded = base64.urlsafe_b64encode(str(artifact_id).encode("utf-8")).decode("ascii").rstrip("=")
    return f"artifact_{encoded}"
