from __future__ import annotations

import json
import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
from sqlalchemy.orm import Session

from municipality.chunking import normalize_for_search
from municipality.models import TopicSubject, TopicSubjectQualityReport, TopicSubjectRun
from municipality.topic_decisions import (
    APPROVAL_VERB_CUES,
    DECISION_ACTION_CUES,
    DEFAULT_DICTA_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    REQUEST_ONLY_CUES,
    TopicDecisionArtifact,
    artifacts_markdown,
    clamp_float,
    compact_text,
    escape_table,
    evidence_ref_for_decision,
    load_accepted_topic_artifacts,
    load_topic_tree_for_municipality,
    parse_json_object,
    quote_supported_by_text,
    shorten,
    string_or_none,
    text_has_any,
    topic_tree_markdown,
    unique_strings,
)


PROVENANCE = "topic_subject_research_v1"

CONDITION_SCOPE_CUES = (
    "בהזמנות הנגזרות",
    "חוזה חתום",
    "מכרז",
    "פטור ממכרז",
    "יועמ\"ש",
    "יועמ ש",
    "ועדת רכש",
    "הצעות מחיר",
    "הקצבות",
    "תמיכות",
    "הסכום ללא הגבלה",
    "הסכומים לעיל",
    "מקסימליים",
    "להגבילם בנוהל פנימי",
    "עד 10,000",
    "בהתאם לסעיפים",
    "פקודת העיריות",
)

DETAIL_CONDITION_CUES = (
    "בתמורה",
    "יצטרכו לבצע",
    "יצטרך לבצע",
    "שעות התנדבות",
    "שעות פעילות",
    "בשנת הלימודים",
    "בלבד",
)

VOTE_DETAIL_CUES = (
    "בעד",
    "נגד",
    "נמנע",
    "נמנעים",
)

QUESTION_DETAIL_CUES = (
    "?",
    "מי ",
    "מתי",
    "מה ",
    "האם",
    "כיצד",
    "מדוע",
)

MEETING_METADATA_CUES = (
    "ישיבת מועצה",
    "מספר דיון",
    "מספר די ו",
    "התקיים בתאריך",
    "נוהל ע",
    "מיקום הישיבה",
    "תועד ע",
    "תאריך אישור",
    "הוקלד",
    "הודפס",
    "נושא הדיון",
)

PARTICIPANT_LIST_CUES = (
    "השתתפו",
    "נכחו",
    "ראש עיר",
    "ממ\"ק",
    "ממ ק",
    "סגן רה\"ע",
    "סגן רה ע",
    "חבר מועצה",
    "חברת מועצה",
    "לשכת",
)

DOMAIN_NOUN_ROOT_CUES = (
    "האצלת סמכויות",
    "האצלת סמכות",
    "הסכמים",
    "התקשרויות",
    "סמכות חתימה",
    "סמכויות חתימה",
    "חוזים",
    "הזמנות",
)

STRONG_APPROVAL_ACTION_CUES = (
    "מאשרים",
    "הוחלט לאשר",
    "פה אחד",
    "ברוב קולות",
    "המועצה מאשרת",
    "המועצה אישרה",
)

CONTACT_INFO_CUES = (
    "רחוב",
    "ת.ד",
    "מיקוד",
    "טלפון",
    "פקס",
)

PROTOCOL_HEADER_CUES = (
    "פרוטוקול",
    "מס' פרוטוקול",
    "מס פרוטוקול",
    "מישיבת",
    "ישיבת",
    "שהתקיימה בתאריך",
    "משתתפים",
    "נוכחים",
    "יו\"ר",
    "חבר ועדה",
)


@dataclass(slots=True)
class TopicSubjectResearchConfig:
    municipality_slug: str = "ashdod"
    model_name: str = DEFAULT_DICTA_MODEL
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    timeout_seconds: float = 180.0
    max_text_chars: int = 3500
    offset: int = 0
    limit: int | None = None
    write: bool = False
    output_dir: Path | None = None


@dataclass(slots=True)
class ExtractedTopicSubject:
    artifact: TopicDecisionArtifact
    subject_index: int
    subject_payload: dict[str, Any]
    validation_status: str
    failure_reasons: list[str]
    judge_payload: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    resident_evidence_links: list[dict[str, str]]


@dataclass(slots=True)
class TopicSubjectQualityRow:
    artifact_id: str
    semantic_node_id: int
    topic: str
    real_text: str
    what_text_is_about: str
    artifact_role: str
    topic_relevance: str
    subject_root_by_dicta: str
    subject_child_by_dicta: str
    subject_object_by_dicta: str
    subject_details_by_dicta: str
    decision_by_dicta: str
    my_judgment: str
    ground_truth: str
    reason_for_failure: str
    status: str
    event_id: str = ""
    event_topic: str = ""
    row_role: str = ""
    anchor_status: str = ""
    linked_event_id: str = ""
    link_confidence: float = 0.0
    link_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TopicSubjectResearchResult:
    run_id: int | None
    topic_tree: dict[str, Any]
    artifacts: list[TopicDecisionArtifact]
    extracted_subjects: list[ExtractedTopicSubject]
    quality_rows: list[TopicSubjectQualityRow]
    elapsed_seconds: float
    output_paths: dict[str, str] = field(default_factory=dict)

    @property
    def candidate_subject_count(self) -> int:
        return sum(1 for item in self.extracted_subjects if is_countable_subject_event(item))

    @property
    def candidate_decision_count(self) -> int:
        return sum(1 for item in self.extracted_subjects if is_countable_subject_event(item) and bool(item.subject_payload.get("is_decision")))

    @property
    def failed_count(self) -> int:
        return sum(1 for row in self.quality_rows if row.status in {"failed", "model_error", "partial", "no_subject"})


class TopicSubjectClient(Protocol):
    def extract(self, *, artifact: TopicDecisionArtifact, config: TopicSubjectResearchConfig) -> dict[str, Any]:
        raise NotImplementedError


class OllamaTopicSubjectClient:
    def extract(self, *, artifact: TopicDecisionArtifact, config: TopicSubjectResearchConfig) -> dict[str, Any]:
        request_payload = topic_subject_prompt_payload(artifact=artifact, max_text_chars=config.max_text_chars)
        body = {
            "model": config.model_name,
            "stream": False,
            "think": False,
            "format": "json",
            "messages": [
                {
                    "role": "system",
                    "content": "/no_think\nYou are a Hebrew municipal subject-tree extraction judge. Return strict JSON only.",
                },
                {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False)},
            ],
            "options": {"temperature": 0.0, "num_predict": 4096},
            "keep_alive": "30m",
        }
        try:
            timeout = httpx.Timeout(config.timeout_seconds, connect=10.0, read=config.timeout_seconds, write=30.0, pool=10.0)
            with httpx.Client(timeout=timeout) as client:
                response = client.post(f"{config.ollama_base_url.rstrip('/')}/api/chat", json=body)
                response.raise_for_status()
                raw_payload = response.json()
        except Exception as exc:  # noqa: BLE001
            return {"error_code": "MODEL_REQUEST_FAILED", "error_text": f"{exc.__class__.__name__}:{exc}"}

        content = str(((raw_payload.get("message") or {}).get("content")) or "")
        parsed = parse_json_object(content)
        if not isinstance(parsed, dict):
            repaired = self._repair_json(raw_content=content, config=config)
            if isinstance(repaired, dict):
                repaired["raw_payload"] = raw_payload
                repaired["json_repair_applied"] = True
                return repaired
            return {"error_code": "MODEL_INVALID_JSON", "error_text": content[:500], "raw_payload": raw_payload}
        parsed["raw_payload"] = raw_payload
        return parsed

    def _repair_json(self, *, raw_content: str, config: TopicSubjectResearchConfig) -> dict[str, Any] | None:
        if not raw_content.strip():
            return None
        body = {
            "model": config.model_name,
            "stream": False,
            "think": False,
            "format": "json",
            "messages": [
                {
                    "role": "system",
                    "content": "/no_think\nReturn valid JSON only. Do not add facts. Do not use markdown.",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "repair_invalid_json_without_changing_meaning",
                            "instructions": [
                                "Convert the supplied text into valid JSON matching this shape: {subjects:[{subject_root_label_he,subject_child_label_he,subject_summary_he,what_text_is_about_he,is_decision,decision,confidence,rationale_he}],overall_summary_he,rationale_he}.",
                                "Do not add new information.",
                                "Escape or remove double quotes inside Hebrew string values.",
                                "If a field is missing, use an empty string, false, null, or 0.0 as appropriate.",
                            ],
                            "invalid_json_text": raw_content[:6000],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "options": {"temperature": 0.0, "num_predict": 2048},
            "keep_alive": "30m",
        }
        try:
            timeout = httpx.Timeout(config.timeout_seconds, connect=10.0, read=config.timeout_seconds, write=30.0, pool=10.0)
            with httpx.Client(timeout=timeout) as client:
                response = client.post(f"{config.ollama_base_url.rstrip('/')}/api/chat", json=body)
                response.raise_for_status()
                raw_payload = response.json()
        except Exception:
            return None
        content = str(((raw_payload.get("message") or {}).get("content")) or "")
        return parse_json_object(content)


class MockTopicSubjectClient:
    """Fast local client for verification; production runs should use OllamaTopicSubjectClient."""

    def extract(self, *, artifact: TopicDecisionArtifact, config: TopicSubjectResearchConfig) -> dict[str, Any]:
        text = compact_text(artifact.real_text)
        text_norm = normalize_for_search(text)
        topic = artifact.topic_label_he
        is_request = text_has_any(text_norm, REQUEST_ONLY_CUES) and not text_has_any(text_norm, DECISION_ACTION_CUES)
        is_decision = text_has_any(text_norm, DECISION_ACTION_CUES) and not is_request
        if is_decision:
            is_approval = text_has_any(text_norm, APPROVAL_VERB_CUES)
            decision_label = "אישור" if is_approval else "פעולה עירונית"
            return {
                "subjects": [
                    {
                        "subject_root_label_he": "אישור" if is_approval else "פעולה",
                        "subject_child_label_he": "אישור פעולה" if is_approval else "פעולה עירונית",
                        "subject_object_he": topic,
                        "subject_details_he": text[:180],
                        "subject_type_evidence_he": text[:120],
                        "artifact_role": "substantive_subject",
                        "topic_relevance": "topic_bearing",
                        "subject_summary_he": text[:180],
                        "what_text_is_about_he": f"הטקסט מתאר החלטה בנושא {topic}.",
                        "is_decision": True,
                        "decision": {
                            "decision_label_he": decision_label,
                            "decision_summary_he": text[:180],
                            "source_quote_he": text[:260],
                            "confidence": 0.7,
                            "limitations": ["פלט בדיקה מקומי; לא הופק על ידי Dicta"],
                        },
                        "confidence": 0.7,
                        "rationale_he": "mock",
                    }
                ],
                "overall_summary_he": f"זוהתה החלטה בנושא {topic}",
                "rationale_he": "mock",
            }
        if text_has_any(text_norm, ("הצעה לסדר יום", "הצעה לסדר")):
            root = "הצעה"
            child = "הצעה לסדר יום"
            about = f"הטקסט מציג הצעה לסדר יום בנושא {topic}."
        elif is_request:
            root = "בקשה"
            child = "בקשת אישור"
            about = f"הטקסט הוא בקשה לאישור בנושא {topic}, ללא תוצאת החלטה."
        else:
            root = "מסגרת מקצועית"
            child = "הסבר מקצועי"
            about = f"הטקסט מספק רקע או הסבר בנושא {topic}."
        return {
            "subjects": [
                {
                    "subject_root_label_he": root,
                    "subject_child_label_he": child,
                    "subject_object_he": topic,
                    "subject_details_he": text[:180],
                    "subject_type_evidence_he": text[:120],
                    "artifact_role": "substantive_subject",
                    "topic_relevance": "topic_bearing",
                    "subject_summary_he": text[:180],
                    "what_text_is_about_he": about,
                    "is_decision": False,
                    "decision": None,
                    "confidence": 0.65,
                    "rationale_he": "mock",
                }
            ],
            "overall_summary_he": about,
            "rationale_he": "mock",
        }


def topic_subject_prompt_payload(*, artifact: TopicDecisionArtifact, max_text_chars: int) -> dict[str, Any]:
    return {
        "task": "extract_open_subject_tree_candidates_from_accepted_topic_artifact",
        "requirements": [
            "The topic was already accepted by the topic pipeline. Do not reclassify the topic and do not modify the topic tree.",
            "Identify what the artifact text is about inside the known topic.",
            "Return exactly one primary subject for the current artifact.",
            "Create open-ended Hebrew subject_root_label_he and subject_child_label_he candidates. They are not closed lists, but they must be topic-agnostic reusable subject-type labels.",
            "Never include the known topic label, named people, job titles, dates, places, or concrete objects in subject_root_label_he or subject_child_label_he.",
            "Subject labels must be tied to semantic action or intent: ask what the current text is doing, not what domain/object it mentions.",
            "subject_root_label_he is the broad action/intent type, for example: בקשה, אישור, דחייה, הפניה, דיון, דיווח, הגדרה, קביעה, מינוי, הקצאה.",
            "subject_child_label_he inherits from the root and is a more specific action/intent subtype, for example: בקשת אישור, אישור השתתפות, הגדרת תנאים, קביעת היקף, דיווח סטטוס.",
            "If the text lists applicable conditions, scope, rules, or eligibility without requesting or approving, use a generic action such as הגדרה/הגדרת תנאים or קביעה/קביעת היקף.",
            "Do not use domain nouns such as האצלת סמכויות, הסכמים, התקשרויות, סמכות חתימה, חוזים, or הזמנות as subject_root_label_he; put them in subject_object_he/details.",
            "Do not use רקע, מסמך, מטא-מסמך, תאריך, חתימה, or מספר אסמכתא as subject roots. Those are artifact roles, not subjects.",
            "Put concrete content such as the trip, seminar, person, role, contract, committee, date, or legal basis in subject_object_he and subject_details_he, not in root/child labels.",
            "Provide subject_type_evidence_he as a short exact phrase from raw_evidence_text that supports the root/child action type.",
            "Use the examples only as guidance. Do not copy an example when a better subject label fits the text.",
            "Prefer reusable labels that can group future artifacts, but do not force a generic label if the text has a concrete subject.",
            "A decision is only one possible subject. Requests, proposals, background, agenda rows, discussion, and budget details are valid non-decision subjects.",
            "Set is_decision=true only when raw_evidence_text directly contains a municipal action/outcome such as approval, rejection, referral, postponement, or binding procedural action.",
            "If a decision-like action appears only in neighbor_contexts, keep is_decision=false for the current artifact and describe the current artifact subject instead.",
            "If raw_evidence_text is only a date, reference number, meeting/protocol header, participant list, signature, salutation, or letter footer, return a structural artifact role, not a substantive subject or decision.",
            "Do not infer approval from a request for approval. A request/proposal remains is_decision=false unless the text says the body approved/rejected/decided.",
            "If is_decision=true, decision.source_quote_he must be a short exact quote copied from raw_evidence_text, not from neighbor_contexts.",
            "Use decision_context_text and neighbor_contexts only to understand adjacent-row context and the object/outcome relationship.",
            "Keep labels and summaries concise so the JSON response is complete and valid.",
            "Inside JSON string values, do not include double quote characters copied from abbreviations or source text; replace them with apostrophes or omit the punctuation so JSON remains valid.",
            "Return strict JSON only with key subjects, no markdown.",
        ],
        "response_schema": {
            "subjects": [
                {
                    "subject_root_label_he": "open Hebrew candidate label, not from a closed list",
                    "subject_child_label_he": "open Hebrew candidate label, not from a closed list",
                    "subject_object_he": "concrete object/topic instance, not a taxonomy label",
                    "subject_details_he": "specific details such as person, role, place, date, committee, legal basis",
                    "subject_type_evidence_he": "short exact phrase from raw_evidence_text supporting root/child",
                        "artifact_role": "substantive_subject|background_context|document_date|reference_number|protocol_header|meeting_header|contact_info|signature_footer|salutation|empty_text",
                        "topic_relevance": "topic_bearing|not_topic_bearing|topic_suspect",
                        "subject_summary_he": "short Hebrew summary of this subject",
                    "what_text_is_about_he": "plain-language Hebrew explanation of what this text is about",
                    "is_decision": "boolean",
                    "decision": {
                        "decision_label_he": "open Hebrew decision candidate label|null",
                        "decision_summary_he": "string|null",
                        "source_quote_he": "exact quote from raw_evidence_text|null",
                        "confidence": "number 0..1",
                        "limitations": ["string"],
                    },
                    "confidence": "number 0..1",
                    "rationale_he": "string",
                }
            ],
            "overall_summary_he": "string",
            "rationale_he": "string",
        },
        "known_topic": {
            "semantic_node_id": artifact.semantic_node_id,
            "topic_label_he": artifact.topic_label_he,
            "root_topic_id": artifact.root_topic_id,
            "root_label_he": artifact.root_label_he,
            "child_topic_id": artifact.child_topic_id,
            "child_label_he": artifact.child_label_he,
        },
        "topic_aware_examples_not_a_codelist": topic_aware_subject_examples(artifact),
        "artifact": {
            "artifact_id": artifact.artifact_id,
            "source_kind": artifact.source_kind,
            "artifact_kind": artifact.artifact_kind,
            "source_title": artifact.source_title,
            "page_span": {"start": artifact.start_page, "end": artifact.end_page},
            "header_path": artifact.header_path,
        },
        "raw_evidence_text": artifact.real_text[: max(500, int(max_text_chars))],
        "decision_context_text": (artifact.decision_context_text or artifact.real_text)[: max(500, int(max_text_chars))],
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


def topic_aware_subject_examples(artifact: TopicDecisionArtifact) -> list[dict[str, Any]]:
    topic = compact_text(artifact.topic_label_he) or "הנושא הידוע"
    text_norm = normalize_for_search(artifact.real_text)
    examples = [
        {
            "known_topic": topic,
            "text_pattern": "מכתב רקע, הסבר מקצועי, או הצדקה ללא החלטה",
            "subject_root_label_he": "הגדרה",
            "subject_child_label_he": "הגדרת הקשר",
            "subject_object_he": topic,
            "subject_details_he": "הסבר או הצדקה בתוך הנושא הידוע",
            "subject_type_evidence_he": "phrase showing explanation/context",
            "artifact_role": "background_context",
            "topic_relevance": "topic_bearing",
            "is_decision": False,
            "why": "הטקסט מסביר את הנושא אבל לא מתאר פעולה עירונית שאושרה או נדחתה.",
        },
        {
            "known_topic": topic,
            "text_pattern": "אבקש/אודה לאישור/מבקשים להעלות לדיון",
            "subject_root_label_he": "בקשה",
            "subject_child_label_he": "בקשת אישור",
            "subject_object_he": topic,
            "subject_details_he": "מה מבקשים לאשר, לממן או לדון בו",
            "subject_type_evidence_he": "אבקש / אודה לאישור / מבקשים",
            "artifact_role": "substantive_subject",
            "topic_relevance": "topic_bearing",
            "is_decision": False,
            "why": "בקשה או הצעה אינן החלטה עד שמופיע בטקסט אישור, דחייה או פעולה אחרת.",
        },
        {
            "known_topic": topic,
            "text_pattern": "המועצה מאשרת/הוחלט/נדחה/הועבר לטיפול",
            "subject_root_label_he": "אישור",
            "subject_child_label_he": "אישור השתתפות",
            "subject_object_he": topic,
            "subject_details_he": "הדבר שאושר והגורם המאשר, אם מופיעים בטקסט",
            "subject_type_evidence_he": "מאשרים / אושר / הוחלט לאשר",
            "artifact_role": "substantive_subject",
            "topic_relevance": "topic_bearing",
            "is_decision": True,
            "why": "הטקסט עצמו כולל תוצאת פעולה עירונית שניתן לצטט מהמקור.",
        },
        {
            "known_topic": topic,
            "text_pattern": "דיון, שאלות, חילופי דברים או עדכון סטטוס ללא הכרעה",
            "subject_root_label_he": "דיון",
            "subject_child_label_he": "דיון ללא הכרעה",
            "subject_object_he": topic,
            "subject_details_he": "העניין שנדון או עודכן ללא תוצאת החלטה",
            "subject_type_evidence_he": "דיון / שאלות / עדכון",
            "artifact_role": "substantive_subject",
            "topic_relevance": "topic_bearing",
            "is_decision": False,
            "why": "דיון או עדכון יכולים להיות נושא חשוב גם בלי החלטה פורמלית.",
        },
    ]
    if text_has_any(text_norm, ("הצעה לסדר יום", "הצעה לסדר")):
        examples.insert(
            0,
            {
                "known_topic": topic,
                "text_pattern": "הצעה לסדר יום או הצעה להעלות נושא לדיון",
                "subject_root_label_he": "הצעה",
                "subject_child_label_he": "הצעה לסדר יום",
                "subject_object_he": topic,
                "subject_details_he": "הנושא שהוצע להעלות לסדר היום והפעולות המבוקשות לדיון",
                "subject_type_evidence_he": "הצעה לסדר יום",
                "artifact_role": "substantive_subject",
                "topic_relevance": "topic_bearing",
                "is_decision": False,
                "why": "הצעה לסדר יום היא פעולה/כוונה להעלות נושא לדיון, לא בקשת אישור ולא החלטה.",
            },
        )
    if text_has_any(text_norm, CONDITION_SCOPE_CUES):
        examples.insert(
            0,
            {
                "known_topic": topic,
                "text_pattern": "רשימת תנאים, היקף תחולה, כללים או מסגרת פעולה ללא בקשה וללא אישור",
                "subject_root_label_he": "הגדרה",
                "subject_child_label_he": "הגדרת תנאים",
                "subject_object_he": "האובייקט שעליו חלים התנאים, למשל חוזים/הזמנות/סמכות חתימה",
                "subject_details_he": "התנאים, המסגרת המשפטית, סוגי ההתקשרויות, סכומים או חריגים שמופיעים בטקסט",
                "subject_type_evidence_he": "מחוזה חתום / מכרז / ועדת רכש / הצעות מחיר / הסכום ללא הגבלה",
                "artifact_role": "substantive_subject",
                "topic_relevance": "topic_bearing",
                "is_decision": False,
                "why": "הטקסט מגדיר תנאים או היקף תחולה; הוא לא מבקש אישור ולא מוכיח החלטה חדשה.",
            },
        )
    return examples


def run_topic_subject_research(
    session: Session,
    *,
    config: TopicSubjectResearchConfig,
    client: TopicSubjectClient | None = None,
) -> TopicSubjectResearchResult:
    started = time.perf_counter()
    client = client or OllamaTopicSubjectClient()
    topic_tree = load_topic_tree_for_municipality(session, municipality_slug=config.municipality_slug)
    load_limit = (max(0, int(config.offset)) + int(config.limit)) if config.limit is not None else None
    artifacts = load_accepted_topic_artifacts(session, municipality_slug=config.municipality_slug, limit=load_limit)
    if config.offset:
        artifacts = artifacts[max(0, int(config.offset)) :]
    run_row: TopicSubjectRun | None = None
    if config.write:
        run_row = TopicSubjectRun(
            municipality_slug=config.municipality_slug,
            model_provider="ollama",
            model_name=config.model_name,
            status="running",
            write_mode=True,
            source_artifact_count=len(artifacts),
            metadata_json=json.dumps({"provenance": PROVENANCE, "offset": config.offset, "limit": config.limit}, ensure_ascii=False),
        )
        session.add(run_row)
        session.flush()

    extracted: list[ExtractedTopicSubject] = []
    quality_rows: list[TopicSubjectQualityRow] = []
    for artifact in artifacts:
        model_payload = preclassify_trivial_subject_payload(artifact) or client.extract(artifact=artifact, config=config)
        artifact_subjects, quality = process_topic_subject_payload(artifact=artifact, model_payload=model_payload)
        extracted.extend(artifact_subjects)
        quality_rows.append(quality)

    apply_event_grouping(artifacts=artifacts, extracted=extracted, quality_rows=quality_rows)

    if run_row is not None:
        extracted_by_artifact: dict[str, list[ExtractedTopicSubject]] = {}
        for item in extracted:
            extracted_by_artifact.setdefault(item.artifact.artifact_id, []).append(item)
        for quality in quality_rows:
            persist_topic_subject_artifact_result(
                session=session,
                run=run_row,
                artifact_subjects=extracted_by_artifact.get(quality.artifact_id, []),
                quality_row=quality,
            )

    if run_row is not None:
        run_row.status = "completed"
        run_row.extraction_count = len(extracted)
        run_row.candidate_subject_count = sum(1 for item in extracted if is_countable_subject_event(item))
        run_row.candidate_decision_count = sum(1 for item in extracted if is_countable_subject_event(item) and bool(item.subject_payload.get("is_decision")))
        run_row.failed_count = sum(1 for row in quality_rows if row.status in {"failed", "partial", "model_error", "no_subject"})
        run_row.finished_at = datetime.utcnow()
        session.flush()

    result = TopicSubjectResearchResult(
        run_id=int(run_row.id) if run_row is not None else None,
        topic_tree=topic_tree,
        artifacts=artifacts,
        extracted_subjects=extracted,
        quality_rows=quality_rows,
        elapsed_seconds=round(time.perf_counter() - started, 3),
    )
    if config.output_dir is not None:
        result.output_paths.update(write_topic_subject_outputs(output_dir=config.output_dir, result=result))
    return result


def process_topic_subject_payload(*, artifact: TopicDecisionArtifact, model_payload: dict[str, Any]) -> tuple[list[ExtractedTopicSubject], TopicSubjectQualityRow]:
    if model_payload.get("error_code"):
        quality = TopicSubjectQualityRow(
            artifact_id=artifact.artifact_id,
            semantic_node_id=artifact.semantic_node_id,
            topic=artifact.topic_label_he,
            real_text=artifact.real_text,
            what_text_is_about="",
            artifact_role="model_error",
            topic_relevance="unknown",
            subject_root_by_dicta="",
            subject_child_by_dicta="",
            subject_object_by_dicta="",
            subject_details_by_dicta="",
            decision_by_dicta="",
            my_judgment="model_error",
            ground_truth="לא ניתן לשפוט כי קריאת Dicta נכשלה.",
            reason_for_failure=str(model_payload.get("error_text") or model_payload.get("error_code") or "model_error"),
            status="model_error",
            metadata={"model_payload": compact_subject_payload(model_payload)},
        )
        return [], quality

    subjects_raw = model_payload.get("subjects") if isinstance(model_payload.get("subjects"), list) else []
    extracted: list[ExtractedTopicSubject] = []
    for idx, subject_raw in enumerate(subjects_raw):
        if not isinstance(subject_raw, dict):
            continue
        subject_payload, failures = normalize_subject_payload(subject_raw)
        apply_subject_validation(artifact=artifact, subject_payload=subject_payload, failures=failures)
        failures = unique_strings(failures)
        validation_status = subject_validation_status(subject_payload=subject_payload, failures=failures)
        if validation_status == "decision_rejected":
            reject_decision_but_keep_subject(subject_payload=subject_payload, reasons=failures)
        source_quote = str((subject_payload.get("decision") or {}).get("source_quote_he") or "").strip()
        evidence_refs = [evidence_ref_for_decision(artifact=artifact, source_quote=source_quote)] if source_quote else []
        resident_links = [{"label_he": "מקור", "icon": "document-link", "evidence_ref": evidence_refs[0]["id"]}] if evidence_refs else []
        judge_payload = judge_subject(subject_payload=subject_payload, failures=failures)
        extracted.append(
            ExtractedTopicSubject(
                artifact=artifact,
                subject_index=idx,
                subject_payload=subject_payload,
                validation_status=validation_status,
                failure_reasons=failures,
                judge_payload=judge_payload,
                evidence_refs=evidence_refs,
                resident_evidence_links=resident_links,
            )
        )

    quality = quality_row_for_artifact(artifact=artifact, extracted=extracted, model_payload=model_payload)
    return extracted, quality


def preclassify_trivial_subject_payload(artifact: TopicDecisionArtifact) -> dict[str, Any] | None:
    text = compact_text(artifact.real_text)
    if not text:
        return non_subject_payload(artifact=artifact, artifact_role="empty_text", about="הארטיפקט אינו מכיל טקסט שמאפשר זיהוי נושא.")
    text_norm = normalize_for_search(text)
    if is_date_only_text(text):
        return non_subject_payload(artifact=artifact, artifact_role="document_date", about="הארטיפקט מכיל תאריך בלבד, ללא תוכן מהותי.")
    if is_reference_only_text(text):
        return non_subject_payload(artifact=artifact, artifact_role="reference_number", about="הארטיפקט מכיל מספר אסמכתא או הנחיית תשובה בלבד.")
    header_role = protocol_or_meeting_header_role(text_norm)
    if header_role:
        return non_subject_payload(artifact=artifact, artifact_role=header_role, about="הארטיפקט מכיל כותרת פרוטוקול/ישיבה או רשימת משתתפים בלבד, ללא נושא עירוני עצמאי.")
    if is_contact_info_text(text_norm):
        return non_subject_payload(artifact=artifact, artifact_role="contact_info", about="הארטיפקט מכיל פרטי קשר וכתובת בלבד, ללא נושא עירוני עצמאי.")
    if is_signature_or_footer_text(text_norm):
        return non_subject_payload(artifact=artifact, artifact_role="signature_footer", about="הארטיפקט הוא חתימה או סיום מכתב, ללא בקשה או החלטה מהותית.")
    return None


def non_subject_payload(*, artifact: TopicDecisionArtifact, artifact_role: str, about: str) -> dict[str, Any]:
    return {
        "subjects": [],
        "overall_summary_he": about,
        "rationale_he": "local_preclassifier",
        "preclassified": True,
        "non_subject": True,
        "artifact_role": artifact_role,
        "topic_relevance": "not_topic_bearing",
        "preclassifier_topic": artifact.topic_label_he,
    }


def is_date_only_text(text: str) -> bool:
    value = compact_text(text)
    if len(value) > 40:
        return False
    if re.fullmatch(r"[\d./\- ]{6,20}", value):
        return True
    has_hebrew_date_word = any(word in value for word in ("תשרי", "חשוון", "כסלו", "טבת", "שבט", "אדר", "ניסן", "אייר", "סיון", "תמוז", "אב", "אלול", "תשפ"))
    return bool(has_hebrew_date_word and re.search(r"[א-ת]\"?[א-ת]?", value))


def is_reference_only_text(text: str) -> bool:
    value = compact_text(text)
    if len(value) > 90:
        return False
    return bool(re.search(r"בתשובתך\s+אנא\s+ציין", value) or re.search(r"[א-ת]{1,4}\d{1,3}-\d{1,3}-\d{1,3}-\d{2,4}-\d{4}", value))


def protocol_or_meeting_header_role(text_norm: str) -> str:
    if len(text_norm) > 900:
        return ""
    if text_has_any(text_norm, REQUEST_ONLY_CUES + STRONG_APPROVAL_ACTION_CUES):
        return ""
    cue_hits = sum(1 for cue in PROTOCOL_HEADER_CUES if normalize_for_search(cue) in text_norm)
    has_protocol = normalize_for_search("פרוטוקול") in text_norm
    has_participants = any(normalize_for_search(cue) in text_norm for cue in ("משתתפים", "נוכחים", "חבר ועדה", "יו\"ר"))
    has_meeting = any(normalize_for_search(cue) in text_norm for cue in ("מישיבת", "ישיבת", "שהתקיימה בתאריך"))
    meeting_metadata_hits = sum(1 for cue in MEETING_METADATA_CUES if normalize_for_search(cue) in text_norm)
    participant_hits = sum(1 for cue in PARTICIPANT_LIST_CUES if normalize_for_search(cue) in text_norm)
    compact_no_space = re.sub(r"\s+", "", text_norm)
    if has_protocol and (has_participants or cue_hits >= 2 or len(text_norm) <= 90):
        return "protocol_header"
    if has_meeting and has_participants:
        return "meeting_header"
    if cue_hits >= 3 and has_participants:
        return "meeting_header"
    if meeting_metadata_hits >= 2:
        return "meeting_header"
    if normalize_for_search("נושא הדיון") in text_norm and normalize_for_search("ישיבת מועצה") in text_norm:
        return "meeting_header"
    if ("נושאהדיון" in compact_no_space or "נ:ושאהדיון" in compact_no_space) and normalize_for_search("ישיבת מועצה") in text_norm:
        return "meeting_header"
    if participant_hits >= 3 and (normalize_for_search("השתתפו") in text_norm or text_norm.count(";") >= 2):
        return "meeting_header"
    if text_norm.count(";") >= 2 and len(re.findall(normalize_for_search("חבר מועצה"), text_norm)) >= 2:
        return "meeting_header"
    return ""


def is_signature_or_footer_text(text_norm: str) -> bool:
    if len(text_norm) > 140:
        return False
    if is_condition_scope_text(text_norm):
        return False
    if text_has_any(text_norm, REQUEST_ONLY_CUES + STRONG_APPROVAL_ACTION_CUES):
        return False
    closing_markers = ("בכבוד רב", "בברכה", "בברכת")
    normalized_markers = [normalize_for_search(cue) for cue in closing_markers]
    for marker in normalized_markers:
        marker_index = text_norm.find(marker)
        if marker_index < 0:
            continue
        prefix = compact_text(text_norm[:marker_index])
        if prefix and len(prefix) > 24:
            return False
        return True
    signature_cues = ("גזבר העירייה", "לשכת הגזבר", "מינהל הכספים", "חתימה")
    cue_hits = sum(1 for cue in signature_cues if normalize_for_search(cue) in text_norm)
    return bool(cue_hits >= 2 and len(text_norm) <= 90)


def is_contact_info_text(text_norm: str) -> bool:
    if len(text_norm) > 160:
        return False
    cue_hits = sum(1 for cue in CONTACT_INFO_CUES if normalize_for_search(cue) in text_norm)
    return cue_hits >= 2


def normalize_subject_payload(row: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    root_label = compact_text(row.get("subject_root_label_he"))[:180]
    child_label = compact_text(row.get("subject_child_label_he"))[:180]
    if not root_label:
        failures.append("missing_subject_root_label")
        root_label = "נושא לא מזוהה"
    if not child_label:
        child_label = "כללי"
    decision_raw = row.get("decision") if isinstance(row.get("decision"), dict) else {}
    is_decision = parse_bool(row.get("is_decision"))
    decision_label = compact_text(decision_raw.get("decision_label_he"))[:180]
    decision = {
        "decision_label_he": decision_label,
        "decision_label_norm": normalize_for_search(decision_label)[:500] if decision_label else None,
        "decision_summary_he": compact_text(decision_raw.get("decision_summary_he"))[:500],
        "source_quote_he": compact_text(decision_raw.get("source_quote_he"))[:1000],
        "confidence": clamp_float(decision_raw.get("confidence"), default=0.0),
        "limitations": [compact_text(item) for item in decision_raw.get("limitations") or [] if compact_text(item)],
    }
    return (
        {
            "subject_root_label_he": root_label,
            "subject_root_label_norm": normalize_for_search(root_label)[:500],
            "subject_child_label_he": child_label,
            "subject_child_label_norm": normalize_for_search(child_label)[:500],
            "subject_object_he": compact_text(row.get("subject_object_he"))[:500],
            "subject_details_he": compact_text(row.get("subject_details_he"))[:1000],
            "subject_type_evidence_he": compact_text(row.get("subject_type_evidence_he"))[:500],
            "subject_summary_he": compact_text(row.get("subject_summary_he"))[:700],
            "what_text_is_about_he": compact_text(row.get("what_text_is_about_he"))[:700],
            "subject_status": "candidate",
            "artifact_role": compact_text(row.get("artifact_role"))[:64] or "substantive_subject",
            "topic_relevance": compact_text(row.get("topic_relevance"))[:64] or "topic_bearing",
            "is_decision": is_decision,
            "decision": decision if is_decision or any(decision.values()) else None,
            "confidence": clamp_float(row.get("confidence"), default=0.0),
            "rationale_he": compact_text(row.get("rationale_he"))[:700],
            "raw_model_payload": row,
        },
        failures,
    )


def apply_subject_validation(*, artifact: TopicDecisionArtifact, subject_payload: dict[str, Any], failures: list[str]) -> None:
    canonicalize_subject_taxonomy_fields(artifact=artifact, subject_payload=subject_payload)
    if not subject_payload.get("subject_root_label_norm"):
        failures.append("empty_subject_root_norm")
    if not subject_payload.get("subject_child_label_norm"):
        failures.append("empty_subject_child_norm")

    if not bool(subject_payload.get("is_decision")):
        return

    decision = subject_payload.get("decision") if isinstance(subject_payload.get("decision"), dict) else {}
    decision_label = compact_text(decision.get("decision_label_he"))
    source_quote = compact_text(decision.get("source_quote_he"))
    quote_norm = normalize_for_search(source_quote)
    if not decision_label:
        failures.append("missing_decision_label_candidate")
    if not source_quote:
        failures.append("missing_decision_source_quote")
        return
    if not quote_supported_by_text(quote=source_quote, text=artifact.real_text):
        failures.append("decision_source_quote_not_grounded_in_raw_evidence_text")
    if text_has_any(quote_norm, REQUEST_ONLY_CUES) and not text_has_any(quote_norm, DECISION_ACTION_CUES):
        failures.append("request_or_proposal_without_decision_outcome")
    if not text_has_any(quote_norm, DECISION_ACTION_CUES):
        failures.append("decision_action_not_grounded_in_source_quote")


def canonicalize_subject_taxonomy_fields(*, artifact: TopicDecisionArtifact, subject_payload: dict[str, Any]) -> None:
    root = compact_text(subject_payload.get("subject_root_label_he"))
    child = compact_text(subject_payload.get("subject_child_label_he"))
    object_text = compact_text(subject_payload.get("subject_object_he"))
    details = compact_text(subject_payload.get("subject_details_he"))
    evidence = compact_text(subject_payload.get("subject_type_evidence_he"))
    raw_text = compact_text(artifact.real_text)
    raw_norm = normalize_for_search(raw_text)

    original_root = remove_topic_from_label(label=root, artifact=artifact)
    original_child = remove_topic_from_label(label=child, artifact=artifact)
    if is_domain_noun_label(original_root) or is_domain_noun_label(original_child):
        object_text = merge_text_parts([object_text, original_root if is_domain_noun_label(original_root) else "", original_child if is_domain_noun_label(original_child) else ""])

    if is_condition_scope_text(raw_norm) and not text_has_any(raw_norm, REQUEST_ONLY_CUES) and not text_has_any(raw_norm, STRONG_APPROVAL_ACTION_CUES):
        root = "הגדרה"
        child = "הגדרת תנאים"
        object_text = object_text or condition_scope_object_from_text(raw_norm) or compact_text(artifact.topic_label_he)
        details = details or raw_text[:500]
        evidence = evidence or first_matching_cue(raw_text, CONDITION_SCOPE_CUES)
    else:
        root = normalize_root_subject_label(original_root, context_norm=raw_norm)
        child = normalize_child_subject_label(original_child, root=root, context_norm=raw_norm)

    if not object_text:
        object_text = compact_text(artifact.topic_label_he)
    if not details:
        details = compact_text(subject_payload.get("what_text_is_about_he") or subject_payload.get("subject_summary_he"))

    subject_payload["subject_root_label_he"] = root or "נושא"
    subject_payload["subject_root_label_norm"] = normalize_for_search(subject_payload["subject_root_label_he"])[:500]
    subject_payload["subject_child_label_he"] = child or "כללי"
    subject_payload["subject_child_label_norm"] = normalize_for_search(subject_payload["subject_child_label_he"])[:500]
    subject_payload["subject_object_he"] = object_text[:500]
    subject_payload["subject_details_he"] = details[:1000]
    subject_payload["subject_type_evidence_he"] = evidence[:500]


def remove_topic_from_label(*, label: str, artifact: TopicDecisionArtifact) -> str:
    value = compact_text(label)
    for topic_label in unique_strings([artifact.topic_label_he, artifact.root_label_he or "", artifact.child_label_he or ""]):
        topic = compact_text(topic_label)
        if not topic:
            continue
        value = value.replace(topic, "")
    value = re.sub(r"\s*[-–—:/]+\s*", " ", value)
    return compact_text(value)


def normalize_root_subject_label(label: str, *, context_norm: str = "") -> str:
    value = compact_text(label)
    value_norm = normalize_for_search(value)
    if text_has_any(context_norm + " " + value_norm, ("הצעה לסדר יום", "הצעה לסדר")):
        return "הצעה"
    if is_condition_scope_text(context_norm) and not text_has_any(context_norm, REQUEST_ONLY_CUES) and not text_has_any(context_norm, STRONG_APPROVAL_ACTION_CUES):
        return "הגדרה"
    if text_has_any(context_norm, REQUEST_ONLY_CUES) and not text_has_any(context_norm, DECISION_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES):
        return "בקשה"
    root_cues = [
        ("אישור", ("אישור", "אישר", "מאשר", "הוחלט לאשר")),
        ("בקשה", ("בקשה", "בקשת", "מבקש", "אודה לאישור", "אבקש")),
        ("הגדרה", ("הגדרה", "הגדרת", "תנאים", "תחולה", "היקף")),
        ("קביעה", ("קביעה", "קביעת")),
        ("דיון", ("דיון", "דיוני", "דיווח")),
        ("הצעה", ("הצעה", "הצעות")),
        ("עדכון", ("עדכון", "סטטוס")),
        ("נושא לא מהותי", ("מטא מסמך", "אסמכתא", "תאריך", "כותרת", "מסמך", "מכתב", "חתימה")),
    ]
    for canonical, cues in root_cues:
        if any(normalize_for_search(cue) in value_norm for cue in cues):
            return canonical
    words = value.split()
    return " ".join(words[:2]) if words else ""


def normalize_child_subject_label(label: str, *, root: str, context_norm: str = "") -> str:
    value = compact_text(label)
    value_norm = normalize_for_search(value)
    if root == "בקשה" and text_has_any(context_norm + " " + value_norm, ("האצלת סמכויות", "האצלת סמכות")):
        return "בקשה להאצלת סמכויות"
    if root == "בקשה" and has_approval_request_shape(context_norm + " " + value_norm):
        return "בקשת אישור"
    if root == "בקשה" and has_inquiry_request_shape(context_norm + " " + value_norm):
        return "בקשת מידע"
    if root == "הצעה" and text_has_any(context_norm + " " + value_norm, ("הצעה לסדר יום", "הצעה לסדר")):
        return "הצעה לסדר יום"
    if root == "הגדרה" and (is_condition_scope_text(context_norm) or text_has_any(context_norm, DETAIL_CONDITION_CUES)):
        return "הגדרת תנאים"
    child_patterns = [
        ("אישור השתתפות", ("אישור השתתפות", "השתתפות")),
        ("בקשת אישור", ("בקשת אישור", "בקשה לאישור")),
        ("הצעה לסדר יום", ("הצעה לסדר יום", "הצעה לסדר")),
        ("הגדרת תנאים", ("תנאים", "הזמנות", "חוזה", "מכרז", "ועדת רכש", "הצעות מחיר", "תמיכות")),
        ("קביעת היקף", ("היקף", "סכום", "ללא הגבלה", "עד")),
        ("הצדקה מקצועית", ("רקע מקצועי", "רקע והסבר", "הצדקה", "רציונל")),
        ("דיון ללא הכרעה", ("דיון ללא הכרעה", "ללא הכרעה")),
    ]
    for canonical, cues in child_patterns:
        if any(normalize_for_search(cue) in value_norm for cue in cues):
            return canonical
    words = value.split()
    if len(words) > 3:
        return " ".join(words[:3])
    if not value and root:
        return root
    return value


def is_condition_scope_text(text_norm: str) -> bool:
    return text_has_any(text_norm, CONDITION_SCOPE_CUES)


def has_approval_request_shape(text_norm: str) -> bool:
    return text_has_any(text_norm, ("אודה לאישור", "אודה לאשר", "מבקש לאשר", "מבקשת לאשר", "מבקשים לאשר", "בקשת אישור", "לאישור מועצת"))


def has_inquiry_request_shape(text_norm: str) -> bool:
    if has_approval_request_shape(text_norm):
        return False
    return text_has_any(text_norm, (*QUESTION_DETAIL_CUES, "שאילתה", "שאילתא", "אבקש לדעת", "בקשת מידע"))


def is_domain_noun_label(label: str) -> bool:
    return text_has_any(label, DOMAIN_NOUN_ROOT_CUES)


def merge_text_parts(parts: list[str]) -> str:
    return "; ".join(unique_strings([compact_text(part) for part in parts if compact_text(part)]))


def condition_scope_object_from_text(text_norm: str) -> str:
    objects: list[str] = []
    if any(normalize_for_search(cue) in text_norm for cue in ("חוזה", "מכרז", "הזמנות", "הצעות מחיר")):
        objects.append("חוזים, הזמנות ומכרזים")
    if any(normalize_for_search(cue) in text_norm for cue in ("הקצבות", "תמיכות")):
        objects.append("הקצבות ותמיכות")
    if any(normalize_for_search(cue) in text_norm for cue in ("סמכות חתימה", "חתימה")):
        objects.append("סמכות חתימה")
    return merge_text_parts(objects)


def first_matching_cue(text: str, cues: tuple[str, ...]) -> str:
    text_norm = normalize_for_search(text)
    for cue in cues:
        cue_norm = normalize_for_search(cue)
        if cue_norm and cue_norm in text_norm:
            return cue
    return ""


def subject_validation_status(*, subject_payload: dict[str, Any], failures: list[str]) -> str:
    if not failures:
        return "accepted"
    subject_failure_prefixes = ("missing_subject_root_label", "empty_subject_root_norm", "empty_subject_child_norm")
    if any(reason.startswith(subject_failure_prefixes) for reason in failures):
        return "failed"
    if bool(subject_payload.get("is_decision")):
        return "decision_rejected"
    return "failed"


def reject_decision_but_keep_subject(*, subject_payload: dict[str, Any], reasons: list[str]) -> None:
    decision = subject_payload.get("decision") if isinstance(subject_payload.get("decision"), dict) else {}
    subject_payload["rejected_decision"] = {
        "decision_label_he": decision.get("decision_label_he"),
        "decision_summary_he": decision.get("decision_summary_he"),
        "source_quote_he": decision.get("source_quote_he"),
        "reasons": reasons,
    }
    subject_payload["is_decision"] = False
    subject_payload["decision"] = None
    subject_payload["subject_status"] = "candidate_decision_rejected"


def judge_subject(*, subject_payload: dict[str, Any], failures: list[str]) -> dict[str, Any]:
    about = compact_text(subject_payload.get("what_text_is_about_he") or subject_payload.get("subject_summary_he"))
    rejected_decision = subject_payload.get("rejected_decision") if isinstance(subject_payload.get("rejected_decision"), dict) else None
    if rejected_decision is not None:
        reasons = "; ".join(unique_strings(rejected_decision.get("reasons") or failures))
        return {
            "judgement": "subject_candidate_with_rejected_decision",
            "ground_truth": f"זיהיתי מועמד נושא פתוח, אך דחיתי את מועמד ההחלטה כי אינו מעוגן מספיק בטקסט הנוכחי. {about}",
            "reason_for_failure": reasons,
        }
    if failures:
        return {
            "judgement": "failed",
            "ground_truth": "נוצרה תווית נושא, אך החלטה או מקור הציטוט דורשים בדיקה.",
            "reason_for_failure": "; ".join(failures),
        }
    if bool(subject_payload.get("is_decision")):
        decision_label = str(((subject_payload.get("decision") or {}).get("decision_label_he")) or "החלטה")
        return {
            "judgement": "decision_candidate",
            "ground_truth": f"זיהיתי מועמד החלטה מסוג '{decision_label}' בתוך נושא פתוח. {about}",
            "reason_for_failure": "",
        }
    return {
        "judgement": "subject_candidate",
        "ground_truth": f"זיהיתי מועמד נושא פתוח שאינו החלטה. {about}",
        "reason_for_failure": "",
    }


def quality_row_for_artifact(*, artifact: TopicDecisionArtifact, extracted: list[ExtractedTopicSubject], model_payload: dict[str, Any]) -> TopicSubjectQualityRow:
    if not extracted:
        if bool(model_payload.get("non_subject")):
            about = compact_text(model_payload.get("overall_summary_he"))
            return TopicSubjectQualityRow(
                artifact_id=artifact.artifact_id,
                semantic_node_id=artifact.semantic_node_id,
                topic=artifact.topic_label_he,
                real_text=artifact.real_text,
                what_text_is_about=about,
                artifact_role=compact_text(model_payload.get("artifact_role")) or "document_fragment",
                topic_relevance=compact_text(model_payload.get("topic_relevance")) or "not_topic_bearing",
                subject_root_by_dicta="",
                subject_child_by_dicta="",
                subject_object_by_dicta="",
                subject_details_by_dicta="",
                decision_by_dicta="",
                my_judgment="non_subject_document_fragment",
                ground_truth=f"הקטע הוא חלק מבנה/מטא-מידע של מסמך ולא נושא עירוני עצמאי. {about}",
                reason_for_failure="",
                status="non_subject",
                metadata={"model_payload": compact_subject_payload(model_payload)},
            )
        return TopicSubjectQualityRow(
            artifact_id=artifact.artifact_id,
            semantic_node_id=artifact.semantic_node_id,
            topic=artifact.topic_label_he,
            real_text=artifact.real_text,
            what_text_is_about=compact_text(model_payload.get("overall_summary_he")),
            artifact_role=compact_text(model_payload.get("artifact_role")) or "unknown",
            topic_relevance=compact_text(model_payload.get("topic_relevance")) or "unknown",
            subject_root_by_dicta="",
            subject_child_by_dicta="",
            subject_object_by_dicta="",
            subject_details_by_dicta="",
            decision_by_dicta="",
            my_judgment="no_subject",
            ground_truth="לא זוהה מועמד נושא בטקסט המצורף.",
            reason_for_failure="Dicta לא החזירה נושא",
            status="no_subject",
            metadata={"model_payload": compact_subject_payload(model_payload)},
        )

    roots = " | ".join(unique_strings([str(item.subject_payload.get("subject_root_label_he") or "") for item in extracted]))
    children = " | ".join(unique_strings([str(item.subject_payload.get("subject_child_label_he") or "") for item in extracted]))
    objects = " | ".join(unique_strings([str(item.subject_payload.get("subject_object_he") or "") for item in extracted]))
    details = " | ".join(unique_strings([str(item.subject_payload.get("subject_details_he") or "") for item in extracted]))
    artifact_roles = " | ".join(unique_strings([str(item.subject_payload.get("artifact_role") or "substantive_subject") for item in extracted]))
    topic_relevance = " | ".join(unique_strings([str(item.subject_payload.get("topic_relevance") or "topic_bearing") for item in extracted]))
    about = " | ".join(unique_strings([str(item.subject_payload.get("what_text_is_about_he") or item.subject_payload.get("subject_summary_he") or "") for item in extracted]))
    decision_by_dicta = " | ".join(subject_decision_summary(item.subject_payload) for item in extracted if bool(item.subject_payload.get("is_decision")))
    rejected_decision_reasons = unique_strings(reason for item in extracted if item.validation_status == "decision_rejected" for reason in item.failure_reasons)
    failed = [item for item in extracted if item.validation_status == "failed"]
    nonfailed = [item for item in extracted if item.validation_status != "failed"]
    if nonfailed and not failed:
        has_decision = any(bool(item.subject_payload.get("is_decision")) for item in nonfailed)
        has_rejected_decision = any(item.validation_status == "decision_rejected" for item in nonfailed)
        status = "decision_candidate" if has_decision else ("subject_candidate_with_rejected_decision" if has_rejected_decision else "subject_candidate")
        my_judgment = status
        ground_truth = " | ".join(str(item.judge_payload.get("ground_truth") or "") for item in nonfailed)
        reason = "; ".join(rejected_decision_reasons)
    elif nonfailed and failed:
        status = "partial"
        my_judgment = "partial"
        ground_truth = "חלק ממועמדי הנושא תקינים וחלק דורשים בדיקה."
        reason = "; ".join(unique_strings(reason for item in failed for reason in item.failure_reasons))
    else:
        status = "failed"
        my_judgment = "failed"
        ground_truth = "לא ניתן לאשר את מועמד הנושא או ההחלטה מהטקסט המצורף."
        reason = "; ".join(unique_strings(reason for item in failed for reason in item.failure_reasons))
    return TopicSubjectQualityRow(
        artifact_id=artifact.artifact_id,
        semantic_node_id=artifact.semantic_node_id,
        topic=artifact.topic_label_he,
        real_text=artifact.real_text,
        what_text_is_about=about,
        artifact_role=artifact_roles,
        topic_relevance=topic_relevance,
        subject_root_by_dicta=roots,
        subject_child_by_dicta=children,
        subject_object_by_dicta=objects,
        subject_details_by_dicta=details,
        decision_by_dicta=decision_by_dicta,
        my_judgment=my_judgment,
        ground_truth=ground_truth,
        reason_for_failure=reason,
        status=status,
        metadata={"model_payload": compact_subject_payload(model_payload)},
    )


def apply_event_grouping(*, artifacts: list[TopicDecisionArtifact], extracted: list[ExtractedTopicSubject], quality_rows: list[TopicSubjectQualityRow]) -> None:
    extracted_by_artifact: dict[str, list[ExtractedTopicSubject]] = {}
    for item in extracted:
        extracted_by_artifact.setdefault(item.artifact.artifact_id, []).append(item)
    quality_by_artifact = {row.artifact_id: row for row in quality_rows}
    artifacts_by_docver: dict[int, list[TopicDecisionArtifact]] = {}
    for artifact in artifacts:
        artifacts_by_docver.setdefault(artifact.source_document_version_id, []).append(artifact)

    for doc_artifacts in artifacts_by_docver.values():
        doc_artifacts.sort(key=lambda item: (item.source_ordinal, item.artifact_id))
        anchors: list[dict[str, Any]] = []
        for artifact in doc_artifacts:
            quality = quality_by_artifact.get(artifact.artifact_id)
            if quality is None:
                continue
            artifact_subjects = extracted_by_artifact.get(artifact.artifact_id, [])
            subject_item = next((item for item in artifact_subjects if item.validation_status != "failed"), None)
            if subject_item is None:
                mark_non_subject_event_fields(quality=quality)
                continue

            if is_dependent_detail_candidate(artifact=artifact, item=subject_item):
                anchor, score, reason = find_compatible_anchor(artifact=artifact, item=subject_item, anchors=anchors)
                if anchor is not None and score >= 0.62:
                    link_detail_to_anchor(artifact=artifact, item=subject_item, quality=quality, anchor=anchor, score=score, reason=reason)
                    anchors.append(
                        {
                            "event_id": anchor["event_id"],
                            "event_topic": anchor["event_topic"],
                            "artifact": artifact,
                            "item": subject_item,
                            "quality": quality,
                            "bridge": True,
                            "anchor_item": anchor.get("anchor_item") or anchor["item"],
                            "anchor_artifact": anchor.get("anchor_artifact") or anchor["artifact"],
                            "anchor_status": anchor.get("anchor_status") or "validated_anchor",
                        }
                    )
                    continue
                mark_orphan_detail(artifact=artifact, item=subject_item, quality=quality, score=score, reason=reason)
                continue

            event_id = event_id_for_artifact(artifact)
            event_topic = resolve_anchor_event_topic(artifact=artifact, item=subject_item)
            anchor_status, anchor_reason = anchor_validation_status(artifact=artifact, item=subject_item)
            row_role = "action_anchor" if anchor_status == "validated_anchor" else "standalone_subject"
            if anchor_status == "suspect_anchor":
                row_role = "suspect_anchor"
            set_subject_event_fields(
                item=subject_item,
                quality=quality,
                event_id=event_id,
                event_topic=event_topic,
                row_role=row_role,
                anchor_status=anchor_status,
                link_confidence=1.0 if anchor_status != "suspect_anchor" else 0.0,
                link_reason="event_anchor" if anchor_status != "suspect_anchor" else anchor_reason,
            )
            if row_role == "action_anchor":
                anchors.append(
                    {
                        "event_id": event_id,
                        "event_topic": event_topic,
                        "artifact": artifact,
                        "item": subject_item,
                        "quality": quality,
                        "anchor_status": anchor_status,
                    }
                )


def mark_non_subject_event_fields(*, quality: TopicSubjectQualityRow) -> None:
    if quality.status == "non_subject":
        quality.row_role = "document_fragment"
        quality.anchor_status = "not_anchor"
        quality.event_topic = ""
        return
    quality.row_role = "no_subject"
    quality.anchor_status = "not_anchor"


def is_countable_subject_event(item: ExtractedTopicSubject) -> bool:
    if item.validation_status == "failed":
        return False
    if str(item.subject_payload.get("anchor_status") or "") == "suspect_anchor":
        return False
    return str(item.subject_payload.get("row_role") or "") not in {"dependent_detail", "document_fragment", "orphan_detail", "suspect_anchor"}


def is_action_anchor_subject(payload: dict[str, Any]) -> bool:
    root = compact_text(payload.get("subject_root_label_he"))
    return root in {"בקשה", "אישור", "דחייה", "הפניה", "דיון", "דיווח", "מינוי", "הקצאה", "קביעה"}


def anchor_validation_status(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject) -> tuple[str, str]:
    if not is_action_anchor_subject(item.subject_payload):
        return "not_anchor", "subject_root_is_not_action_anchor"
    text_norm = normalize_for_search(artifact.real_text)
    if protocol_or_meeting_header_role(text_norm):
        return "suspect_anchor", "raw_text_looks_like_protocol_or_meeting_header"
    root = compact_text(item.subject_payload.get("subject_root_label_he"))
    child = compact_text(item.subject_payload.get("subject_child_label_he"))
    evidence = compact_text(item.subject_payload.get("subject_type_evidence_he"))
    if evidence and not quote_supported_by_text(quote=evidence, text=artifact.real_text):
        return "suspect_anchor", "subject_type_evidence_not_grounded_in_raw_text"
    if not has_anchor_action_evidence(root=root, child=child, text_norm=text_norm, evidence=evidence, is_decision=bool(item.subject_payload.get("is_decision"))):
        return "suspect_anchor", "missing_action_evidence_in_raw_text"
    if not compact_text(item.subject_payload.get("what_text_is_about_he") or item.subject_payload.get("subject_summary_he") or item.subject_payload.get("subject_object_he")):
        return "suspect_anchor", "missing_subject_explanation_or_object"
    return "validated_anchor", "action_evidence_grounded_in_raw_text"


def has_anchor_action_evidence(*, root: str, child: str, text_norm: str, evidence: str, is_decision: bool) -> bool:
    evidence_norm = normalize_for_search(evidence)
    combined = compact_text(f"{text_norm} {evidence_norm}")
    if is_decision:
        return text_has_any(combined, DECISION_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES)
    if root == "בקשה":
        return text_has_any(combined, REQUEST_ONLY_CUES)
    if root == "הצעה":
        return text_has_any(combined, ("הצעה לסדר יום", "הצעה לסדר", "מציע", "מבקש להעלות לדיון"))
    if root == "אישור":
        return text_has_any(combined, APPROVAL_VERB_CUES + STRONG_APPROVAL_ACTION_CUES)
    if root == "דחייה":
        return text_has_any(combined, ("נדחה", "דחייה", "דחתה", "לא אושר"))
    if root == "הפניה":
        return text_has_any(combined, ("הועבר", "להעביר", "הפניה", "הופנה"))
    if root == "דיון":
        return text_has_any(combined, ("דיון", "נדון", "דנו", "להעלות לדיון"))
    if root == "דיווח":
        return text_has_any(combined, ("דיווח", "עדכון", "נמסר", "סקירה"))
    if root == "מינוי":
        return text_has_any(combined, ("מינוי", "מונה", "למנות"))
    if root == "הקצאה":
        return text_has_any(combined, ("הקצאה", "להקצות", "הוקצה"))
    if root == "קביעה":
        return text_has_any(combined, ("קביעה", "קביעת", "נקבע", "קבעה"))
    return False


def is_dependent_detail_candidate(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject) -> bool:
    payload = item.subject_payload
    if bool(payload.get("is_decision")):
        return False
    text_norm = normalize_for_search(artifact.real_text)
    if text_has_any(text_norm, REQUEST_ONLY_CUES) or text_has_any(text_norm, STRONG_APPROVAL_ACTION_CUES):
        return False
    root = compact_text(payload.get("subject_root_label_he"))
    child = compact_text(payload.get("subject_child_label_he"))
    detailish = root in {"הגדרה", "קביעה"} or child in {"הגדרת תנאים", "קביעת היקף"}
    if detailish and (is_condition_scope_text(text_norm) or has_detail_without_action_shape(artifact.real_text) or has_continuation_detail_shape(text_norm)):
        return True
    if is_action_anchor_subject(payload) and has_question_detail_shape(text_norm):
        return True
    action_like_without_grounding = is_action_anchor_subject(payload) and not has_anchor_action_evidence(
        root=root,
        child=child,
        text_norm=text_norm,
        evidence="",
        is_decision=False,
    )
    return bool(action_like_without_grounding and has_continuation_detail_shape(text_norm))


def has_detail_without_action_shape(text: str) -> bool:
    compact = compact_text(text)
    if len(compact) < 20:
        return False
    punctuation_or_list = any(mark in compact for mark in (":", ";", ","))
    no_sentence_action = not text_has_any(compact, REQUEST_ONLY_CUES + STRONG_APPROVAL_ACTION_CUES)
    return bool(punctuation_or_list and no_sentence_action)


def has_continuation_detail_shape(text_norm: str) -> bool:
    if text_has_any(text_norm, REQUEST_ONLY_CUES + STRONG_APPROVAL_ACTION_CUES):
        return False
    if text_has_any(text_norm, DETAIL_CONDITION_CUES):
        return True
    if has_vote_detail_shape(text_norm):
        return True
    if re.match(r"^\s*\d+[).]?\s*(?:₪|שח|ש\s*ח|לסטודנטים|שעות)", text_norm):
        return True
    return False


def has_question_detail_shape(text_norm: str) -> bool:
    if text_has_any(text_norm, REQUEST_ONLY_CUES + STRONG_APPROVAL_ACTION_CUES):
        return False
    return text_has_any(text_norm, QUESTION_DETAIL_CUES)


def has_vote_detail_shape(text_norm: str) -> bool:
    vote_hits = sum(1 for cue in VOTE_DETAIL_CUES if normalize_for_search(cue) in text_norm)
    return bool(vote_hits >= 1 and text_has_any(text_norm, ("שעות", "בברכה", "חברי", "ועדה")))


def find_compatible_anchor(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject, anchors: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float, str]:
    best_anchor: dict[str, Any] | None = None
    best_score = 0.0
    best_reasons: list[str] = []
    for anchor in reversed(anchors[-3:]):
        if str(anchor.get("anchor_status") or "") != "validated_anchor":
            continue
        anchor_artifact: TopicDecisionArtifact = anchor["artifact"]
        distance = abs(artifact.source_ordinal - anchor_artifact.source_ordinal)
        if distance < 1 or distance > 3:
            continue
        score, reasons = link_score(artifact=artifact, item=item, anchor=anchor, distance=distance)
        if score > best_score:
            best_anchor = anchor
            best_score = score
            best_reasons = reasons
    reason = "; ".join(best_reasons) or "no_validated_anchor_with_sufficient_confidence"
    return best_anchor, round(best_score, 3), reason


def link_score(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject, anchor: dict[str, Any], distance: int) -> tuple[float, list[str]]:
    anchor_artifact: TopicDecisionArtifact = anchor["artifact"]
    anchor_item: ExtractedTopicSubject = anchor["item"]
    score = 0.25
    reasons = ["same_document_version"]
    if distance == 1:
        score += 0.25
        reasons.append("adjacent_row")
    elif distance == 2:
        score += 0.12
        reasons.append("nearby_row")
        if is_action_anchor_subject(anchor_item.subject_payload) or bool(anchor.get("bridge")):
            score += 0.2
            reasons.append("previous_row_has_action_anchor")
    if is_condition_scope_text(normalize_for_search(artifact.real_text)):
        score += 0.15
        reasons.append("current_row_is_scope_or_conditions")
    if has_vote_detail_shape(normalize_for_search(artifact.real_text)) and not bool(anchor.get("bridge")) and is_action_anchor_subject(anchor_item.subject_payload):
        score += 0.22
        reasons.append("current_row_is_vote_tally_for_previous_action")
    shared = shared_context_token_count(
        merge_text_parts([anchor_artifact.real_text, anchor_item.subject_payload.get("subject_object_he"), anchor_item.subject_payload.get("subject_details_he")]),
        merge_text_parts([artifact.real_text, item.subject_payload.get("subject_object_he"), item.subject_payload.get("subject_details_he")]),
    )
    if shared >= 2:
        score += 0.15
        reasons.append("shared_object_or_detail_terms")
    elif shared == 1:
        score += 0.08
        reasons.append("one_shared_term")
    if compact_text(anchor_artifact.source_title) == compact_text(artifact.source_title):
        score += 0.08
        reasons.append("same_source_document_title")
    if text_has_any(normalize_for_search(artifact.real_text), REQUEST_ONLY_CUES + STRONG_APPROVAL_ACTION_CUES):
        score -= 0.4
        reasons.append("current_row_has_own_action")
    return max(0.0, min(1.0, score)), reasons


def shared_context_token_count(left: str, right: str) -> int:
    stop = {"של", "את", "על", "עם", "או", "כל", "לפי", "דין", "כדין", "מועצת", "העיר"}
    left_tokens = {token for token in normalize_for_search(left).split() if len(token) >= 4 and token not in stop}
    right_tokens = {token for token in normalize_for_search(right).split() if len(token) >= 4 and token not in stop}
    return len(left_tokens & right_tokens)


def link_detail_to_anchor(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject, quality: TopicSubjectQualityRow, anchor: dict[str, Any], score: float, reason: str) -> None:
    anchor_item: ExtractedTopicSubject = anchor.get("anchor_item") or anchor["item"]
    anchor_payload = anchor_item.subject_payload
    event_id = str(anchor["event_id"])
    event_topic = str(anchor["event_topic"])
    item.validation_status = "linked_detail"
    item.failure_reasons = []
    item.subject_payload["row_role"] = "dependent_detail"
    item.subject_payload["event_id"] = event_id
    item.subject_payload["linked_event_id"] = event_id
    item.subject_payload["event_topic_label_he"] = event_topic
    item.subject_payload["anchor_status"] = "linked_to_validated_anchor"
    item.subject_payload["link_confidence"] = score
    item.subject_payload["link_reason"] = reason
    item.subject_payload["event_subject_root_label_he"] = anchor_payload.get("subject_root_label_he")
    item.subject_payload["event_subject_child_label_he"] = anchor_payload.get("subject_child_label_he")

    quality.event_id = event_id
    quality.event_topic = event_topic
    quality.row_role = "dependent_detail"
    quality.anchor_status = "linked_to_validated_anchor"
    quality.linked_event_id = event_id
    quality.link_confidence = score
    quality.link_reason = reason
    quality.topic_relevance = "topic_bearing"
    quality.artifact_role = "dependent_detail"
    quality.subject_root_by_dicta = compact_text(anchor_payload.get("subject_root_label_he"))
    quality.subject_child_by_dicta = compact_text(anchor_payload.get("subject_child_label_he"))
    quality.subject_object_by_dicta = compact_text(anchor_payload.get("subject_object_he")) or quality.subject_object_by_dicta
    quality.my_judgment = "linked_dependent_detail"
    quality.ground_truth = f"הקטע אינו נושא עצמאי; הוא מוסיף פרטים לאירוע סמוך מסוג {quality.subject_root_by_dicta} / {quality.subject_child_by_dicta}."
    quality.reason_for_failure = ""
    quality.status = "linked_detail"


def mark_orphan_detail(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject, quality: TopicSubjectQualityRow, score: float, reason: str) -> None:
    item.subject_payload["row_role"] = "orphan_detail"
    item.subject_payload["anchor_status"] = "not_anchor"
    item.subject_payload["link_confidence"] = score
    item.subject_payload["link_reason"] = reason
    quality.row_role = "orphan_detail"
    quality.anchor_status = "not_anchor"
    quality.link_confidence = score
    quality.link_reason = reason
    quality.my_judgment = "orphan_detail"
    quality.ground_truth = "הקטע נראה כמו פרטים או היקף תחולה, אך לא נמצא עוגן פעולה סמוך מספיק בטוח."
    quality.status = "orphan_detail"


def set_subject_event_fields(
    *,
    item: ExtractedTopicSubject,
    quality: TopicSubjectQualityRow,
    event_id: str,
    event_topic: str,
    row_role: str,
    anchor_status: str,
    link_confidence: float,
    link_reason: str,
) -> None:
    item.subject_payload["event_id"] = event_id
    item.subject_payload["event_topic_label_he"] = event_topic
    item.subject_payload["row_role"] = row_role
    item.subject_payload["anchor_status"] = anchor_status
    item.subject_payload["link_confidence"] = link_confidence
    item.subject_payload["link_reason"] = link_reason
    quality.event_id = event_id
    quality.event_topic = event_topic
    quality.row_role = row_role
    quality.anchor_status = anchor_status
    quality.link_confidence = link_confidence
    quality.link_reason = link_reason
    if anchor_status == "suspect_anchor":
        quality.my_judgment = "suspect_action_anchor"
        quality.ground_truth = "הקטע נראה כמו תווית פעולה לפי המודל, אך אין מספיק עוגן פעולה בטקסט הגולמי ולכן הוא לא משמש לקישור שורות סמוכות."
        quality.reason_for_failure = link_reason
        quality.status = "suspect_anchor"


def event_id_for_artifact(artifact: TopicDecisionArtifact) -> str:
    digest = hashlib.sha1(f"{artifact.source_document_version_id}|{artifact.artifact_id}|{artifact.semantic_node_id}".encode("utf-8")).hexdigest()[:16]
    return f"topic_subject_event_{digest}"


def resolve_anchor_event_topic(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject) -> str:
    object_text = compact_text(item.subject_payload.get("subject_object_he"))
    if topic_label_supported_by_text(artifact.topic_label_he, artifact.real_text) or topic_label_supported_by_text(artifact.topic_label_he, object_text):
        return artifact.topic_label_he
    if artifact.child_label_he and topic_label_supported_by_text(artifact.child_label_he, artifact.real_text + " " + object_text):
        return artifact.child_label_he
    return artifact.topic_label_he


def topic_label_supported_by_text(label: str | None, text: str) -> bool:
    label_norm = normalize_for_search(label or "")
    text_norm = normalize_for_search(text or "")
    if not label_norm or not text_norm:
        return False
    tokens = [token for token in label_norm.split() if len(token) >= 4]
    if not tokens:
        return False
    return sum(1 for token in tokens if token in text_norm) >= max(1, min(len(tokens), 2))


def persist_topic_subject_artifact_result(
    *,
    session: Session,
    run: TopicSubjectRun,
    artifact_subjects: list[ExtractedTopicSubject],
    quality_row: TopicSubjectQualityRow,
) -> None:
    for item in artifact_subjects:
        subject = item.subject_payload
        decision = subject.get("decision") if isinstance(subject.get("decision"), dict) else {}
        artifact = item.artifact
        row = TopicSubject(
            run_id=int(run.id),
            municipality_slug=str(run.municipality_slug),
            artifact_id=artifact.artifact_id,
            semantic_node_id=artifact.semantic_node_id,
            subject_index=item.subject_index,
            root_topic_id=artifact.root_topic_id,
            child_topic_id=artifact.child_topic_id,
            topic_label_he=artifact.topic_label_he,
            source_kind=artifact.source_kind,
            source_document_id=artifact.source_document_id,
            source_document_version_id=artifact.source_document_version_id,
            source_ordinal=artifact.source_ordinal,
            source_page_start=artifact.start_page,
            source_page_end=artifact.end_page,
            source_title=artifact.source_title,
            subject_root_label_he=str(subject.get("subject_root_label_he") or ""),
            subject_root_label_norm=str(subject.get("subject_root_label_norm") or ""),
            subject_child_label_he=str(subject.get("subject_child_label_he") or ""),
            subject_child_label_norm=str(subject.get("subject_child_label_norm") or ""),
            subject_object_he=string_or_none(subject.get("subject_object_he")),
            subject_details_he=string_or_none(subject.get("subject_details_he")),
            artifact_role=string_or_none(subject.get("artifact_role")),
            topic_relevance=string_or_none(subject.get("topic_relevance")),
            event_id=string_or_none(subject.get("event_id")),
            event_topic_label_he=string_or_none(subject.get("event_topic_label_he")),
            row_role=string_or_none(subject.get("row_role")),
            anchor_status=string_or_none(subject.get("anchor_status")),
            linked_event_id=string_or_none(subject.get("linked_event_id")),
            link_confidence=float(subject.get("link_confidence") or 0.0) if subject.get("link_confidence") is not None else None,
            link_reason=string_or_none(subject.get("link_reason")),
            subject_summary_he=string_or_none(subject.get("subject_summary_he")),
            what_text_is_about_he=string_or_none(subject.get("what_text_is_about_he")),
            subject_status=str(subject.get("subject_status") or "candidate"),
            is_decision=bool(subject.get("is_decision")),
            decision_label_he=string_or_none(decision.get("decision_label_he")),
            decision_label_norm=string_or_none(decision.get("decision_label_norm")),
            decision_summary_he=string_or_none(decision.get("decision_summary_he")),
            decision_source_quote_he=string_or_none(decision.get("source_quote_he")),
            confidence=float(subject.get("confidence") or 0.0),
            validation_status=item.validation_status,
            failure_reason="; ".join(item.failure_reasons),
            evidence_refs_json=json.dumps(item.evidence_refs, ensure_ascii=False),
            resident_evidence_links_json=json.dumps(item.resident_evidence_links, ensure_ascii=False),
            neighbor_contexts_json=json.dumps(artifact.neighbor_contexts, ensure_ascii=False),
            dicta_payload_json=json.dumps(subject.get("raw_model_payload") or {}, ensure_ascii=False),
            judge_payload_json=json.dumps(item.judge_payload, ensure_ascii=False),
        )
        session.add(row)

    session.add(
        TopicSubjectQualityReport(
            run_id=int(run.id),
            artifact_id=quality_row.artifact_id,
            semantic_node_id=quality_row.semantic_node_id,
            topic_label_he=quality_row.topic,
            real_text=quality_row.real_text,
            what_text_is_about_he=quality_row.what_text_is_about,
            artifact_role=quality_row.artifact_role,
            topic_relevance=quality_row.topic_relevance,
            event_id=quality_row.event_id or None,
            event_topic_label_he=quality_row.event_topic or None,
            row_role=quality_row.row_role or None,
            anchor_status=quality_row.anchor_status or None,
            linked_event_id=quality_row.linked_event_id or None,
            link_confidence=quality_row.link_confidence,
            link_reason=quality_row.link_reason or None,
            subject_root_by_dicta=quality_row.subject_root_by_dicta,
            subject_child_by_dicta=quality_row.subject_child_by_dicta,
            subject_object_by_dicta=quality_row.subject_object_by_dicta,
            subject_details_by_dicta=quality_row.subject_details_by_dicta,
            decision_by_dicta=quality_row.decision_by_dicta,
            my_judgment=quality_row.my_judgment,
            ground_truth=quality_row.ground_truth,
            reason_for_failure=quality_row.reason_for_failure,
            status=quality_row.status,
            metadata_json=json.dumps(quality_row.metadata, ensure_ascii=False),
        )
    )
    session.flush()


def write_topic_subject_outputs(*, output_dir: Path, result: TopicSubjectResearchResult) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    tree_path = output_dir / "topic_tree.md"
    artifacts_path = output_dir / "ashdod_artifacts.md"
    subject_tree_path = output_dir / "subject_tree.md"
    quality_path = output_dir / "quality_report.md"
    quality_json_path = output_dir / "quality_report.json"
    tree_path.write_text(topic_tree_markdown(result.topic_tree), encoding="utf-8")
    artifacts_path.write_text(artifacts_markdown(result.artifacts), encoding="utf-8")
    subject_tree_path.write_text(subject_tree_markdown(result.extracted_subjects), encoding="utf-8")
    quality_path.write_text(quality_report_markdown(result.quality_rows), encoding="utf-8")
    quality_json_path.write_text(json.dumps([quality_row_to_dict(row) for row in result.quality_rows], ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "topic_tree_md": str(tree_path),
        "artifacts_md": str(artifacts_path),
        "subject_tree_md": str(subject_tree_path),
        "quality_report_md": str(quality_path),
        "quality_report_json": str(quality_json_path),
    }


def subject_tree_markdown(subjects: list[ExtractedTopicSubject]) -> str:
    lines = ["## Subject Tree Candidates", "", "| Subject Root Candidate | Subject Child Candidate | Support | Decision Candidates | Example Topic | Review Status |", "|---|---|---:|---:|---|---|"]
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for item in subjects:
        if not is_countable_subject_event(item):
            continue
        subject = item.subject_payload
        root = str(subject.get("subject_root_label_he") or "")
        child = str(subject.get("subject_child_label_he") or "")
        if not root or not child:
            continue
        key = (str(subject.get("subject_root_label_norm") or normalize_for_search(root)), str(subject.get("subject_child_label_norm") or normalize_for_search(child)))
        group = groups.setdefault(
            key,
            {
                "root": root,
                "child": child,
                "artifact_ids": set(),
                "decision_count": 0,
                "topics": set(),
                "has_failure": False,
            },
        )
        group["artifact_ids"].add(item.artifact.artifact_id)
        group["topics"].add(item.artifact.topic_label_he)
        group["decision_count"] += 1 if bool(subject.get("is_decision")) and item.validation_status != "failed" else 0
        group["has_failure"] = bool(group["has_failure"] or item.validation_status == "failed")
    for group in sorted(groups.values(), key=lambda row: (-len(row["artifact_ids"]), str(row["root"]), str(row["child"]))):
        status = "candidate_needs_review" if group["has_failure"] else "candidate"
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_table(group["root"]),
                    escape_table(group["child"]),
                    escape_table(str(len(group["artifact_ids"]))),
                    escape_table(str(group["decision_count"])),
                    escape_table(", ".join(sorted(group["topics"])[:3])),
                    escape_table(status),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def quality_report_markdown(rows: list[TopicSubjectQualityRow]) -> str:
    lines = [
        "## Topic Subject Quality Report",
        "",
        "| Artifact Topic | Event Topic | Topic Relevance | Row Role | Anchor Status | Artifact Role | Link Confidence | Real Text | What Text Is About | Subject Root By Dicta | Subject Child By Dicta | Subject Object | Subject Details | Decision By Dicta | My Judgment | Ground Truth | Reason For Failure |",
        "|---|---|---|---|---|---|---:|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_table(row.topic),
                    escape_table(row.event_topic),
                    escape_table(row.topic_relevance),
                    escape_table(row.row_role),
                    escape_table(row.anchor_status),
                    escape_table(row.artifact_role),
                    escape_table(f"{row.link_confidence:.2f}" if row.link_confidence else ""),
                    escape_table(shorten(row.real_text, 220)),
                    escape_table(shorten(row.what_text_is_about, 220)),
                    escape_table(shorten(row.subject_root_by_dicta, 160)),
                    escape_table(shorten(row.subject_child_by_dicta, 160)),
                    escape_table(shorten(row.subject_object_by_dicta, 160)),
                    escape_table(shorten(row.subject_details_by_dicta, 180)),
                    escape_table(shorten(row.decision_by_dicta, 180)),
                    escape_table(row.my_judgment),
                    escape_table(shorten(row.ground_truth, 220)),
                    escape_table(shorten(row.reason_for_failure, 220)),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def quality_row_to_dict(row: TopicSubjectQualityRow) -> dict[str, Any]:
    return {
        "artifact_id": row.artifact_id,
        "semantic_node_id": row.semantic_node_id,
        "topic": row.topic,
        "real_text": row.real_text,
        "what_text_is_about": row.what_text_is_about,
        "artifact_role": row.artifact_role,
        "topic_relevance": row.topic_relevance,
        "event_id": row.event_id,
        "event_topic": row.event_topic,
        "row_role": row.row_role,
        "anchor_status": row.anchor_status,
        "linked_event_id": row.linked_event_id,
        "link_confidence": row.link_confidence,
        "link_reason": row.link_reason,
        "subject_root_by_dicta": row.subject_root_by_dicta,
        "subject_child_by_dicta": row.subject_child_by_dicta,
        "subject_object_by_dicta": row.subject_object_by_dicta,
        "subject_details_by_dicta": row.subject_details_by_dicta,
        "decision_by_dicta": row.decision_by_dicta,
        "my_judgment": row.my_judgment,
        "ground_truth": row.ground_truth,
        "reason_for_failure": row.reason_for_failure,
        "status": row.status,
        "metadata": row.metadata,
    }


def subject_decision_summary(subject: dict[str, Any]) -> str:
    decision = subject.get("decision") if isinstance(subject.get("decision"), dict) else {}
    return " / ".join(
        part
        for part in [
            compact_text(decision.get("decision_label_he")),
            compact_text(decision.get("decision_summary_he")),
        ]
        if part
    )


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    return text in {"true", "1", "yes", "כן"}


def compact_subject_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload.get(key) for key in ("error_code", "error_text", "overall_summary_he", "rationale_he", "subjects", "preclassified", "non_subject", "artifact_role", "topic_relevance", "json_repair_applied") if key in payload}
